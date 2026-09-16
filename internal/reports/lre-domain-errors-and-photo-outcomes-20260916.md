# LRE: traccia degli errori ed esiti foto — 16 settembre 2026

Stato aggiornato: **pubblicato in produzione con la release 62**. Il collaudo
reale limitato e la successiva pulizia richiesta dall'utente sono documentati in
`lre-outcomes-production-20260916.md`.

Il resto di questo documento descrive la chiusura dello sviluppo, precedente
alla pubblicazione: i riferimenti a produzione non aggiornata sono storici.
Base: `a590f564`, successiva all'audit multidimensionale LRE.
Nessun rilancio di lavori reali, riavvio del servizio, modifica degli indici di
esercizio o cancellazione di foto/storico durante questo intervento.

## Richieste recepite

- Foto non decodificabili identificate con una descrizione standard e un codice
  stabile per poterle individuare e riprovare.
- Regola interna al dominio di indicizzazione, non al motore LRE generalista.
- Prefisso inglese fisso fuori dal sistema i18n; spiegazione nella lingua corrente.
- A lavoro concluso, console con traccia persistente degli errori incontrati:
  elementi interessati, codici e conteggi; distinti dai tentativi tecnici.

## Contratto del motore generale

Un produttore approvato può restituire `domain_outcome` versione 1 con
`error_counts`. La proprietà deve essere esplicitamente ammessa nello schema
congelato; non basta uno schema aperto. Forma, codici, cardinalità e conteggi sono
validati; ricevuta legata al digest del risultato. LRE non contiene prefissi,
decoder o categorie fotografiche.

La stessa proiezione viene scritta atomicamente con la conferma o il riuso del
risultato. Il riepilogo legge solo unità confermate dell'utente e della revisione
corrente; ripetere la conferma non incrementa contatori. Le fasi successive non
riemettono gli errori dei predecessori. Ogni elemento originario ha un solo codice
primario: l'identità dell'elemento e il rispetto di questa regola sono responsabilità
del produttore approvato, non inferibili dal motore da soli contatori aggregati.

La console separa tre fatti:

1. `domain_errors.nitems`: quanti elementi hanno un esito negativo; categorie con
   i rispettivi conteggi. Totale calcolato prima del limite di venti categorie.
2. `error_categories`: problemi attuali delle unità/materializzazione; possono
   scomparire quando sono realmente risolti.
3. `attempt_errors.nattempts`: storico dei tentativi con errore strutturato,
   compresi quelli seguiti da successo. Pannello richiudibile, codici senza testo
   libero, conteggi separati dalle foto e dagli altri elementi.

La traccia resta dopo completamento, riapertura del database e ricarica della
pagina, finché lo storico viene conservato. Non vengono ricostruiti contatori
di dominio mancanti nei vecchi lavori partendo dai messaggi d'errore.

Esiti negativi di dominio comportano `completed_with_errors` soltanto dopo tutte
le normali verifiche di copertura, dipendenze, artefatti e consumi. Non autorizzano
risultati parziali impliciti o consumi sconosciuti. Errori tecnici poi recuperati
rimangono nella storia senza trasformare un lavoro riuscito in fallimento.

## Comportamento dell'indicizzazione foto

Solo i due esiti espliciti del decoder, `image_decode_failed` e
`image_format_unreadable`, diventano record `indexing_status=not_indexed`.
La descrizione comincia con `IMAGE_NOT_INDEXED:<codice>`; seguono motivo e limiti
della diagnosi. Non si afferma che il file sia corrotto senza prove.

Il record mantiene identità e percorso della sorgente, non inventa contenuti,
volti, parole chiave o vettori. La decodifica precede l'acquisizione dei modelli:
questi esiti non producono chiamate ai modelli. Gli altri errori continuano a
essere gestiti secondo il contratto; nessuna cattura generale per saltare file.

La pubblicazione verifica che ogni sorgente sia rappresentata una volta e che
`n_entries = n_indexed + n_not_indexed`, con istogramma degli errori coerente.
Cinque file sigillati; matrice vuota valida per un registro interamente negativo,
ma nessuna pretesa di aver indicizzato visivamente le foto. `ok_count` e
`fail_count` restano separati; solo l'analisi emette la ricevuta generale LRE.

La ricerca esatta `IMAGE_NOT_INDEXED` o uno dei due prefissi completi produce un
elenco diagnostico senza modelli, allegati immagine o indicizzazione automatica
di un corpus mancante. La spiegazione è resa nella lingua corrente. Ricerche
normali e vettoriali escludono quei record; la consultazione dell'indice espone
separatamente totale record, foto indicizzate e non indicizzate.

Nella stessa generazione i salvataggi negativi verificati sono riusabili. Un nuovo
aggiornamento incrementale riprova quei file, riusando i successi compatibili.
Non nasce un ciclo automatico di tentativi. I due strumenti manuali storici di
completamento vettori/contesto rifiutano record negativi e generazioni sigillate
prima di caricare modelli o scrivere: aggiornamento mediante il percorso normale.

## Prove e revisione

- Nucleo/indicizzazione: suite integrata con contesa ampliata, sorgenti
  sintetiche, modelli simulati e database isolati.
- Persistenza: job concluso riaperto, trenta categorie oltre il limite visivo,
  conferma ripetuta, riuso, isolamento utente/revisione e tentativi recuperati.
- Foto: corpus misto e interamente non leggibile, entrambi i codici, zero modelli
  sui record negativi, pubblicazione/ripresa, ricerca diagnostica e normale.
- Console: JavaScript reale, API e Chromium isolato in IT/EN, desktop/mobile,
  lavoro concluso con errori, lavoro riuscito con errori recuperati e page reload.
- Contratti/cataloghi: digest e stato linguistico canonico dei tre executor,
  manifest, risorse i18n, guide e compilazione delle unità Tutor.
- Due revisori indipendenti: nucleo/recupero e consumatori/sicurezza, con verifica
  incrociata. Le rispettive prove si sovrappongono: non sommare i loro conteggi.

Una prova documentale già non allineata nella base attendeva 24 executor
annullabili e 13 non annullabili. Il censimento verificato sia in `HEAD` sia in
`HEAD~1` è 23/14: `create_images_indices` è già privo di inversa nel contratto
durevole. Aggiornata l'aspettativa, aggiungendo una verifica esplicita del suo
contratto; nessuna capacità di annullamento rimossa da questo intervento.
Accorciata anche la descrizione iniziale italiana entro il limite canonico.

I risultati numerici conclusivi sono riportati nella chiusura in fondo.

## Compatibilità e rilascio ancora da eseguire

Gli schemi di scoperta, parti e pubblicazione foto passano a `/3`. Non riscrivere
i contratti dei lavori già ammessi. Anche le variazioni dei digest delle capacità
del precedente audit devono essere incluse nella verifica di compatibilità.
Gli indici precedenti restano leggibili; la ripetizione rapida della pubblicazione
richiede sigilli e nuovi contatori coerenti. Non trasferire implicitamente parti
private di vecchie generazioni nei nuovi lavori.

Prima dell'esercizio: release canonica chiusa con attestazione, prova reale
limitata mista, verifica dei contatori e della ricerca, poi eventuale nuovo lavoro
compatibile. Non firmare manualmente, non copiare file nella release installata,
non riavviare soltanto per caricare il codice. Nessuno di questi passi è stato
eseguito qui. Foto e storico reali restano intatti.

Queste prove non certificano la copertura delle oltre 30.000 foto reali, la causa
del precedente errore di decodifica o la durabilità dopo mancanza di alimentazione.
La produzione mantiene la versione precedente finché la release non è applicata.

## Chiusura delle verifiche

- Suite finale nucleo/indicizzazione: **888 superate in 68,25 s**; le quattro prove
  di contesa ampliate sono abilitate con `METNOS_TEST_CONTENTION_SCALE=1`.
  **10 escluse** nel solo `test_find_images_small_corpus.py`: richiedono un percorso
  esplicito al modello BGE locale, non scaricato né sostituito per farle passare.
- Console e API: **16 superate in 11,80 s**, Chromium reale isolato IT/EN,
  compreso storico recuperato, apertura pannelli e ricarica del job concluso.
- Manifest, cataloghi, risorse linguistiche e Tutor: **141 superate in 2,39 s**.
- Totale delle tre suite disgiunte: **1.045 superate, 10 escluse**. Le esecuzioni
  intermedie e quelle dei revisori non si sommano a questo totale.
- Digest dei tre executor e companion linguistici canonici verificati offline;
  nessuna firma o pubblicazione runtime. Il seed contiene soltanto le sei nuove
  chiavi previste IT/EN; il prefisso macchina non è nel catalogo delle traduzioni.
- Documenti pubblici validi: 99 pagine IT/EN. Cataloghi generati allineati.
  Pubblicazione statica finale: `https://3c6c867a.mykleos.pages.dev`; guide IT/EN
  verificate via HTTP. Le istruzioni Cloudflare/Wrangler hanno guidato la sola
  pubblicazione della directory pubblica: niente rapporti interni, catalogo Tutor
  locale, codice runtime o riavvii. Cloudflare resta infrastruttura di sviluppo.
- `git diff --check` superato; nessun residuo delle prove individuato nella
  verifica dei processi. Il browser gestito dal servizio di esercizio non è
  un residuo delle prove ed è stato lasciato intatto.

Il rapporto operativo preesistente `lre-photo-analysis-stop-20260916.md` resta
fuori da questo insieme di modifiche. I nuovi esiti risolvono la scelta di
prodotto aperta nell'audit precedente, non retroattivamente gli indici reali.
