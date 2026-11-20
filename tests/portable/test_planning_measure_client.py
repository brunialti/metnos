"""The file client crosses HTTP; a local server controls the report, not Metnos."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_client(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tests/benchmarks' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client():
    return load_client('compound_dryrun')


REPORT = {
    'origin': 'L3', 'memory': 'off', 'shape': 'ammessa',
    'admission': 'non verificata (richiede registrazione)',
    'gate_before_long': False, 'actions': [{'verb': 'find', 'object': 'files'}],
    'events': [],
    'proposed_plan': {'steps': [{'tool': 'find_files_hash', 'args': {
        'base_path': 'Immagini/zz_prova_metnos_a'}}, {'tool': 'final_answer', 'args': {}}]},
    'final_plan': {'steps': [{'tool': 'find_files_hash', 'args': {
        'base_path': 'Immagini/zz_prova_metnos_a'}}, {'tool': 'final_answer', 'args': {}}]},
}


@contextmanager
def server(respond):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append((self.path, dict(self.headers), body))
            code, report = respond(len(received))
            self.send_response(code)
            if code == 302:
                self.send_header('Location', '/credential-trap')
            self.end_headers()
            self.wfile.write(json.dumps(report).encode())

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{http.server_port}', received
    finally:
        http.shutdown()
        http.server_close()
        thread.join(2)


def files(tmp_path):
    phrases = tmp_path / 'phrases.json'
    phrases.write_text(json.dumps([
        {'id': 'f1-a', 'query': 'Trova duplicati in Immagini/zz_prova_metnos_a'},
        {'id': 'f2-b', 'query': 'Elenca Immagini/zz_prova_metnos_b'},
    ]))
    key = tmp_path / 'admin.key'
    key.write_text('portable-test-key\n')
    return phrases, tmp_path / 'reports.jsonl', key


def test_file_client_keeps_each_pass_and_only_posts_the_measurement(client, tmp_path):
    phrases, output, key = files(tmp_path)
    with server(lambda _: (200, REPORT)) as (url, requests):
        assert client.measure_file(phrases, output, key, base_url=url, runs=2, timeout=2) == 4
    reports = [json.loads(line) for line in output.read_text().splitlines()]
    assert [(row['id'], row['pass']) for row in reports] == [
        ('f1-a', 1), ('f1-a', 2), ('f2-b', 1), ('f2-b', 2)]
    assert all(row['report'] == REPORT for row in reports)
    expected = json.loads(phrases.read_text())
    assert [body for _, _, body in requests] == [
        {'query': row['query'], 'memory': 'off'} for row in expected for _ in range(2)]
    assert all(path == '/admin/plans/measure' for path, _, _ in requests)
    assert all(headers['Authorization'] == 'Bearer portable-test-key' for _, headers, _ in requests)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert 'portable-test-key' not in output.read_text()


@pytest.mark.parametrize('code,report,error', [
    (503, {'error': 'private server detail'}, 'http_status_503'),
    (302, {}, 'redirect_refused'),
    (200, {'origin': 'L1', 'memory': 'on'}, 'report_invalid'),
])
def test_client_stops_on_failure_without_retry_or_redirect(
        client, tmp_path, code, report, error):
    phrases, output, key = files(tmp_path)
    with server(lambda n: (200, REPORT) if n == 1 else (code, report)) as (url, requests):
        with pytest.raises(client.ClientError, match=error):
            client.measure_file(phrases, output, key, base_url=url, runs=2, timeout=2)
    assert len(requests) == 2
    assert len(output.read_text().splitlines()) == 1
    assert 'private server detail' not in output.read_text()


@pytest.mark.parametrize('data', [[], {}, [''], [True],
    [{'id': 'a', 'query': 'x'}, {'id': 'a', 'query': 'y'}],
    [{'id': 'a', 'query': 'x', 'memory': 'on'}]])
def test_client_validates_whole_input_before_creating_output(client, tmp_path, data):
    phrases, output, key = files(tmp_path)
    phrases.write_text(json.dumps(data))
    with pytest.raises(client.ClientError):
        client.measure_file(phrases, output, key, base_url='http://127.0.0.1:1', runs=1, timeout=1)
    assert not output.exists()


def test_old_benchmarks_export_phrases_and_cannot_plan_locally(client, tmp_path):
    historical = tmp_path / 'historical.json'
    client.export_queries(historical)
    rows = client.load_queries(historical)
    assert {row['id']: row['query'] for row in rows} == client.VARIANTS
    with pytest.raises(client.ClientError, match='local_planner_retired'):
        client.plan_only(None, None, None)
    with pytest.raises(client.ClientError, match='local_planner_retired'):
        client.build_calls()
    scaling = load_client('compound_scaling_bench')
    grid = tmp_path / 'scaling.json'
    assert scaling.main(['--max-actions', '2', '--max-domains', '1', '--per-cell', '2',
                         '--export-queries', str(grid)]) == 0
    rows = client.load_queries(grid)
    assert [row['id'] for row in rows] == ['a2-d1-k0', 'a2-d1-k1']
    assert all(row['query'].strip() for row in rows)
