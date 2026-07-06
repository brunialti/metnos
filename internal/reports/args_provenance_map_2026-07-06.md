# Report provenienza args — mappa reale del catalogo (6/7/2026)

> Generato da `runtime/arg_provenance.py::provenance_report`. FASE 0 dell'architettura di proprietà args.

**537 argomenti** su 103 tool con args dichiarati.

## Distribuzione per proprietario
- **runtime** (config, il runtime inietta): 38 (7%)
- **clause** (derivabile dal testo): 114 (21%)
- **semantic** (resta all'LLM): 385 (71%)

**2 tool** hanno args 100% deterministici (nessun semantic).
**101 tool** hanno almeno un arg semantic.

## Cleanup manifest scovato → ✅ LAVORATO (6/7 sera)
I 30 args config (client/account/provider) senza marker sono stati decisi **per-tool alla prova dell'executor** (non per convenzione di nome): **20 marcati** `runtime_resolved` (mono-provider o plumbing; move_files e trio `*_files_doc` riscoperti MONO leggendo i `_HANDLERS`) e **10 esenti intent-bearing** (client files multi-provider = clause-derived; move_messages.client metnos|gmail senza owner runtime; `account` mail — i casi 2+ account sono delegati al planner dal resolver stesso).

Politica = fonte unica in `runtime/tests/test_config_args_marking_policy.py` (+ regole in `arg_provenance.is_intent_bearing_config`); da qui in poi `n_unmarked_config > 0` = drift reale, non backlog. La marcatura ha scovato e chiuso anche il bug d'injection fuori-enum di `resolve_backend_arg` (share_files rotto su ogni share senza marker drive) e 2 enum stantii (write_files lazy-gw, find_events_empty gw handler reale).

## Lettura per il refactor
- Il **28%** degli args (runtime+clause) è ciò che i guard-args oggi rincorrono. Rendendo runtime-inject + clause-derive AUTORITATIVI, questi guard diventano no-op.
- Il **72% semantic** resta legittimamente all'LLM (grammar-args vincola solo gli enum, che sono già in `clause`).
- **Nota**: parte del 72% semantic è in realtà `from_step`-piped (entries/content da step precedenti), NON valori generati liberamente dall'LLM — un raffinamento futuro del classificatore (distinguere `piped` da `semantic`) ridurrebbe ancora la fetta veramente LLM-owned.