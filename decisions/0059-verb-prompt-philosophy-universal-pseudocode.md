---
id: 0059
title: Filosofia dei prompt verbo-specifici (definizione + invarianti + dominio)
date: 2026-04-29
status: accepted
area: synt, stage5
related:
  - 0057  # synt-stage5-modular-verb-prompts
  - 0058  # intent extractor
modifies:
  - 0057  # specifica la struttura interna degli addendum verbo-specifici
---

## Context

ADR 0057 introdusse VERB_PROMPTS per specializzare il prompt di stage 5 per
verbo. La prima implementazione produsse addendum di 9-12K char ciascuno con
contenuto eterogeneo: principi, esempi, pseudocodice, schema. Roberto 29/4
sera dopo 4-5 cicli di iterazione critico'  (citazione):

> "ma cosi' praticamente hai scritto tu la funzione, non dato regole generali
>  per un move universale. o sbaglio?"

E poi:

> "filosofia della coppia verbo/prompt specializzato: diminuire la complessita'
>  del prompt dando solo le regole specifiche per quella classe di azioni. Nel
>  caso move si deve dare un template generico del move e tutte le restrizioni
>  a cui un 'move' e' soggetto. Se proprio necessario si possono aggiungere
>  sezioni per move di file, di mail e/o altri oggetti che si pensa possano
>  usare un executor di tipo move"

E (dopo refactor "rules-only"):

> "va bene pseudo codice simile a codice ma deve essere una astrazione
>  accettabile per tutti i 'mover'. Per esempio check su esistenza
>  destinazione ok, creazione destinazione (se non esiste) ok. Loop su
>  tutti elementi passati nella lista di input ok. Copy per ciascun elemento ok"

Tre tensioni emerse:
1. Pseudocodice troppo concreto = LLM transcribe (prompt = funzione).
2. Solo regole astratte = LLM perde l'aderenza (omette M.select, sbaglia API).
3. Domini specifici (FS/IMAP) hanno API molto diverse — astrarre via.

## Decision

Struttura uniforme per ogni addendum verbo-specifico:

  **1. DEFINIZIONE** (1-2 righe)
  Cosa significa il verbo come azione astratta. Es.:
  `move = copy(src→dst) + remove(src). Distruttivo: la sorgente sparisce.`

  **2. PSEUDOCODICE UNIVERSALE** (astratto, ~20-30 righe)
  Template del flusso usando OPERAZIONI ASTRATTE come funzioni
  (`dst_exists`, `create_dst`, `copy_one`, `verify_copy`, `remove_src`,
  `identity_stable_id`). Il LLM lo usa come scheletro mentale per il loop.
  NON e' codice da copiare letteralmente: indica COSA succede in che ordine.

  **3. INVARIANTI** (5-10 punti DEVI/NON DEVI)
  Regole valide per QUALUNQUE oggetto venga mosso/cancellato/creato/etc.
  Es.: COPY-CHECK-DELETE order, identita' per-entry, schema results,
  no `def reverse(...)`, ecc.

  **4. DOMINIO X** (sezione per ogni oggetto rilevante)
  API concrete e quirks per quel dominio (FS / IMAP / KV / archivi / ...).
  Schema arg, schema results, AVVERTENZE specifiche del protocollo.
  Esempio per IMAP: stato AUTH→SELECTED, return shape data[0], _q quoting.

  **5. HELPERS** (pseudocodice CONCRETO solo dove indispensabile)
  Funzioni piccole e riutilizzabili che il LLM tipicamente sbaglia se lasciate
  astratte. Es. `_q(name)` per quoting IMAP, `_resolve_dst_folder(M, name)`
  per matching folder. Questi SI vengono copiati letteralmente.

## Test

Validato su tutti 13 verbi specializzati (move/delete/send/write/extract/
read/create/find/list/get/filter/fetch/describe). Format `format()` clean
su tutti i 22 prompts (incluso `_default`).

E2e cycle convergence: move ha richiesto 8 cicli per convergenza (varie
ondate di rifinitura: pseudocode, helpers, IMAP API quirks, undo schema),
poi gli altri verbi hanno seguito la struttura senza ulteriori cicli.

## Consequences

### Positive
- Bilanciamento right: regole + helpers concreti dove serve.
- Cross-verb consistency: ogni addendum ha la stessa struttura → diff
  minimo, audit veloce.
- Aggiungere un dominio nuovo (es. KV per write, archivi per extract) =
  aggiungere una sezione in coda all'addendum, niente refactor.

### Negative
- Sezioni DOMAIN tendono a crescere quando il dominio ha quirks (IMAP move
  ha ~80 righe di sezione, 4× le altre).
- Tensione tra "scrivere io la funzione" vs "lasciare al LLM": ogni nuovo
  bug sintetizzato e' un candidato per un nuovo helper concreto. La linea
  di mediazione e' empirica.

### Mitigazioni
- Quando un bug si presenta su MULTIPLI verbi, promuovere la regola al
  GENERIC (fatto per `from_step` contract, return shape, ecc.).
- Quando un quirk e' UNICO per un dominio (es. IMAP _q quoting), restano
  in DOMINIO duplicato fra verbi (move, delete, read, list, create) — Roberto
  preferisce duplicazione locale a coupling globale.

## Status

`accepted`. 13 prompt rifattorizzati 29/4/2026 sera. Synth e2e
convergente per move. Test 100/100 sull'intent extractor (ADR 0058).
