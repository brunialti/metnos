"""Generatore deterministico dell'esempio pratico per ogni promote.

§7.9 (deterministico) per il blocco principale e per la stima risparmio.
Estensione 11/5/2026 (Roberto): aggiunto UN paragrafo LLM tier middle
come narrativa interpretabile (NON come decisione). E' l'UNICO uso di
LLM nel daemon — il fallback su timeout/down e' deterministico.

Output `render_practical_example()` ha 3 sezioni:

    Query: <una query reale dal corpus turn JSONL matching path_hash>
    Pipeline OGGI: <executor sequence dal path_shape pre-synth>
    Pipeline NUOVA: <name del proposal + args dal sig_key>
    Sostituisce: <pct dal call_freq_60d>
    NON sostituisce: <complement deterministico da sig_key shape>

    ## Stima risparmio
    - Tempo: <pct>%
    - Token in: ~<N>/turno (-<pct>%)
    - Frequenza: <call_freq_60d> chiamate/60g

    <!-- llm_commentary -->
    ## Commento
    <paragrafo modello locale tier middle, 3-5 frasi>

Sorgenti dati:
- `sig_key` del proposal (lista JSON-parseable, ADR 0077).
- `path_hash` + `path_steps` del proposal (ADR 0122 enrichment).
- ETA index sqlite per latency p50/p95.
- Turn JSONL grep per UNA query reale matching `path_hash` (prima trovata)
  e per `llm_in_tokens` aggregato (token savings heuristics).
- `vocab.py` per verbo+oggetto del nuovo executor.

Degrade graceful (mai fail-loud, §2.8 si applica solo a rollback senza blob):
- ETA vuoto → "Pipeline OGGI: <da definire>" + "Sostituisce: dati insufficienti".
- Nessuna query reale → "Query: <esempio sintetico>" (`user_query` del JSON).
- sig_key non parseable → "Pipeline NUOVA: <name>(args sconosciuti)".
- LLM down/timeout → "(commento non disponibile)".
- Token savings insufficient data → "Token in: n/a (dati insufficienti)".
"""
from __future__ import annotations

import json
import os
import sys as _sys
from pathlib import Path
from typing import Any

_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as _C  # §7.11

# Stima conservativa della core size del prompt PLANNER (Fase C, 11/5/2026
# section-aware ~5-8KB): assumiamo 6KB per la versione single-section.
# Token approssimato = chars/4 (regola standard tiktoken-like).
_PLANNER_CORE_SIZE_BYTES = 6 * 1024
_TOKENS_PER_BYTE = 0.25  # ~4 char/token

# Timeout LLM per il commento. Cap conservativo (il modello locale su Strix Halo
# di solito risponde 0.5-1.5s; 5s e' un margine ampio per spikes).
_LLM_COMMENTARY_TIMEOUT_S = 5.0
# Token cap per il paragrafo: 3-5 frasi * ~25 parole * ~1.4 token/parola
# ≈ 200 token, con margine.
_LLM_COMMENTARY_MAX_TOKENS = 300


_DEFAULT_TURNS_DIR = _C.PATH_USER_DATA / "turns"
_DEFAULT_ETA_DB = _C.PATH_USER_DATA / "proposals_eta.sqlite"


def _turns_dir() -> Path:
    """Override via env per i test."""
    env = os.environ.get("METNOS_TURNS_DIR")
    return Path(env) if env else _DEFAULT_TURNS_DIR


def _eta_db_path() -> Path:
    env = os.environ.get("METNOS_PROPOSALS_ETA_DB")
    return Path(env) if env else _DEFAULT_ETA_DB


def _find_real_query_for_path_hash(path_hash: str) -> str | None:
    """Cerca la prima query reale nel corpus turn JSONL con path_hash matchante.

    Determinismo: itera file in ordine alfabetico, prima riga matchante vince.
    Restituisce None se path_hash vuoto o nessuna match nel corpus.
    """
    if not path_hash:
        return None
    base = _turns_dir()
    if not base.exists():
        return None
    try:
        from path_shape import path_shape_hash
    except ImportError:
        return None
    for fp in sorted(base.glob("*.jsonl")):
        try:
            content = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (TypeError, ValueError):
                continue
            steps = rec.get("steps") or []
            if not steps:
                continue
            try:
                computed = path_shape_hash(steps)
            except Exception:
                continue
            if computed == path_hash:
                q = rec.get("user_query") or rec.get("query") or ""
                if isinstance(q, str) and q.strip():
                    return q.strip()
    return None


def _lookup_eta(path_hash: str) -> dict | None:
    """Wrapper isolato di proposals_eta_index.lookup con env override."""
    if not path_hash:
        return None
    try:
        from proposals_eta_index import lookup
    except ImportError:
        return None
    try:
        return lookup(path_hash, db_path=_eta_db_path())
    except Exception:
        return None


def _format_args_from_sig_key(sig_key: Any) -> str:
    """Formatta gli args dal sig_key (lista JSON ADR 0077).

    Tre forme attese:
    - `["dedupe", reason, a, b]`
    - `["generalize", [exec1, exec2, ...]]`
    - `["specialize", exec, arg, val_json]`

    Per le proposte synth (non-introvertiva) il sig_key e' spesso il
    nome stesso + args inferred. Fallback: stringa raw troncata.
    """
    if sig_key is None:
        return "args sconosciuti"
    parsed: Any
    if isinstance(sig_key, str):
        try:
            parsed = json.loads(sig_key)
        except (TypeError, ValueError):
            return sig_key[:80] if sig_key else "args sconosciuti"
    else:
        parsed = sig_key
    if not isinstance(parsed, list) or not parsed:
        return str(parsed)[:80] if parsed else "args sconosciuti"
    head = parsed[0]
    if head == "dedupe" and len(parsed) >= 4:
        return f"dedupe({parsed[2]} -> {parsed[3]}, reason={parsed[1]})"
    if head == "generalize" and len(parsed) >= 2:
        seq = parsed[1]
        if isinstance(seq, list):
            return f"generalize({' -> '.join(str(s) for s in seq)})"
        return f"generalize({seq})"
    if head == "specialize" and len(parsed) >= 4:
        return f"specialize({parsed[1]}, arg={parsed[2]}, val={parsed[3]})"
    return str(parsed)[:80]


def _describe_new_executor(name: str, args_schema: dict) -> str:
    """Descrive il nuovo executor in forma `name(arg1, arg2, ...)`.

    Estrae i required args da `args_schema.required` o le properties top-level.
    """
    args_str = ""
    if isinstance(args_schema, dict):
        req = args_schema.get("required") or []
        if isinstance(req, list) and req:
            args_str = ", ".join(str(a) for a in req)
        else:
            props = args_schema.get("properties") or {}
            if isinstance(props, dict) and props:
                # Prendi i primi 4 nomi di property come hint.
                args_str = ", ".join(list(props.keys())[:4])
    return f"{name}({args_str})" if args_str else f"{name}()"


def _format_call_freq_pct(call_freq_60d: int | None,
                            n_total_60d: int | None) -> str:
    """Formatta la % di chiamate sostituite vs totale corpus 60g."""
    if not call_freq_60d or not n_total_60d or n_total_60d <= 0:
        if call_freq_60d:
            return f"{call_freq_60d} chiamate negli ultimi 60g"
        return "dati insufficienti"
    pct = (call_freq_60d / n_total_60d) * 100.0
    return (
        f"{pct:.1f}% delle chiamate ({call_freq_60d}/{n_total_60d} "
        f"negli ultimi 60g)"
    )


def _complement_description(verb: str, obj: str) -> str:
    """Descrive deterministicamente cosa il nuovo executor NON sostituisce.

    Heuristic: per verbi produttori (find/get/read/list/filter) il nuovo
    non sostituisce altre azioni sull'oggetto (write/delete/move/send).
    Per verbi trasformativi, dice che la versione read del dominio resta.
    """
    if not verb or not obj:
        return "azioni su oggetti diversi e altri verbi del catalogo"
    producers = {"find", "get", "read", "list", "filter"}
    transformers = {"move", "delete", "send", "write", "create", "extract",
                    "compress", "change", "set", "order"}
    if verb in producers:
        return (
            f"azioni trasformative su {obj} "
            f"(write/delete/move/send/create) ne' altri oggetti del catalogo"
        )
    if verb in transformers:
        return (
            f"lettura/discovery su {obj} "
            f"(find/get/read/list) ne' altri oggetti del catalogo"
        )
    return "azioni su oggetti diversi e altri verbi del catalogo"


def _aggregate_token_in_for_path_hash(path_hash: str) -> tuple[int, int]:
    """Aggrega `llm_in_tokens` medio sui turni col path_hash matching.

    Ritorna `(mean_tokens_in, sample_count)` — `(0, 0)` se nessun match.

    Heuristic: per stimare il token savings di una pipeline single-step
    nuova, prendiamo come baseline la SOMMA `llm_in_tokens` di tutti gli
    step produttivi del turno multi-step originale; la media su N turni
    matching e' il `prompt_size_in_old` riferimento.
    """
    if not path_hash:
        return (0, 0)
    base = _turns_dir()
    if not base.exists():
        return (0, 0)
    try:
        from path_shape import path_shape_hash
    except ImportError:
        return (0, 0)
    totals: list[int] = []
    for fp in sorted(base.glob("*.jsonl")):
        try:
            content = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (TypeError, ValueError):
                continue
            steps = rec.get("steps") or []
            if not steps:
                continue
            try:
                computed = path_shape_hash(steps)
            except Exception:
                continue
            if computed != path_hash:
                continue
            # Somma llm_in_tokens degli step produttivi (= path_shape):
            # uno step con chosen_tool vuoto e' il final_message LLM, lo
            # escludiamo per non gonfiare la baseline.
            turn_total = 0
            for s in steps:
                if not isinstance(s, dict):
                    continue
                tool = s.get("chosen_tool") or ""
                if not tool:
                    continue
                v = s.get("llm_in_tokens")
                if isinstance(v, (int, float)) and v > 0:
                    turn_total += int(v)
            if turn_total > 0:
                totals.append(turn_total)
    if not totals:
        return (0, 0)
    return (sum(totals) // len(totals), len(totals))


def _estimated_new_tokens_in() -> int:
    """Stima dei prompt_size_in per la nuova pipeline single-step.

    Approssimazione: core PLANNER ~6KB (Fase C section-aware) =
    ~1500 tokens. Single step = 1× core + step overhead trascurabile.
    """
    return int(_PLANNER_CORE_SIZE_BYTES * _TOKENS_PER_BYTE)


def _compute_perf_savings(
    eta_p50_old_ms: int | float | None,
    eta_p50_new_ms: int | float | None,
    *,
    path_hash: str,
    call_freq_60d: int | None,
) -> dict:
    """Calcola stima risparmio tempo + token + frequenza. §7.9 puro deterministico.

    Ritorna dict:
        {
            "time_savings_pct": float | None,
            "time_old_ms": int | None,
            "time_new_ms": int | None,
            "tokens_in_old_mean": int,
            "tokens_in_new_estimated": int,
            "tokens_savings_pct": float | None,
            "tokens_sample_count": int,
            "call_freq_60d": int | None,
            "time_fallback": str | None,   # ragione fallback time
            "tokens_fallback": str | None, # ragione fallback token
        }

    `time_savings_pct` o `tokens_savings_pct` = None quando dati insufficienti.
    """
    # Time savings: ((p50_old - p50_new) / p50_old) * 100.
    time_savings_pct: float | None = None
    time_fallback: str | None = None
    old_int = int(eta_p50_old_ms) if isinstance(
        eta_p50_old_ms, (int, float)) and eta_p50_old_ms > 0 else None
    new_int = int(eta_p50_new_ms) if isinstance(
        eta_p50_new_ms, (int, float)) and eta_p50_new_ms > 0 else None
    if old_int is None or new_int is None:
        time_fallback = "n/a (dati insufficienti)"
    elif new_int >= old_int:
        # Nessun risparmio o peggioramento: lo dichiariamo onesto §2.8.
        time_savings_pct = round(
            ((old_int - new_int) / old_int) * 100.0, 1
        )
    else:
        time_savings_pct = round(
            ((old_int - new_int) / old_int) * 100.0, 1
        )

    # Token savings: aggregato dai turni matching path_hash.
    tokens_old, n_samples = _aggregate_token_in_for_path_hash(path_hash)
    tokens_new = _estimated_new_tokens_in()
    tokens_savings_pct: float | None = None
    tokens_fallback: str | None = None
    if tokens_old <= 0 or n_samples == 0:
        tokens_fallback = "n/a (dati insufficienti)"
    elif tokens_new >= tokens_old:
        tokens_savings_pct = round(
            ((tokens_old - tokens_new) / tokens_old) * 100.0, 1
        )
    else:
        tokens_savings_pct = round(
            ((tokens_old - tokens_new) / tokens_old) * 100.0, 1
        )

    return {
        "time_savings_pct": time_savings_pct,
        "time_old_ms": old_int,
        "time_new_ms": new_int,
        "tokens_in_old_mean": tokens_old,
        "tokens_in_new_estimated": tokens_new,
        "tokens_savings_pct": tokens_savings_pct,
        "tokens_sample_count": n_samples,
        "call_freq_60d": call_freq_60d,
        "time_fallback": time_fallback,
        "tokens_fallback": tokens_fallback,
    }


def _format_perf_savings_block(savings: dict, *, lang: str = "it") -> str:
    """Render markdown della sezione `## Stima risparmio`. §7.9."""
    # Localizzazione minimale via toggle (i18n.sqlite e' overkill per 3 righe;
    # se servira' una terza lingua passeremo a `messages.get`).
    if lang == "en":
        title = "## Performance savings"
        time_label = "Time"
        tokens_label = "Tokens in"
        freq_label = "Frequency"
        per_turn = "/turn"
        per_60d = "calls/60d"
        sample_n = "N=%d turns analyzed"
    else:
        title = "## Stima risparmio"
        time_label = "Tempo"
        tokens_label = "Token in"
        freq_label = "Frequenza"
        per_turn = "/turno"
        per_60d = "chiamate/60g"
        sample_n = "N=%d turni analizzati"

    # Time row.
    if savings.get("time_fallback"):
        time_row = f"- {time_label}: {savings['time_fallback']}"
    else:
        pct = savings.get("time_savings_pct")
        old_ms = savings.get("time_old_ms")
        new_ms = savings.get("time_new_ms")
        time_row = (
            f"- {time_label}: {pct}% "
            f"(p50 OGGI {old_ms}ms → NUOVA {new_ms}ms)"
        )

    # Tokens row.
    if savings.get("tokens_fallback"):
        tok_row = f"- {tokens_label}: {savings['tokens_fallback']}"
    else:
        old_t = savings.get("tokens_in_old_mean") or 0
        pct = savings.get("tokens_savings_pct")
        n = savings.get("tokens_sample_count") or 0
        tok_row = (
            f"- {tokens_label}: ~{old_t}{per_turn} (-{pct}%) "
            f"[{sample_n % n}]"
        )

    # Frequency row.
    cf = savings.get("call_freq_60d")
    if cf is None:
        freq_row = f"- {freq_label}: dati insufficienti"
    else:
        freq_row = f"- {freq_label}: {cf} {per_60d}"

    return "\n".join([title, time_row, tok_row, freq_row])


def _render_llm_commentary(deterministic_data: dict, *, lang: str = "it") -> str:
    """Genera UN paragrafo LLM tier middle che spiega la promozione.

    Input `deterministic_data` shape:
        {
            "proposal_name": str,
            "sig_key": str | list | None,
            "eta_p50_old_ms": int | None,
            "eta_p50_new_ms": int | None,
            "eta_count_60d": int | None,
            "killers": list[str],
            "signals": dict,
            "top_query": str | None,
        }

    Determinismo: §7.9 si applica al FALLBACK. La narrativa interpretabile
    e' giustificata come spiegazione user-facing, NON come decisione (la
    decisione e' deterministica e gia' presa upstream da
    `proposal_evaluator`).

    Cap latenza: timeout 5s, fallback "(commento non disponibile)" su
    qualsiasi errore (provider down, timeout, malformed response).
    """
    # Render del system prompt da .j2 (ADR 0092).
    try:
        from prompt_loader import get as _prompt_get
    except ImportError:
        return _msg_commentary_unavailable(lang)

    # Serializza sig_key in forma stringa stabile per il prompt.
    sig_raw = deterministic_data.get("sig_key")
    if sig_raw is None:
        sig_str = "n/a"
    elif isinstance(sig_raw, str):
        sig_str = sig_raw[:200]
    else:
        try:
            sig_str = json.dumps(sig_raw, ensure_ascii=False)[:200]
        except (TypeError, ValueError):
            sig_str = str(sig_raw)[:200]

    killers = deterministic_data.get("killers") or []
    signals = deterministic_data.get("signals") or {}
    # Compatta i signals per il prompt (evita dump di tutto).
    sig_compact = {
        k: signals.get(k) for k in (
            "eta_speedup", "call_freq_60d", "decidability_pct",
            "pipeline_terminal", "token_saving_pct",
        ) if signals.get(k) is not None
    }
    try:
        system_prompt = _prompt_get(
            "promoter_commentary", lang,
            proposal_name=str(deterministic_data.get("proposal_name") or "?"),
            sig_key=sig_str,
            eta_p50_old_ms=str(deterministic_data.get("eta_p50_old_ms") or "n/a"),
            eta_p50_new_ms=str(deterministic_data.get("eta_p50_new_ms") or "n/a"),
            eta_count_60d=str(deterministic_data.get("eta_count_60d") or "n/a"),
            killers=json.dumps(killers, ensure_ascii=False),
            signals=json.dumps(sig_compact, ensure_ascii=False),
            top_query=str(deterministic_data.get("top_query") or "n/a")[:200],
        )
    except Exception:
        return _msg_commentary_unavailable(lang)

    # User payload: ripeti la richiesta in forma compatta cosi' il LLM
    # ha un focus chiaro anche se il system prompt e' parecchio lungo.
    user_payload = {
        "request": "produci_paragrafo_commento_promozione",
        "lang": lang,
    }

    try:
        from llm_helpers import call_llm
        # call_llm gestisce internamente il provider. Non c'e' un timeout
        # parametrico nativo: facciamo il guard con `concurrent.futures` cosi'
        # il cap di 5s e' onesto §2.8 anche se il provider si pianta.
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(
                call_llm,
                user_payload,
                system_prompt,
                tier="middle",
                max_tokens=_LLM_COMMENTARY_MAX_TOKENS,
                temperature=0.2,
                think=False,
            )
            try:
                text, _meta = future.result(
                    timeout=_LLM_COMMENTARY_TIMEOUT_S
                )
            except _cf.TimeoutError:
                return _msg_commentary_unavailable(lang)
    except Exception:
        # Qualsiasi crash del provider (ConnectionError, RuntimeError, ...)
        # → fallback deterministico.
        return _msg_commentary_unavailable(lang)

    text = (text or "").strip()
    if not text:
        return _msg_commentary_unavailable(lang)
    # Sanitize minimale: niente markdown headings dentro al paragrafo
    # (il commento e' UN paragrafo piano, il "## Commento" lo aggiunge
    # render_practical_example).
    text = text.replace("\r\n", "\n").strip()
    # Collassa eventuali doppie newline interne in singolo spazio (un
    # paragrafo solo, NON DEVI markdown).
    text = " ".join(line.strip() for line in text.split("\n") if line.strip())
    return text


def _msg_commentary_unavailable(lang: str) -> str:
    """Messaggio fallback quando LLM non e' disponibile."""
    if lang == "en":
        return "(commentary unavailable)"
    return "(commento non disponibile)"


def _build_deterministic_data_for_commentary(
    proposal: dict, evaluator_verdict: dict, *,
    eta_p50_old_ms: int | None, top_query: str,
) -> dict:
    """Aggrega i campi che il prompt LLM si aspetta come input."""
    name = proposal.get("name") or proposal.get("expected_name") or "?"
    sig_key = proposal.get("sig_key")
    signals = evaluator_verdict.get("signals") or {}
    killers = evaluator_verdict.get("killers_triggered") or []
    cf = signals.get("call_freq_60d")
    # eta_p50_new_ms: per ora non abbiamo una stima precisa pre-promote.
    # Usiamo la ETA aggregata stessa scalata su 1 step se disponibile,
    # altrimenti None.
    eta_p50_new_ms = None
    if isinstance(eta_p50_old_ms, (int, float)) and eta_p50_old_ms > 0:
        # Stima euristica: single-step ≈ tempo medio per-step.
        # `proposal.path_steps` ci dice quanti step c'erano in OGGI.
        steps_old = proposal.get("path_steps") or []
        n_old = max(1, len(steps_old))
        eta_p50_new_ms = int(eta_p50_old_ms / n_old)
    return {
        "proposal_name": name,
        "sig_key": sig_key,
        "eta_p50_old_ms": int(eta_p50_old_ms) if eta_p50_old_ms else None,
        "eta_p50_new_ms": eta_p50_new_ms,
        "eta_count_60d": cf,
        "killers": list(killers),
        "signals": signals,
        "top_query": top_query,
    }


# Marker HTML-comment usato dalla UI admin per rendere collassabile la
# sezione LLM (ADR 0090-stile: rendering opt-in). I test verificano la
# presenza letterale di questo marker.
_LLM_MARKER = "<!-- llm_commentary -->"


def render_practical_example(
    proposal: dict,
    evaluator_verdict: dict,
    *,
    catalog: Any = None,
    lang: str = "it",
    skip_llm: bool = False,
) -> str:
    """Compone l'esempio pratico in markdown a 3 sezioni.

    Sezione 1 — analisi deterministica §7.9.
    Sezione 2 — stima risparmio %, deterministica §7.9.
    Sezione 3 — UN paragrafo LLM (modello locale tier middle) con
        fallback "(commento non disponibile)" se LLM down/timeout.

    `skip_llm=True`: salta la sezione 3 (usato dai test che non vogliono
    dipendere da llamacpp; il default in produzione e' False).

    `proposal`: il dict JSON della proposta synth.
    `evaluator_verdict`: result.to_dict() da proposal_evaluator (per signals).
    `catalog`: opzionale, per arricchire la descrizione (non strettamente
        necessario, l'esempio resta affermativo anche senza catalog).
    `lang`: lingua dei titoli + del paragrafo LLM (default "it").
    """
    name = proposal.get("name") or proposal.get("expected_name") or "?"
    parts = name.split("_") if name else []
    verb = parts[0] if parts else ""
    obj = parts[1] if len(parts) >= 2 else ""

    # 1) Query reale o sintetica.
    path_hash = proposal.get("path_hash") or ""
    real_q = _find_real_query_for_path_hash(path_hash)
    if real_q is None:
        real_q = (proposal.get("user_query") or "").strip()
    if not real_q:
        real_q = f"[sintetico] esegui {verb} {obj}".strip()
    query_line = f"**Query**: {real_q}"

    # 2) Pipeline OGGI (path_steps + ETA).
    path_steps: list[str] = list(proposal.get("path_steps") or [])
    eta_p50_ms = None
    if not path_steps and path_hash:
        rec = _lookup_eta(path_hash)
        if rec:
            path_steps = list(rec.get("sample_steps") or [])
            eta_p50_ms = rec.get("p50_ms")
    elif path_hash:
        rec = _lookup_eta(path_hash)
        if rec:
            eta_p50_ms = rec.get("p50_ms")
    if path_steps:
        oggi_str = " -> ".join(path_steps)
        if eta_p50_ms:
            oggi_str = f"{oggi_str} (p50 {eta_p50_ms}ms)"
    else:
        oggi_str = "da definire"
    pipeline_oggi_line = f"**Pipeline OGGI**: {oggi_str}"

    # 3) Pipeline NUOVA.
    s2 = (proposal.get("stages") or [])
    args_schema: dict = {}
    if len(s2) >= 2 and isinstance(s2[1], dict):
        out = s2[1].get("output") or {}
        if isinstance(out, dict):
            # Stage 2 puo' avere args_schema o args_properties+args_required.
            if "args_schema" in out and isinstance(out["args_schema"], dict):
                args_schema = out["args_schema"]
            else:
                props = out.get("args_properties") or {}
                req = out.get("args_required") or []
                args_schema = {"properties": props, "required": req}
    nuova_desc = _describe_new_executor(name, args_schema)
    pipeline_nuova_line = f"**Pipeline NUOVA**: {nuova_desc}"

    # 4) Sostituisce: pct from signals.call_freq_60d.
    signals = evaluator_verdict.get("signals") or {}
    cf = signals.get("call_freq_60d")
    cf_total = signals.get("n_calls_60d_total") or signals.get("call_freq_total_60d")
    sostituisce_line = (
        f"**Sostituisce**: {_format_call_freq_pct(cf, cf_total)}"
    )

    # 5) NON sostituisce.
    non_line = f"**NON sostituisce**: {_complement_description(verb, obj)}"

    # Sezione 1 — blocco deterministico (esistente).
    section_1 = "\n".join([
        query_line,
        pipeline_oggi_line,
        pipeline_nuova_line,
        sostituisce_line,
        non_line,
    ])

    # Sezione 2 — stima risparmio (E2).
    savings = _compute_perf_savings(
        eta_p50_ms,
        _estimate_eta_new_ms(eta_p50_ms, path_steps),
        path_hash=path_hash,
        call_freq_60d=cf if isinstance(cf, int) else None,
    )
    section_2 = _format_perf_savings_block(savings, lang=lang)

    # Sezione 3 — commento LLM (E1) o fallback deterministico.
    if skip_llm:
        section_3 = ""
    else:
        det_data = _build_deterministic_data_for_commentary(
            proposal, evaluator_verdict,
            eta_p50_old_ms=eta_p50_ms,
            top_query=real_q,
        )
        commentary = _render_llm_commentary(det_data, lang=lang)
        commentary_title = "## Commentary" if lang == "en" else "## Commento"
        section_3 = "\n".join([
            _LLM_MARKER,
            commentary_title,
            commentary,
        ])

    sections = [section_1, section_2]
    if section_3:
        sections.append(section_3)
    return "\n\n".join(sections)


def _estimate_eta_new_ms(
    eta_p50_old_ms: int | float | None,
    path_steps: list[str],
) -> int | None:
    """Stima il p50 atteso della nuova pipeline single-step.

    Heuristic semplice §7.9: tempo medio per-step della pipeline old.
    Caso degenere (path_steps vuoto o eta old vuoto): None.
    """
    if not eta_p50_old_ms or eta_p50_old_ms <= 0:
        return None
    n_old = max(1, len(path_steps))
    return int(eta_p50_old_ms / n_old)


__all__ = [
    "render_practical_example",
    "_render_llm_commentary",
    "_compute_perf_savings",
    "_format_perf_savings_block",
    "_aggregate_token_in_for_path_hash",
    "_LLM_MARKER",
]
