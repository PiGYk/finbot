import json
import logging
from typing import Any, Dict, Optional
from datetime import datetime

logger = logging.getLogger("finstack")


def parse_frequency_and_time(text: str) -> tuple[Optional[str], Optional[str]]:
    """
    Спробувати витягти частоту та час з тексту.
    
    Returns:
        (frequency: "daily"|"weekly"|"monthly"|None, time_of_day: "HH:MM"|None)
    """
    text_lower = text.lower()
    
    # Частота
    frequency = None
    if any(w in text_lower for w in ["кожен день", "щодня", "daily", "кожні день"]):
        frequency = "daily"
    elif any(w in text_lower for w in ["кожен тиждень", "щотижня", "weekly", "раз на тиждень"]):
        frequency = "weekly"
    elif any(w in text_lower for w in ["кожен місяць", "щомісячно", "monthly", "раз на місяць"]):
        frequency = "monthly"
    
    # Час
    time_of_day = None
    
    # Шукати HH:MM або "о HH:MM" або "в HH:MM"
    import re
    
    time_pattern = r'([01]?\d|2[0-3]):([0-5]\d)'
    matches = re.findall(time_pattern, text)
    if matches:
        hour, minute = matches[0]
        time_of_day = f"{hour.zfill(2)}:{minute}"
    
    # Альтернативно, шукати "ранку", "дня", "вечера"
    if not time_of_day:
        if any(w in text_lower for w in ["ранку", "вранці", "morning", "8", "9", "10"]):
            if "8" in text:
                time_of_day = "08:00"
            elif "9" in text:
                time_of_day = "09:00"
            elif "10" in text:
                time_of_day = "10:00"
            else:
                time_of_day = "08:00"  # За замовчуванням ранку
        elif any(w in text_lower for w in ["дня", "день", "noon", "12", "13"]):
            time_of_day = "12:00"
        elif any(w in text_lower for w in ["вечера", "вечір", "evening", "18", "19", "20"]):
            time_of_day = "19:00"
    
    return frequency, time_of_day


__all__ = ["parse_frequency_and_time"]
