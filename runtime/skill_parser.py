"""skill_parser — parser deterministico (§7.9) di un file SKILL.md.

Importer skill agentskills.io -> executor Metnos. Modulo Task A.

Decompone:
- frontmatter YAML (delimitato da `---` ... `---`).
- body markdown in sezioni canoniche (`## References`, `## Scripts`,
  `## First-Time Setup`/`## Setup`, `## Usage` con sub-headings
  `### <Domain>`).
- code-blocks bash dentro `## Usage` -> sub-command strutturati
  `{domain, action, positional_args, flags, examples}`.

Niente subprocess, niente LLM, niente I/O di rete: solo dataclass +
pure functions su stringhe lette dal disco.

Riferimenti: the design guide §2.2 (vocabolario chiuso), §2.5 (manifest leggibili),
§7.9 (codice deterministico > LLM se equipotente).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SkillParseError(ValueError):
    """SKILL.md malformato (frontmatter assente / YAML invalido / sezioni
    obbligatorie mancanti)."""


# ---------------------------------------------------------------------------
# Dataclass — output del parser
# ---------------------------------------------------------------------------


@dataclass
class SkillFlag:
    """Un flag CLI estratto da un esempio bash.

    `name` senza i due trattini iniziali (`--max` -> `name="max"`).
    `inferred_type` deriva dal valore degli esempi: `string` di default,
    `integer` se tutti i valori sono numerici, `array_csv` se contiene
    virgole, `string_iso_datetime` se matcha ISO 8601, `bool` se compare
    senza valore.
    `default` quando estraibile dalla doc body (frase "defaults to ...").
    """

    name: str
    inferred_type: str = "string"
    default: Any = None
    seen_values: list = field(default_factory=list)
    is_bool_switch: bool = False


@dataclass
class SkillSubCommand:
    """Un sub-command estratto da un code-block di `## Usage`.

    `domain` = dominio della skill (gmail, calendar, drive, ...) ricavato
    dal sub-heading `### Calendar` o dal primo token dopo lo shorthand.
    `action` = secondo token (`$GAPI gmail search ...` -> action="search").
    `positional_args` = token MAIUSCOLI presenti negli esempi (`MESSAGE_ID`,
    `EVENT_ID`).
    `flags` = mapping nome->SkillFlag aggregato fra TUTTI gli esempi del
    sub-command.
    `examples` = lista delle righe bash testuali originali.
    """

    domain: str
    action: str
    positional_args: list = field(default_factory=list)
    flags: dict = field(default_factory=dict)
    examples: list = field(default_factory=list)


@dataclass
class SkillSetupStep:
    """Uno step del flusso `## First-Time Setup` (header `### Step N: <title>`
    + un eventuale code-block bash)."""

    index: int
    title: str
    body: str
    code: str = ""


@dataclass
class ParsedSkill:
    """Output completo di parse_skill_md()."""

    # Frontmatter — campi canonici (assenti -> stringa vuota / lista vuota).
    name: str = ""
    description: str = ""
    version: str = ""
    author: str = ""
    license: str = ""
    platforms: list = field(default_factory=list)
    required_credential_files: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    allowed_tools: list = field(default_factory=list)

    # Body — sezioni indicizzate.
    references: list = field(default_factory=list)
    scripts: list = field(default_factory=list)
    setup_steps: list = field(default_factory=list)
    sub_commands: list = field(default_factory=list)
    output_format: dict = field(default_factory=dict)
    troubleshooting: list = field(default_factory=list)

    # Provenance
    source_path: str = ""
    source_sha256: str = ""
    raw_body: str = ""


# ---------------------------------------------------------------------------
# Parser principale
# ---------------------------------------------------------------------------


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_skill_md(path) -> ParsedSkill:
    """Legge un SKILL.md e ritorna ParsedSkill.

    DEVI: passare un Path esistente.
    NON DEVI: passare un body senza frontmatter `---` di apertura.
    OK: parse_skill_md(Path("/tmp/SKILL.md")).
    ERRORE: parse_skill_md(Path("/tmp/missing.md")) -> FileNotFoundError.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"SKILL.md not found: {p}")
    raw = p.read_text(encoding="utf-8")
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    fm_match = _FRONTMATTER_RE.match(raw)
    if not fm_match:
        raise SkillParseError(
            f"frontmatter YAML mancante in {p} (atteso `---` su prima riga)"
        )
    fm_text = fm_match.group(1)
    body = raw[fm_match.end():]

    parsed = ParsedSkill(source_path=str(p), source_sha256=sha, raw_body=body)
    _populate_frontmatter(parsed, fm_text)
    _populate_body(parsed, body)
    return parsed


# ---------------------------------------------------------------------------
# Frontmatter — mini parser YAML deterministico
# ---------------------------------------------------------------------------


def _populate_frontmatter(parsed: ParsedSkill, fm_text: str) -> None:
    """Mini-YAML deterministico: chiavi top-level scalari/lista/mapping
    annidato. Niente PyYAML (dependency-free).

    Solleva SkillParseError su:
    - tab nell'indentazione (YAML lo vieta).
    - mapping top-level non rispettato.
    """
    if "\t" in fm_text:
        raise SkillParseError("YAML non puo' contenere tab nell'indentazione")

    data = _parse_yaml_block(fm_text)
    if not isinstance(data, dict):
        raise SkillParseError("frontmatter non e' un mapping top-level")

    parsed.name = str(data.get("name") or "").strip()
    parsed.description = str(data.get("description") or "").strip()
    parsed.version = str(data.get("version") or "").strip()
    parsed.author = str(data.get("author") or "").strip()
    parsed.license = str(data.get("license") or "").strip()

    plats = data.get("platforms") or []
    if isinstance(plats, list):
        parsed.platforms = [str(x).strip() for x in plats if str(x).strip()]

    rcf = data.get("required_credential_files") or []
    if isinstance(rcf, list):
        out = []
        for item in rcf:
            if isinstance(item, dict):
                out.append({k: v for k, v in item.items()})
            elif isinstance(item, str):
                out.append({"path": item})
        parsed.required_credential_files = out

    md = data.get("metadata") or {}
    if isinstance(md, dict):
        parsed.metadata = md

    at = data.get("allowed-tools") or data.get("allowed_tools") or []
    if isinstance(at, list):
        parsed.allowed_tools = [str(x).strip() for x in at]
    elif isinstance(at, str):
        parsed.allowed_tools = [s.strip() for s in at.split(",") if s.strip()]


_YAML_KEY_LINE = re.compile(r"^(\s*)([A-Za-z0-9_\-]+):\s*(.*)$")
_YAML_LIST_LINE = re.compile(r"^(\s*)-\s*(.*)$")


def _parse_yaml_block(text: str):
    """Parser ricorsivo basato su indentazione (spazi)."""
    lines = []
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        if raw_line.lstrip().startswith("#"):
            continue
        lines.append(raw_line)
    return _parse_yaml_lines(lines, 0)[0]


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_yaml_lines(lines, base_indent: int):
    if not lines:
        return None, 0
    first = lines[0]
    is_list = bool(_YAML_LIST_LINE.match(first)) and _indent_of(first) == base_indent
    if is_list:
        return _parse_yaml_list(lines, base_indent)
    return _parse_yaml_mapping(lines, base_indent)


def _parse_yaml_mapping(lines, base_indent: int):
    out: dict = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        ind = _indent_of(line)
        if ind < base_indent:
            break
        if ind > base_indent:
            i += 1
            continue
        m = _YAML_KEY_LINE.match(line)
        if not m:
            raise SkillParseError(f"YAML mapping: riga inattesa: {line!r}")
        key = m.group(2)
        val_inline = m.group(3).strip()
        if val_inline:
            out[key] = _parse_yaml_scalar_or_flow(val_inline)
            i += 1
            continue
        # Valore su righe successive (mapping/list child).
        child_lines = []
        j = i + 1
        while j < len(lines) and _indent_of(lines[j]) > base_indent:
            child_lines.append(lines[j])
            j += 1
        if not child_lines:
            out[key] = None
            i += 1
            continue
        child_indent = _indent_of(child_lines[0])
        child_val, _ = _parse_yaml_lines(child_lines, child_indent)
        out[key] = child_val
        i = j
    return out, i


def _parse_yaml_list(lines, base_indent: int):
    out: list = []
    i = 0
    while i < len(lines):
        line = lines[i]
        ind = _indent_of(line)
        if ind < base_indent:
            break
        m = _YAML_LIST_LINE.match(line)
        if not m or ind != base_indent:
            i += 1
            continue
        rest = m.group(2).strip()
        if rest and ":" not in rest:
            out.append(_parse_yaml_scalar_or_flow(rest))
            i += 1
            continue
        # Item complesso (mapping inline su prima riga + figli).
        item_lines: list = []
        if rest:
            synthetic_indent = base_indent + 2
            item_lines.append(" " * synthetic_indent + rest)
        j = i + 1
        while j < len(lines) and _indent_of(lines[j]) > base_indent:
            item_lines.append(lines[j])
            j += 1
        if item_lines:
            item_indent = _indent_of(item_lines[0])
            child, _ = _parse_yaml_mapping(item_lines, item_indent)
            out.append(child)
        else:
            out.append({})
        i = j
    return out, i


def _parse_yaml_scalar_or_flow(s: str):
    """Riconosce stringhe quoted, liste flow [a, b], int, bool."""
    s = s.strip()
    if not s:
        return ""
    if (s.startswith('"') and s.endswith('"')) or (
        s.startswith("'") and s.endswith("'")
    ):
        return s[1:-1]
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        parts = [p.strip() for p in inner.split(",") if p.strip()]
        return [_parse_yaml_scalar_or_flow(p) for p in parts]
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~"):
        return None
    try:
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return int(s)
    except ValueError:
        pass
    return s


# ---------------------------------------------------------------------------
# Body — sezioni canoniche
# ---------------------------------------------------------------------------


def _populate_body(parsed: ParsedSkill, body: str) -> None:
    sections = _split_h2_sections(body)
    for title, content in sections.items():
        norm = title.strip().lower()
        if norm == "references":
            parsed.references = _extract_bullet_paths(content)
        elif norm == "scripts":
            parsed.scripts = _extract_bullet_paths(content)
        elif norm in ("first-time setup", "setup"):
            parsed.setup_steps = _parse_setup_steps(content)
        elif norm == "usage":
            parsed.sub_commands = _parse_usage_subcommands(content)
        elif norm == "output format":
            parsed.output_format = _parse_output_format(content)
        elif norm == "troubleshooting":
            parsed.troubleshooting = _parse_troubleshooting(content)


def _split_h2_sections(body: str) -> dict:
    """`## Title` -> dict `{title: content}`. Mantiene insertion order.
    Ignora h2 dentro code fences.
    """
    out: dict = {}
    current_title = None
    current_buf: list = []
    in_fence = False
    fence_marker = ""

    for raw_line in body.splitlines():
        stripped = raw_line.lstrip()
        if stripped.startswith("```"):
            if not in_fence:
                in_fence = True
                fence_marker = stripped[:3]
            elif stripped.startswith(fence_marker):
                in_fence = False
            if current_title is not None:
                current_buf.append(raw_line)
            continue
        if not in_fence and stripped.startswith("## ") and not stripped.startswith("### "):
            if current_title is not None:
                out[current_title] = "\n".join(current_buf)
            current_title = stripped[3:].strip()
            current_buf = []
            continue
        if current_title is not None:
            current_buf.append(raw_line)
    if current_title is not None:
        out[current_title] = "\n".join(current_buf)
    return out


def _extract_bullet_paths(content: str) -> list:
    """`- path/to/file.py — descrizione` -> ["path/to/file.py", ...]."""
    out: list = []
    for line in content.splitlines():
        m = re.match(r"\s*[-*]\s+(.+)$", line)
        if not m:
            continue
        rest = m.group(1).strip()
        bm = re.match(r"`([^`]+)`", rest)
        if bm:
            out.append(bm.group(1))
            continue
        token = rest.split()[0] if rest.split() else ""
        if token:
            out.append(token)
    return out


# ---------------------------------------------------------------------------
# Setup steps
# ---------------------------------------------------------------------------


_STEP_HEADER_RE = re.compile(r"^###\s+Step\s+(\d+)\s*:\s*(.+)$", re.IGNORECASE)


def _parse_setup_steps(content: str) -> list:
    out: list = []
    current = None
    body_buf: list = []
    code_buf: list = []
    in_code = False

    def flush():
        nonlocal current, body_buf, code_buf
        if current is None:
            return
        current.body = "\n".join(body_buf).strip()
        current.code = "\n".join(code_buf).strip()
        out.append(current)
        current = None
        body_buf = []
        code_buf = []

    for raw_line in content.splitlines():
        m = _STEP_HEADER_RE.match(raw_line.strip())
        if m and not in_code:
            flush()
            idx = int(m.group(1))
            title = m.group(2).strip()
            current = SkillSetupStep(index=idx, title=title, body="")
            continue
        if current is None:
            continue
        if raw_line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            code_buf.append(raw_line)
        else:
            body_buf.append(raw_line)
    flush()
    return out


# ---------------------------------------------------------------------------
# Usage / sub-commands
# ---------------------------------------------------------------------------


_GAPI_TOKEN_RE = re.compile(r"\$([A-Z][A-Z0-9_]*)\s+(.+)$")


def _parse_usage_subcommands(content: str) -> list:
    """Itera dentro `## Usage`. Sub-heading `### Domain` apre dominio,
    ogni code-block bash con righe `$<SHORTHAND> <domain> <action> ...`
    contribuisce flag/examples al sub-command corrispondente.

    Aggrega per `(domain_lower, action)`: piu' esempi della stessa
    `(domain, action)` finiscono in UN solo SkillSubCommand.
    """
    out: list = []
    by_key: dict = {}
    current_domain = None
    in_code = False
    code_lines: list = []

    def flush_code():
        nonlocal code_lines
        if not code_lines:
            code_lines = []
            return
        for ln in code_lines:
            stripped = ln.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # Trailing inline comment : tronca al
            # primo  non dentro stringa quoted.
            stripped = _strip_trailing_comment(stripped)
            m = _GAPI_TOKEN_RE.match(stripped)
            if not m:
                continue
            shorthand = m.group(1)
            rest = m.group(2).strip()
            if shorthand == "GSETUP":
                continue
            tokens = _tokenize_shell(rest)
            if len(tokens) < 2:
                continue
            domain_token = tokens[0].lower()
            action = tokens[1]
            args = tokens[2:]
            domain = domain_token or (current_domain or "").lower()
            if not domain:
                continue
            key = (domain, action)
            sc = by_key.get(key)
            if sc is None:
                sc = SkillSubCommand(domain=domain, action=action)
                by_key[key] = sc
                out.append(sc)
            sc.examples.append("$%s %s" % (shorthand, rest))
            _absorb_args_into(sc, args)
        code_lines = []

    for raw_line in content.splitlines():
        stripped = raw_line.lstrip()
        if stripped.startswith("### ") and not in_code:
            current_domain = stripped[4:].strip().split()[0]
            continue
        if stripped.startswith("```"):
            if not in_code:
                in_code = True
                continue
            in_code = False
            flush_code()
            continue
        if in_code:
            code_lines.append(raw_line)
    flush_code()
    return out


def _tokenize_shell(s: str) -> list:
    """Tokenizza preservando quote singole/doppie. Niente shell escape."""
    out: list = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch.isspace():
            i += 1
            continue
        if ch in ('"', "'"):
            quote = ch
            j = i + 1
            buf: list = []
            while j < n and s[j] != quote:
                buf.append(s[j])
                j += 1
            out.append("".join(buf))
            i = j + 1
            continue
        j = i
        buf2: list = []
        while j < n and not s[j].isspace():
            if s[j] in ('"', "'"):
                quote = s[j]
                j += 1
                while j < n and s[j] != quote:
                    buf2.append(s[j])
                    j += 1
                if j < n:
                    j += 1
            else:
                buf2.append(s[j])
                j += 1
        if buf2:
            out.append("".join(buf2))
        i = j
    return out


def _absorb_args_into(sc: SkillSubCommand, args: list) -> None:
    """Aggiorna `sc.flags` e `sc.positional_args`.

    Riconosce:
    - `--flag value`  -> flag scalare.
    - `--flag`        -> bool switch (se non seguito da valore non-flag).
    - `TOKEN_MAIUSC`  -> positional placeholder.
    """
    i = 0
    n = len(args)
    while i < n:
        tok = args[i]
        if tok.startswith("--"):
            # Supporta sia `--flag value` che `--flag=value` (POSIX std).
            raw = tok[2:]
            if "=" in raw:
                name, _, inline_value = raw.partition("=")
                value = inline_value
                i += 1
            else:
                name = raw
                value = None
                if i + 1 < n and not args[i + 1].startswith("--"):
                    value = args[i + 1]
                    i += 2
                else:
                    i += 1
            flag = sc.flags.get(name)
            if flag is None:
                flag = SkillFlag(name=name)
                sc.flags[name] = flag
            if value is None:
                flag.is_bool_switch = True
                if flag.inferred_type == "string":
                    flag.inferred_type = "bool"
            else:
                flag.seen_values.append(value)
                _refine_inferred_type(flag, value)
            continue
        if re.match(r"^[A-Z][A-Z0-9_]+$", tok):
            if tok not in sc.positional_args:
                sc.positional_args.append(tok)
            i += 1
            continue
        if tok and not tok.startswith("-"):
            if tok not in sc.positional_args:
                sc.positional_args.append(tok)
        i += 1


_RE_ISO_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_RE_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RE_INT = re.compile(r"^-?\d+$")
_RE_FLOAT = re.compile(r"^-?\d+\.\d+$")


def _refine_inferred_type(flag: SkillFlag, value: str) -> None:
    """Aggiorna inferred_type aggregando il tipo di TUTTI i valori visti
    finora.

    Algoritmo:
    1. Classifica `value` -> uno fra: string_iso_datetime / string_iso_date
       / array_csv / integer / number / string.
    2. Se primo valore osservato (len(seen_values)==1 dopo l'append), il
       tipo del flag diventa quel tipo.
    3. Se gia' c'era un tipo, applica least-upper-bound:
       - string_iso_datetime + string_iso_date -> string_iso_datetime
       - integer + number -> number
       - array_csv + qualunque -> array_csv (sticky: la skill spesso usa
         un solo elemento in alcuni esempi).
       - tipi disgiunti (es. integer + string) -> string (catch-all).
    """
    classified = "string"
    if _RE_ISO_DATETIME.match(value):
        classified = "string_iso_datetime"
    elif _RE_ISO_DATE.match(value):
        classified = "string_iso_date"
    elif "," in value:
        classified = "array_csv"
    elif _RE_INT.match(value):
        classified = "integer"
    elif _RE_FLOAT.match(value):
        classified = "number"

    # Stato del flag prima di questo valore. seen_values e' gia' aggiornato
    # dal caller (append fatto prima della chiamata), quindi
    # len(seen_values)==1 indica primo valore osservato.
    is_first = len(flag.seen_values) <= 1
    cur = flag.inferred_type

    if is_first or cur == "bool":
        flag.inferred_type = classified
        return

    if cur == classified:
        return

    # Compatibilita' coppie (LUB).
    pair = frozenset((cur, classified))
    if pair == frozenset(("string_iso_datetime", "string_iso_date")):
        flag.inferred_type = "string_iso_datetime"
        return
    if pair == frozenset(("integer", "number")):
        flag.inferred_type = "number"
        return
    if "array_csv" in pair:
        flag.inferred_type = "array_csv"
        return
    # Disgiunti: catch-all.
    flag.inferred_type = "string"


# ---------------------------------------------------------------------------
# Output Format / Troubleshooting
# ---------------------------------------------------------------------------


def _parse_output_format(content: str) -> dict:
    out: dict = {}
    for line in content.splitlines():
        m = re.match(r"\s*[-*]\s+\*\*([^*]+)\*\*\s*:\s*(.+)$", line)
        if not m:
            continue
        endpoint = m.group(1).strip()
        desc = m.group(2).strip()
        out[endpoint] = desc
    return out


def _parse_troubleshooting(content: str) -> list:
    out: list = []
    for line in content.splitlines():
        if not line.strip().startswith("|"):
            continue
        if re.match(r"^\s*\|\s*[-:]+\s*\|", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        if cells[0].lower() in ("problem", "issue") and cells[1].lower() in (
            "fix", "solution",
        ):
            continue
        out.append((cells[0], cells[1]))
    return out


# ---------------------------------------------------------------------------
# Helpers misc
# ---------------------------------------------------------------------------


def _strip_trailing_comment(line: str) -> str:
    """Tronca un eventuale commento `# ...` a fine riga, rispettando le
    stringhe quoted. Se la riga inizia con `#` la lascia invariata (caso
    gestito a monte).
    """
    out = []
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
            continue
        if ch == "#" and not in_single and not in_double:
            # Solo se preceduto da whitespace (oppure inizio non rilevante
            # — escluso a monte).
            if i > 0 and line[i - 1].isspace():
                break
        out.append(ch)
    return "".join(out).rstrip()
