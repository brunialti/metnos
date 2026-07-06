# Report provenienza args — mappa reale del catalogo (6/7/2026)

> Generato da `runtime/arg_provenance.py::provenance_report`. FASE 0 dell'architettura di proprietà args.

**537 argomenti** su 103 tool con args dichiarati.

## Distribuzione per proprietario
- **runtime** (config, il runtime inietta): 38 (7%)
- **clause** (derivabile dal testo): 114 (21%)
- **semantic** (resta all'LLM): 385 (71%)

**2 tool** hanno args 100% deterministici (nessun semantic).
**101 tool** hanno almeno un arg semantic.

## Cleanup manifest scovato
**30 args config** (client/account/provider) SENZA marker `runtime_resolved` — classificati runtime per convenzione §2.2/0136, ma il marker andrebbe applicato per coerenza (chiude i guard `_scope_sink_provider_to_clause`/`_align_provider_client`):
```
create_dirs.client
create_files_doc.client
create_files_spreadsheet.client
delete_dirs.client
delete_files.client
find_contacts.client
find_dirs.client
find_events_empty.client
find_files.client
find_urls.client
login_session.client
move_files.client
move_messages.account
move_messages.client
read_contacts.client
read_files.client
read_files_doc.client
read_files_spreadsheet.client
read_messages.account
read_messages.client
read_urls_html.client
read_urls_pdf.client
reply_messages.client
send_messages.account
send_messages.client
set_messages.client
share_files.client
write_files.client
write_files_doc.client
write_files_spreadsheet.client
```

## Lettura per il refactor
- Il **28%** degli args (runtime+clause) è ciò che i guard-args oggi rincorrono. Rendendo runtime-inject + clause-derive AUTORITATIVI, questi guard diventano no-op.
- Il **72% semantic** resta legittimamente all'LLM (grammar-args vincola solo gli enum, che sono già in `clause`).
- **Nota**: parte del 72% semantic è in realtà `from_step`-piped (entries/content da step precedenti), NON valori generati liberamente dall'LLM — un raffinamento futuro del classificatore (distinguere `piped` da `semantic`) ridurrebbe ancora la fetta veramente LLM-owned.