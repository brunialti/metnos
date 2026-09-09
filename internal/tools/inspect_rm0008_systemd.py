#!/usr/bin/python3.12
"""Read-only diagnosis of the current signed systemd observation."""
import json
import os
from pathlib import Path
import runpy
import sys


def main():
    if os.geteuid() != 0 or len(sys.argv) != 1:
        return 78
    helper = runpy.run_path(str(Path(__file__).with_name("diagnose_rm0008_preflight.py")))
    module = helper["installed_preflight"]()
    authenticated = module["_authenticate_fixed_ownership_snapshot_v1"]()
    _, materials = module["_load_bound_preflight_materials_v1"](authenticated)
    capture = module["_capture_effective_systemd_units_core_v1"](
        materials, systemctl_executable=materials.descriptor.systemctl_executable,
        live_root=Path("/"), uid=0, gid=0,
    )
    print("HASH", capture.snapshot.effective_units_hash,
          materials.prerequisite.effective_units_hash)
    # Counterfactual hashes are diagnostic only. They are never submitted to
    # the authorization path, written to disk, or used to launch anything.
    entries = capture.snapshot.entries
    edges = {(edge.relation, edge.unit_name) for unit in entries
             for edge in unit.manager_added_edges}
    for relation, name in sorted(edges):
        candidate = tuple(unit._replace(
            manager_added_edges=tuple(
                edge for edge in unit.manager_added_edges
                if (edge.relation, edge.unit_name) != (relation, name)
            ),
            manager_projection=unit.manager_projection._replace(properties=tuple(
                prop._replace(values=tuple(v for v in prop.values if v != name))
                if prop.name == relation else prop
                for prop in unit.manager_projection.properties
            )),
        ) for unit in entries)
        candidate_hash = module["_make_effective_systemd_units_snapshot_v1"](
            candidate).effective_units_hash
        if candidate_hash == materials.prerequisite.effective_units_hash:
            print("EXACT_DIAGNOSTIC_DIFFERENCE", relation, name)
    origins = {}
    for unit in capture.snapshot.entries:
        print("UNIT", unit.unit_name, unit.unit_file_state,
              [(edge.relation, edge.unit_name) for edge in unit.manager_added_edges])
        for edge in unit.manager_added_edges:
            origins[edge.unit_name] = {
                key: value for key, value in edge.as_value().items() if key != "relation"
            }
    for name, origin in sorted(origins.items()):
        print("ORIGIN", name, json.dumps(origin, sort_keys=True))
    for name in ("unit-manifest.json", "predecessor-manifest.json"):
        path = Path("/var/lib/metnos-admin/rm0008-final-b715a765") / name
        value = json.loads(path.read_bytes())
        print("SAVED_SCHEMA", name, list(value)[:20] if isinstance(value, dict) else type(value).__name__)
        if name == "unit-manifest.json":
            print("SAVED_UNITS", json.dumps(value.get("units"))[:1200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
