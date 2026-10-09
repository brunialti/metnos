"""Register the HTTP lifecycle regression after retiring transient index builds."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime.testing.registry import Registry
from runtime.testing.runner import run_module, run_cluster
from scripts.register_monitor_tests import pytest_code


def main():
    registry = Registry.open()
    try:
        registry.add_module('http_async_tasks', 'runtime', 'runtime/http_async_tasks.py',
                            'Dialog expiry and observed model identities')
        registry.add_case('http_async_tasks', 'index_retirement', 'module',
                          'integration', 'python', pytest_code([
                              'tests/runtime/infra/test_http_background_lifecycle.py']))
        for dependency in ('http_routes_admin', 'ui_surfaces'):
            registry.add_dependency('http_async_tasks', dependency)
        for scope, runner in (('module', run_module), ('cluster', run_cluster)):
            results, counts = runner('http_async_tasks', registry)
            print(scope, counts)
            for result in results:
                if result.status != 'pass':
                    print(result.output, result.failure)
            assert all(result.status == 'pass' for result in results)
    finally:
        registry.conn.close()


if __name__ == '__main__':
    main()
