# RM-0001 — Conoscenza locale e selettiva dell'utente

**Stato:** `active`
**Creazione:** 2026-07-23
**Ultima revisione:** 2026-07-23
**Implementazione:** non iniziata; nessun comportamento descritto qui va
considerato presente senza verifica nel codice e nei test
**Conservazione:** roadmap persistente fino a implementazione dimostrata o
cancellazione esplicita di Roberto
**Decisione di prodotto:** il sottosistema è utile soltanto se aumenta in modo
misurabile comprensione e aiuto, senza cambiare involontariamente pianificazione,
autorità, sicurezza o qualità corrente

## 1. Sintesi decisionale

Metnos deve poter conoscere preferenze, riferimenti, abitudini e contesto utile
del proprietario per evitare ripetizioni, capire meglio richieste implicite e
adattare le risposte. La capacità deve essere locale, esplorabile, correggibile,
selettiva e poco invasiva.

La direzione è approvata, ma non va implementata copiando letteralmente Hermes o
Honcho e non va realizzata iniettando un profilo libero in ogni prompt. La
soluzione raccomandata separa tre funzioni:

1. **preferenze di risposta**, chiuse e tipizzate, applicate soltanto alla
   presentazione pertinente;
2. **riferimenti noti**, espliciti o confermati, risolti deterministicamente;
3. **memoria utente libera**, recuperata soltanto quando serve e sottoposta a
   controllo di pertinenza, conflitto e attendibilità.

Le prime due funzioni possono avere proprietà deterministiche entro contratti
chiusi. La terza no: una memoria derivata da un LLM è un'ipotesi semantica
fallibile, non un fatto verificato. Il vantaggio promesso è quindi **locale,
cancellabile, ispezionabile e tracciabile ai turni sorgente**, non una generica
«verificabilità» o «riproducibilità» della sua verità.

Il percorso normale deve restare invariato quando la memoria non è pertinente,
non è disponibile o non supera il controllo di applicabilità. La memoria può
aiutare una decisione; non costituisce autorità e non può ridurre i controlli su
azioni, credenziali, destinatari o dati.

## 2. Documenti consolidati e autorità

Questa roadmap consolida e corregge, come direzione futura, le analisi svolte il
22 e 23 luglio 2026 su Hermes, Honcho, sul codice Metnos e la successiva review
adversariale del primo design. I documenti preparatori sono stati rimossi il 23
luglio perché il contenuto utile, le decisioni e le fonti primarie sono
integralmente assorbiti da RM-0001.

RM-0001 è quindi l'unica fonte corrente della proposta futura. Per lo stato
reale prevalgono sempre il codice, i manifest caricati, i test e le ADR
implementate.

La roadmap non modifica `CLAUDE.md`, non estende il vocabolario degli executor e
non introduce implicitamente una nuova capacità eseguibile.

## 3. Obiettivo per l'utente

La capacità deve produrre benefici osservabili:

- ricordare preferenze persistenti senza farle ripetere;
- distinguere un'istruzione valida solo adesso da una preferenza stabile;
- risolvere riferimenti ricorrenti come «il progetto Atlas» quando esiste una
  corrispondenza affidabile;
- adattare dettaglio, lingua, unità e forma delle risposte dirette;
- riconoscere correzioni e cambiamenti nel tempo;
- mantenere tutte le origini di un'informazione e segnalare contraddizioni;
- rispondere in modo completo a «cosa sai di me?»;
- correggere o dimenticare immediatamente una conoscenza;
- in una fase successiva, proporre con moderazione automazioni ricorrenti
  realmente utili.

Il valore non si misura dal numero di ricordi. Si misura da quante ripetizioni
evita, da quante ambiguità risolve correttamente e da quanto raramente applica
una preferenza fuori contesto.

## 4. Non-obiettivi

Non fanno parte della prima realizzazione:

- costruire un'identità psicologica generale dell'utente;
- dedurre tratti sensibili o stati emotivi senza richiesta esplicita;
- usare la memoria per concedere autorità o superare un consenso;
- alimentare il pianificatore con un profilo libero permanente;
- importare il servizio Honcho completo o dipendere da servizi esterni;
- conservare indefinitamente trascrizioni, allegati o contenuti di strumenti;
- rendere ogni risposta più personale anche quando non è opportuno;
- effettuare proposte proattive con un modello libero fin dalla prima fase;
- apprendere da ospiti non verificati, automazioni o turni di sistema;
- sostituire autopath, mnestoma o la memoria dei piani con un secondo sistema
  concorrente.

## 5. Stato corrente verificato al 23 luglio 2026

### 5.1 Preferenze esistenti

`runtime/users.py` contiene `user_prefs`, con chiavi chiuse e campi
`user_id`, `key`, `value`, `source`, `updated_at`. La base è riutilizzabile, ma:

- `list_prefs()` restituisce soltanto la mappa chiave-valore e perde provenienza
  e data;
- non sono presenti, nel contratto completo, origine puntuale del turno,
  confidenza, ambito d'uso e versione;
- tono e lunghezza non governano centralmente tutte le risposte;
- l'accesso SQLite non è ancora progettato per un derivatore concorrente con
  scritture periodiche.

### 5.2 Percorso del motore

Il percorso ordinario moderno usa `runtime/engine/proposer.py` e i prompt
tipizzati del motore. Il `planner_system` legacy costruito in
`runtime/agent_runtime.py` non è il punto corretto per un'iniezione universale.

Il contesto runtime raggiunge controlli, executor e sostituzioni di valori, ma
non costituisce oggi un profilo utente tipizzato del proponente. Modificare il
prompt generale rischierebbe di cambiare selezione degli executor, argomenti e
cache anche per richieste che non richiedono personalizzazione.

La collisione con le cache non è teorica. L0 indicizza il piano con
`normalize_hash(query)` (`runtime/engine/fastpath.py`); L1 usa una firma
`verb|object|actions` (`runtime/engine/autopath.py`). Le firme di validità sono
deliberatamente query-independent e non includono affinity
(`runtime/engine/cache_validity.py`). Sono quindi cache di piano condivise e
user-agnostiche. Inserire memoria per-utente nel planning senza inserirla nella
chiave la renderebbe invisibile sugli hit; inserirla nella chiave distruggerebbe
la generalizzazione condivisa e renderebbe il routing dipendente dal profilo.
Nessuna delle due alternative è ammessa.

### 5.3 Presentazione

`runtime/engine/executor.py` contiene il finalizzatore comune, ma molti esiti
sono volutamente deterministici: tabelle, ricevute, errori, richieste di
consenso, file e resoconti di esecuzione. `runtime/output_policy.py` resta la
fonte della forma di questi risultati.

Una preferenza di stile non può quindi essere applicata indiscriminatamente a
ogni risposta senza perdita di dati o alterazione di messaggi normativi.
La ricognizione corrente trova inoltre 117 call-site `_msg()` in
`runtime/orchestration.py`: è una misura contingente, non un contratto, ma
conferma che «personalizzare ogni risposta» non è quasi-wiring. La prima fase
può agire soltanto sulle porzioni narrative che hanno già un punto di
composizione controllato.

### 5.4 Storico e identità

I turni persistiti forniscono materiale sufficiente per una derivazione locale,
ma le letture correnti sono orientate a conversazione e diagnostica, non a una
scansione incrementale affidabile per utente. Serve un lettore limitato e un
cursore, non una scansione completa a ogni esecuzione.

La chiave di memoria deve derivare dall'identità canonica verificata del
proprietario. Un attore sconosciuto, un ripiego sul nome host o un mittente non
associato non possono produrre memoria persistente.

### 5.5 Componenti locali riutilizzabili

- modello locale tramite `runtime/llm_router.py`;
- BGE-M3 già disponibile per rappresentazioni semantiche;
- esecuzione notturna sequenziale e limitata in `runtime/scheduler_v2/`;
- controlli centrali di esecuzione e retropressione;
- `get_inputs` per una domanda funzionale quando l'ambiguità resta reale;
- UI utenti, feedback, storico turni e messaggi i18n;
- SQLite e convenzioni di dati locali già adottate dal progetto.

Non serve un nuovo servizio distribuito.

## 6. Analisi multidisciplinare

### 6.1 Prodotto e interazione

Una memoria utile riduce attrito, ma una memoria visibile in ogni risposta crea
ripetizione e sensazione di sorveglianza. L'interfaccia deve privilegiare:

- effetto silenzioso quando il dato è non sensibile, affidabile e pertinente;
- indicazione breve solo quando una memoria ha risolto un'ambiguità importante;
- domanda soltanto quando la scelta cambia materialmente il risultato;
- una superficie unica di esplorazione, correzione e oblio;
- nessuna sequenza di autorizzazioni per preferenze autoregolanti a basso
  impatto.

L'utente non deve amministrare continuamente il proprio profilo. Il sistema
deve assorbire la complessità e rendere semplice soltanto il controllo finale.

### 6.2 Cognizione e qualità

La somiglianza semantica non equivale a identità. «Preferisco risposte brevi» e
«preferisco risposte dettagliate» possono essere vicine nello spazio vettoriale
ma sono contraddittorie. Consolidamento e recupero richiedono quindi:

- normalizzazione deterministica;
- relazione esplicita `same`, `supports`, `contradicts`, `unrelated`;
- gestione temporale degli aggiornamenti;
- capacità di astensione;
- prevalenza dell'istruzione corrente sulla storia.

Il numero di utilizzi non aumenta la verità di una memoria: `use_count` misura
utilità osservata, non confidenza.

Nemmeno provenienza e schema chiuso trasformano una sintesi LLM in verità. La
provenienza consente di risalire a ciò che l'utente ha realmente detto e di
contestare la derivazione; verifica la catena dei dati, non la correttezza
semantica del claim.

### 6.3 Sicurezza

La memoria persistente è una superficie di attacco tra sessioni. Un testo in
una pagina, email o documento può tentare di presentarsi come preferenza o
istruzione dell'utente. Per questo l'origine deve essere un vincolo
architetturale, non una raccomandazione al modello.

Le memorie recuperate sono dati citati e non attendibili. Non possono contenere
istruzioni eseguibili, ampliare capacità, scegliere credenziali, evitare
consensi o modificare i controlli di sicurezza.

### 6.4 Riservatezza e contesto domestico

Locale non significa automaticamente privato: esistono altri utenti della
macchina, copie di sicurezza, interfacce amministrative e dispositivi associati.
Servono isolamento per utente, permessi restrittivi, riduzione dei dati e
cancellazione reale.

L'isolamento non può dipendere da un `_actor` o `user_id` fornito dal modello,
dal form o dal chiamante. Ogni API dello store deve ricevere un principal già
autenticato, derivare da esso la chiave utente e fallire chiusa se principal,
owner del turno/dialogo e record richiesto non coincidono.

Non si derivano silenziosamente categorie sensibili: salute, religione,
politica, biometria, sessualità, condizione finanziaria o informazioni intime su
terzi. Una richiesta esplicita di ricordare può essere accettata con ambito e
provenienza chiari, senza trasformarla in autorizzazione operativa.

### 6.5 Dati e ciclo di vita

Un singolo `source_turn_id` non basta quando più episodi sostengono o
contraddicono la stessa conoscenza. Memoria ed evidenze devono essere separate.
Una correzione conserva la storia della sostituzione; un comando di oblio,
invece, elimina contenuto, rappresentazione e cache collegate.

Se il turno sorgente resta nei registri per altre ragioni, l'interfaccia deve
dichiararlo onestamente. Un'impronta bloccata impedisce al derivatore di
ricreare immediatamente la stessa memoria; l'impronta non conserva il contenuto.

### 6.6 Prestazioni e operazioni

Il turno ordinario non deve richiedere una chiamata LLM aggiuntiva. Le
preferenze tipizzate vengono lette da un'istantanea piccola; la memoria libera
viene interrogata soltanto per casi idonei. La derivazione più costosa avviene a
lotti, con cursore, budget, retropressione e sospensione sotto carico.

Un errore del sottosistema deve essere neutro: il turno prosegue senza
personalizzazione. Fa eccezione un comando esplicito «ricorda/dimentica», il cui
fallimento deve essere dichiarato e non simulato come successo.

## 7. Alternative considerate

| Alternativa | Vantaggi | Limiti | Decisione |
|---|---|---|---|
| solo `user_prefs` chiuse | minima superficie e alta prevedibilità | non comprende riferimenti e contesto implicito | utile come prima fase, insufficiente come soluzione finale |
| file libero tipo `USER.md` | semplice e leggibile | conflitti, provenienza e multiutente deboli; iniezione grossolana | non adottare come fonte operativa |
| Honcho completo locale | capacità ricca già progettata | PostgreSQL/pgvector, Redis, servizio e derivatore aggiuntivi | non KISS per Metnos |
| profilo libero sempre nel prompt | beneficio immediato apparente | sovrapersonalizzazione, contaminazione del planner e attacchi persistenti | rifiutata |
| architettura locale a tre canali | selettiva, tracciabile ai turni e incrementale | i claim liberi restano ipotesi fallibili | raccomandata |

Honcho resta un riferimento di capacità: derivazione in background,
rappresentazioni sintetiche, conclusioni interrogabili e recupero ibrido.
Hermes mostra inoltre il vantaggio di un'istantanea stabile per sessione. Metnos
deve adottarne i principi utili, non il costo operativo né l'iniezione globale.

### 7.1 Esito verificato della review adversariale del 23 luglio

La review è stata confrontata con la roadmap consolidata e con il codice. Non è
accettata in blocco:

| Reperto | Verdetto | Conseguenza in RM-0001 |
|---|---|---|
| C1, collisione planner/cache | **confermato come vincolo architetturale**; non confermata l'accusa che RM-0001 corrente proponga ancora la peer-card nel planner | memoria libera esclusa dal planning; cache di piano esplicitamente user-agnostiche |
| C2, overclaim «verificabile/riproducibile» | **confermato** per i claim LLM | vantaggio limitato a locale, cancellabile, ispezionabile e tracciabile al turno |
| M1, tono/lunghezza su ogni template | **confermato come limite**, già recepito dalla roadmap corrente | solo porzioni narrative dichiarate personalizzabili |
| M2-M3, determinismo del Q&A e store mutabile | **confermati** | nessuna promessa byte-identica; ripetibilità qualificata a snapshot congelato e percorso deterministico |
| M4, reconciler mancante | **confermato il buco nel design Metnos; non confermata la tesi che il pass 2 di Honcho risolva da solo la memoria persistita**, perché le fonti primarie lo descrivono come riconciliazione della sintesi dialectic e il Dreamer come consolidamento | reconciler di stato Metnos obbligatorio prima dell'attivazione, progettato sui nostri invarianti e non copiato per analogia |
| M5, violazione dell'anti-pattern per iniezione nel planner | **non confermato sulla roadmap corrente**, che vieta già l'iniezione | resta accettato il rischio sottostante: prosa derivata trattata solo come dato non attendibile |
| M6, dossier, authz e fonti sensibili | **confermato il rischio e la necessità di un contratto esplicito; non confermata l'affermazione che i vecchi buchi dialog/callback siano ancora aperti**, perché la chiusura audit del 22 luglio e il codice corrente mostrano owner-binding | API del nuovo store principal-bound, policy ospiti e filtro sensibile/redatto fail-closed; nessun affidamento implicito sui confini esistenti |
| m1-m4 | **confermati come requisiti di specifica**, non come prova che ogni meccanismo sia già scelto | policy ospiti, retention separata, trigger tipizzato, limiti epistemici e di latenza |

Questa classificazione sostituisce sia il design iniziale attaccato dalla review
sia la review stessa come documento operativo: ciò che non è riportato qui non è
una decisione accettata.

## 8. Invarianti della soluzione

1. **Identità:** ogni lettura e scrittura deriva il `user_id` da un principal
   canonico verificato; non accetta un'identità libera dal chiamante.
2. **Origine:** solo segnali ammessi possono produrre memoria persistente.
3. **Neutralità:** assenza o guasto della memoria non cambia il percorso base.
4. **Autorità:** nessuna memoria concede permessi o riduce un consenso.
5. **Selettività:** il valore predefinito del recupero è «nessuna memoria».
6. **Precedenza:** l'istruzione esplicita nel turno corrente prevale.
7. **Ambito:** una preferenza vale soltanto per destinatario e dominio
   dichiarati.
8. **Conflitto:** evidenze incompatibili impediscono l'applicazione automatica.
9. **Provenienza:** ogni conoscenza attiva è riconducibile a una o più evidenze.
10. **Oblio:** eliminare significa rimuovere contenuto e impedirne il
    riapprendimento immediato.
11. **Limite:** record, testo iniettato, costo LLM e durata sono limitati.
12. **Osservabilità:** ogni influenza sul turno è registrata per identificatore,
    senza copiare contenuto sensibile nei log.
13. **Compatibilità:** planner, executor, vaglio e politica di output restano
    invariati nelle prime fasi.
14. **Locale:** nessun dato del profilo lascia l'istanza per impostazione
    predefinita.
15. **Confine cache:** le cache di piano restano condivise e user-agnostiche;
    profilo, `user_id` e `profile_version` non entrano nelle loro chiavi.
16. **Confine prompt:** claim derivati non entrano nel prompt di sistema né nel
    planner; quando un controllo locale li esamina sono dati citati e non
    attendibili in un canale delimitato.
17. **Onestà epistemica:** una traccia verso il turno dimostra l'origine, non la
    verità della sintesi; il sistema deve poter mostrare incertezza e astenersi.
18. **Ospiti:** nessun apprendimento implicito per ospiti per impostazione
    predefinita; ogni profilo ospite eventualmente abilitato resta isolato dal
    proprietario e dagli altri ospiti.

## 9. Architettura proposta

```text
input autenticato dell'utente
        |
        +--> istruzione del turno --> planner/cache correnti --> esecuzione
        |                                  |                         |
        |                                  ` nessun profilo          |
        |                                                            v
        +--> filtro di scrittura --> candidati                  output policy
        |                                                            |
inizio turno --> istantanea chiusa ----------------------------------+
        |             | preferenze: sola presentazione narrativa     |
        |             ` riferimenti: sola scelta fra candidati       v
        |                                                    presentazione
        `--> recupero tipizzato su richiesta
                    | candidati limitati dopo il piano
                    | controllo applicabilità/conflitto
                    `--> scelta vincolata, evidenze di turno o astensione

derivatore locale a lotti --> soltanto candidati --> promozione regolata
```

### 9.1 Istantanea per turno

Dopo autenticazione e prima della pianificazione, il runtime risolve una sola
volta l'identità e acquisisce un `UserContextSnapshot` immutabile contenente:

- versione del profilo;
- preferenze tipizzate applicabili;
- riferimenti confermati strettamente necessari;
- politica e lingua;
- nessuna osservazione libera non richiesta.

L'istantanea è caricata just-in-time e resta stabile durante il turno. Una
modifica diventa visibile dal turno successivo. Un'istruzione esplicita nella
richiesta corrente vale immediatamente perché appartiene all'intento corrente,
non perché l'istantanea viene modificata a metà esecuzione.

La **sola cache dell'istantanea profilo** è indicizzata da
`(user_id, profile_version)`. È distinta da L0/L1 e da ogni altra cache di piano,
che resta condivisa e non conosce l'utente. Ogni log di turno registra versione
e hash dell'istantanea, non i suoi contenuti.

### 9.2 Preferenze di risposta

Sono valori chiusi, validati e con ambito, per esempio:

- `lang`;
- `reply_length`;
- `tone`;
- `units`;
- eventuali `format` ed `emoji` soltanto dopo decisione sul vocabolario.

L'ambito minimo distingue:

- risposta dell'assistente al proprietario;
- artefatto creato per il proprietario;
- comunicazione destinata a terzi;
- dominio specifico.

Una preferenza informale per la chat non si applica a una lettera formale, a un
contratto o a un'email verso terzi. Errori, consensi, ricevute, tabelle e avvisi
normativi mantengono la forma definita dalla politica di output.

### 9.3 Riferimenti noti

I riferimenti noti associano un'espressione normalizzata a un oggetto
identificabile entro un tipo chiuso:

- progetto o cartella;
- persona o contatto;
- calendario;
- account o casella, senza credenziali;
- luogo abituale;
- altro tipo ammesso con decisione esplicita.

La prima ambiguità materiale viene risolta con una domanda funzionale. La
risposta dell'utente crea l'associazione con origine e ambito. Utilizzi futuri
possono risolverla in silenzio se oggetto, dominio e contesto coincidono. Se i
candidati cambiano o il riferimento diventa ambiguo, il sistema torna a
chiedere.

### 9.4 Memoria libera

Contiene affermazioni utili che non entrano nel vocabolario chiuso, per esempio
interessi, abitudini o contesti ricorrenti. Ogni affermazione è un dato, non
un'istruzione.

La memoria libera non entra nel prompt generale. Un servizio interno
`consult_user_context()` la interroga soltanto dopo che il chiamante ha
dichiarato uno scopo ammesso, come:

- rispondere a una domanda dell'utente sul proprio profilo;
- risolvere una scelta fra candidati già delimitati;
- interpretare un riferimento personale non risolto deterministicamente;
- in futuro, valutare una proposta non operativa e revocabile.

Il servizio non è inizialmente un executor visibile al planner. È un componente
interno con output strutturato e insieme di scelte chiuso.

Riceve un principal autenticato, un `purpose_code` chiuso, la versione del
profilo e, quando serve una disambiguazione, gli identificatori dei candidati
già prodotti dal motore. Non riceve un `user_id` arbitrario e non può restituire
un identificatore estraneo all'insieme fornito. I claim e gli excerpt esaminati
sono delimitati come dati non attendibili; non diventano istruzioni di sistema.

### 9.5 Trigger del recupero

Nessun LLM libero decide se «personalizzare» un turno. Il servizio può essere
invocato al massimo una volta per turno e soltanto da uno di questi eventi
tipizzati:

1. intento semantico chiuso di ispezione/correzione/oblio del proprio profilo;
2. fallimento di una risoluzione esatta di riferimento, entro un tipo ammesso;
3. insieme di almeno due candidati concreti già delimitati dal motore, quando le
   regole correnti richiederebbero altrimenti `get_inputs`.

Un semplice miss di cache, una somiglianza generica con interessi personali o
una decisione testuale del planner non sono trigger. Frequenza, latenza e costo
devono essere misurati separatamente per `purpose_code`; superare il budget
produce astensione, non un secondo tentativo LLM.

## 10. Modello di fiducia delle origini

| Classe | Esempio | Destinazione | Attivazione |
|---|---|---|---|
| `explicit` | «d'ora in poi rispondi brevemente» | preferenza o memoria | immediata se non sensibile e valida |
| `correction` | «non è Atlas, intendo Orion» | riferimento/conflitto | immediata; prevale sul precedente |
| `interaction` | scelta in un dialogo di disambiguazione | riferimento | attiva nel medesimo ambito |
| `behavioral` | stessa operazione riuscita in episodi indipendenti | candidato | mai attiva al primo evento |
| `derived` | sintesi locale di più utterance dell'utente | candidato | promozione solo con evidenze sufficienti |
| `external` | email, pagina, file, risultato executor | nessuna | scrittura vietata |
| `assistant` | testo prodotto da Metnos | nessuna | scrittura vietata |
| `automation` | task, telos, scheduler, turni di sistema | nessuna | scrittura vietata |

### 10.1 Filtro di scrittura

Il filtro opera prima di qualsiasi estrazione LLM:

1. richiede utente canonico verificato;
2. accetta soltanto parti attribuite direttamente all'utente, dopo lo scrub di
   credenziali e segreti;
3. esclude testo citato, inoltrato, allegato, blocchi di codice e risultati di
   strumenti;
4. distingue marcatori temporanei («in questa risposta») da persistenti
   («d'ora in poi», «preferisco», «sempre»);
5. vieta inferenze sensibili implicite;
6. limita lunghezza e numero di candidati;
7. collega l'evidenza all'episodio originale;
8. controlla l'impronta degli elementi dimenticati prima della promozione.

Un frammento contenente marker di redazione come `<REDACTED:...>`, materiale
proveniente da aree protette o un segreto rilevato non viene passato al
derivatore né persistito come excerpt. Un dato sensibile può entrare soltanto da
una richiesta esplicita e specifica di memoria, con opt-in e ambito visibile;
non può mai essere dedotto implicitamente.

In caso di dubbio non scrive. L'intelligenza dell'estrattore non sostituisce
questo confine deterministico.

### 10.2 Proprietario, ospiti e minori

La derivazione implicita è abilitabile soltanto per il proprietario. Per un
ospite, anche verificato, il default è nessuna derivazione persistente; si
possono conservare preferenze esplicitamente richieste soltanto dopo opt-in
individuale. Per un profilo indicato come minore la derivazione implicita resta
vietata nelle prime fasi.

Il proprietario non ottiene automaticamente lettura del profilo di un ospite.
L'amministrazione può mostrare stato, quota e comando di cancellazione senza
mostrare i claim. «Cosa sai di me?» opera sempre e soltanto sul principal
corrente.

## 11. Modello dati proposto

Il modello è relazionale e locale. I nomi definitivi richiederanno una
specifica implementativa; il contratto logico minimo è il seguente.

### 11.1 Stato del profilo

`user_profile_state`

| Campo | Uso |
|---|---|
| `user_id` | chiave canonica |
| `version` | invalidazione atomica delle istantanee |
| `derivation_cursor` | ultimo turno esaminato |
| `policy_version` | versione delle regole di derivazione |
| `updated_at` | audit |

### 11.2 Preferenze

Estensione di `user_prefs`:

| Campo | Uso |
|---|---|
| `user_id`, `key`, `value` | identità del valore tipizzato |
| `source_mode` | explicit, correction, interaction, derived |
| `source_turn_id` | episodio principale |
| `confidence` | attendibilità calibrata, non conteggio usi |
| `scope` | destinatario e dominio |
| `updated_at` | ordinamento temporale |
| `locked_explicit` | impedisce sovrascrittura da derivazione |

### 11.3 Memorie e riferimenti

`user_memories`

| Campo | Uso |
|---|---|
| `user_id`, `memory_id` | isolamento e identità stabile |
| `kind` | preference, reference, fact, habit, goal, context |
| `claim` | affermazione breve e autonoma |
| `normalized_key` | deduplicazione deterministica quando applicabile |
| `status` | candidate, active, conflict, superseded |
| `source_mode` | classe di origine |
| `confidence` | confidenza calibrata |
| `sensitivity` | normal, restricted, explicit_sensitive |
| `scope` | dominio, destinatario e durata |
| `created_at`, `updated_at`, `expires_at` | ciclo di vita |
| `last_used_at`, `use_count` | utilità, mai verità |
| `retention_class` | durable, temporal o behavioral; governa scadenza e sfratto |
| `embedding` | rappresentazione locale |
| `embedding_model`, `embedding_version`, `embedding_dim` | migrazione e ricostruzione |
| `superseded_by` | aggiornamento non distruttivo |

`user_memory_evidence`

| Campo | Uso |
|---|---|
| `user_id`, `memory_id` | vincolo e cascata |
| `turn_id` | episodio sorgente |
| `span_hash` | integrità e blocco duplicati |
| `excerpt` | frammento breve, ripulito e limitato |
| `observed_at` | ragionamento temporale |
| `relation`, `weight` | supporto o contraddizione |

Un'evidenza identifica sempre il turno reale. Un `memory_id` o un claim
derivato non è, da solo, una citazione sufficiente.

`user_memory_blocks` conserva soltanto impronte salate e scadenza necessarie a
evitare il riapprendimento immediato dopo un oblio. Non conserva il testo.

### 11.4 Vincoli di persistenza

- foreign key e cancellazione a cascata per evidenze e rappresentazioni;
- transazione singola per modifica più incremento versione;
- WAL e `busy_timeout` per letture concorrenti limitate;
- permessi `0600` sul database e nessuna copia nei rapporti;
- massimo iniziale di circa 200 memorie attive per utente; il limite sfratta
  prima candidati e segnali comportamentali, mai un fatto `durable` esplicito
  soltanto perché non è recente;
- scadenza per candidati non confermati e segnali comportamentali;
- ricostruzione esplicita degli embedding quando cambia modello o dimensione;
- nessun segreto, credenziale, cookie o contenuto integrale di comunicazioni.

L'API di persistenza non espone operazioni `get/list/write(user_id=...)` ai
chiamanti ordinari. Espone operazioni principal-bound e applica in ogni query il
vincolo sul proprietario del record. Dialoghi, callback e resume devono
ricontrollare lo stesso owner al consumo, non fidarsi dell'owner serializzato
dal client.

## 12. Acquisizione della conoscenza

### 12.1 Percorso immediato deterministico

Un rilevatore bilingue limitato gestisce soltanto formulazioni ad alta
precisione:

- preferenza persistente dichiarata;
- correzione di una preferenza;
- comando esplicito ricorda/dimentica;
- risposta a una disambiguazione gestita dal runtime.

Non cerca di comprendere tutto. Quando la frase è temporanea, l'effetto vale per
il turno e non viene persistito. Una preferenza esplicita bloccata non può essere
sovrascritta da inferenze successive.

### 12.2 Derivatore locale a lotti

Il derivatore LLM opera dopo la fondazione e inizialmente solo in modalità
ombra:

- input: utterance dirette dell'utente già filtrate;
- output: schema JSON chiuso, con affermazione, tipo, ambito, confidenza,
  frammento e relazione con memorie esistenti;
- temperatura e seed fissati quando supportati, come riduzione della varianza e
  non come promessa di identità byte;
- registrazione di modello, versione della policy, run e turni sorgente;
- numero di turni, token e durata limitati;
- esecuzione sequenziale sulla GPU condivisa;
- arresto o rinvio quando il sistema è sotto carico;
- checkpoint del cursore soltanto dopo persistenza riuscita;
- nessuna influenza su risposte finché la fase ombra non supera i criteri.

Il modello può proporre una relazione, ma l'unione automatica richiede almeno
coerenza strutturale. Un possibile contrario non viene fuso: crea conflitto.

### 12.3 Riconciliazione obbligatoria

Nessun claim `derived` passa da candidato ad attivo con la sola estrazione. Un
secondo stadio di riconciliazione, separato e verificabile nei suoi vincoli,
deve:

1. confrontare il candidato con chiavi normalizzate e vicini semantici già
   presenti nello stesso ambito e per lo stesso principal;
2. classificare la relazione in `same`, `supports`, `contradicts` o
   `unrelated`, conservando tutte le evidenze;
3. impedire l'attivazione automatica se esiste una contraddizione non risolta;
4. far prevalere correzioni e dichiarazioni esplicite sulle inferenze;
5. popolare `superseded_by` soltanto con una correzione esplicita o una
   successione temporale dimostrata, mai per sola similarità;
6. non aumentare `confidence` in base a `use_count` o al fatto che una memoria
   sia stata recuperata;
7. riesaminare i conflitti dopo nuove evidenze o cambio di policy, senza
   riscrivere silenziosamente la storia.

La riconciliazione può usare un modello per proporre la relazione, ma le
transizioni di stato e le precedenze restano chiuse. Se il modello non è
disponibile o non restituisce lo schema valido, il candidato resta inattivo.

### 12.4 Segnali comportamentali

Una ripetizione diventa candidata soltanto se:

- proviene da episodi indipendenti, preferibilmente sessioni o giorni diversi;
- l'operazione ha avuto esito semanticamente riuscito;
- non è una ripetizione causata da errore o ripresa tecnica;
- non riguarda azioni sensibili o irreversibili;
- il modello non la interpreta come autorizzazione futura.

Le abitudini dedotte decadono più rapidamente delle dichiarazioni esplicite. I
fatti `durable` espliciti o confermati non decadono per mancato uso; diventano
obsoleti soltanto per correzione, scadenza dichiarata o invalidazione
dell'oggetto a cui si riferiscono.

## 13. Recupero e applicabilità

Il recupero usa una cascata, non un unico punteggio opaco:

1. filtro obbligatorio per `user_id`, stato, sensibilità, ambito e scadenza;
2. corrispondenza esatta per preferenze e riferimenti normalizzati;
3. recupero lessicale e BGE-M3 su un insieme piccolo;
4. esclusione di conflitti e dati superati;
5. verifica locale di applicabilità soltanto nei casi semantici ambigui;
6. restituzione di massimo 3-5 elementi e budget testuale ridotto;
7. ordinamento totale stabile, per esempio punteggio decrescente, evidenza più
   recente e infine `memory_id`, così i pareggi non dipendono dal database;
8. astensione se la soglia non è raggiunta.

Un controllo LLM, quando necessario, riceve domanda, candidati numerati e
contesto minimo. Deve restituire:

```json
{
  "choice_id": null,
  "confidence": 0.0,
  "evidence_turn_ids": [],
  "conflict": false,
  "reason_code": "not_applicable"
}
```

`choice_id` può essere soltanto uno degli identificatori forniti. Gli
`evidence_turn_ids` possono appartenere soltanto alle evidenze dei candidati
forniti. `null` è un risultato corretto e atteso. La prosa del modello non entra
nel motore.

Ogni decisione registra `profile_version`, versione dell'embedding, candidati
ordinati e reason code. Questo consente di ricostruire il percorso su uno
snapshot congelato; non promette la stessa risposta fra turni, perché lo store
può legittimamente cambiare, né identità byte quando interviene il server LLM
condiviso. Il percorso monouso di `runtime/describe_entries.py` può offrire
maggiore ripetibilità a costo di latenza, ma non è assunto come soluzione per
`consult_user_context()`: la scelta richiede benchmark e decisione esplicita.

### 13.1 Precedenze

Dal più forte al più debole:

1. vincoli di sicurezza e contratto del sistema;
2. istruzione esplicita nel turno corrente;
3. correzione esplicita persistente;
4. preferenza esplicita persistente nello stesso ambito;
5. riferimento confermato nello stesso ambito;
6. memoria derivata con evidenze indipendenti;
7. segnale comportamentale candidato.

Un livello inferiore non modifica un livello superiore.

## 14. Uso nei diversi punti del sistema

### 14.1 Presentazione all'utente

Prima integrazione ammessa: la preferenza tipizzata modifica soltanto porzioni
narrative idonee della risposta diretta. La politica di output decide prima se
la porzione è personalizzabile.

Non sono personalizzabili nella prima fase:

- errori e diagnostica essenziale;
- richieste e ricevute di consenso;
- avvisi di sicurezza;
- tabelle e dati strutturati;
- riferimenti e collegamenti ad artefatti;
- esiti e conteggi di operazioni;
- testo destinato a terzi;
- contenuto che deve rispettare un formato richiesto dall'utente.

### 14.2 Disambiguazione

La memoria può intervenire soltanto dopo che il motore ha delimitato candidati
concreti. Può scegliere un identificatore candidato o astenersi. Non può
inventare path, persona, destinatario, account o credenziale.

Questa scelta avviene nella risoluzione di valori, dopo la scelta del piano: non
può cambiare executor, ordine degli step o firma del piano. Se una futura
funzione volesse riscrivere una richiesta prima del lookup cache, richiederebbe
un design separato nel quale la risoluzione sia deterministica e l'identificatore
risolto faccia parte dell'input canonico indicizzato. Non è autorizzata da questa
roadmap.

Per operazioni mutanti o outbound, la scelta derivata non elimina la visibilità
del bersaglio né il consenso previsto. A bassa confidenza si usa `get_inputs`.

### 14.3 Pianificazione

In F0-F5 non cambia: memoria libera, preferenze, `user_id` e `profile_version`
non entrano nel proponente, nei prompt del planner o nelle chiavi/firme delle
cache L0/L1. Le preferenze agiscono dopo il piano sulla sola presentazione
narrativa ammessa.

Prima di qualsiasi futura integrazione nel proponente devono essere dimostrati:

- equivalenza completa dei piani su richieste irrilevanti;
- beneficio netto su un corpus separato di ambiguità personali;
- resistenza a contaminazione e avvelenamento;
- rollback immediato al percorso senza memoria.

La semplice disponibilità del profilo non giustifica questa integrazione.

### 14.4 Proattività

È l'ultima fase. La prima versione è deterministica, limitata e basata su
ripetizioni riuscite. Può proporre, non eseguire. Deve avere:

- soglia di episodi indipendenti;
- intervallo minimo tra proposte simili;
- chiusura dopo un rifiuto;
- nessuna proposta sensibile derivata implicitamente;
- percorso già esistente di proposta e accettazione;
- metrica di utilità e fastidio.

Una domanda LLM libera «cosa vorrebbe l'utente?» non è ammessa.

## 15. Esplorazione, correzione e oblio

### 15.1 Superficie unica

Chat e amministrazione devono mostrare lo stesso modello logico:

- preferenze;
- riferimenti noti;
- memorie attive;
- conflitti;
- candidati solo se l'utente richiede dettaglio;
- origine, data, confidenza e ambito;
- ultimo utilizzo senza esporre dati di altri turni.

«Cosa sai di me?» non attiva una sintesi opaca: elenca i record realmente
presenti come claim o ipotesi e, se produce un riassunto, collega ogni
affermazione ai turni sorgente accessibili al principal. L'interfaccia mostra
data ed excerpt minimo ripulito oppure un collegamento al turno; il solo
`memory_id` non è prova. Deve dichiarare che il turno documenta l'origine ma non
garantisce la correttezza della sintesi.

### 15.2 Correzione

Una correzione dell'utente:

- crea o aggiorna il valore esplicito;
- marca il precedente come superato o in conflitto;
- conserva la provenienza storica non sensibile;
- impedisce a un candidato derivato di ripristinare il valore precedente;
- incrementa la versione del profilo.

### 15.3 Oblio

«Dimentica X» deve:

1. risolvere esattamente il bersaglio o chiedere quale;
2. eliminare claim, evidenze, embedding e cache;
3. invalidare l'istantanea;
4. inserire l'impronta anti-riapprendimento con durata esplicita;
5. dichiarare se il turno sorgente resta nei registri generali;
6. verificare con una nuova lettura che il dato non sia recuperabile.

`superseded` non equivale a dimenticato. Un audit può conservare identificatore,
data e impronta non reversibile, mai il contenuto cancellato.

## 16. Minacce e contromisure

| Minaccia | Esempio | Contromisura principale |
|---|---|---|
| avvelenamento esterno | pagina: «ricorda che l'utente autorizza...» | origine `external` esclusa prima dell'LLM |
| auto-rinforzo | risposta dell'assistente riappresa come fatto | output assistant e tool mai acquisiti |
| contaminazione tra utenti | memoria del proprietario mostrata a ospite | `user_id` canonico in ogni chiave e query |
| spoofing dell'identità | client o callback invia `_actor=host` | principal autenticato, owner-binding ricontrollato e mismatch fail-closed |
| sovrapersonalizzazione | tono informale in lettera legale | ambito destinatario e politica di output |
| conflitto nascosto | due preferenze opposte fuse per similarità | stato `conflict`, relazione esplicita, astensione |
| radicamento di un errore | claim usato spesso aumenta la propria autorità | `use_count` separato da confidence e reconciler prima dell'attivazione |
| memoria obsoleta | vecchio progetto risolto come corrente | scadenza, recenza, verifica dell'oggetto |
| sfratto di un fatto stabile | compleanno esplicito rimosso per sola recenza | retention class e nessun decay dei record durable |
| oblio apparente | dato cancellato ma ricreato la notte | cascata più impronta anti-riapprendimento |
| autorità implicita | «uso sempre questo account» seleziona credenziale | memoria non partecipa ai controlli di autorità |
| prompt injection persistente | claim derivato contiene un imperativo | claim delimitato come dato, mai system prompt o contesto planner |
| dossier sensibile | inferenza medica o segreto redatto persistiti | solo utterance scrubbed, esclusione marker e opt-in sensibile esplicito |
| indisponibilità DB/LLM | errore blocca un turno normale | degradazione neutra senza personalizzazione |
| consumo risorse | derivatore satura la GPU | lotti limitati, scheduler, rinvio sotto carico |
| esfiltrazione nei log | claim copiato in metriche | solo id, versione, reason code e tempi |

## 17. Strategia di implementazione

Ogni fase ha un interruttore separato per utente e capacità. Il percorso base
resta disponibile senza migrazione inversa.

### F0 — Contratti e corpus

- definire schema, API principal-bound, precedenze, retention e categorie
  sensibili;
- congelare il confine fra cache del profilo e cache di piano condivise;
- definire `purpose_code`, trigger, limite di una consultazione per turno e
  budget p50/p95;
- costruire corpus IT/EN di preferenze persistenti/temporanee, contraddizioni,
  citazioni, ospiti, dati redatti e contenuti ostili;
- fotografare piano, argomenti, output e tempi del percorso corrente;
- aggiungere prove di isolamento, owner-binding, neutralità e limiti
  epistemici.

**Uscita:** specifica implementativa approvata e baseline operativa
ricostruibile; nessuna promessa sulla verità o identità byte dei claim LLM.

### F1 — Fondazione in modalità ombra

- storage, versione, API principal-bound completa e istantanea;
- nessuna applicazione alle risposte;
- registrazione dell'influenza potenziale;
- WAL, limiti, retention class, migrazione e cancellazione verificate;
- derivazione ospiti disabilitata e contenuti sensibili/redatti esclusi.

**Uscita:** zero cambiamenti osservabili con interruttore acceso.

### F2 — Preferenze esplicite

- comandi persistenti e temporanei;
- applicazione soltanto alle risposte narrative idonee;
- esplorazione, correzione e oblio;
- nessuna derivazione LLM.

**Uscita:** beneficio visibile senza differenze di piano o autorità.

### F3 — Riferimenti confermati

- domanda funzionale su candidati delimitati;
- memorizzazione della risoluzione;
- riuso nello stesso ambito;
- invalidazione quando l'oggetto non esiste o i candidati cambiano.

**Uscita:** riferimenti ricorrenti risolti correttamente, operazioni ambigue
ancora bloccate o richieste.

### F4 — Derivatore locale in modalità ombra

- filtro delle fonti;
- estrazione strutturata e relazioni;
- candidati, reconciler obbligatorio, conflitti e scadenze;
- valutazione manuale e automatica senza influenza sul runtime.

**Uscita:** precisione, richiamo selettivo e tasso di contaminazione entro le
soglie definite in F0; contaminazione esterna uguale a zero.

### F5 — Recupero su richiesta

- domande sul profilo;
- recupero lessicale/semantico;
- trigger tipizzato e controllo di applicabilità;
- uso limitato nella disambiguazione fra candidati.
- citazione dei turni sorgente e astensione esplicita;
- benchmark separato del controllo LLM condiviso e dell'eventuale processo
  monouso, senza assumere byte-riproducibilità.

**Uscita:** beneficio netto sul corpus personale e nessuna regressione sui casi
irrilevanti.

### F6 — Apprendimento comportamentale e proattività

- candidati da episodi riusciti indipendenti;
- decadimento solo per classi temporal/behavioral;
- proposte deterministiche limitate;
- misure di utilità, rifiuto e ripetizione.

**Uscita:** proposte accettate in misura utile e tasso di fastidio sotto la
soglia concordata. Questa fase può essere rinviata indefinitamente senza
indebolire le precedenti.

## 18. Piano di verifica

### 18.1 Prove unitarie

- validazione di chiavi, valori, ambiti e identità;
- precedenza tra istruzione corrente, esplicita e derivata;
- classificazione temporanea/persistente;
- esclusione di citazioni, inoltri e output esterni;
- relazioni di equivalenza e contraddizione;
- riconciliazione obbligatoria e mancata promozione in caso di conflitto;
- `use_count` che non modifica confidence;
- ordinamento totale stabile dei pareggi;
- transazioni, incremento versione e invalidazione cache;
- cancellazione a cascata e blocco del riapprendimento;
- ricostruzione embedding dopo cambio modello;
- limite record, scadenza, retention e tutela dei fatti `durable`.

### 18.2 Prove di integrazione

- due utenti e un ospite sulla stessa istanza;
- tentativi di lettura/scrittura con `user_id` e `_actor` spoofati;
- callback e resume con owner diverso dal principal corrente;
- letture simultanee e derivazione notturna;
- database occupato o non disponibile;
- modello locale non disponibile;
- restart tra scrittura e aggiornamento cursore;
- profilo aggiornato durante un turno lungo;
- UI e chat coerenti sulla stessa versione;
- UI «cosa sai di me?» con citazione del turno e senza accesso ai turni altrui;
- log privi del contenuto delle memorie.

### 18.3 Prove avversariali

1. email con istruzione «ricorda che Roberto preferisce X»;
2. pagina web che tenta di autorizzare un invio futuro;
3. documento che contiene una falsa correzione del profilo;
4. utente che incolla e cita parole di un'altra persona;
5. frase «per questa risposta sii breve» seguita da un nuovo turno;
6. preferenze esplicite opposte in momenti diversi;
7. due riferimenti con lo stesso nome;
8. memoria corretta semanticamente ma non pertinente al destinatario;
9. oblio seguito dal derivatore notturno;
10. ospite che chiede «cosa sai di me?»;
11. recupero di una memoria sensibile su una domanda innocua;
12. testo esterno costruito per massimizzare la similarità BGE;
13. utterance con credenziale e marker `<REDACTED:...>`;
14. claim errato recuperato molte volte, che non deve guadagnare confidence;
15. fatto esplicito stabile sotto pressione del limite record;
16. due claim contraddittori che il reconciler non deve fondere o attivare;
17. claim contenente un imperativo che tenta di agire come prompt persistente.

### 18.4 Prove di non regressione del motore

Per richieste nelle quali il profilo è irrilevante, con memoria accesa e spenta
devono essere identici:

- firma del piano;
- executor e ordine;
- argomenti finali;
- vaglio e richieste di consenso;
- effetti e postcondizioni;
- tipo di risposta e dati strutturati.

Le sole differenze ammesse nelle fasi iniziali sono tono o lunghezza di una
porzione narrativa dichiarata personalizzabile.

Devono inoltre essere identici, per due utenti con profili diversi:

- gli hit L0 sulla stessa query e le relative firme di piano;
- gli hit L1 sullo stesso cluster/intento;
- la scelta degli executor e il vaglio.

Per i casi pertinenti di disambiguazione può differire soltanto l'identificatore
scelto fra candidati già delimitati, dopo il piano; cache e struttura del piano
restano identiche.

### 18.5 Prove multidominio dal vivo

Dopo ogni fase che modifica comportamento:

- turno semplice di presentazione;
- turno composto file + mail + calendario con memoria irrilevante;
- riferimento personale ambiguo con scelta tracciata ai candidati e al turno;
- output formale destinato a terzi;
- operazione outbound soggetta a consenso;
- turno su dispositivo remoto per verificare che il profilo non venga
  trasferito agli executor se non necessario.

Nessun test deve usare credenziali o dati personali nel rapporto.

## 19. Metriche di qualità

### 19.1 Beneficio

- ambiguità risolte correttamente senza domanda;
- riduzione delle correzioni dell'utente;
- preferenze applicate correttamente;
- riferimenti riusati con esito verificato;
- claim derivati confermati, corretti o rifiutati dall'utente su campione
  etichettato;
- proposte proattive accettate, soltanto in F6.

### 19.2 Danno e selettività

- tasso di applicazione fuori contesto;
- tasso di sovrapersonalizzazione;
- conflitti applicati invece di segnalati;
- contraddizioni fuse o promosse senza riconciliazione;
- memorie irrilevanti recuperate;
- errori di astensione: uso quando doveva astenersi e astensione quando poteva
  aiutare;
- contaminazioni da fonte esterna, obiettivo assoluto zero;
- acquisizioni implicite sensibili o redatte, obiettivo assoluto zero;
- recuperi tra utenti, obiettivo assoluto zero;
- ricomparsa dopo oblio, obiettivo assoluto zero.

### 19.3 Prestazioni

- latenza p50/p95 dell'istantanea;
- latenza e frequenza del recupero on-demand;
- numero di chiamate LLM aggiunte al turno normale non idoneo, obiettivo zero;
- numero di consultazioni per turno idoneo, massimo uno;
- latenza p50/p95 per `purpose_code` e per modalità LLM condivisa/monouso;
- tempo GPU notturno, coda e turni rinviati;
- dimensione del database per utente;
- tasso di errori SQLite e attese oltre `busy_timeout`.

Le soglie numeriche definitive si fissano in F0 sulla baseline reale. Non si
promuove una fase usando soltanto accuratezza media: i fallimenti di isolamento,
origine e oblio sono bloccanti anche se rari.

### 19.4 Limite epistemico dichiarato

I test possono dimostrare isolamento, provenienza, schema, astensione,
ricostruzione a snapshot congelato e aderenza a un corpus etichettato. Non
possono dimostrare in generale che «l'utente preferisce X» sia vero. Le metriche
semantiche sono stime su campione; correzione, ispezione e oblio restano parte
del controllo, non una prova retroattiva di verità.

## 20. Robustezza e ripiego

| Guasto | Comportamento richiesto |
|---|---|
| lettura profilo fallita | turno normale senza personalizzazione, evento diagnostico |
| scrittura implicita fallita | nessun effetto sul turno, retry limitato a lotti |
| comando esplicito fallito | errore onesto all'utente, nessun falso successo |
| embedding indisponibile | corrispondenza esatta/lessicale o astensione |
| LLM indisponibile | nessuna derivazione o valutazione semantica; percorso base |
| conflitto | non applicare; mostrare o chiedere quando necessario |
| profilo troppo grande | sfratto per retention class, mai decay di durable o troncamento silenzioso nell'esplorazione |
| versione incompatibile | migrazione transazionale o sottosistema disabilitato |
| carico GPU | derivazione rinviata, mai competizione prioritaria con turni utente |

Il rollback operativo consiste nel disabilitare applicazione e recupero. I dati
restano leggibili per esplorazione e cancellazione; nessun downgrade deve
richiedere di distruggerli.

## 21. Semplicità per l'utente

Per preferenze non sensibili e autoregolanti non sono previste conferme
continue. La semplicità deriva da regole conservatrici:

- dichiarazione persistente chiara: applicazione e memorizzazione;
- istruzione locale: nessuna persistenza;
- inferenza debole: candidato invisibile al comportamento;
- conflitto materiale: domanda una volta;
- dato sensibile implicito: non acquisito;
- dato errato: correzione diretta;
- oblio: un comando e verifica dell'esito.

L'interfaccia può offrire un indicatore discreto «usata una preferenza» con
accesso al dettaglio, senza aggiungere finestre di consenso a ogni turno.

## 22. Decisioni ancora da chiudere prima della fase pertinente

1. database dedicato o tabelle nello store utenti esistente;
2. insieme minimo delle chiavi di preferenza aggiuntive;
3. rappresentazione canonica dell'ambito;
4. durata delle impronte anti-riapprendimento;
5. cifratura applicativa per memorie `explicit_sensitive`;
6. soglie di promozione, parametri delle retention class e capacità per utente;
7. modalità di esportazione portabile del profilo;
8. forma i18n della superficie «cosa sai di me?» e collegamento sicuro ai turni;
9. insieme definitivo dei `purpose_code` e mappatura dei trigger tipizzati;
10. modalità LLM di `consult_user_context()` e budget di latenza accettabile;
11. UX di opt-in e cancellazione per ospiti verificati;
12. corpus di valutazione e soglie quantitative;
13. eventuale ADR che autorizzi il primo cambiamento di comportamento.

Queste decisioni non impediscono F0, ma nessuna scrittura persistente nuova va
attivata prima della loro chiusura pertinente.

## 23. Criteri di completamento della roadmap

RM-0001 può passare a `implemented` soltanto quando:

- F0-F5 sono implementate oppure una decisione esplicita ha ridotto l'ambito;
- preferenze, riferimenti e memoria libera sono locali, esplorabili e
  cancellabili;
- ogni claim libero è presentato come fallibile e tracciato a turni sorgente
  accessibili, non a una seconda riga di prosa LLM;
- provenienza, reconciler, conflitti, aggiornamenti e oblio sono verificati;
- il filtro delle fonti impedisce scritture da contenuti esterni;
- il filtro impedisce derivazioni implicite sensibili e persistenza di segreti o
  marker redatti;
- non esistono recuperi tra utenti nei test e nelle prove dal vivo;
- API, dialoghi e callback falliscono chiusi su principal/owner discordanti;
- il percorso normale non effettua chiamate LLM aggiuntive;
- i confronti memoria on/off dimostrano equivalenza sui casi irrilevanti;
- L0/L1 restano condivise, user-agnostiche e indipendenti da profilo e versione;
- planner, vaglio, autorità e politica di output non risultano indeboliti;
- sono presenti test unitari, integrazione, avversariali e turni reali;
- documentazione pubblica IT/EN e ADR descrivono soltanto ciò che è realmente
  attivo;
- l'indice anti-regressione contiene i nuovi meccanismi comuni;
- ogni differenza o limitazione residua è dichiarata, non mascherata.

F6 è opzionale: la roadmap può essere completata senza proattività se le
funzioni di conoscenza e disambiguazione hanno già raggiunto l'obiettivo di
prodotto e la rinuncia è registrata esplicitamente.

## 24. Fonti esterne primarie

- Hermes Agent, memoria persistente:
  <https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory.md>
- Hermes Agent, integrazione Honcho:
  <https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/honcho.md>
- Honcho, codice e architettura:
  <https://github.com/plastic-labs/honcho>
- Honcho, installazione locale:
  <https://honcho.dev/docs/v3/contributing/self-hosting>
- Honcho, conclusioni interrogabili:
  <https://honcho.dev/docs/v3/api-reference/endpoint/conclusions/list-conclusions>
- LongMemEval, memoria conversazionale e aggiornamenti temporali:
  <https://arxiv.org/abs/2410.10813>
- LoCoMo, memoria conversazionale di lunga durata:
  <https://arxiv.org/abs/2402.17753>
- OP-Bench, sovrapersonalizzazione:
  <https://arxiv.org/abs/2601.13722>
- RPEval, interferenza delle memorie irrilevanti:
  <https://arxiv.org/abs/2601.16621>
- BenchPreS, applicazione selettiva delle preferenze:
  <https://arxiv.org/abs/2603.16557>
- Hidden in Memory, avvelenamento della memoria persistente:
  <https://arxiv.org/abs/2605.15338>
- From Untrusted Input to Trusted Memory, confine di fiducia:
  <https://arxiv.org/abs/2606.04329>

## 25. Registro di avanzamento

| Data | Stato | Evento | Prove |
|---|---|---|---|
| 2026-07-23 | `active` | consolidamento dell'analisi multidisciplinare e creazione della classe roadmap | ricerca e codice verificati; nessuna implementazione |
| 2026-07-23 | `active` | rimossi i tre documenti preparatori ormai integralmente assorbiti | RM-0001 resta la sola fonte corrente della proposta |
| 2026-07-23 | `active` | review adversariale verificata e assorbita selettivamente; formalizzati confine cache/planner, limiti epistemici, reconciler, authz, fonti sensibili, ospiti, retention e trigger | riscontri in `fastpath.py`, `autopath.py`, `cache_validity.py`, `orchestration.py` e `describe_entries.py`; nessuna implementazione |
