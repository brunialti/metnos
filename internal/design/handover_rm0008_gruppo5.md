# RM-0008 — passaggio di consegne del gruppo 5

## Stato corrente — 28/8/2026

Worktree: `/tmp/metnos-rm0008-a-only`, ramo unico `main`. Non toccare
`/opt/metnos` e non creare rami.

Il gruppo 4 e' chiuso con commit pubblico `a4d2dba` e ciclo GitHub
`33165938001`, nove lavori su nove verdi. Il commit sorgente di partenza e'
`9fd7edf4`. RM-0008 resta `active`; `closed_build_enforcement()` resta `False`.

G5-A e' chiuso nel repository sorgente dal commit `1791cec3`; G5-B dal commit
`efc00c06`. I due incrementi sono stati pubblicati insieme nel commit pubblico
`8a1f573`. Il piano completo e'
`internal/reports/rm0008-gruppo5-piano-ottimizzato.md`.

## Risultato dell'analisi e della revisione avversariale

La suddivisione in due incrementi e' confermata:

1. G5-A: tre autorita' e registri root-owned separati, cold load e correzione
   del registro multipurpose della catena;
2. G5-B: fabbrica sigillata di riattestazione, prova canonica completa e
   coordinatore recuperabile fino a `CERTIFICATE_PUBLISHED`.

Prima della codifica sono state delimitate cinque lacune reali:

- `_assemble_reattestation_core()` legge il campo inesistente
  `birth.publisher_options`;
- la richiesta produttiva permette al chiamante di scegliere la
  `ProducerReceipt`;
- le tre autorita' non hanno ancora formati o loader installati;
- `OwnershipChainStore` usa un solo registro per cutover e testa;
- `prove_stack_stopped()` perde due campi necessari alla prova canonica e non
  certifica da solo l'insieme completo.

Le unita' installate non hanno ancora il controllo preliminare ownership
dominante. Il coordinatore del gruppo 5 deve quindi rifiutare il punto di non
ritorno sul sistema produttivo finche' il gruppo 6 non consegna una attestazione
sigillata di quel prerequisito. Il gruppo 5 prova il recupero oltre il punto di
non ritorno soltanto in un ambiente isolato e non dichiara un cutover installato.

## Stato dell'incremento G5-A

G5-A e' implementato e committato su `main` come `1791cec3`. Comprende:

- codec e cold loader di tre registri a scopo singolo;
- tre chiavi private separate e controllo sui byte pubblici contro autore,
  Admission e tutti i Producer Birth;
- provisioner recuperabile con file esclusivi, checkpoint monotoni, fsync,
  pubblicazione finale e nuova apertura a freddo;
- due registri obbligatori e disgiunti in `OwnershipChainStore`;
- proprieta' `root:root`, modalita' e diniego dell'identita' di servizio nella
  cella Linux delegata gia' esistente;
- cinque nuovi store owner compilati nella guardia e nell'inventario.

Le prove locali correnti sono: 67 prove autorita'/catena verdi; 77 regressioni
dei soli confini attraversati verdi e 4 prove Linux reali non applicabili nella
sessione locale non delegata. Guardia normale e chiusa sono entrambe a zero e
il rendering e' byte-identico all'inventario. La matrice completa non viene
ripetuta ora: verra' eseguita una volta sola dopo G5-B, alla chiusura del gruppo.

La revisione avversariale del diff G5-A e' conclusa e non rileva blocchi
residui. La matrice pubblica completa non e' stata eseguita: resta unica e
appartiene alla chiusura congiunta di G5-A e G5-B.

L'ultimo giro ha corretto due rilievi circoscritti: la fixture Windows non
invoca piu' il provisioner Linux e il codec rifiuta tipi numerici JSON o Base64
non canonici. I riproduttori del revisore sono ora tutti rifiutati; il verdetto
finale e' `APPROVATO`.

## Stato dell'incremento G5-B

L'implementazione corrente comprende:

- un solo elenco ordinato delle unita' di manutenzione e rifiuto esplicito di
  `load_state` diverso da `loaded`;
- una porta nominale sigillata del publisher e una fabbrica di riattestazione
  il cui unico ingresso produttivo e' `CurrentGeneration`;
- emissione e acquisizione atomiche della ricevuta Producer, rinnovo della
  concessione e ripresa idempotente della stessa richiesta scaduta;
- acquisizione autenticata della corrente con firma separata e confronto dei
  byte esatti di manifest, lingua e codice;
- preparazione della prova completa delle ricevute senza chiudere i proprietari
  precedenti;
- coordinatore durevole con blocco di deployment esterno, record immutabili e
  stati `PREPARED`, `RECEIPTS_COMPLETE`, `CERTIFICATE_READY` e
  `CERTIFICATE_PUBLISHED`;
- arresto produttivo a `RECEIPTS_COMPLETE`; il passaggio successivo richiede un
  prerequisito sigillato che il gruppo 5 non puo' costruire;
- recupero in avanti da morte reale del processo dopo READY, firma, payload o
  rilettura, senza cancellazione e senza ritorno alla build precedente.

La revisione avversariale di G5-B ha inizialmente respinto l'incremento con
cinque P1 e un P2 riprodotti. Le correzioni correnti sono causali:

- un processo nuovo esegue il bootstrap Birth sotto il blocco di deployment;
- un replay da `RECEIPTS_COMPLETE` ricostruisce prova corrente e manutenzione e
  rifiuta qualsiasi variazione;
- prima di `CERTIFICATE_READY` viene richiesta una nuova prova di manutenzione
  identica, mentre il recupero successivo al READY non dipende da una nuova
  fotografia storica;
- il journal recupera deterministicamente un solo temporaneo nominale completo
  o parziale e rifiuta gli altri stati;
- i temporanei parziali del certificato vengono ricostruiti solo se sono un
  prefisso esatto dei byte attesi e solo prima della pubblicazione finale;
- il test di morte e ripresa usa un secondo interprete e ricarica da disco; la
  distribuzione del test e' prodotta dal verificatore reale, non da un oggetto
  costruito dalla giuntura privata.

Il secondo giro avversariale ha rieseguito i sei riproduttori e ha approvato
l'incremento con `P1=0` e `P2=0`. Ha inoltre confermato che, dopo
`CERTIFICATE_READY`, non va richiesta una nuova decisione di manutenzione: il
record ha gia' legato prerequisito e digest, quindi la ripresa deve soltanto
verificare catena e artefatti e avanzare in avanti.

Evidenze locali gia' verdi:

- 113 prove mirate del publisher, ricevute, riattestazione, bootstrap,
  manutenzione e integrazione, con una prova non applicabile;
- 90 prove della base runtime e 19 prove delle fotografie autenticate;
- 16 prove del cutover ownership, 9 del cutover runtime e 10 del coordinatore;
- 21 prove verdi nell'ultimo controllo congiunto di coordinatore, concorrenza,
  ripresa e mancata chiusura dei proprietari precedenti;
- 15 prove del coordinatore verdi dopo le correzioni avversariali, incluse
  deriva al replay, temporanei e ripresa in un secondo interprete;
- 155 prove mirate congiunte verdi e una prova Linux privilegiata non
  applicabile; il revisore ha rieseguito 41 prove di rischio senza salti;
- guardia normale e guardia chiusa entrambe senza rilievi.

Verifica finale locale congelata: suite portatile `391 passed, 24 skipped`,
R1 verde, inventario Python di 1812 percorsi coerente, guardia normale e chiusa
verdi e rendering dell'inventario byte-identico. I 24 salti sono le celle
realmente specifiche di piattaforma o privilegio e verranno eseguite dalla
matrice pubblica Linux/Windows; non sono errori ignorati.

## Correzione causale della prima matrice pubblica

Il ciclo pubblico `33180437885` sul commit `8a1f573` ha concluso verdi sette
degli otto lavori primari. Il solo lavoro portatile Windows ha rilevato nove
errori; il riepilogo e' rimasto rosso per conseguenza. Il log completo del
lavoro `98880002519` ha permesso di separare due cause, senza eseguire
correzioni per tentativi:

- otto errori provenivano da `_ensure_directory()`, che applicava i bit di
  modalita' e proprietario POSIX alle directory temporanee Windows;
- un errore proveniva dalla fotografia privata del candidato, che confrontava
  l'identita' restituita da `lstat()` con quella restituita dal descrittore CRT.
  In Python 3.12 su Windows quei campi non costituiscono un'identita'
  interoperabile fra le due API, anche quando il file non e' cambiato.

La correzione non elimina i controlli. Il controllo di modo e proprietario e'
ora eseguito soltanto sui sistemi POSIX. La lettura Windows usa invece un unico
oracolo Win32 di basso livello, privo di dipendenze da firma o pubblicazione:
apre senza condivisione di scrittura, cancellazione o rinomina, rifiuta reparse
point, directory, cancellazione pendente e hard link, verifica percorso finale,
dimensione e identita' due volte sullo stesso handle. Il modulo della fotografia
continua inoltre a confrontare percorso e componenti prima e dopo la lettura.

Evidenze locali dopo la correzione: 246 prove pertinenti verdi e due salti di
piattaforma, tutte le sette prove manifesto/R1 verdi, guardia normale e guardia
chiusa senza rilievi, inventario Python aggiornato a 1813 percorsi e rendering
della guardia byte-identico. La revisione indipendente del candidato preparato
ha inizialmente individuato e fatto correggere un collegamento incompleto degli
helper condivisi; il secondo giro ha concluso `P1=0`, `P2=0` e 49 prove
pertinenti verdi. La prova decisiva resta il nuovo lavoro Windows pubblico; il
gruppo 6 non e' iniziato.

Questi conteggi descrivono esecuzioni mirate parzialmente sovrapposte e non
vanno sommati. La suite finale portatile deve essere eseguita una sola volta.

## Prossimo passo unico

Registrare su `main` la correzione causale Windows, eseguire una pubblicazione
incrementale e richiedere tutti i lavori Linux/Windows verdi. Non avviare il
gruppo 6.

## Regole operative

- commit piccoli soltanto su `main`, con footer
  `RM-0008-Status: candidate-not-certified`;
- applicare a ogni incremento il budget minimo sufficiente definito in
  `internal/reports/rm0008-regole-di-lavoro-fra-gruppi.md` §8: una sola famiglia
  di prove per rischio distinto, nessuna ripetizione delle suite gia'
  certificate e matrice completa soltanto alla chiusura;
- una sola pubblicazione pubblica alla chiusura locale del gruppo;
- nessuna correzione successiva senza una nuova diagnosi se una prova fallisce;
- aggiornare questo file dopo ogni nuova evidenza, commit e risultato pubblico;
- non iniziare il gruppo 6, F5 o F6 prima della chiusura pubblica del gruppo 5.
