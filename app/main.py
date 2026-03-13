import asyncio
import os
import re
from datetime import date
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
from app.services.profile_runtime import ProfileRuntime, ProfileRuntimeFactory
from app.services.profile_service import ProfileService
from app.services.receipt_parser import ReceiptParser
from app.services.reminder_service import ReminderService
from app.services.reports import ReportService

load_dotenv()

app = FastAPI()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()

CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "").strip()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001").strip()

FIREFLY_BASE_URL = os.getenv("FIREFLY_BASE_URL", "http://firefly:8080").rstrip("/")
FIREFLY_ACCESS_TOKEN = os.getenv("FIREFLY_ACCESS_TOKEN", "").strip()

DEFAULT_SOURCE_ACCOUNT = os.getenv("DEFAULT_SOURCE_ACCOUNT", "Готівка").strip()
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "UAH").strip().upper()

ALLOWED_CHAT_IDS_RAW = os.getenv("ALLOWED_CHAT_IDS", "").strip()
CATEGORY_RULES_FILE = os.getenv("CATEGORY_RULES_FILE", "/app/data/category_rules.json").strip()
REMINDER_DATA_FILE = os.getenv("REMINDER_DATA_FILE", "/app/data/reminders.json").strip()
BUDGET_DATA_FILE = os.getenv("BUDGET_DATA_FILE", "/app/data/budgets.json").strip()
BOT_TIMEZONE = os.getenv("BOT_TIMEZONE", "Europe/Kyiv").strip()
REMINDER_POLL_SECONDS = int(os.getenv("REMINDER_POLL_SECONDS", "30").strip())
SUBSCRIPTION_REMINDER_DAYS = max(0, int(os.getenv("SUBSCRIPTION_REMINDER_DAYS", "2").strip()))

PROFILES_FILE = os.getenv("PROFILES_FILE", "/app/data/bot/profiles.json").strip()
BOT_DATA_ROOT = os.getenv("BOT_DATA_ROOT", "/app/data/bot").strip()

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
TELEGRAM_FILE_API = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}"

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

notification_loop_task: Optional[asyncio.Task] = None
profile_notification_tasks: dict[str, asyncio.Task] = {}
subscription_alert_state: dict[tuple[str, str, str], str] = {}
_default_runtime: Optional[ProfileRuntime] = None

profile_service = ProfileService(PROFILES_FILE)
profile_runtime_factory = ProfileRuntimeFactory(
    profile_service=profile_service,
    claude_api_key=CLAUDE_API_KEY,
    claude_model=CLAUDE_MODEL,
    timezone_name=BOT_TIMEZONE,
    reminder_poll_seconds=REMINDER_POLL_SECONDS,
    data_root=BOT_DATA_ROOT,
)
pending_store = PendingStore()


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


ALLOWED_CHAT_IDS = parse_allowed_chat_ids(ALLOWED_CHAT_IDS_RAW)


def is_chat_allowed(chat_id: int) -> bool:
    if not ALLOWED_CHAT_IDS:
        return True
    return chat_id in ALLOWED_CHAT_IDS


def profiles_enabled() -> bool:
    try:
        return profile_service.count_profiles() > 0
    except Exception as e:
        print("PROFILES_LOAD_ERROR =", repr(e))
        return False


def get_default_runtime() -> ProfileRuntime:
    global _default_runtime
    if _default_runtime is not None:
        return _default_runtime

    firefly = FireflyClient(base_url=FIREFLY_BASE_URL, access_token=FIREFLY_ACCESS_TOKEN)
    claude = ClaudeParser(
        api_key=CLAUDE_API_KEY,
        model=CLAUDE_MODEL,
        default_currency=DEFAULT_CURRENCY,
        default_source_account=DEFAULT_SOURCE_ACCOUNT,
    )
    reports = ReportService(firefly=firefly, default_currency=DEFAULT_CURRENCY)
    advisor = AdvisorService(firefly=firefly, claude=claude, default_currency=DEFAULT_CURRENCY)
    category_rules = CategoryRulesService(file_path=CATEGORY_RULES_FILE)
    receipt_parser = ReceiptParser(
        api_key=CLAUDE_API_KEY,
        model=CLAUDE_MODEL,
        default_currency=DEFAULT_CURRENCY,
        category_rules=category_rules,
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
    _default_runtime = ProfileRuntime(
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
        reminder_service=reminder_service,
        budget_service=budget_service,
    )
    return _default_runtime


async def send_telegram_message(chat_id: int, text: str, reply_markup: Optional[dict] = None) -> None:
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(f"{TELEGRAM_API}/sendMessage", json=payload)
        response.raise_for_status()


async def edit_telegram_message(chat_id: int, message_id: int, text: str, reply_markup: Optional[dict] = None) -> None:
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(f"{TELEGRAM_API}/editMessageText", json=payload)
        response.raise_for_status()


async def answer_callback_query(callback_query_id: str, text: Optional[str] = None) -> None:
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(f"{TELEGRAM_API}/answerCallbackQuery", json=payload)
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
    media_type = detect_media_type(file_path)
    return download_response.content, media_type


def detect_media_type(file_path: str) -> str:
    lower = file_path.lower()
    for extension, media_type in IMAGE_MEDIA_TYPES.items():
        if lower.endswith(extension):
            return media_type
    return "image/jpeg"


def get_receipt_file_id_from_message(message: dict[str, Any]) -> Optional[str]:
    photo = message.get("photo") or []
    if photo:
        largest_photo = photo[-1]
        file_id = largest_photo.get("file_id")
        return str(file_id) if file_id else None
    document = message.get("document") or {}
    file_id = document.get("file_id")
    if not file_id:
        return None
    mime_type = str(document.get("mime_type") or "").lower()
    file_name = str(document.get("file_name") or "").lower()
    if mime_type.startswith("image/") or file_name.endswith(IMAGE_EXTENSIONS):
        return str(file_id)
    return None


def canonicalize_category(runtime: ProfileRuntime, text: str) -> str:
    return runtime.category_rules.resolve_category(text, fallback=text) or text


def format_balance_setup_result(results: list[dict[str, Any]]) -> str:
    lines = ["Оновив баланси:"]
    for item in results:
        action = item.get("action")
        account = item.get("account")
        currency = item.get("currency", "UAH")
        if action == "created_with_opening_balance":
            lines.append(f"• {account}: створив новий рахунок зі стартовим балансом {float(item.get('target_balance', 0) or 0):.2f} {currency}")
        elif action == "adjusted":
            lines.append(
                f"• {account}: було {float(item.get('current_balance', 0) or 0):.2f} {currency}, стало {float(item.get('target_balance', 0) or 0):.2f} {currency}, корекція {float(item.get('delta', 0) or 0):+.2f} {currency}"
            )
        elif action == "no_change":
            lines.append(f"• {account}: без змін, уже {float(item.get('current_balance', 0) or 0):.2f} {currency}")
        else:
            lines.append(f"• {account}: невідомий результат")
    return "\n".join(lines)


def format_transfer_result(parsed: dict[str, Any]) -> str:
    return (
        f"Записав переказ: {float(parsed['amount']):.2f} {parsed['currency']}\n"
        f"З: {parsed['source_account']}\n"
        f"На: {parsed['destination_account']}\n"
        f"Опис: {parsed['description']}"
    )


def format_subscription_result(parsed: dict[str, Any], result: dict[str, Any]) -> str:
    subscription_id = ((result.get("data") or {}).get("id"))
    lines = [
        "Підписку створив:",
        f"• Назва: {parsed['name']}",
        f"• Сума: {float(parsed['amount']):.2f} {parsed['currency']}",
        f"• Періодичність: {parsed['repeat_freq']}",
        f"• Перша дата: {parsed['date']}",
    ]
    skip = int(parsed.get("skip", 0) or 0)
    if skip > 0:
        lines.append(f"• Skip: {skip}")
    if subscription_id:
        lines.append(f"• ID у Firefly: {subscription_id}")
    return "\n".join(lines)


def format_subscription_short(item: dict[str, Any], index: Optional[int] = None) -> str:
    prefix = f"{index}. " if index is not None else ""
    status = "✅" if item.get("active", True) else "⏸"
    extra = f" | наступна дата: {item['next_date']}" if item.get("next_date") else ""
    return (
        f"{prefix}{status} {item.get('name', 'Без назви')} | {float(item.get('amount', 0) or 0):.2f} {item.get('currency', 'UAH')}"
        f" | {item.get('repeat_freq', 'monthly')}{extra}"
    )


def format_subscription_list(items: list[dict[str, Any]]) -> str:
    if not items:
        return "Підписок поки немає."
    lines = ["Твої підписки:"]
    for idx, item in enumerate(items, start=1):
        lines.append(format_subscription_short(item, idx))
    return "\n".join(lines)


def format_subscription_updated(before: dict[str, Any], after: dict[str, Any]) -> str:
    return "\n".join([
        "Оновив підписку:",
        f"• Було: {format_subscription_short(before)}",
        f"• Стало: {format_subscription_short(after)}",
    ])


def format_subscription_deleted(item: dict[str, Any]) -> str:
    return f"Видалив підписку:\n• {format_subscription_short(item)}"


def format_last_transaction_action_result(result: dict[str, Any], default_currency: str) -> str:
    action = result.get("action")
    currency = result.get("currency", default_currency)
    if action == "deleted":
        return (
            f"Видалив останню транзакцію:\n"
            f"• Тип: {result.get('old_type')}\n"
            f"• Сума: {float(result.get('old_amount', 0) or 0):.2f} {currency}\n"
            f"• Опис: {result.get('old_description')}"
        )
    if action == "deleted_split":
        return (
            f"Видалив частину останньої транзакції:\n"
            f"• Частина: {result.get('target_label')}\n"
            f"• Сума: {float(result.get('old_amount', 0) or 0):.2f} {currency}\n"
            f"• Опис: {result.get('old_description')}"
        )
    if action == "updated":
        lines = ["Оновив частину останньої транзакції:" if result.get("target_label") else "Оновив останню транзакцію:"]
        if result.get("target_label"):
            lines.append(f"• Частина: {result.get('target_label')}")
        lines.extend([
            f"• Було: {float(result.get('old_amount', 0) or 0):.2f} {currency} | {result.get('old_description')}",
            f"• Стало: {float(result.get('new_amount', 0) or 0):.2f} {currency} | {result.get('new_description')}",
        ])
        if result.get("old_source_account") != result.get("new_source_account"):
            lines.append(f"• Рахунок: {result.get('old_source_account')} → {result.get('new_source_account')}")
        if result.get("old_destination_account") != result.get("new_destination_account"):
            lines.append(f"• Призначення: {result.get('old_destination_account')} → {result.get('new_destination_account')}")
        if result.get("old_category") != result.get("new_category"):
            lines.append(f"• Категорія: {result.get('old_category')} → {result.get('new_category')}")
        return "\n".join(lines)
    return "Невідомий результат дії над останньою транзакцією."


def format_receipt_preview(receipt: dict[str, Any], default_currency: str) -> str:
    merchant = receipt.get("merchant") or "Чек"
    currency = receipt.get("currency") or default_currency
    total = float(receipt.get("receipt_total", 0) or 0)
    groups = receipt.get("category_totals", []) or []
    lines = [f"Розібрав чек: {merchant}", f"Загальна сума: {total:.2f} {currency}"]
    if groups:
        lines.append("Попередній розподіл:")
        for item in groups:
            lines.append(f"• {item['category']} — {float(item['amount']):.2f} {currency}")
    lines.append("")
    lines.append("Напиши: «підтвердити чек» або «скасувати чек».")
    return "\n".join(lines)


def format_receipt_commit_result(receipt: dict[str, Any], result: dict[str, Any], default_currency: str) -> str:
    merchant = receipt.get("merchant") or "Чек"
    currency = receipt.get("currency") or default_currency
    groups = result.get("groups", []) or []
    lines = [f"Чек записав: {merchant}"]
    for item in groups:
        lines.append(f"• {item['category']} — {float(item['amount']):.2f} {currency}")
    return "\n".join(lines)


def format_pending_transaction_preview(parsed: dict[str, Any]) -> str:
    return "\n".join([
        "Хочу підтвердити операцію перед записом:",
        f"• Тип: {parsed['type']}",
        f"• Сума: {float(parsed['amount']):.2f} {parsed['currency']}",
        f"• Категорія: {parsed['category']}",
        f"• Опис: {parsed['description']}",
        f"• Рахунок: {parsed['source_account']}",
        "",
        "Напиши «підтвердити» або «скасувати».",
    ])


def summarize_transaction_group(group: dict[str, Any], index: int) -> str:
    attrs = group.get("attributes", {})
    splits = attrs.get("transactions") or []
    if not splits:
        splits = [attrs]
    first = splits[0]
    tx_type = str(first.get("type") or "").lower()
    amount = abs(float(str(first.get("amount") or 0).replace(",", ".")))
    currency = first.get("currency_code") or "UAH"
    description = first.get("description") or attrs.get("group_title") or "Операція"
    if tx_type == "withdrawal":
        target = first.get("category_name") or first.get("destination_name") or "Без категорії"
        label = f"витрата → {target}"
    elif tx_type == "deposit":
        target = first.get("source_name") or first.get("category_name") or "Дохід"
        label = f"дохід ← {target}"
    elif tx_type == "transfer":
        label = f"переказ {first.get('source_name')} → {first.get('destination_name')}"
    else:
        label = tx_type or "операція"
    extra = f" (+{len(splits) - 1} част.)" if len(splits) > 1 else ""
    return f"{index}. {amount:.2f} {currency} | {label} | {description}{extra}"


def format_recent_transactions(groups: list[dict[str, Any]]) -> str:
    if not groups:
        return "Операцій поки немає."
    lines = ["Останні операції:"]
    for idx, group in enumerate(groups, start=1):
        lines.append(summarize_transaction_group(group, idx))
    return "\n".join(lines)


def is_receipt_confirm_text(text: str) -> bool:
    low = text.strip().lower()
    return low in {"підтвердити чек", "підтвердити", "ок", "окей", "записуй чек", "записуй", "так"}


def is_receipt_cancel_text(text: str) -> bool:
    low = text.strip().lower()
    return low in {"скасувати чек", "скасувати", "відміна", "ні", "не треба"}


def is_generic_confirm_text(text: str) -> bool:
    return text.strip().lower() in {"підтвердити", "так", "ок", "окей", "yes", "y"}


def is_generic_cancel_text(text: str) -> bool:
    return text.strip().lower() in {"скасувати", "ні", "no", "n", "відміна", "не треба"}


def looks_like_recent_transactions_request(text: str) -> bool:
    low = text.strip().lower()
    return ("останні" in low or "що я записав" in low or "покажи останні" in low) and any(
        word in low for word in ("операц", "транзак", "витрат", "доход", "переказ")
    )


def extract_recent_limit(text: str) -> int:
    match = re.search(r"(\d{1,2})", text)
    if not match:
        return 10
    return max(1, min(30, int(match.group(1))))


def looks_like_budget_status_request(text: str) -> bool:
    low = text.strip().lower()
    if "бюджет" not in low:
        return False
    return any(trigger in low for trigger in ("план", "факт", "вклада", "йду", "залиш", "порів", "справи"))


def extract_target_index(text: str) -> Optional[int]:
    match = re.search(r"(?:№|номер\s*)(\d{1,3})", text.lower())
    if match:
        return int(match.group(1))
    return None


def extract_quoted_text(text: str) -> Optional[str]:
    match = re.search(r'["“«](.+?)["”»]', text)
    if match:
        return match.group(1).strip()
    return None


def needs_transaction_confirmation(runtime: ProfileRuntime, text: str, parsed: dict[str, Any]) -> bool:
    low = text.strip().lower()
    category_low = str(parsed.get("category") or "").strip().lower()
    if category_low in {"інше", "без категорії", "other"}:
        return True
    if len(re.findall(r"\d+[\d\s.,]*\s*(?:грн|uah|₴)", low)) > 1:
        return True
    if any(word in low for word in (",", ";")) and len(re.findall(r"\d+[\d\s.,]*", low)) > 1:
        return True
    accountish = ["карта", "рахунок", "готів", "mono", "monobank", "приват"]
    if any(word in low for word in accountish):
        source = str(parsed.get("source_account") or "").lower()
        if source == runtime.default_source_account.lower() and runtime.default_source_account.lower() == "готівка":
            return True
    return False


async def process_due_subscription_alerts(runtime: ProfileRuntime) -> None:
    chats: list[int]
    if profiles_enabled():
        chats = profile_service.list_bound_chat_ids_for_profile(runtime.profile_id)
    else:
        chats = sorted(ALLOWED_CHAT_IDS)
    if not chats:
        return

    today_key = date.today().isoformat()
    due_items = await runtime.firefly.list_due_subscriptions(days_ahead=SUBSCRIPTION_REMINDER_DAYS)
    for item in due_items:
        alert_key = (runtime.profile_id, str(item.get("id")), str(item.get("next_date")))
        if subscription_alert_state.get(alert_key) == today_key:
            continue
        days_left = int(item.get("days_left", 0) or 0)
        when = "сьогодні" if days_left == 0 else f"через {days_left} дн."
        text = (
            f"Нагадування про підписку:\n"
            f"• {item.get('name')}\n"
            f"• Сума: {float(item.get('amount', 0) or 0):.2f} {item.get('currency', runtime.default_currency)}\n"
            f"• Дата: {item.get('next_date')} ({when})"
        )
        for chat_id in chats:
            await send_telegram_message(chat_id, text)
        subscription_alert_state[alert_key] = today_key


async def notification_loop(runtime: ProfileRuntime) -> None:
    while True:
        try:
            await runtime.reminder_service.process_due(send_telegram_message)
            await process_due_subscription_alerts(runtime)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print("NOTIFICATION_LOOP_ERROR =", runtime.profile_id, repr(e))
        await asyncio.sleep(REMINDER_POLL_SECONDS)


async def ensure_default_notification_loop() -> None:
    global notification_loop_task
    if notification_loop_task is not None:
        return
    notification_loop_task = asyncio.create_task(notification_loop(get_default_runtime()))


async def ensure_profile_notification_loop(profile_id: str) -> None:
    if profile_id in profile_notification_tasks:
        return
    runtime = profile_runtime_factory.get(profile_id)
    profile_notification_tasks[profile_id] = asyncio.create_task(notification_loop(runtime))


async def stop_all_notification_loops() -> None:
    global notification_loop_task
    if notification_loop_task is not None:
        notification_loop_task.cancel()
        try:
            await notification_loop_task
        except asyncio.CancelledError:
            pass
        notification_loop_task = None
    for profile_id, task in list(profile_notification_tasks.items()):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        profile_notification_tasks.pop(profile_id, None)


async def send_profile_picker(chat_id: int) -> None:
    await send_telegram_message(
        chat_id,
        profile_service.format_start_text(chat_id),
        reply_markup=profile_service.build_profile_keyboard(chat_id),
    )


async def handle_callback_query(callback_query: dict[str, Any]) -> dict[str, Any]:
    callback_id = callback_query.get("id")
    data = str(callback_query.get("data") or "")
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    message_id = message.get("message_id")
    if callback_id:
        await answer_callback_query(str(callback_id))
    if not chat_id or not message_id:
        return {"ok": True}
    if not data.startswith("bind_profile:"):
        return {"ok": True}
    profile_id = data.split(":", 1)[1]
    try:
        profile_service.bind_chat_to_profile(int(chat_id), profile_id)
        await ensure_profile_notification_loop(profile_id)
        profile = profile_service.get_profile(profile_id)
        title = profile.get("title", profile_id) if profile else profile_id
        await edit_telegram_message(
            chat_id=int(chat_id),
            message_id=int(message_id),
            text=(
                f"Профіль обрано: {title}\n"
                f"Твій chat_id: {chat_id}\n"
                "Тепер можеш вести облік у цьому профілі."
            ),
        )
    except Exception as e:
        await edit_telegram_message(chat_id=int(chat_id), message_id=int(message_id), text=f"Не зміг прив’язати профіль: {str(e)}")
    return {"ok": True}


async def resolve_runtime_for_chat(chat_id: int, text: Optional[str]) -> Optional[ProfileRuntime]:
    if profiles_enabled():
        if text and text.strip().lower() in {"/start", "/profile", "змінити профіль"}:
            await send_profile_picker(chat_id)
            return None
        allowed_profiles = profile_service.list_allowed_profiles_for_chat(chat_id)
        if not allowed_profiles:
            await send_telegram_message(chat_id, profile_service.format_start_text(chat_id))
            return None
        bound_profile_id = profile_service.get_bound_profile_id(chat_id)
        if not bound_profile_id:
            await send_profile_picker(chat_id)
            return None
        return profile_runtime_factory.get(bound_profile_id)

    if not is_chat_allowed(chat_id):
        await send_telegram_message(
            chat_id,
            (
                "Доступ заборонено.\n"
                f"Твій chat_id: {chat_id}\n"
                "Додай його в ALLOWED_CHAT_IDS у .env, якщо це довірений чат."
            ),
        )
        return None
    return get_default_runtime()


async def finalize_transaction_entry(chat_id: int, runtime: ProfileRuntime, text: str, parsed: dict[str, Any]) -> None:
    await runtime.firefly.create_transaction(parsed)
    reply_text = (
        f"Записав: {parsed['type']} | {parsed['amount']} {parsed['currency']} | "
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


async def handle_pending_action(chat_id: int, runtime: ProfileRuntime, text: Optional[str], receipt_file_id: Optional[str]) -> bool:
    pending = await pending_store.get(chat_id)
    if not pending:
        return False

    kind = pending.get("kind")
    payload = pending.get("payload") or {}

    if kind == "receipt_confirm":
        if receipt_file_id:
            await send_telegram_message(chat_id, "Спочатку підтвердь або скасуй попередній чек.")
            return True
        if not text:
            await send_telegram_message(chat_id, "Напиши «підтвердити чек» або «скасувати чек».")
            return True
        if is_receipt_confirm_text(text):
            result = await runtime.firefly.create_receipt_transactions(
                receipt=payload,
                default_source_account=runtime.default_source_account,
                default_currency=runtime.default_currency,
            )
            await pending_store.clear(chat_id)
            await send_telegram_message(chat_id, format_receipt_commit_result(payload, result, runtime.default_currency))
            return True
        if is_receipt_cancel_text(text):
            await pending_store.clear(chat_id)
            await send_telegram_message(chat_id, "Окей, чек скасовано. Нічого не записував.")
            return True
        await send_telegram_message(chat_id, "Спочатку підтвердь або скасуй чек.")
        return True

    if kind == "transaction_confirm":
        if receipt_file_id:
            await send_telegram_message(chat_id, "Спочатку підтвердь або скасуй попередню операцію.")
            return True
        if not text:
            await send_telegram_message(chat_id, "Напиши «підтвердити» або «скасувати».")
            return True
        if is_generic_confirm_text(text):
            await pending_store.clear(chat_id)
            await finalize_transaction_entry(chat_id, runtime, payload.get("original_text", ""), payload["parsed"])
            return True
        if is_generic_cancel_text(text):
            await pending_store.clear(chat_id)
            await send_telegram_message(chat_id, "Окей, цю операцію скасував. Нічого не записував.")
            return True
        await send_telegram_message(chat_id, "Спочатку підтвердь або скасуй попередню операцію.")
        return True

    return False


async def handle_receipt_upload(chat_id: int, runtime: ProfileRuntime, file_id: str) -> None:
    image_bytes, media_type = await get_telegram_file_bytes(file_id)
    parsed_receipt = await runtime.receipt_parser.parse_receipt_image(image_bytes, media_type)
    await pending_store.set(chat_id, "receipt_confirm", parsed_receipt)
    await send_telegram_message(chat_id, format_receipt_preview(parsed_receipt, runtime.default_currency))


async def handle_reminder_management(chat_id: int, runtime: ProfileRuntime, text: str) -> bool:
    if not runtime.claude.looks_like_reminder_manage_request(text):
        return False
    spec = await runtime.claude.parse_reminder_manage_text(text)
    action = spec["action"]
    if action == "list":
        await send_telegram_message(chat_id, runtime.reminder_service.format_list(runtime.reminder_service.list_reminders(chat_id)))
        return True
    if action == "delete":
        deleted = runtime.reminder_service.delete_reminder(chat_id, target_index=spec.get("target_index"), target_text=spec.get("target_text"))
        await send_telegram_message(chat_id, runtime.reminder_service.format_deleted_result(deleted))
        return True
    if action in {"disable", "enable", "update"}:
        updated = runtime.reminder_service.update_reminder(
            chat_id,
            target_index=spec.get("target_index"),
            target_text=spec.get("target_text"),
            new_text=spec.get("new_text") if action == "update" else None,
            hour=spec.get("hour") if action == "update" else None,
            minute=spec.get("minute") if action == "update" else None,
            enabled=(False if action == "disable" else True if action == "enable" else None),
        )
        await send_telegram_message(chat_id, runtime.reminder_service.format_updated_result(updated))
        return True
    return False


async def resolve_subscription_target(runtime: ProfileRuntime, spec: dict[str, Any]) -> dict:
    target_id = spec.get("target_id")
    target_name = spec.get("target_name")
    subscriptions = await runtime.firefly.list_subscriptions(active_only=False)
    if target_id:
        for item in subscriptions:
            if item["id"] == target_id:
                return item
        raise ValueError("Не знайшов підписку з таким ID")
    query = str(target_name or "").strip().lower()
    if not query:
        raise ValueError("Не визначив, яку саме підписку треба змінити")
    exact = [item for item in subscriptions if item["name"].lower() == query]
    if exact:
        return exact[0]
    partial = [item for item in subscriptions if query in item["name"].lower()]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        raise ValueError("Знайшов кілька схожих підписок. Уточни назву точніше.")
    raise ValueError("Не знайшов таку підписку")


async def handle_subscription_management(chat_id: int, runtime: ProfileRuntime, text: str) -> bool:
    if not runtime.claude.looks_like_subscription_manage_request(text):
        return False
    spec = await runtime.claude.parse_subscription_manage_text(text)
    action = spec["action"]
    if action == "list":
        items = await runtime.firefly.list_subscriptions(active_only=False)
        for item in items:
            item["next_date"] = runtime.firefly._calculate_next_subscription_date(item.get("date", ""), item.get("repeat_freq", "monthly"), item.get("skip", 0))
        await send_telegram_message(chat_id, format_subscription_list(items))
        return True

    target = await resolve_subscription_target(runtime, spec)
    if action == "delete":
        await runtime.firefly.delete_subscription(target["id"])
        await send_telegram_message(chat_id, format_subscription_deleted(target))
        return True

    updates: dict[str, Any] = {}
    if action == "disable":
        updates["active"] = False
    elif action == "enable":
        updates["active"] = True
    elif action == "update":
        for key in ("name", "amount", "currency", "repeat_freq", "date", "skip", "notes"):
            if spec.get(key) is not None:
                updates[key] = spec[key]
        if not updates:
            raise ValueError("Не побачив, що саме треба змінити в підписці")

    await runtime.firefly.update_subscription(target["id"], updates)
    fresh = await runtime.firefly.find_subscription(target["id"]) or {**target, **updates}
    fresh["next_date"] = runtime.firefly._calculate_next_subscription_date(fresh.get("date", ""), fresh.get("repeat_freq", "monthly"), fresh.get("skip", 0))
    await send_telegram_message(chat_id, format_subscription_updated(target, fresh))
    return True


async def handle_recent_transactions(chat_id: int, runtime: ProfileRuntime, text: str) -> bool:
    if not looks_like_recent_transactions_request(text):
        return False
    limit = extract_recent_limit(text)
    groups = await runtime.firefly.list_recent_transaction_groups(limit=limit)
    await send_telegram_message(chat_id, format_recent_transactions(groups[:limit]))
    return True


async def handle_budget_status(chat_id: int, runtime: ProfileRuntime, text: str) -> bool:
    if not looks_like_budget_status_request(text):
        return False
    payload = await runtime.budget_service.compare_budget_to_actual(
        chat_id,
        target_index=extract_target_index(text),
        target_title=extract_quoted_text(text),
    )
    await send_telegram_message(chat_id, runtime.budget_service.format_comparison(payload))
    return True


async def handle_text_message(chat_id: int, runtime: ProfileRuntime, text: str) -> None:
    if runtime.claude.looks_like_balance_setup_request(text):
        parsed_setup = await runtime.claude.parse_balance_setup_text(text)
        results = await runtime.firefly.setup_balances(parsed_setup["accounts"])
        await send_telegram_message(chat_id, format_balance_setup_result(results))
        return

    if runtime.claude.looks_like_category_create_request(text):
        parsed_category = await runtime.claude.parse_category_create_text(text)
        canonical_name = parsed_category["canonical_name"]
        await runtime.firefly.ensure_category(canonical_name)
        rule = runtime.category_rules.upsert_rule(canonical_name=canonical_name, aliases=parsed_category["aliases"])
        await send_telegram_message(chat_id, runtime.category_rules.format_rule_result(rule))
        return

    if await handle_reminder_management(chat_id, runtime, text):
        return

    if runtime.claude.looks_like_reminder_request(text):
        parsed_reminder = await runtime.claude.parse_reminder_create_text(text)
        reminder = runtime.reminder_service.create_daily_reminder(
            chat_id=chat_id,
            text=parsed_reminder["text"],
            hour=parsed_reminder["hour"],
            minute=parsed_reminder["minute"],
        )
        await send_telegram_message(chat_id, runtime.reminder_service.format_created_result(reminder))
        return

    if await handle_budget_status(chat_id, runtime, text):
        return

    if runtime.claude.looks_like_budget_create_request(text):
        parsed_budget = await runtime.claude.parse_budget_create_text(text)
        budget = await runtime.budget_service.create_budget_plan(chat_id=chat_id, amount=parsed_budget["amount"], title=parsed_budget["title"])
        await send_telegram_message(chat_id, runtime.budget_service.format_plan(budget))
        return

    if await handle_subscription_management(chat_id, runtime, text):
        return

    if runtime.claude.looks_like_subscription_create_request(text):
        parsed_subscription = await runtime.claude.parse_subscription_create_text(text)
        result = await runtime.firefly.create_subscription(parsed_subscription)
        await send_telegram_message(chat_id, format_subscription_result(parsed_subscription, result))
        return

    if await handle_recent_transactions(chat_id, runtime, text):
        return

    if runtime.claude.looks_like_transfer_request(text):
        account_names = await runtime.firefly.list_asset_account_names()
        parsed_transfer = await runtime.claude.parse_transfer_text(text, account_names)
        await runtime.firefly.create_transfer(parsed_transfer)
        await send_telegram_message(chat_id, format_transfer_result(parsed_transfer))
        return

    if runtime.claude.looks_like_last_transaction_action_request(text):
        account_names = await runtime.firefly.list_asset_account_names()
        action_spec = await runtime.claude.parse_last_transaction_action_text(text, account_names)
        if action_spec.get("category"):
            action_spec["category"] = canonicalize_category(runtime, action_spec["category"])
        result = await runtime.firefly.apply_last_transaction_action(
            action_spec=action_spec,
            default_currency=runtime.default_currency,
            default_source_account=runtime.default_source_account,
        )
        reply = format_last_transaction_action_result(result, runtime.default_currency)
        learned = None
        if result.get("action") == "updated" and result.get("old_category") != result.get("new_category"):
            learned = runtime.category_rules.learn_from_correction(
                new_category=result.get("new_category") or "Інше",
                old_category=result.get("old_category"),
                old_description=result.get("old_description"),
                old_destination=result.get("old_destination_account"),
            )
        if learned:
            reply += f"\n\nЗапам’ятав виправлення для категорії: {learned.get('canonical_name')}"
        await send_telegram_message(chat_id, reply)
        return

    intent = await runtime.claude.parse_intent_text(text)

    if intent == "smalltalk":
        await send_telegram_message(chat_id, await runtime.claude.answer_smalltalk(text))
        return

    if intent == "finance_query":
        report_reply = await runtime.reports.handle_report_request(text)
        if report_reply:
            await send_telegram_message(chat_id, report_reply)
            return
        await send_telegram_message(chat_id, await runtime.advisor.answer_question(text))
        return

    if intent == "finance_advice":
        await send_telegram_message(chat_id, await runtime.advisor.answer_question(text))
        return

    parsed = await runtime.claude.parse_transaction_text(text)
    parsed["category"] = canonicalize_category(runtime, f"{parsed['description']} {parsed['category']}")

    if needs_transaction_confirmation(runtime, text, parsed):
        await pending_store.set(chat_id, "transaction_confirm", {"parsed": parsed, "original_text": text})
        await send_telegram_message(chat_id, format_pending_transaction_preview(parsed))
        return

    await finalize_transaction_entry(chat_id, runtime, text, parsed)


@app.on_event("startup")
async def on_startup() -> None:
    validate_required_env()
    if profiles_enabled():
        for profile in profile_service.list_profiles():
            profile_id = str(profile.get("profile_id") or "").strip()
            if not profile_id:
                continue
            try:
                await ensure_profile_notification_loop(profile_id)
            except Exception as e:
                print("PROFILE_NOTIFICATION_START_ERROR =", profile_id, repr(e))
    else:
        await ensure_default_notification_loop()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await stop_all_notification_loops()


@app.get("/health")
async def health() -> dict[str, Any]:
    use_profiles = profiles_enabled()
    response: dict[str, Any] = {
        "ok": True,
        "whitelist_enabled": bool(ALLOWED_CHAT_IDS),
        "allowed_chat_ids_count": len(ALLOWED_CHAT_IDS),
        "profiles_enabled": use_profiles,
        "profiles_count": profile_service.count_profiles() if use_profiles else 0,
        "profile_bindings_count": profile_service.count_bindings() if use_profiles else 0,
        "category_rules_count": 0,
        "reminders_count": 0,
        "budgets_count": 0,
        "profile_errors": [],
    }
    if use_profiles:
        for profile in profile_service.list_profiles():
            profile_id = str(profile.get("profile_id") or "").strip()
            if not profile_id:
                continue
            try:
                runtime = profile_runtime_factory.get(profile_id)
                response["category_rules_count"] += len(runtime.category_rules.list_rules())
                response["reminders_count"] += runtime.reminder_service.count()
                response["budgets_count"] += runtime.budget_service.count()
            except Exception as e:
                response["profile_errors"].append({"profile_id": profile_id, "error": str(e)})
    else:
        runtime = get_default_runtime()
        response["category_rules_count"] = len(runtime.category_rules.list_rules())
        response["reminders_count"] = runtime.reminder_service.count()
        response["budgets_count"] = runtime.budget_service.count()
    return response


@app.post("/telegram/{secret}")
async def telegram_webhook(secret: str, request: Request) -> dict[str, Any]:
    if secret != TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    update = await request.json()
    if profiles_enabled():
        callback_query = update.get("callback_query")
        if callback_query:
            return await handle_callback_query(callback_query)

    message = update.get("message") or update.get("edited_message")
    if not message:
        return {"ok": True}

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return {"ok": True}

    chat_id = int(chat_id)
    text = message.get("text")
    receipt_file_id = get_receipt_file_id_from_message(message)

    runtime = await resolve_runtime_for_chat(chat_id, text)
    if runtime is None:
        unauthorized = False
        if profiles_enabled():
            unauthorized = not profile_service.list_allowed_profiles_for_chat(chat_id)
        else:
            unauthorized = not is_chat_allowed(chat_id)
        return {"ok": True, "unauthorized_chat_id": chat_id} if unauthorized else {"ok": True}

    try:
        if await handle_pending_action(chat_id, runtime, text, receipt_file_id):
            return {"ok": True}
        if receipt_file_id:
            await handle_receipt_upload(chat_id, runtime, receipt_file_id)
            return {"ok": True}
        if not text:
            await send_telegram_message(chat_id, "Поки що я обробляю текстові повідомлення і фото чеків.")
            return {"ok": True}
        await handle_text_message(chat_id, runtime, text)
    except Exception as e:
        print("ERROR =", repr(e))
        await send_telegram_message(chat_id, f"Не зміг обробити повідомлення: {str(e)}")

    return {"ok": True}
