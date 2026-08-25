from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from executor_birth_context import (
    AdmissionContextBuildError,
    AdmissionContextMaterial,
    ComponentMaterial,
    MaterialFile,
    build_admission_context,
)
from executor_birth_context_builder import production_context_builder
from executor_birth_identity import admission_context_id


NAMES = (
    "standard", "linter", "vocabulary", "authority_registry",
    "sandbox_registry", "property_catalog", "runner", "review_policy",
    "template_allowlist", "primitive_allowlist", "dependency_allowlist",
)


def _materials(tmp_path: Path) -> AdmissionContextMaterial:
    values = {}
    for index, name in enumerate(NAMES):
        path = tmp_path / f"{name}.material"
        path.write_bytes(f"implementation:{name}:v1\n".encode())
        values[name] = ComponentMaterial(
            "v1",
            (MaterialFile("implementation", path.resolve()),),
            {"enabled": True, "ordinal": index, "rules": [name]},
        )
    return AdmissionContextMaterial(**values)


def test_golden_context_and_epoch_are_canonical(tmp_path: Path) -> None:
    built = build_admission_context(_materials(tmp_path))

    assert admission_context_id(built.context) == built.pin.admission_context_id
    assert built.pin.admission_context_id == (
        "sha256:38a6da13de64cbdf3e1469cc9e9fd521d7444a510311f7bf48fba96e973ca4bf"
    )
    assert built.pin.context_epoch == (
        "sha256:612aede3a52481a1360dc66688238dd533f4c166637dbe60d2055bce10844f89"
    )


@pytest.mark.parametrize("name", NAMES)
def test_each_effective_file_mutation_changes_only_its_component(
    tmp_path: Path, name: str,
) -> None:
    material = _materials(tmp_path)
    before = build_admission_context(material)
    source = getattr(material, name).files[0].path
    source.write_bytes(source.read_bytes() + b"mutation\n")

    after = build_admission_context(material)

    assert getattr(before.context, name).digest != getattr(after.context, name).digest
    assert before.pin != after.pin
    for other in set(NAMES) - {name}:
        assert getattr(before.context, other) == getattr(after.context, other)


@pytest.mark.parametrize("name", NAMES)
def test_each_effective_configuration_mutation_changes_component(
    tmp_path: Path, name: str,
) -> None:
    material = _materials(tmp_path)
    before = build_admission_context(material)
    old = getattr(material, name)
    changed = replace(material, **{
        name: replace(old, configuration={"enabled": False, "rules": [name]})
    })

    after = build_admission_context(changed)

    assert getattr(before.context, name).digest != getattr(after.context, name).digest
    for other in set(NAMES) - {name}:
        assert getattr(before.context, other) == getattr(after.context, other)


def test_built_material_is_a_frozen_copy(tmp_path: Path) -> None:
    material = _materials(tmp_path)
    built = build_admission_context(material)
    original = built.material["runner"].files["implementation"]
    material.runner.files[0].path.write_bytes(b"changed after build")

    assert built.material["runner"].files["implementation"] == original
    with pytest.raises(TypeError):
        built.material["runner"].files["implementation"] = b"forged"  # type: ignore[index]


def test_empty_material_and_supplied_digest_shape_are_not_accepted() -> None:
    with pytest.raises(AdmissionContextBuildError, match="material_empty"):
        ComponentMaterial("v1")
    with pytest.raises(AdmissionContextBuildError, match="config_invalid"):
        ComponentMaterial("v1", configuration={"digest": object()})
    with pytest.raises(AdmissionContextBuildError, match="config_invalid"):
        ComponentMaterial("v1", configuration={1: "ambiguous JSON key"})
    with pytest.raises(AdmissionContextBuildError, match="config_invalid"):
        ComponentMaterial("v1", configuration={"value": float("nan")})


def test_linked_material_is_rejected(tmp_path: Path) -> None:
    material = _materials(tmp_path)
    source = material.runner.files[0].path
    hardlink = tmp_path / "runner-hardlink"
    hardlink.hardlink_to(source)

    with pytest.raises(AdmissionContextBuildError, match="material_unsafe"):
        build_admission_context(material)


def test_production_adapter_holds_one_immutable_process_snapshot(tmp_path: Path) -> None:
    material = _materials(tmp_path)
    adapter = production_context_builder(material)
    context, pin = adapter.preview(object())
    material.standard.files[0].path.write_bytes(b"changed after bootstrap")

    assert adapter.resolve(object()) == (context, pin)
    assert adapter.current_epoch() == pin.context_epoch


def test_production_adapter_has_no_implicit_or_digest_only_default() -> None:
    with pytest.raises(TypeError):
        production_context_builder()  # type: ignore[call-arg]
