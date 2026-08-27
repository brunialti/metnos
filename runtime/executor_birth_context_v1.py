"""The V1 context catalogue and the factory that freezes it (RM-0008).

The material is described once by the installer and rebuilt by the runtime
under its own barrier: section 9.4 forbids trusting the recorded description.
Two implementations of the same digest would diverge without anyone noticing,
so there is one, here, and both sides import it.

The factory **receives** an already open read session over the distribution and
opens nothing of its own: the authority to reach the filesystem stays with the
two doors the productive graph admits.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

CONTEXT_MATERIAL_BASENAME_V1 = "material-v1.json"
CONTEXT_CONTAINER_BASENAME_V1 = "context"
CONTEXT_SOURCE_DIGEST_DOMAIN_V1 = (
    b"metnos.executor-birth.context-source-inventory/v1\0"
)
MAXIMUM_CONTEXT_SOURCE_BYTES_V1 = 4 * 1024 * 1024
PREPARED_STATE_V1 = "prepared_not_active"

# The V1 catalogue is owned by the code and reviewed as a whole: no caller can
# add, remove or rename an entry, and no configuration arrives from a document.
# ``enforcement_state`` says the truth about today, not the intention: section
# 9.2 requires ``prepared_only`` wherever the current code does not really
# apply the policy, and group 3 is what turns one of these to ``productive``.
CONTEXT_CATALOG_V1: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    (
        "standard", "1",
        (
            "executor_standard.py", "presentation_contract.py",
            "code_file_paths.py", "naming_grammar.py",
        ),
        "productive",
    ),
    ("linter", "1", ("manifest_lint.py", "manifest_rules.py"), "prepared_only"),
    (
        "vocabulary", "1",
        ("policy.py", "capabilities.py", "vocab.py"),
        "prepared_only",
    ),
    ("authority_registry", "1", (), "prepared_only"),
    ("sandbox_registry", "1", ("sandbox.py",), "prepared_only"),
    (
        "property_catalog", "1",
        ("executor_birth_properties.py", "executor_birth_property_runner.py"),
        "productive",
    ),
    (
        "runner", "1",
        (
            "executor_birth_runner.py", "executor_birth_runner_windows_v1.py",
            "bounded_subprocess.py",
        ),
        "productive",
    ),
    (
        "review_policy", "1",
        (
            "executor_birth_semantic_review.py",
            "executor_birth_semantic_authority.py", "llm_workloads.py",
        ),
        "productive",
    ),
    ("template_allowlist", "1", (), "prepared_only"),
    ("primitive_allowlist", "1", ("executor_birth_properties.py",), "prepared_only"),
    ("dependency_allowlist", "1", ("code_file_paths.py",), "prepared_only"),
)


class ContextMaterialError(RuntimeError):
    """The context material cannot be built from the installed distribution."""

    def __init__(self, code: str, cause: BaseException | None = None) -> None:
        self.code = code
        self._internal_cause = cause
        super().__init__(code)
        self.__suppress_context__ = True

    @property
    def __cause__(self) -> None:
        return None

    @__cause__.setter
    def __cause__(self, value: BaseException | None) -> None:
        if value is not None and self._internal_cause is None:
            self._internal_cause = value


@dataclass(frozen=True, slots=True)
class PreparedContextMaterialV1:
    """The inert material: described, never applied by the group that builds it."""

    document: bytes
    prepared_admission_context_id: str
    prepared_context_epoch: str
    source_inventory_sha256: str
    material_sha256: str
    # The frozen context and its pin, already computed here: recomputing them
    # elsewhere would be a second implementation of the same identity.
    context: object = None
    pin: object = None


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def component_configuration_v1(
    name: str, enforcement: str, authority_registry: Mapping[str, object],
) -> dict[str, object]:
    """The already resolved configuration of one component.

    ``authority_registry`` is the only component whose material is not a file:
    it is the set of public identities, so the prepared material changes
    whenever those identities change.
    """
    configuration: dict[str, object] = {"enforcement_state": enforcement}
    if name == "authority_registry":
        configuration["registry"] = dict(authority_registry)
    return configuration


def prepare_context_material_v1(
    sources, authority_registry: Mapping[str, object],
) -> PreparedContextMaterialV1:
    """Freeze the eleven components once and describe them.

    Nothing is chosen by a caller: the files come from the closed catalogue,
    the bytes arrive through the session the caller already holds, and the
    configurations are already resolved values.  The identifier and the epoch
    attest the frozen bytes; they do not attest that any check consumes them
    (section 9.1).
    """
    from executor_birth_context import (
        FrozenComponentMaterial, _canonical_json, _component_digest,
        _context_epoch,
    )
    from executor_birth_identity import (
        AdmissionContextV1, ContextComponent, admission_context_id,
    )
    from executor_birth_predecessor import AdmissionContextPin
    from executor_birth_secure_fs import BirthSecureFSError

    components: dict[str, dict[str, object]] = {}
    digests: dict[str, ContextComponent] = {}
    inventory: list[dict[str, object]] = []
    for name, version, files, enforcement in CONTEXT_CATALOG_V1:
        payloads: dict[str, bytes] = {}
        records: list[dict[str, object]] = []
        for label in files:
            try:
                raw = sources.read_file(
                    (label,),
                    maximum=MAXIMUM_CONTEXT_SOURCE_BYTES_V1,
                    exact_private=False,
                )
            except BirthSecureFSError as exc:
                raise ContextMaterialError(exc.code, exc) from None
            payloads[label] = raw
            digest = hashlib.sha256(raw).hexdigest()
            records.append({"label": label, "size": len(raw), "sha256": digest})
            inventory.append(
                {"component": name, "label": label, "sha256": digest}
            )
        configuration = component_configuration_v1(
            name, enforcement, authority_registry,
        )
        frozen = FrozenComponentMaterial(
            version, payloads, _canonical_json(configuration),
        )
        component_digest = _component_digest(name, frozen)
        digests[name] = ContextComponent(version, component_digest)
        components[name] = {
            "version": version,
            "files": records,
            "configuration": configuration,
            "component_digest": component_digest,
        }
    context = AdmissionContextV1(**digests)
    context_id = admission_context_id(context)
    epoch = _context_epoch(context_id)
    document = _canonical({
        "schema_version": 1,
        "state": PREPARED_STATE_V1,
        "components": components,
        "prepared_admission_context_id": context_id,
        "prepared_context_epoch": epoch,
    })
    return PreparedContextMaterialV1(
        document=document,
        prepared_admission_context_id=context_id,
        prepared_context_epoch=epoch,
        source_inventory_sha256=hashlib.sha256(
            CONTEXT_SOURCE_DIGEST_DOMAIN_V1 + _canonical(inventory)
        ).hexdigest(),
        material_sha256=hashlib.sha256(document).hexdigest(),
        context=context,
        pin=AdmissionContextPin(context_id, epoch),
    )


__all__ = [
    "CONTEXT_CATALOG_V1", "CONTEXT_CONTAINER_BASENAME_V1",
    "CONTEXT_MATERIAL_BASENAME_V1", "ContextMaterialError",
    "PreparedContextMaterialV1", "component_configuration_v1",
    "prepare_context_material_v1",
]
