"""
Script de prueba para verificar conexión a Binance Futures
Ejecuta: python test_binance.py
"""
import asyncio
import os
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

async def test_binance_connection():
    """Probar conexión a Binance Futures"""
    print("=" * 60)
    print("🔍 TEST DE CONEXIÓN A BINANCE FUTURES")
    print("=" * 60)
    
    # Verificar variables de entorno
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    
    print(f"\n1️⃣ Verificando variables de entorno...")
    print(f"   BINANCE_API_KEY: {'✅ Configurada' if api_key and api_key != 'tu_api_key_aqui' else '❌ NO configurada'}")
    print(f"   BINANCE_API_SECRET: {'✅ Configurada' if api_secret and api_secret != 'tu_api_secret_aqui' else '❌ NO configurada'}")
    
    if not api_key or api_key == 'tu_api_key_aqui':
        print("\n❌ Configura BINANCE_API_KEY en el archivo .env")
        return
    
    if not api_secret or api_secret == 'tu_api_secret_aqui':
        print("\n❌ Configura BINANCE_API_SECRET en el archivo .env")
        return
    
    print(f"\n   API Key: {api_key[:8]}...{api_key[-4:]}")
    print(f"   API Secret: {api_secret[:4]}...{api_secret[-4:]}")
    
    # Importar el trader
    from binance_trading import BinanceFuturesTrader
    
    trader = BinanceFuturesTrader()
    
    print(f"\n2️⃣ Conectando a Binance Futures...")
    connected = await trader.connect()
    
    if not connected:
        print("\n❌ FALLO LA CONEXIÓN")
        print("   Posibles causas:")
        print("   - API Key o Secret incorrectos")
        print("   - API Key no tiene permisos de Futures")
        print("   - IP no está en la whitelist (si tienes restricción)")
        print("   - Hora del sistema desincronizada")
        return
    
    print(f"\n3️⃣ Obteniendo información de cuenta...")
    
    # Balance
    balance_info = await trader.get_account_balance()
    if balance_info:
        print(f"   💰 Balance Total: ${balance_info['balance']:.2f} USDT")
        print(f"   💵 Disponible: ${balance_info['availableBalance']:.2f} USDT")
    
    # Posiciones abiertas
    print(f"\n4️⃣ Verificando posiciones abiertas...")
    positions = await trader.get_open_positions()
    if positions:
        print(f"   📊 Posiciones activas: {len(positions)}")
        for pos in positions[:5]:  # Mostrar máximo 5
            symbol = pos['symbol']
            amt = float(pos['positionAmt'])
            entry = float(pos['entryPrice'])
            pnl = float(pos['unRealizedProfit'])
            print(f"      {symbol}: {amt:+.4f} @ ${entry:.4f} | PnL: ${pnl:+.2f}")
    else:
        print(f"   📊 Sin posiciones abiertas")
    
    # Órdenes abiertas
    print(f"\n5️⃣ Verificando órdenes abiertas...")
    orders = await trader.get_open_orders()
    if orders:
        print(f"   📋 Órdenes pendientes: {len(orders)}")
        for order in orders[:5]:  # Mostrar máximo 5
            print(f"      {order.symbol}: {order.side} {order.order_type} @ ${order.price:.4f}")
    else:
        print(f"   📋 Sin órdenes pendientes")
    
    # Test de precisión de símbolos
    print(f"\n6️⃣ Verificando información de símbolos...")
    test_symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
    for symbol in test_symbols:
        if symbol in trader.symbol_info:
            info = trader.symbol_info[symbol]
            print(f"   {symbol}: pricePrecision={info['pricePrecision']}, qtyPrecision={info['quantityPrecision']}")
    
    print(f"\n" + "=" * 60)
    print(f"✅ CONEXIÓN EXITOSA - API funcionando correctamente")
    print(f"=" * 60)
    
    # Cerrar sesión
    await trader.disconnect()


async def test_available_endpoints():
    """Mostrar qué datos podemos obtener de la API"""
    print("\n" + "=" * 60)
    print("📚 DATOS DISPONIBLES VÍA API DE BINANCE FUTURES")
    print("=" * 60)
    
    endpoints = [
        ("Balance", "/fapi/v2/balance", "Balance USDT, disponible, en uso"),
        ("Posiciones", "/fapi/v2/positionRisk", "Posiciones abiertas, PnL, leverage"),
        ("Órdenes abiertas", "/fapi/v1/openOrders", "Órdenes limit/stop pendientes"),
        ("Historial trades", "/fapi/v1/userTrades", "Historial de operaciones ejecutadas"),
        ("Historial órdenes", "/fapi/v1/allOrders", "Todas las órdenes (abiertas y cerradas)"),
        ("Income/PnL", "/fapi/v1/income", "Historial de PnL realizado, funding, comisiones"),
        ("Bracket órdenes", "/fapi/v1/positionSide/dual", "Modo One-way o Hedge"),
        ("Leverage", "/fapi/v1/leverage", "Configurar leverage por símbolo"),
        ("Tipo de margen", "/fapi/v1/marginType", "Cross o Isolated"),
        ("Precio actual", "/fapi/v1/ticker/price", "Precio actual de cualquier par"),
        ("Klines/Velas", "/fapi/v1/klines", "Velas OHLCV históricas"),
        ("Exchange Info", "/fapi/v1/exchangeInfo", "Info de todos los pares, precisión, límites"),
    ]
    
    for name, endpoint, desc in endpoints:
        print(f"\n📌 {name}")
        print(f"   Endpoint: {endpoint}")
        print(f"   Datos: {desc}")


if __name__ == "__main__":
    asyncio.run(test_binance_connection())
    asyncio.run(test_available_endpoints())
