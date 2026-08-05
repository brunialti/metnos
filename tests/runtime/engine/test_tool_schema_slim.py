"""Regressioni per il budget aggregato delle superfici planner-facing."""
from __future__ import annotations

import copy
import sys
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from tool_schema_slim import (  # noqa: E402
    _ARG_STRONG_BOUNDARY_RE,
    _boundary_kind,
    slim_args_schemas,
)


class TestSlimArgsPoolBudget(unittest.TestCase):
    def _schemas(self):
        long_desc = " ".join(f"termine{i}" for i in range(50))
        return [
            {
                "type": "object",
                "required": ["short"],
                "properties": {
                    "short": {"type": "string", "description": "breve"},
                    "limit": {
                        "type": "integer", "default": 20,
                        "description": "Numero massimo di risultati restituiti",
                    },
                },
            },
            {
                "type": "object",
                "required": ["payload"],
                "properties": {
                    "payload": {"type": "string", "description": long_desc},
                },
            },
        ]

    def test_short_descriptions_donate_to_long_argument(self):
        from manifest_rules import (
            ARG_DESC_MAX, ARG_RENDER_HARD_MAX, ARG_RENDER_OPTIONAL,
            ARG_RENDER_POOL_SLACK, ARG_RENDER_REQUIRED,
        )

        source = self._schemas()
        rendered = slim_args_schemas(source)
        short = rendered[0]["properties"]["short"]["description"]
        optional = rendered[0]["properties"]["limit"]
        long_desc = rendered[1]["properties"]["payload"]["description"]

        self.assertEqual(short, "breve")
        self.assertIn("description", optional)
        self.assertEqual(optional["default"], 20)
        self.assertGreater(len(long_desc), ARG_DESC_MAX)
        self.assertLessEqual(len(long_desc), ARG_RENDER_HARD_MAX)
        self.assertIn(long_desc.split()[-1], self._schemas()[1]
                      ["properties"]["payload"]["description"].split())
        self.assertLessEqual(
            len(short) + len(long_desc),
            2 * ARG_RENDER_REQUIRED + ARG_RENDER_OPTIONAL
            + ARG_RENDER_POOL_SLACK,
        )
        # Il renderer copia gli schemi e non altera l'input del catalogo.
        self.assertIn("description", source[0]["properties"]["limit"])

    def test_strong_tail_boundary_survives_adaptive_cap(self):
        source = [{
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": (
                        "Destinazione con una spiegazione iniziale molto lunga "
                        "e alcuni esempi intermedi che possono essere abbreviati. "
                        "MATCH ESATTO: wildcard NON supportati."
                    ),
                },
            },
        }]

        rendered = slim_args_schemas(source, pool_slack=0)
        description = rendered[0]["properties"]["target"]["description"]

        self.assertIn("MATCH ESATTO", description)
        self.assertIn("wildcard", description)

    def test_pool_render_is_deterministic(self):
        self.assertEqual(
            slim_args_schemas(self._schemas()),
            slim_args_schemas(self._schemas()),
        )


class TestProviderToolRendering(unittest.TestCase):
    def test_generator_is_materialized_once_and_keeps_all_tools_aligned(self):
        from agent_runtime import render_tools_for_provider

        executors = (
            SimpleNamespace(
                name=f"tool_{index}",
                description=(
                    f"SCOPO: operazione {index}. PATTERN: tool_{index}(). "
                    "NON: altro. OUT: risultato."
                ),
                args_schema={"type": "object", "properties": {}},
            )
            for index in range(2)
        )
        tools = render_tools_for_provider(executors)

        self.assertEqual(
            [tool["function"]["name"] for tool in tools],
            ["tool_0", "tool_1"],
        )
        self.assertTrue(all(tool["function"]["description"] for tool in tools))
        self.assertTrue(all(
            tool["function"]["parameters"]["type"] == "object"
            for tool in tools
        ))

    def test_runtime_resolved_args_are_not_exposed_to_provider(self):
        from agent_runtime import render_tools_for_provider

        executor = SimpleNamespace(
            name="read_demo",
            description=(
                "SCOPO: legge. PATTERN: read_demo(). NON: altro. OUT: entries."
            ),
            args_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Ricerca."},
                    "client": {
                        "type": "string", "runtime_resolved": True,
                        "description": "Backend scelto dal runtime.",
                    },
                },
            },
        )

        tool = render_tools_for_provider([executor])[0]
        properties = tool["function"]["parameters"]["properties"]

        self.assertIn("query", properties)
        self.assertNotIn("client", properties)

    def test_full_core_catalog_is_preserved_by_adaptive_rendering(self):
        """The adaptive renderer supports every currently admitted core manifest."""
        from agent_runtime import planner_facing_schema, render_tools_for_provider
        from manifest_rules import ARG_RENDER_HARD_MAX, render_heads_budgeted

        root = Path(__file__).resolve().parents[3]
        manifests = []
        for path in sorted((root / "executors").glob("*/manifest.toml")):
            with path.open("rb") as handle:
                manifests.append(tomllib.load(handle))
        self.assertTrue(manifests)

        def boundary_kinds(text):
            return {
                _boundary_kind(match.group(0))
                for match in _ARG_STRONG_BOUNDARY_RE.finditer(str(text or ""))
            }

        for lang in ("it", "en"):
            executors = []
            localized_schemas = []
            for manifest in manifests:
                schema = copy.deepcopy(manifest.get("args") or {})
                for spec in (schema.get("properties") or {}).values():
                    if not isinstance(spec, dict):
                        continue
                    description = spec.get("description")
                    if isinstance(description, dict):
                        spec["description"] = (
                            description.get(lang) or description.get("it")
                            or description.get("en") or ""
                        )
                localized_schemas.append(schema)
                executors.append(SimpleNamespace(
                    name=manifest["name"],
                    description=(manifest["description"].get(lang)
                                 or manifest["description"].get("it")),
                    args_schema=schema,
                ))

            tools = render_tools_for_provider(executors)
            expected_descriptions = render_heads_budgeted(
                [executor.description for executor in executors])
            self.assertEqual(len(tools), len(manifests))
            for manifest, schema, tool, expected_description in zip(
                    manifests, localized_schemas, tools,
                    expected_descriptions):
                self.assertEqual(
                    tool["function"]["description"], expected_description,
                    manifest["name"],
                )

                planner_schema = planner_facing_schema(schema)
                rendered_props = tool["function"]["parameters"].get(
                    "properties") or {}
                for name, original in (
                        planner_schema.get("properties") or {}).items():
                    if not isinstance(original, dict):
                        continue
                    rendered = rendered_props.get(name) or {}
                    rendered_description = rendered.get("description", "")
                    self.assertLessEqual(
                        len(rendered_description), ARG_RENDER_HARD_MAX,
                        f"{manifest['name']}.{name}[{lang}]",
                    )
                    self.assertTrue(
                        boundary_kinds(original.get("description"))
                        <= boundary_kinds(rendered_description),
                        f"boundary perso in {manifest['name']}.{name}[{lang}]",
                    )

                raw_props = (schema.get("properties") or {})
                for name, original in raw_props.items():
                    if isinstance(original, dict) \
                            and original.get("runtime_resolved"):
                        self.assertNotIn(
                            name, rendered_props,
                            f"runtime arg esposto: {manifest['name']}.{name}",
                        )


if __name__ == "__main__":
    unittest.main()
