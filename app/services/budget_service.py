import json
import os
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

from app.services.firefly_client import FireflyClient


def parse_row_date(row: dict) -> Optional[date]:
    raw = row.get("date")
    if not raw:
        return None

    raw = str(raw)
    if len(raw) >= 10:
        raw = raw[:10]

    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_row_amount(row: dict) -> float:
    raw = row.get("amount", "0")
    try:
        return abs(float(str(raw).replace(",", ".")))
    except ValueError:
        return 0.0


class BudgetService:
    def __init__(
        self,
        firefly: FireflyClient,
        default_currency: str,
        file_path: str = "/app/data/budgets.json",
    ) -> None:
        self.firefly = firefly
        self.default_currency = default_currency
        self.file_path = file_path
        self._lock = Lock()
        self._loaded = False
        self._data: Dict[str, Any] = {"budgets": []}

    def _ensure_dir(self) -> None:
        folder = os.path.dirname(self.file_path)
        if folder:
            os.makedirs(folder, exist_ok=True)

    def _load(self) -> None:
        if self._loaded:
            return

        with self._lock:
            if self._loaded:
                return

            self._ensure_dir()
            if os.path.exists(self.file_path):
                try:
                    with open(self.file_path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict) and isinstance(loaded.get("budgets"), list):
                        self._data = loaded
                except Exception:
                    self._data = {"budgets": []}

            self._loaded = True

    def _save(self) -> None:
        self._ensure_dir()
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def count(self) -> int:
        self._load()
        return len(self._data.get("budgets", []))

    def should_auto_suggest_after_income(self, text: str, parsed: Dict[str, Any]) -> bool:
        if parsed.get("type") != "income":
            return False

        joined = f"{text} {parsed.get('category', '')} {parsed.get('description', '')}".lower()

        triggers = [
            "зарплата",
            "зп",
            "salary",
            "аванс",
            "премія",
            "bonus",
            "дохід",
        ]

        return any(trigger in joined for trigger in triggers)

    async def _recent_expense_distribution(self) -> Dict[str, float]:
        today = date.today()
        start = today - timedelta(days=29)

        rows = await self.firefly.list_transaction_rows(limit_pages=30)
        bucket: Dict[str, float] = defaultdict(float)

        for row in rows:
            row_date = parse_row_date(row)
            if row_date is None or not (start <= row_date <= today):
                continue

            if str(row.get("type", "")).lower() != "withdrawal":
                continue

            amount = parse_row_amount(row)
            if amount <= 0:
                continue

            category = str(row.get("category_name") or row.get("destination_name") or "Інше").strip() or "Інше"
            bucket[category] += amount

        return dict(bucket)

    def _fallback_allocations(self, amount: float) -> List[Dict[str, Any]]:
        savings = round(amount * 0.20, 2)
        essentials = round(amount * 0.40, 2)
        food = round(amount * 0.20, 2)
        flexible = round(amount * 0.10, 2)
        buffer_ = round(amount - savings - essentials - food - flexible, 2)

        return [
            {"category": "Заощадження", "amount": savings},
            {"category": "Обов’язкові витрати", "amount": essentials},
            {"category": "Продукти", "amount": food},
            {"category": "Вільні витрати", "amount": flexible},
            {"category": "Буфер", "amount": buffer_},
        ]

    def _normalize_allocations_sum(self, allocations: List[Dict[str, Any]], target_amount: float) -> List[Dict[str, Any]]:
        current = round(sum(item["amount"] for item in allocations), 2)
        delta = round(target_amount - current, 2)
        if allocations and abs(delta) >= 0.01:
            allocations[-1]["amount"] = round(allocations[-1]["amount"] + delta, 2)
        return allocations

    async def create_budget_plan(self, chat_id: int, amount: float, title: Optional[str] = None) -> Dict[str, Any]:
        self._load()

        amount = round(float(amount), 2)
        if amount <= 0:
            raise ValueError("Сума бюджету має бути більшою за 0")

        history = await self._recent_expense_distribution()

        if not history:
            allocations = self._fallback_allocations(amount)
            based_on_history = False
        else:
            based_on_history = True
            savings = round(amount * 0.20, 2)
            distributable = round(amount - savings, 2)

            top = sorted(history.items(), key=lambda x: x[1], reverse=True)[:4]
            hist_total = sum(v for _, v in top)

            allocations = [{"category": "Заощадження", "amount": savings}]

            if hist_total <= 0:
                allocations.extend(self._fallback_allocations(distributable))
            else:
                used = 0.0
                for category, value in top:
                    part = round(distributable * (value / hist_total), 2)
                    allocations.append({"category": category, "amount": part})
                    used += part

                remainder = round(amount - savings - used, 2)
                if remainder > 0:
                    allocations.append({"category": "Буфер", "amount": remainder})

            allocations = self._normalize_allocations_sum(allocations, amount)

        budget = {
            "id": int(time.time() * 1000),
            "chat_id": int(chat_id),
            "title": title or f"Бюджет {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "currency": self.default_currency,
            "amount": amount,
            "based_on_history": based_on_history,
            "allocations": allocations,
            "created_at": datetime.utcnow().isoformat() + "Z",
        }

        with self._lock:
            self._data.setdefault("budgets", []).append(budget)
            self._save()

        return budget

    def format_plan(self, budget: Dict[str, Any], intro: str = "Створив бюджет-план:") -> str:
        lines = [
            intro,
            f"{budget['title']}",
            f"Сума: {budget['amount']:.2f} {budget['currency']}",
        ]

        if budget.get("based_on_history"):
            lines.append("Орієнтувався на твої витрати за останні 30 днів.")
        else:
            lines.append("Історії витрат замало, тому використав базовий шаблон.")

        lines.append("")
        lines.append("Рекомендований розподіл:")

        for item in budget.get("allocations", []):
            lines.append(f"• {item['category']} — {item['amount']:.2f} {budget['currency']}")

        return "\n".join(lines)
