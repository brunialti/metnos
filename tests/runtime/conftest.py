"""Infrastructure comune della suite runtime.

L'invariante principale e' *zero pollution*: HOME e tutte le radici mutabili
Metnos vengono reindirizzate a una sandbox di sessione **prima della
collection**.  I test possono quindi importare moduli con costanti risolte a
module-load senza mai osservare path di produzione.  La sandbox contiene una
snapshot read/write dei soli artefatti installati necessari ai test di
conformita' (cataloghi, testi i18n, configurazione non sensibile e chiavi
pubbliche); credenziali e chiavi private reali non vengono mai copiate.
"""
from __future__ import annotations

import os
import secrets
import shutil
import socket
import sys
import tempfile
from pathlib import Path

import pytest


_TESTS_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _TESTS_ROOT.parents[1]
_RUNTIME_ROOT = _REPO_ROOT / "runtime"

# Un solo punto per gli import della suite. Le aggiunte locali nei test legacy
# restano compatibili e possono essere rimosse progressivamente.
for _path in (str(_REPO_ROOT), str(_RUNTIME_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

_TEST_SESSION_ROOT: Path | None = None
_TEST_SESSION_ORIGINAL_ENV: dict[str, str | None] = {}
_TEST_OWNER_USER_ID = "pytest-runtime-owner"

_SESSION_ENV_PATHS = {
    "HOME": "home",
    "USERPROFILE": "home",
    "XDG_DATA_HOME": "home/.local/share",
    "XDG_STATE_HOME": "home/.local/state",
    "XDG_CONFIG_HOME": "home/.config",
    "METNOS_USER_DATA": "home/.local/share/metnos",
    "METNOS_USER_STATE": "home/.local/state/metnos",
    "METNOS_USER_CONFIG": "home/.config/metnos",
    "METNOS_WORKSPACE": "workspace",
    "METNOS_INDEX_ROOT": "home/.local/share/metnos/index",
    "METNOS_LOG_FILE": "home/.local/state/metnos/metnos.log",
    "METNOS_SCHEDULER_V2_DB": "home/.local/state/metnos/scheduler_v2.sqlite",
    "METNOS_HTTP_LOCKFILE": "home/.local/state/metnos/http_server.lock",
}

_SAFE_DATA_FILES = (
    "detection.sqlite",
    "executor_aliases.json",
    "i18n.sqlite",
    # Catalogo derivato e firmato, privo di dati personali. Copiarlo evita che
    # ogni suite HTTP ricompili migliaia di unità; il bootstrap lo rigenera
    # comunque se hash sorgente, schema o firma non sono più validi.
    "tutor_catalog.sqlite",
    "tutor_catalog.sqlite.sig",
    "tutor_catalog.last_good.json",
)
_SAFE_CONFIG_FILES = (
    "blocked_origins.json",
    "embedding_tiers.toml",
    "github_dedup.json",
    "github_watched_repos.json",
    "llm_tiers.toml",
    "owned_domains.json",
    "prefilter_scrub_names.json",
    "runtime.toml",
    "translator_tier.toml",
    "trusted_origins.json",
    "vlm_tiers.toml",
    "workspace_policy.toml",
)


@pytest.fixture(autouse=True)
def _isolate_installed_service_profile(monkeypatch, tmp_path):
    """Unit tests must not inspect the host's root-owned deployment chain."""
    import executor_birth_ownership_chain as ownership

    monkeypatch.setattr(
        ownership, "DEFAULT_OWNERSHIP_CHAIN_ROOT_V1", tmp_path / "ownership",
    )


def _copy_if_present(source: Path, destination: Path) -> None:
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix == ".sqlite":
            _copy_sqlite_complete(source, destination)
        else:
            shutil.copy2(source, destination)


def _copy_sqlite_complete(source: Path, destination: Path) -> None:
    """Copia un SQLite COMPRESO cio' che sta ancora nel registro di scrittura.

    In modalita' WAL una scrittura recente vive nel file `-wal` e non nel file
    principale finche' qualcuno non la travasa. Copiare il solo file
    principale produce quindi una fotografia VECCHIA, e in silenzio: i test
    girano su uno stato che non e' quello della macchina.

    E' costato mezz'ora il 17/8/2026 — un messaggio i18n riscritto risultava
    ancora nella versione precedente dentro la suite e in quella nuova fuori,
    con il test che diceva soltanto «disallineato».

    `backup` di sqlite3 fa la cosa giusta: legge attraverso il registro e
    scrive un file solo, coerente. Se il database non si apre (corrotto, o non
    e' davvero SQLite) si ricade sulla copia semplice: un file inutilizzabile
    e' un problema del test che lo usa, non di questa funzione.
    """
    import sqlite3
    try:
        sorgente = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        try:
            destinazione = sqlite3.connect(str(destination))
            try:
                sorgente.backup(destinazione)
            finally:
                destinazione.close()
        finally:
            sorgente.close()
    except sqlite3.Error:
        shutil.copy2(source, destination)


def _seed_test_installation(
        root: Path, *, source_home: Path, source_data: Path,
        source_config: Path) -> None:
    """Crea una snapshot minima dell'installazione, senza segreti reali."""
    test_data = root / _SESSION_ENV_PATHS["METNOS_USER_DATA"]
    test_config = root / _SESSION_ENV_PATHS["METNOS_USER_CONFIG"]
    test_data.mkdir(parents=True, exist_ok=True)
    test_config.mkdir(parents=True, exist_ok=True)

    for relative in ("executors", "skills"):
        source = source_data / relative
        if source.is_dir():
            shutil.copytree(source, test_data / relative, dirs_exist_ok=True)
    for name in _SAFE_DATA_FILES:
        _copy_if_present(source_data / name, test_data / name)
    for name in _SAFE_CONFIG_FILES:
        _copy_if_present(source_config / name, test_config / name)

    # I manifest installati devono restare verificabili, ma una chiave privata
    # di produzione non deve entrare nella sandbox. Le public key reali vengono
    # copiate con un nome distinto; author/synt sono keypair effimeri di test.
    source_keys = source_config / "keys"
    test_keys = test_config / "keys"
    test_keys.mkdir(parents=True, exist_ok=True)
    if source_keys.is_dir():
        for public in source_keys.glob("*_pub.bin"):
            _copy_if_present(public, test_keys / f"installed_{public.name}")

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    author_private = None
    for name in ("author", "synt"):
        private = Ed25519PrivateKey.generate()
        if name == "author":
            author_private = private
        private_path = test_keys / f"{name}_priv.bin"
        public_path = test_keys / f"{name}_pub.bin"
        private_path.write_bytes(private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        private_path.chmod(0o600)
        public_path.write_bytes(private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ))

    # The copied catalog was signed by the installation author.  Tests use a
    # fresh trust root, so re-sign the immutable snapshot with that ephemeral
    # author instead of copying the real private key.  A last-good envelope
    # carries the old signature and must not be offered as a test fallback.
    catalog = test_data / "tutor_catalog.sqlite"
    signature = test_data / "tutor_catalog.sqlite.sig"
    if catalog.is_file() and author_private is not None:
        signature.write_bytes(author_private.sign(catalog.read_bytes()))
    stale_backup = test_data / "tutor_catalog.last_good.json"
    if stale_backup.exists():
        stale_backup.unlink()

    # Le credenziali dei test devono usare lo stesso confine crittografico del
    # prodotto senza dipendere dalla admin.key reale. La chiave e' effimera e
    # vive soltanto nella sandbox di sessione.
    admin_key = test_config / "admin.key"
    admin_key.write_text(secrets.token_hex(32), encoding="utf-8")
    admin_key.chmod(0o600)

    # La home sorgente e' un parametro esplicito per rendere verificabile che
    # nessun Path.home() venga ricalcolato dopo il redirect.
    del source_home


def _activate_test_session(
        *, env=None, root: Path | None = None, seed: bool = False,
        source_home: Path | None = None, source_data: Path | None = None,
        source_config: Path | None = None) -> Path:
    """Redirect di tutte le radici mutabili, valido anche per test focused."""
    values = os.environ if env is None else env
    session_root = root or Path(tempfile.mkdtemp(prefix="metnos-pytest-"))
    if seed:
        if source_home is None or source_data is None or source_config is None:
            raise ValueError("seed source paths are required")
        _seed_test_installation(
            session_root,
            source_home=source_home,
            source_data=source_data,
            source_config=source_config,
        )
    for name, relative in _SESSION_ENV_PATHS.items():
        path = session_root / relative
        if name not in {"METNOS_LOG_FILE", "METNOS_SCHEDULER_V2_DB",
                        "METNOS_HTTP_LOCKFILE"}:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
        values[name] = str(path)
    return session_root


# Nome storico mantenuto come alias per consumer/test esterni.
_activate_full_suite_state = _activate_test_session


def _full_runtime_suite_requested(args, *, cwd: Path | None = None) -> bool:
    """True only for a directory-level runtime suite, not focused tests."""
    base = (cwd or Path.cwd()).resolve()
    for raw in args:
        value = str(raw)
        if not value or value.startswith("-") or "::" in value:
            continue
        try:
            target = Path(value)
            if not target.is_absolute():
                target = base / target
            if target.resolve() == _TESTS_ROOT:
                return True
        except (OSError, RuntimeError):
            continue
    return False


def _nearest_existing_parent(path: Path) -> Path | None:
    candidate = path.expanduser()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.exists() else None


def _full_suite_environment_issues(
        *, env=None, home: Path | None = None) -> list[str]:
    """Report infrastructure limits that would create false test failures."""
    values = os.environ if env is None else env
    user_home = (home or Path.home()).expanduser()
    defaults = {
        "METNOS_USER_DATA": user_home / ".local" / "share" / "metnos",
        "METNOS_USER_STATE": user_home / ".local" / "state" / "metnos",
        "METNOS_USER_CONFIG": user_home / ".config" / "metnos",
    }
    issues: list[str] = []
    read_only_flag = getattr(os, "ST_RDONLY", 1)
    for variable, default in defaults.items():
        target = Path(values.get(variable) or default)
        parent = _nearest_existing_parent(target)
        if parent is None:
            issues.append(f"{variable}: no existing parent for {target}")
            continue
        try:
            read_only = bool(os.statvfs(parent).f_flag & read_only_flag)
        except OSError as exc:
            issues.append(f"{variable}: cannot inspect {parent}: {exc}")
            continue
        if read_only or not os.access(parent, os.W_OK):
            issues.append(f"{variable}: read-only path {target}")

    probe = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
    except OSError as exc:
        issues.append(f"localhost sockets unavailable: {exc}")
    finally:
        if probe is not None:
            probe.close()
    return issues


def pytest_sessionstart(session) -> None:
    """Fail once as infrastructure error instead of emitting false reds.

    Focused pure tests remain runnable in restricted sandboxes.  The complete
    runtime suite, however, intentionally covers HTTP servers, sidecars and
    state/config persistence; without localhost bind and writable XDG roots it
    cannot produce a meaningful pass/fail verdict.
    """
    args = session.config.invocation_params.args
    if _full_runtime_suite_requested(args):
        issues = _full_suite_environment_issues()
        if issues:
            detail = "\n  - ".join(issues)
            pytest.exit(
                "METNOS_FULL_SUITE_ENVIRONMENT_UNAVAILABLE\n"
                "The full runtime suite requires writable temporary storage "
                "and localhost sockets. Rerun it in the authorized test "
                f"environment.\n  - {detail}",
                returncode=4,
            )

    source_home = Path.home()
    source_data = Path(os.environ.get("METNOS_USER_DATA") or
                       source_home / ".local/share/metnos")
    source_config = Path(os.environ.get("METNOS_USER_CONFIG") or
                         source_home / ".config/metnos")

    global _TEST_SESSION_ROOT, _TEST_SESSION_ORIGINAL_ENV
    _TEST_SESSION_ORIGINAL_ENV = {
        name: os.environ.get(name) for name in _SESSION_ENV_PATHS
    }
    _TEST_SESSION_ORIGINAL_ENV["PYTHONPATH"] = os.environ.get("PYTHONPATH")
    _TEST_SESSION_ORIGINAL_ENV["METNOS_OWNER_USER_ID"] = os.environ.get(
        "METNOS_OWNER_USER_ID")
    dependency_roots = []
    for raw_path in sys.path:
        path = Path(raw_path) if raw_path else None
        if (path is not None
                and path.name in {"site-packages", "dist-packages"}
                and path.is_dir()):
            resolved = str(path.resolve())
            if resolved not in dependency_roots:
                dependency_roots.append(resolved)
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = os.pathsep.join(
        part for part in (*dependency_roots, existing_pythonpath) if part
    )
    _TEST_SESSION_ROOT = _activate_test_session(
        seed=True,
        source_home=source_home,
        source_data=source_data,
        source_config=source_config,
    )
    # Ogni runtime test rappresenta una sessione già autenticata. L'owner
    # effimero conserva il confine multiutente nei test diretti di executor e
    # nei subprocess, senza consentire fallback da actor/sender nel prodotto.
    os.environ["METNOS_OWNER_USER_ID"] = _TEST_OWNER_USER_ID


def pytest_sessionfinish(session, exitstatus) -> None:
    """Restore the caller environment and remove suite-owned state only."""
    del session, exitstatus
    global _TEST_SESSION_ROOT, _TEST_SESSION_ORIGINAL_ENV
    if _TEST_SESSION_ROOT is None:
        return
    for name, original in _TEST_SESSION_ORIGINAL_ENV.items():
        if original is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = original
    shutil.rmtree(_TEST_SESSION_ROOT, ignore_errors=True)
    _TEST_SESSION_ROOT = None
    _TEST_SESSION_ORIGINAL_ENV = {}


# Test file che esercitano TurnLog.write() (finalizer honesty/gate/undo):
# write() persiste in agent_runtime.TURN_LOG_DIR, BOUND a module-load →
# l'env-redirect non basta (config già importato). Fixture sotto: setattr
# sul modulo → i turni di test NON finiscono nel jsonl di PRODUZIONE
# (bug live 6/7 sera: righe spurie «q»/«query plain» nel turn log reale,
# scambiate per turni utente falliti).
_TURNLOG_WRITING_TESTS = frozenset({
    "test_degenerate_final_honesty.py",
    "test_recovery_wrong_type_dir.py",
    "test_mass_mutation_gate.py",
    "test_late_result_a0.py",
    "test_zero_entries_final.py",
    "test_finalizer_unico.py",
    # run_turn completi con query fittizie («query plain», «trova foto
    # simili»): senza isolamento finiscono nel jsonl di prod (visti 23:18).
    "test_engine_seed_uploads.py",
    "test_http_multipart_uploads.py",
    "test_proposer_cap_demote.py",
})


@pytest.fixture(scope="session")
def standard_catalog():
    """Catalogo builtin immutabile condiviso dai test di routing naturali."""
    from loader import Catalog, _load_dir_into_catalog

    value = Catalog()
    _load_dir_into_catalog(
        _REPO_ROOT / "executors",
        value,
        verify=False,
        is_synthesized=False,
    )
    return tuple(value.executors.values())


@pytest.fixture(autouse=True)
def _isolate_turnlog_dir(request, tmp_path, monkeypatch):
    """I test in _TURNLOG_WRITING_TESTS scrivono i TurnLog in tmp, mai nel
    turns/ di produzione. setattr (non env): TURN_LOG_DIR è già risolto."""
    test_file = Path(request.node.fspath).name
    if test_file not in _TURNLOG_WRITING_TESTS:
        yield
        return
    try:
        import sys as _sys
        _rt = str(_RUNTIME_ROOT)
        if _rt not in _sys.path:
            _sys.path.insert(0, _rt)
        import agent_runtime as _ar
        monkeypatch.setattr(_ar, "TURN_LOG_DIR", tmp_path / "turns",
                            raising=True)
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _evict_test_born_embedders():
    """`virt._cache` e' un singleton di PROCESSO: un test che sostituisce il
    costruttore dell'incastonatore (`mock.patch`) lascia dentro la cache
    l'oggetto finto, che sopravvive alla fine del `with` e contamina in
    ordine chiunque venga dopo (fallimento tipico: `'_StubBGE' object has no
    attribute 'embed_query'` nei test del Tutor).

    Regola generale (§7.3), non elenco di test: alla fine di ogni test la
    cache non conserva oggetti NATI DENTRO i test — finti, doppioni o stub.
    Gli incastonatori veri restano caldi, quindi nessun ricaricamento di
    modello aggiuntivo."""
    yield
    try:
        import virt
    except Exception:
        return
    for key, value in list(getattr(virt, "_cache", {}).items()):
        origin = getattr(type(value), "__module__", "") or ""
        if origin.startswith("test") or origin.startswith("unittest.mock"):
            virt._cache.pop(key, None)


@pytest.fixture(autouse=True)
def _isolate_sites_cooldown_db(tmp_path, monkeypatch):
    """ADR 0191 P6: il cooldown sites NON deve scrivere nello state dir reale
    durante i test (`config.PATH_USER_STATE` e' risolto a import-time, quindi la
    fixture HOME non lo copre). Isola il DB per-test → nessuna pollution ne'
    interferenza cross-test."""
    monkeypatch.setenv("METNOS_SITES_COOLDOWN_DB",
                       str(tmp_path / "sites_cooldown.sqlite"))
    yield
