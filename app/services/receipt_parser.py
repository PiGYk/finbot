import base64
import json
import re
from typing import Any, Dict, List, Optional

import httpx

from app.services.category_rules import CategoryRulesService


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
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.default_currency = default_currency
        self.api_url = "https://api.anthropic.com/v1/messages"
        self.category_rules = category_rules
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
        merchant = normalize_text(parsed.get("merchant"), "Чек")
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

            items.append({
                "name": name,
                "total_price": total_price,
                "category": category,
            })

        if not items:
            raise ValueError("У чеку не знайдено валідних позицій")

        category_totals = self._aggregate_category_totals(items)
        receipt_total = round(sum(item["total_price"] for item in items), 2)

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
Ти парсер касових чеків українською.
Потрібно розібрати ФОТО ЧЕКА і повернути СУВОРО лише JSON без markdown, без пояснень, без трійних лапок.

Поверни формат:
{{
  "merchant": "назва магазину або закладу",
  "receipt_date": "YYYY-MM-DD" або null,
  "currency": "{self.default_currency}",
  "items": [
    {{
      "name": "назва позиції",
      "total_price": number,
      "category": "одна з дозволених категорій"
    }}
  ]
}}

Категорії використовуй ТІЛЬКИ з цього списку:
{category_guide}

Жорсткі правила:
- НЕ вигадуй нових категорій.
- total_price це повна сума по рядку, не ціна за штуку.
- якщо позиція нерозбірлива, але видно суму, залиш коротку назву і категорію Інше.
- merchant спробуй знайти максимально точно.
- receipt_date поверни у форматі YYYY-MM-DD, якщо не впевнений — null.
- currency за замовчуванням {self.default_currency}.
- поверни всі видимі позиції з чека, а не тільки загальну суму.
- базові продукти для дому класифікуй як Продукти.
- вода без цукру класифікуй як Вода.
- cola, pepsi, sprite, живчик, соки, холодні чаї, енергетики класифікуй як Солодкі напої.
- хот-доги, бургери, шаурма, ковбаса, сосиски, пельмені, чіпси, шоколад, насіння, солодощі, напівфабрикати класифікуй як Фастфуд і снеки.
- чеки із кафе, ресторанів, пабів і кав'ярень та готові напої в закладах класифікуй як Кафе та ресторани.
- HEETS, TEREA, IQOS, стіки, сигарети, тютюн, вейпи класифікуй як Цигарки.
- бензин, дизель, A95, A92, LPG, газ класифікуй як Пальне.
- якщо чек змішаний, класифікуй КОЖНУ позицію окремо.
""".strip()

    async def parse_receipt_image(self, image_bytes: bytes, media_type: str) -> Dict[str, Any]:
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        prompt = self._build_prompt()
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": 1800,
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
