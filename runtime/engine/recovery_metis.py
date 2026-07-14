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

from .types import (Intent, Framework, StepSpec, RunResult,
                    result_error_classes)
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
# Dominio `sites`: sessione autenticata + path di lettura PROPRIO (`read_sites`).
# `read_urls_html` fa un GET HTTP SENZA cookie di sessione: iniettarlo su un turno
# sites e' scorretto (leggerebbe la pagina pubblica/login) E ri-triggera un
# `open_sites` ridondante → `quota_exceeded` (turn 4769cf88). Il content-fetch
# recovery NON si applica ai turni sites: fail-honest, il consumer riporta il
# vuoto reale.
_SITES_PRODUCERS = frozenset({
    "open_sites", "login_sites", "read_sites", "act_sites", "delete_sites",
})


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

        # 1.ter DIRECTORY passata a un consumer di FILE («cancella i file
        # nella cartella X» → delete_files(paths=[X])): l'executor rifiuta
        # onestamente (ERR_PATH_WRONG_TYPE expected=file actual=directory,
        # campi STRUTTURATI nei failed[] — funziona anche per path su device
        # remoto, dove uno stat locale è impossibile). Ricostruisce
        # [find_files(base_path=<dir>), <stesso tool>(from_step=N)].
        corrected_dir = self._fix_dir_passed_as_file(failed_run, intent, catalog)
        if corrected_dir is not None:
            log.info("MetisRecovery: espansa directory via find_files "
                     "(wrong_type_dir)")
            return corrected_dir

        # 1.quater GLOB passato come path literal («cancella i file in X» →
        # delete_files(paths=["X/*"]) → ERR_PATH_NOT_FOUND). §2.4 dominio
        # aperto = wildcard tollerate: il backend NUOVO le espande da solo,
        # questo copre il runtime device STANTIO. dirname/basename = string
        # ops pure (niente stat: il path può stare su un device remoto);
        # find_files arbitra l'esistenza del parent.
        corrected_glob = self._fix_glob_passed_as_path(failed_run, intent,
                                                       catalog)
        if corrected_glob is not None:
            log.info("MetisRecovery: espanso glob via find_files "
                     "(glob_not_found)")
            return corrected_glob

        # 1.quinquies Precursore open_sites mancante (spec sites F1): un consumer
        # del dominio `sites` (login/read_sites) invocato SENZA sessione = il
        # proposer è andato diretto al consumer saltando l'apertura. Ricostruisce
        # la catena canonica F1 dall'URL nella query.
        corrected_site = self._fix_needs_site_session(failed_run, query, intent,
                                                      catalog)
        if corrected_site is not None:
            log.info("MetisRecovery: inserito open_sites (needs_site_session)")
            return corrected_site

        # Gli executor del dominio sites implementano gia' retry bounded sul
        # proprio stato. Riproporre l'intera pipeline con una sessione ancora
        # valida duplicherebbe open/login e perderebbe il reason_code originale.
        if self._has_live_site_session(failed_run):
            log.info("MetisRecovery: sessione sites ancora valida; "
                     "nessun re-propose stateful")
            return None

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

    @staticmethod
    def _has_live_site_session(failed_run: RunResult) -> bool:
        if not failed_run.steps:
            return False
        last = failed_run.steps[-1]
        if not str(last.tool or "").endswith("_sites"):
            return False
        args = last.args if isinstance(last.args, dict) else {}
        has_session = bool(args.get("session_id") or args.get("session_ids")
                           or args.get("entries"))
        if not has_session:
            return False
        result = last.result if isinstance(last.result, dict) else {}
        classes = set(result_error_classes(result))
        if classes & {"session_lost", "session_expired"}:
            return False
        items = result.get("entries") or result.get("results") or []
        if isinstance(items, list) and any(
                isinstance(item, dict) and item.get("session_closed") is True
                for item in items):
            return False
        return True

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
        # Guard dominio sites: un turno con sessione autenticata ha gia' il suo
        # path di contenuto; read_urls_html (GET senza cookie) non e' applicabile
        # e forzerebbe un open_sites ridondante (quota). Fail-honest.
        if any((s.tool or "") in _SITES_PRODUCERS for s in failed_run.steps):
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

    def _fix_needs_site_session(self, failed_run: RunResult, query: str,
                                intent: Intent,
                                catalog: Optional[list]) -> Optional[Framework]:
        """Un consumer del dominio `sites` (login_sites/read_sites) invocato
        SENZA sessione (session_ids/from_step/entries) = manca il precursore
        `open_sites` (il proposer è andato diretto al consumer; il PLANNER locale
        Qwen non incatena i 3 step). Ricostruisce la catena canonica F1 a partire
        dall'URL esplicito nella query: open_sites → [login_sites] → [read_sites].
        Deterministico §7.9, gemello di `_fix_needs_file_discovery`.

        `delete_sites` (kill-switch) è ESCLUSO: opera su ids espliciti o
        all=true, non apre sessioni. Serve un URL http(s) nella query (senza,
        no-fire → re-propose)."""
        if not failed_run.steps:
            return None
        last = failed_run.steps[-1]
        if last.tool not in ("login_sites", "read_sites", "act_sites") or last.ok:
            return None
        a = last.args if isinstance(last.args, dict) else {}
        if any(a.get(k) for k in
               ("session_ids", "session_id", "from_step", "entries")):
            return None  # aveva una sessione → fallimento per altra causa
        if catalog is not None and not any(
                getattr(e, "name", None) == "open_sites" for e in catalog):
            return None
        import re as _re
        m = _re.search(r"https?://[^\s'\"<>]+", query or "")
        if m:
            url = m.group(0).rstrip(".,;)")
        else:
            m = _re.search(
                r"(?<![@\w])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                r"[a-z]{2,63})(?![\w])", query or "", _re.IGNORECASE)
            if not m:
                return None
            url = "https://" + m.group(1).lower()
        acts = getattr(intent, "actions", None) or []
        verbs = {(x.get("verb") or "").lower() for x in acts
                 if isinstance(x, dict)}
        want_login = "login" in verbs or last.tool == "login_sites"
        want_read = ("read" in verbs or "describe" in verbs
                     or last.tool == "read_sites")
        want_act = "act" in verbs or last.tool == "act_sites"
        steps = [StepSpec(tool="open_sites", args={"urls": [url]})]
        if want_login:
            login_args = ({k: v for k, v in a.items()
                           if k in ("domain", "form_hint")}
                          if last.tool == "login_sites" else {})
            domain = login_args.get("domain")
            if isinstance(domain, str) and domain.startswith(("http://", "https://")):
                try:
                    import urllib.parse as _urlparse
                    login_args["domain"] = _urlparse.urlsplit(domain).hostname or ""
                except ValueError:
                    login_args.pop("domain", None)
            login_args["from_step"] = len(steps)
            steps.append(StepSpec(tool="login_sites", args=login_args))
        if want_read:
            read_args = ({k: v for k, v in a.items()
                          if k in ("include_screenshot", "include_forms")}
                         if last.tool == "read_sites" else {})
            read_args["from_step"] = len(steps)
            steps.append(StepSpec(tool="read_sites", args=read_args))
        if want_act:
            act_args = {"from_step": len(steps)}
            for key in ("action", "value_ref"):
                if a.get(key) is not None:
                    act_args[key] = a[key]
            steps.append(StepSpec(tool="act_sites", args=act_args))
        if len(steps) == 1:
            return None  # solo open non è un recovery utile del consumer
        return Framework(steps=steps, final_message="")

    def _fix_dir_passed_as_file(self, failed_run: RunResult, intent: Intent,
                                catalog: Optional[list]) -> Optional[Framework]:
        """L'ultimo step ha ricevuto in `paths` una DIRECTORY dove servivano
        FILE (failed[] con error_code=ERR_PATH_WRONG_TYPE e actual=directory
        strutturato): l'intento parla dei file CONTENUTI. Ricostruisce
        [<step read-only precedenti>, find_files(base_path=<dir>),
        <stesso tool>(from_step=N)]. Deterministico §7.9: solo campi
        strutturati, mai parsing dell'error text (multi-lingua).

        Guard-rail:
        - intent.object == "dirs" → NO-FIRE: l'utente parlava della cartella
          in sé (misroute verso delete_files: la via giusta è delete_dirs,
          lasciata al re-propose col pool verb-filtered).
        - ok_count > 0 o failed misti → NO-FIRE: il re-run perderebbe traccia
          dei path già processati (§2.8).
        - più directory distinte → NO-FIRE (from_step punta a UN solo step).
        - step precedenti con verbo mutating → NO-FIRE (ri-eseguirli
          duplicherebbe le mutazioni)."""
        if not failed_run.steps:
            return None
        last = failed_run.steps[-1]
        r = last.result if isinstance(last.result, dict) else {}
        a = last.args if isinstance(last.args, dict) else {}
        if last.ok or not isinstance(a.get("paths"), list):
            return None
        if getattr(intent, "object", "") == "dirs":
            return None
        if r.get("ok_count") or r.get("results"):
            return None
        failed = r.get("failed")
        if not (isinstance(failed, list) and failed):
            return None
        # Direzione dell'errore: expected=file. Con i campi strutturati la
        # certifica l'item; SENZA (runtime device stantio, shim non
        # content-addressed → il fix di local.py non è ancora arrivato) la
        # garantisce il NOME del tool (§2.2: <verb>_files consuma FILE) e
        # l'`actual` lo arbitra find_files a valle (path non-directory →
        # errore onesto, nessuna mutazione).
        tool_is_files = (last.tool or "").split("_")[1:2] == ["files"]
        dirs = set()
        for it in failed:
            if not (isinstance(it, dict)
                    and it.get("error_code") == "ERR_PATH_WRONG_TYPE"
                    and it.get("path")):
                return None  # anche UN failed di altra natura → no-fire
            if "expected" in it or "actual" in it:
                if not (it.get("expected") == "file"
                        and it.get("actual") == "directory"):
                    return None
            elif not tool_is_files:
                return None
            dirs.add(str(it["path"]))
        if len(dirs) != 1:
            return None
        if catalog is not None and not any(
                getattr(e, "name", None) == "find_files" for e in catalog):
            return None
        _MUTATING = ("delete", "move", "write", "create", "send", "share",
                     "order", "change", "extract", "undo", "admin")
        steps: list[StepSpec] = []
        for s in failed_run.steps[:-1]:
            if (s.tool or "").split("_")[0] in _MUTATING:
                return None
            steps.append(StepSpec(tool=s.tool, args=self._clean_args(s.args)))
        base = next(iter(dirs))
        ff_idx = len(steps) + 1
        steps.append(StepSpec(tool="find_files", args={"base_path": base}))
        consumer_args = self._clean_args(last.args)
        consumer_args.pop("paths", None)
        consumer_args["from_step"] = ff_idx
        steps.append(StepSpec(tool=last.tool, args=consumer_args))
        # Clausola «...E LE DIRECTORY» (bug live 6/7: le sottodirectory non
        # venivano MAI toccate — find_files è files-only): se l'intent porta
        # ANCHE {delete, dirs}, accoda find_dirs→delete_dirs DOPO lo
        # svuotamento dei file. Default delete_dirs (niente force): le dir
        # non vuote (es. system file rifiutati da delete_files) falliscono
        # per-item ONESTE, mai wipe implicito (§2.9).
        if last.tool == "delete_files" and any(
                (a or {}).get("verb") == "delete"
                and (a or {}).get("object") == "dirs"
                for a in (getattr(intent, "actions", None) or [])):
            fd_idx = len(steps) + 1
            steps.append(StepSpec(tool="find_dirs",
                                  args={"base_path": base}))
            steps.append(StepSpec(tool="delete_dirs",
                                  args={"from_step": fd_idx}))
        return Framework(steps=steps, final_message="")

    def _fix_glob_passed_as_path(self, failed_run: RunResult, intent: Intent,
                                 catalog: Optional[list]) -> Optional[Framework]:
        """L'ultimo step (consumer *_files con arg `paths`) ha ricevuto
        path GLOB literal (`*`/`?`) falliti ERR_PATH_NOT_FOUND. Ricostruisce
        [<prefix read-only>, find_files(base_path=<parent>, patterns=[...],
        recursive=false), <tool>(from_step=N)]. recursive=false = semantica
        fedele al glob (un solo livello). Vincoli come _fix_dir_passed_as_file
        + parent UNICO senza wildcard."""
        if not failed_run.steps:
            return None
        last = failed_run.steps[-1]
        r = last.result if isinstance(last.result, dict) else {}
        a = last.args if isinstance(last.args, dict) else {}
        if last.ok or not isinstance(a.get("paths"), list):
            return None
        if (last.tool or "").split("_")[1:2] != ["files"]:
            return None
        if getattr(intent, "object", "") == "dirs":
            return None
        if r.get("ok_count") or r.get("results"):
            return None
        failed = r.get("failed")
        if not (isinstance(failed, list) and failed):
            return None
        import ntpath
        import posixpath
        parents, patterns = set(), []
        for it in failed:
            p = it.get("path") if isinstance(it, dict) else None
            if not (isinstance(it, dict)
                    and it.get("error_code") == "ERR_PATH_NOT_FOUND"
                    and isinstance(p, str)
                    and any(c in p for c in "*?")):
                return None
            # Split OS-agnostico: il path può essere di un device Windows
            # mentre il server è POSIX (e viceversa).
            mod = ntpath if ("\\" in p or ":" in p[:3]) else posixpath
            parent, base = mod.dirname(p), mod.basename(p)
            if (not parent or not base
                    or any(c in parent for c in "*?")):
                return None
            parents.add(parent)
            patterns.append(base)
        if len(parents) != 1:
            return None
        if catalog is not None and not any(
                getattr(e, "name", None) == "find_files" for e in catalog):
            return None
        _MUTATING = ("delete", "move", "write", "create", "send", "share",
                     "order", "change", "extract", "undo", "admin")
        steps: list[StepSpec] = []
        for s in failed_run.steps[:-1]:
            if (s.tool or "").split("_")[0] in _MUTATING:
                return None
            steps.append(StepSpec(tool=s.tool, args=self._clean_args(s.args)))
        ff_idx = len(steps) + 1
        steps.append(StepSpec(tool="find_files",
                              args={"base_path": next(iter(parents)),
                                    "patterns": sorted(set(patterns)),
                                    "recursive": False}))
        consumer_args = self._clean_args(last.args)
        consumer_args.pop("paths", None)
        consumer_args["from_step"] = ff_idx
        steps.append(StepSpec(tool=last.tool, args=consumer_args))
        return Framework(steps=steps, final_message="")

    @staticmethod
    def _clean_args(args: dict) -> dict:
        """Rimuove placeholder runtime (`_actor`...), `entries` materializzate
        e `from_step` (ricalcolato), tenendo gli arg di dominio."""
        if not isinstance(args, dict):
            return {}
        return {k: v for k, v in args.items()
                if not k.startswith("_") and k not in ("entries", "from_step")}
