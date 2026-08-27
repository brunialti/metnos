"""Shared apparatus for the increment 2B certification.

Every root here is a temporary one and the installer configuration is pointed
at it, so the real installation of the machine that runs the suite is never
opened, read or written.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

def build_id() -> str:
    """The identifier the provisioner derives from its own loaded code."""
    return provisioner()._provisioner_build_id_v1()


def provisioner():
    return importlib.import_module("install.birth_authority_provisioner")


def installer_layout_module():
    return importlib.import_module("install.birth_authority_provisioning")


def private_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def public_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def write(path: Path, payload: bytes, mode: int) -> None:
    path.write_bytes(payload)
    os.chmod(path, mode)


def make_config(tmp_path: Path, *, author=None, extra=(), operator=False) -> Path:
    """Build one isolated installer configuration root.

    ``operator`` installs the ordinary, valid public registries; the tests that
    certify their absence or their defects install their own instead.
    """
    base = tmp_path / "config"
    (base / "birth" / "operator-input-v1").mkdir(mode=0o755, parents=True)
    os.chmod(base / "birth", 0o755)
    if author is not None or extra:
        keys = base / "keys"
        keys.mkdir(mode=0o700)
        if author is not None:
            write(keys / "author_priv.bin", private_bytes(author), 0o600)
            write(keys / "author_pub.bin", public_bytes(author), 0o644)
        for name, payload, mode in extra:
            write(keys / name, payload, mode)
    if operator:
        complete_operator_input(base)
    return base


def use_config(monkeypatch, base: Path) -> None:
    """Point every installer resolver at the temporary installation.

    The distribution is staged too: a working tree is group-writable, and the
    capability refuses such a source on purpose, so a test that wants a real
    provisioning must provide a real installation.
    """
    runtime_config = importlib.import_module("config")
    monkeypatch.setattr(runtime_config, "PATH_USER_CONFIG", base)
    # The mutable roots move too: a public certification must never read or
    # write the installation of the machine that runs it.
    for name in ("PATH_USER_STATE", "PATH_USER_DATA"):
        location = base.parent / name.lower()
        location.mkdir(mode=0o700, parents=True, exist_ok=True)
        monkeypatch.setattr(runtime_config, name, location)
    stage_runtime_sources(base.parent, monkeypatch)


def open_layout(monkeypatch, base: Path):
    use_config(monkeypatch, base)
    return installer_layout_module().open_birth_provisioning_layout_v1()


def provision(monkeypatch, base: Path):
    """Run one whole provisioning attempt on its own session."""
    module = provisioner()
    use_config(monkeypatch, base)
    return module.ensure_executor_birth_authorities_prepared()


def canonical_json(value: object) -> bytes:
    import json

    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def approval_document(actors=None) -> bytes:
    """One canonical operator approval registry, with no private key."""
    import base64

    key = public_bytes(Ed25519PrivateKey.generate())
    return canonical_json({
        "actors": actors if actors is not None else {
            "operator": {"key_ids": ["operator-key"], "scopes": ["birth"]}
        },
        "keys": {"operator-key": base64.b64encode(key).decode("ascii")},
        "revision": 1,
        "schema_version": 1,
    })


def semantic_document(names=("review.pub",), *, revoked=()) -> bytes:
    import importlib

    review = importlib.import_module("executor_birth_semantic_review")
    kinds = sorted(item.value for item in review.IndependentEvidenceKind)
    verifiers = {
        f"review-key-{index}": {
            "path": f"public/{name}",
            "status": "revoked" if name in revoked else "active",
        }
        for index, name in enumerate(names)
    }
    return canonical_json({
        "evidence_dir": "evidence",
        "owners": {kind: ["independent-owner"] for kind in kinds},
        "verifiers": verifiers,
        "versions": {kind: ["v1"] for kind in kinds},
    })


def install_operator_input(
    base: Path, *, approval: bytes | None = None, semantic: bytes | None = None,
    keys=None,
) -> None:
    """Write the two public registries the administrator is expected to install."""
    location = base / "birth" / "operator-input-v1"
    if approval is not None:
        write(location / "approval-authority.json", approval, 0o644)
    if semantic is not None:
        write(location / "semantic-authority.json", semantic, 0o644)
    if keys is not None:
        container = location / "semantic-public"
        container.mkdir(mode=0o755, exist_ok=True)
        for name, payload in keys.items():
            write(container / name, payload, 0o644)


def complete_operator_input(base: Path) -> dict[str, bytes]:
    """The ordinary, valid operator input of a supported installation."""
    keys = {"review.pub": public_bytes(Ed25519PrivateKey.generate())}
    install_operator_input(
        base,
        approval=approval_document(),
        semantic=semantic_document(tuple(keys)),
        keys=keys,
    )
    return keys


def transaction_root(base: Path) -> Path:
    """The one transaction directory of an isolated root."""
    module = provisioner()
    roots = [
        item for item in (base / "birth").iterdir()
        if item.name.startswith(module.TRANSACTION_PREFIX_V1)
    ]
    assert len(roots) == 1, roots
    return roots[0]


def staged_author_store(base: Path) -> Path:
    return transaction_root(base) / "author-root-v1"


def stage_runtime_sources(tmp_path: Path, monkeypatch) -> Path:
    """Copy the catalogued distribution files into an isolated, safe root.

    The working tree of a developer is group-writable, and the capability
    refuses such a source on purpose: a real installation is not.
    """
    import importlib
    import shutil

    module = provisioner()
    runtime_config = importlib.import_module("config")
    stage = tmp_path / "distribution"
    if not stage.is_dir():
        stage.mkdir(mode=0o755, parents=True)
        for _, _, files, _ in module._CONTEXT_CATALOG_V1:
            for name in files:
                shutil.copy(
                    Path(runtime_config.PATH_RUNTIME) / name, stage / name,
                )
                os.chmod(stage / name, 0o644)
    monkeypatch.setattr(runtime_config, "PATH_RUNTIME", stage)
    return stage


_MANIFEST_TEMPLATE_V1 = '''manifest_format = "1.0"
executor_standard = "metnos.executor/1.0"
name = "{name}"
version = "1.0.0"

[description]
it = "SCOPO: prova. PATTERN: sample(). NON: modifica. OUT: results=[]."
en = "SCOPO: test. PATTERN: sample(). NON: modify. OUT: results=[]."

[code]
files = ["{code_file}"]
digest = "{code_digest}"

[output]
schema_inline = "{{ ok: bool, results: list }}"

[[capabilities]]
name = "compute:pure"
hint = []

[[tests]]
name = "sample"
input = {{}}
expect = {{ ok = true }}

[args]
type = "object"
required = []

[args.properties.query]
type = "string"

[args.properties.query.description]
it = "Testo da cercare."
en = "Text to find."
'''


def _hash_text(value: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_contract_source(tmp_path: Path):
    """One signed manifest source, ready for a real publication."""
    import hashlib
    import importlib
    import tomllib

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    materializer = importlib.import_module("i18n_materializer")
    inventory_module = importlib.import_module("manifest_inventory")
    sign_module = importlib.import_module("sign")

    root = tmp_path / "sources"
    directory = root / "sample"
    directory.mkdir(parents=True)
    code = directory / "sample.py"
    code.write_text(
        "def invoke(args):\n    return {'results': []}\n", encoding="utf-8",
    )
    digest = "sha256:" + hashlib.sha256(code.read_bytes()).hexdigest()
    manifest = directory / "manifest.toml"
    manifest.write_text(
        _MANIFEST_TEMPLATE_V1.format(
            name="read_files", code_file=code.name, code_digest=digest,
        ),
        encoding="utf-8",
    )
    parsed = tomllib.loads(manifest.read_text(encoding="utf-8"))
    selectors = materializer.manifest_language_selectors(parsed)
    state = {
        "schema_version": 1,
        "selectors": {
            selector: {
                language: {
                    "version_hash": _hash_text(text),
                    "source_lang": None,
                    "source_hash": None,
                }
                for language, text in table.items()
            }
            for selector, table in selectors.items()
        },
    }
    (directory / "manifest.lang_state.json").write_bytes(
        materializer.encode_language_state(state, manifest=parsed)
    )
    private = Ed25519PrivateKey.generate()
    (directory / "manifest.toml.sig").write_bytes(
        sign_module.sign_manifest_bytes(
            manifest.read_bytes(), private_key=private,
        )
    )
    inventory = inventory_module.inventory_manifests((
        inventory_module.ManifestSource(
            inventory_module.ManifestOrigin.EXPLICIT, root,
            min_depth=1, max_depth=1, allowed_code_roots=(root,),
        ),
    ))
    assert not inventory.problems, inventory.problems
    ref = next(item for item in inventory.manifests if item.name == "read_files")
    return ref, private, (("author", private.public_key()),)


def birth_candidate_snapshot(ref, tmp_path: Path):
    """One admitted candidate snapshot of the same contract, ready to commit."""
    import hashlib
    import importlib
    import tomllib

    import tomlkit

    store = importlib.import_module("contract_store")
    snapshot_module = importlib.import_module("executor_birth_snapshot")

    (ref.manifest_dir / "sample.py").write_text(
        "def invoke(args):\n    return {'results': [], 'birth': 2}\n",
        encoding="utf-8",
    )
    store.prepare_technical_draft(ref)
    document = tomlkit.parse((ref.manifest_dir / "manifest.toml").read_text())
    code_bytes = (ref.manifest_dir / "sample.py").read_bytes()
    document["code"]["digest"] = (
        "sha256:" + hashlib.sha256(code_bytes).hexdigest()
    )
    (ref.manifest_dir / "manifest.toml").write_text(tomlkit.dumps(document))
    source = tmp_path / "birth-candidate"
    source.mkdir()
    parsed = tomllib.loads((ref.manifest_dir / "manifest.toml").read_text())
    for name in ("manifest.toml", "manifest.lang_state.json", *parsed["code"]["files"]):
        destination = source / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ref.manifest_dir / name).read_bytes())
    return snapshot_module.acquire_candidate_snapshot(
        source, private_parent=tmp_path,
    )


def load_staged_author_store(monkeypatch, base: Path):
    """Read the staged author store the way the new layout is meant to be read.

    The historical path loader demands a private ``public`` directory, while
    the signed layout gives that directory the integrity profile, so the
    productive entry for a prepared store is the session loader.
    """
    import importlib

    keystore = importlib.import_module("executor_birth_keystore")
    module = provisioner()
    layout = open_layout(monkeypatch, base)
    session = layout.birth_session
    components = tuple(
        transaction_root(base).relative_to(base / "birth").parts
    ) + ("author-root-v1",)
    with session:
        with session.global_lock(exclusive=True, create=True):
            return keystore._load_birth_keystore_in_session(components, session)


def installed_author_store(base: Path) -> Path:
    return base / "birth" / "author-root-v1"


def installed_marker(base: Path) -> Path:
    return base / "birth" / "prepared-v1.json"


def installed_set(base: Path) -> Path:
    import json

    marker = json.loads(installed_marker(base).read_bytes())
    return base / "birth" / "authority-sets" / marker["set_id"]


def load_installed_author_store(monkeypatch, base: Path):
    """Read the installed author store through the session loader."""
    import importlib

    keystore = importlib.import_module("executor_birth_keystore")
    layout = open_layout(monkeypatch, base)
    session = layout.birth_session
    with session:
        with session.global_lock(exclusive=True, create=True):
            return keystore._load_birth_keystore_in_session(
                ("author-root-v1",), session,
            )


def provision_until_verified(monkeypatch, base: Path):
    """Drive one transaction to ``verified`` and leave it there.

    The complete run publishes the finals and removes its transaction, so a
    test that wants to look inside the journal has to stop before that.
    """
    module = provisioner()
    layout = open_layout(monkeypatch, base)
    session = layout.birth_session
    transaction = module.new_transaction_id_v1()
    journal = module._TransactionJournalV1(session, transaction)
    opened = module._resolve_author_source_v1()
    try:
        source = module.acquire_author_source_v1(opened)
    finally:
        opened.close()
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.create_root()
            journal.write_header(
                module.TransactionHeaderV1(transaction, build_id())
            )
            journal.ensure_checkpoints()
            zero = module.CheckpointV1(
                transaction, 0, None, module.ProvisioningStateV1.created, (),
                module.empty_digests_v1(), None,
            )
            journal.append(zero)
            acquired = module._record_author_source_v1(journal, zero, source)
            staged = module._stage_and_record_v1(
                session, journal, acquired, source,
            )
            module._advance_to_authorities_v1(session, journal, layout, staged)
    return transaction


def prepare_or_defer(monkeypatch, base: Path):
    """The first installer entry: inspects, resumes, or defers."""
    module = provisioner()
    use_config(monkeypatch, base)
    return module.prepare_or_defer_until_legacy_author_exists()
