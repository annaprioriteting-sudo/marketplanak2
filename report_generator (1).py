# ============================================================
#  report_generator.py — Движок решений  v4  (prop-grade)
#  Decision-first. Strategy-aware. Signal quality filter.
#  Совместим с analyzer.py — архитектура не меняется.
# ============================================================

from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional, List, Dict, Tuple
import pytz

from analyzer import (
    FullAnalysis, TimeframeAnalysis, OrderBlock, FairValueGap,
    LiquidityLevel, Pattern, StructureBreak, IndicatorData,
)
from strategy_selector import (
    select_strategy, format_strategy_block, StrategyResult,
    STRATEGY_SMC, STRATEGY_WYCKOFF, STRATEGY_TREND,
    STRATEGY_RANGE, STRATEGY_REVERSAL,
)

logger = logging.getLogger(__name__)

TZ = pytz.timezone("Europe/Moscow")

# ────────────────────────────────────────────────────────────
#  КОНСТАНТЫ КАЧЕСТВА
# ────────────────────────────────────────────────────────────
MIN_RR           = 1.5
MIN_SIGNAL_SCORE = 5.0


# ════════════════════════════════════════════════════════════
#  УТИЛИТЫ
# ════════════════════════════════════════════════════════════

def _safe(val, default=0.0):
    return val if val is not None else default


def _fmt_price(price: float, symbol: str) -> str:
    if price == 0:
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
        risk   = abs(entry - stop)
        reward = abs(target - entry)
        if risk == 0:
            return 0.0
        return round(reward / risk, 2)
    except Exception:
        return 0.0


def _parse_price(s: str) -> float:
    try:
        return float(str(s).replace(",", "").replace(" ", ""))
    except Exception:
        return 0.0


def _fmt_rr(entry: float, target: float, stop: float) -> str:
    rr = _calc_rr_float(entry, target, stop)
    if rr == 0:
        return ""
    try:
        reward_pct = abs(target - entry) / entry * 100
        return f"R:R {rr:.1f}  (+{reward_pct:.1f}%)"
    except Exception:
        return f"R:R {rr:.1f}"


# ════════════════════════════════════════════════════════════
#  БЛОК 1 — ФАЗА РЫНКА
# ════════════════════════════════════════════════════════════

def _determine_phase(fa: FullAnalysis) -> Tuple[str, str]:
    htf = _get_tf(fa, "1W", "1wk", "1D", "1d")
    mtf = _get_tf(fa, "4H", "4h", "1D", "1d")

    if not htf:
        return "нет данных", ""

    ind    = htf.indicators
    price  = fa.current_price or 0.0
    ema20  = _safe(ind.ema20)
    ema50  = _safe(ind.ema50)
    ema200 = _safe(ind.ema200)

    emas_bull = ema20 > ema50 > ema200 and price > ema20
    emas_bear = ema20 < ema50 < ema200 and price < ema20

    htf_bull   = htf.trend == "bullish"
    htf_bear   = htf.trend == "bearish"
    mtf_bull   = mtf and mtf.trend == "bullish"
    mtf_bear   = mtf and mtf.trend == "bearish"

    choch_bull = (htf.last_choch and "bull" in htf.last_choch.type) or \
                 (mtf and mtf.last_choch and "bull" in mtf.last_choch.type)
    choch_bear = (htf.last_choch and "bear" in htf.last_choch.type) or \
                 (mtf and mtf.last_choch and "bear" in mtf.last_choch.type)

    if htf_bull and emas_bull and not mtf_bear:
        return "trend↑", "Восходящий тренд. Структура сохранена."
    if htf_bear and emas_bear and not mtf_bull:
        return "trend↓", "Нисходящий тренд. Структура сохранена."
    if htf_bull and (choch_bear or mtf_bear):
        return "distribution", "HTF бычий, MTF слабеет. Возможная смена."
    if htf_bear and (choch_bull or mtf_bull):
        return "accumulation", "HTF медвежий, MTF показывает разворот."
    if htf_bull and mtf_bear:
        return "correction", "Откат в бычьем тренде."
    if htf_bear and mtf_bull:
        return "correction", "Отскок в медвежьем тренде."
    return "range", "Нет чёткого направления."


# ════════════════════════════════════════════════════════════
#  БЛОК 2 — СТРУКТУРА
# ════════════════════════════════════════════════════════════

def _determine_structure(fa: FullAnalysis) -> Dict:
    bias    = fa.htf_bias
    bias_ru = {"bullish": "бычья", "bearish": "медвежья", "ranging": "нейтральная"}.get(bias, "нейтральная")

    bos_desc   = ""
    choch_desc = ""
    tf_priority = [("1H", "1h"), ("4H", "4h"), ("1D", "1d"), ("1W", "1wk")]

    for tf_a, tf_b in tf_priority:
        tfa = _get_tf(fa, tf_a, tf_b)
        if tfa and tfa.last_bos and not bos_desc:
            b      = tfa.last_bos
            d      = "↑" if "bull" in b.type else "↓"
            tf_lbl = tf_a if tf_a in fa.timeframes else tf_b
            bos_desc = f"BOS {d} · {tf_lbl} · {_fmt_price(b.price, fa.symbol)}"
        if tfa and tfa.last_choch and not choch_desc:
            c      = tfa.last_choch
            d      = "↑" if "bull" in c.type else "↓"
            tf_lbl = tf_a if tf_a in fa.timeframes else tf_b
            choch_desc = f"ChoCH {d} · {tf_lbl} · {_fmt_price(c.price, fa.symbol)}"

    trends  = [t.trend for t in fa.timeframes.values()]
    total   = len(trends) or 1
    bull_n  = trends.count("bullish")
    bear_n  = trends.count("bearish")
    align   = max(bull_n, bear_n) / total

    if align >= 0.6:
        quality = "strong"
    elif align >= 0.4:
        quality = "moderate"
    else:
        quality = "weak"

    return {
        "bias": bias, "bias_ru": bias_ru,
        "bos_desc": bos_desc, "choch_desc": choch_desc,
        "quality": quality, "alignment": align,
        "bull_n": bull_n, "bear_n": bear_n,
    }


# ════════════════════════════════════════════════════════════
#  БЛОК 3 — КЛЮЧЕВЫЕ УРОВНИ (максимум 2+2)
# ════════════════════════════════════════════════════════════

def _get_key_levels(fa: FullAnalysis) -> Dict:
    price        = fa.current_price or 0.0
    supports:    List[Tuple[float, str, int]] = []
    resistances: List[Tuple[float, str, int]] = []
    ob_zones:    List[Tuple[float, float, str, str]] = []
    fvg_zones:   List[Tuple[float, float, str, str]] = []

    tf_weight = {"1W": 5, "1wk": 5, "1D": 4, "1d": 4,
                 "4H": 3, "4h": 3, "1H": 2, "1h": 2, "15m": 1}

    for tf_name, tfa in fa.timeframes.items():
        w = tf_weight.get(tf_name, 1)
        for sr in tfa.sr_levels:
            if sr.price <= 0:
                continue
            if sr.strength < 2 and w < 3:
                continue
            src = f"{tf_name} S/R ×{sr.strength}"
            if sr.type == "support":
                supports.append((sr.price, src, w * sr.strength))
            else:
                resistances.append((sr.price, src, w * sr.strength))
        for ob in tfa.order_blocks:
            if not ob.mitigated:
                ob_zones.append((ob.bottom, ob.top, ob.type, tf_name))
        for fvg in tfa.fvgs:
            if not fvg.filled:
                fvg_zones.append((fvg.bottom, fvg.top, fvg.type, tf_name))
        for liq in tfa.liquidity:
            if liq.swept:
                continue
            if liq.type in ("buy_side", "equal_highs"):
                resistances.append((liq.price, f"Liquidity {tf_name}", w))
            else:
                supports.append((liq.price, f"Liquidity {tf_name}", w))

    def dedupe(lvls, tol=0.002):
        lvls_s = sorted(lvls, key=lambda x: x[2], reverse=True)
        res, used = [], [False] * len(lvls_s)
        for i, (p, s, _) in enumerate(lvls_s):
            if used[i] or p <= 0:
                continue
            for j in range(i + 1, len(lvls_s)):
                q, _, _ = lvls_s[j]
                if not used[j] and abs(p - q) / max(p, 1e-9) < tol:
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

    ob_zones  = _sort_zone(ob_zones)[:1]
    fvg_zones = _sort_zone(fvg_zones)[:1]

    return {
        "supports":    sup_below,
        "resistances": res_above,
        "ob_zones":    ob_zones,
        "fvg_zones":   fvg_zones,
    }


# ════════════════════════════════════════════════════════════
#  БЛОК 4 — СЦЕНАРИИ
#  Адаптируются под выбранную стратегию
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

    p = lambda v: _fmt_price(v, sym) if v else "—"

    long_cond = long_tgt = long_sl = ""
    short_cond = short_tgt = short_sl = ""
    prio = "long" if bias == "bullish" else ("short" if bias == "bearish" else "neutral")

    sname = strategy.name

    # ── SMC: вход в OB/FVG после sweep ликвидности ───────────
    if sname == STRATEGY_SMC:
        if "trend↑" in phase or phase == "accumulation":
            long_cond  = f"Откат в OB/FVG · sweep SSL · BOS↑ на 1H · выше {p(s1)}"
            long_tgt   = p(r1)
            long_sl    = p(s2 or s1)
            short_cond = f"BOS↓ на 4H + sweep BSL · закрытие ниже {p(s1)}"
            short_tgt  = p(s2)
            short_sl   = p(r1)
        elif "trend↓" in phase or phase == "distribution":
            short_cond = f"Отскок в OB/FVG · sweep BSL · BOS↓ на 1H · ниже {p(r1)}"
            short_tgt  = p(s1)
            short_sl   = p(r2 or r1)
            long_cond  = f"BOS↑ на 4H + sweep SSL · закрытие выше {p(r1)}"
            long_tgt   = p(r2)
            long_sl    = p(s1)
            prio = "short"
        else:
            long_cond  = f"Sweep SSL + ChoCH↑ на 1H · выше {p(s1)}"
            long_tgt   = p(r1)
            long_sl    = p(s2)
            short_cond = f"Sweep BSL + ChoCH↓ на 1H · ниже {p(r1)}"
            short_tgt  = p(s1)
            short_sl   = p(r2)
            prio = "neutral"

    # ── Wyckoff: Spring/Upthrust → SOS/SOW ──────────────────
    elif sname == STRATEGY_WYCKOFF:
        if phase == "accumulation":
            long_cond  = f"Spring у {p(s1)} · объём↑ · SOS — закрытие выше {p(r1)}"
            long_tgt   = p(r2 or r1)
            long_sl    = p(s2 or s1)
            short_cond = f"Нет SOS · продолжение ниже {p(s1)}"
            short_tgt  = p(s2)
            short_sl   = p(r1)
            prio = "long"
        elif phase == "distribution":
            short_cond = f"Upthrust у {p(r1)} · объём↑ · SOW — закрытие ниже {p(s1)}"
            short_tgt  = p(s2 or s1)
            short_sl   = p(r2 or r1)
            long_cond  = f"Нет SOW · удержание выше {p(s1)}"
            long_tgt   = p(r1)
            long_sl    = p(s2)
            prio = "short"
        else:
            long_cond  = f"Spring + объём↑ у {p(s1)}"
            long_tgt   = p(r1)
            long_sl    = p(s2)
            short_cond = f"Upthrust + объём↑ у {p(r1)}"
            short_tgt  = p(s1)
            short_sl   = p(r2)
            prio = "neutral"

    # ── Trend Following: откат к EMA, в направлении тренда ──
    elif sname == STRATEGY_TREND:
        if "trend↑" in phase or bias == "bullish":
            long_cond  = f"Откат к EMA20/50 · бычья свеча 1H · выше {p(s1)}"
            long_tgt   = p(r1)
            long_sl    = p(s2 or s1)
            short_cond = f"Только при BOS↓ на 4H — против тренда не торгуем"
            short_tgt  = p(s2)
            short_sl   = p(r1)
        elif "trend↓" in phase or bias == "bearish":
            short_cond = f"Отскок к EMA20/50 · медвежья свеча 1H · ниже {p(r1)}"
            short_tgt  = p(s1)
            short_sl   = p(r2 or r1)
            long_cond  = f"Только при BOS↑ на 4H — против тренда не торгуем"
            long_tgt   = p(r2)
            long_sl    = p(s1)
            prio = "short"
        else:
            long_cond  = f"Подтверждение тренда — откат к {p(s1)}"
            long_tgt   = p(r1)
            long_sl    = p(s2)
            short_cond = f"Подтверждение тренда — отскок к {p(r1)}"
            short_tgt  = p(s1)
            short_sl   = p(r2)
            prio = "neutral"

    # ── Range: от границ диапазона ──────────────────────────
    elif sname == STRATEGY_RANGE:
        long_cond  = f"От поддержки {p(s1)} · RSI < 40 · подтверждение 1H"
        long_tgt   = p(r1)
        long_sl    = p(s2 or s1)
        short_cond = f"От сопротивления {p(r1)} · RSI > 60 · подтверждение 1H"
        short_tgt  = p(s1)
        short_sl   = p(r2 or r1)
        prio = "neutral"

    # ── Reversal Watch: нет сигнала ─────────────────────────
    else:
        prio = "neutral"
        long_cond  = f"ChoCH↑ на HTF · закрытие выше {p(r1)}"
        short_cond = f"ChoCH↓ на HTF · закрытие ниже {p(s1)}"
        long_tgt   = p(r2)
        short_tgt  = p(s2)
        long_sl    = p(s1)
        short_sl   = p(r1)

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
#  БЛОК 5 — QUALITY SCORE (0–10)
#  Учитывает стратегию в расчёте
# ════════════════════════════════════════════════════════════

def _score_signal(fa: FullAnalysis, structure: Dict, levels: Dict,
                  scenarios: Dict, phase: str,
                  strategy: StrategyResult) -> Tuple[float, List[str]]:
    score   = 0.0
    reasons = []
    prio    = scenarios["priority"]

    # Если сигнал запрещён стратегией → 0
    if not strategy.signal_allowed:
        return 0.0, ["Стратегия запрещает сигнал — только анализ"]

    if prio == "neutral":
        return 0.0, ["Нет приоритетного направления"]

    if prio == "long":
        tgt_str  = scenarios["long_target"]
        stop_str = scenarios["long_stop"]
    else:
        tgt_str  = scenarios["short_target"]
        stop_str = scenarios["short_stop"]

    entry  = fa.current_price or 0.0
    target = _parse_price(tgt_str)
    stop   = _parse_price(stop_str)
    rr     = _calc_rr_float(entry, target, stop)

    # 1. Уверенность стратегии (0–2 балла)
    score += strategy.confidence * 2.0
    reasons.append(f"Стратегия: {strategy.name} ({int(strategy.confidence*100)}%)")

    # 2. Alignment структуры (0–2 балла)
    if structure["alignment"] >= 0.6:
        score += 2.0
        reasons.append(f"Структура согласована {int(structure['alignment']*100)}%")
    elif structure["alignment"] >= 0.4:
        score += 1.0

    # 3. R:R (0–2 балла)
    if rr >= 2.5:
        score += 2.0
        reasons.append(f"R:R {rr:.1f} — отличный")
    elif rr >= 1.5:
        score += 1.0
        reasons.append(f"R:R {rr:.1f} — приемлемый")
    else:
        reasons.append(f"R:R {rr:.1f} — недостаточно")

    # 4. OB/FVG confluence (0–1.5 балла)
    if levels["ob_zones"] and levels["fvg_zones"]:
        score += 1.5
        reasons.append("OB + FVG confluence в зоне")
    elif levels["ob_zones"] or levels["fvg_zones"]:
        score += 0.75
        reasons.append("OB или FVG в зоне")

    # 5. RSI не против позиции (0–0.5)
    mtf = _get_tf(fa, "4H", "4h", "1D", "1d")
    if mtf:
        rsi  = _safe(mtf.indicators.rsi, 50)
        macd = mtf.indicators.macd_signal or ""
        if prio == "long" and rsi < 70:
            score += 0.5
        elif prio == "short" and rsi > 30:
            score += 0.5

        # 6. MACD согласован (0–1 балл)
        if prio == "long" and macd in ("bullish", "bullish_cross"):
            score += 1.0
            reasons.append("MACD бычий")
        elif prio == "short" and macd in ("bearish", "bearish_cross"):
            score += 1.0
            reasons.append("MACD медвежий")

    # 7. Premium/Discount (0–0.5)
    pd = scenarios["pd_zone"]
    if pd == "discount" and prio == "long":
        score += 0.5
        reasons.append("Цена в discount")
    elif pd == "premium" and prio == "short":
        score += 0.5
        reasons.append("Цена в premium")

    # 8. Штраф за конфликт стратегий
    if strategy.conflict:
        score -= 1.5
        reasons.append("⚠ Конфликт стратегий −1.5")

    return round(min(max(score, 0.0), 10.0), 1), reasons


def _score_bars(score: float) -> str:
    filled = int(score / 10 * 8)
    return "█" * filled + "░" * (8 - filled)


# ════════════════════════════════════════════════════════════
#  БЛОК 6 — ИНДИКАТОРЫ (одна строка)
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
    if rsi > 70:
        parts.append(f"RSI {rsi:.0f} перекуплен")
    elif rsi < 30:
        parts.append(f"RSI {rsi:.0f} перепродан")
    else:
        parts.append(f"RSI {rsi:.0f}")

    if vol >= 1.3:
        parts.append(f"объём ×{vol:.1f}↑")
    elif vol <= 0.7:
        parts.append(f"объём ×{vol:.1f}↓")

    macd_map = {"bullish_cross": "MACD кросс↑", "bearish_cross": "MACD кросс↓",
                "bullish": "MACD>0", "bearish": "MACD<0"}
    if macd in macd_map:
        parts.append(macd_map[macd])

    bb_map = {"above_upper": "BB выше", "below_lower": "BB ниже",
              "upper": "BB у верхней", "lower": "BB у нижней"}
    if bb in bb_map:
        parts.append(bb_map[bb])

    return " · ".join(parts)


# ════════════════════════════════════════════════════════════
#  БЛОК 7 — SUMMARY
# ════════════════════════════════════════════════════════════

def _build_summary(phase: str, structure: Dict, scenarios: Dict,
                   strategy: StrategyResult) -> str:
    prio = scenarios["priority"]
    pd   = scenarios["pd_zone"]
    qual = structure["quality"]
    sname = strategy.name

    # Базовый текст от фазы
    phase_core = {
        "trend↑":       "Тренд бычий.",
        "trend↓":       "Тренд медвежий.",
        "accumulation": "Возможное дно. Ждём подтверждения.",
        "distribution": "Возможный топ. Ждём подтверждения.",
        "correction":   "Коррекция. Не торгуем против HTF.",
        "range":        "Диапазон.",
    }.get(phase, "Нет чёткого сигнала.")

    # Текст от стратегии
    strat_core = {
        STRATEGY_SMC:     "Ищем OB/FVG после sweep ликвидности.",
        STRATEGY_WYCKOFF: "Ищем Spring/Upthrust с подтверждением объёмом.",
        STRATEGY_TREND:   "Входим в откаты по тренду.",
        STRATEGY_RANGE:   "Торгуем только от границ диапазона.",
        STRATEGY_REVERSAL:"Только наблюдение — сигнала нет.",
    }.get(sname, "")

    extras = []
    if qual == "weak":
        extras.append("Размер позиции ↓")
    if pd == "premium" and prio == "long":
        extras.append("Лонг в premium — не идеально.")
    elif pd == "discount" and prio == "short":
        extras.append("Шорт в discount — не идеально.")

    return " ".join(filter(None, [phase_core, strat_core] + extras))


# ════════════════════════════════════════════════════════════
#  ФОРМАТИРОВАНИЕ — УТРЕННИЙ АНАЛИЗ
# ════════════════════════════════════════════════════════════

def _fmt_morning(fa, phase, structure, levels, scenarios,
                 strategy, score, ind_str, summary) -> str:
    sym   = fa.symbol
    price = fa.current_price or 0.0
    prio  = scenarios["priority"]
    entry = price

    sups = levels["supports"]
    ress = levels["resistances"]
    s1   = sups[0][0] if sups else 0.0
    r1   = ress[0][0] if ress else 0.0
    p    = lambda v: _fmt_price(v, sym) if v else "—"

    # ── Решение ───────────────────────────────────────────
    no_trade = not strategy.signal_allowed or prio == "neutral"

    if not no_trade:
        if prio == "long":
            tgt_raw  = scenarios["long_target"]
            stop_raw = scenarios["long_stop"]
            cond     = scenarios["long_condition"]
            d_emoji  = "🟢"
            decision = "LONG"
        else:
            tgt_raw  = scenarios["short_target"]
            stop_raw = scenarios["short_stop"]
            cond     = scenarios["short_condition"]
            d_emoji  = "🔴"
            decision = "SHORT"

        tgt  = _parse_price(tgt_raw)
        stop = _parse_price(stop_raw)
        rr   = _calc_rr_float(entry, tgt, stop)
        rr_s = _fmt_rr(entry, tgt, stop)

        # R:R и quality filter
        if rr < MIN_RR or score < MIN_SIGNAL_SCORE:
            no_trade = True

    if no_trade:
        d_emoji  = "⚪"
        decision = "NO TRADE"

    # ── Шапка ─────────────────────────────────────────────
    phase_map = {"trend↑": "Тренд ↑", "trend↓": "Тренд ↓",
                 "distribution": "Топ?", "accumulation": "Дно?",
                 "correction": "Откат", "range": "Диапазон"}
    phase_short = phase_map.get(phase, phase)
    bias_icon   = "🟢" if structure["bias"] == "bullish" else \
                  ("🔴" if structure["bias"] == "bearish" else "⚪")
    pd_icon     = {"premium": "▲ premium", "discount": "▼ discount",
                   "equilibrium": "— eq"}.get(scenarios["pd_zone"], "")

    L = []
    L.append(f"*{sym}*  `{_fmt_price(price, sym)}`")
    L.append(f"{bias_icon} {phase_short}  ·  {pd_icon}")
    L.append("")

    # ── РЕШЕНИЕ ───────────────────────────────────────────
    L.append(f"{d_emoji} *▶  {decision}*")
    L.append("")

    # ── СТРАТЕГИЯ ─────────────────────────────────────────
    L.append(format_strategy_block(strategy))
    L.append("")

    if no_trade:
        # Причина отсутствия сигнала
        if not strategy.signal_allowed:
            L.append(f"_Причина: {strategy.conflict_detail or 'Неопределённый рынок'}_")
        elif prio == "neutral":
            L.append("_Нет приоритетного направления_")
        else:
            rr_val = _calc_rr_float(entry,
                _parse_price(scenarios.get("long_target", "0") if prio == "long"
                             else scenarios.get("short_target", "0")),
                _parse_price(scenarios.get("long_stop", "0") if prio == "long"
                             else scenarios.get("short_stop", "0")))
            if rr_val < MIN_RR:
                L.append(f"_R:R {rr_val:.1f} — ниже минимума {MIN_RR}_")
            else:
                L.append(f"_Качество {score:.1f}/10 — ниже минимума {MIN_SIGNAL_SCORE}_")

        # Структура даже при NO TRADE
        L.append("")
        L.append("*Структура:*")
        if structure["bos_desc"]:
            L.append(f"  {structure['bos_desc']}")
        if structure["choch_desc"]:
            L.append(f"  {structure['choch_desc']}")

        L.append("")
        L.append(f"_{summary}_")
        if structure["quality"] == "weak":
            L.append("⚠️ _Структура противоречивая_")
        return "\n".join(L)

    # ── Параметры сделки ──────────────────────────────────
    L.append(f"Вход:   `{_fmt_price(entry, sym)}`")
    L.append(f"Условие: _{cond}_")
    if tgt_raw not in ("", "—"):
        L.append(f"Цель:   `{tgt_raw}`")
    if stop_raw not in ("", "—"):
        L.append(f"Стоп:   `{stop_raw}`")
    if rr_s:
        L.append(f"        _{rr_s}_")
    L.append("")

    # ── Качество ──────────────────────────────────────────
    bars = _score_bars(score)
    L.append(f"Качество:  *{score:.1f}/10*  `{bars}`")
    L.append("")

    # ── Структура ─────────────────────────────────────────
    L.append("*Структура:*")
    if structure["bos_desc"]:
        L.append(f"  {structure['bos_desc']}")
    if structure["choch_desc"]:
        L.append(f"  {structure['choch_desc']}")
    L.append("")

    # ── Уровни (2 ключевых) ───────────────────────────────
    L.append("*Уровни:*")
    if ress:
        pv, src = ress[0]
        L.append(f"  ▲ `{_fmt_price(pv, sym)}`  _{src}_")
    L.append(f"  → `{_fmt_price(price, sym)}`  ← цена")
    if sups:
        pv, src = sups[0]
        L.append(f"  ▼ `{_fmt_price(pv, sym)}`  _{src}_")

    # ── Зона OB/FVG ───────────────────────────────────────
    zone_str = ""
    if levels["ob_zones"]:
        lo, hi, zt, tf = levels["ob_zones"][0]
        zi = "🟢" if zt == "bullish" else "🔴"
        zone_str = f"{zi} OB {tf}  `{p(lo)} – {p(hi)}`"
    elif levels["fvg_zones"]:
        lo, hi, zt, tf = levels["fvg_zones"][0]
        zi = "🟢" if zt == "bullish" else "🔴"
        zone_str = f"{zi} FVG {tf}  `{p(lo)} – {p(hi)}`"
    if zone_str:
        L.append("")
        L.append(f"Зона: {zone_str}")

    # ── Индикаторы ────────────────────────────────────────
    if ind_str:
        L.append("")
        L.append(f"_{ind_str}_")

    # ── Summary ───────────────────────────────────────────
    L.append("")
    L.append(f"_{summary}_")

    if structure["quality"] == "weak":
        L.append("⚠️ _Структура слабая — уменьши размер позиции_")

    return "\n".join(L)


# ════════════════════════════════════════════════════════════
#  ФОРМАТИРОВАНИЕ — АЛЕРТ
# ════════════════════════════════════════════════════════════

def _fmt_alert_msg(fa, phase, structure, levels, scenarios,
                   strategy, summary,
                   approaching_level: float, distance_pct: float) -> str:
    sym   = fa.symbol
    price = fa.current_price or 0.0
    prio  = scenarios["priority"]

    is_support = approaching_level < price

    if is_support:
        lvl_type = "Поддержка"
        if prio == "long" and strategy.signal_allowed:
            action = "Готовимся к LONG"
            emoji  = "🟢"
        else:
            action = "Следим за уровнем"
            emoji  = "👁"
    else:
        lvl_type = "Сопротивление"
        if prio == "short" and strategy.signal_allowed:
            action = "Готовимся к SHORT"
            emoji  = "🔴"
        else:
            action = "Следим за уровнем"
            emoji  = "👁"

    L = []
    L.append(f"⚡ *{sym}*  `{_fmt_price(price, sym)}`")
    L.append(f"Стратегия: {strategy.name}")
    L.append("")
    L.append(f"{lvl_type}: `{_fmt_price(approaching_level, sym)}`  — {distance_pct:.2f}% до уровня")
    L.append("")
    L.append(f"{emoji} *Действие: {action}*")

    if prio == "long" and is_support and strategy.signal_allowed:
        tgt  = scenarios["long_target"]
        sl   = scenarios["long_stop"]
        cond = scenarios["long_condition"]
        L.append(f"Условие: _{cond}_")
        if tgt not in ("", "—"):
            L.append(f"Цель: `{tgt}`  Стоп: `{sl}`")
    elif prio == "short" and not is_support and strategy.signal_allowed:
        tgt  = scenarios["short_target"]
        sl   = scenarios["short_stop"]
        cond = scenarios["short_condition"]
        L.append(f"Условие: _{cond}_")
        if tgt not in ("", "—"):
            L.append(f"Цель: `{tgt}`  Стоп: `{sl}`")

    L.append("")
    L.append(f"_{summary}_")
    return "\n".join(L)


# ════════════════════════════════════════════════════════════
#  ФОРМАТИРОВАНИЕ — ВЕЧЕРНИЙ ИТОГ
# ════════════════════════════════════════════════════════════

def _fmt_evening(fa, phase, structure, levels, scenarios, strategy, summary) -> str:
    sym   = fa.symbol
    price = fa.current_price or 0.0
    prio  = scenarios["priority"]
    sups  = levels["supports"]
    ress  = levels["resistances"]

    phase_map = {"trend↑": "Тренд ↑", "trend↓": "Тренд ↓",
                 "distribution": "Топ?", "accumulation": "Дно?",
                 "correction": "Откат", "range": "Диапазон"}
    phase_short = phase_map.get(phase, phase)
    bias_icon   = "🟢" if structure["bias"] == "bullish" else \
                  ("🔴" if structure["bias"] == "bearish" else "⚪")

    L = []
    L.append(f"*{sym}*  `{_fmt_price(price, sym)}`  {bias_icon} {phase_short}")
    L.append(f"_{strategy.name}_")

    struct_parts = []
    if structure["bos_desc"]:   struct_parts.append(structure["bos_desc"])
    if structure["choch_desc"]: struct_parts.append(structure["choch_desc"])
    if struct_parts:
        L.append("  " + "  ·  ".join(struct_parts))

    if ress:
        pv, _ = ress[0]
        L.append(f"  Сопр: `{_fmt_price(pv, sym)}`")
    if sups:
        pv, _ = sups[0]
        L.append(f"  Подд: `{_fmt_price(pv, sym)}`")

    L.append("")
    if prio == "long" and strategy.signal_allowed:
        L.append(f"🟢 Завтра: {scenarios['long_condition']}")
    elif prio == "short" and strategy.signal_allowed:
        L.append(f"🔴 Завтра: {scenarios['short_condition']}")
    else:
        L.append("⚪ Завтра: Ждём сигнал — рынок неопределённый")

    L.append(f"_{summary}_")
    return "\n".join(L)


# ════════════════════════════════════════════════════════════
#  MARKET SUMMARY
# ════════════════════════════════════════════════════════════

def _build_market_summary(analyses: List[FullAnalysis]) -> str:
    if not analyses:
        return ""

    bull_count = sum(1 for fa in analyses if fa.htf_bias == "bullish")
    bear_count = sum(1 for fa in analyses if fa.htf_bias == "bearish")
    total      = len(analyses)

    if bull_count >= total * 0.6:
        market_trend = "🟢 Бычий"
        edge = "Покупки от уровней"
    elif bear_count >= total * 0.6:
        market_trend = "🔴 Медвежий"
        edge = "Продажи от уровней"
    else:
        market_trend = "⚪ Смешанный"
        edge = "Нейтрально — ждём разрешения"

    top_ideas = []
    for fa in analyses:
        phase, _ = _determine_phase(fa)
        if "trend" in phase and fa.htf_bias in ("bullish", "bearish"):
            direction = "LONG" if fa.htf_bias == "bullish" else "SHORT"
            top_ideas.append(f"{fa.symbol} → {direction}")
        if len(top_ideas) >= 2:
            break

    L = []
    L.append("*MARKET PULSE*")
    L.append(f"Рынок: {market_trend}  ({bull_count}↑ / {bear_count}↓ из {total})")
    if top_ideas:
        L.append(f"Идеи: {' · '.join(top_ideas)}")
    L.append(f"Преимущество: {edge}")

    return "\n".join(L)


# ════════════════════════════════════════════════════════════
#  ГЕНЕРАТОР — ОСНОВНАЯ ТОЧКА ВХОДА
# ════════════════════════════════════════════════════════════

def generate_report(fa: FullAnalysis, report_type: str = "morning",
                    approaching_level: float = 0.0, distance_pct: float = 0.0) -> str:
    if not fa or not fa.timeframes:
        sym = fa.symbol if fa else "—"
        return f"⚠️ {sym}: недостаточно данных."

    phase, _  = _determine_phase(fa)
    structure = _determine_structure(fa)
    levels    = _get_key_levels(fa)
    strategy  = select_strategy(fa, phase)          # ← выбор стратегии
    scenarios = _build_scenarios(fa, structure, levels, phase, strategy)
    ind_str   = _summarize_indicators(fa)
    summary   = _build_summary(phase, structure, scenarios, strategy)

    if report_type == "morning":
        score, _ = _score_signal(fa, structure, levels, scenarios, phase, strategy)
        return _fmt_morning(fa, phase, structure, levels, scenarios,
                            strategy, score, ind_str, summary)

    elif report_type == "alert":
        return _fmt_alert_msg(fa, phase, structure, levels, scenarios,
                              strategy, summary, approaching_level, distance_pct)

    elif report_type == "evening":
        return _fmt_evening(fa, phase, structure, levels, scenarios, strategy, summary)

    return summary


# ════════════════════════════════════════════════════════════
#  СБОРКА ДЛЯ bot.py
# ════════════════════════════════════════════════════════════

def build_morning_message(analyses: List[FullAnalysis]) -> List[str]:
    now = datetime.now(TZ).strftime("%d.%m.%Y  %H:%M")
    sep = "─" * 28

    market_summary = _build_market_summary(analyses)
    messages = [f"{market_summary}\n{sep}\n_{now}_"]

    for fa in analyses:
        icon = {"crypto": "◆", "forex": "◇", "metal": "◈"}.get(fa.asset_type, "·")
        text = generate_report(fa, "morning")
        messages.append(f"{icon}  {text}\n{sep}")

    return messages


def build_alert_message(fa: FullAnalysis, approaching_level: float, distance_pct: float) -> str:
    return generate_report(fa, "alert",
                           approaching_level=approaching_level,
                           distance_pct=distance_pct)


def build_evening_message(analyses: List[FullAnalysis]) -> List[str]:
    now = datetime.now(TZ).strftime("%d.%m.%Y  %H:%M")
    sep = "─" * 28
    messages = [f"*ИТОГ ДНЯ*  ·  {now}\n{sep}"]
    for fa in analyses:
        text = generate_report(fa, "evening")
        messages.append(f"{text}\n{sep}")
    return messages


# ── backward compat ──────────────────────────────────────────
def _calc_rr(entry: float, target: float, stop: float) -> str:
    return _fmt_rr(entry, target, stop)
