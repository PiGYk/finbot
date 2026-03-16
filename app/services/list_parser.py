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
        """Більш розумна категоризація для списків."""
        name_lower = name.lower()
        
        # Виключити магазини/сервіси
        if any(kw in name_lower for kw in ["пакет", "сумка", "силпо", "силпа", "атб", "вог", "novus", "ашан", "carrefour"]):
            return "Інше"
        
        # ⚠️ АЛКОГОЛЬ - ПЕРЕВІРЯЄМ ПЕРШИМ (перед солодкими напоями!)
        # Бо "напій виний" містить обидва слова
        if any(kw in name_lower for kw in [
            "пиво", "вино", "виний", "вина", "водка", "коньяк", "бренди", "виски", "ром", "джин", 
            "ліквер", "шампанськ", "шампанское", "просекко", "гіне", "абсент", "текіла", "мезкал",
            "fragolino", "martini", "jim beam", "bacardi", "guinness", "heineken", "taylor", "tayoro"
        ]):
            return "Алкоголь"
        
        # ОВОЧІ ТА ФРУКТИ - розширена
        elif any(kw in name_lower for kw in [
            "овоч", "фрукт", "ябл", "яблуко", "яблоко", "апель", "апельсин", "морк", "морковь",
            "цибул", "цибула", "помідор", "томат", "огірок", "огурец",
            "батат", "авокад", "авокадо", "банан", "груша", "груша", "слива", "вишня", "малина", "суниця", "клубника",
            "лимон", "лимончик", "апельсин", "мандарин", "грейпфрут", "грейпфрутт", "киви", "ананас", "манго",
            "папая", "папайя", "персик", "персик", "абрикос", "абрикос", "картофель", "картопля", "картошка", "капуста", "броколі", "брокколи",
            "цвітна капуста", "цветная капуста", "баклажан", "баклажан", "перець", "перец", "болгарский", "салат", "листя салату", "шпинат", "укроп", "петрушка", "петрушка",
            "сніг", "снег", "спаржа", "редис", "редиска", "буряк", "свёкла", "ріпа", "кабачок", "цукіні", "цукини"
        ]):
            return "Овочі та фрукти"
        
        # ПРОДУКТИ (молочка, м'ясо, хліб)
        elif any(kw in name_lower for kw in [
            "молок", "молоко", "смет", "сметан", "сметана", "сір", "сыр", "масло", "яйц", "яйцо",
            "йогурт", "йогурт", "кефір", "кефир", "творог", "творог",
            "м'ясо", "м'яса", "мясо", "м'ясне", "мясное",
            "яловичина", "яловичина", "яловичное", "курка", "курячина", "свинина", "свинячий", "свинячая", "свіжа", "свежая",
            "стейк", "стейк", "грудинка", "грудина", "філе", "филе", "філей", "филей", "крило", "крылья", "ніжка", "ножка", "рулет", "рулет",
            "фарш", "фарш", "ковбаса", "колбаса", "ковбаски", "сосиск", "сосиски", "бекон", "бекон", "ветчин", "ветчина", "копчено", "копченое",
            "ребро", "ребро", "реберця", "ребрышки", "косточка", "косточка", "котлета", "котлета", "тефтель", "фрикадель", "гуляш", "гуляш", "паштет", "паштет",
            "салямі", "салями", "прошут", "прошуто", "спек", "шпик", "грудь", "грудь", "окорок", "окорок", "ляжка", "ляжка",
            "риба", "риби", "рыба", "рыбка", "форель", "форель", "окунь", "окунь", "карась", "карась", "сома", "сом", "креветки", "креветка", "краб", "краб", "устриці", "устрица",
            "селедка", "селедка", "скумбрія", "скумбрия", "лосось", "лосось", "тріска", "треска", "мінтай", "минтай", "судак", "судак", "щука", "щука", "лящ", "лещ",
            "хліб", "хлеб", "батон", "батон", "булка", "булка", "ломтик", "ломтик", "сухарь", "сухарь", "брашно", "мука", "мука", "борошно", "борошно",
            "дріжджі", "дрожжи", "гріш", "сыр", "печиво", "печенье", "печенье", "пайчик", "булочка", "булочки",
            "макаронні", "макарони", "макароны", "макарониця", "спагетті", "спагети", "паста", "паста", "рис", "рис", "рисова", "рисовый",
            "гречка", "гречневий", "гречневый", "полтавець", "полтавка", "перловка", "перловка", "ячмінь", "ячмень"
        ]):
            return "Продукти"
        
        # ВОДА
        elif any(kw in name_lower for kw in ["вода", "água", "bonaqua", "aqua", "мінерал"]):
            return "Вода"
        
        # СОЛОДКІ НАПОЇ - розширена
        # ⚠️ НЕ ВКЛЮЧАЄМО "напій" щоб не цепити алкоголь! (перевіряєм вино перед цим)
        elif any(kw in name_lower for kw in [
            "кола", "кока-кола", "спрайт", "пепси", "фанта", "севен ап", "7up", "sprite", "pepsi",
            "лимонад", "лимонад", "сік", "сок", "апельсиновий", "апельсиновый", "яблучний", "яблочный",
            "томатний", "томатный", "морс", "морс", "компот", "компот", "гранатовый", "яблочный",
            "газований", "газированный", "углекислый", "розчин", "раствор",
            "каво", "кофе", "какао", "какао", "гарячий шоколад", "горячий шоколад",
            "cordial", "cordial", "squash", "squash", "растворимый", "растворимый"
        ]):
            return "Солодкі напої"
        
        # ЦИГАРКИ - розширена
        elif any(kw in name_lower for kw in [
            "heets", "terea", "iqos", "lil", "snus", "сигар", "цигар", 
            "паління", "кальян", "табак", "nicotine", "smoking"
        ]):
            return "Цигарки"
        
        # ПАЛЬНЕ
        elif any(kw in name_lower for kw in ["a95", "a92", "дизель", "бензин", "дт", "газ", "lpg"]):
            return "Пальне"
        
        # АПТЕКА - розширена
        elif any(kw in name_lower for kw in [
            "ліки", "таблет", "аспирин", "витамін", "драже", "капсул", "порошок",
            "мазь", "крем лікув", "гель", "сироп", "настойка", "тинктур",
            "пластир", "бинт", "вата", "шприц", "градусник", "термометр",
            "маска медичн", "перчатк", "дезінфекц", "спирт", "перекис", "йод"
        ]):
            return "Аптека"
        
        # ГІГІЄНА ТА ДОГЛЯД - розширена
        elif any(kw in name_lower for kw in [
            "шампун", "мило", "гель для душу", "пінка для ванни", "зубна паста", 
            "зубна щітка", "крем для обличчя", "лосьйон", "серум", "маска для обличчя",
            "дезодоран", "антиперспіран", "бальзам", "кондиціонер", "маска для волосся",
            "гель для волосся", "мус", "лак для волосся", "фен", "стайлер",
            "расчіск", "гребень", "ножиці", "pilka", "пилка", "лак для нігтів",
            "знімач лаку", "вата", "диск ватний", "серветка", "папір туалетний"
        ]):
            return "Гігієна та догляд"
        
        # ПОБУТОВА ХІМІЯ - розширена
        elif any(kw in name_lower for kw in [
            "порошок", "губка", "миття", "fairy", "допомагає", "засіб для чищення",
            "дезінфектант", "дезінсекцій", "дератизац", "дезодоран для кімнати",
            "свічка пахуча", "диф'юзер", "освіжувач", "кондиціонер для білизни",
            "плямовидалювач", "отбілювач", "крохмаль", "цимент для лакавання"
        ]):
            return "Побутова хімія"
        
        # ТВАРИНИ
        elif any(kw in name_lower for kw in [
            "корм для кошек", "корм для собак", "корм для риб", "корм для птиці",
            "ошейник", "повідець", "іграшка для", "мисок", "лапка", "батьківщина",
            "препарат від бліх", "витамін для"
        ]):
            return "Тварини"
        
        # ФАСТФУД І ЗАКУСКИ
        elif any(kw in name_lower for kw in [
            "чіпси", "крекер", "сніданок", "снек", "гранола", "мюслі", "попкорн",
            "орех", "арахіс", "мигдаль", "насіння", "сухофрукт", "заливка",
            "соус", "кетчуп", "гірчиця", "деліль", "маслин", "сушарка"
        ]):
            return "Фастфуд і снеки"
        
        # ІНШЕ (fallback)
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
