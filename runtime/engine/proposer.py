"""engine/proposer.py — Protocol + SimpleProposer (default).

Il Proposer produce un Framework JSON dalla query+intent+pool tool. È
l'unico componente del Layer 3 che dipende dal LLM (a parte filler resolve
nell'Executor). Implementazione default: 1-shot Gemma wise tier con GBNF
strict.

Implementazioni alternative (file separati):
  - proposer_metis.py    → multi-strategia 2-3 alternative ranked telos (β)
  - proposer_frontier.py → Sonnet 4 API single call

Selettore via METNOS_ENGINE env. Swap zero-rewrite del resto del sistema.

§7.9: deterministic dispatcher (engine selector), LLM solo dentro
SimpleProposer.propose().
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional, Callable, Protocol, Sequence

from .types import Intent, Framework

log = logging.getLogger(__name__)


# ── Protocol ──────────────────────────────────────────────────────────────

class Proposer(Protocol):
    """Interface per qualunque proposer engine.

    Contratto:
      - propose() ritorna Framework valido o None se fallisce a generare.
      - Mai solleva eccezioni — return None su qualsiasi errore interno.
      - Deve rispettare excluded_hashes (set di framework_hash da NON
        riproporre, vedi recovery).
      - `catalog` opzionale: lista Executor per render tool schemas inline.
    """
    def propose(self, *, query: str, intent: Intent,
                pool: list[str], excluded_hashes: set[str],
                llm_call: Optional[Callable] = None,
                lang: str = "it",
                catalog: Optional[list] = None,
                exclude_tools: Sequence[str] = ()) -> Optional[Framework]: ...


# ── SimpleProposer (default) ──────────────────────────────────────────────

_THINK_CLOSED_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>", re.IGNORECASE)

# Budget di prompt PER-TOOL + logica di troncamento: SoT in `manifest_rules`
# (il "DNA"), così synt/lint/proposer condividono gli stessi numeri e la stessa
# regola di taglio. Description verbose distraggono il modello medio (§2.5:
# description = sola testa). `manifest_lint` e synt importano dalla stessa SoT.
try:
    from manifest_rules import RENDER_BUDGET as TOOL_DESC_BUDGET, render_head as _render_head
except Exception:  # pragma: no cover — CLI senza runtime sul path
    TOOL_DESC_BUDGET = 260

    def _render_head(desc):
        desc = (desc or "").strip().replace("\n", " ")
        if "PATTERN:" in desc:
            c = desc.find("OUT:")
            return (desc[:c] if c > 0 else desc)[:TOOL_DESC_BUDGET].strip()
        return desc.split(".")[0][:180].strip()


def _render_tool_pool(pool: list[str], catalog: Optional[list]) -> str:
    """Costruisce blocco tools con schema per il prompt.

    Per ogni tool: nome + descrizione 1-frase + required args + requires_one_of.
    Fallback a solo nome se catalog mancante.
    """
    if not catalog:
        return "\n".join(f"- {n}" for n in pool)
    from date_tokens import substitute_date_tokens  # §7.11: anni-esempio freschi
    cat_by_name = {getattr(e, "name", None): e for e in catalog}
    lines = []
    for name in pool:
        e = cat_by_name.get(name)
        if e is None:
            lines.append(f"- {name}")
            continue
        # Troncamento via SoT manifest_rules.render_head (DNA): testa §2.5 fino a
        # OUT: (cap RENDER_BUDGET) per i capitoli; prima frase ROBUSTA (cap
        # RENDER_LEGACY_MAX, non spezza a ".html") per i legacy in attesa di bonifica.
        desc_short = _render_head(substitute_date_tokens(getattr(e, "description", "") or ""))
        schema = getattr(e, "args_schema", None) or {}
        required = schema.get("required") or []
        roo = schema.get("requires_one_of") or []
        props_map = schema.get("properties") or {}
        # Arg di CONFIGURAZIONE (non intento) marcati `runtime_resolved`: NON
        # esposti all'LLM. Lesson A3/B1 (lessons_learned.md): l'enum di un arg
        # come `client`/`account`/`provider` induce un BIAS (il pattern vince
        # sul colloquiale "OMETTI") → il backend lo risolve il RUNTIME, non il
        # proposer. L'arg resta nello schema per validazione/umani/iniezione.
        props = [p for p in props_map.keys()
                 if not (isinstance(props_map.get(p), dict)
                         and props_map[p].get("runtime_resolved"))][:8]
        bits = [f"- {name}"]
        if desc_short:
            bits.append(f" — {desc_short}")
        if required:
            bits.append(f" [required: {','.join(required)}]")
        if roo:
            bits.append(f" [requires_one_of: {roo}]")
        if props:
            bits.append(f" args=[{','.join(props)}]")
        # §8.3 anti-invenzione: esponi gli enum degli arg così il Proposer
        # sceglie un valore valido invece di inventarlo (universal §7.3 — vale
        # per qualunque tool con enum: style, via_channel, ecc.).
        enum_bits = []
        for pname in props:
            decl = props_map.get(pname) or {}
            enum_vals = decl.get("enum")
            if enum_vals:
                vals = ",".join(str(v) for v in enum_vals[:8])
                enum_bits.append(f"{pname}∈{{{vals}}}")
        if enum_bits:
            bits.append(f" enums=[{'; '.join(enum_bits)}]")
        lines.append("".join(bits))
    return "\n".join(lines)


def _render_excluded_signal(excluded_hashes: set[str], lang: str = "it") -> str:
    """Segnale di DIVERSIFICAZIONE per la sezione «FRAMEWORK GIA RIFIUTATI»
    del prompt (var `excluded` dei template engine_proposer*.j2, invariati).

    B15: l'hash sha e' un token opaco che il modello IGNORA → il challenger
    (metis grammar-multi) e i retry (guard/validator/recovery) uscivano
    identici al piano escluso. Si rende invece la FORMA dei piani esclusi
    (sequenza tool + arg keys, via executor.framework_shape_for_hash) +
    istruzione esplicita §6. Deterministico §7.9: sorted, dedup stabile,
    nessun LLM; hash non risolvibili (es. anti_skills di processi passati)
    → conteggio onesto, mai sha grezzi nel prompt.
    """
    if not excluded_hashes:
        return "(nessuno)" if lang == "it" else "(none)"
    try:
        from .executor import framework_shape_for_hash
    except Exception:  # pragma: no cover — import circolare/CLI degradata
        framework_shape_for_hash = lambda h: None
    shapes: list[str] = []
    unresolved = 0
    for h in sorted(excluded_hashes):
        s = framework_shape_for_hash(h)
        if s:
            if s not in shapes:  # dedup: hash diversi, stessa forma → 1 riga
                shapes.append(s)
        else:
            unresolved += 1
    lines = [f"- {s}" for s in shapes]
    # Primi tool dei piani esclusi (ordine stabile): il vincolo CONCRETO
    # («NON ripartire da X») smuove il modello medio piu' del generico
    # «cambia qualcosa»; «oppure sequenza/argomenti diversi» lascia aperta
    # la via legittima del recovery wrong_args (stesso tool, args diversi).
    firsts: list[str] = []
    for s in shapes:
        ft = s.split(" → ", 1)[0].split("(", 1)[0]
        if ft and ft != "final_answer" and ft not in firsts:
            firsts.append(ft)
    quoted = ", ".join(f"«{f}»" for f in firsts)
    if lang == "it":
        if unresolved:
            lines.append(f"- {unresolved} altri piani gia' rifiutati "
                         "(forma non nota)")
        lines.append("DEVI: proporre un piano DIVERSO da quelli sopra"
                     + (f" — primo tool diverso (NON {quoted}), oppure "
                        "sequenza/argomenti diversi." if quoted else "."))
        lines.append("NON DEVI: riemettere un piano elencato sopra.")
        if shapes:
            lines.append(f"ERRORE: ripetere identico «{shapes[0]}».")
    else:
        if unresolved:
            lines.append(f"- {unresolved} more plans already rejected "
                         "(shape unknown)")
        lines.append("YOU MUST: propose a plan DIFFERENT from those above"
                     + (f" — different first tool (NOT {quoted}), or a "
                        "different sequence/arguments." if quoted else "."))
        lines.append("YOU MUST NOT: re-emit a plan listed above.")
        if shapes:
            lines.append(f"ERROR: repeating «{shapes[0]}» verbatim.")
    return "\n".join(lines)


def _strip_think(raw: str) -> str:
    """Rimuove i blocchi `<think>...</think>` CHIUSI dall'output LLM. Un
    `<think>` residuo e' per costruzione APERTO (B5: il troncamento a
    max_tokens lo lascia senza chiusura, oppure il modello omette il tag di
    chiusura): si rimuove il solo tag, cosi' il testo che segue resta
    visibile a `_iter_balanced_json_objects` — che ignora la prosa — e un
    framework emesso DOPO il think non chiuso viene comunque recuperato.
    Prima il think aperto restava intero nel raw e il parser pescava nel
    reasoning (o falliva sul JSON sporco)."""
    if not raw:
        return ""
    raw = _THINK_CLOSED_RE.sub("", raw)
    return _THINK_OPEN_RE.sub("", raw)


def _iter_balanced_json_objects(raw: str):
    """Generatore: yield ogni sottostringa `{...}` BILANCIATA di primo
    livello, rispettando le stringhe JSON e gli escape (una graffa dentro
    una stringa quotata NON altera la profondita'). Sostituisce le regex
    cieche alla profondita' (B4: il greedy `\\{[\\s\\S]*\\}` inglobava
    prosa/oggetti multipli; la regex a 1 livello di nesting perdeva i
    framework annidati steps→step→args). La prosa fra un oggetto e l'altro
    viene ignorata; un oggetto lasciato a meta' dal troncamento non viene
    mai emesso (profondita' mai richiusa)."""
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(raw):
        if in_string:
            # Dentro una stringa JSON: contano solo escape e chiusura.
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            # Le virgolette aprono una stringa JSON solo DENTRO un oggetto;
            # a profondita' 0 sono prosa (es. citazioni nel testo attorno).
            if depth > 0:
                in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                yield raw[start:i + 1]
                start = -1


def _parse_framework_json(raw: str) -> Optional[dict]:
    """Estrae il framework JSON dall'output LLM. Tollerante a prefissi e
    suffissi (`<think>` chiusi o aperti, prosa attorno). Itera gli oggetti
    `{...}` bilanciati e ritorna il PRIMO che parsa a dict CON chiave
    "steps"; altrimenti il primo dict; altrimenti None."""
    if not raw:
        return None
    raw = _strip_think(raw)
    first_dict: Optional[dict] = None
    for block in _iter_balanced_json_objects(raw):
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        if "steps" in parsed:
            return parsed
        if first_dict is None:
            first_dict = parsed
    return first_dict


class SimpleProposer:
    """Default: 1-shot Gemma wise + parse tollerante.

    Niente multi-strategia, niente telos ranking, niente preventive.
    Mētis-like minimal. Fallisce honest se LLM non genera framework JSON.
    """

    def __init__(self, *, prompt_loader: Optional[Callable] = None):
        """prompt_loader: callable (role, lang, **vars) -> str. None (default,
        produzione) usa `prompt_loader.get_split` (layout static_first,
        ottimizzazione A prompt-cache): testa statica → SYSTEM, coda
        per-query (intent/pool/excluded/query) → USER. Un loader INIETTATO
        (test) mantiene il contratto legacy: system=render completo,
        user=query."""
        self._load_prompt = prompt_loader

    def propose(self, *, query: str, intent: Intent,
                pool: list[str], excluded_hashes: set[str],
                llm_call: Optional[Callable] = None,
                lang: str = "it",
                catalog: Optional[list] = None,
                exclude_tools: Sequence[str] = ()) -> Optional[Framework]:
        if not query or llm_call is None:
            return None
        # Tier downgrade per intent high-confidence.
        # Bench 28/5/2026 (15q + 446q FROZEN): think=True NON aumenta ok%
        # rispetto a think=False. Soglia abbassata 0.85→0.70 per coprire
        # piu' query con la fast path (3-5s vs 25-30s).
        # Override via env METNOS_PROPOSER_FAST_CONFIDENCE.
        import os
        threshold = float(os.environ.get(
            "METNOS_PROPOSER_FAST_CONFIDENCE", "0.70"))
        use_fast = intent.confidence >= threshold

        # §7.3 GBNF grammar — DEFAULT ON (8/6/2026, decisione Roberto: routing
        # DETERMINISTICO §7.9). Forza think=False (ADR 0133: grammar+think
        # collidono): il reasoning think=True è non-deterministico vicino ai
        # confini (flip read_urls/find_dirs), e il seed da solo non basta a
        # stabilizzarlo (resta la varianza MTP sul reasoning lungo). grammar
        # (think=False) + seed fisso (llm_provider) → routing riproducibile.
        # Bench 28/5: think=True NON aumenta ok%. Disattiva: METNOS_PROPOSER_GRAMMAR=0.
        use_grammar = os.environ.get("METNOS_PROPOSER_GRAMMAR", "1") == "1"
        if use_grammar:
            use_fast = True  # force think=False

        # §7.3 Task #40 — Verb-aware pool filter (env METNOS_PROPOSER_VERB_FILTER=1)
        # Restringe pool ai tool che matchano intent.verb + universal helpers.
        # Pool 79 → 6-19 (90% reduction) → grammar GBNF molto più stretta +
        # LLM non puo' sbagliare verb family. Bench 446q baseline 47% top-1
        # prefilter → atteso 75%+ con verb constraint.
        effective_pool = pool
        # Compound-aware (§7.3): per query multi-azione (>=2 verbi canonici) il
        # filtro mono-verbo escluderebbe i tool degli altri sotto-intenti
        # (find+write+send) — il pool e' gia' multi-verbo da dispatch. Skip il
        # filtro sui compound, cosi' la pipeline completa resta proponibile
        # (bug 2/6/2026: "trova le issue, salvale, mandami il riassunto" perdeva
        # write_files/send_messages col verb-filter).
        # Compound signal PRIMARIO = decomposizione intent LLM (multilingue, no
        # dizionari di sinonimi): >=2 clausole {verb,object} → compound. Il
        # detector lessicale resta SOLO come fallback se l'LLM non ha decomposto
        # (es. "Prendi le issue ... mettile in un foglio" — "prendi"/"mettile"
        # non sono nel dizionario lessicale → mono-verbo falso → verb-filter
        # stripava create_files_spreadsheet, bug q21 4/6).
        _is_compound = len(getattr(intent, "actions", None) or []) >= 2
        if not _is_compound:
            try:
                from prefilter import (tokenize as _vf_tok,
                                        detect_canonical_verbs_all as _vf_dv)
                _is_compound = len(set(_vf_dv(_vf_tok(query)))) >= 2
            except Exception:
                _is_compound = False
        if (os.environ.get("METNOS_PROPOSER_VERB_FILTER", "1") == "1"
                and intent.verb and not _is_compound):
            try:
                from tool_grammar import filter_pool_by_intent_verb
                pool_objs = [next((e for e in catalog if e.name == n), None) for n in pool] \
                            if catalog else []
                pool_objs = [p for p in pool_objs if p is not None]
                if pool_objs:
                    kept, excluded = filter_pool_by_intent_verb(pool_objs, intent.verb)
                    if kept:
                        effective_pool = [e.name for e in kept]
                        log.info("verb-aware filter: pool %d → %d (verb=%s)",
                                  len(pool), len(effective_pool), intent.verb)
            except Exception as ex:
                log.warning("verb filter fallito: %r — fallback full pool", ex)

        # Esclusione esplicita (es. guard get_inputs misroute in dispatch):
        # applicata DOPO la costruzione del pool, così sopravvive alla
        # re-iniezione degli universal helpers in filter_pool_by_intent_verb.
        # Toglie i nomi sia dal prompt sia dalla grammar GBNF (entrambi usano
        # effective_pool). Universale, deterministico §7.9.
        if exclude_tools:
            _excl = set(exclude_tools)
            effective_pool = [n for n in effective_pool if n not in _excl]

        # B10 — pool effettivo VUOTO (verb-filter/exclude_tools hanno tolto
        # tutto, o pool vuoto dal caller): la grammar GBNF degraderebbe a
        # `tool ::= string` (allucinazione libera) e il prompt non offrirebbe
        # alcuna scelta valida → None onesto (§2.8) invece di proporre alla
        # cieca.
        if not effective_pool:
            log.info("SimpleProposer: pool effettivo vuoto dopo "
                     "verb-filter/exclude_tools — nessun tool proponibile, "
                     "return None")
            return None

        # Render tool schemas inline (Mētis needs arg names + required)
        tools_inline = _render_tool_pool(effective_pool, catalog)
        prompt_vars = dict(
            verb=intent.verb, obj=intent.object,
            keywords=", ".join(intent.keywords),
            tools=tools_inline,
            # B15: forma leggibile dei piani esclusi + istruzione di
            # diversificazione (non hash sha opachi che il modello ignora).
            excluded=_render_excluded_signal(excluded_hashes, lang),
            user_query=query,
        )
        try:
            if self._load_prompt is None:
                # Ottimizzazione A prompt-cache (10/6/2026): testa statica del
                # template → SYSTEM (byte-identica fra le query → llama-server
                # la riusa dal checkpoint n_before_user); coda per-query
                # (intent/pool/excluded/query) → USER. Misura: prompt_n
                # 5521→1277, latenza call 8.15s→2.26s. Vedi
                # prompt_loader.get_split + guard prompts_lint L6.
                from prompt_loader import get_split
                system, user = get_split("engine_proposer", lang, **prompt_vars)
                if not user:
                    user = query  # template senza marker: layout legacy
            else:
                # Loader iniettato (test): contratto legacy 1-stringa.
                system = self._load_prompt("engine_proposer", lang, **prompt_vars)
                user = query
        except Exception as ex:
            log.warning("SimpleProposer prompt load failed: %r", ex)
            return None
        if not system:
            return None
        # Costruisci kwargs LLM con opzionale grammar
        llm_kwargs: dict = {
            "max_tokens": 1024 if use_fast else 2048,
            "think": not use_fast,
        }
        if use_grammar:
            try:
                from .grammar_framework import build_framework_grammar
                # Vincola `tool` ai nomi del pool effettivo: l'LLM non puo'
                # piu' allucinare nomi inesistenti (es. get_issues) ne' uscire
                # dal pool. Bug 2/6/2026: grammar vincolava solo la FORMA JSON,
                # non i nomi tool → find_urls/get_issues invece di
                # find_issues_github (in pool).
                llm_kwargs["grammar"] = build_framework_grammar(effective_pool)
            except Exception as ex:
                log.warning("GBNF grammar load fallita: %r — fallback no-grammar", ex)
        try:
            raw = llm_call(system, user, **llm_kwargs)
        except TypeError:
            # llm_call non supporta grammar/think kwargs → fallback
            if "grammar" in llm_kwargs:
                # B7 — §2.8 no silent failure: il drop della GBNF toglie il
                # vincolo sui nomi tool → generazione NON vincolata
                # (allucinazione possibile). Va segnalato, non taciuto.
                log.warning(
                    "SimpleProposer: llm_call non supporta 'grammar' — "
                    "GBNF droppata, generazione non vincolata (§2.8)")
            llm_kwargs.pop("grammar", None)
            try:
                raw = llm_call(system, user, **llm_kwargs)
            except Exception as ex:
                log.warning("SimpleProposer LLM (fallback) call failed: %r", ex)
                return None
        except Exception as ex:
            log.warning("SimpleProposer LLM call failed: %r", ex)
            return None
        parsed = _parse_framework_json(raw or "")
        if not parsed:
            log.info("SimpleProposer parse fail. Raw head: %r", (raw or "")[:200])
            return None
        return Framework.from_dict(parsed)


# ── Factory (selettore engine) ────────────────────────────────────────────

def get_proposer() -> Proposer:
    """Ritorna istanza Proposer selezionata via METNOS_ENGINE.

    Caricamento lazy: i moduli proposer_metis / proposer_frontier sono
    importati solo se richiesti, così l'assenza del file non blocca il
    sistema (fallback su SimpleProposer).
    """
    from . import get_engine_name
    name = get_engine_name()
    if name == "metis":
        try:
            from . import proposer_metis
            return proposer_metis.MetisProposer()
        except Exception as ex:
            log.warning("MetisProposer unavailable (%r), fallback simple", ex)
    elif name == "frontier":
        try:
            from . import proposer_frontier
            return proposer_frontier.FrontierProposer()
        except Exception as ex:
            log.warning("FrontierProposer unavailable (%r), fallback simple", ex)
    return SimpleProposer()
