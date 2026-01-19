# 📘 Guía de Lógica del Bot de Trading (Fibonacci + ZigZag)

Este documento explica cómo el bot detecta oportunidades de trading, cómo traza los niveles de Fibonacci y cómo funcionan los diferentes "Casos" o escenarios de entrada.

---

## 📐 1. Indicador ZigZag (Estructura de Mercado)

El bot utiliza un algoritmo de **ZigZag** personalizado para identificar los puntos máximos (Highs) y mínimos (Lows) significativos del mercado. Esto es fundamental para trazar los movimientos de precio (Swings).

### ¿Cómo funciona?
El algoritmo (`fibonacci.py`) sigue estos pasos:
1.  **Detección de Pivotes**: Escanea las velas buscando puntos que sean máximos o mínimos locales en una ventana de tiempo definida (`depth`).
2.  **Filtrado por Desviación**: Solo se confirma un nuevo punto si el precio se ha movido un porcentaje mínimo (ej. 5%) desde el punto anterior. Esto elimina el "ruido" de pequeños movimientos.
3.  **Alternancia**: Asegura que siempre haya una secuencia Hig -> Low -> High -> Low...
4.  **Búsqueda Robusta**: Si hay múltiples máximos consecutivos, se queda con el más alto (o el más bajo para los mínimos).

**Objetivo:** Encontrar un **Swing Bajista** (un movimiento desde un High reciente hasta un Low).

---

## 🔢 2. Trazado de Fibonacci

Una vez identificado un Swing válido (High → Low), el bot traza los niveles de retroceso de Fibonacci para buscar entradas en **SHORT** (Venta).

**Rango del Swing:**
*   **0% (Base):** Precio del Low.
*   **100% (Tope):** Precio del High.

El bot calcula niveles intermedios donde es probable que el precio reboten hacia abajo:
*   **45% / 50% / 55%**: Zonas de toma de ganancias (Take Profit).
*   **61.8% ("Golden Pocket")**: Nivel clave de entrada.
*   **78.6%**: Nivel profundo de entrada.
*   **90%**: Nivel de **Invalidación** (Stop Loss conceptual del swing).

**Regla de Oro:** Si el precio toca o supera el nivel del **90%**, el swing se considera "roto" o invalidado y se descarta.

---

## 🎯 3. Casos de Trading (Escenarios)

El bot clasifica la oportunidad en uno de **4 Casos** dependiendo de dónde se encuentre el precio actual respecto al retroceso del Fibonacci.

El sistema utiliza **2 Caminos (Paths)** para encontrar oportunidades:

### 🛤️ Camino 1: Swing Principal (High más reciente)

Se evalúa la posición actual del precio dentro del rango del swing.

#### **🔴 CASO 4: Zona Extrema (75% - 90%)**
*   **Escenario:** El precio ha subido mucho y está muy cerca de invalidar, pero ofrece un ratio riesgo/beneficio muy agresivo.
*   **Acción:** **MARKET ORDER** (Venta inmediata).
*   **Take Profit:** 60% del retroceso.
*   **Riesgo:** Alto (Stop Loss cerca, al 90%).

#### **🟠 CASO 3: Zona Alta (69% - 75%)**
*   **Escenario:** El precio está alto, pero preferimos esperar una mejor entrada en el 78.6%.
*   **Acción:** **LIMIT ORDER** en el nivel **78.6%**.
*   **Take Profit:** 55% del retroceso.
*   **Validación:** Se verifica que el precio no haya tocado ya el 78.6% recientemente (para no entrar tarde).

#### **🟡 CASO 2: Zona Media-Alta (61.8% - 69%)**
*   **Escenario:** El precio está justo en la "Golden Zone" (encima del 61.8%).
*   **Acción:** **MARKET ORDER** (Venta inmediata).
*   **Take Profit:** 45% del retroceso.

#### **🟢 CASO 1: Zona de Espera (55% - 61.8%)**
*   **Escenario:** El precio ha rebotado un poco (pasó el 55%) pero aún no llega a la zona óptima de entrada.
*   **Acción:** **LIMIT ORDER** en el nivel **61.8%**.
*   **Take Profit:** 45% del retroceso.
*   **Condición:** Si el precio ya tocó el 61.8% en este swing previamente, el Caso 1 se invalida (ya dio entrada).

---

### 🛤️ Camino 2: Caso 1++ (Swing Alternativo)

Este es un sistema avanzado. Si el bot entra en un Caso 2, 3 o 4 (swing "pequeño" o reciente), inmediatamente busca un **"Plan B"** o cobertura en una estructura mayor.

#### **🟣 CASO 1++ (Cobertura)**
*   **Lógica:** Busca un **High Anterior más alto** (a la izquierda en el gráfico) para trazar un Fibonacci más grande.
*   **Acción:** Coloca una **LIMIT ORDER** en el **61.8%** de este swing mayor.
*   **Take Profit:** 45% de este swing mayor.
*   **Objetivo:** Si el precio rompe el swing pequeño (stop loss), es probable que frene en el 61.8% del swing grande, recuperando pérdidas.

---

## 🛑 Reglas de Invalidación y Seguridad

1.  **Toque del 90%:** Si cualquier vela toca el 90% del retroceso, todo el swing se cancela.
2.  **Toque Previo de Entrada:**
    *   Para **Caso 3**: Si el precio ya tocó el 78.6% antes, no se pone la orden Limit (se asume que la oportunidad ya pasó).
    *   Para **Caso 1**: Si el precio ya tocó el 61.8% antes, no se pone la orden Limit.
3.  **RSI:** El bot solo busca operaciones si el RSI (14 periodos) en 5 minutos está por encima del umbral (ej. 75), indicando sobrecompra.

---

## 📊 Resumen Visual

```text
      High (100%) ──────────────────────────
           |
           |      [INVALIDACIÓN > 90%]
           |
      90%  ├────────────────────────────────  <-- Stop Loss Técnico de la Estructura/Swing
           |      🔴 CASO 4 (Market)
      78.6%├────────────────────────────────  <-- Entrada Limit (Caso 3)
           |      🟠 CASO 3 (Wait Limit)
      75%  ├────────────────────────────────
           |
      69%  ├────────────────────────────────
           |      🟡 CASO 2 (Market)
      61.8%├────────────────────────────────  <-- Entrada Limit (Caso 1 / 1++)
           |      🟢 CASO 1 (Wait Limit)
      55%  ├────────────────────────────────  <-- Zona mínima para considerar trade
           |
      50%  ├────────────────────────────────  <-- TP Común
           |
      45%  ├────────────────────────────────  <-- TP Agresivo
           |
           |
      Low (0%) ─────────────────────────────
```
