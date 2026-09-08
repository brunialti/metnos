"""Cache only syntax; import resolution must observe each live filesystem."""
from pathlib import Path

import pytest

import executor_birth_admin_preflight as preflight


@pytest.fixture(autouse=True)
def empty_cache():
    preflight._analyze_local_imports_v1.cache_clear()
    yield
    preflight._analyze_local_imports_v1.cache_clear()


def verify(root: Path, path: str, source: bytes) -> None:
    item = preflight.DistributionFileV1(
        path, len(source), preflight.distribution_file_hash_v1(path, source),
        "runtime_code",
    )
    preflight._verify_local_import_closure_v1(root, (item,), {path: source})


def test_same_bytes_reuse_only_syntax(tmp_path, monkeypatch):
    original = preflight.ast.parse
    calls = []

    def parse(*args, **kwargs):
        calls.append(kwargs.get("filename"))
        return original(*args, **kwargs)

    monkeypatch.setattr(preflight.ast, "parse", parse)
    for _ in range(2):
        verify(tmp_path, "runtime/probe.py", b"import missing_dependency\n")
    assert calls == ["runtime/probe.py"]
    assert preflight._analyze_local_imports_v1.cache_info().currsize == 1


@pytest.mark.parametrize("another_root", [False, True])
def test_warm_syntax_never_hides_new_uncovered_import(tmp_path, another_root):
    source = b"import hidden_dependency\n"
    verify(tmp_path, "runtime/probe.py", source)
    observed = tmp_path / "other" if another_root else tmp_path
    observed.mkdir(exist_ok=True)
    (observed / "hidden_dependency.py").write_bytes(b"VALUE = 1\n")
    with pytest.raises(preflight.PreflightError, match="uncovered local import"):
        verify(observed, "runtime/probe.py", source)


def test_changed_bytes_are_reanalyzed(tmp_path):
    verify(tmp_path, "runtime/probe.py", b"VALUE = 1\n")
    with pytest.raises(preflight.PreflightError, match="dynamic code loader"):
        verify(tmp_path, "runtime/probe.py", b"exec('VALUE = 1')\n")


def test_path_is_part_of_syntax_authority(tmp_path):
    source = b"def load_admitted_module_v1(payload):\n exec(payload)\n"
    verify(tmp_path, "runtime/admitted_module_v1.py", source)
    with pytest.raises(preflight.PreflightError, match="dynamic code loader"):
        verify(tmp_path, "runtime/untrusted.py", source)


def test_cache_evicts_previous_candidate(tmp_path):
    for path in ("runtime/first.py", "runtime/second.py", "runtime/first.py"):
        verify(tmp_path, path, b"VALUE = 1\n")
    info = preflight._analyze_local_imports_v1.cache_info()
    assert (info.hits, info.misses, info.currsize) == (0, 3, 1)
