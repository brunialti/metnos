"""registry.py — ExecutorRegistry: core registry tipi semantici.

Storage: sqlite per produzione (atomico, indici), json per simulator (debug).

Boot:
  1. Carica typing da sqlite (o JSON files)
  2. Verifica mtime vs manifest
  3. Refresh stale entries (LLM Stage 2 se necessario)
  4. Index in memory: by_output, by_consumes, by_intent

API:
  - find_by_output(semantic_type) → list[name]
  - find_by_input_accepting(semantic_type) → list[name]
  - find_consumes(constraint_kind) → list[name]
  - typing(name) → ExecutorTyping
  - refresh(name) → re-extract typing per name
  - persist() → save to sqlite

Stesso codice usato in simulator standalone E produzione runtime/engine.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))
from types_semantic import is_compatible


@dataclass
class InputTyping:
    name: str
    structural_type: str       # array | object | string | integer | boolean
    semantic_type: str         # vocab semantic
    required: bool = False
    default: object = None
    role: str = "meta"         # filter | source | meta | piping
    format_hint: str = ""


@dataclass
class OutputTyping:
    type: str                  # semantic type, e.g. "file_entry[]"
    schema: dict = field(default_factory=dict)
    may_be_truncated: bool = False
    may_be_empty: bool = True


@dataclass
class Constraint:
    """Vincolo che un executor consuma. Per constraint propagation."""
    kind: str                  # filter | sort | aggregate | transform
    key: str = ""              # es. "person_name" | "time_window"
    value: object = None


@dataclass
class ExecutorTyping:
    """Singolo executor con I/O tipizzato + constraint behavior."""
    name: str
    manifest_mtime: int = 0
    inputs: dict[str, InputTyping] = field(default_factory=dict)
    requires_one_of: list = field(default_factory=list)
    output: OutputTyping = field(default_factory=OutputTyping)
    consumes: list[str] = field(default_factory=list)   # constraint kinds consumed
    propagates_constraints: bool = False                # passa-through upstream filters
    preconditions: list = field(default_factory=list)
    is_builtin: bool = False
    is_terminator: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "ExecutorTyping":
        inp_raw = d.get("inputs", {}) or {}
        inputs = {}
        for n, meta in inp_raw.items():
            if not isinstance(meta, dict):
                continue
            inputs[n] = InputTyping(
                name=n,
                structural_type=meta.get("structural_type", "string"),
                semantic_type=meta.get("semantic_type", "free_text"),
                required=meta.get("required", False),
                default=meta.get("default"),
                role=meta.get("role", "meta"),
                format_hint=meta.get("format_hint", ""),
            )
        out_raw = d.get("output", {}) or {}
        output = OutputTyping(
            type=out_raw.get("type", "json_object"),
            schema=out_raw.get("schema", {}) or {},
            may_be_truncated=out_raw.get("may_be_truncated", False),
            may_be_empty=out_raw.get("may_be_empty", True),
        )
        return cls(
            name=d.get("name", ""),
            manifest_mtime=int(d.get("manifest_mtime", 0)),
            inputs=inputs,
            requires_one_of=d.get("requires_one_of") or [],
            output=output,
            consumes=d.get("consumes") or [],
            propagates_constraints=d.get("propagates_constraints", False),
            preconditions=d.get("preconditions") or [],
            is_builtin=d.get("_builtin", False),
            is_terminator=d.get("_terminator", False),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "manifest_mtime": self.manifest_mtime,
            "inputs": {n: {
                "structural_type": i.structural_type,
                "semantic_type": i.semantic_type,
                "required": i.required,
                "default": i.default,
                "role": i.role,
                "format_hint": i.format_hint,
            } for n, i in self.inputs.items()},
            "requires_one_of": self.requires_one_of,
            "output": {
                "type": self.output.type,
                "schema": self.output.schema,
                "may_be_truncated": self.output.may_be_truncated,
                "may_be_empty": self.output.may_be_empty,
            },
            "consumes": self.consumes,
            "propagates_constraints": self.propagates_constraints,
            "preconditions": self.preconditions,
            "_builtin": self.is_builtin,
            "_terminator": self.is_terminator,
        }


class ExecutorRegistry:
    """Registry typed di tutti gli executor. In-memory + sqlite persistito."""

    def __init__(self, sqlite_path: Optional[Path] = None,
                  json_dir: Optional[Path] = None):
        """Backend: sqlite (produzione) o json (simulator).

        Se entrambi forniti, sqlite vince. Se nessuno → tmp default.
        """
        self.sqlite_path = sqlite_path
        self.json_dir = json_dir
        self._by_name: dict[str, ExecutorTyping] = {}
        self._by_output: dict[str, list[str]] = defaultdict(list)
        self._by_input_type: dict[str, list[str]] = defaultdict(list)
        self._by_consumes: dict[str, list[str]] = defaultdict(list)
        if self.sqlite_path:
            self._ensure_sqlite()
        self.load()

    def _ensure_sqlite(self) -> None:
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(self.sqlite_path))
        c.executescript("""
        CREATE TABLE IF NOT EXISTS executor_typing (
            name TEXT PRIMARY KEY,
            manifest_mtime INTEGER NOT NULL DEFAULT 0,
            output_type TEXT,
            data_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS et_output ON executor_typing(output_type);
        """)
        c.commit()
        c.close()

    def load(self) -> None:
        self._by_name.clear()
        self._by_output.clear()
        self._by_input_type.clear()
        self._by_consumes.clear()
        if self.sqlite_path and self.sqlite_path.exists():
            self._load_sqlite()
        elif self.json_dir and self.json_dir.exists():
            self._load_json()

    def _load_sqlite(self) -> None:
        c = sqlite3.connect(str(self.sqlite_path))
        for row in c.execute("SELECT data_json FROM executor_typing"):
            try:
                typing = ExecutorTyping.from_dict(json.loads(row[0]))
                self._index(typing)
            except Exception:
                continue
        c.close()

    def _load_json(self) -> None:
        for f in sorted(self.json_dir.glob("*.json")):
            try:
                typing = ExecutorTyping.from_dict(json.loads(f.read_text()))
                self._index(typing)
            except Exception:
                continue

    def _index(self, typing: ExecutorTyping) -> None:
        self._by_name[typing.name] = typing
        self._by_output[typing.output.type].append(typing.name)
        for arg, i in typing.inputs.items():
            self._by_input_type[i.semantic_type].append(typing.name)
        for c in typing.consumes:
            self._by_consumes[c].append(typing.name)

    def add(self, typing: ExecutorTyping, *, persist: bool = True) -> None:
        self._by_name[typing.name] = typing
        self._index(typing)
        if persist and self.sqlite_path:
            self._persist_one(typing)

    def _persist_one(self, typing: ExecutorTyping) -> None:
        c = sqlite3.connect(str(self.sqlite_path))
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        c.execute("""
            INSERT INTO executor_typing(name, manifest_mtime, output_type,
                                        data_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                manifest_mtime = excluded.manifest_mtime,
                output_type = excluded.output_type,
                data_json = excluded.data_json,
                updated_at = excluded.updated_at
        """, (typing.name, typing.manifest_mtime, typing.output.type,
              json.dumps(typing.to_dict(), ensure_ascii=False), ts))
        c.commit()
        c.close()

    # ── Query API ─────────────────────────────────────────────────────────

    def typing(self, name: str) -> Optional[ExecutorTyping]:
        return self._by_name.get(name)

    def all_names(self) -> list[str]:
        return list(self._by_name.keys())

    def __len__(self) -> int:
        return len(self._by_name)

    def find_by_output(self, semantic_type: str) -> list[ExecutorTyping]:
        """Executor che producono `semantic_type` o suo subtype."""
        out = []
        for name in self._by_output.get(semantic_type, []):
            t = self._by_name.get(name)
            if t: out.append(t)
        # is_a downcast: chi produce image_entry[] è anche compatibile file_entry[]
        for other_type, names in self._by_output.items():
            if other_type == semantic_type:
                continue
            if is_compatible(other_type, semantic_type):
                for n in names:
                    t = self._by_name.get(n)
                    if t and t not in out:
                        out.append(t)
        return out

    def find_accepting_input(self, semantic_type: str) -> list[ExecutorTyping]:
        """Executor che accettano `semantic_type` come input."""
        out = []
        seen = set()
        for in_type, names in self._by_input_type.items():
            if is_compatible(semantic_type, in_type):
                for n in names:
                    if n in seen: continue
                    seen.add(n)
                    t = self._by_name.get(n)
                    if t: out.append(t)
        return out

    def find_consumes(self, constraint_kind: str) -> list[ExecutorTyping]:
        """Executor che consumano constraint_kind."""
        return [self._by_name[n] for n in self._by_consumes.get(constraint_kind, [])
                if n in self._by_name]

    def stats(self) -> dict:
        return {
            "n_executors": len(self._by_name),
            "n_output_types": len(self._by_output),
            "n_input_types": len(self._by_input_type),
            "n_constraint_kinds": len(self._by_consumes),
            "builtins": sum(1 for t in self._by_name.values() if t.is_builtin),
        }


# Singleton convenience
_default: Optional[ExecutorRegistry] = None


def get_registry(*, sqlite_path: Optional[Path] = None,
                  json_dir: Optional[Path] = None,
                  reload: bool = False) -> ExecutorRegistry:
    global _default
    if _default is None or reload:
        _default = ExecutorRegistry(sqlite_path=sqlite_path, json_dir=json_dir)
    return _default
