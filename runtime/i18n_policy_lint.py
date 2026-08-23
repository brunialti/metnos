"""Static guard for the single-instance-language boundary.

Operational code may propagate :func:`i18n.instance_language_context`, but it
must never read a per-user ``lang`` preference or call the compatibility
``language_context(value)`` API.  The latter remains in ``i18n.py`` only for
old external integrations and deliberately cannot override the signed
instance authority.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class PolicyIssue:
    path: str
    line: int
    code: str
    message: str


def _literal_string(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.issues: list[PolicyIssue] = []

    def _add(self, node: ast.AST, code: str, message: str) -> None:
        self.issues.append(PolicyIssue(
            path=str(self.path), line=int(getattr(node, "lineno", 0)),
            code=code, message=message,
        ))

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        name = ""
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name == "language_context":
            self._add(
                node, "I18N_PER_REQUEST_CONTEXT",
                "use instance_language_context(); request values cannot select language",
            )
        if name == "get_pref" and len(node.args) >= 2:
            if _literal_string(node.args[1]) == "lang":
                self._add(
                    node, "I18N_USER_PREFERENCE",
                    "per-user language preferences are outside the runtime language boundary",
                )
        self.generic_visit(node)


def scan(paths: Iterable[Path]) -> list[PolicyIssue]:
    """Scan Python files, ignoring the compatibility API definition itself."""
    issues: list[PolicyIssue] = []
    for path in sorted({Path(item) for item in paths}, key=lambda item: str(item)):
        if path.name == "i18n.py" or not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            issues.append(PolicyIssue(str(path), 0, "I18N_LINT_PARSE", str(exc)))
            continue
        visitor = _Visitor(path)
        visitor.visit(tree)
        issues.extend(visitor.issues)
    return issues


def scan_runtime(root: Path) -> list[PolicyIssue]:
    return scan(root.rglob("*.py"))


def main() -> int:
    root = Path(__file__).resolve().parent
    issues = scan_runtime(root)
    for issue in issues:
        print(f"{issue.path}:{issue.line}: {issue.code}: {issue.message}")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
