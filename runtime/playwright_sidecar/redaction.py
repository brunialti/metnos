# SPDX-License-Identifier: AGPL-3.0-only
"""redaction — oscuramento deterministico dei campi segreti PRIMA di ogni
capture (spec sites §3.2 CRITICO-3b, §3.3).

Il presidio #1 («l'LLM non vede il segreto») fallisce per una via laterale se
uno screenshot cattura un campo password/OTP in chiaro e quello screenshot va
al VLM o all'utente. Quindi: PRIMA di OGNI `page.screenshot`, sovrapponiamo un
overlay nero opaco su:
    (1) ogni `input[type=password]` (sempre, deterministico);
    (2) ogni elemento marcato `data-metnos-redact="1"` dal broker quando vi
        ha digitato una credenziale/OTP (§3.2 CRITICO-2/3).

L'overlay è un div `position:fixed` nero disegnato sul bounding-rect di ciascun
target: garantisce che i PIXEL siano neri a prescindere da come il browser
renderizza l'input (difesa in profondità vs `-webkit-text-security`). Nessun
segreto è mai leggibile nell'immagine. §7.9 deterministico: nessun LLM.
"""
from __future__ import annotations

# JS iniettato: rimuove eventuali overlay precedenti (idempotente) e ne crea di
# nuovi sopra i target. Marca gli overlay con una classe nota per la pulizia.
_REDACT_JS = r"""
() => {
  const MARK = 'metnos-redact-overlay';
  // Pulisci overlay precedenti (idempotenza: la funzione può girare più volte).
  document.querySelectorAll('.' + MARK).forEach(e => e.remove());
  const targets = Array.from(document.querySelectorAll(
    'input[type=password], [data-metnos-redact="1"]'));
  let n = 0;
  for (const el of targets) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;  // non visibile
    const box = document.createElement('div');
    box.className = MARK;
    box.style.position = 'fixed';
    box.style.left = Math.max(0, r.left) + 'px';
    box.style.top = Math.max(0, r.top) + 'px';
    box.style.width = r.width + 'px';
    box.style.height = r.height + 'px';
    box.style.background = '#000';
    box.style.zIndex = '2147483647';
    box.style.pointerEvents = 'none';
    box.style.borderRadius = getComputedStyle(el).borderRadius || '0';
    document.body.appendChild(box);
    n++;
  }
  return n;
}
"""


async def apply_redaction(page) -> int:
    """Sovrappone overlay neri opachi sui campi segreti della pagina. Ritorna
    il numero di campi redatti. Idempotente (rimuove overlay precedenti prima
    di ricrearli). Fail-safe: su errore ritorna -1 e il chiamante DEVE trattare
    il capture come non-sicuro (spec §2.8: mai catturare se la redazione fallisce)."""
    try:
        return int(await page.evaluate(_REDACT_JS))
    except Exception:
        return -1
