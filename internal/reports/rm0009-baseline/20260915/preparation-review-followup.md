# RM-0009 — Verifica indipendente delle correzioni preparatorie

Data: 15 settembre 2026. Revisore indipendente: `rm0009_inventory_security`.
Ambito limitato ai quattro file già revisionati in `preparation-review.md`; nessun edit a tali file.

## Esito

**Tutti e tre i falsi verdi riprodotti e il difetto delle fence risultano corretti. Nessun rilievo aperto nel perimetro della verifica mirata.** La prima verifica di questa consegna aveva trovato un nuovo P2 sul template annidato; il coordinatore lo ha riprodotto con un test rosso e corretto. La verifica indipendente finale passa con **62 test: 48 checker e 14 laboratorio**, nessun salto o fallimento. Le sezioni seguenti conservano riproduzione e chiusura del nuovo caso.

Il laboratorio corregge gli errori statici di alias verificati e documenta correttamente il confine delle transazioni. Rimane uno strumento per fixture fidate: non è sandbox, protezione da race o certificazione del runtime. Questa review non è G0.8, non congela il piano e non attesta compatibilità con il futuro commit RM-0008.

## Riproduzioni originarie ripetute

| Caso | Risultato iniziale | Risultato corretto osservato |
|---|---|---|
| Eliminare entrambi i template senza inventario | `valid=true, concrete=true` | `valid=false, concrete=false`; `expansion_inventory_required`, `missing_expansion_family` |
| Spostare FS-A/FS-B/X0 dal remoto 4a al solo 4e | `valid=true` | `valid=false`; antenati mancanti segnalati sui nodi 4a, 4b, 4c, 4d |
| Fence aperta a fine documento | `valid=true` | `PlanFormatError(unclosed_fence)` |
| Symlink del DB verso un secondo archivio sintetico temporaneo | scrittura fuori radice lab | `ValueError(invalid lab database)`; database esterno sintetico byte-identico |
| DDL senza BEGIN | persisteva senza distinzione nel contratto | comportamento esplicitamente documentato e testato: persiste solo DDL fuori transazione; DDL dentro BEGIN annullato |

Le nuove prove esistenti coprono inoltre hardlink del database, data directory sostituita, oggetto con data esterna, riuso dopo cleanup, eccezione con rollback/chiusura, laboratori distinti, inventario assente/invalido/vuoto esplicito, membri mancanti/estranei, JSON null/chiavi duplicate, fence lunghe e prerequisiti intermedi remoti.

## P2-F01 — Componente N interno al nome: riprodotto e chiuso

File: `internal/tools/rm0009_plan_check.py`, funzioni `_is_expansion` e `validate_plan`, costruzione di `templates`/`members`.

`_is_expansion()` esclude ogni ID con un componente `N`, ma la lista `templates` raccoglie soltanto i nomi che terminano con `.N`. Di conseguenza un ID come `D-FS-A.3.N.001` è valido per `_ID`, ma non viene riconosciuto né come template né come espansione concreta. Con un inventario esplicitamente vuoto il checker può dichiarare il piano concreto pur contenendo questo lavoro non inventariato e non unito alla barriera.

Riproduzione eseguita solo in memoria:

```python
from pathlib import Path
from internal.tools import rm0009_plan_check as c
text = Path('internal/roadmap/RM-0009-crescita-allineata-delle-capacita.md').read_text()
g = c.parse_plan(text)
del g['D-FS-A.3.N']
del g['D-FS-B.3.N']
g['D-FS-A.3.N.001'] = ('D-FS-A.2',)
r = c.validate_plan(
    g,
    require_concrete=True,
    expected_expansions={'D-FS-A.3': [], 'D-FS-B.3': []},
)
print(r['valid'], r['concrete'], r['errors'])
```

Osservato sulla versione intermedia digestata sotto: `True True []`.

Correzione minima raccomandata: ogni ID sotto le due famiglie deve essere classificato esaustivamente come barriera, template oppure gruppo concreto, oppure rifiutato. Un componente `N` non terminale non può scomparire dal confronto. Aggiungere il caso sopra e la variante `D-FS-B.3.001.N.002`. Non serve imporre un numero hardcoded di gruppi e non va vietato l'inventario vuoto esplicito quando il grafo non contiene effettivamente lavoro di conversione.

Questo è un falso verde del preparatore sul formato del piano; non è una vulnerabilità runtime. La gravità P2 riguarda l'affidabilità della modalità `--require-concrete` per il successivo incarico.

**Chiusura verificata:** il checker ora riconosce `N` in qualunque segmento separato da punto nell'ID; il nuovo test `test_nested_template_cannot_hide_between_inventory_and_template_checks` conserva la regressione. Ripetute indipendentemente entrambe le varianti `D-FS-A.3.N.001` e `D-FS-B.3.001.N.002`: restituiscono `valid=False`, `concrete=False`, il nome esatto in `templates` e `unexpanded_template`. Nessun nodo residuo risulta più concreto in questi casi. La suite finale passa 62/62.

## Verifiche aggiuntive riuscite

1. Creato in memoria un piano con una espansione concreta per famiglia, collegata alla propria barriera; la CLI con inventario esatto restituisce exit 0, `valid=true`, `concrete=true`.
2. Scritto quel piano come CRLF e un inventario JSON indentato in una directory temporanea propria: entrambi i digest coincidono con SHA-256 dei byte effettivi. L'inventario resta immutato dopo la lettura.
3. Ripetuto l'alias originario verso un database esclusivamente sintetico: rifiuto prima della connessione e byte esterni invariati.
4. Ripetuto il confine DDL: `CREATE TABLE persisted` senza BEGIN resta; `CREATE TABLE rolledback` dopo BEGIN scompare alla chiusura. Nessuna falsa promessa di rollback dello schema fuori transazione.

Non viene trasformata `expansion_inventory_sha256` in una firma o approvazione: il digest identifica l'input dichiarato dal chiamante. Il rapporto conserva `scope=plan_dependencies_only`; l'uguaglianza con un inventario esplicitamente vuoto non dimostra che il mondo reale sia privo di consumer o manifest da convertire.

I controlli lab su DB/data sono preventivi contro errori statici di fixture. Non difendono da sostituzioni concorrenti dello stesso processo o da una ricostruzione arbitraria di tutti i percorsi da parte di codice fidato; questo limite è ora dichiarato nel metodo `connection()` e non costituisce una nuova pretesa di isolamento.

## Esecuzione e baseline

Comando esatto autorizzato, con le radici source vuote indicate dal coordinatore:

```bash
METNOS_USER_DATA=/tmp/metnos-rm0009-checks-qJn9f3/data METNOS_USER_CONFIG=/tmp/metnos-rm0009-checks-qJn9f3/config .venv/bin/python -m pytest -q tests/internal/test_rm0009_plan_check.py tests/internal/test_rm0009_test_lab.py
```

Risultati: prima verifica **61 passed in 0.24s**; dopo la correzione P2-F01, stessa suite esatta **62 passed in 0.44s**. Nessun runtime, rete, servizio, test legacy o codice candidato eseguito. Tutte le riproduzioni aggiuntive hanno usato memoria e directory temporanee con nomi dedicati, eliminate dal rispettivo context manager.

Baseline intermedia sulla quale è stato riprodotto P2-F01:

| File revisionato | SHA-256 |
|---|---|
| `internal/tools/rm0009_plan_check.py` | `58d18b10e01bc54713d3133f4b4004a59fcd22f7883d626d353b062c8b5db288` |
| `internal/tools/rm0009_test_lab.py` | `a6f88a4327e47f5fb13fb240c374508b6f55e0846ab42b5d96e3590b6025974f` |
| `tests/internal/test_rm0009_plan_check.py` | `8739cca17932759ec074387c39b138d7ff7c6492d68d5807142451b340c629c5` |
| `tests/internal/test_rm0009_test_lab.py` | `7eeea5a21bd5fcff4a27dc1fce645c22e0479406866bcfcc081f9f7fce0314a3` |

Baseline finale verificata dopo la chiusura P2-F01:

| File revisionato | SHA-256 finale |
|---|---|
| `internal/tools/rm0009_plan_check.py` | `7fd47fae9bb83336732c6eeb8889a74dc7a29f0fe6b278a704d3869e624574a6` |
| `internal/tools/rm0009_test_lab.py` | `a6f88a4327e47f5fb13fb240c374508b6f55e0846ab42b5d96e3590b6025974f` |
| `tests/internal/test_rm0009_plan_check.py` | `19a73a6f9aad574eaf65e7213210162e9a254e6021336794aa7f5281444b8e26` |
| `tests/internal/test_rm0009_test_lab.py` | `7eeea5a21bd5fcff4a27dc1fce645c22e0479406866bcfcc081f9f7fce0314a3` |

Il solo file modificato dal revisore è questo rapporto. Esito limitato ai preparatori e ai digest finali sopra: nessuna attestazione di G0.8, approvazione, isolamento del codice candidato o compatibilità futura RM-0008.
