from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

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


def fmt_money(amount: float, currency: str) -> str:
    return f"{amount:,.2f} {currency}".replace(",", " ")


class ReportService:
    def __init__(self, firefly: FireflyClient, default_currency: str) -> None:
        self.firefly = firefly
        self.default_currency = default_currency

    def detect_report_request(self, text: str) -> Optional[dict]:
        low = text.strip().lower()

        report_words = [
            "скільки",
            "витратив",
            "витрати",
            "категорії",
            "категорія",
            "топ",
            "найбільші",
            "доход",
            "дохід",
            "заробив",
            "заробіток",
        ]

        if not any(word in low for word in report_words):
            return None

        if "категор" in low or "топ" in low or "найбільш" in low:
            kind = "top_categories"
        elif "дохід" in low or "доход" in low or "зароб" in low:
            kind = "income_total"
        else:
            kind = "expense_total"

        period = "today"
        if "сьогодні" in low:
            period = "today"
        elif "цього тижня" in low:
            period = "this_week"
        elif "за тиждень" in low or "останній тиждень" in low:
            period = "last_7_days"
        elif "цього місяця" in low:
            period = "this_month"
        elif "за місяць" in low or "останній місяць" in low:
            period = "last_30_days"
        elif kind == "top_categories":
            period = "last_30_days"

        return {"kind": kind, "period": period}

    def get_period_range(self, period: str) -> Tuple[date, date, str]:
        today = date.today()

        if period == "today":
            return today, today, "сьогодні"

        if period == "this_week":
            start = today - timedelta(days=today.weekday())
            return start, today, "цього тижня"

        if period == "last_7_days":
            start = today - timedelta(days=6)
            return start, today, "за останні 7 днів"

        if period == "this_month":
            start = today.replace(day=1)
            return start, today, "цього місяця"

        if period == "last_30_days":
            start = today - timedelta(days=29)
            return start, today, "за останні 30 днів"

        return today, today, "сьогодні"

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

    async def handle_report_request(self, text: str) -> Optional[str]:
        spec = self.detect_report_request(text)
        if not spec:
            return None

        start, end, label = self.get_period_range(spec["period"])
        rows = await self._load_rows_for_period(start, end)

        expense_rows = [r for r in rows if str(r.get("type", "")).lower() == "withdrawal"]
        income_rows = [r for r in rows if str(r.get("type", "")).lower() == "deposit"]

        if spec["kind"] == "expense_total":
            total = sum(parse_row_amount(r) for r in expense_rows)
            return f"Ти витратив {label}: {fmt_money(total, self.default_currency)}"

        if spec["kind"] == "income_total":
            total = sum(parse_row_amount(r) for r in income_rows)
            return f"Твій дохід {label}: {fmt_money(total, self.default_currency)}"

        if spec["kind"] == "top_categories":
            bucket: Dict[str, float] = {}

            for row in expense_rows:
                category = row.get("category_name") or row.get("destination_name") or "Без категорії"
                category = str(category).strip() or "Без категорії"
                bucket[category] = bucket.get(category, 0.0) + parse_row_amount(row)

            if not bucket:
                return f"За {label} ще немає витрат по категоріях."

            top = sorted(bucket.items(), key=lambda x: x[1], reverse=True)[:5]
            total = sum(v for _, v in top)

            lines = [f"Найбільші категорії витрат {label}:"]
            for idx, (name, amount) in enumerate(top, start=1):
                part = f"{idx}. {name} — {fmt_money(amount, self.default_currency)}"
                if total > 0:
                    percent = (amount / total) * 100
                    part += f" ({percent:.1f}%)"
                lines.append(part)

            return "\n".join(lines)

        return None
