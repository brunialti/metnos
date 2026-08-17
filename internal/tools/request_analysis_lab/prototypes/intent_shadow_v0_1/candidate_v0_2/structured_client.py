"""Offline structured-output request builder with a fake transport only."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, final

from .canonical import canonical_json_bytes
from .language_tag import normalize_language_tag
from .projection import SCHEMA_NAME, build_prompt, build_schema
from .registry_projection import validate_projection


_FAKE_TRANSPORT_SEAL = object()
_STRUCTURED_CLIENT_SEAL = object()


@final
class FakeStructuredTransport:
    """Sealed deterministic transport constructed only by ``StructuredClient``."""

    __slots__ = ("_responses", "_requests")

    def __init_subclass__(cls, **kwargs: Any) -> None:
        del kwargs
        raise TypeError("FakeStructuredTransport is sealed")

    def __init__(self, responses: tuple[bytes, ...], *, _seal: object) -> None:
        if _seal is not _FAKE_TRANSPORT_SEAL:
            raise TypeError("FakeStructuredTransport cannot be constructed externally")
        if type(responses) is not tuple or any(type(item) is not bytes for item in responses):
            raise TypeError("fake responses must be an exact tuple of bytes")
        self._responses = list(responses)
        self._requests: list[dict[str, Any]] = []

    @property
    def requests(self) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy(self._requests))

    def send(self, request: dict[str, Any]) -> dict[str, Any]:
        self._requests.append(deepcopy(request))
        if not self._responses:
            raise RuntimeError("fake response queue exhausted")
        content = self._responses.pop(0).decode("utf-8")
        return {
            "choices": [
                {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
            ]
        }


@dataclass(frozen=True, slots=True)
class StructuredExchange:
    request: dict[str, Any]
    request_sha256: str
    content: bytes
    content_sha256: str


def build_request(query: str, registry: dict[str, Any], language_tag: str) -> dict[str, Any]:
    if type(query) is not str or not query.strip():
        raise ValueError("query must be a nonempty string")
    try:
        query.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("query contains an invalid Unicode scalar") from exc
    normalized_language = normalize_language_tag(language_tag)
    validate_projection(registry)
    request = {
        "messages": [
            {"role": "system", "content": build_prompt(registry, normalized_language)},
            {"role": "user", "content": query},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": SCHEMA_NAME,
                "strict": True,
                "schema": build_schema(registry),
            },
        },
        "temperature": 0,
    }
    if "grammar" in request or "tools" in request:
        raise AssertionError("structured request must not mix grammar or tools")
    return request


@final
class StructuredClient:
    __slots__ = ("_transport",)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        del kwargs
        raise TypeError("StructuredClient is sealed")

    def __init__(self, transport: Any, *, _seal: object | None = None):
        if _seal is not _STRUCTURED_CLIENT_SEAL:
            raise TypeError("use StructuredClient.from_fake_responses")
        if type(transport) is not FakeStructuredTransport:
            raise TypeError("candidate 0.2 accepts its sealed fake transport only")
        self._transport = transport

    @classmethod
    def from_fake_responses(cls, responses: list[bytes] | tuple[bytes, ...]) -> StructuredClient:
        if type(responses) not in (list, tuple) or any(type(item) is not bytes for item in responses):
            raise TypeError("fake responses must be an exact list or tuple of bytes")
        transport = FakeStructuredTransport(tuple(responses), _seal=_FAKE_TRANSPORT_SEAL)
        return cls(transport, _seal=_STRUCTURED_CLIENT_SEAL)

    @property
    def captured_requests(self) -> tuple[dict[str, Any], ...]:
        return self._transport.requests

    def run(self, query: str, registry: dict[str, Any], language_tag: str) -> StructuredExchange:
        request = build_request(query, registry, language_tag)
        response = self._transport.send(request)
        try:
            choices = response["choices"]
            message = choices[0]["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("fake structured response shape invalid") from exc
        if type(content) is not str:
            raise ValueError("fake structured content must be a string")
        raw = content.encode("utf-8")
        return StructuredExchange(
            request,
            sha256(canonical_json_bytes(request)).hexdigest(),
            raw,
            sha256(raw).hexdigest(),
        )
