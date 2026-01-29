import subprocess
import sys
import os
import time

# Definir las configuraciones para cada instancia del BOT DE TRADING (bot.py)
trading_bots = [
    {
        "name": "REAL_TRADING",
        "script": "bot.py",
        "env": {
            "BOT_TRADING_MODE": "real",
            "BOT_TRADES_FILE": "trades_real.json",
            "BOT_TIMEFRAME": "2h",
            "TELEGRAM_COMMANDS_ENABLED": "false",
            "BOT_SCAN_INTERVAL": "15"
        }
    },
    {
        "name": "PAPER_V2_2H",
        "script": "bot.py",
        "env": {
            "BOT_TRADING_MODE": "paper",
            "BOT_TRADES_FILE": "trades_V2_2h.json",
            "BOT_TIMEFRAME": "2h",
            "TELEGRAM_COMMANDS_ENABLED": "false",
            "BOT_SCAN_INTERVAL": "30"
        }
    }
]

# Configuración del MONITOR TELEGRAM
monitor_bot = {
    "name": "TELEGRAM_MONITOR",
    "script": "telegram_multibot.py",
    "env": {} # Usa .env normal
}

def run_bots():
    # Obtener el entorno base actual
    base_env = os.environ.copy()
    
    print(f"🚀 Iniciando Sistema Multi-Bot...")
    print(f"=================================")

    # 1. Lanzar los Bots de Trading
    for instance in trading_bots:
        # Fusionar variables de entorno de la instancia
        instance_env = base_env.copy()
        instance_env.update(instance["env"])
        
        script = instance["script"]
        if not os.path.exists(script):
            print(f"❌ Error: No se encuentra {script}")
            continue

        # Detectar OS y construir comando
        if os.name == 'nt':
            # Windows: Nueva ventana
            cmd = f'start "{instance["name"]}" cmd /k python {script}'
            subprocess.Popen(cmd, shell=True, env=instance_env)
        else:
            # Linux/Mac: Background process con nohup
            log_file = f"{instance['name'].lower()}.log"
            cmd = f"nohup python {script} > {log_file} 2>&1 &"
            print(f"   ▶️  Lanzando en background (logs en {log_file})...")
            subprocess.Popen(cmd, shell=True, env=instance_env)
            
        time.sleep(2) # Pausa entre lanzamientos

    # 2. Lanzar el Monitor de Telegram
    print(f"   ▶️  Lanzando MONITOR: {monitor_bot['name']}")
    
    if os.name == 'nt':
        subprocess.Popen(f'start "{monitor_bot["name"]}" cmd /k python {monitor_bot["script"]}', shell=True, env=base_env)
    else:
        log_file = "telegram_monitor.log"
        cmd = f"nohup python {monitor_bot['script']} > {log_file} 2>&1 &"
        print(f"   ▶️  Monitor en background (logs en {log_file})")
        subprocess.Popen(cmd, shell=True, env=base_env)

    print("\n✅ Todos los procesos han sido lanzados en ventanas separadas.")
    print("⚠️  Cierra las ventanas individuales para detener cada componente.")

if __name__ == "__main__":
    run_bots()
