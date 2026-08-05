# Handover — Tutor F2, Quick Tour e pubblicazione

Data: 2026-07-23  
Stato: ripreso e ampliato dopo i turni di regressione; restano la nuova
certificazione live e la pubblicazione dell'ultimo insieme documentale.

## Ripresa rapida

Riprendere da questo file senza ricostruire il lavoro precedente. Il worktree è
molto sporco per refactor e pulizie richiesti dall'utente: **non ripristinare,
non cancellare e non includere indiscriminatamente modifiche estranee**.

Obiettivo corrente:

1. chiudere la validazione live del Tutor F2;
2. verificare il deploy pubblico del Quick Tour e dei redirect;
3. aggiornare le evidenze in RM-0003/ADR 0198;
4. non implementare F3/F4: per ora sono progettazione di roadmap.

## Aggiornamento successivo del 23 luglio

- Eliminato l'intero lessico Tutor `help.*`: ingresso e modo sono semantici;
  restano soltanto controlli strutturali e bypass del verbo canonico.
- Aggiunto un singolo scambio Tutor in RAM per principal e conversazione; viene
  usato soltanto se migliora materialmente il retrieval e non entra nella
  telemetria.
- Separati gli esiti del compositore: lacuna documentale e indisponibilità
  tecnica non condividono più lo stesso messaggio.
- Il prompt risponde alle parti sostenute invece di rifiutare l'intera domanda.
- Per richieste d'uso o configurazione il compositore apre con «Chiedi a
  Metnos con una richiesta come quella di questo esempio: …» e una richiesta
  naturale adattabile nella lingua corrente; i dettagli tecnici vengono dopo.
  La scelta è semantica, non lessicale.
- Le pagine Settings sono ora superfici tipizzate in un registro condiviso con
  la sidebar. Il Tutor dà percorso e contenuti visibili prima degli interni;
  da Telegram specifica che il percorso si trova nella chat web di Metnos.
- `runtime/services_registry.py` alimenta direttamente la conoscenza di
  Settings > Servizi, senza duplicare nomi o stato live.
- Aggiunta la guida bilingue IMAP/SMTP non Google. Installer, runtime e manifest
  condividono ora il binding cifrato `smtp_<account>`; `account="all"` scopre
  gli account dal vault.
- Corpus da certificare dopo il riavvio: 2.091 unità (1.047 IT, 1.044 EN),
  1.862 utente e 229 amministrative; le 26 fonti `ui_surface` derivano dal
  registro condiviso con la sidebar.
- Gate mirato corrente: 229 pass Tutor, prompt, installer e mailbox; 76 pass
  manifest e backend mail.

## Decisioni dell'utente da preservare

- Il Tutor è una funzione importante, non accessoria: deve avere un capitolo
  dedicato nel Quick Tour e rilievo nella documentazione.
- F2 deve sussumere F1. Le schede curate non devono restare un requisito rigido
  né richiedere manutenzione al variare degli executor.
- Retrieval semantico tramite embedding; niente affinity hardcoded o grandi
  store di stringhe per risposte deterministiche.
- La composizione della risposta può usare il modello locale.
- Documentazione pubblica soltanto IT/EN. Per altre lingue si recupera il
  contenuto inglese e il modello risponde nella lingua corrente.
- Esempi F3/F4 vanno mostrati nel capitolo Tutor, ma non devono essere presentati
  come capacità già disponibili né entrare nel corpus F2.
- I file del Quick Tour devono avere un nome stabile **senza versione**; la
  versione, se necessaria, vive soltanto nel contenuto/metadati.

## Tutor F2 implementato

Componenti principali:

- `tutor/sources.toml`: fonti esplicite e documentazione bilingue;
- `runtime/tutor/sources.py`: compiler di unità di conoscenza da manifest
  ammessi e documenti pubblici; parser HTML limitato e supporto
  `tutor-exclude`;
- `runtime/tutor/catalog.py`: catalogo SQLite schema 3, firma, sostituzione
  atomica e riuso incrementale dei vettori;
- `runtime/tutor/semantic.py`: retrieval unificato knowledge+cards, fallback
  per concetto `lingua richiesta -> lingua base -> en -> altra variante`;
- `runtime/tutor/mode.py`: classificatore locale chiuso e bypass delle azioni;
- `runtime/tutor/service.py`: risposta F2, procedure letterali, compositore
  locale e fallthrough sicuro;
- `runtime/ui_surfaces.py`: fonte canonica di sidebar, breadcrumb, route e
  riepilogo visibile delle pagine Settings;
- `runtime/tutor/telemetry.py`: include anche `tutor_source_ids`;
- prompt IT/EN in `runtime/prompts/{it,en}/tutor_*.j2`;
- integrazione HTTP/Telegram tramite `runtime/tutor_boundary.py` e daemon.

Correzione importante già applicata: l'esclusione HTML usa una profondità
numerica. La precedente pila basata sui nomi dei tag terminava troppo presto su
`div` annidati e lasciava entrare esempi futuri nel corpus. Sul corpus reale:
`future_leaks=0`.

Ultimo conteggio osservato: 713 unità (358 IT, 355 EN), 520 utente e 193
amministratore; manifest executor inclusi dinamicamente dal catalogo ammesso.

## Roadmap e decisioni

- `internal/roadmap/RM-0003-tutor-integrato.md` è stato riscritto: F2 corrente,
  F1 transitorio/sussunto, F3 e F4 progettati in dettaglio.
- `internal/roadmap/README.md` aggiornato.
- Nuovo ADR: `decisions/0198-tutor-f2-knowledge-compiler-subsumes-f1.md`.
- Aggiornati `decisions/0000-INDEX.md`,
  `decisions/anti-regression-index.md` e `CLAUDE.mutabile.md`.

F3 progettata: probe tipizzati e read-only verso stato reale, capsule con
provenienza/TTL, nessuna nuova autorità operativa.  
F4 progettata: raccolta locale e minimizzata dei gap, clustering, mappa del
debito di conoscenza, replay controfattuale e ritiro automatico delle schede
dopo equivalenza.

## Quick Tour e documentazione pubblica

Master canonici rinominati:

- `docs/it/Metnos_QuickTour.html`
- `docs/en/Metnos_QuickTour.html`

Derivati:

- `docs/it/Metnos_QuickTour.pdf`
- `docs/en/Metnos_QuickTour.pdf`

Il Quick Tour ha ora 12 capitoli e 27 pagine A4. Il capitolo 4 è dedicato al
Tutor. Contiene architettura e capacità F2 correnti, comportamento linguistico
ed esempi esplicitamente futuri per F3/F4. I blocchi futuri hanno classe
`tutor-exclude`.

La scena duplicata e meno significativa su `synt` è stata sostituita con una
domanda reale al Tutor sulle capacità Excel. Il capitolo dedicato non è quindi
un duplicato della scena: spiega fonti, confini, i18n, sicurezza e roadmap.

Nuove pagine:

- `docs/it/architecture/tutor.html`
- `docs/en/architecture/tutor.html`

Aggiornati gli indici architetturali e le landing IT/EN. I link primari delle
landing puntano ai PDF; l'HTML resta il master accessibile.

Naming stabile propagato a:

- generatore `scripts/build_quick_tour_pdf.py`;
- test di pubblicazione;
- fonte del catalogo Tutor;
- canonical/hreflang/sitemap;
- landing IT/EN;
- documentazione del master di stampa;
- eccezioni `.gitignore` per i due master HTML.

In `docs/_redirects` i vecchi nomi `Metnos_QuickTour_v1.{html,pdf}` e gli alias
Myclaw/Mykleos puntano ai nuovi nomi stabili con 301. Non rimuoverli.

## Validazioni già completate

- `56 passed` sulla suite Tutor prima della rinomina.
- Dopo rinomina: `61 passed` su:
  - `tests/runtime/http/test_quicktour_pdf_publication.py`
  - `tests/runtime/tutor/test_tutor_f1.py`
  - `tests/runtime/executors/test_executor_catalog_docs.py`
- `305 passed, 360 subtests` su controlli i18n/prompt selezionati.
- `82 passed` su manifest/loader/standard selezionati.
- `compileall` Tutor completato senza errori.
- Retrieval BGE reale verificato in francese, tedesco e spagnolo con margine
  netto rispetto ai contenuti non pertinenti.
- Compositore locale verificato: contesto inglese e `lang=fr`, risposta francese
  in circa 1,6 s.
- PDF rigenerati con Chromium:
  - IT 2.161.487 byte;
  - EN 2.153.225 byte;
  - entrambi 27 pagine, A4, tagged, PDF 1.4, senza JavaScript.
- Ispezione visuale pagine Tutor IT/EN: nessuna sovrapposizione o clipping.
- Il test di pubblicazione verifica anche assenza dei vecchi file locali e
  presenza dei redirect di compatibilità.

## Deploy eseguito

`./deploy.sh` è terminato con successo:

- 82 executor generati in 2 lingue;
- 14 file caricati, 76 già presenti;
- `_headers` e `_redirects` caricati;
- deployment Cloudflare Pages:
  `https://04b2d91a.mykleos.pages.dev`.

La successiva verifica su `https://metnos.com` non è stata completata: il primo
tentativo `curl` è fallito soltanto per DNS bloccato nel sandbox; il tentativo
con rete elevata è stato interrotto dall'utente per richiedere questo handover.
Il deploy stesso non è fallito.

## Prossime azioni esatte

### 1. Verifica pubblicazione

Con rete disponibile:

```bash
curl -fsSI --max-time 20 https://metnos.com/it/Metnos_QuickTour.pdf
curl -fsSI --max-time 20 https://metnos.com/en/Metnos_QuickTour.pdf
curl -fsSI --max-time 20 https://metnos.com/it/architecture/tutor.html
curl -fsSI --max-time 20 https://metnos.com/en/architecture/tutor.html
curl -sSIL --max-time 20 https://metnos.com/it/Metnos_QuickTour_v1.pdf
```

Atteso: 200 sui quattro URL nuovi e 301 dal vecchio PDF al nuovo. Controllare
anche che le landing servite contengano `Metnos_QuickTour.pdf`.

### 2. Catalogo di produzione e servizio

Prima del restart controllare che non vi siano turni attivi. Poi:

1. riavviare con prudenza `metnos-http.service` per compilare il catalogo F2;
2. controllare journal e health;
3. riavviare `metnos-telegram-daemon.service` soltanto dopo health HTTP verde;
4. verificare nel catalogo SQLite di produzione schema, firma e conteggi.

Non riavviare durante un turno in esecuzione.

### 3. Prove live Tutor

Usare domande informative, non azioni:

- `È possibile leggere e scrivere file Excel?`
- `Pairing dei dispositivi: vorrei capirne la logica.`
- `Che cosa fa read_messages e quali limiti ha?`
- in francese: `Comment Metnos peut-il m'aider avec mes e-mails ?`

Verificare:

- nessun piano/executor per una domanda Tutor;
- risposta nella lingua corrente;
- source IDs in telemetria;
- fallthrough sicuro per domanda non coperta;
- una richiesta operativa reale continua a entrare nel planner e non viene
  sottratta dal Tutor.

Per Telegram è sufficiente prima validare il boundary e il daemon senza inviare
messaggi esterni. Una prova outbound reale va fatta solo se esplicitamente
richiesta.

### 4. Chiusura documentale

Dopo le prove live:

- inserire turn ID, tempi, conteggi e risultati in RM-0003 e ADR 0198;
- marcare chiuso il gate live F2, non le fasi F3/F4;
- eseguire nuovamente la suite Tutor e i test i18n toccati;
- comunicare che F2 è operativo mentre F3/F4 restano roadmap persistente.

## Comandi di controllo utili

```bash
python3 -m pytest tests/runtime/tutor/test_tutor_f1.py \
  tests/runtime/http/test_quicktour_pdf_publication.py \
  tests/runtime/executors/test_executor_catalog_docs.py -q
python3 -m compileall -q runtime/tutor runtime/tutor_boundary.py
python3 scripts/build_quick_tour_pdf.py
pdfinfo docs/it/Metnos_QuickTour.pdf
pdfinfo docs/en/Metnos_QuickTour.pdf
```

## Nota sul worktree

Sono presenti centinaia di modifiche e spostamenti relativi ad attività
precedenti (refactor test, cleanup, executor, client, documentazione). Non sono
un effetto del solo Tutor. Lavorare con diff mirati e non usare comandi
distruttivi o rollback globali.
