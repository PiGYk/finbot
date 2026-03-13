import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from app.services.claude_parser import ClaudeParser
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


class AdvisorService:
    def __init__(self, firefly: FireflyClient, claude: ClaudeParser, default_currency: str) -> None:
        self.firefly = firefly
        self.claude = claude
        self.default_currency = default_currency

    def _detect_period(self, text: str) -> Tuple[date, date, str]:
        low = text.strip().lower()
        today = date.today()

        if "сьогодні" in low:
            return today, today, "сьогодні"

        if "цього тижня" in low:
            start = today - timedelta(days=today.weekday())
            return start, today, "цього тижня"

        if "за тиждень" in low or "останній тиждень" in low or "останні 7 днів" in low:
            start = today - timedelta(days=6)
            return start, today, "за останні 7 днів"

        if "цього місяця" in low:
            start = today.replace(day=1)
            return start, today, "цього місяця"

        # За замовчуванням беремо 30 днів для порад.
        start = today - timedelta(days=29)
        return start, today, "за останні 30 днів"

    async def _load_rows_for_period(self, start: date, end: date) -> List[dict]:
        rows = await self.firefly.list_transaction_rows(limit_pages=30)
        filtered: List[dict] = []

        for row in rows:
            row_date = parse_row_date(row)
            if row_date is None:
                continue
            if start <= row_date <= end:
                filtered.append(row)

        return filtered

    def _infer_merchant(self, row: dict) -> str:
        description = str(row.get("description") or "").strip()

        if " • " in description:
            return description.split(" • ", 1)[0].strip()

        if description:
            return description

        return "Невідомо"

    def _build_summary(self, rows: List[dict], period_label: str) -> dict:
        expense_rows = [r for r in rows if str(r.get("type", "")).lower() == "withdrawal"]
        income_rows = [r for r in rows if str(r.get("type", "")).lower() == "deposit"]

        total_expense = round(sum(parse_row_amount(r) for r in expense_rows), 2)
        total_income = round(sum(parse_row_amount(r) for r in income_rows), 2)

        by_category: Dict[str, float] = defaultdict(float)
        by_merchant: Dict[str, float] = defaultdict(float)
        by_source: Dict[str, float] = defaultdict(float)

        small_expense_count = 0
        small_expense_total = 0.0

        for row in expense_rows:
            amount = parse_row_amount(row)
            category = str(row.get("category_name") or row.get("destination_name") or "Без категорії").strip() or "Без категорії"
            merchant = self._infer_merchant(row)
            source_account = str(row.get("source_name") or "Невідомий рахунок").strip() or "Невідомий рахунок"

            by_category[category] += amount
            by_merchant[merchant] += amount
            by_source[source_account] += amount

            if amount <= 200:
                small_expense_count += 1
                small_expense_total += amount

        def top_items(source: Dict[str, float], limit: int = 7) -> List[dict]:
            return [
                {"name": name, "amount": round(amount, 2)}
                for name, amount in sorted(source.items(), key=lambda x: x[1], reverse=True)[:limit]
            ]

        summary = {
            "period_label": period_label,
            "currency": self.default_currency,
            "rows_count": len(rows),
            "expense_rows_count": len(expense_rows),
            "income_rows_count": len(income_rows),
            "total_expense": total_expense,
            "total_income": total_income,
            "net": round(total_income - total_expense, 2),
            "top_categories": top_items(by_category),
            "top_merchants": top_items(by_merchant),
            "top_source_accounts": top_items(by_source),
            "small_expenses": {
                "count": small_expense_count,
                "total": round(small_expense_total, 2),
            },
        }

        return summary

    async def answer_question(self, user_text: str) -> str:
        start, end, period_label = self._detect_period(user_text)
        rows = await self._load_rows_for_period(start, end)
        summary = self._build_summary(rows, period_label)
        context_json = json.dumps(summary, ensure_ascii=False, indent=2)
        return await self.claude.answer_finance_advice(user_text, context_json)
