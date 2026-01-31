# Bot de Trading Fibonacci - Configuración

import json
import os
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

# Cargar configuración desde shared_config.json
_config_path = os.path.join(os.path.dirname(__file__), "shared_config.json")
_shared_config = {}
if os.path.exists(_config_path):
    with open(_config_path, 'r') as f:
        _shared_config = json.load(f)

# Paper Trading (desde shared_config o defaults)
_trading = _shared_config.get("trading", {})
INITIAL_BALANCE = _trading.get("initial_balance", 30.0)
LEVERAGE = _trading.get("leverage", 10)
MARGIN_PER_TRADE = _trading.get("margin_per_trade", 3.0)
MAX_MARGIN_PER_TRADE = _trading.get("max_margin_per_trade", 10.0)
TARGET_PROFIT = _trading.get("target_profit", 1.0)
COMMISSION_RATE = _trading.get("commission_rate", 0.0006) # Comisión promedio (0.06%)
MIN_AVAILABLE_MARGIN = _trading.get("min_available_margin", 3.0)

# Trading Mode: "paper" or "real"
# Trading Mode: "paper" or "real"
# Prioridad: Variable de entorno > shared_config > default "paper"
env_mode = os.getenv("BOT_TRADING_MODE")
TRADING_MODE = env_mode if env_mode else _trading.get("mode", "paper")

# Bybit Configuration (from shared_config.json and .env)
_bybit = _trading.get("bybit", {})
BYBIT_DEMO = _bybit.get("demo", False)
BYBIT_INITIAL_BALANCE = _bybit.get("initial_balance", 100)
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")

# Dual API Keys Support
BYBIT_REAL_API_KEY = os.getenv("BYBIT_REAL_API_KEY", BYBIT_API_KEY)
BYBIT_REAL_API_SECRET = os.getenv("BYBIT_REAL_API_SECRET", BYBIT_API_SECRET)
BYBIT_DEMO_API_KEY = os.getenv("BYBIT_DEMO_API_KEY", "")
BYBIT_DEMO_API_SECRET = os.getenv("BYBIT_DEMO_API_SECRET", "")

# Scanner (desde shared_config o defaults)
_scanner = _shared_config.get("scanner", {})
TOP_PAIRS_LIMIT = _scanner.get("top_pairs_limit", 200)
RSI_THRESHOLD = _scanner.get("rsi_threshold", 0)
RSI_TIMEFRAME = _scanner.get("rsi_timeframe", "5m")
env_scan_interval = os.getenv("BOT_SCAN_INTERVAL")
SCAN_INTERVAL = int(env_scan_interval) if env_scan_interval else _scanner.get("scan_interval", 30)
FIRST_SCAN_DELAY = _scanner.get("first_scan_delay", 5)  # Primer escaneo en 5 segundos
CANDLE_LIMIT = _scanner.get("candle_limit", 1000)

# Par inicial (desde shared_config - OBLIGATORIO)
_target_pairs = _scanner.get("target_pairs", [])
DEFAULT_SYMBOL = _target_pairs[0] if _target_pairs else None

# Bybit Futures API
WS_BASE_URL = "wss://stream.bybit.com/v5/public/linear"
REST_BASE_URL = "https://api.bybit.com"

# Telegram Config (desde .env)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Intervalos soportados (desde shared_config o default)
# Intervalos soportados (desde shared_config o default)
env_timeframe = os.getenv("BOT_TIMEFRAME")
TIMEFRAME = env_timeframe if env_timeframe else _scanner.get("timeframe", "4h")

# Niveles Fibonacci (desde shared_config o defaults)
_fibonacci = _shared_config.get("fibonacci", {})
_fib_levels = _fibonacci.get("levels", {})
FIBONACCI_LEVELS = {k: float(v) for k, v in _fib_levels.items()} if _fib_levels else {
    "0": 0.0,
    "23.6": 0.236,
    "38.2": 0.382,
    "50": 0.5,
    "55": 0.55,
    "58": 0.58,
    "60": 0.60,
    "61.8": 0.618,
    "69": 0.69,
    "75": 0.75,
    "78.6": 0.786,
    "90": 0.90,
    "100": 1.0
}

# Configuración ZigZag por timeframe
ZIGZAG_CONFIGS = {
    "1m": {"deviation": 0.3, "depth": 5, "backstep": 2},
    "5m": {"deviation": 0.5, "depth": 5, "backstep": 2},
    "15m": {"deviation": 1, "depth": 5, "backstep": 2},
    "1h": {"deviation": 2, "depth": 8, "backstep": 3},
    "2h": {"deviation": 2.5, "depth": 9, "backstep": 3},
    "4h": {"deviation": 3, "depth": 10, "backstep": 3},
    "1d": {"deviation": 5, "depth": 10, "backstep": 3}
}

# Pares excluidos del escaneo
EXCLUDED_PAIRS = [
    "USDCUSDT",
    "TUSDUSDT", 
    "BUSDUSDT",
    "FDUSDUSDT",
    "USDPUSDT",
    "BTCDOMUSDT",
    "DAIUSDT",
    "EURUSDT",
    "GBPUSDT",
    "OMNIUSDT",
    "PONKEUSDT"
]

# Archivos
TRADES_FILE = os.getenv("BOT_TRADES_FILE", "trades.json")

# Colores para consola
C_RESET = "\033[0m"
C_RED = "\033[91m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_BLUE = "\033[94m"
C_MAGENTA = "\033[95m"
C_CYAN = "\033[96m"
C_WHITE = "\033[97m"
