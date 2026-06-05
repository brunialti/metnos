#!/usr/bin/env python3
"""find_urls — crawler BFS multi-tier per scoperta URL su seed (ADR 0081).

Tre strategie di discovery ibride, in ordine di preferenza:
    1. sitemap.xml (e sitemap-index ricorsivo) → estrae URL + lastmod.
    2. RSS/Atom feed via <link rel="alternate" type="application/rss+xml">.
    3. BFS HTML: estrae <a href> interni e li accoda.

Filtri deterministici post-discovery:
    - time_window su lastmod/pubdate (today, last-24h, last-7d, all).
    - topic via BM25 su title+snippet (no LLM): score = somma idf*tf
      normalizzata per lunghezza, bonus se la keyword appare nel path.
    - path_include / path_exclude (regex Python) su URL.path.
    - same_origin_only (default true): scarta link esterni al seed.

Tier resolution (deterministico):
    - tier 3 owned: domain in `~/.config/metnos/owned_domains.json` →
      no robots, rate min 50 ms, max_pages illimitato (cap pratico 50000).
    - tier 2 trusted: domain in `~/.config/metnos/trusted_origins.json` →
      respect_robots ma override possibile, rate min 200 ms, max_pages 2000.
    - tier 1 default: respect_robots, rate min 500 ms, max_pages 50.
    - mode="research"/"archive" require capability `crawl.recursive`.

User-Agent fisso: `metnos-crawler/1.1 (+contact@metnos.com)`.

Cookie: se `auth_cookies_file` puntato a un file Mozilla cookies.txt,
viene caricato con http.cookiejar.MozillaCookieJar e iniettato in ogni
Request via opener.

Output:
    {
      ok, ok_count, fail_count,
      entries=[{url, title, snippet, score, depth, content_type,
                fetched_at, lastmod}],
      truncated, available_total, used,
      cap_field='max_pages', cap_value,
      discovery_strategy in {sitemap, rss, bfs, mixed},
      robots_skipped=[...]
    }
"""
from __future__ import annotations

import collections
import fnmatch
import http.cookiejar
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET


USER_AGENT = "metnos-crawler/1.2 (+metnos@metnos.com)"

# Floor hardcoded: nessun mode/tier puo' scendere sotto 200 ms in tier
# default. Tier 3 owned puo' scendere a 50 ms (vedi _tier_floor_ms).
_RATE_FLOOR_DEFAULT_MS = 200
_RATE_FLOOR_OWNED_MS = 50

# Cap default per tier. Tier 1 default = 50; tier 2 trusted = 2000;
# tier 3 owned = 50000 (effettivamente "illimitato" entro il pratico).
# Cap di pagine per tier (S2, 6/5/2026):
#  - _TIER_DEFAULT: quando l'utente NON passa `max_pages` esplicito.
#    Tier 1 alzato da 50 a 1000 per permettere ricerche ricorsive utili
#    (siti scolastici, blog grossi: 3000+ URL nella sitemap).
#  - _TIER_CAPS: cap superiore quando l'utente passa esplicitamente
#    `max_pages` (es. dopo cap-expand offerta dal runtime). Permette
#    crescita graziosa 1000 → 5000 senza scavalcare il tier polite.
# Tier 3 (owned domains) resta illimitato in pratica. Rate limit 500ms
# tier 1 → 1000 pagine costano ~8 min nel caso peggiore.
_TIER_DEFAULT = {1: 1000, 2: 2000, 3: 50000}
_TIER_CAPS    = {1: 5000, 2: 10000, 3: 50000}

_DEFAULT_PATH_EXCLUDE = ["/login", "/logout", "/feed", "/tag/", "/search", "#"]

CONFIG_DIR = Path.home() / ".config" / "metnos"
OWNED_FILE = CONFIG_DIR / "owned_domains.json"
TRUSTED_FILE = CONFIG_DIR / "trusted_origins.json"
# Policy 7/5/2026: default tier = 2 (parti generoso, retrocedi solo su
# host problematici). `blocked_origins.json` lista host forzati a T1
# (rate piu' stretto, max_pages 50). Auto-popolato dal runtime quando
# un host risponde 429/503 ripetuti, oppure aggiunto a mano.
BLOCKED_FILE = CONFIG_DIR / "blocked_origins.json"

# SearXNG seed-discoverer (ADR 0115, 8/5/2026 sera). Quando l'utente
# chiede "cerca <topic>" senza fornire URL espliciti, find_urls puo'
# interrogare SearXNG locale (multi-backend aggregator self-hosted, §10.3)
# per ottenere automaticamente i seed_urls top-N. Backend default: porta
# 8888 di localhost; override via env METNOS_SEARXNG_URL.
SEARXNG_URL_DEFAULT = "http://localhost:8888"
# Profilo batch+interattivo: 3s tagliava i motori lenti-ma-buoni → pool
# candidati parziale/ballerino che AFFAMA il rerank wide_n (sotto). 12s (≤
# max_request_timeout istanza) lascia completare l'aggregazione. Tuning via env
# senza re-sign (report searxng 4/6 §5.A.2/§5.A.4; §7.11 config-hierarchy).
SEARXNG_TIMEOUT_S = float(os.environ.get("METNOS_SEARXNG_TIMEOUT_S", "12.0"))
# Budget di tempo del rerank LLM: oltre questo, fallback all'ordine SearXNG.
# Senza budget, sotto contesa GPU col planner la chat si appende e
# l'executor va in timeout (bug ARK/people-search). Override via env.
_RERANK_TIMEOUT_S = float(os.environ.get("METNOS_FINDURLS_RERANK_TIMEOUT_S", "8.0"))
# Risultati FINALI (seed per BFS). NB: il rerank NON è affamato da questo — vede
# `wide_n` candidati (METNOS_FIND_URLS_RERANK_WIDE, default 30) e ne promuove i
# migliori top_n (recupera i rank 6-15: report §7 crit.1 già soddisfatto). Default
# 5 invariato; env-override per tuning senza re-sign (report §5.A.4).
SEARXNG_TOP_N = int(os.environ.get("METNOS_SEARXNG_TOP_N", "5"))


# ─── Parsing HTML semplice ──────────────────────────────────────────────

_DOC_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".odt", ".ods",
    ".rtf", ".txt", ".zip", ".tar", ".tar.gz", ".tgz",
)


# Pattern <meta http-equiv="refresh" content="<delay>;URL=<target>"> per
# rilevare meta-refresh (es. homepage Aruba con landing → /sub/). urllib
# non lo segue automaticamente (lo fa solo per HTTP 30x), ma e' un pattern
# legittimo del web (WordPress, sistemi gestionali) e va trattato come una
# redirect 301: il target viene aggiunto come seed nuovo e il body
# meta-refresh sostituito (zero entry "vuote" nel risultato).
# Permissivo: tollera virgolette singole/doppie, spazi, casing.
_META_REFRESH_RE = re.compile(
    r'<meta\s+[^>]*http-equiv\s*=\s*["\']?refresh["\']?'
    r'[^>]*content\s*=\s*["\']?\s*\d+\s*;\s*url\s*=\s*([^"\'>\s]+)',
    re.IGNORECASE,
)


_SEARCH_ENGINE_HOSTS = {
    "google.com", "google.it", "google.fr", "google.de", "google.es",
    "google.co.uk", "google.ch", "google.at", "google.nl", "google.be",
    "www.google.com", "www.google.it", "www.google.fr", "www.google.de",
    "www.google.es", "www.google.co.uk", "www.google.ch", "www.google.at",
    "bing.com", "www.bing.com",
    "duckduckgo.com", "www.duckduckgo.com",
    "search.brave.com", "brave.com",
    "yandex.com", "yandex.ru",
    "yahoo.com", "search.yahoo.com",
    "ecosia.org", "www.ecosia.org",
    "qwant.com", "www.qwant.com",
    "startpage.com", "www.startpage.com",
}


def _is_search_engine_home(url: str) -> bool:
    """True se l'URL e' la home di un motore di ricerca pubblico noto.

    Usato per droppare seed_urls inutili quando search_query e' fornita:
    il PLANNER spesso passa `seed_urls=["https://www.google.it"]` come
    "guida alla ricerca", ma il BFS che ne deriva crawla privacy/terms/
    help del motore stesso, drogando i veri risultati SearXNG.
    """
    try:
        p = urllib.parse.urlparse(url)
        host = (p.hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    if host in _SEARCH_ENGINE_HOSTS:
        # Path "/" o vuoto → home; path piu' specifici (es. una vera URL
        # google con path informativo) non vengono droppati.
        path = (p.path or "").rstrip("/")
        return path in ("", "/")
    return False


def _extract_meta_refresh(html: str) -> str | None:
    """Estrai l'URL di destinazione di un eventuale `<meta http-equiv=
    "refresh" content="N;URL=...">`.

    Ritorna l'URL (stringa) o None. Generico: si applica a QUALSIASI host
    (Aruba, IIS legacy, WordPress redirect plugin, ecc.). Non specializza
    per dominio particolare.
    """
    if not html or len(html) > 4096 * 4:
        # meta-refresh sta sempre nei primi KB del <head>; cap a 16KB
        # evita scansione di body massicci se per errore si chiama questa
        # funzione su pagine non-redirect.
        html = (html or "")[:16384]
    m = _META_REFRESH_RE.search(html)
    if not m:
        return None
    target = m.group(1).strip()
    if not target:
        return None
    return target


def _doc_ext_for(href: str) -> str | None:
    """Ritorna l'estensione documentale (incluso il punto) se l'href la
    contiene, altrimenti None. Case-insensitive, ignora query/fragment."""
    if not href:
        return None
    low = href.lower().split("?", 1)[0].split("#", 1)[0]
    for ext in _DOC_EXTENSIONS:
        if low.endswith(ext):
            return ext
    return None


class _LinkExtractor(HTMLParser):
    """Estrae <a href>, <title>, meta description e link a documenti
    (PDF, DOCX, XLSX, CSV, ZIP, ...) con il testo dell'anchor come
    pseudo-titolo per il ranking BM25.

    Niente DOM completo: segue la stessa convenzione lightweight di
    runtime/html_sanitizer.py (stdlib only).
    """
    def __init__(self):
        super().__init__()
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self.in_title = False
        self.description: str = ""
        self.snippet_parts: list[str] = []
        # rss feed url se presente come <link rel="alternate" type="application/rss+xml">
        self.rss_links: list[str] = []
        self._depth_skip = 0  # counter per <script>/<style>: ignora il testo
        # Document discovery (S1, 6/5/2026): link a PDF/DOCX/XLSX/...
        # Catturiamo href + anchor_text (testo fra <a> e </a>) per usarlo
        # come pseudo-title nel ranking BM25 (PDF non hanno title estraibile
        # dal body senza fetch ad-hoc).
        self.documents: list[dict] = []
        self._cur_doc: dict | None = None  # {href, ext, anchor_buf: list[str]}

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "a":
            href = attrs_d.get("href")
            if href:
                self.links.append(href)
                ext = _doc_ext_for(href)
                if ext is not None:
                    self._cur_doc = {"href": href, "ext": ext, "anchor_buf": []}
        elif tag == "title":
            self.in_title = True
        elif tag == "meta":
            name = (attrs_d.get("name") or "").lower()
            if name == "description":
                self.description = (attrs_d.get("content") or "").strip()
        elif tag == "link":
            rel = (attrs_d.get("rel") or "").lower()
            mime = (attrs_d.get("type") or "").lower()
            if "alternate" in rel and "rss" in mime or "atom" in mime:
                href = attrs_d.get("href")
                if href:
                    self.rss_links.append(href)
        elif tag in ("script", "style"):
            self._depth_skip += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        elif tag == "a" and self._cur_doc is not None:
            anchor = " ".join(self._cur_doc["anchor_buf"]).strip()[:200]
            self.documents.append({
                "href": self._cur_doc["href"],
                "ext": self._cur_doc["ext"],
                "anchor_text": anchor,
            })
            self._cur_doc = None
        elif tag in ("script", "style") and self._depth_skip > 0:
            self._depth_skip -= 1

    def handle_data(self, data):
        if self._depth_skip:
            return
        if self.in_title:
            self.title_parts.append(data)
        else:
            txt = data.strip()
            if txt and len(self.snippet_parts) < 30:
                self.snippet_parts.append(txt)
            # Accumula testo dell'anchor del link documentale corrente.
            if txt and self._cur_doc is not None:
                if len(self._cur_doc["anchor_buf"]) < 8:
                    self._cur_doc["anchor_buf"].append(txt)

    @property
    def title(self) -> str:
        return " ".join(self.title_parts).strip()

    @property
    def snippet(self) -> str:
        if self.description:
            return self.description[:300]
        return " ".join(self.snippet_parts)[:300]


# ─── Tier resolution ────────────────────────────────────────────────────

def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _domain_in(host: str, items: list[str]) -> bool:
    """Match esatto o suffix: 'metnos.com' matcha 'sub.metnos.com'."""
    if not host:
        return False
    host = host.lower()
    for item in items or []:
        item = item.lower().lstrip(".")
        if host == item or host.endswith("." + item):
            return True
    return False


def _is_loopback_host(host: str) -> bool:
    """Loopback/local host (no rate limit applicabile)."""
    if not host:
        return False
    h = host.lower().split(":", 1)[0]
    if h in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    if h.endswith(".local") or h.endswith(".localhost"):
        return True
    return False


def _resolve_tier(host: str, trust: str) -> int:
    """Ritorna 1 (blocked/restrictive), 2 (default), o 3 (owned).

    Policy 7/5/2026 (modifica ADR 0081): il crawler e' identificato (UA
    `metnos-crawler/1.2 +metnos@metnos.com`), quindi parte generoso (T2)
    di default. Solo host esplicitamente listati in `blocked_origins.json`
    (manuale o auto-degrade post-429/503) finiscono in T1.

    Loopback (localhost/127.0.0.1/::1/*.local): SEMPRE T3 owned. La macchina
    e' dell'utente, niente rate-limit ragionevole §7.3.

    `trust='auto'` → consulta i file di config. `trust='owned'` → forza 3.
    `trust='blocked'` → forza 1. `trust='cookie:<dom>'` → tier 2 se match.
    """
    if trust == "owned":
        return 3
    if _is_loopback_host(host):
        return 3
    if trust == "blocked":
        return 1
    if trust and trust.startswith("cookie:"):
        cookie_dom = trust.split(":", 1)[1]
        if _domain_in(host, [cookie_dom]):
            return 2
    owned = _load_json(OWNED_FILE) or {}
    blocked = _load_json(BLOCKED_FILE) or {}
    if _domain_in(host, owned.get("domains", [])):
        return 3
    # T1 forzato: host che hanno dato problemi (429/503) o listati a mano.
    blocked_hosts = blocked.get("hosts", []) if isinstance(blocked, dict) else []
    if _domain_in(host, blocked_hosts):
        return 1
    # Default: T2. Trusted_origins.json mantiene la sua semantica come
    # alias (esplicito → T2 garantito), ma non e' piu' obbligatorio.
    return 2


def _tier_floor_ms(tier: int) -> int:
    return _RATE_FLOOR_OWNED_MS if tier == 3 else _RATE_FLOOR_DEFAULT_MS


# ─── Host capacity & parallel throttle (ADR 0098) ───────────────────────

def _host_capacity() -> dict:
    """Capacita' di parallelismo proporzionata all'host.

    Strix Halo 96GB / 32 thread → cap globale 32 inflight, niente bottleneck
    CPU. Per-host varia per tier per non sovraccaricare server esterni:
    T1 unknown=2, T2 trusted=6, T3 owned=12. Valori conservativi: si puo'
    abbassare via env METNOS_FIND_URLS_GLOBAL_MAX e METNOS_FIND_URLS_PER_HOST_*.
    """
    import multiprocessing
    import os
    cpu = max(1, multiprocessing.cpu_count())
    # Banda upstream tipica .33 fiber 2.5 Gbps: ben oltre 32 conn possibili.
    # Cap = min(64, cpu * 4) bilancia FD ulimit + memoria parsing (~10MB/conn).
    global_max = int(os.environ.get(
        "METNOS_FIND_URLS_GLOBAL_MAX", min(64, cpu * 4)
    ))
    return {
        "global_max": global_max,
        "per_host": {
            # T1 blocked/restrictive: host esplicitamente in
            # blocked_origins.json (auto-degrade post-429/503 o manuale).
            1: int(os.environ.get("METNOS_FIND_URLS_PER_HOST_T1", "2")),
            # T2 default: tutti gli host non bloccati. Crawler identificato
            # (UA + email contact) merita partenza generosa. Auto-degrade a
            # T1 su segnali concreti (429/503).
            2: int(os.environ.get("METNOS_FIND_URLS_PER_HOST_T2", "8")),
            # T3 owned: hosts in owned_domains.json (self-hosted).
            3: int(os.environ.get("METNOS_FIND_URLS_PER_HOST_T3", "16")),
        },
    }


# Throttle condiviso (ADR 0103) — modulo runtime/host_throttle.py.
sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from host_throttle import HostThrottle  # noqa: E402
# Host health tracker per auto-degrade T2→T1 su 429/503 (ADR 0108).
try:
    from host_health import record_response as _hh_record  # noqa: E402
    from host_health import maybe_block_host as _hh_maybe_block  # noqa: E402
except Exception:
    _hh_record = None
    _hh_maybe_block = None



# ─── Robots.txt cache (per origin) ──────────────────────────────────────

class _RobotsCache:
    def __init__(self, opener, ua: str, timeout: float):
        self._cache: dict[str, list[str]] = {}  # origin -> disallow paths
        self._opener = opener
        self._ua = ua
        self._timeout = timeout

    def disallow_for(self, origin: str) -> list[str]:
        if origin in self._cache:
            return self._cache[origin]
        url = origin.rstrip("/") + "/robots.txt"
        rules: list[str] = []
        try:
            req = urllib.request.Request(url, headers={"User-Agent": self._ua})
            with self._opener.open(req, timeout=self._timeout) as resp:
                txt = resp.read(64 * 1024).decode("utf-8", errors="replace")
        except Exception:
            self._cache[origin] = []
            return []
        # Parser molto leggero: cerchiamo solo "User-agent: *" + Disallow.
        active = False
        for line in txt.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip().lower(); v = v.strip()
            if k == "user-agent":
                active = (v == "*" or self._ua.startswith(v))
            elif k == "disallow" and active and v:
                rules.append(v)
        self._cache[origin] = rules
        return rules


def _is_disallowed(path: str, rules: list[str]) -> bool:
    for r in rules:
        if not r:
            continue
        # trattamento glob + prefix: usiamo fnmatch quando il pattern ha *,
        # prefix-match altrimenti (convenzione standard robots.txt).
        if "*" in r or "?" in r:
            if fnmatch.fnmatchcase(path, r):
                return True
        elif path.startswith(r):
            return True
    return False


# ─── Sitemap discovery ──────────────────────────────────────────────────

def _try_sitemap(seed_url: str, opener, timeout: float) -> list[tuple[str, float | None]]:
    """Ritorna [(url, lastmod_epoch | None), ...]. Vuoto se assente.

    Segue sitemap-index per max 1 livello (sufficiente per la maggior
    parte dei siti self-hosted; evita esplosione).
    """
    parsed = urllib.parse.urlparse(seed_url)
    candidates = [
        f"{parsed.scheme}://{parsed.netloc}/sitemap.xml",
        f"{parsed.scheme}://{parsed.netloc}/sitemap_index.xml",
    ]
    for sm_url in candidates:
        try:
            req = urllib.request.Request(sm_url, headers={"User-Agent": USER_AGENT})
            with opener.open(req, timeout=timeout) as resp:
                xml_bytes = resp.read(4 * 1024 * 1024)
        except Exception:
            continue
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError:
            continue
        # Strip namespace robusto
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag[1: root.tag.index("}")]
        nsp = f"{{{ns}}}" if ns else ""
        out: list[tuple[str, float | None]] = []
        # caso 1: sitemapindex → sub-sitemap
        if root.tag.endswith("sitemapindex"):
            sub_urls = [el.findtext(f"{nsp}loc") or "" for el in root.findall(f"{nsp}sitemap")]
            for sub in sub_urls[:20]:  # budget di sicurezza
                if not sub:
                    continue
                try:
                    req2 = urllib.request.Request(sub, headers={"User-Agent": USER_AGENT})
                    with opener.open(req2, timeout=timeout) as resp:
                        sub_bytes = resp.read(4 * 1024 * 1024)
                    sub_root = ET.fromstring(sub_bytes)
                    out.extend(_extract_sitemap_urls(sub_root))
                except Exception:
                    continue
            return out
        # caso 2: urlset diretto
        out.extend(_extract_sitemap_urls(root))
        return out
    return []


def _extract_sitemap_urls(root) -> list[tuple[str, float | None]]:
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag[1: root.tag.index("}")]
    nsp = f"{{{ns}}}" if ns else ""
    out: list[tuple[str, float | None]] = []
    for el in root.findall(f"{nsp}url"):
        loc = el.findtext(f"{nsp}loc") or ""
        lastmod_s = el.findtext(f"{nsp}lastmod")
        lm: float | None = None
        if lastmod_s:
            lm = _parse_iso_to_epoch(lastmod_s)
        if loc:
            out.append((loc, lm))
    return out


def _parse_iso_to_epoch(s: str) -> float | None:
    """Parse ISO8601 (sitemap lastmod). Ritorna epoch o None se illeggibile."""
    s = s.strip()
    formats = (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d",
    )
    for fmt in formats:
        try:
            from datetime import datetime, timezone
            if fmt.endswith("%z"):
                # Python <3.11 non supporta '+02:00' senza colon-strip
                ss = s.replace("Z", "+0000")
                if len(ss) >= 6 and ss[-3] == ":":
                    ss = ss[:-3] + ss[-2:]
                dt = datetime.strptime(ss, fmt)
            elif fmt.endswith("Z"):
                dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            else:
                dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, TypeError):
            continue
    return None


# ─── RSS discovery ──────────────────────────────────────────────────────

def _try_rss(rss_urls: list[str], opener, timeout: float) -> list[tuple[str, str, str, float | None]]:
    """Ritorna [(url, title, snippet, lastmod_epoch | None), ...]."""
    out = []
    for rss_url in rss_urls[:3]:  # niente sciame
        try:
            req = urllib.request.Request(rss_url, headers={"User-Agent": USER_AGENT})
            with opener.open(req, timeout=timeout) as resp:
                xml_bytes = resp.read(4 * 1024 * 1024)
            root = ET.fromstring(xml_bytes)
        except Exception:
            continue
        # Atom: <feed><entry><link href=.../><title/><summary/><updated/>
        if root.tag.endswith("feed"):
            ns = root.tag[1: root.tag.index("}")] if root.tag.startswith("{") else ""
            nsp = f"{{{ns}}}" if ns else ""
            for entry in root.findall(f"{nsp}entry"):
                href = ""
                link_el = entry.find(f"{nsp}link")
                if link_el is not None:
                    href = link_el.get("href") or ""
                title = (entry.findtext(f"{nsp}title") or "").strip()
                summary = (entry.findtext(f"{nsp}summary") or
                           entry.findtext(f"{nsp}content") or "").strip()[:300]
                upd = entry.findtext(f"{nsp}updated") or entry.findtext(f"{nsp}published") or ""
                lm = _parse_iso_to_epoch(upd) if upd else None
                if href:
                    out.append((href, title, summary, lm))
        else:
            # RSS 2.0: <rss><channel><item><link/><title/><description/><pubDate/>
            for item in root.findall(".//item"):
                href = (item.findtext("link") or "").strip()
                title = (item.findtext("title") or "").strip()
                summary = (item.findtext("description") or "").strip()[:300]
                pub = item.findtext("pubDate") or ""
                lm = _parse_rfc822(pub) if pub else None
                if href:
                    out.append((href, title, summary, lm))
    return out


def _parse_rfc822(s: str) -> float | None:
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(s)
        return dt.timestamp() if dt is not None else None
    except (TypeError, ValueError):
        return None


# ─── Time window ────────────────────────────────────────────────────────

def _within_window(epoch: float | None, window: str) -> bool:
    if window == "all":
        return True
    if epoch is None:
        # Senza data, NON escludiamo (potrebbe essere fresca ma senza
        # lastmod). Filtraggio aggressivo perde contenuti utili.
        return True
    now = time.time()
    delta = now - epoch
    if window == "today":
        # giorno corrente UTC: confronta date stringa
        from datetime import datetime, timezone
        return datetime.fromtimestamp(epoch, tz=timezone.utc).date() == \
               datetime.now(tz=timezone.utc).date()
    if window == "last-24h":
        return delta <= 24 * 3600
    if window == "last-7d":
        return delta <= 7 * 24 * 3600
    if window.startswith("last-") and window.endswith("d"):
        try:
            n = int(window[5:-1])
            return delta <= n * 24 * 3600
        except ValueError:
            return True
    return True


# ─── BM25 scoring ───────────────────────────────────────────────────────

def _tokenize(s: str) -> list[str]:
    return re.findall(r"[a-zA-ZÀ-ſ]{2,}", (s or "").lower())


def _bm25_score(query_terms: list[str], docs: list[str], doc_idx: int,
                k1: float = 1.5, b: float = 0.75) -> float:
    """BM25 minimale: doc i' tokens, calcoliamo idf su tutti i docs."""
    if not query_terms or not docs:
        return 0.0
    tokenized = [_tokenize(d) for d in docs]
    doc_lens = [len(t) for t in tokenized]
    avgdl = sum(doc_lens) / max(1, len(doc_lens))
    n_docs = len(docs)
    score = 0.0
    if doc_idx >= len(tokenized):
        return 0.0
    doc_tokens = tokenized[doc_idx]
    if not doc_tokens:
        return 0.0
    for term in query_terms:
        df = sum(1 for t in tokenized if term in t)
        if df == 0:
            continue
        idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1)
        tf = doc_tokens.count(term)
        if tf == 0:
            continue
        denom = tf + k1 * (1 - b + b * doc_lens[doc_idx] / max(1, avgdl))
        score += idf * (tf * (k1 + 1)) / denom
    return score


# ─── Path filtering ─────────────────────────────────────────────────────

def _matches_any(path: str, patterns: list[str]) -> bool:
    """Match user-friendly:
      - pattern con `*` o `?` → glob (fnmatch)
      - pattern con metacaratteri regex (^$|\\d) → regex
      - altrimenti → substring match (case-insensitive)

    Esempi: `/cronaca/roma` matcha `/cronaca/roma/articolo-x` (substring);
    `/blog/*` matcha `/blog/2026/post` (glob); `^/news/[0-9]+` matcha via regex.
    """
    if not patterns:
        return False
    path_low = path.lower()
    for p in patterns:
        if not p:
            continue
        p_low = p.lower()
        try:
            if any(c in p for c in ("*", "?", "[")):
                if fnmatch.fnmatchcase(path_low, p_low):
                    return True
            elif any(c in p for c in ("^", "$", "|", "\\")):
                import re as _re
                if _re.search(p, path):
                    return True
            else:
                # substring case-insensitive (user-friendly default)
                if p_low in path_low:
                    return True
        except Exception:
            pass
        try:
            if re.search(p, path):
                return True
        except re.error:
            continue
    return False


# ─── Deep search helpers (S6, 6/5/2026) ────────────────────────────────


def _embedding_service():
    """Servizio embedding via SHIM ai_backend (esercizio→suprastructure,
    pubblico→BGE ONNX standalone). None se non disponibile (fallback graceful).
    Rimosso l'import diretto `suprastructure.*` (fix dipendenza B1 rilascio
    pubblico): la selezione del backend è centralizzata in runtime/ai_backend."""
    try:
        from ai_backend import embedding_service
        return embedding_service()
    except Exception:
        return None


def _extract_pdf_text(body: bytes, max_chars: int = 30000) -> str:
    """Estrai testo da un body PDF. Best-effort, ritorna stringa (anche
    vuota se il parse fallisce). Cap a 30k char per evitare OOM su PDF
    enormi (basta per ranking)."""
    try:
        import pypdf
        import io as _io
        reader = pypdf.PdfReader(_io.BytesIO(body))
        pages = []
        total = 0
        for page in reader.pages:
            try:
                txt = page.extract_text() or ""
            except Exception:
                txt = ""
            if not txt:
                continue
            pages.append(txt)
            total += len(txt)
            if total >= max_chars:
                break
        return "\n".join(pages)[:max_chars]
    except Exception:
        return ""


def _extract_html_text(body_bytes: bytes, content_type: str,
                        max_chars: int = 30000) -> str:
    """Estrai testo dal body HTML usando il LinkExtractor (lightweight)."""
    try:
        if "text/html" not in (content_type or "").lower():
            return ""
        try:
            text = body_bytes.decode("utf-8", errors="replace")
        except (UnicodeDecodeError, LookupError):
            text = body_bytes.decode("latin-1", errors="replace")
        p = _LinkExtractor()
        p.feed(text)
        # title + snippet_parts joined: gia' senza HTML tags
        full_text = p.title + "\n" + " ".join(p.snippet_parts)
        return full_text[:max_chars]
    except Exception:
        return ""


def _deep_search_phase(entries: list, topic_terms: list[str], *,
                        opener, timeout_s: float, top_k: int,
                        rate_limit_ms: int) -> list:
    """Pre-rank ibrido + lettura body top-K + ranking content.

    Per ogni entry top-K (default 30 per metadata score):
      - se HTML: ri-fetch body, estrai full text via _LinkExtractor
      - se PDF: ri-fetch body, estrai testo via pypdf
    Calcola content_score = BM25 sul full text + cosine embedding(query, body).
    Re-ordina entries per (content_score se presente, metadata_score altrimenti).
    Aggiunge `content_snippet`, `content_score`, `hit_terms` all'entry.
    """
    emb = _embedding_service()
    query_str = " ".join(topic_terms)
    query_vec = None
    if emb is not None:
        try:
            v = emb.embed_texts([query_str])
            if v is not None and len(v) > 0:
                query_vec = v[0]
        except Exception:
            query_vec = None

    # Top-K per metadata score (entries gia' ordinate desc).
    top = entries[:top_k]
    rest = entries[top_k:]

    # Per ognuna: ri-fetch + estrazione + scoring content.
    last_fetch = {}
    for e in top:
        url = e.get("url", "")
        if not url:
            continue
        ps = urllib.parse.urlparse(url)
        origin = f"{ps.scheme}://{ps.netloc}"
        # Rate limit per origin (semplice, blocking).
        prev = last_fetch.get(origin, 0)
        wait = (rate_limit_ms / 1000.0) - (time.time() - prev)
        if wait > 0:
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with opener.open(req, timeout=timeout_s) as resp:
                ctype = resp.headers.get("Content-Type", "")
                body = resp.read(4 * 1024 * 1024)  # cap 4MB per PDF medi
                last_fetch[origin] = time.time()
        except Exception:
            continue

        # Estrai testo in base al tipo.
        if "pdf" in ctype.lower() or e.get("doc_ext") == ".pdf":
            text = _extract_pdf_text(body)
        else:
            text = _extract_html_text(body, ctype)

        if not text:
            continue

        # BM25 content (su singolo doc sintetico).
        text_low = text.lower()
        hit_terms = [t for t in topic_terms if t in text_low]
        bm25 = _bm25_score(topic_terms, [text], 0)
        # Embedding cosine.
        cos = 0.0
        if query_vec is not None and emb is not None:
            try:
                v = emb.embed_texts([text[:8000]])
                if v is not None and len(v) > 0:
                    # Vettori L2-normalized → dot = cosine.
                    cos = float((v[0] * query_vec).sum())
            except Exception:
                cos = 0.0

        # Score finale ibrido. Pesi: 0.3 metadata + 0.4 BM25 content + 0.3 cosine.
        content_score = round(0.4 * bm25 + 0.3 * cos * 10.0, 3)  # cos in [-1,1] → scala
        e["content_score"] = content_score
        e["bm25_content"] = round(bm25, 3)
        e["content_cosine"] = round(cos, 3)
        e["hit_terms"] = hit_terms
        # Snippet attorno al primo hit_term.
        if hit_terms:
            first = hit_terms[0]
            idx = text_low.find(first)
            if idx >= 0:
                start = max(0, idx - 80)
                end = min(len(text), idx + 200)
                e["content_snippet"] = text[start:end].strip()[:300]

        # Score finale combinato.
        e["score"] = round(0.3 * e.get("score", 0.0) + content_score * 1.0, 3)

    # Re-sort top con score aggiornato + concat con rest (rest invariate).
    top.sort(key=lambda x: x.get("score", 0), reverse=True)
    return top + rest


# ─── SearXNG seed-discoverer (ADR 0115) ─────────────────────────────────


def _searxng_search(query: str, top_n: int = SEARXNG_TOP_N,
                     base_url: str | None = None,
                     timeout_s: float = SEARXNG_TIMEOUT_S
                     ) -> tuple[list[str], str | None]:
    """Wrapper retro-compatibile: ritorna solo URL.

    Riusa `_searxng_search_full` e proietta. Mantenuto perche' diversi
    test/import si appoggiano alla firma originale.
    """
    items, err = _searxng_search_full(
        query, top_n=top_n, base_url=base_url, timeout_s=timeout_s,
    )
    return ([it["url"] for it in items], err)


def _time_window_to_searxng_range(window: str) -> str | None:
    """Mappa la canonical time_window a SearXNG `time_range` param.

    Mapping deterministico (no LLM, no hardcoding lingua):
      today, last-24h           → day
      last-Nd, N <= 7           → week
      last-Nd, 7 < N <= 31      → month
      last-Nd, 31 < N <= 365    → year
      last-Nh con N <= 24       → day
      altrimenti (all, range custom) → None (nessun filtro)

    SearXNG time_range filtra server-side via i motori sottostanti.
    """
    if not window or window == "all":
        return None
    if window in ("today", "last-24h"):
        return "day"
    if window.startswith("last-") and window.endswith("h"):
        try:
            n = int(window[5:-1])
            return "day" if n <= 24 else None
        except ValueError:
            return None
    if window.startswith("last-") and window.endswith("d"):
        try:
            n = int(window[5:-1])
        except ValueError:
            return None
        if n <= 7:
            return "week"
        if n <= 31:
            return "month"
        if n <= 365:
            return "year"
        return None
    return None


def _searxng_search_full(query: str, top_n: int = SEARXNG_TOP_N,
                          base_url: str | None = None,
                          timeout_s: float = SEARXNG_TIMEOUT_S,
                          time_range: str | None = None
                          ) -> tuple[list[dict], str | None]:
    """Interroga SearXNG e ritorna ([{url, title, snippet}], error_class).

    Equivalente a `_searxng_search` ma preserva title+snippet per il
    re-rank LLM (ADR 0118). Determinismo §7.9: solo HTTP GET + JSON.
    Filtra blocked_origins (ADR 0108). time_range filtra server-side
    (day/week/month/year) quando passato.
    """
    if not query or not query.strip():
        return ([], "search_backend_invalid")
    base = (base_url
            or os.environ.get("METNOS_SEARXNG_URL", SEARXNG_URL_DEFAULT)
            ).rstrip("/")
    # §7.9: NIENTE restrizione di lingua. Senza questo, l'istanza SearXNG usa il
    # suo default (IT) → filtra/penalizza i risultati EN PRIMA del rerank (bug
    # "AMD ROCm" → congressi medici IT invece di AMD-chip/ROCm). La lingua giusta
    # è un OUTCOME della rilevanza (rerank topico + relevance-gate), non un input
    # da indovinare. Override esplicito a 'all' = neutro multi-lingua.
    params = {"q": query, "format": "json", "language": "all"}
    if time_range:
        params["time_range"] = time_range
    qs = urllib.parse.urlencode(params)
    url = f"{base}/search?{qs}"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read(4 * 1024 * 1024)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError):
        return ([], "search_backend_unavailable")
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ([], "search_backend_invalid")
    items = data.get("results") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return ([], "search_backend_invalid")
    blocked = _load_json(BLOCKED_FILE) or {}
    blocked_hosts = blocked.get("hosts", []) if isinstance(blocked, dict) else []
    out: list[dict] = []
    seen: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        u = it.get("url")
        if not isinstance(u, str) or not u.startswith(("http://", "https://")):
            continue
        if u in seen:
            continue
        seen.add(u)
        try:
            host = urllib.parse.urlparse(u).hostname or ""
        except ValueError:
            continue
        if blocked_hosts and _domain_in(host, blocked_hosts):
            continue
        title = str(it.get("title") or "")[:300]
        snippet = str(it.get("content") or "")[:600]
        out.append({"url": u, "title": title, "snippet": snippet})
        if len(out) >= top_n:
            break
    if not out:
        return ([], "search_backend_invalid")
    return (out, None)


def _llm_rerank_candidates(user_query: str, candidates: list[dict],
                           top_k: int = 10) -> tuple[list[str], dict]:
    """Re-rank LLM general-purpose dei candidati SearXNG (ADR 0118).

    Lingua/dominio/topic-agnostic: il modello (Gemma 4 26B locale)
    riceve query + [{url, title, snippet}] e ritorna `{top: [{url, score}]}`.

    Ritorna (urls_ordered, meta). Su qualsiasi errore (LLM down, JSON
    malformato, top vuoto) ritorna i candidati nell'ordine originale —
    non blocca mai il flusso (graceful degradation §2.8 invariata: il
    fallback non e' silenzioso, finisce in `meta.error`).
    """
    if not candidates:
        return ([], {"used": False, "reason": "no_candidates"})
    n = len(candidates)
    if n <= 1:
        return ([c["url"] for c in candidates],
                {"used": False, "reason": "trivial_size"})
    try:
        import sys as _sys
        # runtime/ già su sys.path dalla bootstrap a riga 380 (METNOS_RUNTIME-aware).
        from prompt_loader import get as _prompt_get  # type: ignore
        from llm_helpers import call_llm as _call_llm  # type: ignore
        from config import DEFAULT_LANG as _lang  # type: ignore
    except Exception as ex:
        return ([c["url"] for c in candidates],
                {"used": False, "reason": "import_failed",
                 "error": repr(ex)[:120]})

    try:
        prompt = _prompt_get("web_rerank", _lang or "it", top_k=top_k)
    except Exception as ex:
        return ([c["url"] for c in candidates],
                {"used": False, "reason": "prompt_failed",
                 "error": repr(ex)[:120]})

    payload = {
        "user_query": user_query,
        "candidates": [
            {
                "url": c["url"],
                "title": c.get("title", ""),
                "snippet": c.get("snippet", ""),
            }
            for c in candidates
        ],
    }

    try:
        text, meta = _call_llm(
            payload, prompt, tier="middle",
            max_tokens=900, temperature=0.0, think=False,
        )
    except Exception as ex:
        return ([c["url"] for c in candidates],
                {"used": False, "reason": "llm_unavailable",
                 "error": repr(ex)[:120]})

    # Estrai JSON dal text (a volte il LLM mette code-fences ```)
    raw = text.strip()
    if raw.startswith("```"):
        # rimuove fence opzionale
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        # Tenta estrarre il primo blocco { ... } valido
        i, j = raw.find("{"), raw.rfind("}")
        if i >= 0 and j > i:
            try:
                obj = json.loads(raw[i:j + 1])
            except json.JSONDecodeError:
                return ([c["url"] for c in candidates],
                        {"used": False, "reason": "json_invalid",
                         "raw_head": raw[:200]})
        else:
            return ([c["url"] for c in candidates],
                    {"used": False, "reason": "json_invalid",
                     "raw_head": raw[:200]})

    top = obj.get("top") if isinstance(obj, dict) else None
    if not isinstance(top, list) or not top:
        return ([c["url"] for c in candidates],
                {"used": False, "reason": "empty_top"})

    valid_urls = {c["url"] for c in candidates}
    seen_out: set[str] = set()
    ranked: list[tuple[str, float]] = []
    for item in top:
        if not isinstance(item, dict):
            continue
        u = item.get("url")
        s = item.get("score", 0.0)
        if not isinstance(u, str) or u not in valid_urls or u in seen_out:
            continue
        try:
            sc = float(s)
        except (TypeError, ValueError):
            sc = 0.0
        ranked.append((u, sc))
        seen_out.add(u)

    if not ranked:
        return ([c["url"] for c in candidates],
                {"used": False, "reason": "no_valid_in_top"})

    ranked.sort(key=lambda t: t[1], reverse=True)
    urls_ordered = [u for (u, _) in ranked[:top_k]]
    return (urls_ordered, {
        "used": True,
        "n_candidates": n,
        "n_kept": len(urls_ordered),
        "in_tokens": meta.get("in_tokens"),
        "out_tokens": meta.get("out_tokens"),
        "latency_ms": meta.get("latency_ms"),
    })


# ─── Main invoke ────────────────────────────────────────────────────────

def _invoke_default(args: dict) -> dict:
    """Implementazione default httpx (urllib + SearXNG). Il dispatcher
    `invoke()` instrada qui via `backends.urls.httpx_default`."""
    seed_urls = args.get("seed_urls") or []
    if isinstance(seed_urls, str):
        seed_urls = [seed_urls]
    if not isinstance(seed_urls, list):
        seed_urls = []

    # SearXNG seed-discoverer (ADR 0115): se l'utente ha passato
    # `search_query`, interroga SearXNG per ottenere i top-N URL e usali
    # come seed. Se anche `seed_urls` e' settato, i risultati di SearXNG
    # vengono PRIMA (probabilmente piu' rilevanti per la query).
    # Robustezza NL→determinismo (§2.4): il planner a volte emette `query`
    # (alias naturale) invece del canonico `search_query`. Senza questo
    # alias la query web cadeva in invalid_args→terminator ("Pipeline
    # malformata") su "cerca chi è X". Dominio aperto: accetta entrambi.
    search_query = args.get("search_query") or args.get("query")
    search_query = _inject_current_date(search_query)
    search_top_n = args.get("search_top_n")
    try:
        top_n = int(search_top_n) if search_top_n is not None else SEARXNG_TOP_N
    except (TypeError, ValueError):
        top_n = SEARXNG_TOP_N
    top_n = max(1, min(20, top_n))
    # Re-rank LLM (ADR 0118): general-purpose, multilingua, no hardcoded.
    # Disable: METNOS_FIND_URLS_RERANK=0. Default ON. Wide candidate set
    # (default 30) viene proposto al LLM, top_n finali vincolati a top_n.
    rerank_on = os.environ.get("METNOS_FIND_URLS_RERANK", "1") not in (
        "", "0", "false", "False",
    )
    try:
        wide_n = int(os.environ.get("METNOS_FIND_URLS_RERANK_WIDE", "30"))
    except ValueError:
        wide_n = 30
    wide_n = max(top_n, min(50, wide_n))
    search_meta: dict = {}
    if isinstance(search_query, str) and search_query.strip():
        # Fetch wide-N (default 30) per dare materiale al re-rank.
        # Se rerank disabilitato, equivalente al vecchio comportamento.
        n_fetch = wide_n if rerank_on else top_n
        # Propaga time_window → SearXNG time_range (filtro server-side).
        # Letto direttamente da args perche' la variabile `time_window`
        # locale viene normalizzata solo piu' avanti nella funzione.
        _sx_tr = _time_window_to_searxng_range(
            str(args.get("time_window", "all")),
        )
        candidates_full, err_class = _searxng_search_full(
            search_query.strip(), top_n=n_fetch, time_range=_sx_tr,
        )
        urls_from_search: list[str] = [c["url"] for c in candidates_full]
        rerank_meta: dict = {"used": False, "reason": "disabled"}
        if err_class is None and rerank_on and len(candidates_full) > top_n:
            urls_ranked, rerank_meta = _llm_rerank_candidates(
                search_query.strip(), candidates_full, top_k=top_n,
            )
            if rerank_meta.get("used"):
                urls_from_search = urls_ranked
            else:
                # Fallback: tronca a top_n dell'ordine originale SearXNG.
                urls_from_search = urls_from_search[:top_n]
        else:
            urls_from_search = urls_from_search[:top_n]
        if err_class is None:
            # 10/5/2026 fix architetturale (Roberto): in `search_query`
            # mode, SearXNG e' la fonte autoritativa dei seed_urls. I
            # seed_urls passati dal caller (tipicamente PLANNER guess
            # dal training, es. `https://www.usr.lazio.it/`) introducono
            # bias non verificabile. Politica: DROP universale dei caller
            # seeds quando search_query e' driver primario. SearXNG sa
            # piu' di Gemma su quale dominio ha la risposta.
            #
            # Caso d'uso «cerca su sito X argomento Y» rimane supportato
            # senza search_query (mode=deep_search con seed_urls=[X] +
            # topic=[Y]) — vedi planner.j2 (B/B.bis).
            caller_seeds_dropped = list(seed_urls)
            seed_urls = list(urls_from_search)
            search_meta = {
                "search_query": search_query.strip(),
                "search_results_used": len(urls_from_search),
                "search_top_n": top_n,
                "search_wide_n": n_fetch,
                "rerank": rerank_meta,
                "caller_seeds_dropped": caller_seeds_dropped,
            }
        else:
            # Backend non disponibile: se l'utente NON ha fornito seed_urls,
            # non possiamo procedere. §2.8 no silent failure.
            if not seed_urls:
                return {
                    "ok": False,
                    "error": (
                        "search backend unavailable "
                        "(SearXNG @ "
                        f"{os.environ.get('METNOS_SEARXNG_URL', SEARXNG_URL_DEFAULT)})"
                        " and no seed_urls fallback"
                    ),
                    "error_class": err_class,
                    "entries": [],
                    "search_query": search_query.strip(),
                }
            # Backend down ma seed_urls esistono: procedi con quelli e
            # segnala warning nei metadata (no fatal).
            search_meta = {
                "search_query": search_query.strip(),
                "search_results_used": 0,
                "search_top_n": top_n,
                "search_error_class": err_class,
            }

    # 10/5/2026 fix universale: droppa seed_urls che sono home di motori
    # di ricerca (Google.it, Bing, ...). Vale ANCHE quando search_query
    # NON e' provided: il PLANNER spesso passa "google.it" come
    # generico hint, ma il BFS poi crawla privacy/terms/help.
    seed_urls_pre = list(seed_urls)
    seed_urls = [u for u in seed_urls if not _is_search_engine_home(u)]
    if seed_urls_pre and not seed_urls and not (
            isinstance(search_query, str) and search_query.strip()):
        # User ha passato SOLO motori di ricerca senza search_query.
        # Niente da fare: niente content reachable.
        return {
            "ok": False,
            "error": _msg("ERR_SEED_URLS_ALL_HOME"),
            "error_class": "invalid_args",
            "entries": [],
        }

    if not seed_urls:
        # 10/5/2026: error_class esplicito per il PLANNER. Se l'utente ha
        # passato search_query e siamo qui, il motore ha ritornato 0
        # risultati (o e' down e nessun fallback). PLANNER deve passare a
        # final_answer onesto, niente retry stesso tool.
        if isinstance(search_query, str) and search_query.strip():
            return {
                "ok": False,
                "error": (
                    f"motore di ricerca senza risultati per "
                    f"'{search_query.strip()[:80]}'. "
                    f"Riformula la query con dettagli specifici (sito, tipo file, ente)."
                ),
                "error_class": "search_no_results",
                "entries": [],
                "search_query": search_query.strip(),
            }
        return {
            "ok": False,
            "error": _msg("ERR_ARG_MISSING_ONE_OF", options="seed_urls, search_query"),
            "error_class": "invalid_args",
        }

    topic = args.get("topic")
    if isinstance(topic, str):
        topic_terms = _tokenize(topic)
    elif isinstance(topic, list):
        topic_terms = []
        for t in topic:
            topic_terms.extend(_tokenize(str(t)))
    else:
        topic_terms = []

    # 10/5/2026 fix: se l'utente passa `search_query` ma non `topic`,
    # auto-deriva topic dai token della query. Senza questo, le 2000
    # candidate dal motore di ricerca restavano con score=0 e l'auto-drop
    # successivo non scattava → output gonfio "Hai 11035 URL...".
    if not topic_terms and isinstance(search_query, str) and search_query.strip():
        topic_terms = _tokenize(search_query)

    mode = args.get("mode", "default")
    trust = args.get("trust", "auto")
    # 10/5/2026: quando search_query guida la ricerca E il caller non
    # ha specificato max_depth, default a 1 (SearXNG roots + 1 hop di
    # cross-link). Sufficiente per scoprire documenti linkati dalle
    # pagine risultato (PDF circolari linkate da news aggregator). Non
    # rumoroso perche' search engine homes gia' filtrate dai seeds e
    # `same_origin_only=True` (default) limita ai domini SearXNG.
    if (isinstance(search_query, str) and search_query.strip()
            and "max_depth" not in args):
        max_depth = 1
    else:
        max_depth = max(0, min(10, int(args.get("max_depth", 2))))
    same_origin_only = bool(args.get("same_origin_only", True))
    include_subdomains = bool(args.get("include_subdomains", True))
    path_include = args.get("path_include") or []
    path_exclude = args.get("path_exclude")
    if path_exclude is None:
        path_exclude = list(_DEFAULT_PATH_EXCLUDE)
    respect_robots = bool(args.get("respect_robots", True))
    auth_cookies_file = args.get("auth_cookies_file")
    time_window = str(args.get("time_window", "all"))
    # Default 200ms (era 500): floor del tier 1. Velocizza ricerche di
    # 2.5x mantenendo politeness (5 req/s, ben sotto i limiti tipici).
    rate_limit_ms = int(args.get("rate_limit_ms", 200))
    timeout_s = float(args.get("timeout_s", 10.0))
    user_max_pages = args.get("max_pages")

    # Tier resolution: prendiamo il TIER dal primo seed (deterministico,
    # niente ambiguita' su crawl multi-tier nella stessa call). Le
    # crawl multi-host miste sono fuori scope MVP (ADR 0081 §future).
    parsed0 = urllib.parse.urlparse(seed_urls[0])
    host0 = parsed0.hostname or ""
    tier = _resolve_tier(host0, trust)

    # max_pages: combinazione user-cap + tier-cap.
    # - se l'utente NON passa max_pages → default del tier (1000 per tier 1)
    # - se passa esplicitamente → min(user, tier_cap_max=5000 per tier 1)
    tier_default = _TIER_DEFAULT.get(tier, 1000)
    tier_cap = _TIER_CAPS.get(tier, 5000)
    if user_max_pages is None or user_max_pages == 0:
        max_pages = tier_default
    else:
        max_pages = min(int(user_max_pages), tier_cap if tier < 3 else 50000)

    # truncated_intentional: se user ha messo max_pages e siamo in tier 3
    truncated_intentional = (tier == 3 and user_max_pages and user_max_pages == max_pages)

    # mode research/archive richiedono capability `crawl.recursive`. La
    # capability check vive nel runtime (vaglio); qui ci limitiamo a
    # validare l'input. Se mode != default, alziamo i cap e profondita'.
    if mode == "research":
        max_depth = max(max_depth, 4)
    elif mode == "archive":
        max_depth = max(max_depth, 6)
        # archive non e' soggetto a time_window
        if time_window != "all":
            time_window = "all"
    elif mode == "deep_search":
        # Modalita' content-aware: BFS larga + ranking ibrido (BM25 +
        # embedding) + lettura full-body dei top-K. Adatto a "trova IL
        # documento X sul sito Y dove il filename e' generico".
        max_depth = max(max_depth, 4)
        if user_max_pages is None or user_max_pages == 0:
            max_pages = max(max_pages, 2000)

    # Floor rate per tier
    floor_ms = _tier_floor_ms(tier)
    rate_limit_ms = max(floor_ms, rate_limit_ms)

    # Cookie jar opzionale. §2.4 robustezza al confine NL→determinismo:
    # se il LLM emette `auth_cookies_file` ma il file non esiste (caso
    # classico di hallucination su query di ricerca pubblica), degradiamo
    # silenziosamente a crawl PUBBLICO invece di fallire hard. Il warn
    # finisce nei metadata cosi' l'utente puo' verificare ex-post.
    cookie_jar = None
    auth_cookies_missing = False
    if auth_cookies_file:
        cookies_path = Path(os.path.expanduser(str(auth_cookies_file)))
        if cookies_path.exists():
            cookie_jar = http.cookiejar.MozillaCookieJar()
            try:
                cookie_jar.load(str(cookies_path), ignore_discard=True, ignore_expires=True)
            except Exception as ex:
                return {"ok": False,
                        "error": f"cookie file load failed: {cookies_path}: {ex}"}
        else:
            # File mancante = degradazione, non errore. Il crawl prosegue
            # come pubblico (nessun cookie). Segnaliamo nel metadata.
            auth_cookies_missing = True
            cookie_jar = None

    # Opener con cookie processor (se presente)
    handlers = []
    if cookie_jar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(cookie_jar))
    opener = urllib.request.build_opener(*handlers)

    # Stato crawl
    seed_origins = set()
    seed_registered = set()  # registered-domain (eTLD+1 approssimato) per
    # accettare sub-domini quando include_subdomains=true (default).
    # Es. seed=www.repubblica.it accetta anche roma.repubblica.it.
    for s in seed_urls:
        ps = urllib.parse.urlparse(s)
        seed_origins.add(f"{ps.scheme}://{ps.netloc}")
        # registered-domain euristica: prendi gli ultimi 2 label del netloc
        # (host senza porta). Funziona per gran parte dei TLD a 1 livello
        # (.it, .com, .org, .net). Per .co.uk e simili overshooting innocuo:
        # accetta sub-domini in piu', non scope-violation.
        host = ps.netloc.split(":")[0].lower()
        labels = host.split(".")
        if len(labels) >= 2:
            seed_registered.add(".".join(labels[-2:]))


    def _origin_match(parsed) -> bool:
        """True se il URL e' nello stesso scope del seed.
        Origin esatto sempre OK; sub-domini accettati se include_subdomains."""
        if f"{parsed.scheme}://{parsed.netloc}" in seed_origins:
            return True
        if not include_subdomains:
            return False
        host = parsed.netloc.split(":")[0].lower()
        labels = host.split(".")
        if len(labels) >= 2 and ".".join(labels[-2:]) in seed_registered:
            return True
        return False

    visited: set[str] = set()
    results: dict[str, dict] = {}  # url -> entry
    robots_skipped: list[str] = []
    queue: collections.deque = collections.deque()
    fail_count = 0
    # Meta-refresh hop budget: cap a 4 redirect a catena per host (evita
    # loop A→B→A patologici). A.it/ → /atp/ → /atp/index → ecc.
    meta_refresh_hops: dict[str, int] = {}

    # ── Discovery strategy ──────────────────────────────────────────────
    strategy_used = set()
    sitemap_pre: list[tuple[str, float | None]] = []
    rss_pre: list[tuple[str, str, str, float | None]] = []

    # 10/5/2026 fix: in search_query mode, SKIP sitemap/RSS prefetch.
    # SearXNG ha gia' filtrato la rilevanza; aggiungere il sitemap del
    # dominio (es. cislscuolaromarieti = 2000+ URL) drogherebbe le
    # entries. La SearXNG result list e' sufficiente come root del BFS.
    _skip_discovery_prefetch = (
        isinstance(search_query, str) and search_query.strip()
        and "max_depth" not in args
    )
    # Per ogni seed, prova sitemap, poi RSS (se sitemap vuoto), poi BFS.
    if not _skip_discovery_prefetch:
        for seed in seed_urls:
            sm = _try_sitemap(seed, opener, timeout_s)
            if sm:
                strategy_used.add("sitemap")
                sitemap_pre.extend(sm)

    # RSS: serve un fetch del seed HTML per scoprire i feed link
    seeds_to_bfs: list[tuple[str, int]] = []  # (url, depth)
    rate_state: dict[str, float] = {}  # origin -> last_fetch_epoch
    robots_cache = _RobotsCache(opener, USER_AGENT, timeout_s) if respect_robots else None

    def _rate_wait(origin: str):
        last = rate_state.get(origin, 0)
        elapsed_ms = (time.time() - last) * 1000
        wait_ms = max(0.0, rate_limit_ms - elapsed_ms)
        if wait_ms > 0:
            time.sleep(wait_ms / 1000)
        rate_state[origin] = time.time()

    def _allowed_by_robots(url: str) -> bool:
        if not respect_robots:
            return True
        ps = urllib.parse.urlparse(url)
        # Tier 3 owned (non-loopback): bypass robots come scelta esplicita del
        # gestore del proprio dominio. Loopback (test/dev) NON bypassa: il test
        # harness puo' simulare server con robots reali e attendersi compliance.
        if tier == 3 and not _is_loopback_host(ps.hostname or ""):
            return True
        origin = f"{ps.scheme}://{ps.netloc}"
        rules = robots_cache.disallow_for(origin) if robots_cache else []
        if _is_disallowed(ps.path or "/", rules):
            return False
        return True

    # Throttle parallelo per-host (ADR 0098). Iniettato sul tier corrente.
    _capacity = _host_capacity()
    _per_host_limit = _capacity["per_host"].get(tier, 2)
    _global_inflight_max = _capacity["global_max"]
    _throttle = HostThrottle(per_host_limit=_per_host_limit,
                              rate_limit_ms=rate_limit_ms)

    def _fetch_html(url: str) -> tuple[str, str, dict] | None:
        """Ritorna (body_text, content_type, headers_dict) o None.

        Versione thread-safe via `_HostThrottle` (per-host semaphore +
        rate-limit). Compatibile con uso sync (1 worker) e parallel.
        """
        ps = urllib.parse.urlparse(url)
        host = ps.netloc
        _throttle.acquire(host)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with opener.open(req, timeout=timeout_s) as resp:
                ctype = resp.headers.get("Content-Type", "")
                body = resp.read(2 * 1024 * 1024)
                if _hh_record is not None:
                    try:
                        _hh_record(host, 200)
                    except Exception:
                        pass
                if "text/html" not in ctype.lower():
                    return ("", ctype, dict(resp.headers))
                try:
                    text = body.decode("utf-8", errors="replace")
                except (UnicodeDecodeError, LookupError):
                    text = body.decode("latin-1", errors="replace")
                return (text, ctype, dict(resp.headers))
        except urllib.error.HTTPError as e:
            # ADR 0108: traccia 429/503 per auto-degrade T2→T1.
            if _hh_record is not None and e.code in (429, 503):
                try:
                    _hh_record(host, e.code)
                    if _hh_maybe_block is not None:
                        _hh_maybe_block(host)
                except Exception:
                    pass
            return None
        except Exception:
            return None
        finally:
            _throttle.release(host)

    def _fetch_html_batch(urls: list[str]) -> dict[str, tuple | None]:
        """Fetch parallelo di N URL. Ritorna {url: result|None}.

        Cap concorrenza globale `_global_inflight_max` (proporzionato CPU).
        Per-host gia' rispettato dal `_HostThrottle` interno a `_fetch_html`.
        """
        if len(urls) <= 1:
            return {urls[0]: _fetch_html(urls[0])} if urls else {}
        from concurrent.futures import ThreadPoolExecutor, as_completed
        out: dict[str, tuple | None] = {}
        workers = min(_global_inflight_max, len(urls))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_fetch_html, u): u for u in urls}
            for fut in as_completed(futs):
                u = futs[fut]
                try:
                    out[u] = fut.result()
                except Exception:
                    out[u] = None
        return out

    # Pre-fetch dei seed per scoprire eventuali RSS feed (link rel=alternate)
    # e per inseguire meta-refresh redirect verso il vero landing page.
    # Non segnamo visited, lasciando che la BFS canonical processi
    # comunque il seed effettivo come radice (per estrarre i link <a href>).
    rss_links_set: set[str] = set()
    # `effective_seeds`: lista finale dei seed dopo aver risolto eventuali
    # meta-refresh ("/" → "/atp/" su WordPress dietro Aruba). Generico:
    # qualsiasi homepage che usa meta-refresh per atterrare su sub-path
    # viene seguita PRIMA della BFS, cosi' la radice del crawl e' la
    # pagina vera con i link <a href>.
    effective_seeds: list[str] = []
    if not sitemap_pre and not _skip_discovery_prefetch:
        for seed in seed_urls:
            cur = seed
            for _hop in range(4):  # cap 4 hop per seed (vedi meta_refresh_hops)
                if not _allowed_by_robots(cur):
                    robots_skipped.append(cur)
                    cur = None
                    break
                r = _fetch_html(cur)
                if r is None:
                    fail_count += 1
                    cur = None
                    break
                text, ctype, _ = r
                # Meta-refresh detection: se il body html e' essenzialmente un
                # solo <meta http-equiv="refresh" url=...>, segui il target
                # come redirect HTTP. Si applica anche ai siti dietro proxy
                # legacy che non emettono header Location:.
                if "text/html" in ctype.lower() and text:
                    mr = _extract_meta_refresh(text)
                    if mr:
                        nxt = urllib.parse.urljoin(cur, mr).split("#", 1)[0]
                        if nxt and nxt != cur:
                            ps_n = urllib.parse.urlparse(nxt)
                            if ps_n.scheme in ("http", "https"):
                                cur = nxt
                                continue
                    # Se siamo qui, niente meta-refresh: estrai RSS e termina hop.
                    try:
                        p = _LinkExtractor()
                        p.feed(text)
                        for rl in p.rss_links:
                            abs_url = urllib.parse.urljoin(cur, rl)
                            rss_links_set.add(abs_url)
                    except Exception:
                        pass
                break
            if cur:
                effective_seeds.append(cur)
        if rss_links_set:
            rss_pre = _try_rss(list(rss_links_set), opener, timeout_s)
            if rss_pre:
                strategy_used.add("rss")
    else:
        effective_seeds = list(seed_urls)
    # Tutti i seed (eventualmente sostituiti dal meta-refresh target) entrano
    # in BFS come root. Non duplichiamo i seed nella queue — un set di unici.
    seen_seed: set[str] = set()
    for seed in effective_seeds:
        if seed not in seen_seed:
            seen_seed.add(seed)
            seeds_to_bfs.append((seed, 0))

    # Se sitemap o RSS hanno popolato urls, li pre-aggiungiamo a results
    # senza fetch (li valutiamo solo per metadata).
    for url, lm in sitemap_pre:
        if url in results:
            continue
        if not _within_window(lm, time_window):
            continue
        ps = urllib.parse.urlparse(url)
        if same_origin_only and not _origin_match(ps):
            continue
        path = ps.path or "/"
        if _matches_any(path, path_exclude):
            continue
        if path_include and not _matches_any(path, path_include):
            continue
        results[url] = {
            "url": url, "title": "", "snippet": "",
            "score": 0.0, "depth": 0,
            "content_type": "", "fetched_at": None, "lastmod": lm,
        }

    for url, title, snippet, lm in rss_pre:
        if url in results:
            continue
        if not _within_window(lm, time_window):
            continue
        ps = urllib.parse.urlparse(url)
        if same_origin_only and not _origin_match(ps):
            continue
        path = ps.path or "/"
        if _matches_any(path, path_exclude):
            continue
        if path_include and not _matches_any(path, path_include):
            continue
        results[url] = {
            "url": url, "title": title, "snippet": snippet,
            "score": 0.0, "depth": 0,
            "content_type": "", "fetched_at": None, "lastmod": lm,
        }

    # ── BFS fallback ────────────────────────────────────────────────────
    # Eseguito SEMPRE per i seed, in modo da catturare link non in
    # sitemap/RSS. Quando sitemap_pre o rss_pre sono presenti, BFS resta
    # confinato dai limiti max_pages / max_depth.
    queue.extend(seeds_to_bfs)
    # BFS parallelizzata (ADR 0098): dequeue batch fino a `_global_inflight_max`
    # URL non-visitati e fetch in parallelo. Il parsing dei risultati resta
    # sequenziale (lavoro CPU-trascurabile vs I/O di rete).
    while queue and len(results) < max_pages:
        # Drena batch
        batch: list[tuple[str, int]] = []
        while queue and len(batch) < _global_inflight_max:
            url, depth = queue.popleft()
            if url in visited:
                continue
            visited.add(url)
            if not _allowed_by_robots(url):
                robots_skipped.append(url)
                continue
            batch.append((url, depth))
            if len(results) + len(batch) >= max_pages:
                break
        if not batch:
            continue
        # Fetch in parallel (per-host throttle gestito da _HostThrottle)
        fetch_results = _fetch_html_batch([u for u, _ in batch])
        # Processo risultati sequenzialmente: parsing + queueing nuovi link
        for url, depth in batch:
            r = fetch_results.get(url)
            if r is None:
                fail_count += 1
                continue
            text, ctype, _ = r
            ps = urllib.parse.urlparse(url)
            if same_origin_only and not _origin_match(ps):
                continue
            path = ps.path or "/"
            if _matches_any(path, path_exclude):
                continue
            if path_include and not _matches_any(path, path_include):
                continue
            # parsa solo se html
            title = ""; snippet = ""; rss_links_local = []
            if "text/html" in ctype.lower() and text:
                # Meta-refresh detection in BFS: se il body e' un puro
                # redirect <meta http-equiv="refresh" url=...>, accoda il
                # target a depth corrente (NON depth+1, perche' e' un
                # redirect non un follow di link) e SALTA la registrazione
                # in results (la pagina-redirect non e' interessante).
                ps_url = urllib.parse.urlparse(url)
                host_key = ps_url.netloc.lower()
                hops = meta_refresh_hops.get(host_key, 0)
                if hops < 4:
                    mr = _extract_meta_refresh(text)
                    if mr:
                        nxt = urllib.parse.urljoin(url, mr).split("#", 1)[0]
                        if nxt and nxt != url and nxt not in visited:
                            ps_n = urllib.parse.urlparse(nxt)
                            if ps_n.scheme in ("http", "https"):
                                meta_refresh_hops[host_key] = hops + 1
                                queue.append((nxt, depth))
                                continue
                try:
                    p = _LinkExtractor()
                    p.feed(text)
                    title = p.title; snippet = p.snippet
                    p.rss_links
                    # accoda link interni se profondita' lo permette
                    if depth < max_depth:
                        for href in p.links:
                            abs_url = urllib.parse.urljoin(url, href)
                            abs_url = abs_url.split("#", 1)[0]
                            if abs_url and abs_url not in visited:
                                ps2 = urllib.parse.urlparse(abs_url)
                                if ps2.scheme not in ("http", "https"):
                                    continue
                                queue.append((abs_url, depth + 1))
                    # Document discovery (S1, 6/5/2026): cataloga link a PDF/DOC/...
                    # come entries con anchor_text al posto del title (PDF non ha
                    # body HTML estraibile in modo leggero). Il BM25 successivo li
                    # scora come gli altri. Non li mettiamo in queue: NON facciamo
                    # fetch del corpo del PDF qui — quello e' compito di
                    # read_urls_pdf in step successivo.
                    for doc in p.documents:
                        doc_abs = urllib.parse.urljoin(url, doc["href"]).split("#", 1)[0]
                        if not doc_abs:
                            continue
                        ps_d = urllib.parse.urlparse(doc_abs)
                        if ps_d.scheme not in ("http", "https"):
                            continue
                        # 10/5/2026: cross-origin SEMPRE consentito per
                        # i documenti (PDF/docx/...). Le circolari scuola
                        # vivono spesso su CDN/portale separato (es.
                        # cislscuolaromarieti.it linka PDF su
                        # atpromaistruzione.it). same_origin_only blocca
                        # navigazione cross-domain, ma il discover di
                        # documenti citati va lasciato passare.
                        if doc_abs in results:
                            ent = results[doc_abs]
                            if not ent.get("title"):
                                ent["title"] = doc["anchor_text"] or ent.get("title", "")
                            continue
                        ext = doc["ext"] or ""
                        mime_guess = {
                            ".pdf":  "application/pdf",
                            ".doc":  "application/msword",
                            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            ".xls":  "application/vnd.ms-excel",
                            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            ".csv":  "text/csv",
                            ".odt":  "application/vnd.oasis.opendocument.text",
                            ".ods":  "application/vnd.oasis.opendocument.spreadsheet",
                            ".rtf":  "application/rtf",
                            ".txt":  "text/plain",
                            ".zip":  "application/zip",
                            ".tar":  "application/x-tar",
                            ".tgz":  "application/gzip",
                        }.get(ext, "application/octet-stream")
                        results[doc_abs] = {
                            "url": doc_abs,
                            "title": doc["anchor_text"] or "",
                            "snippet": "",
                            "score": 0.0,
                            "depth": depth + 1,
                            "content_type": mime_guess,
                            "fetched_at": None,
                            "lastmod": None,
                            "is_document": True,
                            "doc_ext": ext,
                            "parent_url": url,
                        }
                except Exception:
                    pass
                strategy_used.add("bfs")

            if url not in results:
                results[url] = {
                    "url": url, "title": title, "snippet": snippet,
                    "score": 0.0, "depth": depth,
                    "content_type": ctype, "fetched_at": time.time(),
                    "lastmod": None,
                }
            else:
                # arricchisci entry esistente (sitemap/rss)
                ent = results[url]
                if not ent.get("title"):
                    ent["title"] = title
                if not ent.get("snippet"):
                    ent["snippet"] = snippet
                ent["depth"] = depth
                ent["content_type"] = ctype
                ent["fetched_at"] = time.time()

    # ── Scoring + filtering finale ──────────────────────────────────────
    entries = list(results.values())
    if topic_terms:
        # BM25: doc = title + " " + snippet
        docs = [(e.get("title", "") + " " + e.get("snippet", "")) for e in entries]
        for i, e in enumerate(entries):
            base = _bm25_score(topic_terms, docs, i)
            # bonus se la keyword appare nel path o nel netloc dell'URL: peso
            # forte (5.0) perche' quando title+snippet non matchano (es.
            # homepage di un giornale) il path/sub-domain e' l'unico segnale
            # topical. Esempi: /cronaca/roma/ (path) e roma.repubblica.it
            # (netloc) → entrambi matchano topic=roma. Senza questo, su siti
            # generalisti il ranking BM25 e' tutto 0 e l'utente vede "non ho
            # trovato".
            ps_e = urllib.parse.urlparse(e["url"])
            path_low = (ps_e.path or "").lower()
            netloc_low = (ps_e.netloc or "").lower()
            bonus = 0.0
            for term in topic_terms:
                if term in path_low:
                    bonus += 5.0
                elif term in netloc_low:
                    bonus += 5.0
            # Niente floor automatico per documenti (rimosso 6/5/2026 sera):
            # se anchor_text e path NON contengono termini topic, il doc
            # non interessa e va filtrato come gli HTML. La regola ora e'
            # uniforme: score == 0 → fuori. Politica two-pass user-richiesta:
            # il BFS visita molte pagine ma appende a `discovered_documents`
            # solo cio' che ha segnale rilevante; un passo successivo
            # (read_urls_pdf) analizza i top-K candidati per estrarre
            # il vero best match.
            e["score"] = round(base + bonus, 3)
        entries.sort(key=lambda x: x["score"], reverse=True)
        # Topic = ranking, NON filtering (decisione 8/5/2026, allineamento
        # con ADR 0098 "Topic ranking BM25"). Le entries con score=0 (cross-link
        # a sezioni non topical-relevanti) restano nel risultato in fondo
        # alla lista — il PLANNER puo' ignorarle o capparle via `top_k`.
        # Per filtraggio esplicito il caller passa `min_score: float`.
        # Default automatico (10/5/2026 fix): quando topic e' applicato e
        # almeno una entry ha score>0, droppiamo il rumore con score==0
        # (cross-link non rilevanti). Senza questo, search_query con 2000
        # candidati SearXNG inquinava `entries` e ingannava cap-expand
        # ("Hai 2000 describe..."  vs reali ~6 ranked).
        min_score = args.get("min_score")
        if min_score is not None:
            try:
                threshold = float(min_score)
                entries = [e for e in entries if e.get("score", 0) >= threshold]
            except (TypeError, ValueError):
                pass  # malformed → no filter
        else:
            # Auto-drop score==0 quando il ranking ha prodotto almeno un hit.
            # Mantiene il pool per ranking ma toglie il rumore dall'output.
            if any(e.get("score", 0) > 0 for e in entries):
                entries = [e for e in entries if e.get("score", 0) > 0]
    else:
        # Senza topic, ordina per lastmod desc poi depth asc
        def _key(e):
            lm = e.get("lastmod") or 0
            return (-lm, e.get("depth", 99))
        entries.sort(key=_key)

    # Per-domain diversity cap (19/5/2026 v6). Principio generale: nessuna
    # source domina la top-K. Senza questo cap, query come "bitcoin notizie"
    # ritornano 15 entries dallo stesso aggregator (es. Investing.com),
    # 12 listings di prezzo del medesimo sito invece di prospettive
    # diverse. Cap default 3 per registered-domain (eTLD+1) SOLO quando
    # c'e' topic ranking (search query): per BFS seed-based stesso dominio
    # il cap non ha senso (tutti i link interni sono su same origin).
    # Override esplicito via `max_per_domain`, 0 = disable.
    if "max_per_domain" in args:
        max_per_domain = int(args.get("max_per_domain") or 0)
    else:
        max_per_domain = 3 if topic_terms else 0
    if max_per_domain > 0 and entries:
        from collections import Counter as _Counter
        _per_dom = _Counter()
        _kept = []
        _dropped_for_diversity = 0
        for e in entries:
            _u = e.get("url") or ""
            try:
                _ps = urllib.parse.urlparse(_u)
                _host = _ps.netloc.split(":")[0].lower()
                _labels = _host.split(".")
                _reg = ".".join(_labels[-2:]) if len(_labels) >= 2 else _host
            except Exception:
                _reg = "?"
            if _per_dom[_reg] >= max_per_domain:
                _dropped_for_diversity += 1
                continue
            _per_dom[_reg] += 1
            _kept.append(e)
        if _dropped_for_diversity:
            entries = _kept

    # Filtro time_window finale (per le entries arrivate via BFS senza
    # lastmod, _within_window passa sempre — le entries gia' filtrate su
    # sitemap/RSS sono coerenti).
    entries = [e for e in entries if _within_window(e.get("lastmod"), time_window)]

    # ── deep_search (S6, 6/5/2026): pre-rank ibrido + lettura body top-K ─
    # Pipeline content-aware: dopo BM25+path bonus, calcola embedding cosine
    # sui metadata; tieni top-K (default 30); per ogni top-K leggi body
    # (HTML re-extract / PDF via pypdf) ed embedda il full text; ranking
    # finale = mix(metadata_score, content_emb_cos). Output arricchito di
    # `content_snippet`, `content_score`, `hit_terms`. Fallback graceful
    # se EmbeddingService non disponibile o pypdf manca.
    if mode == "deep_search" and topic_terms and entries:
        try:
            entries = _deep_search_phase(
                entries, topic_terms,
                opener=opener, timeout_s=timeout_s,
                top_k=int(args.get("top_k_deep", 30)),
                rate_limit_ms=rate_limit_ms,
            )
        except Exception:
            # Fallback silenzioso al pre-rank base (loggato ma non blocca)
            pass

    # 10/5/2026 fix: `entries` e' la lista RANKED post auto-drop. Il
    # BFS pool (visited+queue) puo' essere enorme (300k+) ma e' rumore
    # interno: l'utente vede solo gli entries reali. truncated/used/
    # available_total riflettono SOLO entries.
    used = len(entries[:max_pages])
    truncated = len(entries) >= max_pages
    available_total = None
    if truncated:
        available_total = len(entries)
    # Lista documenti scoperti (S1, 6/5/2026): subset delle entries con
    # is_document=True, esposto separatamente per facilitarne il consumo
    # da read_urls_pdf / read_urls_html in step successivi.
    docs_list = [
        {
            "url": e["url"],
            "ext": e.get("doc_ext") or "",
            "anchor_text": e.get("title") or "",
            "score": e.get("score", 0.0),
            "parent_url": e.get("parent_url") or "",
        }
        for e in entries[:max_pages] if e.get("is_document")
    ]
    out = {
        "ok": True,
        "ok_count": used,
        "fail_count": fail_count,
        "entries": entries[:max_pages],
        "discovered_documents": docs_list,
        "discovery_strategy": "+".join(sorted(strategy_used)) if strategy_used else "bfs",
        "robots_skipped": robots_skipped,
        "metadata": {
            "tier": tier,
            "host_seed": host0,
            "rate_limit_ms_used": rate_limit_ms,
            "max_depth_used": max_depth,
            "time_window": time_window,
            "topic_terms": topic_terms,
            "discovery_count_sitemap": len(sitemap_pre),
            "discovery_count_rss": len(rss_pre),
            "discovery_count_documents": len(docs_list),
            # Soft warn (19/5 v6): auth_cookies_file passato ma assente.
            # Caller puo' rilevarlo per re-auth o avviso UI.
            **({"auth_cookies_missing": True}
                if auth_cookies_missing else {}),
            **({k: v for k, v in search_meta.items()} if search_meta else {}),
        },
    }
    if truncated:
        out["truncated"] = True
        out["truncated_what"] = "URL"
        out["used"] = used
        if available_total is not None:
            out["available_total"] = available_total
        out["cap_field"] = "max_pages"
        out["cap_value"] = max_pages
        if truncated_intentional:
            out["truncated_intentional"] = True
    return out


# --- Dispatcher (refactor 13/5/2026, ADR pending) -------------------------
_DEFAULT_CLIENT = "httpx"


def _resolve_backend(client: str):
    if client == "httpx":
        from backends.urls import httpx_default
        return httpx_default
    if client == "playwright":
        from backends.urls import playwright_stub
        return playwright_stub
    return None


def _inject_current_date(q):
    """§7.9: l'LLM emette anni dal suo training (es. '2024 2025') ignorando la
    data reale → ricerche temporali fuori fuoco (bug 'prossime conferenze' →
    anni passati). SOLO per intento FUTURO/RECENTE: rimuove gli anni stantii
    emessi e inietta l'anno corrente (+successivo se 'prossime'). Query con anno
    ESPLICITO e nessun marcatore future/recent (es. 'conferenze 2019') restano
    intatte. Deterministico, niente LLM."""
    if not isinstance(q, str) or not q.strip():
        return q
    import re as _re
    from datetime import datetime as _dt
    yr = _dt.now().year
    ql = q.lower()
    fut = _re.search(r"prossim|upcoming|\bnext\b|futur|in arrivo|ventur", ql)
    recent = _re.search(r"recent|ultim|latest|\bnew\b|novit|aggiornat", ql)
    # Drift cutoff LLM: ≥2 anni recenti-ma-passati consecutivi (es. "2024 2025")
    # = il modello tenta di essere "attuale" coi suoi anni di training. Segnale
    # forte; un anno singolo (es. "bilancio 2025") resta intenzionale.
    years = sorted({int(y) for y in _re.findall(r"\b(20\d{2})\b", q)})
    multi_stale = len([y for y in years if yr - 3 <= y < yr]) >= 2
    if not (fut or recent or multi_stale):
        return q
    q2 = _re.sub(r"\b20\d{2}\b", " ", q)          # via gli anni stantii
    q2 = _re.sub(r"\s{2,}", " ", q2).strip()
    return (f"{q2} {yr} {yr + 1}".strip() if (fut or multi_stale)
            else f"{q2} {yr}".strip())


def invoke(args: dict) -> dict:
    client = args.get("client") or _DEFAULT_CLIENT
    backend = _resolve_backend(client)
    if backend is None:
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client {client!r}")}
    return backend.find(args)


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    result = invoke(args)
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
