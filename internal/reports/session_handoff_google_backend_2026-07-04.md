# Handoff sessione Google backend - 2026-07-04

## Stato

La sessione va considerata chiusa con esito operativo negativo.

Non continuare dal presupposto che le ultime modifiche siano corrette. Prima
azione della prossima sessione: rileggere questo file, verificare `git status`
e decidere esplicitamente cosa tenere e cosa scartare.

## Vincoli dati dall'utente

- Non toccare codice senza richiesta esplicita.
- Non impattare componenti non coinvolte.
- Obiettivo tecnico: far passare i casi Google Drive senza rompere il caso
  locale.

## Risultato del test utente

Tabella riportata dall'utente:

- S1 ricerca Drive: atteso verde, reale rosso.
  Dettaglio: `find_credentials > find_files`, `find_files(gw) entries=0`.
- S2 leggi Doc per nome: atteso verde, reale rosso.
  Dettaglio: `read_files_doc`, `any_ok=False`, nessun contenuto.
- S3 leggi foglio per nome: atteso verde, reale rosso.
  Dettaglio: `read_files_spreadsheet`, `any_ok=False`, nessun contenuto.
- S4 regressione locale: verde.

## Diagnosi sintetica

S1 e' rosso perche' `runtime/backend_resolver.py` e' tornato a gestire
`files` solo come locale:

```python
"files": {
    "arg": "client",
    "providers": ["local"],
    "available": lambda p: True,
    "aliases": {},
}
```

Quindi una richiesta che cita "google drive" non forza piu'
`client="google_workspace"` su `find_files`.

S2/S3 sono rossi perche' il backend Google puo' leggere da ID reali o da
locatori strutturati, ma il piano reale puo' ancora arrivare a
`read_files_doc` / `read_files_spreadsheet` con ID opaco inventato oppure senza
`query/name/pattern`. In quel caso il backend non puo' ricavare in modo
affidabile il nome dalla frase utente, perche' la frase utente non gli arriva
come contratto stabile.

## Errore operativo della sessione

Sono state tentate due direzioni sbagliate:

1. Soluzione troppo larga: modifiche a `dispatch`, `skill_wrapper`,
   `delete_files`, oltre al backend Google.
2. Soluzione troppo stretta: rimozione anche del routing `files ->
   google_workspace`, causando la regressione S1.

La prossima sessione deve evitare entrambe.

## File modificati attualmente nel workspace

Al momento del salvataggio risultano modificati o non tracciati:

- `runtime/backends/files/google_workspace.py`
- `runtime/engine/dispatch.py`
- `runtime/tests/test_drive_read_compound.py` non tracciato
- `runtime/tests/test_google_backend_e2e.py` non tracciato
- vari file documentali e design gia' presenti nel workspace

Non assumere che tutte queste modifiche siano dell'ultima sessione. Verificare
con `git diff` e con l'utente prima di revert o ulteriori interventi.

## Direzione tecnica consigliata

Correzione minima, da proporre prima di applicare:

1. Ripristinare in `runtime/backend_resolver.py` il supporto a
   `google_workspace` per l'oggetto `files`, con alias espliciti:
   `google drive`, `gdrive`, `google docs`, `google sheet`, `google fogli`.
2. Mantenere il default locale quando la query non cita Google Drive.
3. Normalizzare solo gli argomenti Drive ovvi per `find_files`, ad esempio
   rimuovere `/gdrive` come falso `base_path` e trasformare il nome estratto in
   `pattern`/`query`.
4. Non toccare `delete_files`.
5. Non toccare `skill_wrapper`.
6. Non aggiungere nuove guardie generali in `dispatch` senza un test che mostri
   il piano esatto da correggere.
7. Per S2/S3 decidere dopo aver visto il piano reale:
   - se il piano e' `find_files(client=google_workspace) -> read_files...`,
     correggere solo il passaggio `entries -> id`;
   - se il piano e' direttamente `read_files_doc(document_id=allucinato)`,
     serve una guardia mirata o una normalizzazione argomenti prima
     dell'executor, ma va limitata ai soli tool Google by-id.

## Test utili

Test live principale:

```bash
python3 runtime/tests/test_google_backend_e2e.py
```

Test mirati non live:

```bash
python3 -m pytest runtime/tests/test_drive_read_compound.py runtime/tests/test_google_token_refresh.py runtime/tests/test_piping_from_step.py -q
```

Controllo locale da non rompere:

```bash
python3 runtime/test_runner.py executors/delete_files/manifest.toml
```

## Prompt consigliato per prossima sessione

```text
Siamo in /opt/metnos. Prima di fare qualunque modifica leggi
internal/reports/session_handoff_google_backend_2026-07-04.md e controlla
git status.

Vincoli:
- non toccare codice senza aver prima spiegato il piano;
- non impattare componenti non coinvolte;
- non modificare delete_files, skill_wrapper o guardie generali del motore salvo
  nuova autorizzazione esplicita;
- obiettivo: far passare il test live runtime/tests/test_google_backend_e2e.py,
  mantenendo verde la regressione locale S4.

Situazione nota:
- S1 ricerca Drive e' regredito perche' backend_resolver.py non instrada piu'
  files verso google_workspace quando la query cita Google Drive.
- S2/S3 lettura Doc/Foglio per nome falliscono perche' il piano reale arriva a
  read_files_doc/read_files_spreadsheet senza ID reale o senza locator utile.

Procedura:
1. Mostrami il git status e separa modifiche preesistenti da modifiche da
   proporre.
2. Leggi runtime/tests/test_google_backend_e2e.py e riproduci/analizza i piani
   reali S1/S2/S3 senza cambiare codice.
3. Proponi una patch minima. Prima priorita': ripristinare solo il routing
   files -> google_workspace in backend_resolver quando la query cita Drive,
   senza cambiare il default locale.
4. Solo dopo approvazione applica la patch e lancia i test.
```
