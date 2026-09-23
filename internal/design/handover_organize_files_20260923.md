# Handover `organize_files` a RM-0008

Data: 23 settembre 2026. Candidato finale del codice:
`codex/organize-files-handoff` a `be88c585`, base `605a6010`.
Questo documento consegna sviluppo e prove isolate; non certifica
installazione, pubblicazione o funzionamento dei servizi vivi.

## Contenuto consegnato

- `38362151` registra il lessico `organize` secondo le convenzioni Metnos.
- `85bbf5a1`, `935c52a2` e `4d530701` introducono e consolidano il contratto
  runtime generale per piani congelati: effetti firmati, grant monouso legato
  a owner/actor/canale/turno/argomenti, form HTTP/Telegram, callback senza nuovo
  planning, outbox durevole, redazione del token e pulizia dei dialoghi
  terminali.
- `5658f1aa` rende esatto per default il routing dell'azione, evitando affinity
  implicite verso verbi diversi.
- `bb5d2665`, `e5dae2d6`, `3da784a0`, `b1a1f75b` e `309ee661` implementano e
  irrobustiscono l'executor Linux `organize_files`: piano content-addressed,
  WAL durevole, recovery apply/reverse, dirfd con `O_NOFOLLOW`, verifica delle
  identita' del filesystem, fallback transazionale per gli `EXDEV` introdotti
  dai bind mount della sandbox e ricevute che non rivelano il bearer.
- `be88c585` aggiunge l'E2E HTTP isolata dell'intero percorso pubblico.

La specifica
`internal/design/organize_files_interaction_20260919.md` descrive il confine
tra `organize` e `move`, il consenso sui due canali e i limiti intenzionali.
Il runtime non contiene eccezioni sul nome `organize_files`: l'autorita' deriva
dal manifest firmato. Il token di conferma e' `runtime_resolved`, non compare
in preview, HTML, header, Telegram, turn log, server log o journal di undo e
non costituisce da solo una concessione applicativa.

## Invarianti dell'executor

La preview e' di sola lettura, inventaria l'insieme completo e persiste un
piano content-addressed. `max_preview` limita soltanto cio' che viene mostrato.
Apply richiede lo stesso token, gli stessi scope e il grant runtime; lega
radici, device, inode, dimensione, mtime, mode, uid e gid. Ogni effetto e'
preceduto da un journal sincronizzato. Una ripresa con lo stesso token
riconcilia cio' che e' accaduto prima di un crash o SIGKILL.

Gli spostamenti preferiscono `renameat2(RENAME_NOREPLACE)` tra directory gia'
aperte. Se la sandbox presenta due bind mount dello stesso filesystem come
montaggi distinti e il rename restituisce `EXDEV`, viene usato un trasferimento
copy-verify-stage-delete journaled: contenuto e metadati sono verificati e ogni
confine di crash e' recuperabile. Un vero passaggio tra device distinti resta
bloccato in preview. Una collisione tardiva non viene cancellata.

Le cancellazioni verificano immediatamente prima di `unlink` identita' e
`st_nlink == 1`. Proprieta', mode, tempi e tutti gli xattr devono superare un
round-trip nello stesso parent prima dell'effetto; altrimenti il piano fallisce
chiuso. Il rename preserva gli hardlink; il fallback copy e la deduplicazione
di un file con piu' link sono rifiutati.

Il reverse accetta soltanto la ricevuta registrata, ne verifica digest e
identita', scrive l'intento prima degli effetti e persiste `undone`. Un secondo
reverse restituisce zero effetti; un apply successivo all'undo e' rifiutato
senza riscrivere la ricevuta.

## Prove finali eseguite

Sul candidato finale sono verdi:

```text
124 passed
  executor, bootstrap, Executor Standard, loader fail-closed e firma

154 passed, 5 subtests passed
  consenso congelato, undo, HTTP/form, dialoghi, routing e i18n

1 passed
  E2E HTTP isolata organize_files
```

L'E2E avvia un server Metnos e un endpoint modello deterministico entrambi
isolati e locali. Attraversa query pubblica, routing, preview troncata, form,
grant, apply del piano completo, deduplicazione, tre move, replay dello stesso
POST e comando pubblico di undo. Verifica i byte finali e il ripristino
byte-exact, prova che l'undo non richiami il modello, controlla la forma del
bottone Telegram e cerca il bearer in tutti gli output e log rilevanti.

Le prove specifiche coprono anche processi realmente uccisi dopo WAL e ai
quattro confini del fallback copy, recovery apply/reverse, collisioni tardive,
sostituzione same-bytes con inode diverso, sostituzione radice, symlink,
hardlink/nlink tardivo, xattr, filesystem diverso, write brevi/EINTR, replay e
broker `undo_last_turn` reale con catalogo firmato.

`runtime/sign.py verify executors/organize_files` e' verde con digest:

```text
sha256:ad5e72b7c30bf42df46319cf89e626746f9c34dd9c40e8bc292f8c7c5a829d54
```

Firma e stato lingua sono stati generati in layout di authoring con radici
dati/stato temporanee. Non e' stato scritto il contract store produttivo.
Anche `git diff --check`, compilazione sintattica e `PRAGMA integrity_check`
del seed i18n sono verdi.

## Limiti espliciti

- La v1 e' solo Linux e locale/server; non esegue provider o device remoti.
- Le directory di destinazione e i parent generati dal template devono gia'
  esistere. Non vengono creati implicitamente.
- Un vero passaggio tra filesystem e la cancellazione di file con piu'
  hardlink falliscono chiusi. Se non e' provabile il round-trip dei metadati,
  il piano non viene applicato.
- I riferimenti a output completi di query concluse restano il TODO generale
  `QUERY-OUTPUT-001`; non esiste memoria privata dell'executor.
- Nessuna prova sul servizio vivo, pubblicazione, deploy o restart e' stata
  eseguita da questo ramo.

## Passi residui assegnati a RM-0008

1. Integrare i commit per contenuto sulla base finale e rigenerare firma e
   `manifest.lang_state.json` se cambia qualunque byte di manifest o codice.
2. Rieseguire le suite sul ramo integrato e la stessa E2E isolata.
3. Completare l'accettazione specifica Telegram sul trasporto reale, inclusi
   consegna al canale originario, scadenza, canale/turno errati e outbox
   `ambiguous` dopo process-death.
4. Registrare separatamente l'accettazione sul percorso reale `/agent/turn`.
   Non usare il servizio corrente per validare questo worktree e non
   riavviarlo durante l'integrazione.
5. Risolvere R-003 prima di qualunque pubblicazione. `runtime/sign.py publish`
   non e' una procedura valida e la firma di authoring qui presente non mette
   l'executor in esercizio.

Il candidato va rifiutato se una prova richiede di allargare il lessico,
saltare il form, ricostruire una lista troncata, trasformare un effetto in
`no_effect`, ritentare una consegna Telegram ambigua o perdere la ricevuta di
undo.
