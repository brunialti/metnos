"""Configuration faults remain local and recover on the next resolution."""
from __future__ import annotations

import os

import pytest


def test_invalid_llm_file_is_not_a_default_binding(tmp_path, monkeypatch):
    import llm_helpers
    import llm_router

    path = tmp_path / "llm_tiers.toml"
    monkeypatch.setenv("METNOS_LLM_TIERS_CONFIG", str(path))
    path.write_text('[fast]\nprovider="llamacpp"\nendpoint="http://127.0.0.1:18281"\n')
    assert llm_router.tier_endpoint("fast") == "http://127.0.0.1:18281"

    path.write_text("[invalid configuration\n")

    def forbidden(*args, **kwargs):
        pytest.fail("invalid configuration must not contact a default provider")

    monkeypatch.setattr(llm_helpers, "LlamaCppProvider", forbidden)
    for _ in range(2):
        with pytest.raises(llm_router.TierConfigError):
            llm_router.tier_endpoint("fast")
        with pytest.raises(llm_router.TierConfigError):
            llm_helpers.call_llm("test", "test", tier="fast")

    path.write_text('[fast]\nprovider="llamacpp"\nendpoint="http://127.0.0.1:18282"\n')
    assert llm_router.tier_endpoint("fast") == "http://127.0.0.1:18282"
    assert llm_router.LLMRouter().tiers["fast"]["endpoint"] == "http://127.0.0.1:18282"


@pytest.mark.parametrize("state", ["missing", "directory", "unreadable"])
def test_only_missing_configuration_uses_defaults(tmp_path, monkeypatch, state):
    import llm_router

    path = tmp_path / "llm_tiers.toml"
    monkeypatch.setenv("METNOS_LLM_TIERS_CONFIG", str(path))
    if state == "missing":
        assert llm_router.tier_endpoint("fast") == llm_router.LOCAL_DEFAULT_ENDPOINT
        return
    if state == "directory":
        path.mkdir()
    else:
        path.write_text("[fast]\nprovider='llamacpp'\n")

        def refuse_read(*args, **kwargs):
            raise PermissionError("test-only read refusal")
        monkeypatch.setattr(llm_router, "_load_config_file", refuse_read)
    with pytest.raises(llm_router.TierConfigError, match="llm_configuration_invalid"):
        llm_router.tier_endpoint("fast")


def test_atomic_config_replacement_with_preserved_mtime_is_observed(tmp_path, monkeypatch):
    import llm_router

    path = tmp_path / "llm_tiers.toml"
    monkeypatch.setenv("METNOS_LLM_TIERS_CONFIG", str(path))
    template = '[fast]\nprovider="llamacpp"\nendpoint="http://127.0.0.1:{port}"\n'
    path.write_text(template.format(port=18281))
    before = path.stat()
    assert llm_router.tier_endpoint("fast") == "http://127.0.0.1:18281"
    replacement = tmp_path / "replacement.toml"
    replacement.write_text(template.format(port=18282))
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    os.replace(replacement, path)

    assert llm_router.tier_endpoint("fast") == "http://127.0.0.1:18282"
