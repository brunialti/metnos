#!/usr/bin/env python3
"""
sign.py — utilità di firma e verifica manifest executor (Metnos v1.1 POC).

Funzioni principali:
    generate_keypair(name)       crea ed25519 keypair in ~/.config/metnos/keys/
    sign_executor(manifest_dir)  calcola digest del codice, aggiorna manifest,
                                 firma con la chiave autore, scrive .sig
    verify_executor(manifest_dir, trusted_pub_keys) verifica firma + digest

CLI:
    python3 sign.py keygen <name>             genera keypair
    python3 sign.py sign <manifest_dir>       firma con chiave 'author'
    python3 sign.py publish <manifest_dir>    firma e pubblica in un solo confine
    python3 sign.py verify <manifest_dir>     verifica con tutte le chiavi trusted
    python3 sign.py sign-all [name]           keygen-se-manca + firma TUTTI gli
                                              executor (usato dall'installer)
"""
import hashlib
import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, TypeAlias

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization

from logging_setup import get_logger
from manifest_code_digest import compute_code_digest, update_digest_in_text
import config as _C  # §7.11
log = get_logger(__name__)

KEYS_DIR = _C.PATH_USER_CONFIG / "keys"
DEFAULT_AUTHOR_KEY = "author"
BUILTIN_CONTRACTS_DIR = Path(__file__).resolve().parent / "builtin_executor_contracts"

TrustedPublic: TypeAlias = tuple[str, Ed25519PublicKey]


@dataclass(frozen=True, slots=True)
class SignerIdentity:
    name: str


class ManifestSignatureError(ValueError):
    """The supplied bytes are not authorized by any trusted public key."""


def _refuse_store_only_bypass(operation: str) -> None:
    """Keep this legacy/offline module from becoming a post-cutover writer."""
    from manifest_inventory import ManifestLayout, resolve_manifest_layout
    if resolve_manifest_layout() is ManifestLayout.STORE_ONLY:
        raise RuntimeError(
            f"{operation} is unavailable in STORE_ONLY; submit an Executor Birth intent"
        )


def sign_manifest_bytes(
    manifest_bytes: bytes,
    *,
    private_key: Ed25519PrivateKey,
) -> bytes:
    """Sign exactly ``manifest_bytes`` without consulting the filesystem."""
    if not isinstance(manifest_bytes, bytes):
        raise TypeError("manifest_bytes must be bytes")
    signer = getattr(private_key, "sign", None)
    if not callable(signer):
        raise TypeError("private_key must provide Ed25519 signing")
    signature = signer(manifest_bytes)
    if not isinstance(signature, bytes):
        raise TypeError("private_key returned a non-bytes signature")
    return signature


def verify_manifest_bytes(
    manifest_bytes: bytes,
    signature_bytes: bytes,
    *,
    trusted_publics: Iterable[TrustedPublic],
) -> SignerIdentity:
    """Return the signer name after verifying the exact supplied bytes.

    The function is intentionally pure: callers load keys and bytes before
    entering this boundary.  Only an Ed25519 key object, never a key name or a
    path, can grant trust here.
    """
    if not isinstance(manifest_bytes, bytes):
        raise TypeError("manifest_bytes must be bytes")
    if not isinstance(signature_bytes, bytes):
        raise TypeError("signature_bytes must be bytes")
    found = False
    for item in trusted_publics:
        try:
            name, public_key = item
        except (TypeError, ValueError) as exc:
            raise TypeError("trusted_publics entries must be (name, Ed25519PublicKey)") from exc
        if not isinstance(name, str) or not name.strip():
            raise TypeError("trusted public name must be non-empty text")
        if not isinstance(public_key, Ed25519PublicKey):
            raise TypeError("trusted public key must be an Ed25519PublicKey")
        found = True
        try:
            public_key.verify(signature_bytes, manifest_bytes)
        except InvalidSignature:
            continue
        return SignerIdentity(name=name)
    detail = "no trusted public keys" if not found else "signature is not trusted"
    raise ManifestSignatureError(detail)


def ensure_keys_dir():
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(KEYS_DIR, 0o700)


def generate_keypair(name):
    """Create a locally trusted Ed25519 pair with resumable file writes.

    The private component is installed first.  If publication of the public
    component is interrupted, the valid private bytes remain sufficient to
    derive that public component on a later installer retry.
    """
    ensure_keys_dir()
    priv = Ed25519PrivateKey.generate()
    priv_bytes = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_bytes = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    priv_path = KEYS_DIR / f"{name}_priv.bin"
    pub_path = KEYS_DIR / f"{name}_pub.bin"
    _atomic_replace_bytes(
        priv_path,
        priv_bytes,
        new_mode=0o600,
        preserve_existing_mode=False,
    )
    _atomic_replace_bytes(
        pub_path,
        pub_bytes,
        new_mode=0o644,
        preserve_existing_mode=False,
    )
    return priv_path, pub_path


def load_private(name):
    return Ed25519PrivateKey.from_private_bytes((KEYS_DIR / f"{name}_priv.bin").read_bytes())


def load_public(name):
    return Ed25519PublicKey.from_public_bytes((KEYS_DIR / f"{name}_pub.bin").read_bytes())


def restore_public_key(name):
    """Derive and atomically restore the public component of a private key."""
    private_key = load_private(name)
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_path = KEYS_DIR / f"{name}_pub.bin"
    _atomic_replace_bytes(
        public_path,
        public_bytes,
        new_mode=0o644,
        preserve_existing_mode=False,
    )
    return public_path


def list_trusted_publics():
    """Tutte le *_pub.bin nella keys dir sono trusted in v1.1 POC."""
    if not KEYS_DIR.exists():
        return []
    out = []
    for p in sorted(KEYS_DIR.glob("*_pub.bin")):
        try:
            out.append((p.stem.replace("_pub", ""), Ed25519PublicKey.from_public_bytes(p.read_bytes())))
        except Exception as _e:  # silent swallow (auto-fixed)
            log.warning("silent exception in %s: %s", __name__, _e)
    return out


def installation_manifest_paths():
    """Return every executor contract that a fresh install must trust.

    In-process builtins use the same signed admission boundary as subprocess
    executors, although their manifests live beside the runtime modules.
    """
    roots = (_C.PATH_EXECUTORS, BUILTIN_CONTRACTS_DIR)
    return sorted({path for root in roots for path in root.glob("**/manifest.toml")})


def _validate_capabilities_schema(manifest: dict, manifest_path: Path) -> None:
    """Rifiuta capabilities in forma `[capabilities]` (dict TOML) invece di
    `[[capabilities]]` (array of tables). Senza questo check il loader
    silenziosamente convertirebbe le chiavi dict in `list[str]` e l'admin
    UI esploderebbe con AttributeError (vedi fix 24/5/2026 F1).
    """
    caps = manifest.get("capabilities")
    if caps is None:
        return
    if isinstance(caps, dict):
        raise ValueError(
            f"{manifest_path}: `capabilities` deve essere array of tables "
            f"`[[capabilities]] name=\"...\" hint=[...]`, NON `[capabilities]` "
            f"(dict TOML). Vedi executors/find_files/manifest.toml come modello."
        )
    if not isinstance(caps, list):
        raise ValueError(
            f"{manifest_path}: `capabilities` tipo inatteso "
            f"{type(caps).__name__} (atteso list)."
        )
    for i, c in enumerate(caps):
        if not isinstance(c, dict) or "name" not in c:
            raise ValueError(
                f"{manifest_path}: capabilities[{i}] deve essere table con "
                f"campo `name`. Got: {c!r}"
            )


def _atomic_replace_bytes(
    path: Path,
    payload: bytes,
    *,
    new_mode: int = 0o600,
    preserve_existing_mode: bool = True,
) -> None:
    """Durably replace one complete sibling file without truncating it.

    The caller prepares and validates the complete payload first.  Existing
    permissions survive by default; security-sensitive callers may require
    the selected mode instead.  A failed replacement can leave only this
    invocation's uniquely named temporary file, which the invocation removes
    when control returns to Python.
    """
    path = Path(path)
    if not isinstance(payload, bytes):
        raise TypeError("atomic replacement payload must be bytes")
    if not isinstance(new_mode, int) or new_mode < 0 or new_mode > 0o7777:
        raise ValueError("new file mode must be a permission mask")
    if not isinstance(preserve_existing_mode, bool):
        raise TypeError("preserve_existing_mode must be boolean")
    try:
        current = path.lstat()
    except FileNotFoundError:
        mode = new_mode
    else:
        if not stat.S_ISREG(current.st_mode):
            raise ValueError(f"atomic replacement target is not a regular file: {path}")
        mode = (
            stat.S_IMODE(current.st_mode)
            if preserve_existing_mode
            else new_mode
        )

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        try:
            os.fchmod(descriptor, mode)
        except (AttributeError, OSError):
            # Windows may not implement the POSIX mode fully.  ``mkstemp`` is
            # still private by default and the replacement remains atomic.
            if os.name != "nt":
                raise
        handle = os.fdopen(descriptor, "wb")
        descriptor = -1  # ownership transferred to ``handle``
        with handle:
            written = handle.write(payload)
            if written != len(payload):  # pragma: no cover - buffered IO contract
                raise OSError("short write while preparing atomic replacement")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if sys.platform.startswith("linux"):
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_descriptor = os.open(path.parent, flags)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _ensure_lang_state_companion(manifest: dict, manifest_dir: Path) -> None:
    """Auto-genera `manifest.lang_state.json` se mancante e description e'
    in schema multilingua (ADR 0092). Evita drift quando un manifest viene
    creato direttamente in nuovo schema senza passare per la migrazione
    (vedi fix 24/5/2026 F2: 12 manifest senza companion).
    """
    desc = manifest.get("description")
    if not isinstance(desc, dict):
        return  # schema flat legacy: lang_state non richiesto
    state_path = manifest_dir / "manifest.lang_state.json"
    if state_path.is_file():
        return
    from i18n_materializer import (
        decode_language_state,
        migrate_language_state_bytes,
    )

    # The empty legacy object is a convenient, schema-independent request to
    # enumerate every localized surface and emit canonical v1 bytes.
    state_bytes = migrate_language_state_bytes(
        b"{}", manifest=manifest,
    ).state_bytes
    # Keep the validation explicit at the write boundary: no malformed or
    # non-canonical state bytes may reach a final authoring path.
    decode_language_state(state_bytes, manifest=manifest)
    manifest_mode = stat.S_IMODE(
        (manifest_dir / "manifest.toml").stat().st_mode,
    )
    _atomic_replace_bytes(
        state_path,
        state_bytes,
        new_mode=manifest_mode,
    )


def _sign_executor_under_catalog_lock(manifest_dir, key_name=DEFAULT_AUTHOR_KEY):
    """Implement the authoring write while the global catalog lock is held.

    I manifest legacy restano firmabili durante la migrazione. Chi dichiara
    l'Executor Standard, invece, deve superare il profilo deterministico legato
    al lifecycle prima che venga emessa una firma. In questo modo generatori e
    importatori non possono usare la firma come scorciatoia di attivazione.
    """
    from executor_birth_legacy_gate import deny_legacy_signing_api
    deny_legacy_signing_api("sign_executor")
    manifest_dir = Path(manifest_dir)
    manifest_path = manifest_dir / "manifest.toml"
    sig_path = manifest_dir / "manifest.toml.sig"

    import tomllib
    original_manifest_bytes = manifest_path.read_bytes()
    try:
        original_manifest_text = original_manifest_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"manifest non UTF-8: {manifest_path}") from exc
    manifest = tomllib.loads(original_manifest_text)
    code_files = manifest.get("code", {}).get("files", [])
    if not code_files:
        raise ValueError("manifest senza [code].files")

    _validate_capabilities_schema(manifest, manifest_path)
    digest = compute_code_digest(manifest_dir, code_files)

    # Costruisci e valida l'intero contratto finale in memoria.  Nessun file
    # autorevole viene toccato finche' anche la firma e' pronta.
    final_manifest_text = update_digest_in_text(original_manifest_text, digest)
    final_manifest_bytes = final_manifest_text.encode("utf-8")
    final_manifest = tomllib.loads(final_manifest_text)
    if final_manifest.get("executor_standard") is not None:
        from executor_standard import validate_for_lifecycle
        findings = validate_for_lifecycle(
            final_manifest,
            require_declaration=True,
        )
        if findings:
            summary = "; ".join(
                f"{finding.code}:{finding.message}" for finding in findings[:8]
            )
            raise ValueError(f"executor standard admission failed: {summary}")

    priv = load_private(key_name)
    signature = sign_manifest_bytes(final_manifest_bytes, private_key=priv)

    manifest_mode = stat.S_IMODE(manifest_path.stat().st_mode)
    _ensure_lang_state_companion(final_manifest, manifest_dir)
    if final_manifest_bytes != original_manifest_bytes:
        _atomic_replace_bytes(
            manifest_path,
            final_manifest_bytes,
            new_mode=manifest_mode,
        )
    _atomic_replace_bytes(sig_path, signature, new_mode=manifest_mode)
    return digest, sig_path


def sign_executor(manifest_dir, key_name=DEFAULT_AUTHOR_KEY):
    """Aggiorna digest e firma offline sotto l'esclusione globale del catalogo.

    La firma non pubblica una revisione. Il lock impedisce però che un cutover
    costruisca la propria fotografia mentre questi file sono a metà modifica.
    """
    from executor_birth_legacy_gate import deny_legacy_signing_api
    deny_legacy_signing_api("sign_executor")
    from contract_store import catalog_admission_lock

    with catalog_admission_lock():
        return _sign_executor_under_catalog_lock(manifest_dir, key_name)


def verify_executor(manifest_dir):
    """
    Verifica firma manifest + digest dei file di codice.
    Ritorna (ok, info_dict).
    """
    manifest_dir = Path(manifest_dir)
    manifest_path = manifest_dir / "manifest.toml"
    sig_path = manifest_dir / "manifest.toml.sig"

    if not sig_path.exists():
        return False, {"reason": f"file firma assente: {sig_path}"}

    manifest_bytes = manifest_path.read_bytes()
    signature = sig_path.read_bytes()

    trusted = list_trusted_publics()
    if not trusted:
        return False, {"reason": "nessuna chiave trusted configurata in ~/.config/metnos/keys/"}

    try:
        verified_by = verify_manifest_bytes(
            manifest_bytes,
            signature,
            trusted_publics=trusted,
        )
    except ManifestSignatureError:
        return False, {"reason": "firma non verificata da alcuna chiave trusted"}

    import tomllib
    try:
        manifest = tomllib.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return False, {"reason": f"manifest non valido: {exc}"}
    declared = manifest.get("code", {}).get("digest", "")
    code_files = manifest.get("code", {}).get("files", [])
    actual = compute_code_digest(manifest_dir, code_files)

    if declared != actual:
        return False, {"reason": f"digest mismatch: declared={declared} actual={actual}"}

    return True, {"signed_by": verified_by.name, "digest": actual}


def _authoring_manifest_ref(
    manifest_dir: str | Path,
    *,
    require_publishable: bool = True,
):
    """Resolve one authoring directory through the shared source inventory.

    Publication identity is never inferred from the executor name or from a
    caller-provided origin.  The explicit authoring inventory remains usable
    after the runtime catalog has switched to immutable store bindings.
    """

    from manifest_inventory import (
        ManifestStatus,
        inventory_authoring_manifests,
    )

    target = (Path(manifest_dir) / "manifest.toml").resolve(strict=True)
    inventory = inventory_authoring_manifests()
    matches = tuple(
        ref for ref in inventory.manifests
        if ref.manifest_path.resolve(strict=True) == target
    )
    if len(matches) != 1:
        raise ValueError(
            f"authoring manifest must resolve to one inventoried contract: {target}"
        )
    ref = matches[0]
    if require_publishable and ref.status not in {
        ManifestStatus.ADMITTED,
        ManifestStatus.DISABLED,
    }:
        raise ValueError(f"contract is not publishable: {ref.contract_id}")
    return ref


def publish_executor(manifest_dir, key_name=DEFAULT_AUTHOR_KEY):
    """Publish one technical authoring change through the live store.

    This is deliberately not ``sign_executor()`` followed by an import.  The
    publisher owns the sole contract lock, recomputes the code digest, signs
    in memory, commits the immutable generation and reconciles authoring as
    one conditional operation.
    """

    from executor_birth_legacy_gate import deny_legacy_signing_api
    deny_legacy_signing_api("publish_executor")
    _refuse_store_only_bypass("publish")
    from contract_store import (
        ContractStoreError,
        current_revision_id,
        prepare_technical_draft,
        publish_technical_update,
    )
    from i18n_pipeline import reconcile_published_contract_registry

    ref = _authoring_manifest_ref(manifest_dir)
    private_key = load_private(key_name)
    trusted = tuple(list_trusted_publics())
    if not trusted:
        raise ValueError("no trusted public keys are configured")
    try:
        # The live generation still declares the old code digest while the
        # authoring tree already contains the proposed update. Reading it via
        # ``current_manifest`` would therefore reject every real code change.
        # The lightweight pointer is only the CAS selector; the publisher
        # authenticates the complete old generation under its writer lock.
        expected = current_revision_id(ref)
    except ContractStoreError as exc:
        # A new source has no directory; an interrupted first publication can
        # already have its immutable binding (and possibly its exact final
        # generation) but no pointer.  Passing ``None`` delegates both cases
        # to the locked publisher, whose initial-history check accepts only a
        # virgin store or that exact recoverable postcondition.  Unrelated
        # history and every other defect remain fail-closed there.
        if exc.code not in {"contract_directory_missing", "current_missing"}:
            raise
        expected = None
    draft = prepare_technical_draft(ref)
    return publish_technical_update(
        ref,
        expected_generation_id=expected,
        draft=draft,
        private_key=private_key,
        trusted_publics=trusted,
        registry_reconciler=reconcile_published_contract_registry,
    )


def publish_authoring_update(
    manifest_dir,
    key_name=DEFAULT_AUTHOR_KEY,
):
    """Make one local authoring update live in the active layout.

    Before the global cutover, signing is the legacy admission boundary.  In
    store-only mode :func:`publish_executor` signs in memory while owning the
    sole writer lock.  The two branches are mutually exclusive: this helper
    never implements a sign-then-publish sequence.
    """

    from executor_birth_legacy_gate import deny_legacy_signing_api
    deny_legacy_signing_api("publish_authoring_update")
    from manifest_inventory import ManifestLayout, resolve_manifest_layout

    if resolve_manifest_layout() is ManifestLayout.AUTHORING:
        digest, signature_path = sign_executor(manifest_dir, key_name)
        return digest, signature_path, None
    publication = publish_executor(manifest_dir, key_name)
    manifest_dir = Path(manifest_dir)
    import tomllib
    manifest = tomllib.loads(
        (manifest_dir / "manifest.toml").read_text(encoding="utf-8"),
    )
    digest = str((manifest.get("code") or {}).get("digest") or "")
    signature_path = manifest_dir / "manifest.toml.sig"
    return digest, signature_path, publication


def retire_executor_contract(
    manifest_dir,
    *,
    actor: str,
    reason: str,
    key_name=DEFAULT_AUTHOR_KEY,
):
    """Retire one live store contract before its source is removed.

    The wrapper owns every productive boundary shared by operational callers:
    structural source identity, key loading, full verification of the live
    generation, durable idempotent audit and registry reconciliation.  An
    exact retry of the same retirement is accepted; a different tombstone
    remains retired and requires the explicit reactivation protocol.
    """
    from audit_jsonl import append_unique_jsonl
    from contract_store import (
        ContractRetirement,
        ContractStoreError,
        current_contract,
        retire,
    )
    from i18n_pipeline import reconcile_published_contract_registry

    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("retirement actor is required")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("retirement reason is required")
    actor = actor.strip()
    reason = reason.strip()
    ref = _authoring_manifest_ref(manifest_dir, require_publishable=False)
    private_key = load_private(key_name)
    trusted = tuple(list_trusted_publics())
    if not trusted:
        raise ValueError("no trusted public keys are configured")
    current = current_contract(ref, trusted_publics=trusted)
    if isinstance(current, ContractRetirement):
        if current.actor != actor or current.reason != reason:
            raise ContractStoreError("contract_retired", current.retirement_id)
        expected = current.previous_generation_id
    else:
        expected = current.generation_id
    if expected is None:  # pragma: no cover - impossible for store revisions
        raise ContractStoreError("revision_id_missing", str(ref.contract_id))

    audit_path = _C.PATH_USER_STATE / "contract-publications.audit.jsonl"
    return retire(
        ref,
        expected_generation_id=expected,
        actor=actor,
        reason=reason,
        private_key=private_key,
        trusted_publics=trusted,
        audit_sink=lambda event: append_unique_jsonl(audit_path, event),
        registry_reconciler=reconcile_published_contract_registry,
    )


def reactivate_executor_contract(
    manifest_dir,
    *,
    actor: str,
    reason: str,
    key_name=DEFAULT_AUTHOR_KEY,
):
    """Explicitly reactivate one authenticated retirement from authoring.

    Ordinary publication never crosses a tombstone.  Reinstall flows call
    this wrapper only after that boundary has reported ``contract_retired``;
    the wrapper authenticates the current tombstone again under the store's
    CAS protocol and records the authorization durably before committing.
    """
    from executor_birth_legacy_gate import deny_legacy_signing_api
    deny_legacy_signing_api("reactivate_executor_contract")
    _refuse_store_only_bypass("reactivate")
    from audit_jsonl import append_unique_jsonl
    from contract_store import (
        ContractRetirement,
        ContractStoreError,
        current_contract,
        prepare_technical_draft,
        reactivate_technical_update,
    )
    from i18n_pipeline import reconcile_published_contract_registry

    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("reactivation actor is required")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reactivation reason is required")
    actor = actor.strip()
    reason = reason.strip()
    ref = _authoring_manifest_ref(manifest_dir, require_publishable=False)
    private_key = load_private(key_name)
    trusted = tuple(list_trusted_publics())
    if not trusted:
        raise ValueError("no trusted public keys are configured")
    current = current_contract(ref, trusted_publics=trusted)
    if not isinstance(current, ContractRetirement):
        raise ContractStoreError(
            "contract_not_retired",
            str(current.generation_id or ref.contract_id),
        )
    draft = prepare_technical_draft(ref)
    audit_path = _C.PATH_USER_STATE / "contract-publications.audit.jsonl"
    return reactivate_technical_update(
        ref,
        expected_retirement_id=current.retirement_id,
        draft=draft,
        actor=actor,
        reason=reason,
        private_key=private_key,
        trusted_publics=trusted,
        audit_sink=lambda event: append_unique_jsonl(audit_path, event),
        registry_reconciler=reconcile_published_contract_registry,
    )


def rollback_executor_contract(
    manifest_dir,
    *,
    expected_generation_id: str,
    target_generation_id: str,
    actor: str,
    reason: str,
):
    """Move one live binding back to an authenticated immutable generation."""
    from executor_birth_legacy_gate import deny_legacy_signing_api
    deny_legacy_signing_api("rollback_executor_contract")
    _refuse_store_only_bypass("rollback")
    from audit_jsonl import append_unique_jsonl
    from contract_store import rollback
    from i18n_pipeline import reconcile_published_contract_registry

    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("rollback actor is required")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("rollback reason is required")
    ref = _authoring_manifest_ref(manifest_dir)
    trusted = tuple(list_trusted_publics())
    if not trusted:
        raise ValueError("no trusted public keys are configured")
    audit_path = _C.PATH_USER_STATE / "contract-publications.audit.jsonl"
    return rollback(
        ref,
        expected_generation_id=expected_generation_id,
        target_generation_id=target_generation_id,
        actor=actor.strip(),
        reason=reason.strip(),
        trusted_publics=trusted,
        audit_sink=lambda event: append_unique_jsonl(audit_path, event),
        registry_reconciler=reconcile_published_contract_registry,
    )


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    cmd = sys.argv[1]

    if cmd == "keygen":
        name = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_AUTHOR_KEY
        priv, pub = generate_keypair(name)
        print(f"keypair generato: {priv.name} (priv 600), {pub.name} (pub 644) in {KEYS_DIR}")

    elif cmd == "sign":
        _refuse_store_only_bypass("sign")
        if len(sys.argv) < 3:
            print("Usage: sign <manifest_dir> [key_name]"); sys.exit(2)
        manifest_dir = sys.argv[2]
        key_name = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_AUTHOR_KEY
        digest, sig_path = sign_executor(manifest_dir, key_name)
        print(
            f"firmato offline: digest={digest} sig={sig_path}; "
            "la sorgente NON e' stata pubblicata e non e' viva"
        )

    elif cmd == "publish":
        _refuse_store_only_bypass("publish")
        if len(sys.argv) < 3:
            print("Usage: publish <manifest_dir> [key_name]"); sys.exit(2)
        manifest_dir = sys.argv[2]
        key_name = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_AUTHOR_KEY
        result = publish_executor(manifest_dir, key_name)
        print(
            "pubblicato: "
            f"contract={result.contract_id} "
            f"generation={result.current_generation_id} "
            f"repeated={str(result.repeated).lower()}"
        )

    elif cmd == "verify":
        if len(sys.argv) < 3:
            print("Usage: verify <manifest_dir>"); sys.exit(2)
        manifest_dir = sys.argv[2]
        ok, info = verify_executor(manifest_dir)
        if ok:
            print(f"OK signed_by={info['signed_by']} digest={info['digest']}")
            sys.exit(0)
        else:
            print(f"FAIL: {info['reason']}")
            sys.exit(1)

    elif cmd == "sign-all":
        _refuse_store_only_bypass("sign-all")
        # Firma-di-massa per l'INSTALLAZIONE: genera la keypair locale 'author'
        # se manca, poi firma OGNI executor (manifest.toml) sotto la executors
        # dir. Senza questo passo una install fresca lascia il catalogo VUOTO:
        # gli .sig spediti sono firmati con la chiave dell'autore upstream, NON
        # trusted sulla macchina dell'utente. Idempotente; rispetta
        # METNOS_INSTALL_ROOT (executors dir derivata).
        key_name = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_AUTHOR_KEY
        if not (KEYS_DIR / f"{key_name}_priv.bin").exists():
            generate_keypair(key_name)
            print(f"keypair '{key_name}' generato in {KEYS_DIR}")
        ex_root = _C.PATH_EXECUTORS
        manifests = installation_manifest_paths()
        ok_n = 0
        failed = []
        for m in manifests:
            try:
                sign_executor(str(m.parent), key_name)
                ok_n += 1
            except Exception as e:  # noqa: BLE001
                failed.append((str(m.parent), str(e)))
        print(f"sign-all: {ok_n} contratti executor firmati, {len(failed)} errori "
              f"(executors dir: {ex_root}; builtin in-process inclusi)")
        for d, e in failed:
            print(f"  FAIL {d}: {e}")
        sys.exit(1 if failed else 0)

    else:
        print(__doc__); sys.exit(2)


if __name__ == "__main__":
    main()
