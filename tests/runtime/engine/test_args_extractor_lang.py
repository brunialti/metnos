"""test_args_extractor_lang — fix v6 (25/6): nome-linguaggio → estensione glob.

Bug live (turn 1765e7ee): «quanti file python» su github → il proposer non
ricavava il pattern «*.py», l'estrattore tornava None perche' «python» eccede
il range estensione {1,5} e non c'era mappa linguaggio→estensione. §7.3 fix
generale: _LANG_EXT_MAP. Questi test difendono la regola, non il singolo caso.
"""
import sys
from pathlib import Path

_RT = (Path(__file__).resolve().parents[3] / "runtime")

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


def _semantic_file_schema():
    return {
        "properties": {
            "patterns": {
                "type": "array",
                "semantic_type": "file_globs",
            },
        },
    }


def test_broad_image_kind_expands_to_complete_manifest_patterns():
    for query in (
        "Trova i file immagine duplicati nella cartella Immagini del server",
        "Trova i file di immagini duplicati nella cartella Immagini",
        "Find duplicate image files in folder Pictures",
    ):
        patterns = regex_extract(query, _semantic_file_schema())["patterns"]
        assert "*.jpg" in patterns, query
        assert "*.png" in patterns, query
        assert "*.arw" in patterns, query
        assert "*.zip" not in patterns, query


def test_container_name_alone_does_not_fabricate_file_kind_filter():
    out = regex_extract(
        "Trova i file duplicati nella cartella Immagini",
        _semantic_file_schema(),
    )
    assert "patterns" not in out


def test_explicit_extension_wins_over_broad_kind():
    out = regex_extract(
        "Trova immagini duplicate .png nella cartella Foto",
        _semantic_file_schema(),
    )
    assert out["patterns"] == ["*.png"]


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


# ── §2.9 safety-relax (9/7): vocab-OPERAZIONE non attiva i flag booleani ──────
# `allow_dirs`/`allow_system` di move_files condividono «spostare»/«allows» nelle
# loro description; senza esclusione, «sposta X in Y» li fabbricava =true erodendo
# il safety-net dei move. Il fix esclude i prefissi-4 condivisi fra >=2 flag bool.
import tomllib  # noqa: E402
from args_extractor import _operation_prefixes  # noqa: E402


def _move_schema():
    with open(_RT.parent / "executors" / "move_files" / "manifest.toml", "rb") as f:
        return tomllib.load(f)["args"]


def test_operation_vocab_excludes_shared_move_verb():
    props = _move_schema()["properties"]
    ops = _operation_prefixes(props)
    assert "spos" in ops   # «spostare» condiviso allow_dirs+allow_system
    assert "cons" in ops   # «consente» condiviso


def test_plain_move_does_not_fabricate_safety_flags():
    sch = _move_schema()
    for q in ("sposta i file da /a a /b", "muovi le foto in /dst",
              "move the files to /dst"):
        r = regex_extract(q, sch)
        assert r.get("allow_dirs") is None, q
        assert r.get("allow_system") is None, q
        assert r.get("copy") is None, q   # runtime_resolved, mai estratto


def test_distinctive_condition_still_activates_allow_system():
    # «file di sistema» = condizione DISTINTIVA di allow_system → resta attivabile.
    r = regex_extract("sposta anche i file di sistema in /b", _move_schema())
    assert r.get("allow_system") is True
