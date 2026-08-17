# Revisione avversariale indipendente dell'oracolo canonico

Data: 2026-08-12  
Revisore: AI B, indipendente e non umano  
Esito: **PASS dell'oracolo canonico; 1 difetto non bloccante nel verificatore**

## Sintesi

Sono stati eseguiti tutti i 32 controlli del piano indipendente. L'oracolo e il
freeze correnti superano i controlli di binding, semantica adjudicata,
tipizzazione, provenienza e integrità. Il verificatore canonico termina con
`error_count = 0`; la suite canonica respinge tutte le 11 mutazioni previste.

L'errore zero significa soltanto che questi vincoli congelati sono soddisfatti:
non dimostra accuratezza universale oltre i 120 casi né completezza del
registro 0.1.

## Risultati dei 32 controlli

| ID | Esito | Evidenza sintetica |
|---|---|---|
| ATK-01 | PASS con rilievo | JSON valido, formato/versione corretti e nessuna chiave duplicata nei due file correnti. Il parser del verificatore non è però rigoroso: vedi D-01. |
| ATK-02 | PASS | Esattamente 120 casi. |
| ATK-03 | PASS | Bijezione 120/120 con il banco per indice e hash; nessun caso estraneo. |
| ATK-04 | PASS | Testi UTF-8 identici al banco congelato, inclusa punteggiatura. |
| ATK-05 | PASS | Tutti i 120 hash ricalcolati coincidono con testo e banco. |
| ATK-06 | PASS | 120 indici, ID e hash unici. |
| ATK-07 | PASS | Ordine esatto 0..119 e serializzazione deterministica ripetibile. |
| ATK-08 | PASS | Radici esclusive e campi ammessi soltanto. |
| ATK-09 | PASS | Tutti i grafi e rami emessi sono non vuoti e tipizzati. |
| ATK-10 | PASS | Tutte le route appartengono al registro congelato. |
| ATK-11 | PASS | Tutte le reason appartengono al registro; nessun sottografo è conservato nelle radici unrepresentable. |
| ATK-12 | PASS | Entrambi i casi di controllo usano `undo_last_turn` come radice `system_control`. |
| ATK-13 | PASS | Barriera `get/approval`, outcome e ordine conformi; outcome omessi sono continuazioni vuote ammesse dal contratto. |
| ATK-14 | PASS | Ogni `data_from` punta a un producer precedente e dominante; porte uniche derivabili. |
| ATK-15 | PASS | Nessuna fuga da ramo o riferimento tra fratelli. |
| ATK-16 | PASS | Ordine e dipendenze dei casi adjudicati coincidono con le decisioni approvate. |
| ATK-17 | PASS | Contratto, registro e payload hash coincidono con il freeze. |
| ATK-18 | PASS | Registro e freeze del registro verificati; 7/7 mutazioni del registro respinte. |
| ATK-19 | PASS | Le fonti congelate richiamate dal registro conservano le impronte dichiarate. |
| ATK-20 | PASS | Audit delle fonti rieseguito con zero errori. |
| ATK-21 | PASS | Banco: 120 richieste e SHA-256 semantico/file invariati. |
| ATK-22 | PASS | c10, c11, repaired e sealed conservano le impronte documentate; il controllo sealed rieseguito passa. La provenienza canonica non promuove l'accordo dei bracci a gold. |
| ATK-23 | PASS | Snapshot dichiarato arretrato: 96 executor nello snapshot contro 83 manifest correnti; 11 aggiunte e 24 assenze nominali osservate. Lo snapshot resta autorità congelata e non viene sostituito silenziosamente. |
| ATK-24 | PASS | Metadati: doppia revisione AI indipendente, `human_review=false`, due revisori. |
| ATK-25 | PASS | 102 accordi esatti e tutti e soli i 18 indici divergenti legati all'adjudication approvata. |
| ATK-26 | PASS | Baseline invariata: 34 controlli e hash file atteso. |
| ATK-27 | PASS | Selezione A/A/B/A esatta; 4 nuovi controlli, totale autorizzato 38. |
| ATK-28 | PASS | Nessuna collisione di testo o hash con i 34, tra i quattro o con le 120 richieste. |
| ATK-29 | PASS | I quattro assi sono distinti: fail-closed composto, vista multi-corpus materializzata, proiezione strutturata, similarità da foto. |
| ATK-30 | PASS con rilievo | Le mutazioni tipizzate canoniche e aggiuntive vengono respinte; il mutante con chiave JSON duplicata è invece mancato, vedi D-01. |
| ATK-31 | PASS | Testo/hash scambiati, caso assente/duplicato e archi futuri, tra fratelli o da ramo verso esterno vengono respinti. |
| ATK-32 | PASS | Pre/post: tutte le 23 fonti congelate identiche, stato Git identico, nessun processo o servizio rilevante apparso/scomparso; nessuna GPU o servizio avviato. |

## Controllo semantico mirato

- **Casi 9 e 11:** `find/persons`. Il contratto dell'executor accetta una
  reference image e fa confronto facciale; `get/persons` legge il registro per
  nome o lo elenca e non accetta foto.
- **Caso 38:** temporaneamente `unrepresentable/outside_registry`. La nota
  futura sulla pipeline completa testo/PDF resta nei metadati decisionali e
  nell'adjudication, non nell'expected.
- **Caso 84:** singolo `get/images`. È dichiarato come vista corrente degli
  indici unificati già materializzati; non è chiamato registro persistente. Il
  futuro registro corpus e lo stato futuro `unavailable_not_deleted` sono
  esplicitamente fuori dagli expected 0.1.
- **Caso 113:** `read/events -> create/files`, con `data_from: [{"from": 0}]`.
  Selezione e rinomina di campi già strutturati sono proiezione.
- **Altri 15 casi divergenti:** expected e prove coincidono 15/15 con le regole
  già approvate: significato esatto, conteggio dal producer e fail-closed del
  composto con clausola indispensabile fuori registro.

## Suite di mutazione

La suite canonica ha respinto 11/11 mutazioni:

1. caso mancante;
2. caso duplicato;
3. testo della query modificato;
4. hash della query modificato;
5. radice sconosciuta;
6. route sconosciuta;
7. reason sconosciuta;
8. arco dati non dominante;
9. controllo duplicato;
10. hash file del freeze alterato;
11. hash del lock alterato.

Sono stati inoltre provati in `/tmp`, con oracle e freeze risigillati in memoria,
controllo/barriera/outcome sconosciuti, outcome duplicato o fuori ordine, radice
mescolata, campo operation extra, arco in avanti, tra rami fratelli e da ramo
verso l'esterno: 10/10 respinti.

## Difetto D-01 — non bloccante per l'oracolo corrente

**Problema.** `verify_oracle.py::read_json` usa `json.loads` senza un
`object_pairs_hook`. In JSON, una chiave duplicata viene quindi accettata e
l'ultimo valore sostituisce silenziosamente il primo.

**Riproduzione concettuale.** Su una copia temporanea, aggiungere prima del
valore valido una seconda chiave top-level, per esempio:

```json
"status": "tampered_first_value",
"status": "canonical_frozen"
```

Poi aggiornare coerentemente hash file e lock del freeze. Il verificatore
restituisce zero errori perché vede soltanto il secondo valore. Il mutante non
tocca gli artefatti canonici ed è stato eseguito esclusivamente in `/tmp`.

**Classificazione.** Non bloccante per l'oracolo congelato attuale: un controllo
indipendente conferma che oracle e freeze correnti non contengono chiavi
duplicate. È però una falla fail-closed del verificatore e va corretta prima di
considerarlo robusto contro input JSON ostili o risigillati.

**Patch precisa suggerita, non applicata.** In `verify_oracle.py`, sostituire il
caricamento con un hook che rifiuti duplicati a ogni livello:

```python
def reject_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key!r}")
        value[key] = item
    return value


def read_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_keys,
    )
```

Aggiungere a `test_oracle_mutations.py` il dodicesimo mutante
`duplicate_json_key`: inserisce due chiavi `status`, aggiorna soltanto gli hash
del freeze come nel test avversariale e deve produrre `error_count > 0` con
`ORACLE_READ`. Dopo la patch occorre rigenerare le impronte di verificatore,
suite e lock del freeze secondo la procedura canonica.

## Verdetto

**Oracolo canonico: PASS, nessun difetto bloccante trovato.**  
**Verificatore: PASS funzionale sui vincoli correnti, con D-01 non bloccante da
correggere.** Nessun artefatto canonico, banco, output salvato o file di
produzione è stato modificato.
