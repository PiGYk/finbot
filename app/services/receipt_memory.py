"""
Receipt Memory: локальна пам'ять про раніше виправлені товари.

Структура:
{
    "merchant": "АТБ",
    "raw_name_canonical": "смет престд",
    "normalized_name": "Сметана Президент",
    "category": "Продукти",
    "barcode": "1234567890123",
    "times_seen": 5,
    "times_confirmed": 5,
    "confidence": 0.95,
    "last_seen": "2026-03-14",
    "created_at": "2026-03-14",
}

Логіка:
1. Користувач виправляє товар → зберігаємо відповідність
2. Наступний чек з того ж магазину → точно розпізнаємо
3. Частота підтвердження впливає на впевненість
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger("finstack")


class ReceiptMemory:
    """
    Локальна пам'ять про товари з чеків.
    Зберігається у JSON для простоти.
    """
    
    def __init__(self, filepath: str = "/opt/finstack/data/bot/receipt_memory.json"):
        self.filepath = filepath
        self.memory: List[Dict] = []
        self._load()
    
    def _load(self):
        """Завантажити пам'ять з файлу."""
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    self.memory = json.load(f)
                logger.info(f"✓ Receipt memory loaded: {len(self.memory)} entries")
            except Exception as e:
                logger.warning(f"Failed to load receipt memory: {e}")
                self.memory = []
        else:
            logger.info("Receipt memory is new (file doesn't exist)")
            self.memory = []
    
    def _save(self):
        """Зберегти пам'ять у файл."""
        try:
            os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(self.memory, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save receipt memory: {e}")
    
    def _canonicalize(self, text: str) -> str:
        """
        Нормалізувати текст для пошуку.
        - lowercase
        - видалити пробіли
        - видалити подвійні символи
        """
        canonical = text.lower().strip()
        canonical = ' '.join(canonical.split())  # Видалити подвійні пробіли
        return canonical
    
    def lookup_exact(
        self,
        merchant: str,
        raw_name: str
    ) -> Optional[Dict]:
        """
        Точний пошук у пам'яті.
        
        Args:
            merchant: Назва магазину
            raw_name: Сирий рядок товара
        
        Returns:
            Entry з пам'яті або None
        """
        canonical = self._canonicalize(raw_name)
        merchant_lower = merchant.lower().strip()
        
        for entry in self.memory:
            if (entry.get('merchant', '').lower() == merchant_lower and
                entry.get('raw_name_canonical') == canonical):
                return entry
        
        return None
    
    def lookup_by_barcode(self, barcode: str) -> Optional[Dict]:
        """
        Пошук по штрихкоду.
        
        Args:
            barcode: Штрихкод/EAN
        
        Returns:
            Entry з пам'яті або None
        """
        if not barcode:
            return None
        
        for entry in self.memory:
            if entry.get('barcode') == barcode:
                return entry
        
        return None
    
    def lookup_fuzzy(
        self,
        merchant: str,
        raw_name: str,
        threshold: float = 0.75
    ) -> Optional[Dict]:
        """
        Нечіткий пошук (на базі Levenshtein distance).
        
        Args:
            merchant: Назва магазину
            raw_name: Сирий рядок товара
            threshold: Мінімальна схожість (0.0-1.0)
        
        Returns:
            Найсхожіший entry або None
        """
        from difflib import SequenceMatcher
        
        canonical = self._canonicalize(raw_name)
        merchant_lower = merchant.lower().strip()
        
        best_match = None
        best_score = 0.0
        
        for entry in self.memory:
            # Розглядати лише entries од того ж магазину
            if entry.get('merchant', '').lower() != merchant_lower:
                continue
            
            # Обчислити подібність
            entry_canonical = entry.get('raw_name_canonical', '')
            score = SequenceMatcher(None, canonical, entry_canonical).ratio()
            
            if score > best_score:
                best_score = score
                best_match = entry
        
        # Повернути лише якщо вище threshold
        if best_score >= threshold:
            return best_match
        
        return None
    
    def save_confirmation(
        self,
        merchant: str,
        raw_name: str,
        normalized_name: str,
        category: str,
        barcode: Optional[str] = None
    ):
        """
        Зберегти підтверджену користувачем відповідність.
        
        Args:
            merchant: Назва магазину
            raw_name: Сирий рядок з чека
            normalized_name: Людська назва (виправлена користувачем або від AI)
            category: Категорія витрат
            barcode: Штрихкод якщо є
        """
        canonical = self._canonicalize(raw_name)
        merchant_lower = merchant.lower().strip()
        
        # Пошукати існуючий entry
        existing_entry = None
        existing_idx = None
        
        for idx, entry in enumerate(self.memory):
            if (entry.get('merchant', '').lower() == merchant_lower and
                entry.get('raw_name_canonical') == canonical):
                existing_entry = entry
                existing_idx = idx
                break
        
        today = datetime.now().isoformat()[:10]
        
        if existing_entry:
            # Оновити
            existing_entry['normalized_name'] = normalized_name
            existing_entry['category'] = category
            existing_entry['times_seen'] += 1
            existing_entry['times_confirmed'] += 1
            
            # Обновити confidence на базі підтверджень
            # confidence = confirmed / seen, но cap на 0.99
            existing_entry['confidence'] = min(
                0.99,
                existing_entry['times_confirmed'] / existing_entry['times_seen']
            )
            existing_entry['last_seen'] = today
            
            logger.info(
                f"✓ Memory updated: '{raw_name}' → '{normalized_name}' "
                f"(merchant={merchant}, confidence={existing_entry['confidence']:.2f})"
            )
        else:
            # Створити новий entry
            new_entry = {
                'merchant': merchant,
                'raw_name': raw_name,
                'raw_name_canonical': canonical,
                'normalized_name': normalized_name,
                'category': category,
                'barcode': barcode,
                'times_seen': 1,
                'times_confirmed': 1,
                'confidence': 0.9,
                'last_seen': today,
                'created_at': today,
            }
            self.memory.append(new_entry)
            
            logger.info(
                f"✓ Memory created: '{raw_name}' → '{normalized_name}' "
                f"(merchant={merchant})"
            )
        
        # Зберегти на диск
        self._save()
    
    def get_stats(self) -> Dict:
        """Отримати статистику пам'яті."""
        merchants = {}
        for entry in self.memory:
            merchant = entry.get('merchant', 'Unknown')
            merchants[merchant] = merchants.get(merchant, 0) + 1
        
        return {
            'total_entries': len(self.memory),
            'merchants': merchants,
            'avg_confidence': (
                sum(e.get('confidence', 0) for e in self.memory) / len(self.memory)
                if self.memory else 0
            ),
        }
    
    def clear(self):
        """Очистити пам'ять (для дебагу)."""
        self.memory = []
        self._save()
        logger.warning("Receipt memory cleared!")
