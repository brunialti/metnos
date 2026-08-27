"""Certification of the installed proof limited to the provisioner."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from . import installed_provisioner_proof_v1 as proof

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="the provisioner Windows matrix is not proven yet"
)


@pytest.fixture(scope="module")
def report(tmp_path_factory) -> dict:
    source = Path(__file__).resolve().parents[3]
    workspace = tmp_path_factory.mktemp("installed-proof")
    return proof.run_proof(source, workspace)


def _step(report: dict, name: str) -> dict:
    return next(
        item["result"] for item in report["steps"] if item["step"] == name
    )


def test_every_step_of_the_installed_proof_succeeds(report: dict):
    failures = [
        item for item in report["steps"] if not item["result"].get("ok")
    ]
    assert failures == []


def test_an_installation_without_an_author_defers_and_creates_nothing(
    report: dict,
):
    assert _step(report, "defer_without_author")["outcome"] == (
        "author_not_yet_created"
    )
    unchanged = _step(report, "root_holds_no_artefact_after_defer")
    assert unchanged["ok"]
    # Taking the lock is how the entry serialises; no author root, no set and
    # no transaction were created.
    assert unchanged["names"] == ["operator-input-v1", "provisioning-v1.lock"]


def test_the_inspect_first_entry_converges_before_the_machine(report: dict):
    """Section 10.6: a stop between the contracts and the ensure converges."""
    converged = _step(report, "converge_before_the_machine")
    assert converged["outcome"] == "installed"
    assert _step(report, "ensure_prepared")["outcome"] == "already_installed"


def test_the_rerun_needs_no_previous_source(report: dict):
    assert _step(report, "rerun_without_the_previous_source")["outcome"] == (
        "already_installed"
    )


def test_the_productive_loaders_agree_with_the_installed_set(report: dict):
    observed = _step(report, "verify_with_productive_loaders")
    assert observed["marker_state"] == "prepared_not_active"
    assert observed["marker_set_id"] == observed["set_id"]
    assert observed["author_verifier_key_ids"] == (
        observed["declared_author_verifier_key_ids"]
    )
    assert observed["admission_active_key_id"] == (
        observed["declared_admission_active_key_id"]
    )
    assert observed["producer_count"] == 11


def test_the_context_material_is_rebuilt_from_the_installed_catalogue(
    report: dict,
):
    observed = _step(report, "verify_with_productive_loaders")
    assert observed["rebuilt_admission_context_id"] == (
        observed["prepared_admission_context_id"]
    )
    assert observed["rebuilt_context_epoch"] == observed["prepared_context_epoch"]
    assert observed["rebuilt_material_sha256"] == (
        observed["installed_material_sha256"]
    )


def test_nothing_is_activated_and_no_caller_is_migrated(report: dict):
    observed = _step(report, "nothing_is_active")
    assert observed["previous_decoder_present"] is True
    assert observed["bootstrap_uses_provisioner"] is False
    assert observed["operational_uses_provisioner"] is False
    assert observed["outcomes"] == [
        "already_installed", "author_not_yet_created", "installed",
    ]


def test_the_report_is_canonical_and_says_what_is_not_proven(
    report: dict, tmp_path: Path,
):
    path = tmp_path / "report.json"
    proof.write_report(report, path)
    assert json.loads(path.read_bytes()) == report
    assert report["proof_id"] == "installed_provisioner_proof_v1"
    assert report["schema_version"] == 1 and report["platform"]
    assert len(report["git_commit"]) == 40
    assert report["not_yet_proven"] and all(
        isinstance(item, str) for item in report["not_yet_proven"]
    )
    for forbidden in ("phase3", "birth_start", "reattestation", "f4"):
        assert forbidden not in report["proof_id"]
