"""
Розумніший формат показання чека користувачу.
Замість суми по категоріях — показуємо окремі позиції.
"""

from typing import Dict, List, Any, Optional


def format_receipt_detailed(receipt: Dict[str, Any], show_categories: bool = True) -> str:
    """
    Форматувати чек з показанням окремих позицій.
    
    Args:
        receipt: дані чека
        show_categories: показувати назви категорій для кожної позиції
    
    Returns:
        Красиво відформатований чек
    """
    merchant = receipt.get("merchant") or "Чек"
    currency = receipt.get("currency") or "UAH"
    total = receipt.get("receipt_total", 0)
    items = receipt.get("items", [])
    
    lines = [
        f"📦 Чек: {merchant}",
        ""
    ]
    
    # Показувати окремі позиції
    if items:
        for item in items:
            name = item.get("name", "?")
            price = item.get("total_price", 0)
            category = item.get("category", "Інше")
            
            # Форматувати рядок позиції
            if show_categories:
                line = f"  • {name} — {price:.2f} {currency} ({category})"
            else:
                line = f"  • {name} — {price:.2f} {currency}"
            
            lines.append(line)
        
        lines.append("  " + "—" * 40)
    
    lines.extend([
        f"💰 Загальна сума: {total:.2f} {currency}",
        "",
        "Виберіть дію:",
        "✅ підтвердити чек",
        "❌ скасувати чек"
    ])
    
    return "\n".join(lines)


def format_receipt_compact(receipt: Dict[str, Any]) -> str:
    """
    Компактний формат чека (сгрупована по категоріях).
    
    Args:
        receipt: дані чека
    
    Returns:
        Компактне представлення
    """
    merchant = receipt.get("merchant") or "Чек"
    currency = receipt.get("currency") or "UAH"
    total = receipt.get("receipt_total", 0)
    
    # Групувати позиції по категоріях
    groups: Dict[str, float] = {}
    for item in receipt.get("items", []):
        category = item.get("category", "Інше")
        price = item.get("total_price", 0)
        groups[category] = groups.get(category, 0) + price
    
    lines = [
        f"📦 Чек: {merchant}",
        "Розподіл по категоріях:",
        ""
    ]
    
    # Сортувати по сумі (спадаючи)
    sorted_groups = sorted(groups.items(), key=lambda x: x[1], reverse=True)
    
    for category, amount in sorted_groups:
        percent = (amount / total * 100) if total > 0 else 0
        line = f"  • {category}: {amount:.2f} {currency} ({percent:.1f}%)"
        lines.append(line)
    
    lines.extend([
        "",
        f"💰 Всього: {total:.2f} {currency}",
        "",
        "✅ підтвердити чек | ❌ скасувати чек"
    ])
    
    return "\n".join(lines)


def format_receipt_with_adjustment_prompt(receipt: Dict[str, Any]) -> str:
    """
    Формат чека з можливістю коригування категорій.
    
    Користувач бачить позиції та може сказати "Це не кава, це цигарки!".
    """
    merchant = receipt.get("merchant") or "Чек"
    currency = receipt.get("currency") or "UAH"
    total = receipt.get("receipt_total", 0)
    items = receipt.get("items", [])
    
    lines = [
        f"📦 Чек: {merchant}",
        "Вот позиції що я розпізнав:",
        ""
    ]
    
    for idx, item in enumerate(items, 1):
        name = item.get("name", "?")
        price = item.get("total_price", 0)
        category = item.get("category", "Інше")
        confidence = item.get("category_confidence", "?")
        
        confidence_emoji = "🟢" if confidence == "high" else "🟡" if confidence == "claude" else "⚫"
        
        line = f"{idx}. {confidence_emoji} {name} — {price:.2f} {currency} → {category}"
        lines.append(line)
    
    lines.extend([
        "",
        f"💰 Всього: {total:.2f} {currency}",
        "",
        "Якщо категорія невірна, напиши: 'позиція 1 це цигарки' або 'cappuccino це кава'",
        "Потім: ✅ підтвердити чек або ❌ скасувати чек"
    ])
    
    return "\n".join(lines)


__all__ = [
    "format_receipt_detailed",
    "format_receipt_compact",
    "format_receipt_with_adjustment_prompt",
]
