# ============================================================
#  news_fetcher.py — Крипто новости из RSS
#  Источники: CoinDesk, CoinTelegraph, Decrypt
#  Лёгкий, без внешних зависимостей кроме requests + xml
# ============================================================

import logging
import asyncio
import re
from datetime import datetime, timezone
from typing import List, Tuple
from email.utils import parsedate_to_datetime

import requests

logger = logging.getLogger(__name__)

RSS_SOURCES = [
    ("CoinDesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt",       "https://decrypt.co/feed"),
]

TIMEOUT  = 6   # сек на источник
MAX_NEWS = 3   # сколько новостей показывать


# ════════════════════════════════════════════════════════════
#  Парсинг RSS
# ════════════════════════════════════════════════════════════

def _fetch_rss(url: str) -> List[Tuple[str, str, datetime]]:
    """
    Возвращает список (title, link, pub_date).
    Работает с базовым XML без внешних библиотек.
    """
    try:
        resp = requests.get(url, timeout=TIMEOUT, headers={
            "User-Agent": "MarketPulseBot/1.0"
        })
        if resp.status_code != 200:
            return []

        text = resp.text
        items = []

        # Находим все <item>
        for item_match in re.finditer(r"<item>(.*?)</item>", text, re.DOTALL):
            item_text = item_match.group(1)

            title = _extract_tag(item_text, "title")
            link  = _extract_tag(item_text, "link")
            pub   = _extract_tag(item_text, "pubDate")

            if not title or not link:
                continue

            # Парсим дату
            try:
                pub_dt = parsedate_to_datetime(pub) if pub else \
                         datetime.now(timezone.utc)
            except Exception:
                pub_dt = datetime.now(timezone.utc)

            # Очищаем от CDATA и HTML
            title = _clean(title)
            link  = _clean(link)

            items.append((title, link, pub_dt))

        return items

    except Exception as e:
        logger.debug(f"RSS {url}: {e}")
        return []


def _extract_tag(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>",
                  text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _clean(text: str) -> str:
    """Убирает HTML-теги и лишние пробелы."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ════════════════════════════════════════════════════════════
#  Ключевые слова для фильтрации
# ════════════════════════════════════════════════════════════

RELEVANT_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "eth", "crypto", "defi", "nft",
    "fed", "interest rate", "sec", "regulation", "etf", "halving",
    "market", "bull", "bear", "liquidation", "solana", "sol",
    "bnb", "xrp", "ripple", "tether", "usdt", "stablecoin",
    "инфляция", "ставка", "рынок", "крипто", "биткоин",
]


def _is_relevant(title: str) -> bool:
    title_l = title.lower()
    return any(kw in title_l for kw in RELEVANT_KEYWORDS)


# ════════════════════════════════════════════════════════════
#  Публичная функция
# ════════════════════════════════════════════════════════════

async def fetch_news(max_items: int = MAX_NEWS) -> str:
    """
    Загружает и форматирует новости для вставки в сигнал.
    Возвращает готовый текстовый блок или "".
    """
    loop = asyncio.get_event_loop()

    all_items: List[Tuple[str, str, datetime, str]] = []

    for source_name, url in RSS_SOURCES:
        try:
            items = await loop.run_in_executor(None, _fetch_rss, url)
            for title, link, pub_dt in items:
                if _is_relevant(title):
                    all_items.append((title, link, pub_dt, source_name))
        except Exception as e:
            logger.debug(f"news {source_name}: {e}")
        await asyncio.sleep(0.1)

    if not all_items:
        return ""

    # Сортируем по дате (свежие первые)
    all_items.sort(key=lambda x: x[2], reverse=True)

    # Дедупликация похожих заголовков
    unique: List[Tuple] = []
    seen_words: set = set()
    for item in all_items:
        title_words = set(item[0].lower().split()[:5])
        if not title_words & seen_words:
            unique.append(item)
            seen_words.update(title_words)
        if len(unique) >= max_items:
            break

    if not unique:
        return ""

    lines = ["📰 *Новости:*"]
    for title, link, pub_dt, source in unique:
        # Обрезаем длинные заголовки
        short_title = title[:90] + "…" if len(title) > 90 else title
        lines.append(f"— {short_title} _({source})_")

    return "\n".join(lines)
