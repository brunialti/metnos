"""Regression test — bug 2/6/2026: query "cerca foto in montagna → spreadsheet
→ email" falliva su 3 cause-radice. Test deterministici (no server) che
bloccano le tre regressioni:

  1. relevance_cut.adaptive_relevance_threshold — taglio di rilevanza adattivo
     (anisotropia embedding densi: soglia ASSOLUTA inutile, serve μ+3σ per-query).
  2. spreadsheet LOCALE — create/write/read_files_spreadsheet defaultano al
     backend `local` (.xlsx allegabile a email), NON Google (§10.3/§2.2).
  3. validate_args — guard universale: un rifiuto LLM trapelato come valore di
     un arg ("Come modello linguistico, non posso...") NON raggiunge l'executor.

Eseguibile sia con pytest sia direttamente:  python3 test_spreadsheet_local_relevance_cut.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for _p in (_ROOT / "runtime",
           _ROOT / "executors" / "create_files_spreadsheet",
           _ROOT / "executors" / "write_files_spreadsheet",
           _ROOT / "executors" / "read_files_spreadsheet"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# ---------------------------------------------------------------- relevance_cut
def test_relevance_cut_collapsed_distribution_keeps_only_tail():
    """Distribuzione collassata (anisotropia): bulk a 0.60, 3 outlier a ~0.76.
    Il taglio μ+3σ tiene SOLO gli outlier, non l'intero bulk."""
    from relevance_cut import adaptive_relevance_threshold
    scores = [0.60] * 1000 + [0.75, 0.76, 0.77]
    thr = adaptive_relevance_threshold(scores, floor=0.40)
    keep = sum(1 for s in scores if s >= thr)
    assert keep == 3, f"atteso 3 outlier tenuti, ottenuti {keep} (thr={thr:.3f})"
    assert thr > 0.60, f"soglia deve superare il bulk 0.60, e' {thr:.3f}"


def test_relevance_cut_floor_protects_no_match_query():
    """Query senza match reali (tutti i punteggi bassi): il pavimento assoluto
    impedisce di restituire la coda relativa di puro rumore."""
    from relevance_cut import adaptive_relevance_threshold
    scores = [0.10 + (i % 5) * 0.001 for i in range(200)]  # tutti ~0.10
    thr = adaptive_relevance_threshold(scores, floor=0.40)
    assert thr >= 0.40, f"soglia sotto il floor: {thr:.3f}"
    assert sum(1 for s in scores if s >= thr) == 0


def test_relevance_cut_small_sample_returns_floor():
    """Sotto la soglia di campione minimo la statistica non e' affidabile."""
    from relevance_cut import adaptive_relevance_threshold
    assert adaptive_relevance_threshold([0.9, 0.8, 0.7], floor=0.40) == 0.40
    assert adaptive_relevance_threshold([], floor=0.25) == 0.25


def test_relevance_cut_full_corpus_not_returned():
    """Regressione diretta del bug: NON deve tenere ~tutto il corpus quando il
    coseno e' collassato in banda stretta ad alta media (μ~0.6)."""
    from relevance_cut import adaptive_relevance_threshold
    import random
    rng = random.Random(42)
    # 31000 valori normali attorno a 0.60 (banda stretta) + 100 outlier a 0.72+
    scores = [min(0.70, max(0.50, rng.gauss(0.60, 0.043))) for _ in range(31000)]
    scores += [0.72 + rng.random() * 0.05 for _ in range(100)]
    thr = adaptive_relevance_threshold(scores, floor=0.40)
    keep = sum(1 for s in scores if s >= thr)
    assert keep < 2000, f"taglio inefficace: {keep}/{len(scores)} tenuti (bug 31062)"


# ----------------------------------------------------------- spreadsheet LOCALE
def _tmp_xlsx(name: str) -> str:
    d = _ROOT / "tests" / "e2e" / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / name)


def test_create_files_spreadsheet_defaults_local_and_makes_real_file():
    """create_files_spreadsheet senza `client` → backend LOCALE → file .xlsx
    reale sul disco, allegabile a un'email (NON un Google Sheets online)."""
    import create_files_spreadsheet as CFS
    path = _tmp_xlsx("regr_create.xlsx")
    if os.path.exists(path):
        os.remove(path)
    rows = [["path", "descrizione"],
            ["/img/CIMG9383.JPG", "Tre persone in ambiente montuoso innevato"]]
    res = CFS.invoke({"title": "foto montagna", "values": rows, "path": path})
    try:
        assert res.get("ok") is True, res
        assert res.get("files_source") == "local", res
        assert os.path.isfile(path) and os.path.getsize(path) > 0
        # contratto undo `delete_created_paths` (manifest): la riga results
        # deve avere created=True + path, cosi' reverse_patterns rimuove il file.
        row = (res.get("results") or [{}])[0]
        assert row.get("created") is True and row.get("path") == path, row
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_spreadsheet_local_roundtrip_create_write_read():
    """create(values) → read → write(append) → read: i dati persistono."""
    import create_files_spreadsheet as CFS
    import write_files_spreadsheet as WFS
    import read_files_spreadsheet as RFS
    path = _tmp_xlsx("regr_roundtrip.xlsx")
    if os.path.exists(path):
        os.remove(path)
    try:
        CFS.invoke({"title": "rt", "values": [["a", "b"]], "path": path})
        r1 = RFS.invoke({"spreadsheet_id": path})
        assert r1.get("ok") and r1.get("used") == 1, r1
        w = WFS.invoke({"spreadsheet_id": path, "values": [["c", "d"]], "mode": "append"})
        assert w.get("ok") and w.get("n_written") == 1, w
        r2 = RFS.invoke({"spreadsheet_id": path})
        assert r2.get("used") == 2, r2
        assert r2["values"][1] == ["c", "d"], r2["values"]
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_read_nonexistent_local_spreadsheet_fails_honestly():
    """File inesistente → ok=false esplicito (§2.8 no silent failure)."""
    import read_files_spreadsheet as RFS
    res = RFS.invoke({"spreadsheet_id": "/tmp/metnos_nope_404.xlsx"})
    assert res.get("ok") is False and res.get("error_code") == "ERR_PATH_NOT_FOUND", res


# ----------------------------------------------------------- refusal-in-args
def test_validate_args_rejects_llm_refusal_leak():
    """Un rifiuto LLM come valore di un arg = step malformato (bug: il refusal
    era trapelato in spreadsheet_id)."""
    from agent_runtime import validate_args
    schema = {"type": "object", "required": ["spreadsheet_id"],
              "properties": {"spreadsheet_id": {"type": "string"}}}
    bad = {"spreadsheet_id": "Non posso eseguire questa operazione. Come modello "
                             "linguistico, non ho accesso diretto ai tuoi file."}
    fails = validate_args(bad, schema)
    assert any("rifiuto" in f or "meta-testo" in f for f in fails), fails
    # EN refusal in a list item
    fails_en = validate_args(
        {"paths": ["/ok.txt", "As an AI, I do not have access to your files"]},
        {"properties": {"paths": {"type": "array"}}})
    assert fails_en, "rifiuto EN in list item non intercettato"


def test_validate_args_allows_legit_values():
    """Nessun falso positivo su valori/query legittimi."""
    from agent_runtime import validate_args
    assert not validate_args(
        {"spreadsheet_id": "/home/x/foglio.xlsx"},
        {"properties": {"spreadsheet_id": {"type": "string"}}})
    assert not validate_args(
        {"query_text": "foto di persone in montagna"},
        {"properties": {"query_text": {"type": "string"}}})


# ------------------------------- fix d'istanza: write upsert + arg nascosto
def test_write_spreadsheet_upsert_creates_missing_file():
    """write_files_spreadsheet (locale) su path inesistente → CREA il file
    (upsert), non fallisce. Accomoda «metti/salva in uno spreadsheet» quando
    il foglio non esiste ancora."""
    import write_files_spreadsheet as WFS
    p = _tmp_xlsx("regr_upsert_missing.xlsx")
    if os.path.exists(p):
        os.remove(p)
    try:
        r = WFS.invoke({"spreadsheet_id": p, "values": [["a", "b"], [1, 2]]})
        assert r.get("ok") is True and r.get("created") is True, r
        assert os.path.isfile(p)
        # file creato → undo rimuove (delete_created_paths)
        assert r.get("_undo", {}).get("reverse_pattern") == "delete_created_paths"
    finally:
        if os.path.exists(p):
            os.remove(p)


def test_write_spreadsheet_entries_columns_real_data():
    """find→spreadsheet: passando `entries` (record) + `columns` (campi), il
    foglio contiene i DATI VERI, non i placeholder `${stepN...}` che il planner
    metteva nelle celle (non sa mappare una lista in una matrice). Sinonimo
    descrizione->description risolto."""
    import write_files_spreadsheet as WFS
    import read_files_spreadsheet as RFS
    p = _tmp_xlsx("regr_entries_cols.xlsx")
    if os.path.exists(p):
        os.remove(p)
    try:
        entries = [{"path": "/img/A.jpg", "description": "persone in montagna", "score": 1.7},
                   {"path": "/img/B.jpg", "description": "escursionisti"}]
        r = WFS.invoke({"spreadsheet_id": p, "entries": entries,
                        "columns": ["path", "descrizione"]})
        assert r.get("ok") is True, r
        vals = RFS.invoke({"spreadsheet_id": p}).get("values")
        assert vals[0] == ["path", "descrizione"], vals
        assert vals[1] == ["/img/A.jpg", "persone in montagna"], vals[1]
        assert vals[2][0] == "/img/B.jpg", vals[2]
        # nessuna cella deve contenere un placeholder non espanso
        flat = [str(c) for row in vals for c in row]
        assert not any("${" in c for c in flat), flat
    finally:
        if os.path.exists(p):
            os.remove(p)


def test_write_spreadsheet_id_hidden_from_planner():
    """Lock-in del fix d'istanza (2/6, da generalizzare nel verb-contract): per
    non far CHIEDERE l'id al planner (get_inputs che bloccava la query), il
    target `spreadsheet_id` deve essere runtime_resolved E assente dal SCOPO —
    altrimenti l'LLM lo richiede comunque (verificato empiricamente)."""
    import tomllib
    m = tomllib.load(open(_ROOT / "executors/write_files_spreadsheet/manifest.toml", "rb"))
    props = m["args"]["properties"]
    assert props["spreadsheet_id"].get("runtime_resolved") is True, "id non runtime_resolved"
    assert "spreadsheet_id" not in m["args"].get("required", []), "id non deve essere required"
    scopo_it = m["description"]["it"].split("PATTERN:")[0]
    assert "spreadsheet_id" not in scopo_it, "SCOPO cita spreadsheet_id → l'LLM lo chiede"


# --------------------------------------------- manifest_lint strutturale
def test_manifest_lint_catches_structural_traps():
    """Il linter becca le trappole strutturali (le ombre dei bug semantici):
    PATTERN con arg inventato, output-shape sbagliata, NON con tool morto,
    e — centrale — arg runtime_resolved citato nel testo visibile (l'LLM lo
    chiederebbe)."""
    from manifest_lint import lint_manifest
    trap = {
        "name": "write_files_trap", "affinity": ["x"],
        "description": {"it": (
            "SCOPO: scrive in uno spreadsheet via spreadsheet_id. "
            "PATTERN: write_files_trap(foo=\"x\", spreadsheet_id=\"y\"). "
            "NON: testo -> write_files_inesistente. OUT: ritorna entries.")},
        "args": {"properties": {
            "spreadsheet_id": {"type": "string", "runtime_resolved": True},
            "values": {"type": "array"}}},
    }
    checks = {f.check for f in lint_manifest(
        trap, language="it", catalog_names={"write_files"},
    )}
    assert "pattern_unknown_arg" in checks, checks   # foo inventato
    assert "runtime_arg_passed" in checks, checks    # spreadsheet_id passato
    assert "output_shape" in checks, checks           # write -> deve dire results
    assert "non_reference" in checks, checks          # write_files_inesistente morto


def test_synt_lint_gate_rejects_defective_synth():
    """Il gate synt (synt_multistage 'stage 5.5') assembla il manifest dagli
    stage output (name=s1, description=s4, args.properties=s2.args_properties) e
    rigetta su lint-error. Qui simulo l'assembly con un PATTERN che usa un arg
    inventato: il gate deve produrre un error 'pattern_args' (→ reject)."""
    from manifest_lint import lint_manifest
    s1 = {"name": "write_files_bad"}
    s2 = {"args_properties": {"values": {"type": "array"}}, "args_required": []}
    s4 = {"description": ("SCOPO: scrive righe. PATTERN: "
                          "write_files_bad(ghost=\"x\"). NON: testo -> write_files. "
                          "OUT: results."),
          "affinity": ["scrivi"]}
    man = {"name": s1["name"], "description": s4["description"],
           "affinity": s4.get("affinity") or [],
           "args": {"properties": s2["args_properties"],
                    "required": s2["args_required"]}}
    errs = [f for f in lint_manifest(
        man, language="it", allow_flat_description=True,
    ) if f.severity == "error"]
    assert any(f.check == "pattern_unknown_arg" for f in errs), errs


def test_touched_spreadsheet_manifests_pass_lint():
    """Lock-in: i manifest spreadsheet toccati stasera NON hanno error di lint
    (PATTERN visibile <260, nessun arg runtime_resolved citato, args validi)."""
    from manifest_lint import lint_file
    for name in ("create_files_spreadsheet", "write_files_spreadsheet",
                 "read_files_spreadsheet"):
        path = _ROOT / "executors" / name / "manifest.toml"
        errs = [
            f for f in lint_file(path, language="it")
            if f.severity == "error"
        ]
        assert not errs, f"{name}: {[str(e) for e in errs]}"


# --------------------------------------------- engine: from_dict robusto
def test_framework_from_dict_tolerates_malformed_llm_output():
    """Mētis: un candidate con step/filler emesso come STRINGA (output LLM
    malformato) NON deve far crashare il parse (prima: AttributeError 'str'
    has no attribute 'get' → planner a mani vuote)."""
    from engine.types import Framework
    # step come stringa + filler come stringa: ignorati, no crash
    fw = Framework.from_dict({
        "steps": ["find_files", {"tool": "delete_files", "args": {}}],
        "fillers": {"x": "non un oggetto", "y": {"prompt": "ok"}},
        "final_message": "done",
    })
    tools = [s.tool for s in fw.steps]
    assert tools == ["delete_files"], tools          # lo step-stringa scartato
    assert "y" in fw.fillers and "x" not in fw.fillers
    assert fw.final_message == "done"


# ---------------------------------------------------------------- direct runner
if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERR  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passati")
    sys.exit(1 if failed else 0)
