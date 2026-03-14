"""
Merchant Profiles: специфичные правила для разных магазинов.

Логика:
- Каждый магазин имеет свой "DNA" (сокращения, форматы, типичные товары)
- Правила применяются ДО общего словаря (приоритет выше)
- Легко расширяемо: просто добавь новый profile в registry

Example:
ATB: "Смет" = "Сметана" (30% товаров в ATB это молочка)
Silpo: "Крас" = "Краса" (много косметики)
AZS: "A95" = "Пальне" (явно бензин, не другое)
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Set
import re


@dataclass
class MerchantProfile:
    """Профиль магазина с правилами."""
    
    merchant_id: str
    display_name: str
    header_patterns: List[str]  # Regex для детектирования (например: ["atb", "а-т-б"])
    
    # Специфичные алиасы (приоритет над глобальным словарём)
    aliases: Dict[str, str]  # {"смет": "Сметана", "молок": "Молоко"}
    
    # Позиции, которые ВСЕГДА пропускать (служебные, реклама)
    ignore_patterns: List[str]  # ["АКЦИЯ", "БОНУС", "РСВ"]
    
    # Типичные категории для этого магазина
    common_categories: List[str]
    
    # Заметки для разработчика
    notes: Optional[str] = None


class MerchantRegistry:
    """Реестр профилей магазинов."""
    
    def __init__(self):
        self.profiles = self._initialize_profiles()
    
    def _initialize_profiles(self) -> Dict[str, MerchantProfile]:
        """Инициализировать встроенные профили магазинов."""
        
        return {
            # ===== АТБ =====
            "atb": MerchantProfile(
                merchant_id="atb",
                display_name="АТБ",
                header_patterns=["atb", "а-т-б", "super market atb"],
                aliases={
                    # Молочные продукты (30% АТБ)
                    "смет": "Сметана",
                    "смет.": "Сметана",
                    "молок": "Молоко",
                    "молок.": "Молоко",
                    "сір": "Сир",
                    "сир": "Сир",
                    "масло": "Масло",
                    "яйц": "Яйця",
                    "йогурт": "Йогурт",
                    
                    # Хлеб
                    "хліб": "Хліб",
                    "батон": "Батон",
                    
                    # Напитки
                    "вода": "Вода",
                    "сік": "Сік",
                    "чай": "Чай",
                    "каф": "Кава",
                    
                    # Остальное
                    "серв": "Серветки",
                    "туал": "Туалетний папір",
                    "порош": "Порошок",
                    "гель": "Гель",
                },
                ignore_patterns=[
                    r"(?i)АКЦІЯ",
                    r"(?i)БОНУС",
                    r"(?i)РСВ",
                    r"(?i)СКИДКА",
                ],
                common_categories=[
                    "Продукти", "Овочі та фрукти", "Гігієна та догляд",
                    "Побутова хімія", "Цигарки", "Вода",
                ],
                notes="Супермаркет, много молочки. Часто используются 4-буквенные сокращения.",
            ),
            
            # ===== СІЛЬПО =====
            "silpo": MerchantProfile(
                merchant_id="silpo",
                display_name="Сільпо",
                header_patterns=["silpo", "сільпо", "метро silpo"],
                aliases={
                    "смет": "Сметана",
                    "молок": "Молоко",
                    "сір": "Сир",
                    "крас": "Краса",  # Типично для Сільпо
                    "косм": "Косметика",
                    "хліб": "Хліб",
                    "батон": "Батон",
                    "вода": "Вода",
                    "сік": "Сік",
                },
                ignore_patterns=[
                    r"(?i)LOYOLA",
                    r"(?i)КАРТКА",
                    r"(?i)ПРЯМУ",
                ],
                common_categories=[
                    "Продукти", "Гігієна та догляд", "Косметика",
                    "Овочі та фрукти", "Цигарки",
                ],
                notes="Супермаркет премиум-класса. Много косметики и красоты.",
            ),
            
            # ===== АЗС / WOG =====
            "wog": MerchantProfile(
                merchant_id="wog",
                display_name="WOG (АЗС)",
                header_patterns=["wog", "газ-азс", "a95", "a92"],
                aliases={
                    # Пальне - ГЛАВНАЯ категория WOG
                    "a95": "А95",
                    "a92": "А92",
                    "a-95": "А95",
                    "a-92": "А92",
                    "дизель": "Дизель",
                    "бензин": "Бензин",
                    "lpg": "LPG",
                    
                    # Закуски
                    "каф": "Кава",
                    "капуч": "Капучино",
                    "чай": "Чай",
                    "сендвіч": "Сендвіч",
                    "чіпс": "Чіпси",
                    "печинка": "Печиво",
                },
                ignore_patterns=[
                    r"(?i)LOYALTY",
                    r"(?i)CARD",
                    r"(?i)ПРОМО",
                ],
                common_categories=[
                    "Пальне", "Кафе та ресторани", "Фастфуд і снеки",
                    "Напої", "Солодкі напої",
                ],
                notes="АЗС. ГЛАВНОЕ - опознавать пальне (A95, A92, дизель).",
            ),
            
            # ===== ОККО (АЗС) =====
            "okko": MerchantProfile(
                merchant_id="okko",
                display_name="OKKO (АЗС)",
                header_patterns=["okko", "окко", "gas station okko"],
                aliases={
                    "a95": "А95",
                    "a92": "А92",
                    "дизель": "Дизель",
                    "пальне": "Пальне",
                    "каф": "Кава",
                    "чай": "Чай",
                    "сендвіч": "Сендвіч",
                },
                ignore_patterns=[
                    r"(?i)OKKO CARD",
                    r"(?i)BONUS",
                ],
                common_categories=[
                    "Пальне", "Напої", "Кафе та ресторани",
                ],
                notes="АЗС. Пальне + кафе.",
            ),
            
            # ===== NOVUS / AUCHAN =====
            "novus": MerchantProfile(
                merchant_id="novus",
                display_name="NOVUS",
                header_patterns=["novus", "новус"],
                aliases={
                    "смет": "Сметана",
                    "молок": "Молоко",
                    "сір": "Сир",
                    "хліб": "Хліб",
                    "овочеві": "Овочі",
                    "фрукти": "Фрукти",
                },
                ignore_patterns=[
                    r"(?i)NOVUS CARD",
                    r"(?i)BONUSPLUS",
                ],
                common_categories=[
                    "Продукти", "Овочі та фрукти", "Гігієна та догляд",
                ],
                notes="Супермаркет. Много овощей и фруктов.",
            ),
            
            # ===== АПТЕКИ =====
            "pharmacy": MerchantProfile(
                merchant_id="pharmacy",
                display_name="Аптека",
                header_patterns=["аптека", "pharmacy", "ліки"],
                aliases={
                    "таблет": "Таблетки",
                    "капсул": "Капсули",
                    "мазь": "Мазь",
                    "сироп": "Сироп",
                    "вітамін": "Вітаміни",
                },
                ignore_patterns=[
                    r"(?i)BONUS",
                ],
                common_categories=[
                    "Аптека", "Гігієна та догляд",
                ],
                notes="Аптека. Все должно быть лекарствами или гигиеной.",
            ),
        }
    
    def detect_merchant(self, merchant_name: str) -> Optional[MerchantProfile]:
        """
        Детектировать профиль по названию магазина.
        
        Returns:
            MerchantProfile или None
        """
        if not merchant_name:
            return None
        
        merchant_lower = merchant_name.lower().strip()
        
        for profile in self.profiles.values():
            for pattern in profile.header_patterns:
                if pattern.lower() in merchant_lower:
                    return profile
        
        return None
    
    def get_profile(self, merchant_id: str) -> Optional[MerchantProfile]:
        """Получить профиль по ID."""
        return self.profiles.get(merchant_id.lower())
    
    def add_profile(self, profile: MerchantProfile):
        """Добавить новый профиль (для расширения)."""
        self.profiles[profile.merchant_id.lower()] = profile
        return profile
    
    def list_merchants(self) -> List[str]:
        """Получить список всех доступных магазинов."""
        return [p.display_name for p in self.profiles.values()]


# Глобальный экземпляр
merchant_registry = MerchantRegistry()
