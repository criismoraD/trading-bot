"""
Scanner Multi-Par para Binance Futures
Escanea top 100 pares, filtra por RSI >= 75, aplica prioridad de casos
"""
import asyncio
import aiohttp
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

from config import REST_BASE_URL, MARGIN_PER_TRADE, MIN_AVAILABLE_MARGIN, TIMEFRAME, CANDLE_LIMIT
from fibonacci import calculate_zigzag, find_valid_fibonacci_swing, determine_trading_case


@dataclass
class ScanResult:
    symbol: str
    rsi: float
    case: int
    current_price: float
    fib_levels: Dict[str, float]
    is_valid: bool


class MarketScanner:
    def __init__(self, top_n: int = 100):
        from config import RSI_THRESHOLD
        self.top_n = top_n
        self.rsi_period = 14
        self.rsi_threshold = RSI_THRESHOLD  # Leer de config.py
        self.pairs_cache: List[str] = []
        self.last_scan_results: Dict[str, ScanResult] = {}
    
    async def get_top_pairs(self) -> List[str]:
        """Obtener top N pares por volumen de Binance Futures"""
        from config import EXCLUDED_PAIRS # Importar aquí para evitar ciclo si no está arriba
        url = f"{REST_BASE_URL}/fapi/v1/ticker/24hr"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        print(f"❌ Error obteniendo pares: {response.status}")
                        return self.pairs_cache or []
                    
                    data = await response.json()
                    
                    # Filtrar y ordenar por volumen
                    usdt_pairs = [
                        item for item in data 
                        if item['symbol'].endswith('USDT') 
                        and 'USDT' not in item['symbol'][:-4]  # Excluir USDTUSDT
                        and 'BTCDOM' not in item['symbol']
                        and item['symbol'] not in EXCLUDED_PAIRS # FILTRO CRÍTICO
                    ]
                    
                    # Ordenar por volumen (quoteVolume = volumen en USDT)
                    sorted_pairs = sorted(
                        usdt_pairs, 
                        key=lambda x: float(x['quoteVolume']), 
                        reverse=True
                    )
                    
                    self.pairs_cache = [p['symbol'] for p in sorted_pairs[:self.top_n]]
                    print(f"📊 Top {len(self.pairs_cache)} pares cargados (excluidos {len(EXCLUDED_PAIRS)} pares prohibidos)")
                    return self.pairs_cache
                    
        except Exception as e:
            print(f"❌ Error en get_top_pairs: {e}")
            return self.pairs_cache or []
    
    async def fetch_klines(self, session: aiohttp.ClientSession, 
                           symbol: str, interval: str = '5m', 
                           limit: int = 100) -> List[dict]:
        """Obtener velas para un par"""
        url = f"{REST_BASE_URL}/fapi/v1/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        
        try:
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    return []
                data = await response.json()
                return [
                    {
                        "time": int(c[0]) // 1000,
                        "open": float(c[1]),
                        "high": float(c[2]),
                        "low": float(c[3]),
                        "close": float(c[4]),
                        "volume": float(c[5])
                    }
                    for c in data
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
                        symbol: str) -> Optional[ScanResult]:
        """Escanear un par individual"""
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
            swing = find_valid_fibonacci_swing(zigzag, candles)
            
            if not swing or not swing.is_valid:
                print(f"   [DEBUG] {symbol}: No hay swing válido")
                return None
            
            current_price = candles[-1]['close']
            case = determine_trading_case(current_price, swing)
            
            print(f"   ✅ {symbol}: Swing válido! Precio ${current_price:.4f} -> CASO {case}")
            
            if case == 0:
                print(f"   [DEBUG] {symbol}: Caso 0 (inválido)")
                return None
            
            return ScanResult(
                symbol=symbol,
                rsi=rsi,
                case=case,
                current_price=current_price,
                fib_levels={
                    '50': swing.levels.get('50', 0),
                    '55': swing.levels.get('55', swing.low.price + (swing.high.price - swing.low.price) * 0.55),
                    '60': swing.levels.get('60', swing.low.price + (swing.high.price - swing.low.price) * 0.60),
                    '62': swing.levels.get('62', swing.low.price + (swing.high.price - swing.low.price) * 0.62),
                    '618': swing.levels.get('61.8', 0),
                    '69': swing.levels.get('69', swing.low.price + (swing.high.price - swing.low.price) * 0.69),
                    '75': swing.levels.get('75', 0),
                    '786': swing.levels.get('78.6', 0),
                    'high': swing.high.price,  # Precio del High (100%)
                    'low': swing.low.price     # Precio del Low (0%)
                },
                is_valid=True
            )
        except Exception as e:
            print(f"   ❌ {symbol}: Error - {e}")
            return None
    
    async def scan_all_pairs(self, pairs: List[str]) -> Dict[int, List[ScanResult]]:
        """Escanear todos los pares y agrupar por caso"""
        results = {1: [], 2: [], 3: [], 4: []}
        
        async with aiohttp.ClientSession() as session:
            # Escanear en lotes (batch)
            batch_size = 50  # Aumentado para mayor velocidad
            total_pairs = len(pairs)
            
            print(f"📊 Iniciando escaneo de {total_pairs} pares...")
            
            for i in range(0, total_pairs, batch_size):
                batch = pairs[i:i+batch_size]
                
                # Mostrar progreso visual
                print(f"   ⚡ Escaneando bloque {i+1}-{min(i+batch_size, total_pairs)} ({batch[0]}...)...")
                
                tasks = [self.scan_pair(session, symbol) for symbol in batch]
                batch_results = await asyncio.gather(*tasks)
                
                for result in batch_results:
                    if result and result.is_valid:
                        results[result.case].append(result)
                        self.last_scan_results[result.symbol] = result
                
                # Pequeña pausa para evitar rate limits
                await asyncio.sleep(0.5)
        
        print(f"🔍 Scan: Caso 3: {len(results[3])} | Caso 2: {len(results[2])} | Caso 1: {len(results[1])}")
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
                    # Endpoint correcto para Futuros: /fapi/v1/ticker/price
                    url = f"{REST_BASE_URL}/fapi/v1/ticker/price?symbol={symbol}"
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()
                            price = float(data['price'])
                            
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
    Ejecutar escaneo SIN prioridad de casos
    Todos los casos se procesan en orden de aparición
    """
    from paper_trading import OrderSide
    
    # Usar cache si está definido, sino hacer fetch
    if scanner.pairs_cache:
        pairs = scanner.pairs_cache
        print(f"📊 Usando {len(pairs)} par(es) definidos: {', '.join(pairs)}")
    else:
        pairs = await scanner.get_top_pairs()
    
    if not pairs:
        print("❌ No se pudieron obtener pares")
        return
    
    scan_results = await scanner.scan_all_pairs(pairs)
    
    # Combinar todos los resultados sin prioridad
    all_results = []
    for case_num in [1, 2, 3, 4]:
        for result in scan_results.get(case_num, []):
            all_results.append((case_num, result))
    
    print(f"\n📊 Encontrados: {len(all_results)} pares con swing válido")
    
    # Procesar todos los resultados en orden de aparición
    for case_num, result in all_results:
        if account.get_available_margin() < MIN_AVAILABLE_MARGIN:
            print(f"⚠️ Margen mínimo alcanzado: ${account.get_available_margin():.2f}")
            break
        
        # Saltar si ya hay posición u orden en este par
        if any(p.symbol == result.symbol for p in account.open_positions.values()):
            continue
        if any(o.symbol == result.symbol for o in account.pending_orders.values()):
            continue
        
        # Ejecutar según el caso
        if case_num == 4:
            # Caso 4: MARKET, TP 62%, SL 105%
            tp_price = result.fib_levels.get('62', result.fib_levels['618'])
            # Calcular SL en nivel 105% (5% por encima del High)
            fib_range = result.fib_levels.get('high', 0) - result.fib_levels.get('low', 0)
            sl_price = result.fib_levels.get('low', 0) + (fib_range * 1.05) if fib_range > 0 else None
            position = account.place_market_order(
                symbol=result.symbol,
                side=OrderSide.SELL,
                current_price=result.current_price,
                margin=margin_per_trade,
                take_profit=tp_price,
                stop_loss=sl_price,
                strategy_case=case_num,
                fib_high=result.fib_levels.get('high'),
                fib_low=result.fib_levels.get('low')
            )
            if position:
                print(f"   🔴 CASO 4 | {result.symbol}: MARKET @ ${result.current_price:.4f} → TP ${tp_price:.4f} | SL ${sl_price:.4f}")
        
        elif case_num == 3:
            # Caso 3: LIMIT 78.6%, TP 62%, SL 105%
            limit_price = result.fib_levels['786']
            tp_price = result.fib_levels.get('62', result.fib_levels['618'])
            # Calcular SL en nivel 105%
            fib_range = result.fib_levels.get('high', 0) - result.fib_levels.get('low', 0)
            sl_price = result.fib_levels.get('low', 0) + (fib_range * 1.05) if fib_range > 0 else None
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
                print(f"   🟠 CASO 3 | {result.symbol}: LIMIT @ ${limit_price:.4f} → TP ${tp_price:.4f} | SL ${sl_price:.4f}")
        
        elif case_num == 2:
            # Caso 2: MARKET + LIMIT 78.6% + LIMIT 120%, SL 130%
            tp_price = result.fib_levels['55']
            # Calcular niveles adicionales
            fib_range = result.fib_levels.get('high', 0) - result.fib_levels.get('low', 0)
            limit_price_120 = result.fib_levels.get('low', 0) + (fib_range * 1.20) if fib_range > 0 else None
            sl_price = result.fib_levels.get('low', 0) + (fib_range * 1.30) if fib_range > 0 else None
            
            position = account.place_market_order(
                symbol=result.symbol,
                side=OrderSide.SELL,
                current_price=result.current_price,
                margin=margin_per_trade,
                take_profit=tp_price,
                stop_loss=sl_price,
                strategy_case=case_num,
                fib_high=result.fib_levels.get('high'),
                fib_low=result.fib_levels.get('low')
            )
            if position and account.get_available_margin() >= MIN_AVAILABLE_MARGIN:
                limit_price = result.fib_levels['786']
                linked_order = account.place_limit_order(
                    symbol=result.symbol,
                    side=OrderSide.SELL,
                    price=limit_price,
                    margin=margin_per_trade,
                    take_profit=tp_price,
                    stop_loss=sl_price,
                    linked_order_id=position.order_id,
                    strategy_case=case_num,
                    fib_high=result.fib_levels.get('high'),
                    fib_low=result.fib_levels.get('low')
                )
                # Tercera orden límite en 120%
                if linked_order and limit_price_120 and account.get_available_margin() >= MIN_AVAILABLE_MARGIN:
                    third_order = account.place_limit_order(
                        symbol=result.symbol,
                        side=OrderSide.SELL,
                        price=limit_price_120,
                        margin=margin_per_trade,
                        take_profit=tp_price,
                        stop_loss=sl_price,
                        linked_order_id=position.order_id,
                        strategy_case=case_num,
                        fib_high=result.fib_levels.get('high'),
                        fib_low=result.fib_levels.get('low')
                    )
                    if third_order:
                        print(f"   🟡 CASO 2 | {result.symbol}: MARKET + LIMIT@${limit_price:.4f} + LIMIT@${limit_price_120:.4f} → TP ${tp_price:.4f} | SL ${sl_price:.4f}")
                elif linked_order:
                    print(f"   🟡 CASO 2 | {result.symbol}: MARKET + LIMIT @ ${limit_price:.4f} → TP ${tp_price:.4f} | SL ${sl_price:.4f}")
        
        elif case_num == 1:
            # Caso 1: 2 LIMIT (61.8% + 78.6%), TP 55%, SL 90%
            if account.get_available_margin() < MIN_AVAILABLE_MARGIN * 2:
                continue
            tp_price = result.fib_levels['55']
            limit_price_1 = result.fib_levels['618']
            # SL en nivel 90%
            fib_range = result.fib_levels.get('high', 0) - result.fib_levels.get('low', 0)
            sl_price = result.fib_levels.get('low', 0) + (fib_range * 0.90) if fib_range > 0 else None
            
            order1 = account.place_limit_order(
                symbol=result.symbol,
                side=OrderSide.SELL,
                price=limit_price_1,
                margin=margin_per_trade,
                take_profit=tp_price,
                stop_loss=sl_price,
                strategy_case=case_num,
                fib_high=result.fib_levels.get('high'),
                fib_low=result.fib_levels.get('low')
            )
            if order1 and account.get_available_margin() >= MIN_AVAILABLE_MARGIN:
                limit_price_2 = result.fib_levels['786']
                order2 = account.place_limit_order(
                    symbol=result.symbol,
                    side=OrderSide.SELL,
                    price=limit_price_2,
                    margin=margin_per_trade,
                    take_profit=tp_price,
                    stop_loss=sl_price,
                    linked_order_id=order1.id,
                    strategy_case=case_num,
                    fib_high=result.fib_levels.get('high'),
                    fib_low=result.fib_levels.get('low')
                )
                if order2:
                    print(f"   🟢 CASO 1 | {result.symbol}: LIMIT @ ${limit_price_1:.4f} + LIMIT @ ${limit_price_2:.4f} | SL ${sl_price:.4f}")
    
    print(f"\n💰 Margen disponible: ${account.get_available_margin():.2f}")


async def run_priority_scan_real(scanner: MarketScanner, binance_trader, margin_per_trade: float = 3.0, leverage: int = 20):
    """
    Ejecutar escaneo con trading REAL en Binance Futures
    Usa margen cruzado y las mismas reglas que paper trading
    """
    
    # Usar cache si está definido, sino hacer fetch
    if scanner.pairs_cache:
        pairs = scanner.pairs_cache
        print(f"📊 [REAL] Usando {len(pairs)} par(es) definidos: {', '.join(pairs)}")
    else:
        pairs = await scanner.get_top_pairs()
    
    if not pairs:
        print("❌ No se pudieron obtener pares")
        return
    
    scan_results = await scanner.scan_all_pairs(pairs)
    
    # Combinar todos los resultados sin prioridad
    all_results = []
    for case_num in [1, 2, 3, 4]:
        for result in scan_results.get(case_num, []):
            all_results.append((case_num, result))
    
    print(f"\n📊 [REAL] Encontrados: {len(all_results)} pares con swing válido")
    
    # Obtener balance actual de Binance
    try:
        balance_info = await binance_trader.get_account_balance()
        available_balance = float(balance_info.get('availableBalance', 0))
        print(f"💰 [REAL] Balance disponible: ${available_balance:.2f} USDT")
    except Exception as e:
        print(f"❌ Error obteniendo balance: {e}")
        return
    
    # Obtener posiciones abiertas actuales
    try:
        positions = await binance_trader.get_positions()
        open_symbols = list(positions.keys())  # get_positions devuelve dict {symbol: BinancePosition}
        if open_symbols:
            print(f"📈 [REAL] Posiciones abiertas: {', '.join(open_symbols)}")
    except Exception as e:
        print(f"⚠️ Error obteniendo posiciones: {e}")
        open_symbols = []
    
    # Obtener órdenes abiertas (solo LIMIT, no TP/SL)
    try:
        open_orders = await binance_trader.get_open_orders()
        order_symbols = list(set(o.symbol for o in open_orders if o.order_type == "LIMIT"))
        if order_symbols:
            print(f"📋 [REAL] Órdenes pendientes en: {', '.join(order_symbols)}")
    except Exception as e:
        print(f"⚠️ Error obteniendo órdenes: {e}")
        order_symbols = []
    
    # Procesar todos los resultados en orden de aparición
    for case_num, result in all_results:
        # Verificar margen mínimo
        if available_balance < MIN_AVAILABLE_MARGIN:
            print(f"⚠️ [REAL] Margen mínimo alcanzado: ${available_balance:.2f}")
            break
        
        # Saltar si ya hay posición u orden en este par
        if result.symbol in open_symbols:
            print(f"   ⏭️ {result.symbol}: Ya tiene posición abierta")
            continue
        if result.symbol in order_symbols:
            print(f"   ⏭️ {result.symbol}: Ya tiene orden pendiente")
            continue
        
        try:
            # Ejecutar según el caso
            if case_num == 4:
                # Caso 4: MARKET, TP 62%, SL 105%
                tp_price = result.fib_levels.get('62', result.fib_levels['618'])
                fib_range = result.fib_levels.get('high', 0) - result.fib_levels.get('low', 0)
                sl_price = result.fib_levels.get('low', 0) + (fib_range * 1.05) if fib_range > 0 else None
                fib_high = result.fib_levels.get('high')
                fib_low = result.fib_levels.get('low')
                
                success = await binance_trader.execute_short_entry(
                    symbol=result.symbol,
                    margin=margin_per_trade,
                    leverage=leverage,
                    entry_price=result.current_price,
                    take_profit=tp_price,
                    stop_loss=sl_price,
                    strategy_case=case_num,
                    fib_high=fib_high,
                    fib_low=fib_low
                )
                if success:
                    print(f"   🔴 [REAL] CASO 4 | {result.symbol}: MARKET @ ${result.current_price:.4f} → TP ${tp_price:.4f} | SL ${sl_price:.4f}")
                    available_balance -= margin_per_trade
            
            elif case_num == 3:
                # Caso 3: LIMIT 78.6%, TP 62%, SL 105%
                limit_price = result.fib_levels['786']
                tp_price = result.fib_levels.get('62', result.fib_levels['618'])
                fib_range = result.fib_levels.get('high', 0) - result.fib_levels.get('low', 0)
                sl_price = result.fib_levels.get('low', 0) + (fib_range * 1.05) if fib_range > 0 else None
                fib_high = result.fib_levels.get('high')
                fib_low = result.fib_levels.get('low')
                
                success = await binance_trader.execute_limit_short(
                    symbol=result.symbol,
                    margin=margin_per_trade,
                    leverage=leverage,
                    limit_price=limit_price,
                    take_profit=tp_price,
                    stop_loss=sl_price,
                    strategy_case=case_num,
                    fib_high=fib_high,
                    fib_low=fib_low,
                    is_linked_order=False
                )
                if success:
                    print(f"   🟠 [REAL] CASO 3 | {result.symbol}: LIMIT @ ${limit_price:.4f} → TP ${tp_price:.4f} | SL ${sl_price:.4f}")
                    available_balance -= margin_per_trade
            
            elif case_num == 2:
                # Caso 2: MARKET + LIMIT 78.6% + LIMIT 120%, SL 130%
                tp_price = result.fib_levels['55']
                fib_range = result.fib_levels.get('high', 0) - result.fib_levels.get('low', 0)
                limit_price_786 = result.fib_levels['786']
                limit_price_120 = result.fib_levels.get('low', 0) + (fib_range * 1.20) if fib_range > 0 else None
                sl_price = result.fib_levels.get('low', 0) + (fib_range * 1.30) if fib_range > 0 else None
                fib_high = result.fib_levels.get('high')
                fib_low = result.fib_levels.get('low')
                
                # Primero orden MARKET
                success1 = await binance_trader.execute_short_entry(
                    symbol=result.symbol,
                    margin=margin_per_trade,
                    leverage=leverage,
                    entry_price=result.current_price,
                    take_profit=tp_price,
                    stop_loss=sl_price,
                    strategy_case=case_num,
                    fib_high=fib_high,
                    fib_low=fib_low
                )
                
                if success1:
                    available_balance -= margin_per_trade
                    orders_placed = ["MARKET"]
                    
                    # Segunda orden LIMIT en 78.6% (LINKED - TP dinámico)
                    if available_balance >= MIN_AVAILABLE_MARGIN:
                        success2 = await binance_trader.execute_limit_short(
                            symbol=result.symbol,
                            margin=margin_per_trade,
                            leverage=leverage,
                            limit_price=limit_price_786,
                            take_profit=tp_price,
                            stop_loss=sl_price,
                            strategy_case=case_num,
                            fib_high=fib_high,
                            fib_low=fib_low,
                            is_linked_order=True  # TP dinámico
                        )
                        if success2:
                            available_balance -= margin_per_trade
                            orders_placed.append(f"LIMIT@${limit_price_786:.4f}")
                    
                    # Tercera orden LIMIT en 120% (LINKED - TP dinámico)
                    if limit_price_120 and available_balance >= MIN_AVAILABLE_MARGIN:
                        success3 = await binance_trader.execute_limit_short(
                            symbol=result.symbol,
                            margin=margin_per_trade,
                            leverage=leverage,
                            limit_price=limit_price_120,
                            take_profit=tp_price,
                            stop_loss=sl_price,
                            strategy_case=case_num,
                            fib_high=fib_high,
                            fib_low=fib_low,
                            is_linked_order=True  # TP dinámico
                        )
                        if success3:
                            available_balance -= margin_per_trade
                            orders_placed.append(f"LIMIT@${limit_price_120:.4f}")
                    
                    print(f"   🟡 [REAL] CASO 2 | {result.symbol}: {' + '.join(orders_placed)} → TP ${tp_price:.4f} | SL ${sl_price:.4f}")
            
            elif case_num == 1:
                # Caso 1: 2 LIMIT (61.8% + 78.6%), TP 55%, SL 90%
                if available_balance < MIN_AVAILABLE_MARGIN * 2:
                    continue
                
                tp_price = result.fib_levels['55']
                limit_price_1 = result.fib_levels['618']
                limit_price_2 = result.fib_levels['786']
                fib_range = result.fib_levels.get('high', 0) - result.fib_levels.get('low', 0)
                sl_price = result.fib_levels.get('low', 0) + (fib_range * 0.90) if fib_range > 0 else None
                fib_high = result.fib_levels.get('high')
                fib_low = result.fib_levels.get('low')
                
                # Primera orden LIMIT (no linked - es la primera)
                success1 = await binance_trader.execute_limit_short(
                    symbol=result.symbol,
                    margin=margin_per_trade,
                    leverage=leverage,
                    limit_price=limit_price_1,
                    take_profit=tp_price,
                    stop_loss=sl_price,
                    strategy_case=case_num,
                    fib_high=fib_high,
                    fib_low=fib_low,
                    is_linked_order=False
                )
                
                if success1:
                    available_balance -= margin_per_trade
                    
                    if available_balance >= MIN_AVAILABLE_MARGIN:
                        # Segunda orden LIMIT (LINKED - TP dinámico)
                        success2 = await binance_trader.execute_limit_short(
                            symbol=result.symbol,
                            margin=margin_per_trade,
                            leverage=leverage,
                            limit_price=limit_price_2,
                            take_profit=tp_price,
                            stop_loss=sl_price,
                            strategy_case=case_num,
                            fib_high=fib_high,
                            fib_low=fib_low,
                            is_linked_order=True  # TP dinámico
                        )
                        if success2:
                            available_balance -= margin_per_trade
                            print(f"   🟢 [REAL] CASO 1 | {result.symbol}: LIMIT @ ${limit_price_1:.4f} + LIMIT @ ${limit_price_2:.4f} (TP dinámico) | SL ${sl_price:.4f}")
        
        except Exception as e:
            print(f"   ❌ Error en {result.symbol} (Caso {case_num}): {e}")
            continue
    
    print(f"\n💰 [REAL] Balance estimado restante: ${available_balance:.2f}")

