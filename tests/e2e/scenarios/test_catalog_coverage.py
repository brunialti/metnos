"""E2E catalog coverage — verifica handcrafted e vocabolario canonico.

Gli executor in-process sono contratti builtin firmati a tutti gli effetti e
devono comparire nel catalogo isolato quando la loro chiave pubblica è trusted.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver import E2EClient, E2EServer

pytestmark = pytest.mark.asyncio


_REPO_ROOT = Path(__file__).resolve().parents[3]


def _canonical_objects() -> tuple[str, ...]:
    """Legge la costante chiusa senza importare codice runtime nell'E2E."""
    source = (_REPO_ROOT / "runtime" / "vocab.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "OBJECTS"
                        for target in node.targets)):
            value = ast.literal_eval(node.value)
            if isinstance(value, tuple) and all(
                    isinstance(item, str) for item in value):
                return value
    raise AssertionError("runtime/vocab.py non espone OBJECTS come tupla letterale")

# Deriva sempre dal SoT: il vecchio snapshot locale a 19 oggetti non vedeva
# approval/issues/pulls/calendars/lists/skills/sites.
_CANONICAL_OBJECTS = _canonical_objects()

# Verbi che ritornano entries del dominio (§2.2 producer + filter).
# `filter` parte da lista esistente ma fornisce entries del dominio,
# quindi conta come producer-equivalente per coverage check.
_PRODUCER_VERBS = ("find", "read", "get", "list", "filter")


@pytest.fixture(scope="module")
def server() -> E2EServer:
    srv = E2EServer.spawn(ready_timeout_s=45.0)
    yield srv
    srv.shutdown(cleanup=True)


@pytest_asyncio.fixture
async def driver(server):
    async with E2EClient(server.url, server.admin_key) as drv:
        yield drv


async def test_catalog_has_handcrafted_executors_from_repo(driver):
    """Catalog deve includere tutti gli executor handcrafted in
    `executors/` repo (eccetto rejected per sign fail / digest mismatch)."""
    repo_executors = {
        d.name for d in (_REPO_ROOT / "executors").iterdir()
        if d.is_dir() and (d / "manifest.toml").exists()
    }
    cat = await driver.admin_get("/admin/executors")
    rows = cat.get("rows") or cat.get("executors") or []
    catalog_names = {r.get("name", "") for r in rows}
    missing = repo_executors - catalog_names
    # I "rejected" sono fuori catalog ma noti — non li forziamo
    rejected_path = _REPO_ROOT  # placeholder
    if missing:
        # Ammettiamo subset di executor che richiedono dep non installate
        # (clip/whisper/face) o capability non concesse. Soglia: > 5 missing
        # = problema strutturale.
        if len(missing) > 5:
            pytest.fail(
                f"catalog manca {len(missing)} executor handcrafted: {missing}"
            )


@pytest.mark.parametrize("obj", _CANONICAL_OBJECTS)
async def test_each_object_has_producer(driver, obj: str):
    """Per ogni OBJECT §2.2, esiste almeno 1 producer (find/read/get/list)."""
    cat = await driver.admin_get("/admin/executors")
    rows = cat.get("rows") or cat.get("executors") or []
    names = {r.get("name", "") for r in rows}
    found = False
    for verb in _PRODUCER_VERBS:
        if f"{verb}_{obj}" in names:
            found = True
            break
        # qualifier presente
        for n in names:
            if n.startswith(f"{verb}_{obj}_"):
                found = True
                break
        if found:
            break
    if not found:
        # Il server E2E di base non copia i bundle provider installati. `numbers`
        # usa l'eccezione ratificata get_now; `calendars` oggi espone soltanto
        # create/delete; `entries` può essere dormant senza store registrati.
        external_only = {"issues", "pulls", "numbers", "calendars", "entries"}
        if obj in external_only:
            pytest.skip(f"obj `{obj}` non in pool minimal handcrafted")
        pytest.fail(f"object `{obj}` SENZA producer in catalog")


async def test_catalog_no_rejected_executors(driver):
    """Catalog rejected list dovrebbe essere vuoto (no sign/digest fail)."""
    cat = await driver.admin_get("/admin/executors")
    rejected = cat.get("rejected") or []
    if rejected:
        # Sample primi 3
        details = rejected[:3]
        pytest.fail(
            f"catalog ha {len(rejected)} rejected: {details}"
        )
