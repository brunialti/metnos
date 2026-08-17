# Revisione avversariale indipendente — ciclo 3

Data: 2026-08-12  
Revisore: AI B, indipendente e non umano  
Artefatti canonici modificati dal revisore: nessuno

## Verdetto

- **Oracolo e freeze canonici: PASS.** I byte e il payload dell'oracolo sono
  invariati; le 23 impronte e il nuovo lock sono coerenti.
- **Patch dei numeri JSON: PASS.** `NaN`, `Infinity`, `-Infinity`, overflow
  `1e999`/`-1e999` e valori annidati vengono respinti, anche dopo risigillatura,
  con messaggi deterministici.
- **Verificatore pienamente fail-closed: FAIL.** È emerso **D-03**: confronti
  Python non sensibili al tipo e metadati incompletamente vincolati consentono
  mutanti risigillati con `error_count=0`.

D-03 è **non bloccante per i byte canonici correnti**, che contengono i tipi e i
valori previsti, ma è **bloccante per attestare il verificatore come fail-closed
contro un oracle risigillato ostile**.

## Esiti richiesti

1. **Parser numerico — PASS.** Oltre ai 4 mutanti ufficiali, 12 scenari
   indipendenti hanno coperto i cinque token `NaN`, `Infinity`, `-Infinity`,
   `1e999`, `-1e999` sia nell'oracle sia nel freeze, più un valore in un array
   annidato e uno nella mappa `freeze.files`: 12/12 respinti. Ogni scenario è
   stato eseguito due volte e ha prodotto la stessa lista di errori.
2. **Tutte le letture JSON — PASS.** Nell'AST di `verify_oracle.py` esiste una
   sola chiamata `json.loads`, dentro `read_json`; oracle, freeze, banco,
   registro e fonti vengono letti da quella funzione. La suite delega a
   `verifier.read_json`. Sono stati riletti rigorosamente i 12 JSON tra le 23
   fonti congelate e il freeze stesso: 13 documenti totali.
3. **Serializzazione — PASS.** Hash canonici, hash del banco, output del
   verificatore e scritture della suite usano `allow_nan=False`.
4. **Suite ufficiali — PASS.** Verificatore base: `error_count=0`. Suite oracle:
   17/17 mutazioni respinte. Registro: 7/7 mutazioni respinte. Audit fonti:
   `error_count=0`.
5. **Identità dell'oracolo — PASS.** SHA-256 file
   `3e0d1155fad7b2920b3b04bb6e617747849c8fe4a1abfea5920f1b51dba6a123`;
   payload
   `2cdec2776fa48594db8729ec1aa17a635956a61f64b2ce7ad00aa32f8cc0427f`.
   Entrambi coincidono con i cicli precedenti.
6. **Freeze — PASS.** Esattamente 23 file, zero mancanti e zero mismatch; lock
   `8827e50e521f0d3b8f6fb1891acf82eb077f262877f3f2fd4307530163441fa0`.
   Rispetto al ciclo 2 sono cambiate soltanto le due impronte attese:
   `verify_oracle.py` →
   `4ae3a8345b47ef7b305aaa0131e6d4f1ac15e903b39c6bc55550e7ba63065ad3`
   e `test_oracle_mutations.py` →
   `232579a99b8072cc0b1c4453b0d7a16e878c0ef07d2ce8606402c2701c6ea2ce`.
7. **Dipendenze e regressioni — PASS con D-03.** Il freeze non include se
   stesso; verificatore e suite non incorporano il digest del freeze. Restano
   validi 120/120, 102+18, radici 84/2/34, casi 9/11, 38, 84, 113, 34+4,
   provenienza double-AI e catalog snapshot arretrato.
8. **Piano originario — 31 PASS, 1 FAIL.** ATK-01–29 e ATK-31–32 passano;
   ATK-30 fallisce sui mutanti D-03. I mutanti ufficiali inclusi in ATK-30
   continuano a passare.

## Evidenza numerica e messaggi

Esempi esatti, stabili su due esecuzioni:

```text
ORACLE_READ: non-finite JSON number at $["decisions"]["compound_fail_closed"]["rule"]: -Infinity
ORACLE_READ: non-finite JSON number at $["new_controls"][0]["semantic_duplicate_of_existing_control_ids"][0]: Infinity
FREEZE_READ: non-finite JSON number at $["created_date"]: 1e999
FREEZE_READ: non-finite JSON number at $["files"]["internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_oracle_v0_1.json"]: -Infinity
```

Il parser conserva il lessema problematico in una sentinella, percorre
ricorsivamente dict e list e lo rifiuta prima della validazione semantica.

## Fuzzing aggiuntivo

Una prima batteria di 31 mutanti ha respinto 17 scenari: bool al posto di
indice/arco, struttura `cases` errata, campo top-level extra, annidamento
patologico, chiave duplicata scritta con escape Unicode, ID alterati, digest
maiuscolo o booleano, set di file mancante/extra, `files` non oggetto e path
assoluti o con alias `./`/`..`. Tutti i 9 attacchi mirati al freeze sono stati
respinti.

La matrice esaustiva dei metadati ha poi provato 47 mutanti minimi risigillati:
27 confusioni di tipo, 8 valori non vincolati, 5 rimozioni di campi non
vincolati e 7 chiavi annidate inattese. **Tutti e 47 hanno ottenuto zero
errori.** Le superfici si sovrappongono in parte alla prima batteria, quindi i
due conteggi non vengono sommati.

## D-03 — confronti non type-strict e schema metadata incompleto

### Causa

I cicli `actual != expected` e il confronto di dict usano l'uguaglianza Python:
`False == 0`, `True == 1` e `2 == 2.0`. Inoltre alcuni campi sono soltanto
presenti, ma il loro valore non viene controllato; i sette sotto-oggetti di
`decisions` non applicano `exact_keys`.

### Tutti i campi type-confusable osservati (27)

Ogni riga indica il mutante minimo accettato dopo risigillatura.

- `review.human_review`: `false` → `0`.
- `review.blind_phase_completed`: `true` → `1`.
- `review.independent_reviews`: `true` → `1`.
- `review.reviewer_count`: `2` → `2.0`.
- `review.exact_agreement_case_count`: `102` → `102.0`.
- `review.adjudicated_case_count`: `18` → `18.0`.
- `binding.query_count`: `120` → `120.0`.
- `binding.unique_query_count`: `120` → `120.0`.
- `counts.sample_case_count`: `120` → `120.0`.
- `counts.unique_sample_indices`: `120` → `120.0`.
- `counts.unique_sample_queries`: `120` → `120.0`.
- `counts.unique_sample_query_hashes`: `120` → `120.0`.
- `counts.reviewer_exact_agreement_count`: `102` → `102.0`.
- `counts.adjudicated_count`: `18` → `18.0`.
- `counts.operation_graph_count`: `84` → `84.0`.
- `counts.system_control_count`: `2` → `2.0`.
- `counts.unrepresentable_count`: `34` → `34.0`.
- `counts.existing_control_count`: `34` → `34.0`.
- `counts.new_control_count`: `4` → `4.0`.
- `counts.authorized_control_total`: `38` → `38.0`.
- `provenance.semantic_control_deduplication.reviewed_against_existing_34`:
  `true` → `1`.
- `provenance.semantic_control_deduplication.reviewed_pairwise`: `true` → `1`.
- `decisions.catalog_scope.catalog_snapshot_executor_count`: `96` → `96.0`.
- `new_controls[0].ordinal`: `35` → `35.0`.
- `new_controls[1].ordinal`: `36` → `36.0`.
- `new_controls[2].ordinal`: `37` → `37.0`.
- `new_controls[3].ordinal`: `38` → `38.0`.

Gli indici dei 120 casi e gli ordinali `data_from` non fanno parte della falla:
controlli espliciti `int` con esclusione di `bool` li respingono.

### Tutti i campi canonici con valore non vincolato (8)

- `provenance.agreed_case_policy`: accettato un array al posto della stringa.
- `provenance.divergent_case_policy`: accettato un oggetto al posto della
  stringa.
- `decisions.compound_fail_closed.rule`: accettati `1e308` e un oggetto.
- `decisions.case_038.future_change_is_not_retroactive`: accettato un intero di
  301 cifre.
- `decisions.catalog_scope.catalog_snapshot_path`: accettato un array.
- `decisions.catalog_scope.current_manifests_are_observed_but_not_silently_substituted`:
  accettato un oggetto.
- `decisions.future_corpus_registry.guides_nightly_reindexing`: accettata una
  stringa arbitraria.
- `integrity.convention`: accettato `null`.

I cinque campi sotto `decisions` possono anche essere rimossi senza errore. I
due campi di provenance e `integrity.convention` devono essere presenti per il
controllo delle chiavi, ma il loro valore e tipo sono liberi. Stringhe con NUL
incorporato sono state accettate nelle policy non vincolate.

### Sotto-oggetti aperti a chiavi arbitrarie (7)

L'aggiunta singola di `"unexpected_cycle3_key": {"nested": [1, true, null]}` è
stata accettata in ciascuno di:

- `decisions.compound_fail_closed`;
- `decisions.case_038`;
- `decisions.case_084`;
- `decisions.case_113`;
- `decisions.catalog_scope`;
- `decisions.future_corpus_registry`;
- `decisions.corpus_unavailability`.

Questa è una superficie aperta, non un elenco finito di nomi: qualunque chiave
extra in questi sette oggetti non è attualmente vincolata.

### Severità

- **Canonico corrente:** non bloccante; verifica indipendente dei byte conferma
  valori e tipi corretti e invariati.
- **Verifier/sigillo come controllo fail-closed:** bloccante. Un autore capace
  di risigillare può alterare dichiarazioni di revisione, conteggi, decisioni e
  convenzione di integrità ottenendo un falso zero.
- **Freeze:** nessun difetto nuovo trovato. I mutanti di path, set e impronta
  sono stati respinti; D-03 è nella validazione dell'oracle.

### Patch precisa suggerita, non applicata

1. Aggiungere un confronto JSON ricorsivo sensibile al tipo:

```python
def same_json_value(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(
            same_json_value(actual[key], expected[key]) for key in expected
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            same_json_value(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected
```

2. Usarlo al posto di `!=` nei confronti con valori attesi di `review`,
   `binding`, `registry_binding`, `counts`, provenance/dedup e
   `new_controls.expected_values`. Mantenerlo anche per i dict `expected`
   confrontati con le fonti, così il vincolo resta corretto se in futuro
   compaiono scalari booleani o numerici.
3. Definire l'oggetto atteso completo e type-strict per `provenance`, per tutti
   i sotto-oggetti `decisions` e per `integrity.convention`; applicare
   `exact_keys` a ciascuno dei sette sotto-oggetti. Costruire a runtime soltanto
   i valori realmente dinamici, come path e hash.
4. Conservare i controlli espliciti `is True`/`is False`, `int` senza `bool`,
   `valid_sha` e il parser non-finito: coprono superfici diverse.

### Test di regressione necessari

Aggiungere mutanti risigillati che esigano codici d'errore deterministici, non
soltanto `error_count > 0`:

- `false→0`, `true→1` e `int→float` su almeno un campo per ciascuna famiglia
  (`review`, `binding`, `counts`, dedup, decisione, ordinal controllo);
- uno per tutti gli 8 campi oggi non vincolati, includendo `1e308`, intero di
  301 cifre, valore strutturato, `null` e NUL Unicode;
- rimozione dei 5 campi `decisions` oggi facoltativi di fatto;
- chiave extra in ognuno dei 7 sotto-oggetti `decisions`;
- test positivo che il canonico conservi `error_count=0` e test d'identità che
  file/payload oracle non cambino durante l'aggiornamento di verifier, suite e
  freeze.

## Limiti

- Nessuna inferenza, GPU, rete, executor o servizio è stato avviato.
- Tutte le copie mutate sono rimaste in `/tmp`; nessun canonico, banco,
  checkpoint, handover o file di produzione è stato modificato.
- L'errore zero di base certifica soltanto i vincoli implementati del campione
  congelato e del registro 0.1; non prova accuratezza universale né rende il
  freeze una firma esterna contro un autore autorizzato a risigillare.
