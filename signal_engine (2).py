# ============================================================
# signal_engine.py — УЛУЧШЕННАЯ ВЕРСИЯ
#
# Изменения:
# 1. format_signal() — понятный нарратив, не техно-мусор
# 2. Сканирует ТОП-50 фьючерсов Bitget
# 3. AI-комментарий (Claude API)
# 4. Жёсткие фильтры RR / стоп / score
# 5. Дедупликация дублей
# 6. Статистика и история сигналов
# ============================================================

from __future__ import annotations
import json
import logging
import asyncio
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import pytz

from analyzer import FullAnalysis
from report_generator import (
    _determine_phase, _determine_structure, _get_key_levels,
    _fmt_price, _parse_price, _build_scenarios, _calc_rr,
)

try:
    from strategy_selector import select_strategy
    HAS_STRATEGY = True
except ImportError:
    HAS_STRATEGY = False

logger = logging.getLogger(__name__)
TZ = pytz.timezone("Europe/Moscow")
SIGNALS_FILE = Path("/root/bots/market_analyst_bot/signals_history.json")

# ── Фильтры
MIN_RR_SCAN   = 2.0   # минимальный R:R
MAX_STOP_PCT  = 2.0   # максимальный стоп в %
MIN_SCORE_SCAN = 5.0  # минимальный балл качества

# ── ТОП-50 фьючерсов Bitget для скана сигнала дня
TOP_50_FUTURES = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
    "MATICUSDT", "LTCUSDT", "UNIUSDT", "ATOMUSDT", "NEARUSDT",
    "APTUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "SUIUSDT",
    "SEIUSDT", "TIAUSDT", "WLDUSDT", "JUPUSDT", "PYTHUSDT",
    "STXUSDT", "RUNEUSDT", "FETUSDT", "RENDERUSDT", "THETAUSDT",
    "SANDUSDT", "MANAUSDT", "GALAUSDT", "APEUSDT", "GMXUSDT",
    "DYDXUSDT", "PEPEUSDT", "SHIBUSDT", "FLOKIUSDT", "BONKUSDT",
    "WIFUSDT", "MEMEUSDT", "EIGENUSDT", "MOODENGUSDT", "ENAUSDT",
    "PENDLEUSDT", "AAVEUSDT", "MKRUSDT", "COMPUSDT", "CRVUSDT",
]


# ════════════════════════════════════════════════════════════
# Датакласс сигнала
# ════════════════════════════════════════════════════════════

@dataclass
class Signal:
    id: str
    symbol: str
    asset_type: str
    direction: str          # "long" / "short"
    entry_price: float
    target_price: float
    stop_price: float
    rr: float
    reward_pct: float
    risk_pct: float
    phase: str
    phase_title: str
    bias: str
    strategy: str
    condition: str
    why: str                # причина сигнала (нарратив)
    score: float            # 0-10
    created_at: str
    status: str = "active"  # active / hit_target / hit_stop / expired
    closed_at: str = ""
    result_pct: float = 0.0
    ai_comment: str = ""


# ════════════════════════════════════════════════════════════
# СКОРИНГ
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

    # 1. Тренд совпадает (+2)
    bias = structure["bias"]
    if (direction == "long" and bias == "bullish") or \
       (direction == "short" and bias == "bearish"):
        score += 2.0

    # 2. BOS или ChoCH подтверждён (+1)
    if structure.get("bos_desc") or structure.get("choch_desc"):
        score += 1.0

    # 3. OB + FVG confluence (+2 / +1.5)
    if levels["ob_zones"] and levels["fvg_zones"]:
        score += 2.0
    elif levels["ob_zones"] or levels["fvg_zones"]:
        score += 1.5

    # 4. R:R
    if rr >= 2.0:
        score += 2.0
    elif rr >= 1.5:
        score += 1.0
    elif rr >= 1.0:
        score += 0.5

    # 5. Объём подтверждает (+1)
    for tf in ["4H", "4h", "1D", "1d"]:
        tfa = fa.timeframes.get(tf)
        if tfa and (tfa.indicators.volume_ratio or 1.0) >= 1.3:
            score += 1.0
            break

    # 6. Liquidity sweep (+1)
    for tf_name, tfa in fa.timeframes.items():
        if any(liq.swept for liq in tfa.liquidity):
            score += 1.0
            break

    # 7. Сильная структура бонус (+1)
    if structure.get("quality") == "strong":
        score += 1.0

    return round(min(score, 10.0), 1)


# ════════════════════════════════════════════════════════════
# ГЕНЕРАЦИЯ СИГНАЛА ИЗ FullAnalysis
# ════════════════════════════════════════════════════════════

def _generate_signal(fa: FullAnalysis) -> Optional[Signal]:
    if not fa or not fa.timeframes or fa.current_price <= 0:
        return None

    try:
        phase, phase_title, phase_why = _determine_phase(fa)
        structure = _determine_structure(fa)
        levels = _get_key_levels(fa)

        strategy_name = "SMC Institutional"
        if HAS_STRATEGY:
            try:
                strategy = select_strategy(fa, phase)
                strategy_name = strategy.name
                if not strategy.signal_allowed:
                    return None
            except Exception:
                pass

        scenarios = _build_scenarios(fa, structure, levels, phase)
        direction = scenarios["priority"]

        if direction not in ("long", "short"):
            return None

        if direction == "long":
            condition = scenarios["long_condition"]
            tgt_str   = scenarios["long_target"]
            stop_str  = scenarios["long_stop"]
            why       = scenarios.get("long_why", "")
        else:
            condition = scenarios["short_condition"]
            tgt_str   = scenarios["short_target"]
            stop_str  = scenarios["short_stop"]
            why       = scenarios.get("short_why", "")

        if not condition or not tgt_str or not stop_str:
            return None

        entry  = fa.current_price
        target = _parse_price(tgt_str)
        stop   = _parse_price(stop_str)

        if target <= 0 or stop <= 0:
            return None
        if direction == "long" and not (stop < entry < target):
            return None
        if direction == "short" and not (target < entry < stop):
            return None

        risk       = abs(entry - stop)
        reward     = abs(target - entry)
        if risk == 0:
            return None

        rr         = round(reward / risk, 2)
        reward_pct = round(reward / entry * 100, 2)
        risk_pct   = round(risk / entry * 100, 2)

        if rr < 1.0:
            return None

        score = calculate_score(fa, direction, phase, structure, levels, rr)
        now   = datetime.now(TZ)
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
            phase_title=phase_title,
            bias=structure["bias"],
            strategy=strategy_name,
            condition=condition,
            why=why,
            score=score,
            created_at=now.isoformat(),
        )

    except Exception as e:
        logger.error(f"_generate_signal {fa.symbol}: {e}")
        return None


# ════════════════════════════════════════════════════════════
# ФИЛЬТР И ВЫБОР ЛУЧШЕГО
# ════════════════════════════════════════════════════════════

def filter_signals(signals: List[Signal]) -> List[Signal]:
    result = []
    for sig in signals:
        if sig.rr < MIN_RR_SCAN:
            continue
        if sig.risk_pct > MAX_STOP_PCT:
            continue
        if sig.score < MIN_SCORE_SCAN:
            continue
        result.append(sig)
    return result


def select_best_signal(signals: List[Signal]) -> Optional[Signal]:
    if not signals:
        return None
    signals.sort(key=lambda s: (s.score, s.rr), reverse=True)
    return signals[0]


# ════════════════════════════════════════════════════════════
# СКАН РЫНКА — ТОП-50 ФЬЮЧЕРСОВ BITGET
# ════════════════════════════════════════════════════════════

async def scan_market_for_best_signal(
    progress_callback=None,
    top_n_scan: int = 50,
) -> Tuple[Optional[Signal], int]:
    """
    Сканирует TOP_50_FUTURES (или top_n_scan символов по объёму).
    Возвращает (лучший_сигнал, кол-во_просканировано).
    """
    loop = asyncio.get_event_loop()

    # ── Шаг 1: список символов
    symbols = TOP_50_FUTURES[:top_n_scan]
    try:
        from data_fetcher import get_top_futures_by_volume
        dynamic = await loop.run_in_executor(
            None, get_top_futures_by_volume, top_n_scan, []
        )
        if dynamic and len(dynamic) >= 20:
            symbols = dynamic[:top_n_scan]
    except Exception as e:
        logger.warning(f"scan: fallback to static list ({e})")

    if progress_callback:
        await progress_callback(f"📡 Анализирую {len(symbols)} инструментов...")

    # ── Шаг 2: анализ каждого
    FAST_TF = ["1D", "4H", "1H"]
    candidates: List[Signal] = []
    scanned = 0

    for i, symbol in enumerate(symbols):
        try:
            from data_fetcher import fetch_bitget_ohlcv
            tf_data = {}
            for tf in FAST_TF:
                df = await loop.run_in_executor(None, fetch_bitget_ohlcv, symbol, tf, 100)
                if df is not None and not df.empty:
                    tf_data[tf] = df
                await asyncio.sleep(0.05)

            if len(tf_data) < 2:
                continue

            from analyzer import full_analysis
            fa = await loop.run_in_executor(
                None, full_analysis, symbol, "crypto", tf_data
            )
            if not fa or not fa.timeframes or fa.current_price <= 0:
                continue

            sig = _generate_signal(fa)
            if sig:
                candidates.append(sig)

            scanned += 1

            if progress_callback and (i + 1) % 10 == 0:
                await progress_callback(
                    f"🔍 Проверено {i+1}/{len(symbols)}... "
                    f"Кандидатов: {len(candidates)}"
                )

        except Exception as e:
            logger.debug(f"scan {symbol}: {e}")
            continue

    # ── Шаг 3: фильтр и выбор
    filtered = filter_signals(candidates)
    best     = select_best_signal(filtered)

    if progress_callback:
        passed = len(filtered)
        await progress_callback(
            f"✅ Готово. Просканировано: {scanned}. "
            f"Прошли фильтр: {passed}."
        )

    return best, scanned


# ════════════════════════════════════════════════════════════
# AI КОММЕНТАРИЙ (Claude API)
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
            f"Ты опытный трейдер проп-компании. Дай краткий комментарий (1-2 предложения) "
            f"к сигналу на {direction_ru.upper()} по {sig.symbol}. "
            f"Стиль: конкретный, профессиональный, без воды и emoji.\n\n"
            f"Данные сигнала:\n"
            f"Фаза рынка: {sig.phase_title}\n"
            f"Стратегия: {sig.strategy}\n"
            f"Вход: {sig.entry_price}, Цель: {sig.target_price} (+{sig.reward_pct}%), "
            f"Стоп: {sig.stop_price} (-{sig.risk_pct}%), R:R: {sig.rr}\n"
            f"Условие входа: {sig.condition}\n"
            f"Обоснование: {sig.why}"
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
# ФОРМАТИРОВАНИЕ СИГНАЛА — понятный нарратив
# ════════════════════════════════════════════════════════════

def format_signal(sig: Signal, ai_comment: str = "") -> str:
    """
    Формат сигнала для трейдера:
    Понятно ЧТО, ПОЧЕМУ, КАК ТОРГОВАТЬ.
    """
    dir_icon = "🟢" if sig.direction == "long" else "🔴"
    dir_ru   = "LONG  (покупка)" if sig.direction == "long" else "SHORT  (продажа)"

    score_filled = int(sig.score / 10 * 8)
    score_bar    = "█" * score_filled + "░" * (8 - score_filled)

    entry_s  = _fmt_price(sig.entry_price, sig.symbol)
    target_s = _fmt_price(sig.target_price, sig.symbol)
    stop_s   = _fmt_price(sig.stop_price, sig.symbol)

    # Объяснение R:R простыми словами
    rr_explain = (
        f"За каждый 1$ риска потенциал {sig.rr}$ прибыли"
        if sig.rr >= 2 else
        f"R:R {sig.rr} — приемлемо"
    )

    lines = [
        f"🎯 *СИГНАЛ ДНЯ*",
        f"",
        f"{dir_icon} *{sig.symbol} — {dir_ru}*",
        f"Фаза: _{sig.phase_title}_",
        f"",
        f"*Точка входа:* `{entry_s}`",
        f"*Стоп-лосс:*   `{stop_s}`  _(-{sig.risk_pct}%)_",
        f"*Тейк-профит:* `{target_s}`  _(+{sig.reward_pct}%)_",
        f"",
        f"*R:R {sig.rr}*  —  _{rr_explain}_",
        f"",
        f"*Почему этот сигнал:*",
        f"_{sig.why}_" if sig.why else f"_Сетап: {sig.strategy}_",
        f"",
        f"*Условие входа:*",
        f"_{sig.condition}_",
    ]

    if ai_comment:
        lines.append("")
        lines.append(f"💬 _{ai_comment}_")

    lines += [
        f"",
        f"Качество: *{sig.score}/10*  `{score_bar}`",
        f"Стратегия: _{sig.strategy}_",
        f"",
        f"{'─' * 28}",
        f"_⚠️ Не финансовый совет. Управляй риском._",
    ]

    return "\n".join(lines)


def format_no_signal(scanned: int) -> str:
    return (
        f"🎯 *СИГНАЛ ДНЯ*\n\n"
        f"Просканировано: {scanned} инструментов Bitget\n\n"
        f"*Сегодня нет сигналов,* прошедших все фильтры:\n"
        f"  · R:R ≥ {MIN_RR_SCAN}\n"
        f"  · Стоп ≤ {MAX_STOP_PCT}%\n"
        f"  · Качество сетапа ≥ {MIN_SCORE_SCAN}/10\n\n"
        f"_Рынок неопределённый. Лучшая позиция сейчас — вне рынка.\n"
        f"Следующий скан через 1 час._"
    )


# ════════════════════════════════════════════════════════════
# ИСТОРИЯ — сохранение, загрузка, дедупликация
# ════════════════════════════════════════════════════════════

def _load_history() -> Dict:
    try:
        if SIGNALS_FILE.exists():
            return json.loads(SIGNALS_FILE.read_text())
    except Exception:
        pass
    return {}


def _has_active_duplicate(history: Dict, symbol: str, direction: str) -> bool:
    for sig_data in history.values():
        if (sig_data.get("symbol") == symbol and
                sig_data.get("direction") == direction and
                sig_data.get("status") == "active"):
            return True
    return False


def save_signals(signals: List[Signal]):
    try:
        history = _load_history()
        saved = 0
        for sig in signals:
            if _has_active_duplicate(history, sig.symbol, sig.direction):
                logger.info(f"Дубль пропущен: {sig.symbol} {sig.direction}")
                continue
            history[sig.id] = asdict(sig)
            saved += 1
        if saved:
            SIGNALS_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.error(f"save_signals: {e}")


def update_signal_status(symbol: str, current_price: float):
    try:
        history = _load_history()
        changed = False
        for sig_id, sig_data in history.items():
            if sig_data["symbol"] != symbol:
                continue
            if sig_data["status"] != "active":
                continue

            entry     = sig_data["entry_price"]
            target    = sig_data["target_price"]
            stop      = sig_data["stop_price"]
            direction = sig_data["direction"]
            now       = datetime.now(TZ).isoformat()

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

        return {k: v for k, v in history.items()
                if v["symbol"] == symbol and v["status"] != "active"}

    except Exception as e:
        logger.error(f"update_signal_status: {e}")
        return {}


# ════════════════════════════════════════════════════════════
# СТАТИСТИКА — понятный формат для трейдера
# ════════════════════════════════════════════════════════════

def get_stats_text() -> str:
    try:
        history = _load_history()
        if not history:
            return (
                "📊 *Статистика сигналов*\n\n"
                "История пуста.\nНажми *🎯 Сигнал дня* чтобы начать."
            )

        all_sigs = list(history.values())
        total    = len(all_sigs)
        active   = [s for s in all_sigs if s["status"] == "active"]
        closed   = [s for s in all_sigs if s["status"] != "active"]
        wins     = [s for s in closed if s["status"] == "hit_target"]
        losses   = [s for s in closed if s["status"] == "hit_stop"]

        winrate  = round(len(wins) / len(closed) * 100) if closed else 0
        avg_win  = round(sum(s["result_pct"] for s in wins) / len(wins), 2) if wins else 0.0
        avg_loss = round(sum(s["result_pct"] for s in losses) / len(losses), 2) if losses else 0.0
        avg_rr   = round(sum(s["rr"] for s in closed) / len(closed), 2) if closed else 0.0

        max_dd              = _calc_max_drawdown(closed)
        win_streak, loss_streak = _calc_streaks(closed)

        # Последние 5 закрытых
        last5 = sorted(closed, key=lambda x: x.get("closed_at", ""), reverse=True)[:5]
        last5_lines = []
        for s in last5:
            icon = "✅" if s["status"] == "hit_target" else "❌"
            sign = "+" if s["result_pct"] >= 0 else ""
            last5_lines.append(
                f"  {icon} {s['symbol']} {s['direction'].upper()} "
                f"{sign}{s['result_pct']}%"
            )

        lines = [
            "📊 *Статистика сигналов*",
            "",
            f"Всего: {total}  ·  Активных: {len(active)}  ·  Закрытых: {len(closed)}",
            "",
            f"✅ В цель: {len(wins)}  ·  ❌ По стопу: {len(losses)}",
            f"Винрейт: *{winrate}%*",
            "",
            f"Средний профит: *+{avg_win}%*",
            f"Средний убыток: *{avg_loss}%*",
            f"Средний R:R (закрытые): *{avg_rr}*",
            "",
            f"Макс. просадка серией: *{max_dd:.1f}%*",
            f"Серия побед: {win_streak}  ·  Серия поражений: {loss_streak}",
        ]

        if last5_lines:
            lines.append("")
            lines.append("*Последние закрытые:*")
            lines.extend(last5_lines)

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"get_stats_text: {e}")
        return "📊 Ошибка загрузки статистики."


def _calc_max_drawdown(closed: List[Dict]) -> float:
    if not closed:
        return 0.0
    sorted_c   = sorted(closed, key=lambda x: x.get("closed_at", ""))
    cumulative = 0.0
    peak       = 0.0
    max_dd     = 0.0
    for s in sorted_c:
        cumulative += s["result_pct"]
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


def _calc_streaks(closed: List[Dict]) -> Tuple[int, int]:
    if not closed:
        return 0, 0
    sorted_c   = sorted(closed, key=lambda x: x.get("closed_at", ""), reverse=True)
    win_streak = 0
    loss_streak = 0
    for s in sorted_c:
        if s["status"] == "hit_target":
            if loss_streak == 0:
                win_streak += 1
            else:
                break
        else:
            if win_streak == 0:
                loss_streak += 1
            else:
                break
    return win_streak, loss_streak


# ════════════════════════════════════════════════════════════
# ОБРАТНАЯ СОВМЕСТИМОСТЬ — get_best_signals (для /report)
# ════════════════════════════════════════════════════════════

def get_best_signals(
    active_analyses: Dict[str, FullAnalysis],
    top_n: int = 1,
    min_score: float = MIN_SCORE_SCAN,
) -> List[Signal]:
    signals = []
    for symbol, fa in active_analyses.items():
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
