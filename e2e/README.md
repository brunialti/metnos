# Metnos E2E Simulator

Ambiente di test E2E **separato** dal codice Metnos (`runtime/`, `executors/`). Simula un utente che usa la UI HTTP per validare end-to-end:

1. **ADR 0158 change_intent lifecycle**: `proposed → accepted → applied → observed → finalized` per i 6 kind, rollback fisico, convergence cross-source.
2. **Skill importer**: mock skill (controllo) e google-workspace reale (con credenziali simulate, copiate dal sistema in dir isolata).
3. **Qualità chat**: query reali estratte dai turn log, classificate per categoria/dominio, asserzione lint + LLM-as-judge.

## Garanzie di separazione

- **Zero import** da `e2e/` verso `runtime/` (o viceversa). Il simulatore parla SOLO via HTTP/CLI.
- **Server in subprocess**: `python3 -m runtime.metnos_http_server` su porta random.
- **Storage isolato**: `e2e/tmp/<run_id>/` (mai tocca `~/.local/share/metnos/` reale).
- **Credenziali**: copiate runtime da sistema in dir isolata, mai committed in repo.

## Quick start

```bash
cd /opt/metnos/e2e

# Setup primo run (estrai corpus da log reali)
python3 corpus/extract.py

# Lancia tutto (judge LLM ON default)
./run.sh

# Test veloci senza judge (lint only)
METNOS_E2E_LLM_JUDGE=0 ./run.sh

# Solo un cluster
pytest scenarios/test_lifecycle_change_intent.py -v
```

## Struttura

```
e2e/
├── README.md
├── pyproject.toml       # deps: aiohttp, pytest-asyncio
├── conftest.py          # fixture server + driver
├── run.sh               # entry point
├── driver/
│   ├── http_client.py   # admin auth + htmx + chat
│   ├── server.py        # subprocess server lifecycle
│   ├── lint.py          # assertion deterministica
│   ├── judge.py         # LLM-as-judge cached (Gemma 26B locale)
│   └── snapshot.py      # regression baseline
├── corpus/
│   ├── extract.py       # estrazione da turns/canonical/multi_tool/feedback
│   ├── corpus.sqlite    # DB classificato (anonimizzato, dedup)
│   └── samples/         # JSON export per ispezione
├── fixtures/
│   ├── skill_mock/      # SKILL.md sintetico (3 executor)
│   └── google_creds/    # placeholder (credenziali copiate runtime)
├── scenarios/
│   ├── test_lifecycle_change_intent.py
│   ├── test_skill_import.py
│   ├── test_skill_import_google.py
│   └── test_chat_quality.py
├── snapshots/           # baseline canonical
├── tmp/                 # storage runtime isolato (gitignored)
├── reports/             # output (gitignored)
└── .cache/              # judge cache (gitignored)
```

## Convenzioni

- Tutti i test sono `async def` (pytest-asyncio).
- Fixture `driver` injetta client autenticato admin.
- Fixture `corpus` carica `corpus.sqlite` (estratta una volta, riusata).
- Judge: default ON. Disable con `METNOS_E2E_LLM_JUDGE=0` per test rapidi.

## Constraint Metnos applicati

- §7.3 soluzioni generali, no hardcoding
- §7.9 codice deterministico, LLM solo se equipotente troppo complesso
- §8.2 fix codice, non test
- §8.5 convergence loop fino errore=0

Vedi anche [ADR 0158](../decisions/0158-unified-change-intent-lifecycle.md).
