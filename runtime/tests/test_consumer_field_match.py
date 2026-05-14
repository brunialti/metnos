"""Test Layer 3 — consumer field match per il routing executor (5/5/2026).

Verifica:
  - normalizzazione plurale↔singolare
  - estrazione produced_keys da observation (entries/results/scalari top-level)
  - consumer_match restituisce executor coerenti con la convenzione I/O Metnos
  - regex domain detector (`_detect_domain_in_query`) — Layer 2
  - pool routing su 5 query reali via mock catalog
  - integrazione Layer 1 (force-include primary tools oltre cap top-K)
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path


_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


# ── Mock executor + catalog (no manifest sign required) ───────────────

@dataclass
class _MockExec:
    name: str
    args_schema: dict = field(default_factory=dict)
    affinity: list = field(default_factory=list)
    description: str = ""

    # Required hooks for prefilter (rank_adaptive iterates catalog).
    def has_capability(self, _):
        return False


def _mock_catalog():
    """Catalog mock semplice con i 10 executor chiave per i test."""
    items = [
        _MockExec("find_files",
                  args_schema={"properties": {"base_path": {}, "pattern": {},
                                              "recursive": {}}},
                  affinity=["find", "trova", "cerca", "file", "fs"],
                  description="trova file sul filesystem"),
        _MockExec("read_files",
                  args_schema={"properties": {"path": {}, "encoding": {}}},
                  affinity=["read", "leggi", "file"],
                  description="legge il contenuto di un file"),
        _MockExec("read_files_csv",
                  args_schema={"properties": {"paths": {}, "delimiter": {}}},
                  affinity=["read", "leggi", "csv"],
                  description="legge file CSV"),
        _MockExec("move_files",
                  args_schema={"properties": {"from_step": {},
                                              "dst_template": {}}},
                  affinity=["move", "sposta", "file"],
                  description="sposta file"),
        _MockExec("delete_files",
                  args_schema={"properties": {"from_step": {}}},
                  affinity=["delete", "cancella", "file"],
                  description="cancella file"),
        _MockExec("compute_files_loc",
                  args_schema={"properties": {"paths": {},
                                              "include_ext": {}}},
                  affinity=["compute", "loc", "file"],
                  description="conta linee di codice"),
        _MockExec("find_urls",
                  args_schema={"properties": {"seed_urls": {}, "topic": {}}},
                  affinity=["find", "trova", "cerca", "url", "web", "sito"],
                  description="crawler web"),
        _MockExec("read_urls_html",
                  args_schema={"properties": {"urls": {},
                                              "max_bytes": {}}},
                  affinity=["read", "leggi", "url", "html"],
                  description="legge pagine HTML"),
        _MockExec("read_urls_pdf",
                  args_schema={"properties": {"urls": {},
                                              "max_bytes": {}}},
                  affinity=["read", "leggi", "url", "pdf"],
                  description="legge PDF da URL"),
        _MockExec("get_urls",
                  args_schema={"properties": {"url": {}, "method": {}}},
                  affinity=["get", "fetch", "url", "scarica"],
                  description="HTTP GET singolo"),
        _MockExec("read_messages",
                  args_schema={"properties": {"folder": {}, "criteria": {}}},
                  affinity=["read", "leggi", "mail", "messaggio"],
                  description="legge mail"),
        _MockExec("move_messages",
                  args_schema={"properties": {"message_ids": {},
                                              "dst_folder": {}}},
                  affinity=["move", "sposta", "mail"],
                  description="sposta mail"),
        _MockExec("delete_messages",
                  args_schema={"properties": {"message_ids": {}}},
                  affinity=["delete", "cancella", "mail"],
                  description="cancella mail"),
        _MockExec("find_images_indices",
                  args_schema={"properties": {"base_path": {},
                                              "query_text": {}}},
                  affinity=["find", "cerca", "foto", "immagine"],
                  description="cerca foto via indice"),
    ]
    # Una piccola classe Catalog-like che implementa __iter__/__len__/get
    class _Cat:
        def __init__(self, items):
            self._d = {e.name: e for e in items}

        def __iter__(self):
            return iter(self._d.values())

        def __len__(self):
            return len(self._d)

        def get(self, name):
            return self._d.get(name)

        @property
        def executors(self):
            return self._d
    return _Cat(items)


# ── Layer 3: consumer_match + normalizzazione ─────────────────────────

class TestNormalizeKey:

    def test_plural_to_singular(self):
        from adaptive_rerank import _norm_key
        assert _norm_key("paths") == "path"
        assert _norm_key("urls") == "url"
        assert _norm_key("message_ids") == "message_id"
        assert _norm_key("entries") == "entrie"  # `s` stripping naive — accettabile

    def test_already_singular(self):
        from adaptive_rerank import _norm_key
        assert _norm_key("path") == "path"
        assert _norm_key("url") == "url"
        assert _norm_key("name") == "name"


class TestProducedKeys:

    def test_entries_with_url_title(self):
        """find_urls produce entries=[{url, title, snippet}] — produced_keys
        deve contenere `url`, `title`, `snippet` (normalizzati)."""
        from adaptive_rerank import _produced_keys
        obs = {"ok": True, "entries": [
            {"url": "https://x", "title": "T", "snippet": "snip"}
        ]}
        keys = _produced_keys(obs)
        assert "url" in keys
        assert "title" in keys
        assert "snippet" in keys

    def test_entries_with_path_name(self):
        from adaptive_rerank import _produced_keys
        obs = {"ok": True, "entries": [
            {"path": "/tmp/x", "name": "x", "mtime": 12345, "size": 100}
        ]}
        keys = _produced_keys(obs)
        assert "path" in keys
        assert "name" in keys

    def test_entries_with_message_id(self):
        from adaptive_rerank import _produced_keys
        obs = {"ok": True, "entries": [
            {"message_id": "abc", "subject": "Sub", "from": "x@y"}
        ]}
        keys = _produced_keys(obs)
        # message_id non ha 's' → resta message_id
        assert "message_id" in keys

    def test_empty_entries(self):
        from adaptive_rerank import _produced_keys
        assert _produced_keys({}) == set()
        assert _produced_keys({"entries": []}) == set()
        assert _produced_keys({"ok": True}) == set()
        assert _produced_keys(None) == set()
        assert _produced_keys("not a dict") == set()

    def test_results_key_too(self):
        """Executor trasformativi ritornano `results` invece di `entries`."""
        from adaptive_rerank import _produced_keys
        obs = {"ok": True, "results": [
            {"src_path": "/a", "dst_path": "/b", "moved": True}
        ]}
        keys = _produced_keys(obs)
        # `dst_path`, `src_path` sono in _GENERIC_PIPING_ARGS → esclusi
        # `moved` resta; piping args meta-control esclusi.
        assert "moved" in keys

    def test_skips_meta_fields(self):
        from adaptive_rerank import _produced_keys
        obs = {"ok": True, "ts_start": 1, "ts_end": 2,
               "duration_ms": 100, "audit_path": "/x",
               "useful": "data"}
        keys = _produced_keys(obs)
        assert "useful" in keys
        assert "ts_start" not in keys
        assert "duration_ms" not in keys
        assert "audit_path" not in keys


class TestConsumerMatch:

    def test_url_produces_url_consumers(self):
        """produced_keys={url, title} matcha read_urls_html (urls), get_urls
        (url), read_urls_pdf (urls)."""
        from adaptive_rerank import consumer_match
        cat = _mock_catalog()
        matches = consumer_match(
            catalog=cat,
            produced_keys={"url", "title", "snippet"},
            exclude_names={"find_urls"},
        )
        names = {e.name for e in matches}
        assert "read_urls_html" in names
        assert "read_urls_pdf" in names
        assert "get_urls" in names

    def test_path_produces_path_consumers(self):
        """produced_keys={path, name, mtime} matcha read_files (path),
        read_files_csv (paths), compute_files_loc (paths)."""
        from adaptive_rerank import consumer_match
        cat = _mock_catalog()
        matches = consumer_match(
            catalog=cat,
            produced_keys={"path", "name", "mtime"},
            exclude_names={"find_files"},
        )
        names = {e.name for e in matches}
        assert "read_files" in names
        assert "read_files_csv" in names
        assert "compute_files_loc" in names

    def test_message_id_matches_move_delete(self):
        """produced_keys={message_id, subject} matcha move_messages,
        delete_messages."""
        from adaptive_rerank import consumer_match
        cat = _mock_catalog()
        matches = consumer_match(
            catalog=cat,
            produced_keys={"message_id", "subject", "from"},
            exclude_names={"read_messages"},
        )
        names = {e.name for e in matches}
        assert "move_messages" in names
        assert "delete_messages" in names

    def test_empty_produced_keys_no_matches(self):
        from adaptive_rerank import consumer_match
        cat = _mock_catalog()
        assert consumer_match(catalog=cat, produced_keys=set()) == []

    def test_self_excluded(self):
        """L'executor stesso non si auto-include (già in current_candidates)."""
        from adaptive_rerank import consumer_match
        cat = _mock_catalog()
        matches = consumer_match(
            catalog=cat,
            produced_keys={"url"},
            exclude_names={"get_urls"},  # esclude se stesso
        )
        names = {e.name for e in matches}
        assert "get_urls" not in names
        # ma deve trovare ALTRI url consumer
        assert "read_urls_html" in names

    def test_final_answer_excluded(self):
        """final_answer non e' un consumer (non e' executor di pipeline)."""
        from adaptive_rerank import consumer_match
        # mini catalog con final_answer fake
        c = _mock_catalog()
        c._d["final_answer"] = _MockExec(
            "final_answer",
            args_schema={"properties": {"answer": {}}},
        )
        matches = consumer_match(catalog=c, produced_keys={"answer"})
        assert "final_answer" not in {e.name for e in matches}


# ── Layer 2: regex domain detector ────────────────────────────────────

class TestDomainDetector:

    def test_dotted_domain_with_cctld(self):
        from prefilter import _detect_domain_in_query
        assert _detect_domain_in_query("cerca in scuola.edu.it organico") is True

    def test_metnos_com(self):
        from prefilter import _detect_domain_in_query
        assert _detect_domain_in_query("vai su metnos.com") is True

    def test_repubblica_it(self):
        from prefilter import _detect_domain_in_query
        assert _detect_domain_in_query("notizie su repubblica.it") is True

    def test_tld_invented_passes(self):
        """Generale per qualsiasi TLD: `.health` non e' nella lista hardcoded
        ma la regex lo riconosce strutturalmente."""
        from prefilter import _detect_domain_in_query
        assert _detect_domain_in_query("vai su site.health") is True

    def test_filesystem_path_no_match(self):
        """`/tmp/file.jpg` NON deve essere detectato come dominio."""
        from prefilter import _detect_domain_in_query
        assert _detect_domain_in_query("leggi /tmp/file.jpg") is False
        assert _detect_domain_in_query("apri /home/user/notes.md") is False

    def test_version_numbers_no_match(self):
        """`v1.2.3`, `1.2`, `2026.01` non sono domini."""
        from prefilter import _detect_domain_in_query
        assert _detect_domain_in_query("usa python v1.2") is False
        assert _detect_domain_in_query("data 2026.01.15") is False

    def test_filename_no_match(self):
        """`file.txt`, `report.pdf` sono filename, non domini."""
        from prefilter import _detect_domain_in_query
        assert _detect_domain_in_query("scrivi report.pdf") is False
        assert _detect_domain_in_query("apri notes.md") is False

    def test_empty_query(self):
        from prefilter import _detect_domain_in_query
        assert _detect_domain_in_query("") is False
        assert _detect_domain_in_query(None) is False


class TestObjectDetectorWithDomain:
    """Verifica che detect_canonical_object usi il regex come override."""

    def test_domain_overrides_to_urls(self):
        from prefilter import detect_canonical_object, tokenize
        q = "cerca in scuola.edu.it i numeri di telefono"
        toks = tokenize(q)
        assert detect_canonical_object(toks, q) == "urls"

    def test_no_domain_normal_path(self):
        from prefilter import detect_canonical_object, tokenize
        q = "leggi il file /tmp/x.txt"
        toks = tokenize(q)
        # nessun dominio → normale detect (files tramite hint 'file')
        result = detect_canonical_object(toks, q)
        # Non e' urls. Puo' essere None o files.
        assert result != "urls"


# ── Smoke routing su query reali (mock catalog) ──────────────────────

class TestRoutingPoolSmoke:
    """Verifica che il pool finale per le 5 query problema contenga
    i tool chiave attesi. Niente LLM, ranker token-based via prefilter."""

    def test_query_foto_compleanno(self):
        """`cerca foto di compleanno` → pool include find_images_indices."""
        from prefilter import rank_adaptive
        cat = _mock_catalog()
        pool, info = rank_adaptive("cerca foto di compleanno", cat,
                                    k_min=5, k_max=8)
        names = [e.name for e in pool]
        assert "find_images_indices" in names, \
            f"find_images_indices missing from pool: {names}"

    def test_query_scuola_edu_it(self):
        """`cerca in scuola.edu.it organico` → pool include find_urls,
        get_urls, read_urls_html (Layer 1+2)."""
        from prefilter import rank_adaptive
        cat = _mock_catalog()
        pool, info = rank_adaptive("cerca in scuola.edu.it organico", cat,
                                    k_min=5, k_max=8)
        names = [e.name for e in pool]
        assert "find_urls" in names
        assert "get_urls" in names
        assert "read_urls_html" in names, \
            f"read_urls_html missing (Layer 1 force-include broke): {names}"

    def test_query_find_files(self):
        from prefilter import rank_adaptive
        cat = _mock_catalog()
        pool, info = rank_adaptive("trova file *.py in /opt", cat,
                                    k_min=5, k_max=8)
        names = [e.name for e in pool]
        assert "find_files" in names

    def test_post_step_find_urls_brings_read_urls_html(self):
        """Dopo step1 ok find_urls (entries={url, title, ...}) → pool step2
        include read_urls_html via consumer-match (Layer 3)."""
        from adaptive_rerank import re_rank_for_step
        cat = _mock_catalog()
        # current candidates simulati post-step1
        current = [cat.get(n) for n in
                   ("find_urls", "get_urls", "read_urls_html")]
        # find_urls produces entries with url
        obs = {"ok": True, "entries": [
            {"url": "https://scuola.edu.it/p1", "title": "Pagina 1",
             "snippet": "..."},
            {"url": "https://scuola.edu.it/p2", "title": "Pagina 2"},
        ]}
        post, rr_info = re_rank_for_step(
            original_query="cerca in scuola.edu.it organico",
            catalog=cat,
            current_candidates=current,
            latest_observation=obs,
        )
        names = [e.name for e in post]
        # read_urls_html era già in current. Ma read_urls_pdf no, e via
        # consumer-match deve entrare:
        assert "read_urls_pdf" in names, \
            f"consumer-match failed: read_urls_pdf missing: {names}"

    def test_post_step_find_files_brings_read_files(self):
        """Dopo step1 ok find_files (entries={path, name, mtime}) → pool
        step2 include read_files (in qualche modo: keyword o consumer-match).
        Verifica anche che consumer-match contribuisce esattamente i tool
        meno linguistically-evident (compute_files_loc / read_files_csv non
        emergono dalla query 'trova file *.py' ma sono raggiunti via match
        struttura args)."""
        from adaptive_rerank import re_rank_for_step
        cat = _mock_catalog()
        current = [cat.get("find_files")]
        obs = {"ok": True, "entries": [
            {"path": "/opt/x.py", "name": "x.py", "mtime": 12345, "size": 100},
            {"path": "/opt/y.py", "name": "y.py", "mtime": 12346, "size": 200},
        ]}
        post, rr_info = re_rank_for_step(
            original_query="trova file *.py in /opt",
            catalog=cat,
            current_candidates=current,
            latest_observation=obs,
        )
        names = [e.name for e in post]
        # read_files deve essere nel pool (qualunque la via)
        assert "read_files" in names
        # Consumer-match deve aver contribuito qualcosa: nel mock c'e' un
        # set di tool path-consumer, almeno UNO di questi non e'
        # linguistically-evident dalla query e arriva solo via consumer-match.
        assert "added_by_consumer_match" in rr_info
        consumer_added = set(rr_info["added_by_consumer_match"])
        # compute_files_loc o read_files_csv devono essere stati portati
        # via consumer-match (sono path-consumer ma keyword non li pesca).
        assert consumer_added & {"compute_files_loc", "read_files_csv"}, \
            f"consumer-match non ha aggiunto path-consumer: {consumer_added}"
