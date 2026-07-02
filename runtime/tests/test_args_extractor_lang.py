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


# ── _extract_count (E.2, 2/7/2026): cap esplicito, mai ints[0] ──────────────

def test_count_noun_adjacent_extracts():
    from args_extractor import _extract_count
    assert _extract_count("cerca 100 foto di mare") == 100
    assert _extract_count("le ultime 10 mail") == 10
    assert _extract_count("mostrami 25 immagini della gita") == 25
    assert _extract_count("find 30 files in tmp") == 30
    assert _extract_count("show me 12 photos of venice") == 12


def test_count_cap_prefix_extracts():
    from args_extractor import _extract_count
    assert _extract_count("le prime 5 mail di oggi") == 5
    assert _extract_count("i primi 3 risultati") == 3
    assert _extract_count("top 7 file per dimensione") == 7
    assert _extract_count("first 10 files by size") == 10
    assert _extract_count("at most 20 results") == 20


def test_count_rejects_years_prices_time():
    # E.2 (14/6): ints[0] naive iniettava anni/prezzi/finestre come cap.
    from args_extractor import _extract_count
    assert _extract_count("le foto del 2020") is None
    assert _extract_count("le spese da 50 euro") is None
    assert _extract_count("le mail delle ultime 24 ore") is None
    assert _extract_count("gli eventi degli ultimi 3 giorni") is None
    assert _extract_count("photos from 2019") is None
    assert _extract_count("emails of the last 48 hours") is None


def test_regex_extract_max_results_uses_count():
    schema = {"properties": {"max_results": {"type": "integer"},
                             "query_text": {"type": "string"}}}
    out = regex_extract("cerca 100 foto di roberto", schema)
    assert out.get("max_results") == 100
    out2 = regex_extract("cerca le foto del 2020", schema)
    assert "max_results" not in out2
