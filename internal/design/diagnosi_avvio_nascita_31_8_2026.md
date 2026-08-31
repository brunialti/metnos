# Diagnosi — perché il codice RM-0008 non avvia il servizio in produzione

> 31 agosto 2026. Scritto per il ciclo avversariale del §6 della consegna
> `handover_ripresa_rm0008_31_8_2026.md`: **nessuna azione su `/opt/metnos`
> prima che queste inferenze siano state contestate.**
>
> Regola che lo impone, in una riga: *ogni volta che stai per toccare la
> produzione basandoti su un «quindi» invece che su un numero misurato.*
>
> Stato al momento della scrittura: produzione intatta (`14ea7179`, albero
> pulito, permessi invariati), servizi attivi, zero unità in errore, zero
> processi residui. **Tutte le misure qui sotto sono state fatte in copia.**

---

## 0. Sintesi in cinque righe

Gli ostacoli non sono due, sono **tre**. Il secondo non è quello che la consegna
diceva: è lo stesso bit di permesso del primo, applicato ai **file** invece che
alla directory. Il terzo non era noto e non è un permesso: **l'insieme preparato
descrive una distribuzione vecchia di un commit**, e il provisioner non ha alcun
percorso previsto per rifarlo. Rimossi tutti e tre, il cancello d'avvio arriva
in fondo in 0,9 secondi: **nessun quarto ostacolo nel bootstrap isolato; server
HTTP e turno reale non ancora provati** (misura C1, ancora aperta e bloccante).

**Che cosa questo documento NON autorizza.** Né una correzione in produzione né
la ricostruzione manuale della radice di nascita: il §5 spiega perché la scelta
«(A) o (B)» era una falsa alternativa e qual è invece il punto decisionale
vero.

---

## 1. Che cosa ho misurato, e con quale apparato

Questa sezione esiste per la trappola pagata due volte: *verificare CHE COSA si
sta misurando.* Ogni esecuzione stampa le radici che sta usando **prima** di
misurare, e il verdetto si legge solo insieme a quelle righe.

Apparato, tutto sotto `…/scratchpad/`:

| nome | che cos'è | perché |
|---|---|---|
| `replica/` | copia di `/tmp/metnos-prova-fusione` senza `.git` (143 MB) | è il codice che finirebbe in produzione |
| `cfg/birth/` | copia con `cp -a` della radice di nascita reale | permessi identici, ma mutabile senza danno |
| `state/`, `data/` | stato utente separato | il provisioner scrive: non deve scrivere sul vivo |
| `sonda.py` | esercita i cancelli uno per uno | isola quale cancello cede, e su quale file |

Radici dichiarate a ogni esecuzione:

```
METNOS_INSTALL_ROOT=<scratchpad>/replica
METNOS_USER_CONFIG=<scratchpad>/cfg
METNOS_USER_STATE=<scratchpad>/state
METNOS_USER_DATA=<scratchpad>/data
PYTHONPATH=<scratchpad>/replica
```

Interprete: `/opt/metnos/.venv/bin/python` — lo stesso del servizio. Con
`python3` di sistema manca `tomlkit` e la sonda muore prima di misurare.

Alla replica ho applicato il **modello di permessi della produzione**
(`chmod 775` sulla directory `runtime`, `664` sui file `.py`), perché
`/tmp/metnos-prova-fusione` nasce da git con la directory a `755` e non
riprodurrebbe il primo ostacolo.

**Verifica di non contaminazione, eseguita dopo tutte le misure:**
`git -C /opt/metnos status --porcelain` = 0 file; `/opt/metnos/runtime` ancora
`775`; `executor_standard.py` ancora `664`; radice di nascita reale con data di
modifica invariata; `metnos.target`, `metnos-http.service`,
`metnos-durable-worker.service` tutti `active`.

---

## 2. (a) Che cosa ho OSSERVATO — solo fatti

### O1 — Il cancello d'avvio diventa fatale perché il marcatore esiste

`require_birth_runtime_before_workers()` nel codice fuso decide così:

```
runtime/executor_birth_bootstrap.py:894
    if not birth_authority_is_prepared_v1(): -> avviso e ritorna
    bootstrap_birth_runtime()               -> ogni fallimento è fatale
```

e `birth_authority_is_prepared_v1()` (riga 863) risponde a **una sola domanda**:
esiste il file `~/.config/metnos/birth/prepared-v1.json`? Sì, creato il 30
agosto alle 13:17:25.

Il codice **oggi in produzione** tollera un'altra cosa: cerca
`birth/bootstrap.json`, che non esiste, e prosegue con un avviso
(`/opt/metnos/runtime/executor_birth_bootstrap.py:819-844`). Per questo la
produzione gira mentre l'insieme preparato è lì e non viene mai letto.

> **Fatto che cambia la lettura dell'incidente**: non è che il codice fuso
> «attiva qualcosa di nuovo». È che ha **cambiato il criterio di tolleranza**,
> da «manca il file di avvio» a «manca il marcatore» — e su questa macchina il
> marcatore c'è.

### O2 — Primo ostacolo: la directory `runtime` a `775` (riprodotto)

Sonda con `runtime` a `775`, file a `664`:

```
-- cancello 1: apertura radice di nascita preparata          ok
-- cancello 2: apertura della distribuzione (PATH_RUNTIME)   FALLITO
   PreparedRootError code=birth_provisioning_acl_unsafe
```

Coincide con la diagnosi già registrata: `_verify_posix_directory`
(`executor_birth_secure_fs.py:855-873`) rifiuta un ruolo `historical_public`
con `mode & 0o022`.

### O3 — Secondo ostacolo: i **file** del catalogo a `664` (riprodotto e isolato)

Stessa sonda dopo `chmod g-w replica/runtime` — **solo la directory**:

```
-- cancello 2: apertura della distribuzione                  ok
-- cancello 2b: lettura dei file del catalogo, uno per uno
   RIFIUTATO executor_standard.py              code=birth_provisioning_acl_unsafe  mode=664
   RIFIUTATO presentation_contract.py          code=birth_provisioning_acl_unsafe  mode=664
   … (tutte e 22 le letture rifiutate, tutte mode=664) …
   letti con successo: 0
-- cancello 3: read_prepared_set_v1
   PreparedRootError code=birth_provisioning_acl_unsafe
   causa interna: ContextMaterialError code=birth_provisioning_acl_unsafe
```

`ContextMaterialError` è **esattamente** l'errore che la consegna attribuiva al
modello dei ruoli. La sua unica sorgente possibile è la riga 178-184 di
`executor_birth_context_v1.py`: `sources.read_file(...)`. E la catena è chiusa
e leggibile: `open_distribution_sources_v1()` →
`_open_legacy_root_session(PATH_RUNTIME, exact_private=False)` → ruolo
`historical_public` → `_verify_posix_file` (riga 875-899) rifiuta
`mode & 0o022`.

Modi reali in produzione: 20 file su 20 presenti sono `0o664`; **316 file su
318** sotto `/opt/metnos/runtime` sono scrivibili dal gruppo. `umask` della
macchina: `0002`. Proprietario e gruppo: `roberto:roberto`.

### O4 — Terzo ostacolo, non registrato da nessuna parte: l'insieme è stantio

Portati i 20 file del catalogo a `644`:

```
-- cancello 2b: letti con successo: 22; primo rifiuto: None
-- cancello 3: read_prepared_set_v1
   PreparedSetError code=birth_prepared_set_mismatch
```

Confronto delle impronte registrate nel materiale contro tre alberi:

| albero | file che coincidono |
|---|---|
| `/opt/metnos` (produzione) | 11 su 20 (3 file nemmeno presenti) |
| `/tmp/metnos-prova-fusione` (fuso) | **19 su 20** |
| `/tmp/metnos-rm0008-g6` (lavoro) | **19 su 20** |

L'unico file discorde in **tutti e tre** gli alberi è `executor_standard.py`.
Il materiale ne registra 33175 byte, impronta `92fc64d4…`. Quella è la versione
del commit `3eeb4b1b` (28/8, 11:04). Gli alberi hanno la versione di
`cac6d7e4` (30/8, 18:48), 34334 byte.

Cronologia, e spiega tutto:

```
28/8 11:04  3eeb4b1b  executor_standard.py -> 33175 byte
30/8 13:17  l'insieme preparato viene creato e congela QUELLA versione
30/8 18:48  cac6d7e4  executor_standard.py -> 34334 byte (+23 righe)
```

Il cambiamento è ordinario e legittimo: 23 righe che validano
`paired_device_identity` nei manifest. **L'insieme preparato è diventato
stantio cinque ore e mezza dopo essere stato creato, per un commit normale.**

### O5 — Prova differenziale: quel file è l'unica causa del terzo ostacolo

Rimessa nella replica la versione `3eeb4b1b` di `executor_standard.py`, a parità
di tutto il resto:

```
-- cancello 3: read_prepared_set_v1   ok -> PreparedSetV1(set_id='e79b9b5c…', state='prepared_not_active', …)
```

### O6 — Rimossi i tre ostacoli, l'avvio arriva in fondo

```
ESITO: AVVIO COMPLETATO in 0.9s
```

`require_birth_runtime_before_workers()` completa — non un cancello, la
funzione che il server chiama a `metnos_http_server.py:366`. **Non c'è un quarto
ostacolo.**

### O7 — Il provisioner non rifà l'insieme

Eseguito `ensure_executor_birth_authorities_prepared()` sulla replica isolata,
con la distribuzione corrente e l'insieme stantio:

```
ESITO provisioner: already_installed
context_material_sha256 PRIMA = 6ce61fb5…   DOPO = 6ce61fb5…   CAMBIATO: False
```

Leggendo `_inspect_installed_v1` (riga 1439-1476) il motivo è chiaro: verifica
che marcatore, `set.json` e materiale **siano coerenti fra loro**, e non guarda
mai la distribuzione installata. È idempotente per presenza, non per contenuto.

### O8 — E non lo si può rifare togliendo il marcatore

Su una copia usa-e-getta, rimossi `prepared-v1.json` e `authority-sets/`,
conservate identità d'autore e ingressi operatore:

```
ESITO provisioner: FALLITO BirthProvisioningError code=birth_provisioning_recovery_ambiguous
```

Nessun oggetto creato, nessun residuo. La causa è
`_provision_prepared_authorities_v1` riga 1432-1433: radice d'autore presente +
marcatore assente + nessuna transazione = stato rifiutato.

### O9 — Che cosa è impegnato sull'identità d'autore corrente

`~/.local/state/metnos/birth/producer_receipts.sqlite`: 23 ricevute, un solo
emittente, tutte in stato terminale (21 `committed`, 2 `rejected`), registrate
fra le 10:48 e le 11:17 del 30 agosto — cioè **prima** che l'insieme corrente
esistesse (13:17). Nulla è in volo.

> **Nota di ordine.** O10-O13 stanno al §5-bis, dove sono state prodotte;
> O14 e O15 rispondono alle misure C2 e C3 chieste dalla revisione.

### O14 — Quali gesti rimettono davvero il permesso di gruppo (misura C2)

Eseguita in un repository git usa-e-getta con `umask 0002`, un file per gesto:

| gesto | modo dopo |
|---|---|
| creazione di un file nuovo (git o programma) | `664` |
| `git checkout` che non riscrive il file | `644` (invariato) |
| `git checkout` di un'altra versione | **`664`** |
| cambio di ramo che riscrive il file | **`664`** |
| `merge` che riscrive il file | **`664`** |
| riscrittura in posto (`open(path,'w')`) | `644` (invariato) |

Quindi il `chmod` **non sopravvive a nessuna operazione git che materializzi
byte diversi**. E l'installazione di codice nuovo È un'operazione git: il
cerotto è garantito rompersi alla prossima installazione, non «forse».

> **Correzione (GIRO CODEX 2, P2-C13).** La prima stesura aggiungeva qui che la
> riscrittura in posto «è come `sign.py publish` tocca un file esistente».
> **Era falso**, e su due piani. Vedi O16.

### O16 — Che cosa fa davvero `sign.py`, misurato sul percorso reale

Due affermazioni sbagliate, corrette con il codice alla mano e una misura.

**Primo: il meccanismo non è quello.** `sign.py` non riscrive mai in posto.
`_atomic_replace_bytes` (`runtime/sign.py:234-305`) crea un temporaneo fratello,
applica **esplicitamente** il modo con `fchmod` e conclude con `os.replace`. Il
percorso di firma lo invoca a `runtime/sign.py:391-399` passando
`new_mode=manifest_mode`, cioè il modo **osservato** del manifest esistente. Il
modo sopravvive per **politica dichiarata**, non come effetto collaterale della
troncatura.

**Secondo: non è nemmeno lo stesso percorso.** In modalità solo-negozio
`publish_executor()` (`runtime/sign.py:500-546`) firma in memoria e pubblica nel
negozio dei contratti: non riscrive affatto il file.

Misura sul percorso reale, `umask 0002`:

| chiamata | modo dopo |
|---|---|
| esistente `664`, `preserve_existing_mode=True` | `664` |
| esistente `644`, `new_mode=` modo osservato (come `sign_executor`) | `644` |
| esistente `664`, `new_mode=0600` imposto | `600` |
| file **nuovo**, `new_mode=0644` | `644` |
| file **nuovo**, modo di difetto | **`600`** |
| (confronto) `open(path,'w')` su esistente `644` | `644` |

Un file nuovo creato da `sign.py` nasce quindi `0600`, non `664`: il difetto
della funzione è restrittivo, non permissivo — l'opposto di quello che accade
con git.

**E c'è un terzo fatto, che toglie del tutto `sign.py` da questa storia**:
`sign_executor` scrive `executors/<nome>/manifest.toml` e la sua firma. **Non
tocca nessuno dei 20 file del catalogo di contesto**, che vivono tutti in
`runtime/`. Citarlo come gesto che potrebbe rimettere i permessi era doppiamente
sbagliato: meccanismo sbagliato e percorso non pertinente. I risultati sui gesti
git restano validi; l'equivalenza con `sign.py` è **ritirata**.

### O15 — Che cosa nomina l'insieme corrente fuori dalla radice (misura C3)

> **Questa osservazione è stata rigenerata dopo il GIRO CODEX 2 (P1-C10).** La
> prima versione dichiarava «15 legami» ed era un artefatto: contava riscontri
> di sottostringa invece di record, ne troncava cinque per database per via di
> un limite di stampa, cercava due superfici dello stesso digest e rileggeva
> come testo i database già interrogati. Lo strumento è stato riscritto e ha
> prove proprie; i numeri qui sotto vengono dalla versione nuova.

Misura riproducibile in `internal/tools/censimento_legami_nascita.py`, con prove
in `internal/tools/prova_censimento_legami.py`. Proprietà che la rendono una
misura e non un conteggio:

- **una sola forma canonica** per identificativo — `sha256:<hex>`, `p-<hex>` e
  `<hex>` sono tre superfici di un solo fatto, cercate una volta sola, con un
  vincolo di confine esadecimale che impedisce di trovare un digest dentro una
  sequenza più lunga;
- **un record per locazione che porta il fatto** — un file è un record, una riga
  di database è un record, quale che sia il numero di colonne o di superfici
  coinvolte; niente dipende da quanto lo strumento decide di stampare;
- **nessun oggetto esaminato due volte** — un database interrogato per colonna
  non viene poi riletto come blocco di byte;
- **fail-closed** — ogni radice, database, tabella o file del perimetro che non
  si riesce a leggere viene registrato come non esaminato, la misura si dichiara
  **incompleta** e il processo esce `2`. Un errore non è mai un riscontro, e una
  copertura mancante non è mai un'assenza di legami.

Identificativi acquisiti dalla radice, mai copiati a mano. Perimetro:
`~/.local/state/metnos`, `~/.local/share/metnos`, `~/.config/metnos`,
`/opt/metnos`. File letti: **69 709**. Copertura: **completa** (uscita `0`).

**Esito: 15 record semantici univoci** — 9 file e 6 righe SQLite:

| classe | tipo | quanti | che cosa |
|---|---|---|---|
| viva | file | 6 | ricevute di ammissione sotto `~/.local/state/metnos/contract-publications/v1/*/admission-receipts/`, ciascuna nomina `prepared_admission_context_id` |
| viva | riga SQLite | 6 | `producer_receipts.sqlite#birth_producer_receipts`, colonna **`terminal_envelope`** |
| archiviata | file | 3 | la radice precedente `birth.pre-property-fix-20260830`: `set.json` e `context/material-v1.json` (11 identificativi ciascuno) e `prepared-v1.json` (1) |

**12 dipendenze vive, 3 copie archiviate, 13 fatti distinti** (un fatto = un
identificativo osservato in una classe; le 12 vive nominano tutte lo stesso
contesto, quindi sono un fatto sotto dodici rappresentazioni).

> **Una coincidenza che vale la pena dichiarare.** Anche il totale nuovo è 15.
> Ma il vecchio era `5 + 1 + 6 + 3` — cinque righe troncate da un limite di
> stampa, un database contato una seconda volta come file, sei ricevute e tre
> file archiviati — mentre il nuovo è `9 + 6`. Stesso numero, composizione
> completamente diversa: era giusto per caso, ed è esattamente il motivo per cui
> un totale non è una misura finché non si sa di che cosa è il totale.

**La riscrittura ha trovato una lacuna vera che la prima versione nascondeva.**
Alla prima esecuzione fail-closed sono comparse **22 tabelle non esaminate**
sotto `~/.local/state/metnos/durable_workloads/`: sono tabelle `WITHOUT ROWID`,
e `select rowid, *` su di esse fallisce. La versione precedente inghiottiva
quell'errore con un `continue` e le dichiarava implicitamente prive di legami.
Ora l'identità di riga ripiega sulla posizione ordinale, il record dichiara
quale identità ha usato, e la copertura è completa.

E l'epoca **viene confrontata**, non solo registrata:
`contract_store.py:4888` rifiuta con `birth_context_changed` se l'epoca corrente
non coincide con quella dell'autorizzazione, e
`executor_birth_reattestation.py:231,334` fa lo stesso alla riattestazione di
una generazione corrente.

---

## 3. (b) Che cosa ho INFERITO — dichiarato come inferenza

**I1. Il secondo ostacolo è la stessa regola del primo, sui file.** Non è nel
modello dei ruoli del filesystem sicuro. *Confidenza: alta* — la catena di
chiamate è letta per intero e la misura O3 la riproduce con l'errore esatto.
Questo **contraddice la consegna**, che diceva «non è un bit di permesso».

**I2. Il primo e il secondo ostacolo sono un solo difetto, non due.** Una
regola del prodotto rifiuta la forma che il prodotto stesso installa — la
stessa famiglia del §23.21. La produzione è a `umask 0002` e ogni file nasce
`664`. *Confidenza: alta.*

**I3. Un `chmod` è un cerotto, non una correzione.** ~~Confidenza media su
quali comandi ricreino i modi~~ — **ora misurata, O14**: ogni operazione git che
materializza byte diversi (checkout di un'altra versione, cambio di ramo,
merge) rimette `664`. Poiché installare codice nuovo è un'operazione git, il
cerotto **si rompe alla prossima installazione**, e non «forse». L'inferenza è
ristretta ai gesti effettivamente provati: **`sign.py` è stato tolto
dall'elenco** (O16), perché non riscrive in posto e non tocca nessuno dei 20
file del catalogo.

**I4. Il terzo ostacolo è strutturale, non un incidente.** L'insieme preparato
appunta le impronte della distribuzione, la distribuzione cambia a ogni commit,
e non esiste un percorso previsto per riallineare i due. Chiunque prepari
l'insieme e poi committi qualunque cosa fra i 20 file del catalogo rompe
l'avvio. *Confidenza: alta sul fatto (O4+O7+O8), media sull'intenzione di
disegno* — può darsi che il disegno preveda di preparare l'insieme come
**ultimo** atto dell'installazione, e che nessuno abbia scritto il percorso di
ri-preparazione perché F4 non è chiusa.

**I5. ~~Rifare l'insieme da zero costa poco su questa macchina, oggi.~~
SMENTITA dalla misura C3 (O15, rigenerata).** Il censimento, ora completo e con
copertura verificata, trova **12 dipendenze vive** — 6 ricevute di ammissione di
contratti pubblicati e 6 righe di `producer_receipts` — che nominano tutte il
contesto corrente, più 3 copie archiviate. E l'epoca **è confrontata** in due
punti del prodotto (`contract_store.py:4888`,
`executor_birth_reattestation.py:231,334`). Cambiare epoca non è gratis.

Nota sul percorso di questa inferenza: era già smentita dalla prima versione del
censimento, ma con un numero che non reggeva. **Una conclusione giusta ottenuta
con una misura sbagliata resta da rifare**, e infatti la misura rifatta ha anche
scoperto 22 tabelle che la prima dichiarava implicitamente pulite.

Quello che resta vero è soltanto il pezzo misurato in O10: l'identità d'autore
non si perde, perché viene riderivata da `~/.config/metnos/keys`.

**I6. Non c'è un quarto ostacolo NEL BOOTSTRAP ISOLATO.** *Confidenza: media*
— O6 e O12 lo provano per `require_birth_runtime_before_workers()` in un
processo isolato, **non** provano che il server HTTP intero parta, né che un
turno funzioni. La misura C1 resta aperta e **bloccante**; la sintesi al §0 è
stata riscritta per non promuovere questa inferenza a fatto.

---

## 4. (c) Quale misura mi smentirebbe — decisa prima di eseguirla

Per I1, I2, I4 le misure smentitrici sono già state eseguite e sono O3, O4, O7,
O8: le dichiaro qui perché siano contestabili, non perché siano da rifare.

Stato delle quattro misure smentitrici:

| # | inferenza | misura | stato |
|---|---|---|---|
| C1 | I6 «nessun quarto ostacolo» | avviare il **server HTTP completo** dalla replica su una porta libera e chiedere un turno reale | **APERTA — BLOCCANTE** |
| C2 | I3 «il `chmod` è fragile» | in un worktree git usa-e-getta con `umask 0002`, misurare separatamente checkout, cambio di ramo, merge e riscrittura in posto | **CHIUSA** → O14, I3 confermata e ristretta ai gesti provati; l'equivalenza con `sign.py` è **ritirata** (O16) |
| C3 | I5 «rifare l'insieme costa poco» | censimento semanticamente univoco e fail-closed su file e archivi SQLite, con identificativi acquisiti dalla radice e prove proprie | **CHIUSA (2ª volta)** → O15 rigenerata, copertura completa, **I5 smentita** |
| C4 | I4 «non esiste percorso di ri-preparazione» | leggere le sezioni normative del gruppo 2 | **CHIUSA** → vedi sotto, e cambia il §5 |

### C4 chiusa: la ri-preparazione non è una lacuna, è un confine

Il rapporto `internal/reports/rm0008-gruppo2-analisi-implementazione.md` non
tace sulla questione. Verificato riga per riga:

- **§7.6** (righe 997-1001): la pulizia «non si rimuove mai una radice finale
  esistente».
- **§8.1** (righe 1107-1122): autore, insieme e `prepared-v1.json` si installano
  per «rinomina **senza sostituzione**», e «Non esiste sostituzione di una
  destinazione finale».
- **§8.2** (righe 1140-1155): tre finali validi senza transazione danno «Successo
  di sola ispezione»; e «Una differenza dopo l'installazione è un errore, non un
  invito a riprovare con altri byte».
- **§9.4** (righe 1240-1246): «Un aggiornamento della distribuzione o di un
  registro produce un **nuovo materiale e una nuova epoca**; non modifica in
  posto l'insieme immutabile.»

Quindi il predispositore del gruppo 2 è **deliberatamente** *prima installazione
oppure ispezione*. Non gli manca un pezzo: gli è vietato sostituire un finale.
E l'aggiornamento della distribuzione **è previsto** — come nuova epoca, non
come riparazione.

**Conseguenza sul §5**: la scelta «(A) riparare / (B) rimuovere a mano» era una
falsa alternativa. (A) come riparazione dei finali contraddice §8.1 e §8.2;
(B) come rimozione manuale della radice contraddice §7.6 e non è una procedura
d'esercizio prevista da nessuna autorità normativa. Il punto decisionale vero è
un altro, ed è scritto al §5.

---

## 5. I rimedi, con il loro costo

### Ostacoli 1 e 2 (permessi) — tre strade

- **(1) Cerotto**: `chmod g-w` su `/opt/metnos/runtime` e `644` sui file.
  Sblocca subito; si riapre da solo al primo file riscritto (I3).
- **(2) Cerotto + guardia**: come sopra, più una prova che fallisce se un file
  del catalogo torna scrivibile dal gruppo. Il difetto resta, ma smette di
  essere invisibile.
- **(3) Correzione**: l'installazione posa la distribuzione **non scrivibile dal
  gruppo** (`umask 022` nella fase che scrive, o un passo esplicito), e la
  guardia di (2) la tiene. È l'unica che chiude la famiglia del §23.21.

Raccomandazione: **(3)**, con **(2)** come primo incremento se serve sbloccare
prima. Il cerotto da solo, no: rimette in produzione un guasto che si
ripresenta senza preavviso e senza indizio.

### Ostacolo 3 (insieme stantio) — non è una scelta fra due strade

La misura C4 ha eliminato l'alternativa che avevo scritto. Riassunta perché non
torni: **(A)** intesa come «insegnare al predispositore a riparare o sostituire
i finali esistenti» contraddice §8.1 («Non esiste sostituzione di una
destinazione finale») e §8.2 («Una differenza dopo l'installazione è un errore,
non un invito a riprovare con altri byte»). **(B)** intesa come «portare via a
mano la radice e ripartire» contraddice §7.6 («non si rimuove mai una radice
finale esistente») e non è autorizzata da nessuna autorità normativa: che sia
già stata fatta il 30 agosto (O11) è un **precedente, non un permesso** — e
anzi, alla luce del §7.6, quel gesto era già fuori protocollo.

### Il punto decisionale vero

§9.4 dice che cosa deve succedere quando la distribuzione cambia: **nuovo
materiale e nuova epoca**, senza modificare in posto l'insieme immutabile. Il
prodotto non ha oggi la **transizione** che porta da un'epoca alla successiva.
Quello che manca non è una riparazione: è un protocollo append-only, con:

1. **un proprietario normativo** — quale tratto lo possiede (F4 è il candidato,
   ma va detto, non dedotto);
2. **una condizione d'ingresso dichiarata** — «la distribuzione installata non
   produce più il materiale che l'insieme descrive» oggi è un rifiuto muto;
   deve diventare un esito nominato, che dice quale file non corrisponde;
3. **una regola per i legami esistenti** — le **12 dipendenze vive** di O15
   (6 ricevute di ammissione e 6 righe di `producer_receipts`) nominano il
   contesto corrente, e l'epoca è confrontata in due punti del prodotto: una
   nuova epoca deve dire che cosa ne è di loro;
4. **chi autorizza** — la transizione non può essere automatica, altrimenti
   sparisce la proprietà che il controllo esiste per garantire: un cambiamento
   inatteso a uno dei 20 file verrebbe benedetto invece di fermare tutto.

Finché quel protocollo non è progettato e revisionato, **non esiste un rimedio
ammesso per il terzo ostacolo**, e quindi non esiste un percorso ammesso verso
la produzione. Le misure O10-O13 restano utili come prova di **meccanismo** — la
catena sa costruire un insieme dalla distribuzione corrente conservando
l'identità d'autore — ma il meccanismo non è l'autorizzazione.

## 5-bis. Il meccanismo, misurato (O10-O13)

> **Come leggere questa sezione, dopo C4.** Quando l'ho scritta la chiamavo
> «(B) misurata» e concludevo «costa poco». Le misure O10-O13 restano valide —
> sono fatti — ma la conclusione no: provano che la catena **sa** costruire un
> insieme dalla distribuzione corrente conservando l'identità d'autore, non che
> sia lecito farlo. §7.6 e §8.1 dicono di no. Meccanismo ≠ autorizzazione.

Su un punto la correzione vale comunque: avevo scritto che rifare l'insieme
«costa la radice d'autore corrente». **Quello era sbagliato**, e O10 lo
smentisce.

### O10 — L'identità d'autore non si perde: viene riderivata da una chiave stabile

`AUTHOR_SOURCE_BASENAME_V1 = "keys"` (riga 1344): il provisioner costruisce la
radice d'autore migrando `~/.config/metnos/keys/author_priv.bin`, che è del
**26 aprile** e non cambia. La radice di nascita non *contiene* l'identità: la
*deriva*.

### O11 — La ricostruzione è già accaduta il 30 agosto

Sulla macchina c'è `~/.config/metnos/birth.pre-property-fix-20260830`, una
radice precedente delle 12:47, sostituita da quella corrente alle 13:17.

| radice | set_id | materiale | transazione |
|---|---|---|---|
| `birth.pre-property-fix-20260830` | `9613b852…` | `54e670ec…` | `eaf11ac8…` |
| `birth` (corrente) | `e79b9b5c…` | `6ce61fb5…` | `4bef73fa…` |

Insieme diverso, materiale diverso, transazione diversa — e **le stesse due
chiavi pubbliche d'autore in entrambe**. La ricostruzione è un'operazione già
praticata, quattro ore prima che nascesse l'insieme che oggi ci blocca.

### O12 — Riprodotta sulla replica, dal principio alla fine

Portata via tutta la radice di nascita, conservati i soli ingressi
dell'operatore, e rieseguito il provisioner:

```
ESITO provisioner: installed
chiave attiva: birth-ed25519-v1-sha256-c0c9eba4…   <- la STESSA di oggi
set_id  = a9788f7eb6e2cd35…    (nuovo)
materiale = 795b92d92e3e1b27…  (costruito dalla distribuzione CORRENTE)
```

E con quell'insieme, **codice corrente, nessun file retrocesso**:

```
cancello 1  ok
cancello 2  ok
cancello 2b letti 22, nessun rifiuto
cancello 3  ok -> PreparedSetV1(set_id='a9788f7e…')
ESITO: AVVIO COMPLETATO in 0.7s
```

**Questa è la prova end-to-end che mancava**: con i permessi corretti e
l'insieme rifatto, il codice fuso si avvia con il codice che vogliamo davvero
installare.

### O13 — Un dettaglio che costa un'ora se non lo si sa

Il primo tentativo di ricostruzione è fallito con `birth_provisioning_acl_unsafe`
per una ragione stupida: avevo creato la cartella `birth` con `mkdir` sotto
`umask 0002`, quindi `775`, e il provisioner la vuole `755`. **È la stessa
famiglia degli ostacoli 1 e 2**, comparsa una terza volta. Chi esegue la
ricostruzione deve creare quella cartella a `755`, non «e basta».

### Che cosa costerebbe davvero, se fosse lecito

Non l'identità d'autore. Ma nemmeno «poco», dopo O15:

- il `set_id` e le chiavi di ammissione e produzione che vivono **sotto**
  l'insieme (sono per-insieme per costruzione: `set_id` è l'impronta di un
  documento che **include** `context_material_sha256`, riga 2280-2283);
- le 23 ricevute terminali di O9, che diventano orfane — nessuna in volo;
- **le 12 dipendenze vive di O15** — 6 ricevute di ammissione di contratti
  pubblicati e 6 righe di `producer_receipts` — che nominano tutte il contesto
  corrente, mentre l'epoca è confrontata in due punti del prodotto: questo è il
  costo che avevo omesso, ed è quello che smentisce I5;
- **la ripetizione**: andrebbe rifatto a ogni installazione di codice che tocchi
  uno dei 20 file. Misurato sulla linea RM-0008: **69 commit negli ultimi 7
  giorni** toccano quei file, 81 negli ultimi 30, su 12 giornate distinte.

Quindi non sarebbe «un cerotto che dura poco»: sarebbe **un passo obbligatorio
di ogni installazione**, oggi non documentato, che se dimenticato spegne il
servizio all'avvio successivo con un messaggio che non dice cosa fare. È così
che il servizio è andato giù il 31 agosto — ed è la ragione per cui la
transizione va progettata come prodotto, non ricordata a memoria.

---

## 5-ter. Il meccanismo esiste: che cosa servirebbe, e il contro che conta

### Che cosa esiste già

La catena completa di installazione di un insieme è scritta, con punti di
ripresa durevoli:

```
created → author_staged → inputs_staged → authorities_staged
        → context_staged → verified → author_installed
        → set_installed → marker_installed
```

Ed è la stessa catena che O12 ha appena eseguito dall'inizio alla fine. Non
manca la macchina: manca **la condizione d'ingresso**.

### Che cosa manca, in tre pezzi

1. **Accorgersi dello scostamento.** Il confronto esiste già ed è a due passi:
   `read_prepared_set_v1` ricostruisce il materiale e lo confronta. Oggi
   `_inspect_installed_v1` (riga 1439) verifica solo che marcatore, `set.json` e
   materiale siano coerenti **fra loro**, e non guarda mai la distribuzione.
   Aggiungere il confronto è poco codice.
2. **Lasciar ripartire la catena con una radice d'autore già presente.** Oggi
   la riga 1432-1433 rifiuta: autore presente + marcatore assente = ambiguo.
   Serve un ingresso distinto — «insieme presente ma scostato» — che riusi
   l'autore invece di pretendere che non ci sia.
3. **Decidere che fine fa il vecchio.** Insieme precedente e ricevute orfane
   hanno bisogno di una regola di ritiro. È l'unico pezzo davvero nuovo.

**Fattibilità: alta.** Non è crittografia nuova, è una seconda porta su una
macchina che funziona e che ho appena visto funzionare.

### Il contro vero, e non è di ingegneria

Oggi «la distribuzione installata non produce più il materiale che l'insieme
descrive» è un **rifiuto**, e il commento nel codice lo dice: *«That is a
mismatch to report, never a reason to adopt what is on disk.»*

Se il provisioner si ri-prepara **da solo** quando nota lo scostamento, quella
proprietà sparisce: un cambiamento inatteso a uno dei 20 file — che è
esattamente ciò che quel controllo esiste per intercettare — verrebbe
silenziosamente benedetto invece di fermare tutto. **(A) fatta male è peggio di
(B).**

### La forma che secondo me regge

Il difetto vero, oggi, non è che manchi la ri-preparazione: è che **il
fallimento è muto**. Al momento il servizio muore su
`birth_prepared_set_mismatch` senza dire a nessuno cosa fare, e ci sono volute
sei ore di misure per capirlo.

Quindi (A) nella forma difendibile è:

1. lo scostamento viene **riconosciuto e nominato** all'avvio, con un messaggio
   che dice quale file non corrisponde e quale comando lo risolve;
2. la ri-preparazione resta un **atto esplicito dell'operatore**, mai
   automatica: la proprietà di sicurezza sopravvive intatta;
3. la procedura di O12 smette di essere folclore e diventa quel comando.

Costa poco più di (B) — perché il comando *è* (B), soltanto scritto una volta
per tutte invece che ricordato a mente — e non svende nulla.

> **Raccomandazione, corretta dopo C4**: nessuna delle due «subito». O12 prova
> che il meccanismo esiste, non che sia lecito usarlo: §7.6 vieta di rimuovere
> una radice finale e §8.1 vieta di sostituirla. La forma difendibile resta
> quella dei tre punti qui sopra — esito nominato, atto esplicito, protocollo
> append-only a nuova epoca — ma **è progettazione da fare e far revisionare**,
> non un passo da eseguire oggi. Fino ad allora il fermo prima della produzione
> è assoluto.

---

## 6. Istruzioni di implementazione, passo per passo

> **A chi legge**: esegui **un passo alla volta**, nell'ordine. Dopo ogni passo
> confronta l'uscita con «atteso». Se non coincide, **fermati e riferisci**: non
> improvvisare una correzione, non passare al passo successivo, non ripetere il
> comando con parametri diversi.
>
> Sostituisci `<SCRATCH>` con la cartella di lavoro della tua sessione. Non
> usare `/tmp` direttamente.

### Regole che non si violano mai

1. **Non fondere e non correggere sull'albero da cui gira il servizio.** Si
   lavora in copia, si prova l'avvio, e solo dopo si sposta.
2. **Per rimettere su lo stack**: `systemctl --user start metnos.target`. **Mai**
   avviare `metnos-stack-ready.service` a mano: va in timeout, fa scattare la
   quarantena e spegne tutto.
3. **L'HTTP è un servizio UTENTE**: `systemctl --user restart
   metnos-http.service`. Il `failed` che vedi **senza** `--user` è il residuo
   dell'unità di sistema vecchia e disabilitata: non è un guasto, non
   ripararlo.
4. **Non riavviare i servizi durante un turno utente attivo.**
5. **Avvia e chiudi i processi con il controllore versionato**,
   `internal/tools/controllore_processo.py`. Non scrivere una procedura shell:
   quella che stava qui **dichiarava** di impedire il riuso del PID e non lo
   faceva — non registrava l'istante di avvio, prima dell'ultimo segnale
   ricontrollava solo la riga di comando, e segnalava il solo capogruppo pur
   avendo creato un gruppo intero, lasciando vivi i nipoti (GIRO CODEX 2,
   P1-C12).

   ```python
   import sys; sys.path.insert(0, "/tmp/metnos-rm0008-g6/internal/tools")
   from controllore_processo import ProcessoControllato

   with ProcessoControllato(["...comando..."]) as p:
       ...                      # il processo e' vivo qui
   # all'uscita dal blocco: TERM al gruppo, attesa, KILL solo se serve
   ```

   Due proprietà, non due intenzioni: **il numero non può essere riusato**,
   perché il figlio non viene raccolto fino alla fine della chiusura e quindi
   PID e identificativo di gruppo restano riservati; **la vitalità si legge da
   un `pidfd`**, che indica il processo e non il numero. L'identità (uid e
   istante di avvio) viene riverificata prima di ogni segnale, e viene chiuso
   **tutto il gruppo posseduto**, non il solo capogruppo. Il residuo è
   dichiarato nel docstring invece di essere nascosto.

   Prove: `internal/tools/prova_controllore_processo.py` — uscita spontanea
   senza segnali, terminazione ordinaria senza escalation, escalation contro un
   processo che ignora TERM, rifiuto di segnalare quando l'identità non
   corrisponde più, nipote che sopravvive al padre, e un processo estraneo che
   non viene toccato.

   La scansione `PPID=1` del §10.8 di CLAUDE.md resta come **rete di sicurezza a
   fine sessione**, non come modo di chiudere ciò che hai avviato: ciò che trova
   e che tu non hai avviato è materiale da riferire, non da uccidere.

6. **Ogni misura dichiara le proprie radici prima di misurare.** Se non vedi
   stampato `PATH_RUNTIME`, non stai misurando quello che credi.

### Passo 0 — Verifica il punto di partenza (script versionato e provato)

> **Correzione (GIRO CODEX 2, P1-C11).** Qui c'era un frammento che assegnava
> l'albero in modo incondizionato, mentre l'evidenza riportata sotto lo passava
> come argomento: quella prova negativa **non poteva provenire da quel
> frammento**. Un controllo il cui ramo rosso non è riproducibile non è un
> controllo. Ora è uno script versionato, con ogni aspettativa parametrica e la
> sonda dello stack iniettabile, così i rami rossi si provano su copie senza mai
> fermare lo stack vero.

Non copiare un blocco: esegui lo script.

```bash
/tmp/metnos-rm0008-g6/internal/tools/passo0_punto_di_partenza.sh /opt/metnos
```

Un codice d'uscita per ogni ramo, così un involucro non deve interpretare testo:

```
0 verde            3 radice del repository diversa   5 testa inattesa   7 stack non attivo
2 non e' un repo   4 albero sporco                   6 tag assente
```

Aspettative sovrascrivibili da ambiente — `PASSO0_TESTA_ATTESA`,
`PASSO0_TAG_ATTESO`, `PASSO0_STATO_CMD` — che è ciò che rende provabili i rami
rossi.

Prove: `internal/tools/prova_passo0.sh`, otto casi su repository git
usa-e-getta e con una sonda dello stack finta. **Nessuna prova ferma, avvia o
interroga lo stack reale.** Coperti: verde, percorso inesistente, radice
diversa, albero sporco, testa inattesa, tag assente, stack non attivo, e il
falso verde storico (`.git` presente ma repository non valido, che senza
`pipefail` sarebbe passato).

Qualunque `FERMO`: **non proseguire**. In particolare, file non committati
significa lavoro di un'altra sessione da mettere al riparo prima (è già
successo: 47 file).

### Passo 1 — Costruisci l'apparato di prova (scratch nuovo e privato)

Lo scratch contiene una copia della radice di nascita: non può essere una
cartella riusata, né ereditare `umask 0002`.

```bash
#!/usr/bin/env bash
set -euo pipefail
fermo() { echo "FERMO: $*" >&2; exit 1; }

S=$(mktemp -d "${TMPDIR:-/tmp}/rm0008-XXXXXXXX")
chmod 700 "$S"
[ "$(stat -c '%u %a' "$S")" = "$(id -u) 700" ] || fermo "scratch non privato"
[ -z "$(ls -A "$S")" ] || fermo "scratch non vuoto"
echo "scratch: $S"

# distribuzione: copia esatta, con eliminazione, cosi' una prova precedente
# non puo' lasciare residui
rsync -a --delete --exclude='.git' /tmp/metnos-prova-fusione/ "$S/replica/"

# radici utente separate; 'birth' deve restare 755, non 775 (vedi O13)
mkdir -m 700 "$S/cfg" "$S/state" "$S/data"
cp -a ~/.config/metnos/birth "$S/cfg/birth"
[ "$(stat -c %a "$S/cfg/birth")" = 755 ] || fermo "cfg/birth non e' 755"

# modello di permessi della PRODUZIONE, altrimenti non riproduci il guasto
chmod 775 "$S/replica/runtime"
find "$S/replica/runtime" -maxdepth 1 -type f -name '*.py' -exec chmod 664 {} +
[ "$(stat -c %a "$S/replica/runtime")" = 775 ] || fermo "replica/runtime non e' 775"
echo "PASSO 1 VERDE"
```

`cp -a` è obbligatorio: conserva i permessi, e i permessi sono l'oggetto della
misura. A fine lavoro: verifica che nessun processo tenga descrittori aperti
nello scratch (`lsof +D "$S"`, e il controllore della regola 5 per i processi
che hai avviato tu), poi `rm -rf "$S"`.

### Passo 2 — Riproduci il guasto prima di correggerlo

La sonda è già scritta e versionata: `internal/tools/sonda_avvio_nascita.py`.
Non riscriverla, e **non interpretare il testo**: leggi il codice d'uscita.

```
0  tutti i cancelli verdi          3  cancello 2 rosso (distribuzione)
1  la sonda non ha potuto girare   4  cancello 2b rosso (file del catalogo)
2  cancello 1 rosso (radice)       5  cancello 3 rosso (insieme non concordante)
```

```bash
cp /tmp/metnos-rm0008-g6/internal/tools/sonda_avvio_nascita.py "$S/sonda.py"

sonda() {  # $1 = codice d'uscita atteso
  set +e
  ( cd "$S" && timeout 300 env \
      METNOS_INSTALL_ROOT="$S/replica" METNOS_USER_CONFIG="$S/cfg" \
      METNOS_USER_STATE="$S/state" METNOS_USER_DATA="$S/data" \
      PYTHONPATH="$S/replica" \
      /opt/metnos/.venv/bin/python sonda.py )
  rc=$?; set -e
  [ "$rc" -eq "$1" ] || { echo "FERMO: uscita $rc, attesa $1" >&2; exit 1; }
  echo "  (uscita $rc, come atteso)"
}

sonda 3     # primo ostacolo: la directory runtime e' 775
```

Se la sonda esce `0` qui, **fermati**: non stai misurando la produzione, quasi
certamente il passo 1 è andato storto.

Usa `/opt/metnos/.venv/bin/python`, non `python3`: con l'interprete di sistema
manca `tomlkit` e la sonda esce `1` senza misurare nulla.

Le prove della sonda stessa (verde, ogni rifiuto atteso, file assente, sonda
non eseguibile) stanno in `internal/tools/prova_sonda_avvio_nascita.py`: girano
in pochi secondi, non toccano nulla e non chiedono una radice di nascita.

### Passo 3 — Verifica i tre ostacoli in sequenza, in copia

Ogni riesecuzione della sonda ha il suo codice atteso, e il blocco si ferma da
sé se non coincide.

```bash
# --- secondo ostacolo: la directory e' a posto, i file no ---
chmod g-w "$S/replica/runtime"
sonda 4     # atteso: cancello 2 ok; cancello 2b rifiuta ogni lettura, mode=664

# --- terzo ostacolo: permessi a posto, insieme stantio ---
# l'elenco dei file lo dà il catalogo, mai una lista copiata a mano
( cd "$S/replica" && /opt/metnos/.venv/bin/python - <<'CATALOGO'
import ast, os, pathlib
src = pathlib.Path('runtime/executor_birth_context_v1.py').read_text()
for n in ast.parse(src).body:
    if isinstance(n, ast.AnnAssign) and getattr(n.target, 'id', '') == 'CONTEXT_CATALOG_V1':
        cat = ast.literal_eval(n.value); break
else:
    raise SystemExit("FERMO: CONTEXT_CATALOG_V1 non trovato")
nomi = sorted({f for _, _, files, _ in cat for f in files})
if len(nomi) != 20:
    raise SystemExit(f"FERMO: il catalogo ha {len(nomi)} file, non 20: documento scaduto")
for f in nomi:
    os.chmod(pathlib.Path('runtime') / f, 0o644)
print(f"portati a 644: {len(nomi)} file")
CATALOGO
) || { echo "FERMO: catalogo inatteso" >&2; exit 1; }

sonda 5     # atteso: cancelli 1, 2 e 2b verdi; cancello 3 birth_prepared_set_mismatch
```

Se il catalogo non ha esattamente 20 file, **questo documento è scaduto**:
fermati e riferisci.

A questo punto hai riprodotto tutti e tre gli ostacoli, in copia, senza aver
toccato niente.

### Passo 4 — Fermo: il rimedio al terzo ostacolo non esiste ancora

A questo punto hai riprodotto tutto in copia e non hai toccato niente.
**Qui la procedura finisce**, e non per prudenza: la misura C4 ha stabilito che
il predispositore del gruppo 2 è deliberatamente *prima installazione oppure
ispezione* (§8.1, §8.2), che una radice finale non si rimuove (§7.6) e che un
aggiornamento della distribuzione richiede **nuovo materiale e nuova epoca**
(§9.4).

Quindi:

- **non** applicare il `chmod` in produzione «intanto» — O14 dimostra che si
  rompe alla prossima installazione, e da solo non risolve il terzo ostacolo;
- **non** portare via la radice di nascita e rifarla — O11/O12 provano che il
  meccanismo funziona, non che sia lecito: §7.6 dice il contrario;
- **non** scrivere una riparazione nel predispositore — contraddice §8.1/§8.2.

Ciò che serve è progettare la **transizione append-only a nuova epoca** (§5,
«Il punto decisionale vero»), farla revisionare, e solo allora scrivere i passi
5 e 6. Se stai leggendo questo documento cercando il comando che sblocca la
produzione: **non c'è, e il fatto che non ci sia è il risultato del lavoro.**

### Passo 5 — La misura C1, l'unica che si può ancora fare in copia

C1 è aperta e bloccante: O6 e O12 provano che
`require_birth_runtime_before_workers()` completa in un processo isolato, non
che il server HTTP parta né che un turno funzioni.

Si esegue **interamente sulla replica**, mai in produzione, e richiede:

- una porta libera scelta e verificata (`ss -ltn` prima di legarla), mai la
  8770 della produzione;
- radici utente separate (`METNOS_USER_CONFIG/STATE/DATA` dentro lo scratch),
  così il server di prova non tocca lo stato vivo;
- il server avviato e chiuso con `ProcessoControllato` (regola 5), così che
  alla fine muoia l'intero gruppo e non il solo capogruppo: un server HTTP
  genera figli, ed è esattamente il caso che la vecchia procedura sbagliava;
- un turno reale su `/agent/turn` con il corpo minimo del dominio toccato, e
  l'esito letto dal codice HTTP oltre che dal testo.

Non scrivo qui il blocco completo perché non l'ho eseguito: scriverlo come se
fosse provato sarebbe esattamente l'errore che questo documento esiste per non
ripetere. Chi esegue C1 lo scrive e lo lascia qui, con l'uscita osservata.

### Passo 6 — NON AUTORIZZATO

Nessun passo su `/opt/metnos` è autorizzato finché la transizione a nuova epoca
non è scelta, progettata e provata. Quando lo sarà, il passo 6 dovrà contenere —
e oggi non li ha — almeno:

1. la **prova che non c'è un turno utente attivo** (§8.6 di CLAUDE.md), non
   l'impressione che non ce ne sia;
2. il comando esatto della correzione, già eseguito in copia;
3. l'istantanea immediatamente precedente alla mutazione (codice, permessi,
   stato i18n, radice di nascita), con il comando che la produce;
4. i comandi di ritorno **e la verifica del ritorno** per ognuna di quelle
   quattro cose: «torna al punto di ritorno» non è una procedura, è una
   speranza;
5. il criterio di verifica finale, incluso un turno reale.

---

## 7. Che cosa NON fare

- **Non retrocedere `executor_standard.py` alla versione `3eeb4b1b`.** È il
  gesto che fa passare il cancello 3 (O5) ed è la cosa sbagliata: butta via una
  correzione di prodotto per compiacere un artefatto stantio. Serve solo come
  prova differenziale, mai come rimedio.
- **Non applicare il `chmod` in produzione «intanto che si decide».** Rimette in
  piedi il servizio nascondendo due difetti, e il primo file riscritto lo
  ributta giù senza indizio.
- **Non rifare la fusione**: è già fatta e verificata in
  `/tmp/metnos-prova-fusione` (`2cb6eac4`).
- **Non toccare la radice di nascita reale, e non rimuoverla.** §7.6 del
  rapporto del gruppo 2 dice che non si rimuove mai una radice finale esistente.
  Le prove si fanno su `cp -a` della radice, mai sulla radice. Che il 30 agosto
  sia già stato fatto (O11) non è un precedente da imitare: alla luce del §7.6
  era già fuori protocollo.
- **Non scrivere una riparazione nel predispositore**: §8.1 vieta la
  sostituzione di una destinazione finale e §8.2 dice che «una differenza dopo
  l'installazione è un errore, non un invito a riprovare con altri byte».

---

## 8. Punti di ritorno

- Tag `pre-fusione-31-8-2026` — stato prima di tutto.
- `14ea7179` — installazione attuale, con i 47 file al riparo.
- `…/scratchpad/backup-47/` — i 47 file dell'installazione.
- `…/scratchpad/permessi-prima.txt` — permessi originali.
- `…/scratchpad/i18n-prima.sqlite` — DB i18n prima della sincronizzazione.
- La radice di nascita reale non è stata toccata da questa diagnosi: data di
  modifica di `prepared-v1.json` invariata al 30/8 13:17:25.

---

## 9. La domanda per il revisore

> Le tre domande originali sono state chiuse dal GIRO CODEX 1 e dalle misure
> C2/C3/C4. Restano queste.

1. **Chi possiede normativamente la transizione a nuova epoca?** §9.4 la
   prescrive, nessun tratto la implementa. F4 è il candidato, ma va dichiarato.
2. **Che cosa succede ai legami esistenti quando l'epoca cambia?** O15 conta 6
   ricevute di ammissione che nominano il contesto corrente, e l'epoca è
   confrontata in due punti del prodotto.
3. **C1 resta aperta**: nessuno ha ancora provato che il server HTTP intero
   parta e serva un turno con le tre correzioni. Finché non è fatta, «nessun
   quarto ostacolo» vale solo per il bootstrap isolato.

---

# GIRO CODEX 1 — revisione avversariale della diagnosi e della sonda

Ancoraggio: contenuto letto inizialmente sul worktree
`/tmp/metnos-rm0008-g6` a testa `651d2b06` e poi materializzato senza cambio
semantico nel commit `be423001`, insieme a
`internal/tools/sonda_avvio_nascita.py`. Nessuna azione eseguita su
`/opt/metnos`; le sole riproduzioni aggiuntive hanno usato percorsi inesistenti
o la replica gia' presente sotto lo scratchpad.

## Evidenze che reggono

- **I1 regge.** La catena
  `open_distribution_sources_v1()` -> sessione `historical_public` ->
  `_verify_posix_file()` rifiuta esattamente `mode & 0o022`; non serve
  invocare un difetto del modello dei ruoli per spiegare O3.
- **O4/O5 reggono come prova differenziale del terzo ostacolo.** Il documento
  distingue correttamente la retrocessione usata per isolare una causa da una
  correzione ammissibile.
- **O7/O8 reggono contro codice e disegno.** Il predispositore, davanti ai tre
  finali validi, esegue sola ispezione; autore finale senza marcatore e senza
  journal e' uno stato ambiguo, non una prima installazione.
- La separazione fra osservazioni, inferenze e misure smentitrici e' utile e va
  conservata. Non autorizza ancora il passo di produzione.

## Rilievi bloccanti

### P1-C1 — C4 ha gia' una risposta normativa, ma non autorizza ne' A ne' B

Il rapporto del Gruppo 2 non lascia la ri-preparazione come dettaglio omesso:

- la pulizia «non rimuove mai una radice finale esistente» (§7.6, righe
  997-1001);
- autore, insieme e `prepared-v1.json` sono installati senza sostituzione e
  «non esiste sostituzione di una destinazione finale» (§8.1, righe
  1107-1122);
- tre finali validi senza transazione producono sola ispezione (§8.2, riga
  1142), mentre una differenza non invita a rigenerare (§8.2, righe
  1151-1153);
- un aggiornamento produce **nuovo materiale e nuova epoca**, senza modificare
  in posto l'insieme immutabile (§9.4, righe 1240-1246).

Quindi A, se significa insegnare al predispositore a sostituire o riparare i
finali esistenti, contraddice il disegno. B, se significa rimuovere manualmente
radice e finali e ripartire, non e' una procedura d'esercizio prevista dal
disegno e non e' autorizzata dalla sola constatazione che 23 ricevute sono
terminali. La conclusione sostenuta e': il predispositore di Gruppo 2 e'
deliberatamente *prima installazione o ispezione*; l'evoluzione richiede una
transizione esplicita a nuova epoca, con proprietario e protocollo da decidere
nel tratto successivo/F4.

**Disposizione richiesta:** chiudere C4 con questa evidenza, eliminare la falsa
scelta A/B e formulare il vero punto decisionale: progettare e revisionare la
transizione append-only a nuova epoca, oppure dimostrare da un'autorita'
normativa diversa che l'installazione puo' essere ricreata da zero. Fino ad
allora il fermo prima della produzione resta assoluto.

### P1-C2 — La sonda stampa fallimenti ma termina con successo

`show()` intercetta `BaseException`, restituisce `False`, ma nessun chiamante
traduce il risultato in uno stato d'uscita non zero. Anche con cancello 1, 2 o
3 rosso, il processo arriva in fondo e normalmente restituisce 0. Un agente o
un involucro che controlli il comando anziche' interpretare testo libero puo'
quindi registrare un falso verde. Inoltre `BaseException` assorbe anche le
interruzioni, e l'`os.stat()` nel ramo d'errore del cancello 2b e' fuori da una
protezione: un file assente o un collegamento puo' sostituire la diagnosi
originale con un secondo errore.

**Disposizione richiesta:** dare alla sonda un contratto d'uscita stabile
(zero soltanto quando tutti i cancelli richiesti sono verdi; non zero
altrimenti), intercettare `Exception`, non `BaseException`, non eseguire un
cancello dipendente quando il prerequisito e' rosso e rendere la stampa del
modo incapace di coprire l'errore originale. Aggiungere prove della sonda per
verde, rifiuto atteso e file assente.

### P1-C3 — Il controllo iniziale puo' dichiarare pulito un percorso inesistente

Il comando `git -C /opt/metnos status --porcelain | wc -l` non usa
`pipefail`. Riprodotto con un percorso inesistente: `git` stampa il fatal,
`wc -l` stampa `0` e l'intera pipeline termina con 0. E' precisamente il valore
«atteso». Il passo 0 inoltre esplicita il fermo solo per `status != 0`, non per
testa, servizio o tag discordanti.

**Disposizione richiesta:** usare un blocco con `set -euo pipefail`, verificare
prima che la radice sia il repository atteso e trasformare ciascuna delle
quattro aspettative in un confronto eseguibile che termina subito su ogni
discordanza.

### P1-C4 — La procedura vieta di improvvisare ma omette i comandi decisivi

Nel passo 3 «porta a 644 i 20 file» e' un commento, non un comando, e non sono
mostrate le tre riesecuzioni della sonda con i rispettivi stati d'uscita. Il
passo 5 non definisce comando di avvio del server isolato, scelta e prova della
porta libera, lockfile, autenticazione della richiesta, corpo minimo del turno,
uscita attesa e arresto verificato. Il passo 6 non contiene prova di assenza di
un turno utente attivo, comando della correzione, snapshot immediatamente
precedente alla mutazione, comandi di ritorno o verifica del ritorno. «Torna al
punto di ritorno» lascia proprio il gesto piu' delicato all'improvvisazione.

**Disposizione richiesta:** rendere eseguibili i passi 3 e 5 con comandi
copiabili, uscite e codici attesi, e punti di fermo. Il passo 6 deve restare
esplicitamente non autorizzato finche' non e' scelta e provata la transizione;
quando verra' scritto, dovra' includere controllo del turno attivo e ritorno
completo per codice, permessi, stato i18n e radice di nascita.

### P1-C5 — La regola sui processi residui ha un bersaglio soggettivo

La scansione `PPID=1` e CPU maggiore di 50 puo' non vedere un residuo dormiente
e puo' mostrare un processo estraneo. «Se e' nostro» non e' un discriminante
riproducibile, e passare direttamente al segnale non recuperabile non verifica
che PID e identita' siano rimasti gli stessi fra censimento e azione.

**Disposizione richiesta:** conservare il PID del solo processo avviato dalla
procedura (o una sua unita'/cgroup isolata), verificarne identita', UID e riga
di comando, chiedere prima la terminazione ordinaria, attendere con limite e
usare l'arresto forzato soltanto se la stessa identita' e' ancora viva. Nessuna
azione deve derivare da una scansione generica del sistema.

## Rilievi probatori

### P2-C6 — C2 non e' eseguibile sull'apparato descritto

Il passo 1 crea `replica/` con `--exclude='.git'`; C2 chiede poi un
`git checkout` «in copia». Sulla replica reale il comando termina 128 con
«not a git repository». Anche corretto questo, un solo `checkout` non dimostra
automaticamente l'effetto di `merge` e `sign.py publish`, che I3 mantiene
distinti e qualifica solo con confidenza media.

**Disposizione richiesta:** eseguire C2 in un worktree Git usa-e-getta, con
`umask 0002`, file e commit noti, e misurare separatamente soltanto i meccanismi
che si vogliono poi nominare. In alternativa restringere I3 al meccanismo
effettivamente provato.

### P2-C7 — Lo scratch non e' obbligatoriamente nuovo ne' privato

`S=<SCRATCH>; mkdir -p "$S"` accetta una directory gia' popolata. `rsync`
senza eliminazione puo' lasciare file di una prova precedente e `cp -a` verso
una destinazione esistente puo' cambiare la forma della copia. Lo scratch
contiene inoltre una copia della radice di nascita e non deve ereditare
semplicemente la `umask 0002` descritta nel documento.

**Disposizione richiesta:** creare una directory nuova con modo 0700, provarne
proprietario e vuotezza e rifiutare il riuso. Dichiarare anche la pulizia finale
e la verifica che nessun processo conservi descrittori verso lo scratch.

### P2-C8 — C3 non ha ancora una misura riproducibile

«Censire ogni cosa» non definisce radici, formati, identificatori completi,
comandi, falsi positivi o uscita attesa. Una ricerca testuale non basta per
SQLite o documenti strutturati, e O9 conta ricevute senza ancora provare quali
campi le leghino a `set_id`, inventario o identita' d'autore.

**Disposizione richiesta:** acquisire prima gli identificatori canonici dalla
copia, elencare tutte le radici e gli archivi inclusi, interrogare
esplicitamente i database pertinenti, registrare conteggi e legami per campo e
definire il risultato che smentisce I5. Fino a quella misura I5 non puo'
sostenere alcuna rimozione o ricreazione.

### P2-C9 — La sintesi promuove I6 da inferenza a fatto

La sintesi conclude senza qualifica «non c'e' un quarto ostacolo», mentre I6
ammette che O6 prova soltanto `require_birth_runtime_before_workers()` e C1
resta aperta sul server intero e sul turno. La sezione dettagliata e' onesta;
la frase piu' visibile non lo e'.

**Disposizione richiesta:** scrivere in sintesi «nessun quarto ostacolo nel
bootstrap isolato; server HTTP e turno non ancora provati» e mantenere C1 come
condizione bloccante.

## VERDETTO DI CONVERGENZA — GIRO CODEX 1

La diagnosi causale dei tre ostacoli e' forte e I1 regge. Non converge ancora
la decisione sul terzo ostacolo ne' la procedura operativa: C4 esclude la falsa
scelta corrente, la sonda puo' restituire falsi verdi e i passi che precedono
la produzione non sono ancora riproducibili senza giudizio implicito.

**NON CONCORDO ANCORA SUL DOCUMENTO.**

---

# GIRO CLAUDE 1 — verifica avversariale dei rilievi di Codex

Ancoraggio: worktree `/tmp/metnos-rm0008-g6`, ramo `rm0008/diagnosi-avvio`,
commit di base `be423001`. Nessuna azione su `/opt/metnos`: albero, permessi,
radice di nascita e servizi verificati invariati alla fine del giro. Tutte le
riproduzioni sono avvenute nello scratchpad di sessione o in repository git
usa-e-getta creati per l'occasione.

## Rilievi accolti, con la prova

### P1-C1 — ACCOLTO, ed è il rilievo che cambia il documento

**Non l'ho preso per buono: ho verificato le quattro citazioni una per una** in
`internal/reports/rm0008-gruppo2-analisi-implementazione.md`. Sono tutte reali e
dicono ciò che Codex riporta:

- §7.6 riga 1001: «non si rimuove mai una radice finale esistente»;
- §8.1 righe 1113-1121: rinomina «senza sostituzione» per autore, insieme e
  marcatore; «Non esiste sostituzione di una destinazione finale»;
- §8.2 riga 1142 e 1151-1153: tre finali validi senza transazione = «Successo di
  sola ispezione»; «Una differenza dopo l'installazione è un errore, non un
  invito a riprovare con altri byte»;
- §9.4 righe 1241-1246: «Un aggiornamento della distribuzione o di un registro
  produce un nuovo materiale e una nuova epoca; non modifica in posto l'insieme
  immutabile».

**Applicato**: §4 chiude C4 con questa evidenza; §5 elimina la falsa alternativa
A/B e formula il punto decisionale vero (transizione append-only a nuova epoca,
con proprietario normativo, condizione d'ingresso dichiarata, regola per i
legami esistenti e autorità che la concede); §5-bis riqualifica O10-O13 come
prova di **meccanismo**, non di autorizzazione; §7 aggiunge i due divieti
espliciti; il passo 4 diventa un fermo motivato e il passo 6 diventa
«NON AUTORIZZATO».

**Aggiungo un fatto che rafforza il rilievo contro la mia stessa tesi
precedente**: O11 documenta che il 30 agosto la radice di nascita *è già stata*
sostituita. Alla luce del §7.6 quel gesto era fuori protocollo. L'avevo citato
come prova che «si può»; è invece la prova che è già successo senza che nessuno
lo autorizzasse.

### P1-C2 — ACCOLTO, sonda riscritta e provata

La sonda aveva tre difetti reali: intercettava `BaseException` (quindi anche le
interruzioni), non traduceva mai un cancello rosso in uno stato d'uscita, e
faceva `os.stat()` dentro il ramo d'errore senza protezione.

**Applicato** in `internal/tools/sonda_avvio_nascita.py`:

- contratto d'uscita dichiarato nel docstring e stabile: `0` tutti verdi, `2`
  cancello 1, `3` cancello 2, `4` cancello 2b, `5` cancello 3, `1` la sonda non
  ha potuto girare;
- `except Exception`, mai `BaseException`;
- un cancello con il prerequisito rosso **non viene eseguito** e viene stampato
  come «NON ESEGUITO»;
- `_mode_of()` degrada a «modo non leggibile (FileNotFoundError)» invece di
  sollevare, così l'errore originale non viene mai coperto.

**Prove**, in `internal/tools/prova_sonda_avvio_nascita.py`: 8 casi con moduli
finti (nessuna chiave, nessun insieme preparato, nessun modello di ruoli), tutte
verdi — tutti verdi, ciascun cancello rosso col suo codice, file assente con
l'errore originale conservato, e le due uscite `1` della sonda non eseguibile.

**La suite non è vacua**: iniettando la regressione «il cancello 3 gira anche se
il 2b è rosso», due prove diventano rosse e la suite esce `1`.

In parallelo la sonda è stata verificata anche contro l'apparato reale, sette
casi: verde (uscita 0), cancello 3 stantio (5), file a 664 (4), directory a 775
(3), radice assente (2), catalogo con un file rimosso (4), e le due uscite `1`.

### P1-C3 — ACCOLTO, e l'ho commesso di nuovo mentre lo correggevo

Il rilievo è esatto: `git … | wc -l` senza `pipefail` stampa `0` anche quando
`git` fallisce, e `0` è il valore atteso.

**Applicato**: il passo 0 è ora un blocco `set -euo pipefail` che verifica la
radice del repository, la pulizia, la testa, il tag e lo stack, e si ferma da sé
su ogni discordanza. Provato nei due versi: verde su `/opt/metnos` (uscita 0),
`FERMO` su un percorso inesistente (uscita 1).

**Nota onesta**: durante la verifica della sonda ho scritto io stesso un test il
cui esito passava per un `grep`, e ho letto l'uscita del `grep` invece che
quella della sonda. Me ne sono accorto e l'ho rifatto catturando il codice
giusto. È lo stesso errore del rilievo, commesso mentre lo correggevo: è la
ragione per cui la disposizione «leggi il codice d'uscita, non il testo» sta ora
scritta nel passo 2.

### P1-C4 — ACCOLTO

**Applicato**: il passo 3 contiene ora i comandi completi, ogni riesecuzione
della sonda col suo codice atteso e un `FERMO` automatico se non coincide; il
`chmod` dei 20 file è un comando che deriva l'elenco dal catalogo e si ferma se
il catalogo non ha esattamente 20 file. Il passo 5 elenca ciò che C1 richiede
(porta libera verificata, radici separate, PID conservato, turno reale) ma **non
finge di essere provato**: non l'ho eseguito, e scriverne il blocco come se lo
fosse sarebbe l'errore che il documento esiste per non ripetere. Il passo 6 è
esplicitamente non autorizzato e elenca i cinque elementi che dovrà contenere,
incluse la prova di assenza di turno attivo e la verifica del ritorno.

### P1-C5 — ACCOLTO

**Applicato**: la regola 5 non parte più da una scansione generica. Si conserva
il PID del processo che si è avviato, insieme alla riga di comando e all'UID; si
verifica l'identità **prima** di ogni segnale; si chiede la terminazione
ordinaria, si attende con limite, e si forza soltanto se la stessa identità è
ancora viva. La scansione `PPID=1` del §10.8 resta come rete di sicurezza a fine
sessione: ciò che trova e che non hai avviato tu è materiale da riferire, non da
uccidere.

### P2-C6 — ACCOLTO, C2 eseguita in un worktree git vero

Il rilievo è esatto: la replica nasce con `--exclude='.git'`, quindi
`git checkout` là dentro esce 128.

**Eseguita** in un repository git usa-e-getta con `umask 0002`, un gesto per
volta (O14):

| gesto | modo dopo |
|---|---|
| creazione di un file nuovo | `664` |
| `git checkout` che non riscrive | `644` |
| `git checkout` di un'altra versione | `664` |
| cambio di ramo che riscrive | `664` |
| `merge` che riscrive | `664` |
| riscrittura in posto | `644` |

I3 esce **confermata e ristretta ai gesti provati**: `sign.py publish` che
riscrive un file esistente conserva il modo; ogni operazione git che materializza
byte diversi lo rimette a `664`.

### P2-C7 — ACCOLTO

**Applicato**: il passo 1 crea lo scratch con `mktemp -d`, lo porta a `0700`,
verifica proprietario e vuotezza e rifiuta il riuso; `rsync` ha `--delete`; le
radici utente si creano con `mkdir -m 700`; si verifica che `cfg/birth` sia
`755` e non `775`. La pulizia finale è dichiarata, con la verifica che nessun
processo tenga descrittori aperti nello scratch.

### P2-C8 — ACCOLTO, ed è il rilievo che ha smentito una mia inferenza

**Eseguita** come misura riproducibile:
`internal/tools/censimento_legami_nascita.py`. Acquisisce gli identificativi
dalla radice invece di copiarli a mano, dichiara le quattro radici esaminate,
interroga gli archivi SQLite tabella per tabella e colonna per colonna invece di
cercare testo nei loro byte, e stampa il criterio che smentisce I5.

Esito (O15): **15 legami fuori dalla radice di nascita**, su 69 373 file letti.
Fra questi **6 ricevute di ammissione** di contratti pubblicati che nominano
`prepared_admission_context_id = sha256:f90abe9d…`. E l'epoca **è confrontata**,
non solo registrata: `contract_store.py:4888` rifiuta con `birth_context_changed`,
`executor_birth_reattestation.py:231,334` fa lo stesso alla riattestazione.

**I5 è smentita** e nel §3 è marcata come tale. Codex aveva ragione a non
accettarla: era la mia inferenza più comoda, l'avevo dichiarata debole, e la
misura l'ha demolita.

### P2-C9 — ACCOLTO

**Applicato**: la sintesi al §0 dice ora «nessun quarto ostacolo nel bootstrap
isolato; server HTTP e turno reale non ancora provati», e dichiara che il
documento non autorizza né una correzione in produzione né la ricostruzione
della radice. I6 al §3 è riformulata allo stesso modo. C1 resta **aperta e
bloccante** nella tabella del §4.

## Rilievi respinti

Nessuno. Tutti e nove reggono alla verifica.

## Che cosa il giro aggiunge, oltre alle correzioni

- Due misure nuove che erano solo intenzioni: **O14** (C2) e **O15** (C3).
- Una prova che mancava: la sonda ora ha **prove proprie**, e le prove sono state
  verificate non vacue con una regressione iniettata.
- Una correzione al mio stesso documento che nessuno aveva chiesto: O11 non è
  più citato come «si può fare», ma come «è già stato fatto fuori protocollo».

## VERDETTO — GIRO CLAUDE 1

Tutti e nove i rilievi sono accolti e applicati; le due misure aperte C2 e C3
sono state eseguite e una di esse ha smentito un'inferenza; la sonda ha un
contratto d'uscita provato e prove proprie non vacue; la procedura si ferma da
sé invece di chiedere giudizio.

**CONCORDO SUL DOCUMENTO**, con due riserve dichiarate che non sono rilievi
irrisolti ma lavoro esplicitamente non fatto:

1. **C1 resta aperta e bloccante.** Il passo 5 dice che cosa richiede e non
   finge di averlo eseguito.
2. **Il rimedio al terzo ostacolo non esiste.** Il documento ora lo dichiara
   invece di proporne uno: serve progettare la transizione append-only a nuova
   epoca, e quello è lavoro di disegno da far revisionare, non un passo da
   eseguire.

La produzione resta fuori ambito, e lo è rimasta per tutto il giro.

---

# GIRO CODEX 2 — verifica delle disposizioni Claude 1

Ancoraggio: worktree `/tmp/metnos-rm0008-g6`, ramo
`rm0008/diagnosi-avvio`, testa `57093842`. Ho rieseguito le prove proprie
della sonda, il censimento C3 e le verifiche statiche sui percorsi citati. Non
ho modificato `/opt/metnos`, la radice di nascita, i permessi o i servizi.

## Ciò che ora regge

- Le otto prove di `internal/tools/prova_sonda_avvio_nascita.py` passano. I
  codici d'uscita dei cancelli, l'arresto delle dipendenze e la conservazione
  dell'errore originale sono ora verificati.
- P1-C1 è chiuso: le quattro disposizioni del gruppo 2 sostengono il fermo del
  passo 4 e tolgono dal documento la falsa scelta fra sostituzione e
  ricostruzione in posto.
- P2-C7 e P2-C9 sono applicati: lo scratch ha un contratto esplicito e la
  conclusione è limitata al bootstrap isolato.
- La produzione rimane esplicitamente fuori ambito. Questo giro non autorizza
  C1, la transizione di epoca o un intervento sui servizi.

## Nuovi rilievi

### P1-C10 — O15 non è ancora una misura valida: il numero 15 è un artefatto

Ho rieseguito `internal/tools/censimento_legami_nascita.py`. L'uscita dice 15,
ma quel numero nasce dalla meccanica del programma, non da quindici legami
univoci:

1. `producer_receipts.sqlite` produce **12 riscontri grezzi**. Una query
   indipendente trova **6 righe**: ciascuna viene contata due volte perché il
   programma cerca sia `sha256:f90abe…` sia il digest nudo contenuto nella
   stessa stringa.
2. Alle righe 79-81 il programma aggiunge a `trovati` soltanto
   `esiti[:5]`. Il totale globale dipende quindi da un limite usato per la
   stampa: dei 12 riscontri grezzi ne entrano arbitrariamente 5.
3. Lo stesso database, già interrogato strutturalmente, viene poi riletto come
   file di byte alle righe 83-101 e aggiunto una seconda volta come un unico
   legame testuale.
4. L'uscita aggiunge 6 ricevute JSON e 3 file della radice archiviata. Il 15 è
   dunque `5 + 1 + 6 + 3`, non la cardinalità dichiarata. Contraddice anche la
   scomposizione di O15: il riscontro SQLite osservato è in
   `birth_producer_receipts.terminal_envelope`, non nel campo `encoded`, e la
   radice precedente produce 3 file, non 8.

La presenza dei sei receipt di ammissione vivi resta un'evidenza qualitativa
forte contro I5, ma non rende vero il conteggio né classifica il costo della
transizione. Inoltre le eccezioni di lettura di tabelle e file vengono ignorate
con `continue`: una copertura incompleta può quindi apparire come assenza di
legami. Un errore di apertura SQLite, al contrario, viene restituito come se
fosse un riscontro e può gonfiare il totale.

**Disposizione richiesta:** C3 torna aperta finché il censimento non:

- conta record semantici univoci, separando file, righe SQLite e identificativi;
- normalizza una sola forma canonica di ogni identificativo;
- non rilegge come testo i database già interrogati strutturalmente;
- distingue dipendenze vive, copie archiviate e rappresentazioni multiple
  dello stesso fatto;
- esce non-zero e dichiara la misura incompleta a ogni radice, database,
  tabella o file non leggibile che rientri nel perimetro;
- ha prove con più di cinque riscontri, forma prefissata e nuda nello stesso
  valore, database già censito, errore SQLite e duplicazione dello stesso
  legame su più rappresentazioni.

Dopo la correzione vanno rigenerati O15, la scomposizione del totale, lo stato
di C3 e le frasi quantitative di I5. Non basta cambiare il numero nel testo.

### P1-C11 — La prova negativa del passo 0 non può provenire dallo script mostrato

Il blocco del passo 0 assegna incondizionatamente `PROD=/opt/metnos` e non legge
argomenti. Eppure l'evidenza dichiara:

```
./passo0.sh /percorso/inesistente
FERMO: /percorso/inesistente non e' un repository git
```

Quell'invocazione, applicata al blocco versionato nel documento, continua a
verificare `/opt/metnos`; non può produrre l'uscita riportata. La correzione di
P1-C3 è giusta nell'intento ma la sua prova nei due versi non è riproducibile.

**Disposizione richiesta:** trasformare il blocco in uno script versionato e
provato, oppure usare esplicitamente `PROD=${1:-/opt/metnos}`. Rieseguire il
caso verde e almeno i casi radice inesistente, repository sporco, testa errata,
tag assente e stack non attivo, verificando il codice d'uscita. Il test deve
iniettare le dipendenze o operare solo su copie: non deve spegnere lo stack per
provare un ramo rosso.

### P1-C12 — La regola 5 dichiara di impedire il riuso del PID, ma non lo fa

La procedura conserva PID, riga di comando e UID. Non conserva l'istante di
avvio da `/proc/$PID/stat`, e prima del `KILL` ricontrolla soltanto la riga di
comando, non più l'UID. Un PID riusato da un processo dello stesso utente e con
la stessa riga supera il controllo. Resta inoltre una finestra fra controllo e
segnale; `setsid` crea un gruppo, ma la procedura segnala solo il leader e può
lasciare figli del server di prova.

Questo è precisamente il rischio che la regola dice di aver eliminato. Non è
accettabile lasciarlo in una procedura destinata a un agente esterno.

**Disposizione richiesta:** affidare avvio, attesa e chiusura a un piccolo
controllore versionato e provato che usi un riferimento stabile al processo
(`pidfd` su Linux) e gestisca l'intero gruppo posseduto. Se si mantiene una
variante shell, almeno acquisire e verificare PID, UID, riga, start-time e PGID
a ogni stadio, dichiarando il residuo TOCTOU invece di dire che il riuso è
impedito. Provare uscita spontanea, TERM riuscito, escalation, PID non più
coerente e figlio ancora vivo.

### P2-C13 — O14 attribuisce a `sign.py` un meccanismo che il codice non usa

O14 misura correttamente `open(path, 'w')`, ma poi afferma che questa è la
modalità con cui `sign.py publish` tocca un file esistente. Il codice dice
altro: `runtime/sign.py:234-305` crea un temporaneo fratello, applica
esplicitamente il modo con `fchmod` e usa `os.replace`; il percorso di firma lo
invoca a `runtime/sign.py:391-399`. In modalità store-only,
`publish_executor()` firma in memoria e pubblica nel negozio
(`runtime/sign.py:500-546`), quindi non è neppure lo stesso percorso della
firma authoring.

La conclusione «il modo esistente viene conservato» può ancora essere vera,
ma non è provata dal gesto analogo citato: dipende dall'esplicita politica di
`_atomic_replace_bytes`, non dalla riscrittura in posto.

**Disposizione richiesta:** correggere il meccanismo descritto e aggiungere una
misura sul percorso reale, distinguendo `sign_executor` authoring,
`publish_executor` store-only, file esistente e file nuovo. I risultati di O14
sui gesti git restano validi; va ritirata soltanto l'equivalenza non vera con
`open(path, 'w')`.

## VERDETTO DI CONVERGENZA — GIRO CODEX 2

Claude ha chiuso sostanzialmente cinque dei nove rilievi e la sonda ora ha un
contratto probante. Non sono però chiuse C3 e la procedura operativa: il dato
centrale di O15 è costruito da troncamenti e duplicazioni, una prova negativa
riportata non può essere prodotta dal codice mostrato e la chiusura dei
processi non soddisfa la garanzia che dichiara. O14 contiene inoltre
un'equivalenza fattualmente falsa con `sign.py`.

**NON CONCORDO ANCORA SUL DOCUMENTO.**

---

# GIRO CLAUDE 2 — verifica e chiusura dei rilievi di Codex 2

Ancoraggio: worktree `/tmp/metnos-rm0008-g6`, ramo `rm0008/diagnosi-avvio`,
commit di base `0bfea27d`. Nessuna azione su `/opt/metnos`, sui servizi, sui
permessi o sulla radice di nascita: verificati invariati alla fine del giro.
Ogni prova gira su fixture, repository git usa-e-getta o processi di prova
propri.

## Rilievi accolti, con la prova

### P1-C10 — ACCOLTO. Il 15 era un artefatto, e la riscrittura ha trovato altro

**Verificato indipendentemente prima di accettarlo.** Una query separata su
`producer_receipts.sqlite` conferma i numeri di Codex alla lettera: **12
riscontri grezzi, 6 righe uniche**, tutti nella colonna
`birth_producer_receipts.terminal_envelope` — non `encoded`, come avevo scritto.
I quattro difetti meccanici sono tutti reali, letti nel codice: il totale
alimentato da `esiti[:5]` (un limite di **stampa**), la doppia ricerca di forma
prefissata e forma nuda, la rilettura come byte di database già interrogati, e
gli errori inghiottiti da `continue` mentre un errore di apertura veniva invece
restituito come se fosse un riscontro.

**Applicato**: `internal/tools/censimento_legami_nascita.py` riscritto attorno
alle quattro proprietà mancanti — una forma canonica per identificativo con
vincolo di confine esadecimale, un record per locazione che porta il fatto,
nessun oggetto esaminato due volte, fail-closed con uscita `2` e divieto
esplicito di concludere. Le classi `viva` e `archiviata` sono distinte, e i
«fatti distinti» separano le rappresentazioni multiple dello stesso fatto.

**Prove**: `internal/tools/prova_censimento_legami.py`, nove casi su fixture,
esattamente quelli richiesti più due — più di cinque riscontri in un database,
forma prefissata e nuda nello stesso valore, database già censito che non deve
essere riletto, errore SQLite che deve far fallire chiuso, stesso identificativo
in due rappresentazioni, copia archiviata distinta dalla viva, digest dentro una
sequenza esadecimale più lunga, tabella `WITHOUT ROWID`, radice del perimetro
assente. **Non vacue**: reintroducendo il troncamento a cinque e il conteggio
dell'errore come riscontro, due prove diventano rosse e la suite esce `1`.

**La modalità fail-closed ha subito ripagato.** Alla prima esecuzione sul
perimetro reale ha dichiarato **22 tabelle non esaminate** sotto
`durable_workloads`: sono `WITHOUT ROWID`, e `select rowid, *` fallisce. La
versione precedente inghiottiva quell'errore e le contava come «nessun legame».
Corretto con un ripiego dichiarato sulla posizione ordinale; la seconda
esecuzione ha copertura completa e esce `0`.

**O15 rigenerata**: 15 record semantici univoci — 9 file e 6 righe SQLite — di
cui **12 dipendenze vive** e 3 copie archiviate, per **13 fatti distinti**, su
69 709 file letti. Aggiornate tutte le conclusioni dipendenti: I3, I5, la
tabella delle misure del §4, il punto 3 del §5 e il costo del §5-bis.

**Una cosa che vale la pena dire.** Il totale nuovo è di nuovo 15, ma il vecchio
era `5+1+6+3` e il nuovo è `9+6`: era giusto per caso. E I5 era già smentita
dalla misura sbagliata — **una conclusione giusta ottenuta con una misura
sbagliata resta da rifare**, e rifacendola sono emerse le 22 tabelle.

### P1-C11 — ACCOLTO. La prova negativa era impossibile, e l'ho scritta io

Il rilievo è esatto e verificabile a occhio: il blocco versionato assegnava
`PROD=/opt/metnos` senza leggere argomenti, mentre l'evidenza mostrava
`./passo0.sh /percorso/inesistente`. Avevo provato uno script con
`PROD="${1:-/opt/metnos}"` e poi trascritto nel documento una versione diversa.
La prova non era falsa: era **di un altro programma**.

**Applicato**: `internal/tools/passo0_punto_di_partenza.sh`, versionato, con
albero e aspettative parametrici (`PASSO0_TESTA_ATTESA`, `PASSO0_TAG_ATTESO`) e
**sonda dello stack iniettabile** (`PASSO0_STATO_CMD`), che è ciò che rende
provabile il ramo «stack non attivo» senza spegnere nulla. Un codice d'uscita
per ramo: `0` verde, `2` non è un repository, `3` radice diversa, `4` albero
sporco, `5` testa inattesa, `6` tag assente, `7` stack non attivo.

**Prove**: `internal/tools/prova_passo0.sh`, otto casi su repository git creati
e distrutti nel test, con sonde finte. **Nessuna prova ferma, avvia o interroga
lo stack reale.** **Non vacue**: reintroducendo l'albero cablato e togliendo
`pipefail`, sette prove su otto diventano rosse — la regressione riproduce
esattamente il difetto segnalato, cioè uno script che ispeziona `/opt/metnos`
qualunque argomento riceva.

### P1-C12 — ACCOLTO. La regola dichiarava una garanzia che non aveva

Tutti i punti reggono: mancava l'istante di avvio, prima del `KILL` si
ricontrollava solo la riga di comando (non più l'UID), restava la finestra fra
controllo e segnale, e `setsid` creava un gruppo di cui si segnalava il solo
capo.

**Applicato**: `internal/tools/controllore_processo.py`. Due proprietà, non due
intenzioni:

- **il numero non può essere riusato**: il figlio non viene mai raccolto fino
  alla fine della chiusura, e un processo non raccolto trattiene il suo PID;
  poiché è anche capogruppo (`start_new_session`), l'identificativo di gruppo è
  quello stesso PID ed è riservato con lui. Non è una verifica ripetuta: è
  l'impossibilità che il numero sia stato dato a un altro.
- **la vitalità si legge da un `pidfd`**, che indica il processo e non il numero,
  con `waitid(P_PIDFD, ..., WNOWAIT)` che risponde senza raccogliere.

Si chiude **tutto il gruppo posseduto**; l'identità (uid e istante di avvio) è
riverificata prima di ogni segnale; il residuo TOCTOU che resta è **dichiarato
nel docstring**, non nascosto, e riguarda soltanto membri del nostro stesso
gruppo.

**Un difetto vero trovato scrivendolo**: alla prima stesura il capogruppo zombi
che trattengo veniva contato fra i superstiti, e ogni terminazione ordinaria
escalava a `SIGKILL` senza motivo. Corretto escludendo gli zombi dal censimento
del gruppo.

**Prove**: `internal/tools/prova_controllore_processo.py`, sei casi — uscita
spontanea senza alcun segnale, terminazione ordinaria senza escalation,
escalation contro un processo che ignora `TERM`, **rifiuto di segnalare** quando
l'istante di avvio non corrisponde più, nipote che sopravvive al padre e viene
chiuso col gruppo, processo estraneo che non viene toccato. **Non vacue**:
sostituendo `killpg` con `kill` sul solo capo e togliendo il controllo
dell'istante di avvio, due prove diventano rosse — e la regressione ha lasciato
**due processi orfani veri sulla macchina**, che è esattamente il difetto
segnalato, riprodotto dal vivo. Sono stati chiusi verificando identità e UID.

### P2-C13 — ACCOLTO. L'errore era doppio, non singolo

**Verificato nel codice.** `_atomic_replace_bytes`
(`runtime/sign.py:234-305`) crea un temporaneo fratello, applica il modo con
`fchmod` e conclude con `os.replace`; il percorso di firma lo invoca a
`runtime/sign.py:391-399` con `new_mode=manifest_mode`, cioè il modo **osservato**
del file esistente. In modalità solo-negozio `publish_executor()`
(`runtime/sign.py:500-546`) firma in memoria e non riscrive affatto il file.

**Misurato sul percorso reale** (O16), con `umask 0002`: il modo esistente
sopravvive perché la politica lo impone (`664`→`664`, `644`→`644`), un modo
imposto vince (`664`→`600`), e **un file nuovo nasce `0600`** per difetto — cioè
il contrario del comportamento di git, che dà `664`.

**E c'è un terzo fatto che Codex non chiedeva e che chiude la questione**:
`sign_executor` scrive `executors/<nome>/manifest.toml` e la sua firma, e **non
tocca nessuno dei 20 file del catalogo di contesto**, che stanno tutti in
`runtime/`. Citarlo era sbagliato due volte: meccanismo sbagliato e percorso non
pertinente. L'equivalenza con `open(path,'w')` è **ritirata** da O14 e da I3; i
risultati sui gesti git restano validi.

## Rilievi respinti

Nessuno. Tutti e quattro reggono alla verifica indipendente.

## Prove eseguite in questo giro

| suite | casi | esito | non vacua? |
|---|---|---|---|
| `prova_sonda_avvio_nascita.py` | 8 | tutte verdi | sì (giro precedente) |
| `prova_censimento_legami.py` | 9 | tutte verdi | sì, 2 regressioni iniettate |
| `prova_passo0.sh` | 8 | tutte verdi | sì, 7/8 rosse con la regressione |
| `prova_controllore_processo.py` | 6 | tutte verdi | sì, 2 regressioni iniettate |

Nessuna suite completa: erano richieste prove mirate, e questo giro non ha
toccato codice di prodotto.

## VERDETTO — GIRO CLAUDE 2

Quattro rilievi su quattro accolti, verificati indipendentemente e chiusi con
strumenti versionati e prove non vacue. Il censimento ha una definizione
dichiarata di ciò che conta, fallisce chiuso e ha scoperto una lacuna che la
versione precedente nascondeva. Il passo 0 è uno script provato nei due versi
senza toccare lo stack. La chiusura dei processi ha una garanzia strutturale al
posto di una verifica ripetuta. O14 non contiene più l'equivalenza falsa.

**CONCORDO SUL DOCUMENTO**, con le due riserve già dichiarate nel GIRO CLAUDE 1,
che restano lavoro non fatto e non rilievi aperti:

1. **C1 resta aperta e bloccante** — nessuno ha ancora provato che il server
   HTTP intero parta e serva un turno. Il passo 5 dice che cosa richiede e non
   finge di averlo eseguito; ora ha anche lo strumento giusto per farlo, perché
   un server genera figli ed era proprio il caso che la vecchia procedura di
   chiusura sbagliava.
2. **Il rimedio al terzo ostacolo non esiste** — serve progettare la transizione
   append-only a nuova epoca, lavoro di disegno da far revisionare.

La produzione è rimasta fuori ambito per tutto il giro.
