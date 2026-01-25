import subprocess
import time
import sys
import os
import signal
import json

# Configuración de las instancias
INSTANCES = [
    {
        "env": {
            "BOT_TIMEFRAME": "2h",
            "BOT_TRADES_FILE": "trades_2h.json",
            "BOT_WEB_PORT": "8082",
            "ENABLE_TELEGRAM": "False"
        },
        "name": "Bot 2H"
    },
    {
        "env": {
            "BOT_TIMEFRAME": "4h",
            "BOT_TRADES_FILE": "trades_4h.json",
            "BOT_WEB_PORT": "8083",
            "ENABLE_TELEGRAM": "False"
        },
        "name": "Bot 4H"
    }
]

processes = []

def signal_handler(sig, frame):
    print("\n🛑 Deteniendo todos los bots...")
    for p in processes:
        p.terminate()
    sys.exit(0)

def main():
    # Registrar manejador de Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)

    # Limpiar bandera de parada anterior si existe
    if os.path.exists("stop_signal.flag"):
        try:
            os.remove("stop_signal.flag")
        except:
            pass

    print("🚀 Iniciando Multi-Bot Launcher + Telegram Monitor...")
    print("=====================================================")

    # 1. Iniciar Bots de Trading
    for i, instance in enumerate(INSTANCES):
        env_vars = os.environ.copy()
        env_vars.update(instance["env"])
        
        print(f"▶ Iniciando {instance['name']} (Timeframe: {instance['env']['BOT_TIMEFRAME']}, Port: {instance['env']['BOT_WEB_PORT']})...")
        
        if sys.platform == 'win32':
            creation_flags = subprocess.CREATE_NEW_CONSOLE
        else:
            creation_flags = 0

        p = subprocess.Popen(
            [sys.executable, "bot.py"],
            env=env_vars,
            cwd=os.getcwd(),
            creationflags=creation_flags
        )
        processes.append(p)
        time.sleep(2) # Dar tiempo a que arranque

    # 2. Iniciar Bot de Telegram Centralizado
    print("▶ Iniciando Telegram Multi-Bot Monitor...")
    if sys.platform == 'win32':
        creation_flags = subprocess.CREATE_NEW_CONSOLE
    else:
        creation_flags = 0
        
    p_tele = subprocess.Popen(
        [sys.executable, "telegram_multibot.py"],
        env=os.environ.copy(),
        cwd=os.getcwd(),
        creationflags=creation_flags
    )
    processes.append(p_tele)

    print("\n✅ Todos los sistemas iniciados.")

    print("Tablero 2H: http://localhost:8082")
    print("Tablero 4H: http://localhost:8083")
    print("\nPresiona Ctrl+C para detener todo.")

    # Mantener script vivo
    while True:
        time.sleep(1)
        
        # Verificar señal de parada desde Telegram
        if os.path.exists("stop_signal.flag"):
            print("\n🛑 Señal de parada recibida desde Telegram.")
            try:
                os.remove("stop_signal.flag")
            except:
                pass
            signal_handler(None, None) # Mismo efecto que Ctrl+C

        # Verificar si algún proceso murió
        for i, p in enumerate(processes):
            if p.poll() is not None:
                # Si es el bot de telegram (último proceso), reiniciarlo si muere?
                # Por ahora solo log
                name = INSTANCES[i]['name'] if i < len(INSTANCES) else "Telegram Monitor"
                if p.poll() != 0: # Solo si murió con error
                     print(f"⚠️ {name} se ha cerrado inesperadamente.")

if __name__ == "__main__":
    main()
