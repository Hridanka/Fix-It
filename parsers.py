"""Recognize common compiler, runtime, and shell errors."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Any


@dataclass
class ParsedError:
    error_type: str
    message: str
    language: str
    severity: str = "ERROR"
    file: str | None = None
    line: int | None = None
    raw: str = ""
    context: list[dict[str, Any]] | None = None
    column: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _context_for(file_name: str | None, line_number: int | None) -> list[dict[str, Any]] | None:
    if not file_name or not line_number or file_name.startswith("<"):
        return None
    try:
        lines = Path(file_name).read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return None
    if line_number < 1 or line_number > len(lines):
        return None
    start = max(1, line_number - 2)
    end = min(len(lines), line_number + 2)
    return [{"number": index, "text": lines[index - 1]} for index in range(start, end + 1)]


def _record(error_type: str, message: str, language: str, raw: str,
            file_name: str | None = None, line_number: int | None = None,
        severity: str = "ERROR", column: int | None = None) -> ParsedError:
    return ParsedError(error_type, message.strip(), language, severity, file_name,
               line_number, raw.strip(), _context_for(file_name, line_number), column)


def parse_errors(text: str) -> list[ParsedError]:
    """Extract supported errors from arbitrary combined output or logs."""
    if not isinstance(text, str) or not text.strip():
        return []

    found: list[ParsedError] = []
    lines = text.splitlines()

    traceback_indices = [i for i, line in enumerate(lines) if line.strip() == "Traceback (most recent call last):"]
    python_exception_indices: set[int] = set()
    for position, start in enumerate(traceback_indices):
        stop = traceback_indices[position + 1] if position + 1 < len(traceback_indices) else len(lines)
        block = lines[start:stop]
        exception_index = next((i for i in range(len(block) - 1, -1, -1)
                                if re.match(r"^[\w.]+(?:Error|Exception|Interrupt|Exit|Warning)(?::|$)", block[i].strip())), None)
        if exception_index is None:
            continue
        python_exception_indices.add(start + exception_index)
        exception_line = block[exception_index].strip()
        error_type, _, message = exception_line.partition(":")
        frames = re.findall(r'^\s*File ["\'](.+?)["\'], line (\d+)',
                    "\n".join(block), re.MULTILINE)
        file_name, line_number = (frames[-1][0], int(frames[-1][1])) if frames else (None, None)
        found.append(_record(error_type, message or error_type, "python", "\n".join(block), file_name, line_number))

    gcc_pattern = re.compile(r"^(.*?):(\d+)(?::(\d+))?:\s*(fatal error|error|warning|note):\s*(.*)$", re.I)
    for line in lines:
        match = gcc_pattern.match(line.strip())
        if match:
            file_name, line_number, column, kind, message = match.groups()
            severity = "CRITICAL" if kind.lower() == "fatal error" else "WARNING" if kind.lower() == "warning" else "INFO" if kind.lower() == "note" else "ERROR"
            found.append(_record(kind.title(), message, "c/c++", line, file_name,
                                 int(line_number), severity,
                                 int(column) if column else None))

    java_throwable = re.compile(r"^([\w.$]*(?:Exception|Error|Throwable))(?::\s*(.*))?$")
    for index, line in enumerate(lines):
        if index in python_exception_indices:
            continue
        match = java_throwable.match(line.strip())
        if not match:
            continue
        java_name = match.group(1)
        if java_name in {"NameError", "TypeError", "ReferenceError", "SyntaxError",
                         "IndentationError", "KeyError", "IndexError", "ValueError",
                         "AttributeError", "ImportError", "ModuleNotFoundError"}:
            continue
        tail = "\n".join(lines[index:])
        frames = re.findall(r"\bat\s+[^\n(]+\(([^():]+):(\d+)\)", tail)
        if not frames and "." not in java_name:
            continue
        file_name, line_number = (frames[0][0], int(frames[0][1])) if frames else (None, None)
        end = index + 1
        while end < len(lines) and (lines[end].lstrip().startswith("at ") or lines[end].lstrip().startswith("Caused by:")):
            end += 1
        found.append(_record(match.group(1), match.group(2) or match.group(1), "java",
                             "\n".join(lines[index:end]), file_name, line_number))

    node_pattern = re.compile(r"^(?:\w*Error|TypeError|ReferenceError|SyntaxError)(?::\s*)(.*)$")
    node_location = re.compile(r"\s+at\s+(?:.+\s+\()?(.+\.m?js):(\d+):(\d+)\)?")
    for index, line in enumerate(lines):
        if index in python_exception_indices:
            continue
        match = node_pattern.match(line.strip())
        if not match:
            continue
        tail = "\n".join(lines[index + 1:index + 8])
        location = node_location.search(tail)
        file_name, line_number = (location.group(1), int(location.group(2))) if location else (None, None)
        found.append(_record(line.strip().split(":", 1)[0], match.group(1), "node.js",
                     "\n".join(lines[index:index + 8]), file_name, line_number,
                     column=int(location.group(3)) if location else None))

    shell_patterns = [
        (re.compile(r"(?:^|:\s*)(.+?):\s*command not found\s*$", re.I), "CommandNotFound", "Command '{target}' was not found.", 127),
        (re.compile(r"(?:^|:\s*)(.+?):\s*Permission denied\s*$", re.I), "PermissionDenied", "Permission was denied for '{target}'.", 126),
        (re.compile(r"(?:^|:\s*)(.+?):\s*No such file or directory\s*$", re.I), "FileNotFound", "The path '{target}' does not exist.", None),
    ]
    for line in lines:
        for pattern, error_type, message_template, _ in shell_patterns:
            match = pattern.search(line.strip())
            if match:
                target = match.group(1).strip().strip("'").strip('"')
                found.append(_record(error_type, message_template.format(target=target), "shell", line,
                                     target if error_type != "CommandNotFound" else None))
                break
        if re.search(r"segmentation fault|segfault", line, re.I):
            found.append(_record("SegmentationFault", line.strip(), "shell", line, severity="CRITICAL"))

    unique: list[ParsedError] = []
    seen: set[tuple[str, str, str | None, int | None]] = set()
    for error in found:
        key = (error.error_type, error.message, error.file, error.line)
        if key not in seen:
            seen.add(key)
            unique.append(error)
    return unique


def interpret_exit_code(code: int | None) -> dict[str, str] | None:
    """Explain common Unix process exit codes."""
    meanings = {
        1: ("ERROR", "General failure."),
        2: ("ERROR", "Invalid command usage or a shell syntax error."),
        126: ("ERROR", "The command was found but could not be executed."),
        127: ("ERROR", "The command was not found."),
        130: ("WARNING", "The process was interrupted with Ctrl+C (SIGINT)."),
        137: ("CRITICAL", "The process was killed with SIGKILL, often due to memory limits."),
        139: ("CRITICAL", "The process terminated with SIGSEGV (segmentation fault)."),
    }
    if code is None:
        return None
    severity, explanation = meanings.get(code, ("INFO", "The process exited successfully." if code == 0 else f"The process exited with status {code}."))
    return {"code": code, "severity": severity, "explanation": explanation}