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
from dataclasses import dataclass
from typing import Callable, Optional

_LOG = logging.getLogger(__name__)

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
      llm_invoke: adapter LLM (e.g. _llm_invoke_local_gemma).
      grammar: GBNF opzionale per constrained generation.
      paternalism_filter: scarta proposte che giudicano l'utente.
    """
    out: list[LensProposal] = []
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
            out.append(LensProposal(
                lens=lens_name,
                operator=op,
                executor_target=tgt,
                telos_id=ctx.telos.id,
                proposed_action=action,
                rationale=rationale,
                new_op_name=new_name,
                paternalism_flag=patern,
            ))
    if paternalism_filter:
        kept = [p for p in out if not p.paternalism_flag]
        if len(kept) < len(out):
            _LOG.info("%s: scartate %d proposte paternalistiche",
                      lens_name, len(out) - len(kept))
        out = kept
    return out
