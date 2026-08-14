"""prompt_loader.py — carica i prompt LLM da `runtime/prompts/<lang>/`.

ADR 0092 (5/5/2026): prompt come dati persistiti su filesystem, non come
stringhe inline nei moduli Python. Engine: MiniJinja (drop-in Jinja2 syntax,
Rust core, errori più chiari).

Bench section format PoC (13/5/2026): env switch
`METNOS_SECTION_FORMAT={prose,yaml_raw,json_raw}` permette di rendering
ciascuna sezione YAML in tre modalita': prose §6 (baseline Asse B),
yaml_raw passthrough, json_raw struttura. Default `prose`. La env entra
nella cache key di `compose()` cosi' che cambi di formato non collidano
fra loro.

API:
    get(role, lang, **vars)         # render runtime/prompts/<lang>/<role>.j2
    compose(role, lang, sections=,  # render 3-layer planner: _core +
            **vars)                 #   sections/<name>.j2 in ordine + _footer
                                     #   (Fase C3, 11/5/2026). Per role!=
                                     #   "planner" alias di `get`.
    validate_invariant()             # boot check: tutte le sub-dir lingua hanno
                                     # stesso set di file di it/ (canonical)
    load_lang_state(lang)            # carica `prompts/<lang>/.lang_state.json`
    save_lang_state(lang, s)         # salva `prompts/<lang>/.lang_state.json`

Lang esplicito al call site (5/5/2026): ogni caller dichiara la lingua;
default fornito da `config.DEFAULT_LANG`. Niente env globale singleton:
ogni Environment è indicizzato per `lang` in `_envs[lang]` (cache lazy).

Determinismo (the design guide §7.9): zero LLM nel loader. Niente DB, niente
network. Filesystem-as-source-of-truth, cache MiniJinja built-in per Env.

Pattern latest-wins per l'allineamento multilingua dei .j2 (estensione
ADR 0092, 6/5/2026): file siblings `.lang_state.json` per ogni dir di
lingua. Per ogni `role` traccia `{version_hash, source_lang, source_hash}`.
Il daemon `i18n_translator.align_prompts()` fa hash-content compare
(non mtime) per detect edit, sceglie edit-source via mtime + tie-break
alfabetico, ritraduce le altre lingue divergenti. Niente IO extra nel
critical path `get(role, lang, **vars)`.

Planner 3-layer (Fase C, 11/5/2026): il `planner` non e' piu' un singolo
file ma una struttura a 3 strati in `runtime/prompts/<lang>/planner/`:
    _core.j2          → identita', vocabolario, regole, esempi generali
    sections/*.j2     → vincoli di dominio (mail, calendar, web, photos,
                        system, admin_shell) iniettati solo se rilevanti
                        per l'intent extractor (selettore in `vocab.py`).
    _footer.j2        → variabili dinamiche per-utente/turno
                        (project_paths, users_known, ...).
La composizione e' deterministica: `compose("planner", lang, sections=[...],
**vars)` concatena `_core` + sezioni (ordinate alfabeticamente per stabilita'
cache) + `_footer`. `sections=None` o `()` = includi TUTTE le sezioni (degrade
graceful per intent.confidence bassa o object unknown). Cache lru_cache.
"""
import functools
import json
import os
import re
from pathlib import Path
from typing import Optional

import minijinja
import yaml

_BASE = Path(__file__).parent / "prompts"
# Cache per-lang: ogni lingua ha la sua Environment isolata (no pollution).
_envs: dict[str, minijinja.Environment] = {}

# Frontmatter fields (the design guide §6.1) da rimuovere dai render raw passthrough:
# sono metadati di styling/ownership, non parte del contenuto utile al PLANNER.
_SECTION_FRONTMATTER_KEYS = frozenset({
    "role", "tier", "lang", "style", "version", "owner", "updated", "sha_prev",
})


def _section_format() -> str:
    """Ritorna il formato di rendering corrente per le sezioni YAML (PoC
    bench 13/5/2026). Valori validi: `prose` (default), `yaml_raw`, `json_raw`.
    Determinismo §7.9: env-driven, valutato AL momento del render (entra in
    cache key di `_compose_planner_cached`)."""
    fmt = os.environ.get("METNOS_SECTION_FORMAT", "prose").strip().lower()
    if fmt not in ("prose", "yaml_raw", "json_raw"):
        # Silenziosamente fallback: il bench drop-in puo' avere typo, non
        # vogliamo crash del PLANNER a runtime. Logged se diverso da `prose`.
        return "prose"
    return fmt


def _synt_format() -> str:
    """Ritorna il formato di rendering corrente per i prompt synt (PoC
    bench 13/5/2026 sera, estensione Asse B a synt). Valori validi: `prose`
    (default, j2 j2 attuale), `yaml_raw`, `json_raw`.

    Distinto da `_section_format()` (che riguarda solo le planner sections):
    le 5 stage + 12 addendum verbo di synt seguono la loro env autonoma
    cosi' il bench puo' iterare su synt mantenendo il planner stabile.

    Determinismo §7.9: env-driven, valutato AL momento del render."""
    fmt = os.environ.get("METNOS_SYNT_FORMAT", "prose").strip().lower()
    if fmt not in ("prose", "yaml_raw", "json_raw"):
        return "prose"
    return fmt


# Lista esatta dei `role` che corrispondono ai prompt synt — usato per il
# dispatch yaml/j2 in `get()`. Determinismo §7.9: lookup tabellare.
_SYNT_ROLES = frozenset({
    "synt_naming", "synt_signature", "synt_tests",
    "synt_description", "synt_code",
    "synt_code_addendum_create", "synt_code_addendum_delete",
    "synt_code_addendum_describe", "synt_code_addendum_extract",
    "synt_code_addendum_filter", "synt_code_addendum_find",
    "synt_code_addendum_get", "synt_code_addendum_list",
    "synt_code_addendum_move", "synt_code_addendum_read",
    "synt_code_addendum_send", "synt_code_addendum_write",
})


# Lingua di ripiego (§K, 15/6/2026): finché una stringa non è ancora tradotta
# nella lingua target, il sistema risponde in INGLESE (allineato a
# `i18n.FALLBACK_CHAIN`). Mai crash, mai IT silenzioso su lingua non-IT.
_FALLBACK_LANG = "en"

# Nome della lingua iniettato come `{{ lang_name }}` (placeholder dinamico: il
# modello scrive il final_message in QUESTA lingua). Allineato a
# `i18n_translator._LANG_NAMES` per le lingue oltre it/en (§K): senza la voce,
# una lingua nuova mostrerebbe il CODICE ("fr") invece del nome.
_LANG_NAMES = {"it": "italiano", "en": "English", "fr": "français",
               "de": "Deutsch", "es": "español"}


def _resolve_prompt_source(root: Path, en_root: Path, name: str) -> Optional[str]:
    """Risolve il testo di un template `name` (es. "intent_extractor.j2" o
    "planner/_core.j2") con catena DETERMINISTICA §7.9/§K:

      1. live  `<lang>/<name>`                       — file approvato/canonico
      2. cand. `<lang>/_pending/<name>.candidate`    — output del daemon, usato
         SUBITO (l'approvazione manuale è opt-in, non un gate; §K 15/6)
      3. EN    `en/<name>`                            — ripiego nel frattempo
      4. EN c. `en/_pending/<name>.candidate`

    Ogni candidato sostituisce-in-vivo il live mancante: una lingua nuova (fr)
    è subito operativa appena il daemon ha scritto i candidati, e finché non li
    ha scritti ricade su EN — invece di far crashare il planner. Per IT/EN i
    live esistono sempre → vince il passo 1 → comportamento invariato (i
    candidati `_pending` di IT/EN restano ignorati). No `..` escape."""
    for base, nm in ((root, name),
                     (root, f"_pending/{name}.candidate"),
                     (en_root, name),
                     (en_root, f"_pending/{name}.candidate")):
        if not base.is_dir():
            continue
        p = (base / nm).resolve()
        try:
            p.relative_to(base.resolve())
        except ValueError:
            continue  # tentativo di uscire dalla dir base
        if p.is_file():
            return p.read_text(encoding="utf-8")
    return None


def _env_for(lang: str) -> minijinja.Environment:
    """Ritorna (creando lazy + cachando) la `minijinja.Environment` per `lang`.
    Il loader applica la catena live→candidato→EN (`_resolve_prompt_source`):
    una lingua senza i suoi `.j2` non fa crashare il planner, ricade su EN.
    Solleva RuntimeError solo se NEMMENO `prompts/<lang>/` né `prompts/en/`
    esistono (misconfig reale)."""
    env = _envs.get(lang)
    if env is not None:
        return env
    root = _BASE / lang
    en_root = _BASE / _FALLBACK_LANG
    if not root.is_dir() and not en_root.is_dir():
        raise RuntimeError(
            f"prompt_loader: né {root} né il ripiego {en_root} esistono. "
            f"Verifica lang ({lang!r}) e la struttura runtime/prompts/."
        )

    def _loader(name: str):
        # Supporta nomi con slash (es. "planner/_core.j2"). Catena §K.
        return _resolve_prompt_source(root, en_root, name)

    env = minijinja.Environment(loader=_loader, keep_trailing_newline=True)
    _envs[lang] = env
    return env


def _lang_has(name: str, lang: str) -> bool:
    """True se la LINGUA `lang` (non il ripiego) possiede `name` come file live
    o candidato `_pending`. Usato per decidere la lingua EFFETTIVA del planner
    (§K): se manca, l'intero planner ricade su EN nel frattempo, invece di
    mescolare _core in una lingua e sezioni nell'altra."""
    return ((_BASE / lang / name).is_file()
            or (_BASE / lang / "_pending" / f"{name}.candidate").is_file())


def _effective_planner_lang(lang: str) -> str:
    """Lingua con cui rendere il planner 3-strati: `lang` se ne possiede il
    `_core` (live o candidato), altrimenti EN (ripiego §K). Normalizzata PRIMA
    di enumerare le sezioni, così sezioni e _core vengono dalla stessa lingua."""
    return lang if _lang_has("planner/_core.j2", lang) else _FALLBACK_LANG


def _section_yaml_path(lang: str, sec: str) -> Optional[Path]:
    """Decide come rendere la sezione `sec`: ritorna il path di un `.yaml`
    (asse B → `_render_yaml_section`) oppure None (asse A → il caller rende il
    `.j2` via env, che risolve live→candidato→EN da sé).

    Priorità (§K): la LINGUA vince sul ripiego, lo YAML vince sul J2 a parità di
    lingua —
      1. yaml della lingua → quel path
      2. j2 della lingua (live o candidato) → None (resta nella lingua via env)
      3. yaml di EN → quel path (ripiego nel frattempo)
      4. altrimenti → None (il `.j2` di EN via env, o errore se nemmeno quello)

    Senza il passo 2 lo YAML di EN scavalcherebbe il J2 PROPRIO della lingua; senza
    il passo 3 una lingua parziale tenterebbe un `.j2` inesistente per le sezioni
    YAML-only di EN."""
    lang_yaml = _BASE / lang / "planner" / "sections" / f"{sec}.yaml"
    if lang_yaml.is_file():
        return lang_yaml
    if _lang_has(f"planner/sections/{sec}.j2", lang):
        return None  # la lingua ha il .j2 (live/candidato): l'env lo risolve
    en_yaml = _BASE / _FALLBACK_LANG / "planner" / "sections" / f"{sec}.yaml"
    if en_yaml.is_file():
        return en_yaml
    return None


def _interp_placeholders(obj, vars: dict):
    """Sostituisce i placeholder `{{ var }}` nelle stringhe del dict YAML
    parsato (ricorsivo). Niente Jinja: lookup table-driven `{{ key }}` →
    `str(vars[key])`. Determinismo §7.9.

    Gestisce: dict (ricorsivo sui valori), list (ricorsivo), str (sostituzione),
    altri tipi (passthrough). Placeholder unknown = lasciato letterale (no fail).
    """
    if isinstance(obj, dict):
        return {k: _interp_placeholders(v, vars) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interp_placeholders(v, vars) for v in obj]
    if isinstance(obj, str):
        s = obj
        # Match `{{ key }}` (con spazi opzionali). Pattern conservativo:
        # placeholder come {{var}} o {{ var }} o {{  var  }}.
        def _sub(m):
            key = m.group(1).strip()
            return str(vars.get(key, m.group(0)))
        return re.sub(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", _sub, s)
    return obj


def _render_synt_yaml(yaml_path: Path, lang: str, fmt: str, **vars) -> str:
    """Render di un prompt synt in formato YAML (Asse B extension synt, 13/5/2026
    sera). Pipeline:
      1. Parse YAML del file (con `{{ var }}` placeholder INTATTI).
      2. Interpola i placeholder ricorsivamente sui valori string (table-driven).
      3. Format dispatcher:
           - `yaml_raw`: YAML grezzo (frontmatter stripped).
           - `json_raw`: JSON serializzato (frontmatter stripped).
           - `prose`   : NON usato (caller usa j2 directly).

    Vantaggio rispetto a Jinja-then-parse: il parser YAML non rompe quando
    i valori interpolati contengono colon/newline/etc (es. blocchi vocab che
    sono multiline narrative). Determinismo §7.9: zero LLM, sostituzione
    tabellare deterministica."""
    raw = yaml_path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(
            f"_render_synt_yaml: {yaml_path} root non e' un dict YAML"
        )
    # Interpola placeholder sui valori.
    data = _interp_placeholders(data, dict(vars))
    clean = {k: v for k, v in data.items() if k not in _SECTION_FRONTMATTER_KEYS}
    if fmt == "yaml_raw":
        return yaml.safe_dump(
            clean,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
    if fmt == "json_raw":
        return json.dumps(clean, ensure_ascii=False, indent=2) + "\n"
    # fmt == "prose" e' gestito dal caller via fallback j2: fallback yaml_raw.
    return yaml.safe_dump(
        clean,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )


def _default_vars() -> dict:
    """Variabili Jinja iniettate automaticamente in ogni render.

    Include `install_root` (path della install Metnos, rename-resilient
    §7.11) — usato dai prompt synt_code_addendum_send e simili per
    suggerire location di storage al LLM senza hardcodare `/opt/metnos`
    nel template (B.4 fix 19/5/2026 v4).
    """
    # current_year / current_date: §7.11-per-le-date. Gli ESEMPI nei prompt non
    # devono hardcodare anni letterali (es. time_window="2024") → invecchiano e
    # biasano l'LLM verso anni stantii. Usa {{ current_year }} / {{ current_date }}
    # negli esempi: il valore è sempre quello reale al render.
    from datetime import datetime as _dt
    _now = _dt.now()
    out = {"current_year": _now.year,
           "current_date": _now.strftime("%Y-%m-%d")}
    try:
        from config import PATH_ROOT
        out["install_root"] = str(PATH_ROOT)
    except Exception:
        out["install_root"] = "/opt/metnos"
    return out


def get(role: str, lang: str, **vars) -> str:
    """Render `runtime/prompts/<lang>/<role>.j2` con `**vars` come variabili
    Jinja2. `lang` è obbligatorio: ogni caller dichiara la lingua corrente
    (default `config.DEFAULT_LANG`). Cache built-in per Environment.
    Solleva TemplateError se il template non esiste o ha errori di sintassi.

    Asse B extension synt (13/5/2026 sera): se `role` e' uno dei
    `_SYNT_ROLES` e l'env `METNOS_SYNT_FORMAT` e' `yaml_raw`/`json_raw`,
    rendiamo via YAML envelope (se `<role>.yaml` esiste). Per `prose` o
    YAML mancante, fallback al `<role>.j2` originale (byte-equivalence
    garantita)."""
    # Inject install_root (B.4 19/5/2026 v4): caller può override passando
    # esplicitamente `install_root=` in vars.
    # Inject `lang` + `lang_name` (9/6/2026): i template possono imporre la
    # lingua di un campo (es. final_message del proposer) via `{{ lang_name }}`,
    # un PLACEHOLDER — così la parola della lingua non viene mai mal-tradotta
    # dal translator automatico (resta dinamica, = lingua corrente).
    _lang_names = _LANG_NAMES
    merged_vars = {**_default_vars(),
                   "lang": lang, "lang_name": _lang_names.get(lang, lang),
                   **vars}
    if role in _SYNT_ROLES:
        fmt = _synt_format()
        if fmt in ("yaml_raw", "json_raw"):
            yaml_path = _BASE / lang / f"{role}.yaml"
            if yaml_path.is_file():
                return _render_synt_yaml(yaml_path, lang, fmt, **merged_vars)
            # YAML missing: fall through to j2 (no silent failure on bench drop-in)
    return _env_for(lang).render_template(f"{role}.j2", **merged_vars)


# Layout static-first (ottimizzazione A prompt-cache, 10/6/2026) -----------
#
# Il marker-commento `{# STATIC-END ... #}` separa nel sorgente .j2 la parte
# INVARIANTE per-query (testa: istruzioni/regole/schema) dalla parte
# VARIABILE (coda: intent/pool/excluded/query). `get_split` renderizza le due
# parti separatamente: il caller manda la testa come messaggio SYSTEM
# (byte-identico fra le query) e la coda come messaggio USER. llama-server
# crea da sé un checkpoint al confine system→user (`n_before_user`,
# llama.cpp server-context) e ogni query riprocessa SOLO la coda
# (misura 10/6: prompt_n 5521→1277, latenza proposer 8.15s→2.26s).
# Il prefisso resiste anche al furto dello slot (host prompt-cache 8 GiB
# salva/ripristina stato+checkpoint) → niente priming né id_slot pinning.
# Guard deterministico: `prompts_lint._check_l6_static_first` (nessuna
# interpolazione non-costante prima del marker).
_STATIC_END_RE = re.compile(r"\{#-?\s*STATIC-END\b.*?#\}\n?", re.DOTALL)


def get_split(role: str, lang: str, **vars) -> tuple[str, str]:
    """Render di `prompts/<lang>/<role>.j2` in DUE parti al marker
    `{# STATIC-END ... #}` (layout static_first).

    Ritorna `(testa_statica, coda_variabile)`:
      - testa: invariante per-query (il guard L6 vieta `{{ var }}`
        non-costanti prima del marker) → messaggio SYSTEM.
      - coda: contenuto per-query → messaggio USER.

    Template SENZA marker → `(render_completo, "")`: il caller degrada al
    layout legacy (system=tutto, user=query). Stesse vars iniettate di
    `get()` (install_root, lang, lang_name, current_*)."""
    # Catena §K live→candidato→EN (come il loader): lingua senza il file ricade
    # su EN nel frattempo, niente crash.
    source = _resolve_prompt_source(_BASE / lang, _BASE / _FALLBACK_LANG,
                                    f"{role}.j2")
    if source is None:
        raise RuntimeError(
            f"prompt_loader.get_split: {role}.j2 assente in {lang!r} e nel "
            f"ripiego {_FALLBACK_LANG!r}")
    m = _STATIC_END_RE.search(source)
    if m is None:
        return get(role, lang, **vars), ""
    _lang_names = _LANG_NAMES
    merged_vars = {**_default_vars(),
                   "lang": lang, "lang_name": _lang_names.get(lang, lang),
                   **vars}
    env = _env_for(lang)
    head = env.render_str(source[:m.start()], **merged_vars)
    tail = env.render_str(source[m.end():], **merged_vars)
    return head.rstrip("\n") + "\n", tail.strip() + "\n"


def list_planner_sections(lang: str) -> tuple[str, ...]:
    """Ritorna i nomi RELATIVI (senza estensione) delle sezioni planner
    disponibili in `runtime/prompts/<lang>/planner/sections/`, ordinati
    alfabeticamente.

    Walk RICORSIVO (12/5/2026): sezioni nested supportate. Il nome relativo
    usa `/` come separatore di sub-dir (es. `web/search`, `workspace/drive`).
    L'ordine alfabetico cross-livello e' deterministico ed e' la chiave di
    cache stabile per `compose()`.

    Asse B (12/5/2026): considera sia `.j2` sia `.yaml`. Durante il PoC
    una sezione puo' esistere come entrambi (coesistenza A/B); il compositor
    `_compose_planner_cached` da' precedenza al `.yaml` se presente. La
    de-duplicazione qui usa il nome senza estensione: una sezione conta UNA
    volta anche se ha entrambi i siblings.

    Ritorna `()` se la dir non esiste (lingua senza split planner).
    """
    names: set[str] = set()
    sec_dir = _BASE / lang / "planner" / "sections"
    if sec_dir.is_dir():
        for pattern in ("*.j2", "*.yaml"):
            for p in sec_dir.rglob(pattern):
                rel = p.relative_to(sec_dir).with_suffix("")
                names.add(rel.as_posix())
    # Candidati §K: una sezione esistente solo come `_pending/.../<n>.j2.candidate`
    # è già operativa (auto-promote) → enumerala, così compose la include.
    cand_dir = _BASE / lang / "_pending" / "planner" / "sections"
    if cand_dir.is_dir():
        for p in cand_dir.rglob("*.j2.candidate"):
            name = p.relative_to(cand_dir).as_posix()
            if name.endswith(".j2.candidate"):
                name = name[: -len(".j2.candidate")]
            names.add(name)
    return tuple(sorted(names))


def _render_yaml_section(yaml_path: Path, fmt: str | None = None) -> str:
    """Dispatcher di rendering per una sezione YAML del PLANNER. PoC bench
    13/5/2026: 3 formati possibili selezionabili via env `METNOS_SECTION_FORMAT`
    (oppure arg `fmt` esplicito):

      - `prose`     (default): prosa §6 (DEVI/NON DEVI/OK/ERRORE) — baseline Asse B.
      - `yaml_raw`  : YAML grezzo passthrough, frontmatter metadati stripped.
      - `json_raw`  : sezione serializzata JSON indented.

    Determinismo §7.9: tutti e 3 i path sono pure-compute, niente LLM.
    """
    fmt = (fmt or _section_format())
    raw = yaml_path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(
            f"_render_yaml_section: {yaml_path} root non e' un dict YAML"
        )

    if fmt == "yaml_raw":
        clean = {k: v for k, v in data.items() if k not in _SECTION_FRONTMATTER_KEYS}
        out = yaml.safe_dump(
            clean,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
    elif fmt == "json_raw":
        clean = {k: v for k, v in data.items() if k not in _SECTION_FRONTMATTER_KEYS}
        out = json.dumps(clean, ensure_ascii=False, indent=2) + "\n"
    else:
        out = _render_yaml_section_prose(yaml_path, data)

    # Token-data §7.11: le sezioni .yaml NON passano da Jinja, quindi
    # `{{ current_year }}`/`{{ current_date }}` negli esempi vanno risolti qui,
    # deterministicamente, con la STESSA convenzione dei .j2 (date_tokens §7.3/§7.9).
    from date_tokens import substitute_date_tokens
    return substitute_date_tokens(out)


def _render_yaml_section_prose(yaml_path: Path, data: dict | None = None) -> str:
    """Render deterministico (§7.9) di una sezione planner in formato YAML
    (asse B refactor, PoC 12/5/2026). Schema canonico in
    `runtime/prompts/SCHEMA_section_rules.md`.

    Output prosa compatta che preserva il pattern §6 (DEVI/NON DEVI/OK/ERRORE)
    per ogni rule. Niente LLM, niente decorazione, niente blank line extra.

    Solleva ValueError su YAML malformato o schema mancante (caller deve
    fixare il `.yaml` prima del boot — niente fallback silenzioso §2.8).
    """
    if data is None:
        raw = yaml_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            raise ValueError(
                f"_render_yaml_section_prose: {yaml_path} root non e' un dict YAML"
            )
    section = data.get("section") or {}
    rules = data.get("rules") or []
    if not isinstance(section, dict) or not isinstance(rules, list):
        raise ValueError(
            f"_render_yaml_section_prose: {yaml_path} missing `section` dict "
            f"or `rules` list"
        )
    header = (section.get("header") or "").rstrip()
    preamble = (section.get("preamble") or "").rstrip()

    # Lingua per i marker compositor (DEVI/NON DEVI/OK/ERRORE vs MUST/etc.).
    # I marker §6 sono prescritti IT per the design guide §6: anche i prompt EN
    # nel codebase mantengono il pattern IT (vedi planner/_core.j2). Lasciamo
    # il marker IT come canonical: linter prompts_lint.py lo cerca.
    lines: list[str] = []
    sep = "=" * 70
    if header:
        lines.append(sep)
        lines.append(header)
        lines.append(sep)
    if preamble:
        if header:
            lines.append("")
        lines.append(preamble)

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        name = rule.get("name") or ""
        when = (rule.get("when") or "").strip()
        must = (rule.get("must") or "").strip()
        must_not = (rule.get("must_not") or "").strip()
        ok = (rule.get("ok") or "").strip()
        error = (rule.get("error") or "").strip()
        disamb = (rule.get("disambiguation") or "").strip()

        # 1 blank line di separazione fra rule e rule (compatto).
        if lines:
            lines.append("")
        # Header rule con nome e WHEN.
        lines.append(f"({name}) WHEN: {when}")
        # Corpo §6.
        lines.append(_compose_marker_line("DEVI", must))
        lines.append(_compose_marker_line("NON DEVI", must_not))
        lines.append(_compose_marker_line("OK", ok))
        lines.append(_compose_marker_line("ERRORE", error))
        if disamb:
            lines.append(_compose_marker_line("NB DISAMBIGUATION", disamb))

    out = "\n".join(lines)
    if not out.endswith("\n"):
        out += "\n"
    return out


def _compose_marker_line(marker: str, body: str) -> str:
    """Compone una riga `MARKER: body.` rispettando §6 (linter L2 cerca
    le keyword esatte). Se `body` finisce gia' con `.`/`!`/`?` non aggiunge
    punto. Multi-line bodies preservano i newline interni."""
    body = body.rstrip()
    if not body:
        return f"{marker}: "
    end_ok = body[-1] in ".!?"
    return f"{marker}: {body}" if end_ok else f"{marker}: {body}."


def _compose_planner_cached(lang: str, sections: tuple[str, ...],
                              vars_items: tuple[tuple[str, object], ...],
                              section_format: str = "prose") -> str:
    """Render del planner 3-layer. `sections` gia' ordinato e tupla immutabile;
    `vars_items` tupla di coppie hashable. Caller esterno: `compose()`.
    Vedere `compose()` per la semantica utente.

    Asse B (12/5/2026): per ogni sezione cerca prima `<section>.yaml` (schema
    strutturato, compositor deterministico). Se assente, fallback su
    `<section>.j2` (Jinja prosa, legacy/coesistenza per A/B test).

    `section_format` (PoC 13/5/2026): entra nella cache key; valori
    `prose`/`yaml_raw`/`json_raw`. Affecta SOLO le sezioni `.yaml`; le
    fallback `.j2` Jinja sono ortogonali (rendering Jinja fissato).
    """
    env = _env_for(lang)
    var_dict = dict(vars_items)
    parts: list[str] = []
    # Layer 1 — _core
    parts.append(env.render_template("planner/_core.j2", **var_dict))
    # Layer 2 — sezioni richieste (deterministico, ordinato alfabeticamente).
    # yaml/j2 dispatch con ripiego EN (§K): la sezione può venire da EN.
    for sec in sections:
        yaml_path = _section_yaml_path(lang, sec)
        if yaml_path is not None:
            # Render YAML strutturato (asse B PoC) — formato selezionabile.
            parts.append(_render_yaml_section(yaml_path, fmt=section_format))
        else:
            # Fallback Jinja2 prosa (asse A / pre-B). L'env risolve live→cand→EN.
            parts.append(env.render_template(f"planner/sections/{sec}.j2",
                                                **var_dict))
    # Layer 3 — _footer
    parts.append(env.render_template("planner/_footer.j2", **var_dict))
    return "\n".join(parts)


# LRU cache di `_compose_planner_cached`: chiave (lang, sections_tuple,
# vars_items_tuple). Limite 128 = supera ampiamente le combinazioni reali
# attese (alcune migliaia di chiamate per turn, ma sections=() o piccolo
# sottoinsieme + vars stabili a parita' di route_info).
_compose_planner_cached = functools.lru_cache(maxsize=128)(
    _compose_planner_cached
)

# Counters per metric esterno (cache_stats). Le hit/miss native di
# `functools.lru_cache` sono accessibili via `.cache_info()`, ma esponiamo
# un'API stabile cross-versione che include anche lookup non-cached (vars
# non-hashable) come miss e disposable_call. Determinismo §7.9: contatori
# in-process, no DB.
_compose_cache_counters = {
    "hits": 0,
    "misses": 0,
    "no_cache": 0,   # vars non hashable, render diretto
}


def compose(role: str, lang: str, *, sections=None, **vars) -> str:
    """Render del prompt `role`. Per `role!="planner"`: alias di `get(role,
    lang, **vars)` (compat universale).

    Per `role == "planner"`: rendering 3-layer (Fase C, 11/5/2026):
        _core.j2  +  sections/<name>.j2 (per ogni name in `sections`,
                     ordinato alfabeticamente)  +  _footer.j2

    `sections` semantica:
      - `None` o `()`  → include TUTTE le sezioni disponibili in
                          `prompts/<lang>/planner/sections/` (degrade
                          graceful: intent.confidence bassa / object
                          unknown / caller senza routing).
      - lista/tupla di nomi base senza `.j2` (es. `["mail"]`,
        `("mail","web")`) → include solo quelle, sorted per cache stability.
        Nomi sconosciuti = silenziosamente skippati (consente call-site
        evolutivo senza esplosione runtime).

    Cache:
      - LRU(128) su (lang, sorted_sections_tuple, frozen_vars_items_tuple).
      - Se `vars` contiene valori non-hashable (dict, list mutabili), il
        cache miss e' totale e la chiamata e' diretta (no cache).

    Logging debug 1 riga: livello DEBUG, prefix `prompt_loader.compose`.

    §K (15/6/2026): se la lingua richiesta non ha il planner split (né live né
    candidato), l'INTERO planner ricade su EN nel frattempo (lingua effettiva,
    `_effective_planner_lang`) — niente crash. Solleva RuntimeError solo se
    nemmeno EN ha la struttura (misconfig reale).
    """
    if role != "planner":
        return get(role, lang, **vars)

    # Lingua EFFETTIVA (§K): se la lingua non ha il planner split (live o
    # candidato), ricade su EN per l'intero planner — sezioni + _core dalla
    # stessa lingua, niente mix. IT/EN hanno i file → invariato.
    lang = _effective_planner_lang(lang)

    # Sezioni OPT-IN: NON incluse nel default "all-sections", solo via
    # selezione mirata. Riservato a sezioni a costo elevato il cui
    # contesto e' inutile fuori dal loro dominio. Vuota di default
    # (15/5/2026): la regressione che aveva motivato `scheduled_tasks`
    # opt-in e' stata risolta dal synthetic final_answer (ADR 0133 ext).
    _OPT_IN_SECTIONS: set[str] = set()
    # Risolvi la lista di sezioni effettive (sorted, deterministica).
    # Semantica (#H0 19/5/2026 sera — distinzione None vs () per core-only):
    #   sections=None  → ALL sezioni (degrade graceful, intent unknown)
    #   sections=()    → CORE ONLY (nessuna sezione, solo _core + _footer).
    #                    Usato quando l'object e' coperto dal core (files,
    #                    dirs, numbers, texts, ...).
    #   sections=[...] → solo quelle sezioni (intersezione con avail).
    # Set di sezioni CANONICO (§K): unione delle sezioni della lingua effettiva
    # con quelle EN. Così una lingua parzialmente tradotta ha SEMPRE la stessa
    # struttura di EN (capability piena) — ogni sezione si rende nella lingua se
    # c'è (live/candidato), altrimenti ricade su EN via il loader. Per IT/EN la
    # simmetria è garantita (pre-commit-symmetry) → unione == proprio set.
    _avail_set = set(list_planner_sections(lang))
    if lang != _FALLBACK_LANG:
        _avail_set |= set(list_planner_sections(_FALLBACK_LANG))
    if sections is None:
        effective = tuple(sorted(s for s in _avail_set
                                 if s not in _OPT_IN_SECTIONS))
    else:
        effective = tuple(sorted(set(sections) & _avail_set))

    # Verifica che la struttura split esista (un solo controllo, cheap): live,
    # candidato o ripiego EN (catena §K). `lang` è già la lingua effettiva.
    if _resolve_prompt_source(_BASE / lang, _BASE / _FALLBACK_LANG,
                              "planner/_core.j2") is None:
        raise RuntimeError(
            f"prompt_loader.compose: planner/_core.j2 assente in {lang!r} e "
            f"nel ripiego {_FALLBACK_LANG!r}. Struttura split planner mancante."
        )

    # Snapshot del formato sezione al tempo della call: entra nella cache key
    # cosi' che `METNOS_SECTION_FORMAT` switch (bench 13/5/2026) non collida
    # con render precedenti.
    sec_fmt = _section_format()

    # Tenta render via cache (vars hashable); fallback diretto se non-hashable.
    try:
        vars_items = tuple(sorted(vars.items()))
        # Test hashability per detection precoce di valori non-hashable.
        hash(vars_items)
        # Snapshot hits prima della call: se lru_cache risolve dalla cache,
        # `hits` increase di 1. Altrimenti misses+1. Comparison decide.
        info_before = _compose_planner_cached.cache_info()
        out = _compose_planner_cached(lang, effective, vars_items, sec_fmt)
        info_after = _compose_planner_cached.cache_info()
        if info_after.hits > info_before.hits:
            _compose_cache_counters["hits"] += 1
            cache_state = "cache_hit"
        else:
            _compose_cache_counters["misses"] += 1
            cache_state = "cache_miss"
    except TypeError:
        # Vars non-hashable (dict/list): render diretto senza cache.
        env = _env_for(lang)
        parts: list[str] = []
        parts.append(env.render_template("planner/_core.j2", **vars))
        for sec in effective:
            yaml_path = _section_yaml_path(lang, sec)   # ripiego EN §K
            if yaml_path is not None:
                parts.append(_render_yaml_section(yaml_path, fmt=sec_fmt))
            else:
                parts.append(env.render_template(
                    f"planner/sections/{sec}.j2", **vars))
        parts.append(env.render_template("planner/_footer.j2", **vars))
        out = "\n".join(parts)
        _compose_cache_counters["no_cache"] += 1
        cache_state = "no_cache"

    # Logging debug (1 riga, deterministico, no LLM, §7.9).
    try:
        import logging as _log
        _log.getLogger(__name__).debug(
            "compose role=planner lang=%s sections=%s fmt=%s %s",
            lang, list(effective), sec_fmt, cache_state,
        )
    except Exception:
        pass

    return out


def cache_stats() -> dict:
    """Ritorna metriche cache compose() in-process. Determinismo §7.9.

    Schema:
        {
            "hits": int,         # cache lru hit
            "misses": int,       # cache lru miss (render eseguito + cached)
            "no_cache": int,     # vars non-hashable, render senza cache
            "size": int,         # entries attualmente nella lru (<= maxsize)
            "maxsize": int,      # bound della lru
            "hit_ratio": float,  # hits / (hits + misses + no_cache); 0 se 0 call
        }
    """
    info = _compose_planner_cached.cache_info()
    h = _compose_cache_counters["hits"]
    m = _compose_cache_counters["misses"]
    nc = _compose_cache_counters["no_cache"]
    total = h + m + nc
    return {
        "hits": h,
        "misses": m,
        "no_cache": nc,
        "size": info.currsize,
        "maxsize": info.maxsize,
        "hit_ratio": (h / total) if total > 0 else 0.0,
    }


def invalidate_cache(role: str | None = None, lang: str | None = None) -> int:
    """Invalida (parzialmente o completamente) la cache di `compose()`.

    Args:
        role: se None o "planner", invalida la lru di `_compose_planner_cached`.
              Altri role attualmente non usano cache (alias di `get`), ritorna 0.
        lang: oggi ignorato (la lru e' globale, non per-lang). Reset completo.
              In futuro, se serve, si puo' rebuildare la cache mantenendo le
              entry di lingue diverse: oggi e' semplice clear() totale.

    Ritorna il numero di entry rimosse dalla lru (info pre-clear).

    Determinismo §7.9: niente effetti collaterali fuori dal modulo.
    Side-effect: reset dei counter hits/misses/no_cache.
    """
    _ = lang  # placeholder per future evoluzioni
    if role is not None and role != "planner":
        return 0
    info = _compose_planner_cached.cache_info()
    removed = info.currsize
    _compose_planner_cached.cache_clear()
    _compose_cache_counters["hits"] = 0
    _compose_cache_counters["misses"] = 0
    _compose_cache_counters["no_cache"] = 0
    return removed


_LANG_STATE_FILENAME = ".lang_state.json"


def lang_state_path(lang: str) -> Path:
    """Ritorna il path del file `.lang_state.json` per `lang`. Non lo crea."""
    return _BASE / lang / _LANG_STATE_FILENAME


def load_lang_state(lang: str) -> dict:
    """Carica `prompts/<lang>/.lang_state.json`. Ritorna `{}` se assente o corrotto.

    Schema:
        {
            "<role>": {
                "version_hash": "sha256:<hex>",
                "source_lang": "<lang>" | None,
                "source_hash": "sha256:<hex>" | None
            },
            ...
        }
    """
    p = lang_state_path(lang)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {}


def save_lang_state(lang: str, state: dict) -> None:
    """Salva `prompts/<lang>/.lang_state.json` con indentazione 2."""
    p = lang_state_path(lang)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _canonical_role_set(lang_dir: Path) -> set[str]:
    """Ritorna i `role` canonici per una dir lingua. Considera (a) i file
    `.j2` flat top-level, (b) la struttura split planner (`planner/_core.j2`,
    `planner/_footer.j2`, `planner/sections/*.j2`), e (c) sezioni YAML
    strutturate (asse B, 12/5/2026: `planner/sections/*.yaml`).

    Per i flat: il role = `stem` (es. "vaglio").
    Per split planner: usa path relativo con slash (es. "planner/_core",
    "planner/sections/mail") in modo che `validate_invariant()` confronti
    insiemi simmetrici fra lingue.

    Asse B: una sezione conta UNA volta indipendentemente dal formato (un
    sibling `calendar.j2` e `calendar.yaml` -> un solo role `planner/
    sections/calendar`). Cosi' la simmetria L5 e' tollerante al PoC misto.
    """
    if not lang_dir.is_dir():
        return set()
    roles: set[str] = set()
    # Flat top-level
    for p in lang_dir.glob("*.j2"):
        roles.add(p.stem)
    # Split planner (Fase C, 11/5/2026): conta i file split come role
    # distinti per il check di simmetria cross-lingua.
    planner_dir = lang_dir / "planner"
    if planner_dir.is_dir():
        for sub in ("_core.j2", "_footer.j2"):
            if (planner_dir / sub).is_file():
                roles.add(f"planner/{sub[:-3]}")
        sec_dir = planner_dir / "sections"
        if sec_dir.is_dir():
            for pattern in ("*.j2", "*.yaml"):
                for p in sec_dir.rglob(pattern):
                    rel = p.relative_to(sec_dir).with_suffix("")
                    roles.add(f"planner/sections/{rel.as_posix()}")
    return roles


def validate_invariant() -> None:
    """Boot check: ogni sub-dir lingua ha lo stesso set di role di it/
    (canonical reference). Chiamata dal server al startup. Boot fail se
    una lingua secondaria ha un role mancante (es. en/ senza synt_code.j2
    o senza planner/sections/web.j2).

    Considera sia i file flat top-level sia la struttura split planner
    (Fase C, 11/5/2026): `planner/_core`, `planner/_footer`,
    `planner/sections/*`.
    """
    canonical_dir = _BASE / "it"
    if not canonical_dir.is_dir():
        raise RuntimeError(
            f"prompt_loader: canonical dir {canonical_dir} non esiste."
        )
    canonical = _canonical_role_set(canonical_dir)
    for sub in _BASE.iterdir():
        if not sub.is_dir() or sub.name == "it":
            continue
        roles = _canonical_role_set(sub)
        missing = canonical - roles
        if missing:
            raise RuntimeError(
                f"prompts/{sub.name}/ missing roles: {sorted(missing)}"
            )
