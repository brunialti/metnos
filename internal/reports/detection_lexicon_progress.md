# detection_lexicon — progress notturno (15-16/6/2026)

Branch: `session/detection-lexicon-i18n` (NON committato). Obiettivo: eliminare
i fallimenti silenziosi al cambio lingua portando i lessici di detection NL
nello stesso meccanismo dell'i18n (store traducibile + daemon + fallback +
coverage guard). Universale/deterministico, no hardcoded/ad-hoc, no regressione.

## Architettura (FATTA)
- `runtime/detection_lexicon.py` — store DB (`detection.sqlite`), matcher
  (match/search/forms/mapping), fallback chain `current→en→it` via **union**
  (per it/en = comportamento IDENTICO ai costrutti lang-agnostici attuali),
  coverage guard (`verify_coverage`), supporto daemon (list_pending/
  set_translated/enqueue_language).
- `runtime/detection_lexicon_seed.py` — fonte canonica IT+EN (register_all).
- `runtime/config.py` — `DB_DETECTION`.
- `tests/runtime/i18n/test_detection_lexicon.py` — copertura/anti-silenzio/union.

## Principio anti-regressione
Per ogni concept l'UNIONE it+en deve = insieme del costrutto hardcoded
sostituito. Matcher unisce {lingua_corrente}∪{it,en}. Validato con self-test
`vecchia-costante ≡ nuovo-matcher` (corpus 333, 0 diff) + suite completa.

## Wave 1 — FATTA (suite 2667/0, test 6/6)
Concept + file call-site migrati e costanti rimosse:
- undo.grammar_marker (word)  ← tool_grammar._UNDO_MARKERS
- undo.intent_bypass (substr) ← intent_extractor._UNDO_PATTERNS
- tasks.marker (word)         ← tool_grammar._TASKS_MARKERS
- tasks.schedule_phrase (rx)  ← tool_grammar._RE_SCHEDULE_PHRASE
- tasks.recurrence_phrase(rx) ← tool_grammar._RE_RECURRENCE_PHRASE
- tasks.recurrence_word(word) ← tool_grammar._RECURRENCE_WORDS
- skills.marker (word)        ← tool_grammar._SKILLS_MARKERS
- notify.request (substr)     ← orchestration._NOTIFY_HINTS
- notify.channel (mapping)    ← orchestration._CHANNEL_HINTS
- output.count_request (rx)   ← output_policy._COUNT_MARKERS
- output.visualize_request(rx)← output_policy._VISUALIZE_MARKERS
- web.cookie_banner (substr)  ← output_format._COOKIE_BANNER_MARKERS
NB: `_legacy/fast_path._UNDO_PATTERNS` lasciato (modulo legacy in dismissione)
→ annotare per Fable.

## Wave 2 — FATTA (suite 2677/0)
Concept migrati (+9) e costanti rimosse:
- confirm.yes / confirm.no (regex) ← channels/daemon._YES/_NO_PATTERN
- compound.connector_word (word, bridge: simboli fissi + parole dal lessico)
  ← compound_decomposer._CONNECTOR_PATTERN
- query.multistep (regex) ← agent_runtime._MULTISTEP_CONJUNCTIONS_RE
- provider.markers (mapping word) ← tool_grammar._PROVIDER_SUFFIX_MARKERS
  (anche args_defaults._provider_qualifiers + cli/skills_cli._strip_unmarked_
  provider ripuntati al lessico)
- llm.refusal_marker / health.imperative / count.quantifier / dialog.resume_hint
  (substring) ← agent_runtime omonimi
Self-test equivalenza wave 2: corpus 333, 0 diff.

## Meccanismo "definitivo" — FATTO
- Daemon `jobs/detection_translate_pending.py`: traduce phrases/mapping via LLM
  (JSON→JSON, tier wise, temp0+seed); regex SALTATI (authoring manuale, §2.8).
  Registrato scheduler v2 `every_6h` (builtin_callbacks). Test offline mockato
  `tests/test_detection_translate_daemon.py` (4 test).
- Coverage guard `_startup_coverage_check()` in ensure_seeded: WARNING esplicito
  se la lingua d'istanza ha gap (muto per it/en). + `verify_coverage(lang)`.
- CLI `cli/detection_cli.py` (stats/coverage/enqueue/translate).
- Bugfix design: `CAP_PER_FIRE` letto a CALL-TIME (era module-import → non
  overridabile a runtime; emerso da isolamento test).
- E2E live: turno "quante mail" → count-intent ok; nessun warning per it.

## STATO VERIFICA (16/6, fine run autonomo)
- Suite **2677/0**. Routing bench **29/29 (100%)**. Intent bench **25/25 (100%)**.
- E2E live (http riavviato): count-intent ok, turno normale ok, NESSUN warning
  coverage per it. Branch `session/detection-lexicon-i18n`, NON committato.
- Debiti noti: `_legacy/fast_path._UNDO_PATTERNS` (copia legacy lasciata);
  `args_defaults.py:26` Path dead-import PRE-ESISTENTE; commenti residui che
  citano i vecchi nomi-costante (innocui).

## VERIFICA (Opus, sostituto di Fable NON disponibile) + fix applicati
Verdetto: meccanismo detection-lexicon CORRETTO e ZERO-REGRESSIONE (0 diff su
tutti i 21 concept verificati indip., match-mode corretti, no casing-shift,
test verdi). CONCERNS sollevati e RISOLTI stanotte:
- [HIGH→FIXED] le chiavi `MSG_ORCH_*` (migrazione i18n di orchestration.py, parte
  iniziale sessione) erano solo nel DB live, NON in `install/data/i18n_seed.sqlite`
  → install pulito = `<missing:…>` + OAuth rotto. FIX: copiate 112 righe (56
  chiavi × it/en) nel seed (552 chiavi totali, OAuth `{url}` ok).
- [MEDIUM→FIXED] mancava guard permanente di equivalenza → aggiunto
  `test_union_equals_original_snapshot` (insiemi originali congelati).
- [D→FIXED] `enqueue_language` non era automatico → `_startup_coverage_check`
  ora auto-accoda i concept scoperti per lingue non-seed (turnkey).
- [LOW] count/visualize restano regex (non auto-tradotti): scelta di fedeltà
  (lo stem `visualizz\w*` non è esprimibile come word-list). Opzionale.
Restano (decisione Roberto): (a) split del refactor MSG_ORCH_* dal branch
detection (igiene); (b) wave 3; (c) ri-verifica con Fable quando disponibile.
Suite 2678/0, routing 29/29, intent 25/25.

## DA FARE (wave 3 — strutturale, HIGH RISK, prossima sessione)
RICETTA (a regressione zero, property-test served-data == snapshot it/en):
- prefilter `_OBJECT_HINTS` → concept mapping `object.hints` (shape identica:
  object→[forme]); call-site 393 itera `_dl.mapping("object.hints")`.
- prefilter `_VERB_TO_CANONICAL` (form→canon, INVERSO) → concept mapping
  `verb.canonical` (canon→[forme], shape naturale) + helper di reverse-lookup
  costruito a load; call-site 185/227/232.
- prefilter `_STOPWORDS_IT/EN` → concept phrases `stopwords` (per-lingua).
- `_IT_CLITIC_SUFFIXES` morfologia IT: phrases (suffissi) o lasciare (morfo).
- `_FS_EXTENSIONS` = NON NL (estensioni file lingua-invarianti) → NON migrare.
- ordering_clause `_PATTERNS`(+mode+capture) / `_DESC_RE` / `_FIELD_FAMILIES`
  (mapping) / `_ARTICLES` / `_KEY_STOP`: regex con capture → bridge word-list.
- time_window_parser/resolver + recurring_tasks: regex con capture (date/ore).
- agent_runtime `_PROPOSE_INTENT_RE` / `_AVAILABILITY_MARKERS_RE`: regex giganti
  → verbatim it+en (dedup) o bridge; alto costo, basso rischio se verbatim.
- compound_decomposer `FORMAT_HINTS` (form→(obj,qual)): rimodellare a mapping
  o lasciare (nomi formato per lo più universali).
DOPO ogni concept: property-test + routing FULL bench (non subset) + intent bench.
- Wave 2 (boolean/regex puliti): channels/daemon _YES/_NO_PATTERN;
  compound_decomposer _CONNECTOR_PATTERN + FORMAT_HINTS; tool_grammar
  _PROVIDER_SUFFIX_MARKERS; agent_runtime _LLM_REFUSAL_MARKERS,
  _HEALTH_IMPERATIVE_KEYWORDS, _COUNT_QUANTIFIER_MARKERS,
  _RESUME_AFTER_DIALOG_HINTS_IT/EN, _AVAILABILITY_MARKERS_RE,
  _MULTISTEP_CONJUNCTIONS_RE, _PROPOSE_INTENT_RE.
- Wave 3 (strutturati / HIGH RISK): ordering_clause (_PATTERNS+mode+capture,
  _DESC_RE, _ARTICLES, _KEY_STOP, _FIELD_FAMILIES); prefilter
  (_VERB_TO_CANONICAL, _OBJECT_HINTS, _STOPWORDS_IT/EN, _IT_CLITIC_SUFFIXES
  come `mapping`/`phrases`, algoritmo invariato); time_window_*; recurring_tasks
  regex.
- Daemon `jobs/detection_translate_pending.py` (gemello i18n): traduce
  phrases/mapping via LLM JSON→JSON; regex via word-list bridge.
- Wire `verify_coverage(current_lang)` allo startup (health/admin) — rende il
  gap ESPLICITO. Admin CLI `detection_cli.py` (stats/coverage/translate).
- Test a tappeto finale + review Fable.

## Comandi utili
- self-test equivalenza: vedi cronologia (corpus da forme+decoy).
- coverage: `python3 detection_lexicon.py coverage <lang>`
