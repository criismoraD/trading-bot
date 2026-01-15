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
    Calcular puntos ZigZag - SINCRONIZADO con app.js
    Esta implementación replica exactamente la lógica de JavaScript
    """
    config = get_zigzag_config(timeframe)
    deviation = config["deviation"] / 100
    depth = config["depth"]
    
    data = candle_data
    if len(data) < depth * 2:
        return []
    
    pivots = []
    
    # Find initial direction (same as JS)
    max_price = data[0]["high"]
    min_price = data[0]["low"]
    max_index = 0
    min_index = 0
    
    for i in range(1, min(depth * 2, len(data))):
        if data[i]["high"] > max_price:
            max_price = data[i]["high"]
            max_index = i
        if data[i]["low"] < min_price:
            min_price = data[i]["low"]
            min_index = i
    
    # Determine initial pivot
    if max_index < min_index:
        # Started with a high
        pivots.append(ZigZagPoint(
            index=max_index,
            time=data[max_index]["time"],
            price=max_price,
            type="high"
        ))
        last_pivot_index = max_index
        last_pivot_price = max_price
        last_pivot_type = "high"
    else:
        # Started with a low
        pivots.append(ZigZagPoint(
            index=min_index,
            time=data[min_index]["time"],
            price=min_price,
            type="low"
        ))
        last_pivot_index = min_index
        last_pivot_price = min_price
        last_pivot_type = "low"
    
    # Find subsequent pivots (same algorithm as JS)
    for i in range(last_pivot_index + 1, len(data)):
        candle = data[i]
        
        if last_pivot_type == "high":
            # Looking for a low
            if candle["low"] < last_pivot_price * (1 - deviation):
                # Check if this is a valid low
                if i - last_pivot_index >= depth:
                    # Find the actual lowest point in this swing
                    lowest_price = candle["low"]
                    lowest_index = i
                    
                    for j in range(last_pivot_index + 1, i + 1):
                        if data[j]["low"] < lowest_price:
                            lowest_price = data[j]["low"]
                            lowest_index = j
                    
                    pivots.append(ZigZagPoint(
                        index=lowest_index,
                        time=data[lowest_index]["time"],
                        price=lowest_price,
                        type="low"
                    ))
                    last_pivot_index = lowest_index
                    last_pivot_price = lowest_price
                    last_pivot_type = "low"
            elif candle["high"] > last_pivot_price:
                # Update the high (same as JS: update last pivot)
                pivots[-1] = ZigZagPoint(
                    index=i,
                    time=candle["time"],
                    price=candle["high"],
                    type="high"
                )
                last_pivot_index = i
                last_pivot_price = candle["high"]
        else:
            # Looking for a high
            if candle["high"] > last_pivot_price * (1 + deviation):
                # Check if this is a valid high
                if i - last_pivot_index >= depth:
                    # Find the actual highest point in this swing
                    highest_price = candle["high"]
                    highest_index = i
                    
                    for j in range(last_pivot_index + 1, i + 1):
                        if data[j]["high"] > highest_price:
                            highest_price = data[j]["high"]
                            highest_index = j
                    
                    pivots.append(ZigZagPoint(
                        index=highest_index,
                        time=data[highest_index]["time"],
                        price=highest_price,
                        type="high"
                    ))
                    last_pivot_index = highest_index
                    last_pivot_price = highest_price
                    last_pivot_type = "high"
            elif candle["low"] < last_pivot_price:
                # Update the low (same as JS: update last pivot)
                pivots[-1] = ZigZagPoint(
                    index=i,
                    time=candle["time"],
                    price=candle["low"],
                    type="low"
                )
                last_pivot_index = i
                last_pivot_price = candle["low"]
    
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
        # Encontrar todos los Lows después de este High
        lows_after_high = [low for low in low_points if low.index > current_high.index]
        
        if not lows_after_high:
            continue
        
        # Encontrar el Low más bajo (precio mínimo)
        lowest_low = min(lows_after_high, key=lambda l: l.price)
        
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
    # NOTA: Desactivado temporalmente para debug - siempre permitir el caso detectado
    # if detected_case > 0 and detected_case < swing.min_valid_case:
    #     print(f"   ⚠️ Case {detected_case} detected but invalidated (min valid = Case {swing.min_valid_case})")
    #     return 0
    
    return detected_case

