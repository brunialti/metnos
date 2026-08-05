"""conftest.py — pytest fixture per E2E simulator.

Fixture scope=session: 1 server per intera sessione test (riusato).
Storage isolato in `tests/e2e/tmp/<run_id>/`. Teardown automatico.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

# Aggiunge `driver/` al path
_E2E_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_E2E_ROOT))

from driver import E2EClient, E2EServer  # noqa: E402
from driver import i18n as e2e_i18n  # noqa: E402


def pytest_addoption(parser):
    parser.addoption(
        "--e2e-slow", action="store_true", default=False,
        help="enable slow and external E2E scenarios",
    )
    parser.addoption(
        "--e2e-keep-tmp", action="store_true", default=False,
        help="preserve isolated server directories for failure diagnostics",
    )


def pytest_configure(config):
    if config.getoption("--e2e-slow"):
        os.environ["METNOS_E2E_RUN_SLOW"] = "1"
    if config.getoption("--e2e-keep-tmp"):
        os.environ["METNOS_E2E_KEEP_TMP"] = "1"


@pytest.fixture(scope="session")
def server() -> E2EServer:
    """Spawn server isolato (storage tmp). Una sola istanza per session."""
    srv = E2EServer.spawn()
    # Bootstrap chiavi i18n del simulatore nel DB isolato
    e2e_i18n.bootstrap()
    yield srv
    srv.shutdown(cleanup=True)


@pytest.fixture(autouse=True)
def _clean_dialog_pending(request):
    """Cleanup `dialog_pending` PRIMA di ogni test sul session server.

    Senza, dialog OAuth attivati da un test precedente (es. query google
    in chat_google_skill module-scope server NON propaga ma session server
    riceve query simili in altri test) persistono e contaminano i test
    successivi. Sintomo: response `Step 2/2 — MSG_OAUTH_PROMPT_SERVICES`
    su query non-google in chat_quality.
    """
    if "driver" not in request.fixturenames or "server" not in request.fixturenames:
        yield
        return
    server = request.getfixturevalue("server")
    import shutil
    pending = server.user_data / "get_inputs"
    if pending.exists():
        try:
            shutil.rmtree(pending)
        except OSError:
            pass
    yield


@pytest_asyncio.fixture
async def driver(server: E2EServer):
    """Client HTTP autenticato admin, una istanza per test."""
    async with E2EClient(server.url, server.admin_key) as drv:
        yield drv


@pytest.fixture(scope="session")
def corpus_db() -> Path:
    """Path al corpus.sqlite estratto. Se non esiste, lo skip-pa.

    Per generarlo: `python3 tests/e2e/corpus/extract.py`.
    """
    db = _E2E_ROOT / "corpus" / "corpus.sqlite"
    if not db.exists():
        pytest.skip(
            f"corpus.sqlite missing: {db}. "
            "Run `python3 tests/e2e/corpus/extract.py` first."
        )
    return db
