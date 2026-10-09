#!/usr/bin/env python3
"""Registro Monitor riproducibile; --verify esegue solo prove offline dichiarate.

Non migra il DB vivo automaticamente. Registrazione e run sono nel database
scelto; i gruppi operativi con turni LLM richiedono la finestra coordinata.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "runtime"), str(ROOT)]
from runtime.testing.registry import DEFAULT_DB, Registry
from runtime.testing.runner import run_cluster, run_module

CASES = {
    "monitor_metrics": ["tests/runtime/infra/test_monitor_metrics.py"],
    "monitor_events": ["tests/runtime/infra/test_monitor_store.py"],
    "monitor_requests": ["tests/runtime/infra/test_monitor_late_coverage.py"],
    "monitor_store": ["tests/runtime/infra/test_monitor_store.py"],
    "monitor_capture": ["tests/runtime/infra/test_monitor_capture.py",
                        "tests/runtime/infra/test_monitor_boundaries.py",
                        "tests/runtime/infra/test_monitor_browser_transport.py",
                        "tests/runtime/infra/test_monitor_browser_late.py",
                        "tests/runtime/infra/test_monitor_transport_limits.py"],
    "monitor_views": ["tests/runtime/infra/test_monitor_views.py"],
    "http_routes_monitor": ["tests/runtime/http/test_monitor_routes.py",
                            "tests/runtime/http/test_monitor_templates.py",
                            "tests/runtime/http/test_monitor_controller_browser.py"],
}
HOOKS = {
    "llm_router": ("runtime/llm_router.py", ["tests/runtime/infra/test_llm_router_tier_policy.py"]),
    "llm_provider": ("runtime/llm_provider.py", ["tests/runtime/infra/test_monitor_capture.py"]),
    "llm_telemetry": ("runtime/llm_telemetry.py", ["tests/runtime/infra/test_llm_telemetry_tier.py"]),
    "executor_helpers": ("runtime/executor_helpers.py", ["tests/runtime/infra/test_monitor_capture.py"]),
    "agent_runtime": ("runtime/agent_runtime.py", ["tests/runtime/infra/test_monitor_transport_limits.py"]),
    "http_routes_agent": ("runtime/http_routes_agent.py", ["tests/runtime/infra/test_monitor_capture.py"]),
    "dialog_pending": ("runtime/dialog_pending.py", ["tests/runtime/infra/test_monitor_capture.py"]),
    "scheduler_v2.daemon": ("runtime/scheduler_v2/daemon.py", ["tests/runtime/infra/test_monitor_boundaries.py"]),
    "durable_workloads.worker": ("runtime/durable_workloads/worker.py", ["tests/runtime/infra/test_monitor_boundaries.py"]),
    "durable_workloads.service": ("runtime/durable_workloads/service.py", ["tests/runtime/durable_workloads/test_service.py"]),
    "playwright_sidecar.server": ("runtime/playwright_sidecar/server.py", ["tests/runtime/infra/test_monitor_browser_transport.py"]),
    "playwright_sidecar.session_client": ("runtime/playwright_sidecar/session_client.py", ["tests/runtime/infra/test_monitor_browser_transport.py"]),
    "vlm_client": ("runtime/vlm_client.py", ["tests/runtime/infra/test_monitor_capture.py"]),
    "llm_helpers": ("runtime/llm_helpers.py", ["tests/runtime/infra/test_monitor_late_coverage.py"]),
    "channels.daemon": ("runtime/channels/daemon.py", ["tests/runtime/infra/test_monitor_capture.py"]),
    "agent_server": ("runtime/agent_server.py", ["tests/runtime/infra/test_monitor_capture.py"]),
    "metnos_http_server": ("runtime/metnos_http_server.py", ["tests/runtime/http/test_monitor_templates.py"]),
    "http_routes_admin": ("runtime/http_routes_admin.py", ["tests/runtime/http/test_monitor_templates.py"]),
    "ui_surfaces": ("runtime/ui_surfaces.py", ["tests/runtime/http/test_monitor_templates.py"]),
}


def pytest_code(files):
    return ("import os, subprocess, sys\nfrom pathlib import Path\n"
            "root = Path(_RT).parent\n"
            "environment = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(root / 'runtime'), str(root)))}\n"
            f"result = subprocess.run([sys.executable, '-m', 'pytest', '-q', *{files!r}], "
            "cwd=root, env=environment, capture_output=True, text=True, timeout=100)\n"
            "print(result.stdout)\nprint(result.stderr)\nassert result.returncode == 0")


def register(registry):
    existing = {row["name"] for row in registry.list_modules()}
    for name, files in CASES.items():
        if name not in existing:
            registry.add_module(name, "runtime", f"runtime/{name}.py", "Monitor: acquisizione e viste")
        case_name = "token_performance_formulas" if name == "monitor_metrics" else "monitor_regression"
        registry.add_case(name, case_name, "module", "integration", "python", pytest_code(files))
    for name, (source, files) in HOOKS.items():
        if name not in existing:
            registry.add_module(name, "runtime", source, "Innesto Monitor; esercizio invariato")
        registry.add_case(name, "monitor_offline_boundary", "module", "integration", "python", pytest_code(files))
    for name in CASES:
        if name != "monitor_metrics":
            registry.add_dependency(name, "monitor_metrics")
    registry.add_dependency("monitor_store", "monitor_events")
    registry.add_dependency("monitor_store", "monitor_requests")
    registry.add_dependency("monitor_views", "monitor_store")
    registry.add_dependency("http_routes_monitor", "monitor_views")
    registry.add_dependency("monitor_capture", "monitor_events")
    # Voci audit storiche: medesime prove, tipo supportato e interprete/root
    # derivati. Nessun percorso dell'installazione fissato nello script.
    for case in registry.cases_for_module("audit_jsonl"):
        if case.test_kind == "pytest" and case.test_code.startswith("tests/"):
            registry.add_case("audit_jsonl", case.name, case.level, case.category,
                              "python", pytest_code([case.test_code.strip()]),
                              case.setup_code, case.teardown_code, case.expected, case.enabled)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    registry = Registry.open(args.db)
    try:
        register(registry)
        if args.verify:
            allowed = set(CASES) | {"audit_jsonl"}
            for name in CASES:
                cluster = registry.cluster_of(name)
                if cluster - allowed:
                    raise RuntimeError(f"Gruppo {name} contiene prove operative: {sorted(cluster - allowed)}")
            failed = []
            checks = [(name, "module", run_module) for name in CASES]
            # Il registro definisce gruppo = vicini diretti, non componente
            # transitiva: ogni nuovo modulo deve avere la sua prova di gruppo.
            checks.extend((name, "cluster", run_cluster) for name in CASES)
            for name, scope, runner in checks:
                results, counts = runner(name, registry)
                print(name, scope, counts, flush=True)
                failed.extend((result.case.name, result.output, result.failure)
                              for result in results if result.status != "pass")
            if failed:
                raise RuntimeError(failed)
    finally:
        registry.conn.close()


if __name__ == "__main__":
    main()
