"""
Base de Datos SQLite para el Bot de Trading
Almacena trades, métricas y configuración de forma persistente
"""
import sqlite3
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from contextlib import contextmanager

from logger import db_logger as logger

# Ruta de la base de datos
DB_PATH = os.path.join(os.path.dirname(__file__), "trading_bot.db")


@contextmanager
def get_connection():
    """Context manager para conexiones a la base de datos"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Acceso por nombre de columna
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Error en base de datos: {e}")
        raise
    finally:
        conn.close()


def init_database():
    """Inicializar la base de datos con todas las tablas"""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Tabla de trades (historial completo)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                close_price REAL,
                quantity REAL NOT NULL,
                margin REAL NOT NULL,
                leverage INTEGER NOT NULL,
                take_profit REAL,
                stop_loss REAL,
                pnl REAL DEFAULT 0,
                min_pnl REAL DEFAULT 0,
                max_pnl REAL DEFAULT 0,
                strategy_case INTEGER DEFAULT 0,
                status TEXT DEFAULT 'OPEN',
                reason TEXT,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Tabla de órdenes pendientes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                price REAL NOT NULL,
                quantity REAL NOT NULL,
                margin REAL NOT NULL,
                leverage INTEGER NOT NULL,
                take_profit REAL,
                stop_loss REAL,
                linked_order_id TEXT,
                strategy_case INTEGER DEFAULT 0,
                status TEXT DEFAULT 'PENDING',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Tabla de balance (snapshots)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS balance_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                balance REAL NOT NULL,
                unrealized_pnl REAL DEFAULT 0,
                margin_used REAL DEFAULT 0,
                open_positions INTEGER DEFAULT 0,
                pending_orders INTEGER DEFAULT 0,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Tabla de métricas diarias
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE NOT NULL,
                starting_balance REAL NOT NULL,
                ending_balance REAL,
                total_trades INTEGER DEFAULT 0,
                winning_trades INTEGER DEFAULT 0,
                losing_trades INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                max_drawdown REAL DEFAULT 0,
                best_trade REAL DEFAULT 0,
                worst_trade REAL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Tabla de configuración
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Índices para optimizar consultas
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_opened_at ON trades(opened_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pending_symbol ON pending_orders(symbol)")
        
        logger.info("Base de datos inicializada correctamente")


# ===== OPERACIONES DE TRADES =====

def save_trade(trade_data: dict) -> int:
    """Guardar un trade en la base de datos"""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO trades (
                order_id, symbol, side, entry_price, close_price,
                quantity, margin, leverage, take_profit, stop_loss,
                pnl, min_pnl, max_pnl, strategy_case, status, reason,
                opened_at, closed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_data.get('order_id'),
            trade_data.get('symbol'),
            trade_data.get('side'),
            trade_data.get('entry_price'),
            trade_data.get('close_price'),
            trade_data.get('quantity'),
            trade_data.get('margin'),
            trade_data.get('leverage', 10),
            trade_data.get('take_profit'),
            trade_data.get('stop_loss'),
            trade_data.get('pnl', 0),
            trade_data.get('min_pnl', 0),
            trade_data.get('max_pnl', 0),
            trade_data.get('strategy_case', 0),
            trade_data.get('status', 'OPEN'),
            trade_data.get('reason'),
            trade_data.get('opened_at', datetime.now().isoformat()),
            trade_data.get('closed_at')
        ))
        
        return cursor.lastrowid


def close_trade(order_id: str, close_price: float, pnl: float, reason: str):
    """Cerrar un trade existente"""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE trades 
            SET close_price = ?, pnl = ?, status = 'CLOSED', 
                reason = ?, closed_at = ?
            WHERE order_id = ?
        """, (close_price, pnl, reason, datetime.now().isoformat(), order_id))
        
        logger.info(f"Trade cerrado: {order_id} | PnL: ${pnl:.4f}")


def get_open_trades() -> List[dict]:
    """Obtener todos los trades abiertos"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE status = 'OPEN'")
        return [dict(row) for row in cursor.fetchall()]


def get_trade_history(limit: int = 100, symbol: str = None) -> List[dict]:
    """Obtener historial de trades"""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        if symbol:
            cursor.execute(
                "SELECT * FROM trades WHERE status = 'CLOSED' AND symbol = ? ORDER BY closed_at DESC LIMIT ?",
                (symbol, limit)
            )
        else:
            cursor.execute(
                "SELECT * FROM trades WHERE status = 'CLOSED' ORDER BY closed_at DESC LIMIT ?",
                (limit,)
            )
        
        return [dict(row) for row in cursor.fetchall()]


# ===== OPERACIONES DE ÓRDENES PENDIENTES =====

def save_pending_order(order_data: dict) -> int:
    """Guardar una orden pendiente"""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO pending_orders (
                order_id, symbol, side, order_type, price, quantity,
                margin, leverage, take_profit, stop_loss, linked_order_id,
                strategy_case, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            order_data.get('order_id'),
            order_data.get('symbol'),
            order_data.get('side'),
            order_data.get('order_type', 'LIMIT'),
            order_data.get('price'),
            order_data.get('quantity'),
            order_data.get('margin'),
            order_data.get('leverage', 10),
            order_data.get('take_profit'),
            order_data.get('stop_loss'),
            order_data.get('linked_order_id'),
            order_data.get('strategy_case', 0),
            order_data.get('status', 'PENDING')
        ))
        
        return cursor.lastrowid


def remove_pending_order(order_id: str):
    """Eliminar una orden pendiente"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM pending_orders WHERE order_id = ?", (order_id,))


def get_pending_orders() -> List[dict]:
    """Obtener todas las órdenes pendientes"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM pending_orders WHERE status = 'PENDING'")
        return [dict(row) for row in cursor.fetchall()]


# ===== MÉTRICAS =====

def save_balance_snapshot(balance: float, unrealized_pnl: float, 
                          margin_used: float, open_positions: int, 
                          pending_orders: int):
    """Guardar snapshot del balance"""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO balance_history (
                balance, unrealized_pnl, margin_used, 
                open_positions, pending_orders
            ) VALUES (?, ?, ?, ?, ?)
        """, (balance, unrealized_pnl, margin_used, open_positions, pending_orders))


def get_balance_history(hours: int = 24) -> List[dict]:
    """Obtener historial de balance de las últimas N horas"""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        cursor.execute(
            "SELECT * FROM balance_history WHERE timestamp >= ? ORDER BY timestamp",
            (since,)
        )
        
        return [dict(row) for row in cursor.fetchall()]


def update_daily_metrics(date: str = None):
    """Actualizar métricas del día"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Obtener trades del día
        cursor.execute("""
            SELECT * FROM trades 
            WHERE DATE(closed_at) = ? AND status = 'CLOSED'
        """, (date,))
        
        trades = cursor.fetchall()
        
        if not trades:
            return
        
        total_trades = len(trades)
        winning = sum(1 for t in trades if t['pnl'] > 0)
        losing = sum(1 for t in trades if t['pnl'] < 0)
        total_pnl = sum(t['pnl'] for t in trades)
        best = max((t['pnl'] for t in trades), default=0)
        worst = min((t['pnl'] for t in trades), default=0)
        max_dd = min((t['min_pnl'] for t in trades), default=0)
        
        cursor.execute("""
            INSERT OR REPLACE INTO daily_metrics (
                date, starting_balance, ending_balance, total_trades,
                winning_trades, losing_trades, total_pnl, max_drawdown,
                best_trade, worst_trade
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (date, 0, 0, total_trades, winning, losing, total_pnl, max_dd, best, worst))


def get_statistics() -> dict:
    """Obtener estadísticas generales"""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Total trades
        cursor.execute("SELECT COUNT(*) FROM trades WHERE status = 'CLOSED'")
        total = cursor.fetchone()[0]
        
        # Winning trades
        cursor.execute("SELECT COUNT(*) FROM trades WHERE status = 'CLOSED' AND pnl > 0")
        winners = cursor.fetchone()[0]
        
        # Total PnL
        cursor.execute("SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE status = 'CLOSED'")
        total_pnl = cursor.fetchone()[0]
        
        # Average win/loss
        cursor.execute("SELECT COALESCE(AVG(pnl), 0) FROM trades WHERE status = 'CLOSED' AND pnl > 0")
        avg_win = cursor.fetchone()[0]
        
        cursor.execute("SELECT COALESCE(AVG(pnl), 0) FROM trades WHERE status = 'CLOSED' AND pnl < 0")
        avg_loss = cursor.fetchone()[0]
        
        # Por caso
        cases = {}
        for case_num in [1, 2, 3, 4]:
            cursor.execute("""
                SELECT COUNT(*), COALESCE(SUM(pnl), 0) 
                FROM trades 
                WHERE status = 'CLOSED' AND strategy_case = ?
            """, (case_num,))
            row = cursor.fetchone()
            cases[case_num] = {"count": row[0], "pnl": row[1]}
        
        # Max drawdown
        cursor.execute("SELECT COALESCE(MIN(min_pnl), 0) FROM trades")
        max_dd = cursor.fetchone()[0]
        
        return {
            "total_trades": total,
            "winning_trades": winners,
            "losing_trades": total - winners,
            "win_rate": (winners / total * 100) if total > 0 else 0,
            "total_pnl": total_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": abs(avg_win / avg_loss) if avg_loss != 0 else 0,
            "max_drawdown": max_dd,
            "by_case": cases
        }


def get_performance_by_symbol() -> List[dict]:
    """Obtener performance agrupado por símbolo"""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                symbol,
                COUNT(*) as total_trades,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winners,
                SUM(pnl) as total_pnl,
                AVG(pnl) as avg_pnl,
                MIN(min_pnl) as max_drawdown
            FROM trades 
            WHERE status = 'CLOSED'
            GROUP BY symbol
            ORDER BY total_pnl DESC
        """)
        
        return [dict(row) for row in cursor.fetchall()]


# ===== CONFIG =====

def set_config(key: str, value: str):
    """Guardar configuración"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO config (key, value, updated_at)
            VALUES (?, ?, ?)
        """, (key, value, datetime.now().isoformat()))


def get_config(key: str, default: str = None) -> Optional[str]:
    """Obtener configuración"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row[0] if row else default


# Inicializar la base de datos al importar el módulo
init_database()
