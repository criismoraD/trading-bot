"""
Servidor web para servir analisis_bot_v3.html y trades.json
Con actualización en tiempo real y soporte para ngrok
"""
import http.server
import socketserver
import os
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import requests

# Importar funciones de fibonacci.py
from fibonacci import calculate_zigzag, calculate_fibonacci_levels, ZigZagPoint

# Configuración
PORT = int(os.getenv("BOT_WEB_PORT", 8080))
DIRECTORY = Path(__file__).parent

# Cache para datos de velas
_candle_cache = {}
_cache_timeout = 60  # segundos

class CustomHandler(http.server.SimpleHTTPRequestHandler):
    """Handler personalizado que sirve archivos desde el directorio del bot"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIRECTORY), **kwargs)
    
    def do_GET(self):
        """Manejar peticiones GET con headers CORS y sin cache para JSON"""
        parsed = urlparse(self.path)
        path = parsed.path
        
        # Redirigir raíz a analisis_bot_v3.html
        if path == '/' or path == '':
            self.path = '/analisis_bot_v3.html'
            return super().do_GET()
        
        # API endpoint para ZigZag
        if path == '/api/zigzag':
            return self._handle_zigzag_api(parse_qs(parsed.query))
        
        # Headers para permitir CORS y evitar cache en JSON
        if path.endswith('.json'):
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()
            
            # Leer y enviar archivo JSON
            file_path = DIRECTORY / path.lstrip('/')
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    self.wfile.write(f.read().encode('utf-8'))
            else:
                self.wfile.write(b'{}')
            return
        
        # Para otros archivos, usar handler normal
        return super().do_GET()
    
    def _handle_zigzag_api(self, params):
        """Calcular ZigZag usando fibonacci.py y devolver JSON"""
        try:
            symbol = params.get('symbol', ['BTCUSDT'])[0]
            timeframe = params.get('timeframe', ['1h'])[0]
            limit = int(params.get('limit', ['200'])[0])
            
            # Convertir timeframe al formato de Bybit
            tf_map = {'1': '1', '5': '5', '15': '15', '60': '60', '240': '240',
                      '1m': '1', '5m': '5', '15m': '15', '1h': '60', '4h': '240'}
            bybit_tf = tf_map.get(timeframe, '60')
            
            # Obtener datos de velas de Bybit
            url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={bybit_tf}&limit={limit}"
            response = requests.get(url, timeout=10)
            data = response.json()
            
            if data.get('retCode') != 0 or not data.get('result', {}).get('list'):
                raise ValueError(f"Error de Bybit: {data.get('retMsg', 'Sin datos')}")
            
            # Convertir a formato esperado por calculate_zigzag
            candles = []
            for c in reversed(data['result']['list']):
                candles.append({
                    'time': int(c[0]) // 1000,  # Convertir ms a segundos
                    'open': float(c[1]),
                    'high': float(c[2]),
                    'low': float(c[3]),
                    'close': float(c[4])
                })
            
            # Calcular ZigZag usando la misma lógica que el bot
            zigzag_points = calculate_zigzag(candles, timeframe)
            
            # Convertir ZigZagPoint a dict para JSON
            result = {
                'symbol': symbol,
                'timeframe': timeframe,
                'candles_count': len(candles),
                'points': [
                    {
                        'index': p.index,
                        'time': p.time,
                        'price': p.price,
                        'type': p.type
                    }
                    for p in zigzag_points
                ]
            }
            
            # Enviar respuesta JSON
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode('utf-8'))
            
        except Exception as e:
            # Error response
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            error_response = {'error': str(e)}
            self.wfile.write(json.dumps(error_response).encode('utf-8'))
    
    def log_message(self, format, *args):
        """Suprimir logs para reducir ruido"""
        pass

class WebServer:
    """Servidor web con control de inicio/parada"""
    
    def __init__(self, port=PORT):
        self.port = port
        self.server = None
        self.thread = None
        self.running = False
    
    def start(self):
        """Iniciar servidor en hilo separado"""
        if self.running:
            print(f"⚠️ Servidor ya está corriendo en puerto {self.port}")
            return
        
        try:
            self.server = socketserver.TCPServer(("", self.port), CustomHandler)
            self.server.allow_reuse_address = True
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            self.running = True
            print(f"🌐 Servidor web iniciado en http://localhost:{self.port}")
            print(f"📊 Análisis Antiguo: http://localhost:{self.port}/analisis_bot_v3.html")
            print(f"🚀 Dashboard Pro:    http://localhost:{self.port}/dashboard_pro.html")
        except OSError as e:
            print(f"❌ Error iniciando servidor: {e}")
            print(f"   Probablemente el puerto {self.port} está en uso")
    
    def stop(self):
        """Detener servidor"""
        if self.server:
            self.server.shutdown()
            self.running = False
            print("🛑 Servidor web detenido")
    
    def get_local_url(self):
        """Obtener URL local"""
        return f"http://localhost:{self.port}/analisis_bot_v3.html"

# Instancia global del servidor
_web_server = None

def start_web_server(port=PORT):
    """Iniciar servidor web (función helper)"""
    global _web_server
    if _web_server is None:
        _web_server = WebServer(port)
    _web_server.start()
    return _web_server

def stop_web_server():
    """Detener servidor web"""
    global _web_server
    if _web_server:
        _web_server.stop()

def get_web_server():
    """Obtener instancia del servidor"""
    return _web_server

if __name__ == "__main__":
    # Ejecutar servidor directamente
    print("=" * 50)
    print("🚀 Iniciando servidor web para Bot Analyzer")
    print("=" * 50)
    
    server = start_web_server()
    
    print("\n📝 Comandos disponibles:")
    print("   - Abre http://localhost:8080 en tu navegador")
    print("   - Presiona Ctrl+C para detener")
    print("\n💡 Para acceso remoto, ejecuta ngrok:")
    print("   ngrok http 8080")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n👋 Deteniendo servidor...")
        stop_web_server()
