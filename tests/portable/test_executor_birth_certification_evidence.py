"""Evidence-owner persistence proofs, not real F5 routing qualification."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from executor_birth_canonical import encode_canonical_ascii_v1 as canonical
from executor_birth_authority_files import OwnershipAuthorityError
import install.birth_certification_evidence as evidence


native = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="managed evidence custody is Linux-only")
DIGEST = "sha256:" + "a" * 64
BASE = dict.fromkeys(("installation_id", "head_id", "source_id", "catalog_id", "harness_id"), DIGEST)
CASES = [{"case_id": name, "postcondition_probes": ["observed_effect"]} for name in ("first", "second")]
MATRIX = "".join(json.dumps(case, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for case in CASES).encode()
MANIFEST = {"case_matrix_sha256": hashlib.sha256(MATRIX).hexdigest()}


def _open(root):
    root.chmod(0o755)
    return evidence._evidence_at_v1(root, root_owned=False)


def _seed(owner):
    owner.census(scope_id=DIGEST, sources=(b"known findings",), review=b"independent census review",
                 findings={"terminal-binding": b"causal defect evidence"})
    opening = owner.frontier.head
    owner.close_defect("terminal-binding", opening=opening, repair=b"reviewed repair",
                       verification=b"affected tests", review=b"independent repair review")
    owner.profile(base=BASE, manifest=canonical(MANIFEST), cases=canonical(CASES))


def _results(*, passed=True):
    return [{"case_id": case["case_id"], "verdict": "pass" if passed else "fail",
             "failure_reasons": [] if passed else ["postcondition_not_proven"],
             "probe_results": [{"name": "observed_effect", "passed": passed, "detail": "fixture proof"}]}
            for case in CASES]


def _finish(owner, *, passed=True, results=None, turns=None):
    start = owner.frontier.pending_cycle
    turns = turns if turns is not None else {
        case["case_id"]: {f"turn-{start}-{case['case_id']}": canonical({"turn": start, "case": case["case_id"]})}
        for case in CASES
    }
    return owner.finish_cycle(start=start, results=canonical(results if results is not None else _results(passed=passed)), turns=turns)


@native
def test_complete_chronology_is_reread_without_signing_or_activation(tmp_path):
    with _open(tmp_path) as owner:
        assert owner.frontier.census_scope is None
        _seed(owner)
        owner.start_cycle()
        first = _finish(owner)
        owner.start_cycle()
        final = _finish(owner)
        assert len(first.consecutive_successes) == 1
        assert len(final.consecutive_successes) == 2
        assert final.open_findings == () and final.pending_cycle is None
        assert not hasattr(final, "certificate")
    with _open(tmp_path) as owner:
        assert owner.frontier == final
    path = tmp_path / evidence.DIRECTORY_V1 / "evidence.sqlite"
    assert path.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(path) as connection:
        for table in ("events", "artifacts"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute(f"DELETE FROM {table}")


@native
@pytest.mark.parametrize("break_kind", ("failure", "interruption", "changed-profile"))
def test_successes_cannot_be_selected_across_a_broken_sequence(tmp_path, break_kind):
    with _open(tmp_path) as owner:
        _seed(owner)
        owner.start_cycle()
        _finish(owner)
        if break_kind == "changed-profile":
            owner.profile(base={**BASE, "catalog_id": "sha256:" + "b" * 64}, manifest=canonical(MANIFEST), cases=canonical(CASES))
        else:
            owner.start_cycle()
            if break_kind == "failure":
                _finish(owner, passed=False)
    with _open(tmp_path) as owner:
        assert not owner.frontier.consecutive_successes
        owner.start_cycle()
        assert len(_finish(owner).consecutive_successes) == 1


@native
def test_reopened_defect_requires_its_new_exact_opening_and_failed_call_is_atomic(tmp_path):
    with _open(tmp_path) as owner:
        _seed(owner)
        previous = owner.frontier.head
        owner.open_defect("terminal-binding", b"new evidence")
        reopened = owner.frontier
        with pytest.raises(evidence.EvidenceError, match="stale finding"):
            owner.close_defect("terminal-binding", opening=previous, repair=b"bad repair",
                               verification=b"bad verification", review=b"bad review")
        assert owner.frontier == reopened
        owner.close_defect("terminal-binding", opening=reopened.head, repair=b"new repair",
                           verification=b"new verification", review=b"new independent review")
        assert not owner.frontier.open_findings
        assert owner._connection.execute("SELECT count(*) FROM artifacts WHERE body=?", (b"bad repair",)).fetchone()[0] == 0


@native
@pytest.mark.parametrize("damage", ("missing", "duplicate", "unknown", "bool-pass", "probe", "turns", "start"))
def test_inconsistent_cycle_is_not_committed(tmp_path, damage):
    with _open(tmp_path) as owner:
        _seed(owner)
        before = owner.start_cycle()
        results = _results()
        turns = None
        if damage == "missing":
            results.pop()
        elif damage == "duplicate":
            results.append(copy.deepcopy(results[0]))
        elif damage == "unknown":
            results[0]["case_id"] = "unknown"
        elif damage == "bool-pass":
            results[0]["probe_results"][0]["passed"] = 1
        elif damage == "probe":
            results[0]["probe_results"] = []
        elif damage == "turns":
            turns = dict.fromkeys((case["case_id"] for case in CASES), {})
        with pytest.raises(evidence.EvidenceError):
            if damage == "start":
                owner.start_cycle()
            else:
                _finish(owner, results=results, turns=turns)
        assert owner.frontier == before
        assert len(_finish(owner).consecutive_successes) == 1


@native
def test_turn_replay_does_not_qualify_as_another_cycle(tmp_path):
    turns = {case["case_id"]: {f"same-{case['case_id']}": canonical(case)} for case in CASES}
    with _open(tmp_path) as owner:
        _seed(owner)
        owner.start_cycle()
        _finish(owner, turns=turns)
        owner.start_cycle()
        with pytest.raises(evidence.EvidenceError, match="turn reused"):
            _finish(owner, turns=turns)


@native
def test_missing_census_and_changed_artifact_or_schema_are_refused(tmp_path):
    with _open(tmp_path) as owner:
        with pytest.raises(evidence.EvidenceError, match="census required"):
            owner.start_cycle()
        _seed(owner)
    path = tmp_path / evidence.DIRECTORY_V1 / "evidence.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER artifacts_update")
        connection.execute("UPDATE artifacts SET body=? WHERE body=?", (b"altered", b"known findings"))
    with pytest.raises(evidence.EvidenceError, match="schema"):
        with _open(tmp_path):
            pass
    with sqlite3.connect(path) as connection:
        connection.execute(next(sql for sql in evidence._DDL if "TRIGGER artifacts_update " in sql))
    with pytest.raises(evidence.EvidenceError, match="artifact missing or changed"):
        with _open(tmp_path):
            pass


@native
def test_unsupported_wal_store_is_refused_without_rewriting_it(tmp_path):
    with _open(tmp_path) as owner:
        _seed(owner)
    path = tmp_path / evidence.DIRECTORY_V1 / "evidence.sqlite"
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    connection.close()
    before = path.read_bytes()
    with pytest.raises(evidence.EvidenceError, match="journal format"):
        with _open(tmp_path):
            pass
    assert path.read_bytes() == before
    assert not path.with_name(path.name + "-wal").exists()


def _terminate_cycle_owner(root, environment, *, root_owned):
    result = subprocess.run([
        sys.executable, "-B", "-c",
        "import os,sys; from pathlib import Path; "
        "from install.birth_certification_evidence import _evidence_at_v1; "
        f"exec('with _evidence_at_v1(Path(sys.argv[1]), root_owned={root_owned!r}) as owner:\\n owner.start_cycle()\\n os._exit(73)')",
        str(root),
    ], env=environment, capture_output=True, timeout=20)
    assert result.returncode == 73, result.stderr.decode()


@native
def test_process_death_preserves_start_and_recovery_records_interruption(tmp_path):
    with _open(tmp_path) as owner:
        _seed(owner)
    repository = Path(__file__).resolve().parents[2]
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join((str(repository), str(repository / "runtime")))}
    _terminate_cycle_owner(tmp_path, environment, root_owned=False)
    with _open(tmp_path) as owner:
        assert owner.frontier.pending_cycle is None
        assert not owner.frontier.consecutive_successes
        last = owner._connection.execute("SELECT body FROM events ORDER BY sequence DESC LIMIT 1").fetchone()[0]
        assert json.loads(last)["kind"] == "cycle_interrupted"


def test_fixed_entry_refuses_wrong_platform_and_unprivileged_owner_before_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected filesystem access")
    monkeypatch.setattr(evidence, "_root_owned_chain", forbidden)
    monkeypatch.setattr(evidence, "_managed_authority_platform_supported_v1", lambda: False)
    with pytest.raises(OwnershipAuthorityError, match="platform_unsupported"):
        with evidence.administrative_evidence_v1():
            pass
    monkeypatch.setattr(evidence, "_managed_authority_platform_supported_v1", lambda: True)
    monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)
    with pytest.raises(OwnershipAuthorityError, match="root_required"):
        with evidence.administrative_evidence_v1():
            pass
    with pytest.raises(TypeError):
        evidence.administrative_evidence_v1(root="/caller-selected")


def test_boundary_guard_classifies_administrative_entry_in_an_unreviewed_consumer(tmp_path):
    import contract_boundary_guard as guard
    path = tmp_path / "runtime" / "unreviewed.py"
    path.parent.mkdir()
    path.write_text(
        "from install.birth_certification_evidence import administrative_evidence_v1\n"
        "def probe():\n    with administrative_evidence_v1() as evidence:\n        pass\n",
        encoding="utf-8",
    )
    facts = guard.scan_file(path, repository_root=tmp_path)
    assert any(fact.scope == "probe" and "store_write" in fact.capabilities for fact in facts)
