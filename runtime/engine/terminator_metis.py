"""engine/terminator_metis.py — MetisTerminator (engine v2 metis).

Migliora SimpleTerminator: quando il turno non si chiude con una risposta ma
ESISTONO risultati parziali utili (es. find_urls ha trovato URL+titoli+snippet
ma la pipeline non è arrivata alla sintesi), li PRESENTA invece di un secco
"Pipeline malformata". §2.8: niente silenzio su lavoro reale già fatto.

Niente LLM (la firma `explain` non riceve `llm_call`): presentazione
deterministica delle entries. Stringhe user-facing via i18n DB (§11):
riusa `MSG_SEARCH_PARTIAL_OR_INTERRUPTED`. Fallback al template SimpleTerminator
quando non c'è nessun risultato parziale.
"""
from __future__ import annotations

import logging
from typing import Optional

from .types import Intent, RunResult
from .terminator import TerminatorResponse, SimpleTerminator, _record_lacuna

log = logging.getLogger(__name__)


class MetisTerminator:
    def explain(self, *, query: str, intent: Intent,
                failed_run: Optional[RunResult],
                error_class: str = "") -> TerminatorResponse:
        entries = self._best_entries(failed_run)
        if entries:
            from messages import get as _msg
            # HTML→testo: body_text/snippet di una pagina possono contenere
            # markup grezzo (es. SPA non parsata) — mai user-facing (§2.8).
            from output_format import _strip_html_to_text
            header = _msg("MSG_SEARCH_PARTIAL_OR_INTERRUPTED", n=len(entries))
            lines = []
            for e in entries[:10]:
                if not isinstance(e, dict):
                    continue
                title = _strip_html_to_text(
                    str(e.get("title") or e.get("url") or "").strip())[:90]
                url = str(e.get("url") or "").strip()
                snippet = _strip_html_to_text(
                    str(e.get("snippet") or e.get("body_text") or "").strip())
                snippet = " ".join(snippet.split())[:140]
                bit = f"- {title}" if title else "-"
                if url and url != title:
                    bit += f" ({url})"
                if snippet:
                    bit += f"\n  {snippet}"
                lines.append(bit)
            text = header + "\n" + "\n".join(lines)
            lid = _record_lacuna(query, intent, error_class or "wrong_args",
                                 "partial_results_presented", "present_partial")
            return TerminatorResponse(
                final_text=text, root_cause="partial_results",
                suggested_action="", lacuna_id=lid)
        # Nessun risultato parziale → template onesto (SimpleTerminator).
        return SimpleTerminator().explain(
            query=query, intent=intent, failed_run=failed_run,
            error_class=error_class)

    @staticmethod
    def _best_entries(failed_run: Optional[RunResult]) -> list:
        """Lista entries più a valle (più arricchita) con almeno un `url`.
        Lo step più avanti nella pipeline vince (es. read_urls_html con body
        batte find_urls metadata)."""
        if not failed_run or not failed_run.steps:
            return []
        best: list = []
        for s in failed_run.steps:
            r = s.result if isinstance(s.result, dict) else {}
            ents = r.get("entries")
            if (isinstance(ents, list) and ents
                    and isinstance(ents[0], dict) and ents[0].get("url")):
                best = ents
        return best
