"""Registro tecniche stealth (ADR 0191 P1) — opt-in, DEFAULT OFF, estensibile.

Confine C2: lo stealth e' un REGISTRO di tecniche, non un blocco monolitico.
Aggiungere una tecnica futura = **una entry**, zero modifiche al cuore. Ogni
tecnica dichiara il LAYER a cui vive:

- ``LAUNCH``   : argomenti di lancio del browser — l'UNICO layer che nasconde
  davvero ``navigator.webdriver`` (verificato: init-JS inefficace). Vive nel
  browser stealth lanciato da ``server.py``.
- ``CONTEXT``  : configurazione del contesto per-sessione (override UA, init-JS).
  Applicato in ``session_broker._context_kwargs`` / ``op_open``.
- ``BEHAVIOR`` : preparazione e ritmo delle interazioni. Applicato sia al
  login sia alle azioni Sites generiche.
- ``SESSION``  : ciclo di vita della sessione. Non modifica il fingerprint;
  puo' riusare soltanto un contesto autenticato ancora vivo e compatibile.

Ogni tecnica ha una preferenza ``on|off`` indipendente, subordinata al master
``sites_stealth``. La selezione e' una tupla di nomi del registro, ordinata
secondo il registro e fissata all'apertura della sessione. MAI attive di
default: lo stealth e' esplicitamente anti-rilevamento, a rischio del
proprietario (ADR 0191), non «igiene».
"""
from __future__ import annotations

import asyncio
from collections import namedtuple

LAUNCH = "LAUNCH"
CONTEXT = "CONTEXT"
BEHAVIOR = "BEHAVIOR"
SESSION = "SESSION"

# name: identificatore stabile · preference_key: chiave user_prefs · label/help:
# chiavi i18n UI · layer · apply: callable del layer LAUNCH/CONTEXT. Per
# BEHAVIOR l'applicazione vive nel consumatore, che interroga `technique_enabled`.
StealthTechnique = namedtuple(
    "StealthTechnique",
    "name preference_key label_key help_key layer apply")


def _apply_webdriver_launch_arg(launch_args: list) -> None:
    """LAUNCH: l'unico flag che disabilita il segnale AutomationControlled."""
    flag = "--disable-blink-features=AutomationControlled"
    if flag not in launch_args:
        launch_args.insert(0, flag)


# ── Costanti + apply del layer CONTEXT (fix adversarial #13: drop-in reale;
# prima erano hardcoded nel broker con apply=None) ──────────────────────────
import os as _os

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


def _chromium_version(cfg: dict) -> str:
    raw = str(cfg.get("browser_version") or "").strip()
    return raw if raw and all(part.isdigit() for part in raw.split(".")) else ""


def _apply_ua_override(cfg: dict) -> None:
    override = _os.getenv("METNOS_SITES_USER_AGENT")
    version = _chromium_version(cfg)
    if override:
        cfg["kwargs"]["user_agent"] = override
    elif version:
        cfg["kwargs"]["user_agent"] = (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{version} Safari/537.36")


def _apply_mobile_emulation(cfg: dict) -> None:
    override = _os.getenv("METNOS_SITES_USER_AGENT")
    version = _chromium_version(cfg)
    if override:
        cfg["kwargs"]["user_agent"] = override
    elif version:
        cfg["kwargs"]["user_agent"] = (
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{version} Mobile Safari/537.36")
    cfg["kwargs"].update({
        "viewport": {"width": 412, "height": 915},
        "device_scale_factor": 2.625, "is_mobile": True, "has_touch": True,
    })


def _apply_context_coherence(cfg: dict) -> None:
    """Allinea viewport, screen e scala senza falsificare la UA nativa."""
    viewport = dict(cfg["kwargs"].get("viewport") or {
        "width": 1280, "height": 800,
    })
    cfg["kwargs"]["viewport"] = viewport
    cfg["kwargs"]["screen"] = {
        "width": int(viewport["width"]),
        "height": int(viewport["height"]),
    }
    cfg["kwargs"].setdefault("device_scale_factor", 1)


def _apply_chrome_permissions_js(cfg: dict) -> None:
    cfg["init_scripts"].append(_CONTEXT_JS)


STEALTH_TECHNIQUES = [
    StealthTechnique(
        "webdriver_launch_arg", "sites_stealth_webdriver",
        "MSG_SETTINGS_STEALTH_WEBDRIVER",
        "MSG_SETTINGS_STEALTH_WEBDRIVER_HELP",
        LAUNCH, _apply_webdriver_launch_arg),
    StealthTechnique(
        "ua_override", "sites_stealth_user_agent",
        "MSG_SETTINGS_STEALTH_USER_AGENT",
        "MSG_SETTINGS_STEALTH_USER_AGENT_HELP",
        CONTEXT, _apply_ua_override),
    StealthTechnique(
        "mobile_emulation", "sites_stealth_mobile",
        "MSG_SETTINGS_STEALTH_MOBILE",
        "MSG_SETTINGS_STEALTH_MOBILE_HELP",
        CONTEXT, _apply_mobile_emulation),
    StealthTechnique(
        "context_coherence", "sites_stealth_context_coherence",
        "MSG_SETTINGS_STEALTH_CONTEXT_COHERENCE",
        "MSG_SETTINGS_STEALTH_CONTEXT_COHERENCE_HELP",
        CONTEXT, _apply_context_coherence),
    StealthTechnique(
        "chrome_permissions_js", "sites_stealth_browser_apis",
        "MSG_SETTINGS_STEALTH_BROWSER_APIS",
        "MSG_SETTINGS_STEALTH_BROWSER_APIS_HELP",
        CONTEXT, _apply_chrome_permissions_js),
    # BEHAVIOR: query-based (il consumatore interroga `technique_enabled`) —
    # `_human_pause` in credential_injection. Anch'esso drop-in: una nuova
    # tecnica BEHAVIOR = una entry + il suo consumatore interroga il registro.
    StealthTechnique(
        "human_delays", "sites_stealth_human_delays",
        "MSG_SETTINGS_STEALTH_HUMAN_DELAYS",
        "MSG_SETTINGS_STEALTH_HUMAN_DELAYS_HELP",
        BEHAVIOR, None),
    StealthTechnique(
        "focus_events", "sites_stealth_focus_events",
        "MSG_SETTINGS_STEALTH_FOCUS_EVENTS",
        "MSG_SETTINGS_STEALTH_FOCUS_EVENTS_HELP",
        BEHAVIOR, None),
    StealthTechnique(
        "reuse_live_session", "sites_stealth_session_reuse",
        "MSG_SETTINGS_STEALTH_SESSION_REUSE",
        "MSG_SETTINGS_STEALTH_SESSION_REUSE_HELP",
        SESSION, None),
]


def preference_specs() -> tuple[dict, ...]:
    """Metadati UI/prefs derivati dal registro, senza una seconda SoT."""
    return tuple({
        "name": t.name,
        "preference_key": t.preference_key,
        "label_key": t.label_key,
        "help_key": t.help_key,
        "layer": t.layer,
    } for t in STEALTH_TECHNIQUES)


def normalize_selection(techniques) -> tuple[str, ...]:
    """Normalizza una selezione valida nell'ordine stabile del registro.

    Input non-lista e nomi ignoti sono esclusi. Il confine HTTP/executor usa
    `unknown_techniques` per rifiutarli; qui la normalizzazione resta pura e
    fail-closed per i consumatori interni.
    """
    if not isinstance(techniques, (list, tuple, set, frozenset)):
        return ()
    requested = {str(value) for value in techniques}
    return tuple(t.name for t in STEALTH_TECHNIQUES if t.name in requested)


def unknown_techniques(techniques) -> tuple[str, ...]:
    if not isinstance(techniques, (list, tuple, set, frozenset)):
        return ("<invalid-container>",) if techniques is not None else ()
    known = {t.name for t in STEALTH_TECHNIQUES}
    return tuple(sorted({str(value) for value in techniques} - known))


def launch_browser_required(techniques) -> bool:
    selected = set(normalize_selection(techniques))
    return any(t.layer == LAUNCH and t.name in selected
               for t in STEALTH_TECHNIQUES)


def apply_launch_args(launch_args: list, *, techniques) -> None:
    """Applica in-place le tecniche LAUNCH abilitate (idempotente, dedupe)."""
    selected = set(normalize_selection(techniques))
    for t in STEALTH_TECHNIQUES:
        if t.layer == LAUNCH and t.apply is not None and t.name in selected:
            t.apply(launch_args)


def technique_enabled(name: str, *, techniques) -> bool:
    """True se la tecnica `name` e' attiva nella selezione corrente.

    I layer CONTEXT/BEHAVIOR interrogano questo per decidere se applicarsi
    (es. `_context_kwargs` per `ua_override`, `_human_pause` per `human_delays`).
    """
    return name in normalize_selection(techniques)


def _build_context_cfg(techniques, *, browser_version: str = "") -> dict:
    selected = set(normalize_selection(techniques))
    cfg = {"kwargs": {}, "init_scripts": [],
           "browser_version": browser_version}
    for t in STEALTH_TECHNIQUES:
        if t.layer == CONTEXT and t.apply is not None and t.name in selected:
            t.apply(cfg)
    return cfg


def context_kwargs(*, techniques, browser_version: str = "") -> dict:
    """kwargs di `new_context` dalle tecniche CONTEXT abilitate (UA, mobile…).
    Drop-in: una nuova tecnica CONTEXT che aggiunge un kwarg = una entry, senza
    toccare il broker."""
    return _build_context_cfg(
        techniques, browser_version=browser_version)["kwargs"]


def context_init_scripts(*, techniques) -> list:
    """init-script da aggiungere al contesto dalle tecniche CONTEXT abilitate.
    Drop-in: una nuova tecnica CONTEXT che aggiunge un init-script = una entry."""
    return _build_context_cfg(techniques)["init_scripts"]


def _interaction_delay_ms() -> int:
    try:
        value = int(_os.getenv("METNOS_SITES_HUMAN_DELAY_MS", "400"))
    except (TypeError, ValueError):
        value = 400
    return max(0, min(value, 3000))


async def pause_before_interaction(page, *, techniques=()) -> None:
    """Applica il ritmo opt-in a qualunque interazione Sites."""
    if not technique_enabled("human_delays", techniques=techniques):
        return
    delay_ms = _interaction_delay_ms()
    if delay_ms <= 0:
        return
    try:
        if hasattr(page, "wait_for_timeout"):
            await page.wait_for_timeout(delay_ms)
        else:
            await asyncio.sleep(delay_ms / 1000)
    except Exception:
        pass


async def prepare_interaction(page, locator=None, *, techniques=()) -> None:
    """Prepara focus, visibilita' e hover prima di fill/click.

    La procedura e' best-effort e bounded: non sostituisce i controlli di
    actionability Playwright e non trasforma un target non valido in valido.
    """
    if not technique_enabled("focus_events", techniques=techniques):
        return
    try:
        if hasattr(page, "bring_to_front"):
            await page.bring_to_front()
    except Exception:
        pass
    if locator is None:
        return
    for method in ("scroll_into_view_if_needed", "hover", "focus"):
        callback = getattr(locator, method, None)
        if callback is None:
            continue
        try:
            await callback(timeout=1200)
        except Exception:
            pass
