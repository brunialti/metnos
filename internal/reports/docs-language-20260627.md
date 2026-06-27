# Passata lingua doc metnos.com — 2026-06-27

Sessione notturna autonoma (agente Opus). Mandato: `~/.claude/projects/-opt-metnos/memory/project_docs_language_rewrite.md`.

## Esito
**Item 5 (architecture/ restanti) e item 6 (libretti) COMPLETI.** Non resta prosa da passare. Deploy a carico dello script cron, non dell'agente.

## Doc chiusi in questa passata (IT+EN, HTML validati, 0 leak modello/fattuali)
Da item 5: `skill_importer`, `skills_backends`, `mnest`, `mnestoma`/`mnestome`, `policy`, `http_api`, `observability` (+ vaglio, sandbox, channel, pairing, approval_ux, telos, multilang, executor già chiusi nelle sessioni 25-26/6).
Da item 6: `Metnos_QuickTour_v1`, `Metnos_Architettura_Intro_v1`/`Metnos_Architecture_Intro_v1`, `Metnos_Glossario_v1`/`Metnos_Glossary_v1`, `code.html`.

## Esempi di rese applicate
- Glossario IT: loop→ciclo, framing→inquadramento, consumer→consumatore, trace→traccia, trigger→innesco, gate→varco/controllo, gateway→servizio web, «tool call»→«chiamate ai tool».
- Glossario IT+EN voce `cascata`/`cascade`: rimosso leak `Qwen 3.6 35B-A3B` in prosa → «sul tier locale» / «on the local tier».
- QuickTour: planner→pianificatore (replace_all verificato non-codice), run→esecuzione, in-process→nel processo, bias→pregiudizio.
- Architettura_Intro: opt-in→facoltativo, dashboard→cruscotto, planner→pianificatore, cloud-first→prima il cloud, fallback→ripiego, screenshot→schermate.
- http_api: payload→corpo della risposta, scrub→rimossa, master→chiave maestra, step→passo, gate→varco/controllo, palette→tavolozza, Multi-user→Multi-utente.
- observability: Stats→Statistiche, top-N→primi N, dashboard→cruscotto, logga→registra, dump→estrazione, gateway→servizio web.
- code.html IT: dump→estrazione grezza, planner→pianificatore (registro nota-d'autore preservato).

## Termini TENUTI (non tradotti)
path, fastpath, autopath, executor, intent, framework, cache, embedding, tier, mnest, mnestoma, telos, vaglio, synt, praxis, scratchpad, skill, backend, sandbox, pairing, channel, ReAct, hash, GBNF, LRU, TTL; `model="...gguf"` dentro `<code>`; headword di glossario (self-enhancement bias, <untrusted>, trust-gating, trace, ExecutionTrace); nomi-test/enum/route; titolo «Where is the beef»; `run-essential`/`installer`/`checkout`/`AI coder` (registro nota-d'autore EN/IT); frontier = Opus 4.8 (nome fattuale voluto).

## Vincoli rispettati
Nessun git commit/merge/push; nessun restart servizio; toccati solo file sotto `docs/`; mai dialoghi/landing/Prospettive/virtualization.html. EN = solo fix leak/fattuali, non riscrittura prosa.

## Validazione
Ogni doc: `html.parser` ben formato (IT+EN) + grep `Qwen|Gemma|Engine v|mnest_01HW|/admin/proposals` = 0.
