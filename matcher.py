"""Load YAML rules and rank exact or fuzzy solutions."""

from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
import re
from typing import Any

import yaml

DEFAULT_KNOWLEDGE_BASE = Path(__file__).resolve().parent.parent / "knowledge_base" / "common.yaml"


def load_rules(path: str | Path | None = None) -> list[dict[str, Any]]:
    source = Path(path) if path is not None else DEFAULT_KNOWLEDGE_BASE
    with source.open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    rules = data.get("rules", [])
    if not isinstance(rules, list):
        raise ValueError("Knowledge base must contain a 'rules' list")
    return [rule for rule in rules if isinstance(rule, dict) and rule.get("id") and rule.get("pattern")]


def _error_text(error: Any) -> str:
    if isinstance(error, dict):
        raw = error.get("raw_text", error.get("raw", ""))
        return " ".join((str(error.get("error_type", "")),
                         str(error.get("message", "")), str(raw))).strip()
    raw = getattr(error, "raw_text", getattr(error, "raw", ""))
    return " ".join((str(getattr(error, "error_type", "")),
                     str(getattr(error, "message", "")), str(raw))).strip()


def match_error(error: Any, rules: list[dict[str, Any]]) -> dict[str, Any]:
    """Return one regex match and up to three likely fuzzy alternatives."""
    searchable = _error_text(error)
    for rule in rules:
        try:
            if re.search(str(rule["pattern"]), searchable, re.IGNORECASE | re.MULTILINE):
                return {"exact": rule, "suggestions": []}
        except re.error:
            continue

    candidates: list[tuple[float, dict[str, Any]]] = []
    query = re.sub(r"[^a-z0-9]+", " ", searchable.lower()).strip()
    for rule in rules:
        haystack = " ".join(str(rule.get(key, "")) for key in ("id", "cause", "explanation", "pattern", "language"))
        normalized = re.sub(r"[^a-z0-9]+", " ", haystack.lower()).strip()
        score = SequenceMatcher(None, query, normalized).ratio()
        query_words = set(query.split())
        rule_words = set(normalized.split())
        if query_words and rule_words:
            score = max(score, len(query_words & rule_words) / len(query_words | rule_words))
        candidates.append((score, rule))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return {"exact": None, "suggestions": [
        {"rule": rule, "confidence": round(score * 100, 1)}
        for score, rule in candidates[:3]
    ]}


def analyze_text(text: str, rules: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    from .parsers import parse_errors

    loaded_rules = load_rules() if rules is None else rules
    results = []
    for error in parse_errors(text):
        match = match_error(error, loaded_rules)
        results.append({"error": error.to_dict(), "match": match["exact"], "suggestions": match["suggestions"]})
    return results