# Il checkout di sviluppo non è più pubblicabile da un utente normale — 20 settembre 2026

## In breve

`/opt/metnos` è il **checkout sorgente**: il servizio in esercizio gira da
`/var/lib/metnos/executor-birth/releases-v1/`, non da qui. Nel checkout però
**167 file sono illeggibili** per l'utente `roberto`, perché appartengono a
`root` con permessi restrittivi. Le conseguenze si sono accumulate in silenzio:

| Da quando | Che cosa è rotto |
|---|---|
| 25 agosto | `scripts/publish-public.sh` aborta: non può copiare 82 firme e 85 `manifest.lang_state.json` |
| 31 agosto | `./deploy.sh` moriva su `install/data/i18n_seed.sqlite` |
| 31 agosto | 30 prove di `tests/runtime/i18n/test_seed_i18n_gate_keys.py` e 2 di `tests/runtime/durable_workloads/` rosse |

Nessuno se n'era accorto perché ogni sintomo sembra un errore diverso.

## Le due cause, distinte

### 1. `install/data` è `0700 root`

La directory contiene sia il seme tracciato in Git (`i18n_seed.sqlite`, modo
0644) sia quattro alberi di dati **privati** scritti da un processo root:
`turns`, `cost`, `executors`, `introvertiva`, tutti `0700`.

Due difetti sovrapposti:

- dati privati di esercizio vivono dentro una directory del repository, dove
  non dovrebbero stare;
- il seme tracciato è diventato irraggiungibile perché la directory è stata
  chiusa per proteggerli.

**Non si risolve con `chmod 755`**: esporrebbe `turns`, cioè le conversazioni.
Il rimedio minimo e sicuro è `chmod 711`, che rende attraversabile il percorso
senza renderlo elencabile; ogni figlio privato continua a proteggersi da solo
col proprio `0700`. Il rimedio vero è spostare quei quattro alberi fuori dal
checkout.

### 2. Firme e stato linguistico degli executor sono `0600 root`

82 file `manifest.toml.sig` e 85 `manifest.lang_state.json` sono stati
riscritti da un processo root il 25 agosto alle 08:54 con modo `0600`. Sono
anche il motivo per cui `git status` li mostra tutti modificati senza che
nessuno li abbia toccati a mano.

Qui **non** propongo un rimedio automatico: sono firme, e allargarne i permessi
o riscriverle in blocco è una decisione che spetta a Roberto. Il ripristino
naturale è `chown` all'utente proprietario del checkout, non `chmod`.

## Che cosa è stato corretto oggi

Il codice aveva un difetto vero, indipendente dai permessi. `runtime/i18n.py`
dichiarava in un commento che un catalogo utente valido deve restare usabile
«anche se il seed del checkout è temporaneamente assente o illeggibile», ma
gestiva solo il caso assente: `Path.is_file` riporta un percorso mancante come
falso e invece **rilancia** l'errore di permesso. Corretto con
`_seed_is_reachable`, due prove di regressione e l'eccezione del chiamante
allargata. Commit `555efd44`.

Con quella sola correzione `./deploy.sh` è tornato a completare e la
documentazione pubblica è stata distribuita. Le 32 prove rosse e la
pubblicazione del codice restano invece bloccate dai permessi: nessuna di esse
passa attraverso il modulo corretto.

## Decisione richiesta

1. `sudo chmod 711 /opt/metnos/install/data` — sblocca 30 prove e rende di
   nuovo affidabile il deploy anche senza la rete di sicurezza del codice.
2. `sudo chown -R roberto:roberto /opt/metnos/executors` — sblocca la
   pubblicazione pubblica e le 2 prove restanti. Da valutare, perché tocca le
   firme.
3. Spostare `turns`, `cost`, `executors` e `introvertiva` fuori da
   `install/data`: è la causa per cui la directory è stata chiusa.
