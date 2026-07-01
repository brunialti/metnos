# Metnos — Installazione

Questo documento descrive come installare Metnos su un nodo nuovo a partire
dal repository in `/opt/metnos`. La fonte unica di verita' delle componenti
(pacchetti Python, pacchetti di sistema, modelli ML, servizi systemd,
directory, template di config, secret) e' `install/manifest.toml`.

## Scopo del manifest centrale

`install/manifest.toml` codifica, in formato TOML leggibile dall'umano e
parsabile da uno script, ogni componente che serve a far girare Metnos su
un nodo nuovo. Aggiungere o cambiare una dipendenza significa: aggiornare
il manifest. Niente componenti nascoste nel codice — se un modulo importa
una libreria nuova, la libreria entra nel manifest prima del merge.

I consumer del manifest sono:

- **`install/setup.sh`**: scaffold di installazione end-to-end.
- **`install/download_models.sh`**: scaricamento + verifica sha256 dei
  modelli ML.
- **Audit manuale**: chi si chiede "che cosa serve a Metnos?" apre questo
  file e ottiene la risposta.

## Workflow di installazione su nodo nuovo

Tre passi obbligatori + uno opzionale.

### 1. Clonare il repository

```bash
sudo mkdir -p /opt/metnos
sudo chown $USER:$USER /opt/metnos
git clone <repo-url> /opt/metnos
cd /opt/metnos
git checkout <ramo-stable>      # o main / un tag
```

### 2. Eseguire `install/setup.sh`

```bash
./install/setup.sh
```

Lo script:

1. verifica Python >= 3.12;
2. installa via apt i pacchetti di `system_packages.debian` (richiede sudo);
3. crea le directory dichiarate in `directories`;
4. genera i secret rigenerabili (`admin.key`, chiavi Ed25519 per firmare
   gli executor sintetizzati);
5. *non* copia automaticamente i file unit di systemd — stampa l'elenco
   delle unit da copiare e i comandi `systemctl enable`. Questa scelta
   e' deliberata (sicurezza: vietato installare service in modo
   automatizzato senza review umana del file unit).

Modi:

- `--dry-run` stampa cosa farebbe senza eseguire.
- `--no-sudo` salta i passi che richiedono sudo (apt + systemd). Utile
  per setup parziali o per nodi in cui i pacchetti di sistema sono gia'
  installati.
- `--skip-models` salta il download dei modelli (passo 3).

### 3. Scaricare i modelli ML

```bash
./install/download_models.sh
```

Scarica e verifica sha256 di:

- **SigLIP-base-patch16-224** (Xenova ONNX quantizzato int8) → ricerca
  scene-concept e similarita' immagini. ~210MB su disco.
- **InsightFace buffalo_l** (RetinaFace `det_10g.onnx` + ArcFace
  `w600k_r50.onnx`) → face detection + face embedding. ~280MB su disco
  (190MB estratti utili).

Il modello text-embedding **MiniLM-L12-v2** e' condiviso con
`giorgio2/suprastructure` e si presume gia' presente in
`/opt/giorgio2/models/onnx/`. NON viene riscaricato.

### 4. Configurazione manuale residua

- Editare `~/.config/metnos/owned_domains.json` con i propri domini.
- Editare `~/.config/metnos/trusted_origins.json` con le origini HTTP
  trusted per `/agent` endpoint.
- Editare `~/.config/metnos/mail/mail.env` con IMAP/SMTP credentials.
- (Opzionale) Configurare Photon (geocoder OSM) e Ollama (LLM tier).
- Pairare Telegram (`/pair-channel`) se si vuole il canale primario
  Telegram.

### 5. Avviare i servizi

I servizi sono **user unit** (nessun `sudo`): `python -m install` (fase 5) li
genera dai template in `install/units/*.tmpl`, sostituendo i percorsi, e li
abilita. Le unit installate: `metnos-http.service`, `metnos-telegram-daemon.service`
(se pairato) e `metnos-i18n-translator.timer` (riempimento traduzioni i18n a
ciclo). Per rifarle a mano:

```bash
python -m install --force-phase 5      # rigenera + abilita tutte le user unit

# Stato / log
systemctl --user status metnos-http
systemctl --user list-timers metnos-i18n-translator.timer
journalctl --user -u metnos-http -f

# Per sopravvivere al logout
sudo loginctl enable-linger $USER
```

I sidecar opzionali (ricerca web SearXNG, ecc.) si aggiungono dopo con
`python -m install.sidecar <nome>` (vedi `install/README.md`).

### 6. Indicizzazione foto (ADR 0117)

L'indicizzazione automatica delle foto e' parte del setup di base, ma con
**due vincoli operativi importanti**.

**Dove vanno messe le foto.** L'indicizzatore opera SOLO su directory
contenute in:

```
~/.local/share/metnos/Immagini/
```

Questa e' la radice canonica del corpus immagini. Qualunque altra
posizione filesystem viene **ignorata** dal task ricorrente. Per usare
foto residenti su un disco esterno o NAS:

- (Suggerito) **mount** del disco/share dentro la radice canonica:
  ```bash
  # Esempio: NAS via CIFS
  sudo mount -t cifs //nas/Foto ~/.local/share/metnos/Immagini/NAS \
              -o user=USER,uid=$(id -u),gid=$(id -g)
  ```
  Mount persistente via fstab/systemd-mount per sopravvivere ai reboot.

- (Alternativa) **symlink** dentro la radice canonica:
  ```bash
  ln -s /mnt/foto-archivio ~/.local/share/metnos/Immagini/Archivio
  ```
  L'indicizzatore segue i symlink ricorsivamente.

**Quando viene aggiornato l'indice.** Il task `images_index_refresh` e'
registrato come builtin nello scheduler v2 al primo boot del server HTTP
(`install_default_jobs` in `runtime/scheduler_v2/builtin_callbacks.py`).
Trigger: `daily@03:00`. Comportamento:

- **Walk + stat ~11s** su tutto il corpus (fino a 50000 foto, default).
- Per ogni foto, signature `(mtime, size)` confrontata con l'entry
  precedente nell'indice unificato:
  - Invariata → skip (riusa description/keywords/embedding/faces).
  - Nuova o modificata → pipeline completa EXIF + ArcFace + VLM
    (Qwen3-VL-2B su `:8081`) + BGE-M3 (~3-4 s/foto su 7900X warm).
  - Cancellata → sparisce al rewrite atomic dell'indice.

**Verifica installazione del task** (post primo boot HTTP):

```bash
curl -s http://127.0.0.1:8770/admin/scheduler --header "X-Admin-Key: $(cat ~/.config/metnos/admin.key)" \
  | jq '.jobs[] | select(.name=="images_index_refresh")'
```

**Re-enrichment globale post upgrade VLM**: NON automatico. La sostituzione
del modello VLM (cambiando `~/.config/metnos/vlm_tiers.toml`) richiede un
trigger manuale:

```bash
systemd-run --user --unit=metnos-vlm-enrich-rebuild \
  --setenv=PYTHONPATH=/opt/metnos/runtime:/opt/suprastructure/src \
  --setenv=METNOS_VLM_URL=http://127.0.0.1:8081 \
  --setenv=METNOS_PROGRESS_FILE=$HOME/.local/share/metnos/index/image/<sha8>/_progress.json \
  /opt/suprastructure/.venv/bin/python -c "
import sys; sys.path.insert(0, '/opt/metnos/runtime')
sys.path.insert(0, '/opt/metnos/executors/create_images_indices')
import create_images_indices as m
print(m.invoke({'base_path': '$HOME/.local/share/metnos/Immagini', 'force': True, 'recursive': True}))
"
```

`force=True` ignora la cache (mtime,size) e re-invoca VLM per ogni foto
(~3 ore su 30k foto).

**Monitoraggio avanzamento di un one-shot**. Tre strumenti complementari:

1. **Progress file JSON** (env var `METNOS_PROGRESS_FILE` impostato al
   lancio): viene aggiornato atomicamente ogni 25 foto e a fine task.
   ```bash
   watch -n 5 cat ~/.local/share/metnos/index/image/<sha8>/_progress.json
   ```
   Schema:
   - `phase`: `"running"` durante, `"done"` al termine.
   - `n_total`, `n_processed`, `ok`, `fail`, `pct`.
   - `last_path`: la foto correntemente in lavorazione.
   - A fine task aggiunge `n_entries_total`, `index_path`, `model_text`,
     `model_vlm`, `model_face`.

2. **Stato unit systemd**:
   ```bash
   systemctl --user status metnos-vlm-enrich-rebuild --no-pager
   systemctl --user is-active metnos-vlm-enrich-rebuild
   ```
   `Memory:`, `CPU:`, `Tasks:` mostrano impronta corrente.

3. **Journal live** (errori per-foto, modelli caricati, fallimenti VLM):
   ```bash
   journalctl --user -u metnos-vlm-enrich-rebuild -f
   ```
   Le righe `build entry failed <path>` indicano foto saltate (corrotte
   / formati non standard); il batch prosegue.

Per i task **ricorrenti** (es. `images_index_refresh` daily) lo stato
runs e' visibile in `/admin/runs` (richiede admin key) + `/admin/scheduler`
con cronologia ultimi N esecuzioni, errori e durate.

## Componenti del manifest (mappa ad alto livello)

| Sezione | Cosa contiene |
|---|---|
| `[meta]` | Nome, versione, maintainer, dominio, schema_version. |
| `[runtime]` | Path standard (working/data/config/state/log dirs), Python min. |
| `[runtime.python_packages]` | Pip required + optional + dev. |
| `[system_packages]` | Pacchetti apt required + optional. |
| `[[models.entry]]` | Modelli ML: nome, source URL, file con sha256, dim, executor che li usano. |
| `[[services.entry]]` | Unit systemd con path src/dst, enabled_at_install, needs_sudo. |
| `[[directories.entry]]` | Path + mode delle directory create al setup. |
| `[[config_templates.entry]]` | Template iniziali di config (owned_domains, trusted_origins, mail.env, ...). |
| `[[secrets.entry]]` | Secret rigenerabili (admin.key, signing key). Generator dichiarato. |
| `[[external_services.entry]]` | Servizi esterni (ollama, photon, ...) con stato `optional`. |
| `[multi_node]` | Note sul setup multi-nodo (vedi sotto). |

## Multi-nodo (sync fra macchine personali)

Il maintainer ha vari server personali in piu' location (LAN domestica,
laptop, nodi remoti). Il manifest e' pensato per essere replicabile su
qualsiasi nodo: l'esecuzione di `setup.sh` + `download_models.sh` su un
nodo nuovo deve produrre lo stesso ambiente sintatticamente.

Quel che NON e' (oggi) automatico:

- Sync delle credenziali (`~/.config/metnos/credentials`,
  `~/.config/metnos/cookies`): per-nodo.
- Sync della history (`~/.local/share/metnos/_history`): per-nodo.
- Sync dell'indice volti / indice immagini: replicabile fra nodi del
  maintainer ma il meccanismo di sync e' topic futuro.

Quel che E' replicabile per costruzione:

- I modelli ML (blob deterministici, sha256 verificabile).
- La configurazione `owned_domains.json` e `trusted_origins.json` (file
  testuali, posso copiare manualmente).
- Lo schema dei DB sqlite (i file vivono in `~/.local/state/metnos`,
  ricreati al primo avvio del relativo modulo).

Il manifest dichiara in `[multi_node]` quali path sono `shareable_paths`
(replicabili senza problemi) e `per_node_paths` (devono restare
per-nodo). Niente paletti che impediscano il sync nel futuro.

## Aggiornare il manifest

Quando si introduce una nuova dipendenza (libreria, modello, servizio):

1. Aggiungere la entry corrispondente in `manifest.toml`.
2. Aggiornare `download_models.sh` se e' un modello ML.
3. Aggiornare `INSTALL.md` se cambia il workflow visibile all'utente.
4. (Per modelli) commit dei file (manifest e script), MAI dei blob
   binari grandi (i blob sono fuori dal repo, scaricati on-demand).

Il manifest non e' un file di configurazione runtime — e' un'ontologia
delle componenti. Cambia in PR a `main` con review.
