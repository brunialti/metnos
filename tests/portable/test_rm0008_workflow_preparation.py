"""The reviewed CI setup must not relax or rewrite the frozen 2A proofs."""
from __future__ import annotations

import re
import subprocess
import sys

import pytest

from tests.portable.rm0008_2a_acceptance import certification_v1 as certification
from tests.portable.test_rm0008_acceptance_evolution import _baseline


WORKFLOW = ".github/workflows/portable-contract-store.yml"
OLD_BLOB = ("100644", "ce3bbd7e5097b23e4ad577ffa1b8af40b75618f7")
NEW_BLOB = ("100644", "dc632bdfa0aa8ad30584a22aafeb5183b67978e4")


@pytest.mark.parametrize("variant", ("reviewed", "unknown", "mode", "predecessor"))
def test_only_exact_reviewed_workflow_preparation_is_admitted(variant):
    source = _baseline()
    source[WORKFLOW] = OLD_BLOB
    current = dict(source)
    for evolution in certification._REVIEWED_ACCEPTANCE_EVOLUTIONS:
        current[evolution] = ("100644", "d" * 40)
    current["tests/portable/test_rm0008_acceptance_evolution.py"] = (
        "100644", "e" * 40,
    )
    current[WORKFLOW] = NEW_BLOB
    if variant == "unknown":
        current[WORKFLOW] = ("100644", "f" * 40)
    elif variant == "mode":
        current[WORKFLOW] = ("100755", NEW_BLOB[1])
    elif variant == "predecessor":
        source[WORKFLOW] = ("100644", "f" * 40)
    if variant == "reviewed":
        certification._validate_reviewed_acceptance_tree_evolution(source, current)
    else:
        with pytest.raises(certification.CertificationError, match="unreviewed evolution"):
            certification._validate_reviewed_acceptance_tree_evolution(source, current)


def test_acceptance_jobs_and_summary_remain_byte_identical():
    current_text = certification.WORKFLOW_PATH.read_text(encoding="utf-8")
    old_text = certification._run_git_bytes(
        ["cat-file", "blob", OLD_BLOB[1]], "read original CI workflow",
    ).decode("utf-8")
    current = certification._workflow_job_blocks(current_text)
    original = certification._workflow_job_blocks(old_text)
    assert current.keys() == original.keys()
    for job in original.keys() - {"portable-contract-store"}:
        assert current[job] == original[job]
    certification.validate_workflow_structure()


def test_additional_workflow_changes_are_rejected(tmp_path):
    path = tmp_path / "workflow.yml"
    source = certification.WORKFLOW_PATH.read_text(encoding="utf-8")
    path.write_text(source.replace("--require-hashes --no-deps", "--no-deps"))
    with pytest.raises(certification.CertificationError, match="frozen certification form"):
        certification.validate_workflow_structure(path)


@pytest.mark.parametrize("phase", ("execution", "shutdown"))
def test_native_watchdog_trace_survives_pytest_descriptor_capture(phase):
    source = certification.WORKFLOW_PATH.read_text(encoding="utf-8")
    command = re.search(r'python -c "([^"]+)"', source)
    assert command is not None
    prefix, suffix = command[1].split("; import pytest; ")
    assert suffix == "raise SystemExit(pytest.main())"
    # Exercise the exact native diagnostic on the local platform, with a
    # shorter timer; no Metnos runner, test outcome or deadline is mocked.
    code = prefix.replace("600,", "0.1,").replace("os.name == 'nt'", "True")
    code += (
        "; import tempfile, time, atexit; capture = tempfile.TemporaryFile(); "
        "os.dup2(capture.fileno(), 2); "
    )
    code += "time.sleep(3)" if phase == "execution" else "atexit.register(time.sleep, 3)"
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 1
    assert "Timeout" in result.stderr
