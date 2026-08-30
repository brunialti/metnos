# RM-0008 — pubblicazione da worktree in produzione e cancello chiuso

> Consegna del 30 agosto 2026. Guasto risolto in produzione; resta un blocco
> che impedisce ogni manutenzione degli executor.

## 1. Il guasto

Il 30/08 alle 12:58 una pubblicazione partita dal worktree
`/tmp/metnos-rm0008-a-only` ha scritto **21 nuove generazioni** nel magazzino
contratti reale: `~/.local/state/metnos/contract-publications/v1`.

Il magazzino non sta nel repository ma in `~/.local/state`, quindi è unico per
tutta la macchina: una pubblicazione da un worktree temporaneo finisce in
produzione.

Ogni generazione dichiara `[code] files = ["implementation.py.src"]`, il
formato autoconsistente del worktree. Quel file esiste **solo** nel worktree:

```
/tmp/metnos-rm0008-a-only/runtime/builtin_executor_contracts/<nome>/implementation.py.src
```

scritto alle 12:58. Per `admin` la sua sha256 è
`da327a88f275b29738b7f0ed34758b30cc79493e2ccc5783b7b732e92aeddf7f`, cioè
esattamente il digest che la generazione attiva pretendeva.

In `/opt/metnos` quei file non esistono: i manifest lì dichiarano ancora il
formato precedente (`admin` → `../../system/admin.py`). Il loader ha quindi
rifiutato tutti e 21 i contratti con `code_file_missing`, e il catalogo vivo è
sceso **da 122 a 101** executor. Fuori dal catalogo, fra gli altri: `admin`,
`classify_entries`, `describe_entries`, `extract_entries`, `write_entries`,
`set_preferences`, `list_tasks`, `create_tasks`, `find_entries`, `start_lre`.
Metnos non sapeva più eseguire un comando di sistema.

`~/.local/state/metnos/contract-publications.audit.jsonl` **non esisteva
affatto**: quella pubblicazione non è passata dalla via audita.

## 2. Già fatto

In `/opt/metnos`, ramo `session/detection-lexicon-i18n`.

**Puntatori riportati indietro.** I 21 contratti sono tornati alla generazione
precedente (25/08 08:54), che corrisponde esattamente al codice presente in
`/opt/metnos`. Catalogo di nuovo a 122, zero rifiuti all'avvio, turno reale
che esegue un comando di sistema verificato.

**Commit `5a0601eb`.** `_require_catalog_name_candidate` in
`runtime/contract_store.py` prendeva il nome di ogni contratto installato da
`current_contract`, che autentica anche il legame col codice: bastava **un**
contratto col carico assente per bloccare la riparazione di **tutti** gli
altri, in qualunque ordine. Ora, quando il carico è assente — solo le tre
classi `code_file_missing` / `code_file_invalid` / `code_file_unreadable` — il
nome si legge dalla base immutabile autenticata via
`_load_generation_for_commit`, come già avviene per il predecessore ritirato.
Il nome resta **riservato**. Un digest che non corrisponde resta fatale
ovunque. **Manca la prova di non regressione**: da scrivere.

**Commit `ee4b7443`** (stesso pomeriggio, causa indipendente). Il watchdog
dello stack teneva il lucchetto di ammissione al catalogo per tutto
`systemctl restart` più l'attesa di prontezza: il server appena avviato non
riusciva a leggere il proprio catalogo entro i 5 s, non diventava pronto e la
quarantena spegneva l'intero stack (da fuori: 502 di Cloudflare). Ora
`catalog_reconcile_lock` restituisce i due confini separati e `restart()`
rilascia quello del catalogo prima di passare a systemd.

## 3. Il blocco che resta

La pubblicazione è chiusa e la via nuova non è aperta:

```
$ python3 runtime/sign.py publish executors/<name>
RuntimeError: publish is unavailable in STORE_ONLY; submit an Executor Birth intent
```

e a ogni avvio il server registra:

```
WARNING executor_birth_bootstrap Birth runtime not provisioned yet
(birth_bootstrap_config_unavailable): continuing without the sealed
authority, as required before the RM-0008 cutover
```

Finché resta così **nessun executor può essere corretto**. Non è una
scomodità: è un blocco totale sulla manutenzione.

## 4. Cosa serve, in ordine

1. Rimettere in funzione la nascita governata dei contratti su questa
   macchina, oppure indicare quale procedura sostituisce oggi
   `sign.py publish`. È il prerequisito di tutto il resto.
2. Impedire che un worktree possa pubblicare nel magazzino condiviso di
   produzione. Vale anche per i test.
3. Capire perché quella pubblicazione non ha lasciato nulla nel registro di
   controllo.

## 5. Due difetti pronti, bloccati dal punto 4.1

### A — `find_packages` misura l'ambiguità sulle righe, non sull'identità

`executors/find_packages/find_packages.py`, `_probe_windows`:

```python
if by_name and len(rows) > 1:
    hit["also_matched"] = [...]
else:
    resolved_id = _launch_identity(pkg_id)
```

winget restituisce spesso più righe per lo stesso programma (installazione per
utente e per macchina). Se tutte portano alla **stessa** identità di avvio la
risposta è unica e `resolved_id` va emesso; identità **diverse** sono la vera
ambiguità. Senza `resolved_id`, `run_processes` rifiuta con
`incomplete_vector_projection` sull'argomento `programs`, che dichiara
`from_entries_key = "resolved_id"` e `from_entries_complete = true`.

Turno reale che lo dimostra: **`6f90e154`**, «avvia dropbox su pc-roberto».
`find_packages` trova Dropbox 268.4.4072 su PC-ROBERTO con
`also_matched=["Dropbox"]` e **senza** `resolved_id`; il piano si ferma lì.

### B — dispositivi accoppiati riconosciuti per nome dentro un argomento

`runtime/target_device.py` risolve già «pc-roberto» come **destinatario**
dell'esecuzione (nel turno `6f90e154` `target_device` risulta correttamente
`PC-ROBERTO`). Ma «fai ping a pc-roberto» chiede «ping a chi?», perché lì il
nome è il **valore di un argomento**, non il destinatario.

Forma generale richiesta da Roberto — nessun elenco di nomi, nessun caso
particolare: l'argomento **dichiara nel manifest** di accettare un'identità di
dispositivo e il runtime la risolve dal registro degli accoppiati, esattamente
come `run_processes.programs` dichiara `from_entries_key`. Richiede una
modifica di manifest, quindi ripubblicazione.

## 6. Stato verificato nel ramo RM-0008

I punti 4.2 e 4.3 sono risolti nel ramo. Il magazzino rifiuta una mutazione
produttiva se il sorgente caricato non coincide con l'installazione selezionata
esplicitamente; il registro di controllo e' obbligatorio prima della prima
scrittura produttiva e deduplica in modo stabile i ritentativi. La fixture
portatile congelata e' tornata byte-identica al baseline: l'isolamento delle
prove non dipende da una modifica locale della fixture.

Il difetto A e' corretto e provato. Il difetto B dispone ora del risolutore
generale, owner-scoped e dichiarato dal manifesto; non deduce indirizzi o altri
attributi di rete. L'attivazione della dichiarazione nel contratto `admin`
resta intenzionalmente non firmata finche' il punto 4.1 non e' disponibile.

Per il punto 4.1 il materiale predisposto e' presente, ma una prova isolata e
senza accesso allo stato operativo si arresta prima della pubblicazione con
`birth_prepared_set_mismatch`: l'insieme autenticato misura una distribuzione
precedente. Il provisioner corrente, quando trova `prepared-v1.json`, esegue
soltanto `_inspect_installed_v1()`; non possiede una transizione di upgrade.
Questa non e' assenza di chiavi e non autorizza il ripristino di
`sign.py publish`.

La procedura sostitutiva per un builtin e':

```text
python3 scripts/generate_builtin_executor_contracts.py --sign --only <nome>
```

`--only` restringe il catalogo mantenendo lo stesso Producer Birth, la stessa
osservazione, revisione, ricevuta e pubblicazione. Non crea una seconda
autorita'. Prima del completamento G6-D la procedura rifiuta correttamente
l'insieme misurato obsoleto; G6-D deve comporre e rileggere l'aggiornamento
della distribuzione e del coordinatore prima che questa facciata possa essere
usata sull'installazione. Fino ad allora nessun manifesto firmato viene
modificato direttamente.
