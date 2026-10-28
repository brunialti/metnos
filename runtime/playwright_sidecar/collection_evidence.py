"""Evidence of one collection kept on the browser session entry.

The only writer of the retained pages, the row-to-detail edges and the
details observed empty; the collection search and the readers go through
these functions instead of editing the lists in place (modularity review
21/10/2026: the same edge was appended in five places).
"""
from __future__ import annotations

import sites_collection_sources

PAGES = "collected_pages"
VIEWS = "collection_record_views"
EMPTY = "collection_empty_details"


def retain(entry: dict, snapshot: dict) -> bool:
    """Keep an observed page with text; True when it has text."""
    collected = entry.setdefault(PAGES, [])
    if not snapshot.get("text"):
        return False
    sites_collection_sources.retain_snapshot(collected, snapshot)
    return True


def add_view(entry: dict, parent: dict, detail: str) -> None:
    """The row `parent["record"]` of page `parent["parent"]` opens `detail`."""
    if parent["parent"] == detail:
        return
    edge = {"parent": parent["parent"], "record": parent["record"], "detail": detail}
    edges = entry.setdefault(VIEWS, [])
    if edge not in edges:
        edges.append(edge)


def add_empty(entry: dict, parent: dict, *, aside: bool = False) -> bool:
    """The row's detail was observed and held nothing; True when new. Seen in
    a second tab only, it is not conclusive for the row: a tab may render an
    empty shell where the site keeps state per tab, or before its data
    arrives (real run bp1, ENI 23/10: an invoice lost that way)."""
    visit = {"parent": parent["parent"], "record": parent["record"], "aside": bool(aside)}
    empty = entry.setdefault(EMPTY, [])
    if visit in empty:
        return False
    empty.append(visit)
    return True


def _in_context(entry: dict, items, context, record: str) -> bool:
    pages = {p.get("key"): p for p in entry.get(PAGES, []) if isinstance(p, dict)}
    return any(isinstance(item, dict) and item.get("record") == record
               and item.get("parent") in pages and pages[item["parent"]].get("context") == context
               for item in items)


def detail_observed(entry: dict, context, record: str) -> bool:
    """A successful detail navigation from this row in the same context, or
    an observed detail that held nothing."""
    return _in_context(entry, [*entry.get(VIEWS, []), *entry.get(EMPTY, [])], context, record)


def detail_empty(entry: dict, context, record: str) -> bool:
    """This row's detail was observed in the same context and held nothing."""
    return _in_context(entry, entry.get(EMPTY, []), context, record)


def empty_in_second_tab(entry: dict, parent: dict) -> bool:
    """The row's detail held nothing in a second tab only, never in place nor
    with content, in the context of the page `parent` names."""
    pages = {p.get("key"): p for p in entry.get(PAGES, []) if isinstance(p, dict)}
    page = pages.get(parent.get("parent"))
    if page is None:
        return False
    context, record = page.get("context"), parent.get("record")
    items = [item for item in entry.get(EMPTY, []) if isinstance(item, dict)]
    return (_in_context(entry, [i for i in items if i.get("aside")], context, record)
            and not _in_context(entry, [i for i in items if not i.get("aside")], context, record)
            and not _in_context(entry, entry.get(VIEWS, []), context, record))
