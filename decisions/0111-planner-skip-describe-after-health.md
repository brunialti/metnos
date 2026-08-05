---
id: 0111
title: PLANNER skip describe_entries dopo get_processes con health
date: 2026-05-07
status: accepted
area: runtime, planner, describe_entries
related:
  - 0095  # output formatter deterministico (_fmt_health_block)
  - 0099  # runtime perf (seed-step) — pattern di intercept deterministico
  - 0102  # thinking-leak scrubber (sintomo correlato)
  - 0104  # report runtime user-facing i18n compliance
complements:
  - 0095
  - 0102
---


## Context

Turn live 7/5/2026 21:32 (turn `62f80f47`). Query utente: «stato sistema».

Pipeline ReAct osservata:
1. step1 = `get_processes(include_health=true)` → ok, observation con
   `entries=[10 processi]` + `health={load, memory, disk, services}`.
2. step2 = `scratchpad_read` (data piping helper).
3. step3 = `describe_entries(from_step=1, ...)` (PLANNER hint dopo
   producer).
4. final_message contiene DUE blocchi sovrapposti:

```
📊 Stato server
Carico: 1m 0.47, 5m 0.52, 15m 0.41 (uptime 173h)
RAM: 36.2% (43/121 GB), swap 11%
Dischi: / 26.4% · ...
Servizi: http ✗ · ...

Salute Generale:
  Carico (Load Avg): Non disponibile (il sistema non ha restituito
                     i dati di carico).
  Uptime: Non disponibile.
  Memoria: Non disponibile.
  Disco: Non disponibile.
  Servizi: Non disponibile.
```

Il primo blocco e' deterministico, prepended da
`TurnLog._prepend_health_block_if_any` (CLAUDE.md §10.6.22, ADR 0095)
con i dati REALI presi da `obs.health`. Il secondo blocco e' la sintesi
LLM di `describe_entries` che dichiara «non disponibile» su tutti i
campi salute. L'utente vede **due verita' contraddittorie** nello
stesso messaggio.

Causa root: `describe_entries` riceve solo le `entries` (lista processi)
via espansione di `from_step`. Il campo `health` (sibling top-level di
`entries` nell'observation) NON viene propagato — `resolve_from_step`
estrae solo il primo campo lista canonico (`entries`/`matches`/...).
Il LLM interno di describe_entries conclude correttamente che salute
non e' nelle entries → dichiara «non disponibile» — ma il blocco
prepended contraddice questa sintesi.

Sintomo aggravato dalla regola del PLANNER (post-ADR 0102) che forza
`describe_entries` come step intermedio dopo i producer per evitare
thinking-leak. La regola e' giusta in generale; fallisce sul caso
specifico in cui `entries` e' affiancato da un campo strutturato non
visibile alla pipeline describe_entries.


## Decision

Tre difese in profondita' (pattern §7.9 deterministico > LLM sui due
livelli backstop, prompt-engineering per il livello educativo):

### Level 1 — PLANNER prompt: regola (Z.cinque) HEALTH BLOCK

Aggiunta in `runtime/prompts/it/planner.j2` dopo (Z.quater), prima di
(A) DISCOVERY. Stile §6 prescrittivo (DEVI / NON DEVI / OK / ERRORE).
Insegna al PLANNER che dopo `get_processes(include_health=true)` ok
con `health` non vuoto, il prossimo step DEVE essere `final_answer`,
NON `describe_entries`. Spiega esplicitamente che describe_entries
vede solo entries → dichiarerebbe «non disponibile» contraddicendo
il blocco prepended.

Limite: il LLM puo' comunque ignorare la regola sotto pressione di
prompt complessi. Educativo, non deterministico.

### Level 2 — describe_entries `health_context`

Difesa intermedia: se il PLANNER chiama `describe_entries` lo stesso,
il runtime inietta deterministicamente `health_context: <dict>` quando
lo step sorgente (raw_args.from_step) ha un `health` non vuoto.

In `runtime/agent_runtime.py` (caso speciale describe_entries):

```python
if chosen_name == "describe_entries":
    _fs = raw_args.get("from_step")
    if isinstance(_fs, int) and 1 <= _fs <= len(history_for_refs):
        _src_obs = history_for_refs[_fs - 1].get("observation", {})
        _h = _src_obs.get("health")
        if isinstance(_h, dict) and _h:
            args = dict(args)
            args["health_context"] = _h
    obs = handle_describe_entries(args, ...)
```

In `runtime/describe_entries.py` (`handle_describe_entries`): se
`health_context` e' presente, pre-pend al prompt LLM un blocco:

```
STATO SERVER GIA' RIASSUNTO (NON RIPETERE, NON DICHIARARE
'NON DISPONIBILE'):
<_fmt_health_block(health) deterministico>

Il tuo compito: riassumi SOLO le entries (processi) sotto.
Carico/RAM/Dischi/Servizi sono GIA' nel blocco sopra, non
commentarli, non ripeterli.
```

Cosi' il LLM, anche chiamato per errore, NON dichiarera' «non
disponibile»: ha visibilita' diretta dei numeri salute via il blocco
`_fmt_health_block` riusato da orchestration.py.

Nota: usa il formatter di ADR 0095 (zero LLM, §7.9), non duplica
logica di rendering.

### Level 3 — runtime auto-final post-get_processes-with-health

Backstop deterministico (§7.9). Dopo che `get_processes` ritorna ok
con `health` non vuoto, il runtime salta il PLANNER step 2+ e chiude
il turno con `final_message=""` (vuoto). `TurnLog.write()` invoca
`_prepend_health_block_if_any` che compone il blocco "Stato server"
+ "Top processi" (tabella ADR 0095) come messaggio finale completo.

Skip-condition (heuristic): non applicare auto-final se l'utente ha
richiesto un'azione esplicita.
- `intent.verb` in `_ACTION_VERBS_PRED` (`move`, `delete`, `send`,
  `write`, `extract`, `create`, `compress`, `compute`, `set`,
  `render`, `change`).
- query lowercased contiene una keyword imperativa: `kill`, `uccidi`,
  `ferma`, `termina`, `stop `, `spegni`, `manda`, `invia`, `scrivi`,
  `esegui`, `lancia`.

Posizione: in `agent_runtime.run_turn`, subito dopo `step.result =
obs` per executor regolari, prima del check `approval_required`.

Determinismo: zero LLM nel path. Heuristic basata su set chiusi
(action verbs + keyword imperative bilingue IT+EN parziale).


## Consequences

### Positive

- L'utente non vede piu' contraddizioni sullo stato server.
- Per query «stato sistema», skip del PLANNER step 2+ → riduce
  latenza ~3-5s (un PLANNER call + un describe_entries call evitati).
- Difesa in profondita': anche se Level 1 fallisce (LLM ignora la
  regola), Level 2 corregge il prompt, Level 3 evita la chiamata.
- Riusa `_fmt_health_block` di ADR 0095 — niente duplicazione
  logica di rendering.

### Negative / Limiti

- Level 3 short-circuita anche query che richiederebbero analisi
  esplicita (es. «trova il processo che mangia piu' RAM»). La
  heuristic action-verb intercetta i casi imperativi, ma non
  esaurisce ogni intento. Casi residui: l'utente puo' chiedere un
  rilancio (es. «descrivimi i processi»).
- Pattern accoppiato a `get_processes` per nome — se in futuro un
  altro executor produce `health`, va aggiunto alla condizione.
  Acceptable: il pattern `health` come campo top-level e' specifico
  di get_processes (cf. ADR 0095 §1).

### Risks mitigated

- Falsa sicurezza dell'utente (CLAUDE.md §2.8): «non disponibile» da
  describe_entries era un silent failure di completezza informativa
  (i dati c'erano, il LLM non li vedeva).
- Spreco di latenza: describe_entries inutile su contenuto
  gia'-renderizzato deterministicamente.


## Test

`tests/runtime/engine/test_health_planner_finalize.py` — 8 test:

- Level 1 (2): planner.j2 contiene `(Z.cinque)` con marker stile §6
  (DEVI/NON DEVI/OK/ERRORE) + riferimenti a `get_processes`,
  `describe_entries`, «non disponibile».
- Level 2 (3): `health_context` non vuoto pre-pend «STATO SERVER GIA'
  RIASSUNTO» al prompt LLM con i numeri reali; senza
  `health_context` nessuna pre-pend; `health_context={}` no-op.
- Level 3 (3): `_prepend_health_block_if_any` con `final_message=""`
  produce blocco completo (>50 char), nessun «non disponibile»;
  idempotente (doppia invocazione non duplica).

Suite full runtime: 781 PASS / 1 FAIL pre-esistente
(`test_pipeline_smoke.py::test_full_pipeline_find_read_group` —
find_urls timing flake non correlato).

Smoke battery: vedi report di sessione.

Test di convergenza programmatica live (3 query) deferred a
ambiente Telegram/HTTP daemon: replicabile manualmente con i 3 input
- «stato sistema»
- «come va il server?»
- «mostrami carico e ram»

In tutti e tre, il PLANNER deve produrre `get_processes(include_health=true)`
come step 1 (gia' coperto dal prefilter primary tools per object,
ADR 0075). Level 3 short-circuita prima del PLANNER step 2 → step
finale = step 1 stesso, final_message = blocco "Stato server" +
"Top processi" come tabella, no «non disponibile».


## Alternatives considered

1. **Solo Level 1 (prompt-only)**: scartato — Gemma 4 26B sotto
   pressione ignora regole specifiche, vedi caso ADR 0102.
2. **Modificare `resolve_from_step` per propagare l'intero
   observation a describe_entries**: scartato — describe_entries
   internal LLM non e' progettato per processare dict eterogenei
   (entries + health + altri campi); la sua sintesi degraderebbe.
   Meglio iniettare un blocco gia' formattato come direttiva.
3. **Output post-processing che rimuove il secondo blocco LLM**:
   scartato — fragile (pattern matching su stringa LLM-generata),
   contro §7.9 (preferire deterministico a monte).
4. **Disabilitare `_prepend_health_block_if_any` quando
   describe_entries gira**: scartato — il blocco deterministico e'
   piu' affidabile della sintesi LLM, va preservato.

Scelta finale: i tre livelli sono complementari e tutti deterministici
nei due backstop (Level 2/3); Level 1 educativo riduce le occasioni
in cui Level 2/3 sono necessari.
