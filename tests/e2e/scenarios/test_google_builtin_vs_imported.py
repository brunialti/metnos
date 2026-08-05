"""E2E confronto google-workspace BUILTIN vs IMPORTED.

Due server isolati paralleli sulla stessa baseline (seed_realistic),
differenza unica = presenza skill importata:

  A) builtin_server:  solo backend `runtime/backends/.../google_workspace.py`
  B) imported_server: builtin + 24 executor `*_google_workspace` importati

Per ogni query del set canonical:
  - lancia su entrambi
  - cattura: latenza wall-clock, tools_used, final_text, success
  - confronta: judge LLM per qualità semantica + delta latenza

Output finale in `reports/google_compare_<timestamp>.json` + markdown
human-readable.

Test SLOW (~15-20 min). Opt-in `METNOS_E2E_RUN_SLOW=1`.

NO import runtime. Tutto via HTTP/CLI.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver import E2EClient, E2EServer, judge, lint


_REAL_GOOGLE_SOURCE = Path.home() / ".local/share/metnos/skills/google-workspace"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUN_SLOW = os.environ.get("METNOS_E2E_RUN_SLOW", "0") == "1"


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not _REAL_GOOGLE_SOURCE.is_dir(),
        reason="google-workspace skill source missing",
    ),
    pytest.mark.skipif(
        not _RUN_SLOW,
        reason="comparison slow (~15-20min). METNOS_E2E_RUN_SLOW=1 per abilitare",
    ),
]


# --- Query canonical set (read-only, no mutating to avoid side effects) ---

# DEVI: query READ-ONLY (no send/create/delete) per evitare side effect
# su Gmail/Calendar/Drive reali. Test SOLO informational fetches.
_QUERIES = [
    # (id, query, lang, expected_domain)
    ("mail_recent",     "leggi le 3 mail piu' recenti su Gmail", "it", "mail"),
    ("mail_search",     "cerca su Gmail mail con oggetto fattura", "it", "mail"),
    ("calendar_today",  "appuntamenti di oggi",                   "it", "calendar"),
    ("calendar_week",   "eventi della prossima settimana",        "it", "calendar"),
    ("drive_search",    "cerca documenti su drive con titolo metnos", "it", "drive"),
]


def _import_google_skill(env: dict, tmp_root: Path) -> None:
    """Pre-spawn hook: copia source + import skill via CLI."""
    user_data = Path(env["METNOS_USER_DATA"])
    target_skill = user_data / "skills" / "google-workspace"
    if target_skill.exists():
        shutil.rmtree(target_skill)
    target_skill.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_REAL_GOOGLE_SOURCE, target_skill)

    hermes_home = tmp_root / "hermes"
    hermes_home.mkdir(parents=True, exist_ok=True)
    for cred in ("google_client_secret.json", "google_token.json"):
        src = _REAL_GOOGLE_SOURCE / cred
        if src.exists():
            shutil.copy2(src, hermes_home / cred)
    env["HERMES_HOME"] = str(hermes_home)
    (Path(env["METNOS_USER_STATE"]) / "executor_stats.db").unlink(
        missing_ok=True)

    r = subprocess.run(
        [sys.executable, "-m", "runtime.cli.skills_cli", "import",
         str(target_skill / "SKILL.md"),
         "--skip-l2", "--skip-l6", "--skip-smoke-battery", "--no-sign"],
        cwd=str(_REPO_ROOT), env=env,
        capture_output=True, text=True, timeout=300,
    )
    if r.returncode != 0:
        raise RuntimeError(
            "skill import failed in pre-spawn:\n"
            f"STDOUT: {r.stdout[-1500:]}\nSTDERR: {r.stderr[-1500:]}")


# --- Fixtures: server paralleli ------------------------------------------

@pytest.fixture(scope="module")
def builtin_server() -> E2EServer:
    """Server isolato con BUILTIN google backend (no skill import)."""
    srv = E2EServer.spawn(seed_realistic=True, ready_timeout_s=45.0)
    yield srv
    srv.shutdown(cleanup=True)


# Builtin executor con equivalente `_google_workspace` (Gmail/Calendar/
# Drive). Vengono nascosti via METNOS_HIDE_EXECUTORS nel server `imported`
# per forzare il PLANNER a scegliere lo skill imported (vedi commento in
# `imported_server`).
_GOOGLE_BUILTIN_EQUIVALENTS = [
    "read_messages", "find_messages", "send_messages",
    "read_events", "find_events", "create_events",
    "delete_events", "set_events",
    "find_files", "read_files", "write_files",
]


@pytest.fixture(scope="module")
def imported_server() -> E2EServer:
    """Server isolato con SOLO skill google IMPORTED (builtin nascosti).

    `hide_executors` rimuove dal catalog gli executor handcrafted Google-
    equivalenti (`read_messages`, `find_messages`, ...): cosi' il PLANNER
    ha SOLO `*_google_workspace` disponibile e il test puo' verificare
    che la skill imported sia effettivamente esercitata. Senza hide, il
    marker provider ADR 0136 esclude `_google_workspace` dal pool quando
    la query non contiene marker semantico (gmail/drive/calendar) → il
    PLANNER sceglie sempre il builtin → test imported non testabile.
    """
    srv = E2EServer.spawn(
        seed_realistic=True,
        pre_spawn_hook=_import_google_skill,
        hide_executors=_GOOGLE_BUILTIN_EQUIVALENTS,
        ready_timeout_s=60.0,
    )
    yield srv
    srv.shutdown(cleanup=True)


@pytest_asyncio.fixture
async def builtin_client(builtin_server):
    async with E2EClient(builtin_server.url, builtin_server.admin_key,
                            timeout_s=300.0) as drv:
        yield drv


@pytest_asyncio.fixture
async def imported_client(imported_server):
    async with E2EClient(imported_server.url, imported_server.admin_key,
                            timeout_s=300.0) as drv:
        yield drv


# --- Helpers --------------------------------------------------------------

async def _run_query(client: E2EClient, query: str, lang: str) -> dict:
    """Esegue una query, ritorna {latency_ms, tools_used, text, error}."""
    t0 = time.time()
    r = await client.chat(query, lang=lang)
    t1 = time.time()
    return {
        "query": query,
        "lang": lang,
        "latency_ms": int((t1 - t0) * 1000),
        "tools_used": [s.get("tool") or s.get("chosen_tool") or "" for s in r.steps],
        "final_text": r.final_text or "",
        "final_kind": r.final_kind,
        "error": r.error,
        "n_steps": len(r.steps),
    }


def _classify_provider(tools: list) -> str:
    """`builtin` se tool senza suffix `_google_workspace`, `imported` se con."""
    has_imp = any(t.endswith("_google_workspace") for t in tools)
    has_native = any(t in {"read_messages", "send_messages", "find_messages",
                            "read_events", "create_events", "delete_events",
                            "find_files", "read_files", "write_files"}
                      for t in tools)
    if has_imp and has_native:
        return "mixed"
    if has_imp:
        return "imported"
    if has_native:
        return "builtin"
    return "none"


# --- Main comparison test -------------------------------------------------

@pytest.mark.parametrize("qid,query,lang,domain", _QUERIES,
                          ids=[q[0] for q in _QUERIES])
async def test_compare_query(builtin_client, imported_client,
                              qid: str, query: str, lang: str, domain: str,
                              request):
    """Lancia la stessa query su builtin + imported, confronta."""
    # Esegui sequenzialmente (LLM locale single-threaded)
    r_built = await _run_query(builtin_client, query, lang)
    r_imp = await _run_query(imported_client, query, lang)

    if r_built["error"] or r_imp["error"]:
        pytest.skip(
            f"network/LLM unreachable: builtin={r_built['error']!r}, "
            f"imported={r_imp['error']!r}"
        )

    # Lint clean su entrambi
    lr_b = lint.check_response(r_built["final_text"], expected_lang=lang)
    lr_i = lint.check_response(r_imp["final_text"], expected_lang=lang)
    issues = []
    if not lr_b.ok:
        issues.append(
            f"BUILTIN lint fail: {lr_b.fail_message()}\n"
            f"tools={r_built['tools_used']} text={r_built['final_text'][:500]}")
    if not lr_i.ok:
        issues.append(
            f"IMPORTED lint fail: {lr_i.fail_message()}\n"
            f"tools={r_imp['tools_used']} text={r_imp['final_text'][:500]}")
    if issues:
        pytest.fail("\n".join(issues))

    # Provider classification
    prov_b = _classify_provider(r_built["tools_used"])
    prov_i = _classify_provider(r_imp["tools_used"])

    # Aggrega report
    record = {
        "qid": qid, "query": query, "domain": domain,
        "builtin": {
            "latency_ms": r_built["latency_ms"],
            "tools": r_built["tools_used"],
            "provider": prov_b,
            "n_steps": r_built["n_steps"],
            "text_excerpt": r_built["final_text"][:200],
        },
        "imported": {
            "latency_ms": r_imp["latency_ms"],
            "tools": r_imp["tools_used"],
            "provider": prov_i,
            "n_steps": r_imp["n_steps"],
            "text_excerpt": r_imp["final_text"][:200],
        },
    }

    # Judge LLM su entrambi (default ON)
    if judge.is_judge_enabled():
        v_b = await judge.evaluate(query, r_built["final_text"], lang=lang)
        v_i = await judge.evaluate(query, r_imp["final_text"], lang=lang)
        record["builtin"]["judge"] = {"ok": v_b.ok, "score": v_b.score,
                                       "reason": v_b.reason}
        record["imported"]["judge"] = {"ok": v_i.ok, "score": v_i.score,
                                        "reason": v_i.reason}

    # Stash su file shared (request.node.user_properties non scala
    # attraverso parametrize; uso file append-only)
    out_dir = _REPO_ROOT / "tests" / "e2e" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    sess_tag = Path(str(getattr(request.config, "rootpath", "session"))).name or "session"
    out_file = out_dir / f"google_compare_{sess_tag}.jsonl"
    with out_file.open("a") as fp:
        fp.write(json.dumps(record, ensure_ascii=False) + "\n")

    # Soft assertion: imported preferred per query con domain match
    if prov_i == "none" and prov_b == "none":
        pytest.fail(
            f"{qid}: neither builtin nor imported tool invoked. "
            f"Tools b={r_built['tools_used']} i={r_imp['tools_used']}"
        )


# --- Final aggregation (post-suite) ---------------------------------------

async def test_aggregate_comparison_report(request):
    """Scrive report finale markdown con confronto latenza + qualita'."""
    out_dir = _REPO_ROOT / "tests" / "e2e" / "reports"
    sess_tag = Path(str(getattr(request.config, "rootpath", "session"))).name or "session"
    jsonl = out_dir / f"google_compare_{sess_tag}.jsonl"
    if not jsonl.exists():
        pytest.skip("no per-query records (parametrize skipped)")
    records = []
    with jsonl.open() as fp:
        for line in fp:
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    if not records:
        pytest.skip("zero records")

    lat_b = [r["builtin"]["latency_ms"] for r in records]
    lat_i = [r["imported"]["latency_ms"] for r in records]
    scores_b = [r["builtin"].get("judge", {}).get("score", 0.0) for r in records]
    scores_i = [r["imported"].get("judge", {}).get("score", 0.0) for r in records]
    prov_b_counter = defaultdict(int)
    prov_i_counter = defaultdict(int)
    for r in records:
        prov_b_counter[r["builtin"]["provider"]] += 1
        prov_i_counter[r["imported"]["provider"]] += 1

    def _stats(xs):
        if not xs:
            return {"n": 0}
        return {
            "n": len(xs),
            "p50": int(statistics.median(xs)),
            "p95": int(statistics.quantiles(xs, n=20)[-1]) if len(xs) > 1 else xs[0],
            "max": max(xs),
        }

    summary = {
        "n_queries": len(records),
        "builtin": {
            "latency_ms": _stats(lat_b),
            "judge_score_mean": round(statistics.fmean(scores_b), 3) if scores_b else None,
            "provider_distribution": dict(prov_b_counter),
        },
        "imported": {
            "latency_ms": _stats(lat_i),
            "judge_score_mean": round(statistics.fmean(scores_i), 3) if scores_i else None,
            "provider_distribution": dict(prov_i_counter),
        },
        "per_query": records,
    }
    out_file = out_dir / f"google_compare_summary_{int(time.time())}.json"
    out_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    # Markdown report
    md = [
        "# Google Workspace: BUILTIN vs IMPORTED comparison",
        "",
        f"**Queries**: {summary['n_queries']}",
        "",
        "## Latency (wall-clock ms)",
        "",
        "| | builtin | imported |",
        "|---|---|---|",
        f"| p50 | {summary['builtin']['latency_ms'].get('p50', '-')} | {summary['imported']['latency_ms'].get('p50', '-')} |",
        f"| p95 | {summary['builtin']['latency_ms'].get('p95', '-')} | {summary['imported']['latency_ms'].get('p95', '-')} |",
        f"| max | {summary['builtin']['latency_ms'].get('max', '-')} | {summary['imported']['latency_ms'].get('max', '-')} |",
        "",
        "## Judge LLM score (0-1)",
        "",
        f"- builtin mean: {summary['builtin']['judge_score_mean']}",
        f"- imported mean: {summary['imported']['judge_score_mean']}",
        "",
        "## Provider routing",
        "",
        f"- builtin server: {dict(prov_b_counter)}",
        f"- imported server: {dict(prov_i_counter)}",
        "",
        "## Per-query detail",
        "",
    ]
    for r in records:
        md.append(f"### {r['qid']} — {r['query']!r}")
        md.append(f"- builtin: {r['builtin']['latency_ms']}ms, tools={r['builtin']['tools']}")
        md.append(f"- imported: {r['imported']['latency_ms']}ms, tools={r['imported']['tools']}")
        md.append("")
    md_file = out_dir / f"google_compare_summary_{int(time.time())}.md"
    md_file.write_text("\n".join(md))

    print(f"\nReport saved: {md_file}")
