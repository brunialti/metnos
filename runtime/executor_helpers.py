# SPDX-License-Identifier: AGPL-3.0-only
"""Helper comuni per executor — robustness al confine NL→determinismo (§2.4).

Pattern §2.4 the design guide: gli executor accettano args dal PLANNER LLM che a
volte sbaglia tipi/destinazioni in modi sistematici (placeholder come '0'
per cap=∞, path-like in `urls` invece di `paths`, ecc.). Invece di
PATCHARE ogni executor reactively, centralizziamo le normalizzazioni in
questo modulo, opt-in per backend/executor.

API attuale:
    normalize_paths_urls(args) -> dict
        Sposta i path-like da `urls` a `paths` (e viceversa http(s) da
        paths a urls). Idempotente, deterministico §7.9.

Razionale (turn live 2a5f2711, 25/5/2026): PLANNER ha emesso
`find_images_web(urls=["/home/user/foto.jpg"])` invece di
`paths=["/home/user/foto.jpg"]`. Confusione semantica diffusa fra i
LLM medium su nomi argoment "url" generico. Normalizer al confine
salva l'executor senza modificare la description (la description resta
prescrittiva per il PLANNER ma il backend e' forgiving §2.4).
"""
from __future__ import annotations

from typing import Any

from messages import get as _msg
from worker_policy import bounded_worker_count


# A date whose year the source never showed carries this mark: the year is the
# current one, filled in so the value stays usable, and the mark keeps it
# honest — it says the year was assumed, not read. It is a statement ABOUT the
# date, not part of it, so whoever interprets the value AS a date must look
# underneath. Without this, a filter on a period silently dropped exactly the
# rows the mark existed to save (sites omit the year on recent rows).
ASSUMED_YEAR_MARK = "*"


def date_text(value):
    """The date under an assumed-year mark, for parsing and comparison."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(ASSUMED_YEAR_MARK):
            return stripped[len(ASSUMED_YEAR_MARK):].strip()
    return value


def scalar_coordinate(value):
    """Un numero da un valore che il planner puo' aver reso plurale (§2.4).

    Il confine NL→determinismo produce sistematicamente la forma degenere:
    dove lo schema vuole uno scalare arriva `[41.8664]`. Preso alla lettera,
    un ancoraggio geografico diventa un dict con dentro due liste e il
    provider lo scarta: la ricerca «qui vicino» si allarga in silenzio al
    mondo intero (misurato su find_places, 50 risultati da tutta Italia).

    Ritorna un float, oppure None se il valore non e' una coordinata.
    """
    if isinstance(value, (list, tuple)):
        if len(value) != 1:
            return None
        value = value[0]
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if -180.0 <= number <= 180.0 else None


def walk_failure(path, reason: str) -> dict:
    """Record di un ramo che una visita ricorsiva non ha potuto leggere.

    Punto unico: ogni chiamante ricostruiva il dizionario a mano e ometteva
    il segnaposto `{path}` del template, cosi' l'utente leggeva la graffa
    grezza al posto del percorso.
    """
    denied = reason == "permission_denied"
    return {
        "path": str(path),
        "error_class": "permission_denied" if denied else "io_error",
        "error_code": (
            "ERR_PERMISSION_DENIED" if denied else "ERR_FILE_READ_FAILED"),
        "error": _msg(
            "ERR_PERMISSION_DENIED" if denied else "ERR_FILE_READ_FAILED",
            path=str(path)),
        "detail": reason,
    }


def apply_skipped_branches(out: dict, failed: list, produced) -> None:
    """Distingue «ricerca fallita» da «rami saltati» (§2.8, §2.11).

    Un solo ramo illeggibile — sotto `/tmp` bastano le directory private di
    systemd — annullava migliaia di risultati validi: la ricerca tornava
    `ok=false` e il planner ripiegava su un fratello che falliva a sua volta.
    Un risultato prodotto NON e' un fallimento: i rami non letti si
    dichiarano, non affondano la risposta. Il fallimento resta quando la
    visita non ha prodotto nulla, cioe' quando la base stessa e' illeggibile
    o inesistente.

    ``produced`` e' cio' che dimostra che l'operazione ha reso qualcosa: la
    lista delle voci per una ricerca, il numero di file esaminati per un
    confronto che puo' legittimamente non trovare nulla.
    """
    if not failed:
        return
    out["ok"] = bool(produced)
    if produced:
        out["partial"] = True
        out["skipped_branches"] = len(failed)
        out["skipped_paths"] = [record["path"] for record in failed[:10]]
    else:
        out["error"] = failed[0]["error"]


def backup_file_for_undo(path) -> str:
    """Copy a file into the canonical, content-addressed undo store.

    The caller must invoke this before a destructive write and abort that
    write if the backup fails.  Keeping the layout here prevents executors
    from drifting on history paths, hashing, or turn scoping.
    """
    import hashlib
    import os
    import re
    import shutil
    from pathlib import Path
    import config as _C

    source = Path(path)
    turn_id = os.environ.get("METNOS_TURN_ID") or "no_turn"
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", turn_id):
        turn_id = "no_turn"
    history = Path(os.environ.get("METNOS_HISTORY_DIR") or (
        _C.PATH_USER_DATA / "_history"))
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    blob = history / turn_id / "blob" / f"{digest.hexdigest()}.bin"
    blob.parent.mkdir(parents=True, exist_ok=True)
    if not blob.exists():
        shutil.copy2(source, blob)
    return str(blob)


def restore_file_from_undo_backup(path, blob_path: str | None) -> bool:
    """Best-effort immediate rollback after a failed destructive write."""
    if not blob_path:
        return False
    import shutil
    try:
        shutil.copy2(blob_path, path)
        return True
    except OSError:
        return False


def assigned_workers(*, default: int = 1, maximum: int = 32) -> int:
    """Worker budget assigned by the central executor scheduler.

    Generated executors use this instead of deriving a pool size from
    ``os.cpu_count()``.  Absence or corruption of the runtime-owned value is
    fail-closed to ``default``; callers may only reduce it with ``maximum``.
    The helper also clamps to CPUs visible in the current sandbox, so a budget
    signed by the server cannot oversubscribe a smaller remote device.
    """
    import os

    try:
        visible_cpus = max(1, int(os.cpu_count() or 1))
    except (TypeError, ValueError):
        visible_cpus = 1
    return bounded_worker_count(
        os.environ.get("METNOS_EXECUTOR_ASSIGNED_WORKERS", default),
        default=default, maximum=maximum, cpu_count=visible_cpus)


def run_stdio(invoke, *, default=None, error_extra=None,
              allow_empty=False) -> None:
    """main() standard di un executor (I/O contract subprocess, §2.1/§2.8): legge
    UN oggetto JSON da stdin, chiama `invoke(args)`, scrive UN oggetto JSON su
    stdout. Gestisce in modo UNIFORME stdin vuoto (ERR_EMPTY_INPUT) e JSON
    invalido (ERR_JSON_INVALID) — mai crash con stdout vuoto. Single source of
    truth del boilerplate `main()` copiato in ~70 executor.

    `invoke` e' chiamato SOLO dopo un parse riuscito (fuori dal try sul
    JSONDecodeError): un eventuale errore interno di `invoke` propaga come
    prima (niente mascheramento «JSON non valido» di bug applicativi).

    Parametri keyword-only per riprodurre fedelmente le varianti del main()
    copiato negli executor esistenti (zero cambi di comportamento §2.8):
      - `default`: callable passato a `json.dumps(default=...)` per serializzare
        valori non-JSON nel risultato (es. datetime/Decimal/embedding → `str`).
        Negli executor che lo usavano: `run_stdio(invoke, default=str)`.
      - `error_extra`: dict fuso nell'envelope di errore (empty/invalid) per
        preservare la shape trasformativa/lista §2.6 di quegli executor
        (es. `{"error_class": "invalid_args", "results": [], "n_created": 0}`).
      - `allow_empty`: se True, stdin vuoto → `invoke({})` invece di
        ERR_EMPTY_INPUT (executor genuinamente no-arg: get_now, get_inputs,
        get_approval, *_signatures, ...).

    Uso nel file executor:
        from executor_helpers import run_stdio
        def main():
            run_stdio(invoke)            # o (invoke, default=str), ...
        if __name__ == "__main__":
            main()
    """
    import json
    import sys
    from messages import get as _msg

    def _err(code: str) -> dict:
        out = {"ok": False, "error": _msg(code)}
        if error_extra:
            out.update(error_extra)
        return out

    raw = sys.stdin.read()
    if not raw.strip():
        result = invoke({}) if allow_empty else _err("ERR_EMPTY_INPUT")
    else:
        try:
            args = json.loads(raw)
        except json.JSONDecodeError:
            result = _err("ERR_JSON_INVALID")
        else:
            result = invoke(args)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, default=default))


def coerce_cap(args: dict, key: str, default: int, *,
               maximum: int | None = None) -> int:
    """Coercizione tollerante di un cap numerico dal PLANNER (§2.4). Ritorna un
    int valido, MAI solleva (il planner emette a volte null / stringhe / 0).

      - assente / None / non numerico → `default`
      - 0 = placeholder «nessun limite del chiamante» (§2.4) → `maximum` se dato,
        altrimenti `default`
      - clamp finale a [1, maximum]

    `default`/`maximum` sono parametri di dominio dell'executor (come i
    QUALIFIERS di vocab): non sono hardcoding, sono il contratto del tool."""
    raw = args.get(key, default)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = int(default)
    if n <= 0:
        n = maximum if maximum is not None else int(default)
    if maximum is not None:
        n = min(n, maximum)
    return max(1, n)


def format_exact_integer(value: Any) -> str:
    """Render an integer exactly with language-neutral digit grouping.

    Executor output can be rendered on the server or on a remote device where
    a full locale database is not guaranteed.  A narrow no-break space is a
    readable grouping separator across the supported languages and, unlike a
    comma or a full stop, cannot be mistaken for a decimal separator.
    """
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return str(value)
    sign = "-" if number < 0 else ""
    digits = str(abs(number))
    head = len(digits) % 3 or 3
    groups = [digits[:head]]
    groups.extend(
        digits[index:index + 3]
        for index in range(head, len(digits), 3)
    )
    return sign + "\u202f".join(groups)


def vector_result(entries: list, failed: list, *,
                  entry_key: str = "entries") -> dict:
    """Build the common envelope for independent vector operations.

    A mixed outcome remains a failed call for backward compatibility, but is
    explicitly marked partial so callers can safely consume successful items.
    """
    out = {
        "ok": not failed,
        "ok_count": len(entries),
        "fail_count": len(failed),
        entry_key: entries,
        "failed": failed,
    }
    if entries and failed:
        out["partial"] = True
    return out


def normalize_vector_result(result: dict, *,
                            entry_key: str = "entries") -> dict:
    """Normalize a backend vector envelope without dropping backend metadata.

    Backends may add pagination, account, or provider-specific fields.  This
    helper keeps them intact while enforcing the shared outcome semantics:
    any failed item makes the call unsuccessful, and a mixed outcome is
    explicitly marked ``partial``.
    """
    if not isinstance(result, dict):
        return result
    out = dict(result)
    entries = out.get(entry_key)
    failed = out.get("failed")
    entries = entries if isinstance(entries, list) else []
    failed = failed if isinstance(failed, list) else []
    out.setdefault("ok_count", len(entries))
    out.setdefault("fail_count", len(failed))
    if failed:
        out["ok"] = False
        if entries:
            out["partial"] = True
        else:
            out.pop("partial", None)
    else:
        out.pop("partial", None)
    return out


def catalog_names(catalog: Any) -> set:
    """Set dei nomi executor da un catalog le cui voci possono essere dict O
    oggetti (l'idioma era copiato ~11× in dispatch; 4 copie usavano solo
    getattr → `{None}` su voci dict). Scarta i None. Deterministico §7.9."""
    out: set = set()
    for e in (catalog or []):
        n = e.get("name") if isinstance(e, dict) else getattr(e, "name", None)
        if n:
            out.add(n)
    return out


def _is_http_url(s: Any) -> bool:
    return isinstance(s, str) and (
        s.startswith("http://") or s.startswith("https://")
    )


def _is_path_like(s: Any) -> bool:
    """True per stringhe che assomigliano a path filesystem (no schema URL).

    Detection deterministica §7.9:
      - non vuota
      - non comincia con uno schema noto (http://, https://, file://,
        ftp://, data:, mailto:)
    """
    if not isinstance(s, str) or not s:
        return False
    lower = s.lower()
    for schema in ("http://", "https://", "file://", "ftp://",
                   "data:", "mailto:", "tel:"):
        if lower.startswith(schema):
            return False
    return True


def normalize_paths_urls(args: dict) -> dict:
    """Normalizza args.paths/args.urls per executor che li distinguono.

    Regole:
      - String path-like in `urls` → spostata in `paths`.
      - String http(s)-URL in `paths` → spostata in `urls`.

    Idempotente: chiamare due volte produce lo stesso risultato. Lascia
    intatto args non-dict, args senza paths/urls, o args con valori non-list.
    Non solleva eccezioni. Non muta l'input (ritorna copia shallow).
    """
    if not isinstance(args, dict):
        return args
    paths_in = args.get("paths")
    urls_in = args.get("urls")
    # §2.4 forgiving: stringa singola → wrap in list (placeholder che
    # risolve a un solo valore non viene quasi mai dentro una list dal LLM).
    if isinstance(paths_in, str):
        paths_in = [paths_in] if paths_in else []
    if isinstance(urls_in, str):
        urls_in = [urls_in] if urls_in else []
    # Se nessuno dei due e' una lista, nulla da normalizzare.
    paths_is_list = isinstance(paths_in, list)
    urls_is_list = isinstance(urls_in, list)
    if not paths_is_list and not urls_is_list:
        return args

    new_paths: list = list(paths_in) if paths_is_list else []
    new_urls: list = list(urls_in) if urls_is_list else []

    # Sposta path-like da urls a paths.
    moved_to_paths: list = []
    keep_urls: list = []
    for u in new_urls:
        if _is_path_like(u):
            moved_to_paths.append(u)
        else:
            keep_urls.append(u)
    # Sposta http(s) da paths a urls.
    moved_to_urls: list = []
    keep_paths: list = []
    for p in new_paths:
        if _is_http_url(p):
            moved_to_urls.append(p)
        else:
            keep_paths.append(p)

    out = dict(args)
    out["paths"] = keep_paths + moved_to_paths
    out["urls"] = keep_urls + moved_to_urls
    return out


# ── Estensioni immagine (SoT condivisa, 10/7/2026) ─────────────────────────
# Nata per l'espansione dir→immagini di write_images_google_photos (§2.4:
# «carica le foto della cartella X» arriva con la DIR in paths). NB:
# `create_images_indices._IMAGE_EXTS` ha ancora una copia propria (executor
# non importabile da qui): unificare su QUESTA al prossimo tocco di quel file.
IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".heic", ".webp",
                        ".tiff", ".bmp"})
