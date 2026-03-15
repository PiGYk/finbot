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
from app.user_preferences import user_preferences  # Обраний рахунок

# Нові модулі для покращення
from app.logging_config import setup_logging
from app.receipt_enhancer import ReceiptEnhancer
from app.receipt_formatter import format_receipt_detailed
from app.receipt_pipeline_logger import ReceiptPipelineLogger  # ФАЗА 1
from app.receipt_review_formatter import (  # ФАЗА 4
    format_receipt_item_review,
    format_receipt_review_menu,
    format_receipt_name_input_prompt,
    format_receipt_category_selector,
    format_correction_saved,
    format_review_complete,
)
from app.receipt_review_state import review_manager  # ФАЗА 4
from app.rate_limiter import claude_limiter, firefly_limiter
from app.validators import validate_transaction, ValidationError
from app.services.speech_to_text import SpeechToTextService
from app.services.recurring_transfers import RecurringTransfersService
from app.services.recurring_parser import parse_frequency_and_time
from app.services.list_parser import list_parser  # ФАЗА 7: List recognition

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

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

# НОВЕ: Налаштування OCR для чеків
RECEIPT_OCR_PROVIDER = os.getenv("RECEIPT_OCR_PROVIDER", "claude").strip().lower()  # claude або openai
RECEIPT_OCR_MODEL_OPENAI = os.getenv("RECEIPT_OCR_MODEL_OPENAI", "gpt-4o-mini").strip()  # gpt-4o-mini, gpt-4o

FIREFLY_BASE_URL = os.getenv("FIREFLY_BASE_URL", "http://firefly:8080").rstrip("/")
FIREFLY_ACCESS_TOKEN = os.getenv("FIREFLY_ACCESS_TOKEN", "").strip()

DEFAULT_SOURCE_ACCOUNT = os.getenv("DEFAULT_SOURCE_ACCOUNT", "Готівка").strip()
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "UAH").strip()

ALLOWED_CHAT_IDS_RAW = os.getenv("ALLOWED_CHAT_IDS", "").strip()
CATEGORY_RULES_FILE = os.getenv("CATEGORY_RULES_FILE", "/app/data/category_rules.json").strip()
REMINDER_DATA_FILE = os.getenv("REMINDER_DATA_FILE", "/app/data/reminders.json").strip()
BUDGET_DATA_FILE = os.getenv("BUDGET_DATA_FILE", "/app/data/budgets.json").strip()
RECURRING_TRANSFERS_FILE = os.getenv("RECURRING_TRANSFERS_FILE", "/app/data/recurring_transfers.json").strip()
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

# НОВЕ: Зберігання останніх скасованих операцій для undo
last_cancelled: dict[int, dict] = {}
last_deleted_transaction: dict[int, dict] = {}  # Для undo видалених транзакцій


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
    speech_to_text: SpeechToTextService  # НОВЕ: розпізнавання голосу
    recurring_transfers: RecurringTransfersService  # НОВЕ: регулярні перекази


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

    reports = ReportService(
        firefly=firefly,
        default_currency=default_currency,
    )

    category_rules = CategoryRulesService(
        file_path=f"{BOT_DATA_ROOT}/category_rules_{profile_id}.json"
    )
    category_rules.ensure_seeded()

    claude = ClaudeParser(
        api_key=CLAUDE_API_KEY,
        model=CLAUDE_MODEL,
        default_currency=default_currency,
        default_source_account=default_source_account,
        category_rules=category_rules,  # НОВЕ: передаємо категорії
    )

    advisor = AdvisorService(
        firefly=firefly,
        claude=claude,
        default_currency=default_currency,
    )

    receipt_parser = ReceiptParser(
        api_key=CLAUDE_API_KEY,
        model=CLAUDE_MODEL,
        default_currency=default_currency,
        category_rules=category_rules,
        provider=RECEIPT_OCR_PROVIDER,  # НОВЕ: claude або openai
        openai_api_key=OPENAI_API_KEY,  # НОВЕ: для OpenAI Vision
        openai_model=RECEIPT_OCR_MODEL_OPENAI,  # НОВЕ: gpt-4o-mini
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

    speech_to_text = SpeechToTextService(
        api_key=OPENAI_API_KEY,
        model="whisper-1",
    )

    recurring_transfers = RecurringTransfersService(
        file_path=f"{BOT_DATA_ROOT}/recurring_transfers_{profile_id}.json",
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
        speech_to_text=speech_to_text,  # НОВЕ: розпізнавання голосу
        recurring_transfers=recurring_transfers,  # НОВЕ: регулярні перекази
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

    reports = ReportService(
        firefly=firefly,
        default_currency=DEFAULT_CURRENCY,
    )

    category_rules = CategoryRulesService(file_path=CATEGORY_RULES_FILE)
    category_rules.ensure_seeded()

    claude = ClaudeParser(
        api_key=CLAUDE_API_KEY,
        model=CLAUDE_MODEL,
        default_currency=DEFAULT_CURRENCY,
        default_source_account=DEFAULT_SOURCE_ACCOUNT,
        category_rules=category_rules,  # НОВЕ: передаємо категорії
    )

    advisor = AdvisorService(
        firefly=firefly,
        claude=claude,
        default_currency=DEFAULT_CURRENCY,
    )

    receipt_parser = ReceiptParser(
        api_key=CLAUDE_API_KEY,
        model=CLAUDE_MODEL,
        default_currency=DEFAULT_CURRENCY,
        category_rules=category_rules,
        provider=RECEIPT_OCR_PROVIDER,  # НОВЕ: claude або openai
        openai_api_key=OPENAI_API_KEY,  # НОВЕ: для OpenAI Vision
        openai_model=RECEIPT_OCR_MODEL_OPENAI,  # НОВЕ: gpt-4o-mini
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

    speech_to_text = SpeechToTextService(
        api_key=OPENAI_API_KEY,
        model="whisper-1",
    )

    recurring_transfers = RecurringTransfersService(
        file_path=RECURRING_TRANSFERS_FILE,
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
        speech_to_text=speech_to_text,  # НОВЕ: розпізнавання голосу
        recurring_transfers=recurring_transfers,  # НОВЕ: регулярні перекази
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

    # НОВЕ: обробка видалення кількох транзакцій
    if action == "deleted_multiple":
        items = result.get("items", [])
        count = result.get("count", 0)
        lines = [f"✅ Видалив {count} останніх транзакцій:"]
        for i, item in enumerate(items, 1):
            lines.append(
                f"{i}. {item.get('description')} - {item.get('amount', 0):.2f} {item.get('currency', currency)}"
            )
        return "\n".join(lines)

    if action == "deleted":
        return (
            f"✅ Видалив останню транзакцію:\n"
            f"• Тип: {result.get('old_type')}\n"
            f"• Сума: {result.get('old_amount', 0):.2f} {currency}\n"
            f"• Опис: {result.get('old_description')}\n\n"
            f"💡 Якщо помилково - напиши «поверни назад»"
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
            lines.append(f"• {item['category']} - {item['amount']:.2f} {currency}")

    lines.append("")
    lines.append("Напиши: «підтвердити чек» або «скасувати чек».")

    return "\n".join(lines)


def format_receipt_commit_result(receipt: dict, result: dict, default_currency: str) -> str:
    merchant = receipt.get("merchant") or "Чек"
    currency = receipt.get("currency") or default_currency
    items = result.get("items", [])  # ЗМІНЕНО: використовуємо items замість groups

    lines = [f"✅ Чек записано: {merchant}"]

    if items:
        for item in items:
            # ЗМІНЕНО: показуємо назву товару + категорію + суму
            lines.append(f"• {item['name']} ({item['category']}) - {item['amount']:.2f} {currency}")

    lines.append(f"\n📊 Всього позицій: {len(items)}")
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


def is_undo_text(text: str) -> bool:
    """Перевірка команди відновлення останньої скасованої операції."""
    low = text.strip().lower()
    return low in {
        "поверни назад",
        "поверни",
        "відновити",
        "відновити чек",
        "undo",
        "повернути",
        "верни",
        "передумав",
        "помилка",
    }


async def ensure_default_reminder_loop() -> None:
    global reminder_loop_task
    if reminder_loop_task is None:
        runtime = get_default_runtime()
        await ensure_runtime_bootstrapped(runtime)
        reminder_loop_task = asyncio.create_task(
            runtime.reminder_service.run_forever(send_telegram_message)
        )

        # НОВЕ: Запустити цикл регулярних переказів для default профіля
        default_recurring_task_id = "__default___recurring"
        if default_recurring_task_id not in profile_reminder_tasks:

            async def default_recurring_callback(transfer: dict) -> None:
                """Callback для default профіля."""
                try:
                    # Повідомити першому користувачу з default профілем
                    for chat_id, bound_profile in bound_profiles.items():
                        if bound_profile == "__default__":
                            await send_telegram_message(
                                chat_id,
                                f"✅ Регулярний переказ виконаний!\n\n"
                                f"Від: {transfer['source_account']}\n"
                                f"На: {transfer['destination_account']}\n"
                                f"Сума: {transfer['amount']} {transfer['currency']}"
                            )
                            break
                except Exception as e:
                    logger.error(f"Error in default recurring transfer callback: {repr(e)}")

            profile_reminder_tasks[default_recurring_task_id] = asyncio.create_task(
                runtime.recurring_transfers.run_forever(
                    firefly_client=runtime.firefly,
                    on_transfer_executed=default_recurring_callback,
                    poll_seconds=60,
                )
            )


async def ensure_profile_reminder_loop(profile_id: str) -> None:
    if profile_id in profile_reminder_tasks:
        return

    runtime = get_profile_runtime(profile_id)
    await ensure_runtime_bootstrapped(runtime)
    profile_reminder_tasks[profile_id] = asyncio.create_task(
        runtime.reminder_service.run_forever(send_telegram_message)
    )

    # НОВЕ: Запустити цикл регулярних переказів
    profile_recurring_task_id = f"{profile_id}_recurring"
    if profile_recurring_task_id not in profile_reminder_tasks:

        async def recurring_transfer_callback(transfer: dict) -> None:
            """Callback для уведомлення про виконаний регулярний переказ."""
            try:
                # Знайти хоча б одного користувача для цього профіля
                for chat_id, bound_profile in bound_profiles.items():
                    if bound_profile == profile_id:
                        await send_telegram_message(
                            chat_id,
                            f"✅ Регулярний переказ виконаний!\n\n"
                            f"Від: {transfer['source_account']}\n"
                            f"На: {transfer['destination_account']}\n"
                            f"Сума: {transfer['amount']} {transfer['currency']}"
                        )
                        break
            except Exception as e:
                logger.error(f"Error in recurring transfer callback: {repr(e)}")

        profile_reminder_tasks[profile_recurring_task_id] = asyncio.create_task(
            runtime.recurring_transfers.run_forever(
                firefly_client=runtime.firefly,
                on_transfer_executed=recurring_transfer_callback,
                poll_seconds=60,
            )
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
                        text=f"Не зміг прив'язати профіль: {str(e)}",
                    )
            
            # НОВЕ: Обробка вибору рахунку
            if data.startswith("select_account:"):
                account_id = int(data.split(":", 1)[1])
                account_name = callback_query.get("message", {}).get("text", "").split('\n')[0]
                
                await user_preferences.set_preferred_account(chat_id, account_id)
                await user_preferences.set_preferred_account_name(chat_id, account_name)
                
                await edit_telegram_message(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"✅ Обраний рахунок: {account_name}\n\nТепер всі нові транзакції буде записано на цей рахунок.",
                )
                await answer_callback_query(callback_id, f"Рахунок '{account_name}' обраний")

            return {"ok": True}

    message = update.get("message") or update.get("edited_message")
    if not message:
        return {"ok": True}

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text")
    photo = message.get("photo")
    voice = message.get("voice")  # НОВЕ: розпізнавання голосу

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
        # ФАЗА 4: Перевірити чи користувач у режимі review
        review_state = review_manager.get_state(chat_id)
        if review_state:
            # Користувач у режимі виправлення позицій
            await _handle_review_mode(chat_id, text, runtime)
            return {"ok": True}

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
                # НОВЕ: Використовувати обраний рахунок якщо є
                source_account = await _get_user_account(chat_id, runtime)
                result = await runtime.firefly.create_receipt_transactions(
                    receipt=receipt,
                    default_source_account=source_account,
                    default_currency=runtime.default_currency,
                )
                await pending_store.clear(chat_id)
                await send_telegram_message(
                    chat_id,
                    format_receipt_commit_result(receipt, result, runtime.default_currency),
                )
                return {"ok": True}

            if is_receipt_cancel_text(text):
                # НОВЕ: Зберегти скасований чек для можливості undo
                last_cancelled[chat_id] = pending.copy()
                await pending_store.clear(chat_id)
                await send_telegram_message(
                    chat_id,
                    "Окей, чек скасовано. Нічого не записував.\n\n💡 Якщо передумав - напиши «поверни назад»."
                )
                return {"ok": True}

            await send_telegram_message(chat_id, "Спочатку підтвердь або скасуй чек.")
            return {"ok": True}

        if photo:
            largest_photo = photo[-1]
            file_id = largest_photo["file_id"]
            image_bytes, media_type = await get_telegram_file_bytes(file_id)

            # ФАЗА 7: Спочатку пробуємо парсити як СПИСОК
            # (списки часто чіткіші ніж касові чеки)
            # parsed_list = await list_parser.parse_list_image_async(image_bytes, media_type)
            # TODO: Додати Vision detection для списків
            # На дан момент фокусуємось на касових чеках

            # Парсити як касовий чек
            parsed_receipt = await runtime.receipt_parser.parse_receipt_image(image_bytes, media_type)

            # НОВЕ: Покращити категоризацію позицій
            # DISABLED: Claude API баланс закончился, OpenAI достаточно для парсинга
            # logger.debug(f"Enhancing receipt categories for {parsed_receipt['merchant']}")
            # enhanced_items = await runtime.receipt_enhancer.enhance_receipt_categories(
            #     items=parsed_receipt["items"],
            #     merchant=parsed_receipt["merchant"]
            # )
            # parsed_receipt["items"] = enhanced_items

            # ФАЗА 1: Логування обробки чека
            pipeline_logger = ReceiptPipelineLogger(chat_id, debug_mode=False)
            pipeline_logger.log_ocr_raw_output({
                "merchant": parsed_receipt.get("merchant"),
                "items": parsed_receipt.get("items", []),
                "receipt_total": parsed_receipt.get("receipt_total"),
            })

            # Зберегти у pending
            await pending_store.set(chat_id, "receipt_confirm", parsed_receipt)

            # ФАЗА 1: використовувати детальний формат чека з confidence
            receipt_message = format_receipt_detailed(
                parsed_receipt,
                show_categories=True,
                show_confidence=False  # На production = False
            )
            await send_telegram_message(chat_id, receipt_message)
            return {"ok": True}

        # НОВЕ: Меню обраних рахунків
        if text and text.lower().strip() in ["/accounts", "/account", "рахунок", "счет", "account"]:
            await _show_accounts_menu(chat_id, runtime)
            return {"ok": True}

        # ФАЗА 4: Режим review (виправлення сумнівних позицій)
        if text and text.lower().strip() in ["виправити сумнівні", "исправить сомнительные", "fix suspicious"]:
            pending = await pending_store.get(chat_id)
            if pending and pending.get("kind") == "receipt_confirm":
                receipt = pending["payload"]

                # Запустити режим review
                state = review_manager.start_review(chat_id, receipt)

                if state is None:
                    # Немає сумнівних позицій
                    await send_telegram_message(chat_id, "Всі позиції виглядають правильно. Немає сумнівних для виправлення.")
                    return {"ok": True}

                # Показати першу сумнівну позицію
                await _show_receipt_item_review(chat_id, state)
                return {"ok": True}

            await send_telegram_message(chat_id, "Спочатку надішли фото чека.")
            return {"ok": True}

        # НОВЕ: Обробка голосових повідомлень
        if voice:
            file_id = voice.get("file_id")
            if file_id:
                logger.debug(f"Processing voice message from {chat_id}")

                try:
                    # Завантажити аудіофайл
                    audio_bytes, _ = await get_telegram_file_bytes(file_id)

                    # Розпізнати голос
                    transcribed_text = await runtime.speech_to_text.transcribe_audio(
                        audio_bytes,
                        audio_filename="voice.ogg",
                        language="uk"
                    )

                    if transcribed_text:
                        logger.debug(f"✅ Voice transcribed: {transcribed_text[:100]}...")
                        # Обробити розпізнаний текст як звичайне повідомлення
                        text = transcribed_text
                        # Продовжити з обробкою як звичайного тексту (див. нижче)
                    else:
                        await send_telegram_message(
                            chat_id,
                            "❌ Не зміг розпізнати голос. Спробуй ще раз або напиши текстом."
                        )
                        logger.warning(f"Voice transcription failed for {chat_id}")
                        return {"ok": True}

                except Exception as e:
                    logger.error(f"❌ Voice processing error: {repr(e)}")
                    await send_telegram_message(
                        chat_id,
                        f"❌ Помилка при обробці голосу: {str(e)[:100]}"
                    )
                    return {"ok": True}

        if not text:
            await send_telegram_message(chat_id, "Поки що я обробляю текстові повідомлення, фото чеків та голосові повідомлення.")
            return {"ok": True}

        # Rate limiting
        if not claude_limiter.check_and_wait(chat_id):
            await send_telegram_message(
                chat_id,
                "⚠️ Занадто багато запитів за мінуту. Зачекайте трохи перед наступною командою."
            )
            logger.warning(f"Rate limit triggered for chat_id {chat_id}")
            return {"ok": True}

        # НОВЕ: Обробка команди відновлення останньої скасованої операції (undo)
        if is_undo_text(text):
            # Спочатку перевіряємо видалені транзакції
            if chat_id in last_deleted_transaction:
                deleted_group = last_deleted_transaction.pop(chat_id)

                # Відновити видалену транзакцію через Firefly API
                try:
                    attrs = deleted_group.get("attributes", {})
                    group_title = attrs.get("group_title") or "Відновлена транзакція"
                    transactions = attrs.get("transactions", [])

                    if transactions:
                        # Підготувати splits для відновлення
                        splits = []
                        for tx in transactions:
                            splits.append({
                                "type": tx.get("type"),
                                "date": tx.get("date"),
                                "amount": tx.get("amount"),
                                "description": tx.get("description"),
                                "source_name": tx.get("source_name"),
                                "destination_name": tx.get("destination_name"),
                                "currency_code": tx.get("currency_code"),
                                "category_name": tx.get("category_name"),
                            })

                        # Створити транзакцію заново
                        await runtime.firefly._recreate_group(group_title, splits)

                        await send_telegram_message(
                            chat_id,
                            f"✅ Відновив видалену транзакцію:\n{group_title} - {transactions[0].get('amount')} {transactions[0].get('currency_code')}\n\n💡 Транзакція знову в Firefly."
                        )
                    else:
                        await send_telegram_message(chat_id, "❌ Не вдалось відновити транзакцію (немає даних).")
                except Exception as e:
                    logger.error(f"Failed to restore deleted transaction: {e}")
                    await send_telegram_message(chat_id, f"❌ Помилка при відновленні транзакції: {str(e)}")

                return {"ok": True}

            # Потім перевіряємо скасовані чеки
            if chat_id in last_cancelled:
                # Відновити скасовану операцію назад у pending
                cancelled_data = last_cancelled.pop(chat_id)
                await pending_store.set(chat_id, cancelled_data["kind"], cancelled_data["payload"])

                if cancelled_data["kind"] == "receipt_confirm":
                    receipt = cancelled_data["payload"]
                    receipt_message = format_receipt_detailed(receipt, show_categories=True)
                    await send_telegram_message(
                        chat_id,
                        f"✅ Відновив чек!\n\n{receipt_message}"
                    )
                else:
                    await send_telegram_message(
                        chat_id,
                        f"✅ Відновив скасовану операцію ({cancelled_data['kind']})."
                    )
                return {"ok": True}

            # Якщо нічого немає
            await send_telegram_message(
                chat_id,
                "❌ Немає видалених транзакцій або скасованих операцій для відновлення."
            )
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

        # НОВЕ: Перевірити чи це регулярний переказ
        if any(word in text.lower() for word in ["регулярно", "щодня", "щотижня", "щомісячно", "кожен день", "кожен тиждень", "кожен місяць", "daily", "weekly", "monthly", "назавжди"]):
            account_names = await runtime.firefly.list_asset_account_names()
            parsed_transfer = await runtime.claude.parse_transfer_text(text, account_names)
            frequency, time_of_day = parse_frequency_and_time(text)

            if parsed_transfer.get("amount") and frequency and time_of_day:
                # Створити регулярний переказ
                transfer_id = f"{parsed_transfer['source_account']}_{parsed_transfer['destination_account']}_{frequency}_{time_of_day}"
                try:
                    config = runtime.recurring_transfers.create(
                        transfer_id=transfer_id,
                        source_account=parsed_transfer["source_account"],
                        destination_account=parsed_transfer["destination_account"],
                        amount=parsed_transfer["amount"],
                        currency=parsed_transfer["currency"],
                        frequency=frequency,
                        time_of_day=time_of_day,
                        description=parsed_transfer.get("description", "Регулярний переказ"),
                    )

                    await send_telegram_message(
                        chat_id,
                        f"✅ Створено регулярний переказ!\n\n"
                        f"Від: {config['source_account']}\n"
                        f"На: {config['destination_account']}\n"
                        f"Сума: {config['amount']} {config['currency']}\n"
                        f"Частота: {config['frequency']}\n"
                        f"Час: {config['time_of_day']}\n"
                        f"Статус: Активно ✅"
                    )
                    return {"ok": True}
                except Exception as e:
                    logger.error(f"Error creating recurring transfer: {repr(e)}")
                    await send_telegram_message(chat_id, f"❌ Помилка: {str(e)}")
                    return {"ok": True}
            else:
                if not parsed_transfer.get("amount"):
                    await send_telegram_message(chat_id, "❌ Не вказав суму переказу!")
                if not frequency:
                    await send_telegram_message(chat_id, "❌ Не розпізнав частоту (щодня/щотижня/щомісячно)")
                if not time_of_day:
                    await send_telegram_message(chat_id, "❌ Не розпізнав час (наприклад: о 8 ранку)")
                return {"ok": True}

        if await runtime.claude.looks_like_transfer_request(text):
            account_names = await runtime.firefly.list_asset_account_names()
            parsed_transfer = await runtime.claude.parse_transfer_text(text, account_names)

            # НОВЕ: Перевірити чи вказана сума
            if not parsed_transfer.get("amount") or parsed_transfer["amount"] == 0:
                await send_telegram_message(
                    chat_id,
                    f"❌ Не вказав суму переказу!\n\n"
                    f"Переказ: {parsed_transfer.get('source_account')} → {parsed_transfer.get('destination_account')}\n\n"
                    f"Спробуй ще раз з сумою, наприклад:\n"
                    f"\"переказ 500 з {parsed_transfer.get('source_account')} на {parsed_transfer.get('destination_account')}\""
                )
                logger.warning(f"Transfer without amount for {chat_id}: {parsed_transfer}")
                return {"ok": True}

            await runtime.firefly.create_transfer(parsed_transfer)
            await send_telegram_message(chat_id, format_transfer_result(parsed_transfer))
            return {"ok": True}

        if await runtime.claude.looks_like_last_transaction_action_request(text):
            account_names = await runtime.firefly.list_asset_account_names()
            action_spec = await runtime.claude.parse_last_transaction_action_text(text, account_names)

            if action_spec.get("category"):
                action_spec["category"] = canonicalize_category(runtime, action_spec["category"])

            # НОВЕ: Використовувати обраний рахунок якщо є
            source_account = await _get_user_account(chat_id, runtime)
            result = await runtime.firefly.apply_last_transaction_action(
                action_spec=action_spec,
                default_currency=runtime.default_currency,
                default_source_account=source_account,
            )

            # НОВЕ: Зберегти видалену транзакцію для можливості undo
            if result.get("action") == "deleted" and "deleted_transaction" in result:
                last_deleted_transaction[chat_id] = result["deleted_transaction"]

            await send_telegram_message(
                chat_id,
                format_last_transaction_action_result(result, runtime.default_currency),
            )
            return {"ok": True}

        # НОВЕ: Команди для управління регулярними перекасами
        if text.lower() in {"мої регулярні", "список регулярних", "/recurring", "регулярні перекази"}:
            active = runtime.recurring_transfers.list_active()
            if not active:
                await send_telegram_message(chat_id, "Немає активних регулярних переказів. Напиши наприклад:\n\"переказ 500 щодня о 8 ранку з готівки на приватбанк\"")
                return {"ok": True}

            lines = ["📋 Активні регулярні перекази:\n"]
            for i, t in enumerate(active, 1):
                lines.append(
                    f"{i}. {t['source_account']} → {t['destination_account']}\n"
                    f"   Сума: {t['amount']} {t['currency']}\n"
                    f"   Частота: {t['frequency']} о {t['time_of_day']}\n"
                )

            await send_telegram_message(chat_id, "".join(lines))
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
        
        # НОВЕ: Якщо користувач обрав рахунок, використати його
        user_account = await _get_user_account(chat_id, runtime)
        if user_account and user_account != runtime.default_source_account:
            parsed["source_account"] = user_account

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


# ФАЗА 4: Допоміжні функції для режиму review

async def _handle_review_mode(chat_id: int, text: str, runtime):
    """Обробка дій користувача у режимі review."""
    state = review_manager.get_state(chat_id)
    if not state or not text:
        return

    text_lower = text.lower().strip()

    # Дія 1: Прийняти поточну позицію
    if text_lower in ["✅", "прийняти", "accept", "ok", "да", "так"]:
        # Позиція не була виправлена, просто перейти до наступної
        if state.next_suspect():
            # Показати наступну сумнівну позицію
            await _show_receipt_item_review(chat_id, state)
        else:
            # Закінчити review
            await _complete_receipt_review(chat_id, state, runtime)
        return

    # Дія 2: Виправити назву
    if text_lower in ["✏️", "виправити назву", "edit name", "edit"]:
        review_manager.set_mode(chat_id, "edit_name")
        await send_telegram_message(chat_id, format_receipt_name_input_prompt())
        return

    # Дія 3: Змінити категорію
    if text_lower in ["📁", "змінити категорію", "edit category", "category"]:
        review_manager.set_mode(chat_id, "edit_category")
        await send_telegram_message(chat_id, format_receipt_category_selector())
        return

    # Дія 4: Далі (без змін)
    if text_lower in ["⏭️", "далі", "next", "пропустити", "skip"]:
        if state.next_suspect():
            await _show_receipt_item_review(chat_id, state)
        else:
            await _complete_receipt_review(chat_id, state, runtime)
        return

    # Дія 5: Скасувати режим review
    if text_lower in ["скасувати", "cancel", "выход", "exit"]:
        review_manager.end_review(chat_id)
        await send_telegram_message(chat_id, "Режим виправлення скасовано.")
        return

    # Обробка вводу для edit_name або edit_category
    if state.mode == "edit_name":
        if text_lower == "скасувати":
            # Повернутися до меню
            review_manager.set_mode(chat_id, "confirm")
            await _show_receipt_item_review(chat_id, state)
            return

        # Зберегти виправлену назву
        new_name = text.strip()
        if len(new_name) > 2 and len(new_name) < 100:
            # Застосувати виправлення
            review_manager.apply_current_correction(chat_id, new_name=new_name)

            # Зберегти в пам'ять
            item = state.current_item()
            if item:
                await runtime.receipt_parser.save_item_confirmation(
                    merchant=state.receipt_data.get("merchant", "Unknown"),
                    raw_name=item.get("raw_name", ""),
                    confirmed_name=new_name,
                    confirmed_category=item.get("category", "Інше"),
                    barcode=item.get("barcode"),
                )

            # Показати підтвердження
            item = state.current_item()
            await send_telegram_message(
                chat_id,
                format_correction_saved(
                    item.get("raw_name", ""),
                    new_name,
                    item.get("category", ""),
                ),
            )

            # Перейти до наступної сумнівної позиції
            review_manager.set_mode(chat_id, "confirm")
            if state.next_suspect():
                await _show_receipt_item_review(chat_id, state)
            else:
                await _complete_receipt_review(chat_id, state, runtime)
        else:
            await send_telegram_message(chat_id, "❌ Назва занадто коротка або довга. Спробуй ще раз.")
        return

    if state.mode == "edit_category":
        # Спроба парсити номер категорії
        try:
            cat_number = int(text_lower.split()[0])
            categories = [
                "Продукти", "Овочі та фрукти", "Вода", "Солодкі напої", "Алкоголь",
                "Фастфуд і снеки", "Кафе та ресторани", "Цигарки", "Пальне",
                "Аптека", "Гігієна та догляд", "Побутова хімія", "Товари для дому",
                "Тварини", "Інше",
            ]

            if 1 <= cat_number <= len(categories):
                new_category = categories[cat_number - 1]

                # Застосувати виправлення
                review_manager.apply_current_correction(chat_id, new_category=new_category)

                # Зберегти в пам'ять
                item = state.current_item()
                if item:
                    await runtime.receipt_parser.save_item_confirmation(
                        merchant=state.receipt_data.get("merchant", "Unknown"),
                        raw_name=item.get("raw_name", ""),
                        confirmed_name=item.get("normalized_name", item.get("name", "")),
                        confirmed_category=new_category,
                        barcode=item.get("barcode"),
                    )

                # Показати підтвердження
                item = state.current_item()
                await send_telegram_message(
                    chat_id,
                    format_correction_saved(
                        item.get("raw_name", ""),
                        item.get("normalized_name", item.get("name", "")),
                        new_category,
                    ),
                )

                # Перейти до наступної
                review_manager.set_mode(chat_id, "confirm")
                if state.next_suspect():
                    await _show_receipt_item_review(chat_id, state)
                else:
                    await _complete_receipt_review(chat_id, state, runtime)
            else:
                await send_telegram_message(chat_id, f"❌ Виберіть номер від 1 до {len(categories)}.")
        except (ValueError, IndexError):
            await send_telegram_message(chat_id, "❌ Введи номер категорії (наприклад, '1' або '5').")
        return

    # Якщо ничого не підійшло
    await send_telegram_message(chat_id, format_receipt_review_menu())


async def _show_receipt_item_review(chat_id: int, state):
    """Показати одну позицію для review."""
    item = state.current_item()
    if not item:
        await send_telegram_message(chat_id, "Помилка: не знайду позицію для review.")
        return

    msg = format_receipt_item_review(
        item=item,
        item_index=state.current_item_index(),
        total_items=len(state.receipt_data.get('items', [])),
        total_suspect_items=state.total_suspects(),
        current_suspect_number=state.current_suspect_number(),
    )

    msg += "\n\n" + format_receipt_review_menu()

    await send_telegram_message(chat_id, msg)


async def _complete_receipt_review(chat_id: int, state, runtime):
    """Завершити режим review і записати чек у Firefly."""
    # Отримати оновлений чек з виправленнями
    corrected_receipt = review_manager.end_review(chat_id)

    if not corrected_receipt:
        await send_telegram_message(chat_id, "❌ Помилка при завершенні review.")
        return

    # Отримати статистику виправлень
    corrections = state.get_corrections_list()

    # Записати у Firefly
    try:
        # НОВЕ: Використовувати обраний рахунок якщо є
        source_account = await _get_user_account(chat_id, runtime)
        result = await runtime.firefly.create_receipt_transactions(
            receipt=corrected_receipt,
            default_source_account=source_account,
            default_currency=runtime.default_currency,
        )

        # Очистити pending
        await pending_store.clear(chat_id)

        # Показати результат
        reply = format_review_complete(len(corrections))

        if corrections:
            reply += "\n\nВиправлення що були збережені в пам'ять:"
            for correction in corrections:
                reply += f"\n• {correction['raw_name']} → {correction['new_name']}"

        await send_telegram_message(chat_id, reply)

        logger.info(f"Receipt review completed for {chat_id}: {len(corrections)} corrections saved")

    except Exception as e:
        logger.error(f"Error completing receipt review: {str(e)}")
        await send_telegram_message(chat_id, f"❌ Помилка при записі: {str(e)}")


# ═════════════════════════════════════════════════════════════════
# НОВЕ: МЕНЮ ОБРАНИХ РАХУНКІВ
# ═════════════════════════════════════════════════════════════════

async def _get_user_account(chat_id: int, runtime) -> str:
    """
    Отримати обраний рахунок користувача або повернутися до дефолтного.
    """
    try:
        preferred_id = await user_preferences.get_preferred_account(chat_id)
        
        if preferred_id:
            # Перевірити що рахунок ще існує
            accounts = await runtime.firefly.list_asset_accounts()
            for account in accounts:
                if account.get("id") == preferred_id:
                    return str(preferred_id)
            
            # Якщо рахунок видален, очистити preference
            await user_preferences.set_preferred_account(chat_id, None)
    
    except Exception as e:
        logger.warning(f"Error getting user account preference: {str(e)}")
    
    # Повернутися до дефолтного
    return runtime.default_source_account


async def _show_accounts_menu(chat_id: int, runtime) -> None:
    """Показати меню для вибору рахунку."""
    try:
        # Отримати список рахунків
        accounts = await runtime.firefly.list_asset_accounts()
        
        if not accounts:
            await send_telegram_message(chat_id, "❌ Немає доступних рахунків.")
            return
        
        # Отримати поточно обраний рахунок
        current_account_id = await user_preferences.get_preferred_account(chat_id)
        current_account_name = await user_preferences.get_preferred_account_name(chat_id)
        
        # Побудувати меню
        text = "💳 **Вибери основний рахунок:**\n\n"
        
        if current_account_name:
            text += f"Поточно обраний: *{current_account_name}*\n\n"
        
        buttons = []
        for account in accounts:
            account_id = account.get("id")
            account_name = account.get("name", "Unknown")
            
            # Позначити обраний рахунок
            prefix = "✅ " if account_id == current_account_id else "  "
            
            buttons.append({
                "text": f"{prefix}{account_name}",
                "callback_data": f"select_account:{account_id}"
            })
        
        #송크Группировать кнопки по 2 в ряду
        inline_keyboard = []
        for i in range(0, len(buttons), 2):
            row = buttons[i:i+2]
            inline_keyboard.append(row)
        
        await send_telegram_message(
            chat_id,
            text,
            reply_markup={"inline_keyboard": inline_keyboard}
        )
    
    except Exception as e:
        logger.error(f"Error showing accounts menu: {str(e)}")
        await send_telegram_message(chat_id, f"❌ Помилка при завантаженні рахунків: {str(e)}")

