"""Six-phase installer: administrative effects in the parent, dialogs as Metnos.

The worker has no administrative capability. Its phase records are convenience
state; installation and activation always repeat the authenticated transition.
"""
from __future__ import annotations

import base64
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "runtime"):
    sys.path.insert(0, str(directory))

from install import disclaimer, i18n, services, state, ui
from runtime.service_profile import validate_profile


def _encode(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode("ascii")


def _decode(value: str) -> dict:
    if len(value) > 65536:
        raise ValueError("installer request size")
    result = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
    if not isinstance(result, dict):
        raise ValueError("installer request")
    return result


def launch(args, profile: dict) -> int:
    """Called after the real user's language/disclaimer and install consent."""
    if args.skip:
        ui.fail(i18n.t("managed_skip"))
    selection = disclaimer.read_language_selection()
    if selection is None:
        ui.fail(i18n.t("managed_consent"))
    request = {
        "args": asdict(args), "profile": validate_profile(profile),
        "consent": json.loads(disclaimer._sentinel().read_text()),
    }
    ui.info(i18n.t("managed_privilege"))
    command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "prepare", _encode(request)]
    if os.geteuid() != 0:
        command = ["sudo", "--", *command]
    return subprocess.run(command, check=False).returncode


def _copy_source(source: Path, destination: Path) -> None:
    """Project product files; never receive .git, a venv or mutable user data."""
    from install.executor_birth_distribution_release import (
        _SOURCE_ROOTS_V1, _projected_source_path_v1,
        BOUNDARY_INVENTORY_SOURCE_PATH_V1, DEPENDENCY_SOURCE_PATH_V1,
    )
    selected = [source / path for path in
                (BOUNDARY_INVENTORY_SOURCE_PATH_V1, DEPENDENCY_SOURCE_PATH_V1)]
    for base in sorted(_SOURCE_ROOTS_V1):
        selected.extend(path for path in (source / base).rglob("*")
                        if path.is_file() and _projected_source_path_v1(path.relative_to(source).as_posix()))
    for path in selected:
        relative = path.relative_to(source)
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != source):
            raise ValueError("installer source link")
        target = destination / relative
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        target.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
    for directory in (destination, *(p for p in destination.rglob("*") if p.is_dir())):
        directory.chmod(0o755)


def _prepare_python(source: Path, profile: dict):
    """Populate the existing exact-lock wheelhouse and managed Python store."""
    from install.executor_birth_distribution_release import (
        _PYTHON_WHEELHOUSE_V1, _PYTHON_ENVIRONMENT_PROFILE_V1,
    )
    from install.executor_birth_python_environment_posix import (
        PRODUCT_ENVIRONMENT_STORE_V1, ensure_python_environment_v1,
    )

    required = ["/usr/bin/python3.12", "/usr/bin/openssl", "/usr/bin/systemctl",
                "/usr/bin/systemd-analyze", "/usr/lib/polkit-1/polkitd"]
    if "playwright" not in profile:
        required.append("/usr/bin/Xvfb")
    if "searxng" not in profile:
        required.append("/usr/bin/git")
    if "photon" not in profile:
        required.extend(("/usr/bin/java", "/usr/bin/unzstd"))
    missing = [path for path in required if not Path(path).is_file()]
    if missing:
        ui.fail(i18n.t("managed_prerequisites", paths=", ".join(missing)))
    wheelhouse = _PYTHON_WHEELHOUSE_V1
    for target in (wheelhouse, PRODUCT_ENVIRONMENT_STORE_V1):
        for directory in (*reversed(target.parents), target):
            directory.mkdir(mode=0o755, exist_ok=True)
            info = directory.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                    or info.st_gid != 0 or info.st_mode & 0o022):
                raise ValueError("installer dependency directory ownership")
    for wheel in wheelhouse.iterdir():
        info = wheel.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("installer wheel ownership")
    lock = source / "requirements-linux-x86_64.lock"
    subprocess.run([sys.executable, "-I", "-B", "-m", "pip", "download", "--quiet",
                    "--disable-pip-version-check", "--no-cache-dir",
                    "--require-hashes", "--no-deps", "--only-binary=:all:",
                    "--dest", str(wheelhouse), "-r", str(lock)], check=True,
                   env={**os.environ, "PIP_CONFIG_FILE": "/dev/null"})
    for wheel in wheelhouse.glob("*.whl"):
        if wheel.is_symlink() or wheel.stat().st_uid != 0:
            raise ValueError("installer wheel ownership")
        wheel.chmod(0o644)
    return ensure_python_environment_v1(lock.read_bytes(), wheelhouse, _PYTHON_ENVIRONMENT_PROFILE_V1)


def _prepare(request: dict) -> int:
    if os.geteuid() != 0:
        raise PermissionError("administrative installer required")
    profile = validate_profile(request["profile"])
    # This is a temporary read-only source projection, not another installation.
    with tempfile.TemporaryDirectory(prefix="metnos-installer-", dir="/var/tmp") as temporary:
        source = Path(temporary)
        _copy_source(ROOT, source)
        environment = _prepare_python(source, profile)
        command = [str(environment.python_executable), "-I", "-B",
                   str(source / "install/managed_install.py"), "run", _encode(request)]
        return subprocess.run(command, cwd=source, check=False).returncode


def _worker(request: dict) -> int:
    """Unprivileged phases and dialogs, using the parent's controlling terminal."""
    if os.geteuid() == 0:
        raise PermissionError("installer worker must use the service account")
    from install.__main__ import _PHASES, _run_phase
    args = SimpleNamespace(**request["args"], managed=True)
    profile = validate_profile(request["profile"])
    phase = request["phase"]
    if phase == 7:
        from install.phases.phase5_systemd import _ensure_lre_feature_config
        _ensure_lre_feature_config()
        return 0
    if phase == 0:
        old = state.load(2)
        if old and old.success and old.notes.get("services_profile") != services.fingerprint(profile):
            ui.fail(i18n.t("managed_profile_changed"))
        import tomlkit
        services._write(services.config_dir() / "services.toml", tomlkit.dumps(profile))
        # The worker retains the acceptance actually collected from the user.
        services._write(disclaimer._sentinel(), json.dumps(request["consent"]))
        if disclaimer.read_language_selection() is None:
            ui.fail(i18n.t("managed_consent"))
        only = args.only_phase
        if only and any(not state.is_done(number) for number in range(1, only)):
            ui.fail(i18n.t("managed_previous_phases"))
        ui.summary_panel(state.summary())
        return 0
    selected = next(item for item in _PHASES if item[0] == phase)
    return 0 if _run_phase(*selected, args) else 2


def _run(request: dict) -> int:
    if os.geteuid() != 0:
        raise PermissionError("administrative installer required")
    from install import executor_birth_transition as transition
    from executor_birth_host_path_policy import SERVICE_ACCOUNT_NAME_V1

    selected, environment = transition._provisioned_service_environment_v1(SERVICE_ACCOUNT_NAME_V1)
    # The receiver imports runtime configuration, whose paths are fixed on
    # import. Select the service identity before loading any such consumer.
    os.environ.update(environment)
    os.environ["METNOS_INSTALL_ROOT"] = str(ROOT)
    from install.executor_birth_source_receiver import _service_account_snapshot_v1

    account = _service_account_snapshot_v1(selected)
    runtime_root, python = ROOT, sys.executable

    def worker(phase: int) -> None:
        env = transition._install_environment_v1(runtime_root, environment)
        env.update(METNOS_VENV=str(Path(python).parent.parent),
                   METNOS_LOCALE=request["consent"]["lang"], PYTHONDONTWRITEBYTECODE="1")
        command = [python, "-I", "-B", str(runtime_root / "install/managed_install.py"),
                   "worker", _encode({**request, "phase": phase})]
        subprocess.run(command, cwd=runtime_root, env=env, check=True,
                       user=account.uid, group=account.gid,
                       extra_groups=account.supplementary_gids, umask=0o077)

    worker(0)
    only = request["args"].get("only_phase")
    for phase in (1, 2):
        if only is None or only == phase:
            worker(phase)
    if only in (1, 2):
        return 0
    from install.managed_infrastructure import prepare
    prepare(request['profile'], environment)
    # Phase state is not authority. Every continuation authenticates the same
    # received source, catalog, distribution and durable transition again.
    ui.step(i18n.t("managed_adopt"))
    distribution, source_id, selected, environment = transition._prepare_install_source_v1(ROOT, selected)
    handoff = dict(distribution=distribution, source_id=source_id, service_user=selected,
                   service_environment=environment, legacy_service_user=None,
                   legacy_installation_root=None)
    transition._invoke_closed_release_v1(**handoff, activate=False)
    runtime_root, _entry, python = transition._installed_transition_runtime_v1(distribution)
    for phase in (3, 4):
        if only is None or only == phase:
            worker(phase)
    if only in (3, 4):
        return 0
    # LRE's default-off configuration must be written before any consumer starts.
    worker(7)
    ui.step(i18n.t("managed_activate"))
    transition._invoke_closed_release_v1(**handoff, activate=True)
    for phase in (5, 6):
        if only is None or only == phase:
            worker(phase)
    ui.banner(i18n.t("managed_complete"), i18n.t("managed_inspect"))
    return 0


def main() -> int:
    sys.dont_write_bytecode = True
    try:
        if len(sys.argv) != 3 or sys.argv[1] not in {"prepare", "run", "worker"}:
            raise ValueError("installer operation")
        request = _decode(sys.argv[2])
        return {"prepare": _prepare, "run": _run, "worker": _worker}[sys.argv[1]](request)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        ui.warn(i18n.t("managed_failed", reason=str(exc)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
