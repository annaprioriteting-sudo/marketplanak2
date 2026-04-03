# ============================================================
#  news_fetcher.py  v2
#
#  RSS новости + sentiment вывод: risk-on / risk-off / neutral
#  Интегрируется в: утренний отчёт, вечерний итог, сигнал дня
# ============================================================

import logging
import asyncio
import re
from datetime import datetime, timezone, timedelta
from typing import List, Tuple
from email.utils import parsedate_to_datetime

import requests

logger = logging.getLogger(__name__)

RSS_SOURCES = [
    ("CoinDesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt",       "https://decrypt.co/feed"),
]

TIMEOUT  = 6
MAX_NEWS = 3


# ── Парсинг ───────────────────────────────────────────────

def _fetch_rss(url: str) -> List[Tuple[str, str, datetime]]:
    try:
        resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "MarketPulseBot/1.0"})
        if resp.status_code != 200:
            return []
        text  = resp.text
        items = []
        for item_match in re.finditer(r"<item>(.*?)</item>", text, re.DOTALL):
            item_text = item_match.group(1)
            title = _extract_tag(item_text, "title")
            link  = _extract_tag(item_text, "link")
            pub   = _extract_tag(item_text, "pubDate")
            if not title or not link:
                continue
            try:
                pub_dt = parsedate_to_datetime(pub) if pub else datetime.now(timezone.utc)
            except Exception:
                pub_dt = datetime.now(timezone.utc)
            items.append((_clean(title), _clean(link), pub_dt))
        return items
    except Exception as e:
        logger.debug(f"RSS {url}: {e}")
        return []


def _extract_tag(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


# ── Ключевые слова ────────────────────────────────────────

RELEVANT = [
    "bitcoin", "btc", "ethereum", "eth", "crypto", "defi",
    "fed", "interest rate", "sec", "regulation", "etf", "halving",
    "market", "bull", "bear", "liquidation", "solana", "sol",
    "bnb", "xrp", "ripple", "tether", "usdt", "stablecoin",
    "inflation", "cpi", "fomc", "rate cut", "rate hike",
]

# Слова, повышающие risk-on
RISK_ON_WORDS = [
    "rally", "surge", "jump", "bull", "approval", "etf approved",
    "halving", "institutional", "buy", "accumulate", "all-time high",
    "recovery", "green", "upside",
]

# Слова, повышающие risk-off
RISK_OFF_WORDS = [
    "crash", "dump", "ban", "regulation", "sec charges", "hack",
    "liquidation", "bear", "sell-off", "collapse", "warning",
    "fear", "red", "downside", "shutdown", "investigation",
]


def _is_relevant(title: str) -> bool:
    title_l = title.lower()
    return any(kw in title_l for kw in RELEVANT)


def _calc_sentiment(titles: List[str]) -> str:
    """Определяет risk-on / risk-off / neutral по заголовкам."""
    risk_on = risk_off = 0
    for title in titles:
        t = title.lower()
        risk_on  += sum(1 for w in RISK_ON_WORDS  if w in t)
        risk_off += sum(1 for w in RISK_OFF_WORDS if w in t)

    if risk_on > risk_off + 1:
        return "🟢 Risk-on"
    elif risk_off > risk_on + 1:
        return "🔴 Risk-off"
    else:
        return "⚪ Neutral"


# ── Публичная функция ─────────────────────────────────────

async def fetch_news(max_items: int = MAX_NEWS) -> str:
    """
    Загружает новости и возвращает форматированный блок:
    - 2-3 релевантных заголовка
    - sentiment: risk-on / risk-off / neutral

    Возвращает "" если новости недоступны.
    """
    loop = asyncio.get_event_loop()
    all_items: List[Tuple[str, str, datetime, str]] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    for source_name, url in RSS_SOURCES:
        try:
            items = await loop.run_in_executor(None, _fetch_rss, url)
            for title, link, pub_dt in items:
                # Только свежие (последние 24ч)
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                if pub_dt >= cutoff and _is_relevant(title):
                    all_items.append((title, link, pub_dt, source_name))
        except Exception as e:
            logger.debug(f"news {source_name}: {e}")
        await asyncio.sleep(0.1)

    if not all_items:
        return ""

    # Сортируем по свежести
    all_items.sort(key=lambda x: x[2], reverse=True)

    # Дедупликация
    unique: List[Tuple] = []
    seen_words: set = set()
    for item in all_items:
        words = set(item[0].lower().split()[:5])
        if not words & seen_words:
            unique.append(item)
            seen_words.update(words)
        if len(unique) >= max_items:
            break

    if not unique:
        return ""

    titles    = [item[0] for item in unique]
    sentiment = _calc_sentiment(titles)

    lines = ["📰 *Новости:*"]
    for title, link, pub_dt, source in unique:
        short = title[:85] + "…" if len(title) > 85 else title
        lines.append(f"— {short} _({source})_")

    lines.append(f"Рынок: {sentiment}")
    return "\n".join(lines)
