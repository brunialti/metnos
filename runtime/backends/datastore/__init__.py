"""backends.datastore — backend di STORAGE per `store.Store` (16/6/2026).

Famiglia di backend per lo store generico multi-backend (`runtime/store.py`),
distinta dai backend per-oggetto (`backends/messages`, `backends/files`, …) che
implementano i verbi di UN dominio. Qui i backend implementano il contratto
astratto `store.Backend` (ensure_schema/find/write/update/delete).

Tassonomia (nomi SINGOLARI: sono categorie/classi, non oggetti §2.2 plurali):
  datastore/
    memory.py              MemoryBackend       (RAM, non-SQL)
    sqldatabase/
      __init__.py          SqlDatabaseBackend  (base SQL condivisa)
      sqlite.py            SqliteBackend
      # postgres.py        PostgresBackend     (futuro, stessa base)

Regola (Roberto): stessi NOMI executor, backend diversi. Il backend si sceglie
nel registro (`store.register`), MAI da query.
"""
