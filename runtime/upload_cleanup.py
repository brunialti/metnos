"""runtime.upload_cleanup — sweep dei file caricati dall'utente in /tmp.

ADR 0092 (5/5/2026): le foto reference allegate via Telegram caption-photo
o HTTP drag&drop vengono salvate in `/tmp/metnos_uploads/<sender>/...`.
Sono one-shot (servono solo per il turno); dopo il turno restano sul disco
finche' non spazzate. Sweep non distruttivo: rimuove SOLO i file con mtime
piu' vecchi di TTL_S (default 1h).

Determinismo §7.9: zero LLM, zero rete. Solo stat + unlink.

Best-effort: chiamato post-turn (canale/HTTP) e ogni N iterazioni dal
daemon (vedi `channels/daemon.py:run_forever`). Niente cron dedicato.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

log = logging.getLogger("metnos.upload_cleanup")

UPLOAD_DIR = Path("/tmp/metnos_uploads")
DEFAULT_TTL_S = 3600  # 1 ora


def _paths_referenced_by_active_dialogs() -> set[str]:
    """Scansiona dialog_pending: ritorna set di path assoluti referenziati
    da dialog non cancellati/non completati (10/5/2026 fix bug live: face
    picker set_persons rompeva quando le upload venivano spazzate dopo 1h
    mentre il dialog era ancora aperto in chat).
    """
    refs: set[str] = set()
    try:
        from pathlib import Path as _P
        import json as _json
        dp_dir = _P.home() / ".local" / "share" / "metnos" / "get_inputs"
        if not dp_dir.exists():
            return refs
        for sender_dir in dp_dir.iterdir():
            if not sender_dir.is_dir():
                continue
            for f in sender_dir.glob("*.json"):
                try:
                    state = _json.loads(f.read_text(encoding="utf-8"))
                except (OSError, _json.JSONDecodeError):
                    continue
                if state.get("cancelled") or state.get("completed"):
                    continue
                for step in state.get("dialog") or []:
                    schema = (step.get("schema") or {}) if isinstance(step, dict) else {}
                    ctx = schema.get("context_image_path")
                    if isinstance(ctx, str) and ctx:
                        refs.add(ctx.split("#", 1)[0])
                    for opt in schema.get("options") or []:
                        prev = (opt.get("preview_image_path") or "") if isinstance(opt, dict) else ""
                        if isinstance(prev, str) and prev:
                            refs.add(prev.split("#", 1)[0])
                # also any direct paths in args_base of on_complete
                oc = state.get("on_complete") or {}
                ab = oc.get("args_base") or {}
                for k in ("paths", "reference_images"):
                    v = ab.get(k)
                    if isinstance(v, list):
                        for x in v:
                            if isinstance(x, str):
                                refs.add(x)
    except Exception as ex:
        log.debug("dialog refs scan failed: %s", ex)
    return refs


def sweep_old_uploads(*, base_dir: Path | None = None,
                      ttl_s: int = DEFAULT_TTL_S,
                      now: float | None = None) -> dict:
    """Rimuove i file in `base_dir` con mtime piu' vecchio di `ttl_s`.

    Ritorna dict `{ok, removed, kept, protected, errors}`. Best-effort:
    errori individuali (file lockato, permessi) non interrompono il sweep.

    NB: il dir `_pending_burst/` (buffer media_group del daemon) viene
    saltato: ha TTL diverso (1.5 s) ed e' gestito da daemon.run_forever.
    NB2: file referenziati da dialog_pending attivi (face picker, etc.)
    sono PROTETTI dal sweep — verrebbero referenziati dal browser durante
    l'interazione utente.
    """
    if base_dir is None:
        base_dir = UPLOAD_DIR
    if now is None:
        now = time.time()
    removed = 0
    kept = 0
    protected = 0
    errors: list[str] = []
    if not base_dir.exists():
        return {"ok": True, "removed": 0, "kept": 0, "protected": 0, "errors": []}
    cutoff = now - max(0, int(ttl_s))
    active_refs = _paths_referenced_by_active_dialogs()
    try:
        for p in base_dir.rglob("*"):
            if not p.is_file():
                continue
            if "_pending_burst" in p.parts:
                continue
            if str(p) in active_refs:
                protected += 1
                continue
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    removed += 1
                else:
                    kept += 1
            except OSError as ex:
                errors.append(f"{p}: {ex}")
        # Cleanup directory vuote (post-rimozione).
        for d in sorted(base_dir.rglob("*"), reverse=True):
            if d.is_dir() and not any(d.iterdir()) and d != base_dir:
                if "_pending_burst" in d.parts:
                    continue
                try:
                    d.rmdir()
                except OSError:
                    pass
    except OSError as ex:
        errors.append(f"sweep failed on {base_dir}: {ex}")
    return {"ok": True, "removed": removed, "kept": kept,
            "protected": protected, "errors": errors}


if __name__ == "__main__":
    import json as _json
    res = sweep_old_uploads()
    print(_json.dumps(res, ensure_ascii=False, indent=2))
