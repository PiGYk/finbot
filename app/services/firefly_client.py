import json
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx


def guess_asset_account_role(name: str) -> str:
    normalized = name.strip().lower()

    cash_names = {
        "готівка",
        "наличка",
        "cash",
        "wallet",
        "гаманець",
        "кошелек",
    }

    if normalized in cash_names:
        return "cashWalletAsset"

    return "defaultAsset"


def parse_float(value: Any, fallback: float = 0.0) -> float:
    try:
        if value is None:
            return fallback
        return float(str(value).replace(",", "."))
    except (ValueError, TypeError):
        return fallback


class FireflyClient:
    def __init__(self, base_url: str, access_token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token

    async def request(
        self,
        method: str,
        path: str,
        json_payload: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> dict:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        url = f"{self.base_url}{path}"

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.request(
                method,
                url,
                headers=headers,
                json=json_payload,
                params=params,
            )

            if response.status_code >= 400:
                raise Exception(f"Firefly {response.status_code}: {response.text}")

            if not response.text.strip():
                return {}

            return response.json()

    async def list_asset_accounts(self) -> List[dict]:
        data = await self.request(
            "GET",
            "/api/v1/accounts",
            params={"type": "asset", "limit": 200},
        )
        return data.get("data", [])

    async def list_asset_account_names(self) -> List[str]:
        items = await self.list_asset_accounts()
        names: List[str] = []

        for item in items:
            attrs = item.get("attributes", {})
            name = attrs.get("name")
            if name:
                names.append(name)

        return names

    async def find_asset_account_by_name(self, name: str) -> Optional[dict]:
        items = await self.list_asset_accounts()
        for item in items:
            attrs = item.get("attributes", {})
            if attrs.get("name") == name:
                return item
        return None

    def extract_current_balance(self, account_item: dict) -> float:
        attrs = account_item.get("attributes", {})

        candidate_keys = [
            "current_balance",
            "current_balance_sum",
            "current_balance_native",
            "current_balance_sum_native",
            "opening_balance",
        ]

        for key in candidate_keys:
            if key in attrs and attrs.get(key) not in (None, ""):
                return parse_float(attrs.get(key), 0.0)

        return 0.0

    async def create_asset_account(
        self,
        name: str,
        currency_code: str,
        opening_balance: float = 0.0,
    ) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        account_role = guess_asset_account_role(name)

        payload = {
            "name": name,
            "type": "asset",
            "account_role": account_role,
            "opening_balance": str(round(opening_balance, 2)),
            "opening_balance_date": today,
            "currency_code": currency_code,
            "active": True,
        }

        print("CREATE_ASSET_ACCOUNT_PAYLOAD =", json.dumps(payload, ensure_ascii=False))
        return await self.request("POST", "/api/v1/accounts", json_payload=payload)

    async def ensure_source_asset_account(self, name: str, currency_code: str) -> dict:
        found = await self.find_asset_account_by_name(name)
        if found:
            return found
        return await self.create_asset_account(name, currency_code, opening_balance=0.0)

    async def find_category_by_name(self, name: str) -> Optional[dict]:
        data = await self.request("GET", "/api/v1/categories", params={"limit": 200})
        for item in data.get("data", []):
            attrs = item.get("attributes", {})
            if attrs.get("name") == name:
                return item
        return None

    async def create_category(self, name: str) -> Optional[dict]:
        payload = {"name": name}
        print("CREATE_CATEGORY_PAYLOAD =", json.dumps(payload, ensure_ascii=False))
        return await self.request("POST", "/api/v1/categories", json_payload=payload)

    async def ensure_category(self, name: str) -> None:
        found = await self.find_category_by_name(name)
        if found:
            return
        await self.create_category(name)

    async def create_transaction(
        self,
        parsed: Dict[str, Any],
        date_override: Optional[str] = None,
    ) -> dict:
        tx_type = parsed["type"]
        amount = parsed["amount"]
        category = parsed["category"]
        description = parsed["description"]
        currency = parsed["currency"]
        source_account = parsed["source_account"]

        await self.ensure_source_asset_account(source_account, currency)
        await self.ensure_category(category)

        tx_date = date_override or datetime.now().strftime("%Y-%m-%d")

        if tx_type == "expense":
            payload = {
                "error_if_duplicate_hash": False,
                "apply_rules": True,
                "fire_webhooks": True,
                "group_title": description,
                "transactions": [
                    {
                        "type": "withdrawal",
                        "date": tx_date,
                        "amount": str(amount),
                        "description": description,
                        "source_name": source_account,
                        "destination_name": category,
                        "currency_code": currency,
                        "category_name": category,
                    }
                ],
            }
        elif tx_type == "income":
            payload = {
                "error_if_duplicate_hash": False,
                "apply_rules": True,
                "fire_webhooks": True,
                "group_title": description,
                "transactions": [
                    {
                        "type": "deposit",
                        "date": tx_date,
                        "amount": str(amount),
                        "description": description,
                        "source_name": category,
                        "destination_name": source_account,
                        "currency_code": currency,
                        "category_name": category,
                    }
                ],
            }
        else:
            raise ValueError(f"Непідтримуваний type: {tx_type}")

        print("FIREFLY_PAYLOAD =", json.dumps(payload, ensure_ascii=False))
        result = await self.request("POST", "/api/v1/transactions", json_payload=payload)
        print("FIREFLY_RESULT =", json.dumps(result, ensure_ascii=False))
        return result

    async def create_transfer(
        self,
        parsed: Dict[str, Any],
        date_override: Optional[str] = None,
    ) -> dict:
        amount = parsed["amount"]
        currency = parsed["currency"]
        source_account = parsed["source_account"]
        destination_account = parsed["destination_account"]
        description = parsed["description"]

        source = await self.find_asset_account_by_name(source_account)
        if not source:
            raise ValueError(f"Не знайшов рахунок-відправник: {source_account}")

        destination = await self.find_asset_account_by_name(destination_account)
        if not destination:
            raise ValueError(f"Не знайшов рахунок-отримувач: {destination_account}")

        if source_account == destination_account:
            raise ValueError("Рахунок-відправник і рахунок-отримувач однакові")

        tx_date = date_override or datetime.now().strftime("%Y-%m-%d")

        payload = {
            "error_if_duplicate_hash": False,
            "apply_rules": True,
            "fire_webhooks": True,
            "group_title": description,
            "transactions": [
                {
                    "type": "transfer",
                    "date": tx_date,
                    "amount": str(amount),
                    "description": description,
                    "source_name": source_account,
                    "destination_name": destination_account,
                    "currency_code": currency,
                }
            ],
        }

        print("FIREFLY_TRANSFER_PAYLOAD =", json.dumps(payload, ensure_ascii=False))
        result = await self.request("POST", "/api/v1/transactions", json_payload=payload)
        print("FIREFLY_TRANSFER_RESULT =", json.dumps(result, ensure_ascii=False))
        return result

    async def create_receipt_transactions(
        self,
        receipt: Dict[str, Any],
        default_source_account: str,
        default_currency: str,
    ) -> dict:
        source_account = receipt.get("source_account") or default_source_account
        currency = receipt.get("currency") or default_currency
        merchant = receipt.get("merchant") or "Чек"
        groups = receipt.get("category_totals") or []
        receipt_date = receipt.get("receipt_date") or datetime.now().strftime("%Y-%m-%d")

        if not groups:
            raise ValueError("У чеку не знайдено категоризованих позицій")

        await self.ensure_source_asset_account(source_account, currency)

        transactions = []
        created_groups = []

        for group in groups:
            category = str(group.get("category") or "Інше").strip() or "Інше"
            amount = round(float(group.get("amount", 0)), 2)
            if amount <= 0:
                continue

            await self.ensure_category(category)

            transactions.append(
                {
                    "type": "withdrawal",
                    "date": receipt_date,
                    "amount": str(amount),
                    "description": f"{merchant} • {category}",
                    "source_name": source_account,
                    "destination_name": category,
                    "currency_code": currency,
                    "category_name": category,
                }
            )
            created_groups.append({"category": category, "amount": amount})

        if not transactions:
            raise ValueError("Після фільтрації в чеку не залишилося валідних категорій")

        payload = {
            "error_if_duplicate_hash": False,
            "apply_rules": True,
            "fire_webhooks": True,
            "group_title": f"Чек: {merchant}",
            "transactions": transactions,
        }

        print("FIREFLY_RECEIPT_PAYLOAD =", json.dumps(payload, ensure_ascii=False))
        result = await self.request("POST", "/api/v1/transactions", json_payload=payload)
        print("FIREFLY_RECEIPT_RESULT =", json.dumps(result, ensure_ascii=False))

        return {
            "created_count": len(transactions),
            "groups": created_groups,
            "result": result,
        }

    async def setup_balances(self, accounts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        for item in accounts:
            name = item["name"]
            desired_balance = round(float(item["balance"]), 2)
            currency = item["currency"]

            existing = await self.find_asset_account_by_name(name)

            if existing is None:
                created = await self.create_asset_account(
                    name=name,
                    currency_code=currency,
                    opening_balance=desired_balance,
                )
                results.append(
                    {
                        "account": name,
                        "action": "created_with_opening_balance",
                        "target_balance": desired_balance,
                        "currency": currency,
                        "result": created,
                    }
                )
                continue

            current_balance = round(self.extract_current_balance(existing), 2)
            delta = round(desired_balance - current_balance, 2)

            if abs(delta) < 0.01:
                results.append(
                    {
                        "account": name,
                        "action": "no_change",
                        "current_balance": current_balance,
                        "target_balance": desired_balance,
                        "currency": currency,
                    }
                )
                continue

            tx_date = datetime.now().strftime("%Y-%m-%d")

            if delta > 0:
                payload = {
                    "error_if_duplicate_hash": False,
                    "apply_rules": True,
                    "fire_webhooks": True,
                    "group_title": f"Корекція балансу {name}",
                    "transactions": [
                        {
                            "type": "deposit",
                            "date": tx_date,
                            "amount": str(abs(delta)),
                            "description": f"Корекція балансу {name}",
                            "source_name": "Balance correction",
                            "destination_name": name,
                            "currency_code": currency,
                            "category_name": "Корекція балансу",
                        }
                    ],
                }
            else:
                payload = {
                    "error_if_duplicate_hash": False,
                    "apply_rules": True,
                    "fire_webhooks": True,
                    "group_title": f"Корекція балансу {name}",
                    "transactions": [
                        {
                            "type": "withdrawal",
                            "date": tx_date,
                            "amount": str(abs(delta)),
                            "description": f"Корекція балансу {name}",
                            "source_name": name,
                            "destination_name": "Balance correction",
                            "currency_code": currency,
                            "category_name": "Корекція балансу",
                        }
                    ],
                }

            print("BALANCE_ADJUSTMENT_PAYLOAD =", json.dumps(payload, ensure_ascii=False))
            tx_result = await self.request("POST", "/api/v1/transactions", json_payload=payload)

            results.append(
                {
                    "account": name,
                    "action": "adjusted",
                    "current_balance": current_balance,
                    "target_balance": desired_balance,
                    "delta": delta,
                    "currency": currency,
                    "result": tx_result,
                }
            )

        return results

    async def list_recent_transaction_groups(self, limit: int = 50) -> List[dict]:
        data = await self.request(
            "GET",
            "/api/v1/transactions",
            params={"page": 1, "limit": limit},
        )
        items = data.get("data", [])

        items.sort(key=lambda x: int(x.get("id", 0)), reverse=True)
        return items

    async def get_last_transaction_group(self) -> dict:
        items = await self.list_recent_transaction_groups(limit=50)
        if not items:
            raise ValueError("У Firefly немає транзакцій")
        return items[0]

    async def delete_transaction_group(self, transaction_id: str) -> None:
        print(f"DELETE_TRANSACTION_GROUP_ID = {transaction_id}")
        await self.request("DELETE", f"/api/v1/transactions/{transaction_id}")

    def _extract_split_rows(self, group_item: dict) -> List[dict]:
        attrs = group_item.get("attributes", {})
        group_title = attrs.get("group_title")

        rows: List[dict] = []

        if isinstance(attrs.get("transactions"), list):
            for tx in attrs["transactions"]:
                row = dict(tx)
                if group_title and not row.get("description"):
                    row["description"] = group_title
                rows.append(row)
            return rows

        row = dict(attrs)
        if group_title and not row.get("description"):
            row["description"] = group_title
        rows.append(row)
        return rows

    def _get_group_splits(self, group_item: dict) -> List[dict]:
        attrs = group_item.get("attributes", {})
        splits = attrs.get("transactions") or []

        result: List[dict] = []
        for split in splits:
            row = dict(split)
            if attrs.get("group_title") and not row.get("description"):
                row["description"] = attrs["group_title"]
            result.append(row)

        return result

    async def apply_last_transaction_action(
        self,
        action_spec: Dict[str, Any],
        default_currency: str,
        default_source_account: str,
    ) -> dict:
        last_group = await self.get_last_transaction_group()

        group_id = str(last_group.get("id"))
        attrs = last_group.get("attributes", {})
        splits = self._get_group_splits(last_group)

        if not splits:
            raise ValueError("Не знайшов спліт останньої транзакції")

        first = splits[0]

        tx_type = str(first.get("type", "")).lower()
        old_amount = abs(parse_float(first.get("amount"), 0.0))
        old_currency = first.get("currency_code") or default_currency
        old_description = first.get("description") or attrs.get("group_title") or "Операція"

        old_source_account = first.get("source_name")
        old_destination_account = first.get("destination_name")
        old_category = first.get("category_name") or old_destination_account or "Інше"

        print("LAST_GROUP_ID =", group_id)
        print("LAST_GROUP_TITLE =", attrs.get("group_title"))
        print("LAST_GROUP_FIRST_SPLIT =", json.dumps(first, ensure_ascii=False))

        if action_spec["action"] == "delete":
            await self.delete_transaction_group(group_id)

            return {
                "action": "deleted",
                "old_type": tx_type,
                "old_amount": old_amount,
                "currency": old_currency,
                "old_description": old_description,
                "old_source_account": old_source_account,
                "old_destination_account": old_destination_account,
                "old_category": old_category,
            }

        if len(splits) != 1:
            raise ValueError(
                "Остання транзакція має кілька частин. Її можна видалити, але редагування цієї версії поки що тільки для одиночних транзакцій."
            )

        tx_date = str(first.get("date") or datetime.now().strftime("%Y-%m-%d"))[:10]

        if tx_type == "withdrawal":
            parsed = {
                "type": "expense",
                "amount": action_spec["amount"] if action_spec["amount"] is not None else old_amount,
                "currency": action_spec.get("currency") or old_currency,
                "category": action_spec["category"] or old_category,
                "description": action_spec["description"] or old_description,
                "source_account": action_spec["source_account"] or old_source_account or default_source_account,
            }
            new_result = await self.create_transaction(parsed, date_override=tx_date)
            new_source_account = parsed["source_account"]
            new_destination_account = parsed["category"]
            new_category = parsed["category"]
            new_amount = parsed["amount"]
            new_description = parsed["description"]

        elif tx_type == "deposit":
            parsed = {
                "type": "income",
                "amount": action_spec["amount"] if action_spec["amount"] is not None else old_amount,
                "currency": action_spec.get("currency") or old_currency,
                "category": action_spec["category"] or first.get("category_name") or first.get("source_name") or "Інше",
                "description": action_spec["description"] or old_description,
                "source_account": action_spec["destination_account"] or action_spec["source_account"] or first.get("destination_name") or default_source_account,
            }
            new_result = await self.create_transaction(parsed, date_override=tx_date)
            new_source_account = parsed["source_account"]
            new_destination_account = parsed["category"]
            new_category = parsed["category"]
            new_amount = parsed["amount"]
            new_description = parsed["description"]

        elif tx_type == "transfer":
            parsed = {
                "amount": action_spec["amount"] if action_spec["amount"] is not None else old_amount,
                "currency": action_spec.get("currency") or old_currency,
                "source_account": action_spec["source_account"] or old_source_account,
                "destination_account": action_spec["destination_account"] or old_destination_account,
                "description": action_spec["description"] or old_description,
            }
            new_result = await self.create_transfer(parsed, date_override=tx_date)
            new_source_account = parsed["source_account"]
            new_destination_account = parsed["destination_account"]
            new_category = None
            new_amount = parsed["amount"]
            new_description = parsed["description"]

        else:
            raise ValueError(f"Непідтримуваний тип останньої транзакції: {tx_type}")

        await self.delete_transaction_group(group_id)

        return {
            "action": "updated",
            "currency": old_currency,
            "old_type": tx_type,
            "old_amount": old_amount,
            "new_amount": new_amount,
            "old_description": old_description,
            "new_description": new_description,
            "old_source_account": old_source_account,
            "new_source_account": new_source_account,
            "old_destination_account": old_destination_account,
            "new_destination_account": new_destination_account,
            "old_category": old_category,
            "new_category": new_category,
            "result": new_result,
        }

    async def list_transaction_rows(self, limit_pages: int = 20) -> List[dict]:
        rows: List[dict] = []
        page = 1

        while True:
            data = await self.request(
                "GET",
                "/api/v1/transactions",
                params={"page": page, "limit": 100},
            )

            items = data.get("data", [])
            if not items:
                break

            for item in items:
                rows.extend(self._extract_split_rows(item))

            meta = data.get("meta", {})
            pagination = meta.get("pagination", {})

            total_pages = pagination.get("total_pages")
            current_page = pagination.get("current_page", page)

            if total_pages is not None:
                if current_page >= total_pages:
                    break
            else:
                if len(items) < 100:
                    break

            page += 1
            if page > limit_pages:
                break

        return rows
