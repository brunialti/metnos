"""prompt_loader.py — carica i prompt LLM da `runtime/prompts/<lang>/`.

ADR 0092 (5/5/2026): prompt come dati persistiti su filesystem, non come
stringhe inline nei moduli Python. Engine: MiniJinja (drop-in Jinja2 syntax,
Rust core, errori più chiari).

API:
    get(role, lang, **vars)  # render runtime/prompts/<lang>/<role>.j2
    validate_invariant()     # boot check: tutte le sub-dir lingua hanno
                             # stesso set di file di it/ (canonical)
    load_lang_state(lang)    # carica `prompts/<lang>/.lang_state.json`
    save_lang_state(lang, s) # salva `prompts/<lang>/.lang_state.json`

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
"""
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
        p = root / name
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


def validate_invariant() -> None:
    """Boot check: ogni sub-dir lingua ha lo stesso set di file di it/
    (canonical reference). Chiamata dal server al startup. Boot fail se
    una lingua secondaria ha un file mancante (es. en/ senza synt_code.j2).
    """
    canonical_dir = _BASE / "it"
    if not canonical_dir.is_dir():
        raise RuntimeError(
            f"prompt_loader: canonical dir {canonical_dir} non esiste."
        )
    canonical = {p.name for p in canonical_dir.glob("*.j2")}
    for sub in _BASE.iterdir():
        if not sub.is_dir() or sub.name == "it":
            continue
        files = {p.name for p in sub.glob("*.j2")}
        missing = canonical - files
        if missing:
            raise RuntimeError(
                f"prompts/{sub.name}/ missing files: {sorted(missing)}"
            )
