import json
import os
from dataclasses import dataclass
from threading import Lock
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request

from app.services.advisor import AdvisorService
from app.services.claude_parser import ClaudeParser
from app.services.firefly_client import FireflyClient
from app.services.reports import ReportService

load_dotenv()

app = FastAPI()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()

CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "").strip()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001").strip()

FIREFLY_BASE_URL_DEFAULT = os.getenv("FIREFLY_BASE_URL", "http://firefly:8080").rstrip("/")
FIREFLY_ACCESS_TOKEN_DEFAULT = os.getenv("FIREFLY_ACCESS_TOKEN", "").strip()

DEFAULT_SOURCE_ACCOUNT = os.getenv("DEFAULT_SOURCE_ACCOUNT", "Готівка").strip()
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "UAH").strip()

ALLOWED_CHAT_IDS_RAW = os.getenv("ALLOWED_CHAT_IDS", "").strip()
PROFILES_FILE = os.getenv("PROFILES_FILE", "/app/data/bot/profiles.json").strip()

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

_profiles_lock = Lock()
_runtime_cache: dict[str, "ProfileRuntime"] = {}


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


def require_env(name: str, value: str) -> None:
    if not value:
        raise RuntimeError(f"{name} is not set")


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


def ensure_profiles_dir() -> None:
    folder = os.path.dirname(PROFILES_FILE)
    if folder:
        os.makedirs(folder, exist_ok=True)


def load_profiles_data() -> dict[str, Any]:
    ensure_profiles_dir()

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
    ensure_profiles_dir()

    with _profiles_lock:
        with open(PROFILES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


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


def get_profile_runtime(profile_id: str) -> ProfileRuntime:
    cached = _runtime_cache.get(profile_id)
    if cached:
        return cached

    profile = get_profile(profile_id)
    if not profile:
        raise ValueError(f"Профіль не знайдено: {profile_id}")

    firefly_base_url = str(profile.get("firefly_base_url") or FIREFLY_BASE_URL_DEFAULT).rstrip("/")
    firefly_access_token = str(profile.get("firefly_access_token") or FIREFLY_ACCESS_TOKEN_DEFAULT).strip()
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

    runtime = ProfileRuntime(
        profile_id=profile_id,
        title=str(profile.get("title") or profile_id),
        default_currency=default_currency,
        default_source_account=default_source_account,
        firefly=firefly,
        claude=claude,
        reports=reports,
        advisor=advisor,
    )

    _runtime_cache[profile_id] = runtime
    return runtime


require_env("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
require_env("TELEGRAM_WEBHOOK_SECRET", TELEGRAM_WEBHOOK_SECRET)
require_env("CLAUDE_API_KEY", CLAUDE_API_KEY)

ALLOWED_CHAT_IDS = parse_allowed_chat_ids(ALLOWED_CHAT_IDS_RAW)


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


async def send_profile_picker(chat_id: int) -> None:
    await send_telegram_message(
        chat_id,
        format_start_text(chat_id),
        reply_markup=build_profile_keyboard(chat_id),
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


@app.get("/health")
async def health() -> dict:
    data = load_profiles_data()
    return {
        "ok": True,
        "whitelist_enabled": bool(ALLOWED_CHAT_IDS),
        "allowed_chat_ids_count": len(ALLOWED_CHAT_IDS),
        "profiles_count": len(data.get("profiles", [])),
        "profile_bindings_count": len(data.get("chat_bindings", {})),
    }


@app.post("/telegram/{secret}")
async def telegram_webhook(secret: str, request: Request) -> dict:
    if secret != TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    update = await request.json()

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

        if not is_chat_allowed(chat_id):
            await edit_telegram_message(
                chat_id,
                message_id,
                (
                    "Доступ заборонено.\n"
                    f"Твій chat_id: {chat_id}\n"
                    "Додай його в ALLOWED_CHAT_IDS у .env, якщо це довірений чат."
                ),
            )
            return {"ok": True}

        if data.startswith("bind_profile:"):
            profile_id = data.split(":", 1)[1]

            try:
                bind_chat_to_profile(chat_id, profile_id)
                selected = get_profile(profile_id)
                title = selected.get("title", profile_id) if selected else profile_id

                await edit_telegram_message(
                    chat_id,
                    message_id,
                    (
                        f"Профіль обрано: {title}\n"
                        f"Твій chat_id: {chat_id}\n"
                        "Тепер можеш вести облік у цьому профілі."
                    ),
                )
            except Exception as e:
                await edit_telegram_message(
                    chat_id,
                    message_id,
                    f"Не зміг прив’язати профіль: {str(e)}",
                )

        return {"ok": True}

    message = update.get("message") or update.get("edited_message")
    if not message:
        return {"ok": True}

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text")

    if not chat_id:
        return {"ok": True}

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

    if text and text.strip().lower() in {"/start", "/profile", "змінити профіль"}:
        await send_profile_picker(chat_id)
        return {"ok": True}

    bound_profile_id = get_bound_profile_id(chat_id)
    if not bound_profile_id:
        await send_profile_picker(chat_id)
        return {"ok": True}

    runtime = get_profile_runtime(bound_profile_id)

    if not text:
        await send_telegram_message(
            chat_id,
            "Поки що я обробляю тільки текстові повідомлення."
        )
        return {"ok": True}

    try:
        if runtime.claude.looks_like_balance_setup_request(text):
            parsed_setup = await runtime.claude.parse_balance_setup_text(text)
            results = await runtime.firefly.setup_balances(parsed_setup["accounts"])
            await send_telegram_message(chat_id, format_balance_setup_result(results))
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

        parsed = await runtime.claude.parse_transaction_text(text)
        await runtime.firefly.create_transaction(parsed)

        await send_telegram_message(
            chat_id,
            (
                f"[{runtime.title}] "
                f"Записав: {parsed['type']} | {parsed['amount']} {parsed['currency']} | "
                f"{parsed['category']} | {parsed['description']} | рахунок: {parsed['source_account']}"
            ),
        )

    except Exception as e:
        print("ERROR =", repr(e))
        await send_telegram_message(chat_id, f"Не зміг обробити повідомлення: {str(e)}")

    return {"ok": True}
