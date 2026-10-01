"""Shared language discovery and error-analysis pipeline."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
import pkgutil
import re
import sqlite3
from typing import Any

from .history import save_errors
from .languages.base import ErrorInfo, LanguagePlugin
from .matcher import load_rules, match_error
from .parsers import interpret_exit_code

KNOWLEDGE_BASE = Path(__file__).resolve().parent.parent / "knowledge_base"
ALIASES = {"c++": "cpp", "js": "javascript", "node": "javascript",
           "nodejs": "javascript", "ts": "typescript", "sh": "bash",
           "shell": "bash", "py": "python"}


def load_plugins() -> dict[str, LanguagePlugin]:
    """Import each module and instantiate its concrete LanguagePlugin class."""
    from . import languages

    plugins: dict[str, LanguagePlugin] = {}
    for module_info in pkgutil.iter_modules(languages.__path__):
        if module_info.name.startswith("_") or module_info.name == "base":
            continue
        module = importlib.import_module(f"{languages.__name__}.{module_info.name}")
        for _, plugin_type in inspect.getmembers(module, inspect.isclass):
            if (issubclass(plugin_type, LanguagePlugin) and plugin_type is not LanguagePlugin
                    and plugin_type.__module__ == module.__name__):
                plugin = plugin_type()
                plugins[plugin.name] = plugin
    return dict(sorted(plugins.items()))


PLUGINS = load_plugins()


def normalize_language(language: str | None) -> str | None:
    if not language:
        return None
    normalized = re.sub(r"[^a-z0-9+#]+", "", language.lower())
    return ALIASES.get(normalized, normalized)


def detect_language(content: str, filename: str | None = None) -> str | None:
    if filename:
        suffix = Path(filename).suffix.lower()
        for name, plugin in PLUGINS.items():
            if suffix in plugin.extensions:
                return name
    first_line = content.splitlines()[0] if content.splitlines() else ""
    shebang = first_line.lower()
    for marker, language in (("python", "python"), ("node", "javascript"),
                             ("bash", "bash"), ("sh", "bash"),
                             ("ruby", "ruby"), ("php", "php")):
        if shebang.startswith("#!") and marker in shebang:
            return language
    heuristics = (
        (r"Traceback \(most recent call last\):|\b(?:SyntaxError|NameError|IndentationError)\b", "python"),
        (r"(?m)^.+\.(?:cc|cpp|cxx|hpp|hh):\d+:\d*:", "cpp"),
        (r"(?m)^.+\.(?:c|h):\d+:\d*:", "c"),
        (r"(?m)^.+\.java:\d+:\s*(?:error|warning):|Exception in thread", "java"),
        (r"(?m)^.+\.ts\(\d+,\d+\):\s*error TS\d+", "typescript"),
        (r"(?m)^.+\.go:\d+:\d+:", "go"),
        (r"(?m)^\s*-->\s+.+\.rs:\d+:\d+", "rust"),
        (r"(?i)command not found|permission denied|no such file or directory|segmentation fault", "bash"),
        (r"\b(?:#include\s*<|int\s+main\s*\()", "c"),
        (r"\b(?:std::|template\s*<)", "cpp"),
        (r"\b(?:public\s+class|java\.lang\.|Exception in thread)", "java"),
        (r"\b(?:const|let|var)\s+\w+\s*=|\bTypeError:|\bReferenceError:", "javascript"),
        (r"\binterface\s+\w+|:\s*(?:string|number|boolean)\b", "typescript"),
        (r"\bpackage\s+\w+|\bfunc\s+\w+\s*\(", "go"),
        (r"\b(?:fn\s+main\s*\(|\blet\s+mut\s+", "rust"),
        (r"\$[A-Za-z_][\w]*\s*=|\bfunction\s+\w+\s*\(", "php"),
        (r"\b(?:def|end)\b|puts\s+['\"]", "ruby"),
        (r"\b(?:SELECT|INSERT|UPDATE|CREATE TABLE)\b", "sql"),
        (r"^\s*(?:if|for|while)\s+.+;\s*do\b|^\s*echo\s+", "bash"),
    )
    for pattern, language in heuristics:
        if re.search(pattern, content, re.I | re.M):
            return language
    return None


def _context(source: str, line_number: int | None) -> list[dict[str, Any]] | None:
    if line_number is None or line_number < 1:
        return None
    lines = source.splitlines()
    if line_number > len(lines):
        return None
    start, end = max(1, line_number - 2), min(len(lines), line_number + 2)
    return [{"number": index, "text": lines[index - 1], "highlight": index == line_number}
            for index in range(start, end + 1)]


def _load_selected_rules(language: str | None) -> list[dict[str, Any]]:
    rules = load_rules(KNOWLEDGE_BASE / "common.yaml")
    if language:
        language_path = KNOWLEDGE_BASE / f"{language}.yaml"
        if language_path.exists():
            rules.extend(load_rules(language_path))
    return rules


def analyze(content: str, language: str | None = None, *, input_type: str = "error",
            filename: str | None = None, exit_code: int | None = None,
            source_path: str | None = None,
            save_history_entry: bool = True) -> dict[str, Any]:
    """Analyze source or pasted tool output using the selected language plugin."""
    if not isinstance(content, str):
        raise TypeError("content must be text")
    if input_type not in {"code", "error"}:
        raise ValueError("input_type must be 'code' or 'error'")
    selected = normalize_language(language)
    auto_detected = selected in (None, "auto", "autodetect")
    if auto_detected:
        selected = detect_language(content, filename)
    if selected and selected not in PLUGINS:
        raise ValueError(f"Unknown language '{language}'. Valid languages: "
                         + ", ".join(PLUGINS))

    note = None
    errors: list[ErrorInfo] = []
    if selected and input_type == "code":
        plugin = PLUGINS[selected]
        check_target = source_path or (filename if filename and Path(filename).is_file() else content)
        errors = plugin.check(check_target)
        note = plugin.last_note
    elif selected and input_type == "error":
        errors = PLUGINS[selected].parse_output(content)
    elif not selected and input_type == "error":
        from .parsers import parse_errors
        errors = [ErrorInfo(item.language, item.error_type, item.message, item.file,
                            item.line, item.column, item.severity, item.raw, item.context)
                  for item in parse_errors(content)]
        note = "Could not auto-detect a language. Choose one from `fixit languages`."
    elif not selected:
        note = "Could not auto-detect a language. Choose one from `fixit languages`."

    if not errors and content.strip() and input_type == "error":
        errors = [ErrorInfo(selected or "unknown", "UnknownError",
                            "No known error format was detected in the supplied text.",
                            filename, severity="ERROR", raw_text=content.strip())]

    results = []
    rules = _load_selected_rules(selected) if selected else load_rules(KNOWLEDGE_BASE / "common.yaml")
    for error in errors:
        if filename and input_type == "code":
            error.file = filename
        if error.context is None and input_type == "code":
            error.context = _context(content, error.line)
        if error.context:
            for row in error.context:
                row["highlight"] = row.get("number") == error.line
        matched = match_error(error, rules)
        results.append({"error": error.to_dict(), "match": matched["exact"],
                        "suggestions": matched["suggestions"]})

    code_info = interpret_exit_code(exit_code) if exit_code is not None else None
    response = {"language": selected, "auto_detected": auto_detected,
                "results": results, "note": note, "exit_code": code_info,
                "summary": {"errors_found": len(results),
                            "fixes_available": sum(bool((item.get("match") or {}).get("command"))
                                                   for item in results),
                            "unknown_errors": sum(item.get("match") is None for item in results)}}
    if save_history_entry and results:
        try:
            save_errors(results, exit_code)
        except (OSError, sqlite3.Error) as error:
            response["history_note"] = f"Could not save history: {error}"
    return response