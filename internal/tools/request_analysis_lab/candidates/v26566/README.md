# V26.5.6.6 — chiude B1-B4 della review V26.5.6.5, checkpoint OFFLINE

Status: **author checkpoint offline**, self-test **71/71 PASS**, `inference=false`.
Zero rete, zero chiamate modello, zero trasporto, zero gate creato o consumato,
nessun live. V26.5.6.5, V26.5.6.4, il registro, il validator, la fixture,
l'overlay e il gold **non sono stati toccati**: sono riusati invariati e
verificati per hash a ogni esecuzione.

Questo candidato non introduce un'idea nuova: tiene quella di V26.5.6.5 —
identità di clausola nella **struttura del documento** — e chiude i quattro
difetti che la review indipendente ha trovato in come era realizzata.

Replay:

```bash
/usr/bin/python3 -I -B \
  internal/tools/request_analysis_lab/candidates/v26566/metnos_v26566_offline_selftest.py
```

Deterministico (seme fisso `20260809`), circa 20 s, nessuna scrittura salvo
`--json`.

## I quattro blocker, e come sono chiusi

### B1 — l'espansore non era totale, e lo stadio 2 non era protetto

Causa: l'idioma `value.get(KEY, []) or []` neutralizza `None` e i falsi, **non**
uno scalare; cinque input di una riga facevano sollevare `expand_frame` e
`structural_fingerprint`, l'eccezione usciva da `evaluate` e il caso non veniva
registrato su **nessuno** dei tre stadi — cioè il guasto V26.5.6.4, nel ramo
che esiste apposta per i frame che lo schema respinge.

Chiusura: ogni lettura di contenitore passa da `_as_list`/`_as_dict`, quindi
uno scalare in qualunque posizione degrada a contenitore vuoto più un codice;
e **tutti e tre** gli stadi della condotta, più l'impronta, sono protetti in
modo simmetrico (`schema_exception:`, `expander_exception:`,
`validator_exception:`, `fingerprint_exception:`).

Misura: **2.343 sostituzioni di tipo** in ogni posizione di cinque frame base
(scalare, stringa, nullo, booleano, lista, oggetto, lista di scalari, oggetto
con chiave estranea, marcatore, oggetto marcato, lista marcata) più i nove
input scalari della review: **0 eccezioni**, e i nove input della review sono
misurati **9/9 su tre stadi**. 781 mutanti (un terzo del corpus) attraversano
anche la condotta completa.

### B2 — l'impronta era query-free solo sui frame schema-validi

Causa: su un frame schema-invalido l'impronta copiava **verbatim** `status`,
`relations`, `clause_roles`, `speech_acts`, `binding_kinds` e `proof_kinds`,
cioè stringhe controllate dal modello; il test di riservatezza non lo vedeva
perché ogni frame sondato era costruito schema-valido.

Chiusura: `structural_fingerprint` riceve il **vocabolario tecnico chiuso**
derivato dal registro congelato e filtra ogni stringa che emette. Ciò che non
appartiene al vocabolario non entra: viene **contato** in
`out_of_vocabulary_strings`, così la perdita è dichiarata invece che nascosta.

Misura: un marcatore piazzato in sei campi di un frame schema-invalido dà
**0 occorrenze** nell'impronta e 7 stringhe contate; il marcatore piantato in
**ogni** posizione di tutto il corpus di fuzz non raggiunge mai un'impronta.

### B3 — `typed_ambiguity` riapriva `clause_ids`, il codice che uccise V26.4.1

Causa: la forma normale congelata ha **una sola** lista globale di clausole
fuori registro e confronta il footprint di clausola di **ogni** alternativa.
V26.5.6.5 lasciava a ogni alternativa il proprio array di clausole, teneva le
fuori-registro della prima e scartava le altre: frame **schema-validi**
finivano su `clause_ids`, `alternative_coverage` e `clause_limit` per un
artefatto di proiezione, non per un errore semantico del modello.

Chiusura, strutturale: la richiesta non ripete più un array di clausole per
alternativa. Porta **un solo scheletro**, e ogni clausola proiettata porta una
`readings` — una lettura per alternativa:

```
{"status":"typed_ambiguity",
 "clauses":[ {"clause_start_segment_id":…, "clause_end_segment_id":…,
              "readings":[ {ruolo, prova, projection, dependencies},   <- alternativa 1
                           {ruolo, prova, projection, dependencies} ]} <- alternativa 2
           , {…, "reason":"out_of_registry"} ]}                        <- condivisa
```

Due alternative non possono più **dire cose diverse su quante clausole ci
sono, su quali span hanno, su quali sono fuori registro**: è irrappresentabile,
non vietato a parole. Il ruolo di clausola vive nella lettura, non nello
scheletro, perché il gold ha un caso in cui le due alternative differiscono
proprio nel ruolo e nell'atto linguistico.

Conseguenza verificata: `clause_ids`, `alternative_coverage`, `clause_limit` e
`alternative_order` **non sono più raggiungibili** da un frame schema-valido.
Le tre forme che la review usava per raggiungerli sono ora schema-invalide, e
lo scheletro condiviso con una clausola fuori registro **a ogni indice** è
valido con zero codici.

Residuo dichiarato: quando le `readings` di due clausole hanno lunghezze
diverse — cosa che lo schema non può vincolare — l'espansore usa il numero
massimo e ripete l'ultima lettura mancante, **dichiarandolo** con
`clauses_disagree_on_reading_count`. Niente sparisce in silenzio.

### B4 — il tetto agli atomi non era imposto, e lo stadio 3 censurava sé stesso

Causa, due metà. Lo schema di risposta ammetteva fino a 8 × 16 = 128 atomi
contro il limite congelato 16; e `validate_frame` **ritorna subito** dopo il
proprio schema interno, quindi un frame di 24 atomi otteneva
`validator: ["schema"]`, **zero** codici semantici, mentre il record
continuava a dichiarare tre stadi misurati.

Prima metà — perché il tetto **non** è stato messo nello schema. Il numero di
atomi è una **somma** su tutte le clausole, e JSON Schema 2020-12 non esprime
somme fra elementi di un array. L'unico tetto per-clausola che garantirebbe il
totale è: 8 clausole × (1 + D) ≤ 16, cioè **D ≤ 1**, una sola dipendenza per
clausola — che renderebbe **irrappresentabile** una clausola con due
produttori, che il validator congelato accetta senza problemi (3 atomi su 16).
Restringere così sarebbe di nuovo il guasto V26.5.6.4: un'analisi legittima
che non si può esprimere. Il tetto per-clausola resta quindi il più largo che
non rifiuta mai un frame che il validator accetterebbe.

Seconda metà — quello che si chiude davvero è la **censura**. L'espansore conta
gli atomi derivati e, se superano il budget congelato, lo dice con un codice
suo (`derived_atom_count_over_frozen_bound`) invece di lasciarlo arrivare come
un opaco `schema` del validator. E la condotta, quando riconosce che il
validator si è fermato al proprio schema, **rimisura la semantica** rieseguendo
le sue stesse funzioni congelate, a sezioni indipendenti: una sezione che non
può girare lo dichiara con `sweep_exception:<Tipo>` e le altre girano lo
stesso. Il record espone `validator_schema_censored`,
`validator_semantic_measured` e `validator_semantic_codes`.

Misura: il frame da 24 atomi è schema-valido, l'espansione nomina il budget, il
validator si censura, e la semantica **viene misurata comunque**
(`output_unconsumed`, `proof_limit`). Sui 781 mutanti del fuzz, le valutazioni
che finiscono senza misurare la semantica sono **0**. E la replica è
**fedele**, non più debole dell'originale: su **1.500** frame casuali
schema-validi non censurati, i codici dello sweep coincidono **esattamente**
con quelli di `validate_frame`.

Nota: l'ordinale `source_ordinal` ha ora anche il limite superiore derivato dal
registro (N1). È una stretta **sana**: un frame valido non può avere più di 16
atomi, quindi un ordinale oltre 16 era comunque irraggiungibile.

## Cosa dicono i numeri, e cosa non dicono

| Gruppo | Esito |
|---|---|
| materialisation | 4/4 — schema e prompt uguali al generatore, registro pinnato; **niente più condizionato all'esistenza dei file** (N5) |
| representability | 2/2 — **34/34** casi gold rappresentabili e validi, **34/34** con esattamente la semantica gold |
| regression_of_the_v26564_failures | 4/4 — span collisi e dipendenza con span stretto restano accettati |
| no_fatal_invariant_outside_the_schema | 4/4 — doppia proiezione, dipendenza orfana, clausola senza proiezione e ordinale fuori bound sono schema-invalidi |
| expander_totality | 5/5 — 2.343 sostituzioni, 0 eccezioni, 0 marcatori nell'impronta, 0 censure silenziose |
| best_effort_diagnosis | 3/3 |
| atom_budget | 7/7 — budget nominato, censura riconosciuta, semantica misurata comunque |
| typed_ambiguity_identity | 5/5 — fuori registro valido a ogni indice, forme divergenti schema-invalide |
| coverage_shapes | 6/6 — multi-clausola, multi-dominio, misto, unsupported, arco fra clausole |
| no_emitted_identity | 5/5 — nessuna etichetta numerica richiesta (N6: disgiunto morto rimosso) |
| unicode_metamorphic | 3/3 — 9 scritture, una sola impronta |
| contamination | 3/3 — prompt **e** schema, 3-gram **e** 4-gram, zero condivisioni |
| fingerprint_privacy | 6/6 — valido e schema-invalido, marcatore, conteggio, struttura conservata |
| fail_closed | 3/3 |
| mutation_suite | 6/6 — 12/12 mutazioni respinte + **2.064 mutazioni semantiche sistematiche** giudicate dall'**oracolo semantico** (N4): 100 restano strutturalmente valide, **nessuna** con la semantica gold |
| structural_closure | 5/5 — 1.500 frame casuali schema-validi, 0 codici strutturalmente chiusi, sweep fedele 1.500/1.500 |

**L'insieme fatale reale, misurato e non dichiarato** (N3). Sui 1.500 frame
casuali schema-validi solo 38 sono validi: il generatore è ostile di proposito,
e il punto non è il tasso di validità ma **quali** codici si accendono. I più
frequenti sono semantici e appartengono al modello — `proof_family` 1.460,
`reference_type` 1.070, `role_speech` 980, `dependency_order` 839. Fra le
regole di sola prosa del validator congelato resta accesa
`clause_source_order` (507): «emetti le clausole in ordine di sorgente» vive
nel prompt, non nello schema. Non è un difetto introdotto qui — è una regola
preesistente del validator congelato — ma **non va dichiarata chiusa**:
l'insieme fatale coincide con lo schema per gli **undici** codici elencati in
`STRUCTURALLY_CLOSED_CODES`, non in generale.

## Limiti dichiarati

- Il registro resta **Phase-1 con 10 relazioni**: questo candidato **non
  certifica i 109** della suite generale, e nessun risultato qui vale come
  accuratezza semantica del modello.
- Nessun live: `inference=false`, zero chiamate modello, zero rete, nessun gate
  creato o consumato. Un eventuale live richiede **un gate nuovo**: quello di
  V26.5.6.4 esigeva `output_must_be_absent` ed è esaurito.
- 71/71 offline dimostra che il contratto è rappresentabile, che l'insieme
  fatale coincide con lo schema per i codici elencati, che la diagnosi non
  censura e che l'impronta non trasporta materiale di richiesta. Non dimostra
  che il modello analizzi bene una frase.
- L'impronta conta gli atomi **dichiarati**: in un frame ambiguo somma le
  letture di tutte le alternative, non gli atomi di una sola.
- Il ponte `DERIVED_REFERENCE_BRIDGE` del self-test collega due **registri
  tecnici congelati** per la sola costruzione della fixture: una voce, nessun
  token di lingua sorgente, mai usato a runtime.

## Prossimo passo

**Review indipendente offline sui byte congelati.** Nessun trasporto è
autorizzabile e nessun gate va progettato prima di un PASS.

## Hash

| Artifact | SHA-256 |
|---|---|
| `metnos_v26566_registry_projection.py` | `7b1af2e41fba32d2129327f0423d10ad19135d17569dba54f8a01f445ef10f7d` |
| `metnos_v26566_expander.py` | `4bcbc3c3563ee51e6ce5f038f8b3f13f3eb3b1ac16261fcc640011774f672701` |
| `metnos_v26566_offline_pipeline.py` | `9d1a2be5c16d7ae634a2a1d4b1acd38d757a0a1973412bb771667bea1dd8ea42` |
| `metnos_v26566_offline_selftest.py` | `23ca6c1e08473c1acb9744af8a2f3ba6237d6db6333b747782d665b7d9f0b17d` |
| `metnos_v26566_clause_owned.schema.json` | `72b2739d676d0c35c21d8a9add383a58f90b30d291c499532f680d5e902ce0b9` |
| `metnos_v26566_clause_owned.prompt.txt` | `803721b9bff01159de0ce163aa03161c3128eccfff232b97faea7d812c7731db` |
| `metnos_v26566_contamination_audit.json` | `5a179d1e64ae132fa2c7a4272247717921d92e51949ea3d83f3d8528d9999a1d` |
| `metnos_v26566_author_selftest_result.json` | `b41989edfb2ef37722b6affdd730841da625fb67d37031b9c24ba13cb590a9e9` |
| `metnos_v26566_author_pre_gate.json` | `cff40b4afe2f238804c3be2aaa38ea00496c68ca7390969a859e4458e4f98cb9` |
| `metnos_v26566_author.freeze.json` | `a2d88bc18eddf4e766b42a2c5cba41e1fe2e3d37a5038ae89f4eb3eb4c1f1f3e` |

Dipendenze congelate riusate invariate: registro tipizzato V26.4.1, validator
iniettato V26.5.3, fixture Phase-1, controlli runtime 34, overlay dell'oracolo,
albero delle dipendenze Python. Il README non fa parte della tabella di freeze.
