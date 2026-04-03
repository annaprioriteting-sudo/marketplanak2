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
    ADMIN_IDS, PAYMENT_INFO, FREE_DAILY_SIGNALS,
)
from access_control import (
    init_user, get_access_level, is_full_access,
    can_get_free_signal, record_signal_usage,
    add_paid_user, remove_paid_user,
    get_user_info, list_paid_users,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp  = Dispatcher(storage=MemoryStorage())

# Единое хранилище анализов — доступно планировщику через BotProxy
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
    rows = [
        [InlineKeyboardButton(text="── Крипто ──", callback_data="noop")],
        [InlineKeyboardButton(text=s, callback_data=f"{action}:{s}") for s in crypto[:3]],
        [InlineKeyboardButton(text=s, callback_data=f"{action}:{s}") for s in crypto[2:]],
        [InlineKeyboardButton(text="✏️ Другой символ", callback_data=f"type:{action}")],
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


# ════════════════════════════════════════════════════════════
#  АНАЛИЗ МОНЕТЫ — ГЛАВНАЯ ФУНКЦИЯ (Приоритет №1)
#  Использует report_type="coin" — premium discretionary format
# ════════════════════════════════════════════════════════════

async def run_coin_analysis(symbol: str) -> str:
    """
    Полный анализ монеты в premium формате.
    Загружает все 5 таймфреймов (1W, 1D, 4H, 1H, 15m).
    Возвращает текст в стиле discretionary trader.
    """
    loop = asyncio.get_event_loop()
    try:
        from data_fetcher import fetch_bitget_all_timeframes
        tf_data = await loop.run_in_executor(None, fetch_bitget_all_timeframes, symbol)
        if not tf_data:
            return f"⚠️ {symbol}: не удалось загрузить данные. Проверь тикер."
        from analyzer import full_analysis
        fa = await loop.run_in_executor(None, full_analysis, symbol, "crypto", tf_data)
        active_analyses[symbol] = fa
        from report_generator import generate_report
        # Используем premium coin-анализ
        return generate_report(fa, "coin")
    except Exception as e:
        logger.error(f"run_coin_analysis {symbol}: {e}")
        return f"❌ Ошибка анализа {symbol}: {e}"


async def run_analysis(symbol: str, asset_type: str = "crypto") -> str:
    """Используется планировщиком и утренним отчётом — morning format."""
    loop = asyncio.get_event_loop()
    try:
        from data_fetcher import fetch_bitget_all_timeframes
        tf_data = await loop.run_in_executor(None, fetch_bitget_all_timeframes, symbol)
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
#  /start
# ════════════════════════════════════════════════════════════

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    init_user(message.chat.id)
    level = get_access_level(message.chat.id)

    if level in ("paid", "admin"):
        status_line = "✅ *Подписка активна*\n\n"
    elif level == "trial":
        status_line = "🔓 *Пробный период* — 3 дня полного доступа\n\n"
    else:
        status_line = f"🔒 *Бесплатный доступ* — {FREE_DAILY_SIGNALS} сигнал в день\n\n"

    await message.answer(
        "📡 *Market Pulse*\n\n"
        + status_line +
        "Готовые торговые сценарии с точками входа, стопом и целью.\n\n"
        "🌅 *06:00* — полный разбор рынка\n"
        "🎯 *Сигнал дня* — лучшая сделка по рынку прямо сейчас\n"
        "🔔 *Алерты* — цена у уровня → уведомление\n"
        "🌙 *21:00* — итог и план на завтра\n\n"
        "👇 Нажми *🔍 Анализ монеты* чтобы начать",
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
    wl  = load_watchlist()
    mon = ", ".join(f"`{s}`" for s in list(active_analyses.keys())[:8]) or "нет"
    await message.answer(
        f"🤖 *Статус*  ·  {now}\n\n"
        f"👤 {get_user_info(message.chat.id)}\n\n"
        f"📊 Загружено анализов: {len(active_analyses)}\n"
        f"📝 В списке: {len(wl)}\n\n"
        f"🔔 Мониторинг: {mon}\n\n"
        f"_Алерты приходят автоматически при подходе цены к уровню_",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("report"))
async def cmd_report(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    if not is_full_access(message.chat.id):
        await message.answer(
            "🔒 *Полный отчёт доступен по подписке*",
            reply_markup=upgrade_inline(),
        )
        return

    await message.answer("⏳ Запускаю полный анализ рынка...\nЗаймёт 2–4 минуты")
    try:
        from scheduler import run_morning_report

        class _P:
            @property
            def active_analyses(self):
                return active_analyses

            @active_analyses.setter
            def active_analyses(self, val):
                active_analyses.clear()
                active_analyses.update(val)

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
        await message.answer("✅ У тебя уже есть полный доступ.", reply_markup=main_keyboard())
        return
    await message.answer(PAYMENT_INFO, reply_markup=main_keyboard())


@dp.message(Command("mystatus"))
async def cmd_mystatus(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    await state.clear()
    level = get_access_level(message.chat.id)
    extra = "\n\nИспользуй /upgrade для полного доступа." if level == "free" else ""
    await message.answer(
        f"👤 *Твой статус:*\n\n{get_user_info(message.chat.id)}{extra}",
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
        await message.answer("Укажи символ: `/analyze BTCUSDT`")
        return
    if not is_full_access(message.chat.id):
        await message.answer("🔒 Анализ по запросу доступен по подписке.", reply_markup=upgrade_inline())
        return
    symbol = parts[1].upper()
    await message.answer(f"🔍 Анализирую `{symbol}`...")
    text = await run_coin_analysis(symbol)
    await safe_send(message.chat.id, text)
    await message.answer("Готово.", reply_markup=main_keyboard())


@dp.message(Command("watch"))
async def cmd_watch(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 2:
        await message.answer("Использование: `/watch BTCUSDT`")
        return
    symbol = parts[1].upper()
    wl = load_watchlist()
    if symbol not in wl:
        wl.append(symbol)
        save_watchlist(wl)
    await message.answer(f"✅ `{symbol}` добавлен.", reply_markup=main_keyboard())


@dp.message(Command("unwatch"))
async def cmd_unwatch(message: Message, state: FSMContext):
    if not is_allowed(message.chat.id):
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 2:
        await message.answer("Использование: `/unwatch BTCUSDT`")
        return
    symbol = parts[1].upper()
    wl = load_watchlist()
    if symbol in wl:
        wl.remove(symbol)
        save_watchlist(wl)
    await message.answer(f"✅ `{symbol}` удалён.", reply_markup=main_keyboard())


# ════════════════════════════════════════════════════════════
#  ADMIN
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
        await message.answer(f"✅ `{target_id}` добавлен в paid.")
        try:
            await bot.send_message(
                target_id,
                "🎉 *Подписка активирована!*\n\n"
                "Полный доступ: сигналы, анализ, алерты.\n\n"
                "Нажми *🔍 Анализ монеты* прямо сейчас 👇",
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
    ok = remove_paid_user(target_id)
    await message.answer(f"{'✅' if ok else '❌'} `{target_id}` переведён в free." if ok else "❌ Ошибка.")


@dp.message(Command("listusers"))
async def cmd_listusers(message: Message, state: FSMContext):
    if message.chat.id not in ADMIN_IDS:
        return
    paid = list_paid_users()
    if not paid:
        await message.answer("Платных пользователей нет.")
        return
    lines = ["👥 *Платные пользователи:*\n"] + [f"• `{uid}`" for uid in paid]
    await message.answer("\n".join(lines))


# ════════════════════════════════════════════════════════════
#  ПОКАЗ СИГНАЛА ДНЯ
#  Сканирует ТОП-50, выбирает 1 лучшую сделку
#  Включает 15m для точного entry layer
# ════════════════════════════════════════════════════════════

def _format_signal_teaser(sig) -> str:
    """Тизер для бесплатных пользователей."""
    d_icon = "🟢" if sig.direction == "long" else "🔴"
    d_ru   = "LONG" if sig.direction == "long" else "SHORT"
    return (
        f"◆ *{sig.symbol}*  {d_icon} *{d_ru}*\n\n"
        f"Фаза: {sig.phase}\n"
        f"Стратегия: {sig.strategy}\n"
        f"Качество: {sig.score}/10\n\n"
        f"Вход: `🔒 скрыто`\n"
        f"Стоп: `🔒 скрыто`\n"
        f"Тейк: `🔒 скрыто`\n"
        f"R:R:  `🔒 скрыто`\n\n"
        f"_Полный сигнал — по подписке_"
    )


async def _show_signals(chat_id: int):
    level = get_access_level(chat_id)

    if level == "free" and not can_get_free_signal(chat_id):
        await bot.send_message(
            chat_id,
            "🔒 *Лимит исчерпан*\n\n"
            f"Сегодня доступен {FREE_DAILY_SIGNALS} сигнал — уже использован.\n"
            "Завтра лимит обновится.",
            reply_markup=upgrade_inline(),
        )
        return

    status_msg = await bot.send_message(
        chat_id,
        "🔍 *Сканирую рынок...*\n_Загружаю ТОП-50 инструментов_",
    )

    async def update_status(text: str):
        try:
            await bot.edit_message_text(
                f"🔍 {text}", chat_id=chat_id, message_id=status_msg.message_id
            )
        except Exception:
            pass

    try:
        from signal_engine import (
            scan_market_for_best_signal, format_signal,
            format_no_signal, save_signals, get_ai_comment,
        )
        from news_fetcher import fetch_news

        # Параллельно: скан + новости
        signal_task = asyncio.create_task(
            scan_market_for_best_signal(progress_callback=update_status, top_n_scan=50)
        )
        news_task = asyncio.create_task(fetch_news(max_items=3))

        best_signal, scanned = await signal_task
        news_text = await news_task

        try:
            await bot.delete_message(chat_id, status_msg.message_id)
        except Exception:
            pass

        if not best_signal:
            no_sig = format_no_signal(scanned)
            if news_text:
                no_sig = news_text + "\n\n" + "─" * 28 + "\n\n" + no_sig
            await safe_send(chat_id, no_sig, reply_markup=main_keyboard())
            return

        if level in ("admin", "paid", "trial"):
            ai_comment = await get_ai_comment(best_signal)
            sig_text   = format_signal(best_signal, ai_comment=ai_comment)
            full_text  = (news_text + "\n\n" + "─" * 28 + "\n\n" + sig_text) if news_text else sig_text
            save_signals([best_signal])
            await safe_send(chat_id, full_text, reply_markup=main_keyboard())
        else:
            # Бесплатный тизер
            from datetime import datetime
            import pytz
            now_str = datetime.now(pytz.timezone("Europe/Moscow")).strftime("%d.%m.%Y  %H:%M")
            teaser = (
                f"🎯 *СИГНАЛ ДНЯ*  ·  {now_str}\n"
                f"Просканировано: {scanned} инструментов\n"
                f"{'─' * 28}\n\n"
                + _format_signal_teaser(best_signal)
            )
            record_signal_usage(chat_id)
            await safe_send(chat_id, teaser, reply_markup=upgrade_inline())

    except Exception as e:
        logger.error(f"_show_signals: {e}")
        try:
            await bot.delete_message(chat_id, status_msg.message_id)
        except Exception:
            pass
        await bot.send_message(chat_id, f"❌ Ошибка: {e}", reply_markup=main_keyboard())


async def _show_alerts(chat_id: int):
    if not is_full_access(chat_id):
        await bot.send_message(
            chat_id,
            "🔔 *Алерты доступны по подписке*\n\n"
            "Уведомления когда цена подходит к ключевому уровню на 0.3–0.5%.",
            reply_markup=upgrade_inline(),
        )
        return

    if not active_analyses:
        await bot.send_message(
            chat_id,
            "🔔 *Алерты*\n\nСначала загрузи данные — нажми *📊 Отчёт*.\n\n"
            "После этого алерты приходят автоматически каждые 5 минут.",
            reply_markup=main_keyboard(),
        )
        return

    lines = ["🔔 *Активные уровни:*\n"]
    for sym, fa in active_analyses.items():
        price = fa.current_price or 0
        if fa.key_levels:
            sup = [l for l in fa.key_levels if l < price][-2:]
            res = [l for l in fa.key_levels if l > price][:2]
            res_str = " · ".join(f"`{l}`" for l in res) if res else "—"
            sup_str = " · ".join(f"`{l}`" for l in reversed(sup)) if sup else "—"
            lines.append(f"*{sym}* @ `{price}`\n  ▲ {res_str}\n  ▼ {sup_str}")
    lines.append("\n_Алерт приходит когда цена подошла к уровню_")
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
    await message.answer(
        "🔍 *Выбери монету или введи тикер:*",
        reply_markup=quick_symbols_inline("analyze")
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
#  FSM — ввод символа
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
    text = await run_coin_analysis(symbol)   # ← premium coin format
    await safe_send(message.chat.id, text)


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
            "✏️ Введи тикер:\n`BTCUSDT` / `SOLUSDT` / `BNBUSDT` / `PEPEUSDT`",
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
    text = await run_coin_analysis(symbol)   # ← premium coin format
    await safe_send(callback.message.chat.id, text)


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
    if await state.get_state():
        return
    await message.answer("Используй кнопки 👇", reply_markup=main_keyboard())


# ════════════════════════════════════════════════════════════
#  ЗАПУСК
# ════════════════════════════════════════════════════════════

async def main():
    logger.info("Бот запускается...")

    class BotProxy:
        @property
        def active_analyses(self):
            return active_analyses

        @active_analyses.setter
        def active_analyses(self, val: dict):
            active_analyses.clear()
            active_analyses.update(val)

        async def send_to_all(self, text: str):
            for cid in ALLOWED_CHAT_IDS:
                await safe_send(cid, text)

    from scheduler import start_scheduler
    logger.info("Бот запущен")
    await asyncio.gather(
        dp.start_polling(bot, skip_updates=True),
        start_scheduler(BotProxy()),
    )


if __name__ == "__main__":
    asyncio.run(main())
