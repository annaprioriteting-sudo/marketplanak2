# ============================================================
#  signal_engine.py — Движок сигналов
#  Выбирает 1-2 лучших торговых идеи из всех активных анализов
#  Без внешних API. Чистая логика на правилах.
# ============================================================

from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytz

from analyzer import FullAnalysis
from report_generator import (
    _determine_phase, _determine_structure, _get_key_levels,
    _build_scenarios, _fmt_price, _parse_price, _calc_rr,
)

logger = logging.getLogger(__name__)
TZ = pytz.timezone("Europe/Moscow")

SIGNALS_FILE = Path("/root/bots/market_analyst_bot/signals_history.json")


# ════════════════════════════════════════════════════════════
#  Датакласс сигнала
# ════════════════════════════════════════════════════════════

@dataclass
class Signal:
    id: str                    # уникальный ID: "BTCUSDT_20260402_0930"
    symbol: str
    asset_type: str
    direction: str             # "long" / "short"
    entry_price: float
    target_price: float
    stop_price: float
    rr: float                  # Risk:Reward
    reward_pct: float          # % до цели
    risk_pct: float            # % до стопа
    phase: str
    bias: str
    condition: str             # текстовое условие входа
    score: float               # 0-10, качество сигнала
    created_at: str
    status: str = "active"     # active / hit_target / hit_stop / expired
    closed_at: str = ""
    result_pct: float = 0.0    # фактический результат


# ════════════════════════════════════════════════════════════
#  Скоринг сигнала (0-10)
#
#  Чем выше — тем лучше сигнал.
#  Критерии:
#  + структура сильная (+2)
#  + bias совпадает с направлением (+1.5)
#  + фаза подходящая (+1.5)
#  + R:R >= 2 (+2), >= 1.5 (+1), >= 1 (+0.5)
#  + HTF и MTF согласованы (+1)
#  + есть OB или FVG в зоне входа (+1)
# ════════════════════════════════════════════════════════════

def _score_signal(
    fa: FullAnalysis,
    direction: str,
    phase: str,
    structure: Dict,
    levels: Dict,
    rr: float,
) -> float:
    score = 0.0

    # Качество структуры
    if structure["quality"] == "сильная":
        score += 2.0
    elif structure["quality"] == "умеренная":
        score += 1.0

    # Совпадение bias и направления
    if (direction == "long"  and structure["bias"] == "bullish") or \
       (direction == "short" and structure["bias"] == "bearish"):
        score += 1.5

    # Фаза рынка
    good_phases = {
        "long":  ["trend↑", "accumulation", "correction"],
        "short": ["trend↓", "distribution", "correction"],
    }
    if any(p in phase for p in good_phases.get(direction, [])):
        score += 1.5

    # R:R
    if rr >= 2.0:
        score += 2.0
    elif rr >= 1.5:
        score += 1.0
    elif rr >= 1.0:
        score += 0.5

    # Согласованность таймфреймов
    tf_trends = [tfa.trend for tfa in fa.timeframes.values()]
    dominant = "bullish" if direction == "long" else "bearish"
    agreement = tf_trends.count(dominant) / max(len(tf_trends), 1)
    if agreement >= 0.6:
        score += 1.0
    elif agreement >= 0.4:
        score += 0.5

    # Наличие OB или FVG (подтверждение зоны)
    if levels["ob_zones"] or levels["fvg_zones"]:
        score += 1.0

    return round(min(score, 10.0), 1)


# ════════════════════════════════════════════════════════════
#  Генерация сигнала из FullAnalysis
# ════════════════════════════════════════════════════════════

def _generate_signal(fa: FullAnalysis) -> Optional[Signal]:
    """Пытается извлечь торговый сигнал из анализа."""
    if not fa or not fa.timeframes or fa.current_price <= 0:
        return None

    try:
        phase, _      = _determine_phase(fa)
        structure     = _determine_structure(fa)
        levels        = _get_key_levels(fa)
        scenarios     = _build_scenarios(fa, structure, levels, phase)
        direction     = scenarios["priority"]

        if direction not in ("long", "short"):
            return None

        # Берём цифры
        if direction == "long":
            condition  = scenarios["long_condition"]
            tgt_str    = scenarios["long_target"]
            stop_str   = scenarios["long_stop"]
        else:
            condition  = scenarios["short_condition"]
            tgt_str    = scenarios["short_target"]
            stop_str   = scenarios["short_stop"]

        if not condition or not tgt_str or not stop_str:
            return None

        entry  = fa.current_price
        target = _parse_price(tgt_str)
        stop   = _parse_price(stop_str)

        if target <= 0 or stop <= 0:
            return None

        # Базовые проверки логики
        if direction == "long"  and not (stop < entry < target):
            return None
        if direction == "short" and not (target < entry < stop):
            return None

        risk       = abs(entry - stop)
        reward     = abs(target - entry)
        if risk == 0:
            return None
        rr         = round(reward / risk, 2)
        reward_pct = round(reward / entry * 100, 2)
        risk_pct   = round(risk   / entry * 100, 2)

        if rr < 1.0:  # отсекаем плохие R:R
            return None

        score = _score_signal(fa, direction, phase, structure, levels, rr)

        now = datetime.now(TZ)
        sig_id = f"{fa.symbol}_{now.strftime('%Y%m%d_%H%M')}"

        return Signal(
            id=sig_id,
            symbol=fa.symbol,
            asset_type=fa.asset_type,
            direction=direction,
            entry_price=entry,
            target_price=target,
            stop_price=stop,
            rr=rr,
            reward_pct=reward_pct,
            risk_pct=risk_pct,
            phase=phase,
            bias=structure["bias"],
            condition=condition,
            score=score,
            created_at=now.isoformat(),
        )

    except Exception as e:
        logger.error(f"_generate_signal {fa.symbol}: {e}")
        return None


# ════════════════════════════════════════════════════════════
#  Выбор лучших сигналов
# ════════════════════════════════════════════════════════════

def get_best_signals(
    active_analyses: Dict[str, FullAnalysis],
    top_n: int = 2,
    min_score: float = 4.0,
) -> List[Signal]:
    """
    Из всех загруженных анализов выбирает top_n лучших сигналов.
    Критерий: score >= min_score, сортировка по score desc.
    """
    signals = []
    for symbol, fa in active_analyses.items():
        sig = _generate_signal(fa)
        if sig and sig.score >= min_score:
            signals.append(sig)

    # Сортируем: сначала по score, потом по R:R
    signals.sort(key=lambda s: (s.score, s.rr), reverse=True)

    # Не берём два сигнала в одном направлении одного класса активов
    result, seen_types = [], set()
    for sig in signals:
        key = f"{sig.asset_type}_{sig.direction}"
        if key not in seen_types:
            result.append(sig)
            seen_types.add(key)
        if len(result) >= top_n:
            break

    return result


# ════════════════════════════════════════════════════════════
#  Форматирование сигнала
# ════════════════════════════════════════════════════════════

def format_signal(sig: Signal, index: int = 1) -> str:
    """Красивый Telegram-текст одного сигнала."""
    dir_icon = "🟢" if sig.direction == "long" else "🔴"
    dir_ru   = "ЛОНГ" if sig.direction == "long" else "ШОРТ"
    asset_icon = {"crypto": "◆", "forex": "◇", "metal": "◈"}.get(sig.asset_type, "·")

    score_bar = "█" * int(sig.score) + "░" * (10 - int(sig.score))
    score_str = f"{sig.score}/10  {score_bar}"

    entry_str  = _fmt_price(sig.entry_price,  sig.symbol)
    target_str = _fmt_price(sig.target_price, sig.symbol)
    stop_str   = _fmt_price(sig.stop_price,   sig.symbol)

    lines = [
        f"{asset_icon} *{sig.symbol}*  {dir_icon} *{dir_ru}*",
        f"",
        f"Вход:   `{entry_str}`",
        f"Цель:   `{target_str}`  _(+{sig.reward_pct}%)_",
        f"Стоп:   `{stop_str}`  _(-{sig.risk_pct}%)_",
        f"R:R:    *{sig.rr}*",
        f"",
        f"Фаза: {sig.phase}  ·  Структура: {sig.bias}",
        f"",
        f"Условие входа:",
        f"_{sig.condition}_",
        f"",
        f"Качество сигнала: {score_str}",
    ]
    return "\n".join(lines)


def format_signals_block(signals: List[Signal], analyses_count: int) -> str:
    """Полное сообщение с сигналами дня."""
    now = datetime.now(TZ).strftime("%d.%m.%Y  %H:%M")

    if not signals:
        return (
            f"🎯 *СИГНАЛ ДНЯ*  ·  {now}\n\n"
            f"Сегодня нет сильных сигналов (score ≥ 4/10).\n\n"
            f"Проанализировано инструментов: {analyses_count}\n\n"
            f"_Рынок в диапазоне или структура противоречивая — лучше ждать._"
        )

    header = f"🎯 *СИГНАЛ ДНЯ*  ·  {now}\nПроанализировано: {analyses_count} инструментов\n{'─' * 28}\n\n"
    parts  = [format_signal(sig, i + 1) for i, sig in enumerate(signals)]
    footer = "\n\n" + "─" * 28 + "\n_⚠️ Это не финансовый совет. Управляй риском самостоятельно._"

    return header + f"\n\n{'─' * 28}\n\n".join(parts) + footer


# ════════════════════════════════════════════════════════════
#  История сигналов (статистика)
# ════════════════════════════════════════════════════════════

def save_signals(signals: List[Signal]):
    """Сохраняет сигналы в JSON-историю."""
    try:
        history = _load_history()
        for sig in signals:
            history[sig.id] = asdict(sig)
        SIGNALS_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.error(f"save_signals: {e}")


def _load_history() -> Dict:
    try:
        if SIGNALS_FILE.exists():
            return json.loads(SIGNALS_FILE.read_text())
    except Exception:
        pass
    return {}


def update_signal_status(symbol: str, current_price: float):
    """
    Проверяет открытые сигналы по символу.
    Если цена достигла цели или стопа — закрывает сигнал.
    """
    try:
        history = _load_history()
        changed = False

        for sig_id, sig_data in history.items():
            if sig_data["symbol"] != symbol:
                continue
            if sig_data["status"] != "active":
                continue

            entry  = sig_data["entry_price"]
            target = sig_data["target_price"]
            stop   = sig_data["stop_price"]
            direction = sig_data["direction"]
            now = datetime.now(TZ).isoformat()

            if direction == "long":
                if current_price >= target:
                    sig_data["status"]     = "hit_target"
                    sig_data["closed_at"]  = now
                    sig_data["result_pct"] = round((current_price - entry) / entry * 100, 2)
                    changed = True
                elif current_price <= stop:
                    sig_data["status"]     = "hit_stop"
                    sig_data["closed_at"]  = now
                    sig_data["result_pct"] = round((current_price - entry) / entry * 100, 2)
                    changed = True
            else:
                if current_price <= target:
                    sig_data["status"]     = "hit_target"
                    sig_data["closed_at"]  = now
                    sig_data["result_pct"] = round((entry - current_price) / entry * 100, 2)
                    changed = True
                elif current_price >= stop:
                    sig_data["status"]     = "hit_stop"
                    sig_data["closed_at"]  = now
                    sig_data["result_pct"] = round((entry - current_price) / entry * 100, 2)
                    changed = True

        if changed:
            SIGNALS_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2))
            return {k: v for k, v in history.items()
                    if v["symbol"] == symbol and v["status"] != "active"}

    except Exception as e:
        logger.error(f"update_signal_status: {e}")
    return {}


def get_stats_text() -> str:
    """Статистика сигналов для отображения в боте."""
    try:
        history = _load_history()
        if not history:
            return "📊 *Статистика сигналов*\n\nИстория пуста. Запусти 🎯 Сигнал дня чтобы начать отслеживание."

        all_sigs  = list(history.values())
        total     = len(all_sigs)
        active    = [s for s in all_sigs if s["status"] == "active"]
        closed    = [s for s in all_sigs if s["status"] != "active"]
        wins      = [s for s in closed if s["status"] == "hit_target"]
        losses    = [s for s in closed if s["status"] == "hit_stop"]

        winrate = round(len(wins) / len(closed) * 100) if closed else 0
        avg_win  = round(sum(s["result_pct"] for s in wins)   / len(wins),   2) if wins   else 0
        avg_loss = round(sum(s["result_pct"] for s in losses) / len(losses),  2) if losses else 0
        avg_rr   = round(sum(s["rr"] for s in all_sigs) / total, 2) if total else 0

        # Последние 5 закрытых
        last5 = sorted(closed, key=lambda x: x.get("closed_at", ""), reverse=True)[:5]
        last5_lines = []
        for s in last5:
            icon = "✅" if s["status"] == "hit_target" else "❌"
            sign = "+" if s["result_pct"] >= 0 else ""
            last5_lines.append(f"  {icon} {s['symbol']} {s['direction'].upper()} {sign}{s['result_pct']}%")

        lines = [
            "📊 *Статистика сигналов*",
            "",
            f"Всего сигналов: {total}",
            f"Активных: {len(active)}",
            f"Закрытых: {len(closed)}",
            "",
            f"✅ Целей достигнуто: {len(wins)}",
            f"❌ Стопов: {len(losses)}",
            f"Винрейт: *{winrate}%*",
            "",
            f"Средний выигрыш: +{avg_win}%",
            f"Средний убыток: {avg_loss}%",
            f"Средний R:R: {avg_rr}",
        ]

        if last5_lines:
            lines.append("")
            lines.append("Последние закрытые:")
            lines.extend(last5_lines)

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"get_stats_text: {e}")
        return "📊 Ошибка загрузки статистики."
