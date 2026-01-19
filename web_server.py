"""
Servidor web para servir analisis_bot.html y trades.json
Con actualización en tiempo real y soporte para ngrok
"""
import http.server
import socketserver
import os
import json
import threading
import time
from pathlib import Path

# Configuración
PORT = 8080
DIRECTORY = Path(__file__).parent

class CustomHandler(http.server.SimpleHTTPRequestHandler):
    """Handler personalizado que sirve archivos desde el directorio del bot"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIRECTORY), **kwargs)
    
    def do_GET(self):
        """Manejar peticiones GET con headers CORS y sin cache para JSON"""
        # Redirigir raíz a analisis_bot.html
        if self.path == '/' or self.path == '':
            self.path = '/analisis_bot.html'
        
        # Headers para permitir CORS y evitar cache en JSON
        if self.path.endswith('.json'):
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()
            
            # Leer y enviar archivo JSON
            file_path = DIRECTORY / self.path.lstrip('/')
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    self.wfile.write(f.read().encode('utf-8'))
            else:
                self.wfile.write(b'{}')
            return
        
        # Para otros archivos, usar handler normal
        return super().do_GET()
    
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
            print(f"📊 Accede a: http://localhost:{self.port}/analisis_bot.html")
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
        return f"http://localhost:{self.port}/analisis_bot.html"

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
