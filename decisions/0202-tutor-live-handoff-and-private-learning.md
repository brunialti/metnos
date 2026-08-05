# ADR 0202 — Tutor: osservazioni live, handoff e apprendimento privato

**Stato:** accepted  
**Data:** 2026-07-28  
**Roadmap:** RM-0003, fasi F3 e F4

## Decisione

F3 e F4 restano fuori dal planner e dalle sue cache. Il Tutor interviene
soltanto dopo che il gate semantico ha classificato la richiesta come
`EXPLAIN` o `MIXED`.

F3 ammette esclusivamente probe registrati in codice, in sola lettura. Una
fonte firmata può dichiarare `probe_refs`; il modello non sceglie il probe e
non ne costruisce gli argomenti. Ogni esecuzione produce una capsula con
schema chiuso, audience, istante di osservazione, scadenza, stato esplicito e
fatti sanitizzati. Cache e binding sono isolati per utente. I primi probe
coprono catalogo degli executor ammessi, salute dei servizi, dispositivi
posseduti e task con cronologia limitata all'attore.

Una richiesta `MIXED` è scomponibile soltanto quando il decompositore
canonico produce esattamente una clausola `EXPLAIN` e una clausola `ACT`.
La risposta spiega la prima e offre di consegnare la seconda al motore. La
clausola d'azione è il segmento letterale dell'utente: non è riscritta dal
Tutor né generata dal compositore. Il pending esistente conserva owner,
conversazione, hash del catalogo, hash della clausola, nonce, TTL e scelta
`continua|annulla`. Il callback è reclamabile una sola volta; su scadenza,
variazione del catalogo, mismatch o replay non esegue nulla. Dopo la conferma
chiama il normale `run_turn`, quindi planner, vaglio, autonomia, consensi ed
executor restano invariati. Un pending preesistente non viene sostituito.

F4 usa un ledger SQLite locale in modalità 0600. Non conserva la domanda in
chiaro. Conserva soltanto hash normalizzato, vettore quando necessario,
lingua, audience, causa chiusa, fonti, versioni e scadenza. Le prove di un
turno riuscito vivono per il tempo breve necessario a ricevere il feedback e
sono eliminate dopo il verdetto. Tutte le chiavi sono separate per utente
mediante un identificatore derivato; quote, TTL e pruning sono obbligatori.

Un feedback positivo può promuovere l'associazione fra il vettore della
domanda e la fonte primaria realmente servita. L'associazione è valida solo
per lo stesso utente, spazio di embedding e hash della fonte firmata. Un
feedback negativo la rimuove e registra un gap. Le associazioni operano nel
retrieval Tutor dopo il gate di modo; non entrano nel planner, non cambiano
una richiesta `ACT` e non contengono frasi o sinonimi codificati a mano.

La mappa del debito aggrega deterministicamente i gap per causa e vicinanza
coseno. Il replay controfattuale confronta il rango della fonte prima e dopo
l'associazione, rifiuta fonti orfane o cambiate e mantiene come gate separato
il corpus delle richieste operative, che deve continuare a cadere fuori dal
Tutor. F4 non genera né pubblica autonomamente documentazione e non introduce
un change-intent privo di un contenuto reale da proporre.

## Conseguenze

- Lo stato live arricchisce soltanto la generazione finale; non riduce
  riproducibilità o hit-rate delle cache L0/L1.
- Nessun fatto osservato o appreso amplia l'autorità dell'utente.
- Il costo delle sonde è limitato dallo scheduler centrale, da timeout, TTL e
  cache per-principal; `partial`, `stale` e `unavailable` restano visibili.
- Il Tutor può diventare più utile per una persona senza contaminare il
  comportamento di un'altra.
- L'evidenza resta la fonte firmata o il dato runtime tipizzato; la prosa del
  compositore non viene mai riusata come prova.

## Prove richieste per chiudere RM-0003

1. test di schema, audience, ownership, cache, timeout e stati delle capsule;
2. test di isolamento, scadenza, replay e immutabilità dell'handoff;
3. test di separazione per utente, TTL, cap, feedback e invalidazione fonte;
4. replay controfattuale e corpus anti-sottrazione al planner;
5. turno live dopo deploy, catalogo Tutor ricompilato e salute dei servizi.
