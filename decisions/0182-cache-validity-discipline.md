# ADR 0182 — Disciplina di validità per le decisioni cachate (catalog-sig a due assi)

- **Stato**: accettato (Roberto 5/7/2026: «vorrei una soluzione definitiva»)
- **Contesto**: mandato Fable Area 1 (deviazione da CP2 concordata al cancello CP1); TODO CRUCIALE `negative-path-cache-invalidation` (25/6, riconfermato 3/7); evidenza live 5/7 (turni 2cd8862a/e130c549/68f28b01).

## Problema (misurato ✓)

Metnos caccia le DECISIONI di pianificazione su più layer, e nessuno traccia una dipendenza dallo **stato del mondo** in cui la decisione fu presa:

| Layer | Store | Persistenza | Invalidazione oggi |
|---|---|---|---|
| L0 fastpath | `fastpaths.sqlite` (0a hash esatto + 0b coseno) | disco | reaper C1 (tool sparito) + C2 (erede name-based). NIENTE su cambio-forma o nuova capacità |
| L1 autopath | `autopath.sqlite` | disco | come L0 |
| alternative-cache proposer | LRU in-process (`_candidate_cache`, key=sha256(query)+verb+object+lang) | processo | solo LRU-evict; serve framework fra turni vicini SENZA validità |
| default appresi | `args_defaults.sqlite` | disco | niente (5/7: filtro install-root su cattura+iniezione — CP1) |

Le entry sono snapshot «l'LLM disse di fare X con questi args», **valide per sempre** finché qualcuno non le purga a mano (fatto più volte). Due modi di marcire:
- **positivo-stantio**: un tool referenziato dal piano cambia forma/comportamento (digest diverso, stesso nome) → il piano gira su presupposti morti.
- **negativo-stantio**: il piano fu battuto quando una capacità NON esisteva; il fratello nuovo compare nel catalogo ma il piano cachato continua a essere servito (la famiglia era diversa quando fu deciso).

Il 5/7 la classe ha morso tre volte in un'ora: piani find_files/get_files serviti su una query da list_dirs (flip-flop dei layer + fratello a ordine-hash), args avvelenati iniettati su piani cache-hit.

## Decisione

**Ogni decisione cachata porta la FIRMA del mondo in cui fu presa; alla LETTURA, se il mondo è cambiato in un modo che può cambiare la decisione, l'entry è un MISS** (e viene rimossa; la ri-registrazione naturale del turno riuscito la rimpiazza con la firma fresca).

Due assi di firma per piano (`runtime/engine/cache_validity.py`):

1. **`tools_sig`** (anti positivo-stantio): sha256 dei `(nome, digest-manifest)` ORDINATI dei tool **referenziati dal piano**. Il digest è quello della firma §7.10 (copre codice+schema): un executor ri-firmato dopo un edit cambia la sig → il piano che lo usa è MISS. Tool sparito → sentinella `nome:!missing` → MISS (assorbe C1 a lettura).
2. **`pool_sig`** (anti negativo-stantio, il «gemello di `_compute_intent_sig`»): per ogni clausola `(verbo, oggetto)` dell'intent, la FAMIGLIA di candidati = nomi del catalogo `verbo_oggetto[_qualifier]` + fratelli produttori (`find/read/get/list_oggetto*` per i verbi produttori); sha256 dell'unione ordinata. Un fratello NUOVO (o rimosso) nella famiglia → la decisione va ripresa. Query-indipendente e deterministica (niente prefilter/affinity nella firma).

Applicazione per layer:
- **L0/L1**: colonne additive `tools_sig`/`pool_sig` (migrazione preserva-dati). Stamp alla registrazione; **verifica al hit** (0a E 0b) nel dispatch: mismatch → log + DELETE riga + MISS. Righe pre-migrazione (sig vuota) = MISS una volta (si ri-registrano fresche).
- **alternative-cache proposer**: la chiave LRU include `catalog_epoch` = sha256 di TUTTI i `(nome, digest)` del catalogo. In-process e a grana grossa: ogni cambio di catalogo azzera di fatto la cache (costo: un retry-LLM in più; beneficio: mai un framework di un mondo passato).
- **args_defaults**: fuori scope (valori scalari, non piani; filtro install-root da CP1). Eventuale TTL = follow-up.
- **guard**: NON serve invalidare sui cambi dei guard — girano su OGNI hit (ADR 0174) e sono idempotenti (CP1 T4): i fix dei guard si auto-applicano ai piani cachati.

Il **reaper** resta per l'igiene (pulizia batch delle righe con sig stantia — C3 accanto a C1/C2); la correttezza sta nella verifica a lettura.

## Costo accettato

Su cambio reale del mondo (executor editato/ri-firmato, capacità nuova nella famiglia) il primo turno della query ri-pianifica (cold ~15-20s sui compound) e ri-registra. È il costo CORRETTO: il memory 25/6 avvertiva che la purga cieca instabilizza il cold-start — qui la ri-pianificazione avviene SOLO quando il mondo è davvero cambiato, non a ogni purga di massa.

## Conseguenze

- `loader`: l'oggetto Executor espone `digest` (parse di `[code].digest` già letto per la verifica).
- Chiusi per costruzione: i due episodi del TODO CRUCIALE (negativo 25/6, positivo 3/7) + la falla alternative-cache (5/7).
- La sig NON copre: cambi di prompt del proposer, versioni dei guard (coperti da ADR 0174+CP1), lessici. Se un giorno un piano dipendesse da altro stato, aggiungere un asse, non allargare questi.
- Test: `tests/runtime/engine/test_cache_validity.py` (sig-funzioni + hit→miss per entrambi gli assi su store temporanei + epoch nel key proposer).
