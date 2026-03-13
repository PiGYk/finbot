import os
from typing import Tuple

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request

from app.services.claude_parser import ClaudeParser
from app.services.firefly_client import FireflyClient
from app.services.pending_store import PendingStore
from app.services.receipt_parser import ReceiptParser
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

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
TELEGRAM_FILE_API = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}"


def require_env(name: str, value: str) -> None:
    if not value:
        raise RuntimeError(f"{name} is not set")


require_env("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
require_env("TELEGRAM_WEBHOOK_SECRET", TELEGRAM_WEBHOOK_SECRET)
require_env("CLAUDE_API_KEY", CLAUDE_API_KEY)
require_env("FIREFLY_ACCESS_TOKEN", FIREFLY_ACCESS_TOKEN)

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

receipt_parser = ReceiptParser(
    api_key=CLAUDE_API_KEY,
    model=CLAUDE_MODEL,
    default_currency=DEFAULT_CURRENCY,
)

reports = ReportService(
    firefly=firefly,
    default_currency=DEFAULT_CURRENCY,
)

pending_store = PendingStore()


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


async def get_telegram_file_bytes(file_id: str) -> Tuple[bytes, str]:
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


def format_receipt_preview(receipt: dict) -> str:
    merchant = receipt.get("merchant") or "Чек"
    currency = receipt.get("currency") or DEFAULT_CURRENCY
    total = receipt.get("receipt_total", 0)
    groups = receipt.get("category_totals", [])

    lines = [f"Розібрав чек: {merchant}", f"Загальна сума: {total:.2f} {currency}"]

    if groups:
        lines.append("Попередній розподіл:")
        for item in groups:
            lines.append(f"• {item['category']} — {item['amount']:.2f} {currency}")

    lines.append("")
    lines.append("Напиши: «підтвердити чек» або «скасувати чек».")

    return "\n".join(lines)


def format_receipt_commit_result(receipt: dict, result: dict) -> str:
    merchant = receipt.get("merchant") or "Чек"
    currency = receipt.get("currency") or DEFAULT_CURRENCY
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


def format_last_transaction_action_result(result: dict) -> str:
    action = result.get("action")
    currency = result.get("currency", DEFAULT_CURRENCY)

    if action == "deleted":
        return (
            f"Видалив останню транзакцію:\n"
            f"• Тип: {result.get('old_type')}\n"
            f"• Сума: {result.get('old_amount', 0):.2f} {currency}\n"
            f"• Опис: {result.get('old_description')}"
        )

    if action == "updated":
        lines = [
            "Оновив останню транзакцію:",
            f"• Було: {result.get('old_amount', 0):.2f} {currency} | {result.get('old_description')}",
            f"• Стало: {result.get('new_amount', 0):.2f} {currency} | {result.get('new_description')}",
        ]

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


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


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
    photo = message.get("photo")

    if not chat_id:
        return {"ok": True}

    try:
        if photo:
            largest_photo = photo[-1]
            file_id = largest_photo["file_id"]
            image_bytes, media_type = await get_telegram_file_bytes(file_id)
            parsed_receipt = await receipt_parser.parse_receipt_image(image_bytes, media_type)
            await pending_store.set(chat_id, "receipt_confirm", parsed_receipt)
            await send_telegram_message(chat_id, format_receipt_preview(parsed_receipt))
            return {"ok": True}

        if not text:
            await send_telegram_message(
                chat_id,
                "Поки що я обробляю текст і фото чеків. Голосові та скріни банку додамо окремо."
            )
            return {"ok": True}

        pending = await pending_store.get(chat_id)
        if pending and pending.get("kind") == "receipt_confirm":
            if is_receipt_confirm_text(text):
                receipt = pending["payload"]
                result = await firefly.create_receipt_transactions(
                    receipt=receipt,
                    default_source_account=DEFAULT_SOURCE_ACCOUNT,
                    default_currency=DEFAULT_CURRENCY,
                )
                await pending_store.clear(chat_id)
                await send_telegram_message(chat_id, format_receipt_commit_result(receipt, result))
                return {"ok": True}

            if is_receipt_cancel_text(text):
                await pending_store.clear(chat_id)
                await send_telegram_message(chat_id, "Окей, чек скасовано. Нічого не записував.")
                return {"ok": True}

        if claude.looks_like_balance_setup_request(text):
            parsed_setup = await claude.parse_balance_setup_text(text)
            results = await firefly.setup_balances(parsed_setup["accounts"])
            await send_telegram_message(chat_id, format_balance_setup_result(results))
            return {"ok": True}

        if claude.looks_like_last_transaction_action_request(text):
            account_names = await firefly.list_asset_account_names()
            action_spec = await claude.parse_last_transaction_action_text(text, account_names)
            result = await firefly.apply_last_transaction_action(
                action_spec=action_spec,
                default_currency=DEFAULT_CURRENCY,
                default_source_account=DEFAULT_SOURCE_ACCOUNT,
            )
            await send_telegram_message(chat_id, format_last_transaction_action_result(result))
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
