#!/usr/bin/env python3
"""hashutil — helper di hashing condivisi (leaf, zero dipendenze runtime).

Single source of truth per lo schema `sha256:<hex>` usato dal pattern
latest-wins multilingua (ADR 0092) e dai digest di stato (synt/manifest).
"""
from __future__ import annotations

import hashlib


def sha256_prefixed(text: str | None) -> str:
    """SHA-256 hex full, prefix-encoded `sha256:<hex>`. `None` → digest di "".

    Deterministico (§7.9). Usato per version_hash/source_text_hash i18n e per
    i digest di stato lang_state. NB: forma TRONCATA (`[:16]`) e digest di FILE
    in streaming restano locali ai rispettivi call-site (shape diversa).
    """
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()
