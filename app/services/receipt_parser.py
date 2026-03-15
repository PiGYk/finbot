import base64
import json
import re
from typing import Any, Dict, List, Optional

import httpx

from app.services.category_rules import CategoryRulesService
from app.services.receipt_structure_parser import ReceiptStructureParser  # ФАЗА 2
from app.services.receipt_memory import ReceiptMemory  # ФАЗА 3
from app.services.receipt_normalizer import ReceiptNormalizer  # ФАЗА 3


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
        
        # ФАЗА 3: Пам'ять і нормалізація
        self.memory = ReceiptMemory()
        self.normalizer = ReceiptNormalizer(
            memory=self.memory,
            category_rules=category_rules
        )
        
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
            
            # ФАЗА 5: Vision тепер повертає тільки raw_name (факти), не категорії!
            raw_name = normalize_text(raw.get("raw_name", raw.get("name", "?")), "Товар")
            total_price = normalize_amount(raw.get("total_price"))
            if total_price <= 0:
                continue
            
            # ФАЗА 3-5: Нормалізація через пам'ять та словники
            norm_result = self.normalizer.normalize_item(
                raw_name=raw_name,
                merchant=merchant,
                barcode=raw.get("barcode"),
            )
            
            # Використовувати нормалізовану назву якщо вона надійніша
            if norm_result['normalized_name'] and norm_result['confidence'] >= 0.7:
                final_name = norm_result['normalized_name']
                normalization_status = norm_result['normalization_status']
                name_confidence = norm_result['confidence']
            else:
                # Fallback на raw_name якщо нормалізація не впевнена
                final_name = raw_name
                normalization_status = norm_result['normalization_status']
                name_confidence = norm_result['confidence']
            
            # Категоризація на основі нормалізованого імені
            # Vision більше не категоризує, це робить memory/rules/category_rules
            if self.category_rules is not None:
                final_category = self.category_rules.resolve_receipt_category(
                    item_name=final_name,
                    model_category=norm_result.get('category'),  # Від нормалізації, не від Vision
                    merchant=merchant,
                    fallback="Інше",
                )
            else:
                final_category = norm_result.get('category', "Інше")
            
            items.append({
                # Legacy (для backward compat з старим кодом)
                "name": final_name,
                "total_price": total_price,
                "category": final_category,
                
                # НОВЕ: Структура для нової архітектури
                "raw_name": raw_name,
                "normalized_name": final_name,  # Після нормалізації (Phase 3)
                "normalization_status": normalization_status,  # Статус нормалізації
                "name_confidence": name_confidence,  # Впевненість от нормалізатора
                "category_confidence": 0.0,  # Буде заповнено категоризатором
                "is_suspect": False,  # Буде помічено якщо low confidence (Phase 2)
                "barcode": raw.get("barcode"),  # Якщо є в чеку
            })

        if not items:
            raise ValueError("У чеку не знайдено валідних позицій")

        category_totals = self._aggregate_category_totals(items)
        receipt_total = round(sum(item["total_price"] for item in items), 2)
        
        # ФАЗА 2-3: Помітити suspect items
        if receipt_total > 0:
            for item in items:
                # Перевірка 1: Ціна дуже висока (>75% від total)
                price = item["total_price"]
                price_ratio = price / receipt_total
                if price_ratio >= 0.75:
                    item["is_suspect"] = True
                
                # Перевірка 2: Service-like слова у raw_name
                raw_lower = item.get("raw_name", "").lower()
                service_keywords = ["касса", "касса", "дата", "чек", "ітого", "всього", "сума", "оплата"]
                if any(kw in raw_lower for kw in service_keywords):
                    item["is_suspect"] = True
                
                # Перевірка 3: Низька впевненість нормалізації (Phase 3)
                name_conf = item.get("name_confidence", 0.0)
                if name_conf > 0 and name_conf < 0.6:
                    item["is_suspect"] = True

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
        """
        ФАЗА 5: Переписаний Vision prompt.
        Мета: витягти ФАКТИ, не вгадувати.
        
        Vision витягає лише:
        - raw_name (максимально близько до чека)
        - total_price (сума)
        
        Категоризація буде зроблена пізніше (memory → dict → unresolved).
        Вгадування буде мінімальним.
        """
        return f"""
Ти дуже точний парсер касових чеків. Твоя мета - витягти ФАКТИ з чека, не вгадувати.

⚠️ ВАЖЛИВО: 
- Зберегти текст МАКСИМАЛЬНО БЛИЗЬКО до того, що видно на чеку
- НЕ розшифровувати скорочення якщо невпевнена
- НЕ вигадувати категорії
- НЕ вигадувати товари
- Якщо нечітко → залиш як є, не вгадуй

Поверни СУВОРО лише JSON без markdown, без пояснень.

Формат:
{{
  "merchant": "назва магазину (якщо видно в шапці чека)",
  "receipt_date": "YYYY-MM-DD" або null,
  "currency": "{self.default_currency}",
  "items": [
    {{
      "raw_name": "максимально близько до оригіналу (навіть якщо скорочено)",
      "total_price": число з чека,
      "barcode": "ШК якщо видно" або null
    }}
  ]
}}

Правила:
- raw_name: скопіюй текст як є, не редагуй
- total_price: сума, що видна на чеку
- НЕ додавай рядки типу "Всього", "Касса", "Чек", "Дата"
- НЕ додавай послуг. рядки (адреса, телефон, реклама)
- Якщо позиція нечітка → ЗАЛИШ як є, не вигадуй
- Якщо нерозумієш текст → ПРОПУСТИ, не додавай
- НЕ розшифровувати скорочення
- НЕ додавати категорії (категоризація буде пізніше)
- НЕ вигадувати ніяких товарів

Приклад:
Вхід: [Фото чека з "Смет Престд 250", ціна 112.00]
Вихід: {{"raw_name": "Смет Престд 250", "total_price": 112.0}}

НЕ это:
{{"raw_name": "Сметана Президент", "total_price": 112.0, "category": "Продукти"}}

Це завдання розпізнавання ФАКТІВ, не інтерпретації.
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
    
    def save_item_confirmation(
        self,
        merchant: str,
        raw_name: str,
        confirmed_name: str,
        confirmed_category: str,
        barcode: Optional[str] = None
    ):
        """
        Зберегти підтверджену користувачем інформацію про товар.
        Буде використано для наступних чеків того ж магазину.
        
        (Цей метод буде викликатися з Phase 4: User Correction Flow)
        """
        self.memory.save_confirmation(
            merchant=merchant,
            raw_name=raw_name,
            normalized_name=confirmed_name,
            category=confirmed_category,
            barcode=barcode,
        )
    
    def detect_document_type(self, raw_text: str) -> str:
        """
        ФАЗА 7: Детектувати тип документа.
        
        Returns: "receipt" або "list"
        """
        from app.services.list_parser import list_parser
        
        if list_parser.is_list_format(raw_text):
            return "list"
        
        return "receipt"
