#!/usr/bin/env python3
"""Recompute the RM-0008 unit-name overlap from the canonical service source."""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runtime"))

import executor_birth_service_catalog as service_catalog  # noqa: E402


EXPECTED_COLLISION = (
    "legacy-service-http-system",
    "service-http",
    "system_unit",
    "system",
    "metnos-http.service",
)


def _binding_identity(value: dict[str, object]) -> tuple[str, ...]:
    return tuple(str(value[field]) for field in (
        "legacy_id", "entry_id", "kind", "scope", "locator",
    ))


def main() -> int:
    unit_names = tuple(sorted({
        item.unit_name
        for item in service_catalog.SERVICE_SOURCE_V1
        if item.unit_name is not None
    }))
    bindings = service_catalog.legacy_bindings_from_source_v1()
    homonyms = tuple(
        item for item in bindings if item["locator"] in set(unit_names)
    )
    collisions = tuple(
        item for item in homonyms
        if item["kind"] == "system_unit" and item["scope"] == "system"
    )
    user_http = tuple(
        item for item in homonyms
        if item["kind"] == "user_unit" and item["scope"] == "user"
        and item["locator"] == "metnos-http.service"
    )

    print(f"dominant unit names:       {len(unit_names)}")
    print(f"legacy bindings:           {len(bindings)}")
    print(f"cross-scope homonyms:      {len(homonyms)}")
    print(f"same-destination overlaps: {len(collisions)}")
    for item in collisions:
        print("collision:", " / ".join(_binding_identity(item)))

    valid = (
        len(unit_names) == 12
        and len(bindings) == 39
        and len(homonyms) == 13
        and tuple(map(_binding_identity, collisions)) == (EXPECTED_COLLISION,)
        and len(user_http) == 1
    )
    print("RESULT:", "exact overlap confirmed" if valid else "unexpected overlap")
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
