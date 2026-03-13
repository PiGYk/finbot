import os

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request

from app.services.claude_parser import ClaudeParser
from app.services.firefly_client import FireflyClient
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
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "UAH").strip()

ALLOWED_CHAT_IDS_RAW = os.getenv("ALLOWED_CHAT_IDS", "").strip()

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


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
    # Якщо whitelist порожній, бот відкритий.
    # Як тільки додаси хоча б один chat_id у .env, почне працювати обмеження.
    if not ALLOWED_CHAT_IDS:
        return True
    return chat_id in ALLOWED_CHAT_IDS


require_env("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
require_env("TELEGRAM_WEBHOOK_SECRET", TELEGRAM_WEBHOOK_SECRET)
require_env("CLAUDE_API_KEY", CLAUDE_API_KEY)
require_env("FIREFLY_ACCESS_TOKEN", FIREFLY_ACCESS_TOKEN)

ALLOWED_CHAT_IDS = parse_allowed_chat_ids(ALLOWED_CHAT_IDS_RAW)

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


async def send_telegram_message(chat_id: int, text: str) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
            },
        )
        response.raise_for_status()


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
    return {
        "ok": True,
        "whitelist_enabled": bool(ALLOWED_CHAT_IDS),
        "allowed_chat_ids_count": len(ALLOWED_CHAT_IDS),
    }


@app.post("/telegram/{secret}")
async def telegram_webhook(secret: str, request: Request) -> dict:
    if secret != TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    update = await request.json()

    message = update.get("message") or update.get("edited_message")
    if not message:
        return {"ok": True}

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text")

    if not chat_id:
        return {"ok": True}

    # Мінімальна сек'юрність: якщо chat_id не у whitelist,
    # одразу відбиваємося і НЕ витрачаємо Claude / Firefly взагалі.
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

    if not text:
        await send_telegram_message(
            chat_id,
            "Поки що я обробляю тільки текстові повідомлення. Чеки, скріни і голосові зробимо наступним етапом."
        )
        return {"ok": True}

    try:
        if claude.looks_like_balance_setup_request(text):
            parsed_setup = await claude.parse_balance_setup_text(text)
            results = await firefly.setup_balances(parsed_setup["accounts"])
            await send_telegram_message(chat_id, format_balance_setup_result(results))
            return {"ok": True}

        report_reply = await reports.handle_report_request(text)
        if report_reply:
            await send_telegram_message(chat_id, report_reply)
            return {"ok": True}

        parsed = await claude.parse_transaction_text(text)
        await firefly.create_transaction(parsed)

        await send_telegram_message(
            chat_id,
            f"Записав: {parsed['type']} | {parsed['amount']} {parsed['currency']} | {parsed['category']} | {parsed['description']} | рахунок: {parsed['source_account']}"
        )

    except Exception as e:
        print("ERROR =", repr(e))
        await send_telegram_message(chat_id, f"Не зміг обробити повідомлення: {str(e)}")

    return {"ok": True}
