# RM-0009 — audit del contratto Birth e proposta D-G0.3

Data dell'osservazione: 2026-09-15  
Tipo di lavoro: sola lettura e proposta normativa; nessuna implementazione,
prova Birth, prova runner, modifica di servizio, database, release o worktree
RM-0008.  
Stato: **BLOCKED_BASELINE + BLOCKED_CONTRACT**.

## 1. Esito

Non si deve congelare D-G0.3 sul checkout principale. Il checkout principale
e la linea RM-0008 hanno antenato comune
`f16991224fdacbec903180ddd7f39f114a847147`, ma sono linee divergenti; allo
snapshot più recente di questa analisi il conteggio era `13` commit solo main
e `1089` solo RM-0008. La roadmap RM-0008 nel main ha 1.581 righe e digest
`sha256:676a33d8bd9989a75353d1b2ae50e7c4083b9d120fab0450bf4de2dd29f43490`;
quella nel worktree RM-0008 ha 3.495 righe e digest
`sha256:2c035e4273c254fe75152cf7d6e046a31642a649ea661c276682c1fcf3411de0`.
Il main perderebbe quindi la specifica e il codice Birth più recenti.

Il commit RM-0008 indicato inizialmente,
`1a9f42fb3dd8dd39a84ba2f7957a9f26abb7782e`, non è più una baseline finale:
durante l'audit il worktree è avanzato a
`de76f375d2704062a9248ce4b718d75cc634eb34` e il rapporto di rilascio è tornato
modificato. Per istruzione del responsabile, un altro agente sta ancora
lavorando su RM-0008. Nessun commit visto durante questa finestra può essere
congelato finché il maintainer non dichiara concluso e committato il lavoro.

L'alternativa sicura è un **nuovo worktree RM-0009 creato, in futuro, dal
commit finale pulito comunicato dal maintainer RM-0008**. La rev8 RM-0009 e gli
strumenti RM-0009 dovranno essere riportati in quel worktree come patch
esplicite, con provenienza e digest registrati. Non va eseguito merge o
cherry-pick cumulativo delle modifiche presenti nel main; non va toccato il
worktree RM-0008 esistente né la release installata.

Questa strategia è coerente come isolamento, ma non autorizza l'implementazione
finché non sono risolti i due blocchi seguenti:

1. `BLOCKED_BASELINE`: manca ancora il commit finale, pulito e dichiarato dal
   maintainer RM-0008, e manca la riconciliazione firmata tra quel commit e la
   release selezionata.
2. `BLOCKED_CONTRACT`: l'API corrente non lega `operation_id` RM-0009 alla
   richiesta/receipt Birth e non restituisce una ricevuta Admission
   verificabile. Il maintainer RM-0008 deve approvare la capability Producer,
   la derivazione idempotente e il reader delle ricevute prima di D-G0.6.

Non è stata osservata né dichiarata in questo rapporto alcuna certificazione
F5, `EXT-RM0008-F5` o FS-A.

## 2. Fonti normative e tecniche lette

Sono stati letti integralmente:

- `CLAUDE.md`;
- `CLAUDE.mutabile.md`;
- `internal/AGENTS.md`;
- `internal/roadmap/RM-0009-crescita-allineata-delle-capacita.md`, con
  particolare riesame dei §§1-6, Appendici A e D ed E.2;
- `internal/roadmap/RM-0008-porta-unica-nascita-executor.md` nel main e nel
  worktree RM-0008;
- `internal/reports/rm0008-release-20260915.md` nel worktree RM-0008;
- `internal/design/handover_rm0008_stop_9_9_2026_1526.md`, usato soltanto come
  documento storico: la sua selezione della release 2 è superata dal rapporto
  corrente.

Il rapporto RM-0008 osservato dichiarava release 51 `selected and ready`, con
source `sha256:0e2ea9995baec01f6755e8253bca53699b193285fe10619789147ebb2aae3eee`,
build `sha256:7048815b433d82361c216a1a081aa70712e378728b58572db0c9b7f883402544`
e head `sha256:5fce7ebf4bf785b5613c93400b7a7176e630f091c6d3fb1b38bdc8b8a8b35d15`.
Durante l'audit era già comparsa la directory immutabile della release 52 e il
rapporto era in modifica. Questi fatti rendono la release 51 una fotografia
provvisoria, non la baseline finale di RM-0009.

Il file osservato
`deployment/executor-birth-deployment-v1.json` della release 51 ha
`schema_version: 1`, `release_sequence: 51`, `descriptor_id` e l'inventario
degli artefatti, ma non contiene commit/source/build/head né proof F5/FS-A. I
tre digest sopra provengono dal rapporto umano e non possono sostituire il
record machine-readable firmato richiesto da RM-0009.

Non sono stati letti contenuti di chiavi, segreti, dati personali o log
personali. Non sono stati avviati servizi, runner legacy, test funzionali o
prove Birth.

## 3. Divergenza concreta main / RM-0008 / release osservata

La tabella riporta digest dei file direttamente rilevanti alla porta e al
runner. `release-51` significa la fotografia immutabile osservata, non una
certificazione corrente.

| file | main | worktree RM-0008 | release-51 |
|---|---|---|---|
| `runtime/synth_request.py` | `32fb2f0b...` | `b8a198eb...` | `b8a198eb...` |
| `runtime/test_runner.py` | `8f0d1461...` | `8f0d1461...` | `8f0d1461...` |
| `runtime/executor_birth_functional.py` | assente | `856b630d...` | `856b630d...` |
| `runtime/executor_birth_runner.py` | `0b709d6b...` | `a7c79f26...` | `a7c79f26...` |
| `runtime/executor_birth_property_runner.py` | `7e6077db...` | `a42301af...` | `a42301af...` |
| `runtime/executor_birth_identity.py` | `27ebb448...` | `489239c6...` | `489239c6...` |
| `runtime/executor_birth_synth.py` | `ae689d24...` | `54b5214a...` | `54b5214a...` |
| `runtime/executor_birth_operational.py` | `b48f762d...` | `328c8a23...` | `328c8a23...` |
| `runtime/executor_birth_transition_receipts.py` | assente | `140f9ce2...` | `140f9ce2...` |
| `runtime/executor_birth_receipts.py` | `10ede8cd...` | uguale | uguale |
| `runtime/executor_birth_preexercise.py` | `d658c9ea...` | uguale | uguale |
| `runtime/executor_birth_lifecycle.py` | `2c4ba68e...` | uguale | uguale |

Per i file riportati in forma abbreviata, il digest completo è riproducibile
con `sha256sum` sui tre root indicati nel §10.

La differenza funzionale decisiva è questa:

- nel main, `runtime/synth_request.py` invoca ancora
  `runtime/test_runner.py` come processo host prima di chiamare Birth;
- nella linea RM-0008/release 51, `synth_request.py` costruisce
  `SynthTestData`, chiama `validate_synth_tests` e consegna l'esecuzione al
  runner Birth sigillato prima della pubblicazione;
- anche nella linea più nuova, però, `executor_birth_functional.py` importa
  ancora `check_expect` da `test_runner.py`, il file legacy esiste ancora e
  33 manifest contengono ancora campi `setup`, `teardown` o `env`.

Congelare sul main sarebbe quindi errato sia normativamente sia tecnicamente:
riaprirebbe il runner host esattamente nel punto che D-FS-A e D-G0.3 devono
chiudere.

## 4. Inventario dei percorsi di crescita

### 4.1 Produttori di `synt_pending`

I soli produttori di marker trovati nel codice produttivo sono:

1. `runtime/proposal_actions.py`, `on_accept`, chiamata
   `_write_marker(SYNT_PENDING_DIR, ...)`;
2. `runtime/engine/fastpath_promote.py`, `approve_proposal`, chiamata
   `proposal_actions._write_marker(proposal_actions.SYNT_PENDING_DIR, ...)`.

Il consumer è `runtime/telos_synth_consumer.py`, `run_once`; la pianificazione
passa da `runtime/scheduler_v2/builtin_callbacks.py`.

### 4.2 Chiamanti produttivi di `handle_synth_request`

Sono stati trovati tre chiamanti diretti:

1. `runtime/change_applier.py`, `apply_create_executor`;
2. `runtime/engine/fastpath_promote.py`, `auto_synthesize`;
3. `runtime/telos_synth_consumer.py`, `run_once`.

Questi tre ingressi, i due produttori marker e il callback scheduler sono il
set minimo che D-F5.9 deve rendere non aggirabile. Il solo controllo dei marker
non basta, perché `change_applier` e `auto_synthesize` possono chiamare la
sintesi direttamente.

### 4.3 Porta Birth reale e produttori autenticati

La singola funzione pubblica dichiarata è:

```python
birth_executor(request: BirthRequest) -> BirthResult
```

Nel codice produttivo osservato, però, i chiamanti non costruiscono direttamente
`BirthRequest`: attraversano undici façade di `runtime/executor_birth_intent.py`.
Tutte convergono in `_submit` → `_execute_intent_with_capability` → `_execute`
con una capability sigillata. Non è stato trovato alcun chiamante produttivo
diretto della funzione pubblica `birth_executor`.

Le capability/façade e i chiamanti sono:

| Producer / operazione | façade | chiamante reale |
|---|---|---|
| `change_applier:extend` | `submit_change_extend_birth` | `runtime/change_applier_extend.py` |
| `change_rollback:rollback` | `submit_change_rollback_birth` | `runtime/change_rollback.py` |
| `synt_multistage:create_or_replay` | `submit_synth_multistage_birth` | `runtime/executor_birth_synth.py`, usata da `runtime/synth_request.py` |
| `synt_specialize:specialize_or_replay` | `submit_synth_specialize_birth` | `runtime/executor_birth_synth.py` |
| `synt_approve:approve_or_replay` | `submit_synth_approve_birth` | `runtime/executor_birth_synth.py` |
| `promoter:promote` | `submit_promote_birth` | `runtime/jobs/promoter_promote.py` |
| `stack_reconcile:restart_sign_first` | `submit_stack_reconcile_birth` | `runtime/stack_reconcile.py` |
| `skills_cli:skill_import_or_reactivation` | `submit_skills_birth` | `runtime/cli/skills_cli.py` |
| `installer_phase3:install` | `submit_installer_birth` | `install/phases/phase3_code.py` |
| `builtin_contract_generator:generate_builtin` | `submit_builtin_generation_birth` | `scripts/generate_builtin_executor_contracts.py` |
| `promoter:rollback` | `submit_promoter_rollback_birth` | `runtime/jobs/promoter_rollback.py` |

Questa architettura conferma che RM-0009 non deve chiamare
`birth_executor` direttamente né fabbricare receipt o autorità: deve ricevere
dal maintainer RM-0008 una façade Producer sigillata e nominale.

## 5. Firme, receipt e lacune del contratto corrente

### 5.1 Tipi pubblici osservati

`BirthIntent` contiene:

```text
candidate_source_root: Path
contract_id: ContractId
reason: str
approval_refs: tuple[str, ...] = ()
```

`BirthRequest` contiene:

```text
request_id, manifest_ref, producer_receipt, actor, reason,
approval_refs, operation_hint, candidate_source_root
```

`BirthResult` contiene:

```text
request_id, report, publication, error_code, diagnostic
```

`PublicationResult` contiene soltanto:

```text
contract_id, previous_generation_id, current_generation_id,
operation, repeated
```

`AdmissionReceipt` contiene invece gli identificatori necessari a RM-0009:
`receipt_id`, `contract_id`, `generation_id`, `candidate_id`,
`semantic_core_id`, `admission_context_id`, `birth_request_id`,
`predecessor_id`, `producer_receipt_hash`, `approved_lifecycle`, esiti e firma.

### 5.2 Receipt non esportata

La receipt Admission codificata è inclusa nell'envelope terminale firmato del
Producer store. `BirthResult` non la restituisce, `PublicationResult` non ne
espone il riferimento e non esiste una façade Producer pubblica che permetta a
RM-0009 di rileggerla per `operation_id`. Esistono reader/verifier interni,
compreso `verify_terminal_registration_v2`, ma richiedono la richiesta
sigillata e dipendenze del core e non sono un contratto consumabile da RM-0009.

Questa è una lacuna bloccante per D-F5.4: un esito `publication is not None`
non è la “receipt verificata” richiesta dalla roadmap RM-0009. È un rilievo
conservativo sullo snapshot provvisorio osservato: non qualifica una futura
baseline e non presuppone, né dichiara, alcuna prova FS-A.

### 5.3 Idempotenza non legata a `operation_id`

La request V1 è derivata da issuer, capability operation, contract,
`objective_hash` e `candidate_source_id`. L'`objective_hash` deriva da `reason`
e `approval_refs`. Non esiste un campo `operation_id` o `decision_receipt_id`.

Il replay di `synth_request.py` su un candidato già pubblicato usa inoltre un
reason diverso (`replay synthesized candidate ...`) da quello iniziale
(`synt multistage: ...`). Un crash dopo la pubblicazione e prima della
registrazione dell'esito RM-0009 può quindi derivare una seconda identità
Birth, invece della stessa richiesta/receipt. Il test richiesto da D-F5.4
“crash e due worker → una sola richiesta e receipt” non è dimostrabile con
l'interfaccia corrente.

### 5.4 Preesercizio, attivazione e rollback

- Per origine `synthesized`, `approval_scope` restituisce soltanto
  `preexercise`. La receipt Admission lega l'`approved_lifecycle`.
- `runtime/executor_birth_preexercise.py` implementa la politica chiusa di
  eleggibilità, ma non è una prova di integrazione produttiva F5.
- `runtime/executor_birth_lifecycle.py` contiene `load_f5_activation` e
  `LifecycleCoordinator`. La costruzione del coordinatore richiede un
  `F5Activation` sigillato ottenuto da un certificato F4 autenticato con almeno
  5 receipt, 2 producer, 2 cicli e 0 difetti.
- Fuori dal modulo e dai test non sono stati trovati chiamanti produttivi di
  `load_f5_activation` o `LifecycleCoordinator`: il coordinatore è ancora
  irraggiungibile dalla selezione produttiva.
- Il rollback di contenuto attraversa Birth in `runtime/change_rollback.py` e
  `runtime/jobs/promoter_rollback.py`. Il rollback di lifecycle active→active
  con `historic_epoch_ref` esiste nel coordinatore, ma non è collegato al ciclo
  produttivo.

La roadmap RM-0008 §23.4 richiede ancora migrazione dello stato, collegamento
al publisher RM-0007 reale, esclusione preexercise da selezione/durevoli,
invalidazione delle cache, guardia del tentativo durevole e feedback reale.
Funzioni callback e database temporanei sono esplicitamente insufficienti.

## 6. Destino di `test_runner.py` e stato FS-A

Il destino normativo minimo deve essere **ritiro completo**, non semplice
inutilizzo nel percorso Synt:

1. migrare tutti i 33 manifest con campi legacy;
2. rendere `setup`, `teardown` ed `env` errori tipizzati della grammatica;
3. spostare il matcher puro `check_expect` in un modulo neutro posseduto dal
   runner Birth;
4. rimuovere l'import da `executor_birth_functional.py`;
5. eliminare `runtime/test_runner.py` e ogni riferimento/census residuo;
6. produrre la prova FS-A nel manifest firmato della release selezionata.

Inventario provvisorio identico tra worktree RM-0008 e release 51:

- 216 righe legacy;
- 33 manifest distinti;
- 5 riferimenti di codice/testo a `test_runner` nel codice produttivo, inclusi
  il file stesso, l'import `check_expect`, un riferimento nel census e il
  commento di indipendenza del nuovo runner.

I 33 manifest sono:

```text
executors/compress_files/manifest.toml
executors/compute_files_loc/manifest.toml
executors/consult_frontier/manifest.toml
executors/create_dirs/manifest.toml
executors/create_files_spreadsheet/manifest.toml
executors/create_images_indices/manifest.toml
executors/delete_dirs/manifest.toml
executors/delete_files/manifest.toml
executors/find_contacts/manifest.toml
executors/find_dirs/manifest.toml
executors/find_files/manifest.toml
executors/find_files_hash/manifest.toml
executors/find_images_google_photos/manifest.toml
executors/get_approval/manifest.toml
executors/get_files/manifest.toml
executors/get_images_google_photos/manifest.toml
executors/get_inputs/manifest.toml
executors/get_location/manifest.toml
executors/get_places/manifest.toml
executors/get_proposals/manifest.toml
executors/list_dirs/manifest.toml
executors/move_files/manifest.toml
executors/read_contacts/manifest.toml
executors/read_files/manifest.toml
executors/read_files_csv/manifest.toml
executors/read_files_doc/manifest.toml
executors/read_files_ocr/manifest.toml
executors/read_files_xlsx/manifest.toml
executors/read_messages/manifest.toml
executors/undo_last_turn/manifest.toml
executors/write_files/manifest.toml
executors/write_files_doc/manifest.toml
executors/write_images_google_photos/manifest.toml
```

Di conseguenza, FS-A è **non prodotta**. Il verde di moduli runner esistenti
non potrebbe certificare il requisito D-FS-A.4 finché questi inventari non sono
vuoti e la release corrente non lega codice, configurazione, inventario e suite.

## 7. Proposta tecnica minima per D-G0.3 e D-G0.6

### 7.1 Testo normativo proposto per D-G0.3

> Tutte le operazioni classificate `create_executor` o `extend_executor`,
> comprese le proposte accettate, `fastpath_promote`, `change_applier` e i
> consumer schedulati, attraversano il decision record RM-0009 D1 prima di
> creare o inviare un candidato. L'unica porta di ammissione resta il core
> RM-0008; RM-0009 usa una sola façade Producer sigillata assegnata dal
> maintainer RM-0008 e non può costruire `BirthRequest`, receipt, keyring,
> runner, comando, environment o publisher. Immediatamente prima
> dell'esecuzione di codice candidato e dell'invio reale la façade rilegge FS-A
> per la release selezionata; assenza, revoca o drift produce
> `blocked/dependency_unready` o, per operation già aperta,
> `waiting_dependency`, con zero invii. D2 resta non aggirabile finché
> `EXT-RM0008-F5`, FS-A e le altre prove applicabili non sono verificate.
> `runtime/test_runner.py`, i relativi campi `setup|teardown|env`, la grammatica
> e tutti i riferimenti vengono rimossi; nessun fallback host è ammesso.

### 7.2 Interfaccia proposta

Quanto segue è una **proposta contrattuale ancora da approvare**, non
un'interfaccia già normativa o congelata. Gli obblighi già normativi sono:
porta Birth unica e non aggirabile, D1 subordinata a FS-A, D2 subordinata a
`EXT-RM0008-F5` e alle altre prove applicabili, receipt riletta, idempotenza e
assenza di fallback host. Nome, forma e framing dei tipi restano decisioni di
D-G0.3/D-G0.6 con il maintainer RM-0008. Nome candidato del modulo core-owned:
`runtime/executor_birth_growth.py`.

```python
@dataclass(frozen=True, slots=True)
class GrowthBirthRequestV1:
    operation_id: str                 # ID opaco stabile; encoding da approvare
    decision_receipt_id: str          # D1 autenticata/riletta
    intent_fingerprint: str           # intent canonico immutabile
    change_kind: Literal["create_executor", "extend_executor"]
    contract_id: ContractId
    candidate_source_root: Path       # staging privato, non authority
    candidate_source_id: str          # sha256 dei byte congelati
    reason_code: Literal["rm0009_d1_create", "rm0009_d1_extend"]
    approval_refs: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class GrowthBirthTrialReceiptV1:
    operation_id: str
    birth_request_id: str
    producer_receipt_id: str
    admission_receipt_id: str
    contract_id: str
    generation_id: str
    predecessor_id: str | None
    approved_lifecycle: Literal["preexercise"]
    terminal_envelope_hash: str
    repeated: bool

def submit_growth_birth(
    request: GrowthBirthRequestV1,
) -> GrowthBirthTrialReceiptV1
def read_growth_birth_trial_receipt(
    operation_id: str,
) -> GrowthBirthTrialReceiptV1
```

Vincoli dell'interfaccia:

- `operation_id`, `decision_receipt_id`, `intent_fingerprint`, capability,
  contract e `candidate_source_id` entrano nella derivazione domain-separated
  della request e nella registrazione durevole; forma, dominio e lunghezza di
  `operation_id` restano da approvare e non sono qui fissati a `sha256`;
- nessun testo utente o `reason` libero entra negli store globali, incluso il
  journal delle operazioni RM-0009: la façade deriva soltanto un `reason_code`
  chiuso. L'eventuale testo della richiesta resta nei soli store operativi
  owner-scoped già autorizzati, soggetti a minimizzazione e cancellazione;
- identico retry restituisce gli stessi byte autenticati; ogni variazione dà
  `operation_binding_conflict` senza una seconda ammissione;
- `submit_growth_birth` usa internamente la capability Producer sigillata e il
  solo `_execute` Birth; non esporta authority o callback;
- la risposta è costruita soltanto dopo rilettura e verifica dell'envelope
  terminale e della receipt Admission;
- la façade rilegge FS-A dal manifest della release selezionata subito prima
  del runner e subito prima dell'invio; nessun booleano “FS-A ready” può essere
  passato dal chiamante;
- la D1 consente al massimo una receipt `preexercise`; `active` richiede la
  separata porta lifecycle con `EXT-RM0008-F5` validata dal core.

Per D2, la porta minima è:

```python
@dataclass(frozen=True, slots=True)
class GrowthLifecycleRequestV1:
    operation_id: str
    decision_receipt_id: str
    admission_receipt_id: str
    contract_id: ContractId
    expected_generation_id: str
    expected_state_version: int
    transition_kind: Literal["activate", "quarantine", "rollback"]
    target_lifecycle: Literal["active", "quarantined"]
    historic_epoch_ref: str | None = None

@dataclass(frozen=True, slots=True)
class GrowthLifecycleReceiptV1:
    operation_id: str
    transition_kind: Literal["activate", "quarantine", "rollback"]
    admission_receipt_id: str
    contract_id: str
    previous_generation_id: str
    current_generation_id: str
    lifecycle: Literal["active", "quarantined"]
    historic_epoch_ref: str | None

def transition_growth_lifecycle(
    request: GrowthLifecycleRequestV1,
) -> GrowthLifecycleReceiptV1
```

Questa funzione deve caricare internamente `EXT-RM0008-F5`, rileggere la
receipt, pubblicare, rileggere e fare il CAS dell'epoca esatta. RM-0009 non può
passare un `F5Activation` né una callback di pubblicazione. Il discriminante
`transition_kind` separa attivazione, quarantena e rollback; per `rollback` il
target resta `active` e `historic_epoch_ref` è obbligatorio. La receipt D1 non
può quindi rappresentare accidentalmente una transizione D2.

### 7.3 Decisione obbligatoria del maintainer RM-0008

Il responsabile RM-0008 deve scegliere e registrare, prima del congelamento:

1. se introdurre una capability `growth_pipeline:create_or_extend` oppure
   estendere nominalmente `synt_multistage` e `change_applier`; la prima è
   preferibile perché impedisce a RM-0009 di scegliere capability esistenti;
2. forma e framing esatti che legano `operation_id` e decision receipt a
   `BirthRequest.request_id`, senza abusare di `reason` o `approval_refs`;
3. il proprietario del mapping durevole operation→candidate→request→receipt e
   del reader autenticato;
4. il punto del manifest/distribution record firmato esistente che ospita
   `EXT-RM0008-F5` e FS-A, inclusa l'eventuale revisione di schema;
5. la façade lifecycle che rende produttivamente raggiungibile
   `LifecycleCoordinator` senza esportarne authority e callback.

Finché questa decisione non è presa, D-G0.3 non ha un contratto implementabile
senza inventare autorità o rischiare una doppia Birth.

### 7.4 Congelamento D-G0.6

D-G0.6 può essere chiuso soltanto quando il work manifest registra almeno:

- nomi definitivi dei moduli/façade e dei tipi sopra;
- schema canonico, domini hash, error code e versioni;
- produttore/capability approvati da RM-0008;
- elenco chiuso di tutti gli ingressi del §4 e test per ciascuno;
- reader e verifier di `EXT-RM0008-F5`, FS-A e receipt;
- il DAG come **ordine parziale**, con gli archi normativi
  `FS-A → D1`, `D1 → Birth receipt verificata → preexercise`,
  `{EXT-RM0008-F5, FS-A, receipt Birth, manifest firmato, preexercise,
  FS-B se applicabile} → D2` e `D2 → active`; FS-A e F5 sono prerequisiti,
  non attività successive rispettivamente a D1 e D2, e tra i prerequisiti
  indipendenti non viene imposto un ordine lineare artificiale;
- rollback/quarantena come transizioni Birth con receipt e CAS;
- controllo statico che vieta chiamate dirette a `handle_synth_request`,
  `birth_executor`, `_execute`, producer factories e runner legacy fuori dagli
  owner dichiarati.

## 8. Proposta proof `EXT-RM0008-F5` e FS-A

### 8.1 `EXT-RM0008-F5`

La prova deve essere un record canonico machine-readable firmato con
l'autorità di release/distribuzione RM-0008 già esistente. Non va creata una
nuova chiave o un nuovo servizio di firma. Schema minimo:

```json
{
  "schema_version": 1,
  "proof_kind": "EXT-RM0008-F5",
  "contract_revision": "rm0008-f5/<frozen-revision>",
  "source_commit": "<40-hex>",
  "release_sequence": 0,
  "closed_build_id": "sha256:<64hex>",
  "distribution_record_id": "sha256:<64hex>",
  "f4_certificate_id": "sha256:<64hex>",
  "admission_receipt_ids_digest": "sha256:<64hex>",
  "producer_ids_digest": "sha256:<64hex>",
  "routing_cycle_receipts_digest": "sha256:<64hex>",
  "integration_suite_manifest_digest": "sha256:<64hex>",
  "integration_suite_result_digest": "sha256:<64hex>",
  "unresolved_defects": 0,
  "issued_at": "YYYY-MM-DDTHH:MM:SSZ",
  "authority_key_id": "<existing-release-key-id>",
  "signature": "<canonical-base64>"
}
```

Il verifier RM-0009 deve ricostruire, non fidarsi di contatori dichiarati:

- almeno 5 receipt Admission reali, uniche e rilette;
- almeno 2 Producer autenticati distinti;
- 2 cicli di routing reali consecutivi;
- 0 difetti o bypass irrisolti;
- completamento dei sette punti di integrazione RM-0008 §23.4 sui percorsi
  loader/router/cache/durable/feedback reali;
- corrispondenza esatta con commit, build chiuso, release selezionata e
  revisione del contratto.

Firma valida con digest o release divergenti non basta. Prova mancante,
revocata, riferita a release non selezionata o con evidenze non rileggibili dà
`FactState.UNVERIFIED` e D2 `blocked/dependency_unready`.

L'attuale `F4Certification` è solo una primitiva: autentica threshold minima,
ma non lega commit, build, suite e integrazione F5 reale. Non è di per sé
`EXT-RM0008-F5`.

### 8.2 FS-A

FS-A deve essere una sezione/artefatto del medesimo manifest di release firmato
e deve poter essere prodotta indipendentemente dal codice F5:

```json
{
  "schema_version": 1,
  "proof_kind": "RM0009-FS-A",
  "contract_revision": "birth-runner/<frozen-revision>",
  "source_commit": "<40-hex>",
  "release_sequence": 0,
  "closed_build_id": "sha256:<64hex>",
  "runner_code_digest": "sha256:<64hex>",
  "runner_config_digest": "sha256:<64hex>",
  "legacy_inventory_digest": "sha256:<empty-canonical-inventory>",
  "legacy_manifest_fields": 0,
  "legacy_runner_references": 0,
  "suite_manifest_digest": "sha256:<64hex>",
  "suite_result_digest": "sha256:<64hex>",
  "platform_results": [
    {"platform": "ubuntu-24.04", "status": "passed"},
    {"platform": "windows-2022", "status": "passed"}
  ],
  "issued_at": "YYYY-MM-DDTHH:MM:SSZ",
  "authority_key_id": "<existing-release-key-id>",
  "signature": "<canonical-base64>"
}
```

La suite deve attestare almeno: backend registrato e indisponibilità tipizzata,
namespace/rete/process tree/utente/IPC/UTS/cgroup su Linux, AppContainer/job
object e drenaggio discendenti su Windows, ambiente chiuso, cwd/fixture
effimeri, timeout unico, CPU/RAM/output limitati, nessun comando/callback/env
fornito dal candidato, equivalenza dei manifest migrati e rifiuto dei campi
legacy. Il verifier ricalcola i digest sul rilascio selezionato; ogni drift
invalida la prova.

## 9. Test esatti riutilizzabili e gap

Nessuno di questi test è stato eseguito durante l'audit. I nomi seguenti sono
quelli esistenti nello snapshot RM-0008 e sono candidati al riuso.

### Porta, receipt e idempotenza

- `tests/runtime/executors/test_executor_birth_intent.py`:
  `test_productive_adapter_is_fail_closed_before_bootstrap`,
  `test_adapter_requires_a_real_birth_request`.
- `tests/runtime/executors/test_executor_birth_synth.py`:
  `test_public_synth_adapter_accepts_data_not_trust_authorities`,
  `test_rejected_and_replayed_approval_never_claim_publication`.
- `tests/runtime/executors/test_executor_birth_operational.py`:
  `test_admitted_pipeline_commits_receipt_and_replays_verified_postcondition`,
  `test_terminal_replay_survives_signing_key_rotation`,
  `test_terminal_envelope_tampering_fails_closed_before_checks_or_publish`,
  `test_concurrent_exact_retries_converge_on_one_committed_binding`,
  `test_forged_actor_cannot_be_expressed_or_select_another_capability`.
- `tests/runtime/executors/test_executor_birth_producer_store.py`:
  `test_atomic_issuance_and_claim_is_single_under_concurrency`,
  `test_atomic_factory_retry_renews_same_request_after_lease_expiry`,
  `test_terminal_result_is_durable_idempotent_and_conflicts_fail`,
  `test_concurrent_different_requests_have_one_permanent_owner`.
- `tests/runtime/contracts/test_executor_birth_receipts.py`:
  `test_admission_round_trip_preserves_null_and_closed_checks`,
  `test_admission_keyring_verifies_history_and_rejects_unknown_or_revoked_keys`,
  `test_admission_tamper_wrong_key_id_and_signature_are_rejected`.
- `tests/runtime/executors/test_executor_birth_postcondition.py`:
  `test_verifies_committed_and_crash_after_publish`,
  `test_surviving_journal_must_match_receipt_and_request`.

Gap: manca un test end-to-end
`operation_id → due worker/crash → identico birth_request_id e identica
admission_receipt_id`, perché l'interfaccia non esiste ancora.

### Runner e FS-A

- `tests/runtime/infra/test_executor_birth_runner.py`:
  `test_v1_policy_is_fixed_and_complete`,
  `test_shell_setup_and_teardown_are_not_part_of_public_api`,
  `test_shared_deadline_cannot_exceed_fixed_total_budget`,
  `test_real_linux_sandbox_cannot_see_undeclared_host_files`,
  `test_real_linux_sandbox_terminates_detached_descendants`.
- `tests/runtime/executors/test_executor_birth_functional.py`:
  `test_public_data_has_no_authority_or_host_path`,
  `test_missing_isolation_evidence_blocks_success`,
  `test_no_bundle_or_backend_cannot_fall_back`,
  `test_candidate_is_a_readonly_mount_not_just_mode_bits`,
  `test_real_functional_stdio_with_runtime_helpers_and_private_fixtures`.
- `tests/runtime/executors/test_executor_birth_functional_real.py`:
  `test_real_host_files_network_environment_and_source_are_isolated`,
  `test_real_descendants_are_drained`,
  `test_real_nonzero_exit_cannot_forge_a_success_summary`.
- `tests/runtime/executors/test_executor_birth_property_runner.py`:
  `test_observed_runner_refuses_missing_or_invalid_linux_registry_before_runner`,
  `test_real_linux_observed_runner_exercises_all_seven_groups_or_skips`,
  `test_the_registries_and_the_closed_table_must_agree_exactly`.
- `tests/portable/test_executor_birth_runner_linux_real.py`:
  `test_real_linux_runner_hides_host_environment_paths_and_network`,
  `test_real_linux_timeout_terminates_child_and_grandchild_cgroup`.
- `tests/portable/test_executor_birth_runner_windows.py`:
  `test_real_job_object_assigns_before_resume_and_drains_tree`,
  `test_productive_gate_requires_the_core_owned_sandbox_registry`.
- `tests/portable/test_executor_birth_runner_windows_appcontainer.py`:
  `test_real_appcontainer_denies_host_file_credentials_and_network_and_drains_descendant`,
  `test_real_appcontainer_kills_on_output_overflow`,
  `test_real_appcontainer_kills_on_timeout`.

Gap: mancano inventario legacy vuoto, cancellazione fisica del runner,
equivalenza di tutti i 33 manifest migrati e proof FS-A firmata/ricalcolata
sulla release selezionata.

### Preexercise, attivazione e rollback

- `tests/runtime/executors/test_executor_birth_preexercise.py`:
  `test_closed_policy_allows_only_known_eligible_read_only_synthesized_case`,
  `test_each_normative_exclusion_fails_closed`,
  `test_new_capability_is_ineligible_until_core_policy_names_it`.
- `tests/runtime/executors/test_executor_birth_lifecycle.py`:
  `test_activation_is_fail_closed_and_threshold_is_authenticated`,
  `test_coordinator_opens_only_after_exact_authenticated_reread`,
  `test_bad_reread_never_changes_epoch`,
  `test_rollback_opens_clean_epoch_with_historic_reference`.
- `tests/runtime/executors/test_executor_birth_epoch_store.py`:
  `test_transition_is_cas_and_cache_survives_failed_stale_transition`,
  `test_promotion_quarantine_and_retirement_each_invalidate_cache`,
  `test_feedback_cas_never_quarantines_successor_b_for_receipt_a`.
- `tests/runtime/learning/test_change_rollback_publication.py`:
  `test_create_rollback_retires_store_contract_before_archiving_source`,
  `test_create_rollback_keeps_source_when_retirement_needs_retry`.

Gap: questi sono test di modulo; mancano il percorso reale
loader→router→cache→durable→feedback, due cicli consecutivi e il certificatore
che ricostruisce le evidenze. Quindi non attestano F5.

### Chiusura dei percorsi D-F5.9

- `tests/runtime/learning/test_proposal_actions.py`:
  `test_new_valid_creates_synt_marker`,
  `test_cluster_dedup_idempotent`,
  `test_below_hard_gate_blocked_no_marker`.
- `tests/runtime/learning/test_telos_synth_consumer.py`:
  `test_dry_run_does_not_call_synth`,
  `test_priority_queue_alignment_desc`,
  `test_success_marker_moved_with_result`,
  `test_candidate_is_preserved_and_counts_as_success`.
- `tests/runtime/engine/test_fastpath_promote.py`:
  `test_approve_free_writes_consumable_marker`,
  `test_flag_on_floor_met_triggers_synt_once`,
  `test_synth_failure_keeps_proposal_pending`.
- `tests/runtime/learning/test_change_applier_extend.py`:
  `test_store_retry_reenters_publisher_when_section_already_exists`,
  `test_missing_birth_request_fails_before_any_authoring_write`.

Gap: i test esistenti provano il vecchio percorso marker/sintesi, non il veto
D1/FS-A. Per ciascun ingresso servono casi `prima di D1`, `FS-A
missing/revoked/drift`, rifiuto, retry/crash/concorrenza, adattatore inerte e
positivo fino a `trial_applying` ma mai `active`.

## 10. Doppia scansione e comandi riproducibili

### 10.1 Crescita/Birth

Comando, eseguito due volte senza modifiche tra i passaggi:

```bash
for root in \
  /opt/metnos \
  /opt/metnos/.claude/worktrees/rm0008-reboot \
  /var/lib/metnos/executor-birth/releases-v1/00000000000000000051
do
  (cd "$root" &&
    rg -n --no-heading --glob '*.py' --glob '!**/tests/**' \
      --glob '!**/.venv/**' \
      '(SYNT_PENDING_DIR|synt_pending|handle_synth_request|birth_executor\(|submit_[A-Za-z0-9_]*birth\(|load_f5_activation\(|LifecycleCoordinator\()' \
      runtime install scripts 2>/dev/null |
    LC_ALL=C sort | sha256sum)
done
```

Digest pass 1 = pass 2:

| root | digest |
|---|---|
| main | `1afcb0a25db754505a7d867730595b58e9e8aa8586473a3cb1909a5598825d31` |
| worktree RM-0008 | `6025a4ce8798935cdc29a012c96b4c32187717a3c0ecdeda7ca7da3afa66b4f2` |
| release 51 | `5329ad9dbe625bd23e1a938ce02cdf7c71bd814a9d13fd18f1e16eac7c27e9e6` |

La differenza worktree/release è dovuta anche alla diversa proiezione dei file
di repository; l'inventario dei chiamanti sostanziali del §4 coincide.

### 10.2 FS-A/legacy

Comando, eseguito due volte senza modifiche tra i passaggi:

```bash
for root in \
  /opt/metnos \
  /opt/metnos/.claude/worktrees/rm0008-reboot \
  /var/lib/metnos/executor-birth/releases-v1/00000000000000000051
do
  (cd "$root" && {
    rg -n --no-heading --glob '*.py' --glob '!**/tests/**' \
      --glob '!**/.venv/**' \
      '(^|[^A-Za-z0-9_])test_runner([^A-Za-z0-9_]|$)|run_test\(' \
      runtime install scripts 2>/dev/null
    rg -n --no-heading --glob 'manifest.toml' \
      '^(setup|teardown|env)\s*=' executors install 2>/dev/null
  } | LC_ALL=C sort | sha256sum)
done
```

Digest pass 1 = pass 2:

| root | digest |
|---|---|
| main | `7bb075c156b4be32e5a7590460f1bff9cf36f7f830feadf1d51a3dbf2a9787dc` |
| worktree RM-0008 | `629a87b2a263e9e843ca0982598b3b08632c2bf4f3602370b43e1057dbf555a2` |
| release 51 | `629a87b2a263e9e843ca0982598b3b08632c2bf4f3602370b43e1057dbf555a2` |

## 11. Protocollo di acquisizione della futura baseline finale

Da eseguire soltanto dopo il messaggio esplicito del maintainer RM-0008 che il
lavoro è finito e committato:

1. registrare commit completo, branch, `git status --short`, commit padre e
   firma/attestazione richiesta dalla politica; il worktree deve essere pulito
   per tutti i file tracciati rilevanti;
2. verificare che il commit finale discenda dalla linea RM-0008 attesa e
   registrare `merge-base` e `rev-list --left-right --count` contro il main;
3. ottenere dal maintainer l'identità della release definitivamente
   selezionata e verificare con i reader della catena/distribuzione RM-0008,
   non dal solo nome directory o dal rapporto umano;
4. confrontare digest completi di roadmap, porta, producer façade, bootstrap,
   receipt, operational, functional/property runner, lifecycle, preexercise,
   test runner, manifest grammar e inventario legacy fra commit e release;
5. ripetere entrambe le scansioni del §10 due volte sul commit congelato e
   sulla release selezionata; ogni differenza inattesa mantiene
   `BLOCKED_BASELINE`;
6. verificare che non siano comparsi nuovi produttori marker, chiamanti diretti
   di `handle_synth_request`, chiamanti diretti di `birth_executor/_execute` o
   writer di contract fuori dalle capability chiuse;
7. creare solo allora un nuovo worktree RM-0009 dal commit finale pulito;
8. applicare la rev8 RM-0009 e `internal/tools/rm0009_plan_check.py` come patch
   esplicite, una per volta, dopo aver verificato ownership e digest dei file
   sorgente nel main; non importare modifiche non attribuite;
9. applicare l'emendamento D-G0.3 approvato da entrambi i maintainer e rieseguire
   i controlli documentali/aciclicità prima di D-G0.6;
10. non interpretare il semplice passaggio dei test di modulo come FS-A, F5 o
    autorizzazione all'attivazione.

Fino al completamento dei punti 1-6, la baseline resta bloccata. Il contratto
resta bloccato soltanto fino alla decisione del §7.3 e all'approvazione di
formato, reader e regole di verifica dei proof del §8; G0.3/G0.6 e il codice
preliminare I1.1 non richiedono che le attestazioni di esercizio siano già
state emesse. L'esistenza e validità corrente di FS-A è invece prerequisito
dell'esercizio D1; `EXT-RM0008-F5` e le altre prove applicabili sono
prerequisiti dell'esercizio D2. Finché tali attestazioni mancano, D1 può essere
provata soltanto con adattatore inerte e D2 resta
`blocked/dependency_unready`, senza trasformare questa indisponibilità in un
blocco transitivo del lavoro preliminare.
