from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


RUNTIME = Path(__file__).resolve().parents[2] / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

import executor_birth_service_catalog as catalog
import executor_birth_admin_operations as admin_operations
from contract_boundary_guard import BIRTH_CLOSED_COORDINATOR_STORE_OWNERS


_HASH_A = "sha256:" + "1" * 64
_PYTHON = "/usr/bin/python3"
_SYSTEMCTL = "/usr/bin/systemctl"

_PUBLIC_EXPORT_OMITTED_ENTRYPOINTS = {
    "deploy/backup_nas.sh",
    "deploy/run_prompts_translator.sh",
    "scripts/migrate-syspath-to-package.py",
    "scripts/rename-myclaw-to-metnos.sh",
}
_PUBLIC_EXPORT_OMITTED_UNITS = {
    "metnos-backup.service",
    "metnos-backup.timer",
    "metnos-prompts-translator.service",
    "metnos-prompts-translator.timer",
}


def test_relative_legacy_locator_depth_is_normative() -> None:
    assert catalog.MAX_RELATIVE_PATH_COMPONENTS_V1 == 32
    maximum = "/".join(["d"] * 31 + ["f"])
    assert catalog._validate_legacy_locator("script", maximum) == maximum
    with pytest.raises(catalog.ServiceCatalogError, match="legacy locator"):
        catalog._validate_legacy_locator("script", "d/" + maximum)


def _has_python_main_guard(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        compare = node.test
        if len(compare.ops) != 1 or not isinstance(compare.ops[0], ast.Eq):
            continue
        values = (compare.left, *compare.comparators)
        if any(
            isinstance(value, ast.Name) and value.id == "__name__"
            for value in values
        ) and any(
            isinstance(value, ast.Constant) and value.value == "__main__"
            for value in values
        ):
            return True
    return False


def _is_python_migration(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstring = (ast.get_docstring(tree, clean=False) or "").lower()
    return "migration" in docstring or "migrazione" in docstring


def _directive(section: str, name: str, values: list[str]) -> dict[str, object]:
    return {
        "section": section, "name": name,
        "value_type": catalog._DIRECTIVE_TYPES[(section, name)],
        "values": values,
    }


def _context(
    *, installation_root: str = "/opt/metnos",
    supplementary_gids: tuple[int, ...] = (),
) -> catalog._SourceCompileContextV1:
    hashes = tuple(
        (item.entry_id, _HASH_A)
        for item in catalog.SERVICE_SOURCE_V1
        if item.target_recipe.execution_kind != "none"
    )
    return catalog._SourceCompileContextV1(
        installation_root, _PYTHON, "metnos", 1000,
        supplementary_gids, "/srv/metnos", _SYSTEMCTL, hashes,
    )


def _entries(
    *, installation_root: str = "/opt/metnos",
) -> tuple[catalog.ServiceCatalogEntryV1, ...]:
    return catalog._compile_service_source_v1(_context(
        installation_root=installation_root,
    ))


def _legacy() -> tuple[catalog.ServiceLegacyBindingV1, ...]:
    return tuple(catalog.ServiceLegacyBindingV1(
        str(item["legacy_id"]), str(item["entry_id"]), str(item["kind"]),
        str(item["scope"]), str(item["locator"]), str(item["disposition"]),
    ) for item in catalog.legacy_bindings_from_source_v1())


def _encoded(*, installation_root: str = "/opt/metnos") -> bytes:
    return catalog._encode_service_catalog_v1(
        _entries(installation_root=installation_root), _legacy(),
    )


def _target_bytes(installation_root: str = "/opt/metnos"):
    paths = (
        _PYTHON, _SYSTEMCTL, "/usr/bin/java", "/usr/bin/Xvfb",
        installation_root + "/runtime/bin/llama-server",
    )
    return tuple((path, ("target:" + path).encode("ascii")) for path in paths)


def test_public_builder_derives_the_fixed_catalog_and_all_unit_fragments() -> None:
    installation_root = "/opt/metnos"
    target_bytes = _target_bytes(installation_root)
    built = catalog._build_service_catalog_v1(
        installation_root=installation_root, python_executable=_PYTHON,
        service_user="metnos", service_gid=1000,
        service_supplementary_gids=(1000,), service_home="/srv/metnos",
        systemctl_executable=_SYSTEMCTL, target_executables=target_bytes,
    )
    content = dict(target_bytes)
    context = _context(
        installation_root=installation_root, supplementary_gids=(1000,),
    )
    by_id = {item.entry_id: item for item in catalog.SERVICE_SOURCE_V1}
    hashes = []
    for source in catalog.SERVICE_SOURCE_V1:
        recipe = source.target_recipe
        if recipe.execution_kind == "none":
            continue
        path = catalog._resolve_recipe_value_v1(
            str(recipe.target_executable), context, by_id,
        )
        hashes.append((
            source.entry_id,
            catalog.target_executable_hash_v1(path, content[path]),
        ))
    expected_entries = catalog._compile_service_source_v1(
        catalog._SourceCompileContextV1(
            installation_root, _PYTHON, "metnos", 1000, (1000,),
            "/srv/metnos", _SYSTEMCTL, tuple(hashes),
        )
    )
    expected_encoded = catalog._encode_service_catalog_v1(
        expected_entries, _legacy(),
    )
    assert built.encoded == expected_encoded
    decoded = catalog.decode_service_catalog_v1(built.encoded)
    assert built.catalog_id == decoded.catalog_id
    assert built.service_coverage_hash == decoded.service_coverage_hash
    assert dict(built.unit_fragments) == {
        str(item.unit_name): catalog.render_unit_spec_v1(
            str(item.unit_name), item.unit_spec,
        )
        for item in decoded.entries if item.unit_spec is not None
    }


@pytest.mark.parametrize("targets", (
    _target_bytes()[:-1],
    _target_bytes() + (("/usr/bin/unexpected", b"extra"),),
))
def test_public_builder_refuses_incomplete_or_extra_target_bytes(targets) -> None:
    with pytest.raises(catalog.ServiceCatalogError, match="target coverage"):
        catalog._build_service_catalog_v1(
            installation_root="/opt/metnos", python_executable=_PYTHON,
            service_user="metnos", service_gid=1000,
            service_supplementary_gids=(1000,), service_home="/srv/metnos",
            systemctl_executable=_SYSTEMCTL, target_executables=targets,
        )


def _reidentify(value: dict[str, object]) -> bytes:
    value["catalog_id"] = catalog._catalog_id(value)
    return catalog._canonical(value)


def test_single_source_covers_repository_units_entrypoints_and_maintenance() -> None:
    source_units = {
        item.unit_name for item in catalog.SERVICE_SOURCE_V1
        if item.unit_name is not None
    }
    source_units |= {
        binding.locator
        for item in catalog.SERVICE_SOURCE_V1
        for binding in item.legacy_bindings
        if binding.kind in {"user_unit", "system_unit"}
    }
    candidate_units = {
        item.unit_name for item in catalog.SERVICE_SOURCE_V1
        if item.unit_name is not None
    }
    legacy_only_units = source_units - candidate_units
    assert candidate_units == {
        "metnos-durable-worker.service", "metnos-http.service",
        "metnos-i18n-translator.service", "metnos-i18n-translator.timer",
        "metnos-llm.service", "metnos-photon.service",
        "metnos-playwright.service", "metnos-searxng.service",
        "metnos-side-display.service", "metnos-stack-quarantine.service",
        "metnos-stack-ready.service", "metnos-stack-watchdog.service",
        "metnos-stack-watchdog.timer", "metnos-telegram-daemon.service",
        "metnos.target",
    }
    assert legacy_only_units == {
        "metnos-backup.service", "metnos-backup.timer",
        "metnos-prompts-translator.service",
        "metnos-prompts-translator.timer",
    }
    root = Path(__file__).resolve().parents[2]
    repository_units = {
        path.name for relative in ("install/units", "systemd", "deploy")
        for path in (root / relative).rglob("*")
        if path.is_file() and path.name.endswith(
            (".service", ".timer", ".target", ".service.tmpl", ".timer.tmpl", ".target.tmpl")
        )
    }
    repository_units = {
        name.removesuffix(".tmpl") for name in repository_units
    }
    assert repository_units <= source_units

    repository_bindings = {
        str(item["locator"])
        for item in catalog.legacy_bindings_from_source_v1()
        if item["scope"] == "repository"
        and item["kind"] in {"script", "python_module"}
    }
    discovered_entrypoints = {
        path.relative_to(root).as_posix()
        for path in (root / "install").rglob("*.sh")
    }
    discovered_entrypoints |= {
        path.relative_to(root).as_posix()
        for path in (root / "install").rglob("*.py")
        if _has_python_main_guard(path)
    }
    playwright_installer = root / "runtime/playwright_sidecar/install.sh"
    assert playwright_installer.is_file()
    discovered_entrypoints.add(playwright_installer.relative_to(root).as_posix())
    discovered_entrypoints |= {
        path.relative_to(root).as_posix()
        for path in (root / "deploy").rglob("*.sh")
    }
    administrative_name = re.compile(
        r"(?:^|[-_])(?:install|migrate|rename|provision|deploy|update|upgrade|"
        r"bootstrap|setup)(?:[-_.]|$)",
    )
    discovered_entrypoints |= {
        path.relative_to(root).as_posix()
        for path in (root / "scripts").rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".sh"}
        and (
            administrative_name.search(path.name)
            or path.suffix == ".py" and _is_python_migration(path)
        )
        and (path.suffix == ".sh" or _has_python_main_guard(path))
    }
    complete_source_tree = (root / "scripts/export-public.sh").is_file()
    store_entrypoints = {
        item.rsplit(":", 1)[0]
        for item in BIRTH_CLOSED_COORDINATOR_STORE_OWNERS
        if item.endswith(":main")
    }
    assert discovered_entrypoints & store_entrypoints == {
        "install/executor_birth_source_receiver.py",
        "install/executor_birth_transition.py",
    }
    assert discovered_entrypoints <= repository_bindings | store_entrypoints
    assert repository_bindings - discovered_entrypoints == (
        set() if complete_source_tree else _PUBLIC_EXPORT_OMITTED_ENTRYPOINTS
    )

    from executor_birth_maintenance_units import (
        CONTRACT_CUTOVER_UNITS, MAINTENANCE_TARGETS_V1,
    )
    assert CONTRACT_CUTOVER_UNITS == catalog.contract_cutover_units_from_source_v1()
    assert MAINTENANCE_TARGETS_V1 == catalog.maintenance_targets_from_source_v1()
    assert "metnos-playwright.service" in CONTRACT_CUTOVER_UNITS
    assert ("system", "metnos-backup.service") in MAINTENANCE_TARGETS_V1


def test_current_unit_directives_have_an_explicit_codec_decision() -> None:
    root = Path(__file__).resolve().parents[2]
    observed: dict[str, set[tuple[str, str]]] = {}
    for directory in (root / "install/units", root / "systemd", root / "deploy"):
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            if not path.name.endswith((".service", ".timer", ".target", ".tmpl")):
                continue
            section = None
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    section = stripped[1:-1]
                elif section in catalog._SECTION_ORDER and re.match(
                    r"^[A-Za-z][A-Za-z]+=", stripped,
                ):
                    observed.setdefault(
                        path.name.removesuffix(".tmpl"), set(),
                    ).add((section, stripped.split("=", 1)[0]))
    candidate = {
        entry.unit_name: {
            (directive.section, directive.name)
            for directive in entry.unit_recipe
        }
        for entry in catalog.SERVICE_SOURCE_V1
        if entry.unit_name is not None
    }
    assert {
        directive for directives in observed.values() for directive in directives
    } <= set(catalog._DIRECTIVE_TYPES)
    legacy_unit_owners = {
        binding.locator: entry.class_name
        for entry in catalog.SERVICE_SOURCE_V1
        for binding in entry.legacy_bindings
        if binding.kind in {"user_unit", "system_unit"}
        and binding.locator not in candidate
    }
    observed_legacy_units = set(observed) - set(candidate)
    assert observed_legacy_units <= set(legacy_unit_owners)
    assert set(legacy_unit_owners) - observed_legacy_units == (
        set() if (root / "scripts/export-public.sh").is_file()
        else _PUBLIC_EXPORT_OMITTED_UNITS
    )
    assert set(legacy_unit_owners.values()) == {"gated_entrypoint"}
    missing = {
        (unit_name, section, name)
        for unit_name in set(observed) & set(candidate)
        for section, name in observed[unit_name] - candidate[unit_name]
    }
    assert missing == set(catalog._CURRENT_UNIT_DIRECTIVE_DISPOSITIONS_V1)


def test_catalog_codec_is_canonical_and_covers_six_classes() -> None:
    encoded = _encoded()
    decoded = catalog.decode_service_catalog_v1(encoded)
    assert decoded.encoded == encoded
    assert decoded.catalog_id == "sha256:" + hashlib.sha256(
        catalog.CATALOG_ID_DOMAIN
        + catalog._canonical({
            key: value for key, value in json.loads(encoded).items()
            if key != "catalog_id"
        })
    ).hexdigest()
    assert decoded.service_coverage_hash == catalog.service_coverage_hash_v1(encoded)
    assert {item.class_name for item in decoded.entries} == {
        "gated_service", "gated_timer", "stop_only", "target",
        "external_dependency", "gated_entrypoint",
    }
    catalog._source_identity(decoded, "/opt/metnos")


@pytest.mark.parametrize("mutation", [
    "schema_bool", "extra_key", "entry_order", "second_readiness",
    "bad_timer", "forbidden_environment", "bad_fragment_hash", "class_list",
    "gated_exec_stop", "stop_only_exec_stop", "stop_only_user",
    "stop_only_environment",
])
def test_catalog_relational_mutants_fail(mutation: str) -> None:
    value = json.loads(_encoded())
    if mutation == "schema_bool":
        value["schema_version"] = True
    elif mutation == "extra_key":
        value["extra"] = None
    elif mutation == "entry_order":
        value["entries"][0], value["entries"][1] = value["entries"][1], value["entries"][0]
    elif mutation == "second_readiness":
        next(
            item for item in value["entries"]
            if item["class"] == "gated_service" and not item["readiness_owner"]
        )["readiness_owner"] = True
    elif mutation == "bad_timer":
        next(item for item in value["entries"] if item["class"] == "gated_timer")["timer_target"] = "target-stack"
    elif mutation == "forbidden_environment":
        next(item for item in value["entries"] if item["class"] == "gated_service")["target_environment"] = [
            {"name": "PYTHONPATH", "value": "/tmp"},
        ]
    elif mutation == "bad_fragment_hash":
        next(item for item in value["entries"] if item["unit_spec"] is not None)["unit_spec"]["fragment_hash"] = _HASH_A
    elif mutation == "class_list":
        value["entries"][0]["class"] = ["gated_service"]
    elif mutation in {"gated_exec_stop", "stop_only_exec_stop"}:
        wanted_class = "gated_service" if mutation == "gated_exec_stop" else "stop_only"
        entry = next(item for item in value["entries"] if item["class"] == wanted_class)
        entry["unit_spec"]["directives"].append(
            _directive("Service", "ExecStop", ["/tmp/unattested"]),
        )
        entry["unit_spec"]["directives"].sort(
            key=lambda item: (
                catalog._SECTION_ORDER[item["section"]], item["name"].encode(),
            ),
        )
        spec = catalog.make_unit_spec_v1(entry["unit_name"], entry["unit_spec"]["directives"])
        entry["unit_spec"]["fragment_hash"] = spec.fragment_hash
    elif mutation in {"stop_only_user", "stop_only_environment"}:
        entry = next(item for item in value["entries"] if item["class"] == "stop_only")
        entry["unit_spec"]["directives"].append(
            _directive(
                "Service",
                "User" if mutation == "stop_only_user" else "Environment",
                [
                    "nobody" if mutation == "stop_only_user"
                    else "SYSTEMD_BUS_ADDRESS=unix:path=/tmp/evil"
                ],
            ),
        )
        entry["unit_spec"]["directives"].sort(
            key=lambda item: (
                catalog._SECTION_ORDER[item["section"]], item["name"].encode(),
            ),
        )
        spec = catalog.make_unit_spec_v1(
            entry["unit_name"], entry["unit_spec"]["directives"],
        )
        entry["unit_spec"]["fragment_hash"] = spec.fragment_hash
    with pytest.raises(catalog.ServiceCatalogError):
        catalog.decode_service_catalog_v1(_reidentify(value))


def test_catalog_rejects_duplicate_and_noncanonical_json() -> None:
    encoded = _encoded()
    duplicate = encoded.replace(b'{"catalog_id":', b'{"catalog_id":"sha256:' + b"0" * 64 + b'","catalog_id":', 1)
    with pytest.raises(catalog.ServiceCatalogError, match="duplicate"):
        catalog.decode_service_catalog_v1(duplicate)
    with pytest.raises(catalog.ServiceCatalogError, match="schema"):
        catalog.decode_service_catalog_v1(encoded + b"\n")


def test_renderer_and_independent_parser_round_trip_and_reject_mutants() -> None:
    for entry in _entries():
        if entry.unit_spec is None:
            continue
        rendered = catalog.render_unit_spec_v1(entry.unit_name, entry.unit_spec)
        assert catalog.parse_unit_fragment_v1(entry.unit_name, rendered) == entry.unit_spec
        for mutant in (
            b"# hidden\n" + rendered,
            rendered.replace(b"=", b"=\n", 1),
            rendered.replace(b"Metnos", b"%n", 1),
            rendered + b"[X]\nUnknown=yes\n",
        ):
            with pytest.raises(catalog.ServiceCatalogError):
                catalog.parse_unit_fragment_v1(entry.unit_name, mutant)


@pytest.mark.parametrize("name,values", [
    ("Environment", ["FOO=alpha beta"]),
    ("Environment", ['FOO="bar"']),
    ("ReadWritePaths", ["/srv/alpha beta"]),
    ("ReadWritePaths", ['/srv/"alpha"']),
])
def test_renderer_domain_rejects_ambiguous_multi_value_tokens(name, values) -> None:
    with pytest.raises(catalog.ServiceCatalogError):
        catalog.make_unit_spec_v1("metnos-test.service", [
            _directive("Service", name, values),
        ])


def test_source_compiler_binds_targets_environment_and_supplementary_groups() -> None:
    entries = catalog._compile_service_source_v1(_context(
        supplementary_gids=(1001, 1002),
    ))
    by_id = {item.entry_id: item for item in entries}
    playwright_environment = {
        item.name: item.value
        for item in by_id["service-playwright"].target_environment
    }
    assert playwright_environment == {
        "DISPLAY": ":99",
        "METNOS_USER_CONFIG": "/srv/metnos/.config/metnos",
        "METNOS_USER_DATA": "/srv/metnos/.local/share/metnos",
        "METNOS_USER_STATE": "/srv/metnos/.local/state/metnos",
        "METNOS_WORKSPACE": "/srv/metnos/.local/share/metnos/workspace",
        "PLAYWRIGHT_BROWSERS_PATH": (
            "/srv/metnos/.local/share/metnos/playwright-browsers"
        ),
    }
    for item in entries:
        if item.class_name != "gated_service":
            continue
        directives = catalog._directive_index(item.unit_spec)
        assert directives[("Service", "SupplementaryGroups")].values == (
            "1001 1002",
        )
    expected_operations = {
        "entry-backup": "backup",
        "entry-download-models": "download-models",
        "entry-install-git-hooks": "install-git-hooks",
        "entry-installer": "install-metnos",
        "entry-llm-installer": "install-llm",
        "entry-migrate-syspath": "migrate-syspath",
        "entry-normalize-installed-executors": "normalize-installed-executors",
        "entry-playwright-installer": "install-playwright",
        "entry-post-rename-baseline": "post-rename-baseline",
        "entry-post-rename-verify": "post-rename-verify",
        "entry-prompts-translator": "prompts-translator",
        "entry-rename-myclaw": "rename-myclaw",
        "entry-service-policy": "install-service-policy",
        "entry-sidecar-photon": "install-sidecar-photon",
        "entry-sidecar-searxng": "install-sidecar-searxng",
        "entry-sidecar-vlm": "install-sidecar-vlm",
    }
    expected_operation_bindings = {
        "backup": {
            ("script", "repository", "deploy/backup_nas.sh"),
            ("system_unit", "system", "metnos-backup.service"),
            ("system_unit", "system", "metnos-backup.timer"),
        },
        "download-models": {
            ("script", "repository", "install/download_models.sh"),
        },
        "install-git-hooks": {
            ("script", "repository", "scripts/install_git_hooks.sh"),
        },
        "install-llm": {
            ("python_module", "repository", "install/llm_manager.py"),
        },
        "install-metnos": {
            ("script", "repository", "install/bootstrap.sh"),
            ("script", "repository", "install/setup.sh"),
            ("python_module", "repository", "install/__main__.py"),
        },
        "install-playwright": {
            ("python_module", "repository", "install/playwright_sidecar.py"),
            ("script", "repository", "runtime/playwright_sidecar/install.sh"),
        },
        "install-service-policy": {
            (
                "python_module", "repository",
                "install/service_control_policy.py",
            ),
        },
        "install-sidecar-photon": {
            ("python_module", "repository", "install/sidecar.py"),
        },
        "install-sidecar-searxng": {
            ("python_module", "repository", "install/sidecar.py"),
        },
        "install-sidecar-vlm": {
            ("python_module", "repository", "install/sidecar.py"),
        },
        "migrate-syspath": {
            (
                "python_module", "repository",
                "scripts/migrate-syspath-to-package.py",
            ),
        },
        "normalize-installed-executors": {
            (
                "python_module", "repository",
                "scripts/normalize_installed_github_executors.py",
            ),
        },
        "post-rename-baseline": {
            ("script", "repository", "scripts/post-rename-verify.sh"),
        },
        "post-rename-verify": {
            ("script", "repository", "scripts/post-rename-verify.sh"),
        },
        "prompts-translator": {
            ("script", "repository", "deploy/run_prompts_translator.sh"),
            (
                "system_unit", "system",
                "metnos-prompts-translator.service",
            ),
            ("system_unit", "system", "metnos-prompts-translator.timer"),
        },
        "rename-myclaw": {
            ("script", "repository", "scripts/rename-myclaw-to-metnos.sh"),
        },
    }
    entrypoints = {
        item.entry_id: item for item in entries
        if item.class_name == "gated_entrypoint"
    }
    assert set(entrypoints) == set(expected_operations)
    assert set(expected_operations.values()) == admin_operations.OPERATIONS_V1
    source_by_id = {item.entry_id: item for item in catalog.SERVICE_SOURCE_V1}
    assert {
        operation: {
            (binding.kind, binding.scope, binding.locator)
            for binding in source_by_id[entry_id].legacy_bindings
        }
        for entry_id, operation in expected_operations.items()
    } == expected_operation_bindings
    for entry_id, operation in expected_operations.items():
        entrypoint = entrypoints[entry_id]
        assert entrypoint.execution_kind == "python_module"
        assert entrypoint.target_executable == _PYTHON
        assert entrypoint.python_module == (
            "runtime.executor_birth_admin_operations"
        )
        assert entrypoint.target_args == (operation,)
        assert entrypoint.target_working_directory == "/opt/metnos"
        assert entrypoint.target_environment == ()
    stop = by_id["stop-stack-quarantine"]
    assert set(stop.target_args[1:]) == {
        item.unit_name for item in catalog.SERVICE_SOURCE_V1
        if item.class_name in {"gated_service", "gated_timer"}
    }


def test_group7_administrative_operations_fail_closed_without_mutation(
    monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(admin_operations.sys, "platform", "linux")
    for operation in sorted(admin_operations.OPERATIONS_V1):
        assert admin_operations.main([operation]) == 78
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == (
            f"birth_ownership_closed_enforcement_required: {operation}\n"
        )
    assert admin_operations.main([]) == 64
    assert capsys.readouterr().err == (
        "birth_ownership_deployment_invalid\n"
    )
    assert admin_operations.main(["unknown"]) == 64
    assert capsys.readouterr().err == (
        "birth_ownership_deployment_invalid\n"
    )


def _copy_administrative_adapter(tmp_path: Path) -> tuple[Path, Path]:
    release = tmp_path / "rebased-release"
    package = release / "runtime"
    package.mkdir(parents=True)
    source = Path(__file__).resolve().parents[2] / "runtime"
    shutil.copyfile(
        source / "executor_birth_admin_operations.py",
        package / "executor_birth_admin_operations.py",
    )
    outside = tmp_path / "outside-release"
    outside.mkdir()
    return release, outside


def test_administrative_adapter_module_scope_is_declarative() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "runtime/executor_birth_admin_operations.py"
    )
    observed = ast.parse(
        source.read_text(encoding="utf-8"), filename=str(source),
    )
    assert isinstance(observed.body[0], ast.Expr)
    assert isinstance(observed.body[0].value, ast.Constant)
    assert isinstance(observed.body[0].value.value, str)
    observed.body[0].value.value = "module-docstring"
    assert isinstance(observed.body[5], ast.FunctionDef)
    observed.body[5].body = [ast.Pass()]
    operation_literals = ",".join(
        repr(operation) for operation in sorted(admin_operations.OPERATIONS_V1)
    )
    expected = ast.parse(
        '"""module-docstring"""\n'
        "from __future__ import annotations\n"
        "import sys\n"
        "from typing import Sequence\n"
        f"OPERATIONS_V1 = frozenset({{{operation_literals}}})\n"
        "def main(argv: Sequence[str] | None = None) -> int:\n"
        "    pass\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n"
    )
    assert ast.dump(observed, include_attributes=False) == ast.dump(
        expected, include_attributes=False,
    )


def test_administrative_adapter_runs_with_real_isolated_bootstrap(
    tmp_path: Path,
) -> None:
    release, outside = _copy_administrative_adapter(tmp_path)
    bootstrap = (
        "import runpy,sys;"
        "sys.path.insert(0,sys.argv[1]);"
        "sys.argv=['runtime.executor_birth_admin_operations','backup'];"
        "runpy.run_module('runtime.executor_birth_admin_operations',"
        "run_name='__main__',alter_sys=False)"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-c", bootstrap, str(release)],
        cwd=outside, env={}, text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 78
    assert completed.stdout == ""
    assert completed.stderr == (
        "birth_ownership_platform_unsupported\n"
        if sys.platform.startswith("win")
        else "birth_ownership_closed_enforcement_required: backup\n"
    )


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_administrative_adapter_denies_before_filesystem_process_or_network_io(
    tmp_path: Path, platform: str,
) -> None:
    release, outside = _copy_administrative_adapter(tmp_path)
    harness = (
        "import os,socket,subprocess,sys\n"
        "sys.path.insert(0,sys.argv[1])\n"
        "from runtime import executor_birth_admin_operations as target\n"
        f"target.sys.platform={platform!r}\n"
        "def deny(event,args):\n"
        "    if event=='open' or event.startswith(('os.','subprocess.','socket.')):\n"
        "        raise RuntimeError('unexpected I/O: '+event)\n"
        "sys.addaudithook(deny)\n"
        "vectors=([[value] for value in sorted(target.OPERATIONS_V1)]"
        "+[[],['unknown']] if target.sys.platform=='linux' else [['backup']])\n"
        "for arguments in vectors:\n"
        "    wanted=(78 if len(arguments)==1 and arguments[0] in "
        "target.OPERATIONS_V1 else 64)\n"
        "    if target.main(arguments)!=wanted:\n"
        "        raise SystemExit(91)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-c", harness, str(release)],
        cwd=outside, env={}, text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == (
        "".join(
            "birth_ownership_closed_enforcement_required: "
            f"{operation}\n"
            for operation in sorted(admin_operations.OPERATIONS_V1)
        ) + "birth_ownership_deployment_invalid\n" * 2
        if platform == "linux"
        else "birth_ownership_platform_unsupported\n"
    )


def test_source_identity_rejects_target_recipe_change() -> None:
    value = json.loads(_encoded())
    entry = next(item for item in value["entries"] if item["entry_id"] == "service-http")
    entry["target_args"][-1] = "8772"
    decoded = catalog.decode_service_catalog_v1(_reidentify(value))
    with pytest.raises(catalog.ServiceCatalogError, match="source recipe"):
        catalog._source_identity(decoded, "/opt/metnos")


def test_public_surface_contains_only_product_loader() -> None:
    assert catalog.__all__ == [
        "capture_current_service_catalog_v1",
        "load_service_catalog_v1",
    ]


def test_target_change_changes_catalog_but_not_unit_fragments() -> None:
    first = _entries()
    changed = list(first)
    index = next(i for i, item in enumerate(changed) if item.class_name == "gated_service")
    original = changed[index]
    changed[index] = catalog.ServiceCatalogEntryV1(
        original.entry_id, original.unit_name, original.external_unit_name,
        original.adapter_path, original.class_name, original.scope,
        original.execution_kind, original.target_executable,
        "sha256:" + "2" * 64, original.python_module,
        ("--serve-v2",), "/opt/metnos-v2", original.target_environment,
        original.timer_target, original.unit_spec, original.requires_preflight,
        original.readiness_owner,
    )
    encoded_first = catalog._encode_service_catalog_v1(first, _legacy())
    encoded_second = catalog._encode_service_catalog_v1(changed, _legacy())
    assert encoded_first != encoded_second
    assert [
        catalog.render_unit_spec_v1(item.unit_name, item.unit_spec)
        for item in first if item.unit_spec is not None
    ] == [
        catalog.render_unit_spec_v1(item.unit_name, item.unit_spec)
        for item in changed if item.unit_spec is not None
    ]


def _nominal_live_record(monkeypatch):
    import executor_birth_distribution_manifest as distribution
    from executor_birth_ownership_preflight import _sealed_build_identity_for_test

    root = "/var/lib/metnos/executor-birth/releases-v1/00000000000000000001"
    encoded = _encoded(installation_root=root)
    decoded = catalog.decode_service_catalog_v1(encoded)
    content = {catalog.CATALOG_PATH_V1: encoded}
    for entry in decoded.entries:
        if entry.unit_spec is not None:
            content[f"deployment/systemd/{entry.unit_name}"] = (
                catalog.render_unit_spec_v1(entry.unit_name, entry.unit_spec)
            )
    files = tuple(
        distribution.DistributionFile(
            path, len(value), distribution.file_content_hash(path, value),
            "service_catalog" if path == catalog.CATALOG_PATH_V1 else "service_unit",
        )
        for path, value in sorted(content.items())
    )
    manifest = b"manifest"
    signature = b"s" * 64
    build_id = "sha256:" + "3" * 64
    boundary_hash = "sha256:" + "4" * 64
    record = distribution.AuthenticatedDistributionRecordV1(
        build_id, None, 1, "1.0.0", "linux", "x86_64", "key", root,
        "/var/lib/metnos/executor-birth", "inventory", boundary_hash,
        "v1", "runtime/preflight.py", files, manifest, signature,
        distribution._authenticated_artifact_binding(manifest, signature),
        distribution._AUTHENTICATED_DISTRIBUTION_SEAL,
    )
    verified = distribution.VerifiedDistribution(
        _sealed_build_identity_for_test(build_id, boundary_hash, "v1"),
        None, 1, "1.0.0", "linux", "x86_64", root,
        "/var/lib/metnos/executor-birth", "runtime/preflight.py", files,
        manifest, signature,
        distribution._distribution_artifact_binding(manifest, signature),
        distribution._VERIFIED_DISTRIBUTION_SEAL,
    )
    monkeypatch.setattr(
        distribution, "verify_installed_distribution_record_v1",
        lambda observed: verified if observed is record else pytest.fail("wrong record"),
    )
    monkeypatch.setattr(
        distribution, "_secure_read",
        lambda _root, item, *, administrative: content[item.path],
    )
    return record, verified, content


@pytest.mark.skipif(sys.platform.startswith("win"), reason="productive loader is Linux-only")
def test_product_loader_reattests_fixed_record_and_rereads_bound_units(monkeypatch) -> None:
    record, _verified, content = _nominal_live_record(monkeypatch)
    loaded = catalog.load_service_catalog_v1(record)
    assert loaded.catalog.encoded == content[catalog.CATALOG_PATH_V1]
    assert dict(loaded.unit_fragments) == {
        path.removeprefix("deployment/systemd/"): value
        for path, value in content.items() if path != catalog.CATALOG_PATH_V1
    }

    import executor_birth_distribution_manifest as distribution
    victim = next(path for path in content if path != catalog.CATALOG_PATH_V1)
    monkeypatch.setattr(
        distribution, "_secure_read",
        lambda _root, item, *, administrative: (
            b"tampered" if item.path == victim else content[item.path]
        ),
    )
    with pytest.raises(catalog.ServiceCatalogError, match="artifact hash"):
        catalog.load_service_catalog_v1(record)


@pytest.mark.skipif(sys.platform.startswith("win"), reason="productive loader is Linux-only")
def test_current_loader_reverifies_the_sealed_release_around_capture(
    monkeypatch,
) -> None:
    _record, verified, content = _nominal_live_record(monkeypatch)
    import executor_birth_distribution_manifest as distribution

    calls = []

    def verify(encoded, signature):
        calls.append((encoded, signature))
        return verified

    monkeypatch.setattr(
        distribution, "verify_current_installation_distribution_v1", verify,
    )
    loaded = catalog.capture_current_service_catalog_v1(verified)

    assert calls == [
        (verified.encoded, verified.signature),
        (verified.encoded, verified.signature),
    ]
    assert loaded.catalog.encoded == content[catalog.CATALOG_PATH_V1]
    assert dict(loaded.unit_fragments) == {
        path.removeprefix("deployment/systemd/"): value
        for path, value in content.items() if path != catalog.CATALOG_PATH_V1
    }


@pytest.mark.skipif(sys.platform.startswith("win"), reason="productive loader is Linux-only")
def test_current_loader_rejects_an_unsealed_distribution_before_verification(
    monkeypatch,
) -> None:
    import executor_birth_distribution_manifest as distribution

    called = []
    monkeypatch.setattr(
        distribution, "verify_current_installation_distribution_v1",
        lambda *_args: called.append(True),
    )
    with pytest.raises(catalog.ServiceCatalogError, match="verified artifact"):
        catalog.capture_current_service_catalog_v1(object())
    assert called == []


def test_product_loader_rejects_wrong_type_or_off_linux_before_authority(monkeypatch) -> None:
    import executor_birth_distribution_manifest as distribution

    called = []
    monkeypatch.setattr(
        distribution, "verify_installed_distribution_record_v1",
        lambda _record: called.append(True),
    )
    if sys.platform.startswith("linux"):
        with pytest.raises(catalog.ServiceCatalogError, match="authenticated record"):
            catalog.load_service_catalog_v1(object())
    else:
        with pytest.raises(catalog.ServiceCatalogError, match="platform_unsupported"):
            catalog.load_service_catalog_v1(object())
    assert called == []
