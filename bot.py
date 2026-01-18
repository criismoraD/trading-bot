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
    INITIAL_BALANCE, LEVERAGE, MARGIN_PER_TRADE,
    DEFAULT_SYMBOL, WS_BASE_URL, REST_BASE_URL,
    TIMEFRAME, CANDLE_LIMIT, TRADES_FILE,
    TOP_PAIRS_LIMIT, RSI_THRESHOLD, FIRST_SCAN_DELAY, SCAN_INTERVAL
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
        
        case = determine_trading_case(current_price, self.current_swing)
        
        # Evitar ejecutar el mismo caso múltiples veces para el mismo swing
        if case == self.last_case_executed:
            return
        
        # Para Casos 2 y 3, verificar que estemos en zona de entrada (55%+)
        # Para Caso 1, siempre colocamos órdenes límite (se ejecutarán cuando el precio suba)
        if case in [2, 3] and not self.current_swing.current_candle_at_55:
            print(f"⏳ Precio en zona de Caso {case} pero esperando confirmación en 55%+")
            return
        
        levels = self.current_swing.levels
        tp_50 = levels["50"]
        level_618 = levels["61.8"]
        level_786 = levels["78.6"]
        
        print(f"\n🎯 CASO {case} detectado | Precio: ${current_price:.4f}")
        print(f"   Niveles: 50%=${tp_50:.4f} | 61.8%=${level_618:.4f} | 78.6%=${level_786:.4f}")
        
        if case == 1:
            # Precio < 61.8%: Órdenes límite en 61.8% y 78.6%
            order1 = self.account.place_limit_order(
                symbol=self.symbol,
                side=OrderSide.SELL,
                price=level_618,
                margin=MARGIN_PER_TRADE,
                take_profit=tp_50
            )
            
            if order1:
                order2 = self.account.place_limit_order(
                    symbol=self.symbol,
                    side=OrderSide.SELL,
                    price=level_786,
                    margin=MARGIN_PER_TRADE,
                    take_profit=tp_50,
                    linked_order_id=order1.id  # Vincular para cancelar si TP de order1 se ejecuta
                )
            
            self.last_case_executed = 1
        
        elif case == 2:
            # 61.8% <= Precio < 78.6%: Mercado + límite en 78.6%
            position = self.account.place_market_order(
                symbol=self.symbol,
                side=OrderSide.SELL,
                current_price=current_price,
                margin=MARGIN_PER_TRADE,
                take_profit=tp_50
            )
            
            if position:
                self.account.place_limit_order(
                    symbol=self.symbol,
                    side=OrderSide.SELL,
                    price=level_786,
                    margin=MARGIN_PER_TRADE,
                    take_profit=tp_50,
                    linked_order_id=position.order_id
                )
            
            self.last_case_executed = 2
        
        elif case == 3:
            # Precio >= 78.6%: Mercado con TP en 61.8%
            self.account.place_market_order(
                symbol=self.symbol,
                side=OrderSide.SELL,
                current_price=current_price,
                margin=MARGIN_PER_TRADE,
                take_profit=level_618
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
    
    open_count = len(account.open_positions) + len(account.pending_orders)
    
    print(f"\n📊 Estado actual (Paper Trading):")
    print(f"   Posiciones abiertas: {len(account.open_positions)}")
    print(f"   Órdenes pendientes: {len(account.pending_orders)}")
    print(f"   Balance: ${account.balance:.2f}")
    print(f"   Margen disponible: ${account.get_available_margin():.2f}")
    
    print(f"\n¿Qué deseas hacer?")
    print(f"   [1] Paper Trading - Empezar de cero (eliminar historial)")
    print(f"   [2] Paper Trading - Continuar con trades existentes")
    
    choice = input("\nOpción: ").strip()
    
    if choice == "1":
        # Eliminar el archivo trades.json físicamente
        import os
        if os.path.exists(TRADES_FILE):
            os.remove(TRADES_FILE)
            print(f"   🗑️  Archivo {TRADES_FILE} eliminado")
        
        # Reiniciar cuenta en memoria
        account.open_positions.clear()
        account.pending_orders.clear()
        account.balance = INITIAL_BALANCE
        account.trade_history = []
        account._save_trades()  # Crear archivo nuevo vacío
        print(f"\n✅ Trades eliminados. Balance reseteado a ${INITIAL_BALANCE}")
    
    print(f"\n✅ Continuando con Paper Trading")


async def main():
    """Función principal del Bot de Trading Fibonacci"""
    from scanner import MarketScanner, run_priority_scan
    from config import SCAN_INTERVAL, MARGIN_PER_TRADE
    
    logger.info("=" * 60)
    logger.info("🚀 INICIANDO BOT DE TRADING FIBONACCI")
    logger.info("=" * 60)
    
    # Crear cuenta paper trading
    account = PaperTradingAccount(
        initial_balance=INITIAL_BALANCE,
        leverage=LEVERAGE,
        trades_file=TRADES_FILE
    )
    
    # Inicializar calculadora de métricas
    performance_calculator.initial_balance = INITIAL_BALANCE
    
    # Mostrar menú de inicio (antes del loop async)
    show_startup_menu(account)
    
    # Iniciar servidor HTTP en hilo separado
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()
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
        active_pairs.update(order.symbol for order in account.pending_orders.values())
        
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
    
    print(f"🎯 Casos: 4 > 3 > 2 > 1 | Niveles Fibonacci desde config")
    print(f"💰 Balance Paper: ${account.balance:.2f} | Margen/orden: ${MARGIN_PER_TRADE}")
    print(f"⏱️  Primer escaneo: {FIRST_SCAN_DELAY}s | Siguientes: {SCAN_INTERVAL}s")
    print(f"\n🌐 Servidor Web: http://localhost:8000")
    
    # Caché de precios compartido (actualizado por WebSocket)
    price_cache = {}
    
    async def price_websocket_handler():
        """WebSocket para precios en tiempo real (Dinámico)"""
        nonlocal price_cache
        current_symbols_set = set()

        while True:
            try:
                # Determinar qué símbolos necesitamos monitorear (Posiciones + Órdenes Pendientes)
                needed_symbols = set(pos.symbol.lower() for pos in account.open_positions.values())
                if account.pending_orders:
                    needed_symbols.update(order.symbol.lower() for order in account.pending_orders.values())
                
                # Si no hay posiciones, dormir y reintentar luego
                if not needed_symbols:
                    current_symbols_set = set()
                    await asyncio.sleep(2)
                    continue

                # Si los símbolos cambiaron, reconectar
                if needed_symbols != current_symbols_set:
                    # print(f"🔄 Actualizando streams de precios: {needed_symbols}")
                    current_symbols_set = needed_symbols
                    
                    # Bybit WebSocket - formato: tickers.BTCUSDT
                    args = [f"tickers.{s.upper()}" for s in needed_symbols]
                    ws_url = "wss://stream.bybit.com/v5/public/linear"
                    
                    async with websockets.connect(ws_url) as ws:
                        # Suscribirse a los tickers de Bybit
                        subscribe_msg = {
                            "op": "subscribe",
                            "args": args
                        }
                        await ws.send(json.dumps(subscribe_msg))
                        
                        while True:
                            # Verificar si necesitamos cambiar streams
                            new_needed = set(pos.symbol.lower() for pos in account.open_positions.values())
                            if account.pending_orders:
                                new_needed.update(order.symbol.lower() for order in account.pending_orders.values())
                            if new_needed != current_symbols_set:
                                break # Salir para reconectar
                            
                            try:
                                msg = await asyncio.wait_for(ws.recv(), timeout=5)
                                data = json.loads(msg)
                                # Bybit ticker format: {"topic":"tickers.BTCUSDT","data":{"symbol":"BTCUSDT","lastPrice":"..."}}
                                if 'data' in data and 'symbol' in data.get('data', {}):
                                    symbol = data['data']['symbol']
                                    price = float(data['data']['lastPrice'])
                                    price_cache[symbol] = price
                                    
                                    # Actualizar y Verificar en tiempo real
                                    if account.open_positions:
                                        account.check_positions(symbol, price)
                                    if account.pending_orders:
                                        account.check_pending_orders(symbol, price)
                                        
                            except asyncio.TimeoutError:
                                # Bybit ping
                                await ws.send(json.dumps({"op": "ping"}))
                                continue
                            except Exception:
                                break # Reconectar
                else:
                    await asyncio.sleep(1)

            except Exception:
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
                pnl_color_pos = C_GREEN if pos.unrealized_pnl >= 0 else C_RED
                current = price_cache.get(pos.symbol, pos.current_price)
                side_color = C_RED if pos.side.value == 'SHORT' else C_GREEN
                case_str = f"C{pos.strategy_case}" if pos.strategy_case else "??"
                
                # Línea 1: Symbol, Case, Side, Qty
                print(f"{C_CYAN}│{C_RESET}  {C_WHITE}{pos.symbol:<10}{C_RESET} {C_YELLOW}({case_str}){C_RESET} │ {side_color}{pos.side.value:<5}{C_RESET} │ Qty: {C_WHITE}{pos.quantity:.3f}{C_RESET}{' '*25}{C_CYAN}│{C_RESET}")
                # Línea 2: Entry, Now, TP, PnL
                print(f"{C_CYAN}│{C_RESET}      Entry: {C_WHITE}${pos.entry_price:.4f}{C_RESET} │ Now: {C_WHITE}${current:.4f}{C_RESET} │ {pnl_color_pos}PnL: ${pos.unrealized_pnl:>.4f}{C_RESET}{' '*8}{C_CYAN}│{C_RESET}")
                
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
                side_color = C_RED if order.side.value == 'SELL' else C_GREEN
                case_str = f"C{order.strategy_case}" if order.strategy_case else "??"
                
                # Línea 1
                print(f"{C_CYAN}│{C_RESET}  {C_WHITE}{order.symbol:<10}{C_RESET} {C_YELLOW}({case_str}){C_RESET} │ {side_color}LIMIT {order.side.value}{C_RESET} │ Qty: {C_WHITE}{order.quantity:.2f}{C_RESET}{' '*21}{C_CYAN}│{C_RESET}")
                # Línea 2
                print(f"{C_CYAN}│{C_RESET}      Price: {C_WHITE}${order.price:.4f}{C_RESET} │ TP: {C_WHITE}${order.take_profit:.4f}{C_RESET}{' '*30}{C_CYAN}│{C_RESET}")
                
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
        
        # --- WATCHDOG INICIAL: Actualizar precios por REST al arrancar ---
        logger.info("Sincronizando precios actuales vía API REST...")
        await scanner.update_prices_for_positions(account, price_cache)
        
        # --- Iniciar Bot de Telegram en paralelo ---
        telegram_bot.account = account
        telegram_bot.scanner = scanner
        telegram_bot.price_cache = price_cache
        telegram_bot.running = True
        asyncio.create_task(telegram_bot.run_polling_loop())
        asyncio.create_task(telegram_bot.run_report_loop())
        logger.info("Bot de Telegram iniciado - Envía /start a @criismorabot")
        # Notificación inmediata si hay chats autorizados
        await telegram_bot.broadcast_message("🚀 <b>BOT INICIADO</b>\nEl sistema está en línea y operando.")
        
        while True:
            # 1. Verificar TP/SL y Pending Orders en tiempo real (WebSocket Cache)
            # Obtener todos los símbolos activos (Posiciones + Órdenes Pendientes)
            active_symbols = set()
            if account.open_positions:
                active_symbols.update(pos.symbol for pos in account.open_positions.values())
            if account.pending_orders:
                active_symbols.update(order.symbol for order in account.pending_orders.values())
            
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
            
            # --- WATCHDOG PERIÓDICO (Cada 10s) ---
            if scan_countdown % 10 == 0 and (account.open_positions or account.pending_orders):
                await scanner.update_prices_for_positions(account, price_cache)

            # 2. Verificar si es hora de escanear
            if scan_countdown <= 0:
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

