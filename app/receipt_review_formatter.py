"""
Receipt Review Formatter: красивий формат для режиму виправлення позицій.

Показує одну сумнівну позицію за раз, дає кнопки для виправлень.
"""

from typing import Dict, Any, List


def format_receipt_item_review(
    item: Dict[str, Any],
    item_index: int,
    total_items: int,
    total_suspect_items: int,
    current_suspect_number: int
) -> str:
    """
    Форматувати одну позицію для режиму review.
    
    Args:
        item: Товарна позиція
        item_index: Індекс у списку всіх товарів (0-based)
        total_items: Всього товарів
        total_suspect_items: Всього сумнівних
        current_suspect_number: Який по порядку це сумнівний (1-based)
    
    Returns:
        Красиво відформатований текст позиції
    """
    raw_name = item.get("raw_name", "?")
    normalized_name = item.get("normalized_name") or item.get("name", "?")
    price = item.get("total_price", 0)
    category = item.get("category", "Інше")
    confidence = item.get("name_confidence", 0.0)
    normalization_status = item.get("normalization_status", "unknown")
    
    lines = [
        f"🔍 Позиція {current_suspect_number}/{total_suspect_items} (всього #{item_index+1}/{total_items})",
        "",
        f"📄 Сирий рядок: {raw_name}",
        f"✨ Визнана як: {normalized_name}",
        f"💰 Сума: {price:.2f} UAH",
        f"📁 Категорія: {category}",
        "",
        f"📊 Статус: {_format_status(normalization_status)}",
        f"💯 Впевненість: {_format_confidence(confidence)}",
        "",
        "Це правильно?",
    ]
    
    return "\n".join(lines)


def format_receipt_review_menu() -> str:
    """Меню виправлення позиції."""
    return (
        "Виберіть дію:\n"
        "✅ Прийняти\n"
        "✏️ Виправити назву\n"
        "📁 Змінити категорію\n"
        "⏭️ Далі (наступна позиція)"
    )


def format_receipt_name_input_prompt() -> str:
    """Промпт для введення нової назви."""
    return (
        "Введіть нову назву товара (або напиши 'скасувати' щоб відмінити):\n\n"
        "Приклади:\n"
        "• Сметана\n"
        "• Молоко Премієм 1л\n"
        "• Хліб Білий\n"
    )


def format_receipt_category_selector() -> str:
    """Показати список категорій для вибору."""
    categories = [
        "Продукти",
        "Овочі та фрукти",
        "Вода",
        "Солодкі напої",
        "Алкоголь",
        "Фастфуд і снеки",
        "Кафе та ресторани",
        "Цигарки",
        "Пальне",
        "Аптека",
        "Гігієна та догляд",
        "Побутова хімія",
        "Товари для дому",
        "Тварини",
        "Інше",
    ]
    
    lines = ["Виберіть категорію:\n"]
    for idx, cat in enumerate(categories, 1):
        lines.append(f"{idx}. {cat}")
    
    return "\n".join(lines)


def format_correction_saved(raw_name: str, new_name: str, category: str) -> str:
    """Показати підтвердження збереження виправлення."""
    return (
        f"✅ Виправлення збережено!\n\n"
        f"'{raw_name}' → '{new_name}'\n"
        f"Категорія: {category}\n\n"
        f"Система запам'ятала. На наступних чеках цього магазину буде розпізнаватися краще."
    )


def format_review_complete(total_corrections: int) -> str:
    """Показати що review завершено."""
    if total_corrections == 0:
        return (
            "✅ Чек прийнятий без змін!\n\n"
            "Всі позиції були правильними. 👍"
        )
    
    return (
        f"✅ Виправлення завершено!\n\n"
        f"Виправлено позицій: {total_corrections}\n"
        f"Система будет краще розпізнавати цей магазин на наступних чеках. 🚀"
    )


def _format_status(status: str) -> str:
    """Форматувати статус нормалізації для читання."""
    status_map = {
        "ocr_only": "Тільки від AI (без пам'яті)",
        "memory_match": "Розпізнано з пам'яті 🧠",
        "barcode_match": "Розпізнано по штрихкоду 📦",
        "dictionary_match": "Розпізнано по словнику 📖",
        "fuzzy_match": "Схожий товар з пам'яті 🔍",
        "unresolved": "Не розпізнано ❓",
    }
    return status_map.get(status, status)


def _format_confidence(conf: float) -> str:
    """Форматувати впевненість як бар + відсоток."""
    if conf <= 0:
        return "Невідомо ❓"
    
    percent = int(conf * 100)
    bar_length = 10
    filled = int(bar_length * conf)
    bar = "█" * filled + "░" * (bar_length - filled)
    
    if conf >= 0.8:
        emoji = "🟢"
    elif conf >= 0.6:
        emoji = "🟡"
    else:
        emoji = "🔴"
    
    return f"{emoji} {bar} {percent}%"
