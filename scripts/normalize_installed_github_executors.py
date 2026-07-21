#!/usr/bin/env python3
"""Normalize installed GitHub executors without contacting GitHub.

This is an idempotent migration for Metnos-owned builtin executors generated
before wrappers acquired boundary argument validation and hermetic birth tests.
The installed artifacts live in user data, so they are deliberately updated
by an explicit migration rather than silently at loader startup.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path


ROOT = Path.home() / ".local/share/metnos/executors/skills/github"

REQUIRED = {
    "change_pulls_github": ("repo", "number"),
    "create_issues_github": ("repo", "title"),
    "create_tasks_github": ("repo", "workflow", "ref"),
    "delete_issues_github": ("repo", "number"),
    "delete_messages_github": ("repo", "comment_id"),
    "find_files_github": ("repo",),
    "find_issues_github": ("repo",),
    "find_pulls_github": ("repo",),
    "list_dirs_github": ("repo",),
    "read_files_github": ("repo", "paths"),
    "read_issues_github": ("repo", "number"),
    "read_pulls_github": ("repo", "number"),
    "read_tasks_github": ("repo",),
    "send_messages_github": ("repo", "target", "body"),
    "set_issues_github": ("repo", "number"),
    "set_pulls_github": ("repo", "number"),
}

MUTATORS = {
    "change_pulls_github",
    "create_issues_github",
    "create_tasks_github",
    "delete_issues_github",
    "delete_messages_github",
    "send_messages_github",
    "set_issues_github",
    "set_pulls_github",
}


def _toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        body = ", ".join(f"{key} = {_toml_value(item)}" for key, item in value.items())
        return "{ " + body + " }"
    raise TypeError(f"unsupported TOML fixture value: {value!r}")


def _sample(name: str, arg_type: str):
    if name == "repo":
        return "owner/repo"
    if name in {"number", "comment_id", "workflow_id"}:
        return 1
    if name == "workflow":
        return "ci.yml"
    if name == "ref":
        return "main"
    if name == "paths":
        return ["README.md"]
    if name == "target":
        return "issue:1"
    if name in {"body", "title"}:
        return "offline test"
    if arg_type == "integer":
        return 1
    if arg_type == "number":
        return 1.0
    if arg_type == "boolean":
        return True
    if arg_type == "array":
        return ["test"]
    return "test"


def _tests_text(name: str, manifest: dict) -> str:
    properties = manifest["args"]["properties"]
    valid = {
        arg: _sample(arg, properties[arg].get("type", "string"))
        for arg in REQUIRED[name]
    }
    happy_expect = {"ok": True, "used": 1} if name in MUTATORS else {"ok": True, "used": 0}
    fake = "skill_test_fakes.success" if name in MUTATORS else "skill_test_fakes.empty"
    tests = (
        ("rejects_unknown_args_offline", {"unknown_contract_arg": True},
         {"ok": False, "error_class": "invalid_args",
          "error_code": "ERR_ARG_INVALID"}, None),
        ("happy_path_offline", valid, happy_expect,
         {"METNOS_SUBPROCESS_FAKE": fake}),
        ("auth_missing_offline_needs_inputs", valid,
         {"ok": True, "decision": "needs_inputs", "error_class": "auth_required"},
         {"METNOS_SUBPROCESS_FAKE": "skill_test_fakes.auth_required"}),
        ("rejects_missing_required_offline", {},
         {"ok": False, "error_class": "invalid_args",
          "error_code": "ERR_ARG_INVALID"}, None),
    )
    blocks = []
    for test_name, input_value, expect, env in tests:
        block = [
            "[[tests]]",
            f'name   = "{test_name}"',
            f"input  = {_toml_value(input_value)}",
            f"expect = {_toml_value(expect)}",
        ]
        if env:
            block.append(f"env    = {_toml_value(env)}")
        blocks.append("\n".join(block))
    return "\n\n\n".join(blocks) + "\n"


def _normalize_manifest(name: str, path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    text = re.sub(
        r"^# Manifest dell'executor `([^`]+)` - Metnos v1\.1 \(importato da skill\)\.$",
        r"# Manifest dell'executor `\1` - Metnos v1.1 (builtin handcrafted).",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"(?m)^# Importato da: [^\n]+\n",
        "",
        text,
        count=1,
    )
    text = text.replace(
        'executor_standard = "metnos.executor/1.0"\n',
        'executor_standard = "metnos.executor/1.0"\norigin = "handcrafted"\n',
        1,
    ) if 'origin = "handcrafted"' not in text else text
    text = re.sub(
        r'(?m)^author\s*=\s*"Metnos importer <importer@metnos\.com>"$',
        'author      = "Metnos builtin maintainers"',
        text,
        count=1,
    )
    text = text.replace("L'importer li marca optional", "La generazione iniziale li marcava optional")
    text = text.replace("L'importer lo marca optional", "La generazione iniziale lo marcava optional")
    text = text.replace("dell'importer-parser", "del parser iniziale")
    text = re.sub(
        r"\n\[provenance\]\n.*?(?=\n\[\[tests\]\])",
        "",
        text,
        count=1,
        flags=re.DOTALL,
    )
    if "error_code?: str" not in text:
        text = text.replace(
            "  error_class?: str,\n",
            "  error_class?: str,\n  error_code?: str,\n",
            1,
        )
    required = "[" + ", ".join(f'"{arg}"' for arg in REQUIRED[name]) + "]"
    text, count = re.subn(
        r"(?m)^required\s*=\s*\[[^\n]*\]$",
        f"required = {required}",
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"{path}: expected one args.required declaration")
    base = text.split("\n[[tests]]", 1)[0].rstrip() + "\n\n\n"
    parsed = tomllib.loads(base)
    return parsed, base + _tests_text(name, parsed)


def _normalize_wrapper(name: str, path: Path, manifest: dict) -> str:
    text = path.read_text(encoding="utf-8")
    text = re.sub(
        rf'^"""{re.escape(name)} - executor importato da skill `[^`]+`\.',
        f'"""{name} - executor builtin GitHub handcrafted.',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if "_error_code_for_class," not in text:
        text, count = re.subn(
            r"(from skill_wrapper import \([^\n]*\n)",
            r"\1    _error_code_for_class,\n",
            text,
            count=1,
        )
        if count != 1:
            raise RuntimeError(f"{path}: skill_wrapper import block not found")
    if "_validate_skill_args," not in text:
        text, count = re.subn(
            r"(from skill_wrapper import \([^\n]*\n)",
            r"\1    _validate_skill_args,\n",
            text,
            count=1,
        )
        if count != 1:
            raise RuntimeError(f"{path}: skill_wrapper import block not found")

    marker = "    _arg_error = _validate_skill_args("
    if marker not in text:
        invoke_head = (
            "def invoke(args):\n"
            "    if not isinstance(args, dict):\n"
            "        return _err_obj(\"args must be an object\", \"invalid_args\")\n"
        )
        allowed = tuple(manifest["args"]["properties"].keys())
        validation = (
            invoke_head
            + "\n    _arg_error = _validate_skill_args(\n"
            + f"        args, allowed=frozenset({allowed!r}),\n"
            + f"        required={REQUIRED[name]!r},\n"
            + "    )\n"
            + "    if _arg_error:\n"
            + "        return _err_obj(_arg_error, \"invalid_args\")\n"
        )
        if invoke_head not in text:
            raise RuntimeError(f"{path}: canonical invoke boundary not found")
        text = text.replace(invoke_head, validation, 1)
    text = text.replace(
        'base = {"ok": False, "error": msg, "error_class": ec}',
        'base = {"ok": False, "error": msg, "error_class": ec,\n'
        '            "error_code": _error_code_for_class(ec)}',
    )
    text = text.replace(
        'return {"ok": False, "error": msg, "error_class": ec, '
        '"entries": [], "used": 0}',
        'return {"ok": False, "error": msg, "error_class": ec,\n'
        '            "error_code": _error_code_for_class(ec), '
        '"entries": [], "used": 0}',
    )
    return text


def main() -> int:
    found = {path.name for path in ROOT.iterdir() if path.is_dir()}
    if found != set(REQUIRED):
        raise RuntimeError(
            f"installed GitHub executor set drift: missing={sorted(set(REQUIRED)-found)} "
            f"unexpected={sorted(found-set(REQUIRED))}"
        )
    for name in sorted(REQUIRED):
        directory = ROOT / name
        manifest_path = directory / "manifest.toml"
        wrapper_path = directory / f"{name}.py"
        parsed, manifest_text = _normalize_manifest(name, manifest_path)
        wrapper_text = _normalize_wrapper(name, wrapper_path, parsed)
        wrapper_path.write_text(wrapper_text, encoding="utf-8")
        manifest_path.write_text(manifest_text, encoding="utf-8")
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
