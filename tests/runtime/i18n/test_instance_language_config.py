"""RM-0005/F0: one signed, instance-scoped language authority."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"


def _run(tmp_path: Path, source: str, *, lang: str = "it") -> dict:
    data = tmp_path / "data"
    state = tmp_path / "state"
    config = tmp_path / "config"
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": f"{ROOT}:{RUNTIME}",
        "METNOS_INSTALL_ROOT": str(ROOT),
        "METNOS_USER_DATA": str(data),
        "METNOS_USER_STATE": str(state),
        "METNOS_USER_CONFIG": str(config),
        "METNOS_LANG": lang,
    })
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_bcp47_normalization_is_structural_and_locale_neutral(tmp_path: Path):
    result = _run(tmp_path, """
import json
import config
values = ["EN_us", "sr-Cyrl-RS", "de-DE-1996", "zh-Hant-TW", "x-private", "", "en-a"]
print(json.dumps([config.normalize_language_tag(value) for value in values]))
""")
    assert result == [
        "en-us", "sr-cyrl-rs", "de-de-1996", "zh-hant-tw", "", "", "",
    ]


def test_signed_request_survives_restart_and_overrides_bootstrap_env(
    tmp_path: Path,
):
    created = _run(tmp_path, """
import json
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import config
keys = config.PATH_USER_CONFIG / "keys"
keys.mkdir(parents=True)
private = Ed25519PrivateKey.generate()
(keys / "author_priv.bin").write_bytes(private.private_bytes(
    serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
    serialization.NoEncryption()))
(keys / "author_pub.bin").write_bytes(private.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw))
request, changed = config.write_localization_request(
    instance_lang="en", requested_lang="pt-BR", state="bootstrap_english",
    corpus_version="sha256:" + "1" * 64)
print(json.dumps({"changed": changed, "requested": request.requested_lang}))
""")
    assert created == {"changed": True, "requested": "pt-br"}

    restarted = _run(tmp_path, """
import json
import config
import i18n
print(json.dumps({
    "instance": config.INSTANCE_LANG,
    "requested": config.REQUESTED_LANG,
    "state": config.LOCALIZATION_STATE,
    "current": i18n.current_lang(),
}))
""", lang="it")
    assert restarted == {
        "instance": "en",
        "requested": "pt-br",
        "state": "bootstrap_english",
        "current": "en",
    }


def test_invalid_configuration_does_not_prevent_boot(tmp_path: Path):
    result = _run(tmp_path, """
import json
import config
print(json.dumps({
    "instance": config.INSTANCE_LANG,
    "requested": config.REQUESTED_LANG,
    "state": config.LOCALIZATION_STATE,
    "error": config.LOCALIZATION_ERROR,
}))
""", lang="not a language")
    assert result == {
        "instance": "it",
        "requested": None,
        "state": "fallback_invalid",
        "error": "invalid_instance_language",
    }


def test_tampered_request_is_rejected_without_preventing_boot(tmp_path: Path):
    _run(tmp_path, """
import json
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import config
keys = config.PATH_USER_CONFIG / "keys"
keys.mkdir(parents=True)
private = Ed25519PrivateKey.generate()
(keys / "author_priv.bin").write_bytes(private.private_bytes(
    serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
    serialization.NoEncryption()))
(keys / "author_pub.bin").write_bytes(private.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw))
config.write_localization_request(
    instance_lang="en", requested_lang="fr", state="bootstrap_english",
    corpus_version="sha256:" + "2" * 64)
print(json.dumps({"ok": True}))
""")
    path = tmp_path / "state" / "i18n" / "localization_request.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["payload"]["requested_lang"] = "de"
    path.write_text(json.dumps(document), encoding="utf-8")

    result = _run(tmp_path, """
import json
import config
print(json.dumps({
    "instance": config.INSTANCE_LANG,
    "requested": config.REQUESTED_LANG,
    "state": config.LOCALIZATION_STATE,
    "error": config.LOCALIZATION_ERROR,
}))
""", lang="")
    assert result == {
        "instance": "it",
        "requested": None,
        "state": "fallback_invalid",
        "error": "invalid_signature",
    }


def test_installer_persistence_is_byte_idempotent(tmp_path: Path):
    result = _run(tmp_path, """
import json
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from install import disclaimer
from runtime import config
keys = config.PATH_USER_CONFIG / "keys"
keys.mkdir(parents=True)
private = Ed25519PrivateKey.generate()
(keys / "author_priv.bin").write_bytes(private.private_bytes(
    serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
    serialization.NoEncryption()))
(keys / "author_pub.bin").write_bytes(private.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw))
config.write_private_text(disclaimer._sentinel(), json.dumps({
    "accepted_at": 1,
    "lang": "en",
    "requested_lang": "fr",
    "localization_state": "bootstrap_english",
    "agreement_token": "i accept",
}))
first = disclaimer.persist_localization_request()
before = config.PATH_LOCALIZATION_REQUEST.read_bytes()
second = disclaimer.persist_localization_request()
after = config.PATH_LOCALIZATION_REQUEST.read_bytes()
print(json.dumps({"first": first, "second": second, "same": before == after}))
""")
    assert result == {"first": True, "second": False, "same": True}


def test_request_context_cannot_replace_instance_language(tmp_path: Path):
    result = _run(tmp_path, """
import json
import i18n
before = i18n.current_lang()
with i18n.language_context("fr"):
    inside = i18n.current_lang()
after = i18n.current_lang()
print(json.dumps([before, inside, after]))
""", lang="en")
    assert result == ["en", "en", "en"]
