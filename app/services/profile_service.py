import json
import os
from threading import Lock
from typing import Any, Dict, List, Optional


class ProfileService:
    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self._lock = Lock()
        self._loaded = False
        self._data: Dict[str, Any] = {
            "profiles": [],
            "chat_access": {},
            "chat_bindings": {},
        }

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
                with open(self.file_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)

                if isinstance(loaded, dict):
                    self._data["profiles"] = loaded.get("profiles", [])
                    self._data["chat_access"] = loaded.get("chat_access", {})
                    self._data["chat_bindings"] = loaded.get("chat_bindings", {})

            self._loaded = True

    def _save(self) -> None:
        self._ensure_dir()
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def list_profiles(self) -> List[Dict[str, Any]]:
        self._load()
        return list(self._data.get("profiles", []))

    def count_profiles(self) -> int:
        return len(self.list_profiles())

    def count_bindings(self) -> int:
        self._load()
        return len(self._data.get("chat_bindings", {}))

    def get_profile(self, profile_id: str) -> Optional[Dict[str, Any]]:
        self._load()
        for profile in self._data.get("profiles", []):
            if profile.get("profile_id") == profile_id:
                return dict(profile)
        return None

    def get_allowed_profile_ids_for_chat(self, chat_id: int) -> List[str]:
        self._load()
        return list(self._data.get("chat_access", {}).get(str(chat_id), []))

    def list_allowed_profiles_for_chat(self, chat_id: int) -> List[Dict[str, Any]]:
        allowed_ids = set(self.get_allowed_profile_ids_for_chat(chat_id))
        if not allowed_ids:
            return []

        result = []
        for profile in self.list_profiles():
            if profile.get("profile_id") in allowed_ids:
                result.append(profile)
        return result

    def get_bound_profile_id(self, chat_id: int) -> Optional[str]:
        self._load()
        chat_id_str = str(chat_id)
        profile_id = self._data.get("chat_bindings", {}).get(chat_id_str)

        if not profile_id:
            return None

        allowed_ids = set(self.get_allowed_profile_ids_for_chat(chat_id))
        if profile_id not in allowed_ids:
            return None

        return profile_id

    def list_bound_chat_ids_for_profile(self, profile_id: str) -> List[int]:
        self._load()
        result: List[int] = []
        bindings = self._data.get("chat_bindings", {})
        for raw_chat_id, bound_profile_id in bindings.items():
            if bound_profile_id != profile_id:
                continue
            try:
                result.append(int(raw_chat_id))
            except (TypeError, ValueError):
                continue
        return result

    def bind_chat_to_profile(self, chat_id: int, profile_id: str) -> None:
        self._load()

        allowed_ids = set(self.get_allowed_profile_ids_for_chat(chat_id))
        if profile_id not in allowed_ids:
            raise ValueError("Цей chat_id не має доступу до вибраного профілю")

        with self._lock:
            self._data.setdefault("chat_bindings", {})[str(chat_id)] = profile_id
            self._save()

    def format_start_text(self, chat_id: int) -> str:
        allowed_profiles = self.list_allowed_profiles_for_chat(chat_id)

        if not allowed_profiles:
            return (
                "Доступ ще не налаштовано.\n"
                f"Твій chat_id: {chat_id}\n"
                "Додай цей chat_id у profiles.json, і тоді я покажу доступні профілі."
            )

        current_profile_id = self.get_bound_profile_id(chat_id)
        current_profile = self.get_profile(current_profile_id) if current_profile_id else None

        lines = [
            f"Твій chat_id: {chat_id}",
            "",
            "Оберіть профіль обліку:",
        ]

        if current_profile:
            lines.insert(1, f"Поточний профіль: {current_profile.get('title', current_profile_id)}")

        return "\n".join(lines)

    def build_profile_keyboard(self, chat_id: int) -> Optional[Dict[str, Any]]:
        allowed_profiles = self.list_allowed_profiles_for_chat(chat_id)
        if not allowed_profiles:
            return None

        inline_keyboard = []
        for profile in allowed_profiles:
            inline_keyboard.append([
                {
                    "text": profile.get("title", profile.get("profile_id")),
                    "callback_data": f"bind_profile:{profile.get('profile_id')}",
                }
            ])

        return {"inline_keyboard": inline_keyboard}
