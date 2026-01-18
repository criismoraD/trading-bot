"""
Scanner Multi-Par para Bybit Futures
Escanea top pares, filtra por RSI >= 75, sin prioridad de casos
Implementa sistema de 2 caminos para detección de swing Fibonacci
"""
import json
import asyncio
import aiohttp
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

from config import REST_BASE_URL, MARGIN_PER_TRADE, MIN_AVAILABLE_MARGIN, TIMEFRAME, CANDLE_LIMIT
from fibonacci import calculate_zigzag, find_valid_fibonacci_swing, determine_trading_case

# Cargar límite de operaciones simultáneas desde config
def get_max_simultaneous_operations() -> int:
    try:
        with open('shared_config.json', 'r') as f:
            config = json.load(f)
            return config.get('trading', {}).get('max_simultaneous_operations', 20)
    except:
        return 20  # Valor por defecto


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
        """Obtener velas para un par desde Bybit"""
        # Convertir intervalo a formato Bybit (1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, M, W)
        interval_map = {
            '1m': '1', '3m': '3', '5m': '5', '15m': '15', '30m': '30',
            '1h': '60', '2h': '120', '4h': '240', '6h': '360', '12h': '720',
            '1d': 'D', '1w': 'W', '1M': 'M'
        }
        bybit_interval = interval_map.get(interval, '60')
        
        url = f"{REST_BASE_URL}/v5/market/kline"
        params = {"category": "linear", "symbol": symbol, "interval": bybit_interval, "limit": limit}
        
        try:
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    return []
                data = await response.json()
                
                if data.get('retCode') != 0:
                    return []
                
                klines = data.get('result', {}).get('list', [])
                # Bybit devuelve en orden descendente, invertir
                klines = klines[::-1]
                
                return [
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
        except:
            return []
    
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
            # Obtener velas 5m para RSI
            candles_5m = await self.fetch_klines(session, symbol, '5m', 100)
            if not candles_5m:
                print(f"   [DEBUG] {symbol}: Sin velas 5m")
                return None
            
            rsi = self.calculate_rsi(candles_5m)
            
            # Filtrar por RSI
            if rsi < self.rsi_threshold:
                print(f"   [DEBUG] {symbol}: RSI {rsi:.1f} < {self.rsi_threshold}")
                return None
            
            # Obtener velas para Fibonacci (usando TIMEFRAME y CANDLE_LIMIT configurados)
            candles = await self.fetch_klines(session, symbol, TIMEFRAME, CANDLE_LIMIT)
            if len(candles) < 50:
                print(f"   [DEBUG] {symbol}: Pocas velas {TIMEFRAME} ({len(candles)})")
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
                        '45': swing.levels.get('45', swing.low.price + (swing.high.price - swing.low.price) * 0.45),
                        '50': swing.levels.get('50', 0),
                        '55': swing.levels.get('55', swing.low.price + (swing.high.price - swing.low.price) * 0.55),
                        '60': swing.levels.get('60', swing.low.price + (swing.high.price - swing.low.price) * 0.60),
                        '62': swing.levels.get('62', swing.low.price + (swing.high.price - swing.low.price) * 0.62),
                        '618': swing.levels.get('61.8', 0),
                        '69': swing.levels.get('69', swing.low.price + (swing.high.price - swing.low.price) * 0.69),
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
        results = {1: [], 2: [], 3: [], 4: []}
        
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
        
        print(f"🔍 Scan: C4: {len(results[4])} | C3: {len(results[3])} | C2: {len(results[2])} | C1: {len(results[1])}")
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
            active_symbols.update(order.symbol for order in account.pending_orders.values())
            
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
    max_ops = get_max_simultaneous_operations()
    
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
    
    BATCH_SIZE = 20  # Procesar 20 pares en paralelo
    
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
                    strategy_case_value = 11 if result.path == 2 else case_num
                    
                    # Verificar duplicados
                    existing = any(p.symbol == result.symbol and p.strategy_case == strategy_case_value 
                                   for p in account.open_positions.values())
                    existing = existing or any(o.symbol == result.symbol and o.strategy_case == strategy_case_value 
                                               for o in account.pending_orders.values())
                    if existing:
                        continue
                    
                    fib_range = result.fib_levels.get('high', 0) - result.fib_levels.get('low', 0)
                    sl_price = None
                    
                    # Colocar orden INMEDIATAMENTE
                    order_placed = await _place_order_for_case(
                        scanner, account, result, case_num, 
                        margin_per_trade, fib_range, sl_price, OrderSide, session
                    )
                    
                    if order_placed:
                        orders_placed += 1
                        # Si C2/C3/C4, buscar C1++
                        if case_num in [2, 3, 4]:
                            await _search_and_place_c1pp(
                                scanner, account, result.symbol,
                                result.fib_levels.get('high', 0),
                                result.fib_levels.get('low', 0),
                                margin_per_trade, sl_price, OrderSide, session
                            )
            
            # Pausa mínima entre batches
            await asyncio.sleep(0.1)
    
    print(f"\n📊 Escaneo completado: {orders_placed} órdenes colocadas")
    print(f"💰 Margen disponible: ${account.get_available_margin():.2f}")


async def _place_order_for_case(scanner, account, result, case_num, margin_per_trade, fib_range, sl_price, OrderSide, session=None):
    """Helper para colocar una orden según el caso"""
    order_placed = False
    
    if case_num == 4:
        # Caso 4: MARKET, TP 60%
        fresh_price = await scanner.get_current_price(result.symbol)
        if not fresh_price:
            return False
        
        level_case4_min = result.fib_levels.get('low', 0) + fib_range * 0.75
        level_case4_max = result.fib_levels.get('low', 0) + fib_range * 0.90
        
        if fresh_price < level_case4_min or fresh_price >= level_case4_max:
            print(f"   ⚠️ {result.symbol}: Precio cambió, ya no está en zona C4")
            return False
        
        tp_price = result.fib_levels['60']
        position = account.place_market_order(
            symbol=result.symbol,
            side=OrderSide.SELL,
            current_price=fresh_price,
            margin=margin_per_trade,
            take_profit=tp_price,
            stop_loss=sl_price,
            strategy_case=case_num,
            fib_high=result.fib_levels.get('high'),
            fib_low=result.fib_levels.get('low')
        )
        if position:
            print(f"   🔴 CASO 4 | {result.symbol}: MARKET @ ${fresh_price:.4f} → TP ${tp_price:.4f}")
            order_placed = True
    
    elif case_num == 3:
        # Caso 3: LIMIT 78.6%, TP 55%
        limit_price = result.fib_levels['786']
        tp_price = result.fib_levels['55']
        order = account.place_limit_order(
            symbol=result.symbol,
            side=OrderSide.SELL,
            price=limit_price,
            margin=margin_per_trade,
            take_profit=tp_price,
            stop_loss=sl_price,
            strategy_case=case_num,
            fib_high=result.fib_levels.get('high'),
            fib_low=result.fib_levels.get('low')
        )
        if order:
            print(f"   🟠 CASO 3 | {result.symbol}: LIMIT @ ${limit_price:.4f} → TP ${tp_price:.4f}")
            order_placed = True
    
    elif case_num == 2:
        # Caso 2: MARKET, TP 45%
        fresh_price = await scanner.get_current_price(result.symbol)
        if not fresh_price:
            return False
        
        level_case2_min = result.fib_levels.get('low', 0) + fib_range * 0.618
        level_case2_max = result.fib_levels.get('low', 0) + fib_range * 0.69
        
        if fresh_price < level_case2_min or fresh_price >= level_case2_max:
            print(f"   ⚠️ {result.symbol}: Precio cambió, ya no está en zona C2")
            return False
        
        tp_price = result.fib_levels['45']
        position = account.place_market_order(
            symbol=result.symbol,
            side=OrderSide.SELL,
            current_price=fresh_price,
            margin=margin_per_trade,
            take_profit=tp_price,
            stop_loss=sl_price,
            strategy_case=case_num,
            fib_high=result.fib_levels.get('high'),
            fib_low=result.fib_levels.get('low')
        )
        if position:
            print(f"   🟡 CASO 2 | {result.symbol}: MARKET @ ${fresh_price:.4f} → TP ${tp_price:.4f}")
            order_placed = True
    
    elif case_num == 1:
        # Caso 1 / Caso 1++: LIMIT 61.8%, TP 45%
        case_label = "CASO 1++" if result.path == 2 else "CASO 1"
        strategy_case_value = 11 if result.path == 2 else 1
        
        tp_price = result.fib_levels['45']
        limit_price = result.fib_levels['618']
        
        order = account.place_limit_order(
            symbol=result.symbol,
            side=OrderSide.SELL,
            price=limit_price,
            margin=margin_per_trade,
            take_profit=tp_price,
            stop_loss=sl_price,
            strategy_case=strategy_case_value,
            fib_high=result.fib_levels.get('high'),
            fib_low=result.fib_levels.get('low')
        )
        if order:
            print(f"   🟢 {case_label} | {result.symbol}: LIMIT @ ${limit_price:.4f} → TP ${tp_price:.4f}")
            order_placed = True
    
    return order_placed


async def _search_and_place_c1pp(scanner, account, symbol, current_high, current_low, margin_per_trade, sl_price, OrderSide, session):
    """
    Buscar el siguiente High MÁS ALTO a la izquierda (más antiguo) y su Low para C1++
    La zona válida para C1++ es 0%-61.8%
    Si 61.8% ya fue tocado, busca el siguiente High más alto a la izquierda
    """
    from fibonacci import calculate_zigzag, calculate_fibonacci_levels
    from config import TIMEFRAME
    
    try:
        print(f"      🔍 Buscando C1++ para {symbol}...")
        
        # Obtener velas del par
        candle_data = await scanner.fetch_klines(session, symbol, TIMEFRAME, limit=500)
        
        if not candle_data or len(candle_data) < 50:
            print(f"      ❌ C1++ {symbol}: No hay suficientes velas")
            return
        
        # Obtener pivotes ZigZag
        zigzag_pivots = calculate_zigzag(candle_data, TIMEFRAME)
        
        if not zigzag_pivots or len(zigzag_pivots) < 2:
            print(f"      ❌ C1++ {symbol}: No hay suficientes pivotes ZigZag")
            return
        
        # Encontrar el índice del High actual del C2/C3/C4
        current_high_pivot = None
        for p in zigzag_pivots:
            if p.type == 'high' and abs(p.price - current_high) < current_high * 0.001:  # Tolerancia 0.1%
                current_high_pivot = p
                break
        
        if not current_high_pivot:
            print(f"      ❌ C1++ {symbol}: No se encontró el High actual en los pivotes")
            return
        
        # Buscar Highs MÁS ALTOS que estén A LA IZQUIERDA (índice menor) del High actual
        left_higher_highs = [p for p in zigzag_pivots 
                            if p.type == 'high' 
                            and p.index < current_high_pivot.index 
                            and p.price > current_high]
        
        if not left_higher_highs:
            # Debug: mostrar todos los Highs a la izquierda
            all_left_highs = [p for p in zigzag_pivots 
                              if p.type == 'high' and p.index < current_high_pivot.index]
            if all_left_highs:
                max_left = max(all_left_highs, key=lambda x: x.price)
                print(f"      ❌ C1++ {symbol}: No hay Highs más altos a la izquierda (High actual: ${current_high:.4f}, Max izq: ${max_left.price:.4f})")
            else:
                print(f"      ❌ C1++ {symbol}: No hay Highs a la izquierda del pivote actual")
            return
        
        # Ordenar por precio descendente (el más alto primero)
        left_higher_highs.sort(key=lambda x: x.price, reverse=True)
        
        current_price = candle_data[-1]['close']
        last_candle_index = len(candle_data) - 1
        
        print(f"      📊 C1++ {symbol}: Encontrados {len(left_higher_highs)} Highs más altos a la izquierda")
        
        # Iterar por cada High más alto a la izquierda hasta encontrar C1++ válido
        for idx, alt_high in enumerate(left_higher_highs):
            alt_high_idx = alt_high.index
            
            # Buscar el Low más bajo entre este High y la vela actual
            candles_after_high = candle_data[alt_high_idx + 1:]
            if not candles_after_high:
                continue
            
            lowest_candle = min(candles_after_high, key=lambda x: x['low'])
            lowest_price = lowest_candle['low']
            lowest_idx = candle_data.index(lowest_candle)
            
            # Calcular rango y niveles Fib
            alt_range = alt_high.price - lowest_price
            if alt_range <= 0:
                continue
            
            fib_618 = lowest_price + (alt_range * 0.618)
            fib_90 = lowest_price + (alt_range * 0.90)
            
            # Verificar que 90% NO haya sido tocado
            touched_90 = False
            for k in range(lowest_idx + 1, last_candle_index + 1):
                if candle_data[k]['high'] >= fib_90:
                    touched_90 = True
                    break
            
            if touched_90:
                print(f"      ⚠️ C1++ {symbol}: 90% tocado para High #{idx+1} (${alt_high.price:.4f}), probando siguiente...")
                continue
            
            # Verificar que 61.8% NO haya sido tocado
            touched_618 = False
            for k in range(lowest_idx + 1, last_candle_index + 1):
                if candle_data[k]['high'] >= fib_618:
                    touched_618 = True
                    break
            
            if touched_618:
                print(f"      ⚠️ C1++ {symbol}: 61.8% ya tocado para High #{idx+1} (${alt_high.price:.4f}), probando siguiente...")
                continue
            
            # Precio debe estar en zona 0%-61.8% para C1++ válido
            if current_price >= fib_618:
                print(f"      ⚠️ C1++ {symbol}: Precio actual encima de 61.8% para High #{idx+1}, probando siguiente...")
                continue
            
            # ¡Encontramos C1++ válido!
            print(f"      ✅ C1++ {symbol}: Encontrado swing válido - High ${alt_high.price:.4f}")
            
            alt_levels = calculate_fibonacci_levels(alt_high.price, lowest_price)
            tp_price = alt_levels['45']
            limit_price = alt_levels['618']
            
            # Verificar que no exista ya esta combinación
            existing = False
            for o in account.pending_orders.values():
                if o.symbol == symbol and o.strategy_case == 11:
                    existing = True
                    break
            for p in account.open_positions.values():
                if p.symbol == symbol and p.strategy_case == 11:
                    existing = True
                    break
            
            if existing:
                return
            
            order = account.place_limit_order(
                symbol=symbol,
                side=OrderSide.SELL,
                price=limit_price,
                margin=margin_per_trade,
                take_profit=tp_price,
                stop_loss=sl_price,
                strategy_case=11,  # Caso 1++
                fib_high=alt_high.price,
                fib_low=lowest_price
            )
            
            if order:
                print(f"   🟣 CASO 1++ | {symbol}: LIMIT @ ${limit_price:.4f} → TP ${tp_price:.4f} (High: ${alt_high.price:.4f})")
            
            return  # Solo colocar 1 C1++ por par
            
    except Exception as e:
        print(f"      ❌ Error buscando C1++ para {symbol}: {e}")
