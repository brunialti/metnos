"""RM-0008 F5: one catalog identity shared by every cache family."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest


def _entry(
    name: str,
    *,
    generation_id: str | None = None,
    lifecycle: str = "active",
    description: str = "description A",
    affinity: tuple[str, ...] = ("alpha",),
    digest: str = "code-a",
    virtual: bool = False,
):
    return SimpleNamespace(
        name=name,
        generation_id=generation_id,
        lifecycle=lifecycle,
        description=description,
        affinity=list(affinity),
        digest=digest,
        args_schema={"type": "object", "properties": {}},
        capabilities=[{"name": "data:read"}],
        tests=[],
        manifest_path=Path("builtin.py" if virtual else "manifest.toml"),
        membership="virtual" if virtual else "product",
        source="virtual" if virtual else "handcrafted",
    )


def test_published_identity_is_generation_plus_effective_lifecycle():
    from executor_catalog_identity import catalog_entry_identity

    original = _entry("read_files", generation_id="gen-a")
    manifest_projection_changed = _entry(
        "read_files", generation_id="gen-a", description="not authoritative",
        affinity=("different",), digest="different",
    )
    assert catalog_entry_identity(original) == catalog_entry_identity(
        manifest_projection_changed)
    assert catalog_entry_identity(original) != catalog_entry_identity(
        _entry("read_files", generation_id="gen-b"))
    assert catalog_entry_identity(original) != catalog_entry_identity(
        _entry("read_files", generation_id="gen-a", lifecycle="quarantined"))


def test_virtual_and_legacy_fallback_domains_cannot_alias():
    from executor_catalog_identity import catalog_entry_identity

    legacy = _entry("get_now")
    virtual = _entry("get_now", virtual=True)
    # Make all projected semantic fields equal except the domain classifier.
    virtual.membership = legacy.membership
    virtual.source = legacy.source
    assert catalog_entry_identity(legacy) != catalog_entry_identity(virtual)


def test_legacy_manifest_only_change_invalidates_common_whole_pool_identity():
    from executor_catalog_identity import catalog_identity

    sibling_a = _entry("read_files", description="old")
    sibling_b = _entry("find_files", description="B")
    before = catalog_identity([sibling_a, sibling_b])
    changed_a = _entry("read_files", description="new")
    assert before != catalog_identity([changed_a, sibling_b])


def test_fallback_canonicalizes_mapping_proxy_dataclass_and_enum():
    from executor_catalog_identity import catalog_entry_identity

    class Mode(Enum):
        READ = "read"

    @dataclass(frozen=True)
    class Policy:
        mode: Mode
        limits: MappingProxyType

    first = _entry("read_files")
    first.new_effective_field = Policy(
        Mode.READ, MappingProxyType({"bytes": 10, "flags": ("a", "b")}))
    second = _entry("read_files")
    second.new_effective_field = Policy(
        Mode.READ, MappingProxyType({"flags": ("a", "b"), "bytes": 10}))
    assert catalog_entry_identity(first) == catalog_entry_identity(second)


def test_new_effective_field_is_never_silently_omitted():
    from executor_catalog_identity import catalog_entry_identity

    first = _entry("read_files")
    second = _entry("read_files")
    first.future_executor_meta = {"authority": "read"}
    second.future_executor_meta = {"authority": "write"}
    assert catalog_entry_identity(first) != catalog_entry_identity(second)


def test_unsupported_value_fails_closed_without_repr_fallback():
    from executor_catalog_identity import CatalogIdentityError, catalog_entry_identity

    class AddressBearingRepr:
        pass

    entry = _entry("read_files")
    entry.future_executor_meta = AddressBearingRepr()
    with pytest.raises(CatalogIdentityError, match="unsupported catalog identity value"):
        catalog_entry_identity(entry)


def test_non_string_mapping_keys_and_private_unknown_fields_fail_closed():
    from executor_catalog_identity import CatalogIdentityError, catalog_entry_identity

    bad_key = _entry("read_files")
    bad_key.future_executor_meta = {1: "ambiguous-with-string-one"}
    with pytest.raises(CatalogIdentityError, match="non-string mapping key"):
        catalog_entry_identity(bad_key)

    private = _entry("read_files")
    private._future_semantics = "cannot-classify"
    with pytest.raises(CatalogIdentityError, match="unknown private catalog fields"):
        catalog_entry_identity(private)


def test_common_identity_is_used_by_all_prefilter_cache_families(tmp_path, monkeypatch):
    from executor_catalog_identity import catalog_identity
    from prefilter_strategies import _catalog_sig, fts5
    from prefilter_strategies import bloom, trie, trie_v2
    import affinity_semantic

    sibling_a = _entry("read_files", generation_id="gen-a")
    sibling_b = _entry("find_files", generation_id="gen-b")
    catalog_a = [sibling_a, sibling_b]
    sibling_b_quarantined = _entry(
        "find_files", generation_id="gen-b", lifecycle="quarantined")
    catalog_b = [sibling_a, sibling_b_quarantined]

    common_a = catalog_identity(catalog_a)
    common_b = catalog_identity(catalog_b)
    assert common_a != common_b
    assert _catalog_sig.catalog_signature(catalog_a) == common_a
    assert fts5._catalog_signature(catalog_a) == common_a
    assert bloom._get_filters(catalog_a) is not bloom._get_filters(catalog_b)
    assert trie._build_trie(catalog_a) is not trie._build_trie(catalog_b)
    assert trie_v2._build_trie(catalog_a) is not trie_v2._build_trie(catalog_b)
    assert affinity_semantic._cache_key(catalog_a) != affinity_semantic._cache_key(
        catalog_b)

    # A persisted FTS5 index carrying the old 16-hex signature must rebuild,
    # even if its indexed rows happen to look usable.
    monkeypatch.setattr(fts5, "_INDEX_PATH", tmp_path / "prefilter.sqlite")
    import sqlite3
    connection = sqlite3.connect(str(fts5._INDEX_PATH))
    connection.execute("CREATE TABLE sig (key TEXT PRIMARY KEY, value TEXT)")
    connection.execute(
        "INSERT INTO sig(key, value) VALUES ('catalog', '0123456789abcdef')")
    connection.commit()
    connection.close()
    rebuilt, signature = fts5._build_index(catalog_a)
    try:
        assert signature == common_a
        assert rebuilt.execute(
            "SELECT value FROM sig WHERE key='catalog'").fetchone() == (common_a,)
    finally:
        rebuilt.close()


def test_cached_token_index_flushes_when_only_lifecycle_changes(monkeypatch):
    from prefilter_strategies import cached_token_flat

    cached_token_flat._CACHE.clear()
    cached_token_flat._CACHE_SIG[0] = "legacy-signature"
    cached_token_flat._CACHE["stale-query"] = (["read_files"], {})

    calls = []

    def rank(_query, catalog, **_kwargs):
        calls.append(tuple(item.lifecycle for item in catalog))
        return list(catalog), {"reason": "rebuilt"}

    import prefilter
    monkeypatch.setattr(prefilter, "_rank_adaptive_legacy", rank)
    catalog = [_entry(
        "read_files", generation_id="gen-a", lifecycle="quarantined")]
    result, info = cached_token_flat.CachedTokenFlatStrategy().rank(
        "stale query", catalog)
    assert result == catalog and info["cache_hit"] is False
    assert calls == [("quarantined",)]
    assert "stale-query" not in cached_token_flat._CACHE


def test_token_index_rebuilds_for_manifest_and_lifecycle_changes(monkeypatch):
    import prefilter_rules

    monkeypatch.setattr(prefilter_rules, "_RARE_TOKENS_CACHE", None)
    monkeypatch.setattr(prefilter_rules, "_RARE_TOKENS_CATALOG_ID", None)
    calls = []

    def build(catalog):
        calls.append(tuple(entry.lifecycle for entry in catalog))
        return {str(len(calls))}

    monkeypatch.setattr(prefilter_rules, "_build_rare_tokens", build)
    active = [_entry("read_files", generation_id="gen-a")]
    quarantined = [_entry(
        "read_files", generation_id="gen-a", lifecycle="quarantined")]
    prefilter_rules.init_rare_tokens(active)
    prefilter_rules.init_rare_tokens(active)
    prefilter_rules.init_rare_tokens(quarantined)
    assert calls == [("active",), ("quarantined",)]


def test_tools_and_whole_family_signatures_bind_generations_and_lifecycle():
    from engine.cache_validity import pool_sig, tools_sig
    from engine.types import Framework, Intent, StepSpec

    framework = Framework(steps=[StepSpec(tool="read_files", args={})])
    intent = Intent(verb="read", object="files")
    sibling_a = _entry("read_files", generation_id="gen-a")
    sibling_b = _entry("find_files", generation_id="gen-b")
    before = [sibling_a, sibling_b]

    assert tools_sig(framework, before) != tools_sig(
        framework, [_entry("read_files", generation_id="gen-c"), sibling_b])
    # B was not selected by the framework, but belongs to the whole producer
    # family and therefore invalidates the negative decision signature.
    assert pool_sig(intent, before) != pool_sig(
        intent, [sibling_a, _entry(
            "find_files", generation_id="gen-b", lifecycle="quarantined")])


def test_new_signature_versions_force_legacy_rows_to_rebuild():
    from engine.cache_validity import catalog_epoch, plan_sigs, validate
    from engine.types import Framework, Intent, StepSpec

    catalog = [_entry("read_files", generation_id="gen-a")]
    framework = Framework(steps=[StepSpec(tool="read_files", args={})])
    intent = Intent(verb="read", object="files")
    tools, pool = plan_sigs(framework, intent, catalog)
    assert tools.startswith("cvs2-") and pool.startswith("cvs2-")
    assert catalog_epoch(catalog).startswith("eci1-")
    ok, _reason = validate(
        "0123456789abcdef", "fedcba9876543210",
        framework, intent, catalog,
    )
    assert ok is False
