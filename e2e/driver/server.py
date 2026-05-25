"""server.py — gestisce il lifecycle del server Metnos in subprocess
con storage isolato (no contaminazione di prod).

Pattern: ogni test session usa una tmp dir `e2e/tmp/<run_id>/`. Server
viene avviato con env `METNOS_USER_DATA` + `METNOS_USER_STATE` puntati
li'. Porta random libera.

API:
    server = E2EServer.spawn()                 # blocca finche' ready
    server.url                                  # http://127.0.0.1:<port>
    server.admin_key                            # 64-char hex
    server.user_data                            # Path tmp data
    server.user_state                           # Path tmp state
    server.shutdown()                           # SIGTERM + wait

Usage in pytest:
    @pytest_asyncio.fixture(scope="session")
    async def server():
        s = E2EServer.spawn()
        yield s
        s.shutdown()
"""
from __future__ import annotations

import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYTHON = sys.executable


# --- Seed realistic helper -------------------------------------------------

_LIVE_USER_DATA = Path.home() / ".local/share/metnos"
_LIVE_USER_STATE = Path.home() / ".local/state/metnos"
_LIVE_USER_CONFIG = Path.home() / ".config/metnos"


def _seed_i18n_baseline(user_data: Path) -> None:
    """Copia il DB i18n live in tmp come baseline.

    Le 1000+ chiavi `MSG_*`/`ERR_*` sono runtime-essentials (formatting
    health block, truncation notice, errori user-facing). Senza queste
    chiavi il runtime emette `<missing:KEY>` ovunque. Necessario per
    OGNI test, non solo con `seed_realistic=True`.

    Idempotente: se gia' presente non sovrascrive.
    """
    src = _LIVE_USER_DATA / "i18n.sqlite"
    if not src.exists():
        return
    dst = user_data / "i18n.sqlite"
    if dst.exists():
        return
    user_data.mkdir(parents=True, exist_ok=True)
    try:
        _copy_db_with_wal(src, dst)
    except (OSError, shutil.Error):
        pass


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
        "telos_proposals.jsonl", "turn_feedback.jsonl",
        "executor_aliases.json", "rejected_patterns.jsonl",
        "introvertiva", "scratchpad.db", "multi_tool_paths.sqlite",
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

    items_config = [
        "runtime.toml", "owned_domains.json", "blocked_origins.json",
        "trusted_origins.json", "mail.env", "github_watched_repos.json",
        "llm_tiers.toml",
    ]
    for name in items_config:
        src = _LIVE_USER_CONFIG / name
        if not src.exists():
            continue
        dst = user_config / name
        try:
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


def _wait_ready(host: str, port: int, timeout_s: float = 30.0) -> bool:
    """Polling TCP fino a connessione ok."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


@dataclass
class E2EServer:
    """Subprocess server isolato per E2E.

    Storage:
      - tmp_root: e2e/tmp/<run_id>/
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
        """Avvia server subprocess. Blocca fino a ready_timeout_s.

        seed_user_data: se passato, copia ricorsivamente la dir come
        baseline di USER_DATA prima dell'avvio.

        seed_realistic: se True, copia subset realistico dal sistema live
        (`~/.local/share/metnos/`): turns history, mnest.sqlite,
        multi_tool_paths, persons, executor_stats, telos_proposals.jsonl,
        turn_feedback.jsonl, credentials. Per simulazioni high-fidelity di
        funzioni introvertive (telos engine, introvertiva propose,
        multi_tool L2 promotion). Zero contaminazione del live (copy →
        tmp, tmp distrutto a teardown).

        pre_spawn_hook: callable `fn(env, tmp_root) -> None` invocato
        dopo seed e prima dello spawn. Use case: importare skill (subprocess
        CLI) per popolare _imports/ → catalog vede gli executor al boot.
        """
        run_id = uuid.uuid4().hex[:8]
        tmp_root = _REPO_ROOT / "e2e" / "tmp" / run_id
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

        # Baseline runtime: i18n.sqlite va SEMPRE seedato dal live, non
        # solo con seed_realistic=True. Il DB contiene 1000+ chiavi
        # `MSG_*`/`ERR_*` usate dal runtime per formatting user-facing
        # (health, truncation, errori). Senza baseline il runtime emette
        # `<missing:KEY>` cascading.
        _seed_i18n_baseline(user_data)

        # Seed realistic: snapshot subset live → tmp. Read-only di natura
        # (tmp viene cancellato). Permette test introvertivi/telos su
        # corpus vero senza contaminare esercizio.
        if seed_realistic:
            _seed_realistic_into(user_data, user_state, user_config)

        # Admin key: genera + scrive in user_config
        admin_key = secrets.token_hex(32)
        (user_config / "admin.key").write_text(admin_key)
        os.chmod(user_config / "admin.key", 0o600)

        port = _free_port()

        env = os.environ.copy()
        env.update({
            "METNOS_USER_DATA": str(user_data),
            "METNOS_USER_STATE": str(user_state),
            "METNOS_USER_CONFIG": str(user_config),
            "METNOS_E2E": "1",  # toggle interno: niente cron pesanti
            # Skill imported via CLI test usa `--no-sign` → loader scarta
            # silenziosamente per digest mismatch. Disable verify per E2E.
            "METNOS_LOADER_VERIFY": "0",
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
        log_fp = log_file.open("w")
        proc = subprocess.Popen(
            [_PYTHON, "-u", "-m", "runtime.metnos_http_server",
             "--host", host, "--port", str(port)],
            cwd=str(_REPO_ROOT),
            env=env,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        # Wait ready
        ok = _wait_ready(host, port, timeout_s=ready_timeout_s)
        if not ok:
            # Cleanup + error
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=3)
            except Exception:
                pass
            log_excerpt = log_file.read_text()[-2000:] if log_file.exists() else "(no log)"
            raise RuntimeError(
                f"server failed to start within {ready_timeout_s}s on {host}:{port}\n"
                f"--- last 2KB of server.log ---\n{log_excerpt}"
            )

        return cls(
            process=proc, port=port, admin_key=admin_key,
            tmp_root=tmp_root, user_data=user_data,
            user_state=user_state, user_config=user_config,
            host=host,
        )

    def shutdown(self, *, timeout_s: float = 5.0, cleanup: bool = True) -> None:
        """SIGTERM + wait. Se cleanup, rimuove tmp_root."""
        if self.process.poll() is None:
            try:
                self.process.send_signal(signal.SIGTERM)
                self.process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
            except Exception:
                pass
        if cleanup:
            try:
                shutil.rmtree(self.tmp_root, ignore_errors=True)
            except Exception:
                pass

    def is_alive(self) -> bool:
        return self.process.poll() is None
