"""engine/recovery_metis.py — MetisRecovery (engine v2 metis).

Recovery LLM-aware + correzione deterministica, superiore a SimpleRecovery.

SimpleRecovery (fallback) ESCLUDE il tool dell'ultimo step e ri-propone: per
errori come `needs_content_fetch` (describe_entries riceve entries metadata-only
e chiede il contenuto) è CONTROPRODUCENTE — describe_entries non è il problema,
manca uno step `read_urls_html` che scarichi il body.

MetisRecovery:
 1. **Correzione deterministica** (§7.9 code>LLM) per segnali espliciti
    dell'executor: `needs_urls_html`/`needs_content_fetch` → inserisce
    `read_urls_html` fra il produttore di URL (find_urls/get_urls) e il
    consumer di contenuto fallito, ricablando il consumer sullo step di lettura.
 2. **Re-propose** per gli altri errori recuperabili escludendo SOLO il
    framework_hash fallito (NON il tool): il Proposer multi-candidato ha un'altra
    chance sull'intero pool, con un hint testuale dell'errore se supportato.

Contratto identico a `recovery.Recovery` (vedi recovery.py::Recovery).
"""
from __future__ import annotations

import logging
from typing import Optional, Callable

from .types import Intent, Framework, StepSpec, RunResult
from .recovery import classify_error, is_recoverable

log = logging.getLogger(__name__)

# Produttori di URL: i loro entries hanno `url` ma NON `body_text`.
# Consumer di contenuto che possono segnalare needs_content_fetch.
_CONTENT_CONSUMERS = (
    "describe_entries", "classify_entries", "filter_entries",
    "compute_entries", "compare_entries",
)
# Reader su filesystem: richiedono una lista di path. Se invocati SENZA
# path/paths/from_step/entries manca il precursore find_files (bug q29 5/6).
_FILE_READERS = (
    "read_files", "read_files_pdf", "read_files_html", "read_files_ocr",
)


class MetisRecovery:
    def recover(self, *, failed_run: RunResult, query: str, intent: Intent,
                pool: list[str], proposer,
                llm_call: Optional[Callable] = None,
                lang: str = "it",
                catalog: Optional[list] = None) -> Optional[Framework]:
        err = classify_error(failed_run)
        if not is_recoverable(err):
            return None  # out_of_scope → terminator

        # 1. Correzione deterministica su segnale esplicito dell'executor.
        corrected = self._fix_needs_content_fetch(failed_run, catalog)
        if corrected is not None:
            log.info("MetisRecovery: inserito read_urls_html (needs_content_fetch)")
            return corrected

        # 1.bis Precursore find_files mancante: un file-reader invocato senza
        # alcuna sorgente-path (proposer ha saltato find_files). Ricostruisce
        # [find_files(base_path=<dir dalla query>), reader(from_step=1)].
        corrected_ff = self._fix_needs_file_discovery(failed_run, query, catalog)
        if corrected_ff is not None:
            log.info("MetisRecovery: inserito find_files (needs_file_discovery)")
            return corrected_ff

        # 2. Re-propose escludendo SOLO il framework fallito (NON il tool: a
        #    differenza di SimpleRecovery, che escludendo il tool dell'ultimo
        #    step peggiora i casi tipo needs_content_fetch). Il Proposer
        #    multi-candidato ha un'altra chance sull'intero pool.
        failed_hash = failed_run.framework_hash
        excluded = {failed_hash} if failed_hash else set()
        try:
            return proposer.propose(
                query=query, intent=intent, pool=pool,
                excluded_hashes=excluded, llm_call=llm_call, lang=lang,
                catalog=catalog)
        except Exception as ex:
            log.warning("MetisRecovery re-propose failed: %r", ex)
            return None

    # ── correzione deterministica ─────────────────────────────────────────
    def _fix_needs_content_fetch(self, failed_run: RunResult,
                                 catalog: Optional[list]) -> Optional[Framework]:
        """Se l'ultimo step (content consumer) ha ricevuto entries metadata-only
        e chiede il contenuto, ricostruisce: [...producer, read_urls_html(
        from_step=producer), consumer(from_step=read)].

        NB: NON si filtra sul `pool` del Proposer (verb-filtered a find_* →
        read_urls_html assente): l'executor invoca PER NOME, il pool serve solo
        al prompt del Proposer. Si verifica solo che read_urls_html esista nel
        catalog (canonical, sempre presente)."""
        if not failed_run.steps:
            return None
        if catalog is not None and not any(
                getattr(e, "name", None) == "read_urls_html" for e in catalog):
            return None
        last = failed_run.steps[-1]
        r = last.result if isinstance(last.result, dict) else {}
        signals = (r.get("needs_urls_html")
                   or r.get("error_class") == "needs_content_fetch")
        if not signals or last.tool not in _CONTENT_CONSUMERS:
            return None
        # Trova lo step (1-based) che ha prodotto entries con `url` ma SENZA
        # `body_text` (cioè un produttore di URL, non un read già fatto).
        url_step = None
        for i, s in enumerate(failed_run.steps):
            sr = s.result if isinstance(s.result, dict) else {}
            ents = sr.get("entries")
            if (isinstance(ents, list) and ents
                    and isinstance(ents[0], dict)
                    and ents[0].get("url") and "body_text" not in ents[0]):
                url_step = i + 1
        if url_step is None:
            return None
        # Ricostruisci: step già eseguiti fino al produttore + read + consumer.
        steps: list[StepSpec] = []
        for i, s in enumerate(failed_run.steps):
            if i + 1 > url_step:
                break
            steps.append(StepSpec(tool=s.tool, args=self._clean_args(s.args)))
        read_idx = len(steps) + 1
        steps.append(StepSpec(tool="read_urls_html",
                              args={"from_step": url_step}))
        consumer_args = self._clean_args(last.args)
        consumer_args["from_step"] = read_idx
        steps.append(StepSpec(tool=last.tool, args=consumer_args))
        return Framework(steps=steps, final_message="")

    def _fix_needs_file_discovery(self, failed_run: RunResult, query: str,
                                  catalog: Optional[list]) -> Optional[Framework]:
        """Un file-reader (read_files/_pdf/_html/_ocr) invocato SENZA alcuna
        sorgente-path (path/paths/from_step/entries) = manca il precursore
        find_files (il proposer è andato diretto al reader). Ricostruisce
        [find_files(base_path=<dir assoluta dalla query>, patterns=[*.ext]),
        reader(from_step=1)]. Deterministico §7.9 (precursor universale).
        Bug q29 5/6: 'leggi i file .txt in /tmp/... e raggruppa' → read_files
        senza path → 'argomento obbligatorio mancante: path'."""
        if not failed_run.steps:
            return None
        last = failed_run.steps[-1]
        if last.tool not in _FILE_READERS or last.ok:
            return None
        a = last.args if isinstance(last.args, dict) else {}
        if any(a.get(k) for k in ("path", "paths", "from_step", "entries")):
            return None  # aveva un input → fallimento per altra causa
        # find_files è canonico (invocato PER NOME dall'executor): NON si gate
        # sul catalog/pool (che può essere verb-filtered a read/get e non
        # contenerlo) — stessa filosofia di _fix_needs_content_fetch.
        import os
        import re as _re
        # Estrai una directory ESISTENTE dalla query (path assoluto o ~).
        base = None
        for p in _re.findall(r"((?:~|/)[^\s'\";:,]+)", query or ""):
            cand = os.path.expanduser(p)
            if os.path.isdir(cand):
                base = p
                break
            parent = os.path.dirname(cand)
            if parent and os.path.isdir(parent):
                base = os.path.dirname(p) or p
                break
        if not base:
            return None
        ff_args = {"base_path": base, "recursive": True}
        m = _re.search(r"\.([a-z0-9]{1,5})\b", query or "", _re.IGNORECASE)
        if m:
            ff_args["patterns"] = [f"*.{m.group(1).lower()}"]
        steps = [
            StepSpec(tool="find_files", args=ff_args),
            StepSpec(tool=last.tool, args={"from_step": 1}),
        ]
        return Framework(steps=steps, final_message="")

    @staticmethod
    def _clean_args(args: dict) -> dict:
        """Rimuove placeholder runtime (`_actor`...), `entries` materializzate
        e `from_step` (ricalcolato), tenendo gli arg di dominio."""
        if not isinstance(args, dict):
            return {}
        return {k: v for k, v in args.items()
                if not k.startswith("_") and k not in ("entries", "from_step")}
