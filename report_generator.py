# ============================================================
#  report_generator.py — Движок решений
#  Без внешних API. Чистая логика на правилах.
#  Совместим с объектами из analyzer.py
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

logger = logging.getLogger(__name__)

TZ = pytz.timezone("Europe/Moscow")


# ════════════════════════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
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


# ════════════════════════════════════════════════════════════
#  БЛОК 1 — ФАЗА РЫНКА
#
#  Правила (приоритет сверху вниз):
#  trend↑   — HTF bullish + EMA20>50>200 + цена выше EMA20 + нет медвежьего MTF
#  trend↓   — HTF bearish + EMA20<50<200 + цена ниже EMA20 + нет бычьего MTF
#  distribution — HTF бычий, но MTF медвежий или ChoCH вниз
#  accumulation — HTF медвежий, но MTF бычий или ChoCH вверх
#  correction   — HTF и MTF разнонаправлены
#  range        — нет чёткой картины
# ════════════════════════════════════════════════════════════

def _determine_phase(fa: FullAnalysis) -> Tuple[str, str]:
    htf = _get_tf(fa, "1W", "1wk", "1D", "1d")
    mtf = _get_tf(fa, "4H", "4h", "1D", "1d")

    if not htf:
        return "нет данных", ""

    ind = htf.indicators
    price  = fa.current_price or 0.0
    ema20  = _safe(ind.ema20)
    ema50  = _safe(ind.ema50)
    ema200 = _safe(ind.ema200)

    emas_bull  = ema20 > ema50 > ema200 and price > ema20
    emas_bear  = ema20 < ema50 < ema200 and price < ema20

    htf_bull  = htf.trend == "bullish"
    htf_bear  = htf.trend == "bearish"
    mtf_bull  = mtf and mtf.trend == "bullish"
    mtf_bear  = mtf and mtf.trend == "bearish"

    choch_bull = (htf.last_choch and "bull" in htf.last_choch.type) or \
                 (mtf and mtf.last_choch and "bull" in mtf.last_choch.type)
    choch_bear = (htf.last_choch and "bear" in htf.last_choch.type) or \
                 (mtf and mtf.last_choch and "bear" in mtf.last_choch.type)

    if htf_bull and emas_bull and not mtf_bear:
        return "trend↑", "Восходящий тренд. EMA выстроены. Структура не нарушена."
    if htf_bear and emas_bear and not mtf_bull:
        return "trend↓", "Нисходящий тренд. EMA выстроены. Структура не нарушена."
    if htf_bull and (choch_bear or mtf_bear):
        return "distribution", "HTF бычий, но MTF слабеет. Возможна смена тренда."
    if htf_bear and (choch_bull or mtf_bull):
        return "accumulation", "HTF медвежий, но MTF показывает признаки разворота."
    if htf_bull and mtf_bear:
        return "correction", "Откат в бычьем тренде. Ищем уровни для входа."
    if htf_bear and mtf_bull:
        return "correction", "Откат в медвежьем тренде. Ищем уровни для входа."
    return "range", "Нет чёткого направления. Работа от границ диапазона."


# ════════════════════════════════════════════════════════════
#  БЛОК 2 — СТРУКТУРА И BIAS
#
#  quality: смотрим какой процент таймфреймов согласован
#  >=60% одного направления → сильная
#  >=40% → умеренная
#  иначе → слабая
# ════════════════════════════════════════════════════════════

def _determine_structure(fa: FullAnalysis) -> Dict:
    bias = fa.htf_bias
    bias_ru = {"bullish": "бычья", "bearish": "медвежья", "ranging": "нейтральная"}.get(bias, "нейтральная")

    bos_desc = ""
    choch_desc = ""
    tf_priority = [("1H", "1h"), ("4H", "4h"), ("1D", "1d"), ("1W", "1wk")]

    for tf_a, tf_b in tf_priority:
        tfa = _get_tf(fa, tf_a, tf_b)
        if tfa and tfa.last_bos and not bos_desc:
            b = tfa.last_bos
            d = "↑" if "bull" in b.type else "↓"
            tf_lbl = tf_a if tf_a in fa.timeframes else tf_b
            bos_desc = f"BOS {d} · {tf_lbl} · {_fmt_price(b.price, fa.symbol)}"
        if tfa and tfa.last_choch and not choch_desc:
            c = tfa.last_choch
            d = "↑" if "bull" in c.type else "↓"
            tf_lbl = tf_a if tf_a in fa.timeframes else tf_b
            choch_desc = f"ChoCH {d} · {tf_lbl} · {_fmt_price(c.price, fa.symbol)}"

    trends = [t.trend for t in fa.timeframes.values()]
    total  = len(trends) or 1
    bull_n = trends.count("bullish")
    bear_n = trends.count("bearish")

    if max(bull_n, bear_n) / total >= 0.6:
        quality = "сильная"
    elif max(bull_n, bear_n) / total >= 0.4:
        quality = "умеренная"
    else:
        quality = "слабая / противоречивая"

    return {
        "bias": bias, "bias_ru": bias_ru,
        "bos_desc": bos_desc, "choch_desc": choch_desc,
        "quality": quality, "bull_n": bull_n, "bear_n": bear_n,
    }


# ════════════════════════════════════════════════════════════
#  БЛОК 3 — КЛЮЧЕВЫЕ УРОВНИ
#
#  Источники (по весу таймфрейма):
#  1W/1D → S/R уровни, OB, FVG, ликвидность
#  Дедупликация в радиусе 0.15% от цены
#  Итог: 3 поддержки снизу + 3 сопротивления сверху
# ════════════════════════════════════════════════════════════

def _get_key_levels(fa: FullAnalysis) -> Dict:
    price = fa.current_price or 0.0
    supports:    List[Tuple[float, str]] = []
    resistances: List[Tuple[float, str]] = []
    ob_zones:    List[Tuple[float, float, str, str]] = []
    fvg_zones:   List[Tuple[float, float, str, str]] = []

    for tf_name, tfa in fa.timeframes.items():
        for sr in tfa.sr_levels:
            if sr.price <= 0:
                continue
            src = f"S/R {tf_name}(x{sr.strength})"
            if sr.type == "support":
                supports.append((sr.price, src))
            else:
                resistances.append((sr.price, src))
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
                resistances.append((liq.price, f"BSL {tf_name}"))
            else:
                supports.append((liq.price, f"SSL {tf_name}"))

    def dedupe(lvls: List[Tuple[float, str]], tol=0.0015) -> List[Tuple[float, str]]:
        res, used = [], [False] * len(lvls)
        for i, (p, s) in enumerate(lvls):
            if used[i] or p <= 0:
                continue
            cluster = [(p, s)]
            for j in range(i + 1, len(lvls)):
                q, t = lvls[j]
                if not used[j] and abs(p - q) / max(p, 1e-9) < tol:
                    cluster.append((q, t))
                    used[j] = True
            avg = sum(x[0] for x in cluster) / len(cluster)
            res.append((avg, cluster[0][1]))
            used[i] = True
        return res

    supports    = dedupe(supports)
    resistances = dedupe(resistances)
    sup_below = sorted([(p, s) for p, s in supports    if p < price], key=lambda x: x[0], reverse=True)[:3]
    res_above = sorted([(p, s) for p, s in resistances if p > price], key=lambda x: x[0])[:3]

    return {
        "supports":    sup_below,
        "resistances": res_above,
        "ob_zones":    ob_zones[:3],
        "fvg_zones":   fvg_zones[:3],
    }


# ════════════════════════════════════════════════════════════
#  БЛОК 4 — СЦЕНАРИИ
#
#  trend↑  → основной лонг от коррекции, альт шорт при BOS↓
#  trend↓  → основной шорт от отскока, альт лонг при BOS↑
#  range   → лонг от нижней, шорт от верхней
#  accum   → лонг при ChoCH↑
#  distr   → шорт при ChoCH↓
#  corr    → ждём завершения коррекции
# ════════════════════════════════════════════════════════════

def _build_scenarios(fa: FullAnalysis, structure: Dict, levels: Dict, phase: str) -> Dict:
    sym   = fa.symbol
    bias  = structure["bias"]
    sups  = levels["supports"]
    ress  = levels["resistances"]

    s1 = sups[0][0] if sups          else 0.0
    s2 = sups[1][0] if len(sups) > 1 else 0.0
    r1 = ress[0][0] if ress          else 0.0
    r2 = ress[1][0] if len(ress) > 1 else 0.0

    p = lambda v: _fmt_price(v, sym) if v else "—"

    long_cond = long_tgt = long_sl = ""
    short_cond = short_tgt = short_sl = ""
    prio = "long" if bias == "bullish" else ("short" if bias == "bearish" else "neutral")

    if "trend↑" in phase:
        long_cond  = f"откат в OB/FVG · подтверждение 1H · выше {p(s1)}"
        long_tgt   = p(r1)
        long_sl    = p(s2)
        short_cond = f"BOS↓ на 4H · закрытие ниже {p(s1)}"
        short_tgt  = p(s2)
        short_sl   = p(r1)

    elif "trend↓" in phase:
        short_cond = f"отскок в OB/FVG · подтверждение 1H · ниже {p(r1)}"
        short_tgt  = p(s1)
        short_sl   = p(r2)
        long_cond  = f"BOS↑ на 4H · закрытие выше {p(r1)}"
        long_tgt   = p(r2)
        long_sl    = p(s1)

    elif phase == "accumulation":
        long_cond  = f"ChoCH↑ подтверждён · выше {p(r1)}"
        long_tgt   = p(r2)
        long_sl    = p(s1)
        short_cond = f"нет ChoCH · продолжение ниже {p(s1)}"
        short_tgt  = p(s2)
        short_sl   = p(r1)
        prio = "long"

    elif phase == "distribution":
        short_cond = f"ChoCH↓ подтверждён · ниже {p(s1)}"
        short_tgt  = p(s2)
        short_sl   = p(r1)
        long_cond  = f"нет ChoCH · удержание выше {p(s1)}"
        long_tgt   = p(r1)
        long_sl    = p(s2)
        prio = "short"

    elif "correction" in phase:
        if bias == "bullish":
            long_cond  = f"остановка коррекции у {p(s1)} · бычья свеча 1H"
            long_tgt   = p(r1)
            long_sl    = p(s2)
            short_cond = f"пробой {p(s1)} с закрытием ниже"
            short_tgt  = p(s2)
            short_sl   = p(r1)
        else:
            short_cond = f"остановка отскока у {p(r1)} · медвежья свеча 1H"
            short_tgt  = p(s1)
            short_sl   = p(r2)
            long_cond  = f"пробой {p(r1)} с закрытием выше"
            long_tgt   = p(r2)
            long_sl    = p(s1)

    else:  # range
        long_cond  = f"от нижней границы {p(s1)} · подтверждение 1H"
        long_tgt   = p(r1)
        long_sl    = p(s2)
        short_cond = f"от верхней границы {p(r1)} · подтверждение 1H"
        short_tgt  = p(s1)
        short_sl   = p(r2)
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
#  БЛОК 5 — ПАТТЕРНЫ И ИНДИКАТОРЫ
# ════════════════════════════════════════════════════════════

def _summarize_patterns(fa: FullAnalysis) -> List[str]:
    lines, seen = [], set()
    for tf_name in ["1D", "1d", "4H", "4h", "1H", "1h", "15m"]:
        if tf_name not in fa.timeframes:
            continue
        for pat in fa.timeframes[tf_name].patterns:
            if pat.name in seen:
                continue
            seen.add(pat.name)
            icon = "↑" if pat.direction == "bullish" else ("↓" if pat.direction == "bearish" else "↔")
            lines.append(f"{pat.name} {icon} · {tf_name} · цель {_fmt_price(pat.target, fa.symbol)} · {int(pat.confidence*100)}%")
    return lines[:3]


def _summarize_indicators(fa: FullAnalysis) -> str:
    mtf = _get_tf(fa, "4H", "4h", "1D", "1d")
    if not mtf:
        return ""
    ind  = mtf.indicators
    rsi  = _safe(ind.rsi, 50)
    vol  = _safe(ind.volume_ratio, 1.0)
    macd = ind.macd_signal or ""
    bb   = ind.bb_position or ""

    rsi_s = "перекуплен" if rsi > 70 else ("перепродан" if rsi < 30 else f"RSI {rsi:.0f}")
    macd_map = {"bullish_cross": "MACD↑ кросс", "bearish_cross": "MACD↓ кросс",
                "bullish": "MACD>0", "bearish": "MACD<0"}
    bb_map   = {"above_upper": "BB выше верхней", "below_lower": "BB ниже нижней",
                "upper": "BB у верхней", "lower": "BB у нижней", "middle": ""}

    parts = [rsi_s]
    if vol != 1.0:
        parts.append(f"объём ×{vol:.1f}")
    if macd in macd_map:
        parts.append(macd_map[macd])
    if bb in bb_map and bb_map[bb]:
        parts.append(bb_map[bb])
    return " · ".join(parts)


# ════════════════════════════════════════════════════════════
#  БЛОК 6 — ACTIONABLE SUMMARY (1-2 строки, только действие)
# ════════════════════════════════════════════════════════════

def _build_summary(phase: str, structure: Dict, scenarios: Dict) -> str:
    bias    = structure["bias"]
    quality = structure["quality"]
    prio    = scenarios["priority"]
    pd_zone = scenarios["pd_zone"]

    core = {
        "trend↑":       "Тренд бычий. Ждём коррекцию в OB/FVG для лонга.",
        "trend↓":       "Тренд медвежий. Ждём отскок в OB/FVG для шорта.",
        "accumulation": "Возможное дно. Лонг только при подтверждённом ChoCH.",
        "distribution": "Возможный топ. Шорт только при подтверждённом ChoCH.",
        "correction":   "Ждём завершения коррекции — не торгуем против HTF.",
        "range":        "Диапазон. Торговля от границ, не от середины.",
    }.get(phase, "Нет чёткого сигнала. Ждём.")

    extras = []
    if quality == "слабая / противоречивая":
        extras.append("Структура противоречивая — уменьши размер позиции.")
    if pd_zone == "premium" and prio == "long":
        extras.append("Цена в premium — лонг не приоритетен.")
    if pd_zone == "discount" and prio == "short":
        extras.append("Цена в discount — шорт не приоритетен.")

    return " ".join([core] + extras)


# ════════════════════════════════════════════════════════════
#  ГЕНЕРАТОР — ОСНОВНАЯ ТОЧКА ВХОДА
# ════════════════════════════════════════════════════════════

def generate_report(fa: FullAnalysis, report_type: str = "morning") -> str:
    if not fa or not fa.timeframes:
        sym = fa.symbol if fa else "—"
        return f"⚠️ {sym}: недостаточно данных."

    phase, phase_comment = _determine_phase(fa)
    structure = _determine_structure(fa)
    levels    = _get_key_levels(fa)
    scenarios = _build_scenarios(fa, structure, levels, phase)
    patterns  = _summarize_patterns(fa)
    ind_str   = _summarize_indicators(fa)
    summary   = _build_summary(phase, structure, scenarios)

    if report_type == "morning":
        return _fmt_morning(fa, phase, phase_comment, structure, levels, scenarios, patterns, ind_str, summary)
    elif report_type == "alert":
        return _fmt_alert(fa, phase, structure, levels, scenarios, summary)
    elif report_type == "evening":
        return _fmt_evening(fa, phase, structure, levels, scenarios, summary)
    return summary


# ════════════════════════════════════════════════════════════
#  ФОРМАТИРОВАНИЕ
# ════════════════════════════════════════════════════════════

def _fmt_morning(fa, phase, phase_comment, structure, levels, scenarios, patterns, ind_str, summary) -> str:
    sym   = fa.symbol
    price = fa.current_price or 0.0
    prio  = scenarios["priority"]
    picon = {"long": "🟢", "short": "🔴", "neutral": "⚪"}.get(prio, "⚪")
    pd_emoji = {"premium": "🔴 premium", "discount": "🟢 discount", "equilibrium": "⚪ equilibrium"}
    pd_ru = pd_emoji.get(scenarios["pd_zone"], scenarios["pd_zone"] or "")

    L = []
    # ── Шапка
    L.append(f"*{sym}*  `{_fmt_price(price, sym)}`")
    L.append(f"Фаза: *{phase}*  ·  {structure['bias_ru']}  ·  {pd_ru}")
    if phase_comment:
        L.append(f"_{phase_comment}_")
    L.append("")
    # ── Структура
    struct_parts = []
    if structure["bos_desc"]:   struct_parts.append(structure["bos_desc"])
    if structure["choch_desc"]: struct_parts.append(structure["choch_desc"])
    if struct_parts:
        L.append("Структура: " + "  ·  ".join(struct_parts))
        L.append("")
    # ── Уровни
    L.append("📍 Уровни:")
    for p, src in reversed(levels["resistances"]):
        L.append(f"  `{_fmt_price(p, sym)}` ▲ сопр  _{src}_")
    L.append(f"  `{_fmt_price(price, sym)}` ← цена")
    for p, src in levels["supports"]:
        L.append(f"  `{_fmt_price(p, sym)}` ▼ подд  _{src}_")
    L.append("")
    # ── OB / FVG
    zones = []
    for lo, hi, ob_t, tf in levels["ob_zones"][:2]:
        ic = "🟢" if ob_t == "bullish" else "🔴"
        zones.append(f"{ic} OB {tf} [{_fmt_price(lo, sym)}–{_fmt_price(hi, sym)}]")
    for lo, hi, fvg_t, tf in levels["fvg_zones"][:2]:
        ic = "🟢" if fvg_t == "bullish" else "🔴"
        zones.append(f"{ic} FVG {tf} [{_fmt_price(lo, sym)}–{_fmt_price(hi, sym)}]")
    if zones:
        L.append("Зоны интереса:")
        for z in zones: L.append(f"  {z}")
        L.append("")
    # ── Паттерны
    if patterns:
        L.append("Паттерны: " + "  ·  ".join(patterns))
        L.append("")
    # ── Индикаторы
    if ind_str:
        L.append(f"_{ind_str}_")
        L.append("")
    # ── СИГНАЛ
    prio_ru = {"long": "ЛОНГ", "short": "ШОРТ", "neutral": "ЖДЁМ"}.get(prio, prio)
    L.append("────────────────────────")
    L.append(f"{picon} *{prio_ru}*")
    L.append("")
    if prio == "long" and scenarios["long_condition"]:
        tgt_str  = scenarios["long_target"]
        stop_str = scenarios["long_stop"]
        rr = _calc_rr(price, _parse_price(tgt_str), _parse_price(stop_str))
        L.append(f"Условие входа:")
        L.append(f"  {scenarios['long_condition']}")
        if tgt_str  not in ("", "—"): L.append(f"Цель:  `{tgt_str}`")
        if stop_str not in ("", "—"): L.append(f"Стоп:  `{stop_str}`")
        if rr: L.append(f"_{rr}_")
        L.append("")
        if scenarios["short_condition"]:
            L.append(f"Альт (шорт если): _{scenarios['short_condition']}_")
    elif prio == "short" and scenarios["short_condition"]:
        tgt_str  = scenarios["short_target"]
        stop_str = scenarios["short_stop"]
        rr = _calc_rr(price, _parse_price(tgt_str), _parse_price(stop_str))
        L.append(f"Условие входа:")
        L.append(f"  {scenarios['short_condition']}")
        if tgt_str  not in ("", "—"): L.append(f"Цель:  `{tgt_str}`")
        if stop_str not in ("", "—"): L.append(f"Стоп:  `{stop_str}`")
        if rr: L.append(f"_{rr}_")
        L.append("")
        if scenarios["long_condition"]:
            L.append(f"Альт (лонг если): _{scenarios['long_condition']}_")
    else:
        if scenarios["long_condition"]:
            rr = _calc_rr(price, _parse_price(scenarios["long_target"]), _parse_price(scenarios["long_stop"]))
            L.append(f"🟢 Лонг если: {scenarios['long_condition']}")
            parts = []
            if scenarios["long_target"] not in ("", "—"): parts.append(f"Цель `{scenarios['long_target']}`")
            if scenarios["long_stop"]   not in ("", "—"): parts.append(f"Стоп `{scenarios['long_stop']}`")
            if rr: parts.append(rr)
            if parts: L.append("   " + "  ".join(parts))
        if scenarios["short_condition"]:
            rr = _calc_rr(price, _parse_price(scenarios["short_target"]), _parse_price(scenarios["short_stop"]))
            L.append(f"🔴 Шорт если: {scenarios['short_condition']}")
            parts = []
            if scenarios["short_target"] not in ("", "—"): parts.append(f"Цель `{scenarios['short_target']}`")
            if scenarios["short_stop"]   not in ("", "—"): parts.append(f"Стоп `{scenarios['short_stop']}`")
            if rr: parts.append(rr)
            if parts: L.append("   " + "  ".join(parts))
    L.append("")
    L.append(f"_{summary}_")
    return "\n".join(L)


def _fmt_alert(fa, phase, structure, levels, scenarios, summary) -> str:
    sym   = fa.symbol
    price = fa.current_price or 0.0
    prio  = scenarios["priority"]
    L = []
    L.append(f"`{sym}`  {_fmt_price(price, sym)}")
    L.append(f"Фаза: {phase} · {structure['bias_ru']}")
    L.append("")
    if levels["resistances"]:
        p, src = levels["resistances"][0]
        L.append(f"Сопр.: `{_fmt_price(p, sym)}` _{src}_")
    if levels["supports"]:
        p, src = levels["supports"][0]
        L.append(f"Подд.: `{_fmt_price(p, sym)}` _{src}_")
    L.append("")
    if prio == "long" and scenarios["long_condition"]:
        L.append(f"🟢 {scenarios['long_condition']}")
        if scenarios["long_target"] not in ("", "—"):
            L.append(f"Цель: {scenarios['long_target']}")
    elif prio == "short" and scenarios["short_condition"]:
        L.append(f"🔴 {scenarios['short_condition']}")
        if scenarios["short_target"] not in ("", "—"):
            L.append(f"Цель: {scenarios['short_target']}")
    L.append("")
    L.append(f"_{summary}_")
    return "\n".join(L)


def _fmt_evening(fa, phase, structure, levels, scenarios, summary) -> str:
    sym   = fa.symbol
    price = fa.current_price or 0.0
    prio  = scenarios["priority"]
    L = []
    L.append(f"`{sym}`  {_fmt_price(price, sym)}")
    L.append(f"Закрытие: *{phase}* · *{structure['bias_ru']}*")
    L.append("")
    if structure["bos_desc"]:
        L.append(f"  {structure['bos_desc']}")
    if structure["choch_desc"]:
        L.append(f"  {structure['choch_desc']}")
    if levels["resistances"]:
        p, _ = levels["resistances"][0]
        L.append(f"Ближ. сопр.: `{_fmt_price(p, sym)}`")
    if levels["supports"]:
        p, _ = levels["supports"][0]
        L.append(f"Ближ. подд.: `{_fmt_price(p, sym)}`")
    L.append("")
    if prio == "long":
        L.append(f"Завтра — лонг: {scenarios['long_condition']}")
    elif prio == "short":
        L.append(f"Завтра — шорт: {scenarios['short_condition']}")
    L.append("")
    L.append(f"_{summary}_")
    return "\n".join(L)


# ════════════════════════════════════════════════════════════
#  СБОРКА СООБЩЕНИЙ ДЛЯ bot.py
# ════════════════════════════════════════════════════════════

def build_morning_message(analyses: List[FullAnalysis]) -> List[str]:
    now = datetime.now(TZ).strftime("%d.%m.%Y  %H:%M")
    messages = [f"*MARKET PULSE*  ·  {now}\n{'─' * 28}"]
    for fa in analyses:
        icon = {"crypto": "◆", "forex": "◇", "metal": "◈"}.get(fa.asset_type, "·")
        text = generate_report(fa, "morning")
        messages.append(f"{icon}  {text}\n{'─' * 28}")
    return messages


def build_alert_message(fa: FullAnalysis, approaching_level: float, distance_pct: float) -> str:
    direction = "▲ сопр." if approaching_level > fa.current_price else "▼ подд."
    header = (
        f"⚠️ *АЛЕРТ  ·  {fa.symbol}*\n"
        f"Цена `{_fmt_price(fa.current_price, fa.symbol)}` → `{_fmt_price(approaching_level, fa.symbol)}` {direction}\n"
        f"Расстояние: {distance_pct:.2f}%\n\n"
    )
    return header + generate_report(fa, "alert")


def build_evening_message(analyses: List[FullAnalysis]) -> List[str]:
    now = datetime.now(TZ).strftime("%d.%m.%Y  %H:%M")
    messages = [f"*ИТОГ ДНЯ*  ·  {now}\n{'─' * 28}"]
    for fa in analyses:
        text = generate_report(fa, "evening")
        messages.append(f"{text}\n{'─' * 28}")
    return messages

# ════════════════════════════════════════════════════════════
#  УТИЛИТЫ СИГНАЛЬНОГО БЛОКА (v2)
# ════════════════════════════════════════════════════════════

def _calc_rr(entry: float, target: float, stop: float) -> str:
    try:
        if not entry or not target or not stop:
            return ""
        risk   = abs(entry - stop)
        reward = abs(target - entry)
        if risk == 0:
            return ""
        rr = reward / risk
        reward_pct = reward / entry * 100
        return f"R:R {rr:.1f}  ({reward_pct:.1f}%)"
    except Exception:
        return ""

def _parse_price(s: str) -> float:
    try:
        return float(str(s).replace(",", "").replace(" ", ""))
    except Exception:
        return 0.0
