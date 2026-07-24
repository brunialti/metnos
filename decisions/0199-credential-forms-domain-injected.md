# 0199 - Form credenziali iniettati dal dominio consumatore

Data: 24/7/2026 · Stato: adottata · Autore: Roberto (design), agente (impl.)

## Contesto

`set_credentials` su un binding nuovo senza `fields` restituiva un errore
grezzo («Argomento obbligatorio mancante: fields or scopes», turni reali
`7f8fd73b`, `2eb981a2`). L'attesa: un form guidato che chieda i campi GIUSTI
per il tipo di credenziale (mail → password, server IMAP/SMTP; GitHub →
token, repository). Una prima iterazione con registro centrale
(`credential_forms.toml`) è stata scartata da Roberto: ancora una forma di
codifica centrale dei tipi. Principio ratificato: **ogni dominio conosce e
gestisce la propria semantica, quindi il form dev'essere iniettato dal
dominio richiedente**.

## Decisione

1. **Dichiarazione nel manifest firmato del dominio.** La serializzazione è
   il canale dichiarativo che ogni dominio già possiede: una sezione
   opzionale `[credential_form]` nel manifest di UN executor del dominio
   (kind, label_key, detect_prefixes, binding_prefix, fields[]). Ogni campo:
   `name` (chiave cifrata), `required`, `secret`, `input` (∈ {text,
   credentials, number}, i kind resi da `dialog_form.html`), `prompt_key`
   (i18n), `default`. Dichiarazioni correnti: mail → `read_messages`;
   site → `login_sites`; github → skill installata (`find_issues_github`);
   il tipo GENERICO `api` appartiene al dominio credenziali stesso
   (`set_credentials`).
2. **Collettore universale.** `credentials.credential_form_kinds()`
   colleziona le sezioni dal catalogo ammesso (`load_catalog`): un dominio
   assente sparisce dal form da solo; una skill nuova porta il proprio form
   con sé; i domini DORMIENTI restano offerti (è per attivarli che servono le
   credenziali). Fail-loud: spec invalida o stesso kind dichiarato da due
   executor = errore che nomina i responsabili. `set_credentials` non
   contiene alcun tipo: costruisce i dialoghi dai dati collezionati.
3. **Flusso a round sul motore dialoghi esistente** (ADR 0090): (R1) scelta
   tipo + nome account, saltata se `detect_prefixes` o l'arg intent-bearing
   `credential_kind` determinano il tipo; (R2) form campi (segreti con input
   `credentials` = password mascherata); (R3) percorso preesistente
   (sovrascrittura, mandato ADR 0190). Ritorno valori via
   `resume_executor_with_values` + `merge_into: "fields"`: re-invocazione
   diretta dell'executor, **i segreti non transitano mai dal planner**.
   Binding canonico da `binding_prefix` (es. mail + «test» → `smtp_test`);
   su resume i campi opzionali vuoti vengono scartati (`_fields_form`).

## Conseguenze

- Nuovo tipo di credenziale = sezione `[credential_form]` nel manifest del
  dominio + chiavi i18n nel DB (seed rigenerato; guard
  `test_seed_i18n_gate_keys.py`). Zero modifiche a executor o runtime.
- Il manifest di `set_credentials` dichiara `credential_kind` e
  `account_name` senza enum statico (match esatto sui kind collezionati:
  niente deriva manifest↔domini).
- `assert_no_secrets_in_return` ha UNA esenzione strutturale: un elemento di
  `schema.choices` con sole chiavi {value, label} è un'opzione UI (ADR 0127),
  non un segreto. Tutto il resto dell'invariante è intatto.
- I bundle skill dovrebbero SPEDIRE la propria sezione `[credential_form]`;
  per github è stata aggiunta alla copia installata e ri-firmata (da
  riportare nel bundle sorgente alla prossima revisione della skill).
- Gotcha operativo: chiavi i18n aggiunte a runtime restano nel WAL sqlite,
  invisibile alla sandbox bwrap che monta il solo file principale →
  `PRAGMA wal_checkpoint(TRUNCATE)` dopo l'inserimento.

## Addendum — collezione server-side e iniezione al choke-point, 24/7/2026 (notte)

Il collettore eseguito DENTRO l'executor non ha mai funzionato in esercizio:
la sandbox bwrap non monta `~/.config/metnos/keys/` (le chiavi private non
devono entrarci) né i manifest delle skill, quindi `load_catalog` rigettava
ogni firma e il fail-loud «nessun [credential_form] dichiarato» faceva
crashare `set_credentials` (turno reale `b7dc5e2d51484fd3`). Il turno di
validazione `3e1a4874645748ac` (21:44) aveva esercitato l'iterazione con
registro centrale, sostituita alle 21:55 e mai rivalidata dopo il restart
delle 21:57.

Revisione ratificata da Roberto: **la collezione gira nel processo server**,
che possiede già il catalogo verificato, e il risultato viene iniettato
nell'arg runtime-owned `credential_forms` di `set_credentials` al choke-point
`_invoke_executor_impl` — quindi vale per invocazione fresca E resume, e il
valore sovrascrive sempre (un leak coercito non sopravvive). Meccanismo
GENERALE: un arg `runtime_resolved` con `runtime_source = "<nome>"` nel
manifest firmato viene riempito dal registro `_RUNTIME_ARG_SOURCES`
(fail-soft: sorgente guasta = arg assente = errore onesto dell'executor a
valle). La dichiarazione `[credential_form]` nei manifest dei domini resta
IDENTICA; cambia solo dove si colleziona. Helper `kind_for_binding` /
`canonical_binding` accettano la mappa già collezionata; il collettore perde
la cache (il server è long-lived e il catalogo cambia a runtime).

Evidenza: test manifest 5/5 (incluso `missing_runtime_forms_is_honest_error`),
pytest iniezione+credenziali 22/22, turno reale post-restart
`67f1ebe30fc847c2` («inserisci credenziali email test2» → form guidato).

## Evidenza al momento dell'adozione

Probe: 4 kind collezionati da 4 manifest di domini diversi; binding
`github-nuova-org` → auto-rilevamento → form username/token/repo;
`smtp_lavoro` → mail. Live: turno `3e1a4874645748ac` («inserisci credenziali
email test» → form tipo+nome, poi 6 campi mail con password mascherata);
turno `497f8f43f1a54b25` («memorizza le credenziali github», binding
esistente → dialogo mandato, semantica preservata). Test: 94 credential,
327 tutor+i18n, tutti verdi. Manifest ri-firmati: `read_messages`,
`login_sites`, `set_credentials`, `find_issues_github` (skill).
