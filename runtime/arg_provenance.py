"""arg_provenance.py — classificazione deterministica della PROVENIENZA degli args.

Architettura di proprietà degli args (ADR 0177 S4 / spec
`internal/design/spec_args_provenance_architecture.md`, 6/7/2026): ogni
argomento di ogni tool ha UN solo proprietario legittimo:

  - "runtime"  : config iniettata dal runtime (client/account/provider),
                 marcata `runtime_resolved` nello schema. L'LLM non li vede.
  - "clause"   : derivabile deterministicamente dal chunk della clausola —
                 (a) è un enum (estraibile via detection_lexicon), oppure
                 (b) `args_extractor.regex_extract` lo estrae per NOME
                 (path/url/pattern/count/email/date/window/repo). Ancorato
                 alla capacità REALE dell'extractor (non a un'euristica).
  - "semantic" : tutto il resto — richiede comprensione (columns, summary,
                 content, il glue). Resta all'LLM.

Questo modulo è DATI PURI: nessun side-effect, nessun cambio di comportamento.
È la pietra angolare del refactor (FASE 0): la mappa che dice a chi appartiene
ogni arg, e quindi quali guard-args sono sostituibili da uno stage
deterministico per-clausola.
"""
from __future__ import annotations

# Nomi-arg che `args_extractor.regex_extract` sa estrarre dal testo. FONTE UNICA:
# `args_extractor.CLAUSE_DERIVABLE_NAMES` (i gruppi accanto ai rami stessi). Niente
# più copia a mano — se l'extractor impara un nome, la provenienza lo segue per
# costruzione (chiude il drift `recipient` vs `to_user/to_users`, misurato 6/7).
from args_extractor import CLAUSE_DERIVABLE_NAMES as _CLAUSE_DERIVABLE_NAMES

PROV_RUNTIME = "runtime"
PROV_CLAUSE = "clause"
PROV_SEMANTIC = "semantic"

# Args di CONFIGURAZIONE backend per CONVENZIONE (§2.2 provider qualifier,
# ADR 0136). La convenzione per-NOME è un'APPROSSIMAZIONE: su alcuni tool la
# scelta porta INTENTO e l'LLM (o un guard clause-scoped) è uno scrittore
# legittimo — vedi `is_intent_bearing_config` (esito PROV.3, marcatura 6/7/2026:
# 21 marcati + 14 esenzioni; politica bloccata da
# tests/test_config_args_marking_policy.py). `provenance_report` segnala come
# cleanup SOLO gli unmarked non-esenti: n_unmarked_config > 0 = drift reale.
_RUNTIME_CONFIG_NAMES: frozenset[str] = frozenset({
    "client", "account", "provider",
})


def is_intent_bearing_config(tool_name: str, arg_name: str, arg_schema) -> bool:
    """Config-per-NOME che NON va marcata `runtime_resolved`: la scelta porta
    intento utente e l'LLM (o un guard clause-scoped) è uno scrittore legittimo.
    Tre regole (misurate 6/7/2026, razionale in
    internal/design/spec_args_provenance_architecture.md):

      1. `client` multi-provider sugli object `files`/`dirs`: clause-derived
         («su drive» → gw), lo scrivono _scope_sink_provider_to_clause /
         _align_provider_client dal TESTO della clausola (PROV.3) — il default
         sink resta local §10.3, quindi il runtime NON ne è l'unico owner.
         (`dirs` multi dal 7/7/2026: stesso modello di files.)
      2. `client` multi-provider SENZA owner runtime (object fuori da
         backend_resolver.OBJECT_BACKENDS, es. move_messages metnos|gmail):
         l'LLM è l'UNICO scrittore del ramo non-default. «Provider» = i valori
         puntano a SORGENTI-DATI note (local/metnos/google_workspace…), non a
         implementazioni della stessa sorgente (httpx|playwright = config).
      3. `account` sugli executor mail: nominato/lista/'all' — il
         mail_account_resolver delega per costruzione i casi 2+ account al
         planner («scelta ambigua: decide il planner»), e send-from è intento.

    NB: events multi-provider NON è esente (owner completo = backend_resolver
    whole-query, famiglia marcata read/delete/create_events)."""
    spec = arg_schema if isinstance(arg_schema, dict) else {}
    # Il marker esplicito e' una decisione di contratto, non un indizio:
    # prevale sulle euristiche basate su enum/nome. In particolare un client
    # multi-provider puo' essere interamente risolto dal runtime (mail/events)
    # e non deve tornare visibile al planner solo perche' l'enum e' completo.
    if spec.get("runtime_resolved"):
        return False
    name = (arg_name or "").lower()
    tool = (tool_name or "").lower()
    enum = spec.get("enum") if isinstance(spec.get("enum"), list) else []
    if name == "client" and len(enum) >= 2:
        try:
            from backend_resolver import object_of, OBJECT_BACKENDS
            obj = object_of(tool)
            known = {p for s in OBJECT_BACKENDS.values()
                     for p in s.get("providers", [])}
        except Exception:  # noqa: BLE001 — classificazione best-effort
            obj, known = None, set()
        # `metnos` = nome del provider self-hosted sui canali mail/telegram
        # (gemello di `local` sui filesystem/calendar, §10.3).
        known |= {"local", "metnos"}
        if sum(1 for v in enum if v in known) < 2:
            return False    # implementazioni (httpx|playwright), non sorgenti
        return obj in ("files", "dirs") or obj is None
    if name == "account" and "messages" in tool.split("_"):
        return True
    return False


def classify_arg(arg_name: str, arg_schema) -> str:
    """Provenienza di UN argomento dal suo schema.

    Priorità: runtime (marker O nome-config-convenzionale) > clause (enum o
    nome-estraibile) > semantic. `arg_schema` = nodo JSON-schema o None."""
    spec = arg_schema if isinstance(arg_schema, dict) else {}
    # 1. runtime: marker esplicito O nome-config per convenzione §2.2/0136.
    if spec.get("runtime_resolved") or arg_name.lower() in _RUNTIME_CONFIG_NAMES:
        return PROV_RUNTIME
    # 2. clause-derivable:
    #    (a) enum → estraibile dal testo via detection_lexicon (dominio chiuso §2.4);
    if isinstance(spec.get("enum"), list) and spec["enum"]:
        return PROV_CLAUSE
    #    (b) nome riconosciuto dall'extractor deterministico.
    if arg_name.lower() in _CLAUSE_DERIVABLE_NAMES:
        return PROV_CLAUSE
    # 3. semantic: richiede comprensione, resta all'LLM.
    return PROV_SEMANTIC


def provenance_map(tool_or_schema) -> dict[str, str]:
    """Mappa {arg_name: provenance} per un tool (Executor con args_schema) o
    direttamente per un args_schema dict. Args non dichiarati → assenti."""
    schema = getattr(tool_or_schema, "args_schema", None)
    if schema is None and isinstance(tool_or_schema, dict):
        schema = tool_or_schema
    if not isinstance(schema, dict):
        return {}
    props = schema.get("properties")
    if not isinstance(props, dict):
        return {}
    return {name: classify_arg(name, spec) for name, spec in props.items()}


def provenance_report(catalog) -> dict:
    """Report aggregato su tutto il catalogo: conteggi per classe, tool con
    args semantic (i «difficili»), tool 100%-deterministici. Per l'analisi +
    la dashboard. Deterministico, read-only."""
    by_class = {PROV_RUNTIME: 0, PROV_CLAUSE: 0, PROV_SEMANTIC: 0}
    per_tool: dict[str, dict] = {}
    tools_with_semantic: list[str] = []
    tools_fully_deterministic: list[str] = []
    unmarked_config: list[str] = []   # config runtime-per-nome ma SENZA marker
    total_args = 0
    for ex in catalog or []:
        name = getattr(ex, "name", None)
        if not isinstance(name, str):
            continue
        _sch = getattr(ex, "args_schema", None)
        _props = _sch.get("properties", {}) if isinstance(_sch, dict) else {}
        for _a in _RUNTIME_CONFIG_NAMES:
            _d = _props.get(_a)
            if (isinstance(_d, dict) and not _d.get("runtime_resolved")
                    and not is_intent_bearing_config(name, _a, _d)):
                unmarked_config.append(f"{name}.{_a}")
        pm = provenance_map(ex)
        if not pm:
            continue
        counts = {PROV_RUNTIME: 0, PROV_CLAUSE: 0, PROV_SEMANTIC: 0}
        for cls in pm.values():
            counts[cls] += 1
            by_class[cls] += 1
            total_args += 1
        per_tool[name] = {"map": pm, "counts": counts}
        if counts[PROV_SEMANTIC] > 0:
            tools_with_semantic.append(name)
        elif pm:  # ha args, tutti runtime/clause → nessun semantic
            tools_fully_deterministic.append(name)
    return {
        "total_args": total_args,
        "by_class": by_class,
        "n_tools": len(per_tool),
        "n_tools_with_semantic": len(tools_with_semantic),
        "n_tools_fully_deterministic": len(tools_fully_deterministic),
        "tools_with_semantic": sorted(tools_with_semantic),
        "unmarked_config": sorted(unmarked_config),   # cleanup manifest
        "n_unmarked_config": len(unmarked_config),
        "per_tool": per_tool,
    }
