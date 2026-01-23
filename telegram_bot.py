"""
Bot de Telegram para Reportes del Trading Bot
Envía reportes automáticos cada 20 minutos y responde a comandos
"""
import asyncio
import aiohttp
import os
import json
from datetime import datetime
from typing import Optional, Dict, List
from dataclasses import dataclass

from logger import telegram_logger as logger
from config import TELEGRAM_TOKEN

# Configuración del Bot de Telegram
# Archivo para guardar los chats autorizados
CHATS_FILE = "telegram_chats.json"

def load_authorized_chats() -> set:
    """Cargar chats autorizados desde archivo"""
    if os.path.exists(CHATS_FILE):
        try:
            with open(CHATS_FILE, 'r') as f:
                return set(json.load(f))
        except Exception as e:
            logger.error(f"Error cargando chats: {e}")
            return set()
    return set()

def save_authorized_chats():
    """Guardar chats autorizados a archivo"""
    try:
        with open(CHATS_FILE, 'w') as f:
            json.dump(list(AUTHORIZED_CHATS), f)
    except Exception as e:
        logger.error(f"Error guardando chats: {e}")

# Inicializar chats autorizados
AUTHORIZED_CHATS: set = load_authorized_chats()

# Permitir chat por defecto desde .env (para notificaciones sin /start)
_default_chat_id = os.getenv("TELEGRAM_CHAT_ID")
if _default_chat_id and _default_chat_id.lstrip("-").isdigit():
    AUTHORIZED_CHATS.add(int(_default_chat_id))
    save_authorized_chats()


@dataclass
class TelegramConfig:
    token: str = TELEGRAM_TOKEN
    report_interval: int = 40 * 60  # 40 minutos
    enabled: bool = True


class TelegramBot:
    """Bot de Telegram para reportes y comandos"""
    
    def __init__(self, config: TelegramConfig = None):
        self.config = config or TelegramConfig()
        self.api_url = f"https://api.telegram.org/bot{self.config.token}"
        self.last_update_id = 0
        self.running = False
        self.account = None  # Se asigna después
        self.scanner = None  # Se asigna después
        self.price_cache: Dict[str, float] = {}
        
    async def send_message(self, chat_id: int, text: str, 
                           parse_mode: str = "HTML") -> bool:
        """Enviar mensaje a un chat"""
        url = f"{self.api_url}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        return True
                    else:
                        error = await response.text()
                        logger.error(f"Error enviando mensaje: {error}")
                        return False
        except Exception as e:
            logger.error(f"Error en send_message: {e}")
            return False

    async def send_document(self, chat_id: int, file_path: str, caption: str = "") -> bool:
        """Enviar documento a un chat"""
        url = f"{self.api_url}/sendDocument"
        
        if not os.path.exists(file_path):
            await self.send_message(chat_id, f"⚠️ Archivo no encontrado: {file_path}")
            return False

        try:
            data = aiohttp.FormData()
            data.add_field('chat_id', str(chat_id))
            data.add_field('caption', caption)
            data.add_field('document', open(file_path, 'rb'), filename=os.path.basename(file_path))

            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data) as response:
                    if response.status == 200:
                        return True
                    else:
                        error = await response.text()
                        logger.error(f"Error enviando documento: {error}")
                        return False
        except Exception as e:
            logger.error(f"Error en send_document: {e}")
            return False
    
    async def get_ngrok_url(self) -> Optional[str]:
        """Obtener URL pública de ngrok si está activo"""
        try:
            async with aiohttp.ClientSession() as session:
                # ngrok expone su API local en el puerto 4040
                async with session.get("http://127.0.0.1:4040/api/tunnels", timeout=2) as response:
                    if response.status == 200:
                        data = await response.json()
                        tunnels = data.get("tunnels", [])
                        for tunnel in tunnels:
                            if tunnel.get("proto") == "https":
                                return tunnel.get("public_url")
                        # Si no hay HTTPS, usar HTTP
                        if tunnels:
                            return tunnels[0].get("public_url")
        except Exception as e:
            logger.debug(f"ngrok no disponible: {e}")
        return None
    
    async def broadcast_message(self, text: str):
        """Enviar mensaje a todos los chats autorizados"""
        if not AUTHORIZED_CHATS:
            return
            
        for chat_id in AUTHORIZED_CHATS:
            await self.send_message(chat_id, text)
    
    def format_report(self) -> str:
        """Generar reporte COMPLETO para Telegram (Cuenta + Stats + Posiciones + Historial)"""
        if not self.account:
            return "⚠️ Bot no inicializado"
        
        # Actualizar PnL con precios actuales antes de generar reporte
        if self.price_cache:
            self.account.update_positions_pnl(self.price_cache)
        
        status = self.account.get_status()
        now = datetime.now().strftime("%H:%M:%S %d/%m/%Y")
        
        # Historial
        history = self.account.trade_history
        total_trades = len(history)
        winners = sum(1 for t in history if t.get('pnl', 0) > 0)
        losers = sum(1 for t in history if t.get('pnl', 0) < 0)
        win_rate = (winners / total_trades * 100) if total_trades > 0 else 0
        
        # PnL
        total_pnl = sum(t.get('pnl', 0) for t in history)
        
        # Emoji según PnL
        pnl_emoji = "🟢" if status['total_unrealized_pnl'] >= 0 else "🔴"
        balance_emoji = "📈" if self.account.balance >= self.account.initial_balance else "📉"
        
        # Por caso (stats)
        cases = {1: [], 2: [], 3: [], 4: []}
        for t in history:
            case = t.get('strategy_case', 0)
            if case in cases:
                cases[case].append(t.get('pnl', 0))
        
        # Balance Margin (Equity) = balance + PnL flotante
        margin_balance = status['balance'] + status['total_unrealized_pnl']
        
        report = f"""
<b>📊 REPORTE COMPLETO</b>
<code>━━━━━━━━━━━━━━━━━━━━━━━━</code>
🕐 <b>{now}</b>

<b>💰 CUENTA</b>
├ Balance: <code>${status['balance']:.2f}</code> {balance_emoji}
├ PnL Flotante: <code>${status['total_unrealized_pnl']:.4f}</code> {pnl_emoji}
├ Balance Margin: <code>${margin_balance:.2f}</code>
└ Margen Disp.: <code>${status['available_margin']:.2f}</code>

<b>📈 RENDIMIENTO</b>
├ Total Trades: <code>{total_trades}</code> (Hoy: {self._count_today_trades()})
├ Win Rate: <code>{win_rate:.1f}%</code> ({winners}W - {losers}L)
├ PnL Acumulado: <code>${total_pnl:.4f}</code>
└ Profit Factor: <code>{self._calculate_profit_factor():.2f}</code>

<b>🎯 POR CASO</b>
├ C1: {len(cases[1])} trades | ${sum(cases[1]):.2f}
├ C3: {len(cases[3])} trades | ${sum(cases[3]):.2f}
└ C4: {len(cases[4])} trades | ${sum(cases[4]):.2f}

<b>📂 ESTADO ACTUAL</b>
├ Posiciones: <code>{status['open_positions']}</code>
└ Órdenes: <code>{status['pending_orders']}</code>
"""
        
        # Agregar detalle de posiciones (todas) con CASO
        if self.account.open_positions:
            report += "\n<b>👁️ POSICIONES ACTIVAS:</b>\n"
            for order_id, pos in self.account.open_positions.items():
                current = self.price_cache.get(pos.symbol, pos.current_price)
                pnl_pos = pos.unrealized_pnl
                emoji = "🟢" if pnl_pos >= 0 else "🔴"
                case_str = self._format_case(pos.strategy_case) if hasattr(pos, 'strategy_case') else "?"
                report += (
                    f"├ {pos.symbol} ({case_str}) {emoji} <code>${pnl_pos:.2f}</code>\n"
                    f"   Entry: <code>${pos.entry_price:.4f}</code> → Actual: <code>${current:.4f}</code>\n"
                )

        # Agregar detalle de órdenes pendientes (todas) con CASO
        if self.account.pending_orders:
            report += "\n<b>📋 ÓRDENES PENDIENTES:</b>\n"
            for order_id, order in self.account.pending_orders.items():
                case_str = self._format_case(order.strategy_case) if hasattr(order, 'strategy_case') else "?"
                report += (
                    f"├ {order.symbol} ({case_str}) {order.side.value}\n"
                    f"   Precio: <code>${order.price:.4f}</code> → TP: <code>${order.take_profit:.4f}</code>\n"
                )
        
        # Agregar últimas operaciones cerradas (máx 5)
        if history:
            report += "\n<b>📜 ÚLTIMAS OPERACIONES:</b>\n"
            recent = list(reversed(history[-5:]))  # Últimas 5, más reciente primero
            for t in recent:
                pnl = t.get('pnl', 0)
                emoji = "🟢" if pnl >= 0 else "🔴"
                reason = t.get('reason', '?')
                reason_emoji = "✅" if reason == 'TP' else "❌"
                case_str = self._format_case(t.get('strategy_case', 0))
                report += f"├ {t.get('symbol', '?')} ({case_str}) {reason_emoji} {emoji} <code>${pnl:.4f}</code>\n"
                
        report += "\n💡 /download para bajar el historial completo"
        return report
    
    def _calculate_profit_factor(self) -> float:
        if not self.account or not self.account.trade_history:
            return 0.0
        winners = [t.get('pnl', 0) for t in self.account.trade_history if t.get('pnl', 0) > 0]
        losers = [t.get('pnl', 0) for t in self.account.trade_history if t.get('pnl', 0) < 0]
        
        gross_profit = sum(winners)
        gross_loss = abs(sum(losers))
        
        if gross_loss == 0:
            return float('inf') if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    def _count_today_trades(self) -> int:
        """Contar trades de hoy"""
        today = datetime.now().date()
        count = 0
        for trade in self.account.trade_history:
            try:
                closed_at = trade.get('closed_at', '')
                if closed_at:
                    trade_date = datetime.fromisoformat(closed_at).date()
                    if trade_date == today:
                        count += 1
            except:
                pass
        return count
    
    def _format_case(self, case: int) -> str:
        """Formatear número de caso para mostrar"""
        return f"C{case}" if case else "?"
    
    def format_balance(self) -> str:
        """Formato corto solo con balance"""
        if not self.account:
            return "⚠️ Bot no inicializado"
        
        # Actualizar PnL con precios actuales
        if self.price_cache:
            self.account.update_positions_pnl(self.price_cache)
        
        status = self.account.get_status()
        return f"""
<b>💰 BALANCE</b>
├ Balance: <code>${status['balance']:.2f}</code>
├ PnL No Realizado: <code>${status['total_unrealized_pnl']:.4f}</code>
└ Margen Disponible: <code>${status['available_margin']:.2f}</code>
"""
    
    def format_positions(self) -> str:
        """Formato solo con posiciones"""
        if not self.account:
            return "⚠️ Bot no inicializado"
        
        # Actualizar PnL con precios actuales
        if self.price_cache:
            self.account.update_positions_pnl(self.price_cache)
        
        if not self.account.open_positions:
            return "📭 Sin posiciones abiertas"
        
        text = "<b>📂 POSICIONES ABIERTAS</b>\n"
        for order_id, pos in self.account.open_positions.items():
            current = self.price_cache.get(pos.symbol, pos.current_price)
            pnl = pos.unrealized_pnl
            emoji = "🟢" if pnl >= 0 else "🔴"
            
            text += f"""
<b>{pos.symbol}</b> (Caso {pos.strategy_case})
├ Lado: {pos.side.value if hasattr(pos.side, 'value') else pos.side}
├ Entrada: <code>${pos.entry_price:.4f}</code>
├ Actual: <code>${current:.4f}</code>
├ TP: <code>${pos.take_profit:.4f}</code>
└ {emoji} PnL: <code>${pnl:.4f}</code>
"""
        return text
    
    def format_stats(self) -> str:
        """Formato con estadísticas detalladas"""
        if not self.account:
            return "⚠️ Bot no inicializado"
        
        history = self.account.trade_history
        if not history:
            return "📊 Sin historial de trades"
        
        total = len(history)
        winners = [t for t in history if t.get('pnl', 0) > 0]
        losers = [t for t in history if t.get('pnl', 0) < 0]
        
        win_rate = len(winners) / total * 100 if total > 0 else 0
        total_pnl = sum(t.get('pnl', 0) for t in history)
        avg_win = sum(t.get('pnl', 0) for t in winners) / len(winners) if winners else 0
        avg_loss = sum(t.get('pnl', 0) for t in losers) / len(losers) if losers else 0
        profit_factor = self._calculate_profit_factor()
        
        # Max drawdown
        max_dd = min((t.get('min_pnl', 0) for t in history), default=0)
        
        # Por caso
        cases = {1: [], 2: [], 3: [], 4: []}
        for t in history:
            case = t.get('strategy_case', 0)
            if case in cases:
                cases[case].append(t.get('pnl', 0))
        
        return f"""
<b>📊 ESTADÍSTICAS DETALLADAS</b>
<code>━━━━━━━━━━━━━━━━━━━━━━━━</code>

<b>📈 GENERAL</b>
├ Total Trades: <code>{total}</code>
├ Ganadores: <code>{len(winners)}</code>
├ Perdedores: <code>{len(losers)}</code>
├ Win Rate: <code>{win_rate:.1f}%</code>
└ PnL Total: <code>${total_pnl:.4f}</code>

<b>💹 PROMEDIOS</b>
├ Ganancia Promedio: <code>${avg_win:.4f}</code>
├ Pérdida Promedio: <code>${avg_loss:.4f}</code>
├ Profit Factor: <code>{profit_factor:.2f}</code>
└ Max Drawdown: <code>${max_dd:.4f}</code>

<b>🎯 POR CASO</b>
├ Caso 1: {len(cases[1])} trades | ${sum(cases[1]):.4f}
├ Caso 3: {len(cases[3])} trades | ${sum(cases[3]):.4f}
└ Caso 4: {len(cases[4])} trades | ${sum(cases[4]):.4f}
"""
    
    def format_history(self, case_filter: int = None, limit: int = 10) -> str:
        """Formato de operaciones cerradas con filtro por caso"""
        if not self.account:
            return "⚠️ Bot no inicializado"
        
        history = self.account.trade_history
        if not history:
            return "📜 Sin historial de operaciones cerradas"
        
        # Filtrar por caso si se especifica
        if case_filter:
            history = [t for t in history if t.get('strategy_case') == case_filter]
            if not history:
                return f"📜 Sin operaciones cerradas para Caso {case_filter}"
        
        # Tomar las últimas N operaciones (más recientes primero)
        recent = list(reversed(history[-limit:]))
        
        header = f"📜 <b>HISTORIAL DE OPERACIONES</b>"
        if case_filter:
            header += f" (Caso {case_filter})"
        header += f"\n<code>━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
        header += f"Mostrando últimas {len(recent)} de {len(history)} operaciones\n\n"
        
        lines = []
        for t in recent:
            pnl = t.get('pnl', 0)
            emoji = "🟢" if pnl >= 0 else "🔴"
            reason = t.get('reason', '?')
            reason_emoji = "✅" if reason == 'TP' else "❌"
            
            lines.append(f"{emoji} <b>{t.get('symbol', '?')}</b> ({self._format_case(t.get('strategy_case', 0))})")
            lines.append(f"   {reason_emoji} {reason} | PnL: <code>${pnl:.4f}</code>")
            lines.append(f"   📊 Entry: ${t.get('entry_price', 0):.4f} → Close: ${t.get('close_price', 0):.4f}")
            lines.append(f"   🎯 TP: ${t.get('take_profit', 0):.4f} | SL: ${t.get('stop_loss', 0):.4f}")
            
            # Mostrar ejecuciones
            executions = t.get('executions', [])
            if executions:
                exec_str = ", ".join([f"{e.get('type', '?')}@${e.get('price', 0):.4f}" for e in executions])
                lines.append(f"   ⚡ {exec_str}")
            lines.append("")
        
        # Resumen
        total_pnl = sum(t.get('pnl', 0) for t in history)
        winners = sum(1 for t in history if t.get('pnl', 0) > 0)
        
        summary = f"<code>━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
        summary += f"📈 Total PnL: <code>${total_pnl:.4f}</code> | Win Rate: {winners}/{len(history)}"
        
        return header + "\n".join(lines) + summary
    
    async def handle_command(self, chat_id: int, command: str, args: List[str]):
        """Procesar comandos"""
        command = command.lower().strip()
        
        if command == "/start":
            if chat_id not in AUTHORIZED_CHATS:
                AUTHORIZED_CHATS.add(chat_id)
                save_authorized_chats()
                logger.info(f"Nuevo chat autorizado: {chat_id}")
            
            await self.send_message(chat_id, """
<b>🤖 Bot de Trading Fibonacci</b>

¡Bienvenido! Este bot te enviará reportes automáticos cada 40 minutos.

<b>Comandos disponibles:</b>
/report - Reporte completo (cuenta, stats, posiciones, historial)
/download - Descargar historial (JSON)
/analyzer - Link al analizador web (requiere ngrok)

Tu chat ha sido registrado para recibir notificaciones.
""")

            # Confirmación inmediata de inicio
            await self.send_message(chat_id, "🚀 <b>BOT INICIADO</b>\nNotificaciones activas para este chat.")
            
        elif command == "/report":
            await self.send_message(chat_id, self.format_report())
            
        elif command == "/balance":
            # Redirigir a report
            await self.send_message(chat_id, self.format_report())

        elif command == "/download":
            path = os.path.join(os.getcwd(), 'trades.json')
            await self.send_document(chat_id, path, caption="📂 Historial de Trades (trades.json)")
        
        elif command == "/analyzer":
            # Obtener URL de ngrok si está activo
            ngrok_url = await self.get_ngrok_url()
            if ngrok_url:
                await self.send_message(chat_id, f"""
<b>📊 Analizador de Trades</b>

🔗 <a href="{ngrok_url}">{ngrok_url}</a>

<b>Funciones:</b>
• Simular TP/SL por caso
• Ver estadísticas en tiempo real
• Activar auto-refresh para datos en vivo

💡 Haz click en el botón "🔄 Auto (OFF)" para activar actualización automática cada 2 segundos.
""")
            else:
                await self.send_message(chat_id, """
<b>📊 Analizador de Trades</b>

⚠️ El servidor web no está activo o ngrok no está corriendo.

<b>Para activar:</b>
1. Ejecuta: <code>python web_server.py</code>
2. En otra terminal: <code>ngrok http 8080</code>
3. Usa /analyzer de nuevo para obtener el link
""")
            
        elif command == "/help":
            await self.send_message(chat_id, """
<b>📚 AYUDA</b>

<b>Comandos:</b>
/report - Reporte completo (cuenta, posiciones, historial, stats)
/download - Descargar archivo trades.json
/analyzer - Link al analizador web (requiere ngrok)

<b>Notificaciones automáticas:</b>
• Reportes cada 40 minutos
• Alertas cuando se abre/cierra una posición
• Alertas cuando se ejecuta una orden límite

<b>Configurar analizador web:</b>
1. <code>python web_server.py</code>
2. <code>ngrok http 8080</code>
3. Usa /analyzer para obtener link
""")
        else:
            await self.send_message(chat_id, "❓ Comando no reconocido. Usa /report o /download")
    
    async def poll_updates(self):
        """Obtener actualizaciones de Telegram (polling)"""
        url = f"{self.api_url}/getUpdates"
        params = {
            "offset": self.last_update_id + 1,
            "timeout": 30
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=35) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("ok"):
                            for update in data.get("result", []):
                                self.last_update_id = update["update_id"]
                                await self.process_update(update)
        except asyncio.TimeoutError:
            pass  # Normal en long polling
        except Exception as e:
            logger.error(f"Error en polling: {e}")
    
    async def process_update(self, update: dict):
        """Procesar una actualización de Telegram"""
        if "message" in update:
            message = update["message"]
            chat_id = message["chat"]["id"]
            text = message.get("text", "")

            # Auto-autorizar cualquier chat que envíe comandos
            if text.startswith("/") and chat_id not in AUTHORIZED_CHATS:
                AUTHORIZED_CHATS.add(chat_id)
                save_authorized_chats()
                logger.info(f"Chat auto-autorizado: {chat_id}")
            
            if text.startswith("/"):
                parts = text.split()
                command = parts[0]
                args = parts[1:] if len(parts) > 1 else []
                await self.handle_command(chat_id, command, args)
    
    async def send_trade_alert(self, action: str, symbol: str, side: str, 
                                price: float, pnl: float = None, case: int = None):
        """Enviar alerta de trade a todos los chats"""
        emoji = "🟢" if action == "OPEN" else ("💰" if pnl and pnl > 0 else "📉")
        
        if action == "OPEN":
            text = f"""
{emoji} <b>NUEVA POSICIÓN</b>

📊 {symbol} (Caso {case})
├ Lado: {side}
└ Entrada: <code>${price:.4f}</code>
"""
        elif action == "CLOSE":
            pnl_emoji = "🟢" if pnl >= 0 else "🔴"
            text = f"""
{emoji} <b>POSICIÓN CERRADA</b>

📊 {symbol}
├ Precio: <code>${price:.4f}</code>
└ {pnl_emoji} PnL: <code>${pnl:.4f}</code>
"""
        elif action == "LIMIT_FILLED":
            text = f"""
⚡ <b>ORDEN LÍMITE EJECUTADA</b>

📊 {symbol} (Caso {case})
├ Lado: {side}
└ Precio: <code>${price:.4f}</code>
"""
        else:
            text = f"{emoji} {action}: {symbol} @ ${price:.4f}"
        
        await self.broadcast_message(text)
    
    async def run_polling_loop(self):
        """Loop principal de polling"""
        logger.info("Iniciando bot de Telegram...")
        self.running = True
        
        # MENSAJE DE BIENVENIDA AL INICIAR
        if AUTHORIZED_CHATS:
             await self.broadcast_message("🚀 <b>BOT INICIADO</b>\nEl sistema está en línea y operando.")
        
        while self.running:
            await self.poll_updates()
            await asyncio.sleep(1)
        
        # Mensaje de cierre
        if AUTHORIZED_CHATS:
             await self.broadcast_message("🛑 <b>BOT DETENIDO</b>\nEl sistema se está apagando.")
    
    async def run_report_loop(self):
        """Loop de reportes automáticos cada N minutos"""
        if not self.running:
            self.running = True
        minutes = self.config.report_interval // 60
        logger.info(f"Iniciando reportes automáticos cada {minutes} minutos")
        
        while self.running:
            await asyncio.sleep(self.config.report_interval)
            
            if AUTHORIZED_CHATS:
                logger.info(f"Enviando reporte automático a {len(AUTHORIZED_CHATS)} chats")
                report = self.format_report()
                await self.broadcast_message(report)
    
    async def start(self, account=None, scanner=None, price_cache: dict = None):
        """Iniciar el bot con las referencias necesarias"""
        self.account = account
        self.scanner = scanner
        if price_cache is not None:
            self.price_cache = price_cache
        
        # Ejecutar ambos loops en paralelo
        await asyncio.gather(
            self.run_polling_loop(),
            self.run_report_loop()
        )
    
    def stop(self):
        """Detener el bot"""
        self.running = False
        logger.info("Bot de Telegram detenido")


# Instancia global del bot
telegram_bot = TelegramBot()


async def notify_trade_open(symbol: str, side: str, price: float, case: int):
    """Notificar apertura de trade"""
    await telegram_bot.send_trade_alert("OPEN", symbol, side, price, case=case)


async def notify_trade_close(symbol: str, price: float, pnl: float):
    """Notificar cierre de trade"""
    await telegram_bot.send_trade_alert("CLOSE", symbol, "", price, pnl=pnl)


async def notify_limit_filled(symbol: str, side: str, price: float, case: int):
    """Notificar ejecución de orden límite"""
    await telegram_bot.send_trade_alert("LIMIT_FILLED", symbol, side, price, case=case)
