"""
Bot de Trading Fibonacci - Paper Trading
Logica de entradas SHORT con datos de Bybit
"""
import asyncio
import json
import websockets
import aiohttp
from datetime import datetime
from typing import List, Dict, Optional

from config import (
    INITIAL_BALANCE, LEVERAGE, MARGIN_PER_TRADE, MAX_MARGIN_PER_TRADE, TARGET_PROFIT, COMMISSION_RATE,
    DEFAULT_SYMBOL, WS_BASE_URL, REST_BASE_URL,
    TIMEFRAME, CANDLE_LIMIT, TRADES_FILE,
    TOP_PAIRS_LIMIT, RSI_THRESHOLD, FIRST_SCAN_DELAY, SCAN_INTERVAL, MIN_AVAILABLE_MARGIN,
    TRADING_MODE, BYBIT_API_KEY, BYBIT_API_SECRET, BYBIT_TESTNET, BYBIT_INITIAL_BALANCE
)
from paper_trading import PaperTradingAccount, OrderSide
from fibonacci import (
    calculate_zigzag, find_valid_fibonacci_swing, 
    determine_trading_case, FibonacciSwing
)

# Nuevos módulos
from logger import bot_logger as logger, trading_logger, log_trade, log_scan_result
from telegram_bot import telegram_bot, notify_trade_open, notify_trade_close, notify_limit_filled
from metrics import PerformanceCalculator, performance_calculator
from web_server import start_web_server


# ===== CLASE LEGACY - ACTUALMENTE NO SE USA =====
# La lógica principal ahora está en main() usando MarketScanner
# Esta clase se mantiene para referencia y posible uso futuro con un solo par
# 
# class FibonacciTradingBot:
#     """Bot de trading para un solo par (legacy)"""
#     ... (ver implementación original si se necesita)
#


class FibonacciTradingBot:
    def __init__(self, symbol: str = DEFAULT_SYMBOL):
        self.symbol = symbol.upper()
        self.timeframe = TIMEFRAME
        self.candle_data: List[dict] = []
        self.current_price: float = 0.0
        self.current_swing: Optional[FibonacciSwing] = None
        self.last_case_executed: int = 0
        
        # Inicializar cuenta paper trading
        self.account = PaperTradingAccount(
            initial_balance=INITIAL_BALANCE,
            leverage=LEVERAGE,
            trades_file=TRADES_FILE
        )
        
        # Control de ejecución
        self.running = False
        self.ws_connection = None
    
    async def fetch_historical_data(self):
        """Obtener datos históricos de velas"""
        url = f"{REST_BASE_URL}/fapi/v1/klines"
        params = {
            "symbol": self.symbol,
            "interval": self.timeframe,
            "limit": CANDLE_LIMIT
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    self.candle_data = [
                        {
                            "time": int(candle[0]) // 1000,
                            "open": float(candle[1]),
                            "high": float(candle[2]),
                            "low": float(candle[3]),
                            "close": float(candle[4]),
                            "volume": float(candle[5])
                        }
                        for candle in data
                    ]
                    print(f"📊 Cargadas {len(self.candle_data)} velas de {self.symbol}")
                else:
                    print(f"❌ Error obteniendo datos: {response.status}")
    
    def analyze_fibonacci(self) -> Optional[FibonacciSwing]:
        """Analizar y obtener swing Fibonacci válido"""
        if len(self.candle_data) < 50:
            return None
        
        # Calcular ZigZag
        zigzag_points = calculate_zigzag(self.candle_data, self.timeframe)
        
        if len(zigzag_points) < 2:
            print("⚠️ No hay suficientes puntos ZigZag")
            return None
        
        # Encontrar swing válido
        swing = find_valid_fibonacci_swing(zigzag_points, self.candle_data)
        
        return swing
    
    def execute_trading_logic(self, current_price: float):
        """Ejecutar lógica de trading según el caso"""
        if not self.current_swing or not self.current_swing.is_valid:
            return
            
        if self.account.get_available_margin() < MIN_AVAILABLE_MARGIN:
            # Silencioso en el log de consola por cada tick, pero bloquea ejecucion
            return
        
        case = determine_trading_case(current_price, self.current_swing)
        
        # Evitar ejecutar el mismo caso múltiples veces para el mismo swing
        if case == self.last_case_executed:
            return
        
        # Para Casos 3, verificar que estemos en zona de entrada (55%+)
        # Para Caso 1, siempre colocamos órdenes límite (se ejecutarán cuando el precio suba)
        if case == 3 and not self.current_swing.current_candle_at_55:
            print(f"⏳ Precio en zona de Caso {case} pero esperando confirmación en 55%+")
            return
        
        levels = self.current_swing.levels
        
        # Cargar TPs y SLs desde shared_config.json
        try:
            with open('shared_config.json', 'r') as f:
                shared_cfg = json.load(f)
                strategies = shared_cfg.get('strategies', {})
                c1_cfg = strategies.get('c1', {'tp': 0.51, 'sl': 0.67})
                c3_cfg = strategies.get('c3', {'tp': 0.50, 'sl': 1.05})
                c4_cfg = strategies.get('c4', {'tp': 0.50, 'sl': 1.05})
        except Exception:
            # Valores por defecto si no se puede leer el archivo
            c1_cfg = {'tp': 0.51, 'sl': 0.67}
            c3_cfg = {'tp': 0.50, 'sl': 1.05}
            c4_cfg = {'tp': 0.50, 'sl': 1.05}
        
        fib_range = self.current_swing.high - self.current_swing.low
        fib_low = self.current_swing.low
        
        # Calcular precios de TP/SL desde niveles Fibonacci (Caso 2 eliminado)
        tp_c1 = fib_low + (fib_range * c1_cfg['tp'])
        tp_c3 = fib_low + (fib_range * c3_cfg['tp'])
        tp_c4 = fib_low + (fib_range * c4_cfg['tp'])
        
        sl_c1 = fib_low + (fib_range * c1_cfg['sl'])
        sl_c3 = fib_low + (fib_range * c3_cfg['sl'])
        sl_c4 = fib_low + (fib_range * c4_cfg['sl'])
        
        level_68 = levels.get('68', fib_low + fib_range * 0.68)  # Caso 1: LIMIT SELL al 68%
        level_786 = levels["78.6"]
        
        # --- Nueva Lógica: Ganancia Bruta y Protección de Comisiones ---
        def calculate_trade_params(entry_price, tp_price):
            """
            Calcula Qty para Ganancia Bruta = TARGET_PROFIT
            Retorna (Qty, Margin, Estimated_Commission, Allowed)
            """
            price_diff = abs(entry_price - tp_price)
            if price_diff == 0:
                print(f"⚠️ Error: Diferencia de precio 0 en {self.symbol}")
                return 0, 0, 0, False
                
            # 1. Calcular Qty para Ganancia Bruta (TARGET_PROFIT = $1)
            # Ganancia Bruta = Qty * |Entry - TP|
            qty = TARGET_PROFIT / price_diff
            
            # 2. Calcular Margin Requerido
            margin = (qty * entry_price) / LEVERAGE
            
            # 3. Calcular Comisión Estimada (Apertura + Cierre)
            # Asumimos peor caso: Taker en Open (si es Market) y Maker en Close (TP Limit)
            # O Maker/Maker si es Limit. Para seguridad usamos COMMISSION_RATE general
            est_commission = qty * (entry_price + tp_price) * COMMISSION_RATE
            
            # 4. Regla de Protección: Comisión < 50% de la Ganancia Bruta
            # Si ganamos $1, no queremos pagar más de $0.50 en comisiones
            if est_commission > (TARGET_PROFIT / 2):
                print(f"🚫 {self.symbol}: Comisión alta (${est_commission:.4f}) vs Profit (${TARGET_PROFIT})")
                return qty, margin, est_commission, False
                
            if margin > MAX_MARGIN_PER_TRADE:
                 qty = (MAX_MARGIN_PER_TRADE * LEVERAGE) / entry_price
                 margin = MAX_MARGIN_PER_TRADE
                 est_commission = qty * (entry_price + tp_price) * COMMISSION_RATE
            
            return qty, margin, est_commission, True

        # Calcular parámetros para C1 y C3
        qty_c1, margin_c1, comm_c1, allowed_c1 = calculate_trade_params(level_68, tp_c1)
        qty_c3, margin_c3, comm_c3, allowed_c3 = calculate_trade_params(current_price, tp_c3)
        # --------------------------------------------------------
        
        print(f"\n🎯 CASO {case} detectado | Precio: ${current_price:.4f}")
        print(f"   Niveles: 61.8%=${level_618:.4f} | 78.6%=${level_786:.4f}")
        
        if case == 1:
            if not allowed_c1:
                return

            # Precio < 68%: Orden límite LIMIT SELL en 68%
            order1 = self.account.place_limit_order(
                symbol=self.symbol,
                side=OrderSide.SELL,
                price=level_68,  # LIMIT SELL al 68%
                margin=margin_c1,
                take_profit=tp_c1,  # TP desde shared_config
                stop_loss=sl_c1,     # SL desde shared_config
                estimated_commission=comm_c1,
                strategy_case=1,
                fib_high=self.current_swing.high.price,
                fib_low=self.current_swing.low.price,
                entry_fib_level=0.68
            )
            
            if order1:
                order2 = self.account.place_limit_order(
                    symbol=self.symbol,
                    side=OrderSide.SELL,
                    price=level_786,
                    margin=margin_c1, # Usamos el mismo margen para simplificar
                    take_profit=tp_c1,   # TP desde shared_config
                    stop_loss=sl_c1,     # SL desde shared_config
                    estimated_commission=comm_c1,
                    strategy_case=1,
                    fib_high=self.current_swing.high.price,
                    fib_low=self.current_swing.low.price,
                    entry_fib_level=0.786
                    # linked_order_id eliminado - ya no se usan órdenes vinculadas
                )
            
            self.last_case_executed = 1
        
        # Caso 2 eliminado - ya no existe
        
        elif case == 3:
            if not allowed_c3:
                return

            # Precio >= 78.6%: Mercado con TP/SL desde shared_config
            self.account.place_market_order(
                symbol=self.symbol,
                side=OrderSide.SELL,
                current_price=current_price,
                margin=margin_c3,
                take_profit=tp_c3,  # TP desde shared_config
                stop_loss=sl_c3,     # SL desde shared_config
                estimated_commission=comm_c3,
                strategy_case=3,
                fib_high=self.current_swing.high.price,
                fib_low=self.current_swing.low.price,
                entry_fib_level=(current_price - fib_low) / fib_range
            )
            
            self.last_case_executed = 3
    
    def on_price_update(self, price: float):
        """Callback cuando se actualiza el precio"""
        self.current_price = price
        
        # Verificar órdenes pendientes y posiciones
        self.account.check_pending_orders(self.symbol, price)
        self.account.check_positions(self.symbol, price)
        
        # Ejecutar lógica de trading
        self.execute_trading_logic(price)
    
    def on_candle_close(self, candle: dict):
        """Callback cuando cierra una vela"""
        # Agregar nueva vela
        self.candle_data.append(candle)
        
        # Mantener límite de velas
        if len(self.candle_data) > CANDLE_LIMIT:
            self.candle_data.pop(0)
        
        # Re-analizar Fibonacci
        print(f"\n🕯️  Nueva vela cerrada: {self.symbol} @ ${candle['close']:.4f}")
        self.current_swing = self.analyze_fibonacci()
        
        if self.current_swing:
            print(f"   📐 Fibonacci válido: High ${self.current_swing.high.price:.4f} -> Low ${self.current_swing.low.price:.4f}")
            self.last_case_executed = 0  # Reset para permitir nuevas entradas
    
    async def connect_websocket(self):
        """Conectar al WebSocket de Binance Futures"""
        stream = f"{self.symbol.lower()}@kline_{self.timeframe}"
        url = f"{WS_BASE_URL}/{stream}"
        
        print(f"🔌 Conectando a WebSocket: {stream}")
        
        try:
            async with websockets.connect(url) as ws:
                self.ws_connection = ws
                print(f"✅ Conectado a {self.symbol} WebSocket")
                
                while self.running:
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=30)
                        data = json.loads(message)
                        
                        if "k" in data:
                            kline = data["k"]
                            current_price = float(kline["c"])
                            
                            # Actualizar precio
                            self.on_price_update(current_price)
                            
                            # Verificar si la vela cerró
                            if kline["x"]:  # x = isClosed
                                candle = {
                                    "time": int(kline["t"]) // 1000,
                                    "open": float(kline["o"]),
                                    "high": float(kline["h"]),
                                    "low": float(kline["l"]),
                                    "close": float(kline["c"]),
                                    "volume": float(kline["v"])
                                }
                                self.on_candle_close(candle)
                    
                    except asyncio.TimeoutError:
                        # Ping para mantener conexión
                        await ws.ping()
                    
        except Exception as e:
            print(f"❌ Error WebSocket: {e}")
            if self.running:
                print("🔄 Reconectando en 5 segundos...")
                await asyncio.sleep(5)
                await self.connect_websocket()
    
    async def start(self):
        """Iniciar el bot"""
        print("\n" + "="*60)
        print("🤖 BOT DE TRADING FIBONACCI - PAPER TRADING")
        print("="*60)
        print(f"📈 Par: {self.symbol}")
        print(f"⏱️  Timeframe: {self.timeframe}")
        print(f"💵 Balance inicial: ${INITIAL_BALANCE}")
        print(f"⚡ Apalancamiento: {LEVERAGE}x")
        print(f"💳 Margen por trade: ${MARGIN_PER_TRADE}")
        print("="*60 + "\n")
        
        self.running = True
        
        # Cargar datos históricos
        await self.fetch_historical_data()
        
        # Analizar Fibonacci inicial
        self.current_swing = self.analyze_fibonacci()
        
        if self.current_swing:
            print(f"\n📐 Fibonacci inicial encontrado:")
            print(f"   High: ${self.current_swing.high.price:.4f}")
            print(f"   Low: ${self.current_swing.low.price:.4f}")
            for name, level in self.current_swing.levels.items():
                print(f"   {name}: ${level:.4f}")
            
            # Ejecutar lógica de trading inmediatamente si hay Fibonacci válido
            # Obtener precio actual de la última vela
            if self.candle_data:
                current_price = self.candle_data[-1]["close"]
                print(f"\n💰 Precio actual: ${current_price:.4f}")
                self.execute_trading_logic(current_price)
        
        # Mostrar estado inicial
        self.account.print_status()
        self.account.print_open_trades()
        
        # Conectar WebSocket
        await self.connect_websocket()
    
    def stop(self):
        """Detener el bot"""
        print("\n🛑 Deteniendo bot...")
        self.running = False
        self.account.print_status()
    
    def get_status_for_web(self) -> dict:
        """Obtener estado para la interfaz web"""
        return {
            "symbol": self.symbol,
            "current_price": self.current_price,
            "account": self.account.get_status(),
            "open_trades": self.account.get_open_trades_for_web(),
            "fibonacci": {
                "high": self.current_swing.high.price if self.current_swing else None,
                "low": self.current_swing.low.price if self.current_swing else None,
                "levels": self.current_swing.levels if self.current_swing else {}
            } if self.current_swing else None
        }


# ===== Servidor HTTP Integrado =====
import http.server
import socketserver
import threading
import os

def start_http_server(port=8000):
    """Iniciar servidor HTTP en un hilo separado"""
    handler = http.server.SimpleHTTPRequestHandler
    
    # Cambiar al directorio del bot
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    try:
        with socketserver.TCPServer(("", port), handler) as httpd:
            print(f"\n🌐 Servidor Web iniciado: http://localhost:{port}")
            print(f"   Abre esta URL en tu navegador para ver las operaciones")
            httpd.serve_forever()
    except OSError as e:
        if "Address already in use" in str(e) or "10048" in str(e):
            print(f"⚠️ Puerto {port} ocupado, intentando {port + 1}...")
            start_http_server(port + 1)
        else:
            print(f"❌ Error iniciando servidor HTTP: {e}")


def show_startup_menu(account):
    """Mostrar menú de inicio (síncrono)"""
    print(f"\n{'='*60}")
    print(f"🚀 BOT DE TRADING FIBONACCI")
    print(f"{'='*60}")
    
    # === SIEMPRE RESETEAR AL INICIAR ===
    import os
    
    # Eliminar el archivo trades.json si existe
    if os.path.exists(TRADES_FILE):
        os.remove(TRADES_FILE)
        print(f"   🗑️  Archivo {TRADES_FILE} eliminado")
    
    # Reiniciar cuenta en memoria
    account.open_positions.clear()
    account.pending_orders.clear()
    account.balance = INITIAL_BALANCE
    account.trade_history = []
    account._save_trades()  # Crear archivo nuevo vacío
    
    print(f"\n📊 Estado (Paper Trading):")
    print(f"   Balance: ${account.balance:.2f}")
    print(f"   Margen disponible: ${account.get_available_margin():.2f}")
    print(f"\n✅ Cuenta reseteada. Iniciando desde 0")


async def main():
    """Función principal del Bot de Trading Fibonacci"""
    from scanner import MarketScanner, run_priority_scan
    from config import SCAN_INTERVAL, MARGIN_PER_TRADE
    
    logger.info("=" * 60)
    logger.info("🚀 INICIANDO BOT DE TRADING FIBONACCI")
    logger.info("=" * 60)
    
    # Crear cuenta según modo de trading
    if TRADING_MODE == "real":
        # Modo REAL: Usar Bybit API
        if not BYBIT_API_KEY or not BYBIT_API_SECRET:
            logger.error("❌ BYBIT_API_KEY y BYBIT_API_SECRET no configurados en .env")
            print("❌ ERROR: Configura BYBIT_API_KEY y BYBIT_API_SECRET en tu archivo .env")
            return
        
        from real_trading import RealTradingAccount
        account = RealTradingAccount(
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET,
            testnet=False,  # Use demo mode, not testnet
            demo=True,      # Bybit Demo Trading (api-demo.bybit.com)
            leverage=LEVERAGE,
            trades_file="trades_real.json"
        )
        mode_text = "DEMO" if True else ("TESTNET" if BYBIT_TESTNET else "MAINNET")
        logger.info(f"🔴 MODO REAL ACTIVADO - Bybit {mode_text}")
        print(f"\n⚠️  MODO REAL ACTIVADO - Bybit {mode_text}")
        print(f"💰 Balance: ${account.balance:.2f}")
    else:
        # Modo PAPER (default)
        account = PaperTradingAccount(
            initial_balance=INITIAL_BALANCE,
            leverage=LEVERAGE,
            trades_file=TRADES_FILE
        )
        logger.info("📝 MODO PAPER TRADING ACTIVADO")
    
    # Inicializar calculadora de métricas
    initial_balance = account.balance if TRADING_MODE == "real" else INITIAL_BALANCE
    performance_calculator.initial_balance = initial_balance
    
    # Mostrar menú de inicio (antes del loop async) - solo para paper trading
    if TRADING_MODE != "real":
        show_startup_menu(account)
    
    # Iniciar servidor HTTP en hilo separado (puerto 8000 - archivos generales)
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()
    
    # Iniciar servidor del Analizador (puerto 8080 - con CORS para ngrok)
    analyzer_thread = threading.Thread(target=start_web_server, daemon=True)
    analyzer_thread.start()
    
    await asyncio.sleep(0.5)
    
    # Configuración: Pares específicos desde shared_config.json
    try:
        with open('shared_config.json', 'r') as f:
            shared_cfg = json.load(f)
            target_pairs = shared_cfg.get('scanner', {}).get('target_pairs', [])
            limit = shared_cfg.get('scanner', {}).get('top_pairs_limit', TOP_PAIRS_LIMIT)
    except Exception as e:
        logger.warning(f"Error leyendo shared_config.json: {e}")
        target_pairs = []
        limit = TOP_PAIRS_LIMIT
    
    # Crear scanner
    scanner = MarketScanner(top_n=limit)
    
    # Identificar pares activos (Posiciones + Pendientes) para asegurarnos de escanearlos
    active_pairs = set()
    
    # Modo PAPER: Usar posiciones y órdenes del paper trading
    if account.open_positions:
        active_pairs.update(pos.symbol for pos in account.open_positions.values())
    if account.pending_orders:
        active_pairs.update(
            (order.get('symbol') if isinstance(order, dict) else order.symbol)
            for order in account.pending_orders.values()
        )
        
    # Guardar pares activos para añadirlos después del fetch (no reemplazar el escaneo completo)
    scanner.active_pairs_to_include = set(active_pairs) if active_pairs else set()
    
    if active_pairs:
        logger.info(f"Pares activos a incluir en escaneo: {', '.join(active_pairs)}")

    if target_pairs:
        # Solo si hay pares específicos definidos manualmente, usar cache
        scanner.pairs_cache = list(set(target_pairs) | active_pairs)
        logger.info(f"Pares objetivo (manual): {', '.join(scanner.pairs_cache)}")
    else:
        # Modo: Escanear TODOS los pares (no setear pairs_cache)
        scanner.pairs_cache = None  # Forzar fetch de todos los pares
        print(f"\n📊 Escaneando TODOS los pares disponibles (filtro RSI >= {RSI_THRESHOLD})")
    
    print(f"🎯 Casos: 4 > 3 > 1 | Niveles Fibonacci desde config")  # Caso 2 eliminado"
    print(f"💰 Balance Paper: ${account.balance:.2f} | Margen/orden: ${MARGIN_PER_TRADE}")
    print(f"⏱️  Primer escaneo: {FIRST_SCAN_DELAY}s | Siguientes: {SCAN_INTERVAL}s")
    print(f"\n🌐 Servidor Web: http://localhost:8000")
    
    # Caché de precios compartido (actualizado por WebSocket)
    price_cache = {}
    
    # Conectar price_cache a la cuenta para que pueda limpiarlo al cerrar posiciones
    account.price_cache = price_cache
    
    async def price_websocket_handler():
        """WebSocket para precios en tiempo real (Dinámico)"""
        nonlocal price_cache
        current_symbols_set = set()

        while True:
            try:
                # Determinar qué símbolos necesitamos monitorear (Posiciones + Órdenes Pendientes)
                needed_symbols = set(pos.symbol.lower() for pos in account.open_positions.values())
                if account.pending_orders:
                    needed_symbols.update(
                        (order.get('symbol').lower() if isinstance(order, dict) else order.symbol.lower())
                        for order in account.pending_orders.values()
                    )
                
                # Si no hay posiciones, dormir y reintentar luego
                if not needed_symbols:
                    current_symbols_set = set()
                    await asyncio.sleep(2)
                    continue

                # Si los símbolos cambiaron, reconectar
                if needed_symbols != current_symbols_set:
                    current_symbols_set = needed_symbols
                    logger.info(f"🔄 Iniciando conexión WebSocket para {len(current_symbols_set)} pares: {current_symbols_set}")
                    print(f"🔌 Conectando WS para: {', '.join(current_symbols_set)}")
                    
                    # Bybit WebSocket - formato: tickers.BTCUSDT
                    args = [f"tickers.{s.upper()}" for s in needed_symbols]
                    ws_url = "wss://stream.bybit.com/v5/public/linear"
                    
                    try:
                        async with websockets.connect(ws_url) as ws:
                            # Suscribirse a los tickers de Bybit
                            subscribe_msg = {
                                "op": "subscribe",
                                "args": args
                            }
                            await ws.send(json.dumps(subscribe_msg))
                            print(f"📡 Suscripción enviada. Esperando datos...")
                            
                            while True:
                                # Verificar si necesitamos cambiar streams
                                new_needed = set(pos.symbol.lower() for pos in account.open_positions.values())
                                if account.pending_orders:
                                    new_needed.update(
                                        (order.get('symbol').lower() if isinstance(order, dict) else order.symbol.lower())
                                        for order in account.pending_orders.values()
                                    )
                                # También incluir símbolos con monitoreo post-cierre activo
                                if new_needed != current_symbols_set:
                                    print(f"⚠️ Cambio en pares activos detectado. Reconectando...")
                                    break # Salir para reconectar
                                
                                try:
                                    msg = await asyncio.wait_for(ws.recv(), timeout=5)
                                    data = json.loads(msg)
                                    
                                    # Ignorar mensajes de confirmación de suscripción
                                    if "op" in data and data["op"] == "subscribe":
                                        continue
                                        
                                    # Bybit ticker format: {"topic":"tickers.BTCUSDT","data":{"symbol":"BTCUSDT","lastPrice":"..."}}
                                    if 'data' in data and 'symbol' in data.get('data', {}):
                                        symbol = data['data']['symbol']
                                        price = float(data['data']['lastPrice'])
                                        price_cache[symbol] = price
                                        
                                        # Debug (solo 1 de cada 50 para no spamear, o si hay cambio significativo)
                                        # print(f"Processing {symbol}: {price}")
                                        
                                        # Actualizar y Verificar en tiempo real
                                        if account.open_positions:
                                            account.check_positions(symbol, price)
                                        if account.pending_orders:
                                            account.check_pending_orders(symbol, price)
                                            
                                except asyncio.TimeoutError:
                                    # Bybit ping
                                    await ws.send(json.dumps({"op": "ping"}))
                                    # print("Ping enviado")
                                    continue
                                except Exception as e:
                                    print(f"❌ Error leyendo WS: {e}")
                                    break # Reconectar
                    except Exception as e:
                         print(f"❌ Error conexión WebSocket: {e}")
                         await asyncio.sleep(2)
                else:
                    await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error fatal en loop WS: {e}")
                await asyncio.sleep(5)
    
    def clear_screen():
        """Limpiar pantalla"""
        import os
        os.system('cls' if os.name == 'nt' else 'clear')
    
    def print_monitor():
        """Imprimir modo monitor con secciones separadas"""
        clear_screen()
        now = datetime.now().strftime('%H:%M:%S')
        
        # ===== HEADER =====
        print(f"{'═'*70}")
        print(f"  🤖 FIBONACCI TRADING BOT - MODO MONITOR  │  {now}")
        print(f"{'═'*70}")
        
        # ===== SECCIÓN 1: ESTADO DE CUENTA =====
        status = account.get_status()
        pnl = status['total_unrealized_pnl']
        pnl_color = "🟢" if pnl >= 0 else "🔴"
        
        print(f"\n┌{'─'*68}┐")
        print(f"│ 💰 CUENTA                                                          │")
        print(f"├{'─'*68}┤")
        print(f"│  Balance:         ${status['balance']:>10.2f}                              │")
        print(f"│  PnL no realizado:{pnl_color} ${pnl:>10.4f}                             │")
        print(f"│  Balance Margen:  ${status['margin_balance']:>10.2f}                              │")
        print(f"│  Margen disponible: ${status['available_margin']:>8.2f}                               │")
        print(f"└{'─'*68}┘")
        
        # ===== SECCIÓN 2: OPERACIONES ABIERTAS =====
        print(f"\n┌{'─'*68}┐")
        print(f"│ 📊 OPERACIONES ABIERTAS ({status['open_positions']} posiciones, {status['pending_orders']} órdenes)       │")
        print(f"├{'─'*68}┤")
        
        if account.open_positions:
            for order_id, pos in account.open_positions.items():
                pnl_emoji = "🟢" if pos.unrealized_pnl >= 0 else "🔴"
                current = price_cache.get(pos.symbol, pos.current_price)
                print(f"│  {pos.symbol:12} │ {pos.side.value:5} │ Entry: ${pos.entry_price:.4f} │ Current: ${current:.4f} │")
                print(f"│               │ TP: ${pos.take_profit:.4f}    │ {pnl_emoji} PnL: ${pos.unrealized_pnl:>8.4f}       │")
        else:
            print(f"│  Sin posiciones abiertas                                          │")
        
        if account.pending_orders:
            print(f"├{'─'*68}┤")
            print(f"│  📋 ÓRDENES LÍMITE                                                 │")
            for order_id, order in account.pending_orders.items():
                print(f"│  {order.symbol:12} │ {order.side.value:5} │ Precio: ${order.price:.4f} │ TP: ${order.take_profit:.4f}     │")
        
        print(f"└{'─'*68}┘")
        
        # ===== SECCIÓN 3: ÚLTIMO ESCANEO =====
        num_pairs_display = len(scanner.pairs_cache) if scanner.pairs_cache else TOP_PAIRS_LIMIT
        print(f"\n┌{'─'*68}┐")
        print(f"│ 🔍 ESCANEO: {num_pairs_display} pares{' '*(39 - len(str(num_pairs_display)))}│")
        print(f"├{'─'*68}┤")
    
    # Iniciar WebSocket de precios en paralelo
    asyncio.create_task(price_websocket_handler())
    
    # Variables para control de tiempo
    last_scan_time = 0
    scan_in_progress = False
    last_scan_result = "Esperando primer escaneo..."
    
    
    def print_monitor_realtime(countdown):
        """Imprimir modo monitor con actualización en tiempo real y colores"""
        # Colores ANSI
        C_RESET = "\033[0m"
        C_GREEN = "\033[92m"
        C_RED = "\033[91m"
        C_YELLOW = "\033[93m"
        C_CYAN = "\033[96m"
        C_BLUE = "\033[94m"
        C_MAGENTA = "\033[95m"
        C_WHITE = "\033[97m"

        clear_screen()
        now = datetime.now().strftime('%H:%M:%S')
        
        # Indicador de modo
        if TRADING_MODE == "real":
            mode_indicator = f"{C_RED}🔴 REAL TRADING{C_RESET}"
        else:
            mode_indicator = f"{C_GREEN}📝 PAPER TRADING{C_RESET}"
        
        # ===== HEADER =====
        print(f"{C_BLUE}{'═'*74}{C_RESET}")
        print(f"  {C_CYAN}🤖 FIBONACCI TRADING BOT{C_RESET}  │  {mode_indicator}  │  {C_WHITE}{now}{C_RESET}")
        print(f"{C_BLUE}{'═'*74}{C_RESET}")
        
        # ===== SECCIÓN 1: ESTADO DE CUENTA =====
        status = account.get_status()
        
        pnl = status['total_unrealized_pnl']
        pnl_color = C_GREEN if pnl >= 0 else C_RED
        
        print(f"\n{C_MAGENTA}┌{'─'*72}┐{C_RESET}")
        print(f"{C_MAGENTA}│ 💰 CUENTA{C_RESET}{' '*61}{C_MAGENTA}│{C_RESET}")
        print(f"{C_MAGENTA}├{'─'*72}┤{C_RESET}")
        print(f"{C_MAGENTA}│{C_RESET}  Balance:          {C_WHITE}${status['balance']:>10.2f}{C_RESET}                                      {C_MAGENTA}│{C_RESET}")
        print(f"{C_MAGENTA}│{C_RESET}  PnL no realizado: {pnl_color}${pnl:>10.4f}{C_RESET}                                      {C_MAGENTA}│{C_RESET}")
        print(f"{C_MAGENTA}│{C_RESET}  Balance Margen:   {C_WHITE}${status['margin_balance']:>10.2f}{C_RESET}                                      {C_MAGENTA}│{C_RESET}")
        print(f"{C_MAGENTA}│{C_RESET}  Margen disponible:{C_WHITE}${status['available_margin']:>10.2f}{C_RESET}                                      {C_MAGENTA}│{C_RESET}")
        print(f"{C_MAGENTA}└{'─'*72}┘{C_RESET}")
        
        # ===== SECCIÓN 3: OPERACIONES ABIERTAS =====
        print(f"\n{C_CYAN}┌{'─'*72}┐{C_RESET}")
        print(f"{C_CYAN}│ 📊 OPERACIONES ABIERTAS ({status['open_positions']} pos, {status['pending_orders']} ord){C_RESET}{' '*(40 - len(str(status['open_positions'])) - len(str(status['pending_orders'])))}{C_CYAN}│{C_RESET}")
        print(f"{C_CYAN}├{'─'*72}┤{C_RESET}")
        
        # Posiciones paper trading
        if account.open_positions:
            for order_id, pos in account.open_positions.items():
                current = price_cache.get(pos.symbol, getattr(pos, 'current_price', pos.entry_price))
                # Calcular PnL en tiempo real con el precio actual
                if current and current > 0:
                    calculated_pnl = pos.calculate_pnl(current)
                else:
                    calculated_pnl = pos.unrealized_pnl
                pnl_color_pos = C_GREEN if calculated_pnl >= 0 else C_RED
                side_color = C_RED if pos.side.value == 'SHORT' else C_GREEN
                case_str = f"C{pos.strategy_case}" if pos.strategy_case else "??"
                
                # Línea 1: Symbol, Case, Side, Qty, Current/Price
                print(f"{C_CYAN}│{C_RESET}  {C_WHITE}{pos.symbol:<10}{C_RESET} {C_YELLOW}({case_str}){C_RESET} │ {side_color}{pos.side.value:<5}{C_RESET} │ Qty: {C_WHITE}{pos.quantity:.3f}{C_RESET} │ Margin: {C_WHITE}${pos.margin:.2f}{C_RESET}{' '*8}{C_CYAN}│{C_RESET}")
                # Línea 2: Entry, Now, TP, PnL
                print(f"{C_CYAN}│{C_RESET}      Entry: {C_WHITE}${pos.entry_price:.4f}{C_RESET} │ Now: {C_WHITE}${current:.4f}{C_RESET} │ {pnl_color_pos}PnL: ${calculated_pnl:>.4f}{C_RESET}{' '*8}{C_CYAN}│{C_RESET}")
                
                if pos != list(account.open_positions.values())[-1]:
                    print(f"{C_CYAN}│{C_RESET}  {'-'*68}  {C_CYAN}│{C_RESET}")
        else:
            print(f"{C_CYAN}│{C_RESET}  {C_WHITE}Sin posiciones abiertas{C_RESET}{' '*45}{C_CYAN}│{C_RESET}")
            
        # Órdenes Pendientes
        if account.pending_orders:
            print(f"{C_CYAN}├{'─'*72}┤{C_RESET}")
            print(f"{C_CYAN}│ 📋 ÓRDENES LÍMITE{C_RESET}{' '*53}{C_CYAN}│{C_RESET}")
            print(f"{C_CYAN}├{'─'*72}┤{C_RESET}")
            for order_id, order in account.pending_orders.items():
                # Extract attributes safely for both Dict (Real) and Object (Paper)
                if isinstance(order, dict):
                    o_side = order.get('side', 'Sell')
                    o_symbol = order.get('symbol', 'Unknown')
                    o_case = order.get('strategy_case', 0)
                    o_qty = float(order.get('quantity', 0))
                    o_margin = float(order.get('margin', 0))
                    o_price = float(order.get('price', 0))
                    o_tp = float(order.get('take_profit', 0))
                else:
                    o_side = order.side.value if hasattr(order.side, 'value') else str(order.side)
                    o_symbol = order.symbol
                    o_case = order.strategy_case
                    o_qty = order.quantity
                    o_margin = order.margin
                    o_price = order.price
                    o_tp = order.take_profit

                side_color = C_RED if str(o_side).upper() == 'SELL' else C_GREEN
                case_str = f"C{o_case}" if o_case else "??"
                
                # Línea 1
                print(f"{C_CYAN}│{C_RESET}  {C_WHITE}{o_symbol:<10}{C_RESET} {C_YELLOW}({case_str}){C_RESET} │ {side_color}LIMIT {o_side}{C_RESET} │ Qty: {C_WHITE}{o_qty:.2f}{C_RESET} │ Margin: {C_WHITE}${o_margin:.2f}{C_RESET}{' '*4}{C_CYAN}│{C_RESET}")
                # Línea 2
                print(f"{C_CYAN}│{C_RESET}      Price: {C_WHITE}${o_price:.4f}{C_RESET} │ TP: {C_WHITE}${o_tp:.4f}{C_RESET}{' '*30}{C_CYAN}│{C_RESET}")
                
                if order != list(account.pending_orders.values())[-1]:
                     print(f"{C_CYAN}│{' '*72}│{C_RESET}")

        print(f"{C_CYAN}└{'─'*72}┘{C_RESET}")
        
        # ===== SECCIÓN 4: ESCANEO =====
        print(f"\n{C_YELLOW}┌{'─'*72}┐{C_RESET}")
        num_pairs = len(scanner.pairs_cache) if scanner.pairs_cache else TOP_PAIRS_LIMIT
        print(f"{C_YELLOW}│ 🔍 ESCANEO: {num_pairs} pares{C_RESET}{' '*50}{C_YELLOW}│{C_RESET}")
        print(f"{C_YELLOW}├{'─'*72}┤{C_RESET}")
        
        # Truncar resultado si es muy largo
        res_text = last_scan_result[:68]
        print(f"{C_YELLOW}│{C_RESET}  {C_WHITE}{res_text:<68}{C_RESET}  {C_YELLOW}│{C_RESET}")
        print(f"{C_YELLOW}│{C_RESET}  ⏳ Próximo escaneo en: {C_WHITE}{countdown:>3}{C_RESET} segundos{' '*37}{C_YELLOW}│{C_RESET}")
        print(f"{C_YELLOW}└{'─'*72}┘{C_RESET}")
    
    try:
        import time
        scan_countdown = FIRST_SCAN_DELAY  # Primer escaneo según config
        equity_timer = 0  # Temporizador para registro de balance (cada 60s)
        
        # --- WATCHDOG INICIAL: Actualizar precios por REST al arrancar ---
        logger.info("Sincronizando precios actuales vía API REST...")
        await scanner.update_prices_for_positions(account, price_cache)
        
        # Registrar punto inicial
        account.record_equity_point(price_cache)

        # --- Iniciar Bot de Telegram en paralelo (SOLO SI ESTÁ ACTIVADO) ---
        from config import TELEGRAM_TOKEN
        # Variable de entorno para desactivar telegram en multibot (por defecto True)
        enable_telegram = os.getenv("ENABLE_TELEGRAM", "true").lower() == "true"
        
        if TELEGRAM_TOKEN and enable_telegram:
            logger.info(f"Token de Telegram configurado: {TELEGRAM_TOKEN[:10]}...")
            telegram_bot.account = account
            telegram_bot.scanner = scanner
            telegram_bot.price_cache = price_cache
            telegram_bot.running = True
            
            # Controlar si este bot debe responder comandos/reportes o ser pasivo (solo alertas)
            # En modo Multi-Bot, el monitor central maneja los comandos.
            commands_enabled = os.getenv("TELEGRAM_COMMANDS_ENABLED", "true").lower() == "true"
            
            if commands_enabled:
                logger.info("Telegram: Comandos y Reportes automáticos ACTIVADOS")
                asyncio.create_task(telegram_bot.run_polling_loop())
                asyncio.create_task(telegram_bot.run_report_loop())
            else:
                logger.info("Telegram: Modo PASIVO (Solo Alertas - Comandos manejados por MultiBot)")
            logger.info("Bot de Telegram iniciado - Envía /start a @criismorabot")
            # Notificación inmediata si hay chats autorizados
            await telegram_bot.broadcast_message("🚀 <b>BOT INICIADO</b>\nEl sistema está en línea y operando.")
        else:
            if not enable_telegram:
                logger.info("🔕 Telegram desactivado por configuración (Modo Multi-Bot)")
            else:
                logger.warning("⚠️ TELEGRAM_TOKEN no configurado - Bot de Telegram deshabilitado")
        
        while True:
            # 1. Verificar TP/SL y Pending Orders en tiempo real (WebSocket Cache)
            # Obtener todos los símbolos activos (Posiciones + Órdenes Pendientes + Monitoreo Post-Cierre)
            active_symbols = set()
            if account.open_positions:
                active_symbols.update(pos.symbol for pos in account.open_positions.values())
            if account.pending_orders:
                active_symbols.update(
                    (order.get('symbol') if isinstance(order, dict) else order.symbol)
                    for order in account.pending_orders.values()
                )
            # También incluir símbolos con monitoreo post-cierre
            
            if active_symbols:
                for symbol in list(active_symbols):
                    price = price_cache.get(symbol)
                    
                    if price and price > 0:
                        # 1. Verificar Cierre de Posiciones (TP/SL)
                        if account.open_positions:
                            account.check_positions(symbol, price)
                        
                        # 2. Verificar Activación de Órdenes Pendientes (Limit)
                        if account.pending_orders:
                            account.check_pending_orders(symbol, price)
            
            # --- 1.1 CHECK GLOBAL EQUITY TAKE PROFIT ---
            # --- 1.1 CHECK GLOBAL EQUITY TAKE PROFIT ---
            try:
                # 1. Intentar leer desde variable de entorno (prioridad alta)
                env_gtp = os.getenv("GLOBAL_TAKE_PROFIT_USD")
                if env_gtp is not None:
                    global_tp_usd = float(env_gtp)
                else:
                    # 2. Cachear lectura de archivo para evitar bloqueos por I/O frecuente
                    current_time = time.time()
                    # Inicializar variables estáticas si no existen
                    if not hasattr(main, "last_config_check"):
                        main.last_config_check = 0
                        main.cached_global_tp = 0.0
                    
                    # Chequear archivo cada 2 segundos enviando I/O excesivo
                    if current_time - main.last_config_check > 2.0:
                        try:
                            with open('shared_config.json', 'r') as f:
                                sh_cfg = json.load(f)
                                main.cached_global_tp = sh_cfg.get('trading', {}).get('global_take_profit_usd', 0.0)
                            main.last_config_check = current_time
                        except Exception as e:
                            logger.error(f"Error leyendo shared_config.json: {e}")
                            print(f"⚠️ Error leyendo config: {e}")
                    
                    global_tp_usd = main.cached_global_tp

            except Exception as e:
                logger.error(f"Error general en Global TP check: {e}")
                global_tp_usd = 0.0

            if global_tp_usd > 0:
                # Calcular Equity actual usando precios en tiempo real
                current_equity = account.get_margin_balance(price_cache) # Balance + Unrealized PnL
                # Meta basada en Initial Balance para que sea relativa al inicio del ciclo
                target_equity = account.initial_balance + global_tp_usd
                
                # Solo actuar si hay posiciones u órdenes (evitar bucle infinito si ya se alcanzó la meta)
                if current_equity >= target_equity and (account.open_positions or account.pending_orders):
                    print_monitor_realtime(0)
                    msg = f"💰 META GLOBAL ALCANZADA: Equidad ${current_equity:.2f} >= Inicial ${account.initial_balance:.2f} + ${global_tp_usd}"
                    logger.info(msg)
                    print(f"\n{msg}")
                    
                    # Cerrar todo
                    account.close_all_positions(price_cache, reason="Global Take Profit")
                    account.cancel_all_orders(reason="Global Take Profit Cleanup")
                    
                    # Reiniciar ciclo: el nuevo balance inicial es el balance actual tras cerrar todo
                    account.initial_balance = account.balance
                    logger.info(f"🔄 Ciclo reiniciado. Nuevo balance inicial: ${account.initial_balance:.2f}")
                    
                    # Notificar Telegram
                    if TELEGRAM_TOKEN and enable_telegram:
                         asyncio.create_task(telegram_bot.broadcast_message(f"🚀 <b>GLOBAL TAKE PROFIT</b>\n{msg}\nTodas las operaciones cerradas. Nuevo ciclo iniciado."))
                    
                    print(f"⏳ Esperando 30 segundos para reiniciar ciclo...")
                    await asyncio.sleep(30)
                    
                    # Forzar reinicio de escaneo inmediato
                    scan_countdown = 0
                    last_scan_result = "Reiniciando tras Global TP..."

            # --- REGISTRO DE EQUITY (Cada 60s) ---
            equity_timer += 1
            if equity_timer >= 60:
                account.record_equity_point(price_cache)
                equity_timer = 0
            
            # --- WATCHDOG PERIÓDICO (Cada 10s) ---
            if scan_countdown % 10 == 0 and (account.open_positions or account.pending_orders):
                await scanner.update_prices_for_positions(account, price_cache)

            # 2. Verificar si es hora de escanear
            if scan_countdown <= 0:
                # Verificar margen ANTES de escanear
                available_margin = account.get_available_margin()
                if available_margin < MIN_AVAILABLE_MARGIN:
                    last_scan_result = f"⏸️ Escaneo pausado (margen ${available_margin:.2f} < ${MIN_AVAILABLE_MARGIN})"
                    scan_countdown = 10  # Reintentar en 10 segundos
                else:
                    last_scan_result = "🔄 Escaneando..."
                    print_monitor_realtime(0)
                    
                    # Ejecutar escaneo
                    await run_priority_scan(scanner, account, MARGIN_PER_TRADE)
                    
                    last_scan_result = f"✅ Completado {datetime.now().strftime('%H:%M:%S')}"
                    scan_countdown = SCAN_INTERVAL
            
            # Mostrar monitor actualizado
            print_monitor_realtime(scan_countdown)
            
            # Esperar 1 segundo y decrementar contador
            await asyncio.sleep(1)
            scan_countdown -= 1
            
    except KeyboardInterrupt:
        logger.info("Bot detenido por el usuario")
        telegram_bot.stop()
        
        # Mostrar métricas finales
        metrics = performance_calculator.calculate_all(account.trade_history, account.balance)
        print(performance_calculator.format_report(metrics))
        
        account.print_status()


if __name__ == "__main__":
    asyncio.run(main())

