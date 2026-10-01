"""Confirmation-gated execution for known repair commands."""

from __future__ import annotations

import subprocess
import shlex
from typing import Any, TextIO


def run_fix(rule: dict[str, Any], input_stream: TextIO, output_stream: TextIO) -> int | None:
    command = rule.get("command")
    if not command:
        output_stream.write("No ready-to-run fix command is available for this rule.\n")
        return None
    output_stream.write(f"Suggested command: {command}\nRun it? [y/N] ")
    output_stream.flush()
    try:
        answer = input_stream.readline().strip().lower()
    except (EOFError, OSError):
        answer = ""
    if answer not in {"y", "yes"}:
        output_stream.write("Fix not run.\n")
        return None
    try:
        return subprocess.run(shlex.split(str(command)), check=False).returncode
    except OSError as error:
        output_stream.write(f"Could not run fix: {error}\n")
        return 1