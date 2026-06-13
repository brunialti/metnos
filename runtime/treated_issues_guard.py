"""treated_issues_guard — pre-filtro deterministico §7.9 nei run schedulati.

Meccanismo anti-costo (12/6/2026, flusso `github_maintenance_flow.html`,
riga di robustezza «già trattate non rientrano»): nel run schedulato di
manutenzione (`run_user_query`, ogni 30m) una issue GIÀ registrata nello
store `issue_qa` (chiave repo+issue_number) e ancora aperta su GitHub
ri-entrava negli step LLM-costosi (classify_entries / describe_entries /
extract_entries — fino al tier FRONTIER, A PAGAMENTO) PRIMA che il dedup
notify-once di `write_issues` la scartasse A VALLE. Costo ricorrente,
zero valore.

Questo guard sta A MONTE del costo: al confine d'invocazione dei builtin
LLM-augmented, SOLO nei turni schedulati (ContextVar settata da
`recurring_tasks._run_user_query_callback` attorno a `run_turn`), droppa
dalle `entries` i record-issue il cui trattamento è già avvenuto
(status `prepared`/`approved`/`posted`; `new` NON è incluso: frontier
giù → resta `new` → DEVE essere ritrattata). Solo le issue NUOVE
proseguono nel ciclo costoso, UNA volta.

Proprietà:
- deterministico §7.9 (lookup sqlite, zero LLM);
- prompt-independent §7.3 (vale per qualunque formulazione della query
  schedulata: il match è sulla FORMA delle entries, non sul testo);
- scope = soli turni schedulati: una query interattiva («classifica le
  issue aperte») resta intoccata;
- fail-open §2.8: un errore del guard non rompe mai la pipeline;
- onestà §2.7/§2.8: il risultato dello step viene annotato con
  `skipped_known` + `skipped_known_refs` + `note` (visibile nel turn log
  e coerente con `_scheduled_push_is_noop`).
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar

from logging_setup import get_logger

log = get_logger(__name__)

# Flag turno schedulato. Settata SOLO da `scheduled_turn_scope()` (chiamata
# in recurring_tasks attorno a run_turn). I path di esecuzione step
# (planner loop, engine dispatch, piani serviti) sono sincroni nello stesso
# thread → la ContextVar è visibile in tutti i call-site del guard.
_SCHEDULED_TURN: ContextVar[bool] = ContextVar(
    "metnos_scheduled_turn", default=False)

# Builtin in-process LLM-augmented: gli step "costosi" da proteggere.
LLM_COSTLY_BUILTINS = frozenset(
    {"classify_entries", "describe_entries", "extract_entries"})

# Status che indicano fase costosa GIÀ pagata. 'new' escluso per contratto
# (github_maintenance_flow: frontier irraggiungibile → resta new → retry).
_TREATED_STATUSES = ("prepared", "approved", "posted")

_ISSUE_URL_RE = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/issues/(\d+)")


@contextmanager
def scheduled_turn_scope():
    """Marca il blocco corrente come turno schedulato (con reset garantito:
    i worker thread sono riusati, niente flag residua sui turni interattivi)."""
    token = _SCHEDULED_TURN.set(True)
    try:
        yield
    finally:
        _SCHEDULED_TURN.reset(token)


def is_scheduled_turn() -> bool:
    return bool(_SCHEDULED_TURN.get())


# Prefissi dei tool di NOTIFICA/INVIO outbound verso l'utente. Una notifica
# in-piano (es. send_messages finale «ti ho fatto X») in un turno schedulato
# è il «notifica» della query: va emessa SOLO se c'è qualcosa di nuovo.
SCHEDULED_NOTIFY_PREFIXES = ("send_",)


def suppress_scheduled_notify(tool, prior_steps) -> bool:
    """True se, in un turno SCHEDULATO, lo step di notifica/invio `tool` va
    SOPPRESSO perché la pipeline a monte è «a vuoto» (0 effetto reale).

    Realizza il requisito «notifica SOLO se c'è qualcosa di nuovo» (Roberto,
    §2.8): un run schedulato che non ha prodotto nulla non deve spammare un
    falso successo (bug live 13/6: «leggi i nuovi issue… → send_messages(ti ho
    analizzato/salvato bozze)» partiva anche su 0 issue aperte — la
    soppressione del push SCHEDULER non copre il send IN-PIANO).

    Confine (deterministico §7.9, generale §7.3):
    - solo in turno schedulato (`is_scheduled_turn`); turni interattivi MAI;
    - solo tool outbound (`SCHEDULED_NOTIFY_PREFIXES`);
    - solo se gli step A MONTE indicano no-op (`counts_indicate_noop`):
      pure-send senza upstream contabile → counts None → NON soppresso (è il
      deliverable, es. promemoria/heartbeat); fallimenti a monte → NON
      soppresso (vanno riportati).
    `prior_steps` = step già eseguiti nel turno (escluso il send corrente),
    shape-agnostic (StepRun engine v2 o StepLog ReAct)."""
    if not is_scheduled_turn():
        return False
    if not tool or not any(tool.startswith(p) for p in SCHEDULED_NOTIFY_PREFIXES):
        return False
    try:
        from pipeline_effects import pipeline_effect_counts, counts_indicate_noop
        return counts_indicate_noop(pipeline_effect_counts(prior_steps))
    except Exception:
        return False  # fail-open §2.8: nel dubbio NON sopprimere


def _issue_identity(e) -> "tuple[str, int] | None":
    """(repo, issue_number) se la entry è un record-issue identificabile,
    altrimenti None. Copre le due forme reali in pipeline:
    - producer remoto (`find_issues_github`/`read_issues_github`):
      kind='github_issue' + number + html_url;
    - store locale (`read_issues`): repo + issue_number.
    Entry generiche (mail, file, web) → None (guard non le tocca)."""
    if not isinstance(e, dict):
        return None
    repo = e.get("repo")
    number = e.get("issue_number")
    if number is None and e.get("kind") == "github_issue":
        number = e.get("number")
    if (not repo or number is None) and e.get("html_url"):
        m = _ISSUE_URL_RE.search(str(e["html_url"]))
        if m:
            repo = repo or m.group(1)
            number = number if number is not None else m.group(2)
    if not repo or number is None:
        return None
    try:
        return str(repo), int(number)
    except (TypeError, ValueError):
        return None


def filter_treated_issue_entries(tool_name: str, args: dict):
    """Ritorna `(args, info|None)`. Attivo SOLO se: turno schedulato +
    tool in LLM_COSTLY_BUILTINS + entries contengono record-issue già
    trattati in `issue_qa`. In ogni altro caso `args` passa intoccato.

    `info` = {"skipped_known": int, "skipped_known_refs": [...], "note": str}
    da applicare al risultato dello step con `annotate_skipped_known`.
    """
    try:
        if not _SCHEDULED_TURN.get():
            return args, None
        if tool_name not in LLM_COSTLY_BUILTINS:
            return args, None
        entries = (args or {}).get("entries")
        if not isinstance(entries, list) or not entries:
            return args, None
        idents = [_issue_identity(e) for e in entries]
        by_repo: dict[str, set[int]] = {}
        for ident in idents:
            if ident:
                by_repo.setdefault(ident[0], set()).add(ident[1])
        if not by_repo:
            return args, None
        import github_issue_qa_store as store
        treated: set[tuple[str, int]] = set()
        for repo, nums in by_repo.items():
            recs = store.list_records(
                repo=repo, status=list(_TREATED_STATUSES),
                numbers=sorted(nums), limit=max(200, len(nums)))
            for r in recs:
                treated.add((repo, int(r["issue_number"])))
        if not treated:
            return args, None
        kept, refs = [], []
        for e, ident in zip(entries, idents):
            if ident and ident in treated:
                refs.append(f"{ident[0]}#{ident[1]}")
            else:
                kept.append(e)
        if not refs:
            return args, None
        new_args = dict(args)
        new_args["entries"] = kept
        info = {
            "skipped_known": len(refs),
            "skipped_known_refs": refs[:20],
            "note": (f"scheduled run: {len(refs)} already-treated issue(s) "
                     f"in issue_qa excluded upstream of {tool_name} "
                     f"(no LLM/frontier re-pay)"),
        }
        log.info("[treated_issues_guard] %s: dropped %d known issue(s): %s",
                 tool_name, len(refs), refs[:10])
        return new_args, info
    except Exception as ex:  # fail-open §2.8: mai rompere la pipeline
        log.warning("treated_issues_guard fail-open: %r", ex)
        return args, None


def annotate_skipped_known(result, info):
    """Applica l'annotazione di onestà §2.7/§2.8 al risultato dello step.
    No-op se info è None o result non è un dict."""
    if not info or not isinstance(result, dict):
        return result
    result["skipped_known"] = (
        int(result.get("skipped_known") or 0) + int(info["skipped_known"]))
    result["skipped_known_refs"] = list(info["skipped_known_refs"])
    prev = result.get("note")
    result["note"] = (f"{prev} | {info['note']}" if prev else info["note"])
    return result
