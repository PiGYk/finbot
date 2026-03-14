import base64
import json
import re
from typing import Any, Dict, List, Optional

import httpx

from app.services.category_rules import CategoryRulesService
from app.services.receipt_structure_parser import ReceiptStructureParser  # ФАЗА 2


def strip_code_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def normalize_text(value: Any, fallback: str) -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def normalize_amount(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        amount = float(value)
    else:
        raw = str(value).strip()
        raw = raw.replace("₴", "").replace("грн", "").replace("uah", "").replace("UAH", "")
        raw = raw.replace(",", ".").replace(" ", "")
        try:
            amount = float(raw)
        except ValueError:
            return 0.0
    return round(abs(amount), 2)


def normalize_receipt_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    return None


class ReceiptParser:
    def __init__(
        self,
        api_key: str,
        model: str,
        default_currency: str,
        category_rules: Optional[CategoryRulesService] = None,
        provider: str = "claude",  # НОВЕ: claude або openai
        openai_api_key: Optional[str] = None,  # НОВЕ: для OpenAI
        openai_model: str = "gpt-4o-mini",  # НОВЕ: модель OpenAI
    ) -> None:
        self.api_key = api_key  # Claude API key
        self.model = model  # Claude model
        self.default_currency = default_currency
        self.api_url = "https://api.anthropic.com/v1/messages"
        self.category_rules = category_rules
        
        # НОВЕ: Підтримка OpenAI
        self.provider = provider.lower()
        self.openai_api_key = openai_api_key
        self.openai_model = openai_model
        self.openai_api_url = "https://api.openai.com/v1/chat/completions"
        
        if self.category_rules is not None:
            self.category_rules.ensure_seeded()

    def _aggregate_category_totals(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        bucket: Dict[str, float] = {}
        for item in items:
            category = item["category"]
            amount = item["total_price"]
            if amount <= 0:
                continue
            bucket[category] = round(bucket.get(category, 0.0) + amount, 2)
        return [
            {"category": category, "amount": amount}
            for category, amount in sorted(bucket.items(), key=lambda x: x[1], reverse=True)
        ]

    def _fallback_category(self, merchant: str, name: str, raw_category: Any) -> str:
        text = f"{merchant} {name} {normalize_text(raw_category, '')}".lower()
        checks = [
            ("Цигарки", ["heets", "terea", "iqos", "сигар", "цигар", "тютюн", "вейп", "стік"]),
            ("Пальне", ["a95", "a-95", "a92", "a-92", "дизель", "бензин", "lpg", "пальне", "fuel"]),
            ("Вода", ["моршинська", "borjomi", "bonaqua", "мінеральна вода", "вода"]),
            ("Солодкі напої", ["cola", "coca", "pepsi", "sprite", "fanta", "живчик", "burn", "red bull", "monster", "сік", "енергетик"]),
            ("Алкоголь", ["пиво", "вино", "віскі", "горіл", "whisky", "gin", "ром"]),
            ("Фастфуд і снеки", ["бургер", "хот дог", "шаурма", "пельмені", "чіпси", "шоколад", "печиво", "насіння", "сендвіч", "ковбас", "сосиски"]),
            ("Аптека", ["ліки", "таблет", "мазь", "сироп", "аптека", "ibuprofen", "парацетамол"]),
            ("Тварини", ["корм", "pet", "кішк", "собак", "вет"]),
            ("Гігієна та догляд", ["шампун", "гель", "мило", "паста", "крем", "туалетний папір", "прокладки"]),
            ("Побутова хімія", ["fairy", "доместос", "порошок", "миття посуду", "засіб для прибирання"]),
            ("Товари для дому", ["рушник", "посуд", "лампочка", "контейнер", "органайзер"]),
        ]
        for category, patterns in checks:
            if any(pattern in text for pattern in patterns):
                return category
        return "Інше"

    def _normalize_receipt(self, parsed: Dict[str, Any]) -> Dict[str, Any]:
        # ФАЗА 2: Структурний парсер чека
        # Спроба детермінувати структуру (товари, totals, service lines, etc.)
        structure_parser = ReceiptStructureParser()
        
        # Будуємо сирі лінійки з items для парсингу структури
        # (На реальних чеках це були б справді сирі лінійки від OCR)
        raw_items = parsed.get("items", [])
        raw_lines_text = "\n".join([
            normalize_text(item.get("raw_name", item.get("name", "")), "")
            for item in raw_items
            if isinstance(item, dict)
        ])
        
        # Парсити структуру
        try:
            structured = structure_parser.parse_raw_text(raw_lines_text)
            structure_warnings = structured.warnings
        except Exception as e:
            # Graceful fallback якщо структурний парсер впав
            structure_warnings = [f"Structure parser error: {str(e)}"]
            structured = None
        
        # Оновити merchant з структурного парсера якщо він кращий
        vision_merchant = normalize_text(parsed.get("merchant"), "Чек")
        if structured and structured.merchant_name:
            # Спроба використовувати merchant з структури якщо він виглядає кращим
            struct_merchant = structured.merchant_name.strip()
            if len(struct_merchant) > 2 and len(struct_merchant) < 50:
                vision_merchant = struct_merchant
        
        merchant = vision_merchant
        currency = normalize_text(parsed.get("currency"), self.default_currency).upper()
        receipt_date = normalize_receipt_date(parsed.get("receipt_date"))
        source_account = normalize_text(parsed.get("source_account"), "")

        raw_items = parsed.get("items")
        if not isinstance(raw_items, list):
            raise ValueError("Claude не повернув items для чека")

        items: List[Dict[str, Any]] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            name = normalize_text(raw.get("name"), "Товар")
            total_price = normalize_amount(raw.get("total_price"))
            if total_price <= 0:
                continue

            if self.category_rules is not None:
                category = self.category_rules.resolve_receipt_category(
                    item_name=name,
                    model_category=raw.get("category"),
                    merchant=merchant,
                    fallback="Інше",
                )
            else:
                category = self._fallback_category(merchant, name, raw.get("category"))

            # ФАЗА 1: Додати raw_name + confidence
            # raw_name = те що насправді було на чеку
            # normalized_name = наша гіпотеза людської назви
            # normalization_status = звідки взялася гіпотеза ("ocr_only" на цьому етапі)
            raw_name = normalize_text(raw.get("raw_name", raw.get("name", "?")), "Товар")
            
            items.append({
                # Legacy (для backward compat з старим кодом)
                "name": name,
                "total_price": total_price,
                "category": category,
                
                # НОВЕ: Структура для нової архітектури
                "raw_name": raw_name,
                "normalized_name": name,  # На цьому етапі = name (від OCR)
                "normalization_status": "ocr_only",  # Статус нормалізації
                "name_confidence": 0.0,  # Буде заповнено нормалізатором (Phase 3)
                "category_confidence": 0.0,  # Буде заповнено категоризатором
                "is_suspect": False,  # Буде помічено якщо low confidence (Phase 4)
                "barcode": raw.get("barcode"),  # Якщо є в чеку
            })

        if not items:
            raise ValueError("У чеку не знайдено валідних позицій")

        category_totals = self._aggregate_category_totals(items)
        receipt_total = round(sum(item["total_price"] for item in items), 2)
        
        # ФАЗА 2: Помітити suspect items на основі структури
        # Якщо item ціна дуже висока (близька до總 суми), ймовірно це помилка
        if receipt_total > 0:
            for item in items:
                price = item["total_price"]
                price_ratio = price / receipt_total
                
                # Якщо позиція становить 80%+ від суми, ймовірно це або:
                # 1) Сама загальна сума (помилка)
                # 2) Одна дуже дорога позиція (але рідко)
                if price_ratio >= 0.75:
                    item["is_suspect"] = True
                    item["name_confidence"] = 0.3  # Низька впевненість
                
                # Якщо raw_name містить service-like слова, помітити як suspect
                raw_lower = item.get("raw_name", "").lower()
                service_keywords = ["касса", "касса", "дата", "чек", "ітого", "всього", "сума", "оплата"]
                if any(kw in raw_lower for kw in service_keywords):
                    item["is_suspect"] = True
                    item["name_confidence"] = 0.2

        return {
            "merchant": merchant,
            "currency": currency,
            "receipt_date": receipt_date,
            "source_account": source_account,
            "items": items,
            "category_totals": category_totals,
            "receipt_total": receipt_total,
        }

    def _build_prompt(self) -> str:
        category_guide = (
            self.category_rules.render_receipt_category_guide()
            if self.category_rules is not None
            else "- Інше: якщо нічого не підійшло."
        )
        return f"""
Ти експертний парсер касових чеків українською мовою з досвідом роботи з НИЗЬКОЯКІСНИМИ, РОЗМИТИМИ та ПОШКОДЖЕНИМИ чеками.

⚠️ ВАЖЛИВО: Чек може бути розмитим, під кутом, з відблисками або застарілим термодруком. 
НАМАГАЙСЯ розпізнати текст навіть якщо він частково нечіткий. Використовуй КОНТЕКСТ (назви товарів, цифри, структуру чека).

Поверни СУВОРО лише JSON без markdown, без пояснень, без трійних лапок.

Формат:
{{
  "merchant": "назва магазину або закладу",
  "receipt_date": "YYYY-MM-DD" або null,
  "currency": "{self.default_currency}",
  "items": [
    {{
      "raw_name": "максимально близько до того, що видно на чеку (навіть якщо нечітко)",
      "name": "назва позиції (розшифруй скорочення якщо можливо)",
      "total_price": number,
      "category": "одна з дозволених категорій"
    }}
  ]
}}

Категорії використовуй ТІЛЬКИ з цього списку:
{category_guide}

Правила розпізнавання:
- ❌ НЕ ВИГАДУЙ позиції! Тільки те, що реально чітко видно на чеку.
- ❌ НЕ додавай товари "для повноти" — лише факти з чека.
- ❌ НЕ ДОДАВАЙ загальну суму чека як окрему позицію! Шукай рядки типу "Всього:", "Сума:", "Total:", "До сплати:" — це НЕ товари!
- ❌ Якщо текст ДУЖЕ нечіткий і не можеш розібрати назву — ПРОПУСТИ цю позицію (краще менше але точно).
- НЕ вигадуй нових категорій (тільки зі списку вище).
- total_price = повна сума за позицію (якщо бачиш множення, порахуй: qty × price).
- Якщо назва ЧАСТКОВО нечітка але сума видна → залиш коротку назву + категорію "Інше".
- Якщо бачиш чіткі скорочення типу "Смет", "Молок", "Серв" → розшифруй (Сметана, Молоко, Серветки).
- merchant: шукай на початку чека (часто великими літерами або в шапці).
- receipt_date: формат YYYY-MM-DD, якщо сумніваєшся → null.
- currency за замовчуванням {self.default_currency}.
- Поверни ТІЛЬКИ ті позиції, які ти реально можеш розібрати з високою впевненістю.

⚠️ КРИТИЧНІ помилки категоризації (уважно!):
- TEREA, HEETS, IQOS стіки → ЗАВЖДИ "Цигарки" (НЕ вино, НЕ алкоголь!)
- Приправи, спеції, сіль, перець → "Продукти"
- Пакети для сміття, губки, рідина для миття → "Побутова хімія"
- Серветки паперові, рушники паперові → "Побутова хімія" або "Товари для дому"
- Пакети пластикові, поліетиленові пакети → "Товари для дому" (НЕ цигарки!)
- Молоко, сметана, масло, сир → "Продукти"
- A95, A92, дизель, бензин → "Пальне" (НЕ інші товари!)

⚠️ ЗАГАЛЬНА СУМА:
- Якщо бачиш суму > 1000 грн на одну позицію → це ймовірно ЗАГАЛЬНА СУМА чека, а не окремий товар!
- Перевір: чи є поруч слова "Всього", "Сума", "До сплати", "Total", "Итого"? Якщо так — НЕ ДОДАВАЙ як позицію!

Категоризація (типові приклади):
- М'ясо, молоко, яйця, крупи, хліб, сметана, приправи, спеції → Продукти
- Помідори, огірки, яблука, банани, зелень → Овочі та фрукти
- Вода "Моршинська", "Bonaqua", мінералка → Вода
- Кола, спрайт, соки, енергетики → Солодкі напої
- Піца, бургер, хот-дог, чіпси, шоколад, пельмені, круасан → Фастфуд і снеки
- Капучино, латте, страви в кафе, серветки в кафе → Кафе та ресторани
- HEETS, TEREA (Arbor Pearl, Blue, Green), IQOS стіки, сигарети, тютюн → Цигарки
- A95, A92, дизель, бензин, газ, LPG → Пальне (ТІЛЬКИ паливо!)
- Ліки, таблетки, вітаміни, мазі → Аптека
- Шампунь, гель, мило, зубна паста, крем для обличчя → Гігієна та догляд
- Пакети для сміття, губки для посуду, рідина для миття, порошок → Побутова хімія
- Серветки паперові, туалетний папір, рушники паперові → Побутова хімія

Якщо чек змішаний → класифікуй КОЖНУ позицію окремо за її типом товару.
""".strip()

    async def _call_openai_vision(self, image_b64: str, prompt: str) -> str:
        """Викликати OpenAI Vision API для розпізнавання чека."""
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.openai_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_b64}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 4000,
            "temperature": 0,
        }
        
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(self.openai_api_url, headers=headers, json=payload)
            if response.status_code >= 400:
                raise Exception(f"OpenAI {response.status_code}: {response.text}")
            data = response.json()
        
        # Витягти текст з відповіді
        choices = data.get("choices", [])
        if not choices:
            raise ValueError("OpenAI повернув порожню відповідь")
        
        message = choices[0].get("message", {})
        content = message.get("content", "")
        return content.strip()

    async def parse_receipt_image(self, image_bytes: bytes, media_type: str) -> Dict[str, Any]:
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        prompt = self._build_prompt()
        
        # НОВЕ: Роутинг між Claude і OpenAI
        if self.provider == "openai":
            if not self.openai_api_key:
                raise ValueError("OpenAI API key не налаштований для receipt parsing")
            
            raw_text = await self._call_openai_vision(image_b64, prompt)
            raw_text = strip_code_fences(raw_text)
        else:
            # Claude API (за замовчуванням)
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            payload = {
                "model": self.model,
                "max_tokens": 4000,
                "temperature": 0,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": image_b64,
                                },
                            },
                        ],
                    }
                ],
            }
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(self.api_url, headers=headers, json=payload)
                if response.status_code >= 400:
                    raise Exception(f"Claude {response.status_code}: {response.text}")
                data = response.json()

            content_blocks = data.get("content", [])
            if not content_blocks:
                raise ValueError("Claude повернув порожню відповідь для чека")

            text_parts: List[str] = []
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))

            raw_text = strip_code_fences("\n".join(text_parts).strip())
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Claude повернув не JSON для чека: {raw_text}") from e

        normalized = self._normalize_receipt(parsed)
        print("PARSED_RECEIPT =", json.dumps(normalized, ensure_ascii=False))
        return normalized
