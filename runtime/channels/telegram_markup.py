"""Strict Telegram inline-keyboard serialization shared by all send paths."""
from __future__ import annotations

from urllib.parse import urlsplit


def inline_keyboard(buttons: list) -> dict:
    rows = []
    for row in buttons or []:
        serialized = []
        for button in row or []:
            if not isinstance(button, dict):
                raise ValueError("telegram button must be an object")
            text = str(button.get("text") or "?")
            data = button.get("data")
            url = button.get("url")
            if bool(data) == bool(url):
                raise ValueError("telegram button requires exactly one action")
            if url:
                parsed = urlsplit(str(url))
                if parsed.scheme != "https" or not parsed.netloc:
                    raise ValueError("telegram button URL must be HTTPS")
                serialized.append({"text": text, "url": str(url)})
            else:
                encoded = str(data).encode("utf-8")
                if not 1 <= len(encoded) <= 64:
                    raise ValueError("telegram callback_data must be 1..64 bytes")
                serialized.append({"text": text, "callback_data": str(data)})
        if serialized:
            rows.append(serialized)
    return {"inline_keyboard": rows}
