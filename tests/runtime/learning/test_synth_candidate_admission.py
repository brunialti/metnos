from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace


RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

import synth_request  # noqa: E402
from executor_standard import STANDARD_ID, validate_for_lifecycle  # noqa: E402
from loader import Catalog, Executor  # noqa: E402
from synt import GeneratedProposal, Synt, _build_specialize_manifest  # noqa: E402
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
    admitted = []
    monkeypatch.setattr(
        synth_request,
        "publish_authoring_update",
        lambda path: admitted.append(path),
    )

    output_dir = synth_request._install_synthesized(
        _candidate_run(), "trova file", "trova file",
    )
    manifest = tomllib.loads((output_dir / "manifest.toml").read_text(encoding="utf-8"))

    assert admitted == [output_dir]
    assert manifest["executor_standard"] == STANDARD_ID
    assert manifest["lifecycle"] == "synthesized"
    assert manifest["execution"]["parallelism_class"] == 0
    assert manifest["execution"]["equivalence_gate"] == "unverified"
    assert len(manifest["tests"]) == 3
    assert manifest["output"]["schema_inline"]
    assert validate_for_lifecycle(manifest) == []


def test_existing_candidate_is_not_regenerated_or_exposed(
        tmp_path: Path, monkeypatch) -> None:
    from manifest_inventory import ManifestLayout

    monkeypatch.setattr(
        "manifest_inventory.resolve_manifest_layout",
        lambda: ManifestLayout.AUTHORING,
    )
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


def test_existing_store_candidate_reconciles_through_idempotent_publisher(
        tmp_path: Path, monkeypatch) -> None:
    from manifest_inventory import ManifestLayout

    source = tmp_path / "find_files" / "manifest.toml"
    source.parent.mkdir()
    source.write_text("name = 'find_files'\n")
    candidate = Executor(
        name="find_files",
        version="0.1.0",
        description="candidate",
        affinity=[],
        args_schema={},
        capabilities=[],
        tests=[],
        code_path=tmp_path / "generation" / "find_files.py",
        manifest_path=tmp_path / "generation" / "manifest.toml",
        authoring_manifest_path=source,
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
        "manifest_inventory.resolve_manifest_layout",
        lambda: ManifestLayout.STORE_ONLY,
    )
    published = []
    monkeypatch.setattr(
        synth_request,
        "publish_authoring_update",
        lambda path: published.append(path),
    )

    result = synth_request.handle_synth_request(
        {"expected_name": "find_files", "intent": "trova file"},
        user_query="trova file",
    )

    assert result["candidate_existing"] is True
    assert published == [source.parent]
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


def test_specialized_manifest_preserves_localized_parent_surfaces() -> None:
    rendered = _build_specialize_manifest(
        target_name="find_files_pdf",
        parent_name="find_files",
        parent_manifest={
            "description": {
                "it": (
                    'SCOPO: trova file. PATTERN: find_files(path="x", '
                    'extension="pdf") NON: non modifica file. OUT: entries.'
                ),
                "en": (
                    'SCOPO: finds files. PATTERN: find_files(path="x", '
                    'extension="pdf") NON: does not modify files. OUT: entries.'
                ),
                "fr": (
                    'SCOPO: trouve des fichiers. PATTERN: find_files(path="x", '
                    'extension="pdf") NON: ne modifie pas les fichiers. OUT: entries.'
                ),
            },
            "affinity": ["find", "files"],
            "args": {"type": "object"},
            "capabilities": [{"name": "fs:read", "hint": ["arg:path"]}],
            "output": {
                "schema_inline": "{ok: bool, entries: list}",
                "media_type": "application/json",
            },
            "tests": [{
                "name": "pdf_case",
                "input": {"path": "x", "extension": "pdf"},
                "expect": {"ok": True},
            }],
            "presentation": {
                "default_view": "list",
                "list": {
                    "columns": [{"key": "item", "source": "$entry"}],
                },
            },
        },
        arg_name="extension",
        dominant_value="pdf",
        args_properties={
            "path": {
                "type": "string",
                "description": {
                    "it": "Percorso.", "en": "Path.", "fr": "Chemin.",
                },
            },
            "extension": {
                "type": "string",
                "default": "pdf",
                "runtime_resolved": True,
                "description": {
                    "it": "Estensione.", "en": "Extension.",
                    "fr": "Extension.",
                },
            },
        },
        args_required=["path"],
    )
    manifest = tomllib.loads(rendered)

    assert set(manifest["description"]) == {"it", "en", "fr"}
    assert manifest["description"]["it"].startswith(
        "SCOPO: [parent=find_files; fixed_arg=extension]"
    )
    for language in manifest["description"]:
        pattern = manifest["description"][language].split(
            "PATTERN: ", 1,
        )[1].split(" NON:", 1)[0]
        parsed_pattern = ast.parse(pattern, mode="eval").body
        assert parsed_pattern.func.id == "find_files_pdf"
        assert [keyword.arg for keyword in parsed_pattern.keywords] == ["path"]
    assert manifest["args"]["properties"]["path"]["description"] == {
        "it": "Percorso.", "en": "Path.", "fr": "Chemin.",
    }
    assert manifest["output"] == {
        "schema_inline": "{ok: bool, entries: list}",
        "media_type": "application/json",
    }
    assert manifest["tests"] == [{
        "name": "specialized_0_pdf_case",
        "input": {"path": "x"},
        "expect": {"ok": True},
    }]
    assert validate_for_lifecycle(manifest) == []
    from manifest_lint import lint_manifest
    for language in manifest["description"]:
        assert not [
            finding for finding in lint_manifest(manifest, language=language)
            if finding.severity == "error"
        ]
