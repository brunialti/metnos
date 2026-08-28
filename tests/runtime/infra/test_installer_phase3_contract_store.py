from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import config
import contract_cutover_guard
import contract_store
import executor_birth_bootstrap
import manifest_inventory
import sign
from admin import i18n_migrate_manifests
from install.phases import phase3_code


def test_shared_cutover_guard_proves_the_complete_lifecycle_catalog() -> None:
    import stack_reconcile

    observations: list[tuple[str, str]] = []

    class Systemctl:
        @staticmethod
        def show(unit: str, scope: str) -> dict:
            observations.append((scope, unit))
            return {
                "LoadState": "loaded",
                "ActiveState": "inactive",
                "MainPID": "0",
            }

    reconciler = SimpleNamespace(
        systemctl=Systemctl(),
        require_quiescent=lambda: {
            "source": "inactive_http_and_inactive_sidecar",
        },
    )

    evidence = contract_cutover_guard.prove_stack_stopped(reconciler)

    lifecycle_catalog = {
        stack_reconcile.TARGET_UNIT,
        *stack_reconcile.STACK_UNITS,
        *stack_reconcile.CONTROL_PLANE_UNITS,
    }
    expected_user = stack_reconcile.CONTRACT_CUTOVER_UNITS
    assert len(expected_user) == len(set(expected_user))
    assert set(expected_user).issubset(lifecycle_catalog)
    assert "metnos-llm.service" not in expected_user
    assert "metnos-searxng.service" not in expected_user
    assert "metnos-photon.service" not in expected_user
    assert "metnos-playwright.service" not in expected_user
    assert observations == [
        *(("user", unit) for unit in expected_user),
        ("system", "metnos-http.service"),
    ]
    assert set(evidence["units"]) == {
        *(f"user:{unit}" for unit in expected_user),
        "system:metnos-http.service",
    }


def test_managed_server_cutover_rejects_non_linux_platform_early(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(contract_cutover_guard.sys, "platform", "win32")
    monkeypatch.setattr(contract_store.sys, "platform", "win32")

    with pytest.raises(contract_cutover_guard.ContractCutoverGuardError) as guard:
        with contract_cutover_guard.contract_cutover_guard():
            pytest.fail("unsupported platform crossed the lifecycle boundary")
    assert guard.value.code == "cutover_platform_unsupported"

    with pytest.raises(contract_store.ContractStoreError) as activation:
        contract_store.activate_store(
            {},
            shadow_root=tmp_path / "shadow" / "v1",
            trusted_publics=(),
            quiescence_guard=lambda: True,
        )
    assert activation.value.code == "cutover_platform_unsupported"


def test_phase3_exposes_runtime_to_clean_module_install_process() -> None:
    root = Path(__file__).resolve().parents[3]
    script = (
        "import os,sys; "
        f"sys.path.insert(0, {str(root)!r}); "
        f"os.environ['METNOS_INSTALL_ROOT'] = {str(root)!r}; "
        "from install.phases import phase3_code; "
        "runtime_dir = phase3_code._ensure_runtime_import_path(); "
        "import contract_store; "
        "assert runtime_dir == "
        f"__import__('pathlib').Path({str(root / 'runtime')!r}); "
        "assert __import__('pathlib').Path(contract_store.__file__).resolve().parent "
        "== runtime_dir"
    )

    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def _report() -> dict:
    return {
        "schema": "metnos.contract-store-cutover/1",
        "shadow_root": "/isolated/shadow/hash/v1",
        "contracts": 1,
        "repeated": 0,
        "catalog": {"core:demo/manifest.toml": "sha256:" + "a" * 64},
    }


def test_active_rerun_uses_only_layout_aware_publication(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(phase3_code.ui, "ok", lambda _message: None)
    monkeypatch.setattr(
        phase3_code, "_production_contract_store_mode", lambda: "active",
    )
    monkeypatch.setattr(
        phase3_code,
        "_migrate_contract_language_states",
        lambda: events.append("migrate") or {"changed": 0},
    )
    monkeypatch.setattr(
        phase3_code,
        "_ensure_author_keypair",
        lambda *, allow_create: (
            events.append(f"key:{allow_create}") or {"created": False}
        ),
    )
    monkeypatch.setattr(
        phase3_code, "_ensure_birth_authorities_prepared",
        lambda: events.append("birth-authorities") or {"outcome": "existing"},
    )
    monkeypatch.setattr(
        phase3_code,
        "_publish_active_authoring_contracts",
        lambda: events.append("publish") or {
            "examined": 1,
            "published": 1,
            "repeated": 1,
            "changed": 0,
            "retired_skipped": 0,
        },
    )
    monkeypatch.setattr(
        contract_cutover_guard,
        "verify_store_only_catalog",
        lambda: events.append("verify") or {
            "bindings": 1, "loaded": 1, "retired": 0,
        },
    )
    result = phase3_code._install_executor_contracts()

    assert events == [
        "migrate", "key:False", "birth-authorities", "publish", "verify",
    ]
    assert result["mode_before"] == "active"
    assert result["mode_after"] == "active"


def test_active_publication_preserves_a_tombstone(monkeypatch, tmp_path) -> None:
    import executor_birth_intent
    from manifest_inventory import ContractId, ManifestOrigin
    active = SimpleNamespace(
        contract_id=ContractId(ManifestOrigin.CORE, "active/manifest.toml"),
        manifest_dir=tmp_path / "active",
    )
    retired = SimpleNamespace(
        contract_id=ContractId(ManifestOrigin.CORE, "retired/manifest.toml"),
        manifest_dir=tmp_path / "retired",
    )
    monkeypatch.setattr(
        phase3_code, "_clean_authoring_inventory", lambda: (active, retired),
    )

    monkeypatch.setattr(executor_birth_intent, "require_birth_intent_adapter", lambda: None)
    def submit(intent):
        if intent.candidate_source_root == retired.manifest_dir:
            return SimpleNamespace(error_code="contract_retired", publication=None)
        return SimpleNamespace(error_code=None, publication=SimpleNamespace(repeated=False))
    monkeypatch.setattr(executor_birth_intent, "submit_installer_birth", submit)

    result = phase3_code._publish_active_authoring_contracts()

    assert result == {
        "examined": 2,
        "published": 1,
        "repeated": 0,
        "changed": 1,
        "retired_skipped": 1,
    }


def test_active_publication_does_not_hide_a_cas_conflict(
    monkeypatch, tmp_path,
) -> None:
    import executor_birth_intent
    from manifest_inventory import ContractId, ManifestOrigin
    ref = SimpleNamespace(
        contract_id=ContractId(ManifestOrigin.CORE, "active/manifest.toml"),
        manifest_dir=tmp_path / "active",
    )
    monkeypatch.setattr(
        phase3_code, "_clean_authoring_inventory", lambda: (ref,),
    )
    monkeypatch.setattr(executor_birth_intent, "require_birth_intent_adapter", lambda: None)
    monkeypatch.setattr(
        executor_birth_intent, "submit_installer_birth",
        lambda _intent: (_ for _ in ()).throw(
            contract_store.ContractStoreError("expected_generation_changed")),
    )

    with pytest.raises(
        contract_store.ContractStoreError,
        match="expected_generation_changed",
    ):
        phase3_code._publish_active_authoring_contracts()


def test_legacy_install_persists_report_before_guarded_activation(
    monkeypatch,
) -> None:
    events: list[str] = []
    report = _report()
    monkeypatch.setattr(phase3_code.ui, "ok", lambda _message: None)
    monkeypatch.setattr(
        phase3_code, "_production_contract_store_mode", lambda: "legacy",
    )
    monkeypatch.setattr(
        phase3_code,
        "_migrate_contract_language_states",
        lambda: events.append("migrate") or {"changed": 1},
    )
    monkeypatch.setattr(
        phase3_code,
        "_ensure_author_keypair",
        lambda *, allow_create: (
            events.append(f"key:{allow_create}") or {"created": True}
        ),
    )
    monkeypatch.setattr(
        phase3_code, "_ensure_birth_authorities_prepared",
        lambda: events.append("birth-authorities") or {"outcome": "prepared"},
    )
    def prepare_initial(*, prove_quiescent):
        events.append("proof")
        prove_quiescent()
        events.append("birth-prepare")
        return report

    monkeypatch.setattr(
        executor_birth_bootstrap,
        "prepare_initial_installer_catalog_v1",
        prepare_initial,
    )
    def persist(value):
        assert value is report
        events.append("persist")
        return phase3_code._cutover_report_path()

    @contextmanager
    def guard():
        events.append("guard-enter")
        yield lambda: True, {
            "source": "inactive_http_and_inactive_sidecar",
        }
        events.append("guard-exit")

    def activate(value, *, proof):
        assert value is report
        assert proof() is True
        events.append("activate")
        return {"bindings": 1, "loaded": 1, "retired": 0}

    monkeypatch.setattr(phase3_code, "_write_cutover_report", persist)
    monkeypatch.setattr(contract_cutover_guard, "contract_cutover_guard", guard)
    monkeypatch.setattr(
        phase3_code, "_activate_prepared_report_locked", activate,
    )

    result = phase3_code._install_executor_contracts()

    assert events == [
        "key:True", "birth-authorities", "guard-enter", "migrate", "proof",
        "birth-prepare", "persist", "activate", "guard-exit",
    ]
    assert result["mode_after"] == "active"
    assert result["birth_authorities"]["outcome"] == "prepared"


def test_marker_only_recovery_resumes_only_from_saved_report(monkeypatch) -> None:
    report = _report()
    events: list[str] = []
    monkeypatch.setattr(phase3_code.ui, "ok", lambda _message: None)
    monkeypatch.setattr(
        phase3_code,
        "_production_contract_store_mode",
        lambda: "recovery_required",
    )
    monkeypatch.setattr(
        phase3_code,
        "_read_cutover_report",
        lambda: events.append("read-report") or report,
    )
    monkeypatch.setattr(
        phase3_code,
        "_activate_prepared_report",
        lambda value: (
            events.append("resume")
            or {
                "quiescence": {"source": "inactive_http_and_inactive_sidecar"},
                "verification": {"bindings": 1, "loaded": 1, "retired": 0},
            }
        ),
    )
    monkeypatch.setattr(
        phase3_code,
        "_migrate_contract_language_states",
        lambda: pytest.fail("recovery must not rewrite prepared sources"),
    )
    monkeypatch.setattr(
        phase3_code,
        "_publish_active_authoring_contracts",
        lambda: pytest.fail("recovery must complete before publication"),
    )
    monkeypatch.setattr(
        phase3_code, "_ensure_birth_authorities_prepared",
        lambda: events.append("birth-authorities") or {"outcome": "existing"},
    )

    result = phase3_code._install_executor_contracts()

    assert events == ["birth-authorities", "read-report", "resume"]
    assert result["resumed"] is True
    assert result["mode_before"] == "recovery_required"
    assert result["recovery_source"] == "saved_preparation_report"


def test_root_only_recovery_authenticates_store_without_old_report(
    monkeypatch,
) -> None:
    monkeypatch.setattr(phase3_code.ui, "ok", lambda _message: None)
    monkeypatch.setattr(
        phase3_code,
        "_production_contract_store_mode",
        lambda: "store_only",
    )
    monkeypatch.setattr(
        phase3_code,
        "_read_cutover_report",
        lambda: pytest.fail("a root-only recovery must not trust an old report"),
    )
    monkeypatch.setattr(
        phase3_code,
        "_recover_store_only",
        lambda: {
            "quiescence": {"source": "inactive_http_and_inactive_sidecar"},
            "verification": {"bindings": 1, "loaded": 1, "retired": 0},
        },
    )
    monkeypatch.setattr(
        phase3_code, "_ensure_birth_authorities_prepared",
        lambda: {"outcome": "existing"},
    )

    result = phase3_code._install_executor_contracts()

    assert result["mode_after"] == "active"
    assert result["recovery_source"] == "authenticated_store_root"


def test_root_only_catalog_reads_the_explicit_inactive_production_root(
    tmp_path, monkeypatch,
) -> None:
    contract_id = manifest_inventory.ContractId(
        manifest_inventory.ManifestOrigin.CORE,
        "demo/manifest.toml",
    )
    ref = SimpleNamespace(contract_id=contract_id)
    inventory = SimpleNamespace(problems=(), manifests=(ref,))
    trusted = (("author", object()),)
    observed = {}
    monkeypatch.setattr(config, "PATH_USER_STATE", tmp_path / "state")
    monkeypatch.setattr(
        manifest_inventory,
        "inventory_store_manifests",
        lambda: inventory,
    )
    monkeypatch.setattr(sign, "list_trusted_publics", lambda: trusted)

    def current(item, *, trusted_publics, store_root):
        observed.update({
            "ref": item,
            "trusted": trusted_publics,
            "store_root": store_root,
        })
        return SimpleNamespace(generation_id="sha256:" + "b" * 64)

    monkeypatch.setattr(contract_store, "current_contract", current)

    expected, actual_trusted = phase3_code._store_only_catalog()

    assert expected == {contract_id: "sha256:" + "b" * 64}
    assert actual_trusted == trusted
    assert observed["store_root"] == (
        tmp_path / "state" / contract_store.STORE_RELATIVE
    )


def test_missing_recovery_report_has_stable_blocked_diagnostic(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("METNOS_USER_STATE", str(tmp_path / "state"))

    with pytest.raises(
        phase3_code.ContractCatalogInstallError,
        match="contract_cutover_recovery_blocked",
    ):
        phase3_code._read_cutover_report()


def test_cutover_report_is_atomic_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("METNOS_USER_STATE", str(tmp_path / "state"))
    report = _report()

    path = phase3_code._write_cutover_report(report)

    assert path.stat().st_mode & 0o777 == 0o600
    assert phase3_code._read_cutover_report() == report


def test_fresh_layout_creates_one_coherent_author_keypair(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setattr(sign, "KEYS_DIR", tmp_path / "keys")

    result = phase3_code._ensure_author_keypair(allow_create=True)

    assert result == {"created": True, "name": "author"}
    assert (tmp_path / "keys" / "author_priv.bin").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "keys" / "author_pub.bin").stat().st_mode & 0o777 == 0o644


def test_active_layout_never_silently_replaces_a_missing_keypair(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setattr(sign, "KEYS_DIR", tmp_path / "keys")

    with pytest.raises(
        phase3_code.ContractCatalogInstallError,
        match="contract_signing_key_missing",
    ):
        phase3_code._ensure_author_keypair(allow_create=False)

    assert not (tmp_path / "keys").exists()


def test_fresh_layout_recovers_valid_private_only_keypair(
    tmp_path, monkeypatch,
) -> None:
    keys = tmp_path / "keys"
    monkeypatch.setattr(sign, "KEYS_DIR", keys)
    real_atomic_replace = sign._atomic_replace_bytes
    interrupted = False

    def interrupt_public_once(
        path,
        payload,
        *,
        new_mode=0o600,
        preserve_existing_mode=True,
    ):
        nonlocal interrupted
        if Path(path).name == "author_pub.bin" and not interrupted:
            interrupted = True
            raise OSError("simulated public-key interruption")
        real_atomic_replace(
            path,
            payload,
            new_mode=new_mode,
            preserve_existing_mode=preserve_existing_mode,
        )

    monkeypatch.setattr(sign, "_atomic_replace_bytes", interrupt_public_once)
    with pytest.raises(
        phase3_code.ContractCatalogInstallError,
        match="contract_signing_key_invalid",
    ):
        phase3_code._ensure_author_keypair(allow_create=True)

    private_path = keys / "author_priv.bin"
    assert private_path.is_file()
    assert not (keys / "author_pub.bin").exists()

    monkeypatch.setattr(sign, "_atomic_replace_bytes", real_atomic_replace)
    private = sign.load_private("author")

    result = phase3_code._ensure_author_keypair(allow_create=True)

    public_path = keys / "author_pub.bin"
    assert result == {"created": False, "name": "author"}
    assert public_path.read_bytes() == private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    assert public_path.stat().st_mode & 0o777 == 0o644


def test_private_only_keypair_is_not_repaired_outside_fresh_creation(
    tmp_path, monkeypatch,
) -> None:
    keys = tmp_path / "keys"
    keys.mkdir()
    private = Ed25519PrivateKey.generate()
    (keys / "author_priv.bin").write_bytes(private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    monkeypatch.setattr(sign, "KEYS_DIR", keys)

    with pytest.raises(
        phase3_code.ContractCatalogInstallError,
        match="contract_signing_key_incomplete",
    ):
        phase3_code._ensure_author_keypair(allow_create=False)

    assert not (keys / "author_pub.bin").exists()


def test_public_only_keypair_remains_fail_closed(
    tmp_path, monkeypatch,
) -> None:
    keys = tmp_path / "keys"
    keys.mkdir()
    private = Ed25519PrivateKey.generate()
    (keys / "author_pub.bin").write_bytes(private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ))
    monkeypatch.setattr(sign, "KEYS_DIR", keys)

    with pytest.raises(
        phase3_code.ContractCatalogInstallError,
        match="contract_signing_key_incomplete",
    ):
        phase3_code._ensure_author_keypair(allow_create=True)

    assert not (keys / "author_priv.bin").exists()


def test_invalid_private_only_keypair_cannot_create_public_component(
    tmp_path, monkeypatch,
) -> None:
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "author_priv.bin").write_bytes(b"not-an-ed25519-private-key")
    monkeypatch.setattr(sign, "KEYS_DIR", keys)

    with pytest.raises(
        phase3_code.ContractCatalogInstallError,
        match="contract_signing_key_invalid",
    ):
        phase3_code._ensure_author_keypair(allow_create=True)

    assert not (keys / "author_pub.bin").exists()


def test_phase3_delegates_guard_and_cold_load_to_shared_boundary(
    monkeypatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        phase3_code, "_production_contract_store_mode", lambda: "active",
    )

    @contextmanager
    def guard():
        events.append("guard-enter")
        yield lambda: True, {"source": "shared-boundary"}
        events.append("guard-exit")

    monkeypatch.setattr(contract_cutover_guard, "contract_cutover_guard", guard)
    monkeypatch.setattr(
        contract_cutover_guard,
        "verify_store_only_catalog",
        lambda: events.append("cold-load") or {
            "bindings": 2, "loaded": 1, "retired": 0,
        },
    )

    with phase3_code._phase3_cutover_boundary() as (proof, evidence):
        assert proof() is True
        assert evidence == {"source": "shared-boundary"}
        verification = phase3_code._verify_contract_store_for_installation()

    assert verification == {"bindings": 2, "loaded": 1, "retired": 0}
    assert events == ["guard-enter", "cold-load", "guard-exit"]
    assert not hasattr(phase3_code, "_prove_stack_stopped")
    assert not hasattr(phase3_code, "_contract_cutover_guard")
    assert not hasattr(phase3_code, "_verify_store_catalog")


def test_installer_adapts_the_shared_authoring_staleness_check(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        i18n_migrate_manifests,
        "activate_prepared_contract_store",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            contract_store.ContractStoreError(
                "activation_authoring_stale",
                "core:reader/manifest.toml",
            )
        ),
    )
    monkeypatch.setattr(
        phase3_code,
        "_verify_contract_store_for_installation",
        lambda: pytest.fail("stale preparation must not be verified"),
    )
    monkeypatch.setattr(
        executor_birth_bootstrap, "verify_initial_installer_report_v1",
        lambda *_args, **_kwargs: {"contracts": 1, "receipts": 1},
    )

    with pytest.raises(phase3_code.ContractCatalogInstallError) as caught:
        phase3_code._activate_prepared_report_locked(
            _report(), proof=lambda: True,
        )

    assert caught.value.code == "contract_cutover_report_stale"
    assert not hasattr(
        phase3_code, "_validate_prepared_report_against_authoring",
    )


def test_root_only_recovery_keeps_shared_guard_through_first_cold_load(
    monkeypatch, tmp_path,
) -> None:
    events: list[str] = []
    trusted = (("author", object()),)
    expected = {"contract": "sha256:" + "c" * 64}

    @contextmanager
    def guard():
        events.append("guard-enter")
        yield lambda: events.append("proof") or True, {"source": "stopped"}
        events.append("guard-exit")

    monkeypatch.setattr(phase3_code, "_phase3_cutover_boundary", guard)
    monkeypatch.setattr(
        phase3_code,
        "_ensure_author_keypair",
        lambda *, allow_create: (
            events.append(f"key:{allow_create}") or {"created": False}
        ),
    )
    monkeypatch.setattr(
        phase3_code,
        "_store_only_catalog",
        lambda: events.append("catalog") or (expected, trusted),
    )
    monkeypatch.setattr(config, "PATH_USER_STATE", tmp_path / "state")

    def activate(actual, *, shadow_root, trusted_publics, quiescence_guard):
        assert actual is expected
        assert trusted_publics is trusted
        assert "store-only-recovery" in str(shadow_root)
        assert quiescence_guard() is True
        events.append("activate")

    monkeypatch.setattr(contract_store, "activate_store", activate)
    def verify_initial(*, prove_quiescent):
        events.append("birth-proof")
        prove_quiescent()
        events.append("birth-receipts")
        return {"contracts": 1, "receipts": 1}

    monkeypatch.setattr(
        executor_birth_bootstrap,
        "verify_initial_installer_store_v1",
        verify_initial,
    )
    monkeypatch.setattr(
        executor_birth_bootstrap, "bootstrap_birth_runtime",
        lambda: events.append("birth-runtime"),
    )
    monkeypatch.setattr(
        phase3_code,
        "_verify_contract_store_for_installation",
        lambda: events.append("cold-load") or {
            "bindings": 2, "loaded": 1, "retired": 1,
        },
    )

    result = phase3_code._recover_store_only()

    assert events == [
        "guard-enter", "key:False", "birth-proof", "proof", "birth-receipts",
        "catalog", "proof", "activate", "birth-runtime", "cold-load",
        "guard-exit",
    ]
    assert result["verification"]["loaded"] == 1


def test_phase3_preserves_its_stable_cutover_diagnostic(monkeypatch) -> None:
    @contextmanager
    def blocked():
        raise contract_cutover_guard.ContractCutoverGuardError(
            "cutover_blocked", "user:metnos-http.service is active",
        )
        yield  # pragma: no cover

    monkeypatch.setattr(
        contract_cutover_guard, "contract_cutover_guard", blocked,
    )

    with pytest.raises(
        phase3_code.ContractCatalogInstallError,
        match="contract_cutover_blocked: user:metnos-http.service is active",
    ):
        with phase3_code._phase3_cutover_boundary():
            pass


def test_authoring_census_keeps_disabled_and_excludes_retired(
    monkeypatch,
) -> None:
    active = SimpleNamespace(status=manifest_inventory.ManifestStatus.ADMITTED)
    disabled = SimpleNamespace(status=manifest_inventory.ManifestStatus.DISABLED)
    retired = SimpleNamespace(status=manifest_inventory.ManifestStatus.RETIRED)
    inventory = manifest_inventory.ManifestInventory(
        problems=(), manifests=(active, disabled, retired),
    )
    monkeypatch.setattr(
        manifest_inventory, "inventory_authoring_manifests", lambda: inventory,
    )

    assert phase3_code._clean_authoring_inventory() == (active, disabled)


def test_phase3_has_no_legacy_mass_signing_invocation() -> None:
    source = phase3_code.Path(phase3_code.__file__).read_text(encoding="utf-8")
    assert '"sign-all"' not in source
    assert "subprocess.run([py, sign_py" not in source
    assert "sign_executor" not in source
