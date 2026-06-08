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
