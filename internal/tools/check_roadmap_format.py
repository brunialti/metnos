#!/usr/bin/env python3
"""Check that every roadmap document keeps the single canonical shape.

The contract is stated in ``internal/roadmap/README.md``: one file-name
schema, one seven-field header table, one closed set of states, and an index
row whose title matches the document's own heading.  Run with no arguments;
a non-zero exit lists every deviation.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROADMAP = Path(__file__).resolve().parent.parent / "roadmap"

FIELDS = (
    "Identificatore",
    "Stato",
    "Creazione",
    "Ultima revisione",
    "Conservazione",
    "Implementazione reale",
    "Origine e prove",
)
STATES = ("active", "ready", "in_progress", "implemented", "closed", "cancelled")

FILE_NAME = re.compile(r"^RM-(\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
HEADING = re.compile(r"^# (RM-\d{4}) — (.+)$")
ROW = re.compile(r"^\| ([^|]+?) \| (.+) \|$")
INDEX_ROW = re.compile(r"^\| \[(RM-\d{4})\]\(([^)]+)\) \| ([^|]+?) \| `([^`]+)` \|")
DATE = re.compile(r"^`\d{4}-\d{2}-\d{2}`")


def check_document(path: Path) -> tuple[list[str], str | None, str | None]:
    """Return (problems, title, state) for one roadmap document."""
    problems: list[str] = []
    name = path.name
    if not FILE_NAME.match(name):
        problems.append(f"{name}: il nome non segue RM-NNNN-<slug-kebab>.md")

    lines = path.read_text(encoding="utf-8").split("\n")
    heading = HEADING.match(lines[0]) if lines else None
    if heading is None:
        problems.append(f"{name}: la prima riga non è '# RM-NNNN — <Titolo>'")
        return problems, None, None
    if not name.startswith(heading.group(1) + "-"):
        problems.append(f"{name}: identificatore del titolo diverso dal nome del file")

    if lines[1:2] != [""]:
        problems.append(f"{name}: manca la riga vuota dopo il titolo")
    if lines[2:4] != ["| Campo | Valore |", "|---|---|"]:
        problems.append(f"{name}: la tabella canonica non apre alla riga 3")
        return problems, heading.group(2), None

    found: list[tuple[str, str]] = []
    for line in lines[4:]:
        row = ROW.match(line)
        if row is None:
            break
        found.append((row.group(1).strip(), row.group(2).strip()))

    names = tuple(field for field, _ in found)
    if names != FIELDS:
        problems.append(
            f"{name}: campi {list(names)} invece dei sette canonici {list(FIELDS)}"
        )
        return problems, heading.group(2), None

    values = dict(found)
    if values["Identificatore"] != f"`{heading.group(1)}`":
        problems.append(f"{name}: Identificatore diverso da quello del titolo")
    state = values["Stato"].split(";")[0].strip().strip("`").split("`")[0]
    if state not in STATES:
        problems.append(f"{name}: stato '{state}' fuori dagli stati ammessi")
    for field in ("Creazione", "Ultima revisione"):
        if not DATE.match(values[field]):
            problems.append(f"{name}: {field} non apre con `YYYY-MM-DD`")
    for field in ("Conservazione", "Implementazione reale", "Origine e prove"):
        if not values[field]:
            problems.append(f"{name}: {field} è vuoto")
    return problems, heading.group(2), state


def main() -> int:
    documents = sorted(p for p in ROADMAP.glob("RM-*.md"))
    if not documents:
        print(f"nessun documento in {ROADMAP}", file=sys.stderr)
        return 2

    problems: list[str] = []
    titles: dict[str, str] = {}
    states: dict[str, str] = {}
    for path in documents:
        found, title, state = check_document(path)
        problems.extend(found)
        identifier = path.name[:7]
        if title is not None:
            titles[identifier] = title
        if state is not None:
            states[identifier] = state

    index = (ROADMAP / "README.md").read_text(encoding="utf-8")
    indexed: set[str] = set()
    for line in index.split("\n"):
        row = INDEX_ROW.match(line)
        if row is None:
            continue
        identifier, link, title, state = row.groups()
        indexed.add(identifier)
        if not (ROADMAP / link).is_file():
            problems.append(f"README: il collegamento {link} non esiste")
        if identifier in titles and title.strip() != titles[identifier]:
            problems.append(
                f"README: titolo di {identifier} diverso da quello del documento"
            )
        if identifier in states and state != states[identifier]:
            problems.append(
                f"README: stato di {identifier} è `{state}`, il documento dice"
                f" `{states[identifier]}`"
            )
    missing = set(titles) - indexed
    if missing:
        problems.append(f"README: manca la riga di indice per {sorted(missing)}")

    for problem in problems:
        print(problem)
    print(f"{len(documents)} documenti controllati, {len(problems)} scostamenti")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
