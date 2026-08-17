# Review statica indipendente V26.5.2

Data: 9 agosto 2026.

Verdetto: **STATIC BLOCK**.

V26.5.2 riproduce correttamente tutti i risultati dichiarati, chiude le
mutazioni superficiali di `role/import_allowed` che bloccavano V26.5.1 e non
usa rete o modello. La falsificazione diretta trova però due confini ancora
aperti: l'unico accessor eseguibile accetta una mappa manifest arbitraria e
mutabile, e `CandidateCore` espone ancora una valutazione che non richiede la
query originale. Di conseguenza questo candidato non abilita wrapper, freeze,
gate o run live.

Il report machine-readable è
`metnos_v2652_independent_static_review.json`, SHA-256
`24839bac5499f9b2285fc16d7e395e03b87fc4b66723e411e09c36aa87283e59`.

## Identità dei byte revisionati

| Artefatto | SHA-256 |
|---|---|
| manifest | `c0761aef08a120843a33dfc86756eed3caf823f0b1ffd26d7a0bcc140b31d56a` |
| candidate core | `e62a1c3b24314736d8c5e8fcc0a52501bfe385bf2a8c17b65f423dabbf3a1ec3` |
| core result | `5cbcf034f43a0dca9807d1071e9d94d95555d40f9d2d8102e84940fd290eb9bc` |
| pre-import verifier | `3453a1eb3e8c81c454e31e723d7c900cc3aaf27859a448942858190b134d9cef` |
| verifier result | `057d9579b6552015981f1ac67d2b324f94897e5865bcda7e73653e705a4bbcc6` |

I cinque artefatti sopra sono rimasti byte-identici durante la review. Il
README precedente alla review aveva SHA-256
`1bbc2b365291b8dfcf01c3dc3081b6d221975b1918152475d12549d01f8e6ad9`.

## Risultati riprodotti offline

Tutti i comandi sono stati eseguiti con
`PYTHONDONTWRITEBYTECODE=1 python3 -B`.

| Controllo | Esito fresco | SHA-256 stdout | Confronto storico |
|---|---:|---|---|
| pre-import verifier | 43/43, 38 artefatti | `057d9579b6552015981f1ac67d2b324f94897e5865bcda7e73653e705a4bbcc6` | byte-identico |
| candidate core | 114/114 | `5cbcf034f43a0dca9807d1071e9d94d95555d40f9d2d8102e84940fd290eb9bc` | byte-identico |
| compact mutation suite | 106/106 | `3306d299b18726da1b903643d9c85763c05ec57680259c2ab05ddf0aa8bdb68f` | byte-identico |
| contamination audit | 15/15 su 213 query | `50e1c5931fe60bfb1007bf5624b1f836214c43fab98194350b66921549487c92` | byte-identico |

Il core 114/114 comprende 14 mutazioni di policy, 4 probe accessor, 6
positivi, 16 negativi nativi e 68 casi storici. I positivi coprono direct,
fan-out multi-azione, multi-dominio, riuso dell'output, ambiguità tipizzata e
coverage mista. La suite 106/106 conserva 56 mutazioni, classifica 3 casi
obsoleti e ne sostituisce 9; l'audit 15/15 trova zero query intere, n-grammi
di almeno tre token o righe surface/mixed sovrapposte nei corpus 109+34+70.

## Cosa passa

- Il core e il verifier hanno le stesse **9/9** policy obbligatorie, e il
  manifest corrente corrisponde a tutte.
- `import_allowed` è un booleano esatto. `false`, campo mancante, stringa
  `"true"` e role swap sono respinti; passano tutte le 14 mutazioni dichiarate.
- Nel normale `CandidateCore.load`, `verified_manifest` verifica struttura,
  policy, path e hash dei 38 artefatti prima di eseguire adapter o validator.
- L'AST contiene un solo sink per sorgenti artifact:
  `exec(compile(...))` alla riga 186 del core. Schema, adapter e validator sono
  percorsi in quest'ordine dal normale entrypoint.
- La query originale resta byte-per-byte nella request. Sette probe Unicode
  indipendenti — apostrofo tipografico, combining mark, CJK, thai, arabo,
  emoji ZWJ e CRLF — conservano la stringa e ogni span torna esattamente alla
  sottostringa sorgente. `regex 2026.3.32` e Unicode `15.0.0` sono pin-nati.
  Questo prova preservazione e allineamento, non sostituisce l'intero corpus
  conformance UAX #29.
- Il verifier corrente, il core, l'adapter e il validator non importano
  trasporto. L'audit hook ha osservato zero eventi rete e zero letture di
  `.pyc` sotto il repository. I `.pyc` preesistenti non sono stati usati né
  modificati.
- Il verifier ha una propria entry nel manifest, la policy esatta
  `audit_tool/true` in `REQUIRED_POLICIES` e l'hash corretto.

Questi PASS sono reali ma non compensano i blocker seguenti.

## B1 — l'accessor eseguibile non è legato al manifest verificato

Blocker causale.

`_artifact_bytes` e `_source_module`, righe 158-187 del core, ricevono dal
caller sia `entries` sia il ruolo atteso. Non verificano che la mappa sia
quella prodotta da `verified_manifest`, non la rendono immutabile e non
ricontrollano path repository-relative o appartenenza al manifest originale.
`CandidateCore.entries`, inoltre, rende pubblica la stessa mappa mutabile.

Tre falsificazioni indipendenti riescono:

1. Dopo una verifica valida, una deep copy delle entry promuove
   `metnos_v2651_validator_generator.py` da `evidence/false` a
   `runtime_input/true`. `_source_module` ne esegue il sorgente.
2. Una mappa costruita da zero contiene un path Python assoluto sotto `/tmp`,
   il suo hash reale e `runtime_input/true`. `_source_module` lo esegue anche
   se non è mai appartenuto al manifest verificato.
3. Dopo una verifica valida, una deep copy promuove lo schema JSON a
   `runtime_input/true`. Un canary su `compile` dimostra che il dato raggiunge
   il sink eseguibile.

Le quattro mutazioni accessor incluse nel self-test cambiano soltanto in
peggio adapter o validator. Non provano elevation post-verifica, mappe
forgiate, path assoluti o promozione data-to-code.

Impatto: il singolo sink `compile/exec` è strutturalmente unico, ma la sua
autorità non è fail-closed. Manifest membership, path policy e separazione
dati/codice possono essere scelti dal caller.

## B2 — `evaluate_segments` aggira il grounding pubblico

Blocker causale.

`CandidateCore.evaluate_segments`, righe 309-340, è ancora un metodo pubblico.
Accetta direttamente `compact_frame` e una lista arbitraria di segmenti. Il
probe passa il primo positivo e una lista di soli oggetti `{"id": n}`: il
risultato è `stage=accepted` senza query originale, testo o offset sorgente.

Il metodo `evaluate(original_request, compact_frame)` alle righe 342-344 è il
confine corretto, perché deriva internamente i segmenti. La presenza del
secondo entrypoint pubblico rende però aggirabile quel confine.

## Verifier self-entry e marker costruiti

La self-entry corrente è coerente e viene hashata. Non elimina, né pretende di
eliminare, la circolarità: una sostituzione sincronizzata di verifier e
manifest può essere rilevata soltanto da una futura radice esterna che pinni
manifest, core, verifier e questa review prima di ogni import. Questa scelta è
correttamente dichiarata e non è un blocker aggiuntivo.

I marker costruiti producono esattamente `/tmp/`, `urllib`, `http.client`,
`socket` e `requests`, evitando il falso positivo sul sorgente del verifier.
La scansione a sottostringa non è però un controllo fail-closed generale:
il sorgente non eseguito `__import__("so"+"cket")` supera la scansione. Alias,
encoding e helper dinamici hanno lo stesso problema. Nei byte correnti non è
stato trovato trasporto, quindi questo è un warning di design e non evidenza
di una chiamata rete avvenuta; il futuro wrapper non deve usare i marker come
unica prova.

## Correzioni richieste per V26.5.3

1. Nessun accessor di lettura o esecuzione deve accettare `entries` o mappe
   manifest dal caller.
2. Ogni read/exec deve rientrare da `verified_manifest`, usare la policy
   `REQUIRED_POLICIES` dell'identità esatta, respingere path assoluti, alias e
   non-member, e ricontrollare l'hash immediatamente prima dell'uso.
3. Le entry verificate devono essere immutabili e non esposte sul core. Le
   identità eseguibili devono essere fisse; schema, registry, prompt, fixture
   ed evidence non devono poter essere promossi tramite argomenti.
4. Rimuovere l'API pubblica `evaluate_segments`. L'unico entrypoint pubblico
   deve ricevere `original_request` e segmentare internamente; i test devono
   usare richieste sintetiche e segmentazione reale.
5. Aggiungere mutazioni per mappa forgiata, path assoluto/alias, elevation
   post-verifica di evidence e schema, mutazione di `core.entries` e
   valutazione id-only.
6. Per l'assenza di trasporto usare pin esterno esatto, analisi sintattica
   allowlist e negazione di rete a livello processo; non soltanto marker byte.

## Limite di autorizzazione

La review non ha modificato manifest, core, verifier o risultati candidati e
non ha creato freeze o gate. Ha effettuato zero chiamate rete e zero inferenze.
Il verdetto **STATIC BLOCK** autorizza soltanto la preparazione e review
offline di V26.5.3. Non autorizza wrapper, freeze, gate o run live.
