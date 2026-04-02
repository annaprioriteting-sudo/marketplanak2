# ============================================================
#  scheduler.py — Планировщик задач
#  Утренний отчёт, вечерний итог, цикл алертов
# ============================================================

import asyncio
import logging
from datetime import datetime, time as dtime
import pytz

from config import (
    MORNING_REPORT_HOUR, MORNING_REPORT_MINUTE,
    EVENING_REPORT_HOUR, EVENING_REPORT_MINUTE,
    ALERT_CHECK_INTERVAL_MINUTES,
    CRYPTO_FIXED_SYMBOLS, FOREX_METALS_SYMBOLS,
)

logger = logging.getLogger(__name__)

# Часовой пояс (можно изменить в config)
TIMEZONE = pytz.timezone("Europe/Moscow")  # UTC+3


def seconds_until(target_hour: int, target_minute: int) -> float:
    """Секунд до следующего запуска в заданное время."""
    now = datetime.now(TIMEZONE)
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    if target <= now:
        # Уже прошло — следующий запуск завтра
        from datetime import timedelta
        target += timedelta(days=1)
    return (target - now).total_seconds()


# ════════════════════════════════════════════════════════════
#  Задачи планировщика
# ════════════════════════════════════════════════════════════

async def run_morning_report(bot_instance):
    """Запускает полный сбор данных и отправку утреннего отчёта."""
    logger.info("⏰ Запуск утреннего отчёта...")
    try:
        from data_fetcher import (
            get_all_crypto_symbols,
            fetch_bitget_all_timeframes,
            fetch_yfinance_all_timeframes,
        )
        from analyzer import full_analysis
        from report_generator import build_morning_message

        loop = asyncio.get_event_loop()
        analyses = []

        # ── Крипто ───────────────────────────────────────
        crypto_symbols = await loop.run_in_executor(None, get_all_crypto_symbols)
        logger.info(f"Крипто для анализа: {crypto_symbols}")

        for symbol in crypto_symbols:
            try:
                tf_data = await loop.run_in_executor(None, fetch_bitget_all_timeframes, symbol)
                fa = await loop.run_in_executor(None, full_analysis, symbol, "crypto", tf_data)
                analyses.append(fa)
            except Exception as e:
                logger.error(f"Ошибка анализа крипто {symbol}: {e}")

        # ── Форекс и металлы ─────────────────────────────
        for symbol_name in FOREX_METALS_SYMBOLS.keys():
            try:
                tf_data = await loop.run_in_executor(None, fetch_yfinance_all_timeframes, symbol_name)
                asset_type = "metal" if symbol_name.startswith("XA") else "forex"
                fa = await loop.run_in_executor(None, full_analysis, symbol_name, asset_type, tf_data)
                analyses.append(fa)
            except Exception as e:
                logger.error(f"Ошибка анализа forex/metal {symbol_name}: {e}")

        # ── Сохраняем анализы для алертов ────────────────
        bot_instance.active_analyses = {fa.symbol: fa for fa in analyses}

        # ── Отправка отчётов ─────────────────────────────
        messages = await loop.run_in_executor(None, build_morning_message, analyses)
        for msg in messages:
            await bot_instance.send_to_all(msg)
            await asyncio.sleep(0.5)

        logger.info("✅ Утренний отчёт отправлен.")

    except Exception as e:
        logger.error(f"Критическая ошибка утреннего отчёта: {e}")
        await bot_instance.send_to_all(f"❌ Ошибка формирования утреннего отчёта: {e}")


async def run_evening_report(bot_instance):
    """Вечерний итог."""
    logger.info("🌙 Запуск вечернего итога...")
    try:
        from report_generator import build_evening_message
        import asyncio

        if not bot_instance.active_analyses:
            await bot_instance.send_to_all("⚠️ Нет данных для вечернего итога. Запусти /report")
            return

        loop = asyncio.get_event_loop()
        analyses = list(bot_instance.active_analyses.values())
        messages = await loop.run_in_executor(None, build_evening_message, analyses)
        for msg in messages:
            await bot_instance.send_to_all(msg)
            await asyncio.sleep(0.5)

        logger.info("✅ Вечерний итог отправлен.")
    except Exception as e:
        logger.error(f"Ошибка вечернего итога: {e}")


async def alert_loop(bot_instance):
    """Бесконечный цикл проверки алертов."""
    from alert_monitor import monitor_alerts
    from report_generator import build_alert_message

    async def send_alert(fa, level, distance_pct):
        text = build_alert_message(fa, level, distance_pct)
        await bot_instance.send_to_all(text)

    while True:
        if bot_instance.active_analyses:
            await monitor_alerts(bot_instance.active_analyses, send_alert)
        await asyncio.sleep(ALERT_CHECK_INTERVAL_MINUTES * 60)


# ════════════════════════════════════════════════════════════
#  Главный планировщик
# ════════════════════════════════════════════════════════════

async def start_scheduler(bot_instance):
    """Запускает все задачи планировщика."""
    logger.info("🗓️ Планировщик запущен.")
    logger.info(f"Утренний отчёт: {MORNING_REPORT_HOUR:02d}:{MORNING_REPORT_MINUTE:02d}")
    logger.info(f"Вечерний итог: {EVENING_REPORT_HOUR:02d}:{EVENING_REPORT_MINUTE:02d}")
    logger.info(f"Проверка алертов каждые {ALERT_CHECK_INTERVAL_MINUTES} минут")

    # Запускаем цикл алертов как фоновую задачу
    asyncio.create_task(alert_loop(bot_instance))

    while True:
        now = datetime.now(TIMEZONE)

        # Утренний отчёт
        secs_morning = seconds_until(MORNING_REPORT_HOUR, MORNING_REPORT_MINUTE)
        # Вечерний итог
        secs_evening = seconds_until(EVENING_REPORT_HOUR, EVENING_REPORT_MINUTE)

        next_task = min(secs_morning, secs_evening)
        logger.info(f"Следующая задача через {next_task/60:.1f} минут")
        await asyncio.sleep(next_task)

        now = datetime.now(TIMEZONE)
        if now.hour == MORNING_REPORT_HOUR and now.minute == MORNING_REPORT_MINUTE:
            await run_morning_report(bot_instance)
        elif now.hour == EVENING_REPORT_HOUR and now.minute == EVENING_REPORT_MINUTE:
            await run_evening_report(bot_instance)

        # Небольшая пауза чтобы не запустить дважды
        await asyncio.sleep(65)
