#!/usr/bin/env python3
"""Generate the bilingual first-party source catalog, not admission evidence."""
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
        # Authoring metadata is not an admission proof. A new source has no
        # previous signature; only Birth may publish its first live contract.
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
        "description": "Le operazioni incluse in Metnos: scopo, requisiti e possibilità di annullamento, ricavati dalle schede tecniche degli executor.",
        "back": "Guida all'architettura",
        "other": "EN",
        "lead": "Un executor è il componente di Metnos che svolge una singola operazione. Questo catalogo descrive gli executor forniti con il prodotto: che cosa fanno, dove possono essere eseguiti e quali effetti si possono annullare.",
        "generated": "L’elenco viene generato da {count} schede tecniche, chiamate manifest, incluse nella distribuzione. Descrive le funzioni fornite, ma non garantisce che siano tutte utilizzabili nella tua installazione: ogni executor deve superare le verifiche di Executor Birth e avere servizi, credenziali e permessi necessari. Le funzioni interne di Metnos e gli executor aggiunti da skill o creati localmente non sono compresi nel conteggio. Per l’elenco effettivamente disponibile, apri <strong>Settings → Ciclo di vita → Executor</strong> nella chat web.",
        "initial_installation": "<strong>Prima installazione: procedura in collaudo.</strong> Su un’istanza nuova, l’installer può adottare il catalogo esatto di una distribuzione già accettata, verificandone integrità, struttura e autorizzazioni. Ogni executor riceve una ricevuta locale di adozione. La ricevuta non equivale a una nuova esecuzione delle prove di funzionamento. Componenti nuovi o modificati devono seguire le verifiche complete di Executor Birth.",
        "concept": "Alcuni executor seguono una procedura fissa; altri possono adattare alcuni passi entro <a href=\"intelligent_executors.html\">limiti prestabiliti</a>. Tutti devono rispettare il proprio contratto: scopo, dati accettati, permessi, luogo di esecuzione e risultato. La ricevuta finale distingue un’azione compiuta da una condizione già soddisfatta: un programma già chiuso, per esempio, non viene annunciato come appena chiuso.",
        "properties_explanation": "La colonna <strong>Contratto</strong> riporta la classe di rischio e il luogo di esecuzione. <code>standard</code> indica un’operazione ordinaria; <code>critico</code> segnala le maggiori restrizioni previste dal contratto. <code>server</code> indica il computer che ospita Metnos; <code>any</code> ammette anche altri dispositivi compatibili. Le piattaforme elencate precisano i sistemi supportati. Un trattino significa che la scheda tecnica non pone una restrizione esplicita sulla piattaforma.",
        "retirement": "L’amministratore può ritirare un executor con una procedura verificata: da quel momento non è più selezionabile per nuove operazioni. Il ritiro produce una ricevuta firmata e conserva codice storico, versioni e riferimenti necessari all’annullamento delle azioni precedenti. Non cancella la storia dell’executor.",
        "launch_consent_title": "Per quanto tempo autorizzi l'avvio di un programma",
        "geo_centre_title": "Dove cercare i luoghi più vicini",
        "geo_centre": "Per una ricerca vicino a te, Metnos usa la tua posizione disponibile; se chiedi vicino al server, usa invece quella del server. Nomi di città e coordinate esplicite restano il centro richiesto. Il nome del server non viene cercato come una città. Le distanze dipendono dalla precisione della posizione e dai risultati dei servizi geografici configurati; un servizio irraggiungibile non significa che non esistano luoghi vicini.",
        "launch_consent": "Quando autorizzi l’avvio di un programma, puoi scegliere una sola volta, fino al prossimo riavvio del PC oppure sempre, fino a revoca. Negli ultimi due casi, chiudere e riaprire il programma non richiede un altro consenso. Il permesso vale per quel programma su quel PC, anche passando dalla chat web a Telegram. Non autorizza l’avvio automatico, l’installazione o la chiusura forzata. Le conferme precedenti non diventano automaticamente permessi riutilizzabili. L’amministratore può revocare un permesso permanente dal registro dei permessi; questa funzione non ha ancora un pulsante nella chat.",
        "undo_title": "Quali azioni si possono annullare",
        "undo_explanation": "<p>La possibilità di annullare dipende dall’operazione e dal risultato:</p><ul><li><strong>Annullabile</strong>: il contratto prevede una procedura di ripristino. In alcuni casi la ricevuta dell’esecuzione deve confermare che l’effetto prodotto consenta davvero il ripristino.</li><li><strong>Non annullabile</strong>: l’operazione modifica dati o stato, ma non offre un ripristino affidabile.</li><li><strong>Non applicabile</strong>: una lettura o un calcolo non lascia nulla da ripristinare.</li></ul><p>Metnos ricava questa distinzione dal contratto firmato e, quando necessario, dalla ricevuta. Non inventa un’operazione inversa dal nome dell’executor.</p>",
        "undo_question": "Puoi chiedere al Tutor: «Quali operazioni modificano file e quali posso annullare?»",
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
        "footer": "Le schede tecniche si trovano in <code>executors/</code>. Il catalogo viene rigenerato da <code>scripts/generate_executor_catalog.py</code> prima della pubblicazione.",
    },
    "en": {
        "title": "Executor catalog",
        "description": "Operations included in Metnos: purpose, requirements, and undo support, taken from executor manifests.",
        "back": "Architecture guide",
        "other": "IT",
        "lead": "An executor is the Metnos component that performs a single operation. This catalog describes the executors supplied with the product: what they do, where they can run, and which effects can be undone.",
        "generated": "This list is generated from {count} technical descriptions, called manifests, included in the distribution. It describes supplied capabilities, but does not guarantee that all are usable on your installation: each executor must pass Executor Birth checks and have the required services, credentials, and permissions. Internal Metnos functions and executors added by skills or created locally are not included in the count. To see what is actually available, open <strong>Settings → Lifecycle → Executors</strong> in web chat.",
        "initial_installation": "<strong>First installation: procedure under test.</strong> On a new instance, the installer can adopt the exact catalog of an accepted distribution after checking integrity, structure, and authorization. Each executor receives a local adoption receipt. This receipt does not mean that its functional tests have been run again. New or modified components must follow the full Executor Birth checks.",
        "concept": "Some executors follow a fixed procedure; others can adapt certain steps within <a href=\"intelligent_executors.html\">predefined limits</a>. All must respect their contract: purpose, accepted data, permissions, execution location, and result. The final receipt distinguishes an action performed from a condition already satisfied: an application that was already closed, for example, is not announced as newly closed.",
        "properties_explanation": "The <strong>Contract</strong> column shows the risk class and execution location. <code>standard</code> denotes an ordinary operation; <code>critical</code> signals the stricter constraints set by the contract. <code>server</code> means the computer hosting Metnos; <code>any</code> also allows other compatible devices. Listed platforms specify supported systems. A dash means that the manifest does not explicitly restrict the platform.",
        "retirement": "Administrators can retire an executor through a verified procedure: it is then unavailable for new operations. Retirement produces a signed receipt and retains historical code, versions, and references needed to undo earlier actions. It does not erase the executor’s history.",
        "launch_consent_title": "How long you authorize launching an application",
        "geo_centre_title": "Where to search for nearby places",
        "geo_centre": "For a search near you, Metnos uses your available location; when you ask for places near the server, it uses the server's location instead. Explicit city names and coordinates remain the requested centre. The server name is not searched as a city. Distances depend on location accuracy and the configured geographic services' results; an unreachable service does not mean there are no nearby places.",
        "launch_consent": "When authorizing an application launch, choose once, until the PC restarts, or always until revoked. With either reusable choice, closing and reopening the application does not require another approval. Permission covers that application on that PC, including when switching between web chat and Telegram. It does not authorize automatic startup, installation, or forced closure. Previous approvals do not automatically become reusable permissions. An administrator can revoke permanent permission through the grant registry; this function does not yet have a chat button.",
        "undo_title": "Which actions can be undone",
        "undo_explanation": "<p>Undo support depends on the operation and its result:</p><ul><li><strong>Undoable</strong>: the contract provides a restoration procedure. In some cases, the execution receipt must confirm that the actual effect can be reversed.</li><li><strong>Not undoable</strong>: the operation changes data or state but offers no reliable restoration.</li><li><strong>Not applicable</strong>: a read or computation leaves nothing to restore.</li></ul><p>Metnos determines this from the signed contract and, when necessary, the receipt. It does not invent an inverse operation from the executor’s name.</p>",
        "undo_question": "Ask the Tutor: “Which operations modify files, and which can I undo?”",
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
        "footer": "Technical descriptions are stored in <code>executors/</code>. This catalog is regenerated by <code>scripts/generate_executor_catalog.py</code> before publication.",
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
<p>{text["initial_installation"]}</p>
<p>{text["concept"]}</p>
<p>{text["retirement"]}</p>
<h2 id="launch-consent">{text["launch_consent_title"]}</h2>
<p>{text["launch_consent"]}</p>
<h2 id="geo-centre">{text["geo_centre_title"]}</h2>
<p>{text["geo_centre"]}</p>
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
