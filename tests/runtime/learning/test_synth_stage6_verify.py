"""Test del Layer 6 di synth admission policy: stage 6 semantic verifier
(ADR 0114, 8/5/2026 sera).

Bug live: `find_texts` description "motori di ricerca online" + code che
faceva tutt'altro. Stage 6 confronta description vs code via LLM e
rifiuta i misalignments.

Determinismo §7.9: tutti i test mockano il LLM (no live call).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


def _make_llm_returning(text: str):
    """Helper: ritorna un fake LLM call function."""
    def fake_call(prompt, model):
        return {"text": text}
    return fake_call


def _make_llm_raising():
    """Helper: ritorna un fake LLM call che solleva."""
    def fake_call(prompt, model):
        raise RuntimeError("fake LLM offline")
    return fake_call


class TestAlignedDescriptionMatchesCode:
    """Quando il LLM ritorna `aligned=true`, la verify accetta."""

    def test_aligned_true_returns_aligned(self, tmp_path, monkeypatch):
        from synt_stage6_verify import verify_semantic_alignment
        import synt_stage6_verify as s6
        monkeypatch.setattr(s6, "VERIFY_AUDIT_DIR", tmp_path / "audit")
        llm = _make_llm_returning('{"aligned": true, "mismatch": ""}')
        result = verify_semantic_alignment(
            description="Cerca file su filesystem.",
            code_body="def invoke(args):\n    import glob\n    return glob.glob(args['pattern'])",
            llm_call=llm,
            name_hint="find_files_test",
        )
        assert result["aligned"] is True
        assert result["mismatch"] == ""
        assert result["model"] == "wise"
        # Raw output preserved
        assert result["raw"]["aligned"] is True


class TestMisalignedDescriptionRejected:
    """Quando il LLM ritorna `aligned=false` con motivo, la verify rifiuta."""

    def test_misaligned_returns_aligned_false(self, tmp_path, monkeypatch):
        from synt_stage6_verify import verify_semantic_alignment
        import synt_stage6_verify as s6
        monkeypatch.setattr(s6, "VERIFY_AUDIT_DIR", tmp_path / "audit")
        llm = _make_llm_returning(
            '{"aligned": false, "mismatch": "description promette web search ma il code legge file locali"}'
        )
        result = verify_semantic_alignment(
            description="Cerca su motori di ricerca online.",
            code_body="def invoke(args):\n    return open('/tmp/x').read()",
            llm_call=llm,
            name_hint="find_texts_bug",
        )
        assert result["aligned"] is False
        assert "description promette web" in result["mismatch"]


class TestMalformedJsonFallsBackToAlignedFalse:
    """JSON malformato 2x → fail-safe `aligned=False`."""

    def test_malformed_json_returns_aligned_false(self, tmp_path, monkeypatch):
        from synt_stage6_verify import verify_semantic_alignment
        import synt_stage6_verify as s6
        monkeypatch.setattr(s6, "VERIFY_AUDIT_DIR", tmp_path / "audit")
        llm = _make_llm_returning("Wait, this is not JSON at all, just preamble.")
        result = verify_semantic_alignment(
            description="Foo bar",
            code_body="def invoke(args): pass",
            llm_call=llm,
            name_hint="malformed_test",
        )
        assert result["aligned"] is False
        assert "fallback" in result["mismatch"].lower()

    def test_llm_raising_returns_aligned_false(self, tmp_path, monkeypatch):
        """Se il LLM stesso solleva, fail-safe."""
        from synt_stage6_verify import verify_semantic_alignment
        import synt_stage6_verify as s6
        monkeypatch.setattr(s6, "VERIFY_AUDIT_DIR", tmp_path / "audit")
        llm = _make_llm_raising()
        result = verify_semantic_alignment(
            description="X",
            code_body="def invoke(args): pass",
            llm_call=llm,
            name_hint="raising_test",
        )
        assert result["aligned"] is False


class TestAuditLogWritten:
    """Ogni verify call append una riga audit."""

    def test_audit_log_has_prompt_and_response(self, tmp_path, monkeypatch):
        from synt_stage6_verify import verify_semantic_alignment
        import synt_stage6_verify as s6
        audit_dir = tmp_path / "audit"
        monkeypatch.setattr(s6, "VERIFY_AUDIT_DIR", audit_dir)
        llm = _make_llm_returning('{"aligned": true, "mismatch": ""}')
        verify_semantic_alignment(
            description="test desc",
            code_body="def invoke(args): pass",
            llm_call=llm,
            name_hint="audit_test",
        )
        assert audit_dir.exists()
        files = list(audit_dir.glob("verify_*.jsonl"))
        assert len(files) == 1
        line = json.loads(files[0].read_text(encoding="utf-8").strip())
        assert "ts" in line
        assert line["name_hint"] == "audit_test"
        assert "prompt_len" in line
        assert "response_len" in line
        assert line["verdict"]["aligned"] is True


class TestMultiModelConsensus:
    """Con `LLM_VERIFY_MODELS=m1,m2,m3`, majority wins."""

    def test_majority_aligned_wins(self, tmp_path, monkeypatch):
        from synt_stage6_verify import verify_semantic_alignment
        import synt_stage6_verify as s6
        monkeypatch.setattr(s6, "VERIFY_AUDIT_DIR", tmp_path / "audit")
        monkeypatch.setenv("LLM_VERIFY_MODELS", "m1,m2,m3")
        # Mock: m1 e m2 dicono aligned=True; m3 dice aligned=False
        responses = {
            "m1": '{"aligned": true, "mismatch": ""}',
            "m2": '{"aligned": true, "mismatch": ""}',
            "m3": '{"aligned": false, "mismatch": "noise"}',
        }
        def fake_call(prompt, model):
            return {"text": responses[model]}
        result = verify_semantic_alignment(
            description="test",
            code_body="def invoke(args): pass",
            llm_call=fake_call,
            name_hint="consensus_test",
        )
        # 2/3 aligned → aligned True
        assert result["aligned"] is True
        assert result["model"] == "consensus:3"

    def test_majority_misaligned_wins(self, tmp_path, monkeypatch):
        from synt_stage6_verify import verify_semantic_alignment
        import synt_stage6_verify as s6
        monkeypatch.setattr(s6, "VERIFY_AUDIT_DIR", tmp_path / "audit")
        monkeypatch.setenv("LLM_VERIFY_MODELS", "m1,m2,m3")
        responses = {
            "m1": '{"aligned": false, "mismatch": "code does X not Y"}',
            "m2": '{"aligned": false, "mismatch": "missing feature"}',
            "m3": '{"aligned": true, "mismatch": ""}',
        }
        def fake_call(prompt, model):
            return {"text": responses[model]}
        result = verify_semantic_alignment(
            description="test",
            code_body="def invoke(args): pass",
            llm_call=fake_call,
            name_hint="consensus_misaligned",
        )
        # 2/3 misaligned → aligned False
        assert result["aligned"] is False


class TestParseVerifyJson:
    """Parser tollerante: gestisce JSON pulito + JSON con preamble."""

    def test_clean_json_parsed(self):
        from synt_stage6_verify import _parse_verify_json
        result = _parse_verify_json('{"aligned": true, "mismatch": ""}')
        assert result == {"aligned": True, "mismatch": ""}

    def test_json_with_preamble_extracted(self):
        from synt_stage6_verify import _parse_verify_json
        result = _parse_verify_json('Sure here it is: {"aligned": false, "mismatch": "reason"}')
        assert result == {"aligned": False, "mismatch": "reason"}

    def test_no_json_returns_none(self):
        from synt_stage6_verify import _parse_verify_json
        result = _parse_verify_json("just plain prose, nothing")
        assert result is None

    def test_empty_returns_none(self):
        from synt_stage6_verify import _parse_verify_json
        assert _parse_verify_json("") is None
        assert _parse_verify_json("   ") is None


class TestStage6WiredInRunFull:
    """`run_full` invoca stage 6 dopo stage 5; misalignment → final_state
    `rejected_semantic_drift`. Approccio: monkey-patch dei singoli
    `run_stageN` per saltare le validazioni interne complesse e
    concentrarci sulla wiring di stage 6."""

    def _patch_pipeline(self, monkeypatch, *, code_text: str, description: str):
        """Patcha le 5 stages per ritornare output minimo coerente."""
        import synt_multistage as sm
        from synt_multistage import StageResult

        def fake_s1(*a, **k):
            return StageResult(stage=1, success=True,
                               output={"name": "find_test_synth",
                                       "action": "find", "object": "files",
                                       "qualifier": None,
                                       "revertible": False, "critical": False,
                                       "target_kind": "discover"})

        def fake_s2(*a, **k):
            return StageResult(stage=2, success=True,
                               output={"args_schema": {"type": "object",
                                                       "required": [],
                                                       "properties": {}},
                                       "args_required": [],
                                       "args_properties": {},
                                       "capabilities": [],
                                       "reverse_pattern": None})

        def fake_s3(*a, **k):
            return StageResult(stage=3, success=True,
                               output={"tests": [{"name": "happy",
                                                  "input": {},
                                                  "expect": {"ok": True}}]})

        def fake_s4(*a, **k):
            contract_description = (
                f"SCOPO: {description}. PATTERN: find_test_synth(). "
                "NON: modificare file. OUT: entries."
            )
            return StageResult(stage=4, success=True,
                               output={"description": contract_description,
                                       "affinity": ["a", "b", "c"]})

        def fake_s5(*a, **k):
            return StageResult(stage=5, success=True, output={"code": code_text})

        monkeypatch.setattr(sm, "run_stage1", fake_s1)
        monkeypatch.setattr(sm, "run_stage2", fake_s2)
        monkeypatch.setattr(sm, "run_stage3", fake_s3)
        monkeypatch.setattr(sm, "run_stage4", fake_s4)
        monkeypatch.setattr(sm, "run_stage5", fake_s5)

    def test_misaligned_synth_rejected_in_pipeline(self, tmp_path, monkeypatch):
        import synt_stage6_verify as s6
        monkeypatch.setattr(s6, "VERIFY_AUDIT_DIR", tmp_path / "audit")
        monkeypatch.delenv("METNOS_SYNT_STAGE6_DISABLED", raising=False)
        self._patch_pipeline(
            monkeypatch,
            code_text="def invoke(args):\n    return {'ok': True}",
            description="Cerca su motori di ricerca online",
        )

        # llm_call_wise: stage 5 (code) gia' patchato; questa call e' SOLO
        # per la verify di stage 6 → ritorna aligned=false
        def llm_call_wise(system, user, **kw):
            return {"text": '{"aligned": false, "mismatch": "code does nothing meaningful"}',
                    "in_tokens": 0, "out_tokens": 0, "latency_ms": 1}
        def llm_call_middle(*a, **k):
            return {"text": "{}", "in_tokens": 0, "out_tokens": 0, "latency_ms": 1}

        from synt_multistage import run_full
        run = run_full("dummy req", llm_call_middle, llm_call_wise)
        assert run.final_state == "rejected_semantic_drift", run.abandon_reason
        assert run.semantic_verdict is not None
        assert run.semantic_verdict["aligned"] is False

    def test_aligned_synth_proceeds(self, tmp_path, monkeypatch):
        """Stage 6 aligned=true → final_state='synthesized'."""
        import synt_stage6_verify as s6
        monkeypatch.setattr(s6, "VERIFY_AUDIT_DIR", tmp_path / "audit")
        monkeypatch.delenv("METNOS_SYNT_STAGE6_DISABLED", raising=False)
        self._patch_pipeline(
            monkeypatch,
            code_text="def invoke(args):\n    return {'ok': True}",
            description="Trova file",
        )

        def llm_call_wise(system, user, **kw):
            return {"text": '{"aligned": true, "mismatch": ""}',
                    "in_tokens": 0, "out_tokens": 0, "latency_ms": 1}
        def llm_call_middle(*a, **k):
            return {"text": "{}", "in_tokens": 0, "out_tokens": 0, "latency_ms": 1}

        from synt_multistage import run_full
        run = run_full("dummy req", llm_call_middle, llm_call_wise)
        assert run.final_state == "synthesized", run.abandon_reason
        assert run.semantic_verdict is not None
        assert run.semantic_verdict["aligned"] is True

    def test_stage6_disabled_env_skips_verify(self, tmp_path, monkeypatch):
        """`METNOS_SYNT_STAGE6_DISABLED=1` → stage 6 non viene chiamato."""
        import synt_stage6_verify as s6
        monkeypatch.setattr(s6, "VERIFY_AUDIT_DIR", tmp_path / "audit")
        monkeypatch.setenv("METNOS_SYNT_STAGE6_DISABLED", "1")
        self._patch_pipeline(
            monkeypatch,
            code_text="def invoke(args):\n    return {'ok': True}",
            description="qualunque",
        )
        # llm_call_wise NON dovrebbe essere chiamato per stage 6
        wise_call_count = {"n": 0}
        def llm_call_wise(*a, **k):
            wise_call_count["n"] += 1
            return {"text": "X", "in_tokens": 0, "out_tokens": 0, "latency_ms": 1}
        def llm_call_middle(*a, **k):
            return {"text": "{}", "in_tokens": 0, "out_tokens": 0, "latency_ms": 1}
        from synt_multistage import run_full
        run = run_full("dummy", llm_call_middle, llm_call_wise)
        # Stage 5 e' patchato → wise non chiamato, e stage 6 disabled
        assert run.final_state == "synthesized"
        assert wise_call_count["n"] == 0
        assert run.semantic_verdict is None
