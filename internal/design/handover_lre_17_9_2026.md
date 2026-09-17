# Passaggio di consegne LRE — ripresa con contesto nuovo

## Aggiornamento della ripresa — sera del 17/9

**Le sezioni 1–10 sotto conservano la fotografia precedente delle 17:26.**
Il blocco di caricamento è stato verificato e la riprova amministrativa unica
è già stata eseguita alle 18:03 CEST: **non ripeterla seguendo il vecchio stato**.
Caricamento singolo e quattro caricamenti concorrenti riusciti; la vecchia
sonda a 32 caricamenti aveva superato il proprio limite temporale. Nessuna
nuova modifica o importazione con cache nell'albero firmato.

Roberto ha poi autorizzato ottimizzazione generale CPU, analisi GPU/OOM,
ricerca delle pratiche raccomandate e separazione fra LRE e gestore dei modelli.
Il job ha ripreso sulla release 64, conservando piano, revisione, risultati e
contabilità. Le pause successive sono prove CPU deliberate, seguite da ripresa
ordinaria; non sono nuovi incidenti del loader. Stato e prove:
`internal/reports/lre-cpu-model-resources-20260917.md`.

Candidato nel worktree `lre-general-parallel-f5`: risorse prenotate insieme
senza trattenere CPU in attesa di altri mezzi; contratto Virt/LRE per il ciclo
di vita; limiti dinamici dei thread nativi nei nuovi subprocessi locali;
diagnostica temporale generale. Test di insieme 861 passati / 10 saltati,
più verifiche focalizzate e modelli locali reali su input sintetici. Nessuna
installazione del candidato né prova `/agent/turn` con quel codice.

**Gestione automatica di più repliche ancora da implementare.** Occorrono
indirizzo stabile, supervisore comune, instradamento, prenotazione dei picchi
di memoria e profili misurati; non impostare due/quattro copie nel job. Il
nuovo confine Virt non dimostra da solo che questa gestione esista.
GPU e F5 invariati. Non riavviare i servizi per trasformare il candidato in
un risultato apparentemente installato. Le guide IT/EN sono state pubblicate
con consenso esplicito di Roberto (`429ed0b8.mykleos.pages.dev`); verifica HTTP
successiva 403, da distinguere dall'esito positivo del comando di distribuzione.

Il requisito UI versione/build/data della sezione 10 resta da sviluppare.

---

Redatto il 17 settembre 2026; ultima lettura produttiva **17:26:17 Europe/Rome**
(`2026-09-17T15:26:17Z`). Documento interno: non pubblicare su siti pubblici.

## 1. Leggere prima: stato reale e ultimo mandato

Roberto ha chiesto di interrompere questa sessione e preparare un handover:
«hai un contesto vecchio. forse meglio uscire e rientrare. ma devi fare un
handover a te stesso». **Non continuare automaticamente le operazioni dalla
vecchia sessione.** Alla nuova richiesta di ripresa, leggere questo documento
e rimisurare lo stato prima di intervenire.

Il precedente incarico era pubblicare la nuova versione LRE se sicura e
riprendere **lo stesso job**, con parallelismo su CPU, senza perdere risultati.
La release 64 è stata installata e quattro nuovi batch sono stati salvati.
**Il job adesso NON avanza: è `needs_attention`.** Non confondere un motore
pronto con un lavoro in esecuzione, né il successo del rilascio con la chiusura
della verifica di ripresa. La funzione «Riprova» **non è stata ancora chiamata**
dopo l'ultimo incidente.

L'agente ha causato un incidente con una propria sonda: un'importazione Python
ha scritto una cache nell'albero firmato, facendo rifiutare il caricamento.
La cache è stata rimossa con verifica puntuale e l'integrità nuovamente
attestata. Una successiva prova concorrente di caricamento è però scaduta:
**esito non conclusivo, da capire prima di dichiarare risolto il problema**.

## 2. Fotografia produttiva aggiornata, non dedotta dal contesto

Fonte: sonda in sola lettura `run-2i3j6xef`, ore 17:26:17.

| Elemento | Valore osservato |
|---|---|
| Release in esercizio | 64 |
| HTTP / worker / Telegram | attivi, nessun riavvio automatico |
| Salute generale | `operational=true` |
| Motore LRE | abilitato, `ready`, heartbeat aggiornato |
| Job | `wrk_4121cc6258c447759e94ccc6f8090e5c` |
| Revisione | `rev_d8d3982283a140bf993f2517731ad00d` |
| Stato/versione job | `needs_attention`, 21 |
| Batch salvati complessivi | **1.266 / 1.935** |
| Batch in attenzione | 1 |
| Batch in attesa | 668 |
| Batch in esecuzione / acquisiti | **0 / 0** |
| Falliti definitivamente / saltati | 0 / 0 |
| Consumi incerti | `usage_unknown=false` |
| Ultimo risultato confermato | `2026-09-17T15:11:25.638391Z` |
| Stima fine | assente perché il job richiede attenzione |

I 1.266 includono fasi diverse: **non sono 1.266 foto**. L'ultima lettura diretta
per fase alle 17:14 dava **298/967 batch di analisi**, contro 294/967 alla pausa;
da allora il totale e l'ultimo salvataggio sono invariati. La proiezione API
della fase corrente, quando il job è in attenzione, restituisce campi nulli:
non presentarli come azzeramento dei risultati.

Il limite del piano esposto dalla UI è 31; i limiti effettivi CPU/VLM configurati
sono **4/4**. Sono già stati osservati quattro tentativi contemporanei, ma non
è dimostrato uno speedup di quattro volte né l'equivalenza prestazionale GPU.
GPU non modificata; F5 non attivato, proprietario Birth ancora `LEGACY`.

PID osservati, utili solo per confronto e da rileggere prima dell'uso:

- HTTP `1909653`, avvio 16:58:46 CEST;
- worker `1909652`, avvio 16:58:46 CEST;
- Telegram `1909716`, avvio 16:58:47 CEST.

Tutti usano `/var/lib/metnos/executor-birth/releases-v1/00000000000000000064`
(il worker ha come directory corrente il relativo `runtime/`).

## 3. Incidente introdotto dalla diagnostica, prova e riparazione

Alle **17:09:03 CEST** la prima versione di `checkpoint-progress.sh` importava
`index_schema.py` dalla release con `importlib`, usando `-I -S` ma non `-B`.
Python ha creato:

`/var/lib/metnos/executor-birth/releases-v1/00000000000000000064/runtime/__pycache__/index_schema.cpython-312.pyc`

Prove di attribuzione (`run-dnl4pvl0`):

- mtime `2026-09-17T15:09:03.470518Z`, coincidente con la sonda `run-19a1dytz`;
- unico file della cartella, root:root, file 0600/cartella 0700;
- SHA256 `da714e5e72342c3f40dfb475822225dc5c7ccd17daef4d1d5301a16d11ab667d`;
- codice compilato identico alla sorgente firmata non modificata;
- preflight indipendente: `birth_ownership_preflight_invalid`,
  **extra distribution entry**.

Il controllo di integrità ha quindi correttamente impedito il successivo
caricamento. Tentativo interessato:
`att_72752dfd952045808e021ea1a8de84e2`, termine 17:11:17 CEST,
classe `capability_unavailable`, codice `execution.executor_unavailable`,
`loader_cause=unclassified`, executor `create_images_indices`. È fallito prima
dell'esecuzione dell'executor, non durante la lettura di una nuova fotografia.
I quattro batch già partiti hanno comunque concluso e salvato i loro risultati.

**Riparazione già effettuata**: `run-qckouwkx` ha rimosso soltanto quella cache
derivata e la sua cartella ormai vuota dopo controllo di percorso, impronta,
proprietà, numero di collegamenti e corrispondenza con la sorgente. Ha poi
attestato positivamente tutti e tre i servizi sulla 64. Nessun originale,
indice, record SQL o sorgente firmata è stato modificato dalla riparazione.
Il file eliminato era rigenerabile; non c'è stata perdita di dati applicativi.

Entrambe le sonde che importano codice installato sono state corrette con
**`-B` e `sys.dont_write_bytecode=True`**. `-I` da solo non vieta la scrittura
del bytecode. In futuro non importare mai codice dall'albero immutabile senza
disabilitare esplicitamente la creazione delle cache. Non aggirare o indebolire
il controllo d'integrità per far passare una sonda.

### Prova successiva NON superata

`loader-probe.sh`, versione corretta, ha tentato 32 letture verificate del
catalogo, con quattro thread e nessuna esecuzione di executor, come account
`metnos`. **`run-r5n5l7pn`: uscita 124 per limite di 120 secondi**. Il risultato
non contiene conteggi conclusivi di successi/errori; log privato:

`/var/lib/metnos-admin/agent-runs/run-r5n5l7pn/probe.log`

Non è una prova verde. Non sappiamo ancora se si tratti soltanto di un limite
troppo breve per 32 verifiche o di un'attesa problematica. Esaminare prima il
log in modo circoscritto; eventualmente misurare una singola lettura con tempo
limite, poi la concorrenza minima utile. Non ripetere alla cieca il carico.
Il controllo dei processi nel contesto host alle 17:26 non ha mostrato residui
della sonda né processi orfani ad alto consumo; nessun processo è stato ucciso.

La precedente sonda `run-y86flc3_` fallì ancora sul controllo della cache:
non confonderla con il timeout della versione corretta.

## 4. Cosa preservare e come riprendere, dopo nuova richiesta dell'utente

1. Leggere le istruzioni del progetto e questo handover, poi acquisire un
   nuovo stato in sola lettura. La fotografia qui sopra può diventare vecchia.
2. Verificare l'assenza della cache e l'attestazione della release selezionata;
   chiarire il timeout del caricatore senza scrivere nell'albero installato.
3. Prima di ogni riprova salvare una nuova fotografia amministrativa dei
   riferimenti unità/risultato. Verificare stessa revisione, stessa impronta
   del piano, risultati precedenti tutti presenti, nessun tentativo attivo
   con contabilità incompleta, `usage_unknown=false` e contratti compatibili.
4. Se la causa è effettivamente rimossa, usare **una sola normale «Riprova»**:
   `POST /agent/workloads/{job_id}/attention/retry`, corpo con i soli
   `expected_version` appena letto e `idempotency_key` univoca.
   Il contratto è in `runtime/http_routes_durable_workloads.py` e
   `runtime/durable_workloads/control.py::resolve_attention`.
   Non usare «Riprendi», che è per una pausa, né modificare SQL a mano.
5. Osservare nuovi risultati **successivi alla riprova**, con confronto
   puntuale dei riferimenti già salvati e avanzamento dei batch successivi.
   Il vecchio osservatore partiva da 1.262: la sua soglia +4 è ormai già
   soddisfatta e non proverebbe la nuova ripresa. Nuova base almeno 1.266.
6. Aggiornare report/coordination, verificare assenza di residui e completare
   il commit documentale. Consegnare il breve testo richiesto per agentef5.

Non aumentare limiti, cambiare politica degli errori, cancellare storico,
azzerare budget, ricreare il job, riavviare servizi o pubblicare un'altra
release per ottenere artificialmente un verde. Non cancellare fotografie o
indici. Roberto aveva limitato le vecchie cancellazioni alle sole fotografie
effettivamente guaste; non è un mandato aperto per altre cancellazioni.

I batch hanno un timeout contrattuale di 1.800 secondi. I primi quattro
simultanei hanno impiegato circa dieci minuti; un controllo può quindi
richiedere attesa, ma va distinto da un blocco vero e va comunicato con misura.
`revision_usage.usage_complete=false` su un job non terminale, da solo, non
dimostra una contabilità guasta: controllare i singoli tentativi e
`usage_unknown`.

## 5. Albero di lavoro, commit e modifiche da non perdere

**Lavorare qui**, non nella copia principale:

`/opt/metnos/.claude/worktrees/lre-general-parallel-f5`

Ramo `codex/lre-general-parallel-f5`; HEAD osservato **`4080f48d`**.
La copia `/opt/metnos` è su `session/detection-lexicon-i18n`, molto modificata,
con file ritirati e lavori estranei: non ripulirla, ripristinarla o fonderla
automaticamente. Il breve file omonimo nella sua `internal/design/` è solo un
puntatore a questo handover.

Commit rilevanti, in ordine:

- `bc103678`: prova sicura del ritiro aggiuntivo nel successore, prima dello stop;
- `55c62222`: fusione documentale F5, include `e581e6c4` e il ramo LRE;
- `a996d02c`: preparazione ufficiale del candidato, impronte riallineate;
- `4080f48d`: strumento interno di rilascio, archiviazione di ricostruzioni
  identiche senza sovrascrittura e misura del verificatore dopo il recupero.

Otto file già modificati da questa sessione, **non ancora committati**:

```
CLAUDE.mutabile.md
decisions/0224-single-deterministic-executor-birth-gate.md
docs/en/architecture/executor_birth.html
docs/en/system/lre.html
docs/it/architecture/executor_birth.html
docs/it/system/lre.html
internal/coordination/richieste-a-lre-17-9-2026.md
internal/reports/lre-generic-parallelism-20260917.md
```

Si aggiunge questo nuovo handover. Nel report è stato aggiunto il richiamo
allo stato 17:26 per non lasciare in testa soltanto «job ripreso» delle 17:02.
Nessun commit, ulteriore rilascio o riprova durante la preparazione dell'handover.

## 6. Rilascio eseguito e prove già disponibili

Pausa cooperativa conclusa alle **16:47:42**: 294/967 analisi, 1.262 risultati
totali, zero tentativi attivi o con consumi incerti. Configurazione modificata
soltanto per CPU/VLM 4. Rilascio `run-sfv9_bnw` riuscito, servizi sulla 64 dalle
16:58:46; ripresa ordinaria `run-pb7bnt8k` alle **17:00:52**, stesso job e piano.
Tutti i 1.262 riferimenti unità/risultato della pausa conservati.

Identità della release, non da sostituire con la sola versione Git:

```
build  sha256:4cc8a5d2cf0cd8f499e67a8238da6b5ebfbd40137cbe545f885dcde4b7644ab4
source sha256:3d5cc47fd52e17a997350de260fb63b7fc9f1b0cfa26ebbdd34f8c6369239ad9
head   sha256:617454f2376751973fd9239230283f41f28536788263f6a4ab0d448e6984deac
cutover sha256:20098df44827ff5dc3eb6f87fc553d1e0f423ae8a6229db90c498ae94b1e475c
plan SHA256 a96bc10667200eb04d1b1f6f0637f206df147ff3725cc19e328ed9bf6c484421
```

Sei contratti del piano confrontati con il catalogo realmente installato come
account di servizio: tutti identici (`run-8uh6iqe9`). La conservazione ufficiale
ha rimosso gli alberi di solo codice 52–62, conservando 63/64 e storia firmata.
Non ripetere questa operazione né interpretarla come pulizia dei job.

Prove isolate dopo preparazione, nella cartella temporanea indicata sotto:

| Gruppo | Esito | Evidenza |
|---|---|---|
| Transizione/ritiro/topologia | 287 superate | `transition-prepared.xml` |
| LRE, incluse vere riprese tra release e interruzione processo | 742 superate, 4 saltate | `lre-prepared.xml` |
| Strumento di rilascio, incluse 9 nuove regressioni | 213 superate | `release-cycle-repeat.xml` |
| API/console | 18 superate, 2 saltate | `http-prepared-host.xml` |

I profili saltati sono facoltativi e dichiarati nel report, non prove verdi.
Il gruppo di transizione usa uno spazio utenti isolato; non è una modifica di
proprietà su dati produttivi. La prima prova HTTP bloccata nella sandbox di
rete è stata interrotta e poi ripetuta correttamente; residui verificati.

Prova reale chat dopo rilascio: turno **`93b2c6c540714e38`**, 20,754 secondi,
zero executor, HTTP/console disponibili (`run-b7usl93b`). Non dimostra la
copertura dell'archivio fotografico. Il primo osservatore della ripresa,
`run-2xsbj9pl`, è fallito alle 17:11 per lo stato in attenzione: **conservarne
l'esito negativo**, non sostituirlo con una verifica precedente positiva.

## 7. Evidenze amministrative e sonde: usare solo dopo rilettura

Cartella di lavoro temporanea:
`/tmp/metnos-retirement-fix-20260917.lRNlWz`

Archivio privato dei riferimenti pre/post pausa:
`/var/lib/metnos-admin/lre-rollout-20260917-bc103678`

Contiene `baseline.json`, `before-pause.json`, `paused.json` (1.262 risultati),
`runtime.before.toml`, `configuration-applied.json`, `resume-requested.json`.
Non sovrascrivere. Il primo osservatore non ha prodotto un `verify.json`
positivo. Per una nuova riprova usare nomi nuovi e un confronto dalla nuova
base, non modificare quelli già presenti.

Gli esiti delle sonde sono sotto `/var/lib/metnos-admin/agent-runs/{run_id}/`.
Le sonde amministrative passano dal lanciatore ristretto già installato:

```
sudo -n /usr/local/sbin/metnos-agent-admin /percorso/assoluto/sonda.sh SHA256 [modalità]
```

Il lanciatore fornisce alla sonda una cartella privata come primo argomento.
Leggere lo script e verificare l'impronta prima dell'uso; non eseguire comandi
copiati dal vecchio contesto senza ricontrollare bersaglio e modalità.

| Sonda, nella cartella temporanea | SHA256 corrente | Avvertenza |
|---|---|---|
| `installed-summary.sh` | `607b69c2375cbe5e10febb301cadb2b2fa9f82055e8c392b6d21fe6ddedc50e3` | Sola lettura; `ok=true` NON significa job in esecuzione |
| `verify-installed-contracts.sh` | `7ca8065c387b2e6f0da5e55bc907e77e9013d2afa471ed9413e178b77af651af` | Sei contratti installati, CPU/VLM, proprietario Birth |
| `checkpoint-progress.sh` | `f46a1b549807d92db2612ca0609d0ebd9c988bf9f4a793de69280ed33fac748b` | Corretta con `-B`; data iniziale fissata alla prima ripresa |
| `loader-probe.sh` | `48a1f8a4496c9a9ccf6580ab70a8e4b463fbb81cd7cd2b8f46022c85452d0849` | Corretta con `-B`, ma prova scaduta: non considerarla verde |
| `cache-inspection.sh` | `bea8265c901ec4f576523016d417e9dbde590f678bba506aa03251fd45e83ca3` | Sola lettura, verifica cache e attestazione |
| `attention-diagnosis.sh` | `faa0fa6e436f866533295b3599c0ee737941cf4e6a738f345860ff519cb7a4dd` | Errori strutturati e journal circoscritto |
| `rollout-control.sh` | `6799961c19f100a80223af9f56ca4443b1e62f65f7a2fee6ea2b15bed3ca2426` | Ora riutilizzare solo `status`; altre modalità già consumate o inadatte |

`remove-probe-cache.sh` ha già completato la rimozione esatta: **non rieseguirlo**.
`release-cycle.sh` ha già pubblicato la 64: **non rieseguirlo** per questo incidente.
La copia temporanea utente della configurazione è già stata eliminata;
la copia amministrativa di recupero resta privata.

DB: `/var/lib/metnos-service/.local/state/metnos/durable_workloads/state.sqlite3`.
Solo letture con `mode=ro` e `PRAGMA query_only=ON`, oppure API normale per i
comandi. Config: `/var/lib/metnos-service/.config/metnos/runtime.toml`.
La chiave API è nel file privato `admin.key` nella stessa cartella: leggerla
soltanto in memoria per autenticare, **mai mostrarla, copiarla nel documento
o stamparla nei log**. Account di esercizio: `metnos`, uid 995/gid 985.

## 8. Coordinamento F5 e documenti pubblici

Leggere la versione del ramo combinato di
`internal/coordination/richieste-a-lre-17-9-2026.md` e confrontare eventuali
nuovi interventi dell'altro agente: non assumere che la sua copia sia immutata.
F5 rimane **non attivato/non certificato**; non sono state create le chiavi
dedicate o il registro delle prove e non sono stati eseguiti i due cicli F5.
L'incidente della cache è attribuito alla nostra diagnostica, non al codice F5.
Il timeout successivo resta da chiarire: nessun nuovo via libera incondizionato.

Resta da consegnare a Roberto un testo sintetico per agentef5, con rilascio 64,
stesso job preservato, parallelismo effettivo quattro, stato aggiornato della
riprova e limite esplicito sulla certificazione. Non inviare messaggi esterni
di iniziativa e non dichiarare la ripresa stabile finché non provata.

Le guide pubbliche IT/EN sono già state pubblicate separatamente, solo statiche:
`https://53507c0c.mykleos.pages.dev` (quattro HTML, 99 documenti validati).
Non ripubblicare rapporti interni o questo handover. Cloudflare è un servizio
di sviluppo/documentazione, **non una componente dell'esercizio pubblico**.

## 9. Criterio di chiusura

Il lavoro non è chiuso finché non sono provati: causa rimossa, stessa revisione
e risultati preservati, nuovi salvataggi dopo la riprova, successivo avanzamento
con la concorrenza prevista, nessuna contabilità incerta e resoconto aggiornato.
Una UI disponibile o un heartbeat verde, da soli, non bastano.

Ultimo intervento di questa sessione: preparazione dell'handover e letture di
stato/processi. **Nessuna riprova, nessun riavvio, nessuna nuova cancellazione.**

## 10. Nuovo requisito: versione e data di build per tutti i servizi

Richiesta successiva di Roberto, 17/9: «tutti i servizi di metnos dovrebbero
avere una versione e un data di building, riportati nella UI».

**Stato: requisito acquisito e sorgenti esaminati; NON implementato né
pubblicato.** Nessun codice applicativo, processo, configurazione o job è stato
modificato per questa richiesta. La priorità della ripresa sicura del job
precedente non è stata implicitamente sostituita da un rilascio della UI.

### Risultato richiesto

Ogni servizio nella console amministrativa deve avere una sezione compatta
sempre visibile, anche quando è fermo, che riporti:

| Dato | Significato |
|---|---|
| Versione | Versione del software di quel servizio, non della sola API |
| Build | Identificatore preciso del pacchetto, distinto dalla versione generica |
| Data di build | Data e ora registrate durante la costruzione di quel pacchetto, con fuso esplicito |

La data di avvio esistente rimane separata. Per dati storici o componenti
esterni che non li forniscono: `n.a.`, con motivo sintetico se utile. Non
dedurre una data di build dal riavvio, dalla pubblicazione, dal commit, dal
mtime di un file o dall'ora corrente della richiesta HTTP.

I servizi interni distribuiti insieme ereditano la stessa versione e identità
di release Metnos: non introdurre contatori manuali indipendenti per HTTP,
Telegram e LRE. Componenti esterni (ad esempio motore LLM, SearXNG, Photon,
Xvfb) conservano la propria versione e la propria eventuale data di build;
non assegnare loro i numeri di Metnos. La versione di Chromium non è quella
del servizio Playwright Metnos, e la versione del modello non è quella del
motore LLM: se esposte entrambe, devono avere etichette distinte.

Distinguere il software **in esecuzione** da quello **installato/configurato**.
Una selezione della release nuova non dimostra da sola che un vecchio processo
sia già stato riavviato. Se l'identità in uso non è verificabile, dichiararlo;
se il servizio è fermo, indicare esplicitamente che il dato riguarda il
pacchetto installato. Nessun campo informativo deve diventare una nuova
autorità di avvio, firma, pubblicazione o controllo dei servizi.

### Riscontri nei sorgenti, al momento dell'analisi

- `runtime/__version__.py`: versione prodotto unica `0.1.0` e versione del
  contratto AI backend. `version_info()` alimenta salute e discovery, ma non
  contiene una data di build.
- `runtime/executor_birth_distribution_manifest.py::VerifiedDistribution`:
  identità firmata `closed_build_id`, `product_version`, `release_sequence`,
  radice e inventario; nessun timestamp di costruzione nel record attuale.
- `install/executor_birth_distribution_release.py::_assemble_staging_v1`:
  costruisce il pacchetto chiuso. Le prove impongono identità byte-per-byte
  alla ripetizione: un `now()` non persistito cambierebbe la build a ogni
  tentativo e romperebbe il recupero. Conservare questo vincolo.
- `internal/tools/rm0008_release_cycle.py::prepare`: prepara, ricontrolla,
  esporta e fissa i sorgenti; `apply` deve ricostruire lo stesso candidato.
  La scelta del punto che registra il tempo deve rispettare questa separazione,
  oltre al percorso pubblico di costruzione/installazione, non solo al tool interno.
- `runtime/services_registry.py`: registro comune, selezione dei target dal
  catalogo firmato e osservazioni. Oggi espone `active_since`, ma non versione
  o build per servizio. Non duplicare qui un secondo inventario di unità.
- `runtime/templates/services.html`: schede amministrative attuali; i dettagli
  del processo LRE sono richiudibili. La versione richiesta deve rimanere
  visibile, non soltanto dentro quei dettagli.
- `runtime/http_routes_admin.py::admin_services`: stessa proiezione per UI e
  JSON; raccolta dello stato con budget di otto secondi.
- Il catalogo della console raggruppa nove servizi logici, mentre il catalogo
  firmato include anche unità ausiliarie e timer. «Tutti» deve coprire anche
  queste componenti nella relativa sezione/dettaglio, senza inventare nuovi
  pulsanti di controllo o aggiungere servizi privati di sviluppo.

### Vincoli di implementazione e verifica

1. Definire un solo descrittore informativo con versione, identità di build,
   istante UTC e provenienza. Registrare l'istante nella filiera di costruzione
   e includerlo nell'inventario autenticato del nuovo pacchetto. Nessuna
   riscrittura delle release già firmate, nessuna cache nel loro albero.
2. Provare che ripetizione, ripresa dopo interruzione e ricostruzione dello
   stesso candidato conservino esattamente il descrittore. Definire chiaramente
   l'evento rappresentato dal timestamp; non etichettare come compilazione
   un dato che misura soltanto un altro evento.
3. Derivare le componenti dal catalogo comune. Per software esterno usare
   metadati verificabili del pacchetto/processo o interfacce in sola lettura
   dichiarate dal componente; nessuna esecuzione di comandi arbitrari desunti
   dalle richieste HTTP. Non risvegliare servizi o modelli solo per leggerne
   la versione. Se il dato manca, non inventarlo.
4. La raccolta deve essere limitata per tempo e dimensione; metadati mancanti,
   malformati o un endpoint lento non devono nascondere lo stato del servizio
   né bloccare la console. Non esporre variabili d'ambiente o segreti.
5. Mostrare gli stessi valori in API e UI. Etichette, spiegazioni e dato
   mancante devono passare dal sistema i18n, con IT/EN e normale percorso di
   traduzione delle altre lingue; versioni/hash/istanti restano dati.
6. Prove: servizi interni ed esterni, fermi, dati mancanti, contenuti malformati,
   processo di una release diversa da quella selezionata, avvio invariato
   senza cambio di build, ripartenza senza perdita dell'identità, ripetizione
   del build, rendering IT/EN, escaping e limiti di osservazione.
7. Riferimenti di test già esistenti:
   `tests/runtime/infra/test_services_registry.py`,
   `tests/runtime/http/test_admin_lre_feature.py`,
   `tests/runtime/infra/test_rm0008_release_cycle.py`,
   `tests/portable/test_executor_birth_distribution_release.py`.
   Leggere integralmente `install/INSTALL_NOTES.md` prima di toccare
   l'installatore; aggiornare contratto e guide IT/EN nello stesso intervento.

Non è stato avviato alcun lavoro in background per questo requisito. La nuova
sessione deve trovare questa voce ancora **da sviluppare**, non presumere una
UI già aggiornata o una data di build disponibile nella release 64.
