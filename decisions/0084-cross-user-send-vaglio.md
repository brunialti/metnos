---
id: 0084
title: Cross-user send vaglio — host gatekeeper, guest-to-other requires vaglio
date: 2026-05-04
status: accepted
area: runtime, executors, vaglio
related:
  - 0083  # multi-user management
complements:
  - 0083
---

## Context

ADR 0083 introduce gli user logici (host + guest) e permette di mandare
messaggi a un user via `to_user="lucia"`. Resta aperta la policy: **chi
ha titolo di scrivere a chi**.

Casi distinti, dal modello host_guest_model (27/4/2026):

- **host → host**: self-send banale.
- **host → propri guest**: e' la natura dell'host (gatekeeper della
  famiglia trusted), permesso senza vaglio.
- **host → guest altrui**: per ora non esistono "guest altrui" (single-host
  policy), ma il caso e' coperto come deny-by-default se mai esistesse.
- **guest → se stesso**: permesso (self-send dal proprio canale).
- **guest → altri (host o altri guest)**: e' la zona "richiede approvazione",
  il guest non e' autorizzato implicitamente a usare le risorse di Metnos
  per scrivere a soggetti che non sono se stesso.

## Decision

### 1. Hook deterministico nel modulo `vaglio.py`

`runtime/vaglio.py::check_cross_user_send(actor_id, target_user_id, channel)
→ {allowed: bool, reason: str|None}`.

Logica:

1. `actor_id == 'host'` → `{allowed: True}`. Host e' sempre permesso.
2. `actor_id == target_user_id` → `{allowed: True}`. Self-send.
3. altrimenti → `{allowed: False, reason: "guest_to_other_user_requires_vaglio"}`
   + entry audit `vaglio/<date>.jsonl` con kind=`cross_user_send_blocked`.

CLAUDE.md §7.9: deterministic > LLM. La policy e' una matrice 3x2,
codificarla come if/elif e' equipotente e zero-cost.

### 2. Wiring in `send_messages`

L'executor chiama `vaglio.check_cross_user_send` PER OGNI recipient
risolto via `to_user`. Comportamento:

- `allowed=True` → procede al dispatch (telegram/mail).
- `allowed=False` → entry in `failed[]` con
  `error_code="ERR_VAGLIO_REQUIRED"` + `recipient_user_id`,
  `recipient_name`, `channel`, `error=reason`.

Niente blocco dell'intera call: se Roberto chiede "manda a lucia e
marco" e per qualche ragione l'actor e' un guest, lucia (=actor) viene
inviata, marco viene messo in failed con vaglio. Coerente con la
filosofia best-effort dell'executor (CLAUDE.md §2.8 no silent failure:
l'utente vede esattamente cosa e' partito e cosa no).

### 3. Audit

Ogni cross-user send bloccato genera un record in
`~/.local/share/metnos/vaglio/<YYYY-MM-DD>.jsonl` (riuso del
`vaglio._log` esistente):

```json
{"ts_iso": "...", "kind": "cross_user_send_blocked",
 "actor": "lucia", "target_user_id": "<uuid>", "channel": "telegram"}
```

Visibilita' al host: gia' coperta dalla pagina `/admin/safety`
(o estensione futura `/admin/audit/cross-user`).

### 4. Hook futuro: vaglio interattivo one-shot

L'attuale MVP nega in silenzio (con notify nell'output). Il tassello
mancante e' un dialog inline:

> "Lucia vuole inviare un messaggio a Marco via telegram. Confermi?"
>   [una volta] [sempre per questa coppia] [no]

Implementazione rinviata: richiede ampliamento del dialog manager
(`approval_registry.py`) per supportare prompt cross-canale, ricezione
risposta del host su un canale diverso da quello dell'actor. Il design
e' nel pattern carta a 3 righe (memoria
`project_dialog_manager_authorization_ux.md`, 24/4/2026).

## Consequences

- **Privacy intra-famiglia preservata**: un guest (es. un familiare)
  non puo' usare Metnos come "spedizioniere" per scrivere ad altri
  membri o a Roberto senza che Roberto autorizzi. La risorsa Metnos
  resta sotto controllo dell'host.
- **Niente regressione del flusso single-user**: `actor='host'` (default
  di run_turn senza pairing) bypassa il check. Il sistema funziona
  esattamente come prima quando c'e' solo Roberto.
- **Test verificati 4/5/2026**:
  - `tests/test_send_messages_multiuser.py::test_cross_user_send_from_guest_to_guest_blocked`
    — actor='lucia' invia a `[lucia, marco]`: lucia ok, marco bloccato
    con `ERR_VAGLIO_REQUIRED`.

## References

- `runtime/vaglio.py::check_cross_user_send`.
- `executors/send_messages/send_messages.py::_check_cross_user_send`
  (wrapper di chiamata).
- ADR 0083 (multi-user management, contesto).
- Memoria `metnos_host_guest_model.md` (27/4/2026, "ognuno approva sul
  proprio canale").
- Memoria `project_dialog_manager_authorization_ux.md` (24/4/2026,
  pattern carta a 3 righe per upgrade futuro a vaglio interattivo).
