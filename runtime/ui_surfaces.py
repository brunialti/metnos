"""Canonical registry of the Settings surfaces shown by the web chat.

The sidebar and the Tutor consume the same typed data.  Routes, breadcrumbs
and visible-purpose summaries therefore do not drift into separate prompt or
template lists.

DRIFT GUARD: the registry is the curated editorial layer over the template
structure; wording is free, silent divergence is not.  ``validate_surfaces``
recomputes each template's structural fingerprint (its literal ``<th>`` row,
including ``msg()`` i18n keys) and fails when it no longer matches the
declared ``structure_sha``.  After editing a Settings page run
``python3 runtime/ui_surfaces.py``: it prints the current fingerprints and
the extracted columns, so updating the registry is a prompted diff review,
not an act of memory.  Route parity with the admitted GET routes is enforced
by the same validator.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


_SECTIONS = {
    "activity": ("Attività", "Activity"),
    "lifecycle": ("Ciclo di vita", "Lifecycle"),
    "memory": ("Memoria", "Memory"),
    "system": ("Sistema", "System"),
}


@dataclass(frozen=True, slots=True)
class UiSurfaceSpec:
    key: str
    section: str
    route: str
    label_it: str
    label_en: str
    summary_it: str
    summary_en: str
    visible_it: tuple[str, ...] = ()
    visible_en: tuple[str, ...] = ()
    controls_it: tuple[str, ...] = ()
    controls_en: tuple[str, ...] = ()
    procedure_it: tuple[str, ...] = ()
    procedure_en: tuple[str, ...] = ()
    stop_it: tuple[str, ...] = ()
    stop_en: tuple[str, ...] = ()
    audience: str = "instance_admin"
    # `audience` governs page ACCESS (every /admin route stays admin-only);
    # `knowledge_audience` governs who may LEARN the surface's content via the
    # Tutor, mirroring the per-card `audience_minima` contract ratified in F1.
    knowledge_audience: str = "instance_admin"
    # Template backing this surface; empty means `<key>.html` with dashes
    # mapped to underscores.  `structure_sha` is the fingerprint of the
    # template's literal header row: see DRIFT GUARD in the module docstring.
    template: str = ""
    structure_sha: str = ""
    # Signed Tutor projection: closed read-only probes admitted when this
    # surface is selected.  IDs are validated against tutor.probes at build.
    probe_refs: tuple[str, ...] = ()

    def label(self, lang: str) -> str:
        return self.label_it if lang == "it" else self.label_en

    def summary(self, lang: str) -> str:
        return self.summary_it if lang == "it" else self.summary_en

    def visible(self, lang: str) -> tuple[str, ...]:
        return self.visible_it if lang == "it" else self.visible_en

    def controls(self, lang: str) -> tuple[str, ...]:
        return self.controls_it if lang == "it" else self.controls_en

    def procedure(self, lang: str) -> tuple[str, ...]:
        return self.procedure_it if lang == "it" else self.procedure_en

    def stop_conditions(self, lang: str) -> tuple[str, ...]:
        return self.stop_it if lang == "it" else self.stop_en

    def breadcrumb(self, lang: str) -> str:
        # Navigation paths are UI literals in the language of the current
        # user.  The Settings shell and this registry consume the same locale,
        # so Tutor and public navigation docs quote what the person sees.
        parts = ["Settings"]
        if self.section:
            parts.append(_SECTIONS[self.section][0 if lang == "it" else 1])
        if self.route != "/admin":
            parts.append(self.label(lang))
        return " > ".join(parts)


SURFACES: tuple[UiSurfaceSpec, ...] = (
    UiSurfaceSpec(
        "settings", "", "/admin", "Settings", "Settings",
        "Panoramica dello stato e accesso alle aree di amministrazione.",
        "Status overview and access to the administration areas.",
        (
            "versione e tempo di attività",
            "turni nelle ultime 24 ore, errori e latenza mediana",
            "proposte introspettive e Telos per stato",
            "executor totali, artigianali, sintetizzati e deprecati",
            "esecuzioni odierne, fallimenti e attività registrate dello scheduler",
            "firme Safety per classe",
            "utenti host, guest e ultimi appaiati",
        ),
        (
            "version and uptime",
            "turns in the last 24 hours, errors, and median latency",
            "introspective and Telos proposals by state",
            "total, handcrafted, synthesized, and deprecated executors",
            "today's scheduler runs, failures, and registered tasks",
            "Safety signatures by class",
            "host and guest users and the most recently paired users",
        ),
        ("collegamenti alle pagine di dettaglio di ogni riquadro",),
        ("links to each card's detail page",),
        template="dashboard.html",
    ),
    UiSurfaceSpec(
        "turns", "activity", "/admin/turns", "Turni", "Turns",
        "Richieste con canale, attore, passi ed esito.",
        "Requests with channel, actor, steps, and outcome.",
        ("identificativo turno", "inizio", "canale", "attore", "passi",
         "esito", "durata", "testo della richiesta"),
        ("turn identifier", "start time", "channel", "actor", "steps",
         "outcome", "duration", "request text"),
        structure_sha="afde90401bbb",
    ),
    UiSurfaceSpec(
        "runs", "activity", "/admin/runs", "Scheduler", "Scheduler",
        "Esecuzioni delle attività programmate, con esito e durata.",
        "Scheduled-task runs with outcome and duration.",
        ("identificativo dell'esecuzione", "attività", "inizio", "fine", "esito", "durata"),
        ("run identifier", "task", "start", "end", "outcome", "duration"),
        ("collegamento a Timer, dove si configura l'attività",),
        ("link to Timers, where tasks are configured",),
        structure_sha="eb12f98ec666",
        probe_refs=("scheduler_health",),
    ),
    UiSurfaceSpec(
        "timers", "activity", "/admin/timers", "Timer", "Timers",
        "Timer di sistema, stato e controlli disponibili.",
        "System timers, state, and available controls.",
        ("attività dell'utente e timer di sistema distinti", "nome e descrizione",
         "regola temporale", "stato", "prossima esecuzione",
         "ultima esecuzione ed esito", "conteggio delle esecuzioni e dei fallimenti"),
        ("separate user tasks and system timers", "name and description",
         "schedule", "state", "next execution", "last execution and outcome",
         "run and failure counts"),
        ("abilita", "disabilita", "esegui ora"),
        ("enable", "disable", "run now"),
        probe_refs=("scheduler_health",),
    ),
    UiSurfaceSpec(
        "builds", "activity", "/admin/builds", "Creazione indici", "Index builds",
        "Creazione degli indici con progresso, stato e tempo stimato.",
        "Index builds with progress, state, and ETA.",
        ("digest", "directory", "indice", "stato", "progresso", "tempo stimato",
         "ultimo aggiornamento", "unità attiva", "errori"),
        ("digest", "directory", "index", "state", "progress", "ETA",
         "last update", "active unit", "errors"),
        structure_sha="925affb988b2",
    ),
    UiSurfaceSpec(
        "changes", "lifecycle", "/admin/changes", "Modifiche", "Changes",
        "Proposte di modifica e relativo ciclo di approvazione.",
        "Change proposals and their approval lifecycle.",
        ("schede per stato del ciclo di vita", "filtri per famiglia e tipo",
         "soglia minima di punteggio e limite risultati", "punteggio", "tipo",
         "bersaglio", "origine", "riepilogo", "dettagli, effetti e metriche"),
        ("lifecycle-state tabs", "family and kind filters",
         "minimum score and result-limit filters", "score", "kind", "target",
         "origin", "summary", "details, effects, and metrics"),
        ("accetta", "rifiuta", "prepara", "ripristina", "riprova"),
        ("accept", "reject", "stage", "roll back", "retry"),
        (
            "Nella riga interessata confronta bersaglio, origine, riepilogo e punteggio; il punteggio aiuta l'ordinamento ma non garantisce la sicurezza.",
            "Apri i dettagli e verifica razionale, corpo completo, ID, impronta, effetti e metriche.",
            "Premi Accetta solo se contenuto e bersaglio coincidono con ciò che intendi autorizzare: l'accettazione mette la proposta in coda e non la applica immediatamente.",
            "Usa Rifiuta per respingerla o Prepara per differirla; Ripristina e Riprova compaiono soltanto negli stati che li ammettono.",
            "Dopo l'accettazione verifica che la riga indichi Accettata e in coda per l'applicazione.",
        ),
        (
            "In the relevant row, compare target, origin, summary, and score; score helps ordering but is not a security guarantee.",
            "Open the details and verify rationale, full body, ID, fingerprint, effects, and metrics.",
            "Press Accept only when content and target match what you intend to authorize: acceptance queues the proposal and does not apply it immediately.",
            "Use Reject to decline it or Stage to defer it; Roll back and Retry appear only in states that allow them.",
            "After acceptance, verify that the row says Accepted and queued for application.",
        ),
        (
            "le etichette o lo stato non coincidono con la procedura",
            "bersaglio, corpo o impronta non corrispondono a ciò che intendi autorizzare",
        ),
        (
            "the labels or state do not match the procedure",
            "the target, body, or fingerprint does not match what you intend to authorize",
        ),
        structure_sha="9bf76e2869d2",
    ),
    UiSurfaceSpec(
        "executors", "lifecycle", "/admin/executors", "Executor", "Executors",
        "Executor installati, appartenenza, stato e motivi di esclusione.",
        "Installed executors, membership, state, and exclusion reasons.",
        ("nome", "versione", "ciclo di vita", "appartenenza", "origine",
         "trasporto", "conformità allo standard", "capacità", "reversibilità",
         "executor rifiutati con percorso e motivo"),
        ("name", "version", "lifecycle", "membership", "origin", "transport",
         "standard compliance", "capability", "reversibility",
         "rejected executors with path and reason"),
        ("collegamento a statistiche e grafici",),
        ("link to statistics and charts",),
        structure_sha="8c69bd92bef0",
        probe_refs=("admitted_executor_state",),
    ),
    UiSurfaceSpec(
        "executor-stats", "lifecycle", "/admin/executors/stats",
        "Statistiche", "Statistics",
        "Statistiche operative degli executor.",
        "Operational executor statistics.",
        ("conteggi per origine e ciclo di vita",
         "distribuzione attivi, deprecati e archiviati",
         "eventi giornalieri degli ultimi 30 giorni"),
        ("counts by origin and lifecycle",
         "active, deprecated, and archived distribution",
         "daily events over the last 30 days"),
        ("visualizzazione JSON dei dati", "ritorno al catalogo executor"),
        ("JSON view of the data", "return to the executor catalog"),
        template="executors_stats.html",
        structure_sha="17980071db96",
    ),
    UiSurfaceSpec(
        "praxis", "memory", "/admin/praxis", "Praxis", "Praxis",
        "Stato degli strati del motore cognitivo e relativa configurazione.",
        "Cognitive-engine layer state and related configuration.",
        ("indicatori di autopath attive e retrocesse, osservazioni e anti-autopath",
         "scorciatoie L0 con origine, significato, usi e ultimo uso",
         "autopath L1 attive e retrocesse con esiti e punteggio",
         "osservazioni recenti e anti-autopath attivi",
         "distribuzione per stato e livello di recupero Pronoia"),
        ("active and demoted autopath, observation, and anti-autopath indicators",
         "L0 shortcuts with origin, meaning, uses, and last use",
         "active and demoted L1 autopaths with outcomes and score",
         "recent observations and active anti-autopaths",
         "state distribution and Pronoia recovery tier"),
        ("svuota L0, L1 o entrambe", "elimina una scorciatoia L0",
         "seleziona il livello Pronoia locale o cloud"),
        ("flush L0, L1, or both", "delete an L0 shortcut",
         "select the local or cloud Pronoia tier"),
        structure_sha="875c62bcd019",
    ),
    UiSurfaceSpec(
        "virt", "system", "/admin/virt", "Modelli", "Models",
        "Configurazione effettiva di LLM, embedding e VLM: LLM e VLM sono modificabili; l'embedding è solo consultabile.",
        "Effective configuration for language, embedding, and vision models: LLM and VLM are editable; embedding is view-only.",
        (
            "famiglie LLM, embedding e VLM",
            "fast micro procedural fidelity, middle, wise, creative e frontier e ruoli text, image e default",
            "fornitore, modello, endpoint o URL di base effettivi",
            "parametri effettivi di generazione LLM, fra cui think, temperature e reasoning_budget",
            "file di configurazione, provenienza dei valori e sostituzioni tramite variabili d'ambiente",
            "errori di lettura o validazione del TOML",
            "valori sensibili oscurati",
            "embedding consultabile ma non modificabile dalla pagina",
        ),
        (
            "LLM, embedding, and VLM families",
            "fast micro procedural fidelity, middle, wise, creative, and frontier tiers and text, image, and default roles",
            "effective provider, model, endpoint, or base URL",
            "effective LLM generation parameters, including think, temperature, and reasoning_budget",
            "configuration file, value origin, and environment override",
            "TOML read or validation errors",
            "redacted sensitive values",
            "embedding shown for inspection but not editable from the page",
        ),
        (
            "rileggi la configurazione dal file",
            "modifica una famiglia LLM o VLM e salva i valori visibili",
            "annulla le modifiche non salvate",
            "ripristina i valori iniziali di LLM o VLM della versione installata",
        ),
        (
            "reload the configuration from its file",
            "edit one LLM or VLM family and save visible values",
            "cancel unsaved changes",
            "restore the installed version's initial LLM or VLM values",
        ),
        (
            "Nella chat web apri Settings > Sistema > Modelli.",
            "Scegli la famiglia e controlla sia il valore sia la sua provenienza: file, valore iniziale, alias o valore di riserva dopo un errore. L'embedding è soltanto consultabile.",
            "Per LLM e VLM premi Modifica, cambia soltanto i campi necessari e scegli Salva modifiche; Metnos valida l'intero file prima di sostituirlo e conserva una copia privata di recupero.",
            "I parametri mostrati sono quelli effettivi del tier. Un override richiesto da una singola operazione non modifica la policy: ogni operazione sceglie solo il proprio workload e la policy di generazione resta del tier.",
            "Le chiamate successive usano la nuova configurazione; Rileggi configurazione mostra nuovamente ciò che il runtime risolve dal file.",
            "Per LLM o VLM, per tornare alla configurazione fornita dalla versione installata scegli Ripristina e conferma: anche in questo caso la configurazione precedente viene conservata come copia di recupero.",
        ),
        (
            "In the web chat, open Settings > System > Models.",
            "Choose the family and inspect both each value and its origin: file, default, alias, or fallback after an error. Embedding is view-only.",
            "For LLM and VLM, press Edit, change only the necessary fields, and choose Save changes; Metnos validates the complete file before replacing it and retains a private recovery copy.",
            "Shown parameters are the tier's effective values. A per-operation override does not change the policy: each operation selects only its workload, while generation policy remains owned by the tier.",
            "Subsequent calls use the new configuration; Reload configuration shows what the runtime resolves from the file again.",
            "For LLM or VLM, to return to the configuration supplied by the installed version, choose Restore defaults and confirm: the previous configuration is retained as a recovery copy in this case too.",
        ),
        (
            "password, token, chiavi, credenziali e parti sensibili degli URL non sono modificabili in questa pagina",
            "se il file cambia dopo l'apertura della pagina, Metnos non sovrascrive il cambiamento: rileggi la configurazione e riprova",
            "se la validazione fallisce, nessuna modifica viene salvata",
        ),
        (
            "passwords, tokens, keys, credentials, and sensitive URL parts cannot be edited on this page",
            "if the file changes after the page is opened, Metnos does not overwrite that change: reload the configuration and try again",
            "if validation fails, no change is saved",
            "the embedding backend cannot be changed from this page: it requires a dedicated, verified migration and index reconstruction",
        ),
    ),
    UiSurfaceSpec(
        "services", "system", "/admin/services", "Servizi", "Services",
        "Servizi Metnos con stato, salute, installazione, PID e controlli.",
        "Metnos services with state, health, installation, PID, and controls.",
        ("servizi raggruppati per funzione", "stato systemd e sottostato",
         "salute applicativa", "installazione", "PID", "ambito",
         "server LLM locale per fast.micro, fast.procedural, fast.fidelity, middle, wise e creative",
         "traduttore i18n automatico con il ruolo wise"),
        ("services grouped by function", "systemd state and sub-state",
         "application health", "installation", "PID", "scope",
         "local LLM server for fast.micro, fast.procedural, fast.fidelity, middle, wise, and creative",
         "automatic i18n translator with the wise role"),
        ("avvia", "arresta", "riavvia", "aggiornamento automatico ogni 15 secondi"),
        ("start", "stop", "restart", "automatic refresh every 15 seconds"),
        probe_refs=("service_health",),
    ),
    UiSurfaceSpec(
        "lre", "system", "/admin/lre", "LRE", "LRE",
        "Console dei lavori lunghi affidati a LRE, con avanzamento, limiti, eventi e artefatti.",
        "Console for long-running work submitted to LRE, including progress, limits, events, and artifacts.",
        visible_it=(
            "elenco dei lavori visibili al proprietario, con stato, avanzamento e ultimo aggiornamento",
            "dettaglio del lavoro e della revisione selezionata",
            "digest di piano e inventario",
            "budget ammessi per unità, tentativi, tempo, byte, token, artefatti e concorrenza",
            "fasi con executor, risorse, tempo massimo e conteggi",
            "timeline persistente, unità che richiedono attenzione e artefatti scaricabili",
        ),
        visible_en=(
            "owner-visible workload list with state, progress, and last update",
            "selected workload and revision detail",
            "plan and inventory digests",
            "admitted budgets for units, attempts, time, bytes, tokens, artifacts, and concurrency",
            "stages with executor, resources, timeout, and counters",
            "persistent timeline, units requiring attention, and downloadable artifacts",
        ),
        controls_it=(
            "apri un lavoro", "carica altre righe o unità",
            "metti in pausa", "riprendi", "annulla",
            "ritenta dopo una decisione", "scarica un artefatto",
        ),
        controls_en=(
            "open a workload", "load more rows or units", "pause", "resume",
            "cancel", "retry after a decision", "download an artifact",
        ),
        procedure_it=(
            "Nella chat web apri Settings > Sistema > LRE. La console serve a osservare e governare i lavori; non abilita LRE e non crea un lavoro.",
            "Nel riquadro sinistro individua il lavoro tramite stato, avanzamento e data, quindi premi Apri.",
            "Nel riquadro destro verifica prima stato e denominatore; consulta poi digest, budget e fasi per capire quale piano è in esecuzione e con quali limiti.",
            "Usa la timeline per seguire i cambiamenti. Carica unità in errore o in attesa di attenzione soltanto quando devi diagnosticare un arresto.",
            "Pausa, ripresa, annullamento e ritentativo compaiono soltanto negli stati che li ammettono. La pagina può essere chiusa: il worker continua in modo indipendente.",
            "Quando il lavoro termina, verifica nome, dimensione, digest e stato di convalida dell'artefatto prima di scaricarlo.",
        ),
        procedure_en=(
            "In the web chat, open Settings > System > LRE. The console observes and controls workloads; it neither enables LRE nor creates a workload.",
            "In the left panel, identify a workload by state, progress, and date, then press Open.",
            "In the right panel, first verify state and denominator; then inspect digests, budgets, and stages to understand which plan is running and under which limits.",
            "Use the timeline to follow changes. Load failed or attention-required units only when diagnosing halted progress.",
            "Pause, resume, cancel, and retry appear only in states that allow them. You may close the page: the worker continues independently.",
            "When the workload finishes, verify the artifact name, size, digest, and validation state before downloading it.",
        ),
        stop_it=(
            "se l'elenco è vuoto, non esistono lavori LRE visibili a questo proprietario",
            "se la console risulta indisponibile, controlla worker e interruttore in Settings > Sistema > Servizi",
            "se compare Richiede attenzione, leggi categoria ed eventi prima di autorizzare un nuovo tentativo",
        ),
        stop_en=(
            "an empty list means that this owner has no visible LRE workloads",
            "if the console is unavailable, inspect the worker and instance switch under Settings > System > Services",
            "when Needs attention appears, read the category and events before authorising another attempt",
        ),
        template="durable_workloads.html",
    ),
    UiSurfaceSpec(
        "safety", "system", "/admin/safety", "Safety", "Safety",
        "Firme che governano autorizzazione e conferma degli executor.",
        "Signatures governing executor authorization and confirmation.",
        ("spiegazione di firma, tipo, severità e origine", "firma", "tipo",
         "severità", "origine", "usi", "data di creazione"),
        ("explanation of signature, kind, severity, and origin", "signature",
         "kind", "severity", "origin", "uses", "creation date"),
        ("filtra tutte, whitelist, graylist, blacklist o forbidden",),
        ("filter all, whitelist, graylist, blacklist, or forbidden",),
        structure_sha="26712fcedda1",
    ),
    UiSurfaceSpec(
        "users", "system", "/admin/users", "Utenti", "Users",
        "Utenti, ruoli e canali associati.",
        "Users, roles, and associated channels.",
        ("elenco con nome, nome visualizzato, email, ruolo, autonomia, canali e data",
         "dettaglio con anagrafica e note", "canali associati e stato",
         "preferenze generali e di navigazione web", "dispositivi posseduti"),
        ("list with name, display name, email, role, autonomy, channels, and date",
         "detail page with profile data and notes", "paired channels and status",
         "general and web-browsing preferences", "owned devices"),
        ("crea un guest", "modifica e salva l'utente", "emetti token o scollega un canale",
         "salva le preferenze", "elimina l'utente"),
        ("create a guest", "edit and save the user", "issue a channel token or disconnect it",
         "save preferences", "delete the user"),
        procedure_it=(
            "Nella chat web apri Settings > Sistema > Utenti.",
            "Nel riquadro in cima compila nome (solo lettere minuscole, cifre e "
            "trattino basso, obbligatorio), nome visualizzato, email, ruolo "
            "(guest per un ospite, host per un secondo proprietario) e "
            "autonomia (read_only guarda soltanto, restricted chiede conferma "
            "per le operazioni che modificano, full procede da solo).",
            "Premi crea: la persona compare nell'elenco ma non ha ancora "
            "alcun canale collegato, quindi non puo' ancora entrare.",
            "Apri il suo nome nell'elenco ed emetti dal dettaglio un token di "
            "collegamento per il canale che usera'. Vale un'ora ed e' monouso.",
            "Per la chat web consegna alla persona l'indirizzo completo "
            "generato; per Telegram consegna il token, che la persona invia al "
            "bot come /start seguito dal token.",
            "Trasferisci indirizzo o token solo per una via fidata: chi li ha "
            "entra come quella persona. Se scadono prima dell'uso, torna sul "
            "dettaglio e generane altri.",
        ),
        procedure_en=(
            "In the web chat open Settings > System > Users.",
            "In the box at the top fill in name (lowercase letters, digits and "
            "underscore only, required), display name, email, role (guest for "
            "a guest, host for a second owner) and autonomy (read_only only "
            "looks, restricted asks for confirmation on operations that change "
            "something, full goes ahead alone).",
            "Press create: the person appears in the list but has no channel "
            "connected yet, so they cannot get in.",
            "Open their name in the list and issue, from the detail page, a "
            "connection token for the channel they will use. It is valid for "
            "one hour and single-use.",
            "For the web chat hand the person the complete address that was "
            "generated; for Telegram hand them the token, which the person "
            "sends to the bot as /start followed by the token.",
            "Pass the address or the token only through a trusted way: whoever "
            "holds them gets in as that person. If they expire before use, go "
            "back to the detail page and generate new ones.",
        ),
        structure_sha="453642d23605",
    ),
    UiSurfaceSpec(
        "devices", "system", "/admin/devices", "Dispositivi", "Devices",
        "Dispositivi remoti associati che eseguono executor.",
        "Paired remote devices that run executors.",
        ("nome", "identificativo", "proprietario", "sistema operativo",
         "versione client", "impronta", "ultimo heartbeat", "stato"),
        ("name", "identifier", "owner", "operating system", "client version",
         "fingerprint", "last heartbeat", "state"),
        ("installa il client su questo PC",
         "genera un link di accoppiamento monouso per un altro PC",
         "genera un token temporaneo monouso valido 10 minuti",
         "revoca un dispositivo"),
        ("install the client on this PC",
         "generate a one-time pairing link for another PC",
         "generate a temporary one-time token valid for 10 minutes",
         "revoke a device"),
        knowledge_audience="user",
        structure_sha="87895346b83f",
    ),
)


_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_TEMPLATE_TH = re.compile(r"<th[^>]*>([^<]*)</th>")


def template_structure(surface: UiSurfaceSpec) -> tuple[str, ...] | None:
    """Extract the literal header row of the surface's template.

    Returns None when the template is missing or exposes no literal table
    header (dynamic pages); the drift guard then does not apply.
    """

    name = surface.template or f"{surface.key.replace('-', '_')}.html"
    path = _TEMPLATE_DIR / name
    if not path.is_file():
        return None
    texts = tuple(
        text.strip() for text in _TEMPLATE_TH.findall(
            path.read_text(encoding="utf-8"))
        if text.strip()
    )
    return texts or None


def structure_fingerprint(surface: UiSurfaceSpec) -> str | None:
    structure = template_structure(surface)
    if structure is None:
        return None
    digest = hashlib.sha256(" | ".join(structure).encode("utf-8"))
    return digest.hexdigest()[:12]


def catalog() -> tuple[UiSurfaceSpec, ...]:
    return SURFACES


def by_key(key: str) -> UiSurfaceSpec:
    return next(surface for surface in SURFACES if surface.key == key)


def settings_navigation(lang: str = "it") -> tuple[dict, ...]:
    """Navigation view consumed directly by the Settings base template."""

    groups = [{
        "key": "root", "label": "",
        "items": tuple(surface for surface in SURFACES if not surface.section),
    }]
    for key, labels in _SECTIONS.items():
        groups.append({
            "key": key,
            "label": labels[0 if lang == "it" else 1],
            "items": tuple(surface for surface in SURFACES
                           if surface.section == key),
        })
    return tuple(groups)


def validate_surfaces() -> tuple[str, ...]:
    """Check registry uniqueness and parity with the admitted GET routes."""

    from http_routes_admin import ROUTES

    findings: list[str] = []
    keys = [surface.key for surface in SURFACES]
    routes = [surface.route for surface in SURFACES]
    if len(keys) != len(set(keys)):
        findings.append("duplicate_key")
    if len(routes) != len(set(routes)):
        findings.append("duplicate_route")
    admitted = {route for method, route, _handler in ROUTES if method == "GET"}
    try:
        from tutor.probes import registered_probe_ids
        admitted_probes = registered_probe_ids()
    except ImportError:
        admitted_probes = frozenset()
    for surface in SURFACES:
        if surface.route not in admitted:
            findings.append(f"missing_route:{surface.key}:{surface.route}")
        if surface.audience != "instance_admin":
            findings.append(f"unsafe_audience:{surface.key}")
        if surface.knowledge_audience not in ("user", "instance_admin"):
            findings.append(f"invalid_knowledge_audience:{surface.key}")
        computed = structure_fingerprint(surface)
        if computed is None:
            if surface.structure_sha:
                findings.append(f"ui_drift:{surface.key}:template_unreadable")
        elif not surface.structure_sha:
            findings.append(f"missing_structure_sha:{surface.key}")
        elif computed != surface.structure_sha:
            findings.append(f"ui_drift:{surface.key}")
        if not surface.summary_it or not surface.summary_en:
            findings.append(f"missing_summary:{surface.key}")
        if not surface.visible_it or not surface.visible_en:
            findings.append(f"missing_visible_content:{surface.key}")
        for probe_id in surface.probe_refs:
            if probe_id not in admitted_probes:
                findings.append(f"unknown_probe:{surface.key}:{probe_id}")
    return tuple(findings)


if __name__ == "__main__":
    # Refresh helper for the drift guard: after editing a Settings template,
    # run this, review the printed columns, update the registry entry and its
    # `structure_sha` accordingly.
    for _surface in SURFACES:
        _fingerprint = structure_fingerprint(_surface)
        _structure = template_structure(_surface) or ()
        state = "no-literal-header" if _fingerprint is None else _fingerprint
        marker = ""
        if _fingerprint is not None and _fingerprint != _surface.structure_sha:
            marker = "  <-- AGGIORNARE structure_sha"
        print(f"{_surface.key:16s} {state:18s}{marker}")
        if _structure:
            print(f"  colonne: {', '.join(_structure)}")
