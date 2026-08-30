"""runtime.describe_entries — builtin LLM-augmented summariser.

Pattern terza categoria di executor (28/4/2026 sera, ratificato in
`feedback_llm_augmented_executors`): vive nel runtime, no manifest su
disco, no subprocess. Wrapper sottile sopra `runtime.llm_helpers.call_llm`:
seleziona un prompt template in base allo style, lascia tutto il
trasporto all'helper.

Style preset:
- 'by_importance' — segnale vs rumore, raggruppa per affinita'. Chiude
  con un'affermazione, MAI con una domanda all'utente (no-forced-response:
  non esiste dialogo pendente che accolga la risposta). Default per
  liste eterogenee (mail, file, eventi).
- 'by_relevance'  — risposta a una richiesta utente (`context`
  obbligatorio): cosa risponde alla domanda, cosa no.
- 'compact'       — una riga per entry (fallback enumerativo).

Override esplicito: passare `prompt_override` come system prompt
completo (lo style viene ignorato).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import os
import re
from string import Formatter
from typing import Mapping
import unicodedata

from llm_helpers import call_llm
from llm_workloads import tier_for
import i18n as _i18n
import prompt_loader
from messages import get as _msg
import detection_lexicon_seed_reconciliation as _reconciliation_lex

# Lista degli style preset disponibili. I prompt sono persistiti in
# `runtime/prompts/<lang>/describe_entries_<style>.j2` (ADR 0092 Phase 2)
# e caricati via `prompt_loader.get(role, lang, **vars)`.
STYLES = ("by_importance", "by_relevance", "compact")

# Cap DINAMICO verso il prompt LLM (12/6/2026, sostituisce il fisso
# _DESCRIBE_CAP=20). Il vincolo reale e' la DIMENSIONE serializzata del
# bundle — context bloat + il modello generalizza/si perde su bundle
# grossi — NON il numero di entries. Un cap a conteggio fisso tagliava
# per un solo elemento di troppo (es. 21 mail corte -> "1 fuori"),
# rischiando di perdere roba importante per nulla: 21 mail ~7 KB stanno
# larghe, il problema vero era il turn 98 KB con 100 entries scene-rumore
# (find_images_indices, diagnosi 8/5). Soluzione di classe (§7.3): pack
# greedy fino a un budget di caratteri (proxy dei token), con un tetto di
# sicurezza sul conteggio per evitare flood di entries minuscole. Sotto
# budget: nessun troncamento. Le entries oltre il cap NON sono dimenticate
# (`item_count` = totale + `truncated*` field §2.7). Override via env.
# Budget alzato 24K->48K (12/6/2026, Roberto): ~60 mail reali (~571-800 B/cad)
# stanno nel budget prima di troncarne una — il describe deterministico regge
# ~12-16K token, ben sotto il ctx 131072. Tunabile via env.
_DESCRIBE_MAX_CHARS = int(os.environ.get("METNOS_DESCRIBE_MAX_CHARS", "48000"))
# Band-aid (8/7, Roberto): budget di output scalato col numero di entry sulle
# query-lista (vedi sotto). ~tok per riga + tetto anti-runaway.
_DESCRIBE_TOKENS_PER_ENTRY = int(os.environ.get("METNOS_DESCRIBE_TOKENS_PER_ENTRY", "45"))
_DESCRIBE_MAX_TOKENS_CAP = int(os.environ.get("METNOS_DESCRIBE_MAX_TOKENS_CAP", "4000"))
_DESCRIBE_HARD_MAX = int(os.environ.get("METNOS_DESCRIBE_HARD_MAX", "200"))

# Map-reduce OVER-BUDGET (22/6/2026, Roberto «robusto, universale, efficiente»):
# quando il bundle sfora il budget, invece di troncare e DIMENTICARE la coda
# (prima N in ordine d'arrivo = arbitrario), si fa map-reduce GENERALE (§7.3,
# vale per mail/file/issue/processi):
#   MAP   — una passata `fast` PER ELEMENTO: resume breve + punteggio di
#           salienza (0-100). Una entry alla volta non sfora MAI il budget
#           (elimina il problema alla radice — Roberto), output corto, N volte.
#   REDUCE— una chiamata `middle` sintetizza i digest (piccoli → stanno nel budget),
#           ordinati per salienza; se i digest stessi sforano (N enorme),
#           ricorsione GERARCHICA (riassunto-di-riassunti) fino a convergenza.
# Copre tutte le entries FINO al cap anti-runaway (sotto). Scatta SOLO
# over-budget: il caso comune (sotto budget) resta la singola chiamata di
# prima, invariato.
# Determinismo §11: il path map-reduce e' N+1 chiamate HTTP fast
# (efficiente, no processo monouso ×N) → NON byte-riproducibile, dichiarato
# onestamente `meta.deterministic=False` (come il fallback HTTP §11).
_DESCRIBE_MAPREDUCE = os.environ.get("METNOS_DESCRIBE_MAPREDUCE", "1").strip() != "0"
_MR_MAX_DEPTH = int(os.environ.get("METNOS_DESCRIBE_MR_DEPTH", "3"))
# Cap ANTI-RUNAWAY del MAP (6/7/2026, Roberto «100 max, configurabile, con
# indicazione chiara all'utente»): il copre-tutto faceva UNA chiamata LLM per
# OGNI entry senza tetto — misurato live: «che file ci sono in /tmp?» → 1759
# chiamate in ~20 min, pipeline turni bloccata (serializza sul llama-server).
# Il MAP lavora le PRIME N entries in ordine d'arrivo (deterministico); il
# resto e' dichiarato ALL'UTENTE nel testo del summary (MSG_DESCRIBE_TRUNCATED,
# stesso pattern §2.8 del path single-call — il notice runtime salta i verbi
# PROCESSOR, quindi il testo e' il canale affidabile) + campi §2.7 nel result.
# 0 = illimitato (§2.4 0-as-placeholder, comportamento pre-cap).
_MR_MAX_ENTRIES = int(os.environ.get("METNOS_DESCRIBE_MR_MAX_ENTRIES", "100"))
# Campi-identita' da preservare nei digest (per la sintesi REDUCE + link
# section ADR 0119). Dominio-agnostici: mail (subject/from), file (path/name),
# issue/url (url/title), eventi (when/date).
_DIGEST_ID_FIELDS = ("subject", "from", "sender", "title", "name",
                     "url", "path", "date", "when", "account")

# Testo DETERMINISTICO per costruzione (12/6/2026): stessa lista di entries
# -> testo IDENTICO byte-a-byte su run ripetuti. Il path HTTP del llama-server
# condiviso NON e' riproducibile (stato di processo, vedi llm_helpers blocco
# DETERMINISTICA); describe passa deterministic=True a call_llm, che genera
# via processo llama-completion monouso (stesso GGUF, stesso template,
# temp=0, seed §11). NIENTE cache/template del contenuto: la sintesi resta
# LLM piena sui dati correnti. Fallback HTTP onesto se il path manca
# (meta.deterministic=False). Opt-out: METNOS_DESCRIBE_DETERMINISTIC=0.
_DESCRIBE_DETERMINISTIC = (
    os.environ.get("METNOS_DESCRIBE_DETERMINISTIC", "1").strip() != "0"
)


def _pack_entries(entries: list) -> tuple[list, bool]:
    """Greedy: include entries in ordine finche' la dimensione serializzata
    sta nel budget caratteri e non si supera il tetto di sicurezza sul
    conteggio. Almeno 1 entry passa sempre (anche se da sola sfora). Ritorna
    (visible, truncated)."""
    visible: list = []
    total_chars = 0
    for e in entries:
        try:
            sz = len(json.dumps(e, ensure_ascii=False))
        except Exception:
            sz = len(str(e))
        if visible and (total_chars + sz > _DESCRIBE_MAX_CHARS
                        or len(visible) >= _DESCRIBE_HARD_MAX):
            break
        visible.append(e)
        total_chars += sz
    return visible, len(visible) < len(entries)


def _fallback_resume(entry) -> str:
    """Resume deterministico senza LLM (fallback se la MAP fallisce)."""
    if isinstance(entry, dict):
        for k in ("body_text", "content", "body", "text", "snippet",
                  "subject", "title", "name"):
            v = entry.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()[:300]
    return json.dumps(entry, ensure_ascii=False)[:300]


# Tetto input per il MAP: per giudicare salienza + 1 frase NON serve la mail
# intera. Tagliare i campi testuali lunghi a ~1500 char accelera il prompt
# processing senza intaccare il giudizio (subject/mittente + incipit bastano).
# NB (22/6): il batch del MAP e' stato PROVATO e SCARTATO — su GPU seriale il
# tempo e' legato ai token totali, non al numero di chiamate (§7.4): batch =
# stesso lavoro, +complessita'. Per-mail e' piu' semplice (§7.2) e pari-veloce.
_MAP_FIELD_CHARS = int(os.environ.get("METNOS_DESCRIBE_MAP_FIELD_CHARS", "1500"))
_MAP_LONG_FIELDS = ("body_text", "content", "body", "text", "snippet", "html")


def _trim_for_map(entry):
    """Copia shallow dell'entry coi campi testuali lunghi troncati a
    _MAP_FIELD_CHARS — solo per il MAP, l'entry originale resta intatto."""
    if not isinstance(entry, dict):
        return entry
    out = dict(entry)
    for k in _MAP_LONG_FIELDS:
        v = out.get(k)
        if isinstance(v, str) and len(v) > _MAP_FIELD_CHARS:
            out[k] = v[:_MAP_FIELD_CHARS]
    return out


def _parse_salience(text: str, entry) -> tuple[int, str]:
    """Estrae (salienza 0-100, resume) dall'output MAP. Robusto §2.4: se il
    formato non torna, salienza neutra 50 + resume = testo/fallback."""
    score = 50
    m = re.search(r'(?:SALIENZA|SALIENCE)\s*[:=]\s*(\d{1,3})', text, re.I)
    if m:
        score = max(0, min(100, int(m.group(1))))
    m2 = re.search(r'(?:RIASSUNTO|SUMMARY)\s*[:=]\s*(.+)', text, re.I | re.S)
    resume = (m2.group(1).strip() if m2 else (text or "").strip())
    if not resume:
        resume = _fallback_resume(entry)
    return score, resume[:600]


def _map_one(entry, map_prompt: str) -> tuple[int, str]:
    """MAP di UN elemento: chiamata `fast` HTTP (efficiente, no processo
    monouso), input troncato, output corto. Ritorna (salienza, resume).
    Fail-open §2.8."""
    try:
        text, _meta = call_llm(
            [_trim_for_map(entry)], map_prompt,
            tier=tier_for("entries.describe.map"),
            max_tokens=140, deterministic=False,
            max_query_chars=_MAP_FIELD_CHARS + 2048)
    except Exception:
        return 50, _fallback_resume(entry)
    return _parse_salience(text, entry)


def _pre_aggregate(entries: list, cap: int):
    """Compressione DETERMINISTICA per liste over-cap OMOGENEE (§7.9, 10/7
    Roberto «deve estendere le entry da riassumere»): raggruppa per il campo
    con miglior compressione (es. 516 processi → ~gruppi per nome), sommando i
    campi numerici e contando le occorrenze. COPRE TUTTE le entries — nessuna
    coda dimenticata — senza il costo del MAP per-elemento (1 call LLM/entry).

    Ritorna (groups, group_field) o None se nessun campo comprime abbastanza
    (entries eterogenee/uniche → fallback al cap col notice, com'era).
    Group-key: campo string presente in ≥90% delle entries, con 2 ≤ n_unique ≤
    min(cap, total/2) — il migliore = quello che comprime di più."""
    total = len(entries)
    dicts = [e for e in entries if isinstance(e, dict)]
    if total < 2 or len(dicts) < int(total * 0.9):
        return None
    # candidati: chiavi string corte presenti in >=90% delle entries.
    # Esclusi i valori NON-categorici (date/timestamp/numeri-stringa: raggruppare
    # per started_at produce gruppi semanticamente vuoti — live 10/7).
    import re as _re
    from collections import Counter, defaultdict
    _non_categorical = _re.compile(r"^[\d\s\-:./TZ+,]+$")
    presence: Counter = Counter()
    for e in dicts:
        for k, v in e.items():
            if (isinstance(v, str) and v and len(v) <= 120
                    and not k.startswith("_")
                    and not _non_categorical.match(v)):
                presence[k] += 1
    # Chiave = la più INFORMATIVA che comprima abbastanza (10/7, live: con il
    # minimo si sceglieva `user`=2 gruppi — massima compressione, minima
    # informazione). Range: almeno 2× di compressione (uniq ≤ total/2), tetto
    # pratico max(cap, total/3) — se i gruppi sforano il budget, la describe
    # ricorsiva li gestisce (sono già ordinati per count).
    limit = min(max(cap, total // 3), max(2, total // 2))

    # Normalizzazione GENERICA dei valori (10/7, live: 579 processi → name 488
    # unici per i kworker/N:M — non comprime): tronca dal primo separatore
    # numerico (cifre, /, :, #, @) → «kworker/1:2»→«kworker», «python3»→
    # «python». Candidato DERIVATO quando il valore grezzo non comprime.
    def _norm_val(v: str) -> str:
        n = _re.sub(r"[\d/:#@].*$", "", v).strip("-_ .")
        return n or v

    best = None  # (n_unique, key, normalized) — vince il MASSIMO nel range
    for k, n in presence.items():
        if n < int(len(dicts) * 0.9):
            continue
        vals = [e.get(k) for e in dicts if isinstance(e.get(k), str)]
        uniq = len(set(vals))
        if 2 <= uniq <= limit:
            if best is None or uniq > best[0]:
                best = (uniq, k, False)
        elif uniq > limit:
            uniq_n = len({_norm_val(v) for v in vals})
            if 2 <= uniq_n <= limit and (best is None or uniq_n > best[0]):
                best = (uniq_n, k, True)
    if not best:
        return None
    gkey, _normalized = best[1], best[2]
    groups: dict = defaultdict(lambda: {"count": 0, "_nums": defaultdict(float)})
    for e in dicts:
        _gv = e.get(gkey)
        if _normalized and isinstance(_gv, str):
            _gv = _norm_val(_gv)
        g = groups[str(_gv)]
        g["count"] += 1
        for k, v in e.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) \
                    and not k.startswith("_") and k not in ("pid", "ppid"):
                g["_nums"][k] += v
    out = []
    for val, g in groups.items():
        row = {gkey: val, "count": g["count"]}
        for k, s in g["_nums"].items():
            row[f"{k}_sum"] = round(s, 1)
        out.append(row)
    # ordina per rilevanza deterministica: count, poi prima somma numerica
    out.sort(key=lambda r: (-r["count"], str(r.get(gkey))))
    return out, gkey


def _describe_map_reduce(entries: list, *, style: str, context: str,
                         data_kind, fmt: str, group_by, max_tokens: int,
                         health_context, mr_depth: int) -> dict:
    """Over-budget describe via map-reduce (§7.3, vedi blocco costanti).
    MAP per-elemento (resume + salienza) sulle PRIME `_MR_MAX_ENTRIES` entries
    (cap anti-runaway; 0 = tutte), ordina per salienza; ricorsione gerarchica
    se i digest sforano. Oltre il cap: nota chiara ALL'UTENTE nel summary +
    campi §2.7. NON byte-determ."""
    total = len(entries)
    capped = 0 < _MR_MAX_ENTRIES < total
    if capped:
        # Pre-aggregazione deterministica (10/7): se le entries sono OMOGENEE,
        # il group-by copre TUTTE le righe (516 processi → gruppi per nome con
        # count+somme) e il describe vede il quadro INTERO — niente coda
        # dimenticata, niente MAP per-elemento. Fallback al cap se eterogenee.
        agg = _pre_aggregate(entries, _MR_MAX_ENTRIES)
        if agg:
            groups, gkey = agg
            res = handle_describe_entries({
                "entries": groups, "style": style, "context": context,
                "data_kind": data_kind, "format": fmt, "group_by": group_by,
                "tier": tier_for("entries.describe.medium"), "max_tokens": max_tokens,
                "health_context": health_context,
            }, _mr_depth=mr_depth + 1, _deterministic=False)
            if isinstance(res, dict) and res.get("ok"):
                res["item_count"] = total
                res["aggregated_by"] = gkey
                res["aggregated_groups"] = len(groups)
                try:
                    note = _msg("MSG_DESCRIBE_AGGREGATED", total=total,
                                groups=len(groups), field=gkey)
                except Exception:
                    note = ""
                s = res.get("summary")
                if isinstance(s, str) and s.strip() and note:
                    res["summary"] = s.rstrip() + "\n\n" + note
                res["map_reduce"] = True
                res["deterministic"] = False
                return res
    mapped_entries = entries[:_MR_MAX_ENTRIES] if capped else entries
    map_prompt = prompt_loader.get("describe_map_salience", _i18n.current_lang(),
                                   context=context or "")
    digests: list = []
    for e in mapped_entries:
        score, resume = _map_one(e, map_prompt)
        d = {}
        if isinstance(e, dict):
            for k in _DIGEST_ID_FIELDS:
                if e.get(k) is not None:
                    d[k] = e[k]
        d["content"] = resume
        d["_salience"] = score
        digests.append(d)
    digests.sort(key=lambda x: x.get("_salience", 0), reverse=True)
    # REDUCE: describe normale sui digest (piccoli → singola chiamata; se
    # sforano ancora, ricorre map-reduce a mr_depth+1 = gerarchico). tier
    # workload `entries.describe.medium` → middle, non deterministico
    # (path efficiente).
    res = handle_describe_entries({
        "entries": digests, "style": style, "context": context,
        "data_kind": data_kind, "format": fmt, "group_by": group_by,
        "tier": tier_for("entries.describe.medium"), "max_tokens": max_tokens,
        "health_context": health_context,
    }, _mr_depth=mr_depth + 1, _deterministic=False)
    if isinstance(res, dict) and res.get("ok"):
        res["item_count"] = total
        if capped:
            # Indicazione CHIARA all'utente NEL TESTO (il summary e' il
            # final_message; il notice runtime salta i verbi PROCESSOR):
            # riuso del messaggio i18n del path single-call.
            try:
                note = _msg("MSG_DESCRIBE_TRUNCATED",
                            visible=len(mapped_entries),
                            hidden=total - len(mapped_entries),
                            cap=_MR_MAX_ENTRIES)
            except Exception:
                note = ""
            s = res.get("summary")
            if isinstance(s, str) and s.strip() and note:
                res["summary"] = s.rstrip() + "\n\n" + note
            elif note:
                res["summary"] = note
            res.update({
                "truncated": True,
                "truncated_what": "describe",
                "used": len(mapped_entries),
                "available_total": total,
                "cap_field": "METNOS_DESCRIBE_MR_MAX_ENTRIES",
                "cap_value": _MR_MAX_ENTRIES,
            })
        res["map_reduce"] = True
        res["mapped"] = len(mapped_entries)
        res["deterministic"] = False
    return res

_FORMATS = frozenset({"markdown", "html", "plain", "json", "bullet_list"})


def _format_directive(fmt: str) -> str:
    """Renderizza le istruzioni di formato nella lingua del turno.

    Le regole sono prompt versionati e traducibili, non stringhe applicative.
    Se una lingua nuova non ha ancora il proprio file, ``prompt_loader`` usa
    il template inglese ma gli passa comunque ``lang_name`` della lingua
    richiesta, così il modello non confonde la lingua del fallback con quella
    della risposta.
    """
    if fmt not in _FORMATS:
        return ""
    return prompt_loader.get(
        "describe_format", _i18n.current_lang(), format_name=fmt,
    )


def _auto_tier(entries: list) -> str:
    """Sceglie il tier in base alla dimensione del bundle serializzato.
    Heuristica conservativa: testi corti reggono col tier fast,
    contenuti medi usano middle, bundle grossi wise."""
    try:
        size = len(json.dumps(entries, ensure_ascii=False))
    except Exception:
        size = 0
    n = len(entries)
    if size < 5_000 and n <= 10:
        return tier_for("entries.describe.small")
    if size < 30_000 and n <= 50:
        return tier_for("entries.describe.medium")
    return tier_for("entries.describe")


_MAX_LINKS_APPENDED = 10


def _format_structured_entries(
        entries: list, fmt: str, group_by: str = "") -> str:
    """Render lossless per record estratti con schema bounded.

    Le chiavi interne di provenienza (prefisso ``_``) non diventano colonne;
    ogni altro campo non vuoto compare esattamente una volta per record.
    """
    import html

    def value_text(value) -> str:
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value)

    rows: list[str] = []
    json_rows: list[dict] = []
    group_field = None
    if group_by:
        try:
            from ordering_clause import resolve_field
            group_field = resolve_field(group_by, entries)
        except Exception:
            group_field = None
    # Un'intestazione come ``organizzazione: ?`` non aggiunge informazione
    # quando la chiave e' assente in TUTTO il corpus. Nei corpus misti il
    # gruppo senza valore resta invece visibile, cosi' la copertura incompleta
    # non viene nascosta.
    if group_field and not any(
            isinstance(entry, dict)
            and entry.get(group_field) not in (None, "", [], {})
            for entry in entries):
        group_field = None
    previous_group = object()
    for entry in entries:
        if not isinstance(entry, dict):
            fields = [("value", entry)]
        else:
            fields = [
                (str(key).replace("_", " "), value)
                for key, value in entry.items()
                if not str(key).startswith("_") and value not in (None, "", [], {})
            ]
        json_rows.append({key: value for key, value in fields})
        if group_field and isinstance(entry, dict):
            group_value = entry.get(group_field)
            group_value = "?" if group_value in (None, "") else str(group_value)
            if group_value != previous_group:
                if fmt == "html":
                    rows.append(
                        f"<h2>{html.escape(str(group_field))}: "
                        f"{html.escape(group_value)}</h2>")
                elif fmt == "plain":
                    rows.append(f"{group_field}: {group_value}")
                else:
                    rows.append(f"## {group_field}: {group_value}")
                previous_group = group_value
        if fmt == "html":
            body = "; ".join(
                f"<b>{html.escape(key)}</b>: {html.escape(value_text(value))}"
                for key, value in fields)
            rows.append(f"• {body}")
        elif fmt == "plain":
            rows.append("- " + "; ".join(
                f"{key}: {value_text(value)}" for key, value in fields))
        else:
            rows.append("* " + "; ".join(
                f"**{key}**: {value_text(value)}" for key, value in fields))
    if fmt == "json":
        return json.dumps({"entries": json_rows, "total": len(entries)},
                          ensure_ascii=False)
    rows.append(_msg("MSG_COUNT_TOTAL", count=len(entries)))
    return "\n".join(rows)


def _maybe_append_link_section(text: str, entries: list,
                               fmt: str, kind: str) -> str:
    """Append elenco link/path al summary se le entries li hanno.

    Logica deterministica:
    - Solo per fmt in {markdown, html, plain} (non json/bullet_list).
    - Salta se le top-5 entries non hanno `url` o `path`.
    - Salta se il LLM ha gia' citato la maggior parte dei top URL/path
      nel summary (heuristic: 60%+ match).
    - Sanitize titoli per evitare break del markdown (`[`, `]`, `\\n`).
    """
    if not isinstance(text, str) or not entries:
        return text
    if fmt in ("json", "bullet_list"):
        return text

    top: list[dict] = []
    seen_targets: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        target = entry.get("url") or entry.get("_source_url") or entry.get("path")
        if not target or target in seen_targets:
            continue
        seen_targets.add(target)
        top.append(entry)
        if len(top) >= _MAX_LINKS_APPENDED:
            break
    if not top:
        return text

    # Estrai URL gia' citati nel testo. Greedy fino a whitespace/angle/quote,
    # poi strip trailing punctuation (.,;:!?)).
    raw_urls = re.findall(r"https?://[^\s<>\"']+", text)
    cited_urls = {u.rstrip(".,;:!?)") for u in raw_urls}

    sample = top[:5]
    sample_urls = [e.get("url") or e.get("_source_url") for e in sample
                   if e.get("url") or e.get("_source_url")]
    sample_paths = [e.get("path") for e in sample if e.get("path")]

    # Conteggio match
    n_url_cited = sum(1 for u in sample_urls if u in cited_urls)
    n_path_cited = sum(1 for p in sample_paths
                       if isinstance(p, str) and p in text)
    n_top = max(1, len(sample_urls) + len(sample_paths))
    coverage = (n_url_cited + n_path_cited) / n_top
    if coverage >= 0.6:
        return text  # LLM ha gia' citato abbastanza link

    has_urls = bool(sample_urls)
    title = _msg(
        "MSG_DESCRIBE_DIRECT_LINKS" if has_urls else "MSG_DESCRIBE_PATHS"
    )

    items: list[str] = []
    for e in top:
        u = e.get("url") or e.get("_source_url")
        p = e.get("path")
        label = (e.get("title") or e.get("_source_title") or e.get("name")
                 or (u or p or "")).replace("[", "(").replace("]", ")")
        label = label.replace("\n", " ").strip() or _msg("MSG_UNTITLED")
        if u and isinstance(u, str) and u.startswith(("http://", "https://")):
            items.append(f"- [{label}]({u})")
        elif p and isinstance(p, str):
            items.append(f"- `{p}` — {label}")
    if not items:
        return text

    if fmt == "html":
        block_lines = [f"<p><b>{title}</b></p><ul>"]
        for e in top:
            u = e.get("url") or e.get("_source_url")
            p = e.get("path")
            label = (e.get("title") or e.get("_source_title") or e.get("name")
                     or (u or p or ""))
            label = (label.replace("&", "&amp;")
                          .replace("<", "&lt;")
                          .replace(">", "&gt;"))
            if u and isinstance(u, str) and u.startswith(("http://", "https://")):
                block_lines.append(f'<li><a href="{u}">{label}</a></li>')
            elif p and isinstance(p, str):
                block_lines.append(f"<li><code>{p}</code> — {label}</li>")
        block_lines.append("</ul>")
        block = "\n".join(block_lines)
    else:
        # markdown / plain
        block = f"**{title}**:\n" + "\n".join(items)

    return text.rstrip() + "\n\n" + block


# Direttiva di raggruppamento ESPLICITO (12/6/2026, clausola «ordina/
# raggruppa per X» — vedi runtime/ordering_clause.py): quando il chiamante
# passa `group_by`, la chiave richiesta dall'utente VINCE sul raggruppamento
# intrinseco per affinità/tema dei prompt by_importance/by_relevance.
# Deterministica §7.9: la STRUTTURA (campo risolto, sezioni, ordine, conteggi)
# è calcolata in codice e prescritta al LLM; al modello resta solo la sintesi
# del contenuto di ciascuna sezione. Soglia sezioni: pochi valori distinti →
# sezioni esplicite; molti (chiave quasi-unica, es. data) → presentazione
# nell'ordine dato citando la chiave.
_GROUP_SECTIONS_MAX = 12


def _build_group_directive(key_text: str, entries: list) -> str:
    """Direttiva prompt deterministica per `group_by`. Risolve la chiave
    utente nel campo reale (ordering_clause.resolve_field); se nessun campo
    plausibile (chiave concettuale, es. 'tema') prescrive il raggruppamento
    per quel concetto. Il testo vive nei prompt traducibili e riceve soltanto
    dati strutturali calcolati qui."""
    try:
        from ordering_clause import resolve_field
        fld = resolve_field(key_text, entries)
    except Exception:
        fld = None
    if fld is None:
        return prompt_loader.get(
            "describe_grouping", _i18n.current_lang(), mode="concept",
            key_text=key_text, field="", section_count=0, sections="",
        )
    ordered_values: list[str] = []
    counts: dict[str, int] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        v = e.get(fld)
        v = "?" if v in (None, "") else str(v)
        if v not in counts:
            ordered_values.append(v)
        counts[v] = counts.get(v, 0) + 1
    n_vals = len(ordered_values)
    if 2 <= n_vals <= _GROUP_SECTIONS_MAX and n_vals < len(entries):
        sections = ", ".join(f"'{v}' ({counts[v]})" for v in ordered_values)
        return prompt_loader.get(
            "describe_grouping", _i18n.current_lang(), mode="sections",
            key_text=key_text, field=fld, section_count=n_vals,
            sections=sections,
        )
    return prompt_loader.get(
        "describe_grouping", _i18n.current_lang(), mode="ordered",
        key_text=key_text, field=fld, section_count=n_vals, sections="",
    )


def _detect_kind(entries: list, hint: str | None) -> str:
    """Determina il `kind` semantico delle entries: hint esplicito >
    campo `kind` uniforme nelle entries > euristica > 'generic'."""
    if hint:
        return hint
    kinds = {e.get("kind") for e in entries if isinstance(e, dict)}
    kinds.discard(None)
    if len(kinds) == 1:
        return next(iter(kinds))
    if len(kinds) > 1:
        return "mixed"
    # Euristica leggera sui campi per inferire dominio
    first = entries[0] if entries else {}
    if isinstance(first, dict):
        if "from" in first and "subject" in first:
            return "email"
        if "url" in first and ("title" in first or "snippet" in first):
            return "web_result"
        if "path" in first:
            return "file"
    return "generic"


DESCRIBE_ENTRIES_TOOL = {
    "type": "function",
    "function": {
        "name": "describe_entries",
        "description": (
            "Riassume lista di entries via LLM interno. Args: from_step=N, "
            "style in {by_importance|by_relevance|compact}. Cap dinamico a "
            "dimensione del bundle (non a conteggio): le entries vengono "
            "inviate al prompt finche' stanno nel budget caratteri; sotto "
            "budget passano tutte. Se si supera, il summary cita il "
            "troncamento e l'output include `truncated=True`, "
            "`cap_field='describe_cap'`, `cap_value=<n inviate>`, "
            "`used=<n inviate>`, `available_total=<total>`.\n"
            "DEVI: chiamare describe_entries SOLO se l'utente chiede "
            "riassunto di una lista nel suo insieme.\n"
            "NON DEVI: chiamare describe_entries se l'utente cita campi "
            "specifici da elencare per entry. Vai a final_answer.\n"
            "OK: 'riassumi le mail di oggi'.\n"
            "OK: 'sintetizza i log degli ultimi 10 minuti'.\n"
            "OK: 'punti importanti dei 30 risultati di ricerca'.\n"
            "ERRORE: 'dimmi mittenti e dimensioni delle 5 mail piu' grandi'.\n"
            "ERRORE: 'mostra path e mtime dei file modificati oggi'.\n"
            "ERRORE: 'top-3 mail per size con sender'.\n"
            "Pattern (NON copiare letteralmente): per richieste con campi "
            "espliciti, leggi entries da scratchpad e formula final_answer."
        ),
        "parameters": {
            "type": "object",
            "required": ["from_step"],
            "properties": {
                "from_step": {
                    "type": "integer",
                    "description": "Numero dello step precedente (in questo "
                                   "turno) che ha prodotto la lista da "
                                   "riassumere. Es. se al passo 1 hai "
                                   "chiamato read_messages, qui passi "
                                   "from_step=1.",
                    "minimum": 1,
                },
                "style": {
                    "type": "string",
                    "description": "Preset di prompt: 'by_importance' (default), "
                                   "'by_relevance' (richiede context), 'compact'.",
                    "enum": ["by_importance", "by_relevance", "compact"],
                },
                "context": {
                    "type": "string",
                    "description": "Per style='by_relevance': la richiesta originale "
                                   "dell'utente, da usare come metro di pertinenza.",
                },
                "group_by": {
                    "type": "string",
                    "description": "Chiave di raggruppamento RICHIESTA "
                                   "dall'utente (es. 'mailbox', 'mittente', "
                                   "'size'): l'output viene organizzato in "
                                   "sezioni/ordine per quella chiave e VINCE "
                                   "sul raggruppamento intrinseco per tema. "
                                   "Risolta sul campo reale delle entries.",
                },
                "data_kind": {
                    "type": "string",
                    "description": "Tipo semantico delle entries (es. 'email', "
                                   "'web_result', 'log_line', 'file'). Se "
                                   "omesso, viene dedotto dai campi 'kind' "
                                   "delle entries o euristicamente. Sovrascrive "
                                   "il 'kind' per-entry nel prompt.",
                },
                "format": {
                    "type": "string",
                    "description": "Formato di output desiderato. 'markdown' "
                                   "(default, leggero, Telegram-friendly), "
                                   "'html' (Telegram parse_mode HTML), "
                                   "'plain' (no markup), 'bullet_list' (solo "
                                   "elenco puntato), 'json' (oggetto strutturato).",
                    "enum": ["markdown", "html", "plain", "bullet_list", "json"],
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Tetto per l'output del LLM. Default 600.",
                    "default": 600,
                },
            },
        },
    },
}


_LEGACY_HEADER_KEYS = frozenset({
    "_meta", "kind", "data_kind", "style", "context", "max_tokens",
    "prompt_override", "group_by", "format", "health_context",
})


def _extract_header(entries):
    """Extract only the closed legacy ``{_meta: True, ...}`` descriptor.

    Truthy non-booleans and descriptors with unknown keys are ordinary input
    records.  Treating them as headers would silently remove caller evidence.
    """
    if not entries:
        return None, entries
    head = entries[0]
    if (isinstance(head, dict) and head.get("_meta") is True
            and set(head).issubset(_LEGACY_HEADER_KEYS)):
        return head, entries[1:]
    return None, entries


_AUDIT_MAX_VALUE_CHARS = 8192
_AUDIT_DISPLAY_CHARS = 240
_AUDIT_MAX_COLLECTION = 256
_AUDIT_MAX_ENTRIES = 10_000


class _AuditEvidenceError(ValueError):
    """Malformed or contradictory caller evidence; never partially audit it."""


def _present(value) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _single_line(value, *, display: bool = False) -> str:
    text = unicodedata.normalize("NFKC", str(value))
    if len(text) > _AUDIT_MAX_VALUE_CHARS:
        raise _AuditEvidenceError("audit value too large")
    text = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in text
    )
    text = " ".join(text.split())
    if display and len(text) > _AUDIT_DISPLAY_CHARS:
        text = text[:_AUDIT_DISPLAY_CHARS - 1].rstrip() + "…"
    return text


def _value_parts(value) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        raise _AuditEvidenceError("audit mapping value is ambiguous")
    if isinstance(value, (list, tuple, set, frozenset)):
        if len(value) > _AUDIT_MAX_COLLECTION:
            raise _AuditEvidenceError("audit collection too large")
        if any(isinstance(item, (Mapping, list, tuple, set, frozenset))
               for item in value):
            raise _AuditEvidenceError("nested audit collection")
        values = tuple(_single_line(item) for item in value if _present(item))
        return tuple(sorted(values, key=lambda item: (item.casefold(), item)))
    return (_single_line(value),)


def _canonical_owners(lexicon, surface: str) -> frozenset[str]:
    owners = set()
    for resolver_name in ("canonical_field", "canonical_audit"):
        resolver = getattr(lexicon, resolver_name, None)
        if resolver is None:
            continue
        owner = resolver(surface)
        if owner is not None:
            owners.add(str(owner))
    return frozenset(owners)


def _entry_value(entry: dict, canonical: str, lexicon):
    """Resolve a canonical value without dict-order or alias precedence.

    Producer metadata has an explicit protocol precedence.  In particular a
    complete source path is an identity, while ``_source_name`` is only its
    fallback label.  Natural-language aliases form a separate equivalence
    class: every populated alias must agree or the audit fails closed.
    """

    primary_key = {
        "origin": "_source_path",
        "readable": "_source_readable",
        "duplicates": "_duplicate_paths",
    }.get(canonical)
    primary = entry.get(primary_key) if primary_key is not None else None

    candidates = []
    for key, value in entry.items():
        if not _present(value):
            continue
        if canonical in _canonical_owners(lexicon, str(key)):
            candidates.append(value)
    if _present(primary):
        candidates.append(primary)
    if not candidates:
        if canonical == "origin" and _present(entry.get("_source_name")):
            return entry["_source_name"]
        return None

    normalized = [_value_parts(value) for value in candidates]
    collection_shape = [
        isinstance(value, (list, tuple, set, frozenset))
        for value in candidates
    ]
    comparable = {
        (is_collection, tuple(part.casefold() for part in parts))
        for is_collection, parts in zip(collection_shape, normalized)
    }
    if len(comparable) != 1:
        raise _AuditEvidenceError(f"conflicting aliases for {canonical}")
    if _present(primary):
        return primary
    # Deterministic even when equivalent aliases differ only by casing.
    selected_idx = min(range(len(candidates)), key=lambda index: (
        tuple((part.casefold(), part) for part in normalized[index]),
        type(candidates[index]).__name__,
    ))
    if collection_shape[selected_idx]:
        return normalized[selected_idx]
    return candidates[selected_idx]


def _source_parts(entry: dict, lexicon) -> tuple[str, str]:
    value = _entry_value(entry, "origin", lexicon)
    if value in (None, ""):
        return "", ""
    identity = _single_line(value).strip().replace("\\", "/")
    return identity, identity.rsplit("/", 1)[-1]


def _document_family(
    name: str, lexicon,
) -> tuple[tuple[str, ...], bool]:
    """Chiave di famiglia per varianti dello stesso documento.

    Rimuove SOLO marcatori espliciti di versione/stato. Questo evita di
    confrontare come contraddittorie righe indipendenti dello stesso foglio,
    ma associa ad esempio ``Budget_Atlas_approvato`` e
    ``Budget_Atlas_revisione``.
    """
    stem = re.sub(
        r"\.[A-Za-z0-9]{1,8}$", "",
        unicodedata.normalize("NFKC", name).casefold(),
    )
    tokens = [
        token for token in re.split(r"[\W_]+", stem, flags=re.UNICODE)
        if token
    ]
    kept = [
        token for token in tokens
        if not lexicon.is_variant_token(token)
    ]
    return tuple(kept), len(kept) != len(tokens)


def _meaningful_family(family: tuple[str, ...], *, variant: bool, lexicon) -> bool:
    if len(family) >= 2:
        return True
    if len(family) != 1:
        return False
    token = family[0]
    normalized = unicodedata.normalize("NFKD", token.casefold())
    normalized = "".join(
        character for character in normalized
        if not unicodedata.combining(character)
    )
    folded = re.sub(
        r"_+", "_", "".join(
            character if character.isalnum() else "_"
            for character in normalized
        ),
    ).strip("_")
    significant_length = (
        len(folded) >= 4
        or any(ord(character) > 127 and character.isalnum()
               for character in folded)
    )
    return (
        significant_length
        and folded not in lexicon.relevance_generic_tokens
        and (variant or not folded.isdigit())
    )


def _semantic_value(entry: dict, canonical: str, lexicon) -> str:
    value = _entry_value(entry, canonical, lexicon)
    if value is None:
        return ""
    if isinstance(value, tuple):
        return ", ".join(_single_line(item, display=True) for item in value)
    return _single_line(value, display=True)


def _document_contradictions(entries: list[dict], *, lexicon) -> list[dict]:
    """Compare named variants and path-distinct copies deterministically."""

    candidates: list[tuple[str, str, dict, tuple[str, ...], bool]] = []
    identity_counts: dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        identity, name = _source_parts(entry, lexicon)
        if not identity:
            continue
        family, variant = _document_family(name, lexicon)
        candidates.append((identity, name, entry, family, variant))
        identity_counts[identity] = identity_counts.get(identity, 0) + 1

    families: dict[
        tuple[str, ...], list[tuple[str, str, dict, bool]]
    ] = {}
    for identity, name, entry, family, variant in candidates:
        # A repeated complete source identity is itself strong evidence that
        # the records must be reconciled, even when its basename is generic.
        if (identity_counts[identity] > 1
                or _meaningful_family(
                    family, variant=variant, lexicon=lexicon,
                )):
            families.setdefault(family, []).append(
                (identity, name, entry, variant),
            )

    conflicts: list[dict] = []
    fields = ("amount", "deadline", "supplier", "status")
    for records in families.values():
        if len(records) < 2:
            continue
        name_counts: dict[str, int] = {}
        for _identity, name, _entry, _variant in records:
            name_counts[name] = name_counts.get(name, 0) + 1
        if (not any(count > 1 for count in name_counts.values())
                and not any(record[3] for record in records)):
            continue
        for left_idx in range(len(records)):
            for right_idx in range(left_idx + 1, len(records)):
                left_id, left_name, left, _ = records[left_idx]
                right_id, right_name, right, _ = records[right_idx]
                details = []
                for canonical in fields:
                    lv = _semantic_value(left, canonical, lexicon)
                    rv = _semantic_value(right, canonical, lexicon)
                    if lv and rv and lv.casefold() != rv.casefold():
                        label = (
                            lexicon.surface(canonical)
                            if canonical in lexicon.fields
                            else lexicon.audit[canonical][0]
                        )
                        details.append(
                            f"{_single_line(label, display=True)}: {lv} ↔ {rv}"
                        )
                if details:
                    conflicts.append({
                        "left": _single_line(
                            left_id if name_counts[left_name] > 1 else left_name,
                            display=True,
                        ),
                        "right": _single_line(
                            right_id if name_counts[right_name] > 1 else right_name,
                            display=True,
                        ),
                        "details": _single_line(
                            "; ".join(details), display=True,
                        ),
                    })
    return conflicts


_AUDIT_TEMPLATE_FIELDS: Mapping[str, frozenset[str]] = {
    "ERR_EXT_SVC_UNAVAILABLE": frozenset(),
    "MSG_DOCUMENT_AUDIT_HEADER": frozenset(),
    "MSG_DOCUMENT_AUDIT_CONTRADICTION": frozenset({"left", "right", "details"}),
    "MSG_DOCUMENT_AUDIT_UNREADABLE": frozenset({"files"}),
    "MSG_DOCUMENT_AUDIT_DUPLICATES": frozenset({"details"}),
}
_AUDIT_TEXT_FORMATS = frozenset({"markdown", "plain", "bullet_list"})


@dataclass(frozen=True, slots=True)
class DocumentAuditRequest:
    conflicts: bool = False
    unreadable: bool = False
    duplicates: bool = False

    @property
    def requested(self) -> bool:
        return self.conflicts or self.unreadable or self.duplicates


@dataclass(frozen=True, slots=True)
class DocumentAuditConflict:
    left: str
    right: str
    details: str


@dataclass(frozen=True, slots=True)
class DocumentAuditOutcome:
    state: str
    request: DocumentAuditRequest
    conflicts: tuple[DocumentAuditConflict, ...] = ()
    unreadable: tuple[str, ...] = ()
    duplicates: tuple[str, ...] = ()
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class _DocumentAuditSnapshot:
    lexicon: object
    patterns: Mapping[str, tuple[re.Pattern, ...]]
    templates: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class _DocumentAuditPlan:
    snapshot: _DocumentAuditSnapshot | None
    outcome: DocumentAuditOutcome
    fmt: str


def _template_fields(template: str) -> frozenset[str] | None:
    try:
        parsed = tuple(Formatter().parse(template))
    except ValueError:
        return None
    fields: set[str] = set()
    for _literal, name, spec, conversion in parsed:
        if name is None:
            continue
        # Audit templates are deliberately data-only.  Attribute/index access,
        # conversions and format mini-languages would widen the rendering seam.
        if not name.isidentifier() or spec or conversion:
            return None
        fields.add(name)
    return frozenset(fields)


def _ready_audit_templates() -> dict[str, str] | None:
    """Freeze one complete, ready language family in one SQLite statement.

    A partially materialized preferred language is not permission to mix its
    rows with fallback rows.  Translation provenance is checked recursively
    against ready, self-hashed source rows from the same frozen query, never
    against mutable per-key fallback reads.
    """

    try:
        chain = _i18n.language_chain(_i18n.current_lang())
        connection = _i18n._open()
        keys = tuple(_AUDIT_TEMPLATE_FIELDS)
        placeholders = ",".join("?" for _ in keys)
        rows = connection.execute(
            "SELECT key,lang,text,needs_translation,source_lang,"
            "version_hash,source_text_hash FROM i18n WHERE key IN ("
            + placeholders + ")",
            keys,
        ).fetchall()
    except Exception:
        return None
    by_identity = {(str(row[0]), str(row[1])): row for row in rows}

    def digest(text: str) -> str:
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

    validated_rows: set[tuple[str, str]] = set()

    def validate_row(key: str, language: str,
                     trail: frozenset[tuple[str, str]]) -> str | None:
        identity = (key, language)
        if identity in validated_rows:
            row = by_identity.get(identity)
            return str(row[2]) if row is not None else None
        if identity in trail:
            return None
        row = by_identity.get(identity)
        if row is None or row[2] is None:
            return None
        try:
            pending = int(row[3] or 0)
        except (TypeError, ValueError):
            return None
        text = str(row[2])
        if pending or row[5] != digest(text):
            return None
        if _template_fields(text) != _AUDIT_TEMPLATE_FIELDS[key]:
            return None
        source_lang = str(row[4] or "")
        source_hash = str(row[6] or "")
        if not source_lang:
            if source_hash:
                return None
        else:
            if (_i18n.normalize_language(source_lang) != source_lang
                    or source_lang == language):
                return None
            source_text = validate_row(
                key, source_lang, trail | frozenset({identity}),
            )
            source = by_identity.get((key, source_lang))
            if (source_text is None or source is None
                    or source_hash != str(source[5] or "")
                    or source_hash != digest(source_text)):
                return None
        validated_rows.add(identity)
        return text

    def validated_family(language: str) -> dict[str, str] | None:
        family: dict[str, str] = {}
        for key in _AUDIT_TEMPLATE_FIELDS:
            text = validate_row(key, language, frozenset())
            if text is None:
                return None
            family[key] = text
        return family

    for candidate in chain:
        present = any((key, candidate) in by_identity for key in keys)
        if not present:
            continue
        # The first present language in the configured chain owns the whole
        # family.  Partial, pending or malformed data is a fail-closed state.
        return validated_family(candidate)
    return None


def _patterns_from_family(resources, concepts) -> dict[str, tuple[re.Pattern, ...]] | None:
    compiled: dict[str, tuple[re.Pattern, ...]] = {}
    for concept in concepts:
        patterns: list[re.Pattern] = []
        seen: set[str] = set()
        for resource in resources.get(concept, ()):
            payload = resource.get("payload")
            if not isinstance(payload, list) or not payload:
                return None
            for source in payload:
                if not isinstance(source, str) or not source.strip():
                    return None
                if source in seen:
                    continue
                try:
                    patterns.append(re.compile(source, re.IGNORECASE))
                except re.error:
                    return None
                seen.add(source)
        if not patterns:
            return None
        compiled[concept] = tuple(patterns)
    return compiled


def _capture_document_audit_snapshot() -> _DocumentAuditSnapshot | None:
    """Capture all audit authority before inspecting intent or entries."""

    import detection_lexicon as detection_lexicon
    import detection_lexicon_seed_runtime_safety as safety

    concepts = (
        safety.DOCUMENT_AUDIT_CONFLICT_INTENT,
        safety.DOCUMENT_AUDIT_UNREADABLE_INTENT,
        safety.DOCUMENT_AUDIT_DUPLICATE_INTENT,
        safety.DOCUMENT_NO_CONTRADICTION_CLAIM,
    )
    try:
        _reconciliation_lex._ensure_registered()
        safety._ensure_registered()
        kinds = _reconciliation_lex.family_kinds()
        kinds.update({concept: "regex" for concept in concepts})
        resources = detection_lexicon.native_ready_family_resources(
            kinds,
            require_manual=True,
            include_reviewed_baselines=True,
        )
    except Exception:
        return None
    if resources is None:
        return None
    lexicon = _reconciliation_lex.from_resources(resources)
    patterns = _patterns_from_family(resources, concepts)
    templates = _ready_audit_templates()
    if lexicon is None or patterns is None or templates is None:
        return None
    return _DocumentAuditSnapshot(
        lexicon=lexicon, patterns=patterns, templates=templates,
    )


def _matches_any(patterns: tuple[re.Pattern, ...], text: str) -> bool:
    return any(pattern.search(text or "") for pattern in patterns)


def _audit_request(snapshot: _DocumentAuditSnapshot,
                   context: str) -> DocumentAuditRequest:
    import detection_lexicon_seed_runtime_safety as safety

    return DocumentAuditRequest(
        conflicts=_matches_any(
            snapshot.patterns[safety.DOCUMENT_AUDIT_CONFLICT_INTENT], context,
        ),
        unreadable=_matches_any(
            snapshot.patterns[safety.DOCUMENT_AUDIT_UNREADABLE_INTENT], context,
        ),
        duplicates=_matches_any(
            snapshot.patterns[safety.DOCUMENT_AUDIT_DUPLICATE_INTENT], context,
        ),
    )


def _prepare_document_audit(entries: list, context: str,
                            fmt: str) -> _DocumentAuditPlan:
    """Capture authority and audit the original, uncapped input exactly once."""

    snapshot = _capture_document_audit_snapshot()
    if snapshot is None:
        return _DocumentAuditPlan(
            snapshot=None,
            outcome=DocumentAuditOutcome(
                state="unavailable", request=DocumentAuditRequest(),
                error_code="ERR_EXT_SVC_UNAVAILABLE",
            ),
            fmt=fmt,
        )
    try:
        request = _audit_request(snapshot, _single_line(context))
    except _AuditEvidenceError:
        return _DocumentAuditPlan(
            snapshot=snapshot,
            outcome=DocumentAuditOutcome(
                state="invalid_evidence", request=DocumentAuditRequest(),
                error_code="ERR_ARG_INVALID",
            ),
            fmt=fmt,
        )
    if not request.requested:
        return _DocumentAuditPlan(
            snapshot=snapshot,
            outcome=DocumentAuditOutcome(
                state="not_requested", request=request,
            ),
            fmt=fmt,
        )
    if fmt not in _AUDIT_TEXT_FORMATS:
        return _DocumentAuditPlan(
            snapshot=snapshot,
            outcome=DocumentAuditOutcome(
                state="unsupported_format", request=request,
                error_code="ERR_EXT_SVC_UNAVAILABLE",
            ),
            fmt=fmt,
        )

    dictionaries = [entry for entry in entries if isinstance(entry, dict)]
    try:
        if len(dictionaries) > _AUDIT_MAX_ENTRIES:
            raise _AuditEvidenceError("too many audit entries")
        conflicts = (
            _document_contradictions(dictionaries, lexicon=snapshot.lexicon)
            if request.conflicts else []
        )
        unreadable: set[str] = set()
        duplicates: set[str] = set()
        for entry in dictionaries:
            identity, retained = _source_parts(entry, snapshot.lexicon)
            retained = _single_line(retained, display=True) if retained else ""
            readable = _entry_value(entry, "readable", snapshot.lexicon)
            if (request.unreadable and retained and readable is False):
                unreadable.add(retained)
            if not request.duplicates or not identity:
                continue
            paths = _entry_value(entry, "duplicates", snapshot.lexicon)
            if not isinstance(paths, (list, tuple, set, frozenset)):
                continue
            for path in paths:
                duplicate = _single_line(path).strip().replace("\\", "/")
                if duplicate:
                    duplicates.add(_single_line(
                        f"{identity} ← {duplicate}", display=True,
                    ))
    except _AuditEvidenceError:
        return _DocumentAuditPlan(
            snapshot=snapshot,
            outcome=DocumentAuditOutcome(
                state="invalid_evidence", request=request,
                error_code="ERR_ARG_INVALID",
            ),
            fmt=fmt,
        )
    return _DocumentAuditPlan(
        snapshot=snapshot,
        outcome=DocumentAuditOutcome(
            state="completed", request=request,
            conflicts=tuple(DocumentAuditConflict(**item) for item in conflicts),
            unreadable=tuple(sorted(unreadable)),
            duplicates=tuple(sorted(duplicates)),
        ),
        fmt=fmt,
    )


def _audit_metadata(outcome: DocumentAuditOutcome) -> dict:
    return {
        "state": outcome.state,
        "request": {
            "conflicts": outcome.request.conflicts,
            "unreadable": outcome.request.unreadable,
            "duplicates": outcome.request.duplicates,
        },
        "conflicts": [
            {"left": item.left, "right": item.right, "details": item.details}
            for item in outcome.conflicts
        ],
        "unreadable": list(outcome.unreadable),
        "duplicates": list(outcome.duplicates),
        "error_code": outcome.error_code,
    }


def _audit_error(plan: _DocumentAuditPlan) -> dict:
    code = plan.outcome.error_code or "ERR_EXT_SVC_UNAVAILABLE"
    template = (
        plan.snapshot.templates.get(code)
        if plan.snapshot is not None else None
    )
    return {
        "ok": False,
        "error_code": code,
        "error": template or code,
        "document_audit": _audit_metadata(plan.outcome),
    }


_AUDIT_MARKDOWN_ESCAPE = re.compile(r"([\\`*_{}\[\]()<>#+.!|~-])")


def _escape_audit_value(value: str, fmt: str) -> str:
    value = _single_line(value, display=True)
    if fmt in {"markdown", "bullet_list"}:
        return _AUDIT_MARKDOWN_ESCAPE.sub(r"\\\1", value)
    return value


def _render_document_audit(text: str, plan: _DocumentAuditPlan) -> str:
    outcome = plan.outcome
    snapshot = plan.snapshot
    if outcome.state != "completed" or snapshot is None:
        return text
    lines: list[str] = []
    if outcome.conflicts:
        import detection_lexicon_seed_runtime_safety as safety
        for pattern in snapshot.patterns[safety.DOCUMENT_NO_CONTRADICTION_CLAIM]:
            text = pattern.sub("", text).rstrip()
        for item in outcome.conflicts:
            lines.append(snapshot.templates[
                "MSG_DOCUMENT_AUDIT_CONTRADICTION"
            ].format(
                left=_escape_audit_value(item.left, plan.fmt),
                right=_escape_audit_value(item.right, plan.fmt),
                details=_escape_audit_value(item.details, plan.fmt),
            ))
    if outcome.unreadable:
        lines.append(snapshot.templates[
            "MSG_DOCUMENT_AUDIT_UNREADABLE"
        ].format(files=", ".join(
            _escape_audit_value(item, plan.fmt) for item in outcome.unreadable
        )))
    if outcome.duplicates:
        lines.append(snapshot.templates[
            "MSG_DOCUMENT_AUDIT_DUPLICATES"
        ].format(details="; ".join(
            _escape_audit_value(item, plan.fmt) for item in outcome.duplicates
        )))
    if not lines:
        return text
    audit = snapshot.templates["MSG_DOCUMENT_AUDIT_HEADER"] + "\n" + "\n".join(lines)
    return (text.rstrip() + "\n\n" + audit).strip() if text.strip() else audit


def _finalize_document_audit(result: dict,
                             plan: _DocumentAuditPlan | None) -> dict:
    if plan is None:
        return result
    if plan.outcome.error_code:
        return _audit_error(plan)
    finalized = dict(result)
    if finalized.get("ok") and isinstance(finalized.get("summary"), str):
        finalized["summary"] = _render_document_audit(
            finalized["summary"], plan,
        )
    finalized["document_audit"] = _audit_metadata(plan.outcome)
    return finalized


def handle_describe_entries(args, *, verbose: bool = False,
                             _mr_depth: int = 0,
                             _deterministic: bool | None = None) -> dict:
    if not isinstance(args, Mapping):
        return {
            "ok": False,
            "error_code": "ERR_ARG_INVALID",
            "error": _msg(
                "ERR_ARG_INVALID", arg="args", reason="must_be_object",
            ),
        }
    entries = args.get("entries")
    if not isinstance(entries, list):
        return {
            "ok": False,
            "error_code": "ERR_ARG_INVALID",
            "error": _msg(
                "ERR_ARG_INVALID", arg="entries", reason="must_be_list",
            ),
        }
    try:
        # Freeze both audit evidence and the later summarization input.  A
        # producer retaining references to nested rows cannot move either side
        # of the comparison after authority has been captured.
        entries = copy.deepcopy(entries)
    except Exception:
        return {
            "ok": False,
            "error_code": "ERR_ARG_INVALID",
            "error": _msg(
                "ERR_ARG_INVALID", arg="entries", reason="not_snapshotable",
            ),
        }
    original_entries = list(entries)

    # Header opzionale come primo elemento: {_meta: True, kind, style,
    # context, max_tokens, prompt_override}. I valori dell'header NON
    # sovrascrivono args espliciti (i kwargs vincono).
    header, entries = _extract_header(entries)
    h = header or {}

    style = args.get("style") or h.get("style") or "by_importance"
    context = args.get("context")
    if context is None:
        context = h.get("context") or ""
    if not isinstance(style, str) or not isinstance(context, str):
        return {
            "ok": False,
            "error_code": "ERR_ARG_INVALID",
            "error": _msg(
                "ERR_ARG_INVALID", arg="style|context",
                reason="must_be_string",
            ),
        }
    # Safety net (20/5 v6): style=by_relevance richiede `context` con la
    # query utente per fare un riassunto mirato. Se il PLANNER l'ha
    # dimenticato, ricadiamo deterministicamente a by_importance (segnale
    # vs rumore, non richiede context). Evita output del tipo "query
    # dell'utente vuota '', non posso rispondere".
    if style == "by_relevance" and not (context and context.strip()):
        style = "by_importance"
    data_kind = args.get("data_kind") or h.get("kind") or h.get("data_kind")
    if data_kind is not None and not isinstance(data_kind, str):
        return {
            "ok": False,
            "error_code": "ERR_ARG_INVALID",
            "error": _msg(
                "ERR_ARG_INVALID", arg="data_kind", reason="must_be_string",
            ),
        }
    # max_tokens adattivo per dimensione bundle (era 600 fisso → 400 → scala):
    # N=1-3 → 200, N=4-10 → 300, N>10 → 400. Riduce KV-cache allocation
    # llama-server proporzionalmente al target output reale (3-5 righe). Caller
    # puo' override esplicito.
    _explicit_max = args.get("max_tokens")
    if _explicit_max is None:
        _explicit_max = h.get("max_tokens")
    if _explicit_max is not None:
        try:
            if isinstance(_explicit_max, bool):
                raise ValueError("boolean is not an integer budget")
            if (isinstance(_explicit_max, float)
                    and not _explicit_max.is_integer()):
                raise ValueError("fractional budget")
            max_tokens = int(_explicit_max)
        except (TypeError, ValueError, OverflowError):
            return {
                "ok": False,
                "error_code": "ERR_ARG_INVALID",
                "error": _msg(
                    "ERR_ARG_INVALID", arg="max_tokens",
                    reason="must_be_integer",
                ),
            }
        if max_tokens <= 0:
            return {
                "ok": False,
                "error_code": "ERR_ARG_INVALID",
                "error": _msg(
                    "ERR_ARG_INVALID", arg="max_tokens",
                    reason="must_be_positive",
                ),
            }
    else:
        _n_ent = len(entries) if isinstance(entries, list) else 0
        if _n_ent <= 3:
            max_tokens = 200
        elif _n_ent <= 10:
            max_tokens = 300
        else:
            # Band-aid (8/7, Roberto): oltre 10 entries il budget fisso a 400 tok
            # troncava l'elenco (~12 righe) sulle query-lista ("top 20 processi").
            # Scala col numero di entry fino a un tetto anti-runaway, cosi' la
            # quantita' richiesta entra. NB: TAMPONE — il fix pulito e' un
            # terminale TABELLA deterministico (output_policy L-mode) per le liste.
            max_tokens = min(_DESCRIBE_MAX_TOKENS_CAP,
                             max(400, _n_ent * _DESCRIBE_TOKENS_PER_ENTRY))
    prompt_override = args.get("prompt_override") or h.get("prompt_override")
    group_by = args.get("group_by") or h.get("group_by") or ""
    fmt = args.get("format")
    if fmt is None:
        fmt = h.get("format") or "markdown"
    if (prompt_override is not None and not isinstance(prompt_override, str)):
        return {
            "ok": False,
            "error_code": "ERR_ARG_INVALID",
            "error": _msg(
                "ERR_ARG_INVALID", arg="prompt_override",
                reason="must_be_string",
            ),
        }
    if not isinstance(group_by, str) or not isinstance(fmt, str):
        return {
            "ok": False,
            "error_code": "ERR_ARG_INVALID",
            "error": _msg(
                "ERR_ARG_INVALID", arg="group_by|format",
                reason="must_be_string",
            ),
        }
    tier = "auto"
    # ADR 0111 (7/5/2026): Level 2 — describe_entries deve sapere se la
    # sorgente (`from_step`) aveva un blocco `health` (load/memoria/dischi/
    # servizi). Senza questa visibilita' il LLM dichiarerebbe "non
    # disponibile" su tutti i campi salute (vedono solo le entries =
    # processi). Il runtime inietta `health_context` (dict) come
    # informazione contestuale che viene PRE-pendata al prompt LLM con
    # istruzione esplicita di non re-discutere salute.
    health_context = (args or {}).get("health_context") or h.get("health_context")
    # tier 'auto' risolto DOPO il pack (dimensiona sul bundle realmente
    # inviato `visible_entries`, non sul totale pre-cap).

    # §2.4 robustezza NL→determinismo: l'LLM confonde gli enum e a volte mette
    # un valore di `format` (es. 'bullet_list') nello `style`. NON far crashare
    # il turno (regressione live: «quali sono i task» → list_tasks OK ma describe
    # rigettava 'bullet_list' e l'errore diventava la risposta). Degrade
    # deterministico: (1) se è un format valido messo nel posto sbagliato,
    # spostalo in `fmt`; (2) ricadi sempre sullo style di default. Mai hard-fail.
    if style not in STYLES and not prompt_override:
        if style in _FORMATS:
            fmt = style           # era un format messo nel posto sbagliato
        style = "by_importance"   # default robusto, mai crash

    if fmt not in _FORMATS:
        return {
            "ok": False,
            "error_code": "ERR_FMT_INVALID",
            "error": _msg("ERR_FMT_INVALID", value=fmt),
        }

    # Only the root call owns audit authority.  Map/reduce children operate on
    # derived digests and must neither reacquire grammar nor audit a subset.
    audit_plan = (
        _prepare_document_audit(original_entries, str(context or ""), fmt)
        if _mr_depth == 0 else None
    )
    if audit_plan is not None and audit_plan.outcome.error_code:
        return _audit_error(audit_plan)

    def finish(result: dict) -> dict:
        return _finalize_document_audit(result, audit_plan)

    if not entries:
        return finish({
            "ok": True, "summary": "", "item_count": 0, "style": style,
            "data_kind": data_kind or "generic",
            "in_tokens": 0, "out_tokens": 0, "latency_ms": 0,
        })

    # Le collezioni prodotte da extract_entries hanno gia' un piccolo schema.
    # Una seconda LLM call di sintesi puo' soltanto perdere righe/campi: il
    # formato compact le rende quindi in modo lossless e riproducibile.
    if style == "compact" and data_kind == "entries" \
            and all(isinstance(entry, dict) for entry in entries):
        text = _format_structured_entries(entries, fmt, str(group_by or ""))
        text = _maybe_append_link_section(text, entries, fmt, data_kind)
        return finish({
            "ok": True,
            "summary": text,
            "item_count": len(entries),
            "style": style,
            "data_kind": data_kind,
            "format": fmt,
            "deterministic": True,
            "in_tokens": 0,
            "out_tokens": 0,
            "latency_ms": 0,
        })

    # ADR 0153 (19/5/2026 v6): content fetch on-demand. Se le entries
    # hanno SOLO url+title+snippet (tipicamente output di find_urls) e
    # nessun campo testuale (content/body/text), describe_entries NON
    # puo' sintetizzare contenuto reale — ricadrebbe in enumerazione di
    # metadata. Dichiara strutturalmente la mancanza con
    # `error_class=needs_content_fetch`; il runtime auto-injecta
    # `read_urls_html` sui top URL e ri-chiama describe_entries con
    # entries arricchite. Pattern install_on_demand (ADR 0143).
    # Campi testuali considerati "contenuto sufficiente" per la sintesi.
    # SOLO testo realmente sintetizzabile:
    # - content/body/text: convenzioni generali
    # - body_text: read_urls_html canonical HTML fetch
    # Snippet ESCLUSO: i find_urls snippets sono preview SEO 100-200 char,
    # non sintetizzabili a riassunto informativo. La presenza di soli
    # snippet trigger needs_content_fetch -> read_urls_html sui top URL.
    _CONTENT_FIELDS = ("content", "body", "text", "body_text")
    # Soglia minima di contenuto sintetizzabile (caratteri):
    # - snippet di search (100-200 char) → NON sufficiente
    # - paragrafo singolo (~300 char) → marginale
    # - 500 char ≈ ~80 parole / 4-5 frasi → contenuto reale.
    # Override via env per tuning durante bench, default conservativo.
    _CONTENT_MIN_CHARS = int(
        os.environ.get("METNOS_DESCRIBE_MIN_CHARS", "500")
    )
    def _has_content(e: dict) -> bool:
        for k in _CONTENT_FIELDS:
            v = e.get(k)
            if isinstance(v, str) and len(v.strip()) >= _CONTENT_MIN_CHARS:
                return True
        return False
    _has_textual_content = any(
        _has_content(e) for e in entries if isinstance(e, dict)
    )
    # Dominio `sites` (regressione turn 4769cf88): le entries vengono da una
    # sessione autenticata (read_sites), il loro `url` punta a pagine dietro
    # login. `read_urls_html` (GET SENZA cookie) leggerebbe la pagina pubblica/
    # login e ri-triggererebbe un open_sites ridondante (quota). Nessun content
    # fetch: descrivi l'esito reale (record o vuoto/challenge onesto).
    _fetchable = data_kind != "sites"
    if _mr_depth == 0 and not _has_textual_content and _fetchable:
        _urls_for_fetch = [
            e["url"] for e in entries
            if isinstance(e, dict)
            and isinstance(e.get("url"), str)
            and e["url"].startswith(("http://", "https://"))
        ][:5]
        if _urls_for_fetch:
            return finish({
                "ok": False,
                "error_code": "ERR_ARG_MISSING",
                "error_class": "needs_content_fetch",
                "needs_urls_html": _urls_for_fetch,
                "error": _msg("ERR_ARG_MISSING", arg="entries.content"),
            })

    # Cap dinamico a budget di caratteri (§2.7, §7.3): mandiamo al prompt
    # solo le prime N entries che stanno nel budget e dichiariamo truncated
    # nel return value. Sotto budget: tutte le entries, nessun troncamento.
    total_entries = len(entries)
    visible_entries, truncated_describe = _pack_entries(entries)
    hidden_count = total_entries - len(visible_entries)

    # Over-budget → map-reduce (§7.3, copre TUTTE le entries invece di
    # troncare la coda). Scatta solo se abilitato e sotto il tetto di
    # ricorsione. Sotto budget (truncated_describe=False): path invariato.
    if (truncated_describe and _DESCRIBE_MAPREDUCE
            and _mr_depth < _MR_MAX_DEPTH):
        return finish(_describe_map_reduce(
            entries, style=style, context=context, data_kind=data_kind,
            fmt=fmt, group_by=group_by, max_tokens=max_tokens,
            health_context=health_context, mr_depth=_mr_depth))

    if tier == "auto":
        tier = _auto_tier(visible_entries)

    kind = _detect_kind(visible_entries, data_kind)
    base_prompt = (prompt_override
                   if prompt_override
                   else prompt_loader.get(f"describe_entries_{style}",
                                          _i18n.current_lang(),
                                          n=len(visible_entries), context=context, kind=kind))
    fmt_directive = _format_directive(fmt)
    # Level 2 (ADR 0111): pre-pend `health_context` quando presente. Il LLM
    # vede un blocco "STATO SERVER GIA' RIASSUNTO" con load/RAM/dischi/
    # servizi formattati e l'istruzione esplicita di non ripeterli ne'
    # dichiararli "non disponibili" — limitati a riassumere le entries
    # (processi) sotto.
    health_directive = ""
    if isinstance(health_context, dict) and health_context:
        try:
            from orchestration import _fmt_health_block  # ADR 0148: package-relative
            block = _fmt_health_block(health_context)
        except Exception:
            block = ""
        if block:
            health_directive = prompt_loader.get(
                "describe_health_context", _i18n.current_lang(),
                health_block=block,
            )
    prompt = base_prompt
    if health_directive:
        prompt = health_directive + "\n\n" + prompt
    if fmt_directive:
        prompt = prompt + "\n\n" + fmt_directive
    # Raggruppamento ESPLICITO richiesto dall'utente: appeso per ULTIMO,
    # vince sul raggruppamento intrinseco del prompt preset (affinità/tema).
    if group_by and visible_entries:
        group_directive = _build_group_directive(str(group_by),
                                                 visible_entries)
        if group_directive:
            prompt = prompt + "\n\n" + group_directive

    try:
        # max_query_chars: il budget di pack (_DESCRIBE_MAX_CHARS) deve
        # passare INTERO a call_llm — il default 12000 di _serialize_query
        # troncherebbe in silenzio il bundle a meta' JSON, smentendo i
        # conteggi visible/hidden dichiarati (§2.7/§2.8).
        _det = (_DESCRIBE_DETERMINISTIC if _deterministic is None
                else _deterministic)
        text, meta = call_llm(visible_entries, prompt, tier=tier,
                              max_tokens=max_tokens,
                              deterministic=_det,
                              max_query_chars=_DESCRIBE_MAX_CHARS + 2048)
    except Exception as e:
        return finish({
            "ok": False, "error_code": "ERR_EXT_SVC_UNAVAILABLE",
            "error": f"LLM call failed: {type(e).__name__}: {e}",
        })

    # ADR 0119 (9/5/2026): post-process append "Link diretti" se le entries
    # hanno `url` o `path` E il LLM non li ha gia' citati nel summary.
    # Generale (qualsiasi `kind` con campo url/path), deterministico (regex),
    # rispetta le regole di prompt che vietano elenco letterale (il LLM
    # produce sintesi pulita, il post-process aggiunge i link sotto).
    text = _maybe_append_link_section(text, visible_entries, fmt, kind)

    # Patch 3 (8/5/2026): se truncated, append nota localizzata al summary
    # cosi' l'utente vede subito il cap (UX onesto §2.8) e il PLANNER
    # puo' decidere se ritagliare/rilanciare.
    if truncated_describe:
        try:
            note = _msg("MSG_DESCRIBE_TRUNCATED",
                        visible=len(visible_entries),
                        hidden=hidden_count,
                        cap=len(visible_entries))
        except Exception:
            note = ""
        if text and note:
            text = text.rstrip() + "\n\n" + note
        elif note:
            text = note

    out = {
        "ok": True,
        "summary": text,
        "item_count": total_entries,
        "style": style,
        "data_kind": kind,
        "format": fmt,
        **meta,
    }
    if group_by:
        out["group_by"] = str(group_by)
    if truncated_describe:
        out.update({
            "truncated": True,
            "truncated_what": "describe",
            "used": len(visible_entries),
            "available_total": total_entries,
            "cap_field": "describe_cap",
            "cap_value": len(visible_entries),
        })
    return finish(out)


# --- API per chiamate da altri executor (Python diretto, no tool_call) -------

def describe(items, *, style: str | None = None, context: str = "",
             data_kind: str | None = None, max_tokens: int = 600,
             prompt_override: str | None = None) -> str:
    """Funzione di alto livello per altri executor che hanno bisogno di
    riassumere una lista. Ritorna SOLO la stringa di riassunto.

    DUE PATTERN equivalenti per il chiamante:

    1. Args espliciti:
        from describe_entries import describe
        summary = describe(results,
                           style="by_relevance",
                           context="le novita' su X",
                           data_kind="web_result")

    2. Lista con descriptor in testa (piu' ergonomico, una sola "cosa"
       da passare in giro fra executor):
        from describe_entries import describe
        bundle = [
            {"_meta": True, "kind": "web_result",
             "style": "by_relevance",
             "context": "le novita' su X"},
            *results,
        ]
        summary = describe(bundle)

    Solleva RuntimeError se la chiamata fallisce.
    """
    res = handle_describe_entries({
        "entries": items, "style": style, "context": context,
        "data_kind": data_kind, "max_tokens": max_tokens,
        "prompt_override": prompt_override,
    })
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "describe failed"))
    return res["summary"]


BUILTIN_INPROC_SPECS = [{
    "name": "describe_entries", "tool_spec": DESCRIBE_ENTRIES_TOOL,
    "affinity": ["riassumi", "sintetizza", "describe", "summarize", "entries"],
}]
