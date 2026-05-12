---
id: 0127
title: Qualifier `_empty` per executor che ritornano lo stato vuoto/sotto-soglia del dominio
date: 2026-05-12
status: accepted
area: vocab | executors | runtime
related:
  - 0045  # naming convention closed vocabulary
  - 0090  # get_inputs UI dichiarativo
  - 0123  # skill importer agentskills.io (read_events / set_events imported)
complements:
  - 0045  # estende la famiglia "modalita' di operazione" con un quinto modifier
---

## Context

Il pattern **propose-and-fire** (proporre N opzioni → l'utente sceglie →
eseguire l'azione mutating sulla scelta) e' emerso 11-12/5/2026 come
caso d'uso ricorrente:

- «proponi 3 mattine la prossima settimana e prenotami quella che scelgo»
- «trova le cartelle vuote sotto /opt e cancellane una»
- «mostra i file piccoli (<10KB) da archiviare e cancella quello che scelgo»

Una query di questo tipo richiede 4 step:
1. Computazione delle **opzioni candidate** (slot liberi del calendario,
   file sotto-soglia, cartelle vuote, messaggi corti, ecc.).
2. `get_inputs(kind="choice", ...)` per far scegliere all'utente.
3. Esecuzione del verbo mutating (`set_events`, `delete_files`, ...)
   sulla scelta selezionata.
4. `final_answer` di conferma.

**Problema di vocabolario**: lo step 1 non e' coperto dai verbi/qualifier
esistenti. Esempi cross-dominio:
- `read_events(time_window="next-week")` ritorna **gli eventi** della
  settimana (record gia' presenti), NON gli **slot liberi** (gap fra eventi).
- `find_files(...)` ritorna file matchanti un pattern, NON quelli
  **sotto una soglia di size** (file vuoti/piccoli da pulire).
- `find_dirs(...)` ritorna dir matchanti, NON le **dir vuote**
  (candidati ad archiviazione).
- `read_messages(...)` ritorna messaggi, NON i **messaggi corti**
  (body sotto-soglia) candidati a cleanup.

Computare "vuoto / sotto-soglia" dato un corpus e' un'operazione derivata,
deterministica, ortogonale al `read`/`find`/`get` puro. **La stessa
semantica si applica trasversalmente a 4+ dominii** — non e' una
peculiarita' del calendario.

Servono dunque executor che esprimano la modalita' "ritorna le entita'
in stato **vuoto/disponibile/sotto-soglia** del dominio". Il PLANNER
deve trovarli come `find_<obj>_<modalita'>` (asse produttore `find` +
obj + qualifier che marca la modalita'), in linea con la convenzione §2.2.

## Decision

Aggiungere il qualifier **`_empty`** alla famiglia "modalita' di
operazione" (§2.2, accanto a `_size`, `_format`, `_similar`, `_loc`).

**Semantica**: il qualifier `_empty` modifica un verbo-produttore (tipico
`find`) per fargli ritornare le entita' del dominio in **stato vuoto /
sotto-soglia / disponibile** — quelle che NON sono "piene" rispetto a una
soglia parametrica `size` (unit-aware). Output e' sempre `entries` (§2.6) —
il "vuoto" e' un record di prima classe.

**Arg canonical** condiviso dalle implementazioni cross-domain:
- `size` (str unit-aware): la soglia che discrimina "vuoto" da "pieno".
  Esempi: `"1hour"`, `"10KB"`, `"100chars"`, `"60min"`. Bare number =
  unita' di default per il dominio (events ⇒ minuti, files ⇒ bytes,
  messages ⇒ chars). Helper `_parse_size_to_minutes`/`_parse_size_to_bytes`
  per-dominio condividono il parsing regex `^\d+(?:\.\d+)?\s*[a-zA-Z]*$`.

**Pattern canonici cross-domain** (lo stesso qualifier, una sola
semantica, generalizzabile §7.3):
- `find_events_empty(time_windows, size="1hour", time_of_day=...)` →
  slot >=1h liberi (gap nella timeline del calendario).
- `find_files_empty(base_path, size="10KB")` → file <=10KB
  (candidati a cleanup).
- `find_dirs_empty(base_path)` → directory vuote (zero file dentro).
- `find_messages_empty(time_window, size="100chars")` → messaggi con
  body <=100 char (candidati a cleanup).

**Composizione propose-and-fire** (workflow universale):
```
get_now → find_<obj>_empty(...) → get_inputs(kind=choice, from_step=N,
display_template="...", value_field="...") → <verb_mutating>(...)
```

Il qualifier e' **read-only-by-construction** (computazione pura sul
corpus esistente, niente side-effect). Reversible irrilevante (no undo
patterns).

## Alternatives considered

**(a)** Qualifier `_free` (proposta originale, 12/5 mattina, ora superseded).
   - **Contro**: lessicalmente associato a "disponibile" piu' che a "vuoto".
     Su files/messages/dirs «free» suona innaturale («free files»? «free
     directories»?). `_empty` ha resa universale: «empty slot» = slot
     libero, «empty file» = file vuoto/sotto-soglia, «empty dir» = dir
     vuota. Stessa parola IT+EN nello stesso senso. Generalizzazione
     legittima cross-dominio senza analogie forzate.

**(b)** Nuovo qualifier `_slots` (specifico timeline).
   - **Contro**: limita la generalizzazione cross-dominio. "Slot" e'
     tipicamente temporale (calendar), ma "vuoto/sotto-soglia" si applica
     anche a filesystem, messaggi, dir. Avere `_slots` per calendar e
     altri qualifier per fs/messages frammenterebbe il vocabolario.

**(c)** Nuovo OBJECT `slots` separato.
   - **Contro**: viola la convenzione "OBJECTS = entita' del dominio
     primario" (events, files, messages, ...). "Slot" non e' un'entita'
     persistente: e' un'astrazione computata. Aggiungerlo come OBJECT
     spinge verso una proliferazione di oggetti astratti (slots, gaps,
     residuals, frees, ...).

**(d)** Qualifier piu' lungo `_availability` o `_disponibilita'`.
   - **Contro**: 13/14 caratteri vs 5 caratteri di `_empty`. Manifest
     description gia' verbose; nomi executor compositivi piu' lunghi
     leggibili meno bene da LLM medium (§2.5).

**(e)** Sinonimo `_gaps`.
   - **Contro**: `gaps` e' tecnicamente preciso ma terminologico
     (engineering jargon §7.8). `empty` e' termine universale IT+EN,
     comprensibile sia per calendario («slot vuoti», «empty slots») sia
     per filesystem («file vuoti», «empty files»), sia per dir («cartelle
     vuote», «empty directories»), sia per messages («messaggi corti» /
     «short messages»). Mappabile su espressioni utente comuni.

**(f)** Trattare il caso senza modificare il vocabolario, lasciando che
   il PLANNER computi gli slot inline dal `read_events` + final_answer
   testuale.
   - **Contro**: l'output `read_events` non e' lista di slot vuoti, e'
     lista di slot OCCUPATI. Il PLANNER (LLM medium) dovrebbe trasformare
     la timeline mentalmente: errore frequente in pratica. Inoltre la
     scelta dell'utente fra opzioni proposte richiede un identificativo
     stabile (`value_field` deterministico) — meglio entries esplicite
     con campi calcolati una volta da codice deterministico. Vale per
     calendar, ma anche per files (sotto-soglia size deterministica) e
     dirs (count file deterministico).

## Consequences

- **Vocabolario cresce di 1 termine** (`_empty` aggiunto in
  `runtime/vocab.py` alla famiglia QUALIFIERS modalita'-operazione).
- **Primo executor concreto**: `find_events_empty` importato sotto
  `~/.local/share/metnos/executors/_imports/google-workspace/` (specializ-
  zazione del binding calendar Google). NON handcrafted in `/opt/myclaw/
  executors/`: il binding al provider (Google Calendar API via skill
  importer ADR 0123) e' specifico, e una eventuale futura versione iCloud
  Calendar avrebbe il proprio `find_events_empty` sotto altro skill.
- **Generalizzabilita' cross-domain** (§7.3): lo stesso qualifier
  funziona per `find_files_empty`, `find_dirs_empty`,
  `find_messages_empty`, `find_quota_empty`, ecc. senza tornare a
  discutere il vocabolario. Una semantica, N implementazioni per-dominio.
  L'arg canonical `size` (str unit-aware) e' condiviso: il parser
  per-dominio (`_parse_size_to_minutes` per events,
  `_parse_size_to_bytes` per files, ecc.) e' un helper deterministico
  §7.9 di poche righe.
- **Pipeline propose-and-fire universale** (PLANNER) diventa esprimibile
  con executor di prima classe + `get_inputs` (kind=choice) generalizzato
  con `from_step` + `display_template` + `value_field`. Vedi planner
  section `calendar.j2` hint `(propose_and_fire)` e `_core.j2` hint
  «pattern propose-and-fire cross-domain».
- **Intent extractor** mappa le query con marker propose+continuazione a
  `{verb: "find", object: <obj>}` (primo step della pipeline e'
  `find_<obj>_empty`); il PLANNER discrimina con la regola della
  continuazione mutating se la query e' propose-only (final_answer
  testuale come da §10.6.41 P5) o propose-and-fire (pipeline 4 step).
- **Compatibilita'**: nessuna rottura. Qualifier additivo. Tutti i 22
  verbi continuano a operare come prima senza il qualifier.

## Implementazione

- `runtime/vocab.py::QUALIFIERS` aggiunge `"empty"` (sostituisce il
  `"free"` originale rimasto solo nelle sessioni 12/5 mattina,
  superseded prima del rollout).
- `executors/_imports/google-workspace/find_events_empty/` (executor +
  manifest TOML, Ed25519 signed §7.10). Args refactor: `time_windows`
  (lista §2.1, default `["next-week"]`) sostituisce `time_window`
  scalare; `size` (str unit-aware, default `"1hour"`) sostituisce
  `duration_min` (integer, minuti soltanto). Helper interno
  `_parse_size_to_minutes` (parser regex deterministico) accetta
  "1hour"/"60min"/"30 min"/"2 hours"/"90"/"0" e variazioni
  case-insensitive.
- `executors/get_inputs/get_inputs.py` estende `kind="choice"` con
  derivazione opzioni da `from_step` + `display_template` + `value_field`
  (invariato rispetto al rollout originale).
- `prompts/{it,en}/planner/sections/calendar.j2` aggiorna hint
  `(propose_and_fire)` con `find_events_empty` + `size=` + `time_windows=`
  OK/ERRORE §6.
- `prompts/{it,en}/planner/_core.j2` aggiunge hint generale «pattern
  propose-and-fire cross-domain» per inquadrare il qualifier `_empty` al
  di la' del solo calendario.
- `prompts/{it,en}/intent_extractor.j2` esempi propose-and-fire
  invariati (mappano a `{verb: "find", object: "events"}`).
- `prompts/{it,en}/synt_naming.j2`/`synt_signature.j2` aggiornati con
  paragrafo `_empty` qualifier + 3 esempi cross-domain (events/files/
  dirs).
- Test: `runtime/tests/test_find_events_empty.py` (10+ test inclusi
  parser `size`), `runtime/tests/test_propose_and_fire_pipeline.py`
  (aggiornati i riferimenti a `find_events_empty`), nuovo blocco
  `_parse_size_to_minutes` 8 case (cf. workflow operativo).
