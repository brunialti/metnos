# TODO — la logica della quota di sessioni va rifatta (7/8/2026)

> Chiesto da Roberto la notte del 7/8, dopo averci sbattuto contro quattro
> volte di fila in mezz'ora. Qui c'e' il PERCHE' e cosa serve; l'ordine di
> grandezza e' un pomeriggio, non una settimana.

## Che cosa fa oggi

`session_broker`: `_MAX_CONTEXTS = 4` sessioni totali, `_PER_USER_QUOTA = 2`
per utente, `_TTL_IDLE_S = 15 min` di inattivita'. Un `open_sites` che trova la
quota piena fallisce con `quota_exceeded`.

## Perche' non va

1. **Un login riuscito lascia la sessione aperta** — giustamente, e' la sessione
   autenticata per l'azione successiva. Ma nessuno la chiude quando il TURNO
   finisce: resta li' 15 minuti. Due tentativi e sei fuori.
2. **Il riuso e' fragile**: pretende identita' esatta su otto campi (allowlist,
   etichetta, modo credenziali, browser, tecniche stealth, binding). Un
   dettaglio diverso e non riusa — apre e brucia quota. La notte del 7/8 e'
   bastato che lo sblocco automatico aggiungesse un host alla allowlist viva
   (corretto congelando il confine dichiarato, ma la fragilita' resta).
3. **La quota e' per PROCESSO**: un riavvio del sidecar la azzera. Cioe' il
   rimedio pratico e' riavviare un servizio — che non e' un rimedio.
4. **Nessuno puo' chiudere una sessione dalla chat**: `delete_sites` esiste, ma
   l'utente non sa che sessione ha aperto ne' come si chiama.

## Che cosa serve

- **Chiusura a fine turno** per le sessioni che nessuno riprendera' (nessun
  pending, nessun handoff): il TTL e' una rete, non la politica.
- **Riuso per CHIAVE OPERATIVA** (proprietario + host + etichetta + mandato)
  invece che per uguaglianza di otto campi; il resto sono dettagli di
  configurazione che non cambiano l'identita' della sessione.
- **Quota che si spiega e si sblocca dalla chat**: il messaggio nuovo (7/8)
  gia' dice quante sessioni, su quali siti e fra quanto scadono; manca
  l'azione — «chiudile» deve essere una frase, non un riavvio di servizio.
- **Persistenza**: se la quota deve essere una politica e non un limite di
  processo, va tenuta fuori dalla memoria del sidecar.

## Riletto sul codice la notte del 7/8 — due dei quattro punti si ridimensionano

Prima di rifare, ho riletto `_reuse_compatible_session` riga per riga. Delle
otto condizioni di riuso, **sei sono autorita' o modo visibile**, non dettagli
di configurazione: proprietario, utente logico, host, modo credenziali,
mandato di task e mandato credenziale sono autorita'; `browser_mode` decide se
l'utente VEDE la finestra; le tecniche stealth non si possono acquisire dopo.
Allargare li' non e' comodita', e' concedere a una richiesta piu' di quanto
abbia chiesto.

Il confine di rete (`allowlist`) e' il caso interessante: la fragilita' vera —
lo sblocco automatico che faceva crescere l'insieme VIVO e rompeva il riuso —
e' gia' chiusa congelando `allowlist_declared`. Un ulteriore allentamento
(riusare una sessione il cui confine e' un SOTTOINSIEME di quello richiesto)
sarebbe difendibile, ma con lo sblocco automatico acceso il confine vivo
cresce, e riusare quella sessione per una richiesta che lo sblocco NON ha
concesso sarebbe un guadagno di privilegio silenzioso. Non si fa senza una
decisione esplicita.

Resta quindi in piedi il punto 1 (nessuno chiude a fine turno) con una
tensione da sciogliere prima di scrivere codice: **chiudere a fine turno
uccide il riuso fra turni**, cioe' costringe a rifare il login — e su un sito
con verifica in due passaggi significa richiedere il codice all'utente ogni
volta. Il TTL non e' pigrizia: e' cio' che rende possibile «entra nel sito» e
poi, in un turno separato, «adesso mostrami X». Una chiusura a fine turno ha
senso solo per le sessioni NON autenticate, che non hanno niente da
riprendere.

Il punto piu' redditizio e senza controindicazioni resta il 4: **poter chiudere
le sessioni dicendolo**. `delete_sites` accetta gia' `all=True`; manca che il
messaggio di quota piena dica all'utente quella frase.

## Dove guardare

`runtime/playwright_sidecar/session_broker.py` — `op_open` (quota, riuso),
`_reuse_compatible_session`, `_TTL_IDLE_S`, `session_close`. Il messaggio
utente sta in `executors/open_sites/open_sites.py` con le chiavi
`MSG_SITES_RC_QUOTA*`.
