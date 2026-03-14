import asyncio
import logging
from typing import TypeVar, Callable, Any
import random

logger = logging.getLogger("finstack")

T = TypeVar("T")

async def retry_with_backoff(
    func: Callable[..., Any],
    *args,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 10.0,
    exponential_base: float = 2.0,
    **kwargs
) -> Any:
    """
    Retry logic з експоненціальною затримкою для Claude API.
    
    Args:
        func: async функція для виконання
        max_retries: максимум спроб (default 3)
        initial_delay: початкова затримка в секундах (default 1)
        max_delay: максимальна затримка (default 10)
        exponential_base: множник для експоненціального зростання (default 2)
    
    Returns:
        Результат функції або Exception
    """
    
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            logger.debug(f"Attempt {attempt + 1}/{max_retries} for {func.__name__}")
            result = await func(*args, **kwargs)
            
            if attempt > 0:
                logger.info(f"✅ {func.__name__} succeeded on attempt {attempt + 1}")
            
            return result
            
        except Exception as e:
            last_exception = e
            
            # Не retry для деяких помилок
            if isinstance(e, ValueError) and "повернув не JSON" in str(e):
                logger.error(f"❌ Claude returned invalid JSON: {str(e)}")
                raise
            
            if attempt < max_retries - 1:
                # Експоненціальна затримка + jitter (±20%)
                delay = min(initial_delay * (exponential_base ** attempt), max_delay)
                jitter = delay * 0.2 * (random.random() - 0.5)
                actual_delay = delay + jitter
                
                logger.warning(
                    f"⚠️ {func.__name__} failed (attempt {attempt + 1}): {str(e)[:100]}. "
                    f"Retrying in {actual_delay:.2f}s..."
                )
                
                await asyncio.sleep(actual_delay)
            else:
                logger.error(f"❌ {func.__name__} failed after {max_retries} attempts: {str(e)}")
    
    raise last_exception


__all__ = ["retry_with_backoff"]
