#!/usr/bin/env python3
"""Build and verify reviewer B's independent intent-shadow oracle."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[6]
SAMPLE = (
    ROOT
    / "internal/tools/request_analysis_lab/misure_11_8/"
    "prova_specchi_riparo_c11.json"
)
REGISTRY = (
    ROOT
    / "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/"
    "intent_shadow_registry_v0_1.json"
)
REGISTRY_FREEZE = REGISTRY.with_name("intent_shadow_registry_v0_1.freeze.json")
SOURCE_AUDIT = REGISTRY.with_name(
    "intent_shadow_oracle_source_audit_v0_1.json"
)
QUESTION_CONTROLS = (
    ROOT / "internal/tools/request_analysis_lab/question_focus_controls_v1.json"
)
PHASE1_ORACLE = (
    ROOT
    / "internal/tools/request_analysis_lab/oracles/phase1_v1/"
    "metnos_phase1_typed_oracle_v1.overlay.json"
)
OUTPUT = HERE / "intent_shadow_oracle_reviewer_b_v0_1.json"


def digest_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def digest_text(value: str) -> str:
    return digest_bytes(value.encode("utf-8"))


def file_digest(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def canonical_digest(value: Any) -> str:
    return digest_bytes(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def op(route: str, *sources: int) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": "operation", "route": route}
    if sources:
        result["data_from"] = [{"from": source} for source in sources]
    return result


def graph(*nodes: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "operation_graph", "body": list(nodes)}


def barrier(*approved: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "barrier",
        "barrier": "get/approval",
        "cases": [{"outcome": "approved", "body": list(approved)}],
    }


def control() -> dict[str, Any]:
    return {"kind": "system_control", "control": "undo_last_turn"}


def unrep(reason: str) -> dict[str, Any]:
    return {"kind": "unrepresentable", "reason": reason}


def g(*items: tuple[str, tuple[int, ...]]) -> dict[str, Any]:
    return graph(*(op(route, *sources) for route, sources in items))


def unsupported() -> dict[str, Any]:
    return unrep("outside_registry")


EXPECTED: list[dict[str, Any]] = [
    g(("find/images", ())),  # 0
    g(("find/images", ())),
    g(("get/persons", ())),
    g(("get/persons", ())),
    g(("delete/persons", ())),
    g(("get/persons", ())),
    g(("find/images", ())),
    g(("get/persons", ())),
    g(("get/persons", ())),
    g(("get/persons", ())),
    g(("find/persons", ())),
    g(("get/persons", ())),
    g(("read/persons", ())),
    g(("delete/persons", ())),
    g(("delete/persons", ())),
    g(("delete/persons", ())),
    g(("get/persons", ())),
    g(("get/persons", ())),
    unsupported(),  # 18: indispensable extraction/reconciliation is outside registry
    g(("move/messages", ())),
    g(("move/messages", ())),
    g(("move/messages", ())),
    g(("read/messages", ())),
    g(("get/processes", ())),
    unsupported(),  # 24: store write/read is not in the frozen registry
    control(),
    g(
        ("find/events", ()),
        ("create/events", (0,)),
        ("send/messages", (1,)),
    ),
    g(("find/files", ()), ("read/files", (0,))),
    g(("delete/dirs", ())),
    g(("find/files", ()), ("read/files", (0,))),
    graph(barrier(op("set/issues"))),
    unsupported(),  # 31: authenticated browser/site actions are outside registry
    g(("delete/files", ()), ("delete/dirs", ())),
    unrep("no_actionable_intent"),
    unsupported(),  # 34: indispensable extraction/classification is outside registry
    g(("delete/dirs", ())),
    unsupported(),  # preferences are observed but have no route
    g(("find/files", ()), ("read/files", (0,))),
    unsupported(),  # 38: indispensable structured extraction is outside registry
    unsupported(),  # 39: GitHub repository-file read is outside frozen routes
    unsupported(),  # 40: generic store operations are outside frozen routes
    g(("read/messages", ())),
    unsupported(),  # authenticated interactive site use is outside registry
    g(("delete/files", ()), ("delete/dirs", ())),
    unsupported(),  # 44: indispensable generic-store persistence is outside registry
    unsupported(),  # Google Photos upload/album mutation has no frozen route
    g(("find/images", ())),
    g(("get/processes", ())),
    unrep("no_actionable_intent"),
    unsupported(),  # 49: indispensable structured extraction is outside registry
    unsupported(),
    g(("get/processes", ())),
    g(("list/dirs", ())),
    g(("list/tasks", ())),
    g(("list/dirs", ()), ("create/files", (0,))),
    g(("read/urls", ())),
    g(("delete/files", ()), ("delete/dirs", ())),
    g(("find/files", ())),
    g(("delete/files", ()), ("delete/dirs", ())),
    graph(barrier(op("set/issues"))),
    g(("get/processes", ())),
    g(("list/tasks", ())),
    g(("list/skills", ())),
    unsupported(),  # 63: generic store and notification pipeline is indispensable
    unsupported(),
    unsupported(),
    control(),
    g(("list/dirs", ()), ("create/files", (0,))),
    g(("create/dirs", ())),
    graph(barrier(op("set/issues"))),
    g(("move/messages", ())),
    g(("read/messages", ())),
    unsupported(),
    unsupported(),  # 73: indispensable structured extraction is outside registry
    g(("list/dirs", ())),
    g(("list/dirs", ())),
    unsupported(),  # 76: indispensable generic-store persistence is outside registry
    unsupported(),  # 77: store read/write surround an approval-gated external effect
    g(("find/images", ())),
    unsupported(),  # 79: indispensable structured extraction is outside registry
    g(("find/files", ())),
    g(("delete/tasks", ())),
    g(("find/issues", ())),
    g(("create/files", ())),
    g(("get/images", ())),
    unsupported(),  # GitHub repository-file read is outside frozen routes
    g(("get/processes", ())),
    g(("find/dirs", ())),
    unsupported(),  # 88: indispensable structured extraction is outside registry
    unsupported(),  # 89: indispensable structured extraction is outside registry
    g(("get/processes", ())),
    g(("create/tasks", ())),
    g(("find/files", ())),
    g(("set/credentials", ())),
    g(("read/messages", ()), ("write/files", (0,))),
    g(("create/tasks", ())),
    unsupported(),
    unsupported(),
    unsupported(),
    g(("delete/tasks", ())),
    unsupported(),  # GitHub repository-file read is outside frozen routes
    g(("get/places", ())),
    g(("delete/files", ())),
    g(("find/images", ())),
    unsupported(),
    g(("find/files", ())),
    unsupported(),  # indispensable generic-store persistence is outside registry
    unsupported(),
    g(("find/files", ()), ("read/files", (0,)), ("create/files", (1,))),
    unsupported(),  # GitHub repository tree listing is outside frozen routes
    g(("create/calendars", ())),
    g(("get/now", ())),
    unsupported(),
    unsupported(),  # 113: explicit field extraction has no registered route
    unsupported(),  # Google Photos album download is outside the frozen registry
    g(("get/location", ())),
    g(("get/now", ())),
    g(("find/images", ())),
    g(("get/now", ())),
    g(("find/files", ())),
]


RATIONALES = [
    "Ricerca visiva descrittiva; 'persona' non implica il registro nominale.",
    "Ricerca di foto per attributi visivi e primo piano.",
    "La domanda enumera il registro di persone enrollate.",
    "Snapshot del registro biometrico, non ricerca fotografica.",
    "Rimozione esplicita dell'enrollment della persona nominata.",
    "La qualificazione 'nel sistema' mantiene il registro persone.",
    "Ricerca fotografica per età apparente e tratti visivi.",
    "Enumerazione degli enrollati.",
    "Interrogazione del registro enrollato.",
    "Identificazione biometrica della persona nella foto allegata.",
    "La foto allegata è riferimento per cercare altre occorrenze della persona.",
    "Identificazione biometrica della persona nella foto allegata.",
    "Profilo dell'attore corrente, non lista del registro.",
    "Cancellazione dell'enrollment nominato.",
    "Un nome inesistente non cambia l'intento di cancellazione.",
    "Un identificatore di prova non cambia l'intento di cancellazione.",
    "Enumerazione completa del registro nominale.",
    "Snapshot di chi è registrato nel riconoscimento persone.",
    "Estrazione e riconciliazione sono indispensabili, ma non hanno una rotta nel registro congelato.",
    "Lo spam è il criterio dell'unica azione richiesta: spostare messaggi.",
    "La sorgente è la mailbox e l'effetto richiesto è move/messages verso Spam.",
    "'Cartella di posta Spam' è destinazione della singola azione move/messages.",
    "read/messages effettua la ricerca nella mailbox e ne espone il conteggio.",
    "La classifica per memoria è un parametro dello snapshot processi richiesto.",
    "La persistenza nello store nominato è indispensabile ma write/entries non è nel registro.",
    "Undo è l'unica radice system_control registrata.",
    "Disponibilità, creazione dell'evento scelto e conferma esterna sono tre azioni ordinate.",
    "Ricerca per estensione seguita dalla lettura dei risultati.",
    "La cartella Google Drive è una directory da cancellare; il backend non cambia la rotta.",
    "Ricerca e lettura sono le due azioni; 'primi 2' limita l'insieme letto.",
    "La sola continuazione approvata aggiorna la issue; il rifiuto resta vuoto.",
    "Login/navigazione e analisi di sito autenticato non hanno una rotta ordinaria completa nel registro.",
    "La frase richiede due cancellazioni distinte: file e directory contenute.",
    "È un'asserzione senza richiesta operativa autonoma.",
    "Estrazione e assegnazione di categorie sono indispensabili, ma extract/classify non sono rotte registrate.",
    "Cancellazione della directory esplicita sul dispositivo indicato.",
    "Preferences è osservato ma non possiede una rotta nel registro congelato.",
    "Scoperta del foglio per titolo, poi lettura del suo contenuto.",
    "L'estrazione strutturata di data/importo è indispensabile e manca dal registro.",
    "La lettura dei README dentro un repository GitHub non è coperta da una rotta registrata.",
    "Il flusso richiede lettura e scrittura dello store generico, assenti dal registro.",
    "Richiesta di lettura della posta dell'account nominato.",
    "Login interattivo e contenuto riservato richiedono capacità di sito fuori registro.",
    "La frase richiede la cancellazione di file e directory contenute.",
    "La persistenza nello store nominato è parte indispensabile del composto ed è fuori registro.",
    "Upload e inserimento in album Google Photos non hanno rotta nel registro.",
    "Ricerca indicizzata con nomi e data come criteri congiunti.",
    "Snapshot dei processi con limite e ordinamento richiesti come parametri.",
    "Un nome di repository isolato non esprime un'azione.",
    "L'estrazione strutturata dei dati dalle mail è indispensabile e manca dal registro.",
    "Il flusso autenticato sul sito è fuori dal registro congelato.",
    "L'indirizzo di rete è una sezione dello snapshot salute del server.",
    "Elenco diretto di una directory nota sul dispositivo.",
    "Enumerazione dei task schedulati dell'attore.",
    "Enumera i path e li materializza in un nuovo foglio.",
    "La richiesta è lettura del corpo dell'URL noto.",
    "La frase richiede le due azioni delete/files e delete/dirs.",
    "Nel confine Metnos, contare file che soddisfano un pattern resta find/files.",
    "La frase richiede le due azioni delete/files e delete/dirs.",
    "La continuazione dell'approvazione aggiorna la issue.",
    "Lo spazio disco appartiene allo snapshot salute del dispositivo.",
    "'Lista' enumera i task, non i processi.",
    "La domanda chiede l'inventario delle capacità/skill.",
    "Store, analisi e notifica sono indispensabili e almeno lo store è fuori registro.",
    "Interazione autenticata e creazione da dati del sito non sono interamente registrate.",
    "La lettura autenticata del sito non ha una rotta completa nel registro.",
    "Undo resta una radice esclusiva di controllo.",
    "Enumera la directory remota e crea un foglio con i path.",
    "Creazione di una directory, con backend scelto fuori dall'intento.",
    "L'aggiornamento della issue appartiene soltanto all'esito approved.",
    "Cancellare mail è la singola azione move/messages verso il cestino.",
    "Lettura delle mail nella finestra odierna.",
    "Preferences è fuori dal registro di operazioni 0.1.",
    "L'estrazione strutturata dei dati dalle mail è indispensabile e manca dal registro.",
    "Enumerazione dei contenuti diretti di Documenti sul dispositivo.",
    "Enumerazione dei contenuti diretti della cartella nota.",
    "La scrittura nel database locale è indispensabile ma assente dal registro.",
    "Store read/write e azioni approvate formano un composto non interamente rappresentabile.",
    "Ricerca fotografica con compresenza di due persone.",
    "L'estrazione strutturata per pagina è indispensabile ma non è registrata; niente sottografo parziale.",
    "Ricerca ricorsiva di file Markdown.",
    "Cancellazione del task identificato.",
    "Elenca issue aperte; il contenuto di singole issue non è richiesto.",
    "Crea un nuovo foglio locale con path e valori letterali.",
    "get/images restituisce direttamente stato e numero di elementi degli indici.",
    "La lettura di un file del repository GitHub non ha rotta nel registro.",
    "Il modello CPU appartiene allo snapshot salute del server.",
    "La panoramica ricorsiva delle directory fornisce il conteggio sottocartelle.",
    "L'estrazione strutturata dei pagamenti è indispensabile e manca dal registro.",
    "L'estrazione strutturata di data/importo è indispensabile e manca dal registro.",
    "Snapshot dei processi con top e memoria come parametri della richiesta.",
    "Il corpo del lavoro è la query futura; ora si crea soltanto il task ricorrente.",
    "Nel confine Metnos, contare file per estensione resta find/files.",
    "Salvataggio di credenziali per il binding Telepass.",
    "Lettura mail e scrittura del risultato in un file.",
    "La richiesta corrente registra un task; il piano interno verrà analizzato al fire.",
    "Login e lettura prenotazioni su sito autenticato sono fuori registro.",
    "Il dominio senza URL preciso richiede navigazione di sito, fuori registro.",
    "Preferences non ha una rotta registrata per rimozione.",
    "Cancellazione del task per identificatore.",
    "Il ramo GitHub richiede lettura file repository non registrata, quindi il composto intero fallisce.",
    "Reverse geocoding di coordinate note.",
    "Cancellazione dei file contenuti, senza cancellare la directory.",
    "Ricerca fotografica di entrambe le persone nominate.",
    "Navigazione autenticata e lettura prenotazioni sono fuori registro.",
    "Ricerca del documento per nome su backend Drive.",
    "Lo store nominato è un sink indispensabile fuori dal registro.",
    "Google Keep come sorgente non ha una rotta registrata.",
    "Scoperta e lettura del file Drive precedono la creazione del nuovo foglio.",
    "L'enumerazione dell'albero di un repository GitHub è fuori registro.",
    "Creazione di un calendario-contenitore, non di un evento.",
    "Data corrente come snapshot temporale.",
    "La pagina non è identificata da URL e richiede navigazione di sito fuori registro.",
    "L'estrazione esplicita dei campi dagli eventi è indispensabile ma non ha rotta registrata.",
    "Il download da Google Photos è indispensabile ma non è coperto dalla semantica get/images congelata (stato indici).",
    "Posizione corrente dell'attore tramite la rotta dedicata.",
    "Ora corrente; il marcatore tra parentesi non cambia l'intento.",
    "Ricerca fotografica per contenuto/scena 'zip line'.",
    "Ora corrente; il marcatore tra parentesi non è un indice dati.",
    "Ricerca ricorsiva per estensione .log nella directory nota.",
]


AMBIGUITIES: dict[int, dict[str, Any]] = {
    0: {
        "status": "resolved_by_context",
        "note": "'persona' può evocare il registro, ma i tratti visivi e il primo piano chiedono ricerca immagini.",
    },
    9: {
        "status": "attachment_required",
        "note": "La foto deittica deve essere presente nel contesto; la rotta resta get/persons.",
    },
    10: {
        "status": "attachment_required",
        "note": "La foto deittica deve essere presente per costruire reference_images.",
    },
    11: {
        "status": "attachment_required",
        "note": "La foto deittica deve essere presente nel contesto.",
    },
    18: {
        "status": "abstraction_limit",
        "note": "Il contratto 0.1 cattura operazioni e flussi astratti, non tutte le sotto-regole analitiche del rapporto.",
    },
    28: {
        "status": "backend_abstracted",
        "note": "La rotta delete/dirs non codifica il backend Google Drive.",
    },
    35: {
        "status": "redacted_literal",
        "note": "Il testo congelato contiene un segmento redatto; la classe dell'azione resta delete/dirs.",
    },
    38: {
        "status": "missing_source_scope",
        "note": "La sorgente delle fatture non è nominata; il dominio storico del campione suggerisce mail, ma gli argomenti richiederebbero chiarimento.",
    },
    41: {
        "status": "lexical",
        "note": "'Elenca posta' può suonare come cartelle, ma il nome mailbox/account rende plausibile la lettura della posta.",
    },
    43: {
        "status": "redacted_literal",
        "note": "Il path è parzialmente redatto, ma 'tutto quello che c'è' indica cancellazione dei contenuti.",
    },
    46: {
        "status": "name_conjunction",
        "note": "Si assume AND/compresenza tra i due nomi; il testo telegrafico non esplicita OR.",
    },
    48: {
        "status": "non_actionable",
        "note": "Potrebbe essere risposta a un dialogo precedente, ma isolata non porta un predicato richiesto.",
    },
    58: {
        "status": "redacted_literal",
        "note": "Il path è redatto; la richiesta esplicita entrambe le classi file/directory.",
    },
    75: {
        "status": "device_default",
        "note": "Il dispositivo non è esplicito; non cambia la rotta list/dirs.",
    },
    84: {
        "status": "registry_abstraction",
        "note": "get/images richiede base_path nell'executor, mentre la frase chiede più corpus; l'oracolo resta al livello di intento astratto.",
    },
    93: {
        "status": "redacted_secret",
        "note": "I valori sono redatti nel campione; la classe semantica set/credentials è inequivoca.",
    },
    114: {
        "status": "registry_gap",
        "note": "'Scelgo io' descrive il picker del provider; la rotta get/images congelata copre lo stato degli indici, non il download di media.",
    },
}


CONFIDENCE_OVERRIDES = {
    18: 0.82,
    24: 0.94,
    31: 0.94,
    38: 0.68,
    41: 0.78,
    48: 0.96,
    63: 0.92,
    79: 0.88,
    84: 0.72,
    100: 0.92,
    108: 0.80,
    114: 0.96,
}


NEW_CONTROLS = [
    {
        "id": "reviewer_b.ctrl.35.condition_owned_mutation.it",
        "language": "it",
        "query": "Leggi la issue 47 e, solo se approvo, impostala come chiusa.",
        "adversarial_target": "Una mutazione condizionata deve appartenere al caso approved e dipendere dai dati letti prima della barriera.",
        "expected": graph(
            op("read/issues"),
            barrier(op("set/issues", 0)),
        ),
        "motivation": "Distingue una barriera strutturale da un set/issues appiattito nel percorso incondizionato.",
        "confidence": 0.99,
        "ambiguity": None,
    },
    {
        "id": "reviewer_b.ctrl.36.mixed_root.it",
        "language": "it",
        "query": "Annulla l'ultima operazione e poi leggi le mail di oggi.",
        "adversarial_target": "Una radice system_control non può essere mescolata con un operation_graph.",
        "expected": unrep("mixed_root_kinds"),
        "motivation": "Controlla l'esclusività delle radici senza trasformare undo in una normale operazione.",
        "confidence": 0.99,
        "ambiguity": None,
    },
    {
        "id": "reviewer_b.ctrl.37.indispensable_outside_registry.it",
        "language": "it",
        "query": "Leggi le mail di oggi e salva i risultati nello store posta_verifica.",
        "adversarial_target": "Una clausola indispensabile fuori registro rende non rappresentabile tutto il composto.",
        "expected": unrep("outside_registry"),
        "motivation": "Applica la regola approvata: non conserva read/messages come sottografo parziale eseguibile.",
        "confidence": 0.99,
        "ambiguity": None,
    },
    {
        "id": "reviewer_b.ctrl.38.safe_but_inaccurate_stop.it",
        "language": "it",
        "query": "Dove ero ieri alle 18?",
        "adversarial_target": "La capacità registrata offre solo la posizione corrente; non deve fingere una risposta storica.",
        "expected": unrep("outside_registry"),
        "motivation": "L'arresto è sicuro ma resta distinto dall'accuratezza; get/location corrente sarebbe semanticamente errato.",
        "confidence": 0.97,
        "ambiguity": None,
    },
]


def walk_operations(document: dict[str, Any]):
    def visit(nodes: list[dict[str, Any]]):
        for node in nodes:
            if node["kind"] == "operation":
                yield node
            elif node["kind"] == "barrier":
                for case in node["cases"]:
                    yield from visit(case["body"])

    if document.get("kind") == "operation_graph":
        yield from visit(document["body"])


def validate_expected(document: dict[str, Any], registry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    kind = document.get("kind")
    if kind == "system_control":
        if set(document) != {"kind", "control"}:
            errors.append("system_control_fields")
        if document.get("control") not in registry["system_controls"]:
            errors.append("system_control_unknown")
        return errors
    if kind == "unrepresentable":
        if set(document) != {"kind", "reason"}:
            errors.append("unrepresentable_fields")
        if document.get("reason") not in registry["unrepresentable_reasons"]:
            errors.append("unrepresentable_reason_unknown")
        return errors
    if kind != "operation_graph":
        return ["root_kind"]
    if set(document) != {"kind", "body"} or not document.get("body"):
        errors.append("operation_graph_fields_or_empty")

    ordinal = 0

    def visit(nodes: Any, inherited: set[int]) -> set[int]:
        nonlocal ordinal
        local = set(inherited)
        if not isinstance(nodes, list) or not nodes:
            errors.append("empty_body")
            return local
        for node in nodes:
            if not isinstance(node, dict):
                errors.append("node_type")
                continue
            node_kind = node.get("kind")
            if node_kind == "operation":
                allowed = {"kind", "route", "data_from"}
                if set(node) - allowed:
                    errors.append("operation_extra_fields")
                if node.get("route") not in registry["operations"]:
                    errors.append(f"operation_unknown:{node.get('route')}")
                for edge in node.get("data_from", []):
                    source = edge.get("from") if isinstance(edge, dict) else None
                    if set(edge) != {"from"}:
                        errors.append("data_edge_fields")
                    if source not in local or source >= ordinal:
                        errors.append("data_edge_dominance")
                local.add(ordinal)
                ordinal += 1
            elif node_kind == "barrier":
                if set(node) != {"kind", "barrier", "cases"}:
                    errors.append("barrier_fields")
                barrier_entry = registry["barriers"].get(node.get("barrier"))
                if not barrier_entry:
                    errors.append("barrier_unknown")
                    continue
                outcomes = [case.get("outcome") for case in node.get("cases", [])]
                expected_order = barrier_entry["outcomes"]
                if len(outcomes) != len(set(outcomes)):
                    errors.append("barrier_outcome_duplicate")
                if outcomes != sorted(outcomes, key=expected_order.index):
                    errors.append("barrier_outcome_order")
                for case in node.get("cases", []):
                    if set(case) != {"outcome", "body"}:
                        errors.append("case_fields")
                    if case.get("outcome") not in expected_order:
                        errors.append("barrier_outcome_unknown")
                    visit(case.get("body"), set(local))
            else:
                errors.append("node_kind")
        return local

    visit(document["body"], set())
    return errors


def case_record(index: int, query: str, expected: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"frozen-{index:03d}",
        "index": index,
        "query_sha256": digest_text(query),
        "query": query,
        "expected": expected,
        "motivation": RATIONALES[index],
        "confidence": CONFIDENCE_OVERRIDES.get(index, 0.97),
        "ambiguity": AMBIGUITIES.get(index),
    }


def build() -> dict[str, Any]:
    frozen = json.loads(SAMPLE.read_text(encoding="utf-8"))
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    queries = frozen["queries"]
    if len(queries) != 120 or len(EXPECTED) != 120 or len(RATIONALES) != 120:
        raise ValueError(
            f"length mismatch queries={len(queries)} expected={len(EXPECTED)} "
            f"rationales={len(RATIONALES)}"
        )
    cases = [
        case_record(index, query, deepcopy(EXPECTED[index]))
        for index, query in enumerate(queries)
    ]
    controls = deepcopy(NEW_CONTROLS)
    for item in controls:
        item["query_sha256"] = digest_text(item["query"])

    source_paths = [
        SAMPLE,
        REGISTRY,
        REGISTRY_FREEZE,
        SOURCE_AUDIT,
        QUESTION_CONTROLS,
        PHASE1_ORACLE,
        ROOT / "internal/design/contratto_ombra_prototipo_intento_12_8_2026.md",
        ROOT / "internal/design/scelte_oracolo_pendenti_12_8_2026.md",
        ROOT / "internal/design/referto_blocco_oracolo_intento_12_8_2026.md",
        ROOT / "tests/benchmarks/intent_accuracy_bench.py",
        ROOT / "tests/benchmarks/intent_compound_bench.py",
    ]
    result: dict[str, Any] = {
        "oracle_format": "metnos.intent-shadow-oracle-review/0.1",
        "contract_version": registry["contract_version"],
        "created_date": "2026-08-12",
        "review": {
            "reviewer": "AI reviewer B",
            "reviewer_type": "ai",
            "human_review": False,
            "independent": True,
            "blind_to_other_reviewer": True,
            "authority": "independent semantic adjudication proposal",
            "status": "complete_proposal_pending_cross_review",
            "model_arm_agreement_used_as_gold": False,
        },
        "judgment_policy": {
            "near_miss": "accuracy_error",
            "safe_stop": "may_be_safe_but_is_not_accurate",
            "wrong_read_or_search": "accuracy_error_not_mutation",
            "unauthorized_mutation": "only_real_state_change_or_external_effect",
            "indispensable_outside_registry_clause": (
                "whole_compound_is_unrepresentable/outside_registry; "
                "partial executable subgraphs are forbidden"
            ),
        },
        "binding": {
            "sample_path": SAMPLE.relative_to(ROOT).as_posix(),
            "sample_file_sha256": file_digest(SAMPLE),
            "sample_sha256": frozen["sample_sha256"],
            "registry_path": REGISTRY.relative_to(ROOT).as_posix(),
            "registry_file_sha256": file_digest(REGISTRY),
            "registry_payload_sha256": registry["integrity"][
                "registry_payload_sha256"
            ],
            "sources": {
                path.relative_to(ROOT).as_posix(): file_digest(path)
                for path in source_paths
            },
        },
        "counts": {
            "base_cases": len(cases),
            "new_controls": len(controls),
            "total_controls_after_append": 38,
        },
        "cases": cases,
        "new_controls": controls,
        "open_semantic_decisions": [],
        "declared_limits": [
            "The 0.1 graph records abstract intent flow, not executor arguments or backend selection.",
            "Registered operations collapse executor qualifiers into verb/object routes.",
            "This is an independent AI proposal, not a human adjudication or final cross-review verdict.",
        ],
    }
    payload = deepcopy(result)
    result["integrity"] = {
        "algorithm": "sha256",
        "convention": "canonical UTF-8 JSON excluding root integrity",
        "oracle_payload_sha256": canonical_digest(payload),
    }
    return result


def verify(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    frozen = json.loads(SAMPLE.read_text(encoding="utf-8"))
    cases = document.get("cases", [])
    controls = document.get("new_controls", [])
    if len(cases) != 120:
        errors.append("base_case_count")
    if len(controls) != 4:
        errors.append("new_control_count")
    if len({item.get("id") for item in cases}) != len(cases):
        errors.append("base_id_unique")
    if len({item.get("index") for item in cases}) != len(cases):
        errors.append("base_index_unique")
    if [item.get("index") for item in cases] != list(range(120)):
        errors.append("base_index_sequence")
    if len({item.get("query_sha256") for item in cases}) != len(cases):
        errors.append("base_hash_unique")
    if len({item.get("id") for item in controls}) != len(controls):
        errors.append("control_id_unique")
    existing_queries = set(
        json.loads(QUESTION_CONTROLS.read_text(encoding="utf-8"))["cases"]
        [index]["query"]
        for index in range(34)
    )
    control_queries = [item.get("query") for item in controls]
    if len(set(control_queries)) != len(control_queries):
        errors.append("control_query_unique")
    if existing_queries & set(control_queries):
        errors.append("control_query_duplicate_existing")

    for item in cases:
        index = item.get("index")
        if not isinstance(index, int) or not 0 <= index < 120:
            continue
        query = frozen["queries"][index]
        if item.get("query") != query:
            errors.append(f"query_text:{index}")
        if item.get("query_sha256") != digest_text(query):
            errors.append(f"query_hash:{index}")
        if not isinstance(item.get("motivation"), str) or not item["motivation"]:
            errors.append(f"motivation:{index}")
        confidence = item.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            errors.append(f"confidence:{index}")
        errors.extend(
            f"case[{index}]:{error}"
            for error in validate_expected(item.get("expected", {}), registry)
        )
    for item in controls:
        if item.get("query_sha256") != digest_text(item.get("query", "")):
            errors.append(f"control_hash:{item.get('id')}")
        errors.extend(
            f"control[{item.get('id')}]:{error}"
            for error in validate_expected(item.get("expected", {}), registry)
        )

    roots = Counter(item["expected"]["kind"] for item in cases)
    declared_counts = document.get("counts", {})
    if declared_counts.get("base_cases") != 120:
        errors.append("declared_base_count")
    if declared_counts.get("new_controls") != 4:
        errors.append("declared_control_count")
    if declared_counts.get("total_controls_after_append") != 38:
        errors.append("declared_total_control_count")
    review = document.get("review", {})
    if review.get("reviewer_type") != "ai" or review.get("human_review") is not False:
        errors.append("reviewer_ai_nonhuman")
    if review.get("independent") is not True or review.get("blind_to_other_reviewer") is not True:
        errors.append("reviewer_independence")
    if review.get("model_arm_agreement_used_as_gold") is not False:
        errors.append("model_consensus_prohibition")
    if document.get("binding", {}).get("sample_sha256") != frozen["sample_sha256"]:
        errors.append("sample_binding")
    if document.get("binding", {}).get("registry_payload_sha256") != registry[
        "integrity"
    ]["registry_payload_sha256"]:
        errors.append("registry_binding")
    for rel, declared in document.get("binding", {}).get("sources", {}).items():
        path = ROOT / rel
        if not path.is_file() or file_digest(path) != declared:
            errors.append(f"source_hash:{rel}")

    payload = deepcopy(document)
    integrity = payload.pop("integrity", {})
    if integrity.get("oracle_payload_sha256") != canonical_digest(payload):
        errors.append("oracle_payload_sha256")
    if roots["operation_graph"] + roots["system_control"] + roots[
        "unrepresentable"
    ] != 120:
        errors.append("root_count_sum")
    return sorted(set(errors))


def main() -> int:
    document = build()
    rendered = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != rendered:
        print("output_missing_or_stale")
        return 1
    loaded = json.loads(OUTPUT.read_text(encoding="utf-8"))
    errors = verify(loaded)
    roots = Counter(item["expected"]["kind"] for item in loaded["cases"])
    report = {
        "status": "ok" if not errors else "error",
        "error_count": len(errors),
        "errors": errors,
        "base_cases": len(loaded["cases"]),
        "new_controls": len(loaded["new_controls"]),
        "root_counts": dict(sorted(roots.items())),
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
