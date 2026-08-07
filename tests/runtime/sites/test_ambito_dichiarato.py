"""The declared scope wins; possession is only the fallback (2026-08-08).

First field of the structured goal, decided by the owner: flat, optional, an
enum the grammar can bind at production time, with the old heuristic left
underneath as the fallback — the same shape as `done_when`, which worked.

Why it exists: the pilot decides whether to open the personal area of a site by
looking for a possession marker in the goal text ("le MIE prenotazioni"). That
is the most fragile signal in the resolver. A language expresses possession in
many ways, the goal reducer can strip it, and a perfectly ordinary request —
"show me the bookings" — carries none. Where the marker is missing the heuristic
cannot succeed, and the pilot searches the home page for something that only
exists behind the account menu.
"""
from __future__ import annotations

import pytest

from playwright_sidecar import action_resolver as ar

_SENZA_POSSESSO = "le prenotazioni"
_CON_POSSESSO = "le mie prenotazioni"


def test_lo_scope_dichiarato_vince_sull_euristica() -> None:
    assert ar.goal_is_personal(_SENZA_POSSESSO, "personale") is True
    assert ar.goal_is_personal(_CON_POSSESSO, "pubblico") is False


def test_senza_dichiarazione_resta_il_possesso() -> None:
    assert ar.goal_is_personal(_CON_POSSESSO) is True
    assert ar.goal_is_personal(_SENZA_POSSESSO) is False


@pytest.mark.parametrize("valore", ["", "   ", "account", "personal", None])
def test_un_valore_fuori_enum_non_governa(valore) -> None:
    """Fail-soft: anything outside the closed enum leaves the fallback in
    charge, instead of silently meaning 'public'."""
    assert ar.goal_is_personal(_CON_POSSESSO, valore or "") is True


def test_la_home_non_soddisfa_un_fine_personale_dichiarato() -> None:
    """The guard that used to depend on the possession marker.

    Without the field, "le prenotazioni" on a home page full of the word
    would look satisfied and the pilot would stop there.
    """
    testo = "Benvenuto\nLe tue prenotazioni recenti\nRimini"
    url = "https://esempio.test/index.html"
    assert ar.page_satisfies_goal(_SENZA_POSSESSO, testo, scope_text=url,
                                  scope="pubblico")
    assert not ar.page_satisfies_goal(_SENZA_POSSESSO, testo, scope_text=url,
                                      scope="personale")


def test_il_logo_non_e_un_passo_verso_l_area_personale() -> None:
    """`goal_candidate_is_admissible` used the same fragile marker."""
    logo = {"id": "logo", "tag": "a", "role": "link", "name": "Esempio",
            "href": "https://esempio.test/", "visible": True}
    assert ar.goal_candidate_is_admissible(_SENZA_POSSESSO, logo)
    assert not ar.goal_candidate_is_admissible(_SENZA_POSSESSO, logo,
                                               scope="personale")


def test_il_campo_e_dichiarato_nel_manifest_firmato() -> None:
    """A field the manifest does not declare is a field the planner never
    produces: the whole path would be dead code."""
    import tomllib
    from pathlib import Path

    radice = Path(__file__).resolve().parents[3]
    manifest = tomllib.loads(
        (radice / "executors" / "act_sites" / "manifest.toml").read_text())
    campo = manifest["args"]["properties"]["ambito"]
    assert campo["enum"] == list(ar.GOAL_SCOPES)
    for lingua in ("it", "en"):
        assert campo["description"][lingua].strip()
