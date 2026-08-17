# Brief — confronto fra l'estrazione di intento attuale e V23lite

Documento operativo per un agente che riprende il lavoro. Autosufficiente.
Leggere PRIMA `internal/design/handover_prompt_ontologia_11_8_2026.md`.

## L'obiettivo vero, che non è il punteggio di un banco

Migliorare la fase del proposer lavorando sull'estrazione di intento (o prima),
**per eliminare codice che si porta dietro dizionari di parole e che è per
costruzione non conforme all'i18n**, e farlo **senza degradazione di qualità**.

Tutto il resto — riscritture di prompt, ablazioni, campioni ciechi — è derivato.
Il criterio di successo è quello sopra, non «quante strutture valide».

## Che cosa c'è già

`V23lite` è un analizzatore di richieste a LLM: legge la frase in **qualunque
lingua** e produce una struttura JSON vincolata da schema (temperatura 0, seme 42).
È lavoro ombra, **non cablato**: `grep -rl v23lite runtime/ engine/` è vuoto.

Misurato su 120 frasi reali mai usate per la messa a punto: **116/120 valide**,
contro un tetto raggiungibile di 117. Dettagli e disciplina di misura nella
consegna. L'unico intervento che ha guadagnato è `riparo_ruolo.py`, sei righe
deterministiche **senza nessuna parola dentro**.

## Che cosa manca, ed è il motivo di questo brief

1. **Non esiste una linea di base.** L'estrattore attuale
   `runtime/intent_extractor.py` non è mai stato eseguito sulle stesse 120 frasi.
   Senza quel numero, «senza degradazione di qualità» è **non verificato**.
2. **116/120 misura la validità della struttura, non la correttezza della rotta.**
   Il validatore controlla che la struttura sia ben formata e che verbo e oggetto
   esistano nel catalogo; non che siano *quelli giusti*. Al proposer serve la
   seconda cosa.

## Compito 1 — la linea di base

Scrivere `internal/tools/request_analysis_lab/misure_11_8/confronto_intento.py`.

- Campione: le stesse identiche 120 frasi. Si ottengono con
  `import prova_cieca as P; queries, _ = P.sample()`. **Non cambiare il seme.**
- Ramo **attuale**: `from intent_extractor import extract_intent`, firma
  `extract_intent(query, llm_call)`. Il chiamante di produzione è
  `runtime/agent_runtime.py:6021`, che passa `_llm_call_fast`. Ritorna
  `{"verb":…, "object":…, "confidence":…}`, più `actions` per le richieste
  composte e `implicit_actions`.
- Ramo **nuovo**: V23lite nella configurazione migliore misurata, cioè prompt di
  riferimento più `riparo_ruolo.py` (vedi `prova_ruolo.py`, braccio «I»). La rotta
  da confrontare è verbo e oggetto dei record con `role == "request"`, nell'ordine.
- Uscita: per ogni frase, rotta attuale, rotta nuova, e se coincidono. Riepilogo:
  accordo, disaccordo, e i casi in cui uno dei due non produce nulla.

## Compito 2 — quanto dizionario si porta dietro l'attuale

È il cuore dell'obiettivo. `intent_extractor.py` contiene **scorciatoie
deterministiche a lessico** che scavalcano il modello, per esempio
`undo.intent_bypass`, `system.status_query`, `health.section_focus` (righe 150-180).
Sono esattamente il codice legato alla lingua che si vuole eliminare.

Misurare, sulle 120 frasi: **quante risposte dell'attuale vengono da una
scorciatoia a lessico invece che dal modello**, e per ognuna se V23lite arriva
alla stessa rotta **senza nessun lessico**. Ogni caso in cui ci arriva è una riga
di dizionario che si può cancellare. Elencarle una per una.

Censire inoltre l'intera catena, non solo `intent_extractor.py`: `fast_path.py`,
`prefilter.py` e i concetti in `detection_lexicon` che servono al solo
instradamento. Quantificare quante voci sono in gioco.

## Compito 3 — la correttezza della rotta, non la validità

Dove i due rami divergono, decidere quale ha ragione. Non esiste un oro: si
giudica caso per caso, con il vocabolario chiuso di `runtime/vocab.py` e i confini
del §2.2 di `CLAUDE.md` come riferimento. Un disaccordo dove il nuovo sbaglia è
una degradazione e va contato come tale; nessuna indulgenza.

Riportare tre numeri: accordo, nuovo migliore, nuovo peggiore.

## Vincoli, non negoziabili

- **Sola lettura sulla produzione**: niente scritture su store reali, nessun
  riavvio di servizio, nessuna modifica al runtime. Niente commit.
- **Non modificare mai** `unified_query_bench_v23_checkpoint.py`: le toppe
  sostituiscono attributi del modulo a runtime.
- **Una misura per volta sulla GPU.** Due misure insieme si corrompono a vicenda:
  la banda di rumore passa da ±1 a ±2 su 120. Verificare che nulla stia girando
  prima di partire.
- **Ogni toppa deve fallire rumorosamente** se non trova il testo che si aspetta.
- **Mai `pgrep -f` o `pkill -f`** con un modello che compare nella riga di comando
  che lo contiene: si autocorrisponde. Filtrare per processo con
  `pgrep -x python3` più `/proc/<pid>/cmdline`.
- Serve `llama-server` vivo: `curl -s -m 5 http://127.0.0.1:8080/health`.
- Commenti e docstring del codice **in inglese**; analisi e documenti in italiano.

## Criterio di successo

L'obiettivo è raggiunto quando si può scrivere, con i numeri in mano:

> «Sulle stesse 120 frasi reali, il nuovo percorso instrada altrettanto bene o
> meglio dell'attuale, e permette di cancellare N voci di dizionario legate alla
> lingua, elencate una per una.»

È **superato** se la qualità migliora invece di restare pari.
