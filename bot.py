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
dp = Dispatcher(storage=MemoryStorage())

active_analyses: Dict = {}
WATCHLIST_FILE = Path("/root/bots/market_analyst_bot/watchlist.json")


# ── FSM ───────────────────────────────────────────────────────
class Form(StatesGroup):
    waiting_symbol = State()
    waiting_add    = State()


# ── Watchlist ─────────────────────────────────────────────────
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


# ── Клавиатуры ────────────────────────────────────────────────
def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Отчёт"),          KeyboardButton(text="🎯 Сигнал дня")],
            [KeyboardButton(text="🔔 Алерты"),          KeyboardButton(text="📈 Статистика")],
            [KeyboardButton(text="🔍 Анализ монеты"),   KeyboardButton(text="📝 Мой список")],
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
        [InlineKeyboardButton(text="✏️ Другая монета", callback_data=f"type:{action}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def watchlist_inline(wl: List[str]) -> Optional[InlineKeyboardMarkup]:
    if not wl:
        return None
    rows = [[
        InlineKeyboardButton(text=f"🔍 {s}", callback_data=f"analyze:{s}"),
        InlineKeyboardButton(text="❌", callback_data=f"remove:{s}"),
    ] for s in wl]
    rows.append([InlineKeyboardButton(text="➕ Добавить ещё", callback_data="type:add")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def upgrade_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💳 Получить полный доступ", callback_data="show_upgrade"),
    ]])


# ── Утилиты ───────────────────────────────────────────────────
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
            chunks.append(buf); buf = line + "\n"
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
        from report_generator import generate_report
        return generate_report(fa, "morning")
    except Exception as e:
        logger.error(f"run_analysis {symbol}: {e}")
        return f"❌ Ошибка анализа {symbol}: {e}"


# ════════════════════════════════════════════════════════════
#  /start — продающий экран
# ════════════════════════════════════════════════════════════

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()

    # Инициализируем пользователя (даём пробный период)
    init_user(message.chat.id)
    level = get_access_level(message.chat.id)

    if level in ("paid", "admin"):
        status_line = "✅ *Подписка активна* — у тебя полный доступ\n\n"
    elif level == "trial":
        status_line = "🔓 *Пробный период активирован* — 3 дня полного доступа\n\n"
    else:
        status_line = f"🔒 *Бесплатный доступ* — {FREE_DAILY_SIGNALS} сигнал в день (без уровней)\n\n"

    await message.answer(
        "📡 *Market Pulse*\n\n"
        + status_line +
        "Бот показывает *где входить в рынок* — не новости, не индикаторы.\n"
        "Готовые торговые сценарии с точками входа, стопом и целью.\n\n"
        "Каждый день:\n"
        "🌅 *06:00* — полный разбор рынка\n"
        "🎯 *Сигнал дня* — 1-2 лучших идеи с R:R\n"
        "⚠️ *Алерты* — цена подошла к уровню → уведомление\n"
        "🌙 *21:00* — итог дня\n\n"
        "Рынки: крипто-фьючерсы, форекс, золото, серебро\n"
        "Метод: Smart Money Concepts\n\n"
        "👇 Нажми *🎯 Сигнал дня* чтобы увидеть сигнал прямо сейчас",
        reply_markup=main_keyboard(),
    )


# ════════════════════════════════════════════════════════════
#  КОМАНДЫ
# ════════════════════════════════════════════════════════════

@dp.message(Command("status"))
async def cmd_status(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    from datetime import datetime
    import pytz
    now = datetime.now(pytz.timezone("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
    wl = load_watchlist()
    monitored = list(active_analyses.keys())
    mon_str = ", ".join(f"`{s}`" for s in monitored[:8]) if monitored else "нет — запусти 📊 Отчёт"
    access_str = get_user_info(message.chat.id)

    await message.answer(
        f"🤖 *Статус*  ·  {now}\n\n"
        f"👤 {access_str}\n\n"
        f"📊 Загружено анализов: {len(active_analyses)}\n"
        f"📝 В списке: {len(wl)}\n\n"
        f"🔔 Мониторинг уровней:\n{mon_str}\n\n"
        f"_Алерты приходят автоматически при подходе цены к уровню_",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("report"))
async def cmd_report(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()

    # Полный отчёт — только для paid/trial/admin
    if not is_full_access(message.chat.id):
        await message.answer(
            "🔒 *Полный отчёт доступен по подписке*\n\n"
            "Включает разбор всех инструментов, ключевые уровни и сценарии.\n\n"
            "Используй /upgrade чтобы получить доступ.",
            reply_markup=upgrade_inline(),
        )
        return

    await message.answer("⏳ Запускаю полный анализ рынка...\nЗаймёт 2–4 минуты 🙂")
    try:
        from scheduler import run_morning_report

        class _P:
            active_analyses = globals()['active_analyses']

            async def send_to_all(self, text):
                await safe_send(message.chat.id, text)

        await run_morning_report(_P())
    except Exception as e:
        logger.error(f"/report: {e}")
        await message.answer(f"❌ Ошибка: {e}", reply_markup=main_keyboard())


@dp.message(Command("signal"))
async def cmd_signal(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    await _show_signals(message.chat.id)


@dp.message(Command("upgrade"))
async def cmd_upgrade(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    level = get_access_level(message.chat.id)
    if level in ("paid", "admin"):
        await message.answer(
            "✅ У тебя уже есть полный доступ.\n\nЕсли есть вопросы — напиши администратору.",
            reply_markup=main_keyboard(),
        )
        return
    await message.answer(PAYMENT_INFO, reply_markup=main_keyboard())


@dp.message(Command("mystatus"))
async def cmd_mystatus(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    info = get_user_info(message.chat.id)
    level = get_access_level(message.chat.id)

    extra = ""
    if level == "free":
        extra = "\n\nИспользуй /upgrade чтобы получить полный доступ."

    await message.answer(
        f"👤 *Твой статус:*\n\n{info}{extra}",
        reply_markup=main_keyboard() if level != "free" else upgrade_inline(),
    )


@dp.message(Command("stats"))
async def cmd_stats(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    from signal_engine import get_stats_text
    await safe_send(message.chat.id, get_stats_text(), reply_markup=main_keyboard())


@dp.message(Command("alerts"))
async def cmd_alerts(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    await _show_alerts(message.chat.id)


@dp.message(Command("watchlist"))
async def cmd_watchlist(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    await _show_watchlist(message.chat.id)


@dp.message(Command("analyze"))
async def cmd_analyze(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 2:
        await message.answer("Укажи символ: `/analyze BTCUSDT`", reply_markup=main_keyboard())
        return

    if not is_full_access(message.chat.id):
        await message.answer(
            "🔒 Анализ по запросу доступен по подписке.\n\nИспользуй /upgrade.",
            reply_markup=upgrade_inline(),
        )
        return

    symbol = parts[1].upper()
    await message.answer(f"🔍 Анализирую `{symbol}`...")
    text = await run_analysis(symbol, detect_asset_type(symbol))
    await safe_send(message.chat.id, f"◆  {text}")
    await message.answer("Готово.", reply_markup=main_keyboard())


@dp.message(Command("watch"))
async def cmd_watch(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 2:
        await message.answer("Использование: `/watch BTCUSDT`", reply_markup=main_keyboard())
        return
    symbol = parts[1].upper()
    wl = load_watchlist()
    if symbol in wl:
        await message.answer(f"`{symbol}` уже в списке.", reply_markup=main_keyboard())
        return
    wl.append(symbol)
    save_watchlist(wl)
    await message.answer(f"✅ `{symbol}` добавлен.", reply_markup=main_keyboard())


@dp.message(Command("unwatch"))
async def cmd_unwatch(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 2:
        await message.answer("Использование: `/unwatch BTCUSDT`", reply_markup=main_keyboard())
        return
    symbol = parts[1].upper()
    wl = load_watchlist()
    if symbol not in wl:
        await message.answer(f"`{symbol}` не найден.", reply_markup=main_keyboard())
        return
    wl.remove(symbol)
    save_watchlist(wl)
    await message.answer(f"✅ `{symbol}` удалён.", reply_markup=main_keyboard())


# ════════════════════════════════════════════════════════════
#  ADMIN — управление пользователями
# ════════════════════════════════════════════════════════════

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
        await message.answer(f"✅ Пользователь `{target_id}` добавлен в paid.")
        # Уведомляем пользователя
        try:
            await bot.send_message(
                target_id,
                "🎉 *Твоя подписка активирована!*\n\n"
                "Теперь у тебя полный доступ:\n"
                "• Полные сигналы с уровнями входа, стопом и целью\n"
                "• Утренний брифинг и вечерний итог\n"
                "• Алерты в реальном времени\n\n"
                "Нажми *🎯 Сигнал дня* прямо сейчас 👇",
            )
        except Exception:
            pass
    else:
        await message.answer("❌ Ошибка при добавлении пользователя.")


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
#  Вспомогательные функции показа
# ════════════════════════════════════════════════════════════

def _format_signal_teaser(sig) -> str:
    """
    Урезанная версия сигнала для бесплатных пользователей.
    Показывает направление и фазу, скрывает уровни.
    """
    dir_icon = "🟢" if sig.direction == "long" else "🔴"
    dir_ru   = "ЛОНГ" if sig.direction == "long" else "ШОРТ"
    asset_icon = {"crypto": "◆", "forex": "◇", "metal": "◈"}.get(sig.asset_type, "·")

    return (
        f"{asset_icon} *{sig.symbol}*  {dir_icon} *{dir_ru}*\n"
        f"\n"
        f"Фаза: {sig.phase}\n"
        f"Качество: {sig.score}/10\n"
        f"\n"
        f"Вход: `🔒 скрыто`\n"
        f"Стоп: `🔒 скрыто`\n"
        f"Цель: `🔒 скрыто`\n"
        f"R:R:  `🔒 скрыто`\n"
        f"\n"
        f"_Условие входа и уровни — в полной подписке_"
    )


async def _show_signals(chat_id: int):
    if not active_analyses:
        await bot.send_message(
            chat_id,
            "🎯 *Сигнал дня*\n\n"
            "Сначала загрузи данные — нажми *📊 Отчёт*.\n\n"
            "После этого кнопка выберет 1-2 лучших сигнала из всех инструментов.",
            reply_markup=main_keyboard(),
        )
        return

    await bot.send_message(chat_id, "🔄 Анализирую сигналы...")

    try:
        from signal_engine import get_best_signals, format_signals_block, format_signal, save_signals
        from datetime import datetime
        import pytz

        signals = get_best_signals(active_analyses, top_n=2, min_score=3.5)
        save_signals(signals)

        level = get_access_level(chat_id)

        # ── Полный доступ ─────────────────────────────────
        if level in ("admin", "paid", "trial"):
            text = format_signals_block(signals, len(active_analyses))
            await safe_send(chat_id, text, reply_markup=main_keyboard())
            return

        # ── Бесплатный доступ ─────────────────────────────
        if not signals:
            await bot.send_message(
                chat_id,
                "🎯 *Сигнал дня*\n\nСегодня нет сильных сигналов.",
                reply_markup=main_keyboard(),
            )
            return

        if not can_get_free_signal(chat_id):
            await bot.send_message(
                chat_id,
                "🔒 *Лимит бесплатных сигналов исчерпан*\n\n"
                f"Сегодня доступен {FREE_DAILY_SIGNALS} сигнал — ты его уже использовал.\n\n"
                "Завтра лимит обновится, или оформи подписку для безлимитного доступа.",
                reply_markup=upgrade_inline(),
            )
            return

        # Показываем первый сигнал — тизер без уровней
        sig = signals[0]
        now_str = datetime.now(pytz.timezone("Europe/Moscow")).strftime("%d.%m.%Y  %H:%M")
        teaser = (
            f"🎯 *СИГНАЛ ДНЯ*  ·  {now_str}\n"
            f"Проанализировано: {len(active_analyses)} инструментов\n"
            f"{'─' * 28}\n\n"
            + _format_signal_teaser(sig)
            + f"\n\n{'─' * 28}\n"
            f"_Полный сигнал с уровнями входа, стопом и целью — по подписке_"
        )
        record_signal_usage(chat_id)
        await safe_send(chat_id, teaser, reply_markup=upgrade_inline())

        if len(signals) > 1:
            await bot.send_message(
                chat_id,
                f"📌 Есть ещё {len(signals) - 1} сигнал{'а' if len(signals) - 1 > 1 else ''} "
                f"— доступны по подписке.",
            )

    except Exception as e:
        logger.error(f"_show_signals: {e}")
        await bot.send_message(chat_id, f"❌ Ошибка: {e}", reply_markup=main_keyboard())


async def _show_alerts(chat_id: int):
    if not is_full_access(chat_id):
        await bot.send_message(
            chat_id,
            "🔔 *Алерты доступны по подписке*\n\n"
            "Бот уведомляет когда цена подходит к ключевому уровню на 0.3–0.5%.\n\n"
            "С подпиской ты получаешь алерты в реальном времени по всем инструментам.",
            reply_markup=upgrade_inline(),
        )
        return

    if not active_analyses:
        await bot.send_message(
            chat_id,
            "🔔 *Алерты*\n\nСначала запусти *📊 Отчёт* — бот загрузит уровни.\n\n"
            "После этого алерты приходят автоматически каждые 5 минут,\n"
            "когда цена подходит к ключевому уровню на 0.3–0.5%.",
        )
        return

    lines = ["🔔 *Активные уровни мониторинга:*\n"]
    for sym, fa in active_analyses.items():
        price = fa.current_price or 0
        if fa.key_levels:
            sup = [l for l in fa.key_levels if l < price][-2:]
            res = [l for l in fa.key_levels if l > price][:2]
            res_str = " · ".join(f"`{l}`" for l in res) if res else "—"
            sup_str = " · ".join(f"`{l}`" for l in reversed(sup)) if sup else "—"
            lines.append(f"*{sym}* @ `{price}`\n  ▲ {res_str}\n  ▼ {sup_str}")
    lines.append("\n_Алерт = сообщение когда цена подошла к уровню_")
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
#  КНОПКИ ГЛАВНОГО МЕНЮ
# ════════════════════════════════════════════════════════════

@dp.message(F.text == "📊 Отчёт")
async def btn_report(message: Message, state: FSMContext):
    await cmd_report(message, state)


@dp.message(F.text == "🎯 Сигнал дня")
async def btn_signal(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    await _show_signals(message.chat.id)


@dp.message(F.text == "🔔 Алерты")
async def btn_alerts(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    await _show_alerts(message.chat.id)


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
            "🔒 Анализ по запросу доступен по подписке.",
            reply_markup=upgrade_inline(),
        )
        return

    await message.answer("🔍 *Выбери инструмент:*", reply_markup=quick_symbols_inline("analyze"))


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
#  FSM — ввод символа вручную
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
    await safe_send(message.chat.id, f"◆  {text}")


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
    await message.answer(f"✅ `{symbol}` добавлен в список.", reply_markup=main_keyboard())


# ════════════════════════════════════════════════════════════
#  INLINE CALLBACKS
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
        await callback.answer()
        return
    action = callback.data.split(":", 1)[1]
    await callback.answer()
    if action == "analyze":
        await state.set_state(Form.waiting_symbol)
        await callback.message.answer(
            "✏️ Введи символ:\n`BTCUSDT` / `SOLUSDT` / `EURUSD` / `XAUUSD`",
            reply_markup=cancel_keyboard(),
        )
    else:
        await state.set_state(Form.waiting_add)
        await callback.message.answer(
            "✏️ Введи символ для добавления в список:",
            reply_markup=cancel_keyboard(),
        )


@dp.callback_query(F.data.startswith("analyze:"))
async def cb_analyze(callback: CallbackQuery, state: FSMContext):
    if not is_allowed(callback.message.chat.id):
        await callback.answer()
        return

    if not is_full_access(callback.message.chat.id):
        await callback.answer("🔒 Доступно по подписке", show_alert=True)
        return

    symbol = callback.data.split(":", 1)[1]
    await callback.answer(f"Анализирую {symbol}...")
    await callback.message.answer(f"🔍 Анализирую `{symbol}`...")
    text = await run_analysis(symbol, detect_asset_type(symbol))
    await safe_send(callback.message.chat.id, f"◆  {text}")


@dp.callback_query(F.data.startswith("add:"))
async def cb_add(callback: CallbackQuery, state: FSMContext):
    if not is_allowed(callback.message.chat.id):
        await callback.answer()
        return
    symbol = callback.data.split(":", 1)[1]
    wl = load_watchlist()
    if symbol in wl:
        await callback.answer(f"{symbol} уже в списке")
        return
    wl.append(symbol)
    save_watchlist(wl)
    await callback.answer(f"✅ {symbol} добавлен")
    await callback.message.answer(
        f"✅ `{symbol}` добавлен.\nСписок: {', '.join(f'`{s}`' for s in wl)}",
        reply_markup=main_keyboard(),
    )


@dp.callback_query(F.data.startswith("remove:"))
async def cb_remove(callback: CallbackQuery, state: FSMContext):
    if not is_allowed(callback.message.chat.id):
        await callback.answer()
        return
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
#  НЕИЗВЕСТНЫЕ СООБЩЕНИЯ
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
#  ЗАПУСК
# ════════════════════════════════════════════════════════════

async def main():
    logger.info("Бот запускается...")

    class _BotProxy:
        active_analyses = globals()['active_analyses']

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
