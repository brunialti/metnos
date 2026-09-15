from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import contract_boundary_guard as boundary_guard

from contract_boundary_guard import (
    BIRTH_CLOSED_GUARD_VERSION,
    BIRTH_CLOSED_COORDINATOR_STORE_OWNERS,
    BIRTH_CLOSED_EXCEPTION_CAPABILITIES,
    BIRTH_CLOSED_EXCEPTION_SCOPES,
    BIRTH_CLOSED_OWNER,
    BIRTH_CLOSED_SCHEMA,
    BIRTH_CLOSED_SEALED_MODULES,
    BIRTH_CLOSED_SOURCE_REVIEW_SHA256,
    SCAN_ROOTS,
    SCHEMA,
    ScopeFacts,
    birth_migration_findings,
    birth_closed_findings,
    check,
    closed_python_source_review_sha256,
    closed_python_sources_from_root,
    discover,
    render_birth_closed_inventory,
    render_inventory,
    scan_file,
)


def _scan(tmp_path: Path, source: str, *, relative: str = "runtime/sample.py"):
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return discover(tmp_path)


def _inventory(
    facts: list[ScopeFacts],
    roles: dict[str, str] | None = None,
) -> dict:
    roles = roles or {}
    return {
        "schema": SCHEMA,
        "scan_roots": list(SCAN_ROOTS),
        "entries": [
            {
                "path": fact.path,
                "scope": fact.scope,
                "role": roles.get(fact.scope, "offline_authoring"),
                "capabilities": list(fact.capabilities),
                "destination": "test",
                "phase": "M4",
            }
            for fact in facts
        ],
    }


def _closed_inventory(
    facts: list[ScopeFacts],
    *,
    exceptions: dict[str, str] | None = None,
) -> dict:
    payload = _inventory(facts)
    payload["source_census"] = BIRTH_CLOSED_SOURCE_REVIEW_SHA256
    exceptions = exceptions or {}
    for entry in payload["entries"]:
        key = f"{entry['path']}:{entry['scope']}"
        compiled = BIRTH_CLOSED_EXCEPTION_SCOPES.get(key)
        entry["role"] = "birth_owner" if key == BIRTH_CLOSED_OWNER else (
            "store_owner" if key in BIRTH_CLOSED_COORDINATOR_STORE_OWNERS
            else
            "offline_authoring" if compiled == "offline_nonproductive_authoring"
            else "operational_producer" if compiled is not None
            else "offline_authoring" if key in exceptions
            else "administrative_tool"
        )
        compiled_exception = BIRTH_CLOSED_EXCEPTION_SCOPES.get(key)
        if compiled_exception is not None:
            entry["closed_exception"] = compiled_exception
        if key in exceptions:
            entry["closed_exception"] = exceptions[key]
    payload["birth_closed"] = {
        "schema": BIRTH_CLOSED_SCHEMA,
        "guard_version": BIRTH_CLOSED_GUARD_VERSION,
        "owner": BIRTH_CLOSED_OWNER,
        "coordinator_store_owners": sorted(BIRTH_CLOSED_COORDINATOR_STORE_OWNERS),
        "sealed_modules": list(BIRTH_CLOSED_SEALED_MODULES),
        "exceptions": [
            {"scope": scope, "exception": exception}
            for scope, exception in sorted(BIRTH_CLOSED_EXCEPTION_SCOPES.items())
        ],
    }
    return payload


def _codes(findings) -> set[str]:
    return {finding.code for finding in findings}


def test_closed_python_source_review_pin_rejects_a_nominal_door_replacement():
    root = Path(__file__).resolve().parents[3]
    sources = closed_python_sources_from_root(root)

    assert (
        closed_python_source_review_sha256(sources)
        == BIRTH_CLOSED_SOURCE_REVIEW_SHA256
    )
    altered = dict(sources)
    altered["runtime/admitted_module_v1.py"] = (
        b"def load_admitted_module_v1(executor):\n    exec(executor)\n"
    )

    assert (
        closed_python_source_review_sha256(altered)
        != BIRTH_CLOSED_SOURCE_REVIEW_SHA256
    )


def _fact(facts: list[ScopeFacts], scope: str) -> ScopeFacts:
    return next(item for item in facts if item.scope == scope)


def test_new_boundary_scope_must_be_classified(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "from pathlib import Path\n"
        "def inspect(manifest_path: Path):\n"
        "    return manifest_path.read_text(encoding='utf-8')\n",
    )

    findings = check(
        facts,
        {"schema": SCHEMA, "scan_roots": list(SCAN_ROOTS), "entries": []},
    )

    assert _codes(findings) == {"unclassified_boundary_scope"}
    assert findings[0].scope.endswith(":inspect")


def test_offline_authoring_and_documentation_are_legal(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "from pathlib import Path\n"
        "def rewrite(manifest_path: Path):\n"
        "    manifest_path.write_text('draft', encoding='utf-8')\n"
        "def document(manifest_path: Path):\n"
        "    return manifest_path.read_text(encoding='utf-8')\n",
    )
    inventory = _inventory(
        facts,
        {"rewrite": "offline_authoring", "document": "documentation"},
    )

    assert check(facts, inventory) == []


def test_live_reader_cannot_reopen_authoring_even_read_only(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "from pathlib import Path\n"
        "def inspect(manifest_path: Path):\n"
        "    return manifest_path.read_text(encoding='utf-8')\n",
    )
    inventory = _inventory(facts, {"inspect": "live_reader"})

    findings = check(facts, inventory)

    assert "live_reader_uses_authoring" in _codes(findings)


def test_manifest_ref_locator_requires_the_versioned_reader(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "def inspect(ref):\n"
        "    return (ref.manifest_dir / 'manifest.toml').read_bytes()\n",
    )
    inventory = _inventory(facts, {"inspect": "administrative_tool"})

    findings = check(facts, inventory)

    assert "direct_manifest_dir_read_without_token" in _codes(findings)


def test_versioned_manifest_ref_reader_hides_authoring_locator(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "from executor_birth_authoring import read_manifest_ref_versioned\n"
        "def inspect(ref):\n"
        "    return read_manifest_ref_versioned(\n"
        "        ref, ('manifest.toml',), timeout=1.0)\n",
    )
    inventory = _inventory(facts, {"inspect": "administrative_tool"})

    assert _fact(facts, "inspect").capabilities == (
        "authoring_versioned_read",
    )
    assert check(facts, inventory) == []


def test_direct_manifest_ref_locator_exceptions_are_narrow(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "def offline(ref):\n"
        "    return (ref.manifest_dir / 'manifest.toml').read_bytes()\n"
        "def migration(ref):\n"
        "    return (ref.manifest_dir / 'manifest.toml').read_bytes()\n"
        "def owner(ref):\n"
        "    return (ref.manifest_dir / 'manifest.toml').read_bytes()\n",
    )
    inventory = _inventory(facts, {
        "offline": "offline_authoring",
        "migration": "migration_boundary",
        "owner": "store_owner",
    })

    # Migration scopes normally also carry the legacy-bootstrap capability.
    # This focused assertion exercises only the direct-locator exception.
    findings = check(facts, inventory)
    assert _codes(findings) == {"migration_boundary_not_constrained"}


def test_publication_store_write_is_forbidden_outside_owner(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "from pathlib import Path\n"
        "def mutate(publication_store_root: Path):\n"
        "    (publication_store_root / 'current').write_text('sha256:bad')\n",
    )
    inventory = _inventory(facts)

    findings = check(facts, inventory)

    assert "store_write_outside_boundary" in _codes(findings)


def test_keyword_paths_cannot_bypass_authoring_or_store_detection(
    tmp_path: Path,
) -> None:
    facts = _scan(
        tmp_path,
        "import os\n"
        "def rewrite(manifest_path, publication_store_root, temporary):\n"
        "    open(file=manifest_path, mode='w').close()\n"
        "    os.replace(src=temporary, dst=publication_store_root / 'current')\n",
    )

    assert _fact(facts, "rewrite").capabilities == (
        "authoring_read", "authoring_write", "store_write",
    )


def test_private_store_mutator_cannot_hide_behind_generic_path(
    tmp_path: Path,
) -> None:
    facts = _scan(
        tmp_path,
        "import contract_store as store\n"
        "def mutate(path, payload):\n"
        "    store._commit_payloads_locked(path, payload)\n",
    )
    inventory = _inventory(facts)

    findings = check(facts, inventory)

    assert "store_write_outside_boundary" in _codes(findings)


@pytest.mark.parametrize(
    "api", ("_deployment_lock_at_v1", "_deployment_lock_for_test_v1"),
)
def test_private_deployment_lock_seams_are_store_boundaries(
    tmp_path: Path, api: str,
) -> None:
    facts = _scan(
        tmp_path,
        "from executor_birth_ownership_coordinator import " + api + "\n"
        "def mutate(root):\n"
        "    return " + api + "(root, root_owned=False)\n",
    )
    inventory = _inventory(facts, {"mutate": "operational_producer"})

    assert _fact(facts, "mutate").capabilities == ("store_write",)
    assert "store_write_outside_boundary" in _codes(check(facts, inventory))


@pytest.mark.parametrize("api", (
    "_ACTIVE_DEPLOYMENT_LOCK_LEASES_V1",
    "_ACTIVE_DEPLOYMENT_LOCK_SESSIONS_V1",
    "_DEPLOYMENT_LOCK_FORK_GUARD",
    "_DeploymentLockLeaseV1",
    "_OPEN_DEPLOYMENT_LOCK_FDS_V1",
))
def test_private_deployment_lock_state_cannot_escape_boundary(
    tmp_path: Path, api: str,
) -> None:
    facts = _scan(
        tmp_path,
        "from executor_birth_ownership_coordinator import " + api + "\n"
        "def mutate():\n"
        "    return " + api + "\n",
    )
    inventory = _inventory(facts, {"mutate": "operational_producer"})

    assert _fact(facts, "mutate").capabilities == ("store_write",)
    assert "store_write_outside_boundary" in _codes(check(facts, inventory))


@pytest.mark.parametrize("api", (
    "_InitialOwnershipChainStateV1",
    "_inspect_ownership_chain_state_core_v1",
    "_mint_initial_ownership_chain_state_v1",
))
def test_private_initial_chain_authority_cannot_escape_boundary(
    tmp_path: Path, api: str,
) -> None:
    facts = _scan(
        tmp_path,
        "from executor_birth_ownership_chain import " + api + "\n"
        "def mutate(root):\n"
        "    return " + api + "\n",
    )
    inventory = _inventory(facts, {"mutate": "operational_producer"})

    assert _fact(facts, "mutate").capabilities == ("store_write",)
    assert "store_write_outside_boundary" in _codes(check(facts, inventory))


def test_operational_sign_is_found_through_a_local_helper(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "from sign import sign_executor\n"
        "def prepare(directory):\n"
        "    sign_executor(directory)\n"
        "def install(directory):\n"
        "    prepare(directory)\n",
    )
    inventory = _inventory(
        facts,
        {"prepare": "offline_authoring", "install": "operational_producer"},
    )

    findings = check(facts, inventory)

    assert any(
        finding.code == "operational_sign_after_cutover"
        and finding.scope.endswith(":install")
        for finding in findings
    )


def test_operational_sign_subprocess_is_not_a_bypass(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "import subprocess\n"
        "def install(python, sign_script):\n"
        "    subprocess.run([python, sign_script, 'sign-all'], check=True)\n",
    )
    inventory = _inventory(facts, {"install": "operational_producer"})

    findings = check(facts, inventory)

    assert "operational_sign_after_cutover" in _codes(findings)


def test_module_sign_entrypoint_and_generic_manifest_path_are_not_bypasses(
    tmp_path: Path,
) -> None:
    facts = _scan(
        tmp_path,
        "import subprocess\n"
        "def refactor_manifest(path):\n"
        "    path.write_text('draft', encoding='utf-8')\n"
        "    subprocess.run(['python3', '-m', 'runtime.sign', 'sign', str(path.parent)])\n",
    )
    inventory = _inventory(facts, {"refactor_manifest": "operational_producer"})

    fact = _fact(facts, "refactor_manifest")
    assert fact.capabilities == ("authoring_write", "sign")
    assert _codes(check(facts, inventory)) == {
        "operational_sign_after_cutover",
        "operational_write_without_publish",
    }


def test_operational_sign_alias_is_not_a_bypass(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "from sign import sign_executor\n"
        "def install(directory):\n"
        "    finish = sign_executor\n"
        "    finish(directory)\n",
    )
    inventory = _inventory(facts, {"install": "operational_producer"})

    assert "operational_sign_after_cutover" in _codes(check(facts, inventory))


def test_operational_authoring_write_requires_publisher(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "from pathlib import Path\n"
        "def install(manifest_path: Path):\n"
        "    manifest_path.write_text('draft', encoding='utf-8')\n",
    )
    inventory = _inventory(facts, {"install": "operational_producer"})

    findings = check(facts, inventory)

    assert "operational_write_without_publish" in _codes(findings)


def test_operational_authoring_can_use_the_single_technical_publisher(
    tmp_path: Path,
) -> None:
    facts = _scan(
        tmp_path,
        "from pathlib import Path\n"
        "from contract_store import publish_technical_update\n"
        "def install(manifest_path: Path, draft):\n"
        "    manifest_path.write_text('draft', encoding='utf-8')\n"
        "    return publish_technical_update(draft)\n",
    )
    inventory = _inventory(facts, {"install": "operational_producer"})

    assert check(facts, inventory) == []


def test_shadow_inventory_reports_direct_technical_publication_as_birth_debt(
    tmp_path: Path,
) -> None:
    facts = _scan(
        tmp_path,
        "from contract_store import publish_technical_update\n"
        "def install(ref, draft):\n"
        "    return publish_technical_update(ref, draft=draft)\n",
    )
    inventory = _inventory(facts, {"install": "operational_producer"})

    findings = birth_migration_findings(facts, inventory)

    assert [(item.code, item.scope) for item in findings] == [
        ("birth_migration_required", "runtime/sample.py:install"),
    ]


def test_operational_producer_may_use_only_the_public_birth_boundary(
    tmp_path: Path,
) -> None:
    facts = _scan(
        tmp_path,
        "from pathlib import Path\n"
        "from executor_birth import birth_executor\n"
        "def install(manifest_path: Path, request):\n"
        "    manifest_path.write_text('candidate', encoding='utf-8')\n"
        "    return birth_executor(request)\n",
    )
    inventory = _inventory(facts, {"install": "operational_producer"})

    assert _fact(facts, "install").capabilities == ("authoring_write", "birth")
    assert check(facts, inventory) == []
    assert birth_migration_findings(facts, inventory) == []


def test_migrated_producer_cannot_keep_low_level_publication_authority(
    tmp_path: Path,
) -> None:
    facts = _scan(
        tmp_path,
        "from executor_birth import birth_executor\n"
        "from contract_store import publish_technical_update\n"
        "def install(request, ref, draft):\n"
        "    birth_executor(request)\n"
        "    return publish_technical_update(ref, draft=draft)\n",
    )
    inventory = _inventory(facts, {"install": "operational_producer"})

    assert "operational_birth_mixed_authority" in _codes(check(facts, inventory))


def test_birth_owner_is_path_restricted_and_cannot_absorb_other_boundaries(
    tmp_path: Path,
) -> None:
    wrong_path = _scan(
        tmp_path / "wrong",
        "from contract_store import publish_technical_update\n"
        "def commit(ref, draft): return publish_technical_update(ref, draft=draft)\n",
    )
    wrong_inventory = _inventory(wrong_path, {"commit": "birth_owner"})
    assert "birth_owner_invalid" in _codes(check(wrong_path, wrong_inventory))

    mixed = _scan(
        tmp_path / "mixed",
        "from contract_store import publish_technical_update, publish_localization\n"
        "def commit(ref, draft):\n"
        "    publish_localization(ref)\n"
        "    return publish_technical_update(ref, draft=draft)\n",
        relative="runtime/executor_birth.py",
    )
    mixed_inventory = _inventory(mixed, {"commit": "birth_owner"})
    assert "birth_owner_invalid" in _codes(check(mixed, mixed_inventory))


def test_birth_shadow_debt_excludes_retire_localization_and_migration_boundaries(
    tmp_path: Path,
) -> None:
    facts = _scan(
        tmp_path,
        "from contract_store import publish_localization, retire, publish_signed_source\n"
        "def localize(ref): return publish_localization(ref)\n"
        "def remove(ref): return retire(ref)\n"
        "def bootstrap(ref): return publish_signed_source(ref)\n",
    )
    inventory = _inventory(
        facts,
        {
            "localize": "operational_producer",
            "remove": "operational_producer",
            "bootstrap": "migration_boundary",
        },
    )

    assert birth_migration_findings(facts, inventory) == []


def test_technical_rollback_alias_is_birth_migration_debt(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "from contract_store import rollback as restore_generation\n"
        "def restore(ref, current, target):\n"
        "    operation = restore_generation\n"
        "    return operation(ref, expected_generation_id=current, "
        "target_generation_id=target)\n",
    )
    inventory = _inventory(facts, {"restore": "operational_producer"})

    assert _fact(facts, "restore").capabilities == ("rollback",)
    findings = birth_migration_findings(facts, inventory)
    assert [(item.code, item.scope) for item in findings] == [
        ("birth_migration_required", "runtime/sample.py:restore"),
    ]


def test_reflective_technical_rollback_is_birth_migration_debt(
    tmp_path: Path,
) -> None:
    facts = _scan(
        tmp_path,
        "import contract_store as store\n"
        "def restore(ref, current, target):\n"
        "    operation = getattr(store, 'rollback')\n"
        "    return operation(ref, expected_generation_id=current, "
        "target_generation_id=target)\n",
    )
    inventory = _inventory(facts, {"restore": "operational_producer"})

    assert _fact(facts, "restore").capabilities == ("rollback",)
    assert len(birth_migration_findings(facts, inventory)) == 1


def test_same_leaf_module_cannot_impersonate_the_publisher(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "from pathlib import Path\n"
        "from evil.contract_store import publish_technical_update\n"
        "def install(manifest_path: Path, draft):\n"
        "    manifest_path.write_text('draft', encoding='utf-8')\n"
        "    return publish_technical_update(draft)\n",
    )
    inventory = _inventory(facts, {"install": "operational_producer"})

    assert _fact(facts, "install").capabilities == ("authoring_write",)
    assert "operational_write_without_publish" in _codes(check(facts, inventory))


def test_rebound_boundary_symbol_is_ambiguous_and_fails_closed(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "from pathlib import Path\n"
        "from contract_store import publish_technical_update\n"
        "def fake(*args): return None\n"
        "def install(manifest_path: Path):\n"
        "    publish_technical_update = fake\n"
        "    manifest_path.write_text('draft')\n"
        "    publish_technical_update(None)\n",
    )
    inventory = _inventory(facts, {"install": "operational_producer"})

    assert _fact(facts, "install").capabilities == (
        "ambiguous_local_authority", "authoring_write", "publish_technical",
    )
    assert "ambiguous_local_authority" in _codes(check(facts, inventory))


@pytest.mark.parametrize("expression", [
    "globals().get('__builtins__').get('__import__')('runtime.sign')",
    "getattr(sys.modules.get('builtins'), '__import__')('runtime.sign')",
])
def test_nested_reflective_import_access_fails_closed(
    tmp_path: Path, expression: str,
) -> None:
    facts = _scan(
        tmp_path,
        "import sys\n"
        "def mutate():\n"
        f"    return {expression}\n",
    )
    inventory = _inventory(facts, {"mutate": "administrative_tool"})

    assert "dynamic_boundary_access" in _fact(facts, "mutate").capabilities
    assert "dynamic_boundary_access" in _codes(check(facts, inventory))


def test_reflective_boundary_access_fails_closed(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "import contract_store\n"
        "def mutate():\n"
        "    return getattr(contract_store, 'not_a_public_boundary')\n",
    )
    inventory = _inventory(facts, {"mutate": "administrative_tool"})

    assert _fact(facts, "mutate").capabilities == ("dynamic_boundary_access",)
    assert "dynamic_boundary_access" in _codes(check(facts, inventory))


def test_dynamic_import_and_star_import_fail_closed(tmp_path: Path) -> None:
    dynamic = _scan(
        tmp_path / "dynamic",
        "def mutate():\n"
        "    return __import__('contract_store')\n",
    )
    star = _scan(
        tmp_path / "star",
        "from contract_store import *\n"
        "def mutate(): return publish_technical_update\n",
    )

    for facts in (dynamic, star):
        inventory = _inventory(facts, {"mutate": "administrative_tool"})
        assert "dynamic_boundary_access" in _codes(check(facts, inventory))


def test_relative_boundary_import_fails_closed(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "from .contract_store import publish_technical_update\n"
        "def mutate(): return publish_technical_update\n",
    )
    inventory = _inventory(facts, {"mutate": "administrative_tool"})

    assert "dynamic_boundary_access" in _codes(check(facts, inventory))


def test_nested_scope_inherits_trusted_outer_alias(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "def outer():\n"
        "    from contract_store import current_manifest\n"
        "    reader = current_manifest\n"
        "    def inner(ref):\n"
        "        return reader(ref)\n"
        "    return inner\n",
    )

    assert _fact(facts, "outer.inner").capabilities == ("verified_store_read",)


def test_ambiguous_same_named_helper_fails_closed(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "class A:\n"
        "    def change(self, manifest_path):\n"
        "        manifest_path.write_text('draft')\n"
        "class B:\n"
        "    def change(self, value):\n"
        "        return value\n"
        "def install(owner, manifest_path):\n"
        "    owner.change(manifest_path)\n",
    )
    inventory = _inventory(facts, {"install": "administrative_tool"})

    assert "ambiguous_local_authority" in _fact(
        facts, "install",
    ).capabilities
    assert "ambiguous_local_authority" in _codes(check(facts, inventory))


def test_super_initializer_does_not_resolve_to_an_unrelated_local_method(
    tmp_path: Path,
) -> None:
    facts = _scan(
        tmp_path,
        "class LocalError(RuntimeError):\n"
        "    def __init__(self):\n"
        "        super().__init__('local')\n"
        "class Journal:\n"
        "    def __init__(self, store_root):\n"
        "        (store_root / 'record').write_text('value')\n",
    )

    assert all(fact.scope != "LocalError.__init__" for fact in facts)
    assert _fact(facts, "Journal.__init__").capabilities == ("store_write",)


def test_local_helper_alias_preserves_its_authority(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "from pathlib import Path\n"
        "def replace_source(manifest_path: Path):\n"
        "    manifest_path.write_text('draft', encoding='utf-8')\n"
        "def install(manifest_path: Path):\n"
        "    writer = replace_source\n"
        "    writer(manifest_path)\n",
    )
    inventory = _inventory(
        facts,
        {"replace_source": "offline_authoring", "install": "operational_producer"},
    )

    assert _fact(facts, "install").capabilities == ("authoring_write",)
    assert "operational_write_without_publish" in _codes(check(facts, inventory))


def test_nested_scope_inherits_boundary_import_from_enclosing_scope(
    tmp_path: Path,
) -> None:
    facts = _scan(
        tmp_path,
        "def validate():\n"
        "    from sign import verify_executor\n"
        "    def validator(directory):\n"
        "        return verify_executor(directory)\n"
        "    return validator\n",
    )

    assert _fact(facts, "validate.validator").capabilities == (
        "authoring_read", "authoring_verify",
    )


def test_first_class_boundary_callable_keeps_its_capability(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "from functools import partial\n"
        "from contract_store import current_contract, publish_localization\n"
        "def callbacks(ref):\n"
        "    return partial(current_contract, ref), partial(publish_localization, ref)\n",
    )

    assert _fact(facts, "callbacks").capabilities == (
        "publish_localization", "verified_store_read",
    )


_CONVERGENCE_CAPABILITIES = (
    "authoring_read", "authoring_write", "birth", "store_write",
    "verified_store_read",
)


def test_contract_convergence_import_alias_and_first_class_are_classified(
    tmp_path: Path,
) -> None:
    aliased = _scan(
        tmp_path / "aliased",
        "import install.executor_birth_contract_convergence as convergence\n"
        "def run(): return convergence.converge()\n",
    )
    first_class = _scan(
        tmp_path / "first-class",
        "from install.executor_birth_contract_convergence import converge as run_now\n"
        "def callback(): return run_now\n",
    )
    assert _fact(aliased, "run").capabilities == _CONVERGENCE_CAPABILITIES
    assert _fact(first_class, "callback").capabilities == (
        _CONVERGENCE_CAPABILITIES
    )


def test_local_converge_name_does_not_gain_contract_authority(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "def converge(value): return value\n"
        "def inspect(manifest_path):\n"
        "    return converge(manifest_path.read_text(encoding='utf-8'))\n",
    )
    assert _fact(facts, "inspect").capabilities == ("authoring_read",)


def _contract_convergence_inventory(root: Path, facts: list[ScopeFacts]) -> dict:
    payload = json.loads(
        (root / "internal/reports/rm0007-m4-boundary-inventory.json").read_text()
    )
    relative = "install/executor_birth_contract_convergence.py"
    payload["entries"] = [
        entry for entry in payload["entries"] if entry["path"] == relative
    ]
    assert len(payload["entries"]) == 5
    assert {fact.scope for fact in facts if fact.capabilities} == {
        entry["scope"] for entry in payload["entries"]
    }
    return payload


def test_contract_convergence_birth_and_source_owner_are_guarded_mutants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[3]
    relative = "install/executor_birth_contract_convergence.py"
    path = root / relative
    baseline = scan_file(path, repository_root=root)
    inventory = _contract_convergence_inventory(root, baseline)
    assert check(baseline, inventory) == []

    owner = "executor_birth_contract_convergence"
    without_birth = {
        name: tuple(cap for cap in capabilities if cap != "birth")
        for name, capabilities in boundary_guard.BOUNDARY_APIS[owner].items()
    }
    with monkeypatch.context() as scoped:
        scoped.setitem(boundary_guard.BOUNDARY_APIS, owner, without_birth)
        observed = scan_file(path, repository_root=root)
        assert "birth" not in _fact(observed, "converge").capabilities
        assert "boundary_scope_changed" in _codes(check(observed, inventory))

    with monkeypatch.context() as scoped:
        # Replacing the mapping preserves the shared policy's insertion order.
        scoped.setattr(boundary_guard, "BOUNDARY_SOURCE_OWNERS", {
            path: owner for path, owner in boundary_guard.BOUNDARY_SOURCE_OWNERS.items()
            if path != relative
        })
        observed = scan_file(path, repository_root=root)
        assert "birth" not in _fact(observed, "converge").capabilities
        assert "boundary_scope_changed" in _codes(check(observed, inventory))


def test_legacy_bootstrap_requires_an_explicit_migration_boundary(
    tmp_path: Path,
) -> None:
    facts = _scan(
        tmp_path,
        "from contract_store import activate_store\n"
        "def activate(expected, shadow, trusted, proof):\n"
        "    return activate_store(expected, shadow_root=shadow, "
        "trusted_publics=trusted, quiescence_guard=proof)\n",
    )
    unsafe = _inventory(facts, {"activate": "operational_producer"})

    findings = check(facts, unsafe)

    assert _fact(facts, "activate").capabilities == ("legacy_bootstrap",)
    assert "legacy_bootstrap_outside_boundary" in _codes(findings)
    assert check(
        facts, _inventory(facts, {"activate": "migration_boundary"}),
    ) == []


def test_first_party_executor_cannot_read_its_authoring_manifest(
    tmp_path: Path,
) -> None:
    facts = _scan(
        tmp_path,
        "from pathlib import Path\n"
        "def invoke(args):\n"
        "    manifest = Path(__file__).with_name('manifest.toml')\n"
        "    return {'raw': manifest.read_text(encoding='utf-8')}\n",
        relative="executors/demo/demo.py",
    )
    inventory = _inventory(facts, {"invoke": "live_reader"})

    findings = check(facts, inventory)

    assert "executors" in SCAN_ROOTS
    assert _fact(facts, "invoke").capabilities == ("authoring_read",)
    assert "live_reader_uses_authoring" in _codes(findings)


def test_filename_filter_taints_the_filesystem_traversal(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "def inventory(root):\n"
        "    roots_and_names = [(root, {'manifest.toml'})]\n"
        "    found = []\n"
        "    for base, names in roots_and_names:\n"
        "        for path in base.rglob('*'):\n"
        "            if path.name in names:\n"
        "                found.append(path.read_bytes())\n"
        "    return found\n",
    )

    assert _fact(facts, "inventory").capabilities == ("authoring_read",)


def test_local_helper_cannot_disguise_a_publisher_bypass(tmp_path: Path) -> None:
    facts = _scan(
        tmp_path,
        "from pathlib import Path\n"
        "def replace_source(manifest_path: Path):\n"
        "    manifest_path.write_text('draft', encoding='utf-8')\n"
        "def pretend_publish():\n"
        "    return True\n"
        "def update(manifest_path: Path):\n"
        "    replace_source(manifest_path)\n"
        "    return pretend_publish()\n",
    )
    inventory = _inventory(
        facts,
        {
            "replace_source": "offline_authoring",
            "update": "operational_producer",
        },
    )

    findings = check(facts, inventory)

    assert _fact(facts, "update").capabilities == ("authoring_write",)
    assert "operational_write_without_publish" in _codes(findings)


def test_live_reader_cannot_retire_or_reactivate_contracts(
    tmp_path: Path,
) -> None:
    facts = _scan(
        tmp_path,
        "from sign import retire_executor_contract, reactivate_executor_contract\n"
        "def retire_contract(directory):\n"
        "    return retire_executor_contract(directory, actor='a', reason='r')\n"
        "def reactivate_contract(directory):\n"
        "    return reactivate_executor_contract(directory, actor='a', reason='r')\n",
    )
    inventory = _inventory(
        facts,
        {
            "retire_contract": "live_reader",
            "reactivate_contract": "live_reader",
        },
    )

    findings = check(facts, inventory)

    assert _fact(facts, "retire_contract").capabilities == ("retire",)
    assert _fact(facts, "reactivate_contract").capabilities == ("reactivate",)
    assert _codes(findings) == {
        "live_mutation_role_invalid",
        "live_reader_uses_authoring",
    }


def test_live_reader_may_use_verified_store_and_artifact_boundaries(
    tmp_path: Path,
) -> None:
    facts = _scan(
        tmp_path,
        "from contract_store import current_manifest\n"
        "from invocations import load_executor_artifact\n"
        "def serve(ref, name, trusted):\n"
        "    snapshot = current_manifest(ref, trusted_publics=trusted)\n"
        "    artifact = load_executor_artifact(name)\n"
        "    return snapshot, artifact\n",
    )
    inventory = _inventory(facts, {"serve": "live_reader"})

    assert _fact(facts, "serve").capabilities == (
        "live_artifact_read", "verified_store_read",
    )
    assert check(facts, inventory) == []


def test_all_live_publication_variants_are_distinct_capabilities(
    tmp_path: Path,
) -> None:
    facts = _scan(
        tmp_path,
        "from contract_store import (publish_localization, publish_signed_source, "
        "publish_technical_update, reactivate_technical_update, retire, rollback)\n"
        "def mutate(a, b, c):\n"
        "    publish_localization(a, b)\n"
        "    publish_signed_source(a, b)\n"
        "    publish_technical_update(a, b)\n"
        "    reactivate_technical_update(a, b)\n"
        "    retire(a, b)\n"
        "    rollback(a, b, c)\n",
    )

    assert _fact(facts, "mutate").capabilities == (
        "publish_bootstrap",
        "publish_localization",
        "publish_technical",
        "reactivate",
        "retire",
        "rollback",
    )


def test_private_store_write_requires_reviewed_store_owner(
    tmp_path: Path,
) -> None:
    facts = _scan(
        tmp_path,
        "from pathlib import Path\n"
        "def write_current(publication_store_root: Path):\n"
        "    (publication_store_root / 'current').write_text('sha256:ok')\n",
        relative="runtime/contract_store.py",
    )

    assert _fact(facts, "write_current").capabilities == ("store_write",)
    assert check(
        facts, _inventory(facts, {"write_current": "store_owner"}),
    ) == []


def test_render_is_deterministic_and_never_auto_approves_new_scope(
    tmp_path: Path,
) -> None:
    facts = _scan(
        tmp_path,
        "from pathlib import Path\n"
        "def inspect(manifest_path: Path):\n"
        "    return manifest_path.read_bytes()\n",
    )

    first = render_inventory(facts)
    second = render_inventory(facts)
    payload = json.loads(first)

    assert first == second
    assert payload["entries"][0]["role"] == "unclassified"
    assert payload["entries"][0]["destination"] == "review-required"


def test_repository_boundary_inventory_is_current() -> None:
    root = Path(__file__).resolve().parents[3]
    inventory_path = root / "internal/reports/rm0007-m4-boundary-inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))

    findings = check(discover(root), inventory)

    assert findings == [], "\n".join(str(finding) for finding in findings)


def test_repository_birth_migration_debt_is_exact() -> None:
    root = Path(__file__).resolve().parents[3]
    inventory_path = root / "internal/reports/rm0007-m4-boundary-inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))

    scopes = {
        finding.scope
        for finding in birth_migration_findings(discover(root), inventory)
    }

    assert scopes == {
        "runtime/sign.py:<module>",
        "runtime/sign.py:main",
        "runtime/sign.py:publish_authoring_update",
        "runtime/sign.py:publish_executor",
        "runtime/sign.py:reactivate_executor_contract",
        "runtime/sign.py:rollback_executor_contract",
    }


def _closed_facts(
    tmp_path: Path,
    extra_source: str = "",
    *,
    relative: str = "runtime/sample.py",
) -> list[ScopeFacts]:
    _scan(
        tmp_path,
        "def birth_executor(request): return request\n",
        relative="runtime/executor_birth_operational.py",
    )
    if extra_source:
        _scan(tmp_path, extra_source, relative=relative)
    _scan(
        tmp_path,
        "from pathlib import Path\n"
        "def _required_head_lock(path):\n"
        "    Path('x').write_bytes(b'x')\n"
        "def _replace_required_pointer(path):\n"
        "    Path('x').write_bytes(b'x')\n"
        "class OwnershipChainStore:\n"
        "    def initialize(self, path):\n"
        "        Path('x').write_bytes(b'x')\n"
        "    def _append_pair(self, path):\n"
        "        Path('x').write_bytes(b'x')\n"
        "    def append_authenticated_build(self, path):\n"
        "        Path('x').write_bytes(b'x')\n"
        "    def append_cutover(self, path):\n"
        "        Path('x').write_bytes(b'x')\n"
        "    def append_head(self, path):\n"
        "        Path('x').write_bytes(b'x')\n"
        "    def update_required_head(self, path):\n"
        "        Path('x').write_bytes(b'x')\n"
        "    def _update_required_head_locked(self, path):\n"
        "        Path('x').write_bytes(b'x')\n",
        relative="runtime/executor_birth_ownership_chain.py",
    )
    _scan(
        tmp_path,
        "def _discard_temporary(path): return path\n"
        "def _sync_directory(path): return path\n"
        "def _write_exclusive(path, payload, mode): return path\n"
        "def _publish_no_replace(source, destination): return destination\n"
        "def _load_or_create_pair(path, kind): return path\n"
        "def _provision_ownership_authorities_at_v1(path): return path\n"
        "def _provision_ownership_authorities_locked_v1(path): return path\n"
        "def _provisioning_lock(path): return path\n"
        "def provision_root_ownership_authorities_v1(value): return value\n",
        relative="install/birth_ownership_authority_provisioner.py",
    )
    facts = discover(tmp_path)
    present = {fact.key for fact in facts}
    for key in BIRTH_CLOSED_COORDINATOR_STORE_OWNERS:
        if key in present:
            continue
        path, scope = key.split(":", 1)
        facts.append(ScopeFacts(path, scope, 1, ("store_write",), ()))
        present.add(key)
    for key, exception in BIRTH_CLOSED_EXCEPTION_SCOPES.items():
        if key in present:
            continue
        path, scope = key.split(":", 1)
        capabilities = tuple(sorted(BIRTH_CLOSED_EXCEPTION_CAPABILITIES[key]))
        facts.append(ScopeFacts(path, scope, 1, capabilities, ()))
    return sorted(facts, key=lambda fact: (fact.path, fact.scope))


def test_birth_closed_requires_exactly_the_compiled_owner(tmp_path: Path) -> None:
    facts = _closed_facts(tmp_path)
    inventory = _closed_inventory(facts)
    assert birth_closed_findings(facts, inventory) == []

    owner = next(entry for entry in inventory["entries"] if entry["role"] == "birth_owner")
    owner["role"] = "administrative_tool"
    assert "birth_closed_owner_invalid" in _codes(
        birth_closed_findings(facts, inventory)
    )


def test_birth_closed_rejects_legacy_direct_call_and_alias(tmp_path: Path) -> None:
    facts = _closed_facts(
        tmp_path,
        "from contract_store import publish_technical_update as old_publish\n"
        "def mutate(ref, draft):\n"
        "    operation = old_publish\n"
        "    return operation(ref, draft=draft)\n",
    )
    findings = birth_closed_findings(facts, _closed_inventory(facts))
    assert "birth_closed_legacy_authority" in _codes(findings)


def test_birth_closed_rejects_reflection_dynamic_import_and_subprocess(
    tmp_path: Path,
) -> None:
    samples = (
        "import contract_store as store\n"
        "def mutate(): return getattr(store, 'publish_technical_update')\n",
        "from importlib import import_module\n"
        "def mutate(): return import_module('contract_store')\n",
        "import subprocess\n"
        "def mutate(): return subprocess.run(['python', '-m', 'runtime.sign', 'sign'])\n",
        "import contract_store as store\n"
        "def mutate(): return vars(store)['publish_technical_update']\n",
        "import contract_store as store\n"
        "def mutate(): return store.__dict__['rollback']\n",
        "from importlib import import_module\n"
        "def mutate(): return import_module('contract_' + 'store')\n",
        "import subprocess\n"
        "def mutate():\n"
        "    command = ['python', '-m', 'runtime.' + 'sign', 'sign']\n"
        "    return subprocess.run(command)\n",
    )
    for index, source in enumerate(samples):
        root = tmp_path / str(index)
        facts = _closed_facts(root, source)
        assert "birth_closed_dynamic_boundary" in _codes(
            birth_closed_findings(facts, _closed_inventory(facts))
        )


@pytest.mark.parametrize("mutation", (None, "scope", "argument", "keyword", "alias"))
def test_guard_allows_only_exact_authenticated_preflight_runpy_door(
    tmp_path: Path, mutation: str | None,
) -> None:
    imported = "import runpy"
    owner = "runpy"
    scope = "_launch_python_target_v1"
    argument = "plan.python_module"
    keywords = 'run_name="__main__", alter_sys=False'
    if mutation == "scope":
        scope = "rogue"
    elif mutation == "argument":
        argument = "'rogue'"
    elif mutation == "keyword":
        keywords += ", init_globals={}"
    elif mutation == "alias":
        imported = "import runpy as runner"
        owner = "runner"
    facts = _scan(
        tmp_path, imported + "\n"
        f"def {scope}(plan):\n"
        f"    {owner}.run_module({argument}, {keywords})\n",
        relative="runtime/executor_birth_admin_preflight.py",
    )
    selected = [
        fact for fact in facts
        if fact.path == "runtime/executor_birth_admin_preflight.py"
    ]
    if mutation is None:
        assert selected == []
    else:
        assert any(
            "dynamic_boundary_access" in fact.capabilities
            for fact in selected
        )


def test_birth_closed_offline_signing_requires_exact_exception(tmp_path: Path) -> None:
    facts = _closed_facts(
        tmp_path / "compiled",
        "from sign import sign_executor\n"
        "def refactor_manifest(directory): return sign_executor(directory)\n",
        relative="runtime/admin/manifest_refactor.py",
    )
    key = "runtime/admin/manifest_refactor.py:refactor_manifest"
    facts = [
        replace(
            fact,
            capabilities=tuple(sorted(BIRTH_CLOSED_EXCEPTION_CAPABILITIES[key])),
        )
        if fact.key == key else fact
        for fact in facts
    ]
    assert birth_closed_findings(facts, _closed_inventory(facts)) == []

    operational = _closed_facts(
        tmp_path / "forged",
        "from sign import sign_executor\n"
        "def prepare(directory): return sign_executor(directory)\n",
    )
    key = "runtime/sample.py:prepare"
    forged = _closed_inventory(
        operational, exceptions={key: "offline_nonproductive_authoring"},
    )
    assert "birth_closed_exception_invalid" in _codes(
        birth_closed_findings(operational, forged)
    )


def test_birth_closed_policy_and_exception_are_exact(tmp_path: Path) -> None:
    facts = _closed_facts(tmp_path)
    inventory = _closed_inventory(facts)
    inventory["birth_closed"]["sealed_modules"].append("runtime/sample.py")
    assert "birth_closed_inventory_invalid" in _codes(
        birth_closed_findings(facts, inventory)
    )

    inventory = _closed_inventory(facts)
    inventory["entries"][0]["closed_exception"] = "anything_goes"
    assert "birth_closed_exception_invalid" in _codes(
        birth_closed_findings(facts, inventory)
    )


def test_birth_closed_rejects_dormant_compiled_exception(tmp_path: Path) -> None:
    facts = _closed_facts(tmp_path)
    missing = next(iter(BIRTH_CLOSED_EXCEPTION_SCOPES))
    facts = [fact for fact in facts if fact.key != missing]
    assert "birth_closed_exception_scope_missing" in _codes(
        birth_closed_findings(facts, _closed_inventory(facts))
    )


@pytest.mark.parametrize(
    ("target", "extra_capability", "expected_code"),
    (
        (None, "retire", "birth_closed_legacy_authority"),
        (None, "publish_localization", "birth_closed_legacy_authority"),
        (
            "runtime/admin/manifest_refactor.py:refactor_manifest",
            "birth",
            "birth_closed_exception_invalid",
        ),
        (
            "runtime/i18n_pipeline.py:live_contract_context",
            "birth",
            "birth_closed_exception_invalid",
        ),
        (
            "runtime/change_rollback.py:_rollback_create_executor",
            "live_artifact_read",
            "birth_closed_exception_invalid",
        ),
        (
            "runtime/cli/skills_cli.py:_cmd_uninstall",
            "verified_store_read",
            "birth_closed_exception_invalid",
        ),
    ),
)
def test_birth_closed_rejects_false_green_capability_mutants(
    tmp_path: Path,
    target: str | None,
    extra_capability: str,
    expected_code: str,
) -> None:
    facts = _closed_facts(tmp_path)
    if target is None:
        facts.append(ScopeFacts(
            "runtime/sample.py", "mutate", 1, (extra_capability,), (),
        ))
    else:
        facts = [
            replace(
                fact,
                capabilities=tuple(sorted({*fact.capabilities, extra_capability})),
            )
            if fact.key == target else fact
            for fact in facts
        ]
    assert expected_code in _codes(
        birth_closed_findings(facts, _closed_inventory(facts))
    )


def test_birth_closed_render_binds_policy_without_inventing_exceptions(
    tmp_path: Path,
) -> None:
    facts = _closed_facts(tmp_path)
    rendered = json.loads(render_birth_closed_inventory(facts))
    assert rendered["birth_closed"] == {
        "schema": BIRTH_CLOSED_SCHEMA,
        "guard_version": BIRTH_CLOSED_GUARD_VERSION,
        "owner": BIRTH_CLOSED_OWNER,
        "coordinator_store_owners": sorted(BIRTH_CLOSED_COORDINATOR_STORE_OWNERS),
        "sealed_modules": list(BIRTH_CLOSED_SEALED_MODULES),
        "exceptions": [
            {"scope": scope, "exception": exception}
            for scope, exception in sorted(BIRTH_CLOSED_EXCEPTION_SCOPES.items())
        ],
    }
    assert all("closed_exception" not in entry for entry in rendered["entries"])
