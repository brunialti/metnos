# Consegna — portare RM-0008 in produzione

> Scritta il 31 agosto 2026 a risorse in esaurimento, per un agente che
> subentra. Roberto e' l'autorita' di nascita e ha autorizzato l'operazione.
> **Metnos e' in funzione**: non e' un'emergenza, e' un lavoro da fare bene.

## 1. Stato in una riga

La fusione dei 539 commit RM-0008 in `/opt/metnos` **funziona tecnicamente** ma
**il servizio non parte**, perche' il codice nuovo attiva l'autorita' di nascita
e l'attivazione fallisce. Provato dal vivo: dieci minuti di interruzione,
ripristinati.

## 2. Cosa NON rifare

- La fusione e' gia' fatta e verificata in `/tmp/metnos-prova-fusione`
  (commit `2cb6eac4` piu' le correzioni successive). Conflitti risolti, suite
  verdi. **Non rifarla da zero.**
- I 47 file non committati dell'installazione sono committati in `14ea7179` e
  copiati in `…/scratchpad/backup-47/`. Nulla e' andato perso.
- Le cinque correzioni di produzione di stanotte sono state confrontate una per
  una con monte: **tre mancavano** (`release_catalog` in `stack_reconcile`,
  `require_disjoint_session` negli script tmux, `publication_is_uninitialized`
  in `contract_store`) e sono state riportate con `git apply --3way`, pulite.

## 3. I due blocchi, in ordine

**Primo — risolto e verificato.** `_verify_posix_directory` in
`runtime/executor_birth_secure_fs.py:855-873` rifiuta una radice
`historical_public` con `mode & 0o022`. `/opt/metnos/runtime` e' `775`.
`chmod g-w /opt/metnos/runtime` sblocca `open_distribution_sources_v1()`.
Verificato eseguendo il controllo NELLA cartella giusta (vedi §5).

**Secondo — aperto.** Superato il primo, l'avvio si ferma in
`runtime/executor_birth_prepared_root.py:196`: `prepare_context_material_v1()`
fallisce con lo stesso codice `birth_provisioning_acl_unsafe`. Non e' un
permesso: e' nel modello dei ruoli del filesystem sicuro. L'insieme preparato
esiste (`~/.config/metnos/birth/`, creato il 30/8 da un'altra sessione) e i suoi
permessi sembrano corretti (`755` integrita', `700` confidenziale).

Da li' si riprende.

## 4. L'ordine giusto, dimostrato a spese di Roberto

**Prima si porta l'autorita' di nascita a uno stato attivabile, poi si fonde il
codice che la pretende.** Il contrario produce un servizio che non riparte, e su
questa macchina il processo vivo era l'ultima istanza: nessun riavvio poteva
riuscire. Il docstring di `require_birth_runtime_before_workers` a monte
descrive gia' questo scenario — qualcuno ci era gia' passato.

## 5. Tre trappole di metodo, pagate stanotte

- **Non fondere sull'albero da cui gira il servizio.** Fondere in copia,
  verificare l'AVVIO, poi spostare.
- **Le suite verdi non dicono che il servizio parte.** Nessuna suite avvia il
  server con le radici reali. Prima di toccare la produzione, provare
  `require_birth_runtime_before_workers()` con `PATH_RUNTIME` reale.
- **Verificare CHE COSA si misura.** Ho provato l'ipotesi del permesso
  eseguendo il controllo dal worktree, dove `PATH_RUNTIME` punta al worktree:
  il `chmod` sulla produzione non poteva contare, e ho concluso «ipotesi
  sbagliata» quando era giusta.

## 6. Punto di ritorno

- Tag `pre-fusione-31-8-2026` = stato prima di tutto.
- `14ea7179` = stato attuale dell'installazione (con i 47 file al riparo).
- Copia del DB i18n prima della sincronizzazione: `…/scratchpad/i18n-prima.sqlite`.
- Permessi originali: `…/scratchpad/permessi-prima.txt`.

## 7. Altro fatto stanotte sull'installazione

- **130 messaggi i18n aggiunti** al DB vivo (zero modificati, misurato prima).
- **`metnos-http.service` utente ABILITATO**: era l'unica unit Metnos non
  abilitata, e un riavvio della macchina l'avrebbe lasciata spenta.
- Il `failed` che si vede con `systemctl status metnos-http` senza `--user` e'
  il residuo dell'unit di SISTEMA, vecchia e disabilitata. Non e' un guasto.
  Pulirlo: `sudo systemctl reset-failed metnos-http.service` (chiede password).
