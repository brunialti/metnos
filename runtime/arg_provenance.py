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

# Nomi-arg che `args_extractor.regex_extract` sa estrarre dal testo (fonte:
# runtime/args_extractor.py::regex_extract, i rami `lname in (...)`). Tenuto in
# SYNC con l'extractor — se l'extractor impara un nome nuovo, aggiungerlo qui.
_CLAUSE_DERIVABLE_NAMES: frozenset[str] = frozenset({
    # path family
    "path", "paths", "base_path", "src", "dst",
    # url family
    "url", "urls", "src_url",
    # glob/pattern
    "pattern", "patterns", "glob",
    # recipients / email
    "to", "recipient_id", "recipients", "email", "recipient",
    # repo slug
    "repo", "repository",
    # count / cap
    "max_results", "max_total", "top", "limit", "n", "count",
    # date / window
    "date", "day", "when", "on_date",
    "time_window", "window", "since", "range",
})

PROV_RUNTIME = "runtime"
PROV_CLAUSE = "clause"
PROV_SEMANTIC = "semantic"

# Args di CONFIGURAZIONE backend per CONVENZIONE (§2.2 provider qualifier,
# ADR 0136): il runtime li risolve SEMPRE, a prescindere dal marker. Il marker
# `runtime_resolved` è applicato in modo INCOERENTE nei manifest (misurato 6/7:
# 5/35 marcati) — la convenzione è la fonte di verità più affidabile per
# QUESTI tre nomi. `provenance_report` segnala gli unmarked come cleanup.
_RUNTIME_CONFIG_NAMES: frozenset[str] = frozenset({
    "client", "account", "provider",
})


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
            if isinstance(_d, dict) and not _d.get("runtime_resolved"):
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
