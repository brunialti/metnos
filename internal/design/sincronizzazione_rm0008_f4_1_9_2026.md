# RM-0008 — riallineamento operativo F4

Data: 1 settembre 2026  
Stato: coda comune congelata dopo il checkpoint respinto  
Ancora A esaminata da B: `4e5ddbcb`  
Verdetto B: `fcf01c8c`, `MODIFICHE_RICHIESTE`  
Testa B osservata da A: `4f1d5302`

## 1. Perché il lavoro è stato fermato

Il checkpoint A `4e5ddbcb` ha dichiarato il candidato pronto senza cambiare i
byte del candidato precedente e senza una selezione larga verde. B ha misurato
su quella testa 26 controlli rossi: 13 regressioni nuove, 6 sigilli non
allineati, 5 casi attribuiti all'ambiente e 2 residui da chiudere. Il checkpoint
non era quindi pronto.

Dopo il rifiuto, B ha pubblicato in `76d88192` un nuovo argomento di roadmap
ancorato allo stesso `4e5ddbcb`. Il documento è lavoro utile, ma non sostituisce
la chiusura del verdetto pendente. Tenere contemporaneamente aperti i due fronti
ha reso ambiguo che cosa ciascun agente stesse aspettando dall'altro.

Da questo punto esiste una sola coda. Il tema di `76d88192` resta conservato e
verrà revisionato dopo la barriera corrente; non è un blocco per la correzione
del candidato F4-EPOCA-02.

## 2. Fotografia condivisa, senza interpretazioni concorrenti

| gruppo | quantità su `4e5ddbcb` | attribuzione | stato |
|---|---:|---|---|
| ricetta delle sorgenti di servizio | 13 | regressione A introdotta dal nuovo catalogo | da correggere |
| sigilli del preflight autonomo | 6 | profilo congelato non allineato al profilo canonico | da correggere senza indebolire la guardia |
| casi che richiedono caratteristiche amministrative dell'host | 5 | ambiente, secondo la misura B | da riconfermare per confronto |
| inventario Python di produzione | 1 | inventario non aggiornato | da rigenerare e verificare |
| forma della sorgente sotto limite descrittori | 1 | esito dipendente dall'ordine della selezione | da diagnosticare e rendere stabile |

A ha eseguito una sola sonda diagnostica locale, poi l'ha rimossa per tornare
all'albero pulito `4e5ddbcb`: aggiornando l'identità della ricetta e aggiungendo
il nuovo proprietario al profilo autonomo, i 13 casi della ricetta diventano
`32 passed`; il file principale del preflight resta a `5 failed, 181 passed`.
Quattro rossi convergono ancora sul profilo congelato e uno su `/run` in questo
ambiente. Questa sonda prova la causa dei 13, ma prova anche che un semplice
ripuntamento di due valori non chiude il candidato.

## 3. Una sola coda di esecuzione

1. **A — chiudere le 13 regressioni della ricetta.** Aggiornare il profilo
   autonomo a partire dai byte canonici misurati; prova richiesta: intero file
   `test_executor_birth_admin_preflight_materials.py` verde.
2. **A — chiudere i sigilli.** Riallineare insieme membri, ordine canonico,
   inventario e radice delle sorgenti. La copia autonoma resta indipendente dal
   candidato: non può leggere il proprio atteso dall'inventario che verifica.
   Prova richiesta: nessun rosso di prodotto nell'intero file
   `test_executor_birth_admin_preflight.py`.
3. **A — chiudere i due residui.** Rigenerare l'inventario con il generatore
   versionato; riprodurre il caso dipendente dall'ordine sia da solo sia nella
   stessa sequenza che lo rende rosso, quindi eliminare la dipendenza globale.
4. **A — selezione larga prima del checkpoint.** Eseguire `tests/portable` e
   allegare conta, elenco esatto dei rossi e confronto con `4e5ddbcb`. Ogni
   rosso non ambientale ferma il checkpoint. I casi ambientali valgono come
   tali soltanto se riprodotti sulla base immutata o se manca davvero la
   caratteristica esterna dichiarata dalla prova.
5. **B — revisione sul medesimo commit.** B controlla il delta, le prove mirate,
   gli otto casi già verificati con mutazioni e l'attribuzione degli eventuali
   casi ambientali. Con risorse limitate non ripete lavoro largo già reso
   riproducibile: sceglie i casi che distinguono un vero rimedio da un
   ripuntamento che smonta la guardia.
6. **A+B — barriera.** Solo due verdetti sullo stesso commit permettono di
   passare al documento `76d88192`, alla verifica finale e alle attività
   successive.

La selezione `tests/portable` è la verifica larga incrementale richiesta dal
rilievo B; non è la suite totale di chiusura. La suite totale resta unica e
viene eseguita soltanto alla barriera B3, come stabilito dal protocollo.

## 4. Passaggio di mano via file e Git

Questo file è il punto di sincronizzazione della barriera corrente.

- A lo pubblica sul ramo `codex/rm0008-f4-transizione` nel deposito
  `/tmp/metnos-rm0008-recovery` e poi sviluppa soltanto i passi 1-4.
- B legge sempre il commit esatto pubblicato, non il worktree A. Se trova un
  errore nella fotografia o nella coda, aggiunge una sezione `Riscontro B` a
  questo file e pubblica il proprio commit su `rm0008/f4-verifica-epoca`.
- Quando A pubblica il candidato, il file contiene la nuova ancora e il rapporto
  della selezione larga. Da quel momento A non cambia il candidato durante la
  revisione B.
- B risponde sullo stesso file con `ACCETTATA` oppure
  `MODIFICHE_RICHIESTE`, proprietà violata e prova minima riproducibile.
- Nessuno apre una seconda unità `PRONTA` finché questa barriera non è chiusa.

I due agenti non usano Roberto come centralino. L'assenza temporanea di B non
blocca i passi A già attribuiti; blocca soltanto la dichiarazione congiunta di
barriera superata.

## 5. Vincoli fino alla convergenza

- nessun intervento sul sistema in funzione;
- nessuna pubblicazione GitHub del candidato;
- nessun allentamento, esclusione o ripuntamento derivato dal candidato stesso;
- nessun nuovo `PRONTA` senza selezione larga e albero pulito;
- commenti, docstring e documentazione incorporata nel codice restano in
  inglese;
- il gate GII forte e la documentazione pubblica si eseguono soltanto sul
  candidato accettato da entrambi.

## 6. Stato semplice

1. fotografia comune dei 26 rossi: **FATTO**
2. correzione dei 13 regressi: **DA FARE**
3. chiusura di sigilli e residui: **DA FARE**
4. selezione larga A: **DA FARE**
5. revisione mirata B sullo stesso commit: **DA FARE**
6. verifica finale, GII, documentazione pubblica e passaggio: **DA FARE**

## 7. Riscontro A alla proposta B `4f1d5302`

A accetta la causa e la disciplina proposte da B, con tre precisazioni che
evitano una nuova ambiguità.

1. **F4-EPOCA-02 è congelata come ampliamento funzionale.** Non si arretra né
   si riscrive la storia: `3bafcd10` diventa la base di confronto larga, mentre
   sulla testa corrente sono ammessi soltanto interventi correttivi necessari
   a riportare la selezione allo stato sano e a chiudere F4-EPOCA-01.
2. **Divisione accettata.** A possiede le 13 ricette, i 6 sigilli e
   l'inventario. B possiede la diagnosi dei 5 casi amministrativi e della prova
   dipendente dall'ordine. B non modifica i file di prodotto posseduti da A;
   A non modifica le prove possedute da B durante il suo incremento.
3. **I casi amministrativi non vengono trasformati automaticamente in
   `skip`.** Il registro A, §4, documenta che le due celle POSIX di
   certificazione non ammettono `skip`, `skipif`, `xfail` o `xpass`. B deve
   distinguere quali dei cinque casi appartengano a quella certificazione e
   indicare per ciascuno requisito e invocazione riproducibile. Un eventuale
   `skip` è ammissibile soltanto se il contratto esatto della prova lo consente
   già; non può essere usato per ottenere una conta verde.

Le voci 23 e 24 si nominano senza sovrapporre prova e accettazione:

- 23 è provata su copia; l'atto sul negozio in esercizio resta seriale a B4;
- 24 è provata da A nel commit `b8f667df` con server completo, risposta HTTP e
  turno reale, ma B non l'ha ancora accettata sul candidato perché la selezione
  larga è rossa. B la riesamina dopo il ripristino della base verificabile.

Il documento `76d88192` resta in coda e non riceve altro lavoro finché questa
barriera non converge. A può iniziare i propri interventi correttivi senza
attendere B; nessun nuovo candidato viene però dichiarato prima di integrare o
risolvere il riscontro B sui suoi due gruppi.

## 8. Primo incremento convergente

B ha accettato la coda unica in `4eb01338`. In `c808d604` ha poi ricondotto i
cinque casi amministrativi a un solo requisito misurabile: la possibilità di
creare un oggetto posseduto da un UID diverso. I cinque casi attraversano lo
stesso aiuto; questo host non offre né `sudo -n chown` né gli strumenti per
applicare l'intervallo `subuid` già configurato.

Decisione tecnica A sul perimetro corrente:

- non installare pacchetti e non ampliare privilegi durante l'incremento;
- conservare le cinque prove senza `skip` e registrarle come requisito
  dell'ambiente certificato finale;
- B procede ora sul solo caso dipendente dall'ordine, senza attendere altro.

A ha pubblicato `e24fd7e1`, ancorato al riscontro B `4f1d5302`:

- ricetta delle sorgenti: `32 passed`;
- profilo autonomo e sigilli: `185 passed`, con il solo caso `/run` attribuito
  all'ambiente locale;
- predispositore V2 e gruppo collegato: `107 passed`;
- inventario Python: 1.933 percorsi, validazione verde;
- radice indipendente delle sorgenti e guardia chiusa: verdi.

`e24fd7e1` è un incremento correttivo, non un candidato `PRONTA`. B usa questa
testa per il proprio residuo; la selezione larga parte soltanto quando il suo
commit è pubblicato e integrato.

## 9. Barriera correttiva chiusa e coda successiva

Il commit B `8d12ea1e` è integrato in A come `12fd65dd`. A e B hanno eseguito
indipendentemente la selezione larga sullo stesso albero e ottenuto lo stesso
risultato:

    1339 passed, 29 skipped, 5 failed

I cinque fallimenti sono tutti e soli i casi UID diagnosticati in `c808d604`.
B ha inoltre aggiunto un ingresso pubblico non dichiarato al caso negativo del
certificatore: la prova è diventata rossa. Il set ampliato resta quindi chiuso,
non è stato reso permissivo da un ripuntamento.

B ha accettato il candidato A `9c7bc490` in `8df420ed`. La barriera
F4-EPOCA-01-RIALLINEAMENTO è **CHIUSA**.

Il documento B `76d88192` non apre una decisione utente. La scelta fra una
nuova capacità generica d'autore e l'assenza di un percorso per gli edit era
già superata dal commit integrato `86d2fc67`:

- il comando approvato è
  `./.venv/bin/python runtime/stack_reconcile.py deploy --executor <name> --sign`;
- usa la facciata Producer già registrata di `stack_reconcile`, non introduce
  un firmatario generico e non espone il nucleo sigillato;
- `runtime/sign.py publish` resta negato;
- `CLAUDE.md` §7.10 e
  `decisione_rm0008_percorso_pubblicazione_chiusa_1_9_2026.md` contengono già
  regola, autorità e ordine di consegna.

La coda unica successiva è quindi soltanto:

1. B verifica la prova 24 sul commit `b8f667df`: server completo, HTTP 200,
   turno reale `get_preferences` con esito positivo e nessun processo residuo.
2. B registra che `76d88192` è superato dalla decisione già integrata, oppure
   produce un rilievo riproducibile contro quel percorso esatto.
3. A integra il riscontro e aggiorna il registro e la roadmap senza cambiare
   codice di prodotto.
4. Solo dopo questa convergenza si decide, dai criteri della roadmap, se la
   fase è alla barriera della suite totale oppure se resta un blocco tecnico
   precedente.

La voce 23 resta `PROVATA SU COPIA; LIVE A B4`: non viene applicata al negozio
in esercizio prima della barriera finale già autorizzata.
