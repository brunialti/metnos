#!/usr/bin/env python3
"""i18n_migrate_manifests — migra description/affinity da manifest TOML al DB i18n.

Fase 2 i18n (1/5/2026 sera).

Per ogni manifest.toml in <install_root>/executors/* + ~/.local/share/metnos/executors/*:
- Se `description` in manifest e' stringa flat → INSERT in DB con (key=<name>.description, lang=it, text=<value>)
- Se `description` e' dict {it,en,...} → INSERT N rows (una per lingua)
- Idempotente: usa INSERT OR REPLACE

Convention chiavi:
- <executor_name>.description
- <executor_name>.affinity (lista serializzata JSON)

Uso:
    python3 -m admin.i18n_migrate_manifests [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import i18n

# ADR 0148 rename-resilient
import config as _C  # noqa: E402
EXEC_DIRS = [
    _C.PATH_EXECUTORS,
    _C.PATH_USER_DATA / "executors",
]
DEFAULT_LANG = "it"


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _atomic_bytes(path: Path, payload: bytes) -> None:
    """Replace one authoring companion without an observable partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _migrate_contract_language_states_locked(*, dry_run: bool = False) -> dict:
    """Preflight and migrate every admitted companion to canonical v1.

    All manifests and legacy states are parsed before the first write.  A
    failed or ambiguous migration therefore leaves the entire authoring
    corpus untouched; an interrupted write phase is safely resumable.
    """
    from i18n_materializer import migrate_language_state_bytes
    from manifest_inventory import inventory_authoring_manifests

    inventory = inventory_authoring_manifests()
    if inventory.problems:
        detail = "; ".join(
            f"{problem.code}:{problem.path}" for problem in inventory.problems[:12]
        )
        raise RuntimeError(f"manifest inventory is not clean: {detail}")

    planned: list[tuple[Path, bytes, dict]] = []
    for ref in inventory.installed():
        manifest_bytes = ref.manifest_path.read_bytes()
        manifest = tomllib.loads(manifest_bytes.decode("utf-8"))
        state_path = ref.manifest_dir / "manifest.lang_state.json"
        old_bytes = state_path.read_bytes() if state_path.is_file() else b"{}"
        migration = migrate_language_state_bytes(old_bytes, manifest=manifest)
        evidence = {
            "contract_id": str(ref.contract_id),
            "state_relative": ref.manifest_relative.replace(
                "manifest.toml", "manifest.lang_state.json",
            ),
            "before_hash": _sha256(old_bytes),
            "after_hash": _sha256(migration.state_bytes),
            "changed": old_bytes != migration.state_bytes,
            "added_entries": list(migration.added_entries),
            "dropped_entries": list(migration.dropped_entries),
            "normalized_language_tags": list(
                migration.normalized_language_tags,
            ),
            "cleared_provenance": list(migration.cleared_provenance),
        }
        planned.append((state_path, migration.state_bytes, evidence))

    if not dry_run:
        for state_path, state_bytes, _evidence in planned:
            if not state_path.is_file() or state_path.read_bytes() != state_bytes:
                _atomic_bytes(state_path, state_bytes)
    rows = [item[2] for item in planned]
    return {
        "schema": "metnos.contract-language-state-migration/1",
        "dry_run": bool(dry_run),
        "contracts": len(rows),
        "changed": sum(bool(row["changed"]) for row in rows),
        "added_entries": sum(len(row["added_entries"]) for row in rows),
        "dropped_entries": sum(len(row["dropped_entries"]) for row in rows),
        "normalized_language_tags": sum(
            len(row["normalized_language_tags"]) for row in rows
        ),
        "cleared_provenance": sum(
            len(row["cleared_provenance"]) for row in rows
        ),
        "evidence": rows,
    }


def migrate_contract_language_states(*, dry_run: bool = False) -> dict:
    """Migrate one stable authoring snapshot outside any concurrent cutover."""
    from contract_store import catalog_admission_lock

    with catalog_admission_lock():
        return _migrate_contract_language_states_locked(dry_run=dry_run)


def _prepare_contract_store_shadow_locked() -> dict:
    """Build and verify the resumable initial immutable catalog."""
    from contract_store import (
        SHADOW_RELATIVE,
        current_manifest,
        publish_signed_source,
        verify_manifest_source,
    )
    from manifest_inventory import inventory_authoring_manifests
    from sign import list_trusted_publics

    inventory = inventory_authoring_manifests()
    if inventory.problems:
        detail = "; ".join(
            f"{problem.code}:{problem.path}" for problem in inventory.problems[:12]
        )
        raise RuntimeError(f"manifest inventory is not clean: {detail}")
    refs = inventory.installed()
    if not refs:
        raise RuntimeError("admitted manifest inventory is empty")
    trusted = tuple(list_trusted_publics())
    if not trusted:
        raise RuntimeError("no trusted contract signing keys are configured")

    verified = []
    nonce_hash = hashlib.sha256()
    for ref in refs:
        snapshot = verify_manifest_source(ref, trusted_publics=trusted)
        verified.append((ref, snapshot))
        for payload in (
            str(ref.contract_id).encode("utf-8"),
            snapshot.manifest_bytes,
            snapshot.signature_bytes,
            snapshot.language_state_bytes,
        ):
            nonce_hash.update(len(payload).to_bytes(8, "big"))
            nonce_hash.update(payload)
    nonce = nonce_hash.hexdigest()
    shadow_root = _C.PATH_USER_STATE / SHADOW_RELATIVE / nonce / "v1"

    expected: dict[str, str] = {}
    repeated = 0
    for ref, source in verified:
        result = publish_signed_source(
            ref,
            expected_generation_id=None,
            trusted_publics=trusted,
            store_root=shadow_root,
        )
        live = current_manifest(
            ref, trusted_publics=trusted, store_root=shadow_root,
        )
        if (
            live.manifest_bytes != source.manifest_bytes
            or live.signature_bytes != source.signature_bytes
            or live.language_state_bytes != source.language_state_bytes
        ):
            raise RuntimeError(
                f"shadow/source mismatch for {ref.contract_id}",
            )
        expected[str(ref.contract_id)] = result.current_generation_id
        repeated += int(result.repeated)
    return {
        "schema": "metnos.contract-store-cutover/1",
        "shadow_root": str(shadow_root),
        "contracts": len(expected),
        "repeated": repeated,
        "catalog": dict(sorted(expected.items())),
    }


def prepare_contract_store_shadow() -> dict:
    """Build one shadow from a globally stable authoring snapshot."""
    from contract_store import catalog_admission_lock

    with catalog_admission_lock():
        return _prepare_contract_store_shadow_locked()


def _activate_prepared_contract_store_locked(
    report: dict,
    *,
    quiescence_guard,
) -> None:
    """Revalidate and activate while production catalog exclusion is held."""
    from contract_store import (
        ContractRetirement,
        ContractStoreError,
        ProductionStoreMode,
        activate_store,
        contract_revision_id,
        current_contract,
        current_manifest,
        generation_id,
        production_store_mode,
        verify_manifest_source,
    )
    from i18n_pipeline import (
        preflight_published_contract_registry,
        reconcile_published_contract_registry,
    )
    from i18n_registry import LocalizationRegistry, RegistryError
    from manifest_inventory import (
        inventory_authoring_manifests,
        inventory_store_manifests,
    )
    from sign import list_trusted_publics

    if not isinstance(report, dict) or report.get("schema") != (
        "metnos.contract-store-cutover/1"
    ):
        raise ValueError("invalid contract-store preparation report")
    raw_catalog = report.get("catalog")
    raw_shadow_root = report.get("shadow_root")
    if (
        not isinstance(raw_catalog, dict)
        or not raw_catalog
        or any(
            not isinstance(contract_id, str)
            or not isinstance(identifier, str)
            for contract_id, identifier in raw_catalog.items()
        )
        or type(report.get("contracts")) is not int
        or report.get("contracts") != len(raw_catalog)
        or not isinstance(raw_shadow_root, str)
        or not raw_shadow_root
    ):
        raise ValueError("invalid contract-store preparation report")

    trusted = tuple(list_trusted_publics())
    if not trusted:
        raise ContractStoreError("trusted_keys_missing")
    shadow_root = Path(raw_shadow_root)
    mode = production_store_mode()

    # Once the global move has happened, the immutable production store is
    # the sole recovery authority.  The shadow no longer exists and authoring
    # may legitimately have advanced.  Authenticate the stable production
    # inventory, repair a missing marker through the same activation API, and
    # reconcile every current revision from a fresh read.  Tombstones are
    # included so a late retry cannot resurrect retired registry resources.
    if mode in {
        ProductionStoreMode.ACTIVE,
        ProductionStoreMode.STORE_ONLY,
    }:
        inventory = inventory_store_manifests()
        if inventory.problems or not inventory.manifests:
            detail = "; ".join(
                f"{problem.code}:{problem.path}"
                for problem in inventory.problems[:12]
            ) or "production catalog is empty"
            raise ContractStoreError(
                "activation_store_inventory_invalid", detail,
            )
        refs = {
            str(ref.contract_id): ref for ref in inventory.manifests
        }
        if set(raw_catalog) != set(refs):
            raise ContractStoreError(
                "activation_catalog_mismatch",
                "preparation identities differ from production bindings",
            )
        revisions = tuple(
            current_contract(refs[key], trusted_publics=trusted)
            for key in sorted(refs)
        )
        active_snapshots = tuple(
            revision for revision in revisions
            if not isinstance(revision, ContractRetirement)
        )
        if active_snapshots:
            try:
                preflight_published_contract_registry(active_snapshots)
            except RegistryError as exc:
                raise ContractStoreError(
                    "activation_registry_collision", str(exc),
                ) from exc
        expected = {
            revision.contract_id: contract_revision_id(revision)
            for revision in revisions
        }
        activate_store(
            expected,
            shadow_root=shadow_root,
            trusted_publics=trusted,
            quiescence_guard=quiescence_guard,
        )
        registry = LocalizationRegistry()
        for key in sorted(refs):
            revision = current_contract(refs[key], trusted_publics=trusted)
            reconcile_published_contract_registry(revision, registry=registry)
        return

    inventory = inventory_authoring_manifests()
    if inventory.problems:
        raise ContractStoreError(
            "activation_authoring_inventory_invalid",
            "manifest inventory changed or is not clean",
        )
    refs = {
        str(ref.contract_id): ref
        for ref in inventory.installed()
    }
    if set(raw_catalog) != set(refs):
        raise ContractStoreError(
            "activation_authoring_stale",
            "preparation catalog differs from admitted inventory",
        )
    expected = {
        refs[key].contract_id: raw_catalog[key]
        for key in raw_catalog
    }
    snapshots = []
    for key in sorted(refs):
        ref = refs[key]
        source = verify_manifest_source(ref, trusted_publics=trusted)
        source_generation = generation_id({
            "manifest.toml": source.manifest_bytes,
            "manifest.toml.sig": source.signature_bytes,
            "manifest.lang_state.json": source.language_state_bytes,
        })
        if source_generation != raw_catalog[key]:
            raise ContractStoreError(
                "activation_authoring_stale", str(ref.contract_id),
            )
        snapshot = current_manifest(
            ref,
            trusted_publics=trusted,
            store_root=shadow_root,
        )
        if snapshot.generation_id != raw_catalog[key]:
            raise ContractStoreError(
                "activation_shadow_stale", str(ref.contract_id),
            )
        snapshots.append(snapshot)

    try:
        preflight_published_contract_registry(tuple(snapshots))
    except RegistryError as exc:
        raise ContractStoreError(
            "activation_registry_collision", str(exc),
        ) from exc
    activate_store(
        expected,
        shadow_root=shadow_root,
        trusted_publics=trusted,
        quiescence_guard=quiescence_guard,
    )
    registry = LocalizationRegistry()
    for key in sorted(refs):
        snapshot = current_manifest(refs[key], trusted_publics=trusted)
        reconcile_published_contract_registry(snapshot, registry=registry)


def activate_prepared_contract_store(
    report: dict,
    *,
    quiescence_guard,
) -> None:
    """Own the complete preflight-to-registry cutover transaction.

    The managed installer already holds this lock through its lifecycle
    barrier.  Reentrant acquisition here also makes direct administrative
    callers safe: authoring cannot drift after preflight and a later publisher
    cannot be overwritten by stale registry reconciliation.
    """
    from contract_store import catalog_admission_lock

    with catalog_admission_lock():
        _activate_prepared_contract_store_locked(
            report,
            quiescence_guard=quiescence_guard,
        )


def _norm_value(val):
    """Normalizza description/affinity: ritorna dict {lang: text} sempre.
    Stringa flat → {DEFAULT_LANG: val}. Lista (per affinity) → JSON-serialize.
    """
    if val is None:
        return None
    if isinstance(val, str):
        return {DEFAULT_LANG: val}
    if isinstance(val, list):
        return {DEFAULT_LANG: json.dumps(val, ensure_ascii=False)}
    if isinstance(val, dict):
        # Già nested {it, en, ...} oppure dict con sub-list (affinity bilingue)
        out = {}
        for k, v in val.items():
            if isinstance(v, str):
                out[k] = v
            elif isinstance(v, list):
                out[k] = json.dumps(v, ensure_ascii=False)
        return out or None
    return None


def migrate_manifest(manifest_path: Path, dry_run: bool = False) -> dict:
    """Legge un manifest e migra description+affinity. Ritorna dict counts."""
    try:
        manifest = tomllib.loads(manifest_path.read_text())
    except Exception as e:
        return {"error": f"toml parse: {e}", "rows": 0}
    name = manifest.get("name")
    if not name:
        return {"error": "no name", "rows": 0}
    counts = {"name": name, "rows": 0, "fields": []}
    for field in ("description", "affinity"):
        raw = manifest.get(field)
        norm = _norm_value(raw)
        if not norm:
            continue
        key = f"{name}.{field}"
        if dry_run:
            for lang, text in norm.items():
                print(f"  [dry-run] would set [{key}, {lang}] = {text[:60]!r}")
        else:
            # Le traduzioni del manifest sono un'unita' editoriale completa:
            # non accodare falsamente una lingua mentre si scrive l'altra.
            i18n.set_catalog_translations(key, norm)
        counts["rows"] += len(norm)
        counts["fields"].append(field)
    return counts


def main():
    p = argparse.ArgumentParser(prog="metnos-i18n-migrate-manifests")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--canonical-contract-state", action="store_true",
        help="migrate admitted manifest companions to canonical v1",
    )
    p.add_argument(
        "--report", type=Path,
        help="write the canonical-state migration evidence as JSON",
    )
    p.add_argument(
        "--prepare-contract-store", action="store_true",
        help="build and verify the initial immutable shadow catalog",
    )
    p.add_argument(
        "--activate-contract-store", type=Path,
        help="activate the shadow catalog described by this report",
    )
    args = p.parse_args()

    if args.prepare_contract_store:
        if args.canonical_contract_state or args.activate_contract_store:
            p.error("contract-store modes are mutually exclusive")
        report = prepare_contract_store_shadow()
        encoded = json.dumps(
            report, ensure_ascii=False, sort_keys=True, indent=2,
        ) + "\n"
        if args.report is not None:
            _atomic_bytes(args.report, encoded.encode("utf-8"))
        print(encoded, end="")
        return
    if args.activate_contract_store is not None:
        if args.canonical_contract_state or args.report is not None:
            p.error("contract-store modes are mutually exclusive")
        report = json.loads(
            args.activate_contract_store.read_text(encoding="utf-8"),
        )
        from contract_cutover_guard import (
            contract_cutover_guard,
            verify_store_only_catalog,
        )

        with contract_cutover_guard() as (proof, evidence):
            activate_prepared_contract_store(
                report, quiescence_guard=proof,
            )
            verification = verify_store_only_catalog()
        print(json.dumps({
            "status": "contract store activated",
            "quiescence": evidence,
            "verification": verification,
        }, ensure_ascii=False, sort_keys=True))
        return

    if args.canonical_contract_state:
        report = migrate_contract_language_states(dry_run=args.dry_run)
        encoded = json.dumps(
            report, ensure_ascii=False, sort_keys=True, indent=2,
        ) + "\n"
        if args.report is not None:
            _atomic_bytes(args.report, encoded.encode("utf-8"))
        print(encoded, end="")
        return
    if args.report is not None:
        p.error("--report requires --canonical-contract-state")

    total = 0
    for d in EXEC_DIRS:
        if not d.is_dir():
            continue
        for sub in sorted(d.iterdir()):
            if not sub.is_dir():
                continue
            mf = sub / "manifest.toml"
            if not mf.is_file():
                continue
            res = migrate_manifest(mf, dry_run=args.dry_run)
            if "error" in res:
                print(f"  SKIP {sub.name}: {res['error']}")
            else:
                print(f"  {res['name']}: +{res['rows']} rows ({', '.join(res['fields'])})")
                total += res["rows"]
    print(f"\nTotal: {total} rows {'[dry-run]' if args.dry_run else 'migrated'}")


if __name__ == "__main__":
    main()
