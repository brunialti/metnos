---
id: 0081
title: Web crawler multi-tier — find_urls + read_urls_html + read_urls_pdf
date: 2026-05-04
status: accepted
area: executor, network, capability
related:
  - 0012  # dialog manager three-line authorization card
  - 0016  # open-source self-hosted default
  - 0042  # capability + sandboxing
  - 0082  # credentials encrypted storage (companion ADR)
---

## Context

Caso d'uso target di Roberto: **ricerca quotidiana ricorrente su sito
autenticato** (registro elettronico scolastico, online banking, news
redazionali) con flow «login → scoperta pagine nuove → estrazione
contenuto → riassunto Telegram». Cluster richieste analogo: monitorare
un blog/news per topic, scaricare circolari PDF dal sito di una scuola,
seguire delle issue di un progetto open source su un mirror non-API.

Stato pre-sessione: l'unico tool web era `get_urls` — HTTP GET single
URL, niente discovery, niente cookie session, niente crawling. Mancavano:
1. **Discovery**: come scoprire le pagine «nuove» di un sito senza che
   l'utente le indichi una ad una?
2. **Auth**: come passare cookie/sessione mantenuti dietro login form?
3. **PDF**: come estrarre testo da PDF servito da URL?
4. **Tier-policy**: come distinguere un crawl massiccio sul proprio
   blog (lecito, no robots) da uno aggressivo su un sito terzo?
5. **Anti-LLM**: l'estrazione contenuto principale (readability) deve
   essere **deterministica** — usare LLM per parsare HTML viola §7.9.

Vincolo CLAUDE.md §7.9 (4/5/2026): codice deterministico > LLM se
equipotente. Per discovery (sitemap/RSS), parsing HTML (readability),
ranking topic (BM25), tutto deve essere codice puro.

Vincolo §2.2: `find` come producer pattern-based, `read` come producer
identifier-based. `urls` oggetto canonico esistente. Niente nuovi verbi.

## Decision

Tre executor handcrafted nuovi, vocabolario chiuso, tier-policy
deterministica:

### 1. `find_urls` (~600 LOC)

Crawler BFS multi-strategia. Discovery in **3 fasi ibride**, in ordine:

| fase | strategia | costo | quando |
|------|-----------|-------|--------|
| 1 | sitemap.xml + sitemap-index | 1-N XML fetch | sito ben configurato |
| 2 | RSS/Atom via `<link rel=alternate>` | 1 HTML + 1 RSS | blog/news |
| 3 | BFS HTML su `<a href>` interni | N HTML | sempre come backup |

Le tre strategie convergono in un dict `results: {url → entry}`,
deduplicato per URL. Sitemap e RSS popolano metadata (lastmod, title,
snippet) senza fetch HTML separato.

**Tier resolution** deterministica:

| tier | source | rate floor | max_pages | robots | UA override |
|------|--------|-----------|-----------|--------|-------------|
| 1 default | catch-all | 200 ms | 50 | rispetta | no |
| 2 trusted | `~/.config/metnos/trusted_origins.json` | 200 ms | 2000 | rispetta | no |
| 3 owned | `~/.config/metnos/owned_domains.json` | 50 ms | 50000 | ignora | n/a |

`mode='research'` → `max_depth >= 4` (richiede capability
`crawl.recursive`); `mode='archive'` → `max_depth >= 6`,
`time_window='all'` forzato (archivi storici sono fuori window-restricted).

**User-Agent fisso**: `metnos-crawler/1.1 (+contact@metnos.com)`. Niente
override (motivo: identificabilita' nei log dei siti terzi, contatto
incluso).

**Topic ranking**: BM25 su `title + snippet`, no LLM. Bonus +0.5 se la
keyword appare nel `URL.path`. Senza topic, ordinamento per `lastmod`
desc (deterministic anche su corpus eterogeneo). **Topic e' RANKING,
NON filtering** (chiarimento 8/5/2026): le entries con score=0 restano
nel risultato in fondo alla lista — il caller (PLANNER o pipeline) puo'
capparle via `top_k` o filtrarle esplicitamente passando `min_score:
float`. La precedente politica "drop score=0 in topic mode" e' stata
rimossa perche' rompeva pipeline test compositive che attendevano la
visibilita' completa del BFS.

**Filtri**: `path_include`/`path_exclude` (regex/glob, default exclude
`['/login','/logout','/feed','/tag/','/search','#']`); `same_origin_only`
(default true); `time_window` (`today` / `last-24h` / `last-7d` /
`last-Nd` / `all`).

### 2. `read_urls_html` (~250 LOC)

Vettoriale (lista URL → lista entries). Per ogni URL: GET con UA fisso,
follow redirect (default stdlib ≤ 10 hop, di fatto ≤ 3 sulla maggior
parte dei siti), strip `<script>/<style>/<nav>/<header>/<footer>/`
`<aside>/<form>/<noscript>/<svg>/<iframe>`, scelta container in ordine
`<article>` > `<main>` > `<body>` (readability-lite). Estrae meta:
`og:*`, `description`, `author`, `<time datetime>`. `body_text`
trimmato a 50 KB. **Skip** Content-Type non `text/html`. Cookie via
`auth_cookies_file` (Mozilla cookies.txt).

### 3. `read_urls_pdf` (~200 LOC)

Vettoriale. Backend: `pypdf` (preferito) o `pdfminer.six` come fallback.
Cap pratico 20 MB / documento, 100 pagine, body_text 200 KB. Hook
`ocr_fallback=true`: se il parser estrae 0 testo, marca l'entry con
`needs_ocr: true` per orchestrazione successiva via `read_files_ocr`
(OCR non implementato qui — eccede il budget MVP, vedi §future).

### 4. Companion: cookie chain

`login_session` (ADR 0082) produce cookies.txt; `find_urls`,
`read_urls_html`, `read_urls_pdf` ricevono `auth_cookies_file=` come
arg. Pipeline canonical:

```
login_session(domain="X")           # produce ~/.config/metnos/cookies/X.txt
  ↓
find_urls(seed_urls=[...],
          auth_cookies_file="~/.config/metnos/cookies/X.txt",
          topic="circolari", time_window="last-7d")
  ↓
filter_entries(content_type ~= "html") + filter_entries(content_type ~= "pdf")
  ↓                                    ↓
read_urls_html                       read_urls_pdf
  ↓                                    ↓
group_entries (merge per url)
  ↓
describe_entries (LLM summary, l'unico LLM call della pipeline)
```

### 5. Hardcoded floors

Un manifest TOML è un prompt LLM (CLAUDE.md §2.5): se i floor fossero
parametri configurabili, la prima query "veloce, niente rate limit"
porterebbe il PLANNER a override. Floor sono nel codice Python:
`_RATE_FLOOR_DEFAULT_MS = 200`, `_RATE_FLOOR_OWNED_MS = 50`,
`USER_AGENT = "metnos-crawler/1.1 (...)"`. Tier 3 owned ottiene 50 ms
solo perche' l'utente dichiara possesso; non e' override LLM-driven.

## Alternatives considered

- **Reusare `get_urls` allargandolo**: violerebbe il principio di
  granularita' (CLAUDE.md §2.1). `get_urls` resta per HTTP GET single
  URL identificato; `find_urls` è un altro concetto (scoperta).
- **Browser headless (Playwright/Selenium) per JS-rendered**: scartato
  per ora. Aggiunge ~300 MB di runtime, complessita' sandbox, latenza
  3-5×. Lasciato come §future quando emergeranno casi reali (SPA-only
  registri scolastici).
- **Self-host SearXNG come unico crawler**: SearXNG è ottimo per **search
  motori di ricerca**, non per crawl-on-site di domini specifici.
  Complementare, non sostitutivo.
- **LLM per readability extraction**: scartato (§7.9). HTML readability
  con article/main/body è equipotente a un LLM extract per il 90% dei
  casi, deterministico, niente latenza, niente token. Per il 10% di
  pagine «strane» (custom div hell) si degrada a `<body>` intero — il
  trim a 50 KB limita l'esplosione comunque.
- **Single executor `crawl_site`**: scartato. Discovery + read sono due
  intent diversi: l'utente puo' volere solo «scopri quali URL ci sono»
  (find_urls) senza scaricarli tutti, e puo' avere gia' una lista
  (read_urls_html da `from_step` o letterale). La separazione rispetta
  §7.9 e l'ortogonalita' producer-consumer.

## Consequences

- **8 nuovi executor signed nel pool seed**: `find_urls`,
  `read_urls_html`, `read_urls_pdf`, `login_session` (ADR 0082),
  `group_entries`. Pool seed attuale: ~40 → ~45 dopo questo batch.
- **Capability nuova**: `crawl.recursive` (per `mode='research'/
  'archive'`). Vaglio puo' chiedere conferma una tantum, salvare la
  concessione standing in `trusted_origins.json` (manuale o via callback
  dialog manager).
- **2 file di config nuovi**: `~/.config/metnos/owned_domains.json` e
  `trusted_origins.json` (mode 0600). Vuoti di default; popolazione
  manuale o via vaglio.
- **Test**: 8 test mock-server per `find_urls`, 5 per `read_urls_html`,
  3 per `read_urls_pdf`. Mock server in test (`http.server.
  ThreadingHTTPServer`) — niente network reale nei test.
- **Pipeline integrata** validata su mock: discovery → fetch HTML/PDF →
  group → describe. La `describe_entries` (LLM) e' l'unico hop LLM
  dell'intera catena.
- **Non implementato (work item)**: OCR su PDF scansionati (hook ma
  no impl), browser headless per JS-rendered, MFA su login, refresh
  automatico cookie scaduti (oggi: utente rilancia login_session
  manualmente con force=true).

## References

- `executors/find_urls/` (manifest + code + sig).
- `executors/read_urls_html/` (manifest + code + sig).
- `executors/read_urls_pdf/` (manifest + code + sig).
- `executors/login_session/` (manifest + code + sig — ADR 0082).
- `executors/group_entries/` (manifest + code + sig).
- `tests/runtime/executors/test_find_urls.py` (8 test mock).
- `tests/runtime/executors/test_read_urls_html.py` (5 test).
- `tests/runtime/executors/test_read_urls_pdf.py` (3 test).
- `~/.config/metnos/{owned_domains,trusted_origins}.json` (config templates).
- ADR 0012 (UX di approval per tier-2 promote, futura integrazione vaglio).
- Memoria `mykleos_topology_and_security.md` (24/4/2026: 3 assi safety —
  libertà, identita', perimetro — il crawl è strettamente sull'asse
  perimetro robustezza).
