"""Strict loader for published tutor cards.

Card selection is intentionally absent from this module.  Natural-language
queries are matched against localized semantic descriptions by the embedding
index admitted in :mod:`tutor.catalog`; cards do not carry routing phrases.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLISHED_CARDS = REPO_ROOT / "tutor" / "cards" / "published"
_CARD_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_AUDIENCE_RANK = {"user": 0, "instance_admin": 1}
_KINDS = frozenset({"guide", "capability_overview"})


@dataclass(frozen=True, slots=True)
class Card:
    card_id: str
    kind: str
    title: dict[str, str]
    semantic: dict[str, str]
    audience: str
    priority: int
    body: dict[str, str]
    procedure: dict[str, dict]
    catalog: dict[str, object]

    def supports(self, lang: str) -> bool:
        return lang in self.body or lang in self.procedure

    def visible_to(self, audience: str) -> bool:
        return _AUDIENCE_RANK.get(audience, -1) >= _AUDIENCE_RANK[self.audience]


def _language_files(directory: Path, prefix: str, suffix: str) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for path in sorted(directory.glob(f"{prefix}.*.{suffix}")):
        lang = path.name[len(prefix) + 1:-(len(suffix) + 1)].lower()
        if lang and lang.isalpha():
            out[lang] = path
    return out


def _load_one(directory: Path) -> Card:
    with (directory / "card.toml").open("rb") as handle:
        raw = tomllib.load(handle)
    card_id = str(raw.get("id") or "")
    if card_id != directory.name or not _CARD_ID.fullmatch(card_id):
        raise ValueError(f"invalid tutor card identity: {directory}")
    audience = str(raw.get("audience_minima") or "")
    if audience not in _AUDIENCE_RANK:
        raise ValueError(f"invalid audience for tutor card {card_id}: {audience!r}")

    kind = str(raw.get("kind") or "guide")
    if kind not in _KINDS:
        raise ValueError(f"invalid kind for tutor card {card_id}: {kind!r}")
    title = {
        str(key): str(value).strip()
        for key, value in (raw.get("title") or {}).items()
        if str(value).strip()
    }
    semantic = {
        str(key): str(value).strip()
        for key, value in (raw.get("semantic") or {}).items()
        if str(value).strip()
    }
    body = {
        lang: path.read_text(encoding="utf-8").strip()
        for lang, path in _language_files(directory, "body", "md").items()
    }
    procedure: dict[str, dict] = {}
    for lang, path in _language_files(directory, "procedure", "toml").items():
        with path.open("rb") as handle:
            procedure[lang] = tomllib.load(handle)
    if not body and not procedure:
        raise ValueError(f"tutor card {card_id} has no localized content")
    for lang in set(body) | set(procedure):
        if lang not in title or lang not in semantic:
            raise ValueError(f"tutor card {card_id} incomplete for language {lang}")
    return Card(
        card_id=card_id,
        kind=kind,
        title=title,
        semantic=semantic,
        audience=audience,
        priority=int(raw.get("priorita") or 0),
        body=body,
        procedure=procedure,
        catalog=dict(raw.get("catalog") or {}),
    )


@lru_cache(maxsize=1)
def load_published() -> tuple[Card, ...]:
    cards = []
    if not PUBLISHED_CARDS.is_dir():
        return ()
    for directory in sorted(PUBLISHED_CARDS.iterdir()):
        if directory.is_dir() and (directory / "card.toml").is_file():
            cards.append(_load_one(directory))
    ids = [card.card_id for card in cards]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate tutor card id")
    return tuple(cards)
