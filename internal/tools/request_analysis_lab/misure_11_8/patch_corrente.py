#!/usr/bin/env python3
"""Patches under test in the current cycle, applied to the frozen bench module.

The bench file itself is never edited: each patch replaces a module attribute,
so the frozen checkpoint stays the reference. `apply` returns the names of the
patches that actually fired, and raises if a patch cannot find its anchor text,
so a silently ineffective cycle is impossible.
"""
from __future__ import annotations

import os

# --- cycle 2 --------------------------------------------------------------
# The prompt instructs exactly what the validator rejects. Validator, both
# branches (lines 1301-1305): a request record must carry an action and an
# object both different from `none`; ANY other role must carry `none` in both.
# The prompt instead tells the model to fill a non-request record with "the
# closest canonical action/object when clear" -- and for "non scaricare" it is
# perfectly clear, so the model complies and the frame is failed.
LICENCE = ("For non-request records use the closest canonical action/object when "
           "clear,\notherwise `none`; they are never executable intents.")
CONTRACT = ("Non-request records are never executable intents: set BOTH action and "
            "object\nto `none`, however clear the prohibited or described operation "
            "may be. Only a\nrequest record carries a canonical action and object, "
            "and there both MUST\ndiffer from `none`.")


def _contract(module) -> bool:
    if LICENCE not in module.BASE_INSTRUCTION:
        raise AssertionError("licence text not found in BASE_INSTRUCTION")
    module.BASE_INSTRUCTION = module.BASE_INSTRUCTION.replace(LICENCE, CONTRACT)
    return True


# --- cycle 4 --------------------------------------------------------------
# Cycle 3 wrote the coordination rule as a soft closing sentence inside a distant
# refinement block, and the model ignored it outright: the second record kept
# anchoring on the shared verb token. Measured cost: 39 -> 37, no gain on the
# target and two unrelated queries broken. So the rule moves to where the anchor
# is first defined, is stated as a prohibition, and its fallback is computable
# from a field the model has already committed to -- its own patient span.
ANCHOR_DEF = ("Set\n`predicate_anchor_token_id` to one numbered source token "
              "anchoring that\npredicate.")
ANCHOR_UNIQUE = ANCHOR_DEF + (
    " Two records MUST NOT carry the same anchor token, and anchors\nMUST grow "
    "with record order. When one verb governs several coordinated\noperations, the "
    "first record anchors on that verb and every later record\nanchors on the first "
    "token of its own patient.")


def _anchor_unique(module) -> bool:
    if ANCHOR_DEF not in module.BASE_INSTRUCTION:
        raise AssertionError("anchor definition not found in BASE_INSTRUCTION")
    module.BASE_INSTRUCTION = module.BASE_INSTRUCTION.replace(ANCHOR_DEF, ANCHOR_UNIQUE)
    return True


# Measured, both discarded: writing the coordination rule as a closing sentence
# in a distant block cost 39 -> 37, and moving it to the anchor definition as a
# prohibition with a patient fallback cost 39 -> 36. In both the model kept
# anchoring the second coordinated record on the shared verb token, so the rule
# was not merely misplaced: it loses against the model's prior. The conflict is
# in the contract, not in the wording -- one verb governing two operations has
# only one verb token, and the validator wants one unique increasing anchor per
# record. Best measured state stays cycle 2.
def _anchor_repair(module) -> bool:
    """The code derives what the grammar cannot supply. See `riparo_ancore.py`
    -- fires only on a collision, inert otherwise."""
    import riparo_ancore

    return riparo_ancore.install(module)


# --- cycle 5 --------------------------------------------------------------
# The ablation says TIE_BREAK is the only block whose removal IMPROVES the
# result, and it is also the only block that negates by naming the canonical
# route it rejects (31% of its sentences, against 0% for the two load-bearing
# ones). Its six facts are already asserted in ONTOLOGY, so removing it loses no
# information.
def _drop_tie_break(module) -> bool:
    if len(module.TIE_BREAK_REFINEMENTS) < 100:
        raise AssertionError("TIE_BREAK already empty")
    module.TIE_BREAK_REFINEMENTS = ""
    return True


# --- cycle 6, the law's prediction ----------------------------------------
# Same six facts, asserted instead of arbitrated: no canonical route is ever
# named in order to be excluded. If the damage came from the negation, this
# scores like the removal; if it came from repeating what ONTOLOGY already says,
# it scores like the original.
TIE_BREAK_ASSERTIVE = """\
Canonical assignments:

- A person/guest's enrollment, registration, or membership in an identity
registry is persons even when the registry itself is not named. A question
about the requester's own identity is read/persons. Enumeration of registered
people is get/persons.
- A request to transfer existing messages between mailboxes is move/messages.
- Reading/downloading the content represented by an already named file is
read/files. An independently commanded save or write is its own request record.
- Selecting, keeping, excluding, or discarding members according to a property
is filter, and source data remains intact. Role forbid belongs to an operation
the grammar prohibits.
- Directory/container aggregate size is find/dirs in the Metnos intent
ontology.
- Adding or retrieving descriptive metadata for photo files is get/files.
Explicitly changing pixel content is set/images.
"""


def _assertive_tie_break(module) -> bool:
    module.TIE_BREAK_REFINEMENTS = TIE_BREAK_ASSERTIVE
    return True


# --- cicli 9-12: togliere gli specchi dall'uscita ---------------------------
# Quattro valori sono chiesti due volte, in `semantic_heads` e in `predicates`,
# e il validatore pretende che coincidano: e' informazione zero e quattro
# occasioni per record di contraddirsi. Si tolgono uno per volta, dal meno
# rischioso (l'arco, che e' quello che ha fatto cadere [31]) al piu' rischioso
# (`role`, che oggi sta accanto a verb/object mentre il modello li sceglie).
def _role_grammar(module) -> bool:
    """Applied last, so it wraps the schema cut: see `grammatica_ruolo.py`."""
    import grammatica_ruolo

    return grammatica_ruolo.install(module)


def _cut(*fields):
    def apply_cut(module) -> bool:
        import uscita_specchi

        return uscita_specchi.install(module, fields)

    return apply_cut


_SETS = {
    "c5": (("contratto_ruolo_allineato_al_validatore", _contract),
           ("tie_break_rimosso", _drop_tie_break)),
    "c6": (("contratto_ruolo_allineato_al_validatore", _contract),
           ("tie_break_affermativo", _assertive_tie_break)),
    "c7": (("contratto_ruolo_allineato_al_validatore", _contract),
           ("tie_break_rimosso", _drop_tie_break),
           ("riparo_deterministico_ancore", _anchor_repair)),
    "c9": (("contratto_ruolo_allineato_al_validatore", _contract),
           ("tie_break_rimosso", _drop_tie_break),
           ("specchio_arco_tolto", _cut("input_from_predicate_id"))),
    "c10": (("contratto_ruolo_allineato_al_validatore", _contract),
            ("tie_break_rimosso", _drop_tie_break),
            ("specchi_arco_id_ancora_tolti",
             _cut("input_from_predicate_id", "predicate_id",
                  "predicate_anchor_token_id"))),
    "c11": (("contratto_ruolo_allineato_al_validatore", _contract),
            ("tie_break_rimosso", _drop_tie_break),
            ("tutti_e_quattro_gli_specchi_tolti",
             _cut("input_from_predicate_id", "predicate_id",
                  "predicate_anchor_token_id", "role"))),
    # c12: il contratto di ruolo passa dal prompt alla grammatica. `role` resta
    # nei predicati perche' e' il discriminante dell'unione.
    "c12": (("contratto_ruolo_allineato_al_validatore", _contract),
            ("tie_break_rimosso", _drop_tie_break),
            ("specchi_arco_id_ancora_tolti",
             _cut("input_from_predicate_id", "predicate_id",
                  "predicate_anchor_token_id")),
            ("contratto_ruolo_nella_grammatica", _role_grammar)),
}

PATCHES = _SETS.get(os.environ.get("SET_PATCH", ""), _SETS["c5"])


def apply(module) -> list[str]:
    return [name for name, fn in PATCHES if fn(module)]
