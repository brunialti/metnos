"""manifest_lint.py — linter STRUTTURALE dei manifest executor (dev-tooling).

Il manifest e' la "scheda istruzioni" che l'LLM-medio (Gemma) legge per scegliere
e chiamare un tool (§2.5). Questo linter e' un correttore automatico di quelle
schede: deterministico (§7.9, zero LLM), beccca gli errori di FORMA che fanno
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

Cio' che resta IRRIDUCIBILMENTE semantico (es. il bias di Gemma "metti"->write) NON
ha ombra strutturale affidabile: il linter NON lo decide — si astiene e lo lascia
al verifier LLM L6 (ADR 0114), che e' il livello giusto per la semantica. Linter
deterministico (forma) + L6 LLM (significato) sono complementari.

Severita': `error` (blocca i NUOVI/toccati), `warn` (solo segnala — §2.5 vieta il
refactor di massa dei vecchi). Uso: synt-admission + importer + CLI.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

try:
    from vocab import PRODUCER_VERBS, DESTRUCTIVE_VERBS  # noqa: E402
except Exception:  # pragma: no cover - fallback se vocab non importabile
    PRODUCER_VERBS = frozenset({"read", "find", "list", "get"})
    DESTRUCTIVE_VERBS = frozenset(
        {"move", "delete", "send", "write", "extract", "create", "share"})

# Il Proposer (engine/proposer.py::_render_tool_pool) mostra all'LLM la
# description fino a "OUT:", troncata a questo numero di caratteri. Tutto cio'
# che sta oltre e' INVISIBILE all'LLM. SINGLE-SOURCE: importato dal proposer
# (fallback 260 se l'import e' indisponibile, es. CLI senza engine).
# SoT delle regole/dimensioni manifest: `manifest_rules` (il "DNA"). Stesso
# modulo importato da proposer (render) e synt (generazione) → numeri allineati,
# zero drift. Fallback ai default §2.5 se non importabile (CLI senza runtime).
try:
    from manifest_rules import (RENDER_BUDGET as PROPOSER_DESC_BUDGET,
                                HEAD_MAX, DESC_MAX, ARG_DESC_MAX)
except Exception:  # pragma: no cover
    PROPOSER_DESC_BUDGET, HEAD_MAX, DESC_MAX, ARG_DESC_MAX = 260, 240, 280, 160

# Arg "universali" di piping/runtime ammessi nel PATTERN anche se non sono
# nelle properties dichiarate (il runtime li gestisce: §4.1).
_UNIVERSAL_ARGS = frozenset({"from_step", "entries"})

# Soglia Jaccard sopra la quale due executor con VERBO diverso sono "troppo
# simili" come affinity → l'LLM rischia di non disambiguare.
_AFFINITY_OVERLAP_WARN = 0.6

# Marker che indicano la gestione CORRETTA di un arg runtime_resolved citato:
# la description dice all'LLM di OMETTERLO (non e' la trappola "use it").
_OMIT_MARKERS = (
    "ometti", "omit", "auto-rilevato", "auto-detected", "auto-detect",
    "non passare", "non specificare", "do not pass", "non serve", "risolto dal runtime",
)


@dataclass
class Finding:
    check: str
    severity: str  # "error" | "warn"
    message: str

    def __str__(self) -> str:
        sev = "ERROR" if self.severity == "error" else "warn "
        return f"  [{sev}] {self.check}: {self.message}"


# --------------------------------------------------------------------------
# Parsing helper: estrae i 4 capitoli dalla description (lingua canonica).
# --------------------------------------------------------------------------
_CHAPTERS = ("SCOPO:", "PATTERN:", "NON:", "OUT:")


def _description_text(manifest: dict) -> str:
    desc = manifest.get("description")
    if isinstance(desc, dict):
        return (desc.get("it") or desc.get("en") or
                next(iter(desc.values()), "") if desc else "")
    return desc or ""


def _visible_to_llm(desc: str) -> str:
    """Replica il taglio del Proposer: testo fino a 'OUT:' (escluso), cap 260."""
    cut = desc.find("OUT:")
    head = desc[:cut] if cut > 0 else desc
    return head[:PROPOSER_DESC_BUDGET]


def _chapter_span(desc: str, name: str) -> str:
    """Testo di un capitolo (es. 'SCOPO:') fino al prossimo capitolo."""
    start = desc.find(name)
    if start < 0:
        return ""
    start += len(name)
    end = len(desc)
    for other in _CHAPTERS:
        if other == name:
            continue
        p = desc.find(other, start)
        if 0 <= p < end:
            end = p
    return desc[start:end].strip()


def _pattern_call_args(desc: str, name: str) -> list[str]:
    """Nomi degli argomenti usati nelle CHIAMATE `name(...)` del capitolo PATTERN.
    Estrae SOLO dalle chiamate reali del tool (non dalla prosa 'ARGS: ...default=':
    falso positivo se si prende `\\w+=` da tutto il capitolo)."""
    pat = _chapter_span(desc, "PATTERN:")
    args: list[str] = []
    for m in re.finditer(rf"{re.escape(name)}\s*\(([^)]*)\)", pat):
        args += re.findall(r"(?:^|[(,\s])([a-zA-Z_]\w*)\s*=(?!=)", m.group(1))
    return args


# --------------------------------------------------------------------------
# I check
# --------------------------------------------------------------------------
def lint_manifest(manifest: dict, *, catalog_names=None,
                  sibling_affinities=None) -> list[Finding]:
    """Linta UN manifest (gia' parsato da TOML). Ritorna lista di Finding.

    catalog_names: set dei nomi executor esistenti (per C_NON_REFS). Opzionale.
    sibling_affinities: dict {name: set(affinity_tokens)} degli altri executor
                        (per C_AFFINITY). Opzionale.
    """
    out: list[Finding] = []
    name = manifest.get("name", "?")
    verb = name.split("_")[0] if "_" in name else name
    desc = _description_text(manifest)
    args_schema = manifest.get("args") or {}
    props = (args_schema.get("properties") or {})
    visible = _visible_to_llm(desc)

    # C_CHAPTERS — i 4 capitoli presenti e in ordine.
    positions = [(c, desc.find(c)) for c in _CHAPTERS]
    missing = [c for c, p in positions if p < 0]
    if missing:
        out.append(Finding("chapters", "warn",
                           f"capitoli mancanti {missing} (atteso SCOPO/PATTERN/NON/OUT §2.5)"))
    else:
        order = [p for _, p in positions]
        if order != sorted(order):
            out.append(Finding("chapters", "warn",
                               "capitoli fuori ordine (atteso SCOPO -> PATTERN -> NON -> OUT)"))

    # C_BUDGET — il Proposer mostra solo i primi 260 char (prima di OUT:). Tutto
    # WARN, non ERROR: l'evidenza 2/6 mostra che un SCOPO ricco + la lista-args
    # compensano un PATTERN/NON troncato (le query funzionavano lo stesso). E'
    # uno SMELL, non un difetto fatale — il linter lo segnala, non blocca synt.
    pos_pattern = desc.find("PATTERN:")
    pos_non = desc.find("NON:")
    out_cut = desc.find("OUT:") if desc.find("OUT:") > 0 else len(desc)
    if 0 <= pos_pattern and pos_pattern >= PROPOSER_DESC_BUDGET:
        out.append(Finding("budget", "warn",
                           f"PATTERN: inizia al char {pos_pattern} > {PROPOSER_DESC_BUDGET}: l'LLM "
                           f"vede a malapena la forma di chiamata. Accorcia lo SCOPO."))
    elif 0 <= pos_non < out_cut and pos_non >= PROPOSER_DESC_BUDGET:
        out.append(Finding("budget", "warn",
                           f"il capitolo NON: (char {pos_non}) e' oltre {PROPOSER_DESC_BUDGET} → "
                           f"troncato per l'LLM. OK solo se la disambiguazione e' gia' nello SCOPO."))

    # C_LENGTH — regole FISICHE §2.5: description = SOLO testa, niente coda.
    head = desc[:out_cut]
    if len(head) > HEAD_MAX:
        out.append(Finding("length", "warn",
                           f"testa (inizio->OUT:) {len(head)} char > {HEAD_MAX}: accorcia "
                           f"SCOPO/PATTERN/NON (la macchina legge solo la testa)."))
    if len(desc) > DESC_MAX:
        out.append(Finding("length", "warn",
                           f"description {len(desc)} char > {DESC_MAX}: contiene CODA non-macchina → "
                           f"spostala in codice(.py)/[args].description/ADR (§2.5: nessuna coda)."))
    for an, decl in props.items():
        if not isinstance(decl, dict):
            continue
        ad = decl.get("description")
        if isinstance(ad, dict):
            ad = ad.get("it") or ad.get("en") or ""
        if isinstance(ad, str) and len(ad) > ARG_DESC_MAX:
            out.append(Finding("length", "warn",
                               f"[args.{an}].description {len(ad)} char > {ARG_DESC_MAX}: "
                               f"1 frase + tipo + esempio + default."))

    # C_PATTERN_ARGS — il PATTERN usa solo arg esistenti nello schema (+ universali).
    if "PATTERN:" in desc and props:
        allowed = set(props.keys()) | _UNIVERSAL_ARGS
        for a in _pattern_call_args(desc, name):
            if a not in allowed:
                out.append(Finding("pattern_args", "error",
                                   f"il PATTERN usa l'arg '{a}' che NON e' nello schema "
                                   f"(props: {sorted(props.keys())}). L'LLM lo copiera' e fallira'."))

    # C_RESOLVED_HIDDEN — un arg runtime_resolved NON deve comparire nel testo
    # visibile all'LLM (SCOPO/PATTERN/NON): altrimenti l'LLM lo chiede comunque
    # (get_inputs). APPRENDIMENTO 2/6: serve toglierlo da args-list E dal testo.
    # ECCEZIONE: se la menzione e' in contesto di OMISSIONE («OMETTI client»,
    # «auto-rilevato») e' la gestione CORRETTA (dice all'LLM di NON passarlo) →
    # non flaggare. La distinzione use-vs-omit e' il punto: il check guarda i
    # marker di omissione vicino all'arg, non la sola presenza.
    for pname, spec in props.items():
        if isinstance(spec, dict) and spec.get("runtime_resolved"):
            m = re.search(rf"\b{re.escape(pname)}\b", visible)
            if m:
                ctx = visible[max(0, m.start() - 45): m.end() + 15].lower()
                if not any(mk in ctx for mk in _OMIT_MARKERS):
                    out.append(Finding("resolved_hidden", "error",
                                       f"arg '{pname}' e' runtime_resolved ma e' CITATO (in contesto "
                                       f"d'uso, non di omissione) nel testo visibile all'LLM → l'LLM "
                                       f"lo chiedera' (get_inputs). Toglilo o di' «OMETTI {pname}»."))

    # C_OUTPUT_SHAPE — output coerente col verbo (§2.6).
    out_chap = _chapter_span(desc, "OUT:")
    if out_chap:
        low = out_chap.lower()
        if verb in PRODUCER_VERBS and "entries" not in low:
            out.append(Finding("output_shape", "warn",
                               f"verbo producer '{verb}' ma OUT non menziona 'entries' (§2.6)"))
        elif verb in (DESTRUCTIVE_VERBS - {"send"}) and "results" not in low:
            out.append(Finding("output_shape", "warn",
                               f"verbo trasformativo '{verb}' ma OUT non menziona 'results' (§2.6)"))

    # C_NON_REFS — i tool citati nel capitolo NON: esistono nel catalog.
    if catalog_names is not None:
        non_chap = _chapter_span(desc, "NON:")
        for ref in re.findall(r"\b([a-z][a-z0-9]+_[a-z0-9_]+)\b", non_chap):
            if "_" in ref and ref != name and ref not in catalog_names:
                # filtra falsi positivi ovvi (parole_con_underscore non-tool)
                if ref.split("_")[0] in (PRODUCER_VERBS | DESTRUCTIVE_VERBS):
                    out.append(Finding("non_refs", "warn",
                                       f"il capitolo NON cita '{ref}' che NON esiste nel catalog "
                                       f"(riferimento morto)."))

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
                    out.append(Finding("affinity", "warn",
                                       f"affinity {jac:.0%} sovrapposta a '{other_name}' (verbo "
                                       f"diverso): l'LLM rischia di non disambiguare. Aggiungi "
                                       f"termini-verbo distintivi."))
    return out


def lint_file(path, *, catalog_names=None, sibling_affinities=None) -> list[Finding]:
    import tomllib
    with open(path, "rb") as fh:
        manifest = tomllib.load(fh)
    return lint_manifest(manifest, catalog_names=catalog_names,
                         sibling_affinities=sibling_affinities)


def _load_all_affinities() -> dict:
    import tomllib
    base = _RUNTIME.parent / "executors"
    out = {}
    for mt in base.glob("*/manifest.toml"):
        try:
            m = tomllib.load(open(mt, "rb"))
            out[m.get("name", mt.parent.name)] = {a.lower() for a in (m.get("affinity") or [])}
        except Exception:
            continue
    return out


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    # --strict: gate per NUOVI/TOCCATI — promuove ogni warn a error (CI / on-touch).
    # Senza, i warn restano advisory (legacy non bloccati, §2.5 no bonifica di massa).
    strict = "--strict" in argv
    argv = [a for a in argv if a != "--strict"]
    base = _RUNTIME.parent / "executors"
    affinities = _load_all_affinities()
    names = set(affinities.keys())
    if argv and argv[0] not in ("--all", "-a"):
        targets = [Path(argv[0])]
    else:
        targets = sorted(base.glob("*/manifest.toml"))
    total_err = total_warn = 0
    for t in targets:
        findings = lint_file(t, catalog_names=names, sibling_affinities=affinities)
        if strict:
            errs, warns = findings, []
        else:
            errs = [f for f in findings if f.severity == "error"]
            warns = [f for f in findings if f.severity == "warn"]
        total_err += len(errs)
        total_warn += len(warns)
        if findings:
            print(f"{t.parent.name}:")
            for f in findings:
                print(f)
    print(f"\n=== manifest_lint: {total_err} error, {total_warn} warn "
          f"su {len(targets)} manifest ===")
    return 1 if total_err else 0


if __name__ == "__main__":
    sys.exit(main())
