---
id: 0153
title: Content fetch on-demand — pattern install_on_demand applicato al contenuto
date: 2026-05-19
status: proposed
area: runtime | executor | describe_entries | find_urls | install_on_demand
related:
  - 0143  # install_on_demand: pattern errore strutturato → runtime remediation
  - 0094  # fast-path: short-circuit deterministico
  - 0078  # output formatter deterministico
---

## Contesto

Bias osservato live (19/5/2026 sera) su pi&ugrave; turni:

- Query utente: `"cerca informazioni delle ultime 24 ore sulle tendenze e
  previsioni riguardo ai bitcoin"`.
- Pipeline emessa dal PLANNER: `find_urls` &rarr; `describe_entries`.
- Output finale: enumerazione di 30 titoli di pagina con score, **niente
  sintesi del contenuto**. L'utente ha chiesto "riassumi" ma il sistema
  ha "elencato".

Causa: `describe_entries` riceve in input le entries di `find_urls`, che
hanno solo `{url, title, snippet, score}` &mdash; **nessun campo
`content`/`body`/`text`**. L'executor non pu&ograve; sintetizzare ci&ograve;
che non vede, e ricade nell'enumerazione dei metadata.

Soluzione naive: aggiungere una regola al prompt del PLANNER del tipo
"se l'utente dice 'riassumi' E la pipeline ha `find_urls` come primo
step, interponi `read_urls_html`". Statistico e fragile: il PLANNER LLM
pu&ograve; non applicare la regola; il bias resta per altri verbi
(condensa, descrivi i contenuti, sintesi).

## Decisione

Applicare il **pattern install_on_demand** (ADR 0143, sudoers
apt-get) al contenuto: un executor che rileva di non avere il
prerequisito necessario lo dichiara esplicitamente con un errore
strutturato; il runtime riconosce l'errore e sintetizza al volo lo step
di remediation.

### Schema generale

```
1. Executor X(args) viene chiamato con input qualitativamente insufficiente.
2. X ritorna {ok: false, error_class: "needs_<resource>",
              <resource>_hint: [<info per rimediarlo>],
              error: "<descrizione human-readable>"}.
3. Runtime agent_runtime detect error_class noto → sintetizza step
   prerequisito P(args derivati dall'hint), lo esegue, raccoglie observation.
4. Runtime ricalcola args di X usando il risultato di P (es. from_step
   sul nuovo step), chiama di nuovo X.
5. Pipeline prosegue come se l'utente avesse mai visto l'errore.
```

### Applicazione specifica: describe_entries su URL-only

Nuovo `error_class = "needs_content_fetch"` in
`executors/describe_entries/describe_entries.py`. Detection:

```python
def invoke(args):
    entries = ...
    if not entries:
        return {"ok": True, "summary": "Nessuna entry.", "entries": []}

    # Detection: nessuna entry ha campo testuale, MA almeno una ha url
    has_content = any(
        bool(e.get("content") or e.get("body") or e.get("text"))
        for e in entries if isinstance(e, dict)
    )
    if not has_content:
        urls = [
            e["url"] for e in entries
            if isinstance(e, dict) and e.get("url")
        ][:5]
        if urls:
            return {
                "ok": False,
                "error_class": "needs_content_fetch",
                "needs_urls_html": urls,
                "error": (
                    "describe_entries non puo' sintetizzare entries "
                    "URL-only (mancano content/body/text). Il runtime "
                    "dovrebbe interporre read_urls_html sui primi N URL "
                    "e rieseguire describe_entries con le entries arricchite."
                ),
            }
    # ... normal summarization su content-bearing entries
```

### Runtime remediation in agent_runtime

In `agent_runtime.run_turn`, dopo `invoke_executor` per ogni step:

```python
if obs.get("error_class") == "needs_content_fetch":
    urls = obs.get("needs_urls_html") or []
    if urls and not _already_tried_content_fetch_this_turn:
        # Sintetizza step read_urls_html, eseguilo
        rh_executor = next(e for e in catalog if e.name == "read_urls_html")
        rh_obs = invoke_executor(rh_executor, {"urls": urls}, ...)
        if rh_obs.get("ok"):
            # Riprova describe_entries con entries arricchite
            new_entries = rh_obs.get("entries") or []
            retry_args = dict(resolved_args)
            retry_args["entries"] = new_entries
            retry_args.pop("from_step", None)
            obs = invoke_executor(executor, retry_args, ...)
            # Log lo step intermedio come "auto-injected remediation"
            ...
    _already_tried_content_fetch_this_turn = True
```

L'idempotency flag `_already_tried_content_fetch_this_turn` evita
loop infiniti se anche il retry fallisce.

## Propriet&agrave;

- **Deterministico** (CLAUDE.md §7.9): la decisione di rimediare e' una
  regex sul `error_class`, niente LLM nel critical path.
- **Generale**: applicabile a qualsiasi executor che dichiari un
  prerequisito mancante. Esempi futuri:
  - `extract_text` su PDF senza OCR &rarr; `error_class: "needs_ocr"` &rarr;
    runtime interpone `change_files_ocr`.
  - `classify_entries` su entries senza embedding &rarr;
    `error_class: "needs_embedding"` &rarr; runtime crea l'embedding.
- **Onesto** (CLAUDE.md §2.8): l'executor non bara enumerando metadata,
  dice esplicitamente "mi manca X".
- **Riusa pattern noto**: identico a ADR 0143 install_on_demand per
  binari mancanti, sostituendo `binary_missing` con `needs_content_fetch`.
- **Migliora anche il PLANNER**: il prompt non deve enumerare ogni caso
  particolare; il PLANNER pu&ograve; emettere la pipeline "ottimistica"
  (find_urls &rarr; describe_entries) e il runtime sistema la
  mancanza al volo.

## Tabella mapping error_class &rarr; remediation

| error_class           | Hint field          | Auto-remediation step |
|-----------------------|---------------------|------------------------|
| `binary_missing`      | `suggested_install` | `admin shell` (sudoers apt-get) |
| `needs_content_fetch` | `needs_urls_html`   | `read_urls_html(urls=hint)` |
| `needs_ocr`           | `needs_ocr_files`   | `change_files_ocr(paths=hint)` (futuro) |
| `needs_embedding`     | `needs_embedding_for` | `create_<dom>_indices(...)` (futuro) |

Centralizzata in `runtime/auto_remediation.py` con mapping
chiuso, analogo a `runtime/system_binaries.py` (ADR 0143).

## Costi

- **Implementation**: ~80 righe (executor check + runtime hook + table).
- **Latency**: +1 step IPC executor (read_urls_html ~3-5s) per i casi che
  trigger. Senza il pattern, la query restituiva enumerazione inutile.
- **Storage**: zero.
- **Maintenance**: aggiungere una riga alla mapping table per ogni nuovo
  error_class.

## Out of scope per questa ADR

- Caching del contenuto fetchato (ADR 0105 HTTP cache lo copre).
- Limiti di lunghezza del content nel describe (esiste gia' truncation
  §2.7).
- Quando describe_entries riceve MIX di content-bearing e URL-only: la
  detection `not has_content` e' all-or-nothing; in caso misto, descrive
  normalmente. Edge case rarissimo.
- Bitcoin query specifica: dopo questa ADR la pipeline diventa
  `find_urls &rarr; describe_entries[needs_content_fetch] &rarr;
  [auto] read_urls_html &rarr; describe_entries[retry] &rarr; OK`.

## Implementation plan

1. `executors/describe_entries/describe_entries.py`: aggiungi detection +
   structured error. Re-sign manifest.
2. `executors/describe_entries/manifest.toml`: description aggiornata —
   "se gli entries non hanno content/body/text MA hanno url, ritorno
   error_class=needs_content_fetch per remediation runtime".
3. `runtime/agent_runtime.py`: hook post-invoke_executor, branch sul
   error_class, sintetizza read_urls_html, retry. Idempotency flag.
4. `runtime/auto_remediation.py` (nuovo modulo): mapping table
   error_class &rarr; (prerequisite_tool, args_builder).
5. Smoke test: query "scarica e riassumi X" senza URL → trigger fast-path
   bitcoin-like → vedi step auto-injected nel turn log.
6. ADR registry update.

Stima totale: 3-4 ore implementation + 1h smoke.
