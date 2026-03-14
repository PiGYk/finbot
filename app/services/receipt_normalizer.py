"""
Receipt Normalizer: нормалізація назв товарів через пам'ять і правила.

Послідовність спроб (Phase 3):
1. Exact memory match (raніше користувач виправив)
2. Barcode match (відомий штрихкод)
3. Dictionary/alias match (Смет → Сметана)
4. Merchant-specific rules (future Phase 6)
5. Fuzzy match (подібні рядки)
6. LLM fallback (Phase 5, обережне)

На Phase 3 реалізуємо 1-5.
"""

import logging
from typing import Dict, Optional

from app.services.receipt_memory import ReceiptMemory
from app.services.category_rules import CategoryRulesService
from app.services.merchant_profiles import merchant_registry  # ФАЗА 6

logger = logging.getLogger("finstack")


class ReceiptNormalizer:
    """
    Нормалізує назву товара через пам'ять, правила, словники.
    """
    
    # Простий словник скорочень (для Phase 3)
    # Буде розширено на Phase 6 з merchant-specific правилами
    ALIAS_DICTIONARY = {
        "смет": {"normalized_name": "Сметана", "confidence": 0.85},
        "смет.": {"normalized_name": "Сметана", "confidence": 0.85},
        "молок": {"normalized_name": "Молоко", "confidence": 0.85},
        "молок.": {"normalized_name": "Молоко", "confidence": 0.85},
        "сир": {"normalized_name": "Сир", "confidence": 0.80},
        "сір": {"normalized_name": "Сир", "confidence": 0.80},
        "крем": {"normalized_name": "Крем", "confidence": 0.70},
        "масло": {"normalized_name": "Масло вершкове", "confidence": 0.75},
        "хліб": {"normalized_name": "Хліб", "confidence": 0.85},
        "батон": {"normalized_name": "Батон", "confidence": 0.85},
        "яйц": {"normalized_name": "Яйця", "confidence": 0.80},
        "сокі": {"normalized_name": "Сік", "confidence": 0.80},
        "сік": {"normalized_name": "Сік", "confidence": 0.80},
        "каф": {"normalized_name": "Кава", "confidence": 0.80},
        "чай": {"normalized_name": "Чай", "confidence": 0.80},
        "воды": {"normalized_name": "Вода", "confidence": 0.75},
        "вода": {"normalized_name": "Вода", "confidence": 0.75},
        "пиво": {"normalized_name": "Пиво", "confidence": 0.85},
        "вино": {"normalized_name": "Вино", "confidence": 0.85},
        "серв": {"normalized_name": "Серветки", "confidence": 0.80},
        "туал": {"normalized_name": "Туалетний папір", "confidence": 0.80},
        "рушн": {"normalized_name": "Рушник", "confidence": 0.75},
        "шампун": {"normalized_name": "Шампунь", "confidence": 0.85},
        "мило": {"normalized_name": "Мило", "confidence": 0.85},
        "зубн": {"normalized_name": "Зубна паста", "confidence": 0.80},
        "паста": {"normalized_name": "Паста", "confidence": 0.70},
        "порош": {"normalized_name": "Порошок для прання", "confidence": 0.75},
        "гель": {"normalized_name": "Гель", "confidence": 0.70},
        "fairy": {"normalized_name": "Fairy (рідина для миття)", "confidence": 0.90},
        "яйч": {"normalized_name": "Яйця", "confidence": 0.80},
    }
    
    def __init__(
        self,
        memory: Optional[ReceiptMemory] = None,
        category_rules: Optional[CategoryRulesService] = None
    ):
        self.memory = memory or ReceiptMemory()
        self.category_rules = category_rules
    
    def normalize_item(
        self,
        raw_name: str,
        merchant: str,
        barcode: Optional[str] = None
    ) -> Dict:
        """
        Нормалізувати назву товара.
        
        Args:
            raw_name: Сирий рядок з чека
            merchant: Назва магазину
            barcode: Штрихкод якщо є
        
        Returns:
            {
                "normalized_name": str or None,
                "category": str,
                "normalization_status": str,
                "confidence": float,
                "source": str,
            }
        """
        
        # Крок 1: Exact memory match
        memory_entry = self.memory.lookup_exact(merchant, raw_name)
        if memory_entry:
            return {
                "normalized_name": memory_entry['normalized_name'],
                "category": memory_entry['category'],
                "normalization_status": "memory_match",
                "confidence": memory_entry['confidence'],
                "source": "memory",
            }
        
        # Крок 2: Barcode match
        if barcode:
            barcode_entry = self.memory.lookup_by_barcode(barcode)
            if barcode_entry:
                return {
                    "normalized_name": barcode_entry['normalized_name'],
                    "category": barcode_entry['category'],
                    "normalization_status": "barcode_match",
                    "confidence": 0.99,
                    "source": "barcode_memory",
                }
        
        # ФАЗА 6: Merchant-specific aliases (приоритет над глобальным словарем)
        merchant_profile = merchant_registry.detect_merchant(merchant)
        if merchant_profile:
            merchant_alias_result = self._try_merchant_aliases(raw_name, merchant_profile)
            if merchant_alias_result:
                return {
                    "normalized_name": merchant_alias_result['normalized_name'],
                    "category": merchant_alias_result.get('category', 'Продукти'),
                    "normalization_status": "merchant_alias",
                    "confidence": merchant_alias_result.get('confidence', 0.85),
                    "source": "merchant_profile",
                }
        
        # Крок 3: Dictionary/alias match (глобальный, приоритет ниже)
        alias_result = self._try_alias_dictionary(raw_name)
        if alias_result:
            return {
                "normalized_name": alias_result['normalized_name'],
                "category": "Продукти",  # Припущення для скорочень
                "normalization_status": "dictionary_match",
                "confidence": alias_result.get('confidence', 0.80),
                "source": "dictionary",
            }
        
        # Крок 4: Fuzzy match (обережно)
        fuzzy_result = self.memory.lookup_fuzzy(merchant, raw_name, threshold=0.75)
        if fuzzy_result:
            return {
                "normalized_name": fuzzy_result['normalized_name'],
                "category": fuzzy_result['category'],
                "normalization_status": "fuzzy_match",
                "confidence": 0.70,  # Нижче ніж exact
                "source": "fuzzy_memory",
            }
        
        # Крок 5: LLM fallback (Phase 5)
        # На Phase 3 цей крок не реалізований, просто повертаємо unresolved
        
        # Якщо нічого не підійшло
        return {
            "normalized_name": None,
            "category": "Інше",
            "normalization_status": "unresolved",
            "confidence": 0.0,
            "source": "unknown",
        }
    
    def _try_merchant_aliases(self, raw_name: str, merchant_profile) -> Optional[Dict]:
        """
        ФАЗА 6: Спроба знайти скорочення у merchant-specific словнику.
        
        Приоритет ВИЩА за глобальний словник.
        """
        raw_lower = raw_name.lower().strip()
        
        if not merchant_profile.aliases:
            return None
        
        # Точний match
        if raw_lower in merchant_profile.aliases:
            return {
                "normalized_name": merchant_profile.aliases[raw_lower],
                "confidence": 0.88,  # Трохи вище за глобальний (0.80)
            }
        
        # Точний match на канонічній формі
        raw_canonical = self._canonicalize(raw_lower)
        for abbr_key, resolved_name in merchant_profile.aliases.items():
            abbr_canonical = self._canonicalize(abbr_key)
            if abbr_canonical == raw_canonical:
                return {
                    "normalized_name": resolved_name,
                    "confidence": 0.88,
                }
        
        # Частинковий match (abbr в рядку)
        for abbr, resolved_name in merchant_profile.aliases.items():
            if abbr in raw_lower:
                return {
                    "normalized_name": resolved_name,
                    "confidence": 0.82,  # Трохи нижче за точный (0.88)
                }
        
        return None
    
    def _try_alias_dictionary(self, raw_name: str) -> Optional[Dict]:
        """
        Спроба знайти скорочення у глобальному словнику.
        """
        raw_lower = raw_name.lower().strip()
        
        # Точний match на словнику
        if raw_lower in self.ALIAS_DICTIONARY:
            return self.ALIAS_DICTIONARY[raw_lower]
        
        # Точний match на канонічній формі
        raw_canonical = self._canonicalize(raw_lower)
        for abbr_key, resolved in self.ALIAS_DICTIONARY.items():
            abbr_canonical = self._canonicalize(abbr_key)
            if abbr_canonical == raw_canonical:
                return resolved
        
        # Частинковий match (abbr в рядку)
        for abbr, resolved in self.ALIAS_DICTIONARY.items():
            if abbr in raw_lower:
                return resolved
        
        return None
    
    @staticmethod
    def _canonicalize(text: str) -> str:
        """Нормалізувати текст для пошуку."""
        canonical = text.lower().strip()
        canonical = ' '.join(canonical.split())  # Видалити подвійні пробіли
        return canonical


class ReceiptNormalizationResult:
    """Результат нормалізації для logging."""
    
    def __init__(self, **kwargs):
        self.normalized_name = kwargs.get('normalized_name')
        self.category = kwargs.get('category')
        self.status = kwargs.get('normalization_status')
        self.confidence = kwargs.get('confidence', 0.0)
        self.source = kwargs.get('source', 'unknown')
    
    def __str__(self):
        return (
            f"Norm({self.status}): '{self.normalized_name}' "
            f"({self.source}, conf={self.confidence:.2f})"
        )
