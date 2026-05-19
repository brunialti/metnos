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

# Soglia cosine / min_uses: Fase 12 19/5/2026 v5 → letti via runtime_settings
# (env override + persistent ~/.config/metnos/runtime.toml + default).
# I module-level constants restano per back-compat ma sono ridotti a
# fallback statici; chi vuole il valore corrente chiama
# `_current_threshold()` / `_current_min_uses()`.
DEFAULT_THRESHOLD = float(os.environ.get("METNOS_CQ_THRESHOLD", "0.95"))
DEFAULT_MIN_USES = int(os.environ.get("METNOS_CQ_MIN_USES", "3"))


def _current_threshold() -> float:
    try:
        from runtime_settings import canonical_query_threshold
        return canonical_query_threshold()
    except Exception:
        return DEFAULT_THRESHOLD


def _current_min_uses() -> int:
    try:
        from runtime_settings import canonical_query_min_uses
        return canonical_query_min_uses()
    except Exception:
        return DEFAULT_MIN_USES

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
            # Parameterized IN (?, ?, ...) — niente f-string in SQL (§7.3).
            _placeholders = ",".join("?" * len(_ACTIVE_STATES))
            _sql = (
                "SELECT id, canonical_query, tool_name, args_shape, uses, "
                "args_observed FROM canonical_query_log "
                "WHERE uses >= ? AND state IN (" + _placeholders + ") "
                "ORDER BY id"
            )
            rows = m.conn.execute(
                _sql, (min_uses, *_ACTIVE_STATES),
            ).fetchall()
        except Exception as ex:
            _LOG.warning("canonical_matcher: lettura DB fallita: %r", ex)
            return []
        out = []
        import json as _json
        for r in rows:
            # args_observed: parse JSON. NULL → None.
            obs_raw = r["args_observed"] if "args_observed" in r.keys() else None
            obs_parsed = None
            if obs_raw:
                try:
                    obs_parsed = _json.loads(obs_raw)
                except (ValueError, TypeError):
                    obs_parsed = None
            out.append({
                "id": r["id"],
                "canonical": r["canonical_query"],
                "tool": r["tool_name"],
                "uses": r["uses"],
                "args_shape": r["args_shape"],
                "args_observed": obs_parsed,
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
                  threshold: float | None = None,
                  min_uses: int | None = None) -> Optional[dict]:
        """Match query → executor via BGE cosine.

        Returns:
          None se nessun match sopra soglia.
          dict {executor, args, render, pattern, cosine} se hit.
        """
        if not query or not query.strip():
            return None
        # Fallback ai default correnti (env + runtime.toml + fallback) se
        # non specificati dal caller. Lazy per honorare runtime.toml reload.
        if threshold is None:
            threshold = _current_threshold()
        if min_uses is None:
            min_uses = _current_min_uses()
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

        # V1.5 19/5/2026 v5: args extraction hybrid via args_extractor (regex
        # deterministico + memoization da `args_observed` del log + LLM
        # fallback opt-in). args_observed contiene i VALORI args reali
        # osservati al primo planner pass (Fase 14 v5, separato da args_shape
        # che e' solo il template placeholder).
        args: dict = {}
        observed = entry.get("args_observed")
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
            args = extract_args(
                query, tool, schema,
                observed_args=observed if isinstance(observed, dict) else None,
                llm_fallback=False,  # env METNOS_CQ_ARGS_LLM=1 sblocca.
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
                          threshold: float | None = None,
                          min_uses: int | None = None
                          ) -> Optional[dict]:
    """Helper module-level (mirror di fast_path.try_fast_path).

    Default `None` → CanonicalMatcher.try_match risolve via runtime_settings.
    """
    return CanonicalMatcher.get().try_match(
        query, threshold=threshold, min_uses=min_uses
    )
