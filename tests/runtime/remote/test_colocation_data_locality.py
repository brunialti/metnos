"""test_colocation_data_locality — un consumer device_ok di dati prodotti sul
SERVER deve co-localizzarsi sul server (§7.9, bug 7/7/2026: get_files su
`${step1.entries.*.path}` di find_images_indices@.33 con device sticky finiva
sul PC → path Linux irraggiungibili). Copre `_references_server_producer` +
il gate `_colocate_server` in invoke_executor."""
import sys
from pathlib import Path


import engine.executor as ex  # noqa: E402
from engine.types import StepRun  # noqa: E402


class _Step:
    def __init__(self, args):
        self.args = args


def _hist(*hosts):
    return [StepRun(step_idx=i + 1, tool=f"t{i+1}", args={}, result={},
                    ok=True, latency_ms=1, host=h) for i, h in enumerate(hosts)]


def test_from_step_producer_server():
    step = _Step({"from_step": 1})
    assert ex._references_server_producer(step, _hist("server")) is True


def test_placeholder_dollar_producer_server():
    # Il caso REALE del bug: get_files pipa via ${step1.entries.*.path}.
    step = _Step({"paths": "${step1.entries.*.path}", "fields": ["date"]})
    assert ex._references_server_producer(step, _hist("server")) is True


def test_placeholder_braces_producer_server():
    step = _Step({"x": "{{step2.field}}"})
    assert ex._references_server_producer(step, _hist("server", "server")) is True


def test_producer_on_device_not_colocated():
    # Producer girato sul DEVICE → il consumer resta sul device (dati là).
    step = _Step({"paths": "${step1.entries.*.path}"})
    assert ex._references_server_producer(step, _hist("7bd3da08dev")) is False


def test_literal_no_reference():
    step = _Step({"path": "C:\\Users\\rober\\Documents\\c.txt"})
    assert ex._references_server_producer(step, _hist("server")) is False


def test_no_producer_step_yet():
    step = _Step({"from_step": 3})
    assert ex._references_server_producer(step, _hist("server")) is False  # step 3 non esiste


def test_step_args_non_dict():
    assert ex._references_server_producer(_Step(None), _hist("server")) is False
