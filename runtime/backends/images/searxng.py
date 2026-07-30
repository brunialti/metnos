"""Ricerca testuale di immagini sul web tramite il SearXNG locale.

Questo backend copre la discovery ``testo -> immagini``. La ricerca inversa
``immagine -> immagini/pagine simili`` resta nel backend Google Vision: i due
modi condividono l'executor pubblico ma non credenziali o semantica.
"""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
import json
import re

from messages import get as _msg
from services_registry import endpoint as _service_endpoint
from agentic_executor import (AgenticContext, AgenticLimits, AgenticProposal,
                               run_bounded_sync)
import prompt_loader


_TIMEOUT_S = 20.0
_MAX_QUERIES = 10


def _queries(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        query = item.strip()
        if query and query not in out:
            out.append(query)
    return out


def _http_url(value) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value if value.startswith(("http://", "https://")) else ""


def _basename(url: str, rank: int) -> str:
    path = urllib.parse.urlsplit(url).path.rstrip("/")
    name = urllib.parse.unquote(path.rsplit("/", 1)[-1]) if path else ""
    return (name or f"image-{rank}")[:120]


def _display_title(raw: dict, image_url: str) -> str:
    """Produce un'etichetta non vuota senza inventare contenuto."""
    title = str(raw.get("title") or "").strip()
    if title:
        return title
    snippet = str(raw.get("content") or "").strip()
    if snippet:
        return snippet
    parsed = urllib.parse.urlsplit(image_url)
    host = parsed.hostname or ""
    path = urllib.parse.unquote(parsed.path.rstrip("/")).split("/")[-1]
    return path or host or image_url


def _query_tokens(query: str) -> set[str]:
    return {token for token in re.findall(r"[\w-]+", query.lower())
            if len(token) >= 3}


def _lexical_relevance(entry: dict, tokens: set[str]) -> int:
    text = " ".join(str(entry.get(key) or "") for key in (
        "title", "snippet", "source", "page_url")).lower()
    return sum(token in text for token in tokens)


def _agentic_rerank(entries: list[dict], query: str, lang: str) -> list[dict]:
    """Semantic fallback when deterministic metadata has no lexical signal."""
    if len(entries) < 4:
        return entries
    tokens = _query_tokens(query)
    if tokens and any(_lexical_relevance(entry, tokens) for entry in entries):
        ranked = sorted(
            enumerate(entries),
            key=lambda pair: (-_lexical_relevance(pair[1], tokens), pair[0]),
        )
        return [pair[1] for pair in ranked]

    by_id = {f"i{idx}": entry for idx, entry in enumerate(entries, 1)}
    observed = [{
        "id": ident,
        "title": entry.get("title") or "",
        "snippet": entry.get("snippet") or "",
        "source": entry.get("source") or entry.get("engine") or "",
    } for ident, entry in by_id.items()]
    context = AgenticContext(
        goal={"operation": "semantic_rerank", "query": query},
        observed=observed,
        constraints={"allowed_ids": list(by_id)},
    )

    def propose(ctx):
        try:
            from llm_router import LLMRouter
            provider = LLMRouter().provider("fast")
            if getattr(provider, "mode", "") != "local":
                return None
            prompt = prompt_loader.get(
                "agentic_image_rerank", lang,
                query_json=json.dumps(query, ensure_ascii=False),
                candidates_json=json.dumps(ctx.observed, ensure_ascii=True),
            )
            result = provider.chat(
                "", prompt, max_tokens=128, temperature=0, think=False)
            raw = str(getattr(result, "text", "") or "").strip()
            if raw.startswith("```"):
                raw = "\n".join(raw.splitlines()[1:-1]).strip()
            payload = json.loads(raw)
            ids = payload.get("ids") if isinstance(payload, dict) else None
            return AgenticProposal(ids) if isinstance(ids, list) else None
        except Exception:
            return None

    def valid(proposal, ctx):
        ids = proposal.action
        return (isinstance(ids, list) and bool(ids)
                and len(ids) == len(set(ids))
                and all(isinstance(ident, str) and ident in by_id
                        for ident in ids))

    def execute(proposal, _ctx):
        selected = [by_id[ident] for ident in proposal.action]
        selected_ids = set(proposal.action)
        return selected + [entry for ident, entry in by_id.items()
                           if ident not in selected_ids]

    outcome = run_bounded_sync(
        context=context, propose=propose, execute=execute, validate=valid,
        limits=AgenticLimits(max_attempts=1),
        postcondition=lambda result, _ctx: (
            isinstance(result, list) and len(result) == len(entries)),
    )
    return outcome.result if outcome.status == "completed" else entries


def find_images_by_text(args: dict) -> dict:
    """Cerca immagini per una o piu' query testuali.

    Ogni risultato resta un record distinto con URL dell'immagine e della
    pagina sorgente. La deduplica avviene sull'URL immagine, preservando
    l'ordine/ranking restituito da SearXNG.
    """
    queries = _queries((args or {}).get("queries"))
    if not queries:
        return {
            "ok": False,
            "error_code": "ERR_ARG_INVALID",
            "error": _msg("ERR_ARG_INVALID", arg="queries",
                          reason="non-empty list[str]"),
            "error_class": "invalid_args",
            "entries": [],
        }
    try:
        max_results = max(1, min(50, int((args or {}).get("max_results") or 10)))
    except (TypeError, ValueError):
        return {
            "ok": False,
            "error_code": "ERR_ARG_INVALID",
            "error": _msg("ERR_ARG_INVALID", arg="max_results",
                          reason="integer in 1..50"),
            "error_class": "invalid_args",
            "entries": [],
        }

    requested = len(queries)
    queries = queries[:_MAX_QUERIES]
    base = _service_endpoint("searxng")
    entries: list[dict] = []
    errors: list[dict] = []
    seen_images: set[str] = set()

    for query in queries:
        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "categories": "images",
            "language": "all",
        })
        request = urllib.request.Request(
            f"{base}/search?{params}",
            headers={"User-Agent": "metnos-image-search/1.0"},
        )
        try:
            import json
            with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            error_class = (
                "rate_limited" if exc.code == 429
                else "server_error" if 500 <= exc.code < 600
                else "network"
            )
            errors.append({"query": query, "error": str(exc),
                           "error_class": error_class})
            continue
        except (OSError, ValueError, urllib.error.URLError) as exc:
            errors.append({"query": query, "error": str(exc),
                           "error_class": "network"})
            continue

        used_for_query = 0
        for raw in payload.get("results") or []:
            if not isinstance(raw, dict):
                continue
            image_url = _http_url(raw.get("img_src"))
            if not image_url or image_url in seen_images:
                continue
            seen_images.add(image_url)
            used_for_query += 1
            try:
                score = float(raw.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            entries.append({
                "query": query,
                "title": _display_title(raw, image_url),
                "image_url": image_url,
                "thumbnail_url": _http_url(raw.get("thumbnail_src")),
                "page_url": _http_url(raw.get("url")),
                "snippet": str(raw.get("content") or "").strip(),
                "source": str(raw.get("source") or "").strip(),
                "engine": str(raw.get("engine") or "").strip(),
                "resolution": str(raw.get("resolution") or "").strip(),
                "score": round(score, 6),
            })
            if used_for_query >= max_results:
                break

    response_lang = str((args or {}).get("_lang") or "it").split("-", 1)[0].lower()
    reranked: list[dict] = []
    for query in queries:
        reranked.extend(_agentic_rerank(
            [entry for entry in entries if entry.get("query") == query],
            query, response_lang))
    entries = reranked

    attachments: list[dict] = []
    for rank, entry in enumerate(entries, start=1):
        url = entry["image_url"]
        attachment = {
            "kind": "image",
            "url": url,
            "thumbnail_url": entry.get("thumbnail_url") or "",
            "basename": _basename(url, rank),
            "score": entry["score"],
        }
        if entry["title"]:
            attachment["caption"] = entry["title"]
        attachments.append(attachment)

    out = {
        "ok": bool(entries) or not errors,
        "mode": "text_search",
        "entries": entries,
        "ok_count": len(entries),
        "errors": errors,
        "attachments": attachments,
    }
    if requested > _MAX_QUERIES:
        out.update({
            "truncated": True,
            "truncated_what": "queries",
            "used": _MAX_QUERIES,
            "available_total": requested,
            "cap_field": "queries",
            "cap_value": _MAX_QUERIES,
        })
    return out
