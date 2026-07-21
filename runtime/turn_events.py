# SPDX-License-Identifier: AGPL-3.0-only
"""turn_events.py — event log durabile per turn, indipendente dalla
connessione HTTP del client.

Architettura (ADR pending — risolve "errore rete" da refresh/tab hidden):

  POST /agent/turn (resumable)
    → 202 Accepted, body {turn_id, stream_url}
    → server spawn asyncio.Task: run_turn() in executor
    → events appended a TurnEventLog[turn_id] mentre il turno procede

  GET /agent/turns/{id}/stream  (resumable, Last-Event-ID)
    → SSE stream di tutti gli eventi del turno
    → se Last-Event-ID header presente, replay dal next id
    → quando il turno e' completo + tutti gli eventi consegnati, close

Disaccoppiamento totale fra esecuzione e connessione:
- Refresh browser: il turno continua, client si ri-attacca via stream_url
- Tab hidden: bytes accumulati in event log, consegnati al focus return
- Network drop: EventSource auto-reconnect con Last-Event-ID nativo

Storage in-memory con cleanup TTL: 5 minuti dopo close, il turno
viene rimosso. Re-fetch via TurnLog jsonl (persistente) se piu' vecchio.

§7.9 deterministico: niente LLM, asyncio + dict + Event.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

_LOG = logging.getLogger(__name__)

# TTL (secondi): quanto teniamo gli eventi di un turno chiuso in memoria
# prima di GC. 300s = utenti possono refresh entro 5 min e riprendere.
_TURN_EVENT_TTL_S = 300


@dataclass
class TurnEvent:
    id: int
    """Event id monotono per turno. Last-Event-ID semantics."""
    event_type: str
    """tool_call | tool_result | step_start | step_end | final | error"""
    payload: dict
    """Payload JSON-serializzabile."""
    ts: float = field(default_factory=time.time)


@dataclass
class _TurnState:
    turn_id: str
    events: list[TurnEvent] = field(default_factory=list)
    closed: bool = False
    closed_at: float | None = None
    # Contesto per il recupero in-flight su reload pagina (turns_recent):
    # un turn ancora running NON è nei JSONL persistiti, quindi il client che
    # ricarica la chat (navigazione → dashboard → chat su Android) deve poterlo
    # ritrovare via event log filtrando per conversation+actor.
    conversation_id: str = ""
    actor: str = ""
    query: str = ""
    created_at: float = field(default_factory=time.time)
    # asyncio.Event per signalling: subscribers fanno wait() su questo
    # quando arrivano alla fine della lista; il publisher set() ad ogni
    # append + clear(). Pattern condition variable.
    cond: asyncio.Event = field(default_factory=asyncio.Event)


class TurnEventLog:
    """Singleton thread-safe del registro eventi per turn.

    Threading: append() puo' essere chiamato da thread non-asyncio (il
    run_turn() gira in executor). subscribe() e' asyncio nativo. Il
    cond.set() su thread esterno usa loop.call_soon_threadsafe.
    """

    _INSTANCE: "TurnEventLog | None" = None
    _LOCK_INIT = asyncio.Lock()

    def __init__(self) -> None:
        self._turns: dict[str, _TurnState] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def get(cls) -> "TurnEventLog":
        if cls._INSTANCE is None:
            cls._INSTANCE = cls()
        return cls._INSTANCE

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Associa il loop asyncio principale (chiamato all'avvio HTTP)."""
        self._loop = loop

    # ─── publisher API ────────────────────────────────────────────────────

    def create(self, turn_id: str, *, conversation_id: str = "",
               actor: str = "", query: str = "") -> None:
        """Inizializza il record del turno. Idempotente.

        gc() opportunistico: sfoltisce i turn chiusi oltre TTL prima di
        aggiungere, così il dict in-memory non cresce per tutta la vita del
        daemon HTTP (gc() non era invocata da nessuna parte → leak di RAM).

        `conversation_id`/`actor`/`query`: contesto per il recupero in-flight
        su reload pagina (vedi running_turns()).
        """
        self.gc()
        if turn_id not in self._turns:
            self._turns[turn_id] = _TurnState(
                turn_id=turn_id, conversation_id=conversation_id,
                actor=actor, query=query)

    def running_turns(self, conversation_id: str, actor: str) -> list[dict]:
        """Turn ancora in esecuzione (non chiusi) per una conversation+actor.

        Usato da `turns_recent` per esporre al client i turn in-flight che NON
        sono ancora nei JSONL persistiti (scritti solo a fine turno). Senza
        questo, ricaricare la chat mentre un turn gira lo perde → ⏳ mai
        risolto / falso errore.
        """
        out: list[dict] = []
        for st in self._turns.values():
            if st.closed:
                continue
            if st.conversation_id != conversation_id or st.actor != actor:
                continue
            out.append({
                "turn_id": st.turn_id,
                "query": st.query,
                "ts_start": st.created_at,
            })
        return out

    def append(self, turn_id: str, event_type: str,
                payload: dict) -> int:
        """Aggiunge un evento; ritorna l'event_id assegnato."""
        st = self._turns.get(turn_id)
        if st is None:
            self.create(turn_id)
            st = self._turns[turn_id]
        eid = len(st.events) + 1
        st.events.append(
            TurnEvent(id=eid, event_type=event_type, payload=payload)
        )
        # Notifica i subscribers asyncio. Se chiamato da thread non-loop,
        # usa call_soon_threadsafe; altrimenti set() diretto.
        self._notify(st)
        return eid

    def close(self, turn_id: str, *, error: str | None = None) -> None:
        """Marca il turn come chiuso. Subscribers wake-up e exit dopo
        aver consegnato gli ultimi eventi."""
        st = self._turns.get(turn_id)
        if st is None:
            return
        st.closed = True
        st.closed_at = time.time()
        if error:
            # Aggiungi un evento `error` finale se presente.
            self.append(turn_id, "error", {"message": error})
        else:
            self._notify(st)

    def _notify(self, st: _TurnState) -> None:
        # Threadsafe wake del cond event.
        if self._loop is None:
            try:
                st.cond.set()
            except Exception:
                pass
            return
        try:
            self._loop.call_soon_threadsafe(st.cond.set)
        except RuntimeError:
            # Loop chiuso; nothing to wake.
            pass

    # ─── subscriber API ────────────────────────────────────────────────────

    async def subscribe(self, turn_id: str, *,
                         last_event_id: int = 0,
                         heartbeat_s: float = 15.0
                         ) -> AsyncIterator[TurnEvent]:
        """Async iterator: yield eventi da last_event_id+1 in poi.

        Termina quando:
        - Il turn e' chiuso E tutti gli eventi sono stati consegnati.
        - L'iteratore viene cancellato dal caller (es. client disconnect).
        """
        st = self._turns.get(turn_id)
        if st is None:
            # Turno non noto: ritorna immediatamente senza eventi.
            # Il caller dovra' fallback al TurnLog persistente.
            return
        cursor = max(0, int(last_event_id))
        while True:
            # Consegna tutti gli eventi disponibili dopo il cursor.
            while cursor < len(st.events):
                yield st.events[cursor]
                cursor += 1
            if st.closed:
                # Tutti gli eventi consegnati, turno chiuso → done.
                return
            # Aspetta nuovo evento o close. Heartbeat: timeout periodico
            # cosi' il caller puo' emettere keepalive verso il client.
            try:
                await asyncio.wait_for(
                    st.cond.wait(), timeout=heartbeat_s,
                )
            except asyncio.TimeoutError:
                # Yield un evento sintetico heartbeat — il caller decide
                # se trasformarlo in `: keepalive` SSE.
                yield TurnEvent(
                    id=cursor, event_type="_heartbeat", payload={},
                )
                continue
            st.cond.clear()

    # ─── inspection / cleanup ──────────────────────────────────────────────

    def has(self, turn_id: str) -> bool:
        return turn_id in self._turns

    def stats(self) -> dict:
        active = sum(1 for s in self._turns.values() if not s.closed)
        return {
            "total_turns": len(self._turns),
            "active": active,
            "closed": len(self._turns) - active,
        }

    def gc(self) -> int:
        """Rimuove turn chiusi da piu' di TTL secondi.
        Ritorna numero di turn rimossi. Idempotente."""
        now = time.time()
        to_drop = [
            tid for tid, st in self._turns.items()
            if st.closed and st.closed_at is not None
            and (now - st.closed_at) > _TURN_EVENT_TTL_S
        ]
        for tid in to_drop:
            del self._turns[tid]
        return len(to_drop)


# ── SSE serializzazione helper ─────────────────────────────────────────────

def format_sse(event: TurnEvent) -> bytes:
    """Formatta un TurnEvent come SSE frame.

    Convenzioni:
    - `id: <N>` per Last-Event-ID semantics
    - `event: <type>` per dispatch lato client
    - `data: <json>` payload

    `_heartbeat` → comment SSE (`: keepalive`) cosi' EventSource lo
    ignora ma il browser vede bytes (keep-alive proxy/firewall).
    """
    if event.event_type == "_heartbeat":
        return b": keepalive\n\n"
    body = json.dumps(event.payload, ensure_ascii=False)
    return (
        f"id: {event.id}\n"
        f"event: {event.event_type}\n"
        f"data: {body}\n\n"
    ).encode("utf-8")


# ── Convenience: callable Progress compatibile con run_turn ────────────────

class TurnEventProgress:
    """Adapter Progress → TurnEventLog. Si comporta come l'oggetto
    `progress` atteso da run_turn (start, update_free, tool_call),
    ma scrive nel registro eventi durabile invece che direttamente
    nella response HTTP."""

    def __init__(self, turn_id: str,
                  log: TurnEventLog | None = None) -> None:
        self.turn_id = turn_id
        self._log = log or TurnEventLog.get()

    def start(self, message: str = "") -> None:
        self._log.append(
            self.turn_id, "start", {"message": message},
        )

    def update_free(self, message: str) -> None:
        self._log.append(
            self.turn_id, "update", {"message": message},
        )

    def tool_call(self, *, step_num: int, tool: str,
                   args_preview: dict | None = None,
                   **kwargs) -> None:
        """Compat con l'API Progress legacy: accetta args/path_so_far/
        predicted_remaining/... e li passa nel payload SSE. Il front-end
        decide quali campi mostrare."""
        payload = {"step": step_num, "tool": tool}
        if args_preview:
            payload["args_preview"] = args_preview
        # kwargs comuni dal call-site di run_turn:
        # args (dict), path_so_far (list[str]), predicted_remaining (list[str])
        for k, v in kwargs.items():
            payload[k] = v
        # Allinea la shape a _SSEProgress: la chat HTML legge `data.path` per il
        # breadcrumb badge (path crescente). Senza, mostrerebbe solo il tool
        # corrente. `path_so_far` → `path`.
        if "path" not in payload and "path_so_far" in payload:
            payload["path"] = payload.pop("path_so_far")
        self._log.append(self.turn_id, "tool_call", payload)

    def emit(self, event_type: str, payload: dict) -> None:
        """Escape hatch per evento custom."""
        self._log.append(self.turn_id, event_type, payload)
