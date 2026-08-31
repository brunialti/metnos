# Consegna a me stesso — ripresa RM-0008 dopo cambio di contesto

> Scritta il 31 agosto 2026, fine sessione, per l'agente che riprende (io, con
> contesto nuovo). Contiene tutto: stato, compito, vincoli, ciclo avversariale.
> **Metnos e' in funzione.** Non e' un'emergenza: e' un lavoro da fare bene.

## 1. Leggi prima queste tre

- `internal/design/handover_fusione_produzione_31_8_2026.md` — il tentativo di
  fusione, i due blocchi, le trappole gia' pagate.
- `internal/roadmap/RM-0008-porta-unica-nascita-executor.md` §23.44-§23.48 —
  l'ultimo tratto, incluso il verbale del guasto che ho causato.
- **`internal/design/diagnosi_avvio_nascita_31_8_2026.md` — LEGGI QUESTO PER
  PRIMO.** Diagnosi misurata del 31/8 pomeriggio: gli ostacoli sono **tre**, non
  due, il secondo **non e' quello descritto qui sotto**, e il terzo non era
  noto. Contiene anche le istruzioni operative passo per passo.

## 2. Stato verificato (31/8, ore 13:20)

| dove | cosa | commit |
|---|---|---|
| `/opt/metnos` | installazione VIVA, albero pulito | `14ea7179` |
| `/tmp/metnos-rm0008-g6` | albero di lavoro | `3708c101` |
| `/tmp/metnos-rm0008-a-only` | pubblicazione, ramo `main` | `b6e95e39` |
| `/tmp/metnos-prova-fusione` | **fusione dei 539 commit gia' fatta e verificata** | `2cb6eac4` |

Servizi: `http`, `durable-worker`, `telegram-daemon` attivi; zero unit `failed`.

Punti di ritorno: tag `pre-fusione-31-8-2026`; copia del DB i18n in
`…/scratchpad/i18n-prima.sqlite`; i 47 file dell'installazione in
`…/scratchpad/backup-47/`; permessi originali in `…/scratchpad/permessi-prima.txt`.

## 3. Il compito

Sbloccare gli ostacoli alla messa in produzione. Erano dati per due; misurati,
sono **tre**, e il terzo e' quello che decide il lavoro.

- **Primo, gia' risolto e verificato**: `_verify_posix_directory`
  (`runtime/executor_birth_secure_fs.py:855-873`) rifiuta una radice
  `historical_public` con `mode & 0o022`, e `/opt/metnos/runtime` e' `775`.
  `chmod g-w` sblocca `open_distribution_sources_v1()`.
- **Secondo — CORRETTO DALLA MISURA del 31/8 pomeriggio.** Scrivevo qui «non e'
  un bit di permesso: e' nel modello dei ruoli». **E' sbagliato.** E' lo stesso
  identico bit di permesso del primo, applicato ai **file** invece che alla
  directory: i 20 file del catalogo di contesto sotto `/opt/metnos/runtime` sono
  `0o664`, e `_verify_posix_file` rifiuta `mode & 0o022` come
  `_verify_posix_directory`. Riprodotto e isolato file per file; il primo
  rifiutato e' `executor_standard.py`. Vedi la diagnosi, §2 O3.
- **Terzo — NON ERA NOTO, ed e' quello che conta.** Rimossi i primi due,
  l'avvio si ferma su `birth_prepared_set_mismatch`: l'insieme preparato
  congela le impronte della distribuzione al commit `3eeb4b1b` (28/8), mentre
  la distribuzione e' a `cac6d7e4` (30/8). L'insieme e' diventato stantio
  cinque ore e mezza dopo essere stato creato, per un commit ordinario, e il
  provisioner **non ha alcun percorso per rifarlo** (`already_installed`).
  Vedi la diagnosi, §2 O4/O7/O8.
- **Non c'e' un quarto ostacolo**: rimossi i tre,
  `require_birth_runtime_before_workers()` completa in 0,9 s (diagnosi, §2 O6).

L'insieme preparato esiste (`~/.config/metnos/birth/`, creato il 30/8) e i suoi
permessi sono corretti: `755` per l'integrita', `700` per il confidenziale — il
problema non e' li', e' nella distribuzione che l'insieme descrive.

## 4. Autorizzazione

Roberto e' l'autorita' di nascita e autorizza l'operazione, **disagi inclusi**.
Questo significa: non fermarsi a chiedere il permesso a ogni passo. NON
significa essere sbrigativi — i vincoli sotto restano, perche' non erano modi
per chiedere consenso, erano il modo di non rompere.

## 5. Vincoli non negoziabili, pagati con due interruzioni

1. **Lavora sempre in copia, mai sull'albero da cui gira il servizio.** Avevo
   fatto la prova in copia e poi ho abbandonato la disciplina proprio nel gesto
   che contava.
2. **Prova che il codice nuovo SI AVVII** prima di metterlo in produzione:
   `require_birth_runtime_before_workers()` con `PATH_RUNTIME` reale. Le suite
   verdi non dicono che il servizio parte — nessuna suite avvia il server con
   le radici vere.
3. **Verifica CHE COSA stai misurando.** Ho provato l'ipotesi del permesso
   eseguendo il controllo da un worktree, dove `PATH_RUNTIME` punta al worktree
   stesso: il `chmod` sulla produzione non poteva contare, e ho concluso
   «ipotesi sbagliata» quando era giusta.
4. **Per rimettere su lo stack usa `systemctl --user start metnos.target`**, mai
   le singole unit: avviare `metnos-stack-ready` a mano lo manda in timeout e fa
   scattare la quarantena, che spegne tutto. L'ho fatto, ed e' la seconda
   interruzione.
5. **L'HTTP e' un servizio UTENTE.** `systemctl --user restart
   metnos-http.service`. Il `failed` che si vede senza `--user` e' il residuo
   dell'unit di sistema, vecchia e disabilitata: non e' un guasto.

## 6. Ciclo avversariale — sulla DIAGNOSI, non sul codice

I quattro giri con Codex della sessione precedente hanno rivisto codice gia'
scritto e hanno funzionato. Ma i due guasti del 31/8 **non sono nati da codice
sbagliato**: sono nati da diagnosi date per buone senza misura. Un revisore che
contesta il codice non li avrebbe fermati; uno che contesta l'ipotesi PRIMA
dell'azione, si'.

Quindi, prima di qualunque azione su `/opt/metnos`, scrivi un documento con:

- **(a) cosa ho OSSERVATO** — solo fatti, con il comando che li ha prodotti;
- **(b) cosa ho INFERITO** — dichiarato come inferenza, non come misura;
- **(c) quale misura mi smentirebbe** — decisa PRIMA di eseguirla.

Roberto lo passa a Codex. Agisci solo dopo che l'inferenza e' stata contestata e
regge, oppure e' stata corretta.

**Criterio per farlo scattare, in una riga:** ogni volta che stai per toccare la
produzione basandoti su un «quindi» invece che su un numero misurato.

Il ciclo sul CODICE serve solo se la correzione tocca il modello dei ruoli del
filesystem sicuro: e' codice di sicurezza scritto da un'altra sessione, e non
deve guardarlo un agente solo.

## 7. Cosa manca a RM-0008 nel complesso

Il gruppo 6 e' chiuso e certificato. Del gruppo 7 e' fatto il primo passo piu'
i cinque pezzi dell'involucro, provati in isolamento e provati insieme.
Restano: **F4** (commit di nascita, il cuore), **F5** (pre-esercizio, cache,
epoche — inizia solo dopo una soglia di ammissioni reali), **F6** (conservazione
e certificazione). Non e' «un passo dalla fine»: F4 e' una fase intera.

## 8. Una cosa piccola, per Roberto

`sudo systemctl reset-failed metnos-http.service` toglie l'allarme fantasma
dell'unit di sistema. Chiede la password: il sudo senza password copre
start/stop/restart, non `reset-failed`.
