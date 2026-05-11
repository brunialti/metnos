---
id: 0098
title: Web crawl — parallelizzazione + strategia + policy default T2
date: 2026-05-07
status: accepted
area: runtime, executor, planner, web
related:
  - 0081  # crawler tier policy (T1/T2/T3)
  - 0095  # output formatter (notice deterministico)
modifies:
  - 0081
complements:
  - 0081
---


## Context

Turn live `d612e0fecb8e402e` (7/5/2026 14:25): l'utente ha chiesto «Dimmi
i risultati delle partite del girone f nella giornata 22 ESPLORANDO IN
PROFONDITA' il sito https://www.federvolley.it/serie-b-maschile-calendario».

Esito: 31 minuti, final_kind=answer, ma il messaggio finale conteneva
solo «find_urls: completato (58 elementi). Protocollo... ; Serie B
Maschile - Calendario; ...» — titoli URL del sito, NON i risultati delle
partite richiesti.

Diagnosi:
1. **Strategia PLANNER errata**: l'utente ha fornito URL specifico +
   verbo discovery («esplorando»). PLANNER ha letto «esplora» come
   "fai BFS crawl del sito" → step 1 `find_urls`. Avrebbe dovuto
   leggere il contenuto della pagina con `read_urls_html(urls=[seed])`.
2. **Bottleneck I/O sequenziale**: `find_urls` (BFS) crawla 58 URL
   uno alla volta su `urllib.request.urlopen`. 8 minuti per round; 3
   round (loop PLANNER) = 27 min totali di executor.
3. **PLANNER loop**: vedendo che read_urls_html non rispondeva, ha
   rilanciato find_urls 2 volte di seguito → cap_same_executor →
   `auto_final_on_duplicate`. Questo ha preso `find_urls` come
   `last_productive` (era ok) e ha formato final_message con i SUOI
   campi (URL+titoli), NON con il `text` di `read_urls_html`.

Inoltre Roberto: «non capisco se ha trovato il risultato».

## Decision

Cinque cambiamenti complementari, ognuno indipendente, ognuno con
beneficio misurabile.

### 1. Parallelizzazione `find_urls` (c2.b)

`runtime/executors/find_urls/find_urls.py`:
- Nuovo `_HostThrottle`: thread-safe `Semaphore` per-host + lock per
  rate-limit minimo. Sostituisce `_rate_wait` racy con dict.
- BFS loop drena batch di URL non visitati e fetcha in parallelo via
  `concurrent.futures.ThreadPoolExecutor(max_workers=_global_inflight_max)`.
  Parsing dei risultati resta sequenziale (CPU trascurabile vs I/O).
- `_host_capacity()`: cap globale `min(64, cpu*4)` (banda fiber 2.5 Gbps
  permette ben oltre 32 conn). Configurabile via env
  `METNOS_FIND_URLS_GLOBAL_MAX`.
- Per-host (riveduti): T1=2, T2=8, T3=16.

Speedup atteso: 58 URL su federvolley.it (T2, K=8) → ~7 round paralleli
= 50-60s vs 8-11 min seriali. **~10× sul caso tipico**, non lineare con
K perche' rate_limit_ms resta minimo per host.

### 2. Policy default tier T2 (c modifica ADR 0081)

ADR 0081 originario: T1=default unknown, T2=trusted (manuale), T3=owned.

Modifica 7/5/2026 (Roberto): **default = T2**, retrocedi a T1 SOLO per
host esplicitamente in `~/.config/metnos/blocked_origins.json`. Razionale:
- Il crawler e' identificato (UA `metnos-crawler/1.2 +metnos@metnos.com`),
  non stealth. Aspettarsi blocco e' pessimismo eccessivo.
- Auto-degrade su 429/503 → host aggiunto a blocked_origins (TODO
  implementazione automatica; oggi e' manuale).
- `trusted_origins.json` mantiene la sua semantica come "alias garantito
  T2", ma non e' piu' obbligatorio per ottenere T2.

User-Agent aggiornato a `metnos-crawler/1.2 +metnos@metnos.com` (criteri
legittimita' Roberto: «supera con criteri di legittimita'»).

### 3. `read_urls_html` iframe-following + PDF-linked + JS detection (c2.a/b/c2.3)

`runtime/executors/read_urls_html/read_urls_html.py`:
- **iframe-following** (c2.1): parser cattura `<iframe src=...>` srcs.
  Se body_text < 200 char E iframe[0] e' same-host, fetcha l'iframe e
  usa il SUO body_text. Limitato a 1 follow per pagina (no chain),
  same-host only (legittimita': non sfuggire dal dominio voluto).
- **PDF-linked rilevante** (c2.2): parser estrae anchor `href=*.pdf`
  con `anchor_text` contenente keyword (`calendario, risultati,
  classifica, girone, giornata, convocazione, comunicato, regolamento,
  elenco`). Esposto come `linked_documents=[{href, anchor_text,
  relevance_score, kind}]` ordinato per rilevanza decrescente.
- **JS-rendering detection** (c2.3): heuristic `text/html ratio < 0.05`
  + presenza `<div id='root'>` SPA + `<noscript>` warning + script
  count >= 5. Se >= 2 signal → `js_rendered=true` + `notice` umano:
  «Pagina probabilmente render lato JS (SPA): il contenuto reale
  viene caricato dal browser dopo l'HTML iniziale. Metnos non esegue
  JavaScript, quindi puoi vedere solo lo scheletro». NON gestiamo
  JS-rendering (Playwright e' fuori scope, +500MB infra). **Solo
  rilevamento + notifica all'utente.**

### 4. PLANNER hint (Z) per URL esplicito (c1)

`runtime/prompts/it/planner.j2`: aggiunta sezione `(Z)` PRIMA di
`(A) DISCOVERY` e `(B) TARGETED SEARCH`. Regola:

```
(Z) URL ESPLICITO + RICHIESTA DI DATI DENTRO LA PAGINA:
DEVI: read_urls_html(urls=[<URL_utente>]) come PRIMO step.
NON DEVI: find_urls in primo step quando l'utente ha gia' indicato l'URL.
OK: "dimmi i risultati di girone F giornata 22 su <URL>" → read_urls_html.
ERRORE: stesso prompt → find_urls.  L'utente perde 30 minuti e ottiene
        58 URL inutili.
```

Distinzione pratica esplicita:
- URL specifico (path) → (Z) read_urls_html.
- Solo dominio + panoramica → (A) find_urls default.
- Solo dominio + dato specifico → (B) find_urls deep_search.

### 5. `auto_final_on_duplicate` prefer read over discovery (c3)

`runtime/agent_runtime.py::_resolve_auto_final_from_steps`:
quando `last_productive` e' `find_urls` (discovery) ma in history
c'e' un `read_urls_html`/`read_urls_pdf`/`get_urls_text` ok con
contenuto sostanziale (`text >= 200 char` OR `summary/detail_md/
final_message_hint >= 80 char`), preferisci il read come fonte di
final_message. Helper `_read_obs_has_content(obs)`.

Stesso pattern di stamattina con `describe_entries` (mio fix
auto_final_count): il discovery e' metadati, la lettura e' contenuto.

## Consequences

Positive:
- Speedup 10× su crawl tipici.
- Default T2 elimina frizione su nuovi host (criteri legittimita').
- iframe-following risolve la maggioranza dei casi "tabella in widget"
  (federvolley, sport sites, dashboard, ecc.).
- PDF-linked esposto al PLANNER → puo' chiamare read_urls_pdf su URL
  rilevanti senza re-discovery.
- JS-rendering rilevato e segnalato all'utente: niente piu' silent
  failure.
- PLANNER hint (Z) chiude il bug strategico federvolley alla radice.
- auto_final prefer read evita risposte vuote quando c'e' contenuto.

Open / future:
- Auto-degrade T2 → T1 su 429/503 ripetuti: oggi blocked_origins.json
  e' manuale. Implementazione automatica TODO.
- `read_urls_html` parallel multi-URL (oggi ancora seriale dentro a
  `invoke()`): meno urgente, l'uso tipico e' 1-3 URL.
- JS-rendering completo (Playwright sidecar) — non in roadmap immediata.

## Test
- `runtime/tests/test_find_urls.py`: 10/10 PASS (8 originali + 2 nuovi
  per meta-refresh: target follow + loop cap; vedi addendum 8/5/2026).
- `runtime/tests/test_read_urls_html.py`: 11/11 PASS (+6 nuovi:
  iframe captured, iframe auto-follow same-host, no follow cross-host,
  PDF linked relevance, JS detected SPA, static HTML not marked).
- `runtime/tests/test_auto_final_on_duplicate.py`: 22/22 PASS
  (+4 nuovi: prefer_read_when_discovery_last,
  keeps_discovery_when_no_content, summary_field_for_content,
  no_read_keeps_discovery).
- `runtime/tests/test_prompt_loader.py`: 14/14 PASS (planner.j2 edit OK).
- Full regression: 1059/1059 (8/5/2026 notte).
- Smoke invariants: 55/55 catalog OK.

## Addendum 8/5/2026 notte — meta-refresh follow

### Trigger

Turn live live (8/5/2026): query «cerca organico di diritto della scuola
provincia di Roma». Sito target `https://www.atpromaistruzione.it/`
(Aruba shared hosting). Risposta sul root `/` = 81 byte di
`<meta http-equiv="refresh" content="0;URL=/atp">`. urllib (e quindi
find_urls BFS) NON segue meta-refresh — solo HTTP 30x. Esito senza
fix: `entries=1, score=0, discovered_documents=0` (la "homepage" e'
solo un tag redirect, niente link interni).

### Fix

Helper `_extract_meta_refresh(html) -> str | None` con regex
`<meta\s+[^>]*http-equiv=["\']?refresh.*content=["\']?\d+\s*;\s*url=...`.
Cap a 16KB scan (meta-refresh sta sempre nel `<head>`, scansione di
body massicci e' insensata).

Wire in due punti deterministici:

1. **Pre-fetch dei seed** (fase RSS-discovery): per ogni seed, fino a 4
   hop a catena, segui meta-refresh (se rilevato) e usa il target finale
   come "effective seed" della BFS. Senza meta-refresh la fase resta
   identica (estrazione RSS link). Output: lista `effective_seeds`
   sostituisce `seed_urls` come radici BFS.

2. **BFS canonical**: durante il dequeue+fetch, se il body e' meta-refresh
   accoda il target a `depth` corrente (NON `depth+1`; e' un redirect non
   un follow di link), salta la registrazione in `results` (la pagina-
   redirect non e' interessante), incrementa contatore `meta_refresh_hops`
   per host. Cap 4 hop/host previene loop A→B→A patologici.

Pattern generico: si applica a QUALSIASI host con meta-refresh redirect
(WordPress dietro Aruba, IIS legacy, sistemi gestionali con landing).
Niente specializzazione per dominio.

### Verifica live

Convergence log `~/.local/share/metnos/convergence_log_organico.jsonl`:
- iter A (max_pages=200): 200 entries, 147 documents, target post page
  `decreto-...-organico-di-diritto-...` trovato a score 19.52 (top 5).
- iter B (max_pages=1000): 826 entries, 593 documents, target PDF
  `m_pi.AOOUSPRM.REGISTRO-UFFICIALEU.0031007.07-05-2026.pdf` trovato
  con score 0.0 ma exact match in `discovered_documents`. La pagina
  parent ha score 20.5 nelle entries.
- iter C/D: 0 entries — host_health auto-block attivato dopo 969 fail
  in iter B (Aruba proxy aggressive 429 rate-limit). ADR 0108 funziona
  come previsto: il cleanup TTL 24h ripristina T2.

### Test convergenza

`runtime/tests/test_find_urls.py`: 2 nuovi test (totale 10/10):
- `test_meta_refresh_followed_to_real_homepage`: body 81-byte meta-refresh
  → BFS atterra su `/atp/`, 5 link interni + 1 PDF discovered.
- `test_meta_refresh_loop_capped`: A→B→A loop cap rispettato (no
  divergence, ok_count <= max_pages).

### Files modificati

- `executors/find_urls/find_urls.py`: +50 LOC (helper +
  pre-fetch loop + BFS detect + signature).
- `runtime/tests/test_find_urls.py`: +75 LOC (2 nuovi test).
- `executors/find_urls/manifest.toml.sig`: re-firmato (§7.10 CLAUDE.md).
