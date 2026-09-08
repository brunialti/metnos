# RM-0008 — verifica produttiva e chiusura documentale dell'8 settembre 2026

## Risultato verificato

Il passaggio F4 r18 è riuscito: codice congelato `b715a765a0f664d1c2c660e5d48e5f6abada0169`.
Non rilanciare reset, `/tmp/m8`, cutover o i tentativi precedenti.
La correzione funzionale consiste nella directory firmata di esecuzione del
worker (`@installation_root@/runtime`); non in un nuovo sottosistema.

- CLI exit0, risultato `PREFLIGHT_VERIFIED`, catena firmata e sette record
  durevoli verificati nuovamente dopo il turno Tutor.
- Bundle: `/var/lib/metnos-admin/rm0008-final-b715a765`.
- Risultato canonico: SHA256
  `3e507091393b0b0217437529037b84c274644c759162c5436bf2d2c82e3d893d`.
- Release: `/var/lib/metnos/executor-birth/releases-v1/00000000000000000001`.
- Ultimo censimento: HTTP PID1951450, LRE PID1951455, browser PID1952072,
  attivi con zero riavvii automatici; stack-ready riuscito.
- LRE conserva `enabled=True`; non disabilitato per superare le prove.
- 717 test passati, 19 skip dichiarati:
  `/tmp/metnos-rm0008-r18-portable-final.xml`, SHA256
  `5cb98a2793f82929af4aa1efc0dc417c6e2fb2b509e9aec0a437943764bdf1df`.

## Prove funzionali, senza nuova transizione

Una sola richiesta dell'ora ha eseguito `get_now`: tempo e registro persistente
verificati, stesso processo HTTP. Ricevuta:
`/var/lib/metnos-admin/rm0008-functional-b715a765-r18/stdout.json`,
SHA256 `edf595ae2df4f7c398bd2048a4d38f4886edc3abd0894991fbeb1c326b59862c`.
Questa prova da sola non attesta inferenza LLM.

Tutor richiedeva il percorso del modello BGE già presente: aggiunto soltanto
`[text].model_dir` alla configurazione privata `embedding_tiers.toml`.
Originale conservato in
`/var/lib/metnos-admin/rm0008-model-path-b715a765/embedding_tiers.toml.original`.
Nessuna chiave rigenerata, nessuna modifica alla release immutabile.
Un primo comando con il Python amministrativo è fallito per dipendenze assenti;
il successivo con il Python gestito del prodotto è terminato con successo:
4 schede, 3471 unità di conoscenza. Prove:
`/var/lib/metnos-admin/rm0008-tutor-b715a765-r18-managed/`.

Turno reale Tutor `2562ecb071574a56`: risposta sulla guida pubblica LRE,
esito persistente `fondata`, 16 riferimenti a fonti, nessuna lacuna, 15,5 s.
Ricevuta:
`/var/lib/metnos-admin/rm0008-tutor-turn-b715a765-r18/result.json`,
SHA256 `58b2df6d0218452576bf8e48f1d610679611281a9a2a6795bbd3bb869d2b4b60`.
È una prova funzionale circoscritta, non una certificazione generale della
qualità di tutte le risposte Tutor.

## RM-0009 e limiti della chiusura

Su richiesta dell'operatore, RM-0009 è ricondotta alla raccolta comune
`internal/roadmap/` e all'indice, conservando integralmente la revisione 4
del 4 settembre proveniente dal commit
`0f38736060927575ad41cd759d16273da2f42623` nel worktree
`/tmp/metnos-rm0009`. La copia originale non è stata rimossa.
Implementazione NON iniziata; stato `active` significa direzione progettuale.
I conteggi contenuti in RM-0009 restano misure storiche del 3 settembre.
Solo il riepilogo pubblico bilingue è destinato al sito/GitHub, escluso dalle
fonti Tutor come capacità attuale tramite `tutor-exclude`.

Il successo F4 non soddisfa automaticamente F5-F6: il preesercizio mantiene
le cinque ammissioni reali da almeno due produttori e gli ulteriori criteri
della roadmap. Non dichiarare RM-0008 interamente `closed`.

## Pubblicazione

L'operatore ha autorizzato esplicitamente pubblicazione GitHub pubblica
incrementale dopo filtro GII, senza riscrittura della storia. Le fonti Python
restano private754 `sha256:2a6c978928a62f8f113d8e13c3664b31b6341df379d760a4256063fdb6256e3b`
e pubbliche742 `sha256:44242641f4a4b53a413de311dff91a62b808d3ffc8094934ea057b9f7c7f5865`.
Il sito finale contiene 99 pagine ammesse, incluse due guide UI rigenerate;
il catalogo Tutor della release immutabile resta quello compilato sopra.
Controllare l'indice pubblico materializzato: cache locali di Wrangler e
bytecode Python non devono entrare nella pubblicazione.
La prima distribuzione del sito è riuscita; segue aggiornamento finale con
RM-0009. Commit pubblico e verifica del sito vanno registrati dopo l'esito.

### Controllo GII finale: pubblicazione del codice fermata

L'export rigenerato contiene 1757 file, senza cache Wrangler né bytecode.
I controlli delle radici private/public e dell'indice Python passano.
Il filtro superficiale del publisher passa, ma **non basta**: il controllo
forte `/tmp/rm0008_public_gii_gate_prototype.py`, su filesystem e indice
materializzato, segnala 387 coppie regola/file da classificare (351 identità
nominative, 35 domini email, 1 indirizzo privato di esempio).
Non equivalgono a 387 segreti: comprendono attribuzioni pubbliche e segnaposto.
Sono però presenti anche riferimenti personali a macchine, account ed esempi
nei commenti e nelle docstring; non possono essere dichiarati zero GII.
Nessun token o chiave privata è emerso dal filtro dei segreti.

**Nessun push effettuato.** Il repository pubblico remoto resta alla base
`005dd00207aca0b5808f692f562568d5e00ed1d7` osservata prima dello staging.
Il worktree pubblico preparato è
`/tmp/metnos-rm0008-publish-r18.4n19bk9i/public-repo`;
la differenza materializzata riguarda 170 file, 29920 aggiunte e 3855 rimozioni,
accumulate dallo sviluppo precedente: non sono modifiche runtime di questa
integrazione documentale.

La bonifica va progettata sul percorso di export, preservando le firme dei
manifest e distinguendo attribuzioni pubbliche consentite da dati privati;
poi occorrono revisione della nuova radice pubblica e controllo GII finale.
Non abbassare il filtro, non aggiornare impronte alla cieca e non pubblicare
prima del controllo. Il nuovo riepilogo RM-0009 italiano/inglese è pronto
localmente, non ancora pubblicato. La distribuzione precedente del sito
`9d097395` non contiene questa successiva integrazione RM-0009.

Verifiche documentali: 99 HTML ammessi; 15 test del confine documentale passati.
RM-0009 nelle due raccolte è byte-identica alla revisione 4 originale:
SHA256 `1171c436834d06e60c2658acbc1dbc933240148616d35f4d64c37f335aee3fd3`.
