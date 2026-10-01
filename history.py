"""Persistent SQLite history of analyzed errors."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


def history_path() -> Path:
    return Path.home() / ".fixit" / "history.db"


def save_errors(results: list[dict[str, Any]], exit_code: int | None = None,
                database: str | Path | None = None) -> None:
    path = Path(database) if database is not None else history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS errors (
            id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, error_type TEXT NOT NULL,
            message TEXT NOT NULL, language TEXT NOT NULL, file TEXT, line INTEGER,
            severity TEXT NOT NULL, rule_id TEXT, exit_code INTEGER, payload TEXT NOT NULL
        )""")
        for result in results:
            error = result["error"]
            connection.execute(
                "INSERT INTO errors (timestamp,error_type,message,language,file,line,severity,rule_id,exit_code,payload) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), error["error_type"],
                 error["message"], error["language"], error.get("file"), error.get("line"),
                 error["severity"], (result.get("match") or {}).get("id"), exit_code,
                 json.dumps(result, ensure_ascii=False)),
            )


def list_history(limit: int = 20, database: str | Path | None = None) -> list[dict[str, Any]]:
    path = Path(database) if database is not None else history_path()
    if not path.exists():
        return []
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("""SELECT e.*, (
            SELECT COUNT(*) FROM errors prior
            WHERE lower(prior.error_type)=lower(e.error_type) AND lower(prior.message)=lower(e.message)
        ) AS repeats FROM errors e ORDER BY e.id DESC LIMIT ?""", (max(1, min(limit, 500)),)).fetchall()
    return [dict(row) for row in rows]