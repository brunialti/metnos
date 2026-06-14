# SPDX-License-Identifier: AGPL-3.0-only
"""telos_loader.py — parse `workspace/TELOS.md` e espone i telos correnti.

ADR pending (riferimento: docs/it/drafts/telos_engine_v1.html, 20/5/2026).

Vincoli architetturali (vedi dialogo platonico Giornata I-IV + microarch
telos.html v1.1):

- TELOS.md vive in workspace, scritto dall'utente.
- Numero telos: 3-7 (soft limit, normalizzazione pesi se la somma != 1.0).
- Telos id formato `t.<slug>`. Campo phrase = riga `##` (titolo header).
- Campi obbligatori per telos: peso (float [0,1]), soglia_attivazione
  (float [0,1]), note (testo multilinea).
- Hot-reload: cache process-life con mtime invalidation.

API pubblica:
    current() -> list[Telos]
    version() -> str           # hash sha256[:16] del file (per cache key)
    by_id(telos_id) -> Telos | None
    declared_weight_sum() -> float
    reload() -> int            # forza re-parse, ritorna # telos parsed

§7.9 deterministico: nessun LLM, regex puro.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_LOG = logging.getLogger(__name__)

# Path canonical da config.py (ADR 0148 rename-resilient).
import config as _C  # §7.11 — SoT canonical
DEFAULT_TELOS_PATH = _C.PATH_WORKSPACE / "TELOS.md"

_HEADER_RE = re.compile(r"^##\s+(t\.\S+)\s*[—-]\s*(.+?)\s*$", re.MULTILINE)
_FIELD_RE = re.compile(
    r"^(peso|soglia_attivazione)\s*:\s*([0-9.]+)\s*$", re.MULTILINE,
)


@dataclass(frozen=True)
class Telos:
    """Un fine ultimo dichiarato dall'utente."""
    id: str                         # "t.tempo"
    phrase: str                     # "Liberare il mio tempo..."
    weight: float                   # [0,1], normalized
    activation_threshold: float     # [0,1]
    notes: str                      # context for LLM judge

    @property
    def is_non_retreat(self) -> bool:
        """Eccezione del dialogo: t.coltivazione_strumenti ha semantica
        speciale (applicabile a request-time via synt cascade, non solo
        a proposte spontanee). Vedi telos.html §5."""
        return self.id == "t.coltivazione_strumenti"


_CACHE_LOCK = threading.Lock()
_CACHE: dict = {"telos": None, "mtime": 0.0, "version": ""}


def _parse(text: str) -> list[Telos]:
    """Parser deterministico del markdown TELOS.md.

    Estrae header `## t.<slug> — <phrase>` + campi peso/soglia + note
    (testo fra header e prossimo header / EOF, dopo i campi).
    """
    telos: list[Telos] = []
    headers = list(_HEADER_RE.finditer(text))
    for i, m in enumerate(headers):
        tid = m.group(1).strip()
        phrase = m.group(2).strip()
        # Block: dal carattere dopo l'header al prossimo header (o EOF)
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        block = text[start:end]
        fields = {k: float(v) for k, v in _FIELD_RE.findall(block)}
        weight = fields.get("peso", 0.0)
        threshold = fields.get("soglia_attivazione", 0.0)
        # Note: ciò che resta dopo aver tolto i campi peso/soglia (e i
        # comment lines). Conservativo: linee non-vuote, non field, non
        # solo whitespace.
        note_lines: list[str] = []
        for line in block.splitlines():
            ls = line.strip()
            if not ls:
                continue
            if _FIELD_RE.match(line):
                continue
            if ls.startswith("note:"):
                note_lines.append(ls[len("note:"):].strip())
            elif note_lines:
                # continuazione note multilinea
                note_lines.append(ls)
        notes = " ".join(note_lines).strip()
        if weight > 0:
            telos.append(Telos(
                id=tid, phrase=phrase, weight=weight,
                activation_threshold=threshold, notes=notes,
            ))
    # Normalizzazione pesi a somma 1.0 (§3 telos.html, post-parsing).
    total = sum(t.weight for t in telos)
    if total > 0 and abs(total - 1.0) > 0.01:
        norm = 1.0 / total
        telos = [
            Telos(t.id, t.phrase, t.weight * norm,
                  t.activation_threshold, t.notes)
            for t in telos
        ]
        _LOG.info("telos_loader: pesi normalizzati (somma %.3f -> 1.0)", total)
    return telos


def _version_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def reload(path: Optional[Path] = None) -> int:
    """Forza re-parse. Ritorna numero di telos parsed."""
    p = path or DEFAULT_TELOS_PATH
    if not p.is_file():
        _LOG.warning("telos_loader: TELOS.md non trovato in %s", p)
        with _CACHE_LOCK:
            _CACHE["telos"] = []
            _CACHE["mtime"] = 0.0
            _CACHE["version"] = ""
        return 0
    text = p.read_text(encoding="utf-8")
    parsed = _parse(text)
    with _CACHE_LOCK:
        _CACHE["telos"] = parsed
        _CACHE["mtime"] = p.stat().st_mtime
        _CACHE["version"] = _version_hash(text)
    _LOG.info("telos_loader: caricati %d telos da %s (v=%s)",
              len(parsed), p, _CACHE["version"])
    return len(parsed)


def _maybe_reload(path: Optional[Path] = None) -> None:
    """Invalida cache su mtime change. Idempotente, thread-safe."""
    p = path or DEFAULT_TELOS_PATH
    try:
        mt = p.stat().st_mtime
    except OSError:
        # File mancante: cache vuota se non gia' tale
        if _CACHE["telos"] is None:
            reload(p)
        return
    if _CACHE["telos"] is None or mt > _CACHE["mtime"]:
        reload(p)


def current(path: Optional[Path] = None) -> list[Telos]:
    """Ritorna lista corrente di telos, con hot-reload trasparente."""
    _maybe_reload(path)
    return list(_CACHE["telos"] or [])


def version(path: Optional[Path] = None) -> str:
    """Hash della versione corrente (16 hex). Usato come cache key in
    LLM call dipendenti (es. alignment_engine), per invalidare cache
    quando TELOS.md cambia."""
    _maybe_reload(path)
    return _CACHE["version"]


def by_id(telos_id: str, path: Optional[Path] = None) -> Optional[Telos]:
    """Lookup per id (`t.slug`)."""
    for t in current(path):
        if t.id == telos_id:
            return t
    return None


def declared_weight_sum(path: Optional[Path] = None) -> float:
    """Somma dei pesi correnti (dopo normalizzazione: ~1.0)."""
    return sum(t.weight for t in current(path))


# Fase 4 (22/5/2026): rendering del blocco TELOS per system prompt PLANNER.
# I telos sono dati utente (frase in lingua scelta dall'utente). Il rendering
# e' lingua-agnostico per la SOSTANZA (phrase utente) e tradotto per le
# istruzioni §6 (DEVI/NON DEVI). Degrade graceful: se TELOS.md mancante o
# vuoto, ritorna stringa vuota — il PLANNER si comporta come oggi (no telos).

_TELOS_HEADERS = {
    "it": "TELOS DELL'UTENTE (fini ultimi dichiarati in workspace/TELOS.md)",
    "en": "USER TELOS (ultimate ends declared in workspace/TELOS.md)",
}

_TELOS_RULES = {
    "it": (
        "DEVI: tenere conto di questi telos quando esistono piu' strategie con esito simile; preferisci quella che li serve.\n"
        "NON DEVI: trattare i telos come hard-constraint — non bloccano richieste esplicite dell'utente, sono segnale soft.\n"
        "OK: utente chiede 'riassumi le mail' → preferisci pipeline veloce (t.tempo, t.parsimonia) se la qualita' regge.\n"
        "ERRORE: rifiutare un'azione perche' 'viola t.discrezione' — i telos pesano le proposte spontanee, non le richieste esplicite."
    ),
    "en": (
        "MUST: account for these telos when multiple strategies yield similar outcomes; prefer the one that serves them.\n"
        "MUST NOT: treat telos as hard-constraints — they don't block explicit user requests, they're a soft signal.\n"
        "OK: user asks 'summarize the mail' → prefer fast pipeline (t.tempo, t.parsimonia) if quality holds.\n"
        "ERROR: refusing an action because it 'violates t.discrezione' — telos weight spontaneous proposals, not explicit requests."
    ),
}


def render_planner_block(lang: str = "it", path: Optional[Path] = None) -> str:
    """Render del blocco TELOS per il system prompt del PLANNER.

    Pattern §6 (the design guide): header separato da `═`, righe `t.<id> (peso X): <phrase>`,
    quartetto DEVI/NON DEVI/OK/ERRORE in lingua. Lingua dei telos = lingua
    in cui l'utente ha scritto TELOS.md (no traduzione). Lingua istruzioni
    = `lang` (it/en supportati; altri lang → fallback 'en').

    Ritorna stringa vuota se TELOS.md mancante o nessun telos parsed:
    degrade graceful, il PLANNER si comporta come pre-fase-4.
    """
    telos_list = current(path)
    if not telos_list:
        return ""
    lang_key = lang if lang in _TELOS_HEADERS else "en"
    bar = "═" * 70
    lines = [bar, _TELOS_HEADERS[lang_key], bar, ""]
    # Ordina per peso decrescente (segnale di priorita' al PLANNER).
    for t in sorted(telos_list, key=lambda x: x.weight, reverse=True):
        lines.append(f"- {t.id} (peso {t.weight:.2f}): {t.phrase}")
    lines.append("")
    lines.append(_TELOS_RULES[lang_key])
    return "\n".join(lines)


# CLI minimo per inspection manuale.
if __name__ == "__main__":
    import json
    import sys
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TELOS_PATH
    n = reload(p)
    print(f"# Loaded {n} telos from {p} (v={version(p)})")
    for t in current(p):
        print(json.dumps({
            "id": t.id, "phrase": t.phrase, "weight": round(t.weight, 4),
            "threshold": t.activation_threshold,
            "notes_len": len(t.notes),
        }, ensure_ascii=False))
