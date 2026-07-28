# RM-0001 — Conoscenza utente locale: memoria forte, semplice e automatica

**Stato:** `ready`
**Creazione:** 2026-07-23
**Ultima revisione:** 2026-07-28
**Implementazione reale:** non iniziata. Esistono soltanto le preferenze W2,
alcuni registri riutilizzabili, il Tutor, le cache di piano e il ciclo W1; non
esistono ancora il principale canonico per la memoria, lo store, il compilatore,
la superficie chat o gli applicatori descritti qui
**Conservazione:** roadmap persistente fino a implementazione dimostrata o
cancellazione esplicita di Roberto
**Decisione:** ADR 0200 **ratificata il 28 luglio 2026**; il calendario di F0 è
deciso separatamente. Questa roadmap è la specifica normativa della direzione
futura, non prova comportamento corrente
**Origini:** review del 23 luglio assorbita selettivamente; review multidominio
del 26 luglio; sette lenti Fable in
`internal/reports/rm0001-review-fable-20260726/`, verificate contro il codice;
audit primario Swafra in
`internal/reports/rm0001-swafra-primary-audit-20260726.md`; review adversariale
conclusiva del 28 luglio (`REVIEW.md` e `refutazione.md` nella stessa cartella);
ricognizione del codice, redazione dello strato di attuazione e doppia verifica
avversariale del 28 luglio in `internal/reports/rm0001-attuazione-20260728/`

Questa revisione sostituisce integralmente le versioni precedenti di RM-0001.
Elimina dal suo perimetro la memoria dell'esperienza degli executor, Leiden,
MCP e la produzione di nuove capacità. Conserva invece ciò che serve perché
Metnos conosca il proprio utente in modo locale, controllabile e realmente
utile, senza contaminare planner e cache condivise.

Rispetto alla revisione del 26 luglio, questa aggiunge lo **strato di
attuazione** (§13): contratti, schemi, punti d'innesto verificati riga per riga,
ordine di costruzione, interruttori e prove, fase per fase. Recepisce inoltre le
tre decisioni ratificate il 28 luglio — ampiezza dell'oblio per chiave,
compositore delle risposte aggregate fuori dal nucleo, ratifica di ADR 0200 — e
chiude i cinque difetti trovati dalla review conclusiva.

## 0. Esito progettuale

RM-0001 è pronta per iniziare F0, non per essere implementata in blocco.
L'impianto finale è composto da sei capacità progressive:

1. identità verificata e contesto utente immutabile per turno;
2. preferenze e default operativi tipizzati;
3. riferimenti personali verso identificatori prodotti da registri reali;
4. memoria libera ed episodi attestati, sempre riconducibili alla fonte;
5. apprendimento implicito non sensibile, automatico per il proprietario;
6. canonicalizzazione di routine personali prima delle cache, senza inserirvi
   dati personali.

Il nucleo non usa una scheda profilo libera nel planner. Le cache L0/L1 restano
condivise e indipendenti dall'utente. I valori personali entrano dopo il piano,
in argomenti dichiarati e posseduti dal runtime. L'unica eccezione è la
canonicalizzazione di una routine: prima di L0 una forma ellittica viene
riscritta in una sequenza canonica di verbi e oggetti chiusi, priva di valori
personali; cartelle, calendari, account e destinatari vengono risolti dopo.

La conoscenza non concede mai autorità, non riduce consensi e non rende
eseguibile la prosa recuperata. Per operazioni mutanti o outbound, ogni sorgente
e destinazione risolta dalla memoria compare nel normale controllo del piano o
nel mandato già esistente.

Questo non è un vicolo cieco: il consolidamento della fiducia sugli effetti ha
già un percorso proprio in Metnos — l'autopath confermato dal segno umano
(ADR 0185) e i mandati credenziale (ADR 0190) — e resta lì. La memoria conosce
l'utente; non decide che cosa gli è permesso fare.

## 1. Risultati attesi per l'utente

### UC-01 — Preferenza esplicita di risposta

**Richiesta:** «Da ora rispondimi in modo sintetico e usa il sistema metrico».

**Risultato:** Metnos aggiorna chiavi W2 chiuse e le applica dalla risposta
successiva. Un'istruzione esplicita nel turno corrente prevale sempre.

**Accettazione:** nessun planner o cache cambia; la chat permette di vedere,
correggere e cancellare la preferenza; il messaggio è localizzato.

### UC-02 — Preferenza appresa senza approvazioni ripetute

**Situazione:** il proprietario corregge più volte risposte troppo lunghe.

**Risultato:** dopo evidenze indipendenti sufficienti, Metnos aggiorna in
silenzio una preferenza di presentazione ammessa. L'inventario mostra valore,
origine e data; «non farlo più» corregge immediatamente.

**Accettazione:** solo vocabolario chiuso, nessun dato sensibile, nessuna
domanda per elemento e nessun effetto su executor o autorità.

### UC-03 — Riferimento personale verificato

**Richiesta:** dopo aver confermato che Atlas è il progetto registrato con un
determinato identificatore, l'utente chiede «controlla i test di Atlas».

**Risultato:** il runtime sceglie soltanto fra gli identificatori restituiti dal
provider di progetti. Se il riferimento è ambiguo, sparito o ha cambiato
di identità, chiede una volta.

**Prima istanza obbligatoria:** `runtime/project_paths.json`, già esistente,
diventa il provider del ReferenceSlot `project`. Non si inventa un secondo
registro di progetti.

### UC-04 — Default operativo personale

**Richiesta:** «Per il lavoro usa il calendario Lavoro»; in seguito «mostrami
gli impegni di domani per il lavoro».

**Risultato:** un default tipizzato riempie l'argomento mancante dopo il piano.
Un valore nominato nella richiesta prevale. Su creazioni, modifiche o invii il
valore risolto è mostrato dal normale controllo dell'azione.

**Accettazione:** il default esiste perché un manifest firmato dichiara
l'argomento personalizzabile e il provider dei valori validi; nessun ramo per
calendario, frase o utente nel runtime.

### UC-05 — Decisione tra sessioni

**Situazione:** l'utente decide che un progetto userà SQLite e giorni dopo
chiede di continuare «secondo la decisione presa».

**Risultato:** Metnos recupera la decisione corrente con data, ambito e turno
d'origine. Una decisione successiva crea `supersedes`; un conflitto irrisolto
produce astensione o domanda, mai fusione silenziosa.

### UC-06 — Episodio attestato dal runtime

**Richiesta:** «Che cosa abbiamo fatto ieri sui rapporti?».

**Risultato:** Metnos consulta riferimenti strutturati a turni ed esiti reali:
azioni, orario, conteggi e risorse ammesse. Non trasforma la propria risposta in
prova e non conserva una seconda narrazione LLM come verità.

**Accettazione:** se il turno sorgente è stato eliminato o non appartiene al
principale corrente, l'episodio non è consultabile.

### UC-07 — Richiesta ellittica di una routine

**Richiesta:** «Prepara le solite cose per il rapporto».

**Risultato:** se esiste una sola routine personale compatibile, il runtime la
trasforma in una sequenza canonica di azioni prima di L0; i valori personali
restano slot da riempire dopo il piano. Con zero o più routine compatibili,
Metnos si astiene o chiede.

**Accettazione:** la riscrittura contiene soltanto verbi, oggetti e nomi di slot
canonici; mai path, indirizzi, account, credenziali o prosa del profilo.

### UC-08 — Domanda aggregata

**Richiesta:** «Quali progetti ho attivi?» oppure «Quali decisioni abbiamo preso
questa settimana?».

**Risultato:** la risposta è un **elenco deterministico citato**: più record entro
un budget esplicito, ciascuno con il turno o l'evento reale da cui viene. Non
presenta un elenco parziale come completo e non passa da un compositore locale.

**Nota di perimetro (decisione ratificata del 28 luglio):** la composizione LLM
delle risposte aggregate resta **fuori dal nucleo**. Rientra soltanto dopo casi
reali documentati in cui l'elenco citato non basta, con ADR separata; il criterio
esatto di riapertura è in §5.8.

### UC-09 — «Che cosa sai di me?»

**Risultato:** dalla chat HTTP o Telegram, senza visitare obbligatoriamente una
pagina amministrativa, Metnos mostra preferenze, default, riferimenti, fatti,
decisioni, routine ed episodi in sezioni distinte. Per ogni voce espone origine,
stato, ambito, data e comando naturale di correzione o oblio.

### UC-10 — Oblio verificato

**Richiesta:** «Dimentica che Atlas era quel progetto».

**Risultato:** spariscono record, indici e collegamenti; gli eventi precedenti
ancora in coda non possono ricreare il dato. Dopo riavvio e restore supportato
la memoria non ricompare.

**Ampiezza (decisione ratificata del 28 luglio).** Per un valore **tipizzato** la
cancellazione risolve il bersaglio **per chiave**: sparisce ogni riga con quella
`(chiave, ambito)` in qualunque contenitore dichiarato — preferenza, default
operativo, riferimento, routine — e in qualunque stato, quindi anche un gemello
già compilato prima della richiesta. Per un **claim libero** si cancella
l'identificativo indicato e ogni claim dello stesso principale con digest
identico; ciò che resta **non sopravvive in silenzio**, perché l'inventario
consegnato subito dopo l'oblio lo elenca esplicitamente. La promessa è quindi
verificabile in entrambi i casi: o il dato muore, o l'utente lo vede ancora
elencato.

### UC-11 — Correzione temporale

**Richiesta:** «Non uso più quel calendario per il lavoro; ora uso Team».

**Risultato:** la correzione prevale subito, conserva la relazione temporale e
invalida snapshot e riferimenti incompatibili. Non aumenta fiducia per numero
di recuperi.

### UC-12 — Continuità tra canali e isolamento

**Situazione:** lo stesso proprietario imposta una preferenza via HTTP e la usa
da Telegram; un ospite chiede che cosa Metnos sa di lui.

**Risultato:** il binding autenticato, non la stringa `actor`, determina il
profilo. Il proprietario non legge automaticamente i claim dell'ospite; la UI
amministrativa può mostrare stato, quota e cancellazione senza contenuto.

## 2. Stato corrente verificato al 26 luglio 2026

| Area | Stato reale | Conseguenza per RM-0001 |
|---|---|---|
| W2 | `runtime/users.py` possiede `user_prefs`, vocabolario chiuso e UI admin; `list_prefs` perde origine e data | riusare e completare, non duplicare |
| Applicazione preferenze | il percorso risposta non consuma normalmente `reply_length`, `tone` e `units`; sono usate soprattutto preferenze sites e lingua | il beneficio UC-01 parte da cablaggio nuovo |
| Identità | `actor_resolver`, HTTP e dispatch builtin hanno ripieghi verso `host` | nessuna API memoria può usare direttamente questi ripieghi |
| Tutor | `TutorPrincipal` nasce ai confini HTTP/Telegram | deve diventare una vista del principale canonico, non un tipo concorrente |
| Dispatch runtime | `_RUNTIME_ARG_SOURCES` inietta valori server-owned ma i provider non ricevono contesto di turno | estenderlo a provider `source(context)`, senza un secondo canale |
| Default provider | `backend_resolver` riempie argomenti dopo il piano | precedente da generalizzare con dichiarazioni firmate per-argomento |
| Riferimenti | `ArgTransform` esiste; `project_paths.json` è già un registro di progetti | primo ReferenceSlot già scelto |
| Clausole | `split_query_chunks` esiste ma non restituisce intervalli; `Intent.actions` porta solo verbo/oggetto | F0 aggiunge intervalli generici, non un segmentatore memoria separato |
| TurnLog | conserva `actor` e canale, non un principale canonico completo | niente importazione storica automatica |
| Esiti | `CallbackOutcome` e gli esiti runtime distinguono successo, parziale ed errore | fonte degli episodi attestati |
| Cache | L0 usa hash della query; L1 firma verbo, oggetto e azioni; piani con letterali sono query-specific | nessun dato personale nelle strutture condivise |
| Ciclo W1 | osserva turni riusciti e lacune, crea autopath shadow e ChangeIntent | le routine personali riusano l'osservazione, non duplicano il learning-loop globale |
| Memoria utente | `runtime/memory/` e database dedicati non esistono | nessuna migrazione o compatibilità da preservare |

## 3. Perimetro

### 3.1 Dentro RM-0001

- conoscenza dichiarata o dedotta da parti attribuite all'utente;
- preferenze di risposta W2;
- default operativi tipizzati e dichiarati dai domini;
- riferimenti confermati a identificatori reali;
- fatti, decisioni e loro temporalità;
- episodi strutturati attestati dal runtime;
- routine personali come forme canoniche di piani già riusciti;
- esplorazione, correzione, oblio, quota, backup e ripristino;
- retrieval esatto e FTS5; composizione locale soltanto per risposte finali.

### 3.2 Fuori RM-0001

- memoria dell'esperienza interna degli executor e suggerimenti di strategia;
- Leiden, comunità, espansione a grafo e scoperta di workflow globali;
- produzione o modifica automatica di executor, Tutor, autopath o mandati;
- accesso MCP esterno;
- categorie sensibili, segreti, credenziali e contenuto integrale di messaggi;
- cambiamento di tool, autorità o consenso in base a prosa recuperata;
- scansione automatica dei TurnLog storici;
- un vector database o un daemon dedicato nella prima implementazione.

Questi temi richiedono una roadmap o ADR propria soltanto dopo un caso reale.
Le vecchie fasi F7-F11 non fanno più parte di RM-0001. In particolare Leiden
non resta come attività di laboratorio obbligatoria: potrà essere riesaminato
solo se un raggruppamento deterministico fallisce su casi documentati e la
scala reale lo giustifica.

## 4. Invarianti

1. **Principale verificato:** ogni lettura e scrittura deriva da un
   `PrincipalContext` creato da dati autenticati del canale.
2. **Fallimento chiuso:** principale assente, LAN non associata, binding
   revocato o revisione autorizzativa stantia non diventano `host`.
3. **Isolamento:** ogni query vincola il proprietario del record; nessun metodo
   ordinario accetta `user_id` libero.
4. **Origine:** testo esterno, citato, inoltrato, allegato, prodotto da tool o
   dall'assistente non scrive conoscenza utente.
5. **Autorità:** una memoria non concede capacità, credenziali, mandato o
   consenso e non ne riduce uno esistente.
6. **Precedenza:** il valore esplicito nel turno corrente prevale su default,
   riferimento, routine e inferenza.
7. **Planner condiviso:** il profilo libero non entra nel planner, nel prompt di
   sistema o nel vaglio.
8. **Cache condivise:** `user_id`, revisioni e valori personali non entrano
   nelle chiavi L0/L1 né nei framework condivisi.
9. **Canonicalizzazione povera:** una routine pre-cache contiene soltanto azioni
   canoniche e slot; se servono valori personali prima del piano, si astiene.
10. **Applicazione dichiarata:** un argomento personale viene riempito soltanto
    se il contratto firmato del dominio lo dichiara personalizzabile.
11. **Valore finale:** capability e consenso sono calcolati sul valore finale
    dopo l'iniezione runtime.
12. **Riferimenti chiusi:** una memoria sceglie soltanto fra ID candidati
    prodotti in quel turno da un provider autorizzato.
13. **Continuità d'identità:** ID riutilizzabili richiedono un'ancora stabile;
    mismatch, scomparsa o ambiguità causano astensione.
14. **Piano intero:** se un piano contiene un effetto mutante o outbound, il
    controllo mostra anche sorgenti e destinazioni risolte dalla memoria.
15. **Nessuna preselezione:** una scelta richiesta all'utente evidenzia il
    suggerimento ma non lo seleziona automaticamente.
16. **Evidenza reale:** un claim cita una clausola dell'utente o un evento
    runtime attestato; un'altra riga LLM non è evidenza.
17. **Conflitto:** claim incompatibili impediscono applicazione automatica.
18. **Deterministico prima:** slot, enum, correzioni, stesso valore e
    `supersedes` su chiavi tipizzate non usano LLM.
19. **LLM limitato:** il modello può estrarre o proporre relazioni per testo
    libero; non decide stato, autorità, cancellazione o identità.
20. **Automatico ma reversibile:** per il proprietario verificato,
    l'acquisizione implicita non sensibile è attiva dopo la promozione della
    fase; nessuna approvazione per elemento.
21. **Ospiti:** apprendimento implicito spento; preferenze esplicite soltanto
    dopo abilitazione individuale.
22. **Minori:** la protezione esiste solo se il registro utenti porta un
    marcatore verificabile e chi può impostarlo; in sua assenza non si dichiara
    di riconoscere un minore.
23. **Sensibili:** salute, religione, politica, biometria, sessualità, finanze,
    segreti e informazioni intime su terzi sono rifiutati e non persistiti.
24. **Limiti:** righe, byte, elementi restituiti, tempo, coda e uso LLM hanno
    tetti espliciti; un troncamento è visibile.
25. **Oblio:** una cancellazione invalida anche eventi preesistenti accodati o
    in compilazione; una nuova dichiarazione successiva può reintrodurre il
    dato.
26. **Ripristino onesto:** un backup vecchio senza journal di cancellazione
    sufficientemente recente resta in quarantena; non dichiara oblio verificato.
27. **Nessun auto-rinforzo:** recupero, uso e prosa dell'assistente non aumentano
    forza o durata del claim.
28. **Episodi strutturati:** il runtime registra esito e riferimenti, non una
    narrazione generata dall'assistente.
29. **Controllo in chat:** inventario, correzione e oblio funzionano sia da HTTP
    sia da Telegram attraverso lo stesso confine.
30. **Beneficio causale:** nessuna fase che influenza risposte o argomenti viene
    promossa senza battere una baseline lineare sul medesimo corpus.

## 5. Architettura

```text
HTTP / Telegram / task owner-bound
              |
              v
      PrincipalContextFactory
              |
              +--> UserContextBoundary -- comandi e domande esplicite
              |
              v
       UserContextSnapshot
        |               |
        v               v
  planner/cache      turno riuscito
  user-agnostici          |
        |                 v
        v          eventi autenticati
  argomenti runtime        |
  personalizzabili         v
        |           compilatore locale
        v                 |
  capability/consenso <---+
        |
        v
     executor
        |
        v
  risposta finale con citazioni
```

### 5.1 Principale canonico

`PrincipalContext` è immutabile e contiene almeno:

- `principal_id` e `owner_user_id` canonici;
- ruolo e tipo soggetto;
- canale, binding e dispositivo autenticati;
- `auth_source` e `auth_strength` chiusi;
- `authz_revision` monotona;
- `conversation_id` e `turn_id` runtime-owned;
- indicatore verificato adulto/minore quando disponibile.

La factory è l'unico costruttore autorevole. HTTP usa soltanto campi prodotti
dal middleware; Telegram usa il binding utenti/canali verificato; task,
dialoghi e riprese conservano un riferimento al principale e lo ricontrollano
al consumo. `TutorPrincipal` diventa una proiezione ristretta di questo valore.

`authz_revision` vive nello store utenti e incrementa per associazione,
revoca, cambio ruolo o modifica dell'owner. Una mutazione ricontrolla la
revisione dentro la stessa unità logica dell'accesso.

### 5.2 Istantanea per turno

`UserContextSnapshot` contiene:

- `principal_id`;
- `prefs_revision`;
- `memory_revision`;
- `deletion_epoch`;
- `policy_version`;
- preferenze e default tipizzati già validati;
- soli identificatori delle memorie applicabili, non un dossier testuale.

Viene costruita una volta all'inizio del turno e non è condivisa tra utenti.
Non viene inserita nelle cache di piano. Se una ripresa avviene dopo revoca o
cambio di revisione autorizzativa, si ricostruisce o fallisce chiusa.
Il `deletion_epoch` autorevole deriva dal journal separato ed è rispecchiato
negli store: uno snapshot non è valido se uno dei due valori è arretrato.

### 5.3 Tre classi di conoscenza

| Classe | Store autorevole | Forma | Effetto ammesso |
|---|---|---|---|
| preferenze e default | `users.db` | chiavi e valori tipizzati | presentazione o riempimento argomento dichiarato |
| memorie e riferimenti | `user_memory.sqlite` | claim, evidenze, relazioni, ID | profilo, retrieval, disambiguazione |
| episodi e routine | `user_memory.sqlite` | riferimenti a esiti e forme canoniche | continuità e riscrittura povera |

Non esiste un quarto profilo riassuntivo da iniettare. Una risposta aggregata è
calcolata su richiesta dal record canonico e dalle sue evidenze.

### 5.4 Dichiarazioni di personalizzazione nei domini

Un dominio dichiara nel proprio manifest firmato una sezione opzionale
`[personalization]`. Ogni voce contiene:

- `preference_key`: chiave semantica stabile;
- `argument`: argomento del manifest che può essere riempito;
- `scope_fields`: campi tipizzati che delimitano l'ambito;
- `value_provider`: sorgente runtime degli ID o enum validi;
- `operations`: tool del dominio cui la voce si applica;
- `effect_class`: `presentation`, `read`, `mutating` o `outbound`;
- `sensitivity`: classe chiusa;
- `identity_anchor`: obbligatoria se l'ID può essere riutilizzato.

Il loader rifiuta argomenti inesistenti, provider ignoti, chiavi duplicate o
classi non ammesse. Il digest del manifest copre la dichiarazione: cambiare la
personalizzazione invalida i piani che usano quel tool tramite `tools_sig`, ma
un cambio di valore utente non cambia il piano.

Il compilatore server-side delle dichiarazioni segue il precedente delle forme
credenziali: un dominio porta la propria semantica; il runtime offre un
meccanismo comune. Nessuna lista centrale di calendari, cartelle o provider.

### 5.5 Iniezione dopo il piano

`_RUNTIME_ARG_SOURCES` evolve da provider senza parametri a provider che
ricevono un `TurnInvocationContext` ristretto. L'ordine è:

1. piano dalla cache o dal proposer;
2. argomenti espliciti e riferimenti di clausola;
3. ArgTransform e ReferenceSlot;
4. default personale soltanto per argomento ancora assente;
5. coercizione allo schema;
6. calcolo capability sul valore finale;
7. controllo, mandato e invocazione.

Il modello non può produrre `principal_id`, revisioni o valori runtime-owned.
Una ripresa di dialogo riesegue i punti 3-7 e sovrascrive eventuali valori
serializzati per gli argomenti posseduti dal runtime.

### 5.6 ReferenceSlot

Ogni slot dichiara tipo, provider, criterio di identità e argomento di uscita.
Il provider restituisce `candidate_id`, etichetta, ambito e `identity_anchor`.
Il resolver riceve la lista del turno e può soltanto:

- restituire uno degli ID presenti;
- chiedere scegliendo dalla stessa lista;
- astenersi con reason code.

Il primo slot è `project`, alimentato da `project_paths.json`. Un secondo slot
si aggiunge solo dopo un caso reale e riusa la stessa interfaccia. Contatti,
calendari e account esistenti vengono prima censiti come provider candidati;
non si creano registri paralleli.

### 5.7 Canonicalizzatore delle routine

Una `RoutineBinding` contiene:

- `routine_id` opaco e principale;
- sequenza ordinata `{verb, object}` dal vocabolario chiuso;
- nomi degli slot richiesti, mai i loro valori;
- firma del contesto tipizzato;
- riferimenti agli esiti riusciti che la sostengono;
- stato `candidate|active|conflict|revoked`;
- revisioni e scadenza.

La firma del contesto contiene soltanto enum e classi di oggetto dichiarate;
non contiene principale, path, account, destinatari o altri valori personali.

Il riconoscimento di una richiesta di routine usa concetti IT/EN nel
`detection_lexicon`, non frasi nel codice. La scelta è deterministica: match di
azioni/oggetti espliciti, contesto compatibile, recenza e margine fissati in
F0. Se non emerge un unico candidato, nessuna riscrittura.

La forma canonica generata prima di L0 contiene soltanto la sequenza di azioni
e segnaposto semantici. Esempio concettuale:

```text
originale: prepara le solite cose per il rapporto
canonico: find files -> compress files -> create messages draft
runtime: project_folder, archive_destination e recipient restano assenti
```

Originale, `routine_id`, forma canonica e snapshot vengono auditati. La cache
vede il canonico generico: utenti che producono la stessa forma possono
condividere il piano; nessun valore personale viene cross-servito.

### 5.8 Consultazione e risposte aggregate

La consultazione porta sempre un `purpose_code`:

- `profile_inventory`;
- `profile_answer`;
- `reference_resolution`;
- `argument_default`;
- `episode_recall`;
- `routine_resolution`.

Ogni purpose ha tipi, limiti e campi restituiti propri. L'inventario è
deterministico.

**Anche la domanda aggregata è deterministica** (decisione ratificata del 28
luglio): risponde con un elenco citato dei record selezionati, non con prosa
generata. È l'applicazione diretta di §7.9 — l'inventario deterministico di UC-09
copre già quasi tutto UC-08 — e toglie dal cammino critico l'unico modello non
necessario del nucleo.

Il compositore locale rientra soltanto con questo criterio, che va soddisfatto
prima e non durante l'implementazione: almeno **tre** casi documentati del corpus
congelato — la banda di rumore misurata sul certificatore Tutor è di un caso — in
cui le fonti recuperate contengono la risposta ma l'elenco entro budget produce
astensione o è giudicato incompleto da due annotatori indipendenti secondo §11.4;
cioè il difetto è nella presentazione e non nel recupero. Servono allora una ADR
separata e una misura di danno preregistrata. Quando entrerà, varranno comunque i
vincoli già scritti: solo dopo selezione, entro un canale dati delimitato, con
obbligo di citare ogni fonte e segnalare i troncamenti; il modello non vede record
di altri principali e non riceve segreti o categorie rifiutate.

### 5.9 Confine chat

`UserContextBoundary` precede planner e cache su HTTP e Telegram. Gestisce:

- preferenze persistenti ad alta precisione;
- «ricorda», «dimentica», «correggi»;
- «che cosa sai di me?» e ricerche esplicite nel profilo;
- risposte a una disambiguazione aperta dal runtime.

Non è un executor e non richiede l'oggetto `memories` nel vocabolario. Una tool
call del planner non prova mai un comando di memoria. Il riconoscitore usa
`detection_lexicon` e intervalli di clausola prodotti dal helper compound
comune. Citazioni, codice, allegati e output tool sono esclusi prima.

Nella prima versione una mutazione memoria deve occupare un'intera clausola
attribuita all'utente. Se il confine di una richiesta composta non è certo, il
sistema chiarisce invece di eseguire una mutazione parziale.

### 5.10 Confine cache

- L0 usa la query originale salvo una routine validata; in quel caso usa la
  forma canonica generica.
- L1 continua a firmare verbo, oggetto e azioni; non contiene valori personali.
- framework con letterali personali non vengono creati dal sottosistema.
- revisioni utente invalidano snapshot e applicazioni, non cache di piano.
- un A/B deve dimostrare piano e tool identici con memoria accesa/spenta per
  richieste irrilevanti e per richieste che differiscono soltanto nei valori
  riempiti dopo il piano.

## 6. Modello dati e consistenza

### 6.1 Estensione dello store utenti

W2 resta l'unica fonte delle preferenze tipizzate. Le modifiche minime sono:

- `prefs_revision` monotona per utente;
- origine, evidenze e `updated_at` restituite dalle API;
- tabella `user_defaults` per chiave, scope canonico, valore, provider,
  ancora d'identità, stato e versione;
- tabella o campo `authz_revision` aggiornato con binding e ruolo;
- transazioni esplicite, WAL, `busy_timeout` e chiusura corretta delle
  connessioni.

`set_pref`, `delete_pref`, `set_default` e `delete_default` aggiornano la
revisione nella stessa transazione. Non esiste commit atomico con il database
delle memorie; lo snapshot rende visibile la coppia di revisioni.

### 6.2 `user_memory.sqlite`

Tabelle del nucleo, create soltanto nella fase che le usa:

| Tabella | Fase | Scopo |
|---|---:|---|
| `memory_state` | F1 | revisioni, epoch rispecchiato, policy, quota, salute |
| `principal_settings` | F1 | stato per principale, vocabolario chiuso |
| `source_events` | F1 | clausole utente ed eventi runtime autenticati |
| `memories` | F1 | fatto, decisione e stato temporale |
| `evidence` | F1 | legame molti-a-molti con eventi reali |
| `evidence_tombstones` | F1 | blocco del riuso della stessa fonte |
| `compile_queue` | F1 | protocollo di lease/epoch; il consumatore reale arriva in F5 |
| `user_defaults` | F3 | default operativi tipizzati (vive in `users.db`) |
| `references` | F3 | alias, provider, identificativo candidato e ancora |
| `applications` | F3 | ogni influenza esercitata sul turno |
| `memory_fts` | F4 | indice FTS5 del solo testo ammesso |
| `episode_refs` | F4 | puntatori strutturati a esiti runtime |
| `relations` | F4 | schema completo, ma in F4 si scrive solo `supersedes` deterministico; F5 ne estende l'uso senza toccare la tabella |
| `uc_filter_rejects` | F5 | codici di scarto, mai il testo scartato |
| `uc_compile_runs` | F5 | esecuzioni del compilatore a lotti |
| `routine_bindings` | F6 | forme canoniche personali |
| `routine_evidence` | F6 | legame routine-evento reale |

Ogni tabella si dichiara in due registri, definiti in F1: `CASCADE_TABLES` per la
cancellazione del principale e `KEY_TARGETS` per l'oblio per chiave. Una prova
d'invariante diventa rossa quando una fase aggiunge un contenitore senza
dichiararlo: è il modo in cui la decisione ratificata sull'ampiezza dell'oblio
resta vera anche per le tabelle che nasceranno dopo. Dettaglio in §13.0 T1 e T4.

Embedding e comunità non sono tabelle minime. Un'eventuale estensione densa
richiede una misura che mostri fallimenti FTS non risolvibili e una ADR nuova.

### 6.3 Eventi sorgente distinti

`source_events.kind` distingue almeno:

- `user_clause`: testo breve ripulito, intervallo, digest e ruolo;
- `interaction_choice`: ID scelto in un dialogo runtime;
- `runtime_outcome`: forma dell'azione, esito semantico, conteggi e riferimenti;
- `correction`: comando diretto con oggetto corretto.

Ogni evento porta principale, turno, `authz_revision`, `observed_at`,
`deletion_epoch_at_enqueue`, versione dello scrubber e correlation ID. Un
evento comportamentale o una routine richiedono un `runtime_outcome=success`;
assenza, parziale, errore o ripresa tecnica non contano come successo.

### 6.4 Relazioni e stato

Stati minimi delle memorie:

```text
observed -> candidate -> active_read
                       -> active_default   (solo valori tipizzati)
                       -> conflict
                       -> rejected
active_* -> superseded | expired | deleted
```

Le relazioni sono canoniche; lo stato è una materializzazione aggiornata nella
stessa transazione. Per stesso slot e scope:

- stesso valore: `supports`, deterministico;
- correzione esplicita con valore diverso: `supersedes`, deterministico;
- valori simultaneamente validi ma incompatibili: `contradicts`;
- chiave libera: il modello può proporre una relazione, ma la transizione resta
  chiusa e un dubbio produce `conflict`.

W2 e memoria libera non si ignorano. Il filtro intercetta claim che ricadono in
una `PreferenceSpec`: li instrada verso W2/default oppure li marca conflitto
cross-store visibile. W2 prevale nell'applicazione tipizzata.

**`PreferenceSpec`** non è un terzo registro e non ha una tabella propria. È una
vista **derivata a runtime**, ricalcolata a ogni invocazione, dall'unione di due
sorgenti che esistono già: le chiavi chiuse di W2 e le dichiarazioni
`[personalization]` firmate dei domini (§5.4). Descrive, per una chiave semantica,
quale store è autorevole, quali valori sono ammessi, quale ambito la delimita e
quale classe d'effetto le compete. La compila il filtro di F5 e la consuma il
riconciliatore; in F2, quando le dichiarazioni di dominio non esistono ancora, si
riduce alle sole chiavi W2. Il ricalcolo a ogni invocazione è deliberato:
congelarla all'importazione la renderebbe cieca a un manifest ricaricato.

### 6.5 Journal di cancellazione e coda

Il journal autorevole delle cancellazioni non vive nello stesso database delle
memorie. È un piccolo store append-only separato, con principale, operazione,
classe del target, ID opachi esatti, epoch, commit ID, stato e data; non conserva
claim o testo utente. Un lock locale per principale serializza allocazione
dell'epoch, cancellazione e compilazione concorrente.

Una cancellazione esegue, in ordine:

1. verifica principale e versione attesa, poi risolve il bersaglio in ID opachi
   con l'ampiezza fissata dalla decisione ratificata: **per chiave** sui valori
   tipizzati, attraversando tutti i contenitori dichiarati in `KEY_TARGETS` e
   qualunque stato; **per identificativo più digest identico** sui claim liberi.
   Ciò che resta viene elencato nell'inventario consegnato al passo 8, mai taciuto;
2. alloca il nuovo `deletion_epoch` e appende+sincronizza sul journal un record
   `PREPARED` prima di modificare gli store;
3. elimina idempotentemente da `users.db` e/o `user_memory.sqlite` preferenze,
   default, record, evidenze, FTS, riferimenti, episodi e relazioni interessati;
4. cancella **tutti** gli eventi non completati del principale osservati prima
   del nuovo epoch, non soltanto quelli già collegati al claim;
5. scrive tombstone per le evidenze note;
6. aggiorna l'epoch rispecchiato e completa i commit locali;
7. appende+sincronizza `COMMITTED` nel journal con lo stesso commit ID;
8. rilettura negativa della postcondizione.

Il compilatore ricontrolla l'epoch dentro la transazione finale. Un lease
acquisito prima del delete non può promuovere nulla dopo: abortisce e marca
l'evento cancellato. La scelta di invalidare l'intera coda pre-delete dello
stesso principale è deliberatamente semplice e conservativa; perde candidati
non ancora compilati, non memorie già attive.

Il blocco locale non copre l'**accodamento**, e la garanzia sta altrove: il
compilatore confronta `deletion_epoch_at_enqueue` di ogni evento con l'epoch
autorevole **dentro la transazione finale**, e un evento con epoch arretrato non
promuove. Un evento inserito fra i punti 4 e 6 con epoch stantio è quindi
invalidato al consumo, non allo sweep. La corsa è chiusa da un confronto
dichiarato, non da un dettaglio implementativo taciuto.

All'avvio e prima di un ripristino, ogni `PREPARED` senza `COMMITTED` viene
rieseguito come cancellazione idempotente e poi completato. Il sistema preferisce
portare a termine una richiesta di oblio già journalizzata anziché rischiare la
ricomparsa del dato. Questo protocollo sostituisce una transazione distribuita
fra i due database e chiude anche il crash tra commit locale e append finale.

Ripristino supportato:

- se il journal corrente sopravvive, viene riapplicato al backup prima di
  rendere leggibile lo store;
- se esiste soltanto un backup vecchio e non è dimostrabile la continuità del
  journal, tutte le memorie ripristinate restano in quarantena;
- l'utente può reimportare selettivamente, ma nessun record riappare in
  automatico;
- cancellazione forense di copie e supporti resta una garanzia separata con SLA
  dichiarato.

### 6.6 Quote e conservazione

Le quote iniziali sono per principale e limitano due grandezze autorevoli:
righe attive e byte totali. Ogni query applica inoltre limiti per tipo e
purpose. Le sottotabelle non hanno sei quote indipendenti finché una misura non
lo richiede.

Classi di conservazione:

- `durable`: dichiarazioni esplicite e decisioni confermate, senza decadimento
  per mancato uso;
- `contextual`: riferimenti e default, validati dal provider e con scadenza;
- `behavioral`: inferenze e routine, durata più breve e revisione periodica;
- `episodic`: durata allineata al TurnLog sorgente.

## 7. Acquisizione automatica

### 7.1 Politica del proprietario

Quando F5 viene promossa, il proprietario verificato ha per impostazione
predefinita l'apprendimento implicito non sensibile attivo. L'attivazione del
sottosistema mostra una sola informazione chiara e i comandi per spegnerlo,
ispezionarlo e cancellarlo; non chiede approvazione per ogni elemento.

Il controllo è conversazionale:

- «Che cosa sai di me?»;
- «Perché lo hai usato?»;
- «Non è corretto: ...»;
- «Dimenticalo»;
- «Non imparare più automaticamente».

### 7.2 Filtro prima del modello

Il filtro deterministico:

1. richiede un principale verificato e una clausola direttamente attribuita
   all'utente;
2. esclude quote, inoltri, allegati, codice, risultati tool e testo della pagina;
3. applica scrub di segreti e marker redatti;
4. classifica il purpose e limita lunghezza/numero;
5. rifiuta categorie sensibili o dubbie;
6. registra con reason code anche ogni scarto, senza conservare il testo
   scartato;
7. collega l'evento al turno sorgente;
8. congela il `deletion_epoch` corrente.

Il denominatore dei criteri a tolleranza zero è il numero di eventi realmente
esposti al confine pertinente, non il numero totale dei turni.

### 7.3 Preferenze chiuse

Una preferenza può nascere da:

- dichiarazione esplicita ad alta precisione: applicazione immediata;
- correzione: applicazione immediata e supersessione;
- scelte ripetute in episodi indipendenti riusciti: candidata;
- correzioni ripetute della presentazione: candidata.

La promozione silenziosa richiede valore nell'enum, scope valido, numero minimo
preregistrato di episodi indipendenti e assenza di conflitto. Un LLM può mappare
una formulazione alla chiave candidata soltanto quando il parser deterministico
non basta; enum e transizione restano verificati dal codice.

### 7.4 Memoria libera

Il compilatore locale lavora a lotti e inizia in ombra. Produce schema JSON
chiuso: claim breve, tipo, scope, intervallo temporale e riferimenti agli eventi.
Non produce autorizzazioni, preferenze fuori registro o testo operativo.

Promozione:

- dichiarazione esplicita non sensibile: `active_read` dopo policy e
  riconciliazione;
- singola osservazione implicita: `observed`, visibile ma non applicabile;
- evidenze indipendenti coerenti: `candidate`, poi `active_read` secondo soglia;
- conflitto o incertezza: resta inattiva;
- nessun claim libero raggiunge `active_default`.

### 7.5 Episodi

Gli episodi non vengono estratti dal testo dell'assistente. Il choke point
runtime emette un evento dopo l'esito semantico del turno. Vengono conservati
soltanto forma canonica delle azioni, conteggi, timestamp, riferimenti ammessi e
puntatore al TurnLog. Una risposta su «che cosa abbiamo fatto» legge il TurnLog
con lo stesso principale e cita il turno.

### 7.6 Isteresi dei riferimenti

Un riferimento confermato non viene ridomandato perché il provider ha aggiunto
candidati irrilevanti. Si chiede di nuovo soltanto se:

- il candidato confermato è scomparso;
- l'ancora d'identità non coincide;
- il provider lo dichiara non più valido;
- una correzione crea conflitto;
- l'insieme corrente non contiene più un unico match coerente.

### 7.7 Ospiti e minori

Per gli ospiti l'apprendimento implicito è spento. Possono attivare preferenze
esplicite dopo abilitazione individuale; il proprietario vede quota e stato,
non automaticamente il contenuto. La documentazione dichiara che
l'amministratore della macchina può accedere ai file locali: l'isolamento è
applicativo, non protezione crittografica dall'amministratore.

Il registro utenti aggiunge un marcatore minore soltanto con una ADR che
definisca chi può impostarlo e come viene verificato. Fino ad allora il sistema
non deduce l'età; per tutti gli ospiti valgono i limiti più conservativi.

## 8. Recupero e applicazione

### 8.1 Ordine iniziale

1. preferenza/default esatto;
2. ReferenceSlot esatto entro candidati;
3. lookup per chiave, scope e tempo;
4. FTS5 su testo ammesso;
5. relazioni tipizzate a un salto per conflitti e supersessioni — disponibili da
   **F4**, che crea `relations` con il solo `supersedes` deterministico; prima di
   F4 i punti 1-4 rispondono da soli e il passo 5 non esiste.

Dense retrieval, RRF e grafo di comunità non fanno parte del nucleo. Si
aggiungono soltanto se il corpus congelato contiene lacune che exact+FTS non
risolvono e la variante batte la baseline senza aumentare il danno.

### 8.2 Selettività

Il valore predefinito è nessuna memoria. Ogni richiesta dichiara `max_items`,
`text_budget` e tipi. Il risultato restituisce `matched|no_match|conflict|
truncated|unavailable`, revisioni e reason code.

Una memoria è applicabile solo se coincidono principale, purpose, scope,
tempo, provider, ancora d'identità e revisione. Similarità non aumenta
source-strength e non risolve un conflitto.

La diversità per sorgente è una variante di selezione, non una scusa per
allargare `max_items`. F4 confronta ranking puro e selezione source-balanced
sullo stesso `k`: prima un risultato per sorgente, poi eventuali secondi
risultati soltanto entro il budget. La variante entra nel default solo se
riduce ridondanza senza perdere tutte le evidenze necessarie. Non esiste una
percentuale fissa del corpus da restituire.

### 8.3 Punti d'effetto

| Punto | Conoscenza ammessa | Effetto |
|---|---|---|
| risposta finale | W2 e claim `active_read` pertinenti | tono, lunghezza, unità, sintesi citata |
| argomento runtime | `user_defaults` dichiarati | riempie solo argomento assente |
| ReferenceSlot | riferimento attivo | sceglie entro candidati reali |
| pre-cache | routine attiva | forma canonica senza valori personali |
| inventario | tutti gli stati visibili al principale | nessun effetto operativo |

La memoria non cambia direttamente tool, numero di passi, vaglio o politica di
output. La routine cambia la richiesta canonica prima del planner in modo
auditabile; è quindi una capacità separata con criterio proprio.

## 9. Minacce e contromisure

| Minaccia | Contromisura obbligatoria |
|---|---|
| `host` fabbricato da fallback | factory memoria fail-closed; nessuna conversione da `actor` solo |
| principal revocato in dialogo o ripresa | ricontrollo `authz_revision` al consumo |
| pagina/email ordina di ricordare | filtro origine prima di ogni modello |
| candidato ostile creato su share/registro | provenienza candidato, ancora d'identità, domanda non preselezionata |
| memoria stantia alimenta un invio | controllo a livello dell'intero piano con sorgente e destinazione |
| due preferenze in store diversi | instradamento PreferenceSpec e conflitto cross-store |
| evento fallito diventa abitudine | `runtime_outcome=success` obbligatorio |
| delete seguito da compilazione tardiva | invalidazione coda per epoch e ricontrollo transazionale |
| restore resuscita dati | journal separato o quarantena completa |
| claim LLM citato come prova | citazione della clausola/turno, mai del claim derivato |
| identificatore riciclato | `identity_anchor` verificata dal provider |
| default personale altera capability | capability calcolate dopo il valore finale |
| routine personale contamina L0/L1 | forma canonica chiusa e priva di valori; A/B cache obbligatorio |
| dossier sensibile | categorie rifiutate, quote, niente profilo riassuntivo persistito |
| lettura profilo ospite da admin | separazione applicativa e limite locale dichiarato |
| LLM indisponibile | CRUD, W2, riferimenti esatti e FTS restano disponibili |

## 10. Superficie in linguaggio naturale

La UX primaria è la chat. Le pagine amministrative sono una vista aggiuntiva,
non il solo controllo. Il Tutor documenta le operazioni soltanto quando sono
realmente implementate e certificate.

Esempi di richieste che il confine deve comprendere semanticamente in IT/EN:

```text
Ricorda che Atlas è il progetto del portale.
Che cosa sai di me?
Perché hai scelto quel calendario?
Correggi: per il lavoro ora uso Team.
Dimentica il riferimento ad Atlas.
Non imparare più automaticamente.
Resume ciò che abbiamo fatto ieri sui rapporti.
Prepara le solite cose per il rapporto.
```

Gli esempi sono casi di accettazione, non frasi da copiare nel codice o nei
prompt. I concetti vivono nel `detection_lexicon` con forme IT/EN. Ogni
risposta rivolta all'utente usa chiavi i18n nella lingua corrente; una nuova
lingua aggiunge mapping e messaggi, non branch applicativi.

## 11. Verifica non giocabile

### 11.1 Corpus congelato in F0

Minimo:

- 30 coppie sorgente-beneficio separate da almeno una sessione: preferenze,
  default, riferimenti, decisioni, episodi e routine;
- 30 richieste-esca con profilo irrilevante, etichettate prima del test;
- 10 trappole con riferimento corretto poi rimosso, sostituito o riciclato;
- 5 trappole di **candidato fabbricato da contenuto esterno prima della
  conferma**: una cartella su uno share condiviso, un contatto sincronizzato, una
  voce in un registro scrivibile da terzi, tutti creati con il nome giusto *prima*
  che l'utente confermi il riferimento. È il modo di guasto più realistico dei
  riferimenti — chi può scrivere sullo share governa i candidati — e senza questi
  casi il criterio bloccante «falso riferimento silenzioso» di §11.3 non ha nulla
  da misurare;
- 12 casi cross-principal e revoca;
- 12 attacchi da testo esterno, citazioni, allegati, codice e output tool;
- IT ed EN separati; formulazioni e domini non condivisi fra sviluppo e test.

Le numerosità qui elencate sono il minimo del corpus. Nessuna fase e nessuna prova
fissa un proprio conteggio di esche o trappole: lo legge dall'etichetta del corpus
(§13.0 T16).

Ogni caso porta referente/valore atteso indipendente dall'esito executor. La
partizione idoneo/non idoneo è nell'etichetta del corpus, non decisa dal sistema
sotto test.

### 11.2 Tre bracci

Sul medesimo replay e snapshot:

1. memoria spenta;
2. baseline lineare: ultimo valore esplicito per slot, senza compilatore o
   retrieval semantico;
3. impianto completo della fase.

La metrica primaria è il numero di interazioni necessarie per raggiungere
l'esito corretto. Per domande di profilo si aggiungono accuratezza rispetto
alle fonti, completezza delle citazioni e astensione corretta. L'impianto deve
battere la baseline lineare, non soltanto l'assenza di memoria.

F0 preregistra per ogni fase metrica primaria, due metriche di danno, effetto
minimo, seed, metodo accoppiato, intervallo, potenza e regola di arresto. Una
riduzione di passi è secondaria e non basta per promuovere.

Contratto delle metriche di retrieval:

- `recall@k` si calcola sulla stessa lista, già troncata a non più di `k`, che
  viene consegnata al consumer;
- `k`, elementi effettivamente restituiti e budget testuale vengono registrati
  per ogni caso; nessun risultato oltre `k` entra nel numeratore;
- `recall_any`, `recall_all` e frazione media restano metriche diverse;
- gli abstention case sono riportati separatamente e non spariscono dal totale
  presentato;
- retrieval recall non viene chiamato accuratezza della memoria: F4 misura
  anche la risposta finale rispetto all'oracolo e l'astensione;
- codice, configurazione, dipendenze, dataset e risultato portano lo stesso
  commit o un provenance manifest che ne dimostra la corrispondenza.

### 11.3 Criteri bloccanti

Tolleranza osservata zero, con denominatore esplicito e limite superiore `3/N`
riportato:

- lettura o scrittura cross-principal;
- principal fabbricato da fallback;
- scrittura da contenuto esterno o prosa dell'assistente;
- persistenza sensibile o di segreti;
- falso riferimento silenzioso nelle trappole;
- ricomparsa dopo oblio;
- routine che inserisce valori personali nella cache;
- consenso o capability ridotti dalla memoria.

Le metriche «una consultazione massima» e «zero LLM su turni non idonei» sono
invarianti/property test, non prove di qualità.

### 11.4 Annotazione

Le annotazioni semantiche dichiarano natura dei due annotatori, indipendenza,
tasso di disaccordo e adjudication. Due esecuzioni dello stesso modello o la
stessa persona in due momenti non vengono chiamate annotazione indipendente
senza qualificazione.

### 11.5 Fault injection e prove vive

Obbligatori:

- crash prima/dopo commit di set e delete;
- lease compiler scaduto durante un delete;
- restore con journal presente, stantio e assente;
- revoca durante dialogo, task e ripresa;
- FTS corrotto o embedding assente;
- provider ID riutilizzato;
- cambio manifest di personalizzazione;
- cache L0/L1 calda tra due principali con default diversi;
- un turno reale HTTP e uno Telegram per ogni fase user-facing;
- spegnimento della fase con comportamento della precedente invariato.

## 12. Fasi

### F0 — Fondazione, identità e prova

**Lavoro:**

- `PrincipalContext` canonico e `authz_revision`;
- intervalli generici nel segmentatore compound;
- `UserContextSnapshot` e contratti puri;
- policy fonte/sensibilità/purpose;
- corpus congelato e piano statistico;
- specifica journal, ripristino e quote;
- tutti gli interruttori spenti.

**Uscita:** nessun percorso memoria accetta `NO_OWNER`, LAN non associata,
binding revocato o solo `actor`; contratti serializzabili; corpus e soglie
preregistrati; nessun comportamento utente modificato.

### F1 — Store esatto e oblio in laboratorio

**Lavoro:**

- schema minimo fino a `evidence_tombstones`;
- scheletro `compile_queue` con lease e controllo epoch, senza compilatore LLM;
- `append_event`: accodamento autenticato **senza filtro**, unico scrittore di
  eventi sorgente per tutte le fasi successive;
- i due registri di estensione dello schema, `CASCADE_TABLES` e `KEY_TARGETS`;
- CRUD legato al principale e risultati vettoriali per elemento;
- revisioni W2/memoria e istantanea;
- journal write-ahead separato, riesecuzione idempotente, invalidazione coda e
  ripristino in quarantena;
- inventario deterministico su dati sintetici.

**Uscita:** set/get/list/delete, restart, migrazione, backup, restore e race
delete/consumer sintetico superano test e fault injection, compresi i crash fra
`PREPARED`, commit dei due store e `COMMITTED`; nessun dato implicito reale.

### F2 — Controllo dalla chat e preferenze esplicite

**Lavoro:**

- `UserContextBoundary` comune HTTP/Telegram;
- detection lexicon e clausole dirette;
- «ricorda/correggi/dimentica/cosa sai»;
- applicazione effettiva di `reply_length`, `tone`, `units` e lingua dove
  pertinente;
- i18n e superficie amministrativa secondaria.

**Uscita:** UC-01, UC-09 e UC-10 verdi end-to-end su HTTP e Telegram; nessun
executor o vocab `memories`; piano/cache invariati sui casi irrilevanti.

### F3 — Riferimenti e default operativi

**Lavoro:**

- ReferenceSlot `project` su `project_paths.json`;
- registro compilato delle dichiarazioni `[personalization]`;
- provider runtime con `TurnInvocationContext`;
- un default operativo pilota su un argomento reale;
- ancore d'identità, isteresi e controllo a livello piano.

**Uscita:** UC-03, UC-04, UC-11 e UC-12 verdi; full vs baseline lineare con
beneficio preregistrato; zero falso riferimento nelle trappole; cache calde
cross-principal senza contaminazione.

### F4 — Ricerca personale ed episodi attestati

**Lavoro:**

- FTS5 con compilatore di query sicuro;
- A/B graduatoria pura contro diversità per sorgente, con lo stesso `k` reale;
- domande aggregate come elenco deterministico citato ai turni;
- `runtime_outcome` ed `episode_refs`, emessi una volta sola per turno;
- **`relations` con il solo `supersedes` deterministico** su chiavi tipizzate: è
  ciò che rende UC-05 chiudibile qui invece che in F5;
- budget, troncamento e ragionamento temporale esatto.

**Uscita:** UC-05, UC-06 e UC-08 verdi; esatto e FTS battono o eguagliano le
baseline senza false applicazioni; richiamo a `k` ed esito complessivo riportati
separatamente; nessun claim del modello usato come evidenza; nessun compositore
locale nel percorso della risposta aggregata.

### F5 — Apprendimento implicito automatico

**Lavoro:**

- enqueue autenticato soltanto dopo F1-F4 certificate;
- filtro e log degli scarti;
- compilatore locale in ombra, reconciler deterministico-prima;
- promozione automatica proprietario per classi non sensibili;
- conflitto W2/memoria, retention e controllo chat.

**Uscita:** UC-02 attiva senza approvazioni per elemento; il braccio completo
batte la baseline lineare; danni bloccanti a zero sul denominatore dichiarato;
ospiti restano spenti e non viene rivendicata alcuna protezione basata sull'età
finché il registro non possiede il marcatore verificabile previsto in F0.

### F6 — Routine personali e comprensione ellittica

**Lavoro:**

- `RoutineBinding` da forme di piani riusciti attestati, riusando W1;
- rilevatore semantico nel lessico comune;
- selezione deterministica e riscrittura canonica povera;
- audit originale/canonico e controllo cache;
- nessuna autorità implicita.

**Uscita:** UC-07 verde su richieste oggi non comprese; il beneficio supera la
baseline lineare; nessun valore personale in L0/L1; casi ambigui chiedono o si
astengono; spegnendo F6 F0-F5 restano integri.

## 13. Attuazione

Questo capitolo dice COME si costruisce ciò che i capitoli precedenti dichiarano.
È vincolante quanto il resto: una fase che se ne discosta non è chiusa.

Le affermazioni sul codice esistente sono marcate `[PROVATO]` con percorso e
riga verificati il 28 luglio 2026; le proposte di progetto sono marcate
`[IPOTESI]`. Un'affermazione non marcata è un difetto di questo capitolo.

### 13.0 Decisioni trasversali

Sedici regole che valgono per tutte le fasi. Non sono raccomandazioni: ognuna
chiude un difetto strutturale accertato, e la prova che la sorveglia è indicata.
Una fase che ne viola una non è costruibile sulle precedenti.

**T1 — Una sola chiave di soggetto: `principal_id`.** Ogni tabella dello store,
ogni predicato di ricerca e ogni firma pubblica usano `principal_id`. In
`users.db` la colonna `owner_user_id` designa il PROPRIETARIO dell'ospite
[PROVATO `runtime/users.py:53`, con `ROLES = ("host","guest")` a `:42`]:
chiavarci sopra fonderebbe ospite e proprietario in un unico contenitore, contro
l'invariante 3 e UC-12. Dove serve la vista del proprietario (quota,
amministrazione) è una colonna in più, mai la chiave. *Prova*: una verifica di
schema rifiuta qualunque tabella dello store la cui colonna di soggetto non sia
quella dichiarata in `store.CASCADE_TABLES`.

**T2 — Un solo registro dei vocabolari chiusi.** `runtime/user_context/contracts.py`,
creato in F0, è l'unica sede di `SourceKind`, `Purpose`, `Sensitivity`,
`MemoryState`, `Retention` e di ogni altro insieme chiuso. `policy.py` importa e
non ridefinisce; nessuna fase successiva introduce un enum omonimo o un sinonimo
(`EventKind`, `PurposeCode` non esistono). `Purpose` porta i sei valori di §5.8 e
non altri, perché i tetti di §2.7 e dell'invariante 24 sono tarati su quelle
chiavi.

**T3 — Un solo emettitore per tipo di evento.** L'accodamento autenticato
`append_event` nasce in **F1**, senza filtro. F4 emette `runtime_outcome`, uno e
uno solo per turno, e non dipende dall'interruttore di F5, perché è un dato di
esito e non apprendimento. F5 aggiunge soltanto `user_clause` e `correction`, il
filtro di §7.2 e i codici di scarto. *Prova d'invariante*: per ogni `turn_id`
esiste al più una riga `source_events(kind='runtime_outcome')` e al più una riga
`episode_refs`.

**T4 — L'oblio per chiave si estende per registro, non per memoria.** Oltre a
`store.CASCADE_TABLES` (cancellazione per principale) esiste `store.KEY_TARGETS`:
ogni contenitore chiavato per `(chiave, ambito)` vi dichiara store, tabella,
colonne e funzione di cancellazione. F1 vi registra `user_prefs` e `memories`; F3
`user_defaults` e `"references"`; F6 `routine_bindings`. *Prova d'invariante*:
diventa rossa quando una fase aggiunge un contenitore chiavato senza dichiararlo.
Senza questo registro la decisione 1 vale solo per le due superfici di F1, e un
default operativo sopravvive in silenzio a un «dimentica».

**T5 — Il digest dei claim ha una normalizzazione propria.** `claim_digest` si
calcola con una normalizzazione definita in `user_context/` — minuscole, spazi,
punteggiatura, **nessuna parola vuota**. È vietato riusare `normalize_query`:
la sua lista di parole vuote comprende verbi pieni e imperativi (`fai`, `dimmi`,
`mostra`, `quali`, `sono`, `show`, `tell`, `list`) [PROVATO
`runtime/engine/cluster.py:76-105`], quindi «fai il caffè» e «caffè» collassano
nello stesso digest e un oblio sull'uno ucciderebbe l'altro; e ogni futura
taratura della cache cambierebbe in silenzio che cosa muore. *Prova*: i moduli di
memoria non importano `engine.cluster`.

**T6 — Un solo specchio dell'epoch di cancellazione.** L'autorità è il journal
separato di F1; l'unico valore rispecchiato vive in `memory_state.deletion_epoch`.
Non esistono `user_revisions.deletion_epoch` né una colonna in `users`.
`continuity(principal_id, mirrored_epoch)` confronta quei due valori e nessun
altro. Le revisioni `authz_revision` e `prefs_revision` nascono entrambe in **F0**.

**T7 — `relations` nasce completa nello schema e ristretta nell'uso.** F4 crea la
tabella con `kind IN ('supports','contradicts','supersedes')` e
`origin IN ('deterministic','model_proposed')`, ma **scrive** soltanto
`('supersedes','deterministic')`, vietato a livello applicativo con prova. F5
toglie il divieto applicativo e non tocca lo schema: in SQLite un vincolo `CHECK`
non è modificabile con `ALTER TABLE` e una ricostruzione della tabella non è
pianificata da nessuna fase.

**T8 — L'inventario dei ripieghi d'identità è generato a macchina.** Nessuna fase
dichiara un numero fisso di ripieghi da chiudere: nel solo `runtime/` ci sono
**66** occorrenze di `or "host"` in 21 file [PROVATO], più i confronti
`actor == "host"`. Una prova enumera automaticamente le occorrenze
(`or "host"`, `== "host"`, valore predefinito `actor="host"` nelle firme) e le
confronta con un elenco di esenzioni motivate riga per riga, committato come
impronta. Il passo diventa rosso quando compare un ripiego nuovo o quando
un'esenzione non è più raggiungibile — non quando il conto non torna.

**T9 — Un solo predicato d'origine.** `policy.origin_admissible(text, span, ctx)
-> RejectReason | None`, puro e senza ingresso/uscita, è l'unica attuazione
dell'invariante 4. Il lessico `uc_quote.*` è seminato in F2 e riusato da F5.
Due attuazioni della stessa difesa divergerebbero, e le trappole del corpus
misurerebbero codice diverso a seconda del ramo.

**T10 — Una sola fonte per la sensibilità.** `detection_lexicon` con famiglie a
radice flessiva IT+EN è la fonte fin da F0; `policy.classify_sensitivity` è un
lettore del lessico, non una tabella parallela e non legata a un solo locale.

**T11 — Gli interruttori sono monotoni.** Un solo lettore centrale,
`user_context.flags`, risolve i sette valori d'ambiente in una **fase effettiva**:
la fase N è attiva soltanto se 0..N-1 lo sono. Una combinazione incoerente è un
rifiuto all'avvio con motivo esplicito, mai un degrado silenzioso. *Prova*: una
matrice sulle 128 combinazioni, ciascuna che mappa su una fase effettiva o su un
rifiuto nominato.

**T12 — Nessun auto-rinforzo, e si verifica.** Un evento `runtime_outcome` è
evidenza per un claim o per una routine soltanto se quel turno **non** porta
un'applicazione di quel claim (riga in `applications` con lo stesso `source_id`)
e non porta quel `routine_id`. È l'attuazione verificabile dell'invariante 27.
*Prova d'invariante*: una routine attiva usata cento volte non incrementa
`n_success` di una unità e non sposta la propria scadenza.

**T13 — Le soglie vivono in F0.** Ogni valore numerico che governa una
promozione — episodi indipendenti, successi minimi di una routine, margine di
selezione — è congelato in F0 dopo la baseline, con la misura che lo giustifica.
Le fasi successive citano la costante e non la ridefiniscono, nemmeno dentro una
prova. Una soglia decisa dentro la prova che dovrebbe verificarla non verifica
nulla.

**T14 — Ogni messaggio che raggiunge l'utente passa da una chiave i18n.** Vale
anche per gli errori di validazione già esistenti: `set_pref` e `delete_pref`
ritornano oggi stringhe italiane cablate [PROVATO `runtime/users.py:700`, `:703`,
`:708-709`], e F2 le porta in chat su HTTP e Telegram. Vanno sostituite con
codici risolti da `messages.get`, con chiavi IT+EN nel seme, **prima** di
qualunque passo che consegni testo.

**T15 — Toccare un sottosistema certificato richiede la sua certificazione.**
F0 riscrive `split_query_chunks` e la verifica del Tutor
[PROVATO `runtime/tutor/handoff.py:35`]; F2 inserisce un confine davanti al
Tutor [PROVATO `runtime/http_routes_agent.py:1072`]. In entrambi i casi la
falsificazione è la ri-esecuzione della certificazione Tutor a macchina scarica,
con la regola già ratificata «nessun verdetto sotto i 3 casi», non un numero di
turni scelto a mano.

**T16 — La numerosità delle prove viene dal corpus, non dal testo.** Ogni conteggio
di esche, trappole e casi è letto dalla partizione etichettata del corpus
congelato di F0. Nessuna sezione fissa un numero per conto proprio.

### 13.1 Moduli e direzione delle dipendenze

I percorsi richiedono conferma nelle ADR di fase; responsabilità e dipendenze
sono vincolanti.

| Fase | Percorso | Responsabilità |
|---|---|---|
| F0 | `runtime/principal_context.py` | factory canonica e viste ristrette |
| F0 | `runtime/user_context/contracts.py` | enum, dataclass pure, **tutti** i vocabolari chiusi (T2) |
| F0 | `runtime/user_context/policy.py` | origine (T9), sensibilità (T10), scopo, budget, soglie (T13) |
| F0 | `runtime/user_context/flags.py` | fase effettiva dai sette interruttori (T11) |
| F0 | `runtime/compound_decomposer.py` | segmentazione con intervalli, unico helper comune |
| F0 | `runtime/users.py` | `authz_revision`, `prefs_revision`, transazioni, durabilità |
| F1 | `runtime/user_context/store.py` | schema, transazioni, `CASCADE_TABLES`, `KEY_TARGETS` (T1, T4) |
| F1 | `runtime/user_context/service.py` | facciata legata al principale, `append_event` (T3) |
| F1 | `runtime/user_context/deletion_journal.py` | append, riesecuzione, quarantena (T6) |
| F2 | `runtime/user_context/boundary.py` | comandi espliciti di chat |
| F2 | `runtime/user_context/presentation.py` | lunghezza, tono, unità nella risposta finale |
| F2 | `runtime/detection_lexicon.py` + seme | concetti `usercontext.*`, `uc_quote.*` IT/EN |
| F3 | `runtime/user_context/personalization.py` | sezione firmata e default dichiarati |
| F3 | `runtime/user_context/references.py` | ReferenceSlot e ancore d'identità |
| F3 | `runtime/agent_runtime.py` | propagazione del principale e iniezione finale |
| F3 | `runtime/tutor_boundary.py` | vista ristretta del principale canonico |
| F4 | `runtime/user_context/retrieval.py` | esatto, FTS5, selezione |
| F4 | `runtime/user_context/episodes.py` | eventi d'esito e lettura del registro turni |
| F5 | `runtime/user_context/source_events.py` | filtro, codici di scarto, accodamento dal turno |
| F5 | `runtime/user_context/compiler.py` | estrazione e riconciliazione |
| F6 | `runtime/user_context/routines.py` | forme canoniche e riscrittura |

```text
contracts <- policy <- flags
    ^          ^
    |          |
 store <- service <- boundary / personalization / retrieval / routines
    ^          ^
    |          |
 source_events + compiler
```

Nessun adattatore riceve una connessione SQLite. `store` e `policy` non importano
planner, HTTP, Telegram, Tutor o executor, e non importano `engine.cluster` (T5).
Il compositore di risposte riceve soltanto risultati già filtrati.

Punti esistenti da censire esplicitamente in F0, non da considerare correzioni
incidentali: `actor_resolver`, `http_routes_agent`, `channels/daemon`,
`agent_runtime`, `orchestration`, dialoghi e riprese, callback dello scheduler,
`_RUNTIME_ARG_SOURCES`, `ArgTransform`, `backend_resolver`, `project_paths.json`,
`TutorPrincipal` e `CallbackOutcome`. Fra i censiti rientra anche
`terminator_log.sqlite`, che conserva query testuali degli utenti senza legame
con un principale e senza politica di conservazione [PROVATO
`runtime/engine/terminator.py:48`]: è una sede di dati personali che l'oblio non
raggiunge, e la decisione su di essa appartiene a F0.

### 13.2 F0 — Fondazione, identità e prova

**Contratti.**

```python
# runtime/principal_context.py  [IPOTESI]
class AuthSource(str, Enum):          # chiuso, uno per sorgente REALE oggi
    ADMIN_KEY = "admin_key"           # http_auth.py:224 [PROVATO]
    DEVICE_TOKEN = "device_token"     # http_auth.py:227 [PROVATO]
    ADMIN_COOKIE = "admin_cookie"     # http_auth.py:233 [PROVATO]
    USER_COOKIE = "user_cookie"       # http_auth.py:240 [PROVATO]
    CHANNEL_BINDING = "channel_binding"   # users.find_user_by_recipient, users.py:523 [PROVATO]
    INTERNAL_TASK = "internal_task"   # rigioco differito o schedulato, mai da rete

class AuthStrength(str, Enum):
    STRONG = "strong"     # chiave o biscotto verificato + legame corrente
    BOUND = "bound"       # legame canale/dispositivo verificato, senza segreto
    NONE = "none"         # sola posizione di rete: http_auth.py:273-274 [PROVATO]

class SubjectKind(str, Enum):
    OWNER = "owner"; GUEST = "guest"; SERVICE = "service"

class PrincipalUnavailable(RuntimeError):
    """Fallimento chiuso (inv. 2). Porta `reason` di vocabolario chiuso."""

@dataclass(frozen=True, slots=True)
class PrincipalContext:
    principal_id: str          # sempre users.id; MAI 'host', MAI un nome
    owner_user_id: str         # vista amministrativa, MAI chiave di store (T1)
    subject_kind: SubjectKind
    authenticated_subject_id: str      # diverso da principal_id se impersonato
    channel: str; device_id: str; binding_id: str
    auth_source: AuthSource; auth_strength: AuthStrength
    authz_revision: int
    conversation_id: str; turn_id: str
    adult_verified: bool | None = None   # None = registro senza marcatore (inv. 22)
    @property
    def is_owner(self) -> bool: ...      # sostituisce ogni `actor == "host"`
    def with_turn(self, turn_id: str) -> "PrincipalContext": ...

def from_http_request(request, body: dict, *, conversation_id: str = "") -> PrincipalContext: ...
def from_channel_binding(channel: str, sender_id: str, *, conversation_id: str = "") -> PrincipalContext: ...
def from_stored_reference(ref: dict) -> PrincipalContext: ...   # riprese, differiti, task
def to_reference(p: PrincipalContext) -> dict: ...              # serializzabile, senza segreti
def project_tutor(p: PrincipalContext): ...                     # TutorPrincipal ristretto
def current_authz_revision(owner_user_id: str) -> int: ...
def bump_authz_revision(conn, owner_user_id: str, reason: str) -> int: ...  # nella transazione del chiamante

# runtime/user_context/contracts.py — pure; UNICA sede dei vocabolari chiusi (T2)
class SourceKind(str, Enum):          # i soli tipi AMMESSI (§6.3)
    USER_CLAUSE = "user_clause"; INTERACTION_CHOICE = "interaction_choice"
    RUNTIME_OUTCOME = "runtime_outcome"; CORRECTION = "correction"

FORBIDDEN_ORIGINS = frozenset({"external_text", "assistant_prose",
                               "attachment", "code_block", "tool_output"})

class Purpose(str, Enum):             # esattamente i sei di §5.8
    PROFILE_INVENTORY = "profile_inventory"; PROFILE_ANSWER = "profile_answer"
    REFERENCE_RESOLUTION = "reference_resolution"; ARGUMENT_DEFAULT = "argument_default"
    EPISODE_RECALL = "episode_recall"; ROUTINE_RESOLUTION = "routine_resolution"

class Sensitivity(str, Enum): ORDINARY = "ordinary"; SENSITIVE = "sensitive"; SECRET = "secret"

@dataclass(frozen=True, slots=True)
class UserContextSnapshot:
    principal_id: str; prefs_revision: int; memory_revision: int
    deletion_epoch: int; policy_version: str
    prefs: Mapping[str, "PrefRecord"]; defaults: Mapping[str, str]
    memory_ids: tuple[str, ...]        # soli identificatori, mai prosa (§5.2)
    def is_stale(self, *, prefs_revision: int, deletion_epoch: int) -> bool: ...

@dataclass(frozen=True, slots=True)
class PrefRecord:
    key: str; value: str; source: str; updated_at: str

# runtime/user_context/policy.py — importa contracts, non ridefinisce nulla (T2)
POLICY_VERSION = "f0.1"
THRESHOLDS: Mapping[str, int]     # congelate qui dopo la baseline (T13)
def origin_admissible(text: str, span: tuple[int, int], ctx) -> "RejectReason | None": ...  # T9
def classify_sensitivity(text: str, lang: str) -> Sensitivity: ...   # lettore di detection_lexicon (T10)
def budget_for(purpose: Purpose) -> "Budget": ...                    # righe, byte, elementi, ms

# runtime/user_context/flags.py — un solo lettore, fase effettiva monotona (T11)
def effective_phase() -> int: ...        # 0..6; combinazione incoerente => rifiuto all'avvio

# runtime/compound_decomposer.py — un solo segmentatore
def split_query_spans(query: str) -> list[tuple[int, int]]: ...   # intervalli del testo già ripulito
def split_query_chunks(query: str) -> list[str]: ...              # derivato dagli intervalli
```

**Dati.**

```sql
-- users.db, creata da users.init_db() (runtime/users.py:112) [PROVATO: oggi assente]
CREATE TABLE IF NOT EXISTS user_revisions (
  user_id         TEXT PRIMARY KEY REFERENCES users(id),
  authz_revision  INTEGER NOT NULL DEFAULT 1 CHECK (authz_revision > 0),
  prefs_revision  INTEGER NOT NULL DEFAULT 1 CHECK (prefs_revision > 0),
  updated_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_user_revisions_updated ON user_revisions(updated_at);
-- Monotonia per costruzione: ogni scrittura è `SET x = x + 1` dentro
-- BEGIN IMMEDIATE, mai un valore assoluto dal chiamante. Nessun innesco SQL:
-- l'incremento appartiene alla transazione applicativa (§6.1).
-- NESSUN `deletion_epoch` qui: l'unico specchio è `memory_state`, l'autorità è
-- il journal di F1 (T6).
```

**Innesti.**

| Punto | File:riga oggi | Che cosa cambia |
|---|---|---|
| Filtro di autenticazione HTTP | `runtime/http_auth.py:273-277` [PROVATO] | **Estensione**: oltre a `role`/`device_id`, il filtro scrive `auth_source` e `auth_strength`; il ripiego per sola posizione di rete resta `role="user"` ma marcato `NONE`, e la fabbrica lo rifiuta |
| Attore HTTP | `runtime/http_routes_agent.py:342-347` [PROVATO] | **Censimento**: `return request.get("device_id") or "host"`. In F0 la fabbrica gira accanto, in ombra; la sostituzione è F3 |
| Utente logico HTTP | `runtime/http_routes_agent.py:2400-2409` [PROVATO] | **Censimento**: host unico (2400-2403) e `return actor or "anonymous"` (2409); il principale sintetico `http_device_<digest>` (2393-2396) NON è `users.id` e non può essere `principal_id` |
| Attore da pairing | `runtime/actor_resolver.py:48,50,53` [PROVATO] | **Censimento**: tre `return "host"` non autenticati. La fabbrica non chiama questa funzione; la scrittura pigra 58-75 resta, registrata come risoluzione con effetto persistente |
| Principale Telegram | `runtime/channels/daemon.py:1245-1273` [PROVATO] | **Estensione**: diventa l'adattatore che alimenta `from_channel_binding`; la deduzione `"host" if actor == "host"` (`:1264-1265`) è un ripiego, e le eccezioni inghiottite a `:1258-1259` diventano rifiuto |
| Registrazione del turno | `runtime/agent_runtime.py:6679` [PROVATO] | **Estensione**: `TurnLog` riceve `principal_ref`, `authz_revision`, `auth_source`. È il riferimento che F4 confronterà, NON `owner_user_id`, che qui nasce dalla catena `owner_user_id or actor or "host"` e per i turni reali vale `host` |
| Firma del turno | `runtime/agent_runtime.py:6640-6643` [PROVATO] | **Estensione**: parola chiave facoltativa `principal=None`, sola registrazione. Il valore predefinito `actor="host"` cade in F3 |
| Apertura di `users.db` | `runtime/users.py:81-87` [PROVATO] | **Sostituzione**: `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`, schema preferenze e revisioni sempre applicati |
| Scrittura preferenze | `runtime/users.py:695-756` [PROVATO] | **Sostituzione**: `set_pref`/`delete_pref` in `BEGIN IMMEDIATE` sul modello di `consume_pairing_token` (`runtime/users.py:456-460` [PROVATO]), incremento di `prefs_revision` nella STESSA transazione, `finally: conn.close()` (oggi assente in tutte e quattro) |
| Lettura preferenze | `runtime/users.py:733-741` [PROVATO] | **Sostituzione**: `list_prefs` ritorna `PrefRecord` con `source` e `updated_at`, oggi scartati nella SELECT. Consumatori da aggiornare insieme: `runtime/http_routes_admin.py:1122` e `runtime/templates/user_detail.html:164-166` [PROVATO] |
| Mutazioni d'identità | `runtime/users.py:269,282,298,342,401,448` [PROVATO] | **Estensione**: sei siti (cancellazione, autonomia, aggiornamento, aggiunta e rimozione canale, consumo del gettone) incrementano `authz_revision` nella propria transazione |
| Revoche fuori `users.db` | `runtime/pairing.py:279` e `runtime/devices.py:625` [PROVATO] | **Estensione**: entrambe chiamano `bump_authz_revision` del proprietario risolto; oggi sono tre revoche indipendenti che non si richiamano |
| Presa in carico della chat | `runtime/active_sessions.py:491` [PROVATO] | **Estensione**: `revoke_session(reason="takeover"\|"admin")` incrementa la revisione; `validate_writer` (`:470-487`) resta il modello di confronto proprietario già corretto |
| Segmentatore comune | `runtime/compound_decomposer.py:95-101` [PROVATO] | **Sostituzione**: `split_query_spans` diventa primario e usa lo STESSO oggetto regex senza gruppi di cattura (`:42-54` [PROVATO]); `split_query_chunks` ne deriva |
| Verifica del Tutor | `runtime/tutor/handoff.py:35` [PROVATO] | **Sostituzione**: `chunk not in query` (contenimento) diventa confronto posizionale sugli intervalli — oggi passa anche se il segmento compare altrove. Falsificazione: certificazione Tutor (T15) |
| Proiezione Tutor | `runtime/tutor_boundary.py:56` e `:68` [PROVATO] | **Nuovo modulo, non ancora innestato**: `project_tutor` esiste e ha una prova di parità; la catena `... or "http-user"` e le due derivazioni divergenti di pubblico restano fino a F3 |
| Autorità per nome | `runtime/system/admin.py:443`, `runtime/vaglio.py:387`, `runtime/admin_chat_commands.py:64` [PROVATO] | **Censimento**: `actor == "host"` (in `vaglio` anche l'attore VUOTO); `PrincipalContext.is_owner` esiste ma nessuno lo consuma in F0 |
| Rigioco differito | `runtime/recurring_tasks.py:549-552`, `runtime/deferred_turns.py:71`, `runtime/agent_server.py:234`, `runtime/orchestration.py:604`, `runtime/http_routes_agent.py:1647` [PROVATO] | **Estensione**: cinque siti scrivono e rileggono un riferimento di principale con `from_stored_reference`, che in F0 solo REGISTRA la discordanza di revisione senza rifiutare |
| Cache di piano | `runtime/engine/fastpath.py:147-160`, `runtime/engine/autopath.py:102-137` [PROVATO] | **Nulla cambia**: F0 aggiunge una prova d'invariante che diventa rossa se compare una colonna d'identità o di revisione (inv. 8) |

**Ordine di costruzione.**

1. `contracts.py`, `policy.py` e `flags.py` puri, senza altro. *Verifica*: prova
   sul grafo delle importazioni — non importano `sqlite3`, `aiohttp`,
   `agent_runtime`, `engine`, `tutor`; violazione = rosso. La matrice delle 128
   combinazioni d'interruttore mappa ciascuna su una fase effettiva o su un
   rifiuto nominato (T11).
2. Durabilità di `users.db`: WAL, attesa sull'occupato, chiusura delle
   connessioni, transazioni esplicite su preferenze. *Verifica*: due scrittori
   concorrenti su 500 scritture, zero `database is locked`; il conteggio dei
   descrittori del processo resta piatto.
3. Chiavi i18n degli errori di validazione di W2 (T14), prima di qualunque passo
   che consegni testo. *Verifica*: nessuna stringa di errore letterale resta in
   `runtime/users.py`; seme IT+EN presente.
4. `user_revisions` e incremento nei sei siti di `users.py` più le due revoche
   esterne. *Verifica*: prova di proprietà — per ogni funzione pubblica che muta
   identità o preferenze, la revisione dopo è strettamente maggiore. **Questo
   passo deve poter fallire in modo visibile**: un sito di mutazione dimenticato
   rende rossa l'enumerazione, che elenca i nomi delle funzioni scoperte.
5. Inventario generato dei ripieghi d'identità (T8) con le esenzioni motivate.
   *Verifica*: l'inventario corrente è committato come impronta; l'aggiunta di un
   `or "host"` in `runtime/` rende rosso il passo, e così la rimozione di
   un'esenzione non più raggiungibile.
6. `principal_context.py` con le tre fabbriche, in sola ombra. *Verifica*: sul
   corpus congelato la fabbrica o costruisce o solleva `PrincipalUnavailable`;
   ogni rifiuto porta un motivo del vocabolario chiuso e ogni caso ammesso
   produce un `principal_id` che è un `users.id` reale.
7. `split_query_spans` e riscrittura di `split_query_chunks`. *Verifica*: per
   ogni richiesta del corpus,
   `[q[a:b].strip() for a,b in spans] == split_query_chunks(q)`; più la richiesta
   con elisione «quando è stato modificato» che oggi il doppio sguardo sugli
   apostrofi protegge (`compound_decomposer.py:39,52` [PROVATO]).
8. `UserContextSnapshot` costruita in sola lettura da `users.db`. *Verifica*: due
   costruzioni consecutive senza mutazioni danno lo stesso valore; una `set_pref`
   in mezzo rende `is_stale` vero.
9. Corpus congelato §11.1 con tutte le sue categorie, e congelamento delle soglie
   (T13) dopo la baseline. *Verifica*: impronta del corpus committata; la
   partizione idoneo/non idoneo è nell'etichetta; una prova rifiuta un caso privo
   di referente atteso indipendente; ogni soglia citata dalle fasi successive
   esiste in `policy.THRESHOLDS`.
10. Parità di comportamento. *Verifica*: rigioco del corpus a interruttore spento,
    prima e dopo l'intera F0 — `final_message`, `final_kind`, `effect_counts` e
    piano identici campo per campo — **più** la ri-esecuzione della certificazione
    Tutor a macchina scarica (T15), perché i passi 7 e la verifica di
    `handoff.py` toccano quel sottosistema.

**Interruttore.** `METNOS_USER_CONTEXT`, valori `off|shadow|on`, predefinito
**`off`**; in F0 `on` non è ammesso e viene rifiutato all'avvio. Con `off`
restano attivi: la durabilità di `users.db` (passo 2), le chiavi i18n (passo 3),
l'incremento delle revisioni (passo 4), l'inventario dei ripieghi (passo 5), il
segmentatore con intervalli (passo 7) e la prova d'invariante sulle cache. **Non**
sono coperti dall'interruttore perché sono correzioni, non capacità: vanno
dimostrati invarianti dal passo 10, non nascosti. Con `shadow` la fabbrica
costruisce e registra principale e istantanea senza che alcun consumatore le
legga: nessuna decisione, nessun rifiuto, nessun messaggio.

**Prove.**

- *Unitarie*: richiesta ammessa per sola posizione di rete → rifiuto, non `host`;
  impersonazione amministrativa → `authenticated_subject_id` diverso da
  `principal_id`; pairing senza riga in `users.db` → rifiuto; incremento della
  revisione da 200 processi concorrenti → sequenza senza salti né ripetizioni;
  nome utente uguale all'identificativo di un altro (`users.py:240` `id=? OR name=?`
  [PROVATO]) → rifiuto per ambiguità; testo citato o inoltrato → `origin_admissible`
  ritorna un motivo, non `None`.
- *D'integrazione*: due browser dello stesso proprietario su dispositivi diversi →
  stesso `principal_id`, `device_id` diversi; parità fra `_callback_principal`
  odierno e `from_channel_binding` su tutti i pairing reali, con la sola differenza
  attesa sui casi dedotti per nome; `project_tutor` uguale byte per byte all'uscita
  odierna di `http_principal` e `telegram_principal` per ogni principale
  costruibile.
- *Avversariali*: `actor` nel corpo della richiesta da un ruolo non amministrativo;
  `X-Forwarded-For` contraffatto da un nodo non fidato; ripresa di un dialogo il cui
  stato porta `actor: "host"` scritto a mano; chiave di preferenza aggiunta al
  registro dopo l'importazione del modulo (`users.py:630-636` congela `PREF_KEYS`
  all'importazione [PROVATO]) → rifiuto esplicito, non silenzio.
- *Iniezione di guasto*: `users.db` occupato durante un incremento → attesa e
  successo, mai revisione persa; interruzione del processo fra scrittura del valore
  e incremento → impossibile per costruzione, dimostrato aprendo il file dopo un
  `kill -9` iniettato fra le due istruzioni; riga di `user_revisions` assente o
  corrotta → rifiuto, non revisione zero; revoca del pairing mentre un dialogo è
  pendente → la rilettura del riferimento segnala la discordanza nel registro.
- *Turno reale §8.5*: una richiesta identica su `/agent/turn` e su Telegram,
  eseguita con `off` e poi con `shadow`: stessa risposta finale e stesso piano; nel
  secondo caso il registro mostra principale, `auth_source` e `authz_revision`, e il
  registro del turno porta il riferimento serializzato. Nessun messaggio all'utente
  cambia.

**Fuori da questa fase.**

- `user_memory.sqlite`, journal di cancellazione, quarantena del ripristino: **F1**.
  Conseguenza da non nascondere: in F0 il campo `deletion_epoch` dell'istantanea
  esiste nel contratto ma la sua **autorità nasce in F1**; finché il journal non
  esiste il valore vale 0 e F0 non può dichiarare alcun oblio verificato.
- Tabella `user_defaults`, sezione firmata `[personalization]`, ReferenceSlot
  `project` su `runtime/project_paths.json` — oggi solo blocco di prosa per il
  planner [PROVATO `runtime/agent_runtime.py:964`] e con contenuto incoerente col
  repository, che la validazione di F3 dovrà segnalare: **F3**.
- Sostituzione reale dei ripieghi censiti nei consumatori: **F3**, quando il
  principale viene propagato; F0 li inventaria e li misura soltanto.
- Cancellazione a cascata di `user_prefs` in `delete_user`, oggi assente [PROVATO
  `runtime/users.py:269-278`]: **F1**, con l'inventario post-oblio che la rende
  visibile.
- Comandi di chat, concetti `usercontext.*` e messaggi localizzati dell'inventario:
  **F2**.

### 13.3 F1 — Store esatto e oblio in laboratorio

**Contratti.**

```python
# runtime/user_context/contracts.py — il file nasce in F0; F1 vi aggiunge questi tipi [IPOTESI]
class MemoryState(str, Enum):  OBSERVED; CANDIDATE; ACTIVE_READ; ACTIVE_DEFAULT; CONFLICT
                               REJECTED; SUPERSEDED; EXPIRED; DELETED          # §6.4
class Retention(str, Enum):    DURABLE; CONTEXTUAL; BEHAVIORAL; EPISODIC       # §6.6
class DeleteScope(str, Enum):  KEY; MEMORY_ID                                  # decisione 1
class ItemStatus(str, Enum):   WRITTEN; UNCHANGED; DELETED; ALREADY_ABSENT; REFUSED; FAILED
class StoreHealth(str, Enum):  OK; QUARANTINED; DEGRADED

@dataclass(frozen=True)
class NewMemory:                       # claim + evento sorgente, in un solo valore
    kind: SourceKind; text: str; span: tuple[int, int]
    slot_key: str | None; scope: str; retention: Retention; value_text: str

@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str; retention: Retention; state: MemoryState
    slot_key: str | None; scope: str; value_text: str; claim_digest: str
    created_at: str; valid_from: str; valid_until: str | None; evidence_ids: tuple[str, ...]

@dataclass(frozen=True)
class ItemResult: index: int; status: ItemStatus; memory_id: str = ""; error_code: str = ""
@dataclass(frozen=True)
class Revisions: prefs_revision: int; memory_revision: int; deletion_epoch: int; policy_version: str
@dataclass(frozen=True)
class DeleteRequest:
    scope: DeleteScope; slot_key: str = ""; scope_name: str = ""
    memory_ids: tuple[str, ...] = (); expected_epoch: int = -1
@dataclass(frozen=True)
class DeleteOutcome:
    ok: bool; commit_id: str; new_epoch: int
    results: tuple[ItemResult, ...]                  # esito vettoriale per elemento (§2.1/§2.6/§2.8)
    invalidated_events: int; tombstones: int
    residual: tuple[MemoryRecord, ...]               # inventario post-oblio, decisione 1
    truncated: bool = False; truncated_what: str = ""; used: int = 0; available_total: int = 0

# runtime/user_context/service.py — unica facciata; nessun metodo accetta user_id libero (inv. 3)
def append_event(principal, kind: SourceKind, text: str,
                 span: tuple[int, int], *, correlation_id: str) -> str: ...   # T3
def put_memories(principal, items: Sequence[NewMemory]) -> list[ItemResult]: ...
def get_memories(principal, memory_ids: Sequence[str]) -> list[ItemResult | MemoryRecord]: ...
def list_memories(principal, *, purpose: Purpose, limit: int, offset: int = 0) -> ListPage: ...
def inventory(principal, *, include_deleted: bool = False) -> ListPage: ...
def forget(principal, request: DeleteRequest) -> DeleteOutcome: ...
def revisions(principal) -> Revisions: ...
def build_snapshot(principal) -> UserContextSnapshot: ...     # costruita, non ancora consumata

# runtime/user_context/store.py — registri che governano l'estensione dello schema
CASCADE_TABLES: dict[str, str]        # tabella -> colonna del principale (T1)
KEY_TARGETS: tuple[KeyTarget, ...]    # contenitori chiavati per (chiave, ambito) (T4)
def claim_digest(text: str) -> str: ...   # normalizzazione PROPRIA, senza parole vuote (T5)

# runtime/user_context/deletion_journal.py — store append-only separato (§6.5)
def prepare(principal_id: str, op: DeleteRequest, target_ids: Sequence[str]) -> tuple[str, int]: ...
def commit(commit_id: str) -> None: ...
def epoch_of(principal_id: str) -> int: ...                   # UNICA autorità sull'epoch (T6)
def replay_pending(*, apply: Callable[[JournalEntry], None]) -> ReplayReport: ...
def continuity(principal_id: str, memory_state_epoch: int) -> StoreHealth: ...
```

**Dati.**

```sql
-- user_memory.sqlite (nuovo, PATH_USER_DATA). WAL + busy_timeout + foreign_keys=ON.
CREATE TABLE memory_state (
  principal_id TEXT PRIMARY KEY, memory_revision INTEGER NOT NULL DEFAULT 0,
  deletion_epoch INTEGER NOT NULL DEFAULT 0,       -- UNICO specchio del journal (T6)
  policy_version TEXT NOT NULL,
  rows_active INTEGER NOT NULL DEFAULT 0, bytes_total INTEGER NOT NULL DEFAULT 0,
  health TEXT NOT NULL DEFAULT 'ok' CHECK (health IN ('ok','quarantined','degraded')),
  updated_at TEXT NOT NULL);

CREATE TABLE principal_settings (          -- stato per principale, vocabolario CHIUSO
  principal_id TEXT NOT NULL, key TEXT NOT NULL
    CHECK (key IN ('implicit_learning','compiler_mode')),
  value TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (principal_id, key));

CREATE TABLE source_events (
  event_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('user_clause','interaction_choice','runtime_outcome','correction')),
  turn_id TEXT NOT NULL, authz_revision INTEGER NOT NULL, observed_at TEXT NOT NULL,
  deletion_epoch_at_enqueue INTEGER NOT NULL, scrubber_version TEXT NOT NULL,
  correlation_id TEXT NOT NULL, span_start INTEGER, span_end INTEGER,
  payload TEXT NOT NULL, payload_digest TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','consumed','invalidated')));
CREATE INDEX idx_se_open  ON source_events(principal_id, state, observed_at);
CREATE UNIQUE INDEX idx_se_dedup ON source_events(principal_id, kind, turn_id, payload_digest);
CREATE UNIQUE INDEX idx_se_outcome ON source_events(principal_id, turn_id)
  WHERE kind = 'runtime_outcome';                  -- un solo esito per turno (T3)

CREATE TABLE memories (
  memory_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL,
  retention TEXT NOT NULL CHECK (retention IN ('durable','contextual','behavioral','episodic')),
  state TEXT NOT NULL, slot_key TEXT, scope TEXT NOT NULL DEFAULT '',
  value_text TEXT NOT NULL, claim_digest TEXT NOT NULL, n_bytes INTEGER NOT NULL,
  created_epoch INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  valid_from TEXT NOT NULL, valid_until TEXT);
CREATE UNIQUE INDEX idx_mem_slot ON memories(principal_id, slot_key, scope)
  WHERE slot_key IS NOT NULL AND state IN ('active_read','active_default');
CREATE INDEX idx_mem_twin  ON memories(principal_id, claim_digest);
CREATE INDEX idx_mem_state ON memories(principal_id, state, updated_at);

CREATE TABLE evidence (
  memory_id TEXT NOT NULL REFERENCES memories(memory_id) ON DELETE CASCADE,
  event_id  TEXT NOT NULL REFERENCES source_events(event_id) ON DELETE RESTRICT,
  role TEXT NOT NULL, added_at TEXT NOT NULL, PRIMARY KEY (memory_id, event_id));

CREATE TABLE evidence_tombstones (       -- blocca il riuso della stessa fonte (§6.2)
  principal_id TEXT NOT NULL, event_digest TEXT NOT NULL, claim_digest TEXT NOT NULL DEFAULT '',
  deletion_epoch INTEGER NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (principal_id, event_digest, claim_digest));

CREATE TABLE compile_queue (             -- scheletro: lease + epoch, consumatore reale in F5
  queue_id INTEGER PRIMARY KEY AUTOINCREMENT, principal_id TEXT NOT NULL,
  event_id TEXT NOT NULL REFERENCES source_events(event_id) ON DELETE CASCADE,
  deletion_epoch_at_enqueue INTEGER NOT NULL, enqueued_at TEXT NOT NULL,
  lease_owner TEXT, lease_until TEXT, attempts INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'ready' CHECK (state IN ('ready','leased','done','invalidated')));
CREATE UNIQUE INDEX idx_cq_event ON compile_queue(event_id);
CREATE INDEX idx_cq_ready ON compile_queue(principal_id, state, enqueued_at);
```

**Ampiezza dell'oblio (decisione ratificata 1, deterministica, zero modello).**
`KEY` risolve il bersaglio per chiave attraversando **tutti** i contenitori
dichiarati in `KEY_TARGETS` (T4) — in F1 `user_prefs` e ogni riga di `memories`
con lo stesso `(principal_id, slot_key, scope)` in **qualsiasi** stato, non solo
attivo: un gemello tipizzato già compilato condivide la chiave e muore con essa.
Le fasi successive vi registrano i propri contenitori, e la prova d'invariante
diventa rossa se non lo fanno. `MEMORY_ID` cancella gli identificativi indicati e
ogni riga dello stesso principale con identico `claim_digest`, calcolato con la
normalizzazione propria di T5. Un gemello che non è né di chiave né di digest
**sopravvive ma non in silenzio**: `DeleteOutcome.residual` è l'inventario di ciò
che resta al principale, con i campi di troncamento di §2.7 quando eccede il
tetto. Ogni evidenza cancellata lascia un tombstone `(event_digest, claim_digest)`
che vieta al compilatore di F5 di ricompilare lo stesso claim dalla stessa
fonte. [IPOTESI]

**Corsa dell'accodamento.** Il blocco locale per principale copre allocazione
dell'epoch, cancellazione e compilazione, ma non l'accodamento. La garanzia è
altrove ed è dichiarata qui: il compilatore confronta
`deletion_epoch_at_enqueue` di ogni evento con l'epoch autorevole **dentro la
transazione finale**, e un evento con epoch arretrato non promuove. Un evento
inserito fra i passi 4 e 6 di §6.5 con epoch stantio è quindi invalidato al
consumo, non allo sweep.

**Innesti.**

| Punto | File:riga oggi | Che cosa cambia |
|---|---|---|
| Percorsi canonici | `runtime/config.py:171-178` [PROVATO] | Estensione: `DB_USER_MEMORY = PATH_USER_DATA/"user_memory.sqlite"` e `LOG_USER_MEMORY_DELETIONS = PATH_USER_STATE/"user_memory_deletions.jsonl"`. Directory diverse per costruzione: un ripristino che riporta indietro i dati senza il journal è rilevabile (§7.11) |
| Transazione dell'oblio su W2 | `runtime/users.py:456-460` (`BEGIN IMMEDIATE` in `consume_pairing_token`) [PROVATO] | Precedente da riusare: `set_pref`/`delete_pref` (`:695-751`) sono oggi in autocommit e non chiudono la connessione [PROVATO]. `forget` apre una transazione esplicita |
| Cascata di W2 | `runtime/users.py:269-278` (`delete_user` cancella solo `user_channels`) [PROVATO] | Nuovo: `forget` non può appoggiarsi a `delete_user`; F1 corregge il difetto preesistente aggiungendo `user_prefs` alla cascata e contando le righe residue nella prova d'oblio |
| Vista in sola lettura del profilo | `runtime/sandbox.py:115-118` (`identity_profile` lega `users.db`), consumata da `executors/read_persons/manifest.toml:100` [PROVATO] | Estensione **obbligatoria**: con `users.db` in WAL un lettore in sola lettura dentro bubblewrap non può creare `-shm`. O si legano i due file affiancati, o W2 resta in modalità `truncate` e il WAL vive solo su `user_memory.sqlite`. Difetto che nasce dal lavoro W2 di F0 e si manifesta qui |
| Durabilità del journal | `runtime/undo.py:37-46` (`flock`+`fsync` su JSONL) [PROVATO] | Nuovo modulo che ne copia la tecnica e **non** la politica d'errore: le due scritture dell'undo sono cedute in silenzio (`runtime/agent_runtime.py:3472-3474` e `:3491-3493`) [PROVATO]; qui un `PREPARED` non scritto interrompe l'oblio prima di toccare gli store |
| Riesecuzione all'avvio | `runtime/metnos_http_server.py:290-297` (blocchi di bootstrap idempotenti) [PROVATO] | Estensione: `replay_pending()` prima di servire. A differenza dei vicini, un fallimento non è un avviso: porta `memory_state.health='quarantined'` e ogni lettura fallisce chiusa |
| Conservazione | `runtime/jobs/maintenance_tasks.py:184-217` (`task_state_reaper`) e `runtime/nightly_orchestrator.py:42` [PROVATO] | Estensione: una voce `user_memory` (code invalidate, tombstone oltre finestra, quarantena scaduta). Nessun compito notturno nuovo (ADR 0186) |
| Registri di estensione | — | Nuovo: `CASCADE_TABLES` e `KEY_TARGETS` (T1, T4). È il modo in cui `"references"`, `applications`, `memory_fts`, `episode_refs`, `relations` e `routine_bindings` entrano senza riscrivere l'algoritmo di §6.5 |

**Ordine di costruzione.**

1. **Percorsi e apertura.** Costanti in `config.py`, `store._open()` con WAL,
   `busy_timeout`, `foreign_keys=ON` e chiusura in `finally`. *Verifica*: due
   processi scrivono in parallelo su file temporaneo senza `database is locked`;
   nessuna connessione resta aperta a fine chiamata.
2. **Schema, stato e registri.** Le tabelle sopra, più `CASCADE_TABLES` e
   `KEY_TARGETS`. *Verifica*: creazione da zero, riapertura e seconda esecuzione
   senza differenze; una tabella non dichiarata nei due registri rende rossa la
   prova di completezza.
3. **Journal separato.** `prepare`/`commit`/`epoch_of`, blocco locale per
   principale. *Verifica*: l'epoch cresce in modo monotono sotto venti richieste
   concorrenti e nessun `PREPARED` esce senza `fsync`; `continuity` confronta il
   journal con il solo `memory_state` (T6).
4. **Accodamento autenticato.** `append_event` senza filtro, con dedup per
   `(principal, kind, turn_id, digest)`. *Verifica*: due chiamate identiche
   producono un solo evento; un `runtime_outcome` doppio per lo stesso turno è
   respinto dall'indice parziale.
5. **CRUD legato al principale con esito vettoriale.** `put/get/list/inventory`,
   quota su righe attive e byte, troncamento con i campi §2.7. Nessuna firma
   accetta un identificativo utente libero. *Verifica*: la lettura con un
   principale diverso restituisce zero righe, non un errore generico; il
   superamento della quota produce `REFUSED` per elemento e non un fallimento
   del lotto.
6. **`forget` completo.** Gli otto passi di §6.5 nell'ordine, con la risoluzione
   del bersaglio della decisione 1 su tutti i `KEY_TARGETS`, l'invalidazione di
   **tutti** gli eventi non completati osservati prima del nuovo epoch, i
   tombstone e la rilettura negativa finale. *Verifica*: rilettura negativa
   fallita ⇒ `ok=False` e quarantena; è il passo che deve poter fallire in modo
   visibile.
7. **Riesecuzione e quarantena.** `replay_pending` all'avvio. *Verifica*:
   uccisione del processo fra `PREPARED` e `COMMITTED`, riavvio, dato assente e
   journal chiuso.
8. **Scheletro della coda.** Lease con scadenza e ricontrollo dell'epoch dentro la
   transazione finale; un consumatore sintetico di prova, non un compilatore.
   *Verifica*: un lease acquisito prima dell'oblio non promuove nulla dopo e marca
   l'evento invalidato.
9. **Inventario deterministico su dati sintetici.** Elenco con valore, stato, data,
   classe di conservazione e identificativi delle evidenze, senza modello.
   *Verifica*: stessa base, stesso ordine, stesso testo byte per byte in due
   esecuzioni.

**Interruttore.** `METNOS_USER_MEMORY`, predefinito `off`, risolto da
`user_context.flags` insieme agli altri (T11). Spenta: nessun file
`user_memory.sqlite` viene creato, nessuna voce nel reaper notturno,
`replay_pending` non gira, il servizio ha esattamente il comportamento odierno.
Restano attivi anche a interruttore spento, perché non sono condizionabili: la
chiusura corretta delle connessioni del CRUD preferenze, la cascata su
`user_prefs` in `delete_user` e l'eventuale estensione della vista
`identity_profile`. Il journal è governato dallo stesso interruttore: non esiste
uno stato in cui si cancella senza journalizzare.

**Prove.**

- *Unitarie.* Chiave duplicata per lo stesso slot attivo respinta dall'indice
  parziale; gemello tipizzato in stato `superseded` cancellato dall'oblio per
  chiave; gemello per digest esatto cancellato dall'oblio per identificativo;
  gemello che non è né l'uno né l'altro presente nell'inventario residuo; «fai il
  caffè» e «caffè» producono digest **diversi** (T5); tombstone che impedisce il
  reinserimento della stessa coppia fonte-claim; troncamento dell'inventario oltre
  la quota con `used` e `available_total` coerenti.
- *Integrazione.* Ciclo completo scrittura, elenco, oblio, rilettura negativa su
  base sintetica; riavvio con basi già popolate; migrazione di una `users.db` reale
  copiata in luogo isolato (`METNOS_USERS_DB` puntato altrove: `_open_db` crea e
  scrive al primo accesso [PROVATO `runtime/users.py:81-87`]); salvataggio e
  ripristino con journal coerente, stantio e assente, con i primi due casi
  leggibili e il terzo interamente in quarantena.
- *Avversariali.* Lettura e scrittura con principale di un altro utente; oblio con
  `expected_epoch` arretrato; richiesta di oblio su identificativi inesistenti
  mescolati a esistenti, che deve dare esito per elemento e non un fallimento
  unico; identificativo di memoria di un altro principale passato fra i propri.
- *Iniezione di guasto.* Interruzione fra `PREPARED` e la modifica degli store; fra
  la modifica dei due store; fra il commit locale e `COMMITTED`; journal non
  scrivibile; disco pieno durante `fsync`; lease del consumatore sintetico scaduto
  durante un oblio; `user_memory.sqlite` corrotto all'avvio.
- *Turno reale §8.5.* Due turni sulla stessa istanza con lo stesso testo, uno a
  interruttore spento e uno acceso: identico piano, identica risposta, zero righe
  scritte in `user_memory.sqlite`, e in entrambi la preferenza di lingua continua a
  risolversi al confine HTTP [PROVATO `runtime/metnos_http_server.py:94`]. Un terzo
  turno che invoca `read_persons` dopo la migrazione di `users.db`, per provare che
  la vista in sola lettura del profilo regge il cambio di modalità del giornale.

**Fuori da questa fase.**

- Qualunque superficie in chat: F1 espone solo funzioni e prove su dati sintetici;
  il confine comune HTTP/Telegram e le chiavi i18n dei comandi sono **F2**.
- `PrincipalContext`, revisioni, transazioni e WAL su `users.db`, intervalli di
  clausola, soglie: sono **F0** e F1 **non è costruibile senza**.
- I contenitori chiavati delle fasi successive (`"references"` e `user_defaults` in
  F3, `routine_bindings` in F6) si registrano da sé in `KEY_TARGETS`: F1 consegna il
  registro e la prova, non le voci.
- La catena `supersedes` non è percorribile in F1: `relations` arriva in F4. Fino ad
  allora il gemello non di chiave e non di digest resta visibile nell'inventario,
  non cancellato.
- Il consumatore reale della coda, il filtro di ammissibilità e ogni scrittura
  implicita sono **F5**: qui la coda ha lease ed epoch, e un solo consumatore finto
  vivo nelle prove.
- La propagazione dell'istantanea dentro `run_turn` e l'iniezione dopo il piano sono
  **F3**: `build_snapshot` esiste e viene provata, ma in produzione nessuno la
  chiama.

### 13.4 F2 — Controllo dalla chat e preferenze esplicite

**Contratti.**

```python
# runtime/user_context/contracts.py — estensione F2 dei tipi puri nati in F0 [IPOTESI]
class ChatCommand(str, Enum):        # vocabolario CHIUSO del confine, non del planner
    REMEMBER = "remember"; CORRECT = "correct"; FORGET = "forget"; INVENTORY = "inventory"

class BoundaryOutcome(str, Enum):
    NOT_MINE = "not_mine"      # prosegue il motore, nessun effetto
    DONE = "done"; ASK = "ask"; REFUSED = "refused"; UNAVAILABLE = "unavailable"

@dataclass(frozen=True)
class ClauseSpan:  start: int; end: int; text: str        # intervalli prodotti in F0
@dataclass(frozen=True)
class ChatCommandMatch:
    command: ChatCommand; span: ClauseSpan; concept: str   # concetto lessicale che ha deciso
    target_kind: str                                        # "pref" | "claim" | "all"
    target_key: str; captured: str                          # gruppo di cattura, mai riscritto
@dataclass(frozen=True)
class BoundaryResult:
    outcome: BoundaryOutcome; message: str                  # già i18n, già nella lingua del turno
    audit: dict                                             # concetto, intervallo, reason code, revisioni

# runtime/user_context/boundary.py
def detect(query: str, *, spans: tuple[ClauseSpan, ...]) -> tuple[ChatCommandMatch, ...]: ...
def handle(query: str, principal, *, has_pending: bool) -> BoundaryResult | None: ...  # None = NOT_MINE

# runtime/user_context/presentation.py — l'unico oggetto che entra nel turno
@dataclass(frozen=True)
class PresentationSpec:
    reply_length: str; tone: str; units: str; lang: str
    prefs_revision: int; max_bullets: int; max_tokens: int
    suppressed: bool                                        # scavalcamento esplicito nel turno (inv. 6)
def presentation_spec(snapshot, query: str) -> PresentationSpec | None: ...   # puro
def apply_units(text_or_entries, spec: PresentationSpec): ...                 # insieme CHIUSO di campi

# inventario: registro di sezioni, non catena di rami — F3/F4/F6 registrano, non modificano
@dataclass(frozen=True)
class InventorySection:
    key: str; label_key: str; state: str                    # "ready" | "not_implemented"
    items: tuple[dict, ...]                                 # valore, origine, ambito, data, comando
def register_section(key: str, provider) -> None: ...
def inventory(principal) -> tuple[InventorySection, ...]: ...  # deterministico, zero LLM
```

**Dati.** F2 non crea tabelle. Scrive attraverso `service.append_event` e
`service.put_memories` di F1 (T3) e in `user_prefs` [PROVATO `runtime/users.py:683`].
L'unica modifica di schema che F2 pretende — `prefs_revision`, transazioni
esplicite e chiavi i18n degli errori di W2 — nasce in **F0** e non è riscritta qui.

**Innesti.**

| Punto | File:riga oggi | Che cosa cambia |
|---|---|---|
| Confine HTTP | `runtime/http_routes_agent.py:1072` (`tutor = await _apply_tutor_http(`) dentro `_preprocess_turn` (`:932`) [PROVATO] | Estensione: nuovo blocco **prima** del Tutor. «Che cosa sai di me?» è semanticamente una domanda sul sistema e oggi la prenderebbe il Tutor; il confine vince solo su corrispondenza ad alta precisione, e la falsificazione è la certificazione Tutor (T15) |
| Sonda dei pendenti | `runtime/http_routes_agent.py:358` `_http_has_pending` [PROVATO] | Riuso invariato: pendente presente ⇒ il confine si astiene (una risposta a un modulo non è un comando) |
| Confine Telegram | `runtime/channels/daemon.py:1500-1541`, principale da `_callback_principal` (`:1245`) [PROVATO] | Estensione simmetrica: stesso modulo, stessa posizione relativa. `_callback_principal` ritorna `None` per mittente ignoto ⇒ astensione fallendo chiuso [PROVATO] |
| Filtro d'origine | `policy.origin_admissible` (F0) | **Consumo, non riscrittura** (T9): il confine chiama il predicato comune; le esclusioni di citazioni, allegati, codice e uscite dei tool non sono reimplementate qui |
| Principale | `runtime/tutor_boundary.py:47` e `:64` [PROVATO] | **Non toccato in F2**: le due funzioni derivano il pubblico da campi diversi e ripiegano su `"http-user"` (`:56`). Il confine usa direttamente la fabbrica di F0; la proiezione del Tutor è lavoro F3 |
| Contesto di turno | `runtime/agent_runtime.py:6231` `runtime_ctx = {` [PROVATO] | Estensione: una sola chiave `presentation` con la `PresentationSpec`. Nessun `user_id`, nessuna memoria, nessun claim. Vietato esporla come `${RUNTIME:...}` [PROVATO `runtime/engine/executor.py:269`]: finirebbe negli argomenti di passo |
| Finalizzatore | `runtime/engine/executor.py:1524` `_finalize_answer_text(framework, steps, query, llm_fast)`, chiamato a `:2089` e `:2618` [PROVATO] | Estensione di firma con `presentation=None`. È la fonte UNICA del testo di un turno `answer` (ADR 0177 T5, dichiarato nel docstring [PROVATO]): un solo punto d'effetto, due chiamanti |
| Elenchi puntati | `runtime/engine/executor.py:156` `_entries_bullet_lines(..., max_items: int = 20)` [PROVATO] | Estensione: `max_items` passato dal chiamante secondo `reply_length`. Effetto deterministico, senza modello |
| Sintesi finale | `runtime/engine/executor.py:1774` `sys_msg = _pl.get("final_assembler", _lang)` e `:1789` `final_tokens = 700 if len(obs_lines) >= 3 else 360` [PROVATO] | Estensione: variabili Jinja per tono, lunghezza e unità, e tetto d'uscita derivato da `reply_length`. Il testo del `.j2` è rivolto al modello: lo scrive Fable, Opus integra e prova |
| Unità | `runtime/photon_client.py:175` `entry["distance_km"]` [PROVATO] | Nessuna modifica alla sorgente. Conversione solo in presentazione, su un insieme CHIUSO di campi dichiarati; insieme vuoto ⇒ nessun effetto, dichiarato nell'inventario invece che finto |
| Lessico | `runtime/detection_lexicon_seed.py:50` `register_all()` [PROVATO] | Nuovi concetti `usercontext.*` e `uc_quote.*` di tipo `regex` con gruppo di cattura, ancorati all'inizio della clausola, IT+EN. `register` è idempotente per riga: una forma sbagliata richiede `set_payload` [PROVATO `runtime/detection_lexicon.py:220`], non un secondo seme |
| Segmentazione | `runtime/compound_decomposer.py:95` [PROVATO] | Consumo, non modifica: F2 usa gli intervalli introdotti in F0. Oggi la funzione perde l'ancoraggio posizionale (`p.strip()`, scarto dei vuoti) e non basta [PROVATO] |
| Lettura preferenze | `runtime/users.py:733` `list_prefs` [PROVATO] | Già sostituita in F0; F2 la consuma. Consumatori aggiornati insieme: `runtime/http_routes_admin.py:1122` e `runtime/templates/user_detail.html:164` [PROVATO] |
| Scrittura preferenza | `runtime/users.py:695` `set_pref(..., source="explicit")` [PROVATO] | Contratto invariato; il confine passa `source="chat"`. Oggi nessun chiamante di prodotto valorizza `source`: diventa un vocabolario chiuso |
| Oblio tipizzato | `runtime/users.py:743` `delete_pref` [PROVATO] | Chiamata dal ramo oblio **attraverso** `service.forget`, dentro la transazione e il journal di F1: mai una cancellazione fuori dal protocollo |
| Superficie admin | `runtime/templates/user_detail.html:152-158` letterali italiani, `:164` chiave tecnica nuda [PROVATO] | Sostituzione con `msg(...)` (§7.13) e sezione inventario in sola lettura. Le chiavi `MSG_SETTINGS_*` del gruppo siti restano il modello |
| Cache | `runtime/engine/fastpath.py:229` `normalize_hash(query)`, `:55` `NON_CACHEABLE_TOOLS` [PROVATO] | **Nessuna modifica, per prova**: il confine non produce piano né executor, quindi non c'è nulla da escludere dalla cache. L'assenza di modifica è essa stessa un requisito verificato (§5.10) |

**Ordine di costruzione.**

1. Concetti `usercontext.*` e `uc_quote.*` nel seme, consumo degli intervalli di F0
   e del predicato d'origine. *Verifica*: sulle richieste-esca del corpus congelato
   — numerosità letta dall'etichetta, non fissata qui (T16) — zero riconoscimenti;
   «ricordami di comprare il latte» resta un compito e non un ricordo. Fallisce in
   modo visibile: un solo falso positivo blocca il passo.
2. Chiavi i18n dell'inventario e dei quattro comandi, **prima** di qualunque ramo
   che consegni testo (T14). *Verifica*: nessun marcatore tecnico (`ready`,
   `not_implemented`) raggiunge l'utente; seme IT+EN completo.
3. `PresentationSpec` e innesto nel finalizzatore, interruttore ancora spento.
   *Verifica*: con interruttore spento, testo finale byte-identico su un campione
   di turni registrati.
4. Accensione della sola presentazione, nessun comando. *Verifica*: A/B a memoria
   accesa e spenta sulle stesse richieste — piano, strumenti e `tools_sig`/`pool_sig`
   identici, lunghezza della risposta misurabilmente diversa. Un solo piano
   divergente è un fallimento bloccante (§5.10).
5. Confine HTTP, ramo inventario in sola lettura. *Verifica*: turno reale, **più**
   la certificazione Tutor a macchina scarica (T15): il confine sottrae casi al
   Tutor e nessun altro strumento lo misura.
6. Confine Telegram con lo stesso modulo. *Verifica*: stesso principale, stessa
   lingua, stesso testo dai due canali (UC-12, inv. 29).
7. Rami ricorda e correggi, attraverso `append_event` e `put_memories` di F1.
   *Verifica*: claim che ricade in una `PreferenceSpec` — in F2 derivata dalle sole
   chiavi W2 [PROVATO `runtime/users.py:632`, `:637`] — instradato a W2 e non alla
   memoria libera; revisione incrementata nella stessa transazione.
8. Ramo oblio e superficie amministrativa. *Verifica*: dopo «dimentica»,
   inventario che elenca esplicitamente ciò che resta, e assenza dopo riavvio del
   servizio.

**Interruttore.** `METNOS_USER_CONTEXT_CHAT`, predefinito `0`, risolto da
`user_context.flags` (T11): con F1 spenta la combinazione è rifiutata all'avvio,
non degradata. Spento: nessun confine su HTTP e Telegram, nessuna chiave
`presentation` in `runtime_ctx`, `_finalize_answer_text` invocata come oggi.
Restano attivi e **non** governati dall'interruttore, perché sono cambi di
contratto e non di comportamento: le chiavi i18n, la pagina amministrativa sanata,
i concetti lessicali seminati ma non interrogati.

**Prove.**

- *Unitarie*: «ricorda che X» riconosciuto in IT e EN con l'intervallo esatto della
  clausola; «ricordami di X» non riconosciuto; forma dentro una citazione, un
  blocco di codice o un allegato respinta **dal predicato comune** e non da una
  copia locale (T9, inv. 4); `presentation_spec` restituisce `suppressed` quando il
  turno contiene un'istruzione esplicita di presentazione (inv. 6); `apply_units`
  non tocca un campo fuori dall'insieme chiuso.
- *Integrazione*: preferenza posta da HTTP e onorata nel turno successivo da
  Telegram; inventario identico dai due canali; comando dato dentro una richiesta
  composta di due clausole — la mutazione tocca solo la propria clausola, l'altra
  prosegue al motore; con dialogo pendente il confine si astiene e la risposta
  arriva al modulo aperto.
- *Avversariali*: pagina o messaggio inoltrato che contiene «ricorda che ...» non
  scrive nulla; ospite che chiede l'inventario non vede claim del proprietario e
  viceversa; comando pronunciato con attore vuoto o su rete locale non associata non
  diventa proprietario (inv. 2); dichiarazione di categoria sensibile rifiutata e
  non persistita, con il testo scartato non conservato.
- *Iniezione di guasto*: store memorie non consultabile — il confine risponde
  `UNAVAILABLE` con messaggio tecnico esplicito e non prosegue verso il motore
  fingendo una richiesta ordinaria; caduta fra scrittura W2 e incremento della
  revisione; lessico non seminato nella lingua dell'istanza — l'esito è registrato,
  non subito; interruttore spento a caldo con turno in corso.
- *Turno reale §8.5*: uno su `/agent/turn` e uno su Telegram per ciascuno di UC-01,
  UC-09 e UC-10, sullo stesso principale, con il registro dei turni citato nella
  chiusura di fase.

**Fuori da questa fase.**

- `PrincipalContext`, revisioni, intervalli di clausola, corpus congelato,
  transazioni e chiavi i18n di W2 sono **F0**: F2 non è costruibile senza.
- Store, accodamento, journal separato, riesecuzione idempotente e quarantena sono
  **F1**: il ramo oblio di F2 è solo il chiamante.
- Default operativi, ReferenceSlot, sezione firmata `[personalization]` e la
  `PreferenceSpec` estesa alle dichiarazioni di dominio sono **F3**: in F2
  l'inventario mostra quelle sezioni come non implementate invece di ometterle.
- Episodi, «che cosa abbiamo fatto ieri», FTS5 e domande aggregate sono **F4**;
  apprendimento implicito e compilatore sono **F5** — in F2 «dimentica» agisce su
  una chiave tipizzata o su un identificativo già esistente.
- Routine e riscrittura canonica prima di L0 sono **F6**: F2 non tocca
  `normalize_hash` né l'insieme dei tool non memorizzabili.
- Tono e lunghezza sulle risposte del Tutor e dentro `describe_entries` restano
  fuori: sono compositori distinti dal finalizzatore, e un secondo punto d'effetto
  va misurato prima di essere aggiunto (§8.3 ne dichiara uno solo).

### 13.5 F3 — Riferimenti e default operativi

**Contratti.**

```python
# runtime/user_context/contracts.py — estensione F3 [IPOTESI]
class EffectClass(str, Enum):        # §5.4, vocabolario chiuso
    PRESENTATION = "presentation"; READ = "read"; MUTATING = "mutating"; OUTBOUND = "outbound"

class FillReason(str, Enum):         # esito per argomento, sempre registrato
    FILLED = "filled"; EXPLICIT_PRESENT = "explicit_present"; NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"; ANCHOR_MISMATCH = "anchor_mismatch"; REVOKED = "revoked"
    PROVIDER_UNAVAILABLE = "provider_unavailable"; STALE_EPOCH = "stale_epoch"; DISABLED = "disabled"

@dataclass(frozen=True)
class PersonalizationSpec:           # una voce della sezione firmata, già validata
    owner: str; preference_key: str; argument: str; scope_fields: tuple[str, ...]
    value_provider: str; operations: tuple[str, ...]; effect_class: EffectClass
    sensitivity: Sensitivity; identity_anchor: str          # "" = identificativo non riusabile

@dataclass(frozen=True)
class TurnInvocationContext:         # ciò che un provider può vedere: nessuna connessione
    principal_id: str; authz_revision: int; prefs_revision: int; memory_revision: int
    deletion_epoch: int; turn_id: str; channel: str; conversation_id: str
    tool: str; args_view: Mapping[str, object]; target_device: str; purpose: Purpose

@dataclass(frozen=True)
class ReferenceCandidate:
    candidate_id: str; label: str; scope: Mapping[str, str]; identity_anchor: str; provider: str

@dataclass(frozen=True)
class SlotResolution:
    status: str  # matched|ambiguous|absent|conflict|unavailable
    candidate_id: str; reason: FillReason; candidates: tuple[ReferenceCandidate, ...]

@dataclass(frozen=True)
class FillDecision:
    tool: str; argument: str; source_kind: str; source_id: str
    value_digest: str; reason: FillReason; effect_class: EffectClass

# runtime/user_context/personalization.py
def validate_section(owner: str, section: dict, args_schema: dict,
                     capabilities: list[dict]) -> tuple[PersonalizationSpec, ...]: ...   # solleva, nomina il colpevole
def compiled_registry(catalog) -> dict[tuple[str, str], PersonalizationSpec]: ...        # (tool, argument)
def fill_personal_args(executor, args: dict, ctx: TurnInvocationContext
                       ) -> tuple[dict, tuple[FillDecision, ...]]: ...                   # riempie SOLO se assente
def render_control_tokens(args: dict, ctx: TurnInvocationContext) -> dict: ...           # ${PERSONAL:tool.arg}
def personalized_effect_steps(framework, catalog) -> tuple[tuple[int, str, str], ...]: ...  # indipendente dal principale

# runtime/user_context/references.py
VALUE_PROVIDERS: dict[str, Callable[[TurnInvocationContext], tuple[ReferenceCandidate, ...]]]
def resolve(slot: str, alias: str, ctx: TurnInvocationContext) -> SlotResolution: ...
def confirm(slot: str, alias: str, cand: ReferenceCandidate, ctx: TurnInvocationContext) -> None: ...
def project_candidates(ctx: TurnInvocationContext) -> tuple[ReferenceCandidate, ...]: ...   # project_paths.json validato

# runtime/agent_runtime.py — rottura di firma consentita da §7.1
_RUNTIME_ARG_SOURCES: dict[str, Callable[[TurnInvocationContext], object]]
```

**Dati.** Entrambe le tabelle si registrano in `KEY_TARGETS` (T4) alla creazione:
un default operativo e un riferimento sono contenitori chiavati per
`(chiave, ambito)` e devono morire con l'oblio per chiave, altrimenti la decisione
ratificata 1 vale solo per le superfici di F1.

```sql
-- users.db (W2 resta autorevole; `prefs_revision` nasce in F0)
CREATE TABLE IF NOT EXISTS user_defaults (
  principal_id TEXT NOT NULL, key TEXT NOT NULL, scope_key TEXT NOT NULL DEFAULT '',
  value TEXT NOT NULL, provider TEXT NOT NULL, identity_anchor TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','revoked','conflict')),
  version INTEGER NOT NULL DEFAULT 1, source TEXT NOT NULL DEFAULT 'explicit',
  updated_at TEXT NOT NULL, expires_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (principal_id, key, scope_key));
CREATE INDEX IF NOT EXISTS user_defaults_live ON user_defaults(principal_id, state, expires_at);

-- user_memory.sqlite. "references" è parola riservata: sempre fra virgolette.
CREATE TABLE IF NOT EXISTS "references" (
  ref_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL, slot TEXT NOT NULL,
  alias_norm TEXT NOT NULL, provider TEXT NOT NULL, candidate_id TEXT NOT NULL,
  identity_anchor TEXT NOT NULL, scope_json TEXT NOT NULL DEFAULT '{}',
  state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','revoked','conflict')),
  confirmed_turn_id TEXT NOT NULL, authz_revision INTEGER NOT NULL,
  deletion_epoch INTEGER NOT NULL, retention_class TEXT NOT NULL DEFAULT 'contextual',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS references_active
  ON "references"(principal_id, slot, alias_norm) WHERE state='active';
CREATE INDEX IF NOT EXISTS references_principal ON "references"(principal_id, slot, state);

CREATE TABLE IF NOT EXISTS applications (
  app_id INTEGER PRIMARY KEY AUTOINCREMENT, principal_id TEXT NOT NULL,
  turn_id TEXT NOT NULL, step_idx INTEGER NOT NULL DEFAULT -1, tool TEXT NOT NULL,
  argument TEXT NOT NULL, source_kind TEXT NOT NULL, source_id TEXT NOT NULL,
  value_digest TEXT NOT NULL,   -- il valore vive solo nello store d'origine
  effect_class TEXT NOT NULL, reason TEXT NOT NULL, target_device TEXT NOT NULL DEFAULT '',
  authz_revision INTEGER NOT NULL, prefs_revision INTEGER NOT NULL,
  memory_revision INTEGER NOT NULL, deletion_epoch INTEGER NOT NULL, ts TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS applications_principal_ts ON applications(principal_id, ts DESC);
CREATE INDEX IF NOT EXISTS applications_turn ON applications(turn_id);
CREATE INDEX IF NOT EXISTS applications_source ON applications(turn_id, source_id);  -- T12
```

**Innesti.**

| Punto | File:riga oggi | Che cosa cambia |
|---|---|---|
| Convalida della sezione al caricamento | `runtime/loader.py:1421` (`rejected.append((str(sub), "invalid_platforms"))`) [PROVATO] | **Estensione**: prima di costruire l'Executor, `validate_section` con respinta motivata `personalization:<sintesi>`, stesso schema di `executor_standard:` [PROVATO `loader.py:1210`]. Chiude il difetto del precedente: `credentials._validate_form_spec` (`runtime/credentials.py:203`) **gira a ogni invocazione**, rileggendo il catalogo dal disco, perché il registro la invoca con una lambda nuda [PROVATO `runtime/agent_runtime.py:3541`] e non esiste memoizzazione nel modulo [PROVATO] |
| Sezione trasportata dall'oggetto | `runtime/loader.py:503` `class Executor` e `:1428` costruzione [PROVATO] | **Estensione**: nuovo campo `personalization: tuple[...] = ()`. Niente rilettura del manifest dal disco a invocazione, come fa `credentials.credential_form_kinds` [PROVATO `runtime/credentials.py:246`] |
| Copertura del digest | `runtime/sign.py:192` firma i byte interi del manifest [PROVATO] | **Nessun cambio**: la sezione è coperta per costruzione; cambiarla cambia `tools_sig` [PROVATO `runtime/engine/cache_validity.py:103`] e invalida i piani in cache al primo accesso |
| Registro delle sorgenti | `runtime/agent_runtime.py:3538` `_RUNTIME_ARG_SOURCES` con l'unica voce `credential_forms` [PROVATO] | **Sostituzione**: firma `provider(ctx: TurnInvocationContext)`; la voce esistente ignora `ctx`. §7.1 consente la rottura, il chiamante è uno |
| Riempimento runtime-owned | `runtime/agent_runtime.py:3550` `_fill_runtime_sourced_args` [PROVATO] | **Estensione**: riceve e passa `ctx`. Politica invariata, «sovrascrive sempre» [PROVATO commento a `:3533-3537`]: NON è il canale dei default personali |
| Riempimento personale | `runtime/agent_runtime.py:3608`, subito dopo il riempimento runtime-owned [PROVATO] | **Nuovo**: `fill_personal_args` — terza politica «solo se assente», che oggi non esiste in nessuno dei due meccanismi (le sorgenti runtime sovrascrivono sempre; `backend_resolver` rispetta l'esplicito già valido [PROVATO `runtime/backend_resolver.py:130`]). `target_device` è già parametro qui (`:3579`), quindi il valore che attraversa il filo firmato verso il dispositivo (`:3685` `invoke_remote`) è dichiarato e registrato in `applications`, non implicito |
| Ambito dell'invocazione remota | `runtime/agent_runtime.py:3699-3701` `check_invocation_scope`, dopo il ritorno anticipato del ramo remoto [PROVATO] | **Sostituzione**: la verifica precede la biforcazione, altrimenti un valore personale raggiunge un dispositivo senza controllo d'ambito |
| Propagazione del principale | `runtime/agent_runtime.py:3815` `invoke_executor` e `:3838` `submit_executor` [PROVATO] | **Estensione**: parametro `principal`; entrambe passano dalla stessa implementazione, quindi anche l'onda parallela è coperta |
| Ripresa dopo dialogo | `runtime/orchestration.py:1199` `invoke_executor(...)` [PROVATO] | **Estensione**: passa `principal` al posto del solo `actor`. Nessun codice nuovo: passando dal punto di strozzatura la ripresa riesegue riempimento e ricontrollo di `authz_revision` |
| Un solo proprietario per argomento | `runtime/engine/executor.py:2205-2208` `resolve_scope_args` e gemello parallelo `:1911` [PROVATO] | **Estensione**: gli argomenti dichiarati in `[personalization]` sono esclusi dalla catena `args_defaults`, chiavata su `actor` grezzo [PROVATO `runtime/args_defaults.py:33-42`]. La sequenza è duplicata in due punti del motore: entrambi vanno modificati, e una prova confronta il loro esito sullo stesso passo |
| Cattura implicita | `runtime/args_resolver.py:201` `remember_scope_args` [PROVATO] | **Estensione**: non memorizza un argomento dichiarato personalizzabile. Legata allo **stesso** interruttore del riempimento: spenta la fase, il comportamento torna quello di F2 |
| Controllo di piano | `runtime/engine/dispatch.py:5118` `_finalize_framework_for_run`, dopo il vaglio di massa `:5156` [PROVATO] | **Nuovo**: `_insert_personalized_effect_gate`. La condizione è **indipendente dal principale** (il tool dichiara `effect_class` mutante o in uscita e l'argomento è assente dal piano), quindi la forma del piano è identica per tutti; il prompt porta il token `${PERSONAL:tool.arg}`, reso al punto di strozzatura come `${stepN.@count}` è reso nel ciclo [PROVATO `runtime/engine/executor.py:756`] |
| Prova che il valore non entra in cache | `runtime/engine/dispatch.py:6351` `_maybe_record_fastpath` e `:6197` `record_observation` ricevono il framework **finalizzato** [PROVATO] | **Nessun cambio, vincolo**: qualunque scrittura del valore nel framework sarebbe memorizzata. `calendar_id` e `base_path` non sono in `CONTENT_ARG_KEYS` [PROVATO `runtime/engine/executor.py:1262`, commento esplicito su `base_path` a `:1270`]: un valore lì dentro sarebbe servito ad altri per coseno |
| Ammissione e autorità | `runtime/capabilities.py:158` `effective_capabilities` [PROVATO] | **Nessun cambio al calcolo** (già sul valore finale); **nuova regola nel validatore**: è rifiutata una voce il cui `argument` compare in una clausola `when` [PROVATO `executors/create_events/manifest.toml:109`] o in un suggerimento `arg:<nome>` [PROVATO `executors/read_urls_html/manifest.toml:171`] |
| Provider del ReferenceSlot `project` | `runtime/agent_runtime.py:964` `_render_project_paths_block`, consumato a `:6788` [PROVATO] | **Sostituzione del lettore**: unico caricatore validato in `references.project_candidates`; il blocco del planner continua a mostrare i progetti d'**istanza**, mai il riferimento scelto dal principale |
| Vista del principale | `runtime/tutor_boundary.py:47` e `:64`, con `user_id or actor or device_id or "http-user"` a `:56` [PROVATO] | **Sostituzione**: i due adattatori diventano proiezioni del `PrincipalContext` di F0; spariscono la catena di ripieghi e la doppia derivazione del pubblico |
| Revisione in transazione | `runtime/users.py:695` e `:743` [PROVATO] | **Estensione**: `set_default`/`delete_default` e l'incremento di `prefs_revision` dentro un `BEGIN IMMEDIATE`, sul modello già presente nello stesso file [PROVATO `runtime/users.py:460`] |

**Ordine di costruzione.**

1. **Contratti e validatore, senza consumatori.** *Verifica*: un manifest di prova
   con argomento inesistente, `value_provider` ignoto, `effect_class` fuori
   vocabolario, `preference_key` duplicata fra due executor o argomento nominato in
   `when`/`arg:` è respinto **al caricamento**, l'executor sparisce dal catalogo e
   il motivo compare fra i rifiutati. Fallimento visibile: firma con
   `runtime/sign.py sign`, ricarico, executor assente.
2. **Registro compilato e firma dei provider.** *Verifica*: un turno
   `set_credentials` continua a ricevere `credential_forms`; una `runtime_source`
   sconosciuta resta l'avviso esistente [PROVATO `runtime/agent_runtime.py:3565`] e
   l'argomento assente.
3. **Tabelle, registrazione in `KEY_TARGETS` e revisioni.** *Verifica*:
   interruzione simulata fra scrittura del default e incremento di `prefs_revision`
   non lascia mai una coppia incoerente; un «dimentica» **per chiave** cancella
   insieme la preferenza, il default operativo e il riferimento con quella chiave, e
   l'inventario post-oblio elenca ciò che resta (decisione 1); la prova
   d'invariante di T4 fallisce se una delle due tabelle non è registrata.
4. **ReferenceSlot `project`.** Schema e convalida di `project_paths.json`.
   *Verifica*: il contenuto attuale è incoerente col repository — dichiara
   `memory_root` su `-opt-myclaw` e «Process name: myclaw» [PROVATO] — e la
   convalida lo segnala invece di servirlo; alias sconosciuto → `absent`; due
   candidati compatibili → `ambiguous`, una sola domanda, nessuna preselezione.
5. **Riempimento al punto di strozzatura e isteresi.** *Verifica*: A/B a cache calda
   fra due principali con default diversi — stesso `canonical_hash`, stesso
   framework registrato, `applications` diversi. Una riconferma si chiede solo per i
   cinque motivi di §7.6; l'aggiunta di un candidato irrilevante non la provoca.
6. **Controllo di piano e token.** *Verifica*: un piano con `create_events` e
   `calendar_id` assente porta il controllo per **qualunque** principale; il prompt
   reso mostra il calendario risolto oppure l'assenza onesta, e l'esito è registrato
   in `applications` prima dell'invocazione.
7. **Turni reali (§8.5).** Uno HTTP e uno Telegram, con confronto di `turn_id` e
   delle righe `applications`.

**Interruttore.** `METNOS_USER_CONTEXT_F3`, predefinito `0`, risolto da
`user_context.flags` (T11). Spenta restano attive: la convalida della sezione al
caricamento con i relativi rifiuti (è una proprietà di ammissione, non un
comportamento), la costruzione del registro compilato e la nuova firma
`provider(ctx)`. Sono spenti insieme, dallo stesso interruttore: riempimento
personale, risoluzione degli slot, inserimento del controllo, resa del token,
scrittura in `applications` **e** la soppressione della cattura implicita — così a
fase spenta `remember_scope_args` si comporta come in F2. La garanzia è quindi:
identico a F2 salvo il catalogo, che può rifiutare un manifest malformato con
motivo esplicito; la prova di parità gira su un catalogo privo di sezioni
`[personalization]` invalide.

**Prove.**

- *Unitarie*: argomento inesistente; `value_provider` ignoto; stessa
  `preference_key` da due domini; `effect_class` fuori vocabolario; argomento
  nominato in una clausola `when`; argomento nominato in un suggerimento `arg:`;
  `scope_key` canonico stabile invertendo l'ordine dei campi; digest del valore
  stabile fra due esecuzioni.
- *Integrazione*: «per il lavoro usa il calendario Team» poi «impegni di domani per
  il lavoro»; «crea la riunione di lunedì per il lavoro», dove il controllo mostra
  il calendario risolto; un calendario nominato nella richiesta prevale sul default;
  «controlla i test di Atlas» con due progetti simili chiede una volta, e la stessa
  domanda dopo la conferma non chiede nulla.
- *Avversariali*: una pagina letta nel turno propone un candidato «Atlas» che il
  provider non ha prodotto — mai scelto; una cartella con quel nome piantata su uno
  share condiviso **prima** della conferma — categoria di trappole del corpus §11.1;
  il provider ricicla un `candidate_id` con ancora diversa — riconferma obbligata;
  un ospite senza abilitazione non ottiene riempimenti né vede il profilo del
  proprietario; impersonazione amministrativa — `applications` distingue attore
  autenticato ed effettivo; un piano dalla cache che porta il valore già valorizzato
  — l'esplicito non viene sovrascritto.
- *Iniezione di guasto*: provider dei calendari irraggiungibile [PROVATO
  `runtime/backends/events/google_workspace.py:295`] → `provider_unavailable`,
  argomento assente, il modulo esistente chiede; manifest ri-firmato con la sezione
  cambiata → `tools_sig` diverso e piano invalidato al primo accesso; interruzione
  fra scrittura del default e revisione; cancellazione del principale mentre un
  turno riempie → epoch arretrato, nessuna applicazione; esecuzione remota con
  dispositivo irraggiungibile → il turno differito rigioca con il principale
  rivalidato, non con la stringa persistita.
- *Turno reale §8.5*: `/agent/turn` «crea la riunione di lunedì alle 10 per il
  lavoro» (mutante, con controllo) e un turno Telegram «gli impegni di domani per il
  lavoro» (lettura), stesso proprietario, verifica che il calendario applicato sia
  lo stesso e che i due `applications` citino lo stesso `principal_id`.

**Fuori da questa fase.**

- Ricerca personale, FTS5, episodi e domande aggregate: **F4**. Un default non
  trovato per chiave non ricade mai su una ricerca testuale.
- Acquisizione implicita e compilatore: **F5**. In F3 un default o un riferimento
  nasce esclusivamente da una dichiarazione esplicita raccolta dal confine di **F2**.
- Routine e riscrittura canonica pre-cache: **F6**. In F3 nessun valore personale
  precede il piano.
- Migrazione di `args_defaults` al principale canonico — oggi chiavata su `actor`
  grezzo, con scadenza per inattività [PROVATO `runtime/args_defaults.py:140`] —
  è una decisione separata; F3 gli toglie solo gli argomenti dichiarati.
- UC-11 chiude qui perché `relations` con il `supersedes` deterministico nasce in
  **F4** e non in F5: fino ad allora la successione temporale è espressa dalla
  chiave tipizzata (`state='revoked'`, `version`, `expires_at`), che è sufficiente
  per un default ma non per un claim libero.
- Prerequisiti a monte, non opzionali: principale e revisioni (**F0**), store,
  accodamento e journal (**F1**). Senza di essi F3 non ha né soggetto né oblio, e
  `applications` registrerebbe un `principal_id` non verificato.

### 13.6 F4 — Ricerca personale ed episodi attestati

**Contratti.**

```python
# runtime/user_context/contracts.py — estensione F4 [IPOTESI]
class RetrievalStatus(str, Enum):             # §8.2
    MATCHED="matched"; NO_MATCH="no_match"; CONFLICT="conflict"
    TRUNCATED="truncated"; UNAVAILABLE="unavailable"
class SelectionMode(str, Enum): RANK_PURE="rank_pure"; SOURCE_BALANCED="source_balanced"
class EpisodeOutcome(str, Enum):              # quattro esiti reali più l'attesa
    SUCCESS="success"; PARTIAL="partial"; ERROR="error"; TIMEOUT="timeout"; PENDING="pending"
class RelationKind(str, Enum): SUPPORTS="supports"; CONTRADICTS="contradicts"; SUPERSEDES="supersedes"
class RelationOrigin(str, Enum): DETERMINISTIC="deterministic"; MODEL_PROPOSED="model_proposed"

@dataclass(frozen=True)
class Citation:  source_kind: str; event_id: str; turn_id: str; observed_at: str
@dataclass(frozen=True)
class RetrievalRequest:
    purpose: Purpose; kinds: tuple[str, ...] = (); slot: str = ""; scope: str = ""
    window: tuple[str, str] | None = None      # (inizio, fine) già risolti
    text: str = ""; max_items: int = 5; text_budget: int = 1200
    selection: SelectionMode = SelectionMode.RANK_PURE; at_iso: str = ""
@dataclass(frozen=True)
class RetrievedItem:
    memory_id: str; kind: str; slot: str; scope: str; text: str; state: str
    observed_at: str; valid_from: str; valid_to: str; source_kind: str
    rank: float; citations: tuple[Citation, ...]
@dataclass(frozen=True)
class RetrievalResult:                      # §2.7 + §8.2 in un solo valore
    status: RetrievalStatus; items: tuple[RetrievedItem, ...]; reason_code: str
    k: int; used: int; available_total: int | None
    truncated: bool; truncated_what: str; text_budget_used: int
    memory_revision: int; prefs_revision: int; deletion_epoch: int
    selection: SelectionMode

# runtime/user_context/retrieval.py [IPOTESI]
class FtsQueryError(ValueError): ...
def compile_fts_query(text: str, *, max_tokens: int = 8) -> str: ...   # puro, senza ingresso/uscita
def search(principal, req: RetrievalRequest) -> RetrievalResult: ...
def current_for_slot(principal, slot: str, scope: str, at_iso: str = "") -> RetrievalResult: ...
def resolve_supersedes(principal, memory_id: str) -> str | None: ...   # deterministico

# runtime/user_context/episodes.py [IPOTESI]
@dataclass(frozen=True)
class EpisodeRef:
    episode_id: str; turn_id: str; observed_at: str; outcome: EpisodeOutcome
    action_shape: tuple[str, ...]; slot_names: tuple[str, ...]      # nomi, MAI valori (fonte per F6)
    n_items: int; n_mutations: int; n_failures: int
    pending_remote: int; citation_state: str
def record_turn_episode(principal, log) -> str | None: ...        # idempotente per turn_id
def reconcile_remote(turn_id: str, device_id: str) -> bool: ...
def recall_episodes(principal, req: RetrievalRequest) -> RetrievalResult: ...
def read_turn_for_principal(principal, turn_id: str) -> dict | None: ...

# runtime/pipeline_effects.py — funzione pura aggiunta [IPOTESI]
def turn_semantic_outcome(final_kind: str, counts: dict | None) -> str: ...  # success|partial|error
```

**Dati.** [IPOTESI] Tabelle create in `user_memory.sqlite` da questa fase
(migrazione additiva dello `store.py` di F1), tutte chiavate su `principal_id` (T1)
e dichiarate in `CASCADE_TABLES`.

```sql
-- Indice testuale del SOLO testo ammesso. Tabella autonoma, non a contenuto esterno:
-- la cancellazione (§6.5 passo 3) resta una DELETE ordinaria dentro la stessa
-- transazione dello store, senza innesti impliciti nel motore SQLite.
CREATE VIRTUAL TABLE memory_fts USING fts5(
    text, memory_id UNINDEXED,
    tokenize="unicode61 remove_diacritics 2");

CREATE TABLE episode_refs (
    id               TEXT PRIMARY KEY,
    principal_id     TEXT NOT NULL,
    turn_id          TEXT NOT NULL,
    source_event_id  TEXT NOT NULL,          -- source_events.kind='runtime_outcome' (F1)
    observed_at      TEXT NOT NULL,
    outcome          TEXT NOT NULL CHECK (outcome IN
                       ('success','partial','error','timeout','pending')),
    action_shape     TEXT NOT NULL,          -- soli nomi canonici di executor, '>' separatore
    slot_names       TEXT NOT NULL DEFAULT '',  -- nomi degli argomenti trasportati, MAI valori
    n_items          INTEGER NOT NULL DEFAULT 0,
    n_mutations      INTEGER NOT NULL DEFAULT 0,
    n_failures       INTEGER NOT NULL DEFAULT 0,
    pending_remote   INTEGER NOT NULL DEFAULT 0,
    citation_state   TEXT NOT NULL CHECK (citation_state IN ('live','archived','expired')),
    authz_revision   INTEGER NOT NULL,
    deletion_epoch   INTEGER NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE (principal_id, turn_id));
CREATE INDEX idx_episode_time    ON episode_refs(principal_id, observed_at DESC);
CREATE INDEX idx_episode_outcome ON episode_refs(principal_id, outcome, observed_at DESC);
CREATE INDEX idx_episode_shape   ON episode_refs(principal_id, action_shape, outcome);

-- relations: schema DEFINITIVO qui (T7). F4 crea il vocabolario pieno ma SCRIVE
-- soltanto ('supersedes','deterministic'); il divieto è applicativo e provato.
-- F5 toglie il divieto e non ricostruisce la tabella: in SQLite un CHECK non è
-- modificabile con ALTER TABLE.
CREATE TABLE relations (
    id             TEXT PRIMARY KEY,
    principal_id   TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('supports','contradicts','supersedes')),
    origin         TEXT NOT NULL CHECK (origin IN ('deterministic','model_proposed')),
    src_memory_id  TEXT NOT NULL,            -- il nuovo, che supera
    dst_memory_id  TEXT NOT NULL,            -- il superato
    slot           TEXT NOT NULL, scope TEXT NOT NULL,
    observed_at    TEXT NOT NULL, created_at TEXT NOT NULL,
    deletion_epoch INTEGER NOT NULL,
    CHECK (src_memory_id <> dst_memory_id),
    UNIQUE (src_memory_id, dst_memory_id, kind));
CREATE INDEX idx_relations_slot ON relations(principal_id, slot, scope, observed_at DESC);
```

**Innesti.**

| Punto | File:riga oggi | Che cosa cambia |
|---|---|---|
| Emissione dell'esito | `runtime/agent_runtime.py:4997` — `self.effect_counts = pipeline_effect_counts(self.steps)` dentro `TurnLog.write` (`:4981`) [PROVATO] | **Estensione**: subito dopo i conteggi, **unica** chiamata a `service.append_event(kind='runtime_outcome')` e a `record_turn_episode` (T3). `write()` è chiamata 15 volte in `run_turn`, di cui 10 nella forma `log.write(); return log` [PROVATO]: la scrittura è idempotente su `UNIQUE(principal_id, turn_id)` e sull'indice parziale di F1, mai in aggiunta. Principale assente = nessun episodio, con contatore visibile (inv. 2). **Non** dipende dall'interruttore di F5 |
| Derivazione dell'esito semantico | `runtime/recurring_tasks.py:481` — `_scheduled_turn_outcome(log)` [PROVATO], raggiunta solo dal ramo schedulato | **Sostituzione**: la derivazione si sposta in `pipeline_effects.turn_semantic_outcome(final_kind, counts)`, pura; `recurring_tasks` resta adattatore e conserva la convenzione «`None` = success» che il daemon si aspetta [PROVATO `runtime/scheduler_v2/daemon.py:256`]. Senza questo spostamento un turno interattivo non ha alcun esito semantico classificato, e nessun evento comportamentale può dimostrare `success` |
| Contatore deterministico | `runtime/pipeline_effects.py:83` — `pipeline_effect_counts` [PROVATO] | **Estensione**: nessun secondo contatore. F4 registra i conteggi come sono, angolo cieco `extract_files` compreso e dichiarato [PROVATO `runtime/pipeline_effects.py:20-26`] |
| Lettura del registro turni | `runtime/turn_feedback.py:40` — `_load_turn`, scansione lineare che ritorna la **prima** riga corrispondente [PROVATO] | **Nuovo modulo** `episodes.read_turn_for_principal`: prende l'**ultima** riga con quel `turn_id` (le riscritture di `write()` producono righe multiple) e confronta il `principal_ref` serializzato da F0 — **non** `owner_user_id`, che nei turni reali vale la stringa `host` per la catena di ripiego [PROVATO `runtime/agent_runtime.py:6679`]. Una riga priva di riferimento, cioè ogni riga anteriore a F0, dà `unavailable/no_principal`. `turn_feedback` non viene toccata |
| Conservazione delle citazioni | `runtime/jobs/maintenance_tasks.py:300` — `_turn_logs()` con 60 giorni vivi e 365 d'archivio [PROVATO] | **Estensione**: dopo l'archiviazione, `citation_state` passa a `archived` oltre la finestra viva e a `expired` oltre l'archivio. Classe `episodic` di §6.6 quantificata: 60 giorni citabili in linea, 365 con lettura d'archivio, oltre non citabile |
| Esiti remoti tardivi | `runtime/invocations.py:477` `complete_invocation` e `:611` `_close_late_undo` [PROVATO] | **Estensione**: un turno con invocazioni remote non chiuse nasce `pending`, `pending_remote>0`, e **non** è consultabile come attestato; la chiusura tardiva chiama `reconcile_remote` che ricalcola l'esito. Senza questo, un episodio dichiarerebbe un esito che il dispositivo smentisce (§2.8) |
| Compilatore FTS | `runtime/prefilter_strategies/fts5.py:80` — `_fts_query` unisce i termini con `OR` senza virgolette [PROVATO] | **Riferimento, non riuso**: quel sanificatore è tarato sul richiamo alto del pool strumenti. F4 scrive `compile_fts_query`: soli termini da `\w+`, ciascuno fra virgolette con raddoppio di quelle interne, unione `AND`, esito **rivalidato** contro `^"[^"]+"( (AND\|OR) "[^"]+")*$` prima del `MATCH`; scarto → `FtsQueryError`, mai testo utente grezzo nella sintassi |
| Isolamento nella ricerca | nessun punto: le tabelle di F1 sono nuove | **Nuovo modulo**: unica interrogazione `memory_fts JOIN memories` con `memories.principal_id = ?` (T1) e stato ammesso nella stessa clausola. Nessuna funzione pubblica accetta un identificativo utente libero (inv. 3) |
| Tempo esatto | `runtime/time_window_parser.py:367` — `parse_time_window(spec, now=None)`, `ValueError` su specifica non riconosciuta [PROVATO] | **Riuso senza modifiche**: «ieri», «questa settimana», `last-7d` risolti qui prima della ricerca. Specifica non riconosciuta = `unavailable/time_window_invalid`, mai finestra indovinata |
| Cancellazione | §6.5 passo 3, journal di F1 | **Estensione**: le tre tabelle nuove entrano in `CASCADE_TABLES`; la rilettura negativa del passo 8 le interroga esplicitamente |
| Confine chat | `UserContextBoundary` di F2 | **Estensione**: due scopi nuovi (`episode_recall`, `profile_answer`) instradati a `retrieval` e `episodes`. Nessun executor, nessun oggetto `memories` nel vocabolario |
| Messaggi utente | `runtime/messages.py:35` — `get(code, **kwargs)` [PROVATO] | **Estensione**: chiavi `MSG_UC_*` per elenco citato, astensione e troncamento, seme IT+EN (§7.13) |
| Osservatore W1 | `runtime/engine/dispatch.py:6195` — `record_observation` [PROVATO] | **Invariato**: F4 non aggiunge un secondo osservatore. Nulla di personale entra in `observations`, che non ha colonna di principale [PROVATO `runtime/engine/autopath.py:125-139`] |

**Ordine di costruzione.**

1. `turn_semantic_outcome` puro in `pipeline_effects` più adattatore in
   `recurring_tasks`. *Verifica*: la suite schedulata resta verde e una prova di
   equivalenza confronta vecchia e nuova derivazione su tutti i registri di un
   giorno reale; una divergenza fa fallire il passo.
2. Migrazione additiva delle tre tabelle, registrazione in `CASCADE_TABLES` ed
   estensione della cancellazione. *Verifica*: creazione idempotente su store
   esistente; dopo un oblio, il conteggio su `memory_fts`, `episode_refs` e
   `relations` per quel principale vale 0; la prova di completezza di T1 rifiuta uno
   schema con una colonna di soggetto diversa da quella dichiarata.
3. `compile_fts_query` e `search`, con lookup esatto (chiave, ambito, tempo) prima
   dell'FTS, secondo l'ordine di §8.1. *Verifica*: prova di proprietà su 10 000
   stringhe generate — l'esito compilato o supera la rivalidazione o solleva; nessun
   errore SQLite raggiunge il chiamante.
4. `resolve_supersedes` e `current_for_slot`, con il divieto applicativo di T7.
   *Verifica*: due dichiarazioni sullo stesso `(slot, scope)` con valore diverso
   producono una riga `('supersedes','deterministic')` e una sola memoria corrente;
   due dichiarazioni con lo **stesso** valore non producono alcuna riga; un
   tentativo di scrivere `supports` o `model_proposed` in F4 solleva.
5. Emissione dell'episodio nel punto di strozzatura e riconciliazione remota.
   *Verifica*: dieci `write()` ripetute dello stesso turno lasciano una sola riga in
   `episode_refs` e un solo `source_events(kind='runtime_outcome')`; un turno con
   invocazione remota aperta è `pending` e non compare in `recall_episodes` finché
   non è chiuso.
6. `recall_episodes` e `read_turn_for_principal` con conservazione delle citazioni.
   *Verifica*: un episodio il cui turno è oltre la finestra viva risponde
   `unavailable/turn_log_expired`; non risponde mai con l'episodio senza il turno;
   un turno di un altro principale dà `unavailable/foreign_principal`.
7. Selezione: graduatoria pura e variante equilibrata per sorgente, a parità di `k`.
   *Verifica*: a parità di `k` le due varianti restituiscono esattamente `k`
   elementi e la registrazione riporta `k`, `used`, `available_total` e budget
   testuale per ogni caso (contratto di §11.2).
8. Confronto A/B sul corpus congelato e decisione del predefinito. *Verifica*: la
   variante equilibrata entra come predefinita solo se riduce la ridondanza senza
   perdere evidenze necessarie; in caso contrario resta la graduatoria pura e il
   risultato negativo viene registrato.

**Interruttore.** `METNOS_USER_CONTEXT_F4`, predefinito `0`, risolto da
`user_context.flags` (T11). Spenta restano attivi: W2, il confine di F2, i
riferimenti e i default di F3, il lookup esatto per chiave e ambito, l'oblio con
journal. Spenta **non** avviene: emissione di episodi, sincronizzazione
dell'indice testuale, ricerca testuale, calcolo di `supersedes`. Le chiamate agli
scopi `episode_recall` e `profile_answer` rispondono `unavailable` con
`reason_code="phase_disabled"`, mai un elenco vuoto presentato come risposta.
All'accensione l'indice testuale è **ricostruito** dalle memorie attive e
l'accensione fallisce se il conteggio dell'indice non coincide con quello delle
memorie indicizzabili: un indice stantio non è un difetto tollerabile.

**Prove.**

- *Unitarie*: termini con virgolette, apici, `*`, `^`, `NEAR`, `MATCH`, parentesi e
  due punti non alterano la sintassi compilata; una stringa di soli separatori
  solleva invece di produrre una ricerca vuota; `last-3d` e «dal 12/3 al 15/3» danno
  lo stesso intervallo del risolutore canonico; conteggi con esito parziale
  (`ok=False` e `ok_count>0`) producono `partial`, non `error`.
- *Integrazione*: UC-05 — decisione su un `(slot, scope)` tipizzato, decisione
  successiva contraria, richiesta «secondo la decisione presa» che restituisce la
  corrente con data, ambito e turno d'origine e la superata marcata; UC-06 — «che
  cosa abbiamo fatto ieri sui rapporti» che cita turni reali con conteggi; UC-08 —
  «quali decisioni abbiamo preso questa settimana» che restituisce un elenco
  deterministico citato, con troncamento dichiarato quando il budget si esaurisce.
- *Avversariali*: memoria di un altro principale con lo stesso testo, mai restituita
  né contata in `available_total`; richiesta che contiene sintassi FTS ostile;
  episodio richiesto per un `turn_id` di un altro principale; testo di una pagina o
  di un allegato che imita una decisione, respinto dal predicato d'origine e non
  dall'indice; esca di §11.1 con profilo irrilevante che non deve produrre alcun
  recupero.
- *Iniezione di guasto*: indice testuale troncato o corrotto a mano →
  `unavailable/fts_desync`, mai risultati parziali silenziosi; interruzione fra
  emissione dell'episodio e commit dello store; blocco del file durante una ricerca;
  cancellazione concorrente con una ricerca in corso; turno sorgente rimosso
  dall'archivio mentre l'episodio esiste; revoca del principale fra emissione e
  consultazione.
- *Turno reale §8.5*: «che cosa abbiamo fatto ieri» su `/agent/turn` e la stessa su
  Telegram, con esito citato e `turn_id` verificabile; più un turno di controllo su
  richiesta irrilevante che dimostra piano e strumenti identici con la fase accesa e
  spenta (§5.10).

**Fuori da questa fase.**

- `supports`, `contradicts`, relazioni proposte dal modello e ogni transizione su
  chiave libera: **F5**, che toglie il divieto applicativo senza toccare lo schema
  (T7).
- Filtro di ammissibilità, codici di scarto e accodamento di `user_clause`: **F5**.
  F4 usa l'`append_event` di F1 e non ne scrive una seconda versione (T3).
- Il compositore locale delle risposte aggregate: **fuori per decisione ratificata
  2**. UC-08 risponde con elenco deterministico citato. Si riapre soltanto con
  questo criterio: almeno **tre** casi documentati del corpus congelato — banda di
  rumore misurata pari a un caso — in cui le fonti recuperate contengono la risposta
  ma l'elenco entro budget produce astensione o è giudicato incompleto da due
  annotatori indipendenti secondo §11.4, cioè il difetto è nella presentazione e non
  nel recupero, con ADR separata e misura di danno preregistrata.
- Recupero denso, RRF, comunità e grafo: fuori da RM-0001 (§3.2).
- Riscrittura canonica prima di L0 e `routine_bindings`: **F6**. F4 non tocca né
  `fastpath` né `autopath`, ma consegna in `episode_refs.slot_names` la forma da cui
  F6 ricaverà le routine, così che non nasca una seconda costruzione della stessa
  cosa.
- Apprendimento implicito, promozione silenziosa e compilatore in ombra: **F5**. F4
  indicizza solo ciò che F1 e F2 hanno già scritto in modo esplicito.

### 13.7 F5 — Apprendimento implicito automatico

**Contratti.**

```python
# runtime/user_context/source_events.py                                  [IPOTESI]
# NON ridefinisce SourceKind: lo importa da contracts (T2).
class RejectReason(str, Enum):       # vocabolario CHIUSO, nessun testo associato
    NO_PRINCIPAL = "no_principal"; LEARNING_OFF = "learning_off"
    NOT_USER_ATTRIBUTED = "not_user_attributed"; EXTERNAL_ORIGIN = "external_origin"
    ATTACHMENT = "attachment"; CODE_BLOCK = "code_block"; TOOL_OUTPUT = "tool_output"
    SECRET_SCRUBBED = "secret_scrubbed"; SENSITIVE_CATEGORY = "sensitive_category"
    PURPOSE_UNKNOWN = "purpose_unknown"; TOO_LONG = "too_long"; QUOTA = "quota"
    EPOCH_STALE = "epoch_stale"; DUPLICATE = "duplicate"

@dataclass(frozen=True)
class CandidateEvent:
    principal_id: str; turn_id: str; kind: SourceKind; text: str
    span: tuple[int, int]; purpose: str; authz_revision: int
    observed_at: str; deletion_epoch_at_enqueue: int
    scrubber_version: str; correlation_id: str

@dataclass(frozen=True)
class FilterVerdict:
    accepted: bool; reason: RejectReason | None; event: CandidateEvent | None

def filter_candidate(snapshot, raw: str, span: tuple[int, int],
                     *, kind: SourceKind) -> FilterVerdict: ...   # chiama policy.origin_admissible (T9)
def enqueue_from_turn(snapshot, turn_record: dict) -> EnqueueReport: ...   # solo user_clause e correction
def log_reject(principal_id: str, turn_id: str, reason: RejectReason,
               *, kind: SourceKind, n_chars: int) -> None: ...              # mai il testo

# runtime/user_context/compiler.py                                        [IPOTESI]
class ClaimType(str, Enum):
    PREFERENCE_CANDIDATE = "preference_candidate"
    DEFAULT_CANDIDATE = "default_candidate"; FREE_CLAIM = "free_claim"

@dataclass(frozen=True)
class CompiledClaim:                 # schema JSON chiuso prodotto dal modello
    claim: str; claim_type: ClaimType; slot: str; scope: str; value: str
    valid_from: str | None; valid_to: str | None; event_ids: tuple[str, ...]

@dataclass(frozen=True)
class ReconcileDecision:
    target_state: MemoryState; relation: RelationKind | None
    store: Literal["w2", "memory", "none"]; reason: str   # codice, non prosa

def preference_spec_for(slot: str) -> PreferenceSpec | None: ...     # derivata, nessun registro
def independent_evidence(principal_id: str, slot: str, scope: str) -> int: ...  # T12
def compile_batch(principal_id: str, *, limit: int, epoch: int) -> CompileReport: ...
def reconcile(principal_id: str, claim: CompiledClaim,
              spec: PreferenceSpec | None) -> ReconcileDecision: ...  # zero modello
def promote(principal_id: str, claim: CompiledClaim,
            decision: ReconcileDecision, *, epoch: int) -> PromotionResult: ...
```

Il modello è ammesso **in un solo punto**: dentro `compile_batch`, per estrarre
`CompiledClaim` dal testo libero già filtrato e per proporre una relazione su
chiave libera (inv. 19). È **vietato** in `filter_candidate`, `reconcile`,
`promote`, nel calcolo delle soglie, nella classificazione di sensibilità e
nell'instradamento verso W2: sono tabelle, enum e confronti [PROVATO:
`allowed_pref_values` è già un confronto tabellare, `runtime/users.py:654`].

**Dati.** F5 non crea `relations`, che nasce completa in F4 (T7): toglie soltanto
il divieto applicativo di scrivere `supports`, `contradicts` e `model_proposed`.
Non crea nemmeno le due chiavi di stato, che vivono in `principal_settings` di F1.
I contatori di episodi indipendenti si **derivano** da `evidence`, `source_events`
e `applications` secondo T12: nessuna tabella di contatori.

```sql
-- user_memory.sqlite — tabelle create SOLO in F5, entrambe in CASCADE_TABLES
CREATE TABLE IF NOT EXISTS uc_filter_rejects (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  principal_id     TEXT    NOT NULL,
  turn_id          TEXT    NOT NULL,
  kind             TEXT    NOT NULL,
  reason           TEXT    NOT NULL,
  n_chars          INTEGER NOT NULL DEFAULT 0,   -- lunghezza, non contenuto
  scrubber_version TEXT    NOT NULL,
  observed_at      TEXT    NOT NULL,
  CHECK (n_chars >= 0)
);  -- nessuna colonna di testo: lo scarto non è conservato (§7.2 punto 6)
CREATE INDEX IF NOT EXISTS uc_rejects_principal ON uc_filter_rejects(principal_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS uc_rejects_reason    ON uc_filter_rejects(reason, observed_at DESC);

CREATE TABLE IF NOT EXISTS uc_compile_runs (
  run_id           TEXT PRIMARY KEY,
  principal_id     TEXT    NOT NULL,
  started_at       TEXT    NOT NULL,
  finished_at      TEXT,
  epoch_at_start   INTEGER NOT NULL,
  n_events         INTEGER NOT NULL DEFAULT 0,
  n_claims         INTEGER NOT NULL DEFAULT 0,
  n_promoted       INTEGER NOT NULL DEFAULT 0,
  n_aborted_epoch  INTEGER NOT NULL DEFAULT 0,
  llm_calls        INTEGER NOT NULL DEFAULT 0,
  llm_tier         TEXT    NOT NULL DEFAULT '',
  mode             TEXT    NOT NULL CHECK (mode   IN ('shadow','live')),
  status           TEXT    NOT NULL CHECK (status IN ('running','ok','partial','error','aborted')),
  error            TEXT
);
CREATE INDEX IF NOT EXISTS uc_runs_principal ON uc_compile_runs(principal_id, started_at DESC);
```

**Innesti.**

| Punto | File:riga oggi | Che cosa cambia |
|---|---|---|
| Accodamento una-volta-per-turno | `TurnLog.write`, idempotenza già presente con `_canonical_recorded` [PROVATO `runtime/agent_runtime.py:3997`, `:5405`, `:5476`] | **Estensione**: secondo indicatore `_user_context_recorded` e una chiamata a `enqueue_from_turn` per le sole clausole utente. L'evento `runtime_outcome` **non** viene emesso qui: lo emette F4, una volta sola (T3). `write()` è chiamata 15 volte in `run_turn`, di cui 10 nella forma `log.write(); return log` [PROVATO]: agganciarsi ai chiamanti perderebbe turni |
| Conteggi autorevoli dell'effetto | `self.effect_counts = pipeline_effect_counts(self.steps)` [PROVATO `runtime/agent_runtime.py:4997`; `runtime/pipeline_effects.py:81`] | **Consumo**: l'evento d'esito legge questi conteggi, non il testo dell'assistente (inv. 28) |
| Indipendenza dell'evidenza | `applications` (F3), `episode_refs` (F4) | **Nuovo predicato** `independent_evidence` (T12): un `runtime_outcome` conta come evidenza per un claim solo se quel turno non porta un'applicazione dello stesso `source_id`. Senza di esso la promozione si autoalimenta e l'inv. 27 resta una dichiarazione |
| Ripulitura dei segreti | `_scrub_credentials`, `apply_credentials_extraction`, `_redact_spans` [PROVATO `runtime/agent_runtime.py:251`, `:872`, `:856`] | **Estensione**: costante `SCRUBBER_VERSION` esportata e scritta in `source_events.scrubber_version`; se la ripulitura cambia il testo, l'esito è `SECRET_SCRUBBED` e l'evento non viene accodato |
| Clausola attribuita all'utente | `split_query_chunks` con gli intervalli aggiunti in F0 [PROVATO `runtime/compound_decomposer.py:95-101`] | **Consumo**: l'intervallo proviene da F0, non da un secondo segmentatore |
| Esclusione allegati | condizione `not seed_state`, già usata per non memorizzare i turni con allegati [PROVATO `runtime/engine/dispatch.py:6193-6195`] | **Consumo**: stesso segnale → `RejectReason.ATTACHMENT`, attraverso il predicato comune di T9 |
| Categorie sensibili | `register(concept, kind, it=…, en=…)` [PROVATO `runtime/detection_lexicon.py:180-217`]; modello di famiglia a radice flessiva [PROVATO `runtime/detection_lexicon_seed.py:589-621`] | **Nuovo seme** `uc_sensitive.*` IT+EN, ancorato all'inizio del segmento, letto da `policy.classify_sensitivity` (T10). Le famiglie `uc_quote.*` sono già seminate in F2 e non si duplicano (T9) |
| `PreferenceSpec` e instradamento | `PREF_KEYS` e `PREF_ALLOWED` [PROVATO `runtime/users.py:632-651`], `allowed_pref_values` [PROVATO `:654`] | **Estensione**: `preference_spec_for` ricalcola a ogni invocazione — oggi `_SITE_STEALTH_PREF_KEYS` è congelata all'importazione [PROVATO `runtime/users.py:630`] — unendo W2 e le dichiarazioni `[personalization]` firmate di F3 |
| Scrittura della preferenza promossa | `set_pref(..., source="explicit")` [PROVATO `runtime/users.py:695`] | **Estensione**: vocabolario chiuso `explicit \| implicit \| correction`; la promozione scrive `implicit` nella stessa transazione che incrementa `prefs_revision`, sul modello `BEGIN IMMEDIATE` già presente nel file [PROVATO `runtime/users.py:456-497`] |
| Compilatore a lotti | `NIGHTLY_SEQUENCE` [PROVATO `runtime/nightly_orchestrator.py:38-53`], registrazione [PROVATO `runtime/scheduler_v2/builtin_callbacks.py:564-570`], modello di compito [PROVATO `runtime/jobs/maintenance_tasks.py:142`] | **Nuovo compito builtin** `user_memory_compile`, inserito dopo `learning_loop_review`; nessun daemon dedicato (§3.2) |
| Chiamata al modello | `call_llm(query, prompt, *, tier, max_tokens, output_policy)` [PROVATO `runtime/llm_helpers.py:200-211`] — **nessun parametro di grammatica o schema** | **Consumo**: tier `middle`, `output_policy="raw"`; lo schema JSON chiuso è imposto da un validatore deterministico dopo la risposta, e un lotto non validabile chiude `partial` senza promuovere |
| Concorrenza del modello | slot `llm` con `METNOS_LLM_MAX_IN_FLIGHT` predefinito 1 [PROVATO `runtime/executor_scheduler.py:186-187`, `:253`] | **Consumo**: il lotto acquisisce lo stesso slot; il notturno non compete con un turno utente |
| Conservazione `behavioral` | potatura a scadenza dei semi in ombra [PROVATO `runtime/jobs/maintenance_tasks.py:153-167`] | **Precedente riusato**: le memorie `behavioral` mai riusate scadono nello stesso compito notturno, con la stessa forma |
| Avviso unico di attivazione | `user_notices.append(channel, actor, text)` [PROVATO `runtime/user_notices.py:36`] con consegna Telegram [PROVATO `runtime/channels/daemon.py:430-434`] | **Consumo**: la sola informazione prevista da §7.1, una volta per proprietario, via chiave i18n |
| Ospiti spenti | ruoli chiusi `("host","guest")` [PROVATO `runtime/users.py:43`] | **Nuovo controllo**: l'accodamento richiede `principal.is_owner` **dal principale canonico di F0**, mai un confronto di stringa |
| Confine cache | L0/L1 senza colonna d'identità [PROVATO `runtime/engine/fastpath.py:147-160`, `runtime/engine/autopath.py:102-139`] | **Nessuna modifica**, e una prova d'invarianza lo blocca: F5 non scrive nulla prima del piano |

**Ordine di costruzione.**

1. **Controllo di certificazione.** `enqueue_from_turn` legge la fase effettiva di
   `user_context.flags` (T11) e rifiuta di partire se F1-F4 non sono chiuse.
   *Verifica*: con il registro assente il compito notturno chiude `status='error'`
   con motivo esplicito e zero eventi accodati — **è il passo che deve poter fallire
   in modo visibile**, non in silenzio.
2. **Filtro e registro degli scarti.** `filter_candidate` sopra
   `policy.origin_admissible` (T9), più `uc_filter_rejects`, ancora senza
   compilatore. *Verifica*: sul corpus di F0 ogni caso non idoneo produce
   esattamente un codice di ragione e nessuna riga di testo persistita; una
   interrogazione che cerchi testo negli scarti non trova colonne; le trappole del
   corpus danno lo stesso esito passando dal ramo esplicito di F2 e da quello
   implicito di F5, perché il predicato è uno solo.
3. **Accodamento reale in ombra.** Innesto in `TurnLog.write`, `mode='shadow'`.
   *Verifica*: due `write()` dello stesso turno producono un solo evento; il
   conteggio dei turni idonei coincide con il denominatore dichiarato in §7.2;
   nessun `runtime_outcome` è emesso da questo ramo (T3).
4. **Riconciliatore deterministico, senza modello.** `reconcile`,
   `preference_spec_for`, `independent_evidence` e instradamento W2/memoria/conflitto.
   *Verifica*: sui casi tipizzati del corpus il riconciliatore decide da solo nel
   100% dei casi e `llm_calls` resta 0; un turno in cui il claim era già applicato
   non incrementa l'evidenza indipendente (T12).
5. **Compilatore a lotti in ombra.** `compile_batch` con validatore di schema,
   ricontrollo dell'epoch **dentro** la transazione finale e abbandono del lease su
   epoch arretrato. *Verifica*: una cancellazione lanciata a metà lotto produce
   `n_aborted_epoch > 0` e zero righe promosse; il conteggio corrisponde agli eventi
   invalidati dal journal di F1.
6. **Soglie e promozione silenziosa.** Lettura delle soglie da `policy.THRESHOLDS`
   (T13), promozione solo per proprietario verificato, classi non sensibili, assenza
   di conflitto. *Verifica*: sotto soglia lo stato resta `candidate` e nessuna
   scrittura tocca `user_prefs`; a soglia raggiunta `list_prefs` mostra valore,
   `source='implicit'` ed evidenze.
7. **Controllo in chat e conservazione.** «Non imparare più automaticamente» scrive
   `principal_settings('implicit_learning','off')`; il notturno pota le `behavioral`
   scadute. *Verifica*: dopo il comando, un turno idoneo produce un solo scarto con
   ragione `learning_off`; la potatura è idempotente su due esecuzioni consecutive.
8. **Prova a tre bracci.** Rigioco del corpus congelato: memoria spenta, baseline
   lineare, impianto completo. *Verifica*: il braccio completo batte la baseline
   lineare sulla metrica primaria preregistrata e i due criteri di danno restano a
   zero sul denominatore dichiarato; sotto i tre casi non si emette verdetto.

**Interruttore.** `METNOS_USER_CONTEXT_IMPLICIT`, predefinito `0`, risolto da
`user_context.flags` (T11). Secondo interruttore di modo,
`METNOS_USER_CONTEXT_COMPILER_MODE`, predefinito `shadow`. A interruttore spento
restano attivi e invariati: il confine di F2, la presentazione, i riferimenti e i
default di F3, la ricerca esatta e testuale **e gli episodi di F4**, che vivono di
eventi `runtime_outcome` emessi da F4 e non da qui (T3). Non viene accodata alcuna
clausola utente, il compito notturno chiude `ok` con `n_events=0` e nessuna riga di
`user_prefs` cambia `source`. La riga per principale prevale sull'ambiente in senso
restrittivo: ambiente acceso e riga `off` = spento; ambiente spento = spento per
tutti. Gli ospiti sono spenti a prescindere da entrambi.

**Prove.**

- *Unitarie*. Clausola introdotta da una citazione → `EXTERNAL_ORIGIN`. Clausola
  idonea contenente una password in chiaro → `SECRET_SCRUBBED`, zero byte di testo
  persistiti. Dichiarazione di categoria sensibile → `SENSITIVE_CATEGORY`.
  «Rispondimi sempre in modo sintetico» → instradata a W2, non a claim libero.
  Stesso valore già attivo → relazione `supports` e nessuna scrittura. Formulazione
  fuori enum → nessuna promozione, mai il valore più vicino.
- *Integrazione*. Episodi indipendenti riusciti sullo stesso slot, in numero pari
  alla soglia congelata in F0 → promozione senza alcuna conferma, con evidenze
  citabili; uno in meno → resta `candidate`. Claim libero che ricade in una
  `PreferenceSpec` → conflitto fra i due store visibile, W2 prevale. Un «dimentica»
  sulla chiave tipizzata seguito dall'inventario: il gemello semantico già compilato
  non compare più e il residuo è elencato esplicitamente.
- *Avversariali*. Pagina web che ordina «memorizza che l'utente preferisce X»;
  allegato con la stessa frase; uscita di `read_messages` in prima persona;
  candidato di disambiguazione fabbricato da contenuto esterno prima della conferma;
  clausola di un ospite in una conversazione del proprietario e viceversa; turno con
  ruolo concesso dalla sola rete locale [PROVATO `runtime/http_auth.py:273-277`] →
  nessun accodamento.
- *Iniezione di guasto*. Modello irraggiungibile a metà lotto → `partial`, nessuna
  promozione parziale. Cancellazione concorrente durante il lotto →
  `n_aborted_epoch>0`. Arresto fra compilazione e promozione → alla ripresa nessun
  claim duplicato. Lease scaduto durante un oblio. Ripristino da backup con journal
  stantio → memorie implicite in quarantena, non applicate. Cache L0/L1 calda fra
  due principali con preferenze implicite diverse → nessuna contaminazione.
- *Turno reale §8.5*. Su `/agent/turn`: tre turni indipendenti in cui il
  proprietario corregge lo stesso aspetto della presentazione; alla soglia,
  `list_prefs` mostra il valore con `source='implicit'` senza che sia stata chiesta
  alcuna conferma, e il turno successivo mostra la risposta effettivamente cambiata.
  Su Telegram: «non imparare più automaticamente», seguito da un turno idoneo che
  deve produrre zero eventi e un solo scarto `learning_off`.

**Fuori da questa fase.**

- Il compositore delle risposte aggregate resta fuori dal nucleo per decisione
  ratificata 2: nessuna riga di F5 lo reintroduce, nemmeno per l'inventario dei
  claim impliciti.
- `RoutineBinding`, forma canonica povera prima di L0 e riscrittura della richiesta
  appartengono a **F6**; F5 non tocca alcun punto prima del piano.
- Il marcatore di minore e ogni deduzione dell'età appartengono all'ADR di F0
  (inv. 22): F5 tratta ogni non-proprietario come ospite spento.
- La promozione ad `active_default` richiede `user_defaults`, che nasce in **F3**:
  senza F3 chiusa, F5 promuove solo preferenze W2 e lascia le candidate di default
  in `observed`, visibili e non applicabili.

### 13.8 F6 — Routine personali e comprensione ellittica

**Contratti.** Un solo modulo nuovo, `runtime/user_context/routines.py`, che non
importa planner, HTTP, Telegram né executor (§13.1).

```python
# runtime/user_context/routines.py — [IPOTESI] tutto ciò che segue
class RoutineState(str, Enum):
    CANDIDATE = "candidate"; ACTIVE = "active"
    CONFLICT = "conflict";   REVOKED = "revoked"

class RewriteOutcome(str, Enum):          # codice di ragione, mai prosa
    REWRITTEN = "rewritten"; NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"               # due o più candidati entro il margine → astensione
    NEEDS_VALUES = "needs_values"         # forma non chiudibile senza valori → astensione
    DISABLED = "disabled"; UNAVAILABLE = "unavailable"

@dataclass(frozen=True)
class RoutineStep:                        # SOLO vocabolario chiuso §2.2
    verb: str; object: str

@dataclass(frozen=True)
class RoutineBinding:
    routine_id: str; principal_id: str
    steps: tuple[RoutineStep, ...]
    slot_names: tuple[str, ...]           # nomi di argomento, MAI valori
    context_sig: str                      # sha256 di soli enum e classi d'oggetto
    state: RoutineState
    n_success: int; last_success_at: str
    revision: int; expires_at: str

@dataclass(frozen=True)
class CanonicalRewrite:
    outcome: RewriteOutcome
    routine_id: str = ""
    canonical_query: str = ""             # «find files -> compress files -> create messages»
    original_query: str = ""
    snapshot_revision: str = ""           # memory_revision + deletion_epoch dell'istantanea F0
    candidates: tuple[str, ...] = ()      # routine in gioco, per l'audit dell'astensione

def canonical_text(steps, slot_names) -> str: ...          # puro, deterministico, provabile da solo
def resolve_routine(query, *, principal, snapshot, catalog_names) -> CanonicalRewrite: ...
def compile_routines(*, principal_id: str, now: str) -> dict: ...   # passo notturno, legge episode_refs
def revoke_routines(*, principal_id: str, routine_ids) -> int: ...  # chiamato dall'oblio di F1
```

**Dati.** In `user_memory.sqlite`, dichiarate in `CASCADE_TABLES` e — poiché una
routine è chiavata per forma e principale — anche in `KEY_TARGETS` (T4): una
routine sopravvissuta a un «dimentica» è il gemello semantico che la decisione
ratificata 1 vieta.

```sql
-- [IPOTESI] F6
CREATE TABLE routine_bindings (
  routine_id       TEXT PRIMARY KEY,
  principal_id     TEXT NOT NULL,
  shape_hash       TEXT NOT NULL,           -- sha256(steps || slot_names || context_sig)
  steps_json       TEXT NOT NULL,           -- [{"verb":..,"object":..}] vocabolario chiuso
  slot_names_json  TEXT NOT NULL,           -- ["project_folder","recipient"] — nomi soltanto
  context_sig      TEXT NOT NULL,
  state            TEXT NOT NULL CHECK (state IN
                     ('candidate','active','conflict','revoked')),
  n_success        INTEGER NOT NULL DEFAULT 0,
  last_success_at  TEXT NOT NULL,
  revision         INTEGER NOT NULL DEFAULT 1,
  created_at       TEXT NOT NULL,
  expires_at       TEXT NOT NULL,           -- classe `behavioral` §6.6
  deletion_epoch_at_compile INTEGER NOT NULL
);
CREATE UNIQUE INDEX rb_shape ON routine_bindings(principal_id, shape_hash);
CREATE INDEX rb_pick ON routine_bindings(principal_id, state, last_success_at DESC);

CREATE TABLE routine_evidence (             -- inv. 16: evidenza reale, non conteggio
  routine_id  TEXT NOT NULL REFERENCES routine_bindings(routine_id) ON DELETE CASCADE,
  event_id    TEXT NOT NULL,                -- source_events.kind='runtime_outcome' (F4)
  turn_id     TEXT NOT NULL,
  PRIMARY KEY (routine_id, event_id)
);
```

Nessuna colonna contiene percorsi, destinatari, conti o testo dell'utente: la sola
prosa ammessa sono nomi di verbo, oggetto e argomento.

**Innesti.**

| Punto | File:riga oggi | Che cosa cambia |
|---|---|---|
| Fonte delle forme | `episode_refs.action_shape` e `.slot_names` (F4) | **Consumo**: la forma canonica si ricava dagli episodi, non da `observations`. È una correzione necessaria: `record_observation` è chiamata solo sul percorso di pianificazione completa [PROVATO `runtime/engine/dispatch.py:6197`] e sull'esito di un piano L1 [PROVATO `:6034`], mentre il ramo che serve un accesso L0 esegue e ritorna senza registrare [PROVATO `:5896-5960`]. Le esecuzioni RIPETUTE — cioè proprio ciò che rende una routine — sono servite da L0: leggendo `observations` il contatore resterebbe a uno e la soglia non si raggiungerebbe mai |
| Sostituzione della richiesta | `runtime/agent_runtime.py:6936` `_query_for_planning = _tr.cleaned_query or user_query_for_run` [PROVATO] | **Estensione**: dopo il blocco di destinazione, una seconda sostituzione condizionata quando l'esito è `REWRITTEN`. Stessa figura architetturale già in produzione (§5.7 la richiede, non la inventa) |
| Copertura dei tre consumatori | `runtime/agent_runtime.py:6952`, `:7094`, `:7142` [PROVATO] | **Nessuna modifica**, ma la riscrittura DEVE stare a monte di tutti e tre (scorciatoia lessicale, motore, ramo con allegati): un innesto dentro `_run_engine` lascerebbe scoperto un ramo |
| Estrazione dell'intento | `runtime/agent_runtime.py:6002` `intent_raw = extract_intent(query, _llm_call_fast)` dentro `_run_engine` [PROVATO] | **Nessuna modifica**: `query` qui è già `_query_for_planning`, quindi intento e firma L1 si calcolano sulla forma canonica senza toccare nulla |
| Chiave L0 | `runtime/engine/fastpath.py:220` `lookup(query)` → `normalize_hash` [PROVATO] | **Nessuna modifica al modulo cache**: la chiave diventa quella della forma povera per costruzione. È la prova che F6 non tocca la struttura condivisa |
| Rete di sicurezza sui letterali | `runtime/engine/dispatch.py:5889` `_ungrounded_mutating_args(fp_hit.framework, query)` [PROVATO] | **Nessuna modifica**, ma va documentata: un piano L0 con un passo mutante a valore letterale non è servibile sotto una forma canonica povera — i termini non compaiono — quindi rifiuto e ripianificazione. Le routine servibili sono quelle i cui slot viaggiano per `from_step` o segnaposto |
| Osservazione W1 | `runtime/engine/dispatch.py:6197` e `:6204` [PROVATO] | **Nessuna modifica: niente secondo osservatore.** `observations` resta materiale di W1 e F6 non la legge |
| Riconoscimento della richiesta ellittica | `runtime/compound_decomposer.py:95`, `:337` `detect_chunk_action` [PROVATO] | **Riuso in sola lettura** per le azioni esplicite; gli intervalli arrivano da F0 |
| Forme IT/EN | `runtime/detection_lexicon_seed.py:51`, blocco a radice flessiva a `:589` [PROVATO] | **Estensione**: nuovo blocco `routine.*` sullo stesso modello. Nessuna lista di frasi in Python |
| Compilatore notturno | `runtime/nightly_orchestrator.py:43`; `runtime/jobs/maintenance_tasks.py:142` [PROVATO] | **Nuovo passo** `user_routines_compile` in `NIGHTLY_SEQUENCE`, con lo stesso schema (idempotente, additivo, mai modello) e la stessa politica di scadenza dei semi in ombra [PROVATO `maintenance_tasks.py:153`] |
| Validità delle cache | `runtime/engine/cache_validity.py:100` `ROUTING_EPOCH` [PROVATO] | **Sostituzione del valore** all'accensione: la riscrittura cambia la scelta degli strumenti per una classe di richieste, e la convenzione dichiarata a `:85` lo impone. Senza il salto, i piani anteriori restano serviti |
| Audit del turno | `runtime/agent_runtime.py:3899` campo `canonical_query` di `TurnLog` (ADR 0149) [PROVATO] | **Nuovi campi distinti** `routine_id` e `routine_canonical`: riusare `canonical_query` sovrapporrebbe due nozioni diverse nello stesso registro. `user_query_raw` resta l'originale |
| Oblio | journal e cancellazione di F1 | **Nuovo consumatore**: il passo 3 della cancellazione chiama `revoke_routines`, e `routine_bindings` è dichiarata in `KEY_TARGETS` |

**Ordine di costruzione.**

1. **Lettore di forme, in sola lettura.** Proietta ogni `episode_refs` con
   `outcome='success'` in `(steps, slot_names, context_sig)`. *Verifica*: su un
   campione reale il proiettore o produce una forma priva di valori, o rifiuta; il
   conteggio dei rifiuti e la loro causa sono nel rapporto. Fallisce in modo
   visibile se l'episodio non porta un principale risolvibile — oggi accade su
   Telegram, dove l'unica chiamata a `run_turn` del demone non passa
   `owner_user_id` [PROVATO `runtime/channels/daemon.py:1894-1897`].
2. **Funzioni pure `canonical_text` e `context_sig`.** *Verifica*: prova di
   proprietà su piani generati — nessuna stringa in uscita compare fra le chiavi di
   contenuto [PROVATO `runtime/engine/executor.py:1262-1292`]; la forma è stabile
   per permutazione degli argomenti e cambia per permutazione dei passi.
3. **Tabelle e compilatore notturno.** Promozione ad `active` solo con `n_success`
   pari alla soglia congelata in F0 (T13) e forma unica per principale; forma doppia
   → `conflict`, mai scelta arbitraria. Il conteggio usa `independent_evidence`
   (T12): un turno servito **dalla routine stessa** non conta. *Verifica*: due
   esecuzioni consecutive del passo notturno non cambiano una riga; una routine
   attiva usata cento volte non incrementa `n_success` né sposta la scadenza; un
   epoch arretrato rispetto a quello autorevole impedisce la promozione.
4. **Riconoscitore e selezione.** Concetti `routine.*` nel lessico; selezione per
   azioni e oggetti espliciti, compatibilità di `context_sig`, recenza e margine
   congelato in F0. *Verifica*: sulle trappole del corpus, due candidati entro il
   margine producono `AMBIGUOUS` e nessuna riscrittura; zero candidati producono
   `NO_MATCH`. L'astensione è un esito riportato, non un silenzio.
5. **Innesto in `agent_runtime`, interruttore spento.** *Verifica*: con
   l'interruttore spento il turno è byte-identico a prima su tutto il corpus.
6. **Audit e prova sulle cache.** Originale, `routine_id`, forma canonica e
   revisione dell'istantanea nel registro del turno. *Verifica*: due principali con
   default diversi che producono la stessa forma canonica condividono la riga L0 e
   ricevono argomenti finali diversi; una lettura diretta di `fastpaths` e
   `observations` dopo il corpus non contiene alcun valore personale.
7. **Accensione.** Salto di `ROUTING_EPOCH`, corpus congelato, turno reale.
   *Verifica*: terzo braccio contro baseline lineare sulla metrica primaria
   preregistrata; danni bloccanti a zero con denominatore dichiarato (§11.3).

**Interruttore.** `METNOS_USER_ROUTINES`, predefinito `0`, risolto da
`user_context.flags` (T11). Spenta: `resolve_routine` non viene mai chiamata
(guardia prima dell'importazione); il passo notturno esce senza aprire il
database; nessuna riga di `routine_bindings` viene letta o scritta. Restano
integre F0-F5: nessun modulo di quelle fasi importa `routines`, la dipendenza è a
senso unico.

**Prove.**

- *Unitarie*: forma canonica priva di valori a partire da un piano con percorso,
  destinatario e conto letterali; forma stabile per riordino degli argomenti;
  `context_sig` che cambia quando cambia una classe d'oggetto e non quando cambia un
  valore; astensione con due forme a pari margine.
- *Integrazione*: richiesta ellittica del proprietario con una sola routine attiva,
  con due, con nessuna; stessa richiesta da un ospite senza routine; revoca della
  routine dentro un «dimentica» e nuova richiesta ellittica che ricade sul motore.
- *Avversariali*: pagina o messaggio che contiene la forma marcatrice di routine —
  l'origine esterna non produce riscrittura, per il predicato comune di T9; routine
  che nomina uno slot corrispondente a un argomento citato in una clausola `when` di
  capacità, rifiutata alla compilazione (inv. 5 e 11); richiesta che nomina
  esplicitamente un valore diverso da quello della routine, dove la precedenza resta
  al turno corrente (inv. 6); due principali sulla stessa forma canonica con cache
  L0 calda, senza contaminazione.
- *Iniezione di guasto*: lessico non coperto nella lingua dell'istanza;
  `episode_refs` svuotata fra compilazione e uso; epoch arretrato durante il passo
  notturno; catalogo cambiato dopo la compilazione, dove la firma di validità
  invalida il piano e non la routine; interruttore spento a metà corpus con
  comportamento della fase precedente invariato (§11.5).
- *Turno reale §8.5*: uno su `/agent/turn` con la richiesta ellittica e uno su
  Telegram; per Telegram l'esito atteso è l'astensione motivata finché F0 non rende
  obbligatorio il proprietario del turno, e va riportato come tale, non come
  successo.

**Fuori da questa fase.**

- Il legame fra una forma di piano e la persona nasce dagli eventi `runtime_outcome`
  e dagli episodi: appartengono a **F4**. Senza F4 certificata, F6 non ha come
  attribuire un successo a un principale e non è chiudibile.
- Principale canonico, proprietario obbligatorio su tutti i canali, intervalli di
  clausola e margine numerico della selezione: **F0**. Finché il proprietario del
  turno resta facoltativo, le routine sono spente su Telegram per fallimento chiuso,
  non per scelta.
- Il riempimento degli slot con valori personali dopo il piano, e la sezione firmata
  che lo autorizza, sono **F3**: F6 produce nomi di slot e si ferma lì.
- Store, journal e quarantena sono **F1**; F6 ne è consumatore in scrittura solo per
  la revoca.
- La promozione di una routine per apprendimento, e la politica del proprietario che
  la consente, sono **F5**: in F6 una routine nasce `candidate` e diventa `active`
  per soglia deterministica dichiarata.
- Un compositore che spieghi a parole quale routine è stata scelta: fuori dal nucleo
  per decisione ratificata 2; l'audit qui è un elenco di identificativi e forme, non
  prosa generata.

### 13.9 Rischio di interferenza con il nucleo

La domanda che precede qualunque implementazione non è «funziona?» ma «che cosa
può rompere di ciò che oggi funziona?». La ricognizione del 28 luglio permette di
rispondere per punti reali, non per rassicurazione.

**Ciò che è protetto da un interruttore.** Tutte le capacità: confine di chat,
presentazione, riferimenti, default, ricerca, episodi, apprendimento implicito,
routine. Spente per impostazione predefinita, monotone (T11), con la garanzia
provata che il turno resti byte-identico. Qui il rischio è basso per costruzione.

**Ciò che NON è protetto da un interruttore, e dove sta il rischio vero.** Sono
correzioni al codice esistente, non capacità: un interruttore le nasconderebbe
invece di dimostrarle. Sono quattro, tutte in F0-F1, e vanno trattate come
interventi a sé, ciascuno reversibile in un commit:

| Intervento | Che cosa può rompere | Contenimento |
|---|---|---|
| WAL su `users.db` (F0) | La vista in sola lettura del profilo dentro bubblewrap non può creare i file `-shm`: `read_persons` smetterebbe di funzionare [PROVATO `runtime/sandbox.py:115-118`, `executors/read_persons/manifest.toml:100`] | O si legano i file affiancati, o **W2 resta in modalità ordinaria** e il WAL vive solo su `user_memory.sqlite`. La seconda opzione è quella predefinita: nessun beneficio di F0 dipende dal WAL su W2 |
| Riscrittura di `split_query_chunks` (F0) | È la segmentazione da cui dipende il Tutor, certificato a 115+7/134 con banda di rumore di un caso | La sostituzione è additiva — `split_query_spans` diventa primario e la funzione esistente ne deriva — e la falsificazione è la **certificazione Tutor a macchina scarica** (T15), non un campione di turni |
| Tipo di ritorno di `list_prefs` (F0) | Due consumatori reali: la pagina amministrativa e il suo modello [PROVATO `runtime/http_routes_admin.py:1122`, `runtime/templates/user_detail.html:164-166`] | Cambio di contratto con i due chiamanti aggiornati nello stesso commit e una prova che nessuno legge più un dizionario piatto |
| Transazioni e chiusura connessioni nel CRUD preferenze (F0) | Oggi quelle funzioni sono in autocommit e non chiudono la connessione [PROVATO `runtime/users.py:695-756`] | È una correzione di un difetto esistente, non un rischio nuovo: la prova è due scrittori concorrenti su 500 scritture senza blocchi e con i descrittori del processo piatti |

**Punti di contatto ad alta densità, da affrontare uno alla volta.**
`TurnLog.write` (F4), il punto di strozzatura dell'invocazione (F3),
`_finalize_answer_text` (F2), l'inserimento del controllo di piano (F3) e la
convalida dei manifest al caricamento (F3, che può togliere un executor dal
catalogo). Ognuno è un chiamante solo o due, dichiarati nelle tabelle «Innesti»
con riga e tipo di modifica: è la ragione per cui quelle tabelle esistono.

**Un evento non correttivo da programmare, non da subire.** L'accensione di F6
richiede il salto di `ROUTING_EPOCH` [PROVATO `runtime/engine/cache_validity.py:100`],
che invalida i piani in cache: è un costo di prestazione una tantum, previsto
dalla convenzione già in uso, non un rischio di correttezza.

**Regola di arresto.** Le fasi sono progressive e ciascuna vale da sola: F0-F1
sono correzioni e infrastruttura senza alcun effetto visibile; F2 è il primo
beneficio; F3 è il primo cambio di *azione*. Fermarsi dopo una qualunque di esse
lascia il sistema coerente, perché ogni fase chiude con la dimostrazione che la
precedente è intatta (§11.5). Ratificare ADR 0200 non avvia nulla: fissa le
scelte, il calendario resta una decisione separata.

### 13.10 Provenienza di questo capitolo

Le sezioni 13.2-13.8 sono state prodotte il 28 luglio 2026 da sette redazioni
indipendenti su cinque dossier di ricognizione del codice, e poi attaccate da due
verifiche avversariali che hanno trovato 38 difetti — nove bloccanti — tutti
riparati qui prima dell'inserimento. Il materiale grezzo, i difetti e i rimedi
sono conservati in `internal/reports/rm0001-attuazione-20260728/`. Le sedici
decisioni trasversali di §13.0 sono la parte di quella riparazione che non poteva
stare dentro una singola fase.

## 14. Decisioni fissate e ADR di fase

ADR 0200 è **ratificata** dal 28 luglio 2026 e fissa:

- perimetro solo conoscenza utente;
- principale canonico fail-closed;
- W2 autorevole per preferenze/default tipizzati;
- valori personali dopo il piano;
- routine pre-cache soltanto in forma canonica povera;
- apprendimento implicito proprietario senza approvazione per elemento;
- chat come controllo primario;
- oblio con invalidazione coda e restore in quarantena;
- **ampiezza dell'oblio per chiave** sui valori tipizzati, e inventario visibile
  di ciò che resta per i claim liberi;
- **aggregazione deterministica**: nessun compositore locale nel nucleo, con il
  criterio di riapertura scritto in §5.8;
- prova a tre bracci;
- esclusione attuale di executor memories, esperienza executor, recupero denso,
  Leiden e MCP.

La ratifica non avvia F0: fissa le scelte perché non vengano riaperte durante
l'implementazione. Il calendario è una decisione separata, e §13.9 dichiara dove
il lavoro tocca il nucleo e come si contiene.

ADR ancora necessarie, una per fase e prima del relativo codice:

1. F0: schema del principale e sorgente di `authz_revision`;
2. F1: schema SQLite, journal e protocollo backup/restore;
3. F2: grammatica dei comandi diretti e UX i18n;
4. F3: schema manifest `[personalization]`, primo default e ReferenceSlot;
5. F4: retention episodica e contratto delle citazioni;
6. F5: soglie preregistrate, classi promuovibili e politica owner/guest;
7. F6: forma canonica delle routine e prova cache.

Le soglie numeriche vengono congelate in F0 dopo la baseline, prima di vedere
il test. Non sono una decisione di prodotto lasciata aperta.

## 15. Criteri di completamento

RM-0001 passa a `implemented` soltanto quando F0-F6 sono chiuse con prove e:

- UC-01...UC-12 hanno casi end-to-end referenziati;
- HTTP e Telegram usano lo stesso `UserContextBoundary` e principale;
- non esiste fallback `host` raggiungibile dalle API memoria;
- W2, default, memorie, episodi e routine sono esplorabili e cancellabili;
- la cancellazione resiste a riavvio, coda concorrente e restore supportato;
- dati sensibili, segreti e contenuto esterno non sono persistiti;
- ogni claim libero cita la fonte reale e dichiara fallibilità;
- l'apprendimento implicito del proprietario è operativo senza conferme per
  elemento, con spegnimento e oblio conversazionali;
- un ospite non apprende implicitamente e non legge altri profili;
- una preferenza/default non riduce capability, mandato o consenso;
- L0/L1 restano condivise e senza valori personali;
- F6 rende comprensibile un corpus congelato di richieste ellittiche oggi
  fallite e batte la baseline lineare;
- ogni fase ha test unitari, integrazione, avversariali, fault injection e
  turni reali;
- documentazione pubblica IT/EN descrive soltanto le fasi realmente attive;
- ADR, indice anti-regressione e installer sono aggiornati alla fase;
- ogni interruttore spegne la propria fase senza rompere quella precedente.

## 16. Recepimento della review Fable

La consegna Fable non ha completato la propria fase di refutazione; i rilievi
sono stati quindi verificati singolarmente contro RM-0001 e codice. Esito:

| Rilievo | Decisione finale |
|---|---|
| il vecchio nucleo era troppo debole | accolto: default, episodi, aggregazioni e routine diventano casi core |
| riscrittura pre-cache unica soluzione | accolto con limite: solo forma canonica povera; valori dopo il piano |
| precedente `backend_resolver` ignorato | accolto e generalizzato dichiarativamente |
| esperienza executor nello stesso documento | accolto: rimossa da RM-0001 |
| derivatore prima della misura | accolto: exact/FTS ed episodi precedono F5 |
| F0 costruiva suite experience prematura | accolto per eliminazione del dominio |
| primo ReferenceSlot non scelto | accolto: `project_paths.json` |
| secondo riconoscitore bilingue | accolto: `detection_lexicon` e splitter comune |
| linee/tabelle provano da sole complessità | respinto; usate solo come segnale |
| 21 azioni necessarie all'utente | accolto il problema, respinto il conteggio aggregato |
| apprendimento silenzioso senza produttore | accolto: F5 e stato finale espliciti |
| togliere conferme mutanti dopo N successi | respinto: l'autorità resta nei mandati |
| principal e contratti senza produttore | accolto come blocco F0 |
| `project_paths` e `_RUNTIME_ARG_SOURCES` ignorati | accolto |
| builtin senza sandbox filesystem | risolto eliminando l'executor memories dal nucleo |
| coda può riscrivere un oblio | accolto con invalidazione conservativa per principale/epoch |
| ledger nello stesso database | accolto con journal separato e quarantena |
| sicurezza definita sul passo, non sul piano | accolto: controllo dell'intero piano |
| MemorySourceEvent senza esito | accolto con tipi evento distinti |
| W2 e memoria libera cieche | accolto con PreferenceSpec e conflitto cross-store |
| minore senza substrato | accolto; nessun claim finché manca il marcatore |
| isolamento ospite contro admin macchina | accolto come limite dichiarato, non come cifratura promessa |
| identificatore riciclato | accolto con `identity_anchor` |
| candidato ostile fabbricato su share o registro prima della conferma | accolto: contromisura in §9 **e** categoria di trappole nel corpus §11.1, senza la quale il criterio bloccante non misurerebbe nulla |
| prova causale solo per hint | accolto con tre bracci per ogni fase influente |
| metriche giocabili | accolto: oracolo indipendente, esche congelate e denominatori |
| Leiden necessario | respinto: rimosso senza soglia numerica arbitraria di riapertura |
| Swafra prova il valore di Leiden | respinto dopo audit primario: manca un'ablation coerente e l'artefatto `@10` non rispetta `k` |
| retrieval source-diverse di Swafra | accolto come variante F4 bounded, non come percentuale fissa o prova end-to-end |
| MCP Swafra come superficie memoria | respinto: identità, origine e autorità devono restare nel confine Metnos |

## 17. Audit primario Swafra e fonti

### 17.1 Oggetto verificato

Audit eseguito il 26 luglio 2026 su:

- sito <https://swafra.vercel.app/>;
- repository <https://github.com/kunal12203/swafra>;
- commit congelato
  [`24dba18a4194aef0cb0d6d6c68cf46e6fcbf2da7`](https://github.com/kunal12203/swafra/tree/24dba18a4194aef0cb0d6d6c68cf46e6fcbf2da7);
- `README.md`, `BENCHMARK.md`, `CLAUDE.md`, `SKILL.md`, engine Python,
  server MCP, runner e risultati LongMemEval versionati.

Il metodo, i conteggi e i limiti sono conservati in
`internal/reports/rm0001-swafra-primary-audit-20260726.md`.

Swafra è un archivio documentale locale esposto come sei tool MCP. Ingerisce
testo, lo spezza, calcola embedding, crea archi e restituisce chunk. Il file
`CLAUDE.md` invita l'agente a salvare proattivamente preferenze, documenti,
decisioni e qualunque informazione probabilmente riutilizzabile, e a chiamare
`get_context` all'inizio di ogni sessione.

Non è lo stesso prodotto di RM-0001. Nel commit verificato non risultano:

- principale autenticato o isolamento per utente;
- distinzione autorevole fra testo dell'utente, dell'assistente e contenuto
  esterno;
- categorie sensibili, policy d'applicazione o capability/consenso;
- evidenze tipizzate, conflitti o correzioni temporali efficaci;
- journal write-ahead, epoch, restore protetto o scritture JSON atomiche.

`delete_source` rimuove chunk e archi con il `source_id` richiesto, ma gli archi
cross-session vengono creati con `source_id=None` proprio per sopravvivere alla
cancellazione della sorgente: possono quindi restare riferimenti pendenti agli
ID dei chunk rimossi. Inoltre il codice di `superseded_by` cerca vecchi chunk
della stessa sorgente dopo averli già eliminati dall'insieme, quindi nel flusso
corrente non costituisce un reconciler delle versioni.

Questi rilievi non sono una valutazione generale del progetto Swafra: spiegano
perché non è adottabile come sottosistema di autorità e dati personali di
Metnos.

### 17.2 Audit del benchmark dichiarato

Il sito dichiara «94.7% on LongMemEval — the best out there». `BENCHMARK.md`
qualifica correttamente il numero come session-level retrieval recall e dice
che l'accuratezza end-to-end dell'LLM non è misurata. Nello stesso file,
però, l'output atteso mostra `99.6% recall_all@10`, non 94,7%.

L'artefatto `bench/results.json` del commit dichiara 470 casi valutati,
30 abstention saltati e `recall_all=0.9957446808510638`. Non dimostra
`recall_all@10`:

1. il runner calcola `recall_at_k(retrieved_sids, answer_sids)` sulla lista
   completa restituita da `get_context`;
2. soltanto nella serializzazione conserva
   `retrieved_sessions = retrieved_sids[:10]`;
3. l'artefatto dichiara da 28 a 46 risultati usati per caso, media
   35,387, pur avendo configurazione `k=10`;
4. ricalcolando sugli unici primi dieci conservati si ottengono
   `recall_all@10 = 434/470 = 92,34%` e
   `recall_any@10 = 464/470 = 98,72%`;
5. l'engine presente nello stesso commit limita oggi l'uscita a dieci per il
   corpus dichiarato, quindi non può aver prodotto i conteggi 28-46: codice e
   risultato versionato appartengono a revisioni comportamentali diverse;
6. il runner registra `SCIMAP_EMBED_BACKEND=local`, ma l'engine non consulta
   quella variabile e tenta comunque `fastembed`: la configurazione salvata non
   prova il backend realmente usato.

Non esiste nello stesso commit un'ablation riproducibile che tenga fissi
retriever, `k` e corpus e isoli il contributo di Leiden rispetto al fallback o
a un chunker semplice. Di conseguenza RM-0001 non usa né il 94,7% né il 99,6%
come evidenza per Leiden, grafo o qualità end-to-end.

### 17.3 Ciò che RM-0001 riusa

Tre idee restano utili come ipotesi, non come pacchetto da importare:

- funzionamento interamente locale;
- retrieval ibrido confrontabile, dopo exact e FTS5, se il corpus Metnos ne
  mostra il bisogno;
- diversità per sorgente per ridurre chunk ridondanti, sempre dentro lo stesso
  `k` e con prova sulle evidenze multiple.

MCP, salvataggio indiscriminato, profilo nel prompt di sessione, percentuale
fissa di sorgenti, Leiden e graph walk non entrano nel nucleo. Una futura
variante deve battere exact+FTS e la baseline lineare sul corpus preregistrato,
misurando separatamente retrieval, risposta finale e danni.

### 17.4 Altre fonti

- ADR 0182, validità delle cache;
- ADR 0185, learning-loop W1;
- ADR 0187, preferenze W2;
- ADR 0193, standard executor e capability condizionali;
- ADR 0196, politica centrale d'esecuzione;
- ADR 0199, dichiarazioni di dominio e iniezione server-side;
- LongMemEval, arXiv 2410.10813;
- Zep/Graphiti, arXiv 2501.13956;
- Mem0, arXiv 2504.19413;
- Sleep-time Compute, arXiv 2504.13171.

I risultati pubblicati da produttori di sistemi di memoria sono segnali di
confronto, non prova che l'iniezione di prosa nel planner sia corretta per
Metnos. La prova autorevole resta il corpus Metnos preregistrato.

## 18. Registro di avanzamento

| Data | Stato | Evento | Prova |
|---|---|---|---|
| 2026-07-23 | `active` | creazione della roadmap e prima review avversariale | analisi di cache, planner, provenienza e oblio |
| 2026-07-26 | `active` | ampliamento multidominio e microprogettazione | review architettura, sicurezza e scienza della memoria |
| 2026-07-26 | `ready` | finalizzazione dopo verifica delle sette lenti Fable | perimetro ridotto, casi forti aggiunti, contratti e fasi F0-F6 definiti; nessuna implementazione |
| 2026-07-26 | `ready` | audit primario Swafra | sito e commit `24dba18`; benchmark ricalcolato, nessun cambio di perimetro |
| 2026-07-28 | `ready` | review adversariale conclusiva | 63 rilievi su 70 già assorbiti, 8 caduti sotto attacco, 5 difetti nuovi trovati nella riscrittura; verdetto «nessun ridisegno necessario»; Leiden confermato fuori con prove indipendenti |
| 2026-07-28 | `ready` | tre decisioni ratificate e ADR 0200 accettata | oblio per chiave, compositore aggregato fuori dal nucleo, calendario di F0 separato |
| 2026-07-28 | `ready` | strato di attuazione §13 | cinque dossier di ricognizione sul codice, sette redazioni di fase, due verifiche avversariali; 38 difetti trovati e riparati, di cui 9 bloccanti; 60 assenze verificate |

## 19. Appendice di compatibilità pubblica

La risposta seguente alimenta una pagina statica già pubblicata e resta
conservata come artefatto storico. Non amplia il perimetro normativo di questa
roadmap: in particolare MCP e Leiden non fanno parte di RM-0001 e richiedono
una decisione futura separata.

## 20. Risposta Reddit pronta

Metnos is a self-hosted architecture for governed AI agents, built around signed, typed executors with explicit authority.

Thanks — this gave me a useful direction. I would not plug the MCP server into Metnos as-is. I would turn the idea into a small local Memory Compiler: hybrid retrieval and typed links first, with Leiden clustering only where it proves useful. The core would handle provenance, conflicts, per-user isolation and forgetting; an executor or MCP endpoint would stay a thin, optional interface for explicit remember, inspect, search and forget operations.

P.S. More on Metnos: https://metnos.com
