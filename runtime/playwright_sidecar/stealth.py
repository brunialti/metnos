"""Registro tecniche stealth (ADR 0191 P1) — opt-in, DEFAULT OFF, estensibile.

Confine C2: lo stealth e' un REGISTRO di tecniche, non un blocco monolitico.
Aggiungere una tecnica futura = **una entry**, zero modifiche al cuore. Ogni
tecnica dichiara il LAYER a cui vive:

- ``LAUNCH``   : argomenti di lancio del browser — l'UNICO layer che nasconde
  davvero ``navigator.webdriver`` (verificato: init-JS inefficace). Vive nel
  browser stealth lanciato da ``server.py``.
- ``CONTEXT``  : configurazione del contesto per-sessione (override UA, init-JS).
  Applicato in ``session_broker._context_kwargs`` / ``op_open``.
- ``BEHAVIOR`` : ritardi "umani" nel flusso login. Applicato in
  ``credential_injection._human_pause``.

``enabled_when(profile)`` regge un profilo futuro (``off|basic|aggressive``)
senza refactor; oggi il profilo e' binario (``on``/``off``). MAI attive di
default: lo stealth e' esplicitamente anti-rilevamento, a rischio del
proprietario (ADR 0191), non «igiene».
"""
from __future__ import annotations

from collections import namedtuple

LAUNCH = "LAUNCH"
CONTEXT = "CONTEXT"
BEHAVIOR = "BEHAVIOR"

# name: identificatore stabile · layer · apply: callable del layer LAUNCH
# (per CONTEXT/BEHAVIOR l'applicazione vive nel modulo del layer, che interroga
# `technique_enabled`) · enabled_when(profile) -> bool
StealthTechnique = namedtuple(
    "StealthTechnique", "name layer apply enabled_when")


def _on_any(profile: str) -> bool:
    return profile == "on"


def _on_opt(profile: str) -> bool:
    # Riservato a profili futuri (es. emulazione mobile). OFF nel profilo binario.
    return False


def _apply_webdriver_launch_arg(launch_args: list) -> None:
    """LAUNCH: l'unico flag che disabilita il segnale AutomationControlled."""
    flag = "--disable-blink-features=AutomationControlled"
    if flag not in launch_args:
        launch_args.insert(0, flag)


STEALTH_TECHNIQUES = [
    StealthTechnique("webdriver_launch_arg", LAUNCH,
                     _apply_webdriver_launch_arg, _on_any),
    StealthTechnique("ua_override", CONTEXT, None, _on_any),
    StealthTechnique("mobile_emulation", CONTEXT, None, _on_opt),
    StealthTechnique("chrome_permissions_js", CONTEXT, None, _on_any),
    StealthTechnique("human_delays", BEHAVIOR, None, _on_any),
]


def _profile(stealth: bool) -> str:
    return "on" if stealth else "off"


def apply_launch_args(launch_args: list, *, stealth: bool = True) -> None:
    """Applica in-place le tecniche LAUNCH abilitate (idempotente, dedupe)."""
    prof = _profile(stealth)
    for t in STEALTH_TECHNIQUES:
        if t.layer == LAUNCH and t.apply is not None and t.enabled_when(prof):
            t.apply(launch_args)


def technique_enabled(name: str, *, stealth: bool) -> bool:
    """True se la tecnica `name` e' attiva per il profilo corrente.

    I layer CONTEXT/BEHAVIOR interrogano questo per decidere se applicarsi
    (es. `_context_kwargs` per `ua_override`, `_human_pause` per `human_delays`).
    """
    prof = _profile(stealth)
    for t in STEALTH_TECHNIQUES:
        if t.name == name:
            return bool(t.enabled_when(prof))
    return False
