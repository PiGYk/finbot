import calendar
import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List


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


def normalize_category_create(parsed: Dict[str, Any]) -> Dict[str, Any]:
    intent = normalize_text(parsed.get("intent"), "").lower()
    if intent != "create_category":
        raise ValueError(f"Непідтримуваний intent: {intent}")

    canonical_name = normalize_text(parsed.get("canonical_name"), "")
    if not canonical_name:
        raise ValueError("Claude не визначив canonical_name для нової категорії")

    aliases_raw = parsed.get("aliases")
    aliases: List[str] = []

    if isinstance(aliases_raw, list):
        for item in aliases_raw:
            alias = str(item or "").strip()
            if alias:
                aliases.append(alias)

    if canonical_name not in aliases:
        aliases.insert(0, canonical_name)

    return {
        "intent": "create_category",
        "canonical_name": canonical_name,
        "aliases": aliases,
    }


def normalize_reminder_create(parsed: Dict[str, Any]) -> Dict[str, Any]:
    intent = normalize_text(parsed.get("intent"), "").lower()
    if intent != "create_reminder":
        raise ValueError(f"Непідтримуваний intent: {intent}")

    kind = normalize_text(parsed.get("kind"), "daily").lower()
    if kind != "daily":
        raise ValueError("Поки що підтримується тільки daily reminder")

    text = normalize_text(parsed.get("text"), "")
    if not text:
        raise ValueError("Claude не визначив текст нагадування")

    try:
        hour = int(parsed.get("hour"))
        minute = int(parsed.get("minute", 0))
    except (TypeError, ValueError):
        raise ValueError("Claude не визначив коректний час нагадування")

    if hour < 0 or hour > 23:
        raise ValueError("Година нагадування має бути від 0 до 23")

    if minute < 0 or minute > 59:
        raise ValueError("Хвилини нагадування мають бути від 0 до 59")

    return {
        "intent": "create_reminder",
        "kind": "daily",
        "hour": hour,
        "minute": minute,
        "text": text,
    }


def normalize_budget_create(parsed: Dict[str, Any], default_currency: str) -> Dict[str, Any]:
    intent = normalize_text(parsed.get("intent"), "").lower()
    if intent != "create_budget":
        raise ValueError(f"Непідтримуваний intent: {intent}")

    amount = normalize_amount(parsed.get("amount"))
    currency = normalize_text(parsed.get("currency"), default_currency).upper()
    title = normalize_text(parsed.get("title"), "Новий бюджет")

    return {
        "intent": "create_budget",
        "amount": amount,
        "currency": currency,
        "title": title,
    }


def normalize_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def validate_iso_date(raw: str) -> str:
    text = normalize_text(raw, "")
    if not text:
        raise ValueError("Не вдалося визначити дату підписки")
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError as e:
        raise ValueError(f"Некоректна дата підписки: {text}") from e


def resolve_subscription_date(
    repeat_freq: str,
    raw_date: Any,
    day_of_month: int | None,
    weekday: int | None,
    month: int | None,
    day: int | None,
) -> str:
    if raw_date:
        return validate_iso_date(str(raw_date))

    today = datetime.now().date()

    if repeat_freq == "daily":
        return today.isoformat()

    if repeat_freq == "weekly":
        weekday = weekday or (today.weekday() + 1)
        weekday = max(1, min(7, weekday))
        delta_days = (weekday - 1 - today.weekday()) % 7
        return (today + timedelta(days=delta_days)).isoformat()

    if repeat_freq == "monthly":
        day_of_month = day_of_month or day or today.day
        day_of_month = max(1, min(31, day_of_month))

        year = today.year
        month_value = today.month
        candidate_day = min(day_of_month, calendar.monthrange(year, month_value)[1])
        candidate = date(year, month_value, candidate_day)

        if candidate < today:
            if month_value == 12:
                year += 1
                month_value = 1
            else:
                month_value += 1
            candidate_day = min(day_of_month, calendar.monthrange(year, month_value)[1])
            candidate = date(year, month_value, candidate_day)

        return candidate.isoformat()

    if repeat_freq == "yearly":
        month = month or today.month
        day = day or day_of_month or today.day

        month = max(1, min(12, month))
        day = max(1, min(31, day))

        candidate_day = min(day, calendar.monthrange(today.year, month)[1])
        candidate = date(today.year, month, candidate_day)

        if candidate < today:
            next_year = today.year + 1
            candidate_day = min(day, calendar.monthrange(next_year, month)[1])
            candidate = date(next_year, month, candidate_day)

        return candidate.isoformat()

    raise ValueError(f"Непідтримуваний repeat_freq: {repeat_freq}")


def normalize_subscription_create(parsed: Dict[str, Any], default_currency: str) -> Dict[str, Any]:
    intent = normalize_text(parsed.get("intent"), "").lower()
    if intent != "create_subscription":
        raise ValueError(f"Непідтримуваний intent: {intent}")

    amount = normalize_amount(parsed.get("amount"))
    currency = normalize_text(parsed.get("currency"), default_currency).upper()
    repeat_freq = normalize_text(parsed.get("repeat_freq"), "monthly").lower()

    if repeat_freq not in {"daily", "weekly", "monthly", "yearly"}:
        raise ValueError(f"Непідтримуваний repeat_freq: {repeat_freq}")

    skip = parsed.get("skip", 0)
    try:
        skip = int(skip)
    except (TypeError, ValueError):
        skip = 0
    if skip < 0:
        skip = 0

    day_of_month = normalize_optional_int(parsed.get("day_of_month"))
    weekday = normalize_optional_int(parsed.get("weekday"))
    month = normalize_optional_int(parsed.get("month"))
    day = normalize_optional_int(parsed.get("day"))

    sub_date = resolve_subscription_date(
        repeat_freq=repeat_freq,
        raw_date=parsed.get("date"),
        day_of_month=day_of_month,
        weekday=weekday,
        month=month,
        day=day,
    )

    name = normalize_text(parsed.get("name"), "")
    if not name:
        if repeat_freq == "monthly" and day_of_month:
            name = f"Підписка {day_of_month} числа"
        elif repeat_freq == "weekly" and weekday:
            name = "Щотижнева підписка"
        elif repeat_freq == "yearly":
            name = "Щорічна підписка"
        else:
            name = "Регулярна підписка"

    notes = normalize_text(parsed.get("notes"), "")

    return {
        "intent": "create_subscription",
        "name": name,
        "amount": amount,
        "currency": currency,
        "repeat_freq": repeat_freq,
        "date": sub_date,
        "skip": skip,
        "notes": notes or None,
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

    def looks_like_category_create_request(self, text: str) -> bool:
        low = text.strip().lower()
        return (
            "категор" in low
            and (
                "додай нов" in low
                or "створи нов" in low
                or "додай категор" in low
                or "створи категор" in low
            )
        )

    def looks_like_reminder_request(self, text: str) -> bool:
        low = text.strip().lower()
        return "нагад" in low and ("кожен день" in low or "щодня" in low or "щоденно" in low)

    def looks_like_budget_create_request(self, text: str) -> bool:
        low = text.strip().lower()
        return (
            "бюджет" in low
            and (
                "створи" in low
                or "створити" in low
                or "зроби" in low
                or "розпиши" in low
                or "розділи" in low
            )
        )

    def looks_like_subscription_create_request(self, text: str) -> bool:
        low = text.strip().lower()
        create_words = ("додай", "додати", "створи", "створити", "зроби", "оформи", "оформити", "заведи")
        schedule_words = (
            "кожен",
            "щодня",
            "щотиж",
            "щоміся",
            "щорок",
            "числ",
            "понед",
            "вівтор",
            "серед",
            "четвер",
            "пʼят",
            "пят",
            "субот",
            "неділ",
        )

        has_create_word = any(word in low for word in create_words)
        has_schedule_word = any(word in low for word in schedule_words)
        has_subscription_word = (
            "підписк" in low
            or "регулярн" in low
            or "автоспис" in low
            or "регулярний плат" in low
            or "регулярну оплат" in low
        )
        has_amount_hint = any(ch.isdigit() for ch in low)

        return has_subscription_word and (has_create_word or (has_schedule_word and has_amount_hint))

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
            "messages": [{"role": "user", "content": prompt}],
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
            "messages": [{"role": "user", "content": prompt}],
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
- finance_write: користувач хоче ЗАПИСАТИ нову фінансову дію
- finance_query: користувач хоче отримати цифри/звіт/статистику
- finance_advice: користувач хоче аналіз, висновки, поради, рекомендації
- smalltalk: звичайна розмова, не фінансова дія і не звіт

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

Повідомлення:
{user_text}
"""
        parsed = await self._call_claude_json(prompt)
        return normalize_balance_setup(parsed, default_currency=self.default_currency)

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

Повідомлення:
{user_text}
"""
        parsed = await self._call_claude_json(prompt, max_tokens=500)
        return normalize_last_transaction_action(parsed, default_currency=self.default_currency)

    async def parse_category_create_text(self, user_text: str) -> Dict[str, Any]:
        prompt = f"""
Ти парсер команд на створення нової фінансової категорії.
Поверни СУВОРО лише JSON без markdown.

Формат:
{{
  "intent": "create_category",
  "canonical_name": "канонічна назва категорії",
  "aliases": ["аліас 1", "аліас 2", "аліас 3"]
}}

Правила:
- canonical_name має бути чистою й красивою назвою категорії
- aliases мають включати найпоширеніші варіанти написання, кирилицю, латиницю, дефіси, поширені помилки і скорочення

Повідомлення:
{user_text}
"""
        parsed = await self._call_claude_json(prompt, max_tokens=300)
        return normalize_category_create(parsed)

    async def parse_reminder_create_text(self, user_text: str) -> Dict[str, Any]:
        prompt = f"""
Ти парсер нагадувань для Telegram-бота.
Поверни СУВОРО лише JSON без markdown.

Формат:
{{
  "intent": "create_reminder",
  "kind": "daily",
  "hour": 9,
  "minute": 0,
  "text": "текст нагадування"
}}

Правила:
- поки що підтримуємо лише щоденні нагадування
- якщо користувач пише "о 9", minute = 0
- текст нагадування має бути коротким і зрозумілим

Приклад:
Вхід: нагадуй мені кожен день о 9 ранку відкладати 500 грн
Вихід:
{{
  "intent": "create_reminder",
  "kind": "daily",
  "hour": 9,
  "minute": 0,
  "text": "Відкласти 500 грн"
}}

Повідомлення:
{user_text}
"""
        parsed = await self._call_claude_json(prompt, max_tokens=200)
        return normalize_reminder_create(parsed)

    async def parse_budget_create_text(self, user_text: str) -> Dict[str, Any]:
        prompt = f"""
Ти парсер команд на створення бюджет-плану.
Поверни СУВОРО лише JSON без markdown.

Формат:
{{
  "intent": "create_budget",
  "amount": number,
  "currency": "{self.default_currency}",
  "title": "назва бюджету"
}}

Приклад:
Вхід: створи бюджет на 30000
Вихід:
{{
  "intent": "create_budget",
  "amount": 30000,
  "currency": "{self.default_currency}",
  "title": "Бюджет на 30000"
}}

Повідомлення:
{user_text}
"""
        parsed = await self._call_claude_json(prompt, max_tokens=180)
        return normalize_budget_create(parsed, default_currency=self.default_currency)

    async def parse_subscription_create_text(self, user_text: str) -> Dict[str, Any]:
        prompt = f"""
Ти парсер команд на створення підписок у Firefly III.
Поверни СУВОРО лише JSON без markdown.

Формат:
{{
  "intent": "create_subscription",
  "name": "назва підписки",
  "amount": number,
  "currency": "{self.default_currency}",
  "repeat_freq": "daily" | "weekly" | "monthly" | "yearly",
  "date": "YYYY-MM-DD" | null,
  "day_of_month": number | null,
  "weekday": number | null,
  "month": number | null,
  "day": number | null,
  "skip": number,
  "notes": "рядок" | null
}}

Правила:
- якщо користувач пише "20-те число" або "20 числа", це monthly і day_of_month = 20
- weekday: понеділок=1, вівторок=2, середа=3, четвер=4, пʼятниця=5, субота=6, неділя=7
- якщо користувач пише "раз на 2 місяці", то repeat_freq = monthly і skip = 1
- якщо користувач пише "раз на 3 місяці", то repeat_freq = monthly і skip = 2
- якщо явної назви нема, придумай коротку зрозумілу назву
- amount має бути додатним числом
- date можна не заповнювати, якщо розклад описаний через day_of_month / weekday / month + day

Приклади:
Вхід: додай підписку netflix 20 числа 239 грн
Вихід:
{{
  "intent": "create_subscription",
  "name": "Netflix",
  "amount": 239,
  "currency": "{self.default_currency}",
  "repeat_freq": "monthly",
  "date": null,
  "day_of_month": 20,
  "weekday": null,
  "month": null,
  "day": null,
  "skip": 0,
  "notes": null
}}

Вхід: створи регулярний платіж спортзал щопонеділка 300 грн
Вихід:
{{
  "intent": "create_subscription",
  "name": "Спортзал",
  "amount": 300,
  "currency": "{self.default_currency}",
  "repeat_freq": "weekly",
  "date": null,
  "day_of_month": null,
  "weekday": 1,
  "month": null,
  "day": null,
  "skip": 0,
  "notes": null
}}

Повідомлення:
{user_text}
"""
        parsed = await self._call_claude_json(prompt, max_tokens=260)
        return normalize_subscription_create(parsed, default_currency=self.default_currency)

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

Питання користувача:
{user_text}

Контекст:
{context_json}
"""
        return await self._call_claude_text(prompt, max_tokens=500)
