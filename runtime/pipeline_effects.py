"""pipeline_effects.py — conteggio deterministico §7.9 degli effetti REALI
di un turno, condiviso fra i due runtime (12/6/2026).

Consumatori:
  - agent_runtime (TurnLog.write): notice anti-falso-successo + soppressione
    push schedulato (recurring_tasks._scheduled_push_is_noop);
  - engine/dispatch._maybe_record_fastpath: criterio di EFFICACIA del
    fastpath L0 — un piano «ok ma a vuoto» (mutante a 0 effetto reale)
    NON viene cachato (vedi ineffective_mutations).

Shape-agnostic: accetta sia gli step del runtime ReAct (attr/chiave
`chosen_tool`, result anche JSON-string) sia gli StepRun dell'engine v2
(attr `tool`, result dict).
"""
from __future__ import annotations

import json

# Prefissi dei tool MUTANTI (side-effecting). Stessa famiglia di
# TurnLog._MUTATE_SUCCESS_KEYS / agent_runtime._detect_false_not_found.
MUTATING_TOOL_PREFIXES = ("delete_", "move_", "change_", "send_", "create_",
                          "set_", "write_", "share_", "render_")

# Counter di successo mutating, in ordine di specificità (il primo intero
# presente vince; fallback len(results)).
MUTATE_COUNT_KEYS = (
    "n_deleted", "n_moved", "n_sent", "n_created", "n_written",
    "n_set", "n_shared", "n_changed", "n_ordered", "ok_count",
)


def _step_tool(s) -> str | None:
    """Nome tool dello step: `chosen_tool` (ReAct) o `tool` (engine StepRun)."""
    for attr in ("chosen_tool", "tool"):
        v = getattr(s, attr, None)
        if v:
            return v
        if isinstance(s, dict) and s.get(attr):
            return s[attr]
    return None


def _step_result(s) -> dict | None:
    """Result dello step come dict (decodifica JSON-string del ReAct log)."""
    res = getattr(s, "result", None)
    if res is None and isinstance(s, dict):
        res = s.get("result")
    if isinstance(res, str):
        try:
            res = json.loads(res)
        except Exception:
            return None
    return res if isinstance(res, dict) else None


def _mutation_count(res: dict) -> int | None:
    """Effetto contabile di uno step mutante: primo counter MUTATE_COUNT_KEYS
    presente, fallback len(results). None = output non contabile."""
    for k in MUTATE_COUNT_KEYS:
        if isinstance(res.get(k), int):
            return res[k]
    if isinstance(res.get("results"), list):
        return len(res["results"])
    return None


def _step_args(s) -> dict:
    """Args risolti dello step (resolved_args/args/raw_args)."""
    for attr in ("resolved_args", "args", "raw_args"):
        v = getattr(s, attr, None)
        if isinstance(v, dict):
            return v
        if isinstance(s, dict) and isinstance(s.get(attr), dict):
            return s[attr]
    return {}


def pipeline_effect_counts(steps) -> dict | None:
    """Conteggio deterministico §7.9 degli effetti REALI di un turno.

    Ritorna None se NESSUNO step espone output contabile (turno non
    giudicabile: niente entries/results/ok_count), altrimenti:
      {countable, items, mutations, mutating_attempted, failures}
    - items     = elementi prodotti dagli step producer (len(entries)).
    - mutations = elementi REALMENTE processati dagli step mutating
                  (ok_count/n_*/len(results), §2.8).
    - failures  = step con ok=False (un run con errori non e' "vuoto").
    """
    countable = items = mutations = failures = 0
    mutating_attempted = False
    for s in steps or []:
        tool = _step_tool(s)
        if not tool or tool == "final_answer" or tool.startswith("@"):
            continue
        res = _step_result(s)
        if res is None or res.get("_duplicate") is True:
            continue
        if res.get("ok") is False:
            failures += 1
            continue
        if any(tool.startswith(p) for p in MUTATING_TOOL_PREFIXES):
            mutating_attempted = True
            # §2.8: mutating su `entries` VUOTE (pipeline-dati a 0 input) =
            # artefatto/azione VUOTA, 0 effetto reale (es. spreadsheet da 0
            # fatture, send a 0 destinatari). Contabile ma NON una mutazione →
            # il guard anti-falso-successo scatta. NB: i create "standalone"
            # (crea cartella X, senza arg entries) NON entrano qui → niente
            # falso-positivo. Turn 36a40c35/3fd7add6.
            _a = _step_args(s)
            if isinstance(_a.get("entries"), list) and len(_a["entries"]) == 0:
                countable += 1
                continue
            n = _mutation_count(res)
            if n is None:
                continue
            countable += 1
            mutations += max(0, n)
        elif isinstance(res.get("entries"), list):
            countable += 1
            items += len(res["entries"])
        elif isinstance(res.get("results"), list):
            countable += 1
            items += len(res["results"])
        elif isinstance(res.get("ok_count"), int):
            countable += 1
            items += max(0, res["ok_count"])
    if countable == 0 and failures == 0:
        return None
    return {"countable": countable, "items": items, "mutations": mutations,
            "mutating_attempted": mutating_attempted, "failures": failures}


def ineffective_mutations(steps) -> list[str]:
    """Tool dei passi MUTANTI eseguiti «a vuoto» (criterio EFFICACIA L0,
    12/6/2026 — bug live 1dcc8307: delete_credentials n_deleted=0 su
    «cancella l'enrollment» cachato come fastpath → misroute auto-perpetuato).

    Confine (deterministico §7.9, per-step):
    - MUTANTE con counter contabile == 0 (n_*/ok_count/len(results)) o
      ok=False → «a vuoto»: il turno ha risposto ma NON ha prodotto
      l'effetto che il verbo d'azione richiedeva.
    - PRODUCER (find/read/list/get/filter) a 0 risultati → esito VALIDO
      (vuoto legittimo): NON riguarda questo predicato.
    - MUTANTE saltato da guard condizionale (if_prev_entries_nonempty) non
      compare fra gli step eseguiti → non giudicato.
    - MUTANTE senza output contabile o result _duplicate → non giudicabile
      → non segnalato (conservativo: mai bloccare senza evidenza).

    Ritorna la lista (eventualmente vuota) dei tool incriminati, utile per
    il log §2.8.
    """
    bad: list[str] = []
    for s in steps or []:
        tool = _step_tool(s)
        if not tool or tool == "final_answer" or tool.startswith("@"):
            continue
        if not any(tool.startswith(p) for p in MUTATING_TOOL_PREFIXES):
            continue
        res = _step_result(s)
        if res is None or res.get("_duplicate") is True:
            continue
        if res.get("ok") is False:
            bad.append(tool)
            continue
        # §2.8/efficacia L0: mutante che CONSUMA entries=[] = «a vuoto»
        # (artefatto vuoto su input vuoto), anche se n_created>0 → il piano
        # NON va cachato. Turn e591854e/71117eef: spreadsheet da 0 fatture
        # cachato come fastpath e ri-servito, iterando l'errore.
        _a = _step_args(s)
        if isinstance(_a.get("entries"), list) and len(_a["entries"]) == 0:
            bad.append(tool)
            continue
        n = _mutation_count(res)
        if n is not None and n <= 0:
            bad.append(tool)
    return bad


def counts_indicate_noop(counts) -> bool:
    """True se i conteggi-effetti indicano una pipeline «a vuoto» (0 effetto
    REALE). Predicato condiviso (§7.9, SoT) fra:
      - recurring_tasks._scheduled_push_is_noop (push schedulato);
      - treated_issues_guard.suppress_scheduled_notify (notifica in-piano).

    Regole:
    - counts None → NON a vuoto (non giudicabile: nessuno step contabile,
      es. pure-send senza upstream → la notifica È il deliverable).
    - failures>0 → NON a vuoto (errori vanno riportati §2.8).
    - mutanti tentati → a vuoto se 0 mutazioni reali.
    - solo-lettura → a vuoto se ci sono step contabili ma 0 items prodotti.
    """
    if not isinstance(counts, dict):
        return False
    if counts.get("failures"):
        return False
    if counts.get("mutating_attempted"):
        return counts.get("mutations", 0) == 0
    return counts.get("countable", 0) > 0 and counts.get("items", 0) == 0
