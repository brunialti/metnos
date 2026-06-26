# SPDX-License-Identifier: AGPL-3.0-only
"""naming_grammar.py — Naming Authority + GBNF generator per §2.2.

Centralizza il vincolo del vocabolario chiuso §2.2 (verbi/oggetti/qualifier)
per TUTTI i generatori di proposte introspettive (telos lenses, introvertiva,
synt_multistage stage 1, skill importer mapping).

§7.9 deterministico: zero LLM. Parsa `runtime/vocab.py` (single source of
truth) e produce:

1. `validate_name(name)` — verifica conformita' (canonical+descriptor)
2. `suggest_canonical(intent_hint)` — mappa intent libera a nome canonico
3. `naming_grammar(live_executors)` — GBNF per LLM constrained generation
4. `parse_name(s)` — decompone in (canonical, descriptor) tupla

ADR-in-writing (21/5/2026): proposta 4° livello "descriptor" OPEN, fuori
grammar canonical. Vedi docs/it/architecture/naming_authority.html (TODO).

Convenzione (Naming Authority v2, 21/5/2026):
    Schema POSIZIONALE a 4 livelli, separatore unico `_`:

        <verb>_<object>[_<qualifier>[_<descriptor>]]

    Livelli:
      1. verb       (CHIUSO §2.2, 23 azioni)
      2. object     (CHIUSO §2.2, 23 oggetti)
      3. qualifier  (CHIUSO §2.2, 4 famiglie) — OPZIONALE
      4. descriptor (APERTO, kebab-case interno `[a-z0-9-]+`) — RICHIEDE qualifier

    Regola d'oro: il 4° livello ESTENDE, non RIMPIAZZA il 3°.
    Se serve estendere un nome a 2 livelli, la risposta giusta e':
      (a) usare un qualifier esistente,
      (b) proporre nuovo qualifier in vocab §2.2 (escalation),
      (c) lasciare nome 2-livello + contesto in proposed_action.

Esempi:
    "compute_files_loc"                       — 3-livello canonical
    "compute_files_loc_per-language"          — 4-livello: qualifier=loc, desc=per-language
    "compute_files_loc_excluding-tests"       — variant
    "find_dirs_empty_recursive"               — variant
    "set_tasks"                               — 2-livello (NO descriptor admesso)
    "set_tasks_invoice-lifecycle"             — INVALIDO (descriptor senza qualifier)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from vocab import (ACTIONS, OBJECTS, PROVIDER_SUFFIXES, QUALIFIERS,
                   qualifier_compatible, qualifiers_for_object)

# ── Separatori e regex ─────────────────────────────────────────────────
#
# Naming Authority v2 (ADR 0156 refinement 21/5/2026):
# Schema POSIZIONALE a 4 livelli, separatore unico `_`:
#
#     <verb>_<object>[_<qualifier>[_<descriptor>]]
#
# Regola d'oro: il 4° livello ESTENDE, non RIMPIAZZA il 3°.
# Un descriptor (4°) puo' apparire SOLO se anche il qualifier (3°) e'
# presente. Se si vuole estendere un nome a 2 livelli, la risposta giusta
# e':
#   (a) usare un qualifier esistente,
#   (b) proporre un nuovo qualifier nel vocab §2.2 (escalation),
#   (c) lasciare il nome 2-livello e mettere il contesto in proposed_action.
#
# Razionale: il delimiter `_` posizionale evita l'asimmetria che si aveva
# con `#`-separator (LLM emetteva `set_tasks_lifecycle` pensando a un
# qualifier OR a un descriptor, due interpretazioni indistinguibili).
# Posizionale = univoco.

# Descriptor kebab-case stretto:
# - contenuto interno [a-z0-9-]+, inizia/termina alfanumerico
# - hyphen `-` come separatore fra segmenti alfanumerici
# - NON underscore (riservato a separatore livelli §2.2)
# - NON doppi hyphen, leading/trailing hyphen
# - lunghezza 1-30
_DESCRIPTOR_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_DESCRIPTOR_MAX_LEN = 30

# Eccezioni semantiche §2.2 (entries meta-oggetto, no find/read/get_entries):
_ENTRIES_FORBIDDEN_VERBS = frozenset({"find", "read", "get"})

# System verbs riservati (§2.2): non possono comparire come prefisso
_SYSTEM_PSEUDO_VERBS = frozenset({"undo", "admin", "audit"})


@dataclass(frozen=True)
class NameComponents:
    """Decomposizione di un nome canonical+descriptor."""
    verb: str
    obj: str
    qualifier: Optional[str]
    descriptor: Optional[str]

    @property
    def canonical(self) -> str:
        parts = [self.verb, self.obj]
        if self.qualifier:
            parts.append(self.qualifier)
        return "_".join(parts)

    @property
    def full(self) -> str:
        if self.descriptor:
            return f"{self.canonical}_{self.descriptor}"
        return self.canonical


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: Optional[str] = None
    components: Optional[NameComponents] = None


# ── Validation ──────────────────────────────────────────────────────────

def parse_name(name: str) -> Optional[NameComponents]:
    """Decompone un nome posizionale in (verb, object, qualifier?, descriptor?).

    Schema: verb_object[_qualifier[_descriptor]]
    Split by `_` produce 2/3/4 parti. Parte 4 (descriptor) e' kebab-case
    (puo' contenere `-` ma non `_`). Parte 3 (qualifier) e' single token
    senza separatori.

    ECCEZIONE provider MULTI-TOKEN: un suffisso provider (`PROVIDER_SUFFIXES`,
    SoT) puo' contenere `_` (es. `google_workspace`). E' un'UNICA unita'
    qualifier-provider, non qualifier+descriptor. Se il nome termina con un
    provider noto, lo si stacca come qualifier prima dello split posizionale —
    cosi' `read_events_google_workspace` → (read, events, google_workspace, None)
    invece di (read, events, google, workspace). Fix bug latente naming 26/6.

    Ritorna None se la sintassi e' invalida (split count fuori 2-4)."""
    if not name or not isinstance(name, str):
        return None
    # Provider multi-token: stacca il suffisso provider PRIMA dello split, cosi'
    # le sue `_` interne non vengono lette come livelli posizionali.
    for prov in PROVIDER_SUFFIXES:
        if "_" in prov and name.endswith("_" + prov):
            head = name[: -(len(prov) + 1)]      # rimuove `_<prov>`
            hp = head.split("_")
            if len(hp) == 2:                      # verb_object_<provider>
                return NameComponents(verb=hp[0], obj=hp[1],
                                      qualifier=prov, descriptor=None)
            # head non e' verb_object pulito → cade al parsing standard sotto
            break
    parts = name.split("_")
    n = len(parts)
    if n < 2 or n > 4:
        return None
    verb = parts[0]
    obj = parts[1]
    qual = parts[2] if n >= 3 else None
    desc = parts[3] if n == 4 else None
    return NameComponents(verb=verb, obj=obj, qualifier=qual, descriptor=desc)


def validate_name(name: str,
                  live_canonicals: Optional[set] = None) -> ValidationResult:
    """Verifica conformita' §2.2 + 4° livello descriptor.

    Args:
      name: nome completo (canonical 2-4 livelli).
      live_canonicals: set di canonical 3-livello GIA' VIVI nel catalog.
        Se passato, attiva la regola "un livello alla volta":
        un nome 4-livello e' accettato SOLO se il suo canonical
        3-livello (verb_obj_qual) e' gia' in live_canonicals.
        Razionale: una proposta non puo' introdurre 3° + 4° insieme.

    Catches:
    - verb / object / qualifier fuori vocab
    - eccezione entries: no find/read/get_entries
    - system pseudo-verbs riservati
    - descriptor senza qualifier (regola posizionale)
    - descriptor con canonical 3-livello non-vivo (regola "uno alla volta")
    - descriptor sintassi (regex kebab-case)
    """
    nc = parse_name(name)
    if nc is None:
        return ValidationResult(False,
            "syntax invalid (expected verb_object[_qualifier[_descriptor]], 2-4 parts split by _)")
    if nc.verb in _SYSTEM_PSEUDO_VERBS:
        return ValidationResult(False, f"verb '{nc.verb}' is reserved system pseudo-verb")
    if nc.verb not in ACTIONS:
        return ValidationResult(False, f"verb '{nc.verb}' not in vocab §2.2 (23 actions)", nc)
    if nc.obj not in OBJECTS:
        return ValidationResult(False, f"object '{nc.obj}' not in vocab §2.2 (23 objects)", nc)
    # Regola posizionale: descriptor (4°) richiede qualifier (3°).
    if nc.descriptor and not nc.qualifier:
        return ValidationResult(False,
            "descriptor (4° livello) requires qualifier (3°) present: "
            "the 4th level EXTENDS, it does not REPLACE the 3rd. "
            "Either use an existing qualifier or propose a new one in vocab §2.2.",
            nc)
    if nc.qualifier and nc.qualifier not in QUALIFIERS:
        return ValidationResult(False, f"qualifier '{nc.qualifier}' not in vocab §2.2", nc)
    # R4: qualifier-object compatibility (deterministico §7.9)
    if nc.qualifier and not qualifier_compatible(nc.qualifier, nc.obj):
        return ValidationResult(False,
            f"qualifier '{nc.qualifier}' not semantically valid for object "
            f"'{nc.obj}' (R4 qualifier-object compatibility). Allowed objects "
            f"per qualifier in vocab.QUALIFIER_OBJECT_COMPAT.",
            nc)
    # Regola "uno alla volta": 4° livello richiede canonical 3-livello
    # gia' vivo nel catalog. Una proposta non puo' introdurre 3° + 4° insieme.
    if nc.descriptor and live_canonicals is not None:
        if nc.canonical not in live_canonicals:
            return ValidationResult(False,
                f"4° livello '{nc.descriptor}' richiede canonical 3-livello "
                f"'{nc.canonical}' gia' presente nel catalog. Propose una "
                f"NUOVA proposta separata che introduce '{nc.canonical}' "
                f"come 3-livello (RICHIEDE estensione vocab §2.2 se il "
                f"qualifier '{nc.qualifier}' non e' ancora ammesso per "
                f"l'object '{nc.obj}').",
                nc)
    # Eccezione entries
    if nc.obj == "entries" and nc.verb in _ENTRIES_FORBIDDEN_VERBS:
        return ValidationResult(
            False,
            f"'{nc.verb}_entries' violates §2.2 exception: entries is in-memory meta-object, "
            "no find/read/get_entries permitted",
            nc,
        )
    if nc.descriptor:
        if len(nc.descriptor) > _DESCRIPTOR_MAX_LEN:
            return ValidationResult(
                False,
                f"descriptor '{nc.descriptor}' exceeds max {_DESCRIPTOR_MAX_LEN} chars",
                nc,
            )
        if not _DESCRIPTOR_RE.match(nc.descriptor):
            return ValidationResult(
                False,
                f"descriptor '{nc.descriptor}' must be kebab-case "
                "([a-z0-9]+(-[a-z0-9]+)*); no underscores, no leading/trailing hyphen",
                nc,
            )
        # Evita pseudo-canonical (descriptor = verb o object §2.2)
        if nc.descriptor in ACTIONS or nc.descriptor in OBJECTS:
            return ValidationResult(
                False,
                f"descriptor '{nc.descriptor}' shadows a vocab token",
                nc,
            )
    return ValidationResult(True, None, nc)


# ── Naming Authority: suggest canonical ─────────────────────────────────

# Mapping intent libero → verbo canonical §2.2 (deterministico).
# Espandibile; tipici fuori-vocab che le proposte introspettive emettono.


# ── GBNF generator ──────────────────────────────────────────────────────

def _quote_jsonstring_enum(values) -> str:
    """Enum di JSON string completi: ogni alternativa e' '\"X\"' (con quote).
    Usato per token che compaiono come VALORI JSON standalone, es.
    `target_name` che e' un campo JSON intero."""
    return " | ".join(f'"\\"{v}\\""' for v in values)


def _quote_token_enum(values) -> str:
    """Enum di token nudi: ogni alternativa e' 'X' (senza quote).
    Usato per pezzi sintattici che vengono concatenati da regole esterne
    che aggiungono le quote JSON una sola volta intorno al risultato."""
    return " | ".join(f'"{v}"' for v in values)


def naming_grammar_fragment(*, live_executors: list[str]) -> str:
    """Fragment GBNF per nomi canonical (no SCAMPER outer JSON).

    Da incorporare in grammar piu' grandi. Definisce 4 regole top-level:
    - target_name: enum executor vivi (anti-hallucination)
    - new_op_name: verb+obj[+qualifier] (vocab CHIUSO)
    - descriptor: [a-z0-9_]{1,30}
    - canonical_or_null: union null|new_op_name

    Le altre regole (root, item, JSON outer) sono compito del caller.

    Esempio uso (SCAMPER):
        grammar = scamper_json_grammar(naming_grammar_fragment(
            live_executors=list(catalog.executors.keys()),
        ))
    """
    if not live_executors:
        target_enum = '""'
    else:
        target_enum = _quote_jsonstring_enum(sorted(set(live_executors)))
    verb_enum = _quote_token_enum(ACTIONS)
    # R4 (v3 ext): per ogni object, enum qualifier compat. Genera 19 rules
    # `<obj>-with-qual ::= "<obj>" ("_" <obj>-qualifier-token)?` con il set
    # di qualifier ammessi semanticamente per quell'object.
    per_object_rules = []
    obj_with_qual_alts = []
    for obj in OBJECTS:
        rule_obj = obj.replace("_", "-")  # GBNF rule names: kebab
        compat_quals = qualifiers_for_object(obj)
        if compat_quals:
            qenum = _quote_token_enum(compat_quals)
            per_object_rules.append(f'{rule_obj}-qualifier ::= {qenum}')
            per_object_rules.append(
                f'{rule_obj}-with-qual ::= "{obj}" ("_" {rule_obj}-qualifier)?'
            )
        else:
            # Nessun qualifier compat per questo object (degenere): solo 2-livello.
            per_object_rules.append(f'{rule_obj}-with-qual ::= "{obj}"')
        obj_with_qual_alts.append(f'{rule_obj}-with-qual')
    obj_with_qual_union = " | ".join(obj_with_qual_alts)
    per_object_block = "\n".join(per_object_rules)
    # Regola "uno alla volta" (v3): canonical-4 ammesso SOLO con canonical-3
    # gia' VIVO nel catalog (filtrato a 3-parti).
    live_3level = sorted({e for e in live_executors if len(e.split("_")) == 3})
    if live_3level:
        canonical3_enum = _quote_token_enum(live_3level)
        desc_4_rule = (
            'canonical-4-with-descriptor ::= "\\"" canonical-3-live '
            '"_" desc-segment ("-" desc-segment)* "\\""'
        )
        canon3_def = f"canonical-3-live ::= {canonical3_enum}"
    else:
        desc_4_rule = 'canonical-4-with-descriptor ::= "null"'  # degenere
        canon3_def = ""
    return f"""
target-name ::= {target_enum}

verb ::= {verb_enum}

{per_object_block}

obj-with-qual ::= {obj_with_qual_union}

{canon3_def}

canonical-2or3 ::= "\\"" verb "_" obj-with-qual "\\""

desc-alnum ::= [a-z0-9]
desc-segment ::= desc-alnum desc-alnum*
{desc_4_rule}

new-op-name ::= canonical-2or3 | canonical-4-with-descriptor | "null"
"""


def scamper_json_grammar(naming_fragment: str) -> str:
    """Grammar GBNF completo per output SCAMPER (JSON array of objects).

    Combina il fragment naming con la struttura JSON SCAMPER:
        [
          {
            "executor_target": "<target_name>",
            "new_op_name":     "<canonical>" | "<canonical#descriptor>" | null,
            "proposed_action": "<free text>",
            "rationale":       "<free text>"
          },
          ...
        ]

    `proposed_action` e `rationale` restano free-text JSON strings
    (creativita' preservata, vocab vincolato solo dove conta).
    """
    # NB: GBNF llama.cpp NON accetta rule body multiline — il body deve
    # stare su una sola riga. Le quattro field-key sono allineate qui in
    # f-string ma vengono unite a una linea sola alla generazione finale.
    return f"""root ::= "[]" | "[" item ("," item)* "]"
item ::= "{{" ws "\\"executor_target\\":" ws target-name "," ws "\\"new_op_name\\":" ws new-op-name "," ws "\\"proposed_action\\":" ws json-string "," ws "\\"rationale\\":" ws json-string ws "}}"
json-string ::= "\\"" json-char* "\\""
json-char ::= [^"\\\\] | "\\\\" ["\\\\/bfnrt]
ws ::= ws-char*
ws-char ::= " " | "\\t" | "\\n"
{naming_fragment}
"""


# ── CLI test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "validate":
        # validate <name>
        name = sys.argv[2]
        r = validate_name(name)
        print(json.dumps({
            "ok": r.ok,
            "reason": r.reason,
            "components": r.components.__dict__ if r.components else None,
        }, indent=2, ensure_ascii=False))
    elif len(sys.argv) > 1 and sys.argv[1] == "grammar":
        # genera grammar per un campione di executor
        sample = ["find_files", "compute_entries", "create_events",
                  "compute_signatures", "change_files_format"]
        frag = naming_grammar_fragment(live_executors=sample)
        full = scamper_json_grammar(frag)
        print(full)
    else:
        print("Usage: python3 -m runtime.naming_grammar validate <name>")
        print("       python3 -m runtime.naming_grammar grammar")
        sys.exit(1)
