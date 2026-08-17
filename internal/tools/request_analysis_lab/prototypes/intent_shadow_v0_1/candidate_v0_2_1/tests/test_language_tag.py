from __future__ import annotations

import unittest

from ...candidate_v0_2.language_tag import normalize_language_tag as normalize_v02
from ..grandfathered_tags import GRANDFATHERED_TAGS
from ..language_tag import normalize_language_tag


class LanguageTagPrerequisiteTests(unittest.TestCase):
    def test_complete_registered_grandfathered_table(self) -> None:
        self.assertEqual(len(GRANDFATHERED_TAGS), 26)
        self.assertEqual(len({item.canonical.casefold() for item in GRANDFATHERED_TAGS}), 26)
        self.assertEqual(sum(item.preferred is not None for item in GRANDFATHERED_TAGS), 21)
        self.assertEqual(sum(item.preferred is None for item in GRANDFATHERED_TAGS), 5)
        for item in GRANDFATHERED_TAGS:
            expected = normalize_v02(item.preferred) if item.preferred else item.canonical
            self.assertEqual(normalize_language_tag(item.canonical.swapcase()), expected)

    def test_regular_irregular_preferred_and_no_preferred(self) -> None:
        expected = {
            "art-lojban": "jbo",
            "i-klingon": "tlh",
            "en-GB-oed": "en-GB-oxendict",
            "sgn-BE-FR": "sfb",
            "cel-gaulish": "cel-gaulish",
            "i-default": "i-default",
            "zh-min": "zh-min",
        }
        for raw, canonical in expected.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_language_tag(raw), canonical)

    def test_modern_regional_script_private_use_are_byte_invariant(self) -> None:
        for tag in ("it-IT", "zh-hant-tw", "de-CH-1901", "x-labtest", "en-u-ca-gregory"):
            with self.subTest(tag=tag):
                self.assertEqual(normalize_language_tag(tag), normalize_v02(tag))

    def test_modern_malformed_and_duplicates_remain_rejected(self) -> None:
        for tag in (
            "", " it-IT", "it_IT", "sl-rozaj-ROZAJ", "en-a-test-a-more",
            "i-Klingon", "ſgn-BE-FR", "ｅｎ-GB", "it–IT",
        ):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                normalize_language_tag(tag)


if __name__ == "__main__":
    unittest.main()
