"""Real NTFS certification for the executor Birth semantic authority."""
from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from executor_birth_semantic_authority import EVIDENCE_DOMAIN, load_semantic_authority
from executor_birth_semantic_review import IndependentEvidenceKind, SemanticReviewError, SemanticReviewRequest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires real Windows NTFS semantics")
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
D3 = "sha256:" + "3" * 64
D4 = "sha256:" + "4" * 64


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def _own_permissions(path: Path) -> None:
    """Give one directory its own permission list, as a real root has.

    A historical read refuses a root whose permissions arrive from an ancestor,
    so the fixture must provision what the contract admits: the inherited
    entries are copied in place and inheritance is switched off.
    """
    subprocess.run(
        ["icacls", str(path), "/inheritance:d"],
        check=True,
        capture_output=True,
    )


def _provision(root: Path) -> tuple[dict[str, object], Ed25519PrivateKey]:
    _own_permissions(root)
    private = Ed25519PrivateKey.generate()
    (root / "evidence").mkdir()
    (root / "semantic.pub").write_bytes(private.public_key().public_bytes_raw())
    evidence = {
        "evidence_id": D3, "evidence_version": "v1", "kind": "deterministic_oracle",
        "owner_id": "oracle", "candidate_id": D1, "admission_context_id": D2,
        "status": "passed", "evidence_hash": D4,
    }
    record = {
        "schema_version": 1, "key_id": "oracle-key", "evidence": evidence,
        "signature": base64.b64encode(
            private.sign(EVIDENCE_DOMAIN + _canonical(evidence))).decode(),
    }
    (root / "evidence" / "proof.json").write_bytes(_canonical(record))
    spec: dict[str, object] = {
        "evidence_dir": "evidence",
        "verifiers": {"oracle-key": {"path": "semantic.pub", "status": "active"}},
        "versions": {kind.value: ["v1"] for kind in IndependentEvidenceKind},
        "owners": {kind.value: ["oracle"] for kind in IndependentEvidenceKind},
    }
    return spec, private


def _request() -> SemanticReviewRequest:
    return SemanticReviewRequest(D1, D2, "model:generator", b"name='x'", b"{}",
                                 {"main.py": b"print('ok')"})


def test_windows_reads_valid_handle_bound_authority(tmp_path: Path) -> None:
    spec, _ = _provision(tmp_path)
    authority = load_semantic_authority(spec, tmp_path)
    _, _, evidence = authority.inputs_for(_request())
    assert [item.evidence_id for item in evidence] == [D3]


@pytest.mark.parametrize("target", ["verifier", "evidence"])
def test_windows_rejects_file_symlink_reparse_points(tmp_path: Path, target: str) -> None:
    spec, _ = _provision(tmp_path)
    path = tmp_path / ("semantic.pub" if target == "verifier" else "evidence/proof.json")
    real = path.with_name("real-" + path.name)
    path.rename(real)
    path.symlink_to(real)
    if target == "verifier":
        with pytest.raises(SemanticReviewError, match="semantic_review_unavailable"):
            load_semantic_authority(spec, tmp_path)
    else:
        authority = load_semantic_authority(spec, tmp_path)
        with pytest.raises(SemanticReviewError, match="evidence_forged"):
            authority.inputs_for(_request())


@pytest.mark.parametrize("target", ["verifier", "evidence"])
def test_windows_rejects_hardlinked_authority_files(tmp_path: Path, target: str) -> None:
    spec, _ = _provision(tmp_path)
    path = tmp_path / ("semantic.pub" if target == "verifier" else "evidence/proof.json")
    os.link(path, tmp_path / (target + ".alias"))
    if target == "verifier":
        with pytest.raises(SemanticReviewError, match="semantic_review_unavailable"):
            load_semantic_authority(spec, tmp_path)
    else:
        authority = load_semantic_authority(spec, tmp_path)
        with pytest.raises(SemanticReviewError, match="evidence_forged"):
            authority.inputs_for(_request())


def test_windows_rejects_evidence_directory_junction(tmp_path: Path) -> None:
    spec, _ = _provision(tmp_path)
    target = tmp_path / "real-evidence"
    (tmp_path / "evidence").rename(target)
    junction = tmp_path / "evidence"
    made = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
                          check=False, capture_output=True, text=True)
    assert made.returncode == 0, made.stderr or made.stdout
    try:
        authority = load_semantic_authority(spec, tmp_path)
        with pytest.raises(SemanticReviewError, match="semantic_review_unavailable"):
            authority.inputs_for(_request())
    finally:
        os.rmdir(junction)


def test_windows_rejects_tampered_signed_record(tmp_path: Path) -> None:
    spec, _ = _provision(tmp_path)
    path = tmp_path / "evidence" / "proof.json"
    value = json.loads(path.read_bytes())
    value["evidence"]["status"] = "failed"
    path.write_bytes(_canonical(value))
    authority = load_semantic_authority(spec, tmp_path)
    with pytest.raises(SemanticReviewError, match="evidence_forged"):
        authority.inputs_for(_request())
