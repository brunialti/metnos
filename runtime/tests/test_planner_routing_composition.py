"""Test routing PLANNER per query «soggetto + composizione» (Mini-PR3.5).

Il prefilter deterministico DEVE rankare `find_persons_indices` come top-1
sulle query di tipo «primo piano di X», «ritratto di X», «mezzo busto di X»
quando X e' una persona. Questo evita la pipeline a 2 step (W) e abilita la
regola single-step (W.bis) del PLANNER prompt.

Regression: query «X al mare» (composizione SCENE, non inquadratura) DEVE
restare candidata alla pipeline 2-step (W), quindi `find_images_indices` deve
restare presente nel pool.

Test deterministici sull'affinity-prefilter (`prefilter.rank` /
`prefilter.affinity_score`). NESSUN LLM coinvolto.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


# ── Helpers ────────────────────────────────────────────────────────────

def _load_real_catalog():
    """Carica il catalog reale dal path canonico (verify=False, no synth).

    Pinniamo `executors_dir=<install_root>/executors` e invalidiamo la cache
    perche' altri test del suite fanno monkeypatch di
    `DEFAULT_EXECUTORS_DIR`/`config.DEFAULT_LANG`/lifecycle filter (cfr.
    commento in test_run_turn_reference_images.py): senza pin, l'ordine di
    esecuzione cambia il catalog visibile e il prefilter score.

    Inoltre `test_introvertive_loop_stress::test_archived_executor_excluded`
    inserisce `register(...)` nel DB executor_aging globale (env restored ma
    DB-state lascia tracce sui successivi `lifecycle_override_map()` se
    rimane il path default). Per isolarci, ricarichiamo a mano i manifest
    senza passare dal layer `executor_aging`: usiamo il loader interno
    `_load_dir_into_catalog` che SCARTA solo i digest mismatch.
    """
    from loader import Catalog, _load_dir_into_catalog
    cat = Catalog()
    _load_dir_into_catalog(Path(__file__).resolve().parents[2] / "executors", cat,
                           verify=False, is_synthesized=False)
    return list(cat.executors.values())


def _score_for(query: str, executor):
    """Affinity-prefilter score deterministico per (query, executor)."""
    from prefilter import (affinity_score, detect_canonical_object,
                           detect_canonical_verb, tokenize)
    qtokens = tokenize(query)
    cverb = detect_canonical_verb(qtokens)
    cobj = detect_canonical_object(qtokens, query)
    return affinity_score(qtokens, executor,
                          query_canonical_verb=cverb,
                          query_canonical_object=cobj)


def _rank_top(query: str, k: int = 5):
    """Ritorna i top-k executor names per query (rank legacy, deterministico)."""
    from prefilter import rank
    cat = _load_real_catalog()
    top = rank(query, cat, k=k, min_score=1)
    return [e.name for e in top]


# ── 1. «primo piano di X» → find_persons_indices top-1 ─────────────────

class TestPrimoPianoRouting:
    def test_primo_piano_di_X_picks_find_persons_indices(self):
        """«primo piano di carol» → find_persons_indices top-1 per affinity.

        La nuova affinity «primo piano», «primo piano di», «ritratto»,
        «ravvicinata» deve garantire che find_persons_indices vinca su
        find_images_indices anche se entrambi matchano «foto»/«cerca».
        """
        top = _rank_top("primo piano di carol", k=5)
        assert top, "rank ritorna lista vuota"
        assert top[0] == "find_persons_indices", (
            f"Atteso find_persons_indices top-1, ottenuto {top[0]} (top5={top})"
        )

    def test_primo_piano_score_beats_find_images_indices(self):
        """Confronto numerico: score(find_persons) > score(find_images) per
        «primo piano di carol». Garantisce che il boost di affinity sia
        sufficiente anche se find_images_indices ha "foto"/"cerca" generici.
        """
        cat = {e.name: e for e in _load_real_catalog()}
        if "find_persons_indices" not in cat or "find_images_indices" not in cat:
            pytest.skip("executor non in catalog (env minimale)")
        q = "primo piano di carol"
        s_persons = _score_for(q, cat["find_persons_indices"])
        s_images = _score_for(q, cat["find_images_indices"])
        assert s_persons > s_images, (
            f"find_persons_indices ({s_persons}) DEVE battere "
            f"find_images_indices ({s_images}) su «{q}»"
        )

    def test_close_up_english_picks_find_persons_indices(self):
        """Variante inglese: «close-up of carol» → find_persons_indices top-1."""
        top = _rank_top("close-up of carol", k=5)
        assert top
        assert top[0] == "find_persons_indices", (
            f"Atteso find_persons_indices top-1 (close-up EN), ottenuto top5={top}"
        )


# ── 2. «ritratto di X» → find_persons_indices top-1 ────────────────────

class TestRitrattoRouting:
    def test_ritratto_di_X_routes_to_find_persons_indices(self):
        """«ritratto di Carol» → find_persons_indices top-1."""
        top = _rank_top("ritratto di Carol", k=5)
        assert top
        assert top[0] == "find_persons_indices", (
            f"Atteso find_persons_indices top-1 (ritratto), ottenuto top5={top}"
        )

    def test_portrait_english_routes_to_find_persons_indices(self):
        """«portrait of Carol» → find_persons_indices top-1."""
        top = _rank_top("portrait of Carol", k=5)
        assert top
        assert top[0] == "find_persons_indices", (
            f"Atteso find_persons_indices top-1 (portrait EN), ottenuto top5={top}"
        )

    def test_mezzo_busto_routes_to_find_persons_indices(self):
        """«mezzo busto di Carol» → find_persons_indices top-1."""
        top = _rank_top("mezzo busto di Carol", k=5)
        assert top
        assert top[0] == "find_persons_indices", (
            f"Atteso find_persons_indices top-1 (mezzo busto), ottenuto top5={top}"
        )


# ── 3. Regression (W) — «X al mare» resta a pipeline 2-step ────────────

class TestPipelineSceneRegression:
    def test_X_al_mare_keeps_find_images_indices_in_pool(self):
        """«carol al mare» (scene) → find_images_indices DEVE restare nel
        top-K (regola W: pipeline 2-step persons→scene). Non deve essere
        scartato dalle nuove affinity W.bis.
        """
        top = _rank_top("carol al mare", k=8)
        assert "find_images_indices" in top, (
            f"find_images_indices DEVE restare in top-K per «carol al mare» "
            f"(pipeline scene). Top8={top}"
        )

    def test_X_al_mare_does_not_explode_find_persons_score(self):
        """«carol al mare» NON deve far esplodere find_persons_indices score:
        l'affinity «primo piano»/«ritratto» NON matcha «mare», quindi il
        boost W.bis NON si attiva. find_persons_indices resta candidato (per
        il nome «carol» in affinity), ma find_images_indices DEVE restare
        comparabile (tipico: entrambi nel top-K).
        """
        cat = {e.name: e for e in _load_real_catalog()}
        if "find_persons_indices" not in cat or "find_images_indices" not in cat:
            pytest.skip("executor non in catalog (env minimale)")
        q = "carol al mare"
        s_persons = _score_for(q, cat["find_persons_indices"])
        s_images = _score_for(q, cat["find_images_indices"])
        # Entrambi positivi: il prefilter deve mantenere coppia di candidati
        # cosi' il PLANNER puo' applicare la regola W (pipeline 2-step).
        assert s_persons > 0, f"find_persons_indices score atteso > 0, ottenuto {s_persons}"
        assert s_images > 0, f"find_images_indices score atteso > 0, ottenuto {s_images}"


# ── 4. Sanity: affinity tokens presenti nel manifest ───────────────────

class TestManifestAffinity:
    def test_manifest_contains_composition_keywords(self):
        """Sanity check: il manifest di find_persons_indices contiene le
        affinity «primo piano», «ritratto», «close-up», «portrait», ecc.
        introdotte nel Mini-PR3.5.
        """
        cat = {e.name: e for e in _load_real_catalog()}
        if "find_persons_indices" not in cat:
            pytest.skip("find_persons_indices non in catalog")
        aff = {a.lower() for a in cat["find_persons_indices"].affinity}
        for tag in ("primo piano", "ritratto", "ravvicinata",
                    "close-up", "portrait", "mezzo busto", "half-bust"):
            assert tag in aff, f"affinity «{tag}» mancante in find_persons_indices"

    def test_description_mentions_min_face_pixels_uso_corretto(self):
        """USO CORRETTO della description IT/EN cita `min_face_pixels` come
        modificatore di composizione (single-step, no pipeline).
        """
        cat = {e.name: e for e in _load_real_catalog()}
        if "find_persons_indices" not in cat:
            pytest.skip("find_persons_indices non in catalog")
        desc = cat["find_persons_indices"].description.lower()
        assert "min_face_pixels" in desc, (
            "description deve menzionare min_face_pixels come modificatore "
            "di composizione"
        )
        # Il claim "SINGLE step" o "single step" deve apparire (IT o EN).
        assert "single step" in desc or "single-step" in desc, (
            "description deve dichiarare 'SINGLE step' (no pipeline 2-step)"
        )
