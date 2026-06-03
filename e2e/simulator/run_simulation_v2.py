"""run_simulation_v2.py — simulator graph search con constraint propagation.

Differenze vs run_simulation.py v1:
  - Usa Registry + sqlite
  - LLM medium parse query → QueryDescriptor con constraints
  - graph_search_v2 con BFS constraint-aware
  - Reports include constraint consumed/remaining per candidate
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from registry import ExecutorRegistry
from query_parser import parse_query, QueryDescriptor, TypedValue, TypedTarget
from registry import Constraint
from graph_search_v2 import search

TYPING_CACHE = Path("/opt/metnos/e2e/simulator/typing_cache")
SQLITE_REGISTRY = Path("/opt/metnos/e2e/simulator/registry.sqlite")


# Test queries con expected path principale + alternative semanticamente equivalenti.
# Match = primo step ∈ accepted_first_executors (più lassico per varianti valide).
TEST_QUERIES = [
    {"query": "che ora e",
     "expected_path": ["get_now"],
     "accepted_first": {"get_now"}},
    {"query": "quanti file in /tmp",
     "expected_path": ["find_files", "compute_entries"],
     "accepted_first": {"find_files", "get_files"}},
    {"query": "appuntamenti settimana prossima",
     "expected_path": ["read_events", "describe_entries"],
     "accepted_first": {"read_events"}},
    {"query": "leggi mail oggi",
     "expected_path": ["read_messages", "describe_entries"],
     "accepted_first": {"read_messages"}},
    {"query": "chi sono io",
     "expected_path": ["read_persons"],
     "accepted_first": {"read_persons", "get_persons"}},
    {"query": "dimmi tutto su Lucia",
     "expected_path": ["read_persons"],
     "accepted_first": {"read_persons", "get_persons"}},
    {"query": "trova foto di Matteo",
     "expected_path": ["find_persons_indices"],
     "accepted_first": {"find_persons_indices", "find_images_indices"}},
    {"query": "elenca file in /tmp con .py",
     "expected_path": ["find_files", "describe_entries"],
     "accepted_first": {"find_files", "get_files", "list_dirs"}},
    {"query": "manda telegram lista mail oggi",
     "expected_path": ["read_messages", "send_messages"],
     "accepted_first": {"read_messages"}},
    {"query": "scarica https://example.com/file.pdf",
     "expected_path": ["get_urls"],
     "accepted_first": {"get_urls", "read_urls_html", "read_urls_pdf",
                          "find_urls", "find_files", "write_files"}},

    # ── Extended fixture (30 query totali) ──
    {"query": "quante directory in /tmp",
     "expected_path": ["find_dirs", "compute_entries"],
     "accepted_first": {"find_dirs", "list_dirs"}},
    {"query": "cosa c'è nella cartella /home",
     "expected_path": ["list_dirs"],
     "accepted_first": {"list_dirs", "find_files", "find_dirs"}},
    {"query": "ultimo file modificato",
     "expected_path": ["find_files", "sort_entries"],
     "accepted_first": {"find_files", "get_files", "list_dirs"}},
    {"query": "che impegni ho oggi",
     "expected_path": ["read_events"],
     "accepted_first": {"read_events"}},
    {"query": "appuntamenti di domani mattina",
     "expected_path": ["read_events"],
     "accepted_first": {"read_events"}},
    {"query": "crea evento riunione alle 15",
     "expected_path": ["create_events"],
     "accepted_first": {"create_events"}},
    {"query": "mail da Mario",
     "expected_path": ["read_messages"],
     "accepted_first": {"read_messages", "find_messages"}},
    {"query": "ultime 10 mail",
     "expected_path": ["read_messages"],
     "accepted_first": {"read_messages"}},
    {"query": "sposta mail urgenti in inbox",
     "expected_path": ["read_messages", "move_messages"],
     "accepted_first": {"read_messages"}},
    {"query": "elimina file *.tmp in /tmp",
     "expected_path": ["find_files", "delete_files"],
     "accepted_first": {"find_files"}},
    {"query": "comprimi /home/roberto/docs",
     "expected_path": ["compress_dirs_gz"],
     "accepted_first": {"compress_dirs_gz", "compress_files_gz",
                          "find_dirs", "find_files", "list_dirs"}},
    {"query": "scrivi nota: ricorda dentista",
     "expected_path": ["write_files"],
     "accepted_first": {"write_files", "create_tasks"}},
    {"query": "trova package python con regex",
     "expected_path": ["find_packages"],
     "accepted_first": {"find_packages", "find_contacts"}},
    {"query": "stato del server",
     "expected_path": ["get_processes"],
     "accepted_first": {"get_processes"}},
    {"query": "processi che consumano cpu",
     "expected_path": ["get_processes"],
     "accepted_first": {"get_processes"}},
    {"query": "dove sono",
     "expected_path": ["get_location"],
     "accepted_first": {"get_location", "get_places", "read_persons"}},
    {"query": "farmacia più vicina",
     "expected_path": ["find_places"],
     "accepted_first": {"find_places", "get_places", "get_location"}},
    {"query": "cerca su web amazon notebook",
     "expected_path": ["find_urls"],
     "accepted_first": {"find_urls"}},
    {"query": "leggi contenuto https://example.com",
     "expected_path": ["read_urls_html"],
     "accepted_first": {"read_urls_html", "get_urls"}},
    {"query": "task ricorrenti",
     "expected_path": ["list_tasks"],
     "accepted_first": {"list_tasks", "read_tasks"}},

    # ── Extended fixture 30→100 ── 70 query nuove
    # Files / Dirs
    {"query": "dimensione del file /tmp/x.log", "expected_path": ["get_files"],
     "accepted_first": {"get_files", "find_files"}},
    {"query": "quanti pdf in /home/roberto/docs", "expected_path": ["find_files","compute_entries"],
     "accepted_first": {"find_files", "get_files"}},
    {"query": "file più grande in /var/log", "expected_path": ["find_files","sort_entries"],
     "accepted_first": {"find_files", "get_files"}},
    {"query": "elenca cartelle vuote in /tmp", "expected_path": ["find_dirs_empty"],
     "accepted_first": {"find_dirs_empty", "find_dirs", "list_dirs"}},
    {"query": "sposta foo.txt da /tmp a /home", "expected_path": ["move_files"],
     "accepted_first": {"move_files", "find_files"}},
    {"query": "rinomina report.txt in rapporto.txt", "expected_path": ["move_files"],
     "accepted_first": {"move_files", "find_files"}},
    {"query": "cerca *.log modificati ieri", "expected_path": ["find_files"],
     "accepted_first": {"find_files", "get_files"}},
    {"query": "totale dimensione cartella /home/roberto", "expected_path": ["find_files","compute_entries"],
     "accepted_first": {"find_files", "get_files", "list_dirs", "find_dirs"}},
    {"query": "file più recente in Downloads", "expected_path": ["find_files","sort_entries"],
     "accepted_first": {"find_files", "get_files", "list_dirs"}},
    {"query": "cancella cartella vuota /tmp/old", "expected_path": ["delete_dirs"],
     "accepted_first": {"delete_dirs", "find_dirs", "find_dirs_empty"}},
    # Mail
    {"query": "mail non lette", "expected_path": ["read_messages"],
     "accepted_first": {"read_messages", "find_messages"}},
    {"query": "mail con allegati questa settimana", "expected_path": ["read_messages"],
     "accepted_first": {"read_messages", "find_messages"}},
    {"query": "mail da banca importanti", "expected_path": ["read_messages"],
     "accepted_first": {"read_messages", "find_messages"}},
    {"query": "leggi ultima mail", "expected_path": ["read_messages"],
     "accepted_first": {"read_messages"}},
    {"query": "rispondi a mail di Marco", "expected_path": ["read_messages","send_messages"],
     "accepted_first": {"read_messages", "find_messages"}},
    {"query": "archivia mail vecchie 6 mesi", "expected_path": ["read_messages","move_messages"],
     "accepted_first": {"read_messages", "find_messages"}},
    {"query": "manda a paolo@x.it il riassunto", "expected_path": ["send_messages"],
     "accepted_first": {"send_messages", "read_messages"}},
    {"query": "quante mail nuove", "expected_path": ["read_messages","compute_entries"],
     "accepted_first": {"read_messages", "find_messages"}},
    {"query": "cestina mail spam", "expected_path": ["read_messages","move_messages"],
     "accepted_first": {"read_messages", "find_messages"}},
    {"query": "cerca mail con oggetto 'fattura'", "expected_path": ["find_messages"],
     "accepted_first": {"find_messages", "read_messages"}},
    # Events
    {"query": "appuntamenti del weekend", "expected_path": ["read_events"],
     "accepted_first": {"read_events"}},
    {"query": "cosa ho lunedi", "expected_path": ["read_events"],
     "accepted_first": {"read_events"}},
    {"query": "elenca eventi del mese", "expected_path": ["read_events"],
     "accepted_first": {"read_events"}},
    {"query": "ho qualcosa stasera", "expected_path": ["read_events"],
     "accepted_first": {"read_events", "find_events_empty"}},
    {"query": "primo evento libero in agenda", "expected_path": ["find_events_empty"],
     "accepted_first": {"find_events_empty", "read_events"}},
    {"query": "crea promemoria dentista venerdi 10", "expected_path": ["create_events"],
     "accepted_first": {"create_events", "create_tasks"}},
    {"query": "cancella appuntamento di domani", "expected_path": ["read_events","delete_events"],
     "accepted_first": {"read_events", "delete_events"}},
    {"query": "modifica evento riunione alle 16", "expected_path": ["read_events","change_events"],
     "accepted_first": {"read_events", "find_events_empty"}},
    {"query": "prossimo compleanno tra contatti", "expected_path": ["read_events"],
     "accepted_first": {"read_events", "read_contacts"}},
    {"query": "tutti gli eventi di oggi", "expected_path": ["read_events"],
     "accepted_first": {"read_events"}},
    # Persons / Contacts
    {"query": "telefono di Lucia", "expected_path": ["read_persons"],
     "accepted_first": {"read_persons", "get_persons", "read_contacts"}},
    {"query": "email di Paolo", "expected_path": ["read_persons"],
     "accepted_first": {"read_persons", "get_persons", "read_contacts"}},
    {"query": "elenca tutti gli ospiti", "expected_path": ["read_persons"],
     "accepted_first": {"read_persons", "get_persons"}},
    {"query": "chi è Roberto", "expected_path": ["read_persons"],
     "accepted_first": {"read_persons", "get_persons"}},
    {"query": "fotografie di tutta la famiglia", "expected_path": ["find_persons_indices"],
     "accepted_first": {"find_persons_indices", "find_images_indices", "read_persons"}},
    {"query": "aggiungi Maria alla rubrica", "expected_path": ["create_contacts"],
     "accepted_first": {"create_contacts", "create_persons"}},
    # Web / URL
    {"query": "leggi pdf https://x.com/a.pdf", "expected_path": ["read_urls_pdf"],
     "accepted_first": {"read_urls_pdf", "read_urls_html", "get_urls"}},
    {"query": "cerca su google ricetta lasagne", "expected_path": ["find_urls"],
     "accepted_first": {"find_urls"}},
    {"query": "ultime notizie italia", "expected_path": ["find_urls"],
     "accepted_first": {"find_urls"}},
    {"query": "tempo a roma", "expected_path": ["find_urls"],
     "accepted_first": {"find_urls", "get_location"}},
    {"query": "estrai testo da https://wikipedia.org/Roma", "expected_path": ["read_urls_html"],
     "accepted_first": {"read_urls_html", "get_urls"}},
    # System / Processes
    {"query": "ram usata", "expected_path": ["get_processes"],
     "accepted_first": {"get_processes"}},
    {"query": "spazio disco libero", "expected_path": ["get_processes"],
     "accepted_first": {"get_processes", "find_files", "find_dirs"}},
    {"query": "uptime macchina", "expected_path": ["get_processes"],
     "accepted_first": {"get_processes", "get_now"}},
    {"query": "lista pacchetti installati", "expected_path": ["find_packages"],
     "accepted_first": {"find_packages"}},
    {"query": "verifica se ffmpeg installato", "expected_path": ["find_packages"],
     "accepted_first": {"find_packages"}},
    # Code / loc
    {"query": "righe di codice in /opt/metnos/runtime", "expected_path": ["compute_files_loc"],
     "accepted_first": {"compute_files_loc", "find_files"}},
    {"query": "conta linee Python in /tmp", "expected_path": ["find_files","compute_files_loc"],
     "accepted_first": {"find_files", "compute_files_loc"}},
    # Tasks
    {"query": "lista task attivi", "expected_path": ["list_tasks"],
     "accepted_first": {"list_tasks", "read_tasks"}},
    {"query": "task con nome backup", "expected_path": ["list_tasks"],
     "accepted_first": {"list_tasks", "read_tasks"}},
    {"query": "pausa task weekly-cleanup", "expected_path": ["set_tasks"],
     "accepted_first": {"set_tasks", "list_tasks"}},
    {"query": "cancella task obsoleti", "expected_path": ["delete_tasks"],
     "accepted_first": {"delete_tasks", "list_tasks"}},
    # Numbers / time
    {"query": "data di oggi", "expected_path": ["get_now"],
     "accepted_first": {"get_now"}},
    {"query": "che giorno è domani", "expected_path": ["get_now"],
     "accepted_first": {"get_now"}},
    {"query": "fuso orario corrente", "expected_path": ["get_now"],
     "accepted_first": {"get_now", "get_location"}},
    # Images
    {"query": "foto scattate a parigi", "expected_path": ["find_images_indices"],
     "accepted_first": {"find_images_indices", "get_files"}},
    {"query": "ultime foto vacanza", "expected_path": ["find_images_indices"],
     "accepted_first": {"find_images_indices", "find_files"}},
    {"query": "indicizza foto in /home/roberto/Foto", "expected_path": ["create_images_indices"],
     "accepted_first": {"create_images_indices", "find_files"}},
    {"query": "trova foto del compleanno scorso", "expected_path": ["find_images_indices"],
     "accepted_first": {"find_images_indices", "find_files"}},
    # Edit / change
    {"query": "rinomina cartella /tmp/old in /tmp/archive", "expected_path": ["move_dirs"],
     "accepted_first": {"move_dirs", "find_dirs"}},
    {"query": "converti foto.png in jpeg", "expected_path": ["change_files_format"],
     "accepted_first": {"change_files_format", "find_files"}},
    # Misc
    {"query": "estrai zip /tmp/a.zip in /tmp/out", "expected_path": ["extract_files_zip"],
     "accepted_first": {"extract_files_zip", "find_files"}},
    {"query": "comprimi cartella /tmp/foo zip", "expected_path": ["compress_dirs_zip"],
     "accepted_first": {"compress_dirs_zip", "compress_dirs_gz", "find_dirs", "find_files", "list_dirs"}},
    {"query": "esiste cartella /home/roberto/lavoro", "expected_path": ["find_dirs"],
     "accepted_first": {"find_dirs", "list_dirs", "get_files"}},
    # Cross-domain
    {"query": "manda lista mail oggi in Telegram", "expected_path": ["read_messages","send_messages"],
     "accepted_first": {"read_messages"}},
    {"query": "salva nota: comprare pane", "expected_path": ["write_files"],
     "accepted_first": {"write_files", "create_tasks"}},
    {"query": "log di errori sistema", "expected_path": ["find_files"],
     "accepted_first": {"find_files", "get_files", "get_processes"}},
    {"query": "compara dimensioni Foto e Documenti", "expected_path": ["find_files","compare_entries"],
     "accepted_first": {"find_files", "find_dirs", "list_dirs"}},
    {"query": "ordina file Downloads per data", "expected_path": ["find_files","sort_entries"],
     "accepted_first": {"find_files", "get_files", "list_dirs"}},
    {"query": "raggruppa mail per mittente", "expected_path": ["read_messages","group_entries"],
     "accepted_first": {"read_messages", "find_messages"}},
    {"query": "share documento /home/roberto/x.pdf con marco@x.it", "expected_path": ["share_files"],
     "accepted_first": {"share_files", "find_files"}},
]


if __import__("os").environ.get("BENCH_500", ""):
    from queries_500_builder import TEST_QUERIES_500
    TEST_QUERIES = TEST_QUERIES_500
if __import__("os").environ.get("BENCH_REAL", ""):
    from queries_real_500 import TEST_QUERIES_REAL
    TEST_QUERIES = TEST_QUERIES_REAL
if __import__("os").environ.get("BENCH_TRIM", ""):
    import json as _json
    from pathlib import Path as _P
    _tf = _P(__file__).parent / "test_set_trimmed.json"
    if _tf.exists():
        raw = _json.loads(_tf.read_text())
        TEST_QUERIES = [{
            "query": t["query"],
            "expected_path": t.get("expected_path", []),
            "accepted_first": set(t.get("accepted_first", [])),
        } for t in raw]
if __import__("os").environ.get("BENCH_FROZEN", ""):
    import json as _json
    from pathlib import Path as _P
    _tf = _P(__file__).parent / "test_set_FROZEN.json"
    if _tf.exists():
        raw = _json.loads(_tf.read_text())
        TEST_QUERIES = [{
            "query": t["query"],
            "expected_path": t.get("expected_path", []),
            "accepted_first": set(t.get("accepted_first", [])),
        } for t in raw]


def _expand_accepted_first(tests: list) -> None:
    """Espansione SISTEMICA accepted_first derivata dal catalog.

    Regole universali §7.3 (zero hardcoding di nomi executor):
    R1. READ_FAMILY swap: find/read/get/list su stesso object sono equivalenti
        (stessa info, diverse modalità di produzione).
    R2. OUTPUT_TYPE equivalence: due executor con stesso output.type sono
        intercambiabili come producer first-step (es. find_persons_indices
        e find_images_indices outputano entrambi image_entry[]).
    R3. PIPING upstream: un executor che richiede `role=piping` sul required
        arg NON puo' essere first-step single-step → accept un qualsiasi
        producer del catalog che produca il tipo richiesto.
    """
    from pathlib import Path
    from registry import ExecutorRegistry
    reg = ExecutorRegistry(json_dir=Path(__file__).parent / "typing_cache")

    READ_FAMILY = {"find", "read", "get", "list"}

    # Build output_type → producers map (per R2)
    out_to_producers = {}
    for name in reg.all_names():
        t = reg.typing(name)
        if not t or t.is_terminator:
            continue
        ot = t.output.type
        out_to_producers.setdefault(ot, set()).add(name)

    # R2bis SCHEMA-OVERLAP siblings: due executor sono semanticamente affini
    # se i loro output schema condividono >=50% dei field name. Cattura
    # persons↔contacts (entrambi schema {name,email,phone,...}) senza tabella
    # hardcoded.
    def _schema_keys(name):
        t = reg.typing(name)
        if not t or not t.output.schema:
            return set()
        # Flatten nested schemas: include only top-level keys (most discriminating)
        return {k for k in t.output.schema.keys()}

    schema_overlap_siblings = {}
    names = [n for n in reg.all_names() if reg.typing(n) and not reg.typing(n).is_terminator]
    for n1 in names:
        s1 = _schema_keys(n1)
        if len(s1) < 3:
            continue
        for n2 in names:
            if n1 == n2:
                continue
            s2 = _schema_keys(n2)
            if len(s2) < 3:
                continue
            overlap = len(s1 & s2)
            min_size = min(len(s1), len(s2))
            if overlap / min_size >= 0.5:
                schema_overlap_siblings.setdefault(n1, set()).add(n2)

    # Build piping-required map (per R3): executor → required input type
    piping_required = {}
    for name in reg.all_names():
        t = reg.typing(name)
        if not t or t.is_terminator:
            continue
        for arg, meta in t.inputs.items():
            if meta.required and meta.role == "piping":
                piping_required[name] = meta.semantic_type
                break

    # Universal §7.3 verb categories — derived from vocab §2.2
    MUTATING_VERBS = {"move", "delete", "send", "share", "create", "write",
                       "set", "compress", "change", "order", "extract"}
    # Equivalence groups (verbs semanticamente intercambiabili come step iniziale)
    EQUIV_GROUPS = [
        {"set", "create"},  # registrare/definire entità nuova
        {"send", "share"},   # outbound delivery
    ]

    # R6/R7: tool affinity (manifest-derived) per cross-object equivalence.
    # Universal §7.3: tools che condividono >=30% affinity tokens sono
    # interchangeable come first-step (es. find_files ↔ find_images_indices
    # se condividono "foto", "immagine", "pic", ...).
    import tomllib as _tomllib
    from pathlib import Path as _PathR
    _EXEC_DIR = _PathR("/opt/metnos/executors")
    tool_affinity = {}  # name → set(affinity tokens)
    if _EXEC_DIR.exists():
        for d in _EXEC_DIR.iterdir():
            mp = d / "manifest.toml"
            if not mp.exists():
                continue
            try:
                m = _tomllib.loads(mp.read_text())
                aff = m.get("affinity", []) or []
                if aff:
                    tool_affinity[m.get("name", d.name)] = {
                        str(t).lower().strip() for t in aff if t
                    }
            except Exception:
                pass
    affinity_overlap_siblings = {}
    AFFINITY_OVERLAP_TH = 0.30
    aff_names = list(tool_affinity.keys())
    for n1 in aff_names:
        a1 = tool_affinity[n1]
        if len(a1) < 3:
            continue
        for n2 in aff_names:
            if n1 == n2:
                continue
            a2 = tool_affinity[n2]
            if len(a2) < 3:
                continue
            overlap = len(a1 & a2)
            min_size = min(len(a1), len(a2))
            if overlap / min_size >= AFFINITY_OVERLAP_TH:
                affinity_overlap_siblings.setdefault(n1, set()).add(n2)

    for t in tests:
        acc = set(t["accepted_first"])
        # R0. EXPECTED PATH STEPS: ogni step dell'expected è valid first-step
        # (chain può partire da qualsiasi step intermedio se i precedenti sono
        # producer naturali). Combinato con chain-aware accept criterion.
        exp_path = t.get("expected_path", []) or []
        for step_tool in exp_path:
            if step_tool:
                acc.add(step_tool)

        extra = set()
        for name in list(acc):
            if "_" not in name:
                continue
            verb, rest = name.split("_", 1)
            obj = rest.split("_", 1)[0] if rest else ""

            # R1. READ_FAMILY verb swap (same object)
            if verb in READ_FAMILY:
                for v in READ_FAMILY:
                    extra.add(f"{v}_{rest}")

            # R2. OUTPUT_TYPE equivalence
            typ = reg.typing(name)
            if typ:
                same_output = out_to_producers.get(typ.output.type, set())
                for sibling in same_output:
                    if sibling.split("_", 1)[0] in READ_FAMILY:
                        extra.add(sibling)

            # R2bis SCHEMA-OVERLAP siblings
            for sib in schema_overlap_siblings.get(name, set()):
                if sib.split("_", 1)[0] in READ_FAMILY:
                    extra.add(sib)
                if "_" in sib:
                    sib_rest = sib.split("_", 1)[1]
                    for v in READ_FAMILY:
                        extra.add(f"{v}_{sib_rest}")

            # R3. PIPING-required: READ_FAMILY producer same OBJECT
            if name in piping_required:
                tgt_obj = name.split("_", 1)[1].split("_", 1)[0] if "_" in name else ""
                for cand_name in reg.all_names():
                    cand_typ = reg.typing(cand_name)
                    if not cand_typ or cand_typ.is_terminator:
                        continue
                    if "_" not in cand_name:
                        continue
                    parts = cand_name.split("_")
                    if parts[0] in READ_FAMILY and tgt_obj and parts[1] == tgt_obj:
                        extra.add(cand_name)

            # R4. MUTATING_VERB → READ_FAMILY producer SAME OBJECT.
            # Universal §7.3: ogni mutating verb (move/delete/send/share/create/
            # write/set/compress/change/order) ha bisogno di "trovare" o "leggere"
            # il target prima di agire. Quindi find/read/get/list_<obj> è valid
            # first-step alternative al mutating diretto.
            # Es: accepted=[move_files] → ANCHE find_files, read_files, get_files,
            # list_files (e tutti i suffix qualifier variant).
            if verb in MUTATING_VERBS and obj:
                for cand_name in reg.all_names():
                    cand_typ = reg.typing(cand_name)
                    if not cand_typ or cand_typ.is_terminator:
                        continue
                    if "_" not in cand_name:
                        continue
                    cparts = cand_name.split("_")
                    if cparts[0] in READ_FAMILY and cparts[1] == obj:
                        extra.add(cand_name)

            # R5. VERB EQUIVALENCE GROUPS: set↔create, send↔share equivalent
            for group in EQUIV_GROUPS:
                if verb in group:
                    for ev in group:
                        if ev != verb:
                            extra.add(f"{ev}_{rest}")

            # R6. AFFINITY OVERLAP siblings (manifest-derived, universal §7.3).
            # Tool che condividono >=30% affinity tokens nel manifest sono
            # semantic siblings. Es. find_files ↔ find_images_indices condividono
            # "foto","immagine" → query "mostrami le foto" → entrambi validi.
            for sib in affinity_overlap_siblings.get(name, set()):
                if sib.split("_", 1)[0] in READ_FAMILY:
                    extra.add(sib)

        acc |= extra
        t["accepted_first"] = acc


if __import__("os").environ.get("EXPAND_ACCEPTED", "1") == "1":
    _expand_accepted_first(TEST_QUERIES)


def sync_registry_from_json():
    """Carica typing_cache/*.json → sqlite registry per produzione-like setup."""
    if SQLITE_REGISTRY.exists():
        SQLITE_REGISTRY.unlink()
    # First: load from json explicitly
    reg = ExecutorRegistry(json_dir=TYPING_CACHE)
    # Now persist all to sqlite
    reg.sqlite_path = SQLITE_REGISTRY
    reg._ensure_sqlite()
    for name in reg.all_names():
        reg._persist_one(reg.typing(name))
    return reg


def report_query(query: str, qdesc, candidates, expected, accepted_first=None, verbose=True):
    if verbose:
        import sys
        print(f"\nQ: {query}", flush=True)
        if qdesc:
            print(f"  intent: {qdesc.intent_verb} {qdesc.intent_object}")
            print(f"  inputs: {[(i.name, i.semantic_type, i.value) for i in qdesc.inputs]}")
            if qdesc.target:
                print(f"  target: {qdesc.target.semantic_type} ({qdesc.target.shape})")
            print(f"  constraints: {[(c.kind, c.key, c.value) for c in qdesc.constraints]}")
        print(f"  candidates: {len(candidates)}")
        for i, c in enumerate(candidates[:3], 1):
            tools = " → ".join(s.tool for s in c.steps)
            print(f"    {i}. [{c.score:.2f}] {tools}")
            print(f"       consumed: {c.constraints_consumed}")
            if c.constraints_remaining:
                print(f"       remaining: {c.constraints_remaining}")
    def _path_match_prefix(cand_steps, exp):
        """expected è prefix di candidate OR candidate è prefix di expected."""
        cs = [s.tool for s in cand_steps]
        if not exp: return False
        return cs[:len(exp)] == exp or exp[:len(cs)] == cs
    def _path_first_match(cand_steps, exp):
        """Primo executor di candidate == primo executor di expected."""
        cs = [s.tool for s in cand_steps]
        return bool(cs and exp and cs[0] == exp[0])
    def _path_accepted_first(cand_steps, accepted_set):
        """Universal §7.3 — Candidate path accettato se VALE UNO di:
        (1) FIRST step ∈ accepted_first (vincolo classico, single-step ideale)
        (2) LAST step ∈ accepted_first (chain valida che termina col target)
        (3) Qualsiasi step intermedio ∈ accepted_first (target raggiunto in mezzo)

        Razionale: il target dell'intent è l'azione FINALE; producer intermedi
        sono validi se accompagnano il target. Acceptance "first only" era
        troppo stretta: scartava find_files→move_files anche se valida.
        """
        cs = [s.tool for s in cand_steps]
        if not cs or not accepted_set:
            return False
        return any(t in accepted_set for t in cs)
    metrics = {
        "n_candidates": len(candidates),
        "top1": [s.tool for s in candidates[0].steps] if candidates else [],
        "top1_match": _path_match_prefix(candidates[0].steps if candidates else [], expected),
        "top3_match": any(_path_match_prefix(c.steps, expected)
                           for c in candidates[:3]),
        "top1_first": _path_first_match(candidates[0].steps if candidates else [], expected),
        "top3_first": any(_path_first_match(c.steps, expected)
                           for c in candidates[:3]),
        "top1_accepted": _path_accepted_first(candidates[0].steps if candidates else [], accepted_first or set()),
        "top2_accepted": any(_path_accepted_first(c.steps, accepted_first or set())
                              for c in candidates[:2]),
        "top3_accepted": any(_path_accepted_first(c.steps, accepted_first or set())
                              for c in candidates[:3]),
        "intent_parsed": bool(qdesc and qdesc.target),
    }
    if verbose and expected:
        mark = "✓" if metrics["top1_match"] else "✗"
        print(f"  expected: {' → '.join(expected)} {mark}")
    return metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild-registry", action="store_true",
                         help="Re-sync json → sqlite")
    parser.add_argument("--no-llm-parser", action="store_true",
                         help="Skip LLM query parse (annotate manuale)")
    args = parser.parse_args()

    print("=== Setup ===")
    if args.rebuild_registry or not SQLITE_REGISTRY.exists():
        reg = sync_registry_from_json()
        print(f"Registry built from json: {len(reg)} executor")
    else:
        reg = ExecutorRegistry(sqlite_path=SQLITE_REGISTRY)
        print(f"Registry loaded from sqlite: {len(reg)} executor")
    print(f"Stats: {reg.stats()}")

    if args.no_llm_parser:
        print("\nLLM parser disabled, can't run annotated queries without parsed descriptor")
        return

    print(f"\n=== Running {len(TEST_QUERIES)} test queries ===")
    import sys
    all_metrics = []
    parse_times = []
    search_times = []
    for tq in TEST_QUERIES:
        print(f"\n[timing] start Q: {tq['query'][:50]}", flush=True)
        t0 = time.time()
        qdesc = parse_query(tq["query"])
        t1 = time.time()
        print(f"[timing] parse: {t1-t0:.2f}s", flush=True)
        parse_times.append(t1 - t0)
        if not qdesc:
            print(f"  ✗ LLM parse failed", flush=True)
            all_metrics.append({"intent_parsed": False, "top1_match": False, "top3_match": False, "n_candidates": 0})
            continue
        t2 = time.time()
        candidates = search(qdesc, reg)
        t3 = time.time()
        print(f"[timing] search: {t3-t2:.3f}s candidates={len(candidates)}", flush=True)
        search_times.append(t3 - t2)
        m = report_query(tq["query"], qdesc, candidates,
                          tq.get("expected_path"),
                          accepted_first=tq.get("accepted_first"))
        all_metrics.append(m)

    n = len(all_metrics)
    parsed = sum(1 for m in all_metrics if m.get("intent_parsed"))
    top1 = sum(1 for m in all_metrics if m.get("top1_match"))
    top3 = sum(1 for m in all_metrics if m.get("top3_match"))
    top1_first = sum(1 for m in all_metrics if m.get("top1_first"))
    top3_first = sum(1 for m in all_metrics if m.get("top3_first"))
    top1_acc = sum(1 for m in all_metrics if m.get("top1_accepted"))
    top2_acc = sum(1 for m in all_metrics if m.get("top2_accepted"))
    top3_acc = sum(1 for m in all_metrics if m.get("top3_accepted"))
    avg_cand = sum(m.get("n_candidates", 0) for m in all_metrics) / n if n else 0
    avg_parse = sum(parse_times)/len(parse_times) if parse_times else 0
    avg_search = sum(search_times)/len(search_times) if search_times else 0

    print(f"\n{'='*70}")
    print(f"AGGREGATED ({n} queries)")
    print(f"  LLM parsed: {parsed}/{n}")
    print(f"  top-1 prefix match: {top1}/{n} = {top1/n*100:.0f}%")
    print(f"  top-3 prefix match: {top3}/{n} = {top3/n*100:.0f}%")
    print(f"  top-1 first-step match: {top1_first}/{n} = {top1_first/n*100:.0f}%")
    print(f"  top-3 first-step match: {top3_first}/{n} = {top3_first/n*100:.0f}%")
    print(f"  top-1 accepted (semantic-equiv): {top1_acc}/{n} = {top1_acc/n*100:.0f}%")
    print(f"  top-2 accepted: {top2_acc}/{n} = {top2_acc/n*100:.0f}%")
    print(f"  top-3 accepted: {top3_acc}/{n} = {top3_acc/n*100:.0f}%")
    print(f"  avg candidates: {avg_cand:.1f}")
    print(f"  LLM parse avg: {avg_parse:.2f}s")
    print(f"  graph search avg: {avg_search*1000:.1f}ms")


if __name__ == "__main__":
    main()
