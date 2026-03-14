import json
import logging
from typing import Dict, Any, Optional
import httpx

logger = logging.getLogger("finstack")


class SmartIntentDetector:
    """
    Розумна детекція намірів користувача.
    Замість regex-приватів, питаємо Claude що користувач хоче.
    """
    
    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001"):
        self.api_key = api_key
        self.model = model
        self.api_url = "https://api.anthropic.com/v1/messages"
    
    async def detect_intent(self, user_text: str) -> Dict[str, Any]:
        """
        Детектувати намір користувача за допомогою Claude.
        
        Returns:
            {
                "intent": "transaction" | "transfer" | "balance_setup" | "last_action" | "report",
                "confidence": 0.0-1.0,
                "reasoning": "пояснення чому це цей тип"
            }
        """
        prompt = f"""Ти помічник для фінансового боту. Аналізуй українське повідомлення користувача і визначи його намір.

Можливі наміри:
1. "transaction" - додавання витрати або доходу (наприклад: "кава 200", "зарплата 30000", "витратив 1500 на ресторан")
2. "transfer" - переказ між рахунками (наприклад: "переведи 5000 з готівки на приватбанк", "з готівки на мономе 1000")
3. "balance_setup" - встановлення або корекція балансів (наприклад: "готівка 10000, приват 5000", "баланс готівка 3000")
4. "last_action" - редагування або видалення останньої транзакції (наприклад: "видали останню", "зміни на 300", "не з готівки")
5. "report" - запит на звіт або аналіз (наприклад: "скільки витратив?", "топ категорій", "дохід за місяць")
6. "unknown" - непонятна команда

ВАЖЛИВО: Будь гнучким! Навіть якщо порядок слів незвичний - розумій намір!

Повідомлення для аналізу:
"{user_text}"

Поверни СУВОРО JSON без markdown, без пояснень:
{{
  "intent": "transaction" | "transfer" | "balance_setup" | "last_action" | "report" | "unknown",
  "confidence": 0.95,
  "reasoning": "коротке пояснення"
}}"""
        
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        
        payload = {
            "model": self.model,
            "max_tokens": 100,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(self.api_url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
            
            # Витягти текст відповіді
            content = data.get("content", [])
            if content and isinstance(content[0], dict):
                text = content[0].get("text", "")
                
                # Видалити markdown кодування
                text = text.strip()
                if text.startswith("```"):
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                    text = text.strip()
                
                result = json.loads(text)
                logger.debug(f"Intent detected: {result['intent']} (confidence: {result['confidence']})")
                return result
        
        except Exception as e:
            logger.warning(f"⚠️ Failed to detect intent: {str(e)}")
            # Fallback на unknown
            return {
                "intent": "unknown",
                "confidence": 0.0,
                "reasoning": f"Помилка детекції: {str(e)}"
            }
    
    async def should_parse_as_transfer(self, user_text: str) -> bool:
        """
        Перевірити чи це переказ між рахунками.
        Більш гнучко, ніж regex.
        """
        result = await self.detect_intent(user_text)
        return result["intent"] == "transfer"
    
    async def should_parse_as_balance_setup(self, user_text: str) -> bool:
        """Перевірити чи це встановлення балансів."""
        result = await self.detect_intent(user_text)
        return result["intent"] == "balance_setup"
    
    async def should_parse_as_last_action(self, user_text: str) -> bool:
        """Перевірити чи це редагування останньої транзакції."""
        result = await self.detect_intent(user_text)
        return result["intent"] == "last_action"
    
    async def should_parse_as_report(self, user_text: str) -> bool:
        """Перевірити чи це запит на звіт."""
        result = await self.detect_intent(user_text)
        return result["intent"] == "report"


__all__ = ["SmartIntentDetector"]
