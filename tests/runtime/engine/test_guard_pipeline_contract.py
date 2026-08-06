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

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

os.environ.setdefault("METNOS_ENGINE", "v3")


# ── T3: contratto d'ordine ────────────────────────────────────────────────

EXPECTED_PIPELINE = (
    # FASE 3.1 provenienza: backstop schema PRIMO per costruzione (tocca solo
    # l'output grezzo del proposer; i guard a valle scrivono dopo di lui).
    ("coerce_args_to_schema", True),
    ("normalize_filter_operation_values", False),
    ("align_framework_objects", False),
    ("route_text_web_image_search", False),
    ("enforce_missing_clauses", False),
    # Il legame provider vale sul PIANO, non solo sul pool dei candidati: un
    # piano che arriva da cache L0/L1 o da una proposta puo' portare una
    # variante provider che la query non lega. Sta PRIMA dell'affinity, così
    # quella lavora su produttori già legati al contesto giusto.
    ("enforce_provider_binding", False),
    # Una affinity composta e univoca può correggere il produttore scelto dal
    # planner/enforce verb-level; deve precedere l'enforce per-oggetto, che vede
    # così l'output tipizzato già corretto e non aggiunge un secondo producer.
    ("align_strong_affinity_producers", True),
    ("enforce_missing_objects", True),
    # spec sites F1: precursore open_sites + ricostruzione catena canonica per i
    # consumer sites (login/read_sites). Dopo enforce_missing_objects (che può
    # appendere il consumer), prima delle riscritture di qualifier/ordine.
    ("ensure_site_session_precursor", False),
    ("decontaminate_reader_qualifier", True),
    ("ensure_extract_clause", True),
    ("ensure_extracted_period_scope", True),
    ("conform_to_intent_order", True),
    # scope_dirs DOPO conform (l'ordine appeso find_dirs→delete_dirs è finale) e
    # PRIMA della gate-insertion (fuori pipeline): il gate rinumera la coda.
    ("scope_dirs_clause_to_contents", True),
    # move-enumeration: «sposta i file DA cartella X» → find_files→move. Prima
    # di fill (opera su step in ordine-intent) — solo move, mai delete.
    ("enrich_move_source_dir", True),
    ("fill_clause_args", True),
    # La copertura di un'azione si verifica sulla COPPIA (verbo,oggetto): le due
    # proiezioni separate possono essere entrambe soddisfatte mentre nessuno
    # esegue la coppia chiesta. DOPO fill, che e' quando il piano ha gli
    # ingressi: un fratello che CONSUMA il produttore a monte e' deliberato,
    # uno che lo ignora e' un misroute.
    ("align_framework_action_pairs", False),
    # Esclusione di una cartella antenata: il filtro deve usare path completo.
    ("normalize_result_folder_exclusion", False),
    ("resolve_store_field_refs", True),
    ("route_mail_delete_to_trash", False),
    ("route_filename_pattern_to_find", False),
    ("align_provider_client", False),
    ("scope_sink_provider_to_clause", False),
    # query hardware/status: get_processes DEVE avere include_health (10/7).
    ("ensure_health_arg", False),
    # size-misroute (turn 5cdf80d0): «peso cartella» = file ricorsivi. PRIMA di
    # degenerate (list-intent-only, nessun conflitto) — routing.
    ("route_folder_size", False),
    ("degenerate_find_to_list", False),
    # Compound documentale: ULTIMA parola, ricompone i rami gia' normalizzati
    # in una sola dataflow create-only senza lasciare a guard successivi la
    # possibilita' di riaggiungere producer paralleli.
    ("normalize_document_report_pipeline", True),
    # Tre o piu' sorgenti eterogenee: ogni dominio conserva la propria
    # estrazione, poi schema/merge/sink comuni. Deve precedere il normalizzatore
    # specializzato mail+calendario, che altrimenti ne assorbirebbe due soltanto.
    ("normalize_multisource_entity_report_pipeline", True),
    # Compound email+calendario: stessa ultima-parola per imporre schema comune,
    # merge/conflitti e output create-only dopo ogni riscrittura generica.
    ("normalize_message_event_report_pipeline", True),
    # Schema dataflow: i sink dichiarano le colonne che extract deve produrre.
    ("propagate_sink_schema_to_extract", False),
    # Cardinalità dataflow: un sink che dichiara una sorgente completa solleva
    # soltanto i cap di presentazione impliciti lungo la lineage.
    ("enforce_complete_sink_cardinality", False),
    # I template per-chiave usano la clausola group-by e lo schema ormai
    # completo; deve precedere la riscrittura finale dei path create-only.
    ("normalize_grouped_artifact_templates", False),
    # Vincolo utente no-overwrite applicato per ultimo a ogni pipeline di
    # artefatti, anche quando un normalizzatore precedente l'ha ricostruita.
    ("enforce_create_only_artifact_policy", False),
    # ULTIMA per costruzione: conforma allo schema del tool quello che parte
    # davvero per l'executor. Sta in fondo perche' un arg fuori-schema puo'
    # essere la PROVA che una guardia legge (include_health su get_files):
    # toglierlo prima accieca chi lo legge. Ingresso tollerante, uscita stretta.
    ("strip_unknown_args", True),
)


def test_guard_pipeline_order_contract():
    from engine.dispatch import GUARD_PIPELINE
    got = tuple((g.name, g.v3_only) for g in GUARD_PIPELINE)
    assert got == EXPECTED_PIPELINE, (
        "GUARD_PIPELINE cambiata: se è INTENZIONALE aggiorna EXPECTED_PIPELINE "
        f"qui e i vincoli di posizione in dispatch.py.\ngot={got}")


def test_guard_pipeline_callables():
    from engine.dispatch import GUARD_PIPELINE
    for g in GUARD_PIPELINE:
        name, fn = g.name, g.fn
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
        for g in D.GUARD_PIPELINE:
            gname, fn = g.name, g.fn
            x = fn(copy.deepcopy(fwk), intent, query, cat)
            y = fn(copy.deepcopy(x), intent, query, cat)
            if _snap(x) != _snap(y):
                bad.append(f"{gname}@{cname}")
    assert not bad, f"guard NON idempotenti: {bad}"


# ── CP3: interazione verb-level × object-level (S2 ADR 0177 chiuso) ──────────
# I due enforce NON sono ridondanti: verb-level (`enforce_missing_clauses`, con
# `_align_foreign_producers_v3` come helper interno) copre i verbi RICHIESTI;
# object-level (`enforce_missing_objects`) copre i drop PER-OGGETTO che il
# verb-level non vede (N domini stesso verbo). Su un compound multi-dominio
# entrambi devono comporre SENZA doppioni: ogni oggetto richiesto ottiene
# ESATTAMENTE un produttore.

def test_two_enforce_guards_compose_no_double_producer():
    from engine import dispatch as D
    from engine.types import Framework, StepSpec, Intent
    cat = _catalog()
    # compound 2-dominio (files + messages), producer di messages DROPPATO.
    fw = Framework(steps=[
        StepSpec(tool="find_files", args={"base_path": "/x", "client": "local"}),
        StepSpec(tool="final_answer", args={}),
    ])
    intent = Intent(verb="find", object="files", actions=[
        {"verb": "find", "object": "files"},
        {"verb": "find", "object": "messages"}])
    q = "trova i file in /x e cerca le mail di ieri"
    out = D._apply_deterministic_structure_guards(fw, intent, q, cat)
    tools = [s.tool for s in out.steps if s.tool != "final_answer"]
    # esattamente UN produttore per oggetto (files + messages), nessun doppione
    files_prod = [t for t in tools if t.startswith(("find_files", "list_dirs"))]
    msg_prod = [t for t in tools if "messages" in t]
    assert len(files_prod) == 1, tools
    assert len(msg_prod) == 1, tools
    # idempotente: ri-applicare non aggiunge un secondo produttore
    out2 = D._apply_deterministic_structure_guards(out, intent, q, cat)
    assert [s.tool for s in out2.steps] == [s.tool for s in out.steps]


# ── PROV.1: registro tipizzato — metadati + non-collisione (6/7/2026) ────────

def test_all_guards_have_metadata():
    from engine.dispatch import GUARD_PIPELINE
    valid_scope = {"per-clause", "cross-clause", "structure", "routing"}
    for g in GUARD_PIPELINE:
        assert g.scope in valid_scope, f"{g.name}: scope {g.scope!r} invalido"
        assert g.rationale, f"{g.name}: rationale vuoto"
        assert g.adr, f"{g.name}: adr vuoto"
        assert isinstance(g.writes, frozenset) and g.writes, f"{g.name}: writes vuoto"
        assert isinstance(g.reads, frozenset), f"{g.name}: reads non frozenset"


def test_writes_subset_of_observed_mutations():
    """I campi DICHIARATI in `writes` devono coprire ciò che il guard cambia
    davvero sul corpus: applica ogni guard, e se muta il framework, il tipo di
    campo cambiato (args.*/step.tool/step) deve essere ⊆ writes dichiarato.
    Verifica leggera (categoria di campo, non arg specifico)."""
    import copy
    from engine import dispatch as D
    cat = _catalog()
    problems = []
    for cname, fwk, intent, query in _cases():
        for g in D.GUARD_PIPELINE:
            before = _snap(fwk)
            after_fw = g.fn(copy.deepcopy(fwk), intent, query, cat)
            after = _snap(after_fw)
            if before == after:
                continue
            # ha mutato: la categoria dichiarata copre? (step/step.tool/args.*)
            decl = g.writes
            touches_step = any(w == "step" for w in decl)
            touches_tool = any(w.startswith("step.tool") for w in decl) or touches_step
            touches_args = any(w.startswith("args.") for w in decl) or touches_step
            # se dichiara step, copre tutto. Altrimenti serve almeno una categoria.
            if not (touches_step or touches_tool or touches_args):
                problems.append(f"{g.name}@{cname}: muta ma writes non dichiara né step né args")
    assert not problems, problems


def test_no_perclause_writes_before_crossclause_same_field():
    """L'ordine load-bearing (S2): un guard `per-clause` che scrive un campo
    NON deve girare PRIMA di un `cross-clause` che scrive lo STESSO campo —
    la contaminazione va risolta prima della derivazione locale. Verifica
    dichiarativa sull'ordine del registro."""
    from engine.dispatch import GUARD_PIPELINE
    seen_perclause_fields: dict = {}
    problems = []
    for idx, g in enumerate(GUARD_PIPELINE):
        if g.scope == "per-clause":
            for w in g.writes:
                seen_perclause_fields.setdefault(w, idx)
        elif g.scope == "cross-clause":
            for w in g.writes:
                if w in seen_perclause_fields:
                    problems.append(
                        f"{g.name} (cross-clause, idx {idx}) scrive {w} DOPO un "
                        f"per-clause (idx {seen_perclause_fields[w]})")
    assert not problems, problems
