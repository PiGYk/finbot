from dataclasses import dataclass
from typing import Dict

from app.services.advisor import AdvisorService
from app.services.budget_service import BudgetService
from app.services.category_rules import CategoryRulesService
from app.services.claude_parser import ClaudeParser
from app.services.firefly_client import FireflyClient
from app.services.receipt_parser import ReceiptParser
from app.services.reminder_service import ReminderService
from app.services.reports import ReportService
from app.services.profile_service import ProfileService


@dataclass
class ProfileRuntime:
    profile_id: str
    title: str
    default_currency: str
    default_source_account: str
    firefly: FireflyClient
    claude: ClaudeParser
    reports: ReportService
    advisor: AdvisorService
    category_rules: CategoryRulesService
    receipt_parser: ReceiptParser
    reminder_service: ReminderService
    budget_service: BudgetService


class ProfileRuntimeFactory:
    def __init__(
        self,
        profile_service: ProfileService,
        claude_api_key: str,
        claude_model: str,
        timezone_name: str,
        reminder_poll_seconds: int,
        data_root: str = "/app/data/bot",
    ) -> None:
        self.profile_service = profile_service
        self.claude_api_key = claude_api_key
        self.claude_model = claude_model
        self.timezone_name = timezone_name
        self.reminder_poll_seconds = reminder_poll_seconds
        self.data_root = data_root
        self._cache: Dict[str, ProfileRuntime] = {}

    def get(self, profile_id: str) -> ProfileRuntime:
        cached = self._cache.get(profile_id)
        if cached:
            return cached

        profile = self.profile_service.get_profile(profile_id)
        if not profile:
            raise ValueError(f"Профіль не знайдено: {profile_id}")

        default_currency = profile.get("default_currency", "UAH")
        default_source_account = profile.get("default_source_account", "Готівка")

        firefly = FireflyClient(
            base_url=profile["firefly_base_url"],
            access_token=profile["firefly_access_token"],
        )

        claude = ClaudeParser(
            api_key=self.claude_api_key,
            model=self.claude_model,
            default_currency=default_currency,
            default_source_account=default_source_account,
        )

        reports = ReportService(
            firefly=firefly,
            default_currency=default_currency,
        )

        advisor = AdvisorService(
            firefly=firefly,
            claude=claude,
            default_currency=default_currency,
        )

        category_rules = CategoryRulesService(
            file_path=f"{self.data_root}/category_rules_{profile_id}.json"
        )

        receipt_parser = ReceiptParser(
            api_key=self.claude_api_key,
            model=self.claude_model,
            default_currency=default_currency,
            category_rules=category_rules,
        )

        reminder_service = ReminderService(
            file_path=f"{self.data_root}/reminders_{profile_id}.json",
            timezone_name=self.timezone_name,
            poll_seconds=self.reminder_poll_seconds,
        )

        budget_service = BudgetService(
            firefly=firefly,
            default_currency=default_currency,
            file_path=f"{self.data_root}/budgets_{profile_id}.json",
        )

        runtime = ProfileRuntime(
            profile_id=profile_id,
            title=profile.get("title", profile_id),
            default_currency=default_currency,
            default_source_account=default_source_account,
            firefly=firefly,
            claude=claude,
            reports=reports,
            advisor=advisor,
            category_rules=category_rules,
            receipt_parser=receipt_parser,
            reminder_service=reminder_service,
            budget_service=budget_service,
        )

        self._cache[profile_id] = runtime
        return runtime
