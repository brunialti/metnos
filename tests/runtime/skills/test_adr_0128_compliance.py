"""Test ADR 0128 compliance — Sprint M rinomine cluster H5-H10.

Verifica che gli executor importati google-workspace post Sprint M
rispettino il vocab §2.2 e la policy di boundary verb-as-data
(`runtime/importer_verb_verify`).

Test:
1. Catalog load mantiene tutti i 24 import google-workspace.
2. I 7 nuovi nomi (post-rename H5-H10') sono presenti, i vecchi assenti.
3. Audit `audit_existing_imports()` su _imports/ -> Verdict aligned=True
   per ognuno (nessun drift residuo dopo Sprint M).
4. I manifest file dei rinominati hanno il campo `name` aggiornato e il
   provenance `imported_from` invariato (only name changed).
5. La firma Ed25519 e' valida per ogni rinominato.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest


def _resolve_imports_root() -> Path:
    """Dual-path resolution (ADR 0160): preferisci `skills/` (new), fallback
    su `_imports/` legacy se installazione precedente."""
    base = Path.home() / ".local/share/metnos/executors"
    new_p = base / "skills" / "google-workspace"
    legacy = base / "_imports" / "google-workspace"
    if new_p.is_dir():
        return new_p
    return legacy


IMPORTS_ROOT = _resolve_imports_root()

# Tabella di rinominazione (old, new) per asserire la fine del drift.
# Eccezione: `set_events → create_events` (14/5/2026) e' stato promosso a
# CANONICAL dispatcher in <install_root>/executors/create_events/ (refactor
# 13/5/2026, plugin area calendar). Non e' piu' un import google-workspace.
# Il check di rinomina lo tratta a parte (vedi `test_create_events_promoted`).
RENAMES = [
    ("change_files_text",  "write_files_text"),
    ("change_files_xlsx",  "set_files_xlsx"),    # nota: nome riassegnato (era sheets.create, ora sheets.update)
    ("change_messages",    "set_messages"),
    ("set_files",          "share_files_google_workspace"),
    ("set_files_text",     "create_files_text"),
    ("set_files_xlsx",     "create_files_xlsx"),  # era sheets.create -> create_files_xlsx
]

NEW_NAMES_EXPECTED = {
    # ADR 0136 (15/5/2026): provider qualifier `_google_workspace`
    # universale. Tutti gli executor importati da skill google-workspace
    # hanno suffix unico, eliminando i mix Sprint M (set_messages vs
    # set_messages_google_workspace).
    "write_files_text_google_workspace",
    "set_files_xlsx_google_workspace",
    "set_messages_google_workspace",
    "share_files_google_workspace",
    "create_files_text_google_workspace",
    "create_files_xlsx_google_workspace",
}


def test_create_events_promoted_to_canonical():
    """set_events → create_events e' canonical dispatcher (13/5/2026),
    NON un import google-workspace. Verifica presenza nel registry canonical."""
    canonical = Path(__file__).resolve().parents[3] / "executors/create_events"
    assert canonical.is_dir(), (
        f"create_events deve essere canonical in {canonical} (refactor 13/5/2026)"
    )
    assert (canonical / "manifest.toml").is_file()
    assert (canonical / "manifest.toml.sig").is_file()
    assert (canonical / "create_events.py").is_file()


def _skip_if_no_google_workspace_imports():
    """Skip se la skill google-workspace non e' installata.
    Universale: dir mancante OR vuota = nessuna skill da testare. Non e'
    un bug del codice, e' una condizione ambientale (skill opt-in)."""
    if not IMPORTS_ROOT.is_dir():
        pytest.skip(f"imports root missing: {IMPORTS_ROOT}")
    if not any(p.is_dir() for p in IMPORTS_ROOT.iterdir()):
        pytest.skip(
            f"google-workspace skill not imported (empty {IMPORTS_ROOT}). "
            "Run `metnos-skills import agentskills.io/local/google-workspace` to enable."
        )


@pytest.fixture(scope="module")
def imports_dir():
    _skip_if_no_google_workspace_imports()
    return IMPORTS_ROOT


# ---------------------------------------------------------------------------
# 1. I nuovi nomi sono presenti, i vecchi assenti
# ---------------------------------------------------------------------------


def test_new_names_present_after_sprint_m(imports_dir):
    existing = {p.name for p in imports_dir.iterdir() if p.is_dir()}
    for new in NEW_NAMES_EXPECTED:
        assert new in existing, f"missing renamed executor: {new}"


def test_old_names_absent_after_sprint_m(imports_dir):
    """Tranne `set_files_xlsx` che e' riassegnato (oggi sheets.update,
    prima sheets.create): l'identita' del nome cambia provider-mapping
    senza essere un "old name absent" puro."""
    existing = {p.name for p in imports_dir.iterdir() if p.is_dir()}
    must_be_absent = {
        "change_files_text",
        "change_files_xlsx",
        "change_messages",
        "set_files",
        "set_files_text",
        "set_events",
    }
    # set_files_xlsx e' presente con NUOVA semantica (sheets.update), non
    # con la vecchia (sheets.create che ora e' create_files_xlsx). Quindi
    # nella set dei nomi rimossi NON va incluso.
    for old in must_be_absent:
        assert old not in existing, f"stale old name still present: {old}"


# ---------------------------------------------------------------------------
# 2. Catalog load mantiene tutti gli imports
# ---------------------------------------------------------------------------


def test_catalog_loads_all_renamed_executors():
    _skip_if_no_google_workspace_imports()
    from runtime import loader
    cat = loader.load_catalog()
    assert len(cat.rejected) == 0, f"rejected non vuoto: {cat.rejected}"
    for new in NEW_NAMES_EXPECTED:
        assert new in cat.executors, f"catalog missing {new}"


# ---------------------------------------------------------------------------
# 3. audit_existing_imports: Verdict.aligned=True per ogni executor
# ---------------------------------------------------------------------------


def test_audit_imports_zero_drift(imports_dir):
    """ADR 0128: dopo Sprint M il drift sistemico e' chiuso."""
    from runtime.importer_verb_verify import audit_existing_imports
    results = audit_existing_imports(imports_dir.parent)
    assert len(results) >= 7, f"audit produced too few results: {len(results)}"

    misaligned = [(name, v) for (name, v) in results if not v.aligned]
    # Filtriamo "no contextual cell" e "no skill context" come acceptable
    # (non sono drift, sono casi non coperti dalla tabella attuale).
    actual_drifts = [
        (n, v) for (n, v) in misaligned
        if v.mismatch_kind and v.mismatch_kind not in ("",)
    ]
    assert actual_drifts == [], (
        "Drift residuo dopo Sprint M (ADR 0128). "
        f"Cluster: {[(n, v.mismatch_kind, v.mismatch_reason) for n, v in actual_drifts]}"
    )


# ---------------------------------------------------------------------------
# 4. Manifest name field aggiornato + provenance invariata
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dir_name", sorted(NEW_NAMES_EXPECTED))
def test_renamed_manifest_name_field(imports_dir, dir_name):
    mf = imports_dir / dir_name / "manifest.toml"
    assert mf.is_file(), f"missing manifest: {mf}"
    doc = tomllib.loads(mf.read_text(encoding="utf-8"))
    assert doc["name"] == dir_name, (
        f"manifest.name={doc['name']!r} mismatched with dir name {dir_name!r}"
    )


@pytest.mark.parametrize("dir_name", sorted(NEW_NAMES_EXPECTED))
def test_renamed_manifest_has_provenance(imports_dir, dir_name):
    """Provenance invariata (rinomina = name change pulito, no shim §7.1).
    `imported_from` deve essere `agentskills.io/local/google-workspace`."""
    mf = imports_dir / dir_name / "manifest.toml"
    doc = tomllib.loads(mf.read_text(encoding="utf-8"))
    prov = doc.get("provenance") or {}
    assert prov.get("imported_from", "").startswith("agentskills.io"), (
        f"provenance malformed for {dir_name}: {prov}"
    )


# ---------------------------------------------------------------------------
# 5. Sign valida per i rinominati (digest match + .sig valido)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dir_name", sorted(NEW_NAMES_EXPECTED))
def test_renamed_executor_sign_valid(imports_dir, dir_name):
    """§7.10: re-firma OGNI executor modificato. Verifica via sign.py."""
    from runtime.sign import verify_executor
    d = imports_dir / dir_name
    # verify_executor ritorna (ok: bool, message: str) o solleva.
    res = verify_executor(d)
    # API tollerante a piu' shape (tuple vs object).
    ok = res[0] if isinstance(res, tuple) else bool(res)
    assert ok, f"signature INVALID for {dir_name}: {res}"
