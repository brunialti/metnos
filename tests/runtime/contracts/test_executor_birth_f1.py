from __future__ import annotations

import ast
from pathlib import Path

from executor_birth import observe_candidate
from executor_birth_identity import (
    AdmissionContextV1,
    ContextComponent,
    ExecutorOrigin,
    RevisionAuthor,
)
from manifest_inventory import ContractId, ManifestOrigin


def _context() -> AdmissionContextV1:
    component = ContextComponent("v1", "sha256:" + "1" * 64)
    return AdmissionContextV1(**{
        name: component for name in AdmissionContextV1.__dataclass_fields__
    })


def _source(root: Path) -> Path:
    root.mkdir()
    (root / "manifest.toml").write_text(
        'manifest_format="1.0"\nexecutor_standard="metnos.executor/1.0"\n'
        'name="demo"\nversion="1"\naffinity=[]\nrevertible=false\n'
        'lifecycle="synthesized"\n[description]\nit="demo"\n'
        '[code]\nfiles=["demo.py"]\ndigest="sha256:' + "2" * 64 + '"\n'
        '[args]\ntype="object"\n',
        encoding="utf-8",
    )
    (root / "manifest.lang_state.json").write_text(
        '{"schema_version":1,"selectors":{}}', encoding="utf-8",
    )
    (root / "demo.py").write_text("def invoke(args): return {'ok': True}\n")
    return root


def test_f1_observes_owned_bytes_and_never_calls_publisher(
    tmp_path: Path, monkeypatch,
) -> None:
    calls = []
    import contract_store
    import sign

    def forbidden(*_args, **_kwargs):
        calls.append("publish")
        raise AssertionError("F1 must not publish")

    for module, names in (
        (contract_store, ("publish_technical_update", "publish_signed_source")),
        (sign, ("publish_executor", "sign_executor")),
    ):
        for name in names:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, forbidden)

    with observe_candidate(
        _source(tmp_path / "source"),
        contract_id=ContractId(ManifestOrigin.USER, "demo/manifest.toml"),
        executor_origin=ExecutorOrigin.SYNTHESIZED,
        revision_authorship=RevisionAuthor.MODEL,
        objective_hash="sha256:" + "3" * 64,
        admission_context=_context(),
        private_parent=tmp_path,
    ) as observed:
        assert observed.identities.candidate_id.startswith("sha256:")
        assert observed.identities.semantic_core_id.startswith("sha256:")
        assert observed.identities.admission_context_id.startswith("sha256:")
        assert observed.snapshot.private_root.is_dir()
    assert calls == []


def test_f1_modules_have_no_publication_or_low_level_signing_imports() -> None:
    root = Path(__file__).resolve().parents[3]
    forbidden = {
        "publish_technical_update", "publish_signed_source", "publish_executor",
        "sign_executor", "_commit_payloads_locked", "sign_manifest_bytes",
    }
    for relative in (
        "runtime/executor_birth.py",
        "runtime/executor_birth_identity.py",
        "runtime/executor_birth_receipts.py",
        "runtime/executor_birth_snapshot.py",
    ):
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert imported.isdisjoint(forbidden), (relative, imported & forbidden)
