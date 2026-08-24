from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from engine.routing_pool import build_routing_pool  # noqa: E402
from engine.types import Intent  # noqa: E402
from intent_extractor import extract_intent  # noqa: E402


@dataclass(frozen=True)
class _Executor:
    name: str


CATALOG = [_Executor("list_tasks"), _Executor("get_proposals")]


def _extract(payload: str) -> dict | None:
    return extract_intent("testo deliberatamente opaco", lambda *_a, **_k: payload)


def test_closed_non_action_kinds_survive_without_canonical_action():
    assert _extract('{"kind":"conversation"}') == {"kind": "conversation"}
    assert _extract('{"kind":"metnos_help"}') == {"kind": "metnos_help"}
    assert _extract('{"kind":"unknown"}') == {"kind": "unknown"}


def test_action_wins_over_a_conflicting_non_action_label():
    assert _extract(
        '{"kind":"conversation","verb":"read","object":"files"}'
    ) == {"kind": "action", "verb": "read", "object": "files"}


def test_legacy_action_output_remains_compatible():
    assert _extract('{"verb":"read","object":"files"}') == {
        "kind": "action", "verb": "read", "object": "files"
    }


def test_malformed_kind_does_not_acquire_non_action_authority():
    assert _extract('{"kind":"chat"}') is None


def test_conversation_and_help_have_no_operational_pool():
    for kind in ("conversation", "metnos_help"):
        assert build_routing_pool("q", Intent(kind=kind), CATALOG) == []


def test_unknown_preserves_prudent_full_catalog_fallback():
    assert build_routing_pool("q", Intent(kind="unknown"), CATALOG) == [
        "list_tasks", "get_proposals"
    ]


def test_it_and_en_prompts_declare_the_same_closed_union():
    for lang in ("it", "en"):
        prompt = (ROOT / "runtime" / "prompts" / lang /
                  "intent_extractor_v4.j2").read_text(encoding="utf-8")
        for kind in ("action", "conversation", "metnos_help", "unknown"):
            assert f"`{kind}`" in prompt
