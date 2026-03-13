import asyncio
import json
import os
import time
from datetime import datetime
from threading import Lock
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo


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

    def _next_id(self) -> int:
        return int(time.time() * 1000)

    def _normalize_query(self, query: Optional[str]) -> str:
        return str(query or "").strip().lower()

    def list_reminders(self, chat_id: int, include_disabled: bool = True) -> List[Dict[str, Any]]:
        self._load()
        reminders = [r for r in self._data.get("reminders", []) if int(r.get("chat_id", 0)) == int(chat_id)]
        reminders.sort(key=lambda item: (int(item.get("hour", 0)), int(item.get("minute", 0)), int(item.get("id", 0))))
        if include_disabled:
            return [dict(item) for item in reminders]
        return [dict(item) for item in reminders if item.get("enabled", True)]

    def create_daily_reminder(self, chat_id: int, text: str, hour: int, minute: int) -> Dict[str, Any]:
        self._load()

        reminder = {
            "id": self._next_id(),
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

        return dict(reminder)

    def _resolve_target_index(self, reminders: List[Dict[str, Any]], target_index: Optional[int]) -> Optional[int]:
        if target_index is None:
            return None
        idx = int(target_index) - 1
        if idx < 0 or idx >= len(reminders):
            raise ValueError(f"Нагадування №{target_index} не існує")
        return idx

    def _match_indices(self, reminders: List[Dict[str, Any]], target_text: Optional[str]) -> List[int]:
        query = self._normalize_query(target_text)
        if not query:
            return []

        matches: List[int] = []
        for idx, reminder in enumerate(reminders):
            hay = f"{reminder.get('text', '')} {int(reminder.get('hour', 0)):02d}:{int(reminder.get('minute', 0)):02d}".lower()
            if query in hay:
                matches.append(idx)
        return matches

    def resolve_reminder(
        self,
        chat_id: int,
        target_index: Optional[int] = None,
        target_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        reminders = self.list_reminders(chat_id, include_disabled=True)
        if not reminders:
            raise ValueError("У тебе ще немає нагадувань")

        idx = self._resolve_target_index(reminders, target_index)
        if idx is not None:
            return reminders[idx]

        matches = self._match_indices(reminders, target_text)
        if len(matches) == 1:
            return reminders[matches[0]]
        if len(matches) > 1:
            raise ValueError("Знайшов кілька схожих нагадувань. Уточни номер або текст точніше.")

        if target_text:
            raise ValueError("Не знайшов нагадування за таким описом")

        return reminders[-1]

    def update_reminder(
        self,
        chat_id: int,
        target_index: Optional[int] = None,
        target_text: Optional[str] = None,
        new_text: Optional[str] = None,
        hour: Optional[int] = None,
        minute: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        self._load()
        reminders = self._data.get("reminders", [])
        resolved = self.resolve_reminder(chat_id, target_index=target_index, target_text=target_text)
        target_id = int(resolved["id"])

        with self._lock:
            for reminder in reminders:
                if int(reminder.get("id", 0)) != target_id:
                    continue
                before = dict(reminder)

                if new_text is not None:
                    clean = str(new_text).strip()
                    if clean:
                        reminder["text"] = clean
                if hour is not None:
                    reminder["hour"] = int(hour)
                if minute is not None:
                    reminder["minute"] = int(minute)
                if enabled is not None:
                    reminder["enabled"] = bool(enabled)
                if hour is not None or minute is not None:
                    reminder["last_fired_date"] = None
                self._save()
                return {"before": before, "after": dict(reminder)}

        raise ValueError("Не зміг оновити нагадування")

    def delete_reminder(
        self,
        chat_id: int,
        target_index: Optional[int] = None,
        target_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._load()
        reminders = self._data.get("reminders", [])
        resolved = self.resolve_reminder(chat_id, target_index=target_index, target_text=target_text)
        target_id = int(resolved["id"])

        with self._lock:
            new_items = [item for item in reminders if int(item.get("id", 0)) != target_id]
            if len(new_items) == len(reminders):
                raise ValueError("Не зміг видалити нагадування")
            self._data["reminders"] = new_items
            self._save()

        return resolved

    def format_created_result(self, reminder: Dict[str, Any]) -> str:
        return (
            f"Створив нагадування.\n"
            f"Щодня о {int(reminder['hour']):02d}:{int(reminder['minute']):02d}\n"
            f"Текст: {reminder['text']}"
        )

    def format_reminder_short(self, reminder: Dict[str, Any], index: Optional[int] = None) -> str:
        prefix = f"{index}. " if index is not None else ""
        status = "✅" if reminder.get("enabled", True) else "⏸"
        return (
            f"{prefix}{status} {int(reminder['hour']):02d}:{int(reminder['minute']):02d}"
            f" | {reminder.get('text', '')}"
        )

    def format_list(self, reminders: List[Dict[str, Any]]) -> str:
        if not reminders:
            return "У тебе ще немає нагадувань."
        lines = ["Твої нагадування:"]
        for idx, reminder in enumerate(reminders, start=1):
            lines.append(self.format_reminder_short(reminder, idx))
        return "\n".join(lines)

    def format_updated_result(self, payload: Dict[str, Any]) -> str:
        before = payload["before"]
        after = payload["after"]
        lines = ["Оновив нагадування:"]
        lines.append(f"• Було: {self.format_reminder_short(before)}")
        lines.append(f"• Стало: {self.format_reminder_short(after)}")
        return "\n".join(lines)

    def format_deleted_result(self, reminder: Dict[str, Any]) -> str:
        return f"Видалив нагадування:\n• {self.format_reminder_short(reminder)}"

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
