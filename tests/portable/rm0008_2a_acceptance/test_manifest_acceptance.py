"""R1 and G6 acceptance cells owned by the platform-independent activity."""
from __future__ import annotations

import ast
import importlib
import inspect
import os
from pathlib import Path

import pytest

from .certification_v1 import (
    ACTIVITIES,
    CertificationError,
    FINAL_EVIDENCE_FIELDS,
    INVENTORY_PATH,
    MANIFEST_PATH,
    REPO_ROOT,
    SUITE_ID,
    canonical_json_bytes,
    collect_a_node_ids,
    digest_file,
    select_cells,
    validate_collection,
    validate_final_evidence,
    validate_manifest,
    validate_no_skip_xfail,
    validate_production_inventory,
    validate_productive_mutation_graph,
    validate_snapshot_aggregate,
    validate_workflow_structure,
)
from .generate_production_inventory_v1 import build_production_inventory_v1
from .required_cells_v1 import EXPECTED_ACTIVITY_COUNTS_V1, REQUIRED_CELLS_V1


@pytest.mark.parametrize("slug", ["installer-only-entry"], ids=["installer-only-entry"])
def test_r1_installer_entry(slug: str) -> None:
    module = importlib.import_module("install.birth_authority_provisioning")
    entry = getattr(module, "open_birth_provisioning_layout_v1")
    assert inspect.isfunction(entry)
    assert entry.__module__ == "install.birth_authority_provisioning"
    assert tuple(inspect.signature(entry).parameters) == ()


@pytest.mark.parametrize(
    "slug",
    ["descriptor-immutable-single-consumption"],
    ids=["descriptor-immutable-single-consumption"],
)
def test_r1_descriptor_immutable_single_consumption(
    slug: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    secure_fs = importlib.import_module("runtime.executor_birth_secure_fs")
    descriptor_type = getattr(secure_fs, "_AuthenticatedRootDescriptor")
    identity = secure_fs._PlatformIdentity(posix_uid=os.geteuid(), windows_service_sid=None)
    sentinel_handle = 987_654_321
    descriptor = descriptor_type(
        handles=(sentinel_handle,),
        root_path="/rm0008-a-only-sentinel",
        identity=identity,
    )
    assert not hasattr(descriptor, "__dict__")
    assert not hasattr(descriptor, "_adopted")
    for field, replacement in (
        ("handles", (sentinel_handle + 1,)),
        ("root_path", "/changed"),
        ("identity", secure_fs._PlatformIdentity(posix_uid=0, windows_service_sid=None)),
    ):
        with pytest.raises((AttributeError, TypeError)):
            setattr(descriptor, field, replacement)

    sessions: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_session(*arguments: object, **keywords: object) -> object:
        sessions.append((arguments, keywords))
        return object()

    monkeypatch.setattr(secure_fs, "_SecureRootSession", fake_session)
    first = secure_fs._adopt_authenticated_root(descriptor)
    assert first is not None
    assert descriptor.handles == (sentinel_handle,)
    with pytest.raises(secure_fs.BirthSecureFSError) as error:
        secure_fs._adopt_authenticated_root(descriptor)
    assert error.value.code == "birth_provisioning_io_unavailable"
    assert len(sessions) == 1


@pytest.mark.parametrize(
    "slug",
    ["productive-graph-no-mutating-capability"],
    ids=["productive-graph-no-mutating-capability"],
)
def test_r1_productive_graph_no_mutating_capability(slug: str) -> None:
    inventory = validate_production_inventory(enforce_filesystem=False)
    validate_productive_mutation_graph(inventory)
    baseline = _r1_graph_mutant_sources()
    validate_productive_mutation_graph(_source_mutant=baseline)

    top_level_alias_escape = dict(baseline)
    top_level_alias_escape["runtime/escape.py"] = """
import executor_birth_secure_fs as secure_fs
def exposed(session):
    return secure_fs.dispose_transaction_object(session)
"""
    in_function_alias_escape = dict(baseline)
    in_function_alias_escape["runtime/escape.py"] = """
def exposed():
    import executor_birth_secure_fs as secure_fs
    alias = secure_fs._adopt_authenticated_root
    return alias
"""
    second_session_factory = dict(baseline)
    second_session_factory["runtime/executor_birth_secure_fs.py"] += """
def _second_factory():
    return _SecureRootSession(_SESSION_TOKEN)
"""
    orphan_installer_helper = dict(baseline)
    orphan_installer_helper["install/birth_authority_provisioning.py"] += """
def _orphan_mutator(session):
    return session.dispose_transaction_object(None)
"""
    direct_legacy_session = dict(baseline)
    direct_legacy_session["runtime/executor_birth_secure_fs.py"] = direct_legacy_session[
        "runtime/executor_birth_secure_fs.py"
    ].replace(
        "return _LegacyReadSession(_LEGACY_TOKEN, session)",
        "return session",
    )
    bound_method_escape = dict(baseline)
    bound_method_escape["runtime/escape.py"] = """
def exposed(session):
    return session.dispose_transaction_object
"""
    token_alias_escape = dict(baseline)
    token_alias_escape["runtime/executor_birth_secure_fs.py"] += """
def _token_escape():
    alias = _SESSION_TOKEN
    return alias
"""
    for mutant in (
        top_level_alias_escape,
        in_function_alias_escape,
        second_session_factory,
        orphan_installer_helper,
        direct_legacy_session,
        bound_method_escape,
        token_alias_escape,
    ):
        with pytest.raises(CertificationError):
            validate_productive_mutation_graph(_source_mutant=mutant)


def _r1_graph_mutant_sources() -> dict[str, str]:
    return {
        "runtime/executor_birth_secure_fs.py": """
_SESSION_TOKEN = object()
_LEGACY_TOKEN = object()
class _AuthenticatedRootDescriptor:
    pass
class _SecureRootSession:
    def __init__(self, token):
        if token is not _SESSION_TOKEN:
            raise TypeError
    def create_file_exclusive(self):
        return None
    def create_directory_exclusive(self):
        return None
    def rename_no_replace(self):
        return None
    def dispose_transaction_object(self):
        return None
class _LegacyReadSession:
    def __init__(self, token, session):
        self.session = session
def _open_legacy_root_session():
    session = _SecureRootSession(_SESSION_TOKEN)
    return _LegacyReadSession(_LEGACY_TOKEN, session)
def _adopt_authenticated_root(descriptor):
    return _SecureRootSession(_SESSION_TOKEN)
""",
        "install/birth_authority_provisioning.py": """
import executor_birth_secure_fs as secure_fs
def open_birth_provisioning_layout_v1():
    descriptor = secure_fs._AuthenticatedRootDescriptor()
    return secure_fs._adopt_authenticated_root(descriptor)
""",
    }


@pytest.mark.parametrize("slug", ["schema-canonical"], ids=["schema-canonical"])
def test_g6_schema_canonical(slug: str) -> None:
    manifest = validate_manifest()
    assert len(manifest["cells"]) == 248
    assert MANIFEST_PATH.read_bytes()[-1:] != b"\n"


@pytest.mark.parametrize(
    "slug", ["required-cell-inventory"], ids=["required-cell-inventory"]
)
def test_g6_required_cell_inventory(slug: str) -> None:
    assert len(REQUIRED_CELLS_V1) == 248
    assert len(set(REQUIRED_CELLS_V1)) == 248
    counts = {
        activity: sum(cell[1] == activity for cell in REQUIRED_CELLS_V1)
        for activity in ACTIVITIES
    }
    assert counts == EXPECTED_ACTIVITY_COUNTS_V1
    source_path = Path(
        inspect.getsourcefile(importlib.import_module(".required_cells_v1", __package__))
        or ""
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "REQUIRED_CELLS_V1"
    )
    assert isinstance(assignment.value, ast.Tuple)
    assert len(assignment.value.elts) == 248
    assert all(isinstance(item, ast.Tuple) and len(item.elts) == 5 for item in assignment.value.elts)
    validate_manifest()


@pytest.mark.parametrize("slug", ["production-inventory"], ids=["production-inventory"])
def test_g6_production_inventory(slug: str) -> None:
    assert INVENTORY_PATH.read_bytes() == canonical_json_bytes(
        build_production_inventory_v1()
    )
    inventory = validate_production_inventory()
    assert inventory["inventory_id"] == "rm-0008-production-python"


@pytest.mark.parametrize("slug", ["collection-exact"], ids=["collection-exact"])
def test_g6_collection_exact(slug: str) -> None:
    manifest = validate_manifest()
    assert validate_collection(manifest) == collect_a_node_ids()


@pytest.mark.parametrize("slug", ["no-skip-xfail"], ids=["no-skip-xfail"])
def test_g6_no_skip_xfail(slug: str) -> None:
    validate_no_skip_xfail()


@pytest.mark.parametrize("slug", ["activity-selection"], ids=["activity-selection"])
def test_g6_activity_selection(slug: str) -> None:
    manifest = validate_manifest()
    selected = {activity: select_cells(manifest, activity) for activity in ACTIVITIES}
    assert {activity: len(cells) for activity, cells in selected.items()} == EXPECTED_ACTIVITY_COUNTS_V1
    assert sum(len(cells) for cells in selected.values()) == 248
    assert all(cell["activity"] == activity for activity, cells in selected.items() for cell in cells)


@pytest.mark.parametrize("slug", ["evidence-schema"], ids=["evidence-schema"])
def test_g6_evidence_schema(slug: str) -> None:
    manifest = validate_manifest()
    cells = select_cells(manifest, "manifest")
    evidence = {
        "schema_version": 1,
        "suite_id": SUITE_ID,
        "git_sha": "0" * 40,
        "manifest_sha256": digest_file(MANIFEST_PATH),
        "production_inventory_sha256": "sha256:" + "1" * 64,
        "runner_image": "ubuntu-24.04",
        "activity": "manifest",
        "results": [
            {"node_id": cell["node_id"], "outcome": "passed"} for cell in cells
        ],
    }
    assert set(evidence) == FINAL_EVIDENCE_FIELDS
    validate_final_evidence(evidence, manifest, "manifest")
    invalid = dict(evidence)
    invalid["unexpected"] = True
    with pytest.raises(CertificationError):
        validate_final_evidence(invalid, manifest, "manifest")


@pytest.mark.parametrize("slug", ["pre-fix-snapshot"], ids=["pre-fix-snapshot"])
def test_g6_pre_fix_snapshot(slug: str) -> None:
    snapshot = validate_snapshot_aggregate()
    assert snapshot["suite_id"] == SUITE_ID
    assert len(snapshot["results"]) == 248


@pytest.mark.parametrize("slug", ["workflow-dependency"], ids=["workflow-dependency"])
def test_g6_workflow_dependency(slug: str) -> None:
    validate_workflow_structure()
    conftest = REPO_ROOT / "tests/windows_identity/conftest.py"
    assert "pytest_sessionfinish" not in conftest.read_text(encoding="utf-8")
