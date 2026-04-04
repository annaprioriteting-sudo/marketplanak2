# ============================================================
# report_generator.py — УЛУЧШЕННАЯ ВЕРСИЯ
# Понятный нарратив для русскоязычных трейдеров
# Фаза рынка · Причины · Сценарии · Ключевые уровни
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
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
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


def _pct_change(a: float, b: float) -> str:
    """Процентное изменение от a к b."""
    if not a:
        return ""
    pct = (b - a) / a * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


# ════════════════════════════════════════════════════════════
# БЛОК 1 — ФАЗА РЫНКА (с объяснением причин)
# ════════════════════════════════════════════════════════════

def _determine_phase(fa: FullAnalysis) -> Tuple[str, str, str]:
    """
    Возвращает (код_фазы, заголовок, объяснение_почему).
    Объяснение — 1-2 предложения на русском, понятных трейдеру.
    """
    htf = _get_tf(fa, "1W", "1wk", "1D", "1d")
    mtf = _get_tf(fa, "4H", "4h", "1D", "1d")
    ltf = _get_tf(fa, "1H", "1h", "15m")

    if not htf:
        return "нет данных", "Нет данных", "Недостаточно свечей для анализа."

    ind = htf.indicators
    price = fa.current_price or 0.0
    ema20 = _safe(ind.ema20)
    ema50 = _safe(ind.ema50)
    ema200 = _safe(ind.ema200)

    emas_bull = ema20 > ema50 > ema200 and price > ema20
    emas_bear = ema20 < ema50 < ema200 and price < ema20

    htf_bull = htf.trend == "bullish"
    htf_bear = htf.trend == "bearish"
    mtf_bull = mtf and mtf.trend == "bullish"
    mtf_bear = mtf and mtf.trend == "bearish"

    choch_bull = (htf.last_choch and "bull" in htf.last_choch.type) or \
                 (mtf and mtf.last_choch and "bull" in mtf.last_choch.type)
    choch_bear = (htf.last_choch and "bear" in htf.last_choch.type) or \
                 (mtf and mtf.last_choch and "bear" in mtf.last_choch.type)

    rsi_val = _safe(ind.rsi, 50)
    rsi_note = ""
    if rsi_val > 70:
        rsi_note = " RSI в зоне перекупленности — осторожно с лонгами."
    elif rsi_val < 30:
        rsi_note = " RSI в зоне перепроданности — возможен отскок."

    pd_zone = htf.premium_discount if htf else "equilibrium"
    pd_note = ""
    if pd_zone == "premium":
        pd_note = " Цена в зоне premium (выше середины диапазона) — рынок дорогой."
    elif pd_zone == "discount":
        pd_note = " Цена в зоне discount (ниже середины диапазона) — рынок дешёвый."

    if htf_bull and emas_bull and not mtf_bear:
        why = (
            f"Все три EMA выстроены вверх на старшем таймфрейме: цена удерживается выше EMA20. "
            f"Рыночная структура формирует HH/HL — покупатели контролируют движение.{rsi_note}{pd_note}"
        )
        return "trend↑", "📈 Восходящий тренд", why

    if htf_bear and emas_bear and not mtf_bull:
        why = (
            f"EMA20 ниже EMA50 ниже EMA200 — классическая медвежья расстановка. "
            f"Структура формирует LH/LL, продавцы давят цену вниз.{rsi_note}{pd_note}"
        )
        return "trend↓", "📉 Нисходящий тренд", why

    if htf_bull and (choch_bear or mtf_bear):
        why = (
            f"Старший таймфрейм ещё бычий, но средний уже показывает признаки слабости — "
            f"структура ломается сверху вниз (ChoCH).{pd_note} "
            f"Это фаза распределения: умные деньги продают в толпу."
        )
        return "distribution", "⚠️ Распределение (топ?)", why

    if htf_bear and (choch_bull or mtf_bull):
        why = (
            f"Медвежий тренд на HTF, но на средних таймфреймах появляются первые признаки покупок — "
            f"возможно формирование дна. ChoCH вверх пока не подтверждён полностью.{rsi_note}"
        )
        return "accumulation", "🔴 Накопление (дно?)", why

    if htf_bull and mtf_bear:
        why = (
            f"Большой тренд бычий, но сейчас идёт коррекция вниз. "
            f"Это нормальная коррекция в рамках восходящего движения — ждём зоны для покупки.{pd_note}"
        )
        return "correction↑", "↩️ Коррекция в бычьем тренде", why

    if htf_bear and mtf_bull:
        why = (
            f"Большой тренд медвежий, но сейчас идёт отскок вверх. "
            f"Типичный dead cat bounce — ждём уровни для продажи.{pd_note}"
        )
        return "correction↓", "↪️ Отскок в медвежьем тренде", why

    why = (
        f"Нет чёткого направления ни на старшем, ни на среднем таймфрейме. "
        f"Рынок находится в диапазоне — работаем только от чётких границ со стопом.{pd_note}"
    )
    return "range", "↔️ Диапазон (флэт)", why


# ════════════════════════════════════════════════════════════
# БЛОК 2 — СТРУКТУРА И BIAS
# ════════════════════════════════════════════════════════════

def _determine_structure(fa: FullAnalysis) -> Dict:
    bias = fa.htf_bias
    bias_ru = {
        "bullish": "бычья",
        "bearish": "медвежья",
        "ranging": "нейтральная"
    }.get(bias, "нейтральная")

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
    total = len(trends) or 1
    bull_n = trends.count("bullish")
    bear_n = trends.count("bearish")

    if max(bull_n, bear_n) / total >= 0.6:
        quality = "strong"
        quality_ru = "Таймфреймы согласованы — высокая уверенность."
    elif max(bull_n, bear_n) / total >= 0.4:
        quality = "moderate"
        quality_ru = "Частичное согласование таймфреймов — умеренная уверенность."
    else:
        quality = "weak"
        quality_ru = "Таймфреймы противоречат друг другу — уменьши размер позиции."

    return {
        "bias": bias, "bias_ru": bias_ru,
        "bos_desc": bos_desc, "choch_desc": choch_desc,
        "quality": quality, "quality_ru": quality_ru,
        "bull_n": bull_n, "bear_n": bear_n,
    }


# ════════════════════════════════════════════════════════════
# БЛОК 3 — КЛЮЧЕВЫЕ УРОВНИ
# ════════════════════════════════════════════════════════════

def _get_key_levels(fa: FullAnalysis) -> Dict:
    price = fa.current_price or 0.0
    supports: List[Tuple[float, str]] = []
    resistances: List[Tuple[float, str]] = []
    ob_zones: List[Tuple[float, float, str, str]] = []
    fvg_zones: List[Tuple[float, float, str, str]] = []

    for tf_name, tfa in fa.timeframes.items():
        for sr in tfa.sr_levels:
            if sr.price <= 0:
                continue
            src = f"S/R {tf_name}"
            if sr.strength >= 3:
                src += f"(×{sr.strength})"
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

    def dedupe(lvls, tol=0.0015):
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

    supports = dedupe(supports)
    resistances = dedupe(resistances)

    sup_below = sorted([(p, s) for p, s in supports if p < price], key=lambda x: x[0], reverse=True)[:3]
    res_above = sorted([(p, s) for p, s in resistances if p > price], key=lambda x: x[0])[:3]

    return {
        "supports": sup_below,
        "resistances": res_above,
        "ob_zones": ob_zones[:3],
        "fvg_zones": fvg_zones[:3],
    }


# ════════════════════════════════════════════════════════════
# БЛОК 4 — СЦЕНАРИИ (с понятным описанием)
# ════════════════════════════════════════════════════════════

def _build_scenarios(fa: FullAnalysis, structure: Dict, levels: Dict, phase: str, strategy=None) -> Dict:
    sym = fa.symbol
    bias = structure["bias"]
    sups = levels["supports"]
    ress = levels["resistances"]

    s1 = sups[0][0] if sups else 0.0
    s2 = sups[1][0] if len(sups) > 1 else 0.0
    r1 = ress[0][0] if ress else 0.0
    r2 = ress[1][0] if len(ress) > 1 else 0.0

    p = lambda v: _fmt_price(v, sym) if v else "—"

    long_cond = long_tgt = long_sl = ""
    short_cond = short_tgt = short_sl = ""
    long_why = short_why = ""

    prio = "long" if bias == "bullish" else ("short" if bias == "bearish" else "neutral")

    if "trend↑" in phase:
        long_cond = f"откат в OB/FVG · sweep SSL · BOS↑ на 1H · выше {p(s1)}"
        long_tgt = p(r1)
        long_sl = p(s2)
        long_why = "Тренд бычий — покупаем коррекции, не гонимся за ценой."
        short_cond = f"BOS↓ на 4H + sweep BSL · закрытие ниже {p(s1)}"
        short_tgt = p(s2)
        short_sl = p(r1)
        short_why = "Только при явном разломе структуры, иначе — риск против тренда."

    elif "trend↓" in phase:
        short_cond = f"отскок в OB/FVG · sweep BSL · BOS↓ на 1H · ниже {p(r1)}"
        short_tgt = p(s1)
        short_sl = p(r2)
        short_why = "Тренд медвежий — продаём отскоки, не ловим дно."
        long_cond = f"BOS↑ на 4H + sweep SSL · закрытие выше {p(r1)}"
        long_tgt = p(r2)
        long_sl = p(s1)
        long_why = "Только при полном развороте структуры на 4H+."

    elif phase == "accumulation":
        long_cond = f"ChoCH↑ подтверждён · sweep SSL · BOS↑ на 1H · выше {p(r1)}"
        long_tgt = p(r2)
        long_sl = p(s1)
        long_why = "Ищем сигнал разворота — до ChoCH на вход не заходим."
        short_cond = f"продолжение медвежьего тренда · закрытие ниже {p(s1)}"
        short_tgt = p(s2)
        short_sl = p(r1)
        short_why = "Если покупатели не подтвердят разворот — продолжение вниз."
        prio = "long"

    elif phase == "distribution":
        short_cond = f"ChoCH↓ подтверждён · sweep BSL · BOS↓ на 1H · ниже {p(s1)}"
        short_tgt = p(s2)
        short_sl = p(r1)
        short_why = "Ждём подтверждения слома структуры перед шортом."
        long_cond = f"удержание уровня {p(s1)} · отскок выше {p(r1)}"
        long_tgt = p(r1)
        long_sl = p(s2)
        long_why = "Только если быки удержат ключевой уровень поддержки."
        prio = "short"

    elif "correction" in phase:
        if bias == "bullish":
            long_cond = f"остановка коррекции у {p(s1)} · бычья свеча 1H на объёме"
            long_tgt = p(r1)
            long_sl = p(s2)
            long_why = "Коррекция в восходящем тренде — это зона для набора лонгов."
            short_cond = f"пробой {p(s1)} с закрытием ниже — тренд меняется"
            short_tgt = p(s2)
            short_sl = p(r1)
            short_why = "Если коррекция переходит в разворот — выходим и ждём."
        else:
            short_cond = f"окончание отскока у {p(r1)} · медвежья свеча 1H на объёме"
            short_tgt = p(s1)
            short_sl = p(r2)
            short_why = "Отскок в нисходящем тренде — зона для набора шортов."
            long_cond = f"пробой {p(r1)} с закрытием выше — отскок переходит в разворот"
            long_tgt = p(r2)
            long_sl = p(s1)
            long_why = "Если отскок превращается в тренд — пересматриваем bias."

    else:  # range
        long_cond = f"от нижней границы {p(s1)} · подтверждение 1H · sweep SSL"
        long_tgt = p(r1)
        long_sl = p(s2)
        long_why = "Диапазон — торгуем от границ. Середина не даёт преимущества."
        short_cond = f"от верхней границы {p(r1)} · подтверждение 1H · sweep BSL"
        short_tgt = p(s1)
        short_sl = p(r2)
        short_why = "Диапазон — торгуем от границ. Середина не даёт преимущества."
        prio = "neutral"

    pd_zone = ""
    htf = _get_tf(fa, "1D", "1d", "4H", "4h")
    if htf:
        pd_zone = htf.premium_discount

    return {
        "priority": prio, "pd_zone": pd_zone,
        "long_condition": long_cond, "long_target": long_tgt, "long_stop": long_sl,
        "long_why": long_why,
        "short_condition": short_cond, "short_target": short_tgt, "short_stop": short_sl,
        "short_why": short_why,
    }


# ════════════════════════════════════════════════════════════
# БЛОК 5 — ИНДИКАТОРЫ (понятно, без технического мусора)
# ════════════════════════════════════════════════════════════

def _summarize_indicators(fa: FullAnalysis) -> str:
    mtf = _get_tf(fa, "4H", "4h", "1D", "1d")
    if not mtf:
        return ""

    ind = mtf.indicators
    rsi = _safe(ind.rsi, 50)
    vol = _safe(ind.volume_ratio, 1.0)
    macd = ind.macd_signal or ""
    bb = ind.bb_position or ""

    parts = []

    # RSI
    if rsi > 70:
        parts.append(f"RSI {rsi:.0f} — перекуплен")
    elif rsi < 30:
        parts.append(f"RSI {rsi:.0f} — перепродан")
    else:
        parts.append(f"RSI {rsi:.0f}")

    # Объём
    if vol >= 1.5:
        parts.append(f"объём ×{vol:.1f}↑ (подтверждение)")
    elif vol <= 0.5:
        parts.append(f"объём ×{vol:.1f}↓ (слабое движение)")

    # MACD
    macd_map = {
        "bullish_cross": "MACD — бычий кросс",
        "bearish_cross": "MACD — медвежий кросс",
        "bullish": "MACD>0",
        "bearish": "MACD<0"
    }
    if macd in macd_map:
        parts.append(macd_map[macd])

    # BB
    bb_map = {
        "above_upper": "BB — цена выше верхней полосы (перегрев)",
        "below_lower": "BB — цена ниже нижней полосы (перепродан)",
        "upper": "BB у верхней",
        "lower": "BB у нижней",
    }
    if bb in bb_map:
        parts.append(bb_map[bb])

    return " · ".join(parts)


def _summarize_patterns(fa: FullAnalysis) -> List[str]:
    lines, seen = [], set()
    for tf_name in ["1D", "1d", "4H", "4h", "1H", "1h", "15m"]:
        if tf_name not in fa.timeframes:
            continue
        for pat in fa.timeframes[tf_name].patterns:
            if pat.name in seen:
                continue
            seen.add(pat.name)
            icon = "📈" if pat.direction == "bullish" else ("📉" if pat.direction == "bearish" else "↔️")
            lines.append(
                f"{icon} {pat.name} · {tf_name} · цель {_fmt_price(pat.target, fa.symbol)} · {int(pat.confidence * 100)}%"
            )
    return lines[:3]


# ════════════════════════════════════════════════════════════
# БЛОК 6 — ACTIONABLE SUMMARY
# ════════════════════════════════════════════════════════════

def _build_summary(phase: str, structure: Dict, scenarios: Dict) -> str:
    bias = structure["bias"]
    quality = structure["quality"]
    prio = scenarios["priority"]
    pd_zone = scenarios["pd_zone"]

    core = {
        "trend↑": "Тренд бычий. Покупаем откаты в OB/FVG, не гонимся за ценой.",
        "trend↓": "Тренд медвежий. Продаём отскоки в OB/FVG, не ловим дно.",
        "accumulation": "Возможное дно. Лонг только при подтверждённом ChoCH — не раньше.",
        "distribution": "Возможный топ. Шорт только при подтверждённом ChoCH вниз.",
        "correction↑": "Коррекция в бычьем тренде. Ждём уровни для покупки — против тренда не торгуем.",
        "correction↓": "Отскок в медвежьем тренде. Ждём уровни для продажи — против тренда не торгуем.",
        "range": "Рынок в диапазоне. Торговля только от границ, середина не даёт преимущества.",
    }.get(phase, "Нет чёткого сигнала. Лучшая позиция — вне рынка.")

    extras = []
    if quality == "weak":
        extras.append("Структура противоречивая — уменьши размер позиции вдвое.")
    if pd_zone == "premium" and prio == "long":
        extras.append("Цена в premium зоне — лонг менее приоритетен, жди глубже.")
    if pd_zone == "discount" and prio == "short":
        extras.append("Цена в discount зоне — шорт менее приоритетен, жди выше.")

    return " ".join([core] + extras)


# ════════════════════════════════════════════════════════════
# ГЕНЕРАТОР — АНАЛИЗ МОНЕТЫ (по нажатию "Анализ монеты")
# Полный понятный отчёт для конкретного инструмента
# ════════════════════════════════════════════════════════════

def generate_coin_report(fa: FullAnalysis) -> str:
    """
    Полный отчёт по конкретной монете.
    Стиль: трейдер объясняет другому трейдеру что происходит.
    """
    if not fa or not fa.timeframes:
        return f"⚠️ {fa.symbol if fa else '—'}: недостаточно данных для анализа."

    phase, phase_title, phase_why = _determine_phase(fa)
    structure = _determine_structure(fa)
    levels = _get_key_levels(fa)
    scenarios = _build_scenarios(fa, structure, levels, phase)
    patterns = _summarize_patterns(fa)
    ind_str = _summarize_indicators(fa)
    summary = _build_summary(phase, structure, scenarios)

    sym = fa.symbol
    price = fa.current_price or 0.0
    prio = scenarios["priority"]

    picon = {"long": "🟢", "short": "🔴", "neutral": "⚪"}.get(prio, "⚪")
    pd_emoji = {
        "premium": "🔴 premium",
        "discount": "🟢 discount",
        "equilibrium": "⚪ equilibrium"
    }
    pd_ru = pd_emoji.get(scenarios["pd_zone"], "")

    L = []

    # ── Шапка
    L.append(f"◆ *{sym}*  `{_fmt_price(price, sym)}`")
    L.append(f"{phase_title}  ·  {pd_ru}  ·  {structure['bias_ru']}")
    L.append("")

    # ── Что происходит (нарратив)
    L.append("*Что происходит:*")
    L.append(f"_{phase_why}_")
    L.append("")

    # ── Структурные сигналы
    struct_parts = []
    if structure["bos_desc"]:
        struct_parts.append(f"  · {structure['bos_desc']}")
    if structure["choch_desc"]:
        struct_parts.append(f"  · {structure['choch_desc']}")
    if struct_parts:
        L.append("*Что формируется:*")
        L.extend(struct_parts)
        L.append("")

    # ── OB / FVG зоны
    zones = []
    for lo, hi, ob_t, tf in levels["ob_zones"][:2]:
        ic = "🟢" if ob_t == "bullish" else "🔴"
        label = "бычий" if ob_t == "bullish" else "медвежий"
        zones.append(f"  · {ic} OB {tf} {_fmt_price(lo, sym)}–{_fmt_price(hi, sym)} — {label}")
    for lo, hi, fvg_t, tf in levels["fvg_zones"][:2]:
        ic = "🟢" if fvg_t == "bullish" else "🔴"
        label = "незаполненный имбаланс"
        zones.append(f"  · {ic} FVG {tf} {_fmt_price(lo, sym)}–{_fmt_price(hi, sym)} — {label}")
    if zones:
        L.append("*Зона интереса:*")
        L.extend(zones)
        L.append("")

    # ── Ключевые уровни (понятно)
    L.append("*Ключевые уровни:*")
    for p_res, src in reversed(levels["resistances"]):
        L.append(f"  ▲ `{_fmt_price(p_res, sym)}`  Сопротивление _{src}_")
    L.append(f"  → `{_fmt_price(price, sym)}`  ← сейчас")
    for p_sup, src in levels["supports"]:
        L.append(f"  ▼ `{_fmt_price(p_sup, sym)}`  Поддержка _{src}_")
    L.append("")

    # ── Индикаторы
    if ind_str:
        L.append(f"_{ind_str}_")
        L.append("")

    # ── Паттерны
    if patterns:
        L.append("*Паттерны:*")
        for pt in patterns:
            L.append(f"  · {pt}")
        L.append("")

    # ── Сценарии (главное)
    L.append("*Сценарии (ждём подтверждения):*")

    if scenarios["long_condition"]:
        tgt_str = scenarios["long_target"]
        stop_str = scenarios["long_stop"]
        rr = _calc_rr(price, _parse_price(tgt_str), _parse_price(stop_str))
        L.append(f"🟢 Лонг: {scenarios['long_condition']}")
        if tgt_str and tgt_str != "—":
            L.append(f"   Цель {tgt_str} · Стоп {stop_str}" + (f" · {rr}" if rr else ""))
        if scenarios["long_why"]:
            L.append(f"   _{scenarios['long_why']}_")

    if scenarios["short_condition"]:
        tgt_str = scenarios["short_target"]
        stop_str = scenarios["short_stop"]
        rr = _calc_rr(price, _parse_price(tgt_str), _parse_price(stop_str))
        L.append(f"🔴 Шорт: {scenarios['short_condition']}")
        if tgt_str and tgt_str != "—":
            L.append(f"   Цель {tgt_str} · Стоп {stop_str}" + (f" · {rr}" if rr else ""))
        if scenarios["short_why"]:
            L.append(f"   _{scenarios['short_why']}_")

    L.append("")

    # ── Invalidation (что отменяет сценарий)
    inv_levels = []
    if levels["supports"]:
        inv_levels.append(f"`{_fmt_price(levels['supports'][0][0], sym)}`")
    if levels["resistances"]:
        inv_levels.append(f"`{_fmt_price(levels['resistances'][0][0], sym)}`")
    if inv_levels:
        L.append(f"*Invalidation:*")
        L.append(f"Выход за {' или '.join(inv_levels)} даст направление.")
        L.append("")

    # ── Вывод
    L.append(f"*Вывод:*")
    L.append(f"_{summary}_")

    # ── Согласованность TF
    if structure["quality_ru"]:
        L.append(f"_{structure['quality_ru']}_")

    return "\n".join(L)


# ════════════════════════════════════════════════════════════
# ОБЩИЙ РЫНОЧНЫЙ ОТЧЁТ (по нажатию "Отчёт")
# BTC + ETH + Золото + валютные пары — рыночный контекст
# ════════════════════════════════════════════════════════════

def generate_market_pulse(analyses: List[FullAnalysis], news: List[str] = None) -> List[str]:
    """
    Общий Market Pulse — обзор рынка целиком.
    Один общий нарратив + каждый инструмент кратко.
    """
    now = datetime.now(TZ).strftime("%d.%m.%Y  %H:%M")
    messages = []

    # ── Определяем общее настроение
    bull_count = 0
    bear_count = 0
    for fa in analyses:
        if fa.htf_bias == "bullish":
            bull_count += 1
        elif fa.htf_bias == "bearish":
            bear_count += 1

    total = len(analyses) or 1
    if bull_count / total >= 0.6:
        sentiment = "🟢 Risk-on"
        sentiment_note = "Большинство инструментов показывают бычью структуру. Приоритет — покупки от уровней."
    elif bear_count / total >= 0.6:
        sentiment = "🔴 Risk-off"
        sentiment_note = "Большинство инструментов медвежьи. Приоритет — продажи от уровней или cash."
    else:
        sentiment = "⚪ Нейтрально"
        sentiment_note = "Рынок смешанный. Торгуем избирательно, без агрессии."

    header = [
        f"*MARKET PULSE  —  Торговый план*",
        f"",
        f"Настроение: {sentiment}",
        f"Бычьих: {bull_count}  ·  Медвежьих: {bear_count}  ·  Всего: {total}",
        f"",
        f"_{sentiment_note}_",
    ]

    # ── Новости
    if news:
        header.append("")
        header.append("*📰 Новости:*")
        for n in news[:3]:
            header.append(f"— {n}")

    header.append(f"\nРынок: {sentiment}")
    header.append("─" * 28)
    header.append(now)

    messages.append("\n".join(header))

    # ── Краткий анализ каждого инструмента
    for fa in analyses:
        if not fa or not fa.timeframes:
            continue

        phase, phase_title, phase_why = _determine_phase(fa)
        structure = _determine_structure(fa)
        levels = _get_key_levels(fa)
        scenarios = _build_scenarios(fa, structure, levels, phase)
        ind_str = _summarize_indicators(fa)
        summary = _build_summary(phase, structure, scenarios)

        sym = fa.symbol
        price = fa.current_price or 0.0
        prio = scenarios["priority"]
        picon = {"long": "🟢", "short": "🔴", "neutral": "⚪"}.get(prio, "⚪")
        pd_zone = scenarios.get("pd_zone", "")
        pd_emoji = {"premium": "▼ premium", "discount": "▼ discount", "equilibrium": ""}.get(pd_zone, "")

        asset_icon = {"crypto": "◆", "forex": "◇", "metal": "◈"}.get(fa.asset_type, "·")

        L = []
        L.append(f"{asset_icon}  *{sym}*  `{_fmt_price(price, sym)}`")

        # Фаза + зона
        pd_line = f"  ·  {pd_emoji}" if pd_emoji else ""
        trend_line = f"  ·  Тренд: {structure['bias_ru']}"
        L.append(f"{phase_title}{pd_line}{trend_line}")

        # Главная причина (1 строка)
        short_why = phase_why.split(".")[0] + "." if phase_why else ""
        L.append(f"_{short_why}_")
        L.append("")

        # Ближайшие уровни
        if levels["resistances"]:
            p_r, _ = levels["resistances"][0]
            L.append(f"  ▲ `{_fmt_price(p_r, sym)}`")
        L.append(f"  → `{_fmt_price(price, sym)}`")
        if levels["supports"]:
            p_s, _ = levels["supports"][0]
            L.append(f"  ▼ `{_fmt_price(p_s, sym)}`")

        # OB зона (одна)
        if levels["ob_zones"]:
            lo, hi, ob_t, tf = levels["ob_zones"][0]
            ic = "🟢" if ob_t == "bullish" else "🔴"
            L.append(f"Зона: {ic} OB {tf} {_fmt_price(lo, sym)}–{_fmt_price(hi, sym)}")

        # Индикаторы (кратко)
        if ind_str:
            L.append(f"_{ind_str}_")
        L.append("")

        # Итог
        L.append(f"{picon} *{'ЛОНГ' if prio == 'long' else 'ШОРТ' if prio == 'short' else 'ЖДЁМ'}*")
        L.append(f"_{summary}_")

        messages.append("\n".join(L))

    return messages


# ════════════════════════════════════════════════════════════
# ОБРАТНАЯ СОВМЕСТИМОСТЬ — generate_report (для bot.py)
# ════════════════════════════════════════════════════════════

def generate_report(fa: FullAnalysis, report_type: str = "morning") -> str:
    """Обёртка для обратной совместимости с bot.py."""
    if report_type in ("morning", "coin"):
        return generate_coin_report(fa)
    elif report_type == "alert":
        return _fmt_alert_short(fa)
    elif report_type == "evening":
        return _fmt_evening_short(fa)
    return generate_coin_report(fa)


def _fmt_alert_short(fa: FullAnalysis) -> str:
    phase, phase_title, phase_why = _determine_phase(fa)
    structure = _determine_structure(fa)
    levels = _get_key_levels(fa)
    scenarios = _build_scenarios(fa, structure, levels, phase)

    sym = fa.symbol
    price = fa.current_price or 0.0
    prio = scenarios["priority"]

    L = [f"`{sym}` {_fmt_price(price, sym)}"]
    L.append(f"{phase_title} · {structure['bias_ru']}")
    L.append("")

    if levels["resistances"]:
        p, src = levels["resistances"][0]
        L.append(f"Сопр.: `{_fmt_price(p, sym)}`")
    if levels["supports"]:
        p, src = levels["supports"][0]
        L.append(f"Подд.: `{_fmt_price(p, sym)}`")
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
    L.append(f"_{_build_summary(phase, structure, scenarios)}_")
    return "\n".join(L)


def _fmt_evening_short(fa: FullAnalysis) -> str:
    phase, phase_title, phase_why = _determine_phase(fa)
    structure = _determine_structure(fa)
    levels = _get_key_levels(fa)
    scenarios = _build_scenarios(fa, structure, levels, phase)

    sym = fa.symbol
    price = fa.current_price or 0.0
    prio = scenarios["priority"]

    L = [f"`{sym}` {_fmt_price(price, sym)}"]
    L.append(f"Закрытие: *{phase_title}* · *{structure['bias_ru']}*")
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
    L.append(f"_{_build_summary(phase, structure, scenarios)}_")
    return "\n".join(L)


# ════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ════════════════════════════════════════════════════════════

def build_morning_message(analyses: List[FullAnalysis], news: List[str] = None) -> List[str]:
    return generate_market_pulse(analyses, news)


def build_alert_message(fa: FullAnalysis, approaching_level: float, distance_pct: float) -> str:
    direction = "▲ сопр." if approaching_level > fa.current_price else "▼ подд."
    header = (
        f"⚠️ *АЛЕРТ · {fa.symbol}*\n"
        f"Цена `{_fmt_price(fa.current_price, fa.symbol)}` → `{_fmt_price(approaching_level, fa.symbol)}` {direction}\n"
        f"Расстояние: {distance_pct:.2f}%\n\n"
    )
    return header + _fmt_alert_short(fa)


def build_evening_message(analyses: List[FullAnalysis]) -> List[str]:
    now = datetime.now(TZ).strftime("%d.%m.%Y  %H:%M")
    messages = [f"*ИТОГ ДНЯ*  ·  {now}\n{'─' * 28}"]
    for fa in analyses:
        text = _fmt_evening_short(fa)
        messages.append(f"{text}\n{'─' * 28}")
    return messages


def _calc_rr(entry: float, target: float, stop: float) -> str:
    try:
        if not entry or not target or not stop:
            return ""
        risk = abs(entry - stop)
        reward = abs(target - entry)
        if risk == 0:
            return ""
        rr = reward / risk
        reward_pct = reward / entry * 100
        return f"R:R {rr:.1f} ({reward_pct:.1f}%)"
    except Exception:
        return ""


def _parse_price(s: str) -> float:
    try:
        return float(str(s).replace(",", "").replace(" ", ""))
    except Exception:
        return 0.0
