import json
import logging
from typing import Dict, List, Any, Optional
import httpx

logger = logging.getLogger("finstack")


class ReceiptEnhancer:
    """
    Покращена категоризація та деталізація чеків.
    Використовує Claude для кращого розпізнавання позицій у чеках.
    """
    
    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001"):
        self.api_key = api_key
        self.model = model
        self.api_url = "https://api.anthropic.com/v1/messages"
    
    # Розширений каталог магазинів та їх типових товарів
    STORE_SPECIFICS = {
        "коло": {
            "name": "КОЛО (мережа магазинів)",
            "common_categories": ["Цигарки", "Кава", "Снеки", "Напої", "Гігієна"],
            "description": "Мережа АЗС та місто-магазинів - продаються цигарки, кава, снеки, напої",
            "hints": ["heets", "terea", "сигар", "кава", "cappuccino", "latte", "espresso", "chips", "напой", "вода"],
        },
        "wog": {
            "name": "WOG (АЗС)",
            "common_categories": ["Пальне", "Кава", "Снеки", "Напої"],
            "description": "АЗС - паливо, кава, снеки",
            "hints": ["a95", "a92", "diesel", "бензин", "кава", "cappuccino"],
        },
        "okko": {
            "name": "OKKO (АЗС)",
            "common_categories": ["Пальне", "Кава", "Снеки"],
            "description": "АЗС - паливо, кава, снеки",
        },
        "auchan": {
            "name": "Auchan (супермаркет)",
            "common_categories": ["Продукти", "Гігієна", "Аптека", "Побутова хімія"],
        },
        "metro": {
            "name": "METRO (супермаркет)",
            "common_categories": ["Продукти", "Алкоголь", "Гігієна"],
        },
    }
    
    # Часто купувані позиції та їх категорії
    HIGH_PRIORITY_KEYWORDS = {
        "Цигарки": [
            "heets", "terea", "neo", "iqos", "стік", "стіки", "сигар", "цигар", "тютюн", 
            "вейп", "табак", "smoke", "cigarette", "marlboro", "dunhill", "lm", "vogue"
        ],
        "Пальне": [
            "a95", "a-95", "a92", "a-92", "дизель", "diesel", "бензин", "lpg", "пальне", 
            "fuel", "евро95", "euro95", "ai-95", "ai-92", "95 octane", "92 octane"
        ],
        "Кава": [
            "cappuccino", "капучино", "latte", "латте", "espresso", "еспресо", "americano", 
            "американо", "coffee", "кава", "macchiato", "мокко", "mocha", "flat white"
        ],
        "Снеки": [
            "chips", "чіпси", "сухарики", "насіння", "попкорн", "крекер", "соломка", 
            "арахіс", "мигдаль", "горіхи", "драже", "мюслі", "гранола"
        ],
        "Вода": [
            "моршинська", "borjomi", "bonaqua", "мінеральна", "вода", "aqua", "aqva", 
            "agua", "вода питна", "carbonated water", "негазована"
        ],
    }
    
    async def enhance_receipt_categories(
        self,
        items: List[Dict[str, Any]],
        merchant: str,
        model_category_fallback: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Покращити категоризацію позицій у чеку за допомогою Claude.
        
        Args:
            items: список позицій з назвами та цінами
            merchant: назва магазину (наприклад "КОЛО")
            model_category_fallback: категорія від моделі як fallback
        
        Returns:
            Список позицій з покращеними категоріями
        """
        
        # Спочатку перевірити high-priority keywords
        for item in items:
            item_name_lower = item.get("name", "").lower()
            
            # Шукати за точним совпаданням в HIGH_PRIORITY_KEYWORDS
            found_category = None
            for category, keywords in self.HIGH_PRIORITY_KEYWORDS.items():
                if any(kw in item_name_lower for kw in keywords):
                    found_category = category
                    break
            
            if found_category:
                item["category"] = found_category
                item["category_confidence"] = "high"
                item["category_source"] = "keyword_match"
                continue
            
            # Якщо не знайшов - залишити дефолтну категорію
            if "category" not in item:
                item["category"] = "Інше"
            
            item["category_confidence"] = "default"
            item["category_source"] = "model"
        
        # Якщо все ще є невизначені позиції - питаємо Claude
        undefined_items = [i for i in items if items[i].get("category_source") == "model"]
        if undefined_items:
            enhanced = await self._claude_enhance_categories(undefined_items, merchant)
            for i, enhanced_item in enumerate(enhanced):
                items[undefined_items[i]]["category"] = enhanced_item["category"]
                items[undefined_items[i]]["category_confidence"] = "claude"
                items[undefined_items[i]]["category_source"] = "claude_ai"
        
        return items
    
    async def _claude_enhance_categories(
        self,
        items: List[Dict[str, Any]],
        merchant: str,
    ) -> List[Dict[str, Any]]:
        """Попросити Claude точніше розпізнати категорії."""
        
        items_text = "\n".join([
            f"- {item['name']}: {item['total_price']} UAH"
            for item in items
        ])
        
        merchant_context = self._get_merchant_context(merchant)
        
        prompt = f"""Ти експерт у категоризації покупок в магазинах. Аналізуй дані позиції з чека та визначи їх категорії максимально точно.

Магазин: {merchant}
{merchant_context}

Позиції:
{items_text}

Можливі категорії:
- Цигарки (HEETS, сигарки, вейп тощо)
- Пальне (бензин, дизель, газ)
- Кава (cappuccino, latte, espresso тощо)
- Напої (кола, сік, вода, енергетики)
- Алкоголь (пиво, вино, горілка)
- Снеки (чіпси, печиво, насіння)
- Продукти (м'ясо, овочі, молочка)
- Гігієна (шампун, мило, зубна паста)
- Аптека (ліки, вітаміни)
- Побутова хімія (миючі засоби)
- Товари для дому (посуд, рушники)
- Косметика (крем, туш, помада)
- Іграшки
- Книги
- Одяг
- Інше

Поверни СУВОРО JSON без markdown:
{{
  "enhanced_items": [
    {{"name": "назва позиції", "category": "найточніша категорія", "reasoning": "коротке пояснення"}}
  ]
}}"""
        
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        
        payload = {
            "model": self.model,
            "max_tokens": 200,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(self.api_url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
            
            content = data.get("content", [])
            if content and isinstance(content[0], dict):
                text = content[0].get("text", "").strip()
                
                # Видалити markdown
                if text.startswith("```"):
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                
                result = json.loads(text.strip())
                return result.get("enhanced_items", items)
        
        except Exception as e:
            logger.warning(f"⚠️ Claude enhancement failed: {str(e)}")
        
        return items
    
    def _get_merchant_context(self, merchant: str) -> str:
        """Отримати контекст про магазин."""
        merchant_lower = merchant.lower()
        
        for store_key, store_info in self.STORE_SPECIFICS.items():
            if store_key in merchant_lower:
                return f"Контекст: {store_info['description']}\nЧастих категорій: {', '.join(store_info['common_categories'])}"
        
        return ""


__all__ = ["ReceiptEnhancer"]
