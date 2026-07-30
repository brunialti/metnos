# SPDX-License-Identifier: AGPL-3.0-only
"""alignment_engine.py — Giudice teleologico (telos engine fase 2).

Implementa la funzione di allineamento dichiarata in
`docs/it/architecture/telos.html` cap.4 (v1.3, 22/5/2026):

    contrib_i = peso_i * gate(fit_i, soglia_i)   [fit_i<0 → penalità senza gate, v1.4]
    top = max(contrib_i)
    rest = sum(contrib_i) - top
    expected_alignment = (ALPHA * top + GAMMA * rest)
                         * urgency * confidence - bother_cost

dove:
- `peso_i`, `soglia_i` provengono da `telos_loader.current()`.
- `fit_i` per ciascun telos e' stimato da un LLM judge (NON dal proponente:
  evita self-enhancement bias documentato Zheng et al. 2023).
- `urgency` ∈ [0.5, 2.0] (1.0 = nessuna pressione, 2.0 = 24h, 0.5 = cronico).
- `confidence` ∈ [0, 1].
- `bother_cost` ∈ [0, ∞).
- `ALPHA = 2.0`, `GAMMA = 0.5` (costanti interne). Vincolo ALPHA > 3*GAMMA
  garantisce ordering: specialista perfetto su telos pesante > tuttofare
  medio (fit=0.5 uniforme).

Razionale formula α·top + γ·rest (storico):
- v1.0 (sum-product Σ peso_i × fit_i): backfill 481 proposte distribuiva
  in [0.08, 0.57] (mean 0.30, stdev 0.075). Premiava tuttofare medio vs
  specialista perfetto — segnale invertito.
- v1.2 (top + 0.3·rest): correggeva l'ordering ma comprimeva stdev a 0.039
  perche' top1 limitato a [0.10, 0.25] (5 valori discreti dai pesi).
- v1.3 (2·top + 0.5·rest): boost ALPHA al top1 espande il range del
  termine dominante (range [0.20, 0.50]), GAMMA piu' alto da' credito
  alle proposte multi-telos (98.5% dei dati reali). Stdev 0.073 ≈ v1.0
  ma con ordering corretto. Vincolo critico α > 3γ: con (2, 0.5)
  margine 33% (2 > 1.5).

Determinismo (§7.9) per la composizione; LLM solo per la stima `fit_i`.

API pubblica:
    compose(fits, telos_list, *, urgency=1.0, confidence=0.8,
            bother_cost=0.0) -> float
    estimate_fit(proposal, telos_list, *, llm_invoke=None,
                 urgency=1.0, confidence=0.8, bother_cost=0.0) -> AlignmentResult

Wire-in: `telos_introspect.run_for_telos` chiama `estimate_fit` post-lens
e aggiorna `LensProposal.expected_alignment`. Backfill 481 proposte
esistenti via CLI `python -m alignment_engine --backfill` (LLM, lento)
o `--recompose` (deterministico dai fit gia' salvati, secondi).
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Callable, Optional

_LOG = logging.getLogger(__name__)

# Default LLM = tier locale (stesso del telos engine MVP fase 1,
# telos_introspect.py). Mantiene "costo zero in background".
# "local" = placeholder: il server serve il GGUF caricato (mapping
# tier→modello fisico solo in llm_router.DEFAULT_TIERS).
_LOCAL_MODEL = "local"
_LOCAL_ENDPOINT = "http://127.0.0.1:8080"


@dataclass(frozen=True)
class FitEstimate:
    """Stima di allineamento per UN telos. `why` = audit trail testuale."""
    telos_id: str
    fit: float          # [0, 1]
    why: str = ""


@dataclass(frozen=True)
class AlignmentResult:
    """Output completo del Giudice teleologico per UNA proposta."""
    expected_alignment: float
    per_telos: list[FitEstimate]
    urgency: float
    confidence: float
    bother_cost: float


# Costanti di taratura formula α·top + γ·rest. Vincolo: ALPHA > 3*GAMMA
# garantisce specialista perfetto su t.tempo (peso 0.25, fit=1) > tuttofare
# medio (fit=0.5 uniforme su tutti i 6 telos). Con (2.0, 0.5) il margine
# e' 33% (2 > 1.5). Sui dati reali — 98.5% proposte multi-telos n>=2 —
# il vincolo e' largamente conservativo: ALPHA elevato espande la dinamica
# del top1 (range [0.20, 0.50] post-α), GAMMA da' credito al rest senza
# diluire il segnale del telos vincente.
_ALPHA = 2.0
_GAMMA = 0.5


def _gate(fit: float, threshold: float) -> float:
    """fit se >= threshold, altrimenti 0. Funzione gate per non sommare rumore."""
    return fit if fit >= threshold else 0.0


def compose(
    fits: list[FitEstimate],
    telos_list: list,  # list[telos_loader.Telos]
    *,
    urgency: float = 1.0,
    confidence: float = 0.8,
    bother_cost: float = 0.0,
) -> float:
    """Composizione deterministica della formula (v1.3). Non chiama LLM.

    Per ogni telos calcola `contrib_i = peso_i * gate(fit_i, soglia_i)`.
    Poi: `top = max(contrib_i)`, `rest = sum(contrib_i) - top`,
    `ea_base = ALPHA * top + GAMMA * rest`.
    Output: `ea_base * urgency * confidence - bother_cost`.

    Razionale α·top + γ·rest: il boost ALPHA al top1 garantisce che le
    proposte specialiste su telos pesanti emergano, GAMMA da' credito
    alle multi-telos (98.5% dei dati reali). Vedi docstring modulo.

    `fits` e `telos_list` matchati per `telos_id`: i telos senza fit
    contribuiscono 0 (degrade graceful, no exception). Pesi e soglie
    presi da `telos_list` (single source — niente sync issue).

    Validazione argomenti: urgency clamped a [0.5, 2.0], confidence
    a [0, 1], bother_cost a [0, ∞). §2.8: niente silent failure su
    valori fuori range, ma niente exception (clamp).

    Casi degeneri:
    - telos_list vuota → 0.0 (no contributi)
    - 1 solo telos → ea_base = contrib (rest=0, top+0=top)
    - tutti i fit sotto soglia → ea_base = 0
    """
    urgency = max(0.5, min(2.0, urgency))
    confidence = max(0.0, min(1.0, confidence))
    bother_cost = max(0.0, bother_cost)
    if not telos_list:
        return -bother_cost  # nessun contributo, ma bother_cost ancora applicato
    fits_by_id = {f.telos_id: f for f in fits}
    contribs: list[float] = []
    penalty = 0.0
    for t in telos_list:
        f = fits_by_id.get(t.id)
        if f is None:
            contribs.append(0.0)
            continue
        if f.fit < 0:
            # v1.4 (2/7/2026): fit NEGATIVO = la proposta LAVORA CONTRO il
            # telos (frontier a pagamento vs t.parsimonia, interruzione vs
            # t.discrezione). Il danno NON passa dal gate: conta sempre,
            # anche sotto soglia — meglio penalizzare un falso conflitto
            # che promuovere una proposta che viola un fine dichiarato.
            penalty += t.weight * (-f.fit)
            contribs.append(0.0)
            continue
        contribs.append(t.weight * _gate(f.fit, t.activation_threshold))
    top = max(contribs)
    rest = sum(contribs) - top
    ea_base = _ALPHA * top + _GAMMA * rest - _ALPHA * penalty
    return ea_base * urgency * confidence - bother_cost


# --- LLM judge prompt ------------------------------------------------------

_JUDGE_PROMPT_IT = """\
Sei il Giudice teleologico di Metnos. Valuti quanto una proposta è allineata
ai TELOS dichiarati dall'utente, NON se la proposta è buona in sé.

I telos:
{telos_block}

LA PROPOSTA:
- generata dalla lente: {lens}
- azione proposta: {proposed_action}
- razionale: {rationale}

DEVI: per ogni telos qui sopra, stimare fit ∈ [-1, 1] = quanto la proposta
SERVE quel telos. 0 = neutra/irrilevante. 0.5 = aiuta indirettamente.
1.0 = lo serve in modo diretto e centrale. NEGATIVO = lavora CONTRO quel
telos (es. usa un servizio a pagamento → t.parsimonia negativo; interrompe
l'utente → t.discrezione negativo; espone dati → t.protezione negativo).
NON DEVI: confondere "buona idea" con "allineata ai telos". Una proposta
brillante ma fuori dai telos dichiarati ha fit basso su tutti.
OK: proposta riduce 5 step a 1 → t.tempo fit alto (0.8), t.parsimonia medio (0.5).
OK: proposta chiama un LLM a pagamento → t.parsimonia fit NEGATIVO (-0.6).
ERRORE: assegnare fit alto a tutti i telos "per sicurezza" — è rumore.

Output: array JSON, una entry per telos, in QUESTO ordine esatto:
{telos_ids_ordered}

Formato esatto (ogni entry):
{{"telos_id": "t.tempo", "fit": 0.7, "why": "una frase breve, max 20 parole"}}

SOLO l'array JSON. Niente prosa prima o dopo. Niente markdown fence.
"""


def _build_telos_block_for_judge(telos_list: list) -> str:
    """Formato compatto per il Giudice: id, phrase, peso, note."""
    lines = []
    for t in telos_list:
        lines.append(
            f"- {t.id} (peso {t.weight:.2f}): {t.phrase}\n"
            f"  note: {t.notes}"
        )
    return "\n".join(lines)


_JSON_ARRAY_RE = re.compile(r"\[\s*\{.*?\}\s*\]", re.DOTALL)


def _parse_fits(raw: str, telos_list: list) -> list[FitEstimate]:
    """Parser tollerante dell'output LLM. Estrae l'array JSON e ne
    valida le entry. Telos mancanti nell'output → fit=0 (degrade graceful)."""
    if not raw or not raw.strip():
        return []
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.M)
    # Trova il primo array JSON nel testo (l'LLM puo' aggiungere prosa).
    m = _JSON_ARRAY_RE.search(s)
    if m:
        s = m.group(0)
    try:
        items = json.loads(s)
    except json.JSONDecodeError as ex:
        _LOG.warning("alignment_engine: JSON parse failed: %s", ex)
        return []
    if not isinstance(items, list):
        return []
    valid_ids = {t.id for t in telos_list}
    fits: dict[str, FitEstimate] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        tid = it.get("telos_id")
        if tid not in valid_ids:
            continue
        try:
            fit = float(it.get("fit", 0))
        except (TypeError, ValueError):
            continue
        fit = max(-1.0, min(1.0, fit))   # negativo = CONTRO il telos (v1.4)
        why = str(it.get("why", ""))[:200]  # cap per audit log size
        fits[tid] = FitEstimate(telos_id=tid, fit=fit, why=why)
    # Telos non presenti in output → fit=0 esplicito (no silent drop)
    return [
        fits.get(t.id, FitEstimate(telos_id=t.id, fit=0.0, why="(missing)"))
        for t in telos_list
    ]


def _default_llm_invoke(prompt: str) -> str:
    """modello locale (Qwen) via `LlamaCppProvider` (stesso pattern di
    `telos_introspect._llm_invoke_local`). Bypassa LLMRouter
    perche' tier=middle puo' essere instradato a Sonnet frontier.

    think=False di default (formato JSON-only, no reasoning) — il
    Giudice teleologico non ha bisogno di chain-of-thought per dare
    un fit numerico per telos. Override via env METNOS_ALIGNMENT_THINK=1.
    """
    think = os.environ.get("METNOS_ALIGNMENT_THINK", "0") == "1"
    from llm_provider import LlamaCppProvider
    prov = LlamaCppProvider(
        model=_LOCAL_MODEL,
        endpoint=_LOCAL_ENDPOINT,
    )
    r = prov.chat(
        "", prompt,
        max_tokens=2048, temperature=0.1,
        think=think,
        reasoning_budget=1024 if think else 0,
    )
    return r.text if hasattr(r, "text") else str(r)


def estimate_fit(
    proposal: dict,
    telos_list: list,
    *,
    llm_invoke: Optional[Callable[[str], str]] = None,
    urgency: float = 1.0,
    confidence: float = 0.8,
    bother_cost: float = 0.0,
) -> AlignmentResult:
    """Stima fit_i per ogni telos + compone expected_alignment.

    `proposal` è un dict con almeno `lens`, `proposed_action`, `rationale`
    (compatibile con il record persistito in telos_proposals.jsonl).
    `telos_list` da `telos_loader.current()`.

    Confidence default 0.8 (LLM judge è abbastanza affidabile per la stima
    relativa fra telos, meno per il valore assoluto). Bother_cost 0
    (l'engine non sa ancora quante proposte sono state pubblicate; verrà
    integrato dal pipeline che chiama `estimate_fit`).

    Determinismo §7.9: la composizione SI; la stima fit_i NO (LLM, dove il
    deterministico è troppo complesso — telos sono in linguaggio naturale).
    """
    if not telos_list:
        return AlignmentResult(
            expected_alignment=0.0, per_telos=[],
            urgency=urgency, confidence=confidence, bother_cost=bother_cost,
        )
    prompt = _JUDGE_PROMPT_IT.format(
        telos_block=_build_telos_block_for_judge(telos_list),
        lens=proposal.get("lens", ""),
        proposed_action=proposal.get("proposed_action", ""),
        rationale=proposal.get("rationale", ""),
        telos_ids_ordered=", ".join(t.id for t in telos_list),
    )
    llm = llm_invoke or _default_llm_invoke
    try:
        raw = llm(prompt)
    except Exception as ex:
        _LOG.warning("alignment_engine: LLM call failed: %r", ex)
        raw = ""
    fits = _parse_fits(raw, telos_list)
    ea = compose(fits, telos_list,
                 urgency=urgency, confidence=confidence, bother_cost=bother_cost)
    return AlignmentResult(
        expected_alignment=ea, per_telos=fits,
        urgency=urgency, confidence=confidence, bother_cost=bother_cost,
    )


# --- CLI offline per backfill di proposte storiche ------------------------

if __name__ == "__main__":
    import argparse
    import sys
    import time
    from pathlib import Path

    ap = argparse.ArgumentParser(description="alignment_engine CLI")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--backfill", type=str, nargs="?", const="",
                      help="path JSONL proposte da rivalutare (LLM call per fit "
                           "stimato, ~9s/proposta). Default prod path se vuoto.")
    mode.add_argument("--recompose", type=str, nargs="?", const="",
                      help="path JSONL gia' con alignment_per_telos: ricompone "
                           "expected_alignment con pesi/formula correnti, NO LLM "
                           "(deterministico, secondi). Default prod path se vuoto.")
    ap.add_argument("--limit", type=int, default=0,
                    help="processa solo prime N entry (0 = tutte)")
    ap.add_argument("--dry-run", action="store_true",
                    help="non scrive output, solo log")
    args = ap.parse_args()

    if not (args.backfill is not None or args.recompose is not None):
        ap.error("specificare --backfill o --recompose")

    import config as _C  # §7.11
    default_path = str(_C.PATH_USER_DATA / "telos_proposals.jsonl")
    mode_name = "recompose" if args.recompose is not None else "backfill"
    src_arg = args.recompose if mode_name == "recompose" else args.backfill
    src_path = src_arg or default_path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import telos_loader
    telos = telos_loader.current()
    if not telos:
        print("ERROR: nessun telos caricato. Verifica workspace/TELOS.md.", file=sys.stderr)
        sys.exit(1)

    src = Path(src_path)
    if not src.is_file():
        print(f"ERROR: {src} non esiste.", file=sys.stderr)
        sys.exit(1)

    suffix = ".rescored.jsonl" if mode_name == "backfill" else ".recomposed.jsonl"
    dst = src.with_suffix(suffix) if not args.dry_run else None
    out_lines = []
    n_done = n_skip = n_no_fits = 0
    t0 = time.time()
    valid_telos_ids = {t.id for t in telos}
    with src.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                n_skip += 1
                continue
            if args.limit and n_done >= args.limit:
                break

            if mode_name == "backfill":
                res = estimate_fit(rec, telos)
                rec["expected_alignment"] = res.expected_alignment
                rec["alignment_per_telos"] = [
                    {"telos_id": f.telos_id, "fit": f.fit, "why": f.why}
                    for f in res.per_telos
                ]
            else:  # recompose: deterministico, no LLM
                stored = rec.get("alignment_per_telos") or []
                if not stored:
                    n_no_fits += 1
                    # mantieni rec inalterato (no expected_alignment update)
                    out_lines.append(json.dumps(rec, ensure_ascii=False))
                    n_done += 1
                    continue
                # Ricostruisci FitEstimate, filtrando telos non piu' validi
                # (es. t.coltivazione_strumenti rimosso in v1.2).
                fits = []
                for entry in stored:
                    if not isinstance(entry, dict):
                        continue
                    tid = entry.get("telos_id")
                    if tid not in valid_telos_ids:
                        continue
                    try:
                        fit_val = float(entry.get("fit", 0))
                    except (TypeError, ValueError):
                        continue
                    fits.append(FitEstimate(
                        telos_id=tid, fit=fit_val,
                        why=str(entry.get("why", "")),
                    ))
                new_ea = compose(fits, telos)
                rec["expected_alignment"] = new_ea
                # Riallinea alignment_per_telos al set corrente (purga telos morti).
                rec["alignment_per_telos"] = [
                    {"telos_id": f.telos_id, "fit": f.fit, "why": f.why}
                    for f in fits
                ]

            out_lines.append(json.dumps(rec, ensure_ascii=False))
            n_done += 1
            if mode_name == "backfill" and n_done % 25 == 0:
                elapsed = time.time() - t0
                print(f"  ... {n_done} done in {elapsed:.1f}s "
                      f"({n_done / elapsed:.1f}/s)", file=sys.stderr)

    if dst is not None:
        dst.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        print(f"{mode_name}d {n_done} proposals → {dst}")
    else:
        print(f"DRY-RUN: would have {mode_name}d {n_done} proposals")
    print(f"skipped (malformed JSON): {n_skip}")
    if mode_name == "recompose":
        print(f"no stored fits (skipped recompose): {n_no_fits}")
    print(f"elapsed: {time.time() - t0:.1f}s")
