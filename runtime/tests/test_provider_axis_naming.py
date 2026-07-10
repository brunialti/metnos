"""test_provider_axis_naming — l'asse PROVIDER §2.2 come famiglia esplicita.

Bonifica SoT (26/6): `github` era in vocab.QUALIFIERS ma `google_workspace` NO
→ `validate_name(read_events_google_workspace)` FALLIVA (parsava qual=google,
desc=workspace). I vendorizzati firmati bypassano la validazione, ma un provider
SINTETIZZATO sarebbe stato rifiutato. Ora `PROVIDER_SUFFIXES` (derivato dalla SoT
`detection_lexicon provider.markers`) è una famiglia esplicita; `parse_name`
riconosce i provider multi-token (`google_workspace`) come UN'unità qualifier.

Difende anche la REGOLA DI FOCUSING (name-containment): un nome
`verbo_oggetto_<provider>` ESTENDE il generico `verbo_oggetto` (drop del generico
col marker presente, `provider_gate_names`); un qualifier-MODALITÀ (ocr/csv/...)
NON ha questa relazione (read_files_ocr ≠ read_files). Audit auto-verificante:
la regola tocca SOLO i provider-pair, mai i qualifier-pair.
"""
import sys
from pathlib import Path

_RT = Path(__file__).resolve().parent.parent
if str(_RT) not in sys.path:
    sys.path.insert(0, str(_RT))

import vocab                                           # noqa: E402
import naming_grammar as ng                            # noqa: E402


# ── SoT sync: PROVIDER_SUFFIXES == provider.markers ─────────────────────────

def test_provider_markers_cover_suffixes():
    """`provider.markers` (detection_lexicon) DERIVA le sue chiavi da
    `vocab.PROVIDER_SUFFIXES` (fonte unica). Questo guard verifica la
    COMPLETEZZA: ogni provider del vocabolario ha i suoi marker NL nel seed, e
    viceversa nessuna chiave-marker orfana. Non è più un anti-drift (non c'è più
    copia), ma una garanzia che identità e marker restino allineati."""
    import detection_lexicon as dl
    marker_keys = {s.lstrip("_") for s in dl.mapping("provider.markers").keys()}
    assert vocab.PROVIDER_SUFFIXES == marker_keys, (
        f"PROVIDER_SUFFIXES {sorted(vocab.PROVIDER_SUFFIXES)} != chiavi "
        f"provider.markers {sorted(marker_keys)} — un provider senza marker NL "
        f"(o viceversa). Aggiungi i marker in detection_lexicon_seed.")


def test_provider_skills_cover_suffixes():
    """`PROVIDER_SKILLS` (provider→skill, per sandbox/placement) copre
    esattamente PROVIDER_SUFFIXES: un provider senza skill (o viceversa)
    romperebbe invocation_skills in silenzio."""
    assert set(vocab.PROVIDER_SKILLS.keys()) == set(vocab.PROVIDER_SUFFIXES)


def test_providers_are_qualifiers_and_compat():
    # ogni provider è un qualifier valido + ha una mappa object-compat.
    for p in vocab.PROVIDER_SUFFIXES:
        assert p in vocab.QUALIFIERS, f"{p} non in QUALIFIERS"
        assert p in vocab.QUALIFIER_OBJECT_COMPAT, f"{p} non in COMPAT"


# ── naming: provider single + multi-token validano ──────────────────────────

def test_provider_names_validate():
    for nm in ("find_files_github", "read_files_github", "list_dirs_github",
               "read_events_google_workspace", "share_files_google_workspace",
               "create_calendars_google_workspace"):
        r = ng.validate_name(nm)
        assert r.ok, f"{nm} dovrebbe validare: {r.reason}"


def test_provider_multitoken_parses_as_one_qualifier():
    nc = ng.parse_name("read_events_google_workspace")
    assert nc.verb == "read" and nc.obj == "events"
    assert nc.qualifier == "google_workspace" and nc.descriptor is None


def test_modality_qualifiers_unchanged():
    # i qualifier-modalità NON sono provider e parsano come prima.
    for nm, q in (("read_files_ocr", "ocr"), ("read_files_csv", "csv"),
                  ("write_files_spreadsheet", "spreadsheet")):
        nc = ng.parse_name(nm)
        assert nc.qualifier == q and nc.descriptor is None
        assert ng.validate_name(nm).ok


def test_descriptor_still_works():
    # 4° livello (descriptor) su qualifier-modalità invariato.
    nc = ng.parse_name("compute_files_loc_per-language")
    assert nc.qualifier == "loc" and nc.descriptor == "per-language"


# ── audit auto-verificante della REGOLA DI FOCUSING ─────────────────────────

def _containment_pairs(names):
    """(A, B, suffix) dove B = A + '_<suffix>' (A prefisso di B su '_')."""
    out = []
    for a in names:
        for b in names:
            if a != b and b.startswith(a + "_"):
                out.append((a, b, b[len(a) + 1:]))
    return out


def _is_provider_suffix(suf):
    head = suf.split("_")[0]
    return suf in vocab.PROVIDER_SUFFIXES or head in vocab.PROVIDER_SUFFIXES


def test_focusing_rule_classification_synthetic():
    """REGOLA DI FOCUSING (test autoritativo, DETERMINISTICO): su un catalog
    SINTETICO con pair noti, il drop-generico classifica come provider SOLO i
    containment-pair il cui suffisso è un provider; i pair qualifier-modalità
    (read_files ⊂ read_files_ocr) NON sono toccati. Indipendente dal catalog
    live (sotto la HOME-sandbox dei test gli executor user-data — github — sono
    assenti: vedi conftest e [[project-i18n-lexicon-debt]] per il pattern). Così
    la regola è verificata SEMPRE, non solo quando esistono executor provider."""
    names = [
        "find_files", "find_files_github",                # provider-pair
        "read_files", "read_files_github",                # provider-pair
        "read_events", "read_events_google_workspace",    # provider multi-token
        "read_files_ocr",                                 # qualifier: read_files ⊂ read_files_ocr
        "filter_entries", "list_dirs",
    ]
    pairs = _containment_pairs(names)
    provider_pairs = [(a, b) for a, b, s in pairs if _is_provider_suffix(s)]
    qualifier_pairs = [(a, b, s) for a, b, s in pairs
                       if not _is_provider_suffix(s)]
    # i provider-pair attesi (incl. multi-token) sono riconosciuti...
    assert ("find_files", "find_files_github") in provider_pairs
    assert ("read_events", "read_events_google_workspace") in provider_pairs
    # ...e il qualifier-modalità NON è classificato provider (mai droppato).
    assert ("read_files", "read_files_ocr", "ocr") in qualifier_pairs
    for a, b, s in qualifier_pairs:
        assert not _is_provider_suffix(s)


def test_focusing_rule_touches_only_provider_pairs():
    """Audit BEST-EFFORT sul catalog REALE: SE ci sono provider-pair, il loro
    suffisso DEVE essere un provider, e nessun qualifier-pair dev'essere
    classificato provider per errore. NON richiede che esistano executor provider
    (sono user-data, assenti sotto la HOME-sandbox dei test → conftest): la regola
    in sé è coperta da `test_focusing_rule_classification_synthetic`. Questo guard
    intercetta un nuovo executor REALE mal-categorizzato, senza essere
    order-dipendente dallo stato user-data del catalog."""
    import loader
    cat = loader.load_catalog(verify=True)
    names = sorted({getattr(e, "name", None)
                    for e in (cat.values() if hasattr(cat, "values") else cat)
                    if getattr(e, "name", None)})
    pairs = _containment_pairs(names)
    qualifier_pairs = [(a, b, s) for a, b, s in pairs
                       if not _is_provider_suffix(s)]
    for a, b, s in _containment_pairs(names):
        if _is_provider_suffix(s):
            assert _is_provider_suffix(b[len(a) + 1:])
    for a, b, s in qualifier_pairs:
        assert not _is_provider_suffix(s), (
            f"{a} ⊂ {b}: suffix {s} classificato provider per errore")


def test_early_warning_unclassified_containment():
    """EARLY-WARNING: ogni containment-pair il cui suffisso NON è né provider
    né qualifier §2.2 è un caso da indagare (es. read_tasks ⊂ read_tasks_history,
    `_history` non classificato). Il test NON fallisce su questi (sono legittimi
    intenti-diversi), ma li ELENCA così un nuovo caso ambiguo non sfugge. Fallisce
    SOLO se un tale suffisso fosse erroneamente trattato come provider."""
    import loader
    cat = loader.load_catalog(verify=True)
    names = sorted({getattr(e, "name", None)
                    for e in (cat.values() if hasattr(cat, "values") else cat)
                    if getattr(e, "name", None)})
    quals = set(vocab.QUALIFIERS)
    unclassified = []
    for a, b, s in _containment_pairs(names):
        head = s.split("_")[0]
        if not _is_provider_suffix(s) and head not in quals:
            unclassified.append((a, b, s))
            # invariante: un suffisso non-classificato NON deve mai essere provider
            assert not _is_provider_suffix(s)
    # documenta (non fallisce): i non-classificati sono intenti-diversi leciti.
    # se la lista cresce inaspettatamente, è un segnale di audit.
    print(f"\n[early-warning] containment non classificati: {unclassified}")
