# SPDX-License-Identifier: AGPL-3.0-only
"""runtime/__version__.py — sorgente UNICA della versione-prodotto Metnos.

SemVer (https://semver.org). Pre-1.0 (`0.x`): API/firme possono rompersi fra
minor (§7.1 dev pre-1.0). Distinta dalla VERSIONE dell'HTTP API (ADR 0078,
`http_routes_agent.VERSION`) e dalla versione del contratto AI-backend
(`runtime.ai_backend.AI_BACKEND_API`).

Sorgente di verità del versioning (vedi ADR versioning): bump qui → riflesso in
`/agent/health`, `/.well-known/metnos.json`, CLI. Release = tag `vX.Y.Z` su
`main` (truth source = ambiente in esercizio).
"""
from __future__ import annotations

__version__ = "0.1.0"

# Versione del CONTRATTO dello shim AI-backend (runtime/ai_backend). Un backend
# dichiara la compatibilità con questo numero → release Metnos disaccoppiate
# dalle implementazioni di backend.
AI_BACKEND_API = 1


def version_info() -> dict:
    """Dict versioni per /agent/health, /.well-known, CLI."""
    return {
        "metnos_version": __version__,
        "ai_backend_api": AI_BACKEND_API,
    }
