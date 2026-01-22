"""
Módulo de Fibonacci para detección de niveles y swings
SINCRONIZADO con el algoritmo de app.js
"""
from typing import List, Dict, Optional
from dataclasses import dataclass
import json
import os

from config import ZIGZAG_CONFIGS, FIBONACCI_LEVELS

# Cargar configuración de trading desde shared_config.json
_config_path = os.path.join(os.path.dirname(__file__), "shared_config.json")
_shared_config = {}
if os.path.exists(_config_path):
    with open(_config_path, 'r') as f:
        _shared_config = json.load(f)

_trading = _shared_config.get("trading", {})
CASE_1_MIN = _trading.get("case_1_min", 0.58)
CASE_1_MAX = _trading.get("case_1_max", 0.68)
# Caso 2 eliminado - ya no existe
CASE_3_MIN = _trading.get("case_3_min", 0.68)
CASE_3_MAX = _trading.get("case_3_max", 0.786)
CASE_4_MIN = _trading.get("case_4_min", 0.786)
CASE_4_MAX = _trading.get("case_4_max", 0.90)

# Niveles de invalidación (desde config o defaults)
_fibonacci = _shared_config.get("fibonacci", {})
INVALIDATION_LEVEL_PRIMARY = _fibonacci.get("invalidation_level", 0.90)
INVALIDATION_LEVEL_SECONDARY = _fibonacci.get("invalidation_level_secondary", 0.786)


@dataclass
class ZigZagPoint:
    index: int
    time: int
    price: float
    type: str  # 'high' o 'low'


@dataclass
class FibonacciSwing:
    high: ZigZagPoint
    low: ZigZagPoint
    levels: Dict[str, float]
    is_valid: bool = True
    current_candle_at_55: bool = False
    min_valid_case: int = 1  # Mínimo caso válido (1=todos, 3=desde C3, 4=solo C4)
    path: int = 1  # Camino: 1 = normal, 2 = swing alternativo (High movido a izquierda)


def get_zigzag_config(timeframe: str) -> dict:
    """Obtener configuración ZigZag según timeframe"""
    return ZIGZAG_CONFIGS.get(timeframe, ZIGZAG_CONFIGS["1h"])


def calculate_zigzag(candle_data: List[dict], timeframe: str = "1h") -> List[ZigZagPoint]:
    """
    Calcular puntos ZigZag - VERSIÓN ROBUSTA
    Detecta máximos y mínimos locales significativos con mejor precisión
    """
    config = get_zigzag_config(timeframe)
    deviation = config["deviation"] / 100
    depth = config["depth"]
    
    data = candle_data
    if len(data) < depth * 2:
        return []
    
    # ===== FASE 1: Encontrar TODOS los pivotes potenciales =====
    # Usamos una ventana más flexible
    potential_pivots = []
    
    for i in range(depth, len(data) - 1):  # Hasta la penúltima vela
        is_high = True
        is_low = True
        
        # Comparar con las velas en la ventana
        for j in range(max(0, i - depth), min(len(data), i + depth + 1)):
            if j == i:
                continue
            if data[j]["high"] >= data[i]["high"]:
                is_high = False
            if data[j]["low"] <= data[i]["low"]:
                is_low = False
        
        if is_high:
            potential_pivots.append({
                "index": i,
                "price": data[i]["high"],
                "type": "high"
            })
        if is_low:
            potential_pivots.append({
                "index": i,
                "price": data[i]["low"],
                "type": "low"
            })
    
    # También agregar extremos de las últimas velas
    last_n = min(depth, len(data) - 1)
    if last_n > 0:
        last_section = data[-last_n:]
        max_idx = len(data) - last_n + max(range(len(last_section)), key=lambda x: last_section[x]["high"])
        min_idx = len(data) - last_n + min(range(len(last_section)), key=lambda x: last_section[x]["low"])
        
        # Solo añadir si no existen ya
        if not any(p["index"] == max_idx and p["type"] == "high" for p in potential_pivots):
            potential_pivots.append({
                "index": max_idx,
                "price": data[max_idx]["high"],
                "type": "high"
            })
        if not any(p["index"] == min_idx and p["type"] == "low" for p in potential_pivots):
            potential_pivots.append({
                "index": min_idx,
                "price": data[min_idx]["low"],
                "type": "low"
            })
    
    if not potential_pivots:
        return []
    
    # Ordenar por índice
    potential_pivots.sort(key=lambda x: x["index"])
    
    # ===== FASE 2: Construir ZigZag alternando y respetando desviación =====
    zigzag = []
    last_type = None
    last_price = None
    
    for pivot in potential_pivots:
        if not zigzag:
            # Primer pivote
            zigzag.append(pivot)
            last_type = pivot["type"]
            last_price = pivot["price"]
            continue
        
        # Si es del mismo tipo que el último
        if pivot["type"] == last_type:
            # Reemplazar si es más extremo
            if last_type == "high" and pivot["price"] > zigzag[-1]["price"]:
                zigzag[-1] = pivot
                last_price = pivot["price"]
            elif last_type == "low" and pivot["price"] < zigzag[-1]["price"]:
                zigzag[-1] = pivot
                last_price = pivot["price"]
        else:
            # Tipo diferente - verificar desviación
            price_change = abs(pivot["price"] - last_price) / last_price
            
            if price_change >= deviation:
                zigzag.append(pivot)
                last_type = pivot["type"]
                last_price = pivot["price"]
            else:
                # No cumple desviación mínima - verificar si es mejor que el anterior del mismo tipo
                if len(zigzag) >= 2 and zigzag[-2]["type"] == pivot["type"]:
                    if pivot["type"] == "high" and pivot["price"] > zigzag[-2]["price"]:
                        zigzag[-2] = pivot
                    elif pivot["type"] == "low" and pivot["price"] < zigzag[-2]["price"]:
                        zigzag[-2] = pivot
    
    # ===== FASE 3: Validar alternancia (debe ser High-Low-High-Low...) =====
    final_zigzag = []
    for i, pivot in enumerate(zigzag):
        if not final_zigzag:
            final_zigzag.append(pivot)
        elif pivot["type"] != final_zigzag[-1]["type"]:
            final_zigzag.append(pivot)
        else:
            # Mismo tipo - mantener el más extremo
            if pivot["type"] == "high" and pivot["price"] > final_zigzag[-1]["price"]:
                final_zigzag[-1] = pivot
            elif pivot["type"] == "low" and pivot["price"] < final_zigzag[-1]["price"]:
                final_zigzag[-1] = pivot
    
    # ===== FASE 4: Convertir a ZigZagPoint =====
    result = []
    for pivot in final_zigzag:
        idx = pivot["index"]
        result.append(ZigZagPoint(
            index=idx,
            time=data[idx]["time"],
            price=pivot["price"],
            type=pivot["type"]
        ))
    
    return result


def calculate_fibonacci_levels(high_price: float, low_price: float) -> Dict[str, float]:
    """Calcular niveles Fibonacci entre High y Low"""
    range_val = high_price - low_price
    levels = {}
    
    for name, ratio in FIBONACCI_LEVELS.items():
        levels[name] = low_price + (range_val * ratio)
    
    return levels


def find_valid_fibonacci_swing(
    zigzag_points: List[ZigZagPoint], 
    candle_data: List[dict]
) -> Optional[List[FibonacciSwing]]:
    """
    Encontrar swings válidos para Fibonacci SHORT
    
    SISTEMA DE 2 CAMINOS:
    - Camino 1: Swing normal con High más reciente
    - Camino 2: SIEMPRE buscar un Caso 1 alternativo con High movido a izquierda
    
    Garantiza que siempre haya un Caso 1 disponible si RSI >= 75
    
    Retorna: Lista de swings válidos (incluye swing del camino 1 Y swing del camino 2)
    """
    if len(zigzag_points) < 2 or len(candle_data) < 2:
        return None
    
    # Separar Highs y Lows, ordenados por índice (más reciente primero)
    high_points = sorted(
        [p for p in zigzag_points if p.type == "high"],
        key=lambda p: p.index,
        reverse=True
    )
    
    low_points = sorted(
        [p for p in zigzag_points if p.type == "low"],
        key=lambda p: p.index,
        reverse=True
    )
    
    if not high_points or not low_points:
        return None
    
    last_candle_index = len(candle_data) - 1
    current_price = candle_data[-1]['close']
    
    # ===== REGLA: Si el último punto ZigZag es un HIGH, ignorarlo =====
    last_zigzag = max(zigzag_points, key=lambda p: p.index)
    skip_first_high = last_zigzag.type == "high"
    
    working_high_points = high_points.copy()
    if skip_first_high and len(working_high_points) > 1:
        print(f"   ⚠️ Último punto ZigZag es HIGH ({last_zigzag.price:.4f}) - Ignorando")
        working_high_points = working_high_points[1:]
    
    valid_swings = []
    found_path1_case1 = False  # Para saber si ya encontramos un Caso 1 en Path 1
    
    # ===== CAMINO 1: Buscar swing normal =====
    for high_idx, current_high in enumerate(working_high_points):
        if current_high.index >= last_candle_index:
            continue
        
        # Buscar el Low real
        lowest_price = float('inf')
        lowest_index = current_high.index + 1
        
        for k in range(current_high.index + 1, last_candle_index + 1):
            if candle_data[k]["low"] < lowest_price:
                lowest_price = candle_data[k]["low"]
                lowest_index = k
        
        if lowest_price == float('inf'):
            continue
        
        lowest_low = ZigZagPoint(
            index=lowest_index,
            time=candle_data[lowest_index]["time"],
            price=lowest_price,
            type="low"
        )
        
        range_val = current_high.price - lowest_low.price
        if range_val <= 0:
            continue
        
        # Calcular niveles
        fib_618_level = lowest_low.price + (range_val * 0.618)
        fib_786_level = lowest_low.price + (range_val * 0.786)
        fib_90_level = lowest_low.price + (range_val * 0.90)
        
        # CHECK 90% - INVALIDACIÓN TOTAL
        invalidated_by_90 = False
        for k in range(lowest_low.index + 1, last_candle_index + 1):
            if candle_data[k]["high"] >= fib_90_level:
                invalidated_by_90 = True
                print(f"   ⛔ 90% TOUCHED - INVALIDATING SWING, moving to next High...")
                break
        
        if invalidated_by_90:
            continue  # Intentar siguiente High
        
        # CHECK 61.8% y 78.6% - verificar TODAS las velas desde el Low hasta la actual
        # Esto garantiza que si el nivel de entrada ya fue tocado, busquemos el siguiente High
        has_touched_618 = False
        has_touched_69 = False
        has_touched_786 = False
        
        fib_69_level = lowest_low.price + (range_val * 0.69)  # Nivel 69%
        
        for k in range(lowest_low.index + 1, last_candle_index + 1):
            if candle_data[k]["high"] >= fib_786_level:
                has_touched_786 = True
            if candle_data[k]["high"] >= fib_69_level:
                has_touched_69 = True
            if candle_data[k]["high"] >= fib_618_level:
                has_touched_618 = True
        
        # Determinar min_valid_case (Solo casos 1, 3, 4 - Caso 2 eliminado)
        # - Si tocó 78.6% → Solo Caso 4 válido (min_valid_case = 4)
        # - Si tocó 68%   → Casos 3, 4 válidos (min_valid_case = 3)  
        # - Si no tocó 68% → Todos los casos (1, 3, 4) válidos
        min_valid_case = 1
        if has_touched_786:
            min_valid_case = 4
            print(f"   ⚠️ 78.6% TOUCHED - Only Case 4 valid (Path 1)")
        elif has_touched_69:
            min_valid_case = 3
            print(f"   ⚠️ 68% TOUCHED - Cases 3,4 valid (Path 1)")
        
        # Verificar si el precio actual está en zona válida para Path 1
        level_case1_min = lowest_low.price + (range_val * CASE_1_MIN)  # 58%
        level_case3_min = lowest_low.price + (range_val * CASE_3_MIN)  # 68%
        level_case4_min = lowest_low.price + (range_val * CASE_4_MIN)  # 78.6%
        
        # Si precio actual está debajo del nivel mínimo válido, buscar siguiente High
        # - min_valid_case = 3: precio debe estar >= 68%   (zona C3+)
        # - min_valid_case = 4: precio debe estar >= 78.6%   (zona C4)
        if min_valid_case == 3 and current_price < level_case3_min:
            print(f"   ⚠️ Casos 3-4 válidos pero precio ({current_price:.4f}) debajo de zona C3 ({level_case3_min:.4f}) - buscando siguiente High...")
            continue
        
        if min_valid_case == 4 and current_price < level_case4_min:
            print(f"   ⚠️ Solo Caso 4 válido pero precio ({current_price:.4f}) debajo de zona C4 ({level_case4_min:.4f}) - buscando siguiente High...")
            continue
        
        levels = calculate_fibonacci_levels(current_high.price, lowest_low.price)
        
        swing_path1 = FibonacciSwing(
            high=current_high,
            low=lowest_low,
            levels=levels,
            is_valid=True,
            current_candle_at_55=False,
            min_valid_case=min_valid_case,
            path=1
        )
        
        case_text = f"Cases {min_valid_case}-4" if min_valid_case > 1 else "All Cases"
        print(f"   ✅ Swing válido (Path 1): High {current_high.price:.4f} -> Low {lowest_low.price:.4f}")
        print(f"      Valid entries: {case_text}")
        
        valid_swings.append(swing_path1)
        
        if min_valid_case == 1:
            found_path1_case1 = True
        
        break  # Encontramos swing de Path 1, salir del loop
    
    # NOTA: Path 2 (C1++ alternativo) ahora se busca en scanner.py/_search_and_place_c1pp
    # Solo se activa DESPUÉS de colocar un C3/C4 (Caso 2 eliminado)
    
    if valid_swings:
        return valid_swings
    
    print("   ⚠️ No se encontró swing válido tras revisar todos los Highs")
    return None


def determine_trading_case(current_price: float, swing: FibonacciSwing, 
                           candle_data: List[dict] = None, last_n_candles: int = 3) -> int:
    """
    Determinar el caso de trading según la posición del precio
    Los thresholds se leen de shared_config.json
    
    NUEVO: Verifica que el nivel de ENTRADA no haya sido tocado por la mecha
    de las últimas N velas (para evitar entrar tarde)
    
    - Path 1: Casos según zona (1-4) respetando min_valid_case
    - Path 2: Solo Caso 1 válido, zona expandida 0% - 61.8%
    
    Returns: 1, 2, 3, 4 o 0 (sin caso válido)
    """
    low = swing.low.price
    range_val = swing.high.price - swing.low.price
    
    # ===== PATH 2: Solo Caso 1, zona 0% - 61.8% =====
    if swing.path == 2:
        level_618 = low + range_val * 0.618
        level_invalid = low + range_val * CASE_4_MAX  # 90%
        
        if current_price >= level_invalid:
            return 0  # Invalidado
        elif current_price >= low and current_price < level_618:
            return 1  # Caso 1 en zona expandida
        else:
            return 0  # Fuera de zona
    
    # ===== PATH 1: Lógica normal de zonas (Solo casos 1, 3, 4) =====
    level_case1_min = low + range_val * CASE_1_MIN  # 58%
    level_case1_max = low + range_val * CASE_1_MAX  # 68%
    level_case3_min = low + range_val * CASE_3_MIN  # 68%
    level_case4_min = low + range_val * CASE_4_MIN  # 78.6%
    level_invalid = low + range_val * CASE_4_MAX    # 90%
    
    # Niveles de entrada según caso
    level_786 = low + range_val * 0.786  # Entrada para Caso 3
    level_68 = low + range_val * 0.68    # Entrada para Caso 1 (LIMIT SELL al 68%)
    
    # Determinar caso según zona (Caso 2 eliminado)
    detected_case = 0
    
    if current_price >= level_invalid:
        detected_case = 0  # Invalidado (encima de 90%)
    elif current_price >= level_case4_min:
        detected_case = 4  # 78.6% - 90%: MARKET
    elif current_price >= level_case3_min:
        detected_case = 3  # 68% - 78.6%: LIMIT 78.6%
    elif current_price >= level_case1_min:
        detected_case = 1  # 58% - 68%: LIMIT 68%
    else:
        detected_case = 0  # Precio muy bajo, sin caso válido
    
    # VALIDAR contra min_valid_case del swing
    if detected_case > 0 and detected_case < swing.min_valid_case:
        print(f"   ⚠️ Case {detected_case} detected but invalidated (min valid = Case {swing.min_valid_case})")
        return 0
    
    # ===== VALIDACIÓN: Verificar que el nivel de ENTRADA no haya sido tocado =====
    # Para TODOS los casos con orden LIMIT, verificar que el nivel no haya sido tocado desde el Low del swing
    # - Caso 1: LIMIT SELL en 68%
    # - Caso 3: LIMIT en 78.6%
    # - Caso 4: MARKET (no tiene LIMIT, pero verificamos el 90% máx)
    if candle_data and detected_case > 0:
        # Determinar el nivel de invalidación según el caso
        if detected_case == 1:
            entry_level = level_68  # 68% - Caso 1 usa LIMIT SELL en 68%
        elif detected_case == 3:
            entry_level = level_786  # 78.6%
        elif detected_case == 4:
            entry_level = level_invalid  # 90% (si toca 90%, invalida C4)
        else:
            entry_level = None
        
        if entry_level:
            # Encontrar el índice del Low del swing
            low_idx = None
            for i, candle in enumerate(candle_data):
                if candle["low"] <= swing.low.price * 1.001:  # Tolerancia 0.1%
                    low_idx = i
            
            # Verificar desde el Low hasta la vela actual (incluida)
            if low_idx is not None:
                start_idx = low_idx + 1  # Empezar desde la vela después del Low
            else:
                start_idx = 0  # Si no encontramos el Low, verificar todas las velas
            
            for i in range(start_idx, len(candle_data)):
                candle_high = candle_data[i]["high"]
                if candle_high >= entry_level:
                    print(f"   ⛔ Case {detected_case} INVALIDATED: Entry level already touched by wick (candle {i})")
                    print(f"      Entry: {entry_level:.6f}, Candle high: {candle_high:.6f}")
                    return 0  # El nivel ya fue tocado, no poner orden
    
    return detected_case

