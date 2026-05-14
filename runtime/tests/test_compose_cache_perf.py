"""Test cache LRU + perf measurement di `prompt_loader.compose()`
(Phase 2 planner reengineer, 12/5/2026).

Fase C (11/5/2026) ha introdotto `compose()` 3-layer (`_core` + sections +
`_footer`) con `lru_cache(maxsize=128)`. Phase 2 estende con:

- `cache_stats()` → metriche hits/misses/no_cache/size/maxsize/hit_ratio.
- `invalidate_cache(role=None, lang=None)` → reset puntuale per i test
  e dopo `i18n_translator.align_prompts()`.
- Bench `prompts_cli bench` per misurare perf in CI/dev.

Determinismo §7.9: zero LLM, solo filesystem + MiniJinja env.
"""
from __future__ import annotations

import sys
from pathlib import Path


_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class TestCacheStatsShape:
    """cache_stats() ritorna dict con la shape documentata."""

    def test_shape_after_reset(self):
        import prompt_loader
        prompt_loader.invalidate_cache()
        stats = prompt_loader.cache_stats()
        assert isinstance(stats, dict)
        for k in ("hits", "misses", "no_cache", "size", "maxsize", "hit_ratio"):
            assert k in stats, f"missing key {k}"
        # Reset → tutti 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["no_cache"] == 0
        assert stats["size"] == 0
        assert stats["maxsize"] >= 1
        assert stats["hit_ratio"] == 0.0


class TestColdMiss:
    """Prima call su una combinazione di sections = cache miss."""

    def test_first_call_is_miss(self):
        import prompt_loader
        prompt_loader.invalidate_cache()
        # Render del planner con sections fisse + vars stub semplici.
        # Tutti gli stub_vars devono essere stringhe per essere hashable.
        out = prompt_loader.compose(
            "planner", "it",
            sections=["mail"],
            project_paths="<stub>",
            users_known="<stub>",
        )
        stats = prompt_loader.cache_stats()
        assert stats["misses"] >= 1
        assert stats["hits"] == 0
        assert stats["size"] >= 1
        assert isinstance(out, str) and len(out) > 0


class TestWarmHit:
    """Seconda call identica = cache hit."""

    def test_second_call_same_args_is_hit(self):
        import prompt_loader
        prompt_loader.invalidate_cache()
        common_args = {
            "project_paths": "<stub>",
            "users_known": "<stub>",
        }
        # 1° call: miss
        out1 = prompt_loader.compose("planner", "it", sections=["mail"], **common_args)
        stats1 = prompt_loader.cache_stats()
        # 2° call: hit
        out2 = prompt_loader.compose("planner", "it", sections=["mail"], **common_args)
        stats2 = prompt_loader.cache_stats()
        assert out1 == out2, "render output deterministico"
        assert stats2["hits"] == stats1["hits"] + 1
        assert stats2["misses"] == stats1["misses"]  # nessuna nuova miss
        # Hit ratio: 1 hit / (1 hit + 1 miss) = 0.5
        assert 0.4 < stats2["hit_ratio"] < 0.6


class TestInvalidateCache:
    """invalidate_cache() resetta lru + counter; ritorna entries rimosse."""

    def test_invalidate_clears_lru_and_counters(self):
        import prompt_loader
        prompt_loader.invalidate_cache()
        common_args = {
            "project_paths": "<stub>",
            "users_known": "<stub>",
        }
        # Riempi cache con 3 entries diverse
        prompt_loader.compose("planner", "it", sections=["mail"], **common_args)
        prompt_loader.compose("planner", "it", sections=["calendar"], **common_args)
        prompt_loader.compose("planner", "it", sections=["web/search"], **common_args)
        before = prompt_loader.cache_stats()
        assert before["size"] >= 3
        # Invalidate
        removed = prompt_loader.invalidate_cache()
        assert removed >= 3
        after = prompt_loader.cache_stats()
        assert after["hits"] == 0
        assert after["misses"] == 0
        assert after["size"] == 0
        # Subsequent call e' di nuovo miss
        prompt_loader.compose("planner", "it", sections=["mail"], **common_args)
        post = prompt_loader.cache_stats()
        assert post["misses"] >= 1

    def test_invalidate_non_planner_role_noop(self):
        """invalidate_cache(role="vaglio") deve essere no-op (vaglio non e'
        cached oggi, ritorna 0)."""
        import prompt_loader
        prompt_loader.invalidate_cache()  # reset to known state
        removed = prompt_loader.invalidate_cache(role="vaglio")
        assert removed == 0
