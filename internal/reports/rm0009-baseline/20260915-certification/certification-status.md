# RM-0008/F5-F6 — verifica di certificabilita per la ripresa RM-0009

Data: 15 settembre 2026.
Baseline RM-0009: `09a58c7cf33e2610ce1506b129db3b3d4a37b735`.
Prodotto RM-0008 incorporato: `5c1220ac25a1899b0d88aac5540fb0ab913db5ea`.

## Verdetto

**F5 e F6 non sono certificabili sulla baseline esaminata.**
Non manca soltanto un documento firmato: mancano collegamenti produttivi e
prove richieste dalla roadmap. I moduli esistenti e i loro test non possono
essere presentati come esercizio delle funzioni complete.

Il coordinatore e un revisore indipendente hanno raggiunto lo stesso esito
leggendo separatamente codice e requisiti. Nessuna soglia e stata ridotta;
nessuna chiave reale e stata usata; nessun attestato `EXT-RM0008-F5` e stato
creato, firmato o installato. Il presente rapporto, anche in formato JSON,
**non e un artefatto di autorizzazione**.

## Matrice requisito, evidenza, parte mancante

| Requisito | Evidenza nel codice | Parte mancante |
|---|---|---|
| Soglia reale per iniziare F5 | RM-0008 §10 e §23.4 richiedono almeno cinque ammissioni non simulate, due Producer, ricevute rilette, due cicli consecutivi e zero difetti aperti. `executor_birth_lifecycle.load_f5_activation` autentica un documento e verifica liste/conteggi. | Il lettore non ricostruisce quelle prove: serve il certificatore indipendente basato sulle evidenze effettive. Il numero di release o di test superati non e il numero di ammissioni qualificate. Qui la soglia non e stata ricostruita, non dichiarata inesistente. |
| Ciclo di vita e preesercizio | `LifecycleCoordinator` e `decide_preexercise` sono presenti e hanno prove isolate. | Nessun chiamante produttivo dei due componenti o di `load_f5_activation`. Occorre collegare la porta di pubblicazione/rilettura, caricatore, selezione e politica. |
| Epoche, memorie temporanee e migrazione | Lo store delle epoche ha identita esatte, CAS e migrazione senza perdita. L'identita di catalogo include gia generazione e lifecycle. | Questa copertura parziale non integra lo store F5 in ogni famiglia. `loader.py` conserva sostituzioni legacy per nome; `promoted_grace` resta letto da promoter e viste. La migrazione produttiva e le prove A/B restano necessarie. |
| Tentativi dei lavori durevoli | `DurableBirthAttemptGuard` verifica identita autenticata ed epoca nelle prove di modulo. | Non e collegato a ogni tentativo produttivo; manca la rilettura al confine effettivo prescritta da RM-0008 §23.4. |
| Riscontro e quarantena | La receipt di esecuzione arriva a dispatch/StepLog; le funzioni di feedback hanno test di CAS e ripresa. | `apply_negative_feedback`/`apply_step_negative_feedback` non hanno il collegamento produttivo al ciclo F5. I callback fittizi dei test non provano pubblicazione e rilettura reali. |
| Conservazione F6 | Grafo, marcatura, CAS, coda e ricevuta minima firmata hanno primitive e test. | `PRODUCTIVE_OWNER_ADAPTERS = OwnerAdapterRegistry(())` e vuoto. Mancano proprietari concreti, raccolta completa, esecuzione periodica e cancellazione/riconciliazione reali. Nessuna cancellazione produttiva e autorizzata da questo rapporto. |
| Prove finali | RM-0008 §§15-17 e §23.6 richiedono suite Linux/Windows, due cicli di routing, prova reale controllata e allineamento documentale/distribuzione. | Questa consegna esegue soltanto prove isolate Linux e controlli statici; non prova Windows, riavvio, nuova installazione, routing reale o cancellazione recuperabile. |
| Prova consumabile da RM-0009 | D-X0.1 e D.1-ter descrivono `EXT-RM0008-F5`. | La prova va prodotta soltanto dopo il completamento effettivo. I campi di revisione di una AdmissionReceipt e i digest del deployment non ne sono un equivalente. Lo schema firmato e il consumer richiedono il contratto comune G0.3. |

Riferimenti di codice sulla baseline:

- `runtime/executor_birth_lifecycle.py:97`: lettore e soglia del certificato;
  `:177`: coordinatore del ciclo.
- `runtime/executor_birth_preexercise.py:101`: decisore puro.
- `runtime/executor_birth_epoch_store.py:1`: integrazione non produttiva;
  `runtime/loader.py:1388`: sostituzioni legacy per nome.
- `runtime/executor_birth_durable_guard.py:43`: guardia del tentativo.
- `runtime/executor_birth_feedback.py:364` e `:409`: applicatori del feedback.
- `runtime/executor_birth_retention_integration.py:106`: registro vuoto.
- `runtime/executor_birth_retention.py:693`: sweep con proprietario esterno.
- `runtime/executor_birth_receipts.py:145`: revisione della singola ammissione,
  non certificazione della fase F5.
- `runtime/executor_birth_admin_preflight.py:761` e `:3444`: schema chiuso
  del descrittore e sua verifica.

Il revisore ha esaminato il commit RM-0008 `5c1220ac`; il coordinatore ha
verificato che runtime, executor, client, installer e test di prodotto del
ramo `09a58c7` non differiscono da quel commit. Le aggiunte RM-0009 sono
preparatori e documentazione, non collegamenti F5/F6 nascosti.

## Prove eseguite dal coordinatore

La suite selezionata passa **244 test**, zero fallimenti e zero test saltati:
137 test dei preparatori RM-0009 e 107 delle fondamenta RM-0008. Le ultime
due famiglie aggiunte coprono la politica di preesercizio e il formato
portabile della conservazione; eseguite su Linux non certificano Windows.
Il comando esatto, i file e i digest sono in `verification.json`.

Passano anche:

- `scripts/check_contract_boundary_policy.py`: allineamento delle regioni
  generate del verificatore amministrativo;
- `runtime/contract_boundary_guard.py --birth-closed`: politica statica del
  confine Birth e censimento sorgente corrente.

Questi controlli non ricostruiscono la soglia di F5 ne esercitano il ciclo
di vita o il grafo di conservazione in produzione.

Come prova negativa del legame fra inventario e codice, il vecchio inventario
dati su `1c308922` e stato ricontrollato contro il nuovo checkout: rifiutato
correttamente con nove differenze di digest. Non e una regressione del prodotto;
dimostra perche il precedente verde non poteva essere trasferito alla nuova
base senza ricalcolo.

L'ambiente di prova usa percorsi dati/configurazione inizialmente vuoti sotto
`/tmp`; il bootstrap della suite prepara archivi e chiavi effimeri. Nessun
database installato, credenziale, chiave privata reale o servizio viene usato
per costruire un esito positivo. Nessuna pubblicazione, riattestazione delle
generazioni correnti, modifica di autorita, arresto o riavvio e stato eseguito.

## Sequenza necessaria per arrivare alla certificazione

Seguire i gruppi gia prescritti da RM-0008 §23.6, senza abbassare i criteri:

1. gruppo 8: certificatore delle evidenze e migrazione dello stato per nome;
2. gruppo 9: integrazione reale di lifecycle, epoche, preesercizio, cache,
   tentativi durevoli e feedback, con prove di arresto/ripresa e CAS;
3. gruppo 10: proprietari F6 completi, prima osservazione senza cancellare,
   poi prova di cancellazione predisposta, controllata e recuperabile;
4. gruppo 11: verifica completa su entrambe le piattaforme, cicli reali,
   documentazione e distribuzione; soltanto dopo emettere la prova valida.

La richiesta di certificazione non e interpretata come permesso di inventare
prove mancanti. Dopo questo esito Roberto ha autorizzato esplicitamente:
«Completa anche RM0008 F5/F6». Ha poi richiesto di fermarsi alla loro
conclusione per una revisione esterna, prima di riprendere RM-0009.
Il coordinatore assume quindi anche quel completamento nella copia isolata;
non sono autorizzati nuovi bypass, attestati privi di evidenza o cambiamenti
di autorita non previsti. La transizione F4 gia verificata l'8 settembre
non va ripetuta (handover `internal/design/handover_rm0008_verifica_8_9_2026.md`).
Questa fotografia precede il nuovo codice F5/F6 e non ne anticipa l'esito.
RM-0009 conserva i requisiti G0.6-G0.10 e D-X0.1/FS-A/FS-B applicabili;
la sua implementazione resta sospesa fino al successivo via libera di Roberto.
