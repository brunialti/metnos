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
from vocab import OBJECTS  # noqa: E402


@dataclass(frozen=True)
class ExecutorEntry:
    name: str
    domain: str
    descriptions: dict[str, str]
    critical: bool
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
        descriptions = manifest.get("description") or {}
        if not isinstance(descriptions, dict):
            descriptions = {"it": str(descriptions), "en": str(descriptions)}
        placement = manifest.get("placement") or {}
        entries.append(ExecutorEntry(
            name=name,
            domain=domain,
            descriptions={
                "it": _purpose(descriptions.get("it") or descriptions.get("en")),
                "en": _purpose(descriptions.get("en") or descriptions.get("it")),
            },
            critical=bool(manifest.get("critical")),
            platforms=tuple(str(p) for p in manifest.get("platforms") or ()),
            scope=str(placement.get("scope") or "any"),
            source_path=f"executors/{name}/",
        ))
    return entries


_TEXT = {
    "it": {
        "title": "Catalogo executor per dominio",
        "description": "Censimento degli executor first-party di Metnos, generato dai manifest firmati e raggruppato per dominio canonico.",
        "back": "Microprogettazione",
        "other": "EN",
        "lead": "Questo catalogo censisce gli executor first-party presenti nel sorgente. Il dominio deriva dall'oggetto canonico del nome executor; non e' una classificazione editoriale mantenuta a mano.",
        "generated": "Documento generato deterministicamente da {count} manifest firmati in <code>executors/</code>. Gli executor installati da skill o sintetizzati localmente appartengono al catalogo runtime della singola istanza e non sono pubblicati qui.",
        "concept": "Un executor puo' implementare una procedura diretta oppure essere un <a href=\"intelligent_executors.html\">executor intelligente a mandato ristretto</a>; il contratto pubblico e la collocazione nel dominio non cambiano.",
        "domain": "Dominio",
        "executor": "Executor",
        "purpose": "Scopo",
        "properties": "Proprieta'",
        "source": "Posizione",
        "critical": "critico",
        "standard": "standard",
        "system": "sistema / cross-domain",
        "footer": "Fonte: manifest firmati sotto <code>executors/</code>. Rigenerazione: <code>python3 scripts/generate_executor_catalog.py</code>.",
    },
    "en": {
        "title": "Executor catalog by domain",
        "description": "Inventory of Metnos first-party executors, generated from signed manifests and grouped by canonical domain.",
        "back": "Microdesign",
        "other": "IT",
        "lead": "This catalog inventories the first-party executors in the source tree. A domain is derived from the canonical object in the executor name; it is not a manually maintained editorial classification.",
        "generated": "This document is deterministically generated from {count} signed manifests under <code>executors/</code>. Executors installed from skills or synthesized locally belong to an instance's runtime catalog and are not published here.",
        "concept": "An executor may implement a direct procedure or be a <a href=\"intelligent_executors.html\">narrow-mandate intelligent executor</a>; its public contract and domain placement do not change.",
        "domain": "Domain",
        "executor": "Executor",
        "purpose": "Purpose",
        "properties": "Properties",
        "source": "Location",
        "critical": "critical",
        "standard": "standard",
        "system": "system / cross-domain",
        "footer": "Source: signed manifests under <code>executors/</code>. Regenerate with <code>python3 scripts/generate_executor_catalog.py</code>.",
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
                f"<td><code>{html.escape(entry.source_path)}</code></td>"
                "</tr>"
            )
        sections.append(
            f'<h2 id="domain-{html.escape(domain.lstrip("_"))}">'
            f'{text["domain"]}: <code>{html.escape(label)}</code> '
            f'({len(rows)})</h2>\n'
            "<table><thead><tr>"
            f'<th>{text["executor"]}</th><th>{text["purpose"]}</th>'
            f'<th>{text["properties"]}</th><th>{text["source"]}</th>'
            "</tr></thead><tbody>\n" + "\n".join(rows) +
            "\n</tbody></table>"
        )

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
<link rel="stylesheet" href="/assets/metnos.css"/></head>
<body><nav><a href="index.html">&larr; {text["back"]}</a> &middot; <a href="/{other}/architecture/executor_catalog.html" hreflang="{other}">{text["other"]}</a></nav>
<h1>{text["title"]}</h1>
<p class="lead">{text["lead"]}</p>
<div class="status">{generated}</div>
<p>{text["concept"]}</p>
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
