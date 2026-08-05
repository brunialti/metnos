from __future__ import annotations

from pathlib import Path

from parallel_walk import parallel_map_ordered, parallel_walk


def _files(path: Path, kind: str, _depth: int) -> bool:
    return kind == "file" and path.suffix == ".txt"


def test_complete_walk_is_recursive_and_deterministic(tmp_path):
    for relative in ("z/last.txt", "a/first.txt", "a/deep/middle.txt"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")

    result = parallel_walk(tmp_path, accept=_files)

    assert [path.relative_to(tmp_path).as_posix() for path in result.items] == [
        "a/deep/middle.txt", "a/first.txt", "z/last.txt",
    ]
    assert result.source_complete is True
    assert result.visited_dirs == 4


def test_callbacks_transform_and_prune_subtrees(tmp_path):
    for relative in ("keep/a.txt", "skip/b.txt"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    result = parallel_walk(
        tmp_path,
        accept=_files,
        descend=lambda path, _depth: path.name != "skip",
        transform=lambda path, _kind, depth, entry: (
            path.name, depth, entry.stat(follow_symlinks=False).st_size),
    )

    assert result.items == [("a.txt", 2, 1)]


def test_positive_cap_is_breadth_first_and_honest(tmp_path):
    (tmp_path / "root.txt").write_text("root", encoding="utf-8")
    for relative in ("a/one.txt", "b/two.txt"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")

    result = parallel_walk(tmp_path, accept=_files, max_items=1)

    assert result.items == [tmp_path / "root.txt"]
    assert result.truncated is True
    assert result.source_complete is False


def test_depth_zero_is_empty_and_none_is_unlimited(tmp_path):
    path = tmp_path / "deep" / "file.txt"
    path.parent.mkdir()
    path.write_text("x", encoding="utf-8")

    assert parallel_walk(tmp_path, accept=_files, max_depth=0).items == []
    assert parallel_walk(
        tmp_path, accept=_files, max_depth=None).items == [path]


def test_symlink_is_returned_only_when_selected_and_never_followed(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "inside.txt").write_text("x", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)

    result = parallel_walk(
        tmp_path,
        accept=lambda _path, kind, _depth: kind in ("file", "symlink"),
    )

    assert link in result.items
    assert result.items.count(target / "inside.txt") == 1


def test_parallel_map_preserves_input_order():
    assert parallel_map_ordered(
        [3, 1, 2], lambda value: value * 10, workers=3) == [30, 10, 20]


def test_explicit_worker_request_cannot_raise_runtime_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("METNOS_EXECUTOR_ASSIGNED_WORKERS", "1")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")

    result = parallel_walk(tmp_path, accept=_files, workers=32)

    assert result.workers == 1
