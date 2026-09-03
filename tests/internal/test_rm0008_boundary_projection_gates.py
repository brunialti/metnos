"""The standalone boundary projection is mandatory before pinning or publish."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check_contract_boundary_policy.py"
REPIN = ROOT / "internal/tools/rm0008_repin_source_roots.py"
PUBLISHER = ROOT / "scripts/publish-public.sh"
RUNTIME_FILES = (
    "contract_boundary_analyzer_ast.py",
    "contract_boundary_analyzer_projection.py",
    "contract_boundary_analyzer_types.py",
    "contract_boundary_api_policy.py",
    "contract_boundary_birth_authority_policy.py",
    "contract_boundary_birth_exception_policy.py",
    "contract_boundary_birth_policy.py",
    "contract_boundary_policy.py",
    "contract_boundary_policy_types.py",
    "contract_boundary_projection.py",
    "contract_boundary_role_policy.py",
    "contract_boundary_syntax_policy.py",
    "executor_birth_admin_preflight.py",
    "executor_birth_canonical.py",
    "executor_birth_crypto_framing.py",
)


def _load_repin():
    spec = importlib.util.spec_from_file_location("rm0008_repin_test", REPIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _isolated_checker(tree: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-S", str(tree / "scripts" / CHECKER.name)],
        cwd=tree, check=False, capture_output=True, text=True,
    )


def _copy_minimal_tree(target: Path) -> None:
    (target / "runtime").mkdir(parents=True)
    (target / "scripts").mkdir()
    shutil.copy2(CHECKER, target / "scripts" / CHECKER.name)
    for name in RUNTIME_FILES:
        shutil.copy2(ROOT / "runtime" / name, target / "runtime" / name)


def test_trusted_checker_accepts_current_projection_in_isolation() -> None:
    checked = _isolated_checker(ROOT)
    assert checked.returncode == 0, checked.stderr


def test_trusted_checker_rejects_stale_projection_in_isolation(
    tmp_path: Path,
) -> None:
    _copy_minimal_tree(tmp_path)
    policy = tmp_path / "runtime/contract_boundary_syntax_policy.py"
    source = policy.read_text(encoding="utf-8")
    policy.write_text(source.replace("2_048", "2_049", 1), encoding="utf-8")
    checked = _isolated_checker(tmp_path)
    assert checked.returncode == 1
    assert checked.stderr.strip() == "contract_boundary_projection_error:stale"


def test_trusted_checker_rejects_stale_analyzer_projection_in_isolation(
    tmp_path: Path,
) -> None:
    _copy_minimal_tree(tmp_path)
    owner = tmp_path / "runtime/contract_boundary_analyzer_ast.py"
    source = owner.read_text(encoding="utf-8")
    owner.write_text(
        source.replace("observed import or alias", "observed canonical alias", 1),
        encoding="utf-8",
    )
    checked = _isolated_checker(tmp_path)
    assert checked.returncode == 1
    assert checked.stderr.strip() == "contract_boundary_projection_error:stale"


@pytest.mark.parametrize(
    "owner_name",
    ("contract_boundary_analyzer_ast.py", "contract_boundary_analyzer_types.py"),
)
def test_trusted_checker_binds_unprojected_owner_bytes_in_isolation(
    tmp_path: Path, owner_name: str,
) -> None:
    _copy_minimal_tree(tmp_path)
    owner = tmp_path / "runtime" / owner_name
    owner.write_bytes(owner.read_bytes() + b"# trust-path byte probe\n")
    checked = _isolated_checker(tmp_path)
    assert checked.returncode == 1
    assert checked.stderr.strip() == "contract_boundary_projection_error:stale"


@pytest.mark.parametrize("case", ("unexpected_assignment", "altered_import"))
def test_trusted_checker_rejects_owner_dependency_drift_in_isolation(
    tmp_path: Path, case: str,
) -> None:
    _copy_minimal_tree(tmp_path)
    owner = tmp_path / "runtime/contract_boundary_analyzer_ast.py"
    source = owner.read_bytes()
    if case == "unexpected_assignment":
        source += b"ast = object()\n"
    else:
        source = source.replace(b"import ast\n", b"import os\n", 1)
    owner.write_bytes(source)
    checked = _isolated_checker(tmp_path)
    assert checked.returncode == 2
    assert checked.stderr.strip() == (
        "contract_boundary_projection_error:owner_profile"
    )


def test_trusted_checker_rejects_external_analyzer_name_collision(
    tmp_path: Path,
) -> None:
    _copy_minimal_tree(tmp_path)
    target = tmp_path / "runtime/executor_birth_admin_preflight.py"
    target.write_bytes(target.read_bytes() + b"_leaf_name = object()\n")
    checked = _isolated_checker(tmp_path)
    assert checked.returncode == 2
    assert checked.stderr.strip() == (
        "contract_boundary_projection_error:external_name_collision"
    )


@pytest.mark.parametrize("case", ("missing", "duplicate"))
def test_trusted_checker_rejects_invalid_analyzer_markers_in_isolation(
    tmp_path: Path, case: str,
) -> None:
    _copy_minimal_tree(tmp_path)
    target = tmp_path / "runtime/executor_birth_admin_preflight.py"
    source = target.read_bytes()
    marker = b"# BEGIN GENERATED CONTRACT BOUNDARY ANALYZER V1\n"
    if case == "missing":
        source = source.replace(marker, b"", 1)
    else:
        source += marker
    target.write_bytes(source)
    checked = _isolated_checker(tmp_path)
    assert checked.returncode == 2
    assert checked.stderr.strip() == "contract_boundary_projection_error:markers"


def test_repin_stops_before_review_when_projection_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_repin()
    reviewed = False

    def refuse(_tree: Path) -> None:
        raise SystemExit("projection refused")

    def review(_tree: Path):
        nonlocal reviewed
        reviewed = True

    monkeypatch.setattr(module, "_require_policy_projection", refuse)
    monkeypatch.setattr(module, "_review_module", review)
    with pytest.raises(SystemExit, match="projection refused"):
        module.main([str(REPIN), str(ROOT)])
    assert not reviewed


def test_publication_checks_projection_before_private_source_root() -> None:
    source = PUBLISHER.read_text(encoding="utf-8")
    private_projection = source.index(
        '"$PYTHON" -I -S "$BOUNDARY_POLICY_CHECKER"',
    )
    private = source.index('source_review_gate \\\n  private-fs')
    public_projection = source.index(
        '"$PYTHON" -I -S "$DEST/scripts/check_contract_boundary_policy.py"',
    )
    public = source.index("public-fs-pin")
    assert private_projection < private
    assert public_projection < public
