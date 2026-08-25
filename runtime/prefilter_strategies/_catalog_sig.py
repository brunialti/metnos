# SPDX-License-Identifier: AGPL-3.0-only
"""Common RM-0008 identity for every catalog-dependent prefilter cache."""
from __future__ import annotations

def catalog_signature(catalog_list) -> str:
    from executor_catalog_identity import catalog_identity
    return catalog_identity(catalog_list)
