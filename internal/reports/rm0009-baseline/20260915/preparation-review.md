# RM-0009 — Review indipendente dei preparatori

Data: 15 settembre 2026. Revisore: agente `rm0009_inventory_security`, indipendente dagli autori del checker e del laboratorio.
Ambito: `internal/tools/rm0009_plan_check.py`, `internal/tools/rm0009_test_lab.py`, `tests/internal/test_rm0009_plan_check.py`, `tests/internal/test_rm0009_test_lab.py`.

**Versione fotografata: implementazione iniziale, identificata dai quattro digest in fondo.** Il coordinatore ha recepito le due riproduzioni P2 e annunciato correzioni e nuovi test; questa review non ne anticipa il risultato. La verifica mirata della versione corretta sarà una consegna distinta.

## Esito

**Due rilievi P2 nel checker producono falsi verdi sulle dipendenze del piano.** Il laboratorio funziona come creatore di archivi sintetici per test fidati; non è un isolamento di codice o un gestore automatico delle transazioni. I limiti di percorso e transazione sotto devono restare espliciti ai futuri test.

La suite esatta autorizzata passa: **28 raccolti, 28 passati, 0 saltati, 0 falliti**, 0,12 secondi riportati da pytest. Le riproduzioni negative aggiuntive non importano runtime Metnos e usano solo memoria o directory temporanee proprie. Nessun codice/test di prodotto modificato, servizio, rete, DB personale o segreto letto.

Questa review **non è G0.8**, non congela RM-0009 e non dimostra compatibilità con il futuro commit RM-0008. Rimane vincolante l'attesa della conclusione/commit dell'altro agente.

## P2-01 — Eliminare i template viene scambiato per averne completato l'espansione

Riferimento: `internal/tools/rm0009_plan_check.py:160–173`, in particolare `concrete: not templates` a riga 178; test `test_templates_block_a_concrete_freeze_but_not_draft_analysis` e `test_expanded_groups_must_be_ancestors_of_their_barrier`.

Il checker controlla i gruppi concreti che sono presenti, ma non conosce quelli attesi dall'inventario. Togliendo entrambi i template senza aggiungere alcun gruppo, il controllo rigoroso restituisce `valid=true, concrete=true`. Le barriere FS-A/FS-B continuano quindi a sembrare valide pur non dipendendo da nessuna conversione censita. È un falso verde rispetto a G0.5/G0.6 e alle barriere D-FS-A.3/D-FS-B.3.

Riproduzione solo in memoria, sul documento corrente:

```python
from pathlib import Path
from internal.tools import rm0009_plan_check as c
text = Path('internal/roadmap/RM-0009-crescita-allineata-delle-capacita.md').read_text()
g = c.parse_plan(text)
del g['D-FS-A.3.N']
del g['D-FS-B.3.N']
print(c.validate_plan(g, require_concrete=True))
```

Osservato: `valid=True`, `concrete=True`, `node_count=99`, `templates=[]`, `errors=[]`.

Correzione raccomandata: la modalità concreta deve ricevere l'insieme esatto degli ID attesi da un inventario con digest e verificare uguaglianza/copertura e raggiungibilità della barriera. Senza tale input deve dichiarare l'espansione **non verificata**, non completa. Non cablare il numero attuale di manifest e non assumere che ogni inventario futuro sia non vuoto.

Test mancante: template rimosso senza sostituzione; gruppo atteso assente; gruppo estraneo; inventario vuoto esplicitamente attestato; digest inventario diverso; tutti i gruppi presenti e antenati della barriera.

## P2-02 — I prerequisiti remoti sono verificati sul solo passaggio finale

Riferimento: `internal/tools/rm0009_plan_check.py:18–28` e `142–152`; RM-0009 D.1-ter controllo 3 e righe D-F6.4a–e.

La tabella `_REQUIRED` verifica D-F6.4e, ma non i nodi precedenti che implementano il confine remoto. È possibile spostare X0.1/FS-A.4/FS-B.4c al solo nodo finale mantenendo un verde, mentre D-F6.4b e il client dipendono solo dal percorso preliminare e dal protocollo. La riga D-F6.4b contiene già il server con CAS di start, completion firmata e rifiuto del downgrade: i prerequisiti non possono semplicemente apparire a valle del lavoro che realizza quel confine se il checker dichiara di verificarne la separazione secondo D.1-ter.

Riproduzione solo in memoria:

```python
g = c.parse_plan(text)
g['D-F6.4a'] = ('D-F6.2.barrier',)
g['D-F6.4e'] += ('D-X0.1', 'D-FS-A.4', 'D-FS-B.4c')
print(c.validate_plan(g))
print('D-FS-A.4' in c._ancestors(g, 'D-F6.4b'))
```

Osservato: `valid=True`, `errors=[]`; la seconda stampa è `False`. Le condizioni finali restano raggiungibili da 4e, ma non dal server 4b. Questa modifica contraddice inoltre le dipendenze esplicite della riga 4a del piano corrente.

Correzione raccomandata: mantenere un elenco esplicito/versionato dei nodi che attivano o realizzano l'enforcement remoto, con i rispettivi predecessori richiesti, e verificare ogni nodo pertinente. Distinguere lo schema puro 4a dalla presenza effettiva dei controlli server/client; non far dipendere un controllo sull'esercizio dal solo nome finale `4e`. Se lo strumento vuole deliberatamente controllare solo tre milestone, ridurre il contratto dichiarato e rendere visibile la mancata copertura delle altre righe, invece di attribuirgli l'intero D.1-ter.

Test mancante: spostamento dei prerequisiti verso un discendente; rimozione delle dipendenze server/client pur mantenendoli tutti tra gli antenati del rilascio finale; verifica parametrica dei nodi di enforcement effettivamente dichiarati.

## P3-03 — Fence non chiusa accettata; parser Markdown intenzionalmente ristretto

Riferimento: `internal/tools/rm0009_plan_check.py:46–56,88–90`.

Il parser non segnala un blocco di codice aperto e mai chiuso. La riproduzione `c.check_document(text + '\n```\n')['valid']` restituisce `True`. Inoltre usa soltanto i primi tre caratteri della fence, quindi non distingue le delimitazioni CommonMark di tre/quattro caratteri e tratta una riga che comincia con tre backtick come chiusura anche quando contiene altro testo.

Impatto: può ignorare future righe del piano o includere righe di esempio in modo diverso dalla resa Markdown. Non è una vulnerabilità d'esecuzione; è una diagnosi incompleta di un documento malformato. Raccomandato errore `unclosed_fence` e contratto esplicito sulle delimitazioni accettate, con test per fence lunghe, chiusura con testo e fine documento.

Non serve introdurre un parser Markdown generalista se il formato del piano viene ristretto e verificato esplicitamente.

## Laboratorio: proprietà dimostrate e limiti da conservare

### Percorsi: il nome dello store è chiuso, il filesystem non è confinato

Riferimento: `internal/tools/rm0009_test_lab.py:60–78`; `tests/internal/test_rm0009_test_lab.py:55–87` e prova cleanup.

`LabStore` rifiuta nomi arbitrari e le directory nuove sono private. Tuttavia SQLite segue un symlink già presente al posto dello store; un alias può quindi indirizzare una connessione fuori dalla radice del laboratorio. Lo si è dimostrato **soltanto verso un secondo database sintetico in /tmp**:

```python
import sqlite3, tempfile
from pathlib import Path
from internal.tools.rm0009_test_lab import temporary_lab, LabStore
with tempfile.TemporaryDirectory(prefix='rm0009-review-synthetic-') as outside:
    target = Path(outside) / 'outside.sqlite'
    with sqlite3.connect(target) as con:
        con.execute('CREATE TABLE sentinel(x)')
    with temporary_lab() as lab:
        (lab.data / 'governance.sqlite').symlink_to(target)
        with lab.connection(LabStore.GOVERNANCE) as con:
            con.execute("INSERT INTO sentinel VALUES ('crossed_lab_boundary')")
            con.commit()
    with sqlite3.connect(target) as con:
        assert con.execute('SELECT * FROM sentinel').fetchall() == [
            ('crossed_lab_boundary',)
        ]
```

Questa riproduzione è riuscita. **Non viene classificata come falla di sandbox**: il modulo dichiara correttamente di non esserlo, e un test fidato può già scrivere fuori dal laboratorio. È però un limite reale alla frase “only synthetic archives”: i test devono usare soltanto percorsi creati dal laboratorio ed evitare link a sorgenti. Un controllo no-follow/containment può proteggere da errori di preparazione, ma non va presentato come difesa contro codice ostile dello stesso processo. Manca un test che documenti rifiuto o responsabilità del chiamante per alias, percorsi ricostruiti tramite dataclass e symlink nella data directory.

### Transazioni: DDL senza BEGIN sopravvive alla chiusura

Riferimento: `internal/tools/rm0009_test_lab.py:64–71`; test `test_stores_are_separate_and_rollback_uncommitted_work`.

La connessione non apre una transazione globale. Con le impostazioni SQLite correnti, `CREATE TABLE` eseguito prima di un `BEGIN` non rimane in una transazione da annullare; il `rollback()` finale non elimina lo schema. Riproduzione riuscita:

```python
with temporary_lab() as lab:
    with lab.connection(LabStore.GOVERNANCE) as con:
        con.execute('CREATE TABLE without_commit(x)')
    with lab.connection(LabStore.GOVERNANCE) as con:
        assert con.execute(
            "SELECT name FROM sqlite_master WHERE name='without_commit'"
        ).fetchone() is not None
```

Questo comportamento è coerente con l'assenza di un commit implicito del gestore e **non è una violazione SQLite**. I futuri test di migrazione e crash devono aprire esplicitamente `BEGIN IMMEDIATE` dove il contratto lo richiede, altrimenti rischiano un test che attribuisce al laboratorio una transazione inesistente. Non aggiungere automaticamente BEGIN se i test devono poter controllare in autonomia CAS, concorrenza e confini del commit. Aggiungere una prova che documenti DDL fuori/dentro transazione e rollback in presenza di eccezione.

### Ambiente

`environment()` restituisce un dizionario nuovo di soli percorsi, senza copiare segreti dall'ambiente del chiamante. Il test con `METNOS_REAL_SECRET` lo conferma. Non modifica `os.environ` e non promette che i consumer ignorino HOME o altre configurazioni: il dizionario è un insieme di override, non un ambiente di esecuzione certificato. È corretto non importare runtime Metnos qui.

Prima di riusarlo con codice di prodotto servirà un adattatore di test che espliciti quali override applica e quali configurazioni ignora. La sola verifica lessicale `Path(value).is_relative_to(lab.root)` non prova il containment dopo symlink. Mancano test su due laboratori contemporanei e riuso degli oggetti dopo uscita dal contesto; sono completamenti utili, non veti per l'uso attuale con fixture fidate.

### Cleanup

La prova esistente verifica eliminazione della radice e conservazione di un file esterno. Una riproduzione aggiuntiva con `RuntimeError` dentro `temporary_lab()` conferma la rimozione anche su eccezione. Non sono state simulate interruzioni di processo o prove Windows; non dichiarare certificata la pulizia in questi scenari. La chiusura delle connessioni appartiene al contesto `connection()`, quindi le prove future devono rispettarne il ciclo di vita.

## Controlli riusciti

- Le dipendenze sconosciute e i cicli vengono negati; il testimone di ciclo è deterministico.
- Il controllo non importa runtime né apre store applicativi.
- La CLI legge bytes prima di decodificare: una prova CRLF su file temporaneo conferma che `document_sha256` è esattamente SHA-256 dei byte del file, senza normalizzazione delle newline.
- La CLI non scrive il documento. Il digest identifica il contenuto letto, ma non è approvazione o congelamento del file su disco dopo la lettura.
- Lo scope di output `plan_dependencies_only` evita una pretesa di certificazione runtime; va mantenuto.
- WAL e foreign key sono abilitati nelle connessioni del laboratorio, gli store governance/target sono distinti e gli INSERT non committati sono annullati alla chiusura.
- Le radici private sono eliminate anche quando il blocco del laboratorio termina con eccezione.

## Prova eseguita e baseline dei file

Comando esatto autorizzato:

```bash
METNOS_USER_DATA=/tmp/metnos-rm0009-checks-qJn9f3/data METNOS_USER_CONFIG=/tmp/metnos-rm0009-checks-qJn9f3/config .venv/bin/python -m pytest -q tests/internal/test_rm0009_plan_check.py tests/internal/test_rm0009_test_lab.py
```

Risultato: `28 passed in 0.12s`. Nessuna suite ulteriore, nessun test legacy e nessuna esecuzione di codice candidato.

| File | SHA-256 revisionato |
|---|---|
| `internal/tools/rm0009_plan_check.py` | `b7e2edc431ff2b73ddab0105a49bbefadbf135a8f2a64e6e82bd3f7633c246b5` |
| `internal/tools/rm0009_test_lab.py` | `bfc0f7866fedb37506e9d5b5619d76f030cda07383004cdee8759dab18f3ed21` |
| `tests/internal/test_rm0009_plan_check.py` | `2d901dea420891aba0795f9ecb217c48a6c625d743c349edeb56a67484968c82` |
| `tests/internal/test_rm0009_test_lab.py` | `328847029f3b4b76726adfd6069547191bc14f8037b201eceb1f73a4049e101a` |

Tutti e quattro erano file non tracciati nella baseline della review, attribuiti ai rispettivi autori dall'incarico. Nessun loro edit è stato eseguito dal revisore. Il solo file scritto è questo rapporto. Ogni correzione successiva richiede aggiornare i digest e ripetere le riproduzioni pertinenti; i 28 verdi non sostituiscono i casi negativi sopra.
