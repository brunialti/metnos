# R-006 — causa del rifiuto dell'ancora RM-0008 2A

Riscontro Codex del 22/9/2026. Diagnosi e correzione verificate sul ramo
`codex/r006-anchor-repair`, derivato da `2ac90907`. La correzione ripristina
il riferimento gia' validato e aggiorna le impronte collegate nel candidato.
Nessuna installazione, pubblicazione esterna o operazione sui servizi e sul
magazzino dei contratti. Resta da integrare e ricertificare sul pubblico.

## Causa verificata

Il commit pubblico
[`b01b2118`](https://github.com/brunialti/metnos/commit/b01b2118ccb583abf62f2febb847a69345dd9220)
del 18/9, `Protect active LRE jobs and add safe task dismissal`, ha
riportato `RM0008_ACCEPTANCE_EVOLUTION_SHA256` in
`runtime/contract_boundary_guard.py` al valore del 2/9, senza riportare
indietro `tests/portable/test_rm0008_acceptance_evolution.py`.

Il test era stato esteso il 16/9 con quattro casi sul prerequisito di
importazione delle prove. Il suo contenuto attuale e' identico a quello
del predecessore di `b01b2118`, dove il riferimento corrispondeva.
Non e' una modifica introdotta dall'esclusione del test privato F5 il 20/9.

| Versione pubblica | Impronta attesa | Impronta del test | Confronto |
|---|---|---|---|
| `937eeac3`, 2/9 | `1babce04…` | `1babce04…` | coincide |
| `d99d2b8b`, 16/9 | `67eb6e54…` | `67eb6e54…` | coincide |
| `74a10959`, 16/9 | `67eb6e54…` | `67eb6e54…` | coincide |
| `9eac7814`, predecessore del 18/9 | `67eb6e54…` | `67eb6e54…` | coincide |
| `b01b2118`, 18/9 | `1babce04…` | `67eb6e54…` | differisce |
| `f3ac40dc` e `92eab91e`, 20/9 | `1babce04…` | `67eb6e54…` | differisce |
| `aaddc391`, ultimo `main` pubblico riletto il 22/9 | `1babce04…` | `67eb6e54…` | differisce |

Valori completi:

- riferimento vecchio: `sha256:1babce04a78b8345cbacb9bf5677bebade3958e655f0dc45884ad70636322167`;
- riferimento corrispondente al test gia' presente e accettato prima del
  18/9: `sha256:67eb6e545ec299e11431a544732edfdb3522304c064ea078b0cd14ddeeaef5c4`.

## Punto del rifiuto e prova

`run_activity_v1.run_activity` in modalita' `final` chiama
`validate_snapshot_aggregate` prima di selezionare o eseguire le celle.
Questa passa da `_validate_frozen_acceptance_baseline` e
`_validate_current_exact_acceptance_blobs`. Il confronto tra il contenuto
Git del test e il riferimento obsoleto causa il rifiuto osservato.

Riletto anche l'ultimo ciclo pubblico:
[esecuzione `35521704038`](https://github.com/brunialti/metnos/actions/runs/35521704038),
commit `aaddc3911808fb1187812ffe42e75c675a4634c0`, controllo manifest
`106106793243`. Nel registro, alle `2026-09-20T16:07:34.5984047Z`:

```text
RM-0008 2A activity failed: current acceptance anchor differs: tests/portable/test_rm0008_acceptance_evolution.py
```

Scaricati i due file pubblici a quel commit e confrontati byte per byte:
entrambi sono identici a quelli della copia `92eab91e` usata per la
controprova. Il disallineamento resta quindi presente nell'ultimo `main`
osservato, non soltanto nelle ricevute storiche.

Quattro verifiche eseguite sul validatore canonico, senza riscrivere file:

1. Riferimento corrente e test corrente: rifiuto esatto riprodotto.
2. Stesso test e riferimento estratto dal predecessore `9eac7814`:
   accettazione.
3. Stesso riferimento precedente, un ritorno a capo aggiunto al test:
   rifiuto.
4. Stesso riferimento precedente, test sostituito da un solo docstring:
   rifiuto.

La sostituzione del riferimento e' durata soltanto nel processo di
controprova ed e' stata annullata al termine. Non e' una certificazione
complessiva verde: non sono state eseguite le celle manifest ne' attribuiti
gli altri errori della suite a questa stessa causa.

Ricevute locali: `/tmp/metnos-r006-20260922.r7b3plig/anchor-proof.json`,
`current-public-manifest.log` e i due file sotto `current-public/`.
La ricevuta JSON riporta tutti i commit completi, i due valori per ogni
confronto, le quattro verifiche e il collegamento all'ultima CI.

## Correzione preparata e verifica conclusiva locale

Ripristinato il riferimento `67eb6e54…` gia' associato agli stessi byte
del test, conservando tutte le prove. Il solo ritorno al test del 2/9
eliminerebbe i quattro controlli successivi. La diagnosi interna del merge
`0f922c5c` e' conservata nel rapporto
`rm0008-r006-acceptance-anchor-20260922.md`, acquisito dal gestore F5 nel
commit di base `2ac90907`.

La costante vive in un sorgente censito: la modifica richiede anche la
verifica e il riallineamento delle impronte dei sorgenti del candidato e
della proiezione pubblica. Eseguito
`internal/tools/rm0008_repin_source_roots.py` sul solo ramo isolato, poi
verificato il risultato con il controllo indipendente `private-fs`.
L'unica modifica funzionale e' il ripristino della costante; gli altri
cambiamenti sono i riferimenti derivati. Test, manifest di accettazione e
workflow non sono stati modificati.

| Candidato | Sorgenti Python | Impronta |
|---|---:|---|
| Ramo privato isolato | 785 | `sha256:5357e95d3aff63ce64d89a5644ec2a3f85e5129e5527268808614618714504cb` |
| Proiezione di quel ramo | 773 | `sha256:fe32eae33986467fd4b736c306f60a8b813728ddab9688c9dee60480a6aba14d` |
| Copia del pubblico con la sola correzione | 778 | `sha256:bcff15a0060c1fa8ec51816aa2a52cfcb39073994f8bd702489d309f511bc163` |

Le due proiezioni pubbliche sono diverse: il ramo F5 non contiene tutti
gli aggiornamenti successivi presenti sul pubblico. La prova usa percio'
una copia separata di `92eab91e`, conservando i suoi sorgenti e applicando
solo la correzione e i tre riferimenti collegati. Non usare l'esportazione
integrale del ramo F5 per sovrascrivere il pubblico corrente. Le impronte
devono essere verificate di nuovo sull'albero finale integrato.

Risultati:

- **14/14** prove di `test_rm0008_acceptance_evolution.py` superate sul
  candidato privato; i due controlli negativi della controprova restano
  attivi;
- copia pubblica, commit locale non pubblicato
  `a4a276d5222c6c79e29f74164bef8b4be183b39d`: **7/7** celle manifest
  superate attraverso `run_activity_v1.py --activity manifest --mode final
  --import-mode=importlib`, con codice di uscita zero e ricevuta canonica;
- inventario dei confini pubblico rigenerato e verificato; impronte dei
  sorgenti verificate; confronto Git senza errori di formato.

Ricevute sotto `/tmp/metnos-r006-20260922.r7b3plig/`:
`private-anchor-tests.log`, `private-repin.log`, `candidate-manifest.log`,
`candidate-manifest-result.json` e `candidate-manifest-evidence.json`.
La ricevuta manifest contiene sette risultati sul commit `a4a276d5`, con
impronta del manifest
`sha256:2ae9687f712ec396c8e5c768cdbf20bd75a24ec94a4c878859babf0e83e6421e`.

Non e' un esito verde dell'intera CI remota. Dopo l'integrazione e la
revisione dell'albero finale, ottenere e registrare il nuovo controllo
pubblico nella R-006. La voce resta aperta fino a quella ricevuta. R-001
e R-003 conservano i rispettivi vincoli sul rilascio.
