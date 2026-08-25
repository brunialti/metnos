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
    undo_outcome_contract: str
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
            undo_outcome_contract=str(
                (manifest.get("undo") or {}).get("outcome") or ""),
            reverse_patterns=reverse_patterns,
            platforms=tuple(str(p) for p in manifest.get("platforms") or ()),
            scope=str(placement.get("scope") or "any"),
            source_path=f"executors/{name}/",
        ))
    return entries


_TEXT = {
    "it": {
        "title": "Catalogo degli executor",
        "description": "Le azioni distribuite con Metnos, raggruppate per dominio e descritte direttamente dai loro manifest firmati.",
        "back": "Guida all'architettura",
        "other": "EN",
        "lead": "Questa pagina risponde a una domanda pratica: quali azioni sono fornite con Metnos e che cosa dichiara il contratto di ciascuna? Gli executor sono raggruppati automaticamente in base al loro nome canonico; l'elenco non viene ricopiato e riordinato a mano.",
        "generated": "L'elenco qui sotto proviene da {count} manifest firmati nella distribuzione. Non comprende le capacità interne al processo, gli executor aggiunti da skill o quelli creati nella singola installazione. Per vedere tutto ciò che l'istanza può usare in questo momento, apri <strong>Settings → Ciclo di vita → Executor</strong> nella chat web.",
        "concept": "Un executor può seguire una procedura diretta oppure adattare alcuni passi entro un <a href=\"intelligent_executors.html\">mandato ristretto</a>. In entrambi i casi conserva lo stesso contratto pubblico: scopo, argomenti, autorità, collocazione e forma del risultato.",
        "properties_explanation": "Nella colonna <strong>Contratto</strong>, <code>standard</code> e <code>critico</code> indicano la classe di rischio; <code>server</code>, <code>any</code> e le piattaforme indicano dove l'executor può essere collocato. Il trattino segnala che il manifest non limita esplicitamente la piattaforma.",
        "undo_title": "Quali azioni si possono annullare",
        "undo_explanation": "<p>L'annullamento non si applica nello stesso modo a tutte le azioni:</p><ul><li><strong>Annullabile</strong>: il manifest dichiara una procedura di ripristino. Alcuni executor stabiliscono l'annullabilità soltanto dopo l'esecuzione, perché dipende dall'effetto realmente prodotto e dalla ricevuta disponibile.</li><li><strong>Non annullabile</strong>: l'azione modifica lo stato, ma il contratto non offre un ripristino affidabile.</li><li><strong>Non applicabile</strong>: una lettura o un calcolo puro non lascia niente da ripristinare.</li></ul><p>La classificazione deriva dal contratto firmato e, quando previsto, dalla ricevuta della singola esecuzione. Metnos non deduce una procedura inversa dal nome dell'executor.</p>",
        "undo_question": "Il Tutor può usare questo catalogo per rispondere, per esempio: «Quali executor modificano file e quali di queste azioni posso annullare?»",
        "undoable_list": "Executor dichiarati annullabili",
        "undoable": "annullabile",
        "not_undoable": "non annullabile",
        "not_applicable": "non applicabile",
        "no_reverse": "nessuna procedura di ripristino dichiarata",
        "no_state": "nessuno stato utente da ripristinare",
        "reverse_pattern": "procedura di ripristino",
        "per_execution": "la ricevuta della singola esecuzione stabilisce l'annullabilità effettiva",
        "domain": "Dominio",
        "executor": "Executor",
        "purpose": "Scopo",
        "properties": "Contratto",
        "undo": "Annullamento",
        "source": "Percorso",
        "critical": "critico",
        "standard": "standard",
        "system": "sistema / più domini",
        "footer": "Fonte: manifest firmati sotto <code>executors/</code>. Rigenerazione dalla radice del repository: <code>./.venv/bin/python scripts/generate_executor_catalog.py</code>.",
    },
    "en": {
        "title": "Executor catalog",
        "description": "The actions distributed with Metnos, grouped by domain and described directly by their signed manifests.",
        "back": "Architecture guide",
        "other": "IT",
        "lead": "This page answers a practical question: which actions come with Metnos, and what does each contract declare? Executors are grouped automatically from their canonical names; nobody copies and rearranges this inventory by hand.",
        "generated": "The list below comes from {count} signed manifests in the distribution. It does not include in-process capabilities, executors added by skills, or executors created within one installation. To see everything the instance can use right now, open <strong>Settings → Lifecycle → Executors</strong> in web chat.",
        "concept": "An executor may follow a direct procedure or adapt some steps within a <a href=\"intelligent_executors.html\">narrow mandate</a>. Either way, it keeps the same public contract: purpose, arguments, authority, placement, and result shape.",
        "properties_explanation": "In the <strong>Contract</strong> column, <code>standard</code> and <code>critical</code> indicate the risk class; <code>server</code>, <code>any</code>, and the platform names show where the executor may run. A dash means that the manifest does not explicitly restrict the platform.",
        "undo_title": "Which actions can be undone",
        "undo_explanation": "<p>Undo does not apply to every action in the same way:</p><ul><li><strong>Undoable</strong>: the manifest declares a restoration procedure. Some executors can determine reversibility only after execution, because it depends on the effect that actually occurred and the receipt available.</li><li><strong>Not undoable</strong>: the action changes state, but its contract offers no reliable restoration.</li><li><strong>Not applicable</strong>: a read or pure computation leaves nothing to restore.</li></ul><p>The classification comes from the signed contract and, where required, the receipt for that particular execution. Metnos never invents an inverse from the executor's name.</p>",
        "undo_question": "Tutor can use this catalog to answer questions such as: “Which executors modify files, and which of those actions can I undo?”",
        "undoable_list": "Executors declared undoable",
        "undoable": "undoable",
        "not_undoable": "not undoable",
        "not_applicable": "not applicable",
        "no_reverse": "no restoration procedure declared",
        "no_state": "no user state to restore",
        "reverse_pattern": "restoration procedure",
        "per_execution": "the individual execution receipt determines actual reversibility",
        "domain": "Domain",
        "executor": "Executor",
        "purpose": "Purpose",
        "properties": "Contract",
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
            conditional = (
                f'<br/>{text["per_execution"]}'
                if entry.undo_outcome_contract == "per_execution" else "")
            detail = f'{text["reverse_pattern"]}: {patterns}{conditional}'
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
        f'{text["undo_explanation"]}\n'
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
<p>{text["properties_explanation"]}</p>
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
