---
id: 0065
title: undo_last_turn classified atomic in PLANNER rule §2-ter
date: 2026-05-01
status: accepted
area: runtime
related:
  - 0058  # intent extractor LLM-based
---

<!-- Estensione del prompt PLANNER §2-ter (TOOL ATOMICO CON ok:false
IRRECUPERABILE → final_answer). §2-ter non aveva ADR formale prima
(era inline nel PLANNER_SYSTEM_NATIVE). Questa ADR formalizza
l'estensione che include `undo_last_turn` fra i tool atomici. -->

## Context

PLANNER prompt rule §2-ter (live in `agent_runtime.PLANNER_SYSTEM_NATIVE`,
introduced 30/4/2026 per UC19): se un tool atomico (originariamente
`get_now`, `get_places`) ritorna `ok:false` con stato di sistema
irrecuperabile, lo step successivo DEVE essere `final_answer`. Mai
ritentare.

UC85 (1/5/2026, edge battery): query `"annulla l'ultima azione"` con
nessun turno reversibile precedente. `undo_last_turn` ritorna
`ok:false, undone_count:0, skipped_count:1` (es. il turno precedente
era un `move_files` cross-mount non revertibile). Il PLANNER (Gemma 4
26B middle) ha provato `undo_last_turn` 3 volte di seguito → cap_same →
loop_break.

Il pattern e' identico a `get_now` ok:false: lo stato di sistema (no
turno revertibile) non si risolve con un retry. La regola §2-ter
copriva concettualmente questo caso ma non lo citava esplicitamente.

CLAUDE.md §4.5 prescriveva gia' "se undo_last_turn ritorna `ok:true`
con `undone_count >= 1`, lo step successivo DEVE essere final_answer";
il caso simmetrico `ok:false` o `undone_count:0` non era enunciato.

## Decision

Estendere la lista dei tool atomici di §2-ter per includere
`undo_last_turn`. Aggiunto esempio OK e ERRORE specifici per undo:

```
OK: undo_last_turn ok:false undone_count:0 → final_answer "Niente da
    annullare: l'ultima azione non e' reversibile o non c'e' un turno
    precedente."
ERRORE: undo_last_turn ok:false → undo_last_turn ok:false → loop_break.
```

Inoltre, esplicitato che il trigger di final_answer puo' essere sia
`ok:false` puro che `ok:false con undone_count:0`: per undo l'esito
parziale "ho provato ma non c'era nulla da fare" e' sostanzialmente
equivalente a un fallimento atomico.

Modifica vive in `runtime/agent_runtime.py` PLANNER_SYSTEM_NATIVE.

## Alternatives considered

* **Lasciare la regola al §4.5 (ok:true → final_answer) e aggiungere un
  separato "undo special-case"**: duplicazione concettuale.
  Concentrare tutto in §2-ter (TOOL ATOMICO) chiarisce che il pattern
  e' generale, non specifico a undo.
* **Hard-code una guard nel runtime per bloccare retry undo**: viola
  §7.3 (soluzioni generali, mai hardcoded). Il prompt PLANNER e' il
  posto giusto: educare il modello, non patchare reattivamente.
* **Cambiare il return shape di undo_last_turn (es. non emettere
  ok:false, sempre ok:true con flag interno)**: maschera il fatto
  oggettivo "non e' stato fatto nulla", viola §2.8 (no silent failure).

## Consequences

* UC85 converge in iter 2 (4/15 → 4/15 PASS, 2 retry undo entro cap=3).
* Pattern replicabile per futuri tool atomici (es. `request_new_executor`
  con `ok:false` permanente, `cap_pending_show` quando lista vuota).
* Il prompt PLANNER cresce di 4 righe: accettabile, la regola §2-ter
  diventa il "punto centrale" per i tool atomici e si capitalizzera'
  con altre estensioni invece di sparpagliare regole inline.
* Sub-ottimale residuo (1/5/2026): il PLANNER iter 2 chiama undo 2 volte
  prima di capire (sotto cap=3, kind=answer). Future improvement:
  rinforzare il prompt con un "ricorda: ok:false e' definitivo, non
  ritentare" piu' incisivo, o aggiungere stop early al primo
  DUPLICATE_CALL su tool atomico.
