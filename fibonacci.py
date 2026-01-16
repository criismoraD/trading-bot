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
CASE_1_MIN = _trading.get("case_1_min", 0.55)
CASE_1_MAX = _trading.get("case_1_max", 0.618)
CASE_2_MIN = _trading.get("case_2_min", 0.618)
CASE_2_MAX = _trading.get("case_2_max", 0.69)
CASE_3_MIN = _trading.get("case_3_min", 0.69)
CASE_3_MAX = _trading.get("case_3_max", 0.75)
CASE_4_MIN = _trading.get("case_4_min", 0.75)
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
    min_valid_case: int = 1  # Mínimo caso válido (1=todos, 2=desde C2, 3=desde C3, 4=solo C4)


def get_zigzag_config(timeframe: str) -> dict:
    """Obtener configuración ZigZag según timeframe"""
    return ZIGZAG_CONFIGS.get(timeframe, ZIGZAG_CONFIGS["1h"])


def calculate_zigzag(candle_data: List[dict], timeframe: str = "1h") -> List[ZigZagPoint]:
    """
    Calcular puntos ZigZag - VERSIÓN MEJORADA
    Detecta correctamente máximos y mínimos locales significativos
    """
    config = get_zigzag_config(timeframe)
    deviation = config["deviation"] / 100
    depth = config["depth"]
    
    data = candle_data
    if len(data) < depth * 2:
        return []
    
    pivots = []
    
    # ===== FASE 1: Encontrar todos los extremos locales =====
    # Usar una ventana deslizante para detectar máximos y mínimos locales
    local_highs = []
    local_lows = []
    
    for i in range(depth, len(data) - depth):
        # Verificar si es un máximo local
        is_high = True
        current_high = data[i]["high"]
        for j in range(i - depth, i + depth + 1):
            if j != i and data[j]["high"] > current_high:
                is_high = False
                break
        if is_high:
            local_highs.append((i, current_high))
        
        # Verificar si es un mínimo local
        is_low = True
        current_low = data[i]["low"]
        for j in range(i - depth, i + depth + 1):
            if j != i and data[j]["low"] < current_low:
                is_low = False
                break
        if is_low:
            local_lows.append((i, current_low))
    
    # También verificar las últimas velas (pueden ser extremos)
    # Buscar el máximo en las últimas 'depth' velas
    if len(data) > depth:
        last_section_start = len(data) - depth
        max_in_last = max(range(last_section_start, len(data)), key=lambda x: data[x]["high"])
        min_in_last = min(range(last_section_start, len(data)), key=lambda x: data[x]["low"])
        
        # Añadir si es significativo
        if not any(h[0] == max_in_last for h in local_highs):
            # Verificar que sea un máximo real comparado con velas anteriores
            is_significant_high = True
            for j in range(max(0, max_in_last - depth), max_in_last):
                if data[j]["high"] > data[max_in_last]["high"]:
                    is_significant_high = False
                    break
            if is_significant_high:
                local_highs.append((max_in_last, data[max_in_last]["high"]))
        
        if not any(l[0] == min_in_last for l in local_lows):
            is_significant_low = True
            for j in range(max(0, min_in_last - depth), min_in_last):
                if data[j]["low"] < data[min_in_last]["low"]:
                    is_significant_low = False
                    break
            if is_significant_low:
                local_lows.append((min_in_last, data[min_in_last]["low"]))
    
    # ===== FASE 2: Combinar y filtrar por desviación =====
    # Crear lista combinada de todos los extremos
    all_extremes = []
    for idx, price in local_highs:
        all_extremes.append({"index": idx, "price": price, "type": "high"})
    for idx, price in local_lows:
        all_extremes.append({"index": idx, "price": price, "type": "low"})
    
    # Ordenar por índice
    all_extremes.sort(key=lambda x: x["index"])
    
    if not all_extremes:
        return []
    
    # ===== FASE 3: Construir ZigZag alternando High-Low =====
    # Filtrar para alternar entre highs y lows, manteniendo los más significativos
    
    # Determinar dirección inicial
    first_high_idx = next((i for i, e in enumerate(all_extremes) if e["type"] == "high"), None)
    first_low_idx = next((i for i, e in enumerate(all_extremes) if e["type"] == "low"), None)
    
    if first_high_idx is None or first_low_idx is None:
        return []
    
    # Empezar con el que viene primero cronológicamente
    if first_high_idx < first_low_idx:
        current_type = "high"
    else:
        current_type = "low"
    
    filtered_pivots = []
    
    i = 0
    while i < len(all_extremes):
        # Buscar el mejor extremo del tipo actual
        best_extreme = None
        best_idx = i
        
        # Agrupar extremos consecutivos del mismo tipo y elegir el mejor
        while i < len(all_extremes):
            extreme = all_extremes[i]
            
            if extreme["type"] == current_type:
                if best_extreme is None:
                    best_extreme = extreme
                    best_idx = i
                else:
                    # Para HIGH, queremos el más alto; para LOW, el más bajo
                    if current_type == "high" and extreme["price"] > best_extreme["price"]:
                        best_extreme = extreme
                        best_idx = i
                    elif current_type == "low" and extreme["price"] < best_extreme["price"]:
                        best_extreme = extreme
                        best_idx = i
                i += 1
            else:
                # Encontramos un extremo del tipo opuesto
                if best_extreme is not None:
                    break
                i += 1
        
        if best_extreme is not None:
            # Verificar desviación mínima respecto al último pivot
            if filtered_pivots:
                last_pivot = filtered_pivots[-1]
                price_change = abs(best_extreme["price"] - last_pivot["price"]) / last_pivot["price"]
                
                if price_change >= deviation:
                    filtered_pivots.append(best_extreme)
                    current_type = "low" if current_type == "high" else "high"
                else:
                    # No cumple desviación, pero si es mejor que el último del mismo tipo, reemplazar
                    if best_extreme["type"] == last_pivot["type"]:
                        if (current_type == "high" and best_extreme["price"] > last_pivot["price"]) or \
                           (current_type == "low" and best_extreme["price"] < last_pivot["price"]):
                            filtered_pivots[-1] = best_extreme
            else:
                filtered_pivots.append(best_extreme)
                current_type = "low" if current_type == "high" else "high"
    
    # ===== FASE 4: Convertir a ZigZagPoint =====
    for extreme in filtered_pivots:
        idx = extreme["index"]
        pivots.append(ZigZagPoint(
            index=idx,
            time=data[idx]["time"],
            price=extreme["price"],
            type=extreme["type"]
        ))
    
    return pivots


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
) -> Optional[FibonacciSwing]:
    """
    Encontrar un swing válido para Fibonacci SHORT
    SINCRONIZADO con la lógica de app.js drawFibonacciForShort()
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
    
    # Iterar por los Highs de derecha a izquierda (más reciente primero)
    for current_high in high_points:
        # Verificar que hay velas después del High
        if current_high.index >= last_candle_index:
            continue
        
        # ===== BUSCAR EL LOW REAL (precio mínimo de TODAS las velas después del High) =====
        # No solo entre puntos ZigZag, sino el precio más bajo real
        lowest_price = float('inf')
        lowest_index = current_high.index + 1
        
        for k in range(current_high.index + 1, last_candle_index + 1):
            if candle_data[k]["low"] < lowest_price:
                lowest_price = candle_data[k]["low"]
                lowest_index = k
        
        if lowest_price == float('inf'):
            continue
        
        # Crear un ZigZagPoint virtual con el Low real
        lowest_low = ZigZagPoint(
            index=lowest_index,
            time=candle_data[lowest_index]["time"],
            price=lowest_price,
            type="low"
        )
        
        # Calcular niveles
        range_val = current_high.price - lowest_low.price
        if range_val <= 0:
            continue
        
        fib_618_level = lowest_low.price + (range_val * 0.618)
        fib_58_level = lowest_low.price + (range_val * 0.58)
        fib_75_level = lowest_low.price + (range_val * 0.75)
        
        # ===== CHECK 61.8% =====
        # Verificar si el 61.8% fue tocado después del Low
        # EXCLUIR las últimas 3 velas (actual + 2 anteriores)
        has_touched_618 = False
        exclude_from_index_618 = max(lowest_low.index + 1, len(candle_data) - 3)
        
        for k in range(lowest_low.index + 1, exclude_from_index_618):
            if candle_data[k]["high"] >= fib_618_level:
                has_touched_618 = True
                break
        
        # ===== CHECK 58% =====
        # Count intermediate candles that touch 58% (excluding last 3)
        intermediates_touching_58 = 0
        exclude_from_index_58 = max(lowest_low.index + 1, last_candle_index - 2)
        
        for k in range(lowest_low.index + 1, exclude_from_index_58):
            if candle_data[k]["high"] >= fib_58_level:
                intermediates_touching_58 += 1
        
        # Check if current candle OR 2 previous are at 58%+
        recent_candles_at_58 = False
        start_check_58 = max(0, last_candle_index - 2)
        for k in range(start_check_58, last_candle_index + 1):
            if candle_data[k]["high"] >= fib_58_level:
                recent_candles_at_58 = True
                break
        
        # ===== CHECK 75% (para Case 2/3) =====
        # Check intermediate candles for 75% (excluding last 3)
        intermediates_touching_75 = 0
        exclude_from_index_75 = max(lowest_low.index + 1, last_candle_index - 2)
        
        for k in range(lowest_low.index + 1, exclude_from_index_75):
            if candle_data[k]["high"] >= fib_75_level:
                intermediates_touching_75 += 1
        
        # Check if current candle OR 2 previous are at 75%+
        recent_candles_at_75 = False
        start_check_75 = max(0, last_candle_index - 2)
        for k in range(start_check_75, last_candle_index + 1):
            if candle_data[k]["high"] >= fib_75_level:
                recent_candles_at_75 = True
                break
        
        # ===== VALIDATION LOGIC (synced with app.js) =====
        invalidated_by_58 = intermediates_touching_58 > 0
        invalidated_by_75 = intermediates_touching_75 > 0
        
        # ===== 90% LEVEL CHECK - IMMEDIATE INVALIDATION =====
        # If ANY candle (including current) from Low to present touches 90%, invalidate
        fib_90_level = lowest_low.price + (range_val * 0.90)
        invalidated_by_90 = False
        
        for k in range(lowest_low.index + 1, last_candle_index + 1):
            if candle_data[k]["high"] >= fib_90_level:
                invalidated_by_90 = True
                print(f"   ⛔ 90% TOUCHED at index {k} ({candle_data[k]['high']:.4f} >= {fib_90_level:.4f}) - INVALIDATING SWING")
                break
        
        if invalidated_by_90:
            print(f"   ⛔ Swing invalidated by 90% level - Moving to next High...")
            continue  # Skip to next High
        
        # ===== NIVEL MÍNIMO VÁLIDO DE ENTRADA =====
        # En vez de invalidar todo el swing, determinar desde qué caso es válido
        # Si tocó 61.8% -> casos 1,2 invalidados -> min_case = 3
        # Si tocó 78.6% -> casos 1,2,3 invalidados -> min_case = 4
        # Si no tocó nada -> todos los casos válidos -> min_case = 1
        
        min_valid_case = 1  # Por defecto, todos los casos válidos
        
        # Calcular niveles de invalidación
        fib_786_level = lowest_low.price + (range_val * 0.786)
        
        # Check 78.6% (excluyendo últimas 3 velas)
        has_touched_786 = False
        exclude_from_index_786 = max(lowest_low.index + 1, last_candle_index - 2)
        for k in range(lowest_low.index + 1, exclude_from_index_786):
            if candle_data[k]["high"] >= fib_786_level:
                has_touched_786 = True
                print(f"   ⚠️ 78.6% TOUCHED at index {k} - Cases 1,2,3 invalidated, only Case 4 valid")
                break
        
        # Check 61.8% (ya calculado arriba como has_touched_618)
        
        # Determinar min_valid_case
        if has_touched_786:
            min_valid_case = 4  # Solo Case 4 válido
        elif has_touched_618:
            min_valid_case = 3  # Cases 3 y 4 válidos
            print(f"   ⚠️ 61.8% TOUCHED - Cases 1,2 invalidated, Cases 3,4 still valid")
        else:
            min_valid_case = 1  # Todos los casos válidos
        
        # ===== VALIDACIÓN FINAL =====
        # El swing es válido si no tocó 90%
        # min_valid_case indica desde qué caso se puede entrar
        
        is_valid_swing = True
        
        if is_valid_swing:
            # Calcular todos los niveles
            levels = calculate_fibonacci_levels(current_high.price, lowest_low.price)
            
            case_text = f"Cases {min_valid_case}-4" if min_valid_case > 1 else "All Cases"
            print(f"   ✅ Swing válido: High {current_high.price:.4f} -> Low {lowest_low.price:.4f}")
            print(f"      Valid entries: {case_text}")
            print(f"      58%: {fib_58_level:.4f} | 61.8%: {fib_618_level:.4f} | 75%: {fib_75_level:.4f}")
            
            return FibonacciSwing(
                high=current_high,
                low=lowest_low,
                levels=levels,
                is_valid=True,
                current_candle_at_55=recent_candles_at_58,
                min_valid_case=min_valid_case  # Nuevo: caso mínimo válido
            )
    
    print("   ⚠️ No se encontró swing válido tras revisar todos los Highs")
    return None


def determine_trading_case(current_price: float, swing: FibonacciSwing) -> int:
    """
    Determinar el caso de trading según la posición del precio
    Los thresholds se leen de shared_config.json
    
    NUEVO: Respeta min_valid_case del swing
    Si el swing tiene min_valid_case=3, solo casos 3 y 4 son válidos
    
    Returns: 1, 2, 3, 4 o 0 (sin caso válido)
    """
    # Calcular niveles usando thresholds de config
    low = swing.low.price
    range_val = swing.high.price - swing.low.price
    
    level_case1_min = low + range_val * CASE_1_MIN  # 55%
    level_case2_min = low + range_val * CASE_2_MIN  # 61.8%
    level_case3_min = low + range_val * CASE_3_MIN  # 69%
    level_case4_min = low + range_val * CASE_4_MIN  # 75%
    level_invalid = low + range_val * CASE_4_MAX    # 90%
    
    # Determinar caso según zona
    detected_case = 0
    
    if current_price >= level_invalid:
        detected_case = 0  # Invalidado (encima de 90%)
    elif current_price >= level_case4_min:
        detected_case = 4  # 75% - 90%: MARKET, TP 62%
    elif current_price >= level_case3_min:
        detected_case = 3  # 69% - 75%: LIMIT 78.6%, TP 62%
    elif current_price >= level_case2_min:
        detected_case = 2  # 61.8% - 69%: MARKET + LIMIT 78.6%, TP 55%
    elif current_price >= level_case1_min:
        detected_case = 1  # 55% - 61.8%: LIMIT 61.8% + LIMIT 78.6%, TP 55%
    else:
        detected_case = 0  # Precio muy bajo, sin caso válido
    
    # VALIDAR contra min_valid_case del swing
    # Si el swing fue parcialmente invalidado, solo casos >= min_valid_case son válidos
    if detected_case > 0 and detected_case < swing.min_valid_case:
        print(f"   ⚠️ Case {detected_case} detected but invalidated (min valid = Case {swing.min_valid_case})")
        return 0
    
    return detected_case

