# UI-AUTH-001 — accesso amministrativo

Stato: specifica approvata dal mandato operativo del 24/8/2026.

## Contratto

- Una navigazione `GET` o `HEAD` che negozia `text/html` verso qualunque rotta
  `/admin` protetta reindirizza a `/admin/login` e porta una destinazione
  `next` relativa, validata e della stessa origine.
- Una chiamata JSON non viene trasformata in navigazione. Risponde con un
  codice stabile, `message` localizzato e `login_url` relativo.
- Una sessione assente o scaduta produce `401`; un'identita' `user` valida ma
  non amministrativa produce `403`. Il login non promuove quella identita':
  richiede comunque la chiave amministrativa.
- `POST`, `PUT`, `PATCH` e `DELETE` non vengono reindirizzati e non sono mai
  riprodotti dopo il login.
- Dopo un login valido il browser torna soltanto al `next` ammesso; in assenza
  di destinazione torna a `/admin`.

## Validazione di `next`

Sono ammessi esclusivamente path assoluti relativi all'origine che iniziano da
`/admin`, senza schema, host, userinfo, backslash, caratteri di controllo o
prefisso `//`. Fragment non ammessi. La query e' conservata. Ogni valore non
valido decade a `/admin`.

La regola e' condivisa da middleware e handler di login. Non contiene route
specifiche e non legge la destinazione da header inoltrati.

## Cache e segreti

Login, rifiuti e redirect usano `Cache-Control: no-store`; la pagina usa
`Referrer-Policy: no-referrer`. La chiave compare soltanto nel corpo POST, non
in URL, log, telemetria o HTML di risposta. Il cookie resta `HttpOnly`,
`SameSite=Strict` e `Secure` quando lo schema esterno verificato e' HTTPS.

## Compatibilita' e gate

I client senza `Accept: text/html` conservano risposte JSON. I test coprono
anonimo, cookie scaduto, ruolo user e admin; metodi sicuri e mutanti; `next`
valido e ostile; ritorno dopo login; reverse proxy e lingue IT/EN.
