"""Test schema v4 unified (ADR 0117)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

import index_schema as ix  # noqa: E402


class TestUnifiedSchema(unittest.TestCase):

    def test_schema_version_is_4(self):
        self.assertEqual(ix.INDEX_SCHEMA_VERSION, 4)

    def test_schema_version_v3_legacy_constant_exists(self):
        self.assertEqual(ix.INDEX_SCHEMA_VERSION_V3, 2)

    def test_unified_fields_non_empty(self):
        self.assertGreater(len(ix.UNIFIED_FIELDS), 0)
        names = ix.unified_field_names()
        # Verifica i campi-chiave attesi
        for expected in (
            "path", "sha256", "name", "mtime", "size",
            "image_w", "image_h", "taken_at_iso", "exif_gps",
            "description", "keywords", "location_hint", "activity_hint",
            "embedding_text_idx", "faces",
        ):
            self.assertIn(expected, names, f"missing unified field: {expected}")

    def test_unified_fields_compute_class_valid(self):
        for f in ix.UNIFIED_FIELDS:
            self.assertIn(
                f.compute_class, ("exif", "vlm", "arcface", "derive"),
                f"invalid compute_class for {f.name}",
            )
            self.assertIn(
                f.cost_class, ("cheap", "medium", "heavy"),
                f"invalid cost_class for {f.name}",
            )

    def test_unified_fields_by_compute(self):
        exif_fields = ix.unified_fields_by_compute("exif")
        # Almeno path/sha256/mtime/size/image_w/image_h sono exif
        names = {f.name for f in exif_fields}
        self.assertIn("path", names)
        self.assertIn("image_w", names)

        vlm_fields = ix.unified_fields_by_compute("vlm")
        names_v = {f.name for f in vlm_fields}
        self.assertIn("description", names_v)
        self.assertIn("keywords", names_v)

        arcface_fields = ix.unified_fields_by_compute("arcface")
        self.assertIn("faces", {f.name for f in arcface_fields})

    def test_is_unified_schema(self):
        self.assertTrue(ix.is_unified_schema({"schema_version": 4}))
        self.assertTrue(ix.is_unified_schema({"schema_version": 5}))
        self.assertFalse(ix.is_unified_schema({"schema_version": 3}))
        self.assertFalse(ix.is_unified_schema({"schema_version": 2}))
        self.assertFalse(ix.is_unified_schema({}))
        self.assertFalse(ix.is_unified_schema(None))

    def test_needs_upgrade(self):
        self.assertTrue(ix.needs_upgrade({"schema_version": 1}))
        self.assertTrue(ix.needs_upgrade({"schema_version": 2}))
        self.assertFalse(ix.needs_upgrade({"schema_version": 4}))

    def test_legacy_constants_preserved(self):
        # IDX_TYPES preservato per migration v3→v4
        self.assertIn("scene", ix.IDX_TYPES)
        self.assertIn("persons", ix.IDX_TYPES)
        self.assertIn("gps", ix.IDX_TYPES)
        # ENRICHMENTS preservato per migration
        self.assertGreater(len(ix.ENRICHMENTS), 0)


if __name__ == "__main__":
    unittest.main()
