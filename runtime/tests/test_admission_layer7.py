"""Test del Layer 7 admission: anti-synth quando intent matcha imported skill
(ADR 0125 / 0114 5° gate, 12/5/2026).

Bug live 11/5/2026: PLANNER ha proposto synth di `read_appointments` e
`read_calendar` per la query «che appuntamenti ho domani» mentre `read_events`
(imported da google-workspace via skill_importer ADR 0123) gia' copriva
l'intent. Risultato: 119s+ di synt cascade bruciati invece di chiamare
direttamente l'executor canonico.

L7 = lookup tabellare deterministico (§7.9): scan al boot di
`~/.local/share/metnos/executors/_imports/`, parse `name = <verb>_<object>`,
costruisce tabella `(verb, object) → [imported_names]`. Sinonimi cross-lang
(appointments/appuntamenti → events) risolti via `canonical_object()`.

Match al lookup time = synth REJECTED con
`error="duplicates_imported_skill_<name>"` e redirect al canonical.
"""
from __future__ import annotations

import sys
import tomllib  # noqa: F401  (assicura disponibilita' nei test)
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


def _seed_imported_skill(root: Path, skill: str, name: str, *, verb_object: str | None = None):
    """Crea un manifest minimale in `root/_imports/<skill>/<name>/manifest.toml`.

    `verb_object`: se None, deriva da `name` (e.g. "set_events").
    Manifest scrive `name = "<name>"` + `[provenance].imported_from = ...`
    perche' L7 si fida del `name` per derivare verb+object.
    """
    ex_dir = root / "_imports" / skill / name
    ex_dir.mkdir(parents=True, exist_ok=True)
    (ex_dir / f"{name}.py").write_text(
        "def invoke(args):\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    manifest = (
        'manifest_format = "1.0"\n'
        f'name = "{name}"\n'
        'version = "0.1.0"\n'
        '[description]\n'
        f'it = "stub imported {name}"\n'
        f'en = "stub imported {name}"\n'
        'affinity = []\n'
        'lifecycle = "active"\n'
        '\n'
        '[code]\n'
        f'files = ["{name}.py"]\n'
        'digest = "sha256:placeholder"\n'
        '\n'
        '[args]\n'
        'type = "object"\n'
        'required = []\n'
        '\n'
        '[provenance]\n'
        f'imported_from = "agentskills.io/local/{skill}"\n'
    )
    (ex_dir / "manifest.toml").write_text(manifest, encoding="utf-8")
    return ex_dir


@pytest.fixture
def isolated_imports(tmp_path, monkeypatch):
    """Redirige `_imports_root()` di vocab.py a una tmp dir vuota.
    Reset cache imported_bindings_index per ogni test."""
    import vocab
    # Override l'helper _imports_root() (Path Home-based) con tmp_path.
    fake_root = tmp_path / "executors"
    fake_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(vocab, "_imports_root", lambda: fake_root / "_imports")
    # Reset cache (le altre suite hanno gia' un index in memoria).
    vocab.invalidate_imported_bindings_cache()
    yield fake_root
    vocab.invalidate_imported_bindings_cache()


# ── 1. Discovery at boot ─────────────────────────────────────────────────

class TestBootDiscovery:
    """imported_bindings_index() scopre i manifest in _imports/ al boot."""

    def test_empty_imports_root_returns_empty_index(self, isolated_imports):
        import vocab
        idx = vocab.imported_bindings_index()
        assert idx == {}

    def test_discovers_single_imported_executor(self, isolated_imports):
        import vocab
        _seed_imported_skill(isolated_imports, "google-workspace", "set_events")
        # Forza re-scan
        vocab.invalidate_imported_bindings_cache()
        idx = vocab.imported_bindings_index()
        assert ("set", "events") in idx
        assert idx[("set", "events")] == ["set_events"]

    def test_discovers_multiple_skills_multiple_executors(self, isolated_imports):
        import vocab
        _seed_imported_skill(isolated_imports, "google-workspace", "set_events")
        _seed_imported_skill(isolated_imports, "google-workspace", "read_events")
        _seed_imported_skill(isolated_imports, "google-workspace", "delete_events")
        _seed_imported_skill(isolated_imports, "slack", "send_messages")
        vocab.invalidate_imported_bindings_cache()
        idx = vocab.imported_bindings_index()
        # 3 (verb, events) keys + 1 (verb, messages)
        assert len(idx) == 4
        assert idx[("set", "events")] == ["set_events"]
        assert idx[("read", "events")] == ["read_events"]
        assert idx[("delete", "events")] == ["delete_events"]
        assert idx[("send", "messages")] == ["send_messages"]


# ── 2. Canonical object resolution via synonyms ─────────────────────────

class TestCanonicalObject:
    """canonical_object() risolve sinonimi IT+EN verso OBJECTS canonici."""

    def test_appointments_to_events(self):
        from vocab import canonical_object
        assert canonical_object("appointments") == "events"
        assert canonical_object("appointment") == "events"
        assert canonical_object("appuntamenti") == "events"
        assert canonical_object("appuntamento") == "events"

    def test_calendar_to_events(self):
        from vocab import canonical_object
        assert canonical_object("calendar") == "events"
        assert canonical_object("calendario") == "events"
        assert canonical_object("schedule") == "events"
        assert canonical_object("agenda") == "events"

    def test_direct_object_passthrough(self):
        from vocab import canonical_object
        assert canonical_object("events") == "events"
        assert canonical_object("messages") == "messages"

    def test_unknown_returns_none(self):
        from vocab import canonical_object
        assert canonical_object("xyz123") is None
        assert canonical_object(None) is None
        assert canonical_object("") is None


# ── 3. L7 admission: 6 case con synth_request ───────────────────────────

class TestL7Admission:
    """6 case che coprono il decision tree di L7."""

    def _patch_catalog(self, monkeypatch, executors_in_catalog=None):
        """Patch load_catalog per ritornare un catalog con solo gli executor
        passati (vuoto = nessuno; usato per evitare branch already_in_catalog
        e canonical_alias)."""
        from loader import Catalog
        cat = Catalog()
        if executors_in_catalog:
            # Stub minimale, solo nomi necessari per il branch test.
            from loader import Executor
            for name in executors_in_catalog:
                cat.executors[name] = Executor(
                    name=name,
                    version="0.0.0",
                    description="",
                    affinity=[],
                    args_schema={},
                    capabilities=[],
                    tests=[],
                    code_path=None,
                    manifest_path=Path(f"/tmp/stub/{name}/manifest.toml"),
                    signed_by="(test)",
                )
        monkeypatch.setattr("synth_request.load_catalog", lambda *a, **kw: cat,
                              raising=False)
        # synth_request importa load_catalog dentro la funzione, dobbiamo
        # patchare anche `loader.load_catalog`.
        import loader
        monkeypatch.setattr(loader, "load_catalog", lambda *a, **kw: cat)
        return cat

    def test_case1_synth_duplicates_imported_rejected(self, isolated_imports, monkeypatch):
        """Synth duplicate: PLANNER chiede `read_events`, imported gia' c'e'.
        Expected: redirect immediato senza synt cascade."""
        import vocab
        _seed_imported_skill(isolated_imports, "google-workspace", "read_events")
        vocab.invalidate_imported_bindings_cache()

        # Catalog vuoto (per evitare already_in_catalog branch).
        self._patch_catalog(monkeypatch, [])

        from synth_request import handle_synth_request
        # L'expected_name e' read_events, ma il catalog (mocked vuoto) NON ce
        # l'ha → entriamo nella L7 path.
        res = handle_synth_request(
            {"expected_name": "read_events", "intent": "leggi calendario"},
            user_query="che appuntamenti ho oggi",
        )
        assert res["ok"] is True
        assert res.get("l7_admission") is True
        assert res.get("synthesized") is False
        assert res.get("redirected") is True
        assert res.get("name") == "read_events"
        assert "duplicates_imported_skill_read_events" in res.get("error", "")

    def test_case2_synth_disjoint_no_block(self, isolated_imports, monkeypatch):
        """Synth disjoint: imported = set_events, synth chiede compute_lines.
        Expected: L7 NON blocca, synt cascade procede (mock multistage)."""
        import vocab
        _seed_imported_skill(isolated_imports, "google-workspace", "set_events")
        vocab.invalidate_imported_bindings_cache()

        self._patch_catalog(monkeypatch, [])

        # Patch multistage_run_full per evitare la cascata reale; assertion
        # principale: L7 NON ha bloccato (il flusso arriva al call).
        called = {"multistage": False}

        def fake_multistage(intent, llm_m, llm_w, progress=None):
            called["multistage"] = True
            # Ritorna un oggetto run-like minimale
            class FakeRun:
                final_state = "abandoned"
                name = "compute_lines_of_code"
                abandon_reason = "stub: no real LLM"
                stages = []
                code_text = ""
            return FakeRun()

        monkeypatch.setattr("synth_request.multistage_run_full", fake_multistage)

        from synth_request import handle_synth_request
        res = handle_synth_request(
            {"expected_name": "compute_lines_of_code",
             "intent": "Compute LOC across a directory"},
            user_query="conta righe codice",
        )
        # L7 non blocca → multistage e' stato chiamato.
        assert called["multistage"] is True
        assert res.get("l7_admission") is not True

    def test_case3_synth_same_verb_diff_object(self, isolated_imports, monkeypatch):
        """Synth chiede `read_files` (object=files), imported = read_events.
        Stesso verb ma object diverso → L7 NON deve bloccare."""
        import vocab
        _seed_imported_skill(isolated_imports, "google-workspace", "read_events")
        vocab.invalidate_imported_bindings_cache()

        self._patch_catalog(monkeypatch, [])

        called = {"multistage": False}
        def fake_multistage(intent, llm_m, llm_w, progress=None):
            called["multistage"] = True
            class FakeRun:
                final_state = "abandoned"; name = "read_files"
                abandon_reason = "stub"; stages = []; code_text = ""
            return FakeRun()
        monkeypatch.setattr("synth_request.multistage_run_full", fake_multistage)

        from synth_request import handle_synth_request
        res = handle_synth_request(
            {"expected_name": "read_files", "intent": "leggi file di testo"},
            user_query="leggi questo file",
        )
        # L7 non blocca: object=files != events
        assert called["multistage"] is True
        assert res.get("l7_admission") is not True

    def test_case4_multi_skill_same_intent_multiple_alternatives(
            self, isolated_imports, monkeypatch):
        """Due skill diversi importano entrambi `set_events`-equivalenti.
        Expected: L7 ritorna lista di alternative."""
        import vocab
        _seed_imported_skill(isolated_imports, "google-workspace", "set_events")
        # Secondo skill alternativo: SET di EVENTS via outlook.
        _seed_imported_skill(isolated_imports, "outlook", "set_events_outlook")
        # NB: set_events_outlook → parts = (set, events, outlook), L7 mappa
        # via (verb=set, object=events). Cosi' i due cadono insieme.
        vocab.invalidate_imported_bindings_cache()

        self._patch_catalog(monkeypatch, [])

        from synth_request import handle_synth_request
        res = handle_synth_request(
            {"expected_name": "set_events", "intent": "crea evento"},
            user_query="aggiungi un appuntamento",
        )
        assert res.get("l7_admission") is True
        alts = res.get("imported_alternatives", [])
        assert "set_events" in alts
        assert "set_events_outlook" in alts
        # `name` deve essere il primo deterministico (ordinato).
        assert res.get("name") == sorted(alts)[0]

    def test_case5_imported_pre_existing_already_in_catalog_short_circuit(
            self, isolated_imports, monkeypatch):
        """Caso boundary: l'imported `read_events` e' anche nel catalog (caso
        live: skill installati). Branch `already_in_catalog` deve vincere
        PRIMA di L7 (e' una optimization piu' veloce)."""
        import vocab
        _seed_imported_skill(isolated_imports, "google-workspace", "read_events")
        vocab.invalidate_imported_bindings_cache()

        # Catalog HA read_events
        self._patch_catalog(monkeypatch, ["read_events"])

        from synth_request import handle_synth_request
        res = handle_synth_request(
            {"expected_name": "read_events", "intent": "leggi calendario"},
            user_query="che appuntamenti",
        )
        # already_in_catalog branch vince
        assert res.get("already_in_catalog") is True
        assert res.get("name") == "read_events"
        # L7 non viene esercitato (no l7_admission flag)
        assert res.get("l7_admission") is not True

    def test_case6_boot_discovery_caches_correctly(self, isolated_imports):
        """Test della logica di cache invalidation di imported_bindings_index().
        Aggiungere un manifest DOPO il primo lookup deve essere visto solo
        dopo invalidate (o quando mtime cambia)."""
        import vocab
        # Primo lookup: vuoto
        idx0 = vocab.imported_bindings_index()
        assert idx0 == {}
        # Aggiungi un manifest
        _seed_imported_skill(isolated_imports, "google-workspace", "set_events")
        # Forza invalidazione (cache mtime-based avrebbe gia' colpito,
        # ma il test ha mtime fissi → invalidate esplicito)
        vocab.invalidate_imported_bindings_cache()
        idx1 = vocab.imported_bindings_index()
        assert ("set", "events") in idx1
        # Senza invalidate, secondo lookup ritorna lo stesso oggetto (cache)
        idx2 = vocab.imported_bindings_index()
        assert idx2 is idx1  # stesso oggetto in memoria (cached)


# ── 4. Bug live case ─────────────────────────────────────────────────────

class TestBugLive11May:
    """Replica esatta del bug live 11/5/2026: query «appuntamenti domani»,
    PLANNER chiede `read_appointments` → L7 deve redirect a `read_events`.
    """

    def test_read_appointments_redirected_to_read_events(self, isolated_imports, monkeypatch):
        import vocab
        _seed_imported_skill(isolated_imports, "google-workspace", "read_events")
        vocab.invalidate_imported_bindings_cache()

        from loader import Catalog
        empty_cat = Catalog()
        monkeypatch.setattr("synth_request.load_catalog",
                              lambda *a, **kw: empty_cat, raising=False)
        import loader as _loader
        monkeypatch.setattr(_loader, "load_catalog", lambda *a, **kw: empty_cat)

        from synth_request import handle_synth_request
        res = handle_synth_request(
            {"expected_name": "read_appointments",
             "intent": "Legge gli appuntamenti del calendario per una data"},
            user_query="che appuntamenti ho domani",
        )
        # L7 redirect a read_events (canonical_object: appointments → events)
        assert res.get("l7_admission") is True
        assert res.get("name") == "read_events"
        assert res.get("expected_name") == "read_appointments"
