"""prompt_loader.py — carica i prompt LLM da `runtime/prompts/<lang>/`.

ADR 0092 (5/5/2026): prompt come dati persistiti su filesystem, non come
stringhe inline nei moduli Python. Engine: MiniJinja (drop-in Jinja2 syntax,
Rust core, errori più chiari).

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

Determinismo (CLAUDE.md §7.9): zero LLM nel loader. Niente DB, niente
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
from pathlib import Path

import minijinja

_BASE = Path(__file__).parent / "prompts"
# Cache per-lang: ogni lingua ha la sua Environment isolata (no pollution).
_envs: dict[str, minijinja.Environment] = {}


def _env_for(lang: str) -> minijinja.Environment:
    """Ritorna (creando lazy + cachando) la `minijinja.Environment` per `lang`.
    Solleva RuntimeError se `runtime/prompts/<lang>/` non esiste."""
    env = _envs.get(lang)
    if env is not None:
        return env
    root = _BASE / lang
    if not root.is_dir():
        raise RuntimeError(
            f"prompt_loader: prompts root {root} non esiste. "
            f"Verifica lang ({lang!r}) e la struttura runtime/prompts/."
        )

    def _loader(name: str):
        # Supporta nomi con slash (es. "planner/_core.j2"): risolti relativi a
        # `root`, senza permettere uscita dalla dir lingua (no `..`).
        p = (root / name).resolve()
        try:
            p.relative_to(root.resolve())
        except ValueError:
            return None  # tentativo di uscire dalla dir lingua
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8")

    env = minijinja.Environment(loader=_loader, keep_trailing_newline=True)
    _envs[lang] = env
    return env


def get(role: str, lang: str, **vars) -> str:
    """Render `runtime/prompts/<lang>/<role>.j2` con `**vars` come variabili
    Jinja2. `lang` è obbligatorio: ogni caller dichiara la lingua corrente
    (default `config.DEFAULT_LANG`). Cache built-in per Environment.
    Solleva TemplateError se il template non esiste o ha errori di sintassi."""
    return _env_for(lang).render_template(f"{role}.j2", **vars)


def list_planner_sections(lang: str) -> tuple[str, ...]:
    """Ritorna i nomi base (senza `.j2`) delle sezioni planner disponibili
    in `runtime/prompts/<lang>/planner/sections/`, ordinati alfabeticamente.

    Stabilita' = ordine = chiave di cache deterministica per `compose()`.
    Ritorna `()` se la dir non esiste (lingua senza split planner).
    """
    sec_dir = _BASE / lang / "planner" / "sections"
    if not sec_dir.is_dir():
        return ()
    return tuple(sorted(p.stem for p in sec_dir.glob("*.j2")))


def _compose_planner_cached(lang: str, sections: tuple[str, ...],
                              vars_items: tuple[tuple[str, object], ...]) -> str:
    """Render del planner 3-layer. `sections` gia' ordinato e tupla immutabile;
    `vars_items` tupla di coppie hashable. Caller esterno: `compose()`.
    Vedere `compose()` per la semantica utente."""
    env = _env_for(lang)
    var_dict = dict(vars_items)
    parts: list[str] = []
    # Layer 1 — _core
    parts.append(env.render_template("planner/_core.j2", **var_dict))
    # Layer 2 — sezioni richieste (deterministico, ordinato alfabeticamente)
    for sec in sections:
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

    Solleva RuntimeError se la struttura split planner non esiste per la
    lingua richiesta (caller deve fixare i prompt prima del boot).
    """
    if role != "planner":
        return get(role, lang, **vars)

    # Risolvi la lista di sezioni effettive (sorted, deterministica).
    if sections is None or not sections:
        effective = list_planner_sections(lang)
    else:
        avail = set(list_planner_sections(lang))
        effective = tuple(sorted(set(sections) & avail))

    # Verifica che la struttura split esista (un solo controllo, cheap):
    core_path = _BASE / lang / "planner" / "_core.j2"
    if not core_path.is_file():
        raise RuntimeError(
            f"prompt_loader.compose: prompts/{lang}/planner/_core.j2 "
            f"non esiste. Struttura split planner mancante per lang={lang!r}."
        )

    # Tenta render via cache (vars hashable); fallback diretto se non-hashable.
    try:
        vars_items = tuple(sorted(vars.items()))
        # Test hashability per detection precoce di valori non-hashable.
        hash(vars_items)
        out = _compose_planner_cached(lang, effective, vars_items)
        cache_state = "cache_ok"
    except TypeError:
        # Vars non-hashable (dict/list): render diretto senza cache.
        env = _env_for(lang)
        parts: list[str] = []
        parts.append(env.render_template("planner/_core.j2", **vars))
        for sec in effective:
            parts.append(env.render_template(f"planner/sections/{sec}.j2",
                                                **vars))
        parts.append(env.render_template("planner/_footer.j2", **vars))
        out = "\n".join(parts)
        cache_state = "no_cache"

    # Logging debug (1 riga, deterministico, no LLM, §7.9).
    try:
        import logging as _log
        _log.getLogger(__name__).debug(
            "compose role=planner lang=%s sections=%s %s",
            lang, list(effective), cache_state,
        )
    except Exception:
        pass

    return out


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
    `.j2` flat top-level e (b) la struttura split planner (`planner/_core.j2`,
    `planner/_footer.j2`, `planner/sections/*.j2`).

    Per i flat: il role = `stem` (es. "vaglio").
    Per split planner: usa path relativo con slash (es. "planner/_core",
    "planner/sections/mail") in modo che `validate_invariant()` confronti
    insiemi simmetrici fra lingue.
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
            for p in sec_dir.glob("*.j2"):
                roles.add(f"planner/sections/{p.stem}")
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
