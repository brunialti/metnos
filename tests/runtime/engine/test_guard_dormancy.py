"""Le guardie dormienti si presentano da sole (6/8/2026).

Il ritiro di una guardia era un progetto: si apriva un'analisi, si misurava a
mano, e in due mesi ne era stata ritirata una sola. Con gli spari persistiti la
domanda «quali sono candidate?» e' una query, e la lista entra nel riepilogo
notturno accanto agli executor invecchiati.

Resta una LISTA DI CANDIDATE, mai un verdetto: zero spari puo' voler dire «non
serve piu'» oppure «il piano arriva sano proprio perche' c'e'», e nessun dato
qui le distingue. Percio' i tre requisiti — silenzio, massa e tempo — devono
valere tutti: senza, la lista si riempirebbe di guardie giovani o poco
attraversate e nessuno la guarderebbe piu'.
"""
from __future__ import annotations

import importlib
from pathlib import Path


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("METNOS_GUARD_STATS_DB", str(tmp_path / "guard_stats.db"))
    monkeypatch.setenv("METNOS_GUARD_STATS_FLUSH", "0")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    import engine.guard_stats as GS
    importlib.reload(GS)
    return GS


def _riga(GS, nome, *, fires, seen, first_seen, last_fire_at=None):
    from contextlib import closing
    with closing(GS._conn()) as c:
        c.execute("INSERT INTO guard_fire(name, fires, seen, first_seen, "
                  "last_fire_at) VALUES (?,?,?,?,?)",
                  (nome, fires, seen, first_seen, last_fire_at))
        c.commit()


def test_silenzio_massa_e_tempo_insieme(tmp_path, monkeypatch) -> None:
    GS = _fresh(tmp_path, monkeypatch)
    adesso = "2026-08-06T12:00:00Z"
    vecchio = "2026-01-01T12:00:00Z"
    _riga(GS, "muta_e_attraversata", fires=0, seen=5000, first_seen=vecchio)
    _riga(GS, "giovane", fires=0, seen=5000, first_seen="2026-08-01T12:00:00Z")
    _riga(GS, "poco_attraversata", fires=0, seen=3, first_seen=vecchio)
    _riga(GS, "spara_ancora", fires=7, seen=5000, first_seen=vecchio,
          last_fire_at="2026-08-05T12:00:00Z")

    nomi = [r["name"] for r in GS.dormant(now_iso=adesso)]
    assert nomi == ["muta_e_attraversata"]


def test_uno_sparo_vecchio_non_salva_la_guardia(tmp_path, monkeypatch) -> None:
    """Ha riparato qualcosa una volta, un anno fa: e' comunque una candidata —
    altrimenti un singolo sparo antico la renderebbe immortale."""
    GS = _fresh(tmp_path, monkeypatch)
    _riga(GS, "sparo_antico", fires=1, seen=9000,
          first_seen="2026-01-01T12:00:00Z",
          last_fire_at="2026-02-01T12:00:00Z")
    nomi = [r["name"] for r in GS.dormant(now_iso="2026-08-06T12:00:00Z")]
    assert nomi == ["sparo_antico"]


def test_la_lista_dice_quanto_e_stata_osservata(tmp_path, monkeypatch) -> None:
    GS = _fresh(tmp_path, monkeypatch)
    _riga(GS, "muta", fires=0, seen=800, first_seen="2026-05-08T12:00:00Z")
    riga = GS.dormant(now_iso="2026-08-06T12:00:00Z")[0]
    assert riga["osservata_da_giorni"] == 90
    assert riga["fires"] == 0 and riga["seen"] == 800


def test_un_db_illeggibile_non_fa_fallire_il_riepilogo(tmp_path, monkeypatch) -> None:
    GS = _fresh(tmp_path, monkeypatch)
    monkeypatch.setattr(GS, "DB_PATH", Path("/proc/non-leggibile/x.db"))
    assert GS.dormant() == []


def test_il_riepilogo_notturno_porta_la_sezione(tmp_path, monkeypatch) -> None:
    """La lista deve comparire DOVE si guarda ogni giorno: senza un posto, il
    ritiro resta un progetto che nessuno apre."""
    GS = _fresh(tmp_path, monkeypatch)
    _riga(GS, "guardia_muta", fires=0, seen=4000,
          first_seen="2026-01-01T12:00:00Z")
    import lifecycle_summary as LS
    importlib.reload(LS)
    riepilogo = {"guard_dormant": {
        "data": {"candidates": GS.dormant(now_iso="2026-08-06T12:00:00Z"),
                 "days": 60, "min_seen": 500},
        "note": None}}
    assert "guardia_muta" in LS.format_summary(riepilogo)


def test_senza_candidate_la_sezione_lo_dice(tmp_path, monkeypatch) -> None:
    """Silenzio ≠ sezione vuota: il riepilogo deve distinguere «nessuna
    candidata» da «non ho guardato» (§2.8)."""
    _fresh(tmp_path, monkeypatch)
    import lifecycle_summary as LS
    importlib.reload(LS)
    testo = LS.format_summary({"guard_dormant": {
        "data": {"candidates": [], "days": 60, "min_seen": 500}, "note": None}})
    assert "60" in testo and "500" in testo
