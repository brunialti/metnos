#!/usr/bin/env python3
"""user_preferences — le preferenze personali come dominio del planner.

Superficie conversazionale sopra `user_prefs` (W2 v1, ADR 0187): chiedere le
proprie preferenze, cambiarne una, rimuoverla. Tre builtin in-process
(`get_preferences`, `set_preferences`, `delete_preferences`) esposti al
planner come qualunque altro executor.

PERCHE' TRE EXECUTOR E NON UN GATE PRIMA DEL PLANNER
Un gate lessicale davanti al planner avrebbe dovuto riconoscere da solo le
forme di superficie: elenchi di sinonimi, varianti flesse, riempitivi
grammaticali di ogni lingua. E' hardcoding (§7.3) e non e' i18n (aggiungere
una lingua diventerebbe scriverne la grammatica a mano). Il classificatore
semantico esiste gia' ed e' il routing stesso: `prefilter` fa hard match sui
termini di affinity e `affinity_semantic` ricade su BGE-M3 quando la query usa
sinonimi, declinazioni irregolari o un'altra lingua. La mappatura
«sinteticamente» → `breve` la fa il planner, vincolato dai valori canonici
dichiarati nel manifest; le varianti di frase le impara la cache L0/L1 come
per ogni altro turno.

Il vocabolario resta CHIUSO e la sua fonte unica e' `users.PREF_KEYS` /
`users.allowed_pref_values`: qui non vive nessuna lista di chiavi o di valori,
sono derivate dal registro a ogni import. Una preferenza nuova nel registro e'
immediatamente impostabile dalla chat senza toccare questo file (il manifest
firmato si rigenera con `scripts/generate_builtin_executor_contracts.py`; il
test di copertura fallisce se resta indietro).

Ambito: ogni attore vede e cambia SOLO le proprie preferenze, risolte per
UTENTE con `devices.owner_id_for_actor` come le preferenze siti e di
presentazione. Non c'e' un percorso per toccare quelle di un altro.

Onesta' (§2.8): una preferenza dichiarata in `users.PREF_WITHOUT_EFFECT` viene
scritta ma nessun percorso di risposta la applica ancora. Il risultato lo dice
(`applied: false` + nota), invece di far credere a un effetto che non c'e'.
"""
from __future__ import annotations

import users as _users
from messages import get as _msg


# --- Risoluzione dell'utente del turno -------------------------------------

def _owner(actor: str | None) -> str:
    """Utente proprietario delle preferenze del turno.

    Stesso resolver centrale delle preferenze siti e di presentazione: l'attore
    puo' essere 'host', un device pairato o un utente del registro.
    """
    try:
        import devices as _devices
        return _devices.owner_id_for_actor(actor) or (actor or "host")
    except Exception:  # noqa: BLE001 — registro device assente: resta l'attore
        return actor or "host"


def _allowed_map() -> dict:
    """{chiave: (valori ammessi)} dal registro — nessuna lista locale."""
    return _users.preference_allowed_map()


def _applied(key: str) -> bool:
    """False se la chiave e' dichiarata senza consumatore (§2.8)."""
    return key not in _users.PREF_WITHOUT_EFFECT


# Origine leggibile della preferenza. `source` e' un identificatore tecnico
# scritto da chi imposta; la chat mostra la parola, non il codice (§7.13).
_ORIGIN_KEYS = {"chat": "MSG_PREF_ORIGIN_CHAT",
                "explicit": "MSG_PREF_ORIGIN_PANEL"}


def _origin(source: str) -> str:
    code = _ORIGIN_KEYS.get((source or "").strip().lower())
    return _msg(code) if code else (source or "")


# --- Handlers ---------------------------------------------------------------

def handle_get_preferences(args: dict, *, actor: str | None = None,
                           **_) -> dict:
    """Preferenze IMPOSTATE dall'attore, con valore, origine e stato d'effetto.

    `entries` contiene solo cio' che e' stato deciso davvero: e' la risposta
    onesta a «che preferenze ho». Il campo `available` accanto elenca cosa si
    puo' impostare, cosi' un elenco vuoto non e' un vicolo cieco.
    """
    owner = _owner(actor)
    allowed = _allowed_map()
    try:
        detailed = _users.list_prefs_detailed(owner)
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "error": str(ex), "error_class": "internal_error"}
    # Le entries sono la RISPOSTA, non il record: quattro campi che si leggono
    # in chat. I valori ammessi stanno in `available` una volta sola invece di
    # ripetersi su ogni riga, e la data di modifica non e' cio' che si chiede
    # domandando «che preferenze ho».
    entries = [
        {
            "key": key,
            "value": detailed[key].get("value"),
            "origin": _origin(detailed[key].get("source") or ""),
            "applied": _applied(key),
        }
        for key in sorted(detailed)
    ]
    out = {
        "ok": True,
        "entries": entries,
        "available": [{"key": k, "allowed": list(v)}
                      for k, v in sorted(allowed.items())],
    }
    if not entries:
        out["note"] = _msg("MSG_PREF_NONE_SET")
    else:
        # Chi ha impostato una preferenza che nessuno applica ancora deve
        # leggerlo a parole, non dedurlo da una colonna booleana (§2.8).
        inerti = [e["key"] for e in entries if not e["applied"]]
        if inerti:
            out["note"] = _msg("MSG_PREF_SOME_INERT", keys=", ".join(inerti))
    return out


def handle_set_preferences(args: dict, *, actor: str | None = None,
                           **_) -> dict:
    """Imposta UNA preferenza dell'attore su un valore del suo insieme chiuso.

    La validazione avviene qui e non a valle perche' il messaggio d'errore deve
    dire all'utente quali valori esistono: un rifiuto senza alternative non e'
    una risposta.
    """
    key = str(args.get("key") or "").strip().lower()
    value = str(args.get("value") or "").strip().lower()
    allowed = _allowed_map()
    if key not in allowed:
        return {"ok": False, "error_class": "invalid_args",
                "error": _msg("ERR_PREF_KEY_UNKNOWN", key=key or "?",
                              allowed=", ".join(sorted(allowed)))}
    values = allowed[key]
    if value not in values:
        return {"ok": False, "error_class": "invalid_args",
                "error": _msg("ERR_PREF_VALUE_INVALID", value=value or "?",
                              key=key, allowed=", ".join(values))}
    owner = _owner(actor)
    # `source` distingue una preferenza decisa parlando da una decisa nel
    # pannello: l'elenco la mostra all'utente insieme al valore.
    res = _users.set_pref(owner, key, value, source="chat")
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error") or "",
                "error_class": "operation_failed"}
    record = {"key": key, "value": value, "applied": _applied(key)}
    if not record["applied"]:
        record["note"] = _msg("MSG_PREF_NO_EFFECT")
    return {"ok": True, "results": [record], "ok_count": 1}


def handle_delete_preferences(args: dict, *, actor: str | None = None,
                              **_) -> dict:
    """Rimuove preferenze dell'attore: tornano al valore predefinito.

    `keys` e' un dominio CHIUSO (§2.4): match esatto, niente glob. `all=true`
    azzera tutto cio' che l'attore ha impostato; senza ne' l'uno ne' l'altro
    non si indovina, si chiede.
    """
    allowed = _allowed_map()
    owner = _owner(actor)
    raw = args.get("keys")
    if isinstance(raw, str):
        raw = [raw]
    keys = [str(k).strip().lower() for k in (raw or []) if str(k).strip()]
    wipe_all = bool(args.get("all"))
    try:
        current = _users.list_prefs_detailed(owner)
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "error": str(ex), "error_class": "internal_error"}
    if wipe_all:
        keys = sorted(current)
    if not keys:
        # Niente da cui dedurre l'oggetto: esito onesto con le alternative
        # concrete, non una cancellazione indovinata (§2.8, §2.11).
        return {"ok": False, "error_class": "invalid_args",
                "error": _msg("ERR_PREF_DELETE_WHICH",
                              set=", ".join(sorted(current)) or "-")}
    unknown = [k for k in keys if k not in allowed]
    if unknown:
        return {"ok": False, "error_class": "invalid_args",
                "error": _msg("ERR_PREF_KEY_UNKNOWN", key=", ".join(unknown),
                              allowed=", ".join(sorted(allowed)))}
    results = []
    for key in keys:
        removed = bool(_users.delete_pref(owner, key))
        results.append({"key": key, "removed": removed,
                        "previous": (current.get(key) or {}).get("value", "")})
    return {"ok": True, "results": results,
            "ok_count": sum(1 for r in results if r["removed"])}


# --- Tool specs (dichiarazione al planner) ---------------------------------
# Le chiavi ammesse sono un'istantanea del registro: l'enum vincola la
# generazione del planner (grammatica) e il test di copertura del manifest
# fallisce se il registro cambia senza rigenerare il contratto firmato.

def _pref_keys() -> list[str]:
    return list(_users.PREF_KEYS)


def values_hint() -> str:
    """`chiave: v1|v2` per ogni preferenza — derivato dal registro.

    Chiavi e valori canonici sono identificatori tecnici uguali in ogni lingua:
    la parte da tradurre e' solo la frase che li introduce. Usato dal manifest
    firmato (`scripts/generate_builtin_executor_contracts.py`) in IT e in EN,
    cosi' il planner vede lo stesso insieme chiuso qualunque sia la lingua.
    """
    return "; ".join(f"{k}: {'|'.join(v)}"
                     for k, v in sorted(_allowed_map().items()) if v)


def keys_hint() -> str:
    """Elenco delle preferenze ammesse, dal registro."""
    return ", ".join(_pref_keys())


def _all_values() -> list[str]:
    """Unione ordinata dei valori ammessi su tutte le preferenze.

    L'insieme ammesso dipende dalla chiave, e uno schema JSON piatto non sa
    esprimere quella dipendenza; l'unione si', ed e' quanto basta a vincolare
    la GENERAZIONE. Senza questo enum il planner scrive testo libero e produce
    troncature come `formal` per `formale`, o il segnaposto `?` (E2E 29/7).
    L'accoppiata chiave-valore la verifica poi il gestore, con un errore che
    nomina i valori giusti per QUELLA chiave.
    """
    out: set[str] = set()
    for values in _allowed_map().values():
        out.update(values)
    return sorted(out)


GET_PREFERENCES_TOOL = {
    "type": "function",
    "function": {
        "name": "get_preferences",
        "description": (
            "SCOPO: Elenca le preferenze personali che l'utente ha impostato, "
            "con valore e origine. PATTERN: get_preferences(). "
            "NON: non serve per i task schedulati (read_tasks) ne' per le "
            "skill installate (list_skills) ne' per il profilo anagrafico "
            "(read_persons). OUT: entries=[{key, value, source, applied, "
            "allowed}] piu' `available` con tutto cio' che si puo' impostare."
        ),
        "parameters": {"type": "object", "required": [], "properties": {}},
    },
}

SET_PREFERENCES_TOOL = {
    "type": "function",
    "function": {
        "name": "set_preferences",
        "description": (
            "SCOPO: Imposta una preferenza personale dell'utente su un valore "
            "ammesso. PATTERN: set_preferences(key=\"reply_length\", "
            "value=\"breve\"). NON: riportare una preferenza al valore "
            "predefinito, toglierla o dimenticarla e' delete_preferences, non "
            "questo tool con un valore inventato; non cambia la configurazione "
            "di sistema (admin) ne' lo stato di una skill (set_skills); una "
            "richiesta valida solo per il turno in corso non e' una "
            "preferenza. OUT: results=[{key, value, applied}]."
        ),
        "parameters": {
            "type": "object",
            "required": ["key", "value"],
            "properties": {
                "key": {
                    "type": "string",
                    "enum": _pref_keys(),
                    "description": "Preferenza da impostare.",
                },
                "value": {
                    "type": "string",
                    "enum": _all_values(),
                    "description": (
                        "Valore canonico ammesso per quella preferenza — "
                        + values_hint()
                    ),
                },
            },
        },
    },
}

DELETE_PREFERENCES_TOOL = {
    "type": "function",
    "function": {
        "name": "delete_preferences",
        "description": (
            "SCOPO: Rimuove preferenze personali: le rimosse tornano al valore "
            "predefinito. USA QUESTO quando l'utente vuole togliere, "
            "azzerare, dimenticare una preferenza o rimetterla come prima. "
            "PATTERN: delete_preferences(keys=[\"tone\"]) oppure "
            "delete_preferences(all=true). NON: non cancella utenti "
            "(delete_persons) ne' task (delete_tasks). OUT: "
            "results=[{key, removed, previous}]."
        ),
        "parameters": {
            "type": "object",
            "required": [],
            "properties": {
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Preferenze da rimuovere, per nome esatto. "
                        "Ammesse: " + keys_hint()
                    ),
                },
                "all": {
                    "type": "boolean",
                    "description": (
                        "true rimuove tutte le preferenze impostate "
                        "dall'utente."
                    ),
                },
            },
        },
    },
}


# Termini di affinity: superficie utente IT+EN per il match duro del
# prefilter. Il fallback semantico (BGE-M3) copre sinonimi, forme flesse e
# altre lingue, quindi qui stanno i termini portanti, non le loro varianti.
BUILTIN_INPROC_SPECS = [
    {"name": "get_preferences", "tool_spec": GET_PREFERENCES_TOOL,
     "affinity": ["preferenza", "preferenze", "impostazioni", "preference",
                  "preferences", "settings", "elenco", "list"]},
    {"name": "set_preferences", "tool_spec": SET_PREFERENCES_TOOL,
     "affinity": ["preferenza", "preferenze", "impostazione", "preference",
                  "preferences", "setting", "imposta", "set", "cambia",
                  "change"]},
    {"name": "delete_preferences", "tool_spec": DELETE_PREFERENCES_TOOL,
     "affinity": ["preferenza", "preferenze", "impostazione", "preference",
                  "preferences", "setting", "togli", "rimuovi", "remove",
                  "azzera", "reset", "predefinito", "default"]},
]


_I18N_KEYS_ENSURED = False


def ensure_i18n_keys() -> None:
    """Registra (se assenti) le chiavi i18n dei messaggi utente. Idempotente."""
    global _I18N_KEYS_ENSURED
    if _I18N_KEYS_ENSURED:
        return
    try:
        from i18n import register_key_if_missing as _rk
        _rk("MSG_PREF_NONE_SET",
            "Non hai impostato nessuna preferenza: valgono i valori "
            "predefiniti.",
            "You have set no preferences: the defaults apply.")
        _rk("MSG_PREF_NO_EFFECT",
            "Preferenza registrata, ma oggi nessun percorso di risposta la "
            "applica ancora.",
            "Preference recorded, but no answer path applies it yet.")
        _rk("ERR_PREF_KEY_UNKNOWN",
            "Preferenza sconosciuta: {key}. Quelle disponibili sono: "
            "{allowed}.",
            "Unknown preference: {key}. The available ones are: {allowed}.")
        _rk("ERR_PREF_VALUE_INVALID",
            "Valore '{value}' non ammesso per {key}. I valori possibili sono: "
            "{allowed}.",
            "Value '{value}' is not allowed for {key}. The possible values "
            "are: {allowed}.")
        _rk("ERR_PREF_DELETE_WHICH",
            "Quale preferenza vuoi rimuovere? Hai impostato: {set}.",
            "Which preference do you want to remove? You have set: {set}.")
        _rk("MSG_PREF_SOME_INERT",
            "Nota: {keys} sono registrate ma nessun percorso di risposta le "
            "applica ancora.",
            "Note: {keys} are recorded but no answer path applies them yet.")
        _rk("MSG_PREF_ORIGIN_CHAT", "dalla chat", "from chat")
        _rk("MSG_PREF_ORIGIN_PANEL", "dal pannello", "from the panel")
        _I18N_KEYS_ENSURED = True
    except Exception:  # noqa: BLE001 — DB i18n assente: get() fa fallback
        pass


ensure_i18n_keys()
