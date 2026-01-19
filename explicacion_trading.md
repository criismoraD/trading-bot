# Explicación del Algoritmo de Trading (ZigZag + Fibonacci)

Este documento detalla el funcionamiento interno del bot, desde la detección de puntos pivote hasta la ejecución de órdenes según los 4 casos + el caso especial 1++.

## 1. Flujo del ZigZag

El cálculo del ZigZag es el primer paso para estructurar el mercado. Se utiliza para identificar **Highs (Máximos)** y **Lows (Mínimos)** significativos.

### Lógica de Construcción (`calculate_zigzag`)
1.  **Detección de Pivotes**: Se analiza una ventana de velas (basada en el parámetro `depth`) para encontrar máximos y mínimos locales.
2.  **Filtrado por Desviación**: Para que un nuevo movimiento sea considerado, el precio debe haberse movido un porcentaje mínimo (configurado en `deviation`) desde el último punto.
3.  **Alternancia Estricta**: El algoritmo fuerza una secuencia **High -> Low -> High -> Low**.
    *   Si se detectan dos Highs consecutivos, se mantiene solo el más alto.
    *   Si se detectan dos Lows consecutivos, se mantiene solo el más bajo.
4.  **Actualización en Tiempo Real**: Si el precio actual supera el último High o rompe el último Low sin completar la desviación para un nuevo punto, se actualiza el punto extremo existente.

---

## 2. Validación de Swings de Fibonacci

Una vez calculados los puntos ZigZag, el algoritmo busca un "Swing" válido para proyectar los niveles de Fibonacci. El bot opera principalmente en **Short**, buscando swings bajistas (High a Low) para vender en los retrocesos.

### Proceso de Búsqueda (`find_valid_fibonacci_swing`)
El sistema recorre los Highs detectados por el ZigZag, empezando por el más reciente (Path 1), y busca conectar con el Low más bajo posterior.

### Reglas de Invalidación y Filtrado
Para cada Swing candidato (High -> Low), se aplican las siguientes validaciones:

1.  **Regla del 90% (Invalidación Total)**:
    *   Si *cualquier* vela posterior al Low del swing ha tocado el nivel **90%** del retroceso, el swing se considera "quemado" o invalidado. Se descarta inmediatamente y se busca el siguiente High.

2.  **Regla de "Toques Previos" (Min Valid Case)**:
    El algoritmo verifica qué niveles de Fibonacci ya han sido tocados por mechas de velas anteriores dentro del mismo retroceso. Esto define qué casos de trading siguen disponibles:

    *   🔴 **Si tocó 78.6%**: Ya se "gastaron" los casos 1, 2 y 3. **Solo el Caso 4 es válido**.
    *   🟠 **Si tocó 69%**: Ya se "gastaron" los casos 1 y 2. **Solo Casos 3 y 4 son válidos**.
    *   🟡 **Si tocó 61.8%**: Ya se "gastó" el caso 1. **Solo Casos 2, 3 y 4 son válidos**.
    *   🟢 **Si no tocó 61.8%**: **Todos los casos (1, 2, 3, 4) son válidos**.

3.  **Validación de Zona Actual**:
    El precio actual (Current Price) debe estar dentro o por encima de la zona de activación del caso mínimo válido.
    *   *Ejemplo*: Si `Min Valid Case = 2` (porque ya tocó el 61.8%), el precio actual debe estar por encima del nivel 61.8%. Si está por debajo (ej. 58%), se considera que ya dio la entrada y se fue, por lo tanto se ignora este swing.

---

## 3. Casos de Trading y Escenarios

El bot clasifica la oportunidad de trading en uno de 4 casos (más un caso especial) dependiendo de dónde se encuentre el precio actual respecto a los niveles de Fibonacci.

### Niveles Clave
*   **Zona C1**: 55% - 61.8%
*   **Zona C2**: 61.8% - 69%
*   **Zona C3**: 69% - 78.6%
*   **Zona C4**: 78.6% - 90%
*   **Invalidación**: > 90%

### Descripción de los Casos

#### 🟢 CASO 1: Entrada Confirmada (Limit)
*   **Condición**: Precio actual entre **55% y 61.8%**.
*   **Requisito**: El nivel 61.8% **NO** debe haber sido tocado previamente.
*   **Operación**: Orden **LIMIT SELL** al **61.8%**.
*   **Take Profit**: Nivel 45%.
*   **Lógica**: Esperamos que el precio suba un poco más para llenar la orden en el nivel aureo (Golden Pocket) y caer.

#### 🟡 CASO 2: Entrada Agresiva (Market)
*   **Condición**: Precio actual entre **61.8% y 69%**.
*   **Requisito**: El nivel 69% **NO** debe haber sido tocado previamente (para evitar entrar tarde en un swing profundo).
*   **Operación**: Orden **MARKET SELL** inmediata.
*   **Take Profit**: Nivel 45%.
*   **Lógica**: El precio ya está en la zona del Golden Pocket extendida. Se entra a mercado para no perder la bajada.

#### 🟠 CASO 3: Entrada Profunda (Limit)
*   **Condición**: Precio actual entre **69% y 78.6%**.
*   **Requisito**: El nivel 78.6% **NO** debe haber sido tocado previamente.
*   **Operación**: Orden **LIMIT SELL** al **78.6%**.
*   **Take Profit**: Nivel 55%.
*   **Lógica**: El precio ha roto el 69%, indicando fuerza alcista en el retroceso. Esperamos una reacción en el último bastión (78.6%) antes de la invalidación.

#### 🔴 CASO 4: Entrada Extrema (Market)
*   **Condición**: Precio actual entre **78.6% y 90%**.
*   **Requisito**: Precio por debajo del 90%.
*   **Operación**: Orden **MARKET SELL** inmediata.
*   **Take Profit**: Nivel 60%.
*   **Lógica**: Situación de alto riesgo/recompensa. El precio está muy cerca de la invalidación. Se vende a mercado buscando un rechazo rápido antes del 90%.

---

## 4. El Caso Especial: C1++ (Path 2)

Este es un mecanismo de cobertura inteligente. Si el bot entra en una operación "profunda" (Casos 2, 3 o 4), significa que el retroceso ha ido más allá de lo ideal. El bot activa entonces un escáner secundario para buscar un **Swing Mayor**.

### ¿Cómo funciona? (`_search_and_place_c1pp`)
1.  **Trigger**: Se activa solo después de colocar una orden de Caso 2, 3 o 4.
2.  **Búsqueda de Historia**: Busca en el pasado puntos ZigZag de tipo **High** que sean **más altos** que el High del swing actual.
3.  **Construcción de Swing Mayor**:
    *   Toma ese High Histórico y el Low más bajo detectado desde entonces.
    *   Traza un nuevo Fibonacci masivo.
4.  **Validación C1++**:
    *   Verifica que el nivel 90% de este nuevo swing mayor NO haya sido tocado.
    *   Verifica que el nivel 61.8% de este nuevo swing mayor **NO** haya sido tocado aún.
    *   Verifica que el precio actual esté **por debajo** del 61.8%.
5.  **Ejecución**:
    *   Si se cumplen las condiciones, coloca una **LIMIT SELL** en el **61.8% del Swing Mayor**.
    *   **Take Profit**: 45% (del swing mayor).

**Objetivo**: Si la operación original (C2/C3/C4) sale mal y el precio sigue subiendo, es muy probable que esté yendo a buscar el 61.8% de una estructura fractalmente mayor. El C1++ deja esa orden lista para atrapar ese movimiento.

---

## Resumen de Validaciones de Entrada (Doble Check)

Antes de poner cualquier orden, el sistema hace una última validación de seguridad (`determine_trading_case` -> validación final):

*   **Check de Mechas Traicioneras**: Revisa vela por vela desde el Low hasta la vela actual. Si alguna mecha ya tocó el nivel de entrada de la orden que queremos poner (ej. ya tocó el 61.8% para un Caso 1, o ya tocó el limit del 78.6% para un Caso 3), la orden se cancela.
*   **Propósito**: Evitar poner órdenes Limit que "deberían haberse llenado ya" o entrar en setups que ya cumplieron su recorrido y están rebotando.
