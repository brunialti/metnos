#!/usr/bin/env python3
"""Generate the bilingual, user-facing reference for every Metnos domain."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import html
import importlib.util
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
BUILTIN_CONTRACTS = RUNTIME / "builtin_executor_contracts"
CATALOG_GENERATOR = ROOT / "scripts" / "generate_executor_catalog.py"
OUTPUTS = {
    "it": ROOT / "docs" / "it" / "domains.html",
    "en": ROOT / "docs" / "en" / "domains.html",
}

if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from naming_grammar import parse_name  # noqa: E402
from vocab import OBJECTS  # noqa: E402


@dataclass(frozen=True, slots=True)
class DomainCopy:
    group: str
    label_it: str
    label_en: str
    summary_it: str
    summary_en: str
    boundary_it: str
    boundary_en: str
    examples_it: tuple[str, ...]
    examples_en: tuple[str, ...]


def _copy(group: str, labels: tuple[str, str], summaries: tuple[str, str],
          boundaries: tuple[str, str], examples_it: tuple[str, ...],
          examples_en: tuple[str, ...]) -> DomainCopy:
    return DomainCopy(
        group=group,
        label_it=labels[0], label_en=labels[1],
        summary_it=summaries[0], summary_en=summaries[1],
        boundary_it=boundaries[0], boundary_en=boundaries[1],
        examples_it=examples_it, examples_en=examples_en,
    )


# Human explanations are deliberately curated; membership and executor names
# below are generated from canonical runtime objects and signed manifests.
DOMAIN_COPY = {
    "files": _copy("content", ("File", "Files"),
        ("Trova, legge, crea, converte, sposta, condivide e analizza file locali o forniti da un servizio collegato. Comprende documenti, fogli, PDF, OCR, archivi, hash e linee di codice.",
         "Finds, reads, creates, converts, moves, shares, and analyses local files or files supplied by a provider. This includes documents, spreadsheets, PDFs, OCR, archives, hashes, and lines of code."),
        ("Il dominio riguarda il contenitore file. Per il significato visuale di una foto usa Immagini; per una pagina pubblica usa URL.",
         "This domain concerns the file container. Use Images for the visual meaning of a photo and URLs for a public web page."),
        ("Trova tutti i PDF modificati questa settimana in Progetti e riassumi quelli che parlano di Atlas.", "Trova i file immagine con contenuto identico nella cartella Immagini del server e dimmi quanto spazio occupano le copie ridondanti."),
        ("Find every PDF changed this week in Projects and summarise the ones about Atlas.", "Find image files with identical content in the server's Pictures folder and tell me how much space the redundant copies use.")),
    "dirs": _copy("content", ("Cartelle", "Folders"),
        ("Elenca, cerca, crea ed elimina directory, anche su un dispositivo remoto quando il piano lo richiede.",
         "Lists, finds, creates, and deletes directories, including on a remote device when the plan requires it."),
        ("Una cartella organizza file; non rappresenta il loro contenuto. Le cancellazioni non sicure richiedono i normali vagli.",
         "A folder organises files; it does not represent their content. Unsafe deletion remains subject to the normal gates."),
        ("Mostrami le sottocartelle di Foto ordinate per dimensione.", "Crea la cartella Clienti/2026 sul portatile dello studio."),
        ("Show the subfolders under Photos ordered by size.", "Create the Clients/2026 folder on the office laptop.")),
    "packages": _copy("content", ("Programmi installati", "Installed programs"),
        ("Verifica se un comando o programma eseguibile è disponibile nel PATH del sistema scelto.",
         "Checks whether an executable command or program is available in the selected system PATH."),
        ("Rileva disponibilità e posizione; non installa automaticamente pacchetti non richiesti.",
         "It detects availability and location; it does not automatically install unrequested packages."),
        ("Verifica se ffmpeg è installato su questo server.", "Dove si trova python3 sul PC Windows accoppiato?"),
        ("Check whether ffmpeg is installed on this server.", "Where is python3 on the paired Windows PC?")),
    "texts": _copy("content", ("Testi", "Texts"),
        ("Filtra e trasforma contenuto testuale già disponibile, con granularità come righe, paragrafi o segmenti.",
         "Filters and transforms text already available, at granularities such as lines, paragraphs, or segments."),
        ("Lavora sul testo in memoria; per estrarlo prima da un file, una mail o una pagina serve il relativo dominio sorgente.",
         "It works on in-memory text; extracting it first from a file, message, or page requires the corresponding source domain."),
        ("Nel log che ti ho dato conserva solo le righe che contengono timeout o retry.", "Leggi il documento e mostrami soltanto i paragrafi con una data."),
        ("From the log I gave you, keep only lines containing timeout or retry.", "Read the document and show only the paragraphs containing a date.")),
    "numbers": _copy("content", ("Numeri", "Numbers"),
        ("Calcola statistiche descrittive su valori numerici: conteggio, minimo, massimo, media e altre sintesi ammesse.",
         "Computes descriptive statistics over numeric values: count, minimum, maximum, mean, and other admitted summaries."),
        ("Riceve numeri espliciti o prodotti da un passo precedente; non sostituisce un foglio di calcolo persistente.",
         "It receives explicit numbers or values produced by a previous step; it is not a persistent spreadsheet."),
        ("Calcola media, minimo e massimo di 12, 18, 21, 21 e 34.", "Trova le fatture del mese e dimmi la spesa media."),
        ("Compute the mean, minimum, and maximum of 12, 18, 21, 21, and 34.", "Find this month's invoices and tell me the average spend.")),
    "entries": _copy("composition", ("Risultati in memoria", "In-memory results"),
        ("Ordina, filtra, raggruppa, classifica, confronta, estrae e riassume le righe strutturate prodotte durante lo stesso turno.",
         "Sorts, filters, groups, classifies, compares, extracts, and summarises structured rows produced during the same turn."),
        ("È un dominio di composizione interno: l'utente descrive il risultato finale, non deve conoscere il nome entries.",
         "This is an internal composition domain: people describe the desired result and do not need to know the name entries."),
        ("Trova le fatture di luglio, raggruppale per fornitore e disponile in ordine decrescente di importo.", "Cerca le issue aperte, classificale per urgenza e mostrami solo le critiche."),
        ("Find July's invoices, group them by supplier, and order them from most expensive.", "Find open issues, classify them by urgency, and show only critical ones.")),
    "lists": _copy("composition", ("Confronto tra liste", "List comparison"),
        ("Confronta due insiemi di risultati per trovare elementi comuni, nuovi o scomparsi, usando chiavi esplicite.",
         "Compares two result sets to find common, new, or missing items using explicit keys."),
        ("Le liste devono provenire dallo stesso piano o da una sorgente identificabile; il confronto non inventa corrispondenze.",
         "Lists must come from the same plan or an identifiable source; comparison does not invent matches."),
        ("Confronta i file di queste due cartelle e mostrami quelli presenti solo nella seconda.", "Quali issue sono nuove rispetto all'elenco salvato ieri?"),
        ("Compare the files in these two folders and show those present only in the second.", "Which issues are new compared with yesterday's saved list?")),
    "messages": _copy("communication", ("Messaggi e posta", "Messages and mail"),
        ("Cerca, legge, invia, risponde, etichetta, sposta o elimina messaggi tramite account configurati, inclusi IMAP/SMTP, Gmail e canali ammessi.",
         "Finds, reads, sends, replies to, labels, moves, or deletes messages through configured accounts, including IMAP/SMTP, Gmail, and admitted channels."),
        ("L'account è selezionabile quando ce n'è più di uno. Invii, risposte ed eliminazioni mantengono consenso e tracciabilità.",
         "The account can be selected when several exist. Sending, replying, and deleting retain consent and traceability."),
        ("Con l'account lavoro cerca le mail di Marta degli ultimi sette giorni con allegati PDF.", "Rispondi nello stesso thread che la riunione è confermata, ma mostrami prima il testo."),
        ("Using my work account, find Marta's messages from the last seven days with PDF attachments.", "Reply in the same thread that the meeting is confirmed, but show me the text first.")),
    "contacts": _copy("communication", ("Contatti", "Contacts"),
        ("Cerca e legge voci di rubrica con nome, email e telefono, locali o fornite da un account collegato.",
         "Finds and reads address-book records containing names, email addresses, and phone numbers, locally or from a connected account."),
        ("Un contatto è una voce di rubrica. Il dominio Persone rappresenta invece identità locali e riconoscimento nelle foto.",
         "A contact is an address-book record. Persons instead represents local identities and recognition in photos."),
        ("Trova l'indirizzo email di Laura Bianchi nei miei contatti.", "Mostrami il numero di telefono del contatto Acme assistenza."),
        ("Find Laura Bianchi's email address in my contacts.", "Show the phone number for the Acme Support contact.")),
    "events": _copy("communication", ("Eventi", "Events"),
        ("Legge, crea ed elimina appuntamenti e trova intervalli liberi nei calendari accessibili.",
         "Reads, creates, and deletes appointments and finds free intervals in accessible calendars."),
        ("Un evento è un appuntamento. Calendari gestisce i contenitori; Attività programmate esegue richieste nel futuro.",
         "An event is an appointment. Calendars manages containers; Scheduled tasks executes requests in the future."),
        ("Trova un'ora libera martedì pomeriggio tra il mio calendario e quello di Anna.", "Crea una riunione di 45 minuti venerdì alle 10 con Luca."),
        ("Find a free hour on Tuesday afternoon across my calendar and Anna's.", "Create a 45-minute meeting with Luca on Friday at 10.")),
    "calendars": _copy("communication", ("Calendari", "Calendars"),
        ("Crea e rimuove calendari-contenitore, separati dagli appuntamenti che contengono.",
         "Creates and removes calendar containers, distinct from the appointments they contain."),
        ("Eliminare un calendario può eliminare anche i suoi eventi ed è quindi un'operazione distruttiva sottoposta a vaglio.",
         "Deleting a calendar may also delete its events and is therefore a destructive gated operation."),
        ("Crea un nuovo calendario Google chiamato Turni assistenza.", "Elimina il calendario di prova Test 2025 e tutto il suo contenuto."),
        ("Create a new Google calendar named Support shifts.", "Delete the Test 2025 trial calendar and all its contents.")),
    "tasks": _copy("communication", ("Attività programmate", "Scheduled tasks"),
        ("Crea, consulta, modifica ed elimina promemoria o richieste ricorrenti; conserva anche la cronologia delle esecuzioni. Le skill possono aggiungere attività remote, come i workflow di GitHub.",
         "Creates, reads, changes, and deletes reminders or recurring requests and retains execution history. Skills may add remote tasks such as GitHub workflows."),
        ("Un'attività programmata esegue una richiesta a un'ora stabilita o al verificarsi di un evento; non è un appuntamento di calendario.",
         "A task executes a request at a time or trigger; it is not a calendar appointment."),
        ("Ogni lunedì alle 8 leggi le mail non lette dell'account lavoro e mandami un riepilogo su Telegram.", "Mostrami gli ultimi esiti dell'attività Backup foto e gli eventuali errori."),
        ("Every Monday at 8, read unread mail from my work account and send me a summary on Telegram.", "Show the latest outcomes of the Photo backup task and any errors.")),
    "urls": _copy("world", ("Web e URL", "Web and URLs"),
        ("Cerca sul web, recupera URL noti e legge pagine HTML o PDF pubblici, estraendo testo e metadati.",
         "Searches the web, fetches known URLs, and reads public HTML pages or PDFs, extracting text and metadata."),
        ("Una pagina pubblica senza sessione appartiene a URL. Per un sito autenticato e interattivo usa Siti.",
         "A public page without session state belongs to URLs. Use Sites for an authenticated, interactive website."),
        ("Cerca sul web le linee guida europee più recenti sul passaporto digitale dei prodotti e cita le fonti.", "Leggi questo PDF online e riassumi requisiti, scadenze e destinatari."),
        ("Search the web for the latest European guidance on digital product passports and cite the sources.", "Read this online PDF and summarise requirements, deadlines, and intended audiences.")),
    "sites": _copy("world", ("Siti con sessione", "Stateful sites"),
        ("Apre una sessione browser persistente, raggiunge il login con credenziali protette, legge lo stato della pagina e compie azioni web a mandato ristretto.",
         "Opens a persistent browser session, reaches login using protected credentials, reads page state, and performs narrow-mandate web actions."),
        ("Le credenziali sono iniettate dal broker e non mostrate al modello. Azioni sensibili richiedono conferma.",
         "Credentials are injected by the broker and never shown to the model. Sensitive actions require confirmation."),
        ("Accedi al portale del corriere e dimmi dove si trova la spedizione 12345.", "Apri il sito della banca, scarica l'estratto conto di giugno e fermati prima di qualsiasi disposizione."),
        ("Sign in to the courier portal and tell me where shipment 12345 is.", "Open the bank website, download June's statement, and stop before making any transaction.")),
    "places": _copy("world", ("Luoghi", "Places"),
        ("Cerca luoghi reali da testo o coordinate e traduce coordinate GPS in nomi geografici.",
         "Finds real-world places from text or coordinates and converts GPS coordinates into geographic names."),
        ("La posizione corrente dell'utente proviene da una funzione di sistema separata; Luoghi interpreta o cerca destinazioni.",
         "The user's current location is a separate system utility; Places interprets or searches destinations."),
        ("Cerca una farmacia vicino a Piazza Maggiore a Bologna.", "A quale luogo corrispondono le coordinate 45.4642, 9.1900?"),
        ("Find a pharmacy near Piazza Maggiore in Bologna.", "Which place corresponds to coordinates 45.4642, 9.1900?")),
    "images": _copy("world", ("Immagini e fotografie", "Images and photographs"),
        ("Descrive immagini, crea e consulta indici locali, cerca per contenuto, persona o somiglianza, cerca immagini sul web e usa le foto Google create tramite Metnos.",
         "Describes images, builds and queries local indexes, searches by content, person, or similarity, searches web images, and accesses Google photos created through Metnos."),
        ("Le operazioni generiche per percorso restano nel dominio File. Google Photos espone soltanto gli elementi ammessi dalla relativa API e dal suo mandato.",
         "Generic path operations remain in Files. Google Photos exposes only items allowed by its API and mandate."),
        ("Nell'archivio foto trova le immagini di Carlo al mare al tramonto.", "Cerca sul web una fotografia riutilizzabile del Duomo di Milano vista dall'alto."),
        ("In the photo archive, find images of Carlo by the sea at sunset.", "Search the web for a reusable aerial photograph of Milan Cathedral.")),
    "persons": _copy("world", ("Persone e identità locali", "People and local identities"),
        ("Gestisce un registro nominale locale e collega volti riconosciuti alle persone nelle fotografie; può anche leggere il profilo dell'attore corrente.",
         "Manages a local named-person registry and links recognised faces to people in photographs; it can also read the current actor's profile."),
        ("Non è una rubrica: email e telefoni appartengono a Contatti. Dati biometrici ed eliminazioni seguono controlli dedicati.",
         "It is not an address book: email addresses and phone numbers belong to Contacts. Biometric data and deletion have dedicated controls."),
        ("Registra questa foto come volto di Francesca Riva.", "Trova nell'archivio tutte le foto in cui compare Francesca."),
        ("Register this photo as Francesca Riva's face.", "Find every archive photo containing Francesca.")),
    "issues": _copy("collaboration", ("Issue", "Issues"),
        ("Attraverso la skill di un fornitore, cerca, legge, crea, aggiorna o chiude segnalazioni di progetto con stato, etichette e assegnatari.",
         "Through a provider skill, finds, reads, creates, updates, or closes project issues with state, labels, and assignees."),
        ("Il fornitore qui documentato è GitHub e richiede credenziali configurate; le capacità effettive dipendono dalle skill ammesse nell'istanza.",
         "The currently documented provider is GitHub and requires configured credentials; effective capabilities depend on the skills admitted by the instance."),
        ("Nel repository metnos trova le issue aperte con etichetta bug create nell'ultima settimana.", "Crea una issue nel repository acme/app con questo titolo e assegnala a Laura."),
        ("In the metnos repository, find open issues labelled bug created in the last week.", "Create an issue in acme/app with this title and assign it to Laura.")),
    "pulls": _copy("collaboration", ("Pull request", "Pull requests"),
        ("Cerca e legge pull request, differenze nel codice e stato della revisione; aggiorna o integra una richiesta quando il fornitore e il mandato lo consentono.",
         "Finds and reads pull requests, diffs, and review state; updates or merges a request when the provider and mandate allow it."),
        ("È distinto dalle issue perché comprende modifiche al codice, revisione e integrazione. Le operazioni che producono effetti sono soggette a vaglio.",
         "It is distinct from issues because it includes code changes, reviews, and merge. Mutating operations are gated."),
        ("Mostrami le pull request aperte di acme/app che modificano la cartella security.", "Leggi il diff della PR 42, riassumi i rischi e fermati prima del merge."),
        ("Show open pull requests in acme/app that modify the security folder.", "Read PR 42's diff, summarise the risks, and stop before merge.")),
    "skills": _copy("collaboration", ("Skill", "Skills"),
        ("Elenca e gestisce estensioni installate che possono aggiungere executor, fornitori e conoscenza specializzata al catalogo locale.",
         "Lists and manages installed extensions that can add executors, providers, and specialised knowledge to the local catalog."),
        ("Una skill viene ammessa attraverso verifica e policy; installarla non aggira vocabolario, sandbox o consenso.",
         "A skill is admitted through verification and policy; installing one does not bypass vocabulary, sandboxing, or consent."),
        ("Quali skill sono installate e quali risultano abilitate?", "Abilita la skill GitHub già installata e mostrami quali capacità aggiunge."),
        ("Which skills are installed and which are enabled?", "Enable the already installed GitHub skill and show which capabilities it adds.")),
    "preferences": _copy("governance", ("Preferenze personali", "Personal preferences"),
        ("Elenca, imposta e rimuove le preferenze con cui l'utente decide come Metnos si comporta con lui: lunghezza e tono della risposta, unità di misura, lingua, modalità del browser.",
         "Lists, sets, and removes the preferences with which a user decides how Metnos behaves towards them: reply length and tone, units, language, browser mode."),
        ("Chiavi e valori formano un vocabolario chiuso, e ciascuno vede soltanto le proprie preferenze. Una preferenza registrata ma non ancora applicata da nessun percorso di risposta viene dichiarata come tale.",
         "Keys and values form a closed vocabulary, and each person sees only their own preferences. A preference that is recorded but not yet applied by any answer path is declared as such."),
        ("Che preferenze ho impostato?", "D'ora in poi rispondimi in modo sintetico."),
        ("Which preferences have I set?", "From now on, answer me concisely.")),
    "processes": _copy("governance", ("Processi e salute del sistema", "Processes and system health"),
        ("Ottiene uno snapshot dei processi e dei segnali di salute del sistema locale o di un dispositivo eleggibile.",
         "Obtains a snapshot of processes and system-health signals on the local system or an eligible device."),
        ("È osservazione, non amministrazione shell arbitraria: le azioni disponibili restano quelle degli executor ammessi.",
         "This is observation, not arbitrary shell administration: available actions remain those of admitted executors."),
        ("Quali processi stanno usando più memoria su questo server?", "Controlla la salute del PC dello studio e mostrami eventuali processi anomali."),
        ("Which processes are using the most memory on this server?", "Check the office PC's health and show any anomalous processes.")),
    "signatures": _copy("governance", ("Firme di sicurezza", "Security signatures"),
        ("Calcola, consulta e cura firme canoniche usate per classificare comandi e azioni di sistema in policy ammesse, dubbie o vietate.",
         "Computes, reads, and curates canonical signatures used to classify system commands and actions into allowed, uncertain, or forbidden policy sets."),
        ("È un dominio amministrativo di sicurezza, non la firma crittografica dei documenti dell'utente.",
         "This is an administrative security domain, not cryptographic signing of user documents."),
        ("Mostrami le signature attualmente in graylist e la loro classificazione.", "Calcola la signature canonica del comando proposto senza eseguirlo."),
        ("Show the signatures currently on the greylist and their classification.", "Compute the canonical signature of the proposed command without executing it.")),
    "proposals": _copy("governance", ("Proposte di evoluzione", "Evolution proposals"),
        ("Espone proposte locali in attesa di revisione. Il processo notturno corrente propone deduplicazioni; generalizzazioni e specializzazioni restano consultabili soltanto nei registri storici.",
         "Exposes local proposals awaiting review. The current nightly producer proposes deduplication; generalisation and specialisation remain readable only in historical records."),
        ("Consultare una proposta non la approva. Accettazione, rifiuto e ripristino avvengono nelle pagine amministrative previste.",
         "Reading a proposal does not approve it. Acceptance, rejection, and rollback occur through the designated administrative surfaces."),
        ("Mostrami le proposte di deduplicazione ancora da revisionare.", "Spiegami questa proposta, i rischi e come raggiungo la pagina per approvarla."),
        ("Show the deduplication proposals still awaiting review.", "Explain this proposal, its risks, and how to reach the page where I can approve it.")),
    "inputs": _copy("governance", ("Richiesta di dati strutturati", "Structured input"),
        ("Raccoglie in modo tipizzato informazioni mancanti tramite dialoghi e moduli, inclusi scelte, date, file e campi sensibili.",
         "Collects missing information in typed dialogs and forms, including choices, dates, files, and sensitive fields."),
        ("È una capacità di interazione usata dentro un'operazione; di norma l'utente non la invoca per nome.",
         "It is an interaction capability used inside an operation; people normally do not invoke it by name."),
        ("Prepara una nuova casella di posta e chiedimi in un modulo server, porta e nome utente che mancano.", "Crea l'evento; se non sai quale calendario usare, fammi scegliere tra quelli disponibili."),
        ("Prepare a new mailbox and ask me for the missing server, port, and username in a form.", "Create the event; if you do not know which calendar to use, let me choose from those available.")),
    "approval": _copy("governance", ("Approvazione umana", "Human approval"),
        ("Presenta una scelta Approva/Disapprova e prosegue sul ramo consentito, preservando anteprima, attore e tracciabilità.",
         "Presents an Approve/Reject choice and continues along the allowed branch while preserving preview, actor, and traceability."),
        ("È un vaglio, non una scorciatoia: l'approvazione non amplia l'autorità dell'executor e non rende lecita un'azione vietata.",
         "It is a gate, not a shortcut: approval does not expand executor authority or make a forbidden action permissible."),
        ("Prepara la cancellazione dei file duplicati, mostrami l'elenco e chiedimi approvazione prima di procedere.", "Se il preventivo supera 500 euro, fermati e chiedimi conferma."),
        ("Prepare deletion of duplicate files, show me the list, and ask for approval before proceeding.", "If the quote exceeds 500 euros, stop and ask for my confirmation.")),
    "credentials": _copy("governance", ("Credenziali", "Credentials"),
        ("Configura, elenca attraverso i metadati ed elimina associazioni cifrate per caselle di posta, API, servizi cloud e siti autenticati.",
         "Configures, lists by metadata, and deletes encrypted bindings for mailboxes, APIs, cloud services, and authenticated sites."),
        ("I segreti in chiaro non tornano al pianificatore o al Tutor. Più account dello stesso tipo restano selezionabili tramite associazioni distinte.",
         "Clear-text secrets never return to the planner or Tutor. Multiple accounts of the same type remain selectable through distinct bindings."),
        ("Configura una seconda casella IMAP chiamata personale e chiedimi le credenziali in modo protetto.", "Quali account email e servizi cloud sono configurati, senza mostrare alcun segreto?"),
        ("Configure a second IMAP mailbox named personal and ask me for credentials securely.", "Which email accounts and cloud providers are configured, without showing any secret?")),
    "_system": _copy("system", ("Funzioni trasversali", "Cross-domain utilities"),
        ("Fornisce data e ora affidabili, ultima posizione osservata, annullamento dell'ultimo turno reversibile e consultazione esperta quando ammessa.",
         "Provides reliable date and time, the last observed location, undo for the latest reversible turn, and expert consultation when admitted."),
        ("Sono funzioni trasversali, non nuovi domini di dati. L'annullamento opera soltanto sugli effetti per i quali è stata registrata un'operazione inversa.",
         "These are cross-domain primitives, not new data domains. Undo operates only on effects with a recorded compensation."),
        ("Che ore sono adesso a Tokyo?", "Annulla le operazioni reversibili del mio ultimo turno."),
        ("What time is it in Tokyo right now?", "Undo the reversible operations from my last turn.")),
}


GROUPS = {
    "it": {
        "content": ("Contenuti e calcolo", "File, strutture e trasformazioni locali."),
        "composition": ("Composizione dei risultati", "Passi interni che collegano più capacità in una sola richiesta."),
        "communication": ("Comunicazione e tempo", "Messaggi, rubriche, calendari e attività future."),
        "world": ("Web, luoghi e media", "Informazioni pubbliche, sessioni web e archivi visuali."),
        "collaboration": ("Collaborazione e fornitori", "Estensioni e oggetti di lavoro condiviso."),
        "governance": ("Controllo e amministrazione", "Osservabilità, consenso, sicurezza e configurazione."),
        "system": ("Funzioni trasversali", "Funzioni di supporto comuni a più domini."),
    },
    "en": {
        "content": ("Content and computation", "Files, structures, and local transformations."),
        "composition": ("Result composition", "Internal steps joining several capabilities in one request."),
        "communication": ("Communication and time", "Messages, address books, calendars, and future activities."),
        "world": ("Web, places, and media", "Public information, web sessions, and visual archives."),
        "collaboration": ("Collaboration and providers", "Extensions and shared work objects."),
        "governance": ("Control and administration", "Observability, consent, security, and configuration."),
        "system": ("Cross-domain utilities", "Primitives assisting several domains."),
    },
}


TEXT = {
    "it": {
        "title": "Riferimento dei domini",
        "domain_count": "domini canonici",
        "description": "Guida completa ai domini operativi di Metnos, con confini, disponibilità ed esempi di richieste in linguaggio naturale.",
        "eyebrow": "Riferimento operativo · generato dal catalogo canonico",
        "lead": "Che cosa puoi chiedere a Metnos, dominio per dominio",
        "intro": "Questa pagina descrive tutti i domini canonici del vocabolario Metnos. Ogni esempio è una frase che puoi usare direttamente in chat: non occorre conoscere executor, argomenti o sintassi tecniche.",
        "contract": "La struttura del riferimento deriva dai {domain_total} oggetti canonici del runtime; nomi e conteggi delle operazioni distribuite derivano dai manifest firmati e dai contratti integrati. Le skill installate possono aggiungere fornitori e operazioni. Per sapere che cosa è disponibile adesso sulla tua istanza, chiedi al Tutor.",
        "ask": "Chiedi a Metnos con una richiesta come quella di questo esempio:",
        "boundary": "Confine",
        "operations": "Operazioni distribuite",
        "extension": "Questo dominio è disponibile quando una skill o un fornitore ammesso ne installa le operazioni.",
        "available": "operazioni documentate",
        "available_one": "operazione documentata",
        "nav_home": "Metnos",
        "nav_interface": "L'interfaccia",
        "nav_manual": "Guida all'architettura",
        "nav_catalog": "Catalogo tecnico",
        "nav_tutor": "Come funziona il Tutor",
        "provider_title": "Fornitori e disponibilità effettiva",
        "provider_text": "I domini sono stabili; i fornitori costituiscono un asse separato. Una singola istanza può usare file e dispositivi locali, caselle IMAP/SMTP, web pubblico, GitHub, Google Workspace o Google Photos in base alle credenziali, alle skill e alle regole ammesse. Il Tutor legge il catalogo corrente dell'istanza e risponde sulla disponibilità effettiva.",
        "footer": "Fonti tecniche: vocabolario canonico, manifest firmati distribuiti con Metnos e contratti integrati. Il documento viene rigenerato prima di ogni pubblicazione.",
    },
    "en": {
        "title": "Domain reference",
        "domain_count": "canonical domains",
        "description": "Complete guide to Metnos operational domains, with boundaries, availability, and natural-language request examples.",
        "eyebrow": "Operational reference · generated from the canonical catalog",
        "lead": "What you can ask Metnos, domain by domain",
        "intro": "This page describes every canonical domain in the Metnos vocabulary. Each example is a sentence you can use directly in chat: no executor names, arguments, or technical syntax are required.",
        "contract": "The reference structure comes from the runtime's {domain_total} canonical objects; names and counts of distributed operations come from signed manifests and builtin contracts. Installed skills may add providers and operations. Ask the Tutor to learn what is currently available on your instance.",
        "ask": "Ask Metnos with a request like this example:",
        "boundary": "Boundary",
        "operations": "Distributed operations",
        "extension": "This domain becomes available when an admitted skill or provider installs its operations.",
        "available": "documented operations",
        "available_one": "documented operation",
        "nav_home": "Metnos",
        "nav_interface": "The interface",
        "nav_manual": "Architecture guide",
        "nav_catalog": "Technical catalog",
        "nav_tutor": "How the Tutor works",
        "provider_title": "Providers and actual availability",
        "provider_text": "Domains are stable; providers are a separate axis. An instance may use local filesystems and devices, IMAP/SMTP mailboxes, the public web, GitHub, Google Workspace, or Google Photos according to admitted credentials, skills, and policy. The Tutor reads the instance's live catalog and answers about current availability.",
        "footer": "Technical sources: canonical vocabulary, signed first-party manifests, and builtin contracts. This document is regenerated before every publication.",
    },
}


def _catalog_module():
    spec = importlib.util.spec_from_file_location(
        "domain_reference_executor_catalog", CATALOG_GENERATOR)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load executor catalog generator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_operations() -> dict[str, tuple[str, ...]]:
    module = _catalog_module()
    by_domain: dict[str, set[str]] = {domain: set() for domain in OBJECTS}
    by_domain["_system"] = set()
    for entry in module.load_entries():
        by_domain[entry.domain].add(entry.name)
    for manifest_path in sorted(BUILTIN_CONTRACTS.glob("*/manifest.toml")):
        with manifest_path.open("rb") as handle:
            manifest = tomllib.load(handle)
        name = str(manifest.get("name") or "").strip()
        if not name or name == "admin":
            continue
        parsed = parse_name(name)
        domain = parsed.obj if parsed and parsed.obj in OBJECTS else "_system"
        by_domain[domain].add(name)
    return {
        domain: tuple(sorted(names)) for domain, names in by_domain.items()
    }


def _domain_order() -> tuple[str, ...]:
    group_order = tuple(GROUPS["it"])
    ordered = []
    for group in group_order:
        ordered.extend(
            domain for domain in (*OBJECTS, "_system")
            if DOMAIN_COPY[domain].group == group
        )
    return tuple(ordered)


def validate_copy() -> None:
    expected = set(OBJECTS) | {"_system"}
    if set(DOMAIN_COPY) != expected:
        missing = sorted(expected - set(DOMAIN_COPY))
        extra = sorted(set(DOMAIN_COPY) - expected)
        raise RuntimeError(
            f"domain reference drift: missing={missing}, extra={extra}"
        )
    for domain, copy in DOMAIN_COPY.items():
        if (copy.group not in GROUPS["it"]
                or len(copy.examples_it) < 2
                or len(copy.examples_en) < 2):
            raise RuntimeError(f"incomplete domain reference copy: {domain}")


def render(lang: str, operations: dict[str, tuple[str, ...]]) -> str:
    validate_copy()
    text = TEXT[lang]
    other = "en" if lang == "it" else "it"
    canonical = f"https://metnos.com/{lang}/domains"
    alternate = f"https://metnos.com/{other}/domains"
    sections = []
    for group, (group_title, group_intro) in GROUPS[lang].items():
        cards = []
        for domain in _domain_order():
            copy = DOMAIN_COPY[domain]
            if copy.group != group:
                continue
            label = copy.label_it if lang == "it" else copy.label_en
            summary = copy.summary_it if lang == "it" else copy.summary_en
            boundary = copy.boundary_it if lang == "it" else copy.boundary_en
            examples = copy.examples_it if lang == "it" else copy.examples_en
            names = operations[domain]
            availability = (
                text["available_one"] if len(names) == 1
                else text["available"]
            )
            operation_body = (
                "".join(f"<code>{html.escape(name)}</code>" for name in names)
                if names else f'<span class="extension">{text["extension"]}</span>'
            )
            open_quote, close_quote = ("«", "»") if lang == "it" else ("“", "”")
            examples_html = "".join(
                f'<p class="example"><span>{text["ask"]}</span>'
                f'{open_quote}{html.escape(example)}{close_quote}</p>'
                for example in examples
            )
            cards.append(f'''
<article class="domain-card" id="domain-{html.escape(domain.lstrip("_"))}">
  <div class="domain-head"><div><span class="domain-key">{html.escape(domain)}</span><h3>{html.escape(label)}</h3></div><span class="count">{len(names)} {availability}</span></div>
  <p>{html.escape(summary)}</p>
  <p class="boundary"><strong>{text["boundary"]}.</strong> {html.escape(boundary)}</p>
  <div class="examples">{examples_html}</div>
  <details><summary>{text["operations"]} ({len(names)})</summary><div class="operations">{operation_body}</div></details>
</article>''')
        sections.append(f'''
<section class="domain-group" id="group-{group}">
  <div class="section-title"><span>{len(cards):02d}</span><div><h2>{group_title}</h2><p>{group_intro}</p></div></div>
  <div class="domain-grid">{"".join(cards)}</div>
</section>''')
    total_operations = len({name for names in operations.values() for name in names})
    group_links = "".join(
        f'<a href="#group-{group}">{html.escape(title)}</a>'
        for group, (title, _intro) in GROUPS[lang].items()
    )
    return f'''<!DOCTYPE html>
<!-- Generated by scripts/generate_domain_reference.py; edit the generator, not this file. -->
<html lang="{lang}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Metnos — {html.escape(text["title"])}</title>
<meta name="description" content="{html.escape(text["description"], quote=True)}">
<meta name="robots" content="index, follow">
<link rel="canonical" href="{canonical}">
<link rel="alternate" hreflang="it" href="https://metnos.com/it/domains">
<link rel="alternate" hreflang="en" href="https://metnos.com/en/domains">
<link rel="alternate" hreflang="x-default" href="https://metnos.com/en/domains">
<link rel="stylesheet" href="/assets/metnos.css?v=20260820-3">
<script defer src="/assets/wiki-shell.js?v=20260820-3"></script>
<style>
:root{{--ink:#25231f;--muted:#69645b;--paper:#fcfaf6;--warm:#f3eee4;--line:#ded5c5;--navy:#173f6b;--blue:#2b6cb0;--green:#477342;--bronze:#9a512f;--white:#fff}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.62 Inter,'Segoe UI',system-ui,sans-serif}}a{{color:var(--navy)}}.shell{{max-width:1180px;margin:auto;padding:0 28px}}nav{{display:flex;gap:18px;flex-wrap:wrap;padding:20px 0;border-bottom:1px solid var(--line);font-size:.92rem}}nav a{{text-decoration:none;font-weight:650}}.hero{{padding:74px 0 42px;display:grid;grid-template-columns:minmax(0,1.5fr) minmax(260px,.7fr);gap:46px;align-items:end}}.eyebrow{{text-transform:uppercase;letter-spacing:.12em;color:var(--bronze);font-size:.76rem;font-weight:800}}h1{{font-family:Georgia,serif;color:var(--navy);font-size:clamp(2.45rem,6vw,5rem);line-height:1.02;margin:.25em 0}}.lead{{font:1.35rem/1.5 Georgia,serif;color:var(--navy);max-width:720px}}.hero-note{{background:var(--navy);color:white;padding:26px;border-radius:4px;box-shadow:12px 12px 0 var(--warm)}}.hero-note strong{{display:block;font:2.2rem Georgia,serif}}.hero-note span{{color:#dce8f6}}.contract{{border-left:5px solid var(--green);background:#eef5eb;padding:20px 24px;margin:0 0 34px}}.jump{{display:flex;flex-wrap:wrap;gap:9px;margin:0 0 62px}}.jump a{{border:1px solid var(--line);background:white;padding:7px 12px;border-radius:999px;text-decoration:none;font-size:.86rem}}.domain-group{{margin:0 0 72px;scroll-margin-top:20px}}.section-title{{display:flex;gap:18px;align-items:center;border-bottom:2px solid var(--navy);margin-bottom:22px;padding-bottom:12px}}.section-title>span{{font:2.4rem Georgia,serif;color:var(--bronze)}}.section-title h2{{margin:0;color:var(--navy);font:1.8rem Georgia,serif}}.section-title p{{margin:2px 0 0;color:var(--muted)}}.domain-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}}.domain-card{{background:var(--white);border:1px solid var(--line);border-top:5px solid var(--navy);padding:24px;box-shadow:0 5px 18px rgba(53,42,25,.05);scroll-margin-top:18px}}.domain-head{{display:flex;justify-content:space-between;gap:16px;align-items:start}}.domain-key{{font:700 .72rem ui-monospace,monospace;text-transform:uppercase;letter-spacing:.08em;color:var(--bronze)}}h3{{font:1.55rem Georgia,serif;color:var(--navy);margin:2px 0 10px}}.count{{white-space:nowrap;color:var(--muted);font-size:.73rem;background:var(--warm);padding:4px 8px;border-radius:999px}}.boundary{{font-size:.91rem;color:var(--muted);border-top:1px solid var(--line);padding-top:12px}}.examples{{display:grid;gap:9px;margin:18px 0}}.example{{margin:0;background:#f5f8fb;border-left:4px solid var(--blue);padding:12px 14px;font-family:Georgia,serif}}.example span{{display:block;font:700 .7rem/1.4 Inter,'Segoe UI',sans-serif;text-transform:uppercase;letter-spacing:.06em;color:var(--blue);margin-bottom:4px}}details{{border-top:1px solid var(--line);padding-top:11px}}summary{{cursor:pointer;color:var(--navy);font-weight:700}}.operations{{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}}.operations code{{font-size:.72rem;background:var(--warm);padding:4px 7px;border-radius:3px}}.extension{{font-size:.85rem;color:var(--muted)}}.provider{{margin:10px 0 72px;padding:32px;background:linear-gradient(120deg,var(--navy),#245f91);color:white}}.provider h2{{font:1.8rem Georgia,serif;margin:0 0 8px}}.provider p{{margin:0;max-width:850px;color:#e7eff7}}footer{{border-top:1px solid var(--line);padding:28px 0 48px;color:var(--muted);font-size:.86rem}}@media(max-width:800px){{.hero{{grid-template-columns:1fr;padding-top:45px}}.domain-grid{{grid-template-columns:1fr}}.shell{{padding:0 16px}}.domain-head{{display:block}}.count{{display:inline-block;margin-bottom:8px}}}}
</style></head><body>
<div class="shell">
<nav><a href="index.html">← {text["nav_home"]}</a><a href="interface.html">{text["nav_interface"]}</a><a href="architecture/index.html">{text["nav_manual"]}</a><a href="architecture/executor_catalog.html">{text["nav_catalog"]}</a><a href="architecture/tutor.html">{text["nav_tutor"]}</a><a href="/{other}/domains.html" hreflang="{other}">{other.upper()}</a></nav>
<header class="hero"><div><div class="eyebrow">{text["eyebrow"]}</div><h1>{text["lead"]}</h1><p class="lead">{text["intro"]}</p></div><div class="hero-note"><strong>{len(OBJECTS)}</strong><span>{text["domain_count"]}</span><strong>{total_operations}</strong><span>{text["available"]}</span></div></header>
<p class="contract">{text["contract"].format(domain_total=len(OBJECTS))}</p>
<div class="jump">{group_links}</div>
{"".join(sections)}
<section class="provider"><h2>{text["provider_title"]}</h2><p>{text["provider_text"]}</p></section>
<footer>{text["footer"]}</footer>
</div></body></html>
'''


def write_reference(*, check: bool = False) -> bool:
    operations = load_operations()
    changed = False
    for lang, output in OUTPUTS.items():
        content = render(lang, operations)
        current = output.read_text(encoding="utf-8") if output.is_file() else ""
        if content == current:
            continue
        changed = True
        if not check:
            output.write_text(content, encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = write_reference(check=args.check)
    if args.check and changed:
        print("domain reference docs are stale", file=sys.stderr)
        return 1
    if not args.check:
        operations = load_operations()
        count = len({name for names in operations.values() for name in names})
        print(f"generated {len(OBJECTS)} domains and {count} operations in 2 locales")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
