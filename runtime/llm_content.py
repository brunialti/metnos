"""Provider-neutral image input; image content is never telemetry content."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ImageInput:
    """One in-memory PNG and its instruction, without URLs or local paths."""

    text: str = field(repr=False)
    png: bytes = field(repr=False)

    def __post_init__(self):
        if (type(self.text) is not str or len(self.text) > 4096
                or type(self.png) is not bytes or not 0 < len(self.png) <= 4 * 1024 * 1024):
            raise ValueError('invalid image input')


def contains_image(user) -> bool:
    """Include callers that supply provider-native content blocks."""
    return isinstance(user, ImageInput) or (
        isinstance(user, list) and any(isinstance(block, dict)
            and block.get('type') in {'image', 'image_url', 'input_image'} for block in user))


def provider_content(user, provider: str):
    """Translate at the existing provider boundary; text input is unchanged."""
    if not isinstance(user, ImageInput):
        return user
    data = base64.b64encode(user.png).decode('ascii')
    if provider == 'openai':
        image = {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + data}}
    elif provider == 'anthropic':
        image = {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': data}}
    else:
        raise ValueError('image provider unsupported')
    return [{'type': 'text', 'text': user.text}, image]
