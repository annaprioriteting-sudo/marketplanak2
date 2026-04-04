# ============================================================
# signal_engine.py — ИСПРАВЛЕНА ЛОГИКА ВХОДА
#
# ГЛАВНЫЙ БАГ:
#   entry_price = fa.current_price  ← вход по рынку прямо сейчас
#   stop = ближайший уровень рядом  ← стоп 0.3%, всегда выбивает
#
# ИСПРАВЛЕНИЕ:
#   entry = граница OB/FVG (лимитный ордер, ждём возврата цены)
#   stop  = нижняя граница OB - ATR*1.5 (за блоком, нормальный размер)
#   Сигнал всегда ЛИМИТНЫЙ, не рыночный
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
    _fmt_price, _parse_price, _calc_rr,
)

try:
    from strategy_selector import select_strategy
    HAS_STRATEGY = True
except ImportError:
    HAS_STRATEGY = False

logger = logging.getLogger(__name__)
TZ = pytz.timezone("Europe/Moscow")
SIGNALS_FILE = Path("/root/bots/market_analyst_bot/signals_history.json")

MIN_RR_SCAN    = 2.0
MAX_STOP_PCT   = 3.5   # стоп за OB — может быть 2-3%, не 0.3%
MIN_SCORE_SCAN = 5.0

TOP_50_FUTURES = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "DOGEUSDT","ADAUSDT","AVAXUSDT","LINKUSDT","DOTUSDT",
    "LTCUSDT","UNIUSDT","ATOMUSDT","NEARUSDT","APTUSDT",
    "ARBUSDT","OPUSDT","INJUSDT","SUIUSDT","SEIUSDT",
    "STXUSDT","RUNEUSDT","FETUSDT","RENDERUSDT","PENDLEUSDT",
    "AAVEUSDT","MKRUSDT","COMPUSDT","CRVUSDT","GMXUSDT",
    "DYDXUSDT","TIAUSDT","WLDUSDT","JUPUSDT","PYTHUSDT",
    "EIGENUSDT","ENAUSDT","THETAUSDT","SANDUSDT","MANAUSDT",
    "GALAUSDT","APEUSDT","PEPEUSDT","SHIBUSDT","FLOKIUSDT",
    "BONKUSDT","WIFUSDT","MEMEUSDT","MATICUSDT","HBARUSDT",
]


# ════════════════════════════════════════════════════════════
# ДАТАКЛАСС СИГНАЛА
# ════════════════════════════════════════════════════════════

@dataclass
class Signal:
    id: str
    symbol: str
    asset_type: str
    direction: str
    entry_type: str        # "limit" всегда, "market" если уже в зоне
    entry_price: float     # цена лимитного ордера
    current_price: float   # текущая цена (для информации)
    target_price: float
    stop_price: float
    rr: float
    reward_pct: float
    risk_pct: float
    phase: str
    phase_title: str
    bias: str
    strategy: str
    entry_zone_top: float
    entry_zone_bot: float
    entry_zone_type: str   # "OB" / "FVG" / "SR"
    entry_zone_tf: str
    condition: str
    why: str
    score: float
    created_at: str
    status: str = "active"
    closed_at: str = ""
    result_pct: float = 0.0
    ai_comment: str = ""


# ════════════════════════════════════════════════════════════
# РАСЧЁТ ВХОДА ОТ ЗОНЫ (ИСПРАВЛЕННАЯ ЛОГИКА)
# ════════════════════════════════════════════════════════════

def _find_entry_from_zone(
    fa: FullAnalysis,
    direction: str,
    levels: Dict,
) -> Optional[Tuple]:
    """
    Ищет ближайшую зону OB/FVG для лимитного ордера.

    ЛОНГ → бычий OB/FVG НИЖЕ цены → entry = верх зоны, stop = низ - ATR*1.5
    ШОРТ → медвежий OB/FVG ВЫШЕ цены → entry = низ зоны, stop = верх + ATR*1.5

    Возвращает (entry, stop, zone_top, zone_bot, atr, zone_type, tf_name) или None.
    """
    price = fa.current_price
    if price <= 0:
        return None

    # ATR из 4H или 1H
    atr = 0.0
    for tf in ["4H","4h","1H","1h","1D","1d"]:
        tfa = fa.timeframes.get(tf)
        if tfa and tfa.indicators.atr > 0:
            atr = tfa.indicators.atr
            break
    if atr <= 0:
        atr = price * 0.005

    best = None
    best_dist = float('inf')

    # ── Order Blocks
    for tf_name, tfa in fa.timeframes.items():
        for ob in tfa.order_blocks:
            if ob.mitigated:
                continue
            if direction == "long" and ob.type == "bullish" and ob.top < price:
                entry = ob.top
                stop  = ob.bottom - atr * 1.5
                if stop <= 0:
                    continue
                dist = price - ob.top
                if dist < best_dist:
                    best_dist = dist
                    best = (entry, stop, ob.top, ob.bottom, atr, "OB", tf_name)

            elif direction == "short" and ob.type == "bearish" and ob.bottom > price:
                entry = ob.bottom
                stop  = ob.top + atr * 1.5
                dist  = ob.bottom - price
                if dist < best_dist:
                    best_dist = dist
                    best = (entry, stop, ob.top, ob.bottom, atr, "OB", tf_name)

    # ── Fair Value Gaps
    for tf_name, tfa in fa.timeframes.items():
        for fvg in tfa.fvgs:
            if fvg.filled:
                continue
            if direction == "long" and fvg.type == "bullish" and fvg.top < price:
                entry = fvg.top
                stop  = fvg.bottom - atr * 1.0
                if stop <= 0:
                    continue
                dist = price - fvg.top
                # FVG вторичен — только если нет OB
                if best is None and dist < best_dist * 1.2:
                    best = (entry, stop, fvg.top, fvg.bottom, atr, "FVG", tf_name)

            elif direction == "short" and fvg.type == "bearish" and fvg.bottom > price:
                entry = fvg.bottom
                stop  = fvg.top + atr * 1.0
                dist  = fvg.bottom - price
                if best is None and dist < best_dist * 1.2:
                    best = (entry, stop, fvg.top, fvg.bottom, atr, "FVG", tf_name)

    # ── Fallback: S/R уровень
    if best is None:
        sups = levels["supports"]
        ress = levels["resistances"]
        if direction == "long" and sups:
            p, src = sups[0]
            entry = p * 1.001
            stop  = p - atr * 2.0
            if stop > 0:
                best = (entry, stop, p * 1.002, p * 0.999, atr, "SR", src[:6])
        elif direction == "short" and ress:
            p, src = ress[0]
            entry = p * 0.999
            stop  = p + atr * 2.0
            best = (entry, stop, p * 1.001, p * 0.998, atr, "SR", src[:6])

    return best


# ════════════════════════════════════════════════════════════
# СКОРИНГ
# ════════════════════════════════════════════════════════════

def calculate_score(
    fa: FullAnalysis, direction: str, phase: str,
    structure: Dict, levels: Dict, rr: float,
    has_ob: bool, has_fvg: bool,
) -> float:
    score = 0.0
    bias = structure["bias"]
    if (direction == "long" and bias == "bullish") or \
       (direction == "short" and bias == "bearish"):
        score += 2.0
    if structure.get("bos_desc") or structure.get("choch_desc"):
        score += 1.0
    if has_ob and has_fvg:
        score += 2.0
    elif has_ob:
        score += 1.5
    elif has_fvg:
        score += 1.0
    if rr >= 3.0:   score += 2.0
    elif rr >= 2.0: score += 1.5
    elif rr >= 1.5: score += 1.0
    elif rr >= 1.0: score += 0.5
    for tf in ["4H","4h","1D","1d"]:
        tfa = fa.timeframes.get(tf)
        if tfa and (tfa.indicators.volume_ratio or 1.0) >= 1.3:
            score += 1.0; break
    for tf_name, tfa in fa.timeframes.items():
        if any(liq.swept for liq in tfa.liquidity):
            score += 1.0; break
    if structure.get("quality") == "strong":
        score += 1.0
    return round(min(score, 10.0), 1)


# ════════════════════════════════════════════════════════════
# ГЕНЕРАЦИЯ СИГНАЛА
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
                strat = select_strategy(fa, phase)
                strategy_name = strat.name
                if not strat.signal_allowed:
                    return None
            except Exception:
                pass

        bias = structure["bias"]
        if bias == "bullish":
            direction = "long"
        elif bias == "bearish":
            direction = "short"
        else:
            return None

        if phase == "range":
            return None

        current_price = fa.current_price

        # ИСПРАВЛЕНИЕ: вход только от зоны, не по текущей цене
        zone = _find_entry_from_zone(fa, direction, levels)
        if zone is None:
            return None
        entry, stop, zone_top, zone_bot, atr, zone_type, zone_tf = zone

        # Цель
        target = 0.0
        if direction == "long":
            ress = [(p, s) for p, s in levels["resistances"] if p > entry * 1.003]
            target = ress[0][0] if ress else entry + atr * 3.0
        else:
            sups = [(p, s) for p, s in levels["supports"] if p < entry * 0.997]
            target = sups[0][0] if sups else entry - atr * 3.0

        if target <= 0:
            return None
        if direction == "long" and not (stop < entry < target):
            return None
        if direction == "short" and not (target < entry < stop):
            return None

        risk   = abs(entry - stop)
        reward = abs(target - entry)
        if risk == 0:
            return None

        rr         = round(reward / risk, 2)
        reward_pct = round(reward / entry * 100, 2)
        risk_pct   = round(risk / entry * 100, 2)
        if rr < 1.0:
            return None

        price_in_zone = zone_bot <= current_price <= zone_top
        has_ob  = any(not ob.mitigated for tfa in fa.timeframes.values() for ob in tfa.order_blocks)
        has_fvg = any(not fvg.filled for tfa in fa.timeframes.values() for fvg in tfa.fvgs)

        score = calculate_score(fa, direction, phase, structure, levels, rr, has_ob, has_fvg)

        dist_pct = abs(current_price - entry) / current_price * 100
        if price_in_zone:
            entry_desc = (
                f"цена СЕЙЧАС в зоне {zone_type} {zone_tf} — "
                f"вход по рынку с подтверждением бычьей/медвежьей свечи на 1H"
            )
            entry_type = "market"
        else:
            dir_word = "откатится" if direction == "long" else "отскочит"
            entry_desc = (
                f"выставляй лимитный ордер на `{_fmt_price(entry, fa.symbol)}` — "
                f"ждём когда цена {dir_word} к {zone_type} {zone_tf} "
                f"[{_fmt_price(zone_bot, fa.symbol)}–{_fmt_price(zone_top, fa.symbol)}] "
                f"({dist_pct:.1f}% от текущей)"
            )
            entry_type = "limit"

        phase_short = phase_why.split(".")[0] if phase_why else ""
        if direction == "long":
            why = f"{phase_short}. Лимит у бычьего {zone_type} {zone_tf}, стоп за блоком ({risk_pct:.1f}%)."
        else:
            why = f"{phase_short}. Лимит у медвежьего {zone_type} {zone_tf}, стоп за блоком ({risk_pct:.1f}%)."

        now    = datetime.now(TZ)
        sig_id = f"{fa.symbol}_{now.strftime('%Y%m%d_%H%M')}"

        return Signal(
            id=sig_id, symbol=fa.symbol, asset_type=fa.asset_type,
            direction=direction, entry_type=entry_type,
            entry_price=round(entry, 8), current_price=round(current_price, 8),
            target_price=round(target, 8), stop_price=round(stop, 8),
            rr=rr, reward_pct=reward_pct, risk_pct=risk_pct,
            phase=phase, phase_title=phase_title, bias=bias,
            strategy=strategy_name,
            entry_zone_top=round(zone_top, 8), entry_zone_bot=round(zone_bot, 8),
            entry_zone_type=zone_type, entry_zone_tf=zone_tf,
            condition=entry_desc, why=why, score=score,
            created_at=now.isoformat(),
        )
    except Exception as e:
        logger.error(f"_generate_signal {fa.symbol}: {e}")
        return None


# ════════════════════════════════════════════════════════════
# ФИЛЬТР / СКАН / ФОРМАТИРОВАНИЕ
# ════════════════════════════════════════════════════════════

def filter_signals(signals: List[Signal]) -> List[Signal]:
    return [s for s in signals
            if s.rr >= MIN_RR_SCAN
            and s.risk_pct <= MAX_STOP_PCT
            and s.score >= MIN_SCORE_SCAN]


def select_best_signal(signals: List[Signal]) -> Optional[Signal]:
    if not signals:
        return None
    signals.sort(key=lambda s: (s.score, s.rr), reverse=True)
    return signals[0]


async def scan_market_for_best_signal(progress_callback=None, top_n_scan=50):
    loop = asyncio.get_event_loop()
    symbols = TOP_50_FUTURES[:top_n_scan]
    try:
        from data_fetcher import get_top_futures_by_volume
        d = await loop.run_in_executor(None, get_top_futures_by_volume, top_n_scan, [])
        if d and len(d) >= 20:
            symbols = d[:top_n_scan]
    except Exception as e:
        logger.warning(f"scan fallback ({e})")

    if progress_callback:
        await progress_callback(f"📡 Анализирую {len(symbols)} инструментов...")

    candidates, scanned = [], 0
    for i, symbol in enumerate(symbols):
        try:
            from data_fetcher import fetch_bitget_ohlcv
            from analyzer import full_analysis
            tf_data = {}
            for tf in ["1D","4H","1H"]:
                df = await loop.run_in_executor(None, fetch_bitget_ohlcv, symbol, tf, 150)
                if df is not None and not df.empty:
                    tf_data[tf] = df
                await asyncio.sleep(0.05)
            if len(tf_data) < 2:
                continue
            fa = await loop.run_in_executor(None, full_analysis, symbol, "crypto", tf_data)
            if not fa or fa.current_price <= 0:
                continue
            sig = _generate_signal(fa)
            if sig:
                candidates.append(sig)
            scanned += 1
            if progress_callback and (i + 1) % 10 == 0:
                await progress_callback(f"🔍 {i+1}/{len(symbols)}... кандидатов: {len(candidates)}")
        except Exception as e:
            logger.debug(f"scan {symbol}: {e}")

    filtered = filter_signals(candidates)
    best     = select_best_signal(filtered)
    if progress_callback:
        await progress_callback(f"✅ Готово. Просканировано: {scanned}. Прошли фильтр: {len(filtered)}.")
    return best, scanned


async def get_ai_comment(sig: Signal) -> str:
    try:
        import os, aiohttp
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            return ""
        direction_ru = "лонг" if sig.direction == "long" else "шорт"
        prompt = (
            f"Ты опытный трейдер. Дай краткий комментарий (1-2 предложения) к сигналу. "
            f"Конкретно, профессионально.\n"
            f"Сигнал: {sig.symbol} {direction_ru.upper()}, фаза: {sig.phase_title}\n"
            f"Зона входа ({sig.entry_zone_type} {sig.entry_zone_tf}): "
            f"{sig.entry_zone_bot}–{sig.entry_zone_top}\n"
            f"Вход лимит: {sig.entry_price}, Цель: {sig.target_price}, "
            f"Стоп: {sig.stop_price}, R:R: {sig.rr}\n"
            f"Обоснование: {sig.why}"
        )
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-sonnet-4-20250514", "max_tokens": 150,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["content"][0]["text"].strip()
    except Exception as e:
        logger.debug(f"ai_comment: {e}")
    return ""


def format_signal(sig: Signal, ai_comment: str = "") -> str:
    dir_icon  = "🟢" if sig.direction == "long" else "🔴"
    dir_ru    = "LONG  (покупка)" if sig.direction == "long" else "SHORT  (продажа)"
    score_bar = "█" * int(sig.score / 10 * 8) + "░" * (8 - int(sig.score / 10 * 8))

    entry_s   = _fmt_price(sig.entry_price, sig.symbol)
    target_s  = _fmt_price(sig.target_price, sig.symbol)
    stop_s    = _fmt_price(sig.stop_price, sig.symbol)
    current_s = _fmt_price(sig.current_price, sig.symbol)
    zt        = _fmt_price(sig.entry_zone_top, sig.symbol)
    zb        = _fmt_price(sig.entry_zone_bot, sig.symbol)

    if sig.entry_type == "limit":
        order_note = (
            f"📌 *Ордер: ЛИМИТНЫЙ — не рыночный!*\n"
            f"Сейчас цена `{current_s}`. Ставь лимит на `{entry_s}`\n"
            f"и жди возврата цены в зону."
        )
    else:
        order_note = (
            f"📌 *Ордер: ПО РЫНКУ* (цена уже в зоне)\n"
            f"Дождись подтверждения — закрытая свеча 1H в сторону сигнала."
        )

    rr_note = (
        f"На $1 риска — потенциал ${sig.rr} прибыли"
        if sig.rr >= 2 else f"R:R {sig.rr}"
    )

    lines = [
        f"🎯 *СИГНАЛ ДНЯ*",
        f"",
        f"{dir_icon} *{sig.symbol} — {dir_ru}*",
        f"Фаза рынка: _{sig.phase_title}_",
        f"",
        order_note,
        f"",
        f"*Зона входа ({sig.entry_zone_type} · {sig.entry_zone_tf}):*",
        f"от `{zb}` до `{zt}`",
        f"",
        f"*Лимитный ордер:* `{entry_s}`",
        f"*Стоп-лосс:*      `{stop_s}` _(-{sig.risk_pct}%)_ ← за блоком",
        f"*Тейк-профит:*    `{target_s}` _(+{sig.reward_pct}%)_",
        f"",
        f"*R:R {sig.rr}*  —  _{rr_note}_",
        f"",
        f"*Почему этот сигнал:*",
        f"_{sig.why}_",
        f"",
        f"*Когда ставить ордер:*",
        f"_{sig.condition}_",
    ]
    if ai_comment:
        lines += ["", f"💬 _{ai_comment}_"]
    lines += [
        f"",
        f"Качество сетапа: *{sig.score}/10*  `{score_bar}`",
        f"Стратегия: _{sig.strategy}_",
        f"",
        f"{'─' * 28}",
        f"_⚠️ Лимитный ордер. Стоп — обязателен. Размер позиции — не более 2% депо._",
    ]
    return "\n".join(lines)


def format_no_signal(scanned: int) -> str:
    return (
        f"🎯 *СИГНАЛ ДНЯ*\n\n"
        f"Просканировано: {scanned} инструментов Bitget\n\n"
        f"*Чистых сетапов сегодня нет.*\n"
        f"Все отсеяны по фильтрам:\n"
        f"  · R:R ≥ {MIN_RR_SCAN}\n"
        f"  · Стоп ≤ {MAX_STOP_PCT}% (за блоком)\n"
        f"  · Качество ≥ {MIN_SCORE_SCAN}/10\n\n"
        f"_Рынок неопределённый. Лучшая позиция — вне рынка._"
    )


# ── История
def _load_history() -> Dict:
    try:
        if SIGNALS_FILE.exists():
            return json.loads(SIGNALS_FILE.read_text())
    except Exception:
        pass
    return {}


def _has_active_duplicate(history: Dict, symbol: str, direction: str) -> bool:
    for s in history.values():
        if s.get("symbol") == symbol and s.get("direction") == direction and s.get("status") == "active":
            return True
    return False


def save_signals(signals: List[Signal]):
    try:
        h = _load_history()
        for sig in signals:
            if _has_active_duplicate(h, sig.symbol, sig.direction):
                continue
            h[sig.id] = asdict(sig)
        SIGNALS_FILE.write_text(json.dumps(h, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.error(f"save_signals: {e}")


def update_signal_status(symbol: str, current_price: float):
    try:
        h = _load_history()
        changed = False
        for sig_id, d in h.items():
            if d["symbol"] != symbol or d["status"] != "active":
                continue
            e, tgt, stp = d["entry_price"], d["target_price"], d["stop_price"]
            direction   = d["direction"]
            now         = datetime.now(TZ).isoformat()
            if direction == "long":
                if current_price >= tgt:
                    d.update(status="hit_target", closed_at=now, result_pct=round((tgt-e)/e*100,2)); changed=True
                elif current_price <= stp:
                    d.update(status="hit_stop",   closed_at=now, result_pct=round((stp-e)/e*100,2)); changed=True
            else:
                if current_price <= tgt:
                    d.update(status="hit_target", closed_at=now, result_pct=round((e-tgt)/e*100,2)); changed=True
                elif current_price >= stp:
                    d.update(status="hit_stop",   closed_at=now, result_pct=round((e-stp)/e*100,2)); changed=True
        if changed:
            SIGNALS_FILE.write_text(json.dumps(h, ensure_ascii=False, indent=2))
        return {k: v for k, v in h.items() if v["symbol"] == symbol and v["status"] != "active"}
    except Exception as e:
        logger.error(f"update_signal_status: {e}"); return {}


def get_stats_text() -> str:
    try:
        h = _load_history()
        if not h:
            return "📊 *Статистика*\n\nИстория пуста. Нажми *🎯 Сигнал дня*."
        all_s  = list(h.values())
        total  = len(all_s)
        active = [s for s in all_s if s["status"] == "active"]
        closed = [s for s in all_s if s["status"] != "active"]
        wins   = [s for s in closed if s["status"] == "hit_target"]
        losses = [s for s in closed if s["status"] == "hit_stop"]
        wr     = round(len(wins)/len(closed)*100) if closed else 0
        aw     = round(sum(s["result_pct"] for s in wins)/len(wins),2) if wins else 0
        al     = round(sum(s["result_pct"] for s in losses)/len(losses),2) if losses else 0
        ar     = round(sum(s["rr"] for s in closed)/len(closed),2) if closed else 0
        last5  = sorted(closed, key=lambda x: x.get("closed_at",""), reverse=True)[:5]
        rows   = [f"  {'✅' if s['status']=='hit_target' else '❌'} {s['symbol']} "
                  f"{s['direction'].upper()} {'+' if s['result_pct']>=0 else ''}{s['result_pct']}%"
                  for s in last5]
        lines  = [
            "📊 *Статистика сигналов*","",
            f"Всего: {total}  ·  Активных: {len(active)}  ·  Закрытых: {len(closed)}","",
            f"✅ В цель: {len(wins)}  ·  ❌ По стопу: {len(losses)}",
            f"Винрейт: *{wr}%*","",
            f"Средний профит: *+{aw}%*",
            f"Средний убыток: *{al}%*",
            f"Средний R:R: *{ar}*",
        ]
        if rows:
            lines += ["","*Последние закрытые:*"] + rows
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"get_stats_text: {e}"); return "📊 Ошибка статистики."


# Совместимость с bot.py
def get_best_signals(active_analyses, top_n=1, min_score=MIN_SCORE_SCAN):
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
            result.append(sig); seen.add(key)
        if len(result) >= top_n:
            break
    return result


def format_signals_block(signals, total_analyzed=0):
    if not signals:
        return format_no_signal(total_analyzed)
    from datetime import datetime
    import pytz
    now_str = datetime.now(pytz.timezone("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
    parts = [f"🎯 *СИГНАЛ ДНЯ*  ·  {now_str}",
             f"Проанализировано: {total_analyzed}", "─"*28, ""]
    for sig in signals[:2]:
        parts.append(format_signal(sig))
        parts.append("")
    return "\n".join(parts)
