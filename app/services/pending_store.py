import asyncio
from typing import Any, Dict, Optional


class PendingStore:
    def __init__(self) -> None:
        self._data: Dict[int, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def set(self, chat_id: int, kind: str, payload: dict) -> None:
        async with self._lock:
            self._data[chat_id] = {
                "kind": kind,
                "payload": payload,
            }

    async def get(self, chat_id: int) -> Optional[dict]:
        async with self._lock:
            return self._data.get(chat_id)

    async def clear(self, chat_id: int) -> None:
        async with self._lock:
            self._data.pop(chat_id, None)
