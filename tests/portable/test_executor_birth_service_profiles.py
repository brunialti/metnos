"""Optional service bindings preserve the closed topology in both readers."""
from __future__ import annotations

import dataclasses
import itertools
import json

import pytest

import executor_birth_admin_preflight as preflight
import executor_birth_distribution_assembler as assembler
import executor_birth_service_catalog as catalog
import service_profile
import test_executor_birth_admin_preflight_materials as fixtures


KEYS = ("llm", "photon", "playwright", "searxng", "vlm")
PROFILES = tuple(tuple(keys) for size in range(6)
                 for keys in itertools.combinations(KEYS, size))


def _profile(keys):
    profile = {key: {"url": "http://services.example.test/" + key} for key in keys}
    for key in {"llm", "vlm"} & set(keys):
        profile[key]["model"] = "local-model"
    return service_profile.validate_profile(profile, resolved=True)


def _build(profile):
    descriptor = preflight._decode_deployment_descriptor_v1(
        assembler.encode_deployment_descriptor_v1(fixtures._deployment_record()))
    targets = fixtures._binder_target_bytes(descriptor.installation_root)
    if isinstance(profile, dict) and "playwright" in profile:
        targets.pop("/usr/bin/Xvfb")
    built = catalog._build_service_catalog_v1(
        installation_root=descriptor.installation_root,
        python_executable=descriptor.python_executable,
        service_user=descriptor.service_user, service_gid=descriptor.service_gid,
        service_supplementary_gids=descriptor.service_supplementary_gids,
        service_home=descriptor.service_home,
        systemctl_executable=descriptor.systemctl_executable,
        target_executables=tuple(targets.items()), service_profile=profile)
    return built, descriptor


def _reidentify(document):
    document["catalog_id"] = catalog._catalog_id(document)
    return catalog._canonical(document)


@pytest.mark.parametrize("keys", PROFILES)
def test_all_service_selections_keep_unselected_companions_and_startup_checks(keys):
    profile = _profile(keys)
    built, descriptor = _build(profile)
    normal = catalog.decode_service_catalog_v1(built.encoded)
    autonomous = preflight._decode_service_catalog_v1(built.encoded)
    catalog._source_identity(normal, descriptor.installation_root)
    preflight._service_source_identity_v1(autonomous, descriptor)
    assert normal.service_profile == autonomous.service_profile
    ids = {entry.entry_id for entry in normal.entries}
    expected_environment = service_profile.target_environment(profile, descriptor.service_home)
    for key in set(KEYS) - {"vlm"}:
        assert ("service-" + key in ids) is (key not in profile)
    assert ("service-side-display" in ids) is ("playwright" not in profile)
    # Legacy execution routes remain covered, including omitted companions.
    assert {item.legacy_id for item in normal.legacy_bindings} == {
        item["legacy_id"] for item in catalog.legacy_bindings_from_source_v1()}
    for entry in normal.entries:
        if entry.class_name != "gated_service":
            continue
        assert entry.requires_preflight
        if entry.execution_kind == "python_module":
            assert dict((item.name, item.value) for item in entry.target_environment).items() >= expected_environment.items()


@pytest.mark.parametrize("mutation", ("missing_url", "url_mismatch", "extra_env", "command", "permissions", "legacy", "relation", "local_companion", "undeclared_remote"))
def test_profile_does_not_authorize_other_recipe_changes(mutation):
    built, descriptor = _build(_profile(KEYS))
    document = json.loads(built.encoded)
    http = next(item for item in document["entries"] if item["entry_id"] == "service-http")
    if mutation == "missing_url":
        http["target_environment"] = [item for item in http["target_environment"] if item["name"] != "METNOS_LLM_URL"]
    elif mutation == "url_mismatch":
        next(item for item in http["target_environment"] if item["name"] == "METNOS_LLM_URL")["value"] = "https://unexpected.example.test"
    elif mutation == "extra_env":
        http["target_environment"].append({"name": "ZZ_EXTRA", "value": "unsafe"})
    elif mutation == "command":
        http["target_args"].append("--unexpected")
    elif mutation in {"permissions", "relation"}:
        spec = catalog.decode_service_catalog_v1(built.encoded).entries
        entry = next(item for item in spec if item.entry_id == "service-http")
        name = "User" if mutation == "permissions" else "After"
        changed = tuple(dataclasses.replace(item, values=("root" if mutation == "permissions" else "unexpected.target",))
                        if item.name == name else item for item in entry.unit_spec.directives)
        http["unit_spec"] = catalog._unit_spec_document(catalog.make_unit_spec_v1(entry.unit_name, changed))
    elif mutation == "legacy":
        document["legacy_bindings"].pop()
    elif mutation == "local_companion":
        local, _ = _build({})
        document["entries"].append(next(item for item in json.loads(local.encoded)["entries"] if item["entry_id"] == "service-llm"))
        document["entries"].sort(key=lambda item: item["entry_id"])
    else:
        del document["service_profile"]["photon"]
    encoded = _reidentify(document)
    with pytest.raises(catalog.ServiceCatalogError):
        catalog._source_identity(catalog.decode_service_catalog_v1(encoded), descriptor.installation_root)
    with pytest.raises(preflight.PreflightError):
        preflight._service_source_identity_v1(preflight._decode_service_catalog_v1(encoded), descriptor)


@pytest.mark.parametrize("profile", (
    [], False, "", 0,
    {"unknown": {"url": "https://example.test"}},
    {"llm": {"url": "https://example.test"}},
    {"llm": {"url": "https://example.test", "model": "m", "frontier": 1}},
    {"photon": {"url": "file:///tmp/data"}},
    {"photon": {"url": "https://u:p@example.com"}},
    {"photon": {"url": "https://example.test:99999"}},
    {"photon": {"url": "https://example.test\nEnvironment=BAD"}},
    {"vlm": {"url": "https://example.test", "model": "bad\nmodel"}},
    {"photon": {"url": "https://example.test", "exec": "command"}},
))
def test_invalid_profile_is_refused_before_recipe_matching(profile):
    with pytest.raises((ValueError, catalog.ServiceCatalogError)):
        _build(profile)
    with pytest.raises(preflight.PreflightError):
        preflight._catalog_service_profile_v1({"service_profile": profile})
