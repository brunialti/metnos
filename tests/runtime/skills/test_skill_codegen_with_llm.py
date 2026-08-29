"""Test R1 (24/5/2026): wiring `skill_description_llm.generate_description_or_fallback`
nel pipeline import (cli/skills_cli._cmd_import).

Copre:
- fallback boilerplate quando LLM non disponibile (timeout/error).
- LLM fake injection via METNOS_LLM_DESCRIPTION_FAKE → description LLM
  finisce nel manifest TOML.
- timeout enforced via thread wrapper (call lenta → fallback).
- audit JSONL append-only in <PATH_USER_DATA>/skill_descriptions_audit.jsonl.

Determinismo §7.9: nessun LLM reale; fake function controllata da test.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


# ---------------------------------------------------------------------------
# Fake LLM injection helpers
# ---------------------------------------------------------------------------


def _fake_llm_ok(prompt, timeout_s, max_tokens):
    """Fake LLM che ritorna un JSON valido (description + affinity)."""
    return json.dumps({
        "description_it": "Cerca eventi di test LLM. DEVI usarla per test. "
                          "NON DEVI usarla in produzione. USO CORRETTO: x. "
                          "ERRORE: y.",
        "description_en": "Searches LLM test events. MUST be used for tests. "
                          "MUST NOT be used in production. CORRECT USE: x. "
                          "ERROR: y.",
        "affinity": ["llm test", "test events", "fake calendar"],
    })


def _fake_llm_timeout(prompt, timeout_s, max_tokens):
    """Fake che simula timeout: dorme oltre il budget e ritorna None
    sarebbe ucciso dal thread join — qui simuliamo None direttamente."""
    return None


def _fake_llm_malformed(prompt, timeout_s, max_tokens):
    """Fake che ritorna JSON malformato."""
    return "not a json {{"


def _fake_llm_bare_affinity(prompt, timeout_s, max_tokens):
    """Valid descriptions with unsafe bare affinity terms."""
    return json.dumps({
        "description_it": "Descrizione valida.",
        "description_en": "Valid description.",
        "affinity": ["file", "document", "create", "write"],
    })


# Module-level for env injection (mod.fn). Pytest can monkeypatch.
sys.modules["__fake_llm__"] = SimpleNamespace(
    ok=_fake_llm_ok,
    timeout=_fake_llm_timeout,
    malformed=_fake_llm_malformed,
    bare_affinity=_fake_llm_bare_affinity,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_user_data(tmp_path, monkeypatch):
    """Isola METNOS_USER_DATA in tmp_path. Reset cache config per refresh."""
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    user_data = fake_home / ".local" / "share" / "metnos"
    user_data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("METNOS_USER_DATA", str(user_data))
    # `config` modulo cached: reset le costanti che leggono env al boot.
    import importlib
    import config as _C
    importlib.reload(_C)
    yield user_data
    # Reload nuovamente al teardown per non lasciare in cache la tmp dir.
    importlib.reload(_C)


@pytest.fixture
def fake_plan():
    """ExecutorPlan minimale per test."""
    plan = SimpleNamespace(
        name="find_events_google_workspace",
        verb="find",
        obj="events",
        qualifier="google_workspace",
        skill_domain="calendar",
        skill_action="list",
        args=[],
        output_kind="entries",
        output_record_kind="calendar_event",
        reversible=False,
        reverse_pattern="",
        capabilities=[],
        provenance={
            "imported_from": "agentskills.io/local/google-workspace",
            "source_version": "1.0",
            "source_section": "Calendar",
            "source_subcommand": "calendar list",
            "imported_at": "2026-05-24T00:00:00Z",
            "source_sha256": "abc123",
            "importer_version": "0.1.0-poc",
        },
        examples=[],
    )
    return plan


@pytest.fixture
def fake_parsed_skill():
    """ParsedSkill minimale."""
    return SimpleNamespace(
        name="google-workspace",
        version="1.0",
        raw_body="## Calendar.list\n\nList events from primary calendar.",
        required_credential_files=[],
        scripts=[],
        allowed_tools=[],
        source_sha256="abc123",
    )


# ---------------------------------------------------------------------------
# Tests R1
# ---------------------------------------------------------------------------


class TestGenerateDescriptionOrFallback:
    """`skill_description_llm.generate_description_or_fallback` API."""

    def test_llm_ok_returns_llm_source(self, isolated_user_data, monkeypatch,
                                        fake_plan, fake_parsed_skill):
        monkeypatch.setenv("METNOS_LLM_DESCRIPTION_FAKE", "__fake_llm__.ok")
        from skill_description_llm import generate_description_or_fallback
        result = generate_description_or_fallback(
            fake_plan, fake_parsed_skill,
            skill_body_snippet="snippet",
            boilerplate_it="boil_it",
            boilerplate_en="boil_en",
            boilerplate_affinity=["boil1"],
        )
        assert result["source"] == "llm"
        assert "LLM" in result["description_it"]
        assert "MUST" in result["description_en"]
        assert "llm test" in result["affinity"]

    def test_llm_unavailable_returns_boilerplate(self, isolated_user_data,
                                                  monkeypatch, fake_plan,
                                                  fake_parsed_skill):
        monkeypatch.setenv("METNOS_LLM_DESCRIPTION_FAKE",
                            "__fake_llm__.timeout")
        from skill_description_llm import generate_description_or_fallback
        result = generate_description_or_fallback(
            fake_plan, fake_parsed_skill,
            skill_body_snippet="",
            boilerplate_it="BOILER IT",
            boilerplate_en="BOILER EN",
            boilerplate_affinity=["b1", "b2"],
        )
        assert result["source"] == "boilerplate"
        assert result["description_it"] == "BOILER IT"
        assert result["description_en"] == "BOILER EN"
        assert result["affinity"] == ["b1", "b2"]

    @pytest.mark.parametrize("override", ["other.fake", "__fake_llm__.missing"])
    def test_unapproved_fake_never_falls_through_to_real_llm(
            self, monkeypatch, override):
        monkeypatch.setenv("METNOS_LLM_DESCRIPTION_FAKE", override)
        from skill_description_llm import _call_llm

        assert _call_llm("must not reach a provider", timeout_s=0.01) is None

    def test_llm_bare_affinity_uses_qualified_baseline(
            self, isolated_user_data, monkeypatch, fake_plan,
            fake_parsed_skill):
        monkeypatch.setenv(
            "METNOS_LLM_DESCRIPTION_FAKE", "__fake_llm__.bare_affinity",
        )
        from skill_description_llm import generate_description_or_fallback
        result = generate_description_or_fallback(
            fake_plan, fake_parsed_skill,
            boilerplate_it="BOILER IT",
            boilerplate_en="BOILER EN",
            boilerplate_affinity=["cerca eventi", "find events"],
        )
        assert result["source"] == "llm"
        assert result["affinity"] == ["cerca eventi", "find events"]

    def test_llm_malformed_returns_boilerplate(self, isolated_user_data,
                                                monkeypatch, fake_plan,
                                                fake_parsed_skill):
        monkeypatch.setenv("METNOS_LLM_DESCRIPTION_FAKE",
                            "__fake_llm__.malformed")
        from skill_description_llm import generate_description_or_fallback
        result = generate_description_or_fallback(
            fake_plan, fake_parsed_skill,
            boilerplate_it="X", boilerplate_en="Y",
            boilerplate_affinity=[],
        )
        assert result["source"] == "boilerplate"
        assert result["description_it"] == "X"


class TestAuditLog:
    """Audit JSONL append-only in `<PATH_USER_DATA>/skill_descriptions_audit.jsonl`."""

    def test_audit_records_llm_call(self, isolated_user_data, monkeypatch,
                                     fake_plan, fake_parsed_skill):
        monkeypatch.setenv("METNOS_LLM_DESCRIPTION_FAKE", "__fake_llm__.ok")
        # Disabilita PYTEST_CURRENT_TEST silencer per questo test.
        monkeypatch.delenv("METNOS_SKILLS_QUIET", raising=False)
        from skill_description_llm import generate_description_or_fallback
        generate_description_or_fallback(
            fake_plan, fake_parsed_skill,
            boilerplate_it="x", boilerplate_en="y",
            boilerplate_affinity=[],
        )
        audit_path = isolated_user_data / "skill_descriptions_audit.jsonl"
        assert audit_path.is_file()
        lines = audit_path.read_text().splitlines()
        assert len(lines) >= 1
        rec = json.loads(lines[-1])
        assert rec["plan_name"] == "find_events_google_workspace"
        assert rec["skill_name"] == "google-workspace"
        assert rec["source"] == "llm"
        assert "elapsed_ms" in rec
        assert "timeout_s" in rec

    def test_audit_records_boilerplate_fallback(self, isolated_user_data,
                                                 monkeypatch, fake_plan,
                                                 fake_parsed_skill):
        monkeypatch.setenv("METNOS_LLM_DESCRIPTION_FAKE",
                            "__fake_llm__.timeout")
        monkeypatch.delenv("METNOS_SKILLS_QUIET", raising=False)
        from skill_description_llm import generate_description_or_fallback
        generate_description_or_fallback(
            fake_plan, fake_parsed_skill,
            boilerplate_it="x", boilerplate_en="y",
            boilerplate_affinity=[],
        )
        audit_path = isolated_user_data / "skill_descriptions_audit.jsonl"
        assert audit_path.is_file()
        lines = audit_path.read_text().splitlines()
        rec = json.loads(lines[-1])
        assert rec["source"] == "boilerplate"
        assert rec.get("error") == "llm_unavailable_or_timeout"

    def test_audit_silenced_in_pytest_quiet(self, isolated_user_data,
                                             monkeypatch, fake_plan,
                                             fake_parsed_skill):
        """Quando METNOS_SKILLS_QUIET=1 + PYTEST_CURRENT_TEST set, niente audit
        write (evita pollution di unit test che non mockano user data)."""
        monkeypatch.setenv("METNOS_LLM_DESCRIPTION_FAKE", "__fake_llm__.ok")
        monkeypatch.setenv("METNOS_SKILLS_QUIET", "1")
        # PYTEST_CURRENT_TEST e' settato da pytest automaticamente.
        from skill_description_llm import generate_description_or_fallback
        generate_description_or_fallback(
            fake_plan, fake_parsed_skill,
            boilerplate_it="x", boilerplate_en="y",
            boilerplate_affinity=[],
        )
        audit_path = isolated_user_data / "skill_descriptions_audit.jsonl"
        assert not audit_path.exists()


class TestTimeoutEnforced:
    """Thread wrapper enforces time budget anche se provider ignora timeout."""

    def test_slow_llm_killed_at_timeout(self, isolated_user_data, monkeypatch,
                                         fake_plan, fake_parsed_skill):
        """Simula LLM che dorme oltre il budget → fallback boilerplate."""
        def _slow_fake(prompt, timeout_s, max_tokens):
            time.sleep(2.0)
            return '{"description_it": "x", "description_en": "y", "affinity": []}'

        sys.modules["__fake_llm__"].slow = _slow_fake
        monkeypatch.setenv("METNOS_LLM_DESCRIPTION_FAKE", "__fake_llm__.slow")
        # Budget piccolo: 0.2s, slow fake sleeps 2.0s → timeout.
        monkeypatch.setenv("METNOS_SKILL_LLM_TIMEOUT_S", "1")
        # Forza reload del modulo per applicare nuovo DEFAULT_TIMEOUT_S.
        import importlib
        import skill_description_llm
        importlib.reload(skill_description_llm)
        from skill_description_llm import generate_description_or_fallback
        started = time.monotonic()
        result = generate_description_or_fallback(
            fake_plan, fake_parsed_skill,
            boilerplate_it="BOILER",
            boilerplate_en="EN",
            boilerplate_affinity=[],
            timeout_s=1,  # explicit override
        )
        # Anche se il fake non onora timeout (e' un thread non killable in
        # Python), il wrapper ritorna None e il caller usa boilerplate.
        assert result["source"] == "boilerplate"
        assert result["description_it"] == "BOILER"
        # Il timeout e' un limite di parete: non deve attendere i 2 secondi
        # della chiamata non cooperativa.
        assert time.monotonic() - started < 1.5


class TestCodegenIntegration:
    """`generate_executor_files` accetta description_it/en/affinity da LLM."""

    def test_description_llm_finishes_in_manifest(self, isolated_user_data,
                                                    tmp_path, fake_plan,
                                                    fake_parsed_skill):
        from skill_codegen import generate_executor_files
        executors_dir = tmp_path / "executors"
        out = generate_executor_files(
            fake_plan, fake_parsed_skill, executors_dir,
            description_it="LLM_DESC_IT",
            description_en="LLM_DESC_EN",
            affinity=["t1", "t2"],
        )
        manifest = Path(out["manifest_path"]).read_text()
        assert "LLM_DESC_IT" in manifest
        assert "LLM_DESC_EN" in manifest
        assert "t1" in manifest and "t2" in manifest
