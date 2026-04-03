# ============================================================
#  signal_engine.py  v4  — prop-grade
#
#  Исправлено vs v3:
#  - scan_market_for_best_signal: добавлен 15m для entry layer
#  - format_signals_block удалён (не использовался) — только format_signal
#  - _build_scenarios теперь принимает strategy (совместимость с report_generator v5)
#  - get_best_signals: совместимость с новым _build_scenarios
#  - Все баги v2 сохранены исправленными (PnL, дубли, avg_rr, quality)
# ============================================================

from __future__ import annotations
import json
import logging
import asyncio
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytz

from analyzer import FullAnalysis
from report_generator import (
    _determine_phase, _determine_structure, _get_key_levels,
    _build_scenarios, _fmt_price, _parse_price,
)
from strategy_selector import select_strategy

logger = logging.getLogger(__name__)
TZ = pytz.timezone("Europe/Moscow")

SIGNALS_FILE = Path("/root/bots/market_analyst_bot/signals_history.json")

MIN_RR_SCAN    = 2.0
MAX_STOP_PCT   = 2.0
MIN_SCORE_SCAN = 5.0

# Таймфреймы для сканирования (включая 15m для entry layer)
SCAN_TIMEFRAMES = ["1D", "4H", "1H", "15m"]


# ════════════════════════════════════════════════════════════
#  Датакласс
# ════════════════════════════════════════════════════════════

@dataclass
class Signal:
    id: str
    symbol: str
    asset_type: str
    direction: str
    entry_price: float
    target_price: float
    stop_price: float
    rr: float
    reward_pct: float
    risk_pct: float
    phase: str
    bias: str
    strategy: str
    condition: str
    score: float
    created_at: str
    status: str = "active"
    closed_at: str = ""
    result_pct: float = 0.0
    ai_comment: str = ""


# ════════════════════════════════════════════════════════════
#  СКОРИНГ
# ════════════════════════════════════════════════════════════

def calculate_score(
    fa: FullAnalysis,
    direction: str,
    phase: str,
    structure: Dict,
    levels: Dict,
    rr: float,
) -> float:
    score = 0.0

    # Тренд совпадает (+2)
    bias = structure["bias"]
    if (direction == "long"  and bias == "bullish") or \
       (direction == "short" and bias == "bearish"):
        score += 2.0

    # BOS/ChoCH (+1)
    if structure.get("bos_desc") or structure.get("choch_desc"):
        score += 1.0

    # OB + FVG confluence (+2) или одно из (+1.5)
    if levels["ob_zones"] and levels["fvg_zones"]:
        score += 2.0
    elif levels["ob_zones"] or levels["fvg_zones"]:
        score += 1.5

    # R:R
    if rr >= 2.0:
        score += 2.0
    elif rr >= 1.5:
        score += 1.0
    elif rr >= 1.0:
        score += 0.5

    # Объём (+1)
    for tf in ["4H", "4h", "1D", "1d"]:
        tfa = fa.timeframes.get(tf)
        if tfa and (tfa.indicators.volume_ratio or 1.0) >= 1.3:
            score += 1.0
            break

    # Liquidity sweep (+1)
    for tfa in fa.timeframes.values():
        if any(liq.swept for liq in tfa.liquidity):
            score += 1.0
            break

    # 15m подтверждение (+0.5 бонус)
    tfa_15m = fa.timeframes.get("15m")
    if tfa_15m:
        if direction == "long" and tfa_15m.trend == "bullish":
            score += 0.5
        elif direction == "short" and tfa_15m.trend == "bearish":
            score += 0.5

    # Структура strong (+1)
    if structure.get("quality") == "strong":
        score += 1.0

    return round(min(score, 10.0), 1)


# ════════════════════════════════════════════════════════════
#  ФИЛЬТР
# ════════════════════════════════════════════════════════════

def filter_signals(signals: List[Signal]) -> List[Signal]:
    return [
        s for s in signals
        if s.rr >= MIN_RR_SCAN
        and s.risk_pct <= MAX_STOP_PCT
        and s.score >= MIN_SCORE_SCAN
    ]


def select_best_signal(signals: List[Signal]) -> Optional[Signal]:
    if not signals:
        return None
    return sorted(signals, key=lambda s: (s.score, s.rr), reverse=True)[0]


# ════════════════════════════════════════════════════════════
#  ГЕНЕРАЦИЯ СИГНАЛА
# ════════════════════════════════════════════════════════════

def _generate_signal(fa: FullAnalysis) -> Optional[Signal]:
    if not fa or not fa.timeframes or fa.current_price <= 0:
        return None
    try:
        phase, _  = _determine_phase(fa)
        structure = _determine_structure(fa)
        levels    = _get_key_levels(fa)
        strategy  = select_strategy(fa, phase)

        if not strategy.signal_allowed:
            return None

        scenarios = _build_scenarios(fa, structure, levels, phase, strategy)
        direction = scenarios["priority"]

        if direction not in ("long", "short"):
            return None

        if direction == "long":
            condition  = scenarios["long_condition"]
            tgt_str    = scenarios["long_target"]
            stop_str   = scenarios["long_stop"]
        else:
            condition  = scenarios["short_condition"]
            tgt_str    = scenarios["short_target"]
            stop_str   = scenarios["short_stop"]

        if not all([condition, tgt_str, stop_str]):
            return None

        entry  = fa.current_price
        target = _parse_price(tgt_str)
        stop   = _parse_price(stop_str)

        if target <= 0 or stop <= 0:
            return None
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

        if rr < 1.0:
            return None

        score  = calculate_score(fa, direction, phase, structure, levels, rr)
        now    = datetime.now(TZ)
        sig_id = f"{fa.symbol}_{now.strftime('%Y%m%d_%H%M')}"

        return Signal(
            id=sig_id, symbol=fa.symbol, asset_type=fa.asset_type,
            direction=direction, entry_price=entry,
            target_price=target, stop_price=stop,
            rr=rr, reward_pct=reward_pct, risk_pct=risk_pct,
            phase=phase, bias=structure["bias"],
            strategy=strategy.name, condition=condition,
            score=score, created_at=now.isoformat(),
        )
    except Exception as e:
        logger.error(f"_generate_signal {fa.symbol}: {e}")
        return None


# ════════════════════════════════════════════════════════════
#  СКАН РЫНКА (ТОП-50, включая 15m)
# ════════════════════════════════════════════════════════════

async def scan_market_for_best_signal(
    progress_callback=None,
    top_n_scan: int = 50,
) -> Tuple[Optional[Signal], int]:
    loop = asyncio.get_event_loop()

    try:
        from data_fetcher import get_top_futures_by_volume
        symbols = await loop.run_in_executor(None, get_top_futures_by_volume, top_n_scan, [])
        if not symbols:
            return None, 0
    except Exception as e:
        logger.error(f"scan: get_top_futures: {e}")
        return None, 0

    if progress_callback:
        await progress_callback(f"Загружено {len(symbols)} инструментов. Начинаю анализ...")

    candidates: List[Signal] = []
    scanned = 0

    for i, symbol in enumerate(symbols):
        try:
            from data_fetcher import fetch_bitget_ohlcv
            tf_data = {}
            for tf in SCAN_TIMEFRAMES:
                df = await loop.run_in_executor(None, fetch_bitget_ohlcv, symbol, tf, 100)
                if df is not None and not df.empty:
                    tf_data[tf] = df
                await asyncio.sleep(0.05)

            if len(tf_data) < 2:
                continue

            from analyzer import full_analysis
            fa = await loop.run_in_executor(None, full_analysis, symbol, "crypto", tf_data)

            if not fa or not fa.timeframes or fa.current_price <= 0:
                continue

            sig = _generate_signal(fa)
            if sig:
                candidates.append(sig)

            scanned += 1

            if progress_callback and (i + 1) % 10 == 0:
                await progress_callback(
                    f"Проверено {i+1}/{len(symbols)}  ·  "
                    f"Кандидатов: {len(candidates)}"
                )

        except Exception as e:
            logger.debug(f"scan {symbol}: {e}")
            continue

    filtered = filter_signals(candidates)
    best     = select_best_signal(filtered)

    if progress_callback:
        await progress_callback(
            f"Готово. Просканировано: {scanned}  ·  Прошли фильтр: {len(filtered)}"
        )

    return best, scanned


# ════════════════════════════════════════════════════════════
#  AI КОММЕНТАРИЙ
# ════════════════════════════════════════════════════════════

async def get_ai_comment(sig: Signal) -> str:
    try:
        import os
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key or api_key == "YOUR_CLAUDE_KEY":
            return ""

        import aiohttp
        direction_ru = "лонг" if sig.direction == "long" else "шорт"

        prompt = (
            f"Ты профессиональный трейдер проп-компании. "
            f"Дай краткий комментарий (1-2 предложения) к сигналу. "
            f"Стиль: уверенно, конкретно, без воды, без emoji.\n\n"
            f"Сигнал: {sig.symbol} {direction_ru.upper()} | "
            f"Стратегия: {sig.strategy} | Фаза: {sig.phase} | "
            f"Вход: {sig.entry_price} | Цель: {sig.target_price} (+{sig.reward_pct}%) | "
            f"Стоп: {sig.stop_price} (-{sig.risk_pct}%) | R:R: {sig.rr}\n"
            f"Условие: {sig.condition}"
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 150,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["content"][0]["text"].strip()
    except Exception as e:
        logger.debug(f"get_ai_comment: {e}")
    return ""


# ════════════════════════════════════════════════════════════
#  ИСТОРИЯ СИГНАЛОВ
# ════════════════════════════════════════════════════════════

def _load_history() -> Dict:
    try:
        if SIGNALS_FILE.exists():
            return json.loads(SIGNALS_FILE.read_text())
    except Exception:
        pass
    return {}


def _has_active_duplicate(history: Dict, symbol: str, direction: str) -> bool:
    """Блокирует дубли: активный сигнал по symbol+direction."""
    return any(
        v.get("symbol") == symbol and
        v.get("direction") == direction and
        v.get("status") == "active"
        for v in history.values()
    )


def save_signals(signals: List[Signal]):
    try:
        history = _load_history()
        for sig in signals:
            if _has_active_duplicate(history, sig.symbol, sig.direction):
                logger.info(f"Дубль пропущен: {sig.symbol} {sig.direction}")
                continue
            history[sig.id] = asdict(sig)
        SIGNALS_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.error(f"save_signals: {e}")


def update_signal_status(symbol: str, current_price: float):
    """FIX: PnL считается от stop/target price, не от current."""
    try:
        history = _load_history()
        changed = False
        for sig_data in history.values():
            if sig_data["symbol"] != symbol or sig_data["status"] != "active":
                continue

            entry, target, stop = (
                sig_data["entry_price"],
                sig_data["target_price"],
                sig_data["stop_price"],
            )
            direction = sig_data["direction"]
            now = datetime.now(TZ).isoformat()

            if direction == "long":
                if current_price >= target:
                    sig_data["status"]     = "hit_target"
                    sig_data["closed_at"]  = now
                    sig_data["result_pct"] = round((target - entry) / entry * 100, 2)
                    changed = True
                elif current_price <= stop:
                    sig_data["status"]     = "hit_stop"
                    sig_data["closed_at"]  = now
                    sig_data["result_pct"] = round((stop - entry) / entry * 100, 2)
                    changed = True
            else:
                if current_price <= target:
                    sig_data["status"]     = "hit_target"
                    sig_data["closed_at"]  = now
                    sig_data["result_pct"] = round((entry - target) / entry * 100, 2)
                    changed = True
                elif current_price >= stop:
                    sig_data["status"]     = "hit_stop"
                    sig_data["closed_at"]  = now
                    sig_data["result_pct"] = round((entry - stop) / entry * 100, 2)
                    changed = True

        if changed:
            SIGNALS_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.error(f"update_signal_status: {e}")


# ════════════════════════════════════════════════════════════
#  ФОРМАТИРОВАНИЕ СИГНАЛА
# ════════════════════════════════════════════════════════════

def format_signal(sig: Signal, ai_comment: str = "") -> str:
    d_icon = "🟢" if sig.direction == "long" else "🔴"
    d_ru   = "LONG" if sig.direction == "long" else "SHORT"
    bars   = "█" * int(sig.score / 10 * 8) + "░" * (8 - int(sig.score / 10 * 8))

    lines = [
        f"🎯 *СИГНАЛ МОМЕНТА*",
        f"",
        f"{d_icon} *{sig.symbol}  —  {d_ru}*",
        f"",
        f"Вход:    `{_fmt_price(sig.entry_price,  sig.symbol)}`",
        f"Стоп:    `{_fmt_price(sig.stop_price,   sig.symbol)}`  _(-{sig.risk_pct}%)_",
        f"Тейк:    `{_fmt_price(sig.target_price, sig.symbol)}`  _(+{sig.reward_pct}%)_",
        f"",
        f"R:R:     *{sig.rr}*",
        f"Сетап:   _{sig.strategy}_",
        f"",
        f"Условие:",
        f"_{sig.condition}_",
    ]

    if ai_comment:
        lines += ["", f"💬 _{ai_comment}_"]

    lines += [
        f"",
        f"Качество: *{sig.score}/10*  `{bars}`",
        f"",
        f"{'─' * 28}",
        f"_⚠️ Не финансовый совет. Управляй риском самостоятельно._",
    ]
    return "\n".join(lines)


def format_no_signal(scanned: int) -> str:
    return (
        f"🎯 *СИГНАЛ МОМЕНТА*\n\n"
        f"Просканировано: {scanned} инструментов\n\n"
        f"Сигналов, прошедших фильтр, нет:\n"
        f"  · R:R ≥ {MIN_RR_SCAN}\n"
        f"  · Стоп ≤ {MAX_STOP_PCT}%\n"
        f"  · Качество ≥ {MIN_SCORE_SCAN}/10\n\n"
        f"_Рынок неопределённый. Лучшая позиция сейчас — вне рынка._"
    )


# ════════════════════════════════════════════════════════════
#  ОБРАТНАЯ СОВМЕСТИМОСТЬ
# ════════════════════════════════════════════════════════════

def get_best_signals(
    active_analyses: Dict[str, FullAnalysis],
    top_n: int = 1,
    min_score: float = MIN_SCORE_SCAN,
) -> List[Signal]:
    signals = []
    for fa in active_analyses.values():
        sig = _generate_signal(fa)
        if sig and sig.score >= min_score:
            signals.append(sig)

    signals.sort(key=lambda s: (s.score, s.rr), reverse=True)

    result, seen = [], set()
    for sig in signals:
        key = f"{sig.asset_type}_{sig.direction}"
        if key not in seen:
            result.append(sig)
            seen.add(key)
        if len(result) >= top_n:
            break
    return result


# ════════════════════════════════════════════════════════════
#  СТАТИСТИКА
# ════════════════════════════════════════════════════════════

def get_stats_text() -> str:
    try:
        history = _load_history()
        if not history:
            return "📊 *Статистика*\n\nИстория пуста.\nНажми *🎯 Сигнал дня* чтобы начать."

        all_sigs = list(history.values())
        total    = len(all_sigs)
        active   = [s for s in all_sigs if s["status"] == "active"]
        closed   = [s for s in all_sigs if s["status"] != "active"]
        wins     = [s for s in closed   if s["status"] == "hit_target"]
        losses   = [s for s in closed   if s["status"] == "hit_stop"]

        winrate       = round(len(wins) / len(closed) * 100) if closed else 0
        avg_win       = round(sum(s["result_pct"] for s in wins)   / len(wins),   2) if wins   else 0.0
        avg_loss      = round(sum(s["result_pct"] for s in losses) / len(losses), 2) if losses else 0.0
        avg_rr_closed = round(sum(s["rr"] for s in closed) / len(closed), 2) if closed else 0.0

        max_dd               = _calc_max_drawdown(closed)
        win_streak, loss_str = _calc_streaks(closed)

        last5 = sorted(closed, key=lambda x: x.get("closed_at", ""), reverse=True)[:5]
        last5_lines = [
            f"  {'✅' if s['status'] == 'hit_target' else '❌'} "
            f"{s['symbol']} {s['direction'].upper()} "
            f"{'+' if s['result_pct'] >= 0 else ''}{s['result_pct']}%"
            for s in last5
        ]

        lines = [
            "📊 *Статистика сигналов*", "",
            f"Всего: {total}  ·  Активных: {len(active)}  ·  Закрытых: {len(closed)}", "",
            f"✅ Целей: {len(wins)}  ·  ❌ Стопов: {len(losses)}",
            f"Винрейт: *{winrate}%*", "",
            f"Средний выигрыш:  *+{avg_win}%*",
            f"Средний убыток:   *{avg_loss}%*",
            f"Средний R:R (закрытые): *{avg_rr_closed}*", "",
            f"Max просадка: *{max_dd:.1f}%*",
            f"Серия побед: {win_streak}  ·  Серия поражений: {loss_str}",
        ]

        if last5_lines:
            lines += ["", "*Последние закрытые:*"] + last5_lines

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"get_stats_text: {e}")
        return "📊 Ошибка загрузки статистики."


def _calc_max_drawdown(closed: List[Dict]) -> float:
    if not closed:
        return 0.0
    sorted_c   = sorted(closed, key=lambda x: x.get("closed_at", ""))
    cum, peak, max_dd = 0.0, 0.0, 0.0
    for s in sorted_c:
        cum += s["result_pct"]
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


def _calc_streaks(closed: List[Dict]) -> Tuple[int, int]:
    if not closed:
        return 0, 0
    sorted_c = sorted(closed, key=lambda x: x.get("closed_at", ""), reverse=True)
    win_s = loss_s = 0
    for s in sorted_c:
        if s["status"] == "hit_target":
            if loss_s == 0:
                win_s += 1
            else:
                break
        else:
            if win_s == 0:
                loss_s += 1
            else:
                break
    return win_s, loss_s
