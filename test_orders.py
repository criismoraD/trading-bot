"""
Test script para colocar órdenes en Bybit Demo Trading
Prueba: WLDUSDT - Market y Limit orders
"""
import os
from dotenv import load_dotenv
from pybit.unified_trading import HTTP

load_dotenv()

# Configuración
API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
SYMBOL = "WLDUSDT"
LEVERAGE = 50

# Conectar a Bybit Demo
session = HTTP(
    demo=True,
    api_key=API_KEY,
    api_secret=API_SECRET
)

def set_leverage():
    """Configurar leverage"""
    try:
        session.set_leverage(
            category="linear",
            symbol=SYMBOL,
            buyLeverage=str(LEVERAGE),
            sellLeverage=str(LEVERAGE)
        )
        print(f"✅ Leverage configurado a {LEVERAGE}x")
    except Exception as e:
        print(f"⚠️ Leverage ya configurado o error: {e}")

def get_current_price():
    """Obtener precio actual"""
    result = session.get_tickers(category="linear", symbol=SYMBOL)
    if result.get("retCode") == 0:
        price = float(result['result']['list'][0]['lastPrice'])
        print(f"📊 Precio actual de {SYMBOL}: ${price}")
        return price
    return None

def place_market_order():
    """Colocar orden market de venta"""
    print("\n" + "="*50)
    print("🔴 TEST 1: MARKET ORDER")
    print("="*50)
    
    current_price = get_current_price()
    if not current_price:
        print("❌ No se pudo obtener precio")
        return
    
    # TP/SL al 5% del precio
    tp_price = round(current_price * 0.95, 4)  # 5% abajo (ganancia para short)
    sl_price = round(current_price * 1.05, 4)  # 5% arriba (pérdida para short)
    
    # Cantidad mínima para $5 de valor nocional
    min_qty = max(12, int(6 / current_price) + 1)  # Al menos $5 de valor
    qty = min_qty
    
    print(f"   Qty: {qty}")
    print(f"   TP: ${tp_price} (-5%)")
    print(f"   SL: ${sl_price} (+5%)")
    
    try:
        result = session.place_order(
            category="linear",
            symbol=SYMBOL,
            side="Sell",
            orderType="Market",
            qty=str(qty),
            takeProfit=str(tp_price),
            stopLoss=str(sl_price),
            tpTriggerBy="LastPrice",
            slTriggerBy="LastPrice",
            tpslMode="Full",
            positionIdx=0
        )
        
        if result.get("retCode") == 0:
            order_id = result['result']['orderId']
            print(f"✅ MARKET ORDER EXITOSA!")
            print(f"   Order ID: {order_id}")
        else:
            print(f"❌ Error: {result.get('retMsg')}")
            
    except Exception as e:
        print(f"❌ Exception: {e}")

def place_limit_order():
    """Colocar orden limit de venta a $0.5"""
    print("\n" + "="*50)
    print("📝 TEST 2: LIMIT ORDER @ $0.50")
    print("="*50)
    
    limit_price = 0.50
    
    # TP/SL al 5% del precio límite
    tp_price = round(limit_price * 0.95, 4)  # 5% abajo
    sl_price = round(limit_price * 1.05, 4)  # 5% arriba
    
    # Cantidad mínima para $5 de valor nocional
    qty = max(12, int(6 / limit_price) + 1)
    
    print(f"   Precio Límite: ${limit_price}")
    print(f"   Qty: {qty}")
    print(f"   TP: ${tp_price} (-5%)")
    print(f"   SL: ${sl_price} (+5%)")
    
    try:
        result = session.place_order(
            category="linear",
            symbol=SYMBOL,
            side="Sell",
            orderType="Limit",
            price=str(limit_price),
            qty=str(qty),
            takeProfit=str(tp_price),
            stopLoss=str(sl_price),
            tpTriggerBy="LastPrice",
            slTriggerBy="LastPrice",
            tpslMode="Full",
            positionIdx=0,
            timeInForce="GTC"
        )
        
        if result.get("retCode") == 0:
            order_id = result['result']['orderId']
            print(f"✅ LIMIT ORDER EXITOSA!")
            print(f"   Order ID: {order_id}")
        else:
            print(f"❌ Error: {result.get('retMsg')}")
            
    except Exception as e:
        print(f"❌ Exception: {e}")

def check_balance():
    """Ver balance disponible"""
    print("\n" + "="*50)
    print("💰 BALANCE ACTUAL")
    print("="*50)
    
    try:
        result = session.get_wallet_balance(accountType="UNIFIED")
        if result.get("retCode") == 0:
            account = result['result']['list'][0]
            available = float(account.get('totalAvailableBalance', 0))
            equity = float(account.get('totalEquity', 0))
            print(f"   Equity Total: ${equity:.2f}")
            print(f"   Disponible: ${available:.2f}")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    print("\n🚀 BYBIT DEMO TRADING - TEST DE ÓRDENES")
    print("="*50)
    
    check_balance()
    set_leverage()
    
    # TEST 1: Market Order
    place_market_order()
    
    # TEST 2: Limit Order
    place_limit_order()
    
    print("\n" + "="*50)
    print("✅ TEST COMPLETADO")
    print("="*50)
