# RM-0009 — Crescita allineata delle capacità

> **Nota editoriale temporanea — da eliminare all'approvazione.** Fino alla
> decisione finale il documento conserva la cronologia delle revisioni e le
> osservazioni avversariali e il dossier implementativo dei §§11-12 per rendere
> verificabile il confronto. Nel commit che recepisce l'approvazione, le
> decisioni accolte devono prima essere
> incorporate nel testo normativo e la testa deve acquisire i metadati minimi
> della raccolta: stato, creazione, ultima revisione, conservazione, stato reale
> dell'implementazione, fonti e prove. Soltanto dopo vanno eliminate questa nota,
> la cronologia discorsiva delle revisioni e gli interi §§11-12; deve restare
> il solo testo normativo approvato, con misure e riferimenti aggiornati alla
> stessa baseline.

> **Decisione di Roberto, 14 settembre 2026 — ambito globale e provenienza.**
> Le modifiche governate da RM-0009 sono globali. Il ciclo registra sia il
> componente che ha proposto la modifica sia `owner_id`, ma `owner_id` resta
> per ora un fatto di provenienza destinato ad audit, osservabilità e usi
> futuri: non è un gate in alcuna fase di RM-0009. Una modifica ammessa e
> attivata deve essere resa disponibile a tutti gli utenti.
>
> **Obiettivo di RM-0009, definito da Roberto il 14 settembre 2026.** Un
> sistema che **aumenta le proprie capacità, anticipa le necessità, ottimizza
> le proprie prestazioni e realizza i fini di TELOS**. Sostituisce la
> formulazione precedente del §1 e governa fasi, indicatori e completamento.
> Tutto **nel modo più autonomo possibile**: «l'utente deve intervenire solo in
> poche e semplici occasioni, il meno possibile».
>
> **Decisione di Roberto, 14 settembre 2026 (pomeriggio) — piano completo e sicurezza subito.**
> (1) La roadmap resta **completa**: fasi F0-F6 e piano operativo del §12. La
> proposta di ridurre la prima versione al solo «ricongiungere» è
> respinta. (2) Le due voci **FS** partono **subito**, come lavori separati da
> RM-0009, senza aspettarne l'approvazione: FS-A (un solo runner di nascita
> isolato, legacy irraggiungibile) e FS-B (nessuna chiave radice esposta agli
> executor, radici protette). Questo supera il differimento di FS(a) a dopo
> RM-0008 (decisione del 2/9, §5) e chiude la scelta lasciata aperta dal §10.
>
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

**Metnos è un sistema che:**

1. **aumenta le proprie capacità.** Si accorge di ciò che non sa fare, lo propone
   e fa nascere la capacità sotto la porta unica; la prova, poi la promuove o la
   dimentica;
2. **anticipa le necessità.** Propone ciò che servirà prima che la richiesta
   fallisca, partendo dagli schemi ricorrenti d'uso e dai fini di TELOS;
3. **ottimizza le proprie prestazioni.** Rende più veloce, economico e affidabile
   ciò che fa già: piani ripetuti, cache, guardie e executor inutilizzati;
4. **realizza i fini di TELOS.** Ogni proposta e ogni misura dichiarano quale
   fine servono (`workspace/TELOS.md`: tempo, ordine, puntualità, protezione,
   discrezione, parsimonia).

Tutto questo avviene in modo **gestito e il più possibile autonomo**. L'utente
interviene solo in **poche e semplici occasioni**: una domanda chiara, con
risposta sì o no, raccolta nel riepilogo e mai come interruzione
(`t.discrezione`). Decide Metnos tutto ciò che è reversibile, misurabile e
dentro le capacità già concesse; all'utente resta solo ciò che allarga
l'autorità, non è reversibile, costa o tocca dati sensibili.

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

**Ambito delle modifiche.** Change intent, regole e capacità che completano il
ciclo sono globali e, dopo l'ammissione, appartengono al catalogo globale. Ogni
intent conserva in forma strutturata e versionata `proposer_component_id`, cioè
il componente che ha scritto la proposta, e `owner_id` dell'evento che l'ha
originata; ulteriori contributi allo stesso intent restano eventi di
provenienza separati con il proprio `source_component_id`. In RM-0009
`owner_id` non entra in fingerprint, deduplicazione, soglie, ranking,
visibilità, decisione,
pubblicazione o autorità d'invocazione. Questa decisione riguarda la provenienza
del ciclo di crescita: non rimuove i controlli già esistenti sulla proprietà dei
dati e sulle richieste operative ordinarie.

**Disponibilità globale.** Quando una modifica raggiunge lo stato globale
`active`, deve entrare nello stesso catalogo e nello stesso routing disponibile
a tutti gli utenti, indipendentemente da componente e `owner_id` di origine.
Non sono ammessi rollout, visibilità, entitlement o varianti funzionali
per-owner. Una modifica che non è ancora sicura per tutti resta in
preesercizio/quarantena e non viene attivata soltanto per l'owner proponente.
I controlli ordinari sui dati, sugli effetti e sulle capability restano validi
e si applicano uniformemente: limitano l'operazione richiesta, non la
disponibilità della modifica in base alla sua provenienza.

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
  turni e dichiarare lo scostamento; registrare `owner_id` e
  `source_component_id` come provenienza senza usarli come gate; separare «il piano ha
  perso un passo» da «nessuno strumento sa farlo», che oggi condividono la
  stessa etichetta. La ricorrenza è globale sull'identità canonica del bisogno
  e somma una sola volta ogni evento autenticato, conservando separati i suoi
  contributori. Entra dopo F0, perché lo scostamento si misura contro i turni.
  *Completata quando* lo scostamento registro/turni è dichiarato e sotto una
  soglia scritta, provenienza e deduplicazione globale sono verificabili, e le
  due cause hanno due nomi distinti.

- **F4, ricongiunzione del segnale.** Collegare la classe che indica davvero
  una capacità assente all'innesco delle proposte, oggi limitato a due classi di
  cui una inesistente nei dati. Entra dopo F3, perché prima le due cause non
  sono distinguibili. La proposta risultante è globale e porta
  `proposer_component_id`, oltre alla provenienza strutturata
  `source_component_id`/`owner_id` dell'evento originario, senza acquisire uno
  scope per-owner. Dopo l'attivazione la capacità deve essere pubblicata nel
  catalogo comune per tutti gli utenti. *Completata quando* una lacuna di
  capacità reale produce una proposta, verificata su un caso vero e non
  simulato, e la produzione di proposte torna diversa da zero.

- **F5, chiusura del ritorno.** Ciò che una persona decide deve avere un
  effetto osservabile sul comportamento successivo: `reject_pattern` collegato o
  rimosso (decisione presa in F1), e il riepilogo che fa transitare **tutti**
  gli stati bloccanti, non il solo `promoted_grace` — che è la ragione per cui
  quel canale non ha mai consegnato un messaggio. Entra dopo F4, perché prima
  non c'è nulla su cui decidere. *Completata quando* una decisione umana su una
  proposta cambia in modo verificabile ciò che il sistema fa dopo, e nessuna
  proposta bloccante resta invisibile oltre una finestra dichiarata. Per una
  modifica attivata, l'effetto e l'invalidazione delle cache devono raggiungere
  il catalogo e il routing di tutti gli utenti, non soltanto dell'owner di
  provenienza.

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
risalito da un altro; due eventi autenticati dello stesso bisogno provenienti
da `owner_id` distinti, che convergono nello stesso aggregato e intent globale
senza perdere la provenienza né duplicare lo stesso evento; una lacuna di
capacità reale che arriva alla proposta; una proposta rifiutata che cambia il
comportamento successivo; una modifica originata dall'owner A che, dopo
l'attivazione globale, è disponibile anche agli owner B e C senza consentire
loro di accedere ai dati di A; e per ogni componente censito in F1, una prova
che fallisce se il componente torna inerte.

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

## 11. Osservazioni temporanee per la revisione avversariale

> **Materiale di revisione, non norma.** Questi rilievi registrano la verifica
> eseguita il 10 settembre 2026 sul worktree corrente e sulla release immutabile
> in esercizio. Non autorizzano fasi, non modificano i criteri e non devono
> sopravvivere all'approvazione: le risposte accolte vanno incorporate nei
> paragrafi normativi; rilievi, risposte, cronologia e questa sezione vanno poi
> cancellati come indicato nella nota editoriale iniziale.

### 11.1 Verdetto e baseline

**Verdetto provvisorio: il documento non è approvabile né promuovibile a
`ready` nella forma corrente.** I rilievi seguenti contraddicono fatti e criteri
ancora presenti nei §§2-10; non basta conservare il §11 durante il confronto e
poi cancellarlo. Serve una nuova revisione normativa che recepisca o respinga
esplicitamente ogni rilievo, seguita da una nuova verifica avversariale sulla
versione pulita e congelata.

La diagnosi centrale è confermata: segnale delle lacune scollegato, punteggio
non confrontabile e solo crescente, `vaglio.judge` non collegato,
`reject_pattern` scritto ma non consumato, preesercizio inattivo. La vista
amministrativa viva conferma inoltre **55 change intent attualmente nello stato
`proposed`**, non 55 proposte nell'intera storia: 43 `telos`, 4 `observation`,
3 `user`, 4 `introvertiva`, 1 `synt`; quattro hanno punteggio almeno 0,8 e il
massimo `telos` è 0,724. L'ultima `observation` ancora presente nella vista è
del 19 luglio 2026. Qualunque affermazione sulla popolazione storica deve invece
comprendere tutti gli stati e le transizioni.

La baseline va però resa esplicita e unica. La release in esercizio al momento
della verifica è la sequenza immutabile 5 ed è operativa; il worktree è molto
sporco e non coincide integralmente con essa. La revisione 4, prima
dell'aggiunta di queste note temporanee, aveva SHA-256
`1171c436834d06e60c2658acbc1dbc933240148616d35f4d64c37f335aee3fd3`, coerente
con il passaggio di consegne RM-0008; nel worktree corrente il documento non è
però ancora tracciato da Git e l'indice della raccolta è una modifica non
registrata. La revisione deve scegliere e registrare commit, identificativo e
digest della release, istante delle misure e query o snapshot delle evidenze;
non può combinare codice del worktree, dati vivi e numeri storici senza
dichiararne la provenienza. Un digest scritto nel file non può identificare la
stessa versione che lo contiene: la baseline sottoposta ai revisori va congelata
da un commit o da una ricevuta esterna prima della decisione finale.

### 11.2 Numeri storici e indicatori

I conteggi dei §§2-4 sono dichiaratamente una fotografia del 3 settembre. La
vista viva conferma soltanto la distribuzione degli intent oggi `proposed`. Il
registro delle lacune, non esposto da quella vista, è la fonte necessaria a
ricalcolare 13/0/4; la storia degli intent, delle ammissioni e delle transizioni
è invece la fonte distinta necessaria a provare «0 su 59». Inoltre «4 righe
contro 17-27 turni» confronta eventi con chiavi aggregate: il registro deduplica
una lacuna e ne aumenta `n_seen`.
Lo scostamento di circa 5 volte non è quindi ancora una misura dimostrata e va
degradato a ipotesi finché una query causale non confronta, nella stessa
finestra e con gli stessi filtri, gli eventi attesi con la somma di `n_seen`.
Tutti questi numeri devono restare marcati come storici finché una prova
ripetibile non li rigenera sulla baseline scelta.

Ogni indicatore permanente deve definire almeno: evento sorgente, schema e
versione, identità del proprietario, finestra temporale, denominatore,
trattamento di record legacy/ignoti e query ripetibile. «Diverso da zero» non è
da solo un criterio di salute: può premiare più fallimenti o duplicazioni. Il
giro completo deve essere ricostruibile tramite identificativi causali e
ricevute, non per uguaglianza approssimativa del testo.

### 11.3 F0 — provenienza e verità osservabile

La necessità di F0 è confermata: negli ultimi 1.000 turni esposti dalla release,
929 hanno attore `host` e quindici appartengono ad attori di collaudo nominati,
ma manca una classificazione strutturata e affidabile test/reale. Il marker non
deve essere dedotto dal nome dell'attore né accettato liberamente dal chiamante:
deve provenire dal confine autenticato che avvia il turno, restare immutabile e
registrare esplicitamente i cambi d'attore.

«Portare l'esito su ogni turno chiuso» non può significare inventare dati per i
turni storici. Lo schema deve ammettere almeno `unknown/not_observed` per ciò che
non era strumentato, distinguendolo da successo e fallimento. La revisione deve
anche precisare quale esito prevale in un turno parzialmente riuscito e come si
lega l'esito di primo livello agli errori dei singoli passi.

I 929 record `host` e i quindici attori nominati come collaudo sono soltanto
limiti inferiori, non una classificazione attestata; la vista amministrativa
non espone neppure `error_class`. Inoltre F0 promette «i quattro indicatori» ma
il §6 ne elenca cinque, e tre dipendono anche da causalità F3, nascita RM-0008 o
modello F6. Prima dell'approvazione va fissato il sottoinsieme che F0 può davvero
produrre e quello che deve restare `unknown` fino alle fasi proprietarie.

«Uso reale», «finalità di collaudo» e «percorso produttivo attraversato» sono
dimensioni diverse. Una sonda E2E su `/agent/turn` usa il percorso produttivo ma
resta collaudo, non domanda spontanea dell'utente. Il requisito del §7 deve
separarle e correggere il rinvio a «§8.5», che non identifica alcun paragrafo di
questa roadmap.

### 11.4 F1 — componenti inerti non è una sola categoria

Il testo chiama «senza chiamanti» quattro casi diversi: `vaglio.judge` e
`decide_preexercise` non hanno un chiamante produttivo; `reject_pattern` ha un
produttore e un osservatore ma nessun consumatore comportamentale; `promoter`
è schedulato e gira, ma resta affamato o seleziona uno stato troppo stretto.
Il censimento deve distinguere almeno **irraggiungibile**, **non consumato**,
**gated per progetto** e **vivo ma senza input**. Altrimenti la prova richiesta
per evitare una futura regressione non può essere la stessa per tutti.

Anche la rappresentazione sintetica del §2 va rifatta: «la catena gira ogni
notte» non può descrivere gli anelli senza chiamanti; l'aritmetica «tre
funzionano, uno funziona per progetto» non è ricavabile senza una tassonomia; e
l'anello 8 non «funziona» come transizione, perché il modulo shadow emette solo
un rapporto senza publisher, firma o lifecycle writer. La tabella deve separare
esistenza, raggiungibilità, alimentazione, effetto e gate esterno per ogni
anello.

### 11.5 F2-F4 — dati, causalità e criteri non manipolabili

Separare le colonne di punteggio è necessario ma non sufficiente. Occorre
distinguere segnali grezzi, valutazioni calibrate e decisione finale; elencare
tutti i lettori e le migrazioni; vietare confronti fra scale diverse; conservare
versione e autore del valutatore. Una correzione verso il basso deve essere
possibile senza cancellare la storia che la giustifica.

Il record corrente non permette neppure di attribuire con sicurezza il massimo
alla famiglia mostrata: fingerprint uguali possono convergere, `origin_family`
resta quella della prima inserzione e l'upsert prende il massimo proposto da
qualunque adattatore. F2 deve quindi conservare valutazioni append-only con
famiglia, autore, versione, istante e valore, oltre ai campi correnti derivati;
la migrazione legacy deve dichiarare ciò che non è ricostruibile.

L'obiettivo richiede che una proposta sia giudicata sui fini dichiarati, ma
nessuna fase oggi impone che **ogni famiglia** attraversi un giudizio TELOS
verificabile prima della decisione o della nascita. F1 potrebbe perfino decidere
di rimuovere `vaglio.judge`, che non è il giudice del ciclo. La revisione deve
assegnare questa responsabilità a una fase e definire la ricevuta del giudizio;
se il giudizio non è applicabile, deve esistere un esito tipizzato, non un salto
silenzioso.

La decisione del 14 settembre chiude l'ambito: modifiche, intent e capacità
risultanti sono globali. Ogni evento deve comunque conservare `owner_id` e
`source_component_id`; il record dell'intent conserva anche
`proposer_component_id` e l'`owner_id` dell'evento originario, mentre contributi
successivi restano in una relazione append-only. `owner_id` non partecipa a chiave di
ricorrenza, fingerprint, soglie o gate. La chiave globale deve usare l'identità
canonica del bisogno e un evento idempotente, non il solo testo della richiesta.
Una modifica che arriva ad `active` deve essere pubblicata e instradata per
tutti gli utenti; non esiste uno stato `active` limitato all'owner proponente.
La separazione fra passo perso e capacità realmente assente deve avvenire nel
punto che possiede la prova, con una categoria `unknown` quando la prova non
basta. Questa scelta non autorizza a confondere capacità globalmente assente con
capacità esistente ma vietata, non collocabile o temporaneamente indisponibile.

Il criterio F4 «la produzione di proposte torna diversa da zero» è osservativo,
non sufficiente: una proposta duplicata o nata da un errore di classificazione
lo soddisferebbe. Serve una traccia reale completa che dimostri causa, assenza
di capacità, deduplicazione, provenienza corretta e proposta conseguente; il
conteggio non deve poter crescere ripetendo la stessa richiesta.

Soglia di riconciliazione F3, finestra F5, insieme degli stati bloccanti e scala
F6 non possono essere scelti dopo l'autorizzazione come dettagli attuativi:
sono ciò che decide se una fase è passata. Devono essere normativi prima dello
stato `ready`, insieme alle regole di migrazione e alle prove negative.

### 11.6 F5 — effetto del rifiuto e consegna

Se `reject_pattern` viene collegato, la regola risultante è globale: deve avere
identità canonica, `proposer_component_id` e `owner_id` di provenienza, scadenza, precedenza,
falsi positivi, audit e rollback, ma non uno scope per-owner. Proprio perché
l'effetto è globale, una formulazione proveniente da un singolo owner può
soltanto proporre la regola: non può attivarla senza il decisore globale
autorizzato. Altrimenti diventerebbe un canale di avvelenamento. Se viene
rimosso, documentazione, adattatore, stati e rollback devono sparire nello
stesso passaggio, senza lasciare un altro componente fantasma.

«Tutti gli stati bloccanti» deve diventare un insieme chiuso e versionato. La
consegna deve essere idempotente, ritentabile, osservabile e separare mancato
invio da decisione assente; una notifica non prova che la decisione abbia
modificato il comportamento successivo.

La frase «prima di F4 non c'è nulla su cui decidere» è smentita dal backlog
attuale di 55 intent `proposed`. F5 può dipendere da F4 per provare il nuovo
segnale, ma deve dichiarare come tratta il backlog preesistente e perché non può
verificare già ora almeno il percorso decisione-effetto su un caso ammesso.

### 11.7 F6 — aggiornamento necessario dopo RM-0008

La frase «oggi non esiste alcun controllo del ciclo di vita» resta vera nei
colli di bottiglia produttivi, ma il quadro successivo alla revisione 4 è più
articolato. RM-0008 ha portato in esercizio identità e ricevute firmate e ha
aggiunto nel bridge durevole un ramo capace di rifiutare lifecycle diverso da
`active`, quarantena e generazioni non attestate. Quel ramo è però condizionato
da `require_generation_attestation`, il cui default è `False`, e il binding
produttivo costruisce il bridge senza attivarlo né fornirgli l'attestatore. Il
normale `agent_runtime.invoke_executor` non applica a sua volta un controllo
equivalente. Il testo normativo deve quindi distinguere **fondazione presente**
da **enforcement produttivo assente**, indicare RM-0008/F5 come proprietaria
dell'attivazione e impedire che F6 introduca un secondo proprietario
dell'ammissione.

Anche la premessa «stessa libertà per ogni executor attivo» è troppo larga e
non provata: la sandbox deriva già accessi di rete e filesystem dalle capability
del manifest, quindi executor diversi possiedono oggi perimetri diversi. La
revisione deve nominare la dimensione realmente assente — per esempio autorità
d'invocazione graduata e attestata — e non usare «libertà massima» come quantità
indefinita. Finché quella dimensione non ha dominio e misura, anche l'indicatore
«executor attivi con libertà diversa dalla massima = 0» resta un'ipotesi.

La scala di libertà deve avere un nome distinto sia dai tier LLM sia dai livelli
di autonomia dell'utente già esistenti. Non esiste però un «minimo» scalare fra
permessi multidimensionali: lettura di rete e scrittura locale, per esempio,
sono incomparabili. L'autorità effettiva deve essere l'intersezione tipizzata di
capacità e ambiti già concessi, mai una scala capace di inventare un permesso.
Lifecycle non `active`, ricevuta o attestazione assente, identità discordante e
stato legacy ignoto sono **veto** che negano l'invocazione; non devono degradare
a un grado che consenta comunque lettura o esfiltrazione.

La politica dell'utente/canale non va descritta come una guardia già operativa:
oggi `autonomy_level` raggiunge il contesto del planner, mentre il parametro
omonimo della sandbox è informativo e molti chiamanti passano `supervised`.
F6 deve o introdurre un valutatore deterministico nel confine proprietario, o
dichiarare questa integrazione come precondizione. La decisione deve legarsi
all'invocazione esatta — proprietario operativo e attore autenticati, canale,
ContractId e
generazione, lifecycle, `admission_context_id` e versione del registro sandbox,
argomenti finali e loro provenienza, capacità effettive, destinazione, limiti del
workload, politica utente/canale e approvazioni o mandati consumati — e produrre
una ricevuta non riusabile su altri argomenti, proprietari operativi, contesti o
generazioni. Il proprietario operativo della richiesta non è l'`owner_id` di
provenienza del change intent. Quest'ultimo viene registrato nella ricevuta per
audit ma, per la decisione del 14 settembre, non determina l'autorità
dell'executor né introduce un gate nel ciclo RM-0009. Restano separatamente
validi gli attuali controlli di proprietà sui dati e sulle richieste operative.

Gli argomenti finali non esistono oggi in un unico istante precedente al bivio
locale/remoto: il ramo locale completa iniezione e risoluzione dopo la scelta di
destinazione. La logica di valutazione può essere unica, ma deve essere applicata
dopo trasformazioni specifiche della destinazione e immediatamente prima del
relativo effetto, senza effetti durante la selezione. Per il remoto occorrono
inoltre ricevuta monouso, scadenza e versione di stato, con nuova verifica sul
consumer immediatamente prima dell'esecuzione; quarantena, ritiro o cambio di
generazione devono invalidare gli elementi già accodati.

I fatti nel manifest sono dichiarazioni firmate, non prova automatica del
comportamento. Reversibilità, effetti, ambiti e capacità devono essere legati
alle evidenze dell'ammissione. La classe d'esecuzione governa risorse e
concorrenza, non autorità; reversibilità ed equivalenza sono condizioni o prove,
non fonti di nuovi permessi. I campi ammessi alla derivazione devono essere un
insieme chiuso e monotono: possono restringere capability e ambiti già ammessi,
mai crearli o saltare un'approvazione.

F6 entra dopo F5 di questa roadmap, ma l'enforcement lifecycle appartiene a
RM-0008/F5, tuttora dietro il proprio cancello. Fino alla certificazione di quel
cancello, F6 può soltanto derivare e misurare decisioni in ombra; l'applicazione
produttiva deve dipendere esplicitamente da RM-0008/F5. Il criterio «due
executor hanno libertà diverse» è inoltre soddisfacibile con una differenza
cosmetica: le prove devono coprire locale, remoto, durevole, ripresa e cambio di
generazione/lifecycle, mostrando un diniego prima di undo, coda remota o
subprocess e registrando versione della politica, fatti d'ingresso e motivo.

### 11.8 FS — precisare entrambi i confini di sicurezza

Entrambi i rilievi FS sono confermati anche nella release in esercizio, ma il
rimedio (a) va riallineato a RM-0008. Il percorso sintetico chiama ancora
`synth_request._validate_birth_tests`, che avvia il vecchio `test_runner.py`;
quel runner non esegue sull'host soltanto `setup` e `teardown`: avvia direttamente
anche il codice candidato con `python3`, i riferimenti pytest e i casi paralleli
con ambiente derivato dall'host. Setup e teardown non hanno un timeout proprio,
e il timeout esterno non attesta la terminazione di figli e nipoti. Il «tampone»
citato nel testo normativo non esiste: la grammatica Birth ammette ancora
`tests[].setup`, `teardown` ed `env`, e il generatore copia i due comandi prodotti
dal modello. In parallelo esiste ora `executor_birth_runner.py`, volutamente
indipendente, con isolamento Birth fail-closed. La revisione deve scegliere un
solo runner certificato, migrare o ritirare **tutte** le superfici del percorso
legacy e provare la terminazione dell'albero dei processi; prescrivere soltanto
`sandbox.wrap_command` rischia di creare due modelli di isolamento concorrenti.

FS(a) non è indipendente dalla nascita reale: finché quel percorso resta
raggiungibile, nessuna nuova nascita automatica né la chiusura della roadmap può
essere certificata in sicurezza. Deve diventare prerequisito di ogni prova che
esegua codice candidato, anche se la sua implementazione resta fuori dalla
sequenza funzionale F0-F6.

Per (b), aggiungere la stringa `~/.config/metnos/**` a una lista di espressioni
regolari non basta: alias, percorsi assoluti, link e `XDG_CONFIG_HOME` possono
indicare la stessa radice. La protezione deve derivare dalla radice di
configurazione effettiva e coprire configurazione, vault, chiave amministrativa
e autorità Birth. Un semplice `resolve()` prima dell'uso non chiude la gara fra
controllo e apertura: il confine filesystem deve usare handle stabili,
`openat`/no-follow o equivalente e concedere eccezioni tipizzate soltanto a chi
porta un mandato firmato. Il mandato non può però autorizzare l'accesso ai byte
grezzi di chiavi amministrative o Birth: deve autorizzare un broker tipizzato
che compie l'operazione o restituisce la sola proiezione minima. Nessuna chiave
radice deve essere montata dentro un executor, neppure in sola lettura; la
capability oggi chiamata `metnos:credentials_metadata_only` non può giustificare
il mount di `admin.key`. Il Vaglio testuale, che ragiona sugli argomenti, resta
difesa in profondità e non il proprietario unico del confine filesystem.

### 11.9 Criterio finale e domande da demolire

Un solo giro reale dimostra raggiungibilità, non affidabilità né sicurezza. La
chiusura dovrebbe richiedere anche almeno un rifiuto efficace, una quarantena o
restrizione realmente applicata, un retry idempotente e un caso legacy
fail-closed. «Nessuna proposta invisibile» non è provabile senza una sorgente
completa: il criterio positivo deve richiedere che ogni evento nel registro
sorgente riceva, entro una finestra fissata, una sola ricevuta causale nel
registro destinazione. Anche il retry deve indicare confine e chiave di
deduplicazione. «Promossa o dimenticata» va sostituito con stati terminali e
regole di conservazione verificabili.

Il completamento attuale richiede che la capacità «si prova» e venga promossa o
dimenticata, ma il preesercizio produttivo è contemporaneamente un non-obiettivo
vincolato a RM-0008/F5. Anche l'anello 8 è sovrastimato: `executor_birth_shadow`
produce un rapporto osservativo e dichiara di non possedere publisher, firma o
writer del lifecycle. La roadmap deve rendere RM-0008/F5 una precondizione
esplicita del proprio completamento oppure restringere il giro finale a ciò che
RM-0009 può davvero concludere.

La condizione di arresto dopo F0 non può dichiarare artefatti i difetti statici
già provati, come `max(score)` o un registro senza consumatore. F0 può correggere
denominatori e gravità, non smentire da sola collegamenti assenti. Ogni fase deve
produrre un manifest di evidenze con baseline, query, ordinamento, intervallo,
timezone, universo, identificativi e ricevute; il riferimento rotto «§8.5» va
sostituito da una fonte precisa.

La pulizia editoriale finale è autorizzabile soltanto dopo una checklist che
dimostri: ogni rilievo accolto incorporato nella norma; ogni rilievo respinto
chiuso con motivazione; metadati minimi permanenti presenti; criteri, soglie e
dipendenze congelati; baseline di approvazione registrata fuori dal file. In
caso contrario cancellare il §11 eliminerebbe le sole cautele che rendono oggi
visibili le insufficienze.

La revisione avversariale deve dare risposta esplicita almeno a queste domande:

1. quale unica baseline e quale manifest di evidenze rendono ripetibili tutti i
   numeri;
2. chi può attestare che un turno è di collaudo e come si rappresenta l'ignoto;
3. come la deduplicazione globale somma una volta sola eventi autenticati dello
   stesso bisogno, conservando `source_component_id` e `owner_id` di ogni contributo senza
   trasformarli in gate;
4. come si prova una capacità assente senza confonderla con un piano difettoso;
5. quale giudice TELOS vede tutte le famiglie e quale ricevuta ne prova l'esito;
6. quali stati chiusi alimentano il riepilogo e quale ricevuta prova l'effetto
   della decisione;
7. quali condizioni sono veto e quale intersezione di capacità definisce
   l'autorità effettiva dell'invocazione esatta;
8. come F6 dipende da RM-0008/F5 senza creare una seconda ammissione;
9. quale runner di nascita resta autorevole e come viene eliminato quello
   legacy prima di eseguire altro codice candidato;
10. quali prove negative impediscono di chiudere la roadmap con un giro solo
    formalmente completo;
11. quali decisioni e metadati devono essere nel testo normativo prima di
    cancellare cronologia e osservazioni;
12. quale broker sostituisce ogni esposizione di chiavi radice agli executor;
13. come vengono revocate e ricontrollate le autorizzazioni già accodate quando
    cambiano lifecycle, generazione, politica o approvazioni.

### 11.10 Review di Claude, 14 settembre 2026 — criteri di progettazione

> Materiale di revisione, non norma: segue la sorte del §11. Criteri indicati da
> Roberto: **KISS, utilità per l'utente, soluzioni universali e non ad hoc,
> niente hardcoding, efficienza**. La review rispetta le decisioni del 14/9
> (piano completo F0-F6 e §12, ambito globale, FS subito): non toglie fasi,
> propone come farle più semplici.

**Fatti verificati nel codice.**

| Affermazione | Verifica |
|---|---|
| L'innesco accetta solo `out_of_scope` e `wrong_tool` | `learning_loop.py:34`: vero |
| Il punteggio può solo salire | `change_intents.py:405`, `max(existing, new)`: vero |
| `vaglio.judge` spento | nessun chiamante passa `vaglio_judge`; default `rule-based-v1`: vero |
| `reject_pattern` senza consumatore | lo scrive `change_applier` e lo cancella `change_rollback`, nessuno lo legge: vero. La doc pubblica `architecture/lifecycle.html` ripete l'affermazione falsa |
| FS(a) test di nascita sull'host | `synth_request.py:72` lancia `python3 test_runner.py`: vero |
| FS(b) chiavi radice esposte | `sandbox.py:549`: `metnos:credentials_metadata_only` monta nell'executor il vault **e `admin.key`** |

**Verdetto per fase.** ✓ = rispetta il criterio, ~ = in parte, ✗ = no.

| Fase | KISS | Utilità utente | Universale | Hardcoding | Efficienza | Intervento proposto |
|---|---|---|---|---|---|---|
| F0 | ✗ sei campi di provenienza | indiretta | ✓ confine autenticato | rischio sui nomi degli attori | ✓ | due campi: **origine** (`user\|test\|system`, dal tipo di principal autenticato) ed **esito** chiuso |
| F1 | ✗ modulo registro a sei stati | ~ onestà | ✗ elenco nominale | — | ✓ | decidere e agire (collegare o togliere); un **test generico** «writer senza lettore» invece del registro |
| F2 | ~ | indiretta | ✓ | ✓ | ~ la tabella cresce | una tabella di valutazioni (intent, dimensione, valore, valutatore, istante); proiezione = ultima per (intent, dimensione); niente `confidence` né normalizzazioni finché non servono; conservazione |
| F3 | ✗ sette tipi di lacuna | ~ | rischio `canonical_need_id` ad hoc | — | ~ | tre tipi (`capability_absent`, `plan_error`, `unknown`); identità del bisogno = la **firma d'intento esistente** |
| F4 | ✓ | **alta** | ✓ | soglia oggi da env | ✓ | innesco solo su `capability_absent`; soglia nel registro di politica centrale |
| F5 | ✗ outbox nuovo | **alta** | ✗ duplica | — | ✓ | **riusare l'outbox di consegna esistente**, canale-neutrale (`durable_workloads/models.py:377`), e `/admin/changes` |
| F6 | ✗ nonce, TTL e ricevute su tre percorsi | bassa oggi (0 nascite su 59) | ✓ se il punto è unico | — | ✗ una scrittura per invocazione | un solo punto: `invoke_executor` è già il «choke-point universale locale e remoto», e il durevole passa da `invoke_scheduled`; funzione pura in memoria; ricevuta solo sulle code remota e durevole |
| processo §12 | ✗ | — | — | — | ✗ | vedi il punto 1 |

**1. KISS.**
- **Tre documenti in uno.** 1577 righe, di cui la norma è la parte minore. La
  rev.5 normativa dovrebbe restare sotto circa 500 righe. Per ogni fase: beneficio
  per l'utente, cambiamento minimo, punto di riuso, prova d'accettazione.
- **Il §12 è pesante.** Aggiunge almeno sette strutture nuove, dodici decisioni
  preliminari e un processo per unità: manifest JSON, cinque stati, riesecuzione
  byte-identica da un secondo agente. Il 14/9 RM-0008 ha misurato questo costo
  (cause C1-C3 in `internal/coordination/analisi-definitiva-ciclo-rilascio.md`,
  worktree `rm0008-reboot`).
  - Evidenza sufficiente: test mirati, commit e un turno reale.
  - Il manifest JSON serve soltanto a P0, per rigenerare i numeri della baseline.
- **Regola:** nessuna struttura nuova se ne esiste già una che fa lo stesso.

**2. Utilità per l'utente.**
- Il valore arriva solo con F4-F5; F0-F3 sono strumentazione. Si può anticipare
  il valore già disponibile senza violare l'ordine delle fasi:
  - **(a) `reject_pattern`.** Oggi è una funzione per l'utente che dichiara un
    effetto e non lo produce: viola il principio §2.8 (nessun fallimento
    silenzioso). Va sistemata subito, collegandola o togliendola dalla UI e
    dalla doc; non aspetta F5.
  - **(b) I 55 intent `proposed`.** Il triage del backlog in F1 dà subito un
    beneficio all'utente: meno rumore in `/admin/changes`.
- **Manca un indicatore di valore reale:** capacità nate e poi usate in turni
  reali, ultimi 30 giorni. Senza di esso il ciclo può «girare» senza servire a nessuno.
- **F6 non porta valore all'utente finché le nascite restano 0 su 59.** Resta
  ultima, come è già.

**3. Universale, non ad hoc.**
- **Identità del bisogno.** Riusare `_compute_intent_sig`
  (`engine/autopath.py:298`: verbo, oggetto, qualificatore), non un nuovo
  `canonical_need_id` con regole proprie.
- **Giudizio TELOS.** Un solo giudice per tutte le famiglie, `alignment_engine`.
  `vaglio.judge` va ritirato, non conservato come secondo giudice.
- **Capacità assente.** La ricerca negativa riusa prefiltro e catalogo del
  motore, non una ricerca parallela.
- **Componenti inerti.** Al posto del censimento nominale, un test generico che
  fallisce se un writer del ciclo non ha lettori o una funzione pubblica del
  ciclo non ha chiamanti. Vale anche per i componenti futuri.
- **Collaudo o uso reale.** Lo decide il tipo di principal autenticato (un
  utente di collaudo registrato come tale), mai i nomi degli attori.

**4. Niente hardcoding.**
- **Soglie.** Riconciliazione F3, ricorrenza F4 (oggi `METNOS_PROPOSE_SEEN=3`),
  finestra F5 e scala F6 vivono in un solo registro di politica versionato, non
  come letterali nei moduli.
- **Enumerazioni.** Esito, tipi di lacuna e stati sono definiti una sola volta
  come fonte tipizzata, come `vocab.py`, e riusati da codice, UI e test.
- **Niente tabelle di prefissi o di stringhe per classificare gli errori**
  (lezione di RM-0008 del 14/9): classi d'errore tipizzate.
- **Testi per l'utente** (proposte, notifiche) solo tramite i18n.

**5. Efficienza.**
- **Tabelle append-only** (valutazioni, eventi di lacuna, decisioni F6): manca una
  politica di conservazione. Servono una scadenza e un'aggregazione notturna,
  altrimenti crescono senza limite.
- **Percorso di ogni turno e invocazione** (F0, F6): calcolo in memoria, nessuna
  scrittura sincrona per invocazione; audit aggregato o campionato.
- **Indicatori:** li calcola il riepilogo notturno esistente
  (`nightly_orchestrator.NIGHTLY_SEQUENCE`), non un job nuovo.
- **Sviluppo:** una consegna per fase con test mirati e un turno reale; la
  riesecuzione da un secondo agente resta solo per P0.

**6. Copertura dell'obiettivo del 14/9.** Il piano copre bene il primo punto e
poco gli altri tre. Proposta KISS: stesso ciclo, stesso intent, stesso giudice,
stessa decisione; cambia soltanto la **sorgente** del segnale.

| Punto dell'obiettivo | Copertura oggi nel piano | Meccanismi già esistenti da riusare | Intervento |
|---|---|---|---|
| Aumenta le capacità | F3 → F4 → F5, nascita via RM-0008 | lacune del motore, `learning_loop` | già nel piano |
| Anticipa le necessità | **assente**: il piano è solo reattivo (parte dai fallimenti) | ciclo introspettivo TELOS notturno (`telos_introspect.py`, 10 lenti, famiglia `telos`: 43 intent `proposed`); autopath shadow da turni ripetuti (`engine.autopath.seed_from_run`); famiglia `observation`; routine RM-0001 (solo progetto) | una **seconda sorgente** di F4 (schemi ricorrenti e lenti TELOS) nella stessa pipeline. Per `t.discrezione` le proposte anticipate passano dal riepilogo (F5), mai da interruzioni |
| Ottimizza le prestazioni | **assente** | promozione L0/L1 (`autopath`), `guard_stats.dormant()`, aging degli executor, telemetria LLM ed executor | una **terza sorgente** nella stessa pipeline: turno ripetuto costoso → autopath, guardia dormiente → ritiro, executor inutilizzato → aging. Indicatori: latenza e costo per turno reale, esito, hit delle cache |
| Realizza i fini di TELOS | parziale: `alignment_engine` giudica solo la famiglia `telos` | `alignment_engine`, `telos_loader`, `workspace/TELOS.md` | ogni intent e ogni indicatore portano il **fine servito**. L'utilità per l'utente si misura per fine (per esempio `t.tempo`: incombenze ripetitive automatizzate; `t.puntualita`: scadenze intercettate; `t.parsimonia`: costo per turno) |

Conseguenze sul documento:
- il §6 riceve tre indicatori nuovi: proposte anticipate accettate e poi usate;
  andamento di latenza e costo per turno reale; valore per fine TELOS;
- il §9 chiude soltanto quando il ciclo ha fatto un giro reale per ciascuna
  delle tre sorgenti (lacuna, anticipazione, ottimizzazione), ciascuno legato a
  un fine TELOS;
- nessuna pipeline nuova: le sorgenti sono adattatori verso gli stessi intent
  (F2-F5).

**7. Autonomia: l'utente interviene il meno possibile.** Oggi il piano mette
una persona su ogni proposta (anello 6, `/admin/changes`, triage, riepilogo) e
chiede dodici decisioni preliminari (§12.2). È l'opposto dell'obiettivo.
Proposta KISS: **una sola regola deterministica** decide chi decide. È la stessa
funzione della libertà modulata (F6), derivata dai fatti firmati e non da
etichette.

| Tipo di cambiamento | Chi decide | Come si torna indietro |
|---|---|---|
| Ottimizzazione dentro capacità esistenti (promozione di autopath e cache, ritiro di una guardia dormiente, aging di un executor inutilizzato) | **Metnos, da solo** | ritorno automatico se gli indicatori peggiorano oltre la soglia di politica |
| Capacità nuova, **reversibile**, senza rete in uscita né credenziali, allineata a un fine TELOS | **Metnos, da solo**, dopo il preesercizio riuscito | undo per esecuzione e ritiro automatico se non viene usata |
| Capacità che allarga l'autorità (scrittura irreversibile, rete in uscita, credenziali, costo), o proposta sotto la soglia di allineamento | **utente**: una domanda sì/no nel riepilogo, con il fine TELOS servito e l'effetto spiegato in una riga | rifiuto che resta: `reject_pattern` funzionante |
| Regola globale che cambia il comportamento per tutti (per esempio un rifiuto ricorrente) | **utente**, una volta sola | rollback della regola |

Conseguenze sul piano:
- **anello 6:** da «una persona decide sempre» a «la politica decide, la persona
  solo nei casi della tabella»;
- **F5:** il riepilogo contiene soltanto le poche domande riservate all'utente;
  le decisioni autonome compaiono come resoconto, senza richiedere nulla;
- **F6 si anticipa come regola di decisione, in ombra:** calcola chi deciderebbe
  anche prima dell'enforcement, così l'autonomia si misura fin dall'inizio;
- **§12.2:** le dodici decisioni non vanno chieste a Roberto. Gli agenti le
  fissano con valori prudenti nel registro di politica; a Roberto resta la sola
  approvazione del documento;
- **indicatore nuovo del §6:** interventi richiesti all'utente per settimana,
  con l'obiettivo di tenerli bassi, e tempo di risposta mediano.

**Priorità per la rev.5.**
1. Riscrivere i §§1-10 incorporando il §11 e questa review; per ogni fase:
   utilità, cambiamento minimo, riuso, prova.
2. Ridurre il §12 a una tabella di unità (file, punto di riuso, prova), oppure
   eliminarlo alla rev.5 come prescrive la nota editoriale.
3. Mettere in testa a F1 `reject_pattern` (§2.8) e il triage dei 55 intent.
4. Aggiungere agli indicatori del §6 quello del valore reale (capacità nate e usate).
5. Allineare i §§1, 5, 6 e 9 all'obiettivo del 14/9: anticipazione e ottimizzazione
   come sorgenti della stessa pipeline, e fine TELOS su ogni intent e indicatore.
6. Sostituire «una persona decide» con la regola di autonomia del punto 7,
   derivata dai fatti firmati; l'utente riceve solo domande sì/no nel riepilogo.

## 12. Analisi implementativa temporanea per agenti medium

> **Dossier operativo, non norma e non autorizzazione.** Questa sezione traduce
> i rilievi del §11 in unità di lavoro abbastanza piccole e determinate da poter
> essere affidate singolarmente a un agente di fascia medium. Descrive una
> soluzione raccomandata, ma non può colmare con una scelta tecnica le decisioni
> normative ancora aperte. Quando la roadmap sarà approvata, le parti accolte
> dovranno essere incorporate nei §§2-10 e questo intero §12 dovrà essere
> cancellato insieme al §11 e alla cronologia, come prescritto dalla nota
> editoriale iniziale.

### 12.1 Contratto di esecuzione per ogni agente

Un agente riceve **una sola unità** fra quelle elencate sotto. Non deve avviare
la fase successiva, non deve distribuire una release e non deve reinterpretare
una decisione mancante. Ogni unità usa questi stati:

- `BLOCKED_DECISION`: manca una decisione normativa applicabile fra quelle del
  §12.2;
- `BLOCKED_BASELINE`: worktree, release o dati osservati non corrispondono alla
  baseline congelata;
- `BLOCKED_SAFETY`: il lavoro richiederebbe allargare permessi, esporre un
  segreto, eseguire codice candidato fuori dal runner Birth certificato o
  indebolire una prova;
- `READY_FOR_REVIEW`: codice e migrazione sono completi, le prove passano e il
  manifest di evidenze è stato prodotto;
- `ACCEPTED`: un revisore diverso dall'autore ha verificato diff, manifest e
  prove negative.

Il flusso obbligatorio di ogni unità è:

1. leggere `CLAUDE.md`, `CLAUDE.mutabile.md`, l'eventuale `AGENTS.md` della
   directory e la versione approvata di RM-0009;
2. registrare `git rev-parse HEAD`, `git status --short`, identificativo e
   digest della release attiva e UTC dell'osservazione;
3. fermarsi con `BLOCKED_BASELINE` se un file da modificare contiene cambi
   preesistenti non attribuiti all'unità o se un file necessario non è leggibile;
4. cercare nuovamente simboli e chiamanti con `rg`: i riferimenti di questo
   dossier sono punti di partenza, non permesso di modificare alla cieca una
   riga che nel frattempo si è spostata;
5. scrivere prima la prova che fallisce, poi il cambiamento minimo, poi la
   migrazione idempotente; non rigenerare firme, identità o fixture per far
   passare una prova;
6. eseguire le prove mirate, poi tutte le suite dei moduli toccati e infine il
   gate generale previsto dalle istruzioni del repository;
7. eseguire una sonda reale soltanto se l'unità la richiede, con autorizzazione
   esplicita e senza dati sensibili; una simulazione non va etichettata `real`;
8. produrre il manifest di evidenze e consegnare diff, rischi residui e comando
   di rollback. L'agente non dichiara da solo `ACCEPTED`.

Ogni unità deve creare una directory
`internal/reports/rm0009-implementation-<YYYYMMDD>/<unit-id>/` contenente almeno
`manifest.json` e, quando applicabile, gli snapshot redatti delle query. Il
manifest usa uno schema chiuso con questi campi minimi:

```json
{
  "schema_version": 1,
  "roadmap": "RM-0009",
  "unit_id": "F0.1",
  "source_commit": "<sha Git>",
  "release": {"id": "<id>", "digest": "sha256:<digest>"},
  "observed_from_utc": "<RFC3339>",
  "observed_to_utc": "<RFC3339>",
  "files_changed": ["<path>"],
  "migration_ids": [],
  "tests": [{"command": "<comando>", "exit_code": 0,
             "artifact_sha256": "sha256:<digest>"}],
  "invariants": [{"id": "<id>", "result": "pass", "evidence": "<path>"}],
  "live_probe": {"performed": false, "reason": "not required"},
  "open_risks": [],
  "rollback": "<procedura verificata>"
}
```

Il manifest non contiene query utente, token, credenziali, chiavi, argomenti
sensibili o dump integrali dei turni. I conteggi devono indicare query, finestra,
timezone, ordinamento, universo e trattamento di `unknown`; un hash senza la
fonte e i parametri non è una prova ripetibile.

#### Matrice minima dei comandi di prova

Il file nuovo nominato nella tabella deve essere creato dall'unità prima di
usare il comando. Questi sono i minimi, non sostituiscono le suite aggiuntive
imposte dai file toccati né il gate generale finale.

| unità | comando minimo dopo l'implementazione |
|---|---|
| P0 | `python3 -m pytest tests/internal/test_rm0009_evidence.py -q` |
| F0 | `python3 -m pytest tests/runtime/infra/test_turn_provenance.py tests/runtime/http/test_http_turn_ownership.py -q` |
| F1 | `python3 -m pytest tests/runtime/learning/test_growth_cycle_registry.py -q` |
| F2 | `python3 -m pytest tests/runtime/learning/test_change_intent_evaluations.py tests/runtime/learning/test_change_intent_adapters.py tests/runtime/learning/test_change_intents.py -q` |
| F3 | `python3 -m pytest tests/runtime/engine/test_lacuna_events.py tests/runtime/engine/test_terminator_operational_blame.py -q` |
| F4 | `python3 -m pytest tests/runtime/learning/test_learning_loop.py tests/runtime/engine/test_lacuna_events.py -q` |
| F5 | `python3 -m pytest tests/runtime/learning/test_promoter_digest.py tests/runtime/learning/test_change_applier.py tests/runtime/learning/test_change_intents_ui.py -q` |
| FS-A | `python3 -m pytest tests/runtime/infra/test_executor_birth_runner.py tests/portable/test_executor_birth_runner_linux_real.py -q` |
| FS-B | `python3 -m pytest tests/runtime/safety/test_credentials.py tests/runtime/safety/test_sandbox_runtime_bind.py tests/runtime/remote/test_invocation_scope.py -q` |
| F6 | `python3 -m pytest tests/runtime/safety/test_invocation_authority.py tests/runtime/durable_workloads/test_execution_bridge.py tests/runtime/remote/test_invocation_scope.py -q` |
| chiusura | `python3 runtime/run_all_tests.py` |

Un exit code zero non basta: il manifest deve riportare numero di prove raccolte,
passate, saltate e fallite. Uno skip nuovo o un test non raccolto è un fallimento
finché non è motivato e approvato; `-k`, `xfail`, aumento dei timeout o riduzione
del corpus non sono rimedi ammessi a una regressione.

### 12.2 Decisioni che devono precedere il codice

`P0` può essere assegnata per costruire la baseline e rendere ripetibili le
misure. Prima di assegnare una successiva unità di prodotto, devono essere
recepite nel testo normativo, con valori chiusi e non con segnaposto, tutte le
decisioni da cui quell'unità dipende:

1. baseline unica di approvazione e luogo esterno al documento che ne conserva
   commit, release e digest;
2. autorità ammesse a emettere la provenienza di un turno e regola immutabile
   per finalità, percorso e cambio di attore;
3. funzione totale dell'esito del turno, compresi `partial`, `needs_input` e
   `unknown/not_observed`;
4. definizione completa dei cinque indicatori: sorgente, denominatore, finestra,
   soglia, legacy e query;
5. destinazione di ciascun componente F1: collegare, ritirare, conservare come
   `gated` o riparare come `alive_starved`;
6. giudice TELOS obbligatorio per ogni famiglia e forma della sua ricevuta;
7. classificatore autorevole di `capability_absent` e soglia di ricorrenza F4;
8. ramo scelto per `reject_pattern`, insieme chiuso degli stati bloccanti e
   finestra massima di consegna F5;
9. vocabolario chiuso dell'autorità d'invocazione, veto, TTL, chiave di
   deduplicazione e politica di revoca F6;
10. certificazione del cancello RM-0008/F5 richiesta prima dell'enforcement F6
    e prima di qualsiasi esecuzione di codice candidato;
11. unico runner Birth autorevole e politica di ritiro del runner legacy;
12. API tipizzata che sostituisce l'esposizione di vault, `admin.key` e chiavi
    Birth agli executor.

Una decisione è già chiusa e non va riaperta dall'agente: **l'ambito è globale**.
Ogni change intent conserva `proposer_component_id`, versione del componente e
`owner_id` di provenienza; nessuno di questi campi crea uno scope per-owner e
`owner_id` non partecipa ad alcun gate di RM-0009. Ogni modifica attivata deve
essere disponibile nel catalogo e nel routing di tutti gli utenti. Se questa
condizione non è sicura, la modifica non è attivabile e resta nel lifecycle di
prova globale; l'agente non inventa un rollout per-owner.

Se anche uno solo dei valori necessario all'unità assegnata manca, l'agente
registra `BLOCKED_DECISION`: non sceglie il valore che rende più facile il test.

### 12.3 Ordine e grafo delle dipendenze

L'ordine funzionale resta `P0 → F0 → F1 → F2 → F3 → F4 → F5 → F6`.
Le unità di sicurezza possono iniziare dopo `P0`, ma impongono questi cancelli:

```text
P0 ──> F0 ──> F1 ──> F2 ──> F3 ──> F4 ──> F5 ──> F6.1/F6.2 (shadow)
 │             │                                      │
 │             └──────── decisione reject_pattern ────┤
 ├──> FS-A (runner Birth unico) ───────────────────────┤
 └──> FS-B (radici protette e broker) ────────────────┤
RM-0008/F5 certificata ────────────────────────────────┴──> F6.3/F6.4 (enforcement)
```

`FS-A` deve essere `ACCEPTED` prima di qualunque prova che esegua codice
candidato. `FS-B` deve essere `ACCEPTED` prima di dichiarare sicuro F6. F6.1 e
F6.2 possono soltanto misurare decisioni in ombra; F6.3 e F6.4 non possono
entrare in enforcement finché RM-0008/F5 non è certificata. Una dipendenza non
si soddisfa con un mock: serve la ricevuta del progetto proprietario.

### 12.4 P0 — congelare baseline e strumenti di prova

**Obiettivo.** Impedire che codice del worktree, dati vivi e release immutabile
vengano combinati in una stessa misura.

**File da aggiungere.** Un report sotto `internal/reports/` secondo §12.1; se
serve uno strumento ripetibile, aggiungere uno script strettamente read-only in
`internal/tools/` e `tests/internal/test_rm0009_evidence.py`. Non modificare dati
vivi né database per produrre la baseline.

**Passi.**

1. congelare in Git la revisione normativa candidata e registrarne il commit;
2. interrogare il meccanismo già autorevole di release e registrare sequenza e
   digest, senza dedurli dal worktree;
3. acquisire snapshot redatti separati per turni, eventi di lacuna, intent e
   transizioni; ogni snapshot deve riportare la query esatta e non soltanto il
   risultato;
4. rigenerare 13/0/4, 0/59, 55 `proposed` e il presunto fattore 5. Se una fonte
   non conserva l'informazione necessaria, scrivere `not_reconstructable` e non
   ricostruirla dal testo;
5. verificare che l'utente di test possa leggere `install/data/i18n_seed.sqlite`
   e i manifest necessari. Correggere i permessi soltanto nel meccanismo che li
   produce, mai con un `chmod` locale usato come prova;
6. far rieseguire lo strumento a un secondo agente sulla stessa baseline: i
   risultati devono essere byte-identici salvo timestamp esplicitamente esclusi.

**Uscita.** Manifest completo, snapshot separati e tabella che marca ogni numero
come `reproduced`, `changed` o `not_reconstructable`. Senza questa uscita nessuna
fase successiva può usare i numeri del §3 o §6.

### 12.5 F0 — provenienza ed esito osservabile dei turni

#### F0.1 — tipo chiuso e funzione dell'esito

**File principali.** Aggiungere `runtime/turn_provenance.py`; modificare
`runtime/agent_runtime.py` nei simboli `TurnLog`, `run_turn` e `TurnLog.write`;
aggiungere `tests/runtime/infra/test_turn_provenance.py`.

Il nuovo modulo deve possedere tipi immutabili e versionati. Non usare booleani
ambigui come `is_test`. La forma minima raccomandata è:

- `purpose`: `user | test | maintenance | unknown`;
- `execution_path`: `production | shadow | unit_test | unknown`;
- `principal_kind`: `person | service | device | unknown`;
- `issuer`: identificatore chiuso del confine autenticato;
- `evidence_id`: riferimento opaco alla prova dell'issuer;
- `schema_version`: `1`.

La richiesta HTTP o il testo dell'utente non possono impostare questi campi.
Per un E2E su `/agent/turn` il valore corretto è `purpose=test` e
`execution_path=production`. I record precedenti restano `unknown`: è vietato
retrodatarne la classificazione dal nome dell'attore.

La funzione pura `derive_closed_outcome(final_kind, steps, effect_counts)` deve
restituire soltanto il dominio approvato al §12.2. Deve avere una tabella di casi
esaustiva nel test, compresi successo senza mutazioni, successo con effetto,
errore, richiesta di input, esito parziale e record legacy non osservabile.
`TurnLog.write` calcola l'esito una volta dopo `effect_counts`; nessun adapter di
canale lo ricalcola.

**Prove negative.** Campo sconosciuto rifiutato; issuer sconosciuto rifiutato;
tentativo del payload HTTP di dichiararsi `user` ignorato/rifiutato; record
legacy letto come `unknown`; un errore di passo seguito da output utile segue la
regola `partial` approvata e non viene promosso a successo.

#### F0.2 — propagazione da tutti i confini autenticati

**File principali.** `runtime/http_routes_agent.py` (`turn`, `turn_submit` e il
worker che chiama `agent_runtime.run_turn`), `runtime/channels/daemon.py`,
`runtime/recurring_tasks.py`, `runtime/change_applier.py`,
`runtime/agent_server.py`, `runtime/orchestration.py` e ogni altro risultato di:

```text
rg -n "run_turn\(" runtime tests
```

Creare factory interne per HTTP, canali, recurring, apply e resume. Una factory
riceve identità già autenticata dal confine e costruisce `TurnProvenanceV1`; non
accetta un dizionario proveniente dalla request. Il resume conserva `purpose` e
`execution_path` del turno origine e aggiunge un evento di attore. Se cambia
proprietario, non muta il turno: apre un nuovo turno correlato oppure rifiuta,
secondo la decisione normativa.

Aggiornare tutte le fake `run_turn` nei test quando il parametro diventa
obbligatorio. È vietato lasciare il default `user`: i chiamanti non migrati
devono produrre `unknown` o fallire esplicitamente, secondo il confine.

**Uscita.** Inventario dei chiamanti con una riga per chiamante, issuer assegnato
e prova. Nessuna occorrenza produttiva resta senza classificazione dichiarata.

#### F0.3 — proiezione amministrativa e primi indicatori

**File principali.** `runtime/http_routes_admin.py`, nella proiezione di
`/admin/turns`; il job notturno che sarà scelto come proprietario degli
indicatori; test HTTP sotto `tests/runtime/http/`.

Esporre solo campi strutturati redatti: outcome, purpose, execution path,
principal kind, issuer, owner opaco e versione schema. Non esporre query o
evidence grezza per calcolare statistiche. F0 pubblica soltanto gli indicatori
che possiede: quota per purpose/path/outcome e quota `unknown`. Gli indicatori
che dipendono da F3, RM-0008 o F6 restano `not_observed`, non zero.

**Accettazione F0.** Un turno umano reale e un E2E attraversano entrambi HTTP
produttivo ma risultano distinti per purpose; un resume/cambio attore segue la
regola approvata; ogni nuovo turno chiuso ha un outcome; il legacy resta ignoto;
nessun client può auto-attestarsi.

### 12.6 F1 — censimento e destino dei componenti

#### F1.1 — registro esplicito del ciclo

**File.** Aggiungere `runtime/growth_cycle_registry.py` e
`tests/runtime/learning/test_growth_cycle_registry.py`; correggere nello stesso
commit le docstring dei moduli censiti e la documentazione pubblica che descrive
collegamenti inesistenti.

Il registro contiene, per ogni componente: `component_id`, modulo/simbolo,
producer, consumer, effetto atteso, proprietario del gate e uno fra
`connected`, `unreachable`, `unconsumed`, `gated`, `alive_starved`, `retired`.
Una voce `connected` senza producer, consumer o prova deve fallire. Il test può
usare import e AST per i collegamenti statici, ma la prova di effetto deve essere
comportamentale; una stringa trovata con `rg` non dimostra un chiamante.

Il censimento iniziale deve includere almeno `vaglio.judge`,
`decide_preexercise`, `reject_pattern`, `promoter`,
`executor_birth_shadow` e ogni ulteriore componente trovato cercando writer
senza reader e funzioni pubbliche senza chiamanti.

#### F1.2 — applicare decisioni, senza inventarle

Per ogni voce, applicare **soltanto** la decisione approvata:

- `retired`: eliminare chiamanti, adapter, job, configurazione, documentazione,
  rollback e test obsoleti nello stesso cambiamento; migrare i record esistenti
  in uno stato terminale esplicito;
- `gated`: rendere visibile il proprietario del gate e aggiungere una prova che
  fallisce se il componente agisce senza ricevuta;
- `alive_starved`: correggere la sorgente o l'insieme chiuso degli input e
  provare consegna, retry e deduplicazione;
- `unconsumed`: aggiungere il consumer comportamentale approvato, oppure
  ritirare il writer; non lasciare un log che finge un effetto;
- `connected`: aggiungere una prova di regressione che osserva l'effetto finale.

Il giudice TELOS del ciclo non va identificato automaticamente con
`vaglio.judge`: deve essere il simbolo scelto al §12.2 e produrre la ricevuta
richiesta per **ogni** famiglia di intent.

**Accettazione F1.** Nessuno stato ambiguo; ogni voce ha un test della propria
semantica; documentazione e codice concordano; un revisore può togliere un
collegamento e vedere fallire la prova corretta.

### 12.7 F2 — valutazioni append-only e scale non confondibili

#### F2.1 — schema e migrazione

**File principali.** `runtime/change_intents.py`; nuovo test
`tests/runtime/learning/test_change_intent_evaluations.py`; adapter sotto
`runtime/change_intent_adapters/`; lettori trovati con:

```text
rg -n "score|min_score|score_desc|origin_family|change_intents" runtime tests
```

Non sostituire `score` con tre colonne mutabili. Aggiungere un registro
append-only `change_intent_evaluations` con almeno: id valutazione, intent id,
dimensione chiusa, valore grezzo canonico, valore normalizzato opzionale,
famiglia del valutatore, id e versione del valutatore, istante, evento sorgente
e vincolo idempotente sull'evento. Le dimensioni minime sono quelle approvate
fra `alignment`, `recurrence`, `rejections` e `confidence`; scale diverse non
sono ordinabili se manca una funzione calibrata e versionata.

Estendere inoltre `change_intents` con `proposer_component_id`,
`proposer_component_version` e `owner_id` dell'evento originario, e aggiungere
una relazione append-only `change_intent_sources` per ogni contributo
successivo: source event id, `source_component_id`/versione, `owner_id`, istante ed
evidenza. Il fingerprint e l'id dell'intent restano globali; questi campi sono
provenienza, non filtri né autorità. `origin_family` non sostituisce il
componente produttore. Lo scope della modifica è il valore costante e
versionato `global_all_users`; non viene calcolato dall'owner.

Per i nuovi intent, `proposer_component_id`; per i nuovi eventi,
`source_component_id` e `owner_id` sono assegnati dall'adapter
core-owned a partire dal contesto autenticato: non possono essere testo libero
del modello, campi dell'intent o valori scelti dal chiamante. L'assenza ammessa
deve essere il valore tipizzato `unknown`, mai stringa vuota o inferenza dal
contenuto.

Conservare temporaneamente `change_intents.score` come
`legacy_score_max_v0`, solo per compatibilità di lettura. Non può alimentare
gate, ranking di autonomia o decisioni nuove. La migrazione crea per ogni riga
esistente una valutazione `legacy_unknown` col valore originario; non attribuisce
il valore a `origin_family`, perché il massimo può essere stato scritto da
un'altra famiglia. Per i record storici anche componente e `owner_id` restano
`unknown` quando la fonte non li prova: non vanno dedotti dal testo. Migrazione
e rollback devono essere idempotenti e non cancellare valutazioni.

#### F2.2 — writer, projection e correzioni verso il basso

Ogni adapter deve chiamare una sola API `append_evaluation(...)` e poi
l'upsert/dedup dell'intent. Rimuovere il `max(old_score, new_score)` da ogni
percorso decisionale. La proiezione corrente può scegliere l'ultima valutazione
valida per `(intent, dimensione, valutatore)` e calcolare un risultato soltanto
con una formula versionata. Una correzione scrive una nuova valutazione: non
aggiorna né cancella la precedente.

La deduplicazione dell'intent e l'idempotenza dell'evento sono due invarianti
diversi: ripetere il job sullo stesso `source_event_id` non aggiunge convergenza,
mentre un nuovo evento equivalente può incrementare la ricorrenza.

#### F2.3 — ricevuta TELOS

Prima che un intent possa essere deciso o generare una nascita, deve esistere
una valutazione `alignment` del giudice approvato, legata a fingerprint,
contenuto valutato, versione del valutatore e politica. `not_applicable` è un
esito tipizzato con motivo; assenza di ricevuta non equivale a
`not_applicable`. Aggiungere una prova parametrica per tutte le famiglie
registrate.

**Accettazione F2.** Una valutazione 0,8 corretta a 0,4 resta 0,4 nella
proiezione senza perdere la storia; una valutazione di altra famiglia non la fa
risalire; due esecuzioni dello stesso evento non duplicano; nessuna famiglia
salta il giudizio TELOS.

### 12.8 F3 — eventi di lacuna, provenienza e riconciliazione

#### F3.1 — separare eventi e aggregati

**File principali.** `runtime/engine/terminator.py`, i punti di dispatch che
producono `_dropped_required_verbs` o equivalenti,
`tests/runtime/engine/test_lacuna_events.py` e i test esistenti del terminator.

La tabella corrente aggrega per hash e incrementa `n_seen`. Aggiungere prima un
registro append-only `lacuna_events` con: `event_id` idempotente, `owner_id` di
provenienza, actor osservato, `source_component_id` e sua versione, turn id,
step, istante, `gap_kind`, evidenza strutturata, versione classificatore e
`canonical_need_id`. Il dominio minimo di `gap_kind` è
`plan_step_missing | capability_absent | capability_denied |
placement_unavailable | temporarily_unavailable | out_of_scope | unknown`.
Query, testo libero e `owner_id` non sono chiavi di autorità.

Costruire l'aggregato globale per `(canonical_need_id, gap_kind)` come vista o
proiezione ricostruibile e conservare a parte gli eventi contributori con i
rispettivi `owner_id` e componenti. Le righe legacy diventano
`owner_id=unknown`, `source_component_id=unknown`, `gap_kind=unknown` e
`provenance=legacy`; possono essere osservate ma non ammesse a F4.

#### F3.2 — classificare dove esiste la prova

Il punto che vede piano, catalogo candidato, esito della selezione e dispatch
deve emettere l'evento. `plan_step_missing` significa che una capacità esisteva
ma il piano non l'ha inclusa; `capability_absent` richiede una ricerca negativa
nel registro globale verificato. Una capacità esistente ma non autorizzata,
non collocabile o temporaneamente indisponibile usa rispettivamente
`capability_denied`, `placement_unavailable` o `temporarily_unavailable` e non
può alimentare F4. Insufficienza di prova è `unknown`. Il terminator persiste la
decisione e non la reinventa dal solo `error_class`.

L'evento deve portare `source_request_id`/turn id e un id deterministico del
fatto; retry e resume dello stesso fatto non aumentano la ricorrenza. Eventi
autenticati dello stesso bisogno provenienti da owner diversi convergono nello
stesso aggregato globale, ma restano due contributi di provenienza distinti.

#### F3.3 — riconciliazione

Confrontare eventi sorgente e `lacuna_events` nella stessa finestra e con gli
stessi filtri. Il denominatore è il numero di eventi attesi, non il numero di
righe aggregate; per il vecchio registro confrontare anche `sum(n_seen)` ma
marcarlo legacy. La soglia deve essere quella approvata, non scelta dopo aver
visto il risultato. Produrre elenco causale di mancanti, duplicati e orfani.

**Accettazione F3.** Stesso bisogno/due owner converge nello stesso aggregato e
somma una volta ciascun evento distinto senza usare `owner_id` come gate; retry
identico non incrementa; passo perso, capacità assente e capacità negata hanno
categorie diverse; legacy ignoto non innesca proposte; scostamento ripetibile
sotto la soglia normativa.

### 12.9 F4 — dal solo gap provato alla proposta

**File principali.** `runtime/learning_loop.py`,
`runtime/engine/terminator.py`, `runtime/change_intents.py` e
`tests/runtime/learning/test_learning_loop.py`.

Sostituire l'innesco basato su classi di errore con un input tipizzato che
accetta esclusivamente un aggregato `capability_absent` corredato dagli eventi
causali F3. `unknown`, `plan_step_missing`, `capability_denied`,
`placement_unavailable`, `temporarily_unavailable`, `out_of_scope` e record
legacy devono essere rifiutati fail-closed. La soglia usa eventi unici globali;
il fingerprint deduplica l'intent globale, mentre la valutazione di ricorrenza
registra i nuovi eventi tramite F2.

La proposta deve conservare almeno `proposer_component_id` e versione,
`owner_id` dell'evento originario, canonical need, ids degli eventi,
classificatore/versione, fingerprint e ricevuta TELOS. Gli
`owner_id`/`source_component_id` contributori restano in
`change_intent_sources`. Ripetere lo stesso evento non crea una nuova
proposta né aumenta il punteggio. Un nuovo evento equivalente può aggiornare la
valutazione append-only senza cambiare l'origine storica dell'intent. Né
`owner_id` originario né quelli contributori limitano la portata globale.

**Prove.** Una prova unitaria per ogni `gap_kind`; due owner che convergono in
un solo intent globale; retry; soglia meno uno/soglia; due eventi con testo
diverso ma stesso need canonico; stesso testo con need diverso. La prova reale
finale deve partire da una richiesta umana
autenticata, dimostrare la ricerca negativa e arrivare all'id della proposta.
Un inserimento diretto nel database o un mock vale per regressione, non per
accettazione.

**Accettazione F4.** Una e una sola proposta globale causalmente legata a un gap
reale; nessuna proposta dagli altri tipi; deduplicazione e provenienza provate;
nessuno scope per-owner. Il mero conteggio `> 0` non chiude la fase.

### 12.10 F5 — decisione, consegna ed effetto

#### F5.1 — outbox delle transizioni

**File principali.** `runtime/change_intents.py` (`_transition` e wrapper),
`runtime/http_routes_admin.py` (`admin_change_action`),
`runtime/jobs/promoter_digest.py`, `runtime/change_applier.py` e relativi test
sotto `tests/runtime/learning/` e `tests/runtime/http/`.

Aggiungere un outbox append-only transazionale. Ogni transizione verso uno
stato appartenente all'insieme chiuso approvato crea una riga unica per
`(intent_id, state, state_version, recipient, notification_kind)`. Stati minimi
della consegna: `pending | sent | acknowledged | failed`, con tentativi,
`retry_at`, ultimo errore tipizzato e ricevuta. Il digest legge l'outbox, non una
sola tabella legacy filtrata su `promoted_grace`. Invio fallito e decisione
mancante sono metriche diverse.

Migrare il backlog di 55 `proposed` secondo una regola esplicita: creare eventi
bootstrap con una versione dedicata oppure escluderli con motivo registrato. È
vietato farli sparire cambiando la finestra. Due esecuzioni notturne non devono
duplicare la notifica; un crash dopo invio deve essere riconciliabile.

#### F5.2 — ramo `reject_pattern`

Eseguire soltanto il ramo deciso in F1.

- **Collegare:** regola globale con fingerprint strutturato,
  `proposer_component_id` e `owner_id` di provenienza, ambito esatto, scadenza, precedenza, policy
  version, decision receipt, audit e rollback. Il consumer opera su struttura
  validata prima del planner e non inserisce testo del rifiuto nel prompt.
  L'owner originario può proporre ma non attivare la regola: serve il decisore
  globale autorizzato. `owner_id` non limita né amplia l'effetto; scadenza e
  rollback ripristinano il caso per tutti.
- **Ritirare:** eliminare kind, writer, adapter, reader amministrativi, job,
  rollback e documentazione nello stesso cambiamento. I record esistenti
  ricevono uno stato terminale `retired`/equivalente con motivo; non vengono
  cancellati né lasciati `proposed`.

#### F5.3 — ricevuta dell'effetto

La decisione umana deve legarsi a intent fingerprint, valutazioni F2,
`proposer_component_id` e `owner_id` di provenienza, contenuto visto, policy e
identità del decisore globale. Componente e `owner_id` sono fatti di audit, non
gate.
L'applicazione produce una ricevuta separata con prima/dopo, effetto globale o
motivo di nessun effetto, e id della decisione. Notifica, decisione e
applicazione sono tre eventi distinti. L'attivazione deve aggiornare in modo
atomico il catalogo globale e invalidare le viste/cache di routing di tutti gli
utenti. Un fallimento parziale mantiene la modifica non attiva per tutti; non è
ammesso uno stato in cui soltanto l'owner proponente la vede o la usa.

**Accettazione F5.** Un caso reale ammesso viene consegnato entro la finestra,
deciso una volta, applicato/respinto in modo osservabile e ritentato senza
duplicazione. Una decisione cambia il comportamento successivo oppure produce
un esito terminale motivato. Se attivata, la modifica è visibile e instradabile
per almeno due utenti diversi da quello di provenienza, ferme le rispettive
autorizzazioni sui dati. Nessuno stato bloccante resta senza riga outbox.

### 12.11 FS-A — un solo runner certificato per ogni codice candidato

**File principali.** `runtime/synth_request.py` (`_validate_birth_tests` e la
serializzazione dei test), `runtime/test_runner.py`,
`runtime/executor_birth_runner.py`, `runtime/executor_birth_identity.py` e
`tests/runtime/infra/test_executor_birth_runner.py`.

#### FS-A.1 — inventario prima della migrazione

Enumerare tutti i chiamanti di `test_runner.py` e tutti i manifest che usano
`tests[].setup`, `teardown`, `env`, comandi Python diretti, riferimenti pytest o
casi paralleli. L'inventario va eseguito sia sulla baseline Git sia sulla
release immutabile. Un file illeggibile blocca l'unità; non equivale a zero
occorrenze.

#### FS-A.2 — migrare nel runner Birth

`executor_birth_runner.run_birth_phase` diventa l'unica API per eseguire codice
candidato. La traduzione dal manifest accetta soltanto operazioni fixture
core-owned e argv tipizzati; vieta shell, `setup`, `teardown`, `env` arbitrario
e riferimenti pytest eseguiti direttamente sull'host. Eventuali fixture legacy
vanno tradotte una per una in operazioni chiuse già ammesse dal runner, non
passate a `/bin/sh` dentro una nuova wrapper.

Ogni caso deve avere timeout nel runner, isolamento di rete/processi/user/ipc,
ambiente allowlist, working directory effimera, limiti di risorse, handshake di
setup e terminazione attestata dell'intero albero dei processi. Se l'isolamento
non è disponibile, l'esito è `test_environment_unavailable`, mai successo né
candidate failure.

#### FS-A.3 — rendere irraggiungibile il legacy

Solo dopo migrazione e prove: rimuovere la chiamata da `synth_request`, togliere
i campi legacy dalla grammatica di `executor_birth_identity`, migrare i
manifest firmati con la procedura proprietaria, poi ritirare `test_runner.py`.
Una ricerca finale deve provare zero chiamanti produttivi. Non si modificano
firme o identità direttamente per adattarle alla nuova grammatica.

**Prove negative.** Shell nei tre campi legacy; accesso rete; lettura di
config/vault/admin/Birth; fork che sopravvive al timeout; symlink; pytest
esterno; bwrap/cgroup assente; output eccessivo. La prova controlla anche che
nessun processo discendente resti vivo.

**Accettazione FS-A.** Tutte le superfici candidate attraversano lo stesso
runner certificato, il percorso legacy è irraggiungibile e una nascita reale
autorizzata produce la ricevuta di isolamento. Prima di allora nessuna nascita
automatica può essere usata per chiudere RM-0009.

### 12.12 FS-B — radici protette, apertura sicura e broker dei segreti

#### FS-B.1 — registro delle radici protette

**File principali.** `runtime/sandbox.py` (`resolve_filesystem_read_args` e
costruzione dei bind), `runtime/vaglio.py`, `runtime/config.py` o il modulo che
possiede le radici effettive, e test sotto `tests/runtime/safety/`.

Aggiungere un unico provider core-owned di radici risolte: configurazione
Metnos, vault credenziali, `admin.key`, chiavi/autorità Birth e ogni alias/XDG
equivalente. Il Vaglio riceve una proiezione testuale per difesa in profondità;
la decisione autorevole usa percorsi canonici e tipo di radice. Path relativo,
assoluto, `~`, `XDG_CONFIG_HOME`, alias e symlink verso la stessa radice devono
avere lo stesso esito.

#### FS-B.2 — eliminare i mount di chiavi grezze

Oggi `runtime/sandbox.py`, nel ramo
`metnos:credentials_metadata_only`, monta vault e `admin.key` nell'executor.
Rimuovere entrambi come canale di accesso ai byte grezzi. La capability deve
chiamare l'API tipizzata approvata in §12.2: il core esegue l'operazione o
restituisce una proiezione metadata allowlist; l'executor riceve soltanto dati
minimi, mai chiave, directory del vault o percorso utile a leggerli.

Migrare insieme `find_credentials`, `set_credentials` e `delete_credentials` e
le funzioni condivise di `runtime/credentials.py`. Provare che metadata-only non
contiene password, ciphertext, salt, key material o path della chiave, anche in
errore e nei log. Un broker non è un subprocess con la stessa directory
montata: il confine resta nel core.

#### FS-B.3 — chiudere TOCTOU al confine filesystem

Il controllo deve legare validazione e apertura allo stesso oggetto mediante
descriptor stabile, `openat`/no-follow o primitiva equivalente. Dopo la
validazione, sostituire un componente con symlink non deve cambiare l'oggetto
aperto o montato. Le eccezioni sono mandati tipizzati, firmati e limitati
all'operazione; nessun mandato consente la lettura dei byte di una chiave radice.

**Prove negative.** Tutte le forme di alias; symlink creato prima e durante
l'apertura; hard link quando applicabile; path inesistente poi creato; bind
read-only; payload metadata contenente un campo extra; errore che stampa path o
segreto. Eseguire anche i test di sandbox esistenti e i tre executor credenziali.

**Accettazione FS-B.** Nessuna chiave radice appare nella command line, nei
mount, nell'ambiente, negli argomenti o nei log di un executor; tutti gli accessi
alle radici protette passano dal confine tipizzato e resistono alla sostituzione
del path.

### 12.13 F6 — autorità d'invocazione graduata e attestata

#### F6.1 — contratto puro, senza enforcement

**File raccomandato.** Aggiungere `runtime/invocation_authority.py` e
`tests/runtime/safety/test_invocation_authority.py`. Non riusare
`autonomy_level` come nome e non modificare `AdmissionContextV1`.

Definire input e decisione immutabili, schema-versioned. L'input lega almeno:
proprietario operativo e actor autenticati, channel, ContractId e generation,
lifecycle, `admission_context_id`, versione del sandbox registry, argomenti
**finali** e
loro provenienza, capability/ambiti effettivi, destinazione, limiti workload,
policy utente/canale e mandati/approvazioni consumabili. La decisione contiene
`allowed_capabilities`, ambiti ristretti, veto, policy version, digest di tutti
gli input, scadenza, nonce e motivo chiuso.

Se l'invocazione deriva da un change intent, la ricevuta registra anche
`proposer_component_id` e `owner_id` di provenienza. Questi due campi non
partecipano al calcolo dell'autorità e una loro differenza non è un veto di
RM-0009. Il proprietario operativo della richiesta resta invece soggetto ai
controlli di sicurezza e di accesso ai dati già esistenti, che questa roadmap
non modifica.

L'algoritmo è monotono: interseca insiemi già concessi e può soltanto
restringere. Lifecycle non `active`, attestazione/ricevuta assente, identità o
generation discordante, legacy ignoto e approval richiesto ma assente sono
veto. Reversibilità, execution class o livello della proposta non aggiungono
capability.

La matrice di test deve coprire ogni veto e almeno due policy con differenza
sostanziale, non cosmetica: per gli stessi argomenti firmati, una consente una
specifica capability/ambito e l'altra la nega. Nessun test chiama subprocess,
coda o rete in F6.1.

#### F6.2 — osservazione in ombra in tutti i colli di bottiglia

Integrare il valutatore, senza cambiare l'esito, immediatamente prima di ogni
effetto e **dopo** le trasformazioni specifiche della destinazione:

- percorso locale in `runtime/agent_runtime.py`, dentro
  `_invoke_executor_impl`, dopo iniezione e
  `sandbox.resolve_filesystem_read_args`, prima di undo o subprocess;
- percorso remoto nello stesso simbolo e in `runtime/remote_exec.py`, dopo la
  serializzazione finale e prima dell'enqueue/invio;
- bridge durevole in `runtime/durable_workloads/execution.py` e binding in
  `runtime/durable_workloads/runtime_bindings.py`, prima dell'esecuzione;
- resume, retry e cache hit che possono riutilizzare una decisione precedente.

Registrare solo decisione redatta, digest, versione e motivo; mai argomenti o
segreti. Confrontare shadow con comportamento attuale e classificare divergenze.
Non esiste un singolo punto temporale comune prima del bivio locale/remoto:
riusare la stessa funzione non significa saltare i controlli per destinazione.

**Accettazione F6.2.** Ogni percorso emette una e una sola decisione shadow per
invocazione esatta; nessun effetto cambia; retry/cache/queue sono distinguibili;
divergenze e `unknown` sono sotto le soglie normative.

#### F6.3 — attivare i veto prima di qualunque effetto

**Precondizioni dure.** F5, FS-A e FS-B `ACCEPTED`; ricevuta di certificazione
RM-0008/F5 presente; nessuna divergenza shadow non spiegata.

Attivare prima i soli veto fail-closed, dietro una versione di policy esplicita.
Il diniego deve avvenire prima di undo, enqueue, subprocess, bind o apertura.
Il ramo durevole già condizionato da `require_generation_attestation` non può
restare opt-in nel binding produttivo: il proprietario RM-0008 deve attivarlo e
fornire l'attestatore. RM-0009 consuma quella ricevuta; non implementa un secondo
database di lifecycle.

Un record legacy o una decisione scaduta non cade nel comportamento permissivo.
Rollback significa tornare a una policy precedente **più restrittiva** o
disabilitare il percorso, non ignorare i veto.

#### F6.4 — remoto, durevole, revoca e monouso

La ricevuta accodata lega proprietario operativo, actor, ContractId, generation,
destination, args digest, capability, admission context, policy, approval,
nonce e TTL; registra separatamente `proposer_component_id` e `owner_id` di provenienza
senza usarli come gate. Il consumer verifica firma, monouso, scadenza e stato
corrente subito prima
dell'effetto. Cambio lifecycle, quarantena, ritiro, nuova generation, cambio
policy o revoca approval invalida coda, retry e cache. L'idempotenza del
workload non rende riusabile l'autorizzazione.

**Prove obbligatorie.** Locale, remoto, durevole, resume, retry, cache hit;
ricevuta riusata; args/proprietario operativo/destination/generation alterati;
componente o `owner_id` di provenienza diversi a parità degli altri fatti, senza
variazione dell'autorità; scadenza; quarantena fra enqueue e consume; policy
cambiata; attestatore assente. Ogni caso negato prova zero undo, zero enqueue
ulteriore, zero subprocess e zero
effetto esterno.

**Accettazione F6.** Due executor con fatti firmati diversi ricevono autorità
effettive materialmente diverse; restringere la policy impedisce davvero
l'azione; nessun percorso aggira veto o ricontrollo; tutte le decisioni sono
ricostruibili da ricevute redatte.

### 12.14 Chiusura integrata e consegna

Il test finale non è un'unica prova monolitica. Il coordinatore deve raccogliere
i manifest `ACCEPTED` delle unità e poi eseguire, nell'ordine:

1. riconciliazione completa F0/F3 sulla baseline congelata;
2. un bisogno umano reale che produce un `capability_absent` provato, una sola
   proposta e una ricevuta TELOS;
3. consegna, decisione umana e ricevuta dell'effetto, compreso un retry
   idempotente;
4. disponibilità della modifica attivata nel catalogo e nel routing di almeno
   due utenti diversi dall'owner di provenienza, con isolamento dei dati ancora
   efficace;
5. nascita soltanto tramite runner FS-A e soltanto se RM-0008/F5 la autorizza;
6. promozione, rifiuto, rollback o altro stato terminale chiuso approvato;
7. restrizione F6 materialmente efficace e caso legacy fail-closed;
8. prova negativa di revoca fra enqueue ed esecuzione e verifica di zero effetti;
9. due esecuzioni consecutive del riepilogo senza duplicare eventi, proposte o
   notifiche.

Ogni passaggio deve portare gli id causali del precedente. Il coordinatore si
ferma al primo id mancante, record duplicato, soglia non soddisfatta o prova che
richieda dati inventati. Il completamento richiede un revisore indipendente per
sicurezza e uno per evidenze; un giro riuscito dimostra reachability, mentre le
prove negative, il retry e le revoche dimostrano gli invarianti minimi.

Soltanto dopo l'accettazione integrata si prepara la versione editoriale pulita:
ogni rilievo accolto del §11 e ogni decisione usata dal §12 entrano nei §§2-10;
ogni rilievo respinto riceve una motivazione nel verbale esterno; metadati,
soglie, dipendenze e baseline restano nella norma. Si congela e si revisiona
quella versione, quindi — non prima — si eliminano nota temporanea, cronologia e
interi §§11-12.
