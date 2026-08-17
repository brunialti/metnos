#!/usr/bin/env python3
"""Report whether software is installed on this machine.

The question «is X installed» is not the question «is there an executable
called X on the PATH», and the two answers differ often enough to matter:
7-Zip is installed and not on the PATH; anything with a graphical interface
almost never is. So the machine's own package manager answers first — winget
on Windows, dpkg/rpm/pacman on Linux — and the PATH is consulted last, as the
catch for software installed outside the manager.

Every entry says in `source` where its answer came from. Without that the
reader cannot tell a registry hit from a PATH guess, and «installed: true»
would carry a confidence it has not earned (CLAUDE.md §2.8).

People name programs, they do not name package identifiers. «Is Outlook
installed» arrives as the word `outlook`, while the machine files it under
the id `9NRX63209R7B` — so an exact-id lookup answers «no» about software
that is plainly there (turn eb4f0cc9, 17/8/2026). The exact identifier is
therefore tried first and a NAME search follows when it misses; `match` says
which of the two answered, and `resolved_id` gives back the identity the
machine actually uses. Reading tolerates a human name; INSTALLING must not —
there the identity has to be exact and confirmed (ADR 0209).
"""

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402


# A package identifier, not a command line. Letters, digits and the few
# separators real identifiers use: `7zip.7zip`, `python3-pip`, `libc6:amd64`,
# `Microsoft.VisualStudioCode`. A leading `-` is refused on purpose: it would
# reach the package manager as an option, not as a name.
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:@-]{0,127}$")

# One probe should answer in about a second; winget on a cold source can take
# several. Beyond this the machine is not going to answer, and waiting longer
# only holds the turn open.
_PROBE_TIMEOUT_S = 25

# Total budget for the whole call. What is left unprobed is DECLARED (§2.7),
# never dropped in silence.
_TOTAL_BUDGET_S = 120


def _which(name):
    """`shutil.which` that cannot take the call down with it.

    A PATH lookup can raise on a broken mount or a permission change, and the
    text of that error carries filesystem detail that has no business in a
    user-facing message. An unreadable PATH means «not found here», which is
    an answer the caller can use.
    """
    try:
        return shutil.which(name) or ""
    except (OSError, TypeError, ValueError):
        return ""


def _as_list(value):
    """A plural argument accepts one element too (CLAUDE.md §2.4).

    Blank strings are dropped rather than carried: an empty value is not a
    package anybody asked about, it is the absence of one, and treating it
    as a malformed identifier would report a failure where there is only a
    gap. What the caller DID name is never dropped.
    """
    if value is None:
        items = []
    elif isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = [value]
    return [x for x in items
            if not (isinstance(x, str) and not x.strip())]


def _run(argv, timeout_s=_PROBE_TIMEOUT_S):
    """Run a command without a shell. Returns (rc, stdout) or (None, '')."""
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout_s,
            shell=False,
            # A package manager must never stop to ask something: nobody is
            # watching this terminal.
            stdin=subprocess.DEVNULL,
        )
        return proc.returncode, (proc.stdout or "")
    except (OSError, subprocess.SubprocessError):
        return None, ""


# ── Linux ─────────────────────────────────────────────────────────────
def _linux_manager():
    """The package manager this distribution actually has, or None."""
    for tool, kind in (("dpkg-query", "dpkg"), ("rpm", "rpm"),
                       ("pacman", "pacman")):
        path = _which(tool)
        if path:
            return kind, path
    return None, ""


def _dpkg_rows(out):
    """Installed rows only. A removed package still has a dpkg record, and
    «present in the database» is not «installed»."""
    rows = []
    for line in out.splitlines():
        name, _, rest = line.partition("\t")
        version, _, status = rest.partition("\t")
        if name.strip() and status.strip() == "installed":
            rows.append((name.strip(), version.strip()))
    return rows


def _probe_linux(package_id, manager, tool_path, by_name=False):
    if manager == "dpkg":
        # A name search is the same query with the term as a substring: dpkg
        # takes a glob, so the fallback needs no second mechanism.
        term = f"*{package_id}*" if by_name else package_id
        rc, out = _run([tool_path, "-W", "-f=${Package}\\t${Version}\\t"
                        "${db:Status-Status}\\n", term])
        rows = _dpkg_rows(out) if rc == 0 else []
        if not rows:
            return None
        # More than one match: the shortest name is the package the person
        # meant («git» over «git-man»), and the full list travels with it so
        # the answer stays checkable.
        rows.sort(key=lambda r: (len(r[0]), r[0]))
        name, version = rows[0]
        hit = {"name": name, "version": version, "source": "dpkg"}
        if by_name:
            hit["resolved_id"] = name
            if len(rows) > 1:
                hit["also_matched"] = [r[0] for r in rows[1:6]]
        return hit
    if manager == "rpm":
        rc, out = _run([tool_path, "-q", "--qf",
                        "%{NAME}\\t%{VERSION}\\n", package_id])
        if rc != 0 or not out.strip():
            return None
        name, _, version = out.strip().partition("\t")
        return {"name": name or package_id, "version": version.strip(),
                "source": "rpm"}
    if manager == "pacman":
        rc, out = _run([tool_path, "-Q", package_id])
        if rc != 0 or not out.strip():
            return None
        parts = out.strip().split()
        return {"name": parts[0] if parts else package_id,
                "version": parts[1] if len(parts) > 1 else "",
                "source": "pacman"}
    return None


# ── Windows ───────────────────────────────────────────────────────────
def _winget_columns(header):
    """Where each column starts, read off the header line.

    Geometry, not vocabulary: the headings are localized («Nome», «Id»,
    «Versione»), their POSITIONS are not, and the column order is fixed
    (name, id, version, ...). A column begins at the start of the line or
    after a run of two or more spaces.
    """
    # `start(1)`, not `start()`: the match includes the spaces before the
    # column, and cutting there would slice into the previous value.
    return [m.start(1) for m in re.finditer(r"(?:^|\s{2,})(\S)", header)]


def _winget_rows(out, term):
    """Table rows that mention the term, as (name, id, version).

    A data row cannot be split on runs of spaces: winget pads each column to
    a fixed width, so a value that fills its column leaves a SINGLE space
    before the next one — «Outlook for Windows 9NRX63209R7B 1.2025.1209.500
    msstore» is four columns, not one. The header's geometry gives the cut
    points; the localized «nothing found» line has no header above it and so
    yields nothing.
    """
    lines = out.splitlines()
    dashes = next((i for i, ln in enumerate(lines)
                   if ln.strip() and set(ln.strip()) <= {"-", "─"}), -1)
    if dashes < 1:
        return []
    starts = _winget_columns(lines[dashes - 1])
    if len(starts) < 2:
        return []
    bounds = list(zip(starts, starts[1:] + [None]))

    rows = []
    low = term.lower()
    for line in lines[dashes + 1:]:
        if not line.strip() or low not in line.lower():
            continue
        cols = [line[a:b].strip() for a, b in bounds]
        name = cols[0]
        pkg_id = cols[1] if len(cols) > 1 else ""
        version = ""
        for col in cols[2:]:
            if re.match(r"^[0-9]", col):
                version = col
                break
        if name and pkg_id:
            rows.append((name, pkg_id, version))
    return rows


def _probe_windows(package_id, tool_path, by_name=False):
    selector = ["--name", package_id] if by_name else [
        "--id", package_id, "--exact"]
    rc, out = _run([tool_path, "list", *selector,
                    "--accept-source-agreements", "--disable-interactivity"])
    if rc != 0:
        # winget answers «nothing found» with a non-zero code. That is the
        # negative case, not a broken call.
        return None
    rows = _winget_rows(out, package_id)
    if not rows:
        return None
    # The header row carries the localized column titles and mentions nothing
    # useful; a real row always has an id, and the shortest name is the
    # closest match to what was asked.
    rows.sort(key=lambda r: (len(r[0]), r[0]))
    name, pkg_id, version = rows[0]
    hit = {"name": name, "version": version, "source": "winget"}
    if by_name:
        hit["resolved_id"] = pkg_id
        if len(rows) > 1:
            hit["also_matched"] = [r[0] for r in rows[1:6]]
    return hit


# ── PATH, last ────────────────────────────────────────────────────────
def _probe_path(package_id):
    """The catch: software installed outside the manager still exists."""
    path = _which(package_id)
    if not path:
        return None
    return {"name": package_id, "version": "", "source": "path",
            "path": path}


def _probe(package_id, ctx):
    hit, match = None, ""
    if ctx["os"] == "windows" and ctx["winget"]:
        hit = _probe_windows(package_id, ctx["winget"])
        match = "exact" if hit else ""
        if hit is None:
            # The identifier missed. People name programs, not identifiers,
            # so before answering «no» the same word is tried as a NAME.
            hit = _probe_windows(package_id, ctx["winget"], by_name=True)
            match = "name" if hit else ""
    elif ctx["manager"]:
        hit = _probe_linux(package_id, ctx["manager"], ctx["manager_path"])
        match = "exact" if hit else ""
        if hit is None:
            hit = _probe_linux(package_id, ctx["manager"],
                               ctx["manager_path"], by_name=True)
            match = "name" if hit else ""
    if hit is None:
        hit = _probe_path(package_id)
        match = "path" if hit else ""
    if hit is None:
        # Not an error: «not installed» is an answer, and a package the
        # machine does not have must not fail the other elements (§2.1).
        return {"package_id": package_id, "installed": False,
                "name": package_id, "version": "",
                "source": ctx["primary_source"], "match": "none"}
    entry = {"package_id": package_id, "installed": True, "match": match}
    entry.update(hit)
    return entry


def _context():
    is_windows = sys.platform.startswith("win")
    winget = _which("winget") if is_windows else ""
    manager, manager_path = ("", "") if is_windows else _linux_manager()
    primary = "winget" if winget else (manager or "path")
    return {"os": "windows" if is_windows else "linux",
            "winget": winget or "", "manager": manager or "",
            "manager_path": manager_path, "primary_source": primary}


def _fail(error, code, error_class="invalid_input"):
    """A refusal with a class the engine can act on.

    The class is not decoration. `missing_input` says «you did not tell me
    WHICH», and the engine answers that by asking the model for the argument
    again — the query names the program, so the second attempt usually has
    it. `invalid_input` says «what you told me cannot be used», and re-asking
    would only repeat the same refusal. Marking a missing argument as
    invalid turned a recoverable turn into a dead end (query «ho outlook sul
    pc?», 17/8/2026: the tool was right, the argument was empty, and nobody
    asked again).
    """
    return {"ok": False, "error": error, "error_class": error_class,
            "error_code": code, "ok_count": 0, "fail_count": 1,
            "entries": [], "failed": [{"error": error,
                                       "error_class": error_class,
                                       "error_code": code}]}


def invoke(args: dict) -> dict:
    if not isinstance(args, dict):
        return _fail(_msg("ERR_ARGS_NOT_OBJECT"), "args_not_object")

    requested = _as_list(args.get("packages"))
    if not requested:
        return _fail(_msg("ERR_ARG_NOT_NONEMPTY_STRING", arg="packages"),
                     "packages_missing", error_class="missing_input")

    ctx = _context()
    entries, failed = [], []
    deadline = time.monotonic() + _TOTAL_BUDGET_S
    probed = 0

    for raw in requested:
        package_id = raw.strip() if isinstance(raw, str) else ""
        if not _ID_RE.match(package_id):
            # A malformed identifier is that element's problem, not the
            # call's: the others are still answered.
            failed.append({
                "package_id": str(raw)[:128],
                "error": _msg("ERR_ARG_INVALID", arg="packages",
                              reason="package identifier required"),
                "error_class": "invalid_input",
                "error_code": "package_id_invalid",
            })
            continue
        if time.monotonic() >= deadline:
            break
        entries.append(_probe(package_id, ctx))
        probed += 1

    out = {
        "ok": not failed,
        # Elements really interrogated, not elements found (§2.8): a package
        # that turned out to be absent was still answered.
        "ok_count": probed,
        "fail_count": len(failed),
        "entries": entries,
        "failed": failed,
        "source_primary": ctx["primary_source"],
    }
    if entries and failed:
        out["partial"] = True
    elif failed and not entries:
        # Nessun elemento e' passato: la chiamata nel suo insieme ha una
        # classe, non solo i singoli elementi. Senza, il chiamante vede
        # `ok: False` e deve indovinare di che errore si tratta.
        out["error"] = failed[0]["error"]
        out["error_class"] = failed[0]["error_class"]
        out["error_code"] = failed[0]["error_code"]
    unprobed = len(requested) - probed - len(failed)
    if unprobed > 0:
        # §2.7: a budget that ran out is declared with what it cost and what
        # is left, never presented as a complete answer.
        out["truncated"] = True
        out["truncated_what"] = "packages"
        out["used"] = probed
        out["available_total"] = len(requested)
        out["cap_field"] = "time_budget_s"
        out["cap_value"] = _TOTAL_BUDGET_S
    return out


def main():
    run_stdio(invoke, allow_empty=True)


if __name__ == "__main__":
    main()
