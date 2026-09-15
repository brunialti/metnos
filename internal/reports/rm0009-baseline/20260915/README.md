# RM-0009 — preparazione e punto di ripartenza dopo RM-0008

Data: 15 settembre 2026. Stato: **preparazione completata nel perimetro qui
descritto; integrazione/runtime non iniziati**. La roadmap resta `active`, non
`ready`. Questi rapporti non sono un secondo documento normativo.

**Aggiornamento conclusivo, 15:37 UTC:** la task RM-0008 osservata ha terminato
il lavoro e il checkout risulta al commit
`1c308922839f7659a3cf54d988f995bba0f215d6`, senza modifiche pendenti ai file
tracciati. L'attesa di coordinamento è soddisfatta per quell'incarico. Restano
da ricontrollare inventari, contratti G0 e corrispondenza della release: non si
dichiara conclusa ogni fase della roadmap RM-0008 o acquisita la prova F5.
Le analisi iniziali sotto restano fotografie dei commit precedenti.

## Cosa è disponibile

| Artefatto | Uso e limite |
|---|---|
| [preflight-observation.json](preflight-observation.json) | Fotografia iniziale di commit, modifiche preesistenti, versioni e stato osservato dei servizi; non snapshot dei dati e non baseline congelata. |
| [upstream-completion.json](upstream-completion.json) | Notifica di conclusione della task RM-0008, commit osservato e stato Git; separata dalle attestazioni di esercizio e dal futuro freeze G0. |
| [inventory-data.md](inventory-data.md) | Store, writer TurnLog, migrazioni, producer/consumer ed esclusioni da ricontrollare sul commit finale. |
| [inventory-security.md](inventory-security.md) | Manifest legacy, consumer sensibili, call graph F6, helper amministrativo e gruppi di lavoro candidati. |
| [birth-contract.md](birth-contract.md) | Divergenza fra main/RM-0008/release, interfacce mancanti e proposta di contratto ancora da concordare. |
| [preparation-analysis.md](preparation-analysis.md) | Otto questioni tecniche del coordinatore, direzioni proposte e criteri di test da congelare. |
| [analysis-review.md](analysis-review.md) | Contestazione indipendente dell'analisi preparatoria; non review di approvazione G0.8. |
| [test-lab.md](test-lab.md) | Laboratorio di soli archivi sintetici, comportamento transazionale e limiti. |
| [preparation-review.md](preparation-review.md) | Review iniziale: i test verdi non intercettavano due falsi esiti positivi. |
| [preparation-review-followup.md](preparation-review-followup.md) | Verifica delle correzioni e del terzo caso trovato durante il riesame; digest dei quattro file di preparazione. |
| [verification.json](verification.json) | Esito finale del coordinatore e digest degli artefatti consegnati. Non certifica RM-0008, FS-A, FS-B o runtime RM-0009. |

I riferimenti a righe/commit/release nei rapporti identificano una fotografia,
non lo stato corrente dell'installazione. I conteggi derivano dalle scansioni
documentate; le esclusioni e gli errori di accesso impediscono di dichiarare
l'inventario completo. Nessun dato personale o database di produzione è stato
aperto per queste attività.

## Strumenti preparatori

File, tutti separati dal runtime:

- `internal/tools/rm0009_plan_check.py`;
- `internal/tools/rm0009_test_lab.py`;
- `tests/internal/test_rm0009_plan_check.py`;
- `tests/internal/test_rm0009_test_lab.py`.

Il checker legge solo l'Appendice D, rileva cicli, dipendenze mancanti o
vietate, prerequisiti remoti spostati a valle e gruppi di conversione non
collegati alla barriera. Il formato è ristretto: tabelle del piano, intestazioni
e delimitatori di codice con al massimo tre spazi iniziali. Non è un parser
Markdown generalista né un verificatore di tutti i requisiti della roadmap.

Dal checkout `/opt/metnos`, controllo del documento in bozza:

```bash
/opt/metnos/.venv/bin/python -B internal/tools/rm0009_plan_check.py \
  internal/roadmap/RM-0009-crescita-allineata-delle-capacita.md
```

L'uscita attesa attuale è `valid=true`, `concrete=false`, 101 unità di base e
due template `.N` ancora presenti. Non significa che i contratti siano
approvati o che G0.6 sia chiuso.

`--require-concrete` richiede anche `--expansion-inventory <file.json>`. Quel
file deve contenere esattamente le chiavi `D-FS-A.3` e `D-FS-B.3`, ognuna con
l'elenco completo degli ID concreti generati dall'inventario finale. La CLI
ne registra il digest. Una lista vuota afferma esplicitamente che non vi sono
gruppi da assegnare: il checker verifica la corrispondenza degli ID, **non** la
veridicità di quella ricognizione. Non è stato creato un inventario definitivo
per aggirare i template. Il documento corrente deve fallire la modalità
concreta. Exit code: 0 controllo riuscito, 1 violazione, 2 lettura/formato non
utilizzabili; lo scope è sempre limitato alle dipendenze del piano.

### Eseguire i test senza usare configurazione/dati installati

Il bootstrap pytest del repository prepara la propria installazione effimera.
Le due sorgenti sotto sono intenzionalmente vuote: nessuna copia di dati o
configurazioni reali deve diventare una fixture.

```bash
rm0009_test_sources="$(mktemp -d /tmp/metnos-rm0009-sources-XXXXXXXX)"
METNOS_USER_DATA="$rm0009_test_sources/data" \
METNOS_USER_CONFIG="$rm0009_test_sources/config" \
/opt/metnos/.venv/bin/python -m pytest -q \
  tests/internal/test_rm0009_plan_check.py \
  tests/internal/test_rm0009_test_lab.py
```

Atteso: **62 test, 62 passati, nessuno saltato** (48 checker, 14 laboratorio).
Non eseguire per analogia i runner legacy, suite Birth reali o suite generiche
non ispezionate. Le prove esistenti di RM-0008 elencate nei report sono
candidati al riuso futuro, non prove eseguite da questo lavoro.

### Usare il laboratorio in un test fidato

```python
from internal.tools.rm0009_test_lab import LabStore, temporary_lab

with temporary_lab() as lab:
    overrides = lab.environment()  # solo percorsi, non muta os.environ
    with lab.connection(LabStore.GOVERNANCE) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE synthetic_probe (id INTEGER PRIMARY KEY)")
        db.execute("INSERT INTO synthetic_probe VALUES (1)")
        db.commit()
    with lab.connection(LabStore.TARGET) as independent_db:
        assert independent_db.execute("PRAGMA foreign_keys").fetchone() == (1,)
```

Ogni laboratorio crea directory private `0700`, due utenti fittizi e due
database separati con WAL e foreign key. Ogni connessione annulla la propria
transazione pendente alla chiusura; non apre automaticamente BEGIN e non
committa per conto del test. Alla fine del contesto elimina la propria radice,
anche su eccezione ordinaria. Non sono certificate pulizia dopo kill del
processo, comportamento Windows o protezione contro codice ostile.

Questo è isolamento degli **archivi di prova**, non sandbox di sistema:
nessun blocco rete/processi/credenziali è dichiarato. Gli override non sono
l'ambiente certificato del runtime; non forniscono HOME/USERPROFILE e non
garantiscono che un consumer arbitrario ignori altri percorsi. Le protezioni
statiche contro alias servono a intercettare errori di fixture, non race.

## Ripartenza dopo RM-0008

La task RM-0008 osservata è `Birth gate review`
(`01a0870e-0fe7-7713-889a-260d91f29a09`, host
`remote-ssh-codex-managed:beelink`). Sono state usate soltanto letture e attese
di stato, senza inviarle istruzioni o interromperla. Il protocollo dettagliato
di acquisizione è in `birth-contract.md`, §11.

Conclusione della task e commit sono ora registrati in `upstream-completion.json`;
il controllo dei file tracciati è pulito. Restano un nuovo inventario e
l'attribuzione delle patch sul commit definitivo. Il materiale non tracciato
`BACHECA`/`internal/coordination` è stato conservato e non importato. Non eseguire
merge cumulativi dal checkout principale e non modificare il worktree
RM-0008. La disponibilità di attestazioni reali FS/F5 è una verifica distinta
dal freeze del contratto e non va introdotta come dipendenza nascosta di I1.1.

Non sono stati creati worktree/branch, eseguiti commit/push, riavviati servizi,
modificati manifest/firme o aperti store personali da questo lavoro.
