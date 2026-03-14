import logging
from typing import Dict, Any

logger = logging.getLogger("finstack")

# Обмеження для транзакцій (залежно від валюти)
AMOUNT_LIMITS = {
    "UAH": {"min": 0.01, "max": 1_000_000},
    "USD": {"min": 0.01, "max": 50_000},
    "EUR": {"min": 0.01, "max": 50_000},
    "GBP": {"min": 0.01, "max": 50_000},
}

class ValidationError(Exception):
    """Помилка валідації транзакції."""
    pass

def validate_amount(amount: float, currency: str = "UAH") -> None:
    """
    Валідувати суму транзакції.
    
    Args:
        amount: сума для перевірки
        currency: валюта (default UAH)
    
    Raises:
        ValidationError: якщо сума не валідна
    """
    limits = AMOUNT_LIMITS.get(currency, AMOUNT_LIMITS["UAH"])
    
    if amount <= 0:
        raise ValidationError(f"Сума має бути позитивною, отримано: {amount}")
    
    if amount < limits["min"]:
        raise ValidationError(
            f"Сума занадто мала: {amount} {currency} "
            f"(мінімум: {limits['min']} {currency})"
        )
    
    if amount > limits["max"]:
        raise ValidationError(
            f"Сума занадто велика: {amount} {currency} "
            f"(максимум: {limits['max']} {currency})"
        )
    
    logger.debug(f"✅ Amount validation passed: {amount} {currency}")

def validate_transaction(parsed: Dict[str, Any]) -> None:
    """
    Валідувати повну транзакцію.
    
    Args:
        parsed: распарсена транзакція
    
    Raises:
        ValidationError: якщо транзакція не валідна
    """
    # Перевірити обов'язкові поля
    required_fields = ["type", "amount", "currency", "category", "description", "source_account"]
    for field in required_fields:
        if field not in parsed or parsed[field] is None:
            raise ValidationError(f"Відсутнє обов'язкове поле: {field}")
    
    # Валідувати тип
    if parsed["type"] not in {"expense", "income"}:
        raise ValidationError(f"Невідомий тип транзакції: {parsed['type']}")
    
    # Валідувати суму
    validate_amount(parsed["amount"], parsed["currency"])
    
    # Валідувати текстові поля (не повинні бути пустими)
    text_fields = ["category", "description", "source_account"]
    for field in text_fields:
        if not isinstance(parsed[field], str) or len(parsed[field].strip()) == 0:
            raise ValidationError(f"Поле {field} не може бути пустим")
    
    logger.debug(f"✅ Transaction validation passed: {parsed['type']} {parsed['amount']} {parsed['currency']}")

def validate_transfer(parsed: Dict[str, Any]) -> None:
    """
    Валідувати переказ між рахунками.
    
    Args:
        parsed: распарсена операція переказу
    
    Raises:
        ValidationError: якщо переказ не валідний
    """
    required_fields = ["amount", "currency", "source_account", "destination_account", "description"]
    for field in required_fields:
        if field not in parsed or parsed[field] is None:
            raise ValidationError(f"Відсутнє обов'язкове поле для переказу: {field}")
    
    # Перевірити що рахунки різні
    if parsed["source_account"] == parsed["destination_account"]:
        raise ValidationError(
            f"Рахунок-відправник і рахунок-отримувач не можуть бути однаковими: "
            f"{parsed['source_account']}"
        )
    
    # Валідувати суму
    validate_amount(parsed["amount"], parsed["currency"])
    
    logger.debug(
        f"✅ Transfer validation passed: "
        f"{parsed['source_account']} → {parsed['destination_account']} "
        f"{parsed['amount']} {parsed['currency']}"
    )

__all__ = ["ValidationError", "validate_amount", "validate_transaction", "validate_transfer"]
