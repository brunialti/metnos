# LRE: coordinamento del rilascio — 22 settembre 2026

Responsabile di questo riscontro: Codex, task LRE con handover del 17/9.
Ambito: istruzioni correnti, attribuzione delle richieste, prerequisiti della
prova R-003. Nessuna pubblicazione, riavvio, riprova di job o attivazione F5.

## Fotografia dei sorgenti

Letture del 22/9, dalle 20:23 Europe/Rome:

- Checkout principale `/opt/metnos`, ramo `session/detection-lexicon-i18n`,
  HEAD `52a9c7aed19964554b1bff83e658d6b59f85a479`, non pulito.
- Ramo storico LRE `codex/lre-general-parallel-f5`, HEAD `cb8669a6`,
  anch'esso con nuove modifiche non committate. Non vengono attribuite a
  questa task, incluse nei suoi commit o sovrascritte.
- Nuovo ramo esclusivamente documentale di questo riscontro:
  `codex/lre-coordination-20260922`, creato dal HEAD principale sopra indicato.
  Non e' un albero dal quale pubblicare.
- La bacheca principale aveva gia' modifiche non committate a R-006 durante
  la lettura. Questa task non le include nei propri commit e non le cancella.

Sono stati letti integralmente `AGENTS.md`, `CLAUDE.md`,
`CLAUDE.mutabile.md`, la bacheca e `internal/AGENTS.md`.
La norma obsoleta di `CLAUDE.md` resta intatta; vale la sospensione esplicita
delle pubblicazioni indicata in R-003 e R-004.

## R-001: attribuzione circoscritta, non pulizia alla cieca

`git status` restituisce 51 voci al campionamento, non le 49 della segnalazione
originaria; il numero puo' cambiare per lavoro concorrente. Comprende 16 file
tracciati mancanti e 16 corrispondenti `*.retired-v1` non tracciati.
Il confronto SHA256 fra **ogni contenuto `HEAD:<percorso>`** e il rispettivo
file ritirato ha dato **16 identita', zero differenze, zero copie mancanti**.

Questo dimostra la conservazione dei contenuti osservati, non l'autore del
ritiro, la sicurezza di riattivare quei punti d'ingresso o la pulizia del
checkout. Non committare automaticamente le cancellazioni come una modifica
funzionale e non rinominare i file ritirati per rendere verde `git status`.
Resta il permesso negato su `install/data/`: non e' stato modificato.

E' riconosciuto come appartenente a questa task soltanto il breve puntatore
`internal/design/handover_lre_17_9_2026.md`, scritto nella conversazione del
17/9. Viene conservato sul nuovo ramo con un'avvertenza esplicita: i numeri
del 17/9 sono storici, non lo stato produttivo del 22/9. La documentazione
completa e gli sviluppi successivi sono nell'albero LRE e vanno riletti.
Il resto delle modifiche principali non viene adottato senza attribuzione.
**R-001 resta aperta; nessun permesso di pubblicare.**

## R-003: presa in carico della preparazione della prova reale

Confermato sul checkout principale, senza importare moduli applicativi:

1. `runtime/stack_reconcile.py:1093`: il comando `deploy --executor ... --sign`
   arriva a `StackReconciler.restart(..., sign_first=True)`.
2. `:493` e `:517`: `verify_named_executors` costruisce il candidato CORE e
   usa `submit_stack_reconcile_birth`, non il vecchio pubblicatore.
3. `:856` e `:857`: in `restart`, `require_quiescent()` precede l'ammissione.
4. `:858` e seguenti: **i controlli del target e del precedente HTTP di sistema
   seguono l'ammissione**. L'HTTP di sistema attivo viene considerato una
   vecchia installazione concorrente (`legacy_baseline_active`), e il riavvio
   seleziona ancora esplicitamente lo scope `user` (`:877`).

SHA256 del file letto:
`5c83dc61340a5963e58005d794a856647a9344b3b4c91ed5609cd9c57ddc2b57`.
`git diff --quiet -- runtime/stack_reconcile.py` ha confermato che non era
modificato rispetto al HEAD principale.

**Conseguenza da verificare prima della prova:** se la nascita riesce e un
controllo successivo rifiuta il riavvio, un comando complessivamente fallito
puo' avere gia' pubblicato una generazione. Un errore del comando non prova
assenza di effetti. Questa e' un'osservazione dell'ordine nel codice, NON una
prova di esecuzione in produzione.

Il ramo LRE `cb8669a6` non ha lo stesso `restart`: ricava lo scope dal catalogo
(`stack_scope()`), limita la barriera sul vecchio HTTP al caso `scope=user`
e contiene la gestione delle attivazioni pendenti. Non si assume che quel
ramo sia fuso, che tutti i suoi cambiamenti siano adatti alla prossima release,
o che il flusso da checkout principale sia quindi gia' certificato.

### Condizioni prima dell'esecuzione

- Chiudere R-001 e selezionare esplicitamente la versione da pubblicare;
  integrare e verificare il raccordo sul checkout principale pulito.
- Verificare la topologia corretta e i rifiuti prima degli effetti;
  non aggiungere un nuovo produttore per aggirare il raccordo esistente.
- Concordare finestra e candidato CORE con l'operatore. Le autorizzazioni
  storiche al rilascio LRE del 17/9 non dimostrano una finestra libera oggi.
- Acquisire nuovamente salute, turni, browser e lavori LRE prima del riavvio;
  non usare la fotografia del job del 17/9. Preservare dati e generazioni.
- Eseguire soltanto il percorso ammesso, da `/opt/metnos`, non da worktree.

### Ricevuta necessaria per chiudere R-003

Registrare release e commit esatti, candidato e impronta, autorizzazione e
finestra, identita' del contratto, generazione precedente e successiva,
ricevuta di Birth ed esito del riavvio. Rileggere poi il contratto attraverso
il catalogo verificato e dimostrare che e' la generazione pubblicata; censire
prima/dopo anche i contratti estranei, che non devono sparire. Aggiungere una
prova reale innocua, salute finale e continuita' degli eventuali job LRE.
In caso di errore, rileggere gli effetti gia' avvenuti prima di ripetere.

**Stato: preparazione in carico a questa task; prova reale non eseguita,
R-003 aperta e pubblicazioni ancora bloccate.** Nessuna prova simulata viene
promossa a ricevuta reale. Non e' stato scelto o modificato un executor.

## Altre voci

- **R-002:** letta la collisione su caricatore, origine dei contratti e codice
  generato. Questa task non modifica quelle aree e non fonde RM-0011. Resta
  vincolante l'ordine di ritiro dei contratti prima della rimozione del suffisso.
- **R-005:** i commit `3cddd734` e `1942462b` sono entrambi **non antenati**
  del HEAD principale osservato (`git merge-base --is-ancestor`, esito 1
  per entrambi). La consegna resta da integrare e verificare nella release
  pertinente. Questo confronto di storia non prova da solo che il codice
  manchi da qualunque installazione: nessuna installazione e' stata ispezionata
  in questa task. Nessuna integrazione o installazione effettuata qui.
- **R-006:** la bacheca la indica gia' in carico alla task RM-0008/F5, con
  aggiornamenti concorrenti. Non duplicare la diagnosi e non spostarne le
  impronte; questa task non la prende in carico.
- **R-004:** nessuna modifica al documento invariante di Roberto.

## Limite di questo riscontro

Non sono stati interrogati o modificati servizi, store o job di produzione.
Non e' dunque una dichiarazione sullo stato attuale di LRE. Il handover del
17/9 e le sue autorizzazioni non sostituiscono una nuova misura e una finestra
operativa. Le richieste rimangono aperte fino alle rispettive prove di chiusura.
