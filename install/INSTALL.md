# Installare Metnos

Italiano · [English](README.md)

Un solo installer guidato prepara un server Metnos completo su Linux: un account di servizio
dedicato, il codice verificato, i modelli, i servizi di supporto e un collegamento di primo
accesso.

## In breve

```bash
git clone https://github.com/brunialti/metnos.git
cd metnos
sudo apt-get update
sudo apt-get install -y $(python3 -c 'import tomllib; p=tomllib.load(open("install/manifest.toml","rb"))["system_packages"]; print(" ".join(p["debian"]+p["debian_optional"]+p["ubuntu_24_04_all_local"]))')
bash install/bootstrap.sh --check
bash install/bootstrap.sh
```

Poi apri il collegamento stampato alla fine e invia una richiesta vera nella chat.

## Prima di cominciare

- **Sistema:** Linux x86-64 con systemd e Python 3.12. Ubuntu 24.04 è il riferimento;
  l’installer accetta solo le versioni di systemd elencate in
  [INSTALL_NOTES.md](INSTALL_NOTES.md).
- **Accesso:** un account che può usare `sudo`. L’installer lo chiede dopo il tuo consenso.
- **Risorse:** Internet, Git, 8 GB liberi su disco oltre ai modelli, 8 GB di RAM consigliati.
  Una GPU aiuta i modelli locali ma non è obbligatoria.
- **Pacchetti:** quelli di [`manifest.toml`](manifest.toml), installati dal comando
  `apt-get` qui sopra. `polkitd` serve anche se riusi servizi remoti.
- **Servizi esistenti (facoltativo):** per riusare un server di modelli, la ricerca, la
  geografia o il browser che hai già, prepara prima `~/.config/metnos/services.toml`. Vedi
  [SERVICES.md](SERVICES.md).

Scaricamenti indicativi quando tutto è locale: vettori di testo 560 MB, visione 1,9 GB,
ricerca 200 MB, geografia 3 GB per un paese, browser 700 MB, più il modello di testo scelto.

## Che cosa fa l’installer

Chiede la lingua, mostra l’avviso di sicurezza e chiede conferma. Poi chiede l’accesso
amministrativo, crea l’account di servizio `metnos` ed esegue sei fasi. Le tue risposte sono
raccolte con quell’account; solo preparazione e attivazione girano come amministratore.

| Fase | Risultato |
|---:|---|
| 1 | Prerequisiti e dipendenze esatte verificati; directory dell’account di servizio |
| 2 | BGE-M3 locale, modelli e servizi di supporto preparati o collegati |
| 3 | Sorgente e catalogo iniziale verificati; database, traduzioni, Tutor |
| 4 | Chiave amministrativa e credenziali cifrate facoltative |
| 5 | Servizi di sistema firmati attivati e controllati |
| 6 | Scelta delle capacità, collegamento di primo accesso monouso, riepilogo |

- L’installer crea chiavi di firma nuove per questa installazione. Non serve un comando a
  parte e non si copiano chiavi da un’altra istanza. Le chiavi amministrative restano
  leggibili solo da root.
- La fase 3 costruisce il Tutor, l’aiuto integrato. Su CPU la prima costruzione può
  richiedere alcuni minuti; le successive riusano ciò che non è cambiato.
- La fase 4 può salvare credenziali per Telegram, posta, GitHub e fornitori di modelli cloud.
  Sono tutte facoltative. Google Workspace si collega dopo l’installazione tramite OAuth.

## Opzioni

| Opzione | Effetto |
|---|---|
| `--check` | Controlla i prerequisiti e non esegue fasi. Tramite `bootstrap.sh` può creare o aggiornare `.venv`; `./.venv/bin/python -m install --check` non scrive nulla. |
| `--yes` | Accetta le scelte predefinite non sensibili. Non sostituisce il consenso iniziale e non abilita mai un modello a pagamento. |
| `--force` | Prosegue oltre gli avvisi non bloccanti. |
| `--force-phase N` | Ripete la fase N. |
| `--only-phase N` | Esegue solo la fase N; le precedenti devono essere complete. |

`--skip` non è accettato: ogni servizio è installato in locale oppure dichiarato in
`services.toml`.

**Per riprendere** un’installazione interrotta, esegui di nuovo `bash install/bootstrap.sh`
con lo stesso `services.toml`. Le fasi completate restano, e la verifica si ripete ogni volta.

## Dove stanno le cose

| Contenuto | Directory |
|---|---|
| Configurazioni e credenziali | `/var/lib/metnos-service/.config/metnos` |
| Dati, modelli e riepilogo | `/var/lib/metnos-service/.local/share/metnos` |
| Stato e marcatori delle fasi | `/var/lib/metnos-service/.local/state/metnos` |

Il codice verificato e il suo Python stanno in depositi separati, di proprietà di root. La
`.venv` nel clone serve solo ad avviare l’installer. Le unità di servizio già presenti e
diverse da quelle attese vengono rifiutate, mai sovrascritte.

## Modelli e servizi

- **I vettori di testo** (BGE-M3) sono sempre locali e girano dentro Metnos.
- **Modello di testo:** locale, oppure un server compatibile esistente dichiarato in
  `services.toml`. Un modello cloud richiede una tua scelta esplicita e le sue credenziali:
  non viene mai scelto al posto tuo.
- **La visione** parte su richiesta. **Ricerca** (SearXNG), **geografia** (Photon) e
  **browser** (Playwright) girano come servizi di sistema dell’account `metnos`.
- **I lavori lunghi** sono spenti all’inizio; si accendono in **Impostazioni → Sistema →
  Servizi**.
- I parametri dei modelli sono in **Impostazioni → Sistema → Modelli**; per cambiare modello
  non serve reinstallare.

La fase 2 chiede se scaricare da Hugging Face il classificatore facoltativo delle griglie
reCAPTCHA,
[recaptcha-classification-57k](https://huggingface.co/DannyLuna/recaptcha-classification-57k)
(licenza MIT). La risposta predefinita è no e `--yes` lo esclude; senza, le griglie a
immagini restano a te. Per aggiungerlo dopo:

```bash
sudo -H -u metnos bash -s captcha-grid < install/download_models.sh
```

I servizi scelti fanno parte dell’installazione firmata. Per cambiarli dopo non basta
modificare i file generati o riavviare: serve un nuovo rilascio amministrato.

## Primo accesso e verifica

```bash
systemctl --failed
systemctl list-units 'metnos*'
curl http://127.0.0.1:8770/agent/health
```

La fase 6 stampa l’indirizzo di questo server e quello di ogni indirizzo della rete locale
privata, con un collegamento di primo accesso che vale 15 minuti e funziona una volta sola.
Gli stessi indirizzi sono salvati in
`/var/lib/metnos-service/.local/share/metnos/install_summary.md`.

Metnos ascolta sulla porta 8770 e accetta la rete locale privata. Il collegamento è HTTP in
chiaro: usa una rete fidata, e non inoltrare mai la porta né esporla a Internet.

Una risposta di salute prova solo che il server è partito. Per completare la verifica invia
nella chat una richiesta vera e innocua, per esempio «Che ora è e quale fuso orario stai
usando?», poi prova il Tutor e i servizi che hai scelto.

## Se qualcosa si ferma

| Che cosa vedi | Che cosa fare |
|---|---|
| L’installazione si è interrotta | Esegui di nuovo `bash install/bootstrap.sh`. |
| «Mancano programmi di sistema necessari» | Installa i pacchetti di «In breve» e ripeti. |
| «Il profilo dei servizi è diverso da quello preparato» | Ripristina il `services.toml` con cui hai cominciato. |
| Un servizio dichiarato non risponde | Correggi l’indirizzo o il servizio; nulla lo sostituisce da solo. |
| Il collegamento di primo accesso è scaduto | Esegui `bash install/bootstrap.sh --force-phase 6`, oppure apri `/admin/login` con la chiave amministrativa dell’istanza. Non condividere quella chiave. |

Altri casi sono nella tabella dei problemi di [INSTALL_NOTES.md](INSTALL_NOTES.md).

## Aggiornamenti

Un’installazione esistente si aggiorna dal percorso di rilascio amministrato, con verifica
della configurazione e una finestra di riavvio concordata.

[INSTALL_NOTES.md](INSTALL_NOTES.md) contiene le regole per chi mantiene l’installer;
[`../README.md`](../README.md) descrive il progetto e il modello di sicurezza.
