"""
Receipt Review State Manager: управління станом режиму виправлення.

Тут зберігаємо:
- receipt_data (чек що редагуємо)
- suspect_items_indices (які товари сумнівні)
- current_index (який редагуємо зараз)
- corrections_made (які вже виправили)
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass


@dataclass
class ReviewState:
    """Стан режиму review."""
    
    receipt_data: Dict[str, Any]  # Весь чек
    suspect_items_indices: List[int]  # Індекси сумнівних товарів
    current_suspect_index: int  # Який по порядку редагуємо (0-based)
    corrections_made: Dict[int, Dict[str, str]]  # {item_idx: {"name": "...", "category": "..."}}
    mode: str  # "confirm" | "edit_name" | "edit_category"
    temp_input: Optional[str] = None  # Тимчасовий вхід користувача
    
    def current_suspect_number(self) -> int:
        """Який по порядку сумнівний товар редагуємо (1-based)."""
        return self.current_suspect_index + 1
    
    def total_suspects(self) -> int:
        """Всього сумнівних товарів."""
        return len(self.suspect_items_indices)
    
    def current_item_index(self) -> int:
        """Індекс поточного товара в списку всіх товарів."""
        if self.current_suspect_index < len(self.suspect_items_indices):
            return self.suspect_items_indices[self.current_suspect_index]
        return -1
    
    def current_item(self) -> Optional[Dict[str, Any]]:
        """Поточний товар."""
        idx = self.current_item_index()
        if idx >= 0 and idx < len(self.receipt_data.get('items', [])):
            return self.receipt_data['items'][idx]
        return None
    
    def next_suspect(self) -> bool:
        """Перейти до наступного сумнівного товара. Повертає True якщо є ще."""
        self.current_suspect_index += 1
        return self.current_suspect_index < len(self.suspect_items_indices)
    
    def apply_correction(self, item_index: int, new_name: Optional[str] = None, new_category: Optional[str] = None):
        """Застосувати виправлення до товара."""
        correction = {}
        
        if new_name is not None:
            correction['name'] = new_name
        if new_category is not None:
            correction['category'] = new_category
        
        if correction:
            self.corrections_made[item_index] = correction
            
            # Одразу оновити receipt_data
            if item_index < len(self.receipt_data.get('items', [])):
                item = self.receipt_data['items'][item_index]
                if new_name is not None:
                    item['name'] = new_name
                    item['normalized_name'] = new_name
                if new_category is not None:
                    item['category'] = new_category
    
    def get_corrections_list(self) -> List[Dict]:
        """Отримати список усіх виправлень для логування."""
        result = []
        for item_idx, corrections in self.corrections_made.items():
            if item_idx < len(self.receipt_data.get('items', [])):
                item = self.receipt_data['items'][item_idx]
                result.append({
                    'item_index': item_idx,
                    'raw_name': item.get('raw_name'),
                    'new_name': corrections.get('name'),
                    'new_category': corrections.get('category'),
                })
        return result


class ReceiptReviewManager:
    """Управління режимом review для всіх користувачів."""
    
    def __init__(self):
        # {chat_id: ReviewState}
        self.states: Dict[int, ReviewState] = {}
    
    def start_review(self, chat_id: int, receipt_data: Dict[str, Any]) -> Optional[ReviewState]:
        """
        Розпочати режим review для чека.
        
        Returns:
            ReviewState або None якщо немає сумнівних товарів
        """
        items = receipt_data.get('items', [])
        suspect_indices = [
            idx for idx, item in enumerate(items)
            if item.get('is_suspect', False)
        ]
        
        if not suspect_indices:
            return None  # Немає сумнівних
        
        state = ReviewState(
            receipt_data=receipt_data,
            suspect_items_indices=suspect_indices,
            current_suspect_index=0,
            corrections_made={},
            mode="confirm",
        )
        
        self.states[chat_id] = state
        return state
    
    def get_state(self, chat_id: int) -> Optional[ReviewState]:
        """Отримати стан користувача."""
        return self.states.get(chat_id)
    
    def set_mode(self, chat_id: int, mode: str):
        """Змінити режим (confirm, edit_name, edit_category)."""
        state = self.get_state(chat_id)
        if state:
            state.mode = mode
    
    def set_temp_input(self, chat_id: int, input_text: str):
        """Встановити тимчасовий вхід (для назви чи категорії)."""
        state = self.get_state(chat_id)
        if state:
            state.temp_input = input_text
    
    def apply_current_correction(self, chat_id: int, new_name: Optional[str] = None, new_category: Optional[str] = None):
        """Застосувати виправлення до поточного товара."""
        state = self.get_state(chat_id)
        if state:
            item_idx = state.current_item_index()
            if item_idx >= 0:
                state.apply_correction(item_idx, new_name, new_category)
    
    def end_review(self, chat_id: int) -> Optional[Dict[str, Any]]:
        """
        Завершити review і повернути оновлений чек.
        
        Returns:
            receipt_data з виправленнями або None
        """
        state = self.states.pop(chat_id, None)
        if state:
            return state.receipt_data
        return None
    
    def get_corrections_summary(self, chat_id: int) -> List[Dict]:
        """Отримати список виправлень для логування."""
        state = self.get_state(chat_id)
        if state:
            return state.get_corrections_list()
        return []


# Глобальний instance
review_manager = ReceiptReviewManager()
