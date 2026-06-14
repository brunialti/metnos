# SPDX-License-Identifier: AGPL-3.0-only
"""Firma del catalogo per invalidare le cache per-strategy quando il set di
executor cambia. Sorgente UNICA (era duplicata byte-identica in bloom +
cached_token_flat). Determinismo §7.9: sha256 dei nomi ORDINATI, 16 hex.
"""
from __future__ import annotations

import hashlib


def catalog_signature(catalog_list) -> str:
    h = hashlib.sha256()
    for e in sorted(catalog_list, key=lambda x: getattr(x, "name", "")):
        h.update((getattr(e, "name", "") or "").encode())
    return h.hexdigest()[:16]
