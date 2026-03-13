import asyncio
import os
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request

from app.services.pending_store import PendingStore
from app.services.profile_runtime import ProfileRuntimeFactory
from app.services.profile_service import ProfileService

load_dotenv()

app = FastAPI()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()

CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "").strip()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001").strip()

PROFILES_FILE = os.getenv("PROFILES_FILE", "/app/data/bot/profiles.json").strip()
BOT_TIMEZONE = os.getenv("BOT_TIMEZONE", "Europe/Kyiv").strip()
REMINDER_POLL_SECONDS = int(os.getenv("REMINDER_POLL_SECONDS", "30").strip())

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
TELEGRAM_FILE_API = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}"

reminder_loop_tasks: dict[str, asyncio.Task] = {}


def require_env(name: str, value: str) -> None:
    if not value:
        raise RuntimeError(f"{name} is not set")


require_env("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
require_env("TELEGRAM_WEBHOOK_SECRET", TELEGRAM_WEBHOOK_SECRET)
require_env("CLAUDE_API_KEY", CLAUDE_API_KEY)

profile_service = ProfileService(file_path=PROFILES_FILE)
runtime_factory = ProfileRuntimeFactory(
    profile_service=profile_service,
    claude_api_key=CLAUDE_API_KEY,
    claude_model=CLAUDE_MODEL,
    timezone_name=BOT_TIMEZONE,
    reminder_poll_seconds=REMINDER_POLL_SECONDS,
    data_root="/app/data/bot",
)
pending_store = PendingStore()


async def send_telegram_message(chat_id: int, text: str, reply_markup: Optional[dict] = None) -> None:
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(f"{TELEGRAM_API}/sendMessage", json=payload)
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


def canonicalize_category(runtime, text: str) -> str:
    return runtime.category_rules.resolve_category(text, fallback=text) or text


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

        lines.extend([
            f"• Було: {result.get('old_amount', 0):.2f} {currency} | {result.get('old_description')}",
            f"• Стало: {result.get('new_amount', 0):.2f} {currency} | {result.get('new_description')}",
        ])

        if result.get("old_source_account") != result.get("new_source_account"):
            lines.append(f"• Рахунок: {result.get('old_source_account')} → {result.get('new_source_account')}")
        if result.get("old_destination_account") != result.get("new_destination_account"):
            lines.append(f"• Призначення: {result.get('old_destination_account')} → {result.get('new_destination_account')}")
        if result.get("old_category") != result.get("new_category"):
            lines.append(f"• Категорія: {result.get('old_category')} → {result.get('new_category')}")

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


async def send_profile_picker(chat_id: int) -> None:
    text = profile_service.format_start_text(chat_id)
    keyboard = profile_service.build_profile_keyboard(chat_id)
    await send_telegram_message(chat_id, text, reply_markup=keyboard)


async def ensure_profile_reminder_task(profile_id: str) -> None:
    if profile_id in reminder_loop_tasks:
        return

    runtime = runtime_factory.get(profile_id)
    reminder_loop_tasks[profile_id] = asyncio.create_task(
        runtime.reminder_service.run_forever(send_telegram_message)
    )


@app.on_event("startup")
async def on_startup() -> None:
    for profile in profile_service.list_profiles():
        profile_id = profile.get("profile_id")
        if profile_id:
            await ensure_profile_reminder_task(profile_id)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    for task in reminder_loop_tasks.values():
        task.cancel()
    reminder_loop_tasks.clear()


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "profiles_count": profile_service.count_profiles(),
        "profile_bindings_count": profile_service.count_bindings(),
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

        if chat_id and message_id and data.startswith("bind_profile:"):
            profile_id = data.split(":", 1)[1]

            try:
                profile_service.bind_chat_to_profile(chat_id, profile_id)
                await ensure_profile_reminder_task(profile_id)

                bound_profile = profile_service.get_profile(profile_id)
                title = bound_profile.get("title", profile_id) if bound_profile else profile_id

                await edit_telegram_message(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=(
                        f"Профіль обрано: {title}\n"
                        f"Твій chat_id: {chat_id}\n"
                        "Тепер можеш вести облік у цьому сімейному профілі."
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

    if text and text.strip().lower() in {"/start", "/profile", "змінити профіль"}:
        await send_profile_picker(chat_id)
        return {"ok": True}

    bound_profile_id = profile_service.get_bound_profile_id(chat_id)
    if not bound_profile_id:
        await send_profile_picker(chat_id)
        return {"ok": True}

    runtime = runtime_factory.get(bound_profile_id)

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
            parsed_receipt = await runtime.receipt_parser.parse_receipt_image(image_bytes, media_type)
            await pending_store.set(chat_id, "receipt_confirm", parsed_receipt)
            await send_telegram_message(
                chat_id,
                format_receipt_preview(parsed_receipt, runtime.default_currency),
            )
            return {"ok": True}

        if not text:
            await send_telegram_message(chat_id, "Поки що я обробляю текстові повідомлення і фото чеків.")
            return {"ok": True}

        if runtime.claude.looks_like_balance_setup_request(text):
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

        if runtime.claude.looks_like_transfer_request(text):
            account_names = await runtime.firefly.list_asset_account_names()
            parsed_transfer = await runtime.claude.parse_transfer_text(text, account_names)
            await runtime.firefly.create_transfer(parsed_transfer)
            await send_telegram_message(chat_id, format_transfer_result(parsed_transfer))
            return {"ok": True}

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

        parsed = await runtime.claude.parse_transaction_text(text)
        parsed["category"] = canonicalize_category(runtime, f"{parsed['description']} {parsed['category']}")
        await runtime.firefly.create_transaction(parsed)

        reply_text = (
            f"[{runtime.title}]\n"
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

    except Exception as e:
        print("ERROR =", repr(e))
        await send_telegram_message(chat_id, f"Не зміг обробити повідомлення: {str(e)}")

    return {"ok": True}
