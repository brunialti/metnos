# RM-0008 — RM-VARIAZIONE-02: sovrapposizione fra unita' conservata e dominante

Data: 1 settembre 2026  
Stato: proposta A corretta per terzo giro di revisione incrociata
Unita': `RM-VARIAZIONE-02`

## 1. Fatto che impone la variazione

Il catalogo firmato produce 15 nomi di unita'. Il piano di conservazione
precedente contiene 39 legami. Sedici legami hanno lo stesso nome testuale di
una unita' dominante, ma quindici appartengono allo spazio utente e quindi non
occupano la destinazione di sistema. Una sola coppia occupa lo stesso nome e la
stessa destinazione:

```text
legacy-service-http-system / system / metnos-http.service
```

Il piano vigente le assegna `mask_system_unit`; il descrittore firmato assegna
alla nuova unita' `/etc/systemd/system/metnos-http.service`. Le due
postcondizioni sono incompatibili: una destinazione non puo' essere insieme un
collegamento a `/dev/null` e il frammento firmato dominante.

La misura in sola lettura conferma inoltre che la destinazione corrente e' un
file ordinario `0644`, non un collegamento, di proprieta' `root:root`. Modo e
proprieta' coincidono quindi con quelli richiesti dal nuovo frammento; la prima
stesura dichiarava il contrario ed era fattualmente errata. Il file non e'
comunque adottabile: i suoi byte e la sua identita' non sono quelli
dell'artefatto del descrittore firmato, mentre la postcondizione richiede
l'artefatto esatto e non un file che abbia soltanto metadati compatibili.

Lo stesso controllo in sola lettura ha misurato che l'unita' di sistema e'
inattiva e disabilitata, mentre `user/metnos-http.service` e' attiva e
abilitata. La transizione maschera proprio questo ingresso utente e avvia il
frammento dominante nello spazio di sistema: e' quindi un cambio esplicito di
ambito del servizio vivo, non una conseguenza incidentale del piano.

Il censimento e' ora riproducibile dal sorgente canonico che genera il catalogo:

```text
/opt/metnos/.venv/bin/python internal/tools/sonda_sovrapposizione_unita_rm0008.py
dominant unit names:       15
legacy bindings:           39
cross-scope homonyms:      16
same-destination overlaps: 1
collision: legacy-service-http-system / service-http / system_unit / system / metnos-http.service
RESULT: exact overlap confirmed
```

Il passaggio produttivo deve ripetere lo stesso censimento sul catalogo
firmato decodificato e pretendere identico risultato prima di applicare il
piano; il sorgente committato rende revisionabile la premessa prima che
l'artefatto installato esista.

## 2. Effetto operativo sottoposto alla decisione dell'autorita'

Questa proposta presenta per intero l'effetto sul servizio vivo che la
decisione dell'autorita' deve approvare dopo le due accettazioni tecniche. Il
verbale della decisione resta separato dalla proposta, come richiede il
protocollo: questo documento non attribuisce a B la verifica di messaggi
ricevuti da A e non presume l'esito della decisione.

La finestra di indisponibilita' comincia quando il piano ferma e maschera
l'unita' utente attiva e termina soltanto quando il nuovo servizio di sistema
supera il proprio cancello e viene osservato attivo. La checklist B4 deve
misurare e registrare entrambi gli estremi; non puo' dichiarare successo fra i
due. Un'interruzione nella finestra lascia gli ingressi precedenti chiusi e la
ripresa completa lo stesso passaggio. Dopo il punto di non ritorno, un ritorno
funzionale richiede una nuova epoca e non riabilita automaticamente l'unita'
utente.

## 3. Regola minima proposta

Il piano resta chiuso e senza azione di difetto, ma distingue il solo caso in
cui un legame di sistema occupa una destinazione del catalogo dominante
firmato:

1. un legame `user_unit/user` resta `mask_user_unit`, anche quando il testo del
   nome coincide con una nuova unita', perche' appartiene a un'altra radice;
2. un legame `system_unit/system` che non compare fra le unita' dominanti resta
   `mask_system_unit`;
3. un legame `system_unit/system` che compare esattamente una volta fra le
   unita' dominanti riceve `preserve_replaced_system_unit`;
4. script e moduli di repository restano `revoke_repository_entrypoint`;
5. una seconda sovrapposizione, una coppia sconosciuta o una copertura non
   completa arrestano il passaggio. Non nasce un comportamento generico di
   sostituzione.

`preserve_replaced_system_unit` rinomina senza sostituzione il file ordinario
precedente in un nome storico deterministico, nella stessa directory, e ne
registra identita', impronta, modalita' e proprietario osservati. Non lo
cancella, non lo modifica e non crea un collegamento al nome finale. Solo dopo
questa rilettura il pubblicatore firmato puo' occupare il nome libero con il
nuovo frammento root-owned `0644`.

Il piano viene derivato dai legami del catalogo e dall'insieme di nomi di unita'
dello stesso catalogo firmato. Il chiamante non passa un elenco indipendente.
L'impronta del piano continua a coprire `legacy_id`, `entry_id`, tipo, ambito,
locator e azione.

## 4. Ordine non permutabile

Sotto deployment lock, gate di avvio esclusivo e manutenzione viva:

1. rileggere il catalogo firmato e derivare il piano completo;
2. osservare l'assenza di processi in corso per ogni identita'
   `(scope, locator)`; il solo testo del locator non basta, perche' il nome HTTP
   esiste sia nello spazio utente sia in quello di sistema;
3. applicare e rileggere tutti i passi che non occupano una destinazione
   dominante;
4. conservare e rileggere l'unico frammento di sistema sostituito;
5. pubblicare e rileggere frammenti e collegamenti dominanti firmati;
6. ricaricare il gestore e osservare la topologia effettiva;
7. ripetere tutte le osservazioni, consumare la capacita' e soltanto allora
   pubblicare prerequisito e certificato;
8. richiedere la nuova testa senza mai ripristinare automaticamente il nome
   conservato.

L'ordine chiude il ritorno agli ingressi precedenti prima che la nuova testa
diventi richiesta. Se un'interruzione avviene dopo il punto 3, il sistema resta
fermo e la ripresa usa la distribuzione immutabile selezionata: non riabilita
gli ingressi precedenti per recuperare.

## 5. Ripresa e conflitti

La ripresa accetta soltanto quattro stati nominati:

- nome precedente presente, nome storico assente: puo' iniziare la
  conservazione;
- nome precedente assente, nome storico esatto presente: puo' pubblicare il
  frammento firmato;
- nome storico esatto e frammento firmato esatto presenti: passo gia'
  completato;
- qualunque altro contenuto, collegamento, doppia presenza non concordante o
  metadato mosso: conflitto, senza sostituzione.

Una seconda esecuzione produce lo stesso piano e le stesse identita'. Il
carattere `repeated` puo' cambiare nella ricevuta operativa, ma non cambia
l'impronta normativa del piano ne' la topologia osservata.

## 6. Prove minime aggiuntive

La barriera B2 aggiunge:

1. sonda committata e censimento sul catalogo firmato che provano entrambi 15
   unita', 39 legami, 16 omonimie fra spazi e una sola collisione di
   destinazione;
2. distinzione fra `user/metnos-http.service` e
   `system/metnos-http.service` nell'osservazione di attivita';
3. conservazione del file precedente byte per byte e pubblicazione del nuovo
   frammento allo stesso nome;
4. ripresa dopo conservazione e prima della pubblicazione;
5. ripetizione con storico e frammento finale gia' esatti;
6. rifiuto di storico occupato, frammento differente, collegamento e seconda
   sovrapposizione;
7. prova che nessun passo maschera il nuovo frammento;
8. prova di convergenza fra esecuzione intera e ogni interruzione durevole.

Le prove restano mirate. La suite completa gira una volta sola a B3.

## 7. Limiti

La proposta non autorizza modifiche a `/etc`, servizi, processi o stato vivo.
Non modifica i sette stati V2, il certificato, il selettore della testa o la
regola append-only della transizione di contesto. Il codice che dipende da
questa regola non inizia prima dell'accettazione incrociata e della decisione
dell'autorita' prevista dal protocollo.

RM0008-Unita: RM-VARIAZIONE-02  
RM0008-Ruolo: agente-a  
RM0008-Stato: PRONTA
RM0008-Ancora: 7aa688e9
RM0008-Percorsi: internal/design/rm0008_variazione_02_sovrapposizione_unita_1_9_2026.md; internal/tools/sonda_sovrapposizione_unita_rm0008.py; internal/roadmap/RM-0008-porta-unica-nascita-executor.md
RM0008-Prova: sonda deterministica committata; osservazioni in sola lettura di entrambe le unita'; decisione dell'autorita' separata dopo le due accettazioni
RM0008-Ambito: roadmap
