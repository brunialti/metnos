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
percorso previsto per rifarlo. Rimossi tutti e tre, l'avvio arriva in fondo in
0,9 secondi: **non c'è un quarto ostacolo.**

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

**I3. Un `chmod` è un cerotto, non una correzione.** Con `umask 0002`, il
prossimo `git checkout`, `git merge` o `sign.py publish` ricrea file `664`, e il
servizio smette di avviarsi — senza che nessuno colleghi le due cose.
*Confidenza: alta sul meccanismo, media su quali comandi esattamente ricreino i
modi* (git non traccia i bit oltre a quello d'esecuzione, quindi un file
riscritto prende `0666 & ~umask`).

**I4. Il terzo ostacolo è strutturale, non un incidente.** L'insieme preparato
appunta le impronte della distribuzione, la distribuzione cambia a ogni commit,
e non esiste un percorso previsto per riallineare i due. Chiunque prepari
l'insieme e poi committi qualunque cosa fra i 20 file del catalogo rompe
l'avvio. *Confidenza: alta sul fatto (O4+O7+O8), media sull'intenzione di
disegno* — può darsi che il disegno preveda di preparare l'insieme come
**ultimo** atto dell'installazione, e che nessuno abbia scritto il percorso di
ri-preparazione perché F4 non è chiusa.

**I5. Rifare l'insieme da zero costa poco su questa macchina, oggi.** Le 23
ricevute sono terminali e anteriori all'insieme corrente; la produzione non
legge mai l'insieme; nessun executor è ancora nato dalla porta. *Confidenza:
media* — è la conclusione che mi fido di meno, perché non ho misurato che cosa
altro sia legato alla radice d'autore.

**I6. Non c'è un quarto ostacolo.** *Confidenza: media* — O6 lo prova per
`require_birth_runtime_before_workers()` in un processo isolato, **non** prova
che il server HTTP intero parta, né che i turni funzionino.

---

## 4. (c) Quale misura mi smentirebbe — decisa prima di eseguirla

Per I1, I2, I4 le misure smentitrici sono già state eseguite e sono O3, O4, O7,
O8: le dichiaro qui perché siano contestabili, non perché siano da rifare.

Restano **aperte**, e vanno eseguite prima di toccare la produzione:

| # | inferenza | misura che la smentirebbe | dove |
|---|---|---|---|
| C1 | I6 «non c'è un quarto ostacolo» | avviare il **server HTTP completo** dalla replica su una porta libera, con le tre correzioni, e chiedere un turno reale | copia |
| C2 | I3 «il `chmod` è fragile» | dopo il `chmod`, eseguire un `git checkout` di un file del catalogo e rileggerne il modo: se resta `644`, I3 è sbagliata | copia |
| C3 | I5 «rifare l'insieme costa poco» | censire ogni cosa che nomina il `set_id` corrente o l'impronta d'inventario dell'autore, fuori dalla radice di nascita | lettura |
| C4 | I4 «non esiste percorso di ri-preparazione» | cercare nel gruppo 2 della RM-0008 se la ri-preparazione è un atto **previsto e non ancora scritto**, o **deliberatamente vietato** | lettura |

**C4 è la più importante**: decide se il rimedio al terzo ostacolo è codice
nuovo o una procedura d'esercizio. Se la ri-preparazione è deliberatamente
vietata, allora l'unico ordine ammesso è *congelare la distribuzione, poi
preparare*, e va rifatto l'insieme sull'albero fuso definitivo.

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

### Ostacolo 3 (insieme stantio) — due strade, e una è mia da proporre, non da scegliere

- **(A) Ri-preparare come atto previsto**: aggiungere al provisioner il percorso
  che oggi manca — riconoscere che la distribuzione installata non produce più
  il materiale descritto, e ricostruire materiale e insieme conservando
  l'identità d'autore. È codice nell'area F4 e **deve passare dal ciclo
  avversariale sul codice**, come dice il §6 della consegna.
- **(B) Ordine d'esercizio**: nessun codice nuovo. Si fonde e si congela la
  distribuzione, e **solo dopo** si prepara l'insieme, da zero, sull'albero
  definitivo. Costa la radice d'autore corrente e le 23 ricevute terminali
  (O9), e resta fragile allo stesso modo al commit successivo.

Non scelgo io fra (A) e (B): dipende da C4.

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
5. **Dopo ogni comando interrotto o andato in timeout**, cerca i residui:
   `ps -eo pid,ppid,pcpu,etime,args --sort=-pcpu | awk 'NR==1 || ($2==1 && $3>50)'`
   Se compare un processo con PPID=1 che è nostro, confermalo con
   `tr '\0' ' ' < /proc/<pid>/cmdline` e poi `kill -9 <pid>`.
6. **Ogni misura dichiara le proprie radici prima di misurare.** Se non vedi
   stampato `PATH_RUNTIME`, non stai misurando quello che credi.

### Passo 0 — Verifica il punto di partenza

```bash
git -C /opt/metnos status --porcelain | wc -l        # atteso: 0
git -C /opt/metnos log -1 --format=%h                # atteso: 14ea7179
systemctl --user is-active metnos.target             # atteso: active
git -C /opt/metnos tag | grep pre-fusione-31-8-2026  # atteso: la riga esiste
```

Se `status` non è 0: **fermati**. C'è lavoro non committato di un'altra
sessione, e va messo al riparo prima (è già successo: 47 file).

### Passo 1 — Costruisci l'apparato di prova

```bash
S=<SCRATCH>
mkdir -p "$S"
rsync -a --exclude='.git' /tmp/metnos-prova-fusione/ "$S/replica/"
mkdir -p "$S/cfg" "$S/state" "$S/data"
cp -a ~/.config/metnos/birth "$S/cfg/birth"
chmod 775 "$S/replica/runtime"
find "$S/replica/runtime" -maxdepth 1 -type f -name '*.py' -exec chmod 664 {} +
```

Atteso: `stat -c '%a' "$S/replica/runtime"` stampa `775`.

`cp -a` è obbligatorio: conserva i permessi, e i permessi sono l'oggetto della
misura.

### Passo 2 — Riproduci il guasto prima di correggerlo

La sonda è già scritta e versionata: `internal/tools/sonda_avvio_nascita.py`.
Non riscriverla. Copiala e basta:

```bash
cp /tmp/metnos-rm0008-g6/internal/tools/sonda_avvio_nascita.py "$S/sonda.py"
```

Poi:

```bash
cd "$S" && timeout 300 env \
  METNOS_INSTALL_ROOT="$S/replica" METNOS_USER_CONFIG="$S/cfg" \
  METNOS_USER_STATE="$S/state" METNOS_USER_DATA="$S/data" \
  PYTHONPATH="$S/replica" \
  /opt/metnos/.venv/bin/python sonda.py
```

Atteso: cancello 1 `ok`, cancello 2 `FALLITO … birth_provisioning_acl_unsafe`.

Se il cancello 2 passa: **fermati**. Non stai misurando la produzione — quasi
certamente la replica ha `runtime` a `755` e il passo 1 è andato storto.

Usa `/opt/metnos/.venv/bin/python`, non `python3`: con l'interprete di sistema
manca `tomlkit` e la sonda muore prima di misurare.

### Passo 3 — Verifica i tre ostacoli in sequenza, in copia

```bash
chmod g-w "$S/replica/runtime"          # atteso poi: cancello 2 ok, cancello 2b rifiuta 22 letture
# poi porta a 644 i 20 file distinti del catalogo
# atteso poi: cancello 2b legge 22, cancello 3 FALLITO birth_prepared_set_mismatch
```

L'elenco dei file lo dà il catalogo stesso, mai una lista copiata a mano:

```bash
cd "$S/replica" && /opt/metnos/.venv/bin/python - <<'PY'
import ast, os
src = open('runtime/executor_birth_context_v1.py').read()
for n in ast.parse(src).body:
    if isinstance(n, ast.AnnAssign) and getattr(n.target, 'id', '') == 'CONTEXT_CATALOG_V1':
        cat = ast.literal_eval(n.value); break
for f in sorted({f for _, _, files, _ in cat for f in files}):
    print(f)
PY
```

Atteso: 20 nomi. Se ne stampa un numero diverso, il catalogo è cambiato e
**questo documento è scaduto**: fermati e riferisci.

### Passo 4 — Non andare oltre senza il verdetto su C4

A questo punto hai riprodotto tutto in copia e non hai toccato niente. **Il
passo successivo dipende dalla scelta fra (A) e (B) del §5**, che dipende da C4,
che è una lettura di disegno e non una misura. Se non hai quel verdetto:
**fermati e riferisci.** Non applicare il cerotto «intanto», perché rimette in
produzione un guasto che si ripresenta muto.

### Passo 5 — Solo dopo il verdetto: la prova d'avvio, ancora in copia

Prima di qualunque cosa su `/opt/metnos`, la correzione scelta va provata sulla
replica con `require_birth_runtime_before_workers()` e con il **server HTTP
completo su una porta libera** (misura C1). Le suite verdi non contano: nessuna
suite avvia il server con le radici vere. È l'errore che è costato dieci minuti
di servizio giù.

### Passo 6 — Solo dopo che l'avvio è provato: la produzione

Nell'ordine, senza saltarne nessuno:

1. Rileggi il punto di ritorno (tag `pre-fusione-31-8-2026`).
2. Ferma lo stack: `systemctl --user stop metnos.target`.
3. Applica la correzione già provata in copia.
4. Prova l'avvio **fuori dal servizio**, con le radici di produzione dichiarate
   ed esplicite, e leggi le righe che le stampano.
5. Solo se il passo 4 è verde: `systemctl --user start metnos.target`.
6. Verifica: `systemctl --user is-active metnos.target metnos-http.service` e
   **un turno reale** su `/agent/turn`.
7. Cerca i residui (regola 5).

Se il passo 4 fallisce, **non** avviare lo stack: torna al punto di ritorno.

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
- **Non toccare la radice di nascita reale** finché la scelta fra (A) e (B) non è
  presa: le prove si fanno su `cp -a` della radice, non sulla radice.

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

Non «il codice è giusto?», ma:

1. **I1 regge?** Il secondo ostacolo è davvero lo stesso bit di permesso sui
   file, e non il modello dei ruoli? (O3 dice di sì; contraddice la consegna
   precedente.)
2. **I4 regge, e con quale intenzione di disegno?** L'insieme preparato appunta
   la distribuzione e nessuno può riallinearli: è una lacuna da colmare (A) o un
   ordine d'esercizio da rispettare (B)? È la domanda C4, ed è quella che decide
   il lavoro.
3. **I5 è troppo comoda?** Ho concluso che rifare l'insieme costa poco senza
   censire che cosa dipende dalla radice d'autore. È la mia inferenza più
   debole, e chiede la misura C3.
