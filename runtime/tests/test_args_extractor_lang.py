"""test_args_extractor_lang — fix v6 (25/6): nome-linguaggio → estensione glob.

Bug live (turn 1765e7ee): «quanti file python» su github → il proposer non
ricavava il pattern «*.py», l'estrattore tornava None perche' «python» eccede
il range estensione {1,5} e non c'era mappa linguaggio→estensione. §7.3 fix
generale: _LANG_EXT_MAP. Questi test difendono la regola, non il singolo caso.
"""
import sys
from pathlib import Path

_RT = Path(__file__).resolve().parent.parent
if str(_RT) not in sys.path:
    sys.path.insert(0, str(_RT))

from args_extractor import _extract_file_ext_glob, regex_extract  # noqa: E402


def test_language_names_map_to_extension():
    cases = {
        "quanti file python": "*.py",
        "i file javascript": "*.js",
        "file typescript": "*.ts",
        "file markdown": "*.md",
        "file rust": "*.rs",
        "quanti file shell": "*.sh",
        "file golang": "*.go",
    }
    for q, exp in cases.items():
        assert _extract_file_ext_glob(q) == exp, f"{q!r} -> {_extract_file_ext_glob(q)!r}"


def test_explicit_extension_still_wins():
    # Il path con estensione esplicita non deve regredire.
    assert _extract_file_ext_glob("file .py") == "*.py"
    assert _extract_file_ext_glob("quanti file .py ci sono") == "*.py"


def test_short_format_keywords_preserved():
    # "file PDF"/"documenti PDF": il ramo {2,5}-lettere resta funzionante.
    assert _extract_file_ext_glob("file PDF") == "*.pdf"
    assert _extract_file_ext_glob("documenti PDF") == "*.pdf"
    assert _extract_file_ext_glob("file di tipo XML") == "*.xml"


def test_no_file_type_returns_none():
    assert _extract_file_ext_glob("crea cartella /tmp/x") is None
    assert _extract_file_ext_glob("che ore sono") is None


def test_generic_file_count_not_a_pattern():
    # Regressione: «quanti file ci sono» NON deve dare *.ci (la parola dopo
    # «file» genera pattern solo se è un'estensione nota — whitelist).
    assert _extract_file_ext_glob("quanti file ci sono nel repo") is None
    assert _extract_file_ext_glob("quanti file ci sono") is None
    assert _extract_file_ext_glob("che file sono") is None
    assert _extract_file_ext_glob("quanti file non letti") is None


def test_javascript_not_shadowed_by_java():
    # Ordine per lunghezza decrescente: «javascript» non deve matchare «java».
    assert _extract_file_ext_glob("file javascript") == "*.js"
    assert _extract_file_ext_glob("file java") == "*.java"


def test_end_to_end_pattern_arg():
    # API pubblica: uno schema che dichiara `pattern` riceve il glob giusto.
    schema = {"properties": {"pattern": {"type": "string"}}}
    out = regex_extract("quanti file python nel repo", schema)
    assert out.get("pattern") == "*.py", out
