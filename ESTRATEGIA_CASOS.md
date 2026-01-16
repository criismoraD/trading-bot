# 📊 Estrategia de Trading Fibonacci - Documentación de Casos

Este documento explica en detalle cómo funcionan los **4 casos de entrada** del bot de trading basado en retrocesos de Fibonacci para operaciones **SHORT** en Binance Futures.

---

## 🎯 Concepto General

El bot detecta **swings** (movimientos de precio entre un máximo y un mínimo) usando el indicador ZigZag, y luego traza niveles de Fibonacci sobre ese swing. Dependiendo de **dónde esté el precio actual** dentro de esos niveles, se determina el **caso de entrada**.

### Niveles Fibonacci Clave
```
┌─────────────────────────────────────────────────────────────────┐
│  100%  ────────────────── HIGH (Máximo del swing)               │
│   90%  ────────────────── ⛔ INVALIDACIÓN TOTAL                 │
│  78.6% ────────────────── Entrada secundaria (Limit)            │
│   75%  ────────────────── Límite superior Caso 4                │
│   69%  ────────────────── Límite superior Caso 3                │
│  61.8% ────────────────── Entrada primaria (Limit) / Golden     │
│   60%  ────────────────── TP Dinámico (cuando se promedian)     │
│   55%  ────────────────── Límite inferior Caso 1 / TP inicial   │
│   50%  ────────────────── Nivel 50%                             │
│    0%  ────────────────── LOW (Mínimo del swing)                │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🟢 CASO 1: Precio entre 55% y 61.8%

### Situación
El precio está en la zona más conservadora, cerca del nivel 55%. Aún no ha tocado el "Golden Ratio" (61.8%).

### Acciones del Bot
1. **Coloca 2 órdenes LIMIT:**
   - 📍 **Orden Límite #1** en el nivel **61.8%**
   - 📍 **Orden Límite #2** en el nivel **78.6%** (vinculada a la primera)

2. **Take Profit inicial:** Nivel **55%**

### Escenarios Posibles

#### ✅ Escenario A: Solo se ejecuta la Orden #1 y toca TP
```
Precio sube → Toca 61.8% → Se ABRE posición SHORT
Precio baja → Toca 55% (TP) → Se CIERRA posición con GANANCIA
Orden #2 (78.6%) → Queda pendiente o se cancela automáticamente
```

**Resultado:** Ganancia basada en el movimiento de 61.8% a 55%.

#### ⚡ Escenario B: Se ejecutan AMBAS órdenes (Promediado)
```
Precio sube → Toca 61.8% → Se ABRE posición SHORT (Orden #1)
Precio sigue subiendo → Toca 78.6% → Se EJECUTA Orden #2
                     → Las posiciones se FUSIONAN (averaging)
                     → Nuevo precio de entrada = PROMEDIO PONDERADO
                     → TP se MUEVE DINÁMICAMENTE de 55% a 60%
Precio baja → Toca 60% (nuevo TP) → Se CIERRA posición promediada
```

**Cálculo del promedio:**
```
Precio Promedio = (Entry1 × Qty1 + Entry2 × Qty2) / (Qty1 + Qty2)
```

**¿Por qué se mueve el TP a 60%?**
Al promediar, el precio de entrada es más alto (mejor para SHORT), por lo que el TP puede estar en un nivel más alto (60% en vez de 55%) y aún así generar ganancia.

#### ❌ Escenario C: El precio sube sin control
```
Precio sube → Toca 61.8% → Se ABRE posición
Precio sigue subiendo → Toca 78.6% → Se PROMEDIAN
Precio sigue subiendo → Toca 90% (Stop Loss implícito)
                     → El swing se INVALIDA
                     → Posición en PÉRDIDA
```

---

## 🟡 CASO 2: Precio entre 61.8% y 69%

### Situación
El precio ya pasó el Golden Ratio (61.8%) y está en zona activa.

### Acciones del Bot
1. **Orden MARKET inmediata** al precio actual
2. **Orden LIMIT adicional** en el nivel **78.6%** (vinculada)
3. **Orden LIMIT adicional** en el nivel **120%** (vinculada) ← NUEVO
4. **Take Profit inicial:** Nivel **55%**
5. **Stop Loss:** Nivel **130%** ← NUEVO

### Escenarios Posibles

#### ✅ Escenario A: Solo la orden Market y toca TP
```
Bot detecta Case 2 → Se ABRE posición SHORT inmediatamente (MARKET)
                   → Se coloca Orden Límite en 78.6%
                   → Se coloca Orden Límite en 120%
Precio baja → Toca 55% (TP) → Se CIERRA posición con GANANCIA
Órdenes 78.6% y 120% → Se cancelan automáticamente
```

#### ⚡ Escenario B: Market + una o más Limits se promedian
```
Bot detecta Case 2 → Se ABRE posición SHORT (MARKET a precio actual)
Precio sube → Toca 78.6% → Se EJECUTA orden Limit #1
           → Las posiciones se FUSIONAN
           → TP se MUEVE de 55% → 60%
Precio sigue subiendo → Toca 120% → Se EJECUTA orden Limit #2
                      → Se FUSIONA de nuevo (triple promedio)
Precio baja → Toca 60% → Se CIERRA con ganancia
```

#### ❌ Escenario C: Stop Loss
```
Precio sube sin control → Toca 130% → STOP LOSS
                       → Posición cerrada con PÉRDIDA
```

---

## 🟠 CASO 3: Precio entre 69% y 75%

### Situación
El precio está alto, cerca de la zona de "último recurso" antes de invalidación.

### Acciones del Bot
1. **Una sola orden LIMIT** en el nivel **78.6%**
2. **Take Profit:** Nivel **62%** (más conservador)

### Escenarios Posibles

#### ✅ Escenario A: Limit se ejecuta y toca TP
```
Bot detecta Case 3 → Coloca Orden Límite en 78.6%
Precio sube → Toca 78.6% → Se ABRE posición SHORT
Precio baja → Toca 62% (TP) → Se CIERRA con GANANCIA
```

#### ❌ Escenario B: Precio invalida el swing
```
Precio sube → Toca 78.6% → Se ABRE posición
Precio sigue subiendo → Toca 90% → INVALIDACIÓN
                      → Posición en pérdida
```

---

## 🔴 CASO 4: Precio entre 75% y 90%

### Situación
El precio está muy alto, en zona de riesgo máximo pero también de máxima recompensa.

### Acciones del Bot
1. **Orden MARKET inmediata** (entrada agresiva)
2. **Take Profit:** Nivel **62%**
3. **NO hay orden secundaria** (no hay más espacio para promediar)

### Escenarios Posibles

#### ✅ Escenario A: Éxito rápido
```
Bot detecta Case 4 → Se ABRE posición SHORT inmediatamente (MARKET)
Precio baja rápidamente → Toca 62% (TP) → Se CIERRA con GANANCIA
```

**Potencial de ganancia:** Es el caso con MAYOR potencial (entrada alta, TP bajo).

#### ❌ Escenario B: Invalidación
```
Bot detecta Case 4 → Se ABRE posición SHORT
Precio sigue subiendo → Toca 90% → STOP LOSS / INVALIDACIÓN
                      → PÉRDIDA significativa
```

---

## 📐 Tabla Resumen de Casos

| Caso | Zona de Precio | Tipo de Entrada | Órdenes | Take Profit | Stop Loss |
|------|----------------|-----------------|---------|-------------|-----------|
| **1** | 55% - 61.8% | 2× LIMIT | 61.8% + 78.6% | 55% (→60% si promedia) | **90%** |
| **2** | 61.8% - 69% | MARKET + 2× LIMIT | Inmediata + 78.6% + 120% | 55% (→60% si promedia) | **130%** |
| **3** | 69% - 75% | LIMIT | 78.6% | 62% | **105%** |
| **4** | 75% - 90% | MARKET | Inmediata | 62% | **105%** |

---

## 🔄 Sistema de Promediado (Averaging)

Cuando dos órdenes del mismo par se ejecutan, el bot las **fusiona en una sola posición**:

### Fórmula
```
Precio Entrada Promedio = (Precio1 × Cantidad1) + (Precio2 × Cantidad2)
                          ─────────────────────────────────────────────
                                    Cantidad1 + Cantidad2
```

### Ejemplo Práctico (BTCUSDT)
```
Swing: High = $100,000 | Low = $90,000 | Rango = $10,000

Nivel 61.8% = $90,000 + ($10,000 × 0.618) = $96,180
Nivel 78.6% = $90,000 + ($10,000 × 0.786) = $97,860
Nivel 55%   = $90,000 + ($10,000 × 0.55)  = $95,500
Nivel 60%   = $90,000 + ($10,000 × 0.60)  = $96,000

─────────────────────────────────────────────────────
CASO 1: Precio actual $95,800 (entre 55% y 61.8%)

Bot coloca:
  • Orden Límite #1: SELL @ $96,180 (nivel 61.8%)
  • Orden Límite #2: SELL @ $97,860 (nivel 78.6%)
  • TP inicial: $95,500 (nivel 55%)

Escenario de promediado:
  • Orden #1 se ejecuta: SHORT 0.0312 BTC @ $96,180
  • Orden #2 se ejecuta: SHORT 0.0306 BTC @ $97,860
  
  Precio promedio = (96180 × 0.0312 + 97860 × 0.0306) / (0.0312 + 0.0306)
                  = (3000.82 + 2996.52) / 0.0618
                  = $97,010.36
  
  Nuevo TP = $96,000 (nivel 60%)
  
  Ganancia potencial: $97,010 → $96,000 = 1.04% × 10x leverage = 10.4%
```

---

## ⛔ Sistema de Invalidación

### Invalidación Total (90%)
Si el precio toca el **90%** del rango en cualquier momento después del Low, **todo el swing se invalida** y no se abren operaciones.

### Invalidación Parcial (61.8% / 78.6%)
El bot implementa un sistema de **invalidación parcial**:

| Si tocó previamente... | Casos Invalidados | Casos Válidos |
|------------------------|-------------------|---------------|
| Nada | Ninguno | 1, 2, 3, 4 |
| 61.8% | 1, 2 | 3, 4 |
| 78.6% | 1, 2, 3 | Solo 4 |
| 90% | Todos | Ninguno |

Esto evita que el bot entre en zonas que ya fueron "agotadas" por el precio.

---

## 📝 Notas Importantes

1. **Todas las operaciones son SHORT** (apostamos a que el precio bajará).
2. **El margen por operación es configurable** (default: $3 USDT).
3. **El apalancamiento es 10x** (configurable en `shared_config.json`).
4. **El TP dinámico (60%) solo se activa cuando se promedian posiciones.**
5. **Las órdenes límite secundarias (78.6%) están "vinculadas" a la primera**, lo que permite el cálculo automático del promedio.

---

## 🛠️ Configuración en `shared_config.json`

```json
{
  "trading": {
    "case_1_min": 0.55,
    "case_1_max": 0.618,
    "case_2_min": 0.618,
    "case_2_max": 0.69,
    "case_3_min": 0.69,
    "case_3_max": 0.75,
    "case_4_min": 0.75,
    "case_4_max": 0.90
  },
  "fibonacci": {
    "tp_levels": {
      "case_1_initial": 0.55,
      "case_2_initial": 0.55,
      "case_3": 0.62,
      "case_4": 0.62,
      "dynamic_tp": 0.60
    }
  }
}
```

---

*Documento generado automáticamente para el Fibonacci Trading Bot v1.0*
