"""The installed proof limited to the provisioner (section 12.4).

It starts from an installed copy and an empty root created by the internal
entry of the provisioner, not by a helper that writes the stores directly.
Every step that must survive a stop runs in its own process, so the stop is
real.

This is not a Phase 3 installed proof, a Birth start, a re-attestation, a
productive publication, a cold archive or an F4 certification.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROOF_ID = "installed_provisioner_proof_v1"

_INSTALLED_TREES = ("runtime", "install")

_NOT_YET_PROVEN = (
    "the Birth runtime is not started and no caller is migrated",
    "Producer factories stay inactive until group 3",
    "distribution authenticity and same-UID protection need groups 4 to 6",
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def install_copy(source: Path, target: Path) -> Path:
    """Copy the distribution the way an installation holds it."""
    target.mkdir(mode=0o755, parents=True)
    for name in _INSTALLED_TREES:
        shutil.copytree(
            source / name, target / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    from . import support

    for path in (target, *target.rglob("*")):
        support.apply_profile(path, directory=path.is_dir(), private=False)
    return target


def _environment(installed: Path, home: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update({
        "METNOS_INSTALL_ROOT": str(installed),
        "METNOS_USER_CONFIG": str(home / "config"),
        "METNOS_USER_STATE": str(home / "state"),
        "METNOS_USER_DATA": str(home / "data"),
        "METNOS_USER_CACHE": str(home / "cache"),
        "PYTHONPATH": os.pathsep.join(
            [str(installed), str(installed / "runtime")]
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return environment


def _run(installed: Path, home: Path, program: str) -> dict[str, object]:
    """Run one step in its own process and return what it reported."""
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=str(installed), env=_environment(installed, home),
        capture_output=True, text=True, timeout=600,
    )
    if completed.returncode != 0:
        return {
            "ok": False,
            "error": completed.stderr.strip().splitlines()[-1]
            if completed.stderr.strip() else "no output",
        }
    return {"ok": True, **json.loads(completed.stdout.splitlines()[-1])}


_STEP_DEFER = '''
import json
from install.birth_authority_provisioner import (
    prepare_or_defer_until_legacy_author_exists,
)
result = prepare_or_defer_until_legacy_author_exists()
print(json.dumps({"outcome": result.outcome.value}))
'''

_STEP_CREATE_AUTHOR = '''
import json
import sign
sign.generate_keypair(sign.DEFAULT_AUTHOR_KEY)
print(json.dumps({"created": True}))
'''

_STEP_ENSURE = '''
import json
from install.birth_authority_provisioner import (
    ensure_executor_birth_authorities_prepared,
)
result = ensure_executor_birth_authorities_prepared()
print(json.dumps({
    "outcome": result.outcome.value,
    "active_key_id": result.active_key_id,
}))
'''

_STEP_VERIFY = '''
import hashlib
import json
from pathlib import Path

import config
from executor_birth_keystore import _load_birth_keystore_in_session
from install.birth_authority_provisioner import (
    AUTHORITY_SETS_BASENAME_V1, AUTHOR_STORE_BASENAME_V1,
    PREPARED_MARKER_BASENAME_V1, _authority_registry_v1,
    _open_installer_layout_v1, _prepare_installed_admission_context_v1,
    _read_set_document_v1, decode_canonical_document_v1,
)

layout = _open_installer_layout_v1()
session = layout.birth_session
with session:
    with session.global_lock(exclusive=True, create=True):
        marker = decode_canonical_document_v1(
            _read_set_document_v1(session, (PREPARED_MARKER_BASENAME_V1,))
        )
        location = (AUTHORITY_SETS_BASENAME_V1, marker["set_id"])
        document = decode_canonical_document_v1(
            _read_set_document_v1(session, location + ("set.json",))
        )
        author = _load_birth_keystore_in_session(
            (AUTHOR_STORE_BASENAME_V1,), session,
        )
        admission = _load_birth_keystore_in_session(
            location + ("admission",), session,
        )
        registry = _authority_registry_v1(session, location)
        rebuilt = _prepare_installed_admission_context_v1(registry)
        material = _read_set_document_v1(
            session, location + ("context", "material-v1.json"),
        )
print(json.dumps({
    "set_id": document["set_id"],
    "marker_state": marker["state"],
    "marker_set_id": marker["set_id"],
    "author_active_key_id": author.active_key_id,
    "author_verifier_key_ids": sorted(author.verifier_keys),
    "declared_author_verifier_key_ids": document["author_verifier_key_ids"],
    "admission_active_key_id": admission.active_key_id,
    "declared_admission_active_key_id": document["admission_active_key_id"],
    "prepared_admission_context_id": document["prepared_admission_context_id"],
    "rebuilt_admission_context_id": rebuilt.prepared_admission_context_id,
    "prepared_context_epoch": document["prepared_context_epoch"],
    "rebuilt_context_epoch": rebuilt.prepared_context_epoch,
    "installed_material_sha256": hashlib.sha256(material).hexdigest(),
    "rebuilt_material_sha256": rebuilt.material_sha256,
    "producer_count": len(document["producer_keys"]),
}))
'''

_STEP_INACTIVE = '''
import inspect
import json

import executor_birth_bootstrap
import executor_birth_operational
from install.birth_authority_provisioner import AuthorProvisioningOutcomeV1

bootstrap_source = inspect.getsource(executor_birth_bootstrap)
print(json.dumps({
    "previous_decoder_present": "_context_builder" in bootstrap_source,
    "bootstrap_uses_provisioner": "birth_authority_provisioner" in bootstrap_source,
    "operational_uses_provisioner": "birth_authority_provisioner" in inspect.getsource(
        executor_birth_operational
    ),
    "outcomes": sorted(item.value for item in AuthorProvisioningOutcomeV1),
}))
'''


def run_proof(source: Path, workspace: Path) -> dict[str, object]:
    """Run every step and return the canonical report."""
    installed = install_copy(source, workspace / "installed")
    from . import support

    home = workspace / "home"
    config = home / "config"
    (config / "birth" / "operator-input-v1").mkdir(mode=0o755, parents=True)
    # ``mkdir`` applies the umask, and a Birth root anyone may write is
    # refused by the capability on purpose: an installation is not a working
    # tree.  On Windows the same fact is a security descriptor.
    for path in (home, config, config / "birth", config / "birth" / "operator-input-v1"):
        support.apply_profile(path, directory=True, private=False)
    keys = support.complete_operator_input(config)
    root = config / "birth"

    steps: list[dict[str, object]] = []
    deferred = _run(installed, home, _STEP_DEFER)
    steps.append({"step": "defer_without_author", "result": deferred})
    after = sorted(item.name for item in root.iterdir())
    # The lock is the capability's own object and taking it is how the entry
    # serialises; what must be absent is every provisioning artefact.
    steps.append({
        "step": "root_holds_no_artefact_after_defer",
        "result": {
            "ok": set(after) <= {"operator-input-v1", "provisioning-v1.lock"},
            "names": after,
        },
    })

    steps.append({
        "step": "create_legacy_author",
        "result": _run(installed, home, _STEP_CREATE_AUTHOR),
    })
    steps.append({
        "step": "converge_before_the_machine",
        "result": _run(installed, home, _STEP_DEFER),
    })
    steps.append({
        "step": "ensure_prepared",
        "result": _run(installed, home, _STEP_ENSURE),
    })

    isolated = workspace / "isolated"
    shutil.copytree(home, isolated)
    for item in sorted((isolated / "config" / "keys").iterdir()):
        item.unlink()
    (isolated / "config" / "keys").rmdir()
    steps.append({
        "step": "rerun_without_the_previous_source",
        "result": _run(installed, isolated, _STEP_ENSURE),
    })
    steps.append({
        "step": "verify_with_productive_loaders",
        "result": _run(installed, isolated, _STEP_VERIFY),
    })
    steps.append({
        "step": "nothing_is_active",
        "result": _run(installed, isolated, _STEP_INACTIVE),
    })

    return {
        "schema_version": 1,
        "proof_id": PROOF_ID,
        "platform": sys.platform,
        "python": "%d.%d" % sys.version_info[:2],
        "git_commit": _git_commit(source),
        "operator_public_keys": sorted(keys),
        "steps": steps,
        "not_yet_proven": list(_NOT_YET_PROVEN),
    }


def _git_commit(source: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def write_report(report: dict[str, object], path: Path) -> None:
    path.write_bytes(_canonical(report))
