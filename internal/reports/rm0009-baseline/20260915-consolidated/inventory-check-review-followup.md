# RM-0009 — follow-up adversarial review del source-binding checker

Data: 2026-09-15  
Oggetto esclusivo: `internal/tools/rm0009_inventory_check.py` e `tests/internal/test_rm0009_inventory_check.py` nello stato corrente del worktree `rm0009-development`.

## Esito

I tre rilievi della prima revisione risultano sostanzialmente risolti. Non rimangono difetti P1/P2 che consentano al **rapporto prodotto dal checker** di attestare più del binding dei byte dichiarati. Il verdetto resta intenzionalmente preliminare: non prova completezza dell'inventario, semantica del payload, modalità dei file, sandbox, idoneità operativa, release o gate D-G0.5.

Rimane una decisione contrattuale P3 da rendere esplicita prima di congelare l'interfaccia:

- il rapporto emette `scope`, ma `_REPORT_FIELDS` non riserva l'omonima chiave dell'inventario. Un inventario può quindi contenere, per esempio, un proprio `scope` ampio e risultare valido; il checker lo elenca correttamente in `unchecked_inventory_fields`, mantiene `inventory_payload_validated: false` e sostituisce nel proprio rapporto il valore con `inventory_source_content_binding_only`. Non è un falso verde del rapporto, purché i consumatori non uniscano o reinterpretino il payload grezzo. Va congelata una delle due regole: riservare anche `scope`, oppure dichiarare normativamente che ogni `scope` sorgente è solo payload specialistico non verificato. `schema_version` è già trattato e testato secondo la seconda regola.
- la matrice parametrica delle chiavi riservate non include `unchecked_inventory_fields`, benché il codice lo riservi. È un piccolo vuoto di regressione, non un difetto attuale: aggiungere quel caso renderebbe completa la prova della policy implementata.

## Riesame dei rilievi originari

| Rilievo precedente | Stato | Evidenza nel sorgente e nelle prove |
|---|---|---|
| Un tree-ish di 40 cifre poteva essere accettato come commit | Risolto | Prima di `ls-tree`, `GitBaseline` esegue `git cat-file -t` e richiede esattamente `commit`. Le prove respingono tree/blob/tag simulati prima della lettura e un tree OID reale. |
| Il digest non distingueva 0644 da 0755 senza dichiararlo | Risolto per restrizione del contratto | Il rapporto dichiara `scope: inventory_source_content_binding_only` e `checks_file_mode: false`; i casi 0644 e 0755 provano esplicitamente che il successo riguarda i byte, non il mode. Non è stata introdotta una falsa attestazione di modalità. |
| Campi arbitrari potevano accompagnare `valid: true` senza separazione fra binding e semantica | Risolto nel rapporto, con la decisione P3 sopra | Le principali chiavi di attestazione sono rifiutate; gli altri campi sono nominati in `unchecked_inventory_fields` e il rapporto fissa `inventory_payload_validated: false`, `certifies_completeness: false` e `authorizes_implementation: false`. |

Sono inoltre verificati i due hardening aggiunti: numeri JSON non finiti anche per overflow (`1e999` e `-1e999`) vengono respinti; parsing e `inventory_sha256` derivano dalla stessa singola lettura dei byte.

## Prova eseguita e binding della revisione

Sono stati eseguiti esclusivamente i tre file-test autorizzati, con `METNOS_USER_CONFIG` e `METNOS_USER_DATA` diretti a directory nuove e vuote sotto `/tmp`:

```text
135 passed in 0.40s
```

Byte riesaminati:

```text
440f31450e7ae1414027e68b6393c31fc273d28ee26c3d790dfbf21bf8f9bfb3  internal/tools/rm0009_inventory_check.py
8ceea69b32a28b2aca8bb31a380feede5c33c3659f9ad367aaf71dd03d7cdc33  tests/internal/test_rm0009_inventory_check.py
```

Il worktree aveva `HEAD` `1c308922839f7659a3cf54d988f995bba0f215d6`, ma entrambi i file riesaminati — come gli altri preparatori coinvolti nella suite — risultavano **untracked**. Pertanto i digest sopra legano questa revisione allo stato letto, mentre `HEAD` identifica soltanto la base Git usata dalle prove: i 135 verdi non sono prova che il checker sia committed, distribuito o presente in una release.

## Confine dell'attestazione

Questa revisione consente di congelare il checker come verificatore locale e fail-closed della corrispondenza fra digest dichiarati, blob del commit atteso e byte del checkout trusted/quiescente. Non chiude D-G0.5 e non sostituisce inventari specialistici, loro validatori, verifica di completezza, prove operative o attestazioni di release.
