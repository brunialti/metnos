"""Boundary facts may be reused; filesystem observations must remain fresh."""
import pytest

import contract_boundary_guard as guard


SOURCE = b"def inspect(manifest_path):\n return manifest_path.read_text()\n"


@pytest.fixture(autouse=True)
def empty_cache():
    guard._discover_source_facts.cache_clear()
    yield
    guard._discover_source_facts.cache_clear()


def source_file(root, name="probe.py", content=SOURCE):
    path = root / "runtime" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_identical_bytes_reuse_syntax_across_roots_without_mutable_results(tmp_path, monkeypatch):
    source_file(tmp_path)
    other = tmp_path / "other"
    source_file(other)
    original = guard.ast.parse
    calls = []

    def parse(*args, **kwargs):
        calls.append(kwargs.get("filename"))
        return original(*args, **kwargs)

    monkeypatch.setattr(guard.ast, "parse", parse)
    first = guard.discover(tmp_path)
    assert first
    expected = tuple(first)
    first.clear()
    assert tuple(guard.discover(other)) == expected
    assert calls == ["runtime/probe.py"]
    assert guard._discover_source_facts.cache_info().currsize == 1


def test_warm_cache_observes_changed_bytes_and_added_removed_files(tmp_path):
    path = source_file(tmp_path)
    assert guard.discover(tmp_path)
    path.write_bytes(b"VALUE = 1\n")
    assert guard.discover(tmp_path) == []
    added = source_file(tmp_path, "added.py")
    assert {fact.path for fact in guard.discover(tmp_path)} == {"runtime/added.py"}
    added.unlink()
    assert guard.discover(tmp_path) == []


def test_source_path_participates_in_cache_key(tmp_path):
    path = source_file(tmp_path)
    guard.discover(tmp_path)
    path.rename(path.with_name("renamed.py"))
    assert {fact.path for fact in guard.discover(tmp_path)} == {"runtime/renamed.py"}


@pytest.mark.parametrize("limit", [
    "MAX_BOUNDARY_SOURCE_FILES", "MAX_BOUNDARY_SOURCE_BYTES",
    "MAX_BOUNDARY_TOTAL_SOURCE_BYTES", "MAX_BOUNDARY_AST_NODES",
    "MAX_BOUNDARY_AST_DEPTH", "MAX_BOUNDARY_SCOPES",
    "MAX_BOUNDARY_CALLS", "MAX_BOUNDARY_TOTAL_AST_NODES",
])
def test_warm_cache_obeys_every_tightened_budget(tmp_path, monkeypatch, limit):
    source_file(tmp_path)
    guard.discover(tmp_path)
    monkeypatch.setattr(guard, limit, 0)
    with pytest.raises(ValueError):
        guard.discover(tmp_path)


def test_warm_cache_does_not_hide_failed_reads(tmp_path, monkeypatch):
    source_file(tmp_path)
    guard.discover(tmp_path)

    def refuse(*args, **kwargs):
        raise OSError("unreadable")

    monkeypatch.setattr(type(tmp_path), "open", refuse)
    with pytest.raises(ValueError, match="cannot read boundary source"):
        guard.discover(tmp_path)


def test_cache_keeps_only_one_candidate(tmp_path):
    for name in ("first", "second", "first"):
        root = tmp_path / name
        source_file(root, name + ".py")
        guard.discover(root)
    info = guard._discover_source_facts.cache_info()
    assert (info.hits, info.misses, info.currsize) == (0, 3, 1)
