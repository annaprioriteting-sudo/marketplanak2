# ============================================================
#  config.py — Настройки бота
#  ТОЛЬКО КРИПТО — форекс убран
# ============================================================

import os
from typing import List

# ── Telegram ─────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")
ALLOWED_CHAT_IDS: List[int] = [
    int(x) for x in os.getenv("ALLOWED_CHAT_IDS", "123456789").split(",")
]

# ── Bitget API ────────────────────────────────────────────────
BITGET_API_KEY    = os.getenv("BITGET_API_KEY", "")
BITGET_SECRET_KEY = os.getenv("BITGET_SECRET_KEY", "")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")

# ── Расписание (UTC+3 = московское время) ─────────────────────
MORNING_REPORT_HOUR   = 6
MORNING_REPORT_MINUTE = 0
EVENING_REPORT_HOUR   = 21
EVENING_REPORT_MINUTE = 0
ALERT_CHECK_INTERVAL_MINUTES = 5

# ════════════════════════════════════════════════════════════
#  МОНЕТИЗАЦИЯ
# ════════════════════════════════════════════════════════════

ADMIN_IDS: List[int] = [
    int(x) for x in os.getenv("ADMIN_IDS", "123456789").split(",")
]

PAID_USERS_STATIC: List[int] = [
    int(x) for x in os.getenv("PAID_USERS", "").split(",") if x.strip()
]

TRIAL_DAYS = 3
FREE_DAILY_SIGNALS = 1
ACCESS_FILE = "access_state.json"

PAYMENT_INFO = """
💳 *Подписка Market Pulse*

*Что входит в подписку:*
✅ Полные сигналы с точками входа, стопом и целью
✅ Утренний брифинг 06:00 — полный разбор рынка
✅ Алерты в реальном времени при подходе цены к уровню
✅ Вечерний итог 21:00
✅ Анализ по запросу — все крипто-фьючерсы

*Стоимость:*
🔹 Первый месяц — *19$*
🔹 Далее — *39$ / месяц*

*Оплата:*
👉 Написать для оплаты: @ВАШ_USERNAME

_После оплаты доступ открывается в течение 15 минут._
"""

# ── Инструменты (ТОЛЬКО КРИПТО) ───────────────────────────────
CRYPTO_FIXED_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
CRYPTO_TOP_N_BY_VOLUME = 5

# Алиас для совместимости с bot.py и scheduler.py
CRYPTO_SYMBOLS_ONLY = CRYPTO_FIXED_SYMBOLS

# Форекс УБРАН — оставляем пустым для совместимости
FOREX_METALS_SYMBOLS: dict = {}

# ── Таймфреймы ────────────────────────────────────────────────
TIMEFRAMES_CRYPTO = {
    "1W":  "1W",
    "1D":  "1D",
    "4H":  "4H",
    "1H":  "1H",
    "15m": "15m",
}

# Форекс-таймфреймы оставляем для совместимости импортов
TIMEFRAMES_FOREX = {}

# ── Алерты ────────────────────────────────────────────────────
ALERT_THRESHOLD_DEFAULT = 0.005
ALERT_THRESHOLD_BY_SYMBOL = {
    "BTCUSDT": 0.003,
    "ETHUSDT": 0.004,
    "SOLUSDT": 0.004,
}
ALERT_COOLDOWN_MINUTES = 60
STATE_FILE = "alert_state.json"

# ── Логирование ───────────────────────────────────────────────
LOG_LEVEL = "INFO"
LOG_FILE  = "bot.log"
