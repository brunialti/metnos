"""corpus.py — lettore corpus.sqlite per i test E2E.

Carica query classificate dal DB estratto da `e2e/corpus/extract.py` e
fornisce sampler stratificati per scenario.

API:
    corpus = Corpus(path)
    rows = corpus.sample(category="planner", success=1, limit=5)
    pos = corpus.positives(limit=10)
    neg = corpus.negatives(limit=10)
    retry = corpus.retry_candidates(limit=5)
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class QueryRow:
    id: int
    query: str
    lang: str
    category: str
    domain: str
    mutating: bool
    success: Optional[bool]
    n_seen: int

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "QueryRow":
        return cls(
            id=r["id"],
            query=r["query"],
            lang=r["lang"],
            category=r["category"],
            domain=r["domain"],
            mutating=bool(r["mutating"]),
            success=(None if r["success"] is None else bool(r["success"])),
            n_seen=int(r["n_seen"]),
        )


class Corpus:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        cn = sqlite3.connect(str(self.db_path))
        cn.row_factory = sqlite3.Row
        return cn

    def sample(self, *,
                category: Optional[str | list] = None,
                domain: Optional[str] = None,
                mutating: Optional[bool] = None,
                success: Optional[int] = None,
                min_n_seen: int = 1,
                limit: int = 10,
                ) -> list[QueryRow]:
        wh = ["dedup_master_id IS NULL", "n_seen >= ?"]
        args: list = [min_n_seen]
        if isinstance(category, str):
            wh.append("category = ?")
            args.append(category)
        elif isinstance(category, list):
            wh.append(f"category IN ({','.join('?' for _ in category)})")
            args.extend(category)
        if domain:
            wh.append("domain = ?")
            args.append(domain)
        if mutating is not None:
            wh.append("mutating = ?")
            args.append(1 if mutating else 0)
        if success is not None:
            wh.append("success = ?")
            args.append(success)
        sql = ("SELECT * FROM queries WHERE " + " AND ".join(wh)
               + " ORDER BY n_seen DESC LIMIT ?")
        args.append(limit)
        cn = self._connect()
        try:
            rows = cn.execute(sql, args).fetchall()
        finally:
            cn.close()
        return [QueryRow.from_row(r) for r in rows]

    def positives(self, *, limit: int = 10,
                   category: Optional[str | list] = None) -> list[QueryRow]:
        """Query con feedback ok o final_kind=answer (success=1)."""
        return self.sample(category=category, success=1, limit=limit)

    def negatives(self, *, limit: int = 10) -> list[QueryRow]:
        """Query con feedback ✗ o final_kind=error (success=0).
        Le risposte di Metnos NON devono essere lo stesso `tools_used`
        di queste (regression guard)."""
        return self.sample(success=0, limit=limit)

    def retry_candidates(self, *, limit: int = 5) -> list[QueryRow]:
        """Query viste >= 2 volte con feedback mixed (n_seen alto, success
        NULL o mixed). Tipico utente che ha rilanciato la stessa query
        dopo un fail."""
        return self.sample(min_n_seen=2, limit=limit)
