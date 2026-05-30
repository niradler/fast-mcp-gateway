"""Deterministic guard against the AI-generated 'slop' that ruff cannot see.

Ruff's ``ERA`` only flags commented-out *code*, and ``PLC0415`` deliberately ignores
``if TYPE_CHECKING:`` blocks because they sit at module level. This checker fills both
gaps for ``src/`` so future contributions (human or agent) cannot reintroduce:

* ``SLOP001`` — explanatory ``#`` comments. Project style keeps intent in docstrings,
  not inline comments. Tooling directives (``noqa``, ``type:``, ``pragma:`` …) are
  allowed, and a genuinely-surprising line may be justified with a ``# keep: <why>``
  comment, which turns adding a comment into a deliberate, reviewable act.
* ``SLOP002`` — imports nested in ``if TYPE_CHECKING:`` blocks. The project wants every
  import at the top of the module; deferring type-only imports into a separate block
  fragments the import section and reads as inline.
* ``SLOP003`` — docstrings longer than ``MAX_DOCSTRING_LINES`` non-blank lines. Docstrings
  exist for the non-obvious and stay tight; a multi-paragraph docstring is relocated slop.
  Exempt: ``@mcp.tool``-decorated functions, whose docstring is the user-facing tool
  description and is legitimately longer.

Run it directly (``python tools/check_slop.py [paths...]``); it exits non-zero when it
finds anything, so it can gate ``make check``. Defaults to scanning ``src``.
"""

from __future__ import annotations

import argparse
import ast
import io
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

MAX_DOCSTRING_LINES = 5

ALLOWED_COMMENT_PREFIXES = (
    "noqa",
    "type:",
    "pragma:",
    "ruff:",
    "mypy:",
    "pylint:",
    "isort:",
    "flake8:",
    "nosec",
    "keep:",
    "-*-",
    "coding:",
)


@dataclass(frozen=True)
class Finding:
    """A single slop violation located in a source file."""

    path: Path
    line: int
    column: int
    code: str
    message: str

    def render(self) -> str:
        """Format the finding as ``path:line:col: CODE message`` for terminal output."""
        return f"{self.path}:{self.line}:{self.column}: {self.code} {self.message}"


def is_allowed_comment(text: str) -> bool:
    """Return True when a comment is a tooling directive or an explicit ``keep:`` note."""
    body = text.lstrip("#").strip()
    if not body or body.startswith("!"):
        return True
    lowered = body.lower()
    return any(lowered.startswith(prefix) for prefix in ALLOWED_COMMENT_PREFIXES)


def find_comment_slop(path: Path, source: str) -> list[Finding]:
    """Flag every ``#`` comment in ``source`` that is not an allowed directive."""
    findings: list[Finding] = []
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for token in tokens:
        if token.type != tokenize.COMMENT or is_allowed_comment(token.string):
            continue
        row, col = token.start
        message = "explanatory comment; remove it or fold a tight note into the docstring"
        findings.append(Finding(path, row, col + 1, "SLOP001", message))
    return findings


def find_type_checking_imports(path: Path, tree: ast.Module) -> list[Finding]:
    """Flag ``if TYPE_CHECKING:`` blocks that contain import statements."""
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not _is_type_checking_test(node.test):
            continue
        if any(isinstance(stmt, (ast.Import, ast.ImportFrom)) for stmt in node.body):
            findings.append(
                Finding(
                    path,
                    node.lineno,
                    node.col_offset + 1,
                    "SLOP002",
                    "TYPE_CHECKING import block; hoist the import to the top of the module",
                )
            )
    return findings


def find_long_docstrings(path: Path, tree: ast.Module) -> list[Finding]:
    """Flag docstrings longer than the allowed 2-3 lines (verbose docstrings are slop too)."""
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_mcp_tool(node):
            continue
        doc = ast.get_docstring(node, clean=True)
        if doc is None:
            continue
        lines = [line for line in doc.splitlines() if line.strip()]
        if len(lines) > MAX_DOCSTRING_LINES:
            anchor = node.body[0]
            message = f"docstring has {len(lines)} lines; keep it to {MAX_DOCSTRING_LINES} or fewer"
            findings.append(Finding(path, anchor.lineno, anchor.col_offset + 1, "SLOP003", message))
    return findings


def _is_mcp_tool(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True for ``@mcp.tool``-style functions whose docstring becomes the tool description."""
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute) and target.attr == "tool":
            return True
        if isinstance(target, ast.Name) and target.id == "tool":
            return True
    return False


def _is_type_checking_test(test: ast.expr) -> bool:
    """Return True when an ``if`` test is ``TYPE_CHECKING`` or ``typing.TYPE_CHECKING``."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def check_file(path: Path) -> list[Finding]:
    """Return all slop findings for a single Python file."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
        return (
            find_comment_slop(path, source)
            + find_type_checking_imports(path, tree)
            + find_long_docstrings(path, tree)
        )
    except (SyntaxError, tokenize.TokenError) as exc:
        message = f"could not parse ({exc})"
        return [Finding(path, 1, 1, "SLOP000", message)]


def iter_python_files(paths: list[Path]) -> list[Path]:
    """Expand the given paths into a sorted list of ``.py`` files."""
    files: set[Path] = set()
    for path in paths:
        if path.is_dir():
            files.update(path.rglob("*.py"))
        elif path.suffix == ".py":
            files.add(path)
    return sorted(files)


def main(argv: list[str] | None = None) -> int:
    """Scan the requested paths and return a process exit code (0 clean, 1 on findings)."""
    parser = argparse.ArgumentParser(description="Flag AI-slop patterns ruff cannot detect.")
    parser.add_argument(
        "paths", nargs="*", default=["src"], help="files or directories (default: src)"
    )
    args = parser.parse_args(argv)

    findings: list[Finding] = []
    for file in iter_python_files([Path(p) for p in args.paths]):
        findings.extend(check_file(file))

    for finding in findings:
        sys.stdout.write(finding.render() + "\n")

    if findings:
        sys.stdout.write(f"\nFound {len(findings)} slop finding(s).\n")
        return 1
    sys.stdout.write("No slop found.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
