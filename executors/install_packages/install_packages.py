#!/usr/bin/env python3
"""Install or remove software through the machine's own package manager.

Two phases, and the first one installs nothing (ADR 0209 D2). Phase one
resolves each requested package against the source catalogue and hands back a
card: name, version, download address and the source's own fingerprint —
with the ALGORITHM stated, because winget publishes SHA-256 and apt on Ubuntu
publishes SHA-512, and a digest labelled with the wrong algorithm is one
nobody can check. Phase two runs only after a person approved that card, and the
runtime hands back a consent token the planner never sees.

Why the card carries the fingerprint: without it a person approves a LABEL.
«Install 7-Zip» and «install this file, from this address, with this
fingerprint» are different acts of consent, and only the second one can be
checked afterwards.

Why the identity must resolve to exactly ONE package: something that merely
resembles what was asked for is the worst outcome here. People do name
programs rather than identifiers — «install LibreHardwareMonitor» arrives
that way, while the source files it as
`LibreHardwareMonitor.LibreHardwareMonitor` — so a bare name is looked up as
a name too, and accepted only when the catalogue answers with a single
candidate. Several candidates are listed back, not chosen: «python» matches
six, and picking one would be deciding for the person who asked. Whatever
resolves goes on the card with its address and fingerprint, so what gets
approved is the resolved identity, never the typed word.

Uninstalling is this same verb with `uninstall: true`, not a different one,
and it is NOT an undo: the machine changed in a way nobody can walk back.
The runtime turns an undo request into an explicit question (ADR 0209 D3).

Authority lives outside this file. Who may install where is decided at the
invocation choke-point and the consent gate — an executor that decides who
may call it is an executor you get around by calling it differently.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402


# A package identity, never a command line and never a pattern. `*` and `?`
# are absent from the class on purpose: a wildcard here reads «install
# whatever matches», which is not something a person can consent to.
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:@-]{0,127}$")

# Resolving a package against a cold source is slower than reading a local
# database, and an install slower still. Neither may hold a turn open forever.
_RESOLVE_TIMEOUT_S = 60
_INSTALL_TIMEOUT_S = 900

# Asking whether the elevated helper is there opens a channel and closes it.
# It must not be able to hold the card back: if it does not answer at once,
# the answer is no.
_HELPER_PROBE_TIMEOUT_S = 10
# Uno scaricamento, poi una finestra a cui una persona deve rispondere:
# il tempo lo detta lei, non la rete.
_HELPER_SETUP_TIMEOUT_S = 600

# How many packages one call may carry. Not a performance limit: a card the
# person cannot read through is a card they approve without reading.
_MAX_PACKAGES = 10


def _as_list(value):
    """A plural argument accepts one element too (CLAUDE.md §2.4)."""
    if value is None:
        items = []
    elif isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = [value]
    return [x for x in items if not (isinstance(x, str) and not x.strip())]


def _which(name):
    try:
        import shutil
        return shutil.which(name) or ""
    except (OSError, TypeError, ValueError):
        return ""


def _run(argv, timeout_s):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout_s, shell=False,
                              # Nobody is watching this terminal: a package
                              # manager that stops to ask would hang here.
                              stdin=subprocess.DEVNULL)
        return proc.returncode, (proc.stdout or ""), (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return None, "", "timeout"
    except (OSError, subprocess.SubprocessError):
        return None, "", "spawn_failed"


def _fail(error, code, error_class="invalid_input"):
    # `installed` is declared non-optional in the output schema, and it is the
    # field a reader checks first. Leaving it out of the failure shape made
    # every refusal answer «I don't know» to the one question that always has
    # an answer here: nothing was installed.
    return {"ok": False, "error": error, "error_class": error_class,
            "error_code": code, "installed": False,
            "ok_count": 0, "fail_count": 1,
            "results": [], "failed": [{"error": error,
                                       "error_class": error_class,
                                       "error_code": code}]}


# ── Resolving an identity on the source ───────────────────────────────
def _winget_show(package_id, tool_path):
    """What the source knows about this exact identifier, or None.

    Read by STRUCTURE, not by wording: `winget show` prints localized labels
    («URL del programma di installazione» in Italian), so the values are
    found by their shape — a URL is a URL, a SHA-256 is 64 hex characters —
    and the version by the one label winget does not translate away, its
    position on the first lines. Anything not found stays empty rather than
    becoming a guess.
    """
    rc, out, _err = _run([tool_path, "show", "--id", package_id, "--exact",
                          "--accept-source-agreements",
                          "--disable-interactivity"], _RESOLVE_TIMEOUT_S)
    if rc != 0 or not out.strip():
        return None
    url, digest, version, publisher = "", "", "", ""
    for line in out.splitlines():
        value = line.split(":", 1)[1].strip() if ":" in line else ""
        if not value:
            continue
        if not digest and re.fullmatch(r"[0-9a-fA-F]{64}", value):
            digest = value.lower()
        elif not url and value.startswith("https://") and "/" in value[8:]:
            # The installer URL is the first https value that points at a
            # file; publisher and licence URLs come later in the output.
            if re.search(r"\.(exe|msi|msix|appx|zip|nupkg)(\?|$)", value, re.I):
                url = value
        if not version and re.fullmatch(r"[0-9][0-9A-Za-z._+-]*", value):
            version = value
        if not publisher and re.fullmatch(r"[^:/]{2,60}", value) \
                and not re.match(r"^[0-9]", value):
            publisher = value
    return {"package_id": package_id, "version": version, "url": url,
            "digest": digest, "digest_algo": "SHA-256" if digest else "",
            "publisher": publisher, "source": "winget",
            "dependencies": _winget_dependencies(out, digest)}


def _winget_dependencies(out, digest):
    """Gli altri pacchetti che verranno installati insieme a questo.

    Perche' e' una questione di consenso e non un dettaglio: la scheda diceva
    «1 programma» mentre il gestore ne avrebbe installati DUE, e la persona
    stava autorizzando anche il secondo senza averlo letto — che e' esattamente
    cio' che la scheda esiste per evitare (turni 234daad8 e b7d63070,
    17/8/2026).

    Letta per STRUTTURA, non per etichetta: le intestazioni di winget sono
    tradotte, e cercare «Dipendenze» funzionerebbe soltanto in italiano.
    L'ancora e' l'impronta, che si riconosce dalla forma (64 cifre
    esadecimali): le voci del blocco delle dipendenze vengono DOPO, mentre le
    parole chiave del pacchetto vengono prima. Una riga di dipendenza porta un
    identificativo nudo — niente due punti, quindi non e' un campo — nella
    forma `Editore.Pacchetto`.
    """
    righe = out.splitlines()
    if not digest:
        return []
    ancora = next((i for i, l in enumerate(righe) if digest in l.lower()), -1)
    if ancora < 0:
        return []
    trovate = []
    for riga in righe[ancora + 1:]:
        valore = riga.strip()
        if not valore or ":" in riga:
            continue
        if "." in valore and _ID_RE.match(valore) and valore not in trovate:
            trovate.append(valore)
    return trovate[:10]


def _apt_show(package_id, tool_path):
    """What apt knows about this package. `apt-get download --print-uris`
    gives the address and the checksum without downloading anything."""
    rc, out, _err = _run([tool_path, "download", "--print-uris", package_id],
                         _RESOLVE_TIMEOUT_S)
    if rc != 0 or not out.strip():
        return None
    line = out.strip().splitlines()[0]
    url = ""
    m = re.search(r"'([^']+)'", line)
    if m:
        url = m.group(1)
    # apt states the digest with its algorithm: today SHA512 on Debian and
    # Ubuntu, SHA256 elsewhere and in older archives. The card must say WHICH
    # algorithm, because «SHA-256: <a sha512>» is a fingerprint nobody can
    # check — worse than no fingerprint, because it looks checkable.
    digest, digest_algo = "", ""
    m = re.search(r"\b(SHA512|SHA256|SHA1|MD5):([0-9a-fA-F]{32,128})", line)
    if m:
        digest_algo, digest = m.group(1).upper(), m.group(2).lower()
    version = ""
    m = re.search(r"_([0-9][^_]*)_", line)
    if m:
        # Il pezzo arriva da un URL, dove `+` e' scritto `%2b`: mostrato cosi'
        # sulla scheda, «3.03%2bdfsg2-8» non e' la versione che la persona
        # trovera' scritta da nessun'altra parte.
        version = urllib.parse.unquote(m.group(1))
    return {"package_id": package_id, "version": version, "url": url,
            "digest": digest, "digest_algo": digest_algo or "SHA-256",
            "publisher": "", "source": "apt"}


_AMBIGUOUS = "ambiguous"


def _winget_resolve_name(name, tool_path):
    """L'identificativo che corrisponde a questo NOME, o un verdetto.

    Le persone nominano programmi: «installa LibreHardwareMonitor» arriva
    cosi', mentre la sorgente lo archivia come
    `LibreHardwareMonitor.LibreHardwareMonitor`. Rifiutare quel nome
    significa non saper installare cio' che l'utente ha chiesto per nome,
    che e' il modo normale di chiederlo (turno 09f7a5fb, 17/8/2026).

    La regola che tiene insieme le due esigenze: si cerca per nome ESATTO e
    si accetta soltanto un candidato UNICO. Un nome con piu' corrispondenze
    non si indovina — «python» ne trova sei — e chi decide non e' questo
    codice. L'identita' risolta finisce comunque sulla scheda: la persona
    approva quella, con indirizzo e impronta, non il nome che ha digitato.

    Ritorna: l'identificativo, `_AMBIGUOUS` con la lista, oppure None.
    """
    rc, out, _err = _run([tool_path, "search", "--name", name, "--exact",
                          "--accept-source-agreements",
                          "--disable-interactivity"], _RESOLVE_TIMEOUT_S)
    if rc != 0:
        return None, []
    rows = _winget_rows(out, name)
    if not rows:
        return None, []
    if len(rows) > 1:
        return _AMBIGUOUS, [r[1] for r in rows[:8]]
    return rows[0][1], []


def _winget_rows(out, term):
    """Righe della tabella che nominano il termine, come (nome, id, versione).

    Stessa lettura per geometria gia' usata da `find_packages`: le colonne
    hanno larghezza fissa e a colonna piena resta UN solo spazio, quindi la
    riga non si spezza sui gruppi di spazi. Le intestazioni sono tradotte,
    le loro POSIZIONI no.
    """
    lines = out.splitlines()
    dashes = next((i for i, ln in enumerate(lines)
                   if ln.strip() and set(ln.strip()) <= {"-", "─"}), -1)
    if dashes < 1:
        return []
    starts = [m.start(1) for m in re.finditer(r"(?:^|\s{2,})(\S)",
                                              lines[dashes - 1])]
    if len(starts) < 2:
        return []
    bounds = list(zip(starts, starts[1:] + [None]))
    rows = []
    low = term.lower()
    for line in lines[dashes + 1:]:
        if not line.strip() or low not in line.lower():
            continue
        cols = [line[a:b].strip() for a, b in bounds]
        if len(cols) > 1 and cols[0] and cols[1]:
            rows.append((cols[0], cols[1], cols[2] if len(cols) > 2 else ""))
    return rows


def _machine_name():
    """Il nome di QUESTA macchina, chiesto alla macchina stessa.

    La scheda deve dire su quale computer si sta per installare. Il runtime
    conosce la destinazione, ma la annota sul risultato DOPO l'esecuzione:
    l'executor non la riceve. Gira pero' proprio li', quindi il nome lo sa
    per conto suo, ed e' il fatto piu' diretto che possa riportare.
    """
    import socket
    try:
        return socket.gethostname() or ""
    except OSError:
        return ""


def _helper_call(*argv, timeout):
    """Ask the elevated Windows helper, through the Metnos client (ADR 0210 D).

    The client is the one that opens the channel, because opening it is not
    the hard part: the hard part is establishing WHO is on the other end
    before sending anything, and that judgment is written once, in Rust. A
    second copy here would be a second copy of a security check, and the
    second copy is the one that drifts.

    Returns the helper's answer as a dictionary, or None when the helper
    cannot be reached at all — which is not the same as a refusal, and the
    caller keeps them apart.
    """
    exe = os.environ.get("METNOS_CLIENT_EXE") or ""
    if not exe or not sys.platform.startswith("win"):
        return None
    try:
        proc = subprocess.run([exe, "helper", *argv], capture_output=True,
                              text=True, timeout=timeout, shell=False,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    # The answer is the LAST line: the client also writes its own log to
    # standard output, and a line of log ahead of the answer must not be
    # mistaken for a malformed answer.
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                return None
    return None


# L'ultimo motivo per cui l'aiutante non ha risposto. Una lista di un
# elemento perche' `_helper_present` viene chiamata da `_context`, che non ha
# dove metterlo: e' diagnostica, non stato.
_MOTIVO_AIUTANTE = [""]


def _helper_present():
    """Is the genuine elevated helper on this machine, right now?

    Asked before the card is built: the question is whether «for every user»
    is a choice this machine can honour. Offering it without knowing would
    mean letting a person choose something that then fails.

    It asks the one question that changes nothing — «who are you» — which is
    also the only way to learn whether the two programs still understand each
    other. It goes through the same checks as any other request, and leaves
    the same line in the helper's own log: a privileged component with a
    quiet side door would have a side door.

    Two things can be wrong and they are not the same thing. The helper may
    not be there, or it may be there speaking a language this client no
    longer speaks — two programs installed at different moments drift apart.
    The second is repairable by bringing the current one; the first is too,
    by the same road. What must never happen is sending a request to a
    component that cannot understand it and reading the silence as a fault.
    """
    answer = _helper_call("check", timeout=_HELPER_PROBE_TIMEOUT_S)
    if not answer or not answer.get("ok"):
        # PERCHE' non risponde. Il client lo sa — «non e' il sistema», «non e'
        # l'eseguibile che mi aspetto», «il canale non si apre» sono guasti
        # diversi che si riparano in posti diversi — e fin qui questa funzione
        # riduceva tutto a un «no» senza appiglio. E' la stessa forma di
        # difetto incontrata tre volte il 19/8/2026: il dato c'e' e non arriva
        # a chi deve agire.
        _MOTIVO_AIUTANTE[0] = " · ".join(
            str(answer.get(k)) for k in ("error_code", "detail")
            if answer and answer.get(k)) if answer else "nessuna risposta"
        return False
    _MOTIVO_AIUTANTE[0] = ""
    # «Allineato» lo decide il client, che le due lingue le conosce
    # entrambe. Una risposta senza quel campo viene da un client vecchio:
    # non e' una prova di allineamento, quindi non si assume.
    return bool(answer.get("aligned"))


def _apply_via_helper(package_id, uninstall):
    """Hand one operation to the elevated helper and report what happened.

    The helper accepts three typed operations on a package identity, never a
    command line: what comes back is its own verdict, and it travels here as
    it is (§2.8).
    """
    verb = "uninstall" if uninstall else "install"
    answer = _helper_call(verb, "--package-id", package_id,
                          timeout=_INSTALL_TIMEOUT_S)
    if answer is None:
        return {"package_id": package_id, "ok": False,
                "action": verb, "via": "helper",
                "error": _msg("ERR_PACKAGES_OPERATION_FAILED",
                              package=package_id, detail="helper_unreachable"),
                "error_class": "capability_missing",
                "error_code": "helper_unreachable"}
    if answer.get("ok"):
        return {"package_id": package_id, "ok": True, "action": verb,
                "source": "winget", "via": "helper"}
    codice = str(answer.get("error_code") or "helper_refused")
    dettaglio = str(answer.get("detail") or "")
    uscita = answer.get("exit_code")
    if uscita is not None:
        dettaglio = f"{dettaglio} [rc={uscita}]" if dettaglio else f"rc={uscita}"
    return {"package_id": package_id, "ok": False, "action": verb,
            "via": "helper",
            "error": _msg("ERR_PACKAGES_OPERATION_FAILED", package=package_id,
                          detail=(f"{codice} · {dettaglio}" if dettaglio
                                  else codice)[:300]),
            "error_class": "resource_unavailable",
            "error_code": codice,
            "exit_code": uscita}


def _is_elevated():
    """Whether THIS process already has the privileges to install for everyone.

    On Windows the Metnos client runs unprivileged by design (ADR 0210), so
    this is normally false there — and machine-wide installs travel through
    the helper instead (`_helper_present`). On Linux it is the root user, and
    there is no helper: the answer here is the whole answer.
    """
    if sys.platform.startswith("win"):
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001 — assenza di prova = niente privilegi
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def _context():
    """What this machine can actually do, asked of the machine itself.

    Machine-wide scope has two roads and they are kept apart. `elevated` is
    this process already having the privileges; `helper` is the elevated
    component being there to be asked (ADR 0210 D). They stay two keys and
    not one because the second road runs somewhere else, and a failure on it
    means something different. What the card needs — whether to offer «for
    every user» at all — is the two together, and that is asked once, in
    `_machine_scope_possible`.
    """
    is_windows = sys.platform.startswith("win")
    winget = _which("winget") if is_windows else ""
    apt = "" if is_windows else (_which("apt-get") or "")
    elevated = _is_elevated()
    # Asked only when it could matter: there is no helper without winget, and
    # none at all outside Windows.
    helper = bool(winget) and not elevated and _helper_present()
    return {"os": "windows" if is_windows else "linux",
            "winget": winget, "apt": apt,
            "manager": "winget" if winget else ("apt" if apt else ""),
            "machine": _machine_name(),
            "elevated": elevated,
            "helper": helper,
            "helper_reason": _MOTIVO_AIUTANTE[0] if not helper else ""}


# Which scopes each manager HAS. Not a preference: winget installs into the
# user's own profile or machine-wide, while apt only ever writes system
# directories — «just for me» is not a smaller version of the same act there,
# it does not exist. A manager added later declares its own row.
_MANAGER_SCOPES = {"winget": ("machine", "user"), "apt": ("machine",)}


def _helper_installable(ctx):
    """Whether the elevated helper could be BROUGHT here, not whether it is.

    Metnos installs its own privileged component the same way it installs
    anything else: through the signed channel, with the person consenting
    once. The alternative — someone copying a file and running it as
    administrator — would make the most privileged piece of the system the
    only one that arrives by hand.

    Windows only, and only where there is something for it to drive.
    """
    return (ctx.get("os") == "windows"
            and bool(ctx.get("winget"))
            and not ctx.get("elevated")
            and not ctx.get("helper"))


def _setup_helper():
    """Ask the client to fetch and install the helper. Windows asks once.

    Returns the client's answer, or None when the client could not be
    reached at all.
    """
    return _helper_call("setup", timeout=_HELPER_SETUP_TIMEOUT_S)


def _machine_scope_possible(ctx):
    """Whether «for every user» is a choice this machine can honour at all.

    Either road will do, and the card does not care which: a person chooses
    WHERE to install, not by what mechanism. The mechanism comes back in the
    result, where it helps whoever reads a failure.
    """
    return bool(ctx.get("elevated") or ctx.get("helper"))


def _scopes_available(ctx):
    """The scopes this machine can honour right now, widest first.

    Two limits meet here and neither is negotiable: what the manager HAS
    (`_MANAGER_SCOPES`) and what this process can REACH — its own privileges,
    or the elevated helper. Offering anything outside the intersection is
    offering a choice that will fail, which is the one thing a confirmation
    card must never do.

    An empty list is an answer too: on this machine, with this manager, from
    here, nothing can be installed at all.
    """
    supportate = _MANAGER_SCOPES.get(ctx.get("manager") or "", ())
    portate = []
    if "machine" in supportate and _machine_scope_possible(ctx):
        portate.append("machine")
    if "user" in supportate:
        portate.append("user")
    return portate


def _resolve(package_id, ctx):
    """Da cio' che l'utente ha scritto a un'identita' precisa, o niente.

    L'identificativo esatto viene per primo. Se manca, la stessa parola si
    prova come NOME: e' il modo in cui le persone chiamano i programmi. Un
    nome con piu' corrispondenze non viene scelto — torna ambiguo con la
    lista, e a decidere e' chi ha chiesto.

    Ritorna (dati, candidati_ambigui): esattamente uno dei due e' pieno.
    """
    if ctx["manager"] == "winget":
        found = _winget_show(package_id, ctx["winget"])
        if found is not None:
            return found, []
        resolved, candidates = _winget_resolve_name(package_id, ctx["winget"])
        if resolved == _AMBIGUOUS:
            return None, candidates
        if not resolved:
            return None, []
        found = _winget_show(resolved, ctx["winget"])
        if found is not None:
            # L'identita' risolta prende il posto del nome digitato: e'
            # quella che finisce sulla scheda, e quella che verra' installata.
            found["asked_as"] = package_id
        return found, []
    if ctx["manager"] == "apt":
        return _apt_show(package_id, ctx["apt"]), []
    return None, []


# ── Doing it ──────────────────────────────────────────────────────────
def _gia_come_lo_volevi(package_id, ctx, uninstall):
    """Il mondo e' gia' come lo si voleva?

    Installare qualcosa che c'e' gia', o togliere qualcosa che non c'e', non
    e' un fallimento: l'obiettivo e' raggiunto. Presentarlo come errore manda
    una persona a cercare un guasto che non esiste (Roberto, 19/8/2026).

    Si CHIEDE al gestore invece di riconoscere codici d'uscita: una tabella di
    codici andrebbe indovinata, e indovinare male qui significa dichiarare
    riuscita un'operazione fallita — l'errore peggiore possibile (§2.8). La
    domanda «c'e' o non c'e'» ha una risposta sola e non si presta a
    interpretazioni.
    """
    if ctx.get("manager") != "winget":
        return False
    rc, out, _ = _run([ctx["winget"], "list", "--id", package_id, "--exact",
                       "--accept-source-agreements", "--disable-interactivity"],
                      _RESOLVE_TIMEOUT_S)
    presente = rc == 0 and package_id.lower() in (out or "").lower()
    return (not presente) if uninstall else presente


def _apply(package_id, ctx, uninstall, scope):
    """Run the operation. The command line is built HERE, from the typed
    arguments — nothing the caller wrote reaches the manager as an option."""
    if ctx["manager"] == "winget":
        verb = "uninstall" if uninstall else "install"
        # Installing for every user is the one thing an unprivileged client
        # cannot do by itself. It goes to the elevated helper, which takes
        # three typed operations on a package identity and never a command
        # line: that boundary is the whole reason it may hold privileges at
        # all (ADR 0210 D1).
        if ctx.get("helper") and not uninstall and scope == "machine":
            return _apply_via_helper(package_id, uninstall=False)
        argv = [ctx["winget"], verb, "--id", package_id, "--exact",
                "--accept-source-agreements", "--disable-interactivity",
                "--silent"]
        if not uninstall:
            argv += ["--accept-package-agreements",
                     "--scope", "user" if scope == "user" else "machine"]
        rc, out, err = _run(argv, _INSTALL_TIMEOUT_S)
        if rc != 0 and uninstall and ctx.get("helper"):
            # Removing something that was installed for every user needs the
            # same reach that installing it needed. This is not a guess about
            # where the package lives: the unprivileged attempt has already
            # failed, and the helper is the only other road that could have
            # put it there. One retry, and its verdict is the final one —
            # whether it says yes or no.
            #
            # Returning it only on success meant that «the helper could not be
            # reached» and «you said no» were both replaced, on their way out,
            # by the ORIGINAL winget error: the person read «access denied»
            # and never learnt that the second road had been tried at all,
            # let alone why it failed (§2.8).
            return _apply_via_helper(package_id, uninstall=True)
    elif ctx["manager"] == "apt":
        verb = "remove" if uninstall else "install"
        argv = [ctx["apt"], verb, "-y", package_id]
        env_note = os.environ.copy()
        env_note["DEBIAN_FRONTEND"] = "noninteractive"
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=_INSTALL_TIMEOUT_S, shell=False,
                                  stdin=subprocess.DEVNULL, env=env_note)
            rc, out, err = proc.returncode, proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired:
            rc, out, err = None, "", "timeout"
        except (OSError, subprocess.SubprocessError):
            rc, out, err = None, "", "spawn_failed"
    else:
        return {"package_id": package_id, "ok": False,
                "error": _msg("ERR_PACKAGES_NO_MANAGER"),
                "error_class": "capability_missing",
                "error_code": "no_package_manager"}

    if rc == 0:
        return {"package_id": package_id, "ok": True,
                "action": "uninstall" if uninstall else "install",
                "source": ctx["manager"]}
    # La diagnosi sta in FONDO, non in cima. La prima riga di winget e'
    # l'insegna del pacchetto trovato («Trovato X [id] Versione 0.9.6»), che
    # mostrata come errore dice l'opposto di cio' che e' successo: sembra un
    # successo (§2.8). Il verdetto lo scrivono in fondo, tutti i gestori.
    #
    # Ultime righe, non parole-chiave: cercare «errore» o «failed»
    # funzionerebbe solo nelle lingue in cui qualcuno le ha scritte, e
    # l'uscita di winget e' localizzata come il resto.
    _righe = [ln.strip() for ln in ((err or "") + "\n" + (out or "")).splitlines()
              if ln.strip()]
    # La prima riga e' l'insegna («Trovato X [id] Versione ...»): dice cosa il
    # gestore ha TROVATO, non come e' andata. Si scarta quando c'e' dell'altro.
    _utili = _righe[1:] if len(_righe) > 1 else _righe
    detail = " · ".join(_utili[-3:]) if _utili else ""
    if rc is not None:
        # Il codice di uscita e' l'unico dato non localizzato, ed e' quello
        # che si cerca in rete quando il testo non basta.
        detail = f"{detail} [rc={rc}]" if detail else f"rc={rc}"
    # Prima di dichiarare un fallimento: il mondo e' per caso gia' come lo si
    # voleva? Un pacchetto gia' installato, o gia' assente, non e' un guasto —
    # e' l'obiettivo, raggiunto prima che arrivassimo noi.
    if _gia_come_lo_volevi(package_id, ctx, uninstall):
        return {"package_id": package_id, "ok": True,
                "action": "uninstall" if uninstall else "install",
                "source": ctx["manager"], "already": True,
                "note": _msg("MSG_PACKAGES_ALREADY_DONE", package=package_id)}
    return {"package_id": package_id, "ok": False,
            "action": "uninstall" if uninstall else "install",
            "error": _msg("ERR_PACKAGES_OPERATION_FAILED",
                          package=package_id, detail=detail[:300]),
            "error_class": "resource_unavailable",
            "error_code": "package_operation_failed",
            "exit_code": rc}


def _consent_token(resolved, uninstall, scope):
    """Un consenso legato a QUESTA scheda, non un permesso generico.

    L'impronta comprende cio' che la persona ha letto — quali pacchetti, in
    che direzione, con quale portata — cosi' il token non vale per
    un'operazione diversa da quella approvata. Non e' un segreto: e' lo
    stato pendente del runtime a renderlo irraggiungibile senza rispondere
    alla domanda, esattamente come per `admin`.
    """
    import hashlib
    impronta = "|".join([
        "uninstall" if uninstall else "install", scope,
        *sorted(r["package_id"] for r in resolved),
    ])
    return hashlib.sha256(impronta.encode("utf-8")).hexdigest()[:32]


# ── The card ──────────────────────────────────────────────────────────
def _card(resolved, uninstall, scope, machine=""):
    """What the person is being asked to approve, in full.

    A missing fingerprint is stated, not hidden: the Microsoft Store serves
    packages winget cannot fingerprint, and «unknown» is a fact the reader
    can weigh. Silence would read as «checked, fine».
    """
    lines = []
    for r in resolved:
        parts = [r["package_id"]]
        if r.get("version"):
            parts.append(_msg("MSG_PACKAGES_CARD_VERSION",
                              version=r["version"]))
        if r.get("publisher"):
            parts.append(r["publisher"])
        lines.append(" · ".join(parts))
        lines.append("   " + (r.get("url")
                              or _msg("MSG_PACKAGES_CARD_URL_UNKNOWN")))
        if r.get("digest"):
            lines.append(f"   {r.get('digest_algo') or 'SHA-256'}: "
                         + r["digest"])
        else:
            lines.append("   " + _msg("MSG_PACKAGES_CARD_HASH_UNKNOWN"))
        # Cio' che viene installato INSIEME e' parte di cio' che si approva.
        for dipendenza in r.get("dependencies") or []:
            lines.append("   " + _msg("MSG_PACKAGES_CARD_DEPENDENCY",
                                      package=dipendenza))
    # Il capo della scheda dice DOVE e CHE COSA. «machine» da solo e' la
    # portata (tutti gli utenti contro l'utente corrente) e si legge come il
    # nome di un computer: chi approva non sapeva su quale macchina stesse
    # per installare (segnalato da Roberto sul turno 762e0f20, 17/8/2026).
    dove = machine or _msg("MSG_PACKAGES_CARD_MACHINE_UNKNOWN")
    # La testa dice DOVE e CHE COSA. La portata non si annuncia qui: la
    # sceglie chi conferma, premendo un bottone. Annunciarla prima della
    # scelta significherebbe dichiarare una decisione non ancora presa.
    head = (_msg("MSG_PACKAGES_CARD_UNINSTALL_TITLE", n=len(resolved),
                 machine=dove)
            if uninstall
            else _msg("MSG_PACKAGES_CARD_INSTALL_TITLE", n=len(resolved),
                      machine=dove))
    del scope
    return head + "\n" + "\n".join(lines)


def _approval_dialog(resolved, uninstall, scope, ctx):
    """La domanda di consenso, con le sole risposte DAVVERO possibili.

    A bottoni, non a parole: una scelta premuta non si puo' fraintendere,
    una frase si' — ed e' stata la lezione della giornata, dal verbo
    installa/disinstalla in giu' (Roberto, 17/8/2026). Chi conferma non deve
    piu' ricordare nessuna formula.

    Le opzioni sono quelle che la macchina puo' onorare adesso: dove non c'e'
    ne' l'elevazione ne' l'aiutante elevato (ADR 0210 D), «per tutti gli
    utenti» non compare, perche' offrirla sarebbe far scegliere qualcosa che
    fallira'. Il perche' resta scritto sulla scheda, cosi' l'assenza e'
    spiegata invece che silenziosa.

    Quale delle due strade porti la portata «macchina» non cambia la domanda:
    la persona sceglie DOVE installare, non con quale meccanismo. Il
    meccanismo torna nel risultato, dove serve a chi legge un fallimento.
    """
    pacchetti = [r["package_id"] for r in resolved]

    def ramo(portata):
        return {"tool": "install_packages", "args": {
            "packages": pacchetti, "uninstall": uninstall, "scope": portata,
            # Il consenso e' stato dato a QUESTA scheda: nasce qui, viaggia
            # solo nello stato pendente del runtime — che nessuno raggiunge
            # senza rispondere — e torna soltanto sul ramo scelto.
            "actor_consent_token": _consent_token(resolved, uninstall,
                                                  portata),
        }}

    portate = _scopes_available(ctx)
    etichette = {"machine": "MSG_PACKAGES_BTN_ALL_USERS",
                 "user": "MSG_PACKAGES_BTN_ONLY_ME"}
    scelte, rami = [], {}
    if uninstall:
        # Rimuovere non ha portata: si toglie cio' che c'e'.
        scelte.append({"label": _msg("MSG_BTN_APPROVE"), "value": "approve"})
        rami["approve"] = ramo(scope)
    else:
        for portata in portate:
            scelte.append({"label": _msg(etichette[portata]), "value": portata})
            rami[portata] = ramo(portata)
        # «Per tutti» manca perche' manca l'aiutante elevato — ma l'aiutante
        # lo porta Metnos, non un amministratore con un file in mano. Il
        # bottone lo dice: Windows chiedera' conferma UNA volta, e da li' in
        # poi nulla chiedera' piu'. Resta separato da «per tutti» semplice
        # perche' promette una cosa diversa, e prometterne una sola sarebbe
        # far scoprire la finestra di Windows dopo aver premuto.
        if "machine" not in portate and _helper_installable(ctx):
            ramo_aiutante = ramo("machine")
            ramo_aiutante["args"]["install_helper"] = True
            scelte.append({"label": _msg("MSG_PACKAGES_BTN_ALL_USERS_SETUP"),
                           "value": "machine_setup"})
            rami["machine_setup"] = ramo_aiutante
    scelte.append({"label": _msg("MSG_BTN_REJECT"), "value": "reject"})

    testo = _card(resolved, uninstall, scope, ctx.get("machine", ""))
    if not uninstall and "machine" not in portate:
        # PERCHE' quella portata manca dipende da dove si e'. Su Windows
        # l'aiutante elevato la restituisce, e la nota dice come averlo;
        # altrove non esiste nessun aiutante da installare (ADR 0210 D8) e la
        # strada e' chi amministra la macchina. Promettere l'aiutante dove non
        # c'e' manderebbe una persona a cercare un programma inesistente.
        testo += "\n\n" + _msg("MSG_PACKAGES_NO_ELEVATION_NOTE"
                                if ctx.get("os") == "windows"
                                else "MSG_PACKAGES_NO_ELEVATION_ADMIN")
        # Un pacchetto che ne trascina altri spesso non entra nella sola
        # cartella dell'utente: le dipendenze sono componenti di sistema. Va
        # detto PRIMA, perche' scoprirlo dopo aver confermato e' scoprirlo
        # troppo tardi (turni 234daad8 e b7d63070, 17/8/2026).
        if any(r.get("dependencies") for r in resolved):
            testo += " " + _msg("MSG_PACKAGES_DEPENDENCY_WARNING")
        # Se l'aiutante c'e' ma non risponde, il perche' va detto: e' un guasto
        # diverso dal non averlo, e si ripara altrove.
        if ctx.get("helper_reason"):
            testo += f"\n\n[{ctx['helper_reason']}]"

    return {
        "title": _msg("MSG_PACKAGES_APPROVAL_TITLE"),
        "description": testo,
        "dialog": [{
            "var": "decision",
            "prompt": _msg("MSG_PACKAGES_APPROVAL_PROMPT"),
            "schema": {"kind": "choice", "choices": scelte},
        }],
        "fmt": "auto",
        # `gate_dispatch` esegue il ramo SOLO su una scelta dichiarata: su
        # «rifiuta», o su qualunque valore non mappato, non invoca niente.
        "on_complete": {
            "type": "gate_dispatch",
            "approve_value": "approve",
            "branches": rami,
        },
    }


def invoke(args: dict) -> dict:
    if not isinstance(args, dict):
        return _fail(_msg("ERR_ARGS_NOT_OBJECT"), "args_not_object")

    requested = _as_list(args.get("packages"))
    if not requested:
        return _fail(_msg("ERR_ARG_NOT_NONEMPTY_STRING", arg="packages"),
                     "packages_missing", error_class="missing_input")
    if len(requested) > _MAX_PACKAGES:
        return _fail(_msg("ERR_PACKAGES_TOO_MANY", n=len(requested),
                          maximum=_MAX_PACKAGES),
                     "too_many_packages")

    uninstall = bool(args.get("uninstall"))
    scope = str(args.get("scope") or "machine").strip().lower()
    if scope not in ("machine", "user"):
        scope = "machine"

    # Identity first, and exactly. A wildcard is refused before anything
    # else: it is not a bad identifier, it is a different request.
    for raw in requested:
        package_id = raw.strip() if isinstance(raw, str) else ""
        if any(ch in package_id for ch in "*?"):
            return _fail(_msg("ERR_PACKAGES_WILDCARD", value=package_id[:60]),
                         "wildcard_not_allowed")
        if not _ID_RE.match(package_id):
            return _fail(_msg("ERR_ARG_INVALID", arg="packages",
                              reason="package identifier required"),
                         "package_id_invalid")

    ctx = _context()
    if not ctx["manager"]:
        return _fail(_msg("ERR_PACKAGES_NO_MANAGER"), "no_package_manager",
                     error_class="capability_missing")

    # Dove QUALCHE portata regge non si rifiuta niente in anticipo: la scheda
    # offre quelle che la macchina puo' onorare e spiega perche' l'altra non
    # c'e' (`_approval_dialog`). Rifiutare prima di chiedere toglieva alla
    # persona la strada che funziona, invece di mostrargliela.
    #
    # Dove NESSUNA regge e' un'altra cosa. Un gestore che scrive solo cartelle
    # di sistema non ha una portata piu' piccola da offrire: senza privilegi
    # non fa niente, ne' installare ne' togliere. Si dice subito e senza
    # risolvere il catalogo: far leggere una scheda con indirizzo e impronta
    # per poi fallire e' far approvare qualcosa che non poteva avvenire.
    consent = str(args.get("actor_consent_token") or "").strip()
    # Un ramo approvato che porta con se' l'aiutante e' l'unico caso in cui
    # «nessuna portata regge» non e' la fine: la portata che manca sta per
    # arrivare, e la persona ha gia' detto di si' a quella specifica scheda.
    porta_aiutante = bool(consent) and bool(args.get("install_helper"))
    if not _scopes_available(ctx) and not porta_aiutante:
        return _fail(_msg("ERR_PACKAGES_NEEDS_ADMIN",
                          machine=ctx.get("machine") or "?"),
                     "needs_administrator", error_class="capability_missing")

    # ── Phase 1: resolve, and ask ─────────────────────────────────────
    if not consent:
        resolved, unresolved, ambiguous = [], [], []
        for raw in requested:
            package_id = raw.strip()
            found, candidates = _resolve(package_id, ctx)
            if candidates:
                ambiguous.append((package_id, candidates))
            elif found is None:
                unresolved.append(package_id)
            else:
                resolved.append(found)
        if ambiguous:
            # Piu' pacchetti rispondono a quel nome: sceglierne uno sarebbe
            # decidere al posto di chi ha chiesto. Si elencano, e la persona
            # ripete con l'identificativo che voleva.
            nome, candidati = ambiguous[0]
            return _fail(_msg("ERR_PACKAGES_AMBIGUOUS", name=nome,
                              candidates=", ".join(candidati[:6])),
                         "package_ambiguous", error_class="invalid_input")
        if unresolved:
            # What does not resolve does not get installed, and the whole
            # call stops: installing the half that resolved would be doing
            # something other than what was asked.
            return _fail(_msg("ERR_PACKAGES_UNRESOLVED",
                              packages=", ".join(unresolved[:5])),
                         "package_not_found", error_class="not_found")
        return {
            "ok": True,
            "decision": "needs_inputs",
            "installed": False,
            "ok_count": 0,
            "fail_count": 0,
            "results": [],
            "failed": [],
            "resolved": resolved,
            "needs_inputs": _approval_dialog(resolved, uninstall, scope,
                                             ctx),
        }

    # ── Phase 2: the person approved ──────────────────────────────────
    #
    # Se il ramo approvato era «per tutti, portando l'aiutante», l'aiutante
    # arriva ADESSO: scaricato dal canale firmato e installato dopo la
    # conferma che mostra Windows. Nessuno copia file a mano, e nessuno
    # amministra la macchina al posto di Metnos.
    if porta_aiutante and not _machine_scope_possible(ctx):
        esito = _setup_helper()
        if esito is None:
            return _fail(_msg("ERR_PACKAGES_HELPER_UNREACHABLE"),
                         "helper_unreachable", error_class="capability_missing")
        if not esito.get("ok"):
            # «Ho detto di no alla finestra» non e' un guasto, ed e' l'unico
            # esito che si racconta con parole sue.
            codice = str(esito.get("error_code") or "helper_setup_failed")
            chiave = ("ERR_PACKAGES_HELPER_REFUSED"
                      if codice == "consent_refused"
                      else "ERR_PACKAGES_HELPER_SETUP_FAILED")
            return _fail(_msg(chiave, detail=str(esito.get("detail") or "")[:200]),
                         codice, error_class="capability_missing")
        # Da qui in poi la macchina e' un'altra: l'aiutante c'e'.
        ctx = _context()

    results, failed = [], []
    deadline = time.monotonic() + _INSTALL_TIMEOUT_S * len(requested)
    for raw in requested:
        if time.monotonic() >= deadline:
            break
        outcome = _apply(raw.strip(), ctx, uninstall, scope)
        (results if outcome.get("ok") else failed).append(outcome)

    done = len(results)
    out = {
        "ok": not failed,
        # Elements really applied, never «asked for» (§2.8): a package the
        # manager refused did not get installed.
        "ok_count": done,
        "fail_count": len(failed),
        "results": results,
        "failed": failed,
        "installed": bool(results) and not uninstall,
        "action": "uninstall" if uninstall else "install",
        "source": ctx["manager"],
    }
    # Se non c'era niente da fare, dirlo. Senza, l'utente legge il generico
    # «Operazione completata» e non sa se il programma e' stato installato
    # adesso o c'era gia': due cose diverse, e la seconda e' quella che si
    # voleva sapere (Roberto, 19/8/2026).
    if results and not failed and all(r.get("already") for r in results):
        out["summary"] = " · ".join(
            str(r.get("note") or "") for r in results if r.get("note"))
    if results and failed:
        out["partial"] = True
    elif failed and not results:
        # Con `.get`: un record di fallimento a cui manca un campo deve poter
        # essere RIFERITO, non far cadere l'executor. Cadere qui
        # trasformerebbe «l'operazione e' fallita, ecco perche'» in un errore
        # interno senza spiegazione, che e' il modo peggiore di fallire.
        primo = failed[0]
        out["error"] = primo.get("error") or ""
        out["error_class"] = primo.get("error_class") or "unknown"
        out["error_code"] = primo.get("error_code") or "unknown"
    unhandled = len(requested) - done - len(failed)
    if unhandled > 0:
        out["truncated"] = True
        out["truncated_what"] = "packages"
        out["used"] = done
        out["available_total"] = len(requested)
        out["cap_field"] = "time_budget_s"
        out["cap_value"] = _INSTALL_TIMEOUT_S * len(requested)
    return out


def main():
    run_stdio(invoke, allow_empty=True)


if __name__ == "__main__":
    main()
