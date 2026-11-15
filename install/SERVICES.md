# Servizi esistenti / Existing services

[Italiano](#italiano) · [English](#english)

## Italiano

Il file facoltativo `~/.config/metnos/services.toml` dice all’installer quali servizi che hai
già vanno riusati, su questa macchina o su un’altra raggiungibile. Ciò che non è nel file
viene installato in locale. Nessun server viene cercato nella rete.

### Come si prepara

Dopo aver scaricato il repository e **prima di installare**:

```bash
install -d -m 700 ~/.config/metnos
install -m 600 install/services.example.toml ~/.config/metnos/services.toml
${EDITOR:-nano} ~/.config/metnos/services.toml
bash install/bootstrap.sh --check
```

Togli il commento solo alle sezioni che ti servono e sostituisci gli indirizzi d’esempio:

```toml
[llm]
url = "http://models.example.test:8080"
frontier = false

[vlm]
url = "http://vision.example.test:8081"
```

Qui modello di testo e visione sono riusati; ricerca, geografia e browser sono installati in
locale.

### Sezioni ammesse

| Sezione | Servizio richiesto | Come viene verificato |
|---|---|---|
| `llm` | llama-server con API compatibile OpenAI | `/v1/models` |
| `vlm` | llama-server con modello visivo e proiettore caricati | `/v1/models` |
| `searxng` | SearXNG con il formato JSON abilitato | una ricerca JSON |
| `photon` | Photon con l’indice pronto | una ricerca geografica |
| `playwright` | il servizio browser di Metnos | `/health`, con il browser pronto |

| Chiave | Dove | Significato |
|---|---|---|
| `url` | ogni sezione, obbligatoria | Indirizzo HTTP o HTTPS, senza credenziali, parametri o frammenti. |
| `model` | `llm`, `vlm` | Nome esatto restituito dal servizio. Si può omettere se il servizio elenca un solo modello. |
| `frontier` | solo `llm` | `false` se omesso. `true` abilita il ruolo cloud a pagamento di Anthropic, che richiede credenziali separate. |

### Regole

- File assente o vuoto: tutto in locale. Una sezione attiva senza `url` è un errore.
- I vettori di testo BGE-M3 restano sempre locali.
- Il servizio deve già accettare connessioni dall’ambiente in cui installi. Dentro un
  contenitore `localhost` è il contenitore. Il file non apre porte e non avvia processi
  remoti; non gestisce token, comandi o variabili.
- L’installer non fa altre domande sui servizi. Controlla gli indirizzi prima degli
  scaricamenti della fase 2 e di nuovo nella fase 5. Un errore nel file, un servizio
  incompatibile o irraggiungibile fermano l’installazione: niente copia locale e niente
  cloud al suo posto. Anche un componente locale che non parte ferma la fase.
- Il cloud resta una scelta esplicita: `--yes` non lo abilita e un errore locale non lo
  seleziona.
- La verifica non esegue il modello e non ne certifica la qualità: prova tu una richiesta
  in chat e una foto.
- `--skip` non è accettato: ogni servizio è locale oppure dichiarato qui.
- Per riprendere un’installazione usa lo stesso file: dopo la fase 2 un profilo diverso
  ferma la ripresa.
- Il profilo entra nell’installazione firmata. `services.env` e `services-llm-tiers.toml`
  sono generati: non modificarli. Cambiare i servizi di un’istanza già installata richiede
  un nuovo rilascio amministrato e una finestra di riavvio; ripetere le fasi 2 e 5 non basta.
- Le unità locali diverse da quelle attese non vengono sostituite.
- Con `METNOS_USER_CONFIG` il file e quelli generati stanno in quella directory. Un
  aggiornamento del codice non sostituisce il tuo profilo.
- Un server condiviso resta responsabile del proprio ciclo di vita e divide il carico fra i
  suoi client; non dà accesso ai dati di un’altra istanza.

## English

The optional file `~/.config/metnos/services.toml` tells the installer which services you
already run should be reused, on this machine or on another reachable one. Whatever is not in
the file is installed locally. No server is searched for on the network.

### How to prepare it

After cloning the repository and **before installing**:

```bash
install -d -m 700 ~/.config/metnos
install -m 600 install/services.example.toml ~/.config/metnos/services.toml
${EDITOR:-nano} ~/.config/metnos/services.toml
bash install/bootstrap.sh --check
```

Uncomment only the sections you need and replace the example addresses:

```toml
[llm]
url = "http://models.example.test:8080"
frontier = false

[vlm]
url = "http://vision.example.test:8081"
```

Here the text model and vision are reused; search, geocoding and browser are installed
locally.

### Allowed sections

| Section | Required service | How it is checked |
|---|---|---|
| `llm` | llama-server with an OpenAI-compatible API | `/v1/models` |
| `vlm` | llama-server with a visual model and projector loaded | `/v1/models` |
| `searxng` | SearXNG with the JSON format enabled | a JSON search |
| `photon` | Photon with a ready index | a geographical search |
| `playwright` | the Metnos browser service | `/health`, with its browser ready |

| Key | Where | Meaning |
|---|---|---|
| `url` | every section, required | HTTP or HTTPS address, without credentials, query string or fragment. |
| `model` | `llm`, `vlm` | Exact name returned by the service. It can be omitted when the service lists one model. |
| `frontier` | `llm` only | `false` when omitted. `true` enables the paid Anthropic cloud tier, which needs separate credentials. |

### Rules

- Absent or empty file: everything local. An active section without `url` is an error.
- BGE-M3 embeddings always stay local.
- The service must already accept connections from where you install. Inside a container,
  `localhost` is the container. The file opens no port and starts no remote process; it
  carries no tokens, commands or variables.
- The installer asks no further question about services. It checks the addresses before the
  phase-2 downloads and again in phase 5. A mistake in the file, or an incompatible or
  unreachable service, stops the installation: no local copy and no cloud replacement. A
  local component that does not start also stops its phase.
- Cloud stays an explicit choice: `--yes` does not enable it and a local failure never
  selects it.
- The check runs no inference and does not certify model quality: try a real chat request
  and an image yourself.
- `--skip` is not accepted: every service is local or declared here.
- To resume an installation use the same file: after phase 2 a different profile stops the
  resume.
- The profile becomes part of the signed installation. `services.env` and
  `services-llm-tiers.toml` are generated: do not edit them. Changing the services of an
  installed instance needs a new administrative release and a restart window; repeating
  phases 2 and 5 is not enough.
- Local units that differ from the expected ones are never replaced.
- With `METNOS_USER_CONFIG` the file and the generated ones live in that directory. A code
  update does not overwrite your profile.
- A shared server stays responsible for its own lifecycle and shares its load across its
  clients; it gives no access to another instance's data.
