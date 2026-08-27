# RM-0008 gruppo 3 — piano ottimizzato

Il gruppo 2 ha lasciato un insieme di autorità **predisposto e inerte**. Il
gruppo 3 lo rende **attivo**: è il gruppo che accende il controllo di nascita.

Questo piano nasce da una richiesta esplicita di Roberto (27/8/2026):
ottimizzare il lavoro rimanente e provare soltanto ciò che serve allo scopo del
gruppo. Le stime di costo qui sotto sono misurate sul gruppo 2, non ipotizzate.

## 1. Che cosa il gruppo 3 rivendica

Una sola frase: **l'insieme predisposto diventa l'autorità che governa la
nascita di un executor, e l'identità del contesto cambia quando cambia una
politica.**

Tutto ciò che non serve a sostenere quella frase non è lavoro del gruppo 3.

## 2. Obblighi ereditati (fonte: §9.3 e sparsi nel rapporto del gruppo 2)

1. invocare davvero il linter sul manifest congelato;
2. controllo AST e risoluzione degli import sulla mappa chiusa dei file e sugli
   involucri dei 21 executor;
3. risoluzione chiusa di modelli e primitive delle proprietà;
4. su Linux sostituire `shutil.which("bwrap")` e `sys.executable` non
   autenticati col registro predisposto; completare il legame Windows;
5. installare i registri di autorità nel bundle privato e **dimostrarne il
   consumo**;
6. portare a `productive` ogni `enforcement_state` interessato, ricostruire
   identificativo ed epoca, e provare che cambiare ciascuna politica cambia
   **sia comportamento sia identità**;
7. analizzare import statici e caricamenti dinamici noti, e fallire se un file
   locale eseguito non appartiene allo snapshot o a una dipendenza chiusa;
8. migrare il bootstrap al pubblicatore sigillato e installare la vista pubblica
   come istantanea del bundle;
9. fornire la tabella chiusa per `ContractId` e attivare le fabbriche Producer;
10. creare le basi dati di ricevute e approvazioni sotto `PATH_USER_STATE/birth`;
11. sostituire il decodificatore libero del contesto **nello stesso incremento
    atomico** che installa il bootstrap sigillato;
12. riaprire e riconvalidare autore, insieme, registri e materiale sotto la
    propria barriera prima di qualunque attivazione: `prepared_not_active` non
    si traduce in `active`.

## 3. Le quattro ottimizzazioni

### 3.1 Congelare una volta sola, alla fine

Ogni modifica alla base congelata costa un ciclo completo: prodotto indietro,
pubblicazione, fotografia, ripristino, pubblicazione, verifica. Nel gruppo 2 ne
sono serviti **dieci**, quasi tutti perché la base veniva emendata appena serviva.

**Regola:** implementare tutto, tenere le modifiche alla base in coda, emendarla
una volta e scattare **una** fotografia alla fine dell'incremento. Costo atteso:
1 ciclo invece di 6.

### 3.2 Provare il proprio contratto, non quello altrui

La primitiva a handle, il giornale, i documenti canonici e la disposizione sono
già certificati dal gruppo 2 e non vanno riesercitati. Le celle del gruppo 3
partono dall'insieme già predisposto — che l'entrata del gruppo 2 sa creare in
una radice isolata — e verificano soltanto attivazione, consumo e identità.

### 3.3 Le due prove che pagano davvero

Misurato sul gruppo 2, i difetti veri li hanno trovati due sole cose:

- **la cella del grafo produttivo (R1)**: ha respinto due volte una porta nel
  posto sbagliato, e aveva ragione entrambe;
- **attraversare la cosa vera invece di simularla**: ha scoperto che su Windows
  una radice storica non era leggibile affatto.

Equivalenti per il gruppo 3: (a) estendere R1 perché dichiari **esattamente
quali chiamanti** possono raggiungere il pubblicatore e le fabbriche, e (b) una
nascita reale di un executor attraverso il bundle attivato, non uno stub.

Il resto delle celle serve a non regredire, non a scoprire.

### 3.4 Nessuno strumento diagnostico senza una decisione che dipende da esso

Nel gruppo 2 tre strumenti su tre hanno risposto per conto proprio prima di dire
la verità: una sonda che si fermava su un'altra cosa, una lettura del token
troncata a 32 bit, un passo diagnostico che non veniva nemmeno eseguito. Uno
strumento si costruisce solo quando la sua risposta cambia una decisione, e
deve dichiarare **come** ha ottenuto la risposta.

## 4. Ordine che minimizza il rilavoro

L'ordine non è libero: il punto 6 (portare le politiche a `productive`) cambia
identificativo ed epoca del contesto, quindi **ogni vettore golden va rifatto**.
Farlo presto significa rifarlo a ogni passo.

1. barriera di riconvalida (12) e migrazione del bootstrap (8, 11) — nessun
   cambio di identità;
2. tabella chiusa `ContractId` e fabbriche Producer (9), basi dati (10);
3. registri consumati davvero (5), linter e controlli chiusi (1, 2, 3, 7),
   registro sandbox (4) — il comportamento diventa reale, l'identità non ancora;
4. **ultimo**: portare a `productive` gli `enforcement_state` corrispondenti (6),
   ricostruire identificativo ed epoca, rifare i vettori golden una volta sola,
   e provare che toccare una politica muove entrambe le cose.

## 5. Fuori dal gruppo 3, dichiarato

Autenticità della distribuzione, protezione fra utenti sullo stesso host,
servizio Windows definitivo, archivio a freddo: gruppi 4-6. Anche i tre
requisiti che il gruppo 2 ha dichiarato non provati restano dichiarati finché
qualcuno non li assegna.

## 6. Criterio di uscita

Come per il gruppo 2: una tabella riempita cella per cella con simbolo
produttivo, prova di modulo, prova di integrazione, prova installata ed esito.
Una cella rinviata dice `N/A`, il gruppo che la possiede e il motivo. Il verde
di una prova che non attraversa il simbolo produttivo non riempie la colonna
della prova installata.


## 6.bis Mappa di migrazione del bootstrap

Lette le voci che `_assemble_birth_core` riceve oggi da `_build`, ecco che cosa
diventa ciascuna sotto la costruzione sigillata. La colonna «oggi» dice da dove
arriva ora; quasi tutte arrivano da `bootstrap.json`, cioe' da un file di
configurazione che sceglie fatti autorevoli — ed e' esattamente cio' che il
§23.8 vieta.

| Ingresso del nucleo | Oggi | Sotto la costruzione sigillata |
|---|---|---|
| `producer_registry`, `producer_db` | voci `producers` del file, con `origin` e `author` scelti li' | archivi Producer dell'insieme predisposto + tabella chiusa `ContractId` |
| `context_resolver`, `context_epoch_resolver` | `_context_builder(value["context"], …)`, undici componenti scelti dal file | fabbrica interna sul materiale predisposto; il decodificatore libero sparisce **nello stesso passaggio** |
| `approval_resolver` | registro indicato dal file | registro copiato nell'insieme, riletto sotto la barriera |
| `shadow_dependencies` | autorita' semantica indicata dal file | autorita' semantica dell'insieme |
| `admission_*` | archivio indicato dal file | archivio Admission dell'insieme |
| `postcondition_verifier` | `trusted_publics` dal file | anello pubblico dell'archivio autore |
| `publisher_options` | `{"trusted_publics": …}` | **sparisce**: il pubblicatore sigillato possiede la primitiva e l'anello (§5.3) |

**Due fatti che oggi vengono dalla configurazione e non hanno ancora una casa
chiusa**, da decidere prima di finire la migrazione:

1. `policy_version` — stringa scelta dal file. Deve venire da una costante
   posseduta dal codice, altrimenti la configurazione continua a scegliere un
   fatto autorevole.
2. `receipt_ttl_seconds` — stesso problema in forma minore: e' un parametro
   operativo, ma governa la validita' di una ricevuta.

**Decise con Roberto il 27/8/2026: entrambe diventano costanti possedute dal
codice**, in `runtime/executor_birth_policy_v1.py`. La versione della politica e'
una proprieta' del codice che decide, non dell'installazione che lo esegue: si
muove quando si muovono le regole ed e' uguale su ogni macchina. La durata
delle ricevute porta con se' il proprio campo di validita' dichiarato, cosi' una
futura preferenza firmata avra' dove sedersi e un valore fuori campo sara' un
difetto invece che una sorpresa.

## 6.ter La fabbrica del contesto va spostata, non duplicata

Il §9.4 impone che il gruppo 3 **non si fidi** del materiale registrato: deve
riaprire le sorgenti installate, ricostruirlo e confrontare ogni digest sotto la
propria barriera. Il codice che lo ricostruisce esiste gia', ma vive in
`install/birth_authority_provisioner.py`, e un modulo di runtime non puo'
importarlo (la cella R1 vieta al runtime di raggiungere il predispositore).

Duplicarlo sarebbe la scelta peggiore: due implementazioni dello stesso digest
divergono, e il giorno che divergono nessuno se ne accorge finche' un'identita'
non cambia da sola.

**Da fare:** spostare catalogo V1 e fabbrica in un modulo di runtime — per
esempio `runtime/executor_birth_context_v1.py` — e farlo importare dal
predispositore (install → runtime e' ammesso). La fabbrica **riceve una
sessione gia' aperta** sulla distribuzione e non ne apre nessuna, cosi' le porte
ammesse restano due: quella dell'installatore e quella di sola lettura del
runtime. Chi apre la sessione e' il chiamante, che quell'autorita' ce l'ha gia'.

Nota di esecuzione: il blocco da spostare e' ~190 righe e va spostato a mano,
non con sostituzioni testuali — un tentativo meccanico si e' rivelato fragile e
lasciarlo a meta' sarebbe peggio che non iniziarlo.

## 6.quater Provenienza: la tabella degli undici, e cosa dice davvero

Il file di configurazione sceglie oggi, per ognuno degli undici produttori, due
fatti che finiscono timbrati sull'executor: **tipo** (`ExecutorOrigin`) e
**autore della revisione** (`RevisionAuthor`). Scriverli in una tabella chiusa
sembrava la mossa ovvia. Guardando gli undici uno per uno, non lo e'.

| Produttore | Operazione | Che cosa fa | Tipo (`origin`) | Autore |
|---|---|---|---|---|
| `synt_multistage` | `create_or_replay` | crea un executor nuovo | sintetizzato | modello |
| `synt_specialize` | `specialize_or_replay` | crea un fratello specializzato | sintetizzato | modello |
| `synt_approve` | `approve_or_replay` | approva un candidato sintetizzato | sintetizzato | modello |
| `skills_cli` | `skill_import_or_reactivation` | importa da fuori | importato | importatore |
| `builtin_contract_generator` | `generate_builtin` | genera i contratti builtin | builtin | manutenzione |
| `installer_phase3` | `install` | installa i contratti spediti | **dipende dal bersaglio** | manutenzione |
| `change_applier` | `extend` | revisiona un executor esistente | **dipende dal predecessore** | modello |
| `change_rollback` | `rollback` | annulla una modifica | **dipende dal predecessore** | manutenzione |
| `promoter` | `promote` | promuove un executor esistente | **dipende dal predecessore** | manutenzione |
| `promoter` | `rollback` | annulla una promozione | **dipende dal predecessore** | manutenzione |
| `stack_reconcile` | `restart_sign_first` | rifirma al riavvio | **dipende dal predecessore** | manutenzione |

**Sei righe su undici non hanno un tipo proprio.** Cinque revisionano un
executor che esiste gia', e per loro il tipo e' quello del predecessore.
L'installatore e' un caso a se': installa contratti di origine diversa (core,
builtin, skill utente) e su una installazione nuova **non esiste nemmeno un
predecessore** da cui prenderlo.

### Conseguenza

Il tipo non e' quasi mai una proprieta' del produttore: e' una proprieta'
**dell'executor che sta nascendo**. Esiste gia' autenticato in due forme —
l'origine strutturale del manifest, che l'inventario calcola da dove il
manifest vive, e l'origine del predecessore per chi revisiona.

**Proposta:**

- l'**autore** resta fisso per produttore, in tabella chiusa: dice chi ha
  scritto *questa* revisione, ed e' davvero una proprieta' di chi la produce;
- il **tipo** si deriva dall'executor: dal predecessore autenticato quando
  c'e', dall'origine strutturale del manifest quando si nasce da zero. Mai dal
  produttore, mai da un file.

Cosi' nessuno *dichiara* una provenienza: o la porta il predecessore, o la
porta il posto in cui il manifest vive. Il costo onesto: due sorgenti invece di
una, e in entrambi i casi va verificato che il dato sia gia' autenticato nel
punto in cui serve — nel flusso attuale lo e', ma va provato, non supposto.

### Decisione presa (27/8/2026)

Roberto ha scelto la forma «fisso tranne chi revisiona», poi ha chiesto di
preferire sempre l'opzione piu' semplice e robusta. Rileggendola con quel
criterio, la seconda sorgente e' sparita: **una revisione non sposta il
manifest**, quindi la posizione del manifest serve identica sia a chi crea sia
a chi revisiona.

Forma finale, in `runtime/executor_birth_producer_table_v1.py`:

- **autore** fisso per produttore, tabella chiusa di undici righe;
- **tipo** derivato da dove vive il manifest, e da nient'altro. Una posizione
  che non prevede nascite (`retired`) e' un rifiuto, non un valore indovinato.

Nessun produttore, chiamante o documento puo' aggiungere una seconda sorgente:
la firma della funzione ha un solo parametro e una prova lo verifica.

Lezione da tenere: la tabella fissa sembrava la scelta semplice, ma era falsa
per sei righe su undici. **Semplice-e-falso non e' semplice**; la versione
davvero semplice e' arrivata togliendo una sorgente, non aggiungendo un caso.

## 6.quinquies Lo scambio atomico, in tre sotto-passi obbligati

Il §9.1 vuole che il decodificatore libero del contesto sparisca **nello stesso
incremento** che installa il bootstrap sigillato, e il §5.3 vuole che il nucleo
riceva soltanto fatti. Sono due cambi che si toccano, e farne uno solo lascia
due percorsi di pubblicazione vivi contemporaneamente. Ordine obbligato:

1. **Il nucleo accetta un pubblicatore, non delle opzioni.** Oggi
   `_assemble_birth_core` riceve `publisher_options` e possiede la primitiva;
   deve invece ricevere il pubblicatore sigillato del 2E e chiamarlo con
   `BirthCommitFactsV1`. Finche' questo non e' fatto, il resto non ha dove
   attaccarsi.
2. **La costruzione sigillata dell'avvio.** `_build_sealed` monta il nucleo da
   `load_sealed_authorities_v1()`: registro emittenti dalle pubbliche Producer,
   chiavi Admission, autorita' di approvazione e semantica, anello autore, e il
   contesto **gia' ricostruito** sotto la barriera (non ricostruirlo di nuovo).
   Le basi dati di ricevute e approvazioni nascono sotto `PATH_USER_STATE/birth`.
3. **La rimozione, nello stesso commit del passo 2.** Spariscono
   `_read_config`, `_load_authorities`, `_context_builder` e la lettura di
   `bootstrap.json`. Non prima: un decodificatore libero ancora raggiungibile
   accanto a uno sigillato e' esattamente la doppia verita' che questo gruppo
   toglie.

Regola pratica gia' pagata una volta in questa sessione: **non iniziare uno
spostamento che non si finisce nel giro corrente.** Meglio un giro speso a
scrivere l'ordine che un albero lasciato a meta'.

## 6.sexies Il percorso di pubblicazione precedente non pubblica affatto

Verificato leggendo, non supposto. In `executor_birth_operational._execute` la
pubblicazione e':

```
core.publisher(ref, expected_generation_id=..., snapshot=..., request_id=...,
               birth_authorization=..., **dict(core.publisher_options))
```

`core.publisher` e' `contract_store.commit_birth_snapshot`, che richiede
`private_key` come argomento obbligatorio senza valore predefinito. Le
`publisher_options` che il bootstrap passa contengono soltanto
`trusted_publics`, e in tutto `executor_birth_bootstrap.py` **non compare mai
una chiave privata autore**: c'e' solo quella di Admission.

Conseguenza: quella chiamata solleverebbe un `TypeError` per argomento
mancante. Il percorso produttivo precedente **non e' incompleto in teoria: non
puo' pubblicare**. Coerente con lo stato dichiarato — il runtime Birth non e'
mai stato attivo, e un'installazione senza `bootstrap.json` non lo costruisce
nemmeno.

**Effetto sul piano.** I sotto-passi 2 e 3 del §6.quinquies non sono una
migrazione: sono la **prima** implementazione completa di quel percorso, e
la rimozione di uno che non ha mai funzionato. Il pubblicatore sigillato del
2E porta la chiave autore, l'anello e la primitiva; il vecchio non portava
nulla. Non c'e' comportamento da preservare, e questo toglie il rischio
principale dello scambio atomico.

## 7. Procedura di rifotografia (l'unica cosa tacita e costosa)

Serve ogni volta che cambia un file sotto `tests/portable/rm0008_2a_acceptance`,
`tests/windows_identity/rm0008_2a_acceptance`, o uno dei percorsi esatti
congelati (flusso di lavoro pubblico, `pytest.ini`, i due `conftest.py`, il
manifesto). L'inventario produttivo e' **escluso** dal sigillo e si rigenera
liberamente.

1. `SAVED=$(git rev-parse HEAD)` e annotarlo fuori dall'albero;
2. riportare il prodotto allo stato precedente la correzione:
   `git checkout f4512d04 -- runtime/ install/`; rimuovere dall'indice i file di
   prodotto nati dopo (oggi `install/birth_authority_provisioner.py`,
   `runtime/executor_birth_commit_publisher.py`) e le prove degli incrementi
   (`tests/portable/rm0008_2b/`), perche' fanno parte della correzione;
3. rigenerare l'inventario:
   `python tests/portable/rm0008_2a_acceptance/generate_production_inventory_v1.py --write`;
4. committare e pubblicare (`scripts/publish-public.sh --incremental`);
5. `gh workflow run 341670856 --repo brunialti/metnos -f rm0008_snapshot_pre_fix=true`
   e attendere il verde;
6. scaricare l'artefatto `rm0008-2a-pre-fix-evidence-v1` e copiarlo in
   `tests/portable/rm0008-2a-pre-fix-evidence-v1.json`;
7. ripristinare: `git checkout "$SAVED" -- runtime/ install/ tests/portable/rm0008_2b/`,
   rigenerare l'inventario, verificare che il confronto col salvato sia vuoto;
8. committare, pubblicare, verificare il ciclo finale verde.

Trappole gia' pagate: il lavoro storico ordinario deve restare verde nello stato
pre-correzione (per questo le prove dell'incremento si rimuovono col prodotto);
il manifesto **non** puo' cambiare senza una nuova fotografia, perche' il suo
digest e' confrontato sia col blob storico sia con quello corrente.

## 10. Caricare codice da un percorso: deciso, con una porta autenticata

L'obbligo 7 chiede di fallire quando «un file locale eseguito non appartiene
allo snapshot o a una dipendenza chiusa».

**Misura** (27/8, sull'albero reale):

| insieme | file | siti che caricano codice da un percorso |
|---|---|---|
| `executors/` | 93 | **1** — `undo_last_turn`, `spec_from_file_location` + `exec_module` |
| `runtime/` | 523 | 3 — `reverse_patterns_patch`, `testing/runner`, di proprieta' del runtime |

Sui 93 file degli executor non c'e' **nessun** `exec`/`eval`/`compile` builtin.

**Le tre uscite che avevo elencato erano tutte peggiori del problema.** Vietare
per tutti obbligava a riprogettare il verbo di sistema `undo`. Esentare per
origine concedeva l'autorita' alla PROVENIENZA, che e' la cosa che questo
gruppo esiste per non fare piu'. Una capacita' nuova nel manifest ampliava il
contratto, che il mandato vieta.

**Decisione (Roberto, 27/8, delegata): una quarta uscita — la porta
autenticata.** Caricare codice da un percorso non e' vietato e non si concede
per fiducia: si concede per AUTENTICAZIONE.
`runtime/admitted_module_v1.py` e' l'unica porta. Riceve un
record di catalogo gia' pubblicato — mai un percorso scelto da chi chiama —
rilegge i file di codice dichiarati, ricalcola il digest e lo confronta con
quello firmato, e solo allora **esegue i byte gia' in memoria**: fra il
controllo e l'esecuzione non resta alcuna finestra. Un record senza digest
firmato appartiene alla distribuzione installata ed e' ammesso soltanto se vive
davvero sotto la radice degli executor di quella distribuzione.

E' **piu' flessibile** dello stato precedente (qualunque executor puo' farlo,
non solo un builtin per il fatto di esserlo) e **piu' sicura** (prima non si
verificava nulla: si apriva il percorso e si eseguiva cio' che c'era).

La verifica statica del candidato rifiuta ora i sei nomi che caricano codice da
un percorso. Costo misurato: 86 executor su 87 gia' chiusi.

**L'unica eccezione, dichiarata.** `undo_last_turn` va portato sulla porta, e
la modifica e' scritta: `internal/design/patch_undo_last_turn_porta_autenticata.diff`.
Non si puo' applicare adesso: dopo il cutover un executor cambia SOLO tramite
un'intenzione di Executor Birth — `sign.py publish` risponde
«unavailable in STORE_ONLY» — ed e' esattamente cio' che questo gruppo sta
rendendo possibile. Quella patch e' la prima intenzione da presentare quando
Birth e' attivo. La cella
`test_the_closure_cost_on_the_real_executors_is_known_and_named` nomina
l'eccezione e diventa rossa quando sara' sanata.

## 11. Criterio di uscita del gruppo 3

Stessa forma del §13 del gruppo 2: una cella rinviata contiene `N/A`, il gruppo
che la possiede e il motivo; non resta vuota e non si colora di verde.

| Obbligo (§2) | Simbolo produttivo | Prova | Esito | Git |
|---|---|---|---|---|
| 1 · linter invocato davvero | `executor_birth_shadow._lint_check` | `test_executor_birth_shadow.py::test_the_linter_really_runs_and_refuses_what_it_rejects`, `..._reads_every_language_the_candidate_declares` | verde | `092caa23` |
| 2 · albero sintattico e import | `executor_birth_shadow._closure_findings_v1` | `..._the_closure_reads_every_file_and_names_what_breaks_it`, `..._costs_nothing`(misura sugli 87 executor) | verde | `0d75e21b` |
| 3 · risoluzione chiusa di primitive | `executor_birth_primitive_table_v1`, `executor_birth_property_runner._resolve` | `test_executor_birth_property_runner.py`, quattro celle | verde | `74b6945f` |
| 3 · risoluzione chiusa dei modelli | `executor_birth_template_table_v1.template_v1` | `test_executor_birth_template_table.py`, cinque celle | verde | `67822d48` |
| 4 · fondo sandbox autenticato (Linux) | `LinuxSandboxRegistry`, `_checked_linux_backend_v1`, `executor_birth_sandbox_registry_v1` | `test_executor_birth_runner.py` (tre rifiuti nominati), `rm0008_2b/test_group3_sandbox_registry.py` (cinque celle) | verde su POSIX | `0838d560`, `57f4dc3e` |
| 4 · legame Windows | `WindowsSandboxRegistry` | — | **NON PROVATO** — vedi sotto | — |
| 5 · registri di autorità consumati | `load_sealed_authorities_v1` | `rm0008_2b/test_group3_authority_consumption.py`, quattro celle | verde su POSIX | `f6ffeca2` |
| 6 · politiche produttive, identità mossa | `CONTEXT_CATALOG_V1` | `rm0008_2b/test_context_material.py::test_a_catalogue_entry_that_moves_changes_the_identity` (retrocessione: identificativo **ed** epoca) | verde | `0a625147` |
| 7 · caricamenti dinamici noti | `admitted_module_v1.load_admitted_module_v1`, `_PATH_CODE_LOADERS_V1` | `test_admitted_module.py`, nove celle | verde, con una eccezione dichiarata | `b111bafd`, `592aceca` |
| 8 · bootstrap sul pubblicatore sigillato | `executor_birth_bootstrap._build_sealed` | `test_executor_birth_bootstrap.py` | verde | `620464e8` |
| 9 · tabella chiusa e fabbriche Producer | `executor_birth_producer_table_v1` | `rm0008_2b/test_group3_producer_table.py` | verde | `14efd6ce` |
| 10 · basi dati sotto la cartella di stato | `_secure_state_dir`, `_secure_state_db` | `test_executor_birth_bootstrap.py`, due celle | verde | `84452ca2` |
| 11 · decodificatore libero rimosso nello stesso passo | assenza di `_build`, `_load_authorities`, `_context_builder`, `_read_config` | `test_executor_birth_bootstrap.py::test_the_sealed_build_refuses_without_a_prepared_set` | verde | `620464e8` |
| 12 · barriera di riconvalida | `executor_birth_prepared_set.load_prepared_set_v1` | `rm0008_2b/test_group3_prepared_set.py` | verde su POSIX | `bc465b41` |

**Requisiti non provati, elencati separatamente:**

1. **Legame Windows del registro sandbox.** Il documento misurato dichiara
   `unavailable` su `nt` e il predispositore non completa comunque su Windows
   (blocco 2A del §13 del gruppo 2, causa non ancora provata). Finché quel
   blocco resta, il fondo Windows non si può né misurare né esercitare: il
   corridore continua a pretendere il suo registro e a rifiutare senza, come
   prima di questo gruppo.
2. **`undo_last_turn` sulla porta autenticata.** È l'unico dei 87 executor che
   carica codice da un percorso calcolato. La modifica è scritta
   (`internal/design/patch_undo_last_turn_porta_autenticata.diff`) e **non
   applicabile ora**: dopo il cutover un executor cambia soltanto tramite
   un'intenzione di Executor Birth, che è ciò che questo gruppo abilita.
   La cella `test_the_closure_cost_on_the_real_executors_is_known_and_named`
   nomina l'eccezione e diventa rossa quando sarà sanata.
3. **Nascita reale di un executor attraverso il bundle attivato** (§3.3 b). Le
   celle partono dall'insieme già predisposto e provano attivazione, consumo e
   identità; nessun executor è ancora nato attraverso il cancello, perché
   nessun chiamante è migrato. È lavoro del gruppo 4, non un rinvio di questo.
4. **Cinque celle POSIX della base** (`g2` pubbliche di altro UID, `g8` binding
   UID) restano rosse in locale perché richiedono `sudo` senza password; sono
   verdi nel ciclo pubblico, che è l'oracolo.
