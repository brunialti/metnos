# Installare Metnos

Questa guida descrive il percorso supportato per installare Metnos su una
macchina Linux con systemd. L’installazione del programma e l’ambiente Python
sono condivisi dalla macchina; configurazione, credenziali, sessioni e dati
restano separati per ciascun utente.

## Requisiti

Servono:

- Python 3.12 o successivo;
- Git e accesso a Internet durante il primo avvio;
- almeno 8 GB liberi, oltre allo spazio richiesto dai modelli scelti;
- una sessione utente systemd;
- `libstdc++` 11 o successiva e `libgomp` per i modelli ONNX.

Alcune capacità richiedono programmi di sistema aggiuntivi. Per esempio,
Tesseract e Poppler servono per l’OCR; Xvfb serve per il browser grafico Side.
Il manifest d’installazione contiene l’inventario completo per Debian e Ubuntu.

Una GPU non è obbligatoria. I livelli LLM possono usare un motore locale su CPU,
un endpoint compatibile su un’altra macchina oppure un servizio frontier. La
qualità e la latenza dipendono dai modelli assegnati ai livelli.

## Procedura supportata

Il solo punto d’ingresso supportato è `install/bootstrap.sh`:

```bash
git clone https://github.com/brunialti/metnos.git
cd metnos
bash install/bootstrap.sh
```

Lo script individua Python, crea l’ambiente virtuale nella directory
`<installazione>/.venv`, installa le dipendenze e avvia l’installatore in sei
fasi. L’ambiente virtuale appartiene all’installazione: non viene creato nella
directory dati di un utente e non dipende dal suo nome.

Per controllare prima i requisiti:

```bash
bash install/bootstrap.sh --check
```

Con questa forma il bootstrap può creare o aggiornare `.venv` prima del
controllo; non avvia però le fasi applicative e non crea configurazioni,
credenziali o dati di Metnos. Se `.venv` esiste già, il controllo diretto è:

```bash
./.venv/bin/python -m install --check
```

## Le sei fasi

| Fase | Operazione | Risultato principale |
|---:|---|---|
| 1 | Preparazione | controlli preliminari, dipendenze Python e directory utente |
| 2 | Infrastruttura AI | embedder BGE-M3, collegamenti dei livelli LLM e sidecar scelti |
| 3 | Codice e cataloghi | verifica del sorgente, database iniziali, catalogo i18n, pubblicazione immutabile di tutti i contratti executor installati e non ritirati, compresi quelli degli skill disattivati, e catalogo Tutor verificato |
| 4 | Dati sensibili | chiave amministrativa e credenziali cifrate |
| 5 | Servizi | unità systemd dell’utente, target integrato e controllo di salute HTTP |
| 6 | Primo accesso | scelta delle capacità, collegamento amministrativo temporaneo e riepilogo |

Ogni fase conclusa scrive un marcatore in
`~/.local/state/metnos/install/`. Un’esecuzione successiva riprende dal primo
punto incompleto. Per ripetere una fase:

```bash
./.venv/bin/python -m install --force-phase 4
```

La prima compilazione del Tutor trasforma la documentazione pubblica e i
manifest correnti in un catalogo semantico firmato; su una macchina che usa la
CPU può richiedere alcuni minuti. Avviene nella fase 3, prima dell’avvio del
servizio, così il controllo di prontezza non può interromperla. Alle esecuzioni
successive il compilatore confronta il contenuto delle fonti e riutilizza i
vettori invariati; una modifica documentale invalida invece il catalogo e ne
provoca l’aggiornamento.

Le opzioni principali sono:

```text
--check               controlla i prerequisiti senza eseguire le fasi
--force               prosegue oltre gli avvisi non bloccanti
--force-phase N       ripete la fase N
--only-phase N        esegue soltanto la fase N
--yes, -y             accetta le scelte non sensibili in modo non interattivo
--enable COMPONENT    installa un componente opzionale indicato
--skip COMPONENT      non installa un componente opzionale indicato
```

L’accettazione iniziale e l’inserimento delle credenziali restano interattivi:
`--yes` non sostituisce un consenso necessario.

## Modelli e livelli

Metnos distingue `fast` (livelli `micro`, `procedural`, `fidelity`), `wise`,
`creative` e `frontier`; non impone un modello unico. La configurazione
effettiva è in:

```text
~/.config/metnos/llm_tiers.toml
~/.config/metnos/embedding_tiers.toml
~/.config/metnos/vlm_tiers.toml
```

Il modello di embedding testuale BGE-M3 è installato dentro Metnos e viene
eseguito nello stesso processo. Non dipende dall’ambiente Python o dai modelli
di altri progetti.

Se un endpoint compatibile risponde già all’indirizzo configurato, la fase 2 lo
collega ai livelli locali senza scaricare un altro LLM. In alternativa può
predisporre un motore locale gestito oppure usare il livello frontier, se sono
state fornite le relative credenziali.

Dopo l’installazione, la configurazione effettiva dei modelli si consulta e si
modifica nella chat web seguendo **Impostazioni → Sistema → Modelli**. La pagina
mostra anche provenienza dei valori, parametri di generazione e configurazioni
implicite. Il comando **Ripristina** ricrea i valori forniti dalla versione
installata; non recupera una configurazione personale precedente.

## Servizi opzionali

I componenti opzionali si possono scegliere durante la fase 2 oppure aggiungere
in seguito:

```bash
./.venv/bin/python -m install.sidecar --list
./.venv/bin/python -m install.sidecar searxng
./.venv/bin/python -m install.sidecar photon
./.venv/bin/python -m install.sidecar vlm
./.venv/bin/python -m install.sidecar playwright
```

| Componente | Capacità servita | Comportamento |
|---|---|---|
| SearXNG | ricerca web | servizio locale dell’utente |
| Photon | ricerca e georeferenziazione dei luoghi | servizio locale dell’utente |
| VLM | descrizione e arricchimento delle immagini | avvio su richiesta, arresto dopo inattività |
| Playwright | pagine JavaScript e sessioni grafiche sui siti | servizio locale con Chromium; il browser Side usa Xvfb |

Photon conserva l’archivio del Paese mentre costruisce l’indice locale. Se
l’espansione dell’archivio o l’importazione viene interrotta, all’esecuzione
successiva scarta l’output parziale non verificato e riprende dall’ultimo
artefatto certificato. Verifica inoltre che l’archivio compresso sia un frame
zstd completo. I marcatori persistenti vengono scritti soltanto dopo la corretta
conclusione dell’espansione e del processo Java: la sola presenza di un file
JSONL o della directory `photon_data/` non è mai considerata una prova di
successo.

Se un componente manca, Metnos non inventa il risultato: la capacità resta
inattiva oppure restituisce una degradazione esplicita. Le altre capacità
continuano a funzionare.

## Credenziali e utenti

La fase 4 può raccogliere credenziali per Telegram, posta, provider frontier e
GitHub. Le salva nel deposito cifrato di Metnos; non crea file temporanei in
chiaro. Google Workspace si collega in seguito con il proprio flusso OAuth,
senza condividere le credenziali dell’account con l’installatore.

Le directory canoniche sono:

```text
METNOS_INSTALL_ROOT   codice e ambiente virtuale condivisi
METNOS_USER_DATA      dati applicativi dell’utente
METNOS_USER_STATE     stato operativo e marcatori dell’utente
METNOS_USER_CONFIG    configurazione e credenziali dell’utente
```

I valori predefiniti delle ultime tre directory seguono le convenzioni XDG:
`~/.local/share/metnos`, `~/.local/state/metnos` e `~/.config/metnos`. Ogni
account di sistema dispone quindi di configurazione, sessioni e dati propri.

## Avvio e verifica

Su una macchina nuova, la fase 5 installa un unico `metnos.target` a livello
utente. Il target coordina il server HTTP e gli eventuali componenti integrati.
Per mantenerlo attivo anche senza una sessione aperta:

```bash
sudo loginctl enable-linger "$USER"
```

Controlli essenziali:

```bash
systemctl --user status metnos.target
./.venv/bin/python runtime/stack_reconcile.py check
curl http://127.0.0.1:8770/agent/health
```

Al termine, la fase 6 stampa l’URL locale e gli URL esatti rilevati per le
interfacce IPv4 della LAN privata. Sul server si apre l’URL con
`127.0.0.1`; da un altro dispositivo sulla stessa rete fidata si apre uno degli
URL LAN stampati. L’installazione guidata propone l’accesso LAN come scelta
predefinita; è possibile scegliere l’ascolto solo locale. Anche `--yes` abilita
la LAN.

Il listener predefinito usa HTTP non cifrato: non inoltrare la porta dal router
e non esporla direttamente a Internet. Il collegamento di onboarding è valido
15 minuti e si usa una sola volta. Se scade, eseguire
`./.venv/bin/python -m install --force-phase 6`, oppure accedere a
`/admin/login` con la chiave in `~/.config/metnos/admin.key`. Gli stessi URL
restano nel file `~/.local/share/metnos/install_summary.md`.

La fase 5 verifica l’avvio e l’endpoint di salute. Non certifica da sola la
qualità del modello né esegue un turno applicativo completo. Dopo il primo
accesso alla chat, inviare una richiesta innocua, per esempio:

> Chiedi a Metnos con una richiesta come quella di questo esempio: “Che ora è e
> quale fuso orario stai usando?”

L’installazione è operativamente completa solo se la chat restituisce una
risposta e i servizi selezionati superano i rispettivi controlli.

## Aggiornamento di un’installazione esistente

Se è già attivo un vecchio `metnos-http.service` a livello di sistema, la fase 5
installa le unità dell’utente ma non avvia un secondo listener e non disabilita
il servizio esistente. Il passaggio al target integrato richiede il controllo
guidato descritto in [`../systemd/README.md`](../systemd/README.md), con due cicli
di prova e ripristino verificato.

Lo stato dei componenti è visibile nella chat web seguendo **Impostazioni →
Sistema → Servizi**. La pagina propone **Avvia** per un servizio arrestato e
**Arresta** o **Riavvia** per un servizio attivo, sempre entro il catalogo
chiuso dei componenti gestibili. Le operazioni di deploy coordinato continuano
a passare dal riconciliatore dello stack.

## Ruolo del manifest

[`manifest.toml`](manifest.toml) è l’inventario leggibile dalla macchina dei
componenti correnti: requisiti di sistema, modelli incorporati o opzionali,
unità, directory e configurazioni. Il comportamento eseguibile resta definito
dalle sorgenti che lo applicano:

- `requirements.txt` e `requirements-optional.txt` per i pacchetti Python;
- `install/phases/` per le sei fasi;
- `install/sidecar.py` per i servizi opzionali;
- `install/units/*.tmpl` per le unità systemd;
- `runtime/virt/` e `runtime/llm_router.py` per la configurazione dei modelli.

Una modifica a uno di questi contratti deve aggiornare nello stesso cambiamento
anche il manifest e questa guida. Il manifest descrive lo stato installabile
corrente: non ospita un diario dello sviluppo.
