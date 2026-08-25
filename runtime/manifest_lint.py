"""manifest_lint.py — linter STRUTTURALE dei manifest executor (dev-tooling).

Il manifest e' la "scheda istruzioni" che l'LLM-medio (modello locale) legge per scegliere
e chiamare un tool (§2.5). Questo linter e' un correttore automatico di quelle
schede: deterministico (§7.9, zero LLM), rileva gli errori di FORMA che fanno
sbagliare l'LLM prima che la scheda vada in uso.

COME EVITA LA TRAPPOLA SEMANTICA SENZA "capire" la semantica
------------------------------------------------------------
Un linter deterministico NON puo' giudicare il significato. Quindi NON ci prova:
codifica invece gli INVARIANTI STRUTTURALI la cui violazione *causa* la trappola
semantica — le "ombre strutturali" dei bug. Esempi (tutti emersi 2/6):

  trappola semantica                          ombra strutturale deterministica
  ------------------------------------------  --------------------------------------
  disambiguazione non arriva all'LLM          il capitolo NON: sta oltre il 260° char
                                              (il Proposer taglia li') → C_BUDGET
  l'LLM chiede un arg auto-risolvibile        arg `runtime_resolved` ANCORA citato nel
  (get_inputs su spreadsheet_id)              testo visibile all'LLM → C_RESOLVED_HIDDEN
  l'LLM non sa scegliere fra 2 tool simili    affinity quasi identica fra due verbi
                                              diversi sullo stesso oggetto → C_AFFINITY
  l'LLM copia un arg inventato                il PATTERN usa un arg non nello schema
                                              → C_PATTERN_ARGS

Cio' che resta IRRIDUCIBILMENTE semantico (es. il bias del modello locale "metti"->write) NON
ha ombra strutturale affidabile: il linter NON lo decide — si astiene e lo lascia
al verifier LLM L6 (ADR 0114), che e' il livello giusto per la semantica. Linter
deterministico (forma) + L6 LLM (significato) sono complementari.

Severita': `error` (blocca i NUOVI/toccati), `warn` (solo segnala — §2.5 vieta il
refactor di massa dei vecchi). Uso: synt-admission + importer + CLI.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import AbstractSet, Literal, Mapping

_RUNTIME = Path(__file__).resolve().parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

try:
    from vocab import PRODUCER_VERBS, DESTRUCTIVE_VERBS  # noqa: E402
except Exception:  # pragma: no cover - fallback se vocab non importabile
    PRODUCER_VERBS = frozenset({"read", "find", "list", "get"})
    DESTRUCTIVE_VERBS = frozenset(
        {"move", "delete", "send", "write", "extract", "create", "share",
         "install", "run"})

# Il Proposer usa questo come budget MEDIO per tool; il pool redistribuisce lo
# spazio inutilizzato entro un hard cap. Il check per-manifest resta volutamente
# conservativo: oltre la media la visibilita' dipende dalla composizione pool.
# SoT delle regole/dimensioni manifest: `manifest_rules` (il "DNA"). Stesso
# modulo importato da proposer (render) e synt (generazione) → numeri allineati,
# zero drift. Il path runtime e' gia' reso importabile sopra: un secondo set di
# default qui ricreerebbe proprio il drift che questo modulo deve impedire.
from manifest_rules import (  # noqa: E402
    ARG_DESC_MAX,
    CHAPTERS,
    DESC_MAX,
    HEAD_MAX,
    RENDER_BUDGET as PROPOSER_DESC_BUDGET,
    UNIVERSAL_ARGS,
    render_head,
)

# Soglia Jaccard sopra la quale due executor con VERBO diverso sono "troppo
# simili" come affinity → l'LLM rischia di non disambiguare.
_AFFINITY_OVERLAP_WARN = 0.6

Severity = Literal["error", "warn"]
FindingScope = Literal["local", "parity", "global"]


@dataclass(frozen=True, slots=True)
class Finding:
    check: str
    severity: Severity
    scope: FindingScope
    message: str
    resource: str = "manifest"
    languages: tuple[str, ...] = ()
    evidence: Mapping[str, object] = field(default_factory=dict)

    def __str__(self) -> str:
        sev = "ERROR" if self.severity == "error" else "warn "
        return f"  [{sev}] {self.check}: {self.message}"


# --------------------------------------------------------------------------
# Parsing helper: estrae i 4 capitoli dalla lingua richiesta.
# --------------------------------------------------------------------------
def _localized_text(
    value: object, language: str, *, allow_flat: bool,
) -> str | None:
    if isinstance(value, Mapping):
        selected = value.get(language)
        return selected if isinstance(selected, str) and selected.strip() else None
    if allow_flat and isinstance(value, str) and value.strip():
        return value
    return None


def _localized_resource_tables(node: Mapping[str, object]):
    """Compatibility iterator over the canonical contract selectors."""
    from i18n_materializer import manifest_language_selectors

    yield from manifest_language_selectors(node).items()


def _without_pattern_chapter(desc: str) -> str:
    start = desc.find("PATTERN:")
    if start < 0:
        return desc
    end = desc.find("NON:", start + len("PATTERN:"))
    if end < 0:
        end = len(desc)
    return desc[:start] + desc[end:]


def _visible_to_llm(desc: str) -> str:
    """Usa lo stesso renderer canonico che costruisce il pool del Proposer."""
    return render_head(desc)


def _chapter_span(desc: str, name: str) -> str:
    """Testo di un capitolo (es. 'SCOPO:') fino al prossimo capitolo."""
    start = desc.find(name)
    if start < 0:
        return ""
    start += len(name)
    end = len(desc)
    for other in CHAPTERS:
        if other == name:
            continue
        p = desc.find(other, start)
        if 0 <= p < end:
            end = p
    return desc[start:end].strip()


def _chapter_problem(desc: str) -> tuple[str, Mapping[str, object]] | None:
    occurrences = {
        marker: tuple(match.start() for match in re.finditer(re.escape(marker), desc))
        for marker in CHAPTERS
    }
    invalid_counts = {
        marker: len(positions)
        for marker, positions in occurrences.items() if len(positions) != 1
    }
    if invalid_counts:
        return (
            "ogni capitolo SCOPO/PATTERN/NON/OUT deve comparire esattamente una volta",
            {"counts": invalid_counts},
        )
    ordered_positions = [occurrences[marker][0] for marker in CHAPTERS]
    if ordered_positions != sorted(ordered_positions):
        return (
            "capitoli fuori ordine (atteso SCOPO -> PATTERN -> NON -> OUT)",
            {},
        )
    return None


def _output_schema_text(manifest: dict) -> str:
    """Return the declared output schema in a representation-neutral form."""
    output = manifest.get("output") or {}
    if not isinstance(output, dict):
        return ""
    schema = output.get("schema_inline") or output.get("schema") or ""
    return str(schema).lower()


@dataclass(frozen=True, order=True, slots=True)
class PatternCall:
    callee: str
    keyword_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PatternAtoms:
    calls: tuple[PatternCall, ...]
    standalone_assignments: tuple[str, ...]
    operator_identifiers: tuple[str, ...]


class PatternSyntaxError(ValueError):
    pass


_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}"}


def _code_mask(text: str) -> str:
    """Blank quoted content and validate every delimiter in one pass."""
    chars = list(text)
    stack: list[str] = []
    quote: str | None = None
    escaped = False
    for index, char in enumerate(text):
        if quote is not None:
            chars[index] = " "
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            chars[index] = " "
        elif char in _OPEN_TO_CLOSE:
            stack.append(_OPEN_TO_CLOSE[char])
        elif char in ")]}":
            if not stack or stack.pop() != char:
                raise PatternSyntaxError(
                    f"delimitatore inatteso alla posizione {index}"
                )
    if quote is not None:
        raise PatternSyntaxError("stringa non chiusa")
    if stack:
        raise PatternSyntaxError(f"delimitatore non chiuso: atteso {stack[-1]}")
    return "".join(chars)


def _matching_paren(text: str, open_index: int) -> int:
    stack: list[str] = [")"]
    quote: str | None = None
    escaped = False
    for index in range(open_index + 1, len(text)):
        char = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char in _OPEN_TO_CLOSE:
            stack.append(_OPEN_TO_CLOSE[char])
        elif char in ")]}":
            if not stack or stack.pop() != char:
                raise PatternSyntaxError(
                    f"delimitatore inatteso alla posizione {index}"
                )
            if not stack:
                return index
    raise PatternSyntaxError("parentesi di chiamata non chiusa")


def _top_level_segments(body: str) -> tuple[str, ...]:
    segments: list[str] = []
    start = 0
    stack: list[str] = []
    quote: str | None = None
    escaped = False
    for index, char in enumerate(body):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char in _OPEN_TO_CLOSE:
            stack.append(_OPEN_TO_CLOSE[char])
        elif char in ")]}":
            if not stack or stack.pop() != char:
                raise PatternSyntaxError(
                    f"delimitatore inatteso alla posizione {index}"
                )
        elif char == "," and not stack:
            segments.append(body[start:index])
            start = index + 1
    if quote is not None or stack:
        raise PatternSyntaxError("argomento di chiamata non chiuso")
    segments.append(body[start:])
    return tuple(segments)


def scan_pattern(pattern: str) -> PatternAtoms:
    """Extract machine atoms from a PATTERN chapter without parsing prose."""
    masked = _code_mask(pattern)
    calls: list[PatternCall] = []
    call_spans: list[tuple[int, int]] = []
    # A machine call is deliberately the adjacent form ``name(``.  Allowing
    # whitespace here turns ordinary prose such as ``Google UI (link...)``
    # into a language-dependent pseudo-call (``UI`` in English, ``Google``
    # in Italian), which is exactly the kind of false block this scanner must
    # avoid.
    call_re = re.compile(rf"\b({_IDENTIFIER})\(")
    for match in call_re.finditer(masked):
        open_index = masked.find("(", match.end(1))
        close_index = _matching_paren(pattern, open_index)
        keywords: list[str] = []
        for segment in _top_level_segments(pattern[open_index + 1:close_index]):
            keyword = re.match(rf"\s*({_IDENTIFIER})\s*=(?!=)", segment)
            if keyword:
                keywords.append(keyword.group(1))
        if len(keywords) != len(set(keywords)):
            raise PatternSyntaxError(
                f"keyword duplicata nella chiamata {match.group(1)}"
            )
        calls.append(PatternCall(match.group(1), tuple(sorted(keywords))))
        call_spans.append((match.start(1), close_index + 1))

    outside_calls = list(masked)
    for start, end in call_spans:
        outside_calls[start:end] = " " * (end - start)
    outside = "".join(outside_calls)
    assignments = tuple(sorted(
        match.group(1)
        for match in re.finditer(rf"\b({_IDENTIFIER})\s*=(?!=)", outside)
    ))
    operators = tuple(sorted(
        "+".join(re.findall(_IDENTIFIER, match.group(0)))
        for match in re.finditer(
            rf"\b{_IDENTIFIER}(?:\s*\+\s*{_IDENTIFIER})+\b", outside,
        )
    ))
    return PatternAtoms(tuple(sorted(calls)), assignments, operators)


def _pattern_call_args(desc: str, name: str) -> list[str]:
    """Compatibility helper backed by the canonical scanner."""
    atoms = scan_pattern(_chapter_span(desc, "PATTERN:"))
    return [
        keyword
        for call in atoms.calls if call.callee == name
        for keyword in call.keyword_names
    ]


_RUNTIME_PLACEHOLDER = re.compile(r"\$\{RUNTIME:[^{}]+\}")


def _runtime_placeholders(text: str) -> tuple[str, ...]:
    matches = tuple(match.group(0) for match in _RUNTIME_PLACEHOLDER.finditer(text))
    remainder = _RUNTIME_PLACEHOLDER.sub("", text)
    if "${RUNTIME:" in remainder:
        raise ValueError("segnaposto runtime non chiuso")
    return tuple(sorted(matches))


def _template_placeholders(text: str) -> tuple[str, ...]:
    values: list[str] = []
    cursor = 0
    while True:
        start = text.find("{{", cursor)
        if start < 0:
            break
        end = text.find("}}", start + 2)
        if end < 0:
            raise ValueError("segnaposto template non chiuso")
        values.append(text[start + 2:end].strip())
        cursor = end + 2
    return tuple(sorted(values))


def _atoms_evidence(atoms: PatternAtoms) -> Mapping[str, object]:
    return {
        "calls": tuple(
            f"{call.callee}({','.join(call.keyword_names)})"
            for call in atoms.calls[:32]
        ),
        "assignments": atoms.standalone_assignments[:32],
        "operators": atoms.operator_identifiers[:32],
    }


def _local_placeholder_findings(
    text: str, *, resource: str, language: str,
) -> list[Finding]:
    findings: list[Finding] = []
    for check, extractor in (
        ("runtime_placeholders", _runtime_placeholders),
        ("template_placeholders", _template_placeholders),
    ):
        try:
            extractor(text)
        except ValueError as exc:
            findings.append(Finding(
                check, "error", "local", str(exc),
                resource=resource, languages=(language,),
            ))
    return findings


def lint_contract_translation(
    source: str,
    translated: str,
    *,
    resource: str,
    source_language: str,
    target_language: str,
) -> list[Finding]:
    """Compare only deterministic machine invariants across two texts."""
    from i18n_registry import normalize_language

    source_lang = normalize_language(source_language)
    target_lang = normalize_language(target_language)
    languages = (source_lang, target_lang)
    findings: list[Finding] = []

    if resource == "description":
        for text, language in ((source, source_lang), (translated, target_lang)):
            chapter_problem = _chapter_problem(text)
            if chapter_problem is not None:
                message, evidence = chapter_problem
                findings.append(Finding(
                    "chapter_order", "error", "local", message,
                    resource=resource, languages=(language,), evidence=evidence,
                ))
        source_atoms = target_atoms = None
        for text, language, holder in (
            (source, source_lang, "source"),
            (translated, target_lang, "target"),
        ):
            try:
                atoms = scan_pattern(_chapter_span(text, "PATTERN:"))
            except PatternSyntaxError as exc:
                findings.append(Finding(
                    "pattern_unparseable", "error", "local", str(exc),
                    resource=resource, languages=(language,),
                ))
                continue
            if holder == "source":
                source_atoms = atoms
            else:
                target_atoms = atoms
        if (
            source_atoms is not None
            and target_atoms is not None
            and source_atoms != target_atoms
        ):
            findings.append(Finding(
                "pattern_atoms", "error", "parity",
                "chiamate o argomenti macchina diversi fra le lingue",
                resource=resource, languages=languages,
                evidence={
                    "source": _atoms_evidence(source_atoms),
                    "target": _atoms_evidence(target_atoms),
                },
            ))

    for check, extractor in (
        ("runtime_placeholders", _runtime_placeholders),
        ("template_placeholders", _template_placeholders),
    ):
        extracted: list[tuple[str, ...] | None] = []
        for text, language in ((source, source_lang), (translated, target_lang)):
            try:
                extracted.append(extractor(text))
            except ValueError as exc:
                extracted.append(None)
                findings.append(Finding(
                    check, "error", "local", str(exc),
                    resource=resource, languages=(language,),
                ))
        if None not in extracted and extracted[0] != extracted[1]:
            findings.append(Finding(
                check, "error", "parity",
                "insieme dei segnaposto diverso fra le lingue",
                resource=resource, languages=languages,
                evidence={"source": extracted[0], "target": extracted[1]},
            ))
    return findings


# --------------------------------------------------------------------------
# I check
# --------------------------------------------------------------------------
def lint_manifest(
    manifest: Mapping[str, object],
    *,
    language: str,
    allow_flat_description: bool = False,
    catalog_names: AbstractSet[str] | None = None,
    sibling_affinities: Mapping[str, AbstractSet[str]] | None = None,
) -> list[Finding]:
    """Linta UN manifest (gia' parsato da TOML). Ritorna lista di Finding.

    catalog_names: set dei nomi executor esistenti (per C_NON_REFS). Opzionale.
    sibling_affinities: dict {name: set(affinity_tokens)} degli altri executor
                        (per C_AFFINITY). Opzionale.
    """
    from i18n_registry import normalize_language

    requested_language = normalize_language(language)
    out: list[Finding] = []
    name = manifest.get("name", "?")
    name = str(name)
    verb = name.split("_")[0] if "_" in name else name
    desc = _localized_text(
        manifest.get("description"), requested_language,
        allow_flat=allow_flat_description,
    )
    if desc is None:
        out.append(Finding(
            "language_missing", "error", "local",
            f"description assente per la lingua richiesta '{requested_language}'",
            resource="description", languages=(requested_language,),
        ))
        desc = ""
    args_schema = manifest.get("args") or {}
    if not isinstance(args_schema, Mapping):
        args_schema = {}
    props = (args_schema.get("properties") or {})
    if not isinstance(props, Mapping):
        props = {}
    visible = _visible_to_llm(desc)
    out.extend(_local_placeholder_findings(
        desc, resource="description", language=requested_language,
    ))

    # C_CHAPTERS — i 4 capitoli presenti e in ordine.
    chapter_problem = _chapter_problem(desc)
    if chapter_problem is not None:
        chapter_message, chapter_evidence = chapter_problem
        out.append(Finding(
            "chapter_order", "error", "local",
            chapter_message,
            resource="description", languages=(requested_language,),
            evidence=chapter_evidence,
        ))

    # C_BUDGET — oltre il budget medio il pool elastico puo' recuperare spazio,
    # ma la visibilita' non e' garantita in un pool composto da molte teste
    # lunghe. WARN, non ERROR: e' uno smell di authoring, non un difetto fatale.
    pos_pattern = desc.find("PATTERN:")
    pos_non = desc.find("NON:")
    out_cut = desc.find("OUT:") if desc.find("OUT:") > 0 else len(desc)
    if 0 <= pos_pattern and pos_pattern >= PROPOSER_DESC_BUDGET:
        out.append(Finding("head_length", "warn", "local",
                           f"PATTERN: inizia al char {pos_pattern} > budget medio "
                           f"{PROPOSER_DESC_BUDGET}: dipende dal residuo del pool. Accorcia lo SCOPO.",
                           resource="description", languages=(requested_language,),
                           evidence={"position": pos_pattern, "limit": PROPOSER_DESC_BUDGET}))
    elif 0 <= pos_non < out_cut and pos_non >= PROPOSER_DESC_BUDGET:
        out.append(Finding("head_length", "warn", "local",
                           f"il capitolo NON: (char {pos_non}) supera il budget medio "
                           f"{PROPOSER_DESC_BUDGET}; il pool elastico puo' recuperarlo, ma non e' "
                           f"garantito. Tieni il boundary essenziale.",
                           resource="description", languages=(requested_language,),
                           evidence={"position": pos_non, "limit": PROPOSER_DESC_BUDGET}))

    # C_LENGTH — regole FISICHE §2.5: description = SOLO testa, niente coda.
    head = desc[:out_cut]
    if len(head) > HEAD_MAX:
        out.append(Finding("head_length", "warn", "local",
                           f"testa (inizio->OUT:) {len(head)} char > {HEAD_MAX}: accorcia "
                           f"SCOPO/PATTERN/NON (la macchina legge solo la testa).",
                           resource="description", languages=(requested_language,),
                           evidence={"length": len(head), "limit": HEAD_MAX}))
    if len(desc) > DESC_MAX:
        out.append(Finding("description_length", "warn", "local",
                           f"description {len(desc)} char > {DESC_MAX}: contiene CODA non-macchina → "
                           f"spostala in codice(.py)/[args].description/ADR (§2.5: nessuna coda).",
                           resource="description", languages=(requested_language,),
                           evidence={"length": len(desc), "limit": DESC_MAX}))
    for an, decl in props.items():
        if not isinstance(decl, Mapping):
            continue
        raw_ad = decl.get("description")
        resource = f"args.properties.{an}.description"
        ad = _localized_text(
            raw_ad, requested_language, allow_flat=allow_flat_description,
        )
        if raw_ad is not None and ad is None:
            out.append(Finding(
                "language_missing", "error", "local",
                f"{resource} assente per la lingua richiesta '{requested_language}'",
                resource=resource, languages=(requested_language,),
            ))
        elif ad is not None and len(ad) > ARG_DESC_MAX:
            out.append(Finding(
                "argument_description_length", "warn", "local",
                f"[{resource}].description {len(ad)} char > {ARG_DESC_MAX}: "
                f"1 frase + tipo + esempio + default.",
                resource=resource, languages=(requested_language,),
                evidence={"length": len(ad), "limit": ARG_DESC_MAX},
            ))
        if ad is not None:
            out.extend(_local_placeholder_findings(
                ad, resource=resource, language=requested_language,
            ))

    pattern_atoms: PatternAtoms | None = None
    if "PATTERN:" in desc:
        try:
            pattern_atoms = scan_pattern(_chapter_span(desc, "PATTERN:"))
        except PatternSyntaxError as exc:
            out.append(Finding(
                "pattern_unparseable", "error", "local", str(exc),
                resource="description", languages=(requested_language,),
            ))

    # C_PATTERN_ARGS — il PATTERN usa solo arg esistenti nello schema (+ universali).
    if pattern_atoms is not None:
        allowed = set(props.keys()) | UNIVERSAL_ARGS
        pattern_arguments = [
            keyword
            for call in pattern_atoms.calls if call.callee == name
            for keyword in call.keyword_names
        ]
        for a in pattern_arguments:
            if a not in allowed:
                out.append(Finding("pattern_unknown_arg", "error", "local",
                                   f"il PATTERN usa l'arg '{a}' che NON e' nello schema "
                                   f"(props: {sorted(props.keys())}). L'LLM lo copiera' e fallira'.",
                                   resource="description", languages=(requested_language,),
                                   evidence={"argument": a}))

    pattern_arguments = {
        keyword
        for call in (pattern_atoms.calls if pattern_atoms is not None else ())
        if call.callee == name
        for keyword in call.keyword_names
    }
    outside_pattern = _without_pattern_chapter(visible)
    for pname, spec in props.items():
        if not isinstance(spec, Mapping) or not spec.get("runtime_resolved"):
            continue
        if pname in pattern_arguments:
            out.append(Finding(
                "runtime_arg_passed", "error", "local",
                f"l'arg runtime_resolved '{pname}' viene passato nel PATTERN",
                resource="description", languages=(requested_language,),
                evidence={"argument": str(pname)},
            ))
        if re.search(rf"`[^`]*\b{re.escape(str(pname))}\b[^`]*`|\b{re.escape(str(pname))}\s*=", outside_pattern):
            out.append(Finding(
                "runtime_arg_code_mention", "warn", "local",
                f"l'arg runtime_resolved '{pname}' è mostrato come codice fuori dal PATTERN",
                resource="description", languages=(requested_language,),
                evidence={"argument": str(pname)},
            ))

    # C_OUTPUT_SHAPE — output coerente col verbo (§3.3).
    # `entries`/`results` sono convenzioni SHOULD, non requisiti MUST: lo
    # standard permette esplicitamente output scalari/dialogo con campi
    # purpose-specific, purche' dichiarati nello schema. Il check resta utile
    # quando OUT e schema non dichiarano ne' la convenzione ne' un'alternativa.
    out_chap = _chapter_span(desc, "OUT:")
    if out_chap:
        low = out_chap.lower()
        schema = _output_schema_text(manifest)
        declared_shape = any(token in schema for token in ("entries", "results"))
        purpose_specific = bool(schema and schema.strip() not in ("{}", "{ ok: bool }"))
        if (verb in PRODUCER_VERBS and "entries" not in low
                and "results" not in low and not declared_shape
                and not purpose_specific):
            out.append(Finding("output_shape", "warn", "local",
                               f"verbo producer '{verb}' senza 'entries' ne' uno schema "
                               f"purpose-specific dichiarato (§3.3)",
                               resource="description", languages=(requested_language,)))
        elif (verb in (DESTRUCTIVE_VERBS - {"send"})
              and "results" not in low and not declared_shape
              and not purpose_specific):
            out.append(Finding("output_shape", "warn", "local",
                               f"verbo trasformativo '{verb}' senza 'results' ne' uno schema "
                               f"purpose-specific dichiarato (§3.3)",
                               resource="description", languages=(requested_language,)))

    # C_NON_REFS — i tool citati nel capitolo NON: esistono nel catalog.
    if catalog_names is not None:
        non_chap = _chapter_span(desc, "NON:")
        for ref in re.findall(r"\b([a-z][a-z0-9]+_[a-z0-9_]+)\b", non_chap):
            if "_" in ref and ref != name and ref not in catalog_names:
                # filtra falsi positivi ovvi (parole_con_underscore non-tool)
                if ref.split("_")[0] in (PRODUCER_VERBS | DESTRUCTIVE_VERBS):
                    out.append(Finding("non_reference", "warn", "local",
                                       f"il capitolo NON cita '{ref}' che NON esiste nel catalog "
                                       f"(riferimento morto).",
                                       resource="description", languages=(requested_language,),
                                       evidence={"reference": ref}))

    # C_AFFINITY — sovrapposizione affinity con un fratello di VERBO diverso.
    if sibling_affinities:
        mine = {a.lower() for a in (manifest.get("affinity") or [])}
        if mine:
            for other_name, other_aff in sibling_affinities.items():
                if other_name == name:
                    continue
                other_verb = other_name.split("_")[0]
                if other_verb == verb:
                    continue  # stesso verbo: condividere e' normale
                inter = mine & other_aff
                union = mine | other_aff
                jac = len(inter) / len(union) if union else 0.0
                if jac >= _AFFINITY_OVERLAP_WARN:
                    out.append(Finding("affinity", "warn", "global",
                                       f"affinity {jac:.0%} sovrapposta a '{other_name}' (verbo "
                                       f"diverso): l'LLM rischia di non disambiguare. Aggiungi "
                                       f"termini-verbo distintivi.",
                                       evidence={"other": other_name, "overlap": jac}))
    return out


def lint_file(
    path: Path,
    *,
    language: str,
    catalog_names: AbstractSet[str] | None = None,
    sibling_affinities: Mapping[str, AbstractSet[str]] | None = None,
) -> list[Finding]:
    import tomllib
    with open(path, "rb") as fh:
        manifest = tomllib.load(fh)
    return lint_manifest(manifest, language=language, catalog_names=catalog_names,
                         sibling_affinities=sibling_affinities)


def _load_all_affinities() -> dict:
    """Compatibility view backed by the shared neutral inventory."""
    import tomllib
    from manifest_inventory import inventory_authoring_manifests

    out = {}
    for ref in inventory_authoring_manifests().manifests:
        try:
            with ref.manifest_path.open("rb") as handle:
                m = tomllib.load(handle)
            out[ref.name] = {
                str(value).lower() for value in (m.get("affinity") or [])
            }
        except Exception:
            continue
    return out


def _load_catalog_names(affinities: dict | None = None) -> set[str]:
    """Compatibility view of names from the shared neutral inventory."""
    from manifest_inventory import inventory_authoring_manifests

    names = set((affinities or {}).keys())
    names.update(ref.name for ref in inventory_authoring_manifests().manifests)
    return names


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="manifest authoring esplicito")
    parser.add_argument("-a", "--all", action="store_true",
                        help="controlla tutto l'inventario authoring")
    parser.add_argument("--language", metavar="BCP47",
                        help="controlla soltanto questa lingua esplicita")
    # --strict: gate per NUOVI/TOCCATI — promuove ogni warn a error (CI / on-touch).
    # Senza, i warn restano advisory (legacy non bloccati, §2.5 no bonifica di massa).
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if args.all and args.path:
        parser.error("path e --all sono alternativi")
    requested_language: str | None = None
    if args.language:
        from i18n_registry import normalize_language

        try:
            requested_language = normalize_language(args.language)
        except ValueError as exc:
            parser.error(str(exc))

    strict = args.strict
    from manifest_inventory import (
        ManifestOrigin,
        ManifestSource,
        inventory_authoring_manifests,
    )

    explicit_audit = args.path is not None
    if explicit_audit:
        explicit = Path(args.path)
        inventory = inventory_authoring_manifests((ManifestSource(
            ManifestOrigin.EXPLICIT, explicit.parent,
            min_depth=0, max_depth=0,
            allowed_code_roots=(explicit.parent,),
        ),))
    else:
        # Questo comando e' un audit OFFLINE delle sorgenti di authoring. Non
        # certifica il catalogo live post-cutover: quel confine deve consumare
        # esclusivamente snapshot verificati dal contract store.
        inventory = inventory_authoring_manifests()
    parsed_manifests = {}
    affinities: dict[str, set[str]] = {}
    names: set[str] = set()
    import tomllib
    for ref in inventory.manifests:
        with ref.manifest_path.open("rb") as handle:
            manifest = tomllib.load(handle)
        parsed_manifests[ref.contract_id] = manifest
        names.add(ref.name)
        affinities[ref.name] = {
            str(item).lower() for item in (manifest.get("affinity") or [])
        }
    for problem in inventory.problems:
        print(f"inventory [{problem.code}] {problem.path}: {problem.detail}")
    total_err = total_warn = 0
    checked = 0
    for ref in inventory.manifests:
        manifest = parsed_manifests[ref.contract_id]
        description = manifest.get("description")
        localized_tables = tuple(_localized_resource_tables(manifest))
        flat_description = isinstance(description, str)
        allow_flat = bool(
            flat_description and explicit_audit and requested_language
        )
        if requested_language is not None and not (
            flat_description and not explicit_audit
        ):
            languages = [requested_language]
        elif not flat_description:
            languages = sorted({
                str(language)
                for _, table in localized_tables
                for language, value in table.items()
                if isinstance(language, str) and isinstance(value, str)
            })
        else:
            languages = []

        if not languages:
            checked += 1
            reason = (
                "description flat priva di lingua: per un file esplicito usa "
                "--language <BCP47>"
                if flat_description else
                "description senza alcuna variante linguistica esplicita"
            )
            finding = Finding(
                "language_missing", "error", "local", reason,
                resource="description",
            )
            total_err += 1
            print(
                f"{ref.name} [lingua non attribuita; {ref.origin.value}; "
                f"{ref.status.value}]:"
            )
            print(finding)
        for index, language in enumerate(languages):
            checked += 1
            findings = lint_manifest(
                manifest, language=language,
                allow_flat_description=allow_flat,
                catalog_names=names,
                sibling_affinities=affinities if index == 0 else None,
            )
            errs = [f for f in findings if f.severity == "error"]
            warns = [f for f in findings if f.severity == "warn"]
            total_err += len(errs)
            total_warn += len(warns)
            if findings:
                print(
                    f"{ref.name} [{language}; {ref.origin.value}; "
                    f"{ref.status.value}]:"
                )
                for f in findings:
                    print(f)
        from itertools import combinations
        for resource, table in localized_tables:
            variants = sorted(
                (str(language), text)
                for language, text in table.items()
                if isinstance(language, str) and isinstance(text, str)
            )
            for (source_language, source), (target_language, target) in combinations(
                variants, 2,
            ):
                if (
                    requested_language is not None
                    and requested_language not in {source_language, target_language}
                ):
                    continue
                parity = [
                    finding for finding in lint_contract_translation(
                        source, target, resource=resource,
                        source_language=source_language,
                        target_language=target_language,
                    )
                    if finding.scope == "parity"
                ]
                total_err += sum(item.severity == "error" for item in parity)
                total_warn += sum(item.severity == "warn" for item in parity)
                if parity:
                    print(
                        f"{ref.name} [{source_language}↔{target_language}; "
                        f"{ref.origin.value}; {resource}]:"
                    )
                    for finding in parity:
                        print(finding)
    print(f"\n=== manifest_lint: {total_err} error, {total_warn} warn "
          f"su {checked} varianti di {len(inventory.manifests)} manifest; "
          f"{len(inventory.problems)} problemi inventario ===")
    return 1 if total_err or (strict and total_warn) else 0


if __name__ == "__main__":
    sys.exit(main())
