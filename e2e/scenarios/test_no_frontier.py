"""E2E guard: nessun test chiama il tier frontier (Anthropic/OpenAI a
pagamento). Verificato via env `METNOS_DISABLE_FRONTIER=1` settato
automaticamente da `E2EServer.spawn()`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver import E2EServer

pytestmark = pytest.mark.asyncio


async def test_server_env_disables_frontier():
    """Lo spawn iniettato `METNOS_DISABLE_FRONTIER=1` nell'env del
    subprocess server. Tutti i call site frontier nel runtime saltano
    quando questa env e' settata."""
    srv = E2EServer.spawn(ready_timeout_s=30.0)
    try:
        # Leggi la env del processo server (Linux: /proc/<pid>/environ)
        pid = srv.process.pid
        env_file = Path(f"/proc/{pid}/environ")
        if not env_file.exists():
            pytest.skip("non-Linux: /proc/<pid>/environ non disponibile")
        raw = env_file.read_bytes()
        entries = raw.decode("utf-8", errors="replace").split("\0")
        env_dict = {}
        for e in entries:
            if "=" in e:
                k, _, v = e.partition("=")
                env_dict[k] = v
        assert env_dict.get("METNOS_DISABLE_FRONTIER") == "1", (
            "METNOS_DISABLE_FRONTIER non settato (test potrebbe spendere $)"
        )
    finally:
        srv.shutdown()


async def test_repo_no_pending_frontier_calls():
    """Audit del repo: ogni call site `tier=\"frontier\"` deve essere
    preceduto da check `_front_skip_reason`. Pattern §7.3 universale."""
    import re
    repo = Path(__file__).resolve().parents[2]
    pattern = re.compile(r'tier=["\']frontier["\']')
    issues = []
    for f in (repo / "runtime").rglob("*.py"):
        if f.name.startswith("test_"):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in pattern.finditer(text):
            # Skip comment lines
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_prefix = text[line_start:m.start()]
            if line_prefix.lstrip().startswith("#"):
                continue
            # Skip docstring (heuristic: triple-quote count odd before match)
            preceding = text[:m.start()]
            if preceding.count('"""') % 2 == 1:
                continue
            # Guard check nei 4000 char precedenti (window ampio per call
            # site annidati in blocchi try/if larghi)
            start = max(0, m.start() - 4000)
            ctx = text[start:m.end()]
            if ("METNOS_DISABLE_FRONTIER" not in ctx
                    and "_front_skip_reason" not in ctx):
                line_no = text[:m.start()].count("\n") + 1
                issues.append(f"{f.relative_to(repo)}:{line_no}: "
                              f"tier=frontier senza guard METNOS_DISABLE_FRONTIER")
    if issues:
        pytest.fail("Frontier guards mancanti:\n  " + "\n  ".join(issues))
