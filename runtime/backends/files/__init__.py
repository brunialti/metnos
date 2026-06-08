"""runtime/backends/files — builtin file/dir backends.

Architettura (decisione 13/5/2026, Q1 canonical+args, sequel di
`runtime/backends/messaging/` del 13/5/2026):
- Ogni file `<provider>.py` espone le funzioni dei verbi files/dirs
  (read/write/find/move/delete/create_dir/find_dirs) per UN provider
  builtin. Files NON ha concetto di `channel` (a differenza di messaging):
  c'e' solo `client`.
- Gli executor `read_files.py`/`write_files.py`/`find_files.py`/...
  dispatchano esplicitamente via `_HANDLERS` table cablato (chiave: solo
  `client`).
- NIENTE registry magico, NIENTE @register decorator: dispatch table
  cablato esplicitamente (the design guide §7.2 + §7.9).

Backend builtin disponibili:
- `local`: filesystem locale (riusa primitive `pathlib`+`os`+`shutil`).

Predisposizione plugin esterni (ADR pending):
- Quando si aggiungeranno plugin esterni in
  `~/.local/share/metnos/plugins/files-*/`, il dispatcher dell'executor
  arricchira' `_HANDLERS` via plugin loader (scan dei plugin manifest +
  import dinamico + trust gate). NIENTE registry magico ora; il pattern
  resta semplice e Pythonico.
- Vincoli previsti per il loader plugin:
  1. Manifest dichiara contract_version + (verb, client) tuples
  2. Trust gate: enabled=true + consent_token in cred store
  3. Precedenza: builtin > plugin (no override builtin senza force flag)

Funzioni per backend:
- `read(args: dict) -> dict`: lettura contenuto file (1 path).
- `write(args: dict) -> dict`: scrittura contenuto file (1 path).
- `find(args: dict) -> dict`: ricerca file per pattern (1 base_path).
- `move(args: dict) -> dict`: spostamento/rinomina (entries list).
- `find_dirs(args: dict) -> dict`: walk ricorsivo directory tree.
- `create_dirs(args: dict) -> dict`: creazione directory (paths list).
- `delete_dirs(args: dict) -> dict`: rimozione directory (paths list).

Contratto comune di ritorno: dict con `ok: bool` + campi specifici del
verbo (entries / results / failed / ok_count / fail_count / ...).
"""
