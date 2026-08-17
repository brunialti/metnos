# Revisione avversariale indipendente — ciclo 4

Data: 2026-08-12  
Revisore: AI B, indipendente e non umano  
Artefatti canonici modificati dal revisore: nessuno

## Verdetto

- **Oracolo e freeze canonici: PASS.** L'oracolo è identico ai cicli
  precedenti; il freeze contiene 23/23 impronte coerenti.
- **Chiusura D-03: PASS.** Tipi JSON esatti, oggetti metadata chiusi
  ricorsivamente, 47/47 mutanti D-03 e 5/5 mutanti schema del freeze respinti.
- **Suite ufficiale: PASS, 69/69.** Il verificatore base termina con
  `error_count=0`.
- **Verificatore interamente fail-closed: FAIL.** È emerso **D-04**: le liste
  delle fonti di autorità vengono ridotte a mappe in modo lossy e la baseline
  dei 34 controlli è contata ma non resa univoca. Sette mutanti risigillati con
  duplicati o record ignorabili ottengono `error_count=0`.

D-04 è **non bloccante per i file canonici correnti**, le cui liste risultano
complete e univoche, ma è **bloccante per attestare il verificatore come
fail-closed contro fonti risigillate ostili**.

## Risultati richiesti

1. **Base — PASS.** `verify_oracle.py`: `error_count=0`, 120 casi, 102 accordi,
   18 adjudication, 34+4=38 controlli e 23 file congelati.
2. **Suite — PASS.** `test_oracle_mutations.py`: 69/69 respinti, inclusi tutti
   i 47 mutanti D-03 e i 5 nuovi mutanti del freeze.
3. **Replay indipendente — PASS.** Gli stessi 47+5 mutanti sono stati
   rieseguiti da un harness separato, risigillando oracle/file/freeze quando
   applicabile: 52/52 respinti dal codice atteso, con lista errori identica su
   due esecuzioni.
4. **Tipi e chiusura — PASS.** `first_json_mismatch` confronta prima
   `type(actual) is type(expected)`, poi chiavi complete dei dict, lunghezza e
   ordine delle liste e infine il valore scalare. `review`, `binding`,
   `registry_binding`, `counts`, `provenance`, `decisions` e `integrity` sono
   confrontati con oggetti attesi completi. Gli expected dei casi e dei nuovi
   controlli usano lo stesso confronto type-strict.
5. **Identità oracle — PASS.** File SHA-256
   `3e0d1155fad7b2920b3b04bb6e617747849c8fe4a1abfea5920f1b51dba6a123`;
   payload
   `2cdec2776fa48594db8729ec1aa17a635956a61f64b2ce7ad00aa32f8cc0427f`.
6. **Freeze — PASS.** File SHA-256
   `61fc3b24212e524c001ac0debddb9fac6e47bcd21b058e9f802e71f4ed12cf9c`;
   lock
   `3a42c11a1535afd9fc59bd9a38e772f637ae8a8ea8876a9a21de9cfe0efb1957`;
   23 fonti, zero mancanti e zero mismatch. Rispetto al ciclo 3 sono cambiate
   soltanto le impronte attese di `verify_oracle.py` e
   `test_oracle_mutations.py`.
7. **Letture JSON — PASS.** Nell'AST del verificatore l'unica `json.loads` è
   dentro `read_json`; la suite delega alla stessa funzione. Sono state rilette
   rigorosamente le 12 fonti JSON congelate e il freeze: 13 documenti.
8. **Dipendenze — PASS.** Il freeze non include se stesso tra le 23 impronte;
   verifier e suite non incorporano il digest del freeze o il lock corrente.
   La direzione resta sorgenti/verifier/test → impronte nel freeze → lock.
9. **Nessuna regressione canonica — PASS.** Registro: 7/7 mutazioni respinte;
   audit fonti: zero errori. Pre/post: nessuna delle 23 fonti, oracle, freeze o
   stato Git preesistente è variato per effetto della revisione.

## Batteria mirata aggiuntiva

Sono stati eseguiti **56 scenari aggiuntivi** in copie temporanee:

- 33 mutanti dell'oracolo: 33/33 respinti;
- 9 mutanti del freeze: 9/9 respinti;
- 2 controlli positivi di locator/symlink: 2/2 accettati correttamente;
- 12 mutanti delle liste sorgente: tutti hanno ottenuto zero errori; 7 sono
  falsi zero D-04, mentre 5 sono puri riordinamenti senza cambio della mappa
  semantica.

Le superfici verdi comprendono:

- inversione/scambio di `cases`, `new_controls`, body e dipendenze;
- `null`, NUL, zero-width e normalizzazione NFD in query, ID, route e chiavi;
- interi negativi o di 301 cifre in indice, arco, conteggio e ordinal;
- hash maiuscoli, di 63 o 65 caratteri e `null`;
- chiavi annidate mancanti o extra in casi, nodi, archi, controlli e decisioni;
- alias `./`, `..`, path assoluto, file mancante o extra nel freeze;
- risigillatura completa e determinismo dei messaggi.

I due controlli positivi confermano che un locator CLI contenente `..` o un
symlink verso gli stessi byte è accettato. Questo non è un bypass: le chiavi di
`freeze.files` restano esattamente quelle canoniche e ogni alias inserito nella
mappa viene respinto.

## Ripetizione del piano originale

| Controlli | Esito | Evidenza sintetica |
|---|---|---|
| ATK-01–07 | PASS | Parser rigoroso, 120/120, bijezione, testo/hash, unicità e ordine canonico. |
| ATK-08–16 | PASS | Root, grafi, route, reason, control, barrier, outcome e `data_from` conformi. |
| ATK-17–24 | PASS | Registro, freeze, fonti, banco, output salvati, catalog snapshot e double-AI coerenti. |
| ATK-25 | **FAIL** | Tracciabilità semantica corretta sui canonici, ma le liste delle fonti accettano duplicati/record ignorati dopo risigillatura: D-04. |
| ATK-26–29 | PASS | Baseline canonica 34, quattro aggiunte, zero collisioni e quattro assi distinti. |
| ATK-30 | PASS | 69 mutanti ufficiali, replay 47+5 e 42 mutanti mirati oracle/freeze respinti. |
| ATK-31 | PASS | Binding, ordine, hash e dipendenze mutate vengono respinti. |
| ATK-32 | PASS | Zero deriva delle fonti congelate o della produzione attribuibile alla revisione. |

Totale: **31 PASS, 1 FAIL**. I vincoli semantici chiave restano invariati:
casi 9/11 `find/persons`; 38 temporaneo `outside_registry`; 84 solo
`get/images` come vista degli indici materializzati; 113
`read/events -> create/files`; note future fuori dagli expected; 34+4=38.

## D-04 — estrazione lossy delle liste di autorità

### Causa

`source_cases` costruisce un dict con assegnazione `result[index] = case`:
duplicati precedenti vengono sovrascritti e record non-dict o con indice non
intero vengono ignorati. `controls_by_id` usa una dict comprehension con lo
stesso comportamento per `control_id`. La baseline verifica soltanto che
`len(cases) == 34`, poi costruisce set senza esigere 34 ID e query distinti.

### Liste vulnerabili esatte

1. `reviews/reviewer_a/intent_shadow_oracle_reviewer_a_v0_1.json` → `cases`;
2. `reviews/reviewer_b/intent_shadow_oracle_reviewer_b_v0_1.json` → `cases`;
3. `reviews/reviewer_a/adjudication_a.json` → `cases`;
4. `reviews/reviewer_a/controls_final_proposal_a.json` → `controls`;
5. `reviews/reviewer_b/controls_final_proposal_b.json` → `controls`;
6. `question_focus_controls_v1.json` → `cases`.

### Sette falsi zero minimi riprodotti

Per ogni mutante è stato aggiornato l'hash della fonte in `freeze.files` e
ricalcolato il lock; oracle e altre fonti sono rimasti invariati.

1. appendere una copia di `cases[0]` al reviewer A;
2. appendere `{"junk": true}` ai casi del reviewer A;
3. appendere una copia di `cases[0]` al reviewer B;
4. appendere una copia del primo caso all'adjudication;
5. appendere un controllo con `control_id` duplicato alla proposta A;
6. appendere un controllo con `control_id` duplicato alla proposta B;
7. nella baseline di lunghezza 34, sostituire l'ultimo caso con una copia del
   primo: rimangono 34 record, ma solo 33 controlli distinti.

Tutti e sette producono deterministically `error_count=0`. Il rischio non è
solo cosmetico: una coppia di record contraddittori viene risolta implicitamente
con last-write-wins; la baseline può perdere un controllo pur continuando a
dichiarare `34+4=38`.

### Riordinamenti

Sono passati anche cinque puri riordinamenti: casi A, casi B, adjudication,
controlli A e baseline. Non cambiano la mappa indice/ID né la semantica dei
120/38 e quindi non sono classificati come falsi zero senza una regola che
renda canonico l'ordine delle fonti. Il freeze ne rileva comunque il cambio di
byte se non viene risigillato. Se si vuole rendere anche l'ordine parte dello
schema fail-closed, va dichiarato ed eseguito il confronto con le sequenze
canoniche indicate sotto.

### Severità

- **Canonici correnti:** non bloccante; le sei liste sono complete, senza
  duplicati e con ordine coerente.
- **Verifier come gate fail-closed:** bloccante; fonti ambigue o una baseline
  ridotta a 33 elementi distinti possono essere risigillate con errore zero.
- **Semantica dei 120 expected correnti:** non alterata.

### Patch precisa suggerita, non applicata

1. Sostituire `source_cases` con un validatore che, prima di creare la mappa,
   richieda: root object; `cases` array; lunghezza esatta; ogni elemento object;
   indice di tipo JSON integer, mai bool; campo `expected` presente; nessun
   indice duplicato o estraneo. Le sequenze attese sono `0..119` per A/B e
   `ADJUDICATED_INDICES` per l'adjudication. In caso di errore non restituire
   una mappa parziale.
2. Sostituire `controls_by_id` con un validatore analogo: `controls` array,
   numero esatto previsto dalla proposta, ogni elemento object, `control_id`
   stringa non vuota, ID unici, tutti gli ID sorgente richiesti presenti e
   nessun record ignorato. Creare la mappa solo dopo la validazione completa.
3. Per la baseline esigere esattamente 34 record validi, 34 ID stringa unici,
   34 query stringa uniche e 34 hash query unici; non usare set costruiti da un
   sottoinsieme filtrato. Questa correzione rende vero il conteggio 34+4.
4. Se l'ordine è normativo, confrontare inoltre le liste di indici/ID con le
   sequenze canoniche; altrimenti dichiarare esplicitamente l'ordine delle
   fonti come non semantico e continuare ad accettare soltanto i puri
   riordinamenti.
5. Conservare il confronto JSON type-strict D-03 per gli `expected` estratti.

### Test di regressione necessari

Aggiungere mutanti risigillati che richiedano un codice d'errore deterministico
specifico, non il solo `error_count > 0`:

- duplicato all'inizio e alla fine delle liste casi A, B e adjudication;
- duplicato contraddittorio prima del record canonico, per provare che non
  esista più last-write-wins;
- record non-object e record con indice mancante, bool o fuori insieme;
- controllo duplicato/contraddittorio e record invalido in entrambe le
  proposte;
- baseline con duplicato a lunghezza 34, ID duplicato, query duplicata e record
  invalido;
- rimozione di una voce da ciascuna lista;
- riordino di ciascuna lista, con esito coerente alla scelta esplicita
  ordine-normativo oppure ordine-non-semantico;
- test positivo canonico, identità file/payload oracle e aggiornamento nel
  freeze soltanto delle impronte di verifier/suite più lock.

## Limiti

- Nessuna inferenza, GPU, rete, executor o servizio è stato avviato.
- Tutti i mutanti e i symlink sono rimasti in directory temporanee sotto
  `/tmp`; nessun artefatto canonico, banco, checkpoint, handover o file di
  produzione è stato modificato.
- Il freeze è un sigillo deterministico revisionato, non una firma esterna
  contro un autore autorizzato a risigillare ogni componente.
- Il risultato riguarda il campione congelato e il registro 0.1, non
  accuratezza universale.
