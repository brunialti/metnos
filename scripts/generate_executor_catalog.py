#!/usr/bin/env python3
"""Generate the bilingual first-party executor catalog from signed manifests."""
from __future__ import annotations

import argparse
import html
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXECUTORS_DIR = ROOT / "executors"
RUNTIME_DIR = ROOT / "runtime"
OUTPUTS = {
    "it": ROOT / "docs" / "it" / "architecture" / "executor_catalog.html",
    "en": ROOT / "docs" / "en" / "architecture" / "executor_catalog.html",
}

if str(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR))

from naming_grammar import parse_name  # noqa: E402
from vocab import OBJECTS, SAFE_VERBS  # noqa: E402


UNDOABLE = "undoable"
NOT_UNDOABLE = "not_undoable"
NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class ExecutorEntry:
    name: str
    verb: str | None
    domain: str
    descriptions: dict[str, str]
    critical: bool
    execution_effect: str
    undo_state: str
    reverse_patterns: tuple[str, ...]
    platforms: tuple[str, ...]
    scope: str
    source_path: str


def _purpose(value: object) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(r"^(SCOPO|PURPOSE)\s*:\s*", "", text,
                  flags=re.IGNORECASE)
    for marker in (" PATTERN:", " NON:", " OUT:"):
        if marker in text:
            text = text.split(marker, 1)[0]
    return text.strip().rstrip(".") + "." if text.strip() else ""


def _reverse_patterns(value: object) -> tuple[str, ...]:
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    if isinstance(value, list):
        patterns = tuple(str(item).strip() for item in value if str(item).strip())
        if patterns:
            return patterns
    return ()


def _undo_contract(*, name: str, verb: str | None,
                   manifest: dict) -> tuple[str, tuple[str, ...]]:
    """Classify undo from the canonical action and the signed contract.

    Undo is a three-state property.  A declared inverse wins; read-only and
    pure-compute canonical actions have no user state to restore; every other
    state-changing action without an inverse is explicitly non-undoable.
    Technical caches are implementation details and do not change the class of
    a safe canonical action.
    """

    patterns = _reverse_patterns(manifest.get("reverse_pattern"))
    revertible = bool(manifest.get("revertible"))
    if revertible:
        if not patterns:
            raise RuntimeError(
                f"revertible executor without reverse_pattern: {name}")
        return UNDOABLE, patterns
    if patterns:
        raise RuntimeError(
            f"non-revertible executor with reverse_pattern: {name}")
    execution = manifest.get("execution") or {}
    effect = (str(execution.get("effect") or "unknown")
              if isinstance(execution, dict) else "unknown")
    if effect == "read_only" or verb is None or verb in SAFE_VERBS:
        return NOT_APPLICABLE, ()
    return NOT_UNDOABLE, ()


def load_entries(executors_dir: Path = EXECUTORS_DIR) -> list[ExecutorEntry]:
    entries: list[ExecutorEntry] = []
    for manifest_path in sorted(executors_dir.glob("*/manifest.toml")):
        signature = manifest_path.with_name("manifest.toml.sig")
        if not signature.is_file():
            raise RuntimeError(f"unsigned executor manifest: {manifest_path}")
        with manifest_path.open("rb") as handle:
            manifest = tomllib.load(handle)
        name = str(manifest.get("name") or "").strip()
        if not name or name != manifest_path.parent.name:
            raise RuntimeError(f"invalid executor name in {manifest_path}")
        parsed = parse_name(name)
        domain = (parsed.obj if parsed and parsed.obj in OBJECTS
                  else "_system")
        verb = parsed.verb if parsed else None
        undo_state, reverse_patterns = _undo_contract(
            name=name, verb=verb, manifest=manifest)
        descriptions = manifest.get("description") or {}
        if not isinstance(descriptions, dict):
            descriptions = {"it": str(descriptions), "en": str(descriptions)}
        placement = manifest.get("placement") or {}
        entries.append(ExecutorEntry(
            name=name,
            verb=verb,
            domain=domain,
            descriptions={
                "it": _purpose(descriptions.get("it") or descriptions.get("en")),
                "en": _purpose(descriptions.get("en") or descriptions.get("it")),
            },
            critical=bool(manifest.get("critical")),
            execution_effect=str((manifest.get("execution") or {}).get(
                "effect") or "unknown"),
            undo_state=undo_state,
            reverse_patterns=reverse_patterns,
            platforms=tuple(str(p) for p in manifest.get("platforms") or ()),
            scope=str(placement.get("scope") or "any"),
            source_path=f"executors/{name}/",
        ))
    return entries


_TEXT = {
    "it": {
        "title": "Catalogo degli executor distribuiti per dominio",
        "description": "Censimento degli executor basati su file e distribuiti con Metnos, generato dai manifest firmati e raggruppato per dominio canonico.",
        "back": "Guida all'architettura",
        "other": "EN",
        "lead": "Questo catalogo censisce gli executor basati su file presenti in <code>executors/</code>. Il dominio deriva dall'oggetto canonico del nome; non è una classificazione editoriale mantenuta a mano.",
        "generated": "Documento generato in modo deterministico da {count} manifest firmati in <code>executors/</code>. Non comprende gli executor interni al processo, quelli installati da skill o quelli sintetizzati nella directory dati della singola istanza. Il catalogo completo in esercizio è visibile nella chat web in Settings → Ciclo di vita → Executor.",
        "concept": "Un executor può implementare una procedura diretta oppure essere un <a href=\"intelligent_executors.html\">executor intelligente a mandato ristretto</a>; il contratto pubblico e la collocazione nel dominio non cambiano.",
        "undo_title": "Censimento della possibilità di annullamento",
        "undo_explanation": "La possibilità di annullamento ha tre stati distinti. <strong>Annullabile</strong> significa che il manifest firmato dichiara <code>revertible = true</code> e uno o più <code>reverse_pattern</code>. <strong>Non annullabile</strong> significa che l'azione modifica stato, ma il contratto non dichiara un percorso inverso. <strong>Non applicabile</strong> identifica operazioni che non lasciano stato utente da ripristinare, riconosciute dall'effetto firmato <code>read_only</code> o dalla tassonomia canonica delle letture e dei calcoli puri. La classificazione deriva dai contratti firmati, non da un elenco editoriale di executor.",
        "undo_question": "Domanda verificabile dal Tutor: «Quali executor modificano file e, per ciascuno, l'annullamento è supportato, non supportato o non applicabile?»",
        "undoable_list": "Executor annullabili dichiarati",
        "undoable": "annullabile",
        "not_undoable": "non annullabile",
        "not_applicable": "non applicabile",
        "no_reverse": "nessun percorso inverso dichiarato",
        "no_state": "nessuno stato utente da ripristinare",
        "reverse_pattern": "percorso inverso",
        "domain": "Dominio",
        "executor": "Executor",
        "purpose": "Scopo",
        "properties": "Proprietà",
        "undo": "Annullamento",
        "source": "Percorso",
        "critical": "critico",
        "standard": "standard",
        "system": "sistema / tra domini",
        "footer": "Fonte: manifest firmati sotto <code>executors/</code>. Rigenerazione dalla radice del repository: <code>./.venv/bin/python scripts/generate_executor_catalog.py</code>.",
    },
    "en": {
        "title": "Distributed executor catalog by domain",
        "description": "Inventory of the file-based executors distributed with Metnos, generated from signed manifests and grouped by canonical domain.",
        "back": "Architecture guide",
        "other": "IT",
        "lead": "This catalog inventories the file-based executors under <code>executors/</code>. A domain is derived from the canonical object in the name; it is not a manually maintained editorial classification.",
        "generated": "This document is deterministically generated from {count} signed manifests under <code>executors/</code>. It excludes in-process executors, skill-installed executors, and executors synthesized in an instance's data directory. The complete live catalog is available in the web chat under Settings → Lifecycle → Executors.",
        "concept": "An executor may implement a direct procedure or be a <a href=\"intelligent_executors.html\">narrow-mandate intelligent executor</a>; its public contract and domain placement do not change.",
        "undo_title": "Undo applicability census",
        "undo_explanation": "Undo applicability has three distinct states. <strong>Undoable</strong> means the signed manifest declares <code>revertible = true</code> and one or more <code>reverse_pattern</code> values. <strong>Not undoable</strong> means the action changes state but its contract declares no inverse path. <strong>Not applicable</strong> identifies operations that leave no user state to restore, recognized from the signed <code>read_only</code> effect or the canonical taxonomy of reads and pure computations. Classification derives from signed contracts, not an editorial list of executors.",
        "undo_question": "A question the Tutor can verify: “Which executors modify files and, for each one, is undo supported, unsupported, or not applicable?”",
        "undoable_list": "Executors declared undoable",
        "undoable": "undoable",
        "not_undoable": "not undoable",
        "not_applicable": "not applicable",
        "no_reverse": "no inverse path declared",
        "no_state": "no user state to restore",
        "reverse_pattern": "inverse path",
        "domain": "Domain",
        "executor": "Executor",
        "purpose": "Purpose",
        "properties": "Properties",
        "undo": "Undo",
        "source": "Location",
        "critical": "critical",
        "standard": "standard",
        "system": "system / cross-domain",
        "footer": "Source: signed manifests under <code>executors/</code>. Regenerate from the repository root with <code>./.venv/bin/python scripts/generate_executor_catalog.py</code>.",
    },
}


def render(entries: list[ExecutorEntry], lang: str) -> str:
    text = _TEXT[lang]
    other = "en" if lang == "it" else "it"
    groups: dict[str, list[ExecutorEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.domain, []).append(entry)
    ordered_domains = [obj for obj in OBJECTS if obj in groups]
    if "_system" in groups:
        ordered_domains.append("_system")

    sections = []

    def undo_cell(entry: ExecutorEntry) -> str:
        label = text[entry.undo_state]
        if entry.undo_state == UNDOABLE:
            patterns = ", ".join(
                f"<code>{html.escape(pattern)}</code>"
                for pattern in entry.reverse_patterns)
            detail = f'{text["reverse_pattern"]}: {patterns}'
        elif entry.undo_state == NOT_UNDOABLE:
            detail = text["no_reverse"]
        else:
            detail = text["no_state"]
        return (
            f'<span data-undo-state="{entry.undo_state}">'
            f'<strong>{label}</strong><br/>{detail}</span>')

    for domain in ordered_domains:
        label = text["system"] if domain == "_system" else domain
        rows = []
        for entry in sorted(groups[domain], key=lambda item: item.name):
            kind = text["critical"] if entry.critical else text["standard"]
            platform = ", ".join(entry.platforms) or "-"
            properties = f"{kind}; {entry.scope}; {platform}"
            rows.append(
                "<tr>"
                f"<td><code>{html.escape(entry.name)}</code></td>"
                f"<td>{html.escape(entry.descriptions[lang])}</td>"
                f"<td>{html.escape(properties)}</td>"
                f"<td>{undo_cell(entry)}</td>"
                f"<td><code>{html.escape(entry.source_path)}</code></td>"
                "</tr>"
            )
        sections.append(
            f'<h2 id="domain-{html.escape(domain.lstrip("_"))}">'
            f'{text["domain"]}: <code>{html.escape(label)}</code> '
            f'({len(rows)})</h2>\n'
            "<table><thead><tr>"
            f'<th>{text["executor"]}</th><th>{text["purpose"]}</th>'
            f'<th>{text["properties"]}</th><th>{text["undo"]}</th>'
            f'<th>{text["source"]}</th>'
            "</tr></thead><tbody>\n" + "\n".join(rows) +
            "\n</tbody></table>"
        )

    undoable_entries = sorted(
        (entry for entry in entries if entry.undo_state == UNDOABLE),
        key=lambda item: item.name)
    undoable_names = " ".join(
        f'<code>{html.escape(entry.name)}</code>' for entry in undoable_entries)
    undo_counts = {
        state: sum(entry.undo_state == state for entry in entries)
        for state in (UNDOABLE, NOT_UNDOABLE, NOT_APPLICABLE)
    }
    undo_summary = (
        f'<h2 id="undo-census">{text["undo_title"]}</h2>\n'
        f'<p>{text["undo_explanation"]}</p>\n'
        f'<p><strong>{text["undo_question"]}</strong></p>\n'
        '<div class="status" data-undo-census="true">'
        f'{text["undoable"]}: {undo_counts[UNDOABLE]}; '
        f'{text["not_undoable"]}: {undo_counts[NOT_UNDOABLE]}; '
        f'{text["not_applicable"]}: {undo_counts[NOT_APPLICABLE]}.'
        '</div>\n'
        f'<h3>{text["undoable_list"]} ({len(undoable_entries)})</h3>\n'
        f'<p data-undoable-executors="true">{undoable_names}</p>')

    canonical = f"https://metnos.com/{lang}/architecture/executor_catalog"
    alternate = f"https://metnos.com/{other}/architecture/executor_catalog"
    generated = text["generated"].format(count=len(entries))
    return f'''<!DOCTYPE html>
<!-- Generated by scripts/generate_executor_catalog.py; do not edit manually. -->
<html lang="{lang}"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Metnos &mdash; {text["title"]}</title>
<meta name="description" content="{html.escape(text["description"], quote=True)}"/>
<link rel="canonical" href="{canonical}"/>
<link rel="alternate" hreflang="{lang}" href="{canonical}"/>
<link rel="alternate" hreflang="{other}" href="{alternate}"/>
<style>:root{{--n:#1A477A;--b:#2B6CB0;--g:#548235;--bg:#FAFBFC;--t:#1a1a1a;--bd:#d0d7de;--c:#f6f8fa}}*{{box-sizing:border-box}}body{{font-family:'Segoe UI',Calibri,sans-serif;color:var(--t);background:var(--bg);max-width:1180px;margin:auto;padding:40px 30px;line-height:1.55;font-size:11pt}}h1{{color:var(--n);font-size:22pt;border-bottom:3px solid var(--n);padding-bottom:10px}}h2{{color:var(--b);font-size:14pt;margin-top:30px;border-bottom:1px solid var(--bd);padding-bottom:5px}}a{{color:var(--n)}}code{{background:var(--c);padding:1px 5px;border-radius:3px}}.lead{{font-size:12pt;color:var(--n);border-left:4px solid var(--g);padding-left:14px}}.status{{background:#dcfce7;color:#14532d;border-left:5px solid #16a34a;padding:14px 20px}}table{{width:100%;border-collapse:collapse;background:#fff}}th{{background:var(--n);color:#fff;text-align:left}}th,td{{padding:8px 10px;border-bottom:1px solid var(--bd);vertical-align:top}}th:nth-child(1){{width:22%}}th:nth-child(3){{width:18%}}th:nth-child(4){{width:23%}}footer{{margin-top:45px;border-top:1px solid var(--bd);padding-top:15px;color:#64748B}}@media(max-width:760px){{body{{padding:24px 14px}}table,thead,tbody,tr,th,td{{display:block}}thead{{display:none}}tr{{border:1px solid var(--bd);margin-bottom:12px}}td{{border-bottom:0}}}}</style>
<link rel="stylesheet" href="/assets/metnos.css?v=20260822-2"/>
<script defer src="/assets/wiki-shell.js?v=20260822-2"></script></head>
<body><nav><a href="index.html">&larr; {text["back"]}</a> &middot; <a href="/{other}/architecture/executor_catalog.html" hreflang="{other}">{text["other"]}</a></nav>
<h1>{text["title"]}</h1>
<p class="lead">{text["lead"]}</p>
<div class="status">{generated}</div>
<p>{text["concept"]}</p>
{undo_summary}
{"\n".join(sections)}
<footer>{text["footer"]}</footer></body></html>
'''


def write_catalog(*, check: bool = False) -> bool:
    entries = load_entries()
    changed = False
    for lang, output in OUTPUTS.items():
        content = render(entries, lang)
        current = output.read_text(encoding="utf-8") if output.is_file() else ""
        if current == content:
            continue
        changed = True
        if not check:
            output.write_text(content, encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="fail if generated docs are stale")
    args = parser.parse_args()
    changed = write_catalog(check=args.check)
    if args.check and changed:
        print("executor catalog docs are stale", file=sys.stderr)
        return 1
    if not args.check:
        print(f"generated {len(load_entries())} executors in 2 locales")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
