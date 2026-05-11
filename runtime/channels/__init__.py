"""runtime.channels — astrazione canale.

Un Channel converte un'interfaccia esterna (CLI, Telegram, voce, ...) in un
flusso di messaggi e risposte parlato al runtime. Adattatore minimo, non
modulo separato per ogni integrazione.

Vedi microprogettazione `docs/it/architecture/channel.html`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class InboundMessage:
    """Un messaggio in arrivo da un canale, normalizzato."""
    channel: str          # "telegram", "cli", ...
    sender_id: str        # chat_id Telegram, username CLI, ...
    text: str
    message_id: str       # id univoco nel canale (per dedup / reply)
    received_at: float    # epoch seconds
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class OutboundMessage:
    """Un messaggio in uscita verso un canale."""
    text: str
    reply_to: str | None = None       # message_id a cui rispondere
    buttons: list[dict] | None = None # bottoni di approval (canali che li supportano)


@runtime_checkable
class Channel(Protocol):
    """Adattatore di canale. Due capacita' minime + nome."""
    name: str

    def send(self, recipient: str, message: OutboundMessage) -> dict:
        """Invia un messaggio. Ritorna dict {ok, error?, sent_message_id?}."""
        ...

    def poll(self) -> list[InboundMessage]:
        """Ritorna eventuali messaggi in arrivo (vuoto se nulla). Stateless al
        livello del Protocol; le implementazioni possono mantenere offset
        interno (Telegram long-poll usa update_id)."""
        ...
