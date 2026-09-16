"""Own an unprivileged HTTP test process and its disposable storage.

Default inputs come from the shipped installation seed, not a personal
configuration. The caller prepares the isolated installation's signing and
Birth prerequisites through ``pre_spawn_hook``; failed bootstrap is never
hidden by treating a maintenance listener as operational readiness.

``seed_realistic`` is an explicit legacy diagnostic mode. It imports personal
data and links image storage; it is NOT isolated certification and must not
be used for F5 qualification or destructive scenarios.
"""
from __future__ import annotations

import os
import http.client
import json
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional



_REPO_ROOT = Path(__file__).resolve().parents[3]
_LIVE_USER_DATA = Path.home() / ".local/share/metnos"
_LIVE_USER_STATE = Path.home() / ".local/state/metnos"
_LIVE_USER_CONFIG = Path.home() / ".config/metnos"


def _runtime_python() -> str:
    """Use the installed Metnos interpreter when available.

    E2E exercises the worktree code with the dependency environment used by
    the deployed services. ``METNOS_E2E_PYTHON`` remains an explicit CI
    override; a source-only checkout falls back to the pytest interpreter.
    """
    override = os.environ.get("METNOS_E2E_PYTHON", "").strip()
    if override and Path(override).is_file():
        return override
    installed = _REPO_ROOT / ".venv" / "bin" / "python"
    return str(installed) if installed.is_file() else sys.executable


_PYTHON = _runtime_python()


# --- Seed realistic helper -------------------------------------------------


def _seed_i18n_baseline(user_data: Path) -> None:
    """Use the same shipped message seed as a fresh installation.

    Missing installation inputs are errors, not permission to borrow live
    data. An explicitly supplied fixture may already contain its own seed.
    """
    src = _REPO_ROOT / "install" / "data" / "i18n_seed.sqlite"
    dst = user_data / "i18n.sqlite"
    if dst.exists():
        return
    user_data.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def _copy_db_with_wal(src: Path, dst: Path) -> None:
    """Copia un file (sqlite o normale). Per sqlite: usa il backup API
    SQLite, che gestisce correttamente WAL/SHM e produce un file
    consistent senza dover copiare i sidecar.

    Razionale §7.9: copiare solo `*.sqlite` di un DB attivo con WAL non
    materializza le transazioni pending → il consumer legge zero rows
    dall'old snapshot. SQLite `.backup()` consolida WAL → main file.

    Per file non-sqlite (jsonl, json, txt), fallback shutil.copy2.
    """
    suffix = src.suffix.lower()
    if suffix in (".sqlite", ".db"):
        import sqlite3
        try:
            with sqlite3.connect(str(src)) as src_conn:
                with sqlite3.connect(str(dst)) as dst_conn:
                    src_conn.backup(dst_conn)
            return
        except sqlite3.Error:
            pass  # fallback a copy2 sotto
    shutil.copy2(src, dst)


def _seed_realistic_into(user_data: Path, user_state: Path,
                          user_config: Path) -> None:
    """Copia subset realistico dal sistema live nelle tmp dir.

    User data (dati storici, read-only di fatto nei test):
      - turns/                  turn history (corpus principale)
      - i18n.sqlite             chiavi messaggi
      - skills/                 skill source (credentials google ecc.)
      - synth_proposals/        synt history
      - telos_proposals.jsonl   proposte introvertive
      - turn_feedback.jsonl     feedback ✓/✗
      - executor_aliases.json   alias dedupe
      - rejected_patterns.jsonl ban patterns
      - introvertiva/           audit logs

    User state (database state, copiati per riferimento ma write isolati):
      - executor_stats.db       stats executor
      - proposals_state.db      introvertiva state
      - change_intents.sqlite   ADR 0158
      - scheduler_v2.sqlite     scheduler jobs

    User config (credentials read-only):
      - admin.key NON copiato — il server genera nuovo per tmp
      - runtime.toml            tuning
      - owned_domains.json      tier policy
      - blocked_origins.json    auto-degrade
      - mail.env                mailbox env

    Niente di workspace/.mnestoma (e' nel repo root, gia' visibile).
    """
    items_data = [
        "turns", "i18n.sqlite", "skills", "synth_proposals",
        # Derived, signed and free of user content.  Keeping the admitted
        # snapshot lets isolated HTTP tests exercise Tutor immediately while
        # the ordinary background bootstrap checks whether a rebuild is due.
        "tutor_catalog.sqlite", "tutor_catalog.sqlite.sig",
        "telos_proposals.jsonl", "turn_feedback.jsonl",
        "executor_aliases.json", "rejected_patterns.jsonl",
        "introvertiva", "scratchpad.db", "multi_tool_paths.sqlite",
        "devices.db", "locations.jsonl",
        "executors/skills",   # skill imports (ADR 0160 canonical)
        "executors/_imports", # skill imports legacy back-compat
        # users.db: registro utenti + canali verified (telegram/mail).
        # Senza, `send_messages(to_user=...)` fallisce con
        # `no_verified_channel` anche se Roberto ha canali in live →
        # test e2e «mandami un ping» rifiutato dal judge (24/5/2026).
        "users.db",
    ]
    for name in items_data:
        src = _LIVE_USER_DATA / name
        if not src.exists():
            continue
        dst = user_data / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            elif name == "tutor_catalog.sqlite":
                # Detached signature authenticates the exact SQLite bytes.
                # sqlite3.backup() is logically equivalent but rewrites page
                # bytes and invalidates the copied signature.
                shutil.copy2(src, dst)
            else:
                _copy_db_with_wal(src, dst)
        except (OSError, shutil.Error):
            # Non blocchiamo lo spawn per un singolo item failure
            pass

    # NB: NON copiare `change_intents.sqlite` ← e' un artefatto del
    # materializer (ADR 0158), rigenerato dai 6 adapter ad ogni run.
    # Se copiato, contiene intent generati con vecchi adapter (es. kind
    # sbagliato dopo refactor) → contamina fixture.
    items_state = [
        "executor_stats.db", "proposals_state.db",
        "scheduler_v2.sqlite",
        "telos_decisions.jsonl",
    ]
    for name in items_state:
        src = _LIVE_USER_STATE / name
        if not src.exists():
            continue
        dst = user_state / name
        try:
            _copy_db_with_wal(src, dst)
        except (OSError, shutil.Error):
            pass

    # Index immagini (read-only, condiviso via symlink: 489MB → 0MB copy).
    # Permette a find_images_indices nei test E2E di vedere il corpus live
    # senza moltiplicare lo storage per ogni test. Fail-safe: se non esiste
    # nel live, skip silenzioso.
    src_index = _LIVE_USER_DATA / "index"
    dst_index = user_data / "index"
    if src_index.is_dir() and not dst_index.exists():
        try:
            os.symlink(str(src_index), str(dst_index),
                        target_is_directory=True)
        except OSError:
            pass

    # Canonical photo workspace: preserve the live indirection without copying
    # the corpus. The paired index symlink above is only useful when discovery
    # can resolve the same canonical corpus path.
    src_images = _LIVE_USER_DATA / "Immagini"
    dst_images = user_data / "Immagini"
    if src_images.is_dir() and not dst_images.exists():
        try:
            os.symlink(str(src_images.resolve()), str(dst_images),
                       target_is_directory=True)
        except OSError:
            pass

    items_config = [
        "runtime.toml", "owned_domains.json", "blocked_origins.json",
        "trusted_origins.json", "mail.env", "github_watched_repos.json",
        "llm_tiers.toml",
        # Public half only: validates the realistic signed Tutor catalog in
        # the isolated process without exposing/copying the author private key.
        "keys/author_pub.bin",
    ]
    for name in items_config:
        src = _LIVE_USER_CONFIG / name
        if not src.exists():
            continue
        dst = user_config / name
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        except (OSError, shutil.Error):
            pass


def _free_port() -> int:
    """Trova una porta TCP libera (bind 0 → SO_REUSEADDR + close)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_ready(host: str, port: int, *, process: subprocess.Popen,
                timeout_s: float = 30.0) -> bool:
    """Require operational HTTP, not a listener or maintenance-only startup.

    Use a direct connection: no environment proxy, redirects or credentials
    from the invoking user's network configuration participate in readiness.
    """
    deadline = time.monotonic() + timeout_s
    while (remaining := deadline - time.monotonic()) > 0:
        if process.poll() is not None:
            return False
        connection = http.client.HTTPConnection(host, port, timeout=min(.5, remaining))
        try:
            connection.request("GET", "/agent/health", headers={"Accept": "application/json"})
            response = connection.getresponse()
            body = response.read(65537)
            value = json.loads(body) if len(body) <= 65536 else None
            if (response.status == 200 and isinstance(value, dict)
                    and value.get("ok") is True
                    and value.get("operational") is True
                    and value.get("maintenance_only") is False
                    and process.poll() is None):
                return True
        except (OSError, http.client.HTTPException, ValueError, RecursionError):
            pass
        finally:
            connection.close()
        time.sleep(min(.1, max(0, deadline - time.monotonic())))
    return False


def _stop_process(process: subprocess.Popen, timeout_s: float = 3.0) -> None:
    """Reap the owned child and terminate its session's remaining children."""
    def send(sig: int) -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, sig)
            elif process.poll() is None:
                process.terminate() if sig == signal.SIGTERM else process.kill()
        except ProcessLookupError:
            pass

    send(signal.SIGTERM)
    try:
        process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        pass
    # The parent can exit before a child that ignores SIGTERM. The group is
    # private because spawn always creates a new session.
    send(signal.SIGKILL if os.name == "posix" else signal.SIGTERM)
    if process.poll() is None:
        process.kill()
    process.wait(timeout=2)


@dataclass
class E2EServer:
    """Subprocess server isolato per E2E.

    Storage:
      - tmp_root: tests/e2e/tmp/<run_id>/
      - user_data: <tmp_root>/data
      - user_state: <tmp_root>/state
      - admin.key: scritto in <user_config>/admin.key
    """
    process: subprocess.Popen
    port: int
    admin_key: str
    tmp_root: Path
    user_data: Path
    user_state: Path
    user_config: Path
    host: str = "127.0.0.1"
    runtime_env: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @classmethod
    def spawn(cls, *, host: str = "127.0.0.1",
              ready_timeout_s: float = 30.0,
              seed_user_data: Optional[Path] = None,
              seed_realistic: bool = False,
              pre_spawn_hook: Optional[callable] = None,
              hide_executors: Optional[list] = None) -> "E2EServer":
        """Start a disposable server and wait for operational HTTP.

        ``seed_user_data`` explicitly supplies a data fixture. Signing and
        closed-build prerequisites belong to ``pre_spawn_hook(env, tmp_root)``.
        ``seed_realistic`` borrows personal inputs and is never an F5 fixture.
        Neither mode permits privileged execution of the HTTP test process.
        """
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            raise RuntimeError("HTTP test processes must run unprivileged")
        parent = _REPO_ROOT / "tests" / "e2e" / "tmp"
        parent.mkdir(parents=True, exist_ok=True)
        tmp_root = Path(tempfile.mkdtemp(prefix="run-", dir=parent))
        try:
            return cls._spawn_at(
                tmp_root, host=host, ready_timeout_s=ready_timeout_s,
                seed_user_data=seed_user_data, seed_realistic=seed_realistic,
                pre_spawn_hook=pre_spawn_hook, hide_executors=hide_executors,
            )
        except BaseException:
            if os.environ.get("METNOS_E2E_KEEP_TMP") != "1":
                shutil.rmtree(tmp_root)
            raise

    @classmethod
    def _spawn_at(cls, tmp_root: Path, *, host, ready_timeout_s, seed_user_data,
                  seed_realistic, pre_spawn_hook, hide_executors) -> "E2EServer":
        user_data = tmp_root / "data"
        user_state = tmp_root / "state"
        user_config = tmp_root / "config"
        for d in (user_data, user_state, user_config):
            d.mkdir(parents=True, exist_ok=True)

        # Seed: copia eventuale dati pre-esistenti (es. credentials)
        if seed_user_data is not None and seed_user_data.is_dir():
            for item in seed_user_data.iterdir():
                src = item
                dst = user_data / item.name
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)

        # Fresh installation input; explicit realistic diagnostics below are
        # separate from isolated certification and are not qualifying cycles.
        _seed_i18n_baseline(user_data)

        # Explicit legacy diagnostics only; linked image storage is not a
        # copy and must not be mistaken for an isolated writable fixture.
        if seed_realistic:
            _seed_realistic_into(user_data, user_state, user_config)

        # Admin key: genera + scrive in user_config
        admin_key = secrets.token_hex(32)
        (user_config / "admin.key").write_text(admin_key)
        os.chmod(user_config / "admin.key", 0o600)

        port = _free_port()

        # Inherited DB/path overrides can escape all three disposable roots.
        # Product configuration is supplied explicitly by the fixture hook.
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("METNOS_")}
        workspace = tmp_root / "workspace"
        workspace.mkdir()
        env.update({
            "METNOS_USER_DATA": str(user_data),
            "METNOS_USER_STATE": str(user_state),
            "METNOS_USER_CONFIG": str(user_config),
            "METNOS_WORKSPACE": str(workspace),
            "METNOS_INSTALL_ROOT": str(_REPO_ROOT),
            # The parent pytest process may itself use a sandbox lock path.
            # Each E2E server must own a distinct lock or parallel lifecycle
            # tests falsely look like a live-server collision.
            "METNOS_HTTP_LOCKFILE": str(user_state / "http_server.lock"),
            "METNOS_E2E": "1",  # toggle interno: niente cron pesanti
            "METNOS_RUNTIME_PROFILE": "e2e",
            # Fixtures must use the actual admission/bootstrap boundary.
            "METNOS_LOADER_VERIFY": "1",
            "METNOS_HTTP_DISABLE_BUILD_TASKS": "1",  # niente async build
            # Mai chiamare frontier (Anthropic/OpenAI a pagamento) nei test
            "METNOS_DISABLE_FRONTIER": "1",
            # Mock telegram backend: niente chiamate reali ai bot API,
            # niente dipendenza dal telegram-daemon (che non gira nel
            # server e2e tmp). send_messages via_channel=telegram ritorna
            # ok=True con message_id placeholder. Necessario per test che
            # esercitano send_messages con users.db seedato (Roberto ha
            # telegram verified in live, e2e deve simulare il send).
            "METNOS_TELEGRAM_MOCK": "1",
            # Evita lock files in giro
            "PYTHONUNBUFFERED": "1",
        })

        # Hide executors: esclude selettivamente executor handcrafted dal
        # catalog del server tmp. Use case: forzare il PLANNER a scegliere
        # uno skill imported quando esiste un builtin equivalente (test
        # `test_google_builtin_vs_imported`, `test_chat_google_skill`).
        # Universal: ANY executor name nella lista viene rimosso a load.
        if hide_executors:
            env["METNOS_HIDE_EXECUTORS"] = ",".join(hide_executors)

        # Pre-spawn hook (es. import skill prima del boot, cosi' catalog
        # vede gli executor importati al primo load)
        if pre_spawn_hook is not None:
            pre_spawn_hook(env, tmp_root)

        # Spawn
        log_file = tmp_root / "server.log"
        with log_file.open("w") as log_fp:
            proc = subprocess.Popen(
                [_PYTHON, "-u", "-m", "runtime.metnos_http_server",
                 "--host", host, "--port", str(port)],
                cwd=str(_REPO_ROOT), env=env, stdout=log_fp,
                stderr=subprocess.STDOUT, start_new_session=True,
            )
        try:
            if not _wait_ready(host, port, process=proc, timeout_s=ready_timeout_s):
                with log_file.open("rb") as stream:
                    stream.seek(max(0, os.fstat(stream.fileno()).st_size - 2000))
                    excerpt = stream.read(2000).decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"server not operational within {ready_timeout_s}s on {host}:{port}\n"
                    f"Last 2KB of server.log:\n{excerpt}"
                )
        except BaseException:
            _stop_process(proc)
            raise

        return cls(
            process=proc, port=port, admin_key=admin_key,
            tmp_root=tmp_root, user_data=user_data,
            user_state=user_state, user_config=user_config,
            host=host, runtime_env=dict(env),
        )

    def shutdown(self, *, timeout_s: float = 5.0, cleanup: bool = True) -> None:
        """SIGTERM + wait. Se cleanup, rimuove tmp_root."""
        _stop_process(self.process, timeout_s=timeout_s)
        if cleanup and os.environ.get("METNOS_E2E_KEEP_TMP") != "1":
            try:
                shutil.rmtree(self.tmp_root, ignore_errors=True)
            except Exception:
                pass

    def is_alive(self) -> bool:
        return self.process.poll() is None
