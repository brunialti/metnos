# SPDX-License-Identifier: AGPL-3.0-only
"""POC disclaimer — shown at first run, requires explicit typed acceptance.

After acceptance, a sentinel is dropped at
``$METNOS_STATE/install/disclaimer.accepted`` so subsequent re-runs of
the installer do not show it again. Pass ``--force-phase 0`` to re-show.

Wording is intentionally plain and explicit: Metnos is proof-of-concept
software that executes code on the user's behalf and can produce
unintended effects. Users accept the software as-is and agree to run
it in an adequately protected environment.

Bilingual (en/it). The user picks the language before the disclaimer
is shown.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
import time
from pathlib import Path

from . import ui


_ACCEPT_TOKEN = {
    "en": "i accept",
    "it": "accetto",
}


_TEXT = {
    "en": """\
[bold red]DISCLAIMER — please read carefully[/bold red]

Metnos is [bold]proof-of-concept[/bold] software released under the
AGPL-3.0 licence. It is offered [bold]AS IS[/bold], without warranty
of any kind, express or implied.

Despite the maintainer's best efforts, Metnos may behave in unexpected
ways. As an agentic system that executes code on your behalf, it can
produce effects that are unintended, destructive, or otherwise
dangerous: writing or deleting files, sending messages, calling
external APIs, charging your accounts.

By proceeding with this installation, you agree that:

  • You install and run Metnos at your own risk.
  • You will operate it inside an adequately protected environment
    (a sandboxed account, limited filesystem access, network
    filtering, recent backups, no production credentials).
  • You take responsibility for reviewing the agent's actions and
    for the consequences of every action it performs on your behalf.
  • You will not hold the maintainer or contributors liable for
    damage, data loss, or any unintended effect resulting from use.

Type [bold cyan]I accept[/bold cyan] exactly (case-insensitive) to
confirm and continue. Anything else aborts the installation.
""",
    "it": """\
[bold red]AVVERTENZA — leggere con attenzione[/bold red]

Metnos è software in stato di [bold]proof-of-concept[/bold] rilasciato
sotto licenza AGPL-3.0. Viene fornito [bold]COSÌ COM'È[/bold], senza
alcuna garanzia, esplicita o implicita.

Nonostante l'impegno del manutentore, Metnos può comportarsi in modo
inatteso. Trattandosi di un sistema agentico che esegue codice per
tuo conto, può produrre effetti non voluti, distruttivi o comunque
pericolosi: scrittura o eliminazione di file, invio di messaggi,
chiamate ad API esterne, addebiti sui tuoi account.

Procedendo con l'installazione accetti che:

  • Installi e utilizzi Metnos a tuo rischio.
  • Lo gestirai in un ambiente adeguatamente protetto (account
    sandboxed, accesso limitato al filesystem, filtraggio di rete,
    backup recenti, niente credenziali di produzione).
  • Ti assumi la responsabilità di rivedere le azioni dell'agente
    e le conseguenze di ogni operazione che eseguirà per tuo conto.
  • Non riterrai il manutentore o i contributori responsabili per
    danni, perdita di dati o effetti non voluti derivanti dall'uso.

Scrivi [bold cyan]Accetto[/bold cyan] esattamente (case-insensitive)
per confermare e proseguire. Qualunque altra risposta interrompe
l'installazione.
""",
}


def _sentinel() -> Path:
    base = os.environ.get("METNOS_USER_STATE") or str(Path.home() / ".local" / "state" / "metnos")
    d = Path(base) / "install"
    d.mkdir(parents=True, exist_ok=True)
    return d / "disclaimer.accepted"


def already_accepted() -> bool:
    return _sentinel().exists()


_TESTED_LOCALES = ("en", "it")


@dataclass(frozen=True, slots=True)
class LanguageSelection:
    instance_lang: str
    requested_lang: str | None
    localization_state: str


def _localization_notice(code: str) -> None:
    """Honest, bilingual notice for a NON-tested target language.

    Auto-localization (translating every prompt, message and tool
    description) is EXPERIMENTAL and may not work. We never let it block
    boot: the system runs in English now and attempts ``code`` in the
    background. §2.8 — no overpromising.
    """
    ui.console().print()
    ui.console().print(
        f"  [yellow]⚠ Automatic localization to '[bold]{code}[/bold]' is an "
        f"EXPERIMENTAL, UNTESTED feature — it may not work.[/yellow]")
    ui.console().print(
        "    • Metnos will try to translate its prompts, messages and tool\n"
        "      descriptions in the background. On local hardware this can\n"
        "      take [bold]~24 hours[/bold], and it may fail or be incomplete.\n"
        "    • Meanwhile (and if it fails) the interface stays in [bold]English[/bold].\n"
        "    • For a first install we [bold]recommend 'en' or 'it'[/bold] — both tested.")
    ui.console().print(
        f"  [dim]IT — La localizzazione automatica in '{code}' è SPERIMENTALE e non\n"
        "  testata: può non funzionare, gira in background (~24h) e nel frattempo\n"
        "  (o se fallisce) l'interfaccia resta in inglese. Per il primo install\n"
        "  consigliamo 'en' o 'it' (testate).[/dim]")


def ask_language() -> LanguageSelection:
    """Return one validated instance/target language selection.

    en (default) / it are tested. ``other`` accepts a structural BCP-47 tag:
    the target is queued while the operational instance remains in English.
    """
    ui.console().print()
    ui.console().print(
        "  Language / Lingua: [cyan]en[/cyan] (default), [cyan]it[/cyan], "
        "or [cyan]other[/cyan] (e.g. fr — experimental)")
    pick = ui.choice("Choose / Scegli", ["en", "it", "other"], default="en")
    if pick in _TESTED_LOCALES:
        return LanguageSelection(pick, None, "active")

    from runtime import config as runtime_config
    code = runtime_config.normalize_language_tag(
        ui.ask("BCP-47 language code (e.g. fr, pt-BR, zh-Hans)")
    )
    if not code or code in _TESTED_LOCALES:
        if code in _TESTED_LOCALES:
            return LanguageSelection(code, None, "active")
        ui.warn("Not a valid, unambiguous BCP-47 code — falling back to 'en'.")
        return LanguageSelection("en", None, "active")
    _localization_notice(code)
    if not ui.confirm(f"Proceed with experimental '{code}'? (English meanwhile)",
                      default=False):
        ui.info("Keeping 'en' (recommended).")
        return LanguageSelection("en", None, "active")
    return LanguageSelection("en", code, "bootstrap_english")


def show_and_confirm(selection: LanguageSelection) -> bool:
    """Print the disclaimer and atomically persist accepted language facts.

    Returns True if the user accepted, False otherwise. On True, the
    sentinel is written.
    """
    lang = selection.instance_lang
    ui.console().print()
    ui.console().print(_TEXT[lang], markup=True)
    expected = _ACCEPT_TOKEN[lang]

    raw = ui.ask("→").strip().lower()
    if raw != expected:
        return False

    # Persist
    payload = {
        "accepted_at": int(time.time()),
        "lang": lang,
        "requested_lang": selection.requested_lang,
        "localization_state": selection.localization_state,
        "agreement_token": expected,
    }
    from runtime import config as runtime_config
    runtime_config.write_private_text(
        _sentinel(), json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    return True


def read_language_selection() -> LanguageSelection | None:
    """Return the accepted instance/target selection, if structurally valid."""

    p = _sentinel()
    if not p.exists():
        return None
    try:
        value = json.loads(p.read_text(encoding="utf-8"))
        from runtime import config as runtime_config
        instance = runtime_config.normalize_language_tag(value.get("lang"))
        requested_raw = value.get("requested_lang")
        requested = (
            runtime_config.normalize_language_tag(requested_raw)
            if requested_raw is not None else None
        )
        state = value.get("localization_state", "active")
        selection = LanguageSelection(instance, requested, state)
        if (
            state == "active"
            and instance in _TESTED_LOCALES
            and requested is None
        ):
            return selection
        if (
            state == "bootstrap_english"
            and instance == "en"
            and requested
            and requested != instance
        ):
            return selection
    except (AttributeError, json.JSONDecodeError, OSError, TypeError):
        pass
    return None


def persist_localization_request(
    selection: LanguageSelection | None = None,
) -> bool:
    """Sign the accepted selection once the installation key exists."""

    selected = selection or read_language_selection()
    if selected is None:
        return False
    from runtime import config as runtime_config
    try:
        _request, changed = runtime_config.write_localization_request(
            instance_lang=selected.instance_lang,
            requested_lang=selected.requested_lang,
            state=selected.localization_state,
        )
        return changed
    except FileNotFoundError:
        # Fresh install: phase 3 creates the installation key, then retries.
        return False


def read_locale() -> str | None:
    """Return the locale the user accepted under, if any (for later phases)."""
    selection = read_language_selection()
    return selection.instance_lang if selection else None
