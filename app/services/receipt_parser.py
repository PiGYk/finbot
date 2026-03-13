import base64
import json
import re
from typing import Any, Dict, List, Optional

import httpx


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
        raw = raw.replace("₴", "")
        raw = raw.replace("грн", "")
        raw = raw.replace("uah", "")
        raw = raw.replace("UAH", "")
        raw = raw.replace(",", ".")
        raw = raw.replace(" ", "")
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


def keyword_category_from_name(name: str) -> Optional[str]:
    low = name.lower().strip()

    high_priority_patterns = [
        (
            "Цигарки",
            [
                "heets",
                "terea",
                "neo",
                "veo",
                "iqos",
                "стік",
                "стіки",
                "стик",
                "стики",
                "сигар",
                "цигар",
                "тютюн",
                "табак",
                "tven",
                "твен",
                "tobacco",
            ],
        ),
        (
            "Пальне",
            [
                "a95",
                "a-95",
                "a92",
                "a-92",
                "diesel",
                "дизель",
                "дп",
                "lpg",
                "бензин",
                "пальне",
                "fuel",
                "евро95",
                "euro95",
                "upg95",
                "95 energy",
                "95 pulls",
                "заправка",
            ],
        ),
        (
            "Алкоголь",
            [
                "пиво",
                "beer",
                "вино",
                "wine",
                "горіл",
                "водка",
                "whisky",
                "віскі",
                "ром",
                "gin",
                "джин",
            ],
        ),
        (
            "Напої",
            [
                "cola",
                "coca",
                "pepsi",
                "sprite",
                "fanta",
                "вода",
                "сік",
                "juice",
                "чай",
                "кава",
                "напій",
            ],
        ),
        (
            "Гігієна",
            [
                "шампун",
                "гель",
                "soap",
                "мило",
                "паста",
                "toothpaste",
                "щітк",
                "deodor",
                "дезодо",
                "сервет",
            ],
        ),
        (
            "Побутова хімія",
            [
                "порошок",
                "fairy",
                "мий",
                "clean",
                "доместос",
                "білизн",
                "праль",
                "хлор",
                "хім",
            ],
        ),
        (
            "Косметика",
            [
                "крем",
                "mascara",
                "туш",
                "помада",
                "lipstick",
                "cosmetic",
                "космет",
            ],
        ),
        (
            "Аптека",
            [
                "таблет",
                "ліки",
                "каплі",
                "ibuprofen",
                "парацет",
                "цитрамон",
                "аптека",
            ],
        ),
        (
            "Тварини",
            [
                "корм",
                "cat",
                "dog",
                "кішк",
                "собак",
                "pet",
            ],
        ),
        (
            "Товари для дому",
            [
                "лампа",
                "батарей",
                "рушник",
                "пакет",
                "контейнер",
                "губка",
                "свічк",
                "house",
            ],
        ),
        (
            "Продукти",
            [
                "хліб",
                "булка",
                "сир",
                "молоко",
                "кефір",
                "масло",
                "м'яс",
                "ковбас",
                "канапка",
                "сендвіч",
                "яйц",
                "макарон",
                "греч",
                "рис",
                "цукер",
                "печиво",
                "йогур",
                "сметан",
                "борщ",
                "курка",
            ],
        ),
    ]

    for category, patterns in high_priority_patterns:
        for pattern in patterns:
            if pattern in low:
                return category

    return None


def normalize_category(claude_category: Any, item_name: str) -> str:
    # Спочатку дивимось на назву товару.
    # Для цигарок і пального це має вищий пріоритет, ніж категорія від Claude.
    name_hit = keyword_category_from_name(item_name)
    if name_hit in {"Цигарки", "Пальне"}:
        return name_hit

    raw = normalize_text(claude_category, "Інше")
    low = raw.lower()

    mapping = {
        "продукти": "Продукти",
        "їжа": "Продукти",
        "food": "Продукти",
        "напої": "Напої",
        "вода": "Напої",
        "соки": "Напої",
        "сигарки": "Цигарки",
        "цигарки": "Цигарки",
        "сигареты": "Цигарки",
        "тютюн": "Цигарки",
        "алкоголь": "Алкоголь",
        "гігієна": "Гігієна",
        "шампунь": "Гігієна",
        "побутова хімія": "Побутова хімія",
        "хімія": "Побутова хімія",
        "косметика": "Косметика",
        "аптека": "Аптека",
        "ліки": "Аптека",
        "товари для дому": "Товари для дому",
        "дім": "Товари для дому",
        "тварини": "Тварини",
        "корм": "Тварини",
        "пальне": "Пальне",
        "бензин": "Пальне",
        "дизель": "Пальне",
        "дп": "Пальне",
        "газ": "Пальне",
        "lpg": "Пальне",
        "fuel": "Пальне",
        "diesel": "Пальне",
        "a95": "Пальне",
        "a-95": "Пальне",
        "a92": "Пальне",
        "a-92": "Пальне",
        "заправка": "Пальне",
        "інше": "Інше",
    }

    if low in mapping:
        mapped = mapping[low]
        if mapped == "Інше" and name_hit:
            return name_hit
        return mapped

    for key, mapped_value in mapping.items():
        if key in low:
            if mapped_value == "Інше" and name_hit:
                return name_hit
            return mapped_value

    if name_hit:
        return name_hit

    return raw[:1].upper() + raw[1:] if raw else "Інше"


class ReceiptParser:
    def __init__(self, api_key: str, model: str, default_currency: str) -> None:
        self.api_key = api_key
        self.model = model
        self.default_currency = default_currency
        self.api_url = "https://api.anthropic.com/v1/messages"

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
            category = normalize_category(raw.get("category"), name)

            if total_price <= 0:
                continue

            items.append(
                {
                    "name": name,
                    "total_price": total_price,
                    "category": category,
                }
            )

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

    async def parse_receipt_image(self, image_bytes: bytes, media_type: str) -> Dict[str, Any]:
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        prompt = f"""
Ти парсер касових чеків українською.
Потрібно розібрати ФОТО ЧЕКА і повернути СУВОРО лише JSON без markdown, без пояснень, без трійних лапок.

Поверни формат:
{{
  "merchant": "назва магазину або супермаркету",
  "receipt_date": "YYYY-MM-DD" або null,
  "currency": "{self.default_currency}",
  "items": [
    {{
      "name": "назва позиції",
      "total_price": number,
      "category": "одна з категорій"
    }}
  ]
}}

Категорії використовуй тільки з цього списку:
- Продукти
- Напої
- Цигарки
- Пальне
- Алкоголь
- Гігієна
- Побутова хімія
- Косметика
- Аптека
- Товари для дому
- Тварини
- Інше

Правила:
- total_price це ПОВНА СУМА ПО РЯДКУ, не ціна за штуку
- якщо є кількість, все одно повертай уже готовий total_price по позиції
- якщо позиція нерозбірлива, але видно суму, можеш лишити коротку назву і категорію Інше
- merchant спробуй знайти максимально точно
- receipt_date поверни у форматі YYYY-MM-DD, якщо не впевнений — null
- currency за замовчуванням {self.default_currency}
- поверни всі видимі позиції з чека, а не тільки загальну суму
- усе, що схоже на HEETS, TEREA, IQOS, стіки, сигарети, тютюн — віднось до категорії Цигарки
- усе, що схоже на бензин, дизель, A95, A92, LPG, газ — віднось до категорії Пальне
"""

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        payload = {
            "model": self.model,
            "max_tokens": 1200,
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

        raw_text = "\n".join(text_parts).strip()
        raw_text = strip_code_fences(raw_text)

        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Claude повернув не JSON для чека: {raw_text}") from e

        normalized = self._normalize_receipt(parsed)
        print("PARSED_RECEIPT =", json.dumps(normalized, ensure_ascii=False))
        return normalized
