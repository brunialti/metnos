# Correzione avvio LRE e dati di avanzamento — 2026-09-16

## Perimetro

Autorizzazione: «fissa il codice e passalo in produzione». Include i dati UI
richiesti precedentemente e sviluppati in parallelo: avvio effettivo, percentuale
sulle unità note, fine prevista prudente e `n.a.` per dati non disponibili.
Nessuna modifica di modelli, prompt Tutor, permessi, budget o dati originali.

`admit_prerequisite` conserva la destinazione server quando entrambi i contratti
verificati sono server-only, compreso il default non remoto. Se uno dei due
ammette dispositivi, mantiene la destinazione ricevuta. Nessuna eccezione per
foto o parole della domanda; il contenuto restituito non sceglie la collocazione.
Un rifiuto preliminare registra il punto del controllo e il tipo di eccezione,
senza argomenti, percorsi o testo libero.

La UI legge lo stato, non lo modifica: una query aggregata per pagina, limitata
al proprietario; avvio dalla revisione corrente, percentuale mai al 100% prima
della conclusione; stima assente per foto multifase, dati vecchi, pausa, errori
o motore non pronto. Cinque chiavi i18n complete IT/EN. Nessuna migrazione DB.

## Verifica precedente alla pubblicazione

- Suite integrata LRE, ammissione, contratti, UI HTTP/JavaScript, servizi,
  riconciliazione, rilascio e documentazione: **900 superati, 1 escluso**.
  L'esclusione richiede una prova root/non-root in ambiente dedicato; non
  conteggiata tra i successi.
- Prima esecuzione: 899 superati e un test incapace di caricare il catalogo
  firmato dall'albero di authoring. Corretta la fixture del test: copia privata,
  chiave effimera, digest e stato linguistico coerenti, verifica firme ancora
  attiva; nessuna modifica al catalogo produttivo o ai suoi controlli.
- Regressione integrata: wrapper, guardia, adapter, compilatore e ammissione
  reali, archivio temporaneo e nessun modello. Tre consegne (ripetizione dello
  stesso turno e turno successivo) producono un solo job del proprietario.
- Operazioni remote, device-only, ibride e metadati ostili non vengono
  convertiti in richieste server; la chiamata diretta remota resta rifiutata.
- 99 HTML pubblici validi, riferimenti UI aggiornati; differenze senza errori
  formali. Nessun processo residuo ad alto consumo rilevato.

## Pubblicazione

Completata sulla **release 57**. Commit implementazione `6da80667`, impronte
canoniche `bf0131da`. Ulteriori **99 test** dei confini di contratto, ammissione
e pianificazione LRE superati dopo la preparazione.

La precedente candidata e il suo handoff sono conservati sotto
`/home/roberto/.local/state/metnos-release-cycle/` con suffisso
`before-lre-placement-20260916`. Il nuovo albero comprende 1772 file, censimento
`fe0c3f6eba3fadf4693fcdfc2979f2e5a74f06406b1751656ebb2c3a443dae1b`.
Sorgenti pubbliche: `sha256:c43dc36ee0b5b47fadcfde0aad6aa51d85981959a7b6bb3e3f061230f305da17`.
Sorgente ricevuta: `sha256:cd11432faa2e885434dc682e59a85f10dca84e674094934f7f5a619388128864`.

Il primo tentativo è stato rifiutato prima della costruzione per un blocco
amministrativo momentaneamente occupato. Il controllo successivo non rilevava
più quel blocco; nessun processo interrotto, lucchetto rimosso o controllo
forzato. La stessa candidata è stata ripresa con la procedura canonica.

Pacchetto firmato:
`sha256:901819a2f8faa7219275c3587aceacd9544049a2c6c46ee20dec89a1202a1725`.
Evidenze: `/var/lib/metnos-admin/rm0008-cycle-evidence-901819a2f8faa721`.
Log: `/tmp/metnos-lre-release.SSgcNeOH/release-cross-1789552823702516953.log`.
Esito finale: codice 0, `CUTOVER_OK`, `PREFLIGHT_VERIFIED`,
`RELEASE_EDITS_ADMITTED`, `ok=true`; 107 contratti valutati, nessuna modifica
di executor da ammettere. Passaggio:
`sha256:e01e349f3e8bec72d7975bfea2bb3886f488036beb8ca51126386ca94435188f`.

Controllo delle 10:06:08 UTC, evidenza `run-faaf3fhb`: head 57 attestata,
Metnos operativo, LRE abilitato/pronto, quattro servizi attivi, zero riavvii
anomali e nessuna attivazione pendente. PID HTTP 510165, Telegram 511056,
LRE 510164, browser 511060. UI servita con tutti i nuovi campi, senza chiavi
i18n irrisolte. Configurazione LRE invariata:
`24b0ec70d4bce174a838d86c3dfd3a9e6e30c37731354df776223f5eb3f382e4`.

## Prova reale del difetto

Ripetuta senza modifiche la domanda **«cerca localmente foto con il mare»**.
Turno `28e856c316db40ec`, 3313 ms; `find_images_indices` risponde con successo
e ricevuta del lavoro `wrk_b7a73e10713b4c15a720d264cf8fd98a`.
Evidenza privata: `/var/lib/metnos-admin/agent-runs/run-rr2p0neo`.
Il lavoro è creato alle 10:07:10 UTC, poi passa da `queued` a `running`.

Controlli alle 10:07:31 e 10:08:45 UTC (`run-yw2jv4to`, `run-4rd1_6h3`):
stessi PID, zero riavvii, un solo lavoro, presenza aggiornata. La UI riceve
`started_at=2026-09-16T10:07:10.418758Z`, percentuale 0 e stima nulla.
Questo valore è coerente con una fase iniziale non ancora conclusa, non è
una percentuale delle fotografie già lette.

Alle **10:10:33 UTC**, controllo `run-q88wwl82`: fase `discover` in esecuzione,
un solo tentativo, nessun errore, **492 blocchi di discovery / 15.744 sorgenti**
già persistiti, ultimo blocco scritto nello stesso secondo del controllo.
È progresso reale durante la scansione; non viene confuso con completamento
dell'analisi visuale o pubblicazione dell'intero indice. L'elaborazione resta
attiva e non è stata riavviata, forzata o privata dei suoi limiti.

Seconda misura alle **10:12:02 UTC**, `run-9aj7o48k`: **630 blocchi / 20.160
sorgenti**, sempre lo stesso primo tentativo di discovery e nessun errore;
ultimo blocco scritto 0,13 secondi prima della misura. Incremento di 4.416
sorgenti in circa 88 secondi: esclusa una fase immobile in questo intervallo.
Controllo host finale: nessun processo orfano ad alto consumo rilevato.

## Guide e Tutor

Guide pubbliche IT/EN distribuite con `deploy.sh --static-only` e procedure
Cloudflare/Wrangler: `https://06120796.mykleos.pages.dev`; testo dei nuovi
campi riletto anche da `metnos.com` in entrambe le lingue. Rapporti e analisi
interni non pubblicati; nessun push GitHub. Cloudflare resta strumento di
sviluppo/documentazione, non componente di esercizio.

Turno informativo `6caf9dab2dee40ad`, 11617 ms, evidenza `run-1xmduodu`:
nessun passo operativo; il Tutor cita la nuova guida e descrive avvio, unità
note e `n.a.`. La prova dimostra l'aggiornamento delle fonti, non certifica
la qualità generale del Tutor: la risposta parafrasa impropriamente il requisito
di fase unica come fase già completata. L'analisi Tutor precedente resta aperta;
nessun algoritmo o prompt modificato in questo rilascio.
