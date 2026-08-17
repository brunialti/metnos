# Adversarial self-review — candidate 0.1

Scope: deterministic offline slice only. No inference, GPU, service, runtime
or production code was used.

Checks performed:

- all 120 canonical expected values plus the 4 typed controls round-trip
  through strict decode, validation, normalization and semantic projection;
- the three required roots are distinct; malformed/non-finite/over-limit
  output remains technical invalidity rather than `unrepresentable`;
- `undo_last_turn` rejects inputs under the current registry;
- omitted `rejected` outcome is materialized empty, while `approved` owns its
  operation and yields one immutable hash-bound continuation;
- forward, sibling, branch-to-outer, port-invalid and duplicate data edges are
  rejected;
- outcome duplication, order, unknown values and empty emitted bodies are
  rejected;
- continuation registry/root/path/outcome/body/hash tampering is rejected;
- a synthetic registry renames the only control, barrier, outcomes, route and
  reason; projection and extraction still work and old names disappear;
- prompt/schema contamination audit covers 120 + 4 + 34 query strings;
- the runner contains no oracle/evaluator/builder import and persists no query
  text; a fake transport permits deterministic end-to-end evaluation;
- the evaluator rejects forged saved projections by reconstructing each
  extraction from the persisted raw bytes before opening gold, then reports
  root, route, data-edge, barrier, control and abstention columns separately;
- the 34 legacy controls remain a separate query-only panel bound to the
  Phase-1 oracle and excluded from typed accuracy totals.

Known limits:

- the schema is recursive and intentionally has no semantic nesting maximum;
  offline parser guardrails are configurable technical limits. Backend schema
  compatibility and final limits require the future pre-GPU protocol;
- no runtime execution or approval dialogue is simulated. Continuations are
  constructed and verified only;
- the candidate judges exact normalized intent structures, not executor
  arguments, credentials or production feasibility;
- double-AI oracle limits remain unchanged; the implementation does not add a
  human review claim;
- Phase-1 evaluation of the 34 legacy controls is not reimplemented here, to
  preserve the authority and separation approved by Roberto.

No new semantic policy was needed. GPU-control identity, order, model, seed,
budget, timeout, final technical bounds and verdict denominators remain for a
separate authorization before inference.

Final deterministic matrix: 7/7 scenario groups pass, including 124/124 typed
round-trips; 57/57 negative mutations are rejected and 7/7 positive controls
are accepted. Candidate verifier and query-suite builder both report
`error_count=0` after the final seal.

## Functional review cycle 2

The independent functional cycle 1 found three blocking boundary defects. All
three are closed without changing any query, oracle `expected`, registry
meaning or panel membership:

- hostile but bounded JSON is total: wrong-type barrier outcomes, oversized
  integers, invalid UTF-8, Unicode surrogates and decoder recursion become a
  deterministic technical/document invalid envelope and never an exception or
  `unrepresentable`;
- every saved-batch object is closed and recursively exact-typed before gold
  access; booleans cannot pass as integers, and replay compares canonical JSON
  bytes rather than Python's permissive value equality;
- evaluation first verifies the sealed candidate checkpoint and then runs the
  pinned canonical oracle verifier over oracle, freeze, lock and all bound
  sources. Missing, wrong, modified and coherently re-sealed-but-unsupported
  oracle variants fail closed before scoring.

The cycle-2 mutation matrix contains 89 negative cases and 7 positive cases.
The added negatives cover every reported reproducer plus type variants and the
three oracle-authority surfaces. The seven scenario groups and all 124 typed
round-trips remain unchanged. No GPU, network, service or production path was
used, and no new semantic policy was introduced.

## Protocollo dell'unica misura — preflight locale

Roberto ha approvato le sette decisioni di protocollo. Sono ora congelati:
controllo corrente e adapter; identità di modello/backend/pesi; profilo comune;
ordine AB/BA; 316 richieste; pannelli 120+4+34 separati; gate di verdetto;
limiti tecnici; consumo e arresto senza retry.

Controlli avversariali aggiunti:

- manifest query-only con 158 identità e 316 coppie, nessun gold;
- impronte complete di sorgenti, lessico logico, binario e pesi;
- tipi JSON esatti, chiavi duplicate, NaN/overflow, omissioni/sostituzioni di
  autorità e profilo alterato;
- marker esclusivo prima del socket, blocco permanente al rerun, journal
  append-only, checkpoint atomici, stop parziale su trasporto/timeout;
- JSON o semantica invalidi contano errore e il fake batch continua;
- replay dell'estrazione da content bytes prima dell'apertura gold;
- valutazione separata A/B e legacy Phase-1 senza compensazione.

Limite esplicito: il runner live esiste ma l'autorizzazione vincolata ad audit
indipendente e autorizzazione finale root è assente. Non è stata aperta alcuna
connessione né consumata la misura. Il protocollo è pronto per revisione
indipendente, non ancora per esecuzione.

Esito locale finale del protocollo: 7/7 prove positive; 41/41 mutazioni
negative respinte; 5/5 controlli positivi accettati; manifest 316/316;
verificatori di protocollo, candidato e oracle con `error_count=0`; preflight
`prepared_not_authorized`, `network_touched=false`, `gpu_touched=false`.

## Correzione audit preflight — gate di astensione

Il gate del verdetto ora consuma lo stesso insieme canonico di nove colonne
congelato nel protocollo, compresa `correct_abstention`. Il loader rifiuta
duplicati, omissioni e sostituzioni; l'evaluator pubblica l'elenco applicato
nel report. Una prova negativa con la sola regressione di astensione impedisce
`candidate_pass`; la prova gemella senza regressioni lo consente. La suite
live sale a 9/9, mentre le mutazioni restano 41/41 negative e 5/5 positive.

L'harness condivide in memoria l'hash delle autorità grandi solo finché inode,
dimensione, mtime e ctime restano invariati; una variazione durante il calcolo
fallisce chiusa. Ciò evita riletture ripetute dei pesi nella stessa suite senza
indebolire il vincolo SHA-256. Nessun POST, marker, autorizzazione o inferenza
è stato prodotto.

## Armamento e guard monouso

L'auth superseded `597037352ac6e10875499c42545184999afeb0ff3b97e0bf0352bb20e42e68bf`
non ha consumato la misura. Il verifier distingue ora `disarmed` da `armed`:
solo nel secondo stato accetta esattamente l'auth indicata e ne valida tutti i
binding. L'auth è esclusa dal guard dei veri artefatti di run; marker, journal,
checkpoint, partial, batch, seal, output e file live inattesi lo bloccano.

Il percorso `run_authorized_once` esegue lo stesso preflight armato usato da
`--execute-once`. Il test raggiunge un transport sentinel su directory
temporanea, senza endpoint, e prova i rifiuti di auth mancante/extra/stale e
di ciascun artefatto del guard. Consumo e zero retry restano invariati.

## Replay/evaluator 0.2 — scelta A e controllo pre-gold

La misura completa non è stata modificata. Il nuovo controllo 0.2 ricostruisce
316/316 contenuti dai byte HTTP, verifica journal, marker, checkpoint, batch e
sigilli e ricalcola ogni estrazione prima di consentire l'accesso al gold.

L'unica tolleranza è un booleano presente in entrambe le copie al puntatore
`adapter_metadata.implicit_actions_ignored`, legato all'indice zero-based 80 e
alla sua identità completa. Il confronto conserva e pubblica `saved` e
`replay`; non elimina il campo. Ogni altro scarto fallisce chiuso.

La matrice finita è 12/12: include i due versi booleani ammessi e il rifiuto di
campo mancante, tipo non booleano, altro metadata, semantica, doppio scarto e
indice diverso. Due processi completi confermano seed 0 = una sola tolleranza,
seed 2 = zero tolleranze, sempre 0 scarti inattesi. Una sentinella impedisce
l'accesso a entrambi i gold quando il controllo non passa.

Revisione avversariale: l'eccezione non è applicata per solo nome campo, non è
estesa agli altri record e non usa l'uguaglianza permissiva di Python. Freeze
e report sono aciclici. Il valutatore 0.2 non è stato eseguito e l'oracolo è
rimasto chiuso. Nessuna nuova policy è risultata necessaria.
