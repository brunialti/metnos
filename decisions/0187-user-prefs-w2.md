# ADR 0187 — Preferenze utente esplicite (W2 v1)

- **Stato**: ACCETTATO (Roberto 6/7: «w2» nel batch; storage a tabella come da raccomandazione).
- **Contesto**: report competitivo 30/5, W2 «user modeling persistente» — users.db aveva solo anagrafici; le uniche preferenze apprese erano gli scope-arg (args_defaults, per-actor, TTL 90gg dal 6/7). Mancava un posto per le preferenze ESPLICITE e stabili.

## Decisione (v1, contenuta)
1. **Storage**: tabella `user_prefs` in users.db — `(user_id, key, value, source, updated_at)`, PK (user_id,key). API `users.set_pref/get_pref/list_prefs/delete_pref`.
2. **Vocabolario CHIUSO** (§2.4 dominio chiuso = match esatto): `lang{it,en}`, `tone{neutro,informale,formale}`, `reply_length{breve,normale,dettagliata}`, `units{metric,imperial}`. Estensioni = edit di `PREF_ALLOWED` + ADR.
3. **Lettura dagli executor**: placeholder `${RUNTIME:pref_<chiave>}` nel resolver runtime (actor→utente via `owner_id_for_actor`); pref ASSENTE ⇒ placeholder INTATTO (§2.8: il buco si vede, mai stringa vuota silenziosa).
4. **UI**: sezione «Preferenze» in `/admin/users/{id}` (select per chiave, «—» = non impostata → delete) + POST `/admin/users/{id}/prefs`.
5. **Profilo**: `read_persons` include `prefs` ⇒ «chi sono io» le mostra in chat.

## Fuori scope v1 (deliberato, prossimi passi W2)
- **Set dalla CHAT** («ricorda che preferisco risposte brevi»): richiede la decisione vocab §2.2 (oggetto `preferences` nuovo vs estensione `set_persons`) — da discutere con Roberto.
- **Consumo nel FINALIZER/synth** (tone/reply_length che modellano la risposta): richiede il plumbing actor→Executor.run; candidato v2 insieme al set-da-chat.
- **`lang` per-utente attivo su i18n** (oggi la lingua è di istanza, METNOS_LANG): switch per-turno = cambio invasivo, rimandato.

## Prove
`test_user_prefs.py` 4/4 (CRUD + vocabolario chiuso + utente ignoto onesto + placeholder con-e-senza pref) · smoke su DB reale (tone=informale per roberto) · profilo `read_persons` con prefs (re-sign §7.10) · UI validata live.
