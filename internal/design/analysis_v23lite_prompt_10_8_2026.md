# V23lite: shadow sulle query vere e analisi del prompt

Data: 10 agosto 2026. Tutto misurato sul server locale, corpus reale, nessuna
scrittura in produzione, nessun gold toccato, nessun gate della linea V26
creato o consumato.

## 1. Shadow di V23lite + V24.1, budget 1.200

Corpus: `~/.local/share/metnos/turns/*.jsonl`, campo `user_query`, deduplicato,
esclusi comandi slash e percorsi incollati → **754 query uniche**. Campione
deterministico seme `20260810`, **40 query**.

| | |
|---|---:|
| catena completa (estrazione + proiezione V24.1) | **36/40 = 90%** |
| latenza p50 / p95 / max | **3.966 / 13.783 / 13.985 ms** |
| token ingresso p50 / max | 5.502 / 6.571 |
| token uscita p50 / max | 283 / 1.200 |

Per tipo di richiesta:

| | completate | p50 | max |
|---|---:|---:|---:|
| clausola singola | **32/32** | 3.883 ms | 8.761 ms |
| multi-clausola | **4/8** | 10.571 ms | 13.985 ms |

**Dove fallisce, e perché conta.** I quattro fallimenti sono **tutti
troncamenti**: uscita esattamente a 1.200 token, JSON tagliato a metà. Non sono
errori di analisi del modello — sono il tetto che abbiamo abbassato. Tutte e
quattro le query sono multi-clausola («crea un task ricorrente che ogni 30
minuti legge le nuove issue…», «accedi a telepass.com e trova tutte le
fatture…», «nella cartella Documenti/Progetto Atlas, trova PDF…»). A budget
2.600 due di esse passavano.

Quindi il taglio del budget **non è gratis**: compra il 59% della coda p95 e
paga il 50% delle multi-clausola. Su un corpus reale dove le multi-clausola
sono ~20% del traffico, è un compromesso da scegliere, non da subire.

## 2. Il prompt: composizione misurata

Prompt montato per `v23lite`: **23.898 caratteri, ~5.900 token**, undici
blocchi.

| componente | caratteri | quota |
|---|---:|---:|
| ontologia dei 27 verbi (`render_boundaries`) | 7.413 | **31%** |
| grafo argomenti taggato (lite) | 2.910 | 12% |
| raffinamenti di ontologia | 2.253 | 9% |
| compound | 1.912 | 8% |
| istruzione base | 1.806 | 8% |
| compound stretto | 1.674 | 7% |
| tie-break | 1.151 | 5% |
| lemmi | 1.117 | 5% |
| struttura | 938 | 4% |
| ancore e sink | 914 | 4% |
| contratto oggetti | 847 | 4% |
| scope | 537 | 2% |
| glosse | 445 | 2% |

**(a) Ridondanza: totale.** Undici concetti chiave su undici ricompaiono in più
di un blocco. Cinque di essi in **cinque blocchi diversi**: `get/processes` vs
`find/processes`; `images` vs `files`; l'ancora come token e non come ordinale;
`persons` come registro di identità; `get/files` per allegati e metadati foto.

**(b) Regole già imposte dallo schema.** Lo schema di risposta ha già enum
chiusi per `role`, `semantic_action` (27), `patient_object` (28),
`resource_scope`, `verb`, `verb_resolution`, `object`, `object_qualifier`, e
unioni taggate per `carrier` e `sink`. Tutta la prosa che elenca valori ammessi
o spiega «scegli una variante ed emetti solo i suoi campi» è ripetizione di ciò
che la grammatica già impone.

**(c) Difetto di igiene.** L'ontologia inglese contiene **sei parole italiane**
(`Cambia`, `esistente`, `Reversibile`, `Distruzione`, `irreversibile`) in un
prompt che deve essere indipendente dalla lingua.

## 3. La riduzione, provata sul vivo

Riscrittura dei dieci blocchi di raffinamento in uno solo, metodo dichiarato:
ogni regola ripetuta detta **una volta sola** nella formulazione più precisa;
via ciò che lo schema già impone; imperativo, una riga per regola, niente prosa
di collegamento, niente esempi letterali.

| | prima | dopo |
|---|---:|---:|
| blocchi di raffinamento | 13.851 char | **4.102 char (−70%)** |
| prompt intero | 23.888 char | **14.139 char (−41%)** |
| token ingresso misurati | 5.502 | **3.528 (−36%)** |

**Ma il risultato contraddice l'aspettativa, e va detto:**

| | prompt intero | prompt ridotto |
|---|---:|---:|
| catena completa | 36/40 | **33/40** |
| latenza p50 | 3.966 ms | **5.117 ms** |
| token uscita p50 | 283 | 291 |

Due regressioni nuove e attribuibili: `predicate_1_incomplete_request` su una
query elementare, `predicate_3_anchor` su una multi-clausola. Entrambe cadono
su regole che nel prompt originale erano ripetute cinque volte e che io ho
detto una volta.

**Conclusione contro-intuitiva ma misurata: su questo modello la ripetizione
non è ridondanza, è rinforzo.** Tagliarla costa ~7 punti di accuratezza.

## 4. Perché ridurre il prompt non può risolvere la latenza

Il conto lo chiude un confronto fra due misure della stessa giornata:

| | token ingresso | token uscita | latenza |
|---|---:|---:|---:|
| estrattore attuale | ~4.700 | 13-39 | **503 ms** |
| V23lite | 5.502 | 283 | **3.966 ms** |

A parità sostanziale di ingresso, 258 token di uscita in più costano 3.466 ms:
**~13,4 ms per token generato**. L'ingresso è quasi gratis — il server riusa il
prefisso comune fra chiamate — mentre **la generazione è tutto il costo**. Ecco
perché il prompt ridotto del 41% non ha guadagnato un millisecondo.

**Quindi la leva giusta non è il prompt: è l'uscita.** E lì c'è margine vero,
perché il frame paga soprattutto i **nomi dei campi**, ripetuti per ogni record:

| campo | quota del frame |
|---|---:|
| `resource_scope` | 6,6% |
| `predicate_anchor_token_id` (in `semantic_heads`) | 5,5% |
| `predicate_anchor_token_id` (di nuovo in `predicates`) | 5,5% |
| `semantic_gloss_en` | 5,1% |
| `input_from_predicate_id` | 5,1% |
| `verb_resolution` | 4,9% |
| `object_qualifier` | 4,7% |

## 5. Tagli proposti, con guadagno e rischio

A 13,4 ms per token, ogni 100 token di uscita risparmiati valgono **1,34 s**.

| # | taglio | guadagno stimato | rischio |
|---|---|---:|---|
| 1 | `predicate_id` derivato dalla posizione (lezione V26.5.6.5) | ~25 tok, 0,3 s | **basso**: è già ordinale per contratto |
| 2 | ancora emessa una volta sola, non in entrambi i blocchi | ~30 tok, 0,4 s | **basso**: è la stessa informazione due volte |
| 3 | nomi di campo brevi (`predicate_anchor_token_id`→`anchor`) | ~60 tok, 0,8 s | **basso**: denotazioni tecniche, non lingua |
| 4 | `verb_resolution` e `object_qualifier` omessi quando valgono il default | ~35 tok, 0,5 s | medio: vanno resi opzionali nello schema |
| 5 | `semantic_gloss_en` rimosso se il lemma basta | ~25 tok, 0,3 s | **alto**: è il pivot che disambigua i falsi amici |
| 6 | prompt ridotto del 41% | **0 s** | **alto**: −7 punti misurati |

Somma dei tagli 1-4, tutti a rischio basso o medio: **~150 token, ~2,0 s**, che
porterebbe V23lite da 3,97 s a **circa 2,0 s**. Non ai 503 ms dell'estrattore
attuale — quella distanza è strutturale, perché produce un'analisi e non un
indizio — ma dimezzata.

Il taglio 6 va **scartato**: non guadagna latenza e costa accuratezza. Il
prompt lungo resta, e questa è la sorpresa utile della giornata.

## Cosa resta aperto

- La riscrittura §6 andrebbe riprovata **conservando la ripetizione dei cinque
  concetti rinforzati**, per separare l'effetto «meno parole» dall'effetto
  «meno ripetizioni». Non l'ho fatto: sarebbe una terza esecuzione.
- Il budget ottimale non è 1.200 né 2.600: le multi-clausola vogliono più
  spazio, le singole no. Un budget **per lunghezza di query** costerebbe nulla
  e recupererebbe i quattro troncamenti.
- V24.1 resta `prototype_frozen_not_production_reviewed`.
