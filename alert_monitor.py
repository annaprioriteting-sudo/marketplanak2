# ============================================================
#  alert_monitor.py — Мониторинг алертов
#  Следит за приближением цены к ключевым уровням
# ============================================================

import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from config import (
    ALERT_THRESHOLD_DEFAULT, ALERT_THRESHOLD_BY_SYMBOL,
    ALERT_COOLDOWN_MINUTES, STATE_FILE,
    FOREX_METALS_SYMBOLS,
)
from analyzer import FullAnalysis
from data_fetcher import get_current_price_crypto, get_current_price_forex

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  Хранилище состояния алертов (JSON-файл)
# ════════════════════════════════════════════════════════════

class AlertState:
    """Хранит информацию о последних сработавших алертах."""

    def __init__(self, filepath: str = STATE_FILE):
        self.filepath = Path(filepath)
        self._state: Dict = self._load()

    def _load(self) -> dict:
        if self.filepath.exists():
            try:
                with open(self.filepath, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save(self):
        with open(self.filepath, "w") as f:
            json.dump(self._state, f, indent=2)

    def _key(self, symbol: str, level: float) -> str:
        return f"{symbol}::{round(level, 6)}"

    def was_alerted_recently(self, symbol: str, level: float) -> bool:
        key = self._key(symbol, level)
        if key not in self._state:
            return False
        last_time = datetime.fromisoformat(self._state[key])
        return datetime.now() - last_time < timedelta(minutes=ALERT_COOLDOWN_MINUTES)

    def mark_alerted(self, symbol: str, level: float):
        key = self._key(symbol, level)
        self._state[key] = datetime.now().isoformat()
        self._save()

    def cleanup_old(self):
        """Удаляет записи старше 24 часов."""
        cutoff = datetime.now() - timedelta(hours=24)
        self._state = {
            k: v for k, v in self._state.items()
            if datetime.fromisoformat(v) > cutoff
        }
        self._save()


# Глобальный объект состояния
alert_state = AlertState()


# ════════════════════════════════════════════════════════════
#  Логика проверки алертов
# ════════════════════════════════════════════════════════════

def get_threshold(symbol: str) -> float:
    return ALERT_THRESHOLD_BY_SYMBOL.get(symbol, ALERT_THRESHOLD_DEFAULT)


def check_proximity(symbol: str, current_price: float, key_levels: List[float]) -> List[Tuple[float, float]]:
    """
    Возвращает список (уровень, расстояние_%), к которым цена приближается.
    Фильтрует уже алертованные в течение cooldown-периода.
    """
    threshold = get_threshold(symbol)
    triggered = []

    for level in key_levels:
        if level <= 0:
            continue
        distance = abs(current_price - level) / level
        if distance <= threshold:
            if not alert_state.was_alerted_recently(symbol, level):
                triggered.append((level, distance * 100))

    return triggered


async def fetch_price(symbol: str, asset_type: str) -> Optional[float]:
    """Получает текущую цену асинхронно (запуск синхронных функций в thread pool)."""
    loop = asyncio.get_event_loop()
    if asset_type == "crypto":
        price = await loop.run_in_executor(None, get_current_price_crypto, symbol)
    else:
        price = await loop.run_in_executor(None, get_current_price_forex, symbol)
    return price


# ════════════════════════════════════════════════════════════
#  Основной цикл мониторинга алертов
# ════════════════════════════════════════════════════════════

async def monitor_alerts(
    active_analyses: Dict[str, FullAnalysis],
    send_alert_callback,
) -> None:
    """
    Периодически проверяет цены и отправляет алерты.
    active_analyses: {symbol: FullAnalysis}
    send_alert_callback: async функция (fa, level, distance_pct)
    """
    logger.info("Запуск мониторинга алертов...")
    alert_state.cleanup_old()

    for symbol, fa in active_analyses.items():
        try:
            current_price = await fetch_price(symbol, fa.asset_type)
            if current_price is None:
                logger.warning(f"Не удалось получить цену для {symbol}")
                continue

            # Обновляем текущую цену в анализе
            fa.current_price = current_price

            # Проверяем приближение к уровням
            triggered = check_proximity(symbol, current_price, fa.key_levels)

            for level, distance_pct in triggered:
                logger.info(f"Алерт: {symbol} @ {current_price:.4f} → уровень {level:.4f} ({distance_pct:.2f}%)")
                try:
                    await send_alert_callback(fa, level, distance_pct)
                    alert_state.mark_alerted(symbol, level)
                except Exception as e:
                    logger.error(f"Ошибка отправки алерта {symbol}: {e}")

            # Небольшая пауза между символами
            await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"Ошибка мониторинга {symbol}: {e}")

    logger.info("Цикл мониторинга завершён.")
