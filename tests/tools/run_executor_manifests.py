#!/usr/bin/env python3
"""Esegue test_runner su tutti i manifest in <install_root>/executors/.

La suite gira UNA ALLA VOLTA per macchina. I born-test isolano dati, stato e
configurazione dell'utente (sotto), ma i loro `setup`/`teardown` usano percorsi
assoluti fissi sotto `/tmp` — e devono, perche' la politica dello spazio di
lavoro ammette proprio quelli. Due esecuzioni contemporanee si cancellano a
vicenda le cartelle di prova a meta' test, e l'esito che ne esce e' rosso su un
executor a caso: misurato il 16/8/2026 su `find_files_hash` in una passata e su
`write_files` in un'altra, entrambe innocenti. Un lucchetto esclusivo lo rende
impossibile invece che confuso.
"""
import fcntl
import subprocess
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXECUTORS_DIR = REPO_ROOT / "executors"
TEST_RUNNER = REPO_ROOT / "runtime" / "test_runner.py"
LOCK_PATH = Path(tempfile.gettempdir()) / "metnos-manifest-suite.lock"


def _acquire_suite_lock():
    """Attende il proprio turno, dicendo che lo sta facendo."""
    handle = open(LOCK_PATH, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except BlockingIOError:
        pass
    print("suite dei manifest gia' in corso: attendo il mio turno "
          f"({LOCK_PATH})", file=sys.stderr, flush=True)
    started = time.monotonic()
    fcntl.flock(handle, fcntl.LOCK_EX)
    print(f"turno acquisito dopo {time.monotonic() - started:.0f}s",
          file=sys.stderr, flush=True)
    return handle


def main():
    lock = _acquire_suite_lock()
    try:
        return _run()
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def _run():
    manifests = sorted(EXECUTORS_DIR.glob("*/manifest.toml"))
    total_pass = total_fail = 0
    failed_executors: list[tuple[str, int]] = []
    with tempfile.TemporaryDirectory(prefix="metnos-manifest-suite-") as raw:
        root = Path(raw)
        env = os.environ.copy()
        installed_data = Path(
            env.get("METNOS_USER_DATA")
            or (Path.home() / ".local" / "share" / "metnos")
        )
        env.update({
            "METNOS_USER_DATA": str(root / "data"),
            "METNOS_USER_STATE": str(root / "state"),
            # La configurazione dell'utente era l'unica radice mutabile
            # rimasta scoperta: un born-test leggeva il file VERO
            # dell'installazione (misurato su get_location, che ha imparato a
            # leggere `location.toml`) e il suo esito dipendeva da come e'
            # configurata la macchina di chi lancia la suite.
            "METNOS_USER_CONFIG": str(root / "config"),
            "METNOS_INDEX_ROOT": str(root / "data" / "index"),
            "METNOS_HISTORY_DIR": str(root / "data" / "_history"),
        })
        for path in (root / "data", root / "state", root / "config"):
            path.mkdir(parents=True, exist_ok=True)
        # Error strings and detection concepts are immutable seed inputs for
        # the birth tests. Copy only those two stores; never expose the rest of
        # the user's mutable data to an executor under test.
        for name in ("i18n.sqlite", "detection.sqlite"):
            source = installed_data / name
            if source.is_file():
                shutil.copy2(source, root / "data" / name)
        # Le chiavi di fiducia sono l'altro ingresso immutabile: senza, il
        # loader scarta ogni executor firmato e la suite misurerebbe la
        # verifica delle firme invece del comportamento.
        installed_config = Path(
            os.environ.get("METNOS_USER_CONFIG")
            or (Path.home() / ".config" / "metnos"))
        if (installed_config / "keys").is_dir():
            shutil.copytree(installed_config / "keys", root / "config" / "keys",
                            dirs_exist_ok=True)
        # Anche la politica dello spazio di lavoro e' un ingresso, non stato:
        # i born-test usano percorsi sotto /tmp e senza di essa ricadrebbero
        # sull'ambito predefinito `~/**`. Resta l'unico file di configurazione
        # copiato oltre alle chiavi: il resto (posizione, tier, admin key) non
        # deve raggiungere un executor sotto test.
        policy = installed_config / "workspace_policy.toml"
        if policy.is_file():
            shutil.copy2(policy, root / "config" / policy.name)
        for m in manifests:
            print(f"\n>>> {m.parent.name}")
            result = subprocess.run(
                ["python3", str(TEST_RUNNER), str(m)],
                capture_output=True, text=True, env=env,
            )
            sys.stdout.write(result.stdout)
            if result.stderr:
                sys.stderr.write(result.stderr)
            # Parse "X/Y passati" dalla coda dello stdout
            import re
            for line in result.stdout.splitlines():
                mt = re.search(r"(\d+)/(\d+)\s+passati", line)
                if mt:
                    p, t = int(mt.group(1)), int(mt.group(2))
                    total_pass += p; total_fail += t - p
                    if p != t:
                        failed_executors.append((m.parent.name, t - p))
                    break
    print(f"\n=================================")
    print(f" SUMMARY: {total_pass}/{total_pass+total_fail} test passati su {len(manifests)} executor")
    if failed_executors:
        rendered = ", ".join(
            f"{name}({count})" for name, count in failed_executors)
        print(f" FAILING EXECUTORS: {rendered}")
    print(f"=================================")
    sys.exit(0 if total_fail == 0 else 1)


if __name__ == "__main__":
    main()
