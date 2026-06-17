# SPDX-License-Identifier: AGPL-3.0-only
"""telos_lenses/_base.py — framework comune per le lenti.

Ogni lens fornisce 3 cose:
1. NAME (str)
2. OPERATORS (tuple di label per operator-by-operator generation, oppure
   ("",) per single-call lens senza operatori espliciti)
3. build_prompt(ctx, operator) -> str

Il resto (LLM call, parse JSON, paternalism guard, grammar wiring,
emit LensProposal) e' centralizzato qui.

§7.2 semplicita': UN punto solo dove avviene la chiamata LLM, il parse
del JSON, il filter anti-paternalismo. Le lenti diventano dati (prompt
+ operatori) invece di codice duplicato.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

_LOG = logging.getLogger(__name__)


# ── SHARED prompt blocks — stile §6 prescrittivo, brevita' obbligatoria ─
#
# Iniettati dalle lenti per evitare duplicazione. Le lenti NON ripetono
# queste norme. Stile: DEVI / NON DEVI / OK / ERRORE / PATTERN.

SHARED_PREAMBLE = """RUOLO: agente Metnos background. Scrivi proposte per il Vaglio. NON parli con l'utente.

DEVI: cambiare cio' che fa METNOS.
NON DEVI: giudicare l'utente.
ERRORE: "dire/impedire/correggere all'utente".

DEVI: §2.1 (single-op vettoriale) | §2.9 (no default impliciti) | §2.10 (no accoppiamento domini) | §2.4 (plurale N=1 OK).
NON DEVI: fondere executor | inferire args dal contesto | accoppiare domini | rimuovere supporto N=1.

METNOS FA GIA': from_step piping | undo_last_turn | fast_path | cache query→piano (engine fastpath L0) | output_format markdown | needs_inputs.
NON re-inventare."""

# Schema naming + governance: SOLO per lenti naming-aware.
SHARED_NAMING_SCHEMA = """NAMING §2.2 + ADR 0156: `verb_object[_qualifier[_descriptor]]` (separatore `_` posizionale).

DEVI: usare token dal vocab CHIUSO (verb/object/qualifier).
DEVI: estendere UN LIVELLO ALLA VOLTA. Se il 3° non esiste nel catalog, NON aggiungere 4°.
DEVI: descriptor (4°) = MODIFICATORE COMPORTAMENTALE a parita' args.

NON DEVI: descriptor senza qualifier presente.
NON DEVI: descriptor che e' timing / contesto applicativo / dominio.
NON DEVI: proporre nuovo qualifier (3°) + nuovo descriptor (4°) insieme.

OK pattern: `compute_files_loc_per-language` | `find_dirs_empty` | `change_files_format_dry-run` | `set_tasks`.
ERRORE pattern: `set_tasks_invoice-lifecycle` (no qualifier) | `create_events_promotion_nightly` (3°+4° nuovi insieme) | `_nightly` (timing).

ESTENSIONE VOCAB §2.2: nuovo token? NON inventarlo nel nome. Scrivi nel rationale
"RICHIEDE estensione vocab §2.2: <criterio>". 3 CRITERI: necessario (no synonym in classe) + generale (semantica riusabile, no domain-specific) + comprensibile (il modello locale senza glossa)."""

# Per lenti concept-only.
SHARED_NAMING_NULL = "NAMING: `new_op_name` = SEMPRE null (questa lens propone un concetto, non un executor variant)."

# Output JSON.
SHARED_OUTPUT_FORMAT = """OUTPUT: array JSON 1-3 oggetti, NIENT'ALTRO. `[]` preferito a proposte deboli.
  {{"executor_target":"<dal campione vivo>","new_op_name":<vedi NAMING>,
    "proposed_action":"<descrizione 1-2 righe>","rationale":"<evidenza dal mnestoma/patterns>"}}"""


def context_block(ctx) -> str:
    """Render del blocco CONTESTO (mnestoma + patterns + executors) comune.

    Compatto: 3 sezioni con whitespace minimo. Le lenti possono includerlo
    o costruire una versione piu' breve se serve."""
    execs = "\n".join(f"  - {e['name']}: {e.get('description','')[:100]}"
                      for e in ctx.executors_sample)
    return (f"CONTESTO:\nMnestoma:\n{ctx.mnestoma_summary}\n\n"
            f"Pattern utente (turn_log 30gg):\n{ctx.user_patterns_summary}\n\n"
            f"Executor disponibili (campione vivo):\n{execs}")


# Regex anti-paternalismo: pattern di proposte che giudicano l'utente.
# Conservativo, deterministico, multilingua IT+EN.
_PATERNALISM_RE = re.compile(
    r"\b("
    r"dire\s+all['']?\s*utente|impedire\s+all['']?\s*utente|"
    r"correggere\s+l['']?\s*utente|consigliare\s+all['']?\s*utente\s+di\s+(?:non\s+)?|"
    r"tell\s+the\s+user|prevent\s+the\s+user|warn\s+the\s+user\s+about|"
    r"advise\s+the\s+user\s+to"
    r")\b",
    re.IGNORECASE,
)


@dataclass
class LensCtx:
    """Contesto passato alle lenti per costruire il prompt."""
    telos: object                       # Telos dataclass
    executors_sample: list[dict]        # [{name, description}, ...]
    mnestoma_summary: str
    user_patterns_summary: str
    live_executor_names: set
    # Proposte gia' emesse in questo run (operator precedenti): permette
    # anti-fixation in lenti multi-operator (es. SCAMPER 7 op). run_lens
    # accumula dopo ogni operator; le lenti che vogliono differenziare
    # leggono ctx.previous_proposals e inseriscono "NON DEVI ripetere".
    previous_proposals: list = field(default_factory=list)


@dataclass
class LensProposal:
    """Una proposta generica (compatibile con SCAMPER schema + estendibile)."""
    lens: str                           # nome della lens (scamper, oulipo, ecc.)
    operator: str                       # label operatore (per lens single-call e' "")
    executor_target: str                # name del catalog vivo
    telos_id: str
    proposed_action: str
    rationale: str
    new_op_name: str | None = None      # verb_object[_qualifier[_descriptor-kebab]] o None
    paternalism_flag: bool = False
    expected_alignment: float = 0.0


def paternalism_check(text: str) -> bool:
    """True se il testo suggerisce paternalismo (giudica utente)."""
    return bool(_PATERNALISM_RE.search(text))


def parse_llm_array(raw: str) -> list[dict]:
    """Parse tollerante: estrae array JSON da raw LLM output.

    Gestisce markdown fence ```json ... ```. Ritorna lista vuota su
    parsing fallito (degenerazione, NIENTE eccezioni up)."""
    if not raw or not raw.strip():
        return []
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.M)
    try:
        items = json.loads(s)
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []
    return [it for it in items if isinstance(it, dict)]


def run_lens(
    *,
    lens_name: str,
    operators: tuple,
    build_prompt: Callable[[LensCtx, str], str],
    ctx: LensCtx,
    llm_invoke: Callable[..., str],
    grammar: Optional[str] = None,
    paternalism_filter: bool = True,
) -> list[LensProposal]:
    """Loop unico: per ogni operator chiama LLM, parse JSON, filter, emit.

    Args:
      lens_name: per stamping nei LensProposal e nei log.
      operators: tuple di label. Per lens senza operatori espliciti, ("",).
      build_prompt: callable che riceve ctx + operator label e ritorna prompt.
      ctx: contesto LensCtx.
      llm_invoke: adapter LLM (e.g. _llm_invoke_local).
      grammar: GBNF opzionale per constrained generation.
      paternalism_filter: scarta proposte che giudicano l'utente.
    """
    out: list[LensProposal] = []
    # Reset accumulator per scope per-lens (no cross-lens pollution).
    ctx.previous_proposals = []
    for op in operators:
        prompt = build_prompt(ctx, op)
        try:
            raw = llm_invoke(prompt, grammar=grammar) if grammar else llm_invoke(prompt)
        except Exception as ex:
            _LOG.warning("%s: LLM call failed op=%s: %r", lens_name, op, ex)
            continue
        items = parse_llm_array(raw)
        for it in items:
            tgt = (it.get("executor_target") or "").strip()
            action = (it.get("proposed_action") or "").strip()
            rationale = (it.get("rationale") or "").strip()
            if not tgt or not action:
                continue
            patern = paternalism_filter and (
                paternalism_check(action) or paternalism_check(rationale)
            )
            new_name_raw = it.get("new_op_name")
            new_name = (
                new_name_raw.strip()
                if isinstance(new_name_raw, str) and new_name_raw.strip()
                else None
            )
            prop = LensProposal(
                lens=lens_name,
                operator=op,
                executor_target=tgt,
                telos_id=ctx.telos.id,
                proposed_action=action,
                rationale=rationale,
                new_op_name=new_name,
                paternalism_flag=patern,
            )
            out.append(prop)
            # Accumula per anti-fixation operator-by-operator.
            ctx.previous_proposals.append({
                "operator": op, "executor_target": tgt,
                "new_op_name": new_name,
            })
    if paternalism_filter:
        kept = [p for p in out if not p.paternalism_flag]
        if len(kept) < len(out):
            _LOG.info("%s: scartate %d proposte paternalistiche",
                      lens_name, len(out) - len(kept))
        out = kept
    return out
