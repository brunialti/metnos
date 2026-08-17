# Installare software — specifica implementativa

**Data**: 16 agosto 2026 · **Stato**: specifica, da eseguire dopo il via di Roberto
**Analisi**: `analysis_install_software_windows_16_8_2026.md`
**Decisioni ratificate**: **ADR 0209** — quelle NON si rinegoziano qui
**Destinatario**: un modello di classe inferiore a Opus. Scritto per essere
ESEGUITO, non interpretato.

---

## 0. Come si legge questo documento

DEVI: eseguire le parti nell'ordine A → B → C → D, verificando ciascuna col
suo criterio prima di passare alla successiva.
NON DEVI: iniziare la parte D senza che Roberto abbia approvato l'ADR
dedicato (§7.1).
NON DEVI: cambiare una decisione di ADR 0209. Se una decisione ti sembra
sbagliata, fermati e segnalalo: non aggirarla.
OK: se un passo non è verificabile col suo criterio, fermarsi e chiedere.
ERRORE: proseguire perché «sembra funzionare».

---

## 1. Che cosa esiste già — NON riscriverlo

| Pezzo | Dove | Stato |
|---|---|---|
| Trasporto executor remoti (coda firmata, idempotenza, spool) | `runtime/invocations.py`, `client-rs/` | ✅ collaudato, mutanti compresi |
| Undo device-aware | ADR 0183 | ✅ (non serve qui, vedi §5.5) |
| Capability `system:admin` | `runtime/policy.py` | ✅ `critical=True`, `default_approval="always"` |
| Carta di approvazione a due fasi | `runtime/system/admin.py` + `sudoer.py` | ✅ modello da COPIARE |
| Firma canonica + blacklist/whitelist | `runtime/safety/canonicalize.py` | ⚠️ solo POSIX (§5.4) |
| Scelta del device | `runtime/placement.py` | ✅ `scope`/`platforms` |
| Autorità per invocazione con clausola `when` | `runtime/capabilities.py` | ✅ da usare in §5.6 |

DEVI: riusare il modello `admin → sudoer`: chi decide non ha i privilegi, chi
ha i privilegi non decide.
NON DEVI: inventare un secondo meccanismo di consenso.

---

## 2. I vincoli non negoziabili (da ADR 0209)

1. Nuovo verbo `install` nel vocabolario chiuso.
2. UN executor, `install_packages`, con la direzione dichiarata da un
   argomento (`uninstall`), NON due executor.
3. `find_packages` risponde a «è installato?», su Linux e Windows, ed è
   vettoriale.
4. `revertible = false`. Un undo su un turno di installazione **spiega** che
   non è un undo e chiede la disinstallazione esplicita, con un form.
5. Un utente installa liberamente sui PROPRI device; sul server solo se
   amministratore.
6. Il meccanismo è **winget**, unico, in prima battuta.
7. Il componente elevato è la via scelta; la fase per-utente è saltata.

---

# PARTE A — Il verbo `install` nel vocabolario

## 3. I cinque passi, nell'ordine scritto in `runtime/vocab.py`

L'intestazione di quel file elenca la procedura. Seguila alla lettera.

1. **`ACTIONS`**: aggiungi `"install"` **in coda alla sua categoria**, mai in
   mezzo alla tupla.
2. **`ACTION_CATEGORIES`**: `"install": "system"`.
3. **`ACTION_MAPPING`**: voce bilingue con il **confine semantico**. Il
   confine è la parte che conta, perché è ciò che il modello legge per non
   confondere `install` con `create` o `write`:

```python
    "install": {
        "it": ["installa", "installare", "metti su", "aggiungi il programma",
               "scarica e installa", "disinstalla", "rimuovi il programma"],
        "en": ["install", "set up", "add the program", "uninstall",
               "remove the program"],
        "boundary": (
            "Mettere o togliere SOFTWARE su una macchina, tramite il suo "
            "gestore di pacchetti. NON crea file (-> create/write), NON "
            "scarica e basta (-> get_urls), NON esegue un comando qualunque "
            "(-> admin). La disinstallazione e' lo stesso verbo con la "
            "direzione dichiarata, non un verbo diverso."
        ),
    },
```

4. **Classificazione**: `install` entra in `DESTRUCTIVE_VERBS` e in
   `COVERAGE_REQUIRED_VERBS`. NON entra in `PRODUCER_VERBS`.
5. **`intent_extractor`**: aggiungi la disambiguazione contro i verbi vicini
   solo se una misura mostra che serve. NON aggiungerla per scrupolo.

DEVI: `OBJECT_DEFAULT_MUTATING_VERB["packages"] = "install"`.

## 4. Criterio di verifica della parte A

- `pytest tests/runtime -q` verde: i consumatori di `ACTIONS` leggono tutti da
  `vocab.py`, quindi il token nuovo li raggiunge per costruzione.
- La grammatica dei nomi accetta `install_packages` e rifiuta
  `install_files` finché quell'executor non esiste.
- Un turno reale «installa X su Y» non risponde più «non ho uno strumento»
  (oggi lo fa, ed è corretto: dopo la parte C non lo sarà più).

NON DEVI: passare alla parte B con la suite rossa.

---

# PARTE B — `find_packages` riscritto

## 5.1 Che cosa fa oggi, e perché non basta

Oggi è `shutil.which(nome)`: guarda **il PATH**, un nome per volta. Risponde a
«c'è un eseguibile che si chiama così», che è una domanda diversa da «questo
programma è installato». 7-Zip installato non è nel PATH; un programma con
interfaccia grafica quasi mai lo è.

## 5.2 Il contratto nuovo

```
find_packages(packages=["7zip.7zip", "git"]) ->
  entries=[{package_id, installed: bool, name, version, source, path?}]
```

DEVI: input **lista** e output **lista**, anche con un solo elemento (§2.1).
DEVI: `ok_count` = elementi realmente interrogati, non «trovati» (§2.8).
NON DEVI: fallire l'intera chiamata perché un pacchetto non esiste: quello è
un elemento con `installed: false`, non un errore.

## 5.3 Le due sorgenti

| Piattaforma | Sorgente | Comando |
|---|---|---|
| Windows | winget | `winget list --id <id> --exact --accept-source-agreements` |
| Linux | gestore di sistema | `dpkg-query -W` / `rpm -q`, secondo la distribuzione |

DEVI: mantenere il PATH come **ultima** verifica, non come unica: un
programma può essere presente senza essere nel registro dei pacchetti.
DEVI: dichiarare in `source` da dove viene la risposta (`winget`, `dpkg`,
`path`). Senza, l'utente non sa quanto fidarsi (§2.8).

## 5.4 Manifest

```toml
platforms = ["linux", "windows"]
[placement]
scope = "any"
device_ok = true
[[capabilities]]
name = "system:read"
```

`system:read`, non `system:admin`: leggere che cosa è installato non è un
privilegio. Un errore qui alzerebbe inutilmente l'autorità di un executor
innocuo.

## 5.5 Criterio di verifica della parte B

- I test di nascita del manifest passano su entrambe le piattaforme.
- Un turno reale «è installato 7-Zip sul mio PC?» risponde con la verità
  verificabile a mano sul PC.
- §7.10: dopo l'edit, `python3 runtime/sign.py sign executors/find_packages`.

---

# PARTE C — `install_packages`

## 6.1 Le due fasi (modello `admin`, copiare non reinventare)

L'installazione **non parte alla prima chiamata**. Due fasi, come `admin`:

```
FASE 1  install_packages(packages=[...])
        -> risolve i pacchetti sulla sorgente
        -> ritorna {ok, decision: "input_required", dialog_id,
                    final_message_hint: <carta con nome, versione, URL, SHA-256>}
        NESSUNA installazione e' avvenuta.

FASE 2  l'utente approva -> il runtime re-invoca con il token di consenso
        -> installa, ritorna {ok, ok_count, entries:[...], failed:[...]}
```

DEVI: mostrare nella carta, per ogni pacchetto: **nome, versione, indirizzo
di scaricamento, impronta SHA-256**. `winget show --id <id>` li dà tutti.
Senza, l'utente sta autorizzando un'etichetta, non un file.
NON DEVI: installare nella fase 1, nemmeno «per provare».
NON DEVI: costruire un `argv` dal testo dell'utente. L'identificativo si
risolve sul catalogo della sorgente; ciò che non si risolve non si installa.

## 6.2 Argomenti

```toml
[args]
required = ["packages"]

[args.properties.packages]          # array of string, dominio CHIUSO
[args.properties.uninstall]         # boolean, default false
[args.properties.scope]             # enum ["machine", "user"], default "machine"
[args.properties.actor_consent_token]  # runtime_resolved = true
```

DEVI: `packages` è dominio **CHIUSO** (§2.4): match esatto, niente glob. Un
carattere jolly su un'installazione significa «installa qualunque cosa».
DEVI: `actor_consent_token` marcato `runtime_resolved = true`, come in
`admin`: lo inietta il runtime dopo l'approvazione, mai il planner.

## 6.3 Manifest

```toml
platforms = ["windows", "linux"]
revertible = false
[placement]
scope = "any"
device_ok = true
[[capabilities]]
name = "system:admin"
hint = ["winget", "apt"]
```

## 6.4 La regola dei permessi (ADR 0209 D4 + emendamento 17/8)

> Un utente installa SOLTANTO sui PROPRI device — **l'amministratore
> compreso**. Sul server, solo se amministratore.

DEVI: applicarla dove vive l'autorità — il choke-point di invocazione e il
gate di consenso — **non dentro l'executor**.
NON DEVI: mettere un `if ruolo == "admin"` nel codice dell'executor. Un
executor che decide chi può chiamarlo è un executor che si può aggirare
chiamandolo diversamente.
NON DEVI: aggiungere un ramo che allarghi il perimetro device a chi è
amministratore. Essere amministratore dell'istanza non è autorità sulla
macchina personale di un'altra persona.

La proprietà del device si legge da `devices.py` (campo owner); il ruolo
dell'attore da `users`. Il rifiuto è un errore esplicito con il motivo, non un
silenzio.

### 6.4.1 Regola uno (device altrui): già strutturale — non indebolirla

Verificato, non assunto: la lista dei device candidati è già filtrata per
proprietario prima del placement (`agent_runtime` → `devices.owner_id_for_actor`),
senza alcun ramo per l'amministratore, e una richiesta che nomina un device
fuori da quella lista solleva `PlacementError` invece di ricadere sul server.

DEVI: lasciare `install_packages` dentro quel percorso, come ogni executor
`scope="device"`.
DEVI: aggiungere un test che lo dimostri — un amministratore che nomina il
device di un altro utente riceve un rifiuto, non un'installazione.
NON DEVI: introdurre una via di risoluzione del device che salti il filtro.

### 6.4.2 Regola due (il server): da costruire

Il controllo «sul server solo amministratore» **non esiste oggi**. Va aggiunto
al choke-point di invocazione, non nell'executor: quando il placement risolve
`placement.SERVER` e il verbo è `install`, l'attore deve avere ruolo `admin`.

DEVI: rifiutare con la ragione (`capability_missing` non è la classe giusta:
qui lo strumento c'è, manca l'autorità — errore esplicito con il motivo).
DEVI: coprirlo con un test per ciascun esito (utente → rifiuto,
amministratore → passa).

## 6.5 L'undo (ADR 0209 D3)

`revertible = false`, nessun reverse pattern.

Quando l'utente chiede di annullare un turno che ha installato qualcosa:

```
DEVI: spiegare che non e' un undo — l'ambiente e' stato modificato in modo
      irreversibile — e chiedere la disinstallazione ESPLICITA con un form
      (`get_inputs`, schema `yes_no`).
NON DEVI: disinstallare automaticamente.
NON DEVI: rispondere solo «non annullabile» e fermarsi: l'utente resta senza
      la strada per rimediare.
OK:   «Ho installato 7-Zip. Non posso annullarlo: un programma installato
      cambia il sistema in modo che non so ripercorrere all'indietro. Vuoi
      che lo disinstalli?» [Sì] [No]
ERRORE: «Fatto, annullato.»
```

Il testo è i18n (§7.13): chiave nuova nel catalogo, non stringa nel codice.

## 6.6 La firma canonica per Windows

`runtime/safety/canonicalize_rules.json` è interamente POSIX. Senza una firma
per Windows non esistono né blacklist né whitelist né memoria delle
approvazioni.

DEVI: la firma distingue **quale** pacchetto. «installa qualsiasi cosa» e
«installa 7-Zip» non sono la stessa autorizzazione, e una whitelist che non
le distingue è una porta aperta.
DEVI: aggiungere `winget` a `subcommand_style_binaries` e i suoi
sottocomandi a `binary_target_hints`.

## 6.7 Criterio di verifica della parte C

- Fase 1 non installa: verificabile perché `winget list` prima e dopo è
  identico.
- La carta mostra URL e SHA-256 reali (confrontali a mano con
  `winget show`).
- Un utente non amministratore riceve un rifiuto **motivato** sul server, e
  riesce sul proprio device.
- Un undo produce la domanda, non la disinstallazione.
- E2E §8.5: almeno un turno reale su `/agent/turn` per ciascuna direzione.

---

# PARTE D — L'aiutante elevato su Windows

## 7.1 Prima del codice serve un ADR

NON DEVI iniziare questa parte senza un ADR dedicato, approvato da Roberto,
che fissi: il canale, il vocabolario delle operazioni, il flusso di
installazione e quello di rimozione.

Motivo: è il software più privilegiato che Metnos installerà mai su una
macchina altrui. Un errore qui non è un difetto, è una scalata di privilegi.

## 7.2 I requisiti che l'ADR dovrà rispettare (da ADR 0209 D5)

1. **Vocabolario CHIUSO di operazioni.** L'aiutante accetta «installa il
   pacchetto con questo identificativo», MAI «esegui questo comando». Un
   servizio di sistema che esegue `argv` arbitrari ricevuti da un processo
   utente è una scalata di privilegi con un'interfaccia gentile.
2. **Canale locale autenticato**: named pipe con ACL legata al SID
   dell'utente proprietario. **Mai** una porta TCP, su nessuna interfaccia.
3. **Verifica del chiamante**: l'aiutante ri-valida ciò che gli viene chiesto
   al momento dell'esecuzione, come `sudoer` ri-valida invece di fidarsi
   della decisione ricevuta.
4. **Audit proprio**, separato da quello del client.
5. **Rimozione pulita**: disinstallare l'aiutante toglie il privilegio, e
   l'utente deve poterlo fare **senza la collaborazione di Metnos**.

## 7.3 Il vincolo di partenza, verificato

Il client gira come Scheduled Task «At logon» con `RunLevel: Limited`, per
scelta: installare Metnos su un PC non deve chiedere l'amministratore.

DEVI: lasciare quella scelta com'è. L'aiutante è un secondo componente,
installato separatamente e **con un consenso esplicito**.
NON DEVI: elevare il client. Sarebbe la soluzione più rapida e la peggiore:
il processo che riceve executor dalla rete diventerebbe amministratore.

## 7.4 Ripiego dichiarato quando l'aiutante non c'è

DEVI: dire la verità e la strada. «Non posso installare software su quel PC:
manca il componente amministrativo. Si installa così: …»
NON DEVI: fallire con un errore generico.
NON DEVI: tentare l'elevazione interattiva di nascosto: su una sessione non
interattiva il popup non compare e il turno resta appeso.

---

## 8. Test obbligatori

| # | Che cosa | Perché non è opzionale |
|---|---|---|
| 1 | `install_packages` fase 1 non installa | è l'invariante di sicurezza dell'intero disegno |
| 2 | La carta contiene URL e SHA-256 | senza, il consenso è cieco |
| 3 | Un identificativo che non si risolve → errore, non installazione | il dominio chiuso di `packages` |
| 4 | Un carattere jolly in `packages` → rifiutato | «installa qualunque cosa» |
| 5 | Permessi: non-admin sul server → rifiuto motivato; sul proprio device → passa | ADR 0209 D4 |
| 6 | Undo → domanda, mai disinstallazione | ADR 0209 D3 |
| 7 | `find_packages` vettoriale, con `source` dichiarato | §2.1, §2.8 |
| 8 | Aiutante assente → messaggio con la strada, non errore generico | §2.8 |

DEVI: il test 1 con una verifica esterna (`winget list` prima e dopo), non
solo con un mock. Un mock non dimostra che non hai installato niente.

---

## 9. Ordine e criterio di fine

| Ordine | Parte | Si può consegnare da sola? |
|---|---|---|
| 1 | A — vocabolario | no, ma sblocca tutto |
| 2 | B — `find_packages` | **sì**: «è installato X sul mio PC?» è già una capacità utile |
| 3 | C — `install_packages` per-utente su Linux (server) | sì, e collauda carta e firma prima di dare privilegi |
| 4 | D — aiutante elevato | solo dopo il suo ADR |

**Criterio di fine**, verificabile:
1. `pytest tests/runtime -q` verde e suite dei manifest 100%;
2. un turno reale per ciascuna delle quattro direzioni (installa/disinstalla ×
   device/server), con l'esito confrontato a mano sulla macchina;
3. `winget list` prima e dopo ogni prova, allegato al rapporto.

---

## 10. Che cosa NON fare

- NON DEVI elevare il client Metnos (§7.3).
- NON DEVI far accettare all'aiutante un comando invece di un identificativo.
- NON DEVI usare una porta TCP per il canale locale.
- NON DEVI installare senza mostrare URL e impronta.
- NON DEVI trattare la disinstallazione come un undo.
- NON DEVI aggiungere un secondo gestore di pacchetti (Chocolatey, Scoop)
  finché una misura non dimostra che winget non basta.
- NON DEVI mettere la regola dei permessi dentro l'executor.
- NON DEVI accettare caratteri jolly in `packages`.
