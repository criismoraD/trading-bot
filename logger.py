"""
Sistema de Logging Profesional para el Bot de Trading
Logs rotativos con niveles DEBUG, INFO, WARNING, ERROR
"""
import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

# Directorio de logs
LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# Formato de logs
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-15s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Colores para consola (ANSI)
COLORS = {
    'DEBUG': '\033[94m',     # Azul
    'INFO': '\033[92m',      # Verde
    'WARNING': '\033[93m',   # Amarillo
    'ERROR': '\033[91m',     # Rojo
    'CRITICAL': '\033[95m',  # Magenta
    'RESET': '\033[0m'
}


class ColoredFormatter(logging.Formatter):
    """Formatter con colores para la consola"""
    
    def format(self, record):
        color = COLORS.get(record.levelname, COLORS['RESET'])
        reset = COLORS['RESET']
        
        # Añadir color al nivel
        record.levelname = f"{color}{record.levelname}{reset}"
        
        return super().format(record)


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Configurar un logger con salida a archivo y consola
    
    Args:
        name: Nombre del logger (ej: 'bot', 'scanner', 'telegram')
        level: Nivel de logging (DEBUG, INFO, WARNING, ERROR)
    
    Returns:
        Logger configurado
    """
    logger = logging.getLogger(name)
    
    # Evitar duplicados si ya está configurado
    if logger.handlers:
        return logger
    
    logger.setLevel(level)
    
    # Handler para archivo (rotativo, max 5MB, 5 backups)
    log_file = os.path.join(LOGS_DIR, f"{name}.log")
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5*1024*1024,  # 5 MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)  # Archivo guarda todo
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    
    # Handler para consola (con colores)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(ColoredFormatter(LOG_FORMAT, DATE_FORMAT))
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger


# Loggers pre-configurados
bot_logger = setup_logger('bot', logging.INFO)
scanner_logger = setup_logger('scanner', logging.INFO)
trading_logger = setup_logger('trading', logging.INFO)
telegram_logger = setup_logger('telegram', logging.INFO)
db_logger = setup_logger('database', logging.INFO)


def log_trade(action: str, symbol: str, side: str, price: float, 
              pnl: float = None, case: int = None, extra: str = ""):
    """
    Log específico para trades (guarda en archivo separado)
    """
    trades_logger = setup_logger('trades', logging.INFO)
    
    msg = f"{action} | {symbol} | {side} | ${price:.4f}"
    if pnl is not None:
        msg += f" | PnL: ${pnl:.4f}"
    if case:
        msg += f" | Caso {case}"
    if extra:
        msg += f" | {extra}"
    
    trades_logger.info(msg)


def log_scan_result(total_pairs: int, valid_swings: int, 
                    cases: dict, duration: float):
    """
    Log del resultado de un escaneo
    """
    scanner_logger.info(
        f"Scan completado: {total_pairs} pares | "
        f"{valid_swings} swings válidos | "
        f"C1:{cases.get(1,0)} C2:{cases.get(2,0)} C3:{cases.get(3,0)} C4:{cases.get(4,0)} | "
        f"{duration:.1f}s"
    )
