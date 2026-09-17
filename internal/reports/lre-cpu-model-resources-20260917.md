# Risorse CPU e servizi di inferenza — 17 settembre 2026

## Stato e confine approvato

Roberto richiede ottimizzazioni generali, CPU ottimizzata e analisi della GPU
con attenzione alla memoria condivisa con altri processi. LRE deve restare
agnostico: i requisiti derivano dai contratti degli executor; scelta, riuso e
ciclo di vita dei servizi appartengono a Virt. Nessun riconoscimento del tipo di
job tramite nomi, contenuti o un altro LLM.

Il candidato è nel worktree `lre-general-parallel-f5`; la release in esercizio
resta 64. Non sono state attivate GPU, F5 o nuove chiavi. Non confondere test del
candidato, misure sull'installazione esistente e installazione di nuovo codice.

## Modifiche candidate implementate

- Lo scheduler riserva atomicamente l'intero vettore di risorse, dopo le
  esclusioni dell'executor e dei percorsi. Un lavoro in attesa di VLM, rete o
  disco non trattiene CPU utilizzabile da un altro lavoro. Restano validi
  scadenza, quote, isolamento, coda equa e rilascio dopo eccezioni. La coda
  globale può ancora limitare il numero dei contendenti: non è una prova di
  assenza di ogni possibile attesa in testa alla coda.
- `virt.resources.ModelResource` espone quota, fatti del modello e disponibilità.
  LRE verifica il contratto prima e dopo l'avvio; non conosce provider, porte o
  launcher. L'adattatore realmente disponibile è quello visivo locale; gli
  altri servizi mantengono la gestione esterna già esistente. Il test di un
  adattatore testuale dimostra il confine, non l'installazione di un nuovo
  launcher testuale.
- I nuovi subprocessi locali gestiti ricevono un budget di thread nativi,
  derivato da affinità CPU, quote cgroup v2 visibili, quota logica assegnata e
  numero di lavoratori interni. CPU logiche dello scheduler e core fisici
  rimangono concetti distinti. OpenMP/BLAS ereditano limiti solo nel figlio;
  ONNX rispetta il budget e disabilita l'attesa attiva. Limiti amministrativi
  inferiori restano vincolanti. Nessuna modifica globale a `os.environ`, nessuna
  riconfigurazione di sessioni già caricate nei demoni. Questo limite non
  riserva core esclusivi, RAM o capacità dei servizi modello esterni. Le
  macchine remote richiedono la stessa politica applicata dal proprio host.
- `internal/tools/lre_performance.py` legge contatori senza modificare il DB,
  distingue somma dei tempi dei tentativi e tempo trascorso, segnala contabilità
  incompleta e non stampa contenuti, percorsi dei dati o prompt.

## Evidenze sul job conservato

La riprova amministrativa unica del 17/9 alle 18:03 CEST ha mantenuto revisione,
piano e tutti i 1.266 risultati già salvati. Controllo `run-zycbvioi` delle 18:45:
1.282 risultati, 314 blocchi di analisi, quattro attivi, consumo sconosciuto falso
e nessun tentativo concluso con contabilità incompleta. Worker PID invariato.

La prima pausa per il confronto CPU (`run-0f0hd4jg`) ha terminato ordinatamente
altri quattro blocchi: 1.286 risultati / 318 analisi. Il servizio temporaneo non
è partito: `MemoryOOMGroup` non è una proprietà supportata dal systemd installato.
Nessuna misura prestazionale prodotta. Ripresa normale verificata, versione 26,
nessun risultato perso. La proprietà corretta `OOMPolicy=kill` è stata verificata
con `/usr/bin/true` in un servizio temporaneo (`run-rs4s9i7o`).

## CPU, processi e caricamento dei modelli

L'installazione attuale ha un solo server VLM condiviso e persistente, avviato
quando necessario e fermabile per inattività. Ogni blocco LRE esegue invece un
subprocesso isolato; BGE, CLIP e riconoscimento volti sono conservati solo per la
vita di quel subprocesso. Non sono ricaricati per ogni foto, ma possono esserlo
al blocco successivo. La cache dei file del kernel non equivale alla condivisione
di sessioni ONNX e dei loro buffer.

Osservazione `run-dvn5n4tt`: ciascuno dei quattro executor aveva circa 99 thread
e 2,2–2,4 GiB di RSS; il VLM CPU circa 13,7 GiB di RSS e 51 thread. I thread
esistenti non sono tutti necessariamente attivi contemporaneamente.

Il confronto storico sullo stesso insieme di otto immagini aveva già misurato
29,24 secondi con quattro richieste a un server contro 42,25/42,29 con una;
non dimostra il vantaggio di più server. Le durate medie del job (219,3 secondi
per blocco seriale contro circa 626,7 per ciascuno dei primi quattro paralleli)
sono su input diversi e non costituiscono un confronto controllato.

## GPU e rischio di esaurimento memoria

Osservazione puntuale del 17/9, non una garanzia futura:

- Il VLM di esercizio non aveva descrittori GPU aperti; il log documentava
  mancato accesso al dispositivo/inizializzazione Vulkan. I processi CPU non
  diventano GPU per effetto del numero di thread. Non sono stati ampliati gruppi
  o permessi del worker.
- La macchina esponeva circa 121,5 GiB di RAM al sistema e 39,4 GiB disponibili
  durante quattro blocchi. VRAM dedicata dichiarata 4 GiB, circa 3,93 utilizzati;
  GTT dichiarata 80 GiB, circa 24,2 utilizzati. GTT massima non è RAM aggiuntiva.
  RSS, cgroup e contatori DRM possono sovrapporsi e non vanno sommati alla cieca.
- Il modello linguistico principale occupava circa 24,6 GiB secondo i contatori
  residenti GPU; altri servizi vocali usavano anch'essi la GPU. Erano attivi
  anche servizi NPU. Il cgroup del worker comprendeva VLM e subprocessi, senza
  limite di memoria; nessun evento OOM nel campione osservato.

Per un gestore dinamico servono stime misurate di pesi, cache del contesto,
preelaborazione e picchi temporanei; prenotazione centrale degli avvii già in
corso; disponibilità host aggiornata e margine per gli altri servizi; code
limitate e rifiuto/attesa esplicita quando manca capacità. Il gestore deve
considerare memoria unificata e pressione del sistema, non solo la VRAM libera.
Un cgroup separato contiene i danni di un processo, ma non prenota da solo tutte
le allocazioni GPU. Non promettere assenza di OOM contro consumatori esterni
privi di quote. Non è stata abilitata inferenza GPU in questa sessione.

## Gestione dinamica: parte ancora necessaria

Il confine Virt/LRE è implementato; la gestione automatica di più repliche non
lo è ancora. Richiede un endpoint logico stabile, un supervisore condiviso fra
job, instradamento con limiti, identità verificata del modello e della politica,
risorse prenotate anche durante l'avvio, recupero dai processi morti e arresto
solo dopo esaurimento delle richieste. I contratti LRE oggi congelano anche
l'endpoint: sostituirlo direttamente per scegliere una replica invalida il
contratto. La distribuzione va dietro l'endpoint stabile.

Le repliche devono essere giustificate da misure sul profilo hardware/modello;
una coda lunga non dimostra capacità CPU inutilizzata. L'aumento e la riduzione
richiedono soglie e ritardi per evitare continui caricamenti. Una configurazione
fissa a due o quattro nel singolo job violerebbe il requisito ricevuto.

## Fonti primarie consultate

- [ONNX: threading](https://onnxruntime.ai/docs/performance/tune-performance/threading.html):
  pool per sessione, limiti dei thread e costo dell'attesa attiva.
- [Ray: risorse logiche](https://docs.ray.io/en/latest/ray-core/scheduling/resources.html)
  e [prenotazione atomica](https://docs.ray.io/en/latest/ray-core/scheduling/placement-group.html).
- [Ray Serve: autoscaling](https://docs.ray.io/en/latest/serve/advanced-guides/advanced-autoscaling.html):
  richieste in corso, soglie e ritardi; riferimento architetturale, nessuna nuova dipendenza Ray.
- [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md):
  gestione dei modelli e server; la versione installata è verificata anche nel sorgente locale.
- [Linux cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html),
  [AMDGPU](https://www.kernel.org/doc/html/latest/gpu/amdgpu/driver-misc.html),
  [DRM usage](https://origin.kernel.org/doc/html/latest/gpu/drm-usage-stats.html).
- [Dask: memoria dei worker](https://distributed.dask.org/en/latest/worker-memory.html):
  pressione misurata e sospensione di nuovo lavoro prima dell'esaurimento.

## Verifiche del candidato e documentazione

- Suite di insieme: **861 passati, 10 saltati**, 105,96 secondi. Comprende
  durable workloads, scheduler, isolamento, equivalenza del parallelismo,
  ciclo di vita VLM, thread nativi e diagnostica. I test CLIP/volti saltano
  quando non trovano i pesi sotto il worktree; altri controlli estesi sono
  opzionali. Non sono stati cancellati o disabilitati per ottenere il verde.
- Dopo il limite Rayon e la lettura delle sole quote prodotte dal runtime:
  **103 passati**, 2,33 secondi, nel gruppo direttamente interessato.
- Prova reale isolata dei modelli locali, con input sintetici: BGE produce
  2 × 1024, CLIP testo 768 e immagine 1 × 768, volti restituisce lista vuota
  per un'immagine uniforme. Tra ONNX senza limite e budget nativo 2,
  differenza massima assoluta **0** sui tre vettori. Thread del processo
  39 contro 9; entrambi i processi mantenevano BLAS/Rayon limitati a 2 per
  isolare il contributo ONNX. Durate singole 2,32 e 2,96 secondi, non un
  confronto prestazionale significativo. Non prova equivalenza su ogni input
  né prestazioni del job completo.
- Non ancora effettuati: installazione del candidato e turno reale
  `/agent/turn` che ne attraversi il percorso completo. Nessuna certificazione
  di rilascio viene dedotta dai soli test o dal turno della vecchia release.
- Guide IT/EN validate localmente (99 pagine indicizzabili) e pubblicate con
  `deploy.sh --static-only` dopo consenso esplicito di Roberto. Distribuzione
  confermata dal fornitore: `https://429ed0b8.mykleos.pages.dev`. La verifica
  HTTP successiva dalla sessione ha ricevuto 403 sia dalla distribuzione sia
  dal dominio canonico: non dichiarare verifica remota del contenuto riuscita.
  Tutor, dati privati, immagini e rapporti interni esclusi dalla pubblicazione.

## Confronto concluso: uno, due e quattro server sulla stessa CPU

Banco finale `run-zoelqyon`, concluso alle **19:25:13 CEST**. Otto input congelati,
medesimi pesi, prompt e limiti; 16 thread CPU, quattro richieste e 16.384 token di
contesto totali in ogni configurazione. GPU disabilitata. Riscaldamento per ogni
replica, servizio temporaneo limitato a 28 GiB senza swap, riserva osservata
host di almeno 16 GiB. Il job era in pausa cooperativa; gli altri servizi host
non sono stati arrestati. Nessun evento OOM nel gruppo di prova.

| Server | Thread per server | Otto input, secondi | Token generati | Picco del cgroup, GiB |
|---|---:|---:|---:|---:|
| 1, controllo finale | 16 | 29,70 | 1.682 | 4,52 |
| 2 | 8 | 29,84 | 1.645 | 6,52 |
| 4 | 4 | 37,19 | 1.743 | 10,02 |

Non emerge un vantaggio da due server; quattro hanno impiegato circa il 25%
in più del controllo finale e più del doppio della memoria misurata. I due
precedenti riferimenti con un server erano 31,52 e 29,16 secondi. La seconda
sessione si era interrotta sul controllo della porta in TIME_WAIT; la terza
aveva misurato solo il riferimento per rispettare il termine complessivo. Il
banco finale ha corretto il riuso della porta e incluso tutti i profili previsti.
Questi problemi appartenevano al banco diagnostico, non al runtime LRE.

Tutti gli output rispettano lo schema, ma due/quattro server hanno prodotto
impronte diverse dal riferimento in 8 casi su 8; i token generati differiscono.
Non è una dimostrazione di equivalenza semantica né un confronto a identico
numero di token. Nessuna replica permanente o regola fissa per il job è stata
introdotta. Su questa misura non è giustificato aggiungere repliche; eventuali
altri modelli, hardware o forme di input richiedono il proprio profilo.

Il picco del cgroup non è la somma degli RSS né una stima garantita per tutte
le immagini: pagine dei pesi possono essere già condivise con altri processi,
e forme diverse degli input cambiano i buffer temporanei. Non usare questi
otto campioni come limite sicuro universale della memoria.

Controllo finale `run-ccx8_o09`, **19:26:03 CEST**: job `running`, versione 39,
330 analisi salvate, quattro tentativi attivi, **1.298 risultati conservati**,
32 nuove analisi dalla riprova iniziale; stessa revisione, stesso piano e worker
PID 1909652. Nessun tentativo concluso con contabilità incompleta, consumi
sconosciuti falsi. `run-0mpvc_fr` conferma che non rimangono unità di servizio
o server temporanei delle prove.

Ulteriori controlli del percorso di avvio reale dei figli, delle quote native
e della diagnostica: **25 passati**, 1,83 secondi. Comprendono invocazioni
ordinarie e durevoli, quote ereditate non attendibili e ambiente del padre
invariato. Il candidato è salvato nel commit `1e8eed24`; nessuna installazione
è stata eseguita. Restano installazione/prova completa del candidato e gestione
automatica delle repliche; il relativo confine architetturale non è una
certificazione di quelle funzioni.


## Pulizia richiesta da Roberto

Rimossi i file del banco temporaneo di questa ripresa, gli array sintetici e
il registro locale di Wrangler; rimossi 562 file di cache ignorati da Git nel
solo worktree dedicato (18.711.466 byte). Nessun processo del banco ancora
attivo e nessun server/unità temporaneo residuo. Codice, test e rapporti sono
conservati nel ramo dedicato; le ricevute amministrative protette restano
nell'archivio di controllo. Nessuna modifica ai dati del job, ai modelli in
esercizio, ad altri worktree o alle cache dell'installazione firmata.

Risposta prestazionale: i test provano minore moltiplicazione dei thread e
assenza della specifica prenotazione parziale che tratteneva CPU in attesa di
altre risorse. Non dimostrano un'accelerazione del job completo. Il beneficio
atteso principale è il contenimento della contesa e la disponibilità per altri
lavori; la separazione Virt migliora il confine architetturale. Il VLM resta la
componente dominante osservata. Nessuna ottimizzazione candidata è ancora in
esercizio.
