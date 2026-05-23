#!/usr/bin/env python3
"""Corpus extractor — legge i log live e produce `corpus.sqlite`
classificato per i test E2E.

Sorgenti:
  - `~/.local/share/metnos/turns/*.jsonl`  (corpus turni reali)
  - `<workspace>/.mnestoma/mnest.sqlite::canonical_query_log` (L1 cache)
  - `~/.local/share/metnos/multi_tool_paths.sqlite::multi_tool_paths` (L2)
  - `~/.local/share/metnos/turn_feedback.jsonl` (feedback ✓/✗)

Output:
  - `corpus.sqlite` con tabella `queries` classificata (anonimizzata, dedup)
  - `samples/by_category.json` per ispezione manuale

Classificazione automatica deterministica (§7.9):
  - category: fast_path_L0 | fast_path_L1 | fast_path_L2 | planner | dialog | undo | admin
  - domain : mail | calendar | files | processes | web | system | introspective
  - mutating: 1 se verbo primario in MUTATING_VERBS (§2.2)
  - success: feedback explicit > inferred da final_kind/error_class

Anonimizzazione (no PII leak): email, phone, IP, user paths assoluti.
Dedup: letterale (n_seen aggregato), semantic via Jaccard (>0.85).

NO IMPORT da runtime/. Tabelle vocabulary inline (zero coupling).

Uso:
  python3 corpus/extract.py [--since DAYS] [--output PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path


# --- Vocab inline (NO import runtime/vocab.py) ---------------------------

# Verbi mutating §2.2 (subset di vocab.ACTIONS che modificano stato)
_MUTATING_VERBS = frozenset({
    "create", "write", "delete", "set", "send", "share",
    "change", "order", "move",
})

# Mapping object → domain (subset _OBJECT_TO_SECTIONS, hardcoded per
# zero coupling). Sufficiente per classificazione corpus.
_OBJECT_DOMAIN = {
    "messages": "mail",
    "events": "calendar",
    "files": "files",
    "dirs": "files",
    "contacts": "calendar",
    "processes": "processes",
    "urls": "web",
    "texts": "web",
    "persons": "introspective",
    "tasks": "system",
    "places": "system",
    "numbers": "system",
    "images": "files",
    "signatures": "introspective",
    "proposals": "introspective",
    "inputs": "system",
    "credentials": "system",
    "entries": "system",
}

# Verbi system (undo_last_turn, admin, builtin) — non hanno object suffix
_SYSTEM_VERBS = frozenset({
    "undo_last_turn", "admin", "consult_frontier", "final_answer",
    "request_new_executor", "get_now", "get_location",
    "scratchpad_read",
})


def _classify_tool(tool_name: str) -> tuple[str, str]:
    """Ritorna `(verb, domain)` per un tool name."""
    if tool_name in _SYSTEM_VERBS:
        return tool_name, "system"
    if "_" not in tool_name:
        return tool_name, "unknown"
    verb = tool_name.split("_", 1)[0]
    parts = tool_name.split("_")
    obj = parts[1] if len(parts) >= 2 else ""
    domain = _OBJECT_DOMAIN.get(obj, "unknown")
    return verb, domain


def _is_mutating(tool_name: str) -> bool:
    verb, _ = _classify_tool(tool_name)
    return verb in _MUTATING_VERBS


# --- Anonimizzazione PII --------------------------------------------------

_USER_HOME = str(Path.home())
_PII_PATTERNS = [
    # Email
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "<email>"),
    # Phone IT (+39 prefix or 10-digit)
    (re.compile(r"(?:\+39\s?)?\d{3}[\s.-]?\d{3}[\s.-]?\d{4}"), "<phone>"),
    # IPv4
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "<ip>"),
    # Path utente (sostituisci /home/<user> con /home/<user>)
    (re.compile(re.escape(_USER_HOME)), "/home/<user>"),
]


def _anonymize(text: str) -> str:
    if not text:
        return text
    out = text
    for pat, repl in _PII_PATTERNS:
        out = pat.sub(repl, out)
    return out


# --- Dedup Jaccard --------------------------------------------------------

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokens(s: str) -> frozenset:
    return frozenset(t.lower() for t in _TOKEN_RE.findall(s or "") if len(t) >= 3)


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    inter = a & b
    union = a | b
    return len(inter) / len(union) if union else 0.0


# --- Classificazione category --------------------------------------------

def _classify_category(turn: dict, fast_path_l1: bool, fast_path_l2: bool) -> str:
    """Deriva category da turn structure + fast-path metadata."""
    steps = turn.get("steps") or []
    n_real_steps = sum(1 for s in steps if s.get("chosen_tool") and s["chosen_tool"] != "final_answer")
    tools_used = [s.get("chosen_tool", "") for s in steps]
    if "undo_last_turn" in tools_used:
        return "undo"
    if "admin" in tools_used:
        return "admin"
    if any(t == "get_inputs" for t in tools_used):
        return "dialog"
    if fast_path_l2:
        return "fast_path_L2"
    if fast_path_l1 and n_real_steps == 1:
        return "fast_path_L1"
    if n_real_steps == 0 and turn.get("final_kind") == "answer":
        # Fast-path L0 (literal pattern matching, niente tool calls)
        return "fast_path_L0"
    return "planner"


def _classify_domain(turn: dict) -> str:
    """Domain dal primo tool produttore (non final_answer)."""
    for step in turn.get("steps") or []:
        tool = step.get("chosen_tool", "")
        if tool and tool != "final_answer":
            _, domain = _classify_tool(tool)
            return domain
    return "unknown"


def _infer_success(turn: dict, feedback_by_turn: dict) -> int | None:
    """Feedback esplicito > inferenza. Ritorna 1/0/None."""
    tid = turn.get("turn_id")
    if tid and tid in feedback_by_turn:
        action = feedback_by_turn[tid]
        if action == "ok":
            return 1
        if action == "error":
            return 0
    # Inferenza
    final_kind = turn.get("final_kind", "")
    if final_kind == "answer":
        return 1
    if final_kind in ("error", "timeout", "loop_break"):
        return 0
    return None


# --- Loaders --------------------------------------------------------------

def _user_data() -> Path:
    return Path(os.environ.get("METNOS_USER_DATA",
                                Path.home() / ".local/share/metnos"))


def _load_turns(since_days: int) -> list[dict]:
    """Carica tutti i turni post `since_days`. Solo file con date >=
    cutoff. Skip silenziosamente jsonl malformed."""
    turns_dir = _user_data() / "turns"
    if not turns_dir.is_dir():
        return []
    cutoff = time.time() - since_days * 86400
    out: list[dict] = []
    for f in sorted(turns_dir.glob("*.jsonl")):
        # Fast skip per data file (yyyy-mm-dd.jsonl)
        try:
            file_date = time.mktime(time.strptime(f.stem, "%Y-%m-%d"))
            if file_date < cutoff - 86400:
                continue
        except ValueError:
            pass
        try:
            with f.open() as fp:
                for line in fp:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if float(rec.get("ts_start", 0)) >= cutoff:
                        out.append(rec)
        except OSError:
            continue
    return out


def _load_feedback() -> dict:
    """`turn_id → action` dal turn_feedback.jsonl (LWW per turn_id)."""
    fpath = _user_data() / "turn_feedback.jsonl"
    out: dict[str, str] = {}
    if not fpath.exists():
        return out
    try:
        with fpath.open() as fp:
            for line in fp:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tid = rec.get("turn_id")
                action = rec.get("action")
                if tid and action:
                    out[tid] = action
    except OSError:
        pass
    return out


def _load_canonical_log() -> dict:
    """`turn_id → True` se la query era L1 hit. Indagato via
    `canonical_query_log` cross-ref. In assenza di link diretto, marca
    L1 le canonical query con uses >= 1 (matched almeno una volta)."""
    workspace = Path(__file__).resolve().parents[2] / "workspace"
    mnest = workspace / ".mnestoma" / "mnest.sqlite"
    if not mnest.exists():
        return {}
    out: dict[str, bool] = {}
    cn = sqlite3.connect(str(mnest), timeout=10.0)
    try:
        rows = cn.execute(
            "SELECT canonical_query FROM canonical_query_log WHERE uses>=1"
        ).fetchall()
        # Indicizza per stringa (lookup approssimato in fase classification)
        for (cq,) in rows:
            out[cq] = True
    finally:
        cn.close()
    return out


def _load_multi_tool() -> dict:
    """`canonical_query → True` se shape L2 attiva."""
    db = _user_data() / "multi_tool_paths.sqlite"
    if not db.exists():
        return {}
    out: dict[str, bool] = {}
    cn = sqlite3.connect(str(db), timeout=10.0)
    try:
        try:
            rows = cn.execute(
                "SELECT canonical_query FROM multi_tool_paths WHERE state IN ('active','shadow')"
            ).fetchall()
        except sqlite3.Error:
            return {}
        for (cq,) in rows:
            out[cq] = True
    finally:
        cn.close()
    return out


# --- Schema corpus.sqlite -------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    query           TEXT NOT NULL,
    query_canonical TEXT,
    lang            TEXT,
    category        TEXT NOT NULL,
    domain          TEXT NOT NULL,
    tools_used      TEXT NOT NULL,  -- JSON list
    n_steps         INTEGER NOT NULL,
    final_kind      TEXT,
    mutating        INTEGER NOT NULL DEFAULT 0,
    success         INTEGER,  -- 1/0/NULL
    n_seen          INTEGER NOT NULL DEFAULT 1,
    latency_ms_p50  INTEGER,
    last_seen       TEXT,
    dedup_master_id INTEGER  -- NULL = master, else FK a master
);
CREATE INDEX IF NOT EXISTS idx_q_category ON queries(category);
CREATE INDEX IF NOT EXISTS idx_q_domain   ON queries(domain);
CREATE INDEX IF NOT EXISTS idx_q_success  ON queries(success);
CREATE INDEX IF NOT EXISTS idx_q_n_seen   ON queries(n_seen DESC);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


# --- Lang heuristic -------------------------------------------------------

_IT_MARKERS = frozenset({
    "il", "la", "i", "le", "di", "che", "con", "per", "del", "della",
    "una", "uno", "ho", "ha", "sono", "fai", "scarica", "mostra", "trova",
    "cerca", "elenca", "leggi", "manda", "invia", "crea", "cancella",
    "domani", "oggi", "ieri", "stamattina", "stasera",
})
_EN_MARKERS = frozenset({
    "the", "of", "is", "and", "to", "in", "for", "with", "send", "find",
    "search", "read", "list", "show", "create", "delete", "tomorrow",
    "today", "yesterday", "morning", "evening",
})


def _detect_lang(text: str) -> str:
    toks = _tokens(text)
    if not toks:
        return "it"  # default
    it_hits = len(toks & _IT_MARKERS)
    en_hits = len(toks & _EN_MARKERS)
    if it_hits >= en_hits:
        return "it"
    return "en"


# --- Main extraction ------------------------------------------------------

def extract(since_days: int, output_path: Path) -> dict:
    turns = _load_turns(since_days)
    feedback = _load_feedback()
    canonical_l1 = _load_canonical_log()
    multi_tool_l2 = _load_multi_tool()

    # Aggregazione per query letterale
    by_query: dict[str, list[dict]] = defaultdict(list)
    for t in turns:
        q = (t.get("user_query") or "").strip()
        if not q:
            continue
        by_query[q].append(t)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()  # rewrite full
    cn = sqlite3.connect(str(output_path))
    cn.executescript(_SCHEMA)

    cat_counter: Counter = Counter()
    dom_counter: Counter = Counter()
    suc_counter: Counter = Counter()
    inserted_rows: list[tuple] = []

    for q_raw, group in by_query.items():
        q = _anonymize(q_raw)
        # Use first turn of group as representative for classification
        rep = group[0]
        n_seen = len(group)
        tools = [s.get("chosen_tool", "") for s in rep.get("steps") or []
                 if s.get("chosen_tool")]
        canonical_match = None
        # Match canonical lookup (approximated): exact match
        for cq in canonical_l1:
            if cq == q_raw or cq == q_raw.lower().strip():
                canonical_match = cq
                break
        fp_l1 = canonical_match is not None
        fp_l2 = False
        for cq in multi_tool_l2:
            if cq == q_raw or cq.lower() == q_raw.lower():
                fp_l2 = True
                break
        category = _classify_category(rep, fp_l1, fp_l2)
        domain = _classify_domain(rep)
        mutating = 1 if any(_is_mutating(t) for t in tools) else 0
        success_votes = [_infer_success(t, feedback) for t in group]
        # majority vote (None counts as None)
        success = None
        if any(s == 0 for s in success_votes):
            # Almeno un fail → marca come fail (conservative)
            success = 0
        elif all(s == 1 for s in success_votes if s is not None):
            if any(s == 1 for s in success_votes):
                success = 1

        # latency p50
        latencies = []
        for t in group:
            tot = 0
            for s in t.get("steps") or []:
                for k in ("llm_latency_ms", "exec_ms", "intent_ms"):
                    v = s.get(k)
                    if isinstance(v, (int, float)):
                        tot += int(v)
            if tot > 0:
                latencies.append(tot)
        latencies.sort()
        p50 = latencies[len(latencies) // 2] if latencies else None

        last_ts = max(float(t.get("ts_start", 0)) for t in group)
        last_seen = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(last_ts))

        cat_counter[category] += 1
        dom_counter[domain] += 1
        suc_counter[str(success)] += 1

        inserted_rows.append((
            q, _anonymize(canonical_match or ""), _detect_lang(q),
            category, domain, json.dumps(tools, ensure_ascii=False),
            len(tools), rep.get("final_kind", ""),
            mutating, success, n_seen, p50, last_seen,
        ))

    cn.executemany(
        """INSERT INTO queries(
            query, query_canonical, lang, category, domain,
            tools_used, n_steps, final_kind, mutating, success,
            n_seen, latency_ms_p50, last_seen
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        inserted_rows,
    )

    # Dedup semantic via Jaccard >= 0.85 (post-insert, set dedup_master_id)
    rows = cn.execute("SELECT id, query FROM queries ORDER BY id").fetchall()
    tokens_by_id = {rid: _tokens(q) for rid, q in rows}
    masters = []  # list[(id, tokens)] di "canonici"
    dedup_map: dict[int, int] = {}
    for rid, q in rows:
        toks = tokens_by_id[rid]
        matched = None
        for m_rid, m_toks in masters:
            if _jaccard(toks, m_toks) >= 0.85:
                matched = m_rid
                break
        if matched is None:
            masters.append((rid, toks))
        else:
            dedup_map[rid] = matched
    for child_id, master_id in dedup_map.items():
        cn.execute(
            "UPDATE queries SET dedup_master_id=? WHERE id=?",
            (master_id, child_id),
        )

    # Meta
    cn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)",
               ("extracted_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
    cn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)",
               ("since_days", str(since_days)))
    cn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)",
               ("n_turns_scanned", str(len(turns))))
    cn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)",
               ("n_unique_queries", str(len(inserted_rows))))
    cn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)",
               ("n_dedup_groups", str(len(dedup_map))))
    cn.commit()
    cn.close()

    return {
        "n_turns_scanned": len(turns),
        "n_unique_queries": len(inserted_rows),
        "n_dedup_groups": len(dedup_map),
        "by_category": dict(cat_counter),
        "by_domain": dict(dom_counter),
        "by_success": dict(suc_counter),
    }


def write_samples(output_path: Path, samples_dir: Path,
                   per_category: int = 10) -> None:
    """Esporta sample JSON per category (per ispezione manuale)."""
    samples_dir.mkdir(parents=True, exist_ok=True)
    cn = sqlite3.connect(str(output_path))
    cn.row_factory = sqlite3.Row
    by_cat = {}
    for cat in ("fast_path_L0", "fast_path_L1", "fast_path_L2",
                "planner", "dialog", "undo", "admin"):
        rows = cn.execute(
            """SELECT * FROM queries WHERE category=? AND dedup_master_id IS NULL
               ORDER BY n_seen DESC LIMIT ?""",
            (cat, per_category),
        ).fetchall()
        by_cat[cat] = [dict(r) for r in rows]
    cn.close()
    out_file = samples_dir / "by_category.json"
    out_file.write_text(json.dumps(by_cat, indent=2, ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser(prog="extract.py")
    ap.add_argument("--since", type=int, default=30,
                    help="giorni da scansionare (default 30)")
    ap.add_argument("--output", default="corpus/corpus.sqlite",
                    help="path output sqlite (default corpus/corpus.sqlite)")
    ap.add_argument("--samples", default="corpus/samples",
                    help="dir output samples JSON")
    args = ap.parse_args()
    output_path = Path(args.output).resolve()
    samples_dir = Path(args.samples).resolve()
    rep = extract(args.since, output_path)
    write_samples(output_path, samples_dir)
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
