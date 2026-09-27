# Servizi esistenti / Existing services

## Italiano

Il file facoltativo `~/.config/metnos/services.toml` indica quali servizi
esistenti riusare. Può puntare a un servizio sulla stessa macchina oppure su
un altro computer raggiungibile. Non viene cercato nessun server nella rete.

- File assente o vuoto: tutti i componenti vengono predisposti localmente.
- File parziale: solo i servizi indicati vengono riusati; tutti gli altri
  vengono predisposti localmente.
- Il percorso amministrato non accetta `--skip`: il catalogo richiede tutti
  i servizi previsti, locali oppure indicati nel file.

Preparazione, **dopo il download del repository e prima dell'installazione**:

```bash
install -d -m 700 ~/.config/metnos
install -m 600 install/services.example.toml ~/.config/metnos/services.toml
${EDITOR:-nano} ~/.config/metnos/services.toml
bash install/bootstrap.sh --check
```

Decommenta soltanto le sezioni necessarie e sostituisci gli indirizzi di esempio.
Una sezione attiva deve avere `url`; una sezione vuota è un errore. Per esempio:

```toml
[llm]
url = "http://models.example.test:8080"
frontier = false

[vlm]
url = "http://vision.example.test:8081"
```

In questo esempio modello testuale e visione sono riusati; ricerca, geografia,
browser ed embedder BGE-M3 vengono installati localmente. Il file non modifica
il percorso amministrativo delle autorità né l'accettazione iniziale.

| Sezione | Servizio richiesto | Verifica |
|---|---|---|
| `llm` | llama-server con API compatibile OpenAI | `/v1/models` |
| `vlm` | llama-server con modello visivo e proiettore caricati | `/v1/models` |
| `searxng` | SearXNG con formato JSON abilitato | ricerca JSON |
| `photon` | Photon con indice pronto | ricerca geografica |
| `playwright` | servizio browser Metnos | `/health` con Chromium |

Per `llm` e `vlm`, `model` è l'identificatore esatto restituito dal servizio.
Puoi ometterlo se il catalogo contiene un solo modello. La verifica non esegue
inferenza e non certifica la qualità del modello: il collaudo chat/foto resta
necessario. `frontier` è ammesso soltanto in `[llm]` ed è `false` se omesso;
`true` abilita esplicitamente il ruolo cloud Anthropic, che richiede credenziali
separate e può comportare costi. Senza profilo, il cloud resta comunque una
scelta esplicita; `--yes` non lo abilita e un errore locale non lo seleziona.

Gli indirizzi devono usare HTTP o HTTPS, senza credenziali, query o frammenti.
Questo profilo non gestisce token, comandi o variabili arbitrarie. Il servizio
deve essere raggiungibile dall'ambiente dove si installa Metnos: dentro un
container `localhost` indica il container. Le porte devono essere esposte dal
server alla rete prevista; il file non apre porte né avvia processi remoti.
L'embedder BGE-M3 resta locale: non esiste qui un'opzione per renderlo remoto.

L'installer legge il file senza nuove domande sui servizi. Controlla gli
indirizzi prima dei download della fase 2 e di nuovo prima dell'avvio della
fase 5. Un errore di sintassi, un servizio incompatibile o irraggiungibile
interrompono la procedura; nessuna copia locale o sostituzione cloud automatica.
Un componente locale che non diventa utilizzabile interrompe ugualmente la
fase: correggi il prerequisito prima di riprendere.

Il profilo selezionato viene incluso nella distribuzione firmata. Il
coordinatore amministrativo genera le unità di sistema con i soli parametri
ammessi. I file privati `services.env` e `services-llm-tiers.toml` conservano
le associazioni applicative. Non modificarli direttamente.

Per riprendere un’installazione usa lo stesso `services.toml`: dopo la fase 2,
un profilo diverso interrompe la ripresa. Cambiare la topologia di un’istanza
già installata richiede un nuovo rilascio amministrato e una finestra di
riavvio. Ripetere soltanto le fasi 2 e 5 non basta. Le unità locali incompatibili
non vengono sostituite automaticamente.

Se imposti `METNOS_USER_CONFIG`, il file e le configurazioni generate vivono
in quella directory invece di `~/.config/metnos`. Un aggiornamento del codice
non sostituisce il profilo personale.

## English

The optional `~/.config/metnos/services.toml` file selects existing services
on this machine or another reachable host. There is no network discovery.
An absent or empty file provisions every component locally. A partial file
reuses only the listed services and provisions every other component locally.
The managed flow requires all catalog services, either local or listed in the
profile; it does not accept `--skip`.

After cloning the repository and **before installation**, copy
`install/services.example.toml` into `~/.config/metnos/services.toml` with
permissions `0600`, uncomment only the services to reuse, replace their URLs,
then run `bash install/bootstrap.sh --check`. The shell commands above apply
to both languages. Empty active sections are invalid: each needs a `url`.

Supported sections are `llm` and `vlm` (llama-server `/v1/models`), `searxng`
(JSON search enabled), `photon` (ready geographical index), and `playwright`
(Metnos browser service reporting Chromium health). For either model service,
`model` must exactly match a catalog ID; it can be omitted only when the server
lists one model. Vision also needs its visual model and projector loaded.
Protocol checks perform no inference and do not certify model quality: test
an actual chat turn and image separately.

Only `[llm]` accepts `frontier`. It defaults to `false`; `true` explicitly
enables the paid Anthropic cloud tier and requires separate credentials.
Without a profile, cloud remains explicit opt-in, is disabled by `--yes`, and
is never selected automatically after local provisioning fails.

URLs use HTTP(S), without credentials, query strings or fragments. Tokens,
commands and arbitrary environment variables are not supported here. The
server must already accept connections from the installation environment.
Inside a container, `localhost` means the container itself. The file neither
opens firewall ports nor starts remote processes. BGE-M3 embeddings remain
local. Initial consent and administrator authority provisioning still apply.

The installer reads the file without additional service-choice prompts. It
checks configured endpoints before phase-2 downloads and again before phase-5
startup. An invalid profile or unavailable/incompatible server stops the
installation, without a local or cloud replacement. An unusable local
component also stops its phase; fix its prerequisite before resuming.

The selected profile is included in the signed distribution. The administrative
coordinator generates system units with only the permitted parameters. Private
`services.env` and `services-llm-tiers.toml` retain application bindings; do not
edit these generated files.

Resume with the same `services.toml`. A different profile after phase 2 stops
resumption. Changing an installed topology requires a new administrative
release and a restart window; repeating phases 2 and 5 alone is insufficient.
Incompatible local units are never replaced automatically. The managed flow
does not accept `--skip`.

`METNOS_USER_CONFIG` overrides the configuration directory for the profile
and its generated files. Updating source code does not overwrite the user's
profile. Shared servers retain responsibility for their lifecycle and share
their load across clients; they do not grant access to another instance's data.
