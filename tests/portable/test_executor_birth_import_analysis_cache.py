"""Cache only syntax; import resolution must observe each live filesystem."""
from pathlib import Path

import pytest

import executor_birth_admin_preflight as preflight
import executor_birth_distribution_manifest as distribution
import contract_boundary_guard as boundary


@pytest.fixture(params=[preflight, distribution])
def backend(request):
    module = request.param
    module._analyze_local_imports_v1.cache_clear()
    yield module
    module._analyze_local_imports_v1.cache_clear()


def verify(backend, root: Path, path: str, source: bytes) -> None:
    item_type = (preflight.DistributionFileV1 if backend is preflight
                 else distribution.DistributionFile)
    item = item_type(
        path, len(source), preflight.distribution_file_hash_v1(path, source),
        "runtime_code",
    )
    closure = (preflight._verify_local_import_closure_v1 if backend is preflight
               else distribution._verify_local_import_closure)
    closure(root, (item,), {path: source})


def test_same_bytes_reuse_only_syntax(tmp_path, monkeypatch, backend):
    original = preflight.ast.parse
    calls = []

    def parse(*args, **kwargs):
        calls.append(kwargs.get("filename"))
        return original(*args, **kwargs)

    monkeypatch.setattr(preflight.ast, "parse", parse)
    for _ in range(2):
        verify(backend, tmp_path, "runtime/probe.py", b"import missing_dependency\n")
    assert calls == ["runtime/probe.py"]
    assert backend._analyze_local_imports_v1.cache_info().currsize == 1


@pytest.mark.parametrize("another_root", [False, True])
def test_warm_syntax_never_hides_new_uncovered_import(tmp_path, another_root, backend):
    source = b"import hidden_dependency\n"
    verify(backend, tmp_path, "runtime/probe.py", source)
    observed = tmp_path / "other" if another_root else tmp_path
    observed.mkdir(exist_ok=True)
    (observed / "hidden_dependency.py").write_bytes(b"VALUE = 1\n")
    with pytest.raises((preflight.PreflightError, distribution.DistributionManifestError),
                       match="uncovered local import"):
        verify(backend, observed, "runtime/probe.py", source)


def test_changed_bytes_are_reanalyzed(tmp_path, backend):
    verify(backend, tmp_path, "runtime/probe.py", b"VALUE = 1\n")
    with pytest.raises((preflight.PreflightError, distribution.DistributionManifestError),
                       match="dynamic code loader"):
        verify(backend, tmp_path, "runtime/probe.py", b"exec('VALUE = 1')\n")


def test_path_is_part_of_syntax_authority(tmp_path, backend):
    source = b"def load_admitted_module_v1(payload):\n exec(payload)\n"
    verify(backend, tmp_path, "runtime/admitted_module_v1.py", source)
    with pytest.raises((preflight.PreflightError, distribution.DistributionManifestError),
                       match="dynamic code loader"):
        verify(backend, tmp_path, "runtime/untrusted.py", source)


def test_cache_evicts_previous_candidate(tmp_path, backend):
    for path in ("runtime/first.py", "runtime/second.py", "runtime/first.py"):
        verify(backend, tmp_path, path, b"VALUE = 1\n")
    info = backend._analyze_local_imports_v1.cache_info()
    assert (info.hits, info.misses, info.currsize) == (0, 3, 1)


@pytest.mark.parametrize("owner,limit", [
    (boundary, "MAX_BOUNDARY_AST_NODES"),
    (distribution, "MAX_BOUNDARY_TOTAL_AST_NODES_V1"),
])
def test_runtime_warm_cache_obeys_tightened_ast_budget(tmp_path, monkeypatch, owner, limit):
    distribution._analyze_local_imports_v1.cache_clear()
    verify(distribution, tmp_path, "runtime/probe.py", b"VALUE = 1\n")
    monkeypatch.setattr(owner, limit, 1)
    with pytest.raises(distribution.DistributionManifestError, match="python source"):
        verify(distribution, tmp_path, "runtime/probe.py", b"VALUE = 1\n")
