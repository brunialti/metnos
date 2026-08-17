from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest

from .. import build_artifacts
from ..critic import MAX_MODEL_CALLS_PER_ANALYSIS, TARGET_MAX_AVERAGE_MODEL_CALLS
from ..language_tag import normalize_language_tag
from ..projection import build_schema
from ..registry_projection import load_projection, validate_projection_against_source
from ..structured_client import FakeStructuredTransport, StructuredClient


HERE = Path(__file__).resolve().parent.parent


class ArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_projection()

    def test_generated_artifacts_and_freeze_are_current(self):
        self.assertEqual(build_artifacts.check(), [])
        validate_projection_against_source(self.registry)

    def test_projection_has_bilingual_manifest_material(self):
        records = [
            description
            for metadata in self.registry["operations"].values()
            for description in metadata["descriptions"]
        ]
        self.assertTrue(records)
        self.assertTrue(any("it" in item["languages"] for item in records))
        self.assertTrue(any("en" in item["languages"] for item in records))
        self.assertTrue(
            any(
                "scope" in chapter and "not" in chapter
                for item in records
                for chapter in item["languages"].values()
            )
        )

    def test_schema_has_no_model_facing_derived_fields(self):
        schema = json.dumps(build_schema(self.registry), sort_keys=True)
        for field in ('"input"', '"output"', '"path"', '"node_path"', '"ordinal"', '"outcome"', '"continuation"'):
            self.assertNotIn(field, schema)

    def test_no_runtime_or_oracle_dependency_in_non_test_modules(self):
        forbidden_import_roots = {"runtime", "requests", "httpx", "urllib", "socket"}
        for path in HERE.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = {alias.name.split(".", 1)[0] for alias in node.names}
                    self.assertFalse(roots & forbidden_import_roots, path.name)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.split(".", 1)[0], forbidden_import_roots, path.name)
        for name in (
            "api.py",
            "compiler.py",
            "critic.py",
            "projection.py",
            "structured_client.py",
            "validator.py",
        ):
            self.assertNotIn("oracle", (HERE / name).read_text(encoding="utf-8").lower(), name)

    def test_offline_transport_required(self):
        class NotOffline:
            offline_only = False

            def send(self, request):
                raise AssertionError(request)

        with self.assertRaises(TypeError):
            StructuredClient(NotOffline())

        client = StructuredClient.from_fake_responses(
            [b'{"kind":"system_control","control":"undo_last_turn"}']
        )
        self.assertIs(type(client), StructuredClient)

    def test_impostor_and_subclass_transport_are_rejected_before_send(self):
        class Impostor:
            offline_only = True

            def __init__(self):
                self.called = False

            def send(self, request):
                del request
                self.called = True
                raise AssertionError("impostor send must never run")

        impostor = Impostor()
        with self.assertRaises(TypeError):
            StructuredClient(impostor)
        self.assertFalse(impostor.called)

        with self.assertRaises(TypeError):
            class FakeSubclass(FakeStructuredTransport):
                pass

        with self.assertRaises(TypeError):
            FakeStructuredTransport((), _seal=object())

    def test_critic_budget_is_declared_and_not_exceeded(self):
        self.assertEqual(MAX_MODEL_CALLS_PER_ANALYSIS, 2)
        self.assertLessEqual(TARGET_MAX_AVERAGE_MODEL_CALLS, 1.20)

    def test_locale_neutral_bcp47_path(self):
        self.assertEqual(normalize_language_tag("it-IT"), "it-IT")
        self.assertEqual(normalize_language_tag("zh-hant-tw"), "zh-Hant-TW")
        self.assertEqual(normalize_language_tag("x-labtest"), "x-labtest")
        raw = b'{"kind":"system_control","control":"undo_last_turn"}'
        for tag, normalized in (
            ("it-IT", "it-IT"),
            ("zh-hant-tw", "zh-Hant-TW"),
            ("x-labtest", "x-labtest"),
        ):
            with self.subTest(tag=tag):
                client = StructuredClient.from_fake_responses([raw])
                exchange = client.run("synthetic unicode \u96ea", self.registry, tag)
                self.assertIn(
                    f"INPUT_LANGUAGE_TAG: {normalized}",
                    exchange.request["messages"][0]["content"],
                )
        for malformed in (
            "", " it-IT", "it_IT", "x", "en-a",
            "sl-rozaj-ROZAJ", "en-a-test-a-more",
        ):
            with self.subTest(malformed=malformed):
                client = StructuredClient.from_fake_responses([raw])
                with self.assertRaises(ValueError):
                    client.run("synthetic", self.registry, malformed)
                self.assertEqual(client.captured_requests, ())

    def test_core_has_no_binary_locale_branch(self):
        for name in ("projection.py", "structured_client.py", "language_tag.py"):
            source = (HERE / name).read_text(encoding="utf-8")
            self.assertNotIn('language == "it"', source)
            self.assertNotIn('language == "en"', source)
            self.assertNotIn('if language in {"it", "en"}', source)


if __name__ == "__main__":
    unittest.main()
