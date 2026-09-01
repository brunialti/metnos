# RM-0008 F4-EPOCA-01 — registro delle 24 prove di transizione

Data: 1 settembre 2026  
Specifica: `internal/design/rm0008_transizione_epoca_31_8_2026.md`, §11  
Stato: candidato per la revisione incrociata R3

## 1. Regola di lettura

Questo registro non trasforma una prova di modulo in una prova operativa. Gli
stati usati sono:

- `PROVATA`: percorso reale del prodotto o persistenza reale su una copia,
  con prova mirata verde;
- `PROVATA SU COPIA; LIVE A B4`: il comportamento distruttivo e la ripresa sono
  provati in una copia, ma la postcondizione sul negozio in esercizio resta un
  atto operativo separato;
- `APERTA A B3`: manca ancora una prova integrata richiesta prima della suite
  totale;
- `RIPROVARE A B3`: la prova mirata esiste ed e' verde, ma viene inclusa anche
  nella verifica finale sull'albero congelato.

La suite completa non viene eseguita per costruire questo registro. Resta una
sola esecuzione a B3, dopo l'accettazione R3 del candidato.

## 2. Corrispondenza esatta

| §11 | Requisito | Evidenza primaria | Stato |
|---:|---|---|---|
| 1 | prima transizione dall'ancora V1 | `test_prepared_v2_record_binds_first_transition_before_publication`; `test_initial_transaction_recovers_a_partial_unpublished_record` | `PROVATA` |
| 2 | seconda transizione dalla testa V1 | `test_prepared_v2_record_binds_a_completed_predecessor_selection`; `test_later_certificate_v2_is_appended_to_the_chain_not_the_anchor`; `test_cold_two_release_chain_reopens_from_disk` | `PROVATA` |
| 3 | zero, una e molte generazioni correnti | `test_zero_one_many_inventory_and_transition_are_independently_reproducible`; `test_zero_one_many_round_trip_bind_exact_current_proof`; `test_zero_current_generations_closes_with_empty_complete_proof`; `test_many_current_generations_include_already_receipted_exactly_once` | `PROVATA` |
| 4 | i 12 legami storici restano immutati | misura reale in sola lettura registrata in `adeguamento_legami_epoca_31_8_2026.md`; `prova_classifica_legami_epoca.py`; `prova_accettazione_v2.py`, caso 4 | `RIPROVARE A B3` |
| 5 | nessuna ricevuta corrente produce una riattestazione per ogni corrente | `test_current_inventory_freezes_before_any_receipt_exists`; `test_staged_selection_builds_only_a_context_bound_reattestation`; `prova_classifica_legami_epoca.py`, caso molte correnti | `PROVATA` |
| 6 | interruzione dopo ogni frontiera durevole | `test_head_crossing_converges_across_every_durable_boundary`; `test_preflight_crossing_converges_across_durable_boundaries`; `test_transaction_writer_recovers_staged_record_and_rejects_conflict`; `sonda_convergenza_confine.py` | `PROVATA` |
| 7 | ripetizione identica dopo ogni interruzione | `test_transaction_writer_persists_rereads_and_resolves_all_states`; `test_context_transition_post_publication_retry_is_idempotent`; `prova_accettazione_v2.py`, caso 3 | `PROVATA` |
| 8 | richieste concorrenti uguali e diverse | `test_deployment_lock_serializes_same_request_and_rejects_a_different_one`; `test_required_head_cas_allows_only_one_competing_successor`; `test_concurrent_exact_retries_converge_on_one_committed_binding` | `PROVATA` |
| 9 | collisione di nome con byte uguali e diversi | `test_context_transition_store_is_initialized_and_exact_retries_converge`; `test_context_transition_collision_inventory_and_proof_mismatch_stop`; `test_v2_publication_refuses_a_collision_without_moving_staging` | `PROVATA` |
| 10 | generazione rimossa, aggiunta o cambiata dopo il censimento | `test_transition_maintenance_rejects_inventory_drift_on_exit`; `test_staged_runtime_reverifies_inventory_and_cannot_become_required`; `test_changed_final_census_never_closes_legacy_owners` | `PROVATA` |
| 11 | distribuzione diversa da quella legata alla testa | `test_prepared_v2_record_rejects_crossed_target_and_release_facts`; `test_build_verified_record_rejects_distribution_drift`; `test_transition_set_and_distribution_must_agree` | `PROVATA` |
| 12 | vecchia epoca e vecchie ricevute restano leggibili e immutate | `prova_ricevute_v2.py`, casi 6, 7 e 7-bis; `prova_accettazione_v2.py`, caso 4; `test_v2_publication_moves_the_exact_set_and_preserves_the_v1_anchor` | `PROVATA` |
| 13 | un solo selettore, senza combinazioni testa/contesto | `prova_ricevute_v2.py`, caso 10; `test_dominant_identity_rejects_a_different_context_transition`; `test_required_head_missing_never_falls_back_to_highest` | `PROVATA` |
| 14 | diniego della build precedente dopo il punto di non ritorno | `test_the_product_gate_is_closed_and_bound_to_its_current_bytes`; `test_closed_build_denies_productive_store_before_touching_inputs`; `test_closed_build_denies_signing_before_path_or_key_access`; `test_fixed_ownership_authentication_rejects_required_head_rollback` | `PROVATA` |
| 15 | ritorno solo con nuova release a sequenza superiore | `test_later_certificate_v2_is_appended_to_the_chain_not_the_anchor`; `test_cold_two_release_chain_reopens_from_disk`; `test_head_crossing_publishes_one_verified_required_prefix` | `PROVATA` |
| 16 | certificato emendato completo; forma vecchia o campo extra rifiutati | `test_v2_codec_state_threshold_table`; `test_v2_codec_rejects_schema_binding_and_threshold_mutants`; `test_v2_codec_rejects_noncanonical_base64_and_extra_fields` | `PROVATA` |
| 17 | interruzione del provisioning prima/dopo PREPARED e ripresa esatta | `test_v2_material_plan_expansion_resumes_without_replacing_exact_bytes`; `test_v2_fixed_entry_returns_the_same_sealed_prepared_set_on_resume`; `test_transaction_writer_recovers_staged_record_and_rejects_conflict` | `PROVATA` |
| 18 | richiesta Producer V1 o di altra epoca non riutilizzabile in V2 | `test_legacy_publisher_without_context_cannot_use_the_v2_port`; `test_v2_port_rejects_a_request_outside_its_prepared_context`; `prova_ricevute_v2.py`, casi 8-11 | `PROVATA` |
| 19 | ricevuta dominante assente, diversa o priva del legame di contesto | `test_certificate_ready_v2_requires_a_sealed_crossing_and_exact_bytes`; `test_certificate_ready_v2_rejects_unsealed_or_drifting_evidence`; `test_the_crossing_refuses_without_a_group7_receipt` | `PROVATA` |
| 20 | digest dominante completo, non il solo digest dei legami | `test_the_complete_receipt_uses_the_required_domain_and_order`; `test_the_framing_separates_neighbouring_fields`; `test_any_field_that_moves_between_the_two_readings_stops_it` | `PROVATA` |
| 21 | V1 e V2 coesistono; la stessa tripla discordante e' rifiutata | `prova_ricevute_v2.py`, casi 4-7-bis; `prova_classifica_legami_epoca.py`, caso V1/V2 distinti; `prova_accettazione_v2.py`, casi 1-4 | `PROVATA` |
| 22 | problema d'inventario, proprieta', alias o file non regolare bloccano | `test_read_only_resolver_rejects_invalid_inventory_and_cardinality`; `test_fixed_ownership_capture_rejects_unsafe_metadata_and_inventory`; casi negativi di `prova_classifica_legami_epoca.py` e `prova_ricevute_v2.py` | `PROVATA` |
| 23 | recupero del contenitore incompleto solo nella forma esatta; secondo uso innocuo | `prova_recupero_pubblicazione.py` (28 casi); `prova_robustezza_recupero_pubblicazione_a.py` (5 controlli) | `PROVATA SU COPIA; LIVE A B4` |
| 24 | server completo e turni reali in copia dopo la transizione | `prova_b3_server_post_transizione.py`: transizione completa da predecessore immutabile, release firmata, server HTTP completo e turno C1 reale su un contratto incorporato rappresentativo; `steps_summary` contiene `get_preferences` con `ok=true`, chiusura senza processi superstiti | `PROVATA` |

## 3. Verifiche mirate rieseguite sul candidato

Le seguenti prove indipendenti sono state rieseguite dopo l'integrazione dei
perimetri e hanno concluso con esito positivo:

- `internal/tools/prova_ricevute_v2.py`: 17 casi;
- `internal/tools/prova_classifica_legami_epoca.py`: 21 casi;
- `internal/tools/prova_accettazione_v2.py`: 6 casi;
- `internal/tools/prova_recupero_pubblicazione.py`: 28 casi;
- `internal/tools/prova_robustezza_recupero_pubblicazione_a.py`: 5 controlli;
- `internal/tools/sonda_convergenza_confine.py`: 19 frontiere in cinque scene;
- `tests/portable/test_executor_birth_ownership_coordinator_v2.py`: 125 prove.
- `tests/portable/test_executor_birth_enforcement_evidence.py` e
  `tests/runtime/contracts/test_executor_birth_legacy_gate.py`: 32 prove.
- `internal/tools/prova_b3_server_post_transizione.py`: release costruita dai
  sorgenti ricevuti, transizione dalla build precedente `641c5095`, insieme e
  testa V2 riletti, server pronto, risposta HTTP 200 e passo reale
  `get_preferences` concluso con `ok=true`; arresto ordinato e zero processi
  superstiti. La sonda usa un contratto incorporato firmato rappresentativo;
  non sostituisce le prove mirate sull'invariante dell'intero catalogo.

Questi risultati non sostituiscono il confronto della suite totale a B3.

## 4. Prove con requisito di ambiente

Le celle POSIX che cambiano proprietario non possono essere trasformate in
`skip`: la certificazione RM-0008 vieta `skip`, `skipif`, `xfail` e `xpass`, e
la modalita' finale accetta soltanto `passed`. Le due celle che richiedono un
cambio UID vengono quindi eseguite nell'ambiente Linux certificato che possiede
quel requisito. Un ambiente locale privo dell'autorizzazione necessaria puo'
registrare il limite, ma non puo' produrre la prova finale e non deve mascherare
il risultato.

## 5. Condizione di avanzamento

R3 puo' accettare il codice e il piano con il solo punto 23-live dichiarato
aperto. La condizione di ingresso di B3, cioe' la prova 24 in copia, e'
soddisfatta. B4 applica il
recupero mirato al negozio in esercizio soltanto dopo le riletture e le
autorizzazioni gia' registrate; nessuna pulizia generica o selezione manuale del
percorso e' ammessa.

Il riscontro indipendente dell'agente B al commit `52307e5e` e' stato
incrociato riga per riga. In particolare, la sua osservazione sulla voce 14 e'
chiusa dalla prova che legge il file reale della build e diventa rossa se il
letterale torna aperto; la voce 23-live resta esplicitamente aperta, mentre la
24 e' stata chiusa dalla successiva prova integrata in copia.
