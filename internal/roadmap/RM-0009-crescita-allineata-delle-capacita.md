# RM-0009 — Crescita allineata delle capacità

> **Revisione 4, 4 settembre 2026.** Roberto ha deciso la sola domanda che la
> revisione 3 gli lasciava aperta: «i 4 livelli dovrebbero dare a Metnos libertà
> diverse. O almeno, la libertà di Metnos deve essere **modulata**». Il §8 non
> chiude più quella strada, il §10 non chiede più quella decisione, e la
> roadmap acquista una fase, **F6**, che la percorre. Resta roadmap: nessuna
> fase è iniziata.
>
> **Revisione 3, 3 settembre 2026.** La revisione 2 è stata sottoposta a quattro
> revisori avversariali e demolita: denominatore sbagliato di 11×, la classe
> portante che significa un'altra cosa, il rilievo principale attribuito al
> componente sbagliato, e la prima fase che scavalcava un cancello di RM-0008.
>
> **Correzione di Roberto che governa questa revisione:** «l'approccio e
> l'obiettivo non è mettere pezze ma creare un sistema che cresce in modo
> intelligente e gestito. Il modo di rappresentarlo è indifferente. L'obiettivo
> no.»
>
> Perciò: l'obiettivo del §1 è **invariante**. Il modello a quattro livelli
> **oggi non è un modello di autorità** — il codice dice che non lo è — e la
> revisione 4 non lo dà per perduto: lo tratta come un obiettivo da raggiungere
> per un'altra strada (F6), non come una descrizione da conservare. Il documento non elenca più fasi di
> costruzione: descrive **il ciclo di crescita che esiste già**, misura dove è
> interrotto, e stabilisce gli indicatori che tengono la tensione verso
> l'obiettivo anche quando nessuno ci sta lavorando.

## 1. Obiettivo — invariante

**Metnos deve crescere in modo intelligente e gestito.** Deve accorgersi di ciò
che non sa fare, proporlo, essere giudicato su fini dichiarati, nascere sotto
una porta unica, provarsi, ed essere promosso o dimenticato — senza che una
persona debba spingere ogni passaggio.

Crescere in modo gestito significa anche che **non tutto ciò che nasce può fare
tutto**: la libertà di Metnos deve essere **modulata**, e la misura di quella
libertà deve poggiare su qualcosa di firmato, non sull'etichetta con cui una
proposta è stata coniata. Oggi non lo è: la libertà è la stessa per ogni
executor che arriva ad essere attivo. Questa è la parte dell'obiettivo che la
fase F6 persegue.

Questo obiettivo **non è misurato oggi**, e non lo era nemmeno nelle revisioni 1
e 2: entrambe hanno provato a dire «quanto serve» con numeri che non
reggevano. La prima cosa che serve non è una fase di costruzione: è **rendere
l'obiettivo osservabile**. Finché non lo è, ogni piano è un'opinione.

## 2. Il ciclo esiste già. Questo è il suo stato reale

Non c'è niente da inventare: la catena è tutta scritta e gira ogni notte. Ecco
i suoi anelli, con la misura di ciascuno al 3/9/2026.

| # | anello | dove | stato misurato |
|---|---|---|---|
| 1 | il motore dichiara di non saper fare | `engine/dispatch.py:7650,7772` | **funziona**, 17 turni |
| 2 | il fallimento diventa una lacuna | `engine/terminator.py:78` | **conta male**: 4 righe contro 17-27 turni |
| 3 | la lacuna innesca una proposta | `learning_loop.py:34` | **INTERROTTO** (§3.1) |
| 4 | la proposta riceve un punteggio | 5 formule diverse | **INTERROTTO** (§3.2) |
| 5 | il giudizio è allineato ai fini | `alignment_engine` (vivo) + `vaglio.judge` (spento) | **parzialmente morto** (§3.3) |
| 6 | una persona decide | `change_applier.py:328` + UI | **funziona per progetto** |
| 7 | la capacità nasce | porta RM-0008 | 59 tentativi, **zero conclusi** |
| 8 | nasce in prova, non attiva | `executor_birth_shadow.py:392` | **funziona** — assegna `preexercise` |
| 9 | la prova decide se resta | `executor_birth_preexercise.py` | **inattivo per progetto**, dietro il cancello F5 di RM-0008 |
| 10 | promozione, scadenza, digest | catena `promoter` | gira ogni notte, **a vuoto dal 13/5** |
| 11 | il rifiuto insegna qualcosa | `change_applier.py:272` | **INTERROTTO** (§3.4) |

**Undici anelli. Tre funzionano, uno funziona per progetto.** Degli altri sette:
tre sono interrotti (3, 4, 11), uno conta male (2), uno è mezzo spento (5), uno
gira a vuoto (10), uno è chiuso a chiave da un'altra roadmap (9). E l'anello 7
non ha mai partorito in 59 tentativi.

Il ciclo non è da costruire: **è da ricongiungere**. Questa è la differenza fra
questa revisione e le due precedenti, che progettavano macchine nuove accanto a
una macchina che esisteva già.

## 3. Le quattro interruzioni, misurate

### 3.1 Il segnale del motore non arriva al generatore

`runtime/learning_loop.py:34`:

    _CAPABILITY_GAP_CLASSES = {"out_of_scope", "wrong_tool"}

Solo queste due classi possono generare una proposta. Misurato sul registro
vivo:

| classe | lacune |
|---|---|
| `out_of_scope` | 13 |
| **`wrong_tool`** | **0** |
| `capability_missing` | 4 — **non è nell'insieme** |

Cioè: `wrong_tool` non esiste nei dati, e `capability_missing` — la classe che
il motore emette davvero quando non sa fare una cosa — **non è ammessa
all'innesco**. L'unica porta viva è `out_of_scope`.

Conseguenza visibile: `learning_loop` ha prodotto 5 proposte, tutte fra il 13 e
il 19 luglio, e **zero nelle sei settimane successive**. Non è pigrizia del
generatore: è un cavo staccato.

> Avvertenza necessaria: `capability_missing` non significa «nessuno strumento
> sa farlo». È emessa da `_dropped_required_verbs` e significa «il piano ha
> perso un passo». Ricongiungere l'anello 3 richiede quindi **prima** di
> separare le due cose, non di collegare l'etichetta così com'è.

### 3.2 Il punteggio non è una scala

Una sola colonna `score` porta **cinque formule diverse**, una per famiglia:

| origine | come nasce il numero | n |
|---|---|---|
| `telos` | giudizio di `alignment_engine` | 43 |
| `observation` | `0.4 + 0.1 × ripetizioni` | 4 |
| `user` | `0.3 + 0.15 × rifiuti` | 3 |
| `introvertiva` | contatore d'uso | 4 |
| `synt` | tabella fissa di stato | 1 |

Due conseguenze misurate. **(a)** Le quattro proposte in cima (≥0,8) sono tutte
delle famiglie a contatore: il giudice non le ha mai viste, e il massimo che il
giudice produce è **0,724**. **(b)** `change_intents.py:404` fa
`max(vecchio, nuovo)`: il punteggio **può solo salire**. Nessuna calibrazione
verso il basso è nemmeno esprimibile.

Quindi una soglia di autonomia su questo numero è spingibile ripetendo una frase
e non è riabbassabile. **Non è un cancello, e non lo può diventare finché resta
una colonna sola.**

### 3.3 Il giudice del Vaglio è spento, e il suo prompt è scaduto

`workspace/TELOS.md` ha rimosso `t.coltivazione_strumenti` il 22/5/2026.
`runtime/prompts/it/vaglio.j2:15-22` — il prompt **vivo**, caricato da
`vaglio.py:241` — elenca ancora sette principi, quel fine compreso, con pesi che
non corrispondono più (0,22/0,13/0,18/0,18/0,09/0,08 contro
0,25/0,15/0,20/0,20/0,10/0,10).

**Ma la verifica successiva ha corretto la gravità, non il fatto.** Quel giudice
**non gira**: nessun chiamante passa `vaglio_judge` con un valore
(`dispatch.py:7162` inoltra solo il proprio parametro, sempre `None` a monte), e
il ramo che legge il prompt richiede `METNOS_JUDGE_KIND=llm-v1`, mentre il
default è `rule-based-v1` e la produzione non lo sovrascrive. Il prompt scaduto
è quindi su un componente **spento due volte**.

Il che sposta il rilievo e lo rende più interessante: il giudice di allineamento
del ciclo di crescita è `alignment_engine`, non `vaglio`. E `vaglio.judge` si
aggiunge all'elenco delle cose che **esistono, sembrano vive e non sono
collegate**.

### 3.4 Il rifiuto non insegna niente

`change_applier.py:272` dichiara: «Agent_runtime legge e usa come hard
constraint nel planner prompt». **Falso.** Ricerca esaustiva: gli unici
riferimenti sono chi scrive (`change_applier`) e chi cancella
(`change_rollback`). Nessun prompt lo legge; `agent_runtime` non lo apre.

Accettare un `reject_pattern` appende una riga a un file di 389 byte, fermo al
22/5, che nessuno consulta. È una funzione utente che **non fa nulla**, e la
documentazione pubblica dell'architettura ripete l'affermazione falsa.

## 4. Dove il ciclo è cieco

Non possiamo dire se Metnos cresce, perché non possiamo vedere:

1. **Quali turni sono reali.** Nella finestra strumentata, 294 turni su 324
   hanno come attore `host`, e attori chiamati `stack-migration-gate`,
   `stack-live-gate`, `codex-run-boundary-e2e` producono turni indistinguibili
   da quelli di una persona. Il 31% sono quattro query di collaudo ripetute.
2. **Quando un turno è fallito.** Il campo `error_class` di primo livello esiste
   solo dal 16/8/2026: 2954 turni su 3248 non possono portarlo. Ogni percentuale
   calcolata su tutto il corpus è un artefatto — è l'errore della revisione 2.
3. **Quanto il registro delle lacune si discosta dal vero.** Misurato: 27 turni
   contengono «ping», 10 con `capability_missing`; il registro ne conta 2, sotto
   la classe sbagliata. Sottoconta di un fattore 5.

**Nessun numero prodotto su questo corpus può decidere se aprire o chiudere un
lavoro sulla crescita.** Questa è la scoperta più utile delle tre revisioni.

## 5. Fasi non permutabili

> **Nessuna fase è iniziata.** Questo documento è una roadmap: l'implementazione
> comincia solo dopo l'approvazione di Roberto. Ogni fase produce un commit Git
> autonomo e inizia dopo che i criteri della precedente sono soddisfatti.

- **F0, osservabilità del ciclo.** Marcare nei turni l'attore di collaudo e
  distinguerlo dall'uso reale; portare l'esito su ogni turno chiuso, non solo
  sui build successivi al 16/8/2026. Nessun componente del ciclo di crescita
  viene toccato. *Completata quando* un turno reale è distinguibile da uno di
  collaudo senza interpretazione, ogni turno chiuso porta il proprio esito, e i
  quattro indicatori del §6 sono producibili a comando.
  **È la precondizione di ogni altra fase**: senza, nessun criterio d'uscita
  successivo è verificabile, ed è l'errore che ha invalidato le revisioni 1 e 2.

- **F1, censimento dei componenti inerti.** Produrre l'elenco verificato dei
  componenti del ciclo che esistono e non sono chiamati da nessuno, e per
  ciascuno registrare una decisione esplicita: collegare o rimuovere. I quattro
  noti sono `reject_pattern`, `vaglio.judge`, `decide_preexercise` e la catena
  `promoter`; il censimento deve cercarne altri, non fidarsi di questi. Ogni
  docstring che afferma un collegamento inesistente va corretta nello stesso
  passaggio, insieme alla documentazione pubblica che la ripete.
  *Completata quando* nessun componente del ciclo è in stato ambiguo e nessuna
  docstring del ciclo afferma un collegamento che non esiste.

- **F2, separazione delle scale di punteggio.** `score` diventa più campi con
  nome — allineamento, ricorrenza, rifiuti — e l'aggiornamento smette di
  applicare `max()` fra vecchio e nuovo: ogni valutatore scrive il proprio
  campo. *Completata quando* una correzione verso il basso è esprimibile,
  nessuna soglia legge una colonna che porta più formule, e l'origine di ogni
  numero è dichiarata nel dato invece che deducibile dal codice.
  Senza F2 nessuna soglia di autonomia è difendibile: oggi il numero è
  spingibile ripetendo una frase e non è riabbassabile.

- **F3, verità del registro delle lacune.** Riconciliare il registro con i
  turni e dichiarare lo scostamento; dare un proprietario alla lacuna, così che
  la ricorrenza sia contata per proprietario e non globalmente; separare «il
  piano ha perso un passo» da «nessuno strumento sa farlo», che oggi condividono
  la stessa etichetta. Entra dopo F0, perché lo scostamento si misura contro i
  turni. *Completata quando* lo scostamento registro/turni è dichiarato e sotto
  una soglia scritta, la ricorrenza è per proprietario, e le due cause hanno due
  nomi distinti.

- **F4, ricongiunzione del segnale.** Collegare la classe che indica davvero
  una capacità assente all'innesco delle proposte, oggi limitato a due classi di
  cui una inesistente nei dati. Entra dopo F3, perché prima le due cause non
  sono distinguibili. *Completata quando* una lacuna di capacità reale produce
  una proposta, verificata su un caso vero e non simulato, e la produzione di
  proposte torna diversa da zero.

- **F5, chiusura del ritorno.** Ciò che una persona decide deve avere un
  effetto osservabile sul comportamento successivo: `reject_pattern` collegato o
  rimosso (decisione presa in F1), e il riepilogo che fa transitare **tutti**
  gli stati bloccanti, non il solo `promoted_grace` — che è la ragione per cui
  quel canale non ha mai consegnato un messaggio. Entra dopo F4, perché prima
  non c'è nulla su cui decidere. *Completata quando* una decisione umana su una
  proposta cambia in modo verificabile ciò che il sistema fa dopo, e nessuna
  proposta bloccante resta invisibile oltre una finestra dichiarata.

- **F6, libertà modulata.** Dare a Metnos gradi di libertà diversi, poggiati su
  fatti **firmati** e non su etichette. È l'obiettivo del §1 che oggi non
  esiste nel codice, e la fase parte da tre misure già fatte:
  (i) l'identità firmata **non può** portare i quattro livelli — origine e
  autore restituiscono `SYNTHESIZED`/`MODEL` per tutti e quattro, e aggiungere
  un campo a `AdmissionContextV1` cambierebbe l'`admission_context_id` di ogni
  executor già nato, rompendo l'ancoraggio d'epoca; (ii) il manifest firmato
  porta già i fatti buoni — insieme delle capacità, ampiezza degli ambiti,
  reversibilità, classe d'esecuzione, stato del ciclo di vita; (iii) esiste già
  una politica che gradua l'ammissibilità, `decide_preexercise`, ed è **binaria
  e senza chiamanti in produzione**: nessuno deriva i suoi fatti da un executor
  reale.
  Il lavoro è quindi in tre passi, in quest'ordine: **derivare** i fatti dal
  manifest firmato invece che a mano; **graduare** l'esito, che oggi è
  ammesso/negato, in una scala di libertà dichiarata; **applicarla in un solo
  punto**, il collo di bottiglia dell'invocazione, dove oggi non esiste alcun
  controllo del ciclo di vita — la visibilità nel catalogo filtra chi il
  pianificatore *vede*, non chi può *agire*, e quindi lo stato `preexercise`
  oggi non toglie nulla e non concede nulla.
  Il rapporto con i quattro livelli va **misurato, non decretato**: il livello
  resta il linguaggio con cui una proposta è coniata, la libertà resta ciò che i
  fatti firmati concedono, e uno scostamento sistematico fra i due — un livello
  «creativo» che nasce sempre con la libertà del reattivo — è un indicatore che
  qualcosa non torna, non un errore da correggere a forza.
  **Entra dopo F5**, e l'ordine non è negoziabile: allargare la libertà di un
  ciclo il cui ritorno non si chiude significa concedere autorità a un
  meccanismo che non sa ancora essere corretto.
  *Completata quando* due executor nati con fatti firmati diversi hanno libertà
  osservabilmente diverse al momento di agire, la scala è dichiarata in un solo
  posto, ed è verificato su un caso reale che restringerla riduce davvero ciò
  che l'executor può fare.
  **Vincolo:** F6 non apre il preesercizio produttivo, che resta il cancello F5
  di RM-0008 e resta un non-obiettivo (§8).

- **FS, sicurezza della filiera — fuori sequenza.** Due interventi indipendenti
  fra loro e da tutte le fasi: (a) i comandi di shell dai test di nascita
  eseguiti fuori dall'involucro — il rimedio che chiude è far girare il runner
  dentro `sandbox.wrap_command`, il rifiuto dei campi nel manifest è il tampone;
  (b) `~/.config/metnos/**` assente da `vaglio._FORBIDDEN_PATH_PATTERNS`, che
  pure copre `.ssh`, `.gnupg` e `.aws/credentials`, mentre lì dentro sta la
  chiave da cui derivano tutte le altre.
  **(a) è differita da Roberto a dopo la chiusura di RM-0008.** Nessuna delle
  due dipende dalle fasi, e nessuna fase dipende da loro: possono partire quando
  Roberto lo decide.

## 6. Indicatori permanenti

Prodotti dal riepilogo notturno accanto agli altri, e guardati quando si decide
se il tema è vivo. Sopravvivono alla roadmap: restano anche quando nessuno ci
sta lavorando.

| indicatore | oggi | che cosa dice |
|---|---|---|
| **proposte nate da un bisogno reale**, ultimi 30 giorni | **0** (ultima il 19/7) | il segnale non arriva |
| **capacità arrivate in fondo**, sempre | **0** su 59 | la porta non ha mai partorito |
| **scostamento del registro dai turni** | fattore ~5 | il segnale mente |
| **quota di turni non di collaudo** | non misurabile | siamo ciechi (chiuso da F0) |
| **executor attivi con libertà diversa dalla massima** | **0** | la libertà non è modulata (chiuso da F6) |

**Regola di tensione:** finché il primo e il secondo restano a zero, il tema
della crescita è **aperto per definizione**, qualunque cosa dicano le
percentuali. Non serve rimisurare la dimensione del problema per sapere che un
ciclo con quattro anelli staccati non gira. È il modo per non ripetere l'errore
delle revisioni 1 e 2, che hanno cercato in un numero il permesso di lavorare.

## 7. Prove obbligatorie

Devono coprire: la distinzione fra attore di collaudo e attore reale, compreso
un turno che cambia attore a metà; un turno chiuso senza esito, che dopo F0 non
deve più esistere; un punteggio corretto verso il basso da un valutatore e non
risalito da un altro; una lacuna con lo stesso testo da due proprietari
distinti, che non deve sommarsi; una lacuna di capacità reale che arriva alla
proposta; una proposta rifiutata che cambia il comportamento successivo; e per
ogni componente censito in F1, una prova che fallisce se il componente torna
inerte.

Ogni cambio di codice del prodotto richiede almeno un turno reale su
`/agent/turn` nel dominio toccato (§8.5).

## 8. Non-obiettivi

- **L'anello 9, il preesercizio produttivo.** Appartiene alla fase F5 di
  RM-0008 e ha un cancello esplicito — cinque ammissioni reali riuscite da
  almeno due produttori — che oggi è a zero, e abbassarlo richiede una decisione
  di Roberto. RM-0009 non lo tocca e non lo anticipa.
- **I quattro livelli scritti nell'identità firmata.** Non è un non-obiettivo
  perché la libertà modulata non serva — Roberto ha deciso che serve, ed è la
  fase F6 — ma perché **quella via** è chiusa da una misura: origine e autore
  firmati restituiscono `SYNTHESIZED`/`MODEL` per tutti e quattro i livelli, e
  aggiungere un campo al contesto d'ammissione cambierebbe l'identità di ogni
  executor già nato. La libertà si modula sui fatti firmati del manifest; il
  livello resta il linguaggio della proposta.
- **La riprogettazione della catena `promoter`.** Esiste tutta e non ha mai
  consegnato nulla perché a monte non le arriva niente. Si ripara a monte, in
  F4; a valle basta F5.
- **`wrong_args`.** È la classe più numerosa, ma è il dominio della
  GUARD_PIPELINE, che ha già il suo giornale, il suo criterio di ritiro e il suo
  decisore (`CLAUDE.mutabile.md §11`). Non è una capacità mancante ed è fuori da
  questa roadmap.
- **`n_seen` come cancello di ammissione** prima di F3: la chiave della lacuna è
  scelta da chi manda il turno e il contatore è globale.

## 9. Completamento e condizioni di arresto

RM-0009 è completata quando il ciclo compie **un giro intero su un caso reale**:
un bisogno non servito diventa una lacuna vera, la lacuna una proposta, la
proposta un giudizio, il giudizio una decisione umana, la decisione una capacità
che nasce, si prova, e viene promossa o dimenticata — con ogni passaggio
osservabile negli indicatori del §6.

Si arresta, e va riaperta solo con una nuova decisione, se dopo F0 gli
indicatori mostrano che il ciclo gira già e che le interruzioni misurate erano
artefatti della cecità descritta al §4.

## 10. Che cosa resta a Roberto

**La sola domanda che questo documento gli poneva ha ricevuto risposta**
(4/9/2026): i quattro livelli devono dare a Metnos libertà diverse, o quanto
meno la libertà di Metnos deve essere modulata. È diventata la fase F6, ed è
l'unica fase che aggiunge un meccanismo invece di ricongiungerne uno esistente.

Le altre fasi non richiedono decisioni di progetto: sono ricongiunzioni di cose
che il progetto ha già deciso di avere. Resta perciò **una sola
autorizzazione, quella a cominciare** — e una scelta di fatto, se le due voci
di FS partano prima delle fasi o dopo la chiusura di RM-0008.
