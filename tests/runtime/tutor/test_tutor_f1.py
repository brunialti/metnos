from __future__ import annotations

import asyncio
import hashlib
import os
from types import SimpleNamespace

import numpy as np
import pytest

from tutor.cards import load_published
from tutor.compose import Composition
from tutor.detect import classify
from tutor.models import TutorPrincipal, TutorRequest
from tutor.mode import ModeDecision
from tutor.semantic import (
    SemanticContext, SemanticMatch, SourceHit, retrieve, retrieve_sources,
)
from tutor.sources import (
    KnowledgeUnit, _resolve_language_paths, build_knowledge_units,
)
from tutor.service import answer_request
from tutor_boundary import http_principal, telegram_principal


# Tranche 1 del ritiro (25/7): console-proposte e dispositivi-accoppiamento
# sono fuori da published/ — le loro query sono servite da F2 (unita' tipizzate
# e documenti). Qui restano solo le schede superstiti.
CANONICAL = {
    "Come faccio a creare un task che legga le mie email?":
        "attivita-programmate",
    "Cosa sai fare su GitHub?": "github-capabilities",
    "Come faccio una ricerca su archivi di foto?": "fotografie-dominio",
}


def _principal(audience: str = "instance_admin") -> TutorPrincipal:
    return TutorPrincipal(
        user_id="u1", actor="host", audience=audience,
        channel="http", conversation_id="c1",
    )


def _patch_request_snapshot(monkeypatch, *, cards=(), units=(),
                            version="sha256:test-catalog"):
    """Install one internally consistent catalog generation for service tests."""

    monkeypatch.setattr(
        "tutor.catalog.load_request_snapshot",
        lambda: SimpleNamespace(
            version=version,
            cards=tuple(cards),
            units=tuple(units),
            card_index=None,
            knowledge_index=None,
        ),
    )


def _route(monkeypatch, card_id: str | None, *, compose: bool = True,
           mode: str = "EXPLAIN"):
    cards = load_published()
    _patch_request_snapshot(monkeypatch, cards=cards)
    context = None
    if card_id:
        card = next(item for item in cards if item.card_id == card_id)
        context = SemanticContext((SourceHit(
            source_type="card", source_id=card.card_id, lang="it",
            score=0.9, card=card,
        ),), top_score=0.9)

    def selected(_query, _lang, audience, **_kwargs):
        if context and not context.hits[0].card.visible_to(audience):
            return SemanticContext((), 0.9, restricted=True)
        return context

    monkeypatch.setattr("tutor.service.retrieve_sources", selected)
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_a, **_k: ModeDecision(mode, True))
    if compose:
        from tutor.compose import Composition
        monkeypatch.setattr(
            "tutor.compose.compose_answer",
            lambda **kwargs: Composition("answer", kwargs["context"]),
        )


def _real_index():
    from tutor.catalog import VectorIndex, _embed_cards

    rows, dimension, fingerprint = _embed_cards(load_published())
    return VectorIndex(
        refs=tuple((row[0], row[1]) for row in rows),
        matrix=np.stack([
            np.frombuffer(row[2], dtype="<f4").astype(np.float32)
            for row in rows
        ]),
        dimension=dimension,
        fingerprint=fingerprint,
    )


def _fake_embedder():
    class Embedder:
        def embed_texts(self, texts):
            matrix = np.zeros((len(texts), 8), dtype=np.float32)
            for index, _text in enumerate(texts):
                matrix[index, index % 8] = 1.0
            return matrix

        def embed_query(self, text):
            return self.embed_texts([text])[0]
    return Embedder()


def _patch_catalog_paths(monkeypatch, tmp_path):
    import sign
    from tutor import catalog

    data = tmp_path / "data"
    state = tmp_path / "state"
    data.mkdir()
    state.mkdir()
    monkeypatch.setattr(sign, "KEYS_DIR", tmp_path / "keys")
    monkeypatch.setattr(catalog, "CATALOG_PATH", data / "tutor_catalog.sqlite")
    monkeypatch.setattr(
        catalog, "SIGNATURE_PATH", data / "tutor_catalog.sqlite.sig")
    monkeypatch.setattr(
        catalog, "BACKUP_PATH", data / "tutor_catalog.last_good.json")
    monkeypatch.setattr(catalog, "LOCK_PATH", state / "tutor_catalog.lock")
    monkeypatch.setattr(catalog, "_CACHE", None)
    monkeypatch.setattr(catalog, "_VECTOR_CACHE", None)
    monkeypatch.setattr(catalog, "_KNOWLEDGE_CACHE", None)
    monkeypatch.setattr(catalog, "_KNOWLEDGE_VECTOR_CACHE", None)
    monkeypatch.setattr("virt.get_local_embedder", lambda *_: _fake_embedder())
    return catalog


def test_principal_audience_is_closed():
    with pytest.raises(ValueError):
        TutorPrincipal("u1", "host", "admin", "http")


def test_http_audience_comes_only_from_authenticated_role():
    user = http_principal(
        role="user", device_id="d1", actor="host", conversation_id="c1")
    admin = http_principal(
        role="admin", device_id=None, actor="guest", conversation_id="c1")
    assert user.audience == "user"
    assert admin.audience == "instance_admin"


def test_telegram_host_maps_to_admin_but_autonomy_is_irrelevant():
    principal = telegram_principal({
        "user_id": "u1", "sender_id": "42", "actor": "roberto",
        "role": "host", "autonomy": "ReadOnly",
    })
    assert principal.audience == "instance_admin"


@pytest.mark.parametrize("query", CANONICAL)
def test_structural_detector_does_not_route_help_phrases(query):
    detection = classify(query)
    assert detection.kind == "unknown"
    assert detection.reason == "semantic_mode_required"


def test_structural_detector_does_not_encode_topic_or_scope():
    topic = classify("Cosa fanno executor github")
    generic = classify("Cosa sai fare")
    assert (topic.intent, topic.scope) == ("", "")
    assert (generic.intent, generic.scope) == ("", "")


@pytest.mark.parametrize("query", [
    "Analizza le email degli ultimi 45 giorni e crea un rapporto.",
    "sì", "annulla", "/admin user list",
])
def test_operational_or_pending_inputs_are_not_help(query):
    assert classify(query).kind != "pure_help"


def test_sensitive_shape_never_enters_tutor():
    result = classify("Come uso GitHub? token=ghp_0123456789abcdefghijklmnop")
    assert result.kind == "unknown"
    assert result.reason == "sensitive_shape"


@pytest.mark.parametrize("query", [
    "Come faccio a creare un task e poi fallo?",
    "Come creo un task? Crealo ogni mattina alle 8 per leggere le mie email.",
    "Come preparo un rapporto; poi invialo a Mario.",
    "How do I prepare a report? Then send it to Alice.",
])
def test_mixed_forms_are_left_to_the_semantic_mode_classifier(query):
    detection = classify(query)
    assert detection.kind == "unknown"
    assert detection.reason == "semantic_mode_required"


def test_published_cards_have_semantics_and_no_phrase_routing():
    cards = load_published()
    assert {card.card_id for card in cards} == {
        "attivita-programmate", "fotografie-dominio",
        "github-capabilities", "metnos-capabilities",
    }
    assert sum(card.kind == "capability_overview" for card in cards) == 1
    for card in cards:
        assert set(card.semantic) == {"it", "en"}
        assert not hasattr(card, "affinity")
        assert not hasattr(card, "exact")


def test_real_local_embeddings_route_without_runtime_phrase_tables():
    cards = load_published()
    index = _real_index()
    for query, expected in {
        **CANONICAL,
        "Cosa fanno executor github": "github-capabilities",
        "Vorrei capire come pianificare un promemoria ricorrente":
            "attivita-programmate",
        "In che modo cerco immagini nel mio archivio?": "fotografie-dominio",
    }.items():
        matched = retrieve(query, "it", cards=cards, index=index)
        assert matched is not None, query
        assert matched.card.card_id == expected, query
    assert retrieve(
        "Cosa fanno Mario e Lucia domani?", "it", cards=cards,
        index=index) is None


def test_f2_corpus_is_explicit_dynamic_and_excludes_internal_sources():
    units = build_knowledge_units()
    refs = {unit.source_ref for unit in units}
    assert any(ref == "manifest:read_messages:it" for ref in refs)
    assert any(ref.startswith("docs/it/Metnos_QuickTour.html#")
               for ref in refs)
    assert all("internal/" not in ref for ref in refs)
    assert all("turns/" not in ref and "logs/" not in ref for ref in refs)
    assert max(len(unit.semantic) for unit in units) < 1100
    public_units = tuple(
        unit for unit in units if unit.source_ref.startswith("docs/"))
    assert public_units
    assert all(unit.public_url.startswith("https://metnos.com/")
               for unit in public_units)
    assert all(not unit.public_url for unit in units
               if not unit.source_ref.startswith("docs/"))


def test_executor_parent_semantics_project_canonical_manifest_affinity_only():
    units = build_knowledge_units()
    move = next(
        unit for unit in units
        if unit.source_ref == "manifest:move_messages:it"
    )
    assert "archivio" in move.semantic.casefold()
    assert "email" in move.semantic.casefold()
    assert "SCOPO:" not in move.semantic
    assert "archivio" not in move.text.casefold()
    destination = next(
        unit for unit in units
        if unit.source_ref.startswith(
            "manifest:move_messages:arg:dst_folder:it#")
    )
    assert "executor=move_messages" in destination.semantic
    assert "archivio" in destination.semantic.casefold()
    assert destination.text in destination.semantic
    assert "affinity=" not in destination.text


def test_tutor_context_exposes_only_canonical_public_document_links():
    from dataclasses import replace

    from tutor.service import _render_context

    internal = _knowledge_unit()
    public = replace(
        internal,
        unit_id="doc-public-guide-it-0001",
        source_kind="manual",
        authority="published_documentation",
        source_ref="docs/it/guide.html#1",
        public_url="https://metnos.com/it/guide",
    )
    public_hit = SourceHit(
        source_type="knowledge", source_id=public.unit_id, lang="it",
        score=0.9, unit=public,
    )
    internal_hit = SourceHit(
        source_type="knowledge", source_id=internal.unit_id, lang="it",
        score=0.8, unit=internal,
    )
    assert "public_url=https://metnos.com/it/guide" in _render_context(
        public_hit, cards=(), audience="user")
    assert "public_url=" not in _render_context(
        internal_hit, cards=(), audience="user")


def test_tutor_prompt_links_only_attested_public_documents():
    from prompt_loader import get

    for lang in ("it", "en"):
        prompt = get("tutor_compose", lang)
        assert "public_url=<URL>" in prompt
        assert "Markdown" in prompt
        assert "manifest" in prompt.casefold()

    italian = get("tutor_compose", "it")
    english = get("tutor_compose", "en")
    assert "contraddice la premessa" in italian
    assert "contradicts the question's premise" in english


def test_f2_services_come_from_the_canonical_settings_registry():
    from services_registry import catalog
    from tutor.sources import _service_registry_units, declared_source_files

    units = _service_registry_units()
    assert {unit.lang for unit in units} == {"it", "en"}
    assert all(unit.audience == "instance_admin" for unit in units)
    assert all(unit.authority == "runtime_registry" for unit in units)
    assert all(unit.source_kind == "ui_surface" for unit in units)
    assert {unit.title for unit in units} == {
        "Settings > Sistema > Servizi",
        "Settings > System > Services",
    }
    assert all(len(unit.semantic) < 220 for unit in units)
    assert all("Inventario" in unit.semantic or "Inventory" in unit.semantic
               for unit in units)
    assert all("/admin/services" in unit.text for unit in units)
    assert all("web" in unit.text.casefold() for unit in units)
    localized = {unit.lang: unit.text for unit in units}
    for service in catalog():
        assert service.label in localized["it"]
        assert (service.label_en or service.label) in localized["en"]
    assert any(path.name == "services_registry.py"
               for path in declared_source_files())


def test_scheduler_diagnosis_is_typed_bilingual_and_live():
    from tutor.sources import _observation_view_units

    units = [
        unit for unit in _observation_view_units()
        if unit.observation_ref == "SCHEDULER_HEALTH"
    ]
    assert {unit.lang for unit in units} == {"it", "en"}
    assert all(unit.audience == "instance_admin" for unit in units)
    assert all(unit.authority == "runtime_registry" for unit in units)
    assert all(unit.source_kind == "live_observation" for unit in units)
    assert all(unit.observation_ref == "SCHEDULER_HEALTH" for unit in units)
    italian = next(unit for unit in units if unit.lang == "it")
    english = next(unit for unit in units if unit.lang == "en")
    assert "Stato e salute del loop scheduler" in italian.semantic
    assert "Scheduler-loop state and health" in english.semantic
    assert "Payload e output" in italian.text


def test_settings_navigation_and_tutor_share_the_canonical_ui_registry():
    from http_render import render_template
    from tutor.sources import _ui_surface_units, declared_source_files
    from ui_surfaces import catalog, validate_surfaces

    assert validate_surfaces() == ()
    surfaces = catalog()
    units = _ui_surface_units()
    procedures = sum(bool(surface.procedure("it")) for surface in surfaces)
    projected = tuple(surface for surface in surfaces
                      if surface.key != "services")
    expected_surface_units = sum(
        2 + len(surface.visible("it")) + len(surface.visible("en"))
        for surface in projected
    )
    assert len(units) == expected_surface_units + procedures * 2
    assert {unit.source_kind for unit in units} == {
        "ui_surface", "ui_procedure"}
    surface_by_key = {surface.key: surface for surface in surfaces}
    for unit in units:
        key = unit.source_ref.split(":")[2]
        assert unit.audience == surface_by_key[key].knowledge_audience
    assert any(path.name == "ui_surfaces.py"
               for path in declared_source_files())
    html = render_template("services.html", services=(), notice="")
    for surface in surfaces:
        assert f'href="{surface.route}"' in html
        assert surface.label("it") in html
        assert surface.visible("it")
        assert surface.visible("en")

    turns = next(unit for unit in units
                 if unit.unit_id == "runtime-ui-turns-it")
    for field in ("identificativo turno", "inizio", "canale", "attore",
                  "passi", "esito", "durata", "testo della richiesta"):
        assert field in turns.text

    users = next(unit for unit in units
                 if unit.unit_id == "runtime-ui-users-it")
    for control in ("crea un guest", "emetti token", "salva le preferenze",
                    "elimina l'utente"):
        assert control in users.text

    settings_facets = [
        unit for unit in units
        if unit.unit_id.startswith("runtime-ui-settings-facet-")
        and unit.lang == "it"
    ]
    settings = next(surface for surface in surfaces
                    if surface.key == "settings")
    assert len(settings_facets) == len(settings.visible("it"))
    assert all(unit.source_ref == "runtime:ui_surface:settings:it"
               for unit in settings_facets)
    assert any("latenza mediana" in unit.semantic
               for unit in settings_facets)


def test_f2_executor_knowledge_follows_the_admitted_catalog(monkeypatch, tmp_path):
    from tutor import sources

    manifest = tmp_path / "manifest.toml"
    manifest.write_text(
        '[description]\n'
        'it = "SCOPO: esamina nuovi oggetti di prova."\n'
        'en = "PURPOSE: inspects new test objects."\n'
        '[args]\n'
        'type = "object"\n'
        '[args.properties.account]\n'
        'type = ["string", "array"]\n'
        'default = "primary"\n'
        '[args.properties.account.description]\n'
        'it = "Casella aggiuntiva configurabile con credenziali proprie."\n'
        'en = "Additional mailbox configurable with its own credentials."\n',
        encoding="utf-8",
    )
    executor = SimpleNamespace(
        name="inspect_test_objects", manifest_path=manifest,
        description="", membership="builtin", lifecycle="active",
        dormant=False, execution_policy={"effect": "read_only"},
        platforms=["linux"],
    )
    monkeypatch.setattr("loader.load_catalog", lambda: [executor])
    units = sources._executor_units()
    assert {unit.lang for unit in units} == {"it", "en"}
    assert all("inspect_test_objects" in unit.text for unit in units)
    assert any("nuovi oggetti di prova" in unit.text for unit in units)
    account_units = [unit for unit in units if unit.title.endswith(".account")]
    assert {unit.lang for unit in account_units} == {"it", "en"}
    assert any("credenziali proprie" in unit.text for unit in account_units)
    assert any('default="primary"' in unit.text for unit in account_units)


def test_f2_indexes_read_messages_account_credentials_from_manifest():
    units = build_knowledge_units()
    account_units = [
        unit for unit in units
        if unit.source_ref.startswith("manifest:read_messages:arg:account:it#")
    ]
    assert account_units
    text = " ".join(unit.text for unit in account_units)
    assert "Account aggiuntivi" in text
    assert "~/.config/metnos/mail/<nome>.env" in text
    assert "HOST_IMAP" in text and "PASS" in text


def test_f2_document_sources_remain_bilingual(tmp_path):
    for lang in ("it", "en", "fr", "pt-BR"):
        target = tmp_path / "docs" / lang / "guide.html"
        target.parent.mkdir(parents=True)
        target.write_text(f"<h1>{lang}</h1>", encoding="utf-8")
    (tmp_path / "docs" / "not_a_locale" / "guide.html").parent.mkdir(
        parents=True)
    found = _resolve_language_paths(
        "docs/{lang}/guide.html", ("it", "en"), repo_root=tmp_path)
    assert set(found) == {"it", "en"}
    assert "fr" not in found and "pt-br" not in found


def test_supplemental_tutor_sources_reject_internal_in_every_environment(
        tmp_path):
    for lang in ("it", "en"):
        target = tmp_path / "internal" / lang / "guide.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"<h1>{lang}</h1>", encoding="utf-8")

    with pytest.raises(ValueError, match="internal material"):
        _resolve_language_paths(
            "internal/{lang}/guide.html", ("it", "en"), repo_root=tmp_path)


def test_f2_document_parser_excludes_published_roadmap_claims():
    from tutor import sources

    parser = sources._HTMLBlocks()
    parser.feed(
        '<h1>Current</h1><p>Available capability.</p>'
        '<div class="tutor-exclude"><h2>Future</h2>'
        '<p>Unreleased capability.</p></div>'
    )
    text = " ".join(text for _tag, text in parser.blocks)
    assert "Available capability" in text
    assert "Unreleased capability" not in text


def test_f2_executor_descriptions_admit_new_manifest_language(
        monkeypatch, tmp_path):
    from tutor import sources

    manifest = tmp_path / "manifest.toml"
    manifest.write_text(
        '[description]\n'
        'it = "Esamina oggetti."\n'
        'fr = "Examine des objets."\n',
        encoding="utf-8",
    )
    executor = SimpleNamespace(
        name="inspect_objects", manifest_path=manifest, description="",
        membership="builtin", lifecycle="active", dormant=False,
        execution_policy={"effect": "read_only"}, platforms=["linux"],
    )
    monkeypatch.setattr("loader.load_catalog", lambda: [executor])
    units = sources._executor_units()
    assert {unit.lang for unit in units} == {"it", "fr"}
    assert {unit.concept_id for unit in units} == {"executor-inspect_objects"}
    assert all(unit.observation_ref == "" for unit in units)
    assert all("effect=read_only" in unit.text for unit in units)


def _knowledge_unit(*, unit_id="executor-read_messages-it",
                    concept_id=None, lang="it", audience="user",
                    text="Legge messaggi da più caselle"):
    return KnowledgeUnit(
        unit_id=unit_id,
        concept_id=concept_id or unit_id.rsplit("-", 1)[0],
        lang=lang,
        audience=audience,
        source_kind="executor_manifest",
        authority="admitted_manifest",
        priority=100,
        title="read_messages",
        text=text,
        semantic=text,
        source_ref=f"manifest:{unit_id}:{lang}",
        content_hash="sha256:test",
    )


def test_f2_language_fallback_is_per_concept_not_global():
    from tutor.catalog import VectorIndex

    first_fr = _knowledge_unit(
        unit_id="executor-alpha-fr", concept_id="executor-alpha", lang="fr",
        text="description française")
    first_en = _knowledge_unit(
        unit_id="executor-alpha-en", concept_id="executor-alpha", lang="en",
        text="English description")
    second_en = _knowledge_unit(
        unit_id="executor-beta-en", concept_id="executor-beta", lang="en",
        text="English-only description")
    card = load_published()[0]
    card_index = VectorIndex(
        refs=((card.card_id, "it"), (card.card_id, "en")),
        matrix=np.asarray([[0.0, 1.0], [0.0, 1.0]], dtype=np.float32),
        dimension=2, fingerprint="test",
    )
    units = (first_fr, first_en, second_en)
    knowledge_index = VectorIndex(
        refs=tuple((unit.unit_id, unit.lang) for unit in units),
        matrix=np.asarray([[1.0, 0.0], [0.8, 0.0], [0.95, 0.0]],
                          dtype=np.float32),
        dimension=2, fingerprint="test",
    )

    class Embedder:
        def embed_texts(self, _texts):
            return np.asarray([[1.0, 0.0]], dtype=np.float32)

        def embed_query(self, text):
            return self.embed_texts([text])[0]

    context = retrieve_sources(
        "objets", "fr", "user", cards=(card,), card_index=card_index,
        units=units, knowledge_index=knowledge_index, embedder=Embedder(),
        minimum_score=0.7,
    )
    assert context is not None
    selected = {hit.source_id for hit in context.hits}
    assert "executor-alpha-fr" in selected
    assert "executor-alpha-en" not in selected
    assert "executor-beta-en" in selected


def test_manifest_root_becomes_primary_and_orphan_arguments_are_excluded():
    from dataclasses import replace

    from tutor.catalog import VectorIndex

    root = replace(
        _knowledge_unit(
            unit_id="executor-relevant-it",
            concept_id="executor-relevant",
            text="Operazione completa pertinente",
        ),
        title="relevant_items",
        source_ref="manifest:relevant_items:it",
    )
    relevant_arg = replace(
        root,
        unit_id="executor-relevant-arg-it",
        concept_id="executor-relevant-arg",
        source_kind="executor_manifest_argument",
        title="relevant_items.target",
        source_ref="manifest:relevant_items:arg:target:it#1",
    )
    orphan_root = replace(
        root,
        unit_id="executor-orphan-it",
        concept_id="executor-orphan",
        title="orphan_items",
        source_ref="manifest:orphan_items:it",
    )
    orphan_arg = replace(
        relevant_arg,
        unit_id="executor-orphan-arg-it",
        concept_id="executor-orphan-arg",
        title="orphan_items.email",
        source_ref="manifest:orphan_items:arg:email:it#1",
    )
    units = (orphan_arg, root, relevant_arg, orphan_root)
    index = VectorIndex(
        refs=tuple((unit.unit_id, "it") for unit in units),
        matrix=np.asarray([
            [1.0, 0.0],
            [0.98, 0.1989975],
            [0.97, 0.2431049],
            [0.70, 0.7141428],
        ], dtype=np.float32),
        dimension=2,
        fingerprint="test",
    )

    class Embedder:
        def embed_query(self, _text):
            return np.asarray([1.0, 0.0], dtype=np.float32)

    context = retrieve_sources(
        "domanda naturale", "it", "user", cards=(), units=units,
        knowledge_index=index, embedder=Embedder(), minimum_score=0.7,
    )

    assert context is not None
    assert context.hits[0].source_id == root.unit_id
    selected = {hit.source_id for hit in context.hits}
    assert relevant_arg.unit_id in selected
    assert orphan_arg.unit_id not in selected


def test_manifest_argument_is_not_displaced_by_an_unrelated_manual():
    from dataclasses import replace

    from tutor.catalog import VectorIndex

    argument = replace(
        _knowledge_unit(
            unit_id="executor-alpha-arg-email-it",
            concept_id="executor-alpha-arg-email",
            text="Parametro email dell'operazione alpha",
        ),
        source_kind="executor_manifest_argument",
        title="alpha_items.email",
        source_ref="manifest:alpha_items:arg:email:it#1",
    )
    manual = replace(
        argument,
        unit_id="doc-nearby-it",
        concept_id="doc-nearby",
        source_kind="manual",
        authority="published_documentation",
        title="Guida vicina",
        source_ref="docs/it/nearby.html#1",
    )
    units = (argument, manual)
    index = VectorIndex(
        refs=tuple((unit.unit_id, "it") for unit in units),
        matrix=np.asarray([
            [1.0, 0.0],
            [0.98, 0.1989975],
        ], dtype=np.float32),
        dimension=2,
        fingerprint="test",
    )

    class Embedder:
        def embed_query(self, _text):
            return np.asarray([1.0, 0.0], dtype=np.float32)

    context = retrieve_sources(
        "domanda sul parametro", "it", "user", cards=(), units=units,
        knowledge_index=index, embedder=Embedder(), minimum_score=0.7,
    )

    assert context is not None
    assert context.hits[0].source_id == argument.unit_id


def test_manifest_composition_scope_uses_only_explicit_source_neighbourhood():
    from dataclasses import replace

    from tutor.service import (
        _coherent_manifest_scope,
        _coverage_items,
        _ledger_scope,
    )

    primary = replace(
        _knowledge_unit(
            unit_id="executor-alpha-it", concept_id="executor-alpha",
            text="SCOPO: trasforma elementi; ricerca preliminare -> beta_items.",
        ),
        title="alpha_items", source_ref="manifest:alpha_items:it",
    )
    linked = replace(
        primary,
        unit_id="executor-beta-it", concept_id="executor-beta",
        title="beta_items", source_ref="manifest:beta_items:it",
        text="SCOPO: cerca elementi per alpha_items.",
    )
    unrelated = replace(
        primary,
        unit_id="executor-gamma-it", concept_id="executor-gamma",
        title="gamma_items", source_ref="manifest:gamma_items:it",
        text="SCOPO: amministra un oggetto distinto.",
    )

    def argument(root, name):
        family = root.title
        return replace(
            root,
            unit_id=f"executor-{family}-arg-{name}-it",
            concept_id=f"executor-{family}-arg-{name}",
            source_kind="executor_manifest_argument",
            title=f"{family}.{name}",
            source_ref=f"manifest:{family}:arg:{name}:it#1",
        )

    def document(unit_id, text):
        return replace(
            primary,
            unit_id=unit_id, concept_id=unit_id,
            source_kind="manual", authority="published_documentation",
            title="Guida", text=text, semantic=text,
            source_ref=f"docs/it/{unit_id}.html#1",
        )

    units = (
        primary, argument(unrelated, "email"), linked,
        argument(primary, "target"), argument(linked, "query"), unrelated,
        document("doc-best", "Spiegazione generale pertinente."),
        document("doc-noise", "Procedimento differente."),
        document("doc-linked", "Contratto alpha_items e relativi limiti."),
    )
    hits = tuple(SourceHit(
        source_type="knowledge", source_id=unit.unit_id, lang="it",
        score=1.0 - index / 100, unit=unit,
    ) for index, unit in enumerate(units))

    scoped = _coherent_manifest_scope(hits, hits[0])
    selected = {hit.source_id for hit in scoped}
    assert primary.unit_id in selected
    assert linked.unit_id in selected
    assert argument(primary, "target").unit_id in selected
    assert argument(linked, "query").unit_id in selected
    assert "doc-best" in selected and "doc-linked" in selected
    assert unrelated.unit_id not in selected
    assert argument(unrelated, "email").unit_id not in selected
    assert "doc-noise" not in selected

    coverage = _coverage_items(_ledger_scope(scoped, hits[0]))
    assert len(coverage["tools"]) == 1


def test_f2_expands_adjacent_sections_of_a_selected_document():
    from tutor.catalog import VectorIndex

    card = load_published()[0]

    def documented(number: int, text: str) -> KnowledgeUnit:
        return KnowledgeUnit(
            unit_id=f"doc-guide-it-{number:04d}",
            concept_id=f"doc-guide-{number:04d}",
            lang="it", audience="user", source_kind="operational",
            authority="published_documentation", priority=95,
            title=f"Sezione {number}", text=text, semantic=text,
            source_ref=f"docs/it/guide.html#{number}",
            content_hash=f"sha256:{number}",
        )

    first = documented(1, "Introduzione semanticamente pertinente")
    second = documented(2, "Identità e concetti principali")
    third = documented(3, "Procedura adiacente con campi e passaggi")
    unrelated = _knowledge_unit(
        unit_id="executor-other-it", concept_id="executor-other",
        text="Altra fonte pertinente ma non parte del documento",
    )
    units = (first, second, third, unrelated)
    card_index = VectorIndex(
        refs=((card.card_id, "it"),),
        matrix=np.asarray([[0.0, 1.0]], dtype=np.float32),
        dimension=2, fingerprint="test",
    )
    knowledge_index = VectorIndex(
        refs=tuple((unit.unit_id, "it") for unit in units),
        matrix=np.asarray([
            [1.0, 0.0], [0.9, 0.4358899], [0.5, 0.8660254], [0.8, 0.6],
        ], dtype=np.float32),
        dimension=2, fingerprint="test",
    )

    class Embedder:
        def embed_texts(self, _texts):
            return np.asarray([[1.0, 0.0]], dtype=np.float32)

        def embed_query(self, text):
            return self.embed_texts([text])[0]

    context = retrieve_sources(
        "argomento pertinente", "it", "user", cards=(card,),
        card_index=card_index, units=units,
        knowledge_index=knowledge_index, embedder=Embedder(),
        minimum_score=0.7, top_k=4,
    )
    assert context is not None
    assert third.unit_id in {hit.source_id for hit in context.hits}


def test_exact_document_identity_binds_retrieval_before_similarity():
    from tutor.catalog import VectorIndex

    document = KnowledgeUnit(
        unit_id="doc-guide-it-0001", concept_id="doc-guide-0001",
        lang="it", audience="user", source_kind="operational",
        authority="published_documentation", priority=95,
        title="Una sezione", text="Contenuto del documento",
        semantic="Tema lontano dalla formulazione dell'utente",
        source_ref="docs/it/guide.html#1", content_hash="sha256:guide",
        public_url="https://metnos.com/it/guide",
    )
    unrelated = _knowledge_unit(
        unit_id="executor-unrelated-it",
        text="Cosa contiene il file richiesto",
    )
    index = VectorIndex(
        refs=((document.unit_id, "it"), (unrelated.unit_id, "it")),
        matrix=np.asarray([[0.0, 1.0], [1.0, 0.0]], dtype=np.float32),
        dimension=2, fingerprint="test",
    )

    class Embedder:
        def embed_texts(self, _texts):
            return np.asarray([[1.0, 0.0]], dtype=np.float32)

        def embed_query(self, text):
            return self.embed_texts([text])[0]

    trace = {}
    context = retrieve_sources(
        "Cosa contiene guide.html?", "it", "user", cards=(),
        units=(document, unrelated), knowledge_index=index,
        embedder=Embedder(), minimum_score=0.99,
        required_source_ref="docs/it/guide.html", explain=trace,
    )

    assert context is not None
    assert tuple(hit.source_id for hit in context.hits) == (document.unit_id,)
    assert trace["identity_source_ref"] == "docs/it/guide.html"


def _oriented(unit_id, vector_marker, *, authority="published_documentation",
              kind="operational", priority=95):
    text = f"Contenuto {vector_marker} indipendente"
    return KnowledgeUnit(
        unit_id=unit_id, concept_id=unit_id, lang="it", audience="user",
        source_kind=kind, authority=authority, priority=priority,
        title=f"Titolo {vector_marker}", text=text, semantic=text,
        source_ref=f"docs/it/{unit_id}.html#1",
        content_hash=f"sha256:{unit_id}",
    )


def _two_axis_embedder(companion_marker: str):
    class Embedder:
        def embed_texts(self, texts):
            return np.asarray([self.embed_query(text) for text in texts],
                              dtype=np.float32)

        def embed_query(self, text):
            if companion_marker in text:
                return np.asarray([0.0, 1.0], dtype=np.float32)
            return np.asarray([1.0, 0.0], dtype=np.float32)

    return Embedder()


def test_companion_probe_admits_the_previous_question_sources():
    """Una fonte entra se e' pertinente per ALMENO una delle formulazioni.

    La domanda corrente e quella risolta contro il turno precedente concorrono
    con un massimo per fonte: chi risponde a una delle due entra, chi non
    risponde a nessuna resta fuori. La controprova e' la terza unita', a mezza
    strada fra i due assi: pertinente in parte per entrambe, dentro la soglia,
    fuori dalla banda.
    """

    from tutor.catalog import VectorIndex

    current = _oriented("solo-corrente", "alfa")
    companion = _oriented("solo-precedente", "beta")
    weak = _oriented("estranea", "gamma")
    units = (current, companion, weak)
    knowledge_index = VectorIndex(
        refs=tuple((unit.unit_id, "it") for unit in units),
        matrix=np.asarray([
            [1.0, 0.0], [0.0, 1.0], [0.70710678, 0.70710678],
        ], dtype=np.float32),
        dimension=2, fingerprint="test",
    )
    context = retrieve_sources(
        "domanda corrente", "it", "user", cards=(), units=units,
        knowledge_index=knowledge_index,
        embedder=_two_axis_embedder("riferimento precedente"),
        minimum_score=0.7, top_k=8,
        companion_query="riferimento precedente",
    )
    assert context is not None
    identifiers = {hit.source_id for hit in context.hits}
    assert identifiers == {"solo-corrente", "solo-precedente"}


def test_authority_quota_admits_a_starved_registry_unit(monkeypatch):
    """Dentro la banda, il tetto di contesto non affama una classe intera.

    Le sezioni di prosa riempiono il contesto per differenze di centesimi;
    l'unita' di registro che attesta il fatto resta fuori. La quota le riserva
    un posto sfrattando la piu' debole della classe sovrarappresentata, senza
    toccare il primario. La controprova e' la stessa selezione con la quota
    spenta, dove l'unita' di registro non compare.
    """

    from tutor.catalog import VectorIndex

    prose = [_oriented(f"prosa-{index}", "alfa") for index in range(3)]
    registry = _oriented(
        "registro", "alfa", authority="runtime_registry", kind="ui_surface")
    units = (*prose, registry)
    knowledge_index = VectorIndex(
        refs=tuple((unit.unit_id, "it") for unit in units),
        matrix=np.asarray([
            [1.0, 0.0], [0.99, 0.141], [0.98, 0.199], [0.97, 0.243],
        ], dtype=np.float32),
        dimension=2, fingerprint="test",
    )

    def select():
        return retrieve_sources(
            "domanda", "it", "user", cards=(), units=units,
            knowledge_index=knowledge_index,
            embedder=_two_axis_embedder("mai presente"),
            minimum_score=0.7, top_k=3,
        )

    monkeypatch.setenv("METNOS_TUTOR_AUTHORITY_QUOTA", "0")
    without = select()
    assert without is not None
    assert "registro" not in {hit.source_id for hit in without.hits}

    monkeypatch.delenv("METNOS_TUTOR_AUTHORITY_QUOTA", raising=False)
    with_quota = select()
    assert with_quota is not None
    identifiers = [hit.source_id for hit in with_quota.hits]
    assert "registro" in identifiers
    assert identifiers[0] == "prosa-0"
    assert len(identifiers) == 3


@pytest.mark.parametrize("query,lang", [
    ("Comment puis-je lire plusieurs boîtes mail ?", "fr"),
    ("Wie kann ich mehrere E-Mail-Postfächer lesen?", "de"),
    ("¿Cómo puedo leer varios buzones de correo?", "es"),
])
def test_f2_real_embedding_retrieves_english_source_cross_language(query, lang):
    from tutor.catalog import VectorIndex
    from virt import get_local_embedder

    card = load_published()[0]
    relevant = _knowledge_unit(
        unit_id="executor-mail-en", concept_id="executor-mail", lang="en",
        text=("Read email messages from multiple mailboxes and normalize "
              "senders, dates and attachments."),
    )
    unrelated = _knowledge_unit(
        unit_id="executor-calendar-en", concept_id="executor-calendar",
        lang="en", text="Create and edit calendar events.",
    )
    embedder = get_local_embedder("text")
    semantics = [card.semantic["en"], relevant.semantic, unrelated.semantic]
    matrix = np.asarray(embedder.embed_texts(semantics), dtype=np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    card_index = VectorIndex(
        refs=((card.card_id, "en"),), matrix=matrix[:1],
        dimension=matrix.shape[1], fingerprint="real-local",
    )
    knowledge_index = VectorIndex(
        refs=((relevant.unit_id, "en"), (unrelated.unit_id, "en")),
        matrix=matrix[1:], dimension=matrix.shape[1], fingerprint="real-local",
    )
    context = retrieve_sources(
        query, lang, "user", cards=(card,), card_index=card_index,
        units=(relevant, unrelated), knowledge_index=knowledge_index,
        embedder=embedder, minimum_score=0.75,
    )
    assert context is not None and not context.restricted
    assert context.hits[0].source_id == relevant.unit_id
    assert context.hits[0].lang == "en"


def test_f2_unified_ranker_can_supersede_the_card_set():
    from tutor.catalog import VectorIndex

    cards = load_published()
    card = next(item for item in cards if item.card_id == "fotografie-dominio")
    unit = _knowledge_unit()
    card_index = VectorIndex(
        refs=((card.card_id, "it"),),
        matrix=np.asarray([[0.0, 1.0, 0.0]], dtype=np.float32),
        dimension=3, fingerprint="test",
    )
    knowledge_index = VectorIndex(
        refs=((unit.unit_id, "it"),),
        matrix=np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32),
        dimension=3, fingerprint="test",
    )

    class Embedder:
        def embed_texts(self, _texts):
            return np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32)

        def embed_query(self, text):
            return self.embed_texts([text])[0]

    context = retrieve_sources(
        "Come leggo tutte le caselle email?", "it", "user",
        cards=(card,), card_index=card_index, units=(unit,),
        knowledge_index=knowledge_index, embedder=Embedder(),
    )
    assert context is not None and not context.restricted
    assert context.hits[0].unit == unit


def test_f2_restricted_source_body_never_leaves_retrieval():
    from tutor.catalog import VectorIndex

    card = load_published()[0]
    unit = _knowledge_unit(
        unit_id="doc-admin-it", audience="instance_admin",
        text="Procedura amministrativa riservata",
    )
    card_index = VectorIndex(
        refs=((card.card_id, "it"),),
        matrix=np.asarray([[0.0, 1.0]], dtype=np.float32),
        dimension=2, fingerprint="test",
    )
    knowledge_index = VectorIndex(
        refs=((unit.unit_id, "it"),),
        matrix=np.asarray([[1.0, 0.0]], dtype=np.float32),
        dimension=2, fingerprint="test",
    )

    class Embedder:
        def embed_texts(self, _texts):
            return np.asarray([[1.0, 0.0]], dtype=np.float32)

        def embed_query(self, text):
            return self.embed_texts([text])[0]

    context = retrieve_sources(
        "Come amministro il sistema?", "it", "user",
        cards=(card,), card_index=card_index, units=(unit,),
        knowledge_index=knowledge_index, embedder=Embedder(),
    )
    assert context is not None and context.restricted
    assert context.hits == ()


def test_f2_service_answers_without_any_card_match(monkeypatch):
    cards = load_published()
    unit = _knowledge_unit()
    context = SemanticContext((SourceHit(
        source_type="knowledge", source_id=unit.unit_id, lang="it",
        score=0.86, unit=unit,
    ),), top_score=0.86)
    _patch_request_snapshot(monkeypatch, cards=cards, units=(unit,))
    monkeypatch.setattr(
        "tutor.service.retrieve_sources", lambda *_a, **_k: context)
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_a, **_k: ModeDecision("EXPLAIN", True))
    monkeypatch.setattr(
        "tutor.compose.compose_answer",
        lambda **kwargs: Composition("answer", kwargs["context"]))
    answer = answer_request(TutorRequest(
        "Come faccio a leggere tutte le mie caselle email?", "it",
        _principal("user")))
    assert answer is not None and answer.esito == "fondata"
    assert answer.card_ids == ()
    assert answer.source_ids == (f"knowledge:{unit.unit_id}",)
    assert "Legge messaggi da più caselle" in answer.answer_md


@pytest.mark.parametrize("query", [
    "Illustrazione del funzionamento del pairing",
    "Pairing dei dispositivi: vorrei capirne la logica",
    "read_messages?",
])
def test_f2_unpredictable_explanation_forms_bypass_the_f1_gate(
        monkeypatch, query):
    cards = load_published()
    unit = _knowledge_unit(text="Il pairing collega un dispositivo.")
    context = SemanticContext((SourceHit(
        source_type="knowledge", source_id=unit.unit_id, lang="it",
        score=0.84, unit=unit,
    ),), top_score=0.84)
    _patch_request_snapshot(monkeypatch, cards=cards, units=(unit,))
    monkeypatch.setattr(
        "tutor.service.retrieve_sources", lambda *_a, **_k: context)
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_a, **_k: ModeDecision("EXPLAIN", True))
    monkeypatch.setattr(
        "tutor.compose.compose_answer",
        lambda **kwargs: Composition("answer", kwargs["context"]))
    answer = answer_request(TutorRequest(query, "it", _principal("user")))
    assert answer is not None and answer.detection == "semantic_help"


def test_f2_semantic_gate_does_not_steal_an_action(monkeypatch):
    cards = load_published()
    unit = _knowledge_unit()
    context = SemanticContext((SourceHit(
        source_type="knowledge", source_id=unit.unit_id, lang="it",
        score=0.84, unit=unit,
    ),), top_score=0.84)
    _patch_request_snapshot(monkeypatch, cards=cards, units=(unit,))
    monkeypatch.setattr(
        "tutor.service.retrieve_sources", lambda *_a, **_k: context)
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_a, **_k: ModeDecision("ACT", True))
    request = TutorRequest(
        "Per favore leggi tutte le mie email", "it", _principal("user"))
    assert answer_request(request) is None


def test_current_readonly_executor_target_returns_to_operational_runtime(
        monkeypatch):
    """An OBSERVE request without a complete view remains operational."""

    from tutor.observation_views import ViewSelection

    _patch_request_snapshot(monkeypatch, cards=(), units=())
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_a, **_k: ModeDecision("OBSERVE", True))
    monkeypatch.setattr(
        "tutor.observation_views.select_view",
        lambda **_kwargs: ViewSelection(None, True, "no_complete_view"))
    monkeypatch.setattr(
        "tutor.service.retrieve_sources",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("OBSERVE crossed static retrieval")))
    monkeypatch.setattr(
        "tutor.probes.execute_probe_refs",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("unselected observation crossed probe gate")))

    assert answer_request(TutorRequest(
        "temperatura cpu pc-roberto", "it", _principal("user"))) is None


def test_f2_unproven_mixed_request_falls_through_to_compound_planner(monkeypatch):
    cards = load_published()
    unit = _knowledge_unit()
    context = SemanticContext((SourceHit(
        source_type="knowledge", source_id=unit.unit_id, lang="it",
        score=0.84, unit=unit,
    ),), top_score=0.84)
    _patch_request_snapshot(monkeypatch, cards=cards, units=(unit,))
    monkeypatch.setattr(
        "tutor.service.retrieve_sources", lambda *_a, **_k: context)
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_a, **_k: ModeDecision("MIXED", True))
    monkeypatch.setattr("tutor.service._msg", lambda key: key)
    answer = answer_request(TutorRequest(
        "Illustrami la posta e poi leggila", "it", _principal("user")))
    assert answer is None


def test_single_clause_canonical_action_uses_semantic_safe_fallthrough(
        monkeypatch):
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_a, **_k: ModeDecision("ACT", True),
    )
    request = TutorRequest(
        "Leggi le mie email", "it", _principal("user"))
    assert answer_request(request) is None


def test_canonical_action_start_does_not_hide_a_mixed_second_clause(
        monkeypatch):
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_a, **_k: ModeDecision("MIXED", True))
    monkeypatch.setattr("tutor.service._msg", lambda key: key)
    request = TutorRequest(
        "Dimmi cosa mostra la pagina Servizi e riavvia il componente web",
        "it", _principal("instance_admin"),
    )
    answer = answer_request(request)
    assert answer is None


def test_f2_mode_classifier_uses_the_central_llm_slot(monkeypatch):
    from tutor.mode import classify_mode

    observed = {}

    def scheduled(executor, call, **_kwargs):
        observed["policy"] = executor.execution_policy
        return call()

    monkeypatch.setattr("executor_scheduler.invoke_scheduled", scheduled)
    def call(*_args, **kwargs):
        observed["call"] = kwargs
        return "EXPLAIN", {"latency_ms": 1}

    monkeypatch.setattr("llm_helpers.call_llm", call)
    assert classify_mode("Mi illustri il pairing", "it") == "EXPLAIN"
    assert observed["policy"]["resource_class"] == "llm"
    assert observed["policy"]["parallelism_class"] == 0
    assert observed["call"]["tier"] == "fast"
    assert "think" not in observed["call"]
    assert "temperature" not in observed["call"]


def test_mode_and_followup_relation_are_independent_classifiers(monkeypatch):
    from tutor.mode import classify_mode_decision

    calls = []
    answers = iter(("OBSERVE", "FOLLOWUP"))
    monkeypatch.setattr(
        "executor_scheduler.invoke_scheduled",
        lambda _executor, call, **_kwargs: call())

    def call(payload, prompt, **_kwargs):
        calls.append((dict(payload), prompt))
        return next(answers), {}

    monkeypatch.setattr("llm_helpers.call_llm", call)
    decision = classify_mode_decision(
        "E quello?", "it",
        conversation_context=(
            "PREVIOUS_USER_QUESTION: Qual è lo stato del servizio?\n"
            "PREVIOUS_TUTOR_ANSWER: Il servizio è attivo."),
        deadline_at=10**12,
    )

    assert decision.mode == "OBSERVE"
    assert decision.is_followup is True
    assert set(calls[0][0]) == {"language", "user_query"}
    assert "conversation_context" not in calls[0][0]
    assert "conversation_context" in calls[1][0]


def test_normal_action_falls_through_to_runtime(monkeypatch):
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_a, **_k: ModeDecision("ACT", True))
    request = TutorRequest("Leggi le mie email", "it", _principal())
    assert answer_request(request) is None


def test_weak_capability_topic_falls_through_to_runtime(monkeypatch):
    _route(monkeypatch, None, mode="ACT")
    request = TutorRequest(
        "Cosa fanno Mario e Lucia domani?", "it", _principal())
    assert answer_request(request) is None


def test_generic_capability_question_uses_metadata_overview(monkeypatch):
    _route(monkeypatch, "metnos-capabilities")
    names = [
        "find_urls", "read_urls_html", "find_places", "get_location",
        "read_messages", "find_images_indices", "create_tasks",
        "find_files_google_workspace",
    ] + [f"executor_{i}" for i in range(12)]
    monkeypatch.setattr(
        "loader.load_catalog",
        lambda: [SimpleNamespace(
            name=name, membership="builtin",
            capabilities=([{"name": "provider:access",
                            "hint": ["google-workspace"]}]
                          if name.endswith("_google_workspace") else []),
        ) for name in names],
    )
    answer = answer_request(TutorRequest("Cosa sai fare", "it", _principal()))
    assert answer is not None and answer.esito == "consolidata"
    assert answer.card_ids == ("metnos-capabilities",)
    assert "object=urls" in answer.answer_md
    assert "object=places" in answer.answer_md
    assert "object=location" in answer.answer_md
    assert "google-workspace" in answer.answer_md
    assert "executors=" not in answer.answer_md


def _procedure_unit_context(monkeypatch, *, audience: str):
    """Contesto con la procedura TIPIZZATA del registro (`ui_procedure`):
    dopo il ritiro tranche-1 la scheda console-proposte non esiste piu' e la
    procedura vive nelle unita' derivate da ui_surfaces."""

    from tutor.sources import _ui_surface_units

    unit = next(u for u in _ui_surface_units()
                if u.unit_id == "runtime-ui-procedure-changes-it")
    _patch_request_snapshot(monkeypatch, units=(unit,))
    hit = SourceHit(source_type="knowledge", source_id=unit.unit_id,
                    lang="it", score=0.9, unit=unit)
    restricted = not (audience == "instance_admin"
                      or unit.audience != "instance_admin")
    context = (SemanticContext((), 0.9, restricted=True) if restricted
               else SemanticContext((hit,), top_score=0.9))
    monkeypatch.setattr(
        "tutor.service.retrieve_sources", lambda *a, **k: context)
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *a, **k: ModeDecision("EXPLAIN", True))
    return unit


def test_admin_procedure_is_audience_filtered(monkeypatch):
    _procedure_unit_context(monkeypatch, audience="user")
    monkeypatch.setattr("tutor.service._msg", lambda key: key)
    request = TutorRequest(
        "Come approvo una proposta di modifica?", "it", _principal("user"))
    answer = answer_request(request)
    assert answer is not None
    assert answer.esito == "restricted"
    assert answer.answer_md == "MSG_TUTOR_ADMIN_REQUIRED"
    assert not answer.source_ids
    assert "/admin/changes" not in answer.answer_md


def test_admin_procedure_is_deterministic_and_has_safe_steps(monkeypatch):
    unit = _procedure_unit_context(monkeypatch, audience="instance_admin")
    request = TutorRequest(
        "Come approvo una proposta di modifica?", "it", _principal())
    answer = answer_request(request)
    # Primaria ui_procedure = testo del registro consegnato alla lettera,
    # nessuna composizione LLM.
    assert answer is not None and answer.esito == "fondata"
    assert answer.answer_md == unit.text
    assert "/admin/changes" in answer.answer_md
    assert "Fermati se" in answer.answer_md
    assert "non la applica immediatamente" in answer.answer_md


def test_github_answer_comes_from_live_builtin_catalog(monkeypatch):
    _route(monkeypatch, "github-capabilities")
    names = [
        "change_pulls_github", "create_issues_github", "create_tasks_github",
        "delete_issues_github", "delete_messages_github", "find_files_github",
        "find_issues_github", "find_pulls_github", "list_dirs_github",
        "read_files_github", "read_issues_github", "read_pulls_github",
        "read_tasks_github", "send_messages_github", "set_issues_github",
        "set_pulls_github",
    ]
    monkeypatch.setattr(
        "loader.load_catalog",
        lambda: [SimpleNamespace(name=name, membership="builtin")
                 for name in names],
    )
    request = TutorRequest("Cosa fanno executor github", "it", _principal())
    answer = answer_request(request)
    assert answer is not None and answer.esito == "consolidata"
    assert "16 capacità GitHub builtin verificate" in answer.answer_md
    assert "Issue" in answer.answer_md
    assert "Pull request" in answer.answer_md


def test_capability_overview_projects_local_manifest_purposes():
    from loader import load_catalog
    from tutor.service import _catalog_summary

    cards = load_published()
    card = next(item for item in cards
                if item.kind == "capability_overview")
    summary = _catalog_summary(card, "it", cards, "user")
    objects = {
        executor.name.split("_", 2)[1]
        for executor in load_catalog()
        if len(executor.name.split("_", 2)) >= 2
    }
    assert sum(line.startswith("- object=")
               for line in summary.splitlines()) == len(objects)
    files = next(line for line in summary.splitlines()
                 if line.startswith("- object=files;"))
    assert "find=cerca file per pattern" in files
    assert "hash SHA-256" in files
    assert "purposes=" in files


def test_how_to_composer_contract_leads_with_a_natural_chat_example():
    import prompt_loader

    italian = prompt_loader.get("tutor_compose", "it")
    english = prompt_loader.get("tutor_compose", "en")

    it_lead = ("Chiedi a Metnos con una richiesta come quella di questo "
               "esempio: «<RICHIESTA_NATURALE>»")
    en_lead = ("Ask Metnos with a request like this example: "
               "“<NATURAL_REQUEST>”")
    assert it_lead in italian
    assert en_lead in english
    assert italian.index(it_lead) < italian.index("nomi di executor")
    assert english.index(en_lead) < english.index("executor names")
    assert "source_kind=ui_surface" in italian
    assert "source_kind=ui_surface" in english
    assert "delivery_channel" in italian and "delivery_channel" in english
    assert "Da Telegram" not in italian
    assert "From Telegram" not in english
    assert "turno successivo" in italian
    assert "next turn" in english
    assert "password" in italian and "password" in english
    assert "Il Tutor di Metnos" in italian
    assert "The Metnos Tutor" in english
    assert "intero `retrieved_context`" in italian
    assert "whole `retrieved_context`" in english
    assert "ogni riga sia rappresentata" in italian
    assert "every row is represented" in english


def test_composer_receives_the_delivery_channel(monkeypatch):
    from tutor.compose import compose_answer

    observed = {}
    monkeypatch.setattr(
        "executor_scheduler.invoke_scheduled",
        lambda _executor, call, **_kwargs: call())

    def call(payload, _prompt, **_kwargs):
        observed.update(payload)
        return "Risposta fondata.", {}

    monkeypatch.setattr("llm_helpers.call_llm", call)
    result = compose_answer(
        query="Dove trovo la pagina?", context="UI ammessa", lang="it",
        source_ids=("knowledge:ui",), delivery_channel="telegram",
    )
    assert result.status == "answer"
    assert observed["delivery_channel"] == "telegram"


def test_new_language_uses_english_prompt_fallback_but_keeps_target(monkeypatch):
    from tutor.compose import compose_answer

    observed = {}
    monkeypatch.setattr(
        "executor_scheduler.invoke_scheduled",
        lambda _executor, call, **_kwargs: call())

    def call(payload, prompt, **_kwargs):
        observed["payload"] = payload
        observed["prompt"] = prompt
        return "Réponse fondée.", {}

    monkeypatch.setattr("llm_helpers.call_llm", call)
    result = compose_answer(
        query="Où est la page ?", context="Source admise", lang="fr",
        source_ids=("knowledge:ui",), delivery_channel="telegram",
    )
    assert result.status == "answer"
    assert observed["payload"]["language"] == "fr"
    assert "fallback for a new language" in observed["prompt"]
    assert "specified language" in observed["prompt"]


def test_local_composer_uses_central_llm_resource(monkeypatch):
    from tutor.compose import compose_answer

    observed = {}

    def scheduled(executor, call, **_kwargs):
        observed["policy"] = executor.execution_policy
        return call()

    monkeypatch.setattr("executor_scheduler.invoke_scheduled", scheduled)
    def call(*_args, **kwargs):
        observed["call"] = kwargs
        return "Risposta fondata.", {"latency_ms": 1}

    monkeypatch.setattr("llm_helpers.call_llm", call)
    answer = compose_answer(
        query="Cosa fa?", context="Contesto ammesso", lang="it",
        source_ids=("card:x:it",),
    )
    assert answer.status == "answer"
    assert answer.text == "Risposta fondata."
    assert observed["policy"]["resource_class"] == "llm"
    assert observed["policy"]["parallelism_class"] == 0
    assert observed["call"]["tier"] == "wise"
    assert "think" not in observed["call"]
    assert "temperature" not in observed["call"]
    assert observed["call"]["output_policy"] == "public"


def test_local_composer_rejects_insufficient_or_thinking(monkeypatch):
    from tutor.compose import compose_answer

    monkeypatch.setattr(
        "executor_scheduler.invoke_scheduled",
        lambda _executor, call, **_kwargs: call())
    expected = {
        "TUTOR_CONTEXT_INSUFFICIENT": "insufficient",
        "<think>reasoning": "unavailable",
    }
    for response, status in expected.items():
        monkeypatch.setattr(
            "llm_helpers.call_llm",
            lambda *_a, _response=response, **_k: (_response, {}),
        )
        result = compose_answer(
            query="Q", context="C", lang="it", source_ids=("card:x:it",),
        )
        assert result.status == status
        assert result.text == ""


def test_composer_insufficient_is_a_lacuna_not_unavailable(monkeypatch):
    from tutor.compose import Composition

    _route(monkeypatch, "github-capabilities", compose=False)
    admitted = [
        f"{verb}_{obj}_github"
        for obj in ("issues", "pulls", "files", "dirs", "tasks", "messages")
        for verb in ("read", "find", "create")
    ][:16]
    monkeypatch.setattr(
        "loader.load_catalog",
        lambda: [SimpleNamespace(
            name=name, membership="builtin") for name in admitted],
    )
    monkeypatch.setattr(
        "tutor.compose.compose_answer",
        lambda **_kwargs: Composition("insufficient"),
    )
    monkeypatch.setattr("tutor.service._msg", lambda key: key)
    answer = answer_request(TutorRequest(
        "Come funziona un dettaglio non documentato?", "it", _principal()))
    assert answer is not None
    assert answer.esito == "lacuna"
    assert answer.answer_md == "MSG_TUTOR_LACUNA"
    assert answer.source_ids == ("card:github-capabilities:it",)


def test_same_conversation_probe_is_one_ranking_with_companion(monkeypatch):
    cards = load_published()
    unit = _knowledge_unit(text="Il catalogo viene verificato all'avvio.")
    context = SemanticContext((SourceHit(
        source_type="knowledge", source_id=unit.unit_id, lang="it",
        score=0.78, unit=unit,
    ),), top_score=0.78)
    observed = {}

    _patch_request_snapshot(monkeypatch, cards=cards, units=(unit,))

    def retrieve(query, *_args, **kwargs):
        observed.setdefault("calls", []).append(
            (query, kwargs.get("companion_query", "")))
        return context

    def compose(**kwargs):
        observed["composition_context"] = kwargs["conversation_context"]
        return Composition("answer", "La fonte descrive la verifica.")

    monkeypatch.setattr("tutor.service.retrieve_sources", retrieve)
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_a, **_k: ModeDecision("EXPLAIN", True, is_followup=True))
    monkeypatch.setattr("tutor.compose.compose_answer", compose)
    previous = (
        "PREVIOUS_USER_QUESTION: Quanto impiega il catalogo all'avvio?\n"
        "PREVIOUS_TUTOR_ANSWER: Il Tutor era temporaneamente indisponibile."
    )
    answer = answer_request(TutorRequest(
        "Motivo del problema?", "it", _principal(),
        conversation_context=previous,
        previous_question="Quanto impiega il catalogo all'avvio?",
    ))

    assert answer is not None and answer.esito == "fondata"
    # UNA sola classifica, con le due formulazioni dentro: scegliere in blocco
    # fra due classifiche confrontava punteggi di testa appartenenti a testi di
    # lunghezza diversa, e scartava la formulazione giusta (audit 25/7).
    assert len(observed["calls"]) == 1
    query, companion = observed["calls"][0]
    assert query == "Motivo del problema?"
    # La SONDA porta la domanda LETTA NEL SUO CONTESTO: la congiunzione della
    # precedente con la corrente, che e' la domanda risolta. La precedente da
    # sola non e' una domanda dell'utente e riportava la classifica
    # sull'argomento del turno prima. La risposta precedente resta fuori —
    # renderebbe il vettore quasi-duplicato delle proprie fonti — e vive nel
    # contesto del COMPOSER, dove serve a risolvere il riferimento.
    assert companion == (
        "Quanto impiega il catalogo all'avvio? Motivo del problema?")
    assert "PREVIOUS_TUTOR_ANSWER" not in companion
    assert "temporaneamente indisponibile" not in companion
    assert observed["composition_context"] == previous
    assert answer.detection == "semantic_contextual_help"


def test_independent_question_does_not_mix_recent_context(monkeypatch):
    cards = load_published()
    unit = _knowledge_unit(text="Il catalogo viene verificato all'avvio.")
    current_context = SemanticContext((SourceHit(
        source_type="knowledge", source_id=unit.unit_id, lang="it",
        score=0.86, unit=unit,
    ),), top_score=0.86)
    contextual_context = SemanticContext((SourceHit(
        source_type="knowledge", source_id=unit.unit_id, lang="it",
        score=0.80, unit=unit,
    ),), top_score=0.80)
    observed = {}
    query = "Quanto impiega il Tutor a compilare il catalogo all'avvio?"

    _patch_request_snapshot(monkeypatch, cards=cards, units=(unit,))

    def retrieve(value, *_args, **_kwargs):
        observed.setdefault("retrieval_queries", []).append(value)
        return (contextual_context if "OLD EXCHANGE" in value
                else current_context)

    def compose(**kwargs):
        observed["composition_context"] = kwargs["conversation_context"]
        return Composition("answer", "La fonte descrive la verifica.")

    monkeypatch.setattr("tutor.service.retrieve_sources", retrieve)
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_a, **_k: ModeDecision("EXPLAIN", True))
    monkeypatch.setattr("tutor.compose.compose_answer", compose)
    answer = answer_request(TutorRequest(
        query, "it", _principal(), conversation_context="OLD EXCHANGE",
    ))

    assert answer is not None and answer.esito == "fondata"
    assert observed["retrieval_queries"][0] == query
    assert observed["composition_context"] == ""


def test_conversation_cache_is_ephemeral_and_principal_isolated():
    from tutor.conversation import _clear_for_tests, recent_context, remember
    from tutor.models import TutorAnswer

    _clear_for_tests()
    first = _principal("user")
    second = TutorPrincipal(
        user_id="other", actor=first.actor, audience=first.audience,
        channel=first.channel, conversation_id=first.conversation_id,
    )
    request = TutorRequest("Prima domanda", "it", first)
    remember(request, TutorAnswer("fondata", "Prima risposta"))

    assert "Prima domanda" in recent_context(first)
    assert "Prima risposta" in recent_context(first)
    assert recent_context(second) == ""
    _clear_for_tests()
    assert recent_context(first) == ""


def test_conversation_cache_never_retains_live_or_incomplete_answers():
    from tutor.conversation import _clear_for_tests, recent_context, remember
    from tutor.models import TutorAnswer

    _clear_for_tests()
    principal = _principal("user")
    request = TutorRequest("Qual è lo stato corrente?", "it", principal)
    remember(request, TutorAnswer(
        "fondata", "Stato osservato.",
        probe_statuses=(("owned_device_state", "ok"),),
    ))
    assert recent_context(principal) == ""
    remember(request, TutorAnswer(
        "fondata", "Risposta parziale.", gap_reason="composer_incomplete",
    ))
    assert recent_context(principal) == ""


def test_help_preserves_pending_with_explicit_notice(monkeypatch):
    _route(monkeypatch, "attivita-programmate")
    monkeypatch.setattr(
        "tutor.service._msg", lambda key: "PENDING" if key ==
        "MSG_TUTOR_PENDING_PRESERVED" else key)
    request = TutorRequest(
        "Come faccio a creare un task che legga le mie email?", "it",
        _principal(), has_pending=True,
    )
    answer = answer_request(request)
    assert answer is not None and answer.answer_md.endswith("PENDING")


def test_tutor_can_be_disabled_without_second_path(monkeypatch):
    monkeypatch.setenv("METNOS_TUTOR", "0")
    request = TutorRequest(
        "Come faccio a creare un task che legga le mie email?", "it",
        _principal(),
    )
    assert answer_request(request) is None


def test_boundary_reports_unavailable_and_never_executes_failed_help(
        monkeypatch):
    import tutor
    from tutor_boundary import answer as boundary_answer

    monkeypatch.setattr(
        tutor, "answer_request",
        lambda _request: (_ for _ in ()).throw(
            ImportError("simulated stale module")),
    )
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_a, **_k: ModeDecision("EXPLAIN", True))
    monkeypatch.setattr(
        "tutor_boundary._msg",
        lambda key: {
            "MSG_TUTOR_UNAVAILABLE": (
                "Il tutor locale non è disponibile. "
                "Nessuna operazione è stata eseguita."),
            "MSG_TUTOR_PENDING_PRESERVED": "La richiesta in attesa resta intatta.",
        }[key],
    )

    result = boundary_answer(
        "Come si inseriscono le credenziali di una mailbox?",
        _principal(),
        has_pending=True,
    )

    assert result is not None
    assert result.esito == "tutor_error"
    assert result.detection == "semantic_unavailable"
    assert "Nessuna operazione è stata eseguita" in result.answer_md
    assert "resta intatta" in result.answer_md


def test_boundary_failure_preserves_operational_fallthrough(monkeypatch):
    import tutor
    from tutor_boundary import answer as boundary_answer

    monkeypatch.setattr(
        tutor, "answer_request",
        lambda _request: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_a, **_k: ModeDecision("ACT", True))

    result = boundary_answer("Leggi le mie email", _principal())
    assert result is not None and result.esito == "tutor_error"


def test_catalog_failure_cannot_steal_a_semantic_action(monkeypatch):
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_a, **_k: ModeDecision("ACT", True))
    monkeypatch.setattr(
        "tutor.catalog.load_request_snapshot",
        lambda: (_ for _ in ()).throw(RuntimeError("catalog unavailable")),
    )
    request = TutorRequest(
        "Per favore recupera i dati correnti", "it", _principal())
    assert answer_request(request) is None


def test_catalog_failure_is_unavailable_only_after_semantic_explain(
        monkeypatch):
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_a, **_k: ModeDecision("EXPLAIN", True))
    monkeypatch.setattr(
        "tutor.catalog.load_request_snapshot",
        lambda: (_ for _ in ()).throw(RuntimeError("catalog unavailable")),
    )
    monkeypatch.setattr("tutor.service._msg", lambda key: key)
    request = TutorRequest(
        "Illustra il funzionamento", "it", _principal())
    answer = answer_request(request)
    assert answer is not None and answer.esito == "tutor_error"
    assert answer.answer_md == "MSG_TUTOR_UNAVAILABLE"




def test_tutor_telemetry_is_minimized(tmp_path, monkeypatch):
    import json
    import config
    from tutor.telemetry import record

    _route(monkeypatch, "attivita-programmate")
    monkeypatch.setattr(config, "PATH_TURNS", tmp_path)
    query = "Come faccio a creare un task che legga le mie email?"
    request = TutorRequest(query, "it", _principal())
    answer = answer_request(request)
    stored = record(request, answer)
    row = json.loads(next(tmp_path.glob("*.jsonl")).read_text().splitlines()[0])
    assert stored.turn_id == row["turn_id"]
    assert row["mode"] == "tutor"
    assert row["user_query"] == ""
    assert query not in json.dumps(row, ensure_ascii=False)
    assert row["tutor_query_hash"].startswith("sha256:")
    assert row["tutor_source_ids"] == [
        "card:attivita-programmate:it"]


def test_signed_catalog_builds_cards_and_vectors(tmp_path, monkeypatch):
    catalog = _patch_catalog_paths(monkeypatch, tmp_path)
    catalog.compile_catalog()
    assert catalog.verify_catalog()
    assert len(catalog.load_cards()) == 4
    index = catalog.load_vector_index()
    assert index.matrix.shape == (8, 8)
    assert np.isfinite(index.matrix).all()
    units = catalog.load_knowledge_units()
    # The isolated signing-key fixture intentionally makes live executor
    # manifests inadmissible; the explicitly declared document corpus remains.
    assert len(units) >= 400
    knowledge_index = catalog.load_knowledge_vector_index()
    assert knowledge_index.matrix.shape == (len(units), 8)
    assert np.isfinite(knowledge_index.matrix).all()


def test_tutor_input_stamp_detects_same_size_same_mtime_doc_change(
        tmp_path, monkeypatch):
    """A published-page edit must invalidate F2 even in reproducible builds."""

    from tutor import catalog

    source = tmp_path / "published.html"
    source.write_text("alpha", encoding="utf-8")
    original = source.stat()
    monkeypatch.setattr(catalog, "_source_files", lambda: (source,))
    monkeypatch.setattr(catalog, "embedding_fingerprint", lambda: "sha256:model")
    monkeypatch.setattr(catalog, "executor_catalog_stamp", lambda: "sha256:tools")

    before = catalog.input_stamp()
    source.write_text("omega", encoding="utf-8")
    os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))

    assert source.stat().st_size == original.st_size
    assert source.stat().st_mtime_ns == original.st_mtime_ns
    assert catalog.input_stamp() != before


def test_tutor_input_stamp_includes_compiler_implementation(monkeypatch):
    from tutor import catalog

    monkeypatch.setattr(catalog, "_source_files", lambda: ())
    monkeypatch.setattr(catalog, "embedding_fingerprint", lambda: "sha256:model")
    monkeypatch.setattr(catalog, "executor_catalog_stamp", lambda: "sha256:tools")
    monkeypatch.setattr(
        catalog, "_compiler_implementation_digest", lambda: "sha256:compiler-a")
    before = catalog.input_stamp()
    monkeypatch.setattr(
        catalog, "_compiler_implementation_digest", lambda: "sha256:compiler-b")

    assert catalog.input_stamp() != before


def test_changed_input_rebuilds_a_complete_tutor_candidate(
        tmp_path, monkeypatch):
    catalog = _patch_catalog_paths(monkeypatch, tmp_path)
    catalog.compile_catalog()
    expected = len(build_knowledge_units())
    observed: list[int] = []
    real_build = catalog._build_candidate

    def capture(path, source_digest, input_digest, knowledge):
        observed.append(len(knowledge))
        return real_build(path, source_digest, input_digest, knowledge)

    monkeypatch.setattr(catalog, "_build_candidate", capture)
    monkeypatch.setattr(catalog, "input_stamp", lambda: "sha256:docs-changed")
    catalog.compile_catalog()

    assert observed == [expected]
    assert int(catalog._metadata(catalog.CATALOG_PATH)["knowledge_count"]) == expected


def test_signed_catalog_last_good_recovery(tmp_path, monkeypatch):
    catalog = _patch_catalog_paths(monkeypatch, tmp_path)
    catalog.compile_catalog()
    catalog.compile_catalog(force=True)
    assert catalog.BACKUP_PATH.is_file()
    catalog.SIGNATURE_PATH.write_bytes(b"invalid")
    monkeypatch.setattr(catalog, "_CACHE", None)
    monkeypatch.setattr(catalog, "_VECTOR_CACHE", None)
    monkeypatch.setattr(catalog, "_KNOWLEDGE_CACHE", None)
    monkeypatch.setattr(catalog, "_KNOWLEDGE_VECTOR_CACHE", None)
    catalog.compile_catalog()
    assert catalog.verify_catalog()
    assert len(catalog.load_cards()) == 4


def test_failed_catalog_candidate_does_not_replace_current(tmp_path, monkeypatch):
    catalog = _patch_catalog_paths(monkeypatch, tmp_path)
    catalog.compile_catalog()
    admitted = catalog.CATALOG_PATH.read_bytes()
    admitted_sig = catalog.SIGNATURE_PATH.read_bytes()

    monkeypatch.setattr(
        catalog, "_build_candidate",
        lambda *_a, **_k: (_ for _ in ()).throw(ValueError("candidate rejected")),
    )
    with pytest.raises(ValueError, match="candidate rejected"):
        catalog.compile_catalog(force=True)
    assert catalog.CATALOG_PATH.read_bytes() == admitted
    assert catalog.SIGNATURE_PATH.read_bytes() == admitted_sig
    assert catalog.verify_catalog()


def test_inputs_changed_during_build_cannot_replace_current(
        tmp_path, monkeypatch):
    catalog = _patch_catalog_paths(monkeypatch, tmp_path)
    catalog.compile_catalog()
    admitted = catalog.CATALOG_PATH.read_bytes()
    admitted_sig = catalog.SIGNATURE_PATH.read_bytes()
    stamps = iter(("sha256:before-build", "sha256:during-build"))
    monkeypatch.setattr(catalog, "input_stamp", lambda: next(stamps))

    with pytest.raises(RuntimeError, match="inputs changed during catalog build"):
        catalog.compile_catalog(force=True)

    assert catalog.CATALOG_PATH.read_bytes() == admitted
    assert catalog.SIGNATURE_PATH.read_bytes() == admitted_sig
    assert catalog.verify_catalog()


def test_interrupted_catalog_candidates_are_cleaned_under_next_build(
        tmp_path, monkeypatch):
    catalog = _patch_catalog_paths(monkeypatch, tmp_path)
    stale_db = catalog.CATALOG_PATH.parent / ".tutor_catalog.interrupted.sqlite"
    stale_sig = catalog.CATALOG_PATH.parent / ".tutor_catalog.interrupted.sqlite.sig"
    stale_db.write_bytes(b"")
    stale_sig.write_bytes(b"")

    catalog.compile_catalog()

    assert not stale_db.exists()
    assert not stale_sig.exists()
    assert catalog.verify_catalog()


def test_stale_process_cannot_recompile_with_new_file_identity(
        tmp_path, monkeypatch):
    catalog = _patch_catalog_paths(monkeypatch, tmp_path)
    admitted_hash = catalog.compile_catalog()
    admitted = catalog.CATALOG_PATH.read_bytes()
    monkeypatch.setattr(
        catalog, "_compiler_implementation_digest", lambda: "sha256:changed")
    monkeypatch.setattr(
        catalog, "_build_candidate",
        lambda *_a, **_k: pytest.fail("stale process attempted compilation"),
    )
    assert catalog.compile_catalog(force=True) == admitted_hash
    assert catalog.CATALOG_PATH.read_bytes() == admitted
    assert catalog.verify_catalog()


def test_http_boundary_returns_tutor_without_blocking_event_loop(monkeypatch):
    import http_routes_agent

    _procedure_unit_context(monkeypatch, audience="instance_admin")
    monkeypatch.setattr(http_routes_agent, "_http_has_pending", lambda *_: True)
    async def run_inline(function, *args, **kwargs):
        return function(*args, **kwargs)
    # The managed test sandbox does not deliver ThreadPoolExecutor callbacks;
    # preserve the awaitable boundary while testing its functional contract.
    monkeypatch.setattr(http_routes_agent.asyncio, "to_thread", run_inline)
    result = asyncio.run(http_routes_agent._apply_tutor_http(
        {"role": "admin", "device_id": None},
        query="Come approvo una proposta di modifica?",
        actor="host", user_id="u1", conversation_id="c1",
        sender_id="http:host:c1",
    ))
    assert result is not None and result.esito == "fondata"
    assert len(result.turn_id) == 16
    assert "/admin/changes" in result.answer_md
    assert "Fermati se" in result.answer_md


def test_tutor_boundary_sensitive_direct_call_falls_through(monkeypatch):
    from contextlib import nullcontext

    import tutor_boundary

    principal = tutor_boundary.http_principal(
        role="admin", device_id=None, actor="host", user_id="u1")
    monkeypatch.setattr(
        "user_lifecycle.owner_session", lambda _owner: nullcontext())
    monkeypatch.setattr(
        "credential_intake.contains_sensitive_input", lambda _query: True)

    assert tutor_boundary._answer(
        "token=secret", principal,
        has_pending=False, pending_sender_id="http:u1:c1",
    ) is None


def test_tutor_boundary_keeps_ready_explanation_near_deadline(monkeypatch):
    import time
    from contextlib import nullcontext

    import tutor_boundary
    from tutor.models import TutorAnswer

    principal = tutor_boundary.http_principal(
        role="admin", device_id=None, actor="host", user_id="u1")
    ready = TutorAnswer(esito="fondata", answer_md="Risposta fondata.")
    monkeypatch.setattr(
        "user_lifecycle.owner_session", lambda _owner: nullcontext())
    monkeypatch.setattr(
        "credential_intake.contains_sensitive_input", lambda _query: False)
    monkeypatch.setattr("i18n.current_lang", lambda: "it")
    monkeypatch.setattr(
        "tutor.deadline.new_deadline", lambda: time.monotonic() + 0.5)
    monkeypatch.setattr("tutor.conversation.recent_context", lambda _p: "")
    monkeypatch.setattr("tutor.conversation.remember", lambda *_a: None)
    monkeypatch.setattr("tutor.answer_request", lambda _request: ready)
    monkeypatch.setattr(tutor_boundary, "record_async", lambda _r, a: a)

    assert tutor_boundary._answer(
        "Come funziona?", principal,
        has_pending=False, pending_sender_id="http:u1:c1",
    ) == ready


def test_tutor_boundary_reuses_resumable_turn_id(monkeypatch):
    from contextlib import nullcontext

    import tutor_boundary
    from tutor.models import TutorAnswer

    principal = tutor_boundary.http_principal(
        role="admin", device_id=None, actor="host", user_id="u1")
    monkeypatch.setattr(
        "user_lifecycle.owner_session", lambda _owner: nullcontext())
    monkeypatch.setattr(
        "credential_intake.contains_sensitive_input", lambda _query: False)
    monkeypatch.setattr("i18n.current_lang", lambda: "it")
    monkeypatch.setattr("tutor.conversation.recent_context", lambda _p: "")
    monkeypatch.setattr("tutor.conversation.recent_question", lambda _p: "")
    monkeypatch.setattr("tutor.conversation.remember", lambda *_a: None)
    monkeypatch.setattr(
        "tutor.answer_request",
        lambda _request: TutorAnswer(
            esito="fondata", answer_md="Risposta fondata."),
    )
    observed = {}

    def record(_request, answer):
        observed["turn_id"] = answer.turn_id
        return answer

    monkeypatch.setattr(tutor_boundary, "record_async", record)
    result = tutor_boundary._answer(
        "Come funziona?", principal,
        has_pending=False, pending_sender_id="http:u1:c1",
        turn_id_hint="0123456789abcdef",
    )

    assert result.turn_id == "0123456789abcdef"
    assert observed["turn_id"] == result.turn_id


def test_resumable_preprocess_defers_tutor_until_after_202(monkeypatch):
    import agent_runtime
    import active_sessions
    import http_routes_agent

    class Request(dict):
        content_type = "application/json"
        path = "/agent/turn/submit"

        async def json(self):
            return {
                "query": "Come funziona il Tutor?",
                "conversation_id": "c1",
                "device_token": "writer-token",
            }

    async def user_id(_request):
        return "u1"

    async def must_not_run(*_args, **_kwargs):
        pytest.fail("Tutor ran before the resumable 202 boundary")

    monkeypatch.setattr(http_routes_agent, "_resolve_actor", lambda *_a: "host")
    monkeypatch.setattr(http_routes_agent, "_resolve_session_user_id", user_id)
    monkeypatch.setattr(http_routes_agent, "_apply_tutor_http", must_not_run)
    monkeypatch.setattr(
        http_routes_agent, "_apply_dialog_cancel", lambda *_a, **_k: None)
    monkeypatch.setattr(
        http_routes_agent, "_apply_dialog_pending", lambda *_a, **_k: None)
    monkeypatch.setattr(
        http_routes_agent, "scrub_sensitive_text", lambda query: (query, 0))
    monkeypatch.setattr(
        active_sessions, "validate_writer", lambda *_a, **_k: (True, ""))
    monkeypatch.setattr(
        agent_runtime, "prepare_credentials_for_routing",
        lambda query: pytest.fail(
            "credential preparation ran before deferred pending resolution"),
    )

    error, data = asyncio.run(http_routes_agent._preprocess_turn(
        Request(role="admin")))

    assert error is None
    assert data["_tutor_deferred"] is True
    assert data["_deferred_query"] == "Come funziona il Tutor?"
    assert data["original_query"] == "Come funziona il Tutor?"


def test_turn_submit_returns_202_while_deferred_tutor_is_running(monkeypatch):
    import json

    import http_routes_agent
    from turn_events import TurnEventLog

    started = asyncio.Event()
    release = asyncio.Event()

    class Request(dict):
        app = {}

    class Pool:
        def __init__(self):
            self.releases = []

        def release(self, reservation, completed=False):
            self.releases.append((reservation, completed))

    pool = Pool()
    reservation = object()
    deferred = http_routes_agent._preprocessed_turn_data(
        query_for_run="Come funziona il Tutor?",
        immediate_msg=None,
        immediate_source="",
        immediate_turn_id="",
        actor="host",
        user_id="u1",
        conversation_id="c1",
        sender_id="http:u1:c1",
        reference_images=[],
        original_query="Come funziona il Tutor?",
        tutor_deferred=True,
        deferred_query="Come funziona il Tutor?",
    )

    async def preprocess(_request):
        return None, deferred

    async def reserve(_request, _actor):
        return reservation

    async def resolve(*_args, **kwargs):
        assert kwargs["turn_id_hint"]
        started.set()
        await release.wait()
        return http_routes_agent._preprocessed_turn_data(
            query_for_run=kwargs["safe_original_query"],
            immediate_msg="Risposta Tutor.",
            immediate_source="tutor",
            immediate_turn_id=kwargs["turn_id_hint"],
            actor=kwargs["actor"],
            user_id=kwargs["user_id"],
            conversation_id=kwargs["conversation_id"],
            sender_id=kwargs["sender_id"],
            reference_images=[],
            original_query=kwargs["safe_original_query"],
        )

    monkeypatch.setattr(http_routes_agent, "_preprocess_turn", preprocess)
    monkeypatch.setattr(http_routes_agent, "_reserve_turn", reserve)
    monkeypatch.setattr(http_routes_agent, "_turn_pool", lambda _request: pool)
    monkeypatch.setattr(
        http_routes_agent, "_resolve_open_http_turn", resolve)

    async def scenario():
        response = await asyncio.wait_for(
            http_routes_agent.turn_submit(Request()), timeout=0.2)
        assert response.status == 202
        payload = json.loads(response.body)
        turn_id = payload["turn_id"]
        await asyncio.wait_for(started.wait(), timeout=0.2)
        before = TurnEventLog.get().snapshot(turn_id)
        assert before is not None and before["closed"] is False
        assert before["events"] == []
        release.set()
        for _ in range(20):
            await asyncio.sleep(0)
            after = TurnEventLog.get().snapshot(turn_id)
            if after is not None and after["closed"]:
                break
        assert after is not None and after["closed"] is True
        assert after["events"][-1]["event_type"] == "final"
        assert after["events"][-1]["payload"]["final_message"] == (
            "Risposta Tutor.")

    asyncio.run(scenario())
    assert pool.releases == [(reservation, True)]


def test_http_tutor_pool_saturation_falls_through_without_consuming_pending(
        monkeypatch):
    import agent_runtime
    import http_routes_agent
    from channels import daemon as daemon_mod

    class Request(dict):
        content_type = "application/json"
        path = "/agent/turn"

        async def json(self):
            return {
                "query": "Trova i file duplicati",
                "conversation_id": "c1",
            }

    async def user_id(_request):
        return "u1"

    async def tutor_busy(*_args, **_kwargs):
        raise http_routes_agent.TutorBoundaryBusy("saturated")

    pending_calls = []

    def pending(*_args, **kwargs):
        pending_calls.append(kwargs)
        return None

    monkeypatch.setattr(http_routes_agent, "_resolve_actor", lambda *_a: "host")
    monkeypatch.setattr(http_routes_agent, "_resolve_session_user_id", user_id)
    monkeypatch.setattr(http_routes_agent, "_apply_tutor_http", tutor_busy)
    monkeypatch.setattr(http_routes_agent, "_apply_dialog_cancel", lambda *_a, **_k: None)
    monkeypatch.setattr(http_routes_agent, "_apply_dialog_pending", pending)
    monkeypatch.setattr(
        http_routes_agent, "scrub_sensitive_text", lambda query: (query, ()))
    monkeypatch.setattr(daemon_mod, "_classify_yes_no", lambda _query: None)
    monkeypatch.setattr(
        agent_runtime, "prepare_credentials_for_routing",
        lambda query: (query, [], ()),
    )

    error, data = asyncio.run(http_routes_agent._preprocess_turn(
        Request(role="admin")))

    assert error is None
    assert data["query_for_run"] == "Trova i file duplicati"
    assert data["immediate_msg"] is None
    # Only the closed-schema pre-pass ran; the open pending was preserved.
    assert len(pending_calls) == 1
    assert pending_calls[0]["admit_only_valid_closed"] is True


def test_async_boundary_preserves_only_valid_persisted_tutor_id():
    import http_routes_agent

    persisted = "0123456789abcdef"
    assert http_routes_agent._turn_id_for_preprocessed({
        "immediate_source": "tutor", "immediate_turn_id": persisted,
    }) == persisted
    assert http_routes_agent._turn_id_for_preprocessed({
        "immediate_source": "pending", "immediate_turn_id": persisted,
    }) != persisted
    assert http_routes_agent._turn_id_for_preprocessed({
        "immediate_source": "tutor", "immediate_turn_id": "../invalid",
    }) != "../invalid"


def test_telegram_boundary_short_circuits_before_run_turn(monkeypatch):
    from channels import InboundMessage
    from channels import daemon as daemon_mod

    _route(monkeypatch, "attivita-programmate")

    class Channel:
        name = "telegram"
        default_chat_id = ""

        def __init__(self):
            self.sent = []

        def send(self, recipient, message):
            self.sent.append((recipient, message.text))
            return {"ok": True}

    channel = Channel()
    daemon = daemon_mod.ChannelDaemon(
        channel,
        run_turn=lambda *args, **kwargs: pytest.fail("planner was invoked"),
        bootstrap_default_sender=False,
    )
    monkeypatch.setattr(
        daemon_mod.pairing, "get_pairing",
        lambda *_: SimpleNamespace(autonomy_level="Full", actor="host"),
    )
    monkeypatch.setattr(daemon_mod.pairing, "touch_last_seen", lambda *_: None)
    monkeypatch.setattr(
        "users.find_user_by_recipient",
        lambda *_a, **_k: {
            "id": "u1", "name": "host", "role": "host",
            "autonomy_level": "full",
        },
    )
    monkeypatch.setattr(daemon, "_callback_principal", lambda _msg: {
        "user_id": "u1", "sender_id": "42", "actor": "host",
        "role": "host", "autonomy": "Full",
    })
    monkeypatch.setattr(
        daemon_mod, "_cap_pending_load", lambda *_a, **_k: None)
    message = InboundMessage(
        channel="telegram", sender_id="42",
        text="Come faccio a creare un task che legga le mie email?",
        message_id="m1", received_at=0.0, extra={},
    )
    result = daemon.handle_message(message)
    assert result["ok"] is True
    assert result["tutor"] == "consolidata"
    assert channel.sent and "attività programmata" in channel.sent[0][1]
