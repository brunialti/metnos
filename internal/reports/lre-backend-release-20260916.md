# Release limitata ai controlli backend — 16 settembre 2026

**Esito: pubblicazione backend completata sulla release 55. LRE inizialmente
disabilitato, poi abilitato su richiesta esplicita di Roberto (sezione finale).
Lavoro foto invariato; Tutor escluso e ancora non certificato.**

## Mandato e perimetro

Roberto ha approvato la pubblicazione separata delle correzioni verificate di
`compress_files` e `open_sites`, mantenendo LRE spento e trattando il Tutor
separatamente. Nessuna autorizzazione a riprendere l'indicizzazione foto,
allargare budget, cancellare consumi sconosciuti o riscrivere la storia.

Nuovo ramo `codex/lre-backend-release`, base `67955816` della release 54;
implementazione selettiva `5464b469`, impronte canoniche `3840cc8a`.
Il ramo sperimentale `codex/lre-resilience` e il rapporto A/B del Tutor sono
conservati, non incorporati. Lo staging respinto è archiviato in
`/home/roberto/.local/state/metnos-release-cycle/export.rejected-tutor-20260916`
con il relativo handoff. Non usarlo per una pubblicazione successiva.

Otto file di correzione: tre moduli runtime per proprietà/annullamento,
test delle proprietà, ADR0224, voce dell'indice anti-regressione e
architettura executor IT/EN. La revisione indipendente ha verificato identità
byte per byte con il sottoinsieme già esaminato e assenza delle modifiche
Tutor. Codice Tutor, tutti i prompt, registro UI e guide LRE IT/EN risultano
identici anche confrontando le distribuzioni installate 54 e 55.

## Prove e preparazione

- Quattro prove native delegate superate sul ramo separato: percorsi e sessioni
  corretti passano, due varianti intenzionalmente difettose vengono rifiutate.
- Suite complessiva: **528 passati, 5 saltati**. Quattro dei cinque sono le
  prove native eseguite separatamente; la quinta segnala
  `linux_sandbox_registry_unavailable`, confermato anche nella riesecuzione
  delegata (4 passati, 1 saltato). Non è conteggiata come prova superata.
  Nessun test o oracolo indebolito.
- Primo avvio nel worktree nuovo fermato in raccolta per esportazione di test
  assente; preparazione canonica completata prima della riesecuzione.
- La prima suite dentro l'ambiente ristretto dell'agente ha 14 fallimenti
  `socket.bind: Operation not permitted`, 514 successi e 5 salti. Rieseguita
  fuori da quel divieto, con fixture isolate, produce i 528 successi sopra.
  Non erano errori dell'implementazione e non sono stati esclusi dalla suite.
- Controlli formali delle differenze e documentazione pubblica superati;
  99 documenti indicizzabili in italiano e inglese.

Preparazione canonica: 1772 file, censimento
`17fa18a81064524a79507844fdc9d539e7aeb0ebec45672500229bbe425c904c`.
Impronta privata
`sha256:6b7112af4031f797ec36ffcfc278f7441f2cc2a35820b06eed2c59bce638d862`;
pubblica
`sha256:99d10a98b1b0c961a944ca2ef412950ec664a29e950cd973fbe91b40a8afb4da`.
Sorgente ricevuta
`sha256:917633684d6defec6d4a2211a5a7a36c7b1845294878889bafa00f53195b84ea`.

## Pubblicazione e controlli operativi

Prima del passaggio, alle 08:35:46 UTC, produzione 54 attestata e operativa:
`/var/lib/metnos-admin/agent-runs/run-s9g8z8u_`. LRE disabilitato; quattro
servizi attivi senza riavvii. Configurazione LRE e lavoro foto invariati.

La procedura canonica ha ritirato soltanto la precedente candidata 55 mai
selezionata, conservandola in
`/var/lib/metnos-admin/rm0008-withdrawn-claims/unclaimed-00000000000000000055-08b3a30e53ee28c9`.
Nuova release 55:
`sha256:a01e5dd4d19f2009164d712e442e4c25908a4fe4c86fd74ec51f5e873606667b`.
Evidenze: `/var/lib/metnos-admin/rm0008-cycle-evidence-a01e5dd4d19f2009`.
Registro completo del comando:
`/tmp/metnos-lre-release.SSgcNeOH/release-cross-1789548028133968613.log`.

Anteprima firmata e controllo di inattività superati: 107 componenti valutati,
solo i due attesi da aggiornare. Il passaggio ha raggiunto `PREFLIGHT_VERIFIED`,
transazione
`sha256:84688db83427222b0bae734c291ead9a78c9bccad286a70c4bde56a073e0e454`.
Ammissione e attivazione concluse: `RELEASE_EDITS_ADMITTED`, codice finale 0,
`ok=true`, `restarted=true`, controllo finale `ready=true`. Nuove generazioni:

- `compress_files`:
  `sha256:cf7cac826113740024ed5fdc60d6813ff6e2b356d3070029313ae11a8ec4e707`;
- `open_sites`:
  `sha256:d531cc33e93263209ffe53a7c089217834455951adc28755574e73532e1ad101`.

Solo questi due hanno nuove ammissioni. L'attivazione comprende anche gli
otto componenti già ammessi ma pendenti dalla release 54: `act_sites`,
`delete_sites`, `list_dirs`, `login_sites`, `read_sites`, `describe_images`,
`list_skills`, `set_skills`. Il residuo è stato chiuso dalla procedura
canonica, non cancellato manualmente. Catalogo vivo e locale: 123 capacità
coincidenti; contratto browser allineato; controlli HTTP e componenti gestiti
superati. LRE è un servizio supervisionato attivo con funzione disabilitata,
non un'elaborazione riavviata.

Alle 08:47:19 UTC, evidenza
`/var/lib/metnos-admin/agent-runs/run-ysf6pv_a`, head 55 attestato:
`sha256:6de89cb51e4aaa23362cd3f54353555a4048b391d322b235c1134277cdc1ddee`.
Metnos operativo, nessuna modalità manutenzione; quattro servizi attivi e
nessun riavvio anomalo. Nessuna attivazione pendente. Console verificata con
motore disabilitato, ultimo risultato e dettagli tecnici; nessuna chiave
i18n irrisolta. Tutte le 139 chiavi LRE sono presenti e tradotte IT/EN.

Configurazione LRE invariata:
`e4729eeeedfc5ef4b1e11c2f120a4ac2a835d55948a3e3065001dc5a63f2ed5b`.
Lavoro foto `wrk_df6d27b3c6c24d828aca6a2b24915b5e` invariato, impronta:
`64f069dafa2f1adb942d8ea2b809cefcbd51c59d9dad9f35925f4843a3907468`.
Resta `needs_attention`, versione 5, prossimo evento 6: 968 unità confermate,
13 analisi da verificare e 954 pendenti. Nessun aumento di budget e nessuna
pubblicazione o ripresa dell'indice foto.

## Prova reale e limiti

Prima prova HTTP su file sintetico sotto il workspace di servizio:
turno `7355a25b06fc493d`, 23,243 secondi; rifiuto `vaglio_guard` perché
`/var` è un albero di sistema protetto. Nessuna operazione eseguita. Evidenza
`/var/lib/metnos-admin/agent-runs/run-6ig6inwd`. Il bersaglio della fixture
era inadatto; le protezioni non sono state cambiate né aggirate.

Nuova fixture privata sotto `/tmp`, stesso contenuto e stessa operazione:
turno `a554adb892dd4837`, **5,029 secondi**, `compress_files` riuscito.
Verificati indipendentemente la creazione dello ZIP nelle directory annidate,
la sola voce `prova.txt`, il contenuto esatto e la sorgente intatta.
Evidenza `/var/lib/metnos-admin/agent-runs/run-_7q8jhtm`;
artefatto `/tmp/backend-release-55-proof-ktum3b5g/output/nested/prova.zip`,
impronta `8d31cea782d4662965605169248da627a6d9e10fb181f4056bfb795e0525625a`.
I piccoli file sintetici sono conservati come evidenza; non sono dati utente.
Nessun annullamento globale invocato sullo storico dell'amministratore.

Controllo conclusivo dopo la prova: **08:50:22 UTC**, evidenza
`/var/lib/metnos-admin/agent-runs/run-30is9g96`. Head, PID e stato dei servizi
stabili, zero riavvii anomali; LRE disabilitato e impronta foto invariata;
nessuna attivazione pendente. Il lavoratore ha un solo thread e fra i due
controlli consuma circa 0,06% di un core: misura del riposo disabilitato, non
certificazione del carico attivo. Nessun processo residuo anomalo rilevato.

Le prove native e l'ammissione attestano il protocollo di annullamento delle
sessioni; non viene dichiarata una nuova prova di navigazione web reale.
Il carico con LRE abilitato, la ripresa foto e la correzione Tutor restano
fuori da questa certificazione limitata.

Guide allineate con `deploy.sh --static-only`, seguendo le procedure
Cloudflare/Wrangler, senza compilare o modificare il catalogo Tutor locale.
Pubblicazione confermata dal fornitore:
`https://590e42aa.mykleos.pages.dev`. Verifiche HTTP dopo reindirizzamento:
200 sia sull'anteprima sia su `metnos.com`; il nuovo paragrafo backend è
stato riletto dal dominio pubblico in IT e EN. Cloudflare resta strumento privato di
sviluppo/documentazione, non componente dell'installazione pubblica. Nessun
push GitHub eseguito.

## Abilitazione successiva di LRE su richiesta esplicita

Dopo la chiusura del rilascio Roberto ha chiesto «attiva lre». Il comando
amministrativo ufficiale `POST /admin/services/durable_workloads/feature/enable`
è stato eseguito dopo la verifica di inattività dello stack, senza modificare
direttamente unità o configurazione e senza riavviare HTTP o gli altri servizi.
La procedura ha persistito l'abilitazione e riavviato il solo lavoratore.
Accettazione alle 08:55:15 UTC, evidenza
`/var/lib/metnos-admin/agent-runs/run-9hxcx8cd`.

Alle **08:56:11 UTC** LRE è effettivamente `enabled=true`, `state=ready`,
`worker_available=true`, presenza aggiornata e nessun motivo di errore;
Metnos resta operativo. Evidenza
`/var/lib/metnos-admin/agent-runs/run-i5lv3r8k`. PID lavoratore 400369,
un thread, zero riavvii anomali. La configurazione persistente ora ha impronta
`24b0ec70d4bce174a838d86c3dfd3a9e6e30c37731354df776223f5eb3f382e4`.

L'abilitazione non equivale alla ripresa forzata della reindicizzazione:
il lavoro foto rimane `needs_attention` con identica impronta
`64f069dafa2f1adb942d8ea2b809cefcbd51c59d9dad9f35925f4843a3907468`.
Inventario lavori: 3 conclusi, 2 falliti storici, 1 da verificare; nessun
lavoro eseguibile in coda. Budget e consumi sconosciuti non sono stati alterati,
nessun ritentativo o ripristino è stato richiesto. Rimane esclusa una prova
di carico attivo; il Tutor non è stato modificato.

Controllo di stabilità alle **08:57:16 UTC**, evidenza
`/var/lib/metnos-admin/agent-runs/run-f50y_vtw`: stesso PID, un thread,
nessun riavvio, motore pronto e presenza aggiornata, foto sempre invariata.
Nei circa 64,66 secondi dall'osservazione precedente, CPU cumulativa da
28.620.196.000 a 28.662.072.000 ns: circa **0,065% di un core** a motore
abilitato senza lavori eseguibili. Il consumo di avvio è escluso da questa
misura; non è una prova prestazionale della reindicizzazione.
