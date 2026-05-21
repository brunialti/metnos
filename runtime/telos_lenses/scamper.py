# SPDX-License-Identifier: AGPL-3.0-only
"""scamper.py — lente SCAMPER (Eberle 1971, Osborn 1953).

Sette operatori di brainstorming applicati ai top-N executor del catalog:

  S - Substitute        sorgente diversa per stesso verbo (read_messages -> read_voicemail)
  C - Combine           due executor in pipeline non ancora vista (find_files + read_messages cross)
  A - Adapt             stesso executor adattato a contesto utente (read_messages_voice)
  M - Modify            executor con parametri default diversi (filter_entries con preset)
  P - Put to other use  executor in contesto non canonico (compute_files_loc su README per stats progetti)
  E - Eliminate         executor sostituito da scorciatoia (cap a 30 entries default invece di 100)
  R - Reverse           verbo inverso (write -> read; find -> ignore)

Output: lista di proposte ognuna con (operator, executor_target, telos_id,
proposed_action, rationale, distance_from_existing).

Anti-paternalismo guard (vedi telos_engine_v1 §3, dialogo Giornata II):
- Proposta NON propone "dire all'utente di X" o "impedire all'utente di Y".
- Cambia comportamento di Metnos, non scelte dell'utente.
- Si applica a executor sintetizzati, schedule, policy interne.

§7.9: la lente USA un LLM per generare (creativita' richiesta), ma il
GATING anti-paternalismo a valle e' deterministico (regex su proposte
sospette + filtraggio).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

_LOG = logging.getLogger(__name__)

SCAMPER_OPERATORS = ("S", "C", "A", "M", "P", "E", "R")
SCAMPER_NAMES = {
    "S": "Substitute",
    "C": "Combine",
    "A": "Adapt",
    "M": "Modify",
    "P": "Put_to_other_use",
    "E": "Eliminate",
    "R": "Reverse",
}

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
class ScamperProposal:
    """Una proposta SCAMPER concreta."""
    operator: str                       # one of SCAMPER_OPERATORS
    executor_target: str                # nome executor da cui parte
    telos_id: str                       # telos servito
    proposed_action: str                # descrizione 1-2 righe
    rationale: str                      # perche' serve il telos
    expected_alignment: float = 0.0     # stima [0,1], LLM judge a valle
    distance_from_existing: int = 0     # quanti executor della proposta sono nuovi
    paternalism_flag: bool = False      # True = scartata dal guard


def _paternalism_check(text: str) -> bool:
    """True se il testo suggerisce paternalismo (giudica utente)."""
    return bool(_PATERNALISM_RE.search(text))


def _build_prompt(
    telos_phrase: str,
    telos_notes: str,
    executors_sample: list[dict],
    mnestoma_summary: str,
    user_patterns_summary: str,
    operator: str,
) -> str:
    """Compone il prompt LLM per un singolo operatore SCAMPER su un telos.

    Schema input:
    - telos: il fine che la proposta deve servire
    - executors_sample: 5-10 executor del catalog con descrizione breve
    - mnestoma_summary: top-N mnest co-attivati di recente
    - user_patterns_summary: verbi/oggetti/ritmi recenti dal turn_log
    - operator: lettera SCAMPER (S/C/A/M/P/E/R)

    Output atteso dall'LLM: JSON array di 1-3 proposte, ognuna con campi
    {executor_target, proposed_action, rationale}.
    """
    op_name = SCAMPER_NAMES[operator]
    op_descriptions = {
        "S": "SOSTITUISCI una componente di un executor con un'alternativa funzionalmente analoga (es. sorgente, formato, scope)",
        "C": "COMBINA due executor in una pipeline che l'utente NON ha mai usato (cross-domain o cross-corpus)",
        "A": "ADATTA un executor a un contesto utente non standard (multi-account, multi-lingua, multi-canale)",
        "M": "MODIFICA i parametri di default di un executor per allinearli al pattern d'uso reale dell'utente",
        "P": "USA un executor in un contesto non canonico (es. compute_files_loc su README per stats progetti)",
        "E": "ELIMINA uno step ridondante / un parametro inutilizzato / un cap che non serve mai",
        "R": "INVERTI il verbo: se write fa X, prova read di X (es. log inverse, undo memoizzato, dry-run)",
    }
    return f"""Sei un agente di Metnos che genera proposte creative di nuove
funzioni o ritmi per servire un fine utente. Operi in background:
NON parli con l'utente, scrivi proposte da sottoporre al Vaglio.

REGOLA CRUCIALE: le tue proposte cambiano cio' che fa METNOS, mai cio'
che fa l'utente. NIENTE proposte tipo "dire all'utente di X" o
"impedire all'utente di Y". Metnos giudica se stesso, non l'utente.

VINCOLI ARCHITETTURALI (proposte che li violano vengono scartate):
1. NON FONDERE due executor in uno. Ogni executor fa una sola
   operazione (vettoriale, batch-by-default). Es: "merge fetch+write
   in single executor" NON e' valido. Le pipeline si compongono nel
   planner via from_step, non nel codice executor.
2. NON proporre default IMPLICITI che inferiscono argomenti dal
   contesto (es. "infer path dal cwd", "infer chiave dal tipo lista"):
   gli executor sono deterministici al confine NL→codice. I default
   inferiti generano sorprese e bug silenti.
3. NON ACCOPPIARE domini ortogonali. Es: filter_entries (predicato
   puro) NON deve "auto-fetch metadata" del dominio file. Il
   trasformatore consuma cio' che riceve, niente di piu'.
4. NON suggerire "approvazione batch silenziosa" o "auto-conferma":
   ogni azione mutante mantiene il gate di vaglio/consent.

COSA METNOS GIA' FA (NON re-inventare):
- Piping fra executor via `from_step: N` nel planner ReAct.
- Undo del turno corrente via `undo_last_turn`.
- Fast-path deterministico per query triviali (zero LLM).
- Memoization di sequenze multi-tool (uses>=3) e promozione a synth
  (uses>=50).
- Output formatter channel-agnostic (markdown), no LLM nel render.
- Dialog `needs_inputs` per parametri mancanti.

TELOS DA SERVIRE:
  {telos_phrase}
  Note utente: {telos_notes}

CONTESTO:
- Mnestoma recente (executor co-attivati, gia' filtrato al catalog vivo):
{mnestoma_summary}

- Pattern d'uso dell'utente (turn_log 30gg):
{user_patterns_summary}

- Executor disponibili nel catalog (campione vivo):
{chr(10).join(f"  - {e['name']}: {e.get('description', '')[:120]}" for e in executors_sample)}

OPERATORE SCAMPER: {operator} = {op_name}
COSA FARE: {op_descriptions[operator]}

Genera 1-3 proposte concrete che applicano l'operatore {operator} a uno
degli executor del catalog VIVO sopra (NON inventare nomi non in lista)
per servire il telos. Ogni proposta in JSON:

  {{
    "executor_target": "<name esatto dell'executor dal campione sopra>",
    "proposed_action": "<descrizione 1-2 righe della proposta>",
    "rationale": "<perche' avvicina al telos, 1 riga, con evidenza dal mnestoma o pattern>"
  }}

Rispondi SOLO con un array JSON di 1-3 oggetti. Niente prosa attorno.
Se nessuna proposta sensata che rispetti i VINCOLI ARCHITETTURALI e'
generabile, rispondi `[]` (preferito a forzare una proposta debole).
"""


def generate_proposals(
    telos,
    executors_sample: list[dict],
    mnestoma_summary: str,
    user_patterns_summary: str,
    llm_invoke: Callable[[str], str],
    operators: Optional[tuple] = None,
    paternalism_filter: bool = True,
) -> list[ScamperProposal]:
    """Genera proposte SCAMPER per un telos via LLM.

    Args:
      telos: oggetto Telos (id, phrase, notes)
      executors_sample: 5-10 executor del catalog con description
      mnestoma_summary: stringa con top-N mnest recenti
      user_patterns_summary: stringa con verbi/ritmi recenti
      llm_invoke: callable(prompt) -> raw_text del LLM (tier middle suggerito)
      operators: subset operatori SCAMPER, default tutti e 7
      paternalism_filter: se True, scarta proposte che giudicano l'utente

    Returns:
      Lista di ScamperProposal, gia' filtrate per anti-paternalismo.
    """
    import json
    ops = operators or SCAMPER_OPERATORS
    proposals: list[ScamperProposal] = []
    for op in ops:
        prompt = _build_prompt(
            telos.phrase, telos.notes,
            executors_sample, mnestoma_summary, user_patterns_summary, op,
        )
        try:
            raw = llm_invoke(prompt)
        except Exception as ex:
            _LOG.warning("scamper: LLM call failed for op=%s: %r", op, ex)
            continue
        if not raw or not raw.strip():
            continue
        # Estrai JSON: tollerante a markdown fence
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.M)
        try:
            items = json.loads(raw)
        except json.JSONDecodeError as ex:
            _LOG.warning("scamper: JSON parse failed for op=%s: %r raw=%r",
                          op, ex, raw[:200])
            continue
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            tgt = (it.get("executor_target") or "").strip()
            action = (it.get("proposed_action") or "").strip()
            rationale = (it.get("rationale") or "").strip()
            if not tgt or not action:
                continue
            patern = paternalism_filter and (
                _paternalism_check(action) or _paternalism_check(rationale)
            )
            proposals.append(ScamperProposal(
                operator=op,
                executor_target=tgt,
                telos_id=telos.id,
                proposed_action=action,
                rationale=rationale,
                paternalism_flag=patern,
            ))
    if paternalism_filter:
        filtered = [p for p in proposals if not p.paternalism_flag]
        if len(filtered) < len(proposals):
            _LOG.info("scamper: scartate %d proposte paternalistiche",
                      len(proposals) - len(filtered))
        proposals = filtered
    return proposals
