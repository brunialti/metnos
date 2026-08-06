"""Arrivare non e' mostrare (7/8/2026).

`act_sites` con un obiettivo NAVIGA e riporta «azione completata»; il contenuto
della pagina raggiunta lo legge `read_sites` — che e' anche il solo a redigere
lo screenshot. Il 7/8 il pilota e' arrivato davvero sulla pagina delle
prenotazioni, e il turno e' finito senza un dato: il piano non leggeva.

Simmetrica di `ensure_site_session_precursor`, che ricostruisce cio' che serve
PRIMA. Il confine: una richiesta puramente operativa non deve produrre una
lettura, e un piano che gia' legge non si tocca.
"""
from __future__ import annotations

from engine.dispatch import _ensure_site_goal_read
from engine.types import Framework, Intent, StepSpec


class _Exec:
    def __init__(self, name): self.name = name


_CATALOGO = [_Exec("open_sites"), _Exec("login_sites"), _Exec("act_sites"),
             _Exec("read_sites")]


def _piano(*passi):
    return Framework(steps=[StepSpec(tool=t, args=dict(a)) for t, a in passi])


def _tools(fw):
    return [s.tool for s in fw.steps]


def test_dopo_una_navigazione_a_obiettivo_il_piano_legge() -> None:
    fw = _ensure_site_goal_read(
        _piano(("open_sites", {"urls": ["https://x.test"]}),
               ("act_sites", {"action": "vai alle mie prenotazioni"}),
               ("final_answer", {})),
        Intent(verb="find", object="sites"), _CATALOGO)
    assert _tools(fw) == ["open_sites", "act_sites", "read_sites", "final_answer"]
    assert fw.steps[2].args["from_step"] == 2


def test_un_piano_che_gia_legge_non_si_tocca() -> None:
    prima = ("open_sites", {}), ("act_sites", {"action": "vai"}), ("read_sites", {"from_step": 2})
    fw = _ensure_site_goal_read(_piano(*prima), Intent(verb="read", object="sites"),
                                _CATALOGO)
    assert _tools(fw) == ["open_sites", "act_sites", "read_sites"]


def test_una_richiesta_operativa_non_produce_una_lettura() -> None:
    """«prenota», «cancella»: l'utente non ha chiesto di vedere niente."""
    fw = _ensure_site_goal_read(
        _piano(("open_sites", {}), ("act_sites", {"action": "prenota la camera"})),
        Intent(verb="create", object="sites"), _CATALOGO)
    assert _tools(fw) == ["open_sites", "act_sites"]


def test_senza_read_sites_in_catalogo_non_si_inventa_niente() -> None:
    fw = _ensure_site_goal_read(
        _piano(("open_sites", {}), ("act_sites", {"action": "vai alle prenotazioni"})),
        Intent(verb="find", object="sites"),
        [_Exec("open_sites"), _Exec("act_sites")])
    assert _tools(fw) == ["open_sites", "act_sites"]


def test_un_act_senza_obiettivo_non_richiede_lettura() -> None:
    """Un clic atomico (`primitive` senza goal) non e' una navigazione a fine."""
    fw = _ensure_site_goal_read(
        _piano(("open_sites", {}), ("act_sites", {"session_ids": ["s1"]})),
        Intent(verb="find", object="sites"), _CATALOGO)
    assert _tools(fw) == ["open_sites", "act_sites"]
