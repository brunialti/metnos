---
id: 0092
title: Prompt-as-data — directory `runtime/prompts/` + auto-allineamento bilingue
date: 2026-05-05
status: proposed
area: runtime, prompts, i18n, maintenance
related:
  - 0072  # adaptive re-rank intra-turno
  - 0080  # telemetria StepLog
  - 0085  # allineamento doc 4/5
complements:
  - 0091  # get_inputs + callback (separa logic/policy: i prompt restano hardcoded oggi)
---

## Context

Snapshot 5 maggio 2026. I prompt LLM core di Metnos sono **hardcoded inline
nei moduli Python**:

| Sorgente | LOC | Mutability |
|---|---|---|
| `PLANNER_SYSTEM_NATIVE` (agent_runtime.py:423) | ~1500 | alta |
| Synt 5-stage (synt_multistage.py) | ~500-1500 | media |
| Intent extractor | ~50 | bassa |
| Vaglio judge | ~200 | bassa |
| Describe / Classify entries | ~100 ciascuno | bassa |
| Manifest description (×52 executor) | 100-800 char | media |

I 118 messaggi user-facing (ERR_/WARN_/MSG_/LOG_) sono già nel DB i18n
(sqlite, `~/.local/share/metnos/i18n.sqlite`), 39 chiavi standard +79
description executor, IT+EN, `needs_translation=0`. Il dict `MESSAGES`
in-memory di legacy fallback è stato rimosso il 5/5/2026.

I prompt LLM **non sono nel DB**: rimangono inline nei moduli. Quattro
problemi operativi:

1. **Verificabilità** assente. Non c'è un punto unico per vedere quali
   prompt esistono, di che dimensione, in quante lingue. Per
   inventariare serve grep multifile.

2. **Comparazione** difficile. Non si può confrontare facilmente IT vs
   EN (l'EN non esiste). `git diff` su agent_runtime.py mostra cambi
   PLANNER mescolati a cambi di codice non-prompt.

3. **Manutenzione** rischiosa. Edit su 1500 righe di prompt dentro un
   modulo da 2500 righe → blast radius esteso, review difficile, easy
   typo su escape Python.

4. **Bilinguismo bloccato**. PLANNER è solo IT. Se domani serve EN
   (Gemma 4 26B funziona meglio in inglese su task complessi), l'unica
   opzione attuale è duplicare il blocco nel modulo Python. Due varianti
   strutturali, entrambe peggiorative:
   - **Insieme nel codice**: il LLM riceve IT+EN simmetrici nello stesso
     payload → confonde, raddoppia token, perde focus su una sola variante
     (CLAUDE.md memoria 1/5/2026 «multilingua alternativo, non duplicato»).
   - **Separati nel codice**: due moduli paralleli (o due variabili
     `PLANNER_SYSTEM_IT` + `PLANNER_SYSTEM_EN`) → la dimensione del
     modulo Python esplode (PLANNER da 1500 → 4500+ righe per 3 lingue),
     il maintainer perde la vista d'insieme, le strutture sezione/esempi
     drift fra lingue senza segnalazione.

In altre parole: **se vogliamo bilinguismo, hardcoded non scala**.
Hardcoded è una decisione che vincola IT-monolingua come stato terminale.

L'obiettivo NON è il multilingua per l'utente finale (oggi un solo utente
italiano). L'obiettivo è quattro:

1. **Verificabilità**: vedere a colpo d'occhio cosa c'è.
2. **Comparazione**: diff cross-lingua e cross-versione strumentale.
3. **Manutenzione**: edit isolati, blast radius limitato, review puntuale.
4. **Bilinguismo gestibile**: IT canonical + EN come riferimento/A-B,
   con allineamento automatico (un edit IT segnala EN out-of-sync).

## Decision

Esporre i prompt LLM come **dati persistiti su filesystem**, non come
stringhe inline nei moduli Python. Storage = directory strutturata
`runtime/prompts/`. Render via Jinja2 con composition. Bilinguismo per
suffisso file. Allineamento cross-lingua automatizzato via
`i18n_translator` esistente.

NON nel DB sqlite (i18n.sqlite resta dedicato ai messaggi brevi
user-facing). Asimmetria struttura/dimensione: un prompt è 50-1500 righe
di Markdown/Jinja2, una entry messaggio è 1-3 righe. Forzare entrambi
nello stesso DB peggiora ognuno.

### Layout — directory per lingua

```
runtime/prompts/
├── it/
│   ├── planner.j2
│   ├── synt_naming.j2
│   ├── synt_signature.j2
│   ├── synt_tests.j2
│   ├── synt_description.j2
│   ├── synt_code.j2
│   ├── intent_extractor.j2
│   ├── vaglio.j2
│   ├── describe_entries.j2
│   └── classify_entries.j2
├── en/                          # quando si attiva — stessi nomi
│   └── ...
└── xx/                          # placeholder per future lingue (fr, de, ...)
```

Una lingua = una dir. `ls runtime/prompts/it/` è l'inventario senza
rumore cross-lingua. Aggiungere una lingua = creare una dir nuova; niente
edit massivo di file esistenti. `diff -r runtime/prompts/it/
runtime/prompts/en/` confronta cosa esiste in una lingua e non nell'altra.
Maintainer monolingua lavora solo nella sua dir.
Niente partial, niente sub-directory, niente meta-file: la complessità non
ha giustificazione (efficienza/efficacia/manutenibilità) per la
dimensione attuale del catalog.

**Niente `_shared/` partial**: i "blocchi condivisi" del PLANNER (vocab,
project_paths, users) sono in realtà **variabili dinamiche** iniettate dal
loader come kwarg Jinja2 (`{{ vocab_objects }}`, `{{ project_paths }}`).
Il valore è calcolato dal codice Python (vocab.py, runtime config), non
da un partial statico — nessuna duplicazione testuale reale da fattorizzare.

**Niente `_meta.toml` per version**: il sync cross-lingua si calcola da
`git log -1 -- planner.it.j2` vs `git log -1 -- planner.en.j2`. Git è già
il sistema di versioning; un secondo "version number" parallelo sarebbe
ridondante e drift-prone.

**Niente `INDEX.md` persistente**: generato on-demand da
`metnos-prompts list`. Non serve un file da mantenere allineato.

Manifest description NON in `runtime/prompts/`. Restano nel TOML del
proprio executor (sono parte del manifest). Migrazione bilingue a fase 3
con `description.it` / `description.en` nel TOML stesso.

### Engine: MiniJinja (Rust core + Python bindings)

**Decisione 5/5/2026**: usare **MiniJinja** invece di Jinja2 puro Python.

Motivazioni:
1. **Drop-in syntax**: `.j2` files sintatticamente identici a Jinja2 (stesso `{{ var }}`, `{% if %}`); maintainer già familiare con la sintassi (template HTML in `runtime/templates/` la usano).
2. **Errori più chiari**: diagnostica significativamente migliore di Jinja2 (line/col preciso, hint sui typo nelle variabili).
3. **Performance**: ~20-30% più veloce in render, irrilevante in valore assoluto ma indicativo della maturità.
4. **Disponibilità Windows**: wheel pre-built su PyPI per `win_amd64`, `win_arm64`, `manylinux_*`, `macos_*`. `pip install minijinja` zero-compile su tutte le piattaforme rilevanti.
5. **Zero impatto executor remoti**: i prompt LLM sono server-side (girano sul `.33`); il client Rust di executor remoti (CLAUDE.md §10.4) non li legge mai. MiniJinja serve solo sul server.
6. **Coerenza con possibile migrazione Rust**: MiniJinja è scritto in **Rust nativo** (crate `minijinja` su crates.io); se domani Metnos migrasse a Rust, stesso engine, stessi `.j2`, zero rewrite dei prompt. Scelta strutturalmente migliore di Jinja2 puro Python (lock-in linguaggio) o Mustache (libreria-shopping fra implementazioni).

I template HTML esistenti in `runtime/templates/` restano su Jinja2 puro
per ora (drop-in possibile più tardi se si vuole un solo engine in tutto
il progetto). Nessuna pressione di migrazione: i due engine convivono
senza conflitto.

### Loader runtime — `runtime/prompt_loader.py` (boot-resolved root)

A boot Metnos legge `current_lang` da env (`METNOS_LANG`, default `it`)
e fissa **una sola root** per il processo:
`PROMPTS_ROOT = runtime/prompts/{current_lang}/`. Da quel momento il
loader carica solo da quella root. Niente parametro `lang` nelle
chiamate `get(role)`. Niente fallback chain runtime.

Coerente con il principio «una lingua per turno» (CLAUDE.md memoria
1/5/2026): il sistema è **monolingua dal punto di vista del LLM**,
multilingua come patrimonio statico sul filesystem. Cambio lingua =
restart (acceptable: decisione amministrativa, non user-toggle).

API minimale (~20 LOC):

```python
import os
from pathlib import Path
import minijinja

_lang = os.environ.get("METNOS_LANG", "it")
_root = Path(__file__).parent / "prompts" / _lang
_env = minijinja.Environment(loader=lambda name: (_root / name).read_text())

def get(role: str, **vars) -> str:
    """Render runtime/prompts/<current_lang>/<role>.j2."""
    return _env.render_template(f"{role}.j2", **vars)

def validate_invariant() -> None:
    """Boot check: ogni sub-dir lingua ha stesso set di file di it/.
    Chiamata da metnos_http_server al startup. Boot fail se manca."""
    base = Path(__file__).parent / "prompts"
    canonical = {p.name for p in (base / "it").glob("*.j2")}
    for sub in base.iterdir():
        if sub.is_dir() and sub.name != "it":
            files = {p.name for p in sub.glob("*.j2")}
            missing = canonical - files
            if missing:
                raise RuntimeError(
                    f"prompts/{sub.name}/ missing files: {sorted(missing)}"
                )
```

Determinismo (CLAUDE.md §7.9): zero LLM nel loader. Niente DB,
niente network. Filesystem-as-source-of-truth, cache Jinja2 built-in.

**Invariante enforced al boot, non runtime**: l'invariante «tutte le
sub-dir lingua hanno lo stesso set di file di `it/` (canonical
reference)» è validata da `validate_invariant()` chiamata da
`metnos_http_server` al startup. Se manca un file in `en/` o `xx/`,
**boot fail** con errore esplicito (es. `prompts/en/ missing files:
['synt_code.j2']`). Niente fallback silenzioso a runtime: un prompt
mancante è un bug del maintainer da risolvere prima del deploy, non un
degrado graceful.

### Auto-allineamento bilingue (chiave architetturale)

Un edit di `planner/it.j2` triggera automaticamente:

1. **Detection desync**: `_meta.toml` di ogni ruolo registra
   `version_per_lang = {it: N, en: M}` + sha256 del template per lingua.
   Pre-commit hook detecta che `it.j2` è cambiato e bumpa `version[it]`.
   Flag `EN out-of-sync` in INDEX.md (mostra delta versione).

2. **Traduzione automatica opt-in**: comando admin
   `metnos-prompts translate <role> --to=en` invoca `i18n_translator`
   esistente (o LLM frontier per qualità) sull'`it.j2` aggiornato e
   genera `en.j2` candidato. Il maintainer review la traduzione e fa
   `metnos-prompts mark-synced <role>` per allineare le versioni.

3. **Fallback graceful**: a runtime, se `lang=en` è richiesto ma
   `version[en] < version[it]`, il loader logga warning ma serve
   comunque `en.j2` (lag accettabile). Il ruolo del flag è solo
   informativo per la manutenzione.

4. **Daemon translator** (estensione di `i18n_translator.py`): in cron
   notturno, scansiona prompt out-of-sync e propone traduzioni. Niente
   auto-merge: il maintainer applica via comando.

Costo di mantenere bilinguismo crolla: l'edit IT è la fonte unica, l'EN
si rigenera/aggiorna in semi-automatico, il maintainer review solo le
divergenze semantiche, non riscrive da zero.

### Tool admin

Tre comandi, ~150 LOC totali in `runtime/admin/prompts_cli.py`:

```
metnos-prompts list                                  # tabella INDEX.md (+ stato sync)
metnos-prompts show <role> [--lang=it]              # render finale (con vars risolte)
metnos-prompts diff <role>                          # diff strutturale IT vs EN
metnos-prompts translate <role> --to=<lang>         # genera traduzione candidato
metnos-prompts mark-synced <role>                   # bump version in _meta.toml
metnos-prompts validate                             # lint Jinja2 + placeholder + stile §6
```

### Pre-commit lint

Hook in `.git/hooks/pre-commit` (e replicato in CI):

- Sintassi Jinja2 valida per ogni `.j2` modificato.
- Placeholder usati nel codice (`get(role, **vars)`) esistono nel template.
- Sezioni richieste dallo stile §6 (DEVI/NON DEVI/OK/ERRORE per le
  regole prescrittive) presenti.
- Lunghezza max 4-5 righe per regola §6.
- Su edit di un `it.j2`, bump automatico di `version[it]` in `_meta.toml`
  e flag `en.j2` come pending sync.

### Smoke trigger

Su modifica di qualsiasi `.j2`, smoke battery (CLAUDE.md §10.6.1) scatta
automaticamente in pre-push. Niente push silenziosi su cambi prompt.

## Migration plan

Phase ordinate, ognuna self-contained e fermabile.

### Phase 1 — Infrastruttura + PLANNER (3 giorni)

- Crea `runtime/prompts/` skeleton.
- Scrivi `prompt_loader.py` + tests (~10 case).
- Scrivi `admin/prompts_cli.py` con comandi `list`, `show`, `validate`.
- Genera `INDEX.md` initial (vuoto).
- **Estrai PLANNER_SYSTEM_NATIVE → `runtime/prompts/planner/it.j2`**:
  - I placeholder esistenti (`__VOCAB_OBJECTS__`, `__PROJECT_PATHS__`,
    `__USERS_KNOWN__`) diventano Jinja2 `{{ vocab_objects }}` etc.
  - Le sezioni dei vincoli di dominio + esempi diventano partials
    `_shared/{vocab_block,project_paths,users_known,examples}.j2`.
- `agent_runtime.py:1623` cambia da `planner_system = PLANNER_SYSTEM_NATIVE`
  a `planner_system = prompt_loader.get("planner", current_lang, vocab_objects=..., project_paths=..., users_known=...)`.
- Smoke battery dopo l'estrazione (deve restare verde).
- Pre-commit hook attivo.

Output Phase 1: PLANNER ha verificabilità, comparazione cross-version
(via `git log runtime/prompts/planner/it.j2`), manutenzione isolata.
Bilinguismo non ancora attivato (solo `it.j2`).

### Phase 2 — Synt + minor prompts (2 giorni)

- Estrai i 5 stage synt → `runtime/prompts/synt_*/it.j2`.
- Estrai intent_extractor, vaglio, describe_entries, classify_entries.
- Verifica tests synt smoke 6q post-estrazione (CLAUDE.md §8.4).

### Phase 3 — Bilinguismo IT+EN (2 giorni)

- Implementa auto-detect desync + bump version in `_meta.toml`.
- Estendi `i18n_translator.py` per gestire prompt lunghi (chunking
  semantico per sezioni, non riga per riga).
- `metnos-prompts translate planner --to=en` genera `en.j2` candidato
  (review umana obbligatoria).
- Daemon notturno traduce prompt out-of-sync.

Phase 3 è opzionale. Si attiva quando si vuole effettivamente switchare
il PLANNER su EN per A/B test o per utenti EN.

### Phase 4 — Manifest description bilingue (3-4h)

Schema manifest TOML cambia:
```toml
[description]
it = "Esplora un sito web da seed URL..."
en = "Explore a website from seed URLs..."  # può laggare
```

Loader catalog (`runtime/loader.py`) risolve via `manifest["description"][current_lang]` con fallback IT.

**Genesi monolingua, sync async** (decisione 5/5/2026 sera, simmetrico Phase 3):
- Stage 4 synt (`synt_description.j2`) produce SOLO `description.<current_lang> = "..."` (una sola lingua, quella di processo).
- Daemon notturno (`i18n_translator` esteso da Phase 3) traduce automaticamente le altre lingue, salva candidate in `_pending/`, notifica review umano.

Migrazione one-shot:
- 43 manifest handcrafted: batch translate IT→EN via translator Phase 3, review, promozione, re-firma Ed25519.
- 11 synth manifest esistenti: idem.

Affinity tags restano IT+EN misti (metadata ranking, non testo prompt — non viola «alternativo non duplicato»).

### Phase 5 — Cleanup ADR doc (0.5 giorni)

- Aggiorna CLAUDE.md §2.5 (manifest leggibili da LLM medium): aggiungi
  «§ analoga vale per i prompt LLM in `runtime/prompts/`».
- Aggiorna CLAUDE.md §10.6 con sezione `runtime/prompts/`.
- Aggiungi `metnos_prompts_workflow.md` come memoria.

## Consequences

### Positive

1. **Verificabilità**: `ls runtime/prompts/` mostra l'inventario.
   `metnos-prompts list` aggiunge metriche (size, lingue, sync).

2. **Comparazione**: `git diff runtime/prompts/planner/it.j2`,
   `git log -- runtime/prompts/planner/it.j2`, `metnos-prompts diff
   planner` → tutti nativi, nessun tool custom serio richiesto.

3. **Manutenzione**: file dedicato per ruolo, max ~200 righe ciascuno
   (PLANNER tagliato in partial). PR review puntuale, blast radius
   ristretto. Pre-commit lint protegge da typo Jinja2.

4. **Bilinguismo gestibile**: edit IT è canonical, EN si rigenera in
   semi-automatico, INDEX.md mostra cosa è out-of-sync. Costo marginale
   di mantenere EN ≈ 10-20% del costo di IT (review traduzione vs
   riscrittura).

5. **Decoupling logic/policy**: il codice Python contiene logica
   (chiamate LLM, gestione step), i prompt contengono policy testuale.
   Cambi al PLANNER non toccano il codice.

6. **Hot-reload opzionale**: in dev, modifica un `.j2`, ricarica al
   prossimo turno senza restart. Non default in prod (cache è ok).

### Negative

1. **Doppio storage** per i contenuti testuali del progetto: messaggi
   brevi nel DB sqlite, prompt LLM nel filesystem. Maintainer deve
   sapere la differenza. Mitigato dalla regola: brevi e numerosi → DB,
   lunghi e pochi → filesystem.

2. **Pre-commit hook** aggiunge step manutentivo. Mitigato perché è
   replicato in CI (hard gate non bypassabile).

3. **Onboarding**: nuovo agente/sviluppatore deve scoprire
   `runtime/prompts/` come «dove vivono i prompt». Mitigato da CLAUDE.md
   sezione dedicata + `metnos-prompts list` come scoperta.

4. **Dipendenza da Jinja2** (già presente per `runtime/templates/`,
   nessuna nuova dipendenza).

5. **Critical path filesystem**: se il filesystem è corrotto, il
   PLANNER non parte. Stesso failure mode di oggi (codice Python sul
   filesystem).

### Neutral

- **Versioning**: git già fa versioning del filesystem. `_meta.toml`
  bumpa una version logica (per sync IT↔EN), non per storia git.
- **Testing**: smoke battery copre già il PLANNER end-to-end. Nessun
  test specifico per prompt (sono asset, non codice).

## Alternatives considered

### A. DB sqlite (estensione di `i18n.sqlite`)

Stessa toolchain dei messaggi. Pro: pattern noto. Contro: editing pessimo
(cell sqlite con 1500 righe di Markdown), diff git binario, `git blame`
non funziona. **Scartato** per gli obiettivi specifici di
verificabilità/comparazione/manutenzione.

### B. TOML hierarchico per ruolo

`runtime/prompts/planner.toml` con sezioni `[it]` e `[en]`. Pro:
filesystem-friendly, tipato. Contro: stringhe multi-line in TOML sono
goffe per 1500 righe; partial inclusion non nativo (devi reinventare
Jinja2-lite). **Scartato**: Jinja2 + Markdown è migliore per asset
testuali lunghi.

### C. Solo extraction in `*.txt` senza Jinja2

Pro: massima semplicità. Contro: perde la composition (PLANNER ha
sezioni dinamiche `__VOCAB_OBJECTS__` etc.). Costretto a string
substitution manuale. **Scartato**: Jinja2 esiste già nel progetto.

### D. Mantenere status quo (hardcoded)

Pro: zero costo. Contro: non scala a multilingua, scoraggia l'edit, mescola
diff prompt e diff codice in PR. **Scartato**: blocca evolutivamente.

## Acceptance criteria

Phase 1 dichiarata done quando:

- [ ] `runtime/prompts/planner/it.j2` esiste e contiene il PLANNER attuale
      verbatim (modulo placeholder Jinja2).
- [ ] `prompt_loader.get("planner", "it", ...)` produce output identico
      al PLANNER_SYSTEM_NATIVE attuale (test deterministic).
- [ ] Smoke battery 8/8 verde post-estrazione.
- [ ] `metnos-prompts list/show/validate` funzionanti.
- [ ] Pre-commit hook attivo.
- [ ] `agent_runtime.py` rimosso `PLANNER_SYSTEM_NATIVE` hardcoded
      (solo import dal loader).
- [ ] CLAUDE.md aggiornato (sezione 10.6 nuova entry).

## Notes

Questa ADR **NON tocca il DB messages** (`i18n.sqlite`): rimane
single-source-of-truth per i 118 messaggi user-facing brevi.

Asimmetria architetturale ratificata: **brevi e numerosi → DB sqlite;
lunghi e pochi → filesystem strutturato**. Stessa logica di
`runtime/templates/` per HTML.

L'obiettivo dichiarato non è multilingua per il consumer finale, ma:
verificabilità + comparazione + manutenzione + bilinguismo gestibile.
La phase 3 (bilinguismo attivo) è opt-in, fermabile, e si appoggia a
infrastruttura `i18n_translator` esistente per ridurre il costo.
