"""github_analyze_callback — Fase F+G del workflow watcher GitHub.

Tre callback handler invocati da `runtime/orchestration.py`:

  - `_extract_snooze_seconds(label)` deterministico §7.9
  - `run_github_analysis(issue_ref, ...)` Stage 1c (consult_frontier)
  - `format_stage2_card(issue_ref, analysis)` rendering markdown approvazione

Naming compositivo §2.2: `analyze` non e' tra i 23 verbi del vocab; vive in
`runtime/jobs/` (callback handler, non executor). Il callback handler emette
un secondo dialog get_inputs `[Posta|Modifica|Scarta]` con on_complete
`github_send_reply`.

Determinismo §7.9 ovunque possibile (snooze parser, ADR subset, card
rendering). L'unico LLM nella catena e' `consult_frontier` chiamato in
`run_github_analysis`.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)


# ---------- snooze parser ------------------------------------------------

_SNOOZE_RE = re.compile(r"snooze\s+(\d+)\s*(h|m|s|d)\b", re.IGNORECASE)
_UNIT_TO_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _extract_snooze_seconds(label: str) -> int | None:
    """Parse "snooze 1h" / "snooze 4h" / "snooze 24h" / "snooze 30m" / "snooze 2d".
    Ritorna seconds (int) o None se label non matcha.
    Determinismo §7.9, case-insensitive (§2.4 dominio aperto).
    """
    if not isinstance(label, str):
        return None
    m = _SNOOZE_RE.search(label)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    mult = _UNIT_TO_SECONDS.get(unit, 0)
    if mult <= 0:
        return None
    return n * mult


# ---------- DNA Metnos inline --------------------------------------------

def _dna_inline() -> dict[str, str]:
    """Stringhe brevi che riassumono i pilastri Metnos §2.2 + architecture.

    Inietta in `local_context.inline` di consult_frontier cosi' il frontier
    capisce il dominio prima di guardare il codice. Hardcoded (deterministico
    §7.9): le linee guida sono nel the design guide ma servono brevi qui."""
    vocab_section = (
        "Vocabolario chiuso (§2.2): 23 azioni "
        "(read,write,move,delete,create,find,list,filter,sort,group,classify,"
        "get,set,send,describe,render,extract,compress,compute,compare,change,"
        "order,share) x 19 oggetti "
        "(files,dirs,packages,messages,events,contacts,places,processes,"
        "persons,tasks,urls,numbers,images,signatures,texts,proposals,inputs,"
        "credentials,entries). Naming compositivo `azione_oggetto[_qualifier]`."
    )
    architecture_layers = (
        "Architettura: planner ReAct (LLM medium Gemma 4 26B locale + Sonnet/"
        "GPT-5 frontier fallback) -> prefilter -> vaglio -> executor con "
        "manifest TOML. Sintetizzati al volo via synt multistage 5-stadi. "
        "Channels: Telegram + HTTP porta 8770. Pipeline immagini in-process "
        "SigLIP+ArcFace+EXIF. Storage: ~/.local/share/metnos/. "
        "Scheduler v2 asyncio co-host (ADR 0112)."
    )
    return {
        "vocab_section": vocab_section,
        "architecture_layers": architecture_layers,
    }


# ---------- ADR subset derivation ----------------------------------------

# ADR 0148 rename-resilient: derive from this module's location
# (<PATH_ROOT>/runtime/jobs/file.py → parents[2] = PATH_ROOT).
_ADR_DIR = Path(__file__).resolve().parents[2] / "decisions"
_TOKEN_RE = re.compile(r"[a-z]{4,}", re.IGNORECASE)
_STOPWORDS = {
    "with", "from", "this", "that", "have", "been", "what", "when",
    "which", "where", "their", "would", "could", "should", "about",
    "into", "issue", "problem", "github", "fail", "fails", "fixed",
    "error", "errore", "test", "tests", "code", "file", "files", "want",
    "need", "make", "made", "after", "before", "every", "some", "more",
}


def _tokenize(title: str) -> set[str]:
    if not isinstance(title, str):
        return set()
    return {
        t.lower() for t in _TOKEN_RE.findall(title.lower())
        if t.lower() not in _STOPWORDS
    }


def _derive_adr_subset(issue_title: str, max_n: int = 3) -> list[str]:
    """Tokenize title -> fuzzy match vs ADR filenames. Top N ADR by overlap.

    Deterministico §7.9: niente LLM. Match su substring del filename
    (after the 4-digit prefix + `-`). Risultato: lista di absolute paths.
    """
    tokens = _tokenize(issue_title)
    if not tokens:
        return []
    if not _ADR_DIR.exists():
        return []
    scored: list[tuple[int, str]] = []
    for p in _ADR_DIR.glob("*.md"):
        name = p.stem.lower()
        # Strip "NNNN-" prefix if present (vocab match dovrebbe scartare i
        # numeri ma essere espliciti non guasta).
        if len(name) > 5 and name[:4].isdigit() and name[4] == "-":
            slug = name[5:]
        else:
            slug = name
        slug_tokens = set(slug.replace("-", " ").split())
        overlap = len(tokens & slug_tokens)
        if overlap > 0:
            scored.append((overlap, str(p)))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [p for _, p in scored[: max(0, int(max_n))]]


# ---------- analysis pipeline (Stage 1c) ---------------------------------

def run_github_analysis(issue_ref: dict, *, actor: str = "host",
                          channel: str | None = None,
                          sender_id: str | None = None) -> dict[str, Any]:
    """Stage 1c — analisi tecnica di una issue/PR via consult_frontier.

    Args:
      issue_ref: {repo, kind, number, title}
      actor/channel/sender_id: propagation routine.

    Returns:
      dict {
        ok: bool,
        analysis: dict | None,           # output strutturato consult_frontier
        draft_reply: str,
        classification: str,
        confidence: float,
        related_issues: list,
        cost_usd: float,
        error?: str,
        error_class?: str,
      }
    """
    if not isinstance(issue_ref, dict):
        return {"ok": False, "error": "issue_ref non dict",
                "error_class": "invalid_args"}
    repo = issue_ref.get("repo") or ""
    number = issue_ref.get("number")
    kind = issue_ref.get("kind") or "issue"
    title = issue_ref.get("title") or ""

    if not repo or "/" not in repo or number is None:
        return {"ok": False,
                "error": "issue_ref incompleto (richiesti: repo,owner/name,number)",
                "error_class": "invalid_args"}

    # 1) local_context: DNA Metnos
    files = ["<install_root>/the design guide"]
    adr_subset = _derive_adr_subset(title, max_n=3)
    files.extend(adr_subset)
    local_context = {
        "files": files,
        "inline": _dna_inline(),
    }

    # 2) remote_context: repo + issue
    remote_context = [
        {"kind": "github_repo", "repo": repo, "ref": "main"},
        {"kind": "github_issue", "repo": repo, "number": int(number)},
    ]

    # 3) output_spec strutturato (json schema dichiarativo)
    output_spec = {
        "format": "json",
        "schema": {
            "classification": "enum:support|bug|question|enhancement|other",
            "confidence": "float 0-1",
            "issue_summary_tldr": "string max 200 chars",
            "root_cause_or_answer": "string",
            "affected_files": "list[string]",
            "proposed_fix": "string | null",
            "reply_to_user": "string",
            "related_issues": "list[{ref, similarity, why}]",
        },
        "max_words": 400,
        "style": ("formale-diretto, IT se issue in IT, EN se issue in EN"),
    }

    # 4) invoke consult_frontier via catalog
    try:
        from loader import load_catalog  # type: ignore
        cat = load_catalog(verify=True, include_synth=True)
        ex = cat.executors.get("consult_frontier")
        if ex is None:
            return {"ok": False,
                    "error": "consult_frontier non in catalog",
                    "error_class": "executor_missing"}
        import agent_runtime  # type: ignore
        args = {
            "role": ("esperto reviewer github issue su progetto Python "
                     "self-hosted"),
            "output_spec": output_spec,
            "local_context": local_context,
            "remote_context": remote_context,
            "tools_allowed": [
                "github_read_file", "github_list_dir",
                "github_read_issue", "github_search_code",
            ],
            "tier": "wise",
            "max_tool_iters": 30,
            "max_remote_bytes": 500_000,
        }
        res = agent_runtime.invoke_executor(
            ex, args, timeout_s=300, actor=actor, channel=channel,
        )
    except Exception as ex:  # pragma: no cover - catalog/runtime failure
        _LOG.exception("run_github_analysis: invoke fail")
        return {"ok": False,
                "error": f"{type(ex).__name__}: {ex}",
                "error_class": "runtime_error"}

    if not isinstance(res, dict) or not res.get("ok"):
        err = (res or {}).get("error") if isinstance(res, dict) else "no_result"
        err_class = ((res or {}).get("error_class")
                     if isinstance(res, dict) else "unknown")
        return {"ok": False,
                "error": err or "consult_frontier_fail",
                "error_class": err_class or "consult_frontier_fail"}

    structured = res.get("response_structured") or {}
    if not isinstance(structured, dict):
        # response_text only? fallback: pack raw text in draft_reply
        structured = {}

    return {
        "ok": True,
        "analysis": structured,
        "draft_reply": str(structured.get("reply_to_user")
                           or res.get("response_text") or "").strip(),
        "classification": str(structured.get("classification") or "other"),
        "confidence": float(structured.get("confidence") or 0.0),
        "issue_summary_tldr": str(structured.get("issue_summary_tldr") or ""),
        "root_cause_or_answer": str(
            structured.get("root_cause_or_answer") or ""
        ),
        "affected_files": list(structured.get("affected_files") or []),
        "proposed_fix": structured.get("proposed_fix"),
        "related_issues": list(structured.get("related_issues") or []),
        "cost_usd": float(res.get("cost_usd") or 0.0),
        "tokens_in": int(res.get("tokens_in") or 0),
        "tokens_out": int(res.get("tokens_out") or 0),
        "tier_used": str(res.get("tier_used") or ""),
        "model_used": str(res.get("model_used") or ""),
    }


# ---------- Stage 2 card rendering ---------------------------------------

def format_stage2_card(issue_ref: dict, analysis: dict) -> str:
    """Markdown card per approval Stage 2 (Posta/Modifica/Scarta).

    Sezioni: header (repo + kind#N + title), TLDR, ROOT CAUSE / ANSWER,
    PROPOSED FIX (se presente), DRAFT REPLY, ISSUE CORRELATE (se presente),
    metadata (cost, tier, classification, confidence).

    Determinismo §7.9: pure rendering, niente LLM.
    """
    repo = issue_ref.get("repo") or "?"
    kind = issue_ref.get("kind") or "issue"
    number = issue_ref.get("number")
    title = issue_ref.get("title") or ""

    tldr = (analysis.get("issue_summary_tldr") or "").strip()
    root = (analysis.get("root_cause_or_answer") or "").strip()
    fix = analysis.get("proposed_fix")
    draft = (analysis.get("draft_reply") or "").strip()
    rel = analysis.get("related_issues") or []
    cls = analysis.get("classification") or "other"
    conf = float(analysis.get("confidence") or 0.0)
    cost = float(analysis.get("cost_usd") or 0.0)
    tier = analysis.get("tier_used") or ""
    model = analysis.get("model_used") or ""

    lines: list[str] = []
    lines.append(f"## GitHub {kind} #{number} su {repo}")
    if title:
        lines.append(f"**Titolo**: {title}")
    lines.append("")
    if tldr:
        lines.append("### TLDR")
        lines.append(tldr)
        lines.append("")
    if root:
        if cls == "bug":
            lines.append("### Root cause")
        else:
            lines.append("### Risposta")
        lines.append(root)
        lines.append("")
    if isinstance(fix, str) and fix.strip():
        lines.append("### Proposed fix")
        lines.append(fix.strip())
        lines.append("")
    if draft:
        lines.append("### Draft reply")
        lines.append(draft)
        lines.append("")
    if rel:
        lines.append("### Issue correlate")
        for r in rel[:5]:
            if not isinstance(r, dict):
                continue
            ref = r.get("ref") or "?"
            sim = r.get("similarity")
            why = (r.get("why") or "").strip()
            try:
                sim_str = f"{float(sim):.2f}"
            except (TypeError, ValueError):
                sim_str = "?"
            why_str = f" — {why}" if why else ""
            lines.append(f"  - {ref} (sim {sim_str}){why_str}")
        lines.append("")
    # Metadata
    lines.append(
        f"_classification: {cls} · confidence: {conf:.2f} · "
        f"cost: ${cost:.4f} · tier: {tier}{(' (' + model + ')') if model else ''}_"
    )
    return "\n".join(lines)
