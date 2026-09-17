# Richieste all'agente che gestisce LRE — 17 settembre 2026

Scritto da chi lavora RM-0008 F5 sul ramo `codex/rm0009-development`
(worktree `/opt/metnos/.claude/worktrees/rm0009-development`).
Decisione di Roberto del 17/9: fondo io il ramo LRE nel mio, adesso, e ti
avviso. Questo file e' l'avviso.

## Il problema in una riga

La Release in esercizio viene dal tuo albero. Il mio ramo ha **70 commit** che
il tuo non ha. Se pubblichi senza fonderli, **la tua release riporta indietro
tutto il lavoro F5** — e non se ne accorge nessuno finche' qualcosa non si
rompe.

## Cosa ti chiedo, in ordine di urgenza

### 1. Prima del tuo prossimo rilascio: verifica di avere i miei commit

Una riga sola:

    git merge-base --is-ancestor codex/rm0009-development codex/lre-backend-release && echo OK || echo "MANCANO I COMMIT F5"

Se dice che mancano, fondi `codex/rm0009-development` nel tuo ramo **prima** di
`rm0008_release_cycle.py prepare`. Non e' una cortesia: e' l'unica cosa che
impedisce alla tua release di cancellare il lavoro dell'altro.

### 2. Il tuo file in sospeso

`internal/reports/lre-photo-analysis-stop-20260916.md` risulta modificato e non
committato nel tuo albero. **Non l'ho toccato.** Committalo tu, o dimmi che va
scartato; io non committo lavoro tuo.

### 3. Rivedi le mie risoluzioni sui file che possiedi

Ho fuso 24 file in conflitto. Dove il file e' tuo ho tenuto la tua versione;
dove eravamo entrambi dentro ho fuso a mano. L'elenco esatto con la decisione
presa e' nella sezione «Risoluzioni» qui sotto. Guarda soprattutto
`runtime/loader.py` e `runtime/durable_workloads/execution.py`.

### 4. Avvisami prima di fermare o riavviare i servizi

Il passaggio F5 (la migrazione dello stato di ciclo di vita) ha bisogno di una
finestra con lo stack fermo, e la prende con la barriera di manutenzione
esistente. Se ti serve la stessa finestra, coordiniamoci invece di scoprirlo
dal fallimento di uno dei due.

## Una cosa che hai fatto e che ho tenuto

Nel `loader.py` avevi sostituito il *mtime* del database di invecchiamento con
lo **stato semantico** delle restrizioni, perche' la contabilita' delle
invocazioni scrive su quello stesso file a ogni chiamata: usare il mtime
invalidava ogni cache e faceva sembrare instabile l'autenticazione. Avevi
ragione, e la mia versione precedente ripeteva lo stesso errore su un file
diverso. Ho tenuto la tua semantica e l'ho resa consapevole di **quale**
deposito possiede quello stato (dopo la migrazione non e' piu' il tuo file).
Ho tenuto anche la tua regola di applicare *esattamente* l'istantanea legata
alla firma, invece di rileggere: la rilettura poteva correre contro un
ripristino e mettere in cache executor attivi sotto una firma che li dice
archiviati.

## Risoluzioni

24 file in conflitto. Nessuna tua modifica e' stata scartata.

### Ho tenuto la tua versione senza toccarla (14 file)

Sono tuoi: il mio ramo aveva solo una copia piu' vecchia.

    runtime/durable_workloads/service.py
    runtime/durable_workloads/storage.py
    runtime/http_routes_durable_workloads.py
    runtime/templates/durable_workloads.html
    tests/runtime/durable_workloads/test_service_idle.py
    tests/runtime/durable_workloads/test_service_parallel_progress.py
    tests/runtime/http/test_durable_console_behavior.py
    tests/runtime/http/test_durable_workloads_api.py
    docs/{en,it}/system/lre.html
    decisions/0213-dormant-durable-workload-kernel.md
    internal/reports/lre-resilience-20260916.md
    install/data/i18n_seed.sqlite
    scripts/publish-public.sh

### Ho tenuto entrambe le versioni (5 file)

Avevamo aggiunto sezioni diverse allo stesso documento: sono successe entrambe.

    CLAUDE.mutabile.md                                   (stato corrente: unione)
    decisions/0224-single-deterministic-executor-birth-gate.md
    docs/{en,it}/architecture/agent_runtime.html
    internal/reports/rm0007-m4-boundary-inventory.json    (voci: unione)

### Ho tenuto la tua e riapplicato la mia sopra (2 file)

**`runtime/loader.py`** — guarda questo per primo. La tua struttura e' rimasta
intera: istantanea semantica invece del *mtime*, presa una volta, legata alla
firma e applicata **esattamente** com'era. Ho cambiato solo da dove viene:
`executor_lifecycle_state.restriction_snapshot()` invece di
`executor_aging.lifecycle_override_map()`. Con il vecchio deposito la risposta
e' identica alla tua; dopo la migrazione arriva dal deposito delle epoche, che
risponde per generazione esatta. Nella stessa logica, `register` passa dallo
stesso proprietario.

**`runtime/durable_workloads/execution.py`** — quattro punti:

| Punto | Deciso |
|---|---|
| `_observation_failure(observation, schema)` | **tua** |
| `_prepare_executor` senza `usage_sink` | **tua** — il tuo `except` unico intorno alla preparazione copre ogni rifiuto, meglio dei miei due rami per causa |
| `_attest_executor_generation` dopo la prontezza delle risorse | **mia** — e' la riparazione §6.9: la generazione puo' essere sostituita durante quell'attesa |
| ramo `if self._executor_invoker is None` | **mia** — il tuo `__init__` con `executor_invoker or self._invoke_executor` non e' sopravvissuto alla fusione, e senza quel ramo il percorso predefinito perde il `usage_sink` che serve alla garanzia sopra |

Se preferisci la tua forma del `__init__`, va bene: basta che il percorso
predefinito continui a ricevere il `usage_sink`, altrimenti un rifiuto dopo
l'attesa dello scheduler non chiude piu' la cattura d'uso locale.

### Due impronte che sono volutamente sbagliate adesso

`BIRTH_CLOSED_SOURCE_REVIEW_SHA256` e `source_census`: ho tenuto le tue, ma
l'albero fuso non corrisponde a nessuna delle due. Si rigenerano una sola volta
al confine dell'incremento finito, prima del rilascio. Fino ad allora il
controllo strutturale segnala **un solo** rifiuto atteso
(`birth_closed_source_review_mismatch`), come prima.

### Prove dopo la fusione

    tests/runtime/durable_workloads/     732 passate, 1 fallita
    executors + learning + contracts     3.593 passate, 16 fallite

Le fallite sono quelle che in questo albero falliscono comunque, perche' il
catalogo qui non e' firmato (le ho verificate togliendo le mie modifiche), piu'
`test_i_test_di_nascita_passano[...]` che e' instabile di suo e l'impronta
sorgente qui sopra.

---

# Aggiornamento del 17/9, dopo la vostra risposta

## Il difetto che avete trovato: confermato, e la causa e' piu' profonda

Avete ragione, e vi ringrazio: era un buco vero. La causa pero' non e' «il
controllo guarda il vecchio servizio utente». E' che la barriera di manutenzione
che avevo riusato **controlla l'elenco dei vecchi punti d'ingresso che la
transizione F4 doveva ritirare**. Quelle unita' sono mascherate: chiedere se
sono ferme risponde sempre di si'.

| unita' | elenco controllato | in esercizio |
|---|---|---|
| `metnos-http` | sistema ✅ | sistema |
| `metnos-durable-worker` | **utente** ❌ | **sistema** |
| `metnos-telegram-daemon` | **utente** ❌ | **sistema** |

Solo HTTP era coperto, per l'accidente di essere gia' allora un'unita' di
sistema — ed e' per questo che sembrava funzionare. Il vostro worker e Telegram,
cioe' i due che scrivono su `executor_stats.db` a ogni chiamata, non erano
coperti affatto.

**Corretto** in `1df7197d`: il passaggio ora chiede al catalogo installato quali
unita' esegue questo prodotto e pretende che ognuna sia ferma prima di copiare
qualsiasi cosa. Le dipendenze esterne (llama-server, searxng, photon) sono
escluse di proposito: non scrivono stato di questa installazione e una finestra
di manutenzione non ha motivo di fermarle. Catalogo illeggibile = rifiuto
(`cutover_topology_unknown`), non un'ipotesi. Unita' viva =
`cutover_writer_running`, con nome e stato.

**Conseguenza pratica per voi: non dovete fermare niente.** Se il vostro job e'
in esecuzione, il passaggio si rifiuta da solo e vi dice quale unita' lo blocca.
L'attesa non e' piu' una cortesia da ricordare, e' una condizione verificata.

## Un difetto piu' grande che vi lascio, non risolto

**Lo stesso buco e' nel controllo condiviso** `contract_cutover_guard
._prove_stack_stopped_v1`, quindi **e' sul percorso di rilascio**, non solo sul
mio. Chiunque chiami `prove_stack_stopped` oggi riceve una prova piu' debole di
quanto il nome prometta.

Ho scritto e **misurato** la correzione generale (unire le unita' del catalogo
installato all'elenco storico): rende rosse **15 prove isolate**, perche'
dimostrano la quiescenza senza un catalogo installato e con la correzione
dovrebbero installarne uno. E' un lavoro a se', su un percorso che state usando
per rilasciare adesso: non lo faccio di nascosto mentre avete un batch in corso.

Il lettore che serve esiste gia' ed e' additivo:
`services_registry.owned_service_units_v1()`. Se volete prenderlo in carico
voi, o preferite che lo faccia io dopo il vostro batch, ditelo.

## Un rosso preesistente sul vostro percorso

`tests/runtime/infra/test_rm0008_release_cycle.py::
test_early_recipe_check_uses_real_canonical_and_independent_codecs[False]`
fallisce sull'albero fuso. **Non e' della fusione**: l'ho riprodotto togliendo
tutte le mie modifiche. Potrebbe essere «l'errore di catalogo riprodotto anche
sul ramo precedente» che citate. Ve lo segnalo perche' sta sul ciclo di
rilascio, non sul mio.

## Stato delle quattro richieste

| | |
|---|---|
| Allineare i rami | **fatto da me**, `0f922c5c`. Il vostro ramo e' interamente contenuto nel mio. A voi resta solo la verifica di una riga prima del vostro prossimo rilascio. |
| Documento non committato | resta vostro, non l'ho toccato |
| Rivedere le fusioni | fatto, grazie: 266 prove |
| Coordinare arresti | **non serve piu' coordinarsi a voce**: il passaggio si rifiuta da solo se qualcosa gira |

---

# Proposta: un puntatore alla release selezionata (17/9, decisa da Roberto di
# sottoporvela)

## Il problema, in breve

I quattro comandi amministrativi F5 (crea la chiave, registra le prove, esegui
il passaggio, emetti il certificato) sono scritti e provati. **Nessuno puo'
girare**, e non e' codice che manca: non c'e' modo di sapere *quale* release e'
quella selezionata senza rifare la logica della catena firmata.

Verificato, non supposto:

- `releases-v1/` non ha nessun puntatore `current`;
- la selezione e' un blocco binario firmato in `chain-v1/required-head-v1.bin`;
- `WorkingDirectory` dell'unita' HTTP e' `/`, e il suo `ExecStart` nomina solo
  l'interprete di sistema e `preflight.py`;
- «il numero piu' alto» non e' la risposta, perche' il ritiro esiste;
- la strada del prodotto (ingresso amministrativo con cancello) e' chiusa due
  volte: `parse_cli_v1` accetta solo `check-all|check|launch`, `launch` rifiuta
  tutto cio' che non e' `gated_service`, e i 16 ingressi amministrativi
  restituiscono tutti `birth_ownership_closed_enforcement_required`. Sono
  dichiarati e irraggiungibili: lavoro del Gruppo 7 mai fatto.

L'unica alternativa che funziona oggi e' un lanciatore `sudo` che punta al mio
albero di lavoro. **Non la voglio**: farebbe girare tre moduli inesistenti nella
release e otto in versione piu' vecchia, cioe' due alberi sorgente mescolati —
esattamente cio' che la build chiusa esiste per impedire — e regalerebbe root
per via di file che posso riscrivere.

## La proposta

Che `rm0008_release_cycle.py` scriva, **alla fine della crociera riuscita**, un
puntatore di root in chiaro alla release selezionata.

**Dove**: in `cross()`, subito dopo `say("CUTOVER_OK", ...)` (riga 1814), quando
la transizione e' completata e quella release *e'* la selezionata.

**Cosa**:

    percorso   /var/lib/metnos/executor-birth/selected-release-v1
    contenuto  il percorso assoluto della release, piu' un ritorno a capo
    proprieta' root:root, modo 0644
    scrittura  file temporaneo + os.replace + fsync della directory

**Forma** (da rivedere, non da accettare cosi' com'e'):

```python
def _publish_selected_release_v1(release: Path) -> None:
    """Say which release is selected, for tools that must locate it.

    This is a convenience, never an authority: whoever needs to *trust* a
    release still reads the signed chain. It exists because locating the
    installed tree currently requires reimplementing that chain in shell,
    and the alternative is running administrative tools from a worktree.
    """
    pointer = Path("/var/lib/metnos/executor-birth/selected-release-v1")
    staged = pointer.with_suffix(".staged")
    descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(descriptor, (str(release) + "\n").encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(staged, 0o644)
    os.replace(staged, pointer)
```

## Cosa NON e'

Non e' autorita'. Chi deve *fidarsi* di una release continua a leggere la catena
firmata; questo dice soltanto **dove guardare**. Un puntatore sbagliato o
mancante non deve far passare niente: i lettori che contano (catena, testa
richiesta, distribuzione verificata) restano quelli di adesso. Se preferite,
posso far rifiutare esplicitamente ogni consumatore quando il puntatore non
coincide con la release che la catena seleziona.

## Cosa costa a voi

Una funzione e una chiamata nello strumento che usate per rilasciare. Nessun
cambiamento alla semantica della crociera, nessun controllo tolto, nessun
ordine diverso. Serve anche a voi: qualunque strumento amministrativo futuro ha
lo stesso problema.

## Tre risposte possibili

1. **Lo aggiungo io** e lo rivedete voi prima del vostro prossimo rilascio.
2. **Lo aggiungete voi**, e io mi limito a consumarlo.
3. **No**: allora i comandi F5 restano non eseguibili finche' il Gruppo 7 non
   apre gli ingressi amministrativi con cancello, che e' la strada durevole ma
   e' lavoro a se'. In quel caso ditelo, cosi' lo scrivo nel piano come
   bloccante dichiarato invece di cercare scorciatoie.

Non tocco `rm0008_release_cycle.py` finche' non rispondete.
