"""
List Parser: розпізнавання чеків зі скріншотів списків покупок.

На відміну від касових чеків (грязні, нечіткі),
списки часто мають:
- Чіткі назви товарів
- Чіткі ціни
- Структурований формат

Мета: витягти структуровані списки і конвертувати у receipt format.
"""

import re
from typing import Dict, List, Optional, Any, Tuple
import logging

logger = logging.getLogger("finstack")


class ListParser:
    """Парсити списки покупок із скріншотів."""
    
    # Патерни для знаходження ціни
    PRICE_PATTERNS = [
        r'(\d+[.,]\d{2})\s*(грн|uah|₴)?',  # 88.49 грн
        r'₴?\s*(\d+[.,]\d{2})',  # ₴ 88.49
        r'(\d+[.,]\d{2})\s*$',  # В кінці рядка
    ]
    
    # Патерни для знаходження separators (розділові рядки)
    SEPARATOR_PATTERNS = [
        r'^[\-\—\=]{3,}',  # -----
        r'^\s*всього\s*[:\-]?',  # Всього:
        r'^\s*total\s*[:\-]?',  # Total:
        r'^\s*itogo\s*[:\-]?',  # ИТОГО:
        r'^\s*сума\s*[:\-]?',  # Сума:
        r'^\s*[$€¥]',  # Символи валют на початку
    ]
    
    def __init__(self):
        pass
    
    def parse_list_image(self, image_bytes: bytes, media_type: str) -> Optional[Dict[str, Any]]:
        """
        Парсити скріншот списку (через Vision API).
        
        Returns: receipt-like structure or None якщо не є список
        """
        # Спочатку витягуємо текст з Vision
        raw_text = self._extract_text_from_image(image_bytes, media_type)
        
        if not raw_text:
            return None
        
        # Намагаємось парсити як список
        parsed = self._parse_list_text(raw_text)
        
        if not parsed or not parsed.get('items'):
            return None
        
        return parsed
    
    async def parse_list_image_async(self, image_bytes: bytes, media_type: str) -> Optional[Dict[str, Any]]:
        """Async version (для інтеграції з Vision API)."""
        return self.parse_list_image(image_bytes, media_type)
    
    def parse_list_text(self, raw_text: str) -> Optional[Dict[str, Any]]:
        """
        Парсити текстовий список (з повідомлення користувача).
        
        Public wrapper для _parse_list_text.
        """
        return self._parse_list_text(raw_text)
    
    def _extract_text_from_image(self, image_bytes: bytes, media_type: str) -> Optional[str]:
        """
        Витягти текст із скріншота (використати Vision API).
        
        На реальній системі це буде async call до Vision API.
        На даний момент - stub (будемо викликати через receipt_parser).
        """
        # TODO: Викликати Vision API через receipt_parser
        return None
    
    def _parse_list_text(self, raw_text: str) -> Optional[Dict[str, Any]]:
        """
        Парсити текст списку.
        
        Returns: {
            "merchant": "Мій список" / "Shopping List" / None,
            "items": [...],
            "receipt_total": sum,
            "currency": "UAH",
            "source": "list"
        }
        """
        lines = raw_text.strip().split('\n')
        
        if not lines:
            return None
        
        items = []
        total = 0.0
        merchant = self._detect_merchant(lines)
        
        for line in lines:
            stripped = line.strip()
            
            # Пропустити пусті та service lines
            if not stripped or self._is_separator(stripped):
                continue
            
            # Спроба парсити як товар
            item = self._parse_item_line(stripped)
            
            if item:
                items.append(item)
                total += item['total_price']
        
        if not items:
            return None
        
        return {
            "merchant": merchant or "Мій список",
            "items": items,
            "receipt_total": round(total, 2),
            "currency": "UAH",
            "receipt_date": None,
            "source_account": "",
            "source": "list",  # ← NUEVO: Помітити що це зі списку
        }
    
    def _detect_merchant(self, lines: List[str]) -> Optional[str]:
        """
        Детектувати назву списку/магазину.
        
        Шукаємо перший рядок з певними ключовими словами або просто назву.
        """
        if not lines:
            return None
        
        for line in lines[:3]:
            stripped = line.strip()
            
            # Шукаємо "Shopping List", "Список", "Покупки", тощо
            if any(kw in stripped.lower() for kw in ["список", "покупк", "shopping", "list", "мой"]):
                return stripped
            
            # Це може бути назва магазину
            if len(stripped) > 3 and len(stripped) < 50 and not any(c.isdigit() for c in stripped):
                return stripped
        
        return None
    
    def _is_separator(self, line: str) -> bool:
        """Чи це service line / separator?"""
        for pattern in self.SEPARATOR_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                return True
        return False
    
    def _parse_item_line(self, line: str) -> Optional[Dict[str, Any]]:
        """
        Парсити один рядок список у товар.
        
        Формати:
        - "Молоко - 88.49"
        - "Молоко 2л 88.49 грн"
        - "Молоко: 88.49"
        - "1. Молоко 88.49"
        - "☐ Молоко 88.49"
        """
        
        # Видалити prefix (чекбокс, нумерація, тощо)
        line = re.sub(r'^[\☐☑✓✗×•\-\*]?\s*', '', line)  # ☐ Молоко...
        line = re.sub(r'^\d+[\.\)]\s*', '', line)  # 1. Молоко...
        
        # Спроба знайти ціну
        price_match = None
        price_value = None
        
        for pattern in self.PRICE_PATTERNS:
            match = re.search(pattern, line)
            if match:
                price_match = match
                price_str = match.group(1).replace(',', '.')
                try:
                    price_value = float(price_str)
                    break
                except ValueError:
                    continue
        
        if not price_match or price_value is None or price_value <= 0:
            return None
        
        # Витягти назву (все ДО ціни)
        price_start = price_match.start()
        raw_name = line[:price_start].strip()
        
        # Видалити останні розділові символи (-, :, тощо)
        raw_name = re.sub(r'\s*[-:–—]\s*$', '', raw_name)
        
        if len(raw_name) < 2:
            return None
        
        return {
            "raw_name": raw_name,
            "name": raw_name,  # На списку назва вже нормальна
            "total_price": round(price_value, 2),
            "category": self._guess_category_from_list(raw_name),
            "source": "list",  # ← Помітити джерело
        }
    
    def _guess_category_from_list(self, name: str) -> str:
        """Обережна категоризація для списку (простіше ніж для чеків)."""
        name_lower = name.lower()
        
        # Часто люди правильно називають категорії у списках
        if any(kw in name_lower for kw in ["молок", "смет", "сір", "масло", "яйц", "йогурт"]):
            return "Продукти"
        elif any(kw in name_lower for kw in ["овоч", "фрукт", "ябл", "апель", "морк", "цибул"]):
            return "Овочі та фрукти"
        elif any(kw in name_lower for kw in ["вода", "água", "bonaqua"]):
            return "Вода"
        elif any(kw in name_lower for kw in ["кола", "спрайт", "пепси", "сік", "компот"]):
            return "Солодкі напої"
        elif any(kw in name_lower for kw in ["пиво", "вино", "водка", "коньяк"]):
            return "Алкоголь"
        elif any(kw in name_lower for kw in ["heets", "terea", "iqos", "сигар", "цигар"]):
            return "Цигарки"
        elif any(kw in name_lower for kw in ["a95", "a92", "дизель", "бензин"]):
            return "Пальне"
        elif any(kw in name_lower for kw in ["ліки", "таблет", "аспирин", "витамін"]):
            return "Аптека"
        elif any(kw in name_lower for kw in ["шампун", "мило", "зубна паста", "крем"]):
            return "Гігієна та догляд"
        elif any(kw in name_lower for kw in ["порошок", "губка", "миття", "fairy"]):
            return "Побутова хімія"
        else:
            return "Інше"
    
    def is_list_format(self, raw_text: str) -> bool:
        """
        Детектувати чи це текст СПИСКУ (не касового чека).
        
        Сигнали:
        - "Список", "Shopping", "мой"
        - Структурований формат (- або ☐)
        - Нема рядків типу "Касса", "Чек", "ПІДСУМОК"
        """
        if not raw_text:
            return False
        
        text_lower = raw_text.lower()
        
        # Сигнали СПИСКУ
        list_signals = [
            "список", "shopping", "покупк", "мой", 
            "todo", "checklist", "подарок"
        ]
        has_list_signal = any(sig in text_lower for sig in list_signals)
        
        # Сигнали ЧЕКА (inverse)
        receipt_signals = [
            "касса", "чек", "касса", "всього", "підсумок",
            "шк ", "пн ", "дата"
        ]
        has_receipt_signal = any(sig in text_lower for sig in receipt_signals)
        
        # Якщо явно сигнал списку або явно НЕ сигнал чека + структура
        if has_list_signal:
            return True
        
        if not has_receipt_signal and re.search(r'^[\☐•\-\*]|\n[\☐•\-\*]', raw_text):
            return True
        
        return False


# Глобальний instance
list_parser = ListParser()
