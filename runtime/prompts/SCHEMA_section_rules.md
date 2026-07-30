# Schema YAML — Planner Sections (Asse B PoC, 12/5/2026)

Schema YAML strutturato per le sezioni `style: prescriptive` del planner,
in alternativa al formato Jinja2 prosa. Compositor deterministico (§7.9)
rende il YAML in prosa rispettando il pattern §6 (DEVI/NON DEVI/OK/ERRORE).

Target: -25-35% byte size, semantica identica. PoC su `calendar` (IT+EN).

## Struttura canonica

```yaml
# Frontmatter equivalente a `{# --- ... --- #}` Jinja (the design guide §6.1)
role: planner
tier: middle
lang: it
style: prescriptive
version: 1
owner: roberto
updated: 2026-05-12
sha_prev: ""

section:
  name: calendar
  header: |
    CALENDAR / EVENTS / AGENDA / GOOGLE WORKSPACE (Calendar)
  preamble: |
    APPUNTAMENTI/AGENDA/EVENTS/CALENDAR (Google Calendar)
    -> read_events (read), create_events (create), delete_events (delete).

rules:
  - name: events_core
    when: "richieste su appuntamenti / calendar events"
    must: "..."
    must_not: "..."
    ok: "..."
    error: "..."
    disambiguation: ""   # opzionale
```

## Vincoli schema

- Frontmatter: 8 campi obbligatori (`role`, `tier`, `lang`, `style`,
  `version`, `owner`, `updated`, `sha_prev`). `style` DEVE essere
  `prescriptive` (gli altri stili usano `.j2`).
- `section`: dict con `name` (str) + `header` (str multi-line ok) +
  `preamble` (str multi-line ok).
- `rules`: lista ORDINATA di dict. Ordine importante (regole prima
  pesano di piu' nel routing Gemma).
- Per ogni rule:
  - `name`: str univoco fra le rules della stessa section.
  - `when`: str descrizione condizione (1 riga preferibile).
  - `must`: str §6 DEVI (non vuoto).
  - `must_not`: str §6 NON DEVI (non vuoto).
  - `ok`: str §6 OK esempio positivo (non vuoto).
  - `error`: str §6 ERRORE esempio negativo (non vuoto).
  - `disambiguation`: str opzionale (default `""`).
- Tutti i valori string ammettono multi-line via `|` (literal block scalar
  YAML, preserva newlines).

## Output renderizzato (deterministico)

Il compositor `_render_yaml_section` produce in ordine:

```
======================================================================
{section.header}
======================================================================

{section.preamble}

({rule.name}) WHEN: {rule.when}
DEVI: {rule.must}.
NON DEVI: {rule.must_not}.
OK: {rule.ok}.
ERRORE: {rule.error}.
NB DISAMBIGUATION: {rule.disambiguation}.   # solo se non vuoto

({rule_next.name}) WHEN: ...
...
```

Niente blank lines extra fra rule e rule (1 separatore singolo `\n\n`),
niente prosa decorativa, niente fold di Jinja loop. Periodi finali
collassati: se `must` finisce gia' con `.`, il rendering non duplica.

## Compositor & fallback

`prompt_loader.compose()` in `runtime/prompt_loader.py`:
- Per ogni `<section>.j2` richiesto, cerca prima `<section>.yaml` nello
  stesso path. Se esiste → render via `_render_yaml_section`. Altrimenti
  fallback su `.j2` esistente (Jinja).
- Niente backward compat shim (§7.1): YAML e .j2 coesistono SOLO durante
  PoC A/B. Dopo verdetto GO, .j2 viene eliminato.

## Linter

`prompts_lint.py` estende `scan(root)` per coprire i `.yaml`:
- Verifica frontmatter 8 campi (idem .j2).
- Verifica `section` e `rules` presenti.
- Per ogni rule: `must`/`must_not`/`ok`/`error` stringhe non vuote.
- `name` univoco fra rules.
- Simmetria cross-lang: ogni `it/.../calendar.yaml` ha sibling
  `en/.../calendar.yaml` (e viceversa).

## Stile compatto vs Jinja prosa

Jinja prosa (calendar.j2 attuale):
- Spiegazione discorsiva fra DEVI/NON DEVI.
- Indentazione multi-line con prefix "  " ricorrente.
- Decorazione `══════` su 70 char.

YAML rendered:
- Frasi imperative concise.
- Niente indentazione del corpo (rule per riga + 1 newline).
- Header ridotto ma riconoscibile.

Risparmio atteso: -25 ÷ -35% byte per sezione "long" (calendar/mail/web).
