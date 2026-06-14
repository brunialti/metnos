"""prompts_lint.py — linter deterministico per `runtime/prompts/<lang>/*.j2`
e `runtime/prompts/<lang>/.../*.yaml` (asse B PoC, 12/5/2026).

Fase C4 (11/5/2026). Sei check (the design guide §7.9, niente LLM):

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
  L5 lang symmetry               — per ogni `it/<path>` (.j2 o .yaml) esiste
                                    sibling in <lang>/ e viceversa (solo
                                    presenza file, non contenuto: drift
                                    gestito dal daemon i18n).
  L6 static-first layout         — ottimizzazione A prompt-cache (10/6/2026):
                                    i template che dichiarano frontmatter
                                    `layout: static_first` DEVONO avere il
                                    marker `{# STATIC-END ... #}` e NESSUNA
                                    interpolazione variabile/statement Jinja
                                    PRIMA del marker (prefisso del render
                                    byte-identico fra le query → checkpoint
                                    condiviso sul llama-server, vedi
                                    prompt_loader.get_split). Il prompt
                                    canonico `engine_proposer` DEVE
                                    dichiarare il layout in OGNI lingua
                                    (anchor: il guard non si elude
                                    rimuovendo il campo) e referenziare
                                    `{{ user_query }}` DOPO il marker (la
                                    coda diventa il messaggio user: senza
                                    query il modello pianifica alla cieca).

Asse B (12/5/2026): le sezioni planner possono essere `.yaml` strutturate.
Per i `.yaml` si verifica:
  - L1 frontmatter top-level (8 campi `role/tier/lang/style/version/owner/
    updated/sha_prev`) come chiavi YAML.
  - schema: `section` dict con `name`, `rules` list di dict con
    `name/when/must/must_not/ok/error`.
  - `rules[*].name` univoco per sezione.
  - `must/must_not/ok/error` non vuoti.
  - L4 trailing newline mantenuto.
  - L5 simmetria cross-lang considera sia .j2 sia .yaml.

API:
    scan(root: Path) -> list[LintIssue]
    LintIssue(file, line, level, code, message)

CLI integrato in `runtime/admin/prompts_cli.py::cmd_lint`. Determinismo §7.9.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import yaml


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


# L6 static-first layout (ottimizzazione A prompt-cache, 10/6/2026) --------
#
# Razionale: il prompt del proposer è ~5.5k token riprocessati a OGNI query
# se il contenuto per-query (intent/pool/excluded) sta nel SYSTEM. Col layout
# invariante-prima/variabile-dopo + split al marker (`prompt_loader.get_split`:
# testa→system, coda→user) il llama-server riusa il prefisso statico dal
# checkpoint `n_before_user` e ogni query paga solo la coda (misura 10/6:
# prompt_n 5521→1277, latenza 8.15s→2.26s). Il guard rende il layout un
# INVARIANTE (§10.6 anti-regressione): synt/translator/edit futuri non
# possono reintrodurre interpolazioni nella parte statica senza rompere
# il lint.
#
# Contratto (deterministico, §7.9):
#   (a) ANCHOR — `prompts/<lang>/<role>.j2` con role in
#       `_STATIC_FIRST_ANCHOR_ROLES` DEVE dichiarare `layout: static_first`
#       nel frontmatter in OGNI lingua (L6_LAYOUT_DECL_MISSING): senza
#       layout la lingua degrada in silenzio al path lento.
#   (b) MARKER — un template che dichiara `layout: static_first` DEVE
#       contenere il marker-commento `{# STATIC-END ... #}`
#       (L6_MARKER_MISSING).
#   (c) PREFISSO PURO — PRIMA del marker: nessuna interpolazione `{{ var }}`
#       con var fuori da `_L6_CONST_RENDER_VARS` (L6_VAR_BEFORE_MARKER) e
#       nessuno statement `{% ... %}` (L6_STMT_BEFORE_MARKER). I commenti
#       Jinja `{# ... #}` sono esclusi dalla scansione (non renderizzano).
#   (d) QUERY NELLA CODA — per i role ANCHOR, `{{ user_query }}` DEVE
#       comparire DOPO il marker (L6_QUERY_VAR_MISSING): con lo split la
#       coda È il messaggio user — senza la query il modello pianifica
#       alla cieca (regressione silenziosa di accuratezza).
#
# `_L6_CONST_RENDER_VARS`: variabili costanti a parità di processo/lingua
# (o di giornata: current_*) iniettate da `prompt_loader._default_vars` —
# non rompono l'identità byte del prefisso FRA query diverse.

_LAYOUT_STATIC_FIRST = "static_first"
_STATIC_FIRST_ANCHOR_ROLES = frozenset({"engine_proposer"})
_STATIC_END_MARKER_RE = re.compile(r"\{#-?\s*STATIC-END\b")
_L6_CONST_RENDER_VARS = frozenset({
    "lang", "lang_name", "current_year", "current_date", "install_root",
})
_JINJA_COMMENT_RE = re.compile(r"\{#.*?#\}", re.DOTALL)
_JINJA_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)")
_JINJA_STMT_RE = re.compile(r"\{%")
_USER_QUERY_VAR_RE = re.compile(r"\{\{\s*user_query\s*\}\}")


def _check_l6_static_first(path: Path, content: str) -> list[LintIssue]:
    """L6: layout static-first (contratto nel blocco commento sopra)."""
    issues: list[LintIssue] = []
    fields, _ = _parse_frontmatter(content)
    fields = fields or {}
    declared = fields.get("layout", "") == _LAYOUT_STATIC_FIRST
    role = fields.get("role", "")
    anchor = role in _STATIC_FIRST_ANCHOR_ROLES

    # (a) anchor: il role canonico non puo' opt-out (in nessuna lingua).
    if anchor and not declared:
        issues.append(LintIssue(
            file=str(path), line=0, level="error",
            code="L6_LAYOUT_DECL_MISSING",
            message=(f"role {role!r} DEVE dichiarare `layout: "
                     f"{_LAYOUT_STATIC_FIRST}` nel frontmatter "
                     "(ottimizzazione A prompt-cache: prefisso statico "
                     "condiviso, prompt_loader.get_split)"),
        ))
        return issues
    if not declared:
        return issues

    # (b) marker obbligatorio.
    m = _STATIC_END_MARKER_RE.search(content)
    if m is None:
        issues.append(LintIssue(
            file=str(path), line=0, level="error",
            code="L6_MARKER_MISSING",
            message=("`layout: static_first` dichiarato ma marker "
                     "`{# STATIC-END ... #}` assente: il confine "
                     "statico→variabile deve essere esplicito"),
        ))
        return issues

    # (c) prefisso puro: scandisci SOLO la parte prima del marker, escludendo
    # gli span commento (il marker stesso e' un commento: le {{ var }} citate
    # al suo interno non renderizzano).
    head = content[:m.start()]
    comment_spans = [c.span() for c in _JINJA_COMMENT_RE.finditer(head)]

    def _in_comment(pos: int) -> bool:
        return any(a <= pos < b for a, b in comment_spans)

    for vm in _JINJA_VAR_RE.finditer(head):
        if _in_comment(vm.start()):
            continue
        var = vm.group(1)
        if var in _L6_CONST_RENDER_VARS:
            continue
        line = head[:vm.start()].count("\n") + 1
        issues.append(LintIssue(
            file=str(path), line=line, level="error",
            code="L6_VAR_BEFORE_MARKER",
            message=(f"interpolazione variabile {{{{ {var} }}}} PRIMA del "
                     "marker STATIC-END: rompe il prefisso byte-identico "
                     "fra query (sposta il contenuto per-query DOPO il "
                     "marker)"),
        ))
    for sm in _JINJA_STMT_RE.finditer(head):
        if _in_comment(sm.start()):
            continue
        line = head[:sm.start()].count("\n") + 1
        issues.append(LintIssue(
            file=str(path), line=line, level="error",
            code="L6_STMT_BEFORE_MARKER",
            message=("statement Jinja `{% ... %}` PRIMA del marker "
                     "STATIC-END: il blocco statico deve essere testo puro "
                     "(output condizionale = prefisso non byte-identico)"),
        ))

    # (d) query nella coda (solo anchor): la coda diventa il messaggio user.
    if anchor:
        tail = content[m.start():]
        tail_clean = _JINJA_COMMENT_RE.sub("", tail)
        if not _USER_QUERY_VAR_RE.search(tail_clean):
            issues.append(LintIssue(
                file=str(path), line=0, level="error",
                code="L6_QUERY_VAR_MISSING",
                message=("`{{ user_query }}` assente DOPO il marker "
                         "STATIC-END: con lo split la coda e' il messaggio "
                         "user — senza query il modello pianifica alla "
                         "cieca"),
            ))
    return issues


# YAML section checks (asse B, 12/5/2026) ---------------------------------

_RULE_REQUIRED_KEYS = ("name", "when", "must", "must_not", "ok", "error")


def _check_yaml_section(path: Path, content: str) -> list[LintIssue]:
    """Lint completo per i `.yaml` schema-strutturati (asse B):
      - frontmatter top-level 8 campi (L1 YAML equivalente).
      - `section` dict.
      - planner sections: `rules` list con name/when/must/must_not/ok/error.
      - synt prompts (synt_*.yaml): schema piu' lasso (no rules richieste o
        rules con schema diverso name/body). Detect via path o `role`
        prefix `synt_`.

    Asse B extension synt (13/5/2026): i synt yaml hanno output strutturato
    JSON, NON pattern §6 (DEVI/NON DEVI). Saltano il check rules dettagliato.
    """
    issues: list[LintIssue] = []
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        issues.append(LintIssue(
            file=str(path), line=0, level="error", code="YL_YAML_PARSE",
            message=f"YAML non parsable: {exc}",
        ))
        return issues
    if not isinstance(data, dict):
        issues.append(LintIssue(
            file=str(path), line=0, level="error", code="YL_ROOT_TYPE",
            message="root YAML non e' un mapping/dict",
        ))
        return issues

    # L1-equivalente: 8 campi frontmatter come chiavi top-level.
    missing_fm = [k for k in _REQUIRED_FIELDS if k not in data]
    if missing_fm:
        issues.append(LintIssue(
            file=str(path), line=0, level="error", code="YL_FRONTMATTER_FIELDS",
            message=f"campi frontmatter mancanti: {sorted(missing_fm)}",
        ))
    lang = str(data.get("lang", ""))
    if lang and not _LANG_RE.match(lang):
        issues.append(LintIssue(
            file=str(path), line=0, level="error", code="YL_FRONTMATTER_LANG_INVALID",
            message=f"lang non valido: {lang!r}",
        ))
    style = str(data.get("style", ""))
    if style and style not in _STYLE_VALID:
        issues.append(LintIssue(
            file=str(path), line=0, level="error", code="YL_FRONTMATTER_STYLE_INVALID",
            message=f"style non valido: {style!r} (atteso uno di {_STYLE_VALID})",
        ))

    section = data.get("section")
    if not isinstance(section, dict):
        issues.append(LintIssue(
            file=str(path), line=0, level="error", code="YL_SECTION_MISSING",
            message="chiave `section` mancante o non dict",
        ))
    else:
        if not section.get("name"):
            issues.append(LintIssue(
                file=str(path), line=0, level="error", code="YL_SECTION_NAME",
                message="section.name mancante o vuoto",
            ))

    # Synt YAML (synt_naming/signature/tests/description/code/code_addendum_*):
    # schema diverso (definitional/few_shot), NON applica i check planner rules.
    role = str(data.get("role", ""))
    is_synt = role.startswith("synt_") or path.name.startswith("synt_")
    if is_synt:
        return issues  # frontmatter + section gia' validati.

    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        issues.append(LintIssue(
            file=str(path), line=0, level="error", code="YL_RULES_MISSING",
            message="chiave `rules` mancante, non lista o vuota",
        ))
        return issues

    seen_names: set[str] = set()
    for idx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            issues.append(LintIssue(
                file=str(path), line=0, level="error", code="YL_RULE_TYPE",
                message=f"rules[{idx}] non e' un dict",
            ))
            continue
        for k in _RULE_REQUIRED_KEYS:
            v = rule.get(k)
            if not isinstance(v, str) or not v.strip():
                issues.append(LintIssue(
                    file=str(path), line=0, level="error",
                    code="YL_RULE_FIELD_EMPTY",
                    message=f"rules[{idx}].{k} mancante o vuoto",
                ))
        name = rule.get("name")
        if isinstance(name, str) and name:
            if name in seen_names:
                issues.append(LintIssue(
                    file=str(path), line=0, level="error",
                    code="YL_RULE_NAME_DUPLICATE",
                    message=f"rules[{idx}].name duplicato: {name!r}",
                ))
            seen_names.add(name)
    return issues


def _check_l5_lang_symmetry(root: Path) -> list[LintIssue]:
    """L5: simmetria cross-lang. Ritorna issue per ogni file presente in
    `<langA>/...` ma mancante in `<langB>/...`. Considera la struttura split
    planner: `planner/_core.j2`, `planner/sections/mail.j2`, etc.

    Asse B (12/5/2026): considera sia `.j2` sia `.yaml` come fonti di
    simmetria. Una sezione conta UNA volta indipendentemente dal formato:
    se IT ha `calendar.yaml` ed EN ha `calendar.j2`, sono simmetrici (la
    coesistenza A/B e' tollerata durante il PoC).

    A differenza degli altri check (per-file), L5 e' uno scan globale: viene
    chiamato una volta sola dal driver `scan(root)`.
    """
    issues: list[LintIssue] = []
    langs = sorted(d.name for d in root.iterdir()
                    if d.is_dir() and not d.name.startswith("_"))
    if not langs:
        return []

    # Mappa lang -> set di path relativi (es. {"planner/_core", "vaglio", ...}).
    by_lang: dict[str, set[str]] = {}
    for lang in langs:
        lang_root = root / lang
        files = set()
        for pattern in ("*.j2", "*.yaml"):
            for p in lang_root.rglob(pattern):
                if "_pending" in p.parts:
                    continue
                rel = p.relative_to(lang_root)
                # Normalize: rimuovi estensione e usa POSIX-slash come chiave.
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
            issues.extend(_check_l6_static_first(p, content))
        # YAML sections (asse B, 12/5/2026).
        for p in sorted(lang_root.rglob("*.yaml")):
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
            issues.extend(_check_yaml_section(p, content))
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
