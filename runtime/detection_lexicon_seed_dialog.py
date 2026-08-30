#!/usr/bin/env python3
"""Reviewed natural-language controls for pending dialogs.

Dialog cancellation is a control-plane decision: accepting a translated form
can retire a pending interaction and prevent its completion callback.  The
active language must therefore have one native, current, manually reviewed
resource.  Italian and English are additive compatibility baselines only
after that gate succeeds.
"""
from __future__ import annotations

import unicodedata

import detection_lexicon as _dl


DIALOG_CANCEL = "dialog.cancel"

_registered_target: tuple[str, int] | None = None


def register_all() -> None:
    """Register the historical exact dialog-cancellation forms."""

    _dl.register(
        DIALOG_CANCEL,
        "phrases",
        match_mode="word",
        review_policy="manual",
        it=[
            "annulla",
            "annulla ultima azione",
            "annulla l'ultima azione",
            "annullare",
            "annullo",
            "annulla turn",
            "annulla l'ultimo turno",
            "annulla ultimo evento",
            "annulla ultimo messaggio",
            "ripristina",
            "ripristina turno precedente",
        ],
        en=[
            "cancel",
            "abort",
            "stop",
            "undo",
            "undo last",
            "undo last action",
            "undo last turn",
            "revert",
            "revert last",
            "rollback",
            "rollback last",
        ],
    )


def _ensure_registered() -> None:
    """Register lazily and retry when tests/installations replace the DB."""

    global _registered_target
    target = (str(_dl.DB_PATH), id(getattr(_dl, "_conn", None)))
    if _registered_target == target:
        return
    register_all()
    ready = all(
        _dl.native_resource_status(DIALOG_CANCEL, language)["ok"]
        for language in ("it", "en")
    )
    _registered_target = (
        (str(_dl.DB_PATH), id(getattr(_dl, "_conn", None))) if ready else None
    )


def _exact_key(value: str) -> str:
    """Case-insensitive exact key; punctuation remains semantically visible."""

    return " ".join(
        unicodedata.normalize("NFKC", str(value or "")).casefold().split()
    )


def _word_key(value: str) -> str:
    """Whitespace-delimited Unicode words used only to spot ambiguity."""

    normalized = unicodedata.normalize(
        "NFKC", str(value or ""),
    ).casefold()
    return " ".join(
        "".join(character if character.isalnum() else " "
                for character in normalized).split()
    )


def exact_match(text: str) -> bool | None:
    """Return ``True``/``False`` for an exact form, ``None`` if unavailable.

    ``None`` is deliberately distinct from a negative match.  A pending-dialog
    consumer must cancel/deny safely in that state; treating the same text as a
    field value could complete the dialog and resume a mutating callback.
    """

    candidate = _exact_key(text)
    if not candidate:
        return False
    try:
        _ensure_registered()
        forms = _dl.native_ready_forms(
            DIALOG_CANCEL,
            require_manual=True,
            include_reviewed_baselines=True,
        )
    except Exception:  # noqa: BLE001 - unavailable control stays unavailable
        return None
    normalized = {_exact_key(form) for form in forms}
    word_forms = {_word_key(form) for form in forms}
    if not normalized or "" in normalized or "" in word_forms:
        return None
    if candidate in normalized:
        return True
    word_candidate = _word_key(text)
    if any(
        f" {form} " in f" {word_candidate} " for form in word_forms
    ):
        # A cancellation form embedded in a longer utterance is not an exact
        # command, but it is unsafe to reinterpret it as a field value that may
        # complete and resume a mutating dialog.
        return None
    return False


__all__ = ["DIALOG_CANCEL", "exact_match", "register_all"]
