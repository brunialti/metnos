"""runtime.progress — UX progress per operazioni lunghe (28/4/2026 sera).

Astrazione canale-agnostica per dare feedback all'utente durante operazioni
&gt; 5 s (synt multistage, batch fs_*, fetch lunghi). Tre primitive:

    progress = NullProgress() | TelegramProgress(channel, chat_id, ...)
    progress.start(header)              # apre il canale visivo
    progress.update(stage, label=None)  # checkpoint
    progress.finish(final_message)      # chiude

Disegno opzione C (scelta Roberto, 28/4 sera): five dots minimal `●○○○○`,
mapping 1:1 con i 5 stage del multistage. Combinato con sendChatAction
nativo Telegram ("typing...") in background per mantenere l'indicator
sempre vivo.

Per altri canali (HTML, voce, terminal) l'astrazione e' la stessa: basta
implementare un altro adapter.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from logging_setup import get_logger
log = get_logger(__name__)


STAGE_LABELS_5 = [
    "sto scegliendo il nome",
    "sto definendo gli argomenti",
    "sto scrivendo i test",
    "sto scrivendo la descrizione",
    "sto generando il codice",
]


def _dots(filled: int, total: int = 5) -> str:
    """`●●○○○` opzione C — 5 dots, mapping 1:1 con i 5 stage multistage."""
    filled = max(0, min(filled, total))
    return "●" * filled + "○" * (total - filled)


class Progress:
    """Interfaccia comune. Implementare `start/update/update_free/finish`."""

    def start(self, header: str) -> None:
        """Apre il canale visivo. Idempotente."""

    def update(self, stage: int, label: Optional[str] = None) -> None:
        """Modalita' synth: stage 0..N con dots."""

    def update_free(self, label: str) -> None:
        """Modalita' free-form per loop ReAct (no dots)."""

    def finish(self, message: str) -> None:
        """Chiude il canale con messaggio finale."""


class NullProgress(Progress):
    """No-op default. Usato quando non e' configurato nessun canale o per i test."""


class TelegramProgress(Progress):
    """sendMessage iniziale + editMessageText per stage updates.

    Loop background (thread daemon) che riemette `sendChatAction("typing")`
    ogni 4 s perche' Telegram fa decadere l'indicator dopo ~5 s.
    """

    CHAT_ACTION_INTERVAL_S = 4.0
    EDIT_RATE_LIMIT_MIN_INTERVAL_S = 0.5

    def __init__(
        self,
        channel,
        chat_id: str,
        *,
        total_stages: int = 5,
        stage_labels: Optional[list[str]] = None,
        upload_action_at_stage: Optional[int] = 5,
    ):
        self.channel = channel
        self.chat_id = chat_id
        self.total = total_stages
        self.labels = list(stage_labels or STAGE_LABELS_5)
        self.message_id: Optional[int] = None
        self._stop_event = threading.Event()
        self._action_thread: Optional[threading.Thread] = None
        self._header: str = ""
        self._current_stage: int = 0
        self._free_count: int = 0
        self._last_edit_ts: float = 0.0
        # Per coerenza tematica: durante lo stage di code generation usa
        # l'action `upload_document` invece di `typing` (icona doc che parte).
        self._upload_at = upload_action_at_stage

    def _render(self, stage: int) -> str:
        dots = _dots(stage, self.total)
        if stage <= 0:
            sub = "preparo la pipeline"
        elif stage <= len(self.labels):
            sub = self.labels[stage - 1]
        elif stage >= self.total:
            sub = "completato"
        else:
            sub = "in corso"
        # HTML: <code> rende monospace coi dots allineati su mobile
        return f"{self._header}\n<code>{dots}</code>  stage {min(stage, self.total)}/{self.total} · {sub}"

    def _action_loop(self):
        while not self._stop_event.is_set():
            try:
                action = "upload_document" if self._upload_at and self._current_stage == self._upload_at else "typing"
                self.channel._call("sendChatAction", {"chat_id": self.chat_id, "action": action})
            except Exception as _e:  # silent swallow (auto-fixed)
                log.warning("silent exception in %s: %s", __name__, _e)
            # Wait con timeout — interrompibile dal finish()
            if self._stop_event.wait(self.CHAT_ACTION_INTERVAL_S):
                break

    def start(self, header: str) -> None:
        """Idempotente: se chiamato due volte (daemon + handle_synth_request),
        solo aggiorna l'header tramite editMessageText."""
        self._header = header
        if self.message_id is not None:
            # gia' partito; aggiorna l'header senza nuovo messaggio
            self._edit(self._render(self._current_stage))
            return
        text = self._render(0)
        try:
            result = self.channel._call("sendMessage", {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            })
            if result.get("ok"):
                self.message_id = (result.get("result") or {}).get("message_id")
        except Exception:
            self.message_id = None
        # Anche se sendMessage fallisce, partiamo col loop typing per indicator nativo.
        self._action_thread = threading.Thread(target=self._action_loop, daemon=True)
        self._action_thread.start()

    def update_free(self, label: str) -> None:
        """Modalita' free-form per il loop ReAct: dots progressivi (1..5
        modulo 5 con cycle) per uniformita' visuale con la sintesi.
        Usata da run_turn ad ogni step."""
        self._free_count += 1
        n = ((self._free_count - 1) % self.total) + 1
        self._current_stage = n
        if self.message_id is None:
            return
        dots = _dots(n, self.total)
        self._edit(f"{self._header}\n<code>{dots}</code>  {label}")

    def _edit(self, text: str) -> None:
        if self.message_id is None:
            return
        now = time.time()
        if now - self._last_edit_ts < self.EDIT_RATE_LIMIT_MIN_INTERVAL_S:
            return
        try:
            self.channel._call("editMessageText", {
                "chat_id": self.chat_id,
                "message_id": self.message_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            })
            self._last_edit_ts = now
        except Exception as _e:  # silent swallow (auto-fixed)
            log.warning("silent exception in %s: %s", __name__, _e)
    def update(self, stage: int, label: Optional[str] = None) -> None:
        self._current_stage = stage
        if label and 0 < stage <= len(self.labels):
            self.labels[stage - 1] = label
        if self.message_id is None:
            return
        self._edit(self._render(stage))

    def finish(self, message: str, *, buttons: list | None = None) -> None:
        """Sostituisce il messaggio progress col final answer.

        Convenzione di formato: il final_answer del runtime e' Markdown
        (e.g. **bold**, < e > letterali). Telegram con parse_mode=HTML
        rifiuta `<` non chiusi e ignora `**`. Pipeline:
        1) Markdown → HTML via format_for_telegram (anche chunking).
        2) editMessageText sul primo chunk con parse_mode=HTML.
        3) Se HTML rifiutato (e.g. residui non chiusi): retry edit con
           tag stripped + parse_mode rimosso.
        4) Se ancora fallito o messaggio multi-chunk: invia come nuovi
           messaggi via channel.send (che ha il proprio fallback HTML→plain).
        Niente `except: pass` silenzioso: ogni errore conduce a un fallback
        attivo cosi' la risposta arriva all'utente (the design guide 2.8).

        `buttons` (10/6/2026): inline keyboard opzionale (list di rows
        [{text, data}]) allegata come reply_markup all'edit. Serve ai
        dialog get_inputs fmt='telegram_inline' e alle approvazioni
        admin: il percorso normale dei turni planner consegna il final
        QUI (edit del progress message), non via channel.send.
        """
        self._stop_event.set()
        if self._action_thread:
            self._action_thread.join(timeout=2.0)

        reply_markup = None
        if buttons:
            import json as _json
            reply_markup = _json.dumps({
                "inline_keyboard": [[{"text": b.get("text", "?"),
                                       "callback_data": b.get("data", "")}
                                      for b in row]
                                     for row in buttons]
            })

        # Format Markdown → HTML chunks (stesso pipeline di channel.send).
        from channels.telegram_format import format_for_telegram
        chunks = format_for_telegram(message or "") or [""]
        first = chunks[0]
        rest = chunks[1:]

        edited_ok = False
        if self.message_id is not None:
            params = {
                "chat_id": self.chat_id,
                "message_id": self.message_id,
                "text": first,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            }
            if reply_markup:
                params["reply_markup"] = reply_markup
            res = self.channel._call("editMessageText", params)
            edited_ok = bool(res.get("ok"))
            if not edited_ok:
                # Fallback: HTML rifiutato, tenta plain text (no parse_mode).
                import re as _re
                plain = _re.sub(r"<[^>]+>", "", first)[:4096]
                params_plain = {
                    "chat_id": self.chat_id,
                    "message_id": self.message_id,
                    "text": plain,
                    "disable_web_page_preview": "true",
                }
                if reply_markup:
                    params_plain["reply_markup"] = reply_markup
                res = self.channel._call("editMessageText", params_plain)
                edited_ok = bool(res.get("ok"))

        if not edited_ok:
            # Edit fallito o nessun progress message attivo: invia da capo.
            from channels import OutboundMessage
            self.channel.send(
                recipient=self.chat_id,
                message=OutboundMessage(text=message or "", buttons=buttons),
            )
            return

        # Multi-chunk: il primo e' stato editato, gli altri li mando come nuovi.
        for ch in rest:
            self.channel._call("sendMessage", {
                "chat_id": self.chat_id,
                "text": ch,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            })
