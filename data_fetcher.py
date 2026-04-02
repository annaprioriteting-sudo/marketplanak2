# ============================================================
#  data_fetcher.py — Получение рыночных данных
#  Bitget (крипто-фьючерсы) + yFinance (форекс, металлы)
#
#  Исправления v2:
#  - yFinance: нормализация колонок (capital → lower)
#  - yFinance: обработка MultiIndex колонок (новые версии)
#  - yFinance: 4H через ресемплинг 1H с правильными именами
#  - yFinance: fallback period если 60d недоступен
#  - yFinance: volume=0 для форекс — не считается ошибкой
#  - Защита от None и пустых DataFrame на каждом шаге
# ============================================================

import time
import hmac
import hashlib
import base64
import logging
from typing import Dict, List, Optional

import requests
import pandas as pd
import yfinance as yf

from config import (
    BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE,
    CRYPTO_FIXED_SYMBOLS, CRYPTO_TOP_N_BY_VOLUME,
    FOREX_METALS_SYMBOLS, TIMEFRAMES_CRYPTO, TIMEFRAMES_FOREX,
)

logger = logging.getLogger(__name__)

BITGET_BASE_URL = "https://api.bitget.com"

OHLCV_COLS = ["open", "high", "low", "close", "volume"]


# ════════════════════════════════════════════════════════════
#  УТИЛИТЫ
# ════════════════════════════════════════════════════════════

def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Приводит DataFrame к единому виду: lowercase колонки,
    только OHLCV, числовые типы, без NaN в OHLC.
    Работает с обычным Index и MultiIndex (новый yfinance).
    """
    if df is None or df.empty:
        return pd.DataFrame()

    # MultiIndex колонок (yfinance >= 0.2.38 иногда возвращает)
    if isinstance(df.columns, pd.MultiIndex):
        df = df.droplevel(level=1, axis=1)

    # Нормализуем имена
    df = df.copy()
    df.columns = [str(c).lower().strip() for c in df.columns]

    # Проверяем наличие нужных колонок
    missing = [c for c in ["open", "high", "low", "close"] if c not in df.columns]
    if missing:
        logger.debug(f"Отсутствуют колонки: {missing}. Доступны: {list(df.columns)}")
        return pd.DataFrame()

    # volume необязателен для форекс (ставим 0)
    if "volume" not in df.columns:
        df["volume"] = 0.0

    # Числовые типы
    for col in OHLCV_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Убираем строки где нет OHLC
    df = df.dropna(subset=["open", "high", "low", "close"])

    return df[OHLCV_COLS]


# ════════════════════════════════════════════════════════════
#  BITGET — подпись запросов
# ════════════════════════════════════════════════════════════

def _bitget_sign(timestamp: str, method: str, path: str, body: str = "") -> str:
    message = f"{timestamp}{method.upper()}{path}{body}"
    mac = hmac.new(
        BITGET_SECRET_KEY.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    )
    return base64.b64encode(mac.digest()).decode()


def _bitget_headers(method: str, path: str, body: str = "") -> dict:
    ts = str(int(time.time() * 1000))
    return {
        "ACCESS-KEY":        BITGET_API_KEY,
        "ACCESS-SIGN":       _bitget_sign(ts, method, path, body),
        "ACCESS-TIMESTAMP":  ts,
        "ACCESS-PASSPHRASE": BITGET_PASSPHRASE,
        "Content-Type":      "application/json",
    }


def _bitget_get(path: str, params: dict = None) -> dict:
    url = BITGET_BASE_URL + path
    headers = _bitget_headers("GET", path)
    resp = requests.get(url, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


# ════════════════════════════════════════════════════════════
#  BITGET — топ-N монет по объёму
# ════════════════════════════════════════════════════════════

def get_top_futures_by_volume(n: int = 5, exclude: List[str] = None) -> List[str]:
    exclude = exclude or []
    try:
        data = _bitget_get("/api/v2/mix/market/tickers", {"productType": "USDT-FUTURES"})
        tickers = data.get("data", [])
        sorted_t = sorted(tickers, key=lambda x: float(x.get("usdtVolume", 0)), reverse=True)
        result = []
        for t in sorted_t:
            sym = t["symbol"].replace("_UMCBL", "")
            if sym not in exclude and sym not in result:
                result.append(sym)
            if len(result) >= n:
                break
        return result
    except Exception as e:
        logger.error(f"Ошибка топ фьючерсов: {e}")
        return []


def get_all_crypto_symbols() -> List[str]:
    top = get_top_futures_by_volume(n=CRYPTO_TOP_N_BY_VOLUME, exclude=CRYPTO_FIXED_SYMBOLS)
    return CRYPTO_FIXED_SYMBOLS + top


# ════════════════════════════════════════════════════════════
#  BITGET — OHLCV
# ════════════════════════════════════════════════════════════

_BITGET_GRANULARITY = {
    "15m": "15m",
    "1H":  "1H",
    "4H":  "4H",
    "1D":  "1Dutc",
    "1W":  "1Wutc",
}


def fetch_bitget_ohlcv(symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
    gran = _BITGET_GRANULARITY.get(timeframe, timeframe)
    path = "/api/v2/mix/market/candles"
    params = {
        "symbol":      symbol,
        "productType": "USDT-FUTURES",
        "granularity": gran,
        "limit":       str(limit),
    }
    try:
        data = _bitget_get(path, params)
        candles = data.get("data", [])
        if not candles:
            return pd.DataFrame()
        df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume", "notional"])
        df["ts"] = pd.to_datetime(df["ts"].astype(float), unit="ms", utc=True)
        df.set_index("ts", inplace=True)
        df.sort_index(inplace=True)
        return _normalize_df(df)
    except Exception as e:
        logger.error(f"Ошибка Bitget OHLCV {symbol} {timeframe}: {e}")
        return pd.DataFrame()


def fetch_bitget_all_timeframes(symbol: str) -> Dict[str, pd.DataFrame]:
    result = {}
    for tf_key in TIMEFRAMES_CRYPTO:
        df = fetch_bitget_ohlcv(symbol, tf_key)
        if not df.empty:
            result[tf_key] = df
        time.sleep(0.15)
    return result


# ════════════════════════════════════════════════════════════
#  YFINANCE — форекс и металлы
#
#  Проблемы yFinance которые здесь решаются:
#  1. Колонки с заглавной: Open/High/Low/Close/Volume → lower
#  2. MultiIndex при запросе нескольких тикеров → droplevel
#  3. 4H не поддерживается → ресемплинг из 1H
#  4. 15m недоступно для старых дат → period="7d" fallback
#  5. Форекс даёт volume=0 — это нормально, не ошибка
#  6. Иногда возвращает Adj Close вместо Close — обрабатываем
# ════════════════════════════════════════════════════════════

# interval → (yf_interval, period_primary, period_fallback)
_YF_CONFIG = {
    "1wk": ("1wk", "5y",  "2y"),
    "1d":  ("1d",  "2y",  "1y"),
    "4h":  None,              # строим из 1h
    "1h":  ("1h",  "60d", "30d"),
    "15m": ("15m", "60d", "7d"),
}


def _fetch_yf_raw(ticker_str: str, interval: str, period: str) -> pd.DataFrame:
    """Одна попытка загрузки из yFinance с нормализацией."""
    try:
        t = yf.Ticker(ticker_str)
        df = t.history(period=period, interval=interval, auto_adjust=True)
        return _normalize_df(df)
    except Exception as e:
        logger.debug(f"yFinance {ticker_str} {interval} {period}: {e}")
        return pd.DataFrame()


def _resample_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """1H → 4H через ресемплинг. Работает с lowercase колонками."""
    if df_1h.empty:
        return pd.DataFrame()
    try:
        # Убеждаемся что индекс — DatetimeIndex
        if not isinstance(df_1h.index, pd.DatetimeIndex):
            return pd.DataFrame()

        df = df_1h.resample("4h", label="left", closed="left").agg({
            "open":   "first",
            "high":   "max",
            "low":    "min",
            "close":  "last",
            "volume": "sum",
        }).dropna(subset=["open", "high", "low", "close"])

        return df[OHLCV_COLS]
    except Exception as e:
        logger.error(f"Ошибка ресемплинга 4H: {e}")
        return pd.DataFrame()


def fetch_yfinance_ohlcv(ticker_str: str, timeframe: str) -> pd.DataFrame:
    """
    Загружает OHLCV для форекс/металл тикера Yahoo Finance.
    timeframe: "1wk", "1d", "4h", "1h", "15m"
    Возвращает DataFrame с lowercase колонками open/high/low/close/volume.
    """
    # 4H — особый случай
    if timeframe == "4h":
        df_1h = fetch_yfinance_ohlcv(ticker_str, "1h")
        return _resample_to_4h(df_1h)

    cfg = _YF_CONFIG.get(timeframe)
    if cfg is None:
        logger.warning(f"Неизвестный таймфрейм yFinance: {timeframe}")
        return pd.DataFrame()

    yf_interval, period_main, period_fallback = cfg

    # Основная попытка
    df = _fetch_yf_raw(ticker_str, yf_interval, period_main)
    if not df.empty:
        return df

    # Fallback с меньшим периодом
    logger.debug(f"yFinance {ticker_str} {timeframe}: fallback на {period_fallback}")
    df = _fetch_yf_raw(ticker_str, yf_interval, period_fallback)
    if not df.empty:
        return df

    logger.warning(f"yFinance {ticker_str} {timeframe}: нет данных ни в одной попытке")
    return pd.DataFrame()


def fetch_yfinance_all_timeframes(symbol_name: str) -> Dict[str, pd.DataFrame]:
    """
    Загружает все таймфреймы для форекс/металл символа.
    symbol_name: "EURUSD", "XAUUSD", etc. (ключ из FOREX_METALS_SYMBOLS)
    """
    ticker_yf = FOREX_METALS_SYMBOLS.get(symbol_name)
    if not ticker_yf:
        logger.error(f"Тикер не найден для {symbol_name}")
        return {}

    result = {}
    loaded = []
    failed = []

    for tf_key in TIMEFRAMES_FOREX:
        df = fetch_yfinance_ohlcv(ticker_yf, tf_key)
        if not df.empty and len(df) >= 10:  # минимум 10 свечей
            result[tf_key] = df
            loaded.append(f"{tf_key}({len(df)})")
        else:
            failed.append(tf_key)
        time.sleep(0.3)  # уважаем rate limit Yahoo

    if loaded:
        logger.info(f"{symbol_name}: загружено {loaded}")
    if failed:
        logger.warning(f"{symbol_name}: не загружено {failed}")

    return result


# ════════════════════════════════════════════════════════════
#  Текущие цены (для алертов)
# ════════════════════════════════════════════════════════════

def get_current_price_crypto(symbol: str) -> Optional[float]:
    try:
        data = _bitget_get("/api/v2/mix/market/ticker", {
            "symbol": symbol, "productType": "USDT-FUTURES"
        })
        return float(data["data"]["lastPr"])
    except Exception as e:
        logger.error(f"Цена Bitget {symbol}: {e}")
        return None


def get_current_price_forex(symbol_name: str) -> Optional[float]:
    """Текущая цена через последнюю 1-минутную свечу yFinance."""
    try:
        ticker_yf = FOREX_METALS_SYMBOLS.get(symbol_name, symbol_name)
        t = yf.Ticker(ticker_yf)
        # Пробуем 1m, fallback на 5m
        for interval in ("1m", "5m", "15m"):
            df = t.history(period="1d", interval=interval, auto_adjust=True)
            df = _normalize_df(df)
            if not df.empty:
                return float(df["close"].iloc[-1])
        return None
    except Exception as e:
        logger.error(f"Цена yFinance {symbol_name}: {e}")
        return None
