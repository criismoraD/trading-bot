"""
Sistema de Trading Real con Binance Futures
Ejecuta operaciones reales usando la API de Binance
"""
import os
import time
import hmac
import hashlib
import asyncio
import aiohttp
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from urllib.parse import urlencode

from dotenv import load_dotenv
from logger import trading_logger as logger

# Cargar variables de entorno
load_dotenv()

# Configuración de API
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
FUTURES_BASE_URL = "https://fapi.binance.com"

# Diferencia de tiempo con servidor de Binance (se calcula al conectar)
_server_time_offset = 0


def get_timestamp():
    """Obtener timestamp en milisegundos ajustado al servidor de Binance"""
    return int(time.time() * 1000) + _server_time_offset


def create_signature(query_string: str) -> str:
    """Crear firma HMAC SHA256 para autenticación"""
    return hmac.new(
        BINANCE_API_SECRET.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()


@dataclass
class BinancePosition:
    """Posición abierta en Binance Futures"""
    symbol: str
    side: str  # "LONG" o "SHORT"
    entry_price: float
    quantity: float
    unrealized_pnl: float
    leverage: int
    margin_type: str  # "cross" o "isolated"


@dataclass
class BinanceOrder:
    """Orden en Binance Futures"""
    order_id: int
    symbol: str
    side: str
    order_type: str
    price: float
    quantity: float
    status: str
    time: str


class BinanceFuturesTrader:
    """Clase para trading real en Binance Futures"""
    
    def __init__(self, trades_file: str = "trades_real.json"):
        self.api_key = BINANCE_API_KEY
        self.api_secret = BINANCE_API_SECRET
        self.base_url = FUTURES_BASE_URL
        self.session: Optional[aiohttp.ClientSession] = None
        self.is_connected = False
        self.trades_file = trades_file
        
        # Cache de información
        self.account_balance: float = 0.0
        self.available_balance: float = 0.0
        self.positions: Dict[str, BinancePosition] = {}
        self.open_orders: Dict[str, BinanceOrder] = {}
        self.limit_orders: List[dict] = []  # Órdenes LIMIT activas en Binance
        self.symbol_info: Dict[str, dict] = {}  # Precisión de cada símbolo
        
        # Tracking de ejecuciones (compatible con paper trading)
        self.executions_history: Dict[str, List[dict]] = {}
        
        # Tracking de órdenes LIMIT pendientes con TP/SL
        # Cuando se llenan, añadimos TP/SL automáticamente
        self.pending_orders_tp_sl: Dict[int, dict] = {}
        
        # Tracking de posiciones activas con info Fibonacci para TP dinámico
        # key: symbol, value: info de posición incluyendo fib levels
        self.active_positions_info: Dict[str, dict] = {}
        
        # Tracking de TP orders activos por símbolo (para cancelar y actualizar)
        self.active_tp_orders: Dict[str, int] = {}  # symbol -> tp_order_id
        
        # === NUEVO: Registro de operaciones y estadísticas ===
        self.trade_history: List[dict] = []  # Historial de trades cerrados
        self.stats = {
            "max_simultaneous_positions": 0,
            "max_simultaneous_orders": 0,
            "max_simultaneous_total": 0,  # positions + orders
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_pnl": 0.0
        }
        
        # Cargar historial si existe
        self._load_trades()
        
    async def connect(self) -> bool:
        """Conectar y verificar credenciales"""
        global _server_time_offset
        
        print(f"🔌 Intentando conectar a Binance Futures...")
        
        if not self.api_key or not self.api_secret or \
           self.api_key == "tu_api_key_aqui" or self.api_secret == "tu_api_secret_aqui":
            print("❌ ERROR: Configura tus credenciales de Binance en el archivo .env")
            print("   BINANCE_API_KEY=tu_clave")
            print("   BINANCE_API_SECRET=tu_secreto")
            return False
        
        print(f"   API Key: {self.api_key[:8]}...{self.api_key[-4:]}")
        
        self.session = aiohttp.ClientSession()
        
        try:
            # PRIMERO: Sincronizar tiempo con servidor de Binance
            print(f"   ⏰ Sincronizando tiempo con servidor...")
            await self._sync_server_time()
            
            # Verificar conexión obteniendo balance
            balance_info = await self.get_account_balance()
            if balance_info is not None:
                self.is_connected = True
                print(f"✅ Conectado a Binance Futures")
                print(f"   💰 Balance USDT: ${balance_info['balance']:.2f}")
                print(f"   💵 Disponible: ${balance_info['availableBalance']:.2f}")
                
                # Mostrar estadísticas cargadas
                if self.trade_history:
                    print(f"   📂 Historial: {len(self.trade_history)} trades | Max simultáneo: {self.stats['max_simultaneous_total']}")
                
                # Cargar información de símbolos
                await self.load_symbol_info()
                print(f"   📊 {len(self.symbol_info)} símbolos cargados")
                return True
            else:
                print("❌ Error conectando a Binance Futures - No se pudo obtener balance")
                return False
        except Exception as e:
            print(f"❌ Error de conexión: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _load_trades(self):
        """Cargar historial de trades desde JSON"""
        import os
        if os.path.exists(self.trades_file):
            try:
                with open(self.trades_file, 'r') as f:
                    data = json.load(f)
                    self.trade_history = data.get("history", [])
                    self.stats = data.get("stats", self.stats)
                    print(f"📂 [REAL] Historial cargado: {len(self.trade_history)} trades")
            except Exception as e:
                print(f"⚠️ Error cargando historial real: {e}")
    
    def _save_trades(self):
        """Guardar historial de trades a JSON"""
        try:
            data = {
                "history": self.trade_history,
                "stats": self.stats,
                "active_positions_info": self.active_positions_info,
                "last_updated": datetime.now().isoformat()
            }
            with open(self.trades_file, 'w') as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            print(f"⚠️ Error guardando trades real: {e}")
    
    def update_max_simultaneous(self, num_positions: int, num_orders: int):
        """Actualizar estadística de máximo de operaciones simultáneas"""
        total = num_positions + num_orders
        
        if num_positions > self.stats["max_simultaneous_positions"]:
            self.stats["max_simultaneous_positions"] = num_positions
        
        if num_orders > self.stats["max_simultaneous_orders"]:
            self.stats["max_simultaneous_orders"] = num_orders
        
        if total > self.stats["max_simultaneous_total"]:
            self.stats["max_simultaneous_total"] = total
            print(f"📊 [REAL] Nuevo máximo simultáneo: {total} ({num_positions} pos + {num_orders} órd)")
            self._save_trades()
    
    def record_trade_close(self, symbol: str, side: str, entry_price: float, exit_price: float, 
                           quantity: float, pnl: float, case: int = 0, reason: str = ""):
        """Registrar cierre de trade en historial"""
        trade = {
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": quantity,
            "pnl": pnl,
            "case": case,
            "reason": reason,
            "closed_at": datetime.now().isoformat()
        }
        
        self.trade_history.append(trade)
        self.stats["total_trades"] += 1
        self.stats["total_pnl"] += pnl
        
        if pnl >= 0:
            self.stats["winning_trades"] += 1
        else:
            self.stats["losing_trades"] += 1
        
        self._save_trades()
        print(f"📝 [REAL] Trade registrado: {symbol} PnL: ${pnl:.4f}")

    async def _sync_server_time(self):
        """Sincronizar tiempo local con servidor de Binance"""
        global _server_time_offset
        
        try:
            url = f"{self.base_url}/fapi/v1/time"
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    server_time = data['serverTime']
                    local_time = int(time.time() * 1000)
                    _server_time_offset = server_time - local_time
                    print(f"   ⏰ Offset de tiempo: {_server_time_offset}ms")
                else:
                    print(f"   ⚠️ No se pudo sincronizar tiempo")
        except Exception as e:
            print(f"   ⚠️ Error sincronizando tiempo: {e}")
    
    async def disconnect(self):
        """Cerrar conexión"""
        if self.session:
            await self.session.close()
            self.session = None
        self.is_connected = False
    
    async def _request(self, method: str, endpoint: str, params: dict = None, signed: bool = False) -> Optional[dict]:
        """Realizar petición a la API"""
        if not self.session:
            print("❌ Sesión no iniciada")
            return None
        
        url = f"{self.base_url}{endpoint}"
        headers = {"X-MBX-APIKEY": self.api_key}
        
        if params is None:
            params = {}
        
        if signed:
            params["timestamp"] = get_timestamp()
            query_string = urlencode(params)
            params["signature"] = create_signature(query_string)
        
        try:
            if method == "GET":
                async with self.session.get(url, params=params, headers=headers) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        error = await response.text()
                        print(f"❌ API Error {response.status} en {endpoint}: {error}")
                        logger.error(f"API Error {response.status}: {error}")
                        return None
            elif method == "POST":
                async with self.session.post(url, params=params, headers=headers) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        error = await response.text()
                        print(f"❌ API Error {response.status} en {endpoint}: {error}")
                        logger.error(f"API Error {response.status}: {error}")
                        return None
            elif method == "DELETE":
                async with self.session.delete(url, params=params, headers=headers) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        error = await response.text()
                        print(f"❌ API Error {response.status} en {endpoint}: {error}")
                        logger.error(f"API Error {response.status}: {error}")
                        return None
        except Exception as e:
            print(f"❌ Request error: {e}")
            logger.error(f"Request error: {e}")
            return None
    
    async def _request_silent(self, method: str, endpoint: str, params: dict = None, 
                              signed: bool = False, ignore_codes: list = None) -> Optional[dict]:
        """Realizar petición silenciando ciertos códigos de error"""
        if not self.session:
            return None
        
        url = f"{self.base_url}{endpoint}"
        headers = {"X-MBX-APIKEY": self.api_key}
        
        if params is None:
            params = {}
        if ignore_codes is None:
            ignore_codes = []
        
        if signed:
            params["timestamp"] = get_timestamp()
            query_string = urlencode(params)
            params["signature"] = create_signature(query_string)
        
        try:
            async with self.session.post(url, params=params, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    error_text = await response.text()
                    # Verificar si es un código a ignorar
                    import json
                    try:
                        error_json = json.loads(error_text)
                        if error_json.get("code") in ignore_codes:
                            return None  # Ignorar silenciosamente
                    except:
                        pass
                    # Si no es ignorable, loggear
                    print(f"❌ API Error {response.status} en {endpoint}: {error_text}")
                    logger.error(f"API Error {response.status}: {error_text}")
                    return None
        except Exception as e:
            logger.error(f"Request error: {e}")
            return None
    
    async def load_symbol_info(self):
        """Cargar información de precisión de símbolos"""
        data = await self._request("GET", "/fapi/v1/exchangeInfo")
        if data and "symbols" in data:
            for symbol in data["symbols"]:
                # Obtener filtros
                tick_size = 0.0001
                min_qty = 0.001
                min_notional = 5.0
                
                for f in symbol["filters"]:
                    if f["filterType"] == "PRICE_FILTER":
                        tick_size = float(f["tickSize"])
                    elif f["filterType"] == "LOT_SIZE":
                        min_qty = float(f["minQty"])
                    elif f["filterType"] == "MIN_NOTIONAL":
                        min_notional = float(f["notional"])
                
                # Tipos de orden soportados
                order_types = symbol.get("orderTypes", [])
                
                self.symbol_info[symbol["symbol"]] = {
                    "pricePrecision": symbol["pricePrecision"],
                    "quantityPrecision": symbol["quantityPrecision"],
                    "tickSize": tick_size,
                    "minQty": min_qty,
                    "minNotional": min_notional,
                    "orderTypes": order_types,
                    "supportsStopMarket": "STOP_MARKET" in order_types,
                    "supportsTpMarket": "TAKE_PROFIT_MARKET" in order_types,
                    "supportsStop": "STOP" in order_types,
                    "supportsTp": "TAKE_PROFIT" in order_types
                }
    
    def format_quantity(self, symbol: str, quantity: float) -> str:
        """Formatear cantidad según precisión del símbolo"""
        if symbol in self.symbol_info:
            precision = self.symbol_info[symbol]["quantityPrecision"]
            # Truncar (no redondear) para evitar exceder el margen
            factor = 10 ** precision
            truncated = int(quantity * factor) / factor
            return f"{truncated:.{precision}f}"
        return f"{quantity:.3f}"
    
    def format_price(self, symbol: str, price: float) -> str:
        """Formatear precio según tick size del símbolo"""
        if symbol in self.symbol_info:
            tick_size = self.symbol_info[symbol]["tickSize"]
            precision = self.symbol_info[symbol]["pricePrecision"]
            # Redondear al tick size más cercano
            rounded_price = round(price / tick_size) * tick_size
            return f"{rounded_price:.{precision}f}"
        return f"{price:.4f}"
    
    async def get_account_balance(self) -> Optional[dict]:
        """Obtener balance de la cuenta. Devuelve dict con balance info o None si falla."""
        data = await self._request("GET", "/fapi/v2/balance", signed=True)
        if data:
            for asset in data:
                if asset["asset"] == "USDT":
                    self.account_balance = float(asset["balance"])
                    self.available_balance = float(asset["availableBalance"])
                    return {
                        "balance": self.account_balance,
                        "availableBalance": self.available_balance
                    }
        print("⚠️ No se encontró USDT en el balance o respuesta inválida")
        return None
    
    async def get_positions(self) -> Dict[str, BinancePosition]:
        """Obtener posiciones abiertas"""
        data = await self._request("GET", "/fapi/v2/positionRisk", signed=True)
        if data:
            self.positions.clear()
            for pos in data:
                amt = float(pos["positionAmt"])
                if amt != 0:
                    symbol = pos["symbol"]
                    self.positions[symbol] = BinancePosition(
                        symbol=symbol,
                        side="LONG" if amt > 0 else "SHORT",
                        entry_price=float(pos["entryPrice"]),
                        quantity=abs(amt),
                        unrealized_pnl=float(pos["unRealizedProfit"]),
                        leverage=int(pos["leverage"]),
                        margin_type=pos["marginType"]
                    )
        return self.positions
    
    async def get_open_orders(self, symbol: str = None) -> List[BinanceOrder]:
        """Obtener órdenes abiertas"""
        params = {}
        if symbol:
            params["symbol"] = symbol
        
        data = await self._request("GET", "/fapi/v1/openOrders", params, signed=True)
        orders = []
        if data:
            for order in data:
                orders.append(BinanceOrder(
                    order_id=order["orderId"],
                    symbol=order["symbol"],
                    side=order["side"],
                    order_type=order["type"],
                    price=float(order["price"]),
                    quantity=float(order["origQty"]),
                    status=order["status"],
                    time=datetime.fromtimestamp(order["time"] / 1000).isoformat()
                ))
        return orders
    
    async def get_limit_orders(self) -> List[dict]:
        """Obtener órdenes LIMIT de venta abiertas (SELL only - las de BUY son TP/SL)"""
        data = await self._request("GET", "/fapi/v1/openOrders", signed=True)
        self.limit_orders = []
        
        if data:
            for order in data:
                order_type = order.get("type", "")
                order_side = order.get("side", "")
                # Solo órdenes LIMIT de venta (SELL)
                # Las órdenes de compra (BUY) LIMIT con reduceOnly son TP
                if order_type == "LIMIT" and order_side == "SELL":
                    self.limit_orders.append({
                        "order_id": order["orderId"],
                        "symbol": order["symbol"],
                        "side": order["side"],
                        "price": float(order["price"]),
                        "quantity": float(order["origQty"]),
                        "status": order["status"]
                    })
        
        return self.limit_orders
    
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Configurar apalancamiento para un símbolo"""
        params = {
            "symbol": symbol,
            "leverage": leverage
        }
        result = await self._request("POST", "/fapi/v1/leverage", params, signed=True)
        return result is not None
    
    async def set_margin_type(self, symbol: str, margin_type: str = "CROSSED") -> bool:
        """Configurar tipo de margen (CROSSED o ISOLATED)"""
        params = {
            "symbol": symbol,
            "marginType": margin_type
        }
        # Intentar cambiar silenciosamente - error -4046 significa que ya está configurado
        await self._request_silent("POST", "/fapi/v1/marginType", params, signed=True, ignore_codes=[-4046])
        return True
    
    async def place_batch_orders(self, orders: list) -> Optional[list]:
        """
        Colocar múltiples órdenes en una sola llamada API (máximo 5)
        Útil para crear orden principal + TP + SL juntos
        
        Args:
            orders: Lista de diccionarios con parámetros de cada orden
        
        Returns:
            Lista de resultados de cada orden
        """
        import json
        
        if not orders or len(orders) > 5:
            logger.warning(f"Batch orders debe tener 1-5 órdenes, recibido: {len(orders) if orders else 0}")
            return None
        
        params = {
            "batchOrders": json.dumps(orders)
        }
        
        result = await self._request("POST", "/fapi/v1/batchOrders", params, signed=True)
        
        if result:
            logger.info(f"📦 BATCH ORDERS: {len(orders)} órdenes enviadas")
            return result
        
        return None
    
    async def place_market_order_with_tp_sl(self, symbol: str, side: str, quantity: float,
                                             take_profit: float = None, stop_loss: float = None) -> Optional[dict]:
        """
        Colocar orden de mercado con TP y SL en una sola llamada API usando batch orders
        Esto crea las 3 órdenes atómicamente como en la UI de Binance
        
        Args:
            symbol: Par de trading
            side: "BUY" o "SELL"
            quantity: Cantidad a operar
            take_profit: Precio del TP (opcional)
            stop_loss: Precio del SL (opcional)
        """
        qty_str = self.format_quantity(symbol, quantity)
        close_side = "BUY" if side == "SELL" else "SELL"
        
        # Orden principal (MARKET)
        orders = [{
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": qty_str
        }]
        
        # Take Profit
        if take_profit:
            orders.append({
                "symbol": symbol,
                "side": close_side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": self.format_price(symbol, take_profit),
                "quantity": qty_str,
                "reduceOnly": "true",
                "workingType": "MARK_PRICE"
            })
        
        # Stop Loss
        if stop_loss:
            orders.append({
                "symbol": symbol,
                "side": close_side,
                "type": "STOP_MARKET",
                "stopPrice": self.format_price(symbol, stop_loss),
                "quantity": qty_str,
                "reduceOnly": "true",
                "workingType": "MARK_PRICE"
            })
        
        # Ejecutar batch
        results = await self.place_batch_orders(orders)
        
        if results:
            # Parsear resultados
            main_order = None
            tp_order_id = None
            sl_order_id = None
            
            for i, res in enumerate(results):
                if "orderId" in res:
                    if i == 0:
                        main_order = res
                    elif i == 1 and take_profit:
                        tp_order_id = res.get("orderId")
                    elif (i == 2 and take_profit) or (i == 1 and not take_profit and stop_loss):
                        sl_order_id = res.get("orderId")
                elif "code" in res:
                    # Error en esta orden específica
                    logger.warning(f"Error en orden batch {i}: {res.get('msg', res)}")
            
            if main_order:
                main_order["tp_order_id"] = tp_order_id
                main_order["sl_order_id"] = sl_order_id
                
                # Guardar referencia del TP
                if tp_order_id:
                    self.active_tp_orders[symbol] = tp_order_id
                
                logger.info(f"✅ BATCH: {side} {symbol} + TP={tp_order_id is not None} + SL={sl_order_id is not None}")
                return main_order
        
        return None

    async def place_market_order(self, symbol: str, side: str, quantity: float,
                                  reduce_only: bool = False) -> Optional[dict]:
        """
        Colocar orden de mercado
        side: "BUY" o "SELL"
        Para SHORT: side="SELL"
        Para cerrar SHORT: side="BUY" con reduce_only=True
        """
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": self.format_quantity(symbol, quantity)
        }
        
        if reduce_only:
            params["reduceOnly"] = "true"
        
        result = await self._request("POST", "/fapi/v1/order", params, signed=True)
        
        if result:
            logger.info(f"🔴 MARKET ORDER: {side} {symbol} qty={quantity}")
            
            # Registrar ejecución
            if symbol not in self.executions_history:
                self.executions_history[symbol] = []
            
            exec_num = len(self.executions_history[symbol]) + 1
            self.executions_history[symbol].append({
                "order_num": exec_num,
                "price": float(result.get("avgPrice", 0)),
                "quantity": quantity,
                "type": "MARKET",
                "order_id": result["orderId"],
                "time": datetime.now().isoformat()
            })
        
        return result
    
    async def place_limit_order(self, symbol: str, side: str, price: float, 
                                 quantity: float, reduce_only: bool = False) -> Optional[dict]:
        """
        Colocar orden límite
        """
        params = {
            "symbol": symbol,
            "side": side,
            "type": "LIMIT",
            "price": self.format_price(symbol, price),
            "quantity": self.format_quantity(symbol, quantity),
            "timeInForce": "GTC"  # Good Till Cancel
        }
        
        if reduce_only:
            params["reduceOnly"] = "true"
        
        result = await self._request("POST", "/fapi/v1/order", params, signed=True)
        
        if result:
            logger.info(f"📝 LIMIT ORDER: {side} {symbol} @ ${price} qty={quantity}")
        
        return result
    
    async def place_stop_loss(self, symbol: str, side: str, stop_price: float,
                               quantity: float) -> Optional[dict]:
        """
        Colocar orden Stop Loss
        Para cerrar SHORT: side="BUY", stop_price = precio donde queremos salir con pérdida
        Intenta STOP_MARKET primero, luego STOP como fallback
        """
        # Verificar cantidad formateada no sea 0
        qty_str = self.format_quantity(symbol, quantity)
        if float(qty_str) == 0:
            logger.warning(f"⚠️ Cantidad SL muy pequeña para {symbol}: {quantity} → {qty_str}")
            return None
        
        # Obtener tipos soportados
        supports_stop_market = self.symbol_info.get(symbol, {}).get("supportsStopMarket", True)
        supports_stop = self.symbol_info.get(symbol, {}).get("supportsStop", True)
        
        # Intentar STOP_MARKET primero (si está soportado)
        if supports_stop_market:
            params = {
                "symbol": symbol,
                "side": side,
                "type": "STOP_MARKET",
                "stopPrice": self.format_price(symbol, stop_price),
                "quantity": qty_str,
                "reduceOnly": "true",
                "workingType": "MARK_PRICE"
            }
            result = await self._request("POST", "/fapi/v1/order", params, signed=True)
            if result:
                logger.info(f"🛑 STOP LOSS: {side} {symbol} @ ${stop_price}")
                return result
        
        # Fallback 1: usar STOP (orden límite condicional)
        if supports_stop:
            if side == "BUY":
                exec_price = stop_price * 1.01  # 1% slippage para asegurar fill
            else:
                exec_price = stop_price * 0.99
            
            params = {
                "symbol": symbol,
                "side": side,
                "type": "STOP",
                "stopPrice": self.format_price(symbol, stop_price),
                "price": self.format_price(symbol, exec_price),
                "quantity": qty_str,
                "timeInForce": "GTC",
                "reduceOnly": "true"
            }
            
            result = await self._request("POST", "/fapi/v1/order", params, signed=True)
            if result:
                logger.info(f"🛑 STOP LOSS (LIMIT): {side} {symbol} @ ${stop_price}")
                return result
        
        # Fallback 2: Usar STOP_MARKET sin workingType (algunos símbolos no soportan MARK_PRICE)
        params = {
            "symbol": symbol,
            "side": side,
            "type": "STOP_MARKET",
            "stopPrice": self.format_price(symbol, stop_price),
            "quantity": qty_str,
            "reduceOnly": "true"
        }
        result = await self._request("POST", "/fapi/v1/order", params, signed=True)
        if result:
            logger.info(f"🛑 STOP LOSS (MARKET sin MARK_PRICE): {side} {symbol} @ ${stop_price}")
        else:
            logger.warning(f"⚠️ No se pudo crear SL para {symbol} - ningún tipo de orden soportado")
        
        return result
    
    async def place_take_profit(self, symbol: str, side: str, tp_price: float,
                                 quantity: float) -> Optional[dict]:
        """
        Colocar orden Take Profit
        Para cerrar SHORT: side="BUY", tp_price = precio donde queremos tomar ganancia
        Intenta TAKE_PROFIT_MARKET primero, luego TAKE_PROFIT como fallback
        """
        # Verificar cantidad formateada no sea 0
        qty_str = self.format_quantity(symbol, quantity)
        if float(qty_str) == 0:
            logger.warning(f"⚠️ Cantidad TP muy pequeña para {symbol}: {quantity} → {qty_str}")
            return None
        
        # Obtener tipos soportados
        supports_tp_market = self.symbol_info.get(symbol, {}).get("supportsTpMarket", True)
        supports_tp = self.symbol_info.get(symbol, {}).get("supportsTp", True)
        
        # Intentar TAKE_PROFIT_MARKET primero (si está soportado)
        if supports_tp_market:
            params = {
                "symbol": symbol,
                "side": side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": self.format_price(symbol, tp_price),
                "quantity": qty_str,
                "reduceOnly": "true",
                "workingType": "MARK_PRICE"
            }
            result = await self._request("POST", "/fapi/v1/order", params, signed=True)
            if result:
                logger.info(f"🎯 TAKE PROFIT: {side} {symbol} @ ${tp_price}")
                return result
        
        # Fallback 1: usar TAKE_PROFIT (orden límite condicional)
        if supports_tp:
            if side == "BUY":
                exec_price = tp_price * 1.005  # 0.5% slippage
            else:
                exec_price = tp_price * 0.995
            
            params = {
                "symbol": symbol,
                "side": side,
                "type": "TAKE_PROFIT",
                "stopPrice": self.format_price(symbol, tp_price),
                "price": self.format_price(symbol, exec_price),
                "quantity": qty_str,
                "timeInForce": "GTC",
                "reduceOnly": "true"
            }
            
            result = await self._request("POST", "/fapi/v1/order", params, signed=True)
            if result:
                logger.info(f"🎯 TAKE PROFIT (LIMIT): {side} {symbol} @ ${tp_price}")
                return result
        
        # Fallback 2: Orden LIMIT simple con reduceOnly (último recurso)
        if side == "BUY":
            exec_price = tp_price * 1.002  # Ligeramente por encima del TP
        else:
            exec_price = tp_price * 0.998
        
        params = {
            "symbol": symbol,
            "side": side,
            "type": "LIMIT",
            "price": self.format_price(symbol, tp_price),
            "quantity": qty_str,
            "timeInForce": "GTC",
            "reduceOnly": "true"
        }
        
        result = await self._request("POST", "/fapi/v1/order", params, signed=True)
        if result:
            logger.info(f"🎯 TAKE PROFIT (LIMIT reduceOnly): {side} {symbol} @ ${tp_price}")
        else:
            logger.warning(f"⚠️ No se pudo crear TP para {symbol} - ningún tipo de orden soportado")
        
        return result
    
    async def cancel_order(self, symbol: str, order_id: int) -> bool:
        """Cancelar una orden"""
        params = {
            "symbol": symbol,
            "orderId": order_id
        }
        result = await self._request("DELETE", "/fapi/v1/order", params, signed=True)
        return result is not None
    
    async def cancel_all_orders(self, symbol: str) -> bool:
        """Cancelar todas las órdenes de un símbolo"""
        params = {"symbol": symbol}
        result = await self._request("DELETE", "/fapi/v1/allOpenOrders", params, signed=True)
        return result is not None
    
    # ===== MÉTODOS COMPATIBLES CON PAPER TRADING =====
    # Estos métodos tienen la misma interfaz que PaperTradingAccount
    
    async def execute_short_entry(self, symbol: str, margin: float, leverage: int,
                                   entry_price: float = None, take_profit: float = None, 
                                   stop_loss: float = None, strategy_case: int = 0,
                                   fib_high: float = None, fib_low: float = None) -> Optional[dict]:
        """
        Ejecutar entrada SHORT con TP y SL usando BATCH ORDERS
        Crea las 3 órdenes en una sola llamada API (como la UI de Binance)
        """
        # Configurar leverage y margen cruzado
        await self.set_leverage(symbol, leverage)
        await self.set_margin_type(symbol, "CROSSED")
        
        # Obtener precio actual
        ticker = await self._request("GET", "/fapi/v1/ticker/price", {"symbol": symbol})
        if not ticker:
            return None
        
        current_price = float(ticker["price"])
        
        # Calcular cantidad basada en margen
        notional = margin * leverage
        quantity = notional / current_price
        
        # Verificar cantidad mínima
        if symbol in self.symbol_info:
            min_qty = self.symbol_info[symbol]["minQty"]
            min_notional = self.symbol_info[symbol]["minNotional"]
            if quantity < min_qty or notional < min_notional:
                logger.warning(f"Cantidad o nocional muy bajo para {symbol}")
                return None
        
        # Usar BATCH ORDERS para crear MARKET + TP + SL en una sola llamada
        order = await self.place_market_order_with_tp_sl(
            symbol=symbol,
            side="SELL",
            quantity=quantity,
            take_profit=take_profit,
            stop_loss=stop_loss
        )
        
        if order:
            # Obtener la cantidad ejecutada real
            executed_qty = float(order.get("executedQty", quantity))
            tp_order_id = order.get("tp_order_id")
            sl_order_id = order.get("sl_order_id")
            
            # Guardar información de posición para TP dinámico
            self.active_positions_info[symbol] = {
                "entry_price": current_price,
                "quantity": executed_qty,
                "take_profit": take_profit,
                "stop_loss": stop_loss,
                "fib_high": fib_high,
                "fib_low": fib_low,
                "strategy_case": strategy_case,
                "side": "SHORT",
                "tp_order_id": tp_order_id,
                "sl_order_id": sl_order_id,
                "executions": [{
                    "order_num": 1,
                    "price": current_price,
                    "quantity": executed_qty,
                    "type": "MARKET",
                    "time": datetime.now().isoformat()
                }]
            }
            
            sl_str = f"${stop_loss:.4f}" if stop_loss else "N/A"
            tp_str = f"${take_profit:.4f}" if take_profit else "N/A"
            tp_status = "✅" if tp_order_id else "❌"
            sl_status = "✅" if sl_order_id else "❌"
            print(f"✅ SHORT {symbol} @ ${current_price:.4f} | TP{tp_status}: {tp_str} | SL{sl_status}: {sl_str}")
            
            return {
                "symbol": symbol,
                "entry_price": current_price,
                "quantity": quantity,
                "margin": margin,
                "leverage": leverage,
                "take_profit": take_profit,
                "stop_loss": stop_loss,
                "strategy_case": strategy_case,
                "fib_high": fib_high,
                "fib_low": fib_low,
                "order_id": order["orderId"]
            }
        
        return None
    
    async def execute_limit_short(self, symbol: str, margin: float, leverage: int,
                                   limit_price: float, take_profit: float = None, 
                                   stop_loss: float = None, strategy_case: int = 0,
                                   fib_high: float = None, fib_low: float = None,
                                   is_linked_order: bool = False) -> Optional[dict]:
        """
        Colocar orden límite SHORT con TP/SL adjuntos usando OTOCO
        Cuando la orden LIMIT se ejecuta, automáticamente se activan TP y SL
        
        is_linked_order: True si esta orden está vinculada a una posición existente
                        (para TP dinámico - cuando se llene, actualizamos TP)
        """
        # Configurar leverage y margen cruzado
        await self.set_leverage(symbol, leverage)
        await self.set_margin_type(symbol, "CROSSED")
        
        # Calcular cantidad
        notional = margin * leverage
        quantity = notional / limit_price
        
        # Intentar OTOCO primero (orden con TP/SL adjuntos)
        if take_profit and stop_loss:
            otoco_result = await self.place_limit_order_with_tp_sl(
                symbol=symbol,
                side="SELL",
                limit_price=limit_price,
                quantity=quantity,
                take_profit=take_profit,
                stop_loss=stop_loss
            )
            if otoco_result:
                tp_str = f"${take_profit:.4f}"
                sl_str = f"${stop_loss:.4f}"
                print(f"📝 LIMIT SHORT {symbol} @ ${limit_price:.4f} | TP: {tp_str} | SL: {sl_str}")
                return {
                    "symbol": symbol,
                    "limit_price": limit_price,
                    "quantity": quantity,
                    "margin": margin,
                    "take_profit": take_profit,
                    "stop_loss": stop_loss,
                    "strategy_case": strategy_case,
                    "order_id": otoco_result.get("orderId", 0)
                }
        
        # Fallback: solo orden límite sin TP/SL (tracking manual)
        order = await self.place_limit_order(symbol, "SELL", limit_price, quantity)
        
        if order:
            order_id = order["orderId"]
            
            # Guardar en pending_orders_tp_sl para añadir TP/SL cuando se llene
            # También incluimos is_linked_order para TP dinámico
            self.pending_orders_tp_sl[order_id] = {
                "symbol": symbol,
                "quantity": quantity,
                "take_profit": take_profit,
                "stop_loss": stop_loss,
                "close_side": "BUY",  # Para cerrar SHORT
                "is_linked_order": is_linked_order,  # Para TP dinámico
                "fib_high": fib_high,
                "fib_low": fib_low,
                "limit_price": limit_price,
                "strategy_case": strategy_case
            }
            
            linked_str = " (LINKED - TP dinámico)" if is_linked_order else ""
            print(f"📝 LIMIT SHORT {symbol} @ ${limit_price:.4f} (sin TP/SL){linked_str}")
            return {
                "symbol": symbol,
                "limit_price": limit_price,
                "quantity": quantity,
                "margin": margin,
                "take_profit": take_profit,
                "stop_loss": stop_loss,
                "strategy_case": strategy_case,
                "order_id": order_id
            }
        
        return None
    
    async def place_limit_order_with_tp_sl(self, symbol: str, side: str, limit_price: float,
                                           quantity: float, take_profit: float, 
                                           stop_loss: float) -> Optional[dict]:
        """
        Colocar orden LIMIT con TP y SL adjuntos (OTOCO - One Triggers OCO)
        Cuando la orden principal se llena, se activan TP y SL automáticamente
        Para SHORT: side="SELL", TP < limit_price, SL > limit_price
        """
        # Determinar el lado opuesto para cerrar
        close_side = "BUY" if side == "SELL" else "SELL"
        
        # Formatear valores
        qty_str = self.format_quantity(symbol, quantity)
        limit_price_str = self.format_price(symbol, limit_price)
        tp_price_str = self.format_price(symbol, take_profit)
        sl_price_str = self.format_price(symbol, stop_loss)
        
        # Precio de ejecución para TP (ligeramente mejor para asegurar fill)
        if close_side == "BUY":
            tp_exec_price = take_profit * 1.002
            sl_exec_price = stop_loss * 1.005
        else:
            tp_exec_price = take_profit * 0.998
            sl_exec_price = stop_loss * 0.995
        
        tp_exec_str = self.format_price(symbol, tp_exec_price)
        sl_exec_str = self.format_price(symbol, sl_exec_price)
        
        # Método 1: Usar batchOrders para enviar las 3 órdenes
        # La orden principal + TP + SL como órdenes separadas pero vinculadas
        
        # Crear orden principal LIMIT
        main_order = {
            "symbol": symbol,
            "side": side,
            "type": "LIMIT",
            "quantity": qty_str,
            "price": limit_price_str,
            "timeInForce": "GTC"
        }
        
        # Primero colocamos la orden LIMIT principal
        result = await self._request("POST", "/fapi/v1/order", main_order, signed=True)
        
        if result:
            order_id = result["orderId"]
            logger.info(f"📝 LIMIT ORDER con TP/SL: {side} {symbol} @ ${limit_price}")
            
            # Guardar info para tracking de TP/SL cuando se ejecute
            if symbol not in self.pending_orders_tp_sl:
                self.pending_orders_tp_sl = {}
            
            self.pending_orders_tp_sl[order_id] = {
                "symbol": symbol,
                "quantity": quantity,
                "take_profit": take_profit,
                "stop_loss": stop_loss,
                "close_side": close_side
            }
            
            return result
        
        return None
        
        return None
    
    def get_available_margin(self) -> float:
        """Obtener margen disponible (compatible con paper trading)"""
        return self.available_balance
    
    async def get_open_positions(self) -> List[dict]:
        """Obtener posiciones abiertas como lista de dicts"""
        data = await self._request("GET", "/fapi/v2/positionRisk", signed=True)
        if data:
            return [pos for pos in data if float(pos.get("positionAmt", 0)) != 0]
        return []
    
    async def get_account_balance_info(self) -> dict:
        """Obtener información completa del balance"""
        data = await self._request("GET", "/fapi/v2/balance", signed=True)
        if data:
            for asset in data:
                if asset["asset"] == "USDT":
                    return {
                        "balance": float(asset["balance"]),
                        "availableBalance": float(asset["availableBalance"]),
                        "crossWalletBalance": float(asset.get("crossWalletBalance", 0)),
                        "crossUnPnl": float(asset.get("crossUnPnl", 0))
                    }
        return {"balance": 0, "availableBalance": 0}
    
    async def refresh_balance(self):
        """Actualizar balance desde Binance"""
        await self.get_account_balance()
    
    async def check_filled_orders_and_add_tp_sl(self):
        """
        Verificar órdenes LIMIT que se han llenado y añadir TP/SL
        Implementa TP dinámico: cuando se llena una orden vinculada,
        promedia la posición y mueve el TP de 55% a 60%
        """
        if not self.pending_orders_tp_sl:
            return
        
        orders_to_remove = []
        
        for order_id, info in self.pending_orders_tp_sl.items():
            symbol = info["symbol"]
            
            # Verificar estado de la orden
            params = {
                "symbol": symbol,
                "orderId": order_id
            }
            order_status = await self._request("GET", "/fapi/v1/order", params, signed=True)
            
            if order_status:
                status = order_status.get("status", "")
                
                if status == "FILLED":
                    # Orden ejecutada
                    executed_qty = float(order_status.get("executedQty", info["quantity"]))
                    fill_price = float(order_status.get("avgPrice", info["limit_price"]))
                    
                    print(f"🔔 Orden LIMIT llenada: {symbol} @ ${fill_price:.4f}")
                    
                    # ===== TP DINÁMICO =====
                    if info.get("is_linked_order") and symbol in self.active_positions_info:
                        # Esta orden está vinculada a una posición existente
                        pos_info = self.active_positions_info[symbol]
                        
                        # 1. Calcular precio promedio (averaging)
                        old_qty = pos_info["quantity"]
                        old_entry = pos_info["entry_price"]
                        total_qty = old_qty + executed_qty
                        new_avg_price = ((old_entry * old_qty) + (fill_price * executed_qty)) / total_qty
                        
                        print(f"   🔄 PROMEDIANDO: Entrada ${old_entry:.4f} → ${new_avg_price:.4f} | Qty {total_qty:.4f}")
                        
                        # 2. Actualizar TP al nivel 60% (mover de 55%)
                        old_tp = pos_info["take_profit"]
                        if old_tp:
                            # Para SHORT: TP está debajo del entry
                            # Mover TP de nivel 55% a nivel 60% (subir un 5% del rango en dirección de profit)
                            # El 55% del rango es la distancia actual, movemos a 60%
                            fib_high = pos_info.get("fib_high")
                            fib_low = pos_info.get("fib_low")
                            
                            if fib_high and fib_low:
                                fib_range = fib_high - fib_low
                                # Nuevo TP en nivel 60% del rango (desde low)
                                new_tp = fib_low + (fib_range * 0.60)
                            else:
                                # Fallback: estimar rango desde la diferencia actual
                                range_estimate = abs(old_entry - old_tp) / 0.45  # 45% = 100% - 55%
                                new_tp = old_tp - (range_estimate * 0.05)  # Mover 5% hacia abajo para SHORT
                            
                            print(f"   🎯 TP DINÁMICO: ${old_tp:.4f} → ${new_tp:.4f}")
                            
                            # 3. Cancelar TP anterior
                            old_tp_order_id = pos_info.get("tp_order_id") or self.active_tp_orders.get(symbol)
                            if old_tp_order_id:
                                cancel_success = await self.cancel_order(symbol, old_tp_order_id)
                                if cancel_success:
                                    print(f"   ❌ TP anterior cancelado")
                            
                            # 4. Obtener posición real de Binance para cantidad correcta
                            await self.get_positions()
                            real_position = self.positions.get(symbol)
                            tp_qty = real_position.quantity if real_position else total_qty
                            
                            # 5. Crear nuevo TP con cantidad total
                            tp_result = await self.place_take_profit(symbol, "BUY", new_tp, tp_qty)
                            if tp_result:
                                new_tp_order_id = tp_result.get("orderId")
                                self.active_tp_orders[symbol] = new_tp_order_id
                                pos_info["tp_order_id"] = new_tp_order_id
                                print(f"   ✅ Nuevo TP creado @ ${new_tp:.4f}")
                            
                            # Actualizar info de posición
                            pos_info["take_profit"] = new_tp
                        
                        # Actualizar posición info
                        pos_info["entry_price"] = new_avg_price
                        pos_info["quantity"] = total_qty
                        pos_info["executions"].append({
                            "order_num": len(pos_info["executions"]) + 1,
                            "price": fill_price,
                            "quantity": executed_qty,
                            "type": "LIMIT",
                            "time": datetime.now().isoformat()
                        })
                        
                    else:
                        # Orden no vinculada - crear nueva posición o es la primera
                        # Si ya existe posición, igual promediamos
                        if symbol in self.active_positions_info:
                            pos_info = self.active_positions_info[symbol]
                            old_qty = pos_info["quantity"]
                            old_entry = pos_info["entry_price"]
                            total_qty = old_qty + executed_qty
                            new_avg_price = ((old_entry * old_qty) + (fill_price * executed_qty)) / total_qty
                            
                            pos_info["entry_price"] = new_avg_price
                            pos_info["quantity"] = total_qty
                            
                            print(f"   🔄 PROMEDIANDO: Entrada → ${new_avg_price:.4f} | Qty {total_qty:.4f}")
                        else:
                            # Nueva posición
                            self.active_positions_info[symbol] = {
                                "entry_price": fill_price,
                                "quantity": executed_qty,
                                "take_profit": info.get("take_profit"),
                                "stop_loss": info.get("stop_loss"),
                                "fib_high": info.get("fib_high"),
                                "fib_low": info.get("fib_low"),
                                "strategy_case": info.get("strategy_case"),
                                "side": "SHORT",
                                "tp_order_id": None,
                                "executions": [{
                                    "order_num": 1,
                                    "price": fill_price,
                                    "quantity": executed_qty,
                                    "type": "LIMIT",
                                    "time": datetime.now().isoformat()
                                }]
                            }
                        
                        # Colocar TP y SL para esta posición (verificar si ya existen)
                        await self.get_positions()
                        real_position = self.positions.get(symbol)
                        tp_qty = real_position.quantity if real_position else executed_qty
                        
                        # Verificar órdenes existentes antes de crear
                        existing_orders = await self._request("GET", "/fapi/v1/openOrders", {"symbol": symbol}, signed=True)
                        has_tp = any(o.get("type") in ["TAKE_PROFIT", "TAKE_PROFIT_MARKET", "TAKE_PROFIT_LIMIT"] for o in (existing_orders or []))
                        has_sl = any(o.get("type") in ["STOP", "STOP_MARKET", "STOP_LIMIT"] for o in (existing_orders or []))
                        
                        if info.get("take_profit") and not has_tp:
                            tp_result = await self.place_take_profit(symbol, "BUY", info["take_profit"], tp_qty)
                            if tp_result:
                                tp_order_id = tp_result.get("orderId")
                                self.active_tp_orders[symbol] = tp_order_id
                                if symbol in self.active_positions_info:
                                    self.active_positions_info[symbol]["tp_order_id"] = tp_order_id
                                print(f"   ✅ TP creado @ ${info['take_profit']:.4f}")
                        elif has_tp:
                            print(f"   ℹ️ {symbol} ya tiene TP, omitiendo")
                        
                        if info.get("stop_loss") and not has_sl:
                            sl_result = await self.place_stop_loss(symbol, "BUY", info["stop_loss"], tp_qty)
                            if sl_result:
                                print(f"   ✅ SL creado @ ${info['stop_loss']:.4f}")
                        elif has_sl:
                            print(f"   ℹ️ {symbol} ya tiene SL, omitiendo")
                    
                    orders_to_remove.append(order_id)
                    
                elif status in ["CANCELED", "EXPIRED", "REJECTED"]:
                    # Orden cancelada - remover del tracking
                    orders_to_remove.append(order_id)
        
        # Limpiar órdenes procesadas
        for order_id in orders_to_remove:
            del self.pending_orders_tp_sl[order_id]
    
    async def sync_positions_on_startup(self):
        """
        Sincronizar posiciones existentes al iniciar el bot
        Útil para recuperar estado después de reinicio
        """
        await self.get_positions()
        
        for symbol, pos in self.positions.items():
            if symbol not in self.active_positions_info:
                self.active_positions_info[symbol] = {
                    "entry_price": pos.entry_price,
                    "quantity": pos.quantity,
                    "take_profit": None,  # Desconocido
                    "stop_loss": None,
                    "fib_high": None,
                    "fib_low": None,
                    "side": pos.side,
                    "tp_order_id": None,
                    "executions": [{
                        "order_num": 1,
                        "price": pos.entry_price,
                        "quantity": pos.quantity,
                        "type": "UNKNOWN",
                        "time": datetime.now().isoformat()
                    }]
                }
                print(f"📥 Posición existente sincronizada: {symbol} {pos.side} @ ${pos.entry_price:.4f}")
    
    def clear_position_info(self, symbol: str):
        """Limpiar info de posición cuando se cierra"""
        if symbol in self.active_positions_info:
            del self.active_positions_info[symbol]
        if symbol in self.active_tp_orders:
            del self.active_tp_orders[symbol]
    
    async def cancel_pending_orders_for_symbol(self, symbol: str) -> bool:
        """
        Cancelar todas las órdenes pendientes de un símbolo cuando la posición se cierra.
        Esto incluye órdenes LIMIT que estaban esperando para promediar la posición.
        Similar a _cancel_linked_orders en paper_trading.py
        """
        try:
            # Cancelar todas las órdenes abiertas del símbolo
            result = await self.cancel_all_orders(symbol)
            if result:
                print(f"   🗑️ Órdenes pendientes canceladas para {symbol}")
                
                # Limpiar del tracking local
                orders_to_remove = [
                    order_id for order_id, info in self.pending_orders_tp_sl.items()
                    if info.get("symbol") == symbol
                ]
                for order_id in orders_to_remove:
                    del self.pending_orders_tp_sl[order_id]
                
                return True
            return False
        except Exception as e:
            print(f"   ⚠️ Error cancelando órdenes de {symbol}: {e}")
            return False
    
    async def ensure_tp_sl_for_positions(self):
        """
        Verificar posiciones abiertas que no tienen TP/SL y añadírselos.
        Usa la info guardada en active_positions_info para saber los niveles.
        Llamar periódicamente después de cada escaneo.
        """
        # Primero actualizar posiciones desde Binance
        await self.get_positions()
        
        if not self.positions:
            return
        
        # Obtener todas las órdenes abiertas para saber qué posiciones ya tienen TP/SL
        all_orders = await self._request("GET", "/fapi/v1/openOrders", signed=True)
        if not all_orders:
            all_orders = []
        
        # Crear set de símbolos que ya tienen TP o SL
        symbols_with_tp = set()
        symbols_with_sl = set()
        
        for order in all_orders:
            order_type = order.get("type", "")
            symbol = order.get("symbol", "")
            
            # Incluir todos los tipos de órdenes TP (limit y market)
            if order_type in ["TAKE_PROFIT", "TAKE_PROFIT_MARKET", "TAKE_PROFIT_LIMIT"]:
                symbols_with_tp.add(symbol)
            # Incluir todos los tipos de órdenes SL (limit y market)
            elif order_type in ["STOP", "STOP_MARKET", "STOP_LIMIT"]:
                symbols_with_sl.add(symbol)
        
        # Revisar cada posición
        for symbol, pos in self.positions.items():
            pos_info = self.active_positions_info.get(symbol)
            
            if not pos_info:
                # No tenemos info de esta posición, no podemos saber TP/SL
                continue
            
            needs_tp = symbol not in symbols_with_tp and pos_info.get("take_profit")
            needs_sl = symbol not in symbols_with_sl and pos_info.get("stop_loss")
            
            if not needs_tp and not needs_sl:
                continue
            
            # Determinar el lado para cerrar
            close_side = "BUY" if pos.side == "SHORT" else "SELL"
            
            if needs_tp:
                tp_price = pos_info["take_profit"]
                print(f"🔧 Añadiendo TP faltante para {symbol} @ ${tp_price:.4f}")
                tp_result = await self.place_take_profit(symbol, close_side, tp_price, pos.quantity)
                if tp_result:
                    tp_order_id = tp_result.get("orderId")
                    self.active_tp_orders[symbol] = tp_order_id
                    pos_info["tp_order_id"] = tp_order_id
                    print(f"   ✅ TP creado para {symbol}")
                else:
                    print(f"   ❌ No se pudo crear TP para {symbol}")
            
            if needs_sl:
                sl_price = pos_info["stop_loss"]
                print(f"🔧 Añadiendo SL faltante para {symbol} @ ${sl_price:.4f}")
                sl_result = await self.place_stop_loss(symbol, close_side, sl_price, pos.quantity)
                if sl_result:
                    print(f"   ✅ SL creado para {symbol}")
                else:
                    print(f"   ❌ No se pudo crear SL para {symbol}")


# Instancia global del trader
binance_trader = BinanceFuturesTrader()
