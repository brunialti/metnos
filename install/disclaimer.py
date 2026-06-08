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
    base = os.environ.get("METNOS_STATE") or str(Path.home() / ".local" / "state" / "metnos")
    d = Path(base) / "install"
    d.mkdir(parents=True, exist_ok=True)
    return d / "disclaimer.accepted"


def already_accepted() -> bool:
    return _sentinel().exists()


_TESTED_LOCALES = ("en", "it")


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


def record_desired_locale(code: str) -> None:
    """Persist a non-tested target locale for the background localization job.

    Operational locale stays tested (en/it) so boot never breaks; this only
    records the user's aspiration so a future i18n job can pursue it.
    """
    base = os.environ.get("METNOS_STATE") or str(Path.home() / ".local" / "state" / "metnos")
    d = Path(base) / "i18n"
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / "desired_locale.json").write_text(
            json.dumps({"target": code, "requested_at": int(time.time()),
                        "status": "experimental-pending"}, indent=2))
    except OSError:
        pass


def ask_language() -> str:
    """Language prompt. Returns the OPERATIONAL locale (always tested: en/it).

    en (default) / it are tested. 'other' lets the user name any ISO 639-1
    code (e.g. fr): we record it as an experimental target and run in English
    meanwhile — picking an untranslated locale must never brick boot.
    """
    ui.console().print()
    ui.console().print(
        "  Language / Lingua: [cyan]en[/cyan] (default), [cyan]it[/cyan], "
        "or [cyan]other[/cyan] (e.g. fr — experimental)")
    pick = ui.choice("Choose / Scegli", ["en", "it", "other"], default="en")
    if pick in _TESTED_LOCALES:
        return pick

    # other → free-form ISO code, experimental
    code = ui.ask("ISO 639-1 code (e.g. fr, de, es)").strip().lower()
    if not (len(code) == 2 and code.isalpha()) or code in _TESTED_LOCALES:
        # invalid or actually a tested one → coerce sensibly
        if code in _TESTED_LOCALES:
            return code
        ui.warn("Not a valid 2-letter code — falling back to 'en'.")
        return "en"
    _localization_notice(code)
    if not ui.confirm(f"Proceed with experimental '{code}'? (English meanwhile)",
                      default=False):
        ui.info("Keeping 'en' (recommended).")
        return "en"
    record_desired_locale(code)
    ui.ok(f"Target '{code}' recorded (experimental). Running in English for now.")
    return "en"


def show_and_confirm(lang: str) -> bool:
    """Print disclaimer in ``lang``, demand exact typed acceptance.

    Returns True if the user accepted, False otherwise. On True, the
    sentinel is written.
    """
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
        "agreement_token": expected,
    }
    _sentinel().write_text(json.dumps(payload, indent=2))
    return True


def read_locale() -> str | None:
    """Return the locale the user accepted under, if any (for later phases)."""
    p = _sentinel()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("lang")
    except (json.JSONDecodeError, OSError):
        return None
