"""Source-binding checks are not completeness or runtime certifications."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from internal.tools.rm0009_inventory_check import (
    GitBaseline,
    load_inventory,
    validate_inventory,
)


COMMIT = "1" * 40
CONTENT = b"synthetic source\n"
PATH = "runtime/example.py"


def inventory():
    return {
        "source_commit": COMMIT,
        "input_sha256": {PATH: hashlib.sha256(CONTENT).hexdigest()},
        "errors": [],
    }


def check(data, *, baseline=None, checkout=None):
    baseline = {PATH: CONTENT} if baseline is None else baseline
    checkout = {PATH: CONTENT} if checkout is None else checkout
    return validate_inventory(
        data,
        expected_commit=COMMIT,
        baseline_reader=baseline.__getitem__,
        checkout_reader=checkout.__getitem__,
    )


def test_matching_sources_have_narrow_verdict():
    result = check(inventory())
    assert result["valid"] is True
    assert result["checked_files"] == 1
    assert result["scope"] == "inventory_source_content_binding_only"
    assert result["checks_file_mode"] is False
    assert result["inventory_payload_validated"] is False
    assert result["certifies_completeness"] is False
    assert result["authorizes_implementation"] is False


@pytest.mark.parametrize("bad", [None, [], 0, True, "inventory"])
def test_non_object_inventory_is_rejected(bad):
    assert check(bad)["valid"] is False


@pytest.mark.parametrize("field,bad", [
    ("source_commit", None), ("source_commit", "2" * 40),
    ("source_commit", "HEAD"), ("source_commit", "--help"),
    ("input_sha256", None), ("input_sha256", {}),
    ("input_sha256", []), ("errors", None),
    ("errors", ""), ("errors", ["unreadable input"]),
])
def test_missing_or_invalid_envelope_is_rejected(field, bad):
    data = inventory()
    data[field] = bad
    assert check(data)["valid"] is False


@pytest.mark.parametrize("path", [
    "/tmp/source.py", "../source.py", "runtime/../source.py",
    "runtime//source.py", "runtime/./source.py", "./source.py",
    "runtime/*.py", "runtime/a?.py", "runtime/[a].py",
    "runtime\\source.py", "C:/source.py", ".git/config",
    "runtime/.git/config", "runtime/source.py\n", "",
])
def test_unsafe_path_never_reaches_reader(path):
    data = inventory()
    data["input_sha256"] = {path: hashlib.sha256(CONTENT).hexdigest()}

    def forbidden(_):
        pytest.fail("invalid path was read")

    result = validate_inventory(
        data, expected_commit=COMMIT,
        baseline_reader=forbidden, checkout_reader=forbidden,
    )
    assert result["valid"] is False


@pytest.mark.parametrize("digest", [None, True, 64, "", "a" * 63, "z" * 64, "A" * 64])
def test_invalid_digest_is_rejected(digest):
    data = inventory()
    data["input_sha256"][PATH] = digest
    assert check(data)["valid"] is False


def test_wrong_commit_never_reads_a_source():
    data = inventory()
    data["source_commit"] = "2" * 40

    def forbidden(_):
        pytest.fail("wrong commit was read")

    assert validate_inventory(
        data, expected_commit=COMMIT,
        baseline_reader=forbidden, checkout_reader=forbidden,
    )["valid"] is False


@pytest.mark.parametrize("side", ["baseline", "checkout"])
def test_missing_source_is_not_a_match(side):
    assert check(inventory(), **{side: {}})["valid"] is False


@pytest.mark.parametrize("side", ["baseline", "checkout"])
def test_changed_source_is_not_a_match(side):
    result = check(inventory(), **{side: {PATH: b"changed source\n"}})
    assert result["valid"] is False
    assert result["checked_files"] == 0


def test_input_order_does_not_change_report_or_mutate_input():
    data = inventory()
    data["input_sha256"]["runtime/second.py"] = hashlib.sha256(CONTENT).hexdigest()
    before = copy.deepcopy(data)
    files = {PATH: CONTENT, "runtime/second.py": CONTENT}
    first = check(data, baseline=files, checkout=files)
    data["input_sha256"] = dict(reversed(list(data["input_sha256"].items())))
    assert check(data, baseline=files, checkout=files) == first
    assert data == before


@pytest.mark.parametrize("raw", [
    '{"source_commit":"first","source_commit":"second"}',
    '{"input_sha256":{"runtime/a.py":"first","runtime/a.py":"second"}}',
    '{"value":NaN}', '{"value":Infinity}', '{"value":-Infinity}',
    '{"value":1e999}', '{"value":-1e999}',
])
def test_ambiguous_or_non_finite_json_is_rejected(tmp_path, raw):
    path = tmp_path / "inventory.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError):
        load_inventory(path)


def test_normal_json_load(tmp_path):
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(inventory()), encoding="utf-8")
    assert load_inventory(path) == inventory()


def test_checkout_reader_rejects_symlink_component(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "example.py").write_bytes(CONTENT)
    (tmp_path / "runtime").symlink_to(real, target_is_directory=True)
    source = GitBaseline(tmp_path, COMMIT)
    with pytest.raises(ValueError, match="symlink"):
        source.read_checkout(PATH)


def test_checkout_reader_rejects_leaf_symlink(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    target = tmp_path / "source.py"
    target.write_bytes(CONTENT)
    (runtime / "example.py").symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        GitBaseline(tmp_path, COMMIT).read_checkout(PATH)


def test_checkout_reader_requires_regular_single_link_file(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    target = runtime / "example.py"
    target.write_bytes(CONTENT)
    (runtime / "alias.py").hardlink_to(target)
    with pytest.raises(ValueError, match="regular|link"):
        GitBaseline(tmp_path, COMMIT).read_checkout(PATH)


def test_real_git_baseline_is_read_only():
    root = Path(__file__).resolve().parents[2]
    commit = "1c308922839f7659a3cf54d988f995bba0f215d6"
    source = GitBaseline(root, commit)
    path = "runtime/change_intents.py"
    baseline = source.read_baseline(path)
    assert baseline == source.read_checkout(path)
    assert hashlib.sha256(baseline).hexdigest() == (
        "a6c1b40b5cf5da08f83dc14f8bcd2ed4458f008d7d9d454cafbc8e717c77b287"
    )


def test_untracked_file_is_not_a_git_baseline_source():
    root = Path(__file__).resolve().parents[2]
    source = GitBaseline(root, "1c308922839f7659a3cf54d988f995bba0f215d6")
    with pytest.raises(ValueError, match="tracked"):
        source.read_baseline("internal/tools/rm0009_inventory_check.py")


@pytest.mark.parametrize("object_kind", [b"tree\n", b"blob\n", b"tag\n"])
def test_git_non_commit_is_rejected_before_ls_tree(tmp_path, monkeypatch, object_kind):
    source = GitBaseline(tmp_path, COMMIT)
    commands = []

    def fake_git(*args):
        commands.append(args)
        if args == ("cat-file", "-t", COMMIT):
            return object_kind
        pytest.fail("non-commit reached source lookup")

    monkeypatch.setattr(source, "_git", fake_git)
    with pytest.raises(ValueError, match="commit"):
        source.read_baseline(PATH)
    assert commands == [("cat-file", "-t", COMMIT)]


def test_real_tree_object_is_not_accepted_as_source_commit():
    root = Path(__file__).resolve().parents[2]
    source = GitBaseline(root, "c93825da501132189bb916fe57194004137cad07")
    with pytest.raises(ValueError, match="commit"):
        source.read_baseline("runtime/change_intents.py")


@pytest.mark.parametrize("claim", [
    "valid", "certifies_completeness", "authorizes_implementation",
    "checked_files", "expected_commit", "inventory_sha256",
    "checks_file_mode", "inventory_payload_validated", "unchecked_inventory_fields",
])
def test_report_claims_are_reserved_and_rejected(claim):
    data = inventory()
    data[claim] = True
    assert check(data)["valid"] is False


def test_semantic_inventory_payload_is_reported_unchecked():
    data = inventory()
    data["counts"] = {"future": 3}
    data["schema_version"] = "rm0009-security-inventory-v1"
    data["scope"] = "unverified specialist claim"
    result = check(data)
    assert result["valid"] is True
    assert result["unchecked_inventory_fields"] == ["counts", "schema_version", "scope"]
    assert result["scope"] == "inventory_source_content_binding_only"
    assert result["inventory_payload_validated"] is False


def test_nonexistent_git_object_is_not_a_binding():
    root = Path(__file__).resolve().parents[2]
    source = GitBaseline(root, "0" * 40)
    path = "runtime/change_intents.py"
    data = {
        "source_commit": "0" * 40,
        "input_sha256": {path: hashlib.sha256(source.read_checkout(path)).hexdigest()},
        "errors": [],
    }
    result = validate_inventory(
        data, expected_commit="0" * 40,
        baseline_reader=source.read_baseline,
        checkout_reader=source.read_checkout,
    )
    assert result["valid"] is False
    assert result["errors"] == [{"code": "baseline_unreadable", "path": path}]


@pytest.mark.parametrize("mode", [0o644, 0o755])
def test_content_binding_explicitly_does_not_certify_executable_mode(tmp_path, mode):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    target = runtime / "example.py"
    target.write_bytes(CONTENT)
    target.chmod(mode)
    source = GitBaseline(tmp_path, COMMIT)
    result = validate_inventory(
        inventory(), expected_commit=COMMIT,
        baseline_reader={PATH: CONTENT}.__getitem__,
        checkout_reader=source.read_checkout,
    )
    assert result["valid"] is True
    assert result["checks_file_mode"] is False


def test_cli_hashes_the_same_single_read_it_decodes(tmp_path, monkeypatch, capsys):
    import internal.tools.rm0009_inventory_check as module

    path = tmp_path / "inventory.json"
    raw = json.dumps(inventory()).encode("utf-8")
    path.write_bytes(raw)
    original_read = Path.read_bytes
    reads = 0

    def counted_read(self):
        nonlocal reads
        if self == path:
            reads += 1
            assert reads == 1, "inventory was read again after parsing"
        return original_read(self)

    class Source:
        def __init__(self, *_):
            pass

        def read_baseline(self, _):
            return CONTENT

        def read_checkout(self, _):
            return CONTENT

    monkeypatch.setattr(Path, "read_bytes", counted_read)
    monkeypatch.setattr(Path, "read_text", lambda self, **_: counted_read(self).decode("utf-8"))
    monkeypatch.setattr(module, "GitBaseline", Source)
    monkeypatch.setattr("sys.argv", [
        "inventory-check", str(path), "--repository", str(tmp_path),
        "--expected-commit", COMMIT,
    ])
    assert module.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["inventory_sha256"] == hashlib.sha256(raw).hexdigest()
    assert reads == 1
