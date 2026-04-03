# ============================================================
#  report_generator.py  v5  — premium market analyst
#
#  Главный режим: "Анализ монеты" (report_type="coin")
#  Формат: discretionary trader, decision-first, clean.
#  Контекст → что формируется → уровни → сценарий →
#  альтернатива → invalidation → решение → вывод трейдера
#
#  Совместимость: analyzer.py, strategy_selector.py — не тронуты.
# ============================================================

from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional, List, Dict, Tuple
import pytz

from analyzer import FullAnalysis, TimeframeAnalysis
from strategy_selector import (
    select_strategy, StrategyResult,
    STRATEGY_SMC, STRATEGY_WYCKOFF, STRATEGY_TREND,
    STRATEGY_RANGE, STRATEGY_REVERSAL,
)

logger = logging.getLogger(__name__)
TZ = pytz.timezone("Europe/Moscow")

MIN_RR           = 1.5
MIN_SIGNAL_SCORE = 5.0


# ════════════════════════════════════════════════════════════
#  УТИЛИТЫ
# ════════════════════════════════════════════════════════════

def _safe(val, default=0.0):
    return val if val is not None else default


def _fmt_price(price: float, symbol: str) -> str:
    if not price:
        return "—"
    if "JPY" in symbol:
        return f"{price:.3f}"
    if any(x in symbol for x in ["XAU", "XAG", "BTC"]):
        return f"{price:,.2f}"
    if any(x in symbol for x in ["EUR", "GBP", "AUD", "NZD"]):
        return f"{price:.5f}"
    if price > 1000:
        return f"{price:,.1f}"
    if price < 0.01:
        return f"{price:.6f}"
    return f"{price:.4f}"


def _get_tf(analysis: FullAnalysis, *candidates: str) -> Optional[TimeframeAnalysis]:
    for tf in candidates:
        if tf in analysis.timeframes:
            return analysis.timeframes[tf]
    return None


def _calc_rr_float(entry: float, target: float, stop: float) -> float:
    try:
        risk = abs(entry - stop)
        reward = abs(target - entry)
        return round(reward / risk, 2) if risk else 0.0
    except Exception:
        return 0.0


def _parse_price(s: str) -> float:
    try:
        return float(str(s).replace(",", "").replace(" ", ""))
    except Exception:
        return 0.0


def _fmt_rr(entry: float, target: float, stop: float) -> str:
    rr = _calc_rr_float(entry, target, stop)
    if not rr:
        return ""
    try:
        reward_pct = abs(target - entry) / entry * 100
        return f"R:R {rr:.1f}  (+{reward_pct:.1f}%)"
    except Exception:
        return f"R:R {rr:.1f}"


def _score_bars(score: float) -> str:
    filled = int(score / 10 * 8)
    return "█" * filled + "░" * (8 - filled)


# ════════════════════════════════════════════════════════════
#  ФАЗА РЫНКА
# ════════════════════════════════════════════════════════════

def _determine_phase(fa: FullAnalysis) -> Tuple[str, str]:
    htf = _get_tf(fa, "1W", "1wk", "1D", "1d")
    mtf = _get_tf(fa, "4H", "4h", "1D", "1d")
    if not htf:
        return "нет данных", ""

    ind   = htf.indicators
    price = fa.current_price or 0.0
    ema20, ema50, ema200 = _safe(ind.ema20), _safe(ind.ema50), _safe(ind.ema200)

    emas_bull  = ema20 > ema50 > ema200 and price > ema20
    emas_bear  = ema20 < ema50 < ema200 and price < ema20
    htf_bull   = htf.trend == "bullish"
    htf_bear   = htf.trend == "bearish"
    mtf_bull   = mtf and mtf.trend == "bullish"
    mtf_bear   = mtf and mtf.trend == "bearish"
    choch_bull = (htf.last_choch and "bull" in htf.last_choch.type) or \
                 (mtf and mtf.last_choch and "bull" in mtf.last_choch.type)
    choch_bear = (htf.last_choch and "bear" in htf.last_choch.type) or \
                 (mtf and mtf.last_choch and "bear" in mtf.last_choch.type)

    if htf_bull and emas_bull and not mtf_bear:
        return "trend↑", "Восходящий тренд. EMA выстроены. Структура сохранена."
    if htf_bear and emas_bear and not mtf_bull:
        return "trend↓", "Нисходящий тренд. EMA выстроены. Структура сохранена."
    if htf_bull and (choch_bear or mtf_bear):
        return "distribution", "HTF бычий, но MTF теряет силу. Возможная смена."
    if htf_bear and (choch_bull or mtf_bull):
        return "accumulation", "HTF медвежий, MTF показывает признаки разворота."
    if htf_bull and mtf_bear:
        return "correction", "Откат в бычьем тренде."
    if htf_bear and mtf_bull:
        return "correction", "Отскок в медвежьем тренде."
    return "range", "Нет чёткого направления."


# ════════════════════════════════════════════════════════════
#  СТРУКТУРА
# ════════════════════════════════════════════════════════════

def _determine_structure(fa: FullAnalysis) -> Dict:
    bias    = fa.htf_bias
    bias_ru = {"bullish": "бычья", "bearish": "медвежья", "ranging": "нейтральная"}.get(bias, "нейтральная")
    bos_desc = choch_desc = ""

    for tf_a, tf_b in [("1H", "1h"), ("4H", "4h"), ("1D", "1d"), ("1W", "1wk")]:
        tfa = _get_tf(fa, tf_a, tf_b)
        if tfa and tfa.last_bos and not bos_desc:
            d = "↑" if "bull" in tfa.last_bos.type else "↓"
            tf_lbl = tf_a if tf_a in fa.timeframes else tf_b
            bos_desc = f"BOS {d} · {tf_lbl} · {_fmt_price(tfa.last_bos.price, fa.symbol)}"
        if tfa and tfa.last_choch and not choch_desc:
            d = "↑" if "bull" in tfa.last_choch.type else "↓"
            tf_lbl = tf_a if tf_a in fa.timeframes else tf_b
            choch_desc = f"ChoCH {d} · {tf_lbl} · {_fmt_price(tfa.last_choch.price, fa.symbol)}"

    trends = [t.trend for t in fa.timeframes.values()]
    total  = len(trends) or 1
    bull_n = trends.count("bullish")
    bear_n = trends.count("bearish")
    align  = max(bull_n, bear_n) / total
    quality = "strong" if align >= 0.6 else ("moderate" if align >= 0.4 else "weak")

    return {
        "bias": bias, "bias_ru": bias_ru,
        "bos_desc": bos_desc, "choch_desc": choch_desc,
        "quality": quality, "alignment": align,
        "bull_n": bull_n, "bear_n": bear_n,
    }


# ════════════════════════════════════════════════════════════
#  УРОВНИ (2+2 сильных)
# ════════════════════════════════════════════════════════════

def _get_key_levels(fa: FullAnalysis) -> Dict:
    price = fa.current_price or 0.0
    supports, resistances = [], []
    ob_zones, fvg_zones   = [], []
    tf_weight = {"1W": 5, "1wk": 5, "1D": 4, "1d": 4,
                 "4H": 3, "4h": 3, "1H": 2, "1h": 2, "15m": 1}

    for tf_name, tfa in fa.timeframes.items():
        w = tf_weight.get(tf_name, 1)
        for sr in tfa.sr_levels:
            if sr.price <= 0 or (sr.strength < 2 and w < 3):
                continue
            src = f"{tf_name} S/R ×{sr.strength}"
            (supports if sr.type == "support" else resistances).append(
                (sr.price, src, w * sr.strength))
        for ob in tfa.order_blocks:
            if not ob.mitigated:
                ob_zones.append((ob.bottom, ob.top, ob.type, tf_name))
        for fvg in tfa.fvgs:
            if not fvg.filled:
                fvg_zones.append((fvg.bottom, fvg.top, fvg.type, tf_name))
        for liq in tfa.liquidity:
            if liq.swept:
                continue
            lst = resistances if liq.type in ("buy_side", "equal_highs") else supports
            lst.append((liq.price, f"Liquidity {tf_name}", w))

    def dedupe(lvls, tol=0.002):
        lvls_s = sorted(lvls, key=lambda x: x[2], reverse=True)
        res, used = [], [False] * len(lvls_s)
        for i, (p, s, _) in enumerate(lvls_s):
            if used[i] or p <= 0:
                continue
            for j in range(i + 1, len(lvls_s)):
                if not used[j] and abs(p - lvls_s[j][0]) / max(p, 1e-9) < tol:
                    used[j] = True
            res.append((p, s))
            used[i] = True
        return res

    supports    = dedupe(supports)
    resistances = dedupe(resistances)
    sup_below = sorted([(p, s) for p, s in supports    if p < price], key=lambda x: x[0], reverse=True)[:2]
    res_above = sorted([(p, s) for p, s in resistances if p > price], key=lambda x: x[0])[:2]

    def _sort_zone(zones):
        return sorted(zones, key=lambda z: (
            -tf_weight.get(z[3], 1),
            abs((z[0] + z[1]) / 2 - price) / max(price, 1e-9)
        ))

    return {
        "supports":    sup_below,
        "resistances": res_above,
        "ob_zones":    _sort_zone(ob_zones)[:1],
        "fvg_zones":   _sort_zone(fvg_zones)[:1],
    }


# ════════════════════════════════════════════════════════════
#  СЦЕНАРИИ (адаптированы под стратегию)
# ════════════════════════════════════════════════════════════

def _build_scenarios(fa: FullAnalysis, structure: Dict, levels: Dict,
                     phase: str, strategy: StrategyResult) -> Dict:
    sym  = fa.symbol
    bias = structure["bias"]
    sups = levels["supports"]
    ress = levels["resistances"]
    s1 = sups[0][0] if sups          else 0.0
    s2 = sups[1][0] if len(sups) > 1 else 0.0
    r1 = ress[0][0] if ress          else 0.0
    r2 = ress[1][0] if len(ress) > 1 else 0.0
    p  = lambda v: _fmt_price(v, sym) if v else "—"

    long_cond = long_tgt = long_sl = ""
    short_cond = short_tgt = short_sl = ""
    prio = "long" if bias == "bullish" else ("short" if bias == "bearish" else "neutral")
    sname = strategy.name

    if sname == STRATEGY_SMC:
        if "trend↑" in phase or phase == "accumulation":
            long_cond  = f"Откат в OB/FVG · sweep SSL · BOS↑ на 1H · выше {p(s1)}"
            long_tgt, long_sl = p(r1), p(s2 or s1)
            short_cond = f"BOS↓ на 4H + sweep BSL · закрытие ниже {p(s1)}"
            short_tgt, short_sl = p(s2), p(r1)
        elif "trend↓" in phase or phase == "distribution":
            short_cond = f"Отскок в OB/FVG · sweep BSL · BOS↓ на 1H · ниже {p(r1)}"
            short_tgt, short_sl = p(s1), p(r2 or r1)
            long_cond  = f"BOS↑ на 4H + sweep SSL · закрытие выше {p(r1)}"
            long_tgt, long_sl = p(r2), p(s1)
            prio = "short"
        else:
            long_cond  = f"Sweep SSL + ChoCH↑ на 1H · выше {p(s1)}"
            long_tgt, long_sl = p(r1), p(s2)
            short_cond = f"Sweep BSL + ChoCH↓ на 1H · ниже {p(r1)}"
            short_tgt, short_sl = p(s1), p(r2)
            prio = "neutral"

    elif sname == STRATEGY_WYCKOFF:
        if phase == "accumulation":
            long_cond  = f"Spring у {p(s1)} + объём↑ + SOS (закрытие выше {p(r1)})"
            long_tgt, long_sl = p(r2 or r1), p(s2 or s1)
            short_cond = f"Нет SOS · продолжение ниже {p(s1)}"
            short_tgt, short_sl = p(s2), p(r1)
            prio = "long"
        elif phase == "distribution":
            short_cond = f"Upthrust у {p(r1)} + объём↑ + SOW (закрытие ниже {p(s1)})"
            short_tgt, short_sl = p(s2 or s1), p(r2 or r1)
            long_cond  = f"Нет SOW · удержание выше {p(s1)}"
            long_tgt, long_sl = p(r1), p(s2)
            prio = "short"
        else:
            long_cond  = f"Spring + объём↑ у {p(s1)}"
            long_tgt, long_sl = p(r1), p(s2)
            short_cond = f"Upthrust + объём↑ у {p(r1)}"
            short_tgt, short_sl = p(s1), p(r2)
            prio = "neutral"

    elif sname == STRATEGY_TREND:
        if "trend↑" in phase or bias == "bullish":
            long_cond  = f"Откат к EMA20/50 · бычья свеча 1H · выше {p(s1)}"
            long_tgt, long_sl = p(r1), p(s2 or s1)
            short_cond = f"Только при BOS↓ на 4H — против тренда не торгуем"
            short_tgt, short_sl = p(s2), p(r1)
        else:
            short_cond = f"Отскок к EMA20/50 · медвежья свеча 1H · ниже {p(r1)}"
            short_tgt, short_sl = p(s1), p(r2 or r1)
            long_cond  = f"Только при BOS↑ на 4H — против тренда не торгуем"
            long_tgt, long_sl = p(r2), p(s1)
            prio = "short"

    elif sname == STRATEGY_RANGE:
        long_cond  = f"От поддержки {p(s1)} · RSI < 40 · подтверждение 1H"
        long_tgt, long_sl = p(r1), p(s2 or s1)
        short_cond = f"От сопротивления {p(r1)} · RSI > 60 · подтверждение 1H"
        short_tgt, short_sl = p(s1), p(r2 or r1)
        prio = "neutral"

    else:  # Reversal Watch
        long_cond  = f"ChoCH↑ на HTF · закрытие выше {p(r1)}"
        short_cond = f"ChoCH↓ на HTF · закрытие ниже {p(s1)}"
        long_tgt, long_sl = p(r2), p(s1)
        short_tgt, short_sl = p(s2), p(r1)
        prio = "neutral"

    pd_zone = ""
    htf = _get_tf(fa, "1D", "1d", "4H", "4h")
    if htf:
        pd_zone = htf.premium_discount

    return {
        "priority": prio, "pd_zone": pd_zone,
        "long_condition": long_cond, "long_target": long_tgt, "long_stop": long_sl,
        "short_condition": short_cond, "short_target": short_tgt, "short_stop": short_sl,
    }


# ════════════════════════════════════════════════════════════
#  QUALITY SCORE
# ════════════════════════════════════════════════════════════

def _score_signal(fa: FullAnalysis, structure: Dict, levels: Dict,
                  scenarios: Dict, strategy: StrategyResult) -> float:
    score = 0.0
    prio  = scenarios["priority"]
    if not strategy.signal_allowed or prio == "neutral":
        return 0.0

    tgt_str  = scenarios["long_target"]  if prio == "long"  else scenarios["short_target"]
    stop_str = scenarios["long_stop"]    if prio == "long"  else scenarios["short_stop"]
    entry    = fa.current_price or 0.0
    rr       = _calc_rr_float(entry, _parse_price(tgt_str), _parse_price(stop_str))

    score += strategy.confidence * 2.0
    score += 2.0 if structure["alignment"] >= 0.6 else (1.0 if structure["alignment"] >= 0.4 else 0.0)
    score += 2.0 if rr >= 2.5 else (1.0 if rr >= 1.5 else 0.0)
    if levels["ob_zones"] and levels["fvg_zones"]:
        score += 1.5
    elif levels["ob_zones"] or levels["fvg_zones"]:
        score += 0.75

    mtf = _get_tf(fa, "4H", "4h", "1D", "1d")
    if mtf:
        rsi  = _safe(mtf.indicators.rsi, 50)
        macd = mtf.indicators.macd_signal or ""
        if (prio == "long" and rsi < 70) or (prio == "short" and rsi > 30):
            score += 0.5
        if (prio == "long" and macd in ("bullish", "bullish_cross")) or \
           (prio == "short" and macd in ("bearish", "bearish_cross")):
            score += 1.0

    pd = scenarios["pd_zone"]
    if (pd == "discount" and prio == "long") or (pd == "premium" and prio == "short"):
        score += 0.5
    if strategy.conflict:
        score -= 1.5

    return round(min(max(score, 0.0), 10.0), 1)


# ════════════════════════════════════════════════════════════
#  ИНДИКАТОРЫ
# ════════════════════════════════════════════════════════════

def _summarize_indicators(fa: FullAnalysis) -> str:
    mtf = _get_tf(fa, "4H", "4h", "1D", "1d")
    if not mtf:
        return ""
    ind  = mtf.indicators
    rsi  = _safe(ind.rsi, 50)
    vol  = _safe(ind.volume_ratio, 1.0)
    macd = ind.macd_signal or ""
    bb   = ind.bb_position or ""

    parts = []
    if rsi > 70:     parts.append(f"RSI {rsi:.0f} перекуплен")
    elif rsi < 30:   parts.append(f"RSI {rsi:.0f} перепродан")
    else:            parts.append(f"RSI {rsi:.0f}")

    if vol >= 1.3:   parts.append(f"объём ×{vol:.1f}↑")
    elif vol <= 0.7: parts.append(f"объём ×{vol:.1f}↓")

    macd_map = {"bullish_cross": "MACD кросс↑", "bearish_cross": "MACD кросс↓",
                "bullish": "MACD>0", "bearish": "MACD<0"}
    if macd in macd_map: parts.append(macd_map[macd])

    bb_map = {"above_upper": "BB выше", "below_lower": "BB ниже",
              "upper": "BB у верхней", "lower": "BB у нижней"}
    if bb in bb_map: parts.append(bb_map[bb])

    return " · ".join(parts)


# ════════════════════════════════════════════════════════════
#  ВЫВОД ТРЕЙДЕРА (используется везде)
# ════════════════════════════════════════════════════════════

def _trader_conclusion(phase: str, prio: str, structure: Dict,
                       strategy: StrategyResult, no_trade: bool) -> str:
    qual = structure["quality"]

    if no_trade:
        base = {
            "trend↑":       "Тренд бычий, но нет точки входа. Ждём откат в OB/FVG.",
            "trend↓":       "Тренд медвежий, но нет точки входа. Ждём отскок в OB/FVG.",
            "distribution": "Структура слабеет. Шорт только при подтверждении ChoCH. Преждевременный вход — ошибка.",
            "accumulation": "Признаки дна есть, но без ChoCH↑ — только ожидание.",
            "correction":   "Коррекция не завершена. Против HTF не торгуем.",
            "range":        "Диапазон. Торгуем только от чётких границ.",
        }.get(phase, "Рынок неопределённый. Лучшая позиция — вне рынка.")
    elif prio == "long":
        base = {
            "trend↑":       "Тренд на стороне покупателей. Лонг от зоны — приоритетная идея.",
            "trend↓":       "Против тренда. Лонг только при чётком BOS↑ с подтверждением.",
            "accumulation": "Накопление. Лонг при подтверждении — асимметричная идея.",
            "correction":   "Откат в бычьем тренде. Структура не сломана — лонг от зоны.",
            "range":        "От нижней границы. R:R в пользу покупателей.",
        }.get(phase, "Лонг при подтверждении условий.")
    else:
        base = {
            "trend↓":       "Тренд медвежий. Шорт от зоны — приоритетная идея.",
            "trend↑":       "Против тренда. Шорт только при чётком BOS↓ с подтверждением.",
            "distribution": "Распределение. Шорт при ChoCH↓ — асимметричная идея.",
            "correction":   "Отскок в медвежьем тренде. Шорт от зоны с подтверждением.",
            "range":        "От верхней границы. R:R в пользу продавцов.",
        }.get(phase, "Шорт при подтверждении условий.")

    if qual == "weak":
        base += " Структура противоречивая — размер позиции ↓."
    elif structure["alignment"] >= 0.8:
        base += " Таймфреймы согласованы — высокая уверенность."

    return base


# ════════════════════════════════════════════════════════════
#  АНАЛИЗ МОНЕТЫ — ГЛАВНЫЙ РЕЖИМ (Приоритет №1)
#  Формат: discretionary trader, не technical dump
# ════════════════════════════════════════════════════════════

def _fmt_coin_analysis(fa: FullAnalysis, phase: str, structure: Dict,
                       levels: Dict, scenarios: Dict, strategy: StrategyResult,
                       score: float, ind_str: str) -> str:
    sym   = fa.symbol
    price = fa.current_price or 0.0
    prio  = scenarios["priority"]
    sups  = levels["supports"]
    ress  = levels["resistances"]
    s1 = sups[0][0] if sups          else 0.0
    s2 = sups[1][0] if len(sups) > 1 else 0.0
    r1 = ress[0][0] if ress          else 0.0
    r2 = ress[1][0] if len(ress) > 1 else 0.0
    p  = lambda v: _fmt_price(v, sym) if v else "—"

    bias_icon   = "🟢" if structure["bias"] == "bullish" else ("🔴" if structure["bias"] == "bearish" else "⚪")
    phase_label = {"trend↑": "Тренд ↑", "trend↓": "Тренд ↓", "distribution": "Распределение",
                   "accumulation": "Накопление", "correction": "Коррекция", "range": "Диапазон"}.get(phase, phase)
    pd_label    = {"premium": "▲ premium", "discount": "▼ discount",
                   "equilibrium": "— eq"}.get(scenarios["pd_zone"], "")

    # Решение
    no_trade = not strategy.signal_allowed or prio == "neutral"
    if not no_trade:
        tgt_raw  = scenarios["long_target"]  if prio == "long"  else scenarios["short_target"]
        stop_raw = scenarios["long_stop"]    if prio == "long"  else scenarios["short_stop"]
        main_cond = scenarios["long_condition"] if prio == "long" else scenarios["short_condition"]
        alt_cond  = scenarios["short_condition"] if prio == "long" else scenarios["long_condition"]
        d_emoji, decision = ("🟢", "LONG") if prio == "long" else ("🔴", "SHORT")
        tgt  = _parse_price(tgt_raw)
        stop = _parse_price(stop_raw)
        rr   = _calc_rr_float(price, tgt, stop)
        if rr < MIN_RR or score < MIN_SIGNAL_SCORE:
            no_trade = True
    if no_trade:
        d_emoji, decision = "⚪", "WAIT"

    # Контекст рынка
    context = {
        "trend↑":       "Тренд восходящий. EMA выстроены, структура не сломана. Bias — покупки.",
        "trend↓":       "Тренд нисходящий. EMA выстроены вниз, структура держится. Bias — продажи.",
        "distribution": "HTF бычий, но MTF начинает слабеть. Институционалы фиксируют позиции.",
        "accumulation": "HTF медвежий, но MTF показывает первые признаки накопления. Разворот не подтверждён.",
        "correction":   "Коррекция внутри тренда. Структура пока не сломана — ищем завершение отката.",
        "range":        "Флэт без чёткого направления. Границы заданы — только от них.",
    }.get(phase, "Рынок неопределённый.")

    # Что формируется
    forming = []
    if structure["bos_desc"]:
        forming.append(structure["bos_desc"])
    if structure["choch_desc"]:
        forming.append(structure["choch_desc"])
    if levels["ob_zones"]:
        lo, hi, zt, tf = levels["ob_zones"][0]
        forming.append(f"OB {tf} [{p(lo)}–{p(hi)}] — {'нетронутый' if zt == 'bullish' else 'медвежий'}")
    if levels["fvg_zones"]:
        lo, hi, zt, tf = levels["fvg_zones"][0]
        forming.append(f"FVG {tf} [{p(lo)}–{p(hi)}] — незаполненный имбаланс")

    # Invalidation
    if not no_trade and prio == "long":
        invalidation = f"Сценарий отменяется при закрытии ниже `{p(s2 or s1)}`."
    elif not no_trade and prio == "short":
        invalidation = f"Сценарий отменяется при закрытии выше `{p(r2 or r1)}`."
    elif r1 and s1:
        invalidation = f"Выход за `{p(s1)}` или `{p(r1)}` даст направление."
    else:
        invalidation = "Нет чёткого уровня инвалидации."

    # ── Сборка ──────────────────────────────────────────
    L = []

    L.append(f"◆ *{sym}*  `{_fmt_price(price, sym)}`")
    L.append(f"{bias_icon} {phase_label}  ·  {pd_label}")
    L.append(f"_Стратегия: {strategy.name}_")
    L.append("")

    # Решение — первым
    L.append(f"{d_emoji} *{decision}*")
    L.append("")

    # Контекст
    L.append("*Контекст:*")
    L.append(f"_{context}_")
    L.append("")

    # Что формируется
    if forming:
        L.append("*Что формируется:*")
        for item in forming[:3]:
            L.append(f"  · {item}")
        L.append("")

    # Уровни
    L.append("*Ключевые уровни:*")
    if ress:
        pv, src = ress[0]
        L.append(f"  ▲ `{_fmt_price(pv, sym)}`  _{src}_")
        if len(ress) > 1:
            pv2, src2 = ress[1]
            L.append(f"  ▲ `{_fmt_price(pv2, sym)}`  _{src2}_")
    L.append(f"  → `{_fmt_price(price, sym)}`  ← сейчас")
    if sups:
        pv, src = sups[0]
        L.append(f"  ▼ `{_fmt_price(pv, sym)}`  _{src}_")
        if len(sups) > 1:
            pv2, src2 = sups[1]
            L.append(f"  ▼ `{_fmt_price(pv2, sym)}`  _{src2}_")

    if ind_str:
        L.append("")
        L.append(f"_{ind_str}_")
    L.append("")

    # Основной сценарий
    if not no_trade:
        L.append(f"*Сценарий — {decision}:*")
        L.append(f"_{main_cond}_")
        L.append(f"Вход: `{_fmt_price(price, sym)}`  →  Цель: `{tgt_raw}`  →  Стоп: `{stop_raw}`")
        rr_s = _fmt_rr(price, tgt, stop)
        if rr_s:
            L.append(f"_{rr_s}_")
        L.append("")

        # Качество
        bars = _score_bars(score)
        L.append(f"Качество: *{score:.1f}/10*  `{bars}`")
        L.append("")

        # Альтернатива
        if alt_cond:
            L.append("*Альтернатива:*")
            L.append(f"_{alt_cond}_")
            L.append("")

    else:
        # WAIT — всё равно даём сценарии
        L.append("*Сценарии (ждём подтверждения):*")
        if scenarios["long_condition"]:
            L.append(f"🟢 _Лонг: {scenarios['long_condition']}_")
            tgt_l, sl_l = scenarios["long_target"], scenarios["long_stop"]
            if tgt_l not in ("", "—"):
                L.append(f"   Цель `{tgt_l}` · Стоп `{sl_l}`")
        if scenarios["short_condition"]:
            L.append(f"🔴 _Шорт: {scenarios['short_condition']}_")
            tgt_s, sl_s = scenarios["short_target"], scenarios["short_stop"]
            if tgt_s not in ("", "—"):
                L.append(f"   Цель `{tgt_s}` · Стоп `{sl_s}`")
        L.append("")

    # Invalidation
    L.append("*Invalidation:*")
    L.append(f"_{invalidation}_")
    L.append("")

    # Вывод
    L.append("*Вывод:*")
    L.append(f"_{_trader_conclusion(phase, prio, structure, strategy, no_trade)}_")

    if structure["quality"] == "weak":
        L.append("")
        L.append("⚠️ _Структура противоречивая — уменьши размер позиции_")

    return "\n".join(L)


# ════════════════════════════════════════════════════════════
#  УТРЕННИЙ ОТЧЁТ — торговый план
# ════════════════════════════════════════════════════════════

def _fmt_morning(fa, phase, structure, levels, scenarios, strategy, score, ind_str) -> str:
    sym   = fa.symbol
    price = fa.current_price or 0.0
    prio  = scenarios["priority"]
    sups  = levels["supports"]
    ress  = levels["resistances"]
    p     = lambda v: _fmt_price(v, sym) if v else "—"

    bias_icon   = "🟢" if structure["bias"] == "bullish" else ("🔴" if structure["bias"] == "bearish" else "⚪")
    phase_label = {"trend↑": "Тренд ↑", "trend↓": "Тренд ↓", "distribution": "Топ?",
                   "accumulation": "Дно?", "correction": "Откат", "range": "Диапазон"}.get(phase, phase)
    pd_label    = {"premium": "▲ premium", "discount": "▼ discount"}.get(scenarios["pd_zone"], "")

    no_trade = not strategy.signal_allowed or prio == "neutral"
    if not no_trade:
        tgt_raw  = scenarios["long_target"]  if prio == "long"  else scenarios["short_target"]
        stop_raw = scenarios["long_stop"]    if prio == "long"  else scenarios["short_stop"]
        cond     = scenarios["long_condition"] if prio == "long" else scenarios["short_condition"]
        d_emoji, decision = ("🟢", "LONG") if prio == "long" else ("🔴", "SHORT")
        tgt  = _parse_price(tgt_raw)
        stop = _parse_price(stop_raw)
        rr   = _calc_rr_float(price, tgt, stop)
        if rr < MIN_RR or score < MIN_SIGNAL_SCORE:
            no_trade = True
    if no_trade:
        d_emoji, decision = "⚪", "WAIT"

    L = []
    L.append(f"*{sym}*  `{_fmt_price(price, sym)}`")
    L.append(f"{bias_icon} {phase_label}  ·  {pd_label}  ·  _{strategy.name}_")
    L.append("")
    L.append(f"{d_emoji} *{decision}*")

    if not no_trade:
        L.append("")
        L.append(f"_{cond}_")
        L.append(f"Цель: `{tgt_raw}`  Стоп: `{stop_raw}`")
        rr_s = _fmt_rr(price, tgt, stop)
        if rr_s:
            L.append(f"_{rr_s}_")
        bars = _score_bars(score)
        L.append(f"Качество: *{score:.1f}/10*  `{bars}`")

    L.append("")
    struct_parts = []
    if structure["bos_desc"]:   struct_parts.append(structure["bos_desc"])
    if structure["choch_desc"]: struct_parts.append(structure["choch_desc"])
    if struct_parts:
        L.append("  " + "  ·  ".join(struct_parts))

    if ress:
        L.append(f"  ▲ `{_fmt_price(ress[0][0], sym)}`")
    L.append(f"  → `{_fmt_price(price, sym)}`")
    if sups:
        L.append(f"  ▼ `{_fmt_price(sups[0][0], sym)}`")

    if levels["ob_zones"]:
        lo, hi, zt, tf = levels["ob_zones"][0]
        zi = "🟢" if zt == "bullish" else "🔴"
        L.append(f"Зона: {zi} OB {tf} `{p(lo)}–{p(hi)}`")
    elif levels["fvg_zones"]:
        lo, hi, zt, tf = levels["fvg_zones"][0]
        zi = "🟢" if zt == "bullish" else "🔴"
        L.append(f"Зона: {zi} FVG {tf} `{p(lo)}–{p(hi)}`")

    if ind_str:
        L.append(f"_{ind_str}_")

    L.append("")
    L.append(f"_{_trader_conclusion(phase, prio, structure, strategy, no_trade)}_")

    return "\n".join(L)


# ════════════════════════════════════════════════════════════
#  АЛЕРТ
# ════════════════════════════════════════════════════════════

def _fmt_alert_msg(fa, phase, structure, levels, scenarios, strategy,
                   approaching_level: float, distance_pct: float) -> str:
    sym   = fa.symbol
    price = fa.current_price or 0.0
    prio  = scenarios["priority"]

    is_support = approaching_level < price
    if is_support:
        lvl_type = "Поддержка"
        action   = "Готовимся к LONG" if (prio == "long" and strategy.signal_allowed) else "Наблюдаем"
        emoji    = "🟢" if prio == "long" else "👁"
    else:
        lvl_type = "Сопротивление"
        action   = "Готовимся к SHORT" if (prio == "short" and strategy.signal_allowed) else "Наблюдаем"
        emoji    = "🔴" if prio == "short" else "👁"

    L = []
    L.append(f"⚡ *{sym}*  `{_fmt_price(price, sym)}`")
    L.append(f"{lvl_type}: `{_fmt_price(approaching_level, sym)}`  —  {distance_pct:.2f}%")
    L.append("")
    L.append(f"{emoji} *{action}*")

    if prio == "long" and is_support and strategy.signal_allowed:
        L.append(f"_{scenarios['long_condition']}_")
        tgt, sl = scenarios["long_target"], scenarios["long_stop"]
        if tgt not in ("", "—"):
            L.append(f"Цель: `{tgt}`  Стоп: `{sl}`")
    elif prio == "short" and not is_support and strategy.signal_allowed:
        L.append(f"_{scenarios['short_condition']}_")
        tgt, sl = scenarios["short_target"], scenarios["short_stop"]
        if tgt not in ("", "—"):
            L.append(f"Цель: `{tgt}`  Стоп: `{sl}`")

    L.append("")
    L.append(f"_{_trader_conclusion(phase, prio, structure, strategy, not strategy.signal_allowed)}_")
    return "\n".join(L)


# ════════════════════════════════════════════════════════════
#  ВЕЧЕРНИЙ ИТОГ
# ════════════════════════════════════════════════════════════

def _fmt_evening(fa, phase, structure, levels, scenarios, strategy) -> str:
    sym   = fa.symbol
    price = fa.current_price or 0.0
    prio  = scenarios["priority"]
    sups  = levels["supports"]
    ress  = levels["resistances"]

    bias_icon   = "🟢" if structure["bias"] == "bullish" else ("🔴" if structure["bias"] == "bearish" else "⚪")
    phase_label = {"trend↑": "Тренд ↑", "trend↓": "Тренд ↓", "distribution": "Топ?",
                   "accumulation": "Дно?", "correction": "Откат", "range": "Диапазон"}.get(phase, phase)

    L = []
    L.append(f"*{sym}*  `{_fmt_price(price, sym)}`  {bias_icon} {phase_label}")

    struct_parts = []
    if structure["bos_desc"]:   struct_parts.append(structure["bos_desc"])
    if structure["choch_desc"]: struct_parts.append(structure["choch_desc"])
    if struct_parts:
        L.append("  " + "  ·  ".join(struct_parts))

    if ress: L.append(f"  Сопр: `{_fmt_price(ress[0][0], sym)}`")
    if sups: L.append(f"  Подд: `{_fmt_price(sups[0][0], sym)}`")

    L.append("")
    if prio == "long" and strategy.signal_allowed:
        L.append(f"🟢 _{scenarios['long_condition']}_")
    elif prio == "short" and strategy.signal_allowed:
        L.append(f"🔴 _{scenarios['short_condition']}_")
    else:
        L.append("⚪ _Ждём сигнал от уровней_")

    L.append(f"_{_trader_conclusion(phase, prio, structure, strategy, not strategy.signal_allowed)}_")
    return "\n".join(L)


# ════════════════════════════════════════════════════════════
#  MARKET SUMMARY
# ════════════════════════════════════════════════════════════

def _build_market_summary(analyses: List[FullAnalysis], news_text: str = "") -> str:
    if not analyses:
        return ""

    bull = sum(1 for fa in analyses if fa.htf_bias == "bullish")
    bear = sum(1 for fa in analyses if fa.htf_bias == "bearish")
    total = len(analyses)

    if bull >= total * 0.6:
        sentiment = "🟢 Risk-on"
        edge = "Покупки от уровней в приоритете."
    elif bear >= total * 0.6:
        sentiment = "🔴 Risk-off"
        edge = "Продажи от уровней в приоритете."
    else:
        sentiment = "⚪ Нейтрально"
        edge = "Смешанный рынок. Работаем от чётких уровней, без агрессии."

    top_ideas = []
    for fa in analyses:
        phase, _ = _determine_phase(fa)
        if "trend" in phase and fa.htf_bias in ("bullish", "bearish"):
            d = "LONG" if fa.htf_bias == "bullish" else "SHORT"
            top_ideas.append(f"{fa.symbol} {d}")
        if len(top_ideas) >= 3:
            break

    L = ["*MARKET PULSE  —  Торговый план*", ""]
    L.append(f"Настроение: {sentiment}")
    L.append(f"Бычьих: {bull}  ·  Медвежьих: {bear}  ·  Всего: {total}")
    if top_ideas:
        L.append("")
        L.append(f"Приоритет: {' · '.join(top_ideas)}")
    L.append("")
    L.append(f"_{edge}_")

    if news_text:
        L.append("")
        L.append(news_text)

    return "\n".join(L)


# ════════════════════════════════════════════════════════════
#  ГЕНЕРАТОР — ТОЧКА ВХОДА
# ════════════════════════════════════════════════════════════

def generate_report(fa: FullAnalysis, report_type: str = "morning",
                    approaching_level: float = 0.0, distance_pct: float = 0.0) -> str:
    if not fa or not fa.timeframes:
        return f"⚠️ {fa.symbol if fa else '—'}: недостаточно данных."

    phase, _  = _determine_phase(fa)
    structure = _determine_structure(fa)
    levels    = _get_key_levels(fa)
    strategy  = select_strategy(fa, phase)
    scenarios = _build_scenarios(fa, structure, levels, phase, strategy)
    ind_str   = _summarize_indicators(fa)

    if report_type == "coin":
        score = _score_signal(fa, structure, levels, scenarios, strategy)
        return _fmt_coin_analysis(fa, phase, structure, levels, scenarios,
                                   strategy, score, ind_str)
    elif report_type == "morning":
        score = _score_signal(fa, structure, levels, scenarios, strategy)
        return _fmt_morning(fa, phase, structure, levels, scenarios,
                            strategy, score, ind_str)
    elif report_type == "alert":
        return _fmt_alert_msg(fa, phase, structure, levels, scenarios,
                              strategy, approaching_level, distance_pct)
    elif report_type == "evening":
        return _fmt_evening(fa, phase, structure, levels, scenarios, strategy)

    return _trader_conclusion(phase, scenarios["priority"], structure, strategy, True)


# ════════════════════════════════════════════════════════════
#  СБОРКА СООБЩЕНИЙ
# ════════════════════════════════════════════════════════════

def build_morning_message(analyses: List[FullAnalysis], news_text: str = "") -> List[str]:
    now = datetime.now(TZ).strftime("%d.%m.%Y  %H:%M")
    sep = "─" * 28
    messages = [f"{_build_market_summary(analyses, news_text)}\n{sep}\n_{now}_"]
    for fa in analyses:
        icon = {"crypto": "◆", "forex": "◇", "metal": "◈"}.get(fa.asset_type, "·")
        messages.append(f"{icon}  {generate_report(fa, 'morning')}\n{sep}")
    return messages


def build_alert_message(fa: FullAnalysis, approaching_level: float, distance_pct: float) -> str:
    return generate_report(fa, "alert", approaching_level=approaching_level, distance_pct=distance_pct)


def build_evening_message(analyses: List[FullAnalysis]) -> List[str]:
    now = datetime.now(TZ).strftime("%d.%m.%Y  %H:%M")
    sep = "─" * 28
    bull = sum(1 for fa in analyses if fa.htf_bias == "bullish")
    bear = sum(1 for fa in analyses if fa.htf_bias == "bearish")
    tmr  = "Завтра приоритет — покупки." if bull > bear else \
           ("Завтра приоритет — продажи." if bear > bull else "Завтра работаем от границ.")
    messages = [f"*ИТОГ ДНЯ*  ·  {now}\n_{tmr}_\n{sep}"]
    for fa in analyses:
        messages.append(f"{generate_report(fa, 'evening')}\n{sep}")
    return messages


# ── backward compat ─────────────────────────────────────────
def _calc_rr(entry: float, target: float, stop: float) -> str:
    return _fmt_rr(entry, target, stop)
