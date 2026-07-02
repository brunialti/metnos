#!/usr/bin/env python3
"""client_signing.py — firma Ed25519 dei binari del client + export pubkey.

Usa la chiave 'author' del server (runtime/sign.py, KEYS_DIR) — la stessa che
firma gli executor e le invocazioni. Il device verifica il binario con la
pubkey pinnata nello script di install (§5.3 del design doc executor remoti).

Uso:
    client_signing.py sign <file>          scrive <file>.sig (Ed25519 raw)
    client_signing.py pubkey-der-b64       stampa la pubkey server (SPKI DER, b64)
"""
import base64
import sys
from pathlib import Path

_RUNTIME = next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "sign.py").is_file())
sys.path.insert(0, _RUNTIME)

from sign import DEFAULT_AUTHOR_KEY, load_private, load_public  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "sign":
        if len(sys.argv) < 3:
            print("uso: client_signing.py sign <file>", file=sys.stderr)
            return 2
        target = Path(sys.argv[2])
        priv = load_private(DEFAULT_AUTHOR_KEY)
        sig = priv.sign(target.read_bytes())
        sig_path = target.with_name(target.name + ".sig")
        sig_path.write_bytes(sig)
        print(str(sig_path))
        return 0
    if cmd == "pubkey-der-b64":
        pub = load_public(DEFAULT_AUTHOR_KEY)
        der = pub.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        print(base64.b64encode(der).decode("ascii"))
        return 0
    print(f"comando sconosciuto: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
