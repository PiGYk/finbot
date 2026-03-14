import asyncio
import json
import logging
import os
from dataclasses import dataclass
from threading import Lock
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request

from app.services.advisor import AdvisorService
from app.services.budget_service import BudgetService
from app.services.category_rules import CategoryRulesService
from app.services.claude_parser import ClaudeParser
from app.services.firefly_client import FireflyClient
from app.services.pending_store import PendingStore
from app.services.receipt_parser import ReceiptParser
from app.services.reminder_service import ReminderService
from app.services.reports import ReportService

# Нові модулі для покращення
from app.logging_config import setup_logging
from app.receipt_enhancer import ReceiptEnhancer
from app.receipt_formatter import format_receipt_detailed
from app.rate_limiter import claude_limiter, firefly_limiter
from app.validators import validate_transaction, ValidationError

load_dotenv()

# Налаштування логування
logger = setup_logging(
    log_dir=os.getenv("LOG_DIR", "/app/logs"),
    log_level=os.getenv("LOG_LEVEL", "INFO")
)

app = FastAPI()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()

CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "").strip()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001").strip()

FIREFLY_BASE_URL = os.getenv("FIREFLY_BASE_URL", "http://firefly:8080").rstrip("/")
FIREFLY_ACCESS_TOKEN = os.getenv("FIREFLY_ACCESS_TOKEN", "").strip()

DEFAULT_SOURCE_ACCOUNT = os.getenv("DEFAULT_SOURCE_ACCOUNT", "Готівка").strip()
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "UAH").strip()

ALLOWED_CHAT_IDS_RAW = os.getenv("ALLOWED_CHAT_IDS", "").strip()
CATEGORY_RULES_FILE = os.getenv("CATEGORY_RULES_FILE", "/app/data/category_rules.json").strip()
REMINDER_DATA_FILE = os.getenv("REMINDER_DATA_FILE", "/app/data/reminders.json").strip()
BUDGET_DATA_FILE = os.getenv("BUDGET_DATA_FILE", "/app/data/budgets.json").strip()
BOT_TIMEZONE = os.getenv("BOT_TIMEZONE", "Europe/Kyiv").strip()
REMINDER_POLL_SECONDS = int(os.getenv("REMINDER_POLL_SECONDS", "30").strip())

PROFILES_FILE = os.getenv("PROFILES_FILE", "/app/data/bot/profiles.json").strip()
BOT_DATA_ROOT = os.getenv("BOT_DATA_ROOT", "/app/data/bot").strip()

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
TELEGRAM_FILE_API = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}"

reminder_loop_task: Optional[asyncio.Task] = None
profile_reminder_tasks: dict[str, asyncio.Task] = {}
_profiles_lock = Lock()
_runtime_cache: dict[str, "ProfileRuntime"] = {}
bootstrapped_runtime_ids: set[str] = set()
_bootstrap_lock: Optional[asyncio.Lock] = None


@dataclass
class ProfileRuntime:
    profile_id: str
    title: str
    default_currency: str
    default_source_account: str
    firefly: FireflyClient
    claude: ClaudeParser
    reports: ReportService
    advisor: AdvisorService
    category_rules: CategoryRulesService
    receipt_parser: ReceiptParser
    reminder_service: ReminderService
    budget_service: BudgetService
    receipt_enhancer: ReceiptEnhancer  # НОВЕ


def require_env(name: str, value: str) -> None:
    if not value:
        raise RuntimeError(f"{name} is not set")


def validate_required_env() -> None:
    require_env("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
    require_env("TELEGRAM_WEBHOOK_SECRET", TELEGRAM_WEBHOOK_SECRET)
    require_env("CLAUDE_API_KEY", CLAUDE_API_KEY)
    if not profiles_enabled():
        require_env("FIREFLY_ACCESS_TOKEN", FIREFLY_ACCESS_TOKEN)


def parse_allowed_chat_ids(raw: str) -> set[int]:
    result: set[int] = set()

    if not raw:
        return result

    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        try:
            result.add(int(value))
        except ValueError:
            print(f"WARNING: invalid chat id in ALLOWED_CHAT_IDS: {value}")

    return result


def is_chat_allowed(chat_id: int) -> bool:
    if not ALLOWED_CHAT_IDS:
        return True
    return chat_id in ALLOWED_CHAT_IDS


def ensure_file_parent_dir(file_path: str) -> None:
    folder = os.path.dirname(file_path)
    if folder:
        os.makedirs(folder, exist_ok=True)


def ensure_profiles_file_parent() -> None:
    ensure_file_parent_dir(PROFILES_FILE)


def load_profiles_data() -> dict[str, Any]:
    ensure_profiles_file_parent()

    if not os.path.exists(PROFILES_FILE):
        return {
            "profiles": [],
            "chat_access": {},
            "chat_bindings": {},
        }

    with _profiles_lock:
        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("profiles.json має бути JSON-об'єктом")

    if "profiles" not in data or not isinstance(data["profiles"], list):
        data["profiles"] = []

    if "chat_access" not in data or not isinstance(data["chat_access"], dict):
        data["chat_access"] = {}

    if "chat_bindings" not in data or not isinstance(data["chat_bindings"], dict):
        data["chat_bindings"] = {}

    return data


def save_profiles_data(data: dict[str, Any]) -> None:
    ensure_profiles_file_parent()
    with _profiles_lock:
        with open(PROFILES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def profiles_enabled() -> bool:
    try:
        return len(list_profiles()) > 0
    except Exception as e:
        print("PROFILES_LOAD_ERROR =", repr(e))
        return False


def list_profiles() -> list[dict[str, Any]]:
    return list(load_profiles_data().get("profiles", []))


def get_profile(profile_id: str) -> Optional[dict[str, Any]]:
    for profile in list_profiles():
        if profile.get("profile_id") == profile_id:
            return dict(profile)
    return None


def get_allowed_profile_ids_for_chat(chat_id: int) -> list[str]:
    data = load_profiles_data()
    return list(data.get("chat_access", {}).get(str(chat_id), []))


def get_allowed_profiles_for_chat(chat_id: int) -> list[dict[str, Any]]:
    allowed_ids = set(get_allowed_profile_ids_for_chat(chat_id))
    if not allowed_ids:
        return []

    result: list[dict[str, Any]] = []
    for profile in list_profiles():
        if profile.get("profile_id") in allowed_ids:
            result.append(profile)
    return result


def get_bound_profile_id(chat_id: int) -> Optional[str]:
    data = load_profiles_data()
    profile_id = data.get("chat_bindings", {}).get(str(chat_id))

    if not profile_id:
        return None

    allowed_ids = set(get_allowed_profile_ids_for_chat(chat_id))
    if profile_id not in allowed_ids:
        return None

    return profile_id


def bind_chat_to_profile(chat_id: int, profile_id: str) -> None:
    data = load_profiles_data()
    allowed_ids = set(get_allowed_profile_ids_for_chat(chat_id))

    if profile_id not in allowed_ids:
        raise ValueError("Цей chat_id не має доступу до вибраного профілю")

    data.setdefault("chat_bindings", {})[str(chat_id)] = profile_id
    save_profiles_data(data)


def format_start_text(chat_id: int) -> str:
    allowed_profiles = get_allowed_profiles_for_chat(chat_id)
    current_profile_id = get_bound_profile_id(chat_id)
    current_profile = get_profile(current_profile_id) if current_profile_id else None

    if not allowed_profiles:
        return (
            "Доступ ще не налаштовано.\n"
            f"Твій chat_id: {chat_id}\n"
            "Додай цей chat_id у profiles.json в секцію chat_access."
        )

    lines = [f"Твій chat_id: {chat_id}"]

    if current_profile:
        lines.append(f"Поточний профіль: {current_profile.get('title', current_profile_id)}")

    lines.append("")
    lines.append("Оберіть профіль обліку:")

    return "\n".join(lines)


def build_profile_keyboard(chat_id: int) -> Optional[dict]:
    allowed_profiles = get_allowed_profiles_for_chat(chat_id)
    if not allowed_profiles:
        return None

    inline_keyboard = []
    for profile in allowed_profiles:
        inline_keyboard.append(
            [
                {
                    "text": profile.get("title", profile.get("profile_id", "Профіль")),
                    "callback_data": f"bind_profile:{profile.get('profile_id')}",
                }
            ]
        )

    return {"inline_keyboard": inline_keyboard}


def build_profile_runtime(profile: dict[str, Any]) -> ProfileRuntime:
    profile_id = str(profile["profile_id"])
    title = str(profile.get("title") or profile_id)
    firefly_base_url = str(profile.get("firefly_base_url") or FIREFLY_BASE_URL).rstrip("/")
    firefly_access_token = str(profile.get("firefly_access_token") or FIREFLY_ACCESS_TOKEN).strip()
    default_source_account = str(profile.get("default_source_account") or DEFAULT_SOURCE_ACCOUNT).strip()
    default_currency = str(profile.get("default_currency") or DEFAULT_CURRENCY).strip().upper()

    if not firefly_access_token:
        raise ValueError(f"У профілю {profile_id} не заданий firefly_access_token")

    firefly = FireflyClient(
        base_url=firefly_base_url,
        access_token=firefly_access_token,
    )

    claude = ClaudeParser(
        api_key=CLAUDE_API_KEY,
        model=CLAUDE_MODEL,
        default_currency=default_currency,
        default_source_account=default_source_account,
    )

    reports = ReportService(
        firefly=firefly,
        default_currency=default_currency,
    )

    advisor = AdvisorService(
        firefly=firefly,
        claude=claude,
        default_currency=default_currency,
    )

    category_rules = CategoryRulesService(
        file_path=f"{BOT_DATA_ROOT}/category_rules_{profile_id}.json"
    )
    category_rules.ensure_seeded()

    receipt_parser = ReceiptParser(
        api_key=CLAUDE_API_KEY,
        model=CLAUDE_MODEL,
        default_currency=default_currency,
        category_rules=category_rules,
    )

    receipt_enhancer = ReceiptEnhancer(
        api_key=CLAUDE_API_KEY,
        model=CLAUDE_MODEL,
    )

    reminder_service = ReminderService(
        file_path=f"{BOT_DATA_ROOT}/reminders_{profile_id}.json",
        timezone_name=BOT_TIMEZONE,
        poll_seconds=REMINDER_POLL_SECONDS,
    )

    budget_service = BudgetService(
        firefly=firefly,
        default_currency=default_currency,
        file_path=f"{BOT_DATA_ROOT}/budgets_{profile_id}.json",
    )

    return ProfileRuntime(
        profile_id=profile_id,
        title=title,
        default_currency=default_currency,
        default_source_account=default_source_account,
        firefly=firefly,
        claude=claude,
        reports=reports,
        advisor=advisor,
        category_rules=category_rules,
        receipt_parser=receipt_parser,
        receipt_enhancer=receipt_enhancer,  # НОВЕ
        reminder_service=reminder_service,
        budget_service=budget_service,
    )


def get_profile_runtime(profile_id: str) -> ProfileRuntime:
    cached = _runtime_cache.get(profile_id)
    if cached:
        return cached

    profile = get_profile(profile_id)
    if not profile:
        raise ValueError(f"Профіль не знайдено: {profile_id}")

    runtime = build_profile_runtime(profile)
    _runtime_cache[profile_id] = runtime
    return runtime


def get_default_runtime() -> ProfileRuntime:
    cached = _runtime_cache.get("__default__")
    if cached:
        return cached

    firefly = FireflyClient(
        base_url=FIREFLY_BASE_URL,
        access_token=FIREFLY_ACCESS_TOKEN,
    )

    claude = ClaudeParser(
        api_key=CLAUDE_API_KEY,
        model=CLAUDE_MODEL,
        default_currency=DEFAULT_CURRENCY,
        default_source_account=DEFAULT_SOURCE_ACCOUNT,
    )

    reports = ReportService(
        firefly=firefly,
        default_currency=DEFAULT_CURRENCY,
    )

    advisor = AdvisorService(
        firefly=firefly,
        claude=claude,
        default_currency=DEFAULT_CURRENCY,
    )

    category_rules = CategoryRulesService(file_path=CATEGORY_RULES_FILE)
    category_rules.ensure_seeded()

    receipt_parser = ReceiptParser(
        api_key=CLAUDE_API_KEY,
        model=CLAUDE_MODEL,
        default_currency=DEFAULT_CURRENCY,
        category_rules=category_rules,
    )

    receipt_enhancer = ReceiptEnhancer(
        api_key=CLAUDE_API_KEY,
        model=CLAUDE_MODEL,
    )

    reminder_service = ReminderService(
        file_path=REMINDER_DATA_FILE,
        timezone_name=BOT_TIMEZONE,
        poll_seconds=REMINDER_POLL_SECONDS,
    )

    budget_service = BudgetService(
        firefly=firefly,
        default_currency=DEFAULT_CURRENCY,
        file_path=BUDGET_DATA_FILE,
    )

    runtime = ProfileRuntime(
        profile_id="__default__",
        title="Default",
        default_currency=DEFAULT_CURRENCY,
        default_source_account=DEFAULT_SOURCE_ACCOUNT,
        firefly=firefly,
        claude=claude,
        reports=reports,
        advisor=advisor,
        category_rules=category_rules,
        receipt_parser=receipt_parser,
        receipt_enhancer=receipt_enhancer,  # НОВЕ
        reminder_service=reminder_service,
        budget_service=budget_service,
    )

    _runtime_cache["__default__"] = runtime
    return runtime


ALLOWED_CHAT_IDS = parse_allowed_chat_ids(ALLOWED_CHAT_IDS_RAW)

pending_store = PendingStore()


async def send_telegram_message(chat_id: int, text: str, reply_markup: Optional[dict] = None) -> None:
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json=payload,
        )
        response.raise_for_status()


async def edit_telegram_message(chat_id: int, message_id: int, text: str, reply_markup: Optional[dict] = None) -> None:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{TELEGRAM_API}/editMessageText",
            json=payload,
        )
        response.raise_for_status()


async def answer_callback_query(callback_query_id: str, text: Optional[str] = None) -> None:
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{TELEGRAM_API}/answerCallbackQuery",
            json=payload,
        )
        response.raise_for_status()


async def get_telegram_file_bytes(file_id: str) -> tuple[bytes, str]:
    async with httpx.AsyncClient(timeout=60) as client:
        meta_response = await client.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id})
        meta_response.raise_for_status()
        meta_data = meta_response.json()

        if not meta_data.get("ok"):
            raise ValueError(f"Telegram getFile failed: {meta_data}")

        file_path = meta_data["result"]["file_path"]
        download_response = await client.get(f"{TELEGRAM_FILE_API}/{file_path}")
        download_response.raise_for_status()

    lower = file_path.lower()
    if lower.endswith(".png"):
        media_type = "image/png"
    elif lower.endswith(".webp"):
        media_type = "image/webp"
    elif lower.endswith(".gif"):
        media_type = "image/gif"
    else:
        media_type = "image/jpeg"

    return download_response.content, media_type


def canonicalize_category(runtime: ProfileRuntime, text: str) -> str:
    return runtime.category_rules.resolve_category(text, fallback=text) or text


async def ensure_runtime_bootstrapped(runtime: ProfileRuntime) -> None:
    global _bootstrap_lock
    if _bootstrap_lock is None:
        _bootstrap_lock = asyncio.Lock()

    runtime_id = str(runtime.profile_id or "__default__")
    if runtime_id in bootstrapped_runtime_ids:
        return

    async with _bootstrap_lock:
        if runtime_id in bootstrapped_runtime_ids:
            return

        runtime.category_rules.ensure_seeded()
        category_names = runtime.category_rules.list_canonical_categories()
        result = await runtime.firefly.ensure_categories(category_names)
        bootstrapped_runtime_ids.add(runtime_id)
        print(
            "CATEGORY_BOOTSTRAP =",
            runtime_id,
            json.dumps(
                {
                    "created_count": result.get("created_count", 0),
                    "created": result.get("created", []),
                    "total_categories": len(category_names),
                },
                ensure_ascii=False,
            ),
        )


def format_balance_setup_result(results: list[dict]) -> str:
    lines = ["Оновив баланси:"]

    for item in results:
        action = item.get("action")
        account = item.get("account")
        currency = item.get("currency", "UAH")

        if action == "created_with_opening_balance":
            target = item.get("target_balance", 0)
            lines.append(f"• {account}: створив новий рахунок зі стартовим балансом {target:.2f} {currency}")
        elif action == "adjusted":
            current = item.get("current_balance", 0)
            target = item.get("target_balance", 0)
            delta = item.get("delta", 0)
            lines.append(
                f"• {account}: було {current:.2f} {currency}, стало {target:.2f} {currency}, корекція {delta:+.2f} {currency}"
            )
        elif action == "no_change":
            current = item.get("current_balance", 0)
            lines.append(f"• {account}: без змін, уже {current:.2f} {currency}")
        else:
            lines.append(f"• {account}: невідомий результат")

    return "\n".join(lines)


def format_transfer_result(parsed: dict) -> str:
    return (
        f"Записав переказ: {parsed['amount']:.2f} {parsed['currency']}\n"
        f"З: {parsed['source_account']}\n"
        f"На: {parsed['destination_account']}\n"
        f"Опис: {parsed['description']}"
    )


def format_last_transaction_action_result(result: dict, default_currency: str) -> str:
    action = result.get("action")
    currency = result.get("currency", default_currency)

    if action == "deleted":
        return (
            f"Видалив останню транзакцію:\n"
            f"• Тип: {result.get('old_type')}\n"
            f"• Сума: {result.get('old_amount', 0):.2f} {currency}\n"
            f"• Опис: {result.get('old_description')}"
        )

    if action == "deleted_split":
        return (
            f"Видалив частину останньої транзакції:\n"
            f"• Частина: {result.get('target_label')}\n"
            f"• Сума: {result.get('old_amount', 0):.2f} {currency}\n"
            f"• Опис: {result.get('old_description')}"
        )

    if action == "updated":
        lines = [
            "Оновив частину останньої транзакції:" if result.get("target_label") else "Оновив останню транзакцію:",
        ]

        if result.get("target_label"):
            lines.append(f"• Частина: {result.get('target_label')}")

        lines.extend(
            [
                f"• Було: {result.get('old_amount', 0):.2f} {currency} | {result.get('old_description')}",
                f"• Стало: {result.get('new_amount', 0):.2f} {currency} | {result.get('new_description')}",
            ]
        )

        if result.get("old_source_account") != result.get("new_source_account"):
            lines.append(
                f"• Рахунок: {result.get('old_source_account')} → {result.get('new_source_account')}"
            )

        if result.get("old_destination_account") != result.get("new_destination_account"):
            lines.append(
                f"• Призначення: {result.get('old_destination_account')} → {result.get('new_destination_account')}"
            )

        if result.get("old_category") != result.get("new_category"):
            lines.append(
                f"• Категорія: {result.get('old_category')} → {result.get('new_category')}"
            )

        return "\n".join(lines)

    return "Невідомий результат дії над останньою транзакцією."


def format_receipt_preview(receipt: dict, default_currency: str) -> str:
    merchant = receipt.get("merchant") or "Чек"
    currency = receipt.get("currency") or default_currency
    total = receipt.get("receipt_total", 0)
    groups = receipt.get("category_totals", [])

    lines = [
        f"Розібрав чек: {merchant}",
        f"Загальна сума: {total:.2f} {currency}",
    ]

    if groups:
        lines.append("Попередній розподіл:")
        for item in groups:
            lines.append(f"• {item['category']} — {item['amount']:.2f} {currency}")

    lines.append("")
    lines.append("Напиши: «підтвердити чек» або «скасувати чек».")

    return "\n".join(lines)


def format_receipt_commit_result(receipt: dict, result: dict, default_currency: str) -> str:
    merchant = receipt.get("merchant") or "Чек"
    currency = receipt.get("currency") or default_currency
    groups = result.get("groups", [])

    lines = [f"Чек записав: {merchant}"]

    if groups:
        for item in groups:
            lines.append(f"• {item['category']} — {item['amount']:.2f} {currency}")

    return "\n".join(lines)


def is_receipt_confirm_text(text: str) -> bool:
    low = text.strip().lower()
    return low in {
        "підтвердити чек",
        "підтвердити",
        "ок",
        "окей",
        "записуй чек",
        "записуй",
        "так",
    }


def is_receipt_cancel_text(text: str) -> bool:
    low = text.strip().lower()
    return low in {
        "скасувати чек",
        "скасувати",
        "відміна",
        "ні",
        "не треба",
    }


async def ensure_default_reminder_loop() -> None:
    global reminder_loop_task
    if reminder_loop_task is None:
        runtime = get_default_runtime()
        await ensure_runtime_bootstrapped(runtime)
        reminder_loop_task = asyncio.create_task(
            runtime.reminder_service.run_forever(send_telegram_message)
        )


async def ensure_profile_reminder_loop(profile_id: str) -> None:
    if profile_id in profile_reminder_tasks:
        return

    runtime = get_profile_runtime(profile_id)
    await ensure_runtime_bootstrapped(runtime)
    profile_reminder_tasks[profile_id] = asyncio.create_task(
        runtime.reminder_service.run_forever(send_telegram_message)
    )


async def send_profile_picker(chat_id: int) -> None:
    await send_telegram_message(
        chat_id,
        format_start_text(chat_id),
        reply_markup=build_profile_keyboard(chat_id),
    )


@app.on_event("startup")
async def on_startup() -> None:
    validate_required_env()
    if profiles_enabled():
        for profile in list_profiles():
            profile_id = profile.get("profile_id")
            if profile_id:
                await ensure_profile_reminder_loop(str(profile_id))
    else:
        runtime = get_default_runtime()
        await ensure_runtime_bootstrapped(runtime)
        await ensure_default_reminder_loop()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global reminder_loop_task

    if reminder_loop_task is not None:
        reminder_loop_task.cancel()
        try:
            await reminder_loop_task
        except asyncio.CancelledError:
            pass
        reminder_loop_task = None

    for profile_id, task in list(profile_reminder_tasks.items()):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        profile_reminder_tasks.pop(profile_id, None)


@app.get("/health")
async def health() -> dict:
    profiles_count = len(list_profiles()) if profiles_enabled() else 0

    if profiles_enabled():
        reminders_count = 0
        budgets_count = 0
        category_rules_count = 0

        for profile in list_profiles():
            profile_id = str(profile.get("profile_id"))
            if not profile_id:
                continue
            runtime = get_profile_runtime(profile_id)
            reminders_count += runtime.reminder_service.count()
            budgets_count += runtime.budget_service.count()
            category_rules_count += len(runtime.category_rules.list_rules())
    else:
        runtime = get_default_runtime()
        reminders_count = runtime.reminder_service.count()
        budgets_count = runtime.budget_service.count()
        category_rules_count = len(runtime.category_rules.list_rules())

    return {
        "ok": True,
        "whitelist_enabled": bool(ALLOWED_CHAT_IDS),
        "allowed_chat_ids_count": len(ALLOWED_CHAT_IDS),
        "profiles_enabled": profiles_enabled(),
        "profiles_count": profiles_count,
        "profile_bindings_count": len(load_profiles_data().get("chat_bindings", {})) if profiles_enabled() else 0,
        "category_rules_count": category_rules_count,
        "reminders_count": reminders_count,
        "budgets_count": budgets_count,
    }


@app.post("/telegram/{secret}")
async def telegram_webhook(secret: str, request: Request) -> dict:
    if secret != TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    update = await request.json()

    if profiles_enabled():
        callback_query = update.get("callback_query")
        if callback_query:
            callback_id = callback_query.get("id")
            data = callback_query.get("data", "")
            message = callback_query.get("message", {})
            chat = message.get("chat", {})
            chat_id = chat.get("id")
            message_id = message.get("message_id")

            if callback_id:
                await answer_callback_query(callback_id)

            if not chat_id or not message_id:
                return {"ok": True}

            if data.startswith("bind_profile:"):
                profile_id = data.split(":", 1)[1]

                try:
                    bind_chat_to_profile(chat_id, profile_id)
                    await ensure_profile_reminder_loop(profile_id)
                    await ensure_runtime_bootstrapped(get_profile_runtime(profile_id))

                    profile = get_profile(profile_id)
                    title = profile.get("title", profile_id) if profile else profile_id

                    await edit_telegram_message(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=(
                            f"Профіль обрано: {title}\n"
                            f"Твій chat_id: {chat_id}\n"
                            "Тепер можеш вести облік у цьому профілі."
                        ),
                    )
                except Exception as e:
                    await edit_telegram_message(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=f"Не зміг прив’язати профіль: {str(e)}",
                    )

            return {"ok": True}

    message = update.get("message") or update.get("edited_message")
    if not message:
        return {"ok": True}

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text")
    photo = message.get("photo")

    if not chat_id:
        return {"ok": True}

    if profiles_enabled():
        if text and text.strip().lower() in {"/start", "/profile", "змінити профіль"}:
            await send_profile_picker(chat_id)
            return {"ok": True}

        allowed_profiles = get_allowed_profiles_for_chat(chat_id)
        if not allowed_profiles:
            await send_telegram_message(chat_id, format_start_text(chat_id))
            return {"ok": True, "unauthorized_chat_id": chat_id}

        bound_profile_id = get_bound_profile_id(chat_id)
        if not bound_profile_id:
            await send_profile_picker(chat_id)
            return {"ok": True}

        runtime = get_profile_runtime(bound_profile_id)
    else:
        if not is_chat_allowed(chat_id):
            await send_telegram_message(
                chat_id,
                (
                    "Доступ заборонено.\n"
                    f"Твій chat_id: {chat_id}\n"
                    "Додай його в ALLOWED_CHAT_IDS у .env, якщо це довірений чат."
                ),
            )
            return {"ok": True, "unauthorized_chat_id": chat_id}

        runtime = get_default_runtime()

    await ensure_runtime_bootstrapped(runtime)

    try:
        pending = await pending_store.get(chat_id)

        if pending and pending.get("kind") == "receipt_confirm":
            if photo:
                await send_telegram_message(chat_id, "Спочатку підтвердь або скасуй попередній чек.")
                return {"ok": True}

            if not text:
                await send_telegram_message(chat_id, "Напиши «підтвердити чек» або «скасувати чек».")
                return {"ok": True}

            if is_receipt_confirm_text(text):
                receipt = pending["payload"]
                result = await runtime.firefly.create_receipt_transactions(
                    receipt=receipt,
                    default_source_account=runtime.default_source_account,
                    default_currency=runtime.default_currency,
                )
                await pending_store.clear(chat_id)
                await send_telegram_message(
                    chat_id,
                    format_receipt_commit_result(receipt, result, runtime.default_currency),
                )
                return {"ok": True}

            if is_receipt_cancel_text(text):
                await pending_store.clear(chat_id)
                await send_telegram_message(chat_id, "Окей, чек скасовано. Нічого не записував.")
                return {"ok": True}

            await send_telegram_message(chat_id, "Спочатку підтвердь або скасуй чек.")
            return {"ok": True}

        if photo:
            largest_photo = photo[-1]
            file_id = largest_photo["file_id"]
            image_bytes, media_type = await get_telegram_file_bytes(file_id)
            
            # Парсити чек
            parsed_receipt = await runtime.receipt_parser.parse_receipt_image(image_bytes, media_type)
            
            # НОВЕ: Покращити категоризацію позицій
            logger.debug(f"Enhancing receipt categories for {parsed_receipt['merchant']}")
            enhanced_items = await runtime.receipt_enhancer.enhance_receipt_categories(
                items=parsed_receipt["items"],
                merchant=parsed_receipt["merchant"]
            )
            parsed_receipt["items"] = enhanced_items
            
            # Зберегти у pending
            await pending_store.set(chat_id, "receipt_confirm", parsed_receipt)
            
            # НОВЕ: використовувати детальний формат чека
            receipt_message = format_receipt_detailed(parsed_receipt, show_categories=True)
            await send_telegram_message(chat_id, receipt_message)
            return {"ok": True}

        if not text:
            await send_telegram_message(chat_id, "Поки що я обробляю текстові повідомлення і фото чеків.")
            return {"ok": True}

        # Rate limiting
        if not claude_limiter.check_and_wait(chat_id):
            await send_telegram_message(
                chat_id,
                "⚠️ Занадто багато запитів за мінуту. Зачекайте трохи перед наступною командою."
            )
            logger.warning(f"Rate limit triggered for chat_id {chat_id}")
            return {"ok": True}

        if await runtime.claude.looks_like_balance_setup_request(text):
            parsed_setup = await runtime.claude.parse_balance_setup_text(text)
            results = await runtime.firefly.setup_balances(parsed_setup["accounts"])
            await send_telegram_message(chat_id, format_balance_setup_result(results))
            return {"ok": True}

        if runtime.claude.looks_like_category_create_request(text):
            parsed_category = await runtime.claude.parse_category_create_text(text)
            canonical_name = parsed_category["canonical_name"]

            await runtime.firefly.ensure_category(canonical_name)
            rule = runtime.category_rules.upsert_rule(
                canonical_name=canonical_name,
                aliases=parsed_category["aliases"],
            )

            await send_telegram_message(chat_id, runtime.category_rules.format_rule_result(rule))
            return {"ok": True}

        if runtime.claude.looks_like_reminder_request(text):
            parsed_reminder = await runtime.claude.parse_reminder_create_text(text)
            reminder = runtime.reminder_service.create_daily_reminder(
                chat_id=chat_id,
                text=parsed_reminder["text"],
                hour=parsed_reminder["hour"],
                minute=parsed_reminder["minute"],
            )
            await send_telegram_message(chat_id, runtime.reminder_service.format_created_result(reminder))
            return {"ok": True}

        if runtime.claude.looks_like_budget_create_request(text):
            parsed_budget = await runtime.claude.parse_budget_create_text(text)
            budget = await runtime.budget_service.create_budget_plan(
                chat_id=chat_id,
                amount=parsed_budget["amount"],
                title=parsed_budget["title"],
            )
            await send_telegram_message(chat_id, runtime.budget_service.format_plan(budget))
            return {"ok": True}

        if await runtime.claude.looks_like_transfer_request(text):
            account_names = await runtime.firefly.list_asset_account_names()
            parsed_transfer = await runtime.claude.parse_transfer_text(text, account_names)
            await runtime.firefly.create_transfer(parsed_transfer)
            await send_telegram_message(chat_id, format_transfer_result(parsed_transfer))
            return {"ok": True}

        if await runtime.claude.looks_like_last_transaction_action_request(text):
            account_names = await runtime.firefly.list_asset_account_names()
            action_spec = await runtime.claude.parse_last_transaction_action_text(text, account_names)

            if action_spec.get("category"):
                action_spec["category"] = canonicalize_category(runtime, action_spec["category"])

            result = await runtime.firefly.apply_last_transaction_action(
                action_spec=action_spec,
                default_currency=runtime.default_currency,
                default_source_account=runtime.default_source_account,
            )
            await send_telegram_message(
                chat_id,
                format_last_transaction_action_result(result, runtime.default_currency),
            )
            return {"ok": True}

        intent = await runtime.claude.parse_intent_text(text)

        if intent == "smalltalk":
            reply = await runtime.claude.answer_smalltalk(text)
            await send_telegram_message(chat_id, reply)
            return {"ok": True}

        if intent == "finance_query":
            report_reply = await runtime.reports.handle_report_request(text)
            if report_reply:
                await send_telegram_message(chat_id, report_reply)
                return {"ok": True}

            advice_reply = await runtime.advisor.answer_question(text)
            await send_telegram_message(chat_id, advice_reply)
            return {"ok": True}

        if intent == "finance_advice":
            advice_reply = await runtime.advisor.answer_question(text)
            await send_telegram_message(chat_id, advice_reply)
            return {"ok": True}

        # Отримати список існуючих рахунків для більш точного розпізнавання
        account_names = await runtime.firefly.list_asset_account_names()
        parsed = await runtime.claude.parse_transaction_text(text, account_names=account_names)
        parsed["category"] = canonicalize_category(runtime, parsed['category'])
        
        # НОВЕ: Валідація перед записом
        try:
            validate_transaction(parsed)
        except ValidationError as e:
            logger.warning(f"Validation error for chat_id {chat_id}: {str(e)}")
            await send_telegram_message(chat_id, f"❌ Помилка: {str(e)}")
            return {"ok": True}
        
        # Записати у Firefly
        await runtime.firefly.create_transaction(parsed)
        
        logger.info(f"Transaction recorded: {parsed['type']} {parsed['amount']} {parsed['currency']}")

        reply_text = (
            f"✅ Записав: {parsed['type']} | {parsed['amount']} {parsed['currency']} | "
            f"{parsed['category']} | {parsed['description']} | рахунок: {parsed['source_account']}"
        )

        if runtime.budget_service.should_auto_suggest_after_income(text, parsed):
            budget = await runtime.budget_service.create_budget_plan(
                chat_id=chat_id,
                amount=parsed["amount"],
                title=f"План після доходу: {parsed['description']}",
            )
            reply_text += "\n\n" + runtime.budget_service.format_plan(
                budget,
                intro="Дохід записав. Рекомендую одразу так розкласти суму:",
            )

        await send_telegram_message(chat_id, reply_text)

    except Exception as e:
        logger.error(f"Unexpected error in webhook for chat_id {chat_id}: {repr(e)}")
        await send_telegram_message(chat_id, f"❌ Не зміг обробити повідомлення: {str(e)}")

    return {"ok": True}
