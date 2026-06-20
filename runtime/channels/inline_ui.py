"""runtime.channels.inline_ui — render Telegram dei pending interattivi.

Sottoinsieme Telegram di `get_inputs` (ADR 0090) + bottoni per i flussi di
autorizzazione cap-pending (`admin_approval`, `approval_required`). Funzioni
PURE (niente chiamate Bot API): il chiamante (ChannelDaemon, recurring_tasks,
TelegramProgress) allega le rows come `reply_markup`/`OutboundMessage.buttons`.

Mappa campo-form → elemento Telegram:
  - yes_no               → 1 row [Sì | No]
  - choice               → 1 row per alternativa (label, callback `c<idx>`)
  - choice_with_preview  → come choice; l'album thumb resta a carico del
                           daemon (richiede canale; qui solo keyboard)
  - admin_approval /
    approval_required    → 1 row [Approva | Rifiuta] (`cap:<turn_id>:yes|no`)
  - text / credentials / number / date / multi_choice / location →
    NON rappresentabili come bottoni → il dialogo resta `dialogue`
    (degrado onesto §2.8: lista numerata + risposta testuale).

callback_data (limite Telegram 64 byte):
  - `dlg:<dialog_id>:<step_idx>:<value>` — risolto da
    ChannelDaemon._handle_dialog_callback via dialog_pending (self-contained:
    funziona anche per dialoghi aperti da query SCHEDULATE, dove non c'e'
    stato in-process).
  - `cap:<turn_id>:yes|no` — risolto da ChannelDaemon._handle_cap_callback
    via cap_pending; il turn_id lega il bottone alla proposta corrente
    (tap su messaggio vecchio → refusal onesto, mai esecuzione su stato
    sbagliato).

Deterministico §7.9: zero LLM. i18n via messages/i18n DB (§11).
"""
from __future__ import annotations

# Kind renderizzabili come InlineKeyboardButton. `multi_choice` escluso
# (richiederebbe toggle ✓ con editMessageReplyMarkup + bottone Conferma:
# scope 2× — si rilascia su use case reale). Caso misto (es. yes_no + text)
# → False: il text richiede comunque sequenza dialogue.
INLINE_COMPATIBLE_KINDS = frozenset({"yes_no", "choice", "choice_with_preview"})

# Cap alternative per step: oltre, la keyboard diventa inutilizzabile su
# mobile e ci si avvicina al limite Telegram (100 bottoni/messaggio) →
# degrado onesto a dialogue (lista numerata).
INLINE_MAX_CHOICES = 24

# Telegram tronca visivamente i testi bottone lunghi: cap esplicito.
_BTN_TEXT_MAX = 64

_I18N_KEYS_ENSURED = False


def ensure_i18n_keys() -> None:
    """Registra (se assenti) le chiavi i18n usate dai bottoni di
    autorizzazione. Idempotente, lazy (una volta per processo)."""
    global _I18N_KEYS_ENSURED
    if _I18N_KEYS_ENSURED:
        return
    try:
        from i18n import register_key_if_missing as _rk
        _rk("MSG_BTN_APPROVE", "Approva", "Approve",
            needs_translation=False)
        _rk("MSG_BTN_REJECT", "Rifiuta", "Reject",
            needs_translation=False)
        _rk("MSG_CAP_PROPOSAL_EXPIRED",
            "(Proposta scaduta o già gestita: nessuna azione eseguita. "
            "Riformula la richiesta se serve.)",
            "(Proposal expired or already handled: no action taken. "
            "Rephrase your request if needed.)",
            needs_translation=False)
        _rk("MSG_CAP_PROPOSAL_DECLINED",
            "Ok, non procedo.", "Ok, not proceeding.",
            needs_translation=False)
        _I18N_KEYS_ENSURED = True
    except Exception:  # i18n DB non disponibile: messages.get fa fallback
        pass


def all_inline_compatible(dialog: list | None) -> bool:
    """True se TUTTI gli step del dialog si rendono come inline keyboard
    Telegram (kind compatibile E numero alternative entro il cap)."""
    for s in (dialog or []):
        schema = (s.get("schema") or {}) if isinstance(s, dict) else {}
        kind = schema.get("kind")
        if kind not in INLINE_COMPATIBLE_KINDS:
            return False
        if kind == "choice":
            n = len(schema.get("choices") or [])
        elif kind == "choice_with_preview":
            n = len(schema.get("options") or [])
        else:
            n = 0
        if n > INLINE_MAX_CHOICES:
            return False
    return True


def _choice_label(c) -> str:
    """Label user-facing di una choice (stringa esplicita oppure dict
    {label, value} derivato da entries, ADR 0127)."""
    if isinstance(c, dict):
        return str(c.get("label") or c.get("value") or "?")
    return str(c)


def build_dialog_keyboard(dialog_id: str, step_idx: int,
                          step: dict) -> list[list[dict]]:
    """Inline keyboard per uno step yes_no/choice/choice_with_preview.

    Formato `buttons` = list di rows, ogni row list di dict {text, data}.
    callback_data = `dlg:<dialog_id>:<step_idx>:<value>` + riga finale
    `dlg:<dialog_id>:cancel`. Per choice/preview il value e' l'INDICE
    (`c<idx>`) — i label possono superare i 64 byte di callback_data;
    la risoluzione indice→value avviene al click via stato persistito.
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
        for i, c in enumerate(choices):
            rows.append([
                {"text": _choice_label(c)[:_BTN_TEXT_MAX],
                 "data": f"{prefix}:c{i}"},
            ])
    elif kind == "choice_with_preview":
        # PR5: i thumb viaggiano come media group separato (lato daemon).
        # La keyboard mostra solo i label. callback `c<idx>` come choice.
        options = (step.get("schema") or {}).get("options") or []
        for i, opt in enumerate(options):
            rows.append([
                {"text": str(opt.get("label", opt.get("value", f"#{i+1}")))[:_BTN_TEXT_MAX],
                 "data": f"{prefix}:c{i}"},
            ])
    rows.append([
        {"text": _msg("MSG_BTN_CANCEL"), "data": f"dlg:{dialog_id}:cancel"},
    ])
    return rows


def build_approval_keyboard(turn_id: str) -> list[list[dict]]:
    """Inline keyboard [Approva | Rifiuta] per le proposte cap-pending di
    autorizzazione (`admin_approval`, `approval_required`).

    callback_data `cap:<turn_id>:yes|no` (24 byte, entro il limite 64).
    La risposta TESTUALE sì/no resta valida in parallelo (stesso
    cap_pending consumato dal daemon)."""
    ensure_i18n_keys()
    from messages import get as _msg
    return [[
        {"text": _msg("MSG_BTN_APPROVE"), "data": f"cap:{turn_id}:yes"},
        {"text": _msg("MSG_BTN_REJECT"),  "data": f"cap:{turn_id}:no"},
    ]]


def sender_state_candidates(channel_name: str, chat_id: str, *,
                            actor: str | None = None,
                            sender_for_state: str | None = None) -> list[str]:
    """Chiavi candidate (ordinate, dedup) per il lookup dello stato
    dialog_pending di un sender Telegram.

    I dialoghi vengono salvati con chiavi diverse a seconda dell'origine:
      - orchestrati (runtime):    `<channel>:<actor>` (es. telegram:host),
                                  esplicitato in `sender_for_state`;
      - executor get_inputs:      `<channel>:<actor>` via METNOS_ACTOR;
      - legacy/test:              `<channel>:<chat_id>` o `<chat_id>`.
    Stessa convenzione multi-candidato di http_routes_agent (§7.9).
    """
    cands: list[str] = []
    if sender_for_state:
        cands.append(str(sender_for_state))
    if channel_name and chat_id:
        cands.append(f"{channel_name}:{chat_id}")
    if chat_id:
        cands.append(str(chat_id))
    if actor:
        if channel_name:
            cands.append(f"{channel_name}:{actor}")
        cands.append(str(actor))
    out: list[str] = []
    for c in cands:
        if c and c not in out:
            out.append(c)
    return out


def load_pending_state(dialog_id: str,
                       sender_candidates: list[str]) -> tuple[dict | None, str | None]:
    """Carica lo stato dialog_pending provando i candidati in ordine.
    Ritorna (state, chiave_che_ha_risolto) oppure (None, None)."""
    if not dialog_id:
        return None, None
    import dialog_pending as _dp
    for cand in sender_candidates:
        st = _dp.load_pending(cand, dialog_id)
        if st is not None:
            return st, cand
    # Fallback GLOBALE (20/6): il `dialog_id` (uuid) e' unico → se nessun
    # candidato-sender lo trova (query schedulata salvata sotto un sender logico
    # che il tap non ricostruisce dal chat_id, e i bridge a TTL sono scaduti),
    # scandisci tutte le sender-dir. Evita il falso «dialogo scaduto» quando il
    # dialogo e' ancora valido. Determinismo §7.9.
    st, sender = _dp.find_by_dialog_id(dialog_id)
    if st is not None:
        return st, sender
    return None, None


def keyboard_for_proposal(p0: dict, *, sender_candidates: list[str],
                          turn_id: str | None = None,
                          ) -> tuple[list[list[dict]] | None, dict | None]:
    """Keyboard per la PRIMA proposta pendente di un turno.

    Ritorna `(buttons, preview_step)`:
      - get_inputs_response + fmt=telegram_inline → keyboard dello step
        corrente; `preview_step` = step se kind=choice_with_preview (il
        caller che HA il canale manda l'album thumb prima del messaggio).
      - admin_approval / approval_required → keyboard Approva/Rifiuta
        (richiede `turn_id` per il binding anti-stale).
      - altro (fmt dialogue/form, kind cap_expand, ...) → (None, None):
        il messaggio resta testuale (degrado onesto §2.8).
    """
    if not isinstance(p0, dict):
        return None, None
    kind = p0.get("kind")
    if kind == "get_inputs_response" and p0.get("fmt") == "telegram_inline":
        dialog_id = p0.get("dialog_id") or ""
        state, _key = load_pending_state(dialog_id, sender_candidates)
        if not state or not state.get("dialog"):
            return None, None
        dialog = state["dialog"]
        idx = int(state.get("step_index") or 0)
        if idx >= len(dialog):
            return None, None
        step = dialog[idx]
        buttons = build_dialog_keyboard(dialog_id, idx, step)
        preview = (step if (step.get("schema") or {}).get("kind")
                   == "choice_with_preview" else None)
        return buttons, preview
    if kind in ("admin_approval", "approval_required") and turn_id:
        return build_approval_keyboard(turn_id), None
    return None, None
