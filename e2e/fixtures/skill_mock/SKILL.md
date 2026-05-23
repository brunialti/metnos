---
name: skill-mock-e2e
description: "Skill sintetica per test E2E del simulatore Metnos. Tre executor mock che ritornano dati deterministici, zero chiamate esterne."
version: 0.1.0
author: e2e-simulator
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [test, mock, e2e]
---

# Skill Mock — E2E test fixture

Skill sintetica usata SOLO dal simulatore E2E per verificare la pipeline di
import skill (parser -> translator -> codegen -> admission -> sign). Niente
credenziali, niente rete, output deterministico.

## Scripts

- `scripts/mock_cli.py` — CLI che ritorna JSON deterministico per ogni
  sub-command, simulando una vera skill esterna.

## Usage

Define a shorthand:

```bash
MOCK="python ${HOME}/.local/share/metnos/skills/skill-mock-e2e/scripts/mock_cli.py"
```

### drive

Operazioni mock su `drive` (domain registrato in skill_vocab_map.json
con object=files). Tre executor verranno generati con suffix provider
`_skill_mock_e2e` (ADR 0136).

#### drive search

```bash
$MOCK drive search --query="hello" --limit=5
$MOCK drive search --query="test"
```

Returns JSON `{"ok": true, "entries": [{"id": ..., "name": ...}]}`.

#### drive read

```bash
$MOCK drive read --id=42
$MOCK drive read --id=7
```

Returns JSON `{"ok": true, "entries": [{"id": ..., "name": ..., "kind": ...}]}`.

#### drive create

```bash
$MOCK drive create --name="nuovo file" --kind=B
```

Returns JSON `{"ok": true, "results": [{"id": <new_id>, "name": ..., "_undo": {...}}]}`.
