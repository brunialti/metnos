from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from install import operator_authority


def _public(private_path: Path) -> bytes:
    key = Ed25519PrivateKey.from_private_bytes(private_path.read_bytes())
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def test_operator_procedure_separates_private_keys_and_is_idempotent(tmp_path: Path) -> None:
    owner = (os.getuid(), os.getgid())
    config_parent = tmp_path / "home" / ".config"
    config_parent.parent.mkdir(mode=0o700)
    config_parent.mkdir(mode=0o700)
    result = operator_authority.provision_paths(
        target_config=config_parent / "metnos",
        private_base=tmp_path / "root-private",
        target_owner=owner,
        private_owner=owner,
    )

    assert result["status"] == "created"
    private = Path(result["private_dir"])
    public = Path(result["public_dir"])
    assert {item.name for item in private.iterdir()} == operator_authority.PRIVATE_FILES
    assert {item.name for item in public.iterdir()} == operator_authority.PUBLIC_FILES
    assert oct(private.stat().st_mode & 0o777) == "0o700"
    assert all(oct(item.stat().st_mode & 0o777) == "0o600" for item in private.iterdir())

    approval = json.loads((public / "approval-authority.json").read_bytes())
    assert base64.b64decode(approval["keys"]["operator-key"]) == _public(
        private / "operator-key.priv"
    )
    assert (public / "semantic-public" / "review.pub").read_bytes() == _public(
        private / "review-key.priv"
    )
    assert not any(item.suffix == ".priv" for item in public.rglob("*"))

    repeated = operator_authority.provision_paths(
        target_config=config_parent / "metnos",
        private_base=tmp_path / "root-private",
        target_owner=owner,
        private_owner=owner,
    )
    assert repeated["status"] == "verified"
    assert repeated["private_created"] is False


def test_operator_procedure_refuses_public_drift(tmp_path: Path) -> None:
    owner = (os.getuid(), os.getgid())
    config_parent = tmp_path / "home" / ".config"
    config_parent.parent.mkdir(mode=0o700)
    config_parent.mkdir(mode=0o700)
    arguments = {
        "target_config": config_parent / "metnos",
        "private_base": tmp_path / "root-private",
        "target_owner": owner,
        "private_owner": owner,
    }
    result = operator_authority.provision_paths(**arguments)
    (Path(result["public_dir"]) / "approval-authority.json").write_bytes(b"{}")

    with pytest.raises(operator_authority.OperatorAuthorityError) as error:
        operator_authority.provision_paths(**arguments)
    assert error.value.code == "operator_authority_public_invalid"


def test_operator_output_is_accepted_by_productive_birth_loader(
    tmp_path: Path, monkeypatch,
) -> None:
    from install.birth_authority_provisioner import acquire_operator_inputs_v1
    from tests.portable.rm0008_2b import support

    owner = (os.getuid(), os.getgid())
    base = tmp_path / "config"
    operator_authority.provision_paths(
        target_config=base,
        private_base=tmp_path / "root-private",
        target_owner=owner,
        private_owner=owner,
    )
    layout = support.open_layout(monkeypatch, base)
    try:
        acquired = acquire_operator_inputs_v1(layout.operator_input)
    finally:
        layout.birth_session.close()

    assert len(acquired.semantic_publics) == 1
    assert len(acquired.approval_sha256) == 64
    assert len(acquired.semantic_sha256) == 64


def test_bootstrap_handoff_exports_flat_runtime_and_check_is_user_state_free() -> None:
    bootstrap = (Path(__file__).resolve().parents[3] / "install" / "bootstrap.sh").read_text()
    assert 'export PYTHONPATH="$REPO_DIR:$REPO_DIR/runtime${PYTHONPATH:+:$PYTHONPATH}"' in bootstrap
    assert 'if [ "$CHECK_ONLY" != 1 ]; then\n  mkdir -p "$METNOS_USER_STATE/install"' in bootstrap
