#!/usr/bin/env python3
"""Mock CLI per skill_mock-e2e — output deterministico, zero side-effect.

Sub-commands: read | find | create

Usato dal simulatore E2E per testare la pipeline di import senza chiamate
esterne. Output: JSON su stdout.
"""
import argparse
import json
import sys


def cmd_read(args) -> dict:
    return {
        "ok": True,
        "entries": [
            {"id": i, "name": f"widget_{i}", "kind": args.kind or "A"}
            for i in range(1, min(args.limit, 10) + 1)
        ],
    }


def cmd_find(args) -> dict:
    q = (args.query or "").lower()
    return {
        "ok": True,
        "entries": [
            {"id": i, "name": f"{q}_{i}", "match_score": 1.0 - i * 0.1}
            for i in range(1, min(args.limit, 5) + 1)
        ],
    }


def cmd_create(args) -> dict:
    new_id = abs(hash(args.name)) % 10000
    return {
        "ok": True,
        "results": [{
            "id": new_id,
            "name": args.name,
            "kind": args.kind or "B",
            "_undo": {"ids": [new_id]},
        }],
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mock_cli.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("read")
    pr.add_argument("--kind", default="A")
    pr.add_argument("--limit", type=int, default=10)
    pf = sub.add_parser("find")
    pf.add_argument("--query", default="")
    pf.add_argument("--limit", type=int, default=5)
    pc = sub.add_parser("create")
    pc.add_argument("--name", required=True)
    pc.add_argument("--kind", default="B")
    args = p.parse_args(argv)
    handler = {"read": cmd_read, "find": cmd_find, "create": cmd_create}[args.cmd]
    print(json.dumps(handler(args), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
