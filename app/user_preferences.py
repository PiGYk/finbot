"""
User Preferences Storage: зберігання вибору користувача (обраний рахунок).

На розробці: JSON file
На продакшене: можна мігрувати в БД
"""

import json
import asyncio
from pathlib import Path
from typing import Optional

PREFERENCES_FILE = Path("/opt/finstack/data/bot/user_preferences.json")


class UserPreferencesStore:
    """Зберігання preference користувачів (обраний рахунок, мова, тощо)."""
    
    def __init__(self):
        self._data = {}
        self._lock = asyncio.Lock()
        self._load_from_file()
    
    def _load_from_file(self):
        """Завантажити preferences з файлу."""
        if PREFERENCES_FILE.exists():
            try:
                with open(PREFERENCES_FILE, 'r', encoding='utf-8') as f:
                    self._data = json.load(f)
            except Exception as e:
                print(f"Error loading preferences: {e}")
                self._data = {}
        else:
            self._data = {}
            # Створити директорію якщо її нема
            PREFERENCES_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    def _save_to_file(self):
        """Зберегти preferences у файл."""
        try:
            PREFERENCES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(PREFERENCES_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving preferences: {e}")
    
    async def set_preferred_account(self, chat_id: int, account_id: int) -> None:
        """Встановити обраний рахунок для користувача."""
        async with self._lock:
            if str(chat_id) not in self._data:
                self._data[str(chat_id)] = {}
            
            self._data[str(chat_id)]["preferred_account_id"] = account_id
            self._save_to_file()
    
    async def get_preferred_account(self, chat_id: int) -> Optional[int]:
        """Отримати обраний рахунок користувача."""
        async with self._lock:
            prefs = self._data.get(str(chat_id), {})
            return prefs.get("preferred_account_id")
    
    async def set_preferred_account_name(self, chat_id: int, account_name: str) -> None:
        """Зберегти також назву рахунку для відображення."""
        async with self._lock:
            if str(chat_id) not in self._data:
                self._data[str(chat_id)] = {}
            
            self._data[str(chat_id)]["preferred_account_name"] = account_name
            self._save_to_file()
    
    async def get_preferred_account_name(self, chat_id: int) -> Optional[str]:
        """Отримати назву обраного рахунку."""
        async with self._lock:
            prefs = self._data.get(str(chat_id), {})
            return prefs.get("preferred_account_name")


# Глобальний instance
user_preferences = UserPreferencesStore()
