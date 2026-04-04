# ============================================================
# news_fetcher.py — УСИЛЕННАЯ ВЕРСИЯ
# Новости с объяснением влияния на рынок на русском языке
# Источники: CryptoCompare RSS + встроенная классификация
# ============================================================

from __future__ import annotations
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple
import aiohttp

logger = logging.getLogger(__name__)

# ── RSS источники (публичные, без API ключа)
RSS_SOURCES = [
    "https://cointelegraph.com/rss",
    "https://cryptonews.com/news/feed/",
    "https://decrypt.co/feed",
    "https://www.theblock.co/rss.xml",
]

# ── Ключевые слова для классификации влияния
BULLISH_KEYWORDS = [
    "etf approved", "etf inflow", "institutional buy", "adoption",
    "rate cut", "fed pivot", "bullish", "surge", "rally", "breakout",
    "all-time high", "ath", "inflows", "купили", "одобрен", "приток",
    "запуск", "листинг", "партнёрство", "халвинг", "halving",
    "strategic reserve", "treasury", "накопление",
]
BEARISH_KEYWORDS = [
    "ban", "crackdown", "sec lawsuit", "fine", "hack", "exploit",
    "bearish", "crash", "collapse", "sell-off", "outflows", "dump",
    "запрет", "штраф", "взлом", "иск", "обвинение", "отток",
    "ликвидация", "банкротство", "manipulation",
]
NEUTRAL_KEYWORDS = [
    "upgrade", "fork", "update", "partnership", "launch", "integration",
    "обновление", "форк", "интеграция", "анонс",
]


# ════════════════════════════════════════════════════════════
# ПАРСИНГ RSS
# ════════════════════════════════════════════════════════════

async def _fetch_rss(url: str, timeout: int = 8) -> List[Dict]:
    """Загружает RSS и парсит заголовки."""
    items = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    return []
                text = await resp.text()

        # Простой парсинг XML без зависимостей
        item_blocks = re.findall(r"<item>(.*?)</item>", text, re.DOTALL)
        for block in item_blocks[:10]:
            title = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", block, re.DOTALL)
            link  = re.search(r"<link>(.*?)</link>", block, re.DOTALL)
            if title:
                items.append({
                    "title": title.group(1).strip(),
                    "link":  link.group(1).strip() if link else "",
                    "source": url.split("/")[2],
                })
    except Exception as e:
        logger.debug(f"rss {url}: {e}")
    return items


async def fetch_all_news(limit: int = 15) -> List[Dict]:
    """Собирает новости из всех источников."""
    tasks = [_fetch_rss(url) for url in RSS_SOURCES]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_items = []
    for r in results:
        if isinstance(r, list):
            all_items.extend(r)
    return all_items[:limit]


# ════════════════════════════════════════════════════════════
# КЛАССИФИКАЦИЯ И ПЕРЕВОД ВЛИЯНИЯ
# ════════════════════════════════════════════════════════════

def _classify_news(title: str) -> Tuple[str, str, str]:
    """
    Определяет: 
    - sentiment: "bullish" / "bearish" / "neutral"
    - icon: 🟢 / 🔴 / ⚪
    - impact: объяснение влияния на рынок (русский)
    """
    title_low = title.lower()

    # Специфические случаи — сначала
    if any(k in title_low for k in ["etf", "spot etf"]):
        if any(k in title_low for k in ["approved", "inflow", "launch", "record"]):
            return "bullish", "🟢", (
                "ETF-потоки напрямую влияют на цену — одобрение или приток денег "
                "в ETF означает реальные покупки биткоина на рынке."
            )
        elif any(k in title_low for k in ["outflow", "rejected", "banned"]):
            return "bearish", "🔴", (
                "Отток из ETF или отказ в одобрении — реальное давление продаж, "
                "институционалы выходят из позиций."
            )

    if any(k in title_low for k in ["fed", "rate cut", "interest rate", "powell"]):
        if any(k in title_low for k in ["cut", "lower", "pivot", "pause"]):
            return "bullish", "🟢", (
                "Снижение ставок ФРС = дешёвые деньги = больше капитала идёт в риск-активы "
                "включая крипту. Исторически — бычий драйвер."
            )
        elif any(k in title_low for k in ["hike", "raise", "higher"]):
            return "bearish", "🔴", (
                "Повышение ставок = дорогие деньги = инвесторы уходят в кэш и облигации. "
                "Давление на все рисковые активы включая крипту."
            )

    if any(k in title_low for k in ["hack", "exploit", "stolen", "breach"]):
        return "bearish", "🔴", (
            "Взлом протокола или биржи — прямые потери пользователей и удар по доверию. "
            "Рынок реагирует продажами в краткосрочной перспективе."
        )

    if any(k in title_low for k in ["sec", "lawsuit", "charges", "fraud"]):
        return "bearish", "🔴", (
            "Регуляторные претензии создают неопределённость — инвесторы снижают экспозицию "
            "до прояснения ситуации. Особенно чувствительно для альткоинов."
        )

    if any(k in title_low for k in ["halving", "halvening"]):
        return "bullish", "🟢", (
            "Халвинг сокращает предложение новых биткоинов вдвое. "
            "Исторически через 6-12 месяцев после халвинга BTC обновлял ATH."
        )

    if any(k in title_low for k in ["ban", "banned", "restrict", "prohibit"]):
        return "bearish", "🔴", (
            "Запрет на крипту в стране — удар по ликвидности и объёму торговли. "
            "Краткосрочно медвежье, долгосрочно рынок адаптируется."
        )

    if any(k in title_low for k in ["adoption", "reserve", "treasury", "government buy"]):
        return "bullish", "🟢", (
            "Государственные и корпоративные покупки — покупатель с огромными карманами "
            "входит на рынок. Прямое давление на предложение."
        )

    if any(k in title_low for k in ["upgrade", "mainnet", "launch", "update"]):
        return "neutral", "⚪", (
            "Технические обновления улучшают сеть — позитивно долгосрочно, "
            "краткосрочный эффект обычно 'купи слухи, продай факт'."
        )

    # Общие keywords
    for kw in BULLISH_KEYWORDS:
        if kw in title_low:
            return "bullish", "🟢", "Позитивная новость — краткосрочно поддерживает спрос на рынке."

    for kw in BEARISH_KEYWORDS:
        if kw in title_low:
            return "bearish", "🔴", "Негативная новость — краткосрочно создаёт давление продаж."

    return "neutral", "⚪", "Новость информационная — наблюдаем за реакцией рынка."


def _translate_title(title: str) -> str:
    """
    Упрощённый перевод ключевых фраз для понимания.
    Оставляет английский, но добавляет ключевые пояснения.
    """
    # Можно подключить Google Translate API или DeepL при наличии ключа
    # Пока — встроенный словарь ключевых фраз
    replacements = {
        "SEC": "SEC (регулятор США)",
        "ETF": "ETF (биржевой фонд)",
        "Fed": "ФРС (Федрезерв)",
        "Federal Reserve": "Федрезерв",
        "interest rate": "процентная ставка",
        "rate cut": "снижение ставки",
        "rate hike": "повышение ставки",
        "bull run": "бычье ралли",
        "bear market": "медвежий рынок",
        "all-time high": "исторический максимум",
        "ATH": "ATH (исторический максимум)",
        "halving": "халвинг (сокращение вознаграждения майнеров)",
        "staking": "стейкинг (заморозка для дохода)",
        "liquidation": "ликвидация позиций",
        "open interest": "открытый интерес (объём позиций)",
        "funding rate": "ставка финансирования",
        "DeFi": "DeFi (децентрализованные финансы)",
        "NFT": "NFT (невзаимозаменяемый токен)",
        "Layer 2": "L2 (масштабирование блокчейна)",
    }
    result = title
    for eng, rus in replacements.items():
        result = result.replace(eng, rus)
    return result


# ════════════════════════════════════════════════════════════
# ФОРМАТИРОВАНИЕ НОВОСТЕЙ ДЛЯ ТЕЛЕГРАМА
# ════════════════════════════════════════════════════════════

def format_news_for_report(news_items: List[Dict], max_items: int = 5) -> str:
    """
    Форматирует новости с объяснением влияния.
    Каждая новость: заголовок + что это значит для рынка.
    """
    if not news_items:
        return ""

    lines = ["*📰 Важные новости и их влияние на рынок:*", ""]

    seen_titles = set()
    count = 0

    for item in news_items:
        if count >= max_items:
            break

        title = item.get("title", "").strip()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)

        sentiment, icon, impact = _classify_news(title)
        translated = _translate_title(title)

        lines.append(f"{icon} *{translated}*")
        lines.append(f"_↳ {impact}_")
        lines.append("")

        count += 1

    if not count:
        return ""

    return "\n".join(lines)


def format_news_short(news_items: List[Dict], max_items: int = 3) -> List[str]:
    """
    Короткий список новостей для Market Pulse отчёта.
    Возвращает список строк для вставки в отчёт.
    """
    result = []
    seen   = set()
    for item in news_items[:max_items * 2]:
        title = item.get("title", "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        sentiment, icon, impact = _classify_news(title)
        # Короткий вариант: только заголовок + краткое влияние
        short_impact = impact.split(".")[0]
        result.append(f"{icon} {title} — _{short_impact}_")
        if len(result) >= max_items:
            break
    return result


# ════════════════════════════════════════════════════════════
# ГЛАВНАЯ ТОЧКА ВХОДА
# ════════════════════════════════════════════════════════════

async def get_market_news(limit: int = 8) -> Tuple[List[str], str]:
    """
    Загружает новости и возвращает:
    - short_list: список строк для Market Pulse
    - full_text: полный блок новостей с объяснениями
    """
    try:
        items = await fetch_all_news(limit * 2)
        short = format_news_short(items, max_items=3)
        full  = format_news_for_report(items, max_items=limit)
        return short, full
    except Exception as e:
        logger.error(f"get_market_news: {e}")
        return [], ""


# Синхронная обёртка для использования без async
def get_news_sync(limit: int = 5) -> List[str]:
    """Синхронная версия для использования в scheduler."""
    try:
        loop = asyncio.new_event_loop()
        items = loop.run_until_complete(fetch_all_news(limit * 2))
        loop.close()
        return format_news_short(items, max_items=limit)
    except Exception as e:
        logger.error(f"get_news_sync: {e}")
        return []
