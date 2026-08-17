# Revisione avversariale indipendente — ciclo 2

Data: 2026-08-12  
Revisore: AI B, indipendente e non umano  
Artefatti canonici modificati dal revisore: nessuno

## Verdetto

- **Oracolo canonico e freeze: PASS.** Contenuto, semantica e 23 impronte sono coerenti.
- **Correzione delle chiavi JSON duplicate: PASS.** La falla D-01 del primo ciclo è chiusa per oracle, freeze e sorgenti JSON lette dal verificatore.
- **Verificatore pienamente fail-closed: FAIL.** È emerso D-02: accetta numeri JSON non finiti. Il difetto non è presente nei file canonici correnti, ma permette un falso zero su un mutante risigillato.

D-02 è quindi **non bloccante per la correttezza dei byte canonici attuali**, ma
**bloccante per attestare il verificatore come fail-closed contro JSON ostile**.

## Esiti richiesti

1. **Chiusura duplicate-key — PASS.** La suite ufficiale respinge sia la chiave duplicata nell'oracle sia quella nel freeze. Due prove aggiuntive hanno duplicato una chiave annidata: l'oracle con hash file e lock aggiornati è stato respinto con `ORACLE_READ`; il freeze è stato respinto con `FREEZE_READ`.
2. **Parser unico — PASS.** Tutte le letture JSON di `verify_oracle.py` passano da `read_json`; l'unica chiamata diretta a `json.loads` è dentro quella funzione e usa `object_pairs_hook=reject_duplicate_json_keys`. Anche la suite riusa `verifier.read_json`.
3. **Verifiche ufficiali — PASS.** `verify_oracle.py`: `error_count=0`. `test_oracle_mutations.py`: 13/13 mutazioni respinte. Registro: 7/7 mutazioni respinte. Audit fonti: zero errori.
4. **Oracle invariato — PASS.** SHA-256 file ancora `3e0d1155fad7b2920b3b04bb6e617747849c8fe4a1abfea5920f1b51dba6a123`; payload ancora `2cdec2776fa48594db8729ec1aa17a635956a61f64b2ce7ad00aa32f8cc0427f`. L'identità byte-per-byte rende invariati anche expected, ordine e metadati semantici.
5. **Nuovo freeze — PASS.** Contiene esattamente 23 fonti, tutte presenti e con hash corretto. Sono cambiate soltanto le impronte previste di `verify_oracle.py` e `test_oracle_mutations.py`; oracle e altre 21 fonti sono identici. I nuovi hash sono rispettivamente `955c4ecc5334594be9f5a8a5fcc569c8c5c44b33e5a1c27b3f6407d7ee39c16e` e `250496635f6c7a3e7281b4961db1cb38266f3e399d04f78d61c5c1b09c0cbd99`; lock `5178eb745143addd749ec62c44e84b4b9afef5986b34ac0b0f77f1c97470f2b9`.
6. **Effetti collaterali e cicli — PASS con D-02.** Nessuna dipendenza crittografica circolare: il freeze non include il proprio file tra le 23 impronte; oracle, verificatore e suite non incorporano il digest del freeze. La direzione resta oracle/verificatore/test → impronte nel freeze → lock. Nessuna regressione strutturale osservata. D-02 è una lacuna di parsing preesistente resa visibile dal nuovo attacco, non una variazione dell'oracolo.
7. **Ripetizione piano avversariale — 31 PASS, 1 FAIL aggiuntivo.** ATK-01–29 e ATK-31–32 passano; ATK-30 fallisce soltanto sul nuovo mutante non finito descritto sotto. I 13 mutanti ufficiali inclusi in ATK-30 passano.

## Vincoli chiave ricontrollati

- 120/120 casi, indici ordinati 0..119, testi e hash legati al banco, tutti unici.
- 102 accordi esatti e tutti e soli i 18 casi divergenti presi dall'adjudication approvata.
- Radici: 84 `operation_graph`, 2 `system_control`, 34 `unrepresentable`; route, reason, control, barrier, outcome, ordine e `data_from` conformi.
- Casi 9/11 `find/persons`; 38 temporaneamente `outside_registry`; 84 solo `get/images` come vista degli indici materializzati; 113 `read/events -> create/files` con dipendenza da 0.
- Note future su pipeline testo/PDF, registro corpus persistente e corpus indisponibile restano nei metadati decisionali, mai negli expected.
- 34+4=38 controlli, selezione A/A/B/A, nessuna collisione testuale/hash e quattro assi semantici distinti.
- Snapshot catalogo dichiarato arretrato: 96 voci contro 83 manifest correnti; nessuna sostituzione silenziosa.
- Provenienza double-AI indipendente e non umana conservata.
- Tutti i JSON congelati letti dal verificatore superano il parser duplicate-key; oracle e freeze correnti contengono zero valori numerici non finiti.
- Controllo sealed degli output salvati riuscito; pre/post invariati 23 file congelati, stato Git e processi rilevanti. Nessuna GPU o servizio avviato.

## D-02 — numeri JSON non finiti accettati

### Riproduzione concettuale

Su una copia temporanea dell'oracle è stato sostituito il testo descrittivo
`decisions.compound_fail_closed.rule`, campo non usato dalla validazione
semantica, prima con `NaN` e poi con `1e999`. Per ciascuna variante sono stati
ricalcolati coerentemente payload dell'oracle, hash file, payload del freeze e
lock. Entrambe le copie hanno ottenuto `error_count=0`.

Il motivo è duplice:

- `json.loads` di Python accetta i token non standard `NaN`/`Infinity` se non si imposta `parse_constant`;
- un numero formalmente scritto come `1e999` diventa `float('inf')` se non si controlla `parse_float`.

### Patch precisa suggerita, non applicata

In `verify_oracle.py`:

```python
import math


def reject_nonfinite_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON number: {value}")


def parse_finite_json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"non-finite JSON number: {value}")
    return number


def read_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_json_keys,
        parse_constant=reject_nonfinite_json_constant,
        parse_float=parse_finite_json_float,
    )
```

Impostare inoltre `allow_nan=False` in `canonical_sha256` e nelle funzioni di
serializzazione della suite, così neppure un oggetto costruito in memoria può
essere sigillato come JSON non standard.

Aggiungere due mutanti integrati, uno con `NaN` e uno con `1e999`, che esigano
come primo errore `ORACLE_READ: non-finite JSON number: ...`; per evitare un
falso positivo, devono aggiornare hash file e lock e controllare il codice
d'errore esatto. Dopo la correzione, aggiornare nel freeze soltanto le impronte
di verificatore e suite e ricalcolare il lock, lasciando invariati file e
payload dell'oracle.

## Limiti dell'attestazione

- Non è stata eseguita inferenza né alcun servizio o executor.
- La revisione prova i vincoli del campione congelato e del registro 0.1, non accuratezza universale.
- Il freeze è un sigillo deterministico revisionato, non una firma esterna contro un autore autorizzato a risigillare ogni componente.
