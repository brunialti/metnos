# myclaw

**Assistente AI personale per casa** — gateway + agente + sandbox + workspace. Local-first, sicuro per default, costruito sopra [`suprastructure`](/opt/suprastructure/).

- **Versione:** 0.0.1 (design phase)
- **Python:** ≥ 3.11
- **Location:** auto-resolved via `runtime.config.PATH_ROOT` (default da `__file__`, override env `METNOS_INSTALL_ROOT`). Su `.33` oggi `/opt/metnos/`; rinomina pianificata a `/opt/metnos/` zero-config (ADR 0148).
- **Status:** solo documentazione di architettura. Nessun codice ancora scritto.

> 📖 **Prima volta qui?** Apri [`docs/index.html`](docs/index.html) nel browser. È l'indice navigabile. Il punto di partenza è **Myclaw — Architettura: Introduzione v1** (20 minuti, didattico, con diagrammi).
>
> 🎒 **Vuoi solo sapere cosa potrai farci?** Vai al [Survival Kit](docs/Myclaw_Survival_Kit_v1.html).

## Cos'è in tre righe

1. Studia l'architettura di [openclaw](https://github.com/openclaw/openclaw) e [zeroclaw](https://github.com/zeroclaw-labs/zeroclaw), ne prende i pattern migliori.
2. Li rifonde in Python per un uso domestico, con sicurezza di default alta (sandbox a strati, approvazione esplicita, default `127.0.0.1`).
3. Consuma `suprastructure` per tutto ciò che è LLM / STT / TTS / embedding / speaker-ID — non riscrive quel layer.

## Rapporto con gli altri progetti in `/opt/`

| Progetto | Cosa fa | Rapporto con myclaw |
|---|---|---|
| [`suprastructure`](/opt/suprastructure/) | Hub di servizi AI (LLM, STT, TTS, ...) | **Dipendenza**: myclaw lo consuma via `registry.get(...)` |
| [`giorgio2`](/opt/giorgio2/) | Assistente vocale smart home | **Sibling**: altro consumer di suprastructure |
| [`shibot`](/opt/shibot/) | Bot | **Sibling** |

## Documentazione

La documentazione vive in [`docs/`](docs/) e viene affinata progressivamente.

- **Livello 1 — introduzione** (questo rilascio): architettura d'insieme + survival kit, stile didattico con diagrammi, pensato per 20 minuti di lettura.
- **Livello 2 — microprogettazione** (futuro): un documento HTML per ogni componente (gateway, policy, sandbox, channel, tool, workspace, memory). Linkati dall'intro.

## Filosofia

Ereditata da `suprastructure`:

1. **Docs are canonical.** Se docs e codice divergono, i docs vincono.
2. **Protocol, not inheritance.** Interfacce come `typing.Protocol`.
3. **Registry over imports.** Consumer usa `registry.get(...)`, mai import diretti.
4. **Sicurezza di default, convenienza opt-in.** `Supervised` mode, localhost-only, sandbox obbligatoria.

## Licenza

Proprietaria. Uso interno.
