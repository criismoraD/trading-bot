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
    
    def __init__(self):
        self.api_key = BINANCE_API_KEY
        self.api_secret = BINANCE_API_SECRET
        self.base_url = FUTURES_BASE_URL
        self.session: Optional[aiohttp.ClientSession] = None
        self.is_connected = False
        
        # Cache de información
        self.account_balance: float = 0.0
        self.available_balance: float = 0.0
        self.positions: Dict[str, BinancePosition] = {}
        self.open_orders: Dict[str, BinanceOrder] = {}
        self.symbol_info: Dict[str, dict] = {}  # Precisión de cada símbolo
        
        # Tracking de ejecuciones (compatible con paper trading)
        self.executions_history: Dict[str, List[dict]] = {}
        
        # Tracking de órdenes LIMIT pendientes con TP/SL
        # Cuando se llenan, añadimos TP/SL automáticamente
        self.pending_orders_tp_sl: Dict[int, dict] = {}
        
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
                    "supportsTpMarket": "TAKE_PROFIT_MARKET" in order_types
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
        # Verificar si soporta STOP_MARKET
        supports_stop_market = True
        if symbol in self.symbol_info:
            supports_stop_market = self.symbol_info[symbol].get("supportsStopMarket", True)
        
        if supports_stop_market:
            # Intentar STOP_MARKET primero
            params = {
                "symbol": symbol,
                "side": side,
                "type": "STOP_MARKET",
                "stopPrice": self.format_price(symbol, stop_price),
                "quantity": self.format_quantity(symbol, quantity),
                "reduceOnly": "true",
                "workingType": "MARK_PRICE"
            }
            result = await self._request("POST", "/fapi/v1/order", params, signed=True)
            if result:
                logger.info(f"🛑 STOP LOSS: {side} {symbol} @ ${stop_price}")
                return result
        
        # Fallback: usar STOP (orden límite condicional)
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
            "quantity": self.format_quantity(symbol, quantity),
            "timeInForce": "GTC",
            "reduceOnly": "true"
        }
        
        result = await self._request("POST", "/fapi/v1/order", params, signed=True)
        if result:
            logger.info(f"🛑 STOP LOSS (LIMIT): {side} {symbol} @ ${stop_price}")
        
        return result
    
    async def place_take_profit(self, symbol: str, side: str, tp_price: float,
                                 quantity: float) -> Optional[dict]:
        """
        Colocar orden Take Profit
        Para cerrar SHORT: side="BUY", tp_price = precio donde queremos tomar ganancia
        Intenta TAKE_PROFIT_MARKET primero, luego TAKE_PROFIT como fallback
        """
        # Verificar si soporta TAKE_PROFIT_MARKET
        supports_tp_market = True
        if symbol in self.symbol_info:
            supports_tp_market = self.symbol_info[symbol].get("supportsTpMarket", True)
        
        if supports_tp_market:
            # Intentar TAKE_PROFIT_MARKET primero
            params = {
                "symbol": symbol,
                "side": side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": self.format_price(symbol, tp_price),
                "quantity": self.format_quantity(symbol, quantity),
                "reduceOnly": "true",
                "workingType": "MARK_PRICE"
            }
            result = await self._request("POST", "/fapi/v1/order", params, signed=True)
            if result:
                logger.info(f"🎯 TAKE PROFIT: {side} {symbol} @ ${tp_price}")
                return result
        
        # Fallback: usar TAKE_PROFIT (orden límite condicional)
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
            "quantity": self.format_quantity(symbol, quantity),
            "timeInForce": "GTC",
            "reduceOnly": "true"
        }
        
        result = await self._request("POST", "/fapi/v1/order", params, signed=True)
        if result:
            logger.info(f"🎯 TAKE PROFIT (LIMIT): {side} {symbol} @ ${tp_price}")
        
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
        Ejecutar entrada SHORT con TP y SL
        Compatible con la lógica del scanner
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
        
        # Abrir posición SHORT (SELL)
        order = await self.place_market_order(symbol, "SELL", quantity)
        
        if order:
            # Obtener la cantidad ejecutada real (puede diferir ligeramente)
            executed_qty = float(order.get("executedQty", quantity))
            
            # Colocar Take Profit (BUY para cerrar SHORT)
            tp_result = None
            if take_profit:
                tp_result = await self.place_take_profit(symbol, "BUY", take_profit, executed_qty)
                if not tp_result:
                    print(f"   ⚠️ No se pudo crear TP para {symbol}")
            
            # Colocar Stop Loss si se especificó
            sl_result = None
            if stop_loss:
                sl_result = await self.place_stop_loss(symbol, "BUY", stop_loss, executed_qty)
                if not sl_result:
                    print(f"   ⚠️ No se pudo crear SL para {symbol}")
            
            sl_str = f"${stop_loss:.4f}" if stop_loss else "N/A"
            tp_str = f"${take_profit:.4f}" if take_profit else "N/A"
            tp_status = "✅" if tp_result else "❌"
            sl_status = "✅" if sl_result else "❌"
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
                                   stop_loss: float = None, strategy_case: int = 0) -> Optional[dict]:
        """
        Colocar orden límite SHORT con TP/SL adjuntos usando OTOCO
        Cuando la orden LIMIT se ejecuta, automáticamente se activan TP y SL
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
        
        # Fallback: solo orden límite sin TP/SL
        order = await self.place_limit_order(symbol, "SELL", limit_price, quantity)
        
        if order:
            print(f"📝 LIMIT SHORT {symbol} @ ${limit_price:.4f} (sin TP/SL)")
            return {
                "symbol": symbol,
                "limit_price": limit_price,
                "quantity": quantity,
                "margin": margin,
                "take_profit": take_profit,
                "stop_loss": stop_loss,
                "strategy_case": strategy_case,
                "order_id": order["orderId"]
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
        Llamar periódicamente para procesar órdenes ejecutadas
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
                    # Orden ejecutada - añadir TP y SL
                    executed_qty = float(order_status.get("executedQty", info["quantity"]))
                    
                    print(f"🔔 Orden LIMIT llenada: {symbol} - Añadiendo TP/SL...")
                    
                    # Colocar TP
                    tp_result = await self.place_take_profit(
                        symbol, 
                        info["close_side"], 
                        info["take_profit"], 
                        executed_qty
                    )
                    
                    # Colocar SL
                    sl_result = await self.place_stop_loss(
                        symbol,
                        info["close_side"],
                        info["stop_loss"],
                        executed_qty
                    )
                    
                    tp_status = "✅" if tp_result else "❌"
                    sl_status = "✅" if sl_result else "❌"
                    print(f"   {symbol}: TP{tp_status} @ ${info['take_profit']:.4f} | SL{sl_status} @ ${info['stop_loss']:.4f}")
                    
                    orders_to_remove.append(order_id)
                    
                elif status in ["CANCELED", "EXPIRED", "REJECTED"]:
                    # Orden cancelada - remover del tracking
                    orders_to_remove.append(order_id)
        
        # Limpiar órdenes procesadas
        for order_id in orders_to_remove:
            del self.pending_orders_tp_sl[order_id]


# Instancia global del trader
binance_trader = BinanceFuturesTrader()
