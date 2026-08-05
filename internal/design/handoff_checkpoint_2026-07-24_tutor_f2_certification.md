# Handover — Tutor F2, fonti pubbliche e certificazione F1

Data: 2026-07-24, 14:57 CEST  
Stato: implementazione F2 molto avanzata; certificazione di equivalenza F1
ancora aperta. **Non ritirare F1.**

## Ripresa rapida

Riprendere da questo documento e dal precedente
`internal/design/handoff_checkpoint_2026-07-23_tutor_f2_quicktour.md`.

Il worktree contiene moltissime modifiche e file non tracciati appartenenti
anche ad attività precedenti. Non usare rollback globali, non cancellare file
non riconosciuti e lavorare con diff mirati.

Obiettivo ordinato dall'utente:

1. costruire un corpus ampio di richieste umane che copra UI amministrativa,
   operazioni tipiche e conversazione;
2. verificare e correggere le risposte fino a renderle chiare, complete e
   orientate all'utente;
3. usare soluzioni semanticamente guidate, universali, senza branch per frase,
   dominio o executor;
4. ritirare le schede F1 soltanto dopo prova che F2 ne ha assorbito tutte le
   capacità con qualità almeno equivalente;
5. indicizzare automaticamente tutti i documenti pubblicati su `metnos.com` e
   usare chunk semantici post-retrieval;
6. mantenere Metnos autoconsistente: nessuna dipendenza sorgente da
   Suprastructure.

## Vincolo più recente dell'utente

> Non sovraingegnerizzare e fai scelte universali. Niente hardcoding né
> soluzioni ad hoc; se serve, fare prima un'analisi multidominio approfondita.

Questo vincolo ha portato a rimuovere due esperimenti provvisori:

- retry LLM basato su verifica lessicale delle radici della risposta;
- boost fisso `+0.08` per tutte le fonti `capability_catalog`.

Non reintrodurli: l'analisi multidominio descritta sotto ha dimostrato che il
boost peggiora il retrieval e il retry raddoppia la latenza reagendo al testo
anziché alla rappresentazione semantica.

## Architettura F2 corrente

Flusso:

```text
query
  -> mode semantico fast: EXPLAIN | ACT | MIXED | UNKNOWN
  -> catalogo SQLite firmato
  -> retrieval BGE-M3 + segnale lessicale derivato dalle fonti
  -> filtro audience
  -> chunk pertinenti + adiacenza strutturale bounded
  -> composer wise locale, grounded e senza strumenti
  -> risposta pubblica filtrata dal gateway LLM centrale
```

Proprietà preservate:

- nessun lessico di frasi Tutor, sinonimi o nomi executor nel routing;
- richieste operative e casi dubbi ricadono nel motore normale;
- il Tutor non possiede executor, browser, credenziali o rete;
- lacuna documentale e guasto tecnico sono esiti distinti;
- un guasto Tutor su una richiesta semanticamente informativa produce un
  messaggio esplicito di indisponibilità e non avvia operazioni;
- un solo scambio precedente può entrare nel contesto, in RAM, isolato per
  principal+conversazione e soltanto con guadagno semantico;
- mode usa il tier logico `fast`; composer usa `wise`; provider, modello,
  endpoint e parametri vengono risolti da `llm_router`/`llm_helpers`;
- il composer usa `output_policy="public"`, che rifiuta marker strutturali
  interni.

## Fonti e pubblicazione automatica

Implementato `runtime/published_docs.py`:

- inventaria ogni HTML indicizzabile nell'esatta radice pubblicata `docs/`;
- valida file regolare UTF-8, assenza di symlink, `html lang`, canonical HTTPS
  sotto `metnos.com` e gruppi `hreflang` reciproci;
- esclude `noindex` e blocchi `tutor-exclude`;
- `tutor/sources.toml` resta soltanto per fonti supplementari non pubbliche;
- aggiunta, modifica o cancellazione di un documento cambia il timbro e
  invalida il catalogo; i vettori invariati vengono riusati per hash;
- non viene eseguito crawling remoto di `metnos.com`.

Stato dell'inventario isolato osservato il 24 luglio:

- 77 pagine HTML indicizzabili;
- 2 redirect storici `noindex` esclusi dal Tutor;
- 3.734 unità complessive;
- 230 descrizioni generali manifest;
- 1.332 descrizioni di argomenti manifest;
- 72 segmenti della reference domini IT/EN;
- `PRAGMA integrity_check=ok` e firma Ed25519 valida.

I documenti lunghi sono segmentati per titoli/sezioni in unità di massimo 920
caratteri. Il retrieval passa solo i segmenti rilevanti e, entro un limite
chiuso, quelli adiacenti dello stesso documento.

## Reference pubblica dei domini

Creato `scripts/generate_domain_reference.py` e generate:

- `docs/it/domains.html`;
- `docs/en/domains.html`.

Contenuto:

- 26 domini canonici più l'utilità `_system`;
- 98 operazioni bundled derivate da manifest e contratti builtin;
- 54 esempi naturali curati per lingua/coppie di dominio;
- confini semantici, provider e disponibilità reale;
- layout verificato a 1.440 px e 390 px senza overflow.

Pubblicazione più recente nota:

- preview Cloudflare Pages: `https://9721ec49.mykleos.pages.dev`;
- URL pubblici: `https://metnos.com/it/domains` e
  `https://metnos.com/en/domains`.

Le pagine sono collegate da landing, Quick Tour, manuale, pagina Tutor e
sitemap.

## LLM centralizzato e reasoning

Modifiche principali:

- `runtime/llm_router.py`: `resolved_tier_spec()` e
  `tier_inference_policy()` sono la SoT di provider, modello, endpoint,
  temperatura e reasoning;
- `runtime/llm_helpers.py`: risoluzione provider centralizzata, normalizzazione
  unica e policy `raw|public`;
- Tutor non forza `think` o temperatura;
- configurazione locale corrente: Qwen 3.6 35B-A3B Q4_K_M/MTP su llama.cpp
  `:8080`, due slot;
- fast/middle/wise puntano oggi allo stesso backend fisico; `wise` resta un
  binding logico.

Una prova con `wise` e thinking attivo ha pubblicato il ragionamento nel normale
contenuto e consumato il budget prima della risposta. Perciò il binding locale
`wise` è temporaneamente `think=false`.

La versione installata di llama-server supporta:

```text
--reasoning-format deepseek
```

che dovrebbe separare `message.reasoning_content` da `message.content`. Il
nostro `LlamaCppProvider` legge già `reasoning_content`, ma il servizio usa oggi
il formato `auto`. Non modificare il servizio senza una prova isolata su un
corpus: il canale separato deve essere affidabile, non basato su tag o regex.

Nota: i comandi di certificazione hanno usato
`/opt/suprastructure/.venv/bin/python` soltanto come interprete contenente le
dipendenze; `PYTHONPATH=/opt/metnos/runtime` importa il codice Metnos corrente.
Non esiste dipendenza sorgente del Tutor da Suprastructure. Sarebbe comunque
preferibile rendere il runner installato/autoconsistente in un secondo momento.

## Prompt composer corrente

Prompt IT/EN `runtime/prompts/{it,en}/tutor_compose.j2`:

- ruolo esplicito: Tutor informativo di Metnos;
- deve cercare nell'intero contesto la risposta migliore e completa;
- integra fonti pertinenti, non si ferma alla prima;
- per richieste d'uso/configurazione apre con una richiesta naturale:
  `Chiedi a Metnos con una richiesta come quella di questo esempio: «…»`;
- per superfici UI dà prima percorso, route e contenuti visibili;
- da Telegram precisa che il percorso si trova nella chat web;
- usa un `COVERAGE_LEDGER` derivato dalle fonti capability selezionate come
  checklist interna, senza copiarne sintassi o identificatori tecnici.

Budget corrente del composer:

- `max_tokens=1536`;
- `max_query_chars=30000`.

Il retry LLM è stato rimosso: deve rimanere una sola composizione.

## Ranking: stato esatto al passaggio

File: `runtime/tutor/semantic.py`.

Configurazione corrente appena semplificata:

- soglia globale `METNOS_TUTOR_KNOWLEDGE_MIN`: default `0.70`;
- `top_k` default/cap: 16;
- distanza relativa: `adjusted(hit) >= adjusted(top) - 0.06`;
- nessun boost per `capability_catalog`;
- priorità fonte resta un tie-break piccolo (`priority / 10000`);
- segnale lessicale IDF e affinità del titolo sono derivati dai testi ammessi;
- massimo 3 frammenti `executor_manifest_argument`;
- per-documento: capability fino a 8 parti, documenti normali 2,
  executor manifest 1;
- espansione adiacente soltanto per documentazione pubblica primaria.

Questa politica è **corrente ma non ancora ricertificata end-to-end dopo la
rimozione del boost**. Eseguire i test prima di ulteriori modifiche.

## Analisi multidominio completata

È stato eseguito un batch di 108 query naturali: due esempi per ciascuno dei 26
domini, in italiano e inglese, usando come ground truth la sezione pubblica che
contiene l'esempio. Sono state confrontate combinazioni di boost capability e
banda relativa, su vettori reali del catalogo firmato.

Risultati più importanti:

| Boost capability | Banda | Recall fonte corretta | Hit medi | Hit capability complessivi |
|---:|---:|---:|---:|---:|
| 0,00 | 0,04 | 85/108 | 9,3 | 5 |
| 0,00 | 0,06 | 89/108 | 12,8 | 10 |
| 0,00 | 0,08 | 89/108 | 14,8 | 10 |
| 0,00 | 0,10 | 89/108 | 15,5 | 10 |
| 0,08 | 0,04 | 82/108 | 8,8 | 253 |
| 0,08 | 0,06 | 87/108 | 13,0 | 366 |
| 0,08 | 0,08 | 88/108 | 15,1 | 413 |
| 0,08 | 0,10 | 88/108 | 15,6 | 419 |

Conclusione sostenuta dai dati:

- il boost capability peggiora il recall e contamina il contesto: rimosso;
- la banda 0,06 mantiene il recall massimo osservato con meno contesto di
  0,08/0,10;
- il problema delle query panoramiche va risolto nella rappresentazione
  semantica delle fonti aggregate o con una selezione concettuale universale,
  non con un bias globale di tipo fonte.

Era stata avviata un'analisi dei 19 casi mancanti per dominio/lingua, ma
l'esecuzione è stata interrotta dall'utente prima di produrre output. Va
ripetuta; non esistono risultati parziali utilizzabili.

## Esperimenti scartati

### 1. Riserva forzata delle fonti capability

Una versione provvisoria riservava spazio alle fonti capability e portava
`top_k` a 24. Risolveva alcune panoramiche inglesi ma trascinava overview e
provider non pertinenti nelle query GitHub. Rimossa.

### 2. Boost fisso capability `+0.08`

Sembrava far salire la fonte overview per `What can you do in practice?`, ma il
batch multidominio ha mostrato una contaminazione enorme e recall peggiore.
Rimosso.

### 3. Retry di composizione con stem check

Una versione provvisoria estraeva le radici delle aree dal ledger, cercava le
prime sei lettere nella risposta e, se mancavano, invocava di nuovo il modello.
Ha portato i primi tre casi GitHub a PASS, ma:

- raddoppiava la latenza da circa 15-18 s a 30-35 s;
- reagiva alla superficie linguistica;
- produceva talvolta una copia tecnica del ledger;
- complicava il sistema senza correggere il retrieval.

Rimosso per esplicita richiesta di non sovraingegnerizzare.

## Corpus e gate automatici

Corpus:

- `tests/runtime/tutor/data/f2_human_certification.json`;
- gruppi `admin_ui`, `typical_operations`, `boundary`, `conversation`,
  `f1_equivalence`;
- include query conversazionali e refusi reali;
- comprende il caso osservato
  `Come si 8nseriscono le credenziali di una mailbox?`;
- il gate inglese GitHub accetta ora `folder|directory` al singolare o plurale:
  il precedente `folders|directories` produceva un falso negativo su una
  risposta che diceva `single folder`.

Ultimo gate statico eseguito dopo la semplificazione finale:

```text
80 passed
```

Suite:

```bash
python3 -m pytest \
  tests/runtime/tutor/test_tutor_f1.py \
  tests/runtime/tutor/test_tutor_f2_certification_corpus.py -q
```

Un gate più ampio eseguito prima degli ultimi esperimenti aveva prodotto:

```text
108 passed
```

su inventario pubblicato, reference domini, catalogo executor, Tutor e
virtualizzazione LLM.

## Certificazione F1: evidenze e stato

Runner:

```bash
env PYTHONPATH=/opt/metnos/runtime \
  /opt/suprastructure/.venv/bin/python scripts/certify_tutor_f2.py \
  --group f1_equivalence --f2-only \
  --catalog-dir /tmp/metnos-public-docs-catalog \
  --report /tmp/report.json
```

Il runner disabilita tutte le schede F1 e non invia le richieste fallite al
planner/executor.

Prime esecuzioni, prima delle correzioni:

- panoramiche: omissioni di hash, places, web, geolocation, provider e processi;
- GitHub: omissioni di pull request, directory, messaggi, credenziali, gate;
- Photos: omissioni di creazione indice, web, Google Photos, gate ed esempio
  naturale non sempre in apertura;
- Scheduled: omissioni di cosa/quando/canale/one-off/gate;
- dispositivi e change procedure non erano ancora stati raggiunti nel replay
  completo interrotto.

Con ledger e contesto ampliato, ma prima della semplificazione:

- panoramica IT: i primi tre casi sono arrivati a PASS;
- GitHub IT: i primi tre casi sono arrivati a PASS con il retry, ora rimosso;
- GitHub EN #4 era semanticamente corretto ma falliva perché diceva `folder`
  singolare; il corpus è stato corretto;
- il replay completo è stato interrotto, quindi non esiste una certificazione
  finale valida.

Non usare questi PASS per dichiarare F1 chiuso: appartengono in parte a una
politica poi scartata.

## Replay live già validi

Turn utili precedenti:

- `8b8bf20f81ed4e2e`: credenziali mailbox con refuso; esempio naturale, campi
  IMAP e nessun executor;
- `6bdfca5e24404dbb`: panoramica italiana completa prima degli ultimi cambi;
  nessun executor e nessun marker interno;
- `9374cf268d954530`: panoramica utile ma incompleta sulle credenziali;
- `b15a47160bd149c6`: lacuna dovuta alla vecchia soglia 0,75;
- `2d1baa7e8d754652`: prova reasoning leak da non ripetere in pubblico.

Il processo HTTP era stato riavviato dopo uno skew di moduli che produceva:

```text
ImportError: cannot import name
'is_unambiguous_canonical_action_start' from tutor.mode
```

Le modifiche più recenti di ranking/prompt/service **non sono state riallineate
con un nuovo restart live**. Non testare HTTP/Telegram assumendo che il processo
carichi il codice corrente; controllare PID/mtime e assenza di turni attivi.

## File principali toccati

- `runtime/published_docs.py`
- `runtime/tutor/sources.py`
- `runtime/tutor/catalog.py`
- `runtime/tutor/semantic.py`
- `runtime/tutor/mode.py`
- `runtime/tutor/compose.py`
- `runtime/tutor/service.py`
- `runtime/tutor/conversation.py`
- `runtime/tutor_boundary.py`
- `runtime/llm_router.py`
- `runtime/llm_helpers.py`
- `runtime/prompts/it/tutor_compose.j2`
- `runtime/prompts/en/tutor_compose.j2`
- `scripts/certify_tutor_f2.py`
- `scripts/generate_domain_reference.py`
- `tests/runtime/tutor/data/f2_human_certification.json`
- `tests/runtime/tutor/test_tutor_f1.py`
- `tests/runtime/tutor/test_tutor_f2_certification_corpus.py`
- `internal/roadmap/RM-0003-tutor-integrato.md`
- `decisions/0198-tutor-f2-knowledge-compiler-subsumes-f1.md`
- `CLAUDE.mutabile.md`

Molti di questi risultano non tracciati nell'attuale worktree; non significa
che siano dispensabili.

## Prossimo percorso raccomandato

### 1. Congelare il ranking corrente e misurarlo

Prima di altre modifiche:

```bash
python3 -m py_compile \
  runtime/tutor/semantic.py runtime/tutor/service.py runtime/tutor/compose.py
python3 -m pytest \
  tests/runtime/tutor/test_tutor_f1.py \
  tests/runtime/tutor/test_tutor_f2_certification_corpus.py -q
```

Poi ripetere l'analisi dei 19 miss su 108 query e produrre una tabella per
dominio/lingua con:

- score della fonte attesa;
- rank della fonte attesa;
- fonte top concorrente;
- causa: embedding, chunk, concetto lingua, cap top-k o corpus ambiguo.

### 2. Correggere la rappresentazione, non la query

Se i miss derivano da fonti aggregate o titoli diluiti, valutare una soluzione
universale come:

- rappresentazioni separate e firmate di titolo e corpo con max/late fusion;
- selezione per `concept_id` e diversificazione semantica dei documenti;
- chunk migliore alla generazione della fonte.

Non aggiungere:

- sinonimi o frasi speciali;
- boost per un provider/domain;
- retry basati sulle parole attese dal test;
- branch per `Cosa sai fare?` o `What can you do?`.

Qualsiasi modifica deve essere confrontata sulle 108 query multidominio più
UI, boundary e azioni.

### 3. Certificare a gruppi brevi

I gruppi lunghi sono stati più volte interrotti. Eseguire:

```bash
for id in f1-overview f1-github f1-photos f1-scheduled \
          f1-devices f1-changes f1-changes-restricted; do
  env PYTHONPATH=/opt/metnos/runtime \
    /opt/suprastructure/.venv/bin/python scripts/certify_tutor_f2.py \
    --group f1_equivalence --id "$id" --f2-only \
    --catalog-dir /tmp/metnos-public-docs-catalog \
    --report "/tmp/${id}.json"
done
```

Valutare anche qualità umana, non solo substring:

- esempio naturale in apertura quando richiesto;
- niente identificatori executor/ledger esposti;
- percorso UI prima degli interni;
- completezza senza enciclopedia irrilevante;
- lingua coerente;
- nessun executor invocato.

### 4. Solo dopo i gruppi F1

- eseguire l'intero corpus `admin_ui`, `typical_operations`, `boundary`,
  `conversation`;
- verificare zero false-steal delle azioni;
- riavviare HTTP in quiescenza e ripetere turni live;
- fare un turno Telegram auditabile;
- aggiornare RM-0003/ADR 0198 con i risultati finali;
- ritirare F1 soltanto se ogni gate è verde.

## Criterio di chiusura

F1 può essere ritirato soltanto quando:

1. tutte le query F1 IT/EN sono servite da F2-only con qualità equivalente;
2. le procedure critiche conservano route, passi e stop condition;
3. il corpus operativo ha zero sottrazioni al planner;
4. forme nuove e conversazionali restano corrette senza liste di frasi;
5. HTTP e Telegram producono turni auditabili;
6. la soluzione resta buona sull'intera analisi multidominio.

Fino a quel momento lo stato corretto è:

```text
F2 operativo e promettente; equivalenza F1 non dimostrata; F1 non ritirato.
```
