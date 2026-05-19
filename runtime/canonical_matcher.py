# SPDX-License-Identifier: AGPL-3.0-only
"""canonical_matcher.py — Layer L1 BGE matcher per fast-path single-tool.

ADR 0149 step 2c (18/5/2026). Riusa BGE-M3 gia' caricato per
`affinity_semantic`. Cross-lingua, deterministico, threshold conservativo.

Architettura:

  query → BGE-M3 encode → cosine vs canonical_query_log entries
        (filtrate per uses >= MIN_USES e state in {candidate, active})
        top-1 cosine >= THRESHOLD → match → return {executor, args, render}

  Miss / sotto soglia → caller (`fast_path.try_fast_path`) ritorna None
  e l'agent_runtime fa fallback al planner LLM. No harm.

V1 caveats:
- args extraction da query NON implementata. Il matcher invoca executor
  con `args={}`. Per executor con required args (path, url, ...) cio'
  causa fail; il runtime cadra' al planner come prima (no harm). V2
  estendera' con regex extraction per placeholder type (<PATH>, <URL>,
  <INT>, ...) parsato da args_shape.
- Cache invalidation by hash of (id, uses) — re-encode tutto al primo
  miss del cache. Costo: ~25ms per entry, lineare in N.

Determinismo §7.9: niente LLM. Solo encoder ONNX + cosine.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

import numpy as np

_LOG = logging.getLogger(__name__)

# Soglia cosine. Conservative: 0.95 per evitare false positivi.
# Override via env per bench/test.
DEFAULT_THRESHOLD = float(os.environ.get("METNOS_CQ_THRESHOLD", "0.95"))
# Numero minimo di uses prima che un entry diventi candidate per il match.
# Default 3 (Roberto 19/5/2026 v4): bilanciamento fra "promozione rapida"
# e "stabilita' del pattern". 1 use = potenziale rumore; 3 use = pattern
# consolidato. Override via env per test/A-B.
DEFAULT_MIN_USES = int(os.environ.get("METNOS_CQ_MIN_USES", "3"))

# Stati accettati dal matcher (esclude `demoted`).
_ACTIVE_STATES = ("candidate", "active", "shadow")


class CanonicalMatcher:
    """Singleton thread-safe per match canonical_query → executor.

    Carica entries da `mnestoma.canonical_query_log`, le encoda con BGE-M3,
    espone `try_match(query)` con cosine top-1.
    """

    _INSTANCE: Optional["CanonicalMatcher"] = None
    _INSTANCE_LOCK = threading.Lock()

    def __init__(self) -> None:
        self._embedder = None  # BGEEmbeddingService o False (failed)
        self._entries: list[dict] = []
        self._vectors: Optional[np.ndarray] = None  # (N, D) L2-normalized
        self._entries_sig: str = ""  # hash invalidation
        self._lock = threading.Lock()

    # ---------------------------------------------------------------------
    @classmethod
    def get(cls) -> "CanonicalMatcher":
        if cls._INSTANCE is not None:
            return cls._INSTANCE
        with cls._INSTANCE_LOCK:
            if cls._INSTANCE is None:
                cls._INSTANCE = cls()
        return cls._INSTANCE

    # ---------------------------------------------------------------------
    def _get_embedder(self):
        if self._embedder is not None:
            return self._embedder if self._embedder is not False else None
        try:
            from bge_embedding import BGEEmbeddingService
            self._embedder = BGEEmbeddingService()
        except Exception as ex:
            _LOG.info("canonical_matcher: BGE non disponibile (%r); "
                      "matcher disattivo", ex)
            self._embedder = False
            return None
        return self._embedder

    # ---------------------------------------------------------------------
    def _load_entries(self, min_uses: int) -> list[dict]:
        """Legge canonical_query_log da mnestoma. Solo entries con
        args_shape vuoto ({}) e uses >= min_uses, state attivo.

        Ritorna lista di dict `{id, canonical, tool, uses}`. Niente
        re-encode in questo metodo: lo fa _refresh_if_stale.
        """
        try:
            from mnestoma import Mnestoma
            m = Mnestoma()
            states_csv = ",".join(f"'{s}'" for s in _ACTIVE_STATES)
            rows = m.conn.execute(
                f"""SELECT id, canonical_query, tool_name, args_shape, uses
                    FROM canonical_query_log
                    WHERE uses >= ?
                      AND state IN ({states_csv})
                    ORDER BY id""",
                (min_uses,),
            ).fetchall()
        except Exception as ex:
            _LOG.warning("canonical_matcher: lettura DB fallita: %r", ex)
            return []
        out = []
        for r in rows:
            out.append({
                "id": r["id"],
                "canonical": r["canonical_query"],
                "tool": r["tool_name"],
                "uses": r["uses"],
                "args_shape": r["args_shape"],
            })
        return out

    @staticmethod
    def _sig(entries: list[dict]) -> str:
        # Hash compatto di (id, uses) coppie ordinate. Invalida la cache
        # quando un entry e' aggiornato (uses cambiato) o aggiunto/rimosso.
        import hashlib
        h = hashlib.sha256()
        for e in entries:
            h.update(f"{e['id']}:{e['uses']}\n".encode("utf-8"))
        return h.hexdigest()[:16]

    # ---------------------------------------------------------------------
    def _refresh_if_stale(self, min_uses: int) -> bool:
        """Re-encode entries se cambiate. Ritorna True se ci sono entries
        disponibili dopo il refresh, False se vuoto o BGE non disponibile.
        """
        entries = self._load_entries(min_uses)
        sig = self._sig(entries)
        if sig == self._entries_sig and self._vectors is not None:
            return len(self._entries) > 0
        if not entries:
            self._entries = []
            self._vectors = None
            self._entries_sig = sig
            return False
        emb = self._get_embedder()
        if emb is None:
            return False
        try:
            vectors = emb.embed_texts([e["canonical"] for e in entries])
            # BGEEmbeddingService.embed_texts ritorna ndarray L2-normalized
            if not isinstance(vectors, np.ndarray):
                vectors = np.asarray(vectors, dtype=np.float32)
        except Exception as ex:
            _LOG.warning("canonical_matcher: encode entries fallito: %r", ex)
            self._entries = []
            self._vectors = None
            self._entries_sig = sig
            return False
        self._entries = entries
        self._vectors = vectors
        self._entries_sig = sig
        return True

    # ---------------------------------------------------------------------
    def try_match(self, query: str, *,
                  threshold: float = DEFAULT_THRESHOLD,
                  min_uses: int = DEFAULT_MIN_USES) -> Optional[dict]:
        """Match query → executor via BGE cosine.

        Returns:
          None se nessun match sopra soglia.
          dict {executor, args, render, pattern, cosine} se hit.
        """
        if not query or not query.strip():
            return None
        with self._lock:
            if not self._refresh_if_stale(min_uses):
                return None
            emb = self._get_embedder()
            if emb is None:
                return None
            try:
                qv = emb.embed_query(query)
                if not isinstance(qv, np.ndarray):
                    qv = np.asarray(qv, dtype=np.float32)
            except Exception as ex:
                _LOG.warning("canonical_matcher: encode query fallito: %r", ex)
                return None
            # Cosine: BGE returns L2-normalized → dot product = cosine.
            scores = self._vectors @ qv  # (N,)
            idx = int(np.argmax(scores))
            top = float(scores[idx])
            if top < threshold:
                return None
            entry = self._entries[idx]
        # Build render: generic — usa summary/message dell'observation o
        # fallback "tool: completed (N elementi)".
        tool = entry["tool"]
        canonical = entry["canonical"]
        cosine_val = top

        def _render(obs: dict) -> str:
            if not isinstance(obs, dict):
                return f"{tool}: completato"
            summary = obs.get("summary") or obs.get("message")
            if isinstance(summary, str) and summary.strip():
                return summary.strip()
            n_entries = len(obs.get("entries") or [])
            if n_entries:
                return f"{tool}: completato ({n_entries} elementi)"
            return f"{tool}: completato"

        # V2 19/5/2026: args extraction hybrid via args_extractor (regex
        # deterministico + memoization da args_shape del log). LLM fallback
        # opt-in tramite METNOS_CQ_ARGS_LLM=1 (default off).
        args: dict = {}
        try:
            from args_extractor import extract_args
            # Lookup schema del tool dal catalogo (lazy).
            schema = None
            try:
                from loader import load_catalog
                cat = load_catalog()
                ex = cat.executors.get(tool)
                if ex is not None:
                    schema = getattr(ex, "args_schema", None)
            except Exception:
                pass
            # args_shape persisted nel log (V2 carryover): non e' un valore
            # vero, e' una signature shape. La extract_args la accetta come
            # `observed_args` solo se contiene valori (oggi shape dict ha
            # solo key=type pairs, ignored). Future: nuova colonna
            # args_observed JSON con valori reali.
            args = extract_args(
                query, tool, schema,
                observed_args=None,
                llm_fallback=False,
            )
        except Exception as _ex:
            _LOG.debug("args_extractor failed for %s: %r", tool, _ex)
            args = {}
        return {
            "executor": tool,
            "args": args,
            "render": _render,
            "pattern": f"bge_match:{canonical}",
            "cosine": cosine_val,
        }


def try_canonical_match(query: str, *,
                          threshold: float = DEFAULT_THRESHOLD,
                          min_uses: int = DEFAULT_MIN_USES
                          ) -> Optional[dict]:
    """Helper module-level (mirror di fast_path.try_fast_path)."""
    return CanonicalMatcher.get().try_match(
        query, threshold=threshold, min_uses=min_uses
    )
