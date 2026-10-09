"""Register and verify the Turns UI in an explicitly selected test database."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime.testing.registry import Registry
from runtime.testing.runner import run_module, run_cluster
from scripts.register_monitor_tests import pytest_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    args = parser.parse_args()
    registry = Registry.open(args.db)
    try:
        registry.add_module('http_turns', 'runtime', 'runtime/http_routes_admin.py', 'Localized recent turns')
        registry.add_case('http_turns', 'listing_and_local_time', 'module', 'integration', 'python', pytest_code([
            'tests/runtime/http/test_turns_routes.py', 'tests/runtime/http/test_turns_browser.py']))
        registry.add_module('http_shared_header', 'runtime', 'runtime/templates/_app_header.html', 'Shared navigation')
        registry.add_case('http_shared_header', 'layout_i18n_browser', 'module', 'integration', 'python', pytest_code([
            'tests/runtime/http/test_shared_header_browser.py']))
        registry.add_dependency('http_turns', 'http_shared_header')
        for scope, runner in [('module', run_module), ('cluster', run_cluster)]:
            results, counts = runner('http_turns', registry)
            print(scope, counts)
            for result in results:
                if result.status != 'pass': print(result.output, result.failure)
            assert results and all(result.status == 'pass' for result in results)
    finally:
        registry.conn.close()


if __name__ == '__main__':
    main()
