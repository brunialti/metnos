# SPDX-License-Identifier: AGPL-3.0-only
"""Installer i18n — IT/EN catalog. The installer adheres to the Metnos i18n
standard: every user-facing string resolves here in the chosen locale
(METNOS_LOCALE, set at the disclaimer gate; default 'en')."""
from __future__ import annotations
import os

def locale() -> str:
    # The installer UI is ENGLISH-ONLY for now (decision, 9/6): the IT/EN choice
    # during install selects METNOS's runtime language, NOT the installer's. So
    # this is pinned to English regardless of METNOS_LOCALE. The IT catalog
    # entries are scaffolding for a future fully-localized installer.
    return "en"

_CATALOG: dict[str, dict[str, str]] = {
    # ─── __main__ (orchestrator) ─────────────────────────────────
    "main_welcome_title": {
        "en": "Metnos installer",
        "it": "Installer Metnos",
    },
    "main_welcome_subtitle": {
        "en": "Self-hosted AI agent · AGPL-3.0 · metnos.com",
        "it": "Agente IA self-hosted · AGPL-3.0 · metnos.com",
    },
    "main_welcome_intro": {
        "en": "  [dim]This installer will set up Metnos in [bold]six phases[/bold]. "
              "Each phase is idempotent: you can interrupt and resume at any time.[/dim]\n",
        "it": "  [dim]Questo installer configura Metnos in [bold]sei fasi[/bold]. "
              "Ogni fase e' idempotente: puoi interrompere e riprendere in qualsiasi momento.[/dim]\n",
    },
    "main_disclaimer_already": {
        "en": "Disclaimer previously accepted (lang={existing}). Re-show with --force-phase 0.",
        "it": "Avvertenza gia' accettata (lingua={existing}). Per rimostrarla usa --force-phase 0.",
    },
    "main_disclaimer_needs_interactive": {
        "en": "The POC disclaimer must be accepted interactively at least once. "
              "Re-run without --yes, accept, then re-add --yes for subsequent runs.",
        "it": "L'avvertenza POC va accettata in modo interattivo almeno una volta. "
              "Riesegui senza --yes, accetta, poi riaggiungi --yes per le esecuzioni successive.",
    },
    "main_disclaimer_not_accepted": {
        "en": "Disclaimer not accepted — aborting installation.",
        "it": "Avvertenza non accettata — installazione interrotta.",
    },
    "main_disclaimer_accepted": {
        "en": "Disclaimer accepted; locale = {lang}",
        "it": "Avvertenza accettata; locale = {lang}",
    },
    "main_confirm_proceed": {
        "en": "Proceed with the next pending phase?",
        "it": "Procedere con la prossima fase in sospeso?",
    },
    "main_phase_already_done": {
        "en": "Phase {num} ({human_name}) already done — skipping",
        "it": "Fase {num} ({human_name}) gia' completata — la salto",
    },
    "main_phase_force": {
        "en": "--force-phase {num}: sentinel cleared, re-running",
        "it": "--force-phase {num}: sentinella azzerata, rieseguo",
    },
    "main_phase_not_implemented": {
        "en": "Phase {num} module not implemented yet ({mod_name}): {err}",
        "it": "Modulo della fase {num} non ancora implementato ({mod_name}): {err}",
    },
    "main_phase_interrupted": {
        "en": "\nPhase {num} interrupted by user. State preserved; re-run to resume.",
        "it": "\nFase {num} interrotta dall'utente. Stato preservato; riesegui per riprendere.",
    },
    "main_phase_failed": {
        "en": "Phase {num} failed: {etype}: {err}",
        "it": "Fase {num} fallita: {etype}: {err}",
    },
    "main_phase_complete": {
        "en": "Phase {num} ({human_name}) complete",
        "it": "Fase {num} ({human_name}) completata",
    },
    "main_aborted": {
        "en": "Aborted.",
        "it": "Interrotto.",
    },
    "main_abort_at_phase": {
        "en": "Aborting at this phase. Re-run installer to resume.",
        "it": "Interruzione a questa fase. Riesegui l'installer per riprendere.",
    },
    "main_done_title": {
        "en": "Install complete",
        "it": "Installazione completata",
    },
    "main_done_subtitle": {
        "en": "Run `systemctl --user status metnos-http` to verify",
        "it": "Esegui `systemctl --user status metnos-http` per verificare",
    },

    # ─── phase 1 (bootstrap) ─────────────────────────────────────
    "p1_banner_title": {
        "en": "Phase 1 — Bootstrap",
        "it": "Fase 1 — Bootstrap",
    },
    "p1_banner_subtitle": {
        "en": "Pre-flight checks + Python dependencies + runtime directories",
        "it": "Controlli preliminari + dipendenze Python + directory di runtime",
    },
    "p1_pip_not_found": {
        "en": "pip not found at {pip} — was bootstrap.sh run?",
        "it": "pip non trovato in {pip} — bootstrap.sh e' stato eseguito?",
    },
    "p1_dep_install_failed": {
        "en": "failed to install {dep}: {reason}",
        "it": "installazione di {dep} fallita: {reason}",
    },
    "p1_dep_timeout": {
        "en": "timeout installing {dep}, skipping",
        "it": "timeout durante l'installazione di {dep}, la salto",
    },
    "p1_step_preflight": {
        "en": "Running pre-flight checks",
        "it": "Esecuzione dei controlli preliminari",
    },
    "p1_preflight_failed": {
        "en": "Pre-flight failed. Re-run with --force to ignore (not recommended) or fix the issues above.",
        "it": "Controlli preliminari falliti. Riesegui con --force per ignorarli (sconsigliato) o correggi i problemi sopra.",
    },
    "p1_step_dirs": {
        "en": "Creating runtime directory layout",
        "it": "Creazione della struttura delle directory di runtime",
    },
    "p1_dirs_ready": {
        "en": "{count} directories ready",
        "it": "{count} directory pronte",
    },
    "p1_step_deps": {
        "en": "Installing {count} Python dependencies (this can take a few minutes)",
        "it": "Installazione di {count} dipendenze Python (puo' richiedere qualche minuto)",
    },
    "p1_deps_done": {
        "en": "{installed} installed, {skipped} already present",
        "it": "{installed} installate, {skipped} gia' presenti",
    },
    "p1_step_imports": {
        "en": "Verifying core imports",
        "it": "Verifica degli import principali",
    },
    "p1_imports_ok": {
        "en": "Core modules importable",
        "it": "Moduli principali importabili",
    },
    "p1_import_failure": {
        "en": "import failure: {err}",
        "it": "errore di import: {err}",
    },
    "p1_progress_deps": {
        "en": "Installing Python dependencies",
        "it": "Installazione delle dipendenze Python",
    },

    # ─── phase 2 (infrastructure) ────────────────────────────────
    "p2_tuning_notice": {
        "en": "  [bold yellow]Tuning notice[/bold yellow]\n"
              "  [yellow]The default tier configuration has been tested end-to-end.[/yellow]\n"
              "  [yellow]Alternative models work — but their effects are not predicted.[/yellow]\n"
              "  [dim]Use the defaults first. Swap one tier at a time afterwards via[/dim]\n"
              "  [dim]~/.config/metnos/llm_tiers.toml. Canonical defaults: runtime/llm_router.py::DEFAULT_TIERS.[/dim]",
        "it": "  [bold yellow]Nota sulla messa a punto[/bold yellow]\n"
              "  [yellow]La configurazione dei tier predefinita e' stata testata end-to-end.[/yellow]\n"
              "  [yellow]Modelli alternativi funzionano — ma i loro effetti non sono prevedibili.[/yellow]\n"
              "  [dim]Usa prima i valori predefiniti. Poi sostituisci un tier alla volta tramite[/dim]\n"
              "  [dim]~/.config/metnos/llm_tiers.toml. Default canonici: runtime/llm_router.py::DEFAULT_TIERS.[/dim]",
    },
    "p2_tiers_exists": {
        "en": "{path} already exists — leaving in place. Edit by hand to change tiers.",
        "it": "{path} esiste gia' — lo lascio invariato. Modificalo a mano per cambiare i tier.",
    },
    "p2_tiers_wrote": {
        "en": "wrote {path}",
        "it": "scritto {path}",
    },
    "p2_local_tiers_head": {
        "en": "  [bold]Local tiers[/bold] · fast / middle / wise",
        "it": "  [bold]Tier locali[/bold] · fast / middle / wise",
    },
    "p2_local_tiers_desc": {
        "en": "  [dim]One llama-server serves all three; they differ only in per-call[/dim]\n"
              "  [dim]parameters (think, num_predict). Concrete model per tier: runtime/llm_router.py::DEFAULT_TIERS.[/dim]",
        "it": "  [dim]Un solo llama-server serve tutti e tre; differiscono solo nei[/dim]\n"
              "  [dim]parametri per-chiamata (think, num_predict). Modello concreto per tier: runtime/llm_router.py::DEFAULT_TIERS.[/dim]",
    },
    "p2_endpoint_alive": {
        "en": "an LLM endpoint already answers at {endpoint} — wiring the local tiers to it",
        "it": "un endpoint LLM risponde gia' su {endpoint} — collego i tier locali a quello",
    },
    "p2_yes_defer_frontier": {
        "en": "--yes and no local LLM serving: deferring local tiers to the frontier API. "
              "Provision a local model later with `python install/llm_manager.py provision --yes`.",
        "it": "--yes e nessun LLM locale attivo: rimando i tier locali all'API frontier. "
              "Approvvigiona un modello locale piu' tardi con `python install/llm_manager.py provision --yes`.",
    },
    "p2_recommended_model": {
        "en": "  [bold]Recommended local model[/bold]: {model} "
              "[dim](backend {backend}, memory budget ~{budget} GB, wise-capable: {wise})[/dim]",
        "it": "  [bold]Modello locale consigliato[/bold]: {model} "
              "[dim](backend {backend}, budget memoria ~{budget} GB, idoneo wise: {wise})[/dim]",
    },
    "p2_model_none_feasible": {
        "en": "(none feasible)",
        "it": "(nessuno idoneo)",
    },
    "p2_no_model_fits": {
        "en": "No local model fits this hardware — the local tiers will use the frontier API.",
        "it": "Nessun modello locale e' adatto a questo hardware — i tier locali useranno l'API frontier.",
    },
    "p2_confirm_provision": {
        "en": "Provision the local model now (recommended)?",
        "it": "Approvvigionare ora il modello locale (consigliato)?",
    },
    "p2_skip_provision": {
        "en": "Skipping local provisioning — every planner turn will hit the frontier API "
              "(higher latency and cost).",
        "it": "Salto l'approvvigionamento locale — ogni turno del planner usera' l'API frontier "
              "(latenza e costo piu' alti).",
    },
    "p2_provision_ok": {
        "en": "local LLM provisioned and verified",
        "it": "LLM locale approvvigionato e verificato",
    },
    "p2_provision_failed": {
        "en": "local provisioning did not complete — falling back to frontier for now. "
              "Re-run: `python install/llm_manager.py provision --yes`.",
        "it": "approvvigionamento locale non completato — per ora ripiego sul frontier. "
              "Riesegui: `python install/llm_manager.py provision --yes`.",
    },
    "p2_frontier_head": {
        "en": "  [bold]Frontier tier[/bold] · hard synthesis, consult_frontier",
        "it": "  [bold]Tier frontier[/bold] · sintesi difficili, consult_frontier",
    },
    "p2_frontier_desc": {
        "en": "  [dim]Cloud API (Anthropic Opus by default). The API key is collected in the next phase.[/dim]",
        "it": "  [dim]API cloud (Anthropic Opus di default). La chiave API si raccoglie nella fase successiva.[/dim]",
    },
    "p2_frontier_confirm": {
        "en": "Use Anthropic for the frontier tier?",
        "it": "Usare Anthropic per il tier frontier?",
    },
    "p2_optional_skip_flag": {
        "en": "{label}: skipping (--skip {key})",
        "it": "{label}: saltato (--skip {key})",
    },
    "p2_optional_head": {
        "en": "\n  [bold]{label}[/bold] · {size}",
        "it": "\n  [bold]{label}[/bold] · {size}",
    },
    "p2_optional_desc": {
        "en": "  [dim]{desc}[/dim]",
        "it": "  [dim]{desc}[/dim]",
    },
    "p2_optional_confirm": {
        "en": "Install {label}?",
        "it": "Installare {label}?",
    },
    "p2_optional_scaffold": {
        "en": "{label}: scaffold only — full setup in a follow-up release.",
        "it": "{label}: solo impalcatura — configurazione completa in una release successiva.",
    },
    "p2_banner_title": {
        "en": "Phase 2 — Infrastructure",
        "it": "Fase 2 — Infrastruttura",
    },
    "p2_banner_subtitle": {
        "en": "Embedder + LLM tier configuration + optional services",
        "it": "Embedder + configurazione dei tier LLM + servizi opzionali",
    },
    "p2_step_bge": {
        "en": "Installing BGE-M3 ONNX embedder (mandatory, ~560 MB)",
        "it": "Installazione dell'embedder BGE-M3 ONNX (obbligatorio, ~560 MB)",
    },
    "p2_bge_failed": {
        "en": "BGE-M3 install failed ({failed}). Metnos cannot "
              "function without the embedder. Fix the network and re-run "
              "`python -m install --force-phase 2`.",
        "it": "Installazione di BGE-M3 fallita ({failed}). Metnos non puo' "
              "funzionare senza l'embedder. Sistema la rete e riesegui "
              "`python -m install --force-phase 2`.",
    },
    # ─── phase 2 optional scaffolds (labels + descriptions) ──────
    "p2_opt_vlm_label": {
        "en": "VLM Qwen3-VL-2B",
        "it": "VLM Qwen3-VL-2B",
    },
    "p2_opt_vlm_desc": {
        "en": "Image enrichment — captions for find_images_indices.",
        "it": "Arricchimento immagini — didascalie per find_images_indices.",
    },
    "p2_opt_photon_label": {
        "en": "Photon offline geocoder",
        "it": "Geocoder offline Photon",
    },
    "p2_opt_photon_desc": {
        "en": "Offline place lookup (per-country dataset).",
        "it": "Ricerca luoghi offline (dataset per nazione).",
    },
    "p2_opt_searxng_label": {
        "en": "SearXNG search aggregator",
        "it": "Aggregatore di ricerca SearXNG",
    },
    "p2_opt_searxng_desc": {
        "en": "Self-hosted web search.",
        "it": "Ricerca web self-hosted.",
    },

    # ─── phase 3 (code & workspace) ──────────────────────────────
    "p3_sqlite_exists": {
        "en": "{name}: exists, leaving in place",
        "it": "{name}: esiste, lo lascio invariato",
    },
    "p3_sqlite_placeholder": {
        "en": "created placeholder: {path}",
        "it": "creato segnaposto: {path}",
    },
    "p3_sqlite_initialised": {
        "en": "initialised sqlite: {path}",
        "it": "sqlite inizializzato: {path}",
    },
    "p3_i18n_exists": {
        "en": "i18n.sqlite: exists, leaving in place",
        "it": "i18n.sqlite: esiste, lo lascio invariato",
    },
    "p3_i18n_seeded": {
        "en": "i18n.sqlite seeded from bundled catalog ({n} keys, en+it)",
        "it": "i18n.sqlite inizializzato dal catalogo incluso ({n} chiavi, en+it)",
    },
    "p3_i18n_no_seed": {
        "en": "i18n seed catalog not bundled — created an empty i18n table. "
              "User-facing strings may show <missing:KEY> until the translator "
              "runs. Release pipeline should ship install/data/i18n_seed.sqlite.",
        "it": "catalogo i18n di partenza non incluso — creata una tabella i18n vuota. "
              "Le stringhe verso l'utente potrebbero mostrare <missing:KEY> finche' il "
              "traduttore non gira. La pipeline di release deve fornire install/data/i18n_seed.sqlite.",
    },
    "p3_source_no_root": {
        "en": "METNOS_INSTALL_ROOT not set — did bootstrap.sh complete?",
        "it": "METNOS_INSTALL_ROOT non impostata — bootstrap.sh e' arrivato in fondo?",
    },
    "p3_source_missing": {
        "en": "source tree missing: {missing}",
        "it": "albero dei sorgenti incompleto: {missing}",
    },
    "p3_source_complete": {
        "en": "source tree complete at {root}",
        "it": "albero dei sorgenti completo in {root}",
    },
    "p3_sign_no_env": {
        "en": "METNOS_INSTALL_ROOT/METNOS_VENV non settati — salto la firma executor",
        "it": "METNOS_INSTALL_ROOT/METNOS_VENV non settati — salto la firma executor",
    },
    "p3_sign_failed": {
        "en": "firma executor fallita: {err}",
        "it": "firma executor fallita: {err}",
    },
    "p3_sign_rc": {
        "en": "sign-all rc={rc}: {out}",
        "it": "sign-all rc={rc}: {out}",
    },
    "p3_sign_done": {
        "en": "executor firmati",
        "it": "executor firmati",
    },
    "p3_banner_title": {
        "en": "Phase 3 — Metnos code & workspace",
        "it": "Fase 3 — Codice Metnos e workspace",
    },
    "p3_banner_subtitle": {
        "en": "Verify source, prepare empty databases",
        "it": "Verifica dei sorgenti, preparazione dei database vuoti",
    },
    "p3_step_verify": {
        "en": "Verifying source tree",
        "it": "Verifica dell'albero dei sorgenti",
    },
    "p3_step_sqlite": {
        "en": "Initialising empty workspace databases",
        "it": "Inizializzazione dei database vuoti del workspace",
    },
    "p3_step_i18n": {
        "en": "Bootstrapping i18n message store",
        "it": "Inizializzazione dell'archivio messaggi i18n",
    },
    "p3_step_sign": {
        "en": "Signing executors with a local key (sign-all)",
        "it": "Firma degli executor con una chiave locale (sign-all)",
    },

    # ─── phase 4 (sensitive data) ────────────────────────────────
    "p4_admin_key_exists": {
        "en": "admin.key exists, leaving in place: {path}",
        "it": "admin.key esiste, la lascio invariata: {path}",
    },
    "p4_admin_key_generated": {
        "en": "admin.key generated (256-bit, {path})",
        "it": "admin.key generata (256-bit, {path})",
    },
    "p4_cred_stashed": {
        "en": "runtime/credentials not yet available; secret stashed at {path} (mode 0600)",
        "it": "runtime/credentials non ancora disponibile; segreto riposto in {path} (mode 0600)",
    },
    "p4_cred_stored": {
        "en": "credential stored: domain={domain}",
        "it": "credenziale salvata: dominio={domain}",
    },
    "p4_cred_store_failed": {
        "en": "failed to store credential {domain}: {err}",
        "it": "salvataggio della credenziale {domain} fallito: {err}",
    },
    "p4_ask_admin_user": {
        "en": "Admin username (for the web dashboard)",
        "it": "Nome utente admin (per la dashboard web)",
    },
    "p4_port_in_use_yes": {
        "en": "port {port} is already in use — set METNOS_HTTP_PORT to a free port.",
        "it": "la porta {port} e' gia' in uso — imposta METNOS_HTTP_PORT su una porta libera.",
    },
    "p4_ask_http_port": {
        "en": "HTTP port for the Metnos dashboard (1024-65535)",
        "it": "Porta HTTP per la dashboard Metnos (1024-65535)",
    },
    "p4_port_invalid": {
        "en": "'{raw}' is not a valid port (1024-65535) — try again.",
        "it": "'{raw}' non e' una porta valida (1024-65535) — riprova.",
    },
    "p4_port_in_use_confirm": {
        "en": "port {port} looks already in use — use it anyway?",
        "it": "la porta {port} sembra gia' in uso — usarla comunque?",
    },
    "p4_telegram_head": {
        "en": "\n  [bold]Telegram channel[/bold] (optional)",
        "it": "\n  [bold]Canale Telegram[/bold] (opzionale)",
    },
    "p4_telegram_desc": {
        "en": "  [dim]Lets you chat with the agent from Telegram. "
              "Create a bot at @BotFather and paste its token.[/dim]",
        "it": "  [dim]Ti permette di parlare con l'agente da Telegram. "
              "Crea un bot su @BotFather e incolla il suo token.[/dim]",
    },
    "p4_telegram_confirm": {
        "en": "Configure Telegram now?",
        "it": "Configurare Telegram ora?",
    },
    "p4_telegram_token": {
        "en": "Telegram BOT_TOKEN",
        "it": "BOT_TOKEN di Telegram",
    },
    "p4_telegram_empty": {
        "en": "empty token, skipping Telegram setup",
        "it": "token vuoto, salto la configurazione di Telegram",
    },
    "p4_telegram_cred_desc": {
        "en": "Telegram BotFather token",
        "it": "Token BotFather di Telegram",
    },
    "p4_imap_head": {
        "en": "\n  [bold]IMAP mail accounts[/bold] (optional)",
        "it": "\n  [bold]Account di posta IMAP[/bold] (opzionali)",
    },
    "p4_imap_confirm_add": {
        "en": "Add a mail account?",
        "it": "Aggiungere un account di posta?",
    },
    "p4_imap_label": {
        "en": "Account label (e.g. 'personal', 'work')",
        "it": "Etichetta dell'account (es. 'personale', 'lavoro')",
    },
    "p4_imap_host": {
        "en": "IMAP server hostname",
        "it": "Hostname del server IMAP",
    },
    "p4_imap_user": {
        "en": "IMAP username",
        "it": "Nome utente IMAP",
    },
    "p4_imap_password": {
        "en": "IMAP password",
        "it": "Password IMAP",
    },
    "p4_imap_cred_desc": {
        "en": "IMAP account: {label}",
        "it": "Account IMAP: {label}",
    },
    "p4_imap_confirm_another": {
        "en": "Add another account?",
        "it": "Aggiungere un altro account?",
    },
    "p4_apikey_head": {
        "en": "\n  [bold]{provider} API key[/bold] (optional)",
        "it": "\n  [bold]Chiave API {provider}[/bold] (opzionale)",
    },
    "p4_apikey_desc": {
        "en": "  [dim]Used for frontier-tier reasoning when explicitly invoked. "
              "Read from {env_hint} if not provided here.[/dim]",
        "it": "  [dim]Usata per il ragionamento del tier frontier quando invocato esplicitamente. "
              "Letta da {env_hint} se non fornita qui.[/dim]",
    },
    "p4_apikey_confirm": {
        "en": "Configure {provider} now?",
        "it": "Configurare {provider} ora?",
    },
    "p4_apikey_ask": {
        "en": "{provider} API key",
        "it": "Chiave API {provider}",
    },
    "p4_apikey_cred_desc": {
        "en": "{provider} API key",
        "it": "Chiave API {provider}",
    },
    "p4_workspace_head": {
        "en": "\n  [bold]Workspace paths[/bold] (where Metnos may read your files)",
        "it": "\n  [bold]Percorsi del workspace[/bold] (dove Metnos puo' leggere i tuoi file)",
    },
    "p4_workspace_pics": {
        "en": "Pictures directory",
        "it": "Directory delle immagini",
    },
    "p4_workspace_docs": {
        "en": "Documents directory",
        "it": "Directory dei documenti",
    },
    "p4_locale_choice": {
        "en": "Default UI / report language",
        "it": "Lingua predefinita dell'interfaccia / dei report",
    },
    "p4_banner_title": {
        "en": "Phase 4 — Sensitive data",
        "it": "Fase 4 — Dati sensibili",
    },
    "p4_banner_subtitle": {
        "en": "Admin key + optional channel / API credentials (stored encrypted)",
        "it": "Chiave admin + credenziali opzionali di canale / API (salvate cifrate)",
    },
    "p4_yes_skip_all": {
        "en": "Running with --yes: every optional integration will be skipped. "
              "Use `metnos-cli credentials add` later to fill them in.",
        "it": "Esecuzione con --yes: ogni integrazione opzionale verra' saltata. "
              "Usa `metnos-cli credentials add` piu' tardi per aggiungerle.",
    },
    "p4_done": {
        "en": "Phase 4 done — all secrets stored (encrypted where the runtime is available).",
        "it": "Fase 4 completata — tutti i segreti salvati (cifrati dove il runtime e' disponibile).",
    },

    # ─── phase 5 (systemd) ───────────────────────────────────────
    "p5_missing_template": {
        "en": "missing template: {path}",
        "it": "template mancante: {path}",
    },
    "p5_unit_wrote": {
        "en": "wrote {dest}",
        "it": "scritto {dest}",
    },
    "p5_banner_title": {
        "en": "Phase 5 — Systemd services",
        "it": "Fase 5 — Servizi systemd",
    },
    "p5_banner_subtitle": {
        "en": "Install user units · enable · health-probe",
        "it": "Installa le unit utente · abilita · sonda di salute",
    },
    "p5_no_systemctl": {
        "en": "systemctl not found — this installer requires systemd (Linux user session).",
        "it": "systemctl non trovato — questo installer richiede systemd (sessione utente Linux).",
    },
    "p5_templates_missing": {
        "en": "templates dir missing: {dir}",
        "it": "directory dei template mancante: {dir}",
    },
    "p5_step_http_unit": {
        "en": "Installing metnos-http.service (port {port})",
        "it": "Installazione di metnos-http.service (porta {port})",
    },
    "p5_step_telegram_unit": {
        "en": "Installing metnos-telegram-daemon.service",
        "it": "Installazione di metnos-telegram-daemon.service",
    },
    "p5_telegram_not_importable": {
        "en": "runtime.telegram_daemon not importable — skipping Telegram unit. "
              "Once the module ships, re-run `python -m install --force-phase 5`.",
        "it": "runtime.telegram_daemon non importabile — salto la unit Telegram. "
              "Quando il modulo sara' disponibile, riesegui `python -m install --force-phase 5`.",
    },
    "p5_step_daemon_reload": {
        "en": "Reloading systemd user unit catalog",
        "it": "Ricaricamento del catalogo unit utente di systemd",
    },
    "p5_daemon_reload_ok": {
        "en": "daemon-reload OK",
        "it": "daemon-reload OK",
    },
    "p5_http_not_importable": {
        "en": "runtime.metnos_http_server not importable in the venv. "
              "Unit file is in place, but enable is skipped to avoid a "
              "failing systemd loop. Re-run phase 5 once runtime/ ships.",
        "it": "runtime.metnos_http_server non importabile nel venv. "
              "La unit e' al suo posto, ma l'abilitazione viene saltata per evitare un "
              "ciclo di fallimenti systemd. Riesegui la fase 5 quando runtime/ sara' disponibile.",
    },
    "p5_step_enable_http": {
        "en": "Enabling and starting metnos-http.service",
        "it": "Abilitazione e avvio di metnos-http.service",
    },
    "p5_enable_failed": {
        "en": "systemctl enable failed: {err}",
        "it": "systemctl enable fallito: {err}",
    },
    "p5_http_enabled": {
        "en": "metnos-http enabled + started",
        "it": "metnos-http abilitato + avviato",
    },
    "p5_step_health": {
        "en": "Probing HTTP health endpoint (up to 20s)",
        "it": "Sondaggio dell'endpoint di salute HTTP (fino a 20s)",
    },
    "p5_health_ok": {
        "en": "http://127.0.0.1:{port}/agent/health responds 200",
        "it": "http://127.0.0.1:{port}/agent/health risponde 200",
    },
    "p5_health_timeout": {
        "en": "health endpoint did not respond within 20s — check `systemctl --user status metnos-http`",
        "it": "l'endpoint di salute non ha risposto entro 20s — controlla `systemctl --user status metnos-http`",
    },
    "p5_step_telegram_start": {
        "en": "Starting metnos-telegram-daemon.service",
        "it": "Avvio di metnos-telegram-daemon.service",
    },
    "p5_telegram_start_failed": {
        "en": "telegram daemon failed to start: {err}",
        "it": "il daemon telegram non si e' avviato: {err}",
    },
    "p5_telegram_running": {
        "en": "telegram daemon running",
        "it": "daemon telegram in esecuzione",
    },
    "p5_linger_tip": {
        "en": "  [bold]Tip:[/bold] to keep Metnos running across reboots even when "
              "you don't log in, run [cyan]sudo loginctl enable-linger $USER[/cyan].",
        "it": "  [bold]Suggerimento:[/bold] per tenere Metnos attivo ai riavvii anche quando "
              "non accedi, esegui [cyan]sudo loginctl enable-linger $USER[/cyan].",
    },
    "p5_probe_last_error": {
        "en": "last error: {err}",
        "it": "ultimo errore: {err}",
    },
    "p5_probe_desc": {
        "en": "Probing {url}",
        "it": "Sondaggio di {url}",
    },

    # ─── phase 6 (first boot) ────────────────────────────────────
    "p6_skill_unavailable": {
        "en": "skill selection unavailable ({err}); leaving defaults.",
        "it": "selezione delle skill non disponibile ({err}); mantengo i valori predefiniti.",
    },
    "p6_step_skills": {
        "en": "Skills (modular capabilities)",
        "it": "Skill (capacita' modulari)",
    },
    "p6_skills_info": {
        "en": "'core' is always on. Each skill below is dormant until its "
              "backend/credential is configured. Change later: metnos-skills.",
        "it": "'core' e' sempre attiva. Ogni skill sotto resta dormiente finche' il suo "
              "backend/credenziale non e' configurato. Modificale dopo: metnos-skills.",
    },
    "p6_skill_confirm": {
        "en": "Enable '{name}' — {desc} (needs {requires})",
        "it": "Abilitare '{name}' — {desc} (richiede {requires})",
    },
    "p6_skill_persist_failed": {
        "en": "could not persist skill '{name}': {err}",
        "it": "impossibile salvare la skill '{name}': {err}",
    },
    "p6_skills_enabled": {
        "en": "skills enabled: {skills}",
        "it": "skill abilitate: {skills}",
    },
    "p6_skills_core_only": {
        "en": "(core only)",
        "it": "(solo core)",
    },
    "p6_banner_title": {
        "en": "Phase 6 — First boot",
        "it": "Fase 6 — Primo avvio",
    },
    "p6_banner_subtitle": {
        "en": "Admin onboarding + summary + next steps",
        "it": "Onboarding admin + riepilogo + prossimi passi",
    },
    "p6_http_not_running": {
        "en": "The HTTP service is not running (phase 5 could not start it). "
              "The onboarding URL below would not resolve yet.",
        "it": "Il servizio HTTP non e' in esecuzione (la fase 5 non e' riuscita ad avviarlo). "
              "L'URL di onboarding qui sotto non sarebbe ancora raggiungibile.",
    },
    "p6_recover_head": {
        "en": "  [bold]To recover:[/bold]",
        "it": "  [bold]Per ripristinare:[/bold]",
    },
    "p6_recover_inspect": {
        "en": "    1) Inspect: [cyan]systemctl --user status metnos-http[/cyan]",
        "it": "    1) Ispeziona: [cyan]systemctl --user status metnos-http[/cyan]",
    },
    "p6_recover_logs": {
        "en": "    2) Logs:    [cyan]journalctl --user -u metnos-http -e[/cyan]",
        "it": "    2) Log:      [cyan]journalctl --user -u metnos-http -e[/cyan]",
    },
    "p6_recover_rerun": {
        "en": "    3) Re-run:  [cyan]python -m install --force-phase 5[/cyan]",
        "it": "    3) Riesegui: [cyan]python -m install --force-phase 5[/cyan]",
    },
    "p6_http_unhealthy": {
        "en": "metnos-http started but did not pass the health probe yet — it may "
              "still be warming up. Check `systemctl --user status metnos-http`.",
        "it": "metnos-http si e' avviato ma non ha ancora superato la sonda di salute — "
              "potrebbe essere in fase di riscaldamento. Controlla `systemctl --user status metnos-http`.",
    },
    "p6_onboard_head": {
        "en": "  [bold green]One-shot admin onboarding URL[/bold green] (valid 15 min):",
        "it": "  [bold green]URL di onboarding admin monouso[/bold green] (valido 15 min):",
    },
    "p6_onboard_no_key": {
        "en": "admin.key not found — was phase 4 completed?",
        "it": "admin.key non trovata — la fase 4 e' stata completata?",
    },
    "p6_onboard_deferred": {
        "en": "Onboarding URL deferred until the service is up (see recovery steps above).",
        "it": "URL di onboarding rinviato finche' il servizio non e' attivo (vedi i passi di ripristino sopra).",
    },
    "p6_webui_head": {
        "en": "  [bold green]Connect to the Web UI[/bold green]:",
        "it": "  [bold green]Connettiti alla Web UI[/bold green]:",
    },
    "p6_webui_local": {
        "en": "    • From this machine:   http://127.0.0.1:{port}/",
        "it": "    • Da questa macchina:   http://127.0.0.1:{port}/",
    },
    "p6_webui_remote": {
        "en": "    • From another device: http://<this-machine-ip>:{port}/",
        "it": "    • Da un altro dispositivo: http://<ip-di-questa-macchina>:{port}/",
    },
    "p6_webui_keynote": {
        "en": "    [bold]First connect needs the admin key.[/bold] Easiest: open the\n"
              "    one-shot onboarding URL above (valid 15 min) — it claims access for\n"
              "    your browser. The key itself lives at [cyan]~/.config/metnos/admin.key[/cyan]\n"
              "    (`cat` it if you need it). Lost the URL? re-run\n"
              "    `python -m install --force-phase 6` to print a fresh one.",
        "it": "    [bold]Il primo accesso richiede la chiave admin.[/bold] Piu' semplice: apri\n"
              "    l'URL di onboarding monouso sopra (valido 15 min) — assegna l'accesso al\n"
              "    tuo browser. La chiave vive in [cyan]~/.config/metnos/admin.key[/cyan]\n"
              "    (fai `cat` se ti serve). Perso l'URL? riesegui\n"
              "    `python -m install --force-phase 6` per generarne uno nuovo.",
    },
    "p6_tg_connect_head": {
        "en": "  [bold green]Connect via Telegram[/bold green] (enabled):",
        "it": "  [bold green]Connettiti via Telegram[/bold green] (abilitato):",
    },
    "p6_tg_connect_1": {
        "en": "    1) On Telegram, open the bot you configured (the one BotFather gave you).",
        "it": "    1) Su Telegram, apri il bot che hai configurato (quello fornito da BotFather).",
    },
    "p6_tg_connect_2": {
        "en": "    2) Send /start.",
        "it": "    2) Invia /start.",
    },
    "p6_tg_connect_3": {
        "en": "    3) Paste the pairing code shown on the Web UI to link your account.",
        "it": "    3) Incolla il codice di abbinamento mostrato nella Web UI per collegare il tuo account.",
    },
    "p6_tg_disabled_head": {
        "en": "  [bold]Telegram[/bold] is not configured. To enable it later:",
        "it": "  [bold]Telegram[/bold] non e' configurato. Per abilitarlo piu' tardi:",
    },
    "p6_tg_disabled_1": {
        "en": "    1) Create a bot with @BotFather on Telegram and copy its token.",
        "it": "    1) Crea un bot con @BotFather su Telegram e copia il suo token.",
    },
    "p6_tg_disabled_2": {
        "en": "    2) Run:  python -m install --force-phase 4   (enter the token)",
        "it": "    2) Esegui:  python -m install --force-phase 4   (inserisci il token)",
    },
    "p6_tg_disabled_3": {
        "en": "    3) Then /start the bot and pair as above.",
        "it": "    3) Poi avvia il bot con /start e abbinalo come sopra.",
    },
    "p6_step_summary": {
        "en": "Writing install summary",
        "it": "Scrittura del riepilogo di installazione",
    },
    "p6_summary_at": {
        "en": "summary at {path}",
        "it": "riepilogo in {path}",
    },
    "p6_final_done": {
        "en": "  [bold]All done.[/bold] Metnos is installed and running.",
        "it": "  [bold]Tutto fatto.[/bold] Metnos e' installato e in esecuzione.",
    },
    "p6_final_started": {
        "en": "  [bold]Installed.[/bold] The service started but has not passed its "
              "health check yet — give it a moment, then re-check.",
        "it": "  [bold]Installato.[/bold] Il servizio si e' avviato ma non ha ancora superato "
              "il controllo di salute — attendi un momento, poi ricontrolla.",
    },
    "p6_final_not_running": {
        "en": "  [bold yellow]Installed, but the service is not running yet.[/bold yellow] "
              "Follow the recovery steps above before connecting.",
        "it": "  [bold yellow]Installato, ma il servizio non e' ancora in esecuzione.[/bold yellow] "
              "Segui i passi di ripristino sopra prima di connetterti.",
    },
    "p6_final_anytime": {
        "en": "  [dim]Run `cat ~/.local/share/metnos/install_summary.md` anytime.[/dim]",
        "it": "  [dim]Esegui `cat ~/.local/share/metnos/install_summary.md` quando vuoi.[/dim]",
    },
}

def t(key: str, /, **kw) -> str:
    entry = _CATALOG.get(key)
    if entry is None:
        return key  # missing key: surface the key, never crash
    s = entry.get(locale()) or entry.get("en") or key
    try:
        return s.format(**kw) if kw else s
    except (KeyError, IndexError):
        return s
