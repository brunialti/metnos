# Metnos live stack gate

- Profile: `live-integrated-user-target`
- Result: `green`
- Generated: `2026-08-24T11:39:47Z`
- Live cutover performed: `true`
- Pilot contract: `bf353d431ebbaedfdf1573e69e0e5acfa8f38a46052d38995c62940f53092e6a`
- Host fingerprint: `33631b19daeb02e1c6cd0a78b4002c703f3aa221b622c43aae7539541ede3b23`

Il pilot finale ha completato due cicli integrati con readiness, un turno reale
e rollback del baseline per ciascun ciclo. I turni sono
`4b5c5a5c15724dee` e `1259e4909212412e`; entrambi hanno eseguito un solo step
con esito positivo.

Il cutover ha quindi disabilitato e arrestato il servizio HTTP di sistema e ha
abilitato `metnos.target` nell'account di servizio. Il controllo composito,
l'inventario di 122 executor e il turno reale `0ecb5e2e71de477c` sono verdi.
Il servizio precedente resta installato come percorso di ripristino, ma non e'
attivo ne' abilitato.

Dopo il cutover sono stati eseguiti altri due cicli live completi. Prima e dopo
ciascun turno lo stack era pronto e quiescente; i turni
`5829a75ec666437b` e `76065e156ef64eb5` hanno entrambi eseguito `get_now` con
successo. Il gate isolato del lifecycle e' inoltre verde per due cicli
consecutivi da 148 test. La suite finale dell'intero repository ha chiuso con
7.043 test e 1.166 subtest superati, 101 esclusioni deliberate e nessun errore.
