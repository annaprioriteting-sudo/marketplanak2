# ============================================================
#  config.py — Настройки бота (заполни своими ключами)
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

# ── Claude API ────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "YOUR_CLAUDE_KEY")
CLAUDE_MODEL      = "claude-opus-4-5"

# ── Расписание (UTC+3 = московское время) ─────────────────────
MORNING_REPORT_HOUR   = 6
MORNING_REPORT_MINUTE = 0
EVENING_REPORT_HOUR   = 21
EVENING_REPORT_MINUTE = 0
ALERT_CHECK_INTERVAL_MINUTES = 5

# ════════════════════════════════════════════════════════════
#  МОНЕТИЗАЦИЯ
# ════════════════════════════════════════════════════════════

# Администраторы (могут добавлять/удалять платных пользователей)
# Заполни своим Telegram ID (узнать: @userinfobot)
ADMIN_IDS: List[int] = [
    int(x) for x in os.getenv("ADMIN_IDS", "123456789").split(",")
]

# Платные пользователи — можно задать прямо здесь (статический список)
# или управлять через /adduser команду (они хранятся в access_state.json)
PAID_USERS_STATIC: List[int] = [
    int(x) for x in os.getenv("PAID_USERS", "").split(",") if x.strip()
]

# Пробный период для новых пользователей (дней)
TRIAL_DAYS = 3

# Сколько сигналов в день для бесплатных пользователей
# Показываем направление (LONG/SHORT) но скрываем уровни
FREE_DAILY_SIGNALS = 1

# Файл хранилища состояния доступа
ACCESS_FILE = "access_state.json"

# Текст для экрана оплаты (/upgrade)
# Измени ссылку на свою реальную страницу оплаты
PAYMENT_INFO = """
💳 *Подписка Market Pulse*

*Что входит в подписку:*
✅ Полные сигналы с точками входа, стопом и целью
✅ Утренний брифинг 06:00 — полный разбор рынка
✅ Алерты в реальном времени при подходе цены к уровню
✅ Вечерний итог 21:00
✅ Анализ по запросу — крипто, форекс, металлы

*Стоимость:*
🔹 Первый месяц — *19$*
🔹 Далее — *39$ / месяц*

*Оплата:*
👉 Написать для оплаты: @ВАШ_USERNAME

_После оплаты доступ открывается в течение 15 минут._
"""

# ── Инструменты ───────────────────────────────────────────────
CRYPTO_FIXED_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
CRYPTO_TOP_N_BY_VOLUME = 5

FOREX_METALS_SYMBOLS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F",
    "XAGUSD": "SI=F",
}

# ── Таймфреймы ────────────────────────────────────────────────
TIMEFRAMES_CRYPTO = {
    "1W":  "1W",
    "1D":  "1D",
    "4H":  "4H",
    "1H":  "1H",
    "15m": "15m",
}

TIMEFRAMES_FOREX = {
    "1wk": "1wk",
    "1d":  "1d",
    "4h":  "4h",
    "1h":  "1h",
    "15m": "15m",
}

# ── Алерты ────────────────────────────────────────────────────
ALERT_THRESHOLD_DEFAULT = 0.005
ALERT_THRESHOLD_BY_SYMBOL = {
    "BTCUSDT": 0.003,
    "XAUUSD":  0.003,
    "EURUSD":  0.0015,
    "GBPUSD":  0.0015,
    "USDJPY":  0.0015,
}
ALERT_COOLDOWN_MINUTES = 60
STATE_FILE = "alert_state.json"

# ── Логирование ───────────────────────────────────────────────
LOG_LEVEL = "INFO"
LOG_FILE  = "bot.log"
