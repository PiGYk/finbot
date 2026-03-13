import asyncio
import json
import os
import time
from threading import Lock
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
from datetime import datetime


class ReminderService:
    def __init__(
        self,
        file_path: str = "/app/data/reminders.json",
        timezone_name: str = "Europe/Kyiv",
        poll_seconds: int = 30,
    ) -> None:
        self.file_path = file_path
        self.timezone_name = timezone_name
        self.poll_seconds = max(10, int(poll_seconds))
        self._lock = Lock()
        self._loaded = False
        self._data: Dict[str, Any] = {"reminders": []}

    def _ensure_dir(self) -> None:
        folder = os.path.dirname(self.file_path)
        if folder:
            os.makedirs(folder, exist_ok=True)

    def _load(self) -> None:
        if self._loaded:
            return

        with self._lock:
            if self._loaded:
                return

            self._ensure_dir()
            if os.path.exists(self.file_path):
                try:
                    with open(self.file_path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict) and isinstance(loaded.get("reminders"), list):
                        self._data = loaded
                except Exception:
                    self._data = {"reminders": []}

            self._loaded = True

    def _save(self) -> None:
        self._ensure_dir()
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def count(self) -> int:
        self._load()
        return len(self._data.get("reminders", []))

    def create_daily_reminder(self, chat_id: int, text: str, hour: int, minute: int) -> Dict[str, Any]:
        self._load()

        reminder = {
            "id": int(time.time() * 1000),
            "chat_id": int(chat_id),
            "kind": "daily",
            "text": str(text).strip(),
            "hour": int(hour),
            "minute": int(minute),
            "enabled": True,
            "last_fired_date": None,
            "created_at": datetime.now(ZoneInfo(self.timezone_name)).isoformat(),
        }

        with self._lock:
            self._data.setdefault("reminders", []).append(reminder)
            self._save()

        return reminder

    def format_created_result(self, reminder: Dict[str, Any]) -> str:
        return (
            f"Створив нагадування.\n"
            f"Щодня о {int(reminder['hour']):02d}:{int(reminder['minute']):02d}\n"
            f"Текст: {reminder['text']}"
        )

    async def run_forever(self, send_message_func) -> None:
        while True:
            try:
                await self.process_due(send_message_func)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print("REMINDER_LOOP_ERROR =", repr(e))

            await asyncio.sleep(self.poll_seconds)

    async def process_due(self, send_message_func) -> None:
        self._load()

        tz = ZoneInfo(self.timezone_name)
        now = datetime.now(tz)
        today_str = now.strftime("%Y-%m-%d")

        changed = False
        reminders = self._data.get("reminders", [])

        for reminder in reminders:
            if not reminder.get("enabled", True):
                continue

            if reminder.get("kind") != "daily":
                continue

            hour = int(reminder.get("hour", 0))
            minute = int(reminder.get("minute", 0))
            last_fired_date = reminder.get("last_fired_date")

            is_due_today = (now.hour > hour) or (now.hour == hour and now.minute >= minute)

            if is_due_today and last_fired_date != today_str:
                chat_id = int(reminder["chat_id"])
                text = str(reminder["text"]).strip()
                await send_message_func(chat_id, f"Нагадування:\n{text}")
                reminder["last_fired_date"] = today_str
                changed = True

        if changed:
            with self._lock:
                self._save()
