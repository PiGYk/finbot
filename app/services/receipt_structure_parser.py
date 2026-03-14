"""
Детермінований парсер структури касового чека.
Класифікує лінійки без спроб вгадування назв товарів.

Логіка:
1. Витягти сирі лінійки
2. Класифікувати кожну (merchant, item, price, total, barcode, service, ad, unknown)
3. Знайти товарні лінійки, service lines, totals, бароди
4. Не вгадувати, просто структурно розібрати

Це дає фундамент для нормалізації (Phase 3).
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple


class LineType(Enum):
    """Тип лінійки у чеку."""
    MERCHANT_HEADER = "merchant_header"  # Назва магазину/закладу
    ITEM_LINE = "item_line"  # Товарна позиція
    PRICE_LINE = "price_line"  # Рядок з ціною (може бути окремий)
    TOTAL_LINE = "total_line"  # Загальна сума
    SUBTOTAL_LINE = "subtotal_line"  # Проміжна сума
    TAX_LINE = "tax_line"  # Податок/ПДВ
    BARCODE_LINE = "barcode_line"  # ШК/EAN рядок
    SERVICE_LINE = "service_line"  # Касса, дата, реквізити, адреса
    AD_LINE = "ad_line"  # Реклама, бонусна програма
    PAYMENT_INFO = "payment_info"  # Спосіб оплати
    DISCOUNT_LINE = "discount_line"  # Знижка
    UNKNOWN = "unknown"  # Не класифікується


@dataclass
class ReceiptLine:
    """Класифікована лінійка чека."""
    raw_text: str
    line_index: int
    line_type: LineType
    confidence: float  # 0.0-1.0 впевненість класифікації
    
    # Додаткові поля для специфічних типів
    amount: Optional[float] = None  # Для PRICE_LINE, TOTAL_LINE, TAX_LINE
    barcode: Optional[str] = None  # Для BARCODE_LINE


@dataclass
class StructuredReceipt:
    """Результат парсингу структури чека."""
    merchant_name: Optional[str]
    receipt_lines: List[ReceiptLine]
    item_line_indices: List[int]  # Індекси товарних лінійок
    total_line_index: Optional[int]  # Індекс лінійки з загальною сумою
    total_amount: Optional[float]  # Загальна сума
    currency: str
    warnings: List[str]  # Про які проблеми парсер попереджає


class ReceiptStructureParser:
    """
    Детермінований парсер структури чека.
    Не використовує LLM, тільки regex + heuristics.
    """
    
    # Патерни для識別ку service/ad/total рядків
    TOTAL_PATTERNS = [
        r'(?i)\bВСЬОГО\b',
        r'(?i)\bITOGO\b',
        r'(?i)\bTotal\b',
        r'(?i)\bДО\s+СПЛАТИ\b',
        r'(?i)\bСУМА\b',
        r'(?i)\bSUMAA\b',
        r'(?i)\bСУМА\s+ДО\s+СПЛАТИ',
    ]
    
    SUBTOTAL_PATTERNS = [
        r'(?i)\bПРОМІЖНА\b',
        r'(?i)\bSUBTOTAL\b',
        r'(?i)\bПІДСУМОК\b',
    ]
    
    TAX_PATTERNS = [
        r'(?i)\bПДВ\b',
        r'(?i)\bТАК\s+(\d+%)',
        r'(?i)\bVAT\b',
        r'(?i)\bТАX\b',
    ]
    
    SERVICE_LINE_PATTERNS = [
        (r'(?i)^ШК\s', "barcode_prefix"),
        (r'(?i)^ПН\s', "receipt_number"),
        (r'(?i)^Каса', "cashier"),
        (r'(?i)^КАССА', "cashier"),
        (r'(?i)^Чек\s*#', "receipt_id"),
        (r'(?i)^ПІДСУМОК', "subtotal"),
        (r'(?i)^АКЦІЯ', "action"),
        (r'(?i)^\d{2}[./-]\d{2}[./-]\d{4}', "date"),
        (r'(?i)^\d{2}:\d{2}:\d{2}', "time"),
        (r'(?i)^тел\.?', "phone"),
        (r'(?i)^адрес', "address"),
        (r'(?i)^www\.', "website"),
        (r'(?i)^Спасибо', "thanks"),
        (r'(?i)^Дякуємо', "thanks_uk"),
        (r'(?i)^БОНУС', "loyalty"),
        (r'(?i)^Собственник', "owner"),
        (r'(?i)^ДІЄ', "work_hours"),
        (r'(?i)^ДОПОМІЖНИЙ', "info"),
    ]
    
    BARCODE_PATTERNS = [
        (r'^\d{12,15}$', "ean_or_upce"),
        (r'^ШК\s*(\d{12,15})', "barcode_with_prefix"),
    ]
    
    def parse_raw_text(self, raw_text: str) -> StructuredReceipt:
        """
        Парсити сирий текст чека (список лінійок).
        
        Args:
            raw_text: Сирий текст від OCR (рядки розділені \n)
        
        Returns:
            StructuredReceipt з класифікованими лінійками
        """
        lines = raw_text.strip().split('\n')
        return self.parse_lines(lines)
    
    def parse_lines(self, raw_lines: List[str]) -> StructuredReceipt:
        """
        Парсити список лінійок.
        
        Args:
            raw_lines: Список сирих лінійок чека
        
        Returns:
            StructuredReceipt з класифікованими лінійками
        """
        classified_lines = []
        item_indices = []
        total_idx = None
        total_amount = None
        warnings = []
        
        # Перший крок: класифікувати кожну лінійку
        for idx, raw_line in enumerate(raw_lines):
            if not raw_line.strip():
                continue  # Пропустити пусті рядки
            
            line_type, confidence, extra_data = self._classify_line(raw_line, idx, raw_lines)
            
            receipt_line = ReceiptLine(
                raw_text=raw_line.strip(),
                line_index=idx,
                line_type=line_type,
                confidence=confidence,
                amount=extra_data.get('amount'),
                barcode=extra_data.get('barcode'),
            )
            
            classified_lines.append(receipt_line)
            
            # Збирати індекси товарних лінійок
            if line_type == LineType.ITEM_LINE:
                item_indices.append(len(classified_lines) - 1)
            
            # Знайти лінійку з загальною сумою
            if line_type == LineType.TOTAL_LINE:
                total_idx = len(classified_lines) - 1
                if extra_data.get('amount'):
                    total_amount = extra_data['amount']
        
        # Другий крок: знайти merchant
        merchant = self._detect_merchant(classified_lines)
        
        # Третій крок: валідація
        if not item_indices:
            warnings.append("Не знайдено товарних лінійок")
        
        if total_amount is None and total_idx is not None:
            warnings.append("Знайдена лінійка totals, але не видна сума")
        
        return StructuredReceipt(
            merchant_name=merchant,
            receipt_lines=classified_lines,
            item_line_indices=item_indices,
            total_line_index=total_idx,
            total_amount=total_amount,
            currency="UAH",
            warnings=warnings,
        )
    
    def _classify_line(
        self,
        line: str,
        idx: int,
        all_lines: List[str]
    ) -> Tuple[LineType, float, dict]:
        """
        Класифікувати одну лінійку.
        
        Returns:
            (line_type, confidence, extra_data)
        """
        stripped = line.strip()
        
        # Перевірка 1: Пусте?
        if not stripped:
            return LineType.UNKNOWN, 0.0, {}
        
        # Перевірка 2: Дуже коротка лінійка (імовірно service)
        if len(stripped) <= 3:
            return LineType.UNKNOWN, 0.3, {}
        
        # Перевірка 3: Barcode line?
        barcode_result = self._try_match_barcode(stripped)
        if barcode_result:
            barcode, confidence = barcode_result
            return LineType.BARCODE_LINE, confidence, {'barcode': barcode}
        
        # Перевірка 4: Service/Ad line?
        service_result = self._try_match_service_line(stripped)
        if service_result:
            line_type, confidence = service_result
            return line_type, confidence, {}
        
        # Перевірка 5: Total line?
        total_result = self._try_match_total_line(stripped)
        if total_result:
            amount, confidence = total_result
            return LineType.TOTAL_LINE, confidence, {'amount': amount}
        
        # Перевірка 6: Subtotal line?
        subtotal_result = self._try_match_subtotal_line(stripped)
        if subtotal_result:
            amount, confidence = subtotal_result
            return LineType.SUBTOTAL_LINE, confidence, {'amount': amount}
        
        # Перевірка 7: Tax line?
        if self._try_match_tax_line(stripped):
            return LineType.TAX_LINE, 0.85, {}
        
        # Перевірка 8: Дефолт — можливо товар
        # Низька впевненість, бо це може бути що завгодно
        return LineType.ITEM_LINE, 0.5, {}
    
    def _try_match_barcode(self, line: str) -> Optional[Tuple[str, float]]:
        """Спроба знайти barcode."""
        for pattern, barcode_type in self.BARCODE_PATTERNS:
            match = re.search(pattern, line)
            if match:
                barcode = match.group(1) if match.groups() else match.group(0)
                return barcode, 0.99  # Дуже висока впевненість для barcodes
        return None
    
    def _try_match_service_line(self, line: str) -> Optional[Tuple[LineType, float]]:
        """Спроба розпізнати service/ad line."""
        for pattern, service_type in self.SERVICE_LINE_PATTERNS:
            if re.search(pattern, line):
                # Service lines ~ дуже надійні
                return LineType.SERVICE_LINE, 0.95
        return None
    
    def _try_match_total_line(self, line: str) -> Optional[Tuple[float, float]]:
        """Спроба знайти лінійку з загальною сумою."""
        for pattern in self.TOTAL_PATTERNS:
            if re.search(pattern, line):
                # Спроба витягти суму з цього рядка або наступного
                amount = self._extract_amount(line)
                if amount is not None:
                    return amount, 0.95
                return None, 0.7  # Знайдене слово "Всього", але ціни нема
        return None
    
    def _try_match_subtotal_line(self, line: str) -> Optional[Tuple[float, float]]:
        """Спроба знайти промежуточну суму."""
        for pattern in self.SUBTOTAL_PATTERNS:
            if re.search(pattern, line):
                amount = self._extract_amount(line)
                if amount is not None:
                    return amount, 0.90
                return None, 0.6
        return None
    
    def _try_match_tax_line(self, line: str) -> bool:
        """Спроба розпізнати рядок податку/ПДВ."""
        for pattern in self.TAX_PATTERNS:
            if re.search(pattern, line):
                return True
        return False
    
    def _extract_amount(self, line: str) -> Optional[float]:
        """Витягти числову суму з рядка."""
        # Шукати числа, можливо з грн, UAH, ₴, або просто цифри
        matches = re.findall(
            r'(\d+[.,]\d{2}|\d+)',
            line.replace(',', '.')
        )
        
        if not matches:
            return None
        
        # Взяти останнє число (зазвичай це сума)
        try:
            last_number = float(matches[-1])
            return round(last_number, 2) if last_number > 0 else None
        except ValueError:
            return None
    
    def _detect_merchant(self, classified_lines: List[ReceiptLine]) -> Optional[str]:
        """
        Знайти назву магазину.
        Зазвичай це перший рядок або в headері.
        """
        # Спроба 1: Перші кілька рядків (найвірогідніше в headері)
        for line in classified_lines[:10]:
            if line.line_type in [LineType.MERCHANT_HEADER, LineType.ITEM_LINE]:
                text = line.raw_text.strip()
                # Merchant зазвичай:
                # - 5-30 символів
                # - великі літери
                # - не містить цифр/спеціальних символів
                if 5 <= len(text) <= 40 and text.isupper():
                    return text
        
        # Спроба 2: Простий fallback
        if classified_lines:
            return classified_lines[0].raw_text.strip()
        
        return None


def extract_raw_lines_from_vision(vision_response: dict) -> List[str]:
    """
    Витягти сирі лінійки з відповіді Vision API.
    (Це для майбутнього використання, коли Vision будетіти повертати raw_lines)
    """
    # Спочатку Vision повертає items (структуровано)
    # Пізніше можна додати raw_lines extraction
    items = vision_response.get("items", [])
    
    # На дан час лінійки добудовуються з items
    raw_lines = []
    for item in items:
        if item.get("raw_name"):
            raw_lines.append(item["raw_name"])
    
    return raw_lines
