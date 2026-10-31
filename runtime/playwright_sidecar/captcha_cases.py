# SPDX-License-Identifier: MIT
"""Grids the public classifier could not answer, kept for later tests.

Roberto, 30/10/2026: «quando si trova un captcha che non si riconosce sarebbe
interessante salvarlo [...] creando un db di casi difficili». Only the provider's
pictures, the instruction word, the scores and the reason: no address, session
or account. Saving never touches the login: any failure just keeps nothing.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time

import config as C

MAX_CASES = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    digest TEXT PRIMARY KEY, saved_at REAL NOT NULL, reason TEXT NOT NULL,
    keyword TEXT NOT NULL, scores TEXT NOT NULL, rows_per_side INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS tiles (
    digest TEXT NOT NULL REFERENCES cases(digest) ON DELETE CASCADE,
    position INTEGER NOT NULL, png BLOB NOT NULL, PRIMARY KEY (digest, position));
"""


def store_path():
    return C.PATH_USER_DATA / "captcha_hard_cases.sqlite"


def save(*, tiles: list[bytes], keyword: str, scores: list[float], reason: str) -> None:
    """Keep one grid once; the oldest cases leave beyond MAX_CASES."""
    digest = hashlib.sha256(b"".join(tiles)).hexdigest()
    with sqlite3.connect(store_path(), timeout=5) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        added = conn.execute(
            "INSERT OR IGNORE INTO cases VALUES (?,?,?,?,?,?)",
            (digest, time.time(), reason, keyword,
             json.dumps([round(score, 4) for score in scores]), round(len(tiles) ** 0.5))).rowcount
        if added:
            conn.executemany("INSERT INTO tiles VALUES (?,?,?)",
                             [(digest, i, png) for i, png in enumerate(tiles)])
        conn.execute("DELETE FROM cases WHERE digest NOT IN "
                     "(SELECT digest FROM cases ORDER BY saved_at DESC LIMIT ?)", (MAX_CASES,))
