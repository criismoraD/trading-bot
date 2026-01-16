# 📖 Documentación del Bot de Trading Fibonacci

## Índice
1. [Descripción General](#descripción-general)
2. [Arquitectura del Sistema](#arquitectura-del-sistema)
3. [Estrategia de Trading](#estrategia-de-trading)
4. [Casos de Trading](#casos-de-trading)
5. [Flujo de Ejecución](#flujo-de-ejecución)
6. [Archivos del Proyecto](#archivos-del-proyecto)
7. [Configuración](#configuración)
8. [Modos de Operación](#modos-de-operación)
9. [Gestión de Órdenes y Posiciones](#gestión-de-órdenes-y-posiciones)
10. [Telegram Bot](#telegram-bot)
11. [Visualizador Web](#visualizador-web)
12. [Despliegue en VPS](#despliegue-en-vps)

---

## Descripción General

Bot de trading automatizado para **Binance Futures** que utiliza niveles de **Fibonacci** combinados con el indicador **RSI** para identificar oportunidades de entrada en corto (SHORT).

### Características Principales:
- ✅ Escaneo automático de **todos los pares USDT** de Binance Futures (~600+)
- ✅ Filtrado por RSI >= 70 (sobrecompra)
- ✅ Detección de swings Fibonacci válidos
- ✅ 4 casos de trading con diferentes configuraciones de TP/SL
- ✅ Paper Trading y Trading Real
- ✅ Notificaciones por Telegram
- ✅ Visualizador web en tiempo real
- ✅ TP Dinámico (promediado de posiciones)

---

## Arquitectura del Sistema

```
┌─────────────────────────────────────────────────────────────┐
│                        bot.py (Principal)                    │
│  - Coordina todo el sistema                                  │
│  - Loop principal de escaneo                                 │
│  - Monitor en tiempo real                                    │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│  scanner.py   │    │ paper_trading │    │binance_trading│
│               │    │     .py       │    │     .py       │
│ - Top 100     │    │               │    │               │
│   pares       │    │ - Simulación  │    │ - API Real    │
│ - RSI         │    │ - TP/SL       │    │ - TP/SL       │
│ - Fibonacci   │    │ - Historial   │    │ - Órdenes     │
│ - Casos 1-4   │    │               │    │               │
└───────────────┘    └───────────────┘    └───────────────┘
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              ▼
                    ┌───────────────┐
                    │ fibonacci.py  │
                    │               │
                    │ - ZigZag      │
                    │ - Swings      │
                    │ - Niveles Fib │
                    └───────────────┘
```

---

## Estrategia de Trading

### Condiciones de Entrada (SHORT)
1. **RSI >= 70** en timeframe 5m (sobrecompra)
2. **Swing Fibonacci válido** detectado:
   - High y Low identificados con ZigZag
   - Precio actual en zona de retroceso (entre 61.8% y 100%)

### Niveles de Fibonacci Utilizados
```
100% ─────── High (máximo del swing)
 90% ─────── 
 78.6% ───── Retroceso profundo
 75% ─────── 
 69% ─────── 
 61.8% ───── Retroceso dorado
 60% ─────── 
 55% ─────── 
 50% ─────── Retroceso medio
 45% ─────── 
  0% ─────── Low (mínimo del swing)
```

---

## Casos de Trading

### CASO 1: Precio entre 50% y 61.8%
**Configuración actual:**
- **Entrada:** 2 órdenes LIMIT
  - 1ra orden @ nivel 61.8%
  - 2da orden @ nivel 78.6%
- **Take Profit:** 45%
- **TP Dinámico:** 50% (cuando se ejecuta la 2da orden)
- **Stop Loss:** 100% (High)

**Flujo:**
```
Precio toca 61.8% → Abre 1ra posición SHORT
  │
  ├─→ Precio baja a 45% → TP ejecutado, 2da orden CANCELADA ✅
  │
  └─→ Precio sube a 78.6% → 2da orden ejecutada, posición PROMEDIADA
        │
        ├─→ TP se MUEVE de 45% a 50%
        │
        └─→ Precio baja a 50% → TP ejecutado ✅
```

### CASO 2: Precio entre 61.8% y 78.6%
**Configuración actual:**
- **Entrada:** MARKET + LIMIT
  - Orden MARKET inmediata
  - Orden LIMIT @ nivel 78.6%
- **Take Profit:** 45%
- **TP Dinámico:** 50% (cuando se ejecuta la orden LIMIT)
- **Stop Loss:** 100% (High)

**Flujo:**
```
Precio en zona 61.8%-78.6% → Abre SHORT MARKET inmediato
  │
  ├─→ Precio baja a 45% → TP ejecutado, orden LIMIT CANCELADA ✅
  │
  └─→ Precio sube a 78.6% → Orden LIMIT ejecutada, posición PROMEDIADA
        │
        ├─→ TP se MUEVE de 45% a 50%
        │
        └─→ Precio baja a 50% → TP ejecutado ✅
```

### CASO 3: Precio entre 78.6% y 100%
**Configuración actual:**
- **Entrada:** 1 orden LIMIT @ 78.6%
- **Take Profit:** 55%
- **Stop Loss:** 105%

**Flujo:**
```
Precio sube y toca 78.6% → Abre SHORT LIMIT
  │
  ├─→ Precio baja a 55% → TP ejecutado ✅
  │
  └─→ Precio sube a 105% → SL ejecutado ❌
```

### CASO 4: Precio encima del 100% (High)
**Configuración actual:**
- **Entrada:** MARKET inmediato
- **Take Profit:** 60%
- **Stop Loss:** 105%

**Flujo:**
```
Precio supera el High → Abre SHORT MARKET inmediato
  │
  ├─→ Precio baja a 60% → TP ejecutado ✅
  │
  └─→ Precio sigue subiendo a 105% → SL ejecutado ❌
```

---

## Resumen de Niveles por Caso

| Caso | Entrada | TP Inicial | TP Dinámico | SL |
|------|---------|------------|-------------|-----|
| **1** | LIMIT 61.8% + LIMIT 78.6% | 45% | 50% | 100% |
| **2** | MARKET + LIMIT 78.6% | 45% | 50% | 100% |
| **3** | LIMIT 78.6% | 55% | - | 105% |
| **4** | MARKET | 60% | - | 105% |

---

## Flujo de Ejecución

### Loop Principal (cada 60 segundos)

```
┌─────────────────────────────────────────────┐
│           INICIO DEL CICLO                  │
└─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│  1. Obtener todos los pares USDT (~600+)    │
│     (excluir pares prohibidos)              │
└─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│  2. Para cada par:                          │
│     - Calcular RSI (5m)                     │
│     - Si RSI < 70 → Descartar               │
└─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│  3. Para pares con RSI >= 70:               │
│     - Obtener velas (15m o 1h)              │
│     - Calcular ZigZag                       │
│     - Buscar swing Fibonacci válido         │
└─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│  4. Si hay swing válido:                    │
│     - Determinar Caso (1, 2, 3 o 4)         │
│     - Verificar margen disponible           │
│     - Ejecutar órdenes según el caso        │
└─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│  5. Monitoreo continuo:                     │
│     - Verificar TP/SL de posiciones         │
│     - Activar órdenes LIMIT pendientes      │
│     - Actualizar precios vía WebSocket      │
└─────────────────────────────────────────────┘
                    │
                    ▼
           [Esperar 60 segundos]
                    │
                    └──────────→ [REPETIR]
```

---

## Archivos del Proyecto

### Archivos Principales

| Archivo | Descripción |
|---------|-------------|
| `bot.py` | Punto de entrada principal. Contiene el loop de escaneo y monitor |
| `scanner.py` | Escanea pares, calcula RSI, detecta casos y ejecuta órdenes |
| `paper_trading.py` | Sistema de simulación con balance virtual |
| `binance_trading.py` | Conexión real con Binance Futures API |
| `fibonacci.py` | Cálculos de ZigZag, swings y niveles Fibonacci |
| `config.py` | Configuración global (balance, leverage, intervalos, etc.) |

### Archivos de Soporte

| Archivo | Descripción |
|---------|-------------|
| `telegram_bot.py` | Bot de Telegram para notificaciones y comandos |
| `logger.py` | Sistema de logging estructurado |
| `metrics.py` | Cálculo de métricas de rendimiento |

### Archivos de Datos

| Archivo | Descripción |
|---------|-------------|
| `trades.json` | Historial de operaciones (paper trading) |
| `shared_config.json` | Configuración compartida con el visor web |
| `.env` | Variables de entorno (API keys) - **NO SUBIR A GIT** |

### Archivos Web

| Archivo | Descripción |
|---------|-------------|
| `index.html` | Interfaz del visualizador web |
| `app.js` | Lógica del visualizador (gráficos, Fibonacci) |
| `styles.css` | Estilos del visualizador |

---

## Configuración

### Archivo `config.py`

```python
# Balance y apalancamiento
INITIAL_BALANCE = 100.0      # Balance inicial (paper trading)
LEVERAGE = 20                # Apalancamiento
MARGIN_PER_TRADE = 3.0       # Margen por operación
MIN_AVAILABLE_MARGIN = 2.0   # Margen mínimo para operar

# Escaneo
TOP_PAIRS_LIMIT = 600        # Cantidad de pares a escanear (~todos)
RSI_THRESHOLD = 70           # RSI mínimo para entrada
SCAN_INTERVAL = 66           # Segundos entre escaneos
FIRST_SCAN_DELAY = 2         # Delay antes del primer escaneo

# Timeframes
TIMEFRAME = "15m"            # Timeframe para Fibonacci
CANDLE_LIMIT = 200           # Cantidad de velas a analizar

# Pares excluidos
EXCLUDED_PAIRS = [
    "USDCUSDT",              # Stablecoins
    "1000BONKUSDT",          # Pares problemáticos
    ...
]
```

### Archivo `.env`

```env
# Binance API (Trading Real)
BINANCE_API_KEY=tu_api_key
BINANCE_API_SECRET=tu_api_secret

# Telegram Bot
TELEGRAM_BOT_TOKEN=tu_token
TELEGRAM_CHAT_ID=tu_chat_id
```

---

## Modos de Operación

### 1. Paper Trading (Simulación)
```bash
python bot.py
# Seleccionar: 1) Paper Trading
```

- Usa balance virtual ($100 por defecto)
- Simula órdenes sin dinero real
- Guarda historial en `trades.json`
- Ideal para probar estrategias

### 2. Trading Real
```bash
python bot.py
# Seleccionar: 2) Trading Real
```

- Conecta con Binance Futures API
- Ejecuta órdenes reales
- Requiere API keys configuradas en `.env`
- ⚠️ **PRECAUCIÓN:** Usa dinero real

### 3. Monitor Only
```bash
python bot.py
# Seleccionar: 3) Monitor
```

- Solo muestra información del mercado
- No ejecuta ninguna operación
- Útil para observar sin riesgo

---

## Gestión de Órdenes y Posiciones

### Órdenes Vinculadas (linked_order_id)

En los Casos 1 y 2, las órdenes secundarias están **vinculadas** a la posición principal:

```
Posición Principal (MARKET o 1ra LIMIT)
    │
    └── Orden Vinculada (2da LIMIT @ 78.6%)
           │
           └── linked_order_id = ID de la posición principal
```

### Cancelación Automática

Cuando una posición se cierra por **TP**:
1. El sistema detecta el cierre
2. Busca órdenes vinculadas (`linked_order_id`)
3. **Cancela automáticamente** las órdenes pendientes

**Paper Trading:** `_cancel_linked_orders()` en `paper_trading.py`
**Trading Real:** `cancel_pending_orders_for_symbol()` en `binance_trading.py`

### TP Dinámico (Promediado)

Cuando se ejecuta la orden vinculada:
1. Se promedia el precio de entrada
2. Se calcula el nuevo TP (de 45% a 50%)
3. Se cancela el TP anterior
4. Se crea nuevo TP con la cantidad total

```
Posición inicial: Entry $100, Qty 10
Orden vinculada ejecutada: Entry $105, Qty 10
────────────────────────────────────────
Posición promediada: Entry $102.50, Qty 20
TP anterior: $95 (45%) → CANCELADO
TP nuevo: $97.50 (50%) → CREADO
```

---

## Telegram Bot

### Comandos Disponibles

| Comando | Descripción |
|---------|-------------|
| `/status` | Estado actual del bot y posiciones |
| `/balance` | Balance y margen disponible |
| `/positions` | Posiciones abiertas con PnL |
| `/orders` | Órdenes pendientes |
| `/history` | Últimas 10 operaciones cerradas |
| `/history 2` | Historial filtrado por Caso 2 |
| `/metrics` | Métricas de rendimiento |
| `/help` | Lista de comandos |

### Notificaciones Automáticas

- 🟢 **Apertura de posición:** Símbolo, precio, caso
- 🔴 **Cierre de posición:** Símbolo, precio, PnL
- ⚡ **Orden LIMIT ejecutada:** Símbolo, precio
- 🔄 **TP Dinámico activado:** Nuevo TP

---

## Visualizador Web

### Iniciar el Servidor

```bash
# Usando Python
python -m http.server 8080

# Acceder en navegador
http://localhost:8080
```

### Características

1. **Gráfico de velas** con Lightweight Charts
2. **Niveles Fibonacci** dibujados automáticamente
3. **Posiciones abiertas** y órdenes pendientes
4. **Modo Análisis** para revisar historial
5. **Editor de niveles Fibonacci** personalizable

### Modo Análisis

1. Cargar archivo JSON histórico
2. Navegar entre operaciones con ◀ ▶
3. Ver niveles Fibonacci de cada trade
4. Analizar entradas y salidas

---

## Despliegue en VPS

### Requisitos
- Ubuntu 20.04+ o Debian 10+
- Python 3.9+
- 1GB RAM mínimo

### Instalación

```bash
# Clonar repositorio
git clone https://github.com/tu-usuario/trading-bot.git
cd trading-bot

# Crear entorno virtual
python3 -m venv venv
source venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt

# Configurar variables de entorno
cp .env.example .env
nano .env  # Editar con tus API keys
```

### Ejecución con Screen

```bash
# Crear sesión
screen -S bot

# Activar entorno y ejecutar
source venv/bin/activate
python bot.py

# Desconectar: Ctrl+A, luego D
# Reconectar: screen -r bot
```

### Ejecución con Systemd

```bash
# Crear servicio
sudo nano /etc/systemd/system/trading-bot.service
```

```ini
[Unit]
Description=Trading Bot Fibonacci
After=network.target

[Service]
Type=simple
User=tu_usuario
WorkingDirectory=/ruta/al/bot
ExecStart=/ruta/al/bot/venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
# Habilitar e iniciar
sudo systemctl enable trading-bot
sudo systemctl start trading-bot

# Ver logs
sudo journalctl -u trading-bot -f
```

### Actualizar desde GitHub

```bash
cd /ruta/al/bot
git pull origin main
sudo systemctl restart trading-bot
```

---

## Métricas de Rendimiento

El sistema calcula automáticamente:

- **Total Trades:** Número de operaciones
- **Win Rate:** Porcentaje de operaciones ganadoras
- **Profit Factor:** Ganancias / Pérdidas
- **Max Drawdown:** Máxima caída del balance
- **Average Win/Loss:** Promedio de ganancias y pérdidas
- **Sharpe Ratio:** Rendimiento ajustado por riesgo

Ver con `/metrics` en Telegram o al cerrar el bot.

---

## Troubleshooting

### Error: "Margen insuficiente"
- Verificar `MARGIN_PER_TRADE` en `config.py`
- Asegurar balance suficiente

### Error: "API Key inválida"
- Verificar keys en `.env`
- Asegurar permisos de Futures habilitados

### Bot no detecta señales
- Verificar `RSI_THRESHOLD` (75 por defecto)
- Revisar `EXCLUDED_PAIRS` en `config.py`
- Aumentar `CANDLE_LIMIT` si es necesario

### Órdenes no se ejecutan (Real Trading)
- Verificar balance en Binance
- Revisar mínimos del par (`minQty`, `minNotional`)
- Verificar que el leverage esté configurado

---

## Historial de Cambios Recientes

### Enero 2026
- ✅ Caso 1 y 2: TP movido a 45%, TP Dinámico a 50%, SL a 100%
- ✅ Caso 3: TP movido a 55%
- ✅ Caso 4: TP movido a 60%
- ✅ Cancelación automática de órdenes vinculadas en Trading Real
- ✅ Editor de niveles Fibonacci en visor web
- ✅ Modo Análisis para revisar historial

---

## Contacto y Soporte

Para reportar bugs o sugerir mejoras, crear un Issue en el repositorio de GitHub.

---

*Última actualización: Enero 2026*
