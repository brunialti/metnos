"""get_proposals — review pull-on-demand delle proposte introvertive.

Legge i JSONL append-only prodotti da `introvertiva.run_all()`:

    ~/.local/share/metnos/introvertiva/candidates_<op>_<unix_ts>.jsonl

dove <op> ∈ {dedupe, generalize, specialize}. Ritorna entries strutturati
con `truncated:true` se l'output supera `max_results`.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Bootstrap universale runtime/ (rename/depth-agnostic, ADR 0148):
# env METNOS_RUNTIME settato da agent_runtime > fallback walk-up via marker.
sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
from config import PATH_EXECUTORS as _PATH_EXECUTORS  # noqa: E402

AUDIT_DIR = Path.home() / ".local" / "share" / "metnos" / "introvertiva"
_FNAME_RE = re.compile(r"^candidates_(?P<kind>dedupe|generalize|specialize)_(?P<ts>\d+)\.jsonl$")


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _file_ts(p: Path) -> int | None:
    m = _FNAME_RE.match(p.name)
    if not m:
        return None
    try:
        return int(m.group("ts"))
    except ValueError:
        return None


def _file_kind(p: Path) -> str | None:
    m = _FNAME_RE.match(p.name)
    return m.group("kind") if m else None


# Cache per i sommari executor (nome → 1ª frase della description del manifest).
# Ogni invocazione di get_proposals e' un subprocess fresco, ma il cache vive
# per il singolo turno (decine di lookup → costo trascurabile).
_EXEC_BRIEF_CACHE: dict[str, str] = {}


def _executor_brief(name: str) -> str:
    """Ritorna una sintesi di 1 frase di cosa fa l'executor `name`,
    leggendo dal suo manifest. Se non trovato, ritorna una string-fallback.
    """
    if name in _EXEC_BRIEF_CACHE:
        return _EXEC_BRIEF_CACHE[name]
    candidates = [
        _PATH_EXECUTORS / name / "manifest.toml",
        Path.home() / f".local/share/metnos/executors/{name}/manifest.toml",
    ]
    desc = None
    for p in candidates:
        if not p.exists():
            continue
        try:
            import tomllib
            with open(p, "rb") as f:
                data = tomllib.load(f)
            d = (data.get("description") or "").strip()
            # First sentence
            for sep in (". ", "! ", "? ", ".\n", "\n"):
                idx = d.find(sep)
                if 0 < idx < 200:
                    d = d[:idx].strip()
                    break
            if len(d) > 160:
                d = d[:157].rstrip() + "…"
            desc = d
            break
        except Exception:
            continue
    if not desc:
        desc = f"(executor «{name}» non più nel catalog)"
    _EXEC_BRIEF_CACHE[name] = desc
    return desc


def _suggest_macro_name(pattern: list[str]) -> tuple[str, str]:
    """Dato una catena, ritorna (proposed_name, status).

    status ∈ {'ok', 'cyclic', 'too_short'}.

    `cyclic` indica che la catena ha elementi ripetuti adiacenti o
    primo/ultimo uguali (es. write→fetch→write): probabilmente non e'
    un macro semanticamente sensato.
    """
    if not pattern or len(pattern) < 2:
        return ("", "too_short")
    if pattern[0] == pattern[-1] and len(pattern) >= 3:
        return ("", "cyclic")
    if len(pattern) >= 2 and any(
        pattern[i] == pattern[i + 1] for i in range(len(pattern) - 1)
    ):
        return ("", "cyclic")
    # Naming euristico: <verbo del primo>_<oggetto dell'ultimo>.
    # Es. get_urls + write_files → "get_files" non va bene (collide con
    # get_files se esistesse).  Meglio: «<primo_full>_then_<ultimo_oggetto>».
    first = pattern[0]
    last = pattern[-1]
    # Estrai oggetto dall'ultimo (es. write_files → files)
    last_obj = last.split("_", 1)[1] if "_" in last else last
    return (f"{first}_to_{last_obj}", "ok")


def _canonical_key(kind: str, payload: dict) -> tuple:
    """Chiave canonica di una proposta: identifica «la stessa raccomandazione»
    fra audit JSONL diversi. Dedup self-referenziale.

    Per kind:
      - dedupe:     (kind_sub, src_executor, dst_executor)
      - generalize: tuple(pattern)
      - specialize: (executor, arg_name, str(dominant_value))
    """
    if kind == "dedupe":
        return (
            "dedupe",
            payload.get("kind", ""),
            payload.get("src_executor", ""),
            payload.get("dst_executor", ""),
        )
    if kind == "generalize":
        return ("generalize", tuple(payload.get("pattern") or ()))
    if kind == "specialize":
        return (
            "specialize",
            payload.get("executor", ""),
            payload.get("arg_name", ""),
            str(payload.get("dominant_value", "")),
        )
    # fallback: hash dei campi del payload (raro)
    return (kind, json.dumps(payload, sort_keys=True))


def invoke(args: dict, ctx: dict | None = None) -> dict:
    kind = args.get("kind", "all")
    if kind not in ("dedupe", "generalize", "specialize", "all"):
        return {"ok": False, "error": _msg("ERR_ARG_ENUM", arg="kind", allowed="dedupe | generalize | specialize | all")}
    max_results = int(args.get("max_results", 50))
    if max_results < 1:
        return {"ok": False, "error": _msg("ERR_ARG_NOT_POSITIVE_INT", arg="max_results")}
    include_dormant = bool(args.get("include_dormant", False))

    # Default since: 7 days ago
    since_iso = args.get("since_iso")
    since_dt = _parse_iso(since_iso) if since_iso else (
        datetime.now(timezone.utc) - timedelta(days=7)
    )
    since_unix = int(since_dt.timestamp()) if since_dt else 0

    if not AUDIT_DIR.exists():
        return {
            "ok": True, "ok_count": 0, "fail_count": 0,
            "entries": [], "truncated": False,
            "summary_by_kind": {"dedupe": 0, "generalize": 0, "specialize": 0},
            "audit_dir": str(AUDIT_DIR),
        }

    # Collect JSONL files matching kind, newer than since.
    candidates: list[tuple[int, str, Path]] = []
    for p in AUDIT_DIR.iterdir():
        if not p.is_file():
            continue
        f_kind = _file_kind(p)
        f_ts = _file_ts(p)
        if f_kind is None or f_ts is None:
            continue
        if kind != "all" and f_kind != kind:
            continue
        if f_ts < since_unix:
            continue
        candidates.append((f_ts, f_kind, p))

    # Newest first (so when we dedupe we keep the most recent occurrence)
    candidates.sort(key=lambda t: t[0], reverse=True)

    seen_keys: set[tuple] = set()
    entries: list[dict] = []
    by_kind = {"dedupe": 0, "generalize": 0, "specialize": 0}
    available_total = 0
    duplicates_collapsed = 0

    for ts, k, p in candidates:
        try:
            with open(p, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Canonical key per kind: identifies the "same proposal" across
            # multiple audit runs. Two proposals with the same key are the
            # same recommendation; we keep only the most recent occurrence
            # (we're iterating newest-first).
            key = _canonical_key(k, payload)
            if key in seen_keys:
                duplicates_collapsed += 1
                continue
            seen_keys.add(key)

            # Filtra fuori le proposte sopite (dormant) di default —
            # tornano visibili solo se il chiamante mette
            # include_dormant=true. Questo evita rumore sulle proposte
            # che l'utente ha implicitamente ignorato per N notti.
            if not include_dormant:
                try:
                    # runtime/ già su sys.path dal bootstrap a top-level (METNOS_RUNTIME-aware).
                    from proposals_state import is_dormant
                    if is_dormant(key):
                        continue
                except Exception:
                    # In dev (no DB ancora), tratta come non-dormant.
                    pass

            available_total += 1
            by_kind[k] = by_kind.get(k, 0) + 1
            if len(entries) < max_results:
                entries.append({
                    "kind": k,
                    "audit_ts": ts,
                    "audit_iso": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                    "audit_file": p.name,
                    "payload": payload,
                })

    truncated = available_total > len(entries)

    # Human-readable summary (1-2 righe): riusato dal runtime quando deve
    # auto-finalizzare senza passare per il LLM, e dal PLANNER come riassunto
    # rapido per richieste generiche («quante proposte ci sono»).
    bits = []
    for k in ("dedupe", "generalize", "specialize"):
        n = by_kind.get(k, 0)
        if n > 0:
            bits.append(f"{n} {k}")
    if bits:
        head = "Proposte introvertive: " + " + ".join(bits)
    else:
        head = "Nessuna proposta introvertiva nel periodo."

    sample = next(
        (e for e in entries if e.get("kind") in ("dedupe", "generalize")),
        entries[0] if entries else None,
    )
    if sample:
        k = sample["kind"]
        p = sample.get("payload", {})
        if k == "dedupe":
            head += f". Es.: {p.get('src_executor','?')} → {p.get('dst_executor','?')} ({p.get('uses', 0)} uses)"
        elif k == "generalize":
            chain = " → ".join(p.get("pattern", []))
            head += f". Es.: catena «{chain}» (uses {p.get('uses', 0)})"
        elif k == "specialize":
            head += f". Es.: {p.get('executor','?')} arg {p.get('arg_name','?')} dominante"

    # Detail multi-line: per richieste mirate (kind specifico) costruisce un
    # blocco pronto da restituire come final_answer. Il PLANNER puo' usarlo
    # tale e quale, senza dover riassumere a freddo le entries dal payload.
    # Per kind="all" il detail e' troppo lungo: lo costruiamo ugualmente con
    # tetto a 12 righe totali per non saturare il messaggio Telegram.
    detail_md = _render_detail(entries, kind, max_lines=12 if kind == "all" else 20)

    return {
        "ok": True,
        "ok_count": len(entries),
        "fail_count": 0,
        "entries": entries,
        "summary_by_kind": by_kind,
        "summary": head,
        "detail_md": detail_md,
        "available_total": available_total,
        "used": len(entries),
        "truncated": truncated,
        "truncated_what": "proposal" if truncated else None,
        "truncated_intentional": truncated,  # max_results is user-requested cap
        "cap_field": "max_results" if truncated else None,
        "cap_value": max_results if truncated else None,
        "audit_dir": str(AUDIT_DIR),
        "filters": {
            "kind": kind,
            "since_iso": since_dt.isoformat() if since_dt else None,
        },
    }


def _explain(idx: int, entry: dict) -> str:
    """Renderizza UNA proposta come paragrafo auto-esplicativo in italiano.

    Ogni proposta segue la struttura:
      «#N — TITOLO»
      cosa propone, in 1-2 frasi.
      perche' ha senso (dati osservati nel corpus).
      cosa cambia se accetti.
      «Vuoi che proceda?»
    """
    k = entry.get("kind", "?")
    p = entry.get("payload", {}) or {}

    if k == "dedupe":
        src  = p.get("src_executor", "?")
        dst  = p.get("dst_executor", "?")
        uses = p.get("uses", 0)
        sub  = p.get("kind", "")
        in_src = p.get("src_in_catalog", True)
        in_dst = p.get("dst_in_catalog", True)
        if sub == "legacy_orphan" and not in_src and in_dst:
            return (
                f"#{idx} — Riconcilia mnest fossile «{src} → {dst}»\n"
                f"Cosa propongo: il mnest «{src} → {dst}» ha {uses} esecuzioni "
                f"alle spalle (peso {p.get('weight', 0):.2f}), ma «{src}» non "
                f"esiste piu' nel catalog (probabilmente rinominato). "
                f"Lo collego al successore corrente di «{src}» nel grafo.\n"
                f"Cosa cambia: la storia di esecuzione viene preservata sotto il "
                f"nuovo nome; le statistiche del mnestoma tornano coerenti. "
                f"Operazione reversibile (audit + restore_blob_backup).\n"
                f"Vuoi che proceda? (sì / no / ignora per sempre)"
            )
        if sub == "legacy_orphan" and in_src and not in_dst:
            return (
                f"#{idx} — Riconcilia mnest fossile «{src} → {dst}»\n"
                f"Cosa propongo: il mnest ha {uses} esecuzioni ma «{dst}» non "
                f"esiste piu' nel catalog. Trovo il successore di «{dst}» e "
                f"riallineo il grafo.\n"
                f"Cosa cambia: solo il grafo del mnestoma. Niente impatto sui "
                f"turni futuri.\n"
                f"Vuoi che proceda? (sì / no / ignora per sempre)"
            )
        return (
            f"#{idx} — Consolida doppione «{src} → {dst}»\n"
            f"Cosa propongo: il mnest «{src} → {dst}» appare duplicato nel "
            f"corpus ({uses} esecuzioni totali). Unifico in un singolo nodo.\n"
            f"Cosa cambia: il grafo si semplifica; nessun impatto operativo.\n"
            f"Vuoi che proceda? (sì / no / ignora)"
        )

    if k == "generalize":
        pattern = p.get("pattern", [])
        chain   = " → ".join(pattern) if pattern else "(catena vuota)"
        uses    = p.get("uses", 0)
        score   = p.get("score") or 0.0
        intents = p.get("distinct_intents", 0)

        if not pattern or uses == 0:
            return (
                f"#{idx} — Catena candidata vuota (skip)\n"
                f"Cosa propongo: nulla — la catena è vuota o senza esecuzioni "
                f"effettive. Probabile residuo di parsing dei turni.\n"
                f"Vuoi rimuoverla dall'audit? (sì / no)"
            )

        macro, status = _suggest_macro_name(pattern)

        if status == "cyclic":
            return (
                f"#{idx} — Catena ciclica «{chain}» — non sensata come macro\n"
                f"Cosa propongo: NIENTE. Questa catena ha primo e ultimo step "
                f"uguali (o passi consecutivi ripetuti): è un ciclo, non una "
                f"sequenza lineare. Probabile artefatto dei turni di test "
                f"(esecuzione doppia dello stesso write/fetch). Un macro "
                f"qui non avrebbe senso operativo.\n"
                f"Cosa cambia: nessuna azione. La proposta resta in audit "
                f"come segnale che il pattern di turni va indagato.\n"
                f"Vuoi ignorarla per sempre? (sì / no)"
            )

        # Costruzione del paragrafo con descrizioni reali degli step
        steps_lines = []
        for s_name in pattern:
            steps_lines.append(f"    – {s_name}: {_executor_brief(s_name)}")
        steps_block = "\n".join(steps_lines)

        # Esempi prima/dopo (semplificati)
        pattern[0]
        pattern[-1]
        before_lines = []
        for i, s in enumerate(pattern, start=1):
            before_lines.append(f"    {i}. {s}(...)")
        before_block = "\n".join(before_lines)

        return (
            f"#{idx} — Macro-executor «{macro}» da catena «{chain}»\n"
            f"\n"
            f"Cosa fa oggi: per ottenere quello che chiedi, il sistema "
            f"compone {len(pattern)} executor in cascata:\n"
            f"{steps_block}\n"
            f"Hai eseguito questa sequenza {uses} volte negli ultimi turni, "
            f"in {intents} intent semanticamente diversi (score {score:.2f}). "
            f"È un pattern stabile, non occasionale.\n"
            f"\n"
            f"Cosa propongo: creare un nuovo executor «{macro}» che esegue "
            f"i {len(pattern)} passi in una sola chiamata, ricevendo gli "
            f"argomenti del primo step e producendo l'output dell'ultimo.\n"
            f"\n"
            f"Esempio del cambiamento. PRIMA (oggi):\n"
            f"{before_block}\n"
            f"DOPO (con il macro):\n"
            f"    1. {macro}(...)\n"
            f"\n"
            f"Cosa cambia: turni futuri di {len(pattern) - 1} step più "
            f"brevi, meno round-trip al pianificatore, semantica più "
            f"chiara nel manifest. L'executor originale resta in pool. "
            f"Reversibile (lifecycle: deprecated → archived).\n"
            f"\n"
            f"Vuoi che lo crei? (sì / no / dimmi un nome diverso)"
        )

    if k == "specialize":
        ex   = p.get("executor", "?")
        arg  = p.get("arg_name", "?")
        val  = p.get("dominant_value", "?")
        dom  = (p.get("dominance") or 0.0) * 100
        uses = p.get("total_uses", 0)
        prop = p.get("proposed_name") or f"{ex}_specialized"
        val_s = str(val)
        if len(val_s) > 40:
            val_s = val_s[:37] + "…"
        # Esempio prima/dopo concreto: il vantaggio si vede meglio leggendo
        # le due chiamate fianco a fianco che leggendo statistiche.
        return (
            f"#{idx} — Variante «{prop}» di «{ex}»\n"
            f"\n"
            f"Osservato: nel {dom:.0f}% delle {uses} chiamate a «{ex}», "
            f"il parametro «{arg}» vale sempre {val_s}.\n"
            f"\n"
            f"Esempio prima:\n"
            f"  {ex}(entries=…, {arg}={val_s}, …)\n"
            f"Esempio dopo:\n"
            f"  {prop}(entries=…)\n"
            f"\n"
            f"Cosa cambia: i turni futuri non devono più passare «{arg}» — "
            f"la variante lo ha già impostato. Originale resta nel pool, "
            f"la variante è aggiunta a fianco; reversibile (deprecated → "
            f"archived).\n"
            f"\n"
            f"Vuoi che la crei? (sì / no)"
        )

    return f"#{idx} — Proposta sconosciuta: {str(p)[:120]}"


def _render_detail(entries: list[dict], kind: str, *, max_lines: int) -> str:
    """Costruisce il detail multi-line leggibile delle entries.

    Per `kind` specifico (dedupe/generalize/specialize): ogni entry diventa
    un PARAGRAFO auto-esplicativo (4-6 righe) con cosa, perche', effetto e
    domanda secca.

    Per `kind=all`: panoramica compatta (un titolo per gruppo + 1 paragrafo
    rappresentativo per kind), tetto di proposte gestibile su Telegram.

    `max_lines` non ha effetto stretto qui (entries → paragrafi); lo usiamo
    per limitare il numero di proposte renderizzate.
    """
    if not entries:
        return "Nessuna proposta nel periodo richiesto."

    # Per `kind=all` mostriamo solo 1 esempio per kind (il piu' rilevante)
    # con paragrafo completo + un riepilogo dei kind.
    if kind == "all":
        groups: dict[str, list[dict]] = {}
        for e in entries:
            groups.setdefault(e.get("kind", "?"), []).append(e)
        blocks: list[str] = []
        idx_global = 0
        for ks in ("dedupe", "generalize", "specialize"):
            items = groups.get(ks, [])
            if not items:
                continue
            idx_global += 1
            blocks.append(f"━━━ {ks.upper()} ({len(items)} totali) ━━━")
            blocks.append(_explain(idx_global, items[0]))
            if len(items) > 1:
                blocks.append(
                    f"...altre {len(items) - 1} proposte di tipo {ks}. "
                    f"Per vederle: «mostra le {ks}»."
                )
            blocks.append("")
        return "\n".join(blocks).rstrip()

    # Single-kind: paragrafi pieni per ogni entry, fino a un massimo.
    # Telegram cap di 4096 char/messaggio + l'utente non riesce a decidere
    # 5+ proposte alla volta su chat. Tetto stretto: 3 per turno.
    cap = 3
    blocks = [f"━━━ {kind.upper()} ({len(entries)} totali) ━━━", ""]
    for i, e in enumerate(entries[:cap], start=1):
        blocks.append(_explain(i, e))
        blocks.append("")
    if len(entries) > cap:
        blocks.append(
            f"…altre {len(entries) - cap} {kind} non mostrate. Per quelle "
            f"successive chiedi «mostra le {kind} dopo la {cap}», oppure "
            f"filtra: «{kind} con dominance > 0.9» o «top 3 {kind} per uses»."
        )
    return "\n".join(blocks).rstrip()



def main():
    run_stdio(invoke, default=str, allow_empty=True)


if __name__ == "__main__":
    main()
