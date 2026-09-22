# RM-0009 — revisione avversariale del controllo di legame sorgente

Data: 2026-09-15  
Oggetto esclusivo:
`internal/tools/rm0009_inventory_check.py` e
`tests/internal/test_rm0009_inventory_check.py` nel worktree
`/opt/metnos/.claude/worktrees/rm0009-development`.  
Perimetro: legame fra inventario, oggetto Git e byte del checkout; non
completezza D-G0.5, sandbox, firma, runtime o autorizzazione a implementare.

## Esito

Il preparatore è prudente sul significato del proprio verdetto e nega già
traversal, glob, componenti `.git`, symlink, hardlink, file non regolari,
duplicati JSON, digest malformati e divergenze fra blob Git, digest dichiarato
e checkout.

Non è ancora adatto a dichiarare che `source_commit` sia davvero un commit:
esiste un falso verde bloccante sul tipo dell'oggetto Git. Due ambiguità
ulteriori non falsificano i byte, ma possono allargare il significato attribuito
al report oltre ciò che il codice ha verificato.

## Rilievi

### [P1] `source_commit` accetta un tree Git come se fosse un commit

**Causa concreta.** `GitBaseline.__init__` controlla soltanto la forma
`[0-9a-f]{40}`. `read_baseline` passa poi quel valore direttamente a
`git ls-tree`. Git accetta come primo argomento qualunque tree-ish, incluso
l'object ID di un tree puro; il codice non esegue un peel a `^{commit}` né
controlla il tipo con `cat-file`.

Sul checkout esaminato, `HEAD^{tree}` è
`c93825da501132189bb916fe57194004137cad07`: `git cat-file -t` lo classifica
`tree`, ma `git ls-tree` su quell'ID restituisce regolarmente il blob di
`runtime/change_intents.py`. Un inventario con quel valore sia in
`source_commit` sia in `--expected-commit`, e con il digest corretto del file,
può quindi ottenere `valid=true` e un report che lo chiama commit.

**Impatto.** Il binding perde autore, genitore, data e appartenenza alla storia
RM-0008; un tree con gli stessi file censiti può essere presentato come baseline
committed. Git trusted e checkout fermo non eliminano l'ambiguità di tipo.

**Correzione minima.** Inizializzare `GitBaseline` risolvendo una sola volta
`<id>^{commit}` con `git rev-parse --verify`, richiedere esattamente lo stesso
ID minuscolo fornito dal coordinatore e fallire prima di leggere file se il
tipo non è commit. Aggiungere casi per tree ID, blob ID, tag annotato, oggetto
inesistente e commit valido.

### [P2] Il bit eseguibile del checkout non è legato al commit

**Causa concreta.** `read_baseline` accetta correttamente soltanto mode Git
`100644` o `100755`, ma scarta il mode dopo la lettura. `read_checkout` verifica
solo file regolare e `st_nlink == 1`; non confronta il bit eseguibile del file
vivo con il mode del tree Git.

**Falso verde possibile.** Un file elencato come `100644` può diventare
eseguibile nel checkout, o viceversa, senza cambiare alcun byte. I due SHA-256
restano uguali e il controllo restituisce valido, sebbene lo stato eseguibile
non coincida col commit.

**Impatto.** Per un inventario di puro contenuto il comportamento è coerente,
ma non per un claim di sorgente eseguibile o checkout identico al commit. Gli
inventari RM-0009 possono includere script e strumenti, quindi la distinzione
deve essere esplicita.

**Correzione minima.** Conservare il mode dal record `ls-tree` e confrontarlo
con `stat.S_IXUSR|stat.S_IXGRP|stat.S_IXOTH` secondo la policy Git adottata;
in alternativa rinominare esplicitamente il campo/verdetto come
`content_bytes_binding_only` e proibire ogni claim sullo stato del checkout.
Testare entrambe le variazioni `100644 ↔ 100755`.

### [P2] Lo schema aperto accetta claim extra incompatibili col verdetto stretto

**Causa concreta.** `validate_inventory` legge soltanto `source_commit`,
`errors` e `input_sha256`, ma non rifiuta altre chiavi al livello superiore.
Per esempio un input può aggiungere
`"certifies_completeness": true`, `"authorizes_implementation": true` o
un secondo identificatore di baseline e risultare comunque valido. Il report
generato rimette correttamente i primi due valori a `false`, ma l'inventario
originario resta un envelope valido con affermazioni ignorate.

**Impatto.** Un consumatore che archivia insieme inventario e report può
attribuire al verde affermazioni che il controllo non ha letto. Non è una
collisione SHA-256, ma è ambiguità del payload e del claim.

**Correzione minima.** Rendere chiuso lo schema della versione 1, per esempio
con le sole chiavi `schema_version`, `source_commit`, `input_sha256`, `errors`
e un valore fisso di `scope`; oppure vietare almeno tutte le chiavi riservate
del report. Aggiungere casi con claim extra, chiave sconosciuta e versione
sconosciuta.

## Osservazioni non promosse a difetto

- Il controllo non verifica che HEAD coincida col commit atteso e non censisce
  file omessi. È coerente con `inventory_source_binding_only` e con
  `certifies_completeness=false`: ciascun path dichiarato è confrontato col
  blob del commit e coi byte vivi.
- La lettura del checkout usa controlli separati da `read_bytes`, quindi resta
  una finestra temporale. Il modulo la dichiara espressamente fuori perimetro e
  il presente incarico assume Git e checkout fidati e fermi.
- L'hash `inventory_sha256` viene calcolato con una seconda lettura del file.
  Sotto la stessa ipotesi di input fermo non è un difetto; se tale ipotesi
  verrà rimossa, caricamento e digest dovranno derivare dagli stessi byte.
- L'assenza di limiti su numero e dimensione dei file è un tema di budget, non
  un falso verde di source-binding nel perimetro trusted assegnato.

## Prove eseguite

Prima della prova sono stati letti integralmente il laboratorio sintetico e i
suoi test. Sono stati eseguiti soltanto:

```text
tests/internal/test_rm0009_inventory_check.py
tests/internal/test_rm0009_plan_check.py
tests/internal/test_rm0009_test_lab.py
```

con `METNOS_USER_CONFIG` e `METNOS_USER_DATA` puntati a nuove directory vuote
sotto `/tmp/rm0009-inventory-review.LMaqfJ`. Esito: **117 passed**. Nessun
runtime, servizio o dato reale è stato aperto. Il totale corrisponde al batch
dichiarato di 55 casi nuovi più 62 controlli precedenti.

Il verde conferma i casi presenti, ma non copre i tre rilievi sopra. Prima di
usare il preparatore come prova di commit-bound source binding va chiuso P1;
P2 deve essere risolto o ristretto esplicitamente nel contratto prima del
congelamento dello schema.
