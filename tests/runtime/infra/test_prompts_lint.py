"""test_prompts_lint.py — copertura per `runtime/prompts_lint.py`.

Fase C4 (11/5/2026). 12+ test, uno per check con valid + invalid + edge case.
Determinismo (CLAUDE.md §7.9): niente LLM, fixture su filesystem temporaneo.
"""
from __future__ import annotations

import sys
from pathlib import Path



from prompts_lint import (  # noqa: E402
    LintIssue,
    _check_l1_frontmatter,
    _check_l2_hedge_blacklist,
    _check_l3_loc,
    _check_l4_trailing_newline,
    _check_l5_lang_symmetry,
    _check_l6_static_first,
    _parse_frontmatter,
    format_issue,
    scan,
)


# Helper: scrivi un .j2 di prova in dir temporanea.

def _write_prompt(dir_: Path, lang: str, role: str, body: str,
                   *, with_frontmatter: bool = True,
                   style: str = "prescriptive") -> Path:
    """Scrive un prompt fittizio per i test. Path: `<dir>/<lang>/<role>.j2`.
    Path subdir (es. "planner/_core") supportato."""
    p = dir_ / lang / f"{role}.j2"
    p.parent.mkdir(parents=True, exist_ok=True)
    if with_frontmatter:
        fm = (
            "{# ---\n"
            f"role: {role.replace('/', '_')}\n"
            "tier: middle\n"
            f"lang: {lang}\n"
            f"style: {style}\n"
            "version: 1\n"
            "owner: roberto\n"
            "updated: 2026-05-11\n"
            "sha_prev: abc12345\n"
            "--- #}\n"
        )
        content = fm + body
    else:
        content = body
    if not content.endswith("\n"):
        content += "\n"
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# L1: frontmatter
# ---------------------------------------------------------------------------

def test_l1_frontmatter_valid_passes(tmp_path):
    p = _write_prompt(tmp_path, "it", "valid", "body\n")
    issues = _check_l1_frontmatter(p, p.read_text())
    assert issues == []


def test_l1_frontmatter_missing_fails(tmp_path):
    p = _write_prompt(tmp_path, "it", "noFM", "just body\n",
                       with_frontmatter=False)
    issues = _check_l1_frontmatter(p, p.read_text())
    assert any(i.code == "L1_FRONTMATTER_MISSING" for i in issues)
    assert issues[0].level == "error"


def test_l1_frontmatter_missing_field(tmp_path):
    # Frontmatter presente ma manca `sha_prev`.
    content = (
        "{# ---\n"
        "role: x\n"
        "tier: middle\n"
        "lang: it\n"
        "style: prescriptive\n"
        "version: 1\n"
        "owner: roberto\n"
        "updated: 2026-05-11\n"
        "--- #}\n"
        "body\n"
    )
    p = tmp_path / "it" / "missing.j2"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    issues = _check_l1_frontmatter(p, content)
    assert any("sha_prev" in i.message and i.code == "L1_FRONTMATTER_FIELDS"
                for i in issues)


def test_l1_frontmatter_invalid_lang(tmp_path):
    content = (
        "{# ---\n"
        "role: x\n"
        "tier: middle\n"
        "lang: INVALID-LANG-VALUE!!\n"
        "style: prescriptive\n"
        "version: 1\n"
        "owner: roberto\n"
        "updated: 2026-05-11\n"
        "sha_prev: abc\n"
        "--- #}\n"
        "body\n"
    )
    p = tmp_path / "xx" / "test.j2"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    issues = _check_l1_frontmatter(p, content)
    assert any(i.code == "L1_FRONTMATTER_LANG_INVALID" for i in issues)


def test_l1_frontmatter_invalid_style(tmp_path):
    p = _write_prompt(tmp_path, "it", "badstyle", "body\n",
                       style="weirdcustom")
    issues = _check_l1_frontmatter(p, p.read_text())
    assert any(i.code == "L1_FRONTMATTER_STYLE_INVALID" for i in issues)


def test_l1_frontmatter_bcp47_lang_ok(tmp_path):
    p = _write_prompt(tmp_path, "zh-Hans", "test", "body\n")
    issues = _check_l1_frontmatter(p, p.read_text())
    assert not any(i.code == "L1_FRONTMATTER_LANG_INVALID" for i in issues)


# ---------------------------------------------------------------------------
# L2: hedge blacklist
# ---------------------------------------------------------------------------

def test_l2_hedge_prescriptive_fails(tmp_path):
    body = "DEVI: fare X.\nPreferibilmente fai Y.\n"  # "preferibilmente" hedge
    p = _write_prompt(tmp_path, "it", "hedgeful", body)
    issues = _check_l2_hedge_blacklist(p, p.read_text())
    assert len(issues) >= 1
    assert issues[0].code == "L2_HEDGE"
    assert "preferibilmente" in issues[0].message.lower()


def test_l2_hedge_matches_words_not_substrings(tmp_path):
    """«entry to keep» contiene «try to»: a sottostringa nuda un prompt
    corretto risultava in violazione, e il rilievo vero restava sepolto."""

    body = ("MUST NOT: Omit an entry to keep the answer shorter.\n"
            "MUST NOT: Try to guess a missing fact.\n")
    p = _write_prompt(tmp_path, "en", "boundary", body)
    issues = _check_l2_hedge_blacklist(p, p.read_text())
    assert [issue.line for issue in issues] == [2]


def test_l2_hedge_definitional_skipped(tmp_path):
    body = "Use cerca di approach when needed. If possible try to be flexible."
    p = _write_prompt(tmp_path, "it", "lax", body, style="definitional")
    issues = _check_l2_hedge_blacklist(p, p.read_text())
    # definitional NON soggetto a hedge check.
    assert issues == []


def test_l2_hedge_clean_passes(tmp_path):
    body = "DEVI: emettere tool_call.\nNON DEVI: scrivere riflessioni."
    p = _write_prompt(tmp_path, "it", "clean", body)
    issues = _check_l2_hedge_blacklist(p, p.read_text())
    assert issues == []


def test_l2_hedge_english_patterns(tmp_path):
    body = "MUST: do X.\nMUST NOT: try to do Y.\nIF POSSIBLE skip."
    p = _write_prompt(tmp_path, "en", "engl", body)
    issues = _check_l2_hedge_blacklist(p, p.read_text())
    # Doppio hit: "try to" + "if possible".
    assert len(issues) >= 2


# ---------------------------------------------------------------------------
# L3: LOC cap
# ---------------------------------------------------------------------------

def test_l3_loc_small_ok(tmp_path):
    body = "line\n" * 100
    p = _write_prompt(tmp_path, "it", "small", body)
    issues = _check_l3_loc(p, p.read_text())
    assert issues == []


def test_l3_loc_warn_threshold(tmp_path):
    body = "line\n" * 900  # 900 > 800 (warn) but <=1200 (no error)
    p = _write_prompt(tmp_path, "it", "warnish", body)
    issues = _check_l3_loc(p, p.read_text())
    assert len(issues) == 1
    assert issues[0].level == "warn"
    assert issues[0].code == "L3_LOC_WARN"


def test_l3_loc_error_threshold(tmp_path):
    body = "line\n" * 1500
    p = _write_prompt(tmp_path, "it", "huge", body)
    issues = _check_l3_loc(p, p.read_text())
    assert len(issues) == 1
    assert issues[0].level == "error"
    assert issues[0].code == "L3_LOC_ERROR"


# ---------------------------------------------------------------------------
# L4: trailing newline
# ---------------------------------------------------------------------------

def test_l4_trailing_newline_ok(tmp_path):
    p = _write_prompt(tmp_path, "it", "ok", "body line\n")
    issues = _check_l4_trailing_newline(p, p.read_text())
    assert issues == []


def test_l4_no_trailing_newline_fails(tmp_path):
    p = tmp_path / "it" / "bad.j2"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("body without newline")
    issues = _check_l4_trailing_newline(p, p.read_text())
    assert len(issues) == 1
    assert issues[0].code == "L4_TRAILING_NEWLINE"


def test_l4_empty_file_skipped(tmp_path):
    p = tmp_path / "it" / "empty.j2"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("")
    issues = _check_l4_trailing_newline(p, p.read_text())
    assert issues == []  # file vuoto coperto da L1, non L4


# ---------------------------------------------------------------------------
# L5: lang symmetry
# ---------------------------------------------------------------------------

def test_l5_symmetry_both_present_ok(tmp_path):
    _write_prompt(tmp_path, "it", "planner", "X\n")
    _write_prompt(tmp_path, "en", "planner", "Y\n")
    issues = _check_l5_lang_symmetry(tmp_path)
    assert issues == []


def test_l5_symmetry_missing_fails(tmp_path):
    _write_prompt(tmp_path, "it", "vaglio", "x\n")
    _write_prompt(tmp_path, "en", "vaglio", "y\n")
    # `synt_code` solo in IT.
    _write_prompt(tmp_path, "it", "synt_code", "z\n")
    issues = _check_l5_lang_symmetry(tmp_path)
    assert len(issues) >= 1
    assert issues[0].code == "L5_LANG_SYMMETRY"
    # Il file mancante atteso e' en/synt_code.j2.
    assert any("synt_code" in i.message for i in issues)
    assert any("en" in i.file or "/en/" in i.file for i in issues)


def test_l5_symmetry_split_planner(tmp_path):
    """Simmetria su path subdir (planner/_core, planner/sections/mail)."""
    _write_prompt(tmp_path, "it", "planner/_core", "core_it\n")
    _write_prompt(tmp_path, "it", "planner/sections/mail", "mail_it\n")
    _write_prompt(tmp_path, "en", "planner/_core", "core_en\n")
    # `planner/sections/mail` mancante in EN.
    issues = _check_l5_lang_symmetry(tmp_path)
    paths = " ".join(i.message for i in issues)
    assert "planner/sections/mail" in paths


def test_l5_symmetry_pending_skipped(tmp_path):
    """Files in `_pending/` non contano per simmetria."""
    _write_prompt(tmp_path, "it", "x", "x\n")
    _write_prompt(tmp_path, "en", "x", "x\n")
    # File draft in pending → ignorato.
    (tmp_path / "en" / "_pending").mkdir(parents=True, exist_ok=True)
    (tmp_path / "en" / "_pending" / "draft.j2").write_text("draft\n")
    issues = _check_l5_lang_symmetry(tmp_path)
    assert issues == []


# ---------------------------------------------------------------------------
# L6: layout static-first (ottimizzazione A prompt-cache, 10/6/2026)
# ---------------------------------------------------------------------------

def _static_first_fm(role: str = "engine_proposer", lang: str = "it",
                      layout: str | None = "static_first") -> str:
    layout_line = f"layout: {layout}\n" if layout else ""
    return (
        "{# ---\n"
        f"role: {role}\n"
        "tier: wise\n"
        f"lang: {lang}\n"
        "style: definitional\n"
        f"{layout_line}"
        "version: 1\n"
        "owner: roberto\n"
        "updated: 2026-06-10\n"
        "sha_prev: abc12345\n"
        "--- #}\n"
    )


_L6_MARKER = "{# STATIC-END — confine cache del prefisso. #}\n"


def test_l6_valid_static_first_passes(tmp_path):
    content = (_static_first_fm()
               + "REGOLE in {{ lang_name }}.\n"
               + _L6_MARKER
               + "POOL\n{{ tools }}\n- verbo: {{ verb }}\n"
               + "RICHIESTA UTENTE\n{{ user_query }}\n")
    p = tmp_path / "engine_proposer.j2"
    p.write_text(content)
    assert _check_l6_static_first(p, content) == []


def test_l6_var_before_marker_fails(tmp_path):
    content = (_static_first_fm()
               + "REGOLE.\nPOOL: {{ tools }}\n"
               + _L6_MARKER
               + "- verbo: {{ verb }}\n{{ user_query }}\n")
    p = tmp_path / "engine_proposer.j2"
    p.write_text(content)
    issues = _check_l6_static_first(p, content)
    assert any(i.code == "L6_VAR_BEFORE_MARKER" and i.level == "error"
               and "tools" in i.message for i in issues)
    # Line number puntato sulla riga dell'interpolazione (1-based nel file).
    assert issues[0].line == 13


def test_l6_stmt_before_marker_fails(tmp_path):
    content = (_static_first_fm()
               + "{% if compound %}REGOLE EXTRA{% endif %}\n"
               + _L6_MARKER
               + "{{ tools }}\n{{ user_query }}\n")
    p = tmp_path / "engine_proposer.j2"
    p.write_text(content)
    issues = _check_l6_static_first(p, content)
    assert any(i.code == "L6_STMT_BEFORE_MARKER" for i in issues)


def test_l6_marker_missing_fails(tmp_path):
    content = _static_first_fm() + "REGOLE.\n{{ tools }}\n"
    p = tmp_path / "engine_proposer.j2"
    p.write_text(content)
    issues = _check_l6_static_first(p, content)
    assert [i.code for i in issues] == ["L6_MARKER_MISSING"]


def test_l6_anchor_requires_declaration_every_lang(tmp_path):
    # engine_proposer SENZA layout: static_first → errore in OGNI lingua
    # (senza layout quella lingua degrada in silenzio al path lento).
    content = _static_first_fm(layout=None) + "REGOLE.\n{{ tools }}\n"
    p = tmp_path / "engine_proposer.j2"
    p.write_text(content)
    issues = _check_l6_static_first(p, content)
    assert [i.code for i in issues] == ["L6_LAYOUT_DECL_MISSING"]
    content_en = (_static_first_fm(lang="en", layout=None)
                  + "RULES.\n{{ tools }}\n")
    issues_en = _check_l6_static_first(p, content_en)
    assert [i.code for i in issues_en] == ["L6_LAYOUT_DECL_MISSING"]


def test_l6_undeclared_non_anchor_skipped(tmp_path):
    # Un prompt qualunque senza layout: static_first non e' soggetto a L6.
    content = _static_first_fm(role="planner", layout=None) + "{{ tools }}\n"
    p = tmp_path / "planner.j2"
    p.write_text(content)
    assert _check_l6_static_first(p, content) == []


def test_l6_vars_in_comment_and_const_whitelist_ok(tmp_path):
    # {{ verb }} citato in un COMMENTO prima del marker non renderizza →
    # nessun errore; {{ lang_name }}/{{ current_year }} sono costanti di
    # render → ammesse nel prefisso statico.
    content = (_static_first_fm()
               + "{# esempio nel commento: {{ verb }} non conta #}\n"
               + "Scrivi in {{ lang_name }} (anno {{ current_year }}).\n"
               + _L6_MARKER
               + "{{ tools }}\n{{ user_query }}\n")
    p = tmp_path / "engine_proposer.j2"
    p.write_text(content)
    assert _check_l6_static_first(p, content) == []


def test_l6_query_var_missing_in_tail_fails(tmp_path):
    # Anchor con coda SENZA {{ user_query }} → errore (la coda e' il
    # messaggio user: senza query il modello pianifica alla cieca). Una
    # citazione di user_query in un COMMENTO della coda non basta.
    content = (_static_first_fm()
               + "REGOLE.\n"
               + _L6_MARKER
               + "{{ tools }}\n{# manca {{ user_query }} reale #}\n")
    p = tmp_path / "engine_proposer.j2"
    p.write_text(content)
    issues = _check_l6_static_first(p, content)
    assert [i.code for i in issues] == ["L6_QUERY_VAR_MISSING"]
    # Role non-anchor con layout dichiarato: la coda libera e' ammessa.
    content_free = (_static_first_fm(role="web_rerank")
                    + "REGOLE.\n" + _L6_MARKER + "{{ tools }}\n")
    issues_free = _check_l6_static_first(p, content_free)
    assert issues_free == []


def test_l6_real_canonical_proposer_passes():
    # I template REALI del repo devono rispettare il contratto (anti-drift),
    # in entrambe le lingue.
    base = (Path(__file__).resolve().parents[3] / "runtime") / "prompts"
    for lang in ("it", "en"):
        real = base / lang / "engine_proposer.j2"
        content = real.read_text(encoding="utf-8")
        assert _check_l6_static_first(real, content) == [], lang


def test_l6_scan_wires_check(tmp_path):
    # scan() applica L6: var prima del marker rilevata via driver completo.
    content = (_static_first_fm()
               + "{{ tools }}\n"
               + _L6_MARKER
               + "{{ verb }}\n{{ user_query }}\n")
    p = tmp_path / "it" / "engine_proposer.j2"
    p.parent.mkdir(parents=True)
    p.write_text(content)
    issues = scan(tmp_path, langs=["it"])
    assert any(i.code == "L6_VAR_BEFORE_MARKER" for i in issues)


# ---------------------------------------------------------------------------
# Driver scan()
# ---------------------------------------------------------------------------

def test_scan_full_pipeline_clean(tmp_path):
    _write_prompt(tmp_path, "it", "valid", "DEVI: X.\n")
    _write_prompt(tmp_path, "en", "valid", "MUST: X.\n")
    issues = scan(tmp_path)
    assert issues == []


def test_scan_finds_mixed_issues(tmp_path):
    # 1 file con hedge + 1 file solo in IT.
    _write_prompt(tmp_path, "it", "hedge",
                   "DEVI: X.\nIf possible go fast.\n")
    _write_prompt(tmp_path, "en", "hedge",
                   "MUST: X.\nGo fast.\n")
    _write_prompt(tmp_path, "it", "only_it", "DEVI: X.\n")
    issues = scan(tmp_path)
    codes = {i.code for i in issues}
    assert "L2_HEDGE" in codes
    assert "L5_LANG_SYMMETRY" in codes


def test_scan_lang_filter(tmp_path):
    _write_prompt(tmp_path, "it", "x", "DEVI: X.\n")
    _write_prompt(tmp_path, "en", "x", "MUST: try to do.\n")  # hedge
    issues_it = scan(tmp_path, langs=["it"])
    assert all("it" in i.file for i in issues_it)
    issues_en = scan(tmp_path, langs=["en"])
    assert any(i.code == "L2_HEDGE" for i in issues_en)


def test_scan_all_keyword(tmp_path):
    _write_prompt(tmp_path, "it", "x", "DEVI: X.\n")
    _write_prompt(tmp_path, "en", "x", "MUST: X.\n")
    issues_all = scan(tmp_path, langs=["all"])
    # "all" interpretato come None → scan completo, niente issue.
    assert issues_all == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_parse_frontmatter_basic():
    content = (
        "{# ---\n"
        "role: planner\n"
        "tier: middle\n"
        "lang: it\n"
        "style: prescriptive\n"
        "version: 1\n"
        "owner: roberto\n"
        "updated: 2026-05-11\n"
        "sha_prev: deadbeef\n"
        "--- #}\n"
        "body\n"
    )
    fields, last = _parse_frontmatter(content)
    assert fields is not None
    assert fields["role"] == "planner"
    assert fields["sha_prev"] == "deadbeef"
    assert last == 10


def test_format_issue_human_readable():
    issue = LintIssue(
        file="/x/y.j2", line=5, level="error",
        code="L2_HEDGE", message="hedge: 'try to'",
    )
    s = format_issue(issue)
    assert "ERROR" in s
    assert "/x/y.j2:5" in s
    assert "L2_HEDGE" in s


def test_format_issue_warn():
    issue = LintIssue(file="/x.j2", line=0, level="warn",
                       code="L3_LOC_WARN", message="too long")
    s = format_issue(issue)
    assert "WARN" in s
    assert ":0" not in s  # line 0 omesso da format
