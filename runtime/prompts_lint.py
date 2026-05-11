"""prompts_lint.py — linter deterministico per `runtime/prompts/<lang>/*.j2`.

Fase C4 (11/5/2026). Cinque check (CLAUDE.md §7.9, niente LLM):

  L1 frontmatter required        — primo non-blank line `{# ---` + 8 campi
                                    (role/tier/lang/style/version/owner/
                                    updated/sha_prev). `lang` deve essere
                                    iso-2 (a-z) o BCP-47 (`xx-Yy`).
  L2 hedge blacklist             — solo per `style: prescriptive`: rileva
                                    "preferibilmente", "se possibile", "cerca
                                    di", "prova a", "preferably", "if
                                    possible", "try to", "perhaps", "maybe".
  L3 LOC cap                     — warn se LOC > 800, error se > 1200.
  L4 trailing newline            — file deve terminare con `\\n`.
  L5 lang symmetry               — per ogni `it/<path>.j2` esiste
                                    `<lang>/<path>.j2` e viceversa (solo
                                    presenza file, non contenuto: drift
                                    gestito dal daemon i18n).

API:
    scan(root: Path) -> list[LintIssue]
    LintIssue(file, line, level, code, message)

CLI integrato in `runtime/admin/prompts_cli.py::cmd_lint`. Determinismo §7.9.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


# Frontmatter parsing -------------------------------------------------------

_FRONTMATTER_OPEN = "{# ---"
_FRONTMATTER_CLOSE = "--- #}"

_REQUIRED_FIELDS = (
    "role", "tier", "lang", "style", "version", "owner", "updated", "sha_prev",
)

# ISO 639-1 (2 lettere a-z) o BCP-47 (es. zh-Hans, pt-BR).
_LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{1,8})*$")

_STYLE_VALID = ("prescriptive", "definitional", "few_shot")

# Hedge blacklist (case-insensitive). Solo `style: prescriptive`.
_HEDGE_PATTERNS = (
    "preferibilmente",
    "se possibile",
    "cerca di",
    "prova a",
    "preferably",
    "if possible",
    "try to",
    "perhaps",
    "maybe",
)

# Soglie LOC: avviso/error.
_LOC_WARN = 800
_LOC_ERROR = 1200


@dataclass(frozen=True)
class LintIssue:
    """Singolo issue del linter. `level` ∈ {'warn', 'error'}. `line` 1-based;
    0 indica check al livello-file (frontmatter mancante, lang symmetry)."""
    file: str
    line: int
    level: str
    code: str
    message: str


# Helpers -------------------------------------------------------------------

def _parse_frontmatter(content: str) -> tuple[dict[str, str] | None, int]:
    """Estrae il dict frontmatter dai primi `{# --- ... --- #}` non-blank.
    Ritorna `(fields, last_line)` o `(None, 0)` se non presente/malformato.
    `last_line` = numero 1-based dell'ultima riga del frontmatter."""
    lines = content.splitlines()
    # Skip blank lines iniziali.
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return None, 0
    if not lines[i].lstrip().startswith(_FRONTMATTER_OPEN):
        return None, 0
    start = i
    fields: dict[str, str] = {}
    i += 1
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith(_FRONTMATTER_CLOSE):
            return fields, i + 1
        m = re.match(r"^\s*([a-zA-Z_][\w]*)\s*:\s*(.*?)\s*$", line)
        if m:
            fields[m.group(1).lower()] = m.group(2)
        i += 1
    # Open ma close mancante.
    return None, start + 1


def _content_after_frontmatter(content: str) -> str:
    """Rimuove il blocco frontmatter (se presente) per consentire l'analisi
    del corpo senza falsi positivi (es. parole hedge nei commenti)."""
    fields, last = _parse_frontmatter(content)
    if not fields:
        return content
    lines = content.splitlines()
    body = "\n".join(lines[last:])
    return body


def _check_l1_frontmatter(path: Path, content: str) -> list[LintIssue]:
    """L1: frontmatter required + 8 campi + lang valido + style valido."""
    issues: list[LintIssue] = []
    fields, _ = _parse_frontmatter(content)
    if not fields:
        issues.append(LintIssue(
            file=str(path), line=0, level="error", code="L1_FRONTMATTER_MISSING",
            message="frontmatter `{# --- ... --- #}` mancante o malformato",
        ))
        return issues
    missing = [k for k in _REQUIRED_FIELDS if k not in fields]
    if missing:
        issues.append(LintIssue(
            file=str(path), line=0, level="error", code="L1_FRONTMATTER_FIELDS",
            message=f"campi frontmatter mancanti: {sorted(missing)}",
        ))
    lang = fields.get("lang", "")
    if lang and not _LANG_RE.match(lang):
        issues.append(LintIssue(
            file=str(path), line=0, level="error", code="L1_FRONTMATTER_LANG_INVALID",
            message=f"lang frontmatter non valido: {lang!r} (atteso ISO 639-1 o BCP-47)",
        ))
    style = fields.get("style", "")
    if style and style not in _STYLE_VALID:
        issues.append(LintIssue(
            file=str(path), line=0, level="error", code="L1_FRONTMATTER_STYLE_INVALID",
            message=f"style frontmatter non valido: {style!r} (atteso uno di {_STYLE_VALID})",
        ))
    return issues


def _check_l2_hedge_blacklist(path: Path, content: str) -> list[LintIssue]:
    """L2: hedge blacklist (solo per `style: prescriptive`)."""
    fields, _ = _parse_frontmatter(content)
    if not fields or fields.get("style") != "prescriptive":
        return []
    body = _content_after_frontmatter(content)
    issues: list[LintIssue] = []
    body_lines = body.splitlines()
    for idx, line in enumerate(body_lines, start=1):
        low = line.lower()
        for pat in _HEDGE_PATTERNS:
            if pat in low:
                issues.append(LintIssue(
                    file=str(path), line=idx, level="error",
                    code="L2_HEDGE",
                    message=f"hedge vietato in prompt prescriptive: {pat!r}",
                ))
                break  # un hit per riga, evita duplicati
    return issues


def _check_l3_loc(path: Path, content: str) -> list[LintIssue]:
    """L3: cap LOC (warn > 800, error > 1200)."""
    loc = len(content.splitlines())
    if loc > _LOC_ERROR:
        return [LintIssue(
            file=str(path), line=0, level="error", code="L3_LOC_ERROR",
            message=f"LOC={loc} > {_LOC_ERROR} (limite hard)",
        )]
    if loc > _LOC_WARN:
        return [LintIssue(
            file=str(path), line=0, level="warn", code="L3_LOC_WARN",
            message=f"LOC={loc} > {_LOC_WARN} (avviso: split consigliato)",
        )]
    return []


def _check_l4_trailing_newline(path: Path, content: str) -> list[LintIssue]:
    """L4: file deve terminare con `\\n`."""
    if not content:
        return []  # file vuoti coperti da L1
    if not content.endswith("\n"):
        return [LintIssue(
            file=str(path), line=0, level="error", code="L4_TRAILING_NEWLINE",
            message="il file non termina con `\\n`",
        )]
    return []


def _check_l5_lang_symmetry(root: Path) -> list[LintIssue]:
    """L5: simmetria cross-lang. Ritorna issue per ogni file presente in
    `<langA>/...` ma mancante in `<langB>/...`. Considera la struttura split
    planner: `planner/_core.j2`, `planner/sections/mail.j2`, etc.

    A differenza degli altri check (per-file), L5 e' uno scan globale: viene
    chiamato una volta sola dal driver `scan(root)`.
    """
    issues: list[LintIssue] = []
    langs = sorted(d.name for d in root.iterdir()
                    if d.is_dir() and not d.name.startswith("_"))
    if not langs:
        return []

    # Mappa lang → set di path relativi (es. {"planner/_core", "vaglio", ...}).
    by_lang: dict[str, set[str]] = {}
    for lang in langs:
        lang_root = root / lang
        files = set()
        for p in lang_root.rglob("*.j2"):
            if "_pending" in p.parts:
                continue
            rel = p.relative_to(lang_root)
            # Normalize: rimuovi `.j2` e usa POSIX-slash come chiave canonical.
            key = rel.with_suffix("").as_posix()
            files.add(key)
        by_lang[lang] = files

    # Tutti i path canonici dell'unione.
    all_keys: set[str] = set()
    for files in by_lang.values():
        all_keys |= files

    for key in sorted(all_keys):
        missing_in = [lang for lang in langs if key not in by_lang[lang]]
        if missing_in:
            # 1 issue per coppia mancante (file teorico).
            for lang in missing_in:
                missing_path = root / lang / f"{key}.j2"
                issues.append(LintIssue(
                    file=str(missing_path), line=0, level="error",
                    code="L5_LANG_SYMMETRY",
                    message=f"role {key!r} presente in altre lingue ma manca in {lang!r}",
                ))
    return issues


# Driver --------------------------------------------------------------------

def scan(root: Path, *, langs: list[str] | None = None) -> list[LintIssue]:
    """Esegue tutti i 5 check sui `.j2` in `<root>/<lang>/`.

    `langs=None` → tutte le lingue presenti. `langs=["it"]` → solo IT, etc.
    `langs=["all"]` interpretato come None.

    Ritorna lista consolidata di `LintIssue`. Ordine: per file (rglob sorted)
    poi per linea crescente (issue.line); L5 in coda.
    """
    if langs and "all" in langs:
        langs = None
    target_langs = langs or [d.name for d in root.iterdir()
                              if d.is_dir() and not d.name.startswith("_")]
    target_langs = sorted(target_langs)

    issues: list[LintIssue] = []
    for lang in target_langs:
        lang_root = root / lang
        if not lang_root.is_dir():
            continue
        for p in sorted(lang_root.rglob("*.j2")):
            if "_pending" in p.parts:
                continue
            try:
                content = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                issues.append(LintIssue(
                    file=str(p), line=0, level="error",
                    code="L0_READ_ERROR",
                    message="file non leggibile come UTF-8",
                ))
                continue
            issues.extend(_check_l1_frontmatter(p, content))
            issues.extend(_check_l2_hedge_blacklist(p, content))
            issues.extend(_check_l3_loc(p, content))
            issues.extend(_check_l4_trailing_newline(p, content))

    # L5 e' uno scan globale: invocato una sola volta.
    if not langs:
        issues.extend(_check_l5_lang_symmetry(root))

    # Sort: per file asc, poi linea asc, poi code asc.
    issues.sort(key=lambda i: (i.file, i.line, i.code))
    return issues


def format_issue(issue: LintIssue) -> str:
    """Format human-readable single line per CLI output."""
    prefix = "ERROR" if issue.level == "error" else "WARN"
    loc = f":{issue.line}" if issue.line > 0 else ""
    return f"{prefix:5s} {issue.file}{loc} [{issue.code}] {issue.message}"
