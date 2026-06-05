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
    python3 sign.py verify <manifest_dir>     verifica con tutte le chiavi trusted
    python3 sign.py sign-all [name]           keygen-se-manca + firma TUTTI gli
                                              executor (usato dall'installer)
"""
import hashlib
import os
import re
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization

from logging_setup import get_logger
import config as _C  # §7.11
log = get_logger(__name__)

KEYS_DIR = _C.PATH_USER_CONFIG / "keys"
DEFAULT_AUTHOR_KEY = "author"


def ensure_keys_dir():
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(KEYS_DIR, 0o700)


def generate_keypair(name):
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
    priv_path.write_bytes(priv_bytes)
    os.chmod(priv_path, 0o600)
    pub_path.write_bytes(pub_bytes)
    os.chmod(pub_path, 0o644)
    return priv_path, pub_path


def load_private(name):
    return Ed25519PrivateKey.from_private_bytes((KEYS_DIR / f"{name}_priv.bin").read_bytes())


def load_public(name):
    return Ed25519PublicKey.from_public_bytes((KEYS_DIR / f"{name}_pub.bin").read_bytes())


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


def compute_code_digest(manifest_dir, code_files):
    """SHA-256 della concatenazione dei file di codice in ordine dichiarato."""
    h = hashlib.sha256()
    for fname in code_files:
        path = manifest_dir / fname
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    return f"sha256:{h.hexdigest()}"


_DIGEST_RE = re.compile(r'(digest\s*=\s*")sha256:[^"]*(")')


def update_digest_in_text(manifest_text, new_digest):
    """Sostituisce la riga digest = '...' con il nuovo valore."""
    if not _DIGEST_RE.search(manifest_text):
        raise ValueError("manifest non contiene una riga 'digest = \"sha256:...\"'")
    return _DIGEST_RE.sub(rf'\g<1>{new_digest}\g<2>', manifest_text)


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
    import hashlib
    import json as _json

    def _h(s: str) -> str:
        return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()

    state: dict = {}
    for lang, val in desc.items():
        if isinstance(val, str):
            state.setdefault("description", {})[lang] = {
                "version_hash": _h(val),
                "source_lang": None,
                "source_hash": None,
            }
    props = (manifest.get("args") or {}).get("properties") or {}
    for arg_name, arg_def in props.items():
        if not isinstance(arg_def, dict):
            continue
        arg_desc = arg_def.get("description")
        if not isinstance(arg_desc, dict):
            continue
        key = f"args.{arg_name}.description"
        for lang, val in arg_desc.items():
            if isinstance(val, str):
                state.setdefault(key, {})[lang] = {
                    "version_hash": _h(val),
                    "source_lang": None,
                    "source_hash": None,
                }
    state_path.write_text(
        _json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sign_executor(manifest_dir, key_name=DEFAULT_AUTHOR_KEY):
    """Aggiorna digest, firma il manifest aggiornato, scrive manifest.toml.sig."""
    manifest_dir = Path(manifest_dir)
    manifest_path = manifest_dir / "manifest.toml"
    sig_path = manifest_dir / "manifest.toml.sig"

    import tomllib
    manifest = tomllib.loads(manifest_path.read_text())
    code_files = manifest.get("code", {}).get("files", [])
    if not code_files:
        raise ValueError("manifest senza [code].files")

    _validate_capabilities_schema(manifest, manifest_path)
    _ensure_lang_state_companion(manifest, manifest_dir)

    digest = compute_code_digest(manifest_dir, code_files)

    # Aggiorna digest nel testo del manifest
    text = manifest_path.read_text()
    new_text = update_digest_in_text(text, digest)
    if new_text != text:
        manifest_path.write_text(new_text)

    # Firma i bytes finali del manifest
    manifest_bytes = manifest_path.read_bytes()
    priv = load_private(key_name)
    signature = priv.sign(manifest_bytes)
    sig_path.write_bytes(signature)
    return digest, sig_path


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

    verified_by = None
    for name, pub in trusted:
        try:
            pub.verify(signature, manifest_bytes)
            verified_by = name
            break
        except Exception:
            continue

    if verified_by is None:
        return False, {"reason": "firma non verificata da alcuna chiave trusted"}

    import tomllib
    manifest = tomllib.loads(manifest_path.read_text())
    declared = manifest.get("code", {}).get("digest", "")
    code_files = manifest.get("code", {}).get("files", [])
    actual = compute_code_digest(manifest_dir, code_files)

    if declared != actual:
        return False, {"reason": f"digest mismatch: declared={declared} actual={actual}"}

    return True, {"signed_by": verified_by, "digest": actual}


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
        if len(sys.argv) < 3:
            print("Usage: sign <manifest_dir> [key_name]"); sys.exit(2)
        manifest_dir = sys.argv[2]
        key_name = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_AUTHOR_KEY
        digest, sig_path = sign_executor(manifest_dir, key_name)
        print(f"firmato: digest={digest} sig={sig_path}")

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
        manifests = sorted(ex_root.glob("**/manifest.toml"))
        ok_n = 0
        failed = []
        for m in manifests:
            try:
                sign_executor(str(m.parent), key_name)
                ok_n += 1
            except Exception as e:  # noqa: BLE001
                failed.append((str(m.parent), str(e)))
        print(f"sign-all: {ok_n} executor firmati, {len(failed)} errori "
              f"(executors dir: {ex_root})")
        for d, e in failed:
            print(f"  FAIL {d}: {e}")
        sys.exit(1 if failed else 0)

    else:
        print(__doc__); sys.exit(2)


if __name__ == "__main__":
    main()
