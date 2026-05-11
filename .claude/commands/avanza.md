---
description: Avanza la progettazione di myclaw scrivendo il prossimo doc secondo l'ordine del giudizio di fase 0.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(ls:*), Bash(curl:*), Bash(python3:*)
---

# /avanza — produci il prossimo documento di microprogettazione

## Ruolo
Sei l'architetto-scrivano di myclaw (`/opt/myclaw/`). A ogni invocazione produci **un solo** documento di microprogettazione, seguendo l'ordine canonico stabilito in `Myclaw_Prospettive_Giudizio_v1.html §8`.

## Stato e ordine canonico

Il prossimo doc da scrivere è il primo di questa lista che **non esiste** come file `/opt/myclaw/docs/architecture/<nome>.html`:

1. `agent_runtime.html` — reasoning loop (ReAct+function calling default), prompt structure, tool-call validation, `ExecutionTrace` first-class, provider failover via suprastructure
2. `approval_ux.html` — flussi CLI/Telegram, batching "approva simili per N min", pausa lettura 3s, revoca, tutor mode ogni K approvazioni
3. `eval.html` — 15-20 scenari YAML + harness replay + report success/p95-latency/cost, CI gate
4. `gateway.html` — FastAPI, sessioni, webhook, cron, auth
5. `channel.html` — Protocol Channel + CLI + Telegram; **status visibility obbligatoria**
6. `tool.html` — Protocol Tool + tool base (fs, shell, web_fetch, supra_llm)
7. `sandbox.html` — profili bwrap, systemd hardening, Docker opzionale
8. `policy.html` — autonomy, approval gating, rate/cost, forbidden paths; **include `cost_tiering` come sotto-sezione** (modello locale per gate, frontier per ragionamento; budget 2€ soft / 5€ hard)
9. `workspace.html` — IDENTITY / USER / MEMORY / AGENTS / SOUL
10. `memory.html` — 3 livelli (working/episodic/semantic+core), promozione via reflection con approvazione
11. `observability.html` — logging JSON, audit append-only, metrics, health
12. `pairing.html` — codici firmati, approve/revoke, SLA delivery
13. `neuron.html` — manifest schema, firma HMAC, journal
14. `synthesizer.html` — pipeline 7 stadi, gate approvazione umana, retry max 3
15. `synapse.html` — grafo, counter, decay Ebbinghaus/ACT-R, potatura
16. `constitution.html` — 4 Leggi + rito di modifica + framing linguistico anti-antropomorfizzazione + boundary untrusted content
17. `config.html` — pydantic-settings schema, overrides, secrets

## Convenzioni di scrittura (obbligatorie)

- **Template HTML** identico ai doc di Livello 1 (vedi `Myclaw_Architettura_Intro_v1.html` come riferimento): palette navy `#1A477A` / sage `#548235` / bronze `#A0522D`, font Segoe UI, max-width 960px.
- **Struttura**: title-page → TOC numerata → capitoli `<h1 id="capN">N. Titolo</h1>` → hr → "Continua a leggere" cards → footer.
- **Sezioni obbligatorie** per ogni doc di componente:
  1. *Scopo e confini* (cos'è, cosa non è)
  2. *Contratto* (Protocol Python con type hints, lista errori sollevabili)
  3. *Implementazione di default* (scelta di base + motivazione)
  4. *Alternative considerate* (e perché scartate o rimandate)
  5. *Test di conformità* (cosa ogni impl deve passare)
  6. *Riferimenti* (link a Letteratura&Adattamenti quando applicabile)
- **Almeno una figura SVG inline**. No PNG, no immagini esterne.
- **Breadcrumb in testa** (sticky, stile identico agli altri doc).
- **"Continua a leggere"** con almeno 3 card: doc di Livello 1 rilevanti, indice microprogettazione, indice principale.
- **Self-contained**: niente `<script>` esterno, niente CSS esterno.
- **Stampabile PDF**: `@media print` con page-break rules come negli altri doc.
- **Lingua**: italiano.

## Procedura

1. **Leggi lo stato**: `ls /opt/myclaw/docs/architecture/*.html` → identifica il prossimo in ordine.
2. **Leggi i riferimenti rilevanti**: sempre `Myclaw_Prospettive_Giudizio_v1.html §4-5` e `Myclaw_Letteratura_Adattamenti_v1.html` per i riferimenti già catalogati.
3. **Scrivi il doc**. Se emerge una decisione non banale non ancora documentata altrove, scegli il default più conservativo e documentala in una callout `warning` con "DECISIONE DA CONFERMARE".
4. **Aggiorna `docs/architecture/index.html`**: cambia lo stato della riga da `pianificato` a `approvato`.
5. **Verifica**: `curl -sI http://192.168.1.33:8810/architecture/<nome>.html` → deve dare `200 OK`.
6. **Report finale** in ≤ 8 righe: nome del doc, URL live, 3 decisioni chiave prese (se ci sono), prossimo doc in coda.

## Vincoli

- UN solo doc per invocazione. Non scrivere anche quello dopo, anche se è breve.
- Non modificare doc di Livello 1 esistenti (solo l'indice microprogettazione e il nuovo file).
- Non toccare file fuori da `/opt/myclaw/docs/`.
- Nessun `git`, nessun `sudo`, nessun `systemctl restart` del server docs (non serve: è un SimpleHTTPServer che rilegge il filesystem).
- Se serve uno SVG complesso, preferisci chiarezza a elaborazione.
- Se un riferimento incrociato a un doc ancora da scrivere è necessario, linkalo comunque (il link diventerà valido quando quel doc sarà prodotto).
