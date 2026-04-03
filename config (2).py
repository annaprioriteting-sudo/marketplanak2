# ============================================================
# config.py — ОБНОВЛЁННАЯ КОНФИГУРАЦИЯ
# Инструменты: крипто фьючерсы Bitget + металлы + форекс
# ============================================================

import os

# ── Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN")
ADMIN_IDS      = [int(x) for x in os.getenv("ADMIN_IDS", "0").split(",") if x.strip().isdigit()]

# ── Anthropic (опционально — для AI-комментариев к сигналу)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── Таймзона
TIMEZONE = "Europe/Moscow"

# ════════════════════════════════════════════════════════════
# ИНСТРУМЕНТЫ ДЛЯ ОБЩЕГО ОТЧЁТА ("Отчёт" / Market Pulse)
# BTC + ETH + Золото + валютные пары
# ════════════════════════════════════════════════════════════

REPORT_SYMBOLS = [
    # Крипто — главные
    {"symbol": "BTCUSDT",  "asset_type": "crypto", "label": "Bitcoin"},
    {"symbol": "ETHUSDT",  "asset_type": "crypto", "label": "Ethereum"},
    {"symbol": "SOLUSDT",  "asset_type": "crypto", "label": "Solana"},
    {"symbol": "BNBUSDT",  "asset_type": "crypto", "label": "BNB"},
    {"symbol": "XRPUSDT",  "asset_type": "crypto", "label": "XRP"},
    # Металлы
    {"symbol": "XAUUSDT",  "asset_type": "metal",  "label": "Золото (XAUT)"},
    {"symbol": "XAUTUSDT", "asset_type": "metal",  "label": "Золото Bitget"},
    # Форекс / крипто-доллар прокси
    {"symbol": "XAGUSDT",  "asset_type": "metal",  "label": "Серебро"},
]

# ════════════════════════════════════════════════════════════
# ТОП-50 ФЬЮЧЕРСОВ BITGET ДЛЯ СКАНА "СИГНАЛ ДНЯ"
# ════════════════════════════════════════════════════════════

TOP_50_FUTURES = [
    # Tier 1 — мегакапы
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    # Tier 2 — крупные альты
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
    "LTCUSDT", "UNIUSDT", "ATOMUSDT", "NEARUSDT", "APTUSDT",
    # Tier 3 — DeFi + L2
    "ARBUSDT", "OPUSDT", "INJUSDT", "SUIUSDT", "SEIUSDT",
    "STXUSDT", "RUNEUSDT", "FETUSDT", "RENDERUSDT", "PENDLEUSDT",
    "AAVEUSDT", "MKRUSDT", "COMPUSDT", "CRVUSDT", "GMXUSDT",
    "DYDXUSDT", "TIAUSDT", "WLDUSDT", "JUPUSDT", "PYTHUSDT",
    # Tier 4 — AI + GameFi + Meme
    "EIGENUSDT", "ENAUSDT", "THETAUSDT", "SANDUSDT", "MANAUSDT",
    "GALAUSDT", "APEUSDT", "PEPEUSDT", "SHIBUSDT", "FLOKIUSDT",
    "BONKUSDT",  "WIFUSDT", "MEMEUSDT", "MOODENGUSDT", "MATICUSDT",
]

# ════════════════════════════════════════════════════════════
# ТАЙМФРЕЙМЫ ДЛЯ АНАЛИЗА
# ════════════════════════════════════════════════════════════

# Полный анализ (монета)
FULL_TIMEFRAMES = ["1W", "1D", "4H", "1H", "15m"]

# Быстрый скан (сигнал дня — 50 монет)
FAST_TIMEFRAMES = ["1D", "4H", "1H"]

# Минимум свечей по таймфрейму
MIN_BARS = {
    "1W": 15, "1wk": 15,
    "1D": 20, "1d": 20,
    "4H": 25, "4h": 25,
    "1H": 30, "1h": 30,
    "15m": 40,
}

# ════════════════════════════════════════════════════════════
# ПАРАМЕТРЫ СИГНАЛА ДНЯ
# ════════════════════════════════════════════════════════════

SIGNAL_MIN_RR    = 2.0   # минимальный R:R
SIGNAL_MAX_STOP  = 2.0   # макс. стоп в %
SIGNAL_MIN_SCORE = 5.0   # минимальный балл качества (из 10)

# ════════════════════════════════════════════════════════════
# АЛЕРТЫ
# ════════════════════════════════════════════════════════════

ALERT_DISTANCE_PCT = 0.3  # алерт когда цена в 0.3% от уровня
ALERT_CHECK_INTERVAL = 30  # секунд между проверками

# ════════════════════════════════════════════════════════════
# РАСПИСАНИЕ АВТОРАССЫЛОК
# ════════════════════════════════════════════════════════════

MORNING_HOUR   = 8   # утренний отчёт
MORNING_MINUTE = 0
EVENING_HOUR   = 20  # вечерний отчёт
EVENING_MINUTE = 0

# ════════════════════════════════════════════════════════════
# ДОСТУП
# ════════════════════════════════════════════════════════════

ALLOWED_USERS_FILE = "/root/bots/market_analyst_bot/allowed_users.json"
SIGNALS_HISTORY_FILE = "/root/bots/market_analyst_bot/signals_history.json"
