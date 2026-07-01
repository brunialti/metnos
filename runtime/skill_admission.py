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
  Idempotente. Da wirare nel scheduler v2 daily@04:30 (vedi the design guide §10.6.43).
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
    """Carica vocab.py canonico (R3, 24/5/2026): single source of truth.

    DEVI: importare da `vocab` direttamente, niente fallback locale.
    NON DEVI: replicare ACTIONS/OBJECTS/QUALIFIERS qui (drift garantito).
    OK: vocab.ACTIONS modificato → admission gate riflette al boot.
    ERRORE: fallback nascondeva drift (es. set 'reply' in fallback ma
    non in vocab → admission accettava plan che vocab avrebbe rifiutato).

    Ritorna tuple `(verbs: frozenset, objs: frozenset, quals: frozenset)`.
    """
    # ADR 0148: ensure runtime/ on sys.path per import diretto.
    runtime_dir = Path(__file__).resolve().parent
    if runtime_dir.exists() and str(runtime_dir) not in sys.path:
        sys.path.insert(0, str(runtime_dir))
    import vocab  # noqa: E402
    return (
        frozenset(vocab.ACTIONS),
        frozenset(vocab.OBJECTS),
        frozenset(vocab.QUALIFIERS),
    )


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
# G6 fix (24/5/2026, §7.3): _HANDCRAFTED_DIRS / _SYNTH_DIRS sono PATH STRINGS
# (consumati da `_scan_existing_executors`), distinti dal `loader.HANDCRAFTED_FAMILIES`
# che e' frozenset di NOMI executor. Stessa parola, semantiche diverse →
# pattern errore. Naming separato + helper `_is_handcrafted(name)` simmetrico
# a `loader._is_synth`/`_is_imported` evita confusione.
_HANDCRAFTED_DIRS: tuple[str, ...] = (str(_C.PATH_EXECUTORS),)
_SYNTH_DIRS: tuple[str, ...] = (str(_C.PATH_SYNTH_EXECUTORS),)

# Back-compat alias (deprecabile gradualmente): mantengo per ora i nomi
# pubblici legacy, ma marcati come deprecati nei consumer test (zero usage
# fuori da skill_admission, vedi `grep -rn HANDCRAFTED_FAMILIES`).
HANDCRAFTED_FAMILIES = _HANDCRAFTED_DIRS
SYNTH_DIRS = _SYNTH_DIRS


def _is_handcrafted(name: str) -> bool:
    """True se `<name>` esiste come dir handcrafted in _HANDCRAFTED_DIRS.

    Helper simmetrico a `loader._is_synth`/`loader._is_imported` (path-based).
    NON confondere con `loader.HANDCRAFTED_FAMILIES` (frozenset di nomi
    discovery primari curati).
    """
    for root in _HANDCRAFTED_DIRS:
        if (Path(root) / name / "manifest.toml").is_file():
            return True
    return False


def _is_synth(name: str) -> bool:
    """True se `<name>` esiste come dir synth in _SYNTH_DIRS (escluso `_imports/`)."""
    for root in _SYNTH_DIRS:
        candidate = Path(root) / name / "manifest.toml"
        if candidate.is_file():
            return True
    return False


def _is_imported(name: str) -> bool:
    """True se `<name>` esiste sotto `skills/<skill>/<name>/manifest.toml`
    (ADR 0160) o legacy `_imports/<skill>/<name>/manifest.toml` (ADR 0123)."""
    from skills_paths import skill_roots as _sr
    for base in _sr():
        for skill_dir in base.iterdir():
            if (skill_dir / name / "manifest.toml").is_file():
                return True
    return False


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


def _affinity_overlap_check(plan, plan_affinity, scan_handcrafted, scan_synth,
                             threshold: Optional[float] = None,
                             binding: str = "") -> tuple[bool, str]:
    """Ritorna (ok, reason). ok=False = reject (jaccard >= threshold).

    Delega a `loader.check_affinity_pair` (single source of truth §7.3):
    questa funzione applica solo la policy at-import (binding-aware
    threshold choice), il calcolo Jaccard e' centralizzato in loader.

    Soglia default 0.5 (= `loader.AFFINITY_OVERLAP_THRESHOLD`) al catalog
    load (ADR 0114 L2 / ADR 0159). 0.4 stretta come gate preventivo a
    evaluator-time (ADR 0122 — non applicata qui).

    Se il plan ha il suffix `_<binding>` (disambiguato in translate_skill
    per evitare collisione con handcrafted/synth gia' esistenti col nome
    canonico), la soglia sale a `AFFINITY_OVERLAP_THRESHOLD_BINDING` (0.85):
    il binding qualifica esplicitamente il dominio remoto, le keyword
    sovrapposte sono attese e legittime, non un doppione mascherato.
    """
    from loader import (
        check_affinity_pair,
        AFFINITY_OVERLAP_THRESHOLD,
        AFFINITY_OVERLAP_THRESHOLD_BINDING,
    )
    plan_set = set(t.lower() for t in plan_affinity if t)
    if not plan_set:
        return True, ""

    if threshold is None:
        threshold = AFFINITY_OVERLAP_THRESHOLD
    eff_threshold = threshold
    if binding and plan.name.endswith(f"_{binding}"):
        eff_threshold = AFFINITY_OVERLAP_THRESHOLD_BINDING

    for name, aff in scan_handcrafted.items():
        if name == plan.name:
            return False, f"name collision with handcrafted {name}"
        triggered, j = check_affinity_pair(plan_set, aff, threshold=eff_threshold)
        if triggered:
            return False, f"affinity jaccard={j:.2f} >= {eff_threshold} vs handcrafted {name}"

    for name, aff in scan_synth.items():
        if name == plan.name:
            return False, f"name collision with existing synth {name}"
        triggered, j = check_affinity_pair(plan_set, aff, threshold=eff_threshold)
        if triggered:
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
                                  "expected_first_tool": "get_files"},
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
    """Scan `skills/` + legacy `_imports/` on-disk per binding ATTUALMENTE
    registrati (ADR 0160 rename, ADR 0123 originario).

    Single source of truth = lo stato fisico del catalog, NON l'audit
    storico (che mantiene record append-only anche dopo uninstall). Cosi'
    uninstall → re-import funziona senza false "already in use".

    Razionale §7.3: l'invariante "binding unico cross-skill" e' uno
    stato di sistema corrente, non un fatto storico permanente.
    """
    from skills_paths import existing_skill_names as _esn
    return _esn()


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


def _run_smoke_for_plan(plan, case: dict) -> tuple[bool, str]:
    """Esegue il routing assertion di smoke per UN plan accepted (ADR 0159 L5).

    Strict: se `expected_first_tool` del prefilter != atteso, reject.
    Skip gracefully con ok=True se il case e' `_no_smoke=True` (pattern
    non in mappa) o se prefilter/catalog non importabili.

    Determinismo §7.9: usa `smoke._run_smoke_with_tool_assertion`
    (intent BoW + prefilter, no LLM).

    Ritorna (ok, reason). ok=True implica smoke passato o skip ammesso.
    """
    if not case or case.get("_no_smoke"):
        return True, "no smoke case mapped (skip)"
    expected = case.get("expected_first_tool")
    if not expected:
        return True, "case has no expected_first_tool (skip)"
    try:
        from smoke import _run_smoke_with_tool_assertion
    except Exception as ex:
        return True, f"smoke runner not importable (skip): {ex}"
    try:
        result = _run_smoke_with_tool_assertion(case, catalog=None)
    except Exception as ex:
        return False, f"smoke runner raised: {ex}"
    if result.get("skip"):
        return True, f"smoke skipped: {result.get('reason', '?')}"
    if result.get("ok"):
        return True, "smoke pass"
    return False, (
        f"smoke fail: expected {result.get('expected')!r} "
        f"got {result.get('actual_first')!r}"
    )


def admit_skill_import(parsed_skill, plans, *,
                       executor_dir: Optional[Path] = None,
                       skip_l2: bool = False,
                       skip_l5_exec: bool = False,
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
    handcrafted_aff = _scan_existing_executors(_HANDCRAFTED_DIRS)
    synth_aff = _scan_existing_executors(_SYNTH_DIRS)
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

        # L5 smoke EXEC (ADR 0159): esegue il routing assertion al-import
        # e rejecta se fail. Default ON. Bypass via `skip_l5_exec` (dev
        # / unit test) o env `METNOS_SMOKE_AT_IMPORT=0` (legacy).
        legacy_smoke_off = os.environ.get("METNOS_SMOKE_AT_IMPORT") == "0"
        if not skip_l5_exec and not legacy_smoke_off and verdict.accepted:
            s_ok, s_reason = _run_smoke_for_plan(plan, verdict.smoke_battery_case)
            verdict.layer_results["L5_smoke_exec"] = s_ok
            if not s_ok:
                verdict.accepted = False
                verdict.reasons.append(f"L5_smoke_exec: {s_reason}")

        # L6 semantic verifier — default ON per imported (ADR 0159):
        # confronta description (manifest) ↔ code body via il modello locale.
        # Reject su `aligned=false`. Bypass via flag esplicito `--skip-l6`
        # (escape hatch dev/CI). Disable globale via env
        # `METNOS_SYNT_STAGE6_DISABLED=1` (test veloce). L'override
        # storico `METNOS_STAGE6_VERIFY_IMPORTED=0` resta come kill-switch
        # legacy per chi vuole il comportamento pre-0159.
        legacy_off = os.environ.get("METNOS_STAGE6_VERIFY_IMPORTED") == "0"
        global_off = os.environ.get("METNOS_SYNT_STAGE6_DISABLED") == "1"
        if not skip_l6 and not legacy_off and not global_off and verdict.accepted:
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
