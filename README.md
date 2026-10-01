# fixit

`fixit` is a local-first error assistant with a Unix-style command line and a Flask web interface. It checks source with installed language tools, parses pasted diagnostics, matches the selected language's YAML knowledge base, and stores analyzed failures in SQLite.

Supported plugins: Python, C, C++, Java, JavaScript, TypeScript, Go, Rust, Bash, PHP, Ruby, and SQL. Native compiler/interpreter checks require their respective system tools. If a checker is missing, fixit reports that clearly and pasted diagnostic parsing remains available.

## Requirements

- Python 3.10 or newer
- `pip`
- Optional system checkers: GCC/G++, JDK, Node.js, TypeScript (`tsc`), Go, Rust, Bash, PHP, and Ruby

## Install

From this directory, create and activate a virtual environment, then install the project and dependencies:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[test]"
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`. The CLI entry point is `fixit`.

## CLI

```sh
fixit languages
fixit check app.py --lang python
fixit check app.c --lang c
fixit run --lang python -- python app.py
python app.py 2>&1 | fixit --lang python
fixit scan build.log --lang c
fixit scan build.log --lang c --follow
fixit history --limit 20
```

When `--lang` is omitted in an interactive terminal, fixit offers a language menu with auto-detect. In a pipe it attempts auto-detection without blocking. `--fix` displays a matched command and asks before execution; commands are invoked as argument lists, not through a shell. `--json`, `--explain`, `--verbose`, `--quiet`, and `--no-color` are also available. Errors return status 1, successful checks status 0, and usage/input errors status 2.

The `check` command runs syntax validation only; it does not execute source code. `run` invokes the provided executable directly without shell expansion and has a 10-second timeout.

## Website

```sh
python -m website.app
```

Open `http://127.0.0.1:5000`. The `/api/analyze` endpoint accepts JSON with `language`, `input_type` (`code` or `error`), and `content`. Requests are limited to 200 KB for analysis; submitted source is checked from a temporary file that is removed after the request. Install a language's compiler/interpreter separately for its source-check feature.

## Knowledge base and history

Rules are loaded from `knowledge_base/common.yaml` and only the selected language's YAML file. Each language catalog contains 15 diagnostic patterns; common shell/OS rules cover command lookup, permissions, paths, process exits, networking, and other frequent failures. The default SQLite database is `~/.fixit/history.db`.

## Tests

```sh
python -m pytest
```

The sample files in `samples/` are intentionally invalid and demonstrate source checking. Plugins are discovered from `fixit/languages/`; a plugin module defines a concrete `LanguagePlugin` subclass.