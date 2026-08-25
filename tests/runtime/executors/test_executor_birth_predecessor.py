from pathlib import Path

from executor_birth_predecessor import (
    derive_revision_facts, predecessor_snapshot, revision_facts_id,
)
from executor_birth_snapshot import CandidateSnapshot


def _candidate(manifest: bytes, state: bytes = b"{}") -> CandidateSnapshot:
    return CandidateSnapshot(Path("/private"), manifest, state, {"x.py": b"pass\n"})


def test_revision_facts_are_canonical_and_ignore_toml_formatting() -> None:
    old = b'name="x"\ndescription="old"\n[code]\nfiles=["x.py"]\ndigest="sha256:aaa"\n'
    new = b'name = "x"\ndescription = "new"\n[code]\nfiles = ["x.py"]\ndigest = "sha256:aaa"\n'
    payloads = {"manifest.toml": old, "manifest.lang_state.json": b"{}"}
    predecessor = predecessor_snapshot("sha256:" + "1" * 64, "generation", payloads)

    first = derive_revision_facts(predecessor, payloads, _candidate(new))
    second = derive_revision_facts(predecessor, dict(reversed(tuple(payloads.items()))), _candidate(new))

    assert first.linguistic_surface_changed
    assert first.semantic_core_unchanged
    assert revision_facts_id(first) == revision_facts_id(second)


def test_predecessor_snapshot_hash_is_mapping_order_independent() -> None:
    left = predecessor_snapshot(
        "sha256:" + "2" * 64, "generation", {"b": b"2", "a": b"1"},
    )
    right = predecessor_snapshot(
        "sha256:" + "2" * 64, "generation", {"a": b"1", "b": b"2"},
    )
    assert left.snapshot_id == right.snapshot_id
