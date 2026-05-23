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


def ask_language() -> str:
    """Quick language prompt — restricted to en/it for now."""
    ui.console().print()
    ui.console().print("  Language / Lingua  ([cyan]en[/cyan], [cyan]it[/cyan])")
    return ui.choice("Choose / Scegli", ["en", "it"], default="en")


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
