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


# ── Costanti + apply del layer CONTEXT (fix adversarial #13: drop-in reale;
# prima erano hardcoded nel broker con apply=None) ──────────────────────────
import os as _os

_DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_DEFAULT_MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
)
# Init-script CONTEXT: normalizza SOLO tell secondari (window.chrome/permissions/
# languages). NON nasconde `navigator.webdriver` (lo copre il launch-arg LAUNCH).
_CONTEXT_JS = r"""
() => {
  try { if (!window.chrome) window.chrome = {runtime: {}}; } catch(e){}
  try {
    const orig = navigator.permissions && navigator.permissions.query;
    if (orig) navigator.permissions.query = (p) =>
      (p && p.name === 'notifications')
        ? Promise.resolve({state: Notification.permission})
        : orig.call(navigator.permissions, p);
  } catch(e){}
  try { if (!navigator.languages || !navigator.languages.length)
        Object.defineProperty(navigator, 'languages',
          {get: () => ['it-IT','it','en-US','en'], configurable: true}); }
  catch(e){}
}
"""


def _apply_ua_override(cfg: dict) -> None:
    cfg["kwargs"]["user_agent"] = _os.getenv("METNOS_SITES_USER_AGENT") or _DEFAULT_UA


def _apply_mobile_emulation(cfg: dict) -> None:
    cfg["kwargs"]["user_agent"] = (_os.getenv("METNOS_SITES_USER_AGENT")
                                   or _DEFAULT_MOBILE_UA)
    cfg["kwargs"].update({
        "viewport": {"width": 412, "height": 915},
        "device_scale_factor": 2.625, "is_mobile": True, "has_touch": True,
    })


def _apply_chrome_permissions_js(cfg: dict) -> None:
    cfg["init_scripts"].append(_CONTEXT_JS)


STEALTH_TECHNIQUES = [
    StealthTechnique("webdriver_launch_arg", LAUNCH,
                     _apply_webdriver_launch_arg, _on_any),
    StealthTechnique("ua_override", CONTEXT, _apply_ua_override, _on_any),
    StealthTechnique("mobile_emulation", CONTEXT, _apply_mobile_emulation, _on_opt),
    StealthTechnique("chrome_permissions_js", CONTEXT,
                     _apply_chrome_permissions_js, _on_any),
    # BEHAVIOR: query-based (il consumatore interroga `technique_enabled`) —
    # `_human_pause` in credential_injection. Anch'esso drop-in: una nuova
    # tecnica BEHAVIOR = una entry + il suo consumatore interroga il registro.
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


def _build_context_cfg(stealth: bool) -> dict:
    prof = _profile(stealth)
    cfg = {"kwargs": {}, "init_scripts": []}
    for t in STEALTH_TECHNIQUES:
        if t.layer == CONTEXT and t.apply is not None and t.enabled_when(prof):
            t.apply(cfg)
    return cfg


def context_kwargs(*, stealth: bool) -> dict:
    """kwargs di `new_context` dalle tecniche CONTEXT abilitate (UA, mobile…).
    Drop-in: una nuova tecnica CONTEXT che aggiunge un kwarg = una entry, senza
    toccare il broker."""
    return _build_context_cfg(stealth)["kwargs"]


def context_init_scripts(*, stealth: bool) -> list:
    """init-script da aggiungere al contesto dalle tecniche CONTEXT abilitate.
    Drop-in: una nuova tecnica CONTEXT che aggiunge un init-script = una entry."""
    return _build_context_cfg(stealth)["init_scripts"]
