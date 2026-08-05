from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace


RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

import synth_request  # noqa: E402
from executor_standard import STANDARD_ID, validate_for_lifecycle  # noqa: E402
from loader import Catalog, Executor  # noqa: E402
from synt import GeneratedProposal, Synt  # noqa: E402
from synt_multistage import StageResult  # noqa: E402


def _candidate_run() -> SimpleNamespace:
    return SimpleNamespace(
        name="find_files",
        code_text=(
            "def invoke(args):\n"
            "    return {'ok': True, 'entries': []}\n"
            "if __name__ == '__main__':\n"
            "    pass\n"
        ),
        stages=[
            StageResult(stage=1, success=True, output={"revertible": False}),
            StageResult(
                stage=2,
                success=True,
                output={
                    "args_required": [],
                    "args_properties": {},
                    "capabilities": [{"name": "fs:read", "hint": ["*"]}],
                    "reverse_pattern": None,
                },
            ),
            StageResult(
                stage=3,
                success=True,
                output={
                    "tests": [
                        {"name": "empty", "input": {}, "expect": {"ok": True}},
                        {"name": "invalid", "input": {"x": 1}, "expect": {"ok": False}},
                        {"name": "edge", "input": {}, "expect": {"ok": True}},
                    ],
                },
            ),
            StageResult(
                stage=4,
                success=True,
                output={
                    "description": (
                        "SCOPO: trova file. PATTERN: find_files(). "
                        "NON: modificare file. OUT: entries=[]."
                    ),
                    "affinity": ["find", "files", "trova", "file"],
                },
            ),
        ],
    )


def test_reactive_synth_is_signed_as_quarantined_standard_candidate(
        tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(synth_request, "SYNTHESIZED_EXECUTORS_DIR", tmp_path)
    signed = []
    monkeypatch.setattr(synth_request, "sign_executor", lambda path: signed.append(path))

    output_dir = synth_request._install_synthesized(
        _candidate_run(), "trova file", "trova file",
    )
    manifest = tomllib.loads((output_dir / "manifest.toml").read_text(encoding="utf-8"))

    assert signed == [output_dir]
    assert manifest["executor_standard"] == STANDARD_ID
    assert manifest["lifecycle"] == "synthesized"
    assert manifest["execution"]["parallelism_class"] == 0
    assert manifest["execution"]["equivalence_gate"] == "unverified"
    assert len(manifest["tests"]) == 3
    assert manifest["output"]["schema_inline"]
    assert validate_for_lifecycle(manifest) == []


def test_existing_candidate_is_not_regenerated_or_exposed(
        tmp_path: Path, monkeypatch) -> None:
    candidate = Executor(
        name="find_files",
        version="0.1.0",
        description="candidate",
        affinity=[],
        args_schema={},
        capabilities=[],
        tests=[],
        code_path=tmp_path / "find_files.py",
        manifest_path=tmp_path / "manifest.toml",
        signed_by="synt",
        lifecycle="synthesized",
        executor_standard=STANDARD_ID,
        standard_state="candidate",
        source="synthesized",
    )
    catalog = Catalog(executors={"find_files": candidate})

    import loader
    monkeypatch.setattr(loader, "load_catalog", lambda **_kwargs: catalog)
    monkeypatch.setattr(
        synth_request,
        "multistage_run_full",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("candidate must not be regenerated")
        ),
    )
    monkeypatch.setattr(
        synth_request, "_msg",
        lambda _key, **kwargs: f"candidate:{kwargs['name']}",
    )

    result = synth_request.handle_synth_request(
        {"expected_name": "find_files", "intent": "trova file"},
        user_query="trova file",
    )

    assert result["ok"] is True
    assert result["candidate_existing"] is True
    assert result["installed"] is False
    assert result["planner_visible"] is False


def test_synt_proposal_renderer_emits_standard_quarantined_manifest(
        tmp_path: Path) -> None:
    proposal = GeneratedProposal(
        proposal_id="proposal-1",
        request_id="request-1",
        name="find_files",
        description=(
            "SCOPO: trova file. PATTERN: find_files(patterns=[]). "
            "NON: modificare file. OUT: entries=[]."
        ),
        purpose="find files",
        affinity=["find", "files", "trova", "file"],
        python_code="def invoke(args): return {'ok': True, 'entries': []}",
        args_schema={"type": "object", "required": [], "properties": {}},
        output_summary="entries",
        proposal_dir=tmp_path,
        llm_provider="test",
        llm_model="test",
        llm_in_tokens=0,
        llm_out_tokens=0,
        llm_latency_ms=0,
        code_imports=[],
        non_stdlib_imports=[],
        convention_ok=True,
        convention_reason="ok",
    )

    manifest = tomllib.loads(Synt._render_manifest_toml(proposal))

    assert manifest["executor_standard"] == STANDARD_ID
    assert manifest["lifecycle"] == "proposed"
    assert manifest["execution"]["parallelism_class"] == 0
    assert manifest["execution"]["equivalence_gate"] == "unverified"
    assert validate_for_lifecycle(manifest) == []
