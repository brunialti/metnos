# myclaw — Agent Guide

Ciao, agente. Questo è il tuo entry point. **Leggi questo file per primo.**

## TL;DR (30 secondi)

`myclaw` è un assistente AI personale per uso domestico, Python ≥ 3.11, che gira a `/opt/myclaw/`. Costruisce **gateway + agent runtime + sandbox a strati + workspace** sopra [`suprastructure`](/opt/suprastructure/) (che fornisce LLM/STT/TTS/embedding/speaker-ID).

**Stato attuale:** solo documentazione di architettura. Nessun codice ancora. Se ti è stato chiesto di implementare qualcosa, verifica prima con l'utente.

## Cosa leggere prima di toccare qualsiasi cosa

L'ordine è questo:

1. [`docs/index.html`](docs/index.html) — indice navigabile dei documenti
2. [`docs/Myclaw_Architettura_Intro_v1.html`](docs/Myclaw_Architettura_Intro_v1.html) — il documento canonico sull'architettura, con diagrammi. Se non lo hai letto, non scrivere codice.
3. [`docs/Myclaw_Survival_Kit_v1.html`](docs/Myclaw_Survival_Kit_v1.html) — cosa un utente può farci, casi d'uso concreti
4. `docs/architecture/` — microprogettazione (quando esisterà): un file HTML per ogni componente

## Regole d'oro

### DO

- ✅ Leggi **prima** il documento di architettura intro. È la contract.
- ✅ Per tutto ciò che è servizio AI (LLM, STT, TTS, embedding), consuma `suprastructure` via `registry.get(Interface)`. **Non** importare SDK direttamente.
- ✅ Default di sicurezza ALTI: bind `127.0.0.1`, autonomy `Supervised`, sandbox sempre attiva per tool shell/fs.
- ✅ Usa `typing.Protocol` per le interfacce (stesso stile di suprastructure).
- ✅ Aggiorna la documentazione **prima** del codice. Se la doc non copre un caso, ferma e scrivi prima la doc.
- ✅ Prima di operazioni distruttive (refactor grossi, rimozione file): `tar czf /var/backups/myclaw/pre-$(date +%Y%m%d-%H%M).tar.gz /opt/myclaw`.

### DO NOT

- ❌ Non importare `anthropic` / `openai` / `faster-whisper` / ecc. in myclaw. Usa sempre il registry di suprastructure.
- ❌ Non bindare il gateway su `0.0.0.0` o su IP pubblici senza approvazione esplicita dell'utente.
- ❌ Non bypassare la sandbox con `subprocess.run` diretto da codice agente. Passa sempre dal wrapper.
- ❌ Non aggiungere canali (Telegram, WhatsApp, ...) prima che la loro Protocol `Channel` sia documentata in `docs/architecture/channel.html`.
- ❌ Non `git init` questa directory se Roberto non lo chiede esplicitamente.
- ❌ Non toccare path in allowlist forbidden: `/etc`, `/root`, `~/.ssh`, `~/.aws`, `~/.config/claude`, `/var/backups`, cartelle di altri progetti.

## Filosofia ereditata da suprastructure

- **Docs are canonical.** Fonte di verità = markdown/HTML in `docs/`. Il codice deriva. Se divergono, docs vincono.
- **Protocol, not inheritance.** Interfacce strutturali.
- **Registry over imports.** DI via registry globale.
- **Minimal core dependencies.** Provider-specific roba dietro extras.

## Quando in dubbio

- **"Come funziona X?"** → `docs/architecture/X.html` (se esiste), altrimenti `docs/Myclaw_Architettura_Intro_v1.html`
- **"Cosa c'è già fatto?"** → niente codice ancora. Solo docs.
- **"Posso scrivere il componente X?"** → solo se `docs/architecture/X.html` esiste e Roberto ti ha detto di procedere.

## Remember

**Docs first, code second.** Se stai scrivendo codice senza aver prima affinato la doc corrispondente, stai sbagliando.
