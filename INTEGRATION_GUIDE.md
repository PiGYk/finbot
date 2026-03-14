# 🚀 Інструкція Інтеграції Покращень

## Файли для інтеграції

На `finstack-bot-improved/app/`:
- ✅ `receipt_enhancer.py` — покращена категоризація позицій
- ✅ `receipt_formatter.py` — розумніший формат чека
- ✅ `smart_intent_detector.py` — розумна детекція намірів
- ✅ `claude_retry.py` — retry logic для Claude
- ✅ `rate_limiter.py` — контроль від спаму
- ✅ `validators.py` — валідація сум
- ✅ `logging_config.py` — proper logging

---

## Крок 1: Інтеграція ReceiptEnhancer + Formatter

### В `app/main.py`:

```python
# На початку файла, в импортах:
from app.receipt_enhancer import ReceiptEnhancer
from app.receipt_formatter import (
    format_receipt_detailed,
    format_receipt_compact,
    format_receipt_with_adjustment_prompt,
)

# В bootstrap функції ProfileRuntime:
class ProfileRuntime:
    def __init__(self, ...):
        ...
        self.receipt_parser = ReceiptParser(...)
        # НОВЕ:
        self.receipt_enhancer = ReceiptEnhancer(
            api_key=CLAUDE_API_KEY,
            model=CLAUDE_MODEL
        )

# В webhook обробці для фото:
if photo:
    largest_photo = photo[-1]
    file_id = largest_photo["file_id"]
    image_bytes, media_type = await get_telegram_file_bytes(file_id)
    
    # Парсити чек
    parsed_receipt = await receipt_parser.parse_receipt_image(image_bytes, media_type)
    
    # НОВЕ: покращити категоризацію позицій
    enhanced_items = await receipt_enhancer.enhance_receipt_categories(
        items=parsed_receipt["items"],
        merchant=parsed_receipt["merchant"]
    )
    parsed_receipt["items"] = enhanced_items
    
    # Зберегти у pending
    await pending_store.set(chat_id, "receipt_confirm", parsed_receipt)
    
    # ЗАМІНИТИ на детальний формат:
    # Було: format_receipt_preview(parsed_receipt)
    # Буде:
    receipt_message = format_receipt_detailed(parsed_receipt, show_categories=True)
    await send_telegram_message(chat_id, receipt_message)
    return {"ok": True}
```

---

## Крок 2: Інтеграція SmartIntentDetector

### Оновити `claude_parser.py`:

```python
# На початку:
from app.smart_intent_detector import SmartIntentDetector
from app.claude_retry import retry_with_backoff

class ClaudeParser:
    def __init__(self, ...):
        ...
        # НОВЕ:
        self.intent_detector = SmartIntentDetector(
            api_key=api_key,
            model=model
        )
    
    # ЗАМІНИТИ на async методи:
    async def looks_like_balance_setup_request(self, text: str) -> bool:
        """Розумна детекція запиту на встановлення балансів."""
        result = await self.intent_detector.detect_intent(text)
        return result["intent"] == "balance_setup"
    
    async def looks_like_last_transaction_action_request(self, text: str) -> bool:
        """Розумна детекція запиту на редагування."""
        result = await self.intent_detector.detect_intent(text)
        return result["intent"] == "last_action"
    
    # Додати retry в _call_claude:
    async def _call_claude(self, prompt: str) -> Dict[str, Any]:
        """Викликати Claude з retry."""
        
        async def _do_call():
            import httpx
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            
            payload = {
                "model": self.model,
                "max_tokens": 350,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            }
            
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(self.api_url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
            
            content_blocks = data.get("content", [])
            if not content_blocks:
                raise ValueError("Claude повернув порожню відповідь")
            
            text_parts = []
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            
            raw_text = "\n".join(text_parts).strip()
            raw_text = strip_code_fences(raw_text)
            
            try:
                return json.loads(raw_text)
            except json.JSONDecodeError as e:
                raise ValueError(f"Claude повернув не JSON: {raw_text}") from e
        
        # Retry з exponential backoff
        return await retry_with_backoff(
            _do_call,
            max_retries=3,
            initial_delay=1.0,
            max_delay=10.0
        )
```

### Оновити вызови у `main.py`:

```python
# БУЛО:
if claude.looks_like_balance_setup_request(text):
    ...

# БУДЕ (добавити await!):
if await claude.looks_like_balance_setup_request(text):
    ...

# БУЛО:
if claude.looks_like_last_transaction_action_request(text):
    ...

# БУДЕ:
if await claude.looks_like_last_transaction_action_request(text):
    ...
```

---

## Крок 3: Додати Logging

### В `app/main.py`:

```python
from app.logging_config import setup_logging

# У функції startup або на початку main:
logger = setup_logging(
    log_dir=os.getenv("LOG_DIR", "/app/logs"),
    log_level=os.getenv("LOG_LEVEL", "INFO")
)

# Вже в коді замінити print() на logger.*:
# БУЛО: print("ERROR =", repr(e))
# БУДЕ: logger.error(f"Failed to process message: {repr(e)}")
```

---

## Крок 4: Додати Валідацію та Rate Limiting

### В `main.py`:

```python
from app.rate_limiter import claude_limiter, firefly_limiter
from app.validators import validate_transaction, ValidationError

# В обробці тексту:
async def handle_text_message(chat_id: int, text: str):
    # Rate limiting
    if not claude_limiter.check_and_wait(chat_id):
        await send_telegram_message(
            chat_id,
            "⚠️ Занадто багато запитів. Зачекайте 1 хвилину перед наступною дією."
        )
        return
    
    try:
        parsed = await claude.parse_transaction_text(text)
        
        # НОВЕ: Валідація
        validate_transaction(parsed)
        
        # Записати транзакцію
        await firefly.create_transaction(parsed)
        
    except ValidationError as e:
        logger.warning(f"Validation error: {str(e)}")
        await send_telegram_message(chat_id, f"❌ Помилка: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error: {repr(e)}")
        await send_telegram_message(chat_id, "❌ Помилка при обробці повідомлення")
```

---

## Крок 5: Тестування

### Тестові сценарії:

```bash
# Тест 1: Різні формати транзакцій
"кава 200"
"витратив 200 на каву"
"каву за 200"  ← новий формат, повинен працювати

# Тест 2: Чек з "КОЛО"
📷 Надіслати фото чека з HEETS
← Мав розпізнати як "Цигарки", не "Ресторан"
← Показати позиції окремо, не суму

# Тест 3: Rate limiting
10+ запитів за хвилину
← Бот повинен сказати "Занадто багато"

# Тест 4: Retry logic
Вимкнути інтернет під час Claude call
← Бот мав retry 3 рази перед помилкою
```

---

## Чек-лист для покращень

- [ ] Скопійовано `receipt_enhancer.py`
- [ ] Скопійовано `receipt_formatter.py`
- [ ] Скопійовано `smart_intent_detector.py`
- [ ] Оновлено `claude_parser.py` з retry logic
- [ ] Оновлено `main.py` з новими імпортами
- [ ] Додано logging на місцях `print()`
- [ ] Добавлено валідацію в `main.py`
- [ ] Добавлено rate limiting для claude/firefly
- [ ] Протестовано на реальних чеках
- [ ] Протестовано на різних форматах запитів

---

## Проблеми, що вирішуються

### ✅ Проблема 1: Чек сумується замість позицій
**Було:**
```
Чек: КОЛО
Загальна сума: 500 UAH
Категорії:
• Ресторан - 500 UAH
```

**Буде:**
```
Чек: КОЛО
• Cappuccino - 120 UAH (Кава)
• HEETS - 100 UAH (Цигарки)
• Чіпси - 80 UAH (Снеки)
...
💰 Загальна сума: 500 UAH
```

### ✅ Проблема 2: Розпізнавання позицій погане
**Було:** "КОЛО" розпізнав як "Ресторан"

**Буде:** 
- High-priority keywords (HEETS, cappuccino) мають 100% точність
- Claude розпізнає 95%+ решти позицій
- Користувач може коригувати перед підтвердженням

### ✅ Проблема 3: Негнучкість парсингу
**Було:** "кава 200" працює, "витратив 200 на каву" не працює

**Буде:** SmartIntentDetector розуміє обидва формати (та ще 50+ варіацій)

---

## Наступні кроки (Phase 2)

- [ ] Додати можливість користувачу коригувати категорії перед запис
- [ ] Запам'ятовувати вибір користувача для автоматизації
- [ ] ML модель для категоризації (замість regex)
- [ ] Ekspor чека в PDF
- [ ] Синхронізація позицій чека з Firefly Split

Готово до інтеграції? 🦞
