# Audit primario Swafra per RM-0001

**Data:** 2026-07-26

**Scopo:** verificare direttamente il precedente Swafra prima di usarlo come
evidenza progettuale per RM-0001

**Esito:** utile come catalogo di ipotesi retrieval; non adottabile come
sottosistema di conoscenza utente e non probante per Leiden o qualità
end-to-end

## 1. Fonti congelate

- sito: <https://swafra.vercel.app/>;
- repository: <https://github.com/kunal12203/swafra>;
- commit verificato:
  [`24dba18a4194aef0cb0d6d6c68cf46e6fcbf2da7`](https://github.com/kunal12203/swafra/tree/24dba18a4194aef0cb0d6d6c68cf46e6fcbf2da7);
- file letti: `README.md`, `BENCHMARK.md`, `CLAUDE.md`, `SKILL.md`,
  `swafra/engine.py`, `swafra/server.py`, `bench/run_eval.py` e
  `bench/results.json`;
- landing page letta direttamente il 26 luglio 2026.

Il repository è stato clonato in una directory temporanea e rimosso dopo
l'audit. Nessun codice Swafra è stato importato in Metnos.

## 2. Che cosa implementa Swafra

Swafra espone sei tool MCP: aggiunta, ricerca, recupero con graph walk,
attraversamento del grafo, inventario delle sorgenti e cancellazione. Il motore
persiste tre file JSON, crea chunk, embedding e archi e fonde BM25, cosine,
overlap di entità/date e trigrammi di caratteri.

Il contratto agentico invita a:

- salvare proattivamente preferenze, contesto, decisioni e documenti;
- recuperare contesto all'inizio di ogni sessione;
- non dichiarare assenza di memoria prima di avere interrogato lo store.

Questo spiega la buona semplicità d'uso, ma sposta sull'LLM la decisione su che
cosa costituisca memoria e quando recuperarla.

## 3. Differenze normative rispetto a RM-0001

Nel commit verificato non sono presenti contratti equivalenti a:

- `PrincipalContext`, associazione autenticata o query owner-bound;
- separazione affidabile fra clausola utente, testo dell'assistente, pagina,
  allegato e risultato tool;
- categorie sensibili, scope d'applicazione o controllo capability/consenso;
- evidenza originaria distinta dal chunk derivato;
- conflitto cross-store, correzione temporale o reconciler deterministico;
- journal di cancellazione separato, epoch, replay o quarantena del restore;
- commit atomico dei tre file JSON.

Due dettagli di codice rafforzano i requisiti di RM-0001:

1. gli archi cross-session sono salvati con `source_id=None`; `delete_source`
   filtra gli archi per il `source_id` cancellato, quindi gli archi
   cross-session verso chunk rimossi possono restare pendenti;
2. `add_knowledge` elimina prima i vecchi chunk dello stesso `source_id` e
   successivamente cerca quegli stessi chunk per impostare `superseded_by`;
   nel flusso corrente quell'insieme è vuoto e non riconcilia versioni.

Questi sono rilievi circoscritti al commit, non una valutazione complessiva
dell'autore o delle versioni future.

## 4. Verifica del benchmark

### 4.1 Claim pubblici e artefatto

La landing page dichiara «94.7% on LongMemEval — the best out there».
`BENCHMARK.md` specifica correttamente che si tratta di session-level retrieval
recall, non di accuratezza end-to-end, ma nello stesso documento mostra come
output atteso `99.6% recall_all@10`.

`bench/results.json` dichiara:

| Campo | Valore versionato |
|---|---:|
| casi totali | 500 |
| valutati | 470 |
| abstention saltati | 30 |
| `k` configurato | 10 |
| `recall_any` | 1,0 |
| `recall_all` | 0,9957446808510638 |
| risultati usati per caso | minimo 28, massimo 46, media 35,387 |

### 4.2 Errore semantico di `@10`

Nel runner la sequenza è:

1. `get_context(question, k=10)` produce `retrieved_sids`;
2. `recall_at_k(retrieved_sids, answer_sids)` usa l'intera lista;
3. soltanto nel JSON viene scritto `retrieved_sessions = retrieved_sids[:10]`.

L'artefatto è quindi sufficiente per ricalcolare il vero top-10, perché
conserva in ordine i primi dieci e le `answer_sessions`:

| Metrica sui primi dieci conservati | Conteggio | Valore |
|---|---:|---:|
| tutte le answer session presenti | 434/470 | 92,34% |
| almeno una answer session presente | 464/470 | 98,72% |

Il `99,57%` versionato corrisponde a 468/470 calcolato sulla lista larga, non
a `recall_all@10`.

### 4.3 Drift di riproducibilità

L'engine nello stesso commit calcola
`target_sources=max(k, int(total_sources*0.15))`. Con le 45-53 sessioni
dichiarate e `k=10`, il risultato corrente non può superare dieci sorgenti.
Non può quindi produrre l'artefatto da 28-46 risultati: almeno engine e
risultato appartengono a comportamenti differenti.

Il runner imposta inoltre `SCIMAP_EMBED_BACKEND=local` e lo registra nella
configurazione del risultato, ma `_get_embedder` non legge la variabile: tenta
`fastembed` e usa il fallback soltanto in caso di errore. L'artefatto non prova
quale backend abbia realmente prodotto i vettori.

### 4.4 Limiti di questo audit

- Non è stato scaricato il dataset da circa 280 MB e non è stata rieseguita
  l'intera prova da 500 casi.
- Il ricalcolo usa l'artefatto dettagliato versionato dal progetto.
- Non viene affermato che 92,34% sia il risultato dell'engine corrente: è il
  top-10 ricostruibile dall'esecuzione storica conservata.
- Non è disponibile nello stesso commit un'ablation che isoli Leiden tenendo
  fissi corpus, chunk budget, retriever e `k`.

## 5. Decisioni trasferite a RM-0001

Accolte come ipotesi da misurare:

- località completa;
- diversità per sorgente entro il medesimo `k`;
- confronto futuro di retrieval ibrido contro exact+FTS5.

Respinte come scorciatoie:

- importare Swafra o dare allo MCP autorità sul profilo;
- salvare indiscriminatamente ciò che l'LLM ritiene riutilizzabile;
- recuperare un dossier a ogni inizio sessione;
- usare il 94,7% o il 99,6% come prova end-to-end;
- introdurre Leiden o graph walk senza ablation Metnos.

Nuovo vincolo di misura: ogni metrica `@k` usa la lista materialmente limitata
a `k` prima del calcolo e riporta separatamente retrieval, risposta finale,
astensione e danni.
