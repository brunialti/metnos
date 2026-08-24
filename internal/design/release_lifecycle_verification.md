# Verifica interna del ciclo di vita delle release

**Stato:** specifica implementativa interna  
**Ambito:** installazione isolata, compatibilita' di aggiornamento e ritorno alla
versione precedente  
**Distribuzione:** esclusa dal repository pubblico; futura base verificata per
una funzione amministrativa pubblica separata

## 1. Scopo

Il componente deve rispondere in modo ripetibile a quattro domande:

1. una copia pubblicabile di Metnos parte da un ambiente vuoto e completa un
   turno reale;
2. il candidato sa usare dati persistenti creati dalla versione precedente;
3. la versione precedente torna a funzionare sugli stessi dati dopo il
   candidato;
4. l'istanza in esercizio non viene arrestata, modificata o usata come area di
   test.

Il componente e' un verificatore di release, non un secondo installer. Riusa
l'export pubblico, il firmatore, il loader e il server reali.

## 2. Confini

Il profilo iniziale e' **portabile e isolato**. Qui `portabile` descrive il
contratto del verificatore, non estende da solo le piattaforme supportate dal
prodotto:

- alberi baseline e candidato materializzati sotto una directory temporanea;
- virtualenv separati quando cambiano le dipendenze;
- home utente, config, dati, stato, workspace, chiavi e porte dedicate;
- avvio diretto del server, senza scrivere unit systemd;
- endpoint LLM esistente usato soltanto attraverso la configurazione runtime;
- nessuna credenziale di produzione copiata;
- produzione controllata prima e dopo solo tramite health e fotografia del
  worktree.

Il profilo portabile non certifica pacchetti di sistema, linger o policy
systemd. Questi appartengono a un successivo profilo gestito su utente/macchina
dedicata. Il rapporto deve dichiarare il profilo, senza promuovere un risultato
parziale a certificazione completa.

Il verificatore usa il layout nativo dei virtualenv, il percorso temporaneo e
il lock esclusivo del sistema operativo. Il workspace persistente viene passato
esplicitamente al runtime e non richiede collegamenti simbolici. Il rapporto
registra sistema, release, architettura e versione Python dell'host.

## 2.1 Matrice di supporto e ruolo

| Ruolo | Piattaforma | Stato | Evidenza richiesta |
|---|---|---|---|
| server Metnos | Linux | supportato | gate fresh/upgrade/rollback completo, due cicli consecutivi |
| client di dispositivo | Windows | supportato | installer, aggiornamento, identita', esecuzione e undo remoti |
| client di dispositivo | macOS | sperimentale | build manuale; nessuna sandbox nativa equivalente certificata |
| server Metnos | Windows o macOS | non supportato | escluso finche' installer, sandbox e gate host non sono deliberati e certificati |

Una prova del client Windows non vale come prova del server Windows. Allo
stesso modo, il fatto che il codice del verificatore eviti assunzioni POSIX non
costituisce certificazione di una piattaforma server aggiuntiva.

## 3. Modello futuro pubblico

Le responsabilita' sono intenzionalmente separate:

| Componente | Oggi | Futuro amministratore |
|---|---|---|
| sorgente release | export locale, directory o clone pubblico | release autenticata |
| staging | directory temporanea isolata | area di staging sul nodo |
| dati persistenti | layout sintetico con sonde hash | config, vault, dati e stato reali |
| transizione | baseline -> candidato -> baseline | aggiorna -> verifica -> ripristina |
| verifica | catalogo, health, turno reale | stessi controlli post-operazione |
| rapporto | JSON e Markdown interni | stato e diagnostica amministrativa |

La futura funzione pubblica non dovra' importare il comando interno. Dovra'
riusare il contratto e le funzioni pure promosse in un modulo pubblico dopo una
decisione separata, con autenticazione della release, consenso, backup e
controllo servizi.

## 4. Sequenza deterministica

1. acquisire un lock esclusivo;
2. fotografare health e worktree della produzione;
3. materializzare baseline e candidato;
4. validare struttura, path e link simbolici di entrambi;
5. preparare ambiente vuoto del candidato, firmare il catalogo e verificare
   import, catalogo, health e turno `final_kind=answer`;
6. preparare baseline con un secondo ambiente persistente e completare gli
   stessi controlli;
7. scrivere sonde casuali nei quattro domini persistenti, registrando soltanto
   hash e path relativi nel rapporto; creare inoltre una credenziale sintetica
   cifrata tramite l'API reale e registrarne soltanto il fingerprint;
8. avviare il candidato sugli stessi domini e ricontrollare sonde e runtime;
9. riavviare la baseline sugli stessi domini e ripetere i controlli;
10. verificare che produzione abbia lo stesso stato osservabile iniziale;
11. chiudere ogni processo, scrivere rapporto atomico e pulire il temporaneo,
    salvo richiesta esplicita di conservarlo.

Un fallimento interrompe la fase corrente ma esegue sempre chiusura processi,
verifica finale della produzione e scrittura del rapporto.

## 5. Criteri di esito

Il profilo portabile e' verde soltanto se:

- export/materializzazione e validazione sono verdi;
- tutti i manifest attivi compatibili con la piattaforma entrano nel catalogo;
- fresh, baseline, candidato e ritorno alla baseline superano health;
- ciascuna delle quattro esecuzioni produce un turno reale
  `final_kind=answer` con testo non vuoto;
- tutte le sonde persistenti conservano lo stesso hash;
- candidato e baseline dopo il rollback decifrano la credenziale sintetica e
  ne restituiscono lo stesso fingerprint;
- nessun processo di test resta vivo;
- health e fotografia del worktree di produzione non peggiorano;
- la pulizia termina oppure il path conservato viene dichiarato.

Il numero di executor non e' una soglia hardcoded: viene derivato dai manifest
attivi della release e confrontato con i nomi effettivamente caricati.

## 6. Rapporto

Il JSON e' la fonte macchina; il Markdown e' una vista. Entrambi contengono:

- versione dello schema, id esecuzione, profilo e tempi;
- piattaforma, release, architettura e versione Python dell'host;
- identita' non segreta delle due sorgenti;
- esito e durata di ogni fase;
- conteggio atteso/caricato/rifiutato del catalogo;
- status HTTP, `final_kind` e `turn_id`, mai il contenuto delle credenziali;
- hash delle sonde, non i valori;
- stato produzione prima/dopo;
- motivo stabile del primo fallimento e lista completa dei controlli eseguiti.

## 7. Non obiettivi iniziali

- aggiornare automaticamente una macchina utente;
- modificare o riavviare i servizi in esercizio;
- copiare vault, account o cookie reali;
- dichiarare certificata la parte systemd/hardware dal profilo portabile;
- pubblicare il verificatore nel repository GitHub pubblico.
