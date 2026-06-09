#!/usr/bin/env python3
"""read_issues — legge i record di trattamento delle issue dal db locale.

Mattone del flusso di maintenance repo (executor, non core): rilegge dallo store
`github_issue_qa` i record per stato/numero/repo. Read-only, deterministico §7.9.
Output `entries` (pipeable §2.6/§2.10), es. verso send_messages_github per le
issue `approved`.

Contratto:
    args: repo?: str, status?: str|list, numbers?: list[int], limit?: int
    returns: {ok, ok_count, entries:[{repo, issue_number, title, classification,
              status, draft_reply, accepted_reply, posted_at, ...}]}
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))

from messages import get as _msg  # noqa: E402
import github_issue_qa_store as _store  # noqa: E402


def invoke(args):
    repo = (args.get("repo") or "").strip() or None
    status = args.get("status") or None
    numbers = args.get("numbers") or None
    if numbers is not None and not isinstance(numbers, list):
        numbers = [numbers]
    try:
        limit = int(args.get("limit", 200))
    except (ValueError, TypeError):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_POSITIVE_INT", arg="limit")}
    if limit < 1:
        return {"ok": False, "error": _msg("ERR_ARG_NOT_POSITIVE_INT", arg="limit")}

    try:
        entries = _store.list_records(repo=repo, status=status,
                                      numbers=numbers, limit=limit)
    except Exception as ex:
        return {"ok": False,
                "error": _msg("ERR_ARG_INVALID", arg="status", reason=type(ex).__name__)}

    return {"ok": True, "ok_count": len(entries), "entries": entries}


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
