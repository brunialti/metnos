#!/usr/bin/env python3
"""Test del daemon detection_translate_pending (offline, LLM mockato).

Verifica il meccanismo "aggiungi lingua -> auto-traduzione" senza GPU:
  - enqueue di una lingua nuova marca ogni concept pending;
  - il daemon traduce phrases/mapping via LLM (mock) e salva forme native;
  - i concept regex sono SALTATI (authoring manuale) e restano pending;
  - dopo il drenaggio, la copertura della lingua = tutto TRANNE i regex.
DB isolato in tmp (no pollution del detection.sqlite reale).
"""
import json
import sys
from pathlib import Path

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

import detection_lexicon as dl  # noqa: E402


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "DB_PATH", tmp_path / "detection.sqlite")
    monkeypatch.setattr(dl, "_conn", None)
    monkeypatch.setattr(dl, "_seeded", False)
    dl._cache.clear()
    dl._regex_cache.clear()
    monkeypatch.setenv("METNOS_DETECTION_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("METNOS_DETECTION_CAP_PER_FIRE", "100")
    dl.ensure_seeded()


def _install_mock_llm(monkeypatch):
    import llm_helpers

    def fake(prompt, sysp, tier=None, max_tokens=0, temperature=0.0):
        # Mock robusto: non dipende dal parsing del prompt (verifica il
        # daemon, non la lingua). Mapping -> stesse chiavi canoniche, forme
        # distinte: una risposta parziale non rappresenta copertura.
        if "Source map (JSON)" in prompt:
            source = json.loads(
                prompt.split("Source map (JSON): ", 1)[1]
                .split("\nOutput exactly:", 1)[0]
            )
            translated = {
                key: [f"X_{index}_{key}"]
                for index, key in enumerate(source)
            }
            return json.dumps({"mapping": translated}), {"model": "mock"}
        return json.dumps({"forms": ["X_a", "X_b"]}), {"model": "mock"}

    monkeypatch.setattr(llm_helpers, "call_llm", fake)


def _regex_concepts():
    out = set()
    for c in dl.registered_concepts():
        nat = dl._native(c, "en") or dl._native(c, "it")
        if nat and nat[0] == "regex":
            out.add(c)
    return out


def _non_auto_traducibili():
    """Concetti che il daemon NON deve tradurre da solo.

    Due categorie, per due ragioni diverse: i `regex`, perche' un pattern
    sbagliato e' peggio di un buco; e i concetti che governano un CONSENSO,
    perche' li' una forma inventata non produce un mancato riconoscimento ma
    un si' che l'utente non ha detto.
    """
    return _regex_concepts() | set(dl.manual_review_concepts())


def _drain_translatable_pending(task, *, max_fires=20):
    """Run bounded fires until only manual-review resources remain pending."""
    results = []
    for _ in range(max_fires):
        result = task()
        results.append(result)
        assert result["ok"]
        if result["metadata"]["pending_seen"] == 0:
            return results
    pytest.fail("detection translation queue did not converge")


def test_enqueue_marks_all_pending(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    n = dl.enqueue_language("zz")
    assert n == len(dl.registered_concepts())
    assert len(dl.list_pending(limit=1000)) == n


def test_daemon_translates_phrases_and_mapping(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    _install_mock_llm(monkeypatch)
    dl.enqueue_language("zz")
    from jobs.detection_translate_pending import task_detection_translate_pending
    results = _drain_translatable_pending(task_detection_translate_pending)
    res = results[-1]
    saltati = _non_auto_traducibili()
    non_regex = [c for c in dl.registered_concepts() if c not in saltati]
    untranslated = [c for c in non_regex if not dl.has_native(c, "zz")]
    # tradotti = auto-traducibili; trattenuti = regex + revisione manuale
    translated = sum(item["metadata"]["translated"] for item in results)
    assert translated == len(non_regex), \
        f"untranslated non-regex: {untranslated}; meta={res['metadata']}"
    # Le righe intraducibili sono escluse in SQL (17/8): non entrano piu' nella
    # finestra, quindi `skipped_*` — che conta cio' che il giro ha LETTO e
    # messo da parte — resta a zero, e il conto vive in `held_*`. Le due cose
    # restano distinte: una finestra piena di righe da tradurre e una finestra
    # piena di righe che nessun modello puo' toccare non sono lo stesso esito.
    trattenuti = (
        res["metadata"]["held_regex"]
        + res["metadata"]["held_manual_review"]
    )
    assert trattenuti == len(saltati), res["metadata"]
    assert res["metadata"]["held_manual_review"] == len(
        set(dl.manual_review_concepts()) - _regex_concepts())
    assert res["metadata"]["held_consent"] == 2
    assert res["metadata"]["skipped_regex"] == 0


def test_coverage_after_translation_only_regex_missing(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    _install_mock_llm(monkeypatch)
    dl.enqueue_language("zz")
    from jobs.detection_translate_pending import task_detection_translate_pending
    _drain_translatable_pending(task_detection_translate_pending)
    cov = dl.verify_coverage("zz")
    assert set(cov["missing"]) == _non_auto_traducibili()
    # un concept phrases/mapping ora ha forme native nella lingua nuova
    assert dl.has_native("notify.request", "zz")
    assert dl.has_native("notify.channel", "zz")


def test_regex_not_autogenerated(tmp_path, monkeypatch):
    """I regex restano pending (no pattern auto-generato fragile, §2.8)."""
    _fresh(tmp_path, monkeypatch)
    _install_mock_llm(monkeypatch)
    dl.enqueue_language("zz")
    from jobs.detection_translate_pending import task_detection_translate_pending
    task_detection_translate_pending()
    for c in _regex_concepts():
        assert not dl.has_native(c, "zz")


def test_mapping_parziale_o_ambiguo_non_viene_accettato(monkeypatch):
    """Il traduttore non puo' dichiarare coperto un mapping amputato."""
    import llm_helpers
    from jobs import detection_translate_pending as job

    replies = iter((
        {"mapping": {"read": ["lire"]}},
        {"mapping": {"read": ["faire"], "run": ["faire"]}},
    ))
    monkeypatch.setattr(
        llm_helpers,
        "call_llm",
        lambda *args, **kwargs: (json.dumps(next(replies)), {"model": "mock"}),
    )
    localized, _meta = job._llm_localize(
        "test.mapping", "mapping",
        {"read": ["read"], "run": ["run"]}, "fr", "en", "wise",
    )
    assert localized is None


def test_failed_first_row_does_not_starve_later_pending_work(
    tmp_path, monkeypatch,
):
    from jobs import detection_translate_pending as job

    monkeypatch.setattr(dl, "DB_PATH", tmp_path / "rotation.sqlite")
    monkeypatch.setattr(dl, "_conn", None)
    monkeypatch.setattr(dl, "_seeded", True)
    monkeypatch.setattr(dl, "_declared_review_policies", {})
    monkeypatch.setattr(dl, "_declared_baseline_languages", {})
    monkeypatch.setenv("METNOS_DETECTION_CAP_PER_FIRE", "1")
    monkeypatch.setenv("METNOS_DETECTION_AUDIT_DIR", str(tmp_path / "audit"))
    for concept in ("a.bad", "b.good"):
        dl.register(concept, "phrases", en=["source"], it=["sorgente"])
        dl.mark_for_translation(concept, "zz")

    monkeypatch.setattr(
        job, "_llm_localize",
        lambda concept, *_args, **_kwargs: (
            (None, {}) if concept == "a.bad"
            else (["translated"], {"model": "mock"})
        ),
    )

    first = job.task_detection_translate_pending()
    second = job.task_detection_translate_pending()

    assert first["error_count"] == 1
    assert second["ok_count"] == 1
    assert not dl.has_native("a.bad", "zz")
    assert dl.has_native("b.good", "zz")


def test_i_concetti_di_consenso_non_si_traducono_da_soli(monkeypatch):
    """`confirm.*` decidono se un consenso e' stato dato.

    Una forma inventata da un modello e mai riletta da nessuno non produce qui
    un mancato riconoscimento: produce un si' che l'utente non ha detto. Il
    daemon li salta e li lascia a una persona. Nel frattempo l'unione con it/en
    tiene utilizzabile la lingua nuova, perche' «ok», «yes», «no» e «stop»
    sono prestiti che si scrivono quasi ovunque.

    Prima del 16/8/2026 il caso non si poneva: erano `kind="regex"` e il
    daemon saltava tutti i regex. Passandoli a `phrases` sono entrati nel
    perimetro della traduzione automatica, e questo gate e' cio' che li
    riporta fuori.
    """
    from jobs import detection_translate_pending as _job

    chiamate = []

    def _mai(*a, **k):
        chiamate.append(a)
        return None, {}

    monkeypatch.setattr(_job, "_llm_localize", _mai)

    riga = {"concept": "confirm.yes", "target_lang": "de", "kind": "phrases",
            "source_lang": "it", "source_payload": '["si"]'}

    def _finto_list_pending(limit=0, exclude_concepts=(), exclude_kinds=()):
        """Riproduce l'esclusione in SQL: chi filtra a monte non vede la riga.

        La riga resta visibile alla chiamata SENZA esclusioni, che e' quella
        con cui il job conta i trattenuti."""
        if riga["concept"] in exclude_concepts or riga["kind"] in exclude_kinds:
            return []
        return [dict(riga)]

    monkeypatch.setattr(_job._dl, "list_pending", _finto_list_pending)
    esito = _job.task_detection_translate_pending()
    assert chiamate == [], "il modello NON deve essere interpellato"
    assert esito["metadata"]["held_manual_review"] >= 1, esito["metadata"]


def test_manual_review_boundary_failure_aborts_before_model(monkeypatch):
    """A missing safety policy is an error, never an empty allow-all set."""
    from jobs import detection_translate_pending as _job

    model_calls = []
    pending_calls = []
    monkeypatch.setattr(
        _job._dl, "manual_review_concepts",
        lambda: (_ for _ in ()).throw(RuntimeError("policy unavailable")),
    )
    monkeypatch.setattr(
        _job._dl, "list_pending",
        lambda *args, **kwargs: pending_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        _job, "_llm_localize",
        lambda *args, **kwargs: model_calls.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="policy unavailable"):
        _job.task_detection_translate_pending()
    assert pending_calls == []
    assert model_calls == []


def test_il_backstop_nel_ciclo_regge_se_l_esclusione_non_arriva(monkeypatch):
    """Doppia difesa: se la riga di consenso entra comunque nella finestra —
    una `list_pending` che ignora le esclusioni, come faceva la vecchia — il
    controllo dentro il ciclo la ferma lo stesso. L'esclusione in SQL e' per
    non sprecare la finestra, non e' cio' che protegge il consenso."""
    from jobs import detection_translate_pending as _job

    chiamate = []
    monkeypatch.setattr(_job, "_llm_localize",
                        lambda *a, **k: (chiamate.append(a), (None, {}))[1])
    monkeypatch.setattr(
        _job._dl, "list_pending",
        lambda limit=0, exclude_concepts=(), exclude_kinds=(): [
            {"concept": "confirm.no", "target_lang": "de", "kind": "phrases",
             "source_lang": "it", "source_payload": '["no"]'},
        ])
    esito = _job.task_detection_translate_pending()
    assert chiamate == [], "il modello NON deve essere interpellato"
    assert esito["metadata"]["skipped_regex"] >= 1, esito["metadata"]
