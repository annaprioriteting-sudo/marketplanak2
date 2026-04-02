# ============================================================
#  scheduler.py — Планировщик задач
#  Утренний отчёт, вечерний итог, цикл алертов
#  ТОЛЬКО КРИПТО — форекс убран
# ============================================================

import asyncio
import logging
from datetime import datetime
import pytz

from config import (
    MORNING_REPORT_HOUR, MORNING_REPORT_MINUTE,
    EVENING_REPORT_HOUR, EVENING_REPORT_MINUTE,
    ALERT_CHECK_INTERVAL_MINUTES,
    CRYPTO_SYMBOLS_ONLY,
)

logger = logging.getLogger(__name__)
TIMEZONE = pytz.timezone("Europe/Moscow")


def seconds_until(target_hour: int, target_minute: int) -> float:
    from datetime import timedelta
    now = datetime.now(TIMEZONE)
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


# ════════════════════════════════════════════════════════════
#  Утренний отчёт
# ════════════════════════════════════════════════════════════

async def run_morning_report(bot_instance):
    logger.info("⏰ Запуск утреннего отчёта...")
    try:
        from data_fetcher import get_all_crypto_symbols, fetch_bitget_all_timeframes
        from analyzer import full_analysis
        from report_generator import build_morning_message

        loop = asyncio.get_event_loop()
        analyses = []

        crypto_symbols = await loop.run_in_executor(None, get_all_crypto_symbols)
        logger.info(f"Крипто для анализа: {crypto_symbols}")

        for symbol in crypto_symbols:
            try:
                tf_data = await loop.run_in_executor(None, fetch_bitget_all_timeframes, symbol)
                if not tf_data:
                    logger.warning(f"{symbol}: нет данных")
                    continue
                fa = await loop.run_in_executor(None, full_analysis, symbol, "crypto", tf_data)
                analyses.append(fa)
            except Exception as e:
                logger.error(f"Ошибка анализа {symbol}: {e}")

        if not analyses:
            await bot_instance.send_to_all("⚠️ Не удалось загрузить данные ни по одному инструменту.")
            return

        # ── Сохраняем в общий словарь через proxy ──────────
        bot_instance.active_analyses = {fa.symbol: fa for fa in analyses}
        logger.info(f"Загружено анализов: {len(analyses)}")

        # ── Отправляем отчёты ──────────────────────────────
        messages = await loop.run_in_executor(None, build_morning_message, analyses)
        for msg in messages:
            await bot_instance.send_to_all(msg)
            await asyncio.sleep(0.5)

        logger.info("✅ Утренний отчёт отправлен.")

    except Exception as e:
        logger.error(f"Критическая ошибка утреннего отчёта: {e}")
        await bot_instance.send_to_all(f"❌ Ошибка формирования отчёта: {e}")


# ════════════════════════════════════════════════════════════
#  Вечерний итог
# ════════════════════════════════════════════════════════════

async def run_evening_report(bot_instance):
    logger.info("🌙 Запуск вечернего итога...")
    try:
        from report_generator import build_evening_message

        if not bot_instance.active_analyses:
            await bot_instance.send_to_all("⚠️ Нет данных для вечернего итога. Запусти 📊 Отчёт")
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


# ════════════════════════════════════════════════════════════
#  Цикл алертов
# ════════════════════════════════════════════════════════════

async def alert_loop(bot_instance):
    from alert_monitor import monitor_alerts
    from report_generator import build_alert_message

    async def send_alert(fa, level, distance_pct):
        text = build_alert_message(fa, level, distance_pct)
        await bot_instance.send_to_all(text)

    logger.info("🔔 Цикл алертов запущен")
    while True:
        try:
            analyses = bot_instance.active_analyses
            if analyses:
                logger.info(f"Проверка алертов для {len(analyses)} инструментов")
                await monitor_alerts(analyses, send_alert)
            else:
                logger.debug("Алерты: нет загруженных анализов, пропускаем")
        except Exception as e:
            logger.error(f"Ошибка в alert_loop: {e}")
        await asyncio.sleep(ALERT_CHECK_INTERVAL_MINUTES * 60)


# ════════════════════════════════════════════════════════════
#  Главный планировщик
# ════════════════════════════════════════════════════════════

async def start_scheduler(bot_instance):
    logger.info("🗓️ Планировщик запущен.")
    logger.info(f"Утренний отчёт: {MORNING_REPORT_HOUR:02d}:{MORNING_REPORT_MINUTE:02d}")
    logger.info(f"Вечерний итог: {EVENING_REPORT_HOUR:02d}:{EVENING_REPORT_MINUTE:02d}")
    logger.info(f"Проверка алертов каждые {ALERT_CHECK_INTERVAL_MINUTES} минут")

    # Запускаем цикл алертов как фоновую задачу
    asyncio.create_task(alert_loop(bot_instance))

    while True:
        secs_morning = seconds_until(MORNING_REPORT_HOUR, MORNING_REPORT_MINUTE)
        secs_evening = seconds_until(EVENING_REPORT_HOUR, EVENING_REPORT_MINUTE)

        next_task = min(secs_morning, secs_evening)
        logger.info(f"Следующая задача через {next_task/60:.1f} минут")
        await asyncio.sleep(next_task)

        now = datetime.now(TIMEZONE)
        if now.hour == MORNING_REPORT_HOUR and now.minute == MORNING_REPORT_MINUTE:
            await run_morning_report(bot_instance)
        elif now.hour == EVENING_REPORT_HOUR and now.minute == EVENING_REPORT_MINUTE:
            await run_evening_report(bot_instance)

        await asyncio.sleep(65)
