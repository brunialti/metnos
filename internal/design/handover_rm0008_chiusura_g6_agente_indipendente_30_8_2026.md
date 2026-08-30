# RM-0008 — handover indipendente per la chiusura di G6

> **MANDATO ESAURITO — G6 e' chiuso il 31 agosto 2026.** Questo documento
> descrive uno stato SUPERATO e va letto come storia, non come consegna: chi lo
> apre per iniziare un lavoro sta partendo dal punto sbagliato.
>
> Esito, con le misure in roadmap §23.32: sette condizioni su sette della
> Definition of Done del §9; suite a copertura totale con **zero regressioni**
> (74 rossi alla base pre-G6, 69 all'albero finale, 5 risolti, 9337 verdi);
> **quattro cicli pubblici verdi consecutivi** su quattro teste diverse
> (`b02ce3e`, `342f869`, `00224c3`, `e492d51`), nove lavori su nove ciascuno;
> cella dal vivo 6 prove su 6 in 373 s.
>
> Il §4 di questo documento dice che C3 e' aperto e il §10 propone un ordine di
> lavoro: entrambi sono stati eseguiti. La catena di dodici cause che ha portato
> C3 al verde e' in roadmap §23.20-§23.29, e comprende una diagnosi mia
> sbagliata (§23.20), conservata perche' l'errore e' istruttivo. Il ciclo
> avversariale che ha certificato il verbale — quattro giri Codex e quattro
> Claude, convergenti — sta in
> `handover_rm0008_g6_revisione_avversariale_30_8_2026.md`.
>
> **Dove si riprende, invece che da qui**: RM-0008 resta aperto per F4-F6, e il
> gruppo 7 ha fatto il solo primo passo (roadmap §23.31). Il resto del gruppo 7
> — attivazione reale con `closed_build_enforcement()=True`, installazione dei
> nomi definitivi, commutazione dei servizi correnti — non e' iniziato, e
> comprende il residuo dichiarato dei percorsi di scrittura dei dati di
> `metnos-telegram-daemon.service`.


> Consegna del 30 agosto 2026. Questo documento e' autosufficiente e destina
> la chiusura del gruppo 6 a un agente esterno indipendente. Lo stato descritto
> e' quello del worktree `/tmp/metnos-rm0008-a-only`, ramo `main`.

## 1. Mandato

Chiudere esclusivamente G6 di RM-0008 con un percorso performante, senza
duplicazioni, robusto e probante. Usare commit e push incrementali. La suite a
copertura totale va eseguita una sola volta, come verifica finale prima della
chiusura del gruppo; durante gli incrementi usare soltanto matrici mirate.

L'agente deve lavorare nel worktree indicato sopra. Non deve modificare
`/opt/metnos`, riavviare servizi gestiti o usare l'archivio contratti operativo.
Le prove che richiedono `root` e systemd reale devono girare soltanto nella VM
GitHub-hosted usa-e-getta gia' prevista dal workflow congelato.

La proiezione pubblica va eseguita esclusivamente con:

```text
METNOS_VENV=/opt/metnos/.venv scripts/publish-public.sh --incremental -m "..."
```

Non usare `git push origin main`: in questo worktree `origin` non e' GitHub.

## 2. Fonti normative da leggere prima di modificare codice

1. `internal/reports/rm0008-gruppo6-piano-ottimizzato.md`, in particolare
   §§ 3.5.4, 4, 5.2-5.4 e 6.2-6.4.
2. `internal/roadmap/RM-0008-porta-unica-nascita-executor.md`, soprattutto
   §§ 23.2 e 23.13-23.14.
3. `internal/design/handover_rm0008_gruppo6.md`, che conserva le decisioni e
   le certificazioni degli incrementi precedenti.
4. Questo documento, che prevale soltanto per lo stato temporale e l'ordine
   residuo; non amplia l'autorita' definita dal piano.

## 3. Stato esatto alla consegna

G6-A e' chiuso. G6-B1, B2 e B3 sono chiusi; G6-B4 resta da completare. G6-C1
e C2 sono certificati. C3 e' implementato, pubblicato e in certificazione
reale. C4 non e' ancora implementato. G6-D resta da completare.

Ultimi commit privati rilevanti:

```text
bb8301b3 fix(rm0008): align G6-C with live systemd relations
92fc63a1 test(rm0008): close public G6-C activation fixture
f5b8845f test(rm0008): execute signed G6-C activation cell
e3a19aed build(rm0008): pin gated launch source profiles
4a39654c feat(rm0008): launch gated services from signed plans
8dd95049 build(rm0008): pin autonomous check source profiles
35653d58 feat(rm0008): activate autonomous preflight checks
```

La proiezione pubblica e' avanzata dopo la consegna. Il run GitHub Actions
`33323763867` ha concluso verdi le sei attivita' possedute 2A, la suite
generale Windows e tutte le altre celle Linux; il solo errore e' la cella C3
systemd reale, lavoro `99290172651`.

La causa misurata e' circoscritta: la fotografia systemd restituisce
`infinity` a una direttiva di durata e
`normalize_systemd_duration_usec_v1()` la invia al parser dei componenti
numerici, che risponde `PreflightError: duration component`. Correggere e
provare soltanto questa semantica documentata prima di ripetere la cella.

Profilo sorgenti corrente:

```text
privato: 686 / sha256:0fd096b3bc0ea9c7170221eafe2efde06f511a0d3b0983947aa7712a1308ceff
pubblico: 674 / sha256:b142384fc396ea3cd0093b7a1895b4820c5724b56c3ef82cd10673a5be79b0ab
export: 1607 file, zero PII, zero segreti, zero file sensibili
```

La matrice mirata piu' recente ha concluso con `211 passed, 1 skipped`; il
salto e' la cella systemd reale opt-in. Il controllo `--birth-closed`, il
controllo del diff e il gate di esportazione sono verdi.

## 4. C3: cosa e' gia' provato e cosa manca

La cella `tests/portable/test_executor_birth_systemd_activation.py` costruisce
una distribuzione completa firmata, installa programma amministrativo e due
unita' isolate, acquisisce la fotografia TCB/systemd e costruisce il grafo
ownership canonico. Prima del prerequisito prova il diniego sia dell'avvio
diretto sia del timer reale. Dopo il prerequisito prova:

- diniego di `check` e `launch` invocati dall'identita' applicativa;
- ammissione causale attraverso il timer reale;
- UID, GID, gruppi, ambiente, working directory e argv firmati;
- soli descrittori 0/1/2, `NoNewPrivs=1` e capability tutte a zero;
- namespace mount vivo e metadati del marcatore;
- acquisizione immediata del gate startup esclusivo mentre il target e' vivo;
- rimozione circoscritta ai soli file firmati della cella.

Il primo run pubblico ha rilevato due errori della fixture, entrambi corretti:
import POSIX a raccolta Windows e dipendenza da un inventario privato escluso
dall'export. Il secondo run ha mostrato che systemd 255 non espone le proprieta'
nominali `References` e `ReferencedBy`. Il profilo e' stato allineato
all'interfaccia reale; restano obbligatorie, fra le altre, `Triggers`,
`TriggeredBy`, `Conflicts` e `ConflictedBy`.

Per chiudere C3 occorre soltanto:

1. verificare l'esito finale del run `33321931596`;
2. se rosso, correggere la sola causa osservata e ripetere il push incrementale;
3. se verde, registrare commit, run, matrici e profili nella roadmap e
   nell'handover storico, quindi pubblicare il checkpoint documentale.

## 5. C4: prova relazionale residua

Estendere la stessa cella usa-e-getta; non creare un secondo harness.

Baseline positiva:

1. dopo l'ammissione C3, eseguire il `check-all` installato con la fotografia
   firmata invariata e pretendere successo;
2. verificare nella fotografia canonica che il timer contenga
   `Triggers=<servizio>` e che il servizio contenga
   `TriggeredBy=<timer>`;
3. verificare che entrambi gli archi partecipino all'hash effettivo.

Unico caso differenziale:

1. installare un'unita' ausiliaria root-owned, confinata allo stesso namespace
   casuale, con `Conflicts=<servizio candidato>`;
2. rileggere i byte e i metadati dell'unita', quindi eseguire
   `daemon-reload`;
3. provare che `ConflictedBy` compare sul candidato, che la nuova origine e la
   fotografia effettiva cambiano e che il successivo `check-all` nega senza
   pubblicare una nuova attestazione;
4. nel `finally`, fermare e rimuovere soltanto l'unita' ausiliaria esatta e
   ricaricare il manager.

Non aggiungere una matrice per ogni relazione: il piano richiede una positiva
timer e un solo differenziale `Conflicts`.

## 6. G6-B4: transazione residua

Implementare il nucleo di pubblicazione della release gia' preparata da B3,
senza creare autorita' produttiva anticipata. Il nucleo deve consumare una
capability nominale privata, verificare nuovamente record, descrittore,
manifesto, firma, sorgente ricevuta, staging e sessione di deployment, quindi
pubblicare il nome finale con no-replace e rilettura completa.

Criteri minimi:

- una sorgente ricevuta e uno staging completi producono una release finale
  byte-identica e un record installato autenticato;
- una ripetizione esatta e' idempotente;
- collisione, staging parziale, byte o metadati cambiati, sessione scaduta,
  piattaforma non supportata e record non nominale negano prima della
  pubblicazione finale;
- nessuna API pubblica ottiene la capability B4;
- assenza dell'autorizzazione che verra' composta in G6-D produce zero I/O e
  fotografia invariata;
- Windows nega prima di consultare sessione, filesystem o autorita'.

Riutilizzare il medesimo harness di interruzione e ripresa di G6-B; non
duplicare codec, firma, ricezione, catena o matrici gia' certificate.

## 7. G6-D: composizione e recupero

Comporre un solo protocollo isolato che raggiunga, in ordine e con rilettura
autenticata, gli stati:

```text
PREPARED
RECEIPTS_COMPLETE
CERTIFICATE_PUBLISHED
BUILD_VERIFIED
HEAD_REQUIRED
PREFLIGHT_VERIFIED
```

Il percorso produttivo di gruppo 6 continua a negare prima di claim e
`PREPARED`; soltanto la seam isolata della VM puo' completare il ciclo. Claim,
prenotazione del successore, descrittore, transazione installata, predecessore,
ricevute, certificato, build, testa richiesta, prerequisito e attestazione
devono essere legati allo stesso `request_id` e riletti a ogni avanzamento.

La ripresa deve essere provata a ogni confine durevole. Un nuovo processo deve
ricostruire la capability isolata dal documento root-owned della cella, dallo
stesso `boot_id` e dalle evidenze vive; non puo' ricevere booleani, percorsi o
descrittori aperti dal chiamante. Dopo il punto di non ritorno il recupero non
puo' cancellare certificato o testa richiesta, riaprire proprietari precedenti
o avviare il predecessore.

Il finale deve eseguire nuovamente il preflight definitivo sulla topologia
effettiva invariata, pubblicare l'attestazione e rileggere il record
`PREFLIGHT_VERIFIED`. Qualunque deriva di frame, build, testa, prerequisito,
unità o TCB deve negare.

## 8. Vincolo operativo emerso il 30 agosto

Una pubblicazione partita da un worktree ha raggiunto in precedenza l'archivio
contratti operativo condiviso. Il ripristino e' gia' avvenuto fuori da questo
ramo, ma la barriera strutturale e l'audit obbligatorio sono in lavorazione
separata mentre G6 e' sospeso.

L'agente G6 deve quindi:

- non invocare API di pubblicazione contratti con `store_root=None`;
- impostare per ogni prova uno stato temporaneo esplicito e verificare che non
  si sovrapponga ai percorsi operativi;
- non proporre una via legacy temporanea per riaprire `sign.py publish`;
- integrare, prima della chiusura finale, la barriera e le prove prodotte dal
  lavoro separato se esse toccano il profilo sorgenti o il confine statico.

## 9. Definition of Done di G6

G6 e' chiuso soltanto quando tutte le condizioni seguenti sono vere:

1. C3 e C4 sono verdi nella VM GitHub-hosted con systemd reale.
2. B4 e D hanno prove positive, negative, di idempotenza, concorrenza,
   interruzione e ripresa, senza nuova autorita' pubblica.
3. Il coordinatore isolato raggiunge e rilegge `PREFLIGHT_VERIFIED`; il percorso
   produttivo del gruppo 6 continua a negare prima di `PREPARED`.
4. Guard del confine, import closure, profilo sorgenti, export e piattaforme
   Linux/Windows sono verdi.
5. La barriera separata contro la pubblicazione dall'albero di sviluppo e la
   prova di audit sono integrate e verdi.
6. Solo a questo punto viene eseguita la suite a copertura totale; non devono
   esserci regressioni, skip o xfail nuovi non già dichiarati.
7. Roadmap e handover storico riportano commit privati, commit pubblici, run,
   conteggi, hash e stato `G6 complete`; RM-0008 resta aperto per F4-F6.

## 10. Ordine consigliato al nuovo agente

1. attesa e certificazione del run C3 corrente;
2. C4 nella cella esistente;
3. B4 con matrici mirate;
4. D e ripresa per ogni stato;
5. integrazione della barriera di pubblicazione separata;
6. matrice connessa, guard e gate export;
7. unica suite totale di chiusura;
8. checkpoint documentale e push pubblico incrementale finale.

Se una prova richiede di modificare il server gestito o l'archivio operativo,
il lavoro deve fermarsi: quella azione non appartiene al mandato G6.

## 11. Dipendenza operativa misurata dopo la consegna

Il materiale Birth predisposto sull'installazione esiste, ma il ramo corrente
rifiuta il caricamento con `birth_prepared_set_mismatch`: il contesto
autenticato appartiene a una distribuzione precedente. Il provisioner iniziale
non ha una transizione di upgrade e, a insieme presente, svolge solo
un'ispezione. G6-D deve quindi legare allo stesso `request_id` anche la
distribuzione aggiornata e rendere nuovamente leggibile il runtime Birth prima
di dichiarare `PREFLIGHT_VERIFIED`.

Non risolvere questa dipendenza con una firma diretta, con un nuovo file di
bootstrap scelto dal chiamante o sostituendo il marcatore predisposto. La
facciata di manutenzione builtin e' gia' ristretta e riprendibile:
`scripts/generate_builtin_executor_contracts.py --sign --only <nome>`; deve
diventare operativa soltanto dopo la convergenza autenticata di G6-D.
