"""runtime.channels.telegram — channel Telegram via Bot API.

MVP: send (channel_out) e poll (channel_in via long-polling getUpdates).
Niente webhook, niente porte aperte: il bot interroga in uscita
api.telegram.org. Coerente con la topologia "metnos-server in casa, nessun
ingresso non scelto" (vedi cap.4 architettura).

Credenziali in `~/.config/metnos/credentials.env`:
    TELEGRAM_BOT_TOKEN=...
    TELEGRAM_CHAT_ID=...   (utente fidato di default)
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Literal

from . import InboundMessage, OutboundMessage
import config as _C  # §7.11

API_BASE = "https://api.telegram.org/bot{token}/{method}"
FILE_BASE = "https://api.telegram.org/file/bot{token}/{file_path}"
DEFAULT_TIMEOUT_S = 25  # long-poll: Telegram raccomanda 25-50s
CREDENTIALS_FILE = _C.PATH_USER_CONFIG / "credentials.env"
DEFAULT_STATE_FILE = _C.PATH_USER_STATE / "telegram_offset"
UPLOAD_DIR = Path("/tmp/metnos_uploads")
PHOTO_DOWNLOAD_TIMEOUT_S = 30
PHOTO_MAX_BYTES = 25 * 1024 * 1024  # 25 MB hard cap


def _read_credentials(path: Path) -> dict[str, str]:
    """Legge credentials.env in stile dotenv (KEY=VALUE, # commenti)."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


class TelegramChannel:
    """Channel Telegram (channel_out + channel_in via long-poll)."""

    name = "telegram"

    def __init__(
        self,
        *,
        token: str | None = None,
        default_chat_id: str | None = None,
        credentials_path: Path | None = None,
        state_path: Path | None | Literal[False] = None,
    ):
        # 3 layer di risoluzione (ADR 0131 extended, 14/5/2026):
        #   1. arg esplicito (test/dependency injection),
        #   2. env var (override volatile),
        #   3. credentials store cifrato (domain `telegram_bot_token` /
        #      `telegram_chat_id_host`),
        #   4. file ~/.config/metnos/credentials.env (legacy fallback).
        store_token, store_chat = self._read_from_store()
        legacy = _read_credentials(credentials_path or CREDENTIALS_FILE)
        self.token = (
            token
            or os.environ.get("TELEGRAM_BOT_TOKEN")
            or store_token
            or legacy.get("TELEGRAM_BOT_TOKEN")
        )
        self.default_chat_id = (
            default_chat_id
            or os.environ.get("TELEGRAM_CHAT_ID")
            or store_chat
            or legacy.get("TELEGRAM_CHAT_ID")
        )
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN mancante (env, "
                             "credentials store, o credentials.env)")
        # state_path=False disabilita la persistenza (utile in test);
        # state_path=None usa il default; altrimenti il path indicato.
        if state_path is False:
            self.state_path: Path | None = None
        else:
            self.state_path = state_path or DEFAULT_STATE_FILE
        self._last_update_id: int | None = self._load_offset()

    @staticmethod
    def _read_from_store() -> tuple[str | None, str | None]:
        """Layer 1 (ADR 0131 extended): legge token+chat_id dallo store
        cifrato Fernet. Ritorna (None, None) se non disponibile."""
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            import credentials as _cr  # type: ignore[import-not-found]
        except ImportError:
            return None, None
        tok_payload = _cr.load("telegram_bot_token")
        chat_payload = _cr.load("telegram_chat_id_host")
        tok = (tok_payload.get("value")
                if isinstance(tok_payload, dict) else None)
        chat = (chat_payload.get("value")
                if isinstance(chat_payload, dict) else None)
        return tok, chat

    def _load_offset(self) -> int | None:
        if not self.state_path or not self.state_path.exists():
            return None
        try:
            raw = self.state_path.read_text(encoding="utf-8").strip()
            return int(raw) if raw else None
        except Exception:
            return None

    def _save_offset(self, update_id: int) -> bool:
        """Persiste atomicamente un offset già gestito dal daemon."""
        if not self.state_path:
            return True
        tmp_name = ""
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.state_path.parent, 0o700)
            except OSError:
                pass
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{self.state_path.name}.", suffix=".tmp",
                dir=str(self.state_path.parent),
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                fp.write(str(update_id))
                fp.flush()
                os.fsync(fp.fileno())
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, self.state_path)
            return True
        except Exception:
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
            return False

    def ack(self, message_or_update_id) -> bool:
        """Conferma un update solo DOPO che il daemon lo ha gestito.

        Monotono e idempotente. Se la persistenza fallisce, non avanza
        neppure l'offset in memoria: il messaggio verrà riproposto.
        """
        raw = message_or_update_id
        if isinstance(raw, InboundMessage):
            raw = (raw.extra or {}).get("update_id")
        try:
            update_id = int(raw)
        except (TypeError, ValueError):
            return False
        if self._last_update_id is not None and update_id <= self._last_update_id:
            return True
        if not self._save_offset(update_id):
            return False
        self._last_update_id = update_id
        return True

    # --- channel_out -------------------------------------------------------

    def prompt_location_share(self, *, chat_id: str, goal: str) -> dict:
        """Prompt richiesta posizione UX (regola §2-quater) con bottone
        inline 'Annulla' ai piedi del messaggio. Telegram NON supporta
        request_location=True in inline keyboard (solo in ReplyKeyboardMarkup),
        quindi la condivisione viene fatta dall'utente via:
          (1) icona graffetta 📎 → Posizione  (manuale)
          (2) testo libero indirizzo / CAP / citta'
          (3) tap bottone Annulla (callback loc_cancel)
        Multilingue via messages.py.
        """
        # runtime/ già su sys.path (channels VIVE in runtime/).
        from messages import get as _msg
        text = _msg("MSG_LOCATION_NEEDED", goal=goal)
        btn_cancel = _msg("MSG_BTN_CANCEL")
        reply_markup = json.dumps({
            "inline_keyboard": [[
                {"text": btn_cancel, "callback_data": "loc_cancel"},
            ]],
        })
        return self._call("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": reply_markup,
            "disable_web_page_preview": "true",
        })

    def clear_keyboard(self, *, chat_id: str, text: str) -> dict:
        """Manda messaggio di status (resolve/cancel). Niente reply_markup
        da rimuovere: ora prompt_location_share usa inline keyboard, che
        sparisce automaticamente quando l'utente clicca o quando arriva
        un nuovo messaggio (UX standard Telegram)."""
        return self._call("sendMessage", {
            "chat_id": chat_id,
            "text": text,
        })

    def send_to(self, chat_id: str, message: OutboundMessage) -> dict:
        """Wrapper esplicito per inviare a un chat_id specifico (multi-user
        4/5/2026, ADR 0083). Equivalente a `send(recipient=chat_id, message)`:
        il contratto di `send` accetta gia' `recipient` come chat_id, ma il
        nome `send_to` chiarisce l'intento dal lato chiamante (executor
        send_messages risolve user→chat_id e chiama questo metodo)."""
        return self.send(chat_id, message)

    def send(self, recipient: str, message: OutboundMessage) -> dict:
        chat_id = recipient or self.default_chat_id
        if not chat_id:
            return {"ok": False, "error": "chat_id mancante"}

        from .telegram_format import format_for_telegram

        chunks = format_for_telegram(message.text or "")
        if not chunks:
            chunks = [""]

        last_result = {"ok": True}
        delivered_chunks = 0
        for i, chunk in enumerate(chunks):
            # Rate-limit minimo fra chunk consecutivi (Telegram limit ~30
            # msg/s/bot in privato). 50 ms = 20 msg/s safe. ADR 0109.
            if i > 0:
                import time as _time
                _time.sleep(0.05)
            params = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            }
            # reply_to e buttons solo sul primo chunk
            if i == 0:
                if message.reply_to:
                    params["reply_to_message_id"] = message.reply_to
                if message.buttons:
                    params["reply_markup"] = json.dumps({
                        "inline_keyboard": [[{"text": b.get("text", "?"),
                                               "callback_data": b.get("data", "")}
                                              for b in row]
                                             for row in message.buttons]
                    })
            result = self._call("sendMessage", params)
            if not result.get("ok") and not result.get("delivery_ambiguous"):
                # Fallback plain: HTML rifiutato, riprova senza parse_mode
                import re as _re
                plain = _re.sub(r"<[^>]+>", "", chunk)
                params.pop("parse_mode", None)
                params["text"] = plain[:4096]
                result = self._call("sendMessage", params)
            if result.get("ok") is not True:
                if delivered_chunks:
                    # Retrying the whole logical message would duplicate the
                    # already accepted prefix.  Treat partial delivery as an
                    # ambiguous effect even when the current chunk was
                    # authoritatively rejected.
                    result = {
                        **result,
                        "delivery_ambiguous": True,
                        "partial_delivery": True,
                    }
                return result
            delivered_chunks += 1
            last_result = result
        return last_result

    def send_media_group(self, *, chat_id: str, attachments: list,
                         turn_id: str, caption_first: str | None = None) -> dict:
        """sendMediaGroup — album di foto upload binario (Telegram non puo
        raggiungere URL LAN-only). Cap a 10 media (limite Telegram).

        Caption della prima media = `caption_first` (es. summary breve);
        caption successive = `att.caption` se presente.
        """
        if not chat_id:
            return {"ok": False, "error": "chat_id mancante"}
        # runtime/ già su sys.path (channels VIVE in runtime/).
        import photo_endpoint  # noqa: E402

        atts = list(attachments or [])[:10]
        if not atts:
            return {"ok": True, "skipped": True}

        media = []
        files = []  # (form_name, filename, bytes)
        for i, att in enumerate(atts):
            data = photo_endpoint.get_thumb_bytes(turn_id, i, "thumb")
            if not data:
                continue
            form_name = f"photo{i}"
            cap = caption_first if (i == 0 and caption_first) else att.get("caption") or ""
            media.append({
                "type": "photo",
                "media": f"attach://{form_name}",
                "caption": cap[:1024],  # Telegram caption cap
            })
            files.append((form_name, att.get("basename") or f"photo{i}.jpg", data))

        if not media:
            return {"ok": False, "error": "no thumbnails available"}

        # Multipart manuale (urllib stdlib, niente requests).
        boundary = "----metnos" + os.urandom(8).hex()
        body = self._build_multipart(boundary, [
            ("chat_id", str(chat_id)),
            ("media", json.dumps(media, ensure_ascii=False)),
        ], files)
        url = API_BASE.format(token=self.token, method="sendMediaGroup")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read().decode("utf-8"))
            except Exception:
                err_body = {"description": str(e)}
            return {"ok": False, "error": err_body.get("description", "HTTPError"),
                    "status_code": e.code}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if resp.get("ok"):
            return {"ok": True,
                    "message_ids": [m.get("message_id") for m in resp.get("result", [])]}
        return {"ok": False, "error": resp.get("description", "unknown")}

    def send_document(self, *, chat_id: str, path: str,
                      basename: str | None = None,
                      caption: str | None = None) -> dict:
        """sendDocument — consegna un file deliverable (xlsx/doc/zip/pdf) come
        documento Telegram (upload binario; Telegram non raggiunge la LAN).
        Bug 5303699e: i create/write_files non arrivavano su chat."""
        if not chat_id:
            return {"ok": False, "error": "chat_id mancante"}
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as e:
            return {"ok": False, "error": f"read failed: {e}"}
        name = basename or os.path.basename(path) or "file"
        boundary = "----metnos" + os.urandom(8).hex()
        fields = [("chat_id", str(chat_id))]
        if caption:
            fields.append(("caption", caption[:1024]))
        body = self._build_multipart(boundary, fields, [("document", name, data)])
        url = API_BASE.format(token=self.token, method="sendDocument")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                resp = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read().decode("utf-8"))
            except Exception:
                err_body = {"description": str(e)}
            return {"ok": False, "error": err_body.get("description", "HTTPError"),
                    "status_code": e.code}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if resp.get("ok"):
            return {"ok": True, "message_id": resp.get("result", {}).get("message_id")}
        return {"ok": False, "error": resp.get("description", "unknown")}

    def send_dialog_preview_album(self, *, chat_id: str,
                                    attachments: list,
                                    reply_to: str | None = None) -> dict:
        """sendMediaGroup per dialog `choice_with_preview` (PR5).

        Diverso da `send_media_group`:
          - sorgente bytes = `att["tmp_path"]` (file gia' scritto a
            /tmp dal builder ChannelDaemon._send_choice_preview_album,
            invece che photo_endpoint thumb cache).
          - caption per ciascuna foto = `att["caption"]` (label opzione).
          - reply_to_message_id propagato sul primo media (Telegram
            API: campo a livello di sendMediaGroup, non per-media).

        Best-effort: ritorna {"ok": False, "error": ...} senza raise
        cosi' il caller puo' degradare a keyboard plain. Cap a 10
        garantito a monte (limite Telegram).
        """
        if not chat_id:
            return {"ok": False, "error": "chat_id mancante"}
        atts = list(attachments or [])[:10]
        if not atts:
            return {"ok": True, "skipped": True}

        media: list[dict] = []
        files: list[tuple[str, str, bytes]] = []
        for i, att in enumerate(atts):
            tp = att.get("tmp_path")
            if not tp:
                continue
            try:
                with open(tp, "rb") as fh:
                    data = fh.read()
            except OSError:
                # log.debug non disponibile qui; il caller logga
                continue  # noqa: E701
            form_name = f"photo{i}"
            cap = (att.get("caption") or "")[:1024]
            media.append({
                "type": "photo",
                "media": f"attach://{form_name}",
                "caption": cap,
            })
            files.append((form_name, att.get("basename") or f"photo{i}.jpg", data))

        if not media:
            return {"ok": False, "error": "no_thumbnails_loaded"}

        boundary = "----metnos" + os.urandom(8).hex()
        fields = [
            ("chat_id", str(chat_id)),
            ("media", json.dumps(media, ensure_ascii=False)),
        ]
        if reply_to:
            fields.append(("reply_to_message_id", str(reply_to)))
        body = self._build_multipart(boundary, fields, files)
        url = API_BASE.format(token=self.token, method="sendMediaGroup")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read().decode("utf-8"))
            except Exception:
                err_body = {"description": str(e)}
            return {"ok": False, "error": err_body.get("description", "HTTPError"),
                    "status_code": e.code}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if resp.get("ok"):
            return {"ok": True,
                    "message_ids": [m.get("message_id")
                                     for m in resp.get("result", [])]}
        return {"ok": False, "error": resp.get("description", "unknown")}

    @staticmethod
    def _build_multipart(boundary: str, fields: list, files: list) -> bytes:
        """Costruisce body multipart/form-data. fields=[(name,str)],
        files=[(form_name, filename, bytes)]."""
        b = boundary.encode()
        out = bytearray()
        for name, value in fields:
            out += b"--" + b + b"\r\n"
            out += (f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                    ).encode("utf-8")
            out += value.encode("utf-8") + b"\r\n"
        for form_name, filename, data in files:
            out += b"--" + b + b"\r\n"
            out += (f'Content-Disposition: form-data; name="{form_name}"; '
                    f'filename="{filename}"\r\n'
                    f'Content-Type: image/jpeg\r\n\r\n').encode("utf-8")
            out += data + b"\r\n"
        out += b"--" + b + b"--\r\n"
        return bytes(out)

    # --- photo download (ADR 0092, 5/5/2026) ------------------------------

    def _download_photo(self, file_id: str, *, chat_id: str, msg_id: str,
                         idx: int) -> str | None:
        """Scarica una foto Telegram dato il `file_id` (caption + photo).
        Ritorna il path locale assoluto, oppure None su errore.

        Pipeline: getFile(file_id) → file_path remoto → GET binario →
        salva in /tmp/metnos_uploads/<chat_id>/<msg_id>_<idx>.jpg.
        """
        info = self._call("getFile", {"file_id": file_id})
        if not info.get("ok"):
            return None
        file_path = (info.get("result") or {}).get("file_path")
        if not file_path:
            return None
        url = FILE_BASE.format(token=self.token, file_path=file_path)
        # cap bytes via Content-Length se disponibile.
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(
                req, timeout=PHOTO_DOWNLOAD_TIMEOUT_S
            ) as r:
                cl = r.headers.get("Content-Length")
                if cl and int(cl) > PHOTO_MAX_BYTES:
                    return None
                data = r.read(PHOTO_MAX_BYTES + 1)
                if len(data) > PHOTO_MAX_BYTES:
                    return None
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            return None
        # Estrai estensione dal file_path remoto, default .jpg.
        ext = ".jpg"
        if "." in file_path.rsplit("/", 1)[-1]:
            ext = "." + file_path.rsplit(".", 1)[-1].lower()
            # whitelist estensioni image: jpg/jpeg/png/webp/heic.
            if ext not in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
                ext = ".jpg"
        safe_chat = re.sub(r"[^A-Za-z0-9_.-]", "_", str(chat_id))
        safe_msg  = re.sub(r"[^A-Za-z0-9_.-]", "_", str(msg_id))
        out_dir = UPLOAD_DIR / safe_chat
        try:
            _C.ensure_private_dir(UPLOAD_DIR)
            _C.ensure_private_dir(out_dir)
        except OSError:
            return None
        out_path = out_dir / f"{safe_msg}_{idx}{ext}"
        descriptor = None
        temporary = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{out_path.name}.", suffix=".tmp",
                dir=str(out_dir))
            temporary = Path(temporary_name)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, out_path)
            temporary = None
            _C.ensure_private_file(out_path)
        except OSError:
            return None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
        return str(out_path)

    # --- channel_in (long-poll) -------------------------------------------

    def poll(self, *, timeout_s: int = DEFAULT_TIMEOUT_S) -> list[InboundMessage]:
        """Ritorna messaggi nuovi (offset Telegram update_id mantenuto in self).

        Bloccante fino a `timeout_s` secondi se non ci sono updates (questo e'
        il long-poll di Telegram: efficiente, evita polling stretto).
        """
        params = {"timeout": str(timeout_s)}
        if self._last_update_id is not None:
            params["offset"] = str(self._last_update_id + 1)
        resp = self._call("getUpdates", params, request_timeout_s=timeout_s + 10)
        if not resp.get("ok"):
            return []
        out: list[InboundMessage] = []
        for u in resp.get("result", []):
            uid = u.get("update_id")
            # callback_query: bottoni inline cliccati. Il "testo" e' il
            # callback_data; il dispatcher del daemon lo decodifica
            # (formato: "approve:<token>" / "reject:<token>").
            cbq = u.get("callback_query")
            if cbq:
                msg_ctx = cbq.get("message") or {}
                out.append(InboundMessage(
                    channel=self.name,
                    sender_id=str(cbq.get("from", {}).get("id") or msg_ctx.get("chat", {}).get("id", "")),
                    text=cbq.get("data", ""),
                    message_id=str(msg_ctx.get("message_id", "")),
                    received_at=float(msg_ctx.get("date", time.time())),
                    extra={"kind": "callback", "callback_id": cbq.get("id"),
                            "from": cbq.get("from", {}), "update_id": uid},
                ))
                # Telegram richiede di rispondere a ogni callback_query (anche
                # solo per togliere la rotella di caricamento sul bottone).
                cb_id = cbq.get("id")
                if cb_id:
                    self._call("answerCallbackQuery", {"callback_query_id": cb_id})
                continue

            msg = u.get("message") or u.get("edited_message")
            if not msg:
                if uid is not None:
                    out.append(InboundMessage(
                        channel=self.name, sender_id="", text="",
                        message_id="", received_at=time.time(),
                        extra={"kind": "transport_noop", "update_id": uid},
                    ))
                continue
            chat_id = str(msg.get("chat", {}).get("id", ""))
            # Location share (📎 Posizione su Telegram): il transport descrive
            # soltanto l'evento. Persistenza e associazione all'owner avvengono
            # nel daemon *dopo* pairing e revocation gate.
            loc = msg.get("location")
            if loc and "latitude" in loc and "longitude" in loc:
                lat_f = float(loc["latitude"])
                lon_f = float(loc["longitude"])
                acc = loc.get("horizontal_accuracy")
                # Propaga al daemon come InboundMessage SEMPRE (anche senza
                # text), perche' il daemon deve sapere di una location share
                # per gestire il dialog pending della regola §2-quater. Il
                # text vuoto + extra.kind="location_share" e' il segnale.
                # Se c'era anche text (caption), lo manteniamo.
                out.append(InboundMessage(
                    channel=self.name,
                    sender_id=chat_id,
                    text=msg.get("text", ""),
                    message_id=str(msg.get("message_id", "")),
                    received_at=float(msg.get("date", time.time())),
                    extra={"kind": "location_share", "lat": lat_f, "lon": lon_f,
                            "accuracy": acc, "update_id": uid},
                ))
                # Un update deve produrre un solo envelope ackabile. Il testo
                # eventuale resta sul location_share e viene gestito lì.
                continue

            # Photo allegate (ADR 0092): seleziona il file_id, ma non scarica
            # nulla nel transport. Il download avviene nel daemon soltanto
            # dopo l'ammissione del sender, evitando traffico e consumo disco
            # provocabili da utenti non pairati.
            photos = msg.get("photo")
            if photos and isinstance(photos, list) and photos:
                # Variante a max risoluzione: l'ultimo elemento (Bot API).
                # In test/edge case usa file_size se presente.
                try:
                    biggest = max(photos, key=lambda p: int(p.get("file_size") or 0))
                except (ValueError, TypeError):
                    biggest = photos[-1]
                file_id = biggest.get("file_id")
                msg_id_str = str(msg.get("message_id", ""))
                caption = msg.get("caption") or ""
                out.append(InboundMessage(
                    channel=self.name,
                    sender_id=chat_id,
                    text=caption,
                    message_id=msg_id_str,
                    received_at=float(msg.get("date", time.time())),
                    extra={
                        "from": msg.get("from", {}), "update_id": uid,
                        "photo_file_id": file_id,
                        "photo_index": 0,
                        "media_group_id": msg.get("media_group_id"),
                    },
                ))
                continue

            if "text" not in msg:
                if uid is not None:
                    out.append(InboundMessage(
                        channel=self.name, sender_id=chat_id, text="",
                        message_id=str(msg.get("message_id", "")),
                        received_at=float(msg.get("date", time.time())),
                        extra={"kind": "transport_noop", "update_id": uid},
                    ))
                continue
            out.append(InboundMessage(
                channel=self.name,
                sender_id=chat_id,
                text=msg["text"],
                message_id=str(msg.get("message_id", "")),
                received_at=float(msg.get("date", time.time())),
                extra={"from": msg.get("from", {}), "update_id": uid},
            ))
        return out

    # --- transport ---------------------------------------------------------

    def _call(self, method: str, params: dict[str, str], *, request_timeout_s: int = 15) -> dict:
        url = API_BASE.format(token=self.token, method=method)
        data = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=request_timeout_s) as r:
                body = r.read().decode("utf-8")
                response = json.loads(body)
                if isinstance(response, dict):
                    response.setdefault("delivery_ambiguous", False)
                return response
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read().decode("utf-8"))
            except Exception:
                err_body = {"description": str(e)}
            return {"ok": False, "error": err_body.get("description", "HTTPError"),
                    "status_code": e.code, "delivery_ambiguous": False}
        except Exception as e:
            return {
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "delivery_ambiguous": True,
            }
