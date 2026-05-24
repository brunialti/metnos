---
id: 0132
title: Plugin esterni per backend `(object, provider)` — manifest + trust gate
date: 2026-05-14
status: deprecated
deprecated_date: 2026-05-24
superseded_by:
  - 0123  # skill bundle import (extension canale unico)
  - 0136  # provider qualifier (suffisso `_<provider>` distingue dal canonical)
  - 0160  # locale-aware + enable/disable skill (governance estensione)
area: runtime | backends | plugins
related:
  - 0078  # HTTP API + dispatcher canonical
  - 0089  # credentials store
  - 0123  # skill importer agentskills.io
  - 0128  # importer verb boundary
  - 0130  # backends/<object>/<provider> tree
complements:
  - 0130
---

> **DEPRECATED 2026-05-24.** Lo stesso effetto (estendere il catalog
> con backend custom per `(object, provider)`) si ottiene importando
> una **skill** che dichiara il provider qualifier (ADR 0136). La skill
> registra direttamente executor `<verb>_<object>_<provider>` firmati
> Ed25519 + admission 7-layer (ADR 0159), senza un canale parallelo di
> discovery. Per sostituire il builtin canonical e' disponibile l'env
> `METNOS_HIDE_EXECUTORS=<canonical_name>` (ADR 0123 ext, 24/5/2026).
> Il modulo `runtime/plugin_loader.py` (scaffolding mai wired) e'
> rimosso. La directory `~/.local/share/metnos/plugins/` resta
> inutilizzata: nessuno scan attivo.


## Context

ADR 0130 ha fissato il pattern `runtime/backends/<object>/<provider>.py`
con provider builtin import statici. Predisposizione per plugin esterni
era citata in tutti i `backends/<object>/__init__.py` ma rimasta non
implementata.

Per estendere senza ricompilare il runtime (es. utente vuole
`notion/calendar.py` o `outlook/calendar.py`), serve un meccanismo di
discovery + trust gate.

## Decision

I plugin esterni risiedono in:

```
~/.local/share/metnos/plugins/<plugin_name>/
  ├── plugin.toml        # manifest dichiarativo
  ├── <object>.py        # implementazione per UN object §2.2
  └── ...
```

Un plugin puo' implementare uno o piu' OBJECTS §2.2 (es. un plugin
`outlook` puo' avere sia `events.py` che `messages.py`).

### Manifest schema (`plugin.toml`)

```toml
manifest_format = "1.0"

name        = "outlook"          # nome plugin (== nome cartella)
provider    = "outlook"          # nome provider esposto al dispatch
version     = "0.1.0"
author      = "..."
enabled     = true               # trust gate (false → skip)
requires    = ["python>=3.12"]   # opzionale

# Tuple (object, function_set) esposte dal plugin
[[backends]]
object   = "events"
file     = "events.py"
provides = ["read", "create", "delete", "find_events_empty"]

[[backends]]
object   = "messages"
file     = "messages.py"
provides = ["send", "read", "find", "delete"]
```

### Trust gate

Layer 1 — `enabled = true` nel manifest. Plugin con `enabled=false`
sono scartati silenziosamente.

Layer 2 — `consent_token` (opzionale): fingerprint sha256 di una
entry nel credentials store con domain `plugin_<plugin_name>_consent`.
Permette all'utente di approvare esplicitamente un plugin via
`metnos-cli plugins approve <plugin_name>` (ADR pending CLI).

Layer 3 — Precedenza: builtin > plugin. Il plugin loader NON puo'
sovrascrivere `("events", "google_workspace")` o altri builtin gia'
registrati nella dispatch table.

### Discovery

`runtime/plugin_loader.py::load_plugins(object_canonical)` scansiona
`~/.local/share/metnos/plugins/*/plugin.toml`, filtra per `enabled +
backends[object==<x>]`, importa il file Python dinamicamente, ritorna
`{provider_name: module}`.

Lazy + cached. Invalidazione esplicita via
`plugin_loader.invalidate_cache()`.

### Wiring nel dispatcher canonical

```python
# Generale, applicato a tutti i dispatcher canonical
from backends.events import local_ics, google_workspace
from plugin_loader import load_plugins

_HANDLERS = {
    "local":            local_ics,
    "google_workspace": google_workspace,
}
_HANDLERS.update({
    name: mod for name, mod in load_plugins("events").items()
    if name not in _HANDLERS  # builtin > plugin (Layer 3)
})
```

## Implementation (scaffolding 14/5/2026)

- `runtime/plugin_loader.py`: scaffolding con `load_plugins(object)`,
  `invalidate_cache()`, `list_installed_plugins()`. NON ancora wired
  nei dispatcher (attivazione condizionata al primo plugin reale).
- Manifest schema documentato qui.
- Skel directory `~/.local/share/metnos/plugins/` (esiste).

## Open

- **CLI tool** `metnos-cli plugins {install,list,approve,disable,uninstall}`:
  ADR pending. Per ora install manuale (drop folder).
- **Sandboxing**: i plugin sono codice Python eseguito nel processo
  metnos-http. Considerare wrap via `runtime/sandbox.py` per
  isolamento (analogo a executor synth). Per ora trust full.
- **Auto-discovery vs explicit list**: il loader scansiona tutta la
  dir `plugins/`. Alternativa: solo plugin in whitelist
  `~/.config/metnos/plugins_enabled.toml`. Per ora auto-discovery +
  manifest `enabled` flag.
- **Plugin verification**: come gli executor (ADR 0114 admission
  layers) i plugin dovrebbero passare un L1 vocab check + L2 affinity
  + L4/L5 smoke. Pending design.

## Why not pattern B (provider-first)?

Vedi ADR 0130 §«Pattern A vs B». Il pattern attuale
`backends/<object>/<provider>` resta canonical anche per i plugin.
Plugin con multi-object esponono N file separati ma condividono
manifest + helper interni (es. OAuth).

## References

- `runtime/plugin_loader.py` (scaffolding)
- `runtime/backends/<object>/__init__.py` (doc references aggiornata)
- `~/.local/share/metnos/plugins/` (root plugin tree)
