"""Private stdin/stdout one-shot worker; no browser, effects, retry or HTTP client."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm_telemetry import BoundedTransportUsageSink, TRANSPORT_USAGE_KEY, transport_usage_context
from monitor_capture import TRANSPORT_KEY, transport_capture
from playwright_sidecar.challenge_vision import (MAX_CROP_BYTES, VisionUnavailable,
    choose_widget_action)

WIRE_SCHEMA = 'metnos.sites-vision/1'
MAX_INPUT_BYTES = 4 * ((MAX_CROP_BYTES + 2) // 3) + 4096


class _TransferredLease:
    """One-use dispatch ticket; parent already reserved and claimed the budget."""
    def __init__(self, deadline, tokens):
        self.deadline = deadline
        self.max_tokens = tokens
        self._claimed = False

    def claim(self):
        if self._claimed:
            raise VisionUnavailable('reservation_invalid')
        self._claimed = True


def _invoke(data):
    if (not isinstance(data, dict) or set(data) != {'schema', 'crop_png', 'provider',
            'deadline', 'max_tokens', 'lang'} or data['schema'] != WIRE_SCHEMA
            or not isinstance(data['crop_png'], str)):
        raise VisionUnavailable('worker_protocol')
    try:
        crop = base64.b64decode(data['crop_png'], validate=True)
    except (ValueError, TypeError):
        raise VisionUnavailable('crop_invalid') from None
    lease = _TransferredLease(data['deadline'], data['max_tokens'])
    return choose_widget_action(crop, data['provider'], lease, lang=data['lang'])


def main() -> int:
    usage = BoundedTransportUsageSink()
    with transport_capture() as monitor, transport_usage_context(usage):
        try:
            raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
            if len(raw) > MAX_INPUT_BYTES:
                raise VisionUnavailable('worker_protocol')
            data = json.loads(raw)
            result = {'schema': WIRE_SCHEMA, 'ok': True, 'plan': _invoke(data)}
        except VisionUnavailable as error:
            result = {'schema': WIRE_SCHEMA, 'ok': False, 'error': str(error)}
        except Exception:
            result = {'schema': WIRE_SCHEMA, 'ok': False, 'error': 'worker_error'}
    result[TRANSPORT_USAGE_KEY] = usage.export()
    if monitor is not None:
        result[TRANSPORT_KEY] = monitor.export()
    # No exception text, prompt, crop, base64 or HTTP diagnostics on either pipe.
    sys.stdout.write(json.dumps(result, separators=(',', ':'), allow_nan=False) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
