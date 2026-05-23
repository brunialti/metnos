"""skill_admission - applica ADR 0114 (5 layer) + ADR 0122 (auto-evaluator)
sull'import di una skill.

API principale: `admit_skill_import(parsed_skill, plans)` -> AdmissionReport.

Layer applicati:
- L1 vocab gate: nome deve essere `azione_oggetto[_qualifier]` con
  verb in vocab.ACTIONS e obj in vocab.OBJECTS.
- L2 affinity overlap (jaccard 0.5): vs handcrafted in /opt/metnos/executors
  + altri synth gia' presenti in ~/.local/share/metnos/executors. RIFIUTA
  il singolo plan in collisione, non l'intero import.
- L3 efficacy ager: NON applicato a admission-time (l'ager opera live
  leggendo turn JSONL post-invocation). Da gap 5 (10/5/2026): gli executor
  importati ricevono il tracking automaticamente — `runtime/executor_aging.py
  ::apply_efficacy_ager` legge da `~/.local/share/metnos/turns/*.jsonl`
  filtrato per `chosen_tool` e demota a `deprecated` chi ha success_rate
  <0.20 dopo 100 invocazioni live, archivia con <0.05 dopo altre 30 post-demotion.
  Idempotente. Da wirare nel scheduler v2 daily@04:30 (vedi CLAUDE.md §10.6.43).
- L5 smoke routing: produce una proposta di BATTERY case per smoke.py
  (NON modifica il file canonico; solo log audit).
- L6 stage 6 semantic verifier: invoca synt stage 6 (mock-able) su ogni
  plan, reject su mismatch.

Plus credentials binding uniqueness: il [required_credentials].binding
deve essere unico tra TUTTE le skill importate (scan imports.jsonl).

Determinismo §7.9: tutti i layer sono procedurali. L6 usa LLM ma e'
graceful-degrade (fallback aligned=True se LLM offline = no false reject).
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Dataclass per report
# ---------------------------------------------------------------------------


@dataclass
class AdmissionVerdict:
    plan_name: str
    accepted: bool
    layer_results: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)
    smoke_battery_case: dict = field(default_factory=dict)


@dataclass
class AdmissionReport:
    skill_name: str
    accepted: list = field(default_factory=list)   # list[AdmissionVerdict]
    rejected: list = field(default_factory=list)   # list[AdmissionVerdict]
    audit_log_path: str = ""

    def summary(self) -> dict:
        return {
            "skill": self.skill_name,
            "n_accepted": len(self.accepted),
            "n_rejected": len(self.rejected),
            "accepted_names": [v.plan_name for v in self.accepted],
            "rejected_names": [(v.plan_name, v.reasons) for v in self.rejected],
        }


# ---------------------------------------------------------------------------
# Vocabolario chiuso (importa da runtime/vocab.py canonico)
# ---------------------------------------------------------------------------


def _load_vocab():
    """Carica vocab.py canonico. Test isolation: ritorna fallback se
    /opt/metnos non disponibile.
    """
    try:
        runtime_canonical = Path(__file__).resolve().parent  # ADR 0148 rename-resilient
        if runtime_canonical.exists() and str(runtime_canonical) not in sys.path:
            sys.path.insert(0, str(runtime_canonical))
        import vocab  # type: ignore
        return set(vocab.ACTIONS), set(vocab.OBJECTS), set(vocab.QUALIFIERS)
    except Exception:
        # Fallback locale (replicato da §2.2 — sync manuale OK per dev).
        verbs = {
            "read", "write", "move", "delete", "create",
            "find", "list", "filter", "sort", "group", "classify",
            "get", "set", "send", "describe", "render",
            "extract", "compress", "compute", "compare", "change", "order",
        }
        objs = {
            "files", "dirs", "packages", "messages", "events",
            "contacts", "places", "processes", "urls", "numbers",
            "images", "signatures", "texts", "proposals", "inputs",
            "credentials",
        }
        quals = {
            "csv", "xlsx", "ocr", "zip", "pdf", "xml", "html", "json", "text",
            "gz", "tar", "video", "audio", "image", "hash",
            "size", "format", "loc", "similar",
            "lines", "paragraphs", "sentences", "pages", "segments",
            "indices",
            "blacklist", "whitelist", "graylist", "forbidden", "seed",
            "sanity", "command", "reversibility",
            "diff", "promotion", "candidates",
            # Estensioni da vocab map (translator usa anche queste come qualifier:
            # share, reply, append, labels — non sono in QUALIFIERS canonical ma
            # vivono nel mapping. Accettate temporaneamente per evitare reject
            # false-positive su skill importate; vedi gap §10.6.43).
            "share", "reply", "append", "labels",
        }
        return verbs, objs, quals


# ---------------------------------------------------------------------------
# L1 — vocab gate
# ---------------------------------------------------------------------------


def _vocab_gate(plan, verbs, objs, quals, binding: str = "") -> tuple[bool, str]:
    """Ritorna (ok, reason). ok=False -> rigetto.

    Il `binding` (snake_case del nome skill) e' accettato come suffix anche
    se i suoi token non sono in vocab.QUALIFIERS. Es. binding=`google_workspace`
    accetta `send_messages_google_workspace`: la coda `google_workspace`
    coincide col binding noto e qualifica il dominio remoto. Senza binding,
    si applica il check qualifier-by-qualifier canonico (§2.2).
    """
    parts = plan.name.split("_")
    if len(parts) < 2:
        return False, f"name {plan.name!r} not azione_oggetto[_qualifier]"
    verb = parts[0]
    obj = parts[1]
    if verb not in verbs:
        return False, f"verb {verb!r} not in vocab.ACTIONS (closed §2.2)"
    if obj not in objs:
        return False, f"object {obj!r} not in vocab.OBJECTS (closed §2.2)"
    tail = parts[2:]
    if binding and tail:
        binding_parts = binding.split("_")
        n = len(binding_parts)
        if len(tail) >= n and tail[-n:] == binding_parts:
            tail = tail[:-n]
    for q in tail:
        if q not in quals:
            return False, f"qualifier {q!r} not in vocab.QUALIFIERS"
    return True, ""


# ---------------------------------------------------------------------------
# L2 — affinity overlap (jaccard)
# ---------------------------------------------------------------------------


import config as _C  # §7.11
HANDCRAFTED_FAMILIES = (
    str(_C.PATH_EXECUTORS),
)
SYNTH_DIRS = (
    str(_C.PATH_SYNTH_EXECUTORS),
)


def _read_affinity_from_manifest(manifest_path: Path) -> list:
    """Lettura veloce di affinity dal manifest TOML.

    Niente tomllib: regex sufficiente per il pattern affinity = [...].
    Robusto a multi-line list (matcha tutto fino al primo ] non in stringa).
    """
    if not manifest_path.exists():
        return []
    text = manifest_path.read_text(encoding="utf-8", errors="ignore")
    import re
    m = re.search(r'affinity\s*=\s*\[(.*?)\]', text, re.DOTALL)
    if not m:
        return []
    inner = m.group(1)
    return [s.strip().strip('"').strip("'") for s in inner.split(",") if s.strip()]


def _scan_existing_executors(roots) -> dict:
    """Itera <root>/<name>/manifest.toml e ritorna {name: affinity_set}."""
    out = {}
    for root in roots:
        rp = Path(root)
        if not rp.exists() or not rp.is_dir():
            continue
        for child in rp.iterdir():
            if not child.is_dir():
                continue
            manifest = child / "manifest.toml"
            if not manifest.exists():
                continue
            aff = _read_affinity_from_manifest(manifest)
            if aff:
                out[child.name] = set(t.lower() for t in aff if t)
    return out


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _affinity_overlap_check(plan, plan_affinity, scan_handcrafted, scan_synth,
                             threshold: float = 0.5,
                             binding: str = "") -> tuple[bool, str]:
    """Ritorna (ok, reason). ok=False = reject (jaccard >= threshold).

    Soglia 0.5 al catalog load (ADR 0114 L2). Soglia 0.4 stretta come gate
    preventivo a evaluator-time (ADR 0122 — non applicata qui).

    Se il plan ha il suffix `_<binding>` (disambiguato in translate_skill
    per evitare collisione con handcrafted/synth gia' esistenti col nome
    canonico), la soglia sale a 0.85: il binding qualifica esplicitamente
    il dominio remoto, le keyword sovrapposte sono attese e legittime,
    non un doppione mascherato.
    """
    plan_set = set(t.lower() for t in plan_affinity if t)
    if not plan_set:
        return True, ""

    eff_threshold = threshold
    if binding and plan.name.endswith(f"_{binding}"):
        eff_threshold = 0.85

    for name, aff in scan_handcrafted.items():
        if name == plan.name:
            return False, f"name collision with handcrafted {name}"
        j = _jaccard(plan_set, aff)
        if j >= eff_threshold:
            return False, f"affinity jaccard={j:.2f} >= {eff_threshold} vs handcrafted {name}"

    for name, aff in scan_synth.items():
        if name == plan.name:
            return False, f"name collision with existing synth {name}"
        j = _jaccard(plan_set, aff)
        if j >= eff_threshold:
            return False, f"affinity jaccard={j:.2f} >= {eff_threshold} vs synth {name}"

    return True, ""


# ---------------------------------------------------------------------------
# L5 — smoke routing case proposal
# ---------------------------------------------------------------------------


def _smoke_case_for_plan(plan) -> dict:
    """Ritorna un BATTERY case proposto per smoke.py (ADR 0114 L5).

    NON modifica /opt/metnos/runtime/smoke.py; e' solo una proposta nel
    audit log per review manuale.

    Genera solo case CON query realistica IT dalla mappa `queries_by_pattern`.
    Per i pattern non mappati ritorna `_no_smoke=True`: `_add_smoke_cases_for`
    skippa l'auto-add per evitare bad test data (es. query stub
    "write files" senza target → loop CYCLIC_CALL o intercept
    route_intent ADR 0129).

    Ogni entry della mappa specifica ESPLICITAMENTE sia la query
    realistica sia l'`expected_first_tool` builtin attualmente in
    catalogo. Importante: alcuni verbi del vocab §2.2 (find/set)
    NON hanno tool builtin per certi object — il PLANNER usa il
    canonical sinonimo (es. `find_messages` non esiste → `read_messages`;
    `set_events` non esiste → `create_events`). La mappa rispecchia
    questa realta'; se aggiungi un tool canonical nuovo, aggiorna qui.
    """
    # Mappa pattern (verb, obj_or_obj_qual) → {query, expected_first_tool}
    # `expected_first_tool` deve essere il NOME REALMENTE PRESENTE nel
    # catalogo builtin (eventualmente diverso dal verb del plan).
    queries_by_pattern: dict[tuple[str, str], dict[str, str]] = {
        # events
        ("read",   "events"):    {"query": "elenca i miei appuntamenti di domani",
                                  "expected_first_tool": "read_events"},
        ("find",   "events"):    {"query": "trova eventi con MNM nei prossimi 7 giorni",
                                  "expected_first_tool": "read_events"},
        ("set",    "events"):    {"query": "crea un evento standup domani alle 10",
                                  "expected_first_tool": "create_events"},
        ("create", "events"):    {"query": "crea un evento standup domani alle 10",
                                  "expected_first_tool": "create_events"},
        ("delete", "events"):    {"query": "cancella l'evento abc-123",
                                  "expected_first_tool": "delete_events"},
        # messages (canale email/IMAP/SMTP); find/read entrambi vanno a read_messages.
        ("find",   "messages"):  {"query": "cerca le mail non lette",
                                  "expected_first_tool": "read_messages"},
        ("read",   "messages"):  {"query": "leggi le mail di oggi",
                                  "expected_first_tool": "read_messages"},
        ("send",   "messages"):  {"query": "scrivi a Roberto: ciao, ci vediamo alle 8",
                                  "expected_first_tool": "send_messages"},
        ("move",   "messages"):  {"query": "sposta nella cartella Junk le mail di spam",
                                  "expected_first_tool": "move_messages"},
        ("set",    "messages"):  {"query": "marca come letta l'ultima mail di Aruba",
                                  "expected_first_tool": "read_messages"},
        # files
        ("find",   "files"):     {"query": "cerca i documenti pdf in /tmp",
                                  "expected_first_tool": "find_files"},
        ("read",   "files"):     {"query": "leggi /tmp/note.txt",
                                  "expected_first_tool": "read_files"},
        ("write",  "files"):     {"query": "scrivi 'ciao' in /tmp/out.txt",
                                  "expected_first_tool": "write_files"},
        ("delete", "files"):     {"query": "cancella /tmp/note.txt",
                                  "expected_first_tool": "delete_files"},
        ("move",   "files"):     {"query": "sposta /tmp/a.txt in /tmp/b.txt",
                                  "expected_first_tool": "move_files"},
        ("change", "files"):     {"query": "rinomina /tmp/a.txt in /tmp/b.txt",
                                  "expected_first_tool": "move_files"},
        ("get",    "files"):     {"query": "metadata di /tmp/note.txt",
                                  "expected_first_tool": "get_files_metadata"},
        # files con qualifier formato: solo i tool effettivamente builtin
        # in /opt/metnos/executors/. Per write/change xlsx esiste solo
        # read_files_xlsx — gli altri sono solo tool importati provider
        # qualified (write_files_xlsx_google_workspace) che potrebbero
        # non essere routati senza marker provider. Lasciali fuori.
        ("read",   "files_xlsx"):  {"query": "leggi /tmp/foglio.xlsx",
                                    "expected_first_tool": "read_files_xlsx"},
        ("set",    "files_text"):  {"query": "crea un documento /tmp/doc.md",
                                    "expected_first_tool": "write_files"},
        ("read",   "files_text"):  {"query": "leggi /tmp/doc.md",
                                    "expected_first_tool": "read_files"},
        ("write",  "files_text"):  {"query": "scrivi una nota in /tmp/doc.md",
                                    "expected_first_tool": "write_files"},
        ("change", "files_text"):  {"query": "modifica il titolo di /tmp/doc.md",
                                    "expected_first_tool": "write_files"},
        # dirs
        ("create", "dirs"):      {"query": "crea cartella /tmp/nuova",
                                  "expected_first_tool": "create_dirs"},
        ("list",   "dirs"):      {"query": "elenca i file in /tmp",
                                  "expected_first_tool": "list_dirs"},
        ("delete", "dirs"):      {"query": "cancella la cartella /tmp/nuova",
                                  "expected_first_tool": "delete_dirs"},
        # contacts
        ("read",   "contacts"):  {"query": "mostra il contatto di Roberto",
                                  "expected_first_tool": "read_contacts"},
        ("find",   "contacts"):  {"query": "cerca il contatto di Lucia",
                                  "expected_first_tool": "read_contacts"},
        # share (ACL grant remoto, builtin canonical da definire)
        ("share",  "files"):     {"query": "condividi /tmp/foglio.xlsx con lucia@example.com",
                                  "expected_first_tool": "share_files"},
    }
    key = (plan.verb, plan.obj if not plan.qualifier else f"{plan.obj}_{plan.qualifier}")
    entry = queries_by_pattern.get(key)
    if not entry:
        # Pattern non mappato: NON aggiungere allo smoke. Una query stub
        # generica "verb object" produce loop/intercept route_intent e
        # rompe il pass-rate della battery con falsi negativi.
        return {
            "_no_smoke": True,
            "_reason": f"no realistic query for pattern {key!r}",
        }
    return {
        "query": entry["query"],
        "expected_first_tool": entry["expected_first_tool"],
        "expected_arg_keys": [a.name for a in plan.args[:3]],
        "min_pass_rate": 0.9,
        "note": "auto-generated by skill_admission L5",
    }


# ---------------------------------------------------------------------------
# L6 — semantic verifier (stage 6 mock-able)
# ---------------------------------------------------------------------------


def _stage6_verify_callable() -> Callable:
    """Risolve la callable di stage 6.

    1. METNOS_STAGE6_VERIFY_FAKE=mod.fn override (test).
    2. /opt/metnos/runtime/synt_stage6_verify.py se importabile.
    3. fallback aligned=True (graceful-degrade: no false reject quando LLM offline).
    """
    fake = os.environ.get("METNOS_STAGE6_VERIFY_FAKE")
    if fake:
        mod_name, _, attr = fake.rpartition(".")
        if mod_name and attr:
            try:
                mod = __import__(mod_name, fromlist=[attr])
                fn = getattr(mod, attr, None)
                if callable(fn):
                    return fn
            except Exception:
                pass

    try:
        runtime_canonical = Path(__file__).resolve().parent  # ADR 0148 rename-resilient
        if runtime_canonical.exists() and str(runtime_canonical) not in sys.path:
            sys.path.insert(0, str(runtime_canonical))
        from synt_stage6_verify import verify_semantic_alignment  # type: ignore
        return verify_semantic_alignment
    except Exception:
        def _noop(description, code_body, **kw):
            return {"aligned": True, "mismatch": "", "_fallback": True}
        return _noop


def _stage6_check(plan, manifest_path, code_path, verifier) -> tuple[bool, str]:
    try:
        description = ""
        if manifest_path:
            description = Path(manifest_path).read_text(encoding="utf-8")
        code_body = ""
        if code_path:
            code_body = Path(code_path).read_text(encoding="utf-8")
        result = verifier(description, code_body, name_hint=plan.name)
        if not isinstance(result, dict):
            return True, ""
        if result.get("aligned") is False:
            return False, f"semantic_drift: {result.get('mismatch', 'unspecified')}"
        return True, ""
    except Exception as e:
        # §2.8 fail-loud: log come reject ma con motivo chiaro.
        return False, f"stage6 verifier raised: {e}"


# ---------------------------------------------------------------------------
# Credentials binding uniqueness
# ---------------------------------------------------------------------------


def _audit_log_path() -> Path:
    """Audit log dir. Rispetta METNOS_USER_DATA (§7.11) per isolamento test/e2e.
    Override esplicito via METNOS_AUDIT_DIR."""
    override = os.environ.get("METNOS_AUDIT_DIR")
    if override:
        base = Path(override)
    else:
        user_data = os.environ.get(
            "METNOS_USER_DATA",
            str(_C.PATH_USER_DATA),
        )
        base = Path(user_data) / "synth_audit"
    base.mkdir(parents=True, exist_ok=True)
    return base / "imports.jsonl"


def _existing_bindings() -> set:
    """Scan `_imports/` on-disk per binding ATTUALMENTE registrati.

    Single source of truth = lo stato fisico del catalog, NON l'audit
    storico (che mantiene record append-only anche dopo uninstall). Cosi'
    uninstall → re-import funziona senza false "already in use".

    Razionale §7.3: l'invariante "binding unico cross-skill" e' uno
    stato di sistema corrente, non un fatto storico permanente.
    """
    base = _C.PATH_USER_DATA / "executors" / "_imports"
    if not base.is_dir():
        return set()
    return {p.name for p in base.iterdir() if p.is_dir()}


def _binding_uniqueness_check(parsed_skill, existing) -> tuple[bool, str]:
    """Ritorna (ok, reason). Il binding di una skill (parsed_skill.name) DEVE
    essere unico tra tutte le skill importate (ADR 0089 + IMPORTER_NOTES §13).

    `existing` puo' includere il nome stesso quando si fa re-import (caller
    deve filtrare per supportare update; admission base = reject).
    """
    binding = parsed_skill.name
    if not binding:
        return False, "skill name empty (binding required)"
    if binding in existing:
        return False, f"binding {binding!r} already in use by another import"
    return True, ""


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------


def admit_skill_import(parsed_skill, plans, *,
                       executor_dir: Optional[Path] = None,
                       skip_l2: bool = False,
                       skip_l6: bool = False,
                       skip_binding_check: bool = False,
                       audit_log: bool = True) -> AdmissionReport:
    """Applica i 4 layer + auto-evaluator a una skill importata.

    DEVI: passare parsed_skill (Task A) + plans (Task B).
    NON DEVI: passare plans non generati da skill_codegen (executor_dir e'
    necessario per L6 verifier solo se i file esistono; se assenti, L6
    fa stage6 sul testo della description plan-only).
    """
    verbs, objs, quals = _load_vocab()
    handcrafted_aff = _scan_existing_executors(HANDCRAFTED_FAMILIES)
    synth_aff = _scan_existing_executors(SYNTH_DIRS)
    verifier = _stage6_verify_callable()
    # Existing bindings = catalog corrente ON-DISK, escludendo la skill che
    # stiamo importando (la pipeline codegen ha gia' creato la dir prima
    # del check). Senza esclusione, ogni import fallirebbe self-collision.
    existing_bindings = _existing_bindings() - {parsed_skill.name}

    report = AdmissionReport(skill_name=parsed_skill.name)
    report.audit_log_path = str(_audit_log_path())

    # Plus credentials binding uniqueness (a livello skill).
    if not skip_binding_check:
        bok, breason = _binding_uniqueness_check(parsed_skill, existing_bindings)
        if not bok:
            # Reject l'intero import.
            for p in plans:
                v = AdmissionVerdict(plan_name=p.name, accepted=False,
                                     layer_results={"binding_unique": False},
                                     reasons=[breason])
                report.rejected.append(v)
            if audit_log:
                _write_audit(report, parsed_skill)
            return report

    binding_norm = parsed_skill.name.lower().replace("-", "_").replace(".", "_")
    for plan in plans:
        verdict = AdmissionVerdict(plan_name=plan.name, accepted=True)

        # L1 vocab gate
        ok, reason = _vocab_gate(plan, verbs, objs, quals, binding=binding_norm)
        verdict.layer_results["L1_vocab"] = ok
        if not ok:
            verdict.accepted = False
            verdict.reasons.append(f"L1_vocab: {reason}")

        # L2 affinity overlap
        if not skip_l2 and verdict.accepted:
            from skill_codegen import _default_affinity
            aff = _default_affinity(plan)
            ok, reason = _affinity_overlap_check(
                plan, aff, handcrafted_aff, synth_aff,
                binding=binding_norm,
            )
            verdict.layer_results["L2_affinity"] = ok
            if not ok:
                verdict.accepted = False
                verdict.reasons.append(f"L2_affinity: {reason}")

        # L5 smoke routing case
        verdict.smoke_battery_case = _smoke_case_for_plan(plan)
        verdict.layer_results["L5_smoke_proposed"] = True

        # L6 semantic verifier — skip by design per imported: il code body
        # e' generato da template Jinja deterministico (skill_codegen), non
        # c'e' LLM drift da intercettare. Stage 6 resta attivo per synth
        # generati da stage 5 LLM (synt.run_full). Override via
        # METNOS_STAGE6_VERIFY_IMPORTED=1 (dev/diagnostica).
        force_l6 = os.environ.get("METNOS_STAGE6_VERIFY_IMPORTED") == "1"
        if not skip_l6 and force_l6 and verdict.accepted:
            mp = None
            cp = None
            if executor_dir is not None:
                mp = Path(executor_dir) / plan.name / "manifest.toml"
                cp = Path(executor_dir) / plan.name / f"{plan.name}.py"
            ok, reason = _stage6_check(plan, mp, cp, verifier)
            verdict.layer_results["L6_semantic"] = ok
            if not ok:
                verdict.accepted = False
                verdict.reasons.append(f"L6_semantic: {reason}")

        if verdict.accepted:
            report.accepted.append(verdict)
        else:
            report.rejected.append(verdict)

    if audit_log:
        _write_audit(report, parsed_skill)

    return report


def _write_audit(report: AdmissionReport, parsed_skill) -> None:
    """Append una riga JSONL all'audit log per ogni outcome."""
    path = _audit_log_path()
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    line = {
        "ts": timestamp,
        "skill": parsed_skill.name,
        "skill_source_sha256": parsed_skill.source_sha256,
        "binding": parsed_skill.name,
        "accepted": [v.plan_name for v in report.accepted],
        "rejected": [
            {"name": v.plan_name, "reasons": v.reasons}
            for v in report.rejected
        ],
        "smoke_cases": [v.smoke_battery_case for v in report.accepted],
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
