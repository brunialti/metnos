# Handoff — Tutor F2 sera 24/7/2026: stato, scala 94→verde, riduzione F1

> Sostituisce `handoff_checkpoint_2026-07-24_tutor_f2_certification.md` come
> punto di ripresa. Alla ripresa: leggere QUESTO file per intero, poi partire
> dal §5 (Passo 1 residuo) e §6 (Passo 2). Roberto ha ratificato il piano a
> passi; le decisioni ancora sue sono nel §8.

## 1. Stato consegnato (tutto in albero, NIENTE committato)

Numeri finali misurati (report JSON in
`internal/reports/tutor_f2_cert_2026-07-24/`):
- **Certificazione `--f2-only`: 94/134** (cert4 completa + cert5 sui 3 gruppi
  ri-ratificati). Per gruppo: f1_equivalence 22/38 · admin_ui 17/28 ·
  typical_operations 25/32 · **boundary 12/12 (zero false-steal)** ·
  conversation 18/24.
- **Multidominio 105/109** (`multidomain_final3.json`; 4 residui noti:
  coppia get_inputs con vincitore semanticamente corretto, coppia undo a
  0,060-0,061 dalla banda).
- **Cross-lingua 4/4** (FR/DE/ES/PT ai gate di retrieval).
- **pytest**: tutor 80/80; tutor+i18n 327 + 392 subtest; credential 94.
- **Live validati** (dopo restart di entrambi i servizi): tutor
  `5d8061e866394cff` (credenziali mailbox IT — risolve la classe del turno
  insoddisfacente `6b84d98b`), `d57eb2bb8a784d57` (GitHub overview con
  prerequisito credenziale+vagli); form credenziali `3e1a4874645748ac`
  (mail nuovo → form guidato) e `497f8f43f1a54b25` (github esistente →
  dialogo mandato). ATTENZIONE: l'ultimo turno TUTOR in prod è
  pre-ultimissimo-restart: il primo turno tutor nuovo ricompila il catalogo
  prod (~30-60 s una tantum).

### Cosa è stato implementato oggi (per famiglia, con file)

**Tutor F2 — retrieval/rappresentazione** (ADR 0198, addendum «inventario»):
- `runtime/ui_surfaces.py`: campo `knowledge_audience` (accesso≠conoscenza;
  devices=`user` da card F1 `audience_minima`) + validator; controlli devices
  con «monouso/temporaneo» (decisione (d), vedi §8); **guardia anti-deriva**
  `template`/`structure_sha` + `template_structure()`/`structure_fingerprint()`
  + CLI di refresh (`python3 runtime/ui_surfaces.py`); docstring col contratto.
- `runtime/tutor/sources.py`: unit ui con `knowledge_audience`; semantic
  `ui_procedure` data-driven dai controlli; **inventario = unit TESTA
  (`runtime-capabilities-overview-<lang>-00`, prosa generale + aree
  naturalizzate) + parti con sole finalità dai manifest** (niente cloni,
  niente slug); riga contratto provider (credenziale+vagli) già nel testo.
- `runtime/tutor/semantic.py`: hook `explain` esteso (walk_selected,
  final_selected, expansion_events); banda `METNOS_TUTOR_KNOWLEDGE_BAND`
  (default 0,06); budget per (documento, sezione); **espansione fratelli dei
  gruppi capability** (fonte logica divisa solo per dimensione si ricongiunge)
  con eviction del più debole FUORI gruppo e membri già ricongiunti PROTETTI
  (bug «ping-pong» trovato con l'instrumentazione e chiuso: un anchor espulso
  non ri-espande).
- `runtime/tutor/service.py`: **ledger di copertura 2.0** — checklist da
  TUTTE le fonti strutturate selezionate (righe inventario + finalità manifest
  via `_catalog_purpose` sul testo dopo il primo punto + contratti superficie
  dal registro `by_key`); lang della richiesta da `config.DEFAULT_LANG`;
  marker provider da `vocab.PROVIDER_SUFFIXES` (SoT).
- `runtime/prompts/{it,en}/tutor_compose.j2` **v8**: lead naturale = PRIMA
  riga sulle domande procedurali; percorso UI subito dopo il lead (la vecchia
  clausola di precedenza contraddiceva corpus e forma delle fonti); passi e
  condizioni di arresto («Fermati se»/“Stop if”) riportati per intero; voci
  ledger `tools=`/`surfaces=`; prerequisito credenziale+vagli per provider.
- Fusione titolo/corpo (B2 del vecchio handover): PROTOTIPATA E SCARTATA con
  misure (+1 problema/−1 controllo; `proto_fusion_report.json`).

**Form credenziali iniettato dal DOMINIO** (ADR 0199 — leggerlo):
- `[credential_form]` nei manifest firmati: `executors/read_messages` (mail),
  `executors/login_sites` (site), `executors/set_credentials` (api generico),
  skill installata `~/.local/share/metnos/executors/skills/github/
  find_issues_github` (github: username/token/repo — campi del binding reale).
  TUTTI e 4 ri-firmati. Il bundle sorgente github NON ha ancora la sezione
  (da riportare alla prossima revisione della skill).
- `runtime/credentials.py`: collettore `credential_form_kinds()` dal catalogo
  ammesso (dormienti inclusi; kind duplicato = fail-loud), `kind_for_binding`,
  `canonical_binding`; esenzione STRUTTURALE in
  `assert_no_secrets_in_return` (solo elementi di `schema.choices` con sole
  chiavi {value,label} — opzione UI ADR 0127, da ratificare in quanto ritocco
  a un invariante di sicurezza).
- `executors/set_credentials/set_credentials.py` + manifest 0.4.0: binding
  NUOVO senza `fields` → round tipo+nome (saltato se `detect_prefixes` o arg
  `credential_kind`) → round campi (segreti kind `credentials` = password
  mascherata) → percorso preesistente (sovrascrittura/mandato). Ritorno via
  `resume_executor_with_values` + `merge_into: "fields"` → segreti MAI dal
  planner; `_fields_form` scarta gli opzionali vuoti.
- i18n: 19 chiavi nuove IT+EN (16 form + 3 github) nel DB vivo E nel seed
  `install/data/i18n_seed.sqlite` (0 live-only, integrity ok, provenienza
  source_text_hash allineata); guard
  `tests/runtime/i18n/test_seed_i18n_gate_keys.py` esteso. **GOTCHA**: righe
  i18n aggiunte a runtime restano nel WAL, invisibile alla sandbox bwrap →
  SEMPRE `PRAGMA wal_checkpoint(TRUNCATE)` dopo insert.

**Ratifiche già applicate** (Roberto: «a: ok, b: ok»):
- (a) corpus: `f1-changes-restricted` e `admin-restricted-user` da
  `restricted` → `answer` con attribuzione all'amministratore (risposte da
  sole fonti pubbliche, zero leak — misurato).
- (b) `scripts/certify_tutor_f2.py`: gate «Telegram» riga-scoped (vietata la
  NAVIGAZIONE Telegram fuori canale, non il nome del servizio omonimo).

**Doc aggiornate**: ADR 0198 (2 addenda), **ADR 0199 nuovo**, RM-0003 §5.3
(meccanismi + analisi `ui_surfaces` + fine-stato manutenzione 0),
CLAUDE.mutabile (§S range ADR 0199, riga Tutor F2, riga form credenziali).

## 2. Vincoli ereditati SEMPRE validi

- NO reintroduzione del boost capability +0.08 né retry lessicali; NO liste
  di frasi/sinonimi nel codice (→ `detection_lexicon`); NO branch per query
  di test; NO frasi-specchio del corpus nei contenuti.
- Certificazioni SEMPRE sequenziali e a macchina scarica: il lavoro CPU
  (embedder ONNX, rebuild, analyzer) affama il llama-server sulla memoria
  unificata → turni prod oltre i ~100 s → 524 dal bordo Cloudflare (successo
  2 volte oggi). Un solo carico pesante alla volta.
- §8.6 niente restart durante turni attivi; §7.10 re-sign dopo OGNI edit
  executor/manifest; MAI store reali nei test (oggi una probe ha scritto
  `smtp_prova-effimera` nel vault reale — rimossa subito: usare binding
  inesistenti che si fermano PRIMA della store, o HOME finto).
- Worktree pieno di file untracked di altre attività: diff mirati, niente
  rollback globali.

## 3. Catalogo isolato e harness

- Catalogo di lavoro: `/tmp/metnos-public-docs-catalog` (potrebbe NON
  sopravvivere a un riavvio host: ricrearlo costa un rebuild completo ~10-15
  min di embed; lo script lo fa da solo).
- Analyzer 109 query: `python3 scripts/analyze_tutor_multidomain.py
  --catalog-dir /tmp/metnos-public-docs-catalog --report <out.json>`.
- Certificatore: `python3 scripts/certify_tutor_f2.py --f2-only
  --catalog-dir /tmp/metnos-public-docs-catalog --group <g> --report <out>`;
  gruppi: f1_equivalence, admin_ui, typical_operations, boundary,
  conversation.
- Hook `explain` in `semantic.retrieve_sources`: threshold/band/ranked/
  walk_selected/final_selected/expansion_events — usarlo, non re-implementare
  il ranking.

## 4. Il piano a passi ratificato (94/134 → verde)

Tassonomia dei 40 rossi: ~14 attese-fotocopia F1 · ~18 sbadataggini composer
(materiale in contesto, 1 dettaglio perso, varianza fra run) · ~4 mode
(«Mostrami…»→ACT) · 1 pari-fallimento documentato (EN «in practice»).

## 5. PASSO 1 — residuo da fare (ratificato, atteso ~105-108)

Fatto: (a)+(b). Resta:
1. **Gate a radici flessive via `detection_lexicon`** (Roberto: usare gli
   helper esistenti — VERIFICATO che funzionano: `match(concept, text)` unisce
   le forme IT∪EN da `_resolve`, quindi copre risposte in entrambe le lingue
   senza helper nuovi; kind `regex` per le flessioni).
   - Registrare in `runtime/detection_lexicon_seed.py::register_all()` i
     concept `tutor_gate.*` (kind `regex`), SOLO per le attese fallite per
     flessione: `create` (it `\bcrea\w*|\bcreazion\w*`, en `\bcreat\w*`),
     `directory` (it `\bcartell\w*|\bdirector\w*`, en
     `\bfolder\w*|\bdirector\w*`), `one_off` (it `\buna sola\b|\bsingola
     esecuzion\w*|\buna volta\b`, en `\bone[- ]off\b|\bsingle run\b|
     \bone[- ]time\b`), `hash` (it `\bhash\b|\bimpront\w*`, en
     `\bhash\w*|\bfingerprint\w*`), `location` (it
     `\bposizion\w*|\bgeolocal\w*`, en `\blocation\b|\bgeolocat\w*`),
     `admin_role` (it `\bamministrat\w*|\badmin\b`, en `\badmin\w*`),
     `retry` (it `\briprov\w*`, en `\bretry\b`), `pull_request`
     (`\bpull request\w*`). Il daemon di traduzione coprirà lingue future.
   - `scripts/certify_tutor_f2.py`: nelle alternative di `must_cover`, voce
     `"lex:<concept>"` → `detection_lexicon.match(concept, text)` (import da
     runtime; lo script già estende sys.path per `tutor.*`). Le voci literal
     restano identiche. NB: i gate «Fermati se»/“Stop if” restano LITERAL di
     proposito (fedeltà dell'intestazione).
   - Corpus `tests/runtime/tutor/data/f2_human_certification.json`: sostituire
     SOLO le alternative fallite per flessione con `"lex:tutor_gate.<x>"`
     (github#4/#5, photos#1/#2/#3/#5, scheduled#1/#4, overview#1/#2,
     changes-actions `riprova`, changes-restricted/admin-restricted
     `amministrator`).
2. **overview#4 EN «in practice»**: flag per-query `known_equal_fail: true`
   nel corpus + skip esplicito nel certificatore con nota «eccezione
   documentata (ADR 0198: pari-fallimento F1 misurato, card non selezionata)».
3. **(c) photos — SERVE LA DECISIONE DI ROBERTO** (§8). Se c1: flag per-set
   `curated_card: true` su f1-photos + il certificatore in `--f2-only` lo
   salta con nota (la card `fotografie-dominio` resta pubblicata e serve
   quelle query); se c2: ridimensionare i `must_cover` photos alle domande.
4. Ri-run gruppi toccati (f1_equivalence, admin_ui) SEQUENZIALE a macchina
   scarica + aggiornare i numeri in ADR 0198.

## 6. PASSO 2 — il correttore di bozze deterministico (atteso ~115-122)

Design ratificato da Roberto («passo 2, ora o prossima sessione»):
- **Dove**: `runtime/tutor/service.py`, subito dopo la composizione.
- **Cosa**: il ledger è GIÀ una checklist; verifica MECCANICA (§7.9, zero
  LLM) che la risposta contenga ogni voce: route/percorso per le superfici in
  `surfaces=`, aree/provider dell'inventario, finalità `tools=` (match a
  radice: riusare `detection_lexicon.match_any` o i medesimi stem dei gate —
  NON re-inventare un matcher). Se mancano voci → UNA sola ricomposizione con
  l'elenco esplicito dei buchi appeso al contesto («integra questi punti
  mancanti: …»), poi si consegna comunque (cap onesto, niente loop).
- **Telemetria**: contare `tutor_repair_pass` (0|1) e le voci mancanti nel
  record turno — servirà a misurare il guadagno reale e a decidere il Passo 4.
- **Costo**: +1 chiamata `wise` SOLO sui turni che falliscono la rilettura
  (~1/3); +8-15 s su quelli.
- **Bersagli attesi**: famiglia route-assente (admin_ui/conversation ~8),
  concetti singoli persi (~8), `from_step` leak (aggiungere al check il
  divieto dei marker interni: se compaiono → ricomposizione), «Stop if».
- Validazione: pytest tutor + ri-certificazione COMPLETA sequenziale + 1
  turno reale per canale.

## 7. PASSI 3-4 e riduzione F1 (progettati, non iniziati)

- **Passo 3 [PRIORITÀ ALTA — todo esplicito di Roberto 24/7]**: prompt del
  mode (fast) — imperativi di VISUALIZZAZIONE
  («Mostrami cosa contiene…») = EXPLAIN. Regola ferrea: dopo il ritocco,
  `boundary` DEVE restare 12/12, altrimenti revert. Casi bersaglio:
  admin-services-content (fallthrough), ops-calendar-events e
  ops-github-issues (clarification), conversation-pronoun-independence#2.
- **Passo 4** (decisione di spesa di Roberto): modello più capace sul binding
  `wise` (`runtime/llm_router.py::DEFAULT_TIERS`, ADR 0146). Zero codice.
- **Riduzione F1**: R1 congelamento (di fatto attivo) → R2 ritiro card
  informative (5 o 6 a seconda di (c)): spostarle fuori da
  `tutor/cards/published/`, rebuild, ri-run f1_equivalence SENZA `--f2-only`
  (deve coincidere col run f2-only), 1 turno reale per dominio → R3
  demolizione codice F1 morto in commit separato: `runtime/tutor/render.py`
  (56 righe, fallback EN non-i18n flaggati), ramo card-catalogo di
  `service._catalog_summary` (~110), `ui_map` F1 + test relativi (~230 righe
  totali); rinominare concettualmente il gruppo `f1_equivalence` in suite di
  regressione delle panoramiche.

## 8. DECISIONI IN ATTESA DI ROBERTO

1. **(c) photos**: c1 = tenere `fotografie-dominio` come procedura curata
   (RACCOMANDATA, prevista da RM-0003 §5.6) oppure c2 = ricalibrare il corpus.
2. **(d) «monouso» devices**: il registro ora dice «link di accoppiamento
   monouso» / «token temporaneo monouso» (fattualmente vero). Se lo giudica
   testo-verso-test: revert di 2 stringhe in `ui_surfaces.py` + refresh
   `structure_sha` NON necessario (le stringhe sono controlli, non `<th>`).
3. **1 messaggio Telegram al tutor** (es. «cosa sai fare?») per il gate 7 —
   unico gate di canale mancante; verificare poi il record
   `channel=telegram, mode=tutor` nel JSONL turni.
4. **Ok al COMMIT** — NIENTE è committato. Proposta modulare (senza trailer
   Co-Authored-By; chiedere prima di eseguire): (1) tutor retrieval+ledger+
   prompt v8 + ui_surfaces (audience+guardia+monouso) + test; (2) form
   credenziali ADR 0199 (runtime/credentials.py, executor+manifest+sig ×4 —
   ATTENZIONE: la skill github sta FUORI repo, in
   `~/.local/share/metnos/executors/...`, il suo manifest+sig non si
   committa qui); (3) corpus+certificatore (ratifiche a+b, poi stems);
   (4) doc: ADR 0198/0199, RM-0003, mutabile, seed i18n + guard test;
   (5) harness: `scripts/analyze_tutor_multidomain.py` + hook explain.
   `runtime/tutor/` e `runtime/published_docs.py` sono UNTRACKED (modulo
   nuovo intero).
5. **Esenzione `assert_no_secrets_in_return`** (opzione UI {value,label}):
   ratifica esplicita del ritocco all'invariante.

## 9. Fuori scope registrati (memorie)

- `project-http-524-error-ui`: la chat mostra HTML grezzo su 524 — trap +
  messaggio i18n (frontend).
- Lingua risposta = istanza (`current_lang()`), non query: EN→IT coi
  contenuti giusti; personalizzazione per-utente = W2.
- Bundle skill github: riportare `[credential_form]` nel sorgente della skill.
- Conformità marginale ~18 casi: tetto stocastico del Qwen 35B locale — il
  Passo 2 è la cintura, il Passo 4 la cura.

## 10. Alla ripresa, in ordine

1. Leggere questo file + ADR 0199 + addenda ADR 0198.
2. Chiedere/ricevere (c); applicare Passo 1 residuo (§5) → ri-run 2 gruppi.
3. Implementare Passo 2 (§6) → ri-certificazione completa sequenziale.
4. Se verde e Roberto d'accordo: R2 ritiro card (§7) + messaggio Telegram +
   commit modulari (§8.4) + aggiornare RM-0003/ADR con i numeri finali.
