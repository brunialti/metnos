# Installare Metnos

Il percorso supportato è `install/bootstrap.sh`, su Linux x86-64 con systemd
e Python 3.12. L’installer richiede privilegi amministrativi e prepara un
account di servizio dedicato, `metnos`. Le domande e le operazioni applicative
sono eseguite con quell’account; la preparazione e l’attivazione della
configurazione firmata passano dal coordinatore amministrativo.

## Preparazione

Servono accesso a Internet, Git, almeno 8 GB liberi oltre ai modelli scelti e i
pacchetti di sistema del manifest. Una GPU non è obbligatoria. Ubuntu 24.04 è
il riferimento per l’inventario completo dei componenti locali.

```bash
git clone https://github.com/brunialti/metnos.git
cd metnos
sudo apt-get update
sudo apt-get install -y $(python3 -c 'import tomllib; p=tomllib.load(open("install/manifest.toml","rb"))["system_packages"]; print(" ".join(p["debian"]+p["debian_optional"]+p["ubuntu_24_04_all_local"]))')
bash install/bootstrap.sh --check
bash install/bootstrap.sh
```

Prima dell’ultimo comando puoi preparare il file facoltativo
`~/.config/metnos/services.toml`, seguendo il [manuale dei servizi](SERVICES.md).
Solo i servizi dichiarati vengono riusati. Senza file, o per le voci mancanti,
l’installer prepara componenti locali. Non cerca server nella rete e non passa
a servizi cloud in caso di errore. Un endpoint dichiarato deve essere
raggiungibile dall’ambiente in cui installi.

Il bootstrap crea `.venv` nel clone per avviare la procedura. Il runtime usa
poi l’ambiente Python amministrato, con versioni e impronte fissate in
`requirements-linux-x86_64.lock`. L’installer genera nuove autorità per la
nuova installazione; non occorre un comando manuale separato né copiare chiavi
da altre istanze. Le chiavi amministrative rimangono accessibili solo a root.

`--check` controlla i prerequisiti senza eseguire le fasi applicative; il
bootstrap può comunque creare o aggiornare `.venv`. Se questa esiste già,
`./.venv/bin/python -m install --check` esegue direttamente il controllo.

## Le sei fasi

| Fase | Risultato |
|---:|---|
| 1 | Verifica di prerequisiti e dipendenze; directory dell’account di servizio |
| 2 | BGE-M3 locale, modelli e componenti locali o collegamenti dichiarati |
| 3 | Sorgente e catalogo iniziale autenticati; database, traduzioni e Tutor |
| 4 | Chiave amministrativa e credenziali cifrate facoltative |
| 5 | Attivazione della distribuzione firmata e controlli dei servizi |
| 6 | Collegamento di primo accesso e riepilogo |

I componenti della fase 2 vengono preparati prima dell’attivazione. Il catalogo
iniziale proviene dalla distribuzione verificata. La sua adozione non è una
certificazione di nuove funzioni prodotte successivamente.

I marcatori sotto `/var/lib/metnos-service/.local/state/metnos/install/`
consentono la ripresa, ma non autorizzano l’avvio: ogni continuazione ripete
la verifica autenticata. Per riprendere, esegui di nuovo
`bash install/bootstrap.sh`. Per ripetere una fase usa `--force-phase N`;
`--only-phase N` richiede che quelle precedenti siano complete.

Le altre opzioni sono `--force` per gli avvisi non bloccanti e `--yes` per le
scelte non sensibili. L’accettazione iniziale resta interattiva. Il percorso
amministrato richiede un insieme completo di servizi e non accetta `--skip`:
per riusarli altrove prepara il profilo prima dell’installazione.

Il Tutor viene compilato e verificato prima dell’avvio. Su CPU la prima
compilazione può richiedere alcuni minuti; quelle successive riusano i vettori
delle fonti invariate. Il compilatore rispetta la quota CPU disponibile,
anche nel contenitore, evitando di moltiplicare i processi di calcolo.

## Modelli, configurazione e dati

BGE-M3 è locale ed eseguito nello stesso processo. I modelli scaricati risiedono
nella directory dati, separata dal codice immutabile. Il profilo può collegare
modello testuale e visione a servizi esistenti; ricerca SearXNG, geografia
Photon e browser Playwright seguono la stessa regola. I servizi locali usano
unità di sistema e l’account `metnos`. La visione locale parte su richiesta.

Il profilo selezionato fa parte della distribuzione firmata. Se una fase 2 è
già completa, riprendi con lo stesso profilo. Cambiare i servizi di un’istanza
già installata richiede un nuovo rilascio amministrato, non la modifica dei
file generati o il solo riavvio.

Le directory dell’account di servizio sono:

| Contenuto | Directory |
|---|---|
| Configurazioni e credenziali | `/var/lib/metnos-service/.config/metnos` |
| Dati, modelli e riepilogo | `/var/lib/metnos-service/.local/share/metnos` |
| Stato e marcatori | `/var/lib/metnos-service/.local/state/metnos` |

Il codice verificato e Python vivono nei depositi amministrati, separati dai
dati. Le variabili `METNOS_INSTALL_ROOT`, `METNOS_VENV`, `METNOS_USER_CONFIG`,
`METNOS_USER_DATA` e `METNOS_USER_STATE` mantengono questi ruoli distinti.

La fase 4 può raccogliere credenziali per Telegram, posta, GitHub e provider
frontier. Il cloud richiede una scelta esplicita e credenziali. Google Workspace
si collega dopo l’installazione tramite OAuth. I parametri dei modelli sono
consultabili nella chat da **Impostazioni → Sistema → Modelli**.

## Primo accesso e verifica

```bash
systemctl --failed
systemctl list-units 'metnos*'
curl http://127.0.0.1:8770/agent/health
```

La fase 6 stampa gli indirizzi locale e LAN e un collegamento amministrativo
valido 15 minuti, utilizzabile una sola volta. Il listener amministrato usa la
porta 8770 e accetta la LAN. Usa la rete fidata: il collegamento predefinito è
HTTP e non va esposto direttamente a Internet.

Il riepilogo è in
`/var/lib/metnos-service/.local/share/metnos/install_summary.md`. Se il link
scade, ripeti la fase 6 oppure accedi a `/admin/login` con la chiave
amministrativa dell’istanza. Non condividere questa chiave.

La salute HTTP verifica l’avvio. Per completare la prova, entra nella chat e
invia una richiesta innocua, per esempio: “Che ora è e quale fuso orario stai
usando?”. Verifica anche il Tutor e i servizi scelti. Un servizio avviato non
certifica da solo la qualità delle risposte.

## Aggiornamenti

L’installer non sovrascrive un servizio preesistente incompatibile. Gli
aggiornamenti e le migrazioni richiedono il percorso di rilascio amministrato,
con verifica della configurazione e finestra di riavvio. La pagina
**Impostazioni → Sistema → Servizi** mostra i componenti gestibili.

[`manifest.toml`](manifest.toml) contiene l’inventario dei requisiti;
[`INSTALL_NOTES.md`](INSTALL_NOTES.md) descrive i vincoli per chi mantiene
l’installer. La procedura eseguibile rimane quella del bootstrap e del
coordinatore `managed_install.py`.
