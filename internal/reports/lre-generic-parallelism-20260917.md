# LRE: parallelismo generale e compatibilità F5 — 17 settembre 2026

## Esito produttivo aggiornato, 17/9 ore 15:50 Europe/Rome

**Release 63 ripristinata e LRE nuovamente in avanzamento. Release 64 costruita,
ma non selezionata: parallelismo a quattro e correzione della previsione non
sono in esercizio.** Le sezioni successive descrivono i collaudi e gli stati
storici, non un via libera al candidato attuale.

- Pausa cooperativa del job `wrk_4121cc6258c447759e94ccc6f8090e5c` a 277/967
  batch di analisi: nessun tentativo attivo, 1.245 risultati conservati e
  contabilità dei tentativi completa. `revisions.usage_complete` resta falso
  finché viene materializzato al completamento: non è il criterio della pausa.
  Il primo banco pretendeva erroneamente quel riepilogo; la verifica successiva
  ha letto tutti i fatti dei tentativi, senza scrivere il database.
- Piano SHA256 `a96bc10667200eb04d1b1f6f0637f206df147ff3725cc19e328ed9bf6c484421`
  e revisione `rev_d8d3982283a140bf993f2517731ad00d` invariati. Dopo il ripristino
  e la ripresa ordinaria è stato confermato il batch 278, alle 15:49:19:
  tutti i 1.245 riferimenti unità/risultato precedenti sono ancora presenti,
  più il risultato nuovo. Nessun job ricreato, indice azzerato o batch salvato
  ripetuto dalla manutenzione.
- Al controllo delle 15:50: 278/967 analisi, 1.246/1.935 batch complessivi,
  uno attivo, nessun batch fallito o in attenzione. Restano quattro esiti
  fotografici negativi e due tentativi tecnici già recuperati, entrambi
  `image_description_truncated`; nessun nuovo errore causato dalla ripresa.
  La previsione resta `n.a./uncertain_progress` nella release 63.
- HTTP, worker e Telegram attivi, zero riavvii automatici; console HTTP 200.
  Il turno informativo `3231f85c9de6457e` ha risposto senza executor. È una
  prova di raggiungibilità della chat, non una certificazione semantica del
  Tutor: la sua risposta suggerisce una stima recuperabile, mentre il difetto
  concreto della release 63 continua a impedirla in questo job.
- In quest'ultima cancellazione è stato rimosso **solo**
  `/mnt/nas_public/media/Immagini/2014/2014-03-14-1054.jpg`, dopo riscontro di
  percorso, dimensione e SHA256 nel risultato negativo. La copia di lavoro
  LRE è ancora presente per la ripresa, non è un backup permanente.

### Blocco emerso durante la transizione

Preparazione `aa1214ce`, esportazione di 1.806 file,
`542bf7220e164cb45a447fd76720d1562ea2128799a5c2c22dbc81a4f985c886`.
Release 64 firmata:
`sha256:f68ac65fe1fdaef21a0bb28d47d2c0809aa2706f62c045b05e5b03988fd3a458`.
Anteprima positiva: 107 contratti, **nessun cambiamento**. Questo non copriva
però la transizione del piano di ritiro amministrativo.

Il confronto dei due cataloghi firmati trova 39 vecchi punti d'ingresso nella
63 e 40 nella 64: unica aggiunta `legacy-install-operator-authority`, percorso
`install/operator_authority.py`, da `830e36ca`. Nessuna rimozione o modifica
degli altri 39. `_observe_previous_retirement_v2` richiede l'identità dei due
piani e rifiuta con `birth_transition_legacy_plan_changed`. La costante della
ricetta è allineata: **non** è il precedente errore d'impronta/fusione.

La barriera aveva già fermato il worker alle 15:40:14 e HTTP alle 15:40:20.
La selezione è rimasta 63, attestata indipendentemente prima del ripristino.
Ripristinata byte per byte la configurazione precedente, riavviato normalmente
`metnos.target`: HTTP e worker avviati alle 15:44:16. Job ripreso alle 15:45:31.
Nessuna forzatura delle firme o della catena, nessuna migrazione/attivazione F5,
nessuna variazione GPU. Il tentativo 64 resta non selezionato, da gestire con
il normale recupero del ciclo al prossimo candidato, non cancellando registri.

È stato aggiunto allo strumento di rilascio il confronto anticipato dei due
piani, prima di qualsiasi arresto, mantenendo anche il controllo autorevole
sotto lock. Cinque prove nuove; 216 prove del ciclo/ritiro superate. La nuova
guardia rifiuta anche i due cataloghi produttivi 63/64 letti in sola lettura e
accetta 63/63. **Previene il fermo, non autorizza il passaggio F5 incompatibile.**

Durante la costruzione erano inoltre emersi due lock temporaneamente occupati
e la maschera 077 ereditata dal lanciatore amministrativo: il costruttore
richiede directory pubbliche 0755. Corretta solo la cartella temporanea vuota
da esso appena creata e impostata 022 nel processo figlio. Nessun segreto o
directory dati è stato reso pubblico. Nessuna vecchia release è stata eliminata.

Evidenze private: `run-k2vptjwa` (foto), `run-gcyth69y` (pausa),
`run-9jzyxswb` (build), `run-1osip_f6` (contratti), `run-3lkt5_yg`
(transizione rifiutata), `run-u71pu1v9` (63 attestata), `run-eppza0an`
(servizi/configurazione ripristinati), `run-srlt5gnz` (ripresa),
`run-b6y08l9p` (nuovo batch e conservazione), `run-21dargbq` (stato finale),
`run-jay0g5qv` (HTTP/chat). Checkpoint operativo, non copia dell'archivio foto:
`/var/lib/metnos-admin/lre-rollout-20260917-6c99f9b9`.

**Prossimo passo:** F5 deve risolvere esplicitamente la compatibilità del piano
di ritiro, oppure Roberto deve scegliere un rilascio LRE separato. Non togliere
la nuova voce o il controllo per ottenere il verde; non ripetere il passaggio
del candidato 64 attuale e non fermare nuovamente LRE per sola diagnosi.

## Stato effettivo

**Aggiornamento 17/9, pomeriggio:** F5 fino a `7b56f414` è integrato nel ramo
combinato, commit `5fa19a79`; verificata la discendenza sia da F5 sia da LRE
`ab763e66`. Il blocco sulla ricetta descritto più sotto è storico e chiuso:
la causa reale era la regressione di fusione `0f922c5c`, che aveva perso il
lettore amministrativo e la costante già approvata; `d2c2fb6a` li ripristina.
Non è stata copiata un'impronta candidata per far passare il controllo.

Rimisura sull'albero combinato aggiornato: 365 prove mirate F5/rilascio/ripresa
superate; `rm0008_2b` 365 superate in sandbox più una prova di sottoprocesso
superata fuori sandbox (resta esclusa soltanto Windows). Suite LRE estesa:
917 superate, quattro profili di carico volontari esclusi. Include quattro
nuove regressioni sulla stima dopo recupero: un batch confermato dopo ritentativo
torna a contare una sola volta, mantenendo tempo perso e storico; consumi ignoti
e problemi ancora aperti continuano a negare la previsione. Il cambiamento è
solo nella lettura dei progressi, senza schema, contratti o budget nuovi.
61 prove mirate su progressi e contabilità superate. HTTP/console: 15 superate,
due prove Chromium non attivate; il primo tentativo HTTP in sandbox ha raggiunto
il limite di 180 secondi ed è stato ripetuto con successo su server temporanei
locali fuori sandbox. Nessun processo di collaudo orfano osservato.
I gruppi si sovrappongono e non vanno sommati come casi unici.

Evidenze: `/tmp/metnos-lre-final-recheck-20260917.XvyLFu/`;
`f5-targeted.xml`, `f5-subprocess.xml`, `lre-core-final.xml`, `eta-after.xml`,
`http-host.xml`. La prova di ripresa usa come precedente `f1ef7e5a`, non due
avvii dello stesso albero. Le guide IT/EN sono state pubblicate in modalità
esclusivamente statica: `3ee11a30.mykleos.pages.dev`; Tutor locale invariato.

Roberto ha autorizzato la pubblicazione con pausa ordinata e ripresa dello
stesso job, senza perdere i risultati. Preparazione e passaggio produttivo
restano da registrare: i collaudi dei sorgenti non certificano ancora
l'artefatto firmato. Nessuna attivazione o migrazione F5 è implicita.

Implementazione e prove isolate concluse; **non attivata in produzione**.
Nessuna pausa, cancellazione, riscrittura di piano, configurazione produttiva,
chiave, migrazione F5, rilascio applicativo o utilizzo della GPU in questa fase.

- Candidato LRE: `ab763e66`, ramo `codex/lre-backend-release`.
- Integrazione con F5 `985fbb38`: `211e9c4f`, ramo
  `codex/lre-general-parallel-f5`, directory stabile
  `/opt/metnos/.claude/worktrees/lre-general-parallel-f5`.
- Fusione della storia completa LRE in `f609608f`: soltanto due rapporti
  riallineati; codice, prove, installer e guide identici a `211e9c4f`.
- Baseline dei sorgenti prima della modifica: `f1ef7e5a`.
- Il TODO sulle priorità GPU preesistente rimane separato e non è incluso
  in questi commit.

## Soluzione riutilizzabile

Il pacchetto della capacità dichiara, tramite un risolutore registrato,
l'insieme verificato delle destinazioni mutabili di ciascun tentativo.
Il nucleo applica soltanto una regola generale: insiemi disgiunti possono
procedere, sovrapposizioni o prove assenti impongono esclusione seriale.
La prenotazione è atomica, limitata e liberata anche in caso di errore.
Una scrittura su directory esclude anche quelle sui suoi discendenti;
la prova include nomi lessicograficamente intercalati, come `/a-other`
fra `/a` e `/a/child`.

Non è un nuovo pianificatore: restano il gruppo centrale, le risorse,
le priorità, le dipendenze, i limiti del piano, le concessioni temporanee
e i numeri di tentativo esistenti. La protezione è per lo stesso executor
e scheduler di processo, non un blocco globale o distribuito del filesystem.
I nuovi fatti non diventano autorità firmata e non viaggiano verso dispositivi.

La funzione immagini è il primo consumatore, fuori dal nucleo generale:
verifica la ricevuta del gruppo e i checkpoint effettivi. Parti immutabili
e riuso della generazione precedente conservano le proprie protezioni.
Scoperta, riduzione e pubblicazione restano seriali.

Configurazione prevista, **non applicata**, nel `runtime.toml` privato:

```toml
[execution_resources]
cpu = 4
vlm = 4
```

Si legge creando un nuovo scheduler, non ridimensionando semafori attivi.
Variabili d'ambiente esistenti prioritarie, valori invalidi ridotti a uno,
valori predefiniti invariati. Sono quattro posti logici, non quattro core.
Le capacità `METNOS_DURABLE_RESOURCE_*` descrivono il singolo tentativo;
non vanno aumentate a quattro per avere quattro lavoratori.

Il beneficio CPU misurato sul campione resta circa +44,5% di rendimento,
non parità con la GPU. Non è ancora una misura di quattro executor completi
con modelli reali e non certifica qualità semantica identica delle descrizioni.

## Prove eseguite

| Insieme | Esito | Confine |
|---|---:|---|
| LRE, scheduler, isolamento, dominio immagini, motore e configurazione nell'albero combinato | 913 superate, 4 saltate | Archivi sintetici; comprende le prove di arresto reale già esistenti |
| Ripresa fra alberi, avanzamento parallelo, F5 inerte e guardia durevole | 43 superate | Prima fase su `f1ef7e5a`, seconda su `211e9c4f`; alcuni casi si sovrappongono alla riga precedente |
| API e console HTTP nell'albero combinato | 15 superate, 2 saltate | Server HTTP temporanei, nessuna chiamata a produzione |
| Lanciatore F5, punto d'ingresso, emittente, migrazione e inattività precedente alla migrazione | 137 superate | Albero F5 `985fbb38`; verifica delle correzioni `f5fadfcb` |
| Ricetta di rilascio nella baseline | 2 superate | Controllo positivo e rifiuto della costante obsoleta |
| Stessa ricetta nel candidato combinato | **1 superata, 1 fallita** | Blocco aperto, non escluso né aggirato |

La prova nuova `test_release_resume.py` usa due interpreti separati e
`RuntimeFactory`, concessioni, archivio SQLite, autorità delle sorgenti e
supervisione reali. La capacità di prova scrive sei file indipendenti:

1. Il processo precedente conferma due batch; ne rimangono quattro.
2. Un caso termina con SIGTERM fra tentativi; l'altro riceve SIGKILL durante
   il terzo tentativo, prima del suo effetto. La politica autorizza tre tentativi.
3. Il processo aggiornato riapre gli stessi archivi. La guardia F5 reale legge
   l'assenza del contrassegno nella directory di prova, senza simulare il verdetto.
4. I quattro batch restanti entrano realmente insieme; l'interrotto viene
   recuperato secondo concessione e politica, senza aggiornamenti SQL manuali.
5. Stesso piano byte per byte e stessa revisione; tutti i riferimenti precedenti
   sono conservati; sei risultati e sei file corretti; nessun batch confermato
   ripetuto, nessun tentativo ancora attivo.

Invocazione riproducibile, indicando un checkout della baseline:

```sh
METNOS_LRE_TEST_PREVIOUS_ROOT=/percorso/checkout-precedente \
  /percorso/venv/bin/python -m pytest -q \
  tests/runtime/durable_workloads/test_release_resume.py
```

Senza la variabile, la prova verifica il riavvio della stessa versione.
Questa evidenza riguarda **sorgenti e archivi sintetici**: non sostituisce
la verifica dei contratti del job reale, della distribuzione firmata,
dell'arresto dei servizi o del riavvio della macchina.

Durante la preparazione del nuovo banco sono stati corretti errori del banco
stesso: nome del campo unità, isolamento dei percorsi, metodo del servizio,
conto dell'inventario già sigillato, riserva per il traffico interattivo e
numero di tentativi. Non è stato allargato alcun limite del job reale per
far riuscire la prova.

## Blocco da chiudere con F5

`tests/runtime/infra/test_rm0008_release_cycle.py::test_early_recipe_check_uses_real_canonical_and_independent_codecs[False]`
fallisce con `PreflightError: service source recipe`; il caso negativo passa.
La baseline passa entrambi. Il confronto mostra che `830e36ca` aggiunge
`legacy-install-operator-authority` a `SERVICE_SOURCE_V1`, mentre la costante
indipendente approvata in `executor_birth_admin_preflight.py` resta invariata.
La divergenza va riesaminata da F5 come modifica della ricetta autorevole,
non risolta copiando automaticamente l'impronta candidata o rimuovendo il test.
Le tre correzioni del lanciatore sono confermate ma non chiudono questo blocco.

Prima del rilascio: chiudere il controllo della ricetta, verificare che il ramo
effettivamente preparato includa F5 e il candidato LRE, controllare i contratti
del job corrente, poi pianificare la pausa ordinaria con svuotamento e la
ripresa della stessa revisione. Dopo il rilascio, misurare quattro tentativi
completi reali e ricontrollare conservazione dei risultati, memoria e latenza.

## Produzione e documentazione

Controllo in sola lettura `run-t27pyhrw`, 11:44 Europe/Rome:
`wrk_4121cc6258c447759e94ccc6f8090e5c` ancora `running`, 215/967 batch
di analisi confermati, uno attivo, 1.183 risultati complessivi. Processo
1175505, revisione `rev_d8d3982283a140bf993f2517731ad00d` e impronta del piano
`a96bc10667200eb04d1b1f6f0637f206df147ff3725cc19e328ed9bf6c484421` invariati.

Pubblicate soltanto le guide statiche IT/EN: distribuzione Pages
`32e2aadc.mykleos.pages.dev`; archivio Tutor locale non modificato. Il primo
tentativo si è fermato sul controllo del riferimento UI prima dell'invio;
rigenerazione e nuovo confronto non hanno lasciato differenze nel riferimento,
il secondo tentativo ha superato tutti i controlli. Nessun rapporto interno,
immagine privata o dato del lavoro è stato incluso.

Risultati XML di questa esecuzione nel banco temporaneo
`/tmp/metnos-lre-general-parallel-20260917.8vkQ8j`:
`combined-core.xml`, `cross-release-resume.xml`, `combined-http.xml`,
`f5-launcher.xml`, `combined-release-recipe.xml`.
