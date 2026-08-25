from __future__ import annotations

import json
from pathlib import Path

from contract_boundary_guard import (
    SCAN_ROOTS,
    SCHEMA,
    ScopeFacts,
    birth_migration_findings,
    check,
    discover,
    render_inventory,
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


def _codes(findings) -> set[str]:
    return {finding.code for finding in findings}


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


def test_rebound_boundary_symbol_loses_its_authority(tmp_path: Path) -> None:
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

    assert _fact(facts, "install").capabilities == ("authoring_write",)
    assert "operational_write_without_publish" in _codes(check(facts, inventory))


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
        "install/phases/phase3_code.py:_publish_active_authoring_contracts",
        "runtime/change_applier_extend.py:extend_executor_manifest",
        "runtime/change_rollback.py:_rollback_extend_executor",
        "runtime/cli/skills_cli.py:_cmd_import",
        "runtime/cli/skills_cli.py:_try_publish_authoring_update",
        "runtime/jobs/promoter_promote.py:promote_to_catalog",
        "runtime/jobs/promoter_rollback.py:rollback_promotion",
        "runtime/sign.py:<module>",
        "runtime/sign.py:main",
        "runtime/sign.py:publish_authoring_update",
        "runtime/sign.py:publish_executor",
        "runtime/sign.py:reactivate_executor_contract",
        "runtime/sign.py:rollback_executor_contract",
        "runtime/stack_reconcile.py:<module>",
        "runtime/stack_reconcile.py:StackReconciler.restart",
        "runtime/stack_reconcile.py:StackReconciler.watchdog",
        "runtime/stack_reconcile.py:main",
        "runtime/stack_reconcile.py:verify_named_executors",
        "runtime/synt.py:<module>",
        "runtime/synt.py:Synt.approve_proposal",
        "runtime/synt.py:Synt.specialize",
        "runtime/synt.py:_cli",
        "runtime/synth_request.py:_install_synthesized",
        "runtime/synth_request.py:handle_synth_request",
        "scripts/generate_builtin_executor_contracts.py:<module>",
        "scripts/generate_builtin_executor_contracts.py:main",
    }
