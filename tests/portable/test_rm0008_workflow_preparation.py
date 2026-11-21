"""The reviewed CI setup must not relax or rewrite the frozen 2A proofs."""
from __future__ import annotations

import pytest

from tests.portable.rm0008_2a_acceptance import certification_v1 as certification


def test_additional_workflow_changes_are_rejected(tmp_path):
    path = tmp_path / "workflow.yml"
    source = certification.WORKFLOW_PATH.read_text(encoding="utf-8")
    path.write_text(source.replace("--require-hashes --no-deps", "--no-deps"))
    with pytest.raises(certification.CertificationError, match="frozen certification form"):
        certification.validate_workflow_structure(path)
