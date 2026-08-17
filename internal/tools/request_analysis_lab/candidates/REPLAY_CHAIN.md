# Catena durevole di ricostruzione V26.4.1

Scopo: permettere a una nuova sessione Codex/Claude di ricostruire i path
storici `/tmp` senza cambiare i freeze. Questa è una mappa di replay, non
un'autorizzazione a chiamare il modello.

Catena di import effettiva:

```text
V26.4.1 → V26.4 semantic freeze + runner-only transport delta
V26.4 → typed registry + typed oracle phase1_v1 + controls34
      ↘ prompt-contamination auditor
V26.3 → V26.2 → V25.3 → V25 → V24.1 → V24
                              ↘ bench V22
V26.3 freeze/audit → prompt-contamination auditor
```

Tutti gli artifact vanno copiati con lo stesso basename, salvo le due
eccezioni nominate esplicitamente. Dopo la materializzazione verificare gli
SHA dei README di ogni directory. Non modificare gli originali archiviati.

## 1. Base V24/V24.1

Da `v24_v241/` a `/tmp` con lo stesso basename, almeno:

- `metnos_v24_offline.py`;
- `metnos_v24_rules_frozen.json`;
- `metnos_v24_contract.schema.json`;
- `metnos_v24_freeze.lock.json`;
- `metnos_v241_offline.py`;
- `metnos_v241_rules_frozen.json`;
- `metnos_v241_contract.schema.json`;
- `metnos_v241_freeze.lock.json`.

Per una verifica completa V24.1 servono anche results/mutation con gli stessi
nomi. C'è una sola eccezione byte-level già documentata nel README padre:
`v24_v241/metnos_v24_on_v23lite_results.json` ha un LF finale aggiunto
dall'archivio. L'originale `/tmp` aveva SHA-256
`2757d52eb324f89e6946a95792df0604b8436c098d91bfd8d6f58a00ff07f669`;
il file archiviato ha SHA-256
`b634fa7c2999fdf2e9c623eeaeb59c496e48e22af77e3cc393bcf906e5954dca`.
Rimuovere esclusivamente quell'ultimo LF nella copia di lavoro `/tmp` quando è
richiesta la ricostruzione byte-identica; non modificare l'archivio.

## 2. Bench e V25

- `replay_deps/metnos_unified_query_bench_v22.py` →
  `/tmp/metnos_unified_query_bench_v22.py`;
- tutti i file di `v25/` → `/tmp` con lo stesso basename;
- in particolare il runner deve chiamarsi `/tmp/metnos_v25_live_runner.py`.

Il bench V22 esatto ha SHA-256
`db2abaf6aaba2ad77205dda3fda96cdfab272a17079e57b0e472201395a4a329`.
Non sostituirlo con `unified_query_bench_v23_checkpoint.py`, che è un artifact
diverso.

## 3. V25.3

Da `v253/` a `/tmp` con lo stesso basename:

- runner, prompt, schema, freeze;
- controls frozen e mutations frozen;
- `metnos_v253_phase1_relation_controls_k1.json` soltanto per riprodurre il
  rescore diagnostico;
- rescore JSON/tool se si vuole verificarne l'output.

Eccezione di nome:

- `../question_focus_controls_v1.json` →
  `/tmp/metnos_question_focus_negative_controls_v1.json`.

Il file controllo deve avere SHA-256
`22dac65689f720e07022ac204ba0fd25feebdf5e8db98a9d89f5bf00c120dcbf`.

## 4. V26.2

Da `v262/` a `/tmp` con lo stesso basename, per la catena import/freeze:

- `metnos_v262_minimal_phase1_runner.py`;
- prompt, schema, freeze, controls frozen e mutations frozen;
- `metnos_prompt_contamination_audit.json` se si verifica il gate storico
  V26.2.

Il gate lock V26.2 ha già autorizzato ed esaurito un solo K1. Non costituisce
autorizzazione per V26.3 e non va riusato.

## 5. V26.3

Da `v263/` a `/tmp` con lo stesso basename:

- `metnos_v263_phase1_runner.py`;
- `metnos_v263_phase1.prompt.txt`;
- `metnos_v263_phase1.schema.json`;
- `metnos_v263_phase1_controls_frozen.json`;
- `metnos_v263_phase1_mutations_frozen.json`;
- `metnos_v263_phase1_contamination_audit.json`;
- `metnos_v263_phase1.freeze.json`.

E inoltre:

- `replay_deps/metnos_prompt_contamination_audit.py` →
  `/tmp/metnos_prompt_contamination_audit.py`.

### Gate V26.3: eccezione di sicurezza

`v263/metnos_v263_phase1_author_pre_gate.json` conserva byte per byte il
pre-gate statico dell'autore, ma **non va materializzato come**
`/tmp/metnos_v263_phase1_preinference_gate.json` per eseguire un run. Il suo
`inference_allowed=true` non include audit oracolo, review indipendente o
hash-chain esterna.

Prima del run, un revisore indipendente deve produrre un nuovo gate lock
separato che leghi freeze, audit oracolo, review e verifier. Il coordinatore
deve poi autorizzare esplicitamente un solo run. Fino ad allora V26.3 è
bloccato anche se tutti i file sono ricostruibili.

## 6. V26.4

Da `v264/` a `/tmp` con lo stesso basename, almeno:

- `metnos_v264_typed_phase1_runner.py`;
- `metnos_v264_typed_registry.json`;
- schema, prompt, controls frozen, mutations frozen, contamination audit,
  author pre-gate e freeze;
- invariant review, mutation proposal, independent probe e risultato probe
  se si ricostruisce l'intera verifica offline.

Materializzare inoltre:

- `../oracles/phase1_v1/metnos_phase1_oracle_audit_v1.md` → stesso basename in
  `/tmp`;
- gli altri quattro file `phase1_v1` → stesso basename in `/tmp`;
- `replay_deps/metnos_prompt_contamination_audit.py` →
  `/tmp/metnos_prompt_contamination_audit.py`.

V26.4 non importa alcun runner V26 precedente. I cinque hash dell'oracolo e
le versioni runtime del segmenter/validator sono legati nel freeze. La review
indipendente finale del graph è archiviata in `v264/`, ma non fa parte del
freeze autore: un eventuale gate esterno deve legarne esplicitamente path e
SHA insieme al freeze. Deve legare anche
`metnos_v264_independent_graph_final_review.addendum.md` SHA
`53254dca278c2d2da7f4ac2958502866b3758dd332f672643950c87ccffe1c4d`;
la review originaria resta immutabile.

Non materializzare o inventare
`/tmp/metnos_v264_typed_phase1_external_gate.lock.json`: il file deve essere
prodotto dal coordinatore soltanto dopo tutte le review richieste. In sua
assenza il runner deve rifiutare il run. L'author pre-gate resta
`inference_allowed=false`.

## 7. V26.4.1

Da `v2641/` a `/tmp` con lo stesso basename:

- runner, registry, schema, prompt e controls frozen;
- mutations, contamination audit, author pre-gate e freeze;
- infra mutation probe e relativo risultato.

Registry, schema, prompt e fixture devono mantenere gli SHA V26.4 elencati nel
README V26.4.1. Materializzare inoltre le dipendenze V26.4/oracolo/auditor della
sezione precedente. Il freeze V26.4.1 lega anche la diagnosi K1 archiviata in
`v264/`.

Non inventare né materializzare gate preflight o live. Il primo richiede review
indipendente e autorizza un solo GET `/v1/models`. Un eventuale live lock nuovo
deve legare freeze/review, l'hash di quel PASS recente e un output assente; il
runner impone poi un secondo GET inline nello stesso processo. Il vecchio gate
V26.4 è consumato.

## 8. Controllo finale prima di ogni replay

1. Verificare SHA-256 di ogni file contro i README.
2. Verificare che `git status` non mostri modifiche ai freeze archiviati.
3. Eseguire soltanto controlli offline finché il gate lock esterno non esiste.
4. Non usare il gold, l'oracolo o i risultati K1 nel request body del modello.
5. Non attribuire credito a rescore post-hoc.
6. Pubblicare separatamente validità, coverage, binding e tupla semantica.
