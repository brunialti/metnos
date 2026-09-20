# RM-0011 — Un solo modo di esprimere un provider

| Campo | Valore |
|---|---|
| Identificatore | `RM-0011` |
| Stato | `active`; non ancora `ready` |
| Creazione | `2026-09-20` |
| Ultima revisione | `2026-09-20` |
| Conservazione | persistente |
| Implementazione reale | nessuna fase F0-F3 iniziata. Due decisioni di vocabolario, `comments` e `workflows`, attendono Roberto e bloccano F2 |
| Origine e prove | RM-0010 §6bis, strada (c); dipende dal principio di RM-0010 §0, una regola sola uguale per ogni tipo di executor |

## 1. Esigenza

In Metnos esistono **due** modi di dire «questo strumento parla con un
fornitore esterno», e uno dei due ha già prodotto un difetto in esercizio.

**Il modo giusto**, quello di Google Workspace: il fornitore è un
**argomento**. Non esiste nessun `create_events_google_workspace`; esiste
`create_events`, e `backend_resolver.OBJECT_BACKENDS` dichiara
`{"arg": "client", "providers": [...], "available": ..., "alias_keys": ...}`.
Il runtime inietta il valore, il planner non lo vede e non lo sceglie.

**Il modo sbagliato**, quello di GitHub: il fornitore è un **suffisso nel
nome**. Sedici strumenti `*_github`, ciascuno con manifest firmato, record di
nascita e insieme di affinity proprio.

RM-0011 elimina il secondo. Non è pulizia estetica: è la causa del difetto
misurato in RM-0010 §2bis.

## 2. Perché il suffisso produce difetti, misurato

I 16 strumenti si dividono esattamente in due, e **la divisione coincide col
difetto**:

- **sette** duplicano un oggetto che esiste già nativamente, e sono **gli
  stessi sette** che portano i 35 tag di affinity presi in prestito dal
  dominio nativo;
- **nove** vivono su oggetti propri (`issues`, `pulls`), e sono **gli stessi
  nove** risultati puliti.

Non è coincidenza: uno strumento che duplica un oggetto nativo eredita il
vocabolario del nativo, perché il nome dichiara che fa la stessa cosa.

**E tre dei sette non fanno affatto quello che il nome dice.** Verificato nei
manifest il 20/9:

| strumento | cosa fa davvero | argomenti |
|---|---|---|
| `send_messages_github` | posta un **commento** su issue/PR | `repo`, `target`, `body`, `review_event` |
| `delete_messages_github` | cancella un **commento** | `repo`, `comment_id` |
| `create_tasks_github` | avvia un **workflow** GitHub Actions | `repo`, `workflow`, `ref`, `inputs` |
| `read_tasks_github` | elenca le **run** dei workflow | `repo`, `status`, `workflow_id`, `branch` |

Non sono posta e non sono promemoria. L'affinity è stata copiata perché era
stato copiato l'oggetto: **oggetto sbagliato → vocabolario sbagliato →
collisione**. I 35 tag non sono un difetto di etichettatura, sono il sintomo
di un difetto di modellazione.

Gli altri tre (`find_files_github`, `read_files_github`, `list_dirs_github`)
sono invece onesti: leggere un file da un repository è leggere un file,
esattamente come da Drive.

## 3. La regola che decide ogni caso

> **Stesso verbo, stesso oggetto, stessa semantica** → un solo strumento, con
> l'argomento provider.
> **Semantica diversa** → oggetto diverso, non un ramo in più.

È la regola che impedisce il gonfiamento degli strumenti standard: il criterio
d'ingresso è la semantica, non il fornitore. Un `read_files` che legge da
locale, da Drive e da un repository resta un `read_files`. Un «posta un
commento su una pull request» non entra in `send_messages` nemmeno se qualcuno
gli ha dato quel nome.

## 4. Prima e dopo

### Prima — 16 strumenti, tutti sotto `skills/`, tutti col fornitore nel nome

`find_files_github`, `read_files_github`, `list_dirs_github`,
`send_messages_github`, `delete_messages_github`, `create_tasks_github`,
`read_tasks_github`, `find_issues_github`, `read_issues_github`,
`create_issues_github`, `delete_issues_github`, `set_issues_github`,
`find_pulls_github`, `read_pulls_github`, `set_pulls_github`,
`change_pulls_github`.

### Dopo — 13 strumenti, nessuno col nome del fornitore

| oggetto | strumenti | stato dell'oggetto |
|---|---|---|
| `files` | `find_files`, `read_files` | **assorbiti**, zero nuovi |
| `dirs` | `list_dirs` | **assorbito**, zero nuovi |
| `issues` | `find`, `read`, `create`, `delete`, `set` | già nel vocabolario chiuso |
| `pulls` | `find`, `read`, `set`, `change` | già nel vocabolario chiuso |
| `comments` | `create`, `delete` | **nuovo, decisione di Roberto** |
| `workflows` | `create`, `read` | **nuovo, decisione di Roberto** |

Catalogo da 122 a 119. Il numero conta poco; conta che **un secondo fornitore
di versionamento (GitLab, Forgejo) aggiunge zero strumenti**, mentre oggi ne
aggiungerebbe sedici.

### Dove finiscono

I 13 escono da `skills/` e vanno in `/opt/metnos/executors/<nome>/`, come ogni
altro executor. In `executors/skills/github/` resta **solo lo script ponte**
verso l'API, esattamente come `google_api.py` e `gws_bridge.py` per Google.

**Attenzione**: questo non va usato per risolvere l'esenzione dal controllo di
affinity di RM-0010 F0. Sarebbe di nuovo un'eccezione per collocazione. F0 la
toglie a prescindere.

## 5. Le due decisioni che servono prima

`issues` e `pulls` sono **già** nel vocabolario chiuso. `comments` e
`workflows` no, e il cancello di governance (CLAUDE.md §2.2, ADR 0156) chiede
tre criteri congiunti:

**`comments`** — necessario: nessun sinonimo nella classe copre «commento su
un elemento di discussione»; generale: vale per issue, PR, e qualunque
sistema di revisione futuro; comprensibile: sì, senza glossa.

**`workflows`** — necessario: `tasks` è lo scheduler interno e significa
un'altra cosa; generale: vale per Actions, per le pipeline di GitLab e per
qualunque esecutore di automazioni remote; comprensibile: sì.

Se una delle due viene respinta, gli strumenti corrispondenti restano fuori da
RM-0011 e vanno riprogettati, non rinominati a forza.

## 6. Fasi

### F0 — I tre assorbimenti onesti

**Non dipende da alcuna decisione di vocabolario**, e si può fare subito.

`find_files`, `read_files`, `list_dirs` guadagnano `github` fra i provider;
`find_files_github`, `read_files_github`, `list_dirs_github` vengono ritirati.

### F1 — `issues` e `pulls` senza il nome del fornitore

Nove strumenti perdono il suffisso. Gli oggetti esistono già, quindi serve
solo la registrazione dei backend e la ripubblicazione.

### F2 — `comments` e `workflows`

Quattro strumenti, **dopo** le due decisioni di §5.

### F3 — Ritiro e ponte

I 16 contratti vecchi vengono ritirati; in `executors/skills/github/` resta il
solo script ponte.

## 7. Istruzioni di sviluppo

### 7.1 Registrare il backend

In `runtime/backend_resolver.py::OBJECT_BACKENDS`, sulla forma già in uso:

```python
"issues": {
    "arg": "client",
    "providers": ["github"],
    "available": lambda p: _github_creds(),
    "alias_keys": {"github": "issues.github"},
},
```

Per gli oggetti che esistono già (`files`, `dirs`) si **aggiunge** `github`
all'elenco `providers` e la relativa chiave in `alias_keys`; non si riscrive
la voce.

`_github_creds()` va scritto accanto a `_gw_creds()`, con la stessa forma: una
lettura del deposito credenziali, nessuna chiamata di rete.

**Ordine dei provider = ordine di preferenza.** Per `files` e `dirs` il locale
resta primo (§10.3, self-hosted come default); `github` entra in coda ed è
opt-in, raggiungibile solo se nominato.

### 7.2 Scrivere i 13 manifest

Formato §2.5, quattro capitoli in quest'ordine:

```
SCOPO: <1 frase>. PATTERN: <chiamata canonica literal>. NON: <anti-pattern +
disambiguazione vs tool simili>. OUT: <shape output pipeable>.
```

Tabella `[description]` per lingua, con companion `manifest.lang_state.json`
(ADR 0092).

**L'affinity è la parte che ha prodotto il difetto, quindi ha una regola sua**:
ogni tag deve portare almeno un token che nessun'altra famiglia rivendica. Si
verifica con la stessa misura di RM-0010 F1 **prima** di firmare, non dopo.
Vietato copiare l'affinity dello strumento nativo corrispondente: è
esattamente l'errore che ha causato l'incidente.

### 7.3 Il ramo provider dentro l'executor

Il valore arriva nell'arg `client`, iniettato dal runtime. L'executor non lo
sceglie e non lo indovina. Il ramo GitHub usa il ponte in
`executors/skills/github/`, importato pigramente come fa Google
(`_GW_CLIENT_TOOLS`, lazy-gw), non a livello di modulo: un import in testa
caricherebbe il ponte anche quando il provider non è richiesto.

### 7.4 Undo

Gli oggetti nuovi hanno bisogno del loro pattern inverso dal catalogo chiuso
(§2.3). `delete_comments` è reversibile solo se la ricevuta porta l'id, quindi
`delete_<object>_by_id` con contratto `comment_ids`. La creazione di una run di
workflow **non è annullabile**: va dichiarata tale, non finta reversibile.

### 7.5 Cosa NON serve

- **Planner e cache**: L0 e L1 verificano `tools_sig` e `pool_sig` in lettura,
  quindi i piani che citano i sedici nomi vecchi si invalidano da soli. Nessun
  intervento manuale, nessuna migrazione di cache.
- **La pulizia dei 35 tag** di
  `internal/reports/github-affinity-cleanup-20260920.md`: quei tag spariscono
  con gli strumenti che li portavano. Se RM-0011 viene fatta, quell'intervento
  separato non serve.

### 7.6 Ordine obbligato

1. `_github_creds()` e le voci `OBJECT_BACKENDS` (nessun effetto finché
   nessuno le usa);
2. i rami provider nei tre nativi, con le prove;
3. i manifest nuovi, uno per volta, con la misura sull'affinity **prima**
   della firma;
4. la pubblicazione attraverso Birth (§7.10), che è l'unico passo che tocca
   l'esercizio e non appartiene a questa fase di sviluppo;
5. il ritiro dei 16 contratti, **dopo** che i sostituti sono vivi.

Fra il 4 e il 5 il catalogo contiene entrambi: è la finestra in cui la misura
di RM-0010 F1 va rieseguita, perché è l'unico momento in cui vecchi e nuovi
competono davvero.

## 8. Impegno

| voce | quantità |
|---|---|
| decisioni di vocabolario (Roberto) | **2** |
| voci `OBJECT_BACKENDS` nuove | 4 |
| voci `OBJECT_BACKENDS` modificate | 2 |
| executor nuovi (manifest + codice + firma) | 13 |
| rami provider in executor nativi | 3 |
| contratti da ritirare | 16 |
| pattern inversi nuovi | 2-4 |
| lavoro su planner e cache | **0** |

Il grosso sono 13 manifest fatti bene, cioè il lavoro di importare una skill
da zero.

## 9. Criterio di uscita

1. Nessun executor porta il nome di un fornitore.
2. `OBJECT_BACKENDS` è l'unico posto dove un fornitore è dichiarato.
3. Aggiungere un secondo sistema di versionamento non aggiunge executor.
4. La misura di RM-0010 F1 sul catalogo risultante restituisce zero tag privi
   di token propri.
5. In `executors/skills/github/` resta solo il ponte.
