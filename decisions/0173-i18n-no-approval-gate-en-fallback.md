---
id: 0173
title: i18n — niente gate di approvazione traduzioni + ripiego EN nel frattempo
date: 2026-06-15
status: accepted
area: runtime
related:
  - 0044  # intent extractor (prompt LLM per-lingua)
  - 0146  # tier LLM (il daemon traduce con il middle locale)
complements:
  - 0092  # multilang: file .j2 per-lingua + candidati _pending + lang_state
modifies:
  - 0092  # la review umana NON è più un gate bloccante (era "obbligatoria"):
          #   i candidati sono usati in-vivo; promozione = opt-in
---

## Context

ADR 0092 ha introdotto il bilinguismo: prompt `.j2` per-lingua, descrizioni
manifest a tabella `[description].<lang>`, messaggi nel DB i18n, e un daemon
notturno (`i18n_translator.py`) che traduce con il modello locale. Il flusso 0092
scriveva le traduzioni dei prompt come **candidati** in
`prompts/<lang>/_pending/<role>.j2.candidate` e richiedeva una **promozione
manuale** (`admin.prompts_cli mark-synced`) prima che il runtime le usasse — «mai
diretto in `<role>.j2`, review umana obbligatoria» (commento del daemon).

Due problemi pratici, sollevati da Roberto (15/6):

1. **La revisione è un gate inutile.** Nessuno controlla a mano centinaia di
   stringhe (le descrizioni manifest sono una per executor × decine di executor;
   i messaggi DB sono centinaia). Il gate blocca l'attivazione di una lingua senza
   aggiungere qualità reale.

2. **Nessun default nel frattempo, anzi: crash.** Verifica sul codice (15/6):
   - L1 prompt (`prompt_loader.get`/`_env_for`): una lingua senza i suoi `.j2`
     non aveva ripiego → `RuntimeError`/errore template. Aggiungere `fr` creava
     `prompts/fr/` vuota e i candidati in `_pending/`, ma finché non promossi a
     mano il planner **andava in crash** sulla lingua nuova.
   - L2 descrizioni (`loader._resolve_lang_text`): ripiego alla «prima lingua in
     ordine alfabetico», non a EN esplicito.
   - L3 messaggi (`i18n.get`): già `FALLBACK_CHAIN=("en","it")` e
     `needs_translation` trattato come HINT, non come gate. Corretto già così.

Decisione di Roberto: togliere il gate di approvazione e fissare il **default nel
frattempo = EN**, uniforme sui tre livelli.

## Decision

Una sola catena di risoluzione DETERMINISTICA (§7.9), applicata a tutti i livelli:
**live → candidato `_pending` (usato in-vivo) → EN**.

- **L1 prompt** (`runtime/prompt_loader.py`): nuovo `_resolve_prompt_source(root,
  en_root, name)` usato dal loader di `_env_for` (quindi da `get`, `get_split`,
  `compose`, include e sezioni — un punto solo, §7.3): per ogni file prova il live
  della lingua, poi il candidato `_pending/<name>.candidate` (così
  l'approvazione manuale NON serve più), poi EN (live e candidato). `_env_for` non
  fa più crashare su lingua mancante: solleva solo se nemmeno EN esiste. Il
  planner sezionato usa la **lingua effettiva** (`_effective_planner_lang`): se la
  lingua non ha il `_core` (live o candidato), l'intero planner usa EN — niente
  mix _core/sezioni di lingue diverse; `list_planner_sections` enumera anche i
  candidati.
- **L2 descrizioni** (`runtime/loader.py::_resolve_lang_text`): ripiego **EN
  esplicito** prima dell'ordine alfabetico.
- **L3 messaggi**: invariato (era già EN-first, hint non gate).

La **promozione** del candidato a `prompts/<lang>/<role>.j2` resta possibile ma
diventa **opt-in**: canonicalizza la traduzione e riattiva il linter prescrittivo
(§6.1) sul file promosso. Non è più un prerequisito perché la traduzione abbia
effetto.

Conseguenza per l'utente: scelta la lingua all'installazione (ADR install:
`METNOS_LANG`), il daemon notturno traduce e le traduzioni sono **operative
appena scritte**; finché una stringa non c'è, l'assistente risponde in **inglese**
invece di rompersi. IT/EN (uniche lingue validate oggi) hanno sempre i file live →
vince il passo 1 → **comportamento invariato**, rischio zero (verificato: suite
2667/0, due turni reali IT intatti).

## Alternatives considered

- **Mantenere la review obbligatoria** (0092). Onesto in teoria, inutile in
  pratica: nessuno revisiona centinaia di stringhe, e blocca le lingue nuove.
- **Il daemon scrive DIRETTO sul live** (`prompts/<lang>/<role>.j2`). Più
  invasivo: perde la traccia/audit del candidato e rischia di sovrascrivere file
  hand-authored. La catena al consumo (candidato usato in-vivo) ottiene lo stesso
  effetto senza toccare i file canonici.
- **Ripiego su IT invece che EN.** IT è la lingua sorgente di default, ma EN è la
  seconda lingua validata e la lingua franca: per una lingua nuova (fr/de) ricadere
  su EN è più utile che su IT. Allineato a `i18n.FALLBACK_CHAIN`.

## Consequences

- Una lingua nuova è operativa appena il daemon ha scritto i candidati; nel
  frattempo EN, mai crash. Attivazione lingua = sola scelta install + riavvio (la
  promozione manuale non è più sul percorso critico).
- I candidati `_pending` di IT/EN restano ignorati (i live vincono): nessun
  effetto sulle due lingue validate.
- Il linter prescrittivo (§6.1) gira solo sui file live: i candidati auto-usati
  non sono lintati — accettabile per lingue non ancora validate; la promozione
  opt-in li riporta sotto linter.
- Lavoro residuo: aggiornare la doc `docs/*/architecture/multilang.html` (il
  flusso «review obbligatoria» è superato) e il claim del post di presentazione
  (già aggiornato). Guard: `tests/runtime/infra/test_prompt_loader.py`
  (`TestKFallbackAndAutoPromote`, fallback EN) + `test_loader_description_lang.py`
  (`TestResolveLangTextEnFallback`).
