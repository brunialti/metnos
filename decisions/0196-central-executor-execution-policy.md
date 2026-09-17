---
id: 0196
title: Politica centrale di esecuzione degli executor con concorrenza esplicita
date: 2026-07-20
status: accepted
area: runtime | executor | synt
related: [0051, 0057, 0100, 0103, 0193, 0194, 0195]
---

# 0196 - Politica centrale di esecuzione degli executor

## Contesto

Gli executor esistenti hanno semantica, ordinamento, limiti e profili di effetto
diversi. Aggiungere thread in modo trasversale avrebbe potuto cambiare l'ordine
degli effetti, moltiplicare pool interni e introdurre risultati non equivalenti.
Allo stesso tempo metriche, retropressione e limiti hardware non devono essere
replicati in ogni executor o nei tre percorsi che generano manifest.

## Decisione

`runtime/executor_scheduler.py` e' l'unico punto di applicazione della politica
comune. Ogni invocazione, locale o remota, attraversa il suo percorso sincrono:
metriche e retropressione sono universali, mentre il valore predefinito resta
strettamente seriale. Il pool trasversale e' inoltre disattivato per
impostazione predefinita.

Il manifest puo' dichiarare una sezione chiusa `[execution]`:

```toml
[execution]
effect = "unknown"
parallelism_class = 0
resource_class = "default"
concurrency_key = "none"
equivalence_gate = "unverified"
```

Le classi sono una richiesta di budget, sempre limitata dall'hardware:

1. `0`: nessun thread trasversale;
2. `1`: concorrenza moderata;
3. `2`: concorrenza alta;
4. `3`: massimo controllato dal motore e dall'hardware.

La classe non concede autorita' e non equivale a `read_only`. Un executor
`create_only`, `reversible` o `mutating` potra' usare una classe positiva se
dichiara una chiave d'isolamento concreta, riceve a runtime l'identita' della
risorsa e supera prove ermetiche di equivalenza, idempotenza/collisione e
postcondizione. `unknown`, `interactive`, una verifica non completata o una
identita' d'isolamento assente degradano sempre alla classe `0`.

Le prove di nascita con `equivalence_runs` confrontano strutturalmente l'output
sequenziale con piu' esecuzioni concorrenti. L'ammissione resta esplicita per
singolo executor: nessun executor esistente diventa concorrente soltanto per
effetto di questa decisione.

## Generazione governata

I tre percorsi di generazione restano separati perche' hanno cicli di vita
diversi, ma condividono `runtime/generated_executor_contract.py`. Questa sola
autorita' produce intestazione standard e politica seriale e valida il TOML
prima della scrittura. I modelli locali ricevono istruzioni leggibili e possono
costruire implementazioni ricche nella zona prevista; non possono cambiare
identita', ciclo di vita, I/O, autorita' o politica di esecuzione di base.

Se il codice generato parallelizza elementi indipendenti usa esclusivamente
`executor_helpers.assigned_workers()`, cosi' il numero di worker resta deciso
dal motore centrale e i risultati sono ricomposti nell'ordine di ingresso.

## Conseguenze

- Una modifica ai limiti centrali raggiunge tutte le invocazioni senza riscrivere
  gli executor.
- Codice, input, output e capacita' degli executor esistenti restano invariati.
- I pool interni legacy migrano solo dopo benchmark e prova di equivalenza per
  executor; fino ad allora conservano il comportamento storico.
- La concorrenza puo' migliorare la velocita', ma non puo' ampliare permessi,
  eliminare ordinamento osservabile o nascondere fallimenti.
- Il trasporto remoto riceve lo stesso budget assegnato nel payload firmato;
  il client lo applica all'ambiente del sottoprocesso come per l'esecuzione
  locale, e `assigned_workers()` lo limita ulteriormente alle CPU visibili sul
  dispositivo destinatario.

## Evidenza iniziale

- lint dello standard sull'intero albero handcrafted: zero finding;
- test di scheduler, retropressione, isolamento, generazione e equivalenza;
- integrazione al choke-point `agent_runtime.invoke_executor`, senza modifica
  dei manifest e senza attivazione della concorrenza in produzione.

## Estensione LRE: bersagli multipli verificati (17 settembre 2026)

Il pacchetto registrato di una capacità può fornire un
`concurrency_targets_resolver`. Dopo la verifica del contratto congelato,
il ponte raccoglie le identità canoniche di **tutti** i bersagli mutabili
dell'invocazione. Sono fatti di ammissione locali: non modificano il piano,
non viaggiano al dispositivo, non derivano da dichiarazioni libere del job
e non aumentano classe firmata, autorità, budget o risorse.

Lo scheduler usa `InvocationIsolation`: prenotazione atomica dell'insieme,
esclusione di sovrapposizioni anche parziali e, per percorsi, di antenati e
discendenti. Un'identità mancante conserva l'esclusione seriale; anche una
chiamata ordinaria senza identità esclude quelle partizionate dello stesso
executor. Le richieste disgiunte possono procedere, quelle in conflitto
rispettano l'ordine di arrivo. Errori e scadenze liberano ogni prenotazione.
Il registro conserva soltanto detentori e attese correnti, limitati dai posti
centrali; massimo 256 identità e 64 KiB per insieme.

Il confine resta **il singolo scheduler di processo e la stessa identità di
executor**, non un blocco distribuito del filesystem. Il pacchetto deve
dimostrare stabilità delle identità, copertura di ogni scrittura, assenza di
alias e sicurezza delle eventuali scritture condivise. Operazioni esterne,
altri executor e altri processi richiedono le loro garanzie preesistenti;
questa estensione non le inventa.

Primo consumatore: la fase di analisi dell'indice immagini verifica la ricevuta
del gruppo e restituisce i percorsi effettivi dei checkpoint per sorgente.
Parti immutabili, archivio dei contenuti e consultazione della generazione
precedente conservano le protezioni esistenti. Scoperta, riduzione e
pubblicazione non ricevono questa prova e restano seriali. Il nucleo generale
non contiene nomi di domini o controlli specifici sulle immagini.

Le capacità centrali possono essere configurate all'avvio nella sezione
`[execution_resources]` del `runtime.toml` privato; le variabili già esistenti
restano prioritarie. Valori espliciti invalidi riducono la risorsa a uno;
nessuna modifica di semafori in uso. I valori predefiniti restano invariati.
Sono posti logici per processo, non core o scelta CPU/GPU. Le capacità dei
singoli lavoratori LRE sono invece requisiti per tentativo: non vanno
moltiplicate per il numero di lavoratori.

Prove: quattro scrittori indipendenti realmente concorrenti, sovrapposizioni
e percorsi con prefissi ambigui, ritorno seriale, scadenze, integrità dell'indice
e riuso dei checkpoint, ripresa della stessa revisione e degli stessi risultati.
La prova tra release usa processi Python distinti e archivi sintetici isolati:
non certifica da sola il rilascio firmato né la configurazione produttiva.
