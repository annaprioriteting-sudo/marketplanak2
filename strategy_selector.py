# ============================================================
#  strategy_selector.py — Выбор торговой стратегии
#
#  5 стратегий, каждая под своё рыночное состояние.
#  Выбор по: тип актива + фаза + структура + индикаторы.
#
#  НЕ изменяет analyzer.py. Работает только с FullAnalysis.
# ============================================================

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple
import logging

from analyzer import FullAnalysis, TimeframeAnalysis

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  СТРАТЕГИИ
# ════════════════════════════════════════════════════════════

STRATEGY_SMC        = "SMC Institutional"
STRATEGY_WYCKOFF    = "Wyckoff"
STRATEGY_TREND      = "Trend Following"
STRATEGY_RANGE      = "Range / Mean Revert"
STRATEGY_REVERSAL   = "Reversal Watch"


@dataclass
class StrategyResult:
    """Результат выбора стратегии."""
    name: str                    # название стратегии
    confidence: float            # уверенность 0.0–1.0
    reasoning: List[str]         # почему выбрана эта стратегия
    focus: List[str]             # на что смотреть в анализе
    signal_allowed: bool         # True = сигнал возможен, False = только анализ
    conflict: bool               # True = стратегии конфликтовали
    conflict_detail: str         # описание конфликта если есть


# ════════════════════════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ════════════════════════════════════════════════════════════

def _get_tf(fa: FullAnalysis, *candidates: str) -> Optional[TimeframeAnalysis]:
    for tf in candidates:
        if tf in fa.timeframes:
            return fa.timeframes[tf]
    return None


def _safe(val, default=0.0):
    return val if val is not None else default


def _trend_alignment(fa: FullAnalysis) -> Tuple[float, str]:
    """
    Возвращает (процент согласованности, направление).
    Смотрит на все таймфреймы кроме 15m.
    """
    relevant = {k: v for k, v in fa.timeframes.items() if k != "15m"}
    if not relevant:
        return 0.0, "ranging"

    trends = [t.trend for t in relevant.values()]
    total  = len(trends)
    bull_n = trends.count("bullish")
    bear_n = trends.count("bearish")

    if bull_n >= bear_n:
        return bull_n / total, "bullish"
    else:
        return bear_n / total, "bearish"


def _has_clean_structure(fa: FullAnalysis) -> bool:
    """True если есть BOS или ChoCH хотя бы на двух таймфреймах."""
    count = 0
    for tfa in fa.timeframes.values():
        if tfa.last_bos or tfa.last_choch:
            count += 1
    return count >= 2


def _has_ob_fvg(fa: FullAnalysis) -> bool:
    """True если есть нетронутые OB или FVG на HTF/MTF."""
    for tf in ["1W", "1wk", "1D", "1d", "4H", "4h"]:
        tfa = fa.timeframes.get(tf)
        if not tfa:
            continue
        if any(not ob.mitigated for ob in tfa.order_blocks):
            return True
        if any(not fvg.filled for fvg in tfa.fvgs):
            return True
    return False


def _has_liquidity_sweep(fa: FullAnalysis) -> bool:
    """True если была swept ликвидность на MTF/LTF (институциональный след)."""
    for tf in ["4H", "4h", "1H", "1h", "15m"]:
        tfa = fa.timeframes.get(tf)
        if not tfa:
            continue
        if any(liq.swept for liq in tfa.liquidity):
            return True
    return False


def _wyckoff_phase(fa: FullAnalysis) -> Optional[str]:
    """
    Определяет фазу Wyckoff по HTF структуре.
    Возвращает: "accumulation" | "distribution" | None
    """
    htf = _get_tf(fa, "1W", "1wk", "1D", "1d")
    mtf = _get_tf(fa, "4H", "4h")
    if not htf or not mtf:
        return None

    htf_bull = htf.trend == "bullish"
    htf_bear = htf.trend == "bearish"
    mtf_bull = mtf.trend == "bullish"
    mtf_bear = mtf.trend == "bearish"

    # Накопление: HTF медвежий + MTF начинает бычий + объём растёт
    vol_rising = _safe(mtf.indicators.volume_ratio, 1.0) > 1.2

    if htf_bear and mtf_bull and vol_rising:
        return "accumulation"

    # Распределение: HTF бычий + MTF начинает медвежий + объём
    if htf_bull and mtf_bear and vol_rising:
        return "distribution"

    # Боковик с накоплением: оба ranging + объём выше среднего
    htf_range = htf.trend == "ranging"
    mtf_range = mtf.trend == "ranging"
    if (htf_range or mtf_range) and vol_rising:
        # Смотрим ChoCH для направления
        if htf.last_choch and "bull" in htf.last_choch.type:
            return "accumulation"
        if htf.last_choch and "bear" in htf.last_choch.type:
            return "distribution"

    return None


def _is_range_market(fa: FullAnalysis) -> Tuple[bool, float]:
    """
    True если рынок в диапазоне.
    Возвращает (is_range, range_quality 0-1).
    """
    htf = _get_tf(fa, "1D", "1d", "4H", "4h")
    mtf = _get_tf(fa, "4H", "4h", "1H", "1h")
    if not htf or not mtf:
        return False, 0.0

    htf_ranging = htf.trend == "ranging"
    mtf_ranging = mtf.trend == "ranging"

    # Нет BOS на обоих
    no_bos = not htf.last_bos and not mtf.last_bos

    # BB в середине (цена не у экстремумов)
    bb_mid = htf.indicators.bb_position in ("middle", "upper", "lower")

    score = sum([htf_ranging, mtf_ranging, no_bos, bb_mid])
    return score >= 2, score / 4.0


def _ema_aligned(fa: FullAnalysis) -> Tuple[bool, str]:
    """
    True если EMA20 > EMA50 > EMA200 (бычий) или наоборот (медвежий).
    Возвращает (aligned, direction).
    """
    htf = _get_tf(fa, "1D", "1d", "1W", "1wk")
    if not htf:
        return False, ""

    ind    = htf.indicators
    ema20  = _safe(ind.ema20)
    ema50  = _safe(ind.ema50)
    ema200 = _safe(ind.ema200)

    if ema20 > ema50 > ema200:
        return True, "bullish"
    if ema20 < ema50 < ema200:
        return True, "bearish"
    return False, ""


def _volume_confirms(fa: FullAnalysis) -> bool:
    """True если объём на MTF подтверждает движение (ratio > 1.3)."""
    mtf = _get_tf(fa, "4H", "4h", "1D", "1d")
    if not mtf:
        return False
    return _safe(mtf.indicators.volume_ratio, 1.0) >= 1.3


# ════════════════════════════════════════════════════════════
#  СКОРИНГ КАЖДОЙ СТРАТЕГИИ
#  Каждая стратегия получает балл 0.0–1.0
# ════════════════════════════════════════════════════════════

def _score_smc(fa: FullAnalysis) -> Tuple[float, List[str]]:
    score   = 0.0
    reasons = []

    alignment, direction = _trend_alignment(fa)
    if alignment >= 0.6:
        score += 0.25
        reasons.append(f"Структура согласована {int(alignment*100)}%")

    if _has_clean_structure(fa):
        score += 0.25
        reasons.append("Чёткие BOS/ChoCH на 2+ таймфреймах")

    if _has_ob_fvg(fa):
        score += 0.25
        reasons.append("Нетронутые OB/FVG на HTF/MTF")

    if _has_liquidity_sweep(fa):
        score += 0.15
        reasons.append("Sweep ликвидности — институциональный след")

    # Крипто — SMC работает лучше всего
    if fa.asset_type == "crypto":
        score += 0.10
        reasons.append("Крипто — SMC primary")

    return min(score, 1.0), reasons


def _score_wyckoff(fa: FullAnalysis) -> Tuple[float, List[str]]:
    score   = 0.0
    reasons = []

    phase = _wyckoff_phase(fa)
    if phase == "accumulation":
        score += 0.50
        reasons.append("Фаза накопления: HTF медвежий + MTF бычий + объём↑")
    elif phase == "distribution":
        score += 0.50
        reasons.append("Фаза распределения: HTF бычий + MTF медвежий + объём↑")

    # Боковик — Wyckoff хорошо работает
    is_range, rq = _is_range_market(fa)
    if is_range:
        score += 0.20 * rq
        reasons.append(f"Диапазон — Wyckoff применим")

    # Металлы — институциональный актив, Wyckoff актуален
    if fa.asset_type == "metal":
        score += 0.15
        reasons.append("Металл — Wyckoff primary")

    if _volume_confirms(fa):
        score += 0.15
        reasons.append("Объём подтверждает фазу")

    return min(score, 1.0), reasons


def _score_trend(fa: FullAnalysis) -> Tuple[float, List[str]]:
    score   = 0.0
    reasons = []

    alignment, direction = _trend_alignment(fa)
    if alignment >= 0.7:
        score += 0.35
        reasons.append(f"Сильный тренд: {int(alignment*100)}% таймфреймов согласованы")
    elif alignment >= 0.5:
        score += 0.20
        reasons.append(f"Умеренный тренд: {int(alignment*100)}%")

    ema_ok, ema_dir = _ema_aligned(fa)
    if ema_ok:
        score += 0.30
        reasons.append(f"EMA выстроены ({ema_dir}): 20 > 50 > 200")

    # MACD согласован с трендом
    htf = _get_tf(fa, "1D", "1d", "4H", "4h")
    if htf:
        macd = htf.indicators.macd_signal or ""
        if direction == "bullish" and macd in ("bullish", "bullish_cross"):
            score += 0.20
            reasons.append("MACD бычий — тренд подтверждён")
        elif direction == "bearish" and macd in ("bearish", "bearish_cross"):
            score += 0.20
            reasons.append("MACD медвежий — тренд подтверждён")

    # Форекс — Trend Following хорошо работает
    if fa.asset_type == "forex":
        score += 0.15
        reasons.append("Форекс — Trend Following primary")

    return min(score, 1.0), reasons


def _score_range(fa: FullAnalysis) -> Tuple[float, List[str]]:
    score   = 0.0
    reasons = []

    is_range, quality = _is_range_market(fa)
    if is_range:
        score += 0.50 * quality + 0.20
        reasons.append(f"Диапазон подтверждён (качество {int(quality*100)}%)")

    # Нет явного тренда
    alignment, _ = _trend_alignment(fa)
    if alignment < 0.45:
        score += 0.20
        reasons.append("Нет чёткого тренда — диапазонная торговля предпочтительна")

    # RSI в середине (40–60) — подтверждение флэта
    mtf = _get_tf(fa, "4H", "4h")
    if mtf:
        rsi = _safe(mtf.indicators.rsi, 50)
        if 35 <= rsi <= 65:
            score += 0.10
            reasons.append(f"RSI {rsi:.0f} — нейтральная зона")

    return min(score, 1.0), reasons


# ════════════════════════════════════════════════════════════
#  ГЛАВНАЯ ФУНКЦИЯ ВЫБОРА СТРАТЕГИИ
# ════════════════════════════════════════════════════════════

def select_strategy(fa: FullAnalysis, phase: str) -> StrategyResult:
    """
    Выбирает оптимальную стратегию для актива.

    Логика:
    1. Считаем балл каждой стратегии
    2. Если победитель явный (разрыв > 0.2) — выбираем его
    3. Если два лидера с разрывом ≤ 0.2 — проверяем конфликт
    4. Конфликт LONG vs SHORT → Reversal Watch (анализ без сигнала)
    5. Конфликт совместимых стратегий → выбираем сильнейшую, отмечаем
    """

    scores = {
        STRATEGY_SMC:     _score_smc(fa),
        STRATEGY_WYCKOFF: _score_wyckoff(fa),
        STRATEGY_TREND:   _score_trend(fa),
        STRATEGY_RANGE:   _score_range(fa),
    }

    # Сортируем по баллу
    ranked = sorted(scores.items(), key=lambda x: x[1][0], reverse=True)
    top_name, (top_score, top_reasons)   = ranked[0]
    sec_name, (sec_score, sec_reasons)   = ranked[1]

    # ── Проверка конфликта ─────────────────────────────────────
    conflict        = False
    conflict_detail = ""
    signal_allowed  = True

    gap = top_score - sec_score

    if gap <= 0.20 and top_score >= 0.35:
        # Две стратегии близко — проверяем направления
        top_dir = _strategy_direction(fa, top_name)
        sec_dir = _strategy_direction(fa, sec_name)

        if top_dir and sec_dir and top_dir != sec_dir and \
           top_dir != "neutral" and sec_dir != "neutral":
            # Реальный конфликт: одна говорит LONG, другая SHORT
            conflict        = True
            signal_allowed  = False
            conflict_detail = (
                f"{top_name} ({top_dir.upper()}) vs {sec_name} ({sec_dir.upper()}) — "
                f"противоположные направления, сигнал не выдаётся"
            )
            return StrategyResult(
                name=STRATEGY_REVERSAL,
                confidence=round(top_score, 2),
                reasoning=[conflict_detail] + top_reasons[:2],
                focus=_reversal_focus(),
                signal_allowed=False,
                conflict=True,
                conflict_detail=conflict_detail,
            )
        else:
            # Стратегии не конфликтуют по направлению — берём сильнейшую, отмечаем
            conflict        = True
            conflict_detail = f"{top_name} и {sec_name} оба релевантны — приоритет {top_name}"

    # ── Низкий общий балл — Reversal Watch ─────────────────────
    if top_score < 0.30:
        return StrategyResult(
            name=STRATEGY_REVERSAL,
            confidence=round(top_score, 2),
            reasoning=["Ни одна стратегия не набрала достаточный балл",
                       "Рынок неопределённый — только наблюдение"],
            focus=_reversal_focus(),
            signal_allowed=False,
            conflict=False,
            conflict_detail="",
        )

    # ── Победитель ─────────────────────────────────────────────
    return StrategyResult(
        name=top_name,
        confidence=round(top_score, 2),
        reasoning=top_reasons,
        focus=_strategy_focus(top_name, fa),
        signal_allowed=signal_allowed,
        conflict=conflict,
        conflict_detail=conflict_detail,
    )


# ════════════════════════════════════════════════════════════
#  НАПРАВЛЕНИЕ СТРАТЕГИИ (для проверки конфликта)
# ════════════════════════════════════════════════════════════

def _strategy_direction(fa: FullAnalysis, strategy: str) -> Optional[str]:
    """Возвращает implied direction для стратегии: 'bullish' | 'bearish' | 'neutral'."""
    if strategy == STRATEGY_SMC:
        alignment, direction = _trend_alignment(fa)
        return direction if alignment >= 0.5 else "neutral"

    if strategy == STRATEGY_WYCKOFF:
        phase = _wyckoff_phase(fa)
        if phase == "accumulation":
            return "bullish"
        if phase == "distribution":
            return "bearish"
        return "neutral"

    if strategy == STRATEGY_TREND:
        _, direction = _trend_alignment(fa)
        return direction

    if strategy == STRATEGY_RANGE:
        return "neutral"

    return "neutral"


# ════════════════════════════════════════════════════════════
#  ФОКУС АНАЛИЗА ДЛЯ КАЖДОЙ СТРАТЕГИИ
#  Что именно смотреть и на что обращать внимание
# ════════════════════════════════════════════════════════════

def _strategy_focus(strategy: str, fa: FullAnalysis) -> List[str]:
    wyckoff_phase = _wyckoff_phase(fa) or "неопределена"
    _, ema_dir    = _ema_aligned(fa)

    focus_map = {
        STRATEGY_SMC: [
            "HTF BOS/ChoCH — определяет направление",
            "OB на HTF/MTF — зоны институционального входа",
            "FVG — имбалансы для заполнения",
            "Sweep ликвидности — сигнал перед разворотом",
            "Confluences: OB + FVG + уровень в одной зоне",
        ],
        STRATEGY_WYCKOFF: [
            f"Фаза: {wyckoff_phase}",
            "Объём при движении (импульс) vs. объём при коррекции (слабость)",
            "Spring / Upthrust — ложный пробой перед разворотом",
            "Signs of Strength (SOS) / Signs of Weakness (SOW)",
            "Last Point of Support (LPS) — финальная точка входа",
        ],
        STRATEGY_TREND: [
            f"EMA выстроены ({ema_dir}) — тренд определён",
            "Откаты к EMA20/50 — зоны для входа в тренд",
            "MACD для подтверждения импульса",
            "Не торговать против HTF тренда",
            "Стоп за ближайший HL (лонг) или LH (шорт)",
        ],
        STRATEGY_RANGE: [
            "Границы диапазона — только от них",
            "RSI: перепродан у поддержки, перекуплен у сопротивления",
            "Bollinger Bands — экстремумы как зоны входа",
            "Объём при пробое — фильтр ложных пробоев",
            "Не торговать от середины диапазона",
        ],
    }
    return focus_map.get(strategy, ["Наблюдение и ожидание"])


def _reversal_focus() -> List[str]:
    return [
        "Сигналов нет — только анализ",
        "Следим за ChoCH на HTF для определения нового направления",
        "BOS в любую сторону даст первый сигнал стратегии",
        "Уменьши позицию или выйди из рынка",
        "Ждём разрешения неопределённости",
    ]


# ════════════════════════════════════════════════════════════
#  ФОРМАТИРОВАНИЕ ДЛЯ REPORT GENERATOR
# ════════════════════════════════════════════════════════════

def format_strategy_block(result: StrategyResult) -> str:
    """
    Возвращает текстовый блок для вставки в отчёт.
    Используется в report_generator._fmt_morning()
    """
    conf_pct = int(result.confidence * 100)

    # Иконки стратегий
    icons = {
        STRATEGY_SMC:      "◈",
        STRATEGY_WYCKOFF:  "◉",
        STRATEGY_TREND:    "◆",
        STRATEGY_RANGE:    "◇",
        STRATEGY_REVERSAL: "○",
    }
    icon = icons.get(result.name, "·")

    lines = []
    lines.append(f"*Стратегия:* {icon} {result.name}  _{conf_pct}% уверенность_")

    # Причины выбора (кратко, максимум 2)
    if result.reasoning:
        for r in result.reasoning[:2]:
            lines.append(f"  ↳ _{r}_")

    # Конфликт
    if result.conflict and result.conflict_detail:
        lines.append(f"⚠️ _{result.conflict_detail}_")

    # Фокус анализа (что смотреть)
    lines.append("")
    lines.append("*Фокус анализа:*")
    for f in result.focus[:4]:
        lines.append(f"  · {f}")

    # Нет сигнала
    if not result.signal_allowed:
        lines.append("")
        lines.append("🚫 _Сигнал не выдаётся — только анализ_")

    return "\n".join(lines)
