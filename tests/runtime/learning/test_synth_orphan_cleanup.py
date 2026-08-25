from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import synth_orphan_cleanup as cleanup


@pytest.fixture
def keys():
    private = Ed25519PrivateKey.generate()
    return private, (("test-author", private.public_key()),)


def _orphan(root: Path, name: str = "find_things") -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / f"{name}.py").write_text(
        "def invoke(args):\n    return {'ok': True}\n", encoding="utf-8",
    )
    return directory


def _apply(tmp_path: Path, keys, **kwargs):
    root = tmp_path / "executors"
    root.mkdir(exist_ok=True)
    receipts = tmp_path / "state" / "receipts.jsonl"
    receipts.parent.mkdir(exist_ok=True)
    private, trusted = keys
    return cleanup.quarantine(
        root=root,
        receipts_path=receipts,
        quarantine_root=root / "quarantine-test",
        dry_run=False,
        actor="test-operator",
        private_key=private,
        key_id="test-author",
        trusted_publics=trusted,
        admitted_names=set(),
        **kwargs,
    ), receipts, root


def test_default_is_read_only_and_does_not_load_signing_key(
        tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "executors"
    source = _orphan(root)
    monkeypatch.setattr(
        cleanup, "load_private",
        lambda _name: (_ for _ in ()).throw(AssertionError("must not load key")),
    )

    report = cleanup.quarantine(root=root, admitted_names=set())

    assert report["dry_run"] is True
    assert [item["name"] for item in report["candidates"]] == ["find_things"]
    assert source.is_dir()


def test_apply_writes_verified_receipt_before_same_filesystem_rename(
        tmp_path: Path, keys) -> None:
    root = tmp_path / "executors"
    source = _orphan(root)
    observed = []

    def before_rename(path: Path) -> None:
        receipt_path = tmp_path / "state" / "receipts.jsonl"
        observed.append(receipt_path.is_file() and path.is_dir())

    result, receipts, root = _apply(tmp_path, keys, before_rename=before_rename)

    assert observed == [True]
    assert not source.exists()
    destination = Path(result["quarantined"][0])
    assert destination.parent.stat().st_dev == root.stat().st_dev
    assert (destination / "find_things.py").is_file()
    rows = [json.loads(line) for line in receipts.read_text().splitlines()]
    assert len(rows) == 1
    signer = cleanup.verify_receipt(rows[0], keys[1])
    assert signer.name == "test-author"
    assert str(tmp_path) not in json.dumps(rows[0])


def test_mutation_after_receipt_prevents_rename(tmp_path: Path, keys) -> None:
    root = tmp_path / "executors"
    source = _orphan(root)

    def mutate(path: Path) -> None:
        (path / "find_things.py").write_text("changed\n", encoding="utf-8")

    with pytest.raises(cleanup.OrphanCleanupError, match="candidate_changed"):
        _apply(tmp_path, keys, before_rename=mutate)

    assert source.is_dir()
    assert not (root / "quarantine-test").joinpath(
        "find_things--anything",
    ).exists()


def test_admitted_name_fails_before_receipt_or_move(tmp_path: Path) -> None:
    root = tmp_path / "executors"
    source = _orphan(root)

    with pytest.raises(cleanup.OrphanCleanupError, match="candidate_is_admitted"):
        cleanup.quarantine(
            root=root,
            receipts_path=tmp_path / "receipts.jsonl",
            dry_run=False,
            actor="operator",
            admitted_names={"find_things"},
        )

    assert source.is_dir()
    assert not (tmp_path / "receipts.jsonl").exists()


@pytest.mark.parametrize("extra", ["manifest.toml", "other.py"])
def test_manifest_or_extra_file_is_rejected_not_quarantined(
        tmp_path: Path, extra: str) -> None:
    root = tmp_path / "executors"
    source = _orphan(root)
    (source / extra).write_text("x", encoding="utf-8")

    candidates, rejected = cleanup.discover(root)

    assert candidates == []
    assert rejected == [{
        "name": "find_things", "error": "candidate_not_incomplete_orphan",
    }]


def test_symlink_file_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "executors"
    directory = root / "find_things"
    directory.mkdir(parents=True)
    target = tmp_path / "target.py"
    target.write_text("pass\n", encoding="utf-8")
    (directory / "find_things.py").symlink_to(target)

    candidates, rejected = cleanup.discover(root)

    assert candidates == []
    assert rejected[0]["error"] == "candidate_file_forbidden"


def test_hard_link_file_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "executors"
    directory = root / "find_things"
    directory.mkdir(parents=True)
    target = tmp_path / "target.py"
    target.write_text("pass\n", encoding="utf-8")
    (directory / "find_things.py").hardlink_to(target)

    candidates, rejected = cleanup.discover(root)

    assert candidates == []
    assert rejected[0]["error"] == "candidate_file_forbidden"


def test_tampered_receipt_signature_is_rejected(tmp_path: Path, keys) -> None:
    _orphan(tmp_path / "executors")
    _result, receipts, _root = _apply(tmp_path, keys)
    receipt = json.loads(receipts.read_text())
    receipt["payload"]["size"] += 1

    with pytest.raises(Exception):
        cleanup.verify_receipt(receipt, keys[1])


def test_retry_after_receipt_before_rename_is_idempotent(
        tmp_path: Path, keys) -> None:
    root = tmp_path / "executors"
    _orphan(root)

    def interrupt(_path: Path) -> None:
        raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError, match="simulated crash"):
        _apply(tmp_path, keys, before_rename=interrupt)

    result, receipts, _root = _apply(tmp_path, keys)
    assert len(receipts.read_text().splitlines()) == 1
    assert len(result["quarantined"]) == 1


def test_declared_signer_must_match_verified_key(tmp_path: Path, keys) -> None:
    root = tmp_path / "executors"
    item = cleanup.discover(_orphan(root).parent)[0][0]
    payload = cleanup._payload(item, actor="operator")
    receipt = cleanup._signed_receipt(
        payload, private_key=keys[0], key_id="different-key",
    )

    with pytest.raises(cleanup.OrphanCleanupError, match="receipt_signer_mismatch"):
        cleanup.verify_receipt(receipt, keys[1])


def test_actor_is_required_before_any_write(tmp_path: Path, keys) -> None:
    root = tmp_path / "executors"
    source = _orphan(root)

    with pytest.raises(cleanup.OrphanCleanupError, match="actor_required"):
        cleanup.quarantine(
            root=root, receipts_path=tmp_path / "receipts.jsonl",
            dry_run=False, admitted_names=set(), private_key=keys[0],
            trusted_publics=keys[1],
        )

    assert source.exists()
    assert not (tmp_path / "receipts.jsonl").exists()


def test_symlink_quarantine_root_is_rejected_before_receipt(
        tmp_path: Path, keys) -> None:
    root = tmp_path / "executors"
    source = _orphan(root)
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    quarantine_root = root / "quarantine-test"
    quarantine_root.symlink_to(redirected, target_is_directory=True)
    private, trusted = keys
    receipts = tmp_path / "receipts.jsonl"

    with pytest.raises(cleanup.OrphanCleanupError, match="quarantine_link_forbidden"):
        cleanup.quarantine(
            root=root, receipts_path=receipts,
            quarantine_root=quarantine_root, dry_run=False,
            actor="test-operator", private_key=private,
            key_id="test-author", trusted_publics=trusted,
            admitted_names=set(),
        )

    assert source.exists()
    assert not receipts.exists()


def test_destination_conflict_is_rejected_before_receipt(
        tmp_path: Path, keys) -> None:
    root = tmp_path / "executors"
    source = _orphan(root)
    item = cleanup.discover(root)[0][0]
    quarantine_root = root / "quarantine-test"
    quarantine_root.mkdir()
    destination = quarantine_root / f"find_things--{item['sha256'][:16]}"
    destination.mkdir()
    private, trusted = keys
    receipts = tmp_path / "receipts.jsonl"

    with pytest.raises(cleanup.OrphanCleanupError, match="quarantine_conflict"):
        cleanup.quarantine(
            root=root, receipts_path=receipts,
            quarantine_root=quarantine_root, dry_run=False,
            actor="test-operator", private_key=private,
            key_id="test-author", trusted_publics=trusted,
            admitted_names=set(),
        )

    assert source.exists()
    assert not receipts.exists()
