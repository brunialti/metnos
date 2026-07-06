# SPEC IMPLEMENTATIVA — Qualità traduzioni / caccia agli anglicismi (§7.8)

> **Destinatario**: LLM esecutore (Opus o inferiore). Ogni passo ha file:riga, criterio di done, e test. NON serve creatività: seguire i passi in ordine.
> **Obiettivo**: eliminare gli anglicismi crudi nella prosa italiana dei prompt/description/messaggi, riscrivendo il prompt di traduzione + aggiungendo validazione post-traduzione + sweep del corpus.
> **Autore analisi**: Fable, 6/7/2026. **Vincolo**: rispettare §7.8 (italiano senza anglicismi) e la memoria `i18n-lexicon-debt` (2 locali IT+EN, non introdurre un 3° locale).

---

## 0. Contesto architetturale (fatti verificati, file:riga)

Il sistema ha **3 layer multilingua** e **4 code-path di traduzione con 4 prompt di forza diversa**:

| # | Costante prompt | file:riga | Tier | Bersaglio |
|---|---|---|---|---|
| 1 | `_PROMPT_TEMPLATE_USER_FACING` | `runtime/i18n_translator.py:49-66` | middle | stringhe UI brevi (Layer 3 = DB) |
| 2 | `_PROMPT_TEMPLATE_LLM_TARGETED` | `runtime/i18n_translator.py:72-100` | wise | testi consumati da LLM (Layer 3) |
| 3 | `_PROMPT_FILE_TEMPLATE` | `runtime/i18n_translator.py:455-485` | wise | file `.j2` (Layer 1) + description manifest (Layer 2) |
| 4 | `_PROMPT_TMPL` | `runtime/jobs/i18n_translate_pending.py:269-273` | wise | righe DB pending (Layer 3) — **il più debole, gira every_6h** |

Classificatore che sceglie il prompt: `_is_llm_targeted_key` (`runtime/i18n_translator.py:103-113`).

**Validazione post-traduzione esistente** (`_validate_translation`, `runtime/i18n_translator.py:404-447`): 4 check (Jinja syntax, placeholder set, len ratio 0.7-1.4×, sentinel leak). **NESSUN check di anglicismi.**

**Metrica di qualità** (`runtime/translator_quality.py:208-299`, `score_translation`): `0.5·cosine + 0.4·roundtrip + 0.1·placeholder`. **BUG CONCETTUALE**: in spazio embedding multilingua, una traduzione IT che lascia parole EN assomiglia di più al source EN → cosine più alto → **la metrica premia gli anglicismi**.

**Unico presidio esistente** (istruzione, non detector): `runtime/prompts/it/promoter_commentary.j2:22-23` — `NON DEVI: usare anglicismi (peer, trigger, goal, plumbing); usa innesco, obiettivo, integrazione.` — è l'unico posto che dà i **sostituti italiani**.

**Linter** `runtime/prompts_lint.py` (589 righe, 6 check L1-L6): NON valida anglicismi. Il gancio naturale è accanto a L2 hedge blacklist (`_HEDGE_PATTERNS`, `runtime/prompts_lint.py:78-88`).

---

## 1. PREREQUISITO — estrarre la whitelist canonica in un modulo unico

**Problema**: la lista di identificatori "da tenere in EN" (`find_files`, `executor`, `manifest`, `runtime`, `planner`, `synt`, `fastpath`, `autopath`, `scratchpad`, nomi-arg, verbi/oggetti) è **duplicata** in `runtime/i18n_translator.py:81` e `:467` e implicita nel job. Un detector che non legge dalla STESSA fonte dei prompt segnalerebbe falsi positivi.

**Passo 1.1** — Creare `runtime/i18n_canonical.py`:
- `CANONICAL_KEEP_EN: frozenset[str]` = domain nouns (`executor(s)`, `manifest(s)`, `runtime`, `planner`, `synt`, `fastpath`, `autopath`, `scratchpad`) — copiare ESATTAMENTE da `runtime/i18n_translator.py:81`.
- `TOOL_NAMES`, `ARG_NAMES`, `VERB_OBJECT_MODIFIERS`: derivarli a runtime da `runtime/vocab.py` (23 verbi + 23 oggetti + qualifier) invece di hardcodare — così restano in sync col vocabolario chiuso §2.2. Importare `vocab` e costruire i set.
- `ANGLICISM_MAP: dict[str,str]` = mappa anglicismo→sostituto IT, seed dai casi noti §7.8: `{"trigger":"innesco", "goal":"obiettivo", "peer":"pari", "plumbing":"integrazione", "gate":"cancello", "loop":"ciclo", "workflow":"flusso di lavoro", "fallback":"ripiego", "match":"corrispondenza", "walk":"scansione", "folder":"cartella", "request":"richiesta", "window":"finestra", "encoding":"codifica"}`. Ampliabile.

**Done 1**: `python3 -c "from runtime.i18n_canonical import CANONICAL_KEEP_EN, ANGLICISM_MAP; print(len(CANONICAL_KEEP_EN), len(ANGLICISM_MAP))"` stampa numeri >0.

**Passo 1.2** — Sostituire le liste duplicate nei prompt (`runtime/i18n_translator.py:81`, `:467`) con un riferimento generato da `i18n_canonical` (renderizzare la lista dentro il template a costruzione-prompt, non hardcodata). Verificare che il testo prompt resti equivalente.

---

## 2. DETECTOR deterministico di anglicismi

**Passo 2.1** — In `runtime/i18n_canonical.py`, funzione `find_anglicisms(text: str, lang: str) -> list[dict]`:
- No-op se `lang == "en"` (ritorna `[]`).
- Maschera gli span invarianti PRIMA di scandire: riusa `_mask_invariant_spans` (`runtime/i18n_translator.py:343`) per togliere placeholder `{...}`, code-fence, `${RUNTIME:...}`, `{# … #}` Jinja. Per i `.j2` togliere anche il frontmatter (`_content_after_frontmatter`, `runtime/prompts_lint.py:136`).
- Per ogni parola dell'`ANGLICISM_MAP.keys()`: match `\b<word>\b` case-insensitive sul testo mascherato, MA escludere se la parola fa parte di un identificatore canonico (`CANONICAL_KEEP_EN`, `TOOL_NAMES`, `ARG_NAMES`) o è dentro un `.affinity` (liste-keyword bilingui volute).
- Ritorna `[{word, suggestion, offset}]`.

**ESCLUSIONI CRITICHE** (l'agente ha misurato che il grep naïve sovrastima): NON segnalare (a) `promoter_commentary.j2` (è l'istruzione anti-anglicismo stessa), (b) arg-name in chiamate `tool(goal=...)`, (c) liste `.affinity`, (d) few-shot con query utente inglesi d'esempio (`intent_extractor_*`). Implementare come: se il match è dentro `identifier(...)` o preceduto da `=`/`.` → skip; se il file è in una whitelist di path (`_ANGLICISM_EXEMPT_PATHS`) → skip intero.

**Done 2**: test `runtime/tests/test_anglicism_detector.py`:
- `find_anglicisms("il trigger concreto", "it")` → 1 hit (trigger→innesco).
- `find_anglicisms("chiama find_files con from_step", "it")` → 0 (canonici).
- `find_anglicisms("the build_old folder", "en")` → 0 (lang en).
- `find_anglicisms("goal=... nella chiamata", "it")` → 0 (arg-name).

---

## 3. RISCRITTURA dei 4 prompt

Per ciascuno dei 4 prompt (§0 tabella), aggiungere una regola in coda, PRIMA della regola di preservazione identificatori:

```
REGOLA (target non-inglese): rendi l'italiano IDIOMATICO. VIETATO lasciare parole
inglesi nella prosa. Gli identificatori tecnici canonici (elencati sotto) restano
in inglese; TUTTO il resto va tradotto. Sostituzioni: trigger→innesco, goal→obiettivo,
loop→ciclo, gate→cancello, fallback→ripiego, workflow→flusso, match→corrispondenza,
folder→cartella, request→richiesta, window→finestra.
OK: "il ciclo sta dentro l'executor". ERRORE: "il loop sta dentro l'executor".
```

**PRIORITÀ**: `runtime/jobs/i18n_translate_pending.py:269-273` (`_PROMPT_TMPL`) — è una riga sola («Traduci … preservando placeholder e stile imperativo»), il più debole, e gira ogni 6h su 452 righe IT pending. Riscriverlo per PRIMO col template §6-conforme + whitelist da `i18n_canonical`.

**Done 3**: dopo il restart, una traduzione di prova con anglicismo nel source produce output senza anglicismi. Test manuale: `python3 -m admin.prompts_cli translate-one <key>` (verificare che il tool CLI esista in `runtime/admin/prompts_cli.py`; altrimenti chiamare `i18n_translator.translate_batch` direttamente).

---

## 4. GATE post-traduzione (rifiuta+ritenta)

**Passo 4.1** — In `_validate_translation` (`runtime/i18n_translator.py:404-447`), aggiungere il 5° check DOPO gli altri 4:
```python
angl = i18n_canonical.find_anglicisms(translated, target_lang)
if angl:
    return False, f"anglicismi non tradotti: {[a['word'] for a in angl]}"
```

**Passo 4.2** — In `_llm_translate` del job (`runtime/jobs/i18n_translate_pending.py:316-355`): stesso gate + retry (ricalca il pattern già presente per il JSON malformato a `:333`). Al 2° fallimento: marcare la riga `needs_translation=1` e loggare (NON scrivere una traduzione sporca).

**Done 4**: test `runtime/tests/test_translation_gate.py`: `_validate_translation("il loop gira", placeholders, "it")` → `(False, ...)`; `_validate_translation("il ciclo gira", ..., "it")` → `(True, ...)`.

---

## 5. SWEEP del corpus (L7 linter + DB)

**Passo 5.1** — Linter: `_check_l7_anglicisms` in `runtime/prompts_lint.py` accanto a L2 (`:178`). Usa `find_anglicisms`, emette `LintIssue(kind="L7_ANGLICISM", ...)`. Aggiungere al driver `scan` (`runtime/prompts_lint.py:523`). Gate CI come L2.

**Passo 5.2** — Sweep DB Layer 3: comando accanto a `cmd_audit_quality` (`runtime/admin/prompts_cli.py:728`) che scandisce le righe IT con `find_anglicisms`, e per ogni hit marca `needs_translation=1` (via `i18n.set` o UPDATE diretto) → il job le ri-tradurrà col prompt nuovo (§3).

**Passo 5.3** — Sweep Layer 1/2: `align_prompts`/`align_manifest_descriptions` (`runtime/i18n_translator.py:819`) già ri-generano i candidati; dopo il prompt nuovo (§3), rigenerare i `.candidate` sporchi.

**Done 5**: `python3 runtime/prompts_lint.py scan` riporta il conteggio L7 (baseline misurato dall'agente: corpus IT `.j2` ha `trigger`×4, `loop`×~8, `gate`×~10, `fallback`×6, `walk`×1, `workflow`×1 reali). Target dopo lo sweep: →0.

---

## 6. METRICA before/after (correggere il bug che premia gli anglicismi)

**Passo 6.1** — In `runtime/translator_quality.py:289` (formula `score_translation`), aggiungere un 4° termine penalizzante:
```
anglicism_density = len(find_anglicisms(translated, lang)) / max(1, n_prose_words)
score = 0.45*cosine + 0.35*roundtrip + 0.1*placeholder - 0.5*anglicism_density
```
(pesi da tarare; il punto è che la densità di anglicismi ABBASSA lo score).

**Passo 6.2** — Rigenerare `runtime/audit_quality_it.json` via `cmd_audit_quality` prima e dopo, confrontare `anglicism_density` medio.

**Metriche di successo** (tutte quantitative):
1. `# L7_ANGLICISM` da `prompts_lint scan` → 0.
2. `anglicism_density` medio nel report → ~0.
3. `roundtrip_sim` (`translator_quality.py:251`) NON deve crollare (guardia anti-regressione semantica: la de-anglicizzazione non deve rompere il significato).
4. `# righe needs_translation=1` residue (`i18n.stats()`, `runtime/i18n.py:318`) prima/dopo lo sweep.

---

## 7. ORDINE DI ESECUZIONE + RISCHI

1. §1 (modulo canonico) — prerequisito di tutto.
2. §2 (detector) + test.
3. §4 (gate) — protegge da nuove traduzioni sporche PRIMA di §3/§5.
4. §3 (prompt) — priorità al job every_6h.
5. §6 (metrica) — per misurare.
6. §5 (sweep) — per ultimo, quando prompt+gate+metrica sono pronti.

**RISCHI**:
- **Falsi positivi**: il detector che segnala `find_files`/`executor`/affinity → mitiga §1 (fonte unica) + esclusioni §2. Testare su tutto il corpus prima di attivare il gate CI.
- **Over-blocking del job**: se il gate §4 è troppo severo, le righe restano pending all'infinito. Il retry+re-queue (non scrivere sporco) è corretto, ma monitorare `i18n.stats()`.
- **YAGNI 2-locali**: NON introdurre un 3° locale né refactor del DB i18n (memoria `i18n-lexicon-debt`). Questo intervento è SOLO qualità IT/EN.
- **Corpus sorgente sporco**: alcune `description` IT hand-authored hanno anglicismi alla fonte → lo sweep §5 li marca, il job li ri-traduce. Non riscriverli a mano in massa (§2.5 «non rifattorizzare in massa»).
