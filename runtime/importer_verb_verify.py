"""importer_verb_verify — Layer 6.bis di synth admission (ADR 0128, 12/5/2026).

Verifica deterministica che un `ExecutorPlan` prodotto dal skill_translator
aderisca alla policy di boundary verb-as-data per executor importati.

Il bug live 12/5 ha mostrato drift sistemico sul skill importer ADR 0123:
provider verbs `get`/`change`/`set` mappati letteralmente, senza distinguere
fra:
- `get` = snapshot metadata (Metnos `get_*`) vs `get` = fetch content (Metnos `read_*`),
- `change` = body modify (`write_*`) vs `change` = shape modify (`change_*`) vs
  `change` = state/labels (`set_*`),
- `set` = state upsert (`set_*`) vs `set` = create-only (`create_*`) vs
  `set` = ACL grant (`share_*`).

L'audit 12/5 ha trovato 8 cluster (H5-H10 + estensioni). Questo verifier
li intercetta a translate-time PRIMA che il manifest venga firmato, e
puo' essere chiamato anche post-import per audit periodico.

Determinismo §7.9: tabella `verb_by_target_side` in skill_vocab_map.json e'
la single source of truth. Niente LLM, niente regex euristici.

API principale:
- `check_plan(plan, *, sub_command, vocab_map)` -> Verdict
- `audit_existing_imports(root_path)` -> list[(name, Verdict)]
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config as _C  # §7.11


VOCAB_MAP_PATH = Path(__file__).resolve().parent / "skill_vocab_map.json"


@dataclass
class Verdict:
    """Esito della verifica.

    `aligned=True` significa: la coppia `(target_kind, side_effect)` deducibile
    dal sub-command rinvia al verb scelto dal plan. `mismatch_kind` enumera
    le 4 famiglie di drift sistemico identificate dall'audit.
    """

    aligned: bool
    chosen_verb: str
    expected_verb: str = ""
    target_kind: str = ""
    side_effect: str = ""
    mismatch_kind: str = ""    # get_drift|change_overload|set_overload|share_drift|share_collapse|unknown
    mismatch_reason: str = ""

    def __bool__(self) -> bool:
        return self.aligned


# ---------------------------------------------------------------------------
# Vocab map loader (riusato da skill_translator)
# ---------------------------------------------------------------------------


def _load_vocab_map(path: Path | None = None) -> dict:
    """Carica skill_vocab_map.json. Cached non e' necessario qui (chiamata
    rara: solo a import-time + audit periodico)."""
    p = path or VOCAB_MAP_PATH
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Mismatch classifier
# ---------------------------------------------------------------------------


def classify_mismatch(chosen_verb: str, expected_verb: str) -> str:
    """Classifica il tipo di drift fra `chosen` ed `expected`.

    Le 4 famiglie sono quelle dell'audit 12/5/2026 (Sprint M):
    - `get_drift`: chosen=`get` ma expected=`read` (provider get = fetch content).
    - `change_overload`: chosen=`change` ma expected ∈ {write, set} (append/labels
      mappati a change erroneamente).
    - `set_overload`: chosen=`set` ma expected ∈ {create, share} (create-only o
      share-access mappati a set).
    - `share_drift`: chosen ∈ {set, send} ma expected=`share` (drive.share
      mappato a set/send).
    - `share_collapse`: chosen=`share` ma expected ∈ {send, set} (uso indebito
      di share dove c'e' invece copia o upsert).
    - `unknown`: drift non riconducibile alle famiglie sopra.
    """
    if chosen_verb == expected_verb:
        return ""
    if chosen_verb == "get" and expected_verb == "read":
        return "get_drift"
    if chosen_verb == "change" and expected_verb in ("write", "set"):
        return "change_overload"
    # share_drift va PRIMA di set_overload: (set, share) e' piu'
    # specificamente "share drift" che "set overload generico verso terminal
    # create" — l'ACL grant e' la deroga critica dell'audit 12/5.
    if chosen_verb in ("set", "send") and expected_verb == "share":
        return "share_drift"
    if chosen_verb == "set" and expected_verb == "create":
        return "set_overload"
    if chosen_verb == "share" and expected_verb in ("send", "set"):
        return "share_collapse"
    return "unknown"


# ---------------------------------------------------------------------------
# Plan checker (PRE-import)
# ---------------------------------------------------------------------------


def check_plan(plan: Any, *, domain: str = "", action: str = "",
               vocab_map: dict | None = None) -> Verdict:
    """Verifica che il `plan.verb` matchi la (target_kind, side_effect) del
    contesto `(domain, action)`.

    DEVI: passare `domain`/`action` se il `plan` non li espone (vecchi
    skill_translator). I plan recenti hanno `plan.skill_domain`/`plan.skill_action`.
    NON DEVI: invocare per executor non importati (handcrafted/synth puri non
    hanno provenance contestuale).
    OK: check_plan(plan_for_drive_share_with_verb_share) -> aligned=True.
    ERRORE: check_plan(plan_for_drive_share_with_verb_set) -> aligned=False,
    mismatch_kind="share_drift".
    """
    vm = vocab_map or _load_vocab_map()
    contextual = vm.get("contextual", {})
    vm.get("verb_by_target_side", {})

    chosen = getattr(plan, "verb", "") or ""
    # Permetti override esplicito; fallback ai campi del plan.
    dom = domain or getattr(plan, "skill_domain", "") or ""
    act = action or getattr(plan, "skill_action", "") or ""

    if not (dom and act):
        # Nessun contesto noto: il plan non viene da un importer (es. synth
        # interno) — non ci compete. Restituisci aligned=True (no-op).
        return Verdict(aligned=True, chosen_verb=chosen,
                       mismatch_kind="",
                       mismatch_reason="no skill context (non-imported plan)")

    ctx = contextual.get(f"{dom}:{act}")
    if not ctx:
        # Domain:action non in contextual: non posso verificare.
        # L'audit periodico flaggera' questo come "uncovered" — TODO sprint
        # successivo (estendere contextual o regola di pass-through).
        return Verdict(aligned=True, chosen_verb=chosen,
                       mismatch_kind="",
                       mismatch_reason=f"no contextual cell for {dom}:{act}")

    expected = ctx.get("verb", "")
    target_kind = ctx.get("target_kind", "")
    side_effect = ctx.get("side_effect", "")

    # NB sulla `verb_by_target_side`: e' un'INDICAZIONE PROTOTIPICA del verb
    # canonico per una `(target_kind, side_effect)`, non un constraint
    # esclusivo. Una stessa coppia puo' supportare PIU' verbi a seconda
    # dell'azione provider:
    #   gmail.search (content|fetch_content)  -> find
    #   gmail.get    (content|fetch_content)  -> read
    # Entrambi sono validi: la cella `contextual` distingue list/search/get
    # per action. Quindi NON segnaliamo un drift "interno" se la cella
    # contextual ha un verb diverso da `verb_by_target_side`: la cella
    # contextual ha priorita'. La tabella verb_by_target_side resta utile
    # come template di estensione (cosa scrivere per una nuova cella).

    if chosen == expected:
        return Verdict(aligned=True, chosen_verb=chosen, expected_verb=expected,
                       target_kind=target_kind, side_effect=side_effect)

    return Verdict(
        aligned=False,
        chosen_verb=chosen,
        expected_verb=expected,
        target_kind=target_kind,
        side_effect=side_effect,
        mismatch_kind=classify_mismatch(chosen, expected),
        mismatch_reason=(
            f"chosen verb {chosen!r} does not match expected {expected!r} "
            f"for (target_kind={target_kind!r}, side_effect={side_effect!r})"
        ),
    )


# ---------------------------------------------------------------------------
# Post-import audit
# ---------------------------------------------------------------------------


def audit_existing_imports(root: Path | None = None,
                           vocab_map: dict | None = None) -> list:
    """Scan dei manifest importati in `<USER_DATA>/executors/skills/` (ADR 0160)
    + legacy `<USER_DATA>/executors/_imports/` (ADR 0123) e verifica per ognuno
    che il `name` del manifest matchi la cella contestuale derivabile da
    `[provenance].source_subcommand`.

    Ritorna lista `[(executor_name, Verdict)]` ordinata per name.

    Determinismo §7.9: read-only sui manifest, lookup tabellare.
    """
    import tomllib
    from skills_paths import skill_roots as _sr

    vm = vocab_map or _load_vocab_map()
    if root is not None:
        bases = [root]
    else:
        bases = _sr(include_builtin=False)
    if not bases:
        return []

    out: list = []
    skill_dirs = []
    for base in bases:
        if not base.is_dir():
            continue
        for sd in sorted(base.iterdir()):
            if sd.is_dir():
                skill_dirs.append(sd)
    for skill_dir in skill_dirs:
        for ex_dir in sorted(skill_dir.iterdir()):
            if not ex_dir.is_dir():
                continue
            mf = ex_dir / "manifest.toml"
            if not mf.is_file():
                continue
            try:
                doc = tomllib.loads(mf.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                continue
            name = doc.get("name") or ex_dir.name
            prov = doc.get("provenance") or {}
            sub_cmd = prov.get("source_subcommand") or ""

            # Format atteso: "drive share" o "calendar list (composed)".
            # Il marker `(composed)` segnala un executor sintetizzato a
            # partire da PIU' sub-command della skill (es. find_events_empty
            # combina calendar.list + computazione dei gap). Per i composed
            # l'azione provider letterale non riflette il verb canonico
            # Metnos: skippiamo il check (sono pipeline derivate, non
            # 1:1 con un'API call).
            if "(composed)" in sub_cmd:
                out.append((name, Verdict(
                    aligned=True, chosen_verb=name.split("_", 1)[0].lower(),
                    mismatch_kind="",
                    mismatch_reason="composed executor, skipped (pipeline derived)",
                )))
                continue

            tokens = sub_cmd.split()
            if len(tokens) < 2:
                continue
            dom = tokens[0].lower()
            act = tokens[1].lower()

            # Estrai verb dal name canonical: primo segmento prima del primo `_`.
            chosen_verb = name.split("_", 1)[0].lower() if "_" in name else name.lower()

            # Costruisci un plan-like minimal per check_plan.
            class _PlanShim:
                pass
            plan = _PlanShim()
            plan.verb = chosen_verb
            plan.skill_domain = dom
            plan.skill_action = act

            verdict = check_plan(plan, vocab_map=vm)
            out.append((name, verdict))

    return out
