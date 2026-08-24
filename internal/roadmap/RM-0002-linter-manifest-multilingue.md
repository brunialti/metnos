# RM-0002 — Controllo multilingue dei manifest executor

> `RM-0002` · stato `in_progress` · creazione `2026-07-23` · analisi e
> specifica chiuse `2026-08-24` · L0-L4 implementate `2026-08-25` · L5-L6
> subordinate al completamento di RM-0007 · documento interno

## 1. Stato e decisione

La progettazione è conclusa. L0-L4 sono implementate e provate; L5-L6 restano
aperte perché il loro blocco operativo richiede prima il confine di
pubblicazione di RM-0007. Lo stato della roadmap è quindi `in_progress`:
`implemented` dichiarerebbe falsamente completati quei gate.

RM-0002 realizza tre interventi circoscritti:

1. rende sempre esplicita la lingua controllata dal linter;
2. confronta fra sorgente e traduzione quattro invarianti macchina;
3. usa l'inventario comune dei manifest anche per vedere i pacchetti importati
   nella modalità di controllo, senza concedere loro autorità di modifica.

Le misure storiche e le due revisioni avversariali sono conservate nel rapporto
`internal/reports/rm0002-linter-manifest-multilingue-audit-20260824.md`. Quel
rapporto spiega le decisioni ma non è una specifica.

### Dipendenze

- RM-0005 resta `closed`: registro, materializzatore, candidati e attivazione
  vengono riusati, non riscritti.
- RM-0007 rende sicure pubblicazione linguistica, firma e lettura del
  manifesto. Deve essere implementata prima di trasformare in blocchi
  operativi le nuove regole di confronto fra traduzioni. Il legame forte fra
  codice verificato e codice eseguito resta fuori da entrambe le roadmap.
- `AFF-I18N-001` possiede la futura localizzazione di `affinity`. RM-0002 non
  anticipa né duplica quella decisione.

## 2. Obiettivo e valore

Una persona deve poter usare Metnos in una lingua senza che il sistema:

- controlli in realtà l'italiano;
- suggerisca argomenti diversi nella traduzione;
- perda o modifichi segnaposto risolti dal runtime;
- accetti capitoli del contratto in un ordine che cambia ciò che legge il
  planner;
- blocchi una lingua per una parola naturale omonima di un argomento tecnico.

Il controllo è deterministico. Non giudica la qualità letteraria e non usa un
LLM. La revisione semantica di RM-0005 resta separata.

## 3. Stato corrente che l'implementazione deve cambiare

Al 24 agosto 2026:

- `runtime/manifest_lint.py::_description_text()` sceglie `it`, poi `en`, poi
  la prima lingua disponibile;
- `lint_manifest()` e `lint_file()` non ricevono una lingua;
- `runtime/i18n_activation.py::validate_manifests()` invoca il linter senza
  indicare la lingua che sta attivando;
- il controllo `runtime_resolved` cerca parole naturali e produce il falso
  errore `get_location.actor` in inglese;
- `_OMIT_MARKERS` contiene frasi italiane e inglesi cablate nel codice;
- `_validate_common()` non confronta ordine dei capitoli, chiamate e argomenti;
- i test di attivazione passano un validatore fittizio sempre positivo;
- CLI, materializzatore e loader non condividono lo stesso inventario.

La presenza dei quattro capitoli in ogni lingua è già controllata dallo
standard executor. Le descrizioni generate sono già sottoposte a parte dei
limiti di budget. Questi controlli vanno riusati, non duplicati.

## 4. Perimetro

### 4.1 Compreso

- lingua obbligatoria nelle API del linter;
- nessun ripiego implicito durante authoring, traduzione o attivazione;
- controllo locale della lingua richiesta;
- confronto deterministico sorgente-destinazione;
- scanner prudente del solo capitolo `PATTERN:`;
- correzione generale del falso positivo `runtime_resolved`;
- inventario comune consumato in modalità di audit e dal CLI;
- collegamento del confronto al candidato in memoria;
- adozione prima informativa e poi bloccante;
- prove con validatore reale e lingua sintetica.

### 4.2 Escluso

- sicurezza e atomicità della pubblicazione, assegnate a RM-0007;
- nuova autorità per `manifest.lang_state.json`;
- rifirma, rollback e fotografia consumata dal loader;
- localizzazione o nuovo schema di `affinity`;
- giudizio semantico della prosa;
- riscrittura o accorciamento automatico dei manifest;
- nuovo renderer del pool;
- dizionari di omissione per lingua;
- supporto speciale limitato a italiano e inglese;
- inserimento automatico dei pacchetti importati nel percorso di traduzione o
  firma.

## 5. Vocabolario normativo

### Lingua richiesta

Tag BCP-47 normalizzato con `i18n_registry.normalize_language()`. È sempre
fornito dal chiamante. Se manca nella risorsa, il linter produce
`language_missing`; non usa un'altra lingua.

### Risorsa

Percorso TOML canonico della prosa, per esempio `description` oppure
`args.properties.path.description`. La forma corta
`args.path.description` non deve essere introdotta.

### Controllo locale

Regola applicata a una sola lingua: capitoli, argomenti ammessi, uso di
argomenti risolti dal runtime e limiti editoriali.

### Controllo trasversale

Confronto di invarianti macchina fra testo sorgente e testo tradotto. Non
confronta parole naturali, ordine delle frasi o sinonimi.

### Atomo macchina

Elemento riconoscibile senza interpretazione semantica: chiamata, nome di
argomento keyword, assegnazione tecnica, segnaposto runtime o segnaposto
template.

### Astensione

Impossibilità di classificare con certezza una forma naturale o ambigua. Non è
un errore e non può bloccare. L'evidenza resta disponibile nella diagnostica.

### Inventario

Enumerazione neutra di manifest e problemi. La scoperta non implica che il
contratto sia attivo, traducibile, modificabile o firmabile.

## 6. Contratti Python obbligatori

### 6.1 Risultato strutturato

Modificare `runtime/manifest_lint.py` usando queste forme:

```python
Severity = Literal["error", "warn"]
FindingScope = Literal["local", "parity", "global"]

@dataclass(frozen=True, slots=True)
class Finding:
    check: str
    severity: Severity
    scope: FindingScope
    message: str
    resource: str = "manifest"
    languages: tuple[str, ...] = ()
    evidence: Mapping[str, object] = field(default_factory=dict)
```

`languages` contiene una lingua per un controllo locale e due lingue per un
confronto sorgente-destinazione. Una regola globale usa la tupla vuota. I dati
in `evidence` devono essere limitati e privi di intere descrizioni; includono
misure, nomi di atomi o hash, non prosa arbitrariamente lunga.

I codici `check` sono API stabili. Non ricavare comportamento analizzando
`message`.

### 6.2 API locale

```python
def lint_manifest(
    manifest: Mapping[str, object],
    *,
    language: str,
    allow_flat_description: bool = False,
    catalog_names: AbstractSet[str] | None = None,
    sibling_affinities: Mapping[str, AbstractSet[str]] | None = None,
) -> list[Finding]:
    ...

def lint_file(
    path: Path,
    *,
    language: str,
    catalog_names: AbstractSet[str] | None = None,
    sibling_affinities: Mapping[str, AbstractSet[str]] | None = None,
) -> list[Finding]:
    ...
```

`lint_file()` è un adattatore per CLI, fixture e layout legacy. Dopo il cutover
di RM-0007 non viene usato da un confine produttivo: attivazione e pubblicazione
passano `VerifiedContractSnapshot.parsed_manifest` a `lint_manifest()`, così il
linter giudica gli stessi byte già verificati.

Regole dell'API:

1. `language` non ha valore predefinito;
2. viene normalizzata una volta all'ingresso;
3. nessun helper sceglie `it`, `en` o la prima chiave;
4. una mappa senza la lingua richiesta produce `language_missing`;
5. una descrizione piatta è ammessa soltanto quando
   `allow_flat_description=True`, usato da Synt sul candidato transitorio;
6. un manifest persistente con descrizione piatta resta responsabilità dello
   standard executor e non ottiene un ripiego dal linter;
7. la funzione non modifica il mapping ricevuto.

Eliminare `_description_text()` dopo aver migrato tutti i chiamanti. Non
lasciare una API compatibile ambigua.

### 6.3 API trasversale

```python
def lint_contract_translation(
    source: str,
    translated: str,
    *,
    resource: str,
    source_language: str,
    target_language: str,
) -> list[Finding]:
    ...
```

Questa funzione:

- normalizza entrambe le lingue;
- applica le regole di parità pertinenti alla risorsa;
- non legge file, registro o configurazione globale;
- non traduce e non corregge;
- restituisce tutti i rilievi deterministici in un solo passaggio.

### 6.4 Atomi del `PATTERN`

```python
@dataclass(frozen=True, order=True, slots=True)
class PatternCall:
    callee: str
    keyword_names: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class PatternAtoms:
    calls: tuple[PatternCall, ...]
    standalone_assignments: tuple[str, ...]
    operator_identifiers: tuple[str, ...]
```

Le tuple sono ordinate in forma canonica ma conservano i duplicati. I valori
degli argomenti non vengono confrontati, salvo i segnaposto disciplinati da
regole separate.

### 6.5 Inventario condiviso

RM-0002 introduce `runtime/manifest_inventory.py` con `ManifestOrigin`,
`ManifestSource`, `ManifestRef`, `InventoryProblem`, `ManifestInventory` e
`inventory_manifests()`. Il modulo è una dipendenza neutra condivisa anche con
RM-0007; scoperta, pubblicazione e firma restano autorità distinte.

Il CLI usa tutte le origini. Il materializzatore conserva inizialmente le sole
origini già autorizzate. I pacchetti importati diventano visibili nel rapporto,
ma non acquisiscono automaticamente traduzione, scrittura o firma.

## 7. Regole normative

### 7.1 Matrice

| ID | Regola | Ambito | Gravità finale | Blocco finale |
|---|---|---|---|---|
| `language_missing` | lingua richiesta assente o vuota | locale | error | sì |
| `chapter_order` | marker principali esattamente una volta e ordinati | locale/parità | error | sì |
| `pattern_unparseable` | stringa o delimitatore non chiuso | locale | error | sì sui nuovi/toccati |
| `pattern_unknown_arg` | keyword non nello schema né universale | locale | error | sì |
| `runtime_arg_passed` | argomento `runtime_resolved` passato nella chiamata | locale | error | sì |
| `pattern_atoms` | chiamate e keyword diverse fra le lingue | parità | error | sì |
| `runtime_placeholders` | multiinsieme `${RUNTIME:...}` diverso | parità | error | sì |
| `template_placeholders` | multiinsieme `{{...}}` diverso | parità | error | sì |
| `runtime_arg_code_mention` | argomento tecnico mostrato come codice fuori pattern | locale | warn | no |
| `head_length` | testa oltre il limite corrente | locale | warn | no |
| `description_length` | descrizione oltre il limite corrente | locale | warn | no |
| `argument_description_length` | descrizione argomento oltre il limite | locale | warn | no |
| `non_reference` | riferimento `NON:` non risolto | locale | warn | no |

Il controllo affinity esistente può continuare a essere emesso una volta dal
CLI, ma non viene esteso né reso bloccante da questa roadmap.

### 7.2 Capitoli

Per `resource="description"` contare le occorrenze esatte dei marker
`SCOPO:`, `PATTERN:`, `NON:`, `OUT:`.

- ognuno deve comparire esattamente una volta;
- gli indici devono essere strettamente crescenti;
- marker tradotti o duplicati sono errori;
- descrizioni di argomento non applicano questa regola.

Lo standard executor continua a controllare la presenza. RM-0002 aggiunge
ordine e unicità senza copiare il validatore dello standard.

### 7.3 Scanner del `PATTERN`

Non usare una singola espressione regolare sull'intera descrizione. Applicare
uno scanner lineare soltanto al testo fra `PATTERN:` e `NON:`:

1. scorrere carattere per carattere;
2. riconoscere apici singoli e doppi e rispettare `\`;
3. mantenere profondità separate per `()`, `[]` e `{}`;
4. riconoscere `identificatore(` fuori dalle stringhe;
5. trovare la parentesi di chiusura bilanciata della chiamata;
6. nel corpo della chiamata, dividere solo sulle virgole a profondità zero;
7. riconoscere come keyword solo `identificatore =` a profondità zero;
8. ordinare i nomi keyword e rifiutare duplicati nella stessa chiamata;
9. registrare ogni chiamata, comprese alternative ripetute;
10. registrare assegnazioni tecniche autonome come `all=true`;
11. registrare identificatori uniti esplicitamente da operatori come
    `from_step+columns`;
12. non interpretare il valore di una keyword;
13. stringa o delimitatore non chiusi producono `pattern_unparseable`;
14. zero chiamate è ammesso per contratti che dichiarano un pattern naturale;
15. se una lingua contiene atomi e l'altra no, `pattern_atoms` rileva la
    divergenza.

Il confronto usa il multiinsieme canonico di `PatternCall`, assegnazioni e
operatori. L'ordine degli esempi può cambiare; quantità, nomi delle chiamate e
insiemi di keyword no.

Non usare `ast.parse`: i pattern correnti includono ellissi, frecce, booleani
TOML e alternative in prosa che non formano espressioni Python complete.

### 7.4 Segnaposto

Per `${RUNTIME:chiave}` confrontare il token completo, chiave compresa, e
conservare la molteplicità. Un token iniziato ma non chiuso produce errore
locale.

Per `{{...}}` confrontare il contenuto dopo avere eliminato soltanto gli spazi
immediatamente interni alle doppie parentesi. Non normalizzare l'espressione
interna e conservare i duplicati.

La parità si applica a descrizione principale e descrizioni argomento.

### 7.5 `runtime_resolved`

Eliminare `_OMIT_MARKERS` e la ricerca della parola naturale come errore.

- keyword `nome=` dentro una chiamata del `PATTERN`, quando `nome` ha
  `runtime_resolved=true`: `runtime_arg_passed`, errore;
- forma codice inequivoca fuori dal `PATTERN`, per esempio backtick o
  assegnazione: `runtime_arg_code_mention`, avviso;
- semplice omonimo nella prosa, per esempio “current actor”: nessun rilievo;
- nessun elenco lessicale per lingua e nessun ripiego all'inglese.

Questa è una regola generale basata sulla forma, non un'eccezione per `actor`.

### 7.6 Lunghezze e riferimenti

I limiti esistenti restano avvisi locali per lingua. Non aggregare lingue in un
singolo finding e non modificare automaticamente la prosa. `NON:` continua a
produrre soltanto avvisi per riferimenti non risolti finché non esiste una
grammatica più forte.

## 8. Integrazione file per file

| File | Modifica | Verifica |
|---|---|---|
| `runtime/manifest_lint.py` | API esplicita, finding, scanner, confronto, nuova regola runtime | unità complete |
| `runtime/i18n_activation.py` | prima validatore `(Path, language)`; dopo RM-0007 snapshot + lingua bersaglio | prova con validatore reale sugli stessi byte |
| `runtime/i18n_pipeline.py` | chiamare il confronto per `layer="contract"` dopo `_validate_common()` | candidato errato non ammesso |
| `runtime/synt_multistage.py` | passare lingua corrente e `allow_flat_description=True` | candidato flat ancora validato |
| `runtime/manifest_inventory.py` | introdurre l'inventario neutro consumato anche da RM-0007 | nessun percorso concede autorità |
| CLI in `manifest_lint.py` | enumerare ogni lingua e origine; affinity una volta | rapporto stabile e sola lettura |
| test E2E che chiamano il linter | aggiungere lingua esplicita | nessun vecchio ripiego |
| `tests/runtime/i18n/test_i18n_activation.py` | almeno un percorso col validatore vero | difetto nella lingua bersaglio fermato |

Nel ramo `contract` di `_translate_item()` l'ordine finale deve essere:

```text
traduzione
  -> _validate_common
  -> lint_contract_translation
  -> se errori: CandidateValidationError, nessun candidato ammesso
  -> altrimenti artefatto candidato
```

Il blocco operativo di questa sequenza entra solo dopo RM-0007. Prima può
essere eseguito in prova e in rapporto informativo.

## 9. Ordine di implementazione vincolante

Ogni fase corrisponde a un commit autonomo. Un agente non deve iniziare la fase
successiva se il gate indicato non è verde.

### L0 — Caratterizzazione e validatore reale

**Modificare:** soltanto test e fixture.

1. Aggiungere una fixture con lingua sintetica valida.
2. Aggiungere una fixture con difetto presente soltanto nella lingua bersaglio.
3. Eseguire `validate_manifests()` senza sostituire il validatore.
4. Conservare una prova esplicita che i vecchi test usano ancora il doppio
   quando stanno testando soltanto altri componenti.

**Gate:** almeno una prova completa attraversa linter e verifica reale; prima
deve fallire per il motivo atteso, non per firma o setup.

### L1 — Lingua obbligatoria e falso positivo

**Modificare:** `runtime/manifest_lint.py`, tutti i chiamanti trovati con una
ricerca globale e i relativi test.

1. Cambiare le firme come in §6.2.
2. Migrare ogni chiamante; nessun valore predefinito temporaneo.
3. Sostituire la regola `runtime_resolved` come in §7.5.
4. Eliminare `_description_text()` e `_OMIT_MARKERS` solo quando non hanno più
   riferimenti.
5. Passare la lingua bersaglio dall'attivazione.

**Gate:** ricerca globale senza chiamate prive di `language`; `current actor`
non è errore; `actor=` nel pattern lo è; lingua mancante non usa ripiego.

Questa fase può essere sviluppata prima di RM-0007 perché corregge il controllo
locale già esistente. Non deve introdurre le nuove regole trasversali come
blocco di pubblicazione.

### L2 — Inventario comune in osservazione

1. Sostituire le scansioni del CLI con `inventory_manifests()`.
2. Passare fonti esplicite dalla configurazione.
3. Mostrare origine e stato nel rapporto.
4. Rendere visibili gli import soltanto in audit.
5. Eseguire affinity una volta per manifest, non una volta per lingua.

**Gate:** fixture di tutte le topologie; import visibile ma mai aggiunto alla
promozione; ritirati e disabilitati distinti; nessun conteggio cablato.

### L3 — Scanner e confronto in memoria

**Modificare:** `runtime/manifest_lint.py` e test unitari.

1. Implementare prima scanner e tipi `Pattern*`.
2. Provare ogni caso limite di §7.3.
3. Implementare capitoli e segnaposto.
4. Implementare `lint_contract_translation()`.
5. Eseguirlo su fixture e catalogo in sola osservazione.

**Gate:** nessun falso errore sul corpus ammesso non ambiguo; ogni mutazione
sintetica prevista produce esattamente il codice di regola atteso.

### L4 — Bonifica guidata dai dati

1. Generare un rapporto dinamico per origine e lingua.
2. Correggere alla fonte soltanto divergenze certe dimostrate dalle regole.
3. Ogni correzione a un manifest ha una prova specifica e una rifirma eseguita
   attraverso RM-0007.
4. Non aggiungere allowlist di nomi per preservare difetti esistenti.

**Gate:** zero errori deterministici nell'inventario ammesso; gli import non
autorizzati restano segnalati senza essere modificati.

### L5 — Blocco dei candidati

**Prerequisito:** RM-0007 `implemented` almeno fino alla pubblicazione
linguistica e al cutover della fotografia verificata.

1. Collegare `lint_contract_translation()` a `_translate_item()`.
2. Convertire i finding `error` in `CandidateValidationError` prima della
   pubblicazione.
3. Fare usare ad attivazione e pubblicazione il mapping dello snapshot, non
   `lint_file()` su un percorso riaperto.
4. Conservare i warning nel rapporto senza bloccare.
5. Provare candidato obsoleto, errore del linter e firma fallita.

**Gate:** nessun errore modifica puntatore o generazione corrente; una
traduzione valida produce una generazione verificata.

### L6 — Adozione comune e chiusura tecnica

1. Verificare generatori, importatori e CLI con ricerca dei chiamanti reali.
2. Riutilizzare il linter senza copiare regole nei template.
3. Eseguire suite mirate, suite completa e due cicli del corpus di routing.
4. Generare il rapporto finale dalla revisione Git candidata.
5. Aggiornare indice anti-regressione e stato della roadmap.

**Gate:** tutti i criteri di §12 soddisfatti.

## 10. Piano di test obbligatorio

### 10.1 Unità lingua e runtime

- lingua richiesta diversa da `it` realmente controllata;
- lingua assente produce `language_missing`;
- nessun ripiego da `fr` a `en` o `it`;
- candidato flat rifiutato senza autorizzazione e ammesso da Synt con lingua;
- `current actor` non produce errore;
- `actor=` nel `PATTERN` produce `runtime_arg_passed`;
- backtick o assegnazione tecnica fuori pattern produce soltanto avviso.

### 10.2 Scanner

- virgole, `=`, parentesi e operatori dentro stringhe ignorati;
- liste e dizionari annidati non spezzano gli argomenti top-level;
- apici con escape;
- due alternative della stessa chiamata;
- pipeline `find_packages(...) -> run_processes(from_step=1)`;
- ellissi e booleani non Python;
- chiamata duplicata conservata nel multiinsieme;
- keyword duplicata rifiutata;
- delimitatore o stringa non chiusa;
- pattern naturale senza chiamate.

### 10.3 Parità

- nome della chiamata modificato;
- keyword `reason` aggiunta o rimossa;
- riordinamento degli esempi ammesso;
- chiamata persa o duplicata rifiutata;
- `${RUNTIME:actor}` sostituito con `${RUNTIME:now}`;
- segnaposto runtime perso o duplicato;
- `{{ value }}` e `{{value}}` equivalenti;
- espressione Jinja interna modificata;
- capitoli riordinati, duplicati o tradotti;
- descrizione argomento con segnaposto divergente.

### 10.4 Inventario

- core, builtin, skill builtin, utente diretto, skill utente e legacy;
- ritirato e disabilitato classificati;
- symlink, alias, duplicato e collisione segnalati;
- import nel rapporto ma non nella promozione;
- ordine indipendente dall'ordine restituito dal filesystem;
- radici temporanee iniettate, nessun accesso alla home reale nei test.

### 10.5 Integrazione

- attivazione valida con validatore reale;
- lingua sintetica difettosa rifiutata per il codice atteso;
- traduzione difettosa non crea una generazione;
- warning editoriale non blocca;
- CLI controlla tutte le lingue presenti;
- affinity non viene duplicata;
- `--strict` mostra e conta la gravità effettiva in modo coerente;
- audit non modifica manifest, firma, stato o registro;
- nessun cambiamento a catalogo, ordine dei tool o piani quando il linter non è
  nel confine di authoring.

### 10.6 Prestazioni

Il controllo completo dell'inventario ammesso, con file già disponibili sul
filesystem locale, deve terminare sotto 500 ms al percentile 95 nell'ambiente
CI di riferimento. La misura viene registrata, non usata per introdurre cache
non prevista. Il percorso ordinario dei turni non importa né invoca il linter.

## 11. Rischi e contromisure

| Rischio | Gravità | Contromisura | Prova |
|---|---:|---|---|
| blocco di una lingua per falso positivo | alta | sole forme macchina, astensione sulla prosa | corpus ambiguo `actor` |
| executor scompare dopo nuova regola | alta | osservazione, bonifica, poi blocco | catalogo prima/dopo |
| linter costruito su pubblicazione insicura | bloccante | RM-0007 prima di L5 | gate di dipendenza |
| import difettosi bloccano il catalogo | alta | audit senza autorità, bonifica esplicita | vista import separata |
| scanner interpreta prosa come codice | alta | solo capitolo PATTERN, parser lineare prudente | casi ambigui |
| controllo troppo permissivo | alta | mutazioni sintetiche di ogni atomo | matrice parità |
| regole duplicate nei generatori | media | un solo modulo e ricerca statica | nessuna copia dei codici |
| nuovo hardcoding linguistico | alta | lingua come dato BCP-47, nessun lessico | fixture terza lingua |
| conteggi storici diventano requisiti | media | inventario dinamico e report separato | nessun numero nel gate |
| affinity progettata due volte | alta | esclusione e dipendenza AFF-I18N | revisione del diff |

## 12. Criteri di completamento

RM-0002 passa a `implemented` soltanto quando:

- nessuna API del linter sceglie implicitamente una lingua;
- ogni chiamante passa una lingua normalizzata;
- l'attivazione controlla esattamente la lingua richiesta;
- almeno una prova completa usa il validatore reale;
- `current actor` non blocca e un argomento runtime passato sì;
- ordine dei capitoli, atomi del pattern e segnaposto sono verificati prima
  della pubblicazione;
- lo scanner rispetta tutti i casi limite di §10.2;
- il CLI usa l'inventario comune senza concedere autorità agli import;
- nessun warning editoriale blocca una lingua;
- non esistono eccezioni per executor, italiano o inglese;
- il catalogo ammesso non contiene errori deterministici delle nuove regole;
- audit e linter non entrano nel percorso ordinario dei turni;
- suite mirate, suite completa e due cicli di routing sono verdi;
- il rapporto finale è generato dall'inventario e registra revisione e hash;
- RM-0007 ha completato il confine richiesto da L5;
- indice anti-regressione e documentazione interna sono aggiornati.

Passa a `closed` dopo distribuzione, prova sull'installazione di riferimento e
assenza di attività residua.

## 13. Istruzioni per un agente implementatore

1. Leggere per intero questa roadmap, RM-0007, ADR 0223 e il rapporto storico.
2. Eseguire una sola fase L0-L6 per commit.
3. Cercare tutti i chiamanti prima di cambiare una firma; non affidarsi
   all'elenco di §8 come se fosse completo nel futuro.
4. Scrivere prima la fixture che dimostra il difetto della fase.
5. Non aggiungere parametri predefiniti per mantenere compatibilità ambigua.
6. Non aggiungere nomi di executor, lingue o quantità nel codice.
7. Non usare un LLM nel linter e non correggere automaticamente la prosa.
8. Non duplicare controlli dello standard executor.
9. Non modificare `affinity`.
10. Non rendere bloccante il confronto prima del gate RM-0007.
11. Non correggere un falso positivo con una eccezione nominale; restringere la
    regola alla forma macchina che lo rende certo.
12. Se lo scanner incontra una forma non prevista, aggiungere prima fixture e
    decidere fra atomo o astensione; non estendere una regex alla cieca.
13. Dopo ogni fase eseguire test mirati e `git diff --check`.
14. Registrare commit e prove nel documento prima di cambiare lo stato.
15. Fermarsi e chiedere una decisione se la modifica richiede nuovi campi del
    manifest, una severità diversa o un allentamento di firma e autorità.

## 14. Registro

| Data | Stato | Evento |
|---|---|---|
| 2026-07-23 | `active` | prima analisi e roadmap |
| 2026-08-24 | `active` | riverifica, revisione avversariale e controrevisione |
| 2026-08-24 | `ready` | storia separata; perimetro ridotto; specifica e ordine di sviluppo chiusi |
| 2026-08-25 | `in_progress` | L0-L4 implementate: lingua esplicita, inventario condiviso, attivazione, materializzatore, parità e osservazione; L5-L6 attendono il confine RM-0007 |
