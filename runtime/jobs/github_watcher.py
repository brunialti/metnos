"""github_watcher — scheduler v2 task per il monitoring di repo GitHub (Fase D).

Pipeline per ogni repo watched:
  Stage 0  : detect nuovi issue/PR/commenti (find_issues_github / find_pulls_github)
  Stage 1a : embed query + cosine search vs github_issue_qa.sqlite
  Stage 1b : 4-AND safety → auto-reply dedup (send_messages_github)
  Stage 1c : altrimenti dialog get_inputs (Stage 2 placeholder, completato Fase F)

Config: `~/.config/metnos/github_watched_repos.json` (default empty = no-op).
Default trigger: `every_30m` (Fase 1, niente split business/night).

Tolleranze:
  - executor mancante (find_issues_github / send_messages_github) → log + skip,
    NON crash (Fase B/C ancora in build).
  - BGE non disponibile → skip auto-reply, fallback gate normale.
  - get_inputs failure → log + continue al prossimo repo.

Idempotenza: gli `event_id` osservati sono persistiti in `watch_state.sqlite`;
re-fire dello stesso job non duplica reply.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)


import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as _C  # §7.11
CONFIG_REPOS = _C.PATH_USER_CONFIG / "github_watched_repos.json"
CONFIG_DEDUP = _C.PATH_USER_CONFIG / "github_dedup.json"


# Defaults per github_dedup.json (sovrascritti dal file se presente).
_DEFAULT_DEDUP_CONFIG: dict[str, Any] = {
    "auto_reply_threshold": 0.85,
    "skip_bot_comments": [
        "dependabot[bot]", "github-actions[bot]",
        "codecov[bot]", "renovate[bot]",
    ],
    "skip_own_comments": True,
    "min_event_age_s": 60,
    "snooze_options_s": [3600, 14400, 86400],
}


def _load_repos_config() -> list[dict[str, Any]]:
    """Lista di {repo, events, poll_interval_*}. Default empty = no-op."""
    if not CONFIG_REPOS.exists():
        return []
    try:
        data = json.loads(CONFIG_REPOS.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            _LOG.warning(
                "github_watcher: %s non e' una lista, ignoro", CONFIG_REPOS
            )
            return []
        return data
    except Exception as e:
        _LOG.warning("github_watcher: parse %s fail: %r", CONFIG_REPOS, e)
        return []


def _load_dedup_config() -> dict[str, Any]:
    """Merge con default. File assente = default. Errore parse = default."""
    cfg = dict(_DEFAULT_DEDUP_CONFIG)
    if not CONFIG_DEDUP.exists():
        return cfg
    try:
        user = json.loads(CONFIG_DEDUP.read_text(encoding="utf-8"))
        if isinstance(user, dict):
            cfg.update(user)
    except Exception as e:
        _LOG.warning("github_watcher: parse %s fail: %r", CONFIG_DEDUP, e)
    return cfg


def _ensure_default_dedup_file() -> None:
    """Scrive il file con i default se assente. Idempotente."""
    if CONFIG_DEDUP.exists():
        return
    try:
        CONFIG_DEDUP.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_DEDUP.write_text(
            json.dumps(_DEFAULT_DEDUP_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        _LOG.info("github_watcher: cannot write %s: %r", CONFIG_DEDUP, e)


def _ensure_default_repos_file() -> None:
    """Scrive file vuoto `[]` se assente. Idempotente."""
    if CONFIG_REPOS.exists():
        return
    try:
        CONFIG_REPOS.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_REPOS.write_text("[]\n", encoding="utf-8")
    except Exception as e:
        _LOG.info("github_watcher: cannot write %s: %r", CONFIG_REPOS, e)


def _resolve_executor(name: str):
    """Cerca un executor nel catalog. Ritorna None se assente (Fase B/C in build)."""
    try:
        from loader import load_catalog  # type: ignore
        # verify=False: la verifica firme è già fatta dal loader al boot;
        # rifarla ad OGNI _invoke (più volte per fire, per evento) è spreco.
        cat = load_catalog(verify=False, include_synth=True)
        return cat.executors.get(name)
    except Exception as e:
        _LOG.info("github_watcher: load_catalog fail: %r", e)
        return None


def _invoke(name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """Invoca un executor per nome. Ritorna dict-result o None se executor
    assente / errore. Niente raise: il watcher continua il loop."""
    ex = _resolve_executor(name)
    if ex is None:
        _LOG.info("github_watcher: executor %s assente, skip", name)
        return None
    try:
        import agent_runtime  # type: ignore
        return agent_runtime.invoke_executor(
            ex, args, timeout_s=getattr(ex, "timeout_s", 30),
            actor="host", channel=None,
        )
    except Exception as e:
        _LOG.warning("github_watcher: invoke %s fail: %r", name, e)
        return None


def _detect_events_for_repo(
    repo: str, events_kinds: list[str], dedup_cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    """Detect nuovi issue/PR con commenti nuovi.

    Strategy minimale Fase D: chiama find_issues_github (state=open) e
    find_pulls_github (state=open). Per ogni record, confronta last_event_id
    con watch_state. Se executor mancante → ritorna lista vuota.

    NB: la signature `find_issues_github` non e' ancora stabile (Fase C la
    sta scrivendo). Il watcher tenta `state="open"`, since=last_check;
    formati alternativi sono tollerati (degrade silent).
    """
    import time as _time
    from github_watch_state import get_state, set_state  # type: ignore
    out: list[dict[str, Any]] = []
    skip_bots = set(dedup_cfg.get("skip_bot_comments") or [])
    _now = int(_time.time())

    def _is_snoozed(state: dict[str, Any] | None) -> bool:
        """True se l'utente ha snoozato questo elemento e lo snooze non e'
        ancora scaduto. Senza questo check lo snooze veniva vanificato: ogni
        nuova attivita' ri-gatava l'elemento (bug 1/6/2026)."""
        if not state:
            return False
        sn = state.get("snoozed_until")
        try:
            return bool(sn) and int(sn) > _now
        except (TypeError, ValueError):
            return False

    # Issue
    if "new_issue" in events_kinds or "new_comment" in events_kinds:
        res = _invoke("find_issues_github",
                       {"repo": repo, "state": "open"})
        if res and isinstance(res, dict) and res.get("ok"):
            for e in (res.get("entries") or []):
                if not isinstance(e, dict):
                    continue
                number = e.get("number")
                if number is None:
                    continue
                author = (e.get("author") or e.get("user") or "").strip()
                if author in skip_bots:
                    continue
                last_ev = str(e.get("last_event_id") or e.get("updated_at") or "")
                state = get_state(repo, "issue", int(number))
                if state and state.get("last_event_id") == last_ev:
                    continue  # nessun evento nuovo
                if _is_snoozed(state):
                    continue  # snoozato dall'utente: non ri-gatare
                out.append({
                    "kind": "issue",
                    "number": int(number),
                    "title": e.get("title") or "",
                    "body": e.get("body") or "",
                    "author": author,
                    "last_event_id": last_ev,
                    "raw": e,
                })

    # Pull
    if "new_pr" in events_kinds:
        res = _invoke("find_pulls_github",
                       {"repo": repo, "state": "open"})
        if res and isinstance(res, dict) and res.get("ok"):
            for e in (res.get("entries") or []):
                if not isinstance(e, dict):
                    continue
                number = e.get("number")
                if number is None:
                    continue
                author = (e.get("author") or e.get("user") or "").strip()
                if author in skip_bots:
                    continue
                last_ev = str(e.get("last_event_id") or e.get("updated_at") or "")
                state = get_state(repo, "pr", int(number))
                if state and state.get("last_event_id") == last_ev:
                    continue
                if _is_snoozed(state):
                    continue  # snoozato dall'utente: non ri-gatare
                out.append({
                    "kind": "pr",
                    "number": int(number),
                    "title": e.get("title") or "",
                    "body": e.get("body") or "",
                    "author": author,
                    "last_event_id": last_ev,
                    "raw": e,
                })
    return out


def _notify_owner(text: str) -> None:
    """Invia notifica post-hoc all'admin via send_messages (Telegram).
    Best-effort: failure logged ma watcher continua."""
    try:
        # Contratto canonico send_messages (vedi builtin_callbacks): wrapper
        # `messages: [{to_user, subject, body}]`. Prima usava {to:["host"]} +
        # body top-level → arg invalidi → notifica mai recapitata (§2.8).
        res = _invoke("send_messages", {
            "messages": [{
                "to_user": "host",
                "subject": "GitHub watcher",
                "body": text,
            }],
        })
        ok = isinstance(res, dict) and int(res.get("ok_count") or 0) >= 1
        if not ok:
            _LOG.info("github_watcher: notify_owner non recapitato (%r)", res)
    except Exception as e:
        _LOG.info("github_watcher: notify_owner fail: %r", e)


def _open_gate_dialog(repo: str, kind: str, number: int,
                       title: str, body: str,
                       top_matches: list[dict[str, Any]],
                       classification_hint: str,
                       snooze_options_s: list[int]) -> bool:
    """Apre dialog get_inputs (Stage 2) per gate Roberto. Ritorna True se
    il dialog e' stato registrato, False altrimenti.

    Il `on_complete` ha `type=github_analyze` + flag `TODO_PHASE_F`: il
    callback consumer e' implementato in Fase F (chiama consult_frontier
    e poi torna ad un altro get_inputs di approval Stage 2 finale)."""
    refs_summary = ", ".join(
        f"{m['ref']} ({m['similarity']:.2f})" for m in top_matches[:3]
    ) or "nessun match precedente"
    preview = (body or "")[:200]
    prompt = (
        f"GitHub {kind} #{number} su {repo}\n"
        f"Titolo: {title}\n"
        f"Hint: {classification_hint}\n"
        f"Match storici: {refs_summary}\n\n"
        f"Anteprima: {preview}"
    )
    on_complete = {
        "type": "github_analyze",
        "issue_ref": {"repo": repo, "kind": kind, "number": int(number)},
        "classification_hint": classification_hint,
        "related_refs": [
            {"ref": m["ref"], "similarity": m["similarity"]}
            for m in top_matches[:5]
        ],
        "TODO_PHASE_F": True,
    }
    args = {
        "title": f"GitHub {kind} #{number}",
        "dialog": [
            {
                "kind": "choice",
                "label": prompt,
                "options": ["analizza", "skip", "snooze 1h", "snooze 4h", "snooze 24h"],
                "var": "decision",
            },
        ],
        "fmt": "auto",
        "on_complete": on_complete,
    }
    res = _invoke("get_inputs", args)
    registered = bool(res and isinstance(res, dict) and res.get("ok"))
    # Headless (§2.8): in un task schedulato get_inputs SOLO registra il dialog
    # (sender_id="host", channel="") — nessun channel adapter lo spinge. Senza
    # questo, il gate e' un drop SILENZIOSO: il dialog resta nello storage ma
    # Roberto non lo vede mai (era il bug "dialog_failed"). Notifichiamo l'owner
    # via il path PROVATO (send_messages -> Telegram host, vedi _notify_owner)
    # cosi' la richiesta di decisione lo raggiunge; il dialog registrato resta
    # azionabile quando apre un canale.
    _notify_owner(
        f"{prompt}\n\nDecisione richiesta (analizza / skip / snooze): "
        f"apri Metnos per rispondere."
    )
    return registered


def _event_age_s(event: dict[str, Any]) -> float:
    """Età in secondi dell'evento da `updated_at`/`created_at` (ISO) del raw.
    `inf` se ignota (non blocca: età sconosciuta = considerata non-fresca)."""
    import datetime as _dt
    raw = event.get("raw") or {}
    ts = raw.get("updated_at") or raw.get("created_at")
    if not ts:
        return float("inf")
    try:
        t = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=_dt.timezone.utc)
        return (_dt.datetime.now(_dt.timezone.utc) - t).total_seconds()
    except Exception:
        return float("inf")


def _process_event(
    repo: str, event: dict[str, Any], dedup_cfg: dict[str, Any],
) -> dict[str, Any]:
    """Ritorna {auto_replied: bool, gated: bool, error: str|None}."""
    from github_watch_state import set_state  # type: ignore
    from github_issue_qa_store import find_similar, insert as qa_insert  # type: ignore
    from .github_dedup import (
        embed_query, classify_hint, check_4_and_safety,
        format_auto_reply_body, similarity_band_of,
    )

    kind = event["kind"]
    number = int(event["number"])
    title = event.get("title") or ""
    body = event.get("body") or ""
    last_ev = event.get("last_event_id") or ""

    # Stage 1a: embed + search
    hint = classify_hint(title, body)
    qe = embed_query(f"{title}\n\n{body}")
    top_matches: list[dict[str, Any]] = []
    if qe is not None:
        top_matches = find_similar(repo, qe, top_n=5)

    # Stage 1b: 4-AND safety check. Soglia da config `auto_reply_threshold`
    # (prima hardcoded 0.85, config inerte) + anti-race `min_event_age_s`:
    # non auto-rispondere a eventi troppo recenti (utente potrebbe star
    # ancora editando / commento appena arrivato).
    threshold = float(dedup_cfg.get("auto_reply_threshold") or 0.85)
    min_age = int(dedup_cfg.get("min_event_age_s") or 0)
    fresh_enough = min_age <= 0 or _event_age_s(event) >= min_age
    auto = False
    if (fresh_enough and top_matches
            and check_4_and_safety(top_matches[0], hint, min_similarity=threshold)):
        body_reply = format_auto_reply_body(
            top_matches, similarity_band_of(top_matches[0]["similarity"]),
        )
        send_res = _invoke("send_messages_github", {
            "target": f"{kind}:{number}",
            "repo": repo,
            "body": body_reply,
        })
        if send_res and isinstance(send_res, dict) and send_res.get("ok"):
            auto = True
            related_refs = [
                {"ref": m["ref"], "similarity": m["similarity"]}
                for m in top_matches[:5]
            ]
            qa_insert(
                repo=repo, issue_number=number,
                classification=hint,
                question_text=f"{title}\n\n{body}",
                embedding=qe,
                accepted_reply=body_reply,
                related_refs=related_refs,
                cost_usd=0.0,
                auto_replied=True,
            )
            _notify_owner(
                f"auto-risposto a {kind} #{number} su {repo} "
                f"(match {top_matches[0]['similarity']:.2f} con "
                f"{top_matches[0]['ref']})"
            )
            set_state(repo, kind, number, last_event_id=last_ev)
            return {"auto_replied": True, "gated": False, "error": None}
        else:
            _LOG.info(
                "github_watcher: send_messages_github fail su %s#%d",
                repo, number,
            )

    # Stage 1c: dialog gate (Fase F implementera' il consumer)
    snooze_opts = dedup_cfg.get("snooze_options_s") or [3600, 14400, 86400]
    ok = _open_gate_dialog(
        repo, kind, number, title, body, top_matches, hint, snooze_opts,
    )
    set_state(repo, kind, number, last_event_id=last_ev)
    return {
        "auto_replied": False,
        "gated": bool(ok),
        "error": None if ok else "dialog_failed",
    }


def task_github_watcher(payload: dict | None = None) -> dict[str, Any]:
    """Entry point scheduler v2 (cb(payload) signature).

    Trigger default `every_30m`. Ritorna report sintetico:
      {ok, summary, n_repos, n_new_events, n_auto_replied,
       n_pending_user, errors}
    """
    from github_watch_state import init_db as init_state, expired_snooze_cleanup
    from github_issue_qa_store import init_db as init_qa

    # Idempotent init (no-op se gia' presenti).
    init_state()
    init_qa()
    expired_snooze_cleanup()
    _ensure_default_repos_file()
    _ensure_default_dedup_file()

    repos = _load_repos_config()
    dedup_cfg = _load_dedup_config()

    if not repos:
        return {
            "ok": True,
            "summary": "no repos watched",
            "n_repos": 0,
            "n_new_events": 0,
            "n_auto_replied": 0,
            "n_pending_user": 0,
            "errors": [],
        }

    n_new = 0
    n_auto = 0
    n_gate = 0
    errors: list[str] = []
    started = time.time()

    for repo_cfg in repos:
        if not isinstance(repo_cfg, dict):
            continue
        repo = repo_cfg.get("repo")
        events_kinds = repo_cfg.get("events") or ["new_issue", "new_comment", "new_pr"]
        if not isinstance(repo, str) or "/" not in repo:
            errors.append(f"repo_cfg invalido: {repo_cfg!r}")
            continue
        try:
            events = _detect_events_for_repo(repo, events_kinds, dedup_cfg)
        except Exception as e:
            _LOG.exception("github_watcher: detect fail su %s", repo)
            errors.append(f"detect {repo}: {e!r}")
            continue
        for ev in events:
            n_new += 1
            try:
                rep = _process_event(repo, ev, dedup_cfg)
            except Exception as e:
                _LOG.exception(
                    "github_watcher: process fail su %s %s#%s",
                    repo, ev.get("kind"), ev.get("number"),
                )
                errors.append(
                    f"process {repo} {ev.get('kind')}#{ev.get('number')}: {e!r}"
                )
                continue
            if rep["auto_replied"]:
                n_auto += 1
            elif rep["gated"]:
                n_gate += 1
            elif rep.get("error"):
                errors.append(
                    f"{repo} {ev.get('kind')}#{ev.get('number')}: {rep['error']}"
                )

    elapsed = time.time() - started
    summary = (
        f"checked {len(repos)} repos, {n_new} new events, "
        f"{n_auto} auto-replied, {n_gate} pending user"
    )
    return {
        "ok": True,
        "summary": summary,
        "n_repos": len(repos),
        "n_new_events": n_new,
        "n_auto_replied": n_auto,
        "n_pending_user": n_gate,
        "errors": errors,
        "elapsed_s": round(elapsed, 3),
    }
