Що додано в цій збірці

1. Керування нагадуваннями:
- показати нагадування
- змінити нагадування
- вимкнути / увімкнути нагадування
- видалити нагадування

Приклади:
- покажи мої нагадування
- зміни нагадування про відкласти 500 на 08:30
- вимкни нагадування про воду
- видали нагадування №2

2. Керування підписками:
- показати підписки
- змінити підписку
- вимкнути / увімкнути підписку
- видалити підписку

Приклади:
- покажи мої підписки
- зміни підписку Netflix на 299 грн
- вимкни підписку Spotify
- видали підписку Xbox

3. Останні операції:
- покажи останні 10 операцій
- що я записав останнім часом

4. Підтвердження неоднозначних транзакцій:
- якщо категорія дуже нечітка або в тексті схоже на кілька сум, бот попросить підтвердження перед записом

5. Бюджет vs факт:
- як я йду по бюджету
- покажи факт vs план по бюджету

6. Автонавчання категорій на виправленнях:
- якщо ти виправляєш категорію останньої транзакції, бот запам’ятовує це як правило

7. Нагадування про підписки:
- бот сам надсилає повідомлення про підписки, що скоро спишуться
- керується env SUBSCRIPTION_REMINDER_DAYS, за замовчуванням 2

8. Порівняльні звіти місяць до місяця:
- порівняй витрати цього місяця з минулим
- порівняй дохід цього місяця з минулим

Файли, які треба замінити
- main.py
- firefly_client.py
- claude_parser.py
- reminder_service.py
- budget_service.py
- reports.py
- category_rules.py
- profile_service.py

Як оновити
1. Зроби бекап старих файлів.
2. Замінюй файли новими.
3. Перезбери або перезапусти контейнер.
4. Перевір логи.

Команди:
cp main.py main.py.bak
cp app/services/firefly_client.py app/services/firefly_client.py.bak
cp app/services/claude_parser.py app/services/claude_parser.py.bak
cp app/services/reminder_service.py app/services/reminder_service.py.bak
cp app/services/budget_service.py app/services/budget_service.py.bak
cp app/services/reports.py app/services/reports.py.bak
cp app/services/category_rules.py app/services/category_rules.py.bak
cp app/services/profile_service.py app/services/profile_service.py.bak

Після заміни файлів:
docker compose up -d --build

Або, якщо код змонтований volume-ом:
docker compose restart

Логи:
docker compose logs -f
