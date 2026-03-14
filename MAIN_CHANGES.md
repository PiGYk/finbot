# Зміни до main.py для інтеграції нових модулів

## Місце 1: Додати Logging (після load_dotenv)

**Знайти:**
```python
load_dotenv()

app = FastAPI()
```

**Замінити на:**
```python
load_dotenv()

# Налаштування логування
logger = setup_logging(
    log_dir=os.getenv("LOG_DIR", "/app/logs"),
    log_level=os.getenv("LOG_LEVEL", "INFO")
)

app = FastAPI()
```

---

## Місце 2: Додати ReceiptEnhancer у ProfileRuntime

**Знайти:**
```python
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
```

**Додати поле:**
```python
    receipt_enhancer: ReceiptEnhancer  # ← НОВЕ
```

---

## Місце 3: Додати Rate Limiting перед обробкою текстуДнайти строку ~927 (обробка текстового повідомлення):
```python
        if not text:
            await send_telegram_message(
                chat_id,
                "Поки що я обробляю текст і фото чеків..."
            )
            return {"ok": True}
```

**Додати перед цим (rate limiting):**
```python
        # Rate limiting
        if not claude_limiter.check_and_wait(chat_id):
            await send_telegram_message(
                chat_id,
                "⚠️ Занадто багато запитів за мінуту. Зачекайте трохи перед наступною командою."
            )
            logger.warning(f"Rate limit triggered for chat_id {chat_id}")
            return {"ok": True}
```

---

## Місце 4: Обновити looks_like_* вызови (додати `await`)

**Знайти (рядок ~927):**
```python
        if runtime.claude.looks_like_balance_setup_request(text):
```

**Замінити на:**
```python
        if await runtime.claude.looks_like_balance_setup_request(text):
```

**Знайти (рядок ~967):**
```python
        if runtime.claude.looks_like_transfer_request(text):
```

**Замінити на:**
```python
        if await runtime.claude.looks_like_transfer_request(text):
```

**Знайти (рядок ~974):**
```python
        if runtime.claude.looks_like_last_transaction_action_request(text):
```

**Замінити на:**
```python
        if await runtime.claude.looks_like_last_transaction_action_request(text):
```

---

## Місце 5: Обновити обробку чеків (додати ReceiptEnhancer + Formatter)

**Знайти (рядок ~911):**
```python
        if photo:
            largest_photo = photo[-1]
            file_id = largest_photo["file_id"]
            image_bytes, media_type = await get_telegram_file_bytes(file_id)
            parsed_receipt = await runtime.receipt_parser.parse_receipt_image(image_bytes, media_type)
            await pending_store.set(chat_id, "receipt_confirm", parsed_receipt)
            await send_telegram_message(chat_id, format_receipt_preview(parsed_receipt))
            return {"ok": True}
```

**Замінити на:**
```python
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
```

---

## Місце 6: Додати Валідацію при записі транзакції

**Знайти (рядок ~1000, де записується транзакція):**
```python
        parsed = await runtime.claude.parse_transaction_text(text)
        await runtime.firefly.create_transaction(parsed)

        await send_telegram_message(
            chat_id,
            f"Записав: {parsed['type']} | {parsed['amount']} {parsed['currency']} | ..."
        )
```

**Замінити на:**
```python
        parsed = await runtime.claude.parse_transaction_text(text)
        
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
        
        await send_telegram_message(
            chat_id,
            f"✅ Записав: {parsed['type']} | {parsed['amount']} {parsed['currency']} | {parsed['category']} | {parsed['description']} | рахунок: {parsed['source_account']}"
        )
```

---

## Місце 7: Обновити Error Handling (замість print на logger)

**Знайти (рядок ~1030):**
```python
    except Exception as e:
        print("ERROR =", repr(e))
        await send_telegram_message(chat_id, f"Не зміг обробити повідомлення: {str(e)}")
```

**Замінити на:**
```python
    except Exception as e:
        logger.error(f"Unexpected error in webhook for chat_id {chat_id}: {repr(e)}")
        await send_telegram_message(chat_id, f"❌ Не зміг обробити повідомлення: {str(e)}")
```

---

## Місце 8: Додати ReceiptEnhancer у bootstrap функцію

**Знайти функцію `async def _bootstrap_profile_runtime()`**

Там де інціалізуються сервіси:
```python
    receipt_parser = ReceiptParser(
        api_key=CLAUDE_API_KEY,
        model=CLAUDE_MODEL,
        default_currency=default_currency,
        category_rules=category_rules,
    )
```

**Додати після цього:**
```python
    receipt_enhancer = ReceiptEnhancer(
        api_key=CLAUDE_API_KEY,
        model=CLAUDE_MODEL,
    )
```

---

## Місце 9: Додати receipt_enhancer у ProfileRuntime інстанціювання

**Знайти (останні рядки функції _bootstrap_profile_runtime):**
```python
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
        reminder_service=reminder_service,
        budget_service=budget_service,
    )
```

**Додати поле:**
```python
        receipt_enhancer=receipt_enhancer,  # ← НОВЕ
```

---

## Резюме змін

| Місце | Що робити | Тип |
|-------|-----------|-----|
| 1 | Додати logging setup | Імпорт |
| 2 | Додати поле у ProfileRuntime | Dataclass |
| 3 | Додати rate limiting | Функціональність |
| 4 | Додати `await` до looks_like_* | Критичне |
| 5 | Обновити обробку чеків | Критичне |
| 6 | Додати валідацію | Функціональність |
| 7 | Замінити print на logger | Best Practice |
| 8-9 | Додати ReceiptEnhancer | Функціональність |

**Всього змін: ~50-70 ліній у 8 місцях**

---

## Перевірка після змін

```bash
# 1. Синтаксис
python3 -m py_compile app/main.py

# 2. Імпорти
python3 -c "from app.main import *"

# 3. Тестування (якщо є Docker)
docker-compose up
```
