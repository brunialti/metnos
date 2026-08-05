from __future__ import annotations

from backends.files import local
from executors.compute_files_loc.compute_files_loc import invoke as compute_loc
from executors.create_images_indices.create_images_indices import invoke as create_index
from executors.list_dirs.list_dirs import _mtime_iso, invoke as list_dirs


def test_find_files_counts_beyond_display_cap(tmp_path):
    for index in range(1005):
        (tmp_path / f"file-{index:04d}.txt").write_text(
            str(index), encoding="utf-8")

    out = local.find({
        "base_path": str(tmp_path),
        "patterns": ["*.txt"],
        "recursive": True,
        "max_results": 2,
    })

    assert out["ok"] is True
    assert len(out["entries"]) == 2
    assert out["truncated"] is True
    assert out["available_total"] == 1005
    assert out["metadata"]["source_complete"] is True


def test_list_dirs_sorts_complete_set_before_display_cap(tmp_path):
    (tmp_path / "small.bin").write_bytes(b"x")
    (tmp_path / "medium.bin").write_bytes(b"xx")
    (tmp_path / "large.bin").write_bytes(b"xxx")

    out = list_dirs({
        "path": str(tmp_path),
        "sort": "size",
        "max_results": 2,
    })

    assert [entry["name"] for entry in out["entries"]] == [
        "large.bin", "medium.bin",
    ]
    assert out["available_total"] == 3


def test_list_dirs_counts_each_entry_type_before_display_cap(tmp_path):
    (tmp_path / "one.txt").write_text("one", encoding="utf-8")
    (tmp_path / "two.txt").write_text("two", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "one.txt")

    out = list_dirs({"path": str(tmp_path), "max_results": 1})

    assert out["ok"] is True
    assert out["metadata"]["file_count_total"] == 2
    assert out["metadata"]["dir_count_total"] == 1
    assert out["metadata"]["symlink_count_total"] == 1
    assert out["metadata"]["available_total"] == 4
    presentation = out["authoritative_presentation"]
    assert presentation["scope"] == "count"
    assert presentation["covers_truncation"] is True
    assert "file: 2; directory: 1; collegamenti simbolici: 1" in \
        presentation["text"]
    assert "L’elenco mostra 1 dei 4 elementi" in presentation["text"]


def test_list_dirs_keeps_entry_when_mtime_is_outside_datetime_range():
    assert _mtime_iso(10 ** 20) is None


def test_list_dirs_zero_depth_means_unbounded(tmp_path):
    nested = tmp_path / "one" / "two"
    nested.mkdir(parents=True)
    (nested / "deep.txt").write_text("x", encoding="utf-8")

    out = list_dirs({
        "path": str(tmp_path), "recursive": True,
        "max_depth": 0, "max_results": 0,
    })

    assert out["ok"] is True
    assert any(entry["name"] == "deep.txt" for entry in out["entries"])


def test_find_dirs_aggregates_all_dirs_before_display_cap(tmp_path):
    (tmp_path / "root.txt").write_text("root", encoding="utf-8")
    for name in ("a", "b"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / f"{name}.txt").write_text(name, encoding="utf-8")

    out = local.find_dirs({
        "base_path": str(tmp_path),
        "recursive": True,
        "max_results": 1,
    })

    assert out["ok"] is True
    assert len(out["entries"]) == 1
    assert out["metadata"]["available_dirs"] == 2
    assert out["metadata"]["file_count_total"] == 3
    assert out["available_total"] == 2


def test_compute_loc_uses_recursive_parallel_walk(tmp_path):
    nested = tmp_path / "src" / "deep"
    nested.mkdir(parents=True)
    (tmp_path / "root.py").write_text("one\n", encoding="utf-8")
    (nested / "deep.py").write_text("two\nthree\n", encoding="utf-8")

    out = compute_loc({"paths": [str(tmp_path)], "include_ext": [".py"]})

    assert out["ok"] is True
    assert out["total_files"] == 2
    assert out["total_lines"] == 3


def test_image_index_dry_run_scans_nested_corpus_once(tmp_path):
    nested = tmp_path / "year" / "month"
    nested.mkdir(parents=True)
    (tmp_path / "one.JPG").write_bytes(b"not-decoded-in-dry-run")
    (nested / "two.png").write_bytes(b"not-decoded-in-dry-run")
    (nested / "note.txt").write_text("note", encoding="utf-8")

    out = create_index({
        "base_path": str(tmp_path),
        "recursive": True,
        "dry_run": True,
    })

    assert out["ok"] is True
    assert out["would_index_count"] == 2
    assert out["n_other_files"] == 1
    assert out["visited_dirs"] == 3
