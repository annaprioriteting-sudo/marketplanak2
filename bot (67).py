# ============================================================
# bot.py — ОБНОВЛЁННАЯ ВЕРСИЯ
# + Уведомления (вместо "Алертов")
# + Раздел Обучение
# + Исправлены кнопки
# ============================================================

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)
from aiogram.client.default import DefaultBotProperties

from config import (
    TELEGRAM_BOT_TOKEN, ALLOWED_CHAT_IDS,
    FOREX_METALS_SYMBOLS, ADMIN_IDS, PAYMENT_INFO,
    FREE_DAILY_SIGNALS,
)
from access_control import (
    init_user, get_access_level, is_full_access,
    can_get_free_signal, record_signal_usage,
    add_paid_user, remove_paid_user,
    get_user_info, list_paid_users,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp  = Dispatcher(storage=MemoryStorage())

active_analyses: Dict = {}
WATCHLIST_FILE = Path("/root/bots/market_analyst_bot/watchlist.json")


# ════════════════════════════════════════════════════════════
# FSM
# ════════════════════════════════════════════════════════════

class Form(StatesGroup):
    waiting_symbol = State()
    waiting_add    = State()


# ════════════════════════════════════════════════════════════
# WATCHLIST
# ════════════════════════════════════════════════════════════

def load_watchlist() -> List[str]:
    try:
        if WATCHLIST_FILE.exists():
            return json.loads(WATCHLIST_FILE.read_text())
    except Exception:
        pass
    return []


def save_watchlist(lst: List[str]):
    try:
        WATCHLIST_FILE.write_text(json.dumps(lst, ensure_ascii=False))
    except Exception as e:
        logger.error(f"watchlist: {e}")


# ════════════════════════════════════════════════════════════
# КЛАВИАТУРЫ
# ════════════════════════════════════════════════════════════

def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Отчёт"),       KeyboardButton(text="🎯 Сигнал дня")],
            [KeyboardButton(text="🔔 Уведомления"), KeyboardButton(text="📈 Статистика")],
            [KeyboardButton(text="🔍 Анализ монеты"),KeyboardButton(text="📚 Обучение")],
            [KeyboardButton(text="📝 Мой список")],
        ],
        resize_keyboard=True, persistent=True,
    )


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
    )


def quick_symbols_inline(action: str) -> InlineKeyboardMarkup:
    crypto = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
    forex  = ["XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY"]
    rows = [
        [InlineKeyboardButton(text="── Крипто ──", callback_data="noop")],
        [InlineKeyboardButton(text=s, callback_data=f"{action}:{s}") for s in crypto[:3]],
        [InlineKeyboardButton(text=s, callback_data=f"{action}:{s}") for s in crypto[3:]],
        [InlineKeyboardButton(text="── Форекс и металлы ──", callback_data="noop")],
        [InlineKeyboardButton(text=s, callback_data=f"{action}:{s}") for s in forex[:3]],
        [InlineKeyboardButton(text=s, callback_data=f"{action}:{s}") for s in forex[2:]],
        [InlineKeyboardButton(text="✏️ Другой тикер", callback_data=f"type:{action}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def watchlist_inline(wl: List[str]) -> Optional[InlineKeyboardMarkup]:
    if not wl:
        return None
    rows = [[
        InlineKeyboardButton(text=f"🔍 {s}", callback_data=f"analyze:{s}"),
        InlineKeyboardButton(text="❌", callback_data=f"remove:{s}"),
    ] for s in wl]
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="type:add")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def upgrade_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💳 Получить полный доступ", callback_data="show_upgrade"),
    ]])


def education_inline() -> InlineKeyboardMarkup:
    """Кнопки раздела Обучение."""
    rows = [
        [InlineKeyboardButton(text="📖 Что такое SMC?", callback_data="edu:smc")],
        [InlineKeyboardButton(text="🧱 Order Block (OB)", callback_data="edu:ob")],
        [InlineKeyboardButton(text="📊 Fair Value Gap (FVG)", callback_data="edu:fvg")],
        [InlineKeyboardButton(text="💧 Ликвидность (Liquidity)", callback_data="edu:liquidity")],
        [InlineKeyboardButton(text="🔄 BOS и ChoCH", callback_data="edu:bos")],
        [InlineKeyboardButton(text="📐 Premium и Discount зоны", callback_data="edu:pd")],
        [InlineKeyboardButton(text="⚖️ Что такое R:R?", callback_data="edu:rr")],
        [InlineKeyboardButton(text="🎯 Как читать сигнал бота?", callback_data="edu:signal")],
        [InlineKeyboardButton(text="📋 Стратегии бота", callback_data="edu:strategies")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ════════════════════════════════════════════════════════════
# ТЕКСТЫ ОБУЧЕНИЯ
# ════════════════════════════════════════════════════════════

EDUCATION_TEXTS = {

    "smc": (
        "📖 *Что такое Smart Money Concepts (SMC)?*\n\n"
        "SMC — это метод торговли, который изучает *следы крупных игроков:* "
        "банков, хедж-фондов и маркетмейкеров.\n\n"
        "Идея простая: крупные игроки не могут зайти или выйти из позиции "
        "по одной цене — им нужна *ликвидность* (чужие ордера напротив).\n\n"
        "Поэтому они:\n"
        "1. *Собирают ликвидность* — гоняют цену к стопам мелких трейдеров\n"
        "2. *Разворачивают цену* — из зоны своих ордеров (Order Block)\n"
        "3. *Заполняют имбалансы* — закрывают незаполненные ценовые gaps (FVG)\n\n"
        "Бот отслеживает эти следы и ждёт вход в нужной зоне.\n\n"
        "💡 *Главное правило SMC:* торгуй в сторону крупного игрока, не против него."
    ),

    "ob": (
        "🧱 *Order Block (Блок ордеров, OB)*\n\n"
        "Order Block — это *последняя свеча перед сильным импульсным движением.*\n\n"
        "Представь: цена резко улетела вверх на +5%. "
        "Последняя медвежья свеча ПЕРЕД этим движением — и есть OB.\n\n"
        "Почему это важно?\n"
        "Крупный игрок размещал покупки именно там. "
        "Когда цена вернётся в эту зону — он снова выкупит, "
        "потому что у него там остались незакрытые ордера.\n\n"
        "Типы OB:\n"
        "🟢 *Бычий OB* — медвежья свеча перед импульсом вверх → ищем лонг\n"
        "🔴 *Медвежий OB* — бычья свеча перед импульсом вниз → ищем шорт\n\n"
        "❗ Как торгует бот:\n"
        "Ставит *лимитный ордер* на границу OB. "
        "Стоп — за OB (чтобы не выбило случайно). "
        "Вход НЕ по рынку прямо сейчас — ждём возврата цены."
    ),

    "fvg": (
        "📊 *Fair Value Gap (Имбаланс, FVG)*\n\n"
        "FVG — это *ценовой разрыв*, который образуется при очень быстром движении.\n\n"
        "Когда цена летит быстро, на каком-то уровне не было нормального обмена "
        "покупок и продаж. Это 'незаполненный' участок графика.\n\n"
        "Рынок стремится заполнить эти пробелы — цена возвращается в зону FVG.\n\n"
        "Как выглядит:\n"
        "Три свечи подряд. Промежуток между хвостом 1-й и хвостом 3-й свечи "
        "— это и есть FVG.\n\n"
        "Типы FVG:\n"
        "🟢 *Бычий FVG* — разрыв при движении вверх → поддержка при откате\n"
        "🔴 *Медвежий FVG* — разрыв при движении вниз → сопротивление при отскоке\n\n"
        "❗ Как торгует бот:\n"
        "FVG часто используется *вместе с OB* (confluence). "
        "Когда OB и FVG находятся в одной зоне — сигнал намного сильнее."
    ),

    "liquidity": (
        "💧 *Ликвидность (Liquidity)*\n\n"
        "Ликвидность — это *скопление стоп-ордеров* других трейдеров.\n\n"
        "Большинство трейдеров ставят стопы за очевидными уровнями:\n"
        "· За прошлым хаем → там сосредоточены стопы шортов\n"
        "· За прошлым лоу → там сосредоточены стопы лонгов\n\n"
        "Крупному игроку нужны эти ордера как контрагент. "
        "Поэтому он *специально* гоняет цену за эти уровни (sweep/grab), "
        "забирает ликвидность и разворачивается.\n\n"
        "В боте:\n"
        "🔴 *BSL (Buy-Side Liquidity)* — ликвидность выше рынка (стопы шортистов)\n"
        "🟢 *SSL (Sell-Side Liquidity)* — ликвидность ниже рынка (стопы лонгистов)\n\n"
        "💡 *Классический сетап:*\n"
        "Цена делает sweep вниз (выносит стопы лонгов) → "
        "разворачивается вверх из OB. Это сигнал на лонг."
    ),

    "bos": (
        "🔄 *BOS и ChoCH — слом структуры*\n\n"
        "Это сигналы смены направления движения.\n\n"
        "─────────────────────────\n"
        "*BOS (Break of Structure) — пробой структуры*\n\n"
        "Тренд продолжается. При восходящем тренде каждый новый хай выше предыдущего — это BOS↑. "
        "Значит умные деньги всё ещё покупают.\n\n"
        "─────────────────────────\n"
        "*ChoCH (Change of Character) — смена характера*\n\n"
        "Тренд МЕНЯЕТСЯ. Бычий тренд обрывается когда цена пробивает *вниз* предыдущий лой — "
        "это ChoCH↓. Продавцы взяли контроль.\n\n"
        "─────────────────────────\n"
        "Как использует бот:\n"
        "· Ждёт BOS↑ на 1H как подтверждение входа в лонг\n"
        "· При ChoCH↓ на 4H — сигнал на шорт или выход из лонгов\n"
        "· *Входим только ПОСЛЕ* BOS/ChoCH, не до!"
    ),

    "pd": (
        "📐 *Premium и Discount зоны*\n\n"
        "Рынок движется между двумя крайностями:\n\n"
        "Берём диапазон (последний хай — последний лой) и делим пополам:\n\n"
        "🔴 *Premium (выше середины)* — цена дорогая. "
        "Умные деньги ПРОДАЮТ в premium.\n\n"
        "🟢 *Discount (ниже середины)* — цена дешёвая. "
        "Умные деньги ПОКУПАЮТ в discount.\n\n"
        "⚪ *Equilibrium (середина)* — нейтральная зона, нет преимущества.\n\n"
        "Правило бота:\n"
        "✅ Лонг ищем в Discount (покупаем дёшево)\n"
        "✅ Шорт ищем в Premium (продаём дорого)\n"
        "❌ Не покупаем в Premium и не продаём в Discount"
    ),

    "rr": (
        "⚖️ *Что такое R:R (Risk to Reward)?*\n\n"
        "R:R — соотношение риска к прибыли в сделке.\n\n"
        "*Пример:*\n"
        "Ты рискуешь $100 (стоп). Потенциал прибыли $200 (тейк).\n"
        "R:R = 1:2 (или просто '2')\n\n"
        "Почему это важно:\n"
        "Даже если ты угадываешь только 50% сделок, "
        "при R:R 2 ты всё равно в ПЛЮСЕ.\n\n"
        "Математика:\n"
        "· 5 побед × $200 = +$1000\n"
        "· 5 поражений × $100 = -$500\n"
        "· Итого: +$500 при 50% винрейте!\n\n"
        "Стандарты бота:\n"
        "✅ R:R ≥ 2.0 — принимаем к сигналу\n"
        "⚠️ R:R 1.5 — допустимо при высоком качестве\n"
        "❌ R:R < 1.5 — не торгуем\n\n"
        "💡 *Никогда не торгуй с R:R меньше 1. "
        "Это проигрышная математика по определению.*"
    ),

    "signal": (
        "🎯 *Как читать сигнал бота?*\n\n"
        "Разберём на примере:\n\n"
        "───────────────────────────\n"
        "🟢 *SOLUSDT — LONG (покупка)*\n\n"
        "📌 Ордер: ЛИМИТНЫЙ\n"
        "Сейчас цена `82.50`. Ставь лимит на `79.30`\n\n"
        "Зона входа (OB · 4H): от `78.90` до `79.80`\n\n"
        "Лимитный ордер: `79.30`\n"
        "Стоп-лосс: `77.50` (-2.3%) ← за блоком\n"
        "Тейк-профит: `88.00` (+10.9%)\n\n"
        "R:R 4.7 — на $1 риска потенциал $4.7 прибыли\n"
        "───────────────────────────\n\n"
        "Что делать:\n"
        "1️⃣ Открой Bitget (или другую биржу)\n"
        "2️⃣ Найди SOLUSDT фьючерс\n"
        "3️⃣ Выбери 'Лимитный ордер'\n"
        "4️⃣ Цена: 79.30 · Стоп: 77.50 · Тейк: 88.00\n"
        "5️⃣ Размер: не более 2% от депозита на риск\n"
        "6️⃣ Жди — ордер сработает когда цена вернётся в зону\n\n"
        "⚠️ Если цена не возвращается и уходит выше — "
        "отменяй ордер. Сетап устарел."
    ),

    "strategies": (
        "📋 *Стратегии которые использует бот*\n\n"
        "Бот автоматически выбирает стратегию в зависимости от фазы рынка:\n\n"
        "─────────────────────────\n"
        "1️⃣ *SMC Institutional (основная)*\n"
        "Применяется при: бычьем или медвежьем тренде\n"
        "Суть: входим от OB/FVG в направлении тренда. "
        "Ждём sweep ликвидности и BOS на младшем TF.\n"
        "Стоп: за OB на ATR × 1.5\n\n"
        "─────────────────────────\n"
        "2️⃣ *Trend Following (по тренду)*\n"
        "Применяется при: сильном тренде (EMA выстроены)\n"
        "Суть: покупаем/продаём откаты к EMA20, "
        "не против основного направления.\n"
        "Стоп: за EMA50\n\n"
        "─────────────────────────\n"
        "3️⃣ *Range Trading (диапазон)*\n"
        "Применяется при: боковом рынке\n"
        "Суть: покупаем у нижней границы диапазона, "
        "продаём у верхней. Середина диапазона — нейтральная зона.\n"
        "Стоп: за пробоем границы + ATR\n\n"
        "─────────────────────────\n"
        "❗ *Важно:* Бот НЕ торгует в автоматическом режиме. "
        "Он выдаёт идею с уровнями. "
        "*Решение о входе — за тобой.*"
    ),
}


# ════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ════════════════════════════════════════════════════════════

def is_allowed(cid: int) -> bool:
    return cid in ALLOWED_CHAT_IDS


async def safe_send(chat_id: int, text: str, **kw):
    if len(text) <= 4000:
        try:
            await bot.send_message(chat_id, text, **kw)
        except Exception:
            try:
                await bot.send_message(chat_id, text, parse_mode=None)
            except Exception:
                pass
        return

    chunks, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > 3900:
            chunks.append(buf)
            buf = line + "\n"
        else:
            buf += line + "\n"
    if buf:
        chunks.append(buf)

    for chunk in chunks:
        try:
            await bot.send_message(chat_id, chunk, **kw)
        except Exception:
            await bot.send_message(chat_id, chunk, parse_mode=None)
        await asyncio.sleep(0.4)


def detect_asset_type(sym: str) -> str:
    if sym in FOREX_METALS_SYMBOLS:
        return "metal" if sym.startswith("XA") else "forex"
    return "crypto"


async def run_analysis(symbol: str, asset_type: str) -> str:
    loop = asyncio.get_event_loop()
    try:
        if asset_type == "crypto":
            from data_fetcher import fetch_bitget_all_timeframes
            tf_data = await loop.run_in_executor(None, fetch_bitget_all_timeframes, symbol)
        else:
            from data_fetcher import fetch_yfinance_all_timeframes
            tf_data = await loop.run_in_executor(None, fetch_yfinance_all_timeframes, symbol)

        if not tf_data:
            return f"⚠️ {symbol}: не удалось загрузить данные."

        from analyzer import full_analysis
        fa = await loop.run_in_executor(None, full_analysis, symbol, asset_type, tf_data)
        active_analyses[symbol] = fa

        from report_generator import generate_coin_report
        return generate_coin_report(fa)

    except Exception as e:
        logger.error(f"run_analysis {symbol}: {e}")
        return f"❌ Ошибка анализа {symbol}: {e}"


# ════════════════════════════════════════════════════════════
# /start
# ════════════════════════════════════════════════════════════

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    init_user(message.chat.id)
    level = get_access_level(message.chat.id)

    if level in ("paid", "admin"):
        status_line = "✅ *Подписка активна* — полный доступ\n\n"
    elif level == "trial":
        status_line = "🔓 *Пробный период* — 3 дня полного доступа\n\n"
    else:
        status_line = f"🔒 *Бесплатный доступ* — {FREE_DAILY_SIGNALS} сигнал в день\n\n"

    await message.answer(
        "📡 *Market Pulse*\n\n"
        + status_line +
        "Бот анализирует рынок по методу Smart Money Concepts — "
        "показывает где торгуют крупные игроки и даёт точки входа.\n\n"
        "Каждый день:\n"
        "🌅 *08:00* — обзор рынка (BTC, ETH, Золото)\n"
        "🎯 *Сигнал дня* — скан 50 фьючерсов Bitget, лучший сетап\n"
        "🔔 *Уведомления* — когда цена подходит к уровню\n"
        "🌙 *20:00* — итог дня\n\n"
        "👇 Начни с *📊 Отчёт* или *🎯 Сигнал дня*",
        reply_markup=main_keyboard(),
    )


# ════════════════════════════════════════════════════════════
# КНОПКИ ГЛАВНОГО МЕНЮ
# ════════════════════════════════════════════════════════════

@dp.message(F.text == "📊 Отчёт")
async def btn_report(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()

    if not is_full_access(message.chat.id):
        await message.answer(
            "🔒 *Полный отчёт доступен по подписке*\n\n"
            "Включает анализ всех инструментов, уровни и сценарии.\n\n"
            "Используй /upgrade.",
            reply_markup=upgrade_inline(),
        )
        return

    await message.answer("⏳ Запускаю полный анализ рынка...\nЗаймёт 2–4 минуты")
    try:
        from scheduler import run_morning_report

        class _P:
            active_analyses = globals()["active_analyses"]
            async def send_to_all(self, text):
                await safe_send(message.chat.id, text)

        await run_morning_report(_P())
    except Exception as e:
        logger.error(f"btn_report: {e}")
        await message.answer(f"❌ Ошибка: {e}", reply_markup=main_keyboard())


@dp.message(F.text == "🎯 Сигнал дня")
async def btn_signal(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    await _show_signals(message.chat.id)


@dp.message(F.text == "🔔 Уведомления")
async def btn_notifications(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    await _show_notifications(message.chat.id)


@dp.message(F.text == "📈 Статистика")
async def btn_stats(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    from signal_engine import get_stats_text
    await safe_send(message.chat.id, get_stats_text(), reply_markup=main_keyboard())


@dp.message(F.text == "🔍 Анализ монеты")
async def btn_analyze(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    if not is_full_access(message.chat.id):
        await message.answer(
            "🔒 Анализ монеты доступен по подписке.",
            reply_markup=upgrade_inline(),
        )
        return
    await message.answer("🔍 *Выбери инструмент:*", reply_markup=quick_symbols_inline("analyze"))


@dp.message(F.text == "📚 Обучение")
async def btn_education(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    await message.answer(
        "📚 *Обучение — Smart Money Concepts*\n\n"
        "Выбери тему. Всё объясняю простым языком — "
        "так чтобы было понятно даже без опыта в трейдинге:\n",
        reply_markup=education_inline(),
    )


@dp.message(F.text == "📝 Мой список")
async def btn_watchlist(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    await _show_watchlist(message.chat.id)


@dp.message(F.text == "❌ Отмена")
async def btn_cancel(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_keyboard())


# ════════════════════════════════════════════════════════════
# CALLBACKS — ОБУЧЕНИЕ
# ════════════════════════════════════════════════════════════

@dp.callback_query(F.data.startswith("edu:"))
async def cb_education(callback: CallbackQuery):
    if not is_allowed(callback.message.chat.id):
        await callback.answer()
        return

    topic = callback.data.split(":", 1)[1]
    text  = EDUCATION_TEXTS.get(topic, "Раздел в разработке.")
    await callback.answer()

    # Добавляем кнопку "назад"
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="← Назад к темам", callback_data="edu:back"),
    ]])
    await callback.message.answer(text, reply_markup=back_kb)


@dp.callback_query(F.data == "edu:back")
async def cb_education_back(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "📚 *Обучение — Smart Money Concepts*\n\nВыбери тему:",
        reply_markup=education_inline(),
    )


# ════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ════════════════════════════════════════════════════════════

def _format_signal_teaser(sig) -> str:
    """Тизер для бесплатных пользователей — без уровней."""
    dir_icon = "🟢" if sig.direction == "long" else "🔴"
    dir_ru   = "ЛОНГ" if sig.direction == "long" else "ШОРТ"
    asset_ic = {"crypto": "◆", "forex": "◇", "metal": "◈"}.get(sig.asset_type, "·")
    return (
        f"{asset_ic} *{sig.symbol}* {dir_icon} *{dir_ru}*\n\n"
        f"Фаза: {getattr(sig, 'phase_title', sig.phase)}\n"
        f"Качество: {sig.score}/10\n\n"
        f"Зона входа: `🔒 скрыто`\n"
        f"Вход: `🔒 скрыто`\n"
        f"Стоп: `🔒 скрыто`\n"
        f"Тейк: `🔒 скрыто`\n"
        f"R:R: `🔒 скрыто`\n\n"
        f"_Уровни и условие входа — в полной подписке_"
    )


async def _show_signals(chat_id: int):
    """Кнопка 🎯 Сигнал дня — сканирует ТОП-50 Bitget."""
    level = get_access_level(chat_id)

    if level in ("admin", "paid", "trial"):
        # Полный доступ — запускаем скан
        msg = await bot.send_message(chat_id, "🔄 Сканирую 50 фьючерсов Bitget...")

        async def progress(text: str):
            try:
                await msg.edit_text(text)
            except Exception:
                pass

        try:
            from signal_engine import (
                scan_market_for_best_signal, get_ai_comment,
                format_signal, format_no_signal, save_signals,
            )
            best, scanned = await scan_market_for_best_signal(
                progress_callback=progress, top_n_scan=50
            )

            if best is None:
                await msg.edit_text(format_no_signal(scanned))
                return

            ai_comment = await get_ai_comment(best)
            save_signals([best])
            text = format_signal(best, ai_comment)
            await msg.delete()
            await safe_send(chat_id, text, reply_markup=main_keyboard())

        except Exception as e:
            logger.error(f"_show_signals: {e}")
            await msg.edit_text(f"❌ Ошибка сканирования: {e}")

    else:
        # Бесплатный — берём из уже загруженных анализов
        if not active_analyses:
            await bot.send_message(
                chat_id,
                "🎯 *Сигнал дня*\n\n"
                "Сначала загрузи данные — нажми *📊 Отчёт*.\n\n"
                "После этого система выберет лучший сетап.",
                reply_markup=main_keyboard(),
            )
            return

        if not can_get_free_signal(chat_id):
            await bot.send_message(
                chat_id,
                "🔒 *Лимит бесплатных сигналов исчерпан*\n\n"
                f"Сегодня доступен {FREE_DAILY_SIGNALS} сигнал — ты его уже использовал.\n\n"
                "Завтра лимит обновится, или оформи подписку.",
                reply_markup=upgrade_inline(),
            )
            return

        try:
            from signal_engine import get_best_signals, save_signals
            signals = get_best_signals(active_analyses, top_n=2, min_score=3.5)
            save_signals(signals)

            if not signals:
                await bot.send_message(chat_id, "🎯 Сегодня нет сильных сигналов.",
                                       reply_markup=main_keyboard())
                return

            from datetime import datetime
            import pytz
            now_str = datetime.now(pytz.timezone("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")

            teaser = (
                f"🎯 *СИГНАЛ ДНЯ*  ·  {now_str}\n"
                f"{'─' * 28}\n\n"
                + _format_signal_teaser(signals[0])
                + f"\n\n{'─' * 28}\n"
                f"_Полный сигнал с зоной входа, стопом и целью — по подписке_"
            )
            record_signal_usage(chat_id)
            await safe_send(chat_id, teaser, reply_markup=upgrade_inline())

        except Exception as e:
            logger.error(f"_show_signals free: {e}")
            await bot.send_message(chat_id, f"❌ Ошибка: {e}", reply_markup=main_keyboard())


async def _show_notifications(chat_id: int):
    """
    Кнопка 🔔 Уведомления — показывает настройку уведомлений.
    ВМЕСТО старых 'Алертов' — понятнее для пользователя.
    """
    if not is_full_access(chat_id):
        await bot.send_message(
            chat_id,
            "🔔 *Уведомления доступны по подписке*\n\n"
            "Бот автоматически напишет тебе когда:\n"
            "  · 📍 Цена подходит к ключевому уровню (за 0.3–0.5%)\n"
            "  · 🎯 Активируется лимитный ордер из Сигнала дня\n"
            "  · ✅ Достигнута цель или ❌ сработал стоп\n\n"
            "Никаких лишних сообщений — только важные события.",
            reply_markup=upgrade_inline(),
        )
        return

    if not active_analyses:
        await bot.send_message(
            chat_id,
            "🔔 *Уведомления*\n\n"
            "Сначала запусти *📊 Отчёт* — бот загрузит уровни.\n\n"
            "После этого уведомления будут приходить автоматически "
            "когда цена подходит к ключевому уровню.",
            reply_markup=main_keyboard(),
        )
        return

    lines = ["🔔 *Активные уровни для уведомлений:*\n"]

    for sym, fa in list(active_analyses.items())[:8]:
        price = fa.current_price or 0
        if fa.key_levels:
            sup = [l for l in fa.key_levels if l < price][-2:]
            res = [l for l in fa.key_levels if l > price][:2]
            res_str = " · ".join(f"`{l}`" for l in res) if res else "—"
            sup_str = " · ".join(f"`{l}`" for l in reversed(sup)) if sup else "—"
            lines.append(f"*{sym}* @ `{price}`\n  ▲ {res_str}\n  ▼ {sup_str}")

    lines.append("\n_Уведомление приходит когда цена в 0.3–0.5% от уровня_")
    await safe_send(chat_id, "\n\n".join(lines))


async def _show_watchlist(chat_id: int):
    wl = load_watchlist()
    if not wl:
        await bot.send_message(
            chat_id,
            "📝 *Список наблюдения пуст*\n\nНажми *🔍 Анализ монеты* → добавить",
            reply_markup=main_keyboard(),
        )
        return
    await bot.send_message(
        chat_id,
        "📝 *Список наблюдения:*\n\nНажми для анализа или ❌ удалить:",
        reply_markup=watchlist_inline(wl),
    )


# ════════════════════════════════════════════════════════════
# КОМАНДЫ
# ════════════════════════════════════════════════════════════

@dp.message(Command("status"))
async def cmd_status(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    from datetime import datetime
    import pytz
    now = datetime.now(pytz.timezone("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
    wl  = load_watchlist()
    mon = list(active_analyses.keys())
    mon_str = ", ".join(f"`{s}`" for s in mon[:8]) if mon else "нет"
    access_str = get_user_info(message.chat.id)
    await message.answer(
        f"🤖 *Статус*  ·  {now}\n\n"
        f"👤 {access_str}\n\n"
        f"📊 Загружено анализов: {len(active_analyses)}\n"
        f"📝 В списке: {len(wl)}\n\n"
        f"🔔 Мониторинг:\n{mon_str}\n\n"
        f"_Уведомления приходят автоматически при подходе цены к уровню_",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("upgrade"))
async def cmd_upgrade(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    if get_access_level(message.chat.id) in ("paid", "admin"):
        await message.answer("✅ У тебя уже есть полный доступ.", reply_markup=main_keyboard())
        return
    await message.answer(PAYMENT_INFO, reply_markup=main_keyboard())


@dp.message(Command("stats"))
async def cmd_stats(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    from signal_engine import get_stats_text
    await safe_send(message.chat.id, get_stats_text(), reply_markup=main_keyboard())


# ── Admin ─────────────────────────────────────────────────

@dp.message(Command("adduser"))
async def cmd_adduser(message: Message, state: FSMContext):
    if message.chat.id not in ADMIN_IDS:
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 2:
        await message.answer("Использование: `/adduser 123456789`")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("Неверный ID")
        return
    if add_paid_user(target_id):
        await message.answer(f"✅ Пользователь `{target_id}` добавлен.")
        try:
            await bot.send_message(
                target_id,
                "🎉 *Твоя подписка активирована!*\n\n"
                "Теперь доступны:\n"
                "• Полный анализ рынка\n"
                "• Сигнал дня с лимитными ордерами\n"
                "• Уведомления в реальном времени\n"
                "• Анализ любой монеты\n\n"
                "Нажми *🎯 Сигнал дня* 👇",
            )
        except Exception:
            pass
    else:
        await message.answer("❌ Ошибка.")


@dp.message(Command("removeuser"))
async def cmd_removeuser(message: Message, state: FSMContext):
    if message.chat.id not in ADMIN_IDS:
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 2:
        await message.answer("Использование: `/removeuser 123456789`")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("Неверный ID")
        return
    if remove_paid_user(target_id):
        await message.answer(f"✅ Пользователь `{target_id}` переведён в free.")
    else:
        await message.answer("❌ Ошибка.")


@dp.message(Command("listusers"))
async def cmd_listusers(message: Message, state: FSMContext):
    if message.chat.id not in ADMIN_IDS:
        return
    paid = list_paid_users()
    if not paid:
        await message.answer("Платных пользователей нет.")
        return
    lines = ["👥 *Платные пользователи:*\n"]
    for uid in paid:
        lines.append(f"• `{uid}`")
    await message.answer("\n".join(lines))


# ════════════════════════════════════════════════════════════
# FSM — ВВОД СИМВОЛА
# ════════════════════════════════════════════════════════════

@dp.message(Form.waiting_symbol)
async def fsm_got_symbol(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_keyboard())
        return
    symbol = (message.text or "").strip().upper().replace("/", "").replace("-", "")
    if not symbol:
        await message.answer("Введи символ, например `BTCUSDT`:")
        return
    await state.clear()
    await message.answer(f"🔍 Анализирую `{symbol}`...", reply_markup=main_keyboard())
    text = await run_analysis(symbol, detect_asset_type(symbol))
    await safe_send(message.chat.id, f"◆ {text}")


@dp.message(Form.waiting_add)
async def fsm_got_add(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_keyboard())
        return
    symbol = (message.text or "").strip().upper().replace("/", "").replace("-", "")
    if not symbol:
        await message.answer("Введи символ, например `BTCUSDT`:")
        return
    await state.clear()
    wl = load_watchlist()
    if symbol in wl:
        await message.answer(f"`{symbol}` уже в списке.", reply_markup=main_keyboard())
        return
    wl.append(symbol)
    save_watchlist(wl)
    await message.answer(f"✅ `{symbol}` добавлен.", reply_markup=main_keyboard())


# ════════════════════════════════════════════════════════════
# INLINE CALLBACKS
# ════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()


@dp.callback_query(F.data == "show_upgrade")
async def cb_show_upgrade(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(PAYMENT_INFO)


@dp.callback_query(F.data.startswith("type:"))
async def cb_type(callback: CallbackQuery, state: FSMContext):
    if not is_allowed(callback.message.chat.id):
        await callback.answer(); return
    action = callback.data.split(":", 1)[1]
    await callback.answer()
    if action == "analyze":
        await state.set_state(Form.waiting_symbol)
        await callback.message.answer(
            "✏️ Введи тикер:\n`BTCUSDT` / `SOLUSDT` / `BNBUSDT`",
            reply_markup=cancel_keyboard(),
        )
    else:
        await state.set_state(Form.waiting_add)
        await callback.message.answer("✏️ Введи тикер для добавления:", reply_markup=cancel_keyboard())


@dp.callback_query(F.data.startswith("analyze:"))
async def cb_analyze(callback: CallbackQuery, state: FSMContext):
    if not is_allowed(callback.message.chat.id):
        await callback.answer(); return
    if not is_full_access(callback.message.chat.id):
        await callback.answer("🔒 Доступно по подписке", show_alert=True); return
    symbol = callback.data.split(":", 1)[1]
    await callback.answer(f"Анализирую {symbol}...")
    await callback.message.answer(f"🔍 Анализирую `{symbol}`...")
    text = await run_analysis(symbol, detect_asset_type(symbol))
    await safe_send(callback.message.chat.id, f"◆ {text}")


@dp.callback_query(F.data.startswith("remove:"))
async def cb_remove(callback: CallbackQuery, state: FSMContext):
    if not is_allowed(callback.message.chat.id):
        await callback.answer(); return
    symbol = callback.data.split(":", 1)[1]
    wl = load_watchlist()
    if symbol in wl:
        wl.remove(symbol)
        save_watchlist(wl)
    await callback.answer(f"{symbol} удалён")
    if wl:
        try:
            await callback.message.edit_text(
                "📝 *Список наблюдения:*\n\nНажми для анализа или ❌ удалить:",
                reply_markup=watchlist_inline(wl))
        except Exception:
            await callback.message.answer("Список обновлён.", reply_markup=watchlist_inline(wl))
    else:
        try:
            await callback.message.edit_text("Список пуст.")
        except Exception:
            await callback.message.answer("Список пуст.")


# ════════════════════════════════════════════════════════════
# НЕИЗВЕСТНЫЕ СООБЩЕНИЯ
# ════════════════════════════════════════════════════════════

@dp.message()
async def unknown_message(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    cur = await state.get_state()
    if cur:
        return
    await message.answer("Используй кнопки 👇", reply_markup=main_keyboard())


# ════════════════════════════════════════════════════════════
# ЗАПУСК
# ════════════════════════════════════════════════════════════

async def main():
    logger.info("Бот запускается...")

    class _BotProxy:
        active_analyses = globals()["active_analyses"]
        async def send_to_all(self, text: str):
            for cid in ALLOWED_CHAT_IDS:
                await safe_send(cid, text)

    from scheduler import start_scheduler
    logger.info("Бот запущен")
    await asyncio.gather(
        dp.start_polling(bot, skip_updates=True),
        start_scheduler(_BotProxy()),
    )


if __name__ == "__main__":
    asyncio.run(main())
