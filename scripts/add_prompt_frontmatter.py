#!/usr/bin/env python3
"""add_prompt_frontmatter.py — Fase B3 (11/5/2026).

Aggiunge frontmatter (commento Jinja) ai file `runtime/prompts/<lang>/*.j2`
con 8 campi obbligatori: role, tier, lang, style, version, owner, updated,
sha_prev. Idempotente: re-run skip file gia' annotati.

Schema atteso (commento MiniJinja, ignorato dal renderer):

    {# ---
    role: planner
    tier: middle
    lang: it
    style: prescriptive
    version: 1
    owner: roberto
    updated: 2026-05-11
    sha_prev:
    --- #}

Uso:
    python scripts/add_prompt_frontmatter.py --dry-run
    python scripts/add_prompt_frontmatter.py --apply

Determinismo (the design guide §7.9): puro Python, hashing locale, niente LLM.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent / "runtime" / "prompts"
_TODAY = "2026-05-11"
_OWNER = "roberto"

# --- tier per role -----------------------------------------------------
# planner: middle (Gemma 4 26B think=true, ADR 0072 / §11)
# synt_code, synt_code_addendum_*: wise (stage 5 codegen, ADR §3 stage 5)
# synt_naming/signature/tests/description/generate/birth_tests: middle (procedurali)
# intent_extractor, vaglio: middle (ADR 0106 verdict, §11)
# describe_entries_*: auto (tier dipende dal volume entries — runtime decide)
# classify_entries: auto (idem)
# web_rerank: middle (BM25 + LLM rerank, ADR 0081)

_TIER_BY_ROLE = {
    "planner": "middle",
    "intent_extractor": "middle",
    "vaglio": "middle",
    "describe_entries_by_importance": "auto",
    "describe_entries_by_relevance": "auto",
    "describe_entries_compact": "auto",
    "classify_entries": "auto",
    "synt_naming": "middle",
    "synt_signature": "middle",
    "synt_tests": "middle",
    "synt_description": "middle",
    "synt_generate": "middle",
    "synt_birth_tests": "middle",
    "synt_code": "wise",
    "web_rerank": "middle",
}
# Addendum verbo: synt_code_addendum_<verb> → wise
_ADDENDUM_RE = re.compile(r"^synt_code_addendum_[a-z]+$")


# --- style per role ----------------------------------------------------
# Definito dalla guidance B3 + spot check leggendo i file.

_STYLE_BY_ROLE = {
    "planner": "prescriptive",
    "intent_extractor": "definitional",
    "vaglio": "definitional",
    "describe_entries_by_importance": "prescriptive",
    "describe_entries_by_relevance": "prescriptive",
    "describe_entries_compact": "prescriptive",
    "classify_entries": "prescriptive",
    "synt_naming": "definitional",
    "synt_signature": "definitional",
    "synt_tests": "definitional",
    "synt_description": "definitional",
    "synt_generate": "definitional",
    "synt_birth_tests": "definitional",
    "synt_code": "definitional",
    "web_rerank": "definitional",
}
# Addendum verbo: synt_code_addendum_<verb> → few_shot
_STYLE_ADDENDUM = "few_shot"

_FRONTMATTER_OPEN = "{# ---"
_FRONTMATTER_CLOSE = "--- #}"


def _tier_for(role: str) -> str:
    """Tier mapping con fallback wise per gli addendum."""
    if _ADDENDUM_RE.match(role):
        return "wise"
    return _TIER_BY_ROLE.get(role, "middle")


def _style_for(role: str) -> str:
    """Style mapping con fallback few_shot per gli addendum."""
    if _ADDENDUM_RE.match(role):
        return _STYLE_ADDENDUM
    return _STYLE_BY_ROLE.get(role, "prescriptive")


def _has_frontmatter(content: str) -> bool:
    """True se il primo non-blank line e' `{# ---` (frontmatter gia' presente).
    Conserva blank lines iniziali per essere robusti."""
    for line in content.splitlines():
        if not line.strip():
            continue
        return line.lstrip().startswith(_FRONTMATTER_OPEN)
    return False


def _frontmatter_block(*, role: str, tier: str, lang: str, style: str,
                       sha_prev: str) -> str:
    """Costruisce il blocco frontmatter Jinja (commento) deterministico."""
    return (
        f"{_FRONTMATTER_OPEN}\n"
        f"role: {role}\n"
        f"tier: {tier}\n"
        f"lang: {lang}\n"
        f"style: {style}\n"
        f"version: 1\n"
        f"owner: {_OWNER}\n"
        f"updated: {_TODAY}\n"
        f"sha_prev: {sha_prev}\n"
        f"{_FRONTMATTER_CLOSE}\n"
    )


def _process_file(p: Path, *, apply: bool) -> dict:
    """Processa un singolo file .j2. Ritorna {status, role, lang, ...}.

    Status:
      - skip_has_frontmatter: gia' annotato (idempotenza).
      - applied: frontmatter aggiunto.
      - would_apply: dry-run, nessuna scrittura.
    """
    content = p.read_text(encoding="utf-8")
    role = p.stem  # filename senza .j2
    # `lang` = nome della dir lingua immediata sotto `_BASE`. Per file flat
    # (prompts/it/foo.j2): parent = it. Per file split planner
    # (prompts/it/planner/_core.j2 o prompts/it/planner/sections/mail.j2):
    # bisogna risalire finche' parent.parent == _BASE.
    try:
        rel = p.relative_to(_BASE)
    except ValueError:
        rel = p
    lang = rel.parts[0] if rel.parts else p.parent.name
    if _has_frontmatter(content):
        return {"status": "skip_has_frontmatter", "path": str(p),
                "role": role, "lang": lang}
    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
    tier = _tier_for(role)
    style = _style_for(role)
    block = _frontmatter_block(role=role, tier=tier, lang=lang, style=style,
                               sha_prev=sha)
    new_content = block + content
    if apply:
        p.write_text(new_content, encoding="utf-8")
        action = "applied"
    else:
        action = "would_apply"
    return {"status": action, "path": str(p), "role": role, "lang": lang,
            "tier": tier, "style": style, "sha_prev": sha}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="Stampa cosa verrebbe modificato senza scrivere.")
    parser.add_argument("--apply", action="store_true",
                        help="Applica le modifiche al filesystem.")
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        print("Specifica --dry-run o --apply.", file=sys.stderr)
        return 2

    if not _BASE.is_dir():
        print(f"ERRORE: {_BASE} non esiste.", file=sys.stderr)
        return 1

    n_applied = n_skip = n_total = 0
    for lang_dir in sorted(_BASE.iterdir()):
        if not lang_dir.is_dir() or lang_dir.name.startswith("_"):
            continue
        # `rglob("*.j2")` per coprire anche la struttura split planner
        # (Fase C, 11/5/2026): `prompts/<lang>/planner/*.j2` +
        # `prompts/<lang>/planner/sections/*.j2`. Skip `_pending/` (draft).
        for fp in sorted(lang_dir.rglob("*.j2")):
            if "_pending" in fp.parts:
                continue
            res = _process_file(fp, apply=args.apply)
            n_total += 1
            if res["status"] == "skip_has_frontmatter":
                n_skip += 1
                print(f"SKIP  {fp.relative_to(_BASE.parent.parent)} "
                      f"(frontmatter gia' presente)")
            else:
                n_applied += 1
                print(f"{res['status'].upper():12s} "
                      f"{fp.relative_to(_BASE.parent.parent)} "
                      f"[role={res['role']} tier={res['tier']} "
                      f"style={res['style']} lang={res['lang']} "
                      f"sha_prev={res['sha_prev']}]")
    print()
    print(f"Totale: {n_total} file. "
          f"Applicati/applicabili: {n_applied}. "
          f"Skip (gia' annotati): {n_skip}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
