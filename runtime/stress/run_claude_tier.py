#!/usr/bin/env python3
"""Wrapper: avvia synt_stress_50.py dopo aver letto ANTHROPIC_API_KEY dal
credentials.env. Mantiene la stessa CLI di synt_stress_50.py ma evita di
dover impostare la variabile in shell."""
import os
import subprocess
import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parents[1]
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))
import config as C  # noqa: E402

CRED = C.PATH_USER_CONFIG / "credentials.env"


def load_env():
    if not CRED.exists():
        return
    for line in CRED.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        # Tronca a primo spazio (alcune linee hanno commenti)
        # ma per la API key tienila intatta
        os.environ.setdefault(k, v)


def main():
    load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY non trovato in env o credentials.env",
              file=sys.stderr)
        sys.exit(2)
    cmd = [sys.executable, str(Path(__file__).parent / "synt_stress_50.py")] \
          + sys.argv[1:]
    sys.exit(subprocess.call(cmd, env=os.environ))


if __name__ == "__main__":
    main()
