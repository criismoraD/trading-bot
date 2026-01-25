"""
Sistema de Paper Trading para Binance Futures
Simula operaciones sin usar dinero real
"""
import json
import os
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict
from enum import Enum

from logger import trading_logger as logger, log_trade

# Comisiones de Bybit Futuros
MAKER_FEE = 0.0002  # 0.02% para órdenes Limit (C1, C3)
TAKER_FEE = 0.00055 # 0.055% para órdenes Market (C4)

class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"

class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderStatus(Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"

class PositionSide(Enum):
    LONG = "LONG"
    SHORT = "SHORT"

@dataclass
class Order:
    id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float  # Precio de entrada (para LIMIT) o precio ejecutado (para MARKET)
    margin: float
    leverage: int
    take_profit: float
    stop_loss: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    filled_at: Optional[str] = None
    closed_at: Optional[str] = None
    pnl: float = 0.0
    # linked_order_id eliminado - ya no se usan órdenes vinculadas
    strategy_case: int = 0  # Caso de trading (1-4)
    fib_high: Optional[float] = None  # Precio del High (100%) del swing
    fib_low: Optional[float] = None   # Precio del Low (0%) del swing
    entry_fib_level: Optional[float] = None  # Nivel Fibonacci de entrada (ej: 0.618 para 61.8%)
    current_price: float = 0.0  # Precio actual del mercado (vivo/informativo)
    creation_price: float = 0.0 # Precio de mercado al momento de crear la orden
    creation_fib_level: Optional[float] = None # Nivel Fibonacci del mercado al crear
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": self.quantity,
            "price": self.price,
            "margin": self.margin,
            "leverage": self.leverage,
            "take_profit": self.take_profit,
            "stop_loss": self.stop_loss,
            "status": self.status.value,
            "created_at": self.created_at,
            "filled_at": self.filled_at,
            "closed_at": self.closed_at,
            "pnl": self.pnl,
            # linked_order_id eliminado del registro
            "strategy_case": self.strategy_case,
            "fib_high": self.fib_high,
            "fib_low": self.fib_low,
            "strategy_case": self.strategy_case,
            "fib_high": self.fib_high,
            "fib_low": self.fib_low,
            "entry_fib_level": self.entry_fib_level,
            "current_price": self.current_price,
            "creation_price": self.creation_price,
            "creation_fib_level": self.creation_fib_level
        }

@dataclass
class Position:
    symbol: str
    side: PositionSide
    entry_price: float
    quantity: float
    margin: float
    leverage: int
    take_profit: float
    stop_loss: Optional[float] = None
    order_id: str = ""
    unrealized_pnl: float = 0.0
    current_price: float = 0.0  # Precio actual para calcular PnL
    strategy_case: int = 0  # Caso de trading (1-4)
    fib_high: Optional[float] = None  # Precio del High (100%) del swing
    fib_low: Optional[float] = None   # Precio del Low (0%) del swing
    entry_fib_level: Optional[float] = None  # Nivel Fibonacci de entrada (ej: 0.618 para 61.8%)
    creation_fib_level: Optional[float] = None  # Nivel Fibonacci del mercado al crear la orden
    # Historial de ejecuciones (para tracking de órdenes promediadas)
    executions: List[dict] = field(default_factory=list)  # [{price, qty, time, order_num}]
    # Timestamp de apertura para cooldown de TP/SL
    opened_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # Fecha de creación original (cuando fue orden pendiente)
    created_at: Optional[str] = None
    
    def calculate_pnl(self, current_price: float) -> float:
        """Calcular PnL no realizado y actualizar precios extremos"""
        from datetime import datetime
        current_time = datetime.now(timezone.utc).isoformat()
        
        self.current_price = current_price  # Guardar precio actual
        if self.side == PositionSide.SHORT:
            # Para SHORT: ganamos si el precio baja
            pnl = (self.entry_price - current_price) * self.quantity
        else:
            # Para LONG: ganamos si el precio sube
            pnl = (current_price - self.entry_price) * self.quantity
        self.unrealized_pnl = pnl
        
        # Actualizar precios extremos durante la operación        # LOGIC REMOVED: Pre/post close price tracking is no longer used.
        # Values will be cleared from JSON.
        pass
            
        return pnl
    
    def check_take_profit(self, current_price: float) -> bool:
        """Verificar si se alcanzó el Take Profit"""
        if self.side == PositionSide.SHORT:
            return current_price <= self.take_profit
        else:
            return current_price >= self.take_profit
    
    def check_stop_loss(self, current_price: float) -> bool:
        """Verificar si se alcanzó el Stop Loss"""
        sl_price = self.stop_loss
        
        # Si no hay SL guardado, calcularlo desde niveles Fibonacci
        if sl_price is None and self.fib_high and self.fib_low:
            range_val = self.fib_high - self.fib_low
            # SL por caso: C1/C1++/C2=110%, C3=94%, C4=93%
            case = self.strategy_case if self.strategy_case != 11 else 1
            sl_ratios = {1: 1.10, 2: 1.10, 3: 0.94, 4: 0.93}
            sl_ratio = sl_ratios.get(case, 1.10)
            sl_price = self.fib_low + (range_val * sl_ratio)
        
        if sl_price is None:
            return False
            
        if self.side == PositionSide.SHORT:
            return current_price >= sl_price
        else:
            return current_price <= sl_price


class PaperTradingAccount:
    def __init__(self, initial_balance: float, leverage: int, trades_file: str = "trades.json"):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.leverage = leverage
        self.trades_file = trades_file
        
        self.pending_orders: Dict[str, Order] = {}
        self.open_positions: Dict[str, Position] = {}
        self.trade_history: List[dict] = []
        self.cancelled_history: List[dict] = []  # Historial de órdenes canceladas
        self.order_counter = 0
        
        # === Monitoreo de posiciones cerradas (seguimiento de precio post-cierre) ===
        self.closed_positions_monitoring: Dict[str, dict] = {}  # {order_id: {symbol, close_price, price_history: [{price, time}]}}
        
        # === Control de guardado para evitar exceso de escrituras ===
        self._last_save_time = 0  # Timestamp del último guardado
        self._save_interval = 1.0  # Intervalo mínimo entre guardados (segundos)
        
        # === NUEVO: Estadísticas de operaciones simultáneas ===
        self.stats = {
            "max_simultaneous_positions": 0,
            "max_simultaneous_orders": 0,
            "max_simultaneous_total": 0,  # positions + orders
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0
        }
        
        # === NUEVO: Histórico de Equity (Balance + PnL Flotante) cada 1 min ===
        self.equity_history: List[dict] = []
        self._last_equity_record_time = 0
        
        # === Referencia al price_cache externo (se asigna desde bot.py) ===
        self.price_cache: Dict[str, float] = {}
        
        # Cargar historial si existe
        self._load_trades()
    
    def _generate_order_id(self) -> str:
        self.order_counter += 1
        return f"ORD-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{self.order_counter}"
    
    def update_max_simultaneous(self):
        """Actualizar estadística de máximo de operaciones simultáneas"""
        num_positions = len(self.open_positions)
        num_orders = len(self.pending_orders)
        total = num_positions + num_orders
        
        if num_positions > self.stats["max_simultaneous_positions"]:
            self.stats["max_simultaneous_positions"] = num_positions
        
        if num_orders > self.stats["max_simultaneous_orders"]:
            self.stats["max_simultaneous_orders"] = num_orders
        
        if total > self.stats["max_simultaneous_total"]:
            self.stats["max_simultaneous_total"] = total
            print(f"📊 Nuevo máximo simultáneo: {total} ({num_positions} pos + {num_orders} órd)")
    
    def _load_trades(self):
        """Reiniciar trades.json al iniciar el bot (siempre empezar desde cero)"""
        # Siempre reiniciar - no cargar historial anterior
        self.trade_history = []
        self.balance = self.initial_balance
        self.closed_positions_monitoring = {}
        self.stats = {
            "max_simultaneous_positions": 0,
            "max_simultaneous_orders": 0,
            "max_simultaneous_total": 0,
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "cancelled_orders": 0
        }
        self.equity_history = []
        print(f"🗑️ trades.json reiniciado | Balance inicial: ${self.initial_balance:.2f}")
        self._save_trades()  # Guardar archivo limpio
    
    def _serialize_position(self, pos: Position) -> dict:
        """Serializar una Position correctamente (enums como strings)"""
        return {
            "symbol": pos.symbol,
            "side": pos.side.value if hasattr(pos.side, 'value') else pos.side,
            "entry_price": pos.entry_price,
            "quantity": pos.quantity,
            "margin": pos.margin,
            "leverage": pos.leverage,
            "take_profit": pos.take_profit,
            "stop_loss": pos.stop_loss,
            "order_id": pos.order_id,
            "unrealized_pnl": pos.unrealized_pnl,
            "current_price": pos.current_price,
            "strategy_case": pos.strategy_case,
            "fib_high": pos.fib_high,
            "fib_low": pos.fib_low,
            "creation_fib_level": pos.creation_fib_level,
            "executions": pos.executions,
            "opened_at": pos.opened_at,
            "created_at": pos.created_at
        }
    
    def _save_trades(self):
        """Guardar historial de trades a JSON"""
        try:
            # Actualizar estadísticas de trades
            self.stats["total_trades"] = len(self.trade_history)
            self.stats["winning_trades"] = len([t for t in self.trade_history if t.get("pnl", 0) >= 0])
            self.stats["losing_trades"] = len([t for t in self.trade_history if t.get("pnl", 0) < 0])
            
            data = {
                "balance": self.balance,
                "initial_balance": self.initial_balance,
                "leverage": self.leverage,
                "history": self.trade_history,
                "cancelled_history": self.cancelled_history,
                "stats": self.stats,
                "open_positions": {k: self._serialize_position(v) for k, v in self.open_positions.items()},
                "pending_orders": {k: v.to_dict() for k, v in self.pending_orders.items()},
                "equity_history": self.equity_history,  # Histórico de balance total
                "closed_positions_monitoring": self.closed_positions_monitoring,  # Persistir monitoreo post-cierre
                "last_updated": datetime.now(timezone.utc).isoformat()
            }
            with open(self.trades_file, 'w') as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            print(f"⚠️ Error guardando trades: {e}")
    
    def get_available_margin(self) -> float:
        """Obtener margen disponible"""
        used_margin = sum(pos.margin for pos in self.open_positions.values())
        used_margin += sum(order.margin for order in self.pending_orders.values())
        return self.balance - used_margin
    
    def get_unrealized_pnl(self, current_prices: Dict[str, float] = None) -> float:
        """Calcular PnL no realizado total de todas las posiciones"""
        total_pnl = 0.0
        for pos in self.open_positions.values():
            if current_prices and pos.symbol in current_prices:
                current_price = current_prices[pos.symbol]
                pos.unrealized_pnl = pos.calculate_pnl(current_price)
            total_pnl += pos.unrealized_pnl
        return total_pnl
    
    def get_margin_balance(self, current_prices: Dict[str, float] = None) -> float:
        """Obtener balance de margen (balance + PnL no realizado)"""
        return self.balance + self.get_unrealized_pnl(current_prices)
    
    def record_equity_point(self, current_prices: Dict[str, float] = None):
        """Registrar el balance total actual (Equity) para el gráfico de historial"""
        unrealized_pnl = self.get_unrealized_pnl(current_prices)
        margin_balance = self.balance + unrealized_pnl
        
        # Nuevos valores solicitados
        active_ops = len(self.open_positions) + len(self.pending_orders)
        drawdown = self.initial_balance - margin_balance

        point = {
            "time": datetime.now(timezone.utc).isoformat(),
            "balance": round(self.balance, 2),
            "unrealized_pnl": round(unrealized_pnl, 4),
            "equity": round(margin_balance, 2),
            "active_operations_count": active_ops,
            "balance_drawdown": round(drawdown, 2)
        }
        
        self.equity_history.append(point)
        # Mantener un límite razonable para evitar archivos gigantes (ej: 10,000 puntos = 7 días a 1 min)
        if len(self.equity_history) > 10000:
            self.equity_history.pop(0)
            
        # Forzar un guardado inmediato cuando se registra el punto
        self._save_trades()
    
    def update_positions_pnl(self, current_prices: Dict[str, float]):
        """Actualizar PnL de todas las posiciones con precios actuales y guardar"""
        updated = False
        for pos in self.open_positions.values():
            if pos.symbol in current_prices:
                pos.calculate_pnl(current_prices[pos.symbol])
                updated = True
        if updated:
            self._save_trades()  # Persistir cambios en JSON

    def place_limit_order(self, symbol: str, side: OrderSide, price: float,
                          margin: float, take_profit: float, stop_loss: Optional[float] = None,
                          strategy_case: int = 0,
                          fib_high: Optional[float] = None,
                          fib_low: Optional[float] = None,
                          entry_fib_level: Optional[float] = None,
                          current_price: Optional[float] = None) -> Optional[Order]:
        """Colocar una orden límite"""
        # Verificar margen disponible
        from config import MIN_AVAILABLE_MARGIN
        if self.get_available_margin() < MIN_AVAILABLE_MARGIN:
            print(f"⚠️ No hay suficiente margen disponible para colocar orden límite en {symbol}")
            return None
        
        # Calcular cantidad basada en margen y apalancamiento
        notional_value = margin * self.leverage
        quantity = notional_value / price
        
        # Calcular entry_fib_level si no se proporciona
        if entry_fib_level is None and fib_high is not None and fib_low is not None:
            fib_range = fib_high - fib_low
            if fib_range > 0:
                entry_fib_level = (price - fib_low) / fib_range
        
        # Calcular creation_fib_level si hay current_price
        creation_fib_level = None
        if current_price is not None and fib_high is not None and fib_low is not None:
             fib_range = fib_high - fib_low
             if fib_range > 0:
                  creation_fib_level = (current_price - fib_low) / fib_range
        
        order = Order(
            id=self._generate_order_id(),
            symbol=symbol,
            side=side,
            order_type=OrderType.LIMIT,
            quantity=quantity,
            price=price,
            margin=margin,
            leverage=self.leverage,
            take_profit=take_profit,
            stop_loss=stop_loss,
            # linked_order_id eliminado
            strategy_case=strategy_case,
            fib_high=fib_high,
            fib_low=fib_low,
            entry_fib_level=entry_fib_level,
            creation_price=current_price if current_price else 0.0,
            creation_fib_level=creation_fib_level
        )
        
        self.pending_orders[order.id] = order
        self.update_max_simultaneous()  # Track máximo simultáneo
        self._save_trades()
        
        sl_text = f" | SL: ${stop_loss:.4f}" if stop_loss else ""
        fib_text = f" | Fib: {entry_fib_level*100:.1f}%" if entry_fib_level else ""
        print(f"📝 Orden Límite: {side.value} {symbol} @ ${price:.4f} | Margen: ${margin} | TP: ${take_profit:.4f}{sl_text}{fib_text}")
        return order
    
    def place_market_order(self, symbol: str, side: OrderSide, current_price: float,
                           margin: float, take_profit: float, stop_loss: Optional[float] = None,
                           strategy_case: int = 0,
                           fib_high: Optional[float] = None,
                           fib_low: Optional[float] = None,
                           entry_fib_level: Optional[float] = None) -> Optional[Position]:
        """Colocar orden de mercado (ejecución inmediata)"""
        # Verificar margen disponible
        from config import MIN_AVAILABLE_MARGIN
        if self.get_available_margin() < MIN_AVAILABLE_MARGIN:
            print(f"⚠️ No hay suficiente margen disponible para orden de mercado en {symbol}")
            return None
        
        # Calcular cantidad
        notional_value = margin * self.leverage
        quantity = notional_value / current_price
        
        # Calcular entry_fib_level si no se proporciona
        if entry_fib_level is None and fib_high is not None and fib_low is not None:
            fib_range = fib_high - fib_low
            if fib_range > 0:
                entry_fib_level = (current_price - fib_low) / fib_range
        
        # Para órdenes de mercado, creation_fib_level = entry_fib_level
        # (porque se ejecutan inmediatamente al precio actual)
        creation_fib_level = entry_fib_level
        
        order_id = self._generate_order_id()
        position_side = PositionSide.SHORT if side == OrderSide.SELL else PositionSide.LONG
        
        # Detener monitoreo de posiciones cerradas del mismo par
        self._stop_monitoring_symbol(symbol)
        
        # Crear nueva posición (sin fusión - cada trade es independiente)
        position = Position(
            symbol=symbol,
            side=position_side,
            entry_price=current_price,
            quantity=quantity,
            margin=margin,
            leverage=self.leverage,
            take_profit=take_profit,
            stop_loss=stop_loss,
            order_id=order_id,
            strategy_case=strategy_case,
            fib_high=fib_high,
            fib_low=fib_low,
            entry_fib_level=entry_fib_level,
            creation_fib_level=creation_fib_level,
            executions=[{
                "order_num": 1,
                "price": current_price,
                "quantity": quantity,
                "type": "MARKET",
                "time": datetime.now().isoformat()
            }],
            created_at=datetime.now(timezone.utc).isoformat() # Para market order, creado y abierto es igual
        )
        self.open_positions[order_id] = position
        
        # Cobrar comisión de apertura (Taker para Market Order)
        notional_value = quantity * current_price
        fee = notional_value * TAKER_FEE
        self.balance -= fee
            
        self._save_trades()
        
        # Log y notificación
        log_trade("OPEN", symbol, side.value, current_price, case=strategy_case)
        logger.info(f"Orden Mercado: {side.value} {symbol} @ ${current_price:.4f} | Margen: ${margin} | TP: ${take_profit:.4f}")
        
        # Notificación Telegram DESACTIVADA para aperturas
        # Solo se notifica al cerrar
        
        return position
    
    def _fill_order(self, order_or_id, fill_price: float):
        """Ejecutar una orden límite
        
        Args:
            order_or_id: Puede ser un objeto Order o un string order_id
            fill_price: Precio de ejecución
        """
        # Soportar tanto Order object como string id
        if isinstance(order_or_id, Order):
            order_id = order_or_id.id
        else:
            order_id = order_or_id
            
        if order_id not in self.pending_orders:
            return
        
        order = self.pending_orders.pop(order_id)
        order.status = OrderStatus.FILLED
        order.filled_at = datetime.now(timezone.utc).isoformat()
        order.price = fill_price  # Precio real de ejecución
        
        position_side = PositionSide.SHORT if order.side == OrderSide.SELL else PositionSide.LONG
        
        # Crear nueva posición (sin fusión - cada C1++ es independiente)
        position = Position(
            symbol=order.symbol,
            side=position_side,
            entry_price=fill_price,
            quantity=order.quantity,
            margin=order.margin,
            leverage=order.leverage,
            take_profit=order.take_profit,
            stop_loss=order.stop_loss,
            order_id=order_id,
            strategy_case=order.strategy_case,
            fib_high=order.fib_high,
            fib_low=order.fib_low,
            entry_fib_level=(fill_price - order.fib_low) / (order.fib_high - order.fib_low) if (order.fib_high and order.fib_low and (order.fib_high - order.fib_low) != 0) else order.entry_fib_level,
            creation_fib_level=order.creation_fib_level,  # Nivel Fib cuando se creó la orden
            executions=[{
                "order_num": 1,
                "price": fill_price,
                "quantity": order.quantity,
                "type": "LIMIT",
                "time": datetime.now(timezone.utc).isoformat()
            }],
            created_at=order.created_at # Conservar fecha original de la orden
        )
        
        self.open_positions[order_id] = position
        
        # Cobrar comisión de apertura (Maker para Limit Order)
        notional_value = position.quantity * fill_price
        fee = notional_value * MAKER_FEE
        self.balance -= fee
            
        self.update_max_simultaneous()  # Track máximo simultáneo
        self._save_trades()
        
        print(f"✅ Orden ejecutada: {order.side.value} {order.symbol} @ ${fill_price:.4f}")
    
    def check_positions(self, symbol: str, current_price: float):
        """Verificar TP/SL de posiciones abiertas"""
        import time
        positions_to_close = []
        significant_change = False
        
        # Cooldown: No cerrar posiciones abiertas hace menos de 1 segundo
        COOLDOWN_SECONDS = 1
        now = datetime.now(timezone.utc)
        
        for order_id, position in self.open_positions.items():
            if position.symbol != symbol:
                continue
            
            # Verificar cooldown - evitar cerrar posiciones recién abiertas
            try:
                opened_time = datetime.fromisoformat(position.opened_at)
                age_seconds = (now - opened_time).total_seconds()
                if age_seconds < COOLDOWN_SECONDS:
                    # print(f"⏳ {symbol}: Cooldown activo ({age_seconds:.1f}s < {COOLDOWN_SECONDS}s)")
                    continue
            except (AttributeError, ValueError):
                pass  # Si no tiene opened_at o es inválido, continuar normalmente
            
            # Logic removed for significant change detection involving min/max pre close
            pass
            
            if position.check_take_profit(current_price):
                positions_to_close.append((order_id, "TP", current_price))
            elif position.check_stop_loss(current_price):
                positions_to_close.append((order_id, "SL", current_price))
        
        for order_id, reason, price in positions_to_close:
            self._close_position(order_id, price, reason)
        
        # Guardar si hay posiciones abiertas actualizadas (con throttle)
        current_time = time.time()
        should_save = (
            not positions_to_close and  # No se cerró nada (ya guarda en _close_position)
            len(self.open_positions) > 0 and  # Hay posiciones abiertas
            (significant_change or current_time - self._last_save_time >= self._save_interval)
        )
        
        if should_save:
            self._save_trades()
            self._last_save_time = current_time
        
        # Logic removed: _update_closed_positions_price call

    # Logic removed: _update_closed_positions_price method
    
    def _stop_monitoring_symbol(self, symbol: str):
        """Detener monitoreo de precios post-cierre para un par cuando se abre nueva operación"""
        orders_to_remove = [
            order_id for order_id, data in self.closed_positions_monitoring.items()
            if data["symbol"] == symbol
        ]
        for order_id in orders_to_remove:
            del self.closed_positions_monitoring[order_id]

    def check_pending_orders(self, symbol: str, current_price: float):
        """Verificar si se activan órdenes pendientes (Limit Orders)"""
        orders_to_fill = []

        for order_id, order in self.pending_orders.items():
            if order.symbol != symbol:
                continue
            
            # Actualizar precio actual en la orden para visualización json
            order.current_price = current_price
            
            # --- REGLAS DE CANCELACIÓN AUTOMÁTICA ---
            # Leer configuración de cancelación
            cancel_c1 = 0.382
            cancel_c3 = 0.50
            try:
                with open('shared_config.json', 'r') as f:
                    cfg = json.load(f)
                    trading_cfg = cfg.get('trading', {})
                    cancel_c1 = trading_cfg.get('c1_cancel_below', 0.382)
                    cancel_c3 = trading_cfg.get('c3_cancel_below', 0.50)
            except:
                pass

            cancel_reason = None
            if order.fib_high and order.fib_low:
                fib_range = order.fib_high - order.fib_low
                if fib_range > 0:
                    # Calcular nivel fib actual relativo al swing
                    current_fib = (current_price - order.fib_low) / fib_range
                    
                    # Caso 1: Cancelar si baja al nivel configurado
                    if order.strategy_case == 1 and current_fib <= cancel_c1:
                        cancel_reason = f"Precio tocó {cancel_c1*100}% (C1 anulado)"
                    
                    # Caso 3: Cancelar si baja al nivel configurado
                    if order.strategy_case == 3 and current_fib <= cancel_c3:
                        cancel_reason = f"Precio tocó {cancel_c3*100}% (C3 anulado)"

            if cancel_reason:
                orders_to_fill.append((order, "CANCEL", cancel_reason))
                continue
            # ----------------------------------------

            should_fill = False
            # Lógica para SELL LIMIT (Short): Precio sube y toca nuestra orden
            if order.side == OrderSide.SELL and order.order_type == OrderType.LIMIT:
                if current_price >= order.price:
                    should_fill = True
            
            # Lógica para BUY LIMIT (Long): Precio baja y toca nuestra orden
            elif order.side == OrderSide.BUY and order.order_type == OrderType.LIMIT:
                if current_price <= order.price:
                    should_fill = True
            
            if should_fill:
                orders_to_fill.append((order, "FILL", None))
        
        # Ejecutar fuera del bucle para no modificar el diccionario mientras iteramos
        for order, action, reason in orders_to_fill:
            if action == "FILL":
                print(f"⚡ Orden Límite ACTIVADA: {order.symbol} @ {current_price:.4f} (Limit: {order.price:.4f})")
                self._fill_order(order, current_price)
            elif action == "CANCEL":
                # Cancelar orden
                if order.id in self.pending_orders:
                    del self.pending_orders[order.id]
                    order.status = OrderStatus.CANCELLED
                    
                    # Registrar cancelación
                    self.stats["cancelled_orders"] = self.stats.get("cancelled_orders", 0) + 1
                    
                    # Guardar historial de cancelación
                    cancel_record = order.to_dict()
                    cancel_record["cancelled_at"] = datetime.now(timezone.utc).isoformat()
                    cancel_record["cancel_reason"] = reason
                    self.cancelled_history.append(cancel_record)
                    
                    self._save_trades()
                    print(f"🚫 Orden {order.symbol} CANCELADA autom.: {reason}")
    
    def _close_position(self, order_id: str, close_price: float, reason: str):
        """Cerrar una posición"""
        if order_id not in self.open_positions:
            return
        
        position = self.open_positions.pop(order_id)
        pnl = position.calculate_pnl(close_price)
        
        # IMPORTANTE: Limpiar price_cache del símbolo para evitar TP/SL falsos en nuevas posiciones
        if self.price_cache and position.symbol in self.price_cache:
            del self.price_cache[position.symbol]
            # print(f"🧹 Cache limpiado para {position.symbol}")
        
        # Actualizar balance
        self.balance += pnl
        
        # Cobrar comisión de cierre según el caso
        # C1 (1, 11) y C3 son Maker (0.02%), C4 es Taker (0.055%)
        notional_value = position.quantity * close_price
        fee_rate = MAKER_FEE if position.strategy_case in [1, 3, 11] else TAKER_FEE
        closing_fee = notional_value * fee_rate
        self.balance -= closing_fee
        
        # Calcular ganancia/pérdida potencial en USDT
        # Para SHORT: ganancia si precio baja al TP, pérdida si sube al SL
        # Para LONG: ganancia si precio sube al TP, pérdida si baja al SL
        potential_profit_usdt = 0.0
        potential_loss_usdt = 0.0
        
        if position.take_profit:
            if position.side == PositionSide.SHORT:
                potential_profit_usdt = (position.entry_price - position.take_profit) * position.quantity
            else:  # LONG
                potential_profit_usdt = (position.take_profit - position.entry_price) * position.quantity
        
        if position.stop_loss:
            if position.side == PositionSide.SHORT:
                potential_loss_usdt = (position.stop_loss - position.entry_price) * position.quantity
            else:  # LONG
                potential_loss_usdt = (position.entry_price - position.stop_loss) * position.quantity
        
        # Guardar en historial
        trade_record = {
            "order_id": order_id,
            "symbol": position.symbol,
            "side": position.side.value if hasattr(position.side, 'value') else position.side,
            "entry_price": position.entry_price,
            "close_price": close_price,
            "quantity": position.quantity,
            "margin": position.margin,
            "pnl": pnl,
            "potential_profit_usdt": round(potential_profit_usdt, 4),  # Ganancia potencial al TP
            "potential_loss_usdt": round(potential_loss_usdt, 4),      # Pérdida potencial al SL
            "strategy_case": position.strategy_case,
            "reason": reason,
            "fib_high": position.fib_high,  # Nivel 100% (precio del High)
            "fib_low": position.fib_low,    # Nivel 0% (precio del Low)
            "stop_loss": round(position.stop_loss, 8) if position.stop_loss else None,
            "take_profit": position.take_profit,
            "creation_fib_level": position.creation_fib_level,
            "executions": position.executions,  # Historial de ejecuciones
            "opened_at": position.opened_at,  # Fecha de entrada (cuando se abrió/ejecutó)
            "created_at": position.created_at, # Fecha de creación original (scanner)
            "closed_at": datetime.now(timezone.utc).isoformat()
        }
        self.trade_history.append(trade_record)
        
        # Iniciar monitoreo post-cierre
        self.closed_positions_monitoring[order_id] = {
            "symbol": position.symbol,
            "close_price": close_price,
            "history_index": len(self.trade_history) - 1  # Índice en trade_history para actualizar
        }
        
        # Función de cancelar órdenes vinculadas eliminada - ya no se usan
        
        self._save_trades()
        
        # Log y notificación
        emoji = "💰" if pnl > 0 else "📉"
        log_trade("CLOSE", position.symbol, position.side.value if hasattr(position.side, 'value') else position.side, 
                  close_price, pnl=pnl, case=position.strategy_case, extra=reason)
        logger.info(f"{emoji} Posición cerrada ({reason}): {position.symbol} | PnL: ${pnl:.4f} | Balance: ${self.balance:.2f}")
        
        # Notificación Telegram (async desde sync)
        try:
            from telegram_bot import telegram_bot, AUTHORIZED_CHATS
            if AUTHORIZED_CHATS:
                asyncio.create_task(telegram_bot.send_trade_alert(
                    "CLOSE", position.symbol, "", close_price, pnl=pnl
                ))
        except Exception:
            pass  # Si falla Telegram, no interrumpir el bot
    
    # _cancel_linked_orders eliminado - ya no se usan órdenes vinculadas
    
    def cancel_order(self, order_id: str):
        """Cancelar una orden pendiente"""
        if order_id in self.pending_orders:
            order = self.pending_orders.pop(order_id)
            order.status = OrderStatus.CANCELLED
            
            # Registrar cancelación
            self.stats["cancelled_orders"] = self.stats.get("cancelled_orders", 0) + 1
            
            self._save_trades()
            print(f"🚫 Orden cancelada: {order.id}")
    
    def get_status(self) -> dict:
        """Obtener estado actual de la cuenta"""
        total_unrealized_pnl = sum(pos.unrealized_pnl for pos in self.open_positions.values())
        margin_balance = self.balance + total_unrealized_pnl
        
        return {
            "balance": self.balance,
            "available_margin": self.get_available_margin(),
            "total_unrealized_pnl": total_unrealized_pnl,
            "margin_balance": margin_balance,
            "open_positions": len(self.open_positions),
            "pending_orders": len(self.pending_orders),
            "total_trades": len(self.trade_history),
            "trade_history": self.trade_history[-20:]  # Últimos 20 trades
        }
    
    def print_status(self):
        """Imprimir estado de la cuenta"""
        status = self.get_status()
        print("\n" + "="*50)
        print("📊 ESTADO DE CUENTA PAPER TRADING")
        print("="*50)
        print(f"💵 Balance: ${status['balance']:.2f}")
        print(f"💳 Margen disponible: ${status['available_margin']:.2f}")
        print(f"📈 PnL no realizado: ${status['total_unrealized_pnl']:.4f}")
        print(f"📊 Balance de Margen: ${status['margin_balance']:.2f}")
        print(f"📂 Posiciones abiertas: {status['open_positions']}")
        print(f"⏳ Órdenes pendientes: {status['pending_orders']}")
        print(f"📜 Total trades: {status['total_trades']}")
        print("="*50 + "\n")
    
    def print_open_trades(self):
        """Imprimir todas las operaciones abiertas y órdenes límite"""
        if not self.open_positions and not self.pending_orders:
            print("📭 Sin operaciones abiertas ni órdenes pendientes")
            return
        
        print("\n" + "-"*60)
        print("📋 OPERACIONES ABIERTAS")
        print("-"*60)
        
        # Posiciones abiertas
        if self.open_positions:
            print("\n🔴 POSICIONES:")
            for order_id, pos in self.open_positions.items():
                pnl_emoji = "🟢" if pos.unrealized_pnl >= 0 else "🔴"
                print(f"   {pos.symbol} | {pos.side.value} @ ${pos.entry_price:.4f}")
                print(f"      TP: ${pos.take_profit:.4f} | Margen: ${pos.margin:.2f}")
                print(f"      {pnl_emoji} PnL: ${pos.unrealized_pnl:.4f}")
        
        # Órdenes límite pendientes
        if self.pending_orders:
            print("\n🟠 ÓRDENES LÍMITE:")
            for order_id, order in self.pending_orders.items():
                print(f"   {order.symbol} | {order.side.value} @ ${order.price:.4f}")
                print(f"      TP: ${order.take_profit:.4f} | Margen: ${order.margin:.2f}")
        
        print("-"*60 + "\n")
    
    def get_open_trades_for_web(self) -> List[dict]:
        """Obtener trades abiertos para mostrar en la web"""
        trades = []
        
        # Posiciones abiertas
        for order_id, pos in self.open_positions.items():
            trades.append({
                "id": order_id,
                "symbol": pos.symbol,
                "type": "POSITION",
                "side": pos.side.value,
                "entry_price": pos.entry_price,
                "take_profit": pos.take_profit,
                "margin": pos.margin,
                "unrealized_pnl": pos.unrealized_pnl
            })
        
        # Órdenes pendientes
        for order_id, order in self.pending_orders.items():
            trades.append({
                "id": order_id,
                "symbol": order.symbol,
                "type": "PENDING",
                "side": order.side.value,
                "price": order.price,
                "take_profit": order.take_profit,
                "margin": order.margin
            })
        
        return trades
