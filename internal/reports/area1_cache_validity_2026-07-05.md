# Report AREA 1 · checkpoint «cache-validity» (ADR 0182) — 5/7/2026

**Riferimento**: mandato Fable Area 1 — deviazione da CP2 decisa da Roberto al cancello CP1 («alternativa-cache è migliore? vorrei una soluzione definitiva») · ADR 0182 · Branch `session/detection-lexicon-i18n` (non pushato) · Prod live.

## Esito: CONSOLIDATO ✓ — il TODO CRUCIALE `negative-path-cache-invalidation` è chiuso per costruzione

### La decisione (ADR 0182)
Ogni decisione cachata porta la **firma del mondo** in cui fu presa; alla **lettura**, mondo cambiato ⇒ MISS. Due assi per piano (`engine/cache_validity.py`):
- **`tools_sig`** — anti positivo-stantio: sha256 dei `(nome, digest-manifest §7.10)` dei tool referenziati. Executor ri-firmato dopo un edit ⇒ mismatch; sparito ⇒ sentinella `!missing` (+ check C1 esplicito in `validate`: tool assente = invalido a prescindere).
- **`pool_sig`** — anti negativo-stantio (il «gemello di `_compute_intent_sig`» del memory 25/6): famiglie di candidati per le clausole dell'intent (canonici + varianti + fratelli-produttori §2.2). Capacità nuova nella famiglia ⇒ la decisione va ripresa.

### Applicazione per layer ✓
| Layer | Meccanica |
|---|---|
| L0 fastpath | colonne additive (migrazione preserva-dati); stamp a `record_success(catalog=…)`; verifica al hit (0a E 0b) → mismatch = **morte + fall-through**; il successo ri-registra con firme fresche. Sussume la vecchia morte C1 hit-time. |
| L1 autopath | stamp sulle `observations` (il momento in cui il mondo è visto), copiate alla promozione; **la ri-promozione rinfresca le sig vuote**; al hit mismatch = fall-through **senza delete** (la promozione è capitale di feedback umano; potatura batch = reaper C3, follow-up). |
| alternative-cache proposer | `catalog_epoch` nella chiave LRU: mai un framework di un mondo passato dal retry-path. |
| args_defaults | fuori scope (scalari, non piani; filtro install-root da CP1). |

**Regola dura scoperta implementando**: le firme si calcolano SEMPRE col catalogo **del chiamante** (quello con cui il piano fu deciso e verrà validato). Il primo design aveva un fallback implicito a `load_catalog` → **side-effect nascosto** (timbrava `first_seen` sul DB aging — scovato dai test lifecycle). Rimosso: senza catalogo ⇒ sig vuote = miss-once onesto.

### Costo accettato (semantica «definitiva»)
Al PRIMO turno dopo un cambio reale del mondo la query ri-pianifica (cold ~16s sui compound) e ri-registra. Mai più: piani che sopravvivono a executor cambiati, capacità nuove ignorate dai piani vecchi, framework d'un processo passato dal retry, purghe a mano.

## Prove del cancello §A
- **Live end-to-end (prod)**: riga pre-migrazione fp_id=244 → `«INVALIDATO: sig assente» → morte + fall-through` → L3 ripiana (16.4s) → **re-record fp_id=247 CON firme** (`a9a1dd79`/`a725345c` su disco) → ripetizione = **hit VALIDO in 1.4s**. Esecuzione corretta entrambe le volte (list_dirs sul device + xlsx).
- **Test**: `test_cache_validity.py` **13** (assi, roundtrip, C1, L0 stamp/refresh/no-catalog, L1 promozione/refresh, epoch LRU) + lifecycle L0 adeguati al contratto-mondo (assertion INTATTE; il test send-a-vuoto passava per la ragione sbagliata, ora esercita il path inteso) → **80/80**; sweep aree toccate **471 verdi**; gate §2.8 4/4 + pipeline-contract; **bench compound 8/8 stabile** (2 run).
- **Commit** (7): `bd33183` ADR · `ec7a4c6` modulo+loader.digest · `f0cfa87` 3 layer · `86b7727` test · `3fedd24` CLAUDE §11 v22i + indice (+4 meccanismi) · [questo report + fix registry].
- Prod riavviata, migrazione colonne applicata ai DB reali senza perdita.

## ⚠ Follow-up dichiarati (non bloccanti)
- **Reaper C3**: potatura batch delle righe L0/L1 con firme stantie (la correttezza è già a lettura; C3 = igiene disco).
- **args_defaults TTL** (valori scalari appresi): valutare scadenza.
- Limite onesto documentato in ADR: i **builtin** (digest vuoto) cambiano solo col deploy+restart (che azzera la LRU in-process); un piano L0 che referenzia SOLO builtin non è invalidato da un cambio di comportamento del builtin.

## Prossimo checkpoint (mandato)
Torno alla scaletta: **CP2·M2 (T5) — Finalizer unico** (una sola fonte del messaggio finale; sana S5), salvo tua diversa priorità.
