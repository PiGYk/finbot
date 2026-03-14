"""
Receipt pipeline observability.
Логування кожного етапу обробки чека для дебагу.
"""

import json
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger("finstack")


class ReceiptPipelineLogger:
    """
    Логування обробки чека на кожному етапі.
    Допомагає дебагити та розуміти, як система обробляє чек.
    """
    
    def __init__(self, chat_id: int, debug_mode: bool = False):
        self.chat_id = chat_id
        self.debug_mode = debug_mode
        self.stage = "init"
    
    def log_ocr_input(self, image_size: int, media_type: str, provider: str):
        """Логувати вхід до OCR/Vision API."""
        self.stage = "ocr_input"
        if self.debug_mode:
            logger.debug(
                f"[Receipt #{self.chat_id}] OCR Input: {provider} | "
                f"size={image_size} bytes | type={media_type}"
            )
    
    def log_ocr_raw_output(self, raw_json: Dict[str, Any]):
        """Логувати сиру відповідь від OCR/Vision API."""
        self.stage = "ocr_raw"
        if self.debug_mode:
            # Скоротити для логу
            summary = {
                "merchant": raw_json.get("merchant", "?"),
                "items_count": len(raw_json.get("items", [])),
                "total": raw_json.get("receipt_total"),
            }
            logger.debug(f"[Receipt #{self.chat_id}] OCR Raw Output: {json.dumps(summary, ensure_ascii=False)}")
    
    def log_item_raw_name(self, idx: int, raw_name: str):
        """Логувати сирий рядок товара (перед нормалізацією)."""
        if self.debug_mode:
            logger.debug(f"[Receipt #{self.chat_id}] Item #{idx} raw: {raw_name}")
    
    def log_normalization_attempt(
        self,
        item_idx: int,
        raw_name: str,
        attempt_type: str,
        result: Optional[str],
        confidence: float
    ):
        """
        Логувати спробу нормалізації.
        attempt_type: exact, dict, memory, fuzzy, llm, unresolved
        """
        if self.debug_mode:
            logger.debug(
                f"[Receipt #{self.chat_id}] Item #{item_idx} Norm ({attempt_type}): "
                f"'{raw_name}' → '{result}' (conf={confidence:.2f})"
            )
    
    def log_categorization_step(
        self,
        item_idx: int,
        name: str,
        assigned_category: str,
        category_confidence: float,
        source: str
    ):
        """Логувати крок категоризації."""
        if self.debug_mode:
            logger.debug(
                f"[Receipt #{self.chat_id}] Item #{item_idx} Category ({source}): "
                f"'{name}' → '{assigned_category}' (conf={category_confidence:.2f})"
            )
    
    def log_suspect_item(self, item_idx: int, raw_name: str, reason: str, confidence: float):
        """Помітити позицію як сумнівну."""
        logger.warning(
            f"[Receipt #{self.chat_id}] Item #{item_idx} SUSPECT: "
            f"'{raw_name}' (reason={reason}, confidence={confidence:.2f})"
        )
    
    def log_structure_parse(
        self,
        merchant: Optional[str],
        item_count: int,
        total: Optional[float],
        warnings: List[str]
    ):
        """Логувати результат парсингу структури чека."""
        if self.debug_mode:
            logger.debug(
                f"[Receipt #{self.chat_id}] Structure: merchant='{merchant}' | "
                f"items={item_count} | total={total} | warnings={len(warnings)}"
            )
    
    def log_user_correction(
        self,
        item_idx: int,
        raw_name: str,
        corrected_name: Optional[str],
        corrected_category: Optional[str]
    ):
        """Логувати виправлення користувачем."""
        logger.info(
            f"[Receipt #{self.chat_id}] User Correction #{item_idx}: "
            f"name='{corrected_name}' | category='{corrected_category}'"
        )
    
    def log_memory_hit(self, merchant: str, raw_name: str, normalized_name: str, hit_type: str):
        """Логувати попадання в пам'ять."""
        if self.debug_mode:
            logger.debug(
                f"[Receipt #{self.chat_id}] Memory Hit ({hit_type}): "
                f"'{raw_name}' → '{normalized_name}' (merchant={merchant})"
            )
    
    def log_stage_complete(self, stage_name: str, duration_ms: int = 0):
        """Логувати завершення етапу."""
        self.stage = stage_name
        if self.debug_mode and duration_ms:
            logger.debug(f"[Receipt #{self.chat_id}] {stage_name} completed in {duration_ms}ms")
