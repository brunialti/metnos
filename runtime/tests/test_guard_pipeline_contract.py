"""ADR 0177 T3+T4 (CP1·M0, 5/7/2026) — contratto della pipeline guard.

T3 — ORDINE: `GUARD_PIPELINE` è il contratto esplicito (nomi, ordine, gate v3).
Chi inserisce/sposta/rimuove un guard DEVE aggiornare `EXPECTED_PIPELINE` qui —
il fallimento del test è il momento in cui si ragiona sull'ordine, non dopo un
misroute in prod. I vincoli di posizione sono documentati accanto alla tupla in
`engine/dispatch.py`.

T4 — IDEMPOTENZA su cache-hit: `guards(guards(fw)) == guards(fw)`. I guard
girano su OGNI hit L0/L1 (ADR 0174): un guard non-idempotente accumula danno a
ogni hit dello stesso piano (rischio S3, ADR 0177). Proprietà misurata pulita
sul corpus reale (sweep 33/33, 5/7); qui un corpus INCORPORATO rappresentativo
la blocca nel tempo — full-chain E per-guard (diagnostica del violatore).
"""
from __future__ import annotations

import copy
import dataclasses
import os
import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

os.environ.setdefault("METNOS_ENGINE", "v3")


# ── T3: contratto d'ordine ────────────────────────────────────────────────

EXPECTED_PIPELINE = (
    ("overwrite_phantom_install_args", False),
    ("align_framework_objects", False),
    ("enforce_missing_clauses", False),
    ("enforce_missing_objects", True),
    ("decontaminate_reader_qualifier", True),
    ("ensure_extract_clause", True),
    ("conform_to_intent_order", True),
    ("fill_clause_args", True),
    ("resolve_store_field_refs", True),
    ("route_mail_delete_to_trash", False),
    ("route_filename_pattern_to_find", False),
    ("align_provider_client", False),
    ("scope_sink_provider_to_clause", False),
    ("degenerate_find_to_list", False),
)


def test_guard_pipeline_order_contract():
    from engine.dispatch import GUARD_PIPELINE
    got = tuple((name, v3only) for name, v3only, _fn in GUARD_PIPELINE)
    assert got == EXPECTED_PIPELINE, (
        "GUARD_PIPELINE cambiata: se è INTENZIONALE aggiorna EXPECTED_PIPELINE "
        f"qui e i vincoli di posizione in dispatch.py.\ngot={got}")


def test_guard_pipeline_callables():
    from engine.dispatch import GUARD_PIPELINE
    for name, _v3, fn in GUARD_PIPELINE:
        assert callable(fn), name


# ── T4: idempotenza su corpus incorporato ─────────────────────────────────
# Piani REALI rappresentativi (estratti da L0/turni live 5/7): shape L0-hit
# pulita, piani SPORCHI pre-guard (le riparazioni devono CONVERGERE), mono e
# compound, provider gw e locale, store-refs.

def _cases():
    from engine.types import Framework, StepSpec, Intent

    def fw(*steps):
        return Framework(steps=[StepSpec(tool=t, args=dict(a))
                                for t, a in steps]
                         + [StepSpec(tool="final_answer", args={})])

    win = "C:\\Windows\\System32\\drivers\\etc"
    return [
        # L0 id=246 (pulito): list_dirs → create; intent (list,files)
        ("l0_list_dirs_create",
         fw(("list_dirs", {"path": win, "recursive": False}),
            ("create_files_spreadsheet",
             {"title": "File in etc", "columns": ["path"],
              "from_step": 1, "client": "local"})),
         Intent(verb="list", object="files", actions=[
             {"verb": "list", "object": "files"},
             {"verb": "write", "object": "files"}]),
         f"elenca i file della cartella {win} e metti i path in uno spreadsheet"),
        # piano AVVELENATO (find fantasma install-root, no selettore)
        ("poisoned_find_install_root",
         fw(("find_files", {"client": "local",
                            "base_path": "/opt/metnos/executors/read_files"}),
            ("create_files_spreadsheet",
             {"title": "File in etc", "columns": ["path"], "client": "local"})),
         Intent(verb="list", object="files", actions=[
             {"verb": "list", "object": "files"},
             {"verb": "write", "object": "files"}]),
         f"elenca i file della cartella {win} e metti i path in uno spreadsheet"),
        # flagship KAKEBO (gw read + extract + create local)
        ("kakebo_drive_doc_to_sheet",
         fw(("find_files", {"query": "KAKEBO SPESE 2026",
                            "client": "google_workspace"}),
            ("read_files", {"name": "KAKEBO SPESE 2026",
                            "client": "google_workspace", "from_step": 1}),
            ("extract_entries", {"from_step": 2,
                                 "fields": ["data", "descrizione", "importo"]}),
            ("create_files_spreadsheet",
             {"from_step": 3,
              "columns": ["data", "descrizione", "importo"]})),
         Intent(verb="find", object="files", actions=[
             {"verb": "find", "object": "files"},
             {"verb": "read", "object": "files"},
             {"verb": "extract", "object": "entries"},
             {"verb": "create", "object": "files"}]),
         "cerca su google drive il file KAKEBO SPESE 2026 e crea uno "
         "spreadsheet con tutti i dati: data, descrizione, importo"),
        # variante SPORCA flagship (read qualificato + term inquinato, no extract)
        ("kakebo_dirty_pre_guard",
         fw(("find_files", {"query": "google drive", "top_k": 1,
                            "client": "google_workspace"}),
            ("read_files_spreadsheet", {"name": "KAKEBO SPESE 2026",
                                        "client": "google_workspace"}),
            ("create_files_spreadsheet",
             {"from_step": 2,
              "columns": ["data", "descrizione", "importo"]})),
         Intent(verb="find", object="files", actions=[
             {"verb": "find", "object": "files"},
             {"verb": "read", "object": "files"},
             {"verb": "extract", "object": "entries"},
             {"verb": "create", "object": "files"}]),
         "cerca su google drive il file KAKEBO SPESE 2026 e crea uno "
         "spreadsheet con tutti i dati: data, descrizione, importo"),
        # mail → foglio (bench compound classe)
        ("mail_to_sheet",
         fw(("read_messages", {"time_window": "last-30d"}),
            ("extract_entries", {"from_step": 1,
                                 "fields": ["importo", "data"]}),
            ("create_files_spreadsheet", {"from_step": 2,
                                          "columns": ["importo", "data"]})),
         Intent(verb="read", object="messages", actions=[
             {"verb": "read", "object": "messages"},
             {"verb": "extract", "object": "entries"},
             {"verb": "create", "object": "files"}]),
         "leggi le mail con le fatture, estrai importo e data, e mettile "
         "in un foglio"),
        # mono-azione (guard quasi tutti no-op: il no-op DEVE essere stabile)
        ("mono_get_now",
         fw(("get_now", {})),
         Intent(verb="get", object="now", actions=[]),
         "che ore sono"),
        # delete mail → move Trash (route_mail guard)
        ("mail_delete_trash",
         fw(("find_messages", {"query": "spam"}),
            ("delete_entries", {"store": "messages", "from_step": 1})),
         Intent(verb="delete", object="messages", actions=[
             {"verb": "find", "object": "messages"},
             {"verb": "delete", "object": "messages"}]),
         "trova le mail di spam e cancellale"),
    ]


def _catalog():
    from loader import load_catalog, filter_for_visibility, VISIBILITY_COMPOSER
    return filter_for_visibility(load_catalog(verify=True),
                                 VISIBILITY_COMPOSER)


def _snap(fwk):
    return dataclasses.asdict(fwk)


def test_full_chain_idempotent_on_corpus():
    from engine import dispatch as D
    cat = _catalog()
    bad = []
    for name, fwk, intent, query in _cases():
        a = D._apply_deterministic_structure_guards(
            copy.deepcopy(fwk), intent, query, cat)
        b = D._apply_deterministic_structure_guards(
            copy.deepcopy(a), intent, query, cat)
        if _snap(a) != _snap(b):
            bad.append(name)
    assert not bad, f"guard chain NON idempotente su: {bad}"


def test_each_guard_idempotent_on_corpus():
    """Per-guard: diagnostica del violatore (T4). Ogni guard applicato due
    volte allo stesso input dà lo stesso output."""
    from engine import dispatch as D
    cat = _catalog()
    bad = []
    for cname, fwk, intent, query in _cases():
        for gname, _v3, fn in D.GUARD_PIPELINE:
            x = fn(copy.deepcopy(fwk), intent, query, cat)
            y = fn(copy.deepcopy(x), intent, query, cat)
            if _snap(x) != _snap(y):
                bad.append(f"{gname}@{cname}")
    assert not bad, f"guard NON idempotenti: {bad}"
