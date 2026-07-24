"""Deterministic rendering of curated card content."""

from __future__ import annotations

from .cards import Card


def _text(value: object) -> str:
    return str(value or "").strip()


def render_procedure(card: Card, lang: str) -> str:
    data = card.procedure[lang]
    labels = data.get("labels") or {}
    lines = [f"## {_text(card.title.get(lang))}"]
    for key in ("goal", "prerequisites"):
        value = _text(data.get(key))
        if value:
            label = _text(labels.get(key)) or key.replace("_", " ").title()
            lines.extend(("", f"**{label}.** {value}"))
    steps = data.get("steps") or []
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"procedure {card.card_id}/{lang} has no steps")
    lines.append("")
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict) or not _text(step.get("text")):
            raise ValueError(f"invalid step {index} in {card.card_id}/{lang}")
        lines.append(f"{index}. {_text(step['text'])}")
        verify = _text(step.get("verify_before"))
        if verify:
            label = _text(labels.get("verify_before")) or "Verify first"
            lines.append(f"   - **{label}:** {verify}")
        alternative = _text(step.get("safe_alternative"))
        if alternative:
            label = _text(labels.get("safe_alternative")) or "Safe alternative"
            lines.append(f"   - **{label}:** {alternative}")
    expected = _text(data.get("expected_after"))
    if expected:
        label = _text(labels.get("expected_after")) or "Expected result"
        lines.extend(("", f"**{label}.** {expected}"))
    stops = data.get("stop_conditions") or []
    if stops:
        label = _text(labels.get("stop_conditions")) or "Stop if"
        lines.extend(("", f"**{label}:**"))
        lines.extend(f"- {_text(item)}" for item in stops if _text(item))
    return "\n".join(lines).strip()


def render_card(card: Card, lang: str, *, catalog_summary: str = "") -> str:
    if lang in card.procedure:
        return render_procedure(card, lang)
    title = _text(card.title.get(lang))
    body = _text(card.body.get(lang))
    if "{catalog_summary}" in body:
        body = body.replace("{catalog_summary}", catalog_summary)
    return f"## {title}\n\n{body}".strip()
