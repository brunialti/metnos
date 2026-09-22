# RM-0009 — ricontrollo consolidato del contratto Birth

Data: 2026-09-15  
Baseline di codice: commit RM-0008
`1c308922839f7659a3cf54d988f995bba0f215d6`  
Metodo: sola lettura; nessun test, runner, servizio, database o rilascio
eseguito o modificato.

## 1. Esito

La baseline committed è ora sufficiente per concordare **D-G0.3** e per
scrivere il codice preliminare di **I1.1**. Non serve attendere una prova reale
FS-A o F5 per definire e collaudare in isolamento il contratto.

Restano però due stati distinti:

- **contratto congelabile ora:** una sola façade Producer RM-0009, identità
  stabile per `operation_id`, rilettura autenticata della receipt Admission e
  una porta lifecycle che non esporti autorità;
- **esercizio non autorizzato:** D1 reale resta vietata senza FS-A valida e
  runner disponibile; D2 reale resta vietata senza `EXT-RM0008-F5`, FS-A,
  receipt, manifest, preesercizio e FS-B quando applicabile.

La conclusione della manutenzione RM-0008 e la selezione della release 53 non
chiudono automaticamente F5 o F6. Nel commit e nel descriptor della release
non esistono `EXT-RM0008-F5` o `RM0009-FS-A`.

## 2. Prova di codice e claim di rilascio

| Fatto | Evidenza | Valore probatorio |
|---|---|---|
| Commit esaminato | `1c308922839f7659a3cf54d988f995bba0f215d6`; i soli elementi non tracciati erano `BACHECA` e `internal/coordination/`, esclusi | baseline committed determinata |
| Corrispondenza con release 53 | digest identici, file per file, per `executor_birth_{intent,bootstrap,operational,producer_context,producer_store,receipts,lifecycle,preexercise,functional}.py`, `synth_request.py` e `test_runner.py` | prova che queste sorgenti della release sono gli stessi byte del commit |
| Descriptor osservato | `/var/lib/metnos/executor-birth/releases-v1/00000000000000000053/deployment/executor-birth-deployment-v1.json`, schema 1, sequenza 53, digest file `sha256:9f83be83c192b08f9d9ae9efb630ef3e05c28a508b893b3e3dcfb3526006c063` | metadato pubblico della directory; non è stata eseguita la verifica della catena |
| Claim umano | `internal/reports/rm0008-release-20260915.md` dichiara release 53 “selected and ready”, con build/head e prove Tutor/documentazione; dichiara anche che non cambia il Python runtime rispetto alla 52 | prova documentale di rilascio, non attestazione F5/FS-A |
| Contenuto descriptor | identità installazione, artefatti, eseguibili, servizi e sequenza; nessun commit sorgente, suite F5/FS-A, `proof_kind` o riferimento a tali prove | non lega da solo commit, build e prove operative |

Quindi: la corrispondenza dei byte è una **prova di codice distribuito**; la
frase “selected and ready” è un **claim di rilascio**; nessuna delle due è la
certificazione F5 richiesta da D-X0.1.

## 3. Contratto corrente realmente implementato

### 3.1 Façade Producer

`runtime/executor_birth_intent.py` definisce:

```text
BirthIntent(candidate_source_root, contract_id, reason, approval_refs=())
```

La capability `_ProducerCapability` è costruibile soltanto col sigillo del
modulo. Le undici façade reali sono:

```text
submit_change_extend_birth       change_applier:extend
submit_change_rollback_birth     change_rollback:rollback
submit_synth_multistage_birth    synt_multistage:create_or_replay
submit_synth_specialize_birth    synt_specialize:specialize_or_replay
submit_synth_approve_birth       synt_approve:approve_or_replay
submit_promote_birth             promoter:promote
submit_stack_reconcile_birth     stack_reconcile:restart_sign_first
submit_skills_birth              skills_cli:skill_import_or_reactivation
submit_installer_birth           installer_phase3:install
submit_builtin_generation_birth  builtin_contract_generator:generate_builtin
submit_promoter_rollback_birth   promoter:rollback
```

Tutte convergono in `_submit` → `_execute_intent_with_capability` → `_execute`.
`runtime/executor_birth_synth.py` offre la superficie dati specifica per i tre
ingressi Synth. `birth_executor(BirthRequest)` esiste, ma RM-0009 non deve
chiamarlo né costruire `BirthRequest`.

Non esistono una capability/façade `growth_pipeline`, un tipo RM-0009 o una
façade unica create/extend. Il registro
`runtime/contract_boundary_api_policy.py` contiene inoltre il nome inesistente
`submit_synth_producer_birth`, mentre i tre nomi effettivi sono diversi: il
registro va riallineato nello stesso cambiamento che aggiunge la façade growth.

### 3.2 Identità e idempotenza

La factory V1 di `runtime/executor_birth_bootstrap.py` calcola:

```text
objective_hash = H(objective/v1, reason, *approval_refs)
request_id = H(request/v1, issuer_id, capability.operation,
               contract_id, objective_hash, candidate_source_id)
```

Lo store `runtime/executor_birth_producer_store.py`, schema 5, rende
idempotente la stessa `request_id` e registra capability, contratto, obiettivo,
sorgente e receipt. Gli stati sono `available | in_progress | committed |
rejected`.

Questo non basta per RM-0009:

- non esistono `operation_id`, `decision_receipt_id` o
  `intent_fingerprint` nella richiesta o nello store Producer;
- in `synth_request.py` il primo invio usa `reason="synt multistage: ..."`, il
  recupero di un candidato esistente usa
  `reason="replay synthesized candidate ..."`; la stessa operation RM-0009
  può quindi ottenere una seconda `request_id`;
- `ProducerRequestV2` lega una reattestazione a generazione, contesto,
  transizione, epoca, set e sorgente, ma non è una richiesta di crescita e non
  lega `operation_id`.

### 3.3 Receipt Admission

`runtime/executor_birth_receipts.py` definisce già gli enum autorevoli:

```text
AdmissionKind = admission | reattestation
ApprovedLifecycle = active | preexercise | quarantined
AdmittedCheckStatus = passed | not_applicable
RevisionClass = first_birth | code_revision | authority_revision |
                contract_revision | localization_revision |
                equivalent_republish | promotion_revision |
                reactivation_revision | reattestation
```

`AdmissionReceipt` schema 1 contiene `receipt_id`, contratto, generazione,
candidate/semantic/context ID, `birth_request_id`, predecessore, hash della
receipt Producer, classe revisione, check, lifecycle e firma.

La receipt è inserita nell'envelope terminale firmato interno, ma
`BirthResult` non la espone e `PublicationResult` espone soltanto contratto,
generazione precedente/corrente, operazione e `repeated`. I reader di
`contract_store` richiedono generazione e materiale trusted interno; non sono
esportati in `contract_store.__all__`. `inspect_birth_receipts` restituisce
solo stato, formato, generazione e contesto. Non esiste quindi un reader
consumabile da RM-0009 per `operation_id`.

### 3.4 Lifecycle/F5

Sono definiti:

- `BirthLifecycle = proposed | synthesized | preexercise | active |
  quarantined | deprecated | archived` in `executor_birth_epoch_store.py`;
- `F4Certification`, `F5Activation`, `load_f5_activation` e
  `LifecycleCoordinator` in `executor_birth_lifecycle.py`;
- soglia del certificato corrente: almeno 5 receipt, 2 Producer, 2 cicli e 0
  difetti;
- `LifecycleCoordinator.revise`, con rilettura Admission e CAS; rollback
  `active → active` soltanto con `historic_epoch_ref`;
- la funzione pura `decide_preexercise` e la policy chiusa in
  `executor_birth_preexercise.py`.

Non esistono chiamanti produttivi di `load_f5_activation`,
`LifecycleCoordinator` o `decide_preexercise`; fuori dai moduli compaiono solo
test. Non esistono una façade lifecycle RM-0009, un reader
`EXT-RM0008-F5` o uno schema firmato che leghi F5 a commit/build/suite. Le
primitive sono prova di codice, non prova che F5 sia attiva o chiusa.

### 3.5 Runner/FS-A

FS-A è materialmente non chiusa nel commit e nella release 53:

- `runtime/test_runner.py` esiste;
- `runtime/executor_birth_functional.py` importa `check_expect` da quel file;
- la scansione ripetuta trova 216 dichiarazioni `setup|teardown|env` in 33
  manifest, lo stesso inventario del rapporto precedente.

Esiste inoltre la contraddizione documentale che D-G0.3 deve risolvere:
`RM-0008-porta-unica-nascita-executor.md` §9 prescrive ancora di usare e
irrobustire `runtime/test_runner.py`, mentre RM-0009 FS-A ne prescrive il ritiro
dopo conversione equivalente dei manifest. L'emendamento comune deve rendere
autorevole questa sequenza: compatibilità chiusa nel runner Birth, conversione
uno-a-uno, rifiuto tipizzato dei campi legacy, poi rimozione del runner host.

Il dettaglio dei 33 percorsi resta nel rapporto
`internal/reports/rm0009-baseline/20260915/birth-contract.md` §6; non viene
duplicato qui. Il verde delle suite Birth esistenti non può trasformarsi in
FS-A finché inventario e riferimenti non sono vuoti e una prova firmata lega
codice, configurazione e risultati della release selezionata.

## 4. Contratto minimo da concordare in D-G0.3

Questa sezione è un **contratto futuro implementabile**, non un claim che le
API esistano già. Riusa l'autorità, le chiavi, il publisher, il keyring e il
sigillo Producer RM-0008; non introduce una firma o un'autorità nuova.

### 4.1 Superficie D1

Proposta da congelare:

```python
class GrowthChangeKindV1(str, Enum):
    CREATE_EXECUTOR = "create_executor"
    EXTEND_EXECUTOR = "extend_executor"

class GrowthBirthErrorCodeV1(str, Enum):
    DEPENDENCY_UNREADY = "dependency_unready"
    OPERATION_IN_PROGRESS = "operation_in_progress"
    OPERATION_REJECTED = "operation_rejected"
    OPERATION_BINDING_CONFLICT = "operation_binding_conflict"
    ADMISSION_RECEIPT_INVALID = "admission_receipt_invalid"
    TRIAL_LIFECYCLE_INVALID = "growth_trial_lifecycle_invalid"

@dataclass(frozen=True, slots=True)
class GrowthBirthIntentV1:
    operation_id: str          # sha256:<64 hex>, stabile per l'operation
    decision_receipt_id: str   # sha256:<64 hex>, D1 riletta da RM-0009
    intent_fingerprint: str    # sha256:<64 hex>, intent canonico
    change_kind: GrowthChangeKindV1
    contract_id: ContractId
    candidate_source_root: Path

@dataclass(frozen=True, slots=True)
class GrowthBirthTrialReceiptV1:
    operation_id: str
    birth_request_id: str
    producer_receipt_id: str
    admission_receipt_id: str
    contract_id: str
    generation_id: str
    candidate_id: str
    admission_context_id: str
    predecessor_id: str | None
    approved_lifecycle: ApprovedLifecycle  # deve essere PREEXERCISE
    terminal_envelope_hash: str
    repeated: bool

def submit_growth_trial_birth(
    intent: GrowthBirthIntentV1,
) -> GrowthBirthTrialReceiptV1

def read_growth_trial_receipt(
    operation_id: str,
) -> GrowthBirthTrialReceiptV1 | None
```

Vincoli obbligatori:

1. La sola capability nominale è
   `growth_pipeline:create_or_extend`, costruita col meccanismo sigillato già
   esistente. È un nuovo nome di percorso, non una nuova autorità: ricevuta,
   issuer, chiavi e registri Producer sono quelli RM-0008 già esistenti. Il
   chiamante non sceglie issuer, capability, actor, `reason`, keyring, runner
   o publisher.
2. La factory calcola internamente `candidate_source_id` dai byte congelati.
   `request_id` è un hash domain-separated e length-framed di capability,
   `operation_id`, `decision_receipt_id`, `intent_fingerprint`, `change_kind`,
   contratto e `candidate_source_id`.
3. Nello stesso database Producer, una registrazione core-owned lega
   atomicamente `operation_id` a binding completo, `request_id` e receipt
   Producer. Stessa operation e stessi byte convergono; qualunque variazione
   dà `operation_binding_conflict` prima di una seconda Birth.
4. La risposta nasce soltanto dopo rilettura e verifica dell'envelope terminale
   e della receipt Admission. Il reader ripete la stessa verifica e restituisce
   `None` soltanto se l'operation non è mai stata registrata; una registrazione
   non terminale o rifiutata usa il relativo errore chiuso.
5. La receipt D1 deve avere `approved_lifecycle=preexercise`; `active` è errore
   `growth_trial_lifecycle_invalid`.
6. La façade non legge né valida FS-A da un booleano del chiamante. Il lettore
   core della release selezionata la rilegge immediatamente prima del runner e
   immediatamente prima dell'invio. Assenza, revoca o divergenza produce
   `dependency_unready`; RM-0009 traduce questo in `blocked` prima di aprire
   l'operation o `waiting_dependency` se l'operation esiste già.

La registrazione logica minima è:

```text
birth_growth_operation_bindings_v1
  operation_id PRIMARY KEY
  binding_id NOT NULL
  request_id NOT NULL UNIQUE
  candidate_source_id NOT NULL
  producer_receipt_id NOT NULL UNIQUE
  admission_receipt_id NULL
  terminal_envelope_hash NULL
```

Binding, request e receipt Producer devono nascere nella stessa transazione
della issuance Producer; receipt Admission e hash terminale devono essere
aggiornati atomicamente con la finalizzazione già esistente. Il numero fisico
di migrazione non è disponibile prima del work manifest D-G0.6 e non va
inventato in D-G0.3.

### 4.2 Superficie D2

D-G0.3 deve congelare il confine, non fornire oggi una prova F5:

```python
class GrowthLifecycleTransitionV1(str, Enum):
    ACTIVATE = "activate"
    QUARANTINE = "quarantine"
    ROLLBACK = "rollback"

@dataclass(frozen=True, slots=True)
class GrowthLifecycleIntentV1:
    operation_id: str
    decision_receipt_id: str
    admission_receipt_id: str
    contract_id: ContractId
    expected_generation_id: str
    expected_state_version: int
    transition: GrowthLifecycleTransitionV1
    historic_epoch_ref: str | None = None

@dataclass(frozen=True, slots=True)
class GrowthLifecycleReceiptV1:
    operation_id: str
    admission_receipt_id: str
    previous_generation_id: str
    current_generation_id: str
    lifecycle: ApprovedLifecycle
    state_version: int
    historic_epoch_ref: str | None

def transition_growth_lifecycle(
    intent: GrowthLifecycleIntentV1,
) -> GrowthLifecycleReceiptV1
```

La façade carica internamente `EXT-RM0008-F5`, FS-A, receipt, manifest,
preesercizio e FS-B quando applicabile; non accetta `F5Activation`, chiavi,
proof bytes, callback di pubblicazione o callback di verifica. Pubblica,
rilegge e applica il CAS dell'epoca esatta. Per `ROLLBACK` il lifecycle finale
è `active` e `historic_epoch_ref` è obbligatorio. Un fatto mancante, non
verificabile, revocato o riferito a un'altra release restituisce
`dependency_unready`; nessun consenso lo supera.

### 4.3 Prove di release consumate dalle façade

Il consumer RM-0009 può essere congelato ora su una proiezione verificata:

```text
VerifiedReleaseFactV1
  proof_kind: EXT-RM0008-F5 | RM0009-FS-A
  contract_revision
  source_commit
  release_sequence
  closed_build_id
  evidence_digest
  authority_key_id
```

La proiezione è emessa soltanto dal verificatore core usando l'autorità di
release esistente e ricalcolando i digest correnti. Non è un record fornito
dal candidato o dal chiamante.

Il contenitore fisico definitivo non è disponibile: gli schemi committed del
distribution record e del deployment descriptor sono chiusi e non prevedono
queste prove. Il maintainer RM-0008 deve scegliere una revisione dello schema o
un artefatto firmato e referenziato dalla distribuzione, sempre con l'autorità
esistente. Questa scelta di provider non impedisce di implementare e testare
in isolamento il reader fail-closed di F5.1.

## 5. Dipendenze esterne e responsabilità

| Incarico | Responsabile | Serve a D-G0.3/I1.1? | Serve all'esercizio reale? |
|---|---|---|---|
| Approvare nomi, campi, framing e capability della §4; riallineare i registri di confine | maintainer RM-0008 + coordinatore RM-0009 | sì, per chiudere D-G0.3 | sì |
| Implementare façade growth, binding operation→request→receipt e reader Admission | owner core RM-0008, integrato da D-F5.4 | contratto sì; codice preliminare usa adattatore inerte | sì per D1 reale |
| Decidere il contenitore firmato delle prove senza nuova autorità | owner distribuzione RM-0008 | basta congelare l'interfaccia del reader | sì |
| Ritirare `test_runner.py`, convertire i 33 manifest e produrre FS-A | tranche D-FS-A | no per I1.1 | sì prima di runner/invio D1 reale |
| Collegare lifecycle a loader/router/cache/durable/feedback, provare 5 receipt, 2 Producer, 2 cicli e 0 difetti, emettere `EXT-RM0008-F5` | D-X0.1 / maintainer RM-0008 | no per I1.1 | sì per D2 e attivazione |
| Produrre FS-B se il contratto raggiunge credenziali o piano di controllo | tranche D-FS-B | no per I1.1 | sì per la D2 applicabile |
| Abilitare enforcement F6 locale/remoto/durevole | tranche D-F6.3-D-F6.6 | no; I1.1 termina con F6 in ombra | sì per i percorsi realmente governati in I0 |

## 6. Criterio di chiusura corretto

D-G0.3 può chiudere quando i due maintainer approvano il contratto della §4,
l'emendamento sostituisce la prescrizione RM-0008 §9 sul runner legacy, le due
roadmap non contengono altre istruzioni concorrenti e il registro delle API
riflette la façade scelta. Non richiede l'emissione di FS-A o
`EXT-RM0008-F5`.

D-F5.4/I1.1 possono collaudare con adattatore inerte e chiavi esclusivamente
di test:

- FS-A assente/revocata/divergente → zero invii reali;
- due worker e crash sulla stessa `operation_id` → una `request_id` e una
  receipt;
- binding diverso sulla stessa operation → `operation_binding_conflict`;
- receipt mancante/non autenticata → nessun passaggio ad
  `awaiting_activation`;
- D2 resta `blocked/dependency_unready`; F6 resta in ombra.

Solo I0, dopo D-X0.1, D-FS-A.4 e le altre protezioni applicabili, può usare
Birth e lifecycle reali e trasformare queste prove di codice in claim
operativi verificati.
