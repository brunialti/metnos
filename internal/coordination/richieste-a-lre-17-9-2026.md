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
