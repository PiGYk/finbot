# Finance Telegram Bot for Firefly III

Telegram-бот для особистого або сімейного фінансового обліку з природною мовою, Firefly III і AI-розбором тексту та чеків.

Проєкт приймає повідомлення в Telegram, визначає намір користувача, розбирає транзакцію або іншу команду через Claude, а потім записує результат у Firefly III. Додатково бот уміє працювати з фото чеків, правилами категоризації, нагадуваннями, бюджетами, звітами, порадами та кількома профілями обліку.

---

## Що вміє бот

### Основний облік
- додавати **витрати** та **доходи** з тексту природною мовою
- записувати **перекази між рахунками**
- задавати **початкові баланси** або робити корекцію балансу по рахунках
- редагувати або видаляти **останню транзакцію**

### Чеки
- приймати **фото чеків** з Telegram
- відправляти зображення в Claude для аналізу
- групувати позиції чека по категоріях
- показувати прев’ю перед записом
- записувати чек у Firefly III тільки після підтвердження

### Категорії
- створювати нові категорії текстом
- зберігати власні **аліаси категорій** у JSON
- використовувати локальні правила для нормалізації категорій
- підтримувати окремі правила для окремих профілів

### Нагадування і бюджети
- створювати щоденні нагадування
- зберігати їх локально й відправляти у потрібний час
- створювати бюджетні плани
- пропонувати бюджет автоматично після надходження доходу

### Аналітика
- відповідати на базові фінансові запити
- будувати короткі звіти по витратах, доходах і категоріях
- давати короткі поради на основі історії транзакцій

### Профілі
- працювати в режимі **одного профілю** або **кількох профілів**
- прив’язувати Telegram chat_id до конкретного профілю
- мати окремі правила категорій, нагадування й бюджети для кожного профілю

---

## Як це працює

1. Telegram надсилає webhook у FastAPI.
2. Бот визначає контекст: текст, чек, callback-кнопка, профіль.
3. Claude розбирає команду або транзакцію.
4. Firefly III отримує транзакцію, переказ, корекцію балансу або іншу дію.
5. Локальні JSON-файли зберігають правила категорій, нагадування, бюджети і прив’язки профілів.

---

## Рекомендована структура репозиторію

У коді використовується імпорт у стилі `app.services.*`, тому найкраще оформити репозиторій так:

```text
.
├── README.md
├── REPOSITORY_DESCRIPTION.md
├── .env.example
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── app/
│   ├── __init__.py
│   ├── main.py
│   └── services/
│       ├── __init__.py
│       ├── advisor.py
│       ├── budget_service.py
│       ├── category_rules.py
│       ├── claude_parser.py
│       ├── firefly_client.py
│       ├── pending_store.py
│       ├── profile_runtime.py
│       ├── profile_service.py
│       ├── receipt_parser.py
│       ├── reminder_service.py
│       └── reports.py
└── data/
    ├── category_rules.json
    ├── reminders.json
    ├── budgets.json
    └── bot/
        ├── profiles.json
        ├── category_rules_<profile_id>.json
        ├── reminders_<profile_id>.json
        └── budgets_<profile_id>.json
```

Якщо зараз файли лежать пласко в одній папці, їх варто розкласти саме так. Інакше Python-імпорти виглядатимуть як привіт із паралельного всесвіту.

---

## Основні модулі

### `app/main.py`
Точка входу FastAPI. Тут живуть:
- webhook для Telegram
- startup/shutdown
- розв’язання профілю по chat_id
- orchestration між Claude, Firefly і локальними сервісами
- `/health`

### `app/services/claude_parser.py`
Парсинг текстових команд через Claude:
- транзакції
- початкові баланси
- перекази
- дії над останньою транзакцією
- створення категорій
- створення нагадувань
- створення бюджетів
- визначення intent

### `app/services/receipt_parser.py`
AI-розбір чеків з фото:
- merchant
- дата чека
- сума
- позиції
- категорії
- агрегація по категоріях

### `app/services/firefly_client.py`
Клієнт Firefly III API:
- створення транзакцій
- перекази
- корекція балансів
- робота з категоріями
- створення транзакцій з чеків
- робота з останньою транзакцією

### `app/services/category_rules.py`
Локальне сховище правил категоризації:
- canonical category
- aliases
- fuzzy matching
- оновлення та форматування правил

### `app/services/reminder_service.py`
Щоденні нагадування з локальним JSON-сховищем.

### `app/services/budget_service.py`
Локальні бюджетні плани з авто-розподілом по категоріях на основі історії Firefly.

### `app/services/reports.py`
Короткі звіти по витратах, доходах і категоріях.

### `app/services/advisor.py`
Поради на основі фінансової історії.

### `app/services/profile_service.py`
Завантаження профілів, прав доступу і прив’язок чатів.

### `app/services/profile_runtime.py`
Фабрика runtime-об’єктів для кожного профілю.

### `app/services/pending_store.py`
Тимчасове сховище для дій, які треба підтвердити, наприклад для чеків.

---

## Залежності

Мінімально потрібні:
- Python 3.11+
- FastAPI
- Uvicorn
- httpx
- python-dotenv
- Firefly III
- Telegram Bot API
- Claude API

Приклад `requirements.txt`:

```txt
fastapi
uvicorn[standard]
httpx
python-dotenv
```

---

## Налаштування

### 1. Створи Telegram-бота
Через BotFather отримай токен.

### 2. Створи API token у Firefly III
У Firefly III потрібен персональний access token.

### 3. Підготуй Claude API key
Бот використовує Claude для розбору тексту і чеків.

### 4. Заповни `.env`
Використай `.env.example` як шаблон.

---

## Змінні середовища

### Обов’язкові
- `TELEGRAM_BOT_TOKEN` - токен бота Telegram
- `TELEGRAM_WEBHOOK_SECRET` - секрет у URL вебхука
- `CLAUDE_API_KEY` - API ключ Claude
- `FIREFLY_ACCESS_TOKEN` - access token Firefly III

### Базові налаштування
- `CLAUDE_MODEL` - модель Claude, за замовчуванням `claude-haiku-4-5-20251001`
- `FIREFLY_BASE_URL` - URL Firefly III
- `DEFAULT_SOURCE_ACCOUNT` - рахунок за замовчуванням
- `DEFAULT_CURRENCY` - валюта за замовчуванням, наприклад `UAH`
- `ALLOWED_CHAT_IDS` - список дозволених chat_id через кому для режиму без профілів
- `BOT_TIMEZONE` - часовий пояс, за замовчуванням `Europe/Kyiv`
- `REMINDER_POLL_SECONDS` - частота перевірки нагадувань

### Файли локальних даних
- `CATEGORY_RULES_FILE`
- `REMINDER_DATA_FILE`
- `BUDGET_DATA_FILE`

### Режим кількох профілів
- `PROFILES_FILE` - JSON-файл з профілями, доступами і bindings
- `BOT_DATA_ROOT` - каталог для профільних JSON-файлів

---

## Приклад `.env`

```env
TELEGRAM_BOT_TOKEN=123456:telegram-token
TELEGRAM_WEBHOOK_SECRET=super-secret-string

CLAUDE_API_KEY=sk-ant-xxxxx
CLAUDE_MODEL=claude-haiku-4-5-20251001

FIREFLY_BASE_URL=http://firefly:8080
FIREFLY_ACCESS_TOKEN=your-firefly-token

DEFAULT_SOURCE_ACCOUNT=Готівка
DEFAULT_CURRENCY=UAH

ALLOWED_CHAT_IDS=123456789,987654321
BOT_TIMEZONE=Europe/Kyiv
REMINDER_POLL_SECONDS=30

CATEGORY_RULES_FILE=/app/data/category_rules.json
REMINDER_DATA_FILE=/app/data/reminders.json
BUDGET_DATA_FILE=/app/data/budgets.json

PROFILES_FILE=/app/data/bot/profiles.json
BOT_DATA_ROOT=/app/data/bot
```

---

## Режими роботи

### Варіант 1. Один профіль
Найпростіший режим:
- задаєш `FIREFLY_ACCESS_TOKEN`
- в `.env` додаєш `ALLOWED_CHAT_IDS`
- бот працює як один фінансовий обліковий контур

### Варіант 2. Кілька профілів
Використовуй `profiles.json`, якщо треба:
- кілька рахунків Firefly
- окремі профілі для сім’ї, бізнесу або різних людей
- окремі правила категорій і нагадування

Приклад `profiles.json`:

```json
{
  "profiles": [
    {
      "profile_id": "family",
      "title": "Сімейний бюджет",
      "firefly_base_url": "http://firefly:8080",
      "firefly_access_token": "token-for-family",
      "default_source_account": "Готівка",
      "default_currency": "UAH"
    },
    {
      "profile_id": "business",
      "title": "Ветеринарка",
      "firefly_base_url": "http://firefly:8080",
      "firefly_access_token": "token-for-business",
      "default_source_account": "Каса",
      "default_currency": "UAH"
    }
  ],
  "chat_access": {
    "123456789": ["family", "business"]
  },
  "chat_bindings": {
    "123456789": "family"
  }
}
```

---

## Запуск локально

### Через Uvicorn

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Через Docker Compose

Приклад ідеї:
- контейнер з ботом
- контейнер або зовнішній інстанс Firefly III
- volume для `data/`
- reverse proxy для HTTPS

---

## Налаштування Telegram webhook

Після запуску сервісу потрібно виставити webhook на:

```text
https://your-domain.com/telegram/<TELEGRAM_WEBHOOK_SECRET>
```

Приклад запиту до Telegram API:

```bash
curl -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  -d "url=https://your-domain.com/telegram/<TELEGRAM_WEBHOOK_SECRET>"
```

---

## Healthcheck

Бот має endpoint:

```text
GET /health
```

Він повертає:
- чи запущений сервіс
- чи увімкнений whitelist
- чи увімкнений режим профілів
- кількість правил категорій
- кількість нагадувань
- кількість бюджетів

---

## Приклади повідомлень, які бот має розуміти

### Витрати і доходи
- `Купив каву 95 грн`
- `Продукти 1240 грн з картки`
- `Отримав 25000 зарплати`

### Баланси
- `Встанови готівку 5000 і монобанк 12000`
- `Початковий баланс: готівка 3000, карта 8000`

### Перекази
- `Переведи 1000 грн з Монобанку в Готівку`
- `Переказ 500 з каси на карту`

### Категорії
- `Створи категорію Солодкі напої, аліаси: кола, пепсі, спрайт`

### Нагадування
- `Нагадуй щодня о 9:00 записувати витрати`

### Бюджети
- `Створи бюджет 15000 грн на місяць`

### Чеки
- відправ фото чека
- бот покаже прев’ю
- далі напиши `підтвердити чек` або `скасувати чек`

### Звіти
- `Скільки я витратив цього місяця?`
- `Які топ категорії за 30 днів?`

### Поради
- `На що я найбільше витрачаю?`
- `Що можна оптимізувати в моїх витратах?`

### Остання транзакція
- `Видали останню транзакцію`
- `Зміни останню витрату на 250 грн`
- `Це була категорія Аптека`

---

## Локальні дані

Бот зберігає частину стану не у Firefly, а в JSON-файлах.

### Навіщо це потрібно
- правила категоризації не мають губитися
- нагадування мають переживати рестарт контейнера
- бюджети мають бути локально доступні
- прив’язки профілів до чатів мають зберігатися між перезапусками

### Що треба зробити в проді
Каталог `data/` треба винести у volume або bind mount.

---

## Що добре б доробити далі

Проєкт уже корисний, але логічні наступні кроки такі:
- повний CRUD для нагадувань
- повний CRUD для бюджетів
- повний CRUD для підписок у Firefly III
- більш детальна класифікація чеків
- автонавчання категорій на виправленнях користувача
- `.env.example`, `requirements.txt`, `Dockerfile`, `docker-compose.yml` у репі
- тести на ключові сценарії

---

## Відомі обмеження

- AI-парсинг не гарантує 100% точності
- чек із поганою якістю фото може розібратися криво
- категоризація сильно залежить від правил і від якості тексту в чеку
- локальні JSON-файли не замінюють нормальну БД, але для цього проєкту це свідомий компроміс

---

## Порада перед комітом на GitHub

Щоб репозиторій виглядав як нормальний проєкт, а не як коробка з дротами після ремонту:
- додай `README.md`
- додай `.env.example`
- додай `requirements.txt`
- додай `.gitignore`
- не коміть `.env`, токени і локальні `data/*.json`
- винеси код у `app/` і `app/services/`
- зафіксуй один базовий спосіб запуску, бажано Docker Compose

---

## Ліцензія

Додай ту ліцензію, яка тобі підходить:
- MIT, якщо хочеш максимально просте використання
- Apache-2.0, якщо хочеш трохи формальніше
- Private / Proprietary, якщо це внутрішній проєкт

Поки ліцензії нема, з юридичного погляду це просто набір файлів із характером.
