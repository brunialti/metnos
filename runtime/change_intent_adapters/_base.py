"""change_intent_adapters._base — helper comuni per gli adapter.

Score normalization: ogni family ha scale diversa (uses int, ea 0-1,
n_seen int, ...). Mappiamo tutto in [0, 1] per cross-source comparison.
"""
from __future__ import annotations

import math
import time


def _iso_from_ts(ts: float) -> str:
    """Float epoch → ISO 8601 UTC."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def score_from_uses(uses: int, *, mid: int = 20) -> float:
    """Normalizza uses → [0, 1] via funzione log-saturante.
    Punto medio = score 0.5 (default uses=20).
    Range tipico: uses=1 → 0.05, uses=5 → 0.20, uses=20 → 0.50,
                 uses=50 → 0.71, uses=100 → 0.83, uses=500 → 0.95.
    """
    if uses <= 0:
        return 0.0
    return 1.0 - math.exp(-math.log(2.0) * uses / mid)


def score_from_n_seen(n_seen: int, last_uses: int) -> float:
    """Introvertiva: combina n_seen (osservazioni distinte) e last_uses
    (frequenza recente). Mantiene la formula storica `n_seen*0.05 +
    last_uses*0.005` capped a 1.0 per coerenza con UI esistente."""
    return min(1.0, n_seen * 0.05 + last_uses * 0.005)


def score_from_ea(ea: float | None) -> float:
    """Telos expected_alignment v1.3 e' gia' in [0, 1]. None → 0."""
    if ea is None:
        return 0.0
    try:
        v = float(ea)
    except (ValueError, TypeError):
        return 0.0
    if v < 0:
        return 0.0
    if v > 1:
        return 1.0
    return v


def score_from_synt_state(final_state: str | None) -> float:
    """Synt proposals: installed=0.8 (alta utilita' empirica),
    abandoned=0.3 (parziale), rejected*=0.05 (per audit, non azione)."""
    if not final_state:
        return 0.0
    s = final_state.lower()
    if s == "installed":
        return 0.8
    if s.startswith("abandoned"):
        return 0.3
    if s.startswith("rejected"):
        return 0.05
    return 0.1


def score_for_reject_pattern(n_rejections: int) -> float:
    """User reject feedback: piu' rifiuti uguali → piu' urgente di
    bannare pattern (ma comunque cap a 0.9, no auto-action)."""
    if n_rejections <= 0:
        return 0.0
    return min(0.9, 0.3 + 0.15 * n_rejections)


__all__ = [
    "_iso_from_ts",
    "score_from_uses",
    "score_from_n_seen",
    "score_from_ea",
    "score_from_synt_state",
    "score_for_reject_pattern",
]
