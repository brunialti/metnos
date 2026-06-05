---
id: 0148
title: Audit pre-refactor supra interface — disalignments e debito tecnico
date: 2026-05-18
status: accepted
area: runtime | refactor | audit
related:
  - 0144  # supranet consolidation .33
  - 0145  # public distribution design
  - 0146  # LLM tier consolidation Gemma
  - 0147  # bench Qwen vs Gemma
---

## Context

ADR 0145 §2 prevedeva il refactor di 2 callsite Metnos HTTP raw verso
`suprastructure.llm.LLMClient` per ottenere un single API contract
identico fra dev e distribuzione. Prima di eseguire il refactor, audit
completo del codebase per individuare tutte le incongruenze e usare
quel passaggio come verifica strutturale.

Trovato un quadro più articolato di quanto la sola "2 callsite httpx"
suggeriva.

## Findings

### F1. La stima Explore era leggermente sbagliata: urllib, non httpx

Lo statement Explore originale parlava di "2 callsite con httpx raw".
Verifica empirica:

- `runtime/llm_provider.py::LlamaCppProvider._call` usa
  `urllib.request.urlopen` (linee 423-426), **non httpx**. Stesso
  pattern conceptual (HTTP diretto a `:8080`), mechanics differente.
- `executors/create_images_indices/create_images_indices.py::_call_vlm`
  usa httpx (confermato).

Implicazione: il refactor verso `suprastructure.llm.LLMClient` deve
gestire entrambi i pattern. Niente sostituzione "httpx.post → LLMClient"
generica.

### F2. Ollama-branch in agent_runtime: dead code con qwen3:8b stale

`runtime/agent_runtime.py:3769`:

```python
planner_provider = os.environ.get("METNOS_PLANNER_PROVIDER", "llamacpp")
if planner_provider == "ollama":
    provider = OllamaProvider(
        model=os.environ.get("METNOS_PLANNER_MODEL", "qwen3:8b"),
        endpoint=os.environ.get("METNOS_PLANNER_ENDPOINT", "http://localhost:11434"),
        think=think,
    )
else:
    # llamacpp branch (default) → Gemma 4 26B su :8080
```

Su `.33` `ollama.service` è disabilitato (ADR 0144); nessuno chiama
`METNOS_PLANNER_PROVIDER=ollama` in produzione. Branch dormiente con
ref stale a qwen3:8b. **Refactor sicuro**: rimuovere l'ollama-branch +
sostituire il fallback default coerente con ADR 0146 (Gemma).

### F3. OllamaProvider defaults disseminati con qwen3:8b

`runtime/llm_provider.py` ha **5 occorrenze** del default `qwen3:8b`:

| Linea | Contesto |
|---|---|
| 70 | `OllamaProvider.__init__(model="qwen3:8b", endpoint="http://localhost:11434")` |
| 916 | `model=cfg.get("model", "qwen3:8b")` in factory |
| 930 | docstring `{"provider": "ollama", "model": "qwen3:8b"}` |
| 937 | `model=spec.get("model", "qwen3:8b")` fallback |
| 963 | CLI default `model = sys.argv[1] if ... else "qwen3:8b"` |

L'intera classe `OllamaProvider` è effettivamente codice morto su
`.33` post-ADR 0146. Decisioni possibili:

- **A**: lasciare OllamaProvider in vita per back-compat (qualcuno
  potrebbe ancora servire qwen via ollama esterno), ma rimuovere il
  default e richiedere `model=` esplicito.
- **B**: rimuovere OllamaProvider del tutto. Pulizia massima ma
  potrebbe rompere chi ha config custom.

Scelta consigliata: **A** in questa fase, **B** quando la distribuzione
pubblica raggiunge v1.0. Per ora marcare OllamaProvider come deprecato
nel docstring + default neutro (raise se non specificato).

### F4. 85 file con `sys.path.insert("/opt/myclaw/runtime")` hardcoded

Pattern diffusissimo nel codebase: ogni script standalone che vuole
importare moduli runtime apre con:

```python
import sys
sys.path.insert(0, "/opt/myclaw/runtime")
sys.path.insert(0, "/opt/suprastructure/src")
```

Esempi: `host_throttle.py`, `bench_semantic_tuning.py`,
`describe_entries.py`, `lifecycle_summary.py`,
`bench_intent_vaglio_tier.py`, `messages.py` (già migrato pulito),
`users_email_sync.py`, `bench_prefilter_categorized.py`,
`users_pairings_sync.py`, `watch_progress_telegram.py`, + 75 altri.

**Implicazione pubblica**: ogni file con questo pattern romperebbe in
distribuzione (path non esiste sull'utente). Refactor necessario in
**tutti gli 85** prima del publish.

Pattern di sostituzione:

```python
# PRIMA (rompe in distribuzione)
import sys
sys.path.insert(0, "/opt/myclaw/runtime")
from foo import bar

# DOPO (funziona ovunque)
from runtime.foo import bar
```

Si può automatizzare con uno script `scripts/migrate-syspath-to-package.py`
(scrittura prossimo turno).

### F5. 467 reference assolute /opt/myclaw|/home/roberto|/opt/suprastructure

Conteggio totale di path assoluti hardcoded in runtime/ + executors/.
Distribuzione di tipi (campione):

- `sys.path.insert(0, "/opt/myclaw/runtime")` — 85 file (F4)
- `Path("/opt/myclaw/runtime")` nelle docstring + commenti — ~120
- Path assoluti in result JSON di bench storici — ~80 (immutabili,
  archivio)
- Path assoluti in test fixture — ~30
- Path assoluti in user prompts di esempio (`/home/user/images`
  ecc.) — ~50

Solo F4 (sys.path) è codice eseguibile. Il resto è documentazione /
storia. Per la distribuzione: F4 si fixa, gli altri si scrubbano
selettivamente solo nei file che vanno effettivamente in repo
pubblico.

### F6. qwen3:8b residuo in 10+ runtime file post-ADR 0146

Files che ancora referenziano `qwen3:8b` (escluso bench JSONL
archiviati):

| File | Tipo |
|---|---|
| `runtime/bench_intent_vaglio_tier.py` | docstring storico (OK) |
| `runtime/stress/build_tier_comparison.py` | tier list (potenzialmente attivo) |
| `runtime/agent_runtime.py` | docstring + dead ollama-branch (F2) |
| `runtime/llm_provider.py` | OllamaProvider defaults (F3) |
| `runtime/synt_multistage.py` | da verificare |
| `runtime/synth_request.py` | da verificare |
| `runtime/stress/stress_failures.py` | stress test, OK |
| `runtime/testing/populate_cases.py` | test fixture |
| `runtime/stress/stress.py` | stress test, OK |
| `runtime/safety/sanity_check.py` | da verificare |

Da indagare: `synt_multistage.py`, `synth_request.py`,
`safety/sanity_check.py` — se referenziano qwen3:8b come tier valido
sono potenzialmente broken.

### F7. Provider VLM URL doppia logica di risoluzione

`create_images_indices.py::_resolve_vlm_url`:
```python
val = (env_val or "http://127.0.0.1:8081").strip()
if "/v1/" in val or val.endswith("/chat/completions"):
    return val
return val.rstrip("/") + "/v1/chat/completions"
```

Pattern OpenAI-SDK-like (auto-completa il path). Quando si refattorera
verso `suprastructure.llm.LLMClient`, questa logica diventa
ridondante: il client si occupa del routing. Da rimuovere insieme al
refactor di `_call_vlm`.

## Plan refactor in ordine di rischio crescente

### Fase R1 — Pulizia ovvia (rischio nullo, executable subito)

- [ ] Rimuovere l'ollama-branch da `agent_runtime.py:3769` (F2).
  L'utente che vuole ollama puo' sempre configurarlo via
  `~/.config/metnos/llm_tiers.toml` — il pianificatore non deve
  saperlo.
- [ ] Cambiare OllamaProvider default da `qwen3:8b` a `None` con
  warning all'init se non specificato. Marcare classe come
  deprecated nel docstring (F3 opzione A).
- [ ] Aggiornare docstring `agent_runtime.py:21` per togliere
  riferimento a qwen3:8b come default. Sostituire con "Gemma 4 26B
  via llamacpp on :8080 (ADR 0146)".

### Fase R2 — Conversione sys.path.insert → import di pacchetto (rischio basso)

- [x] Scrittura `scripts/migrate-syspath-to-package.py` — fatto 18/5/2026.
  Detector regex (148 moduli runtime, gestisce indentazione, alias
  `_sys`, dedup multipli, skip stringhe). Dry-run su 5 file pilota
  `bench_*`: tutti i diff sembrano corretti (rimuove insert, prefissa
  `runtime.X` per import che erano bare).

**Constraint scoperto**: per applicare in massa su `.33` serve prima:
  1. creare `/opt/myclaw/runtime/__init__.py` (non esiste oggi)
  2. aggiungere `/opt/myclaw` al PYTHONPATH systemd (`systemd-http.service.d/override.conf`)
  3. restart `metnos-http.service`

L'operazione è disruptiva su produzione. **Strategia adottata**:
applicare R2 incrementalmente, **un file per volta**, quando ciascun
file migra al repo pubblico. Il pubblico ha già `runtime/__init__.py`
(verificato 18/5: `from runtime import vocab` funziona, 23 verbi
caricati).

Per applicare massivamente su `.33` (futuro):
- creare init + path supplement
- `python3 scripts/migrate-syspath-to-package.py --apply`
- restart unit
- audit residui (subdir packages, executors)

### Fase R3 — Refactor LlamaCppProvider verso suprastructure.llm.LLMClient (rischio medio)

- [ ] Verificare API `suprastructure.llm.LLMClient`:
  - signature di `chat`, `chat_with_tools`
  - gestione id_slot (ADR 0120)
  - response shape (usage tokens, thinking, tool_calls)
- [ ] Refactor `LlamaCppProvider._call` per delegare a `LLMClient`
  o sostituirlo del tutto. Mantenere la classe come thin wrapper
  finché tutti i caller non sono aggiornati.
- [ ] Smoke battery `runtime/smoke.py` PASS post-refactor.

### Fase R4 — Refactor _call_vlm in create_images_indices (rischio medio)

- [ ] Verificare se `suprastructure.llm.LLMClient` ha già un metodo
  vision-capable. Se no, estendere il provider llamacpp con
  `chat_with_image(image_url, prompt, model=...)`.
- [ ] Sostituire `_call_vlm` interno.
- [ ] Smoke con `find_images_indices` su foto reale.

### Fase R5 — Pulizia residui qwen3:8b sparsi (rischio basso)

- [ ] Caso-per-caso F6. Per le docstring storiche: lasciare con
  marker "[ADR 0146]". Per il codice attivo: convertire a
  parametrizzato.

## Output utile per la sessione corrente

- ADR 0148 (questo doc) → mappa pulita di TUTTI i lavori, niente più
  perdita di pezzi.
- Tabella files-da-toccare per ogni fase con conteggio LOC.
- Dipendenze: R1 indipendente; R2 indipendente; R3 dipende da R2;
  R4 dipende da R3 (per uniformità); R5 può partire ovunque.

## Consequences

+ Audit fatto una volta sola, riferito da ogni successiva sessione.
+ Refactor con ordine di rischio: comincia da R1 oggi, ferma quando
  serve, riprende dove era senza re-audit.
+ Database tracking emerso (F4 quantificato) — sappiamo che il
  porting completo richiede 85 file da modificare, non vago "tutto
  il codebase".
- ADR è internal, non va in distribuzione. La distribuzione vede
  solo gli effetti del refactor, non l'audit.
