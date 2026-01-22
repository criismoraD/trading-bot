"""
Sistema de Logging Simplificado para el Bot de Trading
- Un archivo de log por cada ejecución del bot
- Formato: bot_YYYYMMDD_HHMMSS.log
- Solo información relevante: Scanner, Trades, Errores
"""
import logging
import os
import glob
from datetime import datetime

# Directorio de logs
LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# Timestamp de inicio de esta sesión
SESSION_START = datetime.now().strftime("%Y%m%d_%H%M%S")

# Archivo de log para esta sesión
SESSION_LOG_FILE = os.path.join(LOGS_DIR, f"bot_{SESSION_START}.log")

# Formato de logs
LOG_FORMAT = "%(asctime)s | %(levelname)-5s | %(message)s"
DATE_FORMAT = "%H:%M:%S"

# Logger principal (único para toda la sesión)
_main_logger = None


def cleanup_old_logs(keep_last: int = 10):
    """
    Eliminar logs antiguos, mantener solo los últimos N archivos.
    Se ejecuta al iniciar el bot.
    """
    try:
        log_files = glob.glob(os.path.join(LOGS_DIR, "bot_*.log"))
        log_files.sort(key=os.path.getmtime, reverse=True)
        
        # Eliminar archivos antiguos (mantener los últimos 'keep_last')
        for old_file in log_files[keep_last:]:
            try:
                os.remove(old_file)
            except:
                pass
                
        # También eliminar logs del sistema anterior
        old_logs = ['bot.log', 'scanner.log', 'trading.log', 'telegram.log', 
                    'database.log', 'trades.log']
        for old_log in old_logs:
            old_path = os.path.join(LOGS_DIR, old_log)
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except:
                    pass
    except Exception as e:
        print(f"Error cleaning logs: {e}")


def get_logger() -> logging.Logger:
    """
    Obtener el logger principal de la sesión.
    Se crea una sola vez por ejecución del bot.
    """
    global _main_logger
    
    if _main_logger is not None:
        return _main_logger
    
    _main_logger = logging.getLogger('bot_session')
    _main_logger.setLevel(logging.DEBUG)
    
    # Limpiar handlers existentes
    _main_logger.handlers.clear()
    
    # Handler para archivo (solo esta sesión)
    file_handler = logging.FileHandler(
        SESSION_LOG_FILE,
        mode='w',  # Sobrescribir (nuevo archivo por sesión)
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    
    _main_logger.addHandler(file_handler)
    
    # Header del log
    _main_logger.info("=" * 60)
    _main_logger.info(f"BOT SESSION STARTED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    _main_logger.info("=" * 60)
    
    return _main_logger


# ===== FUNCIONES DE LOGGING ESPECÍFICAS =====

def log_info(message: str):
    """Log de información general"""
    get_logger().info(message)


def log_debug(message: str):
    """Log de debug (solo va al archivo)"""
    get_logger().debug(message)


def log_warning(message: str):
    """Log de advertencia"""
    get_logger().warning(message)


def log_error(message: str):
    """Log de error"""
    get_logger().error(message)


def log_trade_open(symbol: str, side: str, price: float, case: int, margin: float):
    """Log cuando se abre un trade"""
    get_logger().info(
        f"📈 OPEN | {symbol} | {side} | ${price:.5f} | Case {case} | Margin ${margin:.2f}"
    )


def log_trade_close(symbol: str, side: str, entry: float, close: float, 
                    pnl: float, reason: str):
    """Log cuando se cierra un trade"""
    emoji = "✅" if pnl >= 0 else "❌"
    get_logger().info(
        f"{emoji} CLOSE | {symbol} | {side} | ${entry:.5f} → ${close:.5f} | "
        f"PnL: ${pnl:.4f} | {reason}"
    )


def log_scan_start(total_pairs: int):
    """Log al iniciar un escaneo"""
    get_logger().info(f"🔍 SCAN START | {total_pairs} pairs")


def log_scan_signal(symbol: str, case: int, price: float, rsi: float):
    """Log cuando se encuentra una señal"""
    get_logger().info(
        f"📊 SIGNAL | {symbol} | Case {case} | ${price:.5f} | RSI: {rsi:.1f}"
    )


def log_scan_complete(orders_placed: int, duration: float):
    """Log al completar un escaneo"""
    get_logger().info(
        f"✅ SCAN COMPLETE | {orders_placed} orders | {duration:.1f}s"
    )


def log_order_placed(symbol: str, order_type: str, side: str, 
                     price: float, case: int):
    """Log cuando se coloca una orden"""
    get_logger().info(
        f"📝 ORDER | {symbol} | {order_type} {side} | ${price:.5f} | Case {case}"
    )


def log_order_filled(symbol: str, fill_price: float, case: int):
    """Log cuando una orden se ejecuta"""
    get_logger().info(
        f"🎯 FILLED | {symbol} | ${fill_price:.5f} | Case {case}"
    )


def log_balance(available: float, total: float, pnl: float):
    """Log del balance actual"""
    get_logger().info(
        f"💰 BALANCE | Available: ${available:.2f} | Total: ${total:.2f} | PnL: ${pnl:+.2f}"
    )


# ===== COMPATIBILIDAD CON CÓDIGO EXISTENTE =====

def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Compatibilidad: retorna el logger principal"""
    return get_logger()


# Loggers pre-configurados (todos apuntan al mismo logger)
bot_logger = None
scanner_logger = None
trading_logger = None
telegram_logger = None
db_logger = None


def _init_loggers():
    """Inicializar los loggers de compatibilidad"""
    global bot_logger, scanner_logger, trading_logger, telegram_logger, db_logger
    logger = get_logger()
    bot_logger = logger
    scanner_logger = logger
    trading_logger = logger
    telegram_logger = logger
    db_logger = logger


def log_trade(action: str, symbol: str, side: str, price: float, 
              pnl: float = None, case: int = None, extra: str = ""):
    """Compatibilidad con log_trade anterior"""
    msg = f"{action} | {symbol} | {side} | ${price:.4f}"
    if pnl is not None:
        msg += f" | PnL: ${pnl:.4f}"
    if case:
        msg += f" | Case {case}"
    if extra:
        msg += f" | {extra}"
    get_logger().info(msg)


def log_scan_result(total_pairs: int, valid_swings: int, 
                    cases: dict, duration: float):
    """Compatibilidad con log_scan_result anterior"""
    get_logger().info(
        f"Scan: {total_pairs} pairs | {valid_swings} swings | "
        f"C1:{cases.get(1,0)} C3:{cases.get(3,0)} C4:{cases.get(4,0)} | "  # C2 eliminado
        f"{duration:.1f}s"
    )


# Limpiar logs antiguos e inicializar loggers al importar el módulo
cleanup_old_logs(keep_last=10)
_init_loggers()

