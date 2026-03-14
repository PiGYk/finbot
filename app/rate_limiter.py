import time
import logging
from typing import Dict, Tuple
from threading import Lock
from collections import defaultdict

logger = logging.getLogger("finstack")

class RateLimiter:
    """Rate limiter для Claude API та Firefly запитів."""
    
    def __init__(self, max_requests_per_minute: int = 10):
        """
        Args:
            max_requests_per_minute: максимум запитів на користувача за хвилину
        """
        self.max_requests = max_requests_per_minute
        self._user_requests: Dict[int, list] = defaultdict(list)  # chat_id -> [timestamp1, timestamp2, ...]
        self._lock = Lock()
    
    def is_rate_limited(self, chat_id: int) -> Tuple[bool, float]:
        """
        Перевірити чи користувач перевищив rate limit.
        
        Returns:
            (is_limited: bool, wait_seconds: float)
        """
        with self._lock:
            now = time.time()
            minute_ago = now - 60
            
            # Видалити старі запити (старше 1 хвилини)
            self._user_requests[chat_id] = [
                ts for ts in self._user_requests[chat_id] if ts > minute_ago
            ]
            
            request_count = len(self._user_requests[chat_id])
            
            if request_count >= self.max_requests:
                # Знайти скільки секунд чекати до найстарішого запиту
                oldest_request = self._user_requests[chat_id][0]
                wait_time = (oldest_request + 60) - now
                return True, max(wait_time, 0)
            
            # Додати новий запит
            self._user_requests[chat_id].append(now)
            return False, 0.0
    
    def check_and_wait(self, chat_id: int) -> bool:
        """
        Перевірити rate limit. Якщо обмежено, записати у логи.
        
        Returns:
            True якщо дозволено, False якщо обмежено
        """
        is_limited, wait_time = self.is_rate_limited(chat_id)
        
        if is_limited:
            logger.warning(
                f"⚠️ Rate limit для chat_id {chat_id}: "
                f"вичерпано {self.max_requests} запитів за хвилину. "
                f"Потрібно чекати {wait_time:.1f}s"
            )
            return False
        
        return True

# Глобальні лімітери для Claude та Firefly
claude_limiter = RateLimiter(max_requests_per_minute=10)
firefly_limiter = RateLimiter(max_requests_per_minute=20)

__all__ = ["RateLimiter", "claude_limiter", "firefly_limiter"]
