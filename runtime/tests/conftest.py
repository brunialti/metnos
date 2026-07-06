"""Conftest per runtime/tests — zero-pollution invariant (8/5/2026 notte).

Disciplina §7.9 / §8.5: i test legacy delle pipeline immagini/persons
seedavano direttamente sotto `Path.home() / ".local/share/metnos/index/..."`
e poi facevano `rmtree(idx_dir.parent.parent)`. Questo ha (a) sporcato lo
storage globale di produzione e (b) un'invocazione di `test_new_filter_args`
ha addirittura cancellato l'intera dir `~/.local/share/metnos/index/image/`,
distruggendo la corpora indicizzata da 67667 entries.

Fix: per i test file legacy elencati in `_POLLUTING_TESTS`, autouse fixture
che redirige `HOME` env a una tmp dir per-test. `Path.home()` su Linux
usa `os.path.expanduser("~")` che onora `$HOME` → tutte le scritture
legacy cadono nella tmp dir. Setta anche `METNOS_INDEX_ROOT` /
`METNOS_USER_DATA` espliciti per gli executor che li leggono direttamente.

NIENTE shim, niente Path.home monkeypatch (rumoroso e propaga oltre il
test). Solo env vars in monkeypatch (auto-restore al teardown).

I test E2E nuovi (`test_find_images_e2e_dryrun.py`) impostano gia' i loro
env vars; questa fixture e' compatibile (override per quei test, ma non
attiva perche' non in lista).
"""
from __future__ import annotations

from pathlib import Path

import pytest


# Test file basenames che scrivono direttamente sotto ~/.local/share/metnos/
# tramite seed helpers Path.home()-based o tramite executor che fanno
# `_index_dir(...).mkdir(parents=True)` interno. Allowlist verificata dal
# pre/post snapshot del 8/5/2026 notte. Mantenere allineato con l'output
# di:
#   for f in runtime/tests/test_*.py; do
#     PRE=$(ls ~/.local/share/metnos/index/image/ | wc -l)
#     pytest $f -q
#     POST=$(ls ~/.local/share/metnos/index/image/ | wc -l)
#     [ $POST -gt $PRE ] && echo "POLLUTES: $f"
#   done
_POLLUTING_TESTS = frozenset({
    "test_new_filter_args.py",
    "test_delete_images_indices.py",
    "test_find_images_indices_paths_filter.py",
    "test_find_images_indices_quality.py",
    "test_get_images_indices.py",
    "test_find_images_indices.py",
    "test_find_images_build_all_on_missing.py",
    "test_find_images_real_queries.py",
    # Aggiunti 8/5/2026 notte (post default-deny verifica):
    "test_create_images_indices_all_idx.py",
    "test_index_schema_upgrade.py",
    "test_multi_dir_paths_filter_dispatch.py",
    "test_create_images_indices.py",
})


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
        _rt = str(Path(__file__).resolve().parent.parent)
        if _rt not in _sys.path:
            _sys.path.insert(0, _rt)
        import agent_runtime as _ar
        monkeypatch.setattr(_ar, "TURN_LOG_DIR", tmp_path / "turns",
                            raising=True)
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _isolate_home_for_legacy_image_tests(request, tmp_path, monkeypatch):
    """AUTOUSE GLOBALE (8/5/2026 notte): ogni test in runtime/tests/ vede
    HOME=tmp_path/home. Default-deny per costruzione: impossibile per un
    test sporcare `~/.local/share/metnos/` di produzione.

    Triggered da incidente: 8/5/2026 il pattern legacy `_POLLUTING_TESTS`
    enumerativo (allowlist di file noti) non copriva test che NON fanno
    seed-and-rmtree ma chiamano executor che internamente fanno
    `_index_dir(...).mkdir(parents=True)` lasciando dirs vuote.
    Default-deny risolve alla radice.

    Opt-out via `_REAL_HOME_TESTS` (frozen set vuoto oggi).

    Effetto: `Path.home()` (= `os.path.expanduser('~')` su Linux, che onora
    `$HOME`) risolve a `fake_home` per la durata del test. Anche
    `METNOS_USER_DATA` e `METNOS_INDEX_ROOT` esplicitamente settati per
    gli executor moderni che leggono env vars direttamente.

    Composability: i test E2E (`test_find_images_e2e_dryrun.py`) settano i
    propri env vars dentro fixture interna; il loro override e' l'ultimo,
    vince. Questa autouse non rompe nulla.
    """
    test_file = Path(request.node.fspath).name
    if test_file not in _POLLUTING_TESTS:
        yield
        return

    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("METNOS_USER_DATA", str(fake_home / ".local" / "share" / "metnos"))
    monkeypatch.setenv(
        "METNOS_INDEX_ROOT",
        str(fake_home / ".local" / "share" / "metnos" / "index"),
    )
    yield
