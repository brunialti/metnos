# ADR 0184 — Self-update automatico del client remoto (W4)

- **Stato**: ACCETTATO (direttiva Roberto 5/7/2026: «l'upgrade del client remoto deve essere automatico»; validato e2e).
- **Contesto**: fino a 0.2.10 il client rilevava la versione nuova dal poll (`server_client_version`) ma il swap era rimandato (W4). Ogni upgrade richiedeva un reinstall manuale sul device (join flow) — visto live 5/7: PC a 0.2.7 con shim stantio, mutanti falliti finché non si reinstalla.

## Decisione
1. **Descrittore FIRMATO**: `GET /agent/client/update/{target}` → `{version, target, sha256, url_path, sig}`; `sig` = firma server su canonical `{sha256,target,version}` — il client verifica con la **pubkey pinnata al pairing** (stessa ancora di shim/invocazioni). Autenticità, non il solo sha-integrità di install.ps1.
2. **Trigger**: al poll, `server_client_version != CARGO_PKG_VERSION` → `selfupdate::maybe_update`.
3. **Idempotenza per SHA**: se lo sha256 del PROPRIO exe == sha del descrittore → già aggiornato, stop (nessun loop «stessa build, version diversa»). Il server è fidato+firmato: si segue la sua versione anche all'indietro (rollback pubblicando una versione precedente = feature).
4. **Swap atomico**: download → `<exe>.new` (sha verificato) → `<exe>`→`<exe>.old` → `.new`→`<exe>`. Su Windows l'immagine in esecuzione si RINOMINA (non si sovrascrive): tecnica standard. Fallimento del 2° rename → ripristino del vecchio (mai un client senza binario). `.old` resta come rollback manuale.
5. **Respawn**: spawn dello stesso path exe con gli argomenti originali + `METNOS_RESPAWNED=1`; il padre esce (rilascia il lock single-instance); il figlio ritenta il lock per 20s. Se lo spawn fallisce, esce comunque: Scheduled Task/systemd rilancia (RestartInterval già configurato).

## Prove
- e2e `scripts/c7-validate-selfupdate.sh` (server+mirror ISOLATI): mirror pubblica «9.9.9» (binario reale +1 byte, sha diverso) → swap avvenuto, `.old` = binario precedente, il processo respawnato ESEGUE (list_dirs→done), UN solo swap nei log.
- cargo 9/9 (unit swap keep/restore) + e2e mutanti re-verdi con 0.2.11.
- Prod: endpoint live su :8765, mirror `latest=0.2.11` firmato.

## Limiti onesti
- I client ≤0.2.10 NON si auto-aggiornano (il codice swap non c'è): serve UN ultimo upgrade manuale (join flow) per entrare nel regime automatico.
- PowerShell-side (install.ps1) resta sha-integrità (Ed25519 in PS = W6); il self-update Rust è già a firma piena.
- Nessun canale di rilascio (stable/beta): il mirror ha UNA `latest`; differenziazione = futuro se servirà.
