"""The V1 context catalogue and the factory that freezes it (RM-0008).

The material is described once by the installer and rebuilt by the runtime
under its own barrier: section 9.4 forbids trusting the recorded description.
Two implementations of the same digest would diverge without anyone noticing,
so there is one, here, and both sides import it.

The factory **receives** an already open read session over the distribution and
opens nothing of its own: the authority to reach the filesystem stays with the
two doors the productive graph admits.

The historical decoder returns only inert evidence from authenticated stored
material. It cannot replace the source rebuild needed for runtime authority.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Mapping

CONTEXT_MATERIAL_BASENAME_V1 = "material-v1.json"
CONTEXT_CONTAINER_BASENAME_V1 = "context"
CONTEXT_SOURCE_DIGEST_DOMAIN_V1 = (
    b"metnos.executor-birth.context-source-inventory/v1\0"
)
MAXIMUM_CONTEXT_SOURCE_BYTES_V1 = 4 * 1024 * 1024
MAXIMUM_CONTEXT_DOCUMENT_BYTES_V1 = 1024 * 1024
PREPARED_STATE_V1 = "prepared_not_active"

# The V1 catalogue is owned by the code and reviewed as a whole: no caller can
# add, remove or rename an entry, and no configuration arrives from a document.
# ``enforcement_state`` says the truth about today, not the intention: section
# 9.2 requires ``prepared_only`` wherever the current code does not really
# apply the policy.  Group 3 made the last seven real — the linter decides, the
# vocabulary is consulted by the standard check, the authority registry is read
# under the barrier and proven consumed, the sandbox backend is measured and
# authenticated, and templates, primitives and code paths resolve from closed
# owners — so every component is now ``productive``.  Turning one back is a
# statement that its policy stopped being applied, never a convenience.
CONTEXT_CATALOG_V1: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    (
        "standard", "1",
        (
            "executor_standard.py", "presentation_contract.py",
            "code_file_paths.py", "naming_grammar.py",
        ),
        "productive",
    ),
    ("linter", "1", ("manifest_lint.py", "manifest_rules.py"), "productive"),
    (
        "vocabulary", "1",
        ("policy.py", "capabilities.py", "vocab.py"),
        "productive",
    ),
    ("authority_registry", "1", (), "productive"),
    (
        "sandbox_registry", "1",
        ("executor_birth_sandbox_registry_v1.py",),
        "productive",
    ),
    (
        "property_catalog", "2",
        ("executor_birth_properties.py", "executor_birth_property_runner.py",
         "executor_birth_preexercise.py"),
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
    (
        "template_allowlist", "1",
        ("executor_birth_template_table_v1.py",),
        "productive",
    ),
    (
        "primitive_allowlist", "1",
        ("executor_birth_properties.py", "executor_birth_primitive_table_v1.py"),
        "productive",
    ),
    ("dependency_allowlist", "1", ("code_file_paths.py",), "productive"),
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


@dataclass(frozen=True, slots=True)
class HistoricalContextMaterialV1:
    """Inert persisted evidence; source bytes have not been reconstructed."""

    context: object
    pin: object
    registry_document: bytes
    source_inventory_sha256: str
    material_sha256: str


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def decode_historical_context_material_v1(
    encoded: bytes,
) -> HistoricalContextMaterialV1:
    """Decode existing material; its caller must authenticate the material hash.

    The registry configuration is retained in full and can be rehashed. Other
    component digests are historical claims, not recomputed source evidence.
    No current source, catalog version or signing key is consulted.
    """
    from executor_birth_context import (
        FrozenComponentMaterial, _component_digest, _context_epoch,
    )
    from executor_birth_identity import (
        AdmissionContextV1, ContextComponent, admission_context_id,
    )
    from executor_birth_predecessor import AdmissionContextPin

    try:
        if (type(encoded) is not bytes
                or len(encoded) > MAXIMUM_CONTEXT_DOCUMENT_BYTES_V1):
            raise ValueError("material size")
        value = json.loads(encoded.decode("utf-8"))
        if (type(value) is not dict or _canonical(value) != encoded
                or set(value) != {
                    "schema_version", "state", "components",
                    "prepared_admission_context_id", "prepared_context_epoch",
                }
                or type(value["schema_version"]) is not int
                or value["schema_version"] != 1
                or value["state"] != PREPARED_STATE_V1):
            raise ValueError("material schema")
        components = value["components"]
        if (type(components) is not dict
                or set(components) != set(AdmissionContextV1.__dataclass_fields__)):
            raise ValueError("components")
        identities = {}
        inventory = []
        # Declaration order is the persisted V1 inventory order, not today's
        # source-file catalog. Individual file order comes from the document.
        for name in AdmissionContextV1.__dataclass_fields__:
            item = components[name]
            if type(item) is not dict or set(item) != {
                "version", "files", "configuration", "component_digest",
            }:
                raise ValueError("component schema")
            identities[name] = ContextComponent(item["version"], item["component_digest"])
            configuration = item["configuration"]
            fields = {"enforcement_state"}
            if name == "authority_registry":
                fields.add("registry")
            if (type(configuration) is not dict or set(configuration) != fields
                    or configuration["enforcement_state"] not in {
                        "prepared_only", "productive",
                    }
                    or type(item["files"]) is not list):
                raise ValueError("component configuration")
            labels = set()
            for record in item["files"]:
                if (type(record) is not dict
                        or set(record) != {"label", "size", "sha256"}):
                    raise ValueError("source record")
                label = record["label"]
                if (type(label) is not str or not label
                        or "\x00" in label or "\\" in label
                        or PurePosixPath(label).is_absolute()
                        or ".." in PurePosixPath(label).parts
                        or PurePosixPath(label).as_posix() != label or label in labels
                        or type(record["size"]) is not int or record["size"] < 0
                        or type(record["sha256"]) is not str
                        or re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is None):
                    raise ValueError("source record value")
                labels.add(label)
                inventory.append({
                    "component": name, "label": label, "sha256": record["sha256"],
                })
        registry_component = components["authority_registry"]
        configuration = registry_component["configuration"]
        if (registry_component["files"]
                or type(configuration["registry"]) is not dict
                or _component_digest("authority_registry", FrozenComponentMaterial(
                    registry_component["version"], {}, _canonical(configuration),
                )) != registry_component["component_digest"]):
            raise ValueError("registry binding")
        context = AdmissionContextV1(**identities)
        context_id = admission_context_id(context)
        epoch = _context_epoch(context_id)
        if (context_id != value["prepared_admission_context_id"]
                or epoch != value["prepared_context_epoch"]):
            raise ValueError("context binding")
        return HistoricalContextMaterialV1(
            context, AdmissionContextPin(context_id, epoch),
            _canonical(configuration["registry"]),
            hashlib.sha256(
                CONTEXT_SOURCE_DIGEST_DOMAIN_V1 + _canonical(inventory),
            ).hexdigest(),
            hashlib.sha256(encoded).hexdigest(),
        )
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError) as exc:
        raise ContextMaterialError("birth_prepared_set_invalid", exc) from None


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
    return _prepare_context_material_v1(sources, authority_registry, CONTEXT_CATALOG_V1)


def rebuild_previous_context_material_v1(
    sources, authority_registry: Mapping[str, object],
) -> PreparedContextMaterialV1:
    """Rebuild N from N's authenticated source during an explicit N+1 update.

    The distribution reader authenticates every byte, including the literal
    catalogue declaration. No historical module is executed and no persisted
    material claim replaces a source rebuild. Ordinary runtime construction
    continues to use the current closed catalogue.
    """
    import ast
    from contract_boundary_guard import _bounded_ast_metrics
    from executor_birth_identity import AdmissionContextV1
    from executor_birth_secure_fs import BirthSecureFSError

    try:
        encoded = sources.read_file(
            ("executor_birth_context_v1.py",),
            maximum=MAXIMUM_CONTEXT_SOURCE_BYTES_V1, exact_private=False,
        )
        tree = ast.parse(encoded.decode("utf-8"))
        _bounded_ast_metrics(tree)
        name = "CONTEXT_CATALOG_V1"
        stores = [node for node in ast.walk(tree) if isinstance(node, ast.Name)
                  and node.id == name and isinstance(node.ctx, (ast.Store, ast.Del))]
        declarations = [node for node in tree.body if isinstance(node, ast.AnnAssign)
                        and isinstance(node.target, ast.Name) and node.target.id == name]
        if len(stores) != 1 or len(declarations) != 1:
            raise ValueError("catalogue declaration")
        catalog = ast.literal_eval(declarations[0].value)
        if (type(catalog) is not tuple
                or len(catalog) != len(AdmissionContextV1.__dataclass_fields__)):
            raise ValueError("catalogue shape")
        for item in catalog:
            if type(item) is not tuple or len(item) != 4:
                raise ValueError("component shape")
            component, version, files, state = item
            if (type(component) is not str or type(version) is not str or not version
                    or type(files) is not tuple or state not in {"productive", "prepared_only"}
                    or any(type(label) is not str or not label
                           or label in {".", ".."} or any(c in label for c in "/\\\0")
                           for label in files)
                    or len(files) != len(set(files))
                    or component == "authority_registry" and files):
                raise ValueError("component value")
        if tuple(item[0] for item in catalog) != tuple(AdmissionContextV1.__dataclass_fields__):
            raise ValueError("component order")
    except BirthSecureFSError as exc:
        raise ContextMaterialError(exc.code, exc) from None
    except (UnicodeError, SyntaxError, RecursionError, ValueError, TypeError,
            OverflowError, MemoryError) as exc:
        raise ContextMaterialError("birth_context_catalog_invalid", exc) from None
    return _prepare_context_material_v1(sources, authority_registry, catalog)


def _prepare_context_material_v1(sources, authority_registry, catalog):
    """The common V1 framing, independent of the selected catalogue revision."""
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
    for name, version, files, enforcement in catalog:
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
    "prepare_context_material_v1", "rebuild_previous_context_material_v1",
]
