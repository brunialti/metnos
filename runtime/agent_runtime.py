#!/usr/bin/env python3
"""
agent_runtime.py — il loop pianificatore (Metnos v1.1 POC).

Decisioni di design applicate (sessione 26/4/2026):
    D1  mode-parametrizzato:
            local  = multistep ReAct (default)
            online = single-shot (rimandato; PoC non ha provider online)
            hybrid = router (rimandato)
    D2  pre-filtrato (bag-of-words v1.1, MiniLM rimandato).
    D3  turno = una richiesta utente.
    D4  vaglio probabilistico, qui stub always-approve. In multistep gira fra step.
    D7  sequenziale.
    D8  in-memory + JSONL append-only.
    D9  niente retry, cap step + cap chiamate (configurabili, default 5/2).
    M1  config + router (in PoC mode hardcoded a local).

Aggiornato dopo ciclo finale POC (26/4):
    - tool-use NATIVO via OllamaProvider.chat_with_tools (no prompt-based JSON parsing)
    - data piping con sintassi {{stepN.field}} (opzione A confermata nel ciclo 12)
    - default model = qwen3:8b con think=false
"""
import functools
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import uuid
from cost_tracker import CostTracker
from llm_provider import OllamaProvider, ProviderError, make_provider_from_spec
from loader import load_catalog, filter_for_visibility, VISIBILITY_COMPOSER
from messages import get as msg
from mnestoma import Mnestoma, build_desired_signature
from prefilter import rank, rank_adaptive
from scratchpad import Scratchpad, SCRATCHPAD_READ_TOOL
from synt import Synt, make_request as synt_make_request
from synth_request import SYNTH_REQUEST_TOOL, handle_synth_request
import location_request as _location_request
import prompt_loader  # ADR 0092: prompt LLM in runtime/prompts/<lang>/
from config import DEFAULT_LANG, DEFAULT_TIMEZONE
from fast_path import try_fast_path, try_seed_step

LOCATION_REQUEST_TOOL = {
    "type": "function",
    "function": {
        "name": "request_location_from_user",
        "description": (
            "USA QUESTO TOOL quando hai gia' chiamato get_location come precursor "
            "di una query LOCATION-RELATIVE (con marker prossimita' tipo 'vicino a "
            "me', 'qui', 'intorno', 'near me', 'nearby') e get_location ha ritornato "
            "ok:false con error tipo 'no location received yet'. Il tool rinegozia "
            "con l'utente via canale (Telegram: bottoni 'Invia posizione'/'Annulla' "
            "+ campo testo per indirizzo/CAP/citta'). Il TURNO TERMINA SILENZIOSAMENTE "
            "subito dopo: ritornera' un'observation con awaiting:true e il runtime "
            "soppimera' il final_answer. Quando l'utente risponde, il daemon "
            "rilancera' un nuovo turno con la query originale e get_location avra' "
            "la posizione fresca. NON usare per query con luogo esplicito ('a Roma', "
            "'in Via X') — quelle vanno a find_places diretto senza get_location."
        ),
        "parameters": {
            "type": "object",
            "required": ["goal"],
            "properties": {
                "goal": {
                    "type": "string",
                    "description": (
                        "Verbo+oggetto della query corrente in italiano breve, da "
                        "mostrare all'utente nel prompt 'Mi serve la tua posizione "
                        "per <goal>'. Es. 'trovare la farmacia piu' vicina', "
                        "'cercare ristoranti nei dintorni'."
                    ),
                },
            },
        },
    },
}
from describe_entries import DESCRIBE_ENTRIES_TOOL, handle_describe_entries
from classify_entries import CLASSIFY_ENTRIES_TOOL, handle_classify_entries
from recurring_tasks import (
    CREATE_TASKS_TOOL, LIST_TASKS_TOOL,
    DELETE_TASKS_TOOL, READ_TASKS_TOOL,
    SET_TASKS_TOOL, READ_TASKS_HISTORY_TOOL,
    handle_create_tasks, handle_list_tasks,
    handle_delete_tasks, handle_read_tasks,
    handle_set_tasks, handle_read_tasks_history,
)
from test_runner import check_hints
from undo import UndoLog
from vaglio import judge

TURN_LOG_DIR = Path.home() / ".local" / "share" / "metnos" / "turns"
DEFAULT_CAP_STEPS = 30
DEFAULT_CAP_SAME_EXECUTOR = 10
# Cap per-turn per executor non-action (find/get/list/read/classify/filter):
# chiamate >= soglia (anche non consecutive) forzano final_answer (CLAUDE.md
# §4.4 estesa, 8/5/2026 notte). Configurabile via env. I verbi action
# (write/move/delete/send/create/set/change) hanno guardie proprie a monte
# (cyclic-call, duplicate, vaglio).
DEFAULT_CAP_MAX_PER_TURN = int(os.environ.get("METNOS_CAP_MAX_PER_TURN", "3") or "3")
SCRATCHPAD_THRESHOLD_BYTES = 4096  # observation oltre questa dimensione vanno in scratchpad


# Scrubbing credenziali nel turn log (ADR 0082, 4/5/2026).
# I pattern si applicano DOPO che il PLANNER ha gia' processato la query
# (le credenziali restano in RAM per il turn). Output jsonl pulito.
_CRED_RE = re.compile(
    r"(\bp(?:wd|assword|sw|ass)\s*[:=]?\s*)(\S+)", re.IGNORECASE
)
_USER_RE = re.compile(
    r"(\bu(?:ser|name|tente)\s*[:=]?\s*)(\S+)", re.IGNORECASE
)


def _scrub_credentials(text: str) -> tuple[str, int]:
    """Sostituisce match di password/username inline con `<REDACTED:cred>`.

    Ritorna (testo_pulito, n_match). Idempotente: re-applicare e' no-op
    perche' `<REDACTED:cred>` non matcha gli stessi pattern.
    """
    if not isinstance(text, str) or not text:
        return text, 0
    n_matches = 0
    def _r(m):
        nonlocal n_matches
        n_matches += 1
        return m.group(1) + "<REDACTED:cred>"
    cleaned = _CRED_RE.sub(_r, text)
    cleaned = _USER_RE.sub(_r, cleaned)
    return cleaned, n_matches


# Anti thinking-leak (ADR 0102, 7/5/2026). Gemma 4 26B think=true a volte
# emette il proprio reasoning interno nel canale `text` invece che nel
# canale `thinking` separato — il final_message dell'utente si riempie di
# righe tipo "Wait, I'll check...", "Actually, I should...", "Let me think".
# Lo scrubber e' deterministico (regex su righe standalone, §7.9):
# rimuove SOLO righe il cui inizio e' un trigger di reasoning, preservando
# substring legittime in mezzo a paragrafi reali (§2.8 no silent failure).
_THINKING_LEAK_RE = re.compile(
    r"^\s*(?:"
    r"Wait\b|Actually\b|Let me\b|I'll\b|I will\b|Hmm\b|"
    r"Looking at\b|One detail:|Final Answer(?:\s+construction)?:|"
    r"Wait,?\s+I(?:'|)ll\b|Wait,?\s+I should\b|"
    r"Now I'll\b|Actually,?\s+I'll\b|So,?\s+the answer\b|Let me think\b|"
    r"I should\b|Rule:\s|Given\b"
    r").*$",
    re.IGNORECASE,
)

# Pattern italiani — meta-permission e self-talk di Gemma 4 26B think=true.
# Caso live federvolley (7/5/2026): "(posso provare a cercarli se mi dai il
# via libera)" e "ti suggerisco queste alternative" come list intro.
# Politica chirurgica (CLAUDE.md §2.8 / §7.9): rimuoviamo SOLO righe in
# parentesi che chiedono permesso, oppure righe standalone che aprono con
# meta-permission ("se vuoi", "se mi dai il via libera", ...). Mantieni
# substring legittime in mezzo a contenuto reale.

# (a) Riga interamente fra parentesi che chiede permesso.
_LEAK_IT_PAREN_PERMISSION_RE = re.compile(
    r"^\s*\(\s*(?:"
    r"posso provare|posso cercare|posso aiutarti|posso suggerirti|"
    r"posso farlo|posso fare|posso recuperare|posso scaricare|"
    r"se mi dai il via libera|se vuoi|se preferisci|fammi sapere|"
    r"dimmi se|vuoi che (?:lo )?faccia|se ti serve|se hai bisogno"
    r")[^)]*\)\s*\.?\s*$",
    re.IGNORECASE,
)

# (b) Riga standalone che APRE con meta-permission/meta-discourse e
# termina nello stesso periodo (no continuazione su altre frasi).
# Pattern: la riga inizia con uno dei trigger e finisce con `.`/`?`/`!`
# o EOL — l'intera riga e' una richiesta di permesso unica. Se prosegue
# con altri contenuti (es. "se vuoi posso aiutarti, ma prima ..."), NON
# scattare per evitare di mutilare contenuto utile.
_LEAK_IT_STANDALONE_RE = re.compile(
    r"^\s*(?:"
    r"se mi dai il via libera|se vuoi posso|fammi sapere se|"
    r"dimmi se vuoi|vuoi che (?:lo )?faccia|"
    r"posso provare a|posso cercare|posso aiutarti|posso suggerirti"
    r")\b[^.?!,;]*[.?!]?\s*$",
    re.IGNORECASE,
)


def _scrub_thinking_leak(text):
    """Rimuove pattern di reasoning leak da response PLANNER.

    Gemma 4 26B think=true a volte emette thinking nel canale text invece
    che nel canale thinking separato. Pattern rimossi:

    - EN: righe (standalone) che iniziano con marker di reasoning interno
      tipo "Wait, ", "Actually, ", "Let me ", "I'll check", "Hmm, ",
      "Looking at", "Final Answer:", "One detail:", "Rule: ".
    - IT (chirurgico, ADR estensione 7/5/2026): righe ENTIRE in parentesi
      che chiedono permesso ("(posso provare a ... se mi dai il via libera)")
      e righe standalone meta-permission ("se vuoi posso ...", "fammi
      sapere se ..."). NON tocca "Riassumendo: ..." o "Ti suggerisco ..."
      in mezzo a contenuto perche' possono essere legittimi (es. l'utente
      ha chiesto un riassunto).

    Sostringhe in mezzo a paragrafi legittimi sono PRESERVATE (es. il
    documento citato dice "Wait, this is important" rimane).

    Idempotente: re-applicare e' no-op (le righe leak sono gia' rimosse).
    Ritorna stringa pulita; input non-stringa o vuoto torna invariato.
    """
    if not text or not isinstance(text, str):
        return text
    cleaned = []
    for line in text.split("\n"):
        if _THINKING_LEAK_RE.match(line):
            continue
        if _LEAK_IT_PAREN_PERMISSION_RE.match(line):
            continue
        if _LEAK_IT_STANDALONE_RE.match(line):
            continue
        cleaned.append(line)
    out = "\n".join(cleaned).strip()
    # Collapse multi-blank-lines residue dopo rimozioni.
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out


def _scrub_args_recursive(node, total: list[int]) -> object:
    """Scrub ricorsivo su dict/list/str. Mutates total[0] con il count."""
    if isinstance(node, str):
        cleaned, n = _scrub_credentials(node)
        total[0] += n
        return cleaned
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            # Anche scrubbing diretto dei value se la chiave dice "password"
            if isinstance(k, str) and k.lower() in (
                "password", "pwd", "psw", "pass",
            ) and isinstance(v, str) and v:
                total[0] += 1
                out[k] = "<REDACTED:cred>"
            else:
                out[k] = _scrub_args_recursive(v, total)
        return out
    if isinstance(node, list):
        return [_scrub_args_recursive(x, total) for x in node]
    return node


# ── Estrazione credenziali dalla query (Strato 1 — ADR 0089, 4/5/2026) ──
# Quando l'utente scrive "monta share \\\\nas\\Public user roberto pwd hunter2"
# il runtime estrae user/pwd e li salva cifrati prima che la query raggiunga
# il PLANNER. La query passata al pianificatore ha le creds rimpiazzate da
# `<REDACTED:cred:domain>` cosi' il LLM non le vede mai. Il dominio viene
# derivato deterministicamente dal contesto della query (host CIFS, URL web,
# host SSH) — codice deterministico > LLM (CLAUDE.md §7.9).
#
# Pattern coperti:
#   "user X pwd Y", "utente X password Y", "user=X pass=Y",
#   "username: X, password: Y", "nome utente X passw Y", "X / Y" come slot.
#
# Riconoscimento del dominio:
#   - share CIFS:  "//192.168.1.20/Public" / "\\\\nas.local\\share" → cifs_<host>
#   - URL/host web: "https://webmail.example.com" → web_<host>
#   - ssh:          "ssh roberto@nas.local"        → ssh_<host>
#   - hint testuale: "share|smb|cifs|nas" → cifs ; "login|portale|sito" → web ;
#                    "ssh" → ssh.
#   - fallback: "generic" se nessun host derivabile (caso degenere).

# Pattern di estrazione: cattura coppie user/pwd in una passata sola.
# Usiamo finditer per ricavare gli offset esatti (per scrubbing offsets).
# Le keyword sono ordinate per lunghezza (LONGEST FIRST) per evitare match
# parziali tipo "user" che taglia "username" ⇒ value="name:carlo".
_USER_KEYWORD = (
    r"(?:\busername|\busernam|\butente|\buser|\bnome\s+utente|\blogin)"
)
_PWD_KEYWORD = (
    r"(?:\bpassword|\bpasswd|\bpasw|\bpwd|\bpsw|\bpass)"
)
_VAL = r"[^\s,;]+"
# user prima di pwd (caso piu' comune)
_USER_THEN_PWD = re.compile(
    rf"({_USER_KEYWORD})\s*[:=]?\s*({_VAL})"
    rf"\s*[,;]?\s*"
    rf"({_PWD_KEYWORD})\s*[:=]?\s*({_VAL})",
    re.IGNORECASE,
)
# pwd prima di user (caso meno comune ma valido)
_PWD_THEN_USER = re.compile(
    rf"({_PWD_KEYWORD})\s*[:=]?\s*({_VAL})"
    rf"\s*[,;]?\s*"
    rf"({_USER_KEYWORD})\s*[:=]?\s*({_VAL})",
    re.IGNORECASE,
)

# Riconoscimento host CIFS: //host/share oppure \\host\share (Windows-style).
# Tolleriamo doppia barra invertita escapata in stringa Telegram.
_CIFS_SHARE_RE = re.compile(
    r"(?:\\\\|//)([A-Za-z0-9._-]+)(?:\\|/)([A-Za-z0-9._/\\-]+)"
)
# URL / host web
_URL_HOST_RE = re.compile(
    r"https?://([A-Za-z0-9._-]+)(/[^\s]*)?", re.IGNORECASE
)
# host bare (FQDN o IP) — usato solo quando hint = ssh/login/sito
_BARE_HOST_RE = re.compile(
    r"\b((?:\d{1,3}\.){3}\d{1,3}|(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})\b"
)
# Hint del binding: parole-spia per CIFS/web/ssh.
# Prioritizziamo discriminatori inequivocabili (ssh come comando, https://, //)
# rispetto a parole "ambigue" (nas, share) che possono comparire anche in
# query che parlano di accesso ssh a un NAS.
_BINDING_STRONG = (
    ("ssh",  (r"\bssh\s", r"\bscp\s", r"\bsftp\s", r"\bssh\b$")),
    ("web",  (r"https?://",)),
    ("cifs", (r"//\S+/", r"\\\\\S+",)),
)
_BINDING_WEAK = (
    ("cifs", ("share", "smb", "cifs", "nas", "monta", "mount", "samba")),
    ("ssh",  ("ssh", "scp", "sftp")),
    ("web",  ("login", "sito", "portale", "registro", "banca",
              "browser", "webmail")),
)


def _detect_binding(query: str) -> str:
    """Ritorna 'cifs' | 'ssh' | 'web' | 'generic' in base ad hint linguistici.

    Priorita' a discriminatori inequivocabili (regex) prima dei keyword.
    """
    qlc = query.lower()
    for binding, patterns in _BINDING_STRONG:
        for p in patterns:
            if re.search(p, qlc):
                return binding
    for binding, kws in _BINDING_WEAK:
        if any(k in qlc for k in kws):
            return binding
    return "generic"


def _detect_host(query: str, binding: str) -> tuple[str, dict]:
    """Ritorna (host, context) deterministicamente. context include share path
    quando applicabile per uso da parte del sudoer al fire time.
    """
    ctx: dict = {"binding": binding}
    if binding == "cifs":
        m = _CIFS_SHARE_RE.search(query)
        if m:
            host = m.group(1).lower()
            ctx["host"] = host
            ctx["share"] = m.group(2).replace("\\", "/")
            return host, ctx
    if binding == "web":
        m = _URL_HOST_RE.search(query)
        if m:
            host = m.group(1).lower()
            ctx["host"] = host
            return host, ctx
    # Fallback bare host (vale anche per ssh)
    m = _BARE_HOST_RE.search(query)
    if m:
        host = m.group(1).lower()
        ctx["host"] = host
        return host, ctx
    return "", ctx


def extract_credentials(query: str) -> list[dict]:
    """Estrae coppie user/pwd dalla query con regex deterministici (ADR 0089).

    Ritorna lista (puo' essere vuota) di dict con shape:
        {
          "domain":   "cifs_192.168.1.20",   # chiave canonica per credentials.store
          "username": "roberto",
          "password": "hunter2",
          "context":  {"binding": "cifs", "host": "...", "share": "..."},
          "scrub_spans": [(start, end), ...],   # offsets nel testo originale
        }

    Pattern accettati: "user X pwd Y", "utente X password Y", "user=X pass=Y",
    "username:X password:Y", "nome utente X passw Y". Case-insensitive.
    Il dominio e' derivato dall'host nella query (CIFS share, URL web, host
    bare) + binding inferito dalle parole-spia (share/cifs/nas → cifs,
    login/portale → web, ssh → ssh, fallback "generic").

    Non solleva eccezioni: query senza match → lista vuota.
    """
    if not isinstance(query, str) or not query.strip():
        return []
    binding = _detect_binding(query)
    host, ctx = _detect_host(query, binding)
    domain_prefix = binding if binding != "generic" else "host"
    if host:
        domain = f"{domain_prefix}_{host}"
    else:
        domain = f"{domain_prefix}_unknown"

    out: list[dict] = []
    seen_spans: set[tuple[int, int]] = set()

    def _add(user: str, pwd: str, spans: list[tuple[int, int]]) -> None:
        # Skip vuoti / placeholder gia' redacted
        if not user or not pwd:
            return
        if user.startswith("<REDACTED") or pwd.startswith("<REDACTED"):
            return
        for s in spans:
            if s in seen_spans:
                return
        for s in spans:
            seen_spans.add(s)
        out.append({
            "domain": domain,
            "username": user,
            "password": pwd,
            "context": dict(ctx),
            "scrub_spans": list(spans),
        })

    for m in _USER_THEN_PWD.finditer(query):
        # group 2 = user value, group 4 = pwd value
        user_val = m.group(2).rstrip(",;.")
        pwd_val = m.group(4).rstrip(",;.")
        # Scrub spans: solo i VALUE, non le keyword (per leggibilita').
        spans = [(m.start(2), m.start(2) + len(user_val)),
                 (m.start(4), m.start(4) + len(pwd_val))]
        _add(user_val, pwd_val, spans)
    for m in _PWD_THEN_USER.finditer(query):
        pwd_val = m.group(2).rstrip(",;.")
        user_val = m.group(4).rstrip(",;.")
        spans = [(m.start(2), m.start(2) + len(pwd_val)),
                 (m.start(4), m.start(4) + len(user_val))]
        _add(user_val, pwd_val, spans)

    return out


def _redact_spans(text: str, spans: list[tuple[int, int]], domain: str) -> str:
    """Sostituisce i tratti span con `<REDACTED:cred:domain>`. Preserva offset
    riducendo gli span man mano. Lavora su una copia, niente mutazione in-place.
    """
    if not spans:
        return text
    placeholder = f"<REDACTED:cred:{domain}>"
    # Ordina per start desc cosi' le sostituzioni successive non spostano
    # gli span ancora da processare.
    sorted_spans = sorted(set(spans), key=lambda s: s[0], reverse=True)
    out = text
    for start, end in sorted_spans:
        if 0 <= start < end <= len(out):
            out = out[:start] + placeholder + out[end:]
    return out


def apply_credentials_extraction(query: str) -> tuple[str, list[dict]]:
    """Strato 1 del flow UX credenziali (ADR 0089).

    1. Estrae credenziali dalla query con `extract_credentials`.
    2. Per ciascuna: salva cifrate via `credentials.store` (ADR 0082).
    3. Sostituisce i value nel testo con `<REDACTED:cred:domain>`.
    4. Ritorna (query_redacted, list_di_creds_metadata) — la metadata
       contiene solo domain + context (NON username/password) ed e'
       sicura da iniettare nel context del PLANNER.
    """
    creds = extract_credentials(query or "")
    if not creds:
        return query, []
    try:
        import credentials  # type: ignore
    except ImportError:
        return query, []
    redacted = query
    safe_meta: list[dict] = []
    # Aggrega tutti gli scrub span (l'estrazione produce piu' record
    # con stesso domain — gli span vanno comunque rimpiazzati tutti).
    all_spans: list[tuple[int, int]] = []
    for c in creds:
        for s in c.get("scrub_spans") or []:
            all_spans.append(tuple(s))
        try:
            credentials.store(
                c["domain"],
                {
                    "username": c["username"],
                    "password": c["password"],
                    **{k: v for k, v in (c.get("context") or {}).items()
                       if k in ("binding", "host", "share", "workgroup", "port")},
                },
            )
        except (ValueError, OSError, FileNotFoundError):
            # Storage fallito: log lo stato ma scrubbamo lo stesso il testo
            # (priorita': non leakare la pwd anche se non riusciamo a salvarla).
            continue
        safe_meta.append({
            "domain": c["domain"],
            "context": dict(c.get("context") or {}),
        })
    if all_spans:
        # Per il redact uso il primo dominio come tag, ma ogni run cattura
        # un solo dominio per query nella pratica (un solo host).
        primary_domain = creds[0]["domain"]
        redacted = _redact_spans(query, all_spans, primary_domain)
    return redacted, safe_meta


# --- Mode router ------------------------------------------------------------

class ModeRouter:
    def __init__(self, mode="local"):
        self.mode = mode

    def select(self, query, catalog):
        return self.mode


# --- Prompt + tools rendering ---------------------------------------------
# PLANNER prompt è in runtime/prompts/<METNOS_LANG>/planner.j2 (ADR 0092).
# Caricato via prompt_loader.get("planner", **vars) in run_turn().

# Inietta il vocabolario centralizzato (vocab.py) nel prompt — single source.
from vocab import (
    render_actions_inline as _vocab_actions,
    render_objects_inline as _vocab_objects,
    render_qualifiers_inline as _vocab_qualifiers,
)
from logging_setup import get_logger
log = get_logger(__name__)


def _render_project_paths_block() -> str:
    """Carica `runtime/project_paths.json` e ritorna un blocco testuale per
    il prompt PLANNER. Formato: una riga per progetto con name → code_root.

    Bug fix 4/5/2026 (ADR 0079): l'utente puo' chiamare un progetto col suo
    nome ('metnos', 'giorgio2', ...) come oggetto della query. Senza questo
    blocco il PLANNER interpretava il nome come pattern di filename e
    chiamava find_files(pattern="*metnos*") invece di usare il code_root
    canonico. Letto a load-time del modulo: gli aggiornamenti al JSON si
    rifletteranno al prossimo restart del runtime/daemon.
    """
    cfg_path = Path(__file__).resolve().parent / "project_paths.json"
    if not cfg_path.exists():
        return '  (nessun progetto configurato in runtime/project_paths.json)'
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as ex:
        log.warning("project_paths.json read failed: %s", ex)
        return '  (errore lettura runtime/project_paths.json)'
    if not isinstance(data, dict) or not data:
        return '  (nessun progetto configurato)'
    lines = []
    for proj, meta in data.items():
        if not isinstance(meta, dict):
            continue
        # Supporto sia codebase (code_root) sia collezioni dati (data_root):
        # se entrambi mancano, "?". Il PLANNER vede comunque description.
        root = meta.get("code_root") or meta.get("data_root") or "?"
        kind = "codebase" if meta.get("code_root") else "collezione"
        desc = meta.get("description") or ""
        lines.append(f'  "{proj}" = {kind} in {root}'
                      + (f' ({desc})' if desc else ''))
    return "\n".join(lines) if lines else '  (nessun progetto configurato)'


def _render_users_known_block() -> str:
    """Carica `users.list_users()` e ritorna un blocco testuale per il prompt
    PLANNER. Formato: una riga per user con name (role, owner, autonomy,
    canali verificati). Multi-user (4/5/2026, ADR 0083): permette al
    pianificatore di risolvere "manda a Lucia" → `to_user="lucia"` invece
    di indovinare chat_id letterali. Letto a load-time: aggiornare l'elenco
    richiede restart del runtime/daemon (mantiene il prompt deterministico
    durante la pianificazione di un turno).
    """
    try:
        import users as _users
        _users.init_db()
        rows = _users.list_users()
    except Exception as ex:
        log.warning("users.list_users failed: %s", ex)
        return '  (servizio utenti non disponibile)'
    if not rows:
        return '  (nessun utente registrato)'
    out = []
    for u in rows:
        try:
            chans = _users.list_channels(u["id"])
        except Exception:
            chans = []
        verified = [c["channel"] for c in chans if c.get("verified_at")]
        pending = [c["channel"] for c in chans
                   if not c.get("verified_at") and c.get("pairing_token")]
        suffix_chans = []
        if verified:
            suffix_chans.append(", ".join(f"{c} OK" for c in verified))
        if pending:
            suffix_chans.append(", ".join(f"{c} pending" for c in pending))
        suffix = " — " + "; ".join(suffix_chans) if suffix_chans else ""
        owner = ""
        if u.get("owner_user_id"):
            o = _users.get_user(u["owner_user_id"])
            if o:
                owner = f", owner={o['name']}"
        out.append(
            f'  - {u["name"]} ({u["role"]}{owner}, '
            f'autonomy={u["autonomy_level"]}){suffix}'
        )
    return "\n".join(out)




_OBS_HISTORY_CHAR_CAP = 8000


def _trim_obs_for_history(obs: dict | str, *, cap: int = _OBS_HISTORY_CHAR_CAP) -> str:
    """Serializza una observation in JSON pronto per `history_for_llm` con cap
    sui caratteri. Se sfora, prova prima a rimuovere `body_preview` dalle
    entries (campo pesante, recuperabile da scratchpad_read); se ancora sfora,
    riduce il numero di entries preservando struttura JSON valida; come ultima
    risorsa, fallback al troncamento "stupido" di stringa con marker.

    Niente perdita silenziosa: se rimuove campi/entries lo dichiara via campo
    `_obs_trimmed` con dettaglio."""
    if not isinstance(obs, dict):
        s = json.dumps(obs, ensure_ascii=False) if not isinstance(obs, str) else obs
        return s if len(s) <= cap else s[:cap] + "...[troncato]"

    s = json.dumps(obs, ensure_ascii=False)
    if len(s) <= cap:
        return s

    entries = obs.get("entries")
    if isinstance(entries, list) and entries:
        # Tentativo 1: rimuovi body_preview
        slim_entries = [
            {k: v for k, v in e.items() if k != "body_preview"} if isinstance(e, dict) else e
            for e in entries
        ]
        slim = dict(obs)
        slim["entries"] = slim_entries
        slim["_obs_trimmed"] = "body_preview rimosso (recuperabile via scratchpad_read)"
        s2 = json.dumps(slim, ensure_ascii=False)
        if len(s2) <= cap:
            return s2

        # Tentativo 2: riduci numero di entries proporzionalmente
        n = len(slim_entries)
        keep = max(1, int(n * cap / max(len(s2), 1)))
        slim["entries"] = slim_entries[:keep]
        slim["_obs_trimmed"] = (
            f"body_preview rimosso + entries ridotte a {keep}/{n} "
            f"(piene via scratchpad_read)"
        )
        s3 = json.dumps(slim, ensure_ascii=False)
        if len(s3) <= cap:
            return s3

    # Fallback: troncamento stringa con marker
    return s[:cap] + "...[troncato]"


_FROM_STEP_DESC = (
    "Numero dello step precedente (in questo turno) che ha prodotto la "
    "lista da consumare. Es. se al passo 1 hai chiamato find_files / "
    "read_messages / find_dirs, qui passi from_step=1: il runtime "
    "espande automaticamente le entries dallo scratchpad. NON passare "
    "entries inline: lo schema non lo prevede e il modello non ha visibilita' "
    "sui dati prodotti dagli step precedenti."
)


_WEEKDAY_IT = ["lunedi'", "martedi'", "mercoledi'", "giovedi'",
                "venerdi'", "sabato", "domenica"]
_WEEKDAY_EN = ["Monday", "Tuesday", "Wednesday", "Thursday",
                "Friday", "Saturday", "Sunday"]


def _render_now_vars() -> dict:
    """Restituisce dict con riferimenti temporali correnti per il prompt
    planner footer (Roberto 12/5/2026): today_iso/now_hhmm/weekday_*/tz.
    Iniettato in compose() per evitare step get_now ridondante quando il
    planner deve solo risolvere una data relativa banale. §7.9 deterministico.
    Per orari precisi al secondo o explicit time-of-day request, il planner
    invoca comunque get_now (hint in _footer.j2).
    """
    from datetime import datetime
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(DEFAULT_TIMEZONE)
        now = datetime.now(tz)
    except Exception:
        now = datetime.now()
    wd = now.weekday()
    return {
        "today_iso": now.strftime("%Y-%m-%d"),
        "now_hhmm": now.strftime("%H:%M"),
        "weekday_it": _WEEKDAY_IT[wd],
        "weekday_en": _WEEKDAY_EN[wd],
        "tz": DEFAULT_TIMEZONE,
    }


def planner_facing_schema(schema):
    """Trasforma lo `args_schema` di un executor nello schema esposto al
    pianificatore LLM. Fix strutturale (30/4/2026) per il disallineamento
    prompt↔schema sotto schema-guided decoding (Ollama/Gemma).

    Regola unica: se l'executor consuma una lista prodotta da uno step
    upstream (rilevato dalla presenza di `entries` in `properties` o in
    `required` del manifest), lo schema esposto al modello rimuove
    `entries` (che NON puo' essere inventato dall'LLM, e' un dato di
    runtime) e inietta `from_step: integer >=1` come argomento di
    riferimento allo step. Altri campi del manifest (e.g. `by`, `op`,
    `dst_template`) restano invariati.

    Razionale: sotto schema-guided decoding il modello segue lo schema,
    non il prompt. Esporre `entries: array` come richiesto forza l'LLM a
    inventare contenuto plausibile o passare `[]`. Esporre `from_step`
    come unico riferimento alla lista upstream rende il vincolo
    strutturale, non un consiglio nel prompt. Vedi §2.4 di CLAUDE.md
    (robustezza al confine NL→determinismo) e ADR-from_step.

    Il `validate_args` accetta sia `from_step` che `entries` (vedi
    special-case linea 387), quindi:
    - manifest che gia' usano `from_step` (filter_entries, get_files_metadata,
      move_files): nessuna trasformazione necessaria.
    - manifest che usano `entries` (sort_entries, compute_entries,
      get_file_dates): vengono trasformati qui in modo consistente.
    """
    if not isinstance(schema, dict):
        return schema
    props = dict(schema.get("properties") or {})
    required = list(schema.get("required") or [])
    has_entries = "entries" in props or "entries" in required
    if not has_entries:
        return schema
    # Rimuovi entries dalla vista del modello: non puo' inventarle.
    props.pop("entries", None)
    # Inietta from_step (idempotente).
    if "from_step" not in props:
        props["from_step"] = {
            "type": "integer",
            "minimum": 1,
            "description": _FROM_STEP_DESC,
        }
    # Sostituisci entries con from_step in required, deduplicando.
    new_required = []
    for r in required:
        target = "from_step" if r == "entries" else r
        if target not in new_required:
            new_required.append(target)
    if "from_step" not in new_required:
        new_required.append("from_step")
    out = dict(schema)
    out["properties"] = props
    out["required"] = new_required
    return out


def render_tools_for_provider(executors):
    """Converte la lista di Executor in tools format Ollama/OpenAI.

    Applica `planner_facing_schema` per garantire che lo schema esposto
    al modello sia coerente con la convenzione `from_step` (vedi
    docstring). Trasformazione centralizzata: i singoli manifest non
    devono ricordare la convenzione, la pipeline rendering la impone.
    """
    tools = []
    for ex in executors:
        tools.append({
            "type": "function",
            "function": {
                "name": ex.name,
                "description": ex.description,
                "parameters": planner_facing_schema(ex.args_schema) or {"type": "object"},
            },
        })
    return tools


# --- Validazione args (subset JSON Schema v1.1) ----------------------------

def validate_args(args, schema):
    failures = []
    schema = schema or {}
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    for r in required:
        if r not in args:
            # `from_step` viene consumato da resolve_from_step e sostituito da
            # `entries` (popolato dallo scratchpad). Se entries e' presente,
            # il required e' soddisfatto. Stesso pattern per altri arg che
            # un futuro resolver potrebbe iniettare.
            if r == "from_step" and "entries" in args:
                continue
            failures.append(f"missing required arg '{r}'")
    for name, value in (args or {}).items():
        if name not in props:
            continue
        spec = props[name]
        expected_type = spec.get("type")
        if expected_type == "string" and not isinstance(value, str):
            failures.append(f"arg '{name}' deve essere string, e' {type(value).__name__}")
        elif expected_type == "integer" and not isinstance(value, int):
            failures.append(f"arg '{name}' deve essere integer, e' {type(value).__name__}")
        elif expected_type == "number" and not isinstance(value, (int, float)):
            failures.append(f"arg '{name}' deve essere number, e' {type(value).__name__}")
        elif expected_type == "boolean" and not isinstance(value, bool):
            failures.append(f"arg '{name}' deve essere boolean, e' {type(value).__name__}")
        elif expected_type == "object" and not isinstance(value, dict):
            failures.append(f"arg '{name}' deve essere object, e' {type(value).__name__}")
        if "enum" in spec and value not in spec["enum"]:
            failures.append(f"arg '{name}' deve essere in {spec['enum']}, e' {value!r}")
    return failures


# --- Data piping {{stepN.field}} ------------------------------------------

_REF_RE = re.compile(r"^\{\{step(\d+)\.([a-zA-Z0-9_.]+)\}\}$")


_ACTION_VERBS_PRED = {
    "move", "delete", "send", "write", "extract", "create",
    "compress", "compute", "set", "render", "change",
}

# Keyword imperative bilingue (IT+EN) per discriminare query d'azione
# ("uccidi processo X", "kill -9 firefox") da query di stato puro
# ("stato sistema", "uptime"). Usate da Level 3 ADR 0111 e dal safety
# net in TurnLog.write() per decidere se il `final_message` LLM va
# preservato (azione richiesta) o sostituito dal blocco deterministico.
_HEALTH_IMPERATIVE_KEYWORDS = (
    "kill", "uccidi", "ferma", "termina", "stop ", "spegni",
    "manda", "invia", "scrivi", "esegui", "lancia", "riavvia", "restart",
)


# Executor transformative single-shot: dopo ok:True con _undo registrato,
# il runtime forza final_answer per evitare che il planner LLM oscilli e
# crei duplicati. ADR 0123 + bug live turn c627784c (11/5/2026 sera).
# Estendere solo per executor che creano UNA singola entita' remota per
# invocazione (set_*, send_*, write_* su provider esterni).
_AUTO_FINAL_TRANSFORMATIVE = frozenset({
    # Post ADR 0128 (12/5/2026): set_events -> create_events,
    # set_files -> share_files_google_workspace, set_files_text -> create_files_text,
    # set_files_xlsx kept but ora rappresenta sheets.update (state upsert),
    # create_files_xlsx aggiunto (sheets.create), set_messages aggiunto
    # (gmail.modify), write_files_text aggiunto (docs.append).
    "create_events",
    "send_messages_google_workspace",
    "send_messages",
    "write_files_google_workspace",
    "write_files_text",
    "share_files_google_workspace",
    "create_files_text",
    "create_files_xlsx",
    "set_files_xlsx",
    "set_messages",
    "create_dirs_google_workspace",
})


# P3 (12/5/2026) — Detection congiunzioni multi-step nella user_query.
# Trigger: turn b1d9c236 «fissa appuntamento il prossimo mercoledi mattina
# dopo le 9 per un ora se c'è posto E MANDAMI UNA EMAIL DI CONFERMA» →
# set_events ok → _AUTO_FINAL_TRANSFORMATIVE chiuse il turno → la parte
# «e mandami email di conferma» mai eseguita.
# Soluzione: regex IT+EN word-boundary su congiunzioni seguite da verbo
# d'azione esplicito. NON usiamo " e " generico per evitare falsi positivi
# («fissa o mercoledi o giovedi» = alternativa, «email e telefono» = lista
# noun). §7.9 deterministico, niente LLM.
# Congiunzioni STRUTTURALI di continuita' multi-step. Solo marker linguistici
# universali (no verbi enumerati): `_query_has_continuation` rileva multi-step
# anche via classe semantica (>=2 verbi canonici distinti — derivato da
# prefilter._VERB_TO_CANONICAL vocab IT+EN). §7.3 NON hardcoded enumerazione.
_MULTISTEP_CONJUNCTIONS_RE = re.compile(
    r"\b(e\s+poi|e\s+dopo|e\s+inoltre|e\s+anche|"
    r"inoltre|poi|dopodiche'|dopodiche|"
    r"and\s+then|and\s+also|and\s+after|"
    r"moreover|then|afterwards|additionally)\b",
    re.IGNORECASE,
)


def _query_has_continuation(query: str) -> bool:
    """True se la query e' multi-step esplicito. Detection a 2 strati:

    1. Congiunzione strutturale di continuita' (regex universale IT+EN).
    2. Classe semantica: 2+ verbi canonici distinti nel query (derivato da
       prefilter._VERB_TO_CANONICAL, gia' contiene sinonimi IT+EN per le
       22 azioni di vocab.ACTIONS). §7.3 generale, non enumerativa.

    Esempi positivi:
      - «fissa appuntamento ... e mandami email di conferma» (set+send)
      - «book meeting friday and send confirmation» (set+send + «and»)
      - «mandami l'email e fissa l'appuntamento» (send+set inverso)
    Esempi negativi:
      - «fissa o mercoledi o giovedi» (alternativa, 1 verbo solo)
      - «email e telefono di X» (lista nominale, 0 verbi)
      - «fissa appuntamento mercoledi alle 9» (1 verbo solo)
    Determinismo §7.9: lookup O(len(query)) + token scan vocab, niente LLM.
    """
    if not query or not isinstance(query, str):
        return False
    if _MULTISTEP_CONJUNCTIONS_RE.search(query):
        return True
    # Multi-verb detection via vocab classes. Tokenize semplice + lookup
    # _VERB_TO_CANONICAL (gia' usato da prefilter, IT+EN sinonimi per le 22
    # ACTIONS). 2+ verbi canonici distinti → multi-step.
    try:
        from prefilter import tokenize, detect_canonical_verbs_all
        verbs = detect_canonical_verbs_all(tokenize(query))
        return len(verbs) >= 2
    except Exception:
        return False


def _format_send_messages_detail(obs: dict) -> str:
    """Costruisce il `detail` di MSG_TRANSFORMATIVE_AUTO_FINAL per send_messages.

    Pattern: «a <recipient_id> «<subject>»». Helper §7.9 (no LLM). Idem
    `_format_..._detail` per altri executor che non espongono htmlLink/id ma
    hanno una shape `results[0]` con campi user-facing standardizzati.
    """
    if not isinstance(obs, dict):
        return ""
    r0 = (obs.get("results") or [{}])[0]
    if not isinstance(r0, dict):
        return ""
    to = r0.get("recipient_id") or r0.get("target") or ""
    if isinstance(to, list):
        to = ", ".join(str(x) for x in to)
    subj = r0.get("subject") or ""
    if to and subj:
        return f"a {to} «{subj}»"
    if to:
        return f"a {to}"
    return subj


_MUTATING_VERBS = frozenset({
    # Sottoinsieme delle 23 ACTIONS §2.2 con side-effect remoto:
    # send, create, delete, set, write, move, share, change, render.
    # NON include: read, find, get, list, filter, sort, group, classify,
    # describe, compute, compare, order, extract, compress.
    "send", "create", "delete", "set", "write", "move", "share",
    "change", "render",
})


def _all_query_verbs_satisfied(query: str, executed_tools: list[str]) -> bool:
    """True se TUTTI i verbi MUTATING della query sono coperti da almeno
    uno degli `executed_tools` (prefisso `<verb>_`). Verbi read-only/
    suggestion (find/get/read/list/filter/describe/...) sono opzionali per
    la chiusura pipeline. Determinismo §7.9.

    Bug live turn 7f7381d2dba24bdc (14/5/2026, propose+choose+notify): dopo
    send_messages ok=True il PLANNER rifaceva find_events_empty perche'
    auto_final_transformative era inibito da `_query_has_continuation` (la
    query ha 2 verbi: `proponi`→describe + `mandami`→send). Pipeline e'
    completa quando il verbo MUTATING (`send`) e' stato eseguito; il
    `describe` di «proponi» e' P5 suggestion-semantics, copertura
    soddisfatta dal `find_events_empty + get_inputs` upstream.

    `executed_tools` deve essere la lista dei tool name (chosen_name) degli
    step ok=True inclusi i `resumed_from_prior_turn` (lo scratchpad
    ricostruito dal callback resume_planner). Caller responsabile.
    """
    if not query or not isinstance(query, str) or not executed_tools:
        return False
    try:
        from prefilter import tokenize, detect_canonical_verbs_all
        verbs = detect_canonical_verbs_all(tokenize(query))
        mutating = [v for v in verbs if v in _MUTATING_VERBS]
        if not mutating:
            return False
        executed_verbs = {
            t.split("_", 1)[0] for t in executed_tools if isinstance(t, str) and "_" in t
        }
        return all(v in executed_verbs for v in mutating)
    except Exception:
        return False


# P6 (12/5/2026) — Notify-continuation detection per multi-pipeline.
# Trigger: turn 35431172 «proponi N orari ... E MANDAMI EMAIL con la
# scelta». Atteso: pipeline propose-and-notify (variant a/b) con
# send_messages come step finale dopo get_inputs (e create_events
# eventualmente in mezzo).
# Pattern: marker "notify" (mandami/inviami/notificami/avvisami + email/
# notifica/conferma) + congiunzione («e», «and», ", e") che precede.
# §7.9 deterministico, niente LLM.
# La regex e' DISTINTA da `_MULTISTEP_CONJUNCTIONS_RE`: qui catturiamo
# il VERBO di notifica esplicito + il MEZZO (email/notifica/messaggio/
# telegram/conferma), non solo la congiunzione strutturale.
_NOTIFY_CONTINUATION_RE = re.compile(
    r"("
    # IT verbi notify con enclitici tipici mi/ci/gli — token interi via \b.
    r"\b(?:mandami|inviami|spediscimi|notificami|avvisami|scrivimi)\b|"
    r"\bfammi\s+sapere\b|"
    # Forma "e/poi + verbo + (article)? + (mezzo)": multi-step strutturale
    # con marker esplicito del mezzo. NB: \b iniziale prima della cong.
    r"\b(?:e|and|poi)\s+(?:mi\s+)?(?:mandi|invii|spedisci|notifichi|avvisi|"
    r"invia|manda|notifica|avvisa)\s+(?:una\s+|un\s+|la\s+)?"
    r"(?:email|mail|messaggio|notifica|conferma|sms|telegram|whatsapp)\b|"
    # EN verbi notify
    r"\b(?:email|notify|alert|message|text|ping)\s+me\b|"
    r"\bsend\s+me\s+(?:a\s+|an\s+)?(?:email|message|text|notification|notify)\b|"
    r"\blet\s+me\s+know\b|"
    # Marker del MEZZO di notifica esplicito: «via email», «via telegram».
    r"\bvia\s+(?:email|mail|telegram|sms|whatsapp|notifica|notification|message)\b|"
    # Coda «+ invia conferma», «and send confirmation»: cong NON-word (+/,)
    # OPPURE word (e/and/poi). Senza \b sul prefix per ammettere `+`/`,`.
    # Lookbehind senza fixed-width: usiamo char-class al posto di alternation.
    r"(?:[\s\+,]|\b)(?:e|and|poi|\+|,)\s+(?:invia|manda|notifica|send|notify)\s+"
    r"(?:una\s+|un\s+|la\s+|a\s+|an\s+|the\s+)?"
    r"(?:conferma|confirmation|notifica|notification|messaggio|email|mail)\b|"
    # Verbo notify standalone dopo cong NON-word/word (end-of-clause):
    # «+ notifica», «and notify» a fine richiesta. Implica «notify the user».
    r"(?:[\s\+,]|\b)(?:e|and|poi|\+|,)\s+(?:notifica|notify)(?=\s*[.!?]|\s*$)|"
    # Cong NON-word + <noun_medium> [<noun_conferma>]: «+ email conferma»,
    # «, email confirmation», «+ telegram avviso». Forma ellittica del
    # verbo notify (verbo sottinteso, mezzo+oggetto espliciti). Solo per
    # congiunzioni NON-word (+/,) che marcano gia' lo step separato; un
    # verbo coniugato «e/and/poi» da solo NON triggera questa branch per
    # evitare falsi positivi (es. «cerca email» — congiunzione word senza
    # ellissi verbale).
    r"(?:\s*[\+,])\s+"
    r"(?:una\s+|un\s+|la\s+|a\s+|an\s+|the\s+)?"
    r"(?:email|mail|telegram|sms|whatsapp|notifica|notification|messaggio|message)\s*"
    r"(?:di\s+|of\s+)?"
    r"(?:conferma|confirmation|riassunto|summary|notifica|notification|"
    r"avviso|alert|update|aggiornamento)?\b"
    r")",
    re.IGNORECASE,
)


def _query_has_notify_continuation(query: str) -> bool:
    """True se la query contiene una continuation di notifica esplicita
    diretta all'utente.

    Esempi positivi:
      - «... e mandami email con la scelta»
      - «... e notificami il risultato»
      - «... and email me the choice»
      - «... and let me know»
      - «... con conferma via email»
    Esempi negativi (devono ritornare False):
      - «cerca email» (verbo search, non notify)
      - «leggi le email di oggi» (verbo read)
      - «email di Mario» (sostantivo, no verbo notify)
    Determinismo §7.9: regex O(len(query)), niente LLM.
    """
    if not query or not isinstance(query, str):
        return False
    return bool(_NOTIFY_CONTINUATION_RE.search(query))


# P4 (12/5/2026) — Availability marker detection per check_availability.
# Trigger: turn b1d9c236 «... SE C'È POSTO» → planner ha chiamato set_events
# direttamente senza read_events. Il workflow (check_availability) di
# calendar.j2 era ignorato.
# Defense in depth: il runtime intercetta set_events quando la query ha
# un availability marker e read_events NON e' nei step precedenti.
# Soluzione (c): post-hoc reject + hint, lascia che il planner ri-pianifichi.
# Determinismo §7.9: regex deterministico, niente LLM.
_AVAILABILITY_MARKERS_RE = re.compile(
    r"\b("
    # IT
    r"se\s+c['’]?[eè]\s+(un\s+)?(posto|buco|slot|spazio|tempo)|"
    r"se\s+(la\s+finestra|lo\s+slot)\s+[eè]['\s]*libera|"
    r"se\s+sono\s+libero|se\s+sei\s+libero|"
    r"se\s+non\s+ho\s+(altro|impegni|gi[aà])|"
    r"verifica\s+(la\s+)?disponibilit[aà]|controlla\s+(la\s+)?disponibilit[aà]|"
    r"se\s+disponibile|"
    # EN
    r"if\s+(it['’]?s\s+)?available|if\s+(i\s+am|i['’]?m)\s+free|"
    r"if\s+there['’]?s\s+(a\s+)?(slot|opening|space|time)|"
    r"if\s+free|check\s+availability|"
    r"if\s+(the\s+)?(slot|window)\s+is\s+free"
    r")\b",
    re.IGNORECASE,
)


# Tool che CREANO eventi calendar: derivati dal catalog al call-time
# (verb in classe trasformativa + object="events"). §7.3 NON hardcoded.
# Cache LRU al boot per evitare scan ad ogni gate check.
def _calendar_write_tools() -> frozenset:
    """Set di executor name che scrivono sul calendario.
    Derivato dal catalog (`verb in {set, create} AND object == "events"`).
    Cache modulo-level invalidata solo a reload manuale (no overhead per-call).
    """
    cached = getattr(_calendar_write_tools, "_cached", None)
    if cached is not None:
        return cached
    try:
        from loader import load_catalog
        from vocab import canonical_object
        names = set()
        for ex in load_catalog():
            name = ex.name
            if "_" not in name:
                continue
            verb, _, obj_raw = name.partition("_")
            if verb not in ("set", "create"):
                continue
            # canonical_object riconosce sinonimi (events/eventi/appuntamenti).
            # Per executor naming il suffisso e' gia' canonico, ma normalizziamo
            # per robustezza (es. send_messages_google_workspace).
            obj_canon = canonical_object(obj_raw.split("_")[0])
            if obj_canon == "events":
                names.add(name)
        result = frozenset(names)
    except Exception:
        # Fallback graceful se catalog non disponibile (test/boot iniziale):
        # almeno create_events e' guaranteed-canonical (post ADR 0128, era set_events).
        result = frozenset({"create_events"})
    _calendar_write_tools._cached = result
    return result


def _invalidate_calendar_write_tools_cache():
    """Per test: forza re-derivation da catalog al prossimo call."""
    if hasattr(_calendar_write_tools, "_cached"):
        del _calendar_write_tools._cached


def _query_requires_availability_check(query: str) -> bool:
    """True se la query richiede availability check pre-set_events.

    Esempi positivi:
      - «se c'è posto», «se sono libero», «se non ho altro»
      - «verifica disponibilità», «if there's a slot», «if free»
    Esempi negativi:
      - «fissa appuntamento mercoledi alle 9» (no marker)
      - «book meeting friday» (no marker)
    Determinismo §7.9: regex lookup O(len(query)), niente LLM.
    """
    if not query or not isinstance(query, str):
        return False
    return bool(_AVAILABILITY_MARKERS_RE.search(query))


# P5 (12/5/2026) — Propose-intent detection per gate suggestion vs destructive.
# Trigger: turn a0b96f6f (12/5/2026 09:07) → query «proponi 3 orari per
# appuntamento la prossima settimana mattina» → planner ha chiamato
# set_events con summary="Appuntamento Proposto 1" e finestra 8:00-12:00
# lunedi-sabato (whole-week blob destructive). Atteso: read_events + final
# testuale con N slot computati. NESSUN set_events.
#
# Regex SEMANTICA UNIVERSALE: cattura verbi di suggerimento IT+EN con
# eventuali enclitici (mi/ti/ci/gli) tramite quantifier, NON enumerazione
# enclitica esaustiva. Cattura anche le formulazioni interrogative tipiche
# («che ne dici», «what about», «quali sono N ... liberi»). Determinismo
# §7.9: regex compilata, niente LLM nel runtime gate.
#
# Pattern espliciti per «quali sono N X liberi/disponibili» perche' la
# costruzione «quali sono i miei impegni» (read events) NON deve triggerare
# il gate (la query e' read, non suggerimento di nuovo slot).
_PROPOSE_INTENT_RE = re.compile(
    r"(?:\b|^)("
    # IT — verbi suggestion con eventuali enclitici (mi/ti/ci/gli/mela/...)
    # Forma generale: stem + opzionale enclitico. Compatto via quantifier.
    # Stem + opzionale enclitico (mi/ti/ci/gli/cela/...) — quantifier-based,
    # NON enumerazione esaustiva. La forma `\w{1,5}?` cattura enclitici e
    # desinenze di coniugazione (-armi -arci -ami -ate -ano -ebbe ...).
    r"propon[a-z]{1,5}|propor[a-z]{2,7}|"
    r"suggeris[a-z]{1,5}|sugger[a-z]{2,7}|"
    r"raccomand[a-z]{1,6}|"
    # IT — formulazioni interrogative tipiche di richiesta suggerimento.
    # Pattern «che [ne] dici», «cosa [ne] pensi», «che dici», «consigliami N»
    r"che(?:\s+ne)?\s+dici|cosa(?:\s+ne)?\s+(?:dici|pensi)|"
    r"consigli[a-z]{1,5}|"
    # IT — «quali (sono|fasce|orari|slot|...) ... liber[ie]/disponibil[ie]/...»
    # Pattern semantico: parola interrogativa «quali» seguita entro la frase
    # da un marker di disponibilita'/vacuita'. La distanza max 0-6 tokens.
    r"quali\s+(?:\w+\s+){0,6}(?:liber[ie]|disponibil[ie]|aperte?|vuoti?|vuote)|"
    # IT — «N alternative/opzioni/slot/orari/fasce/mattine/proposte».
    # Forma con numero (3/2/...) + sostantivo proposta-like. Cattura
    # «dammi 3 alternative», «cerca 3 slot 9-11», «alcune proposte»,
    # «2 mercoledi liberi», «qualche slot». Indipendente dal verbo
    # principale (cerca/dammi/voglio/etc.: il SOSTANTIVO + il NUMERO
    # bastano a inferire "richiesta di N opzioni" semanticamente).
    # Lista sostantivi: alternative/opzioni/proposte sono universali
    # proposal-noun; slot/orari/fasce/mattine/pomeriggi/giorni-settimana
    # sono dominio calendar (parte di `_OBJECT_HINTS["events"]`).
    r"(?:\d+|alcun[ie]|qualche|alcune|alcuni|some)\s+"
    r"(?:opzion[ie]|alternativ[ae]|propost[ae]|slot|slots|orari[oi]?|"
    r"fasce?|mattine?|pomeriggi|finestre?|"
    r"mercoled[ìi]|luned[ìi]|marted[ìi]|"
    r"gioved[ìi]|venerd[ìi]|sabat[oi]|domenic[ah]e?)|"
    # EN — verbs (gerund/3rd, infinitive)
    r"propose|proposes|proposing|"
    r"suggest|suggests|suggesting|"
    r"recommend|recommends|recommending|"
    # EN — interrogative
    r"what\s+about|how\s+about|"
    # EN — «what slots/times/X (are) free/available/open»: marker dispon-
    # bilita' su sostantivo plurale. Stessa logica di «quali» IT.
    r"what\s+(?:\w+\s+){0,4}(?:are\s+|is\s+)?(?:free|available|open)|"
    r"which\s+(?:\w+\s+){0,4}(?:are\s+|is\s+)?(?:free|available|open)|"
    # EN — «any free X», «any open X» — domanda «c'e' / ce ne sono?»
    # Restringo al dominio calendar via lista nomi temporal: slot/time/window.
    r"any\s+(?:free|available|open)\s+(?:slots?|times?|windows?|mornings?|afternoons?|days?|appointments?|meetings?)|"
    # EN — «N options/alternatives/slots/morning times/...» (with optional
    # preceding politeness verb: give me / I'd like / I want / can you).
    # Lista nomi RISTRETTA al dominio proposal/calendar:
    # options/alternatives/proposals = universal proposal-noun;
    # slots/times/mornings/afternoons/openings = calendar dominio.
    # Esclude generici (emails/files/messages) per evitare falsi positivi.
    r"(?:\d+|some|a\s+few|several|any)\s+"
    r"(?:morning\s+|afternoon\s+|free\s+|available\s+|open\s+|"
    r"alternative\s+|proposed?\s+)?"
    r"(?:options?|alternatives?|proposals?|slots?|times?|"
    r"mornings?|afternoons?|openings?|windows?)"
    r")(?:\b|$)",
    re.IGNORECASE,
)


def _query_is_propose_intent(query: str) -> bool:
    """True se la query e' propose-intent (richiesta di suggerimento/proposta
    di alternative), NON di creazione/modifica destrutiva.

    Esempi positivi:
      - «proponi 3 orari per appuntamento»
      - «suggeriscimi 2 mercoledi liberi»
      - «raccomandami una mattina libera»
      - «che ne dici di lunedi 9-10»
      - «quali sono le mattine libere prossima settimana»
      - «propose 3 morning times», «suggest a meeting time»
      - «what are 3 free slots tomorrow», «give me 3 options»

    Esempi negativi (devono ritornare False):
      - «fissa appuntamento mercoledi alle 9»  (set destructive)
      - «book a meeting friday»  (set destructive)
      - «quali sono i miei impegni domani»  (read events, no «liberi/disponibili»)
      - «crea evento lunedi»  (create destructive)

    Determinismo §7.9: regex O(len(query)), niente LLM.
    """
    if not query or not isinstance(query, str):
        return False
    return bool(_PROPOSE_INTENT_RE.search(query))


def _has_prior_read_events_ok(steps) -> bool:
    """True se uno dei step precedenti e' read_events con ok=True.

    Usato dai gate P4/P5 per non bloccare set_events quando il check
    availability/read gia' fatto. §7.9 lookup deterministico.
    """
    for s in steps:
        tool = getattr(s, "chosen_tool", None)
        if tool != "read_events":
            continue
        res = getattr(s, "result", None)
        if isinstance(res, dict) and res.get("ok") is True:
            return True
    return False


# Bug 12/5/2026 resume PLANNER dopo dialog pick (propose+notify continuation).
# Euristica deterministica §7.9 per detectare MID-pipeline get_inputs: il
# PLANNER ha emesso get_inputs ma la query contiene una continuation
# (notify/create/move/...) che richiede un altro step dopo il pick.
_RESUME_AFTER_DIALOG_HINTS_IT = (
    "mandami", "manda", "inviami", "invia", "notificami",
    "scrivimi", "avvisami", "informami",
    "e poi crea", "e poi prenota", "e poi fissa", "e poi sposta",
    "e poi cancella", "e poi invia", "e poi manda",
    "e crea", "e prenota", "e fissa", "e sposta", "e cancella",
    "e invia", "e manda",
)
_RESUME_AFTER_DIALOG_HINTS_EN = (
    "send me", "notify me", "tell me", "email me", "let me know",
    "and create", "and book", "and schedule", "and move",
    "and delete", "and send", "and notify",
    "then create", "then book", "then schedule", "then send",
)


def _should_resume_planner_after_dialog(query: str, route_info,
                                          history_for_refs) -> bool:
    """True se il get_inputs corrente e' MID-pipeline e il turno deve
    riprendere dopo il pick dell'utente (bug 12/5/2026 propose+notify).

    Sources di evidenza (OR):
      1. route_info ha uno dei marker `multi_pipeline_*` (propose+notify
         o notify-only) — il rank ha gia' classificato la query come
         multi-pipeline.
      2. Hint linguistici di continuation (mandami/manda/invia/notify/
         create/...): notify e action-verb residuo dopo il dialog pick.

    Determinismo §7.9: nessun LLM. Restituisce bool.

    DEVI: ritornare True solo se la query contiene una continuation
    legittima oltre il dialog.
    NON DEVI: triggerare resume per query single-step (es. solo
    «proponi 3 orari» senza «e mandami»).
    OK: «proponi 3 orari e mandami email» → True.
    ERRORE: «mostrami 3 orari» → False (solo display, no action verb).
    """
    if not isinstance(query, str) or not query.strip():
        return False
    # Source 1: route_info marker (set in run_turn quando la query e'
    # propose+notify o notify-only).
    if isinstance(route_info, dict):
        if (route_info.get("multi_pipeline_propose_notify")
                or route_info.get("multi_pipeline_notify_only")):
            return True
    # Source 2: hint linguistici espliciti.
    q_low = query.lower()
    for hint in _RESUME_AFTER_DIALOG_HINTS_IT:
        if hint in q_low:
            return True
    for hint in _RESUME_AFTER_DIALOG_HINTS_EN:
        if hint in q_low:
            return True
    return False


def _inject_get_inputs_choice_for_propose(*, entries: list,
                                            object_canonical: str
                                            ) -> dict | None:
    """Costruisce args deterministici per `get_inputs(kind=choice)` quando
    siamo in pipeline propose+notify dopo `find_events_empty` (ADR 0129
    extended, 14/5/2026 sera).

    Determinismo §7.9: nessun LLM, lookup tabellare per object_canonical
    (oggi solo `events`; estendere quando emerge altro pattern).
    """
    if not entries:
        return None
    if object_canonical == "events":
        return {
            "title": "Scegli l'orario per l'appuntamento",
            "entries": entries,
            "dialog": [{
                "var": "scelta",
                "prompt": "Quale orario preferisci per l'appuntamento?",
                "schema": {
                    "kind": "choice",
                    "display_template": "{when_human}",
                    "value_field": "start",
                },
            }],
        }
    return None


def _snapshot_scratchpad(history_for_refs) -> list:
    """Snapshot serializzabile JSON dello scratchpad per il resume callback.

    Filtra step troppo pesanti (entries molto lunghe): truncation a max 50
    entries per step + max 10KB per observation totale (heuristica safe).
    Determinismo §7.9.
    """
    out: list = []
    for h in (history_for_refs or []):
        if not isinstance(h, dict):
            continue
        obs = h.get("observation") or {}
        # Trim entries lunghe: il PLANNER continuation vede sintesi, non
        # blob full. Se servono dettagli il PLANNER puo' ri-leggere.
        if isinstance(obs, dict):
            obs_trimmed = dict(obs)
            entries = obs_trimmed.get("entries")
            if isinstance(entries, list) and len(entries) > 50:
                obs_trimmed["entries"] = entries[:50]
                obs_trimmed["_entries_truncated"] = True
                obs_trimmed["_entries_total"] = len(entries)
        else:
            obs_trimmed = obs
        out.append({
            "step": int(h.get("step") or 0),
            "tool": h.get("tool") or "",
            "args": h.get("args") or {},
            "observation": obs_trimmed,
        })
    return out


_AUTO_FINAL_SKIP_TOOLS = frozenset({
    "scratchpad_read", "filter_entries", "classify_entries", "describe_entries",
})


# Verbi non-action (read-only/discovery/pure-compute) soggetti al cap
# per-turn rinforzato (8/5/2026 notte). Identificati dal prefisso del
# tool name (azione_oggetto). I verbi action restano protetti dalle
# guardie pre-esistenti (vaglio, cyclic, duplicate).
_NON_ACTION_VERB_PREFIXES = frozenset({
    "find", "get", "list", "read", "classify", "filter",
    "describe", "compute", "compare", "sort", "group",
})


def _is_non_action_tool(tool_name: str) -> bool:
    """True se il tool e' un verbo non-action (cf. `_NON_ACTION_VERB_PREFIXES`).

    Estrae il prefisso azione dal nome `azione_oggetto[_qualifier]`.
    Ritorna False per `final_answer`, scratchpad_read e tool senza
    underscore (sicurezza per nomi atipici).
    """
    if not tool_name or "_" not in tool_name:
        return False
    if tool_name == "scratchpad_read":
        return True  # scratchpad_read e' read-only data-piping
    verb = tool_name.split("_", 1)[0]
    return verb in _NON_ACTION_VERB_PREFIXES


def _tokenize_for_dup(value):
    """Estrae token-set ordinato da uno scalare/lista per Jaccard.

    Strip whitespace, lowercase, split su whitespace + punteggiatura
    semplice. Numeri, path e identificatori opachi restano interi.
    """
    if value is None:
        return frozenset()
    if isinstance(value, (list, tuple)):
        out = set()
        for v in value:
            out |= _tokenize_for_dup(v)
        return frozenset(out)
    if isinstance(value, dict):
        out = set()
        for v in value.values():
            out |= _tokenize_for_dup(v)
        return frozenset(out)
    s = str(value).strip().lower()
    if not s:
        return frozenset()
    # Split su whitespace e punteggiatura "soft" — preserva path e URL.
    tokens = re.split(r"[\s,;|]+", s)
    return frozenset(t for t in tokens if t)


def _normalize_args_for_dup(args):
    """Normalizza dict args per confronto duplicate-near-identical.

    Ritorna dict con:
      - Chiavi ordinate.
      - Stringhe whitespace-stripped + lowercase.
      - Liste di stringhe ordinate.
      - Campi `topic`/`query`/`pattern`/`q` ridotti a token-set ordinato
        (sorted tuple) per Jaccard cross-call.
    Argomenti mancanti / None / liste vuote: rimossi.
    """
    if not isinstance(args, dict):
        return {}
    _SEMANTIC_FIELDS = {"topic", "query", "pattern", "q", "search", "text"}
    out = {}
    for k in sorted(args.keys()):
        v = args[k]
        if v is None:
            continue
        if isinstance(v, str):
            v2 = v.strip().lower()
            if not v2:
                continue
            if k in _SEMANTIC_FIELDS:
                out[k] = tuple(sorted(_tokenize_for_dup(v2)))
            else:
                out[k] = v2
        elif isinstance(v, (list, tuple)):
            if not v:
                continue
            if k in _SEMANTIC_FIELDS:
                out[k] = tuple(sorted(_tokenize_for_dup(v)))
            else:
                # Lista di stringhe → strip+lowercase+ordinata; altri tipi → repr stabile.
                norm = []
                for item in v:
                    if isinstance(item, str):
                        norm.append(item.strip().lower())
                    else:
                        norm.append(repr(item))
                out[k] = tuple(sorted(norm))
        elif isinstance(v, dict):
            sub = _normalize_args_for_dup(v)
            if sub:
                out[k] = tuple(sorted(sub.items()))
        else:
            out[k] = v
    return out


def _args_jaccard(a_norm, b_norm) -> float:
    """Jaccard token-set fra due args normalizzati su campi semantici.

    Se nessuno dei due ha campi semantici (topic/query/pattern/...),
    fall-back: ritorna 1.0 sse i dict normalizzati sono uguali, 0.0 altrimenti.
    """
    _SEMANTIC_FIELDS = {"topic", "query", "pattern", "q", "search", "text"}
    a_tokens = set()
    b_tokens = set()
    for k in _SEMANTIC_FIELDS:
        if k in a_norm and isinstance(a_norm[k], tuple):
            a_tokens |= set(a_norm[k])
        if k in b_norm and isinstance(b_norm[k], tuple):
            b_tokens |= set(b_norm[k])
    if not a_tokens and not b_tokens:
        return 1.0 if a_norm == b_norm else 0.0
    if not a_tokens or not b_tokens:
        return 0.0
    inter = a_tokens & b_tokens
    union = a_tokens | b_tokens
    return len(inter) / len(union) if union else 0.0


_AUTO_FINAL_PREFER_READ_OVER_DISCOVERY = (
    # find_urls e' discovery (URL+title+snippet); read_urls_html ha
    # contenuto reale. Quando entrambi sono presenti e il read ha text
    # non-banale, preferisci il read come fonte di final_message.
    {"find_urls"},
    {"read_urls_html", "read_urls_pdf", "get_urls_text"},
)


def _compose_final_message_from_obs(lp_tool, lp_obs):
    """Compose final_message dal `last_productive` (tool, obs).

    Estrazione del detail dalla observation con precedenza:
      detail_md > summary > final_message_hint > message > results/entries.
    Ritorna (final_message, ok_count, n_above_threshold).
    Riusato da auto_final_on_duplicate e cap_max_per_turn (8/5/2026 notte).
    """
    ok_count, n_above_threshold = _extract_auto_final_count(lp_obs)
    explicit_detail_md = (
        lp_obs.get("detail_md") if isinstance(lp_obs, dict) else None
    )
    explicit_summary = (
        lp_obs.get("summary") if isinstance(lp_obs, dict) else None
    )
    explicit_hint = (
        lp_obs.get("final_message_hint") if isinstance(lp_obs, dict) else None
    )
    explicit_message = (
        lp_obs.get("message") if isinstance(lp_obs, dict) else None
    )
    detail = None
    if explicit_detail_md and isinstance(explicit_detail_md, str):
        detail = explicit_detail_md.strip()[:1500]
    elif explicit_summary and isinstance(explicit_summary, str):
        detail = explicit_summary.strip()[:400]
    elif explicit_hint and isinstance(explicit_hint, str):
        detail = explicit_hint.strip()[:600]
    elif explicit_message and isinstance(explicit_message, str):
        detail = explicit_message.strip()[:400]
    else:
        results = (lp_obs.get("results") or []) if isinstance(lp_obs, dict) else []
        entries_list = (lp_obs.get("entries") or []) if isinstance(lp_obs, dict) else []
        summary_bits = []
        for r in results[:5]:
            if not isinstance(r, dict):
                continue
            if r.get("to") and r.get("subject"):
                summary_bits.append(f"a {','.join(r['to']) if isinstance(r['to'], list) else r['to']} «{r['subject']}»")
            elif r.get("path"):
                summary_bits.append(r["path"])
            elif r.get("dst"):
                d = r["dst"]; folder = d.get("folder") if isinstance(d, dict) else d
                summary_bits.append(f"→ {folder}")
        if not summary_bits:
            for e in entries_list[:3]:
                if not isinstance(e, dict):
                    continue
                for k in ("name", "subject", "path", "title", "url",
                          "signature", "kind"):
                    v = e.get(k)
                    if v:
                        summary_bits.append(str(v)[:80])
                        break
        detail = "; ".join(summary_bits) if summary_bits else None
    if ok_count is None and explicit_message and isinstance(explicit_message, str):
        final_message = f"{lp_tool}: {explicit_message.strip()}"
    else:
        count_str = _format_auto_final_count(ok_count, n_above_threshold)
        final_message = msg(
            "MSG_AUTO_FINAL_COMPLETED",
            tool=lp_tool, count_str=count_str,
            detail=(detail if detail else msg("MSG_AUTO_FINAL_NO_DETAIL")),
        )
    return final_message, ok_count, n_above_threshold


# Vectorial enforcement helpers (ADR 0130, 12/5/2026).
# Bug live turn `8f8080c0` (12/5/2026, 13min): find_events_empty x9 consecutivi
# con args che variavano solo `time_windows` -> DUPLICATE_CALL non scattava (args
# diff), cap_same a 10 troppo permissivo per executor vettoriali. Anti-pattern
# §2.1: un executor che accetta args plurali (paths/urls/time_windows/...) DEVE
# essere chiamato UNA volta con N args, NON N volte. Detection deterministica via
# manifest introspection (`args_schema.properties[arg].type == "array"`), zero
# whitelist hardcoded §7.3. Cap_same custom 2 per executor vettoriali (vs 10
# default), perche' la chiamata seguente alla prima ok=True su un vettoriale e'
# sempre un retry del LLM che non aggiunge lavoro utile (segno di confusione su
# §2.1, non di esplorazione legittima).
_VECTORIAL_CAP_SAME = 2  # cap_same per executor vettoriali (vs DEFAULT_CAP_SAME_EXECUTOR=10)
_VECTORIAL_ARG_TYPE_ARRAY = "array"  # JSON Schema type marker per args plurali


@functools.lru_cache(maxsize=512)
def _executor_has_plural_args(executor_name: str, schema_signature: str) -> bool:
    """True se l'executor accetta almeno un arg plurale (lista) nel suo schema.

    Detection introspettiva del `args_schema.properties`: cerca proprieta' con
    `type=="array"` (JSON Schema). Determinismo §7.9: niente LLM, niente
    whitelist hardcoded. Si applica a TUTTI gli executor vettoriali §2.1.

    `schema_signature` e' una stringa stabile derivata dallo schema (sorted
    properties + type), usata come chiave di cache: invalida automaticamente
    al re-firma dell'executor (manifest re-loaded).

    DEVI: passare il signature dal caller (vedi `_vectorial_schema_signature`).
    NON DEVI: ispezionare l'oggetto Executor in cache (non hashable).
    """
    # Parser leggero del signature: "name:type;name:type;..." con type "array"
    # come marker per detection. Se non c'e' "array" nel signature, nessun
    # plural arg presente.
    return ":array" in schema_signature or ";array" in schema_signature


def _vectorial_schema_signature(args_schema: dict | None) -> str:
    """Stringa stabile derivata da `args_schema.properties` per cache key.

    Format: "name1:type1;name2:type2;..." (sorted by name). Tipi normalizzati a
    lowercase (json schema usa lowercase). Lascia "" se schema vuoto o malformed.
    Riusa solo type del top-level (no nested items.type analysis, sufficient
    per detection plural).
    """
    if not isinstance(args_schema, dict):
        return ""
    props = args_schema.get("properties") or {}
    if not isinstance(props, dict) or not props:
        return ""
    parts = []
    for name in sorted(props.keys()):
        prop = props.get(name) or {}
        t = (prop.get("type") if isinstance(prop, dict) else None) or ""
        parts.append(f"{name}:{str(t).lower()}")
    return ";".join(parts)


def _cap_same_for_executor(executor, default_cap: int) -> int:
    """Cap_same dedicato per `executor`: 2 se vettoriale (plural args), default
    altrimenti. ADR 0130 §2.1: executor vettoriali devono essere chiamati una
    volta con N args, non N volte. La soglia bassa previene il thrashing del
    LLM che varia gli args sperando in un risultato diverso.
    """
    if executor is None:
        return default_cap
    sig = _vectorial_schema_signature(getattr(executor, "args_schema", None))
    if _executor_has_plural_args(executor.name, sig):
        return _VECTORIAL_CAP_SAME
    return default_cap


# Bug live turn `eb837329` (11/5/2026): final_message su loop_break era una
# stringa hardcoded ("file extension, precise path") che parlava di file
# anche per query su events/messages/urls. Soluzione: hint parametrico per
# OBJECT dell'intent, tabella deterministica in `i18n.sqlite` (ADR 0104).
# Determinismo §7.9: mapping OBJECT -> chiave i18n, niente LLM.
_LOOP_BREAK_HINT_OBJECTS = frozenset({
    "files", "dirs", "messages", "events", "urls",
    "images", "processes", "contacts", "credentials",
})


def _loop_break_hint(intent_object: str | None) -> str:
    """Ritorna il hint user-facing parametrico sull'object dell'intent.

    Mappa `intent.object` (lowercase) -> chiave `MSG_LOOP_BREAK_HINT_<OBJ>`
    in `i18n.sqlite`. Fallback `MSG_LOOP_BREAK_HINT_GENERIC` quando l'object
    e' None, vuoto o non in `_LOOP_BREAK_HINT_OBJECTS`. Riusa `messages.get`
    (alias `msg`) per il fallback chain `current_lang -> en -> it`.
    """
    obj = (intent_object or "").strip().lower()
    if obj in _LOOP_BREAK_HINT_OBJECTS:
        text = msg(f"MSG_LOOP_BREAK_HINT_{obj.upper()}")
        # `<missing:KEY>` indica che la chiave non esiste in DB: fallback a generic.
        if not text.startswith("<missing:"):
            return text
    return msg("MSG_LOOP_BREAK_HINT_GENERIC")


def _intent_object_from_route(route_info) -> str | None:
    """Estrae `intent.object` da `route_info`. Robusto a None/missing.

    Riusato dai 6 emit-site di `MSG_LOOP_BREAK` in `run_turn` (5 guard
    branches pre-execute + 1 post-execute fail) per costruire il hint
    object-aware (`_loop_break_hint`).
    """
    if not route_info:
        return None
    intent = route_info.get("intent") if isinstance(route_info, dict) else None
    if not intent:
        return None
    return intent.get("object") if isinstance(intent, dict) else None


def _resolve_auto_final_from_steps(steps):
    """Risolve il `last_productive` per `auto_final_on_duplicate`.

    Walk back fra `steps` saltando data-piping helpers e LLM-narrators
    (`describe_entries`). Ritorna `(lp_tool, lp_obs)` del primo step ok
    productive trovato, oppure dell'ultimo step se nessun candidato.
    Ritorna `(None, {})` se la lista e' vuota.

    Preferenza speciale (turn live federvolley 7/5/2026): quando il
    last_productive e' un executor di "discovery" (find_urls) ma in
    history c'e' un executor di "lettura" (read_urls_html/_pdf/get_urls_text)
    con `text`/`entries` non-vuoti, preferisci il READ — il discovery e'
    metadati, il read e' contenuto.

    L'estrazione esiste per testabilita' (cf. test_auto_final_on_duplicate.py).
    """
    discovery_tools, read_tools = _AUTO_FINAL_PREFER_READ_OVER_DISCOVERY

    last_productive = None
    for prev in reversed(steps):
        res = getattr(prev, "result", None)
        if not isinstance(res, dict) or not res.get("ok"):
            continue
        if getattr(prev, "chosen_tool", None) in _AUTO_FINAL_SKIP_TOOLS:
            continue
        last_productive = prev
        break

    # Override discovery → read se applicabile (cf. ADR 0098)
    if last_productive is not None:
        lp_tool_check = getattr(last_productive, "chosen_tool", None)
        if lp_tool_check in discovery_tools:
            for prev in reversed(steps):
                res = getattr(prev, "result", None)
                if not isinstance(res, dict) or not res.get("ok"):
                    continue
                if getattr(prev, "chosen_tool", None) not in read_tools:
                    continue
                # Verifica che il read abbia contenuto non-banale
                if _read_obs_has_content(res):
                    last_productive = prev
                    break

    if last_productive is None and steps:
        last_productive = steps[-1]
    if last_productive is None:
        return (None, {})
    lp_tool = getattr(last_productive, "chosen_tool", None)
    lp_obs = getattr(last_productive, "result", None) or {}
    return (lp_tool, lp_obs if isinstance(lp_obs, dict) else {})


def _read_obs_has_content(obs: dict) -> bool:
    """Heuristic: l'observation di un read_urls_* ha contenuto utile?

    True se almeno una entry ha `text`/`body` con >= 200 char, oppure
    `summary`/`detail_md` non-vuoto, oppure `final_message_hint`
    descrittivo.
    """
    if not isinstance(obs, dict):
        return False
    for k in ("summary", "detail_md", "final_message_hint"):
        v = obs.get(k)
        if isinstance(v, str) and len(v.strip()) >= 80:
            return True
    entries = obs.get("entries") or obs.get("results") or []
    if isinstance(entries, list):
        for e in entries[:5]:
            if not isinstance(e, dict):
                continue
            for k in ("text", "body", "content"):
                v = e.get(k)
                if isinstance(v, str) and len(v.strip()) >= 200:
                    return True
    return False


def _extract_auto_final_count(lp_obs: dict) -> tuple[int | None, int | None]:
    """Estrae `(ok_count, n_above_threshold)` da un'observation productive.
    `n_above_threshold` solo se strettamente maggiore di `ok_count`.
    """
    if not isinstance(lp_obs, dict):
        return (None, None)
    ok_count = lp_obs.get("ok_count")
    if ok_count is None:
        for k in ("entries", "matches", "results", "files", "paths"):
            v = lp_obs.get(k)
            if isinstance(v, list):
                ok_count = len(v); break
    if ok_count is None:
        for k in ("n_entries", "item_count"):
            v = lp_obs.get(k)
            if isinstance(v, int):
                ok_count = v; break
    nat = lp_obs.get("n_above_threshold")
    if isinstance(nat, int) and isinstance(ok_count, int) and nat > ok_count:
        return (ok_count, nat)
    return (ok_count if isinstance(ok_count, int) else None, None)


def _format_auto_final_count(ok_count: int | None,
                             n_above_threshold: int | None) -> str:
    if not isinstance(ok_count, int):
        return msg("MSG_AUTO_FINAL_COUNT_UNKNOWN")
    if isinstance(n_above_threshold, int):
        return msg("MSG_AUTO_FINAL_COUNT_THRESHOLD",
                   n=ok_count, total=n_above_threshold)
    return msg("MSG_AUTO_FINAL_COUNT_PLAIN", n=ok_count)


def _predict_remaining_path(intent: dict | None, current_tool: str) -> list[str]:
    """Previsione euristica dei prossimi step dato intent + tool corrente.

    Conservative: ritorna al massimo 2 elementi (incertezza alta nei
    multi-step ReAct). Usata SOLO per il rendering del breadcrumb live
    (badge "futuri" muti); il path reale puo' divergere senza danni.

    Logica:
      - current_tool == "final_answer" → []
      - intent.verb in ACTION_VERBS (move/delete/send/...) → ["final_answer"]
        (l'azione di solito chiude il turno, niente describe dopo)
      - current_tool == "describe_entries" → ["final_answer"]
      - producer (read/find/list/get) + intent NON azione → ["describe_entries", "final_answer"]
      - default → ["final_answer"]
    """
    if current_tool == "final_answer":
        return []
    if current_tool == "describe_entries":
        return ["final_answer"]
    verb = (intent or {}).get("verb")
    if verb in _ACTION_VERBS_PRED:
        return ["final_answer"]
    if verb in ("read", "find", "list", "get"):
        return ["describe_entries", "final_answer"]
    return ["final_answer"]


def _lookup_field(obj, dotted):
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None, f"campo '{part}' non trovato"
    return cur, None


def _consumer_match_arg(consumer_schema: dict | None, prev_entries: list) -> str | None:
    """Layer 4 (5/5/2026): rileva l'arg consumer naturale per una lista
    di entries, basandosi sulla convenzione I/O Metnos (plurale↔singolare).

    Esempio: find_urls produce entries=[{url, title, ...}], read_urls_html
    consuma `urls`. Match: arg `urls` → singolare `url` → presente in
    entries[0] → estrai entries[*].url.

    Caso degenere `prev_entries=[]`: non possiamo ispezionare entries[0],
    quindi prendiamo come consumer arg il primo array required dello schema
    (esclusi entries/from_step). Il caller iniettera' lista vuota — l'executor
    decide se ok_count=0 o errore di dominio.

    Ritorna il nome dell'arg consumer (string) o None se nessun match.
    Esclude `entries` stesso (target di fallback gestito dal caller).
    """
    if not isinstance(consumer_schema, dict) or not isinstance(prev_entries, list):
        return None
    props = consumer_schema.get("properties") or {}
    if not isinstance(props, dict):
        return None
    required = consumer_schema.get("required") or []
    if not isinstance(required, list):
        required = []

    # Caso degenere: lista vuota. Prendi il primo array required, escludendo
    # `entries`/`from_step`. Senza required, ritorna None → fallback `entries`.
    if not prev_entries:
        for arg_name in required:
            if arg_name in ("entries", "from_step"):
                continue
            spec = props.get(arg_name)
            if isinstance(spec, dict):
                t = spec.get("type")
                if t and t != "array":
                    continue
            return arg_name
        return None

    if not isinstance(prev_entries[0], dict):
        return None
    sample_keys = set(prev_entries[0].keys())
    # Ranking: prima l'arg required (semantica piu' forte), poi alfabetico stabile.
    candidates = []
    for arg_name, spec in props.items():
        if arg_name == "entries":
            continue  # gestito dal fallback
        if arg_name == "from_step":
            continue
        # Solo arg di tipo array: l'auto-espansione consegna una lista.
        if isinstance(spec, dict):
            t = spec.get("type")
            if t and t != "array":
                continue
        # Singolare = arg.rstrip('s'). Match esatto contro un campo di entries[0].
        singular = arg_name[:-1] if arg_name.endswith("s") and len(arg_name) > 1 else arg_name
        if singular in sample_keys:
            priority = 0 if arg_name in required else 1
            candidates.append((priority, arg_name, singular))
    if not candidates:
        return None
    # Sort: prima i required (priority=0), tie-break alfabetico.
    candidates.sort(key=lambda t: (t[0], t[1]))
    return candidates[0][1]  # nome arg consumer


def _expand_nested_from_step(args: dict, history: list) -> tuple[dict, list]:
    """Espande pattern `from_step:N` ANNIDATI dentro liste args (8/5/2026).

    Caso d'uso: `paths_filter: ["from_step:2"]` — il PLANNER vuole passare
    i path dello step 2 come filtro, ma `resolve_from_step` standard
    riconosce solo top-level `from_step: int`. Senza questa espansione,
    il valore literal "from_step:2" finisce nella lista paths_filter →
    intersezione vuota → 0 entries (bug live silvia al mare 8/5).

    Sostituisce ogni stringa `"from_step:N"` o `"from_step=N"` dentro
    valori lista degli args con la lista dei path estratti dallo step N.

    Per `paths_filter` (semantica: lista path assoluti) estrae
    `entry["path"]` da entries dello step N.
    Per altri arg lista, estrae il singolare se matcha (urls, ids, ecc).

    Idempotente: gli args senza pattern restano invariati. Errors append-only.
    """
    import re as _re
    PATTERN = _re.compile(r"^\s*from_step\s*[:=]\s*(\d+)\s*$", _re.IGNORECASE)
    errors: list[str] = []
    if not isinstance(args, dict):
        return args, errors
    new_args = dict(args)
    for arg_name, val in list(args.items()):
        if not isinstance(val, list):
            continue
        if not any(isinstance(v, str) and PATTERN.match(v) for v in val):
            continue
        # Trovato almeno un placeholder. Espandi.
        expanded: list = []
        for v in val:
            if isinstance(v, str) and (m := PATTERN.match(v)):
                step_n = int(m.group(1))
                if step_n < 1 or step_n > len(history):
                    errors.append(
                        f"{arg_name}: from_step={step_n} fuori range "
                        f"(history len={len(history)})"
                    )
                    continue
                step_obs = history[step_n - 1].get("observation") or {}
                step_entries = step_obs.get("entries") if isinstance(step_obs, dict) else None
                if not isinstance(step_entries, list):
                    errors.append(
                        f"{arg_name}: step {step_n} non ha entries (lista)"
                    )
                    continue
                # Mappa entry → scalare per arg_name. paths_filter → path.
                # Generico: se arg termina in 's', il singolare e' la chiave
                # da estrarre (paths_filter → ricava 'path' speciale).
                if arg_name == "paths_filter":
                    singular = "path"
                elif arg_name.endswith("s") and len(arg_name) > 1:
                    singular = arg_name[:-1]
                else:
                    singular = arg_name
                for e in step_entries:
                    if isinstance(e, dict) and singular in e:
                        sv = e[singular]
                        if sv is not None:
                            expanded.append(sv)
            else:
                expanded.append(v)
        new_args[arg_name] = expanded
    return new_args, errors


def resolve_from_step(args, history, consumer_schema=None):
    """Espande l'arg shortcut `from_step: int` in `entries: <list>` (o nell'arg
    consumer naturale) consultando lo scratchpad/observation dello step indicato.

    Pattern preferito (29/4/2026, refactor F1) per passare al tool una lista
    prodotta da uno step precedente: `from_step: 2` invece di `entries:
    "{{step2.entries}}"`. Lo schema-guided decoding emette un int, niente
    possibilita' di inventare dict — F1 risolto strutturalmente.

    Comportamento:
    - Se args ha `from_step: int`, recupera `history[N-1].observation`,
      cerca il primo campo lista canonico (entries/matches/items/results/
      files/paths).
    - Se `consumer_schema` e' fornito (Layer 4, 5/5/2026): tenta di mappare
      le entries sull'arg consumer naturale (es. read_urls_html consuma
      `urls` ↔ entries[*].url). Estrae i valori scalari e li passa
      sotto quell'arg. Fallback: inietta sotto `entries`.
    - Se args non ha from_step, no-op.
    - Errori (step inesistente, step senza lista, type sbagliato): list di
      stringhe istruttive.
    """
    errors = []
    if not isinstance(args, dict):
        return args, errors
    if "from_step" not in args:
        return args, errors
    fs = args.get("from_step")
    if isinstance(fs, str) and fs.isdigit():
        fs = int(fs)
    if not isinstance(fs, int):
        errors.append(
            f"from_step: deve essere un intero, ricevuto {type(fs).__name__} ({fs!r}). "
            f"Usa il numero dello step precedente che ha prodotto la lista (es. from_step=1)."
        )
        return args, errors
    # Args alternativi che identificano gia' il target senza bisogno di
    # from_step (10/5/2026 fix bug live: PLANNER spesso passa from_step
    # SUPERFLUO accanto a un name/names/all/paths/urls esplicito; non ha
    # senso bloccare l'esecuzione se l'utente ha gia' detto cosa fare).
    # 15/5/2026 estesa con event_ids/event_id/entries/to/to_user dopo
    # bug live: "cancella gli eventi con id X, Y, Z" → LLM emette
    # delete_events(from_step=1, event_ids=[...]) → from_step=1 al primo
    # step inesistente blocca pur con event_ids espliciti.
    _ALT_TARGET_KEYS = ("name", "names", "all", "paths", "urls", "ids",
                          "messages", "patterns",
                          "event_ids", "event_id", "entries",
                          "to", "to_user")
    _has_alt = any(
        k in args and args[k] not in (None, "", [], {})
        for k in _ALT_TARGET_KEYS
    )
    if fs < 1 or fs > len(history):
        if _has_alt:
            new_args = dict(args)
            new_args.pop("from_step", None)
            return new_args, errors
        errors.append(
            f"from_step={fs}: step inesistente. Validi: 1..{len(history)}. "
            f"Indica lo step che nel turno corrente ha gia' prodotto una lista."
        )
        return args, errors
    step_obs = history[fs - 1].get("observation", {})
    src_tool = history[fs - 1].get("tool", "?")
    if not isinstance(step_obs, dict):
        if _has_alt:
            new_args = dict(args)
            new_args.pop("from_step", None)
            return new_args, errors
        errors.append(f"from_step={fs}: step '{src_tool}' senza observation valida")
        return args, errors
    list_field = None
    for k in ("entries", "matches", "items", "results", "files", "paths"):
        v = step_obs.get(k)
        if isinstance(v, list):
            list_field = k
            break
    if list_field is None:
        if _has_alt:
            new_args = dict(args)
            new_args.pop("from_step", None)
            return new_args, errors
        errors.append(
            f"from_step={fs}: step '{src_tool}' non ha prodotto una lista "
            f"(cerco entries/matches/items/results/files/paths). "
            f"Scegli uno step diverso o passa entries inline."
        )
        return args, errors
    prev_list = step_obs[list_field]
    new_args = dict(args)
    new_args.pop("from_step", None)
    # Layer 4 (5/5/2026): consumer-arg auto-espansione. Se lo schema consumer
    # ha un arg matchabile sul singolare di un campo di entries[0] e l'arg
    # consumer non e' gia' presente in args, estrae i valori scalari.
    consumer_arg = _consumer_match_arg(consumer_schema, prev_list)
    if consumer_arg and consumer_arg not in new_args:
        singular = (consumer_arg[:-1]
                    if consumer_arg.endswith("s") and len(consumer_arg) > 1
                    else consumer_arg)
        values = []
        for e in prev_list:
            if isinstance(e, dict) and singular in e:
                v = e[singular]
                if v is not None:
                    values.append(v)
        new_args[consumer_arg] = values
        return new_args, errors
    # Fallback storico: inietta sotto `entries` (target standard universale).
    new_args["entries"] = prev_list
    return new_args, errors


def resolve_references(args, history):
    errors = []

    def walk(node):
        if isinstance(node, str):
            m = _REF_RE.match(node.strip())
            if not m:
                return node
            step_num = int(m.group(1))
            field = m.group(2)
            if step_num < 1 or step_num > len(history):
                errors.append(f"reference {node}: step {step_num} non esiste")
                return node
            obs = history[step_num - 1]["observation"]
            value, err = _lookup_field(obs, field)
            if err:
                errors.append(f"reference {node}: {err}")
                return node
            return value
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(args), errors


_INLINE_LIST_THRESHOLD = 3  # >= N dict in lista inline => richiedi reference (default)
# Nomi di arg riservati al "data passing" fra step: per questi la soglia e' 1
# (qualunque dict inline e' rifiutato — devono arrivare via {{stepN.<field>}}).
# Coerente con i nomi che `scratchpad._summarize_structured` cerca come
# `chosen_key` e con `ref_hint` nel synthetic handle.
_REFERENCE_ARG_NAMES = frozenset({"entries", "items", "matches", "results", "files", "paths"})

_MALFORMED_REF_RE = re.compile(r"\{\{?step\d+\.[^{}]*[|?][^{}]*\}\}?|(?<!\{)\{step\d+\.[^{}]*\}(?!\})")


def check_inline_data(args):
    """Rifiuta args che contengono liste di dict inline non banali.

    Il pianificatore deve usare {{stepN.field}} per riusare output di step
    precedenti, mai re-incollare inline (fragile a quoting/escape e a token
    waste). Sotto la soglia (N<3) accettiamo dati costruiti ad hoc dal modello.
    """
    if not isinstance(args, dict):
        return None
    for k, v in args.items():
        if not isinstance(v, list):
            continue
        if not v or not isinstance(v[0], dict):
            continue
        # Per gli arg "data passing" canonici (entries, items, matches, ...)
        # qualunque dict inline e' rifiutato: devono arrivare via reference.
        # Per altri arg, soglia di tolleranza per dati costruiti ad hoc.
        threshold = 1 if k in _REFERENCE_ARG_NAMES else _INLINE_LIST_THRESHOLD
        if len(v) < threshold:
            continue
        return (
            f"INLINE_DATA_REJECTED: hai passato '{k}' come {len(v)} dict inline. "
            f"Per riusare l'output di un passo precedente DEVI usare la reference "
            f"'{{{{stepN.{k}}}}}' (es. '{{{{step1.{k}}}}}'). "
            f"Esempio corretto: {{ \"{k}\": \"{{{{step1.{k}}}}}\" }}. "
            f"Riformula la chiamata."
        )
    return None


def check_malformed_reference(args):
    """Rileva placeholder malformati (graffa singola, pipe, ternario).

    L'unica sintassi valida e' `{{stepN.field}}` puro. Tutto il resto e' un
    template engine che il runtime non interpreta: il LLM ha confuso la
    sintassi con Jinja o simili. Errore istruttivo, no loop.
    """
    if not isinstance(args, dict):
        return None
    def _scan(node, key_path=""):
        if isinstance(node, str):
            m = _MALFORMED_REF_RE.search(node)
            if m:
                return (
                    f"MALFORMED_REFERENCE: arg '{key_path}' contiene un placeholder non valido: "
                    f"'{m.group(0)}'. La SOLA sintassi ammessa e' `{{{{stepN.field}}}}` puro "
                    f"(doppie graffe, niente pipe `|`, niente ternario `?`, niente espressioni). "
                    f"Per filtrare/derivare dati usa uno step intermedio (es. chiama filter_entries "
                    f"come step a se') e poi referenzia il suo output."
                )
        elif isinstance(node, dict):
            for k, v in node.items():
                err = _scan(v, f"{key_path}.{k}" if key_path else k)
                if err:
                    return err
        elif isinstance(node, list):
            for i, v in enumerate(node):
                err = _scan(v, f"{key_path}[{i}]")
                if err:
                    return err
        return None
    return _scan(args)


def extract_step_refs(args) -> set[int]:
    """Ritorna l'insieme degli step numerici referenziati in args via {{stepN.field}}."""
    refs: set[int] = set()

    def walk(node):
        if isinstance(node, str):
            m = _REF_RE.match(node.strip())
            if m:
                refs.add(int(m.group(1)))
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(args)
    return refs


# --- Anti-allucinazione final_message (Bug B, 5/5/2026) ---------------------
#
# Caso live turn 23be1548: PLANNER ha emesso final_message "Sto effettuando
# una ricerca... ti aggiornerò non appena disponibili" — falso, nessun BG worker
# in coda. Self-check deterministico (regex, no LLM, §7.9): se il messaggio
# contiene verbi di promessa futura E nessuno step ok ha registrato un'azione,
# prepende notice "azione NON registrata". Notice additiva, non sostitutiva.

_HALLUCINATION_RE = re.compile(
    r"\b("
    # Forme "ti X-ò" (futuro semplice 1pps), con e senza accento finale
    r"ti (informer[oò'`]|aggiorner[oò'`]|far[oò'`] sapere|dir[oò'`]|"
    r"segnaler[oò'`]|comunicher[oò'`]|contatter[oò'`]|risponder[oò'`])"
    # Forme "sto X-ndo" (gerundio progressivo) tranne quando seguite da
    # una conferma esplicita di azione registrata.
    r"|sto (cercando|effettuando|controllando|monitorando|verificando|raccogliendo)"
    # Forme "appena X" (futuro condizionato a evento)
    r"|appena (avr[oò'`]|trovo|trovato|trovi|disponibili|disponibile|ricever[oò'`])"
    r")",
    re.IGNORECASE,
)

# Tool che REGISTRANO un'azione futura concreta. Se il PLANNER promette
# follow-up e ne ha chiamato uno con ok=true, la promessa e' supportata.
# Aggiungere qui ogni nuovo executor con effetto persistente registrato.
_REGISTERED_FUTURE_TOOLS = frozenset({
    "create_tasks",              # task ricorrente nel scheduler builtin
    "send_messages",             # mail/telegram in uscita
    "write_files",               # file scritti localmente
    "create_dirs",               # directory create
    "set_signatures",            # safety policy aggiornata
    "create_indices_image",      # indice persistente costruito
    "move_files",                # file spostati
    "move_messages",             # mail spostate
    "delete_files",              # file cancellati
    "delete_messages",           # mail cancellate
    "admin",                     # azioni privilegiate eseguite (mount, kill, ...)
})


def _detect_unbacked_promise(final_message: str | None, steps: list) -> bool:
    """Ritorna True se il `final_message` contiene una promessa di azione
    futura ma nessuno step ok ha chiamato un tool che registra azioni.

    Determinismo §7.9: regex + lookup, niente LLM nel critical path.
    """
    if not final_message:
        return False
    if not _HALLUCINATION_RE.search(final_message):
        return False
    for s in steps or []:
        tool = getattr(s, "chosen_tool", None) or (
            s.get("chosen_tool") if isinstance(s, dict) else None
        )
        result = getattr(s, "result", None)
        if result is None and isinstance(s, dict):
            result = s.get("result")
        if (tool in _REGISTERED_FUTURE_TOOLS
                and isinstance(result, dict) and result.get("ok")):
            return False
    return True


def invoke_executor(executor, args, timeout_s=30, *, autonomy="supervised",
                    turn_id=None, actor=None, channel=None):
    """Invoca un executor, opzionalmente in sandbox bubblewrap.

    Se `bwrap` e' installato e `METNOS_SANDBOX` non e' disabilitato,
    il comando viene wrappato; altrimenti gira come subprocess Python
    diretto (la pseudo-sandbox del runtime resta attiva: filtro path/host
    + Vaglio).

    `actor` / `channel` (12/5/2026): propagati come `METNOS_ACTOR` /
    `METNOS_CHANNEL` nell'env del subprocess. Servono a `get_inputs` per
    derivare un `sender_id` stabile (`<channel>:<actor>`) che e' chiave
    di storage per `dialog_pending`. Senza questa propagazione gli
    executor defaultano a `actor="host"`/`channel=""` e il consumer HTTP
    cerca lo state con un sender_id diverso da quello con cui e' stato
    salvato (bug live 12/5/2026: pipeline find_events_empty → get_inputs
    → send_messages perdeva il dialog state).
    """
    import sandbox as _sandbox  # lazy: evita import circolare e overhead per moduli che non lo usano
    payload = json.dumps(args)
    base_cmd = [sys.executable, str(executor.code_path)]
    cmd = _sandbox.wrap_command(executor, base_cmd, autonomy=autonomy)
    # PYTHONPATH augmentato: gli executor (specie quelli sintetizzati) importano
    # moduli runtime (mail_client, messages, platform_policy, ...) per nome.
    # Senza questo, il subprocess vede solo stdlib e fallisce con
    # ModuleNotFoundError. Vedi caso live 29/4/2026 sera (move_messages errore in
    # esecuzione anche dopo birth tests verdi).
    env = os.environ.copy()
    runtime_path = str(Path(__file__).resolve().parent)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        runtime_path if not existing_pp
        else f"{runtime_path}{os.pathsep}{existing_pp}"
    )
    # Esponi METNOS_TURN_ID al subprocess: gli executor revertibili lo usano
    # per nominare i blob backup deterministicamente
    # (`<HISTORY>/<turn_id>/blob/<sha256>.bin`).
    if turn_id:
        env["METNOS_TURN_ID"] = turn_id
    if actor:
        env["METNOS_ACTOR"] = actor
    if channel:
        env["METNOS_CHANNEL"] = channel
    result = subprocess.run(
        cmd, input=payload, capture_output=True, text=True, timeout=timeout_s,
        env=env,
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"non-JSON output: {result.stdout!r}; stderr: {result.stderr!r}"}


# --- Step + Turn log -------------------------------------------------------

@dataclass
class StepLog:
    step_num: int
    llm_text: str = ""
    llm_thinking: str = ""
    llm_in_tokens: int = 0
    llm_out_tokens: int = 0
    llm_latency_ms: int = 0
    chosen_tool: str = ""
    raw_args: dict = field(default_factory=dict)
    resolved_args: dict = field(default_factory=dict)
    validation_failures: list = field(default_factory=list)
    scope_violation: str | None = None
    vaglio_approved: bool = False
    result: dict = field(default_factory=dict)
    error: str | None = None
    # Telemetria fine (ADR 0080, 4/5/2026): le 4 sotto-componenti del
    # tempo di turno fuori dal PLANNER LLM. Default None: campi nuovi sui
    # turn JSONL, opzionali per non rompere la deserializzazione storica.
    intent_ms: int | None = None     # intent extractor (LLM, tipicamente solo step 1)
    vaglio_ms: int | None = None     # judge() (LLM o stub)
    exec_ms: int | None = None       # invoke_executor (sandbox + I/O)
    rerank_ms: int | None = None     # re_rank_for_step (post-step ok)
    prefilter_ms: int | None = None  # rank_adaptive (tipicamente solo step 1)
    # ADR 0099: True quando lo step e' iniettato deterministicamente dal
    # runtime (URL detection -> read_urls_html primo step), non scelto dal
    # PLANNER. Visibile in TurnLog JSONL per telemetria.
    seed_step: bool = False
    # CLAUDE.md §4.4 estesa (8/5/2026 notte): marker per cap_max_per_turn
    # rinforzato. Settato a "max_calls_per_turn" quando un tool non-action
    # viene chiamato >= DEFAULT_CAP_MAX_PER_TURN volte nel turno con args
    # near-identical (Jaccard >= 0.7).
    loop_break_total: str | None = None


@dataclass
class TurnLog:
    ts_start: float
    ts_end: float = 0.0
    user_query: str = ""
    turn_id: str = ""
    mode: str = ""
    candidates: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    final_message: str = ""
    final_kind: str = ""
    # Lista di proposte di cap expand emerse dal turno: ogni elemento e'
    # {step_num, executor, args, used, available_total, suggested_args}.
    # Popolata in write() per i daemon channel che gestiscono dialog
    # stateful di conferma (CLAUDE.md 2.11 fase 2).
    expandable_caps: list = field(default_factory=list)
    # Attachments emessi dall ultimo step che ne ha prodotti (es.
    # find_images_indices). Lista di dict {kind, path, score, basename, caption}.
    # Il path crudo NON viene mai serializzato verso il client: i channel
    # adapter lo sostituiscono con URL signed (HTTP) o upload binario
    # (Telegram sendMediaGroup).
    attachments: list = field(default_factory=list)
    # Pending location request (regola §2-quater): metadata per il daemon che
    # deve renderizzare il prompt UI (Telegram bottoni / web form / CLI prompt).
    # Forma: {pending_id, goal, chat_id, original_query, expires_in_s}.
    pending_location: dict | None = None
    # Scrubbing credenziali nel turn log (ADR 0082, 4/5/2026): se la query
    # utente contiene pattern tipo `pwd:foo` / `user:bar`, prima di
    # serializzare il jsonl rimpiazziamo il valore con `<REDACTED:cred>`.
    # In RAM la credenziale resta finche' il turno e' attivo (per non
    # spezzare i tool che la consumano), ma su disco non si scrive mai.
    redacted: bool = False
    n_redacted_fields: int = 0

    # Anti-allucinazione final_message (Bug B, 5/5/2026): True se il
    # messaggio finale conteneva promesse di azione futura non registrate
    # da alcuno step ok. Notice additiva applicata in write(); il campo
    # serve da telemetria per metriche future (frequenza allucinazioni
    # PLANNER per turno).
    unbacked_promise_detected: bool = False

    # Identita' utente + canale del turno (6/5/2026): necessari a write()
    # per orchestrare get_inputs sul cap-expand (ADR 0091 generalizzato).
    # Settati da run_turn ai parametri ricevuti.
    actor: str = "host"
    channel: str = ""
    # Conversation linking (8/5/2026): persisted nel JSONL per permettere
    # al chat HTTP di ricaricare la storia della conversazione dopo un tab
    # close. Sender HTTP setta dal body POST `conversation_id`. Telegram lo
    # ricava da `chat_id`. Vuoto = turn standalone (nessun grouping).
    conversation_id: str = ""

    # Mappa fallback executor → nome dell'arg che controlla il cap di output.
    # Coverage di tutti gli executor del catalog corrente (30/4/2026 sera) che
    # hanno un cap esplicito sui risultati. Da migrare a campo `cap_field` nel
    # manifest (estensione CLAUDE.md 2.7+2.11) per evitare hardcoding.
    # max_depth/max_batch_size esclusi: non sono cap di output, sono limiti
    # operativi (profondita' ricorsione, batch IMAP interno).
    _CAP_FIELD_FALLBACK = {
        "read_messages":      "max_total",
        "find_files":         "max_results",
        "list_dirs":          "max_results",
        "find_places":        "max_results",
        "filter_texts_lines": "max_results",
        "read_files":         "max_bytes",
        "read_files_csv":     "max_rows",
        "read_files_xlsx":    "max_rows",
    }

    def _collect_expandable_caps(self):
        """Per ogni step truncated dove conosciamo (a) il cap_field e (b)
        l'available_total > used, costruisce una proposta di re-run con
        cap esteso. Convenzione: l'executor puo' esporre `cap_field` e
        `cap_value` direttamente nel result; altrimenti fallback su mappa
        interna per nome executor.

        Eccezione (ADR 0090, get_inputs): se uno step ha emesso
        `expandable_caps` con kind specifici (es. `get_inputs_response`),
        questi vengono propagati senza richiedere il pattern truncated.
        Sono proposte non di cap-expand, ma di dialogo strutturato.
        """
        proposals = []
        seen = set()
        # ── Pass 1: propaga expandable_caps custom (ADR 0090) ──
        for s in self.steps:
            res = s.result if isinstance(s.result, dict) else {}
            custom = res.get("expandable_caps") or []
            if not isinstance(custom, list):
                continue
            for item in custom:
                if isinstance(item, dict) and item.get("kind"):
                    enriched = dict(item)
                    enriched.setdefault("step_num", s.step_num)
                    enriched.setdefault("executor", s.chosen_tool)
                    proposals.append(enriched)
        # 10/5/2026 fix UX: skip cap-expand per output narrativi/non-azionabili.
        # describe_entries produce TESTO sintetizzato (LLM): allargare il cap
        # non aiuta l'utente, riallarga solo il contesto LLM. Stesso per
        # find_urls quando used >= 10 (un umano non legge 5000 link).
        _NARRATIVE_NO_CAP_EXPAND = {"describe", "URL"}

        for s in self.steps:
            res = s.result if isinstance(s.result, dict) else {}
            if not res.get("truncated"):
                continue
            # Skip explicit user-set cap (truncated_intentional, ADR 0062).
            if res.get("truncated_intentional"):
                continue
            used = res.get("used") or res.get("ok_count") or res.get("count")
            available = res.get("available_total")
            if not available or not used or available <= used:
                continue
            tw = res.get("truncated_what") or ""
            if tw in _NARRATIVE_NO_CAP_EXPAND and used >= 10:
                continue
            if s.chosen_tool == "describe_entries":
                continue
            # Qualifier `_empty` (ADR 0127): l'executor ritorna ESATTAMENTE
            # quanto chiesto dall'utente (es. find_events_empty max_results=3).
            # Cap intenzionale per semantica del verbo, no overflow inquiry.
            # §7.3 detection generale (qualifier suffix), no whitelist hardcoded.
            if s.chosen_tool and s.chosen_tool.endswith("_empty"):
                continue
            cap_field = res.get("cap_field") or self._CAP_FIELD_FALLBACK.get(s.chosen_tool)
            if not cap_field:
                continue
            cap_current = res.get("cap_value") or used  # used coincide col cap usato
            # suggested = available_total + 10% buffer, arrotondato per eccesso
            # a una potenza "umana" (200, 500, 1000, 2000, 5000, ...).
            target = int(available * 1.1)
            for step_size in (200, 500, 1000, 2000, 5000, 10000):
                if step_size >= target:
                    suggested_cap = step_size; break
            else:
                suggested_cap = target
            key = (s.chosen_tool, cap_field, cap_current)
            if key in seen:
                continue
            seen.add(key)
            new_args = dict(s.raw_args or {})
            new_args[cap_field] = suggested_cap
            proposals.append({
                "kind": "cap_expand",
                "step_num": s.step_num,
                "executor": s.chosen_tool,
                "cap_field": cap_field,
                "cap_current": cap_current,
                "cap_suggested": suggested_cap,
                "used": used,
                "available_total": available,
                "args_original": s.raw_args,
                "args_suggested": new_args,
            })
        return proposals

    def _orchestrate_cap_expand_dialog(self, proposal: dict) -> None:
        """Sintetizza un get_inputs (1 step yes_no) per chiedere conferma
        d'allargamento cap, in luogo della vecchia stringa "rispondi sì".

        Side-effect: sostituisce `self.final_message` con il
        `final_message_hint` del get_inputs orchestrato e
        `self.expandable_caps` con la entry `get_inputs_response`. Il
        canale (Telegram daemon o HTTP) consume gia' `get_inputs_response`
        come dialogo persistente in `dialog_pending` (mode 0600), quindi
        sopravvive al restart del daemon.

        Idempotente: se l'orchestration fallisce (es. dialog_pending
        write error), preserva il final_message originale e logga il warning.
        """
        executor = proposal.get("executor") or ""
        cap_field = proposal.get("cap_field") or ""
        cap_suggested = proposal.get("cap_suggested")
        available_total = proposal.get("available_total")
        used = proposal.get("used")
        args_suggested = proposal.get("args_suggested") or {}
        if not executor or not cap_field or cap_suggested is None:
            return  # proposta malformata: lascia stato com'e'

        # Etichetta leggibile per la preview ("processi", "foto", "righe")
        # — dal `truncated_what` se l'executor lo dichiara, altrimenti
        # fallback "risultati".
        preview_label = "risultati"
        for s in self.steps:
            if s.chosen_tool == executor:
                res = s.result if isinstance(s.result, dict) else {}
                tw = res.get("truncated_what")
                if isinstance(tw, str) and tw:
                    preview_label = tw
                break

        prompt = (
            f"Hai chiesto un risultato troncato a {used} {preview_label}; "
            f"in totale ce ne sono {available_total}. "
            f"Allargo a {cap_suggested}?"
        )
        title = "Allargamento risultato"
        dialog = [{
            "var": "confirm",
            "prompt": prompt,
            "schema": {"kind": "yes_no"},
        }]
        on_complete = {
            "type": "expand_cap_and_resume",
            "executor": executor,
            "cap_field": cap_field,
            "cap_suggested": cap_suggested,
            "args_suggested": args_suggested,
            "preview_label": preview_label,
        }

        sender_id = (
            f"{self.channel}:{self.actor}" if self.channel
            else (self.actor or "host")
        )
        try:
            import sys as _sys
            _sys.path.insert(0, "/opt/myclaw/runtime")
            import orchestration as _orch
            res = _orch.invoke_get_inputs_internal(
                sender_id=sender_id,
                title=title,
                description=None,
                dialog=dialog,
                fmt="auto",
                on_complete=on_complete,
                actor=self.actor or "host",
                channel=self.channel or None,
                timeout_s=600,
            )
        except (ImportError, OSError, RuntimeError, ValueError, TypeError):
            log.exception("cap-expand orchestration fallita; lascio prompt vuoto")
            return

        if not res.get("ok"):
            log.warning("cap-expand orchestration ko: %s", res.get("error"))
            return

        hint = res.get("final_message_hint") or ""
        if hint:
            self.final_message = ((self.final_message or "").rstrip()
                                  + "\n\n" + hint).strip()
        # Sostituisci expandable_caps con la entry get_inputs_response cosi'
        # i channel adapter prendono il cammino gia' rodato (dialog_pending +
        # process_completion_callback) invece del bespoke cap_pending.
        new_caps = []
        for c in res.get("expandable_caps") or []:
            if isinstance(c, dict):
                enriched = dict(c)
                enriched.setdefault("step_num", proposal.get("step_num"))
                enriched.setdefault("executor", executor)
                new_caps.append(enriched)
        if new_caps:
            self.expandable_caps = new_caps

    def _append_images_results_if_any(self) -> None:
        """Se in history c'e' uno step find_images_indices/find_persons_indices
        ok con entries, accoda al final_message la lista path reali (max 15).
        Previene hallucination LLM (PLANNER inventa path "IMG_001.jpg..."
        invece di leggere entries reali): l'append deterministico ancorato
        ai path effettivi sostituisce/integra il messaggio LLM."""
        import os
        seen_paths: set = set()
        all_entries: list = []
        for s in self.steps:
            if s.chosen_tool not in ("find_images_indices", "find_persons_indices"):
                continue
            res = s.result if isinstance(s.result, dict) else {}
            if not res.get("ok"):
                continue
            for e in res.get("entries") or []:
                if not isinstance(e, dict):
                    continue
                p = e.get("path")
                if not p or p in seen_paths:
                    continue
                seen_paths.add(p)
                all_entries.append(e)
        if not all_entries:
            return
        max_show = 15
        n_total = len(all_entries)
        sample = all_entries[:max_show]
        # Se il LLM ha gia' incluso path corretti, non duplicare (idempotente).
        existing = self.final_message or ""
        already_in_msg = sum(
            1 for e in sample
            if os.path.basename(e.get("path", "")) in existing
        )
        if already_in_msg >= len(sample) // 2 and already_in_msg > 0:
            return  # gia' presente in modo significativo, skip
        # Solo basename nel testo: caption VLM visibile in gallery viewer
        # (hover tooltip + overlay HTML), NON duplicata nel final testuale
        # (Roberto 15/5/2026: troppo verbose).
        lines = ["", "", "**Risultati:**"]
        for e in sample:
            basename = os.path.basename(e.get("path", ""))
            lines.append(f"- `{basename}`")
        if n_total > max_show:
            lines.append(f"_... e altre {n_total - max_show} foto._")
        self.final_message = (existing.rstrip() + "\n".join(lines))

    def _append_search_results_if_any(self) -> None:
        """Se in history esistono step `find_urls` ok con entries,
        AGGREGA tutti i risultati, dedup per URL, ordina per score desc,
        appende al final_message la lista markdown cliccabile via
        `output_format.format_search_results`.

        UX fix 10/5/2026 «risposta educata ma inutile» + «PLANNER pesca
        lo step sbagliato»: aggregazione cross-step + dedup garantisce
        che le URL piu' rilevanti emergano anche se PLANNER ha invocato
        find_urls multiple volte con varianti (alcune buone, alcune
        rumorose).
        """
        all_entries_by_url: dict[str, dict] = {}
        all_docs_by_url: dict[str, dict] = {}
        for s in self.steps:
            if s.chosen_tool != "find_urls":
                continue
            res = s.result if isinstance(s.result, dict) else {}
            if not res.get("ok"):
                continue
            for e in res.get("entries") or []:
                if not isinstance(e, dict):
                    continue
                url = e.get("url")
                if not isinstance(url, str) or not url:
                    continue
                # Mantieni la entry con score piu' alto se duplicata.
                prev = all_entries_by_url.get(url)
                cur_score = e.get("score") or 0
                prev_score = (prev or {}).get("score") or 0
                if prev is None or cur_score > prev_score:
                    all_entries_by_url[url] = e
            for d in res.get("discovered_documents") or []:
                if not isinstance(d, dict):
                    continue
                url = d.get("url")
                if not isinstance(url, str) or not url:
                    continue
                if url not in all_docs_by_url:
                    all_docs_by_url[url] = d

        if not all_entries_by_url and not all_docs_by_url:
            return

        # Noise tail elimination (Roberto, 10/5/2026):
        # 1) Score-relative threshold STRETTO 30% del top (era 5% troppo
        #    morbido — su top 35 lasciava entrare migliaia con score 1-5).
        #    Top tipico BM25 web 30-50 -> threshold 9-15; entries 0-9
        #    droppate. LLM rerank top 1 -> threshold 0.3.
        # 2) Hard cap 30 entries finali post-threshold. Un umano non
        #    legge oltre 30 link. Cap_expand cap_field=top_k esposto
        #    al PLANNER per allargare on-demand.
        scores = [
            e.get("score") for e in all_entries_by_url.values()
            if isinstance(e.get("score"), (int, float))
        ]
        if scores:
            top_score = max(scores)
            if top_score > 0:
                threshold = top_score * 0.30
                all_entries_by_url = {
                    u: e for u, e in all_entries_by_url.items()
                    if isinstance(e.get("score"), (int, float))
                    and e["score"] >= threshold
                }

        # Drop entries da motori di ricerca (privacy/terms/help/...) —
        # mai utili come "risultato" anche se BFS li ha pescati come
        # cross-link. Stesso whitelist di find_urls._is_search_engine_home
        # (host esatti, NON match parziale per evitare false positive).
        _SEARCH_ENGINE_HOSTS_DROP = {
            "google.com", "google.it", "google.fr", "google.de", "google.es",
            "google.co.uk", "google.ch", "google.at", "google.nl", "google.be",
            "www.google.com", "www.google.it", "www.google.fr",
            "www.google.de", "www.google.es", "www.google.co.uk",
            "www.google.ch", "www.google.at", "www.google.nl",
            "policies.google.com", "support.google.com",
            "bing.com", "www.bing.com",
            "duckduckgo.com", "www.duckduckgo.com",
            "search.brave.com", "brave.com",
            "yandex.com", "yandex.ru",
            "yahoo.com", "search.yahoo.com",
            "ecosia.org", "www.ecosia.org",
            "qwant.com", "www.qwant.com",
            "startpage.com", "www.startpage.com",
        }
        from urllib.parse import urlparse as _urlparse

        def _is_search_engine_url(url: str) -> bool:
            try:
                host = (_urlparse(url).hostname or "").lower()
            except Exception:
                return False
            return host in _SEARCH_ENGINE_HOSTS_DROP

        # Sort entries: score desc, poi URL stabile. Drop score==0 se
        # almeno UNA entry ha score>0 (filtro rumore cross-step).
        # Drop sempre URL da motori di ricerca (cross-link policy/help).
        entries_list = [
            e for e in all_entries_by_url.values()
            if not _is_search_engine_url(e.get("url") or "")
        ]
        any_positive = any(
            isinstance(e.get("score"), (int, float)) and e.get("score", 0) > 0
            for e in entries_list
        )
        if any_positive:
            entries_list = [
                e for e in entries_list
                if isinstance(e.get("score"), (int, float)) and e["score"] > 0
            ]
        entries_list.sort(
            key=lambda e: (-(e.get("score") or 0), e.get("url") or "")
        )
        # 11/5/2026: dedup avanzato per titolo+score. Siti con accessibility
        # options (?textMode=0/1/2, ?contrastMode=0/1/2) generano URL
        # multipli con stesso content+title+score. Idem footer/sidebar
        # uniform: ogni pagina del sito espone lo stesso indirizzo come
        # entry-titolo separata. Tenere SOLO la prima entry per ogni
        # (title_normalized, score_rounded) pair.
        seen_title_score: set[tuple[str, float]] = set()
        deduped_entries: list[dict] = []
        for e in entries_list:
            t = (e.get("title") or "").strip().lower()
            s = round(float(e.get("score") or 0.0), 2)
            key = (t, s)
            if t and key in seen_title_score:
                continue
            if t:
                seen_title_score.add(key)
            deduped_entries.append(e)
        entries_list = deduped_entries

        # Split entries vs documenti per evitare duplicati nelle due
        # sezioni del rendering. «Risultati» mostra solo pagine HTML
        # (is_document=False); «Documenti scoperti» mostra solo file
        # binari (PDF/DOCX/XLSX, is_document=True). Lo stesso URL non
        # appare mai in entrambi.
        html_entries: list[dict] = []
        doc_entries: list[dict] = []
        for e in entries_list:
            if e.get("is_document"):
                doc_entries.append(e)
            else:
                html_entries.append(e)
        # Aggrega i docs da all_docs_by_url (find_urls expone subset doc
        # separatamente, ma potrebbe contenere docs non in entries — es.
        # da BFS cross-domain). Unione + dedup per URL.
        for d in all_docs_by_url.values():
            if _is_search_engine_url(d.get("url") or ""):
                continue
            u = d.get("url")
            if u and not any(de.get("url") == u for de in doc_entries):
                doc_entries.append(d)
        # Sort docs per score desc.
        doc_entries.sort(
            key=lambda d: (-(d.get("score") or 0), d.get("url") or "")
        )
        # Hard cap 30 per sezione: nessuno legge piu' di 30 link.
        _HARD_CAP = 30
        entries = html_entries[:_HARD_CAP]
        docs = doc_entries[:_HARD_CAP]
        try:
            from output_format import format_search_results
        except Exception:
            return
        block = format_search_results(
            entries,
            query=self.user_query or "",
            discovered_documents=docs,
            max_show=20,
        )
        if not block:
            return
        # Idempotenza: se gia' presente nel messaggio, non duplicare.
        first_url = ""
        for e in entries:
            u = e.get("url") if isinstance(e, dict) else ""
            if isinstance(u, str) and u:
                first_url = u
                break
        if first_url and first_url in (self.final_message or ""):
            return

        # Loop_break/error: il messaggio runtime "Mi sono bloccato" e'
        # confondente quando AVEMMO comunque dei risultati. Sostituisce
        # con un'intro morbida che dichiara onestamente i limiti senza
        # negare l'utilita' della lista. Idempotente.
        if self.final_kind in ("loop_break", "error") and entries:
            try:
                from messages import get as _msg_local
                soft = _msg_local("MSG_SEARCH_PARTIAL_OR_INTERRUPTED",
                                    n=len(entries))
            except Exception:
                soft = (
                    f"Ricerca interrotta prima di poter convergere su una "
                    f"risposta diretta, ma sono stati raccolti {len(entries)} "
                    f"risultati pertinenti."
                )
            self.final_message = soft
            self.final_kind = "answer"
        self.final_message = (
            (self.final_message or "").rstrip() + "\n\n" + block
        ).strip()

    def _prepend_health_block_if_any(self) -> None:
        """Se uno step ok ha prodotto un dict `health`, prepend il blocco
        salute formattato al final_message. Si appoggia al formatter
        condiviso di runtime/orchestration.py (`_fmt_health_block` +
        `_fmt_entries_block`). Idempotente: se il blocco e' gia' nel
        messaggio non duplica.
        """
        health = None
        entries = None
        for s in self.steps:
            res = s.result if isinstance(s.result, dict) else {}
            h = res.get("health")
            if isinstance(h, dict):
                health = h
                # Stesso step: prendi anche le entries (lista processi).
                e = res.get("entries")
                if isinstance(e, list):
                    entries = e
                break
        if not health:
            return
        try:
            import sys as _sys
            _sys.path.insert(0, "/opt/myclaw/runtime")
            from orchestration import _fmt_health_block, _fmt_entries_block
            block = _fmt_health_block(health)
            if entries:
                # Top 10 processi col detail cpu%/mem% (non solo nomi nudi).
                # Cap 10 perche' health gia' occupa righe; per piu' c'e'
                # cap-expand.
                proc_block = _fmt_entries_block(entries, cap=10)
                if proc_block:
                    block = block + "\n\n**" + msg("MSG_HEALTH_TOP_PROCESSES") + "**\n" + proc_block
        except (ImportError, KeyError, AttributeError):
            return
        if not block or "Stato server" in (self.final_message or ""):
            return  # gia' presente o formatter non funzionante
        # ADR 0111 Level 3 safety net: per query di stato puro (nessuna
        # keyword imperativa nella user_query), il `final_message` LLM
        # tende a duplicare/allucinare i dati (esempi: uptime sbagliato,
        # RAM/dischi inventati). Il blocco deterministico prepended e'
        # gia' la risposta completa: zerare il final_message LLM evita
        # il doppio output. Se c'e' una keyword imperativa, l'LLM puo'
        # avere aggiunto una conferma di azione legittima → preserva.
        # Funziona indipendentemente da `is_multistep`/`chosen_mode`,
        # quindi copre anche i path single-step / fast-path / scratchpad.
        _q = (self.user_query or "").lower()
        if not any(k in _q for k in _HEALTH_IMPERATIVE_KEYWORDS):
            self.final_message = ""
        self.final_message = (block + "\n\n" + (self.final_message or "")).strip()

    def _collect_truncation_notices(self):
        """Scansiona le observation degli step per estrarre cap/truncation
        non dichiarati. Convenzione: un executor che colpisce un cap aggiunge
        all'observation `truncated: true`, opzionalmente `available_total`
        (cardinalita' reale prima del cap), `used: int`, e `truncated_what`
        (nome leggibile della unita': 'email', 'file', 'risultati', ...).
        Vedi feedback_truncation_visibility."""
        notices = []
        seen = set()
        for s in self.steps:
            res = s.result if isinstance(s.result, dict) else {}
            if not res.get("truncated"):
                continue
            # Qualifier `_empty` (ADR 0127): l'executor ritorna ESATTAMENTE
            # quanto chiesto dall'utente (es. find_events_empty max_results=3).
            # "Truncated" qui significa "ho rispettato il tuo cap", non "ho
            # tagliato risultati validi". Notice e' rumore. §7.3 detection
            # generale via suffix qualifier, parallelo a cap-expand suppression.
            if s.chosen_tool and s.chosen_tool.endswith("_empty"):
                continue
            what = (res.get("truncated_what") or s.chosen_tool
                    or msg("MSG_TRUNCATED_DEFAULT_WHAT"))
            used = res.get("used") or res.get("ok_count") or res.get("count")
            available = res.get("available_total")
            key = (what, used, available)
            if key in seen:
                continue
            seen.add(key)
            if available and used:
                notices.append(msg("MSG_TRUNCATED_GENERIC",
                                   available=available, what=what, used=used))
            elif used:
                notices.append(msg("MSG_TRUNCATED_NO_TOTAL", used=used, what=what))
        return notices

    def write(self):
        # Anti thinking-leak (ADR 0102, 7/5/2026): rimuovi righe di
        # reasoning interno emesse erroneamente dal PLANNER nel canale
        # text. Applicato PRIMA di qualsiasi prepend (truncation/health/
        # hallucination) e PRIMA dell'append di elapsed, cosi' nessuna
        # riga aggiunta dal runtime viene scartata. Idempotente.
        if self.final_kind == "answer" and self.final_message:
            self.final_message = _scrub_thinking_leak(self.final_message)
        # 10/5/2026: append lista risultati formattati quando in history
        # c'e' uno step `find_urls` ok con entries. Indipendente da
        # final_kind: anche su loop_break/error l'utente vede i risultati
        # parziali raccolti — prima il PLANNER andava in loop e l'utente
        # non vedeva NIENTE dei link gia' trovati.
        if self.final_kind in ("answer", "loop_break", "error"):
            self._append_search_results_if_any()
            self._append_images_results_if_any()
        # Prepend di eventuali notice di truncation prima della final answer.
        # Una sola volta, idempotente: se la stringa e' gia' presente non duplica.
        # Skip quando l'ultimo step e' `final_answer` synthetic (ADR 0133 ext):
        # il LLM ha ricevuto la describe_entries (con info `truncated`) e ha
        # gia' formulato un final consapevole — prependere ridonda e copre il
        # messaggio utile (bug live 15/5/2026 mail run 1: prepend mascherava
        # la sintesi LLM del riassunto mail).
        if self.final_kind == "answer":
            _llm_synth_final = bool(
                self.steps and self.steps[-1].chosen_tool == "final_answer"
            )
            if not _llm_synth_final:
                for notice in self._collect_truncation_notices():
                    if notice and notice not in (self.final_message or ""):
                        self.final_message = (notice + "\n\n" + (self.final_message or "")).strip()
            # Bug C estensione (6/5/2026): se uno step ha prodotto una sezione
            # `health` (get_processes(include_health=true)), prepend il blocco
            # salute al final_message. describe_entries non sa leggere health
            # perche' lavora solo su entries; senza questo, lo "stato server"
            # mostra solo i nomi dei processi e basta. Idempotente.
            self._prepend_health_block_if_any()
            # Auto cap expand proposal (CLAUDE.md 2.11 fase 2).
            # Popola `expandable_caps` cosi' il channel daemon puo' salvare
            # dialog state e riconoscere la risposta di conferma utente.
            self.expandable_caps = self._collect_expandable_caps()
            if self.expandable_caps:
                p = self.expandable_caps[0]
                # Skip prompt per kind custom gia' renderizzati (ADR 0090):
                # `get_inputs_response` ha gia' la carta UX (Step N/M) emessa
                # dall'executor; `admin_approval` ha la carta vaglio.
                if p.get("kind") in ("get_inputs_response", "admin_approval"):
                    pass
                elif p.get("kind") == "cap_expand" and "available_total" in p and "cap_field" in p:
                    # Migrazione 6/5/2026: invece della stringa testuale "rispondi
                    # sì" + bespoke cap_pending storage, sintetizziamo un
                    # get_inputs (1 step yes_no) con on_complete=expand_cap_and_resume.
                    # Persistente su disco (dialog_pending), sopravvive al daemon
                    # restart, riusa la pipeline get_inputs_response gia' rodata.
                    self._orchestrate_cap_expand_dialog(p)
            # Anti-allucinazione (Bug B, 5/5/2026): notice additiva quando il
            # final_message contiene promesse di azione futura ma nessuno step
            # ok ha registrato l'azione. §2.8 No silent failure: la falsa
            # sicurezza sull'utente e' colpo mortale. Notice prepended (non
            # rewrite) per preservare l'eventuale info utile del messaggio.
            if _detect_unbacked_promise(self.final_message, self.steps):
                self.unbacked_promise_detected = True
                _hallucination_notice = msg("MSG_HALLUCINATION_NOTICE")
                if _hallucination_notice not in (self.final_message or ""):
                    self.final_message = (
                        _hallucination_notice + "\n\n"
                        + (self.final_message or "")
                    ).strip()
        # Propaga attachments dall ultimo step che ne ha prodotti (use
        # case realistico: un solo find_images_indices per turno).
        for s_step in reversed(self.steps):
            res = s_step.result if isinstance(s_step.result, dict) else {}
            atts = res.get("attachments")
            if isinstance(atts, list) and atts:
                self.attachments = atts
                break
        # Footer "elapsed: Xs · chiuso HH:MM:SS" rimosso 7/5/2026 notte
        # (Roberto: ridondante con il badge meta della UI HTTP, valore
        # gia' presente nel jsonl come ts_end-ts_start per telemetria).
        TURN_LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = TURN_LOG_DIR / f"{time.strftime('%Y-%m-%d')}.jsonl"
        # Scrubbing credenziali prima della serializzazione (ADR 0082):
        # passiamo da asdict (snapshot) e ri-iniettiamo le entry pulite.
        record = asdict(self)
        n_redacted_total = [0]
        cleaned_query, _n = _scrub_credentials(record.get("user_query", "") or "")
        n_redacted_total[0] += _n
        record["user_query"] = cleaned_query
        steps_clean = []
        for s in record.get("steps", []):
            if isinstance(s, dict) and isinstance(s.get("raw_args"), dict):
                s["raw_args"] = _scrub_args_recursive(s["raw_args"], n_redacted_total)
            if isinstance(s, dict) and isinstance(s.get("resolved_args"), dict):
                s["resolved_args"] = _scrub_args_recursive(s["resolved_args"], n_redacted_total)
            steps_clean.append(s)
        record["steps"] = steps_clean
        if n_redacted_total[0] > 0:
            record["redacted"] = True
            record["n_redacted_fields"] = n_redacted_total[0]
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


# --- Hook synt-on-the-fly --------------------------------------------------

def _try_synt_compose(mnestoma, target_intent: str, mnest_id: str, *, verbose: bool = False) -> dict | None:
    """Tenta sintesi reattiva (solo compose, niente generate) per un proto-mnest
    appena registrato. Se synt trova una catena di executor firmati che chiude
    il proto, restituisce un dict con un suggerimento per il LLM. Se compose
    fallisce o synt e' indisponibile, ritorna None (degrada graziosamente).
    """
    try:
        s = Synt(mnestoma=mnestoma, router=None)  # router=None: niente generate
        req = synt_make_request(target_intent=target_intent, proto_mnest=mnest_id)
        prop = s.react(req)
    except Exception as ex:
        if verbose:
            print(f"[synt] react failed: {ex}")
        return None
    if prop.state == "composed":
        chain = prop.artefact.get("chain") or []
        first_hop = None
        names = []
        for hop in chain:
            dst = hop.get("dst_executor") if isinstance(hop, dict) else getattr(hop, "dst_executor", None)
            if dst:
                names.append(dst)
                if first_hop is None:
                    first_hop = dst
        return {
            "strategy": "compose",
            "state": "composed",
            "chain": names,
            "first_hop": first_hop,
            "suggestion": (
                f"Esiste una catena di executor firmati ({' -> '.join(names)}) "
                f"che copre questa esigenza. Riprova invocando '{first_hop}' come prossimo passo."
            ),
        }
    if prop.state in ("generating", "born", "proposed"):
        # generate richiede router non nullo, non dovrebbe accadere qui; ma se
        # arriva, segnaliamo proposta in attesa.
        return {
            "strategy": "generate",
            "state": prop.state,
            "suggestion": (
                f"E' stata creata una proposta di executor (stato: {prop.state}, in attesa di "
                f"approvazione utente). Per ora cerca una via alternativa o rinuncia."
            ),
        }
    if prop.state in ("abandoned", "rejected"):
        return {
            "strategy": prop.strategy,
            "state": prop.state,
            "suggestion": (
                f"Sintesi reattiva ha rinunciato (stato: {prop.state}). "
                f"Non c'e' executor disponibile per questa esigenza, cerca un'altra via."
            ),
        }
    return None


# --- Loop pianificatore (multistep con tool-use nativo) -------------------

def run_turn(user_query, *, mode="local", model=None, k=None, k_min=5, k_max=8, think=None, progress=None,
             cap_steps=DEFAULT_CAP_STEPS, cap_same=DEFAULT_CAP_SAME_EXECUTOR,
             scratchpad_threshold=SCRATCHPAD_THRESHOLD_BYTES,
             actor="host", channel="", conversation_id="",
             reference_images=None,
             resume_with_scratchpad=None,
             verbose=False):
    """
    Se k=None (default v1.1), usa adaptive K fra k_min e k_max.
    Se k=int, usa K fisso (legacy/debug).

    `reference_images` (5/5/2026, ADR 0092): lista di path di foto allegate
    al turno (Telegram caption + photo, HTTP drag&drop). Se non vuoto, viene
    iniettato uno step 0 virtuale `@uploaded` nello scratchpad con
    `entries=[{path, reference_image, source}]`. Il PLANNER vede le foto
    come prima entry (consumer-match Layer 3 porta find_images_indices nel
    pool); chiama `find_images_indices(from_step=1, idx="scene")` e
    l'auto-explode (consumer match) inietta `reference_images=[paths]`.

    `resume_with_scratchpad` (12/5/2026, fix bug propose+notify): lista di
    step records pre-existing nella forma `[{step, tool, args, observation}]`.
    Quando presente, il turno parte con scratchpad gia' popolato e il loop
    inizia da step_num = len(prior_steps) + 1. Usato da
    `orchestration._process_resume_planner_with_dialog_values` per riprendere
    pipeline multi-pipeline dopo un get_inputs MID-pipeline. Bypassa
    fast_path / seed_step (sono inutili: il context ha gia' steps).
    Determinismo §7.9.
    """
    log = TurnLog(ts_start=time.time(), user_query=user_query,
                   actor=actor or "host", channel=channel or "",
                   conversation_id=conversation_id or "")

    # ── Strato 1 (ADR 0089): estrazione automatica delle credenziali ──
    # Se la query contiene user/pwd inline, salviamoli cifrati subito e
    # passiamo al PLANNER una versione redacted. La metadata (solo
    # domain+context, NO password) viene iniettata nel system prompt come
    # blocco prescrittivo cosi' il PLANNER puo' settare credentials_domain
    # nei tool che lo accettano (admin per CIFS, login_session per web).
    redacted_query, extracted_meta = apply_credentials_extraction(user_query)
    user_query_for_run = redacted_query
    if extracted_meta:
        log.user_query = redacted_query  # niente plaintext nel log
        log.redacted = True
        log.n_redacted_fields = max(log.n_redacted_fields, len(extracted_meta))

    # Admin chat commands shortcut (11/5/2026): `/admin user <action>` per
    # gestire utenti e pair URL via chat o Telegram. Determinismo §7.9:
    # niente PLANNER, niente synth. Restricted a actor='host'.
    try:
        from admin_chat_commands import matches as _adm_match, dispatch as _adm_disp
        if _adm_match(user_query_for_run):
            import os as _os
            origin = _os.environ.get("METNOS_PUBLIC_ORIGIN",
                                       "http://localhost:8770")
            adm_msg = _adm_disp(user_query_for_run, actor=actor, origin=origin)
            if adm_msg is not None:
                log.turn_id = uuid.uuid4().hex[:16]
                log.final_message = adm_msg
                log.final_kind = "answer"
                log.ts_end = time.time()
                log.write()
                return log
    except ImportError:
        pass

    # Blocco prescrittivo per il PLANNER: lista delle credenziali estratte
    # nel turno corrente. Solo metadata (domain + context), MAI le pwd.
    # ADR 0092: il PLANNER è caricato da runtime/prompts/<lang>/planner/.
    # Fase C (11/5/2026): rendering 3-layer (_core + sections + _footer) via
    # `prompt_loader.compose()`. Selettore deterministico delle sezioni via
    # `vocab.sections_for_object(intent.object)`. Quando l'intent extractor
    # non si e' ancora eseguito (early route_info=None nel turno) o l'object
    # e' unknown, passiamo sections=None → composer include TUTTE le sezioni
    # (degrade graceful). Lo split avviene piu' avanti nel turno via re-render
    # se serve, ma per il PLANNER prompt sistema il render iniziale e' OK con
    # all-sections — il routing si concretizza ai prossimi step.
    # Lang esplicito al call site (5/5/2026): default da config.DEFAULT_LANG.
    try:
        from vocab import sections_for_object as _sections_for_object
        # `route_info` non e' ancora disponibile a questo punto (precede
        # l'intent extractor del turno principale). Per il primo prompt
        # PLANNER passiamo sections=None (= all sections) come degrade
        # graceful. Refactor futuro: rendering lazy del system prompt ad
        # ogni step, basato sull'intent extractor risolto.
        _planner_sections = None
    except Exception:
        _planner_sections = None
    _now_vars = _render_now_vars()
    planner_system = prompt_loader.compose(
        "planner",
        DEFAULT_LANG,
        sections=_planner_sections,
        vocab_actions=_vocab_actions(),
        vocab_objects=_vocab_objects(),
        vocab_qualifiers=_vocab_qualifiers(),
        project_paths=_render_project_paths_block(),
        users_known=_render_users_known_block(),
        **_now_vars,
    )
    if extracted_meta:
        lines = [
            "",
            "═" * 70,
            "CREDENZIALI ESTRATTE DALLA QUERY (Strato 1 — ADR 0089)",
            "Le credenziali user/pwd sono state estratte e salvate cifrate.",
            "Le password NON ti sono visibili. Se chiami admin/login_session ",
            "per operazioni su questi host, passa `credentials_domain` cosi'",
            "il sudoer/login risolve le credenziali al fire time.",
            "",
        ]
        for m in extracted_meta:
            ctx = m.get("context") or {}
            host = ctx.get("host", "?")
            binding = ctx.get("binding", "?")
            share = ctx.get("share")
            line = f"  - domain=\"{m['domain']}\" binding={binding} host={host}"
            if share:
                line += f" share={share}"
            lines.append(line)
        lines.append("═" * 70)
        planner_system = planner_system + "\n" + "\n".join(lines)

    # Reference images uploaded (ADR 0092): blocco prescrittivo al PLANNER
    # cosi' il primo step richiama find_images_indices con from_step=1
    # (entries del @uploaded virtuale) invece di chiedere altre foto.
    _ref_images_for_prompt = [p for p in (reference_images or [])
                               if isinstance(p, str) and p.strip()]
    if _ref_images_for_prompt:
        n_ref = len(_ref_images_for_prompt)
        sample = _ref_images_for_prompt[0]
        ref_block = [
            "",
            "═" * 70,
            f"FOTO ALLEGATE AL TURNO (ADR 0092) — {n_ref} reference image(s)",
            "L'utente ha allegato foto. Sono gia' in scratchpad come step 1",
            "virtuale `@uploaded` con `entries=[{path, reference_image, ...}]`.",
            "DEVI: usare `find_images_indices(from_step=1, idx=\"scene\")` per",
            "trovare foto simili (image-to-image SigLIP). Per match per volti:",
            "`idx=\"persons\"`. Per prossimita' GPS: `idx=\"gps\"`.",
            "NON DEVI: chiedere all'utente altre foto: ce le ha gia' fornite.",
            f"Esempio path: {sample}",
            "═" * 70,
        ]
        planner_system = planner_system + "\n" + "\n".join(ref_block)

    # Progress: avvia subito il canale visivo se passato dal daemon. Per turni
    # &gt; 5 s l'utente vede "sto pensando..." con typing animation Telegram-native.
    if progress is not None:
        try:
            progress.start("Sto pensando il modo migliore di rispondere…")
        except Exception as _e:  # silent swallow (auto-fixed)
            log.warning("silent exception in %s: %s", __name__, _e)
    catalog = filter_for_visibility(load_catalog(), VISIBILITY_COMPOSER)
    if len(catalog) == 0:
        log.final_kind = "error"; log.final_message = "(catalogo vuoto)"
        log.ts_end = time.time(); log.write(); return log

    turn_id = uuid.uuid4().hex[:16]
    log.turn_id = turn_id
    sp = Scratchpad.open()
    sp.gc()  # cleanup periodico

    # ── Fast path deterministico (ADR 0094) ──────────────────────────
    # Pattern catch-all PRIMA del PLANNER LLM per query triviali ad
    # alta confidenza (es. "che ora e", "what time is it"). Se match
    # esatto: invoca direttamente l'executor, formatta la final_message
    # con template deterministico, ZERO chiamate LLM. Risparmio: ~50 s
    # per pattern coperti vs flusso PLANNER completo.
    # Conservativo: sull'incertezza ritorna None e prosegue normale.
    # Reference images NON triviali: se ci sono allegati, salta il
    # fast path (l'utente ha intenzioni piu' ricche del pattern letterale).
    # resume_with_scratchpad: skip anche fast_path (turno gia' avviato, il
    # PLANNER continua dallo stato in history).
    if not _ref_images_for_prompt and not resume_with_scratchpad:
        _fp_hit = try_fast_path(user_query_for_run, lang=DEFAULT_LANG,
                                  default_timezone=DEFAULT_TIMEZONE)
        if _fp_hit is not None:
            _fp_exec = next((e for e in catalog if e.name == _fp_hit["executor"]), None)
            if _fp_exec is not None:
                _fp_step = StepLog(step_num=1)
                _fp_step.chosen_tool = _fp_hit["executor"]
                _fp_step.raw_args = dict(_fp_hit["args"])
                _fp_step.resolved_args = dict(_fp_hit["args"])
                _fp_step.vaglio_approved = True  # short-circuit, no vaglio (read-only)
                _t_fp = time.perf_counter()
                try:
                    _fp_obs = invoke_executor(
                        _fp_exec, _fp_hit["args"],
                        timeout_s=getattr(_fp_exec, "timeout_s", None) or 10,
                        autonomy="supervised", turn_id=turn_id,
                        actor=actor, channel=channel,
                    )
                except Exception as ex:
                    _fp_obs = {"ok": False,
                                "error": f"{type(ex).__name__}: {ex}"}
                _fp_step.exec_ms = int((time.perf_counter() - _t_fp) * 1000)
                _fp_step.result = _fp_obs
                # Marker audit per turn log: questo step e' arrivato
                # dal fast path, NON dal PLANNER.
                if hasattr(_fp_step, "__dict__"):
                    _fp_step.__dict__["fast_path"] = True
                if _fp_obs.get("ok"):
                    log.steps.append(_fp_step)
                    log.final_kind = "answer"
                    log.final_message = _fp_hit["render"](_fp_obs)
                    if verbose:
                        print(f"[fast_path] hit pattern='{_fp_hit['pattern']}' "
                              f"executor={_fp_hit['executor']} exec_ms={_fp_step.exec_ms}")
                    log.ts_end = time.time(); log.write(); return log
                # Se l'executor fallisce: NON crashare il turno. Cadi nel
                # flusso normale PLANNER, che potra' tentare strade alternative
                # (timezone diversa, error reporting). NON aggiungere lo step
                # all'history: il PLANNER deve poter ripartire pulito.
                if verbose:
                    print(f"[fast_path] match ma executor fallito ({_fp_obs.get('error')!r}), "
                          f"fallback PLANNER")

    chosen_mode = ModeRouter(mode).select(user_query_for_run, catalog)
    log.mode = chosen_mode

    # Telemetria fine (ADR 0080): prefilter_ms + intent_ms misurati al
    # confine, attribuiti allo step 1 (sotto). intent_ms e' la quota LLM
    # interna a rank_adaptive; prefilter_ms = totale - quota LLM.
    _intent_ms_acc = 0  # accumulatore quota LLM dentro _intent_llm
    if k is None:
        # Intent extractor LLM-based (gemma 4 26B middle tier) come primary
        # signal del prefilter (Roberto 29/4/2026). Fallback al bag-of-words
        # se l'LLM e' down o non riesce a parsare.
        def _intent_llm(system, user, max_tokens=80, think=False):
            nonlocal _intent_ms_acc
            from llm_router import LLMRouter
            _r = LLMRouter()
            _p = _r.provider("middle")  # gemma 4 26B
            _t0 = time.perf_counter()
            _res = _p.chat(system, user, max_tokens=max_tokens,
                           temperature=0, think=think)
            _intent_ms_acc += int((time.perf_counter() - _t0) * 1000)
            return {"text": _res.text or "",
                    "in_tokens": _res.in_tokens,
                    "out_tokens": _res.out_tokens}
        _t_prefilter0 = time.perf_counter()
        candidates, route_info = rank_adaptive(
            user_query_for_run, catalog, k_min=k_min, k_max=k_max,
            llm_call=_intent_llm,
        )
        _prefilter_total_ms = int((time.perf_counter() - _t_prefilter0) * 1000)
        if verbose:
            conf = route_info.get('confidence')
            conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) else str(conf)
            print(f"[router] K={route_info['chosen_k']} confidence={conf_s} reason={route_info['reason']}")
            if route_info.get("intent"):
                print(f"[router] intent={route_info['intent']}")
    else:
        _t_prefilter0 = time.perf_counter()
        candidates = rank(user_query_for_run, catalog, k=k)
        _prefilter_total_ms = int((time.perf_counter() - _t_prefilter0) * 1000)
        route_info = {"chosen_k": len(candidates), "confidence": None, "reason": "fixed_k"}
    # Quota prefilter «non-LLM» (token rank + adattivita') = totale - intent.
    _prefilter_only_ms = max(0, _prefilter_total_ms - _intent_ms_acc)

    # P6 (12/5/2026) — Multi-pipeline propose / notify injection.
    # Bug live turn 35431172: query «proponi N orari ... e mandami email
    # con la scelta». Intent LLM ha estratto verb=send object=messages →
    # rank_with_intent ha popolato top-K con send_messages,
    # find_messages_google_workspace, read_messages — NESSUN tool calendar.
    # Step 1: find_messages_google_workspace (sbagliato).
    # Step 2: send_messages premature (sbagliato, scelta non fatta).
    # Step 3: find_events_empty (corretto ma tardi).
    # Defense in depth §7.9: il runtime detecta tre forme di multi-pipeline
    # e amplia il pool. Le tre forme sono ortogonali (propose XOR notify XOR
    # entrambi) ma applicano la STESSA logica add-only (no shim §7.1):
    #   propose-only → inietta find_events_empty + get_inputs + create_events
    #                   (variante propose+fire; calendar pipeline producer/
    #                   consumer + dialog).
    #   notify-only  → inietta send_messages (consumer di notifica).
    #   entrambi     → inietta TUTTA la pipeline 6-step + rimuove gli
    #                   hijackers mail-search (la query NON cerca mail).
    # NB: NON rimuoviamo send_messages: e' il consumer corretto della
    # variante (a)/(b). NON rimuoviamo find_events_empty/create_events/
    # read_events: sono i producer/consumer della pipeline. La rimozione
    # dei hijackers mail-search scatta SOLO se entrambi i flag, perche'
    # in solo-notify la query potrebbe legittimamente cercare destinatari
    # via Gmail (caso «manda email a Mario» non triggera notify-cont).
    _is_propose = _query_is_propose_intent(user_query_for_run)
    _is_notify = _query_has_notify_continuation(user_query_for_run)
    if _is_propose or _is_notify:
        _CALENDAR_PROPOSE_TOOLS = (
            "get_now",
            "find_events_empty",
            "create_events",
            "read_events",
            "get_inputs",
        )
        _NOTIFY_TOOLS = ("send_messages",)
        _HIJACKERS_BOTH = frozenset({
            # Tool di RICERCA mail: la query NON cerca mail esistenti.
            "find_messages_google_workspace",
            "read_messages",
            "read_messages_google_workspace",
        })
        if _is_propose and _is_notify:
            needed = _CALENDAR_PROPOSE_TOOLS + _NOTIFY_TOOLS
            hijackers = _HIJACKERS_BOTH
            route_info["multi_pipeline_propose_notify"] = True
        elif _is_propose:
            needed = _CALENDAR_PROPOSE_TOOLS
            hijackers = frozenset()
            route_info["multi_pipeline_propose_only"] = True
        else:  # notify-only
            needed = _NOTIFY_TOOLS
            hijackers = frozenset()
            route_info["multi_pipeline_notify_only"] = True

        existing_names = {e.name for e in candidates}
        # Rimuovi hijackers (add-only e' la default policy del rerank, ma
        # qui rimuoviamo perche' sono distrattori semantici dimostrati).
        if hijackers:
            candidates = [e for e in candidates if e.name not in hijackers]
        # Promote i pipeline tools all'INIZIO della lista (priorita'): bug
        # live turn e0cd5bfe — planner ha skippato get_inputs perche' era
        # in 7° posizione del top-K, scegliendo send_messages diretto.
        # Inserire prima rende visibile il sequencing corretto al LLM.
        _to_promote = []
        for _need in needed:
            _exec = next((e for e in catalog if e.name == _need), None)
            if _exec is None:
                continue
            if _need not in existing_names:
                _to_promote.append(_exec)
            else:
                # Gia' nel pool: rimuovi e re-inserisci all'inizio.
                candidates = [e for e in candidates if e.name != _need]
                _to_promote.append(_exec)
        # Ordine pipeline canonico: get_now, find_events_empty, get_inputs,
        # create_events, read_events, send_messages. Riordina _to_promote
        # per rispettare la sequenza naturale.
        _pipeline_order = {n: i for i, n in enumerate(needed)}
        _to_promote.sort(key=lambda e: _pipeline_order.get(e.name, 99))
        candidates = _to_promote + candidates
        if verbose:
            print(f"[multi_pipeline] propose={_is_propose} notify={_is_notify}: "
                  f"injected {needed}, hijackers={list(hijackers)}")

    log.candidates = [e.name for e in candidates]
    if verbose:
        print(f"[prefilter] candidati: {log.candidates}")

    # Fase C3 (11/5/2026): re-render planner_system con sezioni mirate dopo
    # che `route_info` (con intent.verb/intent.object) e' disponibile. Selettore
    # deterministico `vocab.sections_for_object(obj)` (§7.9). Fallback: se
    # l'object e' unknown o non mappato, include TUTTE le sezioni (degrade
    # graceful — comportamento del primo render). Confidence dal route_info
    # come ulteriore guard: se < 0.6 (intent extractor incerto), all sections.
    try:
        from vocab import sections_for_object as _sections_for_object
        _intent_for_route = (route_info or {}).get("intent") or {}
        _conf = (route_info or {}).get("confidence")
        _obj = _intent_for_route.get("object")
        if not isinstance(_conf, (int, float)) or _conf < 0.6:
            _sections_resolved = None  # all
        else:
            _candidate_secs = _sections_for_object(_obj)
            _sections_resolved = list(_candidate_secs) if _candidate_secs else None
        # Re-render solo se la lista differisce dall'all-sections iniziale.
        if _sections_resolved is not None:
            _planner_targeted = prompt_loader.compose(
                "planner", DEFAULT_LANG,
                sections=_sections_resolved,
                vocab_actions=_vocab_actions(),
                vocab_objects=_vocab_objects(),
                vocab_qualifiers=_vocab_qualifiers(),
                project_paths=_render_project_paths_block(),
                users_known=_render_users_known_block(),
                **_now_vars,
            )
            # Riapplica gli addenda (credenziali + reference images) gia'
            # accumulati nel `planner_system`, calcolando la diff rispetto
            # all'iniziale rendering all-sections.
            _planner_all = prompt_loader.compose(
                "planner", DEFAULT_LANG, sections=None,
                vocab_actions=_vocab_actions(),
                vocab_objects=_vocab_objects(),
                vocab_qualifiers=_vocab_qualifiers(),
                project_paths=_render_project_paths_block(),
                users_known=_render_users_known_block(),
                **_now_vars,
            )
            if planner_system.startswith(_planner_all):
                _suffix = planner_system[len(_planner_all):]
                planner_system = _planner_targeted + _suffix
            # Se l'utente ha esteso planner_system in modo non-prefix (caso
            # raro), lasciamo l'iniziale all-sections (no regress, no info loss).
    except Exception as _e:
        # Niente fail su difetti del routing: rimaniamo con all-sections.
        log.warning("planner section routing skipped: %s", _e)

    # IMPLICIT ACTIONS injection (ADR 0129, 14/5/2026): se l'intent extractor
    # ha rilevato azioni mutating implicite (pattern noun→object senza verbo
    # mutating esplicito nella query), inietta un blocco strutturato nel
    # prompt PLANNER cosi' il LLM vede le entry come hint deterministico
    # (la regola comportamentale e' nell'invariante `_core.j2`).
    try:
        _intent = route_info.get("intent") if isinstance(route_info, dict) else None
        _implicit = (_intent or {}).get("implicit_actions") if isinstance(_intent, dict) else None
        print(f"[implicit_actions] route_intent={bool(_intent)} "
              f"implicit_count={len(_implicit or []) if isinstance(_implicit, list) else 'N/A'} "
              f"resume={bool(resume_with_scratchpad)}", flush=True)
        if isinstance(_implicit, list) and _implicit:
            _ia_lines = [
                "",
                "═" * 70,
                "IMPLICIT ACTIONS rilevate dall'intent extractor (ADR 0129):",
            ]
            for _a in _implicit:
                if not isinstance(_a, dict):
                    continue
                _ia_lines.append(
                    f"  - verb={_a.get('verb')!r} "
                    f"object={_a.get('object')!r} "
                    f"strategy={_a.get('strategy')!r} "
                    f"confidence={_a.get('confidence')} "
                    f"(noun='{_a.get('noun_token','')}')"
                )
            _ia_lines.append("Applica la regola IMPLICIT ACTIONS dell'invariante.")
            _ia_lines.append("═" * 70)
            planner_system = planner_system + "\n" + "\n".join(_ia_lines)
    except Exception as _e:
        if verbose:
            print(f"[implicit_actions] injection failed: {_e}")

    # Provider selection (27/4 sera): default = Gemma 4 26B (llamacpp) come "middle" tier
    # locale per pianificare task multi-step. Override esplicito via env METNOS_PLANNER_*.
    # think (28/4 sera): default True sul planner.
    # Il bug Gemma "tool_call magnetico get_files_metadata anche con reasoning
    # corretto" si manifestava solo con tools_for_step gonfio (15-22 tool):
    # il pattern matching del modello sotto-pesava le description e si attaccava
    # a nomi calamita. Con prefilter k_max=8 + cap effettivo a 9 (incl. synth),
    # il bug non si riproduce piu': budget 256/512/768/1024 producono tutti
    # tool_call corretto su query mail/foto/compute. Default reasoning_budget
    # 512 (sweet spot: ragionamento sufficiente, latenza contenuta).
    env_think = os.environ.get("METNOS_PLANNER_THINK")
    if think is None:
        if env_think is not None:
            think = env_think.lower() in ("1", "true", "yes", "on")
        else:
            think = True  # default v1.1: think on (post-fix prefilter k_max=8)
    if model:
        provider = OllamaProvider(model=model, think=think)
    else:
        planner_provider = os.environ.get("METNOS_PLANNER_PROVIDER", "llamacpp")
        if planner_provider == "ollama":
            provider = OllamaProvider(
                model=os.environ.get("METNOS_PLANNER_MODEL", "qwen3:8b"),
                endpoint=os.environ.get("METNOS_PLANNER_ENDPOINT", "http://localhost:11434"),
                think=think,
            )
        else:
            provider = make_provider_from_spec({
                "provider": "llamacpp",
                "model": os.environ.get("METNOS_PLANNER_MODEL", "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"),
                "endpoint": os.environ.get("METNOS_PLANNER_ENDPOINT", "http://127.0.0.1:8080"),
            })
    tracker = CostTracker()
    is_multistep = (chosen_mode == "local")
    mnestoma = Mnestoma()  # storage di mnest e proto-mnest

    base_tools = render_tools_for_provider(candidates)
    history_for_llm = []  # messages array per chat API
    history_for_refs = []  # per resolve_references
    same_count = Counter()

    # ── resume_with_scratchpad (12/5/2026) ────────────────────────────
    # Continuation di un turno precedente fermato a get_inputs MID-pipeline.
    # Pre-popola scratchpad + history LLM + step counter. Bypass fast_path
    # e seed_step (sono per turni nuovi). Determinismo §7.9: nessun LLM.
    _resume_step_offset = 0
    if resume_with_scratchpad and isinstance(resume_with_scratchpad, list):
        for rs in resume_with_scratchpad:
            if not isinstance(rs, dict):
                continue
            _rs_step = int(rs.get("step") or 0) or (len(history_for_refs) + 1)
            _rs_tool = rs.get("tool") or "unknown"
            _rs_args = rs.get("args") or {}
            _rs_obs = rs.get("observation") or {}
            history_for_refs.append({
                "step": _rs_step, "tool": _rs_tool,
                "args": _rs_args, "observation": _rs_obs,
            })
            # StepLog audit-only: il log mostra la genesi del turno
            # continuation. Step records senza exec_ms (gia' eseguiti
            # nel turno precedente). vaglio_approved=True: gia' passati.
            _rs_step_log = StepLog(step_num=_rs_step)
            _rs_step_log.chosen_tool = _rs_tool
            _rs_step_log.raw_args = dict(_rs_args) if isinstance(_rs_args, dict) else {}
            _rs_step_log.resolved_args = dict(_rs_args) if isinstance(_rs_args, dict) else {}
            _rs_step_log.result = _rs_obs
            _rs_step_log.vaglio_approved = True
            _rs_step_log.error = "resumed_from_prior_turn"
            log.steps.append(_rs_step_log)
            # Tool_call virtuale per il LLM: il prossimo step PLANNER vede
            # gli step precedenti come tool calls eseguiti.
            _rs_call_id = f"resume_{_rs_step}"
            history_for_llm.append({
                "role": "assistant",
                "tool_calls": [{
                    "id": _rs_call_id, "type": "function",
                    "function": {
                        "name": _rs_tool,
                        "arguments": (_rs_args
                                      if isinstance(_rs_args, dict) else {}),
                    },
                }],
            })
            history_for_llm.append({
                "role": "tool", "tool_call_id": _rs_call_id,
                "name": _rs_tool,
                "content": json.dumps(_rs_obs, ensure_ascii=False),
            })
            _resume_step_offset = max(_resume_step_offset, _rs_step)
        if verbose:
            print(f"[resume] pre-populated {len(history_for_refs)} steps "
                  f"(offset={_resume_step_offset})")

    # ── Seed-step injection (ADR 0099) ────────────────────────────────
    # Quando la query contiene un URL completo, inietta deterministicamente
    # `read_urls_html(urls=[URL])` come step 1, BYPASSANDO la chiamata
    # PLANNER per quel primo step. Il PLANNER prende il controllo dallo
    # step 2 in poi, vedendo il risultato del read in history.
    #
    # Razionale: la regola PLANNER (url_explicit_seed) di ADR 0098 (URL
    # esplicito → read primo step) si e' rivelata insufficiente live (turn federvolley
    # 7/5/2026 15:29: PLANNER ha comunque scelto find_urls). Garantirlo
    # nel runtime e' deterministico; PLANNER resta libero post step 1.
    #
    # Esclusioni: skip se reference_images allegati (semantica diversa) o
    # se resume_with_scratchpad (history gia' popolata).
    _seed_step_used = False
    _seed_step_n = 0
    if not _ref_images_for_prompt and not resume_with_scratchpad:
        _seed_hit = try_seed_step(user_query_for_run)
        if _seed_hit is not None:
            _seed_exec = next(
                (e for e in catalog if e.name == _seed_hit["executor"]),
                None,
            )
            if _seed_exec is not None:
                _seed_step_n = 1
                _seed_step = StepLog(step_num=_seed_step_n)
                _seed_step.chosen_tool = _seed_hit["executor"]
                _seed_step.raw_args = dict(_seed_hit["args"])
                _seed_step.resolved_args = dict(_seed_hit["args"])
                _seed_step.vaglio_approved = True  # read-only safe-by-construction
                _t_seed = time.perf_counter()
                try:
                    _seed_obs = invoke_executor(
                        _seed_exec, _seed_hit["args"],
                        timeout_s=getattr(_seed_exec, "timeout_s", None) or 30,
                        autonomy="supervised", turn_id=turn_id,
                        actor=actor, channel=channel,
                    )
                except Exception as ex:
                    _seed_obs = {"ok": False,
                                  "error": f"{type(ex).__name__}: {ex}"}
                _seed_step.exec_ms = int((time.perf_counter() - _t_seed) * 1000)
                _seed_step.result = _seed_obs
                _seed_step.seed_step = True
                # Inserimento nel log + history per il PLANNER successivo.
                log.steps.append(_seed_step)
                _seed_call_id = "seed_0"
                history_for_refs.append({
                    "step": _seed_step_n,
                    "tool": _seed_hit["executor"],
                    "args": _seed_hit["args"],
                    "observation": _seed_obs,
                })
                history_for_llm.append({
                    "role": "assistant",
                    "tool_calls": [{
                        "id": _seed_call_id, "type": "function",
                        "function": {
                            "name": _seed_hit["executor"],
                            "arguments": _seed_hit["args"],
                        },
                    }],
                })
                history_for_llm.append({
                    "role": "tool",
                    "tool_call_id": _seed_call_id,
                    "name": _seed_hit["executor"],
                    "content": json.dumps(_seed_obs, ensure_ascii=False),
                })
                _seed_step_used = True
                if verbose:
                    print(f"[seed_step] hit url={_seed_hit['url']} "
                          f"executor={_seed_hit['executor']} "
                          f"exec_ms={_seed_step.exec_ms} "
                          f"ok={_seed_obs.get('ok')}")

    # ── Reference images uploaded (ADR 0092, 5/5/2026) ────────────
    # Foto allegate al turno via Telegram caption-photo o HTTP drag&drop.
    # Inietta uno step 0 virtuale `@uploaded` nello scratchpad cosi' il
    # PLANNER (al primo step reale) trova entries pronte per consumer-match
    # → find_images_indices(from_step=1, idx=...) → auto-explode in
    # reference_images. Il singular di `reference_images` e' `reference_image`,
    # quindi includere quel campo nelle entries (in piu' a `path` per i
    # consumer-match generici tipo read_files).
    _ref_images = list(reference_images or [])
    _ref_images = [p for p in _ref_images if isinstance(p, str) and p.strip()]
    if _ref_images:
        upload_entries = [
            {"path": p, "reference_image": p, "source": "upload"}
            for p in _ref_images
        ]
        upload_obs = {
            "ok": True,
            "entries": upload_entries,
            "_virtual": True,
            "_kind": "uploaded_reference_images",
            "n": len(upload_entries),
        }
        # Tool call + tool result virtuali, formato compatibile con i
        # provider OpenAI/Ollama tool-use. call_id univoco per evitare
        # collisioni con tc.call_id reali.
        _upload_call_id = "upload_0"
        history_for_refs.append({
            "step": 1,
            "tool": "@uploaded",
            "args": {"source": "upload", "n": len(upload_entries)},
            "observation": upload_obs,
        })
        history_for_llm.append({
            "role": "assistant",
            "tool_calls": [{
                "id": _upload_call_id, "type": "function",
                "function": {"name": "@uploaded", "arguments": {}},
            }],
        })
        history_for_llm.append({
            "role": "tool", "tool_call_id": _upload_call_id,
            "name": "@uploaded",
            "content": json.dumps(upload_obs, ensure_ascii=False),
        })
        # StepLog con step_num=0: virtual, NON conta verso cap_steps. Visibile
        # nel turn log per audit.
        _virtual_step = StepLog(step_num=0)
        _virtual_step.chosen_tool = "@uploaded"
        _virtual_step.raw_args = {"source": "upload"}
        _virtual_step.resolved_args = {}
        _virtual_step.result = upload_obs
        _virtual_step.vaglio_approved = True
        log.steps.append(_virtual_step)
        if verbose:
            print(f"[upload] {len(upload_entries)} reference images → step 0 virtual")


    # Storia (tool, identifier) per detectare duplicati di lettura
    # identifier = args.path per fs_*, args.url per get_urls
    read_calls_seen = []  # list of (step_num, tool_name, identifier)
    consecutive_blocked = 0  # step consecutivi senza progresso (duplicate/inline/error)
    LOOP_BREAK_THRESHOLD = 3

    # Loop start: 2 se seed_step ha gia' consumato step_num=1, altrimenti 1.
    # cap_steps non cambia: il seed_step CONTA come step (consume budget).
    # Se resume_with_scratchpad: parte da `max(prior_step) + 1`.
    if _resume_step_offset > 0:
        _loop_start_step = _resume_step_offset + 1
    else:
        _loop_start_step = _seed_step_n + 1 if _seed_step_used else 1

    for step_num in range(_loop_start_step, cap_steps + 1):
        # Strategia E (ADR 0133): early loop-detect su (tool, error_class)
        # ripetuti. Cattura il caso residuo dove duplicate_call (args
        # identici) + cap_same_executor (10) + consecutive_blocked (3)
        # non scattano abbastanza presto. Soglia 2: due fail consecutivi
        # stesso (tool, error_class) = loop confermato.
        if step_num > _loop_start_step + 1:  # serve almeno 2 step pregressi
            try:
                from loop_detect import (is_repeated_failure,
                                          repeated_failure_hint)
                if is_repeated_failure(log.steps, threshold=2):
                    _e_hint = repeated_failure_hint(log.steps)
                    log.final_kind = "loop_break"
                    log.final_message = msg(
                        "MSG_LOOP_BREAK", n=2,
                        hint=_e_hint or _loop_break_hint(
                            _intent_object_from_route(route_info)),
                    )
                    log.ts_end = time.time(); log.write(); return log
            except Exception as _e:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "loop_detect failed: %s", _e)
        step = StepLog(step_num=step_num)
        # Attribuisci prefilter+intent al PRIMO step LLM (potrebbe essere 1 o 2
        # a seconda del seed_step). Sono il costo di setup del turno
        # (rank_adaptive + intent extractor LLM, una volta sola per turno).
        if step_num == _loop_start_step:
            step.prefilter_ms = _prefilter_only_ms
            step.intent_ms = _intent_ms_acc if _intent_ms_acc > 0 else None
        if progress is not None:
            try:
                progress.update_free(f"step {step_num} · sto decidendo il prossimo passo…")
            except Exception as _e:  # silent swallow (auto-fixed)
                log.warning("silent exception in %s: %s", __name__, _e)
        # Aggiungi il builtin scratchpad_read se ci sono entries di questo turno.
        # SYNTH_REQUEST_TOOL e' sempre disponibile: e' il telos di non-rinuncia
        # cablato come tool meta che il LLM puo' chiamare quando nessun seed copre.
        sp_entries = sp.list_for_turn(turn_id)
        # describe_entries e' magnetic per il planner: lo includiamo SOLO
        # quando il verbo dell'utente e' read/list/find/describe (cioe' la
        # richiesta termina nel mostrare/riassumere). Per verbi d'azione
        # (move/delete/send/...) describe distrae il planner dal completare
        # l'azione (caso live 29/4/2026: sposta mail → describe → loop_break).
        # classify_entries resta sempre: utile come passo intermedio per la
        # maggior parte delle pipeline.
        _intent = (route_info or {}).get("intent") or {}
        _intent_verb = _intent.get("verb")
        _action_verbs = {"move", "delete", "send", "write", "extract", "create",
                         "compress", "compute", "set"}
        _allow_describe = (_intent_verb is None) or (_intent_verb not in _action_verbs)
        synth_tools = [
            SYNTH_REQUEST_TOOL, CLASSIFY_ENTRIES_TOOL, LOCATION_REQUEST_TOOL,
            CREATE_TASKS_TOOL, LIST_TASKS_TOOL,
            DELETE_TASKS_TOOL, READ_TASKS_TOOL,
            SET_TASKS_TOOL, READ_TASKS_HISTORY_TOOL,
        ]
        if _allow_describe:
            synth_tools.append(DESCRIBE_ENTRIES_TOOL)
        # filter_entries e' un pipeline-helper come classify_entries: serve
        # quasi sempre come step intermedio (es. "sposta mail di pubblicita"
        # = read+classify+filter+move). Lo iniettiamo sempre se disponibile
        # nel catalog (e' un executor regolare, non un builtin runtime).
        # Caso live 29/4/2026: senza filter, il planner si blocca dopo
        # classify perche' non puo' selezionare il subset.
        # undo_last_turn e' universalmente utile: l'utente puo' chiedere
        # "annulla" in qualunque momento, indipendentemente dal verbo
        # estratto dall'intent (caso live 29/4/2026: intent mappava
        # "annulla" → delete, undo_last_turn non in candidati → planner
        # innescava synt inutile).
        _UNIVERSAL_HELPERS = ("filter_entries", "sort_entries", "compute_entries", "undo_last_turn")
        # Pipeline helpers che richiedono `from_step` su una lista preesistente:
        # esclusi dal pool al primo step §4.2. Caso live 15/5/2026: query
        # "fissa appuntamento mercoledi mattina dopo le 9" → PLANNER sceglie
        # `filter_entries(from_step=1, ...)` riferendo a se stesso (step 1
        # vuoto) → loop. General-purpose §7.3: vale per ogni *_entries
        # helper data-piping che opera su observation di step precedente.
        # `undo_last_turn` resta perche' e' azione utente diretta a qualsiasi
        # step (compreso il primo: «annulla» fa undo del turno PRECEDENTE).
        _FROM_STEP_HELPERS = frozenset({
            "filter_entries", "sort_entries", "compute_entries",
            "classify_entries", "group_entries", "describe_entries",
        })
        _existing_names = {e.name for e in candidates}
        _added_any = False
        for _helper in _UNIVERSAL_HELPERS:
            if _helper in _existing_names:
                continue
            # Skip al primo step gli helpers from_step-only: senza step
            # precedente con entries non hanno argomento valido.
            if step_num == 1 and _helper in _FROM_STEP_HELPERS:
                continue
            _helper_exec = next((e for e in catalog if e.name == _helper), None)
            if _helper_exec is not None:
                candidates = list(candidates) + [_helper_exec]
                _added_any = True
        # Anche per i candidates gia' presenti via prefilter: al primo step
        # escludi i from_step-helpers. Vale anche se il prefilter li ha
        # scelti (rumore semantico, non utile §4.2).
        if step_num == 1:
            candidates = [e for e in candidates
                          if e.name not in _FROM_STEP_HELPERS]
            _added_any = True
        if _added_any:
            base_tools = render_tools_for_provider(candidates)
        tools_for_step = base_tools + synth_tools + ([SCRATCHPAD_READ_TOOL] if sp_entries else [])

        # Reasoning budget dinamico (ADR 0099): step >= 2 ha history,
        # ranker gia' applicato, pool tool ristretto → decisione piu'
        # vincolata. Riduciamo da 512 (default LlamaCpp) a 256 → ~50%
        # latency PLANNER per step 2+. Step 1 mantiene 768 (poco piu' del
        # default) per il setup iniziale piu' aperto. Pass-through solo a
        # provider che lo supportano (LlamaCpp); altri provider ignorano
        # il kwarg via filter.
        _chat_kwargs: dict = dict(max_tokens=4096, temperature=0, think=think)
        if getattr(provider, "name", "") == "llamacpp":
            # Override env-driven per bench (12/5/2026 sera):
            # METNOS_REASONING_BUDGET="dyn" (default ADR 0099) | "<int>" flat per tutti gli step
            _rb_env = os.environ.get("METNOS_REASONING_BUDGET", "dyn")
            if _rb_env == "dyn":
                _chat_kwargs["reasoning_budget"] = 768 if step_num == _loop_start_step else 256
            else:
                try:
                    _chat_kwargs["reasoning_budget"] = int(_rb_env)
                except ValueError:
                    _chat_kwargs["reasoning_budget"] = 768 if step_num == _loop_start_step else 256

            # ADR 0133: grammar-constrained tool_call opt-in via env.
            # `METNOS_GRAMMAR=1` → genera grammar GBNF dal pool tools_for_step
            # e forza il LLM a emettere SOLO JSON tool_call valido. Risolve
            # bug PLANNER fragility (thinking loop, prosa al posto di
            # tool_call). Implicitamente disabilita thinking per quel call
            # (grammar + thinking + max_tokens collidono). §7.9 deterministico.
            if os.environ.get("METNOS_GRAMMAR", "0") == "1":
                try:
                    from tool_grammar import (generate_tool_grammar,
                                                filter_pool_for_grammar)
                    from prefilter import _QUERY_DEPENDENT_PRECURSORS
                    _proximity_markers = next(
                        (mk for _, prec, mk in _QUERY_DEPENDENT_PRECURSORS
                         if prec == "get_location"), ()
                    )
                    _pool_for_grammar, _excluded = filter_pool_for_grammar(
                        tools_for_step,
                        user_query_for_run or "",
                        proximity_markers=_proximity_markers,
                    )
                    # final_answer synthetic tool (ADR 0133 ext, 15/5/2026):
                    # abilitato da step 2 in poi. Allo step 1 forziamo
                    # l'esecuzione di un producer (no early-exit). Senza
                    # questo, il LLM grammar-mode non puo' emettere final
                    # naturale: regrediva su describe_entries duplicato.
                    _allow_fa = step_num >= 2
                    _grammar = generate_tool_grammar(
                        _pool_for_grammar, allow_final_answer=_allow_fa,
                    )
                    if _grammar:
                        _chat_kwargs["grammar"] = _grammar
                        if verbose:
                            print(f"[grammar] step {step_num}: "
                                  f"grammar {len(_grammar)} chars su "
                                  f"{len(_pool_for_grammar)} tools "
                                  f"(filtered {_excluded or '-'}, "
                                  f"final_answer={_allow_fa})")
                except Exception as _ex:
                    # `log` qui e' TurnLog (shadow): uso logger module
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "grammar generation failed: %s", _ex)

        try:
            r = provider.chat_with_tools(
                planner_system, user_query_for_run, tools_for_step,
                history=history_for_llm, **_chat_kwargs,
            )
        except ProviderError as e:
            step.error = f"LLM error: {e}"; log.steps.append(step)
            log.final_kind = "error"; log.final_message = f"(errore LLM: {e})"
            log.ts_end = time.time(); log.write(); return log

        tracker.record_post_call(provider.name, r.model, r.in_tokens, r.out_tokens)
        step.llm_in_tokens = r.in_tokens; step.llm_out_tokens = r.out_tokens
        step.llm_latency_ms = r.latency_ms
        step.llm_text = r.text or ""
        step.llm_thinking = r.thinking or ""
        if verbose:
            print(f"[step {step_num}] llm {r.in_tokens}->{r.out_tokens} toks in {r.latency_ms}ms, tool_calls={len(r.tool_calls)}")
            if r.thinking:
                print(f"[step {step_num}] thinking: {r.thinking[:120]}…")

        # Caso 1: nessun tool_call -> testo finale
        if not r.tool_calls:
            log.steps.append(step)
            log.final_kind = "answer"; log.final_message = r.text or "(risposta vuota)"
            log.ts_end = time.time(); log.write(); return log

        # Caso 2: tool_call (D7 sequenziale = uno solo per turno).
        # Bug Gemma 4 26B: a volte emette 2+ tool_calls paralleli — il primo e'
        # un "placeholder magnetico" con args vuoti (es. get_files_metadata
        # entries=[]), il secondo/ultimo e' quello corretto coi reali args
        # derivati dalla query. Selettore: scegli il tool_call con args NON
        # vuoti; se piu' di uno qualifica, prendi l'ultimo (Gemma tende a
        # mettere l'intent "vero" in coda). Se nessuno ha args, prendi il
        # primo (fallback degenere).
        def _has_real_args(tcx):
            a = tcx.arguments if isinstance(tcx.arguments, dict) else {}
            for v in a.values():
                if v is None: continue
                if isinstance(v, (list, dict)) and len(v) == 0: continue
                if isinstance(v, str) and not v.strip(): continue
                return True
            return False
        candidates_tc = [t for t in r.tool_calls if _has_real_args(t)]
        if not candidates_tc:
            candidates_tc = list(r.tool_calls)
        tc = candidates_tc[-1]
        if len(r.tool_calls) > 1 and verbose:
            print(f"[step {step_num}] selected '{tc.name}' from {len(r.tool_calls)} tool_calls: {[t.name for t in r.tool_calls]}")
        chosen_name = tc.name
        raw_args = tc.arguments if isinstance(tc.arguments, dict) else {}
        step.chosen_tool = chosen_name
        step.raw_args = raw_args

        # Synthetic `final_answer` da grammar (ADR 0133 ext, 15/5/2026):
        # il LLM in grammar-mode emette `final_answer({message:"..."})`
        # come tool_call per chiudere il turno con testo naturale. Il
        # runtime intercetta qui e termina senza invocare alcun executor.
        if chosen_name == "final_answer":
            _msg = raw_args.get("message", "") if isinstance(raw_args, dict) else ""
            log.steps.append(step)
            log.final_kind = "answer"
            log.final_message = str(_msg).strip() or (r.text or "(risposta vuota)")
            log.ts_end = time.time(); log.write(); return log

        # ADR 0133 Strategia 3: post-decode semantic validation per grammar
        # mode. Grammar GBNF garantisce sintassi (JSON ben formato + name in
        # enum), NON semantica (args possono non rispettare schema, es.
        # required missing, type mismatch, enum non in list). Se validazione
        # fail: NON eseguiamo l'executor (perderemmo tempo subprocess);
        # iniettiamo error nel history_for_llm cosi' il prossimo step LLM
        # vede il messaggio e corregge. Determinismo §7.9.
        if os.environ.get("METNOS_GRAMMAR", "0") == "1":
            try:
                from tool_grammar import validate_tool_call as _vtc
                _ok, _err = _vtc(
                    {"name": chosen_name, "arguments": raw_args},
                    tools_for_step,
                    allow_final_answer=(step_num >= 2),
                )
            except Exception as _ex:
                _ok, _err = True, ""  # fail-open: non bloccare se validator buggy
            if not _ok:
                step.error = f"grammar_post_validate: {_err}"
                step.result = {
                    "ok": False,
                    "error": _err,
                    "error_class": "invalid_args",
                    "_grammar_post_validate_failed": True,
                }
                log.steps.append(step)
                history_for_refs.append({
                    "step": step_num, "tool": chosen_name,
                    "args": raw_args, "observation": step.result,
                })
                # History LLM: error visibile al prossimo step → il LLM
                # corregge args. Limite consecutive_blocked previene loop.
                history_for_llm.append({
                    "role": "assistant",
                    "tool_calls": [{
                        "id": tc.call_id, "type": "function",
                        "function": {"name": chosen_name,
                                     "arguments": json.dumps(raw_args)},
                    }],
                })
                history_for_llm.append({
                    "role": "tool", "tool_call_id": tc.call_id,
                    "name": chosen_name,
                    "content": json.dumps({"ok": False, "error": _err,
                                            "error_class": "invalid_args"}),
                })
                consecutive_blocked += 1
                if consecutive_blocked >= LOOP_BREAK_THRESHOLD:
                    log.final_kind = "loop_break"
                    _hint = _loop_break_hint(_intent_object_from_route(route_info))
                    log.final_message = msg("MSG_LOOP_BREAK",
                                              n=consecutive_blocked, hint=_hint)
                    log.ts_end = time.time(); log.write(); return log
                continue
        if progress is not None:
            try:
                # Label canale-agnostic: niente tag HTML qui (Telegram con
                # parse_mode=HTML li renderizza, ma SSE → chat HTML browser e
                # fallback Telegram plain mostrano tag letterali). Il canale
                # decide se applicare formatting nel proprio adapter.
                progress.update_free(f"step {step_num} · {chosen_name}")
                # tool_call strutturato (per chat HTML breadcrumb live).
                # Path = tutti i tool degli step gia' completati + corrente.
                if hasattr(progress, "tool_call"):
                    path_so_far = [
                        s.chosen_tool for s in log.steps if s.chosen_tool
                    ] + [chosen_name]
                    # Previsione step rimanenti (euristica intent-based).
                    predicted_remaining = _predict_remaining_path(
                        intent=(route_info or {}).get("intent"),
                        current_tool=chosen_name,
                    )
                    progress.tool_call(
                        tool=chosen_name, step_num=step_num,
                        path_so_far=path_so_far,
                        args=raw_args if isinstance(raw_args, dict) else {},
                        predicted_remaining=predicted_remaining,
                    )
            except Exception as _e:  # silent swallow (auto-fixed)
                log.warning("silent exception in %s: %s", __name__, _e)
        if verbose:
            print(f"[step {step_num}] tool_call: {chosen_name}({raw_args})")

        # Reject placeholder malformati (graffa singola, pipe, ternario): no loop, errore subito.
        malformed = check_malformed_reference(raw_args)
        if malformed:
            obs = {"ok": False, "_malformed_ref": True, "error": malformed}
            step.result = obs
            step.error = "malformed_reference"
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": raw_args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
            consecutive_blocked += 1
            if consecutive_blocked >= LOOP_BREAK_THRESHOLD:
                log.final_kind = "loop_break"
                _last_err = (step.result.get("error") if isinstance(step.result, dict) else None) or step.error or "n/a"
                _hint = _loop_break_hint(_intent_object_from_route(route_info))
                log.final_message = msg("MSG_LOOP_BREAK", n=consecutive_blocked, hint=_hint)
                log.ts_end = time.time(); log.write(); return log
            continue

        # Reject inline data passing: liste di dict inline >= soglia devono passare via reference.
        inline_violation = check_inline_data(raw_args)
        if inline_violation:
            obs = {"ok": False, "_inline_rejected": True, "error": inline_violation}
            step.result = obs
            step.error = "inline_data_rejected"
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": raw_args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
            consecutive_blocked += 1
            if consecutive_blocked >= LOOP_BREAK_THRESHOLD:
                log.final_kind = "loop_break"
                _last_err = (step.result.get("error") if isinstance(step.result, dict) else None) or step.error or "n/a"
                _hint = _loop_break_hint(_intent_object_from_route(route_info))
                log.final_message = msg("MSG_LOOP_BREAK", n=consecutive_blocked, hint=_hint)
                log.ts_end = time.time(); log.write(); return log
            continue

        # Duplicate detection: stessa chiamata identica alla precedente del medesimo tool.
        # NON incrementa same_count (e' un blocco a monte): il modello deve solo formulare la
        # final_answer dai risultati precedenti.
        last_args_for_tool = None
        for _prev in reversed(log.steps):
            if _prev.chosen_tool == chosen_name:
                last_args_for_tool = _prev.raw_args
                break
        if last_args_for_tool is not None and last_args_for_tool == raw_args:
            # Auto-final-on-duplicate: se l'ultima call con questi args ha
            # avuto successo, il modello sta solo cercando rassicurazione.
            # Chiudi il turno con un final_answer derivato dall'ULTIMO step
            # productive del turno (qualsiasi tool, non solo il duplicato),
            # cosi' pipeline come read→describe→send chiudono dichiarando
            # send (azione utente-significativa) e non read (data fetch).
            # Skip step di pure data-piping (scratchpad_read, classify, filter)
            # se ci sono action verb piu' recenti.
            last_obs_for_dup = None
            for _prev in reversed(log.steps):
                if _prev.chosen_tool == chosen_name and _prev.raw_args == raw_args:
                    last_obs_for_dup = _prev.result
                    break
            if isinstance(last_obs_for_dup, dict) and last_obs_for_dup.get("ok"):
                # Cerca l'ultimo step productive del turno (preferendo action
                # verbs su producer): scorre dalla fine, prende il primo tool
                # con ok:true che NON sia un mere data-piping helper o un
                # narrator LLM (describe_entries). Logica estratta a livello
                # modulo per testabilita' (cf. _resolve_auto_final_from_steps).
                lp_tool, lp_obs = _resolve_auto_final_from_steps(log.steps)
                if lp_tool is None:
                    lp_tool = chosen_name
                    lp_obs = last_obs_for_dup if isinstance(last_obs_for_dup, dict) else {}
                step.error = "auto_final_on_duplicate"
                log.steps.append(step)
                ok_count, n_above_threshold = _extract_auto_final_count(lp_obs)
                # Quattro fonti per il messaggio finale auto, in ordine di
                # precedenza:
                # 0. `detail_md`: blocco multi-riga gia' renderizzato
                #    dall'executor (executor producer ricchi). Quando c'e',
                #    e' la fonte autorevole — niente boilerplate aggiuntivo.
                # 1. `summary`: 1-2 righe pronto-uso (executor cooperativi).
                # 2. `results`: lista di dict trasformativi (move/write/send).
                # 3. `entries`: lista di dict producer (find/get/list) con
                #    campi identificativi.
                explicit_detail_md = (
                    lp_obs.get("detail_md") if isinstance(lp_obs, dict) else None
                )
                explicit_summary = (
                    lp_obs.get("summary") if isinstance(lp_obs, dict) else None
                )
                explicit_hint = (
                    lp_obs.get("final_message_hint") if isinstance(lp_obs, dict) else None
                )
                explicit_message = (
                    lp_obs.get("message") if isinstance(lp_obs, dict) else None
                )
                detail = None
                if explicit_detail_md and isinstance(explicit_detail_md, str):
                    # Cap a 1500 caratteri per evitare messaggi troppo lunghi
                    # su Telegram. Un blocco serio ha tipicamente 200-800.
                    detail = explicit_detail_md.strip()[:1500]
                elif explicit_summary and isinstance(explicit_summary, str):
                    detail = explicit_summary.strip()[:400]
                elif explicit_hint and isinstance(explicit_hint, str):
                    detail = explicit_hint.strip()[:600]
                elif explicit_message and isinstance(explicit_message, str):
                    detail = explicit_message.strip()[:400]
                else:
                    results = (lp_obs.get("results") or []) if isinstance(lp_obs, dict) else []
                    entries_list = (lp_obs.get("entries") or []) if isinstance(lp_obs, dict) else []
                    summary_bits = []
                    for r in results[:5]:
                        if not isinstance(r, dict):
                            continue
                        if r.get("to") and r.get("subject"):
                            summary_bits.append(f"a {','.join(r['to']) if isinstance(r['to'], list) else r['to']} «{r['subject']}»")
                        elif r.get("path"):
                            summary_bits.append(r["path"])
                        elif r.get("dst"):
                            d = r["dst"]; folder = d.get("folder") if isinstance(d, dict) else d
                            summary_bits.append(f"→ {folder}")
                    if not summary_bits:
                        for e in entries_list[:3]:
                            if not isinstance(e, dict):
                                continue
                            for k in ("name", "subject", "path", "title", "url",
                                       "signature", "kind"):
                                v = e.get(k)
                                if v:
                                    summary_bits.append(str(v)[:80])
                                    break
                    detail = "; ".join(summary_bits) if summary_bits else None
                log.final_kind = "answer"
                # Quando il count e' ignoto E abbiamo un 'message' descrittivo
                # autorevole (verb-unique / azione singola), non aggiungere
                # rumore "(? elementi)" — usa direttamente il message.
                if ok_count is None and explicit_message and isinstance(explicit_message, str):
                    log.final_message = f"{lp_tool}: {explicit_message.strip()}"
                else:
                    count_str = _format_auto_final_count(ok_count, n_above_threshold)
                    log.final_message = msg(
                        "MSG_AUTO_FINAL_COMPLETED",
                        tool=lp_tool, count_str=count_str,
                        detail=(detail if detail else msg("MSG_AUTO_FINAL_NO_DETAIL")),
                    )
                log.ts_end = time.time(); log.write(); return log
            # Duplicate call ma ok=False al primo tentativo: l'errore e'
            # gia' definitivo (target_not_found, missing_credentials, etc.).
            # Riprovare e' anti-pattern §2.8 (no silent failure: il fail
            # autorevole va trasformato in final user-facing onesto).
            # Bug live 15/5/2026 turn "cancella task test_inesistente":
            # list_tasks ok → delete_tasks ok=False (non trovato) →
            # LLM riprova 3× delete_tasks identico → loop_break generico.
            # Fix: chiudi turno con error del primo step come final.
            if isinstance(last_obs_for_dup, dict) and not last_obs_for_dup.get("ok"):
                _err_msg = (last_obs_for_dup.get("error") or
                              last_obs_for_dup.get("message") or "")
                if isinstance(_err_msg, str) and _err_msg.strip():
                    step.error = "auto_final_on_duplicate_fail"
                    log.steps.append(step)
                    log.final_kind = "answer"
                    log.final_message = f"{chosen_name}: {_err_msg.strip()}"
                    log.ts_end = time.time(); log.write(); return log
            obs = {
                "ok": False,
                "_duplicate": True,
                "error": (
                    f"DUPLICATE_CALL: hai gia' chiamato '{chosen_name}' con questi stessi args "
                    "al passo precedente. Il risultato sara' identico. FORMULA LA FINAL_ANSWER "
                    "usando i risultati gia' ottenuti, non chiamare di nuovo questo tool."
                ),
            }
            step.result = obs
            step.error = "duplicate_call_blocked"
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": raw_args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
            consecutive_blocked += 1
            if consecutive_blocked >= LOOP_BREAK_THRESHOLD:
                log.final_kind = "loop_break"
                _last_err = (step.result.get("error") if isinstance(step.result, dict) else None) or step.error or "n/a"
                _hint = _loop_break_hint(_intent_object_from_route(route_info))
                log.final_message = msg("MSG_LOOP_BREAK", n=consecutive_blocked, hint=_hint)
                log.ts_end = time.time(); log.write(); return log
            continue

        # Vectorial violation guard (ADR 0130, 12/5/2026).
        # Bug live turn `8f8080c0`: find_events_empty x9 consecutivi su args
        # `time_windows` che variavano (DUPLICATE_CALL non scattava). §2.1
        # vettoriale: chi accetta args plurali (paths/urls/time_windows/...)
        # va chiamato UNA volta con N args, non N volte. Detection §7.9
        # introspettiva sul `args_schema.properties` (zero whitelist §7.3).
        # Posizione: post-DUPLICATE (args diff), pre-cap_same (intercetta
        # PRIMA di consumare budget) e pre-invoke (zero cost, no work).
        _executor_for_guard = catalog.get(chosen_name)
        if (
            _executor_for_guard is not None
            and log.steps
            and log.steps[-1].chosen_tool == chosen_name
            and isinstance(log.steps[-1].result, dict)
            and log.steps[-1].result.get("ok") is True
        ):
            _sig = _vectorial_schema_signature(_executor_for_guard.args_schema)
            if _executor_has_plural_args(chosen_name, _sig):
                obs = {
                    "ok": False,
                    "_anti_vectorial": True,
                    "error": (
                        f"VECTORIAL_VIOLATION: hai gia' chiamato '{chosen_name}' "
                        f"al passo precedente con esito ok=True. Questo executor "
                        f"accetta args plurali (§2.1 vettoriale): se hai bisogno "
                        f"di MULTIPLE finestre/path/id/url, passali TUTTI in UNA "
                        f"sola call come lista. NON chiamarlo di nuovo. Formula "
                        f"final_answer dai risultati gia' ottenuti, oppure procedi "
                        f"al next step della pipeline."
                    ),
                }
                step.result = obs
                step.error = "anti_vectorial_blocked"
                log.steps.append(step)
                history_for_refs.append({"step": step_num, "tool": chosen_name, "args": raw_args, "observation": obs})
                history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
                history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
                consecutive_blocked += 1
                if consecutive_blocked >= LOOP_BREAK_THRESHOLD:
                    log.final_kind = "loop_break"
                    _hint = _loop_break_hint(_intent_object_from_route(route_info))
                    log.final_message = msg("MSG_LOOP_BREAK", n=consecutive_blocked, hint=_hint)
                    log.ts_end = time.time(); log.write(); return log
                continue

        # Cap chiamate stesso tool: 10 default per qualsiasi tool. Soglia generosa
        # per permettere iterazioni legittime (universal helpers su args
        # diversi, producer su windows/account differenti). Loop reali
        # vengono comunque catturati prima da duplicate_call_blocked.
        # ADR 0130: cap_same per executor vettoriali abbassato a 2 (vs 10
        # default) via `_cap_same_for_executor`: chiamare un vettoriale piu'
        # di 2 volte e' anti-pattern §2.1 anche con args diversi.
        same_count[chosen_name] += 1
        _cap_same_effective = _cap_same_for_executor(_executor_for_guard, cap_same)
        if same_count[chosen_name] > _cap_same_effective:
            step.error = f"cap_same_executor superato per {chosen_name}"
            log.steps.append(step)
            log.final_kind = "cap_same_executor"
            log.final_message = f"(stop: '{chosen_name}' chiamato {_cap_same_effective} volte)"
            log.ts_end = time.time(); log.write(); return log

        # Cap_max_per_turn (8/5/2026 notte, CLAUDE.md §4.4 estesa).
        # Trigger live: turn 9-step thrashing «cerca organico scuola Roma»
        # con find_texts ×7. cap_same a 10 e duplicate_call non bastano:
        # gli args cambiavano leggermente ogni call (topic ridotto progress.).
        # Soglia: stesso tool non-action chiamato >= DEFAULT_CAP_MAX_PER_TURN
        # volte nel turno con args near-identical (Jaccard token-set > 0.7
        # sui campi semantici topic/query/pattern) → forza final_answer
        # con `_compose_final_message_from_obs(last_productive)`.
        # I verbi action sono protetti dalle guardie a monte (vaglio,
        # cyclic-call, duplicate); qui solo non-action verbs.
        if _is_non_action_tool(chosen_name):
            _cur_norm = _normalize_args_for_dup(raw_args)
            _near_count = 1  # questa chiamata
            for _prev in log.steps:
                if _prev.chosen_tool != chosen_name:
                    continue
                _prev_norm = _normalize_args_for_dup(_prev.raw_args)
                if _args_jaccard(_cur_norm, _prev_norm) >= 0.7:
                    _near_count += 1
            if _near_count >= DEFAULT_CAP_MAX_PER_TURN:
                lp_tool, lp_obs = _resolve_auto_final_from_steps(log.steps)
                if lp_tool is None:
                    lp_tool = chosen_name
                    lp_obs = {}
                step.error = "cap_max_per_turn"
                step.loop_break_total = "max_calls_per_turn"
                log.steps.append(step)
                final_msg_str, _, _ = _compose_final_message_from_obs(lp_tool, lp_obs)
                log.final_kind = "answer"
                log.final_message = final_msg_str
                log.ts_end = time.time(); log.write(); return log

        # Cyclic-call guard (3/5/2026, ADR informale «evita doppia
        # esecuzione inutile, sempre»). Pattern A → X → A con A in
        # `_DESTRUCTIVE_VERBS` indica che il PLANNER sta richiamando un
        # executor distruttivo dopo un'interruzione: artefatto di
        # ragionamento, non lavoro genuino. Blocchiamo qui, senza eseguire.
        try:
            from vocab import DESTRUCTIVE_VERBS as _DV
        except Exception:  # pragma: no cover
            _DV = frozenset({"write", "move", "delete", "send", "extract", "create"})
        _verb_of = chosen_name.split("_", 1)[0] if "_" in chosen_name else chosen_name
        if (
            _verb_of in _DV
            and len(log.steps) >= 2
            and log.steps[-2].chosen_tool == chosen_name
        ):
            obs = {
                "ok": False,
                "_cyclic": True,
                "error": (
                    f"CYCLIC_CALL: hai gia' chiamato '{chosen_name}' due passi "
                    f"fa, con un altro tool nel mezzo. Pattern A → X → A su "
                    f"verbo destructive ('{_verb_of}'): la seconda chiamata "
                    f"non aggiunge lavoro utile. Vai a final_answer ora."
                ),
            }
            step.result = obs
            step.error = "cyclic_call_blocked"
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": raw_args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
            consecutive_blocked += 1
            if consecutive_blocked >= LOOP_BREAK_THRESHOLD:
                log.final_kind = "loop_break"
                _hint = _loop_break_hint(_intent_object_from_route(route_info))
                log.final_message = msg(
                    "MSG_LOOP_BREAK", n=consecutive_blocked, hint=_hint,
                )
                log.ts_end = time.time(); log.write(); return log
            continue

        # Resolve from_step shortcut → arg consumer injection (refactor F1, 29/4;
        # Layer 4 consumer-arg match aggiunto 5/5/2026 per Bug A turn 23be1548:
        # find_urls produce entries=[{url,...}], read_urls_html consuma `urls`
        # (NON `entries`). Senza schema il fallback inietta sotto `entries` e il
        # validate_args fallisce. Con schema: estrae entries[*].url → urls=[...]).
        _consumer_executor = catalog.get(chosen_name)
        _consumer_schema = _consumer_executor.args_schema if _consumer_executor else None
        # 8/5/2026: pre-pass per espandere `from_step:N` annidati in liste
        # args (es. paths_filter: ["from_step:2"]). Bug live: il PLANNER
        # passa la stringa `from_step:2` letterale come elemento di lista,
        # senza questa espansione finisce a paths_filter literal → match
        # vuoto → 0 entries. Pre-pass idempotente.
        raw_args_pre, nfs_errors = _expand_nested_from_step(raw_args, history_for_refs)
        args_after_from_step, fs_errors = resolve_from_step(
            raw_args_pre, history_for_refs, consumer_schema=_consumer_schema,
        )
        fs_errors = list(nfs_errors) + list(fs_errors)
        if fs_errors:
            obs = {"ok": False, "error": "from_step: " + "; ".join(fs_errors)}
            step.result = obs
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": raw_args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
            consecutive_blocked += 1
            if consecutive_blocked >= LOOP_BREAK_THRESHOLD:
                log.final_kind = "loop_break"
                _last_err = (step.result.get("error") if isinstance(step.result, dict) else None) or step.error or "n/a"
                _hint = _loop_break_hint(_intent_object_from_route(route_info))
                log.final_message = msg("MSG_LOOP_BREAK", n=consecutive_blocked, hint=_hint)
                log.ts_end = time.time(); log.write(); return log
            if not is_multistep:
                log.final_kind = "error"; log.final_message = f"(from_step: {fs_errors})"
                log.ts_end = time.time(); log.write(); return log
            continue

        # Resolve references (sintassi {{stepN.field}}, retro-compat)
        args, ref_errors = resolve_references(args_after_from_step, history_for_refs)
        step.resolved_args = args
        if ref_errors:
            obs = {"ok": False, "error": "references: " + "; ".join(ref_errors)}
            step.result = obs
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": raw_args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
            if not is_multistep:
                log.final_kind = "error"; log.final_message = f"(errore reference: {ref_errors})"
                log.ts_end = time.time(); log.write(); return log
            continue

        # Caso speciale: request_location_from_user e' builtin UX (regola §2-quater).
        # Salva pending state, invoca channel adapter coi bottoni, termina turno
        # silenziosamente. Daemon rilancia il turno quando l'utente risponde.
        if chosen_name == "request_location_from_user":
            chat_id_for_prompt = getattr(progress, "chat_id", None) if progress else None
            req_meta = _location_request.request(
                turn_id=turn_id,
                actor=actor,
                channel=channel or "cli",
                original_query=user_query_for_run,
                goal=args.get("goal", "rispondere alla tua richiesta"),
                chat_id=chat_id_for_prompt,
            )
            obs = dict(req_meta)
            obs["awaiting"] = True
            obs["suppress_final"] = True
            step.result = obs
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": args, "observation": obs})
            log.pending_location = req_meta  # passato al daemon per render UI
            log.final_kind = "awaiting"
            log.final_message = ""
            log.ts_end = time.time()
            log.write()
            return log

        # Casi speciali builtin per scheduling ricorrente (1/5/2026 sera).
        # I 3 tool sono callable dal PLANNER per registrare/elencare/cancellare
        # task ricorrenti che il scheduler builtin esegue al fire automatico.
        if chosen_name in ("create_tasks", "list_tasks",
                             "delete_tasks", "read_tasks",
                             "set_tasks", "read_tasks_history"):
            _handler = {
                "create_tasks": handle_create_tasks,
                "list_tasks": handle_list_tasks,
                "delete_tasks": handle_delete_tasks,
                "read_tasks": handle_read_tasks,
                "set_tasks": handle_set_tasks,
                "read_tasks_history": handle_read_tasks_history,
            }[chosen_name]
            _cid = getattr(progress, "chat_id", None) if progress else None
            obs = _handler(args, actor=actor, channel=channel, chat_id=_cid)
            step.result = obs
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            obs_str = _trim_obs_for_history(obs)
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": obs_str})
            if not is_multistep:
                log.final_kind = "answer" if obs.get("ok") else "error"
                log.final_message = obs.get("message") or json.dumps(obs, ensure_ascii=False)
                log.ts_end = time.time(); log.write(); return log
            continue

        # Caso speciale: request_new_executor e' builtin (telos di non-rinuncia).
        # Lancia synt multistage sincrono (~150 s wall) e ritorna esito al LLM.
        if chosen_name == "request_new_executor":
            # ADR 0122: passa gli step gia' eseguiti del turno corrente
            # cosi' synth_request puo' calcolare il path_shape_hash e
            # arricchire la proposta con i campi path_eta_*/call_count.
            obs = handle_synth_request(args, user_query=user_query_for_run, progress=progress, verbose=verbose, current_steps=list(log.steps))
            step.result = obs
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            obs_str = _trim_obs_for_history(obs)
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": obs_str})

            # Reload catalog se synthesis ha installato un nuovo executor:
            # cosi' il LLM al passo successivo lo vede e puo' chiamarlo
            # direttamente per chiudere il task originale.
            if obs.get("installed") and obs.get("proposed_name"):
                try:
                    new_catalog = filter_for_visibility(load_catalog(), VISIBILITY_COMPOSER)
                    new_ex = new_catalog.executors.get(obs["proposed_name"])
                    if new_ex is not None:
                        # aggiungi il nuovo executor in coda ai candidates,
                        # cosi' base_tools viene rinfrescato al prossimo iter.
                        if new_ex.name not in {e.name for e in candidates}:
                            candidates.append(new_ex)
                        catalog = new_catalog
                        base_tools = render_tools_for_provider(candidates)
                        if verbose:
                            print(f"[catalog] reloaded, {obs['proposed_name']} now visible to planner")
                except Exception as ex:
                    if verbose:
                        print(f"[catalog] reload failed: {ex}")

            if not is_multistep:
                log.final_kind = "answer" if obs.get("ok") else "error"
                log.final_message = obs.get("message") or json.dumps(obs, ensure_ascii=False)
                log.ts_end = time.time(); log.write(); return log
            continue

        # Caso speciale: describe_entries e' builtin LLM-augmented.
        # Loop+ragionamento dentro l'executor, niente subprocess.
        if chosen_name == "describe_entries":
            # ADR 0111 (7/5/2026): Level 2 — inietta `health_context` se
            # lo step sorgente (raw_args.from_step) ha un campo `health`
            # non vuoto. Senza questo, describe_entries vede solo le
            # entries (processi) e dichiarerebbe "non disponibile" sui
            # dati salute, contraddicendo il blocco che il runtime
            # prependera' al final_message.
            try:
                _fs = raw_args.get("from_step") if isinstance(raw_args, dict) else None
                if isinstance(_fs, str) and _fs.isdigit():
                    _fs = int(_fs)
                if (isinstance(_fs, int)
                        and 1 <= _fs <= len(history_for_refs)):
                    _src_obs = history_for_refs[_fs - 1].get("observation", {})
                    _h = _src_obs.get("health") if isinstance(_src_obs, dict) else None
                    if isinstance(_h, dict) and _h and "health_context" not in args:
                        args = dict(args)
                        args["health_context"] = _h
                    # 10/5/2026 fix UX deterministico: se la sorgente e'
                    # find_urls (anche attraverso filter/sort/group
                    # helpers in mezzo) con entries (URL list metadata),
                    # SKIP describe_entries — produrrebbe sintesi educata
                    # senza link. Sostituisce con auto-final: il runtime
                    # appende la lista cliccabile via
                    # `_append_search_results_if_any`. Il PLANNER puo'
                    # ignorare il rule Z.sette ma il runtime no.
                    #
                    # Walk-back attraverso helpers (filter_entries,
                    # sort_entries, group_entries, classify_entries):
                    # se from_step punta a uno di questi, risali fino al
                    # vero source (find_urls).
                    _HELPER_TOOLS = {
                        "filter_entries", "sort_entries", "group_entries",
                        "classify_entries",
                    }
                    _walk = _fs
                    _walked = 0
                    _src_tool = ""
                    while _walked < 5 and 1 <= _walk <= len(history_for_refs):
                        _entry_step = history_for_refs[_walk - 1]
                        _entry_tool = _entry_step.get("tool", "")
                        if _entry_tool in _HELPER_TOOLS:
                            _entry_args = _entry_step.get("args", {}) or {}
                            _next_fs = _entry_args.get("from_step")
                            if isinstance(_next_fs, str) and _next_fs.isdigit():
                                _next_fs = int(_next_fs)
                            if not isinstance(_next_fs, int):
                                break
                            _walk = _next_fs
                            _walked += 1
                            continue
                        _src_tool = _entry_tool
                        _src_obs = _entry_step.get("observation", {})
                        break
                    if (_src_tool == "find_urls"
                            and isinstance(_src_obs, dict)
                            and _src_obs.get("ok")
                            and _src_obs.get("entries")):
                        _entries = _src_obs.get("entries") or []
                        _docs = _src_obs.get("discovered_documents") or []
                        _n_ranked = sum(
                            1 for _e in _entries
                            if isinstance(_e, dict)
                            and isinstance(_e.get("score"), (int, float))
                            and _e["score"] > 0
                        )
                        # Phantom step: registra il NO-OP per audit
                        step.result = {
                            "ok": True,
                            "skipped": True,
                            "reason": "describe_on_find_urls_intercepted",
                            "from_step": _fs,
                            "n_entries": len(_entries),
                            "n_ranked": _n_ranked,
                            "n_docs": len(_docs),
                        }
                        step.error = "describe_skipped_for_search_results"
                        log.steps.append(step)
                        history_for_refs.append({
                            "step": step_num, "tool": chosen_name,
                            "args": args, "observation": step.result,
                        })
                        # Final answer breve, deterministico. La lista
                        # cliccabile la appende write() via
                        # _append_search_results_if_any.
                        log.final_kind = "answer"
                        try:
                            from messages import get as _msg_local
                            log.final_message = _msg_local(
                                "MSG_SEARCH_INTRO",
                                n=len(_entries), query=user_query_for_run or "",
                            )
                        except Exception:
                            log.final_message = (
                                f"Trovati {len(_entries)} risultati per "
                                f"«{(user_query_for_run or '')[:80]}»."
                            )
                        log.ts_end = time.time(); log.write(); return log
            except (KeyError, IndexError, TypeError):
                pass
            obs = handle_describe_entries(args, verbose=verbose)
            step.result = obs
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            obs_str = _trim_obs_for_history(obs)
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": obs_str})
            if not is_multistep:
                log.final_kind = "answer" if obs.get("ok") else "error"
                log.final_message = obs.get("summary") or obs.get("error") or json.dumps(obs, ensure_ascii=False)
                log.ts_end = time.time(); log.write(); return log
            continue

        # Caso speciale: classify_entries e' builtin LLM-augmented.
        # Arricchisce ogni entry con un campo `<dimension>` etichettato; non
        # partiziona — la partizione si fa con filter_entries downstream.
        if chosen_name == "classify_entries":
            obs = handle_classify_entries(args, verbose=verbose)
            step.result = obs
            # Offload a scratchpad per non leakare le entries arricchite.
            obs_for_history = obs
            obs_str = json.dumps(obs, ensure_ascii=False)
            # Offload a scratchpad SOLO per liste grandi: sotto la soglia
            # il modello vede le entries inline nell'observation, evitando
            # di vedere solo l'handle (con il rischio di "fabbricare" output).
            # Soglia 20: tipica top-K query (top 5/10) sta sotto, niente
            # offload, modello vede i veri dati. Liste lunghe vanno in scratchpad.
            _ent = obs.get("entries")
            has_structured_list = isinstance(_ent, list) and len(_ent) > 20
            if obs.get("ok") and (has_structured_list or len(obs_str) > scratchpad_threshold):
                obs_for_history = sp.put(turn_id, step_num, chosen_name, obs)
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            obs_str_h = _trim_obs_for_history(obs_for_history)
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": obs_str_h})
            if not is_multistep:
                log.final_kind = "answer" if obs.get("ok") else "error"
                if obs.get("ok"):
                    counts = obs.get("counts") or {}
                    log.final_message = f"Classificato: {counts}"
                else:
                    log.final_message = obs.get("error") or json.dumps(obs, ensure_ascii=False)
                log.ts_end = time.time(); log.write(); return log
            continue

        # Caso speciale: admin (ADR 0088) e' verb-unique builtin VISIBILE al
        # PLANNER. Va instradato via VERB_UNIQUE_REGISTRY (vaglio always-on),
        # non via subprocess executor. Il sudoer resta invisibile.
        if chosen_name == "admin":
            from loader import invoke_verb_unique
            try:
                obs = invoke_verb_unique(
                    "admin", caller="agent_runtime",
                    intent=args.get("intent", ""),
                    command_proposed=args.get("command_proposed", ""),
                    credentials_domain=args.get("credentials_domain"),
                    actor_consent_token=args.get("actor_consent_token"),
                    actor=actor,
                )
            except (PermissionError, KeyError, RuntimeError) as e:
                obs = {"ok": False, "error": f"admin invoke failed: {e}"}
            step.result = obs
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            obs_str = _trim_obs_for_history(obs)
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": obs_str})

            # ADR 0091 (5/5/2026): admin ha bisogno di credenziali NON
            # ancora salvate. Auto-orchestriamo `get_inputs(fmt="auto")`:
            # il runtime invoca direttamente l'orchestratore (no PLANNER
            # round-trip) e salva un `dialog_pending` con `on_complete`
            # callback che salvera' le creds e ri-invochera' admin con i
            # suoi args originali. Il turno chiude con una carta UI
            # (form HTTP standalone oppure prima domanda dialogue).
            if isinstance(obs, dict) and obs.get("decision") == "needs_inputs":
                from orchestration import orchestrate_needs_inputs
                sender_for_state = (
                    f"{channel}:{actor}" if channel else (actor or "host")
                )
                gi_result = orchestrate_needs_inputs(
                    obs,
                    sender_id=sender_for_state,
                    actor=actor or "host",
                    channel=channel or None,
                )
                if not gi_result.get("ok"):
                    # Fallback: se l'orchestrazione fallisce (es. payload
                    # malformato, storage non scrivibile), usa la stringa
                    # plain text legacy come fallback diagnostico per non
                    # lasciare l'utente senza risposta.
                    log.final_kind = "error"
                    log.final_message = (
                        "Servono credenziali ma l'orchestrazione "
                        "get_inputs e' fallita: "
                        + (gi_result.get("error") or "errore sconosciuto")
                        + ". Ritenta con `metnos-cli credentials add ...`."
                    )
                    log.ts_end = time.time(); log.write(); return log

                # Successo: chiudi il turno con la carta UX e propaga
                # expandable_caps di tipo `get_inputs_response` cosi' il
                # daemon riconosce il prossimo messaggio utente come
                # risposta al dialogo (e il submit HTTP lo trova via
                # /agent/dialog/<id>/form).
                log.final_kind = "answer"
                log.final_message = (
                    gi_result.get("final_message_hint")
                    or "Servono alcuni input per continuare."
                )
                log.ts_end = time.time()
                log.write()
                # Forza expandable_caps DOPO write() (write() popola la
                # lista da self._collect_expandable_caps che pero' non vede
                # questo dialogo perche' obs viene da admin, non da get_inputs).
                caps = gi_result.get("expandable_caps") or []
                if caps:
                    log.expandable_caps = caps
                return log

            # Se admin ha emesso una carta vaglio: registriamo un'expandable_caps
            # speciale (kind="admin_approval") che il channel daemon riconosce
            # e consuma diversamente dal cap-expand standard. Termina il turno
            # subito con la summary come final_answer.
            if isinstance(obs, dict) and obs.get("approval_required"):
                approval_proposal = {
                    "kind": "admin_approval",
                    "step_num": step_num,
                    "executor": "admin",
                    "cap_field": "actor_consent_token",
                    "cap_suggested": obs.get("consent_token", ""),
                    "args_original": dict(raw_args or {}),
                    "args_suggested": dict(raw_args or {},
                                          actor_consent_token=obs.get("consent_token", "")),
                    # carta vaglio per UI (Telegram bottoni o altro)
                    "approval_card": obs.get("approval_card") or {},
                    "signature": obs.get("signature", ""),
                }
                # _collect_expandable_caps non riconosce questa shape; la
                # iniettiamo manualmente settando expandable_caps qui (dopo
                # write() la lista normale viene sovrascritta — la
                # impostiamo dopo write()).
                log.final_kind = "answer"
                log.final_message = obs.get("summary") or "Operazione richiede approvazione."
                # Forza expandable_caps direttamente, write() lo preserva
                # solo se non sovrascrive: aggiungiamo il flag prima di write
                # via override locale.
                log._pending_admin_approval = approval_proposal  # type: ignore[attr-defined]
                log.ts_end = time.time()
                log.write()
                # Dopo write(), inietta la proposta come unica entry di
                # expandable_caps cosi' il daemon la trova.
                log.expandable_caps = [approval_proposal]
                return log

            # Decisione finale (execute_silent o reject): chiudi turno.
            if not is_multistep:
                log.final_kind = "answer" if obs.get("ok") else "error"
                log.final_message = obs.get("summary") or json.dumps(obs, ensure_ascii=False)
                log.ts_end = time.time(); log.write(); return log
            continue

        # Caso speciale: scratchpad_read e' builtin (no manifest, no subprocess)
        if chosen_name == "scratchpad_read":
            sp_id = args.get("scratchpad_id")
            if not sp_id:
                obs = {"ok": False, "error": "scratchpad_id mancante"}
            else:
                obs = sp.read(sp_id, mode=args.get("mode", "head"),
                              n=args.get("n", 2000),
                              start=args.get("start", 0),
                              end=args.get("end"))
            step.result = obs
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            obs_str = _trim_obs_for_history(obs)
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": obs_str})
            if not is_multistep:
                log.final_kind = "answer"; log.final_message = format_simple_answer(chosen_name, obs)
                log.ts_end = time.time(); log.write(); return log
            continue

        executor = catalog.get(chosen_name)
        if executor is None:
            obs = {"ok": False, "error": f"executor inesistente: {chosen_name}"}
            # Hook proto-mnest: se il LLM ha chiesto un tool inesistente con piping
            # da uno step precedente, registra un proto-mnest src=step_referenziato,
            # dst=nome_desiderato. Senza piping non e' un "passing".
            refs = extract_step_refs(raw_args)
            last_mnest_id = None
            for ref_step in refs:
                if 1 <= ref_step <= len(history_for_refs):
                    src_tool_name = history_for_refs[ref_step - 1]["tool"]
                    src_executor = catalog.get(src_tool_name)
                    if src_executor is None:
                        continue  # il src stesso era proto/scratchpad: skip
                    sig = build_desired_signature(chosen_name, raw_args, user_query=user_query_for_run)
                    try:
                        last_mnest_id = mnestoma.record_passing(
                            src_executor.name, src_executor.version,
                            chosen_name, dst_version=None, dst_exists=False,
                            desired_signature=sig, turn_id=turn_id,
                        )
                    except Exception as ex:
                        if verbose:
                            print(f"[mnest] proto record failed: {ex}")
            # Tentativo synt-on-the-fly: solo compose (router=None disabilita generate
            # costoso). Se compose trova una catena di executor firmati che copre il
            # proto-mnest, suggerisce al LLM di riprovare invocando il primo hop.
            if last_mnest_id is not None:
                synt_hint = _try_synt_compose(mnestoma, chosen_name, last_mnest_id, verbose=verbose)
                if synt_hint:
                    obs["synt"] = synt_hint
            step.result = obs
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
            if not is_multistep:
                log.final_kind = "error"; log.final_message = f"(errore: tool '{chosen_name}' inesistente)"
                log.ts_end = time.time(); log.write(); return log
            continue

        # Guard: duplicate read detection (read_files/write_files/get_urls su stesso path/url)
        # Evita loop dove il LLM ri-legge lo stesso file con args leggermente diversi
        # invece di formulare la final_answer.
        identifier = None
        if chosen_name in ("read_files", "write_files"):
            identifier = args.get("path")
        elif chosen_name == "get_urls":
            identifier = args.get("url")
        if identifier:
            for prev_step, prev_tool, prev_id in read_calls_seen:
                if prev_tool == chosen_name and prev_id == identifier:
                    obs = {
                        "ok": False,
                        "error": "duplicate_call",
                        "message": f"Hai gia' chiamato {chosen_name} su '{identifier}' al passo {prev_step}. Usa il content di quella observation per formulare la final_answer all'utente. Non rifare la lettura.",
                    }
                    step.result = obs
                    step.error = "duplicate read intercepted by runtime"
                    log.steps.append(step)
                    history_for_refs.append({"step": step_num, "tool": chosen_name, "args": args, "observation": obs})
                    history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
                    history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
                    if not is_multistep:
                        log.final_kind = "error"; log.final_message = f"(rifiuto: {obs['message']})"
                        log.ts_end = time.time(); log.write(); return log
                    # In multistep, continua: il LLM al prossimo step dovrebbe formulare
                    break
            else:
                read_calls_seen.append((step_num, chosen_name, identifier))
        if identifier and read_calls_seen and read_calls_seen[-1] != (step_num, chosen_name, identifier) and step.error == "duplicate read intercepted by runtime":
            continue

        # P4 (12/5/2026) — Availability check gate per calendar write tools.
        # Bug live turn b1d9c236: query «fissa appuntamento ... SE C'È POSTO»
        # → planner ha chiamato set_events direttamente, bypassando il
        # workflow (check_availability) di calendar.j2 (1.get_now → 2.read_events
        # → 3.set_events se vuoto). Roberto aveva impegno tutto mercoledi:
        # l'evento e' stato creato lo stesso, overlap.
        # Defense in depth §7.9: il runtime rifiuta set_events quando:
        #   (1) chosen_name e' calendar write tool,
        #   (2) user_query contiene un availability marker,
        #   (3) NESSUN step precedente e' read_events ok.
        # L'obs di rifiuto e' lasciata in history: il planner LLM al prossimo
        # turno vede l'errore con hint e chiama read_events.
        if (chosen_name in _calendar_write_tools()
                and _query_requires_availability_check(user_query_for_run)
                and not _has_prior_read_events_ok(log.steps)):
            obs = {
                "ok": False,
                "_availability_check_required": True,
                "error": (
                    f"AVAILABILITY_CHECK_REQUIRED: la query contiene un "
                    f"marker di disponibilita' (es. «se c'e' posto», «if "
                    f"available»). DEVI chiamare read_events(time_window=...) "
                    f"PRIMA di '{chosen_name}' per verificare lo slot, poi "
                    f"set_events solo se entries vuota. Workflow numerato in "
                    f"section calendar (check_availability)."
                ),
            }
            step.result = obs
            step.error = "availability_check_required"
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": raw_args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
            consecutive_blocked += 1
            if consecutive_blocked >= LOOP_BREAK_THRESHOLD:
                log.final_kind = "loop_break"
                _hint = _loop_break_hint(_intent_object_from_route(route_info))
                log.final_message = msg("MSG_LOOP_BREAK", n=consecutive_blocked, hint=_hint)
                log.ts_end = time.time(); log.write(); return log
            continue

        # P5 (12/5/2026) — Propose-intent gate per calendar write tools.
        # Bug live turn a0b96f6f: query «proponi 3 orari per appuntamento
        # la prossima settimana mattina» → planner ha chiamato set_events
        # con whole-week blob (lun→sab 8-12). Atteso: read_events + final
        # testuale con N slot. NESSUN evento creato.
        # Defense in depth §7.9: il runtime rifiuta calendar-write tools
        # quando:
        #   (1) chosen_name e' calendar write tool (set/create _events),
        #   (2) user_query e' propose-intent (regex semantica universale).
        # NB: a differenza di P4, NON serve "prior read_events ok": una
        # propose-intent query e' SEMPRE risolta con final_answer testuale,
        # mai con set_events. Il read_events e' raccomandato per dati ma
        # opzionale (planner puo' rispondere generico se serve). Cio' che
        # blocchiamo qui e' la creazione effettiva di eventi.
        # ADR 0129 fire-after-propose exception: la guardia propose_intent
        # va sospesa quando siamo in continuation post-dialog (utente ha
        # gia' fatto la scelta esplicita → la "proposta" e' diventata
        # "fire"). Detect: history contiene `get_inputs` con observation
        # `decision="completed"` o `_resumed=True`. §7.9 deterministico.
        _dialog_completed = any(
            isinstance(h, dict)
            and h.get("tool") == "get_inputs"
            and isinstance(h.get("observation"), dict)
            and (h["observation"].get("decision") == "completed"
                 or h["observation"].get("_resumed"))
            for h in history_for_refs
        )
        if (chosen_name in _calendar_write_tools()
                and _query_is_propose_intent(user_query_for_run)
                and not _dialog_completed):
            obs = {
                "ok": False,
                "_propose_intent_detected": True,
                "error": (
                    f"PROPOSE_INTENT_NO_WRITE: la query e' una richiesta di "
                    f"proposta/suggerimento (es. «proponi 3 orari», «suggest "
                    f"options», «what are free slots»). DEVI rispondere "
                    f"testualmente con N slot/alternative computate da "
                    f"read_events. NON DEVI invocare '{chosen_name}' o "
                    f"altri tool calendar-write: la query non chiede di "
                    f"CREARE un evento, ma di SUGGERIRE alternative. "
                    f"Workflow: get_now → read_events(time_window=...) → "
                    f"final_answer con N proposte. Section calendar (propose_intent)."
                ),
            }
            step.result = obs
            step.error = "propose_intent_no_write"
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": raw_args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
            consecutive_blocked += 1
            if consecutive_blocked >= LOOP_BREAK_THRESHOLD:
                log.final_kind = "loop_break"
                _hint = _loop_break_hint(_intent_object_from_route(route_info))
                log.final_message = msg("MSG_LOOP_BREAK", n=consecutive_blocked, hint=_hint)
                log.ts_end = time.time(); log.write(); return log
            continue

        # Validazione, sandbox, vaglio
        validation = validate_args(args, executor.args_schema)
        step.validation_failures = validation
        if validation:
            obs = {"ok": False, "error": f"validation failed: {validation}"}
        else:
            scope_violation = check_hints(args, executor.capabilities)
            step.scope_violation = scope_violation
            if scope_violation:
                obs = {"ok": False, "error": scope_violation}
            else:
                # Telemetria fine (ADR 0080): vaglio_ms misurato al confine.
                _t_vaglio0 = time.perf_counter()
                verdict = judge(user_query_for_run, chosen_name, args, {"mode": chosen_mode, "step": step_num})
                step.vaglio_ms = int((time.perf_counter() - _t_vaglio0) * 1000)
                step.vaglio_approved = verdict.approved
                if not verdict.approved:
                    obs = {"ok": False, "error": f"vaglio rifiuta: {verdict.reason}"}
                else:
                    if verbose:
                        print(f"[step {step_num}] exec {chosen_name}({args})")
                    op_id = None
                    if executor.revertible:
                        op_id = uuid.uuid4().hex
                        try:
                            UndoLog().append_pending(op_id, turn_id, executor.name, args, plan={}, actor=actor, channel=channel)
                        except Exception as ex:
                            if verbose:
                                print(f"[undo] append_pending failed: {ex}")
                            op_id = None
                    # Telemetria fine (ADR 0080): exec_ms = pura execution
                    # (subprocess + I/O), esclusa la chiamata LLM del PLANNER.
                    _t_exec0 = time.perf_counter()
                    try:
                        obs = invoke_executor(
                            executor, args, turn_id=turn_id,
                            timeout_s=getattr(executor, "timeout_s", 30),
                            actor=actor, channel=channel,
                        )
                    except subprocess.TimeoutExpired:
                        obs = {"ok": False, "error": "executor timeout"}
                    step.exec_ms = int((time.perf_counter() - _t_exec0) * 1000)
                    if op_id and obs.get("ok"):
                        try:
                            UndoLog().append_done(op_id, obs)
                        except Exception as ex:
                            if verbose:
                                print(f"[undo] append_done failed: {ex}")

        step.result = obs
        log.steps.append(step)
        history_for_refs.append({"step": step_num, "tool": chosen_name, "args": args, "observation": obs})

        # Terminal-failure short-circuit (15/5/2026): executor che ritorna
        # `_terminal: True` + `final_message_hint` indica un fail con
        # messaggio user-facing autoritativo (es. index_missing). NIENTE
        # senso a iterare: chiudi il turno con quel hint come final_message.
        # Bug live 15/5/2026 turn e362785f: find_images_indices su path
        # non indicizzato → 3× retry → loop_break generico. Con _terminal
        # il turno chiude al primo fail con messaggio chiaro.
        if isinstance(obs, dict) and obs.get("_terminal") \
                and obs.get("final_message_hint"):
            log.final_kind = "answer"
            log.final_message = str(obs.get("final_message_hint", ""))
            log.ts_end = time.time(); log.write(); return log

        # Generic needs_inputs orchestration: qualsiasi executor (set_persons
        # face picker, delete_persons batch ambiguous, future tools) che
        # ritorna `decision="needs_inputs"` deve far chiudere il turno
        # con la carta UI persistita via `dialog_pending`. Senza questo
        # branch il PLANNER continua e inventa una final_answer al posto
        # del face picker (10/5/2026: bug live turn 9e066cb9 set_persons).
        # Il branch admin-specifico sopra (chosen_name=="admin") resta
        # come fast-path ma non e' piu' l'unico path.
        if (isinstance(obs, dict)
                and obs.get("decision") == "needs_inputs"
                and chosen_name not in ("admin", "get_inputs")):
            from orchestration import orchestrate_needs_inputs
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            obs_str_h = _trim_obs_for_history(obs)
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": obs_str_h})
            sender_for_state = (
                f"{channel}:{actor}" if channel else (actor or "host")
            )
            gi_result = orchestrate_needs_inputs(
                obs,
                sender_id=sender_for_state,
                actor=actor or "host",
                channel=channel or None,
            )
            if not gi_result.get("ok"):
                log.final_kind = "error"
                log.final_message = (
                    f"Orchestrazione get_inputs fallita per {chosen_name}: "
                    + (gi_result.get("error") or "errore sconosciuto")
                )
                log.ts_end = time.time(); log.write(); return log
            log.final_kind = "answer"
            log.final_message = (
                gi_result.get("final_message_hint")
                or obs.get("final_message_hint")
                or "Servono alcuni input per continuare."
            )
            log.ts_end = time.time()
            log.write()
            # Forza expandable_caps DOPO write(): _collect_expandable_caps
            # non riconosce decision=needs_inputs su tool generici (come
            # set_persons), quindi la lista naturale e' vuota. Copiamo
            # quella prodotta da orchestrate_needs_inputs perche' il
            # daemon (Telegram) e _apply_cap_pending (HTTP) leggono da
            # qui per dispatchare la prossima risposta utente al dialog.
            caps = gi_result.get("expandable_caps") or []
            for cap in caps:
                if cap.get("kind") == "get_inputs_response":
                    cap.setdefault("sender_for_state", sender_for_state)
            if caps:
                log.expandable_caps = caps
            return log

        # ADR 0111 (7/5/2026): Level 3 — auto-final deterministico dopo
        # `get_processes` ok con campo `health` non vuoto. La query e' di
        # tipo "stato sistema/server/carico/ram"; il blocco "Stato server"
        # gia' formattato sara' prepended da `_prepend_health_block_if_any`
        # in TurnLog.write(). Saltare il PLANNER step 2+ evita che il
        # modello chiami describe_entries (che vede solo entries=processi
        # e dichiarerebbe "non disponibile" su salute, contraddicendo il
        # blocco prepended) o ri-chieda lo stato (loop). §7.9 deterministico
        # > LLM. Skip se l'utente ha richiesto un'azione (kill/ferma/manda/
        # scrivi/...) — heuristic su intent.verb e keyword imperative.
        if (chosen_name == "get_processes"
                and isinstance(obs, dict)
                and obs.get("ok")
                and isinstance(obs.get("health"), dict)
                and obs.get("health")
                and is_multistep):
            _q_low = (user_query_for_run or "").lower()
            _has_imperative = any(k in _q_low for k in _HEALTH_IMPERATIVE_KEYWORDS)
            _is_action_intent = _intent_verb in _ACTION_VERBS_PRED
            if not _has_imperative and not _is_action_intent:
                # history_for_llm append per la step record completa.
                history_for_llm.append({
                    "role": "assistant",
                    "tool_calls": [{
                        "id": tc.call_id, "type": "function",
                        "function": {"name": chosen_name, "arguments": raw_args},
                    }],
                })
                obs_str_h = _trim_obs_for_history(obs)
                history_for_llm.append({
                    "role": "tool", "tool_call_id": tc.call_id,
                    "name": chosen_name, "content": obs_str_h,
                })
                log.final_kind = "answer"
                # final_message minimo: il blocco "Stato server" verra'
                # prepended automaticamente da _prepend_health_block_if_any
                # in write(). Lasciamo stringa vuota → il blocco diventa
                # tutto il messaggio (no boilerplate aggiuntivo, vedi
                # ADR 0095 output deterministico).
                log.final_message = ""
                step.error = "auto_final_health"
                log.ts_end = time.time()
                log.write()
                return log

        # Approval card generica (CLAUDE.md 2.11 fase 2): qualsiasi executor
        # regolare puo' chiedere conferma esplicita ritornando
        # `approval_required:true` + `final_message_hint` + `expandable_caps`.
        # Esempio: find_images_indices lazy build chiede di indicizzare 30k foto.
        # Senza questo break il PLANNER vede observation con ok=true, legge
        # `args_suggested` nel cap, e RILANCIA lo stesso tool con
        # `force_build=true` di sua iniziativa — bypassando l'utente. Stop qui:
        # write() preserva expandable_caps, daemon salva pending state, prossimo
        # turno l'utente conferma o annulla.
        if (isinstance(obs, dict)
                and obs.get("ok")
                and obs.get("approval_required")
                and obs.get("final_message_hint")
                and chosen_name not in ("admin", "get_inputs")):
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            obs_str_h = _trim_obs_for_history(obs)
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": obs_str_h})
            log.final_kind = "answer"
            log.final_message = obs.get("final_message_hint")
            log.ts_end = time.time()
            log.write()
            return log

        # Caso speciale get_inputs (ADR 0090): chiude il turno immediatamente
        # quando il dialogo richiede input (evita che il PLANNER prosegua o
        # che il modello inventi una final_answer al posto del prompt UX
        # dell'executor). final_message = `final_message_hint` dell'observation.
        # `expandable_caps` con kind="get_inputs_response" propagato a TurnLog
        # (via _collect_expandable_caps + injecting sender_for_state).
        if (chosen_name == "get_inputs"
                and isinstance(obs, dict)
                and obs.get("ok")
                and obs.get("decision") == "input_required"):
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            obs_str_h = _trim_obs_for_history(obs)
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": obs_str_h})

            # MID-PIPELINE get_inputs (12/5/2026, bug residuo propose+notify):
            # quando la query e' propose+notify e/o ha continuation di
            # notifica/azione, il PLANNER deve riprendere DOPO il pick utente
            # con send_messages/create_events/... Patchamo il dialog state per
            # iniettare `on_complete=resume_planner_with_dialog_values` con
            # snapshot dello scratchpad. process_completion_callback prendera'
            # la palla al submit dell'utente. Determinismo §7.9.
            try:
                if _should_resume_planner_after_dialog(
                        user_query_for_run, route_info, history_for_refs):
                    sender_for_state = (
                        f"{channel}:{actor}" if channel else (actor or "host")
                    )
                    dialog_id = obs.get("dialog_id")
                    if dialog_id:
                        import dialog_pending as _dp
                        _state = _dp.load_pending(sender_for_state, dialog_id)
                        if _state is not None and not _state.get("on_complete"):
                            _dialog_var = None
                            _dlg = _state.get("dialog") or []
                            if _dlg and isinstance(_dlg[0], dict):
                                _dialog_var = _dlg[0].get("var")
                            # Estrai implicit_actions dall'intent del turno
                            # corrente per orchestrazione deterministica post-
                            # dialog (ADR 0129). Generalizza l'orchestratore: il
                            # callback non deve indovinare quali verbi mutating
                            # eseguire, li legge dall'intent gia' classificato.
                            _intent_dict = (route_info.get("intent")
                                            if isinstance(route_info, dict)
                                            else None) or {}
                            _implicit_actions = (
                                _intent_dict.get("implicit_actions")
                                if isinstance(_intent_dict, dict) else None
                            ) or []
                            _state["on_complete"] = {
                                "type": "resume_planner_with_dialog_values",
                                "original_query": user_query_for_run,
                                "prior_steps": _snapshot_scratchpad(
                                    history_for_refs),
                                "dialog_step_num": step_num,
                                "dialog_var_name": _dialog_var or "values",
                                "conversation_id": conversation_id or "",
                                "implicit_actions": list(_implicit_actions),
                            }
                            _dp.save_pending(sender_for_state, dialog_id, _state)
                            if verbose:
                                print(f"[resume_after_dialog] on_complete "
                                      f"iniettato per dialog_id={dialog_id} "
                                      f"prior_steps={len(history_for_refs)}")
            except Exception as _ex:
                log.warning("inject on_complete (resume_planner) fallito: %s", _ex)

            log.final_kind = "answer"
            log.final_message = (
                obs.get("final_message_hint")
                or obs.get("summary")
                or "Servono alcuni input per continuare."
            )
            log.ts_end = time.time()
            log.write()
            # Dopo write() (che ha gia' propagato expandable_caps via
            # _collect_expandable_caps), arricchisci la prima entry con
            # `sender_for_state` (channel:actor) cosi' il daemon trova il
            # dialog state al posto giusto. write() preserva i campi extra.
            if log.expandable_caps:
                sender_for_state = (
                    f"{channel}:{actor}" if channel else (actor or "host")
                )
                for cap in log.expandable_caps:
                    if cap.get("kind") == "get_inputs_response":
                        cap.setdefault("sender_for_state", sender_for_state)
                        # actor_consent_token-style: se admin-credentials migration
                        # passa qui, il chiamante ha gia' settato `credentials_domain`.
            return log

        # Hook executor_aging: ogni invocazione effettiva di un executor
        # del pool aggiorna last_used_at + total_calls. Idempotente,
        # best-effort. Il decay `apply_executor_ager` notturno legge da
        # qui per decidere quale executor retirare.
        try:
            from executor_aging import touch as _exec_touch
            _exec_touch(chosen_name, ok=bool(obs.get("ok")))
        except Exception:
            pass  # silent: tracking history non blocca mai un turno

        # Hook adaptive re-rank (4/5/2026): se lo step e' andato a buon
        # fine, ricalcola il pool di candidati per il prossimo step
        # basandosi su keywords estratte dall'observation. Add-only:
        # i tool gia' selezionati restano nel pool (cap 2 × k_max).
        # Indipendente dal ranker sottostante (token-based oggi, embedding
        # domani). Costo a regime ~5 ms su 300 executor (misurato).
        if isinstance(obs, dict) and obs.get("ok"):
            # Telemetria fine (ADR 0080): rerank_ms.
            _t_rerank0 = time.perf_counter()
            try:
                from adaptive_rerank import re_rank_for_step
                _candidates_before = list(candidates)
                candidates_post, rr_info = re_rank_for_step(
                    original_query=user_query_for_run,
                    catalog=catalog,
                    current_candidates=_candidates_before,
                    latest_observation=obs,
                    k_min=k_min,
                    k_max=k_max,
                    history_tools=[h["tool"] for h in history_for_refs],
                )
                if rr_info.get("applied"):
                    candidates = candidates_post
                    if verbose:
                        print(f"[rerank] step {step_num}: +{len(rr_info.get('added') or [])} "
                              f"({','.join(rr_info.get('added') or [])})")
                    # Ricostruzione base_tools: i nuovi tool diventano
                    # disponibili al prossimo turno della LLM.
                    base_tools = render_tools_for_provider(candidates)
            except Exception as _e:
                # Re-rank non blocca mai il turno: e' un'ottimizzazione.
                if verbose:
                    print(f"[rerank] step {step_num}: skipped ({type(_e).__name__})")
            step.rerank_ms = int((time.perf_counter() - _t_rerank0) * 1000)

        # Hook mnest: se lo step e' andato a buon fine E i raw_args contenevano
        # riferimenti {{stepM.field}}, registra un mnest src=step_M_tool, dst=current.
        if obs.get("ok"):
            refs = extract_step_refs(raw_args)
            for ref_step in refs:
                if 1 <= ref_step <= len(history_for_refs) - 1:  # -1 perche' history include lo step corrente
                    src_tool_name = history_for_refs[ref_step - 1]["tool"]
                    src_executor = catalog.get(src_tool_name)
                    if src_executor is None:
                        continue  # src non era un executor reale (proto/scratchpad)
                    try:
                        mnestoma.record_passing(
                            src_executor.name, src_executor.version,
                            executor.name, executor.version,
                            dst_exists=True, turn_id=turn_id,
                        )
                    except Exception as ex:
                        if verbose:
                            print(f"[mnest] active record failed: {ex}")

        # Offload a scratchpad: SEMPRE per output strutturati (liste in candidate_keys)
        # o per content > soglia. Il pianificatore vede solo handle (scratchpad_id +
        # count + schema + ref_hint), mai i dati raw — i dati raw passano al prossimo
        # executor solo via reference {{stepN.field}}, risolta dal runtime.
        obs_for_history = obs
        obs_str = json.dumps(obs, ensure_ascii=False)
        # Offload solo per liste grandi (>20). Sotto soglia, il modello
        # vede entries inline (no handle): evita fabricazione su top-K piccoli.
        has_structured_list = any(
            isinstance(obs.get(k), list) and len(obs.get(k)) > 20
            for k in ("entries", "matches", "items", "results", "files", "paths")
        )
        if obs.get("ok") and (has_structured_list or len(obs_str) > scratchpad_threshold):
            obs_for_history = sp.put(turn_id, step_num, chosen_name, obs)
            if verbose:
                print(f"[step {step_num}] obs -> scratchpad id={obs_for_history['scratchpad_id']} (size={len(obs_str)} list={has_structured_list})")

        # Reset consecutive_blocked counter: questo step e' produttivo (executor eseguito)
        if obs.get("ok"):
            consecutive_blocked = 0
        else:
            consecutive_blocked += 1
            if consecutive_blocked >= LOOP_BREAK_THRESHOLD:
                log.final_kind = "loop_break"
                _last_err = (step.result.get("error") if isinstance(step.result, dict) else None) or step.error or "n/a"
                _hint = _loop_break_hint(_intent_object_from_route(route_info))
                log.final_message = msg("MSG_LOOP_BREAK", n=consecutive_blocked, hint=_hint)
                log.ts_end = time.time(); log.write(); return log

        # Aggiungi tool_call e tool_result alla history LLM
        history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
        obs_str_h = _trim_obs_for_history(obs_for_history)
        history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": obs_str_h})

        # In single-shot, finiamo qui col simple-format
        if not is_multistep:
            log.final_kind = "answer"; log.final_message = format_simple_answer(chosen_name, obs)
            log.ts_end = time.time(); log.write(); return log

        # ─── Deterministic seed-step injection per pipeline propose+notify ────
        # ADR 0129 extended (14/5/2026 sera): dopo `find_events_empty` ok con
        # entries in pipeline propose+notify, il PLANNER medium (Gemma 4 26B)
        # va in thinking loop su query con dettagli aggiuntivi («con Silvia»,
        # «di una ora la mattina», ecc.) — esaurisce max_tokens senza emettere
        # `get_inputs`. Bug live turn cc8d3980 (166s, step 2 vuoto).
        # Fix: emetto deterministicamente lo step `get_inputs(choice)` come
        # next-step, bypassando il PLANNER per quel singolo step. Pattern
        # analogo a `try_seed_step` di ADR 0099. §7.9.
        if (chosen_name == "find_events_empty"
                and isinstance(obs, dict) and obs.get("ok")
                and (obs.get("entries") or [])
                and _should_resume_planner_after_dialog(
                    user_query_for_run, route_info, history_for_refs)):
            try:
                _seed_gi = _inject_get_inputs_choice_for_propose(
                    entries=obs.get("entries") or [],
                    object_canonical=(_intent_object_from_route(route_info)
                                       or "events"),
                )
            except Exception as _ex:
                log.warning("seed get_inputs injection failed: %s", _ex)
                _seed_gi = None
            if _seed_gi is not None and verbose:
                print(f"[seed-step] inject get_inputs(choice) "
                      f"dopo find_events_empty step={step_num}")
            if _seed_gi is not None:
                # Eseguo direttamente get_inputs (autonomous step+1).
                _gi_args = _seed_gi
                _gi_step_num = step_num + 1
                _gi_step = StepLog(step_num=_gi_step_num)
                _gi_step.chosen_tool = "get_inputs"
                _gi_step.raw_args = dict(_gi_args)
                _gi_step.resolved_args = dict(_gi_args)
                _gi_step.vaglio_approved = True
                _gi_step.error = "seed_step_after_find_empty"
                try:
                    from loader import load_catalog as _lc
                    _cat = _lc(verify=True, include_synth=True)
                    _gi_ex = _cat.executors.get("get_inputs")
                    if _gi_ex is None:
                        raise RuntimeError("get_inputs executor non in catalog")
                    _gi_obs = invoke_executor(
                        _gi_ex, _gi_args,
                        timeout_s=getattr(_gi_ex, "timeout_s", 30),
                        actor=actor, channel=channel or "",
                    )
                except Exception as _ex:
                    _gi_step.error = f"seed_step_failed: {type(_ex).__name__}: {_ex}"
                    log.steps.append(_gi_step)
                    # Lascio il loop continuare: il PLANNER tentera' step+1 reale
                else:
                    _gi_step.result = _gi_obs
                    log.steps.append(_gi_step)
                    history_for_refs.append({
                        "step": _gi_step_num, "tool": "get_inputs",
                        "args": _gi_args, "observation": _gi_obs,
                    })
                    # Chiudi il turno se input_required (uguale al ramo
                    # `decision == "input_required"` piu' sotto, ma evita
                    # round trip PLANNER + duplicazione codice).
                    if (isinstance(_gi_obs, dict) and _gi_obs.get("ok")
                            and _gi_obs.get("decision") == "input_required"):
                        log.final_kind = "answer"
                        log.final_message = (
                            _gi_obs.get("final_message_hint")
                            or "Servono alcuni input per continuare."
                        )
                        # Inject on_complete per resume planner (stessa
                        # logica del blocco standard a riga ~4978).
                        try:
                            if _should_resume_planner_after_dialog(
                                    user_query_for_run, route_info,
                                    history_for_refs):
                                sender_for_state = (
                                    f"{channel}:{actor}" if channel
                                    else (actor or "host")
                                )
                                dialog_id = _gi_obs.get("dialog_id")
                                if dialog_id:
                                    import dialog_pending as _dp
                                    _state = _dp.load_pending(
                                        sender_for_state, dialog_id)
                                    if (_state is not None
                                            and not _state.get("on_complete")):
                                        _dialog_var = None
                                        _dlg = _state.get("dialog") or []
                                        if _dlg and isinstance(_dlg[0], dict):
                                            _dialog_var = _dlg[0].get("var")
                                        _intent_dict = (
                                            route_info.get("intent")
                                            if isinstance(route_info, dict)
                                            else None) or {}
                                        _implicit_actions = (
                                            _intent_dict.get(
                                                "implicit_actions")
                                            if isinstance(_intent_dict, dict)
                                            else None) or []
                                        _state["on_complete"] = {
                                            "type": "resume_planner_with_dialog_values",
                                            "original_query": user_query_for_run,
                                            "prior_steps":
                                                _snapshot_scratchpad(
                                                    history_for_refs),
                                            "dialog_step_num": _gi_step_num,
                                            "dialog_var_name":
                                                _dialog_var or "values",
                                            "conversation_id":
                                                conversation_id or "",
                                            "implicit_actions":
                                                list(_implicit_actions),
                                        }
                                        _dp.save_pending(
                                            sender_for_state, dialog_id, _state)
                        except Exception as _ex:
                            log.warning(
                                "inject on_complete (seed-step) fallito: %s",
                                _ex)
                        log.ts_end = time.time()
                        log.write()
                        if log.expandable_caps:
                            sender_for_state = (
                                f"{channel}:{actor}" if channel
                                else (actor or "host")
                            )
                            for cap in log.expandable_caps:
                                if cap.get("kind") == "get_inputs_response":
                                    cap.setdefault("sender_for_state",
                                                    sender_for_state)
                        return log

        # Auto-final dopo executor transformative idempotente che ha gia'
        # creato/modificato una entita' remota: il planner LLM puo' oscillare
        # con args leggermente diversi (es. start 9-10 vs 9-11) bypassando
        # DUPLICATE_CALL, creando N entita' duplicate. Bug live turn c627784c
        # (11/5/2026 sera): set_events x5 → 5 eventi calendario.
        # Pattern: chosen_name in lista whitelist + obs.ok + _undo presente
        # (= operazione registrata revertibile) → final_answer immediato con
        # link/id. §7.9 deterministico, niente LLM aggiuntivo.
        #
        # P3 (12/5/2026): suppress auto-final se la user_query contiene una
        # congiunzione di continuation («e mandami email», «and notify me»).
        # Bug live turn b1d9c236: «fissa appuntamento ... E MANDAMI EMAIL»
        # chiudeva il turno dopo set_events ok, perdendo la send_messages.
        # `_query_has_continuation` lookup regex deterministico.
        # `_query_has_continuation` inibisce auto-final per evitare di
        # chiudere il turno a meta' pipeline (turn b1d9c236, 12/5: «fissa
        # appuntamento E MANDAMI EMAIL» chiudeva dopo create_events). Ma se
        # TUTTI i verbi della query sono gia' coperti dagli step ok=True
        # (incluso il corrente), la pipeline e' completa e bisogna chiudere
        # (turn 7f7381d2, 14/5: PLANNER ri-eseguiva find_events_empty dopo
        # send_messages ok perche' continuation era ancora True). §7.9.
        _executed_verbs_now = [
            (s.chosen_tool or "") for s in log.steps
            if isinstance(s.result, dict) and s.result.get("ok") is True
        ] + [chosen_name]
        _pipeline_complete = _all_query_verbs_satisfied(
            user_query_for_run, _executed_verbs_now,
        )
        # Reversibili (set_events/create_events/...): chiude su _undo+ids
        # presenti (= operazione registrata revertibile, htmlLink/id nel detail).
        # Irreversibili (send_messages): nessun `_undo`; chiude SE pipeline
        # complete (tutti i verbi query satisfied). Senza pipeline_complete
        # la guardia continuation resta intatta (memoria turn b1d9c236).
        _has_undo = (isinstance(obs.get("_undo"), dict)
                     and obs.get("_undo", {}).get("ids"))
        _final_safe = (
            (not _query_has_continuation(user_query_for_run) and _has_undo)
            or _pipeline_complete
        )
        if (chosen_name in _AUTO_FINAL_TRANSFORMATIVE
                and isinstance(obs, dict)
                and obs.get("ok") is True
                and _final_safe):
            r0 = (obs.get("results") or [{}])[0]
            _detail = (r0.get("htmlLink") or r0.get("id")
                       or _format_send_messages_detail(obs))
            log.final_kind = "answer"
            log.final_message = msg(
                "MSG_TRANSFORMATIVE_AUTO_FINAL",
                executor=chosen_name,
                count=obs.get("n_created") or obs.get("ok_count") or 1,
                detail=_detail,
            )
            log.ts_end = time.time(); log.write(); return log

        # Auto-final dopo undo successful: il modello tipicamente non rispetta
        # la regola 2-bis del prompt (chiamare undo una sola volta per turno);
        # il runtime forza la chiusura cosi' l'utente vede subito l'esito.
        if (chosen_name == "undo_last_turn"
                and isinstance(obs, dict)
                and obs.get("ok")
                and (obs.get("undone_count") or 0) >= 1):
            details = obs.get("details") or []
            d0 = details[0] if details else {}
            target_executor = d0.get("executor", "azione")
            target_count = d0.get("ok_count", obs.get("undone_count", 0))
            log.final_kind = "answer"
            log.final_message = msg(
                "MSG_UNDO_AUTO_FINAL",
                executor=target_executor, count=target_count,
            )
            log.ts_end = time.time(); log.write(); return log

        # Auto-final dopo undo FALLITO (ok:false, undone_count=0): bug live
        # turn d7417418 — query «annulla ultima azione» quando non c'e' nulla
        # da annullare faceva 3x undo → loop_break con hint generico. Il
        # planner non rispettava la regola 2-bis (undo ok:false → final).
        # Forziamo deterministicamente. §7.9.
        if (chosen_name == "undo_last_turn"
                and isinstance(obs, dict)
                and obs.get("ok") is False
                and (obs.get("undone_count") or 0) == 0):
            log.final_kind = "answer"
            log.final_message = msg("MSG_UNDO_NOTHING_TO_UNDO")
            log.ts_end = time.time(); log.write(); return log

    # Cap steps superato
    log.final_kind = "cap_steps"
    log.final_message = msg("MSG_CAP_STEPS", cap=cap_steps)
    log.ts_end = time.time(); log.write(); return log


def format_simple_answer(executor_name, result):
    if not result.get("ok"):
        return f"Errore in {executor_name}: {result.get('error', 'sconosciuto')}"
    content = result.get("content", "")
    meta = result.get("metadata", {})
    if executor_name == "get_now":
        return f"Sono le {content} ({meta.get('timezone','UTC')})."
    if executor_name == "read_files":
        preview = (content or "")[:300]
        return f"{meta.get('path','?')}:\n{preview}{'…' if len(content) > 300 else ''}"
    if executor_name == "write_files":
        return f"Scritti {meta.get('bytes_written',0)} byte in {meta.get('path','?')}."
    if executor_name == "get_urls":
        preview = (content or "")[:300]
        return f"GET {meta.get('url','?')} -> {meta.get('status','?')}, {meta.get('bytes',0)} byte:\n{preview}{'…' if len(content) > 300 else ''}"
    return json.dumps(result, ensure_ascii=False)[:300]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument("--mode", default="local", choices=["local", "online", "hybrid"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--think", action="store_true", help="Abilita thinking del LLM (qwen3, deepseek)")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--cap-steps", type=int, default=DEFAULT_CAP_STEPS)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    query = " ".join(args.query)
    log = run_turn(query, mode=args.mode, model=args.model, k=args.k,
                   cap_steps=args.cap_steps, think=args.think, verbose=args.verbose)
    print(f"\n>>> {log.final_message}\n")
    if args.verbose:
        total_in = sum(s.llm_in_tokens for s in log.steps)
        total_out = sum(s.llm_out_tokens for s in log.steps)
        total_lat = sum(s.llm_latency_ms for s in log.steps)
        print(f"--- log: {len(log.steps)} step, llm {total_in}->{total_out} toks in {total_lat}ms, turn {(log.ts_end - log.ts_start)*1000:.0f}ms, kind={log.final_kind}")
