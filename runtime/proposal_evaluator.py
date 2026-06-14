"""proposal_evaluator.py — auto-evaluator delle proposte synth (ADR 0122).

Date una proposta synth (JSON sotto `~/.local/share/metnos/synt_proposals/`),
emette un verdetto deterministico `accept|gray|reject` basato su:

KILLER (uno solo basta a REJECT):
1. **inflation**: nome viola `runtime/vocab.py` (verbo non in ACTIONS,
   oggetto non in OBJECTS, qualifier non in QUALIFIERS o famiglie 3).
2. **affinity_overlap**: Jaccard >= 0.4 verso UN executor handcrafted in
   `loader.HANDCRAFTED_FAMILIES` o un altro synth piu' vecchio.
   Soglia stretta 0.4 vs 0.5 al catalog load — l'evaluator e' piu'
   conservativo dell'admission gate.
3. **test_pass_rate**: stage 3 tests + smoke battery non al 100%.
4. **reversibility_parity**: se il path storico contiene un executor
   con `reverse_pattern`, anche il nuovo deve dichiararne uno.
5. **error_class_discriminability**: il nuovo executor non dichiara
   almeno 2 classi di errore distinte (description o codice).
6. **observation_schema_stability**: viola §2.6 (entries vs results)
   per il consumer del path.

SCORE WEIGHTED (quando nessun killer):
    score =
        (eta_speedup>=2.0)            ? +2 : (<1.2 ? -1 : 0)
        (call_freq_60d>=30)            ? +1.5 : -0.5
        (decidability>=0.7)            ? +1 : (<0.5 ? -1 : 0)
        (noising_top10_pct>=0.8)       ? +1 : 0
        pipeline_terminal              ? +1 : 0
        truncation_honest              ? +1 : 0
        (token_saving_pct>=30)         ? +1 : 0

VERDICT:
    score>=4 e nessun killer → ACCEPT
    -2<score<4 e nessun killer → GRAY (review umana)
    score<=-2 o killer triggerato → REJECT

Determinismo §7.9: niente LLM-as-judge nell'evaluator. La decidability
usa l'`intent_extractor` BoW + `prefilter.rank_with_intent` (gia' presenti).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import config as _C  # §7.11

# Default thresholds (tunable via kwargs).
ETA_SPEEDUP_ACCEPT = 2.0
ETA_SPEEDUP_PENALTY = 1.2
CALL_FREQ_60D_ACCEPT = 30
DECIDABILITY_ACCEPT = 0.7
DECIDABILITY_PENALTY = 0.5
NOISING_TOP10_ACCEPT = 0.8
TOKEN_SAVING_ACCEPT_PCT = 30
AFFINITY_OVERLAP_THRESHOLD_EVALUATOR = 0.4

# Score thresholds.
SCORE_ACCEPT = 4
SCORE_REJECT = -2

# Reformulations templates per la decidability heuristic. Per ogni
# query originale, il PLANNER simulato deve scegliere il nuovo executor
# come #1 candidato in almeno 5/N riformulazioni.
_REFORMULATION_TEMPLATES_IT: tuple[str, ...] = (
    "{q}",
    "puoi {q}?",
    "vorrei {q}",
    "ho bisogno di {q}",
    "fai questo: {q}",
    "{q}, per favore",
    "esegui: {q}",
)
_REFORMULATION_TEMPLATES_EN: tuple[str, ...] = (
    "{q}",
    "can you {q}?",
    "I need to {q}",
    "please {q}",
    "do this: {q}",
)
DECIDABILITY_MIN_PASS = 5  # almeno 5 riformulazioni vincenti


# Audit log JSONL.
_AUDIT_DIR = _C.PATH_USER_DATA / "synth_audit"


def _audit_path() -> Path:
    _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    return _AUDIT_DIR / "proposal_evaluator.jsonl"


@dataclass
class EvaluationResult:
    """Risultato deterministico dell'evaluator."""
    proposal_id: str
    name: str
    verdict: Literal["accept", "gray", "reject"]
    score: float
    killers_triggered: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "name": self.name,
            "verdict": self.verdict,
            "score": self.score,
            "killers_triggered": list(self.killers_triggered),
            "signals": dict(self.signals),
            "rationale": self.rationale,
        }


def _stage_output(proposal: dict, idx: int) -> dict:
    """Recupera output dello stage idx (1-based) della proposta."""
    stages = proposal.get("stages") or []
    if idx < 1 or idx > len(stages):
        return {}
    s = stages[idx - 1]
    if not isinstance(s, dict):
        return {}
    out = s.get("output")
    return out if isinstance(out, dict) else {}


# ─── Killer 1: vocabolario inflation ───────────────────────────────────


def _check_inflation(proposal: dict) -> tuple[bool, str]:
    """True+reason se il nome viola il vocab chiuso."""
    try:
        from vocab import ACTIONS, OBJECTS, QUALIFIERS
    except Exception as ex:
        return False, f"vocab import failed ({ex}); skip"
    name = proposal.get("name") or ""
    if not name:
        return True, "name vuoto"
    parts = name.split("_")
    if len(parts) < 2:
        return True, f"name '{name}' non ha forma azione_oggetto"
    verb = parts[0]
    obj = parts[1]
    quals = parts[2:]
    if verb not in ACTIONS:
        return True, f"verbo '{verb}' fuori dalle 22 azioni"
    if obj not in OBJECTS:
        return True, f"oggetto '{obj}' fuori dai 15 oggetti"
    for q in quals:
        if q not in QUALIFIERS:
            return True, f"qualifier '{q}' fuori dalle 3 famiglie"
    return False, ""


# ─── Killer 2: affinity overlap (soglia 0.4 stretta) ───────────────────


def _affinity_set(proposal: dict) -> set[str]:
    s4 = _stage_output(proposal, 4)
    aff = s4.get("affinity") or []
    return {str(t).strip().lower() for t in aff if t}


def _check_affinity_overlap(proposal: dict, *, catalog=None) -> tuple[bool, str, dict]:
    """True+reason se affinity Jaccard >= 0.4 vs handcrafted o synth piu' vecchio.

    Riusa `loader.HANDCRAFTED_FAMILIES` come lista canonica. Confronta
    SOLO contro executor presenti nel catalog (no I/O extra).
    """
    aff = _affinity_set(proposal)
    if not aff:
        return False, "", {}
    try:
        from loader import load_catalog, SYNTHESIZED_EXECUTORS_DIR
    except Exception as ex:
        return False, f"loader import failed ({ex}); skip", {}
    if catalog is None:
        try:
            catalog = load_catalog(verify=True)
        except Exception as ex:
            return False, f"catalog load failed ({ex}); skip", {}
    proposal_name = proposal.get("name") or ""
    proposal_ts = float(proposal.get("ts_start") or 0.0)
    best_overlap = 0.0
    best_other: str | None = None
    best_shared: list[str] = []
    for ex_name, ex in (getattr(catalog, "executors", catalog) or {}).items():
        if ex_name == proposal_name:
            continue
        other_aff = {str(t).strip().lower() for t in (getattr(ex, "affinity", []) or []) if t}
        if not other_aff:
            continue
        # Skip altri synth piu' giovani della proposta corrente.
        is_synth_other = False
        try:
            is_synth_other = str(SYNTHESIZED_EXECUTORS_DIR) in str(ex.manifest_path)
        except Exception:
            pass
        if is_synth_other and proposal_ts > 0:
            try:
                other_mtime = ex.manifest_path.stat().st_mtime
                if other_mtime >= proposal_ts:
                    continue
            except OSError:
                continue
        inter = aff & other_aff
        union = aff | other_aff
        if not union:
            continue
        j = len(inter) / len(union)
        if j > best_overlap:
            best_overlap = j
            best_other = ex_name
            best_shared = sorted(inter)
    info = {
        "best_jaccard": round(best_overlap, 3),
        "best_overlap_with": best_other,
        "shared_terms": best_shared,
    }
    if best_overlap >= AFFINITY_OVERLAP_THRESHOLD_EVALUATOR and best_other:
        return True, (
            f"affinity Jaccard {best_overlap:.2f} >= "
            f"{AFFINITY_OVERLAP_THRESHOLD_EVALUATOR} verso '{best_other}'"
        ), info
    return False, "", info


# ─── Killer 3: test pass rate ─────────────────────────────────────────


def _check_test_pass_rate(proposal: dict) -> tuple[bool, str, dict]:
    """True+reason se i test stage 3 non sono al 100% di success."""
    stages = proposal.get("stages") or []
    if len(stages) < 3:
        return True, "stage 3 (tests) assente", {"tests_total": 0, "tests_passed": 0}
    s3 = stages[2]
    if not s3.get("success"):
        return True, "stage 3 ha fallito", {"tests_total": 0, "tests_passed": 0}
    tests = (s3.get("output") or {}).get("tests") or []
    n = len(tests)
    if n == 0:
        return True, "nessun test dichiarato in stage 3", {"tests_total": 0, "tests_passed": 0}
    # birth tests sono validati al momento dell'install (synth_request._validate_birth_tests)
    # — qui assumiamo che la proposta `synthesized` sia gia' passata per quella
    # gate; un final_state diverso da `synthesized` indica failure.
    final_state = proposal.get("final_state") or ""
    if final_state != "synthesized":
        return True, f"final_state='{final_state}' indica synthesis non riuscita", {
            "tests_total": n, "tests_passed": 0, "final_state": final_state,
        }
    return False, "", {"tests_total": n, "tests_passed": n, "final_state": final_state}


# ─── Killer 4: reversibility parity ───────────────────────────────────


def _path_has_reversible_step(path_steps: list[str], catalog) -> bool:
    """True se UN executor del path ha `reverse_pattern` non None."""
    if not catalog:
        return False
    execs = getattr(catalog, "executors", catalog) or {}
    for tool in path_steps:
        ex = execs.get(tool)
        if ex is None:
            continue
        rp = getattr(ex, "reverse_pattern", None)
        if rp:
            return True
    return False


def _check_reversibility_parity(
    proposal: dict, *, path_steps: list[str], catalog,
) -> tuple[bool, str, dict]:
    s2 = _stage_output(proposal, 2)
    new_rp = s2.get("reverse_pattern")
    path_reversible = _path_has_reversible_step(path_steps, catalog)
    info = {
        "path_reversible": path_reversible,
        "new_reverse_pattern": new_rp,
    }
    if path_reversible and not new_rp:
        return True, (
            "il path conteneva un executor reversible, ma il nuovo "
            "non dichiara reverse_pattern"
        ), info
    return False, "", info


# ─── Killer 5: error_class discriminability ───────────────────────────


_ERROR_CLASS_HINTS_RE = re.compile(
    r"\b(error_class|forbidden|rate_limited|not_found|server_error|"
    r"timeout|network|non_html|js_rendered|unknown|missing|invalid|"
    r"permission_denied|conflict)\b",
    re.IGNORECASE,
)


def _extract_error_classes(proposal: dict) -> list[str]:
    """Estrae nomi di classi d'errore distinti da description + code.

    Determinismo: lista chiusa di tag noti + match `error_class:"..."`.
    """
    found: set[str] = set()
    s4 = _stage_output(proposal, 4)
    desc = s4.get("description") or ""
    s5 = _stage_output(proposal, 5)
    code = s5.get("code") or ""
    for blob in (desc, code):
        for m in _ERROR_CLASS_HINTS_RE.finditer(blob):
            tok = m.group(1).lower()
            if tok != "error_class":
                found.add(tok)
        # Match esplicito "error_class": "<nome>" o error_class='<nome>'
        for m in re.finditer(
            r"""error_class["']?\s*[:=]\s*["']([a-z_][a-z0-9_]*)["']""",
            blob, re.IGNORECASE,
        ):
            found.add(m.group(1).lower())
    return sorted(found)


def _check_error_class(proposal: dict) -> tuple[bool, str, dict]:
    classes = _extract_error_classes(proposal)
    info = {"error_classes": classes}
    if len(classes) < 2:
        return True, (
            f"il nuovo executor dichiara {len(classes)} classi d'errore; "
            f"minimo 2 distinte richieste"
        ), info
    return False, "", info


# ─── Killer 6: observation schema stability ───────────────────────────


_TRANSFORMATIVE_VERBS: frozenset[str] = frozenset({
    "move", "delete", "send", "write", "create", "extract", "compress",
    "change", "set", "order",
})


def _check_observation_schema(proposal: dict) -> tuple[bool, str, dict]:
    """§2.6: verbi che arricchiscono/leggono → `entries`. Trasformativi → `results`.

    Heuristic basata sul verbo del nome. Non strictly enforced (alcuni
    verbi hanno entrambi gli output: e.g. `compute`/`compare` ritornano
    valori scalari). Killer scatta solo per casi macroscopicamente
    incoerenti: code che ritorna `entries` per verbi trasformativi puri
    (move/delete/send/write).
    """
    name = proposal.get("name") or ""
    parts = name.split("_")
    if len(parts) < 2:
        return False, "", {"verb": None}
    verb = parts[0]
    s5 = _stage_output(proposal, 5)
    code = s5.get("code") or ""
    info = {"verb": verb}
    if verb in {"move", "delete", "send", "write", "create"}:
        # Per questi verbi attendiamo `results`; flagga incoerenza solo
        # se il code ritorna ESCLUSIVAMENTE entries (nessun results).
        has_entries = bool(re.search(r"""["']entries["']\s*:""", code))
        has_results = bool(re.search(r"""["']results["']\s*:""", code))
        if has_entries and not has_results:
            return True, (
                f"verbo trasformativo '{verb}' ma il code ritorna "
                f"`entries` invece di `results` (§2.6)"
            ), info
    return False, "", info


# ─── Killer 7: TRIVIALITY (single-executor alias / parameter-fix) ─────


def _check_triviality(
    proposal: dict, *, path_steps: list[str], catalog,
) -> tuple[bool, str, dict]:
    """Killer: la proposta sussume UN solo executor esistente con args
    sottoinsieme + valori fissi. Nessuna abstraction reale. Fix corretto:
    aggiornare il default del param nell'executor originale OPPURE
    emettere SPECIALIZE introvertiva (ADR 0077).
    """
    info: dict = {"path_len": len(path_steps), "subset": False}
    if len(path_steps) != 1:
        return False, "", info
    if not catalog:
        return False, "", info
    orig_name = path_steps[0]
    execs = getattr(catalog, "executors", catalog) or {}
    orig = execs.get(orig_name)
    if orig is None:
        info["orig_found"] = False
        return False, "", info
    info["orig_found"] = True
    s2 = _stage_output(proposal, 2)
    # Stage 2 output puo' avere args_properties piatto (helper test) o
    # args_schema.properties (manifest synth reale). Accetta entrambi.
    new_props = s2.get("args_properties")
    if new_props is None:
        sch = s2.get("args_schema") or {}
        new_props = (sch.get("properties") or {}) if isinstance(sch, dict) else {}
    if not isinstance(new_props, dict):
        new_props = {}
    orig_args_schema = getattr(orig, "args_schema", None) or {}
    if not isinstance(orig_args_schema, dict):
        return False, "", info
    orig_props = orig_args_schema.get("properties") or {}
    if not isinstance(orig_props, dict):
        orig_props = {}
    new_keys = set(new_props.keys())
    orig_keys = set(orig_props.keys())
    if not new_keys or not orig_keys:
        return False, "", info
    is_subset = new_keys.issubset(orig_keys)
    is_strict_smaller = is_subset and len(new_keys) < len(orig_keys)
    info["subset"] = is_subset
    info["new_args"] = sorted(new_keys)
    info["orig_args"] = sorted(orig_keys)
    if is_strict_smaller:
        return True, (
            f"path-len=1 vs `{orig_name}` con args sottoinsieme stretto "
            f"({len(new_keys)} < {len(orig_keys)}). Usa SPECIALIZE "
            f"introvertiva o aggiorna i default del param nell'executor "
            f"originale."
        ), info
    return False, "", info


# ─── Killer 8: CAPABILITIES (no privilege escalation) ─────────────────


def _check_capabilities(
    proposal: dict, *, path_steps: list[str], catalog,
) -> tuple[bool, str, dict]:
    """Killer: il nuovo executor dichiara capability piu' larghe di quelle
    nel path originale. Es. path operava su `/tmp/foo` ma nuovo dichiara
    `fs:write` con hint `~/**` → escalation tacita.
    """
    s2 = _stage_output(proposal, 2)
    new_caps_raw = s2.get("capabilities") or []
    info: dict = {"new_capabilities": new_caps_raw}
    if not catalog or not path_steps:
        return False, "", info
    execs = getattr(catalog, "executors", catalog) or {}
    # Unione delle capability degli executor del path
    path_caps: list[dict] = []
    for tool in path_steps:
        ex = execs.get(tool)
        if ex is None:
            continue
        for cap in (getattr(ex, "capabilities", []) or []):
            if isinstance(cap, dict):
                path_caps.append(cap)
    info["path_capabilities"] = path_caps
    # Se nuovo dichiara `fs:write` o `network:write` mai presente nel path
    new_names = {
        c.get("name") for c in new_caps_raw if isinstance(c, dict)
    }
    path_names = {
        c.get("name") for c in path_caps if isinstance(c, dict)
    }
    escalated = new_names - path_names
    high_risk = {
        "fs:write", "fs:delete", "process:kill", "network:write",
        "shell:exec", "admin",
    }
    leak = escalated & high_risk
    if leak:
        info["escalated"] = sorted(leak)
        return True, (
            f"il nuovo executor dichiara capability {sorted(leak)} non "
            f"presenti nel path originale (escalation tacita)"
        ), info
    return False, "", info


# ─── Killer 9: SAFETY (no signatures touch without admin) ─────────────


def _check_safety(proposal: dict) -> tuple[bool, str, dict]:
    """Killer: il nuovo executor opera sull'oggetto `signatures` (ADR 0071
    safety policy: blacklist/whitelist/forbidden/seed/...) senza essere
    un executor admin esplicito.
    """
    name = proposal.get("name") or ""
    info: dict = {"name": name}
    parts = name.split("_")
    if len(parts) < 2:
        return False, "", info
    obj = parts[1] if len(parts) > 1 else ""
    if obj != "signatures":
        return False, "", info
    # Se l'oggetto e' signatures, deve essere admin-tier vaglio.
    s2 = _stage_output(proposal, 2)
    caps = s2.get("capabilities") or []
    is_admin = any(
        isinstance(c, dict) and c.get("name") == "admin"
        for c in caps
    )
    info["is_admin"] = is_admin
    if not is_admin:
        return True, (
            "executor opera su `signatures` (safety policy ADR 0071) ma "
            "non dichiara capability `admin` esplicitamente"
        ), info
    return False, "", info


# ─── Killer 10: TESTABILITY (preserve dry_run support) ─────────────────


def _check_testability(
    proposal: dict, *, path_steps: list[str], catalog,
) -> tuple[bool, str, dict]:
    """Killer: il path originale supportava `dry_run=true` (preview senza
    side-effect) ma il nuovo executor non lo dichiara fra gli args.
    Perdere preview su sussunzione critical-path → negativo.
    """
    info: dict = {"path_dry_run": False, "new_dry_run": False}
    if not catalog or not path_steps:
        return False, "", info
    execs = getattr(catalog, "executors", catalog) or {}
    path_has_dry = False
    for tool in path_steps:
        ex = execs.get(tool)
        if ex is None:
            continue
        sch = getattr(ex, "args_schema", None) or {}
        if isinstance(sch, dict):
            props = sch.get("properties") or {}
            if "dry_run" in props:
                path_has_dry = True
                break
    info["path_dry_run"] = path_has_dry
    if not path_has_dry:
        return False, "", info
    s2 = _stage_output(proposal, 2)
    new_props = s2.get("args_properties")
    if new_props is None:
        sch = s2.get("args_schema") or {}
        new_props = (sch.get("properties") or {}) if isinstance(sch, dict) else {}
    if not isinstance(new_props, dict):
        new_props = {}
    new_has_dry = "dry_run" in new_props
    info["new_dry_run"] = new_has_dry
    if not new_has_dry:
        return True, (
            "il path conteneva un executor con `dry_run`, ma il nuovo "
            "non dichiara questo arg (perdita preview)"
        ), info
    return False, "", info


# ─── Signal: ETA speedup ──────────────────────────────────────────────


def _eta_speedup(
    proposal: dict, *, default_new_p50_ms: int = 1500,
) -> tuple[float, dict]:
    """Calcola path_eta_p50_ms / new_executor_latency_p50_ms.

    `path_eta_p50_ms` deve essere salvato nella proposta da
    `synth_request.handle_synth_request` (lookup proposals_eta_index).
    Se mancante, ritorna 0.0 (signal neutro: l'evaluator interpreta come
    "non disponibile" → score 0).

    `new_executor_latency_p50_ms`: per ora usa un default conservativo
    (1500ms = 1.5s) se non dichiarato in proposta. Estensibile in futuro
    con bench live durante stage 5.
    """
    p50_path = proposal.get("path_eta_p50_ms")
    p50_new = proposal.get("new_executor_latency_p50_ms") or default_new_p50_ms
    info: dict[str, Any] = {
        "path_eta_p50_ms": p50_path,
        "new_executor_latency_p50_ms": p50_new,
    }
    if not isinstance(p50_path, (int, float)) or p50_path <= 0:
        return 0.0, info
    if not isinstance(p50_new, (int, float)) or p50_new <= 0:
        return 0.0, info
    speedup = float(p50_path) / float(p50_new)
    info["eta_speedup"] = round(speedup, 3)
    return speedup, info


# ─── Signal: call frequency 60d ───────────────────────────────────────


def _call_frequency(proposal: dict) -> tuple[int, dict]:
    n = proposal.get("path_call_count_60d")
    info = {"path_call_count_60d": n}
    if isinstance(n, (int, float)) and n >= 0:
        return int(n), info
    return 0, info


# ─── Signal: decidability heuristic ───────────────────────────────────


def _bow_intent_simple(query: str) -> dict:
    """BoW intent leggero per simulare il PLANNER offline.

    Riusa lo stesso pattern di `smoke._bow_intent_for_smoke` ma in
    forma indipendente per non importare `smoke.py` (che ha import
    pesanti del catalog). Determinismo §7.9.
    """
    q = (query or "").lower()
    verb = None
    if any(t in q for t in ("trova", "cerca", "find", "search")):
        verb = "find"
    elif any(t in q for t in ("elenca", "lista", "list")):
        verb = "list"
    elif any(t in q for t in ("leggi", "read")):
        verb = "read"
    elif any(t in q for t in ("scrivi", "write", "salva")):
        verb = "write"
    elif any(t in q for t in ("sposta", "move", "rinomina")):
        verb = "move"
    elif any(t in q for t in ("cancella", "delete", "rimuovi")):
        verb = "delete"
    elif any(t in q for t in ("invia", "send", "manda")):
        verb = "send"
    elif any(t in q for t in ("riassumi", "describe", "summarize")):
        verb = "describe"
    elif any(t in q for t in ("scarica", "download", "url", "https://", "http://")):
        verb = "get"
    elif any(t in q for t in ("comprimi", "compress", "zippa")):
        verb = "compress"
    obj = None
    if any(t in q for t in ("file", "files")):
        obj = "files"
    elif any(t in q for t in ("foto", "immagin", "photo", "image")):
        obj = "images"
    elif any(t in q for t in ("mail", "email", "messaggi", "message")):
        obj = "messages"
    elif any(t in q for t in ("url", "web", "internet", "online", "https://", "http://")):
        obj = "urls"
    elif any(t in q for t in ("processi", "stato", "sistema", "system", "service")):
        obj = "processes"
    elif any(t in q for t in ("cartella", "directory", "dir ", "dirs")):
        obj = "dirs"
    if not verb and not obj:
        return {}
    return {"verb": verb, "object": obj}


def _decidability(
    proposal: dict, *, catalog,
) -> tuple[float, dict]:
    """Heuristic: il PLANNER (intent_extractor BoW + prefilter) seleziona
    il nuovo executor su 5+ riformulazioni della query originale.

    Ritorna (pct_pass, info). pct_pass = riformulazioni in cui
    `ranked[0].name == proposal.name` / totale.
    """
    name = proposal.get("name") or ""
    user_query = (proposal.get("user_query") or proposal.get("intent") or "").strip()
    info: dict[str, Any] = {
        "user_query": user_query[:120],
        "name": name,
        "reformulations_total": 0,
        "reformulations_pass": 0,
        "min_pass_required": DECIDABILITY_MIN_PASS,
    }
    if not name or not user_query:
        return 0.0, info
    if catalog is None:
        info["skip"] = "catalog unavailable"
        return 0.0, info
    try:
        from prefilter import rank_with_intent, rank as _rank_plain
    except Exception as ex:
        info["skip"] = f"prefilter import failed: {ex}"
        return 0.0, info

    templates = list(_REFORMULATION_TEMPLATES_IT) + list(_REFORMULATION_TEMPLATES_EN)
    n_pass = 0
    for tpl in templates:
        q = tpl.format(q=user_query)
        intent = _bow_intent_simple(q)
        ranked = []
        try:
            if intent:
                ranked = rank_with_intent(q, catalog, intent, k=5) or []
            if not ranked:
                ranked = _rank_plain(q, catalog, k=5) or []
        except Exception:
            ranked = []
        if not ranked:
            continue
        first = ranked[0]
        first_name = getattr(first, "name", None) or (first.get("name") if isinstance(first, dict) else None)
        if first_name == name:
            n_pass += 1
    info["reformulations_total"] = len(templates)
    info["reformulations_pass"] = n_pass
    pct = n_pass / max(1, len(templates))
    info["decidability_pct"] = round(pct, 3)
    return pct, info


# ─── Signal: noising top-10% ──────────────────────────────────────────


def _noising_top10(
    proposal: dict, *, catalog, path_queries: list[str] | None = None,
) -> tuple[float, dict]:
    """Pct di query del path storico in cui il nuovo executor risale nel top-10.

    `path_queries`: lista delle query utente che storicamente hanno percorso
    lo shape (caller responsibility — accumulate dal turn JSONL). Se vuota,
    usa la `user_query` come unico campione (fallback).
    """
    name = proposal.get("name") or ""
    info: dict[str, Any] = {"name": name, "samples": 0, "top10": 0}
    if not name or catalog is None:
        return 0.0, info
    samples = list(path_queries or [])
    if not samples:
        uq = (proposal.get("user_query") or "").strip()
        if uq:
            samples = [uq]
    if not samples:
        return 0.0, info
    try:
        from prefilter import rank_with_intent, rank as _rank_plain
    except Exception as ex:
        info["skip"] = f"prefilter import failed: {ex}"
        return 0.0, info
    n_top10 = 0
    for q in samples:
        intent = _bow_intent_simple(q)
        try:
            ranked = rank_with_intent(q, catalog, intent, k=10) or []
            if not ranked:
                ranked = _rank_plain(q, catalog, k=10) or []
        except Exception:
            ranked = []
        names = [
            (getattr(r, "name", None) or (r.get("name") if isinstance(r, dict) else None))
            for r in ranked
        ]
        if name in names:
            n_top10 += 1
    info["samples"] = len(samples)
    info["top10"] = n_top10
    pct = n_top10 / max(1, len(samples))
    info["noising_top10_pct"] = round(pct, 3)
    return pct, info


# ─── Signal: pipeline_terminal + truncation_honest + token_saving ─────


def _pipeline_terminal(proposal: dict) -> bool:
    """Se la proposta dichiara `pipeline_terminal=True`, true. Default false.

    Heuristic: il nome contiene un verbo trasformativo (move/delete/send/...)
    sono per natura terminali nella pipeline (chiudono la sequenza).
    """
    flag = proposal.get("pipeline_terminal")
    if isinstance(flag, bool):
        return flag
    name = proposal.get("name") or ""
    parts = name.split("_")
    return bool(parts and parts[0] in _TRANSFORMATIVE_VERBS)


def _truncation_honest(proposal: dict) -> bool:
    """True se il code menziona `truncated` (cap visibility, §2.7).

    Heuristic: scarica codice stage 5 e cerca `truncated`. Pessimistic
    fallback: False se code assente.
    """
    s5 = _stage_output(proposal, 5)
    code = s5.get("code") or ""
    return bool(re.search(r'\btruncated\b', code))


def _token_saving_pct(proposal: dict) -> tuple[float, dict]:
    """Pct di token risparmiati rispetto al path multi-step.

    Approssimato come (path_steps - 1) / path_steps * 100, dove
    `path_steps` = lunghezza dello shape originale. Se la sostituzione
    riduce 3 step a 1, il saving e' 66%.
    """
    n = proposal.get("path_n_steps") or 0
    info = {"path_n_steps": n}
    if not isinstance(n, (int, float)) or n <= 1:
        return 0.0, info
    pct = (float(n) - 1.0) / float(n) * 100.0
    info["token_saving_pct"] = round(pct, 1)
    return pct, info


# ─── Verdict & scoring ────────────────────────────────────────────────


def _score_signals(signals: dict) -> float:
    """Score weighted dei 6 signal canonici: ETA, VOLUME, DECIDABILITY,
    NOISING, TERMINAL, TRUNCATION. Token saving rimosso (subordinato a
    ETA per LLM locale §10.3, decisione utente 10/5/2026).
    """
    score = 0.0
    eta = float(signals.get("eta_speedup") or 0.0)
    if eta >= ETA_SPEEDUP_ACCEPT:
        score += 2.0
    elif eta < ETA_SPEEDUP_PENALTY and eta > 0:
        score -= 1.0
    cf = int(signals.get("call_freq_60d") or 0)
    if cf >= CALL_FREQ_60D_ACCEPT:
        score += 1.5
    else:
        score -= 0.5
    dc = float(signals.get("decidability_pct") or 0.0)
    if dc >= DECIDABILITY_ACCEPT:
        score += 1.0
    elif dc < DECIDABILITY_PENALTY:
        score -= 1.0
    ns = float(signals.get("noising_top10_pct") or 0.0)
    if ns >= NOISING_TOP10_ACCEPT:
        score += 1.0
    if signals.get("pipeline_terminal"):
        score += 1.0
    if signals.get("truncation_honest"):
        score += 1.0
    return round(score, 2)


def evaluate_proposal(
    proposal_path: str | Path,
    *,
    catalog=None,
    path_queries: list[str] | None = None,
    audit: bool = True,
) -> EvaluationResult:
    """Punto di ingresso: legge una proposta JSON, emette il verdetto.

    `proposal_path`: path al file `<id>.json` in synt_proposals/.
    `catalog`: opzionale, se non passato lo carica via `loader.load_catalog`.
    `path_queries`: lista di query storiche per il signal noising; se non
        passata, l'evaluator usa solo la `user_query` della proposta.
    `audit`: se True, append il verdict al log JSONL.
    """
    p = Path(proposal_path)
    if not p.exists():
        raise FileNotFoundError(f"proposal file non trovato: {p}")
    try:
        proposal = json.loads(p.read_text(encoding="utf-8"))
    except (TypeError, ValueError) as ex:
        raise ValueError(f"proposta JSON malformata: {ex}") from ex

    proposal_id = proposal.get("id") or p.stem
    name = proposal.get("name") or proposal.get("expected_name") or "?"

    if catalog is None:
        try:
            from loader import load_catalog
            catalog = load_catalog(verify=True)
        except Exception:
            catalog = None

    killers: list[str] = []
    rationale_parts: list[str] = []
    signals: dict[str, Any] = {}

    # --- Killer checks (run all to populate audit info) ---------------
    # Naming canonico (10 killer): INFLATION, OVERLAP, DEFECTIVENESS,
    # REVERSIBILITY, DISCRIMINABILITY, PLUGABILITY, CAPABILITIES, SAFETY,
    # TESTABILITY, TRIVIALITY. Snake_case nei campi `signals`.
    triggered, reason = _check_inflation(proposal)
    signals["inflation"] = {"triggered": triggered, "reason": reason}
    if triggered:
        killers.append("inflation")
        rationale_parts.append(f"INFLATION: {reason}")

    triggered, reason, info = _check_affinity_overlap(proposal, catalog=catalog)
    signals["overlap"] = {**info, "triggered": triggered, "reason": reason}
    if triggered:
        killers.append("overlap")
        rationale_parts.append(f"OVERLAP: {reason}")

    triggered, reason, info = _check_test_pass_rate(proposal)
    signals["defectiveness"] = {**info, "triggered": triggered, "reason": reason}
    if triggered:
        killers.append("defectiveness")
        rationale_parts.append(f"DEFECTIVENESS: {reason}")

    # Path steps from proposta (`path_steps` field) o ricostruito dallo
    # shape sample. Usiamo lookup eta index per recuperare sample_steps.
    path_steps: list[str] = list(proposal.get("path_steps") or [])
    if not path_steps and proposal.get("path_hash"):
        try:
            from proposals_eta_index import lookup as _lookup_eta
            rec = _lookup_eta(proposal.get("path_hash") or "")
            if rec:
                path_steps = list(rec.get("sample_steps") or [])
        except Exception:
            pass

    triggered, reason, info = _check_reversibility_parity(
        proposal, path_steps=path_steps, catalog=catalog,
    )
    signals["reversibility"] = {**info, "triggered": triggered, "reason": reason}
    if triggered:
        killers.append("reversibility")
        rationale_parts.append(f"REVERSIBILITY: {reason}")

    triggered, reason, info = _check_error_class(proposal)
    signals["discriminability"] = {**info, "triggered": triggered, "reason": reason}
    if triggered:
        killers.append("discriminability")
        rationale_parts.append(f"DISCRIMINABILITY: {reason}")

    triggered, reason, info = _check_observation_schema(proposal)
    signals["plugability"] = {**info, "triggered": triggered, "reason": reason}
    if triggered:
        killers.append("plugability")
        rationale_parts.append(f"PLUGABILITY: {reason}")

    triggered, reason, info = _check_triviality(
        proposal, path_steps=path_steps, catalog=catalog,
    )
    signals["triviality"] = {**info, "triggered": triggered, "reason": reason}
    if triggered:
        killers.append("triviality")
        rationale_parts.append(f"TRIVIALITY: {reason}")

    triggered, reason, info = _check_capabilities(
        proposal, path_steps=path_steps, catalog=catalog,
    )
    signals["capabilities"] = {**info, "triggered": triggered, "reason": reason}
    if triggered:
        killers.append("capabilities")
        rationale_parts.append(f"CAPABILITIES: {reason}")

    triggered, reason, info = _check_safety(proposal)
    signals["safety"] = {**info, "triggered": triggered, "reason": reason}
    if triggered:
        killers.append("safety")
        rationale_parts.append(f"SAFETY: {reason}")

    triggered, reason, info = _check_testability(
        proposal, path_steps=path_steps, catalog=catalog,
    )
    signals["testability"] = {**info, "triggered": triggered, "reason": reason}
    if triggered:
        killers.append("testability")
        rationale_parts.append(f"TESTABILITY: {reason}")

    # --- Signals (always computed, anche se killer triggerati) --------
    eta_v, eta_info = _eta_speedup(proposal)
    signals.update(eta_info)
    signals["eta_speedup"] = round(eta_v, 3) if eta_v else 0.0

    cf_v, cf_info = _call_frequency(proposal)
    signals.update(cf_info)
    signals["call_freq_60d"] = cf_v

    dc_v, dc_info = _decidability(proposal, catalog=catalog)
    signals.update(dc_info)

    ns_v, ns_info = _noising_top10(
        proposal, catalog=catalog, path_queries=path_queries,
    )
    signals.update(ns_info)

    signals["pipeline_terminal"] = _pipeline_terminal(proposal)
    signals["truncation_honest"] = _truncation_honest(proposal)

    ts_v, ts_info = _token_saving_pct(proposal)
    signals.update(ts_info)

    score = _score_signals(signals)

    if killers:
        verdict: Literal["accept", "gray", "reject"] = "reject"
        rationale = "REJECT (killer): " + "; ".join(rationale_parts)
    elif score >= SCORE_ACCEPT:
        verdict = "accept"
        rationale = (
            f"ACCEPT (score={score}): "
            f"eta_speedup={signals.get('eta_speedup')}, "
            f"call_freq_60d={signals.get('call_freq_60d')}, "
            f"decidability_pct={signals.get('decidability_pct')}, "
            f"noising_top10_pct={signals.get('noising_top10_pct')}"
        )
    elif score <= SCORE_REJECT:
        verdict = "reject"
        rationale = (
            f"REJECT (score={score}<= {SCORE_REJECT}): segnali insufficienti"
        )
    else:
        verdict = "gray"
        rationale = (
            f"GRAY (score={score}): review umana necessaria"
        )

    result = EvaluationResult(
        proposal_id=proposal_id,
        name=name,
        verdict=verdict,
        score=score,
        killers_triggered=killers,
        signals=signals,
        rationale=rationale,
    )

    if audit:
        try:
            ap = _audit_path()
            with ap.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": time.time(),
                    "proposal_id": proposal_id,
                    "name": name,
                    "verdict": verdict,
                    "score": score,
                    "killers": killers,
                    "rationale": rationale,
                }, ensure_ascii=False) + "\n")
        except OSError:
            pass

    return result


__all__ = [
    "EvaluationResult",
    "evaluate_proposal",
    "SCORE_ACCEPT",
    "SCORE_REJECT",
    "AFFINITY_OVERLAP_THRESHOLD_EVALUATOR",
    "DECIDABILITY_MIN_PASS",
]
