# Indicatore di disponibilità LRE e analisi Tutor — 16 settembre 2026

## Perimetro e diagnosi

Richiesta: applicare la correzione del pallino verde con LRE disabilitato e
analizzare i problemi del Tutor e come superarli. La correzione pubblicabile
riguarda la presentazione, non salute canonica, supervisione o abilitazione.
Il processo LRE disabilitato ma convergente è correttamente sano; la scheda
usava quel valore come se rappresentasse la disponibilità della funzione.

Ora il pallino LRE è grigio se disabilitato e verificato, verde solo se
abilitato e pronto, giallo per transizione o stato incerto, rosso per guasto
o configurazione invalida. Lo stato funzionale è espresso anche in testo;
quello del processo resta nei Dettagli tecnici. Le altre schede non cambiano.
Testi già presenti nel catalogo i18n, prove IT/EN, nessuna nuova frase locale.

Il rapporto interno `internal/design/tutor-affidabilita-analisi-20260916.md`
è una proposta, non una specifica approvata. Separa recupero, ambito,
composizione e valutazione; contiene attività T0–T8 con condizioni di uscita.
Nessun codice, prompt o modello dell'esperimento Tutor respinto è incorporato.
Il registro UI e le guide cambiano soltanto per documentare il nuovo indicatore.

## Verifiche prima del rilascio

- Suite integrata servizi, endpoint LRE, console, documentazione generata,
  riconciliazione e ciclo di rilascio: **376 superati, 1 saltato**.
  L'esclusione riguarda la prova di permessi root/non-root, che richiede
  un ambiente temporaneo dedicato; non è conteggiata come superata.
- Suite confini e autorità di installazione: **96 superati**.
- Revisione indipendente: nessun rilievo bloccante; ulteriori 65 test
  superati e lo stesso test privilegiato saltato. Non sommare questi
  risultati, parzialmente sovrapposti, come casi indipendenti.
- Quattordici scenari dell'indicatore in entrambe le lingue; verifica di
  priorità errori/incertezza, testo accessibile, stato non mutato e assenza
  di verde non giustificato. LRE non viene disabilitato in produzione per
  provare il grigio.
- Controllo formale delle differenze e 99 documenti pubblici IT/EN validi.

Commit implementazione/documentazione/analisi: `8e1dd92b`; impronte derivate
dal percorso canonico: `ccffdb38`.
Staging precedente conservato in
`/home/roberto/.local/state/metnos-release-cycle/export.before-lre-indicator-20260916`
con relativo handoff. Nessun push GitHub.

Staging nuovo: 1772 file, censimento
`b47cbd9f34a241ceed1b45bf318ad7cf110d6da08e1bc6646476eaad16454c00`.
Impronta sorgenti pubbliche:
`sha256:ca3643f7f1a5a2f53cc114c52d44ebf5b2fa49704aac6d78d3e7a622dfda6ebf`.
Sorgente ricevuta:
`sha256:666f27eef3150f30300f8fba35ed1a5da92c3e6bdfea5a87191fb523724750f5`.

Controllo precedente al rilascio alle 09:08:33 UTC:
`/var/lib/metnos-admin/agent-runs/run-32u3adhq`. LRE abilitato e pronto,
PID 400369, un thread, zero riavvii anomali. Tre lavori conclusi, due
falliti storici, uno da verificare. Configurazione persistente:
`24b0ec70d4bce174a838d86c3dfd3a9e6e30c37731354df776223f5eb3f382e4`.
Foto `wrk_df6d27b3c6c24d828aca6a2b24915b5e`: `needs_attention`, versione 5,
prossimo evento 6, impronta
`64f069dafa2f1adb942d8ea2b809cefcbd51c59d9dad9f35925f4843a3907468`.
Nessuna ripresa, alterazione del budget, cancellazione dei consumi o storia.

## Evidenza di rilascio

Pacchetto firmato 56:
`sha256:376d4b75e7759293075d8812e91efd25a33c5f0b8c69b6ae77eb56057b9aa031`.
Evidenze canoniche:
`/var/lib/metnos-admin/rm0008-cycle-evidence-376d4b75e7759293`.
Log completo:
`/tmp/metnos-lre-release.SSgcNeOH/release-cross-1789549828868488483.log`.
Anteprima: 107 componenti, nessuna modifica di executor da ammettere.
La procedura è conclusa con codice 0, `CUTOVER_OK`, stato
`PREFLIGHT_VERIFIED`, `RELEASE_EDITS_ADMITTED`, `ok=true`.
Transazione di passaggio:
`sha256:b659eb732a8f896e940ab90ab7dd89a33f3975f1a2ff15b1653496b5e0e5836b`.

Controllo HTTP autenticato alle **09:16:09 UTC**, evidenza
`/var/lib/metnos-admin/agent-runs/run-1pfnltix`:
Metnos operativo, non in manutenzione; LRE abilitato e pronto, presenza
aggiornata. Quattro servizi attivi, zero riavvii anomali, nessuna attivazione
pendente. PID HTTP 434092, Telegram 435024, LRE 434091, browser 435023.
La scheda effettivamente servita ha pallino verde coerente con il motore
abilitato, stato funzionale principale, processo nei dettagli e nessuna
chiave i18n irrisolta. Configurazione e impronta completa del lavoro foto
sono identiche a prima del rilascio.

## Prova informativa reale e limiti

Turno HTTP `592d4d3c39cb48a0`, **4,517 secondi**, esito `answer`, nessun
passo operativo (`steps_summary=[]`). Evidenza privata:
`/var/lib/metnos-admin/agent-runs/run-hhq2hr6j`.
Domanda: «Nella pagina Servizi, che cosa significa il pallino grigio di LRE?
Spiega soltanto la guida, senza eseguire operazioni.»
La risposta distingue correttamente i quattro colori e funzione/processo,
coerentemente con la guida pubblicata. Questo è un riscontro puntuale e una
prova del percorso HTTP, **non una certificazione generale del Tutor**.
Non sostituisce la domanda incidentale immutabile, i casi trasversali o
l'A/B proposti nel rapporto di analisi. Nessun algoritmo o prompt Tutor
è cambiato rispetto alla base `ade071ee`.

Controllo conclusivo dopo il turno alle **09:17:03 UTC**, evidenza
`/var/lib/metnos-admin/agent-runs/run-eck0chjq`: stessi PID, zero riavvii,
Metnos operativo, LRE pronto, configurazione e lavoro foto invariati,
nessuna attivazione pendente. Fra 09:16:09 e 09:17:03 la CPU cumulativa
del lavoratore cresce di circa 0,0355 secondi su 53,35 secondi, pari a
circa 0,067% di un core. È riposo abilitato, non prova di carico attivo.

## Documentazione esterna

Le guide sono pubblicate soltanto con `deploy.sh --static-only`, seguendo
le procedure Cloudflare/Wrangler, senza compilare il catalogo Tutor locale.
Il primo tentativo è stato respinto dal controllo automatico per assenza di
consenso esplicito all'invio esterno; non è stato aggirato. Roberto ha poi
autorizzato «Sì, pubblica solo le guide pubbliche». L'analisi Tutor rimane
interna e fuori dall'albero `docs/`. Cloudflare non diventa componente di
esercizio dell'installazione pubblica.

Pubblicazione completata: `https://60a9dbe6.mykleos.pages.dev`.
Le guide IT/EN sono state rilette anche da `metnos.com`, seguendo i
reindirizzamenti: entrambe contengono i nuovi colori e i Dettagli tecnici.
