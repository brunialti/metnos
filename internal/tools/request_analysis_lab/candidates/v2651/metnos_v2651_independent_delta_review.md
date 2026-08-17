# V26.5.1 — review indipendente del delta corrente

Data: 2026-08-09  
Verdetto: **STATIC BLOCK**

Il blocco è circoscritto alla policy di esecuzione del manifest. Non ho trovato
una regressione nel grafo compatto: schema, adapter, validator, mutazioni,
multi-azione/multi-dominio e audit anti-contaminazione riproducono i risultati
archiviati. V26.5.1 non può però diventare freeze o ricevere un wrapper live
finché un artefatto dichiarato non importabile può comunque essere eseguito.

Il report machine-readable è
`metnos_v2651_independent_delta_review.json`, SHA-256
`d445a84e747868b0be7d13211d350b5d7d8099162eef694e1bc89e413c9be304`.

## Perimetro

Review soltanto offline:

- zero rete e zero chiamate modello;
- nessun output live letto;
- nessun freeze o gate creato;
- nessun artefatto candidato modificato;
- esecuzioni con `-B` e bytecode disabilitato;
- hash dei file candidati invariati prima/dopo i probe.

Sono stati aggiunti soltanto questo report, il gemello JSON e gli aggiornamenti
documentali richiesti.

## Riproduzione indipendente

| Controllo | Esito fresco | SHA-256 stdout/risultato |
|---|---:|---|
| manifest/pre-import | 38/38, 34 artefatti | `119c9219d32fef1a9fbf48f667255033311397b23b3ba7127d6f4804502d5a52` |
| validator self-contained | 11/11 | `1b2021d9f50efa6e7867fe872c5eb4a378e9f714ba9a3d74f52ae35de0fcbda3` |
| candidate core | 96/96 | `d1476a1660ed81dd52cf3ef7b24ab9a679d107d62b54e2bb083d123efd696746` |
| suite compact-native | 106/106 | `3306d299b18726da1b903643d9c85763c05ec57680259c2ab05ddf0aa8bdb68f` |
| audit 109+34+70 | 15/15 | `50e1c5931fe60bfb1007bf5624b1f836214c43fab98194350b66921549487c92` |

I generatori riproducono esattamente i byte archiviati di schema, prompt,
validator, mutation matrix, fixture, corpus e auditor.

## Blocker B1 — `role/import_allowed` non governa l'esecuzione

Il manifest corrente è corretto, ma la policy dichiarata non è applicata
fail-closed:

1. `verified_manifest()` controlla `role/import_allowed` soltanto per l'entry
   del core stesso;
2. `_source_module()` compila qualunque path ricevuto se hash e presenza
   coincidono, senza controllarne ruolo o permesso;
3. `CandidateCore.load()` passa direttamente i path hardcoded di adapter e
   validator;
4. il verifier considera valida una entry quando
   `import_allowed is False` **oppure** il ruolo è importabile. Non richiede
   che il valore sia booleano e non confronta la policy col consumer reale.

Probe causale in memoria, senza cambiare il manifest su disco:

- adapter riclassificato
  `historical_source_only / import_allowed=false`;
- `verified_manifest()`: accetta;
- verifier: ancora 38/38;
- `_source_module()`: esegue comunque l'adapter;
- anche `import_allowed="truthy-not-bool"` viene accettato da entrambi.

Il probe è `/tmp/metnos_v2651_independent_policy_probe.py`, SHA-256
`7022be4546e4b6f086cd8fa99d57c86c5626e91007d3ab3c35993bc241cad86a`;
output SHA-256
`84ea1712b90360b1ac5dbb20e545352fc54b8ccec4cbc86f33b24bb045cd5111`.

Questo è un bypass reale del confine dichiarato, non il normale problema del
self-pin. Blocca V26.5.1 prima di wrapper, freeze, gate o live.

## Controlli superati

### Byte, replay e derivazione

- tutti i 34 path correnti sono relativi, unici, presenti e hash-corretti;
- il core verifica l'intero manifest corrente prima di compilare adapter e
  validator e ricalcola l'hash dei singoli consumatori;
- schema e prompt rigenerati hanno rispettivamente SHA
  `76cd30411704c780e5866fd165f945bd3e773f0dab1b359e875d41f6eb54a921`
  e
  `893db06f910acd48f8929251a89afaaaf630632b90589e0863948792643a7418`;
- il validator estratto rigenera SHA
  `b19b3aa781ab3ac44b973db2aed3f6da889c29703addfbdf8251a5c60ae97656`.

### Query intatta e Unicode

La richiesta resta identica dopo costruzione e parse del body JSON. Gli span
sono source-aligned su probe con combining mark, CJK, Thai, arabo, emoji ZWJ,
apostrofo/trattino e CRLF. Non viene applicata normalizzazione lessicale o
Unicode alla stringa originale.

### Pipeline semantica

I sei positivi attraversano realmente:

`schema compatto -> adapter canonico -> validator con registry frozen`.

Passano direct, fan-out multi-azione, multi-dominio, riuso dell'output,
ambiguità tipizzata e copertura mixed. Un controllo con segmenti prodotti da
una richiesta reale attraversa lo stesso percorso e ricostruisce quattro
atomi.

La suite 106/106 censisce:

- 56 mutazioni V26.4 ancora necessarie;
- 3 mutazioni obsolete perché riguardavano soltanto `clause_id` derivato;
- 9 sostituzioni compact-native per ordinal e campi rimossi;
- 16 negativi nativi;
- 6 positivi.

Il core usa soltanto il registry frozen SHA
`448c058d805e141c871580253e815849b24a9999d98c505cab970fcd55d1e46f`.
Le sorgenti storiche/sintetiche restano evidence non importabile e non
installano un registry nel core.

### Audit e assenza di trasporto

Le quattro fonti canoniche sono presenti nel manifest come
`audit_source/import_allowed=false`. La rigenerazione conserva contenuto e
ordine delle 109+34+70 query e riproduce il corpus query-only SHA
`237aae0006fd6926fc20e5f31aa50a4aa0bd8a2d10e7162a9ebe8424c6558149`.
Audit: 15/15, zero righe surface/mixed, zero query intere e zero overlap di
almeno tre token.

Core, adapter e validator non importano librerie di trasporto e non contengono
endpoint di completion. Tutti i replay riportano zero rete/modello.

## Confini da non confondere col blocker

### Manifest, report e trust root

Un manifest non può contenere il proprio hash né questo report successivo
senza circolarità. Non è un'autorità autonoma. Dopo la correzione B1, un
wrapper/freeze **separato e source-only** dovrà verificare prima
dell'esecuzione almeno manifest, core, verifier, review e ambiente. Questa
esigenza esterna è prevista; non giustifica però l'attuale bypass della policy
interna.

### Bytecode

Il core compila adapter e validator dai byte sorgente verificati. L'audit hook
ha osservato zero letture di `.pyc` nel repository e i replay `-B` non hanno
modificato file candidati. Erano già presenti nove cache ignorate sotto
`v2651/__pycache__`; sono rimaste invariate e non sono state lette. Prima del
freeze conviene usare cache esterna o directory pulita, senza scambiare
presenza fisica e lettura.

### API segmenti

`evaluate_segments()` accetta per design segmenti già fidati e può validare
record contenenti soli id, senza richiesta originale. Il futuro wrapper non
deve esporre questa API come ingresso esterno: deve usare
`evaluate(original_request, frame)` o legare esplicitamente i segmenti al body
costruito dal core.

## Correzione minima V26.5.2

1. Richiedere `import_allowed` booleano esatto per ogni entry.
2. Prima di ogni `compile/exec`, imporre la policy del consumer: adapter e
   validator devono essere `runtime_input/import_allowed=true`.
3. Separare accessor dati e accessor eseguibile; un'entry non importabile non
   deve mai raggiungere `compile/exec`.
4. Allineare il verifier alla stessa mappa path/ruolo/permesso e aggiungere
   mutazioni per `false`, campo assente, stringa truthy e role swap.
5. Ripetere tutti i replay offline e ottenere una nuova review indipendente.
6. Solo dopo, creare il wrapper esterno che pinna manifest+review prima di
   caricare il core. Il trasporto resta un componente successivo e separato.

Fino a quella review: **nessun freeze, gate, rete, inferenza o credito live**.
