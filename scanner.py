"""
Scanner Multi-Par para Bybit Futures
Escanea top pares, filtra por RSI >= 75, sin prioridad de casos
Implementa sistema de 2 caminos para detección de swing Fibonacci
"""
import json
import os
import asyncio
import aiohttp
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

from config import REST_BASE_URL, MARGIN_PER_TRADE, MAX_MARGIN_PER_TRADE, TARGET_PROFIT, LEVERAGE, COMMISSION_RATE, MIN_AVAILABLE_MARGIN, TIMEFRAME, CANDLE_LIMIT, RSI_TIMEFRAME
from fibonacci import calculate_zigzag, find_valid_fibonacci_swing, determine_trading_case
from logger import setup_logger

# Logger para el scanner
scanner_logger = setup_logger("scanner")

# Cargar límite de operaciones simultáneas desde config
def get_max_simultaneous_operations() -> int:
    try:
        with open('shared_config.json', 'r') as f:
            config = json.load(f)
            return config.get('trading', {}).get('max_simultaneous_operations', 20)
    except:
        return 20  # Valor por defecto

# Cargar configuración de TP/SL por estrategia
def get_strategy_config() -> dict:
    """Obtiene la configuración de TP/SL por caso desde shared_config.json"""
    
    # 1. Intentar cargar desde variable de entorno (Override)
    # Esto permite inyectar estrategias específicas por instancia (Multibot)
    env_override = os.getenv("BOT_STRATEGIES_OVERRIDE")
    if env_override:
        try:
            return json.loads(env_override)
        except Exception as e:
            print(f"⚠️ Error al parsear BOT_STRATEGIES_OVERRIDE: {e}")
            # Fallback a shared_config si falla

    defaults = {
        "c1": {"tp": 0.50, "sl": 0.88},
        "c3": {"tp": 0.51, "sl": 1.05},
        "c4": {"tp": 0.56, "sl": 1.05}
    }
    try:
        with open('shared_config.json', 'r') as f:
            config = json.load(f)
            return config.get('strategies', defaults)
    except:
        return defaults


@dataclass
class ScanResult:
    symbol: str
    rsi: float
    case: int
    current_price: float
    fib_levels: Dict[str, float]
    is_valid: bool
    path: int = 1  # 1 = normal, 2 = alternativo (Caso 1+)


class MarketScanner:
    def __init__(self, top_n: int = 100):
        from config import RSI_THRESHOLD
        self.top_n = top_n
        self.rsi_period = 14
        self.rsi_threshold = RSI_THRESHOLD  # Leer de config.py
        self.rsi_timeframe = RSI_TIMEFRAME  # Leer de config.py
        self.pairs_cache: List[str] = []
        self.last_scan_results: Dict[str, ScanResult] = {}
    
    async def get_top_pairs(self) -> List[str]:
        """Obtener top N pares por volumen de Bybit Futures"""
        from config import EXCLUDED_PAIRS # Importar aquí para evitar ciclo si no está arriba
        url = f"{REST_BASE_URL}/v5/market/tickers?category=linear"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        print(f"❌ Error obteniendo pares: {response.status}")
                        return self.pairs_cache or []
                    
                    data = await response.json()
                    
                    if data.get('retCode') != 0:
                        print(f"❌ Error API Bybit: {data.get('retMsg')}")
                        return self.pairs_cache or []
                    
                    tickers = data.get('result', {}).get('list', [])
                    
                    # Filtrar y ordenar por volumen
                    usdt_pairs = [
                        item for item in tickers 
                        if item['symbol'].endswith('USDT') 
                        and 'USDT' not in item['symbol'][:-4]  # Excluir USDTUSDT
                        and 'BTCDOM' not in item['symbol']
                        and item['symbol'] not in EXCLUDED_PAIRS # FILTRO CRÍTICO
                    ]
                    
                    # Ordenar por volumen (turnover24h = volumen en USDT)
                    sorted_pairs = sorted(
                        usdt_pairs, 
                        key=lambda x: float(x.get('turnover24h', 0)), 
                        reverse=True
                    )
                    
                    self.pairs_cache = [p['symbol'] for p in sorted_pairs[:self.top_n]]
                    print(f"📊 Top {len(self.pairs_cache)} pares cargados (excluidos {len(EXCLUDED_PAIRS)} pares prohibidos)")
                    return self.pairs_cache
                    
        except Exception as e:
            print(f"❌ Error en get_top_pairs: {e}")
            return self.pairs_cache or []
    
    async def get_current_price(self, symbol: str) -> Optional[float]:
        """Obtener precio actual de un símbolo vía REST API"""
        url = f"{REST_BASE_URL}/v5/market/tickers"
        params = {"category": "linear", "symbol": symbol}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as response:
                    if response.status != 200:
                        return None
                    data = await response.json()
                    
                    if data.get('retCode') != 0:
                        return None
                    
                    tickers = data.get('result', {}).get('list', [])
                    if tickers:
                        return float(tickers[0].get('lastPrice', 0))
            return None
        except Exception as e:
            print(f"❌ Error obteniendo precio de {symbol}: {e}")
            return None
    
    async def fetch_klines(self, session: aiohttp.ClientSession, 
                           symbol: str, interval: str = '5m', 
                           limit: int = 100) -> List[dict]:
        """Obtener velas para un par desde Bybit (con paginación automática si limit > 1000)"""
        # Convertir intervalo a formato Bybit (1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, M, W)
        interval_map = {
            '1m': '1', '3m': '3', '5m': '5', '15m': '15', '30m': '30',
            '1h': '60', '2h': '120', '4h': '240', '6h': '360', '12h': '720',
            '1d': 'D', '1w': 'W', '1M': 'M'
        }
        bybit_interval = interval_map.get(interval, '60')
        
        url = f"{REST_BASE_URL}/v5/market/kline"
        all_candles = []
        remaining = limit
        end_time = None
        
        try:
            while remaining > 0:
                batch_limit = min(remaining, 1000)  # Bybit max 1000 per request
                params = {"category": "linear", "symbol": symbol, "interval": bybit_interval, "limit": batch_limit}
                
                if end_time:
                    params["end"] = end_time
                
                async with session.get(url, params=params) as response:
                    if response.status != 200:
                        break
                    data = await response.json()
                    
                    if data.get('retCode') != 0:
                        break
                    
                    klines = data.get('result', {}).get('list', [])
                    if not klines:
                        break
                    
                    # Bybit devuelve en orden descendente (más reciente primero)
                    parsed = [
                        {
                            "time": int(c[0]) // 1000,
                            "open": float(c[1]),
                            "high": float(c[2]),
                            "low": float(c[3]),
                            "close": float(c[4]),
                            "volume": float(c[5])
                        }
                        for c in klines
                    ]
                    
                    # Insertar al inicio (son más antiguas)
                    all_candles = parsed + all_candles
                    
                    remaining -= len(klines)
                    
                    if remaining > 0 and len(klines) == batch_limit:
                        # Obtener timestamp de la vela más antigua para siguiente request
                        end_time = int(klines[-1][0]) - 1  # -1 para no duplicar
                    else:
                        break
            
            # Ordenar por tiempo ascendente
            all_candles.sort(key=lambda x: x['time'])
            return all_candles
            
        except Exception as e:
            return all_candles if all_candles else []
    
    def calculate_rsi(self, candles: List[dict], period: int = 14) -> float:
        """Calcular RSI de las velas"""
        if len(candles) < period + 1:
            return 50.0  # Valor neutral si no hay suficientes datos
        
        closes = [c['close'] for c in candles]
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        
        # EMA de ganancias y pérdidas
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    async def scan_pair(self, session: aiohttp.ClientSession, 
                        symbol: str) -> Optional[List[ScanResult]]:
        """
        Escanear un par individual
        
        NUEVO: Puede retornar múltiples resultados (uno por cada swing válido)
        debido al sistema de 2 caminos
        """
        try:
            # === OPTIMIZACIÓN: Fetch paralelo de RSI_TIMEFRAME (RSI) y TIMEFRAME (Fibo) ===
            t_rsi_task = self.fetch_klines(session, symbol, self.rsi_timeframe, 100)
            tfibo_task = self.fetch_klines(session, symbol, TIMEFRAME, CANDLE_LIMIT)
            
            candles_rsi, candles = await asyncio.gather(t_rsi_task, tfibo_task)
            
            if not candles_rsi:
                # print(f"   [DEBUG] {symbol}: Sin velas {self.rsi_timeframe}")
                return None
            
            rsi = self.calculate_rsi(candles_rsi)
            
            # Filtrar por RSI
            if rsi < self.rsi_threshold:
                # print(f"   [DEBUG] {symbol}: RSI {rsi:.1f} < {self.rsi_threshold}")
                return None
            
            if len(candles) < 50:
                # print(f"   [DEBUG] {symbol}: Pocas velas {TIMEFRAME} ({len(candles)})")
                return None
            
            # Calcular Fibonacci
            zigzag = calculate_zigzag(candles, TIMEFRAME)
            if len(zigzag) < 2:
                print(f"   [DEBUG] {symbol}: ZigZag insuficiente ({len(zigzag)} puntos)")
                return None
            
            print(f"   📊 {symbol}: ZigZag OK ({len(zigzag)} puntos), buscando swing...")
            swings = find_valid_fibonacci_swing(zigzag, candles)
            
            if not swings:
                print(f"   [DEBUG] {symbol}: No hay swing válido")
                return None
            
            current_price = candles[-1]['close']
            results = []
            
            # Procesar cada swing encontrado (camino 1 y/o camino 2)
            for swing in swings:
                if not swing.is_valid:
                    continue
                    
                # NUEVO: Pasar candle_data para validar que el nivel de entrada no fue tocado
                case = determine_trading_case(current_price, swing, candles, last_n_candles=3)
                
                path_text = f"(Path {swing.path})" if swing.path == 2 else ""
                print(f"   ✅ {symbol}: Swing válido {path_text}! Precio ${current_price:.4f} -> CASO {case}")
                
                if case == 0:
                    print(f"   [DEBUG] {symbol}: Caso 0 (inválido) {path_text}")
                    continue
                
                result = ScanResult(
                    symbol=symbol,
                    rsi=rsi,
                    case=case,
                    current_price=current_price,
                    fib_levels={
                        '40': swing.levels.get('40', swing.low.price + (swing.high.price - swing.low.price) * 0.40),
                        '45': swing.levels.get('45', swing.low.price + (swing.high.price - swing.low.price) * 0.45),
                        '50': swing.levels.get('50', 0),
                        '55': swing.levels.get('55', swing.low.price + (swing.high.price - swing.low.price) * 0.55),
                        '60': swing.levels.get('60', swing.low.price + (swing.high.price - swing.low.price) * 0.60),
                        '62': swing.levels.get('62', swing.low.price + (swing.high.price - swing.low.price) * 0.62),
                        '618': swing.levels.get('61.8', 0),
                        '69': swing.levels.get('69', swing.low.price + (swing.high.price - swing.low.price) * 0.69),
                        '70': swing.levels.get('70', swing.low.price + (swing.high.price - swing.low.price) * 0.70),
                        '75': swing.levels.get('75', 0),
                        '786': swing.levels.get('78.6', 0),
                        'high': swing.high.price,
                        'low': swing.low.price
                    },
                    is_valid=True,
                    path=swing.path  # Guardar el path (1 = normal, 2 = alternativo)
                )
                results.append(result)
            
            return results if results else None
        except Exception as e:
            print(f"   ❌ {symbol}: Error - {e}")
            return None
    
    async def scan_all_pairs(self, pairs: List[str]) -> Dict[int, List[ScanResult]]:
        """
        Escanear todos los pares y agrupar por caso
        
        NUEVO: scan_pair puede retornar múltiples resultados (por sistema de 2 caminos)
        """
        results = {1: [], 3: [], 4: []}  # Caso 2 eliminado
        
        async with aiohttp.ClientSession() as session:
            # Escanear en lotes (batch)
            batch_size = 50
            total_pairs = len(pairs)
            
            print(f"📊 Iniciando escaneo de {total_pairs} pares...")
            
            for i in range(0, total_pairs, batch_size):
                batch = pairs[i:i+batch_size]
                
                print(f"   ⚡ Escaneando bloque {i+1}-{min(i+batch_size, total_pairs)} ({batch[0]}...)...")
                
                tasks = [self.scan_pair(session, symbol) for symbol in batch]
                batch_results = await asyncio.gather(*tasks)
                
                for scan_results in batch_results:
                    # scan_results ahora es una lista de ScanResult (o None)
                    if scan_results:
                        for result in scan_results:
                            if result and result.is_valid:
                                results[result.case].append(result)
                                self.last_scan_results[result.symbol] = result
                
                await asyncio.sleep(0.5)
        
        print(f"🔍 Scan: C4: {len(results[4])} | C3: {len(results[3])} | C1: {len(results[1])}")  # Caso 2 eliminado
        return results


    async def update_prices_for_positions(self, account, price_cache: dict):
        """
        [WATCHDOG] Actualizar precios de posiciones abiertas Y órdenes pendientes vía REST API
        Esto sirve como fallback si el WebSocket falla.
        """
        active_symbols = set()
        if account.open_positions:
            active_symbols.update(pos.symbol for pos in account.open_positions.values())
        if account.pending_orders:
            active_symbols.update(
                (order.get('symbol') if isinstance(order, dict) else order.symbol)
                for order in account.pending_orders.values()
            )
            
        if not active_symbols:
            return

        # print(f"🛡️ Watchdog: Verificando precios REST para {len(active_symbols)} monedas...")
        
        async with aiohttp.ClientSession() as session:
            for symbol in list(active_symbols):
                try:
                    # Bybit endpoint para ticker
                    url = f"{REST_BASE_URL}/v5/market/tickers?category=linear&symbol={symbol}"
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data.get('retCode') == 0:
                                tickers = data.get('result', {}).get('list', [])
                                if tickers:
                                    price = float(tickers[0]['lastPrice'])
                                    
                                    # Actualizar cache 
                                    price_cache[symbol] = price
                                    
                                    # Validar TP/SL y Pending Orders
                                    if account.open_positions:
                                        account.check_positions(symbol, price)
                                    if account.pending_orders:
                                        account.check_pending_orders(symbol, price)
                            
                            # print(f"   🛡️ {symbol}: ${price}")
                except Exception as e:
                    print(f"   ❌ Error Watchdog {symbol}: {e}")

async def run_priority_scan(scanner: MarketScanner, account, margin_per_trade: float = 3.0):
    """
    Ejecutar escaneo EN TIEMPO REAL
    Las órdenes se colocan INMEDIATAMENTE cuando se encuentra un par válido
    """
    from paper_trading import OrderSide
    
    # Obtener límite de operaciones simultáneas
    try:
        with open('shared_config.json', 'r') as f:
            config = json.load(f)
            max_ops = config.get('trading', {}).get('max_simultaneous_operations', 20)
    except:
        max_ops = 20
    
    # Usar cache si está definido, sino hacer fetch
    if scanner.pairs_cache:
        pairs = scanner.pairs_cache
        print(f"📊 Usando {len(pairs)} par(es) definidos: {', '.join(pairs)}")
    else:
        pairs = await scanner.get_top_pairs()
    
    if not pairs:
        print("❌ No se pudieron obtener pares")
        return
    
    total_pairs = len(pairs)
    orders_placed = 0
    
    print(f"📊 Escaneando {total_pairs} pares en paralelo...")
    
    BATCH_SIZE = 50  # Optimizado: de 20 a 50
    
    async with aiohttp.ClientSession() as session:
        # Procesar en batches para velocidad
        for batch_start in range(0, total_pairs, BATCH_SIZE):
            # Verificar límite antes de cada batch
            current_ops = len(account.open_positions) + len(account.pending_orders)
            if current_ops >= max_ops:
                print(f"⚠️ Límite alcanzado: {current_ops}/{max_ops}")
                break
            
            if account.get_available_margin() < MIN_AVAILABLE_MARGIN:
                print(f"⚠️ Margen mínimo: ${account.get_available_margin():.2f}")
                break
            
            batch_end = min(batch_start + BATCH_SIZE, total_pairs)
            batch_pairs = pairs[batch_start:batch_end]
            
            # Escanear batch en paralelo
            tasks = [scanner.scan_pair(session, symbol) for symbol in batch_pairs]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Procesar resultados y colocar órdenes INMEDIATAMENTE
            for symbol, scan_results in zip(batch_pairs, batch_results):
                if isinstance(scan_results, Exception):
                    continue
                if not scan_results:
                    continue
                
                for result in scan_results:
                    if not result or not result.is_valid:
                        continue
                    
                    # Re-verificar límites antes de cada orden
                    current_ops = len(account.open_positions) + len(account.pending_orders)
                    if current_ops >= max_ops:
                        break

                    case_num = result.case
                    
                    # Verificar duplicados - Solo 1 operación por símbolo
                    existing = any(p.symbol == result.symbol for p in account.open_positions.values())
                    existing = existing or any(
                        (o.get('symbol') if isinstance(o, dict) else o.symbol) == result.symbol 
                        for o in account.pending_orders.values()
                    )
                    if existing:
                        continue
                    
                    fib_range = result.fib_levels.get('high', 0) - result.fib_levels.get('low', 0)
                    sl_price = None
                    
                    # Colocar orden INMEDIATAMENTE
                    order_placed, order_id, primary_sl = await _place_order_for_case(
                        scanner, account, result, case_num, 
                        margin_per_trade, fib_range, sl_price, OrderSide, session
                    )
                    
                    if order_placed:
                        orders_placed += 1

            
            # Pausa eliminada para máxima velocidad
            pass
    
    print(f"\n📊 Escaneo completado: {orders_placed} órdenes colocadas")
    print(f"💰 Margen disponible: ${account.get_available_margin():.2f}")


async def _place_order_for_case(scanner, account, result, case_num, margin_per_trade, fib_range, sl_price, OrderSide, session=None):
    """Helper para colocar una orden según el caso. Retorna (success, order_id, sl_price)"""
    order_placed = False
    order_id = None
    final_sl = None
    fresh_price = 0.0
    
    # Obtener configuración de estrategias y trading
    strategies = get_strategy_config()
    
    # Leer niveles de entrada desde shared_config.json
    try:
        with open('shared_config.json', 'r') as f:
            config = json.load(f)
            trading = config.get('trading', {})
            case_1_max_3_min = trading.get('case_1_max_3_min', 0.67)
            case_3_max_4_min = trading.get('case_3_max_4_min', 0.79)
            case_4_max = trading.get('case_4_max', 0.90)
    except:
        case_1_max_3_min = 0.67
        case_3_max_4_min = 0.79
        case_4_max = 0.90
    
    # Obtener precio fresco para registrar 'creation_price' precisa
    if not fresh_price or fresh_price == 0.0:
         fresh_price = result.current_price if hasattr(result, 'current_price') else 0.0

    # --- Nueva Lógica: Ganancia Bruta y Protección de Comisiones ---
    def calculate_trade_params(entry_price, tp_price):
        """
        Calcula Qty para Ganancia Bruta = TARGET_PROFIT
        Retorna (Qty, Margin, Estimated_Commission, Allowed)
        """
        price_diff = abs(entry_price - tp_price)
        if price_diff == 0:
            return 0, 0, 0, False
            
        # 1. Calcular Qty para Ganancia Bruta (TARGET_PROFIT = $1)
        # Ganancia Bruta = Qty * |Entry - TP|
        # Qty = TARGET_PROFIT / |Entry - TP|
        qty = TARGET_PROFIT / price_diff
        
        # 2. Calcular Margin Requerido
        margin = (qty * entry_price) / LEVERAGE
        
        # 3. Calcular Comisión Estimada (Apertura + Cierre)
        # Asumimos peor caso: Taker en Open (si es Market) y Maker en Close (TP Limit)
        # O Maker/Maker si es Limit. Para seguridad usamos un promedio o el peor caso.
        # Simplificación: Usamos COMMISSION_RATE general (0.06% = 0.0006)
        # Comm = Qty * (Entry + TP) * Rate
        est_commission = qty * (entry_price + tp_price) * COMMISSION_RATE
        
        # 4. Regla de Protección: Comisión < 50% de la Ganancia Bruta
        # Si ganamos $1, no queremos pagar más de $0.50 en comisiones
        if est_commission > (TARGET_PROFIT / 2):
            print(f"   🚫 {result.symbol}: Comisión alta (${est_commission:.4f}) vs Profit (${TARGET_PROFIT})")
            return qty, margin, est_commission, False
            
        if margin > MAX_MARGIN_PER_TRADE:
             # Ajustar al máximo margen permitido (reducir Qty)
             # Esto reducirá la ganancia bruta esperada, pero es un límite duro de seguridad
             qty = (MAX_MARGIN_PER_TRADE * LEVERAGE) / entry_price
             margin = MAX_MARGIN_PER_TRADE
             # Recalcular comisión
             est_commission = qty * (entry_price + tp_price) * COMMISSION_RATE
        
        return qty, margin, est_commission, True

    if case_num == 4:
        # Caso 4: MARKET ORDER (Revertido a Market por solicitud)
        if not fresh_price or fresh_price == 0.0:
            return False, None, None
        
        level_case4_min = result.fib_levels.get('low', 0) + fib_range * case_3_max_4_min
        level_case4_max = result.fib_levels.get('low', 0) + fib_range * case_4_max
        
        # Validar zona (79% - 90%)
        if fresh_price < level_case4_min or fresh_price >= level_case4_max:
             # print(f"   ⚠️ {result.symbol}: Precio fuera de zona C4")
             return False, None, None
        
        # TP y SL desde configuración
        c4_config = strategies.get('c4', {'tp': 0.65, 'sl': 1.265})
        tp_price = result.fib_levels.get('low', 0) + fib_range * c4_config['tp']
        sl_price = result.fib_levels.get('low', 0) + fib_range * c4_config['sl'] if c4_config.get('sl') else None
        
        # Calcular parámetros
        qty, margin, est_comm, allowed = calculate_trade_params(fresh_price, tp_price)
        
        if not allowed:
            return False, None, None

        order = account.place_market_order(
            symbol=result.symbol,
            side=OrderSide.SELL,
            current_price=fresh_price,
            margin=margin,
            take_profit=tp_price,
            stop_loss=sl_price,
            strategy_case=case_num,
            fib_high=result.fib_levels.get('high'),
            fib_low=result.fib_levels.get('low'),
            entry_fib_level=(fresh_price - result.fib_levels.get('low', 0)) / fib_range,
            estimated_commission=est_comm
        )
        if order:
            sl_str = f" | SL ${sl_price:.4f}" if sl_price else ""
            print(f"   🔴 CASO 4 | {result.symbol}: MARKET @ ${fresh_price:.4f} → TP ${tp_price:.4f}{sl_str}")
            order_placed = True
            order_id = order.order_id # Order object in Real, or dict... check return type
            final_sl = sl_price
        else:
            print(f"   ❌ CASO 4 | {result.symbol}: Orden no colocada (ver logs)")
    
    elif case_num == 3:
        # Caso 3: LIMIT al nivel case_3_max_4_min (por defecto 79%)
        limit_price = result.fib_levels.get('low', 0) + fib_range * case_3_max_4_min
        
        # TP y SL desde configuración
        c3_config = strategies.get('c3', {'tp': 0.62, 'sl': 0.94})
        tp_price = result.fib_levels.get('low', 0) + fib_range * c3_config['tp']
        sl_price = result.fib_levels.get('low', 0) + fib_range * c3_config['sl'] if c3_config.get('sl') else None
        
        # Calcular parámetros
        qty, margin, est_comm, allowed = calculate_trade_params(limit_price, tp_price)
        
        if not allowed:
            return False, None, None
            
        order = account.place_limit_order(
            symbol=result.symbol,
            side=OrderSide.SELL,
            price=limit_price,
            margin=margin,
            take_profit=tp_price,
            stop_loss=sl_price,
            strategy_case=case_num,
            fib_high=result.fib_levels.get('high'),
            fib_low=result.fib_levels.get('low'),
            current_price=fresh_price,
            estimated_commission=est_comm
        )
        if order:
            sl_str = f" | SL ${sl_price:.4f}" if sl_price else ""
            print(f"   🟠 CASO 3 | {result.symbol}: LIMIT @ ${limit_price:.4f} → TP ${tp_price:.4f}{sl_str}")
            order_placed = True
            order_id = order['id'] if isinstance(order, dict) else order.id
            final_sl = sl_price
    
    # Caso 2 eliminado - ya no existe
    
    elif case_num == 1:
        # Caso 1: LIMIT SELL al nivel case_1_max_3_min (por defecto 67%)
        
        # TP y SL desde configuración
        c1_config = strategies.get('c1', {'tp': 0.51, 'sl': 0.67})
        tp_price = result.fib_levels.get('low', 0) + fib_range * c1_config['tp']
        sl_price = result.fib_levels.get('low', 0) + fib_range * c1_config['sl'] if c1_config.get('sl') else None
        # LIMIT SELL al nivel configurado
        limit_price = result.fib_levels.get('low', 0) + fib_range * case_1_max_3_min
        
        # Calcular parámetros
        qty, margin, est_comm, allowed = calculate_trade_params(limit_price, tp_price)
        
        if not allowed:
            return False, None, None
            
        order = account.place_limit_order(
            symbol=result.symbol,
            side=OrderSide.SELL,
            price=limit_price,
            margin=margin,
            take_profit=tp_price,
            stop_loss=sl_price,
            strategy_case=1,
            fib_high=result.fib_levels.get('high'),
            fib_low=result.fib_levels.get('low'),
            current_price=fresh_price,
            estimated_commission=est_comm
        )
        if order:
            sl_str = f" | SL ${sl_price:.4f}" if sl_price else ""
            print(f"   🟢 CASO 1 | {result.symbol}: LIMIT @ ${limit_price:.4f} → TP ${tp_price:.4f}{sl_str}")
            order_placed = True
            order_id = order['id'] if isinstance(order, dict) else order.id
            final_sl = sl_price
    
    return order_placed, order_id, final_sl



