import json
from datetime import date, datetime, timedelta
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
        data = await self.request("GET", "/api/v1/accounts", params={"type": "asset", "limit": 200})
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

    async def create_asset_account(self, name: str, currency_code: str, opening_balance: float = 0.0) -> dict:
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

    async def create_subscription(self, parsed: Dict[str, Any]) -> dict:
        payload = {
            "name": parsed["name"],
            "amount_min": parsed["amount"],
            "amount_max": parsed["amount"],
            "date": parsed["date"],
            "repeat_freq": parsed["repeat_freq"],
            "skip": int(parsed.get("skip", 0) or 0),
            "active": True,
            "currency_code": parsed["currency"],
        }

        if parsed.get("notes"):
            payload["notes"] = parsed["notes"]

        print("FIREFLY_SUBSCRIPTION_PAYLOAD =", json.dumps(payload, ensure_ascii=False))

        try:
            result = await self.request("POST", "/api/v1/bills", json_payload=payload)
        except Exception as first_error:
            error_text = str(first_error)
            if "404" not in error_text and "405" not in error_text:
                raise
            result = await self.request("POST", "/api/v1/subscriptions", json_payload=payload)

        print("FIREFLY_SUBSCRIPTION_RESULT =", json.dumps(result, ensure_ascii=False))
        return result

    async def _subscription_request(
        self,
        method: str,
        subscription_id: Optional[str] = None,
        json_payload: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> dict:
        suffix = f"/{subscription_id}" if subscription_id else ""
        last_error: Optional[Exception] = None
        for base_path in ("/api/v1/bills", "/api/v1/subscriptions"):
            try:
                return await self.request(method, f"{base_path}{suffix}", json_payload=json_payload, params=params)
            except Exception as e:
                error_text = str(e)
                if any(code in error_text for code in ("404", "405")):
                    last_error = e
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("Не вдалося виконати запит до підписок")

    def _normalize_subscription_item(self, item: dict) -> dict:
        attrs = item.get("attributes", {})
        raw_date = str(
            attrs.get("date")
            or attrs.get("next_expected_match")
            or attrs.get("pay_dates")
            or attrs.get("created_at")
            or datetime.now().strftime("%Y-%m-%d")
        )
        if len(raw_date) >= 10:
            raw_date = raw_date[:10]

        raw_repeat = str(attrs.get("repeat_freq") or attrs.get("repeat") or "monthly").lower().strip()
        repeat_freq = raw_repeat if raw_repeat in {"daily", "weekly", "monthly", "yearly"} else "monthly"

        return {
            "id": str(item.get("id") or ""),
            "name": str(attrs.get("name") or "Без назви").strip() or "Без назви",
            "amount": round(parse_float(attrs.get("amount_min") or attrs.get("amount") or attrs.get("amount_max"), 0.0), 2),
            "currency": str(attrs.get("currency_code") or attrs.get("currency_symbol") or "UAH").strip() or "UAH",
            "date": raw_date,
            "repeat_freq": repeat_freq,
            "skip": max(0, int(parse_float(attrs.get("skip"), 0))),
            "active": bool(attrs.get("active", True)),
            "notes": attrs.get("notes") or None,
            "raw": item,
        }

    async def list_subscriptions(self, active_only: bool = False) -> List[dict]:
        data = await self._subscription_request("GET", params={"limit": 200})
        items = [self._normalize_subscription_item(item) for item in data.get("data", [])]
        items.sort(key=lambda item: item.get("name", "").lower())
        if active_only:
            return [item for item in items if item.get("active", True)]
        return items

    async def find_subscription(self, query: str) -> Optional[dict]:
        query = str(query or "").strip()
        if not query:
            return None
        items = await self.list_subscriptions(active_only=False)
        for item in items:
            if item["id"] == query:
                return item
        query_low = query.lower()
        exact = [item for item in items if item["name"].lower() == query_low]
        if exact:
            return exact[0]
        partial = [item for item in items if query_low in item["name"].lower()]
        if len(partial) == 1:
            return partial[0]
        return None

    async def update_subscription(self, subscription_id: str, updates: Dict[str, Any]) -> dict:
        current = await self._subscription_request("GET", subscription_id=subscription_id)
        current_item = self._normalize_subscription_item((current.get("data") or {}))
        payload = {
            "name": updates.get("name", current_item["name"]),
            "amount_min": updates.get("amount", current_item["amount"]),
            "amount_max": updates.get("amount", current_item["amount"]),
            "date": updates.get("date", current_item["date"]),
            "repeat_freq": updates.get("repeat_freq", current_item["repeat_freq"]),
            "skip": int(updates.get("skip", current_item.get("skip", 0)) or 0),
            "active": current_item.get("active", True) if updates.get("active") is None else bool(updates.get("active")),
            "currency_code": updates.get("currency", current_item["currency"]),
            "notes": updates.get("notes", current_item.get("notes")),
        }
        print("FIREFLY_UPDATE_SUBSCRIPTION_PAYLOAD =", json.dumps(payload, ensure_ascii=False))
        try:
            result = await self._subscription_request("PUT", subscription_id=subscription_id, json_payload=payload)
        except Exception as e:
            if "405" not in str(e):
                raise
            result = await self._subscription_request("PATCH", subscription_id=subscription_id, json_payload=payload)
        print("FIREFLY_UPDATE_SUBSCRIPTION_RESULT =", json.dumps(result, ensure_ascii=False))
        return result

    async def delete_subscription(self, subscription_id: str) -> None:
        print("FIREFLY_DELETE_SUBSCRIPTION_ID =", subscription_id)
        await self._subscription_request("DELETE", subscription_id=subscription_id)

    def _calculate_next_subscription_date(self, start_date_raw: str, repeat_freq: str, skip: int = 0) -> Optional[str]:
        try:
            start_date = datetime.strptime(str(start_date_raw)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

        today = date.today()
        candidate = start_date
        step = max(1, int(skip or 0) + 1)
        safety = 0

        def add_months(d: date, months: int) -> date:
            year = d.year + ((d.month - 1 + months) // 12)
            month = ((d.month - 1 + months) % 12) + 1
            day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
            return date(year, month, day)

        while candidate < today and safety < 1000:
            if repeat_freq == "daily":
                candidate += timedelta(days=step)
            elif repeat_freq == "weekly":
                candidate += timedelta(weeks=step)
            elif repeat_freq == "monthly":
                candidate = add_months(candidate, step)
            elif repeat_freq == "yearly":
                candidate = add_months(candidate, 12 * step)
            else:
                return None
            safety += 1

        return candidate.isoformat()

    async def list_due_subscriptions(self, days_ahead: int = 2) -> List[dict]:
        result: List[dict] = []
        today = date.today()
        horizon = today + timedelta(days=max(0, int(days_ahead)))
        for item in await self.list_subscriptions(active_only=True):
            next_date_raw = self._calculate_next_subscription_date(item.get("date", ""), item.get("repeat_freq", "monthly"), item.get("skip", 0))
            if not next_date_raw:
                continue
            next_date = datetime.strptime(next_date_raw, "%Y-%m-%d").date()
            if today <= next_date <= horizon:
                enriched = dict(item)
                enriched["next_date"] = next_date.isoformat()
                enriched["days_left"] = (next_date - today).days
                result.append(enriched)
        result.sort(key=lambda item: (item.get("next_date", ""), item.get("name", "")))
        return result


    async def create_transaction(self, parsed: Dict[str, Any], date_override: Optional[str] = None) -> dict:
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

    async def create_transfer(self, parsed: Dict[str, Any], date_override: Optional[str] = None) -> dict:
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

    async def create_receipt_transactions(self, receipt: Dict[str, Any], default_source_account: str, default_currency: str) -> dict:
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
        data = await self.request("GET", "/api/v1/transactions", params={"page": 1, "limit": limit})
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

    def _split_label(self, split: dict, index: int) -> str:
        category = split.get("category_name") or split.get("destination_name") or "Без категорії"
        description = split.get("description") or "Без опису"
        amount = abs(parse_float(split.get("amount"), 0.0))
        return f"{index + 1}. {category} | {description} | {amount:.2f}"

    def _list_split_labels(self, splits: List[dict]) -> str:
        return "; ".join(self._split_label(split, idx) for idx, split in enumerate(splits))

    def _find_target_split_index(self, action_spec: Dict[str, Any], splits: List[dict]) -> Optional[int]:
        target_index = action_spec.get("target_index")
        target_category = (action_spec.get("target_category") or "").strip().lower()
        target_description = (action_spec.get("target_description") or "").strip().lower()

        if target_index is not None:
            idx = target_index - 1
            if 0 <= idx < len(splits):
                return idx
            raise ValueError(f"Частини №{target_index} не існує. Доступні частини: {self._list_split_labels(splits)}")

        candidates = list(range(len(splits)))

        if target_category:
            filtered = []
            for i in candidates:
                split = splits[i]
                category = str(split.get("category_name") or split.get("destination_name") or "").strip().lower()
                if target_category in category:
                    filtered.append(i)
            candidates = filtered

        if target_description:
            filtered = []
            for i in candidates:
                split = splits[i]
                description = str(split.get("description") or "").strip().lower()
                if target_description in description:
                    filtered.append(i)
            candidates = filtered

        if target_category or target_description:
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) == 0:
                raise ValueError(
                    f"Не знайшов потрібну частину в останній транзакції. Доступні частини: {self._list_split_labels(splits)}"
                )
            raise ValueError(
                f"Знайшов кілька схожих частин. Уточни категорію або номер частини. Доступні частини: {self._list_split_labels(splits)}"
            )

        if len(splits) == 1:
            return 0

        return None

    def _build_split_payload(self, split: dict) -> dict:
        tx_type = str(split.get("type", "")).lower()
        payload = {
            "type": tx_type,
            "date": str(split.get("date") or datetime.now().strftime("%Y-%m-%d"))[:10],
            "amount": str(abs(parse_float(split.get("amount"), 0.0))),
            "description": split.get("description") or "Операція",
            "source_name": split.get("source_name"),
            "destination_name": split.get("destination_name"),
            "currency_code": split.get("currency_code"),
        }

        if tx_type in {"withdrawal", "deposit"}:
            category_name = split.get("category_name")
            if category_name:
                payload["category_name"] = category_name

        return payload

    async def _recreate_group(self, group_title: str, splits: List[dict]) -> dict:
        payload = {
            "error_if_duplicate_hash": False,
            "apply_rules": True,
            "fire_webhooks": True,
            "group_title": group_title,
            "transactions": [self._build_split_payload(split) for split in splits],
        }

        print("FIREFLY_RECREATE_GROUP_PAYLOAD =", json.dumps(payload, ensure_ascii=False))
        result = await self.request("POST", "/api/v1/transactions", json_payload=payload)
        print("FIREFLY_RECREATE_GROUP_RESULT =", json.dumps(result, ensure_ascii=False))
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
        group_title = attrs.get("group_title") or "Операція"
        splits = self._get_group_splits(last_group)

        if not splits:
            raise ValueError("Не знайшов спліт останньої транзакції")

        target_idx = self._find_target_split_index(action_spec, splits)

        if action_spec["action"] == "delete":
            if target_idx is None:
                first = splits[0]
                old_amount = abs(parse_float(first.get("amount"), 0.0))
                old_currency = first.get("currency_code") or default_currency
                old_description = first.get("description") or group_title

                await self.delete_transaction_group(group_id)
                return {
                    "action": "deleted",
                    "old_type": str(first.get("type", "")).lower(),
                    "old_amount": old_amount,
                    "currency": old_currency,
                    "old_description": old_description,
                }

            target_split = dict(splits[target_idx])
            remaining_splits = [dict(s) for i, s in enumerate(splits) if i != target_idx]

            old_amount = abs(parse_float(target_split.get("amount"), 0.0))
            old_currency = target_split.get("currency_code") or default_currency
            old_description = target_split.get("description") or group_title

            if not remaining_splits:
                await self.delete_transaction_group(group_id)
            else:
                await self._recreate_group(group_title, remaining_splits)
                await self.delete_transaction_group(group_id)

            return {
                "action": "deleted_split",
                "target_label": self._split_label(target_split, target_idx),
                "old_amount": old_amount,
                "currency": old_currency,
                "old_description": old_description,
            }

        if target_idx is None and len(splits) > 1:
            raise ValueError(
                f"Остання транзакція має кілька частин. Уточни, що саме міняти: {self._list_split_labels(splits)}"
            )

        target_idx = 0 if target_idx is None else target_idx
        target_split = dict(splits[target_idx])

        tx_type = str(target_split.get("type", "")).lower()
        old_amount = abs(parse_float(target_split.get("amount"), 0.0))
        old_currency = target_split.get("currency_code") or default_currency
        old_description = target_split.get("description") or group_title
        old_source_account = target_split.get("source_name")
        old_destination_account = target_split.get("destination_name")
        old_category = target_split.get("category_name") or old_destination_account or "Інше"

        new_split = dict(target_split)

        if action_spec["amount"] is not None:
            new_split["amount"] = str(action_spec["amount"])

        if action_spec["description"]:
            new_split["description"] = action_spec["description"]

        if tx_type == "withdrawal":
            if action_spec["source_account"]:
                new_split["source_name"] = action_spec["source_account"]

            new_category = action_spec["category"] or (action_spec["destination_account"] or None)
            if new_category:
                await self.ensure_category(new_category)
                new_split["destination_name"] = new_category
                new_split["category_name"] = new_category

        elif tx_type == "deposit":
            if action_spec["destination_account"] or action_spec["source_account"]:
                new_split["destination_name"] = action_spec["destination_account"] or action_spec["source_account"]

            if action_spec["category"]:
                await self.ensure_category(action_spec["category"])
                new_split["source_name"] = action_spec["category"]
                new_split["category_name"] = action_spec["category"]

        elif tx_type == "transfer":
            if action_spec["source_account"]:
                new_split["source_name"] = action_spec["source_account"]
            if action_spec["destination_account"]:
                new_split["destination_name"] = action_spec["destination_account"]

            if new_split.get("source_name") == new_split.get("destination_name"):
                raise ValueError("Рахунок-відправник і рахунок-отримувач однакові")
        else:
            raise ValueError(f"Непідтримуваний тип останньої транзакції: {tx_type}")

        new_splits = [dict(s) for s in splits]
        new_splits[target_idx] = new_split

        new_group_title = group_title
        if len(new_splits) == 1:
            new_group_title = new_split.get("description") or group_title

        recreate_result = await self._recreate_group(new_group_title, new_splits)
        await self.delete_transaction_group(group_id)

        new_amount = abs(parse_float(new_split.get("amount"), 0.0))
        new_description = new_split.get("description") or new_group_title
        new_source_account = new_split.get("source_name")
        new_destination_account = new_split.get("destination_name")
        new_category = new_split.get("category_name") or new_destination_account or "Інше"

        return {
            "action": "updated",
            "currency": old_currency,
            "target_label": self._split_label(target_split, target_idx),
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
            "result": recreate_result,
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
