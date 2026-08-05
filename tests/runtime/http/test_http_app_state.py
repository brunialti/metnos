from __future__ import annotations

import warnings

from aiohttp import web
from aiohttp.web_exceptions import NotAppKeyWarning

from http_app_state import ADMIN_KEY, SSE_RESPONSES, app_get, app_setdefault


def test_real_application_state_uses_typed_keys_without_warning() -> None:
    app = web.Application()
    with warnings.catch_warnings():
        warnings.simplefilter("error", NotAppKeyWarning)
        app[ADMIN_KEY] = "secret"
        responses = app_setdefault(app, SSE_RESPONSES, set())
    assert app_get(app, ADMIN_KEY) == "secret"
    assert responses is app[SSE_RESPONSES]


def test_plain_dict_fakes_keep_legacy_string_compatibility() -> None:
    responses = {object()}
    app = {"admin_key": "test", "sse_responses": responses}
    assert app_get(app, ADMIN_KEY) == "test"
    assert app_setdefault(app, SSE_RESPONSES, set()) is responses
