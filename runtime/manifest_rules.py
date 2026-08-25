"""manifest_rules.py — SoT delle regole universali manifest (il "DNA" di Metnos).

Single-source consumato da TUTTE le fasi di vita dell'executor:
  - engine/proposer._render_tool_pool  → budget RENDER (cosa vede l'LLM che sceglie)
  - manifest_lint                      → cap FISICI (testa / description / arg)
  - synt stage-4 + importer            → i numeri iniettati nel prompt di generazione

PRINCIPIO (§2.5, 7/6/2026): la `description` e' SOLO la testa §2.5
(SCOPO/PATTERN/NON/OUT) — l'unico testo che la macchina legge. NESSUNA coda di
prosa implementativa: comportamento -> codice (.py), uso arg -> [args].description,
razionale -> ADR (§9.1 codice=verita'). Target stretti per authoring; il render
del pool applica un budget complessivo elastico ma bounded.

Tutte le dimensioni sono PARAMETRIZZABILI via env. Cambiare un default =
rilanciare `tests/benchmarks/routing_subset_bench.py` (il budget tocca TUTTI i manifest).
"""
from __future__ import annotations
import re
from config import env_int


def _positive_env_int(name: str, default: int) -> int:
    parsed = env_int(name, default)
    return parsed if parsed > 0 else default


# ── Budget RENDER (cosa il proposer mostra all'LLM) ──────────────────────────
# Manifest a capitoli (§2.5): ``RENDER_BUDGET`` e' il budget MEDIO per tool.
# Il renderer di pool usa un water-filling deterministico: le teste corte
# cedono i caratteri non usati a quelle lunghe, senza superare HARD_MAX e con
# uno slack totale fisso. Il costo massimo del prompt resta quindi bounded.
RENDER_BUDGET = _positive_env_int("METNOS_MANIFEST_RENDER_BUDGET", 260)
RENDER_HARD_MAX = _positive_env_int("METNOS_MANIFEST_RENDER_HARD_MAX", 320)
RENDER_POOL_SLACK = _positive_env_int("METNOS_MANIFEST_RENDER_POOL_SLACK", 60)
# Manifest legacy (senza capitoli, in attesa di bonifica): prima frase robusta,
# cap RENDER_LEGACY_MAX. Alzato dal vecchio 120 fragile a un valore accettabile.
# NB: allargare troppo DISTRAE l'LLM su description verbose (test 7/6: 260 ->
# get_urls invece di read_urls_html). Tenere moderato finche' i manifest non
# sono bonificati a sola-testa.
RENDER_LEGACY_MAX = _positive_env_int("METNOS_MANIFEST_LEGACY_MAX", 180)

# ── Cap FISICI (validati dal linter) ─────────────────────────────────────────
HEAD_MAX = _positive_env_int("METNOS_MANIFEST_HEAD_MAX", 240)      # testa: inizio -> OUT: (escluso)
DESC_MAX = _positive_env_int("METNOS_MANIFEST_DESC_MAX", 320)      # description intera (testa + OUT:)
ARG_DESC_MAX = _positive_env_int("METNOS_MANIFEST_ARG_DESC_MAX", 180)  # ogni [args.<arg>].description
# Rendering schema-arg: conserva il tetto teorico dei vecchi cap fissi
# (100 required / 80 optional), ma consente agli slot corti o autoesplicativi
# di cedere il residuo. Lo slack e' PER INTERO POOL, non per executor.
ARG_RENDER_REQUIRED = _positive_env_int("METNOS_MANIFEST_ARG_RENDER_REQUIRED", 100)
ARG_RENDER_OPTIONAL = _positive_env_int("METNOS_MANIFEST_ARG_RENDER_OPTIONAL", 80)
ARG_RENDER_POOL_SLACK = _positive_env_int("METNOS_MANIFEST_ARG_RENDER_POOL_SLACK", 180)
# Il limite fisico di authoring e il limite runtime non sono la stessa cosa:
# un arg legacy puo' usare il residuo donato dal pool, pur restando bounded.
ARG_RENDER_HARD_MAX = _positive_env_int("METNOS_MANIFEST_ARG_RENDER_HARD_MAX", 320)

# ── Capitoli §2.5 ────────────────────────────────────────────────────────────
CHAPTERS = ("SCOPO:", "PATTERN:", "NON:", "OUT:")

# Eccezioni di schema per il carrier universale di pipeline. Questa e' la sola
# autorita' della regola condivisa da linter, normalizzatore, coerce e typecheck;
# non descrive gli altri argomenti che il runtime puo' iniettare in fasi diverse.
UNIVERSAL_ARGS = frozenset({"from_step", "entries"})


def _first_sentence(desc: str) -> str:
    """Prima frase robusta: spezza solo a '. ' (punto + spazio) o a fine stringa.
    NON spezza ad acronimi/estensioni con punto interno (es. '.html', '.py')."""
    m = re.search(r"\.\s", desc)
    return desc[: m.start()] if m else desc


def _cap_at_word(text: str, cap: int) -> str:
    """Tronca `text` a `cap` char SENZA spezzare l'ultima parola. Un token
    mutilato (es. `min_face_pixel` da `min_face_pixels=40000`) sembra un arg
    valido e inganna l'LLM (§7.3): meglio una parola in meno che una falsa.
    Se il taglio cade gia' su un confine, nessuna rimozione."""
    if len(text) <= cap:
        return text.strip()
    head = text[:cap]
    if not text[cap].isspace() and not head[-1].isspace():
        head = head[: head.rfind(" ") + 1] if " " in head else head
    return head.strip()


def render_head(desc: str) -> str:
    """Testa renderizzata per il proposer (SoT del troncamento, usata anche dal
    linter per coerenza). Manifest a capitoli: fino a 'OUT:' cap RENDER_BUDGET.
    Legacy: prima frase robusta cap RENDER_LEGACY_MAX. Taglio sempre a confine
    di parola (`_cap_at_word`)."""
    desc = (desc or "").strip().replace("\n", " ")
    if "PATTERN:" in desc:
        cut = desc.find("OUT:")
        return _cap_at_word(desc[:cut] if cut > 0 else desc, RENDER_BUDGET)
    return _cap_at_word(_first_sentence(desc), RENDER_LEGACY_MAX)


def render_heads_budgeted(descriptions: list[str]) -> list[str]:
    """Renderizza un pool con budget medio condiviso e hard cap per tool.

    Il totale non supera ``N * RENDER_BUDGET + RENDER_POOL_SLACK``. Si parte
    dal target autore ``HEAD_MAX`` e si distribuisce il residuo in modo equo
    alle sole teste che ne hanno bisogno. Il risultato e' deterministico e
    conserva byte-identico il comportamento per descrizioni entro HEAD_MAX.
    """
    normalized = [str(d or "").strip().replace("\n", " ")
                  for d in descriptions]
    heads: list[str] = []
    legacy: list[bool] = []
    for desc in normalized:
        is_legacy = "PATTERN:" not in desc
        legacy.append(is_legacy)
        if is_legacy:
            heads.append(_first_sentence(desc))
        else:
            cut = desc.find("OUT:")
            heads.append(desc[:cut] if cut > 0 else desc)

    target_cap = min(HEAD_MAX, RENDER_BUDGET, RENDER_HARD_MAX)
    caps = [min(len(head), RENDER_LEGACY_MAX if is_legacy else target_cap)
            for head, is_legacy in zip(heads, legacy)]
    total_budget = min(
        sum(len(head) for head in heads),
        len(heads) * RENDER_BUDGET + RENDER_POOL_SLACK,
    )
    remaining = max(0, total_budget - sum(caps))
    active = [i for i, head in enumerate(heads)
              if not legacy[i]
              and caps[i] < min(len(head), RENDER_HARD_MAX)]
    while remaining and active:
        share = max(1, remaining // len(active))
        next_active: list[int] = []
        for position, i in enumerate(active):
            ceiling = min(len(heads[i]), RENDER_HARD_MAX)
            add = min(share, ceiling - caps[i], remaining)
            caps[i] += add
            remaining -= add
            if caps[i] < ceiling:
                next_active.append(i)
            if not remaining:
                next_active.extend(active[position + 1:])
                break
        active = next_active

    return [_cap_at_word(head, cap) for head, cap in zip(heads, caps)]
