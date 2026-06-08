"""Taglio di rilevanza ADATTIVO per retrieval con punteggi densi (§7.3).

PROBLEMA (anisotropia degli embedding densi). Le similarita' coseno fra una
query e un corpus prodotte da modelli densi (BGE-M3, SigLIP, ...) NON si
distribuiscono su [0,1]: collassano in una banda stretta ad alta media. Misura
reale su corpus immagini Metnos (31445 foto), query "persone in montagna":

    cos: μ=0.601  σ=0.043  p50=0.596  p90=0.659  p99=0.716  max=0.773
    cos>=0.40 → 99.9%    cos>=0.55 → 91.2%    cos>=0.60 → 45.7%

Una soglia ASSOLUTA (es. 0.40 o 0.55) e' percio' priva di senso: lascia
passare quasi tutto il corpus. Il segnale di rilevanza NON e' il valore
assoluto del coseno ma la sua POSIZIONE RELATIVA nella distribuzione per-query:
i match veri sono gli outlier della coda superiore (cos 0.70-0.77, ben oltre
la media-sfondo 0.60).

SOLUZIONE (regola 3-sigma). Lo sfondo (la massa non-rilevante) e' descritto da
(μ, σ) della distribuzione per-query. Un match e' rilevante se e' un outlier
statisticamente significativo sopra lo sfondo: `score >= μ + k·σ`. Con k=3
(regola dei 3 sigma, ~99.7° percentile di una normale) il taglio e' stabile e
universale: validato su 5 query eterogenee tiene lo 0.2-2.8% del corpus contro
il 99% della soglia fissa. Il knee/elbow globale NON funziona qui (la forma
plateau-dominata lo fa cadere in fondo alla distribuzione: keep=1 o keep=47%).

UNIVERSALE: nessun valore di dominio hard-coded; (μ, σ) sono per-query. k e' la
soglia di significativita' statistica, non un parametro tarato sul corpus.
Riusabile da QUALSIASI executor di retrieval scored (immagini, URL semantici,
ranking affinity): passa i punteggi grezzi, ottieni la soglia, tieni i >= soglia.
"""
from __future__ import annotations

import statistics

# Regola dei 3 sigma: un punteggio e' un match rilevante se e' un outlier
# significativo (>= μ+3σ) sopra lo sfondo per-query. Non e' un valore di
# dominio: e' la soglia statistica standard di significativita'.
RELEVANCE_SIGMA_DEFAULT = 3.0

# Sotto questo numero di candidati la statistica (μ, σ) non e' affidabile:
# il taglio adattivo si disattiva e si applica solo il pavimento assoluto.
_MIN_SAMPLE = 8


def adaptive_relevance_threshold(
    scores,
    *,
    sigma: float = RELEVANCE_SIGMA_DEFAULT,
    floor: float = 0.0,
) -> float:
    """Soglia di rilevanza adattiva per una lista di punteggi per-query.

    Args:
      scores: iterabile di punteggi grezzi (es. coseno) del corpus candidato.
      sigma:  numero di deviazioni standard sopra la media (default 3.0).
      floor:  pavimento ASSOLUTO anti-rumore. La soglia non scende mai sotto
              questo valore — protegge le query senza match reali (corpus che
              non contiene il concetto) dal restituire la coda relativa di puro
              sfondo.

    Returns:
      La soglia `t`: il chiamante tiene i candidati con `score >= t`.
      Vale sempre `t = max(floor, μ + sigma·σ)`.

    Con meno di `_MIN_SAMPLE` candidati ritorna `floor` (statistica inaffidabile).
    """
    vals = [float(s) for s in scores if s is not None]
    if len(vals) < _MIN_SAMPLE:
        return float(floor)
    mu = statistics.fmean(vals)
    sd = statistics.pstdev(vals)
    return max(float(floor), mu + float(sigma) * sd)
