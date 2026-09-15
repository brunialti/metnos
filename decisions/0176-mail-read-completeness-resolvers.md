---
id: 0176
title: Mail-read completeness — resolver di completamento-args deterministici + robustezza backend
date: 2026-06-22
status: accepted
area: runtime
related:
  - 0165  # backend-resolver-uniforme (capostipite della famiglia resolver)
  - 0146  # consolidamento routing / DEFAULT_TIERS
  - 0175  # engine-v3 compound redesign (clausola «estrai» recuperata in posizione)
  - 0062  # truncation visibility vs notify
complements:
  - 0174  # disciplina cache compound (i resolver puri si riapplicano sui piani serviti)
---

<!-- ESTENDE la famiglia "ri-risoluzione slot query-specific" (ADR 0165, entry
indice 12/6) con un terzo resolver (from_contains) e due estensioni di copertura,
più la robustezza del backend mail su account="all". Tutto §7.9, additivo. -->

## Context

Banco di prova: «Cerca nelle mie email i pagamenti Anthropic dell'ultimo anno,
estrai data e importo, e crea un foglio di calcolo» (test live 21-22/6). La
STRUTTURA compound (read → extract → create) è garantita da ADR 0175; restava
però una catena di fallimenti che impediva il risultato, tutti riconducibili a
**args che l'LLM omette o sbaglia** + **robustezza del backend su molte mailbox**.

Modello causale verificato (turni reali, dry-run + e2e):

1. **L'LLM non setta gli args di filtro.** Il proposer copia la FORMA dal PATTERN
   del manifest (§2.5), non l'entità/finestra della query. Risultati osservati:
   `account=None` (1 sola mailbox letta), `time_window=None` su «ultimo anno»,
   `from_contains=None` su «pagamenti Anthropic». Conseguenza: poche mail lette,
   ago nel pagliaio non trovato, foglio non creato (poi nota onesta §2.8).

2. **Copertura dei resolver esistenti incompleta.** `time_window_resolver`
   gestiva «ultimi N mesi/giorni» (N+unità) ma NON il singolare nudo «(dell')ultimo
   mese/anno». `mail_account_resolver` gestiva «tutta/all la posta» ma NON il
   possessivo plurale «(le) mie email» = tutte le mie caselle.

3. **Nessun resolver per il mittente.** «da Anthropic»/«fatture Anthropic» nomina
   il mittente, ma non c'era un meccanismo deterministico per portarlo a
   `from_contains`; senza, il read è broad e `extract_entries` (cap `_MAX_INPUTS`
   = 50 sorgenti) processa solo le prime 50 mail → trova 2 fatture su 16.

4. **Robustezza backend su account="all".** (a) L'aggregazione multi-account
   appendeva PER-ACCOUNT: «le più recenti prima» valeva solo dentro un account →
   le mail recenti di un account iterato per ultimo finivano oltre i cap a valle.
   (b) Una mailbox (tiscali) aveva una mail con un campo bytes non-UTF8 →
   `json.dumps(invoke(args))` sollevava → executor «non-JSON output» → turno
   morto.

5. **Visibilità cap fuorviante.** Il notice di truncation per
   `truncated_what="input_sources"` (cap sorgenti di extract) leggeva i campi
   OUTPUT (record estratti, es. 2) invece di INPUT (sorgenti, 426) → «Hai 2
   input_sources, ne considero 2» (nonsenso + gergo).

## Decision

Tesi (coerente con ADR 0165/0174): gli args che l'LLM omette si **completano
deterministicamente dalla query** (§7.9), non si insegue il determinismo LLM. Il
backend mail deve essere **completo e mai-crashante** su molte mailbox.

- **D1 — terzo resolver: `from_contains`** (`runtime/from_contains_resolver.py`,
  gemello di `mail_account_resolver`/`time_window_resolver`). Segnale robusto: la
  preposizione di provenienza «da/from <NomeProprio>» oppure un nome-commerciale
  «(fattur*/pagament*/ordin*/ricevut*/bollett*/invoice/receipt/…) <NomeProprio>»
  NOMINA il mittente. **CONSERVATIVO**: solo `read_messages` canale email; solo se
  `from_contains` E `subject_contains` vuoti (l'LLM/utente vince); entità =
  NomeProprio CAPITALIZZATO non-stopword/giorno/mese/parola-mail/account; candidato
  UNICO (≥2 distinti → noop, ambiguo). Query tutta-minuscola → noop (la maiuscola
  È il segnale sicuro). Wrong-guess → read 0 → nota onesta §2.11, recuperabile, MAI
  un'azione mutating. Agganciato in `engine/executor._apply_pure_resolvers`.

- **D2 — estensione copertura resolver esistenti.**
  - `time_window_resolver`: «(dell')ultimo mese/anno» singolare → rolling
    `last-1m`/`last-1y` (IT+EN). Settimana NON aggiunta (resta calendario, «settimana
    scorsa»→None).
  - `mail_account_resolver`: possessivo plurale «(le) mie/miei + parola-mail» →
    `account=all` (IT `mie/miei` è plurale → «la mia mail» singolare resta escluso;
    `my + mail` EN). Account nominato vince comunque.

- **D3 — robustezza backend `account="all"`** (`backends/messages/email_metnos.py`).
  - **Sort globale per data**: la lista aggregata multi-account è riordinata per
    data desc (mail non parsabili in coda) → «le più recenti PRIMA» è globale, le
    mail recenti non finiscono oltre i cap a valle. §2.1.
  - **Mai crashare** (§2.8/§7.3): `read_messages.py` serializza con
    `default=_json_safe` (bytes→decode utf-8 replace, altro→str) → una mail
    malformata diventa stringa, non uccide la lettura. Re-sign §7.10.

- **D4 — notice cap corretto** (`agent_runtime`, §2.7): ramo dedicato per
  `truncated_what="input_sources"` → usa `available_input_total` (sorgenti) +
  `cap_value` (50), parola generica, niente gergo «input_sources».

## Consequences

- e2e: la query passa da «0/2 fatture, crash, foglio assente» a **16 fatture
  complete** in un foglio, in modo veloce (from_contains filtra server-side → 17
  mail, niente deadline) e deterministico (i resolver garantiscono gli args a
  parità di query).
- Additivo: i tre resolver sono no-op sui casi non-pertinenti (tool ≠
  read_messages, arg già settato, nessun segnale, ambiguità). Zero regressione.
- Onestà preservata: se un resolver sbaglia entità, il read torna 0 e il notice è
  onesto; nessun risultato parziale spacciato per completo.
- Limite VOLUTO: query tutta-minuscola → `from_contains` noop (l'LLM resta
  responsabile). La maiuscola è la barriera anti-falsi-positivi.

## Alternatives considered

- **from_contains via prompt rafforzato**: già insufficiente (l'LLM ignora «da
  Anthropic»); resta LLM, non deterministico. Scelto resolver §7.9.
- **NER/entity-extraction generale (ADR 0171)**: più potente ma molto più lavoro e
  rischio falsi-positivi; il pattern «da/(nome-commerciale) <NomeProprio>
  capitalizzato» copre il caso reale con rischio bounded.
- **Alzare `_MAX_INPUTS` di extract**: il budget-token non regge centinaia di mail
  (19K→110K token); il filtro a monte (from_contains) è la leva giusta.
- **Suppressione del notice extract** (aggiungere `extract` a PROCESSOR_VERBS):
  scartata — il cap input È user-relevant (spiega l'incompletezza); meglio
  riportarlo corretto.

## Implementation

- `runtime/from_contains_resolver.py` (nuovo) + wire in
  `engine/executor._apply_pure_resolvers`; `test_from_contains_resolver.py` (14).
- `runtime/time_window_resolver.py`, `runtime/mail_account_resolver.py`:
  estensioni pattern.
- `runtime/backends/messages/email_metnos.py`: sort globale per data
  (multi-account); cap a fonte unica + deadline/parziale §2.7 (21/6).
- `executors/read_messages/read_messages.py`: `default=_json_safe` + re-sign.
- `runtime/agent_runtime.py`: notice `input_sources` sui campi INPUT.
- Moduli RUNTIME → nessun re-sign tranne `read_messages` (executor).

## Addendum — shared temporal semantics (2026-09-15)

The owner requested one general, fast and localized treatment of dates and
intervals, including unfamiliar expressions and explicit clarification when
meaning is ambiguous. Domain-specific date guesses duplicated arithmetic and
could confuse a complete window with its lower endpoint. The common boundary
now recognizes schema formats `date`, `date-time` and `time-window` on scalar
and array properties. This does not claim recursive support for arbitrary
nested schemas. Existing window consumers share the same arithmetic.

`time_window_parser` distinguishes calendar periods, elapsed durations and
instants using one timezone-aware reference clock. Versioned language resources
and `time_window_resolver` handle common wording without a model. A bounded
local `temporal.interpret` workload may map unfamiliar wording to the same
canonical grammar; it does not perform arithmetic, invent absolute dates or
erase constraints by returning the unbounded `all` window. Ambiguous weekdays
and invalid or ambiguous daylight-saving times are not silently guessed.

`temporal_resolution` uses the existing input-selection form when meaning is
unresolved. Choices contain frozen absolute bounds and preserve valid array
siblings on resume. This is clarification, not a permission request. One whole
period is not copied into `since`; explicit endpoints retain their own meaning.
An explicitly supplied `all` is valid only as an unbounded window, never as a
date/time instant.

For mail, IMAP date search is only an outward-rounded coarse filter. Exact
receipt timestamps (`INTERNALDATE`, with the message Date header as fallback)
are filtered before the result limit; `before` is exclusive. Read-only selection
and `BODY.PEEK` avoid changing message flags. Scanning remains bounded and
partial coverage is visible. The implementation and real read-only HTTP/dialog
evidence are recorded in `internal/reports/rm0008-temporal-20260915.md`.
