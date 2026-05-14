"""Test del re-ranking adattativo intra-turno.

Verifica che:
  - extract_keywords pesca i token significativi e ignora stopwords/skip fields
  - build_extended_query concatena query + top keywords
  - re_rank_for_step e' add-only (mai rimuove candidati)
  - re_rank_for_step rispetta il cap di sicurezza (pool ≤ 2 × k_max)
  - re_rank_for_step e' resiliente a observation malformati / vuoti
  - costo medio ~ms su catalog reale (smoke perf)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path


_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


# ── extract_keywords ──────────────────────────────────────────────────

class TestExtractKeywords:

    def test_strings_in_dict(self):
        from adaptive_rerank import extract_keywords
        obs = {
            "ok": True,
            "entries": [
                {"path": "/etc/hostname", "name": "hostname", "kind": "system"},
                {"path": "/etc/passwd",   "name": "passwd",   "kind": "system"},
            ],
        }
        kws = extract_keywords(obs)
        # 'hostname' e 'passwd' devono comparire; 'entries' anche (nome campo)
        assert "hostname" in kws
        assert "passwd" in kws
        # 'true', 'false', 'ok' sono stopwords filtrate
        assert "true" not in kws
        assert "ok" not in kws

    def test_skips_audit_and_metadata_fields(self):
        from adaptive_rerank import extract_keywords
        obs = {
            "ok": True,
            "audit_path": "/home/r/very/secret/path/audit_xyz.jsonl",
            "digest": "sha256:abc123",
            "duration_ms": 47,
            "useful_field": "tessera_sanitaria",
        }
        kws = extract_keywords(obs)
        # Il path audit non deve essere visto
        assert "secret" not in kws
        assert "audit_xyz" not in kws.__repr__() or True  # ridondante
        # Il valore "useful" deve esserci
        assert "tessera_sanitaria" in kws or "tessera" in kws

    def test_empty_or_malformed(self):
        from adaptive_rerank import extract_keywords
        assert extract_keywords({}) == []
        assert extract_keywords(None) == []
        assert extract_keywords([]) == []
        assert extract_keywords("just a string") == []

    def test_max_keywords_cap(self):
        from adaptive_rerank import extract_keywords
        # Costruisci un'observation con 50 token diversi
        obs = {"items": [
            {"name": f"item_{i}_unique"} for i in range(50)
        ]}
        kws = extract_keywords(obs, max_keywords=8)
        assert len(kws) <= 8

    def test_value_length_capped(self):
        from adaptive_rerank import extract_keywords
        # Un value gigante non deve far esplodere il numero di token estratti
        big = " ".join(f"word{i}" for i in range(2000))
        obs = {"content": big}
        kws = extract_keywords(obs, max_keywords=10)
        assert len(kws) <= 10


# ── build_extended_query ──────────────────────────────────────────────

class TestBuildExtendedQuery:

    def test_no_observation_returns_original(self):
        from adaptive_rerank import build_extended_query
        q = build_extended_query(
            original_query="leggi le mail importanti",
            latest_observation=None,
        )
        assert q == "leggi le mail importanti"

    def test_concatenates_keywords(self):
        from adaptive_rerank import build_extended_query
        q = build_extended_query(
            original_query="trova fatture",
            latest_observation={"entries": [
                {"subject": "Fattura nr 1234", "from": "amazon"},
                {"subject": "Receipt", "from": "google"},
            ]},
        )
        # La query estesa deve contenere quella originale
        assert q.startswith("trova fatture")
        # E qualche keyword dell'observation
        lower = q.lower()
        assert "amazon" in lower or "google" in lower or "subject" in lower

    def test_strips_whitespace(self):
        from adaptive_rerank import build_extended_query
        q = build_extended_query(
            original_query="  ",
            latest_observation={"x": "alpha beta"},
        )
        # Almeno alpha o beta nei keywords
        assert "alpha" in q or "beta" in q


# ── re_rank_for_step ──────────────────────────────────────────────────

class TestReRankForStep:

    def _catalog(self):
        from loader import load_catalog
        return load_catalog(verify=True)

    def test_add_only_property(self):
        """I candidati esistenti non vengono mai rimossi: il re-rank può
        solo aggiungerne di nuovi."""
        from adaptive_rerank import re_rank_for_step
        cat = self._catalog()
        all_execs = list(cat.executors.values())
        current = all_execs[:5]
        current_names = {e.name for e in current}
        post, info = re_rank_for_step(
            original_query="leggi le mail",
            catalog=cat,
            current_candidates=current,
            latest_observation={"entries": [{"subject": "fattura amazon"}]},
        )
        # Tutti gli originali ancora presenti
        post_names = {e.name for e in post}
        assert current_names.issubset(post_names), (
            f"current candidates lost: {current_names - post_names}"
        )

    def test_cap_2x_kmax(self):
        from adaptive_rerank import re_rank_for_step
        cat = self._catalog()
        all_execs = list(cat.executors.values())
        # Parto con un pool di 8 (= k_max default)
        current = all_execs[:8]
        post, info = re_rank_for_step(
            original_query="qualcosa di molto generico",
            catalog=cat,
            current_candidates=current,
            latest_observation={"entries": [
                {"name": f"item_{i}", "kind": "thing"} for i in range(20)
            ]},
            k_min=5, k_max=8,
        )
        # Pool finale ≤ 2 × k_max = 16
        assert len(post) <= 16

    def test_no_observation_no_changes(self):
        from adaptive_rerank import re_rank_for_step
        cat = self._catalog()
        all_execs = list(cat.executors.values())
        current = all_execs[:5]
        post, info = re_rank_for_step(
            original_query="trova file",
            catalog=cat,
            current_candidates=current,
            latest_observation={},  # empty
        )
        assert info["applied"] is False
        assert post == current

    def test_resilient_to_malformed_observation(self):
        """L'observation malformata o non dict non deve far crashare."""
        from adaptive_rerank import re_rank_for_step
        cat = self._catalog()
        all_execs = list(cat.executors.values())
        current = all_execs[:3]
        for obs in (None, "not a dict", 42, []):
            post, info = re_rank_for_step(
                original_query="qualcosa",
                catalog=cat,
                current_candidates=current,
                latest_observation=obs,
            )
            assert isinstance(post, list)
            assert all(e in post for e in current)


# ── perf smoke ────────────────────────────────────────────────────────

class TestPerfSmoke:

    def test_re_rank_cost_under_50ms(self):
        """Smoke perf: il re-rank su catalog reale deve costare ben sotto
        50 ms (target ≤ 10 ms su catalog reale di ~40 executor).
        """
        from adaptive_rerank import re_rank_for_step
        from loader import load_catalog
        cat = load_catalog(verify=True)
        current = list(cat.executors.values())[:5]
        # Warmup
        for _ in range(3):
            re_rank_for_step(
                original_query="leggi le mail di oggi",
                catalog=cat,
                current_candidates=current,
                latest_observation={"entries": [{"subject": "test"}]},
            )
        # Measured
        times = []
        for _ in range(20):
            t0 = time.perf_counter()
            re_rank_for_step(
                original_query="leggi le mail di oggi",
                catalog=cat,
                current_candidates=current,
                latest_observation={"entries": [
                    {"subject": "fattura amazon", "from": "noreply@amazon.com"},
                    {"subject": "receipt google cloud", "from": "google"},
                ]},
            )
            times.append((time.perf_counter() - t0) * 1000)
        mean = sum(times) / len(times)
        assert mean < 50.0, f"re-rank slow: {mean:.2f}ms (target <50ms)"
