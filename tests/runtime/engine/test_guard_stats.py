"""Gli spari delle guardie si contano, e restano (6/8/2026).

Il contatore c'era gia' ma viveva in un dizionario di processo ed era spento in
produzione: nessuno sapeva quali guardie sparassero ancora, quindi nessuna si
poteva ritirare, quindi il loro numero poteva solo salire. Persistere la misura
e' la condizione perche' esista un «meno uno».

Cio' che la misura NON dice resta scritto nel modulo: zero spari puo' voler
dire «non serve piu'» oppure «serve, e per questo il piano arriva sano». Il
ritiro richiede la quarantena osservata; qui c'e' il numero, non il verdetto.

DB isolato per test: mai toccare quello reale.
"""
from __future__ import annotations

import importlib
from pathlib import Path


def _fresh(tmp_path, monkeypatch, flush_every="0"):
    monkeypatch.setenv("METNOS_GUARD_STATS_DB", str(tmp_path / "guard_stats.db"))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("METNOS_GUARD_STATS_FLUSH", flush_every)
    import engine.guard_stats as GS
    importlib.reload(GS)
    return GS


def test_conta_spari_e_attraversamenti(tmp_path, monkeypatch):
    GS = _fresh(tmp_path, monkeypatch)
    GS.record("align_framework_objects", True)
    GS.record("align_framework_objects", False)
    GS.record("route_folder_size", False)
    GS.flush()
    per_nome = {r["name"]: r for r in GS.stats()}
    assert per_nome["align_framework_objects"]["fires"] == 1
    assert per_nome["align_framework_objects"]["seen"] == 2
    assert per_nome["route_folder_size"] == {
        "name": "route_folder_size", "fires": 0, "seen": 1,
        "first_seen": per_nome["route_folder_size"]["first_seen"],
        "last_fire_at": None}


def test_i_conteggi_si_sommano_fra_riversamenti(tmp_path, monkeypatch):
    """Una guardia che spara oggi e domani deve risultare con due spari, non
    con l'ultimo: il conteggio serve a decidere un ritiro nel tempo."""
    GS = _fresh(tmp_path, monkeypatch)
    GS.record("degenerate_find_to_list", True)
    GS.flush()
    GS.record("degenerate_find_to_list", True)
    GS.flush()
    assert GS.stats()[0]["fires"] == 2
    assert GS.stats()[0]["seen"] == 2


def test_stats_include_il_buffer_non_ancora_riversato(tmp_path, monkeypatch):
    GS = _fresh(tmp_path, monkeypatch)
    GS.record("fill_clause_args", True)
    assert {r["name"]: r["fires"] for r in GS.stats()} == {"fill_clause_args": 1}


def test_riversa_ogni_n_piani(tmp_path, monkeypatch):
    GS = _fresh(tmp_path, monkeypatch, flush_every="2")
    GS.record("coerce_args_to_schema", True)
    GS.end_plan()
    assert not Path(GS.DB_PATH).exists() or GS._SEEN            # non ancora
    GS.record("coerce_args_to_schema", True)
    GS.end_plan()
    assert not GS._SEEN                                          # riversato
    assert GS.stats()[0]["fires"] == 2


def test_un_db_illeggibile_non_fa_fallire_nulla(tmp_path, monkeypatch):
    """Una statistica non deve mai far fallire un turno (§2.8: mai dichiarare
    un esito che non corrisponde alla realta' — men che meno per un contatore)."""
    GS = _fresh(tmp_path, monkeypatch)
    monkeypatch.setattr(GS, "DB_PATH", Path("/proc/non-scrivibile/x.db"))
    GS.record("qualsiasi", True)
    GS.flush()          # non solleva
    assert GS.stats() == []


def test_la_suite_non_inquina_la_misura_di_esercizio(tmp_path, monkeypatch):
    """La suite attraversa la pipeline migliaia di volte con piani costruiti a
    mano. Sommarli al traffico reale falserebbe l'unica misura che serve a
    decidere un ritiro: misurato, una sola esecuzione aggiungeva 5621
    attraversamenti."""
    GS = _fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "qualche::test")
    GS.record("align_framework_objects", True)
    assert not GS._SEEN and GS.stats() == []


def test_un_replay_offline_non_inquina_la_misura(tmp_path, monkeypatch):
    """La suite non e' l'unico modo di attraversare la pipeline senza essere
    traffico vero: un bench o l'oracolo del corpus rigirano piani salvati con
    lo STESSO codice di un turno. Il 6/8 un vaglio ha scritto 1673
    attraversamenti finti nel contatore di produzione — l'unico dato su cui si
    decide un ritiro. Chi replaya lo deve dichiarare; qui si verifica che la
    dichiarazione serva davvero."""
    GS = _fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("METNOS_GUARD_STATS", "0")
    GS.record("align_framework_objects", True)
    GS.flush()
    assert not GS._SEEN and GS.stats() == []
