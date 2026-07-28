# Lente leiden

Riferimenti a riga = `internal/roadmap/RM-0001-conoscenza-utente-locale.md`
(revisione 26/7/2026, 2832 righe, letta per intero) salvo diversa indicazione.
Tutti i comandi citati sono stati eseguiti in sola lettura il 26/7/2026.

## Verdetto in tre righe

**Non serve.** La partizione Leiden alimenta una sola cosa — proposte revisionabili
(report/ChangeIntent) — e quel canale esiste già in produzione con raggruppamento
deterministico (ADR 0180/0185, `telos_proposals_store`); la scala provata dell'istanza
(17 archi nel grafo di co-attivazione dopo 2 mesi; tetto di ~200 memorie/utente) è di
2-4 ordini di grandezza sotto quella in cui Leiden si distingue da componenti connesse;
l'alternativa più semplice a parità di scopo è: componenti connesse sulla proiezione
positiva + grouping per chiavi e sequence mining che il documento stesso impone come
baseline (r. 1399). Che cosa si perde togliendolo: detto in fondo, ed è poco.

## Rilievi

### 1. La partizione non alimenta nessuna decisione che non abbia già un produttore
[PROVATO] Tutto ciò che il documento fa con le comunità: UC-07 «Leiden può raggruppare
comunità offline» → «report o ChangeIntent revisionabile» (r. 118-121); «Leiden può
produrre soltanto report o ChangeIntent revisionabili» (r. 1605); F9 «comunità come sole
proposte; adapter verso candidati workflow» (r. 1907-1908); «risultato soltanto candidate»
(r. 1407); «Una comunità Leiden non crea di per sé una relazione epistemica» (r. 1200).
Nessuna query online (r. 1495), nessuna promozione, nessun campo dati consuma la comunità.
Il canale «ripetizioni → proposta revisionabile» esiste già: ADR 0185 (turno costoso
ripetuto → autopath ombra; lacuna ricorrente → change_intent PROPOSED) e ADR 0180
(adapter telos cluster-head), con raggruppamento deterministico per firma in produzione:
`runtime/telos_proposals_store.py:172` (`annotate_clusters`), `:233` (`cluster_score`),
`:246` (`recompose_clusters`).
[OPINIONE] Conseguenza: per il criterio stesso del mandato — un algoritmo di comunità che
non alimenta nessuna decisione nuova è ornamento — Leiden qui è ornamento; in più è una
seconda strada verso lo stesso artefatto (proposta revisionabile), rilievo §7.2 e di
sorgente unica di verità che nessuna delle review precedenti ha sollevato.

### 2. La scala provata dell'istanza rende l'esito di F9-Leiden già scritto — dal documento stesso
[PROVATO] Dati reali (comandi read-only): 7.023 turni in 61 giorni (jsonl in
`PATH_TURNS`, 2026-05-27→2026-07-26), 16.441 step, 105 tool distinti, 230 step
`act_sites`/`login_sites`; 84 executor firmati su disco; 2 utenti (`users.db`); 4 persone;
5 autopath; 76 fastpath; 188 righe `executor_stats`. Il documento fissa ~200 memorie
attive per utente (r. 1075-1077) e deduplica l'esperienza per tupla indipendente
(r. 1172-1174).
[IPOTESI] Proiezione a un anno al tasso corrente (che include traffico di sviluppo e
bench, quindi per eccesso): ~42.000 turni; grafo memoria utente nell'ordine delle
centinaia di nodi (tetto 200/utente × 2 utenti, più candidati); esperienza del pilot
`act_sites` nell'ordine di 10^3 episodi pre-dedup; grafo workflow con ~10^2 nodi
executor. Nessun grafo supera 10^3-10^4 nodi.
[PROVATO] Il documento stesso: «Leiden non viene promosso su grafi piccoli» (r. 1412).
Conseguenza: F9-Leiden è una fase numerata il cui esito negativo è deducibile oggi dai
dati e dalla regola di promozione del documento; mantenerla come fase con harness
obbligatorio è spesa di specifica a risultato noto.

### 3. Il precedente interno: il grafo di co-attivazione reale ha 17 archi dopo due mesi
[PROVATO] Metnos possiede già un grafo di co-occorrenza fra executor: mnestoma
(`runtime/mnestoma.py`, tabelle `mnests`/`events`, traversal `walk` r. 624, `top_k_*`
r. 606-615). Sull'istanza di produzione (`/opt/metnos/workspace/.mnestoma/mnest.sqlite`):
17 `mnests` (archi), 1.034 `events`, 84 `canonical_query_log` — dopo ~2 mesi di uso a
~115 turni/giorno.
[OPINIONE] Conseguenza: il grafo relazionale che questa istanza produce davvero non
matura nemmeno i dati per porre il problema delle comunità; 17 archi si ispezionano a
occhio. È il riscontro empirico che RM-0001 non porta: nessuna stima di nodi/archi in
tutto il documento (vedi «non trovato»).

### 4. Il problema che Leiden risolve è un fenomeno da grafi grandi
[PROVATO fonte] Traag, Waltman, van Eck 2019 (fonte già in §24, r. 2460-2461; abstract
arXiv:1810.08473): Louvain produce «fino al 25% di comunità mal connesse e fino al 16%
disconnesse», Leiden «garantisce comunità connesse» ed è più veloce. Sono percentuali
misurate su reti empiriche grandi, ed è la garanzia di connessione il valore distintivo
di Leiden su Louvain.
[OPINIONE] Su un grafo di centinaia di nodi la connessione di ogni gruppo si verifica
direttamente in microsecondi, e le componenti connesse — deterministiche, 20 righe di
BFS o `networkx` già installato — danno gruppi connessi per costruzione. A questa scala
la proprietà per cui Leiden esiste non è il fattore che decide nulla.

### 5. Contraddizione interna: Leiden è «livello di retrieval» e insieme «mai nella query»
[PROVATO] §9.0 lo pone come livello 5 della scala e afferma «Ogni livello implementa la
stessa interfaccia di retrieval» (r. 638-647); §19.5 lo mette nel confronto «Exact, FTS5,
dense, RRF, grafo e Leiden ricevono stessi input, stesso k, stesso budget di output e
stesso reader» (r. 2233-2234). Ma §13.2: «Leiden non fa parte della query online»
(r. 1495-1497). Una partizione offline non risponde a una query con k risultati: il
documento non definisce mai come Leiden implementerebbe l'interfaccia di retrieval che
gli attribuisce due volte.
Conseguenza: l'ablation di §22 punto 16 (r. 2367) è inapplicabile così com'è scritta per
il ramo Leiden; o si definisce un uso retrieval (espansione per cluster — mai descritta)
o Leiden va tolto dalla scala e dal confronto a stesso k.

### 6. La parte portante del «grafo tipizzato» non può aspettare F9
[PROVATO] Le relazioni sono «la fonte canonica di supports, contradicts e supersedes» e
`status` è una loro materializzazione nella stessa transazione (r. 1052-1054, tabella
`memory_relations` fra le «minime» a r. 1116); il reconciler di F4 «classifica
EvidenceRelation e MemoryRelation» (r. 1301-1304). Eppure §17.1 colloca «relations e
Leiden in F9» (r. 1969-1970) e §25.1 assegna `relations.py` a F9 (r. 2499).
Conseguenza: il contenuto davvero portante di F9 (gli archi tipizzati) è dovuto a F1/F4
per il funzionamento della macchina a stati; ciò che resta esclusivo di F9 è l'espansione
a un salto e Leiden. Chiarito questo, «F9 = grafo tipizzato» è un'etichetta che fa
sembrare strutturale una fase il cui contenuto proprio è quasi solo l'ornamento del
rilievo 1.

### 7. Costo di dipendenza: Leiden non c'è nell'ambiente, e non arriva gratis
[PROVATO] `python3 -c "import leidenalg"` → ModuleNotFoundError; `import igraph` →
ModuleNotFoundError; `networkx` 3.6.1 presente con `louvain_communities` nativo, ma
`leiden_communities` è solo API di dispatch: eseguirlo dà «NotImplementedError:
'leiden_communities' is not implemented by 'networkx' backend». Quindi Leiden vero
richiede `leidenalg`+`python-igraph` (estensioni C) o un backend esterno: una dipendenza
nuova per un modulo a esito pre-scritto (rilievo 2). `sklearn` 1.8.0 e `scipy` 1.17.1
sono già presenti per ogni alternativa agglomerativa/a soglia.
[OPINIONE] Il tempo di calcolo, viceversa, non è un argomento in nessuna direzione: a
10^2-10^3 nodi qualunque algoritmo termina in millisecondi. Il costo reale è dipendenza,
harness (rilievo 8) e superficie di specifica, non la CPU.

### 8. Il modulo più caro di F9 è l'harness anti-stocasticità, che i metodi semplici non richiedono
[PROVATO] Leiden è stocastico e il documento lo sa e lo presidia onestamente: seed e
ordine input deterministici, più seed e perturbazioni degli archi, stabilità VI/ARI,
confronto obbligatorio senza Leiden (r. 1403-1409), «Seed fisso dimostra ripetibilità,
non stabilità» (r. 1411), harness in F9 (r. 1906) e verifica «cluster instabili»
(r. 1915-1916).
[OPINIONE] Quindi il §7.9 è salvo, ma al prezzo massimo: l'apparato multi-seed/VI/ARI
esiste SOLO per sorvegliare una proprietà (l'instabilità della partizione) che componenti
connesse, grouping per chiavi e sequence mining non hanno affatto, essendo deterministici
per costruzione. Si costruisce il pezzo più costoso della fase per governare un difetto
che le alternative non introducono.

### 9. I workflow sono sequenze ordinate; la proiezione positiva butta via l'ordine
[PROVATO] La proiezione dichiarata esclude «contradicts, supersedes e archi diretti»
dagli archi di comunità (r. 1400-1402), ma l'arco d'esperienza che definisce un workflow
è `followed_by` (r. 1193), cioè direzionale e ordinato; e il bersaglio di UC-07 sono
«sequenze verificate di executor» (r. 116). La baseline dichiarata dal documento —
«grouping per chiavi e frequent sequence mining» (r. 1399, 1905) — è lo strumento
canonico e deterministico proprio per le sequenze.
[OPINIONE] Conseguenza: per lo scopo workflow, Leiden non è solo sovradimensionato, è lo
strumento della categoria sbagliata: una comunità di co-occorrenza non conserva l'ordine
che rende un workflow proponibile. La baseline non è un termine di paragone da battere:
è la soluzione.

## Che cosa toglierei / che cosa aggiungerei

**Toglierei** [OPINIONE, sui riscontri 1-9]:
- Leiden da F9 e dai punti che lo citano come stadio: livello 5 di §9.0 (r. 642), blocco
  Leiden di §12.6 (r. 1397-1413), voce nel confronto a stesso k di §19.5 (r. 2233-2234),
  harness e verifiche Leiden di F9 (r. 1906, 1912-1916); §22 punto 16 ridotto a
  «embedding e grafo». F9 si rinomina «relazioni tipizzate e un salto», con gli archi
  canonici anticipati alla fase che già li richiede (rilievo 6).
- La menzione di Leiden nella risposta Reddit di §28 (r. 2827): promette in vetrina il
  modulo meno difendibile del documento.

**Aggiungerei** [OPINIONE]:
- come livello massimo del raggruppamento offline: componenti connesse deterministiche
  sulla proiezione positiva (zero dipendenze nuove, deterministiche, comunità connesse
  per costruzione); per l'eventuale raggruppamento su embedding, soglia di similarità o
  agglomerativo con `sklearn` già installato;
- UNA riga di condizione di riapertura misurabile, al posto dell'intera macchina F9-Leiden:
  «si riapre la valutazione di un algoritmo di comunità se la proiezione positiva supera
  ~10^4 nodi CON una componente gigante (le componenti connesse non discriminano più),
  oppure se esistono N casi contati in cui grouping per chiavi e sequence mining
  producono raggruppamenti sbagliati documentati». Oggi entrambe le condizioni sono
  lontane ordini di grandezza (rilievi 2-3).

**Che cosa si perde togliendo F9-Leiden** (dovere di onestà del verdetto «non serve»):
(a) la capacità di scoprire, su grafi grandi e densi, comunità ben connesse che non
condividono chiavi né contiguità sequenziale — un caso che questa istanza, ai tassi
misurati, non produrrà negli anni coperti dalla roadmap; (b) una voce di vetrina in §28;
(c) nulla dei casi core: §23 dichiara già la roadmap completabile senza grafo e Leiden
(r. 2417-2421) e UC-07/08 non sono gate (r. 2423-2426). Le relazioni tipizzate e il
salto singolo NON si perdono: restano, anticipate dove servono.

## Ciò che ho cercato e NON ho trovato

- [PROVATO] Una decisione, un campo dato o un consumatore che usi la comunità oltre
  report/ChangeIntent: assente — le 27 occorrenze di «Leiden» in RM-0001 (grep con
  righe) portano tutte a proposta revisionabile, harness o rinuncia; nessuna tabella di
  §11.5/§11.6 ha un campo comunità.
- [PROVATO] `leiden`/`louvain` nel codice: 0 occorrenze in `runtime/` (grep).
- [PROVATO] Una stima di nodi o archi attesi, o una soglia numerica di scala, in tutto
  RM-0001: assente — esiste «budget di nodi e tempo» (r. 1408) senza alcun numero.
- [PROVATO] Una metrica di qualità dei raggruppamenti oltre la stabilità VI/ARI:
  «utilità end-to-end» è nominata (r. 1916) ma mai definita; il documento ammette che
  «modularità alta non dimostra utilità» (r. 1411-1412) senza dire che cosa la dimostri.
- [PROVATO] Preferenze W2 registrate sull'istanza: `users.db` reale contiene 2 utenti e
  nessuna tabella `user_prefs` ancora creata — il corpus del profilo parte oggi da zero,
  coerente con la stima di scala del rilievo 2.
- [PROVATO] Nelle lenti già scritte, la catena scala+alternative+costo su Leiden:
  `lente_ambizione.md` propone di degradare F9 ad appendice ma come [OPINIONE] di
  ambizione («contribuisce zero comprensione utente»), senza dati d'istanza, senza
  alternative e senza costi di dipendenza; §7.2 del documento (r. 489) e §27.2 (r. 2754)
  tengono Leiden come edge dietro ablation ma nessuna review ha verificato che l'esito
  dell'ablation sia già deducibile dalla scala reale.
