"""E2E chat con skill google-workspace REALE importata pre-spawn.

Pipeline test:
  1. Fixture custom: tmp dir + copia source google-workspace (incl.
     credenziali google_client_secret.json + google_token.json) +
     import via CLI subprocess → 24 executor in executors/skills/
  2. Spawn server con env tmp → catalog include `*_google_workspace`
  3. Chat query naturale → planner sceglie l'executor importato compatibile
     → bridge gws/google_api.py → API Google reale con token OAuth
  4. Lint clean + judge LLM ok

NB: richiede credenziali google valide in ~/.local/share/metnos/skills/
google-workspace/. Token OAuth con scope mail/calendar/drive.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver import E2EClient, E2EServer, judge, lint


_REAL_GOOGLE_SOURCE = Path.home() / ".local/share/metnos/skills/google-workspace"
_RUN_SLOW = os.environ.get("METNOS_E2E_RUN_SLOW", "0") == "1"
_REPO_ROOT = Path(__file__).resolve().parents[3]


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not _REAL_GOOGLE_SOURCE.is_dir(),
        reason="google-workspace skill source missing in ~/.local/share/metnos/skills/",
    ),
    pytest.mark.skipif(
        not _RUN_SLOW,
        reason="chat google e' slow (~30-60s/query). Abilita con METNOS_E2E_RUN_SLOW=1",
    ),
]


def _import_google_skill(env: dict, tmp_root: Path) -> None:
    """Pre-spawn hook: copia google-workspace skill source + import via CLI.

    Idempotente. Lascia lo storage tmp pronto per il server boot con
    catalog completo (handcrafted + 24 imported google_workspace).
    """
    user_data = Path(env["METNOS_USER_DATA"])
    target_skill = user_data / "skills" / "google-workspace"
    if target_skill.exists():
        shutil.rmtree(target_skill)
    target_skill.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_REAL_GOOGLE_SOURCE, target_skill)

    # Bridge scripts della skill cercano `~/.hermes/google_token.json`.
    # Simula HERMES_HOME = tmp_root cosi' bridge usa credenziali isolate.
    hermes_home = tmp_root / "hermes"
    hermes_home.mkdir(parents=True, exist_ok=True)
    for cred in ("google_client_secret.json", "google_token.json"):
        src = _REAL_GOOGLE_SOURCE / cred
        if src.exists():
            shutil.copy2(src, hermes_home / cred)
    env["HERMES_HOME"] = str(hermes_home)

    # Il test misura l'import corrente, non lo storico di aging del server
    # live: statistiche copiate possono archiviare executor provider mai usati
    # prima e rendere il catalogo della fixture dipendente dal passato.
    (Path(env["METNOS_USER_STATE"]) / "executor_stats.db").unlink(
        missing_ok=True)

    # Import via CLI subprocess (env tmp)
    r = subprocess.run(
        [sys.executable, "-m", "runtime.cli.skills_cli", "import",
         str(target_skill / "SKILL.md"),
         "--skip-l2", "--skip-smoke-battery", "--no-sign"],
        cwd=str(_REPO_ROOT), env=env,
        capture_output=True, text=True, timeout=300,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"google skill import failed during pre-spawn (rc={r.returncode}):\n"
            f"STDOUT: {r.stdout[-2000:]}\n"
            f"STDERR: {r.stderr[-1000:]}"
        )


# Builtin executor con equivalente `_google_workspace`. Nascosti per
# forzare il PLANNER a usare la skill imported (test asserisce
# expected_tool con suffix `_google_workspace`).
_GOOGLE_BUILTIN_EQUIVALENTS = [
    "read_messages", "find_messages", "send_messages",
    "read_events", "find_events", "create_events",
    "delete_events", "set_events",
    "find_files", "read_files", "write_files",
]


@pytest.fixture(scope="module")
def google_server() -> E2EServer:
    """Server isolato con skill google-workspace pre-importata, builtin
    Google-equivalenti nascosti, seed realistic.

    `hide_executors` rimuove dal catalog `read_messages` & co cosi' il
    PLANNER ha solo `*_google_workspace` come opzione → il test puo'
    verificare l'instradamento alla skill imported.
    """
    srv = E2EServer.spawn(
        seed_realistic=True,
        pre_spawn_hook=_import_google_skill,
        hide_executors=_GOOGLE_BUILTIN_EQUIVALENTS,
        ready_timeout_s=45.0,
    )
    yield srv
    srv.shutdown(cleanup=True)


@pytest_asyncio.fixture
async def driver(google_server):
    async with E2EClient(google_server.url, google_server.admin_key,
                            timeout_s=300.0) as drv:
        yield drv


# --- Query reali --------------------------------------------------------

_QUERIES = [
    # (query, lang, hint executor atteso)
    ("cerca file denominati E2E su Google Drive", "it",
     "find_files_google_workspace"),
    ("cerca mail da bookings", "it", "find_messages_google_workspace"),
    ("appuntamenti di domani in calendario", "it", "read_events_google_workspace"),
]


@pytest.mark.parametrize("query,lang,expected_tool", _QUERIES,
                          ids=[q for q, _, _ in _QUERIES])
async def test_chat_with_google_skill(driver, query: str, lang: str,
                                        expected_tool: str):
    """Chat reale: planner deve scegliere l'executor `_google_workspace`
    appropriato e produrre risposta coerente."""
    r = await driver.chat(query, lang=lang)
    if r.error:
        pytest.skip(f"chat unreachable: {r.error}")

    # A Workspace account may authenticate correctly while an individual API
    # capability is disabled (for example Gmail on a non-Mail account).  The
    # routing assertion remains mandatory; only the external capability check
    # is environmental and therefore skippable.
    tools_called = [s.get("tool") or s.get("chosen_tool") or "" for s in r.steps]
    expected_steps = [s for s in r.steps
                      if (s.get("tool") or s.get("chosen_tool")) == expected_tool]
    if expected_steps:
        if expected_steps[-1].get("error_class") == "capability_missing":
            pytest.skip(
                f"provider capability unavailable for `{expected_tool}`")

    text = r.final_text or (r.final_html or "")

    # Lint clean (no leak, no traceback, lang OK)
    lint_result = lint.check_response(text, expected_lang=lang)
    if not lint_result.ok:
        pytest.fail(
            f"lint failed for «{query}»:\n{lint_result.fail_message()}\n"
            f"answer: {text[:300]}"
        )

    # Pipeline shape valida
    shape = lint.check_pipeline_shape(r.steps)
    if not shape.ok:
        pytest.fail(f"pipeline shape invalid: {shape.fail_message()}")

    # Executor atteso (heuristic: il tool deve essere usato in almeno uno step)
    if expected_tool not in tools_called:
        pytest.fail(
            f"expected tool `{expected_tool}` not invoked for «{query}». "
            f"Tools used: {tools_called}"
        )

    # Judge LLM (default ON via METNOS_E2E_LLM_JUDGE=1)
    if judge.is_judge_enabled():
        verdict = await judge.evaluate(query, text, lang=lang)
        if not verdict.ok:
            pytest.fail(
                f"judge rejected «{query}»:\n"
                f"  score={verdict.score:.2f} reason={verdict.reason}\n"
                f"  answer: {text[:300]}"
            )
