"""runtime.channels.daemon — main loop che lega un Channel al runtime.

Modello semplice (v1.1 MVP): un solo processo, un solo canale per istanza,
nessun threading. Long-poll del canale (Telegram = ~25s di attesa naturale)
+ chiamata sincrona a run_turn + risposta.

Uso:
    python3 -m channels.daemon              # default: TelegramChannel
    python3 -m channels.daemon --dry-run    # logga ma non risponde

Sicurezza:
- I sender devono essere riconosciuti dal modulo `pairing`. Il primo
  messaggio del `default_chat_id` (da credentials.env) viene auto-pairato
  come Full (bootstrap dev).
- Comando `/pair PAIR.<...>.<...>`: consuma il codice firmato e registra
  il pairing per quel channel+sender_id. Niente run_turn.
- Su exception di run_turn: log + risposta di errore al sender, daemon
  prosegue. Niente crash sul singolo turno.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import agent_server  # noqa: E402
import approval_registry  # noqa: E402
import pairing  # noqa: E402
from progress import NullProgress, TelegramProgress  # noqa: E402
from . import Channel, InboundMessage, OutboundMessage  # noqa: E402
from .telegram import TelegramChannel  # noqa: E402
import config as _C  # noqa: E402  §7.11

log = logging.getLogger("metnos.daemon")

DAEMON_LOCKFILE = Path(os.environ.get(
    "METNOS_DAEMON_LOCKFILE",
    str(_C.PATH_USER_STATE / "daemon.lock"),
))

# Burst aggregation per media_group_id (ADR 0092, 5/5/2026): Telegram invia
# album come messaggi separati con stesso `media_group_id` in arrivo a
# stretto giro (≤ 1-2 s). Accumuliamo i path delle foto fino al timeout o
# al primo update non-photo dello stesso sender, poi processiamo l'ultimo
# messaggio del gruppo come "carrier" della query (caption).
MEDIA_GROUP_BUFFER_DIR = Path("/tmp/metnos_uploads/_pending_burst")
MEDIA_GROUP_TTL_S = 1.5  # finestra di accumulo per gruppi multi-foto

# Cap-expand pending state (the design guide 2.11): file per sender_id che memorizza
# la proposta di rilancio con cap esteso emessa nel turno precedente.
# Quando l'utente risponde "sì" il daemon rilancia con cap nuovo; "no" pulisce.
CAP_PENDING_DIR = _C.PATH_USER_STATE / "cap_pending"
CAP_PENDING_TTL_S = 600  # 10 min: oltre, la proposta scade.
_YES_PATTERN = re.compile(r"\b(s[iì]|yes|y|ok|okay|alza|aumenta|rilancia|più)\b",
                          re.IGNORECASE)
_NO_PATTERN  = re.compile(r"\b(no|n|annulla|lascia|niente|stop)\b", re.IGNORECASE)


def _cap_pending_path(sender_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(sender_id))
    return CAP_PENDING_DIR / f"{safe}.json"


def _cap_pending_save(sender_id, original_query, proposal, turn_id):
    CAP_PENDING_DIR.mkdir(parents=True, exist_ok=True)
    p = _cap_pending_path(sender_id)
    p.write_text(json.dumps({
        "ts": time.time(),
        "turn_id": turn_id,
        "original_query": original_query,
        "proposal": proposal,
    }, ensure_ascii=False))


def _cap_pending_load(sender_id):
    p = _cap_pending_path(sender_id)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
    except Exception:
        return None
    if time.time() - float(d.get("ts", 0)) > CAP_PENDING_TTL_S:
        try: p.unlink()
        except OSError: pass
        return None
    return d


def _cap_pending_clear(sender_id):
    p = _cap_pending_path(sender_id)
    try: p.unlink()
    except FileNotFoundError: pass


def _media_group_path(sender_id: str, group_id: str) -> Path:
    safe_s = re.sub(r"[^A-Za-z0-9_.-]", "_", str(sender_id))
    safe_g = re.sub(r"[^A-Za-z0-9_.-]", "_", str(group_id))
    return MEDIA_GROUP_BUFFER_DIR / safe_s / f"{safe_g}.json"


def _media_group_load(sender_id: str, group_id: str) -> dict | None:
    p = _media_group_path(sender_id, group_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _media_group_save(sender_id: str, group_id: str, data: dict) -> None:
    p = _media_group_path(sender_id, group_id)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _media_group_clear(sender_id: str, group_id: str) -> None:
    p = _media_group_path(sender_id, group_id)
    try: p.unlink()
    except FileNotFoundError: pass


def _media_group_sweep_expired(now: float | None = None) -> list[tuple[str, str, dict]]:
    """Sweep buffer scaduti: ritorna [(sender_id, group_id, data)] dei gruppi
    pronti da processare (last_seen + TTL < now). Side-effect: rimuove i
    file processati."""
    if now is None:
        now = time.time()
    out: list[tuple[str, str, dict]] = []
    if not MEDIA_GROUP_BUFFER_DIR.exists():
        return out
    for sender_dir in MEDIA_GROUP_BUFFER_DIR.iterdir():
        if not sender_dir.is_dir():
            continue
        for f in sender_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                try: f.unlink()
                except OSError: pass
                continue
            last = float(data.get("last_seen") or 0)
            if now - last >= MEDIA_GROUP_TTL_S:
                out.append((sender_dir.name, f.stem, data))
                try: f.unlink()
                except OSError: pass
    return out


def _classify_yes_no(text: str) -> str:
    """Classifica una risposta come 'yes', 'no', o 'other'.
    Match su tutta la stringa, case-insensitive. Solo risposte CORTE
    (≤ 30 char) sono considerate conferma; query lunghe sono nuove
    richieste anche se contengono 'sì'/'no'."""
    if not text or len(text) > 30:
        return "other"
    if _YES_PATTERN.search(text):
        return "yes"
    if _NO_PATTERN.search(text):
        return "no"
    return "other"

PAIR_COMMAND = "/pair "
START_COMMAND = "/start "  # Multi-user pairing token (ADR 0083, 4/5/2026)
UNPAIRED_REPLY = (
    "Non ti riconosco su questo canale. Chiedi a Roberto un codice di pairing, "
    "poi inviamelo come `/pair <codice>`."
)
LEVEL_BLOCKS_RUN = {"ReadOnly"}  # questi non ottengono run_turn (per ora)
LEVEL_REPLY_BLOCKED = (
    "Sei pairato come {level}: posso solo leggere, non eseguire ancora azioni "
    "per te. Chiedi a Roberto di alzare il livello."
)


def _format_turn_result(result) -> str:
    """Riduce un TurnLog a una risposta testuale per il canale."""
    if hasattr(result, "final_message") and result.final_message:
        return result.final_message
    if hasattr(result, "final_kind"):
        return f"(turno chiuso senza testo: {result.final_kind})"
    return str(result)


def parse_step_value(raw: str, schema: dict) -> tuple[bool, object, str]:
    """Parser deterministico di una risposta utente secondo `schema.kind`
    (ADR 0090). Ritorna `(ok, value, error)`. Niente LLM.

    Kinds supportati MVP:
      - text / file_path / location: pass-through (location come stringa
        "lat,lon" oppure "indirizzo"; resolution lasciata a step esterni).
      - credentials: pass-through (la mascheratura e' UI, non parsing).
      - yes_no: tollerante (si/sì/yes/y/ok/true ↔ no/n/false/0).
      - choice: deve coincidere (case-insensitive) con uno dei choices,
        oppure essere l'indice 1-based del choice.
      - multi_choice: CSV o newline-separated; ogni token deve matchare.
      - number: int/float secondo il valore.
      - date: ISO YYYY-MM-DD oppure variante DD/MM/YYYY (best-effort).
    """
    kind = (schema or {}).get("kind")
    s = (raw or "").strip()
    if not s:
        return False, None, "Risposta vuota. Riprova."
    if kind in ("text", "credentials", "file_path", "location"):
        return True, s, ""
    if kind == "yes_no":
        low = s.lower()
        if low in ("si", "sì", "yes", "y", "ok", "okay", "true", "1"):
            return True, True, ""
        if low in ("no", "n", "false", "0", "annulla"):
            return True, False, ""
        return False, None, "Rispondi `sì` o `no`."
    if kind == "number":
        try:
            if "." in s or "e" in s.lower():
                return True, float(s), ""
            return True, int(s), ""
        except ValueError:
            return False, None, "Inserisci un numero (es. 42 o 3.14)."
    if kind == "date":
        m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
        if m_iso:
            return True, s, ""
        m_eu = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
        if m_eu:
            d, m, y = m_eu.groups()
            return True, f"{int(y):04d}-{int(m):02d}-{int(d):02d}", ""
        return False, None, "Inserisci una data (formato 2026-05-04 o 04/05/2026)."
    if kind == "choice":
        choices = (schema or {}).get("choices") or []
        # Choices possono essere stringhe (esplicite) o dict {label, value}
        # (derivati da entries via display_template, ADR 0127).
        for c in choices:
            if isinstance(c, dict):
                if str(c.get("value", "")).lower() == s.lower():
                    return True, c.get("value"), ""
                if str(c.get("label", "")).lower() == s.lower():
                    return True, c.get("value"), ""
            else:
                if str(c).lower() == s.lower():
                    return True, c, ""
        try:
            idx = int(s)
            if 1 <= idx <= len(choices):
                pick = choices[idx - 1]
                if isinstance(pick, dict):
                    return True, pick.get("value"), ""
                return True, pick, ""
        except ValueError:
            pass
        # Messaggio: usa label se dict, altrimenti str.
        def _label(c):
            return c.get("label", c.get("value", "?")) if isinstance(c, dict) else str(c)
        return False, None, (
            "Scegli una fra: " + ", ".join(_label(c) for c in choices) +
            " (oppure il numero d'ordine)."
        )
    if kind == "choice_with_preview":
        # Schema PR5: options=[{value,label,preview_image_path}]. In
        # modalita' dialogue (fallback >10 opzioni) l'utente risponde
        # col `value` letterale, col `label` esatto, oppure con l'indice
        # 1-based. Ritorniamo sempre il `value` (consumato dal callback).
        options = (schema or {}).get("options") or []
        for opt in options:
            if str(opt.get("value", "")).lower() == s.lower():
                return True, opt.get("value"), ""
            if str(opt.get("label", "")).lower() == s.lower():
                return True, opt.get("value"), ""
        try:
            idx = int(s)
            if 1 <= idx <= len(options):
                return True, options[idx - 1].get("value"), ""
        except ValueError:
            pass
        return False, None, (
            "Scegli una fra: "
            + ", ".join(str(opt.get("label", opt.get("value", "?")))
                         for opt in options)
            + " (oppure il numero d'ordine)."
        )
    if kind == "multi_choice":
        choices = (schema or {}).get("choices") or []
        tokens = [t.strip() for t in s.replace("\n", ",").split(",") if t.strip()]
        picked = []
        for tok in tokens:
            match = None
            for c in choices:
                if str(c).lower() == tok.lower():
                    match = c; break
            if match is None:
                try:
                    idx = int(tok)
                    if 1 <= idx <= len(choices):
                        match = choices[idx - 1]
                except ValueError:
                    pass
            if match is None:
                return False, None, (
                    f"Token '{tok}' non riconosciuto. Scelte ammesse: "
                    + ", ".join(str(c) for c in choices)
                )
            picked.append(match)
        return True, picked, ""
    return False, None, f"kind {kind!r} non supportato dal parser."


class ChannelDaemon:
    """Loop poll → run_turn → send su un Channel."""

    def __init__(
        self,
        channel: Channel,
        *,
        run_turn=None,
        dry_run: bool = False,
        bootstrap_default_sender: bool = True,
    ):
        self.channel = channel
        self.run_turn = run_turn  # iniettabile per test; default = lazy import
        self.dry_run = dry_run
        self.bootstrap_default_sender = bootstrap_default_sender
        self._stop = False
        # Sync pairings.db → users.user_channels al boot: riconcilia bootstrap
        # Telegram (default_chat_id) + /pair PAIR.<token> coi binding identita'
        # multi-user (ADR 0083). Senza questo /admin/users mostra channels=[]
        # per host bootstrappato. Idempotente, deterministico (§7.9).
        try:
            # runtime/ già su sys.path (channels VIVE in runtime/).
            import users_pairings_sync as _ups
            _stats = _ups.sync_pairings_to_user_channels()
            log.info("users_pairings_sync at daemon start (%s): %s",
                      channel.name, _stats)
        except Exception as ex:
            log.warning("users_pairings_sync at daemon start failed: %s", ex)

        # Sync users.email → user_channels(channel="mail"): popola pairing
        # email implicito dai dati anagrafici (14/5/2026). Senza, send_messages
        # fallisce con channel_not_paired per utenti con email in users ma
        # mancante in user_channels. Idempotente §7.9.
        try:
            import users_email_sync as _ues
            _e_stats = _ues.sync_users_email_to_user_channels()
            log.info("users_email_sync at daemon start (%s): %s",
                      channel.name, _e_stats)
        except Exception as ex:
            log.warning("users_email_sync at daemon start failed: %s", ex)

    def _resolve_run_turn(self):
        if self.run_turn is not None:
            return self.run_turn
        # Lazy import per evitare ciclo
        from agent_runtime import run_turn as _rt
        return _rt

    def stop(self):
        self._stop = True

    def _send_text(self, recipient: str, text: str, reply_to: str | None = None) -> dict:
        if self.dry_run:
            log.info("dry-run: avrei inviato a %s: %r", recipient, text[:120])
            return {"ok": True, "dry_run": True}
        return self.channel.send(
            recipient=recipient,
            message=OutboundMessage(text=text, reply_to=reply_to),
        )

    def _consume_admin_approval(self, proposal: dict, *,
                                 actor: str = "host") -> str:
        """Esegui un admin pending dopo conferma utente (ADR 0088).

        Riprende argomenti originali + actor_consent_token dalla proposal,
        invoca direttamente admin via verb-unique registry. Niente PLANNER
        round-trip. Ritorna il testo da mandare all'utente come esito.
        """
        from loader import invoke_verb_unique
        args = dict(proposal.get("args_suggested") or {})
        try:
            res = invoke_verb_unique(
                "admin", caller="agent_runtime",
                intent=args.get("intent", ""),
                command_proposed=args.get("command_proposed", ""),
                credentials_domain=args.get("credentials_domain"),
                actor_consent_token=args.get("actor_consent_token"),
                actor=actor,
            )
        except (PermissionError, KeyError, RuntimeError) as e:
            log.exception("admin approval consume failed")
            return f"(esecuzione fallita: {type(e).__name__}: {e})"
        if isinstance(res, dict):
            return res.get("summary") or json.dumps(res, ensure_ascii=False)[:600]
        return str(res)

    def _consume_approval_required(self, proposal: dict, *,
                                     actor: str = "host") -> str:
        """Esegui un approval pending (find_images_indices build) dopo
        conferma utente. Direct invoke senza PLANNER (mirror del path HTTP
        in `http_routes_agent._apply_cap_pending`)."""
        executor_name = proposal.get("executor") or ""
        args = dict(proposal.get("args_suggested") or {})
        try:
            from loader import load_catalog
            cat = load_catalog(verify=True, include_synth=True)
            ex = cat.executors.get(executor_name)
            if ex is None:
                return f"(executor {executor_name} non in catalog)"
            import agent_runtime as _ar
            res = _ar.invoke_executor(
                ex, args, timeout_s=getattr(ex, "timeout_s", 30),
                actor=actor, channel="telegram",
            )
        except (PermissionError, KeyError, RuntimeError, TypeError) as e:
            log.exception("approval_required consume failed")
            return f"(esecuzione fallita: {type(e).__name__}: {e})"
        if not isinstance(res, dict) or not res.get("ok"):
            err = (res or {}).get("error", "errore sconosciuto") if isinstance(res, dict) else "no result"
            return f"Rilancio fallito: {err}"
        return res.get("summary") or json.dumps(res, ensure_ascii=False)[:600]

    def _parse_step_value(self, raw: str, schema: dict) -> tuple[bool, object, str]:
        """Wrapper di compatibilita': delega a `parse_step_value` modulo-level."""
        return parse_step_value(raw, schema)

    def _consume_get_inputs_response(self, proposal: dict, msg_text: str,
                                      *, actor: str = "host",
                                      sender_id: str = "") -> tuple[str | None, bool, str | None]:
        """Consuma una risposta utente per un dialogo `get_inputs` pendente
        (ADR 0090). Ritorna `(reply_text, retry_original, completion_summary)`:

          - reply_text: testo da inviare subito (prossima domanda, errore di
            parsing, conferma di cancel). None = niente reply diretto.
          - retry_original: True se il dialogo e' COMPLETATO e il daemon deve
            ri-eseguire la query originale per chiudere il flow del PLANNER.
          - completion_summary: testo riassuntivo da inviare quando il
            dialogo si completa (mostra le var raccolte; mai i valori
            credentials in chiaro).
        """
        try:
            # runtime/ già su sys.path (channels VIVE in runtime/).
            import dialog_pending as _dp
        except ImportError as ex:
            log.warning("dialog_pending non disponibile: %s", ex)
            return None, False, None

        dialog_id = proposal.get("dialog_id") or ""
        sender_for_state = proposal.get("sender_for_state") or sender_id
        text_norm = (msg_text or "").strip().lower()

        if text_norm in ("annulla", "cancel", "abort", "stop"):
            _dp.cancel_pending(sender_for_state, dialog_id)
            return ("Dialogo annullato. I valori non sono stati salvati.",
                    False, None)

        state = _dp.load_pending(sender_for_state, dialog_id)
        if state is None:
            return ("(Dialogo scaduto o sconosciuto. Riformula la richiesta.)",
                    False, None)
        dialog = state.get("dialog") or []
        idx = int(state.get("step_index") or 0)
        if idx >= len(dialog):
            # Stato inconsistente: marca completed, segnala
            return None, True, None
        cur_step = dialog[idx]
        var = cur_step.get("var")
        schema = cur_step.get("schema") or {}
        schema_kind = (schema or {}).get("kind")
        ok, value, err = self._parse_step_value(msg_text, schema)
        if not ok:
            # Per dialog yes_no (cap-expand tipico): se l'utente scrive
            # un testo lungo (>10 char) invece di sì/no, e' una query
            # nuova — non un re-prompt. Cancel del dialogo (equivalente
            # a "no") e segnala al daemon di processare il messaggio
            # come turno nuovo. Senza questo l'utente resta bloccato.
            text_len = len((msg_text or "").strip())
            if schema_kind == "yes_no" and text_len > 10:
                _dp.cancel_pending(sender_for_state, dialog_id)
                # Marker speciale (None, None, None): il caller deve
                # leggere come "passthrough" e processare il messaggio
                # come query nuova.
                return None, False, None
            # Ri-prompt dello stesso step
            return (f"{err}\n\nStep {idx+1}/{len(dialog)} — {cur_step.get('prompt')}",
                    False, None)
        # Avanza lo stato
        cres = _dp.consume_pending_step(sender_for_state, dialog_id, var, value)
        if not cres.get("ok"):
            return (f"(Errore stato dialogo: {cres.get('error')})",
                    False, None)
        if cres.get("completed"):
            new_state = cres["state"]
            # Summary all'utente: mostra le var raccolte. Maschera i valori
            # con kind credentials per non echeggiare password in chat.
            lines = [f"Dialogo «{state.get('title','?')}» completato."]
            for s in dialog:
                v = s.get("var")
                k = (s.get("schema") or {}).get("kind")
                val = new_state["values_collected"].get(v)
                if k == "credentials" or (s.get("schema") or {}).get("secret"):
                    lines.append(f"  {v}: ********")
                else:
                    lines.append(f"  {v}: {val}")
            summary = "\n".join(lines)
            return None, True, summary
        # Prossimo step
        next_step = dialog[idx + 1]
        next_prompt = next_step.get("prompt") or "?"
        next_kind = (next_step.get("schema") or {}).get("kind")
        masked_hint = ""
        if next_kind == "credentials":
            masked_hint = "\n(la risposta sara' mascherata in registro)"
        return (f"Step {idx+2}/{len(dialog)} — {next_prompt}{masked_hint}",
                False, None)

    def _on_get_inputs_completed(self, state: dict, *,
                                  actor: str = "host") -> str | None:
        """Hook on-completion: applica callback dichiarativo `on_complete`
        (ADR 0091) o, per dialoghi legacy senza callback, il vecchio
        side-effect ad-hoc (salvataggio credenziali da `credentials_domain`).

        Ritorna il messaggio user-facing prodotto dal callback (es. esito
        del resume_call admin), oppure None se non c'e' nulla da inviare.
        Il caller (handle del messaggio) decide se inviarlo come testo
        separato dopo il summary del dialogo.
        """
        # Path nuovo (ADR 0091): on_complete callback persisto nel state.
        on_complete = state.get("on_complete")
        if isinstance(on_complete, dict):
            sender_id = state.get("sender_id") or ""
            dialog_id = state.get("dialog_id") or ""
            if not sender_id or not dialog_id:
                log.warning("on_complete callback ma sender_id/dialog_id "
                             "mancanti nello state")
                return None
            try:
                # runtime/ già su sys.path (channels VIVE in runtime/).
                from orchestration import process_completion_callback
                msg_back = process_completion_callback(
                    sender_id, dialog_id,
                    actor=actor or state.get("actor") or "host",
                    channel=state.get("channel") or None,
                )
                return msg_back
            except (ImportError, RuntimeError) as ex:
                log.exception("process_completion_callback fallito")
                return f"(Callback fallito: {type(ex).__name__}: {ex})"
        # Path legacy (state senza on_complete; i dialog state vecchi non
        # hanno il campo, finche' la migrazione non e' su tutti i nodi).
        values = state.get("values_collected") or {}
        cred_domain = state.get("credentials_domain")
        if not cred_domain:
            return None
        username = values.get("username") or values.get("user")
        password = values.get("password") or values.get("pwd")
        if not username or not password:
            return None
        try:
            # runtime/ già su sys.path (channels VIVE in runtime/).
            import credentials as _cred  # noqa: WPS433
            _cred.store(cred_domain, {
                "username": username,
                "password": password,
            })
            log.info("get_inputs (legacy): credentials saved per dominio %s",
                     cred_domain)
        except Exception as ex:
            log.warning("get_inputs: store credenziali fallito: %s", ex)
        return None

    # _consume_credentials_required RIMOSSO 5/5/2026 (ADR 0091).
    # Il pattern Strato 2 ad-hoc e' stato sostituito da get_inputs +
    # on_complete callback. Il branch `kind="credentials_required"` nel
    # cap-pending registry e' obsoleto e rimosso (vedi _handle_message).

    def _try_bootstrap(self, msg: InboundMessage) -> pairing.Pairing | None:
        """Auto-pair come Full il default_chat_id, se non esistono pairing."""
        if not self.bootstrap_default_sender:
            return None
        default = getattr(self.channel, "default_chat_id", None)
        if not default or str(default) != msg.sender_id:
            return None
        try:
            p = pairing.bootstrap_default_chat_id(self.channel.name, msg.sender_id)
            log.info("bootstrap: pairato %s/%s come Full", self.channel.name, msg.sender_id)
            return p
        except pairing.PairingError as e:
            log.debug("bootstrap rifiutato: %s", e)
            return None

    def _handle_pair_command(self, msg: InboundMessage) -> dict:
        code = msg.text[len(PAIR_COMMAND):].strip()
        try:
            p = pairing.consume_code(code, self.channel.name, msg.sender_id)
        except pairing.PairingError as e:
            self._send_text(msg.sender_id,
                            f"Pairing fallito: {e}", reply_to=msg.message_id)
            return {"ok": False, "reason": "pairing_failed", "error": str(e)}
        self._send_text(msg.sender_id,
                        f"Pairato come {p.autonomy_level}. Benvenuto.",
                        reply_to=msg.message_id)
        return {"ok": True, "paired": p.autonomy_level}

    def _handle_start_command(self, msg: InboundMessage) -> dict:
        """Gestisce `/start <pairing_token>` (multi-user, ADR 0083, 4/5/2026).

        Distinto da `/pair` (codice firmato Ed25519, autonomy_level): il
        token di `/start` viene emesso dall'admin UI per legare un
        Telegram chat_id a uno user logico (host o guest) preesistente.
        Niente firma: il token e' un secret one-shot a TTL breve.
        """
        token = msg.text[len(START_COMMAND):].strip()
        if not token:
            self._send_text(msg.sender_id,
                            "Manca il token. Uso: /start <token>",
                            reply_to=msg.message_id)
            return {"ok": False, "reason": "missing_token"}
        try:
            # runtime/ già su sys.path (channels VIVE in runtime/).
            import users as _users
        except ImportError as e:
            log.warning("users module unavailable: %s", e)
            self._send_text(msg.sender_id,
                            "Servizio utenti non disponibile.",
                            reply_to=msg.message_id)
            return {"ok": False, "reason": "users_unavailable"}
        try:
            user = _users.consume_pairing_token(
                "telegram", msg.sender_id, token,
            )
        except ValueError as e:
            log.info("start token rifiutato: %s", e)
            self._send_text(msg.sender_id,
                            "Token scaduto o invalido.",
                            reply_to=msg.message_id)
            return {"ok": False, "reason": "invalid_token", "error": str(e)}
        name = user.get("name", "?")
        role = user.get("role", "?")
        self._send_text(
            msg.sender_id,
            f"Sei stato pairato come {name} ({role}). Benvenuto/a in Metnos.",
            reply_to=msg.message_id,
        )
        return {"ok": True, "user_id": user.get("id"), "name": name, "role": role}

    def _build_dialog_keyboard(self, dialog_id: str, step_idx: int,
                                 step: dict) -> list[list[dict]]:
        """Costruisce la inline keyboard per uno step yes_no/choice.

        Formato `buttons` = list di rows, ogni row list di dict
        {text, data}. callback_data = `dlg:<dialog_id>:<step_idx>:<value>`
        + caso speciale `dlg:<dialog_id>:cancel`. Telegram limita
        callback_data a 64 byte: per choice con label lunghi usiamo
        l'indice della choice come value (`dlg:<id>:<step>:c<idx>`),
        risolto al click via lookup nello state.
        """
        from messages import get as _msg
        kind = (step.get("schema") or {}).get("kind")
        rows: list[list[dict]] = []
        prefix = f"dlg:{dialog_id}:{step_idx}"
        if kind == "yes_no":
            rows.append([
                {"text": _msg("MSG_BTN_YES"), "data": f"{prefix}:yes"},
                {"text": _msg("MSG_BTN_NO"),  "data": f"{prefix}:no"},
            ])
        elif kind == "choice":
            choices = (step.get("schema") or {}).get("choices") or []
            for i, label in enumerate(choices):
                rows.append([
                    {"text": str(label), "data": f"{prefix}:c{i}"},
                ])
        elif kind == "choice_with_preview":
            # PR5: i thumb sono inviati separatamente come media group.
            # La keyboard mostra solo i label per il tap. callback_data
            # `c<idx>` (compatibile con choice) per consistenza decode.
            options = (step.get("schema") or {}).get("options") or []
            for i, opt in enumerate(options):
                rows.append([
                    {"text": str(opt.get("label", opt.get("value", f"#{i+1}"))),
                     "data": f"{prefix}:c{i}"},
                ])
        rows.append([
            {"text": _msg("MSG_BTN_CANCEL"), "data": f"dlg:{dialog_id}:cancel"},
        ])
        return rows

    # PR5: limite Telegram per sendMediaGroup album = 10 media.
    # Sopra il limite degradiamo a kind="choice" plain (senza preview)
    # per non perdere visibilita' del label e mantenere la scelta possibile.
    _PREVIEW_MAX_OPTIONS = 10

    def _send_choice_preview_album(self, *, chat_id: str, step: dict,
                                    reply_to: str | None = None) -> dict:
        """Invia le miniature di uno step `choice_with_preview` come album
        Telegram (sendMediaGroup). Crop bbox al volo via dialog_preview.

        Caption sotto ogni foto = `label`. Best-effort: se una thumb
        fallisce, viene saltata; almeno 1 foto -> manda l'album, 0 ->
        ritorna {ok:false} cosi' il caller puo' degradare a kind=choice.
        """
        import os as _os
        import tempfile
        # runtime/ già su sys.path (channels VIVE in runtime/).
        import dialog_preview as _dpv

        options = (step.get("schema") or {}).get("options") or []
        if not options:
            return {"ok": False, "error": "no_options"}
        if len(options) > self._PREVIEW_MAX_OPTIONS:
            return {"ok": False, "error": "too_many_options",
                    "n_options": len(options)}

        attachments: list[dict] = []
        tmp_files: list[str] = []
        try:
            for i, opt in enumerate(options):
                spec = opt.get("preview_image_path") or ""
                try:
                    path, bbox = _dpv.validate_preview_spec(
                        spec, require_exists=True,
                    )
                    body = _dpv.crop_image_bytes(path, bbox)
                except (ValueError, OSError) as ex:
                    log.warning("preview thumb failed for option %d: %s", i, ex)
                    continue
                # Scrivi su tmp file: il TelegramChannel.send_media_group
                # legge da photo_endpoint (che cerca turn_id) — quindi
                # qui passiamo bytes via un attachment-shape dedicato:
                # `tmp_path` in attachment dict + caption `label`.
                f = tempfile.NamedTemporaryFile(
                    prefix=f"metnos_dlg_thumb_{i}_", suffix=".jpg",
                    delete=False,
                )
                f.write(body); f.close()
                tmp_files.append(f.name)
                attachments.append({
                    "kind": "image",
                    "tmp_path": f.name,
                    "basename": _os.path.basename(f.name),
                    "caption": str(opt.get("label") or "")[:1000],
                })
            if not attachments:
                return {"ok": False, "error": "no_thumbs_built"}
            # Send via channel.send_dialog_preview_album (helper sul
            # TelegramChannel). Cap a 10 garantito a monte.
            send = getattr(self.channel, "send_dialog_preview_album", None)
            if send is None:
                return {"ok": False, "error": "channel_unsupported"}
            res = send(chat_id=chat_id, attachments=attachments,
                       reply_to=reply_to)
            return res
        finally:
            for tf in tmp_files:
                try: _os.unlink(tf)
                except OSError: pass

    def _handle_dialog_callback(self, msg: InboundMessage,
                                  data: str) -> dict:
        """Gestisce callback_data prefissato `dlg:`. Decodifica + consume
        dello step corrente + invio prompt prossimo step (o completion).

        Stesso pipeline di `_consume_get_inputs_response` (single source
        of truth: validation + advance state + on_complete callback).
        Differenza: il valore arriva da callback_data invece che da
        msg.text utente, e il prompt successivo include la keyboard.
        """
        from messages import get as _msg
        try:
            # runtime/ già su sys.path (channels VIVE in runtime/).
            import dialog_pending as _dp
        except ImportError as ex:
            log.warning("dialog_pending non disponibile: %s", ex)
            return {"ok": False, "reason": "dialog_pending_unavailable"}

        # Parse: dlg:<dialog_id>:<step_idx>:<value>  OR  dlg:<dialog_id>:cancel
        parts = data.split(":", 3)
        if len(parts) < 3 or parts[0] != "dlg":
            return {"ok": False, "reason": "bad_callback_data"}
        dialog_id = parts[1]

        # sender_for_state e' la stessa chiave usata in
        # _consume_get_inputs_response. Per coerenza, riusiamo la
        # convention `<channel>:<chat_id>` quando il proposal non
        # esplicita altro (sender_id e' gia' chat_id su Telegram).
        sender_for_state = f"{self.channel.name}:{msg.sender_id}"
        # Fallback senza prefisso (alcune proposal lo usano cosi').
        state = (_dp.load_pending(sender_for_state, dialog_id)
                 or _dp.load_pending(msg.sender_id, dialog_id))
        if state is None:
            self._send_text(msg.sender_id,
                             _msg("MSG_DIALOG_EXPIRED"),
                             reply_to=msg.message_id)
            return {"ok": False, "reason": "dialog_expired"}
        # Risolvi sender_for_state effettivo dal load (per consume coerente).
        sender_eff = state.get("sender_id") or sender_for_state
        if parts[2] == "cancel":
            _dp.cancel_pending(sender_eff, dialog_id)
            _cap_pending_clear(msg.sender_id)
            self._send_text(msg.sender_id,
                             _msg("MSG_DIALOG_CANCELLED"),
                             reply_to=msg.message_id)
            return {"ok": True, "callback": "dlg_cancel"}

        try:
            step_idx = int(parts[2])
        except ValueError:
            return {"ok": False, "reason": "bad_step_idx"}
        raw_value = parts[3] if len(parts) > 3 else ""
        dialog = state.get("dialog") or []
        if step_idx >= len(dialog):
            return {"ok": False, "reason": "step_out_of_range"}
        cur_step = dialog[step_idx]
        kind = (cur_step.get("schema") or {}).get("kind")
        # Decodifica il value reale dalla callback_data.
        if kind == "yes_no":
            value = True if raw_value == "yes" else False
        elif kind == "choice":
            choices = (cur_step.get("schema") or {}).get("choices") or []
            if raw_value.startswith("c"):
                try:
                    ci = int(raw_value[1:])
                except ValueError:
                    return {"ok": False, "reason": "bad_choice_idx"}
                if ci < 0 or ci >= len(choices):
                    return {"ok": False, "reason": "choice_out_of_range"}
                value = choices[ci]
            else:
                return {"ok": False, "reason": "bad_choice_format"}
        elif kind == "choice_with_preview":
            # PR5: stesso formato `c<idx>` di choice; risolve sull'option
            # corrispondente e ritorna il `value` (non il label).
            options = (cur_step.get("schema") or {}).get("options") or []
            if raw_value.startswith("c"):
                try:
                    ci = int(raw_value[1:])
                except ValueError:
                    return {"ok": False, "reason": "bad_choice_idx"}
                if ci < 0 or ci >= len(options):
                    return {"ok": False, "reason": "choice_out_of_range"}
                value = options[ci].get("value")
            else:
                return {"ok": False, "reason": "bad_choice_format"}
        else:
            return {"ok": False, "reason": f"unsupported_kind:{kind}"}

        cres = _dp.consume_pending_step(sender_eff, dialog_id,
                                          cur_step.get("var"), value)
        if not cres.get("ok"):
            self._send_text(msg.sender_id,
                             f"(Errore stato dialogo: {cres.get('error')})",
                             reply_to=msg.message_id)
            return {"ok": False, "reason": "consume_failed"}
        if cres.get("completed"):
            new_state = cres["state"]
            # Summary (replica di _consume_get_inputs_response).
            lines = [f"Dialogo «{state.get('title','?')}» completato."]
            for s in dialog:
                v = s.get("var")
                k = (s.get("schema") or {}).get("kind")
                val = new_state["values_collected"].get(v)
                if k == "credentials" or (s.get("schema") or {}).get("secret"):
                    lines.append(f"  {v}: ********")
                else:
                    lines.append(f"  {v}: {val}")
            self._send_text(msg.sender_id, "\n".join(lines),
                             reply_to=msg.message_id)
            from actor_resolver import resolve_actor as _ra
            actor_for = _ra(self.channel.name, msg.sender_id)
            callback_msg = self._on_get_inputs_completed(
                new_state, actor=actor_for,
            )
            if callback_msg:
                self._send_text(msg.sender_id, callback_msg,
                                 reply_to=msg.message_id)
            _cap_pending_clear(msg.sender_id)
            return {"ok": True, "callback": "dlg_completed"}
        # Prossimo step: prompt + keyboard.
        next_idx = step_idx + 1
        next_step = dialog[next_idx]
        next_prompt = (f"Step {next_idx+1}/{len(dialog)} — "
                        f"{next_step.get('prompt', '?')}")
        # PR5: choice_with_preview → manda album thumb prima della
        # keyboard. Se >10 opzioni o invio thumb fallisce, degrada
        # silenziosamente a keyboard sola con i label.
        next_kind = (next_step.get("schema") or {}).get("kind")
        if (next_kind == "choice_with_preview"
                and self.channel.name == "telegram"):
            try:
                self._send_choice_preview_album(
                    chat_id=msg.sender_id, step=next_step,
                    reply_to=msg.message_id,
                )
            except Exception as ex:
                log.warning("preview album send failed: %s", ex)
        keyboard = self._build_dialog_keyboard(dialog_id, next_idx, next_step)
        self.channel.send(
            recipient=msg.sender_id,
            message=OutboundMessage(text=next_prompt,
                                      reply_to=msg.message_id,
                                      buttons=keyboard),
        )
        return {"ok": True, "callback": "dlg_advance",
                "step_idx": next_idx}

    def _handle_promoter_callback(self, msg: InboundMessage,
                                    data: str) -> dict:
        """Gestisce callback dei bottoni inviati dal `promoter_digest`
        (ADR 0090). Formato `promoter:<proposal_id>:ok|rollback`.

        - `ok`        → mark_acked + reply "Confermato in grace fino a <iso>".
        - `rollback`  → invoca rollback_promotion + reply esito.

        Determinismo §7.9: niente LLM. Sicurezza: callback_data parsing
        strict, errori esposti come reply (mai stacktrace).
        """
        parts = data.split(":", 2)
        if len(parts) != 3:
            return {"ok": False, "reason": "bad_callback_data", "data": data}
        _, proposal_id, action = parts
        # E3 11/5/2026: special-case aggregato → invia link al form HTTP.
        if proposal_id == "_aggregated" and action == "open_form":
            try:
                import os as _os
                base = _os.environ.get(
                    "METNOS_HTTP_BASE_URL", "http://127.0.0.1:8770",
                )
                url = f"{base}/admin/promotions/review"
                self._send_text(
                    msg.sender_id,
                    f"Apri il form review: {url}",
                    reply_to=msg.message_id,
                )
            except Exception as ex:  # noqa: BLE001
                log.warning("aggregated open_form failed: %s", ex)
                return {"ok": False, "reason": "open_form_error",
                        "error": str(ex)}
            return {"ok": True, "callback": "promoter_open_form"}
        if action == "ok":
            try:
                # runtime/ già su sys.path (channels VIVE in runtime/).
                from jobs.promoter_state import (  # noqa: WPS433
                    load_proposal_state, mark_acked,
                )
                state = load_proposal_state(proposal_id) or {}
                mark_acked(proposal_id)
            except Exception as ex:  # noqa: BLE001
                log.warning("promoter ok callback failed: %s", ex)
                self._send_text(msg.sender_id,
                                f"Errore conferma promoter: {ex}",
                                reply_to=msg.message_id)
                return {"ok": False, "reason": "ack_failed",
                        "error": str(ex)}
            grace = state.get("grace_until") or "(grace gia' finalizzata)"
            self._send_text(
                msg.sender_id,
                f"Confermato. In grace fino a {grace}.",
                reply_to=msg.message_id,
            )
            return {"ok": True, "callback": "promoter_ok",
                    "proposal_id": proposal_id}
        if action == "rollback":
            try:
                # runtime/ già su sys.path (channels VIVE in runtime/).
                from jobs.promoter_rollback import (  # noqa: WPS433
                    rollback_promotion,
                )
                result = rollback_promotion(proposal_id)
            except Exception as ex:  # noqa: BLE001
                log.warning("promoter rollback callback failed: %s", ex)
                self._send_text(msg.sender_id,
                                f"Errore rollback: {ex}",
                                reply_to=msg.message_id)
                return {"ok": False, "reason": "rollback_crash",
                        "error": str(ex)}
            if result.get("ok"):
                self._send_text(
                    msg.sender_id,
                    f"Promozione annullata: executor "
                    f"`{result.get('name', '?')}` rimosso.",
                    reply_to=msg.message_id,
                )
            else:
                self._send_text(
                    msg.sender_id,
                    f"Rollback non riuscito: "
                    f"{result.get('error', 'errore sconosciuto')}",
                    reply_to=msg.message_id,
                )
            return {"ok": bool(result.get("ok")),
                    "callback": "promoter_rollback",
                    "proposal_id": proposal_id,
                    "result": result}
        return {"ok": False, "reason": "unknown_action", "action": action}

    def _handle_scheduler_callback(self, msg: InboundMessage,
                                    data: str) -> dict:
        """Gestisce i bottoni della notifica circuit-breaker dello scheduler
        (recurring_tasks._notify_circuit_break). Formato
        `sched:<azione>:<entry_name>` con azione cont|susp|canc.

        - cont  → resume_job: riabilita + azzera streak + ricalcola next_fire.
        - susp  → resta disabilitato (toggle off idempotente). Ripristinabile.
        - canc  → cancella la schedulazione (scheduler entry + record utente).

        entry_name e' il nome scheduler (`user_<task>`); per la pulizia del
        record utente si rimuove il prefisso `user_`. Determinismo §7.9: niente
        LLM, parsing strict, errori esposti come reply (mai stacktrace). Testo
        user-facing via i18n DB (§11): chiavi MSG_SCHED_*."""
        from messages import get as _msg
        parts = data.split(":", 2)
        if len(parts) != 3 or not parts[2]:
            return {"ok": False, "reason": "bad_callback_data", "data": data}
        _, action, entry_name = parts
        try:
            from scheduler_v2 import client as sched_client
        except Exception as ex:  # noqa: BLE001
            self._send_text(msg.sender_id,
                            _msg("MSG_SCHED_UNREACHABLE", error=ex),
                            reply_to=msg.message_id)
            return {"ok": False, "reason": "scheduler_unreachable", "error": str(ex)}

        if action == "cont":
            ok = sched_client.resume_job(entry_name)
            reply = _msg("MSG_SCHED_RESUMED" if ok else "MSG_SCHED_NOT_FOUND")
            return self._sched_cb_reply(msg, ok, reply, "resume", entry_name)
        if action == "susp":
            ok = sched_client.toggle_job(entry_name, False)
            reply = _msg("MSG_SCHED_SUSPENDED" if ok else "MSG_SCHED_NOT_FOUND")
            return self._sched_cb_reply(msg, ok, reply, "suspend", entry_name)
        if action == "canc":
            ok = sched_client.cancel_job(entry_name)
            # Pulisci anche il record recurring_tasks (chiave senza `user_`).
            rec_name = entry_name[len("user_"):] if entry_name.startswith("user_") else entry_name
            try:
                from recurring_tasks import cancel_user_task
                cancel_user_task(rec_name)
            except Exception as ex:  # noqa: BLE001
                log.warning("cancel_user_task('%s') fallita: %s", rec_name, ex)
            reply = _msg("MSG_SCHED_CANCELLED" if ok else "MSG_SCHED_NOT_FOUND")
            return self._sched_cb_reply(msg, ok, reply, "cancel", entry_name)
        return {"ok": False, "reason": "unknown_action", "action": action}

    def _sched_cb_reply(self, msg: InboundMessage, ok: bool, reply: str,
                         action: str, entry_name: str) -> dict:
        self._send_text(msg.sender_id, reply, reply_to=msg.message_id)
        return {"ok": bool(ok), "callback": f"sched_{action}",
                "entry_name": entry_name}

    def _handle_callback(self, msg: InboundMessage) -> dict:
        """Risolve un callback_query 'approve:<token>' / 'reject:<token>' /
        'loc_cancel' / 'dlg:...' (dialog inline keyboard, ADR 0090) /
        'promoter:<id>:ok|rollback' (digest promoter daemon) /
        'sched:<azione>:<entry>' (circuit-breaker scheduler)."""
        data = (msg.text or "").strip()
        if data.startswith("dlg:"):
            return self._handle_dialog_callback(msg, data)
        if data.startswith("promoter:"):
            return self._handle_promoter_callback(msg, data)
        if data.startswith("sched:"):
            return self._handle_scheduler_callback(msg, data)
        if data == "loc_cancel":
            try:
                # runtime/ già su sys.path (channels VIVE in runtime/).
                import location_request as _locreq
                from actor_resolver import resolve_actor as _ra
                from messages import get as _msg
                actor_for = _ra(self.channel.name, msg.sender_id)
                pending = _locreq.get_pending_for(actor_for, self.channel.name)
                if pending:
                    _locreq.cancel(pending["pending_id"])
                self._send_text(msg.sender_id, _msg("MSG_LOCATION_REFUSED"),
                                 reply_to=msg.message_id)
            except Exception as ex:
                log.warning("loc_cancel handling failed: %s", ex)
            return {"ok": True, "callback": "loc_cancel"}
        if data.startswith("approve:"):
            decision = "approved"
            token = data[len("approve:"):]
            user_label = "Approvato"
        elif data.startswith("reject:"):
            decision = "rejected"
            token = data[len("reject:"):]
            user_label = "Rifiutato"
        else:
            log.warning("callback_data non riconosciuto: %r", data)
            return {"ok": False, "reason": "unknown_callback", "data": data}
        if not token:
            return {"ok": False, "reason": "missing_token"}
        try:
            rec = approval_registry.resolve(
                token, decision,
                by_channel=self.channel.name, by_sender=msg.sender_id,
            )
        except approval_registry.ApprovalError as e:
            self._send_text(msg.sender_id,
                            f"Approval non risolvibile: {e}",
                            reply_to=msg.message_id)
            return {"ok": False, "reason": "approval_failed", "error": str(e)}
        log.info("approval risolto: token=%s decision=%s sender=%s",
                 token, decision, msg.sender_id)
        self._send_text(msg.sender_id,
                        f"{user_label}: {rec.action_verb} {rec.target_summary}",
                        reply_to=msg.message_id)
        return {"ok": True, "decision": decision, "token": token,
                "capability_class": rec.capability_class}

    def handle_message(self, msg: InboundMessage) -> dict:
        """Gestisce un singolo messaggio. Ritorna dict di esito (per log/test)."""
        # Callback dei bottoni inline (approve:<tok> / reject:<tok>).
        # Vengono prima del check pairing perche' il sender che clicca e'
        # gia' chi ha generato la richiesta (verificato in approval_registry.resolve).
        if (msg.extra or {}).get("kind") == "callback":
            return self._handle_callback(msg)

        # ── Multi-foto burst aggregation (ADR 0092, 5/5/2026) ──────
        # Telegram invia album come messaggi separati con stesso
        # `media_group_id`. Accumuliamo i path nel buffer; il primo messaggio
        # con caption "porta" la query, gli altri arrivano dopo (di solito).
        # Strategia semplice: appendi al buffer, processa subito SOLO se TTL
        # gia' scaduto su un altro gruppo (sweep), altrimenti lascia in
        # buffer. La sweep avviene a ogni iterazione di run_forever.
        extra = msg.extra or {}
        group_id = extra.get("media_group_id")
        if group_id and extra.get("attached_images"):
            buf = _media_group_load(msg.sender_id, group_id) or {
                "paths": [], "caption": "", "first_msg_id": None,
                "last_seen": 0.0,
            }
            for p in extra.get("attached_images") or []:
                if p and p not in buf["paths"]:
                    buf["paths"].append(p)
            # Caption: prendi la prima non-vuota (di solito sul primo del gruppo).
            if msg.text and not buf.get("caption"):
                buf["caption"] = msg.text
            if buf.get("first_msg_id") is None:
                buf["first_msg_id"] = msg.message_id
            buf["last_seen"] = time.time()
            buf["sender_id"] = msg.sender_id
            _media_group_save(msg.sender_id, group_id, buf)
            log.debug("media_group %s/%s: %d foto buffered",
                      msg.sender_id, group_id, len(buf["paths"]))
            return {"ok": True, "media_group_buffered": group_id,
                    "n_paths": len(buf["paths"])}
        # Comandi di pairing accettati anche da non-pairati: sono la loro ragione d'essere.
        if msg.text.startswith(PAIR_COMMAND):
            return self._handle_pair_command(msg)
        if msg.text.startswith(START_COMMAND):
            # /start <token>: pairing multi-user (ADR 0083). Si arriva qui anche
            # da chat_id non noti: e' il punto d'ingresso del flusso.
            res = self._handle_start_command(msg)
            # Dopo /start ok, sincronizza pairings.db cosi' i turni successivi
            # del nuovo user passano i check classici (autonomy + actor).
            if res.get("ok"):
                try:
                    role = res.get("role", "guest")
                    autonomy = "Full" if role == "host" else "Supervised"
                    actor = res.get("name") or "guest"
                    conn = pairing._open_db()
                    try:
                        with conn:
                            now = pairing._now_iso()
                            conn.execute(
                                "INSERT INTO pairings (channel, sender_id, autonomy_level, "
                                "paired_at, paired_by, actor, display_name) "
                                "VALUES (?,?,?,?,?,?,?) "
                                "ON CONFLICT(channel, sender_id) DO UPDATE SET "
                                "autonomy_level=excluded.autonomy_level, "
                                "paired_at=excluded.paired_at, "
                                "paired_by=excluded.paired_by, "
                                "actor=excluded.actor, "
                                "display_name=COALESCE(excluded.display_name, display_name), "
                                "revoked_at=NULL",
                                (self.channel.name, msg.sender_id, autonomy,
                                 now, "users_pair", actor, actor),
                            )
                    finally:
                        conn.close()
                except Exception as ex:
                    log.warning("post-start sync to pairings.db failed: %s", ex)
            return res

        # Riconoscimento sender
        existing = pairing.get_pairing(self.channel.name, msg.sender_id)
        if existing is None:
            existing = self._try_bootstrap(msg)
        if existing is None:
            # Multi-user fallback (ADR 0083): se il chat_id e' gia' bindato a
            # uno user verificato in users.db (paired via /start), accettalo
            # senza richiedere il bootstrap classico.
            try:
                # runtime/ già su sys.path (channels VIVE in runtime/).
                import users as _users
                u = _users.find_user_by_recipient(
                    self.channel.name, msg.sender_id,
                )
            except Exception as ex:
                log.debug("users.find_user_by_recipient failed: %s", ex)
                u = None
            if u is not None:
                # Crea on-the-fly un pairing in pairings.db con autonomy
                # mappato da role: host=Full, guest=Supervised.
                try:
                    autonomy = "Full" if u.get("role") == "host" else "Supervised"
                    actor = u.get("name") or "guest"
                    conn = pairing._open_db()
                    try:
                        with conn:
                            now = pairing._now_iso()
                            conn.execute(
                                "INSERT INTO pairings (channel, sender_id, autonomy_level, "
                                "paired_at, paired_by, actor, display_name) "
                                "VALUES (?,?,?,?,?,?,?) "
                                "ON CONFLICT(channel, sender_id) DO UPDATE SET "
                                "autonomy_level=excluded.autonomy_level, "
                                "actor=excluded.actor, "
                                "revoked_at=NULL",
                                (self.channel.name, msg.sender_id, autonomy,
                                 now, "users_db", actor, actor),
                            )
                    finally:
                        conn.close()
                    existing = pairing.get_pairing(self.channel.name, msg.sender_id)
                except Exception as ex:
                    log.warning("on-the-fly pair from users.db failed: %s", ex)
        if existing is None:
            log.warning("sender non pairato: %s/%s", self.channel.name, msg.sender_id)
            self._send_text(msg.sender_id, UNPAIRED_REPLY, reply_to=msg.message_id)
            return {"ok": False, "reason": "sender_not_paired", "sender": msg.sender_id}

        # Touch last_seen per audit/observability
        pairing.touch_last_seen(self.channel.name, msg.sender_id)

        # Livello ReadOnly: nessuna azione, risposta cortese
        if existing.autonomy_level in LEVEL_BLOCKS_RUN:
            self._send_text(msg.sender_id,
                            LEVEL_REPLY_BLOCKED.format(level=existing.autonomy_level),
                            reply_to=msg.message_id)
            return {"ok": False, "reason": "autonomy_too_low",
                    "level": existing.autonomy_level}

        log.info("turno: sender=%s level=%s text=%r",
                 msg.sender_id, existing.autonomy_level, msg.text[:80])
        # Costruisci progress channel-specifico per UX su operazioni lunghe
        # (synt multistage, ~150 s). Su Telegram: bar Unicode con editMessageText
        # + sendChatAction("typing") in loop. Altri canali: NullProgress finche'
        # non hanno adapter dedicato.
        progress = NullProgress()
        if self.channel.name == "telegram":
            try:
                progress = TelegramProgress(self.channel, msg.sender_id)
            except Exception as ex:
                log.warning("TelegramProgress init fallita: %s", ex)
        # LOCATION REQUEST fase 2 (regola PLANNER §2-quater): se nel turno
        # precedente abbiamo emesso request_location_from_user, c'e' un
        # pending state in attesa della risposta utente. Quattro path:
        #   (a) location share (📎)         → resolve + ricostruisci query originale + rilancia
        #   (b) testo "annulla" / "cancel"  → cancel + reply MSG_LOCATION_REFUSED + skip
        #   (c) testo libero (indirizzo/CAP/citta') → forward_geocode → resolve + rilancia
        #       se geocode fallisce → reply MSG_LOCATION_GEOCODE_FAIL + lascia pending
        #   (d) altro testo                 → forward_geocode best-effort (path c)
        # Multilingue: keyword cancel da messages.py (IT+EN). Forward geocode
        # via nominatim_client (gia' usato da find_places).
        text_for_run = msg.text
        # Multi-user (1/5/2026): risolvi actor logico dal pairing.
        # Default "host" (MVP single-user, fallback in actor_resolver).
        try:
            # runtime/ già su sys.path (channels VIVE in runtime/).
            from actor_resolver import resolve_actor as _resolve_actor
            actor_for_pending = _resolve_actor(self.channel.name, msg.sender_id)
        except Exception as ex:
            log.warning("actor_resolver fallito, fallback host: %s", ex)
            actor_for_pending = "host"
        # Channel key: stesso formato che l'handler di request_location_from_user
        # usa quando salva il pending (channel=run_turn.channel param, oggi
        # passato come self.channel.name dal daemon). NON includere sender_id:
        # multi-utente discriminato da actor, non da channel sub-key.
        loc_channel_key = self.channel.name
        try:
            # runtime/ già su sys.path (channels VIVE in runtime/).
            import location_request as _locreq
            from messages import get as _msg
            _loc_pending = _locreq.get_pending_for(actor_for_pending, loc_channel_key)
        except Exception as ex:
            log.warning("location_request lookup failed: %s", ex)
            _loc_pending = None
        if _loc_pending:
            extra = msg.extra or {}
            if extra.get("kind") == "location_share":
                # (a) Share via 📎 → resolve, rilancia query originale
                resolved = _locreq.resolve(
                    _loc_pending["pending_id"],
                    lat=extra["lat"], lon=extra["lon"],
                    source="telegram_share", accuracy=extra.get("accuracy"),
                )
                if resolved.get("status") == "resolved":
                    if self.channel.name == "telegram":
                        try:
                            self.channel.clear_keyboard(
                                chat_id=msg.sender_id,
                                text=_msg("MSG_LOCATION_RESOLVED"),
                            )
                        except Exception as ex:
                            log.warning("clear_keyboard failed: %s", ex)
                    text_for_run = _loc_pending["original_query"]
                    log.info("location_pending: share resolved -> rilancio %r", text_for_run[:80])
            else:
                # (b)/(c)/(d) testo: classifica annulla vs free-text-geocode
                txt = (msg.text or "").strip()
                cancel_kws = set(_msg("MSG_LOCATION_CANCEL_KEYWORDS").split("|"))
                if txt.lower() in cancel_kws:
                    _locreq.cancel(_loc_pending["pending_id"])
                    if self.channel.name == "telegram":
                        try:
                            self.channel.clear_keyboard(
                                chat_id=msg.sender_id,
                                text=_msg("MSG_LOCATION_REFUSED"),
                            )
                        except Exception as ex:
                            log.warning("clear_keyboard failed: %s", ex)
                    return {"ok": True, "location_pending": "cancelled"}
                # Tentativo forward geocode
                geo = _locreq.try_geocode_text(txt)
                if geo:
                    resolved = _locreq.resolve(
                        _loc_pending["pending_id"],
                        lat=geo["lat"], lon=geo["lon"],
                        source=geo["source"],
                    )
                    if resolved.get("status") == "resolved":
                        if self.channel.name == "telegram":
                            try:
                                self.channel.clear_keyboard(
                                    chat_id=msg.sender_id,
                                    text=_msg("MSG_LOCATION_RESOLVED"),
                                )
                            except Exception as ex:
                                log.warning("clear_keyboard failed: %s", ex)
                        text_for_run = _loc_pending["original_query"]
                        log.info("location_pending: geocode '%s' resolved -> rilancio %r",
                                  txt[:40], text_for_run[:80])
                else:
                    # geocode fail: lascia pending, chiedi di riprovare
                    self._send_text(msg.sender_id,
                                     _msg("MSG_LOCATION_GEOCODE_FAIL", text=txt[:80]),
                                     reply_to=msg.message_id)
                    return {"ok": False, "reason": "geocode_failed", "text": txt[:80]}
        elif (msg.extra or {}).get("kind") == "location_share":
            # location share senza pending dialog: solo aggiornamento background
            # (gia' registrato in poll() via record_location). Niente turno.
            return {"ok": True, "location_share": "background_update"}

        # CAP EXPAND fase 2 (the design guide 2.11): se nel turno precedente abbiamo
        # registrato una proposta di cap expand per questo sender, e l'utente
        # risponde "sì"/"yes" → ricostruisci la query originale forzando il
        # nuovo cap e rilancia. "No" / qualunque altro → cancella stato e
        # procedi normale con la nuova query.
        pending = _cap_pending_load(msg.sender_id)
        if pending:
            p = pending["proposal"]
            # Caso speciale get_inputs (ADR 0090): dialogo strutturato
            # multi-step. Il messaggio utente e' una risposta allo step
            # corrente. Parsing+validation+advance state. Se completato,
            # eventuali side-effect (es. salvare credenziali) e ri-esecuzione
            # della query originale; altrimenti, prossima domanda.
            if p.get("kind") == "get_inputs_response":
                reply, retry_original, summary = self._consume_get_inputs_response(
                    p, msg.text, actor=actor_for_pending,
                    sender_id=msg.sender_id,
                )
                if reply is not None:
                    self._send_text(msg.sender_id, reply,
                                     reply_to=getattr(msg, "message_id", None))
                if retry_original:
                    # Dialogo completato. Carica state per applicare il
                    # callback `on_complete` (ADR 0091): tipicamente
                    # save_credentials_and_resume → ri-invoca admin con
                    # i suoi args originali. NON rilanciamo piu' la query
                    # originale dell'utente: il callback FA gia' la chiamata
                    # diretta al verb e ci ritorna il summary.
                    try:
                        # runtime/ già su sys.path (channels VIVE in runtime/).
                        import dialog_pending as _dp
                        _state = _dp.load_pending(
                            p.get("sender_for_state") or msg.sender_id,
                            p.get("dialog_id") or "",
                        ) or {}
                    except Exception as ex:
                        log.warning("dialog_pending lookup failed: %s", ex)
                        _state = {}
                    callback_msg = self._on_get_inputs_completed(
                        _state, actor=actor_for_pending,
                    )
                    if summary:
                        self._send_text(msg.sender_id, summary,
                                         reply_to=getattr(msg, "message_id", None))
                    if callback_msg:
                        # Output del callback (es. carta vaglio admin con
                        # signature mount.cifs, esito execute, ecc.)
                        self._send_text(msg.sender_id, callback_msg,
                                         reply_to=getattr(msg, "message_id", None))
                    _cap_pending_clear(msg.sender_id)
                    log.info("get_inputs: dialogo completato, callback applicato")
                    return {"ok": True, "get_inputs_completed": True}
                else:
                    # Caso passthrough (6/5/2026): consumer ha tornato
                    # (None, False, None) e ha gia' cancellato il dialog
                    # → l'utente ha scritto una query nuova invece di
                    # sì/no. Cancella cap_pending e LASCIA PROSEGUIRE il
                    # flow normale (run_turn sotto) anziche' return early.
                    if reply is None and summary is None:
                        _cap_pending_clear(msg.sender_id)
                        log.info("get_inputs: passthrough (long input), cancellato dialog "
                                  "+ procedo come query nuova")
                        # FALL THROUGH al run_turn standard
                    else:
                        if reply is None:
                            # cancel/error path: stato gia' pulito da consume
                            _cap_pending_clear(msg.sender_id)
                        return {"ok": True, "get_inputs_pending": True,
                                "completed": False}
            # ADR 0091 (5/5/2026): rimosso il branch `kind="credentials_required"`.
            # 6/5/2026: rimosso il branch generico cap_expand (rewrite
            # "(forza X=Y su Z)" che il PLANNER medium leggeva come saluto).
            # Cap-expand ora sintetizza un get_inputs upstream (agent_runtime
            # _orchestrate_cap_expand_dialog) e arriva qui come
            # `kind="get_inputs_response"`, gestito sopra in modo uniforme.
            elif p.get("kind") == "admin_approval":
                ans = _classify_yes_no(msg.text)
                if ans == "yes":
                    # ADR 0088: NON rilanciare via PLANNER. Invoca direttamente
                    # admin con il consent_token firmato, sudoer esegue,
                    # restituisci summary all'utente.
                    _cap_pending_clear(msg.sender_id)
                    answer = self._consume_admin_approval(p, actor=actor_for_pending)
                    self._send_text(msg.sender_id, answer,
                                     reply_to=getattr(msg, "message_id", None))
                    return {"ok": True, "admin_approval_yes": True}
                _cap_pending_clear(msg.sender_id)
                if ans == "no":
                    log.info("cap_pending: admin_approval no -> stato pulito")
            elif p.get("kind") == "approval_required":
                # find_images_indices build approval: pattern bespoke
                # superstite (non migrato a get_inputs in questo sprint).
                # Direct invoke con args_suggested al "sì" (replica HTTP).
                ans = _classify_yes_no(msg.text)
                if ans == "yes":
                    _cap_pending_clear(msg.sender_id)
                    answer = self._consume_approval_required(p, actor=actor_for_pending)
                    if answer:
                        self._send_text(msg.sender_id, answer,
                                         reply_to=getattr(msg, "message_id", None))
                    return {"ok": True, "approval_required_yes": True}
                _cap_pending_clear(msg.sender_id)
                if ans == "no":
                    log.info("cap_pending: approval_required no -> stato pulito")
            else:
                # Kind sconosciuto: scarta lo stato e procedi normalmente.
                log.warning("cap_pending: kind sconosciuto %r -> scarto",
                             p.get("kind"))
                _cap_pending_clear(msg.sender_id)
        try:
            run_turn = self._resolve_run_turn()
            # Reference images ADR 0092: foto allegate al turno (caption +
            # photo Telegram). Passa al run_turn per inietto step 0 virtuale.
            ref_imgs = list((msg.extra or {}).get("attached_images") or [])
            ref_imgs = [p for p in ref_imgs if p]
            # Notice §2.8 se download Telegram fallito: niente silent failure.
            if (msg.extra or {}).get("attached_failed"):
                self._send_text(msg.sender_id,
                                 "(non sono riuscito a scaricare la foto allegata)",
                                 reply_to=msg.message_id)
            # Propaga actor (multi-user 1/5/2026) e channel a run_turn cosi' che
            # tool atomici (get_location, undo_last_turn, request_location_from_user,
            # ecc.) operino sull'actor giusto invece di hardcodare "host".
            turn = run_turn(text_for_run, progress=progress,
                             actor=actor_for_pending,
                             channel=self.channel.name,
                             reference_images=ref_imgs or None)
            answer = _format_turn_result(turn)
            # Salva pending se il turno ha proposto cap expand.
            if getattr(turn, "expandable_caps", None):
                _cap_pending_save(msg.sender_id, msg.text, turn.expandable_caps[0],
                                  turn.turn_id)
        except Exception as e:
            log.exception("run_turn fallito")
            answer = f"(errore interno: {type(e).__name__}: {e})"
            turn = None
        # LOCATION REQUEST fase 1 (regola PLANNER §2-quater): se il turno ha
        # emesso request_location_from_user, NON mandiamo answer (e' vuoto);
        # invece renderizziamo il prompt UI coi bottoni via channel adapter.
        if turn is not None and getattr(turn, "pending_location", None):
            pl = turn.pending_location
            chat_id_for = pl.get("chat_id") or msg.sender_id
            try:
                if self.channel.name == "telegram":
                    self.channel.prompt_location_share(
                        chat_id=chat_id_for,
                        goal=pl.get("goal", "rispondere alla tua richiesta"),
                    )
            except Exception as ex:
                log.warning("prompt_location_share failed: %s", ex)
            return {"ok": True, "pending_location": pl.get("pending_id"),
                    "level": existing.autonomy_level}
        if self.dry_run:
            log.info("dry-run: avrei risposto %r", answer[:120])
            return {"ok": True, "dry_run": True, "answer_preview": answer[:200],
                    "level": existing.autonomy_level}
        # Telegram media group (Opzione 1, 5/5/2026): se il turno ha
        # prodotto attachments (es. find_images_indices), manda album(s)
        # sendMediaGroup coi thumb (Telegram max 10 per album → split in
        # chunks). Best-effort: se uno fallisce, log e si continua.
        atts = list(getattr(turn, "attachments", []) or []) if turn is not None else []
        if atts and self.channel.name == "telegram":
            CHUNK = 10
            n_total = len(atts)
            for i in range(0, n_total, CHUNK):
                chunk = atts[i:i+CHUNK]
                start, end = i + 1, i + len(chunk)
                caption = f"Foto {start}-{end} di {n_total} per la tua query"
                try:
                    mg = self.channel.send_media_group(
                        chat_id=msg.sender_id,
                        attachments=chunk,
                        turn_id=turn.turn_id,
                        caption_first=caption,
                    )
                    if not mg.get("ok"):
                        log.warning("send_media_group chunk %d failed: %s",
                                    i // CHUNK, mg.get("error"))
                except Exception as ex:
                    log.warning("send_media_group chunk %d raised: %s",
                                i // CHUNK, ex)
            # Link gallery HTTP per browse completo (top_k full + score badge).
            # LAN-only (192.0.2.10:8770), accessibile via VPN/Tailscale.
            try:
                import os as _os
                host = _os.environ.get("METNOS_PUBLIC_HOST", "192.0.2.10")
                port = _os.environ.get("METNOS_HTTP_PORT", "8770")
                gallery_url = f"http://{host}:{port}/agent/gallery/{turn.turn_id}"
                # Append link in coda al final answer testuale
                answer = (answer or "").rstrip()
                answer += f"\n\n📷 Gallery completa: {gallery_url}"
            except Exception as ex:
                log.debug("gallery url append failed: %s", ex)
        # Catena di consegna del final answer all'utente, ROBUSTA per costruzione:
        # 1) progress.finish (edita il progress message col final).
        # 2) channel.send (manda un nuovo messaggio formattato).
        # 3) last-ditch: manda raw plain text via canale (no formattazione).
        # Lo step 3 e' la garanzia anti-silent-failure (the design guide 2.8): l'utente
        # deve SEMPRE ricevere qualcosa o vedere un log ERROR esplicito,
        # mai trovare il bot che si ammutolisce a meta' turno.
        progress_msg_id = getattr(progress, "message_id", None)
        send_result: dict = {"ok": False, "error": "no send attempted"}
        if isinstance(progress, TelegramProgress) and progress_msg_id is not None:
            try:
                progress.finish(answer)
                send_result = {"ok": True, "via": "progress.finish"}
            except Exception as ex:
                log.warning("progress.finish failed, falling back to channel.send: %s", ex)
        if not send_result.get("ok"):
            try:
                # Inline keyboard se il turno apre un dialog get_inputs
                # con fmt='telegram_inline' (yes_no/choice). Costruiamo
                # la keyboard del primo step (idx=0) leggendo lo state
                # da dialog_pending. Per altri fmt la keyboard resta None.
                first_step_buttons = None
                if (self.channel.name == "telegram"
                        and turn is not None
                        and getattr(turn, "expandable_caps", None)):
                    p0 = turn.expandable_caps[0] or {}
                    if (p0.get("kind") == "get_inputs_response"
                            and p0.get("fmt") == "telegram_inline"):
                        try:
                            # runtime/ già su sys.path (channels VIVE in runtime/).
                            import dialog_pending as _dp2
                            sender_for_state = (
                                p0.get("sender_for_state")
                                or f"{self.channel.name}:{msg.sender_id}"
                            )
                            st0 = (_dp2.load_pending(sender_for_state,
                                                       p0.get("dialog_id") or "")
                                    or _dp2.load_pending(msg.sender_id,
                                                            p0.get("dialog_id") or ""))
                            if st0 and st0.get("dialog"):
                                first_step = st0["dialog"][0]
                                first_kind = (first_step.get("schema")
                                              or {}).get("kind")
                                # PR5: choice_with_preview → manda album
                                # thumb prima della keyboard. Best-effort:
                                # se >10 opzioni o thumb fail, degrada a
                                # keyboard sola con label.
                                if first_kind == "choice_with_preview":
                                    try:
                                        self._send_choice_preview_album(
                                            chat_id=msg.sender_id,
                                            step=first_step,
                                            reply_to=msg.message_id,
                                        )
                                    except Exception as ex:
                                        log.warning(
                                            "preview album send failed: %s", ex)
                                first_step_buttons = self._build_dialog_keyboard(
                                    p0.get("dialog_id") or "",
                                    0, first_step,
                                )
                        except Exception as ex:
                            log.warning("inline keyboard build failed: %s", ex)
                send_result = self.channel.send(
                    recipient=msg.sender_id,
                    message=OutboundMessage(
                        text=answer, reply_to=msg.message_id,
                        buttons=first_step_buttons,
                    ),
                )
            except Exception as ex:
                log.warning("channel.send failed, last-ditch plain send: %s", ex)
                send_result = {"ok": False, "error": f"channel.send raised: {ex}"}
        if not send_result.get("ok"):
            # Ultimo tentativo: testo plain, niente reply_to, niente parse_mode.
            # Anche se il messaggio e' brutto graficamente, almeno arriva.
            try:
                from html_sanitizer import to_plain_text
                plain = to_plain_text(answer)[:4000]
                last = self._send_text(msg.sender_id, plain)
                if last.get("ok"):
                    send_result = {"ok": True, "via": "last_ditch_plain"}
                else:
                    log.error("LAST-DITCH SEND FAILED: %s | answer-preview=%r",
                              last, (answer or "")[:200])
                    send_result = {"ok": False, "error": "all send paths failed",
                                   "last": last}
            except Exception as ex:
                log.error("LAST-DITCH SEND EXCEPTION: %s | answer-preview=%r",
                          ex, (answer or "")[:200])
                send_result = {"ok": False, "error": f"last-ditch raised: {ex}"}
        return {"ok": send_result.get("ok", False), "answer": answer,
                "send": send_result, "level": existing.autonomy_level}

    def run_forever(self, *, max_iterations: int | None = None) -> int:
        """Loop principale. `max_iterations` per i test; None = davvero infinito."""
        log.info("daemon avviato: channel=%s dry_run=%s bootstrap=%s",
                 self.channel.name, self.dry_run, self.bootstrap_default_sender)
        i = 0
        while not self._stop:
            if max_iterations is not None and i >= max_iterations:
                break
            i += 1
            try:
                messages = self.channel.poll()
            except Exception:
                log.exception("poll fallito (riprovo fra 5s)")
                time.sleep(5)
                continue
            for m in messages:
                self.handle_message(m)
            # Sweep media_group buffer: gruppi con last_seen + TTL < now sono
            # pronti per il run_turn. Il "carrier" della query e' il primo
            # messaggio (caption / first_msg_id), gli attached_images
            # aggregano tutti i path del gruppo (ADR 0092).
            try:
                ready = _media_group_sweep_expired()
            except Exception:
                log.exception("media_group sweep fallita")
                ready = []
            for sender_id, group_id, data in ready:
                paths = data.get("paths") or []
                if not paths:
                    continue
                synthetic = InboundMessage(
                    channel=self.channel.name,
                    sender_id=sender_id,
                    text=data.get("caption") or "",
                    message_id=data.get("first_msg_id") or "",
                    received_at=float(data.get("last_seen") or time.time()),
                    extra={
                        "attached_images": paths,
                        "media_group_id": None,  # gia' aggregato
                        "kind": "media_group_aggregated",
                    },
                )
                try:
                    self.handle_message(synthetic)
                except Exception:
                    log.exception("media_group flush handle_message failed")
            # Cleanup tmp uploads vecchi (TTL 1h) ogni N=15 iterazioni.
            if i % 15 == 0:
                try:
                    # runtime/ già su sys.path (channels VIVE in runtime/).
                    from upload_cleanup import sweep_old_uploads
                    sweep_old_uploads()
                except Exception:
                    log.debug("upload_cleanup sweep skipped", exc_info=True)
        log.info("daemon terminato dopo %d iterazioni", i)
        return i


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Metnos channel daemon")
    ap.add_argument("--channel", default="telegram", choices=["telegram"])
    ap.add_argument("--dry-run", action="store_true", help="logga ma non risponde")
    ap.add_argument("--no-bootstrap", action="store_true",
                    help="disabilita auto-pair del default_chat_id alla prima interazione")
    ap.add_argument("--no-agent-server", action="store_true",
                    help="disabilita il server HTTP per executor remoti")
    ap.add_argument("--agent-host", default=agent_server.DEFAULT_HOST,
                    help="host bind del server HTTP (default 127.0.0.1)")
    ap.add_argument("--agent-port", type=int, default=agent_server.DEFAULT_PORT,
                    help="porta del server HTTP (default 8765)")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.channel == "telegram":
        ch = TelegramChannel()
    else:
        raise SystemExit(f"channel non supportato: {args.channel}")

    # Single-instance gate per il processo combinato Telegram+HTTP.
    lock = agent_server.ProcessLock(DAEMON_LOCKFILE, owner="metnos-daemon")
    lock.acquire()

    # Server HTTP per executor remoti, in thread daemon, auto-resume su crash.
    if not args.no_agent_server:
        srv = agent_server.AgentServerThread(
            host=args.agent_host, port=args.agent_port,
        )
        srv.start()
        log.info("agent_server thread avviato su %s:%d", args.agent_host, args.agent_port)
    else:
        srv = None

    d = ChannelDaemon(ch, dry_run=args.dry_run,
                      bootstrap_default_sender=not args.no_bootstrap)

    # SIGTERM da systemd -> stop pulito; SIGINT da terminale -> idem.
    def _handle_signal(signum, _frame):
        log.info("ricevuto segnale %s, fermo il daemon", signum)
        d.stop()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        d.run_forever()
    except KeyboardInterrupt:
        log.info("interrotto da tastiera")
    finally:
        if srv is not None:
            try:
                srv.stop()
            except Exception:
                log.exception("errore nello stop del agent_server")
        try:
            lock.release()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
