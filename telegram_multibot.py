"""
Bot de Telegram Centralizado (Multi-Bot)
Monitorea los archivos JSON de las 3 instancias y da un reporte consolidado.
"""
import asyncio
import aiohttp
import os
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
from dotenv import load_dotenv

# Configuración básica de logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("MultiBotTelegram")

# Cargar variables de entorno
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHATS_FILE = "telegram_chats.json"

# Configuración de los bots a monitorear
BOTS_CONFIG = [

    {"name": "Bot 2H", "file": "trades_2h.json", "emoji": "🕑"},
    {"name": "Bot 4H", "file": "trades_4h.json", "emoji": "🕓"}
]

class MultiTelegramBot:
    def __init__(self, token):
        self.token = token
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.last_update_id = 0
        self.running = False
        self.running = False
        self.authorized_chats = self._load_chats()
        self.startup_time = int(datetime.now().timestamp())
        
    def _load_chats(self) -> set:
        if os.path.exists(CHATS_FILE):
            try:
                with open(CHATS_FILE, 'r') as f:
                    return set(json.load(f))
            except Exception:
                return set()
        return set()

    def _save_chats(self):
        try:
            with open(CHATS_FILE, 'w') as f:
                json.dump(list(self.authorized_chats), f)
        except Exception as e:
            logger.error(f"Error guardando chats: {e}")

    async def send_message(self, chat_id: int, text: str, parse_mode: str = "HTML") -> bool:
        url = f"{self.api_url}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    return response.status == 200
        except Exception as e:
            logger.error(f"Error enviando mensaje: {e}")
            return False

    async def send_document(self, chat_id: int, file_path: str, caption: str = "") -> bool:
        url = f"{self.api_url}/sendDocument"
        if not os.path.exists(file_path):
            await self.send_message(chat_id, f"⚠️ No encontrado: {file_path}")
            return False
            
        try:
            data = aiohttp.FormData()
            data.add_field('chat_id', str(chat_id))
            data.add_field('caption', caption)
            data.add_field('document', open(file_path, 'rb'), filename=os.path.basename(file_path))
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data) as response:
                    return response.status == 200
        except Exception as e:
            logger.error(f"Error enviando documento: {e}")
            return False

    def get_bot_status(self, file_path, name):
        """Lee el JSON y extrae el estado actual"""
        if not os.path.exists(file_path):
            return {"error": "Esperando datos..."}
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            balance = data.get("balance", 0)
            initial_balance = data.get("initial_balance", 30.0) # Default
            
            # Calcular PnL flotante
            open_positions = data.get("open_positions", {})
            params = data.get("params", {}) # A veces guardamos params extra
            
            pnl_float = 0.0
            margin_used = 0.0
            
            # Nota: El PnL flotante exacto depende del precio actual de mercado.
            # Como este script NO corre el websocket de precios, mostraremos el PnL guardado
            # (que podría tener un ligero retraso) o simplemente 0 si no lo tenemos.
            # Idealmente, los bots escriben el pnl_unrealized en 'status' o similar.
            # Revisando structure de trades.json generado por paper_trading.py (ver _save_trades):
            # Guardamos open_positions, balance, etc. No hay un campo 'overview'.
            # Sin embargo, open_positions tiene 'unrealized_pnl'.
            
            for pos in open_positions.values():
                pnl_float += pos.get("unrealized_pnl", 0)
                margin_used += pos.get("margin", 0)
                
            pending_margin = 0
            for order in data.get("pending_orders", {}).values():
                pending_margin += order.get("margin", 0)
                
            available_margin = balance - margin_used - pending_margin
            
            return {
                "balance": balance,
                "pnl_float": pnl_float,
                "margin_used": margin_used + pending_margin,
                "available_margin": available_margin,
                "open_count": len(open_positions),
                "order_count": len(data.get("pending_orders", {}))
            }
        except Exception as e:
            return {"error": str(e)}

    def generate_report(self):
        now = datetime.now().strftime("%H:%M:%S")
        report = f"🤖 <b>REPORTE MULTI-BOT</b>\n📅 {now}\n"
        
        for bot in BOTS_CONFIG:
            status = self.get_bot_status(bot["file"], bot["name"])
            report += f"\n{bot['emoji']} <b>{bot['name']}</b>"
            
            if "error" in status:
                report += f"\n└ ⚠️ {status['error']}\n"
                continue
                
            pnl = status['pnl_float']
            pnl_emoji = "🟢" if pnl >= 0 else "🔴"
            balance_trend = "📈"  # Simplificado
            
            margin_balance = status['balance'] + pnl
            
            report += f"\n├ Balance: <code>${status['balance']:.2f}</code> {balance_trend}"
            report += f"\n├ PnL Flotante: <code>${pnl:.4f}</code> {pnl_emoji}"
            report += f"\n├ Balance Margin: <code>${margin_balance:.2f}</code>"
            report += f"\n└ Margen Disp.: <code>${status['available_margin']:.2f}</code>\n"
            
        report += "\n💡 /files para descargar los JSON"
        return report

    async def handle_command(self, chat_id, text):
        command = text.split()[0].lower()
        
        if command == "/start":
            self.authorized_chats.add(chat_id)
            self._save_chats()
            await self.send_message(chat_id, "✅ <b>Multi-Bot Monitor Iniciado</b>\nRecibirás reportes periódicos.")
            
        elif command in ["/report", "/balance"]:
            await self.send_message(chat_id, self.generate_report())
            
        elif command == "/files":
            for bot in BOTS_CONFIG:
                await self.send_document(chat_id, bot["file"], caption=f"📂 {bot['name']}")
                
        elif command == "/help":
            await self.send_message(chat_id, "Comandos:\n/report - Ver estado\n/files - Descargar JSONs\n/stop - Detener todos los bots")

        elif command == "/stop":
            await self.send_message(chat_id, "⚠️ <b>DETENIENDO SISTEMA...</b>\nSe cerrarán todos los bots.")
            # Crear archivo bandera para que el launcher lo detecte
            with open("stop_signal.flag", "w") as f:
                f.write("STOP")

    async def poll_loop(self):
        logger.info("Iniciando polling...")
        while self.running:
            try:
                url = f"{self.api_url}/getUpdates"
                params = {"offset": self.last_update_id + 1, "timeout": 30}
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, params=params) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for update in data.get("result", []):
                                self.last_update_id = update["update_id"]
                                if "message" in update:
                                    msg = update["message"]
                                    # Ignorar mensajes viejos (anteriores al inicio del bot)
                                    # Usamos una ventana de seguridad de 10 segundos
                                    if "date" in msg and msg["date"] < (self.startup_time - 10):
                                        logger.info(f"Ignorando mensaje viejo ID {update['update_id']}")
                                        continue
                                        
                                    if "text" in msg:
                                        await self.handle_command(msg["chat"]["id"], msg["text"])
            except Exception as e:
                logger.error(f"Error polling: {e}")
                await asyncio.sleep(5)
            await asyncio.sleep(1)

    async def report_loop(self):
        logger.info("Iniciando loop de reportes (20 min)...")
        while self.running:
            await asyncio.sleep(20 * 60) # 20 minutos
            if self.authorized_chats:
                report = self.generate_report()
                for chat_id in self.authorized_chats:
                    await self.send_message(chat_id, report)

    async def run(self):
        self.running = True
        logger.info("Bot Multi-Telegram online")
        
        # Notificar inicio
        if self.authorized_chats:
            for chat in self.authorized_chats:
                await self.send_message(chat, "🚀 <b>MONITOR MULTI-BOT INICIADO</b>")
        
        await asyncio.gather(self.poll_loop(), self.report_loop())

if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        print("❌ TELEGRAM_TOKEN no configurado en .env")
        exit(1)
        
    bot = MultiTelegramBot(TELEGRAM_TOKEN)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("\n👋 Bot detenido")
