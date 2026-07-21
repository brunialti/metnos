"""store.py — store generico multi-backend (gioiellino, 16/6/2026).

Astrazione RIUTILIZZABILE per accesso dati, OPTIONAL e ADDITIVA: i ~44 store
sqlite ad-hoc esistenti NON sono migrati né obbligati ad adottarla; è una
libreria per uso FUTURO (nuovi store, o adozione graduale). §7.1/§7.2/§7.9.

Tre concetti:
  - `Schema`   — descrizione DICHIARATIVA di una tabella (colonne tipizzate,
                 chiave, indici). La migrazione è DERIVATA: additiva idempotente.
  - `Backend`  — contratto ASTRATTO (ABC) di ciò che lo Store necessita. NON
                 espone SQL/placeholder/PRAGMA → non è sqlite-shaped. Possiede
                 TUTTO il dialetto. Impl: SqliteBackend (persistente),
                 MemoryBackend (effimero, prova il giunto multi-backend).
  - `Store`    — lega uno Schema a un Backend; CRUD: find/write/delete/update.

Tipi-colonna ASTRATTI (il backend li mappa al motore): TEXT/INT/REAL/BLOB/JSON.

Esempio:
    from store import Store, Schema, TEXT, INT, BLOB
    s = Store(Schema("notes", {"id": TEXT, "body": TEXT, "n": INT},
                     primary_key=("id",)))
    s.write({"id": "a", "body": "ciao", "n": 1})            # upsert (crea-se-manca)
    s.find(where={"id": "a"})                               # → [{"id":"a",...}]
    s.delete(where={"id": "a"})

Test seam (NIENTE reload di modulo): passa un backend con path/tmp al costrutt.:
    Store(schema, backend=SqliteBackend(path=tmp/"x.sqlite"))
    Store(schema, backend=MemoryBackend())
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

# ── Tipi-colonna astratti (il backend mappa al motore concreto) ────────────
TEXT = "text"
INT = "int"
REAL = "real"
BLOB = "blob"
JSON = "json"   # serializzato a stringa dal backend; dict/list in lettura

_ABSTRACT_TYPES = frozenset({TEXT, INT, REAL, BLOB, JSON})


@dataclass(frozen=True)
class Schema:
    """Descrizione dichiarativa di una tabella. La migrazione (additiva) è
    derivata da `columns`: il backend aggiunge le colonne mancanti."""
    table: str
    columns: dict[str, str]                       # nome → tipo astratto
    primary_key: tuple[str, ...] = ()
    indexes: tuple[tuple[str, ...], ...] = ()     # ogni tupla = colonne d'indice

    def __post_init__(self):
        if not self.table or not self.table.isidentifier():
            raise ValueError(f"nome tabella non valido: {self.table!r}")
        for name, typ in self.columns.items():
            if not name.isidentifier():
                raise ValueError(f"nome colonna non valido: {name!r}")
            if typ not in _ABSTRACT_TYPES:
                raise ValueError(
                    f"tipo astratto non valido per {name!r}: {typ!r} "
                    f"(usa uno di {sorted(_ABSTRACT_TYPES)})")
        for k in self.primary_key:
            if k not in self.columns:
                raise ValueError(f"primary_key {k!r} non è fra le colonne")

    def json_columns(self) -> frozenset[str]:
        return frozenset(n for n, t in self.columns.items() if t == JSON)


# ── Contratto backend (astratto: nessun SQL/placeholder/PRAGMA trapela) ─────
class Backend(ABC):
    """Ciò che lo Store NECESSITA da un motore di storage. Astratto per
    costruzione: il chiamante non vede mai SQL. Aggiungere un motore = una
    sottoclasse, non un refactor dello Store."""

    #: feature dichiarate (es. {"vector","fts"}); find_entries semantico le legge
    capabilities: frozenset[str] = frozenset()

    @abstractmethod
    def ensure_schema(self, schema: Schema) -> None:
        """Crea tabella+indici se assenti; migra ADDITIVO (colonne mancanti).
        Idempotente."""

    @abstractmethod
    def find(self, schema: Schema, where: Optional[dict] = None,
             order: Optional[Sequence] = None,
             limit: Optional[int] = None) -> list[dict]:
        """SELECT: restrizione/ricerca. `where` = {col: val} (uguaglianza) o
        {col: [v1, v2]} (IN). `order` = [(col, 'asc'|'desc'), ...] o ['col'].
        Ritorna list[dict] (colonne JSON già deserializzate)."""

    @abstractmethod
    def write(self, schema: Schema, rows: list[dict],
              key: tuple[str, ...]) -> int:
        """UPSERT per `key`: record assente → INSERT (creato), presente →
        UPDATE. `key` vuota → INSERT puro. Ritorna n. righe scritte."""

    @abstractmethod
    def update(self, schema: Schema, values: dict, where: dict) -> int:
        """UPDATE values WHERE where. Ritorna n. righe toccate."""

    @abstractmethod
    def delete(self, schema: Schema, where: Optional[dict] = None) -> int:
        """DELETE WHERE where (where None/{} = svuota). Ritorna n. righe."""

    def raw(self, query: str, params: Sequence = ()) -> list[dict]:
        """Escape-hatch backend-SPECIFICO (sconsigliato: rompe la portabilità)."""
        raise NotImplementedError(
            f"{type(self).__name__} non supporta raw()")

    def close(self) -> None:
        """Rilascia risorse (connessioni/pool). Idempotente."""


def _default_backend(table: str, path=None) -> Backend:
    """SqliteBackend di default, path risolto a CALL-TIME (env → arg → config).
    Import lazy per evitare cicli e tenere `store` come core puro."""
    from backends.datastore.sqldatabase.sqlite import SqliteBackend
    return SqliteBackend(path=path, default_name=table)


class Store:
    """Uno store logico = uno Schema su un Backend. Thread-safe (lock per
    istanza). Schema garantito (ensure) pigramente alla prima operazione."""

    def __init__(self, schema: Schema, *, backend: Optional[Backend] = None,
                 path=None, insert_defaults: Optional[dict] = None):
        self.schema = schema
        self._backend = backend
        self._path = path
        self._lock = threading.RLock()
        self._ready = False
        # Valore INIZIALE deterministico dei campi assenti, dichiarato alla
        # registrazione (es. github_issue_qa: status='new'). Sorgente di
        # config (NON dalla query/LLM) → FASE 1 detect persiste sempre in
        # PENDING senza dipendere dall'arg-filling del proposer. Applicato in
        # write() solo dove il campo manca/None (non sovrascrive i presenti:
        # FASE 2/3 che impostano status esplicito non sono toccate).
        self.insert_defaults = dict(insert_defaults or {})

    @property
    def backend(self) -> Backend:
        # Creazione pigra del backend di default → path risolto a call-time
        # (seam di test: passa backend/path; niente reload di modulo).
        if self._backend is None:
            self._backend = _default_backend(self.schema.table, self._path)
        return self._backend

    def _ensure(self) -> None:
        if self._ready:
            return
        with self._lock:
            if not self._ready:
                self.backend.ensure_schema(self.schema)
                self._ready = True

    # ── CRUD ────────────────────────────────────────────────────────────
    def find(self, where: Optional[dict] = None,
             order: Optional[Sequence] = None,
             limit: Optional[int] = None) -> list[dict]:
        self._ensure()
        with self._lock:
            return self.backend.find(self.schema, where, order, limit)

    def get(self, where: Optional[dict] = None) -> Optional[dict]:
        """Comodità: primo record che matcha, o None."""
        rows = self.find(where=where, limit=1)
        return rows[0] if rows else None

    def write(self, rows, key: Optional[Iterable[str]] = None) -> int:
        """Upsert (crea-se-manca). `rows` = dict o list[dict]. `key` default =
        primary_key dello schema."""
        self._ensure()
        if isinstance(rows, dict):
            rows = [rows]
        rows = [dict(r) for r in rows]
        if not rows:
            return 0
        k = tuple(key) if key is not None else tuple(self.schema.primary_key)
        if self.insert_defaults:
            # `insert_defaults` = valore INIZIALE dei campi assenti → si applica
            # SOLO ai record NUOVI (clobber-preserve 20/6): un record già
            # presente conserva il suo stato (es. il detect github che re-ingesta
            # un'issue già 'answered' NON la riporta a 'new'). Senza chiave ogni
            # write è un INSERT → default sempre applicati.
            for r in rows:
                if k and self._exists(r, k):
                    continue
                for dk, dv in self.insert_defaults.items():
                    if r.get(dk) is None:
                        r[dk] = dv
        with self._lock:
            return self.backend.write(self.schema, rows, k)

    def _exists(self, row: dict, key: tuple) -> bool:
        """True se un record con la stessa chiave è già nello store. Best-effort:
        chiave non interamente valorizzata → trattato come NUOVO (default
        applicati). Usato per gli insert_defaults E per `check_new` (stessa
        query, un find per riga, chiave indicizzata)."""
        try:
            where = {kc: row.get(kc) for kc in key}
            if any(v is None for v in where.values()):
                return False
            return bool(self.find(where=where, limit=1))
        except Exception:
            return False

    def check_new(self, rows, key: Optional[Iterable[str]] = None) -> list[bool]:
        """True per ogni riga ASSENTE (diventerebbe un INSERT se scritta
        ora), False se gia' presente (diventerebbe un UPDATE/upsert-noop).
        Riusa `_exists()` — nessuna query in piu' rispetto a quella che
        `write()` fa comunque per gli insert_defaults (§7.2).

        Fix bug live 3/7 (§2.8): `write()` upserta e ritorna solo un conteggio
        di RIGHE SCRITTE, indistinguibile fra "creata ora" e "gia' presente,
        ri-scritta identica" — una pipeline che conta i risultati di write()
        come "elementi nuovi" mente (upsert su un record esistente conta
        comunque). Va chiamato PRIMA di `write()` sulle stesse righe/key: il
        "prima" e' genuino solo finche' il write non e' ancora avvenuto."""
        self._ensure()
        if isinstance(rows, dict):
            rows = [rows]
        k = tuple(key) if key is not None else tuple(self.schema.primary_key)
        if not k:
            return [True] * len(rows)  # niente chiave -> ogni write e' insert puro
        return [not self._exists(r, k) for r in rows]

    def update(self, values: dict, where: dict) -> int:
        self._ensure()
        with self._lock:
            return self.backend.update(self.schema, dict(values), dict(where))

    def delete(self, where: Optional[dict] = None) -> int:
        self._ensure()
        with self._lock:
            return self.backend.delete(self.schema, where)

    def count(self, where: Optional[dict] = None) -> int:
        return len(self.find(where=where))

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
            self._ready = False


# ── Registro store (disaccoppia query↔backend) ─────────────────────────────
# Il backend NON si sceglie mai da query: si dichiara QUI alla registrazione
# dello store. La query nomina solo lo store (es. find_entries(store="X")); il
# registro risolve nome → Store(schema+backend). Inizialmente VUOTO: niente lo
# adotta ancora (regola Roberto «non obbligatorio subito»). Lookup su nome
# sconosciuto → KeyError (errore onesto §2.8, l'executor lo traduce).
_registry: dict[str, Store] = {}
_registry_lock = threading.Lock()


def register(schema: Schema, *, name: Optional[str] = None,
             backend: Optional[Backend] = None, path=None,
             insert_defaults: Optional[dict] = None) -> Store:
    """Dichiara uno store: nome (default schema.table) → Store(schema, backend).
    Idempotente sul nome (ri-registrare sostituisce). Ritorna lo Store.
    `insert_defaults`: valore iniziale dei campi assenti (vedi Store)."""
    key = name or schema.table
    st = Store(schema, backend=backend, path=path,
               insert_defaults=insert_defaults)
    with _registry_lock:
        _registry[key] = st
    return st


def get_store(name: str) -> Store:
    """Store registrato per nome, o KeyError (onesto §2.8)."""
    with _registry_lock:
        st = _registry.get(name)
    if st is None:
        raise KeyError(
            f"store non registrato: {name!r} (registrati: {sorted(_registry)})")
    return st


def is_registered(name: str) -> bool:
    with _registry_lock:
        return name in _registry


def registered() -> list[str]:
    with _registry_lock:
        return sorted(_registry)


def unregister(name: str) -> None:
    """Rimuove uno store dal registro (test/teardown). Idempotente."""
    with _registry_lock:
        st = _registry.pop(name, None)
    if st is not None:
        st.close()
