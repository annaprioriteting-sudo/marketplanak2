# ============================================================
#  analyzer.py — Технический анализ
#  Smart Money Concepts + классический ТА
# ============================================================

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
import logging

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  Датаклассы результатов
# ════════════════════════════════════════════════════════════

@dataclass
class StructurePoint:
    """Точка рыночной структуры (HH, HL, LH, LL)."""
    idx: int
    price: float
    type: str        # "HH", "HL", "LH", "LL"
    timeframe: str

@dataclass
class StructureBreak:
    """BOS или ChoCH."""
    type: str          # "BOS_bull", "BOS_bear", "ChoCH_bull", "ChoCH_bear"
    price: float
    bar_idx: int
    timeframe: str

@dataclass
class OrderBlock:
    """Order Block (зона институционального интереса)."""
    top: float
    bottom: float
    type: str          # "bullish" / "bearish"
    timeframe: str
    strength: float    # 0-1, сила блока
    mitigated: bool = False

@dataclass
class FairValueGap:
    """Fair Value Gap (имбаланс)."""
    top: float
    bottom: float
    type: str          # "bullish" / "bearish"
    timeframe: str
    filled: bool = False

@dataclass
class LiquidityLevel:
    """Уровень ликвидности (свинг хай/лоу, EQH/EQL)."""
    price: float
    type: str          # "buy_side", "sell_side", "equal_highs", "equal_lows"
    timeframe: str
    swept: bool = False

@dataclass
class Pattern:
    """Графический паттерн."""
    name: str
    direction: str     # "bullish", "bearish", "neutral"
    top: float
    bottom: float
    target: float
    timeframe: str
    confidence: float  # 0-1

@dataclass
class SupportResistanceLevel:
    """Уровень поддержки/сопротивления."""
    price: float
    type: str          # "support", "resistance"
    strength: int      # количество касаний
    timeframe: str

@dataclass
class IndicatorData:
    """Показатели индикаторов."""
    rsi: float
    ema20: float
    ema50: float
    ema200: float
    atr: float
    volume_ratio: float   # текущий объём / средний
    macd_signal: str      # "bullish_cross", "bearish_cross", "bullish", "bearish"
    bb_position: str      # "above_upper", "below_lower", "middle", "upper", "lower"

@dataclass
class TimeframeAnalysis:
    """Полный анализ одного таймфрейма."""
    timeframe: str
    trend: str                      # "bullish", "bearish", "ranging"
    structure_points: List[StructurePoint]
    last_bos: Optional[StructureBreak]
    last_choch: Optional[StructureBreak]
    order_blocks: List[OrderBlock]
    fvgs: List[FairValueGap]
    liquidity: List[LiquidityLevel]
    patterns: List[Pattern]
    sr_levels: List[SupportResistanceLevel]
    indicators: IndicatorData
    current_price: float
    premium_discount: str           # "premium", "discount", "equilibrium"

@dataclass
class FullAnalysis:
    """Полный мультитаймфреймовый анализ инструмента."""
    symbol: str
    asset_type: str                 # "crypto", "forex", "metal"
    current_price: float
    timeframes: Dict[str, TimeframeAnalysis]
    htf_bias: str                   # "bullish", "bearish", "ranging" (недельный/дневной)
    key_levels: List[float]         # важнейшие уровни для алертов


# ════════════════════════════════════════════════════════════
#  Индикаторы
# ════════════════════════════════════════════════════════════

def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl  = df["high"] - df["low"]
    hc  = (df["high"] - df["close"].shift()).abs()
    lc  = (df["low"]  - df["close"].shift()).abs()
    tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def calc_macd(series: pd.Series) -> Tuple[pd.Series, pd.Series, pd.Series]:
    ema12 = calc_ema(series, 12)
    ema26 = calc_ema(series, 26)
    macd  = ema12 - ema26
    signal = calc_ema(macd, 9)
    hist   = macd - signal
    return macd, signal, hist


def calc_bollinger(series: pd.Series, period: int = 20, std: float = 2.0):
    mid = series.rolling(period).mean()
    s   = series.rolling(period).std()
    return mid + std * s, mid, mid - std * s


def compute_indicators(df: pd.DataFrame) -> IndicatorData:
    close = df["close"]
    rsi  = calc_rsi(close).iloc[-1]
    ema20 = calc_ema(close, 20).iloc[-1]
    ema50 = calc_ema(close, 50).iloc[-1]
    ema200 = calc_ema(close, 200).iloc[-1] if len(df) >= 200 else calc_ema(close, len(df)).iloc[-1]
    atr = calc_atr(df).iloc[-1]

    avg_vol = df["volume"].rolling(20).mean().iloc[-1]
    vol_ratio = df["volume"].iloc[-1] / avg_vol if avg_vol > 0 else 1.0

    macd, signal, hist = calc_macd(close)
    if hist.iloc[-1] > 0 and hist.iloc[-2] <= 0:
        macd_signal = "bullish_cross"
    elif hist.iloc[-1] < 0 and hist.iloc[-2] >= 0:
        macd_signal = "bearish_cross"
    elif hist.iloc[-1] > 0:
        macd_signal = "bullish"
    else:
        macd_signal = "bearish"

    upper, mid, lower = calc_bollinger(close)
    price = close.iloc[-1]
    if price > upper.iloc[-1]:
        bb_pos = "above_upper"
    elif price < lower.iloc[-1]:
        bb_pos = "below_lower"
    elif price > mid.iloc[-1] + (upper.iloc[-1] - mid.iloc[-1]) * 0.5:
        bb_pos = "upper"
    elif price < mid.iloc[-1] - (mid.iloc[-1] - lower.iloc[-1]) * 0.5:
        bb_pos = "lower"
    else:
        bb_pos = "middle"

    return IndicatorData(
        rsi=round(rsi, 1),
        ema20=round(ema20, 6),
        ema50=round(ema50, 6),
        ema200=round(ema200, 6),
        atr=round(atr, 6),
        volume_ratio=round(vol_ratio, 2),
        macd_signal=macd_signal,
        bb_position=bb_pos,
    )


# ════════════════════════════════════════════════════════════
#  Smart Money: структура рынка
# ════════════════════════════════════════════════════════════

def find_swing_points(df: pd.DataFrame, swing_len: int = 5) -> Tuple[List, List]:
    """Находит свинг-хаи и свинг-лоу."""
    highs, lows = [], []
    for i in range(swing_len, len(df) - swing_len):
        if df["high"].iloc[i] == df["high"].iloc[i-swing_len:i+swing_len+1].max():
            highs.append((i, df["high"].iloc[i]))
        if df["low"].iloc[i] == df["low"].iloc[i-swing_len:i+swing_len+1].min():
            lows.append((i, df["low"].iloc[i]))
    return highs, lows


def classify_structure(highs: List, lows: List, tf: str) -> Tuple[List[StructurePoint], str]:
    """Классифицирует структурные точки и определяет тренд."""
    points = []
    if len(highs) < 2 or len(lows) < 2:
        return points, "ranging"

    # Последние 3 хая и лоу
    recent_highs = highs[-3:]
    recent_lows  = lows[-3:]

    # HH / LH
    for i in range(1, len(recent_highs)):
        t = "HH" if recent_highs[i][1] > recent_highs[i-1][1] else "LH"
        points.append(StructurePoint(recent_highs[i][0], recent_highs[i][1], t, tf))

    # HL / LL
    for i in range(1, len(recent_lows)):
        t = "HL" if recent_lows[i][1] > recent_lows[i-1][1] else "LL"
        points.append(StructurePoint(recent_lows[i][0], recent_lows[i][1], t, tf))

    hh = any(p.type == "HH" for p in points)
    hl = any(p.type == "HL" for p in points)
    ll = any(p.type == "LL" for p in points)
    lh = any(p.type == "LH" for p in points)

    if hh and hl:
        trend = "bullish"
    elif ll and lh:
        trend = "bearish"
    else:
        trend = "ranging"

    return points, trend


def find_bos_choch(df: pd.DataFrame, highs: List, lows: List, tf: str) -> Tuple[Optional[StructureBreak], Optional[StructureBreak]]:
    """Определяет последний BOS и ChoCH."""
    last_bos, last_choch = None, None

    if len(highs) < 2 or len(lows) < 2:
        return None, None

    # Смотрим последние 50 свечей
    recent = df.iloc[-50:]
    prev_high = highs[-2][1] if len(highs) >= 2 else None
    prev_low  = lows[-2][1]  if len(lows)  >= 2 else None

    if prev_high and recent["close"].iloc[-1] > prev_high:
        # Пробой предыдущего хая
        last_bos = StructureBreak("BOS_bull", prev_high, len(df)-1, tf)
    elif prev_low and recent["close"].iloc[-1] < prev_low:
        last_bos = StructureBreak("BOS_bear", prev_low, len(df)-1, tf)

    # ChoCH: предыдущая структура была медвежьей, но теперь пробой хая (или наоборот)
    if len(highs) >= 3 and len(lows) >= 3:
        # Упрощённый ChoCH: предпоследний лоу был LL, но цена пробила предпоследний хай
        was_bearish = lows[-2][1] < lows[-3][1]
        if was_bearish and prev_high and recent["close"].iloc[-1] > prev_high:
            last_choch = StructureBreak("ChoCH_bull", prev_high, len(df)-1, tf)

        was_bullish = lows[-2][1] > lows[-3][1]
        if was_bullish and prev_low and recent["close"].iloc[-1] < prev_low:
            last_choch = StructureBreak("ChoCH_bear", prev_low, len(df)-1, tf)

    return last_bos, last_choch


# ════════════════════════════════════════════════════════════
#  Smart Money: Order Blocks
# ════════════════════════════════════════════════════════════

def find_order_blocks(df: pd.DataFrame, tf: str, lookback: int = 50) -> List[OrderBlock]:
    """Находит Order Blocks: последняя свеча перед импульсным движением."""
    obs = []
    data = df.iloc[-lookback:]

    for i in range(2, len(data) - 1):
        curr = data.iloc[i]
        next_c = data.iloc[i+1]

        # Импульсная бычья свеча (тело > 1.5 × ATR)
        body = abs(next_c["close"] - next_c["open"])
        atr_approx = (data["high"] - data["low"]).rolling(14).mean().iloc[i]

        if body > 1.5 * atr_approx:
            if next_c["close"] > next_c["open"]:  # бычий импульс → bearish OB
                obs.append(OrderBlock(
                    top=float(curr["high"]),
                    bottom=float(curr["low"]),
                    type="bearish",
                    timeframe=tf,
                    strength=round(body / atr_approx, 2),
                ))
            else:  # медвежий импульс → bullish OB
                obs.append(OrderBlock(
                    top=float(curr["high"]),
                    bottom=float(curr["low"]),
                    type="bullish",
                    timeframe=tf,
                    strength=round(body / atr_approx, 2),
                ))

    # Отмечаем смягчённые (mitigated) блоки
    current_price = float(df["close"].iloc[-1])
    for ob in obs:
        if ob.type == "bullish" and current_price < ob.bottom:
            ob.mitigated = True
        elif ob.type == "bearish" and current_price > ob.top:
            ob.mitigated = True

    # Берём только актуальные (не смягчённые), максимум 5
    active = [ob for ob in obs if not ob.mitigated]
    return sorted(active, key=lambda x: x.strength, reverse=True)[:5]


# ════════════════════════════════════════════════════════════
#  Smart Money: Fair Value Gaps
# ════════════════════════════════════════════════════════════

def find_fvg(df: pd.DataFrame, tf: str, lookback: int = 30) -> List[FairValueGap]:
    """Находит FVG (имбалансы): gap между свечой i-2 и свечой i."""
    fvgs = []
    data = df.iloc[-lookback:]

    for i in range(2, len(data)):
        prev2 = data.iloc[i-2]
        curr  = data.iloc[i]

        # Бычий FVG: low[i] > high[i-2]
        if curr["low"] > prev2["high"]:
            fvgs.append(FairValueGap(
                top=float(curr["low"]),
                bottom=float(prev2["high"]),
                type="bullish",
                timeframe=tf,
            ))
        # Медвежий FVG: high[i] < low[i-2]
        elif curr["high"] < prev2["low"]:
            fvgs.append(FairValueGap(
                top=float(prev2["low"]),
                bottom=float(curr["high"]),
                type="bearish",
                timeframe=tf,
            ))

    # Отмечаем заполненные
    current_price = float(df["close"].iloc[-1])
    for fvg in fvgs:
        if fvg.type == "bullish" and current_price < fvg.bottom:
            fvg.filled = True
        elif fvg.type == "bearish" and current_price > fvg.top:
            fvg.filled = True

    active = [f for f in fvgs if not f.filled]
    return active[-5:]  # последние 5 незаполненных


# ════════════════════════════════════════════════════════════
#  Smart Money: Уровни ликвидности
# ════════════════════════════════════════════════════════════

def find_liquidity(df: pd.DataFrame, tf: str, lookback: int = 50, tolerance: float = 0.001) -> List[LiquidityLevel]:
    """Находит уровни ликвидности: свинг-хаи/лоу и равные вершины/основания."""
    levels = []
    data = df.iloc[-lookback:]
    highs_arr = data["high"].values
    lows_arr  = data["low"].values

    # Свинг-хаи (buy side liquidity выше рынка)
    for i in range(2, len(data) - 2):
        if highs_arr[i] > highs_arr[i-1] and highs_arr[i] > highs_arr[i+1]:
            levels.append(LiquidityLevel(float(highs_arr[i]), "buy_side", tf))

    # Свинг-лоу (sell side liquidity ниже рынка)
    for i in range(2, len(data) - 2):
        if lows_arr[i] < lows_arr[i-1] and lows_arr[i] < lows_arr[i+1]:
            levels.append(LiquidityLevel(float(lows_arr[i]), "sell_side", tf))

    # Равные хаи (EQH) — два хая с допуском
    swing_highs = [l for l in levels if l.type == "buy_side"]
    for i in range(len(swing_highs)):
        for j in range(i+1, len(swing_highs)):
            if abs(swing_highs[i].price - swing_highs[j].price) / swing_highs[i].price < tolerance:
                levels.append(LiquidityLevel(
                    (swing_highs[i].price + swing_highs[j].price) / 2,
                    "equal_highs", tf
                ))

    # Отмечаем swept
    current = float(df["close"].iloc[-1])
    for liq in levels:
        if liq.type in ("buy_side", "equal_highs") and current > liq.price * 1.001:
            liq.swept = True
        elif liq.type == "sell_side" and current < liq.price * 0.999:
            liq.swept = True

    active = [l for l in levels if not l.swept]
    return active[-8:]


# ════════════════════════════════════════════════════════════
#  Паттерны
# ════════════════════════════════════════════════════════════

def find_patterns(df: pd.DataFrame, tf: str) -> List[Pattern]:
    """Определяет классические графические паттерны."""
    patterns = []
    if len(df) < 30:
        return patterns

    close = df["close"].values
    high  = df["high"].values
    low   = df["low"].values
    data  = df.iloc[-60:]

    # ─── Восходящий треугольник ───────────────────────────
    # Сопротивление горизонтальное, поддержка растёт
    resistance_highs = data["high"].rolling(5).max().iloc[-20:]
    if resistance_highs.std() / resistance_highs.mean() < 0.005:   # горизонталь
        support_lows = data["low"].rolling(5).min().iloc[-20:]
        if support_lows.iloc[-1] > support_lows.iloc[0]:            # растущая
            target = float(resistance_highs.mean() + (resistance_highs.mean() - support_lows.iloc[0]))
            patterns.append(Pattern(
                name="Восходящий треугольник",
                direction="bullish",
                top=float(resistance_highs.mean()),
                bottom=float(support_lows.iloc[-1]),
                target=target,
                timeframe=tf,
                confidence=0.70,
            ))

    # ─── Нисходящий треугольник ───────────────────────────
    support_lows2 = data["low"].rolling(5).min().iloc[-20:]
    if support_lows2.std() / abs(support_lows2.mean()) < 0.005:
        resistance_highs2 = data["high"].rolling(5).max().iloc[-20:]
        if resistance_highs2.iloc[-1] < resistance_highs2.iloc[0]:
            target = float(support_lows2.mean() - (resistance_highs2.iloc[0] - support_lows2.mean()))
            patterns.append(Pattern(
                name="Нисходящий треугольник",
                direction="bearish",
                top=float(resistance_highs2.iloc[-1]),
                bottom=float(support_lows2.mean()),
                target=target,
                timeframe=tf,
                confidence=0.70,
            ))

    # ─── Сужающийся клин ──────────────────────────────────
    recent_highs_cl = data["high"].iloc[-20:]
    recent_lows_cl  = data["low"].iloc[-20:]
    high_slope = np.polyfit(range(len(recent_highs_cl)), recent_highs_cl.values, 1)[0]
    low_slope  = np.polyfit(range(len(recent_lows_cl)),  recent_lows_cl.values,  1)[0]

    if high_slope < 0 and low_slope > 0:  # обе линии сходятся
        patterns.append(Pattern(
            name="Сужающийся клин (Symmetrical)",
            direction="neutral",
            top=float(recent_highs_cl.iloc[-1]),
            bottom=float(recent_lows_cl.iloc[-1]),
            target=float(recent_highs_cl.iloc[-1] * 1.03),
            timeframe=tf,
            confidence=0.65,
        ))
    elif high_slope < 0 and low_slope < 0 and abs(low_slope) < abs(high_slope):
        patterns.append(Pattern(
            name="Нисходящий клин",
            direction="bullish",
            top=float(recent_highs_cl.iloc[-1]),
            bottom=float(recent_lows_cl.iloc[-1]),
            target=float(recent_highs_cl.iloc[0]),
            timeframe=tf,
            confidence=0.68,
        ))
    elif high_slope > 0 and low_slope > 0 and high_slope < low_slope:
        patterns.append(Pattern(
            name="Восходящий клин",
            direction="bearish",
            top=float(recent_highs_cl.iloc[-1]),
            bottom=float(recent_lows_cl.iloc[-1]),
            target=float(recent_lows_cl.iloc[0]),
            timeframe=tf,
            confidence=0.68,
        ))

    # ─── Флаг / Пеннант ───────────────────────────────────
    impulse = abs(close[-30] - close[-20]) / close[-30] if close[-30] != 0 else 0
    consolidation_range = (max(high[-15:]) - min(low[-15:])) / close[-15]
    if impulse > 0.03 and consolidation_range < impulse * 0.4:
        direction = "bullish" if close[-20] > close[-30] else "bearish"
        patterns.append(Pattern(
            name="Флаг",
            direction=direction,
            top=float(max(high[-15:])),
            bottom=float(min(low[-15:])),
            target=float(close[-1] + (close[-20] - close[-30])),
            timeframe=tf,
            confidence=0.72,
        ))

    return patterns[:3]  # не больше 3 паттернов на таймфрейм


# ════════════════════════════════════════════════════════════
#  Поддержка / Сопротивление
# ════════════════════════════════════════════════════════════

def find_sr_levels(df: pd.DataFrame, tf: str, lookback: int = 100, tolerance: float = 0.002) -> List[SupportResistanceLevel]:
    """Кластеризует уровни по касаниям."""
    data = df.iloc[-lookback:]
    candidate_prices = []

    for i in range(2, len(data) - 2):
        # Локальный хай
        if data["high"].iloc[i] >= data["high"].iloc[i-1] and data["high"].iloc[i] >= data["high"].iloc[i+1]:
            candidate_prices.append(float(data["high"].iloc[i]))
        # Локальный лоу
        if data["low"].iloc[i] <= data["low"].iloc[i-1] and data["low"].iloc[i] <= data["low"].iloc[i+1]:
            candidate_prices.append(float(data["low"].iloc[i]))

    if not candidate_prices:
        return []

    # Кластеризация
    clusters = []
    used = [False] * len(candidate_prices)
    for i, p in enumerate(candidate_prices):
        if used[i]:
            continue
        cluster = [p]
        for j, q in enumerate(candidate_prices):
            if i != j and not used[j] and p > 0 and abs(p - q) / p < tolerance:
                cluster.append(q)
                used[j] = True
        clusters.append(cluster)

    current = float(df["close"].iloc[-1])
    levels = []
    for cl in clusters:
        avg_price = sum(cl) / len(cl)
        t = "resistance" if avg_price > current else "support"
        levels.append(SupportResistanceLevel(
            price=round(avg_price, 6),
            type=t,
            strength=len(cl),
            timeframe=tf,
        ))

    # Топ по силе
    return sorted(levels, key=lambda x: x.strength, reverse=True)[:8]


# ════════════════════════════════════════════════════════════
#  Premium / Discount зоны
# ════════════════════════════════════════════════════════════

def get_premium_discount(df: pd.DataFrame) -> str:
    """Определяет положение цены в диапазоне: premium/discount/equilibrium."""
    recent = df.iloc[-50:]
    high = recent["high"].max()
    low  = recent["low"].min()
    mid  = (high + low) / 2
    price = float(df["close"].iloc[-1])

    eq_band = (high - low) * 0.1
    if price > mid + eq_band:
        return "premium"
    elif price < mid - eq_band:
        return "discount"
    else:
        return "equilibrium"


# ════════════════════════════════════════════════════════════
#  Главная функция анализа одного таймфрейма
# ════════════════════════════════════════════════════════════

def analyze_timeframe(df: pd.DataFrame, tf: str) -> Optional[TimeframeAnalysis]:
    # Минимальное количество свечей зависит от таймфрейма
    _min_bars = {"1W": 15, "1wk": 15, "1D": 20, "1d": 20,
                 "4H": 20, "4h": 20, "1H": 15, "1h": 15, "15m": 10}
    min_bars = _min_bars.get(tf, 20)
    if df is None or df.empty or len(df) < min_bars:
        logger.debug(f"Таймфрейм {tf}: мало свечей ({len(df) if df is not None else 0} < {min_bars})")
        return None

    try:
        current_price = float(df["close"].iloc[-1])
        highs, lows = find_swing_points(df)
        struct_points, trend = classify_structure(highs, lows, tf)
        last_bos, last_choch = find_bos_choch(df, highs, lows, tf)
        order_blocks = find_order_blocks(df, tf)
        fvgs = find_fvg(df, tf)
        liquidity = find_liquidity(df, tf)
        patterns = find_patterns(df, tf)
        sr_levels = find_sr_levels(df, tf)
        indicators = compute_indicators(df)
        pd_zone = get_premium_discount(df)

        return TimeframeAnalysis(
            timeframe=tf,
            trend=trend,
            structure_points=struct_points,
            last_bos=last_bos,
            last_choch=last_choch,
            order_blocks=order_blocks,
            fvgs=fvgs,
            liquidity=liquidity,
            patterns=patterns,
            sr_levels=sr_levels,
            indicators=indicators,
            current_price=current_price,
            premium_discount=pd_zone,
        )
    except Exception as e:
        logger.error(f"Ошибка анализа {tf}: {e}")
        return None


# ════════════════════════════════════════════════════════════
#  Полный мультитаймфреймовый анализ
# ════════════════════════════════════════════════════════════

def full_analysis(symbol: str, asset_type: str, all_tf_data: Dict[str, pd.DataFrame]) -> FullAnalysis:
    """
    Запускает анализ по всем таймфреймам и возвращает FullAnalysis.
    asset_type: "crypto" / "forex" / "metal"
    """
    tf_analyses = {}
    for tf, df in all_tf_data.items():
        result = analyze_timeframe(df, tf)
        if result:
            tf_analyses[tf] = result

    if not tf_analyses:
        logger.warning(f"{symbol}: ни один таймфрейм не прошёл анализ. Загружено TF: {list(all_tf_data.keys())}")

    # HTF bias: смотрим недельный/дневной
    htf_order = ["1W", "1wk", "1D", "1d"]
    htf_bias = "ranging"
    for tf in htf_order:
        if tf in tf_analyses:
            htf_bias = tf_analyses[tf].trend
            break

    # Ключевые уровни (из всех таймфреймов, топ по силе)
    all_levels = []
    for tf_a in tf_analyses.values():
        for sr in tf_a.sr_levels:
            all_levels.append(sr.price)
        for ob in tf_a.order_blocks:
            all_levels.append((ob.top + ob.bottom) / 2)

    # Текущая цена — из анализа или напрямую из сырых данных
    current_price = 0.0
    for tf in ["15m", "1H", "1h", "4H", "4h", "1D", "1d", "1W", "1wk"]:
        if tf in tf_analyses:
            current_price = tf_analyses[tf].current_price
            break
    # Fallback: берём последнюю цену из любого загруженного DataFrame
    if current_price == 0.0:
        for tf in ["15m", "1h", "4h", "1d", "1wk"]:
            if tf in all_tf_data and not all_tf_data[tf].empty:
                try:
                    current_price = float(all_tf_data[tf]["close"].iloc[-1])
                    logger.info(f"{symbol}: цена из raw data {tf} = {current_price}")
                    break
                except Exception:
                    pass

    return FullAnalysis(
        symbol=symbol,
        asset_type=asset_type,
        current_price=current_price,
        timeframes=tf_analyses,
        htf_bias=htf_bias,
        key_levels=sorted(set(round(p, 4) for p in all_levels if p > 0)),
    )
