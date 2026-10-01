"""Terminal and JSON presentation for analysis results."""

from __future__ import annotations

import json
from difflib import unified_diff
from typing import Any

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
except ImportError:  # Keep imports and JSON output usable before optional install.
    Console = Panel = Table = None


COLORS = {"INFO": "cyan", "WARNING": "yellow", "ERROR": "red", "CRITICAL": "bold red"}


def render_json(results: list[dict[str, Any]], exit_code: int | None = None) -> str:
    return json.dumps({"results": results, "exit_code": exit_code,
                       "summary": {"errors_found": len(results),
                                   "fixes_available": sum(bool((item.get("match") or {}).get("command")) for item in results),
                                   "unknown_errors": sum(item.get("match") is None for item in results)}}, indent=2)


def render_text(results: list[dict[str, Any]], *, explain: bool = False,
                verbose: bool = False, quiet: bool = False,
                no_color: bool = False, exit_code: int | None = None) -> str:
    if Console is None or Table is None or Panel is None:
        return _render_plain(results, explain, verbose, quiet, exit_code)
    console = Console(record=True, force_terminal=not no_color, color_system=None if no_color else "standard", width=100)
    if not quiet:
        for index, result in enumerate(results, start=1):
            error = result["error"]
            severity = error["severity"]
            color = COLORS.get(severity, "white")
            heading = f"[{color}]{severity}[/{color}]  {error['error_type']} ({error['language']})"
            detail = error["message"]
            if error.get("file"):
                detail += f"\n{error['file']}" + (f":{error['line']}" if error.get("line") else "")
                if error.get("column"):
                    detail += f":{error['column']}"
            console.print(Panel(detail, title=heading, border_style=color, expand=False))
            if error.get("context"):
                context = Table(show_header=False, box=None, pad_edge=False)
                for row in error["context"]:
                    context.add_row(str(row["number"]), row["text"])
                console.print(context)
            rule = result.get("match")
            if rule:
                console.print(f"[bold green]Solution: {rule.get('cause', rule['id'])}[/bold green]")
                if explain:
                    console.print(rule.get("explanation", ""))
                for step_index, step in enumerate(rule.get("steps", []), start=1):
                    console.print(f"  {step_index}. {step}")
                if rule.get("command"):
                    console.print(f"[cyan]Fix command:[/cyan] {rule['command']}")
                example = rule.get("fixed_code_example") or {}
                before, after = example.get("before"), example.get("after")
                if before is not None and after is not None and before != after:
                    diff = unified_diff(str(before).splitlines(), str(after).splitlines(),
                                        fromfile="before", tofile="after", lineterm="")
                    console.print("\n".join(diff))
                if rule.get("docs_url"):
                    console.print(f"[dim]Docs: {rule['docs_url']}[/dim]")
            elif result.get("suggestions"):
                console.print("[yellow]No exact rule. Likely solutions:[/yellow]")
                for suggestion in result["suggestions"]:
                    candidate = suggestion["rule"]
                    console.print(f"  {suggestion['confidence']:.1f}%  {candidate.get('cause', candidate['id'])}")
                    if explain:
                        console.print(f"    {candidate.get('explanation', '')}")
                    for step_index, step in enumerate(candidate.get("steps", []), start=1):
                        console.print(f"    {step_index}. {step}")
                    example = candidate.get("fixed_code_example") or {}
                    if example.get("before") != example.get("after"):
                        diff = unified_diff(str(example.get("before", "")).splitlines(),
                                            str(example.get("after", "")).splitlines(),
                                            fromfile="before", tofile="after", lineterm="")
                        console.print("\n".join(diff))
            else:
                console.print("[yellow]No matching solution found.[/yellow]")
            if verbose:
                console.print(f"[dim]Raw: {error.get('raw_text', error.get('raw', ''))}[/dim]")
            if index < len(results):
                console.print()
        if exit_code is not None:
            from .parsers import interpret_exit_code
            explanation = interpret_exit_code(exit_code)
            if explanation:
                console.print(f"Exit {exit_code}: {explanation['explanation']}")
    fixes = sum(bool((item.get("match") or {}).get("command")) for item in results)
    unknown = sum(item.get("match") is None for item in results)
    console.print(f"Summary: {len(results)} error(s) found, {fixes} fix(es) available, {unknown} unknown.")
    return console.export_text(styles=not no_color).strip()


def _render_plain(results: list[dict[str, Any]], explain: bool, verbose: bool,
                  quiet: bool, exit_code: int | None) -> str:
    lines: list[str] = []
    if not quiet:
        for result in results:
            error = result["error"]
            lines.extend((f"{error['severity']} {error['error_type']} ({error['language']}): {error['message']}",))
            if error.get("file"):
                location = f"{error['file']}:{error.get('line') or '?'}"
                if error.get("column"):
                    location += f":{error['column']}"
                lines.append(f"Location: {location}")
            rule = result.get("match")
            if rule:
                lines.append(f"Solution: {rule.get('cause', rule['id'])}")
                if explain:
                    lines.append(rule.get("explanation", ""))
                lines.extend(f"  {step}" for step in rule.get("steps", []))
                if rule.get("command"):
                    lines.append(f"Fix command: {rule['command']}")
                example = rule.get("fixed_code_example") or {}
                if example.get("before") != example.get("after"):
                    diff = unified_diff(str(example.get("before", "")).splitlines(),
                                        str(example.get("after", "")).splitlines(),
                                        fromfile="before", tofile="after", lineterm="")
                    lines.extend(diff)
            else:
                lines.append("No exact rule found.")
                for item in result.get("suggestions", []):
                    candidate = item["rule"]
                    lines.append(f"  {item['confidence']:.1f}% {candidate.get('cause', candidate['id'])}")
                    if explain:
                        lines.append(f"    {candidate.get('explanation', '')}")
                    lines.extend(f"    {step}" for step in candidate.get("steps", []))
                    example = candidate.get("fixed_code_example") or {}
                    if example.get("before") != example.get("after"):
                        diff = unified_diff(str(example.get("before", "")).splitlines(),
                                            str(example.get("after", "")).splitlines(),
                                            fromfile="before", tofile="after", lineterm="")
                        lines.extend(diff)
            if verbose:
                lines.append(f"Raw: {error.get('raw_text', error.get('raw', ''))}")
    fixes = sum(bool((item.get("match") or {}).get("command")) for item in results)
    unknown = sum(item.get("match") is None for item in results)
    lines.append(f"Summary: {len(results)} error(s) found, {fixes} fix(es) available, {unknown} unknown.")
    return "\n".join(lines)