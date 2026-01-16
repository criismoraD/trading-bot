"""
Sistema de Paper Trading para Binance Futures
Simula operaciones sin usar dinero real
"""
import json
import os
import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict
from enum import Enum

from logger import trading_logger as logger, log_trade

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
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    filled_at: Optional[str] = None
    closed_at: Optional[str] = None
    pnl: float = 0.0
    linked_order_id: Optional[str] = None  # Para cancelar orden complementaria
    strategy_case: int = 0  # Caso de trading (1-4)
    fib_high: Optional[float] = None  # Precio del High (100%) del swing
    fib_low: Optional[float] = None   # Precio del Low (0%) del swing
    
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
            "linked_order_id": self.linked_order_id,
            "strategy_case": self.strategy_case,
            "fib_high": self.fib_high,
            "fib_low": self.fib_low
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
    linked_order_id: Optional[str] = None  # ID de orden vinculada para cerrar juntas
    min_pnl: float = 0.0  # Máximo drawdown registrado (valor negativo mas bajo)
    max_pnl: float = -9999.0  # Mínimo inicial bajo. Máximo beneficio alcanzado.
    strategy_case: int = 0  # Caso de trading (1-4)
    fib_high: Optional[float] = None  # Precio del High (100%) del swing
    fib_low: Optional[float] = None   # Precio del Low (0%) del swing
    # Historial de ejecuciones (para tracking de órdenes promediadas)
    executions: List[dict] = field(default_factory=list)  # [{price, qty, time, order_num}]
    
    def calculate_pnl(self, current_price: float) -> float:
        """Calcular PnL no realizado y actualizar min_pnl/max_pnl"""
        self.current_price = current_price  # Guardar precio actual
        if self.side == PositionSide.SHORT:
            # Para SHORT: ganamos si el precio baja
            pnl = (self.entry_price - current_price) * self.quantity
        else:
            # Para LONG: ganamos si el precio sube
            pnl = (current_price - self.entry_price) * self.quantity
        self.unrealized_pnl = pnl
        
        # Actualizar Max Drawdown (menor PnL registrado)
        if pnl < self.min_pnl:
            self.min_pnl = pnl

        # Actualizar Max Profit (mayor PnL registrado)
        # Inicializar max_pnl si es la primera vez (para no quedarse en -9999)
        if self.max_pnl == -9999.0:
            self.max_pnl = pnl
        elif pnl > self.max_pnl:
            self.max_pnl = pnl
            
        return pnl
    
    def check_take_profit(self, current_price: float) -> bool:
        """Verificar si se alcanzó el Take Profit"""
        if self.side == PositionSide.SHORT:
            return current_price <= self.take_profit
        else:
            return current_price >= self.take_profit
    
    def check_stop_loss(self, current_price: float) -> bool:
        """Verificar si se alcanzó el Stop Loss"""
        if self.stop_loss is None:
            return False
        if self.side == PositionSide.SHORT:
            return current_price >= self.stop_loss
        else:
            return current_price <= self.stop_loss


class PaperTradingAccount:
    def __init__(self, initial_balance: float, leverage: int, trades_file: str = "trades.json"):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.leverage = leverage
        self.trades_file = trades_file
        
        self.pending_orders: Dict[str, Order] = {}
        self.open_positions: Dict[str, Position] = {}
        self.trade_history: List[dict] = []
        self.order_counter = 0
        
        # === NUEVO: Estadísticas de operaciones simultáneas ===
        self.stats = {
            "max_simultaneous_positions": 0,
            "max_simultaneous_orders": 0,
            "max_simultaneous_total": 0,  # positions + orders
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0
        }
        
        # Cargar historial si existe
        self._load_trades()
    
    def _generate_order_id(self) -> str:
        self.order_counter += 1
        return f"ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}-{self.order_counter}"
    
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
        """Cargar historial de trades desde JSON"""
        if os.path.exists(self.trades_file):
            try:
                with open(self.trades_file, 'r') as f:
                    data = json.load(f)
                    self.trade_history = data.get("history", [])
                    self.balance = data.get("balance", self.initial_balance)
                    self.stats = data.get("stats", self.stats)
                    print(f"📂 Historial cargado: {len(self.trade_history)} trades, Balance: ${self.balance:.2f}")
                    if self.stats["max_simultaneous_total"] > 0:
                        print(f"   📊 Max simultáneo: {self.stats['max_simultaneous_total']} operaciones")
            except Exception as e:
                print(f"⚠️ Error cargando historial: {e}")
    
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
            "linked_order_id": pos.linked_order_id,
            "min_pnl": pos.min_pnl,
            "max_pnl": pos.max_pnl,
            "strategy_case": pos.strategy_case,
            "fib_high": pos.fib_high,
            "fib_low": pos.fib_low,
            "executions": pos.executions  # Historial de ejecuciones
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
                "stats": self.stats,
                "open_positions": {k: self._serialize_position(v) for k, v in self.open_positions.items()},
                "pending_orders": {k: v.to_dict() for k, v in self.pending_orders.items()},
                "last_updated": datetime.now().isoformat()
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
                          margin: float, take_profit: float, 
                          stop_loss: Optional[float] = None,
                          linked_order_id: Optional[str] = None,
                          strategy_case: int = 0,
                          fib_high: Optional[float] = None,
                          fib_low: Optional[float] = None) -> Optional[Order]:
        """Colocar orden límite"""
        if margin > self.get_available_margin():
            print(f"❌ Margen insuficiente. Disponible: ${self.get_available_margin():.2f}")
            return None
        
        # Calcular cantidad basada en margen y apalancamiento
        notional_value = margin * self.leverage
        quantity = notional_value / price
        
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
            linked_order_id=linked_order_id,
            strategy_case=strategy_case,
            fib_high=fib_high,
            fib_low=fib_low
        )
        
        self.pending_orders[order.id] = order
        self.update_max_simultaneous()  # Track máximo simultáneo
        self._save_trades()
        
        sl_text = f" | SL: ${stop_loss:.4f}" if stop_loss else ""
        print(f"📝 Orden Límite: {side.value} {symbol} @ ${price:.4f} | Margen: ${margin} | TP: ${take_profit:.4f}{sl_text}")
        return order
    
    def place_market_order(self, symbol: str, side: OrderSide, current_price: float,
                           margin: float, take_profit: float, stop_loss: Optional[float] = None,
                           strategy_case: int = 0,
                           fib_high: Optional[float] = None,
                           fib_low: Optional[float] = None) -> Optional[Position]:
        """Colocar orden de mercado (ejecución inmediata) con promedio de posición"""
        if margin > self.get_available_margin():
            print(f"❌ Margen insuficiente. Disponible: ${self.get_available_margin():.2f}")
            return None
        
        # Calcular cantidad
        notional_value = margin * self.leverage
        quantity = notional_value / current_price
        
        order_id = self._generate_order_id()
        position_side = PositionSide.SHORT if side == OrderSide.SELL else PositionSide.LONG
        
        # BUSCAR POSICIÓN EXISTENTE PARA FUSIONAR (Averaging)
        existing_pos_id = None
        for pid, pos in self.open_positions.items():
            if pos.symbol == symbol and pos.side == position_side:
                existing_pos_id = pid
                break
        
        position = None
        if existing_pos_id:
            # Fusionar con posición existente
            pos = self.open_positions[existing_pos_id]
            total_qty = pos.quantity + quantity
            # Precio promedio ponderado
            avg_price = ((pos.entry_price * pos.quantity) + (current_price * quantity)) / total_qty
            
            pos.entry_price = avg_price
            pos.quantity = total_qty
            pos.margin += margin
            pos.current_price = current_price # Actualizar precio actual
            # Mantener el strategy_case original o actualizar? Mantener original.
            # Mantener fib_high/fib_low originales
            
            # Registrar ejecución en historial
            exec_num = len(pos.executions) + 1
            pos.executions.append({
                "order_num": exec_num,
                "price": current_price,
                "quantity": quantity,
                "type": "MARKET",
                "time": datetime.now().isoformat()
            })
            
            print(f"🔄 Posición promediada: {symbol} Entrada ${avg_price:.4f} Qty {total_qty:.4f}")
            position = pos
        else:
            # Crear nueva posición
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
                executions=[{
                    "order_num": 1,
                    "price": current_price,
                    "quantity": quantity,
                    "type": "MARKET",
                    "time": datetime.now().isoformat()
                }]
            )
            self.open_positions[order_id] = position
            
        self._save_trades()
        
        # Log y notificación
        log_trade("OPEN", symbol, side.value, current_price, case=strategy_case)
        logger.info(f"Orden Mercado: {side.value} {symbol} @ ${current_price:.4f} | Margen: ${margin} | TP: ${take_profit:.4f}")
        
        # Notificación Telegram
        try:
            from telegram_bot import telegram_bot, AUTHORIZED_CHATS
            if AUTHORIZED_CHATS:
                asyncio.create_task(telegram_bot.send_trade_alert(
                    "OPEN", symbol, side.value, current_price, case=strategy_case
                ))
        except Exception:
            pass
        
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
        order.filled_at = datetime.now().isoformat()
        order.price = fill_price  # Precio real de ejecución
        
        # ===== TP DINÁMICO: Si esta orden está vinculada, actualizar TP de posición principal a 50% =====
        if order.linked_order_id:
            # Esta es una orden secundaria vinculada a una posición existente
            # Actualizar TP de la posición principal al nivel 50%
            if order.linked_order_id in self.open_positions:
                main_position = self.open_positions[order.linked_order_id]
                # Calcular nuevo TP (50% del rango)
                # Asumiendo SHORT: el 50% está más arriba que el 45%
                # Usamos la diferencia para calcular el nuevo nivel
                old_tp = main_position.take_profit
                # Mover TP de nivel 45% a nivel 50% (subir un 5% del rango)
                range_estimate = abs(main_position.entry_price - old_tp) / 0.05  # Estimación del rango
                new_tp = old_tp + (range_estimate * 0.05)  # Nuevo TP en 50%
                main_position.take_profit = new_tp
                print(f"🔄 TP DINÁMICO: {order.symbol} TP actualizado de ${old_tp:.4f} → ${new_tp:.4f}")
        
        # BUSCAR POSICIÓN EXISTENTE PARA FUSIONAR
        position_side = PositionSide.SHORT if order.side == OrderSide.SELL else PositionSide.LONG
        existing_pos_id = None
        
        # Primero intentar por vinculación explícita
        if order.linked_order_id and order.linked_order_id in self.open_positions:
            existing_pos_id = order.linked_order_id
        else:
            # Buscar por símbolo y lado
            for pid, pos in self.open_positions.items():
                if pos.symbol == order.symbol and pos.side == position_side:
                    existing_pos_id = pid
                    break
        
        if existing_pos_id:
            # FUSIONAR (Averaging)
            pos = self.open_positions[existing_pos_id]
            total_qty = pos.quantity + order.quantity
            # Nuevo precio promedio
            avg_price = ((pos.entry_price * pos.quantity) + (fill_price * order.quantity)) / total_qty
            
            print(f"🔄 FUSIONANDO ejecucion Limit en {order.symbol}:")
            print(f"   Antiguo: Entry ${pos.entry_price:.4f}, Qty {pos.quantity:.4f}")
            print(f"   Nuevo:   Entry ${avg_price:.4f}, Qty {total_qty:.4f}")
            
            pos.entry_price = avg_price
            pos.quantity = total_qty
            pos.margin += order.margin
            pos.current_price = fill_price
            
            # Registrar ejecución en historial
            exec_num = len(pos.executions) + 1
            pos.executions.append({
                "order_num": exec_num,
                "price": fill_price,
                "quantity": order.quantity,
                "type": "LIMIT",
                "time": datetime.now().isoformat()
            })
            
            # El TP ya se actualizó dinámicamente arriba si estaba vinculado
            
        else:
            # Crear nueva posición
            # Usar linked_order_id como ID si queremos que futuras órdenes se vinculen a esta? 
            # Mejor usar el ID de esta orden como key nueva
            
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
                executions=[{
                    "order_num": 1,
                    "price": fill_price,
                    "quantity": order.quantity,
                    "type": "LIMIT",
                    "time": datetime.now().isoformat()
                }]
            )
            
            # Guardar linked_order_id para referencias futuras
            if order.linked_order_id:
                position.linked_order_id = order.linked_order_id
            
            self.open_positions[order_id] = position
            
        self.update_max_simultaneous()  # Track máximo simultáneo
        self._save_trades()
        
        print(f"✅ Orden ejecutada: {order.side.value} {order.symbol} @ ${fill_price:.4f}")
    
    def check_positions(self, symbol: str, current_price: float):
        """Verificar TP/SL de posiciones abiertas"""
        positions_to_close = []
        
        for order_id, position in self.open_positions.items():
            if position.symbol != symbol:
                continue
            
            position.calculate_pnl(current_price)
            
            if position.check_take_profit(current_price):
                positions_to_close.append((order_id, "TP", current_price))
            elif position.check_stop_loss(current_price):
                positions_to_close.append((order_id, "SL", current_price))
        
        for order_id, reason, price in positions_to_close:
            self._close_position(order_id, price, reason)

    def check_pending_orders(self, symbol: str, current_price: float):
        """Verificar si se activan órdenes pendientes (Limit Orders)"""
        orders_to_fill = []

        for order_id, order in self.pending_orders.items():
            if order.symbol != symbol:
                continue
            
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
                orders_to_fill.append(order)
        
        # Ejecutar fuera del bucle para no modificar el diccionario mientras iteramos
        for order in orders_to_fill:
            print(f"⚡ Orden Límite ACTIVADA: {order.symbol} @ {current_price:.4f} (Limit: {order.price:.4f})")
            self._fill_order(order, current_price)
    
    def _close_position(self, order_id: str, close_price: float, reason: str):
        """Cerrar una posición"""
        if order_id not in self.open_positions:
            return
        
        position = self.open_positions.pop(order_id)
        pnl = position.calculate_pnl(close_price)
        
        # Actualizar balance
        self.balance += pnl
        
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
            "min_pnl": position.min_pnl,  # Max Drawdown
            "strategy_case": position.strategy_case,
            "reason": reason,
            "fib_high": position.fib_high,  # Nivel 100% (precio del High)
            "fib_low": position.fib_low,    # Nivel 0% (precio del Low)
            "stop_loss": position.stop_loss,
            "take_profit": position.take_profit,
            "executions": position.executions,  # Historial de ejecuciones (precio de cada orden)
            "closed_at": datetime.now().isoformat()
        }
        self.trade_history.append(trade_record)
        
        # Cancelar órdenes vinculadas si el TP se ejecutó
        if reason == "TP":
            self._cancel_linked_orders(order_id)
        
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
    
    def _cancel_linked_orders(self, closed_order_id: str):
        """Cancelar órdenes vinculadas cuando se cierra una posición"""
        orders_to_cancel = []
        
        for order_id, order in self.pending_orders.items():
            if order.linked_order_id == closed_order_id:
                orders_to_cancel.append(order_id)
        
        for order_id in orders_to_cancel:
            order = self.pending_orders.pop(order_id)
            order.status = OrderStatus.CANCELLED
            logger.info(f"Orden cancelada: {order.side.value} {order.symbol} @ ${order.price:.4f}")
    
    def cancel_order(self, order_id: str):
        """Cancelar una orden pendiente"""
        if order_id in self.pending_orders:
            order = self.pending_orders.pop(order_id)
            order.status = OrderStatus.CANCELLED
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
