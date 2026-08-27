# SPDX-License-Identifier: AGPL-3.0-only
"""Phase 3 — Metnos source and initial stores.

By the time this runs, ``$METNOS_INSTALL_ROOT`` points at a checked-out
Metnos source tree. Phase 3:

- verifies the source tree has what we need (sentinel files)
- creates the empty sqlite databases the runtime expects
- copies the bundled, complete ``i18n.sqlite`` seed
- publishes the shipped executor contracts with a key trusted by this
  installation;
- compiles and verifies the Tutor catalog from the public documentation and
  current runtime manifests before the HTTP service is allowed to start.

No personal data is involved. The phase is resumable and idempotent across
the supported publication states; the legacy-to-store boundary itself is
deliberately one-way.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .. import ui


_EXPECTED_SOURCE_DIRS = (
    "install",
    "runtime",
    "executors",
    "docs",
)

_TUTOR_COMPILE_TIMEOUT_S = 1800

_CUTOVER_REPORT = "contract_store_cutover.v1.json"

_SQLITE_FILES = (
    # filename, schema-init SQL (minimal — runtime migrations bring up to date)
    ("scratchpad.db",     "CREATE TABLE IF NOT EXISTS scratchpad (turn_id TEXT, key TEXT, value BLOB, PRIMARY KEY (turn_id, key));"),
    ("scheduler_v2.sqlite", "CREATE TABLE IF NOT EXISTS schedule_entries (id INTEGER PRIMARY KEY, name TEXT, trigger TEXT, callback TEXT, args TEXT, next_fire_at REAL, enabled INTEGER DEFAULT 1);"),
    ("persons.sqlite",    "CREATE TABLE IF NOT EXISTS persons (slug TEXT PRIMARY KEY, display_name TEXT, embedding_face BLOB);"),
    ("users.sqlite",      "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, email TEXT, created_at REAL, user_channels TEXT);"),
    ("host_health.json",  None),   # not sqlite, just empty placeholder
)


def _ensure_runtime_import_path() -> Path:
    """Expose the checked-out runtime to its intentionally flat imports.

    ``python -m install`` starts with the repository root on ``sys.path``;
    runtime modules such as ``contract_store`` live one level below it and
    import their peers by their canonical flat names.  Resolve the selected
    installation root once, before any phase-3 runtime import.
    """
    install_root = Path(os.environ.get(
        "METNOS_INSTALL_ROOT", Path(__file__).resolve().parents[2],
    )).resolve()
    runtime_dir = install_root / "runtime"
    runtime_text = str(runtime_dir)
    if runtime_text not in sys.path:
        sys.path.insert(0, runtime_text)
    return runtime_dir


def _data_dir() -> Path:
    return Path(os.environ.get("METNOS_USER_DATA", Path.home() / ".local" / "share" / "metnos"))


def _init_sqlite(path: Path, schema: str | None) -> bool:
    if path.exists():
        ui.info(f"{path.name}: exists, leaving in place")
        return False
    if schema is None:
        path.write_text("{}\n")
        path.chmod(0o600)
        ui.ok(f"created placeholder: {path}")
        return True
    with sqlite3.connect(str(path)) as conn:
        conn.executescript(schema)
        conn.commit()
    path.chmod(0o600)
    ui.ok(f"initialised sqlite: {path}")
    return True


def _init_i18n(data: Path) -> bool:
    """Seed ``i18n.sqlite`` from the bundled catalog.

    The runtime (``runtime/i18n.py``) reads the ``i18n`` table (key, lang,
    text, needs_translation, source_lang, …) and resolves MSG_*/ERR_*/WARN_*
    by key+lang. A fresh install MUST seed the full catalog or user-facing
    strings render as ``<missing:MSG_*>``. We copy the bundled seed
    (``install/data/i18n_seed.sqlite``). If it is absent, create an empty
    ``i18n`` table with the correct schema and WARN (the background i18n
    translator can fill it later, but coverage is incomplete until then).
    """
    import shutil
    p = data / "i18n.sqlite"
    if p.exists():
        ui.info(
            "i18n.sqlite: exists; preserving it (the runtime adds only "
            "missing bundled key/language rows)"
        )
        return False
    repo = os.environ.get("METNOS_INSTALL_ROOT", "")
    seed = Path(repo) / "install" / "data" / "i18n_seed.sqlite" if repo else None
    if seed and seed.exists():
        shutil.copyfile(seed, p)
        p.chmod(0o600)
        try:
            n = sqlite3.connect(str(p)).execute(
                "SELECT count(DISTINCT key) FROM i18n").fetchone()[0]
        except sqlite3.Error:
            n = "?"
        ui.ok(f"i18n.sqlite seeded from bundled catalog ({n} keys, en+it)")
        return True
    # Fallback: correct schema, empty — never the wrong `messages` table.
    with sqlite3.connect(str(p)) as conn:
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS i18n ("
            "  key TEXT NOT NULL, lang TEXT NOT NULL, text TEXT,"
            "  needs_translation INTEGER NOT NULL DEFAULT 0, source_lang TEXT,"
            "  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),"
            "  PRIMARY KEY (key, lang));")
        conn.commit()
    p.chmod(0o600)
    ui.warn("i18n seed catalog not bundled — created an empty i18n table. "
            "User-facing strings may show <missing:KEY> until the translator "
            "runs. Release pipeline should ship install/data/i18n_seed.sqlite.")
    return True


def _verify_source() -> dict[str, Any]:
    """Check $METNOS_INSTALL_ROOT has the expected layout."""
    repo = os.environ.get("METNOS_INSTALL_ROOT")
    if not repo:
        ui.warn("METNOS_INSTALL_ROOT not set — did bootstrap.sh complete?")
        return {"source_ok": False}
    root = Path(repo)
    missing = [d for d in _EXPECTED_SOURCE_DIRS if not (root / d).exists()]
    if missing:
        ui.warn(f"source tree missing: {', '.join(missing)}")
    else:
        ui.ok(f"source tree complete at {root}")
    return {"source_ok": not missing, "repo_dir": str(root), "missing_dirs": missing}


class ContractCatalogInstallError(RuntimeError):
    """Stable, fail-closed phase-3 error."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


_CUTOVER_INSTALL_ERROR_CODES = {
    "activation_authoring_inventory_invalid": "contract_authoring_inventory_invalid",
    "activation_authoring_stale": "contract_cutover_report_stale",
    "activation_registry_collision": "contract_cutover_registry_collision",
    "activation_shadow_stale": "contract_cutover_report_stale",
    "activation_store_inventory_invalid": "contract_store_inventory_invalid",
    "cutover_platform_unsupported": "contract_cutover_platform_unsupported",
    "lifecycle_catalog_invalid": "contract_cutover_lifecycle_catalog_invalid",
    "quiescence_unknown": "contract_cutover_quiescence_unknown",
    "cutover_blocked": "contract_cutover_blocked",
    "cutover_lock_unavailable": "contract_cutover_lock_unavailable",
    "store_inventory_invalid": "contract_store_inventory_invalid",
    "trusted_keys_missing": "contract_trusted_keys_missing",
    "store_name_invalid": "contract_store_name_invalid",
    "store_catalog_rejected": "contract_catalog_rejected",
    "store_catalog_incomplete": "contract_catalog_incomplete",
}


def _as_install_cutover_error(exc: Exception) -> ContractCatalogInstallError:
    """Keep phase-3 diagnostics stable across the shared runtime boundary."""

    code = str(getattr(exc, "code", "contract_cutover_failed"))
    detail = str(getattr(exc, "detail", "") or "")
    return ContractCatalogInstallError(
        _CUTOVER_INSTALL_ERROR_CODES.get(code, code), detail,
    )


@contextmanager
def _phase3_cutover_boundary():
    """Adapt the canonical guard without reimplementing its safety logic."""

    from contract_cutover_guard import (
        ContractCutoverGuardError,
        contract_cutover_guard,
    )

    try:
        with contract_cutover_guard() as boundary:
            yield boundary
    except ContractCutoverGuardError as exc:
        raise _as_install_cutover_error(exc) from exc


def _verify_contract_store_for_installation() -> dict[str, Any]:
    """Delegate the cold store-only proof to the canonical runtime boundary."""

    from contract_cutover_guard import (
        ContractCutoverGuardError,
        verify_store_only_catalog,
    )

    mode = _production_contract_store_mode()
    if mode != "active":
        raise ContractCatalogInstallError("contract_store_not_active", mode)
    try:
        return verify_store_only_catalog()
    except ContractCutoverGuardError as exc:
        raise _as_install_cutover_error(exc) from exc


def _user_state_dir() -> Path:
    return Path(os.environ.get(
        "METNOS_USER_STATE",
        Path.home() / ".local" / "state" / "metnos",
    ))


def _cutover_report_path() -> Path:
    return _user_state_dir() / "install" / _CUTOVER_REPORT


def _validate_cutover_report(report: object) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise ContractCatalogInstallError(
            "contract_cutover_report_invalid", "report is not an object",
        )
    catalog = report.get("catalog")
    if (
        report.get("schema") != "metnos.contract-store-cutover/1"
        or not isinstance(report.get("shadow_root"), str)
        or not report["shadow_root"]
        or not isinstance(catalog, dict)
        or not catalog
        or any(
            not isinstance(contract_id, str)
            or not isinstance(generation_id, str)
            for contract_id, generation_id in catalog.items()
        )
        or type(report.get("contracts")) is not int
        or report.get("contracts") != len(catalog)
    ):
        raise ContractCatalogInstallError(
            "contract_cutover_report_invalid", "unexpected report schema",
        )
    return report


def _write_cutover_report(report: dict[str, Any]) -> Path:
    """Persist recovery evidence before crossing the global boundary."""

    validated = _validate_cutover_report(report)
    destination = _cutover_report_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise ContractCatalogInstallError(
            "contract_cutover_report_invalid", str(destination),
        )
    payload = (
        json.dumps(validated, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        if os.name != "nt":
            directory_fd = os.open(
                destination.parent, os.O_RDONLY | os.O_DIRECTORY,
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return destination


def _read_cutover_report() -> dict[str, Any]:
    path = _cutover_report_path()
    try:
        status = path.lstat()
    except FileNotFoundError as exc:
        raise ContractCatalogInstallError(
            "contract_cutover_recovery_blocked",
            f"missing preparation report: {path}",
        ) from exc
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
        raise ContractCatalogInstallError(
            "contract_cutover_report_invalid", str(path),
        )
    if status.st_size > 4 * 1024 * 1024:
        raise ContractCatalogInstallError(
            "contract_cutover_report_invalid", "report is too large",
        )
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractCatalogInstallError(
            "contract_cutover_report_invalid", str(exc),
        ) from exc
    return _validate_cutover_report(report)


def _production_contract_store_mode() -> str:
    from contract_store import production_store_mode

    return production_store_mode().value


def _ensure_author_keypair(*, allow_create: bool) -> dict[str, Any]:
    """Create one local keypair, or prove the existing pair is coherent."""

    from cryptography.hazmat.primitives import serialization
    from sign import (
        DEFAULT_AUTHOR_KEY,
        KEYS_DIR,
        generate_keypair,
        load_private,
        load_public,
        restore_public_key,
    )

    private_path = KEYS_DIR / f"{DEFAULT_AUTHOR_KEY}_priv.bin"
    public_path = KEYS_DIR / f"{DEFAULT_AUTHOR_KEY}_pub.bin"
    private_exists = private_path.exists() or private_path.is_symlink()
    public_exists = public_path.exists() or public_path.is_symlink()
    if public_exists and not private_exists:
        raise ContractCatalogInstallError(
            "contract_signing_key_incomplete",
            "the author keypair has only its public component",
        )
    created = not private_exists and not public_exists
    if private_exists and not public_exists:
        if not allow_create:
            raise ContractCatalogInstallError(
                "contract_signing_key_incomplete",
                "the author keypair has only its private component",
            )
        try:
            private_status = private_path.lstat()
        except OSError as exc:
            raise ContractCatalogInstallError(
                "contract_signing_key_invalid", f"{private_path}: {exc}",
            ) from exc
        if (
            stat.S_ISLNK(private_status.st_mode)
            or not stat.S_ISREG(private_status.st_mode)
        ):
            raise ContractCatalogInstallError(
                "contract_signing_key_invalid", str(private_path),
            )
        try:
            restore_public_key(DEFAULT_AUTHOR_KEY)
        except (OSError, ValueError) as exc:
            raise ContractCatalogInstallError(
                "contract_signing_key_invalid", str(exc),
            ) from exc
    elif created:
        if not allow_create:
            raise ContractCatalogInstallError(
                "contract_signing_key_missing",
                "an activated or interrupted store requires its existing keypair",
            )
        try:
            generate_keypair(DEFAULT_AUTHOR_KEY)
        except (OSError, ValueError) as exc:
            raise ContractCatalogInstallError(
                "contract_signing_key_invalid", str(exc),
            ) from exc
    for path in (private_path, public_path):
        try:
            status = path.lstat()
        except OSError as exc:
            raise ContractCatalogInstallError(
                "contract_signing_key_invalid", f"{path}: {exc}",
            ) from exc
        if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
            raise ContractCatalogInstallError(
                "contract_signing_key_invalid", str(path),
            )
    try:
        derived = load_private(DEFAULT_AUTHOR_KEY).public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        configured = load_public(DEFAULT_AUTHOR_KEY).public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    except (OSError, ValueError) as exc:
        raise ContractCatalogInstallError(
            "contract_signing_key_invalid", str(exc),
        ) from exc
    if derived != configured:
        raise ContractCatalogInstallError(
            "contract_signing_key_mismatch",
            "the author private and public keys do not form one pair",
        )
    os.chmod(private_path, 0o600)
    os.chmod(public_path, 0o644)
    return {"created": created, "name": DEFAULT_AUTHOR_KEY}


def _clean_authoring_inventory():
    from manifest_inventory import inventory_authoring_manifests

    inventory = inventory_authoring_manifests()
    if inventory.problems:
        detail = "; ".join(
            f"{problem.code}:{problem.path}"
            for problem in inventory.problems[:12]
        )
        raise ContractCatalogInstallError(
            "contract_authoring_inventory_invalid", detail,
        )
    # Enablement is a visibility policy, not an installation boundary.  Keep
    # disabled skill contracts in the signed immutable catalog so enabling a
    # skill never falls back to mutable authoring bytes.  The structural API
    # is shared by language-state migration and shadow preparation.
    refs = inventory.installed()
    if not refs:
        raise ContractCatalogInstallError(
            "contract_authoring_inventory_empty",
        )
    return refs


def _migrate_contract_language_states() -> dict[str, Any]:
    from admin.i18n_migrate_manifests import (
        migrate_contract_language_states,
    )

    return migrate_contract_language_states(dry_run=False)


def _sign_and_verify_legacy_contracts() -> dict[str, Any]:
    """Sign only while authoring is still the live legacy authority."""

    from contract_store import verify_manifest_source
    from sign import list_trusted_publics, sign_executor

    refs = _clean_authoring_inventory()
    for ref in refs:
        sign_executor(ref.manifest_dir)

    # Signing updates the manifest digest, so verification must use a fresh
    # inventory observation rather than the stale pre-sign hashes.
    verified_refs = _clean_authoring_inventory()
    trusted = tuple(list_trusted_publics())
    if not trusted:
        raise ContractCatalogInstallError("contract_trusted_keys_missing")
    for ref in verified_refs:
        verify_manifest_source(ref, trusted_publics=trusted)
    return {"signed": len(refs), "verified": len(verified_refs)}


def _publish_active_authoring_contracts() -> dict[str, Any]:
    """Submit every active-layout source through the operational Birth gate."""

    from contract_store import ContractStoreError
    from executor_birth_intent import (
        BirthIntent, require_birth_intent_adapter, submit_installer_birth,
    )

    refs = _clean_authoring_inventory()
    require_birth_intent_adapter()
    repeated = 0
    retired_skipped = 0
    for ref in refs:
        try:
            birth = submit_installer_birth(BirthIntent(
                candidate_source_root=ref.manifest_dir,
                contract_id=ref.contract_id,
                reason="converge installed executor contract catalog",
            ))
            if birth.error_code == "contract_retired":
                retired_skipped += 1
                continue
            if birth.error_code or birth.publication is None:
                raise ContractCatalogInstallError(
                    "contract_birth_rejected", birth.error_code or str(ref.contract_id),
                )
            publication = birth.publication
        except ContractStoreError as exc:
            # Retirement is a signed live revision, not an installation
            # failure and never an invitation to reactivate implicitly.  The
            # publisher makes this decision under its contract lock, so a
            # concurrent retire/reactivate remains a normal CAS conflict.
            if exc.code != "contract_retired":
                raise
            retired_skipped += 1
            continue
        repeated += int(publication.repeated)
    return {
        "examined": len(refs),
        "published": len(refs) - retired_skipped,
        "repeated": repeated,
        "changed": len(refs) - repeated - retired_skipped,
        "retired_skipped": retired_skipped,
    }


def _activate_prepared_report_locked(
    report: dict[str, Any],
    *,
    proof,
) -> dict[str, Any]:
    from admin.i18n_migrate_manifests import (
        activate_prepared_contract_store,
    )
    from contract_store import ContractStoreError

    try:
        activate_prepared_contract_store(report, quiescence_guard=proof)
    except ContractStoreError as exc:
        raise _as_install_cutover_error(exc) from exc
    return _verify_contract_store_for_installation()


def _activate_prepared_report(report: dict[str, Any]) -> dict[str, Any]:
    """Resume marker-only recovery while retaining the lifecycle barrier."""

    with _phase3_cutover_boundary() as (proof, evidence):
        key = _ensure_author_keypair(allow_create=False)
        verification = _activate_prepared_report_locked(report, proof=proof)
    return {
        "key": key,
        "quiescence": evidence,
        "verification": verification,
    }


def _store_only_catalog() -> tuple[dict, tuple]:
    """Reconstruct the exact authenticated catalog from a root-only store."""

    from contract_store import ContractRetirement, STORE_RELATIVE, current_contract
    from config import PATH_USER_STATE
    from manifest_inventory import inventory_store_manifests
    from sign import list_trusted_publics

    inventory = inventory_store_manifests()
    if inventory.problems:
        detail = "; ".join(
            f"{problem.code}:{problem.path}"
            for problem in inventory.problems[:12]
        )
        raise ContractCatalogInstallError(
            "contract_store_recovery_invalid", detail,
        )
    if not inventory.manifests:
        raise ContractCatalogInstallError(
            "contract_store_recovery_invalid", "store catalog is empty",
        )
    trusted = tuple(list_trusted_publics())
    if not trusted:
        raise ContractCatalogInstallError("contract_trusted_keys_missing")
    expected = {}
    store_root = PATH_USER_STATE / STORE_RELATIVE
    for ref in inventory.manifests:
        current = current_contract(
            ref,
            trusted_publics=trusted,
            store_root=store_root,
        )
        identifier = (
            current.retirement_id
            if isinstance(current, ContractRetirement)
            else current.generation_id
        )
        if not isinstance(identifier, str) or not identifier:
            raise ContractCatalogInstallError(
                "contract_store_recovery_invalid", str(ref.contract_id),
            )
        expected[ref.contract_id] = identifier
    return expected, trusted


def _recover_store_only() -> dict[str, Any]:
    """Restore only the marker; the authenticated root is the evidence."""

    from contract_store import SHADOW_RELATIVE, activate_store
    from config import PATH_USER_STATE

    with _phase3_cutover_boundary() as (proof, evidence):
        key = _ensure_author_keypair(allow_create=False)
        expected, trusted = _store_only_catalog()
        # ``activate_store`` ignores the shadow in STORE_ONLY mode, but still
        # requires a canonical shadow-shaped locator at its API boundary.
        unused_shadow = (
            PATH_USER_STATE / SHADOW_RELATIVE / "store-only-recovery" / "v1"
        )
        activate_store(
            expected,
            shadow_root=unused_shadow,
            trusted_publics=trusted,
            quiescence_guard=proof,
        )
        verification = _verify_contract_store_for_installation()
    return {
        "key": key,
        "quiescence": evidence,
        "verification": verification,
    }


def _install_executor_contracts() -> dict[str, Any]:
    """Converge phase 3 across fresh, active and interrupted layouts."""

    mode_before = _production_contract_store_mode()
    if mode_before == "active":
        migration = _migrate_contract_language_states()
        key = _ensure_author_keypair(allow_create=False)
        publication = _publish_active_authoring_contracts()
        verification = _verify_contract_store_for_installation()
        ui.ok(
            "executor contract store verified "
            f"({verification['loaded']} live contracts)"
        )
        return {
            "mode_before": mode_before,
            "mode_after": "active",
            "migration": migration,
            "key": key,
            "publication": publication,
            "verification": verification,
        }

    if mode_before == "store_only":
        activation = _recover_store_only()
        ui.ok("root-only executor contract store recovered and verified")
        return {
            "mode_before": mode_before,
            "mode_after": "active",
            "resumed": True,
            "recovery_source": "authenticated_store_root",
            **activation,
        }

    if mode_before == "recovery_required":
        report = _read_cutover_report()
        activation = _activate_prepared_report(report)
        ui.ok("interrupted executor contract cutover resumed and verified")
        return {
            "mode_before": mode_before,
            "mode_after": "active",
            "resumed": True,
            "recovery_source": "saved_preparation_report",
            "report": str(_cutover_report_path()),
            **activation,
        }

    if mode_before != "legacy":
        raise ContractCatalogInstallError(
            "contract_store_mode_unknown", mode_before,
        )

    from admin.i18n_migrate_manifests import prepare_contract_store_shadow

    # The authoring tree is still live in LEGACY mode.  Keep the same stopped-
    # stack barrier across migration, signing, shadow preparation, activation
    # and the first store-only load so no reader can observe a partial corpus.
    with _phase3_cutover_boundary() as (proof, evidence):
        migration = _migrate_contract_language_states()
        key = _ensure_author_keypair(allow_create=True)
        signing = _sign_and_verify_legacy_contracts()
        report = prepare_contract_store_shadow()
        report_path = _write_cutover_report(report)
        verification = _activate_prepared_report_locked(report, proof=proof)
    ui.ok(
        "executor contracts published and verified "
        f"({verification['loaded']} live contracts)"
    )
    return {
        "mode_before": mode_before,
        "mode_after": "active",
        "migration": migration,
        "key": key,
        "signing": signing,
        "report": str(report_path),
        "quiescence": evidence,
        "verification": verification,
    }


def _persist_localization_request() -> dict[str, Any]:
    """Create the signed instance-language authority after key generation."""

    from .. import disclaimer
    selection = disclaimer.read_language_selection()
    if selection is None:
        return {"persisted": False, "error": "accepted language unavailable"}
    try:
        from runtime import config as runtime_config
        request, changed = runtime_config.write_localization_request(
            instance_lang=selection.instance_lang,
            requested_lang=selection.requested_lang,
            state=selection.localization_state,
        )
    except (OSError, ValueError) as exc:
        return {
            "persisted": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "persisted": True,
        "changed": changed,
        "instance_lang": request.instance_lang,
        "requested_lang": request.requested_lang,
        "state": request.state,
        "corpus_version": request.corpus_version,
    }


def _compile_tutor_catalog() -> dict[str, Any]:
    """Build the signed Tutor catalog before service readiness is evaluated.

    A fresh F2 catalog embeds several thousand public-document units and can
    legitimately take minutes on CPU. Doing that work in phase 3 gives the
    installer one durable, resumable build boundary; leaving it to HTTP startup
    would make the 120-second stack-readiness circuit kill the compiler and
    repeat the same cold build forever.
    """
    repo = os.environ.get("METNOS_INSTALL_ROOT")
    venv = os.environ.get("METNOS_VENV")
    if not repo or not venv:
        return {
            "compiled": False,
            "error": "METNOS_INSTALL_ROOT/METNOS_VENV not configured",
        }
    py = Path(venv) / "bin" / "python"
    if not py.is_file():
        return {"compiled": False, "error": f"missing interpreter: {py}"}

    env = dict(os.environ)
    pythonpath = f"{repo}:{repo}/runtime"
    env["PYTHONPATH"] = (
        pythonpath + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    )
    code = (
        "from tutor.catalog import compile_catalog, verify_catalog; "
        "digest=compile_catalog(); "
        "assert verify_catalog(), 'Tutor catalog verification failed'; "
        "print(digest)"
    )
    started = time.monotonic()
    try:
        result = subprocess.run(
            [str(py), "-c", code],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=_TUTOR_COMPILE_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "compiled": False,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": round(time.monotonic() - started, 1),
        }
    elapsed = round(time.monotonic() - started, 1)
    output = (result.stdout or "").strip().splitlines()
    digest = output[-1] if output else ""
    if result.returncode != 0 or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        detail = (result.stderr or result.stdout or "no diagnostic output").strip()
        return {
            "compiled": False,
            "error": detail[-600:],
            "returncode": result.returncode,
            "elapsed_seconds": elapsed,
        }
    ui.ok(f"Tutor catalog compiled and verified in {elapsed:.1f}s")
    return {
        "compiled": True,
        "digest": digest,
        "elapsed_seconds": elapsed,
    }


def run(args: Any) -> dict[str, Any]:
    notes: dict[str, Any] = {}
    ui.banner("Phase 3 — Metnos code & workspace", "Verify source, prepare empty databases")

    # 1. Source tree verification
    ui.step("Verifying source tree")
    notes.update(_verify_source())
    _ensure_runtime_import_path()

    # 2. SQLite skeletons
    ui.step("Initialising empty workspace databases")
    data = _data_dir()
    data.mkdir(parents=True, exist_ok=True)
    created = 0
    for fname, schema in _SQLITE_FILES:
        if _init_sqlite(data / fname, schema):
            created += 1
    notes["sqlite_created"] = created

    # 3. i18n bootstrap
    ui.step("Bootstrapping i18n message store")
    _init_i18n(data)

    # 4. Canonical, resumable publication boundary.  A legacy fresh install
    # signs locally before the one-way cutover; an active installation uses
    # only the generation publisher.  An interrupted cutover resumes only
    # from its exact durable preparation report.
    ui.step("Publishing and verifying executor contracts")
    notes["contracts"] = _install_executor_contracts()

    # The language selection predates the signing key. Materialize its signed,
    # atomic authority now; repeating phase 3 is byte-idempotent.
    ui.step("Signing the instance localization request")
    notes["localization"] = _persist_localization_request()
    if not notes["localization"].get("persisted"):
        ui.warn(
            "instance localization request not persisted; runtime will use "
            "its safe instance-language bootstrap"
        )

    # 5. Tutor F2 catalog. Documentation and runtime manifests are compiler
    # inputs, so this step automatically rebuilds only when their content stamp
    # changes; unchanged vectors are reused by the catalog compiler.
    ui.step("Compiling the Tutor catalog from public documentation "
            "(the first build can take several minutes)")
    notes["tutor_catalog"] = _compile_tutor_catalog()
    if not notes["tutor_catalog"].get("compiled"):
        ui.fail("Tutor catalog compilation failed: "
                f"{notes['tutor_catalog'].get('error', 'unknown error')}")

    return notes
