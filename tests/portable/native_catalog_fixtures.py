"""Authenticate public executor bytes with a temporary fixture author key.

This supplies a real native catalog without trusting the host installation or
executing the copied executor. It does not attest installed catalog custody.
"""
from pathlib import Path
import shutil

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def authenticated_catalog(tmp_path, monkeypatch):
    import loader
    import sign

    source = Path(__file__).resolve().parents[2] / 'executors' / 'read_files_ocr'
    root = tmp_path / 'authenticated-public-catalog'
    target = root / source.name
    shutil.copytree(source, target)
    private = Ed25519PrivateKey.generate()
    (target / 'manifest.toml.sig').write_bytes(sign.sign_manifest_bytes(
        (target / 'manifest.toml').read_bytes(), private_key=private))
    catalog = loader.Catalog()
    with monkeypatch.context() as trust:
        trust.setattr(sign, 'list_trusted_publics',
                      lambda: (('fixture-author', private.public_key()),))
        loader._load_dir_into_catalog(root, catalog, verify=True, current_lang='en')
    assert not catalog.rejected, catalog.rejected
    assert catalog.get('read_files_ocr').signed_by == 'fixture-author'

    def forbidden(*_args, **_kwargs):
        pytest.fail('already authenticated catalog reopened through service loader')

    monkeypatch.setattr(loader, 'load_catalog', forbidden)
    return catalog
