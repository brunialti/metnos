# RM-0008 gruppo 4 — piano ottimizzato per la chiusura statica F4

## 1. Stato di partenza verificato

Il gruppo 3 e' chiuso. La prova pubblica autorevole e' il ciclo GitHub Actions
`33154494801`, commit pubblico `57ec5a9`: otto lavori su otto verdi su Linux e
Windows. La correzione produttiva del confine Windows e' nel commit sorgente
`8485ca23`, pubblico `a04267b`, ciclo `33153843377`.

Il gruppo 4 corrisponde al §23.6.4 della roadmap: **chiusura statica F4**. Non
esegue ancora coordinatore, distribuzione, passaggio reale, F5 o F6.

La misura iniziale e' questo comando:

```text
/opt/metnos/.venv/bin/python runtime/contract_boundary_guard.py \
  --birth-closed \
  --inventory internal/reports/rm0007-m4-boundary-inventory.json
```

Risultato del 28/8/2026: 26 rilievi, divisi in modo esatto:

| Codice | Numero | Significato |
|---|---:|---|
| `birth_closed_legacy_authority` | 5 | installatore e generatore incorporato conservano chiamate di firma diretta |
| `unclassified_boundary_scope` | 2 | porta dei moduli ammessi e bootstrap sigillato non sono ancora classificati |
| `birth_closed_exception_invalid` | 16 | le eccezioni compilate e nominate non sono riportate nell'inventario |
| `birth_closed_inventory_invalid` | 1 | manca la politica chiusa esatta nell'inventario |
| `birth_closed_owner_invalid` | 1 | l'unico proprietario Birth non e' ancora marcato come tale |
| `stale_boundary_classification` | 1 | resta la vecchia voce `_build`, sostituita da `_build_sealed` |

Questa tabella e' la base. Non si modifica l'inventario per far sparire un
rilievo prima di aver rimosso o giustificato la capacita' produttiva sottostante.

## 2. Rivendicazione del gruppo

Una sola frase:

> Sullo stesso albero destinato alla distribuzione, ogni nascita o modifica di
> un executor raggiunge l'unico proprietario Birth; nessun percorso produttivo
> conserva firma diretta, caricamento di codice non autenticato o autorita'
> dinamica, e la guardia `--birth-closed` restituisce zero rilievi.

Il gruppo 4 **non** cambia ancora `closed_build_enforcement()` da `False` a
`True`. Il bit diventa vero soltanto nell'artefatto chiuso e nel passaggio reale
dei gruppi successivi, dopo coordinatore, distribuzione firmata e controllo
preliminare. Anticiparlo renderebbe inutilizzabile l'installazione.

## 3. Cose che non costituiscono una soluzione

- Aggiungere eccezioni a installatore o generatore per nascondere `sign`.
- Spostare `sign_executor` dietro una funzione con un altro nome.
- Far leggere a un executor il percorso di un fratello e fidarsi perche' e'
  incorporato nella distribuzione.
- Rigenerare l'inventario e accettare automaticamente i ruoli proposti.
- Cambiare il bit della politica chiusa prima che esista l'artefatto F4.
- Avviare F5 o F6 mentre la guardia statica e' ancora rossa.

## 4. Ordine non permutabile

Il gruppo usa tre incrementi causali. L'inventario viene congelato una sola
volta, alla fine del secondo incremento.

1. **G4-A — dipendenze fra executor autenticate.** Riparare
   `undo_last_turn` e `find_persons_indices`; provare alterazione, sandbox e
   percorso Birth reale.
2. **G4-B — rimozione delle cinque firme dirette.** Il nuovo impianto e il
   generatore consegnano intenzioni prive di autorita'; il bootstrap iniziale
   e' privato, sigillato e limitato allo stato precedente al certificato.
3. **G4-C — inventario chiuso e guardia a zero.** Classificare i fatti rimasti,
   controllare una per una le 16 eccezioni gia' compilate, congelare
   l'inventario e ottenere zero rilievi senza cambiare il bit F4.

Un incremento si pubblica soltanto quando simbolo produttivo, prova diretta e
prova del percorso reale sono nello stesso commit. Se la matrice pubblica e'
rossa, l'incremento successivo non comincia.

## 5. G4-A — prima fetta verticale

### 5.1 Difetti da eliminare

`undo_last_turn` usa `importlib.util.spec_from_file_location()` sul percorso
del modulo indicato dal catalogo. La firma del catalogo non viene confrontata
con i byte eseguiti.

`find_persons_indices` inserisce la cartella del fratello in `sys.path` e lo
importa. Il suo manifest firma soltanto `find_persons_indices.py`; quindi il
codice realmente eseguito di `find_images_indices` non appartiene al suo
snapshot.

Entrambi devono usare `runtime/admitted_module_v1.py`. La porta legge i byte
una volta, confronta il digest firmato e compila la stessa copia in memoria.

### 5.2 Modifiche produttive precise

1. `runtime/loader.py`
   - aggiungere a `Executor` il tuple immutabile `code_files`;
   - popolarlo dalla lista `[code].files` del manifest gia' autenticato;
   - non riaprire il manifest per ricostruire questa lista.
2. `runtime/admitted_module_v1.py`
   - ricevere da `Executor` `code_files`, `code_path`, `digest` e la radice
     della generazione gia' verificata;
   - validare di nuovo percorsi relativi, duplicati, collisioni di maiuscole e
     contenimento;
   - leggere ogni file con il controllo anti-link gia' presente;
   - confrontare il digest su tutti i file dichiarati;
   - eseguire soltanto il primo file, dagli stessi byte gia' verificati;
   - eliminare `_declared_code_files_v1()` e la rilettura TOML. Questo rimuove
     anche l'ambito non classificato `authoring_read` senza falsificarne il
     ruolo.
3. `executors/undo_last_turn/undo_last_turn.py`
   - applicare la sostanza della patch
     `internal/design/patch_undo_last_turn_porta_autenticata.diff`;
   - passare l'oggetto `Executor`, non `code_path`;
   - tradurre `AdmittedModuleError` nell'esito di annullamento gia' previsto,
     senza esporre percorsi o dettagli del digest.
4. `executors/find_persons_indices/find_persons_indices.py`
   - conservare il nome pubblico e la validazione degli argomenti;
   - ottenere `find_images_indices` dal catalogo verificato;
   - caricarlo tramite `load_admitted_module_v1()`;
   - invocare `module.invoke(forwarded)`;
   - rimuovere inserimento in `sys.path` e import del fratello;
   - in caso di catalogo assente, digest diverso o modulo non caricabile,
     restituire un rifiuto stabile e non eseguire il fratello.

### 5.3 Preparazione del candidato e pubblicazione

Modificare codice rende obsoleto il campo derivato `[code].digest`. Non e'
ammesso risolvere chiamando `sign_executor`.

La preparazione deve quindi separare due operazioni:

1. una trasformazione pura e deterministica aggiorna soltanto il digest
   derivato sui byte posseduti del candidato;
2. `submit_installer_birth(BirthIntent(...))` esegue controlli, firma e
   pubblicazione con le autorita' sigillate.

La trasformazione del digest va estratta da `runtime/sign.py` in un modulo
privo di chiavi e di capacita' di pubblicazione, riusato sia dal percorso
storico sia dalla preparazione Birth. Deve produrre una directory di staging
chiusa; non deve modificare in anticipo il sorgente vivo.

La prova installata deve partire da autorita' predisposte, presentare le due
revisioni tramite l'adattatore `installer_phase3`, rileggere le generazioni dal
solo archivio e verificare che i file autorevoli riconciliati nel sorgente
corrispondano ai byte pubblicati. Manifesto e firma da registrare nel repository
devono essere quelli prodotti da Birth; nessun test o comando richiama
`sign.py sign` o `sign.py publish`.

### 5.4 Prove possedute da G4-A

- `tests/runtime/executors/test_admitted_module.py`
  - lista file presa dal record autenticato;
  - percorso fuori radice, duplicato, link e collisione di maiuscole rifiutati;
  - alterazione dell'entrata o di un fratello rifiutata prima di `exec`;
  - byte autenticati e byte eseguiti sono la stessa copia.
- `tests/runtime/infra/test_undo_chokepoint.py`
  - giro completo di annullamento invariato;
  - codice alterato dopo il caricamento del catalogo non viene eseguito;
  - nessun uso di `spec_from_file_location` resta nell'executor.
- `tests/runtime/executors/test_executor_standard_index_readers.py`
  - `test_sandboxed_readers_reuse_logical_symlink_index_without_source_bind`
    diventa verde senza legare la sorgente del fratello nella sandbox.
- `tests/runtime/executors/test_executor_birth_shadow.py`
  - `test_the_closure_cost_on_the_real_executors_is_known_and_named` non deve
    piu' elencare eccezioni per questi due executor.
- nuova cella portatile del gruppo 4
  - pubblica entrambe le revisioni tramite una vera intenzione Birth;
  - ricarica dal solo archivio;
  - esercita entrambe le chiamate;
  - prova il rifiuto dopo alterazione.

### 5.5 Criterio di uscita G4-A

- zero caricamenti diretti da percorso nei due executor;
- le due celle sandbox prima rosse sono verdi;
- alterare il fratello autenticato non esegue alcun byte;
- le revisioni sono pubblicate da Birth, non firmate direttamente;
- R1 e le prove del gruppo 3 restano verdi;
- matrice pubblica Linux/Windows interamente verde.

## 6. G4-B — eliminazione delle firme dirette

### 6.1 Cinque rilievi da portare a zero

Tre rilievi sono in `install/phases/phase3_code.py`:

- `_sign_and_verify_legacy_contracts`;
- `_install_executor_contracts`;
- `run`.

Due sono in `scripts/generate_builtin_executor_contracts.py`:

- `<module>`;
- `main`.

### 6.2 Nuovo impianto

Il percorso di nuovo impianto deve rispettare quest'ordine:

1. verificare gli ingressi pubblici delle autorita';
2. creare o verificare la coppia autore storica;
3. completare la predisposizione Birth;
4. entrare nella barriera di quiescenza;
5. costruire internamente l'inventario esatto dei contratti installati;
6. preparare candidati chiusi aggiornando soltanto i campi derivati;
7. eseguire Birth contro un archivio ombra esplicito;
8. rileggere ogni generazione, ricevuta e firma;
9. persistere il rapporto prima dell'attivazione;
10. attivare l'archivio, ricaricare a freddo e installare il bundle produttivo.

Il bootstrap privato:

- vive nel confine sigillato Birth gia' autenticato;
- non accetta chiavi, produttori, percorsi o inventari dal chiamante;
- e' ammesso soltanto in stato `legacy`, sotto quiescenza e prima del
  certificato;
- usa un archivio ombra esplicito, quindi non richiede che l'archivio
  produttivo sia gia' attivo;
- non restituisce firme o capacita' riutilizzabili;
- dopo l'attivazione rifiuta ogni nuovo uso;
- non installa nel globale un bundle legato al percorso ombra: dopo il
  passaggio va costruito il bundle produttivo sul percorso attivo.

`phase3_code.py` orchestra soltanto e conserva la prova. Non importa
`sign_executor` e non possiede la firma.

Il generatore incorporato produce candidati. Con `--sign` deve consegnarli a
`submit_builtin_generation_birth`; il ramo che chiama `sign_executor` va
rimosso, non nascosto dietro una condizione di layout.

### 6.3 Prove possedute da G4-B

- impianto nuovo con zero, uno e molti contratti;
- arresto dopo preparazione, dopo ogni pubblicazione e dopo rapporto durevole;
- ripresa esatta senza nuova firma o doppia generazione;
- secondo avvio idempotente;
- bootstrap rifiutato in `active`, `store_only` ambiguo o dopo certificato;
- generatore incorporato: candidato rifiutato non muta archivio o sorgente;
- mutante che reintroduce `sign_executor` riporta esattamente i cinque rilievi;
- caricamento a freddo usa soltanto archivio e autorita' predisposte.

### 6.4 Criterio di uscita G4-B

- `birth_closed_legacy_authority`: 5 → 0;
- nessuna eccezione nuova per installatore o generatore;
- nuovo impianto e ripresa attraversano il percorso reale;
- il prodotto resta avviabile con `closed_build_enforcement() == False`;
- matrice pubblica Linux/Windows interamente verde.

## 7. G4-C — inventario e guardia

Solo dopo G4-A e G4-B:

1. rigenerare meccanicamente il candidato dell'inventario chiuso;
2. classificare `_build_sealed` come proprietario dell'inizializzazione dei
   due archivi durevoli, non come proprietario Birth;
3. classificare `birth_executor` come unico `birth_owner`;
4. eliminare la voce stale `_build`;
5. aggiungere il blocco `birth_closed` esattamente uguale alla politica
   compilata;
6. controllare una per una le 16 eccezioni compilate:
   - tredici di authoring offline devono restare prive di accesso al prodotto;
   - `localization_only` puo' soltanto pubblicare localizzazione;
   - `retirement_only` puo' soltanto ritirare;
   - ogni ambito deve esistere e usare davvero la capacita' giustificata;
7. aggiungere `closed_exception` soltanto dopo quel controllo;
8. eseguire guardia normale e `--birth-closed` sullo stesso albero tracciato.

Non si aggiorna l'inventario prima di `git add`: la guardia legge l'albero
tracciato. Non si cambia `closed_build_enforcement()`.

## 8. Sequenza minima delle verifiche

Per ogni incremento:

1. prove del file modificato;
2. R1 e mutanti della guardia;
3. prove del gruppo 4;
4. prove del gruppo 3;
5. manifesto 2A;
6. una sola pubblicazione incrementale;
7. matrice pubblica completa Linux/Windows.

La suite completa locale non viene rieseguita a ogni modifica. Si esegue alla
chiusura del gruppo o se una modifica attraversa un confine non posseduto dal
gruppo.

## 9. Regola di arresto

Se un incremento progettato fallisce la matrice pubblica:

1. nessuna seconda modifica immediata;
2. registrare lavoro, passo e codice esatto;
3. confrontare il punto col risultato previsto;
4. formulare una nuova causa che distingua almeno due soluzioni;
5. fare al massimo una misura discriminante;
6. aggiornare il passaggio di consegne prima di correggere.

## 10. Criterio di uscita del gruppo 4

| Requisito | Prova richiesta | Esito iniziale |
|---|---|---|
| due dipendenze fra executor autenticate | sandbox, alterazione, invocazione reale | non provato |
| nessuna firma diretta dell'installatore | nuovo impianto e ripresa | 3 rilievi |
| nessuna firma diretta del generatore | generazione e rifiuto atomico | 2 rilievi |
| unico proprietario Birth | inventario e guardia | proprietario mancante |
| eccezioni chiuse esatte | mutanti e controllo ambito per ambito | 16 non registrate |
| inventario corrente | guardia normale e chiusa | 2 non classificati, 1 stale |
| guardia chiusa | `--birth-closed` | 26 rilievi |
| politica runtime | controllo letterale e prova di avvio | deve restare `False` |
| portabilita' | matrice pubblica | da eseguire a ogni incremento |

Il gruppo 4 e' chiuso soltanto con **zero rilievi** della guardia chiusa e
matrice pubblica interamente verde. Questo non chiude F4 ne' RM-0008: autorizza
il gruppo successivo, che costruisce autorita' e coordinatore F4.

## 11. Passaggio di consegne obbligatorio

Dopo ogni evidenza o commit aggiornare
`internal/design/handover_rm0008_gruppo4.md` con:

- commit sorgente e pubblico;
- ciclo e lavori GitHub;
- conteggio dei rilievi per codice;
- prove eseguite e risultati;
- decisioni confermate o falsificate;
- file modificati e lavoro non committato;
- prossimo passo unico.
