# ============================================================
#  access_control.py — Управление доступом
#  Бесплатно: 1 сигнал в день (без уровней)
#  Платно: всё, алерты, полный анализ
#  Пробный период: TRIAL_DAYS дней с первого /start
# ============================================================

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config import (
    ADMIN_IDS,
    PAID_USERS_STATIC,
    TRIAL_DAYS,
    FREE_DAILY_SIGNALS,
    ACCESS_FILE,
)

logger = logging.getLogger(__name__)
ACCESS_PATH = Path(ACCESS_FILE)


# ════════════════════════════════════════════════════════════
#  Хранилище состояния доступа
# ════════════════════════════════════════════════════════════
#
#  Формат JSON:
#  {
#    "123456789": {
#      "status": "paid" | "trial" | "free",
#      "trial_until": "2026-04-05T00:00:00",   # только для trial
#      "paid_since":  "2026-04-02T00:00:00",   # только для paid
#      "daily": {
#        "2026-04-02": 1   # количество использованных сигналов сегодня
#      }
#    }
#  }
# ════════════════════════════════════════════════════════════

def _load() -> dict:
    if ACCESS_PATH.exists():
        try:
            return json.loads(ACCESS_PATH.read_text())
        except Exception:
            pass
    return {}


def _save(data: dict):
    try:
        ACCESS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.error(f"access_control _save: {e}")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ════════════════════════════════════════════════════════════
#  Инициализация пользователя
# ════════════════════════════════════════════════════════════

def init_user(chat_id: int) -> dict:
    """
    Создаёт запись для нового пользователя с пробным периодом.
    Если пользователь уже есть — ничего не делает.
    Возвращает запись пользователя.
    """
    data = _load()
    key = str(chat_id)

    if key not in data:
        trial_until = (datetime.now() + timedelta(days=TRIAL_DAYS)).isoformat()
        data[key] = {
            "status": "trial",
            "trial_until": trial_until,
            "daily": {},
        }
        _save(data)
        logger.info(f"Новый пользователь {chat_id}, пробный период до {trial_until[:10]}")

    return data[key]


# ════════════════════════════════════════════════════════════
#  Проверка уровня доступа
# ════════════════════════════════════════════════════════════

def get_access_level(chat_id: int) -> str:
    """
    Возвращает: "admin" | "paid" | "trial" | "free"
    """
    if chat_id in ADMIN_IDS:
        return "admin"

    if chat_id in PAID_USERS_STATIC:
        return "paid"

    data = _load()
    key = str(chat_id)
    if key not in data:
        return "free"

    record = data[key]
    status = record.get("status", "free")

    if status == "paid":
        return "paid"

    if status == "trial":
        trial_until_str = record.get("trial_until", "")
        try:
            trial_until = datetime.fromisoformat(trial_until_str)
            if datetime.now() < trial_until:
                return "trial"
            else:
                # Пробный период истёк → понижаем до free
                data[key]["status"] = "free"
                _save(data)
                return "free"
        except Exception:
            return "free"

    return "free"


def is_full_access(chat_id: int) -> bool:
    """Paid, trial или admin — полный доступ."""
    return get_access_level(chat_id) in ("admin", "paid", "trial")


# ════════════════════════════════════════════════════════════
#  Лимит сигналов для free-пользователей
# ════════════════════════════════════════════════════════════

def can_get_free_signal(chat_id: int) -> bool:
    """Проверяет, не исчерпал ли free-пользователь дневной лимит."""
    data = _load()
    key = str(chat_id)
    today = _today()

    if key not in data:
        return True

    used = data[key].get("daily", {}).get(today, 0)
    return used < FREE_DAILY_SIGNALS


def record_signal_usage(chat_id: int):
    """Записывает использование сигнала (для free-пользователей)."""
    data = _load()
    key = str(chat_id)
    today = _today()

    if key not in data:
        data[key] = {"status": "free", "daily": {}}

    if "daily" not in data[key]:
        data[key]["daily"] = {}

    data[key]["daily"][today] = data[key]["daily"].get(today, 0) + 1
    _save(data)


def signals_used_today(chat_id: int) -> int:
    """Сколько сигналов использовано сегодня."""
    data = _load()
    key = str(chat_id)
    today = _today()
    return data.get(key, {}).get("daily", {}).get(today, 0)


# ════════════════════════════════════════════════════════════
#  Управление платными пользователями (admin)
# ════════════════════════════════════════════════════════════

def add_paid_user(chat_id: int) -> bool:
    """Добавляет пользователя в paid. Возвращает True если успешно."""
    try:
        data = _load()
        key = str(chat_id)
        if key not in data:
            data[key] = {}
        data[key]["status"] = "paid"
        data[key]["paid_since"] = datetime.now().isoformat()
        _save(data)
        logger.info(f"Пользователь {chat_id} добавлен в paid")
        return True
    except Exception as e:
        logger.error(f"add_paid_user {chat_id}: {e}")
        return False


def remove_paid_user(chat_id: int) -> bool:
    """Переводит пользователя с paid в free."""
    try:
        data = _load()
        key = str(chat_id)
        if key in data:
            data[key]["status"] = "free"
            data[key].pop("paid_since", None)
            _save(data)
        logger.info(f"Пользователь {chat_id} переведён в free")
        return True
    except Exception as e:
        logger.error(f"remove_paid_user {chat_id}: {e}")
        return False


def get_user_info(chat_id: int) -> str:
    """Текстовая информация о статусе пользователя."""
    level = get_access_level(chat_id)
    data = _load()
    key = str(chat_id)
    record = data.get(key, {})

    if level == "admin":
        return "👑 Администратор"

    if level == "paid":
        since = record.get("paid_since", "")[:10] if record else ""
        return f"✅ Подписка активна" + (f" (с {since})" if since else "")

    if level == "trial":
        until = record.get("trial_until", "")[:10] if record else ""
        days_left = 0
        if until:
            try:
                days_left = (datetime.fromisoformat(record["trial_until"]) - datetime.now()).days + 1
            except Exception:
                pass
        return f"🔓 Пробный период — осталось {max(days_left, 1)} дн."

    # free
    used = signals_used_today(chat_id)
    left = max(FREE_DAILY_SIGNALS - used, 0)
    return f"🔒 Бесплатный доступ — сигналов сегодня: {left}/{FREE_DAILY_SIGNALS}"


def list_paid_users() -> list:
    """Список всех paid-пользователей из файла."""
    data = _load()
    return [int(k) for k, v in data.items() if v.get("status") == "paid"]
