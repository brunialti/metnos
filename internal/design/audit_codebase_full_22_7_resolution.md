# Chiusura audit codebase 22/7/2026

Stato verificato sul codice e sullo stack live il 22 luglio 2026. Questo file
non modifica l'audit originale: registra decisioni, prove e i pochi reperti
che, dopo verifica, non erano codice morto.

## Esito per area

- **T1 / onesta' d'esito**: chiusi BE-1, EN-1/2/3/4/5, SC-1/5, LG-2 e
  LO-1. Errori, parziali e truncation attraversano il confine runtime senza
  diventare successi vuoti; i reject loader sono visibili e auditabili.
- **T2 / identita'**: dialoghi HTTP e callback Telegram sono owner-bound;
  callback promoter/scheduler passano dal pairing e dalle autorizzazioni; le
  capability cross-device sono firmate.
- **T3 / fail-closed**: cookie revocati, firma loader, enable gate, L6 e
  importer rifiutano su errore o su contesto non coperto. L'override firma
  per sviluppo non apre il profilo di produzione.
- **T4 / governance**: dedupe puo' ritirare soltanto executor synth e mai core
  o handcrafted; `in_progress` resta PROPOSED; un artefatto `synthesized` ha
  un solo owner, il promoter autoregolato (evaluator, grace e kill-switch), e
  non genera una seconda autorizzazione umana illusoria.
- **T5 / Rust remoto**: STARTED write-ahead, runtime Python descritto e
  firmato, cache executor riverificata integralmente, anti-replay persistente,
  4xx transient ritentati, scratch CSPRNG, ACL per-invocazione e sandbox minima
  firmata. Autorita' filesystem Linux/Windows dalla stessa funzione.
- **HTTP/canali**: proxy foto SSRF-resistant, pairing e approval atomici,
  TurnEventLog snapshot-safe, pool turni bounded con backpressure e fairness,
  dashboard I/O fuori dall'event loop, registrazione/discovery coerenti,
  offset Telegram ack-after-handle.
- **Scheduler**: countdown finito nello scheduler centrale, allineamento JIT
  legacy, `partial` fuori dal circuit breaker, one-shot non rifirati, nomi
  builtin derivati dalla SoT, esiti nightly compositi. Il solo costruttore di
  produzione e' nel daemon HTTP protetto da `ProcessLock`; non e' stato
  aggiunto un secondo lease DB che avrebbe cambiato crash/retry semantics.
- **Backend**: provider frontier dalla SoT, OAuth Google condiviso e con
  backoff, parity memory/sqlite, `UID EXPUNGE` mirato, status geo preservato.
- **Learning/growth**: proto-mnest decadono fino al purge, dedupe protetto,
  adapter synt non accetta stati incompleti, `proposals_unified.py` e i test
  esclusivamente accoppiati al vecchio hub sono stati rimossi.
- **Loader/standard**: admission skill e codice PROPOSED fail-closed,
  boundary verb coperto, default di esecuzione e metadati remoti vincolanti;
  firme dei 99 cataloghi verificate.

## Reperti verificati e deliberatamente conservati

- `scheduler_v2/migrate_v1.py` non e' vestigiale: `recurring_tasks.db` e'
  ancora scritto da `register_user_task` ed e' fonte dei mandati. La migrazione
  idempotente ripara al boot una registrazione v2 rimasta incompleta.
- I prefilter `*_v2` sono strategie selezionabili da `METNOS_PREFILTER` (una
  configurazione installata usa `token_flat_v2`); non sono moduli morti.
- `email_metnos.delete` non e' esposto da un executor corrente, ma implementa
  il contratto backend standard e ora non usa piu' EXPUNGE folder-wide. La sua
  rimozione non dava beneficio e avrebbe ridotto l'elasticita' futura.
- Le classi omonime in moduli diversi (`Executor`, `Verdict`, ecc.) hanno
  responsabilita' e namespace distinti. Un rename pubblico avrebbe introdotto
  rischio senza correggere una divergenza runtime.

## Prove finali

- Python: **4869 passed, 33 skipped, 366 subtests passed**.
- Rust: **48 passed**; `cargo check --target x86_64-pc-windows-gnu` verde.
- Integrita': `git diff --check` verde; **99/99** manifest/contratti firmati
  verificati; compileall runtime verde.
- Deploy: daemon HTTP e Telegram/agent-server riavviati e attivi; discovery,
  `/agent/register`, health e descrittore runtime firmato verificati live;
  nessun errore nei journal post-start. Readiness composita finale
  `ok=true`, `ready=true`, `quiescent=true`, catalogo locale/live 115/115 e
  contratto HTTP/Playwright allineato. I due failed state oneshot storici del
  vecchio pilot sono stati azzerati senza attivare target o watchdog.
- Windows reale: `PC-ROBERTO`, client 0.2.25, invocazione read-only
  `inv-18c4aab25413a819bb5524ce` completata `ok=true` in 3,97 s con sandbox
  effettiva `appcontainer`.
