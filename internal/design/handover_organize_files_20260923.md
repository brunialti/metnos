# Handover `organize_files` a RM-0008

Data: 23 settembre 2026. Ramo candidato:
`codex/organize-files-handoff`, base `605a6010` con lessico `38362151`.
Questo documento consegna sviluppo e prove; non certifica installazione,
pubblicazione o funzionamento dei servizi.

## Contenuto consegnato

- `85bbf5a1` introduce il contratto generale runtime per piani congelati:
  effetti firmati, grant monouso sul payload finale, form-only HTTP/Telegram,
  callback diretta senza nuovo planning, redazione del token, sandbox sulle
  capability effettive e outbox durevole.
- `bb5d2665` introduce l'executor Linux `organize_files`, manifest attivo nel
  solo inventario di authoring, stato lingua e firma offline. Il piano e il
  reverse usano journal write-ahead durevoli; move e delete sono ancorati a
  dirfd e identità di filesystem; la ricevuta è byte-exact e lo stato undo è
  terminale.
- La specifica
  `internal/design/organize_files_interaction_20260919.md` descrive il confine
  tra `organize` e `move`, il consenso sui due canali e i limiti intenzionali.

Il runtime non contiene controlli sul nome `organize_files`: l'autorità deriva
dal manifest firmato. Il token di conferma è `runtime_resolved`, non viene
esposto ai log/audit e non costituisce da solo una concessione applicativa.

## Invarianti dell'executor

La preview è di sola lettura, inventaria l'insieme completo e persiste un piano
content-addressed. `max_preview` limita solo ciò che viene mostrato. Apply
richiede lo stesso token, gli stessi tre scope e il grant runtime; lega radici,
device, inode, dimensione, mtime, mode, uid e gid. Ogni effetto è preceduto da
un journal sincronizzato. Una ripresa con lo stesso token riconcilia ciò che è
accaduto prima di un crash o SIGKILL.

Gli spostamenti usano `renameat2(RENAME_NOREPLACE)` tra directory già aperte
con `O_NOFOLLOW`; una collisione tardiva non viene cancellata. Le cancellazioni
verificano immediatamente prima di `unlink` identità e `st_nlink == 1`.
Proprietà, mode, tempi e tutti gli xattr devono superare un round-trip nello
stesso parent prima dell'effetto; altrimenti il piano fallisce chiuso. Move
preserva gli hardlink; la deduplicazione di un file con più link è rifiutata.

Il reverse accetta soltanto la ricevuta registrata, verifica digest e identità,
scrive il proprio intento prima degli effetti e persiste `undone`. Un secondo
reverse restituisce zero effetti; un apply successivo all'undo è rifiutato
senza riscrivere la ricevuta.

## Prove di sviluppo eseguite

Sul commit executor sono passate 71 prove:

```text
python3 -m pytest \
  tests/runtime/executors/test_organize_files.py \
  tests/runtime/executors/test_executor_standard.py \
  tests/runtime/infra/test_loader_fail_closed.py -q
71 passed
```

Le 28 prove specifiche includono processi realmente uccisi dopo WAL e dopo
l'effetto, recovery apply/reverse, collisione creata dopo la preview,
sostituzione same-bytes con inode diverso, sostituzione radice, symlink,
hardlink e nlink tardivo, xattr, filesystem diverso, replay dopo undo e broker
`undo_last_turn` reale. Quest'ultima prova usa radici utente temporanee, forza
il layout authoring, carica `organize_files` con `load_catalog(verify=True)` e
non sostituisce né catalogo né reverse.

Prima del checkpoint runtime erano passate 82 prove mirate, 110 prove
standard/metadata/sandbox/orchestration e 3 prove HTTP selezionate. Una
verifica indipendente del coordinatore ha riportato 245 prove verdi
(63 + 25 + 157) e `git diff --check` verde. Il test isolato di catalogo ha
caricato il manifest firmato; `runtime/sign.py verify` ha verificato firma e
digest. Firma e stato lingua sono stati generati in layout authoring usando
radici dati/stato temporanee, senza scrivere nel contract store produttivo.

## Limiti espliciti

- La v1 è solo Linux e locale/server; non esegue provider o device remoti.
- Le directory di destinazione e i parent generati dal template devono già
  esistere. Non vengono creati implicitamente.
- Move tra filesystem e cancellazione di file con più hardlink falliscono
  chiusi. Se il filesystem non prova il round-trip dei metadati, non si
  applica il piano.
- I riferimenti a output completi di query concluse restano il TODO generale
  `QUERY-OUTPUT-001`; non esiste memoria privata dell'executor.
- Nessuna prova di servizio vivo, pubblicazione, deploy o restart è stata
  eseguita da questo ramo.

## Passi residui assegnati a RM-0008

1. Integrare i commit per contenuto sulla base finale e rigenerare firma e
   `manifest.lang_state.json` se qualunque byte di manifest o codice cambia.
2. Eseguire le suite complete del ramo integrato, incluse i18n/routing,
   ammissione Executor Standard, sandbox, HTTP, Telegram, undo e recovery.
3. Eseguire l'E2E isolata: preview, form-only in chat, callback/grant, apply,
   ricevuta, broker reverse; ripetere per Telegram verificando URL e consegna
   al canale originario. Provare replay, canale e turno errati, scadenza,
   doppio invio e outbox `ambiguous` dopo process-death.
4. Registrare separatamente l'accettazione sul percorso reale `/agent/turn`.
   Non usare il servizio corrente per validare questo ramo e non riavviarlo
   durante l'integrazione.
5. Risolvere R-003 prima di qualunque pubblicazione. `runtime/sign.py publish`
   non è una procedura valida e la firma di authoring qui presente non mette
   l'executor in esercizio.

Il candidato va rifiutato se una delle prove richiede di allargare il lessico,
saltare il form, ricostruire una lista troncata, trasformare un effetto in
`no_effect`, ritentare una consegna Telegram ambigua o perdere la ricevuta di
undo.
