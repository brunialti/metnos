#!/usr/bin/env python3
"""synt.py — synth orchestrator (MVP compose + generate).

Implementa il microdesign synt.html v1.1:
- strategia REACT/COMPOSE (BFS sul mnestoma, max 5 hop)
- strategia REACT/GENERATE (POC: stadi 2+3 unificati in una chiamata LLM tier=wise,
  proposta scritta in workspace/.synt/proposals/<id>/ pronta per approvazione CLI)
- reward formula del cap. 6 con judge stub fisso 0.5
- audit JSONL append-only
- lock 24h post-abandoned, 30gg post-rejected (opzionale)
- homeostasis e revise: stub

Non in MVP (rimandato):
- introvertive (merge / generalize / specialize)
- revise
- judge LLM probabilistico
- profilo sandbox stretto (oggi: profilo conservativo derivato dagli import)

Storage:
- workspace/.audit/synt/YYYY-MM-DD.jsonl (audit)
- workspace/.synt/locks.json (lock semplice file-based)
- workspace/.synt/proposals/<id>/ (proposte generate pendenti)
- workspace/.synt/rejected/<id>/ (proposte rejected, per audit)
- mnestoma SQLite (consultato in lettura)
"""
from __future__ import annotations

import ast
import json
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal

sys.path.insert(0, str(Path(__file__).parent))
from mnestoma import Mnest, Mnestoma  # noqa: E402
import prompt_loader  # noqa: E402
from config import DEFAULT_LANG  # noqa: E402

# --- Costanti dal microdesign synt.html cap. 6 e cap. 8 ------------------

GATE_THRESHOLD = 0.65
COMPOSE_MAX_HOPS = 5
ABANDON_LOCK_DAYS = 1
REJECT_LOCK_DAYS = 30

STRATEGY_COST_BONUS = {
    "compose": 1.0,
    "merge": 0.6,
    "specialize": 0.6,
    "generalize": 0.4,
    "generate": 0.0,
}

# ADR 0148 rename-resilient: synt working dirs default under PATH_WORKSPACE.
import config as _C  # noqa: E402
_DEFAULT_WORKSPACE = _C.PATH_WORKSPACE
DEFAULT_AUDIT_DIR = Path(
    os.environ.get("SYNT_AUDIT_DIR", str(_DEFAULT_WORKSPACE / ".audit" / "synt"))
)
DEFAULT_LOCK_PATH = Path(
    os.environ.get("SYNT_LOCK_PATH", str(_DEFAULT_WORKSPACE / ".synt" / "locks.json"))
)
DEFAULT_PROPOSALS_DIR = Path(
    os.environ.get("SYNT_PROPOSALS_DIR", str(_DEFAULT_WORKSPACE / ".synt" / "proposals"))
)
DEFAULT_REJECTED_DIR = Path(
    os.environ.get("SYNT_REJECTED_DIR", str(_DEFAULT_WORKSPACE / ".synt" / "rejected"))
)

# Stdlib whitelist conservativa per validazione import (cap. 4 scaffolding):
# se la generate produce import fuori da questo set, il proposal va in
# birth_failed con motivazione "non-stdlib import requires explicit profile".
STDLIB_WHITELIST = frozenset({
    "json", "sys", "os", "re", "io", "string", "typing", "collections",
    "datetime", "math", "itertools", "functools", "pathlib", "hashlib",
    "base64", "uuid", "time", "dataclasses", "enum", "unicodedata",
    "decimal", "fractions", "statistics", "random", "csv", "html",
    "urllib", "http", "email", "mimetypes", "zlib", "gzip", "bz2", "lzma",
    "tarfile", "zipfile", "shutil", "tempfile", "glob", "fnmatch", "argparse",
    "secrets", "uuid", "operator", "copy", "abc", "contextlib",
})

Strategy = Literal["compose", "generate", "merge", "generalize", "specialize"]

# --- Generate: prompt + tool schema (stadi 2+3 unificati nel POC) ----------

# Prompt LLM estratti in runtime/prompts/it/{synt_generate,synt_birth_tests}.j2
# (ADR 0092 Phase 2.5, 5/5/2026). Caricati lazy via prompt_loader.


PROPOSE_BIRTH_TESTS_TOOL = [{
    "type": "function",
    "function": {
        "name": "propose_birth_tests",
        "description": "Proponi 3-5 birth-test declarativi per l'executor.",
        "parameters": {
            "type": "object",
            "properties": {
                "tests": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "input": {"type": "object"},
                            "expect": {
                                "type": "object",
                                "properties": {
                                    "ok": {"type": "boolean"},
                                    "content_contains": {"type": "string"},
                                    "error_contains": {"type": "string"},
                                    "metadata_field_eq": {"type": "object"},
                                },
                            },
                        },
                        "required": ["name", "input", "expect"],
                    },
                    "minItems": 3,
                    "maxItems": 5,
                },
            },
            "required": ["tests"],
        },
    },
}]


PROPOSE_EXECUTOR_TOOL = [{
    "type": "function",
    "function": {
        "name": "propose_executor",
        "description": "Proponi un nuovo executor per Metnos. Tutti i campi sono obbligatori.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "snake_case, verb_noun (es. count_words, parse_invoice)",
                },
                "description": {
                    "type": "string",
                    "description": "una frase in IT che descrive cosa fa l'executor",
                },
                "purpose": {
                    "type": "string",
                    "description": "scopo dell'executor in 1-2 frasi (specifica)",
                },
                "affinity": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "5-10 termini IT/EN per matching capability_hint",
                },
                "python_code": {
                    "type": "string",
                    "description": "contenuto integrale del file <name>.py",
                },
                "args_schema": {
                    "type": "object",
                    "description": "JSON Schema (object) per gli argomenti di invoke",
                    "properties": {
                        "type": {"type": "string"},
                        "properties": {"type": "object"},
                        "required": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["type", "properties"],
                },
                "output_summary": {
                    "type": "string",
                    "description": "1 riga: cosa contiene 'content' e 'metadata' in caso di successo",
                },
            },
            "required": ["name", "description", "purpose", "affinity",
                         "python_code", "args_schema", "output_summary"],
        },
    },
}]
ProposalState = Literal[
    "composing", "composed",
    "generating", "born", "abandoned",
    "proposed", "rejected",
    "fused", "generalized", "specialized",
]


# --- Tipi (cap. 9) ---------------------------------------------------------

@dataclass(frozen=True)
class SynthRequest:
    request_id: str
    mode: Literal["reactive", "introspective"]
    proto_mnest: str | None
    target_intent: str
    budget_cents: int
    capability_hint: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RewardBreakdown:
    det_pass_rate: float
    judge_score: float
    judge_reasoning: str
    cost_ratio: float
    similarity_penalty: float
    coverage_bonus: float
    strategy_cost_bonus: float
    total: float


@dataclass
class SynthProposal:
    request_id: str
    strategy: Strategy
    state: ProposalState
    artefact: dict
    reward: RewardBreakdown
    cost_cents: int = 0
    rationale: str = ""


@dataclass
class GeneratedProposal:
    """Esito strutturato dello stadio 2+3 di generate, salvato su disco."""
    proposal_id: str
    request_id: str
    name: str
    description: str
    purpose: str
    affinity: list[str]
    python_code: str
    args_schema: dict
    output_summary: str
    proposal_dir: Path
    llm_provider: str
    llm_model: str
    llm_in_tokens: int
    llm_out_tokens: int
    llm_latency_ms: int
    code_imports: list[str]
    non_stdlib_imports: list[str]
    convention_ok: bool
    convention_reason: str
    sandbox_profile: dict | None = None
    birth_test_results: dict | None = None


# Eccezioni usate internamente; nel POC vengono trasformate in stato (cap. 9)
class StrategyExhaustedError(Exception): ...
class BudgetExceededError(Exception): ...
class PolicyVetoError(Exception): ...
class ConstitutionViolationError(Exception): ...


# --- Helpers ---------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_request(
    target_intent: str,
    *,
    proto_mnest: str | None = None,
    budget_cents: int = 200,
    capability_hint: list[str] | None = None,
    mode: Literal["reactive", "introspective"] = "reactive",
) -> SynthRequest:
    return SynthRequest(
        request_id=uuid.uuid4().hex[:16],
        mode=mode,
        proto_mnest=proto_mnest,
        target_intent=target_intent,
        budget_cents=budget_cents,
        capability_hint=capability_hint or [],
    )


# Stop-word minimal per tokenizzazione di nomi snake_case (verb_noun convention)
_HINT_STOP_WORDS = frozenset({
    "a", "an", "the", "to", "of", "in", "for", "on", "and", "or",
    "is", "are", "be", "with", "from",
    # IT
    "il", "la", "lo", "i", "gli", "le", "un", "una", "di", "da", "per",
    "in", "su", "con", "e", "o",
})


def keywords_from_proto_name(name: str, *, min_len: int = 3) -> list[str]:
    """Tokenizza un nome snake_case in parole utili come capability_hint.

    Esempi:
        "archive_news"           -> ["archive_news", "archive", "news"]
        "save_invoice_to_calendar" -> ["save_invoice_to_calendar", "save", "invoice", "calendar"]
        "x"                       -> ["x"]  (non tokenizzabile, ritorna se stesso)

    Mantiene il nome originale come prima entry (massima specificita'), poi
    aggiunge i token di lunghezza >= min_len, filtrati da stop-words minime.
    """
    out = [name]
    if "_" not in name:
        return out
    for tok in name.split("_"):
        tok = tok.strip().lower()
        if not tok or len(tok) < min_len or tok in _HINT_STOP_WORDS:
            continue
        if tok not in out:
            out.append(tok)
    return out


def compute_reward(
    strategy: Strategy,
    *,
    det_pass_rate: float = 1.0,
    judge_score: float = 0.5,
    judge_reasoning: str = "(stub: judge LLM non attivato in MVP)",
    cost_ratio: float = 1.0,
    similarity_penalty: float = 0.0,
    coverage_bonus: float = 1.0,
) -> RewardBreakdown:
    """Formula del cap. 6 di synt.html, scalare in [0,1]."""
    bonus = STRATEGY_COST_BONUS.get(strategy, 0.0)
    total = (
        0.35 * det_pass_rate
        + 0.20 * judge_score
        + 0.15 * cost_ratio
        - 0.10 * similarity_penalty
        + 0.10 * coverage_bonus
        + 0.10 * bonus
    )
    total = max(0.0, min(1.0, total))
    return RewardBreakdown(
        det_pass_rate=det_pass_rate,
        judge_score=judge_score,
        judge_reasoning=judge_reasoning,
        cost_ratio=cost_ratio,
        similarity_penalty=similarity_penalty,
        coverage_bonus=coverage_bonus,
        strategy_cost_bonus=bonus,
        total=total,
    )


# --- Audit JSONL (cap. 10) -------------------------------------------------

class SyntAudit:
    def __init__(self, dir_path: str | Path | None = None):
        self.dir = Path(dir_path or DEFAULT_AUDIT_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path_for_today(self) -> Path:
        return self.dir / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"

    def log(self, entry: dict) -> None:
        if "ts" not in entry:
            entry["ts"] = _now_iso()
        with open(self._path_for_today(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def read_all(self, since_iso: str | None = None) -> list[dict]:
        out = []
        if not self.dir.exists():
            return out
        for p in sorted(self.dir.glob("*.jsonl")):
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                e = json.loads(line)
                if since_iso and e.get("ts", "") < since_iso:
                    continue
                out.append(e)
        return out


# --- Locks (cap. 8.3) ------------------------------------------------------

class SyntLocks:
    """Lock file-based: chiave -> ISO scadenza. JSON semplice."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or DEFAULT_LOCK_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}")

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text() or "{}")
        except json.JSONDecodeError:
            return {}

    def _save(self, d: dict) -> None:
        self.path.write_text(json.dumps(d, indent=2, sort_keys=True))

    def is_locked(self, key: str) -> bool:
        d = self._load()
        until = d.get(key)
        if not until:
            return False
        try:
            return datetime.now(timezone.utc) < datetime.fromisoformat(until)
        except ValueError:
            return False

    def lock(self, key: str, days: int) -> None:
        d = self._load()
        d[key] = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        self._save(d)

    def clear(self, key: str) -> None:
        d = self._load()
        d.pop(key, None)
        self._save(d)


# --- Compose (cap. 4.1) ----------------------------------------------------

class Composer:
    """Strategia COMPOSE: BFS sul mnestoma cercando una catena dal nodo
    src verso un dst che soddisfa un predicato di capability/nome."""

    def __init__(self, mnestoma: Mnestoma):
        self.mnestoma = mnestoma

    def find_chain(
        self,
        start_executor: str,
        target_pred: Callable[[str], bool],
        max_hops: int = COMPOSE_MAX_HOPS,
    ) -> list[Mnest] | None:
        """Ritorna il primo path di archi 'active' che termina su un dst per
        cui target_pred=True. Il default di walk filtra gli archi proto:
        compose si fa solo con executor che esistono davvero (cap. 4.1).
        I path sono ordinati per peso medio decrescente.
        """
        if max_hops < 1:
            return None
        paths = self.mnestoma.walk(
            start_executor, max_depth=max_hops, state_filter=("active",),
        )
        for path in paths:
            last_dst = path[-1].dst_executor
            if target_pred(last_dst):
                return path
        return None


# --- Synt principale -------------------------------------------------------

class Synt:
    def __init__(
        self,
        *,
        mnestoma: Mnestoma | None = None,
        audit: SyntAudit | None = None,
        locks: SyntLocks | None = None,
        gate_threshold: float = GATE_THRESHOLD,
        router=None,
        proposals_dir: Path | None = None,
    ):
        self.mnestoma = mnestoma or Mnestoma()
        self.audit = audit or SyntAudit()
        self.locks = locks or SyntLocks()
        self.composer = Composer(self.mnestoma)
        self.gate_threshold = gate_threshold
        self.router = router  # LLMRouter; se None, generate fallisce con stato esplicito
        self.proposals_dir = Path(proposals_dir or DEFAULT_PROPOSALS_DIR)

    # --- Cascata reattiva (cap. 4) ----------------------------------------

    def react(
        self,
        req: SynthRequest,
        *,
        target_pred: Callable[[str], bool] | None = None,
    ) -> SynthProposal:
        """Cascata reattiva MVP: tenta compose; se fallisce, abandoned (lock).

        target_pred: predicato sul nome dell'executor finale della catena.
        Se None, default = match con capability_hint (substring case-insensitive).
        """
        intent_key = req.target_intent or req.proto_mnest or req.request_id

        if self.locks.is_locked(f"abandon:{intent_key}"):
            return self._abandoned(req, "locked: ricorrenza < lock 24h")
        if self.locks.is_locked(f"reject:{intent_key}"):
            return self._abandoned(req, "locked: rejected internamente, lock 30gg")

        # Per il MVP serve il proto_mnest per sapere da dove partire.
        if not req.proto_mnest:
            self._log_terminal(req, "compose", "abandoned",
                               reason="no proto_mnest (MVP requires it)")
            return self._abandoned(req, "no proto_mnest (MVP requires it)")

        proto = self.mnestoma.get(req.proto_mnest)
        if proto is None:
            reason = f"proto-mnest {req.proto_mnest} not found"
            self._log_terminal(req, "compose", "abandoned", reason=reason)
            return self._abandoned(req, reason)

        src = proto.src_executor

        if target_pred is None:
            hints = req.capability_hint or [proto.dst_executor]
            target_pred = self._make_default_pred(hints, exclude=[src])

        chain = self.composer.find_chain(src, target_pred, max_hops=COMPOSE_MAX_HOPS)
        if chain is None:
            # Compose ha fallito: cascata reattiva -> generate.
            self._log_terminal(
                req, "compose", "abandoned",
                reason="no chain found, escalating to generate",
            )
            return self._generate(req, intent_key=intent_key)

        artefact = self._chain_artefact(chain)
        reward = compute_reward(
            "compose",
            det_pass_rate=1.0,        # tutti gli executor della catena sono firmati
            judge_score=0.5,          # stub
            cost_ratio=1.0,           # compose = 0 €
            similarity_penalty=0.0,   # nessun executor nuovo
            coverage_bonus=1.0,       # la catena copre il proto-mnest per costruzione
        )

        if reward.total < self.gate_threshold:
            self._log_terminal(req, "compose", "abandoned",
                               reward=asdict(reward),
                               reason=f"reward {reward.total:.3f} < gate {self.gate_threshold}")
            self.locks.lock(f"abandon:{intent_key}", ABANDON_LOCK_DAYS)
            return self._abandoned(
                req, f"reward {reward.total:.3f} below gate {self.gate_threshold}",
                reward=reward,
            )

        prop = SynthProposal(
            request_id=req.request_id,
            strategy="compose",
            state="composed",
            artefact=artefact,
            reward=reward,
            cost_cents=0,
            rationale=(
                f"composed chain of {len(chain)} hop(s); "
                f"avg_weight={artefact['avg_weight']:.2f}; src={src}; "
                f"final_dst={chain[-1].dst_executor}"
            ),
        )
        self._log_terminal(
            req, "compose", "composed",
            reward=asdict(reward),
            chain=artefact["chain"],
            cost_cents=0,
            rationale=prop.rationale,
        )
        return prop

    # --- Generate (stadi 2+3 unificati nel POC) ---------------------------

    def _generate(self, req: SynthRequest, *, intent_key: str) -> SynthProposal:
        """Cade qui quando compose fallisce. POC: stadi 2+3 in una sola chiamata
        LLM tier=wise via tool-use propose_executor. Salva il proposal su disco
        e ritorna SynthProposal con stato 'generating' (in attesa di stadi 4-7).

        Stadi 4 (profilo) e 5 (birth-test) sono svolti qui in forma minima:
            - profilo: valida che gli import siano in STDLIB_WHITELIST
            - birth-test smoke: il file Python deve fare AST parse + avere invoke+main
        Birth-test livello 2 (LLM-generated) e' task #5, non in questo metodo.
        """
        if self.router is None:
            reason = "generate: nessun LLMRouter configurato; tier wise non raggiungibile"
            self.locks.lock(f"abandon:{intent_key}", ABANDON_LOCK_DAYS)
            self._log_terminal(req, "generate", "abandoned", reason=reason)
            return self._abandoned(req, reason, strategy="generate")

        # Costruisce il prompt utente con il contesto del proto-mnest e dell'intent.
        proto_summary = self._proto_summary(req.proto_mnest)
        hints = ", ".join(req.capability_hint) if req.capability_hint else "(nessuno)"
        user_prompt = (
            f"Genera un executor con questa specifica:\n\n"
            f"target_intent: {req.target_intent!r}\n"
            f"proto_mnest_summary: {proto_summary}\n"
            f"capability_hint: [{hints}]\n\n"
            f"Decidi tu name (snake_case verb_noun), affinity, args_schema, "
            f"e codice Python conforme alla convenzione."
        )

        try:
            # max_tokens generoso: Gemma 4 spende ~1024 in reasoning + il resto
            # in tool-call (skeleton ~60-100 righe + schema). Tot ~6000 e' sicuro.
            res = self.router.chat_with_tools(
                prompt_loader.get("synt_generate", DEFAULT_LANG), user_prompt,
                tools=PROPOSE_EXECUTOR_TOOL,
                tier="wise",
                max_tokens=6000,
                for_code=True,
            )
        except Exception as e:
            reason = f"generate: errore LLM tier=wise: {e}"
            self.locks.lock(f"abandon:{intent_key}", ABANDON_LOCK_DAYS)
            self._log_terminal(req, "generate", "abandoned", reason=reason)
            return self._abandoned(req, reason, strategy="generate")

        if not res.tool_calls:
            reason = "generate: LLM non ha chiamato propose_executor"
            self.locks.lock(f"abandon:{intent_key}", ABANDON_LOCK_DAYS)
            self._log_terminal(req, "generate", "abandoned",
                               reason=reason, llm_text=res.text[:300])
            return self._abandoned(req, reason, strategy="generate")

        tc = res.tool_calls[0]
        if tc.name != "propose_executor":
            reason = f"generate: tool_call inatteso: {tc.name!r}"
            self.locks.lock(f"abandon:{intent_key}", ABANDON_LOCK_DAYS)
            self._log_terminal(req, "generate", "abandoned", reason=reason)
            return self._abandoned(req, reason, strategy="generate")

        proposal_args = dict(tc.arguments)
        # Recuperi soft per campi che gli LLM dimenticano di compilare anche
        # quando sono required nello schema. Sono tutti recuperabili da altri
        # campi del proposal stesso.
        if not proposal_args.get("description") and proposal_args.get("purpose"):
            # description = prima frase di purpose
            purpose = str(proposal_args["purpose"]).strip()
            first = purpose.split(".")[0].strip() or purpose[:120]
            proposal_args["description"] = first
        if not proposal_args.get("output_summary"):
            proposal_args["output_summary"] = "(non specificato dal generatore)"
        # Affinity vuota e' sospetta ma non bloccante
        if not proposal_args.get("affinity"):
            proposal_args["affinity"] = list(req.capability_hint)

        # Validazioni minimali del payload (campi non recuperabili)
        missing = [k for k in ("name", "purpose", "python_code", "args_schema")
                   if not proposal_args.get(k)]
        if missing:
            reason = f"generate: campi non recuperabili mancanti: {missing}"
            self._log_terminal(req, "generate", "abandoned", reason=reason)
            return self._abandoned(req, reason, strategy="generate")

        # Convention check via AST + import whitelist
        ok, conv_reason, imports = self._validate_executor_code(
            proposal_args["python_code"],
        )
        non_stdlib = [i for i in imports if i not in STDLIB_WHITELIST]

        # Debug: se la convention check fallisce, salva comunque il raw output
        # in proposals_dir/<id>_failed/ per ispezione.
        if not ok:
            fail_id = uuid.uuid4().hex[:16]
            fail_dir = self.proposals_dir / f"_failed_{fail_id}"
            fail_dir.mkdir(parents=True, exist_ok=True)
            (fail_dir / "raw_code.py").write_text(
                proposal_args["python_code"], encoding="utf-8",
            )
            (fail_dir / "tool_args.json").write_text(
                json.dumps(proposal_args, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            (fail_dir / "fail_reason.txt").write_text(
                f"convention_reason: {conv_reason}\n"
                f"target_intent: {req.target_intent}\n"
                f"capability_hint: {req.capability_hint}\n"
                f"llm: {res.provider}:{res.model}\n",
                encoding="utf-8",
            )

        proposal_id = uuid.uuid4().hex[:16]
        proposal_dir = self.proposals_dir / proposal_id
        gp = GeneratedProposal(
            proposal_id=proposal_id,
            request_id=req.request_id,
            name=proposal_args["name"],
            description=proposal_args["description"],
            purpose=proposal_args["purpose"],
            affinity=list(proposal_args["affinity"]),
            python_code=proposal_args["python_code"],
            args_schema=dict(proposal_args["args_schema"]),
            output_summary=proposal_args["output_summary"],
            proposal_dir=proposal_dir,
            llm_provider=getattr(res, "provider", "?"),
            llm_model=getattr(res, "model", "?"),
            llm_in_tokens=res.in_tokens,
            llm_out_tokens=res.out_tokens,
            llm_latency_ms=res.latency_ms,
            code_imports=imports,
            non_stdlib_imports=non_stdlib,
            convention_ok=ok,
            convention_reason=conv_reason,
        )

        if not ok:
            self._log_terminal(req, "generate", "abandoned",
                               reason=f"convention check failed: {conv_reason}",
                               llm_provider=gp.llm_provider, llm_model=gp.llm_model)
            return self._abandoned(req, f"generate: {conv_reason}", strategy="generate")

        if non_stdlib:
            self._log_terminal(req, "generate", "abandoned",
                               reason=f"non-stdlib imports require explicit profile: {non_stdlib}",
                               llm_provider=gp.llm_provider, llm_model=gp.llm_model)
            return self._abandoned(
                req, f"generate: non-stdlib imports {non_stdlib}",
                strategy="generate",
            )

        # Stadio 4: profilo sandbox conservativo derivato dagli import + scan AST.
        sandbox = derive_sandbox_profile(gp.python_code, imports)
        if sandbox["dangerous"]:
            self._log_terminal(req, "generate", "abandoned",
                               reason=f"sandbox dangerous: {sandbox['reasons']}",
                               llm_provider=gp.llm_provider, llm_model=gp.llm_model,
                               sandbox=sandbox)
            return self._abandoned(
                req, f"generate: sandbox dangerous ({sandbox['reasons']})",
                strategy="generate",
            )
        gp.sandbox_profile = sandbox

        # Persiste il proposal su disco prima dei birth-test (la persistenza
        # serve anche se il birth-test fallisce: l'audit deve poter riferirsi
        # al codice esatto rifiutato).
        self._write_proposal_to_disk(req, gp)

        # Stadio 5: birth-test livello 2 — LLM genera tests, runner li esegue.
        bt = self._run_birth_tests(req, gp)
        gp.birth_test_results = bt
        # Riscrive il proposal.json con i risultati
        self._write_proposal_to_disk(req, gp)
        if not bt.get("all_passed", False):
            failed_summary = bt.get("summary", "birth tests failed")
            self._log_terminal(
                req, "generate", "abandoned",
                reason=f"birth tests failed: {failed_summary}",
                proposal_id=gp.proposal_id,
                proposal_dir=str(gp.proposal_dir),
                executor_name=gp.name,
                birth_test_results=bt,
                llm_provider=gp.llm_provider, llm_model=gp.llm_model,
            )
            return self._abandoned(
                req, f"generate: birth tests failed ({failed_summary})",
                strategy="generate",
            )

        reward = compute_reward(
            "generate",
            det_pass_rate=1.0,        # AST + convention OK; birth-test reali in task #5
            judge_score=0.5,
            cost_ratio=1.0,           # cost_tracker non ancora collegato qui
            similarity_penalty=0.0,
            coverage_bonus=1.0,
        )

        prop = SynthProposal(
            request_id=req.request_id,
            strategy="generate",
            state="generating",
            artefact={
                "proposal_id": proposal_id,
                "proposal_dir": str(proposal_dir),
                "name": gp.name,
                "description": gp.description,
                "purpose": gp.purpose,
                "affinity": gp.affinity,
                "code_lines": len(gp.python_code.splitlines()),
                "imports": imports,
            },
            reward=reward,
            cost_cents=0,
            rationale=(
                f"generated executor '{gp.name}' via {gp.llm_provider}:{gp.llm_model} "
                f"in {gp.llm_latency_ms}ms ({gp.llm_in_tokens}->{gp.llm_out_tokens} tokens). "
                f"Awaiting human approval (synt approve {proposal_id})."
            ),
        )
        self._log_terminal(
            req, "generate", "generating",
            reward=asdict(reward),
            proposal_id=proposal_id,
            proposal_dir=str(proposal_dir),
            executor_name=gp.name,
            llm_provider=gp.llm_provider, llm_model=gp.llm_model,
            llm_in_tokens=gp.llm_in_tokens, llm_out_tokens=gp.llm_out_tokens,
            llm_latency_ms=gp.llm_latency_ms,
            rationale=prop.rationale,
        )
        return prop

    def _run_birth_tests(self, req: SynthRequest,
                         gp: GeneratedProposal) -> dict:
        """Stadio 5: chiama LLM tier=wise per generare 3-5 test, li esegue
        contro il file Python del proposal e valuta gli expect.

        Ritorna dict con:
            tests:      lista di {name, input, expect, run, passed, reason}
            all_passed: True se tutti passano e almeno N>=3
            summary:    stringa breve
            generation_latency_ms / generation_tokens
        """
        out: dict = {
            "tests": [],
            "all_passed": False,
            "summary": "(not run)",
        }
        if self.router is None:
            out["summary"] = "no router configured for birth-test generation"
            return out

        # Chiede all'LLM di generare i test, dato codice + spec sintetica
        user = (
            f"L'executor '{gp.name}' ha questa specifica:\n"
            f"description: {gp.description}\n"
            f"purpose: {gp.purpose}\n"
            f"output_summary: {gp.output_summary}\n\n"
            f"Codice Python:\n```python\n{gp.python_code}\n```\n\n"
            f"Scrivi 3-5 birth-test conformi al protocollo."
        )
        try:
            # ~1024 thinking + ~3000 per 3-5 test JSON dichiarativi
            res = self.router.chat_with_tools(
                prompt_loader.get("synt_birth_tests", DEFAULT_LANG), user,
                tools=PROPOSE_BIRTH_TESTS_TOOL,
                tier="wise",
                max_tokens=4500,
                for_code=False,  # qui produce solo dichiarazioni di test, non codice
            )
        except Exception as e:
            out["summary"] = f"birth-test generation LLM error: {e}"
            return out

        out["generation_latency_ms"] = res.latency_ms
        out["generation_in_tokens"]  = res.in_tokens
        out["generation_out_tokens"] = res.out_tokens
        out["generation_provider"]   = getattr(res, "provider", "?")
        out["generation_model"]      = getattr(res, "model", "?")

        if not res.tool_calls or res.tool_calls[0].name != "propose_birth_tests":
            out["summary"] = "LLM non ha chiamato propose_birth_tests"
            return out

        tests = res.tool_calls[0].arguments.get("tests") or []
        if len(tests) < 3:
            out["summary"] = f"only {len(tests)} tests generated, minimum 3 required"
            out["tests"] = tests
            return out

        # Esegue ogni test via subprocess sul file Python del proposal
        code_path = gp.proposal_dir / f"{gp.name}.py"
        results = []
        passed_count = 0
        for t in tests:
            r = self._exec_birth_test(code_path, t)
            results.append(r)
            if r["passed"]:
                passed_count += 1
        out["tests"] = results
        out["passed_count"] = passed_count
        out["total_count"] = len(results)
        out["all_passed"] = (passed_count == len(results) and len(results) >= 3)
        if out["all_passed"]:
            out["summary"] = f"{passed_count}/{len(results)} passed"
        else:
            failed = [r["name"] for r in results if not r["passed"]]
            out["summary"] = (
                f"{passed_count}/{len(results)} passed; failed: {failed}"
            )
        return out

    @staticmethod
    def _exec_birth_test(code_path: Path, t: dict) -> dict:
        """Esegue un singolo birth-test e valuta gli expect."""
        import subprocess
        name = t.get("name", "(unnamed)")
        inp = t.get("input") or {}
        expect = t.get("expect") or {}
        result = {
            "name": name,
            "input": inp,
            "expect": expect,
            "passed": False,
            "reason": "",
            "stdout": "",
            "stderr": "",
            "returncode": None,
        }
        try:
            proc = subprocess.run(
                ["python3", str(code_path)],
                input=json.dumps(inp), capture_output=True, text=True,
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            result["reason"] = "timeout"
            return result
        result["stdout"] = proc.stdout[:2000]
        result["stderr"] = proc.stderr[:1000]
        result["returncode"] = proc.returncode
        if proc.returncode != 0:
            result["reason"] = f"non-zero return: {proc.returncode}"
            return result
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            result["reason"] = f"stdout not JSON: {e}"
            return result

        # Valuta expect clauses (devono passare TUTTE quelle dichiarate)
        if "ok" in expect and out.get("ok") is not expect["ok"]:
            result["reason"] = f"expected ok={expect['ok']}, got ok={out.get('ok')}"
            return result
        if "content_contains" in expect:
            content = out.get("content", "")
            if not isinstance(content, str) or expect["content_contains"] not in content:
                result["reason"] = (
                    f"expected content_contains={expect['content_contains']!r}, "
                    f"got content={str(content)[:120]!r}"
                )
                return result
        if "error_contains" in expect:
            err = out.get("error", "")
            if not isinstance(err, str) or expect["error_contains"] not in err:
                result["reason"] = (
                    f"expected error_contains={expect['error_contains']!r}, "
                    f"got error={str(err)[:120]!r}"
                )
                return result
        if "metadata_field_eq" in expect:
            md = out.get("metadata") or {}
            for k, v in expect["metadata_field_eq"].items():
                if md.get(k) != v:
                    result["reason"] = (
                        f"expected metadata.{k}={v!r}, got {md.get(k)!r}"
                    )
                    return result
        result["passed"] = True
        result["reason"] = "ok"
        return result

    @staticmethod
    def _validate_executor_code(code: str) -> tuple[bool, str, list[str]]:
        """AST parse + presenza di invoke + main. Ritorna (ok, reason, imports)."""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, f"AST parse failed: {e}", []
        fns = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        if "invoke" not in fns:
            return False, "convention: function 'invoke' missing", []
        if "main" not in fns:
            return False, "convention: function 'main' missing", []
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split(".")[0])
        return True, "ok", sorted(imports)


# derive_sandbox_profile e' definito DOPO la classe Synt (vedi in fondo al file)

    def _proto_summary(self, proto_mnest_id: str | None) -> str:
        if not proto_mnest_id:
            return "(no proto-mnest provided)"
        proto = self.mnestoma.get(proto_mnest_id)
        if proto is None:
            return f"(proto {proto_mnest_id} not found)"
        sig = (proto.desired_sig or {}).get("summary") if hasattr(proto, "desired_sig") else None
        return (
            f"src={proto.src_executor} -> dst={proto.dst_executor} "
            f"(uses={proto.uses}, w={proto.weight:.2f})"
            + (f"; sig={sig}" if sig else "")
        )

    def _write_proposal_to_disk(self, req: SynthRequest, gp: GeneratedProposal) -> None:
        gp.proposal_dir.mkdir(parents=True, exist_ok=True)
        # 1) il file Python dell'executor candidato
        code_path = gp.proposal_dir / f"{gp.name}.py"
        code_path.write_text(gp.python_code, encoding="utf-8")
        # 2) manifest TOML draft (digest sara' calcolato in fase di firma)
        manifest = self._render_manifest_toml(gp)
        (gp.proposal_dir / "manifest.toml").write_text(manifest, encoding="utf-8")
        # 3) schema args separato (utile per UX e test runner)
        (gp.proposal_dir / "args_schema.json").write_text(
            json.dumps(gp.args_schema, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        # 4) profilo di sandbox (separato per leggibilita')
        if gp.sandbox_profile is not None:
            (gp.proposal_dir / "sandbox_profile.json").write_text(
                json.dumps(gp.sandbox_profile, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        # 5) metadati del proposal (origin request, LLM info, validazioni)
        meta = {
            "proposal_id": gp.proposal_id,
            "request_id": gp.request_id,
            "created_at": _now_iso(),
            "name": gp.name,
            "description": gp.description,
            "purpose": gp.purpose,
            "affinity": gp.affinity,
            "output_summary": gp.output_summary,
            "target_intent": req.target_intent,
            "proto_mnest": req.proto_mnest,
            "capability_hint": list(req.capability_hint),
            "llm": {
                "provider": gp.llm_provider,
                "model": gp.llm_model,
                "in_tokens": gp.llm_in_tokens,
                "out_tokens": gp.llm_out_tokens,
                "latency_ms": gp.llm_latency_ms,
            },
            "validation": {
                "imports": gp.code_imports,
                "non_stdlib_imports": gp.non_stdlib_imports,
                "convention_ok": gp.convention_ok,
                "convention_reason": gp.convention_reason,
            },
            "sandbox_profile": gp.sandbox_profile,
            "birth_test_results": gp.birth_test_results,
            "stage": "generated",
            "next_stages_required": [
                "stage_5_birth_tests",
                "stage_6_human_approval",
                "stage_7_sign_install",
            ],
        }
        (gp.proposal_dir / "proposal.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8",
        )

    @staticmethod
    def _render_manifest_toml(gp: GeneratedProposal) -> str:
        """Rende un draft di manifest.toml conforme alla convenzione executor.

        Il digest sara' calcolato e sostituito dalla pipeline di firma (stadio 7).

        ADR 0092 Phase 4 (5/5/2026): description e args.properties.<arg>.description
        sono table multilingua `[description] <lang> = "..."` invece di scalari.
        """
        import os as _os
        cur_lang = _os.environ.get("METNOS_LANG", "it")
        affinity_arr = ", ".join(json.dumps(a, ensure_ascii=False) for a in gp.affinity)
        # estrai required dal schema, default []
        required = gp.args_schema.get("required") or []
        required_arr = ", ".join(json.dumps(r) for r in required)
        # le properties le includiamo come blocco args.* gia' atteso dal manifest
        props_blocks = []
        for prop_name, prop in (gp.args_schema.get("properties") or {}).items():
            ptype = prop.get("type", "string")
            pdesc = prop.get("description", "")
            pdef  = prop.get("default")
            block = [f"\n[args.properties.{prop_name}]",
                     f'type        = "{ptype}"']
            if pdef is not None:
                block.append(f"default     = {json.dumps(pdef)}")
            block.append("")
            block.append(f"[args.properties.{prop_name}.description]")
            block.append(f'{cur_lang} = {json.dumps(pdesc, ensure_ascii=False)}')
            props_blocks.append("\n".join(block))

        return (
            f"# Manifest dell'executor '{gp.name}' — Metnos v1.1 (proposal {gp.proposal_id})\n"
            f"# Generato da synt; awaiting human approval.\n\n"
            f'manifest_format = "1.0"\n\n'
            f'name        = "{gp.name}"\n'
            f'version     = "0.1.0"\n'
            f'author      = "synt+human <synt@metnos.local>"\n'
            f"affinity    = [{affinity_arr}]\n\n"
            f"[description]\n"
            f'{cur_lang} = {json.dumps(gp.description, ensure_ascii=False)}\n\n'
            f"[code]\n"
            f'files  = ["{gp.name}.py"]\n'
            f'digest = "sha256:PENDING_SIGN"  # populated by sign_executor()\n\n'
            f"[args]\n"
            f'type     = "{gp.args_schema.get("type","object")}"\n'
            f"required = [{required_arr}]\n"
            + "\n".join(props_blocks) + "\n"
        )

    # --- Introvertive: specialize (3/5/2026) ------------------------------

    def specialize(
        self,
        *,
        parent_name: str,
        arg_name: str,
        dominant_value,
        proposed_name: str | None = None,
    ) -> SynthProposal:
        """Crea un executor specializzato che cabla `arg_name=dominant_value`
        come default del parent. Wrapper diretto: l'arg specializzato viene
        rimosso dallo schema args dell'esposizione, il codice del wrapper
        re-itera args ricevuti, inserisce il valore cablato, chiama
        l'`invoke()` del parent.

        Sicurezza: la variante eredita 1:1 capabilities del parent (sandbox,
        firma, hint). NON aggiunge superficie d'attacco. Reversibile:
        `executor_aging` la archivia se non usata per 44+ giorni.

        Output: SynthProposal con state='specialized' se installazione OK,
        'rejected' altrimenti.
        """
        import re
        import shutil as _sh
        import subprocess as _sp
        import tomllib
        from pathlib import Path as _P

        rid = f"specialize_{parent_name}_{arg_name}_{int(time.time())}"
        target_name = proposed_name or f"{parent_name}_{arg_name}_specialized"
        # Sanitize target_name (no spaces, no special chars)
        target_name = re.sub(r"[^A-Za-z0-9_]+", "_", target_name).strip("_")

        # Locate parent manifest
        candidates = [
            _C.PATH_EXECUTORS / parent_name / "manifest.toml",
            _P.home() / f".local/share/metnos/executors/{parent_name}/manifest.toml",
        ]
        parent_manifest_path = None
        for p in candidates:
            if p.exists():
                parent_manifest_path = p
                break
        if parent_manifest_path is None:
            return SynthProposal(
                request_id=rid, strategy=Strategy("introspective", "specialize"),
                state="rejected", artefact={}, reward=RewardBreakdown(0, 0, 0, 0, 0),
                rationale=f"parent manifest non trovato: {parent_name}",
            )

        try:
            with open(parent_manifest_path, "rb") as f:
                pm = tomllib.load(f)
        except Exception as e:
            return SynthProposal(
                request_id=rid, strategy=Strategy("introspective", "specialize"),
                state="rejected", artefact={}, reward=RewardBreakdown(0, 0, 0, 0, 0),
                rationale=f"parse error parent manifest: {e}",
            )

        # Build child manifest: inherit description + affinity + capabilities,
        # remove `arg_name` from required + properties.
        parent_args = pm.get("args", {}) or {}
        parent_props = (parent_args.get("properties") or {}).copy()
        if arg_name in parent_props:
            del parent_props[arg_name]
        parent_required = [a for a in (parent_args.get("required") or []) if a != arg_name]

        # Wrapper code
        py_code = _wrapper_code(parent_name, arg_name, dominant_value)

        # Target dir under SYNTHESIZED_EXECUTORS_DIR
        from loader import SYNTHESIZED_EXECUTORS_DIR
        target_dir = SYNTHESIZED_EXECUTORS_DIR / target_name
        if target_dir.exists():
            return SynthProposal(
                request_id=rid, strategy=Strategy("introspective", "specialize"),
                state="rejected", artefact={"reason": "target_already_exists"},
                reward=RewardBreakdown(0, 0, 0, 0, 0),
                rationale=f"target dir already exists: {target_dir}",
            )
        target_dir.mkdir(parents=True, exist_ok=True)

        # Write code
        code_path = target_dir / f"{target_name}.py"
        code_path.write_text(py_code, encoding="utf-8")

        # Write manifest
        toml_text = _build_specialize_manifest(
            target_name=target_name,
            parent_name=parent_name,
            parent_manifest=pm,
            arg_name=arg_name,
            dominant_value=dominant_value,
            args_properties=parent_props,
            args_required=parent_required,
        )
        manifest_path = target_dir / "manifest.toml"
        manifest_path.write_text(toml_text, encoding="utf-8")

        # Sign with synt key
        try:
            sign_proc = _sp.run(
                ["python3", str(_C.PATH_RUNTIME / "sign.py"), "sign",
                 str(target_dir), "synt"],
                capture_output=True, text=True, timeout=10,
            )
            if sign_proc.returncode != 0:
                _sh.rmtree(target_dir, ignore_errors=True)
                return SynthProposal(
                    request_id=rid, strategy=Strategy("introspective", "specialize"),
                    state="rejected",
                    artefact={"sign_stderr": sign_proc.stderr[:300]},
                    reward=RewardBreakdown(0, 0, 0, 0, 0),
                    rationale=f"sign failed: {sign_proc.stderr[:200]}",
                )
        except Exception as e:
            _sh.rmtree(target_dir, ignore_errors=True)
            return SynthProposal(
                request_id=rid, strategy=Strategy("introspective", "specialize"),
                state="rejected", artefact={},
                reward=RewardBreakdown(0, 0, 0, 0, 0),
                rationale=f"sign exception: {e}",
            )

        # Verify
        try:
            from sign import verify_executor
            ok, info = verify_executor(target_dir)
            if not ok:
                _sh.rmtree(target_dir, ignore_errors=True)
                return SynthProposal(
                    request_id=rid, strategy=Strategy("introspective", "specialize"),
                    state="rejected", artefact={"verify_info": str(info)[:300]},
                    reward=RewardBreakdown(0, 0, 0, 0, 0),
                    rationale="verify failed after sign",
                )
        except Exception:
            pass

        # Register in executor_aging with the proper source tag for the
        # history timeline (admin dashboard).
        try:
            from executor_aging import register as _exec_register
            _exec_register(
                target_name,
                source="synth:introvertive_specialize",
                detail={
                    "parent_name": parent_name,
                    "arg_name": arg_name,
                    "dominant_value": dominant_value,
                },
            )
        except Exception:
            pass

        return SynthProposal(
            request_id=rid,
            strategy=Strategy("introspective", "specialize"),
            state="specialized",
            artefact={
                "target_name": target_name,
                "parent_name": parent_name,
                "arg_name": arg_name,
                "dominant_value": dominant_value,
                "manifest_path": str(manifest_path),
                "code_path": str(code_path),
            },
            reward=RewardBreakdown(1.0, 0.0, 0.0, 0.0, 1.0),
            rationale=f"specialize {parent_name} → {target_name} (cabled {arg_name}={dominant_value!r})",
        )

    # --- Introvertive (rule-based v1.1) -----------------------------------

    def homeostasis(
        self,
        *,
        catalog: list | None = None,
        merge_min_jaccard: float = 0.70,
        generalize_min_cluster: int = 3,
        lookback_days: int = 30,
    ) -> list[SynthProposal]:
        """Pass introvertive: scorre il pool e propone fusioni/generalizzazioni.

        Heuristiche rule-based v1.1, niente LLM:
            - **merge**: coppie di executor con stesso target_kind + capability
              identica + Jaccard delle affinity tag >= merge_min_jaccard.
            - **generalize**: cluster di >= generalize_min_cluster executor
              con stesso prefix verb (es. fs_*, llm_*, pkg_*) + stesso
              target_kind.
            - **specialize**: rinviato a v1.2 (richiede analisi pattern di mnest
              su lookback_days).

        `catalog`: lista di Executor caricati (es. da `loader.load_catalog()`).
        Se None, carica il catalogo dal filesystem.

        Ritorna list di SynthProposal con state='proposed' (non auto-applica).
        Logga ciascuna proposta in audit.
        """
        if catalog is None:
            try:
                from loader import load_catalog
                catalog = list(load_catalog().values())
            except Exception:
                return []

        proposals: list[SynthProposal] = []
        proposals.extend(self._propose_merge(catalog, min_jaccard=merge_min_jaccard))
        proposals.extend(self._propose_generalize(catalog, min_cluster=generalize_min_cluster))
        return proposals

    def _executor_attrs(self, ex) -> dict:
        """Estrae attributi confrontabili da un Executor (duck-typed)."""
        return {
            "name": getattr(ex, "name", "?"),
            "version": getattr(ex, "version", "?"),
            "target_kind": getattr(ex, "target_kind", None),
            "capabilities": tuple(sorted(getattr(ex, "capabilities", []) or [])),
            "affinity": frozenset((getattr(ex, "affinity", None) or [])),
        }

    @staticmethod
    def _jaccard(a: frozenset, b: frozenset) -> float:
        if not a and not b:
            return 0.0
        u = a | b
        return len(a & b) / len(u) if u else 0.0

    def _propose_merge(self, catalog: list, *, min_jaccard: float) -> list[SynthProposal]:
        """Coppie con stesso target_kind + capability + Jaccard affinity alta."""
        attrs = [self._executor_attrs(e) for e in catalog]
        out: list[SynthProposal] = []
        seen: set[tuple[str, str]] = set()
        for i, a in enumerate(attrs):
            for b in attrs[i + 1:]:
                if a["target_kind"] != b["target_kind"]:
                    continue
                if a["capabilities"] != b["capabilities"]:
                    continue
                if not a["affinity"] or not b["affinity"]:
                    continue
                key = tuple(sorted((a["name"], b["name"])))
                if key in seen:
                    continue
                jac = self._jaccard(a["affinity"], b["affinity"])
                if jac < min_jaccard:
                    continue
                seen.add(key)
                shared = sorted(a["affinity"] & b["affinity"])
                rationale = (
                    f"merge candidato: {a['name']} + {b['name']} "
                    f"(target_kind={a['target_kind']}, capability comuni, "
                    f"jaccard affinity={jac:.2f}, condivisi={shared})"
                )
                reward = compute_reward(
                    "merge", det_pass_rate=1.0, coverage_bonus=jac,
                    similarity_penalty=0.0,
                )
                req = make_request(
                    target_intent=f"merge:{a['name']}+{b['name']}",
                    mode="introspective",
                )
                prop = SynthProposal(
                    request_id=req.request_id,
                    strategy="merge",
                    state="proposed",
                    artefact={
                        "candidates": [a["name"], b["name"]],
                        "shared_affinity": shared,
                        "jaccard": jac,
                        "target_kind": a["target_kind"],
                    },
                    reward=reward,
                    cost_cents=0,
                    rationale=rationale,
                )
                out.append(prop)
                self._log_terminal(req, "merge", "proposed",
                                   reward=asdict(reward),
                                   candidates=[a["name"], b["name"]],
                                   jaccard=jac, rationale=rationale)
        return out

    def _propose_generalize(self, catalog: list, *, min_cluster: int) -> list[SynthProposal]:
        """Cluster con stesso prefix verb (es. fs_*, llm_*) + target_kind."""
        attrs = [self._executor_attrs(e) for e in catalog]
        # Raggruppa per (prefix, target_kind)
        from collections import defaultdict
        clusters: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for a in attrs:
            name = a["name"]
            if "_" not in name:
                continue
            prefix = name.split("_", 1)[0]
            if len(prefix) < 2:
                continue
            clusters[(prefix, a["target_kind"] or "")].append(a)

        out: list[SynthProposal] = []
        for (prefix, tk), members in clusters.items():
            if len(members) < min_cluster:
                continue
            names = sorted(m["name"] for m in members)
            shared = frozenset.intersection(*(m["affinity"] for m in members)) if all(m["affinity"] for m in members) else frozenset()
            rationale = (
                f"generalize candidato: cluster '{prefix}_*' "
                f"({len(members)} executor: {names}, target_kind={tk}, "
                f"affinity comuni={sorted(shared)})"
            )
            reward = compute_reward(
                "generalize", det_pass_rate=1.0,
                coverage_bonus=min(1.0, len(members) / 5.0),
            )
            req = make_request(
                target_intent=f"generalize:{prefix}_*",
                mode="introspective",
            )
            prop = SynthProposal(
                request_id=req.request_id,
                strategy="generalize",
                state="proposed",
                artefact={
                    "prefix": prefix,
                    "members": names,
                    "shared_affinity": sorted(shared),
                    "target_kind": tk,
                    "size": len(members),
                },
                reward=reward,
                cost_cents=0,
                rationale=rationale,
            )
            out.append(prop)
            self._log_terminal(req, "generalize", "proposed",
                               reward=asdict(reward),
                               prefix=prefix, members=names,
                               size=len(members), rationale=rationale)
        return out

    def revise(
        self,
        request_id: str,
        feedback: str,
        target_strategy: Strategy | None = None,
    ) -> SynthProposal:
        raise NotImplementedError("revise: rimandato a v1.2 (richiede generate + UX)")

    # --- Helpers privati ---------------------------------------------------

    def _make_default_pred(
        self, hints: list[str], *, exclude: list[str] | None = None,
    ) -> Callable[[str], bool]:
        excl = set(exclude or [])
        lhints = [h.lower() for h in hints if h]

        def pred(name: str) -> bool:
            if name in excl:
                return False
            if not lhints:
                return False
            ln = name.lower()
            return any(h in ln or ln in h for h in lhints)

        return pred

    def _chain_artefact(self, chain: list[Mnest]) -> dict:
        return {
            "chain": [chain[0].src_executor] + [m.dst_executor for m in chain],
            "chain_mnest_ids": [m.id for m in chain],
            "avg_weight": sum(m.weight for m in chain) / len(chain),
            "hops": len(chain),
        }

    def _abandoned(
        self, req: SynthRequest, reason: str,
        *, reward: RewardBreakdown | None = None,
        strategy: Strategy = "compose",
    ) -> SynthProposal:
        if reward is None:
            reward = compute_reward(
                strategy, det_pass_rate=0.0, coverage_bonus=0.0,
            )
        return SynthProposal(
            request_id=req.request_id,
            strategy=strategy,
            state="abandoned",
            artefact={},
            reward=reward,
            cost_cents=0,
            rationale=reason,
        )

    def _log_terminal(
        self, req: SynthRequest, strategy: Strategy, state: ProposalState,
        **extra,
    ) -> None:
        entry = {
            "ts": _now_iso(),
            "request_id": req.request_id,
            "mode": req.mode,
            "proto_mnest": req.proto_mnest,
            "target_intent": req.target_intent,
            "strategy": strategy,
            "state": state,
        }
        entry.update(extra)
        self.audit.log(entry)

    # --- Stadi 6+7: approval CLI-driven, sign, install ---------------------

    def list_proposals(self) -> list[dict]:
        """Elenca tutte le proposte pendenti in proposals_dir."""
        out = []
        if not self.proposals_dir.exists():
            return out
        for d in sorted(self.proposals_dir.iterdir()):
            if not d.is_dir():
                continue
            mp = d / "proposal.json"
            if not mp.exists():
                continue
            try:
                meta = json.loads(mp.read_text())
            except Exception:
                continue
            bt = meta.get("birth_test_results") or {}
            out.append({
                "proposal_id": meta.get("proposal_id"),
                "name": meta.get("name"),
                "description": meta.get("description"),
                "created_at": meta.get("created_at"),
                "birth_passed": bt.get("all_passed"),
                "birth_summary": bt.get("summary"),
                "proposal_dir": str(d),
            })
        return out

    def approve_proposal(self, proposal_id: str, *,
                         executors_dir: Path | str = str(_C.PATH_EXECUTORS),
                         key_name: str = "author") -> dict:
        """Stadi 6 (approval = chiamata stessa) + 7 (firma e install).

        Verifica: birth_tests passati. Sposta in executors_dir/<name>/, calcola
        digest, firma il manifest. Ritorna dict di risultato.
        """
        executors_dir = Path(executors_dir)
        prop_dir = self.proposals_dir / proposal_id
        if not prop_dir.exists():
            return {"ok": False, "error": f"proposal {proposal_id} non trovato"}
        meta_path = prop_dir / "proposal.json"
        meta = json.loads(meta_path.read_text())
        bt = meta.get("birth_test_results") or {}
        if not bt.get("all_passed"):
            return {
                "ok": False,
                "error": f"birth tests non passati: {bt.get('summary','?')}",
            }

        name = meta["name"]
        target_dir = executors_dir / name
        if target_dir.exists():
            return {
                "ok": False,
                "error": f"executor '{name}' gia' esistente in {target_dir}",
            }

        # Importa qui per evitare dipendenza al modulo se non si firma mai
        sys.path.insert(0, str(Path(__file__).parent))
        from sign import sign_executor  # noqa: WPS433

        target_dir.mkdir(parents=True)
        # Copia code + manifest + schema (non sandbox_profile e proposal.json,
        # che restano nel proposal originale per audit)
        import shutil
        for fname in (f"{name}.py", "manifest.toml", "args_schema.json"):
            src = prop_dir / fname
            if src.exists():
                shutil.copy2(src, target_dir / fname)
        # Firma
        digest, sig_path = sign_executor(target_dir, key_name=key_name)

        # Sposta il proposal in archived/<id>
        archived_dir = self.proposals_dir.parent / "approved" / proposal_id
        archived_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(prop_dir), str(archived_dir))

        # Audit
        self.audit.log({
            "ts": _now_iso(),
            "event": "approve",
            "proposal_id": proposal_id,
            "executor_name": name,
            "executor_dir": str(target_dir),
            "digest": digest,
            "sig_path": str(sig_path),
            "archived_proposal_dir": str(archived_dir),
            "request_id": meta.get("request_id"),
        })
        return {
            "ok": True,
            "executor_name": name,
            "executor_dir": str(target_dir),
            "digest": digest,
            "sig_path": str(sig_path),
            "archived_proposal_dir": str(archived_dir),
        }

    def reject_proposal(self, proposal_id: str, *, reason: str = "") -> dict:
        """Reject un proposal: lock 30gg + sposta in rejected/."""
        prop_dir = self.proposals_dir / proposal_id
        if not prop_dir.exists():
            return {"ok": False, "error": f"proposal {proposal_id} non trovato"}
        meta = json.loads((prop_dir / "proposal.json").read_text())
        target_intent = meta.get("target_intent") or proposal_id
        # Lock 30 giorni sul target_intent
        self.locks.lock(f"reject:{target_intent}", REJECT_LOCK_DAYS)

        rejected_dir = self.proposals_dir.parent / "rejected" / proposal_id
        rejected_dir.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.move(str(prop_dir), str(rejected_dir))

        self.audit.log({
            "ts": _now_iso(),
            "event": "reject",
            "proposal_id": proposal_id,
            "executor_name": meta.get("name"),
            "rejected_dir": str(rejected_dir),
            "lock_days": REJECT_LOCK_DAYS,
            "lock_key": f"reject:{target_intent}",
            "human_reason": reason,
            "request_id": meta.get("request_id"),
        })
        return {
            "ok": True,
            "rejected_dir": str(rejected_dir),
            "lock_key": f"reject:{target_intent}",
            "lock_days": REJECT_LOCK_DAYS,
        }


# --- Stadio 4: profilo sandbox conservativo (modulo level) -----------------

_NET_MODULES = frozenset({"urllib", "http", "socket", "ssl", "ftplib",
                          "smtplib", "imaplib", "poplib", "telnetlib",
                          "asyncio", "selectors"})
_SUBPROCESS_MODULES = frozenset({"subprocess", "multiprocessing"})
_FS_WRITE_MODULES = frozenset({"shutil", "tempfile", "tarfile", "zipfile",
                               "gzip", "bz2", "lzma"})
_DANGEROUS_NAMES = frozenset({"system", "popen", "spawn", "exec", "eval",
                              "compile", "__import__"})


def _wrapper_code(parent_name: str, arg_name: str, dominant_value) -> str:
    """Genera il codice Python del wrapper specialize.

    Il wrapper:
    - importa l'invoke() del parent dal pool;
    - intercetta args, inserisce arg_name=dominant_value se non presente;
    - chiama parent.invoke(args, ctx) e ritorna il risultato senza modifiche.
    """
    import json as _json
    val_repr = _json.dumps(dominant_value)
    return f'''"""Wrapper specialize generato dal Synt il {time.strftime('%Y-%m-%d')}.
Cabla `{arg_name}={val_repr}` come default del parent `{parent_name}`.
Eredita 1:1 capabilities, sandbox, firma del parent (executor_aging gestisce
il decay automatico in caso di inutilizzo: 30g→deprecated, 14g→archived).
"""
from __future__ import annotations

import importlib.util
import json as _json
import sys
from pathlib import Path

_PARENT_NAME = "{parent_name}"
_CABLED_ARG = "{arg_name}"
_CABLED_VAL = {val_repr}


def _load_parent_invoke():
    import os as _os
    _user_data = Path(_os.environ.get("METNOS_USER_DATA",
                                       str(Path.home() / ".local/share/metnos")))
    candidates = [
        Path(f"{_C.PATH_EXECUTORS}/{{_PARENT_NAME}}/{{_PARENT_NAME}}.py"),
        _user_data / f"executors/{{_PARENT_NAME}}/{{_PARENT_NAME}}.py",
    ]
    for p in candidates:
        if p.exists():
            spec = importlib.util.spec_from_file_location(f"_parent_{{_PARENT_NAME}}", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "invoke"):
                return mod.invoke
    raise RuntimeError(f"Parent executor '{{_PARENT_NAME}}' not found in pool")


def invoke(args: dict, ctx: dict | None = None) -> dict:
    """Wrapper: cabla {arg_name}={val_repr} se non gia' passato."""
    if not isinstance(args, dict):
        args = {{}}
    args = dict(args)  # don't mutate caller's args
    if _CABLED_ARG not in args:
        args[_CABLED_ARG] = _CABLED_VAL
    parent_invoke = _load_parent_invoke()
    return parent_invoke(args, ctx)


if __name__ == "__main__":  # pragma: no cover
    raw = sys.stdin.read() or "{{}}"
    args = _json.loads(raw)
    print(_json.dumps(invoke(args), default=str, ensure_ascii=False))
'''


def _build_specialize_manifest(
    *,
    target_name: str,
    parent_name: str,
    parent_manifest: dict,
    arg_name: str,
    dominant_value,
    args_properties: dict,
    args_required: list,
) -> str:
    """Costruisce il TOML del manifest della variante specializzata."""
    import json as _json

    parent_desc = (parent_manifest.get("description") or "").strip()
    parent_aff = parent_manifest.get("affinity") or []

    desc = (
        f"Variante specializzata di `{parent_name}` con "
        f"`{arg_name}={_json.dumps(dominant_value)}` cablato di default. "
        f"Eredita 1:1 le capabilities del parent. "
        f"Auto-creata dal Synt.specialize il {time.strftime('%Y-%m-%d')} "
        f"sopra soglia (dom>=0.90, uses>=30). Reversibile via decay "
        f"(`executor_aging`)."
    )
    if parent_desc:
        desc += f"\\n\\nParent: {parent_desc[:200]}"

    aff = list(parent_aff) + [f"specialized:{parent_name}", f"cable:{arg_name}",
                               "auto-created", "synt"]

    # Args block: inherit type, required (without arg_name), properties
    # (without arg_name).
    args_type = (parent_manifest.get("args") or {}).get("type", "object")

    # Render properties
    props_lines = []
    for pname, pdef in args_properties.items():
        if not isinstance(pdef, dict):
            continue
        props_lines.append(f"\\n[args.properties.{pname}]")
        for k, v in pdef.items():
            if isinstance(v, str):
                v_repr = _json.dumps(v)
            elif isinstance(v, bool):
                v_repr = "true" if v else "false"
            elif isinstance(v, (int, float)):
                v_repr = str(v)
            elif isinstance(v, list):
                v_repr = _json.dumps(v)
            elif isinstance(v, dict):
                v_repr = _json.dumps(v)
            else:
                continue
            props_lines.append(f"{k} = {v_repr}")

    required_arr = ", ".join(_json.dumps(r) for r in args_required)
    aff_arr = ", ".join(_json.dumps(a) for a in aff)

    parent_caps = parent_manifest.get("capabilities") or []
    caps_blocks = []
    if isinstance(parent_caps, list):
        for c in parent_caps:
            if not isinstance(c, dict):
                continue
            caps_blocks.append("\\n[[capabilities]]")
            for k, v in c.items():
                if isinstance(v, str):
                    caps_blocks.append(f'{k} = {_json.dumps(v)}')
                elif isinstance(v, list):
                    caps_blocks.append(f'{k} = {_json.dumps(v)}')

    revertible = parent_manifest.get("revertible", False)
    rev_pat    = parent_manifest.get("reverse_pattern") or []

    return (
        f"# Manifest auto-generato dal Synt.specialize ({time.strftime('%Y-%m-%d')}).\\n"
        f"# Variante specializzata di `{parent_name}` con\\n"
        f"# `{arg_name}={_json.dumps(dominant_value)}` cablato di default.\\n\\n"
        f'manifest_format = "1.0"\\n\\n'
        f'name        = "{target_name}"\\n'
        f'version     = "0.1.0"\\n'
        f'author      = "synt-multistage <synt@metnos.com>"\\n'
        f'description = {_json.dumps(desc)}\\n'
        f'affinity    = [{aff_arr}]\\n'
        f'revertible  = {"true" if revertible else "false"}\\n'
        f'reverse_pattern = {_json.dumps(rev_pat)}\\n'
        f'lifecycle   = "active"\\n\\n'
        f"[code]\\n"
        f'files  = ["{target_name}.py"]\\n'
        f'digest = "sha256:0000000000000000000000000000000000000000000000000000000000000000"\\n\\n'
        f"[args]\\n"
        f'type     = "{args_type}"\\n'
        f'required = [{required_arr}]'
        + "".join(props_lines)
        + "\\n"
        + "".join(caps_blocks)
        + "\\n"
    ).replace("\\n", "\n")


def derive_sandbox_profile(code: str, imports: list[str]) -> dict:
    """Calcola il profilo di sandbox piu' stretto compatibile col codice.

    Profilo = dict con flag boolean piu' reasons. Se dangerous=True,
    il proposal e' rigettato in generate (stadio 4).
    """
    reasons = []
    profile = {
        "net": False,
        "subprocess": False,
        "write_files": False,
        "read_files": False,
        "dangerous": False,
        "reasons": reasons,
        "imports": list(imports),
    }
    iset = set(imports)
    if iset & _NET_MODULES:
        profile["net"] = True
        reasons.append(f"imports network modules: {sorted(iset & _NET_MODULES)}")
    if iset & _SUBPROCESS_MODULES:
        profile["subprocess"] = True
        reasons.append(f"imports subprocess module: {sorted(iset & _SUBPROCESS_MODULES)}")
    if iset & _FS_WRITE_MODULES:
        profile["write_files"] = True
        reasons.append(f"imports fs-write modules: {sorted(iset & _FS_WRITE_MODULES)}")

    try:
        tree = ast.parse(code)
    except SyntaxError:
        profile["dangerous"] = True
        reasons.append("AST parse failed; cannot certify sandbox")
        return profile

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn_name = ""
            if isinstance(node.func, ast.Name):
                fn_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                fn_name = node.func.attr
            if fn_name in _DANGEROUS_NAMES:
                profile["dangerous"] = True
                reasons.append(f"dangerous call: {fn_name}()")
            if fn_name == "open" and node.args:
                for arg in node.args[1:2]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        if any(m in arg.value for m in ("w", "a", "x")):
                            profile["write_files"] = True
                            reasons.append(f"open() in write mode: {arg.value!r}")
                profile["read_files"] = True

    if not any([profile["net"], profile["subprocess"], profile["write_files"],
                profile["read_files"], profile["dangerous"]]):
        reasons.append("pure computation: no I/O detected")

    return profile


# --- CLI ------------------------------------------------------------------

def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="Synt: cascata reattiva compose+generate, approval CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_react = sub.add_parser("react", help="Cascata reattiva (compose -> generate)")
    p_react.add_argument("--proto-mnest", required=True,
                         help="ID del proto-mnest da risolvere")
    p_react.add_argument("--intent", default="",
                         help="target_intent NL (usato anche dal generate per il prompt)")
    p_react.add_argument("--hint", action="append", default=[],
                         help="capability hint (substring, ripetibile)")
    p_react.add_argument("--budget-cents", type=int, default=200)
    p_react.add_argument("--no-generate", action="store_true",
                         help="se compose fallisce, non scendere a generate (solo abandoned)")

    sub.add_parser("audit", help="Stampa l'audit JSONL")

    p_pro = sub.add_parser("protos", help="Lista proto-mnest ricorrenti dal mnestoma")
    p_pro.add_argument("--min-uses", type=int, default=1)
    p_pro.add_argument("--min-weight", type=float, default=0.0)

    p_sug = sub.add_parser(
        "suggest",
        help="Per ogni proto-mnest ricorrente nel mnestoma, tenta react() compose-only",
    )
    p_sug.add_argument("--min-uses", type=int, default=1)
    p_sug.add_argument("--min-weight", type=float, default=0.0)
    p_sug.add_argument("--limit", type=int, default=10)

    p_intro = sub.add_parser("introvert", help="Pass introvertive: merge + generalize sul pool")
    p_intro.add_argument("--merge-jaccard", type=float, default=0.70,
                         help="soglia Jaccard affinity per proporre merge")
    p_intro.add_argument("--cluster-min", type=int, default=3,
                         help="cluster minimo per proporre generalize")
    sub.add_parser("proposals", help="Lista le proposte pendenti")
    p_app = sub.add_parser("approve", help="Approva una proposta (firma + install)")
    p_app.add_argument("proposal_id")
    p_app.add_argument("--executors-dir", default=str(_C.PATH_EXECUTORS))
    p_app.add_argument("--key-name", default="author")
    p_rej = sub.add_parser("reject", help="Rigetta una proposta (lock 30gg)")
    p_rej.add_argument("proposal_id")
    p_rej.add_argument("--reason", default="")

    args = ap.parse_args()

    # Per react/proposals/approve/reject serve il router (il generate puo'
    # fallire senza, ma compose+approve funzionano comunque).
    router = None
    if args.cmd == "react" and not args.no_generate:
        try:
            from llm_router import LLMRouter
            router = LLMRouter()
        except Exception as e:
            print(f"[warn] LLMRouter non disponibile: {e}; il generate fallira'.")

    s = Synt(router=router)

    if args.cmd == "react":
        req = make_request(
            args.intent, proto_mnest=args.proto_mnest,
            budget_cents=args.budget_cents, capability_hint=args.hint,
        )
        prop = s.react(req)
        print(json.dumps(asdict(prop), indent=2, ensure_ascii=False, default=str))
    elif args.cmd == "audit":
        for entry in s.audit.read_all():
            print(json.dumps(entry, ensure_ascii=False))
    elif args.cmd == "protos":
        for p in s.mnestoma.recurring_protos(
            min_uses=args.min_uses, min_weight=args.min_weight,
        ):
            sig = (p.desired_sig or {}).get("summary", "")
            print(f"  {p.id}  {p.src_executor}->{p.dst_executor}  "
                  f"w={p.weight:.3f}  uses={p.uses}  sig={sig}")
    elif args.cmd == "suggest":
        protos = s.mnestoma.recurring_protos(
            min_uses=args.min_uses, min_weight=args.min_weight,
        )[: args.limit]
        if not protos:
            print("(nessun proto-mnest ricorrente nel mnestoma)")
            return
        for p in protos:
            req = make_request(
                f"satisfy proto {p.dst_executor} from {p.src_executor}",
                proto_mnest=p.id,
                capability_hint=[p.dst_executor],
            )
            prop = s.react(req)
            chain = prop.artefact.get("chain") if prop.artefact else None
            chain_str = " -> ".join(chain) if chain else "(none)"
            print(f"\n--- proto {p.id} ({p.src_executor}->{p.dst_executor}, "
                  f"uses={p.uses}, w={p.weight:.2f}) ---")
            print(f"  state:    {prop.state}")
            print(f"  chain:    {chain_str}")
            print(f"  R total:  {prop.reward.total:.3f}")
            print(f"  rationale: {prop.rationale}")
    elif args.cmd == "introvert":
        proposals = s.homeostasis(
            merge_min_jaccard=args.merge_jaccard,
            generalize_min_cluster=args.cluster_min,
        )
        if not proposals:
            print("(nessuna proposta introvertive)")
            return
        print(f"=== {len(proposals)} proposte introvertive ===\n")
        for p in proposals:
            print(f"  [{p.strategy}] R={p.reward.total:.3f}")
            print(f"    {p.rationale}")
            print(f"    artefact: {json.dumps(p.artefact, ensure_ascii=False)}\n")
    elif args.cmd == "proposals":
        props = s.list_proposals()
        if not props:
            print("(nessuna proposta pendente)")
            return
        print(f"=== {len(props)} proposte pendenti in {s.proposals_dir} ===\n")
        for p in props:
            mark = "OK" if p["birth_passed"] else "??"
            print(f"  [{mark}] {p['proposal_id']}  {p['name']}")
            print(f"        {p['description']}")
            print(f"        birth: {p['birth_summary']}")
            print(f"        created: {p['created_at']}")
            print(f"        dir: {p['proposal_dir']}\n")
    elif args.cmd == "approve":
        res = s.approve_proposal(
            args.proposal_id,
            executors_dir=args.executors_dir,
            key_name=args.key_name,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
        if not res.get("ok"):
            sys.exit(1)
    elif args.cmd == "reject":
        res = s.reject_proposal(args.proposal_id, reason=args.reason)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        if not res.get("ok"):
            sys.exit(1)


if __name__ == "__main__":
    _cli()
