import json
import re
from typing import Any, Dict, List, Optional


def strip_code_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def normalize_amount(value: Any) -> float:
    if value is None:
        raise ValueError("Claude не визначив amount")

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
        amount = float(raw)

    if amount < 0:
        amount = abs(amount)

    if amount == 0:
        raise ValueError("amount = 0, це не схоже на нормальну суму")

    return round(amount, 2)


def normalize_text(value: Any, fallback: str) -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def normalize_parsed(parsed: Dict[str, Any], default_currency: str, default_source_account: str) -> Dict[str, Any]:
    tx_type = normalize_text(parsed.get("type"), "").lower()
    if tx_type not in {"expense", "income"}:
        raise ValueError(f"Непідтримуваний type: {tx_type}")

    amount = normalize_amount(parsed.get("amount"))
    currency = normalize_text(parsed.get("currency"), default_currency).upper()
    category = normalize_text(parsed.get("category"), "Інше")
    description = normalize_text(parsed.get("description"), category)
    source_account = normalize_text(parsed.get("source_account"), default_source_account)

    return {
        "type": tx_type,
        "amount": amount,
        "currency": currency,
        "category": category,
        "description": description,
        "source_account": source_account,
    }


def normalize_balance_setup(parsed: Dict[str, Any], default_currency: str) -> Dict[str, Any]:
    intent = normalize_text(parsed.get("intent"), "").lower()
    if intent != "balance_setup":
        raise ValueError(f"Непідтримуваний intent: {intent}")

    accounts = parsed.get("accounts")
    if not isinstance(accounts, list) or not accounts:
        raise ValueError("Claude не повернув список accounts")

    normalized_accounts: List[Dict[str, Any]] = []
    for item in accounts:
        if not isinstance(item, dict):
            continue

        name = normalize_text(item.get("name"), "")
        if not name:
            continue

        balance = normalize_amount(item.get("balance"))
        currency = normalize_text(item.get("currency"), default_currency).upper()

        normalized_accounts.append({
            "name": name,
            "balance": balance,
            "currency": currency,
        })

    if not normalized_accounts:
        raise ValueError("Після нормалізації список accounts порожній")

    return {
        "intent": "balance_setup",
        "accounts": normalized_accounts,
    }


def normalize_transfer(parsed: Dict[str, Any], default_currency: str) -> Dict[str, Any]:
    intent = normalize_text(parsed.get("intent"), "").lower()
    if intent != "transfer":
        raise ValueError(f"Непідтримуваний intent: {intent}")

    amount = normalize_amount(parsed.get("amount"))
    currency = normalize_text(parsed.get("currency"), default_currency).upper()
    source_account = normalize_text(parsed.get("source_account"), "")
    destination_account = normalize_text(parsed.get("destination_account"), "")
    description = normalize_text(parsed.get("description"), "Переказ між рахунками")

    if not source_account:
        raise ValueError("Claude не визначив рахунок-відправник")
    if not destination_account:
        raise ValueError("Claude не визначив рахунок-отримувач")
    if source_account == destination_account:
        raise ValueError("Рахунок-відправник і рахунок-отримувач однакові")

    return {
        "intent": "transfer",
        "amount": amount,
        "currency": currency,
        "source_account": source_account,
        "destination_account": destination_account,
        "description": description,
    }


def normalize_last_transaction_action(parsed: Dict[str, Any], default_currency: str) -> Dict[str, Any]:
    intent = normalize_text(parsed.get("intent"), "").lower()
    if intent != "last_transaction_action":
        raise ValueError(f"Непідтримуваний intent: {intent}")

    action = normalize_text(parsed.get("action"), "").lower()
    if action not in {"delete", "update"}:
        raise ValueError(f"Непідтримуваний action: {action}")

    amount = parsed.get("amount")
    if amount is not None:
        amount = normalize_amount(amount)

    target_index = parsed.get("target_index")
    if target_index is not None:
        try:
            target_index = int(target_index)
        except (TypeError, ValueError):
            target_index = None
        if target_index is not None and target_index <= 0:
            target_index = None

    category = parsed.get("category")
    if category is not None:
        category = normalize_text(category, "")

    description = parsed.get("description")
    if description is not None:
        description = normalize_text(description, "")

    source_account = parsed.get("source_account")
    if source_account is not None:
        source_account = normalize_text(source_account, "")

    destination_account = parsed.get("destination_account")
    if destination_account is not None:
        destination_account = normalize_text(destination_account, "")

    target_category = parsed.get("target_category")
    if target_category is not None:
        target_category = normalize_text(target_category, "")

    target_description = parsed.get("target_description")
    if target_description is not None:
        target_description = normalize_text(target_description, "")

    currency = normalize_text(parsed.get("currency"), default_currency).upper()

    return {
        "intent": "last_transaction_action",
        "action": action,
        "amount": amount,
        "currency": currency,
        "category": category,
        "description": description,
        "source_account": source_account,
        "destination_account": destination_account,
        "target_index": target_index,
        "target_category": target_category,
        "target_description": target_description,
    }


def normalize_intent(parsed: Dict[str, Any]) -> str:
    intent = normalize_text(parsed.get("intent"), "").lower()

    allowed = {
        "finance_write",
        "finance_query",
        "finance_advice",
        "smalltalk",
    }

    if intent not in allowed:
        raise ValueError(f"Непідтримуваний intent router result: {intent}")

    return intent


class ClaudeParser:
    def __init__(self, api_key: str, model: str, default_currency: str, default_source_account: str) -> None:
        self.api_key = api_key
        self.model = model
        self.default_currency = default_currency
        self.default_source_account = default_source_account
        self.api_url = "https://api.anthropic.com/v1/messages"

    def looks_like_balance_setup_request(self, text: str) -> bool:
        low = text.strip().lower()

        triggers = [
            "початкові баланси",
            "початковий баланс",
            "стартові баланси",
            "стартовий баланс",
            "встанови баланс",
            "онови баланс",
            "задати баланс",
            "зараз на рахунках",
            "залишки по рахунках",
        ]

        return any(trigger in low for trigger in triggers)

    def looks_like_transfer_request(self, text: str) -> bool:
        low = text.strip().lower()
        triggers = [
            "перевів",
            "перекинув",
            "переказав",
            "скинув",
            "скинула",
            "перевела",
            "перевести",
            "перекинути",
            "переказ",
            "між рахунками",
            "з рахунку",
            "на рахунок",
        ]
        return any(trigger in low for trigger in triggers)

    def looks_like_last_transaction_action_request(self, text: str) -> bool:
        low = text.strip().lower()

        if low.startswith("не з ") or low.startswith("не на "):
            return True

        triggers = [
            "видали остан",
            "зміни остан",
            "останню транзакц",
            "останню витрат",
            "останній дохід",
            "останній переказ",
            "останньої транзакц",
            "останнього чеку",
            "в останньому чеку",
        ]

        return any(trigger in low for trigger in triggers)

    async def _call_claude_json(self, prompt: str, max_tokens: int = 350) -> Dict[str, Any]:
        import httpx

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0,
            "messages": [
                {"role": "user", "content": prompt}
            ],
        }

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(self.api_url, headers=headers, json=payload)

            if response.status_code >= 400:
                raise Exception(f"Claude {response.status_code}: {response.text}")

            data = response.json()

        content_blocks = data.get("content", [])
        if not content_blocks:
            raise ValueError("Claude повернув порожню відповідь")

        text_parts: List[str] = []
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))

        raw_text = "\n".join(text_parts).strip()
        raw_text = strip_code_fences(raw_text)

        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Claude повернув не JSON: {raw_text}") from e

    async def _call_claude_text(self, prompt: str, max_tokens: int = 500) -> str:
        import httpx

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "messages": [
                {"role": "user", "content": prompt}
            ],
        }

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(self.api_url, headers=headers, json=payload)

            if response.status_code >= 400:
                raise Exception(f"Claude {response.status_code}: {response.text}")

            data = response.json()

        content_blocks = data.get("content", [])
        if not content_blocks:
            raise ValueError("Claude повернув порожню відповідь")

        text_parts: List[str] = []
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))

        return "\n".join(text_parts).strip()

    async def parse_intent_text(self, user_text: str) -> str:
        prompt = f"""
Ти роутер намірів для фінансового Telegram-бота.
Поверни СУВОРО лише JSON без markdown.

Формат:
{{
  "intent": "finance_write" | "finance_query" | "finance_advice" | "smalltalk"
}}

Пояснення категорій:
- finance_write: користувач хоче ЗАПИСАТИ нову фінансову дію. Приклади: "кава 200", "зарплата 30000", "АТБ 540"
- finance_query: користувач хоче отримати цифри/звіт/статистику. Приклади: "скільки я витратив за тиждень", "топ категорії"
- finance_advice: користувач хоче аналіз, висновки, поради, рекомендації. Приклади: "на що мені зменшити витрати", "що в мене не так по витратах"
- smalltalk: звичайна розмова, не фінансова дія і не звіт. Приклади: "як справи", "шо нового", "ти тут?"

Повідомлення:
{user_text}
"""
        parsed = await self._call_claude_json(prompt, max_tokens=80)
        return normalize_intent(parsed)

    async def parse_transaction_text(self, user_text: str) -> Dict[str, Any]:
        prompt = f"""
Ти парсер коротких фінансових повідомлень українською.
Поверни СУВОРО лише JSON без markdown, без пояснень, без трійних лапок.

Формат:
{{
  "type": "expense" | "income",
  "amount": number,
  "currency": "{self.default_currency}",
  "category": "рядок",
  "description": "рядок",
  "source_account": "{self.default_source_account}"
}}

Правила:
- expense = витрата
- income = дохід
- amount має бути числом
- якщо користувач поставив мінус, все одно повертай amount додатнім числом
- currency за замовчуванням "{self.default_currency}"
- category коротка і людська
- description короткий нормальний опис
- source_account за замовчуванням "{self.default_source_account}"

Повідомлення:
{user_text}
"""
        parsed = await self._call_claude_json(prompt)
        return normalize_parsed(
            parsed,
            default_currency=self.default_currency,
            default_source_account=self.default_source_account,
        )

    async def parse_balance_setup_text(self, user_text: str) -> Dict[str, Any]:
        prompt = f"""
Ти парсер запитів на встановлення стартових або поточних балансів рахунків.
Поверни СУВОРО лише JSON без markdown, без пояснень, без трійних лапок.

Формат:
{{
  "intent": "balance_setup",
  "accounts": [
    {{
      "name": "назва рахунку",
      "balance": number,
      "currency": "{self.default_currency}"
    }}
  ]
}}

Правила:
- intent завжди "balance_setup"
- accounts це список рахунків
- balance має бути числом
- currency за замовчуванням "{self.default_currency}"
- назви рахунків короткі та людські
- якщо в тексті кілька рахунків, поверни кілька об'єктів у accounts
- якщо є мінус, повертай balance додатнім числом

Повідомлення:
{user_text}
"""
        parsed = await self._call_claude_json(prompt)
        return normalize_balance_setup(
            parsed,
            default_currency=self.default_currency,
        )

    async def parse_transfer_text(self, user_text: str, account_names: List[str]) -> Dict[str, Any]:
        accounts_text = "\n".join(f"- {name}" for name in account_names)

        prompt = f"""
Ти парсер переказів між уже існуючими рахунками.
Поверни СУВОРО лише JSON без markdown, без пояснень, без трійних лапок.

Доступні рахунки:
{accounts_text}

Формат:
{{
  "intent": "transfer",
  "amount": number,
  "currency": "{self.default_currency}",
  "source_account": "точна назва зі списку",
  "destination_account": "точна назва зі списку",
  "description": "рядок"
}}

Правила:
- враховуй неточності, скорочення, відмінки, цифри в назвах рахунків
- не вигадуй нові рахунки
- якщо написано "з X на Y", то X це source_account, Y це destination_account

Повідомлення:
{user_text}
"""
        parsed = await self._call_claude_json(prompt)
        return normalize_transfer(parsed, default_currency=self.default_currency)

    async def parse_last_transaction_action_text(self, user_text: str, account_names: List[str]) -> Dict[str, Any]:
        accounts_text = "\n".join(f"- {name}" for name in account_names)

        prompt = f"""
Ти парсер команд для редагування або видалення останньої транзакції або її частини.
Поверни СУВОРО лише JSON без markdown, без пояснень, без трійних лапок.

Доступні asset-рахунки:
{accounts_text}

Формат:
{{
  "intent": "last_transaction_action",
  "action": "delete" | "update",
  "amount": number | null,
  "currency": "{self.default_currency}",
  "category": "рядок" | null,
  "description": "рядок" | null,
  "source_account": "точна назва зі списку" | null,
  "destination_account": "точна назва зі списку" | null,
  "target_index": number | null,
  "target_category": "рядок" | null,
  "target_description": "рядок" | null
}}

Пояснення:
- action=delete: видалення всієї останньої транзакції або її частини
- action=update: редагування всієї останньої транзакції або її частини
- якщо користувач вказує частину чека або частину групової транзакції, поверни selector:
  - target_index: номер частини, якщо сказано "1 частину", "другу частину" тощо
  - target_category: якщо вказано категорію частини, наприклад "інше", "напої", "продукти"
  - target_description: якщо вказано опис частини

Правила:
- якщо поле не треба змінювати, повертай null
- source_account і destination_account мають бути тільки зі списку
- враховуй опечатки, скорочення, цифри в назвах рахунків

Приклади:

Вхід: видали останню транзакцію
Вихід:
{{
  "intent": "last_transaction_action",
  "action": "delete",
  "amount": null,
  "currency": "{self.default_currency}",
  "category": null,
  "description": null,
  "source_account": null,
  "destination_account": null,
  "target_index": null,
  "target_category": null,
  "target_description": null
}}

Вхід: зміни останню витрату з 200 на 250
Вихід:
{{
  "intent": "last_transaction_action",
  "action": "update",
  "amount": 250,
  "currency": "{self.default_currency}",
  "category": null,
  "description": null,
  "source_account": null,
  "destination_account": null,
  "target_index": null,
  "target_category": null,
  "target_description": null
}}

Вхід: зміни останню витрату "інше" і додай її в цигарки
Вихід:
{{
  "intent": "last_transaction_action",
  "action": "update",
  "amount": null,
  "currency": "{self.default_currency}",
  "category": "Цигарки",
  "description": null,
  "source_account": null,
  "destination_account": null,
  "target_index": null,
  "target_category": "Інше",
  "target_description": null
}}

Вхід: видали з останнього чеку напої
Вихід:
{{
  "intent": "last_transaction_action",
  "action": "delete",
  "amount": null,
  "currency": "{self.default_currency}",
  "category": null,
  "description": null,
  "source_account": null,
  "destination_account": null,
  "target_index": null,
  "target_category": "Напої",
  "target_description": null
}}

Повідомлення:
{user_text}
"""
        parsed = await self._call_claude_json(prompt, max_tokens=500)
        return normalize_last_transaction_action(parsed, default_currency=self.default_currency)

    async def answer_smalltalk(self, user_text: str) -> str:
        prompt = f"""
Ти Telegram-бот з фінансовим ухилом.
Відповідай коротко, природно, українською.
Не вигадуй фінансові дані, якщо тебе про них не питали.
Не намагайся записувати транзакцію.

Повідомлення користувача:
{user_text}
"""
        return await self._call_claude_text(prompt, max_tokens=120)

    async def answer_finance_advice(self, user_text: str, context_json: str) -> str:
        prompt = f"""
Ти фінансовий помічник користувача.
Тобі дано зведений фінансовий контекст у JSON.
На основі нього дай коротку, практичну відповідь українською.

Правила:
- не вигадуй дані поза контекстом
- якщо даних мало, чесно так і скажи
- давай конкретні висновки і 2-4 практичні рекомендації
- не будь занадто багатослівним
- якщо бачиш явні великі категорії витрат, звертай на них увагу

Питання користувача:
{user_text}

Контекст:
{context_json}
"""
        return await self._call_claude_text(prompt, max_tokens=500)
