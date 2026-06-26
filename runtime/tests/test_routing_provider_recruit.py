"""test_routing_provider_recruit — reclutamento PROVIDER nel pool di routing.

Chiude il GAP «pool provider-cieco sul path MONO» (bug live 582b4824/22f32adb,
26/6): «su github» non reclutava `find_files_github` → il proposer ripiegava su
`find_urls`(web, raschia pagine HTML + PDF estranei) o `find_files`(locale).

Il fix `routing_pool._provider_recruit_and_gate` (§7.9 deterministico, SoT
`detection_lexicon provider.markers`, ZERO liste-sinonimi; modello ADR 0165 «il
provider è configurazione, non intent»): recluta `*_<provider>` con object ∈
clausole → gate per-nome → web-steal CONDIZIONATO (solo se un produttore nativo
non-urls è reclutato e la query non chiede urls/URL esplicito).

Casi del design panel + i 2 flaw segnalati dai giudici (mixed-compound web,
single-url read). Catalog SINTETICI: i `*_github` sono user-data (non nel repo).
"""
import sys
from pathlib import Path

_RT = Path(__file__).resolve().parent.parent
if str(_RT) not in sys.path:
    sys.path.insert(0, str(_RT))

from engine.routing_pool import _provider_recruit_and_gate  # noqa: E402
from engine.types import Intent                              # noqa: E402


def _exec(name, dormant=False):
    return type("E", (), {"name": name, "dormant": dormant})()


def _cat(*names):
    return [_exec(n) for n in names]


def _intent(verb="", obj="", actions=None):
    return Intent(verb=verb, object=obj, lang="it",
                  actions=list(actions or []))


def _run(names, query, intent, catalog):
    """Il guard opera su una lista-NOMI (come nel pool finale). Passa un catalog
    di Executor-stub per recruit/dormancy."""
    return _provider_recruit_and_gate(list(names), query, intent, catalog)


# ── (1) il bug headline: github files via find_urls ─────────────────────────

def test_github_files_recruits_native_and_strips_web_and_local():
    cat = _cat("find_files", "read_files", "get_files", "find_urls",
               "read_urls_html", "find_files_github", "read_files_github",
               "describe_entries")
    pool = ["find_files", "read_files", "find_urls", "read_urls_html",
            "describe_entries"]
    out = _run(pool, "riassumi i file readme.md su github nel repo brunialti/metnos",
               _intent("read", "files"), cat)
    assert "find_files_github" in out and "read_files_github" in out
    assert "find_urls" not in out and "read_urls_html" not in out   # web-steal
    assert "find_files" not in out                                  # gate (twin)
    assert "describe_entries" in out                                # innocuo resta


def test_github_count_gates_local_find():
    cat = _cat("find_files", "find_files_github")
    out = _run(["find_files"], "conta i file su github",
               _intent("find", "files"), cat)
    assert "find_files_github" in out and "find_files" not in out


# ── confini: nessun over-suppression ────────────────────────────────────────

def test_no_provider_marker_keeps_local():
    # marker assente (path /opt/.../issues strippato) → nessun recruit/gate.
    cat = _cat("find_files", "find_files_github")
    out = _run(["find_files"], "conta i file in /opt/metnos/issues",
               _intent("find", "files"), cat)
    assert "find_files" in out and "find_files_github" not in out


def test_web_steal_only_with_native_producer():
    # object=urls, nessun produttore file nativo reclutato → find_urls RESTA.
    cat = _cat("find_urls", "read_urls_html", "find_files_github")
    out = _run(["find_urls", "read_urls_html"],
               "trova articoli sul web su github actions",
               _intent("find", "urls"), cat)
    assert "find_urls" in out


def test_mixed_compound_web_clause_preserved():
    # FLAW #1 dei giudici: clausola web GENUINA + clausola github files.
    # urls ∈ clause_objs → web-steal NON scatta → find_urls preservato.
    cat = _cat("find_files", "find_urls", "read_urls_html",
               "find_files_github", "read_files_github")
    out = _run(["find_files", "find_urls", "read_urls_html"],
               "leggi i readme su github e cerca sul web notizie su rust",
               _intent("read", "files",
                       actions=[{"verb": "read", "object": "files"},
                                {"verb": "find", "object": "urls"}]), cat)
    assert "find_files_github" in out
    assert "find_urls" in out          # la clausola web sopravvive


def test_single_url_github_read_preserved():
    # FLAW #2 dei giudici: «leggi github.com/o/r/blob/F.md» = single-URL read.
    # URL esplicito → web-steal NON scatta → read_urls_html preservato.
    cat = _cat("read_urls_html", "find_files_github", "read_files_github")
    out = _run(["read_urls_html", "read_files_github"],
               "leggi https://github.com/brunialti/metnos/blob/main/README.md",
               _intent("read", "files"), cat)
    assert "read_urls_html" in out


def test_tutti_scivolamento_object_urls_web_suppressed():
    """T2 (causa: «tutti» allarga l'object all'accezione-web di «file»). Quando
    l'intent SCIVOLA a object=urls ma un produttore github-file è presente nel
    pool (dal prefilter) e «github» è marker attivo, l'accezione-web è VINCOLATA
    dal provider → web soppresso ANCHE con object=urls. «su github» dice che «i
    file» sono su github, non sul web. Deterministico (prima: l'LLM sceglieva)."""
    cat = _cat("find_files", "find_urls", "read_urls_html",
               "find_files_github", "read_files_github")
    # il prefilter porta find_files_github nel pool anche con intent.object=urls
    out = _run(["find_files_github", "find_urls", "read_urls_html"],
               "riassumi tutti i file readme.md su github nel repo brunialti/metnos",
               _intent("find", "urls"), cat)
    assert "find_files_github" in out
    assert "find_urls" not in out and "read_urls_html" not in out


def test_web_genuine_no_provider_marker_untouched():
    """T3 (NON deve rompersi): «cerca articoli sul web su rust» — nessun marker
    provider → `active_provider_suffixes`=[] → web-steal early-return → web
    intatto. (find_files_github può essere nel pool dal prefilter per affinità di
    «cerca»: innocuo, l'invariante è che il WEB non sia soppresso.)"""
    cat = _cat("find_urls", "read_urls_html", "find_files_github")
    out = _run(["find_files_github", "find_urls", "read_urls_html"],
               "cerca articoli sul web su rust async",
               _intent("find", "urls"), cat)
    assert "find_urls" in out and "read_urls_html" in out  # web preservato


# ── dormancy + safety ───────────────────────────────────────────────────────

def test_recruit_respects_dormancy():
    cat = [_exec("find_files"), _exec("find_files_github", dormant=True)]
    out = _run(["find_files"], "conta i file su github",
               _intent("find", "files"), cat)
    assert "find_files_github" not in out and "find_files" in out


def test_no_github_in_catalog_noop():
    # bench invariant: catalog repo-only (no *_github) → no-op, no exception.
    cat = _cat("find_files", "find_urls")
    out = _run(["find_files", "find_urls"], "conta i file su github",
               _intent("find", "files"), cat)
    assert "find_files" in out and "find_urls" in out


def test_never_empties_pool():
    cat = _cat("find_urls", "find_files_github")
    out = _run(["find_urls"], "conta i file su github",
               _intent("find", "files"), cat)
    assert out  # mai vuoto


# ── generalizza oltre github ────────────────────────────────────────────────

def test_google_workspace_generalizes():
    cat = _cat("read_events", "read_events_google_workspace")
    out = _run(["read_events"], "leggi gli eventi su google calendar",
               _intent("read", "events"), cat)
    assert "read_events_google_workspace" in out
    assert "read_events" not in out


def test_idempotent_second_pass():
    cat = _cat("find_files", "find_files_github")
    q = "conta i file su github"
    one = _run(["find_files"], q, _intent("find", "files"), cat)
    two = _run(one, q, _intent("find", "files"), cat)
    assert sorted(two) == sorted(one)
