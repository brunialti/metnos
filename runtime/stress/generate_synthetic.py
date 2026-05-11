#!/usr/bin/env python3
"""Genera N executor sintetici firmati per stress test del catalogo."""
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from sign import sign_executor


# Famiglie sintetiche: ogni famiglia ha 1 capability + N executor con affinity diversi.
FAMILIES = [
    ("fs",      "fs:read",      "path_glob", ["~/notes/**", "/tmp/**"], ["file","leggi","fs"]),
    ("net",     "network:http", "host",      ["api.example.com"],        ["web","http","scarica"]),
    ("time",    "time:read",    "none",      [],                          ["ora","data","time"]),
    ("text",    "fs:read",      "path_glob", ["/tmp/**"],                 ["testo","parse","analizza"]),
    ("img",     "fs:read",      "path_glob", ["/tmp/**"],                 ["immagine","foto","img"]),
    ("audio",   "fs:read",      "path_glob", ["/tmp/**"],                 ["audio","suono","trascrivi"]),
    ("data",    "fs:read",      "path_glob", ["/tmp/**"],                 ["dati","csv","json","query"]),
    ("calc",    "time:read",    "none",      [],                          ["calcolo","matematica","conta"]),
]


CODE_TEMPLATE = '''#!/usr/bin/env python3
"""Synthetic executor: {name}."""
import json, sys
def invoke(args):
    return {{"ok": True, "content": "synthetic-{name}", "metadata": {{"family":"{family}","name":"{name}"}}}}
def main():
    raw = sys.stdin.read()
    try:
        args = json.loads(raw) if raw.strip() else {{}}
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({{"ok":False,"error":str(e)}})); return
    sys.stdout.write(json.dumps(invoke(args)))
if __name__ == "__main__":
    main()
'''


def generate(out_dir, n):
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    for i in range(n):
        family = FAMILIES[i % len(FAMILIES)]
        fam_name, capability, target_kind, hints, base_aff = family
        name = f"{fam_name}_synth_{i:04d}"
        ex_dir = out_dir / name
        ex_dir.mkdir()

        # Manifest
        hints_toml = ", ".join(f'"{h}"' for h in hints)
        affinity = base_aff + [f"variant{i % 7}", f"sub{i % 5}"]
        affinity_toml = ", ".join(f'"{a}"' for a in affinity)
        manifest = f"""manifest_format = "1.0"
name        = "{name}"
version     = "0.1.0"
author      = "stress-test"
description = "Executor sintetico stress di famiglia {fam_name}, indice {i}, per test scaling del catalogo."
affinity    = [{affinity_toml}]

[code]
files  = ["main.py"]
digest = "sha256:PENDING"

[args]
type = "object"

[args.properties.q]
type = "string"
description = "Stringa libera."

[[capabilities]]
name = "{capability}"
hint = [{hints_toml}]

[[tests]]
name   = "smoke"
input  = {{}}
expect = {{ ok = true }}
"""
        (ex_dir / "manifest.toml").write_text(manifest)
        (ex_dir / "main.py").write_text(CODE_TEMPLATE.format(name=name, family=fam_name))
        sign_executor(ex_dir, key_name="author")

    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/metnos_stress_executors")
    ap.add_argument("--n", type=int, default=30)
    args = ap.parse_args()
    n = generate(args.out, args.n)
    print(f"generati {n} executor sintetici in {args.out}")


if __name__ == "__main__":
    main()
