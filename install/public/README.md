# Metnos public install — overview

Pipeline di installazione per utenti pubblici (GitHub clone).

## Architettura a 3 layer

```
┌──────────────────────────────────────┐
│  metnos (questo repo)                │  ← assistente AI personale
│   - runtime, executors, chat HTTP    │
│   - suprashim.py (adapter)           │
└────────────────┬─────────────────────┘
                 │ chiama
                 ▼
┌──────────────────────────────────────┐
│  suprastructure (dipendenza)         │  ← hub servizi AI
│   - registry: routing logico tier    │
│   - clients: llamacpp/ollama/anthropic│
└────────────────┬─────────────────────┘
                 │ chiama
                 ▼
┌──────────────────────────────────────┐
│  backend LLM (scelta utente)         │  ← motori inference
│   - llama-server (Gemma, Qwen, ...)  │
│   - ollama, vllm, Anthropic API, ... │
└──────────────────────────────────────┘
```

Metnos NON sa quale modello concreto risponde alle sue richieste. Vede solo
**tier logici** (`tiny`/`fast`/`middle`/`wise`/`frontier`). La mappatura
tier→backend vive in suprastructure.

## Script di installazione

| Script | Cosa fa | Quando |
|---|---|---|
| `00_prepare_env.sh` | Verifica OS, GPU, RAM; scrive `.env` con paths | Una volta, primo install |
| `01_install_supra.sh` | Clona+installa suprastructure venv | Una volta |
| `02_install_llm_<model>.sh` | Scarica modello + lancia llama-server systemd unit | Per ogni modello scelto |
| `03_install_metnos.sh` | Clone+venv+systemd+config Metnos | Una volta |
| `04_wire_supra.sh` | Registra backend installati nei tier supra | Dopo ogni cambio modello |

**Script intelligente `metnos-installer`**: wrapper interattivo che chiede
all'utente cosa ha (RAM, GPU, OS, internet) e cosa vuole (uso quotidiano,
sviluppo, sola lettura...), poi orchestra `00`-`04` con le scelte migliori
per il profilo. **Setup standard sempre caldamente raccomandato** (Gemma 4
26B + Qwen 9B come tier `wise`+`fast`).

## Suprashim integration

`runtime/suprashim.py` espone:
- `get_llm(tier="fast")` → client da supra registry
- `chat(system, user, tier=..., ...)` → wrapper sync legacy
- `is_available()` → True se supra installato + wired
- `get_tier_info()` → introspect per admin dashboard
- `SupraNotConfigured` exception

Tutto `llm_router` / `llm_helpers` chiama `suprashim.chat(...)` invece di
URL diretti. Fallback: se `is_available() == False`, usa
`llm_tiers.toml::provider=llamacpp` storico (dev/test mode).

## Manuali utente

- `tier_swap.md` — come cambiare il modello dietro un tier (es. upgrade Gemma 4 26B → Gemma 5 33B)
- `model_tuning.md` — parametri llama-server consigliati per OS/GPU comuni (Strix Halo, RTX 30/40/50, Apple M-series, CPU-only)
- `troubleshooting.md` — errori comuni: tier non disponibile, supra disconnesso, OOM, ecc.

## Per chi deve **modificare il backend dopo install**

1. Stop systemd unit del modello vecchio: `systemctl --user stop llamacpp-<old>.service`
2. Install nuovo modello: `./02_install_llm_<new>.sh`
3. Re-wiring: `./04_wire_supra.sh --tier=fast --replace`
4. Metnos prosegue zero downtime (supra reroute live)

Vedi `tier_swap.md` per dettagli.
