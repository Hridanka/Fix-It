"""Command-line interface for fixit."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from .core import PLUGINS, analyze, normalize_language
from .fixer import run_fix
from .formatter import render_text
from .history import list_history


def _add_analysis_options(parser: argparse.ArgumentParser, *, suppress: bool = False) -> None:
    default = argparse.SUPPRESS if suppress else None
    parser.add_argument("--lang", default=default, help="Language name or 'auto'")
    for flag, help_text in (("--fix", "Offer a confirmed repair command"),
                            ("--explain", "Include plain-language explanations"),
                            ("--json", "Print machine-readable JSON"),
                            ("--verbose", "Include raw diagnostic output"),
                            ("--quiet", "Print only the final summary"),
                            ("--no-color", "Disable colored terminal output")):
        parser.add_argument(flag, action="store_true", default=argparse.SUPPRESS if suppress else False,
                            help=help_text)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fixit", description="Find and explain errors in source and logs.")
    _add_analysis_options(parser)
    commands = parser.add_subparsers(dest="subcommand")

    check_parser = commands.add_parser("check", help="Check a source file with its language tool",
                                       description="Run the selected language's syntax checker on a file.")
    check_parser.add_argument("file", help="Source file to check")
    _add_analysis_options(check_parser, suppress=True)

    run_parser = commands.add_parser("run", help="Run a command and analyze its output",
                                     description="Execute a command without a shell and analyze its output.")
    _add_analysis_options(run_parser, suppress=True)
    run_parser.add_argument("command", nargs=argparse.REMAINDER, help="Command and arguments (use -- before command options)")

    scan_parser = commands.add_parser("scan", help="Analyze a log file",
                                      description="Scan a diagnostic log, optionally following appended lines.")
    scan_parser.add_argument("logfile", help="Log file to read")
    scan_parser.add_argument("-f", "--follow", action="store_true", help="Follow appended log content")
    _add_analysis_options(scan_parser, suppress=True)

    history_parser = commands.add_parser("history", help="List analyzed errors from SQLite history",
                                         description="List recent errors and mark recurring diagnostics.")
    history_parser.add_argument("--limit", type=int, default=20, help="Maximum records to display (1-500)")
    commands.add_parser("languages", help="List supported languages and local tools",
                        description="Show supported languages and whether their checker is installed.")
    return parser


def _select_language(language: str | None, content: str, filename: str | None = None) -> str | None:
    if language:
        return language
    if not sys.stdin.isatty():
        return "auto"
    try:
        from rich.console import Console
        from rich.prompt import Prompt
        Console().print("[bold]Choose a language[/bold]")
        Console().print("  1. Auto-detect")
        choices = list(PLUGINS)
        for index, name in enumerate(choices, start=2):
            Console().print(f"  {index}. {name}")
        selection = Prompt.ask("Language number", default="1")
    except ImportError:
        print("Choose a language:")
        print("  1. Auto-detect")
        choices = list(PLUGINS)
        for index, name in enumerate(choices, start=2):
            print(f"  {index}. {name}")
        selection = input("Language number [1]: ").strip() or "1"
    if selection == "1":
        return "auto"
    try:
        return choices[int(selection) - 2]
    except (ValueError, IndexError):
        print("Invalid selection. Using auto-detect.", file=sys.stderr)
        return "auto"


def _display(response: dict[str, Any], args: argparse.Namespace) -> int:
    if response.get("note"):
        print(response["note"], file=sys.stderr)
    if response.get("history_note"):
        print(response["history_note"], file=sys.stderr)
    if args.json:
        print(json.dumps(response, indent=2, ensure_ascii=False))
    else:
        output = render_text(response["results"], explain=args.explain,
                             verbose=args.verbose, quiet=args.quiet,
                             no_color=args.no_color,
                             exit_code=(response.get("exit_code") or {}).get("code"))
        print(output)
    if args.fix:
        confirmation_stream = sys.stdin
        close_confirmation_stream = False
        if not sys.stdin.isatty():
            try:
                confirmation_stream = open("CONIN$" if os.name == "nt" else "/dev/tty",
                                           "r", encoding="utf-8")
                close_confirmation_stream = True
            except OSError:
                pass
        for result in response["results"]:
            rule = result.get("match")
            if rule and rule.get("command"):
                run_fix(rule, confirmation_stream, sys.stdout)
        if close_confirmation_stream:
            confirmation_stream.close()
    return 1 if response["summary"]["errors_found"] else 0


def _analyze(content: str, language: str | None, args: argparse.Namespace,
             *, input_type: str = "error", filename: str | None = None,
             exit_code: int | None = None) -> int:
    try:
        selected = _select_language(language, content, filename)
        response = analyze(content, selected, input_type=input_type,
                           filename=filename, exit_code=exit_code)
    except (ValueError, OSError) as error:
        print(f"fixit: {error}", file=sys.stderr)
        print("Valid languages: " + ", ".join(PLUGINS), file=sys.stderr)
        return 2
    return _display(response, args)


def _run_command(command: list[str], language: str | None, args: argparse.Namespace) -> int:
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("fixit: run requires a command", file=sys.stderr)
        return 2
    command = list(command)
    if command and command[0] == "--":
        command = command[1:]
    else:
        if "--lang" in command:
            index = command.index("--lang")
            if index + 1 < len(command):
                language = command[index + 1]
                del command[index:index + 2]
        for option, attribute in (("--fix", "fix"), ("--explain", "explain"), ("--json", "json"),
                                 ("--verbose", "verbose"), ("--quiet", "quiet"), ("--no-color", "no_color")):
            if option in command:
                setattr(args, attribute, True)
                command.remove(option)
    selected_language = normalize_language(language)
    if (selected_language and selected_language not in {"auto", "autodetect"}
            and selected_language not in PLUGINS):
        print(f"fixit: unknown language '{language}'", file=sys.stderr)
        print("Valid languages: " + ", ".join(PLUGINS), file=sys.stderr)
        return 2
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   timeout=10, check=False)
    except FileNotFoundError:
        return _analyze(f"{command[0]}: command not found", language, args, exit_code=127)
    except subprocess.TimeoutExpired as error:
        output = "\n".join(part for part in (error.stderr, error.stdout) if part)
        return _analyze(output + "\nCommand timed out after 10 seconds.", language, args,
                        exit_code=124)
    except OSError as error:
        print(f"fixit: could not run command: {error}", file=sys.stderr)
        return 2
    if completed.returncode == 0:
        if completed.stderr.strip():
            return _analyze(completed.stderr, language, args, exit_code=0)
        print("Command completed successfully.")
        return 0
    output = "\n".join(part for part in (completed.stderr, completed.stdout) if part)
    return _analyze(output or f"Command exited with status {completed.returncode}.", language,
                    args, exit_code=completed.returncode)


def _scan(path: Path, language: str | None, follow: bool, args: argparse.Namespace) -> int:
    selected = normalize_language(language)
    if selected and selected not in {"auto", "autodetect"} and selected not in PLUGINS:
        print(f"fixit: unknown language '{language}'", file=sys.stderr)
        print("Valid languages: " + ", ".join(PLUGINS), file=sys.stderr)
        return 2
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            content = stream.read()
            if content:
                status = _analyze(content, language, args, filename=str(path))
            else:
                status = 0
            if not follow:
                return status
            print(f"Following {path}; press Ctrl+C to stop.", file=sys.stderr)
            while True:
                appended = stream.read()
                if appended:
                    status = max(status, _analyze(appended, language, args,
                                                  filename=str(path)))
                else:
                    time.sleep(0.25)
    except KeyboardInterrupt:
        return 130
    except OSError as error:
        print(f"fixit: cannot read {path}: {error}", file=sys.stderr)
        return 2


def _show_languages() -> int:
    for name, plugin in PLUGINS.items():
        status = "installed" if plugin.is_installed() else f"missing ({plugin.required_tool})"
        print(f"{name:<12} {status}")
    return 0


def _show_history(limit: int) -> int:
    if not 1 <= limit <= 500:
        print("fixit: --limit must be between 1 and 500", file=sys.stderr)
        return 2
    records = list_history(limit)
    if not records:
        print("No errors in history.")
        return 0
    for record in records:
        repeated = " [repeat]" if record["repeats"] > 1 else ""
        location = f" {record['file']}:{record['line'] or '?'}" if record.get("file") else ""
        print(f"{record['timestamp']} {record['severity']} {record['language']} "
              f"{record['error_type']}: {record['message']}{location}{repeated}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.subcommand == "languages":
        return _show_languages()
    if args.subcommand == "history":
        return _show_history(args.limit)
    if args.subcommand == "check":
        selected_language = normalize_language(args.lang)
        if (selected_language and selected_language not in {"auto", "autodetect"}
            and selected_language not in PLUGINS):
            print(f"fixit: unknown language '{args.lang}'", file=sys.stderr)
            print("Valid languages: " + ", ".join(PLUGINS), file=sys.stderr)
            return 2
        path = Path(args.file)
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            print(f"fixit: cannot read {path}: {error}", file=sys.stderr)
            return 2
        return _analyze(source, args.lang, args, input_type="code", filename=str(path))
    if args.subcommand == "run":
        return _run_command(args.command, args.lang, args)
    if args.subcommand == "scan":
        return _scan(Path(args.logfile), args.lang, args.follow, args)
    if args.subcommand is None:
        if sys.stdin.isatty():
            parser.print_help(sys.stderr)
            return 2
        content = sys.stdin.read()
        if not content.strip():
            print("fixit: stdin is empty", file=sys.stderr)
            return 2
        return _analyze(content, args.lang, args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())