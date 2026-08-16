# Installare software su un PC Windows remoto — analisi e studio di fattibilità

**Data**: 16 agosto 2026 · **Stato**: analisi, nessuna implementazione
**Richiesta**: capability di executor remoto per installare software su
Windows. Soluzione robusta, general purpose.

> Documento fermato allo studio di fattibilità su richiesta di Roberto.
> Tutti i numeri e i comportamenti citati come «verificato» sono stati
> misurati il 16/8 sulla macchina reale `pcroberto` o letti nel codice, non
> dedotti.

---

## 1. Il verdetto in tre righe

È fattibile e vale la pena farlo, ma **non con l'architettura attuale del
client**: il client Metnos su Windows gira senza privilegi amministrativi, per
una scelta di progetto deliberata e difesa (§3). Installare software di
sistema richiede quindi un *secondo* componente elevato, e quel componente è
il vero oggetto del progetto — non l'executor, che è la parte facile.

Esiste già in casa il modello architetturale giusto: la catena `admin →
sudoer` di ADR 0070, che separa *chi decide* da *chi esegue* e mette il vaglio
in mezzo. La proposta è portare quella stessa forma su Windows, non
inventarne una nuova.

---

## 2. Che cosa esiste oggi (verificato)

### 2.1 La catena degli executor remoti funziona

Fase 7 è chiusa. Il client Rust gira su `pcroberto` alla versione **0.2.25**,
si aggiorna da solo, ed esegue executor remoti compresi quelli **mutanti**
(write/move/delete) con undo device-aware (ADR 0183). Sedici manifest
dichiarano `device_ok = true`. Il protocollo (coda firmata, idempotenza per
`invocation_id`, spool dei risultati, heartbeat) è collaudato end-to-end.

Quindi: **il trasporto non è il problema.** Un `install_*` remoto viaggerebbe
sulla stessa strada già percorsa da `delete_files` su device.

### 2.2 Il modello di operazione privilegiata esiste, ma solo per Linux

ADR 0070 definisce due primitivi builtin:

- **`admin`** — deliberativo. Riceve l'intento in linguaggio naturale,
  produce un `argv` validato, non esegue nulla. Passa per: cancello
  sintattico anti-shell-letterale → una sola chiamata LLM schema-guidata →
  strumenti di sicurezza deterministici (firma canonica, forbidden, blacklist,
  whitelist) → carta di vaglio all'utente su whitelist miss.
- **`sudoer`** — esecutivo. Riceve l'`argv` validato, **ri-valida** al momento
  dello sparo (per onorare modifiche alla blacklist fatte nel frattempo), e
  esegue in `subprocess.run(argv, shell=False)` dentro un profilo bwrap.

La capability `system:admin` esiste già nel registro: `critical=True`,
`default_approval="always"`, `target_kind="exact"`. **Non serve inventare una
capability nuova.**

Ma il contratto builtin di `admin` dice, oggi:

```toml
platforms = ["linux"]
[placement]
scope = "server"
device_ok = false
```

e le regole di canonicalizzazione (`runtime/safety/canonicalize_rules.json`)
sono interamente POSIX: `sudo`/`doas`/`pkexec`, `apt`/`yum`/`dnf`/`pacman`,
`rm`/`chmod`/`chown`. Non esiste alcuna nozione di `winget`, `msiexec`,
PowerShell o `Start-Process -Verb RunAs`.

**Conclusione**: la forma è riusabile, il contenuto no. Portarlo su Windows è
un lavoro di sostanza, non un cambio di flag.

### 2.3 Che cosa Metnos sa già fare con i pacchetti

Un solo executor, `find_packages`: cerca un eseguibile **nel PATH**
(`platforms = ["linux","windows"]`, `device_ok = true`, capability
`system:read`). Non interroga alcun database di package manager. Il dominio
«pacchetti» è quindi praticamente vergine: c'è il sostantivo, non c'è il
verbo.

---

## 3. Il vincolo che decide tutto: il client non è elevato

**Verificato il 16/8 su `pcroberto`**:

```
Get-ScheduledTask MetnosClient → Principal
  UserId   : rober
  RunLevel : Limited
```

Il client parte da una Scheduled Task «At logon» registrata **nel contesto
dell'utente, senza privilegi**. Non è un dettaglio: l'installer lo fa apposta,
e il commento nel codice racconta la battaglia del 3/7/2026 — un trigger
`-AtLogOn` senza `-User` vale «al logon di *qualsiasi* utente», richiede
l'elevazione e faceva fallire l'installazione con «Accesso negato». La scelta
è: **installare Metnos su un PC non deve richiedere l'amministratore.**

Quella scelta è giusta e non va rovesciata. Ma implica che il processo che
riceve gli executor **non può installare software di sistema**. Qualunque
soluzione deve attraversare questo muro, e il modo in cui lo attraversa *è* il
progetto.

---

## 4. Le tre vie per l'elevazione

### Via A — restare non elevati: solo installazioni per-utente

`winget install --scope user`, oppure Scoop, oppure MSI per-utente. Nessuna
elevazione, nessun componente nuovo, l'executor gira dov'è già il client.

- **Pro**: si implementa in giorni, non settimane. Rischio di sicurezza
  minimo: il raggio d'azione resta la home dell'utente. Reversibile
  (disinstallazione per-utente).
- **Contro**: copertura parziale e **imprevedibile**. Molti pacchetti sono
  solo machine-scope (7-Zip, incluso: il manifest winget dichiara installer
  `wix`/MSI). L'utente chiede «installa X», e la risposta è «questo sì, quello
  no» secondo una regola che non controlla né capisce. Per un assistente
  «general purpose», è una promessa che si rompe spesso.
- **Verdetto**: buona **prima fase**, cattiva soluzione finale.

### Via B — un servizio elevato installato una volta, con consenso esplicito

Un secondo componente (servizio Windows in `LocalSystem`, oppure Scheduled
Task `-RunLevel Highest`) installato **una sola volta**, con un UAC esplicito
al momento dell'accoppiamento o su richiesta successiva. Il client non elevato
gli passa richieste su un canale locale; il servizio accetta **solo**
operazioni firmate e riconosciute.

È il calco esatto di `admin → sudoer`: chi decide non ha i privilegi, chi ha i
privilegi non decide.

- **Pro**: copertura completa e prevedibile. Il perimetro di fiducia è
  esplicito, installato con consenso, e revocabile disinstallando un servizio.
  Riusa il vaglio, la firma canonica, la blacklist/whitelist e la carta di
  approvazione che esistono già.
- **Contro**: è il pezzo di software più delicato dell'intero prodotto. Un
  servizio `LocalSystem` che accetta comandi da un processo utente è, se
  sbagliato, una scalata di privilegi locale servita su un piatto. Richiede:
  canale locale autenticato (named pipe con ACL sul SID dell'utente
  proprietario, **non** una porta TCP), verifica della firma del chiamante,
  vocabolario di operazioni **chiuso** (non «esegui questo comando»), e un
  audit separato.
- **Verdetto**: è la soluzione vera. È anche l'unica parte del lavoro che
  merita la parola «robusta».

### Via C — UAC interattivo per singola operazione

`Start-Process -Verb RunAs` a ogni installazione: compare il popup UAC sul
desktop dell'utente.

- **Pro**: zero componenti nuovi, consenso massimamente esplicito.
- **Contro**: richiede una persona davanti allo schermo, in quel momento.
  Metnos è un assistente che agisce anche quando non lo guardi; un'operazione
  che si blocca in attesa di un popup invisibile viola §2.8 (mai dichiarare un
  esito che non corrisponde alla realtà) o resta appesa. Su una sessione non
  interattiva (l'utente ha fatto logout) il popup non compare affatto.
- **Verdetto**: non è una soluzione, è una scorciatoia che sposta il problema
  sull'utente. Può però essere il **ripiego dichiarato** quando il servizio
  elevato non è installato: «posso farlo solo se sei davanti al PC e confermi».

---

## 5. Il meccanismo di installazione: winget, verificato

**Verificato su `pcroberto`**: `winget v1.29.280` presente e funzionante,
sorgenti accettate, query non interattive OK.

`winget show --id 7zip.7zip` restituisce metadati strutturati che sono, dal
punto di vista della sicurezza, **oro**:

```
Tipo di programma di installazione : wix
URL del programma di installazione : https://github.com/ip7z/7zip/releases/download/26.02/7z2602-x64.msi
SHA256 del programma di installazione : db407a4f6d4999e5c7bc00ce8a882be94717b56e7fa68140fe3f12605d91643e
```

Perché conta: winget dichiara **prima** dell'installazione l'URL esatto e
l'impronta SHA-256 del programma di installazione. La carta di approvazione
può quindi mostrare all'utente *che cosa esattamente* sta per essere scaricato
ed eseguito, e la verifica dell'impronta è fatta da winget stesso. È il
contrario di «esegui questo comando e speriamo».

Winget è inoltre parte di Windows dal 1809/Win11: nessuna dipendenza da
installare, nessun terzo da fidare oltre Microsoft e il repository dei
manifest.

**Scelta raccomandata**: winget come meccanismo **unico** in prima battuta.
Non Chocolatey (richiede installazione propria ed è esso stesso elevato), non
`msiexec` diretto (perde i metadati e la verifica), non Scoop (solo
per-utente, utile semmai come complemento alla Via A). Un secondo meccanismo
si aggiunge solo quando una misura dice che winget non basta — non prima.

---

## 6. Il problema del nome (§2.2)

Il vocabolario delle azioni è **CHIUSO** e non contiene `install`:

```
act change classify compare compress compute create delete describe extract
filter find get group list login move open order read render send set share
sort write
```

Tre strade, in ordine di preferenza:

1. **`admin` esteso a Windows.** L'installazione è un'operazione privilegiata
   di sistema, ed `admin` è per definizione «il varco delle operazioni
   privilegiate»; il commento in `_dropped_required_verbs` lo dice già:
   «apt/systemctl/mount condividono questo unico varco». Nessun token nuovo,
   nessuna escalation di vocabolario, e l'utente continua a esprimersi in
   linguaggio naturale. **Costo**: `admin` diventa multipiattaforma e
   device-capable, cioè cambia un builtin critico.
2. **`create_packages`.** Usa solo token esistenti. `create` = «porta in
   esistenza un oggetto»; installare un pacchetto lo è, e la simmetria
   `delete_packages` per la disinstallazione viene gratis con il reverse
   pattern `delete_<object>_by_id` già a catalogo (§2.3). **Costo**: il nome
   non è quello che un umano userebbe, ma il vocabolario non è per gli umani —
   è per il modello, e il modello vede la description.
3. **Estendere il vocabolario con `install`.** Richiede i tre criteri
   congiunti di §2.2 (necessario, generale, comprensibile) e una decisione di
   Roberto. È il caso di scuola: «necessario» è discutibile finché (1) e (2)
   esistono.

**Raccomandazione**: (2) `create_packages` per l'executor, con `admin` che
resta il varco per le operazioni privilegiate che *non* sono un'installazione.
Motivo: tenere `admin` server-only e Linux-only è una semplificazione di
sicurezza che non conviene perdere, e un executor dedicato ha un contratto
argomentativo stretto (`package_id` da un catalogo, non un `argv` libero) che
`admin` per costruzione non può avere.

Questa è comunque **una decisione di design che spetta a Roberto** (§10.2).

---

## 7. Che cosa serve prima che l'operazione possa esistere

Elenco dei pezzi mancanti, in ordine di rischio decrescente:

| # | Pezzo | Perché non è opzionale |
|---|---|---|
| 1 | Servizio elevato + canale locale autenticato | Senza, non si installa niente di machine-scope (§3) |
| 2 | Vocabolario chiuso di operazioni per il servizio | Un servizio `LocalSystem` che accetta `argv` arbitrario è una scalata di privilegi |
| 3 | Firma canonica per Windows | `canonicalize_rules.json` non conosce winget; senza firma non c'è né blacklist né whitelist né carta di vaglio |
| 4 | Carta di approvazione con URL + SHA-256 | §2.8: l'utente deve vedere *cosa* si installa, non solo *che* si installa |
| 5 | Contratto di undo | Vedi §8 |
| 6 | Ripiego onesto quando il servizio non c'è | «Non posso installare software su quel PC: manca il componente amministrativo, e si installa così» — mai un fallimento generico |

Il punto 3 merita una nota: la firma canonica è ciò che rende *misurabile* la
sicurezza. Oggi `compute_signature(["apt","install","x"])` produce una firma
stabile che si può mettere in blacklist. L'equivalente Windows
(`winget install --id X --scope machine`) non esiste ancora, e va progettato
con la stessa cura — inclusa la domanda scomoda: la firma deve distinguere
*quale* pacchetto? (Sì: «installa qualsiasi cosa» e «installa 7-Zip» non sono
la stessa autorizzazione.)

---

## 8. Undo: il punto in cui la promessa si rompe

§2.3 impone che `reverse_pattern` venga da un catalogo **chiuso** di cinque
voci. Nessuna copre «disinstalla il pacchetto che hai appena installato».
`delete_<object>_by_id` è la più vicina e funzionerebbe *se* l'esito
dell'installazione restituisse un `package_ids` — winget lo può fare (l'`id`
del pacchetto è stabile).

Ma disinstallare non è l'inverso di installare, e dichiararlo tale sarebbe
disonesto:

- se il pacchetto **era già presente** in un'altra versione, l'installazione
  è stata un aggiornamento e l'inverso sarebbe un *downgrade*, non una
  rimozione;
- un programma di installazione modifica registro, associazioni di file,
  servizi, PATH: la disinstallazione ne rimette a posto una parte, non tutto;
- alcuni pacchetti non hanno affatto un disinstallatore silenzioso.

**Raccomandazione onesta**: dichiarare `revertible = false` e dire all'utente,
nella carta di approvazione, che l'operazione **non è annullabile da Metnos**;
offrire semmai una `delete_packages` come operazione *separata e richiesta*,
non come undo automatico. Vedere §2.8: meglio un «non lo so disfare» esplicito
che un undo che finge.

---

## 9. Fattibilità: verdetto e piano per fasi

**Fattibile**, con questo ordine:

- **Fase 0 — decisioni (Roberto)**. Nome dell'executor (§6), e via
  dell'elevazione (§4): A come tappa o si va diretti a B.
- **Fase 1 — Via A, per-utente.** `create_packages` con `--scope user`,
  `device_ok = true`, `platforms = ["windows"]`, capability `system:admin`,
  carta di approvazione con URL e SHA-256 da `winget show`. Nessun componente
  nuovo sul PC. Consegna una capacità vera e limitata, e soprattutto **mette
  in esercizio la carta di approvazione e la firma canonica Windows** — cioè
  la parte che va collaudata prima di dare privilegi a qualcuno.
- **Fase 2 — il servizio elevato.** Il pezzo serio: installazione con consenso
  esplicito, named pipe con ACL, vocabolario chiuso di operazioni, audit
  separato, disinstallazione pulita. Da progettare in un ADR proprio, non in
  una riga di questo documento.
- **Fase 3 — copertura.** `delete_packages`, elenco dei pacchetti installati
  (oggi `find_packages` guarda solo il PATH), aggiornamenti.

**Stima onesta**: Fase 1 è dell'ordine di giorni. Fase 2 non lo è, e chi la
stima in giorni non l'ha guardata: è il componente più privilegiato che
Metnos avrebbe mai installato su una macchina altrui.

---

## 10. Decisioni aperte, per Roberto

1. **Nome**: `create_packages` (raccomandato), `admin` esteso, oppure nuovo
   token `install` con escalation §2.2.
2. **Elevazione**: si parte dalla Fase 1 per-utente (raccomandato) o si va
   diretti al servizio elevato?
3. **Undo**: si accetta `revertible = false` con dichiarazione esplicita
   all'utente (raccomandato), o si vuole tentare la simmetria
   installa/disinstalla?
4. **Perimetro**: l'installazione è ammessa su **qualunque** pacchetto del
   catalogo winget, o serve una lista di pacchetti approvati per la prima
   fase? (La seconda è più prudente e si allarga poi con la whitelist che il
   vaglio già sa gestire.)

---

## Riferimenti

Codice: `runtime/system/admin.py`, `runtime/system/sudoer.py`,
`runtime/safety/canonicalize.py` + `canonicalize_rules.json`,
`runtime/policy.py::CAPABILITY_REGISTRY`, `runtime/placement.py`,
`client-rs/src/{sandbox_windows,appcontainer}.rs`,
`executors/find_packages/`, mirror `install.ps1`.

ADR: 0011 (architettura client/server), 0046 (client Rust), 0070 (catena
admin→sudoer), 0071 (firme di sicurezza), 0183 (undo device-aware), 0184
(self-update). Runbook: `internal/design/e2e-windows-runbook.md`.
