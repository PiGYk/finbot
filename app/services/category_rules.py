import json
import os
import re
from difflib import SequenceMatcher
from threading import Lock
from typing import Any, Dict, List, Optional


def _pretty_title(text: str) -> str:
    text = str(text).strip()
    if not text:
        return text

    special_map = {
        "coca cola": "Coca Cola",
        "coca-cola": "Coca Cola",
        "pepsi": "Pepsi",
        "fanta": "Fanta",
        "sprite": "Sprite",
        "monster energy": "Monster Energy",
        "red bull": "Red Bull",
        "iqos": "IQOS",
        "heets": "HEETS",
        "terea": "TEREA",
    }

    low = text.lower()
    if low in special_map:
        return special_map[low]

    words = re.split(r"\s+", text)
    return " ".join(w[:1].upper() + w[1:].lower() if w else "" for w in words).strip()


class CategoryRulesService:
    def __init__(self, file_path: str = "/app/data/category_rules.json") -> None:
        self.file_path = file_path
        self._lock = Lock()
        self._data: Dict[str, Any] = {"rules": []}
        self._loaded = False

    def _ensure_dir(self) -> None:
        folder = os.path.dirname(self.file_path)
        if folder:
            os.makedirs(folder, exist_ok=True)

    def _normalize(self, text: str) -> str:
        text = str(text or "").lower().strip()
        text = text.replace("ё", "е")
        text = text.replace("’", "'")
        text = re.sub(r"[^a-zа-яіїєґ0-9\s\-]", " ", text)
        text = text.replace("-", " ")
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _compact(self, text: str) -> str:
        return self._normalize(text).replace(" ", "")

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
                    if isinstance(loaded, dict) and isinstance(loaded.get("rules"), list):
                        self._data = loaded
                except Exception:
                    self._data = {"rules": []}

            self._loaded = True

    def _save(self) -> None:
        self._ensure_dir()
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def list_rules(self) -> List[Dict[str, Any]]:
        self._load()
        return list(self._data.get("rules", []))

    def upsert_rule(self, canonical_name: str, aliases: List[str]) -> Dict[str, Any]:
        self._load()

        canonical_name = _pretty_title(canonical_name)
        normalized_canonical = self._normalize(canonical_name)

        clean_aliases: List[str] = []
        seen = set()

        for raw in [canonical_name, *aliases]:
            alias = str(raw or "").strip()
            if not alias:
                continue
            norm = self._normalize(alias)
            if not norm:
                continue
            if norm in seen:
                continue
            seen.add(norm)
            clean_aliases.append(alias)

        if not clean_aliases:
            clean_aliases = [canonical_name]

        with self._lock:
            rules = self._data.setdefault("rules", [])

            existing = None
            for rule in rules:
                if self._normalize(rule.get("canonical_name", "")) == normalized_canonical:
                    existing = rule
                    break

            if existing is None:
                existing = {
                    "canonical_name": canonical_name,
                    "aliases": [],
                }
                rules.append(existing)

            existing["canonical_name"] = canonical_name

            merged = []
            merged_seen = set()
            for raw in [*existing.get("aliases", []), *clean_aliases]:
                alias = str(raw or "").strip()
                norm = self._normalize(alias)
                if not norm or norm in merged_seen:
                    continue
                merged_seen.add(norm)
                merged.append(alias)

            existing["aliases"] = merged
            self._save()
            return existing

    def _best_match_score(self, text: str, alias: str) -> float:
        text_norm = self._normalize(text)
        alias_norm = self._normalize(alias)

        if not text_norm or not alias_norm:
            return 0.0

        if alias_norm == text_norm:
            return 1.0

        if alias_norm in text_norm:
            return 0.98

        text_compact = self._compact(text)
        alias_compact = self._compact(alias)

        if alias_compact and alias_compact in text_compact:
            return 0.97

        text_tokens = text_norm.split()
        alias_tokens = alias_norm.split()

        if not text_tokens or not alias_tokens:
            return 0.0

        alias_joined = " ".join(alias_tokens)
        best = 0.0

        if len(alias_tokens) == 1:
            for token in text_tokens:
                best = max(best, SequenceMatcher(None, token, alias_joined).ratio())
        else:
            window = len(alias_tokens)
            for i in range(0, max(len(text_tokens) - window + 1, 1)):
                candidate = " ".join(text_tokens[i:i + window])
                best = max(best, SequenceMatcher(None, candidate, alias_joined).ratio())

        return best

    def resolve_category(self, text: str, fallback: Optional[str] = None) -> Optional[str]:
        self._load()

        best_name = None
        best_score = 0.0

        for rule in self._data.get("rules", []):
            canonical_name = rule.get("canonical_name")
            aliases = rule.get("aliases", [])

            for alias in aliases:
                score = self._best_match_score(text, alias)
                if score > best_score:
                    best_score = score
                    best_name = canonical_name

        if best_name and best_score >= 0.88:
            return best_name

        return fallback

    def format_rule_result(self, rule: Dict[str, Any]) -> str:
        canonical_name = rule.get("canonical_name", "Нова категорія")
        aliases = rule.get("aliases", [])
        aliases_preview = ", ".join(aliases[:12])

        lines = [
            f"Створив категорію: {canonical_name}",
            "Запам’ятав варіанти для розпізнавання:",
            aliases_preview or canonical_name,
            "",
            "Тепер текстові витрати й позиції з чеків будуть намагатися потрапляти саме в цю категорію.",
        ]
        return "\n".join(lines)
