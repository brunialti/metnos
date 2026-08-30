from __future__ import annotations

from pathlib import Path

import pytest

from executable_lexicon_census import scan_file, scan_runtime


def _scan_mutant(tmp_path: Path, relative: str, source_text: str):
    source = tmp_path / Path(relative).name
    source.write_text(source_text, encoding="utf-8")
    return [
        issue for issue in scan_file(source, relative_path=relative)
        if issue.code != "LEXICON_STALE_INVARIANT"
    ]


def test_rejects_private_table_and_private_prefilter_import(tmp_path: Path) -> None:
    source = tmp_path / "consumer.py"
    source.write_text(
        "from prefilter import _OBJECT_HINTS\n"
        "_ACTION_MARKERS = {'crea', 'create'}\n",
        encoding="utf-8",
    )

    assert {issue.code for issue in scan_file(source)} == {
        "LEXICON_LITERAL", "LEXICON_PRIVATE_IMPORT",
    }


def test_rejects_intent_regex_even_without_word_or_pattern_in_name(
    tmp_path: Path,
) -> None:
    source = tmp_path / "consumer.py"
    source.write_text(
        "_PROPOSE_INTENT_RE = {'proponi', 'suggest'}\n",
        encoding="utf-8",
    )

    issues = scan_file(source)

    assert [(issue.code, issue.symbol) for issue in issues] == [
        ("LEXICON_LITERAL", "_PROPOSE_INTENT_RE"),
    ]


def test_rejects_function_local_natural_language_table(tmp_path: Path) -> None:
    source = tmp_path / "consumer.py"
    source.write_text(
        "def resolve():\n"
        "    action_aliases = {'apri': 'open', 'chiudi': 'close'}\n"
        "    return action_aliases\n",
        encoding="utf-8",
    )

    issues = scan_file(source)

    assert [(issue.code, issue.symbol) for issue in issues] == [
        ("LEXICON_LITERAL", "action_aliases"),
    ]


def test_ignores_function_local_dynamic_concept_loader(tmp_path: Path) -> None:
    source = tmp_path / "consumer.py"
    source.write_text(
        "def resolve():\n"
        "    action_lexicon = load_family('sites.action_verb')\n"
        "    return action_lexicon\n",
        encoding="utf-8",
    )

    assert scan_file(source) == []


def test_rejects_neutral_lookup_name(tmp_path: Path) -> None:
    source = tmp_path / "consumer.py"
    source.write_text("LOOKUP = {'apri': 'open'}\n", encoding="utf-8")

    assert [(issue.code, issue.symbol) for issue in scan_file(source)] == [
        ("LEXICON_LITERAL", "LOOKUP"),
    ]


def test_rejects_language_sniffing_branch(tmp_path: Path) -> None:
    source = tmp_path / "consumer.py"
    source.write_text(
        "import re\n"
        "def render(query):\n"
        "    is_italian = bool(re.search(r'crea|cartella|foglio', query))\n"
        "    return 'fatto' if is_italian else 'done'\n",
        encoding="utf-8",
    )

    assert ("LEXICON_LITERAL", "is_italian") in {
        (issue.code, issue.symbol) for issue in scan_file(source)
    }


@pytest.mark.parametrize(
    ("relative", "source_text", "expected_code", "expected_symbol"),
    [
        (
            "skill_wrapper.py",
            "_TRUTHY = {'1', 'true', 'yes', 'si', 'attiva'}\n",
            "LEXICON_LITERAL", "_TRUTHY",
        ),
        (
            "skill_admin.py",
            "def handle_set_skills(enabled):\n"
            "    s = str(enabled).lower()\n"
            "    return s in ('true', 'yes', 'si', 'attiva')\n",
            "LEXICON_INLINE_COMPARISON", "handle_set_skills::s",
        ),
        (
            "store_entries.py",
            "_AFFINITY = ['store', 'archivio', 'raccolta', 'collection']\n",
            "LEXICON_LITERAL", "_AFFINITY",
        ),
        (
            "engine/dispatch.py",
            "def _normalize_result_folder_exclusion(folded):\n"
            "    excludes = any(token in folded for token in "
            "('esclud', 'senza includ', 'exclude', 'without includ'))\n"
            "    return excludes\n",
            "LEXICON_INLINE_COLLECTION",
            "_normalize_result_folder_exclusion::token",
        ),
        (
            "proposal_evaluator.py",
            "_REFORMULATION_TEMPLATES_IT = "
            "('{q}', 'puoi {q}?', 'vorrei {q}')\n",
            "LEXICON_LITERAL", "_REFORMULATION_TEMPLATES_IT",
        ),
        (
            "proposal_evaluator.py",
            "def _bow_intent_simple(q):\n"
            "    return any(t in q for t in "
            "('trova', 'cerca', 'find', 'search'))\n",
            "LEXICON_INLINE_COLLECTION", "_bow_intent_simple::t",
        ),
        (
            "http_routes_agent.py",
            "def _consume_http_get_inputs_response(query):\n"
            "    text_norm = query.strip().lower()\n"
            "    return text_norm in ('annulla', 'cancel', 'abort', 'stop')\n",
            "LEXICON_INLINE_COMPARISON",
            "_consume_http_get_inputs_response::text_norm",
        ),
        (
            "engine/executor.py",
            "import re\n"
            "def _prepare_static_read_args(query):\n"
            "    return bool(re.search(r'(esplor|ricorsiv|crawl|entire site)', "
            "query))\n",
            "LEXICON_INLINE_REGEX",
            "_prepare_static_read_args::<inline-regex>",
        ),
        (
            "google_places_client.py",
            "_AUTO_GOOGLE_TYPE = "
            "{'farmacia': 'pharmacy', 'ristorante': 'restaurant'}\n",
            "LEXICON_LITERAL", "_AUTO_GOOGLE_TYPE",
        ),
        (
            "prefilter_strategies/constraint.py",
            "def _extract_constraints(query):\n"
            "    return any(m in query for m in "
            "('google', 'gmail', 'drive', 'workspace'))\n",
            "LEXICON_INLINE_COLLECTION", "_extract_constraints::m",
        ),
        (
            "telos_lenses/_base.py",
            "import re\n"
            "def paternalism_check(text):\n"
            "    return bool(re.search(r'tell the user|dire all utente', text))\n",
            "LEXICON_INLINE_REGEX", "paternalism_check::<inline-regex>",
        ),
        (
            "change_intent_adapters/telos.py",
            "import re\n"
            "def _infer_arg_from_action(action):\n"
            "    return re.search(r'(arg|parametro|argomento) ([a-z_]+)', "
            "action)\n",
            "LEXICON_INLINE_REGEX", "_infer_arg_from_action::<inline-regex>",
        ),
        (
            "telos_proposals_store.py",
            "def _semantic_overlap_query(q):\n"
            "    data = {'il', 'la', 'the', 'and', 'con', 'with'}\n"
            "    return data\n",
            "LEXICON_LITERAL", "data",
        ),
        (
            "admin/promotions_review.py",
            "_OPTIONS_PROMOTED_GRACE = "
            "(('confirm', 'Conferma promozione'), ('skip', 'Skip'))\n",
            "LEXICON_LITERAL", "_OPTIONS_PROMOTED_GRACE",
        ),
        (
            "extract_entries.py",
            "_RELEVANCE_GENERIC_TOKENS = "
            "frozenset({'documenti', 'documents', 'cartella', 'folder'})\n",
            "LEXICON_LITERAL", "_RELEVANCE_GENERIC_TOKENS",
        ),
        (
            "describe_entries.py",
            "import re\n"
            "def _append_document_audit(q):\n"
            "    wants_conflicts = bool(re.search("
            "r'contradditt|contradict|conflict', q))\n"
            "    return wants_conflicts\n",
            "LEXICON_INLINE_LITERAL",
            "_append_document_audit::<inline-regex>",
        ),
    ],
)
def test_rejects_historical_p1_p2_boundary_mutants(
    tmp_path: Path, relative: str, source_text: str,
    expected_code: str, expected_symbol: str,
) -> None:
    issues = _scan_mutant(tmp_path, relative, source_text)

    assert (expected_code, expected_symbol) in {
        (issue.code, issue.symbol) for issue in issues
    }


def test_rejects_neutral_local_mapping_without_name_heuristic(
    tmp_path: Path,
) -> None:
    issues = _scan_mutant(
        tmp_path,
        "prefilter.py",
        "def unrelated_boundary(value):\n"
        "    data = {'apri': 'open'}\n"
        "    return data.get(value)\n",
    )

    assert ("LEXICON_NEUTRAL_TABLE", "data") in {
        (issue.code, issue.symbol) for issue in issues
    }


def test_rejects_neutral_mapping_in_newly_discovered_module(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    source = runtime_root / "brand_new_runtime_boundary.py"
    source.write_text(
        "def resolve(value):\n"
        "    data = {'apri': 'open'}\n"
        "    return data.get(value)\n",
        encoding="utf-8",
    )
    issues = scan_runtime(runtime_root)

    assert ("LEXICON_NEUTRAL_TABLE", "data") in {
        (issue.code, issue.symbol) for issue in issues
    }


def test_rejects_neutral_set_in_prefilter(tmp_path: Path) -> None:
    issues = _scan_mutant(
        tmp_path,
        "prefilter.py",
        "def resolve(value):\n"
        "    data = {'apri', 'open'}\n"
        "    return value in data\n",
    )

    assert ("LEXICON_NEUTRAL_COLLECTION", "data") in {
        (issue.code, issue.symbol) for issue in issues
    }


def test_rejects_reintroduced_nested_skill_admin_affinity(
    tmp_path: Path,
) -> None:
    issues = _scan_mutant(
        tmp_path,
        "skill_admin.py",
        "BUILTIN_INPROC_SPECS = [\n"
        "    {'name': 'list_skills',\n"
        "     'affinity': ['skill', 'capacita', 'capability']},\n"
        "]\n",
    )

    assert ("LEXICON_NESTED_TABLE", "BUILTIN_INPROC_SPECS") in {
        (issue.code, issue.symbol) for issue in issues
    }


def test_rejects_nested_affinity_in_any_new_module(tmp_path: Path) -> None:
    issues = _scan_mutant(
        tmp_path,
        "brand_new_runtime_boundary.py",
        "SPEC = {'name': 'resolve', 'affinity': ['apri', 'open']}\n",
    )

    assert ("LEXICON_NESTED_TABLE", "SPEC") in {
        (issue.code, issue.symbol) for issue in issues
    }


def test_rejects_new_seed_like_module_not_in_exact_seed_inventory(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    source = runtime_root / "detection_lexicon_seed_unlisted.py"
    source.write_text(
        "def resolve(value):\n"
        "    data = {'abrir': 'open'}\n"
        "    return data.get(value)\n",
        encoding="utf-8",
    )

    assert ("LEXICON_NEUTRAL_TABLE", "data") in {
        (issue.code, issue.symbol) for issue in scan_runtime(runtime_root)
    }


def test_known_seed_syntax_error_is_a_census_finding(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "detection_lexicon_seed.py").write_text(
        "def broken(:\n", encoding="utf-8",
    )

    assert ("LEXICON_PARSE", "") in {
        (issue.code, issue.symbol) for issue in scan_runtime(runtime_root)
    }


@pytest.mark.parametrize(
    "declaration",
    [
        "data = {'abrir': 'open'}",
        "data = {'abrir'}",
        "data = ['abrir']",
        "data = ('abrir',)",
        "data = {'verbs': ['abrir', 'cerrar']}",
    ],
)
def test_rejects_monolingual_and_nested_neutral_containers(
    tmp_path: Path, declaration: str,
) -> None:
    issues = _scan_mutant(
        tmp_path,
        "brand_new_runtime_boundary.py",
        "def resolve(value):\n"
        f"    {declaration}\n"
        "    return data.get(value) if isinstance(data, dict) "
        "else value in data\n",
    )

    assert any(issue.code.startswith("LEXICON_NEUTRAL_") for issue in issues)


def test_rejects_neutral_container_passed_to_helper(tmp_path: Path) -> None:
    issues = _scan_mutant(
        tmp_path,
        "brand_new_runtime_boundary.py",
        "def resolve(value):\n"
        "    data = {'abrir'}\n"
        "    return matches(value, data)\n",
    )

    assert ("LEXICON_NEUTRAL_COLLECTION", "data") in {
        (issue.code, issue.symbol) for issue in issues
    }


def test_symbol_only_technical_waiver_cannot_hide_changed_literal(
    tmp_path: Path,
) -> None:
    issues = _scan_mutant(
        tmp_path,
        "prefilter.py",
        "import re\n_WORD_RE = re.compile(r'apri|abrir')\n",
    )

    assert ("LEXICON_LITERAL", "_WORD_RE") in {
        (issue.code, issue.symbol) for issue in issues
    }


def test_exact_container_fingerprint_cannot_hide_changed_payload(
    tmp_path: Path,
) -> None:
    issues = _scan_mutant(
        tmp_path,
        "engine/dispatch.py",
        "_READ_INTENT_VERBS = ('abrir',)\n",
    )

    assert ("LEXICON_LITERAL", "_READ_INTENT_VERBS") in {
        (issue.code, issue.symbol) for issue in issues
    }


@pytest.mark.parametrize(
    "source_text",
    [
        "SPEC = {'affinity': load_words()}\n",
        "SPEC = {'affinity': words()}\n",
        "SPEC = {'aff' + 'inity': ['abrir']}\n",
        "KEY = 'aff' + 'inity'\nSPEC = {KEY: load_words()}\n",
    ],
)
def test_rejects_computed_affinity_value_and_constant_folded_key(
    tmp_path: Path, source_text: str,
) -> None:
    issues = _scan_mutant(
        tmp_path, "brand_new_runtime_boundary.py", source_text,
    )

    assert ("LEXICON_NESTED_TABLE", "SPEC") in {
        (issue.code, issue.symbol) for issue in issues
    }


def test_store_dynamic_exception_cannot_borrow_changed_authority(
    tmp_path: Path,
) -> None:
    issues = _scan_mutant(
        tmp_path,
        "store_entries.py",
        "def _store_affinity():\n"
        "    return words()\n"
        "_AFFINITY = _store_affinity()\n"
        "BUILTIN_INPROC_SPECS = [\n"
        "    {'name': 'find_entries', 'affinity': _AFFINITY},\n"
        "    {'name': 'write_entries', 'affinity': _AFFINITY},\n"
        "    {'name': 'delete_entries', 'affinity': _AFFINITY},\n"
        "]\n",
    )

    assert sum(
        issue.code == "LEXICON_NESTED_TABLE" for issue in issues
    ) == 3


@pytest.mark.parametrize(
    "source_text",
    [
        "def resolve(query):\n    return query in {'abrir'}\n",
        "def resolve(query):\n"
        "    return any(word in query for word in ('abrir',))\n",
        "def resolve(query):\n    return {'abrir': 'open'}.get(query)\n",
        "import re\ndef resolve(query):\n"
        "    return bool(re.search(r'abrir|cerrar', query))\n",
    ],
)
def test_rejects_unowned_inline_literal_in_new_module(
    tmp_path: Path, source_text: str,
) -> None:
    issues = _scan_mutant(
        tmp_path, "brand_new_runtime_boundary.py", source_text,
    )

    assert "LEXICON_INLINE_LITERAL" in {issue.code for issue in issues}


@pytest.mark.parametrize(
    "expression",
    [
        "query == ('abrir', 'cerrar')",
        "('abrir', 'cerrar') == query",
        "query != 'abrir'",
        "'abrir' != query",
        "query in ('abrir', 'cerrar')",
        "('abrir', 'cerrar') in query",
    ],
)
def test_rejects_literal_gate_on_either_compare_operand(
    tmp_path: Path, expression: str,
) -> None:
    issues = _scan_mutant(
        tmp_path, "brand_new_runtime_boundary.py",
        f"def resolve(query):\n    return {expression}\n",
    )

    assert "LEXICON_INLINE_LITERAL" in {issue.code for issue in issues}


@pytest.mark.parametrize(
    "source_text",
    [
        "def resolve(query):\n"
        "    return helper(query, ('abrir', 'cerrar'))\n",
        "def resolve(query):\n"
        "    return helper(query, markers=('abrir', 'cerrar'))\n",
        "def resolve(query, markers=('abrir', 'cerrar')):\n"
        "    return helper(query, markers)\n",
        "def resolve(query, *, markers=('abrir', 'cerrar')):\n"
        "    return helper(query, markers)\n",
    ],
)
def test_rejects_literal_container_in_helper_arguments_and_defaults(
    tmp_path: Path, source_text: str,
) -> None:
    issues = _scan_mutant(
        tmp_path, "brand_new_runtime_boundary.py", source_text,
    )

    assert "LEXICON_INLINE_LITERAL" in {issue.code for issue in issues}


def test_rejects_literal_regex_through_constant_def_use(tmp_path: Path) -> None:
    issues = _scan_mutant(
        tmp_path, "brand_new_runtime_boundary.py",
        "import re\n"
        "def resolve(query):\n"
        "    head = r'abrir|'\n"
        "    tail = r'cerrar'\n"
        "    pattern = head + tail\n"
        "    return bool(re.search(pattern, query))\n",
    )

    assert "LEXICON_INLINE_LITERAL" in {issue.code for issue in issues}


@pytest.mark.parametrize(
    "declaration",
    [
        "data = ['abrir'] + ['cerrar']",
        "data = ('abrir',) + ('cerrar',)",
        "data = {'verbs': ['abrir'] + ['cerrar']}",
    ],
)
def test_rejects_constant_concatenation_and_nested_values(
    tmp_path: Path, declaration: str,
) -> None:
    issues = _scan_mutant(
        tmp_path, "brand_new_runtime_boundary.py",
        "def resolve(query):\n"
        f"    {declaration}\n"
        "    return helper(query, data)\n",
    )

    assert any(issue.code.startswith("LEXICON_NEUTRAL_") for issue in issues)


@pytest.mark.parametrize(
    "declaration",
    [
        "data = 'abrir|cerrar'.split('|')",
        "data = json.loads('[\"abrir\", \"cerrar\"]')",
    ],
)
def test_rejects_containers_derived_from_closed_literal_operations(
    tmp_path: Path, declaration: str,
) -> None:
    issues = _scan_mutant(
        tmp_path, "brand_new_runtime_boundary.py",
        "import json\n"
        "def resolve(query):\n"
        f"    {declaration}\n"
        "    return query in data\n",
    )

    assert any(issue.code.startswith("LEXICON_NEUTRAL_") for issue in issues)


@pytest.mark.parametrize(
    "source_text",
    [
        "SPEC = dict(name='resolve', affinity=['abrir'])\n",
        "SPEC = {}\nSPEC['affinity'] = ['abrir']\n",
        "SPEC = {}\nSPEC.update({'affinity': ['abrir']})\n",
        "SPEC = {}\nSPEC.update({'affinity': words()})\n",
        "SPEC = {}\nSPEC.update(affinity=['abrir'])\n",
        "SPEC = {'affinity': []}\nSPEC['affinity'].append('abrir')\n",
        "affinity = []\naffinity.extend(['abrir'])\n",
        "SPEC = {}\nSPEC['affinity'] += ['abrir']\n",
        "SPEC = {}\nSPEC.setdefault('affinity', []).append('abrir')\n",
        "SPEC = {}\nSPEC.setdefault('affinity', []).extend(['abrir'])\n",
        "SPEC = {}\nSPEC.setdefault('affinity', set()).update({'abrir'})\n",
    ],
)
def test_rejects_affinity_construction_and_successive_mutation(
    tmp_path: Path, source_text: str,
) -> None:
    issues = _scan_mutant(
        tmp_path, "brand_new_runtime_boundary.py", source_text,
    )

    assert "LEXICON_NESTED_TABLE" in {issue.code for issue in issues}


def test_discovery_excludes_only_exact_census_and_lexicon_paths(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    nested = runtime_root / "shadow"
    nested.mkdir(parents=True)
    (nested / "detection_lexicon.py").write_text(
        "def resolve(query):\n"
        "    return query in ('abrir', 'cerrar')\n",
        encoding="utf-8",
    )

    issues = scan_runtime(runtime_root)

    assert ("LEXICON_INLINE_LITERAL", "resolve::<inline-membership>") in {
        (issue.code, issue.symbol) for issue in issues
    }


def test_known_seed_allows_registration_but_rejects_direct_gate(
    tmp_path: Path,
) -> None:
    registration_root = tmp_path / "registration" / "runtime"
    registration_root.mkdir(parents=True)
    (registration_root / "detection_lexicon_seed.py").write_text(
        "import detection_lexicon as _dl\n"
        "def register_all():\n"
        "    R = _dl.register\n"
        "    R('example.concept', 'phrases', it=['apri'], en=['open'])\n",
        encoding="utf-8",
    )
    gate_root = tmp_path / "gate" / "runtime"
    gate_root.mkdir(parents=True)
    (gate_root / "detection_lexicon_seed.py").write_text(
        "def register_preview(query):\n"
        "    return query in ('apri', 'open')\n",
        encoding="utf-8",
    )

    assert scan_runtime(registration_root) == []
    assert "LEXICON_SEED_GATE" in {
        issue.code for issue in scan_runtime(gate_root)
    }


def test_known_seed_requires_unique_literal_registration_concept(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "detection_lexicon_seed.py").write_text(
        "import detection_lexicon as _dl\n"
        "def register_all():\n"
        "    R = _dl.register\n"
        "    R('example.duplicate', 'phrases', it=['apri'], en=['open'])\n"
        "    R('example.duplicate', 'phrases', it=['chiudi'], en=['close'])\n",
        encoding="utf-8",
    )

    assert "LEXICON_SEED_GATE" in {
        issue.code for issue in scan_runtime(runtime_root)
    }


def test_known_seed_registration_cannot_hide_executable_argument_gate(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "detection_lexicon_seed.py").write_text(
        "import detection_lexicon as _dl\n"
        "def register_all(query):\n"
        "    R = _dl.register\n"
        "    R('example.concept', 'phrases', "
        "it=query in {'apri'}, en=['open'])\n",
        encoding="utf-8",
    )

    assert "LEXICON_SEED_GATE" in {
        issue.code for issue in scan_runtime(runtime_root)
    }


def test_exact_legacy_waiver_cannot_authorize_a_duplicate_site(
    tmp_path: Path,
) -> None:
    runtime_root = Path(__file__).resolve().parents[3] / "runtime"
    tree_source = (runtime_root / "engine" / "dispatch.py").read_text(
        encoding="utf-8",
    )
    import ast

    tree = ast.parse(tree_source)
    assignment = next(
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "_FS_CONTAINER_PRODUCERS"
    )
    duplicate_source = f"{ast.unparse(assignment)}\n{ast.unparse(assignment)}\n"
    source = tmp_path / "dispatch.py"
    source.write_text(duplicate_source, encoding="utf-8")
    issues = scan_file(source, relative_path="engine/dispatch.py")

    assert "LEXICON_STALE_INVARIANT" in {issue.code for issue in issues}


def test_value_bound_exception_cannot_hide_added_language(
    tmp_path: Path,
) -> None:
    issues = _scan_mutant(
        tmp_path,
        "engine/dispatch.py",
        "_FS_CONTAINER_PRODUCERS = {\n"
        "    'find_dirs': 'base_path', 'find_files': 'base_path',\n"
        "    'apri': 'open',\n"
        "}\n",
    )

    assert ("LEXICON_NEUTRAL_TABLE", "_FS_CONTAINER_PRODUCERS") in {
        (issue.code, issue.symbol) for issue in issues
    }


def test_accepts_only_value_bound_technical_tables(tmp_path: Path) -> None:
    skill_issues = _scan_mutant(
        tmp_path,
        "skill_wrapper.py",
        "_STORE_TRUE_CANONICAL_TRUE = frozenset({'1', 'true'})\n"
        "_STORE_TRUE_CANONICAL_FALSE = frozenset({'0', 'false'})\n",
    )
    google_issues = _scan_mutant(
        tmp_path,
        "google_places_client.py",
        "_OSM_TO_GOOGLE_TYPE = "
        "{'amenity:pharmacy': 'pharmacy', 'shop:books': 'book_store'}\n",
    )
    promotion_issues = _scan_mutant(
        tmp_path,
        "admin/promotions_review.py",
        "_OPTIONS_PROMOTED_GRACE = "
        "(('confirm', 'MSG_PROMOTER_REVIEW_CONFIRM'),)\n",
    )

    assert skill_issues == []
    assert google_issues == []
    assert promotion_issues == []


def test_rejects_language_smuggled_into_technical_projection(
    tmp_path: Path,
) -> None:
    issues = _scan_mutant(
        tmp_path,
        "google_places_client.py",
        "_OSM_TO_GOOGLE_TYPE = "
        "{'amenity:pharmacy': 'pharmacy', 'amenity:farmacia': 'pharmacy'}\n",
    )

    assert [(issue.code, issue.symbol) for issue in issues] == [
        ("LEXICON_LITERAL", "_OSM_TO_GOOGLE_TYPE"),
    ]


def test_rm0005_executable_lexicon_census_is_clean() -> None:
    root = Path(__file__).resolve().parents[3] / "runtime"
    issues = scan_runtime(root)
    assert issues == [], "\n" + "\n".join(
        f"{issue.path}:{issue.line} {issue.code} {issue.symbol}"
        for issue in issues
    )
