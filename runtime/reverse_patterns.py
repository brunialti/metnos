"""reverse_patterns.py — catalogo deterministico dei pattern di undo per Metnos.

Idea: il synt non scrive mai una funzione `reverse(plan, results)` libera.
Sceglie da un catalogo chiuso di pattern, dichiarandolo nel manifest:

    reverse_pattern = "swap_src_dst"
    # oppure multistage:
    reverse_pattern = ["swap_src_dst", "delete_created_dirs"]

L'`undo_last_turn` lookup priority:
    1. manifest.reverse_pattern  → esegue catalogo (deterministico).
    2. modulo.reverse(plan, results)  → fallback back-compat per executor manuali.
    3. niente  → skip (irreversible).

Aggiungere un pattern nuovo: definire la funzione + entry in `PATTERNS`.
Il vocabolario e' chiuso: synt non puo' inventare. Se un caso d'uso non
rientra → escalation a Roberto per progettare un pattern aggiuntivo.

Replica cross-language: i client remoti (Rust per Windows) replicano lo
stesso catalogo coi medesimi nomi.
"""
import shutil
from pathlib import Path

from logging_setup import get_logger
log = get_logger(__name__)


# ---- Pattern: swap_src_dst -----------------------------------------------
# Inverso di un'operazione che ha registrato pair {src, dst} nei results.
# Es. move_files: i pair vengono ribaltati (dst -> src).

def _swap_src_dst(plan, results):
    """Inverso generico di un move che ha registrato pair {src, dst}.
    Riconosce due varianti dal payload del result:
      - FILESYSTEM: pair = {src: path, dst: path} → shutil.move(dst → src)
      - IMAP: pair = {account, src: folder, dst: folder, uid} → IMAP COPY
        + STORE \\Deleted + EXPUNGE in senso inverso.
    La distinzione e' basata sulla presenza di campi specifici:
    `account` + `uid` = IMAP; altrimenti = filesystem path.
    """
    pairs = (results or {}).get("results") or []
    if not pairs:
        return {"ok": True, "ok_count": 0, "fail_count": 0, "results": [], "failed": []}
    # Sniff: il primo pair ci dice il dominio. Schema accettati:
    #   FLAT IMAP:   {"account", "src": folder, "dst": folder, "uid", "message_id"}
    #   NESTED IMAP: {"src": {"account","folder","uid"}, "dst": {"account","folder","uid"}, "message_id"}
    #   FS:          {"src": "/path", "dst": "/path"}
    first = pairs[0] if isinstance(pairs[0], dict) else {}
    src_v = first.get("src")
    is_imap_flat = ("account" in first and "uid" in first)
    is_imap_nested = isinstance(src_v, dict) and "account" in src_v and "uid" in src_v
    if is_imap_flat or is_imap_nested:
        if is_imap_nested:
            # Normalizza nested → flat per il resto della pipeline.
            pairs = [_normalize_imap_pair(p) for p in pairs]
        return _swap_src_dst_imap(pairs)
    return _swap_src_dst_filesystem(pairs)


def _normalize_imap_pair(p):
    """Converte schema nested src/dst (dict) a flat (string + top-level account/uid).
    Schema input nested:
        {"src": {"account":a,"folder":f,"uid":u}, "dst": {"account":a,"folder":F,"uid":U}, "message_id":m}
    Schema output flat:
        {"account":a,"src":f,"dst":F,"uid":u,"message_id":m,"_dst_uid":U}
    L'`uid` flat e' quello della SORGENTE (per ricreare lo state pre-move).
    `_dst_uid` conserva il nuovo UID in destinazione (utile per debug/audit).
    """
    src, dst = p.get("src", {}), p.get("dst", {})
    if not isinstance(src, dict) or not isinstance(dst, dict):
        return p
    return {
        "account": src.get("account") or dst.get("account"),
        "src": src.get("folder"),
        "dst": dst.get("folder"),
        "uid": src.get("uid"),
        "_dst_uid": dst.get("uid"),
        "message_id": p.get("message_id"),
    }


def _swap_src_dst_filesystem(pairs):
    out, failed = [], []
    for i, p in enumerate(pairs):
        src_now = Path(p["dst"])
        dst_back = Path(p["src"])
        if str(src_now) == str(dst_back):
            continue
        if not src_now.exists():
            failed.append({"index": i, "error": f"dst no longer exists: {src_now}"})
            continue
        if dst_back.exists():
            failed.append({"index": i, "error": f"src position already occupied: {dst_back}"})
            continue
        try:
            dst_back.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_now), str(dst_back))
            out.append({"src": str(src_now), "dst": str(dst_back)})
        except OSError as e:
            failed.append({"index": i, "error": str(e)})
    return {
        "ok": len(failed) == 0,
        "ok_count": len(out),
        "fail_count": len(failed),
        "results": out,
        "failed": failed,
    }


def _q_imap(name):
    """Quote IMAP folder names con spazi/non-ASCII (imaplib non quota auto)."""
    if name and (" " in name or any(ord(c) > 127 for c in name)):
        return f'"{name}"'
    return name


def _swap_src_dst_imap(pairs):
    """Ribalta IMAP move usando il `message_id` come ID stabile cross-folder.
    L'UID IMAP cambia fra cartelle (server-assigned al COPY): cercare per UID
    nella destinazione fallisce silenziosamente. Il Message-ID (header
    globally unique) sopravvive al COPY e permette di trovare la mail
    nella destinazione anche se il suo UID e' diverso.
    Senza message_id nel pair: skip + failed (e' un dato runtime mancante).
    """
    # runtime/ già su sys.path (reverse_patterns VIVE in runtime/).
    from mail_client import open_imap
    by_account = {}
    for p in pairs:
        by_account.setdefault(p["account"], []).append(p)
    out, failed = [], []
    for account, group in by_account.items():
        try:
            conn = open_imap(account)
        except Exception as e:
            for p in group:
                failed.append({"index": pairs.index(p), "uid": p.get("uid"),
                               "error": f"IMAP connect failed: {e}"})
            continue
        by_dst = {}
        for p in group:
            by_dst.setdefault(p["dst"], []).append(p)
        try:
            for dst_folder, dst_group in by_dst.items():
                sstatus, _ = conn.select(_q_imap(dst_folder), readonly=False)
                if sstatus != "OK":
                    for p in dst_group:
                        failed.append({"index": pairs.index(p), "uid": p.get("uid"),
                                       "error": f"select {dst_folder} failed"})
                    continue
                for p in dst_group:
                    src_back = p["src"]
                    mid = p.get("message_id")
                    if not mid:
                        # Senza message_id il reverse e' inaffidabile (UID diverso
                        # nelle folder). Dichiariamo fallito invece di tentare alla cieca.
                        failed.append({"index": pairs.index(p), "uid": p.get("uid"),
                                       "error": "missing message_id in result, cannot reliably reverse IMAP move"})
                        continue
                    # Cerca per Message-ID nella folder di destinazione (charset=None)
                    sstatus2, sdata = conn.uid("SEARCH", None, "HEADER", "Message-ID", mid)
                    if sstatus2 != "OK" or not sdata or not sdata[0]:
                        failed.append({"index": pairs.index(p), "uid": p.get("uid"),
                                       "error": f"message_id {mid} not found in {dst_folder}"})
                        continue
                    found_uids = sdata[0].split()
                    if not found_uids:
                        failed.append({"index": pairs.index(p), "uid": p.get("uid"),
                                       "error": f"message_id {mid} not found in {dst_folder}"})
                        continue
                    uid_in_dst = found_uids[0].decode() if isinstance(found_uids[0], bytes) else str(found_uids[0])
                    cstatus, _ = conn.uid("COPY", uid_in_dst, _q_imap(src_back))
                    if cstatus != "OK":
                        failed.append({"index": pairs.index(p), "uid": uid_in_dst,
                                       "error": f"COPY back to {src_back} failed: {cstatus}"})
                        continue
                    conn.uid("STORE", uid_in_dst, "+FLAGS", r"(\Deleted)")
                    out.append({"src": dst_folder, "dst": src_back,
                                "uid": uid_in_dst, "account": account,
                                "message_id": mid})
                try:
                    conn.expunge()
                except Exception as _e:  # silent swallow (auto-fixed)
                    log.warning("silent exception in %s: %s", __name__, _e)
        finally:
            try: conn.close()
            except Exception as _e:  # silent swallow (auto-fixed)
                log.warning("silent exception in %s: %s", __name__, _e)
            try: conn.logout()
            except Exception as _e:  # silent swallow (auto-fixed)
                log.warning("silent exception in %s: %s", __name__, _e)
    return {
        "ok": len(failed) == 0,
        "ok_count": len(out),
        "fail_count": len(failed),
        "results": out,
        "failed": failed,
    }


# ---- Pattern: delete_created_dirs ----------------------------------------
# Rimuove le directory tracciate in `results.dirs_created` se ancora vuote.
# Es. seconda fase di undo per move_files (rimuove dir create dal forward).

def _delete_created_dirs(plan, results):
    dirs = (results or {}).get("dirs_created") or []
    sorted_dirs = sorted(dirs, key=lambda p: p.count("/"), reverse=True)
    removed, kept = [], []
    for d_str in sorted_dirs:
        d = Path(d_str)
        if not d.exists() or not d.is_dir():
            continue
        try:
            if not any(d.iterdir()):
                d.rmdir()
                removed.append(d_str)
            else:
                kept.append(d_str)
        except OSError:
            kept.append(d_str)
    return {"ok": True, "removed": removed, "kept": kept}


# ---- Pattern: delete_created_paths ---------------------------------------
# Rimuove i path appena creati dal forward. Funziona su FILE e DIRECTORY
# (questi ultimi solo se ancora vuoti, per non perdere dati altrui).
# Sorgenti dei path da rimuovere:
#   1. `results.results[]` con `created=true` e `path` (convenzione canonica).
#   2. `results.dirs_created` (lista di str): directory parent create
#      indirettamente (es. mkdir -p dentro write_files).
# Order di rimozione: file prima, poi dir piu' profonde (per rimuovere
# i parent una volta vuoti).

def _delete_created_paths(plan, results):
    entries = (results or {}).get("results") or []
    parent_dirs = (results or {}).get("dirs_created") or []
    files = []
    dirs = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("created"):
            continue
        p = Path(entry["path"])
        if p.is_file() or p.is_symlink():
            files.append(p)
        elif p.is_dir():
            dirs.append(p)
        elif not p.exists():
            # Path non esiste piu' (gia' rimosso): noop.
            continue
    for ds in parent_dirs:
        dirs.append(Path(ds))
    # File prima (cosi' poi le dir possono diventare vuote), poi dir
    # in ordine di profondita' decrescente.
    out, failed = [], []
    for f in files:
        try:
            f.unlink()
            out.append({"path": str(f), "removed": True, "kind": "file"})
        except FileNotFoundError:
            continue
        except OSError as e:
            failed.append({"path": str(f), "error": str(e), "kind": "file"})
    dirs_sorted = sorted(set(dirs), key=lambda d: str(d).count("/"), reverse=True)
    for d in dirs_sorted:
        if not d.exists():
            continue
        if not d.is_dir():
            failed.append({"path": str(d), "error": "not a directory"})
            continue
        try:
            if not any(d.iterdir()):
                d.rmdir()
                out.append({"path": str(d), "removed": True, "kind": "dir"})
            else:
                failed.append({"path": str(d), "error": "directory not empty",
                               "kind": "dir"})
        except OSError as e:
            failed.append({"path": str(d), "error": str(e), "kind": "dir"})
    return {
        "ok": len(failed) == 0,
        "ok_count": len(out),
        "fail_count": len(failed),
        "results": out,
        "failed": failed,
    }


# ---- Pattern: restore_blob_backup ----------------------------------------
# Ripristina i blob salvati prima di un'operazione distruttiva (delete/write
# con overwrite). Schema results atteso:
#   FS:    {"path": str, "blob_path": str}   (blob_path = path al file blob su disco)
#   IMAP:  {"account": str, "folder": str, "blob_path": str}
#   FS write con overwrite: {"path": str, "prev_blob_path": str}
# Il blob e' i bytes letterali del file (FS) o raw RFC822 (IMAP).
# Compatibile con `blob_sha256` legacy (ricerca in METNOS_HISTORY_DIR).

def _restore_blob_backup(plan, results):
    import os as _os
    from pathlib import Path as _Path
    import config as _C  # §7.11
    entries = (results or {}).get("results") or []
    out, failed = [], []
    history_root = _Path(_os.environ.get(
        "METNOS_HISTORY_DIR",
        _C.PATH_USER_DATA / "_history",
    ))
    imap_conns = {}  # account -> open IMAP connection (lazy)

    def _find_blob_path(entry):
        # Priorita': blob_path esplicito > prev_blob_path > sha256 lookup
        bp = entry.get("blob_path") or entry.get("prev_blob_path")
        if bp and _Path(bp).exists():
            return _Path(bp)
        sha = entry.get("blob_sha256") or entry.get("prev_blob_sha256")
        if sha:
            sha = sha.replace("sha256:", "")
            for cand in history_root.rglob(f"{sha}*"):
                if cand.is_file():
                    return cand
        return None

    try:
        for i, entry in enumerate(entries):
            blob_path = _find_blob_path(entry)
            if blob_path is None:
                failed.append({"index": i, "error": "blob not found",
                               "entry": entry})
                continue
            try:
                raw = blob_path.read_bytes()
            except Exception as e:
                failed.append({"index": i, "error": f"blob read failed: {e}",
                               "blob_path": str(blob_path)})
                continue
            # Sniff: FS se ha "path", IMAP se ha "account"+"folder"
            if "path" in entry and "account" not in entry:
                target = _Path(entry["path"])
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(raw)
                    out.append({"path": str(target),
                                "bytes_restored": len(raw),
                                "blob_path": str(blob_path)})
                except Exception as e:
                    failed.append({"index": i, "path": entry.get("path"),
                                   "error": f"write failed: {e}"})
            elif "account" in entry and "folder" in entry:
                account = entry["account"]
                folder = entry["folder"]
                try:
                    if account not in imap_conns:
                        # runtime/ già su sys.path (reverse_patterns VIVE in runtime/).
                        from mail_client import open_imap
                        imap_conns[account] = open_imap(account)
                    M = imap_conns[account]
                    fname = _q_imap(folder)  # _q_imap fa già il check spazi/non-ASCII
                    typ, _ = M.append(fname, None, None, raw)
                    if typ != "OK":
                        failed.append({"index": i, "error": f"IMAP append failed: {typ}",
                                       "account": account, "folder": folder})
                        continue
                    out.append({"account": account, "folder": folder,
                                "bytes_restored": len(raw),
                                "blob_path": str(blob_path),
                                "message_id": entry.get("message_id")})
                except Exception as e:
                    failed.append({"index": i, "account": account, "folder": folder,
                                   "error": f"IMAP restore failed: {e}"})
            else:
                failed.append({"index": i, "error": "unknown entry shape (no path/account)",
                               "entry": entry})
    finally:
        for conn in imap_conns.values():
            try: conn.logout()
            except Exception as _e:  # silent swallow (auto-fixed)
                log.warning("silent exception in %s: %s", __name__, _e)
    return {
        "ok": len(failed) == 0,
        "ok_count": len(out),
        "fail_count": len(failed),
        "results": out,
        "failed": failed,
    }


# ---- Catalog -------------------------------------------------------------

PATTERNS = {
    "swap_src_dst":         _swap_src_dst,
    "delete_created_dirs":  _delete_created_dirs,
    "delete_created_paths": _delete_created_paths,
    "restore_blob_backup":  _restore_blob_backup,
}

# 5° famiglia `delete_<object>_by_id` (ADR 0123 §2.3): registrata in-place dal
# patch module per evitare di duplicare il registry per oggetto.
import sys as _sys  # noqa: E402
from reverse_patterns_patch import register_delete_by_id_pattern as _register_by_id  # noqa: E402
_register_by_id(_sys.modules[__name__])
del _sys, _register_by_id


def apply_pattern(name: str, plan: dict, results: dict) -> dict:
    """Esegue un pattern singolo. Errore esplicito se nome non in catalog."""
    fn = PATTERNS.get(name)
    if fn is None:
        return {"ok": False, "error": f"unknown reverse_pattern: {name!r}; valid: {sorted(PATTERNS.keys())}"}
    try:
        return fn(plan or {}, results or {})
    except Exception as e:
        return {"ok": False, "error": f"pattern {name!r} raised: {e}"}


def apply_patterns(names, plan: dict, results: dict) -> dict:
    """Esegue uno o piu' pattern in ordine (multistage). Aggrega risultati."""
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list):
        return {"ok": False, "error": f"reverse_pattern must be string or list, got {type(names).__name__}"}
    stages = []
    overall_ok = True
    total_ok = 0
    total_fail = 0
    for n in names:
        r = apply_pattern(n, plan, results)
        stages.append({"pattern": n, "result": r})
        if r.get("ok", True) is False:
            overall_ok = False
        total_ok += r.get("ok_count", 0)
        total_fail += r.get("fail_count", 0)
    return {
        "ok": overall_ok,
        "ok_count": total_ok,
        "fail_count": total_fail,
        "stages": stages,
    }
