# Handover RM-0008 — passaggio produttivo riuscito l'8 settembre 2026

> AGGIORNAMENTO 9/9: dopo il guasto al reboot, HTTP/Telegram/browser/LRE sono
> nuovamente operativi grazie al verificatore d'avvio circoscritto al servizio.
> Il target ordinario e il controllo generale sono riusciti; richiesta reale e
> notifica Telegram verificate. RM-0008 non è interamente chiusa. Prima di agire
> leggere `internal/reports/rm0008-maintenance-analysis-20260909.md`, che distingue
> le modifiche installate da quelle soltanto testate/pubblicate. NON rilanciare
> `/tmp/m8`, reset, transizione o la riparazione a predecessore esatto.

> STATO CORRENTE AUTOREVOLE: r18 `b715a765` installato e verificato in esercizio.
> CLI exit0, catena firmata verificata, HTTP/browser/LRE attivi; una richiesta
> reale `che ore sono?` riuscita e riletta nel registro, stesso processo HTTP.
> Il precedente blocco di avvio LRE è corretto. NON rilanciare `/tmp/m8` né i
> tentativi precedenti. Le sezioni datate6/9 qui sotto sono storia conservata;
> gli ultimi aggiornamenti in fondo riportano prove e percorsi correnti.
> Anche Tutor è compilato e verificato con un turno reale fondato.
> Riepilogo corrente: `internal/design/handover_rm0008_verifica_8_9_2026.md`.
> Pubblicazione documenti/Git e raccolta RM-0009 sono nel closeout documentale.

Data: 2026-09-06  
Obiettivo: completare il cutover produttivo del Birth Gate RM-0008 in modo KISS, senza fallback sulla vecchia versione.

## Stato reale

- Il cutover **non è ancora partito**.
- L’ultimo comando dell’operatore è fallito immediatamente con:
  `exec: /tmp/metnos-rm0008-authorize-617f059f.sh: Permission denied`.
- Causa: il target aveva mode `664`, mentre lo shortcut `/tmp/m8` era eseguibile.
- Il bit eseguibile è stato corretto; il contenuto non è stato modificato.
- Ultimo comando da lanciare una sola volta:

```bash
sudo /tmp/m8
```

Il comando redirige tutto in:
`/var/lib/metnos-admin/rm0008-mobile.log`.

## Artefatti finali

Bundle previsto: `/var/lib/metnos-admin/rm0008-final-617f059f`  
Sudoers previsto: `/etc/sudoers.d/metnos-rm0008-617f059f`  
Nessuno dei due risultava ancora installato al momento di questo handover.

Shortcut: `/tmp/m8`  
Authorizer: `/tmp/metnos-rm0008-authorize-617f059f.sh`  
Controller: `/tmp/metnos-rm0008-controller-617f059f.sh`  
Reset: `/tmp/metnos-rm0008-reset-617f059f.py`  
Helper reset: `/tmp/metnos_rm0008_reset_fs_617f059f.py`  
Verifier successo: `/tmp/metnos-rm0008-verify-success-617f059f.py`  
Verifier ambiente: `/tmp/metnos-rm0008-verify-environment-617f059f.py`

## Identità e pin

- Commit prodotto: `5b50b3d6a6248a7b6c77395317e14101c61b0f13`
- Source ID: `sha256:617f059f9325c35171b652cc1776c8b5febd88a3ef9412f4cc31c1f001572e98`
- Archivio source SHA-256: `bb02cc79d636c558ff5589a1a8547b5667c59e6ec77817d85d015c3b3af21e94`
- Controller SHA-256: `a6bd666445ea4cdeb0f7d4adc20f6f1a1e1fe03dbbd9184eeea92517cfe82fcb0d`
- Reset SHA-256: `9067d9f074d989a0135cefffa955eeb2c8d7e38ef0bdbaef5c7857fb067234bb`
- Helper SHA-256: `5d9a959aee29d6ce8e997e9a2332dabc33540cbd8a91278807b18fc9bb9cbf17`
- Authorizer SHA-256: `00a6d0ebd008f9da653ca6ef13568addb7a3e1e9d4e18c837fcf0c7e91e3fe3e`

Pinset locale già verificato: `FINAL_PINSET_OK 8`.  
Shell, Python, pyflakes, replay reset e sudoers già verificati: `FINAL_LOCAL_GATE_OK`.

## Cosa fa il comando

1. Verifica archivio, source ID, pin e ambiente.
2. Revoca la vecchia regola sudoers solo dopo aver validato la nuova.
3. Installa il bundle root-owned in modo atomico.
4. Esegue il reset KISS della vecchia transizione solo se lo stato è esatto e senza transazioni V2 vive.
5. Avvia il cutover tramite unit systemd transient.
6. Verifica il risultato come utente `metnos`.
7. Rende attivo `metnos.target` e controlla readiness dei servizi esterni.

Il reset non accetta stati ambigui: transazioni V2 vive o journal legacy non vuoto producono NO-GO esplicito. Non aggiungere patch permissive.

## Dopo il lancio

Leggere:

```bash
sudo tail -n 200 /var/lib/metnos-admin/rm0008-mobile.log
```

Esito valido: presenza di `CUTOVER_OK`/`RESULT_OK` e bundle installato.  
In caso di errore, non rilanciare a tentativi: conservare integralmente il log e analizzare lo stage indicato.

Solo dopo esito positivo:

- verificare unit e readiness;
- fare censimento finale;
- rimuovere vecchi artefatti e script one-off secondo piano di retirement;
- aggiornare documentazione pubblica;
- fare deploy incrementale su Git pubblico.

## Vincoli

- Nessun fallback o restore della vecchia Birth Gate.
- Nessuna cancellazione preventiva prima di `CUTOVER_OK`.
- Non modificare pin, source o controller senza nuova revisione completa.
- Il working tree contiene modifiche di prodotto già presenti: non usare reset distruttivi e non sovrascriverle.

## Nota per l’agente subentrante

L’operatore ha dichiarato che questo è l’ultimo lancio. Se il comando termina con successo, procedere direttamente a verifica, pulizia controllata e documentazione. Se fallisce, fermarsi sul primo errore concreto e riferire causa, file e rimedio minimo; niente catena di tentativi.

## Aggiornamento post-ultimo lancio (2026-09-06)

Il comando finale è stato eseguito. Risultato registrato in `/var/lib/metnos-admin/rm0008-mobile.log`:

```text
RM-0008 exact bundle installed; production cutover is starting.
RESET_REFUSED RuntimeError live V2 transaction requires separate recovery
RM-0008 refused: exact incomplete-attempt reset was refused
```

Il bundle nuovo e il sudoers nuovo risultano installati. Il cutover produttivo **non è completato**. Il reset ha rifiutato correttamente di terminare una transazione V2 viva; non cancellare directory o journal senza prima identificare esattamente transaction-id, owner e checkpoint. La prossima attività deve essere una recovery esplicita della transazione V2, quindi una nuova verifica e un solo rilancio del controller.

## Aggiornamento autorevole: recovery V2 terminale pronta (2026-09-06)

È stata preparata una recovery fail-closed specifica per il residuo V2 dell'attempt
`82acf85a`. Non elimina dati: accetta soltanto una singola transazione V2 completa e
verificata (`created -> verified`), ne valida header, request/build, material plan,
checkpoint, payload, owner/mode/xattr/inode, set-id e assenza del target pubblicato,
quindi la rinomina atomicamente nella quarantena root-owned:

`/var/lib/metnos-admin/rm0008-final-617f059f/recovery-v2-82acf85a/transaction-v2`

Plan e completion marker sono persistenti e rendono la recovery ripetibile dopo un
crash. Qualsiasi transazione non terminale, doppia, mutata, già pubblicata o non
appartenente all'attempt 82 viene rifiutata prima della modifica.

Artefatti nuovi e pin:

- recovery: `/tmp/metnos-rm0008-recover-v2-617f059f.py`
  - SHA-256 `aefaf8fc5f3b47122c4730e0abb915b662c34444ff80f0b15ab561ad3a485a22`
- wrapper recovery + activate: `/tmp/metnos-rm0008-recover-and-activate-617f059f.sh`
  - SHA-256 `30f8d1d73a646cd3eb74c08deadf755ab879da87db696e921dd6b298cebe4b59`
- shortcut finale: `/tmp/m8`
  - SHA-256 `453edae025faca4b65a5c0b61b5ea9352a31251363b86dae69cd80743fa34a62`

Gate conclusivo superato:

```text
V2_RECOVERY_REPLAY_TEST_OK seams=3 refusals=5
RECOVERY_CHAIN_OK recovery=aefaf8fc5f3b47122c4730e0abb915b662c34444ff80f0b15ab561ad3a485a22 wrapper=30f8d1d73a646cd3eb74c08deadf755ab879da87db696e921dd6b298cebe4b59 shortcut=453edae025faca4b65a5c0b61b5ea9352a31251363b86dae69cd80743fa34a62
```

Sono passati inoltre `py_compile`, `pyflakes`, `bash -n`, `sh -n` e `shellcheck`.
Il controller installato è ancora integro e il controllo root read-only restituisce:

```text
RM-0008 STATUS_INCOMPLETE source=617f059f
```

Il solo comando operativo rimasto è:

```bash
sudo /tmp/m8
```

Questa versione dello shortcut esegue in una sola invocazione: verifica dei pin,
installazione atomica della recovery nel bundle, recovery terminale V2 e immediato
`controller.sh --locked activate`. Non eseguire separatamente recovery o controller.
Il successo richiede nel log `V2_RECOVERY_OK` (oppure replay
`V2_RECOVERY_ALREADY_OK`) seguito da `RM-0008 CUTOVER_OK` o
`RM-0008 CUTOVER_ALREADY_OK`; confermare infine `RM-0008 STATUS_OK`.

## Aggiornamento autorevole r2 — sostituisce il blocco precedente (2026-09-06)

Il primo lancio della recovery ha rifiutato **prima di qualunque mutazione**:

```text
V2_RECOVERY_REFUSED RuntimeError V2 checkpoint chain is not created-to-verified
```

La diagnosi è stata eseguita tramite bind mount temporaneo read-only, poi smontato.
Lo stato reale è conforme al protocollo V2:

- transaction-id `211cc8bd748931efd0c4a7c6741613d5`;
- request/build coincidono con l'attempt `82acf85a`;
- predecessore `fac2e29592f936024a39cdfc1a2834e6ac6a3d48784511bc9834b51dd8e7fe8a`;
- target staged `60c0e9a2e665ce64748043544e4c15e30f4f7220c1c933708825ac49dd2a24bc`;
- checkpoint 0 `created`, checkpoint 1 `verified`, catena digest valida;
- il target non è pubblicato; in `authority-sets` è presente soltanto il predecessore.

La causa del falso rifiuto era una precondizione errata nel one-off: V2 riusa
l'author store già verificato e quindi
`author_source_public_inventory_sha256` deve essere `null`. Gli altri digest
devono essere presenti e `author_store_public_inventory_sha256` deve coincidere
con il marker del predecessore. La r2 implementa esattamente questa regola e
aggiunge due test negativi per source digest inatteso e author store sganciato.

Nessuna directory `recovery-v2-82acf85a`, plan, completion marker o quarantena è
stata creata dal rifiuto. Il vecchio verificatore installato è conservato come
evidenza; la r2 viene installata con un nome nuovo.

Pin correnti (gli hash del blocco precedente sono superseded):

- recovery r2 `/tmp/metnos-rm0008-recover-v2-617f059f.py`:
  `2d7667f471f916003b767c56155b86f6e342b907a38b588e330f99f87d3f1375`;
- wrapper `/tmp/metnos-rm0008-recover-and-activate-617f059f.sh`:
  `2ffed0d33d32c18267321c3dd253ce560ec8c82af5f0c850fe15a166e0a92c57`;
- shortcut `/tmp/m8`:
  `760cfeac73f7d540f940ede8f0eff56655c466d9f301af361a91784621141a5a`.

Gate r2:

```text
V2_RECOVERY_REPLAY_TEST_OK seams=3 refusals=7
FINAL_R2_GATE_OK recovery=2d7667f471f916003b767c56155b86f6e342b907a38b588e330f99f87d3f1375 wrapper=2ffed0d33d32c18267321c3dd253ce560ec8c82af5f0c850fe15a166e0a92c57 shortcut=760cfeac73f7d540f940ede8f0eff56655c466d9f301af361a91784621141a5a
```

Comando operativo corrente, ancora unico:

```bash
sudo /tmp/m8
```

## Aggiornamento autorevole r3 — recovery legacy integrata (2026-09-06)

Questo blocco sostituisce come istruzione operativa tutti i blocchi precedenti.
L'ultimo lancio r2 ha completato e reso durevole la recovery V2, poi il reset
KISS ha rifiutato il secondo residuo storico:

```text
RM-0008 terminal V2 recovery is starting.
V2_RECOVERY_OK
RM-0008 terminal V2 recovery is durable; production cutover is starting.
RESET_REFUSED RuntimeError legacy journal requires separate recovery
RM-0008 refused: exact incomplete-attempt reset was refused
```

Non è partita alcuna unità di transizione o verifica-successo. Release, incoming
e claim dell'attempt 82 sono ancora live e integri. La V2 è invece già nella
quarantena durevole
`/var/lib/metnos-admin/rm0008-final-617f059f/recovery-v2-82acf85a`.

Il journal legacy live è stato censito read-only e autenticato contro il codec
della source congelata. È la catena terminale esatta dell'attempt 82:

- record: `record-000.json` .. `record-003.json`;
- stati: `PLANNED -> INVENTORIED -> AUTHORING_ADOPTED -> LEGACY_STATE_READY`;
- legacy request:
  `sha256:33a19153fc7eef28c1edf022bb4ddabf70cc32389789b77e14d6c8e43c9093b0`;
- policy:
  `sha256:cbbc2c326de1aef494f9a31a6ab0437e7f67e4af018de0e09920962347752f12`;
- osservazione READY stabile `exact-service`:
  `sha256:53751321ca83e46322825f7236625200d980f73fa3aa77906e077b5692bc2b10`;
- SHA-256 file, in ordine:
  `6fcf62fd38a500d1c2bc5a338da05305d23c94e207f6ed6bdfa448fe434f863a`,
  `04854c7bbb34a1ddb24761d15badc3b3bd138b01a65ea904531207a198045b17`,
  `f9df12f0fd7ca95ef2c4e1f24957a1ae28294d75cdaf5d7df9c246315d217081`,
  `d14ac04bff653315d205ca0b15a1831b242bcbbcf537156bb63ff4dbb57b2407`.

È stata aggiunta una recovery fail-closed separata. Sotto l'ordine lock canonico
deployment -> provisioning -> startup gate -> cutover guard -> journal, essa:

1. riesegue il verificatore V2 r2 installato e pretende stato `complete`;
2. deriva nuovamente request e policy dalla build vecchia e dall'account
   `metnos`;
3. osserva due volte lo stato e pretende identità READY `exact-service`;
4. autentica bytes, metadati, catena, record-id e lock vuoto;
5. rinomina atomicamente i record in ordine inverso `003 -> 002 -> 001 -> 000`,
   mantenendo sempre un prefisso journal valido;
6. pubblica un completion marker durevole e diventa replay-safe;
7. dopo il completamento consente journal successori, compreso il seam POSIX
   final+pending hard-linked, ma rifiuta la ricomparsa di qualunque digest
   ritirato.

Artefatti e pin r3:

- legacy recovery `/tmp/metnos-rm0008-recover-legacy-617f059f.py`:
  `58b45748fb03dab0c2644c79b5b4bd6df471ec97cb0d76acccdbbb457c337c44`;
- wrapper integrato `/tmp/metnos-rm0008-recover-and-activate-617f059f.sh`:
  `be6752943e8b3d5ba7b98bdaec70d37443ac646d0b74031d96999ee84ea1e271`;
- shortcut `/tmp/m8`:
  `9ebc4cb2417b90e30633a86389fb396ff70a140994ad397295cbddfcbec1dbb8`.

Gate conclusivi:

```text
V2_RECOVERY_REPLAY_TEST_OK seams=3 refusals=7
LEGACY_RECOVERY_REPLAY_TEST_OK seams=7 refusals=13
RESET_REPLAY_TEST_OK seams=4
FINAL_LEGACY_GATE_OK legacy=58b45748fb03dab0c2644c79b5b4bd6df471ec97cb0d76acccdbbb457c337c44 wrapper=be6752943e8b3d5ba7b98bdaec70d37443ac646d0b74031d96999ee84ea1e271 shortcut=9ebc4cb2417b90e30633a86389fb396ff70a140994ad397295cbddfcbec1dbb8
```

Sono passati anche `py_compile`, `pyflakes`, `bash -n` e `sh -n`. La revisione
adversarial finale ha dato GO.

L'unica invocazione operativa r3 è:

```bash
sudo /tmp/m8
```

Essa esegue in sequenza: verifica/installazione pin -> replay V2 -> recovery
legacy -> reset incompleto -> cutover -> verifica. Non rilanciare una versione
precedente dello shortcut. Successo valido: `LEGACY_RECOVERY_OK` (oppure
`LEGACY_RECOVERY_ALREADY_OK`), quindi `RM-0008 CUTOVER_OK` o
`RM-0008 CUTOVER_ALREADY_OK`, infine `RM-0008 STATUS_OK`.

## Revisione r4 prima del nuovo tentativo (2026-09-06)

Il lancio r3 ha confermato un difetto di framing prima di modificare il journal:
`initial.json` è JSON canonico senza newline finale, mentre la recovery legacy
e il reset precedenti richiedevano il formato con newline. Il reset avrebbe
quindi rifiutato il claim anche dopo una recovery legacy riuscita.

La correzione autentica esplicitamente il framing canonical senza newline e
mantiene invariati SHA-256 e identità del claim. Il wrapper aggiorna reset e
controller già installati soltanto dai digest precedenti attesi, con staging e
pubblicazione atomici. L'aggiornamento ora avviene sotto entrambi i lock RM-0008
e dopo la verifica di assenza di unità concorrenti.

Pin r4:

- reset: `f54052ee40db5e1a809393d249bf6925b2c4f6a8637d63ac17bad4dcd6eeea8b`;
- controller: `51df5e35ea53e42096c6daeb4ac779c81de6a5453d29dfbc33d6171a4bc963e6`;
- legacy recovery: `58b45748fb03dab0c2644c79b5b4bd6df471ec97cb0d76acccdbbb457c337c44`;
- V2 recovery: `2d7667f471f916003b767c56155b86f6e342b907a38b588e330f99f87d3f1375`;
- wrapper: `faea392ba29458e969a4cfcba0cfb2902c4b62022242e664b4a1c5673e7217dc`;
- `/tmp/m8`: `4b11d2f3206475f6fcea37924eea801ee456ccf00710739c1d986f634683aee9`;
- entry point unico `/tmp/FINISCI-RM0008`:
  `bc862cfd9df31e0c5881969ac2dab588944b7bd1d4d03b8cdab9218fb05e637b`.

Gate r4 superati: i tre replay (`reset seams=4`, `V2 seams=3 refusals=7`,
`legacy seams=7 refusals=13`), test framing claim, `py_compile`, `pyflakes`,
`bash -n`, `sh -n` e `FINAL_PIN_CHAIN_OK artifacts=7`. Dopo il rifiuto r3 non
è stata eseguita alcuna nuova operazione privilegiata.

Unica invocazione operativa r4:

```bash
sudo /tmp/FINISCI-RM0008
```

## Revisione r5 dopo il rifiuto r4 (2026-09-06)

Il lancio r4 si è fermato prima di spostare record legacy:

```text
V2_RECOVERY_ALREADY_OK
LEGACY_RECOVERY_REFUSED RuntimeError noncanonical JSON: .../initial.json
```

La correzione del framing era presente nel reset, ma mancava nel prerequisito
`_require_claim()` della recovery legacy. Quest'ultimo ora legge il file tramite
descriptor `O_NOFOLLOW`, autentica proprietario/modo/link/SHA e accetta
esclusivamente i 415 byte JSON canonical senza newline del claim produttivo.
Il replay legacy non sostituisce più `_require_claim`: crea e verifica il claim
produttivo esatto, quindi tutte le sette crash seam attraversano il lettore
reale. Il wrapper ammette l'upgrade atomico soltanto dal digest r4 installato al
nuovo digest r5.

Pin r5:

- recovery legacy: `3c1d09a47dac7a1055af2e4593b0cceb159953175e35539e465f104219021151`;
- wrapper: `48c71c1ea44453f555b851831203dae49d3f7f8f376a9c334cd5c7ae50274897`;
- `/tmp/m8`: `03ff51b6d97e14ff52d29d0f5da3951ff40d34d12eb7b2368be004c981eda2c4`;
- `/tmp/FINISCI-RM0008`:
  `e677e3fbc3525c40e74bab1ee6356ad63a957b814ab63b7dd6ab275b226dd735`.

Gate r5: tre replay verdi, `py_compile`, `pyflakes`, sintassi shell e
`FINAL_PIN_CHAIN_OK artifacts=7 upgrade=legacy-r1-to-r2`. Il rifiuto r4 non ha
creato la directory di recovery legacy e non ha modificato il journal.

## Stato autoritativo finale r6 (2026-09-07)

Questa sezione sostituisce le istruzioni operative r1-r5 precedenti. Non usare
`/tmp/m8`, wrapper o controller di una revisione precedente.

L'analisi completa ha separato due problemi: una regressione del prodotto nella
prima adozione della generazione corrente e una catena operativa che non
modellava esattamente i residui dei tentativi precedenti. La correzione del
prodotto è KISS e circoscritta: adozione iniziale esplicita solo per sequenza 1,
stato staged e assenza di predecessore; autorità semantica materializzata una
volta; timeout del processo padre coerente con il budget del figlio; Python
gestito unico per la convergenza, senza graft di `tomlkit`; framing JSON e float
TOML deterministici. Non è stato introdotto un nuovo framework di recovery.

Prodotto congelato:

- commit: `a2af66a92b7feef0bdf1a1c08667be462e9e5efd`;
- source id: `sha256:356f4dedaf343e18e65c9ad322d17d7f210481ad93ebdf5e7636042e0bce1161`;
- archivio: `/tmp/metnos-rm0008-source-a2af66a9.tar`;
- SHA-256 archivio: `9d5d6bf439f107263f56ab9dc7ea8c57f61fea039130d07aa27359ec3dc9101e`;
- una seconda costruzione indipendente dell'archivio ha lo stesso hash.

Stato live da riprendere: le recovery terminali del vecchio tentativo risultano
durabili, mentre il tentativo `617f059f` ha lasciato una transazione V2 prepared.
La catena r6 autentica prima il vecchio source id, quindi esegue in ordine
V2 recovery, legacy recovery, reset esatto e una sola nuova transition. Ogni
passo è replay-safe e rifiuta topologie o contenuti non riconosciuti.

Entrypoint unico finale:

- file: `/tmp/FINISCI-RM0008`;
- tipo/proprietà: file regolare, `0755`, uid/gid `1000:1000`, un solo hard link;
- SHA-256: `30903f8609bc8b2eb8c6b418b52e615f8cdb705bb18ddb9f0ba3e3516df8d8d1`.

Pin principali della catena:

- controller: `ebc3bc2531c4294675f290ceb0f79c583808061eadb2324835f3660772504944`;
- verifica successo: `5813a85580b7d92614e18cfca7ac8e8944ab11240735b4bbce70e54eada47d2e`;
- verifica ambiente: `970e5f2c7420a383ca513f6f2890870603a5288c88950bb8ba6e685ccab5a8d1`;
- sudoers nuovo: `fe6e77b9f7fc9ec469c5fb2ad52f0a7ad141460678297a0599424dac45045d8a`;
- recovery V2: `864f07af035b6257abd6d8d0ce970d501eca0c239d0d1331ae15663b80654821`;
- recovery legacy: `a516dafcbdf425eeb0fb6211f088e4e197e20b890599f4cbd0e7f38f3503589b`;
- reset: `2cc5eeb10e6ed1528e1cbbce0b6b175781d6f13059e15d4ae83b0727d02cc985`;
- helper reset installato: `5d9a959aee29d6ce8e997e9a2332dabc33540cbd8a91278807b18fc9bb9cbf17`.

Verifiche concluse:

- suite prodotto effettiva: 2049 test PASS (2044 diretti e i 5 test che
  richiedono ownership privilegiata ripetuti nel relativo ambiente simulato);
- gate integrato finale PASS, inclusi recovery V2 -> legacy -> reset, una sola
  transition, persistenza del risultato, secondo activate senza nuovo deploy,
  status e sei failure injection sui seam shell;
- archivio/source id, pin closure, sintassi shell, bytecode Python, pyflakes e
  sudoers PASS;
- invocazione diretta senza root: exit 78 con rifiuto root esplicito, quindi il
  vecchio errore `Permission denied` non è più possibile;
- tre revisioni indipendenti: GO, nessun blocker residuo.

Il gate usa systemd/deploy simulati ai confini; non pretende di essere una
seconda esecuzione produttiva. La transition reale resta coperta dalla suite del
prodotto e sarà eseguita una sola volta sul live.

Unica invocazione autorizzata:

```bash
sudo /tmp/FINISCI-RM0008
```

Non sono richiesti altri script né altri rilanci preventivi. Il comando registra
l'output in `/var/lib/metnos-admin/rm0008-mobile.log`. Può richiedere diversi
minuti perché recovery e transition hanno timeout deliberatamente ampi.

Successo valido: `RM-0008 CUTOVER_OK` oppure `RM-0008 CUTOVER_ALREADY_OK`, poi
`RM-0008 STATUS_OK`, target/readiness attivi e servizi esterni ancora attivi.
Solo dopo il marker di successo il controller tenta, in best-effort, la revoca
esatta dei sudoers residui `617f059f` e `metnos-rm0008-codex`; un contenuto non
riconosciuto viene lasciato intatto e segnalato.

## Correzione operativa dopo la prova live r6 (2026-09-07)

La prova reale r6 ha completato `V2_RECOVERY_OK`, poi si è fermata su
`LEGACY_RECOVERY_REFUSED RuntimeError terminal legacy observation changed`.
Il journal legacy e tutti gli oggetti del reset erano ancora intatti.
Il gate precedente simulava anche il binding dell'osservazione legacy: questo
limite non era esplicitato nella sezione r6 e ha lasciato scoperto il difetto.

L'analisi read-only ha dimostrato un delta esatto e interamente attribuibile al
PREPARED interrotto: 22 ricevute, 44 directory, 22 incrementi di nlink dei
contract root e 22 record audit (9824 byte). Rimuovendo solo questo delta **in
memoria**, l'osservazione corrente ricostruisce esattamente il READY storico.
Il prefisso audit corretto è unico fra 68 possibilità (20717 byte). Nessun
contenuto, permesso, inode o altro metadato storico risulta divergente.

- READY storico: `sha256:53751321ca83e46322825f7236625200d980f73fa3aa77906e077b5692bc2b10`;
- osservazione corrente: `sha256:bf47ab48729c79a813db20af795726a2ff003745e46a4be1b0f001823f485da9`;
- prova riproducibile: `/tmp/metnos-rm0008-test-legacy-observation-356f4ded.py`;
- prova PASS: delta esatto, successo del guard reale e cinque rifiuti su
  osservazione storica, contenuto, permessi, inode e instabilità;
- precondizioni legacy corrette verificate **sul live** con `_phase()`, fase 0;
- precondizioni live reset PASS: claim, release, incoming, coordinator,
  namespaces, quarantine e V2 completion.

La sola correzione semantica è nella recovery amministrativa: conserva READY
come identità storica e autentica separatamente l'osservazione corrente esatta.
Ricevute e audit restano intatti. Il prodotto congelato non è cambiato.
L'autorizzatore aggiorna atomicamente solo i tre file operativi già installati,
accettando esclusivamente i precedenti digest r6, sotto i lock esistenti.
Upgrade e replay hanno superato 19 casi isolati, incluso sudoers invalido.

Nuovi pin che sostituiscono i corrispondenti r6:

- `/tmp/FINISCI-RM0008`: `eb2abce9508d4d1b2abb2a380d4b4b39f1567bf9acac44bb1de94ac4503567ee`;
- recovery legacy: `3d59b561ac869cf111e5021f5bd7877b1bfbb97e82b0beb7d27df060a2a9e4ab`;
- controller: `f3483ac8b443cd1ed50c55e14bee517c6189c8cde0eddf4df01c57d42e98dfab`;
- sudoers: `48d9d3c87c829708dcfe7b58cb6c4f3a9b311744d9829a74dae6368cee2e09bf`.

Il test del delta legacy è ora incluso nel gate finale. Al momento di questa
nota la ripresa produttiva corretta non è ancora stata eseguita: nessuna
dichiarazione di successo finché non esistono i marker e lo stato durabile.

La prima ripresa corretta è stata rifiutata prima dell'upgrade perché le sonde
root dirette avevano importato `config.ensure_dirs()` senza `METNOS_WORKSPACE`.
Il confronto integrale dei 3251 membri dell'archivio ha trovato **zero file
mancanti o divergenti** e solo tre directory aggiunte, tutte vuote: `workspace`,
`workspace/.mnestoma`, `workspace/.scheduler`. Sono state spostate senza
cancellazioni in
`/var/lib/metnos-admin/rm0008-final-617f059f/diagnostic-workspace-20260907`.
Il source id `617f059f…` è stato verificato nuovamente e coincide.
Le future sonde che importano il prodotto devono usare ambiente esplicito e
protezione filesystem read-only; `-I -B` non impedisce gli effetti degli import.
Il controller usa già `METNOS_WORKSPACE` e tutti i percorsi dell'account
espliciti per recovery/reset; la transition monta il nuovo source read-only.

Ripresa successiva, ore 19:43 CEST del 7 settembre:

```text
V2_RECOVERY_ALREADY_OK
LEGACY_RECOVERY_OK
INCOMPLETE_ATTEMPT_RESET
RM-0008 prepared residue is reset; production transition is starting.
```

La unità `metnos-rm0008-transition-356f4ded.service` è in esecuzione; attendere
questa stessa operazione, non rilanciarla. Esito finale ancora da verificare.

## Esito effettivo 356f4ded e causa comune (2026-09-07, sera)

La transition appena descritta è TERMINATA con exit 78:

```text
birth_prepared_set_mismatch
RM-0008 refused: production transition failed; durable state retained for exact resume
```

Non è in esecuzione e RM-0008 non è completato. Non rilanciare lo stesso codice.
Una sonda sotto utente servizio, filesystem read-only e rete privata ha provato:
anchor storico `fac2e295…` valido; `load_sealed_authorities_v1()` rifiuta alla
riga 258 di `executor_birth_prepared_root.py`, perché ricostruisce il materiale
storico con il runtime nuovo. Le chiamate errate non sono una sola:
convergenza dei contratti, costruzione anticipata del runtime installer,
verifica iniziale con ricevute differite ed enumeratore nel ramo storico.

Una seconda sonda senza pubblicazioni ha autenticato e confrontato tutti i
contratti reali: `examined=122`, `unchanged=122`, `requires_publication=[]`.
La correzione in preparazione separa il materiale pubblico di verifica storica
dall'autorità eseguibile e costruisce il runtime installer solo se serve una
pubblicazione. Il lettore runtime rigoroso deve continuare a rifiutare il V1
incompatibile. Test integrati richiesti: predecessore realmente creato con
codice A, successore con codice B, tutti i consumatori iniziali reali, senza
sostituire i lettori di autorità con simulazioni.

Stato residuo censito read-only:

- claim `sha256:4fe468ea0f8a6d7e3eeb5ae338f44d6736dd9d19a1146c561816423aeb038921`;
- build `sha256:ffab3006cd89b4cc65a434b5342d2eabe06281d3cec64abb0f142686c8de0737`;
- Birth V2 `.birth-provisioning-v2.txn.f7a4de9e4449e408247678a61d650855`;
- checkpoint `verified`, target `c25f9ee4d1ef53a69907564648451a9301abf3ea41a167c5012273365503d585`
  ancora contenuto nella transazione, NON pubblicato in `authority-sets`;
- nessun record coordinator V2 e nessun head nuovo;
- journal legacy con tre record, ultimo `AUTHORING_ADOPTED`, osservazione
  `sha256:bf47ab48729c79a813db20af795726a2ff003745e46a4be1b0f001823f485da9`;
- vecchie recovery 617f e relativo reset completati e conservati;
- script del censimento: `/tmp/metnos-rm0008-census-failed-356f4ded.py`.

I nuovi residui non sono ancora stati rimossi o spostati. Le chiavi e i 122
contratti correnti restano intatti; per il prossimo sorgente serve recupero
esatto dei soli oggetti incompleti censiti, dopo i test della correzione.

## Correzione storica congelata — 7 settembre, ore 20:20 CEST

Il difetto `birth_prepared_set_mismatch` è corretto sul candidato, non ancora
distribuito. Commit `5d876b7b702f0eb382eb5f50192c14a8907eae8a`; worktree candidato
pulito. Quattro file di prodotto distinguono verifica storica pubblica e
runtime eseguibile; i controlli rigorosi del contesto non sono stati indeboliti.

Prove concluse:

- 1418 test core portable PASS, 27 skipped secondo piattaforma;
- regressione con V1 creato sotto codice A e codice B realmente differente:
  lettori storici, convergenza invariata, verifica iniziale, preparazione e
  pubblicazione del nuovo insieme e ricevuta V2 reale PASS; insieme storico
  e binding alterati restano rifiutati;
- test mirati dei consumer e controllo antimutazione PASS;
- censimenti sorgente privato/pubblico, policy, documentazione bilingue e
  due costruzioni byte-identiche dell'archivio PASS;
- nuovo reset ristretto: 16 gruppi di test sintetici PASS, incluse interruzioni
  dei sette spostamenti e del marker, collisioni, lock e ripetizione con
  successore già presente senza modificarlo;
- preflight live strettamente read-only: sette oggetti esatti in fase zero;
- lettore corretto sul live, account servizio e filesystem read-only:
  `LIVE_HISTORICAL_READER_OK authenticated_contracts=122`.

L'intera directory `tests/portable` include inoltre test POSIX multi-UID che
richiedono un ambiente privilegiato: una prova globale si è arrestata per il
`sudo` non disponibile nel sandbox, non per una regressione del prodotto. Non
contare quella prova come PASS; la suite core sopra è stata eseguita per intero.

Pacchetto:

- archivio `/tmp/metnos-rm0008-source-historical-v1.tar`;
- SHA256 `9d005f9c18b54b193ff8b93732d002a0c1b8d936c147b4e32959734f1104cfbb`;
- source ID `sha256:fd36339e87abb4ec6b014c39b79fbba9537d74f6a5add42999c9292e027efe1e`;
- nuovo bundle previsto `/var/lib/metnos-admin/rm0008-final-historical-v1`;
- entrypoint in verifica `/tmp/metnos-rm0008-authorize-historical-v1.sh`;
- reset `/tmp/metnos-rm0008-reset-failed-356f4ded.py`, SHA256
  `8f733841c6b83ee94218fb17c296464bf7100ba2a34bacd9aee20f879e50243b`.

Non eseguire i vecchi `/tmp/m8` o `/tmp/FINISCI-RM0008`. Il nuovo entrypoint
non è stato ancora lanciato: mancano il gate della catena shell e la verifica
incrociata finale. Il reset conserva in quarantena soltanto V2 staged, tre
record legacy, release, incoming e claim del tentativo 356f; non cancella
chiavi, ricevute o contratti. I servizi esterni sono attivi; target Metnos e
readiness sono inattivi. Nessun operatore RM-0008 è in esecuzione.

## Avvio effettivo historical-v1 — 7 settembre 2026, ore 20:31 CEST

Il gate della catena operativa è passato: installazione, replay, continuità
dei lock, una sola esecuzione del deploy, recupero del risultato temporaneo,
rifiuti preventivi e status. Impronte finali verificate, authorizer eseguibile
0755; nessuna ulteriore modifica al prodotto congelato.

Pin operativi finali:

- authorizer: `96b064c98cc2be01070f99ab30c514445d59d70e78eda1a5794e25dca3314cb9`;
- controller: `2b399eb1ca942aef560d256f703ac2be29d2393de2597f099dc9ca762201a770`;
- verificatore successo: `69ec1dd1f4cf9d53d4d77620ff49fa140c67a057d51acb35afe99a21edeb9edf`;
- verificatore ambiente: `f7e00bb8f1b382ac6576dc48fb5cd960b549286f724ba44994c852e77b130492`;
- sudoers: `7ca7838d95106899c2ba2d3fd469e2a72784eefcbd57342716df94d53c1f9be5`.

Il nuovo authorizer è stato lanciato dall'agente con l'autorizzazione già
fornita dall'operatore. Bundle e sudoers installati. Recupero reale riuscito:

```text
UNCOMMITTED_ATTEMPT_QUARANTINED source=356f4ded objects=7
RM-0008 prepared residue is reset; production transition is starting.
```

I sette oggetti sono conservati in
`/var/lib/metnos-admin/rm0008-final-356f4ded/quarantine-uncommitted-356f4ded`.
La transizione è ora in corso: non avviare un secondo processo. Il successo
non è ancora dichiarato; servono uscita zero, `CUTOVER_OK` e verifica finale
`STATUS_OK` del nuovo controller.

## Esito historical-v1 e diagnosi confermata — 7 settembre 2026

**Questo aggiornamento sostituisce lo stato «in corso» precedente. RM-0008
non è completato. Non rilanciare il controller attuale.**

L'esecuzione è terminata con exit 78 dopo avere raggiunto
`RECEIPTS_COMPLETE`, con errore `birth_transition_predecessor_invalid`.
Il recupero dei sette oggetti 356f è riuscito; il nuovo rifiuto è successivo.

Causa deterministica verificata eseguendo il solo censimento del prodotto
installato come root, con filesystem strettamente read-only e senza import
dell'applicazione: in `install/birth_authority_provisioner.py:4612` fallisce
`required.issubset(discovered)`. Il catalogo nuovo include il binding
`legacy-install-contract-convergence` verso
`install/executor_birth_contract_convergence.py`, assente in `/opt/metnos`:
quel modulo è stato introdotto soltanto nel candidato. Tutti gli altri
locatori repository richiesti sono presenti. Non è un problema di chiavi,
permessi o corruzione dei contratti.

Il controllo preventivo precedente era incompleto: la regressione storica
A→B copriva i consumer e le ricevute, mentre il test del predecessore
sostituiva il censimento reale con un mock. Il gate shell simulava il deploy:
non era una prova dell'intero dominio fino al retirement. Non ripresentare
questi gate come prova end-to-end completa del cutover.

Correzione di prodotto individuata ma **non applicata**: rimuovere soltanto
il falso binding storico dal catalogo. Il modulo candidato resta nella
release firmata, nella classificazione preflight e nei registri boundary,
e viene avviato esplicitamente dalla nuova release. Aggiornare il test che
confonde entrypoint candidati e percorsi legacy, aggiungendo una prova reale
di census e piano di retirement con il nuovo modulo assente dal predecessore.
Ignorare l'assenza nel solo census non basta: la revoca successiva fallirebbe
sullo stesso path. Non creare un file fittizio nella vecchia installazione.

Stato persistente attuale, da conservare integralmente:

- source `sha256:fd36339e87abb4ec6b014c39b79fbba9537d74f6a5add42999c9292e027efe1e`;
- build `sha256:9decf2704095c9a0e06fe2b59e07f9bcd3e2d4ed0f545d200e79eead64ac0bf6`;
- request `sha256:790ca4c1d2a50c8515bd9fc13937f134d1ff9ec8d0b5f6b86f25181d7c8dd2e5`;
- coordinator: soli `record-000-v2.json` PREPARED e `record-001-v2.json`
  RECEIPTS_COMPLETE, nella directory della request sopra;
- context edge `sha256:c6c1cbe7d1af39033c5f734807451835086993c5b72b24135009fd2337979b63`;
- transazione privata `.birth-provisioning-v2.txn.e3dd2af8dc525f1298a2a61de201c3f1`;
- target `47bdab2c9b936a886b6e1e28c5cbf3977a2fd3dd1fddb40b202dae3f23bd1fdd`
  **già pubblicato**, non soltanto staged; ricevute nuove già registrate;
- il marker continua a selezionare
  `fac2e29592f936024a39cdfc1a2834e6ac6a3d48784511bc9834b51dd8e7fe8a`;
- nessun certificato, head o attestazione preflight pubblicati;
- `metnos.target` e `metnos-stack-ready.service` inattivi; llama-server,
  searxng e photon attivi; nessuna operazione RM-0008 ancora in esecuzione.

La release firmata e il candidato congelato non sono stati alterati dopo
il rifiuto. La modifica del catalogo cambia source/build/coverage: non è
lecito applicarla alla release firmata o riscrivere claim e record esistenti.
Il prodotto attuale non offre un annullamento V2 e rifiuta una nuova release
sopra questa transazione incompleta. Proseguire richiede una recovery
esplicita, separata dal precedente reset a sette oggetti: deve considerare
anche l'autorità già pubblicata e le ricevute emesse, con conservazione
integrale e prova di non avere oltrepassato il cutover. Non usare il reset
precedente per questo stato avanzato; non introdurre bypass o reset dinamici.

## Rianalisi locale dopo il rifiuto — 7 settembre 2026, ore 21 CEST

Nessun nuovo intervento in produzione in questa prosecuzione. La recovery dello
stato `RECEIPTS_COMPLETE` con autorita' gia' pubblicata richiede l'approvazione
esplicita chiesta all'operatore nel messaggio precedente; al momento non e'
arrivata. Non interpretare una continuazione automatica del goal come assenso.

Il candidato congelato `5d876b7b` e il bundle historical-v1 restano invariati.
Le correzioni sono separate nel worktree
`/tmp/metnos-rm0008-legacy-catalog-fix`, branch
`codex/rm0008-legacy-catalog-fix`, derivato da quel commit:

- rimosso il solo falso binding `legacy-install-contract-convergence` (4 righe);
- test di copertura distinguono componenti storici, entrypoint del contract
  store e modulo di convergenza del candidato, mantenendo la chiusura esatta;
- aggiunta fixture con 16 percorsi storici fissati indipendentemente dal
  catalogo candidato e prova del vero census, cattura e retirement su filesystem
  in user namespace root: nessun mock di metadata, census o neutralizzazione;
- prova negativa: l'assenza di un vero file storico resta un rifiuto; reinserire
  il falso binding riproduce il guasto osservato in produzione.

Prima della correzione del replay sono passati 79 test di catalogo/retirement/
composizione e 290 ulteriori test di neutralizzazione, systemd e coordinator,
con 2 skip nella seconda suite. I tre nuovi casi filesystem sono stati eseguiti,
non saltati. Questi sono test di componenti e filesystem, NON una certificazione
end-to-end della macchina fino a `PREFLIGHT_VERIFIED`.

La rianalisi ha inoltre trovato che `_publish_initial_predecessor_v2` ricensiva
i percorsi originali ad ogni replay: dopo una prima revoca tali percorsi sono
gia' rinominati. Correzione locale minima in verifica: quando l'anchor esiste,
leggerlo con il lettore root-owned canonico esistente, riusare soltanto i suoi
file storici e ricostruire tutti i binding della transizione dagli input fidati.
Il no-replace esistente confronta l'intero documento e rifiuta ogni mismatch.
La sola presenza del file non basta, e non si introduce una scansione dinamica
degli oggetti ritirati. La prova positiva e i casi avversariali del replay sono
ancora in lavorazione al momento di questo aggiornamento: nessun GO produttivo.

Documentazione IT/EN e note installer aggiornate nel candidato locale; non
ancora pubblicate. Pin privato ricalcolato su 754 sorgenti:
`sha256:81760071903c46efcd2c2009c2c95e4e47d9fbd3971d636d8f7db1c1773789f6`;
pin pubblico su 742 sorgenti:
`sha256:7fbaf847c514fce2eee2126ff43d30b985258a632f08c15d7e440eaef1652de3`.
Gate boundary chiuso, verifica del pin privato e proiezione policy sono passati.
Questi pin non autorizzano modifiche alla release live ne' una nuova esecuzione.

## Candidato locale verificato e salvato — 7 settembre 2026, ore 21:18 CEST

Le due regressioni di prodotto individuate sono corrette nel commit
`e0a0df0db095de896e6da97d1de9d2f7269eb52b`, branch
`codex/rm0008-legacy-catalog-fix`, worktree
`/tmp/metnos-rm0008-legacy-catalog-fix` pulito. NON e' la release in produzione.

La suite estesa ha rilevato anche il necessario allineamento del fingerprint
autonomo del catalogo nel preflight: `_EXPECTED_SERVICE_SOURCE_IDENTITY_V1`
passa da `26eaba361992d88295babf0265c0f48469d245415442275ab5ee7a0e6ef9de8e`
a `4ab5911ac9851cfe3b77c991152846e64a86ae87574f688f14cf8f1c1550faa2`
(entrambi con prefisso `sha256:`). La proiezione differenziale ricostruisce
esattamente il vecchio digest aggiungendo la sola falsa voce: le entry di
servizio restano identiche, i binding passano da 40 a 39. Nessun confronto
rigoroso o fallback e' stato allargato. Il nuovo test fully-rebound rigetta
il catalogo che reintroduce quella falsa voce.

Prove finali sul candidato:

- tutti i 62 moduli `tests/portable/test_executor_birth_*.py`, come utente
  normale con ambiente e XDG interamente temporanei: **1393 passati, 42 skip**,
  46 secondi;
- i **15 nuovi casi filesystem** richiedono root e sono compresi nei 42 skip
  sopra: eseguiti separatamente sotto `unshare --user --map-root-user`, senza
  fakeroot, sono **15 passati, 23 deselezionati**, 0,66 secondi;
- l'intero modulo di composizione ha anche **38 passati** nel medesimo dominio
  user namespace con metadata effettivi. Questi conteggi si sovrappongono:
  non sommarli come fossero suite disgiunte;
- publisher precedente estratto via AST dal commit **5d876b7b** ed eseguito
  soltanto in memoria sul test nuovo, sempre in user namespace: fallisce alla
  seconda pubblicazione in `_predecessor_file_locators_v2:4612`, dopo il vero
  ritiro di `install/bootstrap.sh`. La versione corretta passa senza cambiare
  byte o inode dell'anchor;
- replay negativo: binding admin/transazione alterato, mode, symlink,
  hardlink, JSON non canonico, oltre ai sei campi storici controllati
  separatamente (transaction, root, admin, catalog, coverage, commands): tutti
  rifiutati conservando byte e inode;
- gate boundary chiuso, proiezione policy, pin privato, pin e rigenerazione
  boundary dell'export pubblico, `git diff --check`: tutti passati.

La prima esecuzione dell'intera suite dentro user namespace produceva anche
13 errori dovuti a test che richiedono UID di servizio non zero o demozione a
UID non mappati. Sono stati rieseguiti nel loro dominio normale, non corretti
indebolendo test o prodotto. I 12 errori restanti erano il fingerprint speculare
del catalogo e sono scomparsi dopo la correzione puntuale descritta sopra.

Pin finali, che sostituiscono quelli intermedi del paragrafo precedente:

- privato, 754 sorgenti:
  `sha256:a3911cd48bcec97fc808bc7dd4a7f3544f0d2fc8bdbd3098a44d5c65c9f6a216`;
- pubblico, 742 sorgenti:
  `sha256:13ff5edeb0760efb7e9f2f59095133ee18e8f722eba88f91fc35e54e57d14eb0`.

Le modifiche funzionali nette sono **+10 righe** fra catalogo e publisher,
oltre alla sostituzione del fingerprint; la crescita restante e' costituita
da test, documentazione e pin generati. Nessun framework di recovery aggiunto.

Limite delle prove: non e' ancora una prova completa su clone con systemd e
identita' multiple fino a `PREFLIGHT_VERIFIED`. Restano 27 skip della suite non
coperti dalla prova filesystem. Non dichiarare un GO produttivo da questi soli
risultati. Documentazione pubblica aggiornata nell'export, non pubblicata sul sito.
Non sono stati creati nuovi authorizer, modificati sudoers o avviati tentativi.

Ultima ricontrollata read-only della macchina: log invariato dal 20:37:20 CEST,
ultimo errore ancora `birth_transition_predecessor_invalid`; `metnos.target` e
`metnos-stack-ready.service` inactive/dead; nessuna unita' `metnos-rm0008-*`
attiva. Il worktree congelato precedente e' ancora pulito.
La recovery della transazione avanzata resta in attesa della conferma esplicita
richiesta all'operatore; il messaggio di frustrazione non e' un'autorizzazione.

## Audit del blocco dopo la terza prosecuzione senza assenso

Il turno precedente ha prodotto progressi reali: commit `e0a0df0d`, regressioni
corrette e suite verificate. La prosecuzione automatica successiva non contiene
l'approvazione umana della recovery richiesta. Il blocco resta lo stesso.

Controllo conclusivo read-only: nessun runner full-clone gia' pronto trovato in
`/opt/metnos/internal` o fra i file RM-0008/G6/C3 al primo livello di `/tmp`.
In `/tmp/metnos-rm0008-test-historical-chain.py` il sistema servizi e' simulato
e l'esito `PREFLIGHT_VERIFIED` e' emesso direttamente dalla fixture: non usarlo
come prova di transizione reale. Le celle G6-C sono prove ridotte e opt-in da VM
usa-e-getta, con percorsi fissi che sulla macchina attuale sono produttivi:
NON abilitarle qui. Nessuno fra systemd-nspawn, qemu-system-x86_64, virsh, podman
e docker risulta disponibile sul PATH di questa sessione.

Le correzioni locali sono salvate; ulteriore progresso operativo richiede
l'assenso alla recovery conservativa e la disponibilita' di un ambiente
isolato completo per la prova richiesta, non un altro test shell simulato.
Goal da porre blocked secondo l'audit di tre turni, non complete. Nessuna nuova
modifica o prova mutante e' stata applicata alla produzione in questo audit.

## Recupero conservativo autorizzato e concluso — 7 settembre 2026, 21:45 CEST

Roberto ha successivamente autorizzato esplicitamente: «continua. autorizzo il
recupero conservativo». Questo assenso supera il blocco di autorizzazione
riportato sopra. Il recupero e' stato eseguito, NON soltanto preparato.

Il censimento root in sola lettura ha confermato lo stato esatto fd363:
coordinator `PREPARED` + `RECEIPTS_COMPLETE`, target 47bd pubblicato ma marker
ancora fac2, nessun predecessor, certificate, head, build o preflight; journal
legacy con **quattro** record 000–003, ultimo `LEGACY_STATE_READY`.

Procedura installata root-owned 0400:
`/var/lib/metnos-admin/rm0008-final-historical-v1/recovery-fd363.py`.
Copia leggibile: `/tmp/metnos-rm0008-recover-fd363.py`.
SHA-256:
`26ade76e1c58b1ef58764d1f4a1162c54eef0e24289f6d4b49b49a7f49c0814c`.
Non modifica codice di prodotto ne' contenuti firmati.

Undici oggetti conservati mediante rename no-replace, nell'ordine:
coordinator request790ca, context edge c6c1, privata e3dd, target authority47bd,
journal003→000, release1, incomingfd363, initialclaim ultimo.
Quarantena integrale e recuperabile, root-only 0700:
`/var/lib/metnos-admin/rm0008-final-historical-v1/quarantine-receipts-complete-fd363`.
Contiene anche `complete.json` canonico con identita' attese; nessun oggetto e'
stato cancellato e nessuna ricevuta e' stata inventata o riscritta.

Sei alberi preservati in posizione e confrontati prima/dopo: precedente
authorityfac2, author-root, operator-input, ownership authorities, state/birth
(incluso Producer SQLite) e l'intero contract-publications (incluse ricevute).
Controllati anche owner, mode, link e assenza di ACL; marker preparato invariato.
I materiali target47bd restano integralmente nella quarantena, incluse chiavi.

Le quattro vecchie autorizzazioni sudoers RM-0008 (356f4ded, 617f059f, codex,
historical-v1), censite e verificate per digest/owner/mode, sono conservate in
`/var/lib/metnos-admin/rm0008-final-historical-v1/retired-authorizations-fd363`.
Revoca prima di rilasciare i lock, nessuna nuova autorizzazione persistente.
La successiva lettura di `sudo -l` conferma l'assenza dei quattro permessi.

Gate precedenti all'esecuzione:

- revisione indipendente di ordine, lock e conservazione;
- 15 test sintetici real-filesystem in root user namespace, 3,67 secondi:
  22 seam sugli 11 rename, prima/dopo i quattro rename sudoers, rifiuti di
  mismatch/duplicati/nonmonotonia/namespace avanzato, preservazione byte-mode-
  inode, ripresa del marker e barriera fcntl; nessuna fixture tratta da segreti
  di produzione e nessun sistema servizi simulato presentato come E2E;
- harness `/tmp/metnos-rm0008-test-recover-fd363.py`, SHA-256
  `da583aa5320e5ec668edb58a61a4bcbd9f28a34831bbf1bb2b123def8e82d413`;
- preflight effettivo `--audit` della copia root-only, in sandbox read-only:
  `CONSERVATIVE_RECOVERY_PREFLIGHT_OK moved=0 preserved_trees=6`;
- mount host unico ext4 per tutti i rename; niente bind RW separati che
  causerebbero EXDEV. Interprete di servizio esatto gia' attestato, ambiente
  HOME/XDG/METNOS completo prima degli import applicativi.

Esecuzione 21:44:56–21:45:07 CEST, exit 0; log distinto, senza sovrascrivere
il precedente fallimento: `/var/lib/metnos-admin/rm0008-recovery-fd363.log`.
Esito: `CONSERVATIVE_RECOVERY_OK source=fd363 objects=11
receipts_and_keys=preserved marker=unchanged`.

Controllo indipendente successivo, nuovo processo con filesystem read-only:
`INDEPENDENT_POSTCHECK_OK archived=11 preserved_trees=6 obsolete_grants=4
marker=unchanged pending=0 cutover=not_started`.

**Stato attuale:** niente V2/coordinator/claim pendente live, journal vuoto
salvo lock, release1 e incomingfd363 archiviati, marker storico fac2 invariato.
`metnos.target` e `metnos-stack-ready.service` restano inactive/dead.
Il recupero e' concluso; **RM-0008 NON e' ancora attivato/completato**.
Non rilanciare `/tmp/m8`, `/tmp/FINISCI-RM0008` o vecchi authorizer/controller:
potrebbero reinstallare il candidato abbandonato usando un sudo interattivo.
La ripresa della recovery gia' conclusa verifica soltanto l'archivio e completa
l'eventuale revoca: non ricontrolla o modifica lo stato di un nuovo candidato.

Preparato anche l'archivio pulito del commit corretto e0a0df0d, senza installarlo:
`/tmp/metnos-rm0008-source-e0a0df0d.tar`, SHA-256
`b1c5a8278da50b625d4285c018351a0dbae6d2525933915f9f642b9077df2c63`.
Estrazione in directory temporanea sintetica e checker originale root-userns:
source ID `sha256:49d9287bbcfba62b11b0ac61b0e3a1f81c5425af30b9c59c6c126145a0acba76`.
Il worktree corretto resta pulito; non esistono ancora nuovo bundle/controller
live per questa identita'. Resta necessaria la prova integrata del candidato e
la sua attivazione verificata; non confondere il gate recovery con quella prova.

## Prova isolata e verifiche preventive — sera 7 settembre

Autorizzazione corrente: Roberto richiede completamento autonomo nel perimetro
RM-0008, inclusi recupero conservativo e prove isolate, senza ulteriori lanci
manuali. Il recupero delle 21:45 sopra descritto resta concluso e intatto.

Laboratorio reale avviato, separato dai dati produttivi:
`rm0008-test-e0a0df0d`, root in
`/var/lib/machines/rm0008-test-e0a0df0d`, servizio host transitorio
`rm0008-lab-e0a0df0d.service` (massimo sei ore). Installati sull'host soltanto
systemd-container 255.4-1ubuntu8.17 e debootstrap, senza aggiornare altri
pacchetti o riavviare servizi Metnos. Guest Ubuntu 24.04, systemd identico
all'host, user namespace non identico, rete privata con solo loopback,
nessun bind di dati/credenziali/bus produttivi. E' un contenitore con kernel
condiviso, non una macchina virtuale.

Controllo di isolamento completato: tutti i namespace separati, cgroup
delegato confinato e prove effettive root/nobody con filesystem read-only,
rete separata e NoNewPrivileges. Checker locale
`/tmp/metnos-rm0008-guest-isolation-check.py`, copia guest
`/opt/rm0008-lab/isolation-check-v2.py`, SHA-256
`04f90cc6a5f391c8c536f8a383edddad2bda648be5025aefbfb7a78510d245bd`.
Nel guest presenti sorgente e0a0df0d, 71 wheel core verificate e ambiente di
test con pytest 9.1.1. Nessun dato o materiale segreto produttivo copiato.

Prima esecuzione G6-C reale: fallita in 46,76 secondi **prima** del passaggio
firmato, con `service Python executable binding`. La fixture usava Python
di sistema anche per il servizio, mentre le release ora richiedono quello
gestito. Log conservato nel guest:
`/opt/rm0008-lab/g6c-result/pytest.log`; nessun risultato positivo da dichiarare.
La correzione in preparazione allinea la fixture al vero ambiente gestito e
la ricetta isolata allo stesso controllo di binding gia' usato dal prodotto.
Non rilanciare il programma completo sulla fixture residua senza prima
conservarla e verificarne la quiescenza.

Ulteriori difetti produttivi rilevati PRIMA di un nuovo cutover:

- il catalogo G7 avvia sempre Playwright, ma il lock core non lo include;
- il sealing dell'ambiente toglie il bit eseguibile al driver Node del wheel;
- la disponibilita' automatica del sidecar e di altri componenti osserva
  ancora lo scope user, non quello system del catalogo firmato G7.

Correzioni circoscritte in corso nel worktree separato
`/tmp/metnos-rm0008-legacy-catalog-fix`. Il commit e0a0df0d e il suo archivio
restano una base storica: **non sono piu' il candidato finale da installare**.
Occorre completare i test, ricalcolare le impronte del nuovo candidato e
provarlo nel laboratorio prima dell'attivazione. Nessun nuovo bundle live
installato e nessun nuovo cutover produttivo avviato in questa fase.

### Aggiornamento 7 settembre, 23:05 CEST: seconda prova isolata

Commit intermedio `45481bacc944a08bb2de5ad159207f653baa96f9`, archivio
`/tmp/metnos-rm0008-source-45481bac.tar`, SHA-256
`8313e8a22071fa1909b98fdf135d030737443233e33149a2706bc2cce36ee1a8`.
Source ID con permessi canonici 0755/0644:
`sha256:7d08ade2dc86466246ce8843542778c0fa951f2643062dfe44fe1230e217ec58`.
Pin privato 754 sorgenti `sha256:1c07efd4016dbac02ee2c56760269a633a5ca99e4ec2a3a7f86107e6b4818466`;
pubblico 742 sorgenti `sha256:d01b195f9c3e902ea949314fa6c618d0293c0e68e339e82ccd0421e4eaac19fb`.
Entrambi i gate di inventario passati. Suite dei 63 moduli portable Birth:
1405 PASS, 42 SKIP espliciti (prove root/piattaforma/live non simulate).
Cluster readiness 110 PASS. Managed environment costruito ex novo con tutte
le 74 dipendenze, verifica driver Playwright start/stop e riuso idempotente PASS.

Seconda G6-C reale nel guest, log
`/opt/rm0008-lab/g6c-result-r2/pytest.log`: fallita prima della firma effettiva
con `systemd origin fragment path` su `-.mount`. Il root mount nspawn e'
creato dal manager senza frammento; sull'host la stessa unita' deriva invece
da `/run/systemd/generator/-.mount` e `/etc/fstab`. Non indebolire la policy
del prodotto per accettare il laboratorio. Prova e residui sono conservati.

La revisione ulteriore ha trovato che il nuovo lettore readiness apriva
indirettamente `.required-head-v1.lock`, root 0600, da un servizio non-root.
Correzione minima in corso: usare il cold reader pubblico autenticato della
catena richiesta, non cambiare permessi del lock o identita' del servizio.
Quindi **45481bac non e' ancora un candidato qualificato per il live**.

La sonda live readonly `/var/lib/metnos-admin/rm0008-final-historical-v1/preconditions-r2.py`
ha riconfermato l'archivio del recupero e i namespace vuoti; si e' poi fermata
sulla directory `/home/roberto/.config/systemd/user`, owner 1000:1000,
mode 0775 senza ACL. E' un requisito reale del prodotto: `_transition_roots_v2`
richiede assenza dei bit 0022. Nessun chmod e nessun cutover eseguiti da questa
sonda. Le eventuali rettifiche devono essere circoscritte e documentate.

### Aggiornamento 7 settembre, 23:38 CEST: candidato e verifiche reali

Il candidato corrente e' `b5d57b0fcd4868d54104e6bd4b6db6175302db71`, nel
worktree `/tmp/metnos-rm0008-legacy-catalog-fix`. Archivio
`/tmp/metnos-rm0008-source-b5d57b0f.tar`, SHA-256
`654561da56c589b07d5d3fa1e4c0a99b951889c2e767f0ac256f1d27417cadb1`;
source ID canonico
`sha256:2dffe49901663e181c78ef3c7be29dd0c9e315f3979d6f993de7c03d7c13ff05`.
Pin privato 754 sorgenti
`sha256:c2c0cd9692fbe04af8a8feaf0c41efe613ca817602a3a78f85aef811d31850a8`;
pubblico 742 sorgenti
`sha256:104b3dd48eebfce1387276d308fbe08b424152cc9f5a36fbd4afb3fd8903fc41`.
Non confondere il digest dei byte del lock,
`7bef3b77d4655ba6fcbfbf8b8a0345dd1452b2ef51613fbae7f6bc9f5a6c4e43`,
con la sua identita' canonica/dirname ambiente,
`bb5cf3991b4cc15b3ea3f5ece123dc3d725f77545b5f7db67ba709ba6197e8e9`.

Correzione readonly completata con il cold reader pubblico autenticato;
nessun allargamento dei permessi del lock. Suite 1405 PASS/42 SKIP;
cluster readonly e catena 173 PASS/1 SKIP. La prova DAC saltata localmente
e' stata eseguita come root nel guest, con figlio uid/gid 65534:
**1 PASS senza skip**, verificando il rifiuto del vecchio accesso al lock
e l'instradamento readonly corretto. Non equivale da sola a prova di
attivazione completa o di tutte le verifiche crittografiche.

Unica rettifica produttiva ulteriore: directory
`/home/roberto/.config/systemd/user`, 0775 -> 0755, proprietari e inode
invariati, ACL assenti; directory antenate e file non modificati.
Ripetuta la sonda `preconditions-r2.py`: **preconditions_verified**,
11 oggetti archiviati/6 alberi preservati, nessun claim/V2/release1 live,
manager user raggiungibile e tre servizi esterni attivi. La sonda non
autorizza il cutover e non attesta i contenuti dei 16 file legacy.
Browser Chromium e headless shell gia' presenti: accesso reale uid995/gid985
e dipendenze native senza librerie mancanti, nessun browser avviato.

Nel solo guest installata origine statica veritiera di `-.mount`, senza
start/stop/remount; mountinfo e proprieta' effettive invariati. Terza G6-C,
source-r3, fallita in 48,11 secondi ancora prima della firma per origine
mancante di `tmp.mount`; log `/opt/rm0008-lab/g6c-result-r3/pytest.log`.
I quattro alberi residui di questa prova sono ancora live NEL GUEST e
devono essere conservati prima di una nuova prova; i residui precedenti
sono gia' archiviati in percorsi `g6c-failed-r2` e `g6c-failed-e0a0df0d`.

Censimento globale guest readonly completato: 169 unita' caricate,
186 origini, 2480 relazioni, 34 mount, due fotografie identiche e zero
errori di query. Diciannove origini senza frammento e due transient;
nessuna relazione diretta da unita' Metnos perche' la G6-C ha gia' tolto
i propri frammenti nel finally. Non dedurre che tutti questi oggetti
siano blocchi di Metnos: va censito il catalogo esatto installato ma
non avviato prima di scegliere normalizzazione minima guest o VM.
Sonda locale `/tmp/metnos-rm0008-guest-systemd-origin-census.py`, SHA-256
`62985c2f772d7a0bbb131965df6f870568693afda77368aef8e5745fc9a51901`.

Launcher live in revisione `/tmp/metnos-rm0008-final-b5d57b0f.py`:
**non installato e non eseguito**. Il controllo host ha trovato
`authorization.lock` root 0644, `activation.lock` root 0600,
directory operator root 0700: adeguare l'aspettativa esatta, non fare chmod.
Nessun nuovo cutover produttivo tentato. I vecchi shortcut restano vietati.

## Aggiornamento 8 settembre, 00:14 CEST — regressione timer isolata

**b5d57b0f NON qualificato per il live.** Analisi del vero percorso G7:
`_install_bound_topology_v2` firma a timer fermi; l'attivazione successiva
fa comparire `TriggeredBy` e cambia l'hash controllato prima dell'avvio.
La readiness richiede il timer i18n attivo e ricattura l'intero catalogo.
La precedente G6 preattivava il timer prima della firma e non copriva il difetto.

Correzione locale minima nel worktree candidato: sottrarre dal solo residuo
`TriggeredBy` il timer interno esattamente legato tramite `timer_target` e
`Timer.Unit`; niente archi sintetici, `Triggers` e archi esterni invariati.
G6 modificata per firmare inattivo e confrontare il medesimo hash dopo l'avvio,
con prerequisite immutato e denial `ConflictedBy` esterno preservato.
Review indipendente runtime GO; nuovi test portabili ancora in completamento.
Pin privato 754 `sha256:c1a6666f44a1166f02fa6801fc3d52c4dcecc2b23b77f01225ecb68d9b84630a`;
pubblico 742 `sha256:91349fba433eb31482773d8e048afd69e09cf771bbe20ee27fedef6956b430b2`.
Gate boundary e proiezione policy PASS; export locale r5 1732 file, zero PII.

Il census inerte del catalogo G6 esatto ha confermato che solo `tmp.mount`
blocca le origini. Helper guest SHA `3a3c246a9cae270ef23d6ca55d18db0ad2bf20dc4031f967ff8a39012a4e4a45`
eseguito: il frammento root 0444 e' stato installato, ma il controllo dopo
daemon-reload ha rifiutato perche' `SourcePath` resta `/proc/self/mountinfo`.
NON dichiarare questo helper PASS; non rilanciarlo (O_EXCL). Montaggio non
avviato, fermato o rimontato. Restano i quattro residui G6-r3 da conservare.

Fixture integrazione r4 pronta, SHA `8108b92ac89c918225f4d7e08b9ed3f32f485d6b87e0cf39026ca7705c945bcf`:
corretti workspace isolato, chiusura manager utente metnos dopo il seed e
audit cold-chain crittografico. Non eseguita; aggiornare SOURCE_B al prossimo
candidato congelato. In esercizio il manager utente metnos e' gia' chiuso:
linger, bus e systemd sotto /run/user/995 assenti; entrambe le unita' inactive.
Launcher b5: 15 test isolati PASS; verifica finale resa realmente readonly.
Launcher e probe funzionale NON installati/eseguiti, pin da riallineare al
prossimo candidato. Il prodotto reale resta nello stato recuperato descritto sopra.

### Candidato congelato 9fd17899 — 00:19 CEST

Commit `9fd17899a20c11a8ae469f45872b9792a5e7960c`, archivio
`/tmp/metnos-rm0008-source-9fd17899.tar`, SHA-256
`ef3483ad6dc9e931eefea2ded7281061b667701b08bacb15fb58b3d638161f2c`;
source ID canonico `sha256:669325a55732b15064bf761519297348b24e068a8ff2372403cf352c4ac05d41`.
Tutti i 63 moduli Birth portabili: **1414 passati, 42 skip, zero errori,
46,50 secondi**. Nove nuovi casi compresi nel totale provano identita' completa
inattivo/attivo/inattivo e rifiuto delle dipendenze non dichiarate.
Il codice produttivo cambia di 20 righe nette, non l'architettura; restante
delta costituito da test, documentazione IT/EN e pin. Hook commit ordinari passati.
Export definitivo locale `/tmp/metnos-rm0008-public-r6`: 1732 file, zero PII,
pin pubblico e gate boundary/policy verificati. Nessuna pubblicazione esterna.

Nuovi harness locali (non ancora eseguiti): stage-r4 SHA
`f71696adda952898903f63d32cd64eeda5a846c374c78f8e29ecca500bdca8f4`,
G6-r4 SHA `5e3480ef0cb17a291eb57f7380152f2102698b9dbf39137adfdc01b610f9fc70`,
integrazione-r4 SHA `d4179e7f72ead4db9499900c560ef6adc9a07e9ef936fb673ac3c992e65602ff`.
Tutti puntano al futuro guest `source-r4`; stage conserva i quattro residui r3
tramite rename no-replace. Il fix guest `SourcePath=` e' supportato dalla
semantica systemd v255 per rendere nativa l'origine del frammento; ancora in
preparazione, nessun nuovo test G6 avviato e nessun cutover live tentato.

### Esiti successivi del laboratorio — 8 settembre, dopo 00:19 CEST

Il fix guest `tmp-sourcepath-r2.py` e' stato eseguito con successo:
SHA `e4f15bd3c06e76afc3c4e84c4c1d0c6f3325ff27574a1d63e4f3c83b7269d842`.
Solo `SourcePath=` nel frammento nativo del mount; vecchio inode conservato,
mountinfo completo invariato (SHA `8319aae9dd63d3ca323b844d318ff16e86f6ced2e8d30809c7286856fc5fac65`),
nessun mount/remount/start/stop. Il censimento inerte del catalogo G6 esatto
ora passa su tutte le dieci origini e sulla cattura completa delle dipendenze.
Stage-r4 completato: source-r4 e' il candidato 9fd17899; i quattro residui
r3 sono conservati nei percorsi `g6c-failed-r3`, senza cancellazioni.

G6-r4 e' stata eseguita e **NON passa**: 49,24 secondi, exit pytest 1.
Log guest `/opt/rm0008-lab/g6c-result-r4/pytest.log`; rifiuto prima
dell'attestazione: `attest|legacy state journal missing`. La fixture
costruisce record V2 sintetici ma omette il journal legacy obbligatorio,
inserendo un digest nullo. Il lettore richiede quattro record terminali
concatenati e il loro digest esatto. Correzione in corso SOLO sul test,
nessun allentamento del prodotto. La verifica dell'adozione reale resta
separata e obbligatoria tramite l'integrazione dell'installer effettivo.
Al momento di questo aggiornamento i quattro residui r4 sono ancora nel
guest ai percorsi originali: conservarli prima di un'altra prova.

La regressione timer e' stata anche riprodotta indipendentemente caricando
in memoria la sola vecchia funzione dal commit b5d57b0f: il nuovo test
fallisce sull'uguaglianza fra cattura inattiva e attiva, come previsto.
Il candidato corretto supera quel caso. Nessun cutover produttivo dopo
il recupero conservativo; nessun lancio da chiedere all'operatore.

### Candidato 5df72c1d e primo seed integrato — 8 settembre, 00:45 CEST

Commit `5df72c1dc4ea7d29e6ca5d9a2d603f6992f689ea`: soltanto due file di test
(journal G6 e tre prove portable di autenticazione/binding). Il codice
produttivo, i pin privato/pubblico e il lock dipendenze restano identici a
9fd17899. Suite indipendente completa: **1417 PASS, 42 SKIP, 45,20 secondi**.
Archivio `/tmp/metnos-rm0008-source-5df72c1d.tar`, SHA-256
`c2e7328dd7bbd6e72a1f82f4cf3ed452cddc3b0d1077d300fb667bd10138e5cd`;
source ID `sha256:efbfcba65e288caa43c8aba41f1da4c1d959263aa417c4be20e18bcc8a43fb67`.
Guest `/opt/rm0008-lab/source-r5` staged e verificato: confronto byte-per-byte
con source-r4 prova che cambiano solo i due test. Stage-r5 SHA
`01c284da2b38cd7e707eef74ae81db38b86d517d346707c6af0f4c246eab6748`.
Export pubblico locale r7: 1733 file, zero PII, pin/gate verificati; non pubblicato.

I quattro residui G6-r4 sono ora conservati nei percorsi `g6c-failed-r4`:
`preserve-r4.py` SHA `192ce033e43d8f2b5ce4e1a91a054afa9c0edbd069f0358119ffb3dd387e9ba4`
eseguito PASS. Integrazione reale r4 avviata e fermata al seed, PRIMA della
migrazione: `BirthBootstrapError birth_ownership_recovery_required: productive store`.
Checkpoint guest `/opt/rm0008-lab/transition-result-v1`: 00, 01, 02,
03-seed-legacy.stdout vuoto, 03-seed-legacy.stderr e 99-failure.json.
Il seed ha creato chiavi e prepared-v1, ma manca lo scaffold root-owned
`authorities-v1`/`chain-v1` richiesto dal bootstrap corrente anche in modalita'
storica. Nessuna pubblicazione nel guest: state contiene solo admission lock
e log; ownership root contiene solo preflight-attestations e legacy journal.
Non rilanciare `all`, che richiede stato fresco. In preparazione continuazione
esplicita della sola fixture: API prodotto per autorita'/chain iniziale,
poi stessa fase installer e transizione candidata source-r5. Conservare i log.

Nuovo controllo readonly in esercizio PASS: 11 oggetti archiviati, 6 alberi
preservati, zero claim/V2 pendenti, marker precedente invariato, tre servizi
esterni attivi. Launcher/sonda 5df72c1d preparati e testati (15 PASS),
NON installati ne' eseguiti. Nessun nuovo tentativo produttivo.

### Continuazione del seed r5 — 8 settembre, circa 01:10 CEST

`/tmp/metnos-rm0008-guest-continue-r5.py`, SHA
`804df748fe33f5329853b9f6bb3439fb41bbe1d5e7eea35124dae3cf973aa29d`,
installato guest 0444 ed eseguito. Le API prodotto hanno inizializzato
autorita' root e chain: checkpoint `01-scaffold.json` conferma Birth invariato.
La fase storica si e' poi fermata con exit 78 per Permission denied creando
`historical-source/runtime/builtin_executor_contracts/.admin.birth-control-*`.
Esiti conservati in `/opt/rm0008-lab/transition-result-r5-v1`; NON rilanciare
lo stesso helper fresh-only. La copia storica era erroneamente tutta root-owned;
si stanno verificando i due subtree di authoring (`executors` e
`runtime/builtin_executor_contracts`) e l'esatto punto di interruzione prima
della continuazione. Il candidato `source-r5` resta immutabile e non modificato.
Account servizio nel guest: UID/GID 991:991, diverso dall'host 995:985.

Sonda readonly Telegram in esercizio eseguita con isolamento e identita'
995:985: `chat_record_valid=true`, `desired_state=running`,
`token_available=true`, nessun errore. Nessuna credenziale stampata o modificata.
File `/tmp/metnos-rm0008-telegram-readonly-5df72c1d.py`, SHA
`ce600a560c2b2d8c5ee74eaf1edd9e4fdc5e3129727e6e9c2fa8376cbd6342c7`.
La prova integrata e il passaggio in esercizio restano incompleti.

Nel guest il censimento readonly successivo conferma che lo stop r5 avviene
in `recover_authoring`, prima della prima pubblicazione: entrambi i DB Birth
sono file vuoti, shadow contiene solo il lock di ammissione da un byte,
nessun control/journal authoring nei due subtree. Nessun prefisso pubblicato
da eliminare. La recovery attraversa anche i retired per il solo lock:
la copia storica completa dei due subtree deve quindi appartenere al servizio.

Input browser laboratorio installati e verificati (circa 01:14 CEST):
17 pacchetti Ubuntu ufficiali, nessun upgrade/rimozione, dpkg audit vuoto;
tre alberi browser copiati senza profili o credenziali, byte verificati.
Chromium reale versione `149.0.7827.55` avviato come 991:991 e chiuso, PASS.
Helper guest `/opt/rm0008-lab/browser-r5.py` 0400, SHA
`80905171cb0255931b92e93b3876e9e0eb7588d824b1ee136160315a677465e1`.
Archivio browser `/tmp/metnos-rm0008-browser-runtime.hNmd3R7e/browser-runtime-1228.tar`,
SHA `33b7241a93ddfd2c6e9e2deb5441b832d9b929a7deb513743f3e4203d1a96a34`;
pacchetti `/tmp/metnos-browser-debs.zLUJFg/browser-dependencies.tar.gz`,
SHA `c0df69cded548bb01e71a637625e1492a51f98d0948844181402a388dd1a5ea5`.
Cache guest canonica sotto service-data, owner 991:991. Host non modificato.

Preparazione readiness guest r6 completata: helper
`/opt/rm0008-lab/readiness-inputs-r6.py` 0444, SHA
`a2025c8892e4c832ba1e0b698de501484477a1f36f7f922c7769ee1a0a48f331`.
La sola fixture SearXNG ora risponde 200 al controllo HTTP esatto, 404 altrove;
originale sleep conservato NOREPLACE nel checkpoint `readiness-inputs-r6`.
Intenzione sintetica Telegram `stopped` registrata nel guest (non sull'host).
Llama/Photon rimangono processi simulati: non dichiarare provato un turno LLM
dal laboratorio. La prova funzionale reale resta obbligatoria in esercizio.
La precedente preparazione r5 era stata rifiutata senza modifiche per un
controllo erroneo sugli antenati della home: home/.local/.local/state sono
legittimamente root 0755; solo la directory finale metnos e' servizio 0700.

Continuazione r6, SHA `c59072460c89fd3c0d63e5f3bd6ed63801163ba49275ab9aa2cd914252fb71e9`,
installata guest `continue-r6.py` 0444: confronto readonly dei due subtree
storici con source-r5 PASS. Poi rifiuto **prima del chown** nello snapshot
dell'intera copia storica: `workspace/.scheduler` e `workspace/.mnestoma`
sono directory root 0700 create dal bootstrap, non 0755. Nessun altro percorso
con mode divergente; nessuna correzione di proprieta' ancora applicata.
Checkpoint `transition-result-r6-v1`: solo 00-resume e 99-failure
(`RuntimeError: authoring mode changed`). Il seguito deve conservare questi
esiti e ammettere soltanto le due directory private nello snapshot della
copia storica, non normalizzare i loro permessi. Candidato produttivo invariato.

### Esito r7 e revisione del laboratorio — 8 settembre

La continuazione r7 e' TERMINATA, exit 78; nessuna integrazione resta in corso.
Helper `/tmp/metnos-rm0008-guest-continue-r7.py`, SHA
`1394b49e8dfb83cf36df0b2e9e02fb4083338714cc90830fd96239aeec46c81b`.
Checkpoint guest `/opt/rm0008-lab/transition-result-r7-v1`:
`01-authoring-owner.json` conferma 559 membri dei due subtree storici portati
all'owner sintetico; poi `BirthBootstrapError: property_runner_unavailable`.
Lettura root indipendente: registro sandbox sintetico `state=unavailable`,
`reason=bwrap_absent`, `/usr/bin/bwrap` assente. Nessuna migrazione raggiunta.
Non rilanciare r7: lo stato della fixture e' avanzato e va ricensito.

Il laboratorio finora tenta una nuova pubblicazione storica sintetica di tutti
i contratti, non una copia dello stato recuperato in esercizio. Prima di
un altro tentativo verificare anche delegazione cgroup, interprete visibile
nella sandbox e protocollo di esecuzione delle implementazioni builtin:
installare soltanto bwrap non prova che quel seed sia valido. In valutazione
una copia locale isolata del baseline reale, senza allentare il prodotto.
Ancora nessun nuovo passaggio produttivo; candidato 5df72c1d invariato.

### Baseline reale acquisito e G6-r6 fallito — 8 settembre, circa 01:58 CEST

Il seed sintetico non riproduceva l'adozione dello stato attuale: oltre a
bwrap assente, la nuova pubblicazione richiede protocollo delle proprieta',
import e delegazione cgroup che quella fixture non forniva. Non indebolire
il prodotto per farla passare. Lo scaffold sintetico (21 oggetti) e' conservato
nel guest in `/var/lib/metnos/integration-seed-r7-preserved`, senza cancellazioni.

Copia locale protetta dello stato reale recuperato COMPLETATA, con verifica
byte/metadati prima e dopo e precondizioni produttive invariate. Input root-only:
`/var/lib/metnos-admin/rm0008-clone-input-5df72c1d`, copiato nel guest isolato in
`/opt/rm0008-lab/baseline-input-r1`. Comprende 23965 membri, 338095696 byte,
nessun xattr; contiene le chiavi di firma necessarie, ma non credenziali utente,
Telegram, posta o LLM. Non pubblicare e non riportare alcun artefatto dal guest
in esercizio. Tar SHA `e238c8a1af199f63c6b0912ffc113424b4c7a75d413b4eb79e4a017cda3576a9`;
manifest SHA `24e59b55a3f9b92b9b433aad822a3057d17ff8e8c1b47fdf01a86880e248d6e3`.
Helper capture SHA `8b9d7b365e0982e1cb5cddc571d9f0567a0eb2d376b695c5242dcdaad8e31a4d`.
Import non ancora eseguito: richiede prima prova G6 riuscita e replica nel guest
delle identita' numeriche metnos 995:985 e roberto 1000:1000.

G6-r6 TERMINATO con pytest exit 1 in 151 secondi. Risultati guest:
`/opt/rm0008-lab/g6c-result-r6/{result.json,pytest.log,pytest.xml}`.
Helper SHA `575f08aee51bf9dec8624cbdfce01960280bef763cb6f94cbfabb14ac7360f6f`.
La verifica amministrativa iniziale passa, ma dopo l'attivazione del timer
`_cross_preflight_boundary_locked_v2` fallisce al confronto delle impronte
systemd: observed `14a3d53b29f639d681a92c157d04362461bbfc7eeaa9dc5596ba74a70d4e1b49`,
signed `b3df85a2da0386e0bcf0bb0425c144db2c0ce6340969cd94d23089f2ad31b92a`.
Le due riletture sono stabili e la versione del gestore coincide; serve il
confronto dei singoli campi prima/dopo. Nessun nuovo fix di prodotto applicato.
Controllo readonly successivo: nessuna unita' G6 residua attiva/caricata;
`/usr/libexec/metnos` assente; quattro directory della fixture ancora presenti.
Non rilanciare r6 fresh-only. Archiviare senza sostituire prima della prova
diagnostica osservativa. Il laboratorio resta attivo, con scadenza 04:13 CEST.

### Correzione misurata e candidato r8 — 8 settembre, circa 02:24 CEST

G6-r7 diagnostico e' terminato con fallimento riproducibile: dopo l'attivazione
del timer cambiano esattamente il collegamento inverso implicito `After`
del servizio verso il proprio timer firmato (sia nella proiezione sia negli
archi aggiunti dal gestore) e `WatchdogSec` non configurato, da `infinity`
a `0`. Le due riletture successive coincidono; nessun'altra differenza stabile.
Prove private guest: `/opt/rm0008-lab/g6c-result-r7/`, compreso
`systemd-delta.jsonl`. Fixture r6 e r7 conservate con rename NOREPLACE e
rapporti `/opt/rm0008-lab/preserved-r6.json` e `preserved-r7.json`.

Correzione minima congelata nel commit
`a90c3f079a3ce5cc61e19fa68ad9b37390327008`, worktree
`/tmp/metnos-rm0008-legacy-catalog-fix`. Si normalizzano soltanto gli inversi
del timer dichiarato e il watchdog disabilitato non configurato. Gli `After`
espliciti, i timer estranei e i watchdog configurati restano verificati.
Revisione indipendente GO; suite Birth portable completa: 1422 PASS,
42 SKIP, zero failure/error in 45.637 secondi. Prova reale ancora obbligatoria.

Archivio `/tmp/metnos-rm0008-source-a90c3f07.tar`, SHA
`d90aa27c5521358f3be2f2b34bfcc84a18cd772e55f46a6c951a0ced312a0eda`;
source-id canonico verificato anche nel guest:
`sha256:1f928022d1d26a76fbf5de218281c3d03e4f0484f68eb21788c4ddeb4cd657cc`.
Stage guest `/opt/rm0008-lab/source-r8` completato. Helper stage-r8 SHA
`d021cac143b9e6bd9defff271630ad68ba5aea537bfc764fdaaad5ca29393c3d`.
Pin privato: 754 file, `70e7fa0bf680360bdbffe614faab563bdce94beaa725bb7785c40dac11737ce5`.
Pin pubblico: 742 file, `de9e2ef1adad9e93c80edefaa5235b5817263d1631787967abe18f51c7dcc221`.
Export pubblico locale `/tmp/metnos-rm0008-public-r8`: 1733 file, zero PII;
non ancora pubblicato. Documentazione pubblica bilingue aggiornata nel commit.

Bubblewrap ufficiale `0.9.0-1ubuntu0.1` installato SOLO nel guest offline:
eseguibile SHA `52231e1caf55bcbc667b269f49c63599a6f7db4767ae6a039580d0ff853db712`,
dpkg audit pulito; report `/opt/rm0008-lab/bwrap-result-r1/result.json`.
G6-r8 senza plugin diagnostico avviato nell'unita' guest
`rm0008-g6c-r8.service`; attendere questa esecuzione, non rilanciare.
Helper `/opt/rm0008-lab/g6c-r8.py` SHA
`b3b1e9a08b7fe140de36a035d145db0338d4aa20c9289162436247b53b3ddbe8`.
Output atteso `/opt/rm0008-lab/g6c-result-r8/`.

Import baseline reale NON ancora eseguito. Importatore preparato
`/tmp/metnos-rm0008-guest-import-baseline-r1.py` SHA
`19c66a69180c66d509ccdcddb033815432f9867e6202913f3710889aec07f540`
e' ancora legato intenzionalmente al PASS r6 (fallito): dopo PASS reale r8
aggiornare il solo riferimento alla prova, ripinnare e verificare. Prima del
deploy sul clone chiudere l'eventuale linger sintetico metnos; NON avviare
user@995. Preparare invece directory e manager utente di roberto1000.
Nessun nuovo cutover produttivo e nessun launcher produttivo a90 installato.

G6-r8 concluso **PASS**: 1 test in 397.25 secondi, exit 0, nessun errore,
fallimento o skip, nessuna unita' residua. Verificati indipendentemente in
lettura root sia result.json/JUnit sia l'esito della sessione originaria.
Il test copre attivazione reale, permanenza del prerequisito firmato e rifiuto
di una dipendenza conflittuale estranea. Fixture positiva conservata in
`/var/lib/metnos/g6c-completed-r8` e `/run/rm0008-g6c-completed-r8`.

Importatore aggiornato e revisionato: SHA
`a04b76c9dc815d3b79393c4a83b92cec5bea7bebe1e72530ec3b1b2772e47ca2`,
installato guest `/opt/rm0008-lab/import-baseline-r1.py` 0444.
Richiede ora il PASS r8 e conserva nel checkpoint il solo marker sintetico
linger/metnos osservato (file root 0644 vuoto, no xattrs), prima di cambiare
NSS. Nessuna cancellazione. La sua SOURCE_ID efbfc resta quella del capture
immutabile, non del candidato a90. Import avviato come
`rm0008-import-baseline-r1.service`; controllare l'esito prima di proseguire.

La revisione del clone richiede anche una copia supplementare dei soli binding
systemd utente legacy realmente coinvolti: una directory roberto vuota non
proverebbe la retirement reale. Audit in corso; non copiare unit estranee o
credenziali e non avviare il manager1000 prima di controllarne l'autostart.

File finali a90 preparati SOLO localmente, non installati/eseguiti:
`/tmp/metnos-rm0008-final-a90c3f07.py`, SHA
`968a1917e0bf84e5c9139aaf90f0a090b7d50fba06351cbeec942f7b19ad69c5`;
verificatore `/tmp/metnos-rm0008-verify-success-a90c3f07.py`, SHA
`733fa846f32255f4a78f9881e56146a24b098cebbd1ace345616ce216a5da502`;
prova funzionale `/tmp/metnos-rm0008-functional-probe-a90c3f07.py`, SHA
`f2791f1af9f55ac6cd3008a0def4365d31df47894cbf4ad9b2b0ba5f11d2ccab`.

### Clone reale importato; ostacoli preventivi confermati — 8 settembre, 02:50 CEST

Import baseline r1 concluso con exit 0, stato `baseline_imported` e tutte le
sette fasi verificate in `/opt/rm0008-lab/baseline-import-r1/result.json`.
Identita' guest ora metnos 995:985 e roberto 1000:1000. Linger sintetico,
home sintetica e journal sintetico sono conservati, non cancellati.
Nessuna migrazione di prodotto avviata. `user@995` resta chiuso.

Preparazione delle dipendenze r8 conclusa con exit 0, stato `prepared`:
17 pacchetti nativi verificati, browser ripristinati con owner 995:985,
Searx isolato verificato. Report `/opt/rm0008-lab/clone-readiness-r8/result.json`;
helper SHA `1c553378e2a05f7f3382afe3b0a467a0e347db25fa515febf6ae2d0a10950ffa`.
Non equivale alla prova funzionale completa: LLM e Photon restano sostituti
isolati; manager roberto1000 non ancora avviato; browser995 non ancora provato.

Due ostacoli certi individuati PRIMA di lanciare la migrazione:

- `predecessor-v1.json` e' ASSENTE sull'host. Il capture iniziale conteneva
  soltanto i 16 file legacy obbligatori, mentre la prima transizione censisce
  tutti i sei alberi deploy/executors/install/runtime/scripts/tutor. Serve
  completarli fedelmente nel clone, non creare directory vuote sostitutive.
- La retirement nativa conserva il solo frammento HTTP: i cinque drop-in
  attivi resterebbero applicati e il preflight firmato li rifiuta. Conservare
  l'intera directory (sei file, incluso un backup inattivo) e' necessario,
  ma la sola quarantena cambierebbe tre comportamenti: engine v3 verso metis,
  account mail predefinito e opt-in Telos notturno. Normativa corrente:
  CLAUDE.mutabile prescrive v3. Non modificare queste impostazioni in silenzio,
  non allentare il divieto di drop-in e non inserire dati personali nel codice
  pubblico. Analisi della destinazione delle impostazioni ancora aperta.

Copia supplementare delle sole unita' coinvolte acquisita dall'host in sola
lettura, senza modificare servizi: `/tmp/metnos-rm0008-unit-supplement-r8`
root0700, `units.tar` e `manifest.json` root0600. 40 oggetti, 31 file;
nessun riferimento a credenziali, EnvironmentFile, alias o collegamento di
attivazione nello scope verificato. Tar SHA
`f7fb1ec37222fd87ea0a22381d02bae8b539544d411250fcbcbe1a5a7936fc2d`;
manifest SHA `d33a4d16c0d51d2f8e03743ee83a655929c27310686e9a2c882f2cea43993f13`.
Capture helper root0400 `/var/lib/metnos-admin/rm0008-capture-unit-supplement-r8.py`,
SHA `1efac0f94f5c9bd11accef7dea92bfb85477e6820efe2a7819f4e65a710f5cac`.
Import di questo supplemento non ancora eseguito. I file finali a90 NON sono
pronti per l'esercizio finche' questi ostacoli non sono risolti e provati.

### Candidato congelato e clone completo — 8 settembre, 03:35 CEST

Il supplemento unita' r8 e l'import completo dei sei alberi del predecessore
sono conclusi PASS nel clone. Quest'ultimo contiene 1407 file, 18.840.488 byte
e un solo collegamento interno verificato; nessun predecessore eseguito.
`/opt/rm0008-lab/predecessor-import-r8/result.json` riporta
`authority_sha256_before == authority_sha256_after ==
61a3487dce64778aac840def6f6c98da7f8f37479caae3369258fa9ea2f42bb8`.
Il predecessore parziale precedente e' conservato nel checkpoint.

Nuovo commit candidato `6983b250833f87c9af78ec03f62f9c171755ffd5`, nel
worktree `/tmp/metnos-rm0008-legacy-catalog-fix`; l'unico untracked residuo
`--help/` e' preesistente e NON e' incluso nel commit. Le correzioni minime
ripristinano engine v3, conservano le due preferenze HTTP nel runtime.toml
privato esistente e consentono al sandbox SMTP solo le credenziali del conto
selezionato quando il manifest firmato ammette mail:send. Nessun executor
o suo manifest modificato. Documentazione IT/EN, note installazione e ADR0224
aggiornati nel commit; pubblicazione e Tutor ancora da completare.

Prove concluse: 1740 test portabili PASS, 45 SKIP, zero fallimenti
(`/tmp/metnos-rm0008-portable-settings-r8-final.xml`); 116 test runtime
mirati PASS; 31 test documentali PASS. L'impronta della ricetta e' ora
`sha256:3aac6d3965c3967a84cea6471b9f7a0dfa0942747fde678e4248d284c6ff4386`;
verifica indipendente conferma che l'unica differenza canonica e' metis->v3,
e che una ricetta metis interamente ricalcolata continua a essere rifiutata.

Archivio definitivo `/tmp/metnos-rm0008-source-6983b250.closed.tar`, SHA
`37b670f5bae71079303a70897747603c9730766b46543b8cffabe420397ef008`.
Guest `/opt/rm0008-lab/source-r9` staged PASS con controllo indipendente:
2996 file, source ID
`sha256:a9c1ee9993d65c0e99e30acfc6d23716849b2116fc3aa079a6c84111eb78b857`.
Usare l'archivio `.closed.tar` (git tar.umask=0022), non quello iniziale.
Il vecchio calcolatore che importa il prodotto crea workspace nella copia
locale: NON usarlo per verificare un albero congelato. Il checker puro
`source-id-r5.py` installato nel clone non ha questo effetto collaterale.

Runtime.toml host acquisito in sola lettura e copiato nel clone:
originale SHA `f0dcce120884b522a7f50820ce02d9b235a87372093b1b03d5a0801c29f74a9c`,
1061 byte, owner995:985 mode0664; input privati root0600 sotto0700.
Il primo helper di conservazione impostazioni (SHA3833a947...) ha rifiutato
PRIMA di qualsiasi modifica: confrontava il Python guest con il digest host.
Diagnostica sola lettura conferma tutti gli altri controlli iniziali positivi
e checkpoint/config/archivio ancora assenti. Entrambi i Python guest (sistema
e managed74) hanno SHA
`359ca926ba4596147d7d558c1e0cbf6a5784f5475d7e8bf0a55d9d7c4fa7106c`;
non usare il digest host1643dac nel clone. Correzione locale dell'aiutante
in preparazione; non equivale ancora a conservazione impostazioni PASS.

Non e' ancora stato avviato il deploy CLI completo sul clone. LLM e Photon
sono sostituti di processo: sufficienti solo per le rispettive verifiche di
stato, NON per dichiarare inferenza reale. La prova finale del turno resta
obbligatoria. Servizi produttivi Metnos verificati fermi alle03:25, nessun
nuovo cutover produttivo. Il clone ha scadenza04:13:45 CEST: osservare lo
stesso processo, non ricrearlo automaticamente in caso di timeout.

Conservazione impostazioni HTTP nel clone ora conclusa PASS (exit0, 480ms).
Helper aggiornato SHA
`ceb6633c71017840b68e65b1e31c9a8c5c66060ea02d7565202ccb12b2108d9c`,
vecchio3833 conservato sia localmente sia nel clone come
`preserve-http-settings-r8.before-guest-python.py`. Report
`/opt/rm0008-lab/http-settings-preserved-r8/result.json`: tutte quattro fasi
positive, originale1061 byte conservato, nuova configurazione1132 byte SHA
`a081ab00e38a094016f450a57c9bf835a00989587f52771346a501b6ef12fe4b`;
directory dei sei drop-in archiviata in
`/etc/systemd/system/metnos-http.service.d.pre-rm0008-v1`.
Daemon-reload completato, DropInPaths vuoto, NeedDaemonReload=no,
nessun servizio o manager avviato. Produzione invariata.

### Pacchetto corretto dopo la prima prova completa — 8 settembre, 04:02 CEST

Browser reale Playwright/Chromium verificato nel clone con UID995/GID985:
`/opt/rm0008-lab/browser-probe-r8/result.json`, status passed. Nessuna
navigazione esterna; non equivale ancora a un turno HTTP verificato.

La prima CLI completa r9 e' terminata alle03:47 con exit78 e
`birth_ownership_deployment_invalid`. Prove conservate sotto
`/opt/rm0008-lab/deploy-real-r9`: stdout vuoto, stderr35 byte,
exit-code.txt `78`, nessun result.json. Nessun claim/transazione pubblicato;
source a9 ricevuta e staging release a9 vuoto, entrambi conservati.

Causa confermata da codice e archivio: `git archive` esclude `internal/`,
ma l'assembly richiede `internal/reports/rm0007-m4-boundary-inventory.json`.
Il controllo fallisce PRIMA della lettura degli eseguibili. Xvfb e' presente
nel clone, root0755,2060768 byte; non e' la causa di questo tentativo.

Correzione SOLO di confezionamento, commit6983b250 invariato:
`/tmp/metnos-rm0008-package-r10.py` genera
`/tmp/metnos-rm0008-source-6983b250-r10.tar`, SHA
`dcd98d42907d3dfded8eb8fbb1d2e87ee0ee616acab311c6e04cd9c0eed66b97`.
Tutti3254 membri dell'archivio base invariati per contenuto e metadati;
aggiunti soltanto inventario tracked dal commit e due directory parent.
2997 file, source ID
`sha256:9aa7903159f3ad72693b07167fb262d714f1c9a721e9f91e0417261310b1445f`.
Identita' confermata indipendentemente dal codec vero del prodotto e dal
checker puro nel clone. Tutti6 file obbligatori presenti; censimento chiuso
978b2f e policy verificati indipendentemente. Stage guest source-r10 PASS.

Il census root conserva inoltre sei sorgenti storiche oltre ad a9; NON
assumere namespace incoming con un solo elemento. La nuova prova deve
verificare i sette nomi osservati, il solo vecchio staging vuoto e le prove
di fallimento r9, senza cancellare o spostare niente. Produzione invariata;
nessun successo RM-0008 dichiarato e nessun nuovo passaggio produttivo.

CLI completa r10 avviata alle04:02:54 CEST, stesso clone:
host `rm0008-clone-full-r10.service`, MainPID1444487; guest
`rm0008-guest-deploy-r10.service`, MainPID7902. Helper guest
`/opt/rm0008-lab/deploy-real-r10.py` root0444 SHA
`e15501df4110144c19aaaf4cf82d83e5130e80136de779fa34cb46611cfb8352`;
verifier root0444 SHA
`291203ccf2ec57a3b18807286a2e4ace1f5cb54bc3cc33548d0d1352f16cad79`.
I sette nomi incoming sono verificati esattamente; tutte le directory
conservate. Prima osservazione: release0001 pubblicata, vecchio staging
vuoto ancora presente, nessun claim/transazione, logstdout/stderr vuoti,
processi ancora attivi. Non interpretare questi dati intermedi come esito.

Probe funzionale root0444 installato ma NON ancora eseguito:
`/opt/rm0008-lab/functional-probe-r10.py`, SHA
`a56646de4b5c55beb00a31e0a2b479709ec6c5c267362a741c0c50bccc93b11a`.
Una sola POST autenticata `che ore sono?`, stesso HTTP, controllo get_now,
TurnLog persistente e salute prima/dopo; non dichiara inferenza LLM reale.

### Esito r10 e checkpoint preciso — 8 settembre, 04:12 CEST

R10 e' TERMINATO alle04:07:46 con exit78 dopo circa292 secondi:
stderr34 byte `birth_ownership_recovery_required`, stdout vuoto, nessun
result.json. Probe HTTP NON eseguito. Nessun rilancio. Il pacchetto ha
superato l'assembly; non confondere questo guasto con l'omissione r9.

Stato root verificato nel clone: release0001 presente; claim initial.json;
coordinator transactions-v2 vuoto. La transazione BIRTH separata e'
`.birth-provisioning-v2.txn.805c160bb24bb860ad2bef8521e5fdc9`, con
checkpoint000 created e001 verified, authority-set/set.json complete,
material-plan-v2.json74728 byte. Previous set fac2e295... invariato;
closed build `sha256:6dfc6d070f66d64b1e4f3c170a8da515f87948ea5be0dc1460b4d626afaf6433`,
request `sha256:64cc5d2fa545b076c7b031254e9a44286bd52fdfb9076454cf540b3b180cf4df`.

Journal legacy: record000 PLANNED,001 INVENTORIED,002 AUTHORING_ADOPTED,
003 LEGACY_STATE_READY. Request legacy
`sha256:33a33eb1618536fd9280aacd48e78943e0969b2462ebaab51f876e43d77c48c9`.
Chain contiene solo quattro directory vuote; preflight attestations vuoto.
Questo restringe il guasto alla parte successiva alla preparazione materiale
e all'adozione legacy, prima di append_prepared_transition. Diagnosi ancora
aperta: non attribuire una causa a un'ipotesi sul proprietario dei lettori.
Nessuna cancellazione, cambio pin o recovery applicata a questo residuo.

Alle04:14:42 CEST il clone e il wrapper r10 risultano entrambi TERMINALI
(MainPID0, inactive/dead); nessun processo orfano nostro con CPU>50%.
Il fallimento della CLI era gia' avvenuto alle04:07:46, quindi NON e'
causato dalla scadenza successiva del laboratorio. Nessuna riaccensione
o ricreazione automatica eseguita. Prima di usare di nuovo l'immagine
serve rivalidare isolamento e identita' del nuovo processo; i vecchi report
legati alle namespace non attestano automaticamente un nuovo avvio.

### Diagnosi successiva, senza nuova installazione — 8 settembre

L'immagine persistita e' presente. Rilettura root dei metadati: ultimo record
di adozione alle04:07:44.720150734, stderr alle04:07:46.638327246;
coordinator-v1 contiene soltanto successor-claims-v1/initial.json415 byte e
transactions-v2 vuoto. Il journal di adozione legacy e il vecchio journal
V1 del coordinator sono distinti: l'ipotesi di una disposition mancante era
una confusione fra i due e' stata ritirata; NON richiede una patch.

Nuova evidenza root: la directory privata legacy
`/var/lib/metnos-service/.config/metnos/keys` NON ESISTE nel clone.
Il freeze usa l'anello autore Birth gia' autenticato per enumerare i current,
ma `_verify_store_only_catalog_locked` rilegge `sign.list_trusted_publics`,
che consulta proprio quella directory e, se assente, restituisce una lista
vuota. Il controllo seguente emette `trusted_keys_missing`, poi nascosto
dal codice CLI generico. Collegamento statico deterministico; in corso una
misura discriminante del punto esatto e test di regressione con chiavi legacy
assenti. Nessun ripristino della vecchia cartella chiavi previsto.

Prima diagnosi effimera r10 terminata PRIMA del payload: nspawn rifiuta
l'id-mapping dell'overlay, `ID mapped mounts are apparently not available`.
Unita' `rm0008-diagnostic-r10.service` terminale exit1/MainPID0, limiti
verificati5min/2GiB/CPU200%/512 task. Nessun import prodotto, nessuna modifica
persistente del clone. Due script e log conservati root700 in
`/tmp/metnos-rm0008-diagnostic-r10.AmbqWF7E`. Non confondere questo limite
del contenimento diagnostico con il fallimento della CLI r10.

### Causa misurata e copia conservativa — 8 settembre, circa 05:00 CEST

La sonda RO ha completato l'accesso reale ai dati della release r10. Report
root700 in `/tmp/metnos-rm0008-diagnostic-r10.uHrNWTQC`; unita'
`rm0008-diagnostic-r10ro-data.service` TERMINALE exit78/MainPID0.
L'esito negativo e' atteso dalla sonda: historical view valida, due chiavi
autore e una admission; 122 current autenticati, impronta inventario
`sha256:f7453bb10a5ff0d71ab35efa06256d3428a28058439ead43f42fee08728723eb`.
Il controllo successivo fallisce realmente in
`contract_cutover_guard.py:291`, codice `trusted_keys_missing`.
Entrambi i confronti metadati (import config e letture catalogo) hanno zero
variazioni. Non e' piu' soltanto un collegamento statico ipotizzato.

Entry finale `/tmp/metnos-rm0008-diagnostic-entry-r10ro.py` SHA
`70d48b81a9edc83fb0321c82f370c92ad944ccf5f267305440e0083b2632dfc0`;
probe r10b SHA `0a7e08624509791ebce7444f01483bfe4fee8dc0c8c5095e47494d39096d4327`.
Isolamento: ext4 originale RO, solo due bind codice RO e una COPIA temporanea
del catalog lock RW, rete privata, sette namespace SELF diverse dall'host,
map0->289538048/65536, cap0xca, durata5min/memoria2GiB/CPU200%/512 task.
L'introspezione delle namespace PID1 era rifiutata da proc/ptrace e non e'
necessaria per provare l'isolamento del payload; non affermare che PID1 fosse
fuori userNS. Due soli mount API duplicati osservati e accettati:
`/proc/sys/kernel/random/boot_id`, `/proc/sys/net`; tutti i livelli verificati.
I precedenti rifiuti della sonda non hanno eseguito codice prodotto.

Creata copia FISICA indipendente in `/var/lib/machines/rm0008-test-r11`,
root0700, senza avvio o modifica identita'; originale r10 invariato.
Copia e audit terminali exit0. Verifica read-only contenuti, metadati, hardlink,
ACL e xattr: unica differenza il modo0700 della directory radice intenzionale.
Log `/tmp/metnos-rm0008-clone-copy-audit.7Qj4zAaF`; inode Python distinti.
La minore allocazione6.663GB contro6.695GB deriva dalla copia sparse, non da
file mancanti. Prima dell'avvio servono nuova identita' veritiera, root0755,
nuove prove d'isolamento e ripristino conservativo della baseline NELLA COPIA.

Fix prodotto ancora nel candidato, senza commit/repin: ring autenticato passato
dal port transition al cold loader e lettore runtime Birth. Revisione trova
anche `admitted_module_v1` ancora legato ai vecchi public file: correzione
minima e test in corso. Due prove root sintetiche si sono fermate su errori
del manifest/entrypoint della fixture, non su nuovi difetti produttivi.
La produzione e' rimasta invariata e RM-0008 non e' ancora completato.

### Ripresa: clone r11 avviato e isolamento provato — 8 settembre

La prova sintetica reale root->servizio995 della lettura a freddo ha PASS:
unita' `rm0008-root-service-cold-ring-r3.service`, terminale0/MainPID0,
log `/tmp/metnos-rm0008-cold-ring-test.sFLkJxTR`, esito
`ROOT_SERVICE_COLD_RING_OK`. Non e' ancora una prova completa del prodotto.

Identita' r11 assegnata soltanto alla copia indipendente: stager SHA
`f79fbf15f240e71b5e79169f717e8670880f9ba17f54062d90d5b42649330ff0`,
unita' `rm0008-stage-identity-r11.service` terminale0, log
`/tmp/metnos-rm0008-identity-r11.l1Xb8Z1K`. I quattro file identita' precedenti
e il linger sintetico990 sono conservati in `opt/rm0008-lab/identity-r11`
nell'immagine; origine attestata0600 e root immagine0755.

Avviato `rm0008-test-r11.service`, nspawn privato con basic.target, senza
bind di dati o bus host. I soli due tmpfs aggiuntivi mascherano le directory
OS timers.target.wants di /etc e /usr/lib. Limiti effettivi6h/4GiB/swap0/
CPU200%/2048task, Delegateyes. Il leader e' confinato in
`/system.slice/rm0008-test-r11.service/payload/init.scope`; il primo controllo
host si era fermato prima delle sonde perche' ometteva il ramo payload.
Nessun riavvio o nuovo tentativo di installazione prodotto per quel controllo.

Nuove prove root/nobody PASS `R11_ISOLATION_OK`, log
`/tmp/metnos-rm0008-isolation-r11.zW0pYBcC`; sonde raccolte, MainPID0.
Machine-id `de7c3dd4d7bec9950ed4b6b27f3d44bc`, map0->289538048/65536,
sette namespace distinte dall'host, root ext4 r11 rw idmapped, rete solo lo,
filesystem delle sonde realmente RO e NoNewPrivileges1.

Restore SHA `8df6ba62a052d1150b0625554d255f1782b85bd3990bda4cd904c40b002929f3`,
19 test offline PASS, avviato nel guest come `rm0008-restore-r11.service`;
log host `/tmp/metnos-rm0008-restore-r11.C4BcqPYL`. Esito ancora da rileggere:
non ripetere il ripristino su checkpoint esistente.

Esito rilevato successivamente: `baseline_restored`, terminale0/MainPID0,
tutte le14 pubblicazioni concluse e verificate. Hash input e configurazione
preparata concordi; NSS, avvio manager, reload systemd e deploy restano false.
Nessun file eliminato: stati precedenti conservati nel checkpoint del clone.
I componenti mantenuti (ambiente74, browser e fixture esterne) richiedono
ancora la nuova prova operativa; i vecchi risultati r8 non valgono per r11.

Revisione correzione dipendenze: NO-GO alla prima variante che proteggeva
soltanto la directory public; i suoi antenati sintetici restavano sostituibili.
In lavorazione un trasporto delle sole chiavi pubbliche selezionate in un
mount privato alla radice della sandbox, non una riscrittura dei percorsi
CONFIG. Nessun commit/repin/installazione della variante non verificata.

### Chiusura correzione e verifiche estese r11 — 8 settembre, dopo le 06:00 CEST

La correzione e' stata chiusa e revisionata: ring autenticato nel cold loader,
lettore runtime Birth e proiezione delle sole chiavi pubbliche selezionate nel
mount privato `/.metnos-admission-v1`. Il mount e' sola lettura anche senza
dipendenze, con namespace utente annidati negati; `/tmp` e i bind RW espliciti
restano funzionanti. Nessuna pretesa di isolamento dal codice Python capace di
alterare il proprio processo. Nessuna modifica agli executor o ai manifest.

La prima prova G4 realmente eseguita fuori dal contenimento dell'app ha fallito
per la chiave SINTETICA creata0664 dall'umask. Corretto il test a0644, senza
allentare il lettore. Nuova G4 reale2/2 PASS e gruppo runtime127/127 PASS,
inclusi i controlli positivi/negativi e il vecchio test HOME ora con fixture
controllata. Risultati `/tmp/metnos-rm0008-g4-real-r11-fixed.xml` e
`/tmp/metnos-rm0008-admission-cluster-r11.xml`.

Pin runtime congelati prima del repin:
admitted_module `787dbc242cb2111a3f6b72fe4dc75f7e0de9f7551ad3633718e40a2eab812625`;
agent_runtime `f54e71f069b26d431c7213e09dbc79bb8f26d559f09f9400cdfe44656ad87ff8`;
sandbox `bf3d488b62d52f9409b34391793e897f86cf58511f0ee98d6ca9bfd1a5116f11`.
Repin meccanico concluso: private754
`sha256:f4b243aae507c2852aff423c461e8def8e418496b8bebc604cafd78a872388f0`,
public742 `sha256:74bce7dff2a97175337a1620c4ddda0cafc0df32e33d36f8449f689a6291bbd9`.
Il residuo untracked `--help/` e' preservato ed escluso solo dall'export locale
con un excludesFile temporaneo; nessuna modifica all'indice/config Git permanente.

Suite completa portabile+runtime interessato:2350PASS/43SKIP/6FAIL in155.54s,
`/tmp/metnos-rm0008-candidate-r11-final-suite.xml`. Cinque fallimenti UID in
esame con l'apparato fakeroot corretto (non dichiarare ancora PASS). Il sesto
era il censimento2021 non aggiornato per quattro test gia' tracciati; rigenerato
dall'indice a2025, nuova intera prova manifest7/7 PASS in9.04s,
`/tmp/metnos-rm0008-r11-inventory-gate.xml`. Non e' un difetto runtime.

Nuovo censimento read-only nel guest r11, log
`/tmp/metnos-rm0008-g6-input-r11.4USHWUmP`:
ROOT21058righe/19282file/331905991byte, snapshot
`4c4f37b04c064dcc0e4ebf453578b62db6f00d94c002771e102d35a8fd5e124c`,
identita' `(64512,13774862,0,0,0755)`;
vecchia fixture14righe/2file/1byte, snapshot
`c4c08c6da8167eb2409144aa0b2341b8096c9835eb6bfbf176c04c66f052b1af`,
identita' `(64512,13517781,0,0,0755)`; `/run/metnos-g6c-fixture` assente.

Preparati soltanto draft PENDING, non installati/eseguiti: G6 r11 preserva
quei due oggetti esatti senza sostituzione e rimette ROOT identico solo dopo
G6 PASS1/noSkip e quiescenza. CLI r11 pretende nuovi receipt restore/G6/readiness
e verifica il risultato durevole completo. Readiness r11 confronta i browser
gia' presenti con l'archivio byte-per-byte senza estrarre, verifica managed74 e
prova browser/bwrap con UID995 e rete privata. Review indipendente GO
strutturale;13test offline G6/CLI e12test readiness PASS.
Il clone resta attivo con scadenza naturale alle11:40CEST circa.
La produzione e' invariata; RM-0008 NON e' concluso.

### Candidato congelato e prima G6 r11 — 8 settembre, circa 06:30 CEST

I cinque controlli UID sono PASS anche con root reale, senza fakeroot o shim:
unita' host `rm0008-uid-proof-r11.service`, PrivateTmp/PrivateNetwork,
ProtectSystem=strict e sorgente RO; terminale0/MainPID0. XML privato in
`/tmp/metnos-rm0008-uid-proof-r11.GF8ba5uR/result.xml`.
Il PASS simulato precedente e' soltanto una prova aggiuntiva.

Ulteriore insieme runtime Birth:645PASS/4SKIP/2FAIL, XML
`/tmp/metnos-rm0008-r11-birth-runtime.xml`. I due FAIL sono i test di nascita
degli executor consult_frontier/find_places dipendenti da provider esterni;
quel runner lancia Python direttamente, non attraversa la correzione Birth/
sandbox, e i file coinvolti sono invariati. La causa specifica del provider non
e' dimostrata dal report: non dichiarare quelle due prove superate.

Commit locale candidato `298da195550bc0da55ab351dbdfa89f578548c3a`,25file
tracciati,984aggiunte/60rimozioni inclusi test e documentazione; `--help/`
non tracciato e' preservato. Archivio nuovo53073920byte,2997file,
`/tmp/metnos-rm0008-source-r11.tar`, SHA
`770c709100e0cf97693c8a8b0f891c459d065ecb5e4214b265c396f32abd724d`;
source-id `sha256:f5462fc574fe662d3cbf29b7d4558b5bf7892dbc5230fb029c7018bdd01a776b`;
inventario SHA `1265c8109a716668938a1f1ab44480cb42ed0a33ef5db19c7ed80810f1bdac5e`.
Staging nel clone PASS `source_staged`, log host
`/tmp/metnos-rm0008-stage-r11.YhrfyOCg`; sorgente autonoma ricalcolata identica.

G6 r11 realmente eseguita, NON PASS: unita' `rm0008-g6c-r11.service`
terminale78/MainPID0, log `/tmp/metnos-rm0008-g6-live-r11.lmioeWoN`.
Errore preciso nel collaudo: `_materialize_release` eredita umask0077 dal
nuovo wrapper e crea OWNERSHIP_ROOT0700; il prodotto richiede0755 e rifiuta
prima dell'installazione amministrativa. Nessun allentamento del prodotto.
Il test e' finito, resta il solo manager sintetico user1.

Il baseline e la vecchia fixture sono conservati in `g6c-r11/baseline-root`
e `g6c-r11/previous-fixture`, identita' e snapshot originali riconfermate.
Nuovi oggetti falliti esattamente censiti:
ROOT `(64512,13245659,0,0,0700)`,895righe,
`012bb04bca9bf9881922e6f8de01dd0ffc3399c0bcaa16b1169ba36681f3df92`;
FIXTURE `(64512,13667511,0,0,0755)`,13righe,
`780d54de5d25bf841a06ac8c69449acdd29e6637613ec0d55ee1a69ab0c325f7`;
LIFECYCLE `(66,131,0,0,0700)`,1riga,
`c838dc9e8da208025120ac87aa0ff1266f5d19db1a28cdb8f761ce2886db32e9`.
Nessuna unita' G6/c4, nessun `/usr/libexec/metnos` o startup-runtime.
Recupero esatto di questi oggetti in preparazione, NON ancora eseguito;
nessun rilancio automatico, nessuna cancellazione, nessun deploy produttivo.

### Recupero G6 concluso, nuova prova r11b in corso — 8 settembre 06:46 CEST

Il primo helper recupero SHA `d9452eb751eebfea3d121e93a44d86519a144dba48dea5f183b244d2ec19affe`
si e' fermato PRIMA del checkpoint e delle mutazioni: il filtro `*probe*`
includeva `system-modprobe.slice`, che non e' un servizio di prova.
Diagnosi read-only con scritture intercettate ha individuato `terminal()`;
la nuova variante limita quel solo elenco a `--type=service`.

Recupero r11b SHA `b1dce57d5da40de4abe81bc67a4f08d33466db0fd75a6394475202d6b844a93a`
PASS, unita' `rm0008-recover-g6-r11b.service` terminale0/MainPID0.
Log host `/tmp/metnos-rm0008-g6-r11b.siDm5cHV/recover.log` e receipt guest
`/opt/rm0008-lab/recover-g6-r11b/result.json`: entrambi gli originali sono
tornati con IDENTICHE identita', conteggi e snapshot. I tre resti falliti
sono conservati in `g6c-r11/failed-root`, `g6c-r11/failed-fixture` e
`/run/rm0008-g6c-failed-r11`; nessuna cancellazione e nessun esito G6 riscritto.

G6 r11b SHA `25d0a96d3ff0451c198ba1c4858dff62f45f73099e48e71b31d456fda47e3c1e`:
soli cambi funzionali, `umask=0o022` nel subprocess pytest e XML atteso0644;
report del padre ancora0600. Prova reale separata del subprocess PASS
(`/tmp/metnos-rm0008-umask-r11b.zex8lcp8`) e13controlli offline PASS.
Stesso codice prodotto, commit298da195 e source-idf5462fc5 invariati.

Avviata realmente `rm0008-g6c-r11b.service` SOLO dopo il recupero PASS:
log host `/tmp/metnos-rm0008-g6-r11b.siDm5cHV/g6.log`, pytest guest
`/opt/rm0008-lab/g6c-result-r11b/pytest.log`. Alle06:46 e' ancora attiva,
processo test con CPU osservata: NON rilanciare e NON dedurre PASS.
Readiness/CLI locali ora puntano alle nuove ricevute r11b, ancora PENDING.

### G6 r11b PASS, readiness fermata prima del deploy — 8 settembre 07:00 CEST

`rm0008-g6c-r11b.service` e' terminale con exit0/MainPID0: la prova reale
`test_signed_systemd_cell_denies_then_admits_real_timer` e' PASS (1 test,
0 errori/fallimenti/skip), durata 6min42s. Baseline ripubblicato con identiche
identita' e SHA. Ricevuta `g6c-r11b/result.json` SHA
`0158e9544fd0cd9c58f12aee41465f941b60e4da876195548a7838334a003c8b`;
ricevuta restore SHA
`91cd1c55873a0ec378987a5d64c45846ced46a74332be02ac3db91530858bbf4`.

Helper readiness r11 installato root0444, SHA
`469aa4d486bfe8072664697f9568a915a1c0a198f012b9ebb1f792e78f4f4c8a`.
Driver `rm0008-readiness-driver-r11.service` terminale78/MainPID0 dopo3.828s,
fase `real_probes`: censimento 17 pacchetti,74 distribuzioni managed, browser
retained e browser reale offline superati. Chromium149.0.7827.55 ha restituito
titoloRM0008 con UID995/GID985. Prova bwrap NON PASS:
`bwrap: cannot open /proc/sys/user/max_user_namespaces: Read-only file system`.
Output originali conservati in `/opt/rm0008-lab/readiness-r11/`; nessuna
ricevuta di successo, nessuna installazione CLI avviata.

La lettura attuale del clone dimostra `/proc/sys` RO imposto da nspawn e
`/proc/sys/net` RW separato. Il driver ha ProtectSystem=no e
ProtectKernelTunables=no; il catalogo del prodotto per HTTP non impone questi
due limiti. I flag di sicurezza bwrap `--disable-userns` e
`--assert-userns-disabled` restano invariati. Analisi in corso della minima
correzione del solo ambiente clone: non cambiare sysctl host, non esporre
filesystem/processi host e non allentare i controlli per ottenere un PASS.

Draft finale298da195 sottoposto a review indipendente e14test offline PASS;
quattro pin delle prove ancoraPENDING. Non installato o eseguito in produzione.

### Readiness reale PASS; arresto pre-CLI identificato — 8 settembre 07:33 CEST

Correzione esclusivamente nel clone nspawn r11: il mount `/proc/sys` e'
reso private (non ricorsivo), poi self-bind della DIRECTORY `/proc/sys/user`
e remount del solo nuovo bind RW/nosuid/nodev/noexec. Unita' guest
`rm0008-userns-api-setup-r11.service` terminale0 in22ms; nessun sysctl scritto
dall'helper, valori host e guest PID1 invariati. Il nuovo mount734 ha parent360,
device0:72 e root `/sys/user`; tutti gli altri mount invariati tranne la
propagazione private del parent360. Il precedente tentativo nsenter host
aveva fallito alla PRIMA operazione con libmount5005/exit32 senza creare bind;
evidenza conservata, non era un esito positivo. Nessun cambiamento host o
riduzione dei flag bwrap. La modifica e' effimera e confinata al clone.

Nuova attestazione isolamento root/nobody PASS. Readiness r11b terminale0
in4.145s:17pacchetti,74distribuzioni, byte browser, Chromium reale UID995 e
bwrap con entrambi i flag userns superati. Helper SHA
`4d504d90c3032e762a38ee08bbab41d2021a03ea369d10dd438757e01296128f`;
ricevuta `/opt/rm0008-lab/readiness-r11b/result.json` SHA
`f594724131f38cd45c5b9d58cd1a4e5c26e4cc495be4dd7ec31bcd767a8b6507`.
Log host `/tmp/metnos-rm0008-readiness-r11b.pNJegqWC/`.

Driver deploy r11 terminale78 dopo876ms alle05:18:30UTC:
`URLError: [Errno111] Connection refused`. Censimento successivo conferma:
la directory `deploy-real-r11` e' VUOTA, la vera unita' prodotto
`rm0008-guest-deploy-r11.service` non e' mai stata creata/avviata;
ROOT snapshot sempre `4c4f37b04c064dcc0e4ebf453578b62db6f00d94c002771e102d35a8fd5e124c`.
La fixture HTTP ora risponde200 con l'esatto JSON sintetico. Causa accertata:
il GET immediato dopo Type=simple anticipava l'apertura della porta.
NON e' un nuovo fallimento del CLI prodotto.

Nuovo driver locale r11b SHA
`7b03256506a04fe911380bdc3b9da9b5ba650509a1e20f7fdce3685468b75029`,
14test offline PASS e review indipendente GO: chiama lo startup UNA volta,
poi attende al massimo10s solo per connection-refused/timeout del GET.
HTTP error, JSON errato e servizio inattivo restano rifiuti; deploy mai
ritentato. Recupero preparatorio limitato ai5prerequisiti simulati e alla
conservazione NOREPLACE della directory vuota; non ancora eseguito al
momento di questa nota. Nessuna mutazione dello stato applicativo.

Audit readonly delle precondizioni host PASS: zero pending/transactions,
nessuna V2 live/release1,11oggetti archiviati e6alberi conservati.
Il guard amministrativo finale ora usa le5unita' system e15user del catalogo
gia' pinned, invece di bloccare anche `metnos-issues-sidecar.service`,
strumento interno non appartenente al catalogo. Non fermare quel sidecar.
Controllo globale UID995 invariato; test15/15 e test quiescenza PASS.
Draft finale SHA `4d4aab334f17591df90ee2b3076b4a8f2daa063e12d27870da694a0dc595e95b`.
I4pin delle prove finali restanoPENDING; produzione non aggiornata.

### CLI reale r11 avviata — 8 settembre 07:36 CEST

Recupero pre-CLI SHA `e5c24f569d546e4d1beee9dc384011e68afe0f7a02ae59750f03140c2abe1e54`
eseguito sul solo clone, terminale0 in1.147s. Cinque prerequisiti sintetici
fermati; baseline SHA invariato e quiet completa verificata. Directory vuota
originale conservata senza sovrascrittura in
`/opt/rm0008-lab/deploy-real-r11-preflight-failed`; nessuna cancellazione.

Driver r11b installato root0444 con SHA7b032565... invariato e avviato una volta.
Verifica systemd corrente: `rm0008-deploy-driver-r11b.service` attivo PID3335;
la VERA CLI prodotto `rm0008-guest-deploy-r11.service` attiva PID3366,
avvio05:35:28UTC. I prerequisiti sono superati: questa e' un'installazione
reale in corso, non ancora un PASS. NON rilanciare finche' i processi sono vivi.
Host log `/tmp/metnos-rm0008-deploy-real-r11b.1avqk22h/` (stage/preserve/driver.log).
Output prodotto guest `/opt/rm0008-lab/deploy-real-r11/`.

Audit host predittivo readonly
`/tmp/metnos-rm0008-audit-final298-readonly.py` SHA
`ec3c71583f90daa37e966c09dca2aabb0f010fe1bfd43636b31e06ebde1d1b69`
eseguito con filesystem read-only/rete privata. Ha superato quiet,
baseline/HTTP iniziali, archivio/source/lock,71wheel+3supplemento e Python,
ma si e' fermato su ENOENT di `/usr/lib/python3/dist-packages/tomlkit`.
NON chiamarlo audit interamente PASS: i controlli finali before/after non
sono stati raggiunti. Manca soltanto la preparazione di quella directory
VUOTA root0755 come mountpoint: prevederla nel passaggio amministrativo
finale dopo le prove clone, senza pacchetti globali o allentamenti del
launcher. Nessuna directory creata in produzione da questo audit.

### CLI r11 terminata; regressioni di composizione — 8 settembre 08:01 CEST

Il driver r11b e' terminato78 alle05:42:07UTC, dopo6min39.830s.
Non e' piu' in corso. Log host conservato in
`/tmp/metnos-rm0008-deploy-real-r11b.1avqk22h/driver.log`;
stdout prodotto vuoto, stderr `birth_ownership_distribution_invalid`, exit78.
Transazione clone `sha256:4490cc7fe9fa5a4d1f5435d4ea801854a50904247ad4bd0d878eb524520c7b49`
con soli record0 PREPARED e1 RECEIPTS_COMPLETE. Predecessore presente;
preflight amministrativo assente e archivio chain-v1/builds-v1 vuoto.
Non rilanciare o ripulire questo clone: conservare la prova del fallimento.

Causa confermata: il chiamante passava VerifiedDistribution a G6, che richiede
AuthenticatedDistributionRecordV1. Correzione locale circoscritta: autenticare
gli stessi encoded/signature per G6, conservando VerifiedDistribution per il
passaggio successivo. Due test originali RED, poi GREEN; nessun typeguard
allentato e nessuna scrittura guest/host produttiva.

Verifica preventiva estesa al percorso successivo: il dominant startup legava
il catalogo SERVIZI, ma il certificato richiede il catalogo delle RICEVUTE
CONTRATTI. Test del chiamante ora attraversa davvero dominant startup e
certificate-ready, con firma/verifica del certificato, invece di sostituirli.
Ha riprodotto `certificate ready binding`; correzione locale usa il codec
canonico delle ricevute e conserva la doppia ricattura del catalogo servizi.
Gruppo mirato:259PASS16SKIP, `/tmp/metnos-rm0008-crossings-r12.xml`.

Terzo difetto individuato PRIMA di un nuovo tentativo: selezione del candidato
a RECEIPTS_COMPLETE pretendeva un build archiviato, ma la pubblicazione del
build avviene solo dopo il certificato. Test nuovo con archivio vuoto RED.
Correzione locale in corso: usare il candidato appena autenticato, confrontare
byte/hash con il record durevole e qualsiasi archivio gia' presente, senza
anticipare scritture o selezionare una vecchia testa.37test materiali PASS;
restano da ampliare prove negative e verifica del chiamante produttivo.

Nessun nuovo commit, inventario, archivio r12 o installazione finale.
Tutte le prove r11 appartengono ancora al commit298 e NON certificano queste
modifiche. R12 non creato all'ultimo controllo; sorgente clone r10 conservata.

### Candidato congelato e clone r12 — 8 settembre 08:36 CEST

Commit `fa85e9a6d11e37fc660ef954399c19bc8c6ff782` sul worktree
`/tmp/metnos-rm0008-legacy-catalog-fix`: tutte e tre le correzioni concluse,
nessun allentamento dei controlli. Tracked pulito; directory untracked
`--help/` preservata ed esclusa dall'esportazione. Test mirati296PASS17SKIP;
27test installer/documentazione PASS. I5test UID impossibili nella sandbox
PASS con UID root reali in unita' host isolata. I2test di pin prima falliti
PASS dopo rigenerazione del censimento. Documentazione97HTML validata;
pubblicazione e Tutor NON ancora eseguiti.

Archivio `/tmp/metnos-rm0008-source-r12.tar`,53094400byte,
SHA `02ed2a823b713b2fe81fe4c88a7d03f9375397e4eec72e4662c71fa977e526f6`;
source-id `sha256:05c6494571bcc0887b395bbd4b9a358a42cbcdb55bbb4c82be521e1af373dd07`;
inventario SHA `c6093ca64539d765df9a80a4256e0662bf801e8cf226fddbb6b5cd8ac0046ae8`.

Clone r12 indipendente da r10 fermo, confronto fisico byte/metadati PASS
(solo permesso temporaneo della radice, poi normalizzato), rete solo loopback.
Unita' `rm0008-test-r12.service`,nspawn1636499,init1636502,start206023977;
machine-id `00a8ab3734e2171e395d715183f20fa2`,uid/gid-map0:289603584:65536.
Restore e staging TERMINATI0: log `/tmp/metnos-rm0008-restore-stage-r12.4tX47S5d/`.
Ricevuta restore SHA `719af9d58f7c7d3cece6628f71472eeb98d741eaa55e4d67862e9b0885654100`;
ricevuta source SHA `49c32cb84e6b514a5b098c080d4da1ef88f1826b5c17eb1ffee7242bded58756`.
Snapshot baseline e fixture identici ai pin precedenti; nuovi inode14297687
e14041229. API `/proc/sys/user` predisposta solo nel namespace guest,
altri mount e valori sysctl invariati; passaggio terminale0.

G6 reale r12 AVVIATO, non ancora PASS: host session83877,
guest `rm0008-g6-driver-r12.service`; log `/tmp/metnos-rm0008-g6-r12.68zzykig/`.
Helper SHA `39a3955810df97baa485f62aac8bf8c84471f47b2af6793a5886c63d3bc8b6a2`;
13test adattatori offline PASS. Non rilanciare mentre vivo. Driver readiness
e CLI r12 preparati ma pin prove PENDING, non installati/ne' eseguiti.
Produzione invariata; G6,readiness,CLI,turno funzionale r12 ancora da provare.

### G6 e readiness r12 PASS; avvio CLI — 8 settembre 08:50 CEST

G6 r12 TERMINATO0, con baseline ripubblicata byte/metadata/inode identici.
Ricevuta `g6c-r12/result.json` SHA
`f867016c1ce6b4c79a9bae62adc84dcff88a37d987281c3bc53eb782aad7c643`.
Readiness r12 TERMINATA0:17pacchetti nativi,74dipendenze gestite, browser
e sandbox reali PASS. Ricevuta `readiness-r12/result.json` SHA
`ec9045aa7d34d40c7b78846c141c26ec2edcc8f22d4c8952fbf3ff59cb3d849c`;
log host `/tmp/metnos-rm0008-readiness-r12.k692q3wt/`.

Un primo TRASFERIMENTO dell'helper readiness era stato rifiutato dal digest
prima di scrivere il file: systemd espandeva i riferimenti `${...}` presenti
nel testo Python. Evidenza terminale1 e file assente, log
`/tmp/metnos-rm0008-readiness-r12.ypn0d5en/stage.log`. Trasporto corretto con
byte in base64 verificati, senza cambiare il contenuto SHA414cf639...;
la readiness stessa e' stata eseguita UNA sola volta. Non e' un tentativo
CLI o una modifica al prodotto. Usare trasporto binario per i nuovi helper.

CLI r12 helper root0444 SHA
`e9f7bd2e1b1d670434db7600c65d4df7e3a539207238997ab809cbcc7e297e79`;
verificatore root0444 SHA
`5844b27bd06e6179b18b9954443c47daca06308b2c186523c137c853b06a991a`.
13test adattatori e13test attesa HTTP PASS; funzioni CLI identiche al driver
revisionato salvo identita'/pin. Avvio unico sotto session81904, unita'
`rm0008-deploy-driver-r12.service`; vera CLI `rm0008-guest-deploy-r12.service`.
NON ancora PASS, non rilanciare durante l'esecuzione.

Draft finale `/tmp/metnos-rm0008-final-fa85e9a6-r12.py` SHA
`d35121d66d4d8891d28f976c24fe963387467cb379aa515fa4118f433b82fe34`:
15test PASS e archivio3257membri/2997file verificato. Quattro pin prove
ancora PENDING, non installato. Verificatore produzione SHA
`1487d289caa22e86db0dc8a68f1da730809cf7f87c7149e4c91ec487ef3568ea`.
Probe funzionale guest e produzione predisposti, non eseguiti;10test
protocollo/context PASS. Nessun cutover produttivo effettuato.

### Audit host completato e CLI viva — 8 settembre 08:53 CEST

Host log CLI r12 `/tmp/metnos-rm0008-deploy-real-r12.qqfu8soy/`.
Alle06:53UTC driver PID2922 e CLI prodotto PID2953 entrambi attivi;
stdout e stderr ancora vuoti. Host session81904 ancora viva: attendere,
non rilanciare. Nessuna ricevuta CLI o funzionale ancora disponibile.

Nuovo audit sola lettura `/tmp/metnos-rm0008-audit-finalfa85-readonly.py`
SHA `0b206c46504ea4c061f8db358f812db421da455ecc6739eaa7ed494d586be9cb`,
eseguito sotto ProtectSystem=strict/ProtectHome=read-only/PrivateNetwork,
terminale0. Log `/tmp/metnos-rm0008-audit-finalfa85.i_5f51yq/audit.log`.
Stato esplicito `rm0008_predictive_checked_preparation_required`, NON GO
produttivo. Tutti i confronti byte prima/dopo sono conclusi: baseline6alberi,
40unita', HTTP, archivio3257membri/2997file,Python,71wheel e supplemento4
coerenti. La directory `/usr/lib/python3/dist-packages/tomlkit` e' ancora
ASSENTE, genitore sicuro: predisporla vuota root0755 soltanto nel passaggio
finale dopo4prove clone. L'audit segnala l'assenza senza fermare gli altri
confronti; nessun mutatore o requisito del launcher e' stato allentato.

### CLI r12 terminata con rifiuto — 8 settembre 09:04 CEST

La sessione81904 e' TERMINALE78, non piu' viva. Log host
`/tmp/metnos-rm0008-deploy-real-r12.qqfu8soy/driver.log`: transizione fallita,
evidenze conservate, nessun retry. Stderr guest `birth_ownership_preflight_invalid`;
stdout vuoto, exit-code78. La transazione
`sha256:9a7375c9ad22edb657e4d7194029e6145ead1f55a1a321bb8be81525bef28941`
ha record0 PREPARED e record1 RECEIPTS_COMPLETE, nessun record successivo
all'ultimo censimento. G6 e readiness restano PASS per fa85, ma CLI e prova
funzionale NON PASS. Nessuna modifica produttiva, draft finale ancora4PENDING.

Prossimo passo: individuare il dettaglio del rifiuto mediante il confine
preflight di sola lettura sui materiali del clone; NON invocare di nuovo la
transizione completa, NON ripristinare il clone e NON indebolire i controlli.

### Rifiuto r12 riprodotto e correzione locale — 8 settembre 09:16 CEST

Diagnostica SOLA LETTURA terminata0, filesystem protetto. Log
`/tmp/metnos-rm0008-preflight-diagnostic-r12.oedqkinj/diagnostic.log`.
Codec catalogo/descrittore, compilazione unita', TCB e autenticazione snapshot
fisso PASS. Fallisce `_service_source_identity_v1`: dettaglio
`service source recipe`, riga3123 del candidato fa85 congelato.

Causa riprodotta offline: la proiezione della struttura non normalizzava
`Service/ReadWritePaths` di Telegram rispetto alla home firmata. Il pin
conteneva implicitamente `/var/lib/metnos/.local/{share,state}/metnos`, home
delle fixture; home reale `/var/lib/metnos-service` produceva altro digest.
Il doppio interprete NON e' la causa di questo rifiuto.

Correzione NON committata in `/tmp/metnos-rm0008-legacy-catalog-fix`:
13righe nel ramo comune della proiezione e nuovo unico digest della struttura
`sha256:ef01c27393455d4d7f262f1de0778c58d68549ca56ca09efe54bf279a30a9c2c`.
Normalizza soltanto i percorsi sotto la home firmata, conserva suffissi esatti
e percorsi fissi; nessun permesso aggiunto, nessuna alternativa permissiva.
SHA del modulo modificato
`0db8625bce930f4c85f9a7f1462e331e5defce503a2f2103dd0361399a8f9317`.
Clone, release e archivio r12 conservano fa85 INVARIATO; non sono nuove prove.

Test RED:4casi di home differente fallivano prima del fix. Ora142PASS15SKIP
nelle4suite materiali/catalogo/transizione/cutover; XML
`/tmp/metnos-rm0008-home-binding-final.xml`. Include6contesti home/interprete,
3mutanti con percorsi scrivibili ricalcolati e3home nel vero binder candidato.
Usare `/opt/metnos/.venv/bin/python` (pytest9.1.1,tomlkit0.15.0): Python di
sistema non ha tomlkit e un primo controllo esteso ha avuto6errori di import,
poi tutti risolti rieseguendo nell'ambiente corretto; nessuna modifica ai test
per aggirare errori prodotto.

Confronto di sola lettura sugli STESSI due JSON reali del clone r12:
controllo fa85 rifiuta, controllo corretto PASS; byte catturati invariati.
Questo prova il difetto locale, NON il cutover completo. Documentazione IT/EN
e ADR0224 aggiornati localmente; pubblicazione e Tutor ancora non eseguiti.
Restano censimento/pin sorgente, nuovo congelamento e prova integrale su clone
indipendente prima di qualsiasi passo produttivo. Nessun nuovo clone avviato.

Controllo esteso concluso:276PASS3SKIP1FAIL nelle4suite preflight/launch/TCB/live;
XML `/tmp/metnos-rm0008-home-binding-preflight.xml`. Unico fallimento
`test_installed_preflight_rejects_candidate_self_attestation`: confronto
`Python source-review root`, perche' il sorgente modificato non ha ancora
il nuovo pin compilato. NON saltare il test: rigenerare il censimento/pin e
riverificarlo prima del congelamento. Validazione documenti97HTML PASS.
Tutte le sessioni strumenti di questa ripresa sono terminali; nessuna CLI
o diagnostica ancora in esecuzione. Ambiente test corretto confermato sopra.

### Candidato r13 congelato, clone preparato — 8 settembre 09:34 CEST

Questo aggiornamento supera lo stato locale non committato delle09:16.
HEAD candidato `/tmp/metnos-rm0008-legacy-catalog-fix`:
`3e815ab58bd03fdf055ec0e65582783a0cb850ee`.
Correzione di prodotto limitata al ramo13righe ReadWritePaths descritto sopra;
test, pin, ADR e documentazione IT/EN aggiornati nello stesso commit.
Tracked pulito; directory untracked `--help/` conservata ed esclusa dagli export.

Suite portable completa:2101PASS47SKIP5FAIL, XML
`/tmp/metnos-rm0008-portable-r13.xml`. I5fallimenti sono i controlli di owner
Unix impediti dalla sandbox: rieseguiti tutti nell'ambiente root isolato,
5PASS, XML `/tmp/metnos-rm0008-uid-proof-r13.cj7037ih/result.xml`.
Non confondere questo risultato combinato con una suite iniziale senza errori.
Censimento, pin e confronto della radice di revisione ora PASS.

Archivio `/tmp/metnos-rm0008-source-r13.tar`, SHA256
`ab221c097d9c9ae867c62ae2c1d1a7632394d5d5352c47c99806f6f1dd16a90b`;
source-id `sha256:7919bfd7017f74a3a4776c1de22710e3c7ed161abfe5538681158719eaa9dcc9`;
inventory SHA256 `99d345bf5eeda8c2718e593600735d4017bbd045dde03aa372d37f019a61f16a`.
Radice Python privata754file:
`sha256:e21f1c33f17fe9139f25bbfb8c4b032336d37326e6493f4e61fa3eef6c0da53e`;
pubblica742file:
`sha256:9a9f30828800949c7d8840cd2ee26265240934ad8e59a44c5398fc9893b5b1f4`.
Export locale `/tmp/metnos-rm0008-public-r13.RKZ0xRIT/tree` verificato;
pubblicazione documenti e rigenerazione/prova Tutor ancora DA FARE.

Clone indipendente `/var/lib/machines/rm0008-test-r13` copiato dalla sorgente
ferma `rm0008-test-e0a0df0d`, confronto completo read-only PASS salvo modo
della directory radice intenzionalmente0700 durante la preparazione.
Audit `/tmp/metnos-rm0008-copy-audit-r13.qdz132ix/`.
Identita' r13 installata con conservazione degli originali; sessione67683
TERMINALE0, log `/tmp/metnos-rm0008-identity-r13.gjtkydeg`:
`R13_IDENTITY_STAGED original_identity_and_linger_preserved no_boot`.
Machine-id `c7c56f96aa8c4f5c9aaef01d3f53c60e`, mappa prevista
`0:289669120:65536`. Clone NON ANCORA AVVIATO; nessuna prova G6/readiness/CLI
o funzionale r13 disponibile. Le prove r12 non valgono per questo candidato.

Prossimo passo: prova offline del restore r13 e staging verificato degli
artefatti, avvio isolato, G6/readiness, una sola CLI completa e prova reale
`/agent/turn`. Clone r12 fallito conservato: NON ritentare quella CLI.
Produzione non modificata in questa ripresa; vecchio launcher finale fa85
ancora non valido per r13 e NON da eseguire. Non dichiarare RM-0008 concluso.

### G6 e preparazione r13 PASS; CLI avviata — 8 settembre 09:52 CEST

L'utente ha segnalato risorse limitate: nessun nuovo agente, nuova copia o
nuova suite completa; riuso del clone r13 e dei controlli mirati esistenti.
Clone r13 avviato: nspawn PID1682691, init PID1682695, start206455962;
isolamento verificato, log `/tmp/metnos-rm0008-isolation-r13.3tfb83x2/isolation.log`.
Ripristino ingressi, staging sorgente e censimento tutti TERMINALI0:
`/tmp/metnos-rm0008-restore-stage-r13.0uvd52__/`.
Test offline restore:19PASS; test adattatori e controlli CLI:13PASS.

G6 reale PASS, baseline restituita identica anche per inode; sessione51663
TERMINALE0, log `/tmp/metnos-rm0008-g6-r13.ozfb9bno/`.
Ricevuta guest `/opt/rm0008-lab/g6c-r13/result.json`, SHA256
`7ef6844a9a2b605712136bfd8ea594e29c8525058820cf72bf33c4b968f1859b`.
Identita' baseline `[64512,14960748,0,0,493]`; fixture precedente inode14705224.
Browser, bwrap,17pacchetti nativi e74dipendenze gestite PASS:
sessione46372 TERMINALE0, log `/tmp/metnos-rm0008-readiness-r13.hs09ejfm/`;
ricevuta `/opt/rm0008-lab/readiness-r13/result.json`, SHA256
`3107a5b117554edb564c7dc3beb6c5effff3eadd11881d3b6a59336411dc17bd`.

CLI reale r13 AVVIATA, sessione88553 ancora viva all'ultimo controllo09:51;
NON rilanciare. Log host `/tmp/metnos-rm0008-deploy-real-r13.zjyz_3z5/`.
Driver guest `/opt/rm0008-lab/deploy-real-r13.py`, SHA256
`1078d147eaf6c5194219500ba7fa40492a54cb400095c141f35791ba6dd864cc`.
Verificatore guest SHA256
`330540bb807e0ee6c7164ce521edbdccd907d9559a4881d4761f488ccbf1c5a8`.
Prova funzionale r13 preparata ma NON eseguita, CLI_RESULT_SHA=PENDING.

Draft produttivo `/tmp/metnos-rm0008-final-3e815ab5-r13.py`, SHA256
`4339687719b5829038f5ed347a56e8d366d022dbe650fb13144ffea278139ac2`:
due prove reali presenti, CLI/funzionale ancora PENDING; NON installato o
eseguito. Test offline draft15PASS e protocollo funzionale10PASS.
Verificatore produttivo `/tmp/metnos-rm0008-verify-success-3e815ab5-r13.py`,
SHA256 `2cd26fbef7e068cd72da53a56325b2955a49f4f5ae4c14a4b7ba08b801888ef3`;
probe `/tmp/metnos-rm0008-functional-probe-3e815ab5-r13.py`, SHA256
`1c11d680c1fa3e1ee1dc31b209f5ca133a57114ac7a1767c2c15883db3887803`.

Host:17pacchetti nativi e bubblewrap gia' presenti alle versioni verificate
nel clone. Browser gia' presente con le tre versioni attese, directory cache
owner995:985 modo0775 e ulteriore `.links`: NON equiparare questa sola lettura
alla prova operativa del browser e NON cambiare permessi automaticamente.
Produzione non modificata; pubblicazione documenti e Tutor ancora da fare.

### Controlli host r13 conclusi; installazione ancora attiva — 8 settembre

Confronto in sola lettura del browser host: tre alberi con617elementi,
598file e674450646byte IDENTICI all'archivio verificato nel clone.
Restano differenze di modo in19elementi, cache0775 e `.links` aggiuntiva;
nessun permesso o file cambiato. Il primo script diagnostico confrontava
anche atime dopo la lettura e ha rifiutato: corretto il solo confronto
di stabilita' escludendo atime, mantenendo device/inode/modo/owner/link/
dimensione/mtime/ctime; secondo confronto concluso0. Non e' un retry della
CLI e non prova l'avvio del browser nel nuovo ambiente produttivo.

Audit host del draft r13 TERMINALE0:
`/tmp/metnos-rm0008-audit-final3e815.s0qttaqw/audit.log`.
Script `/tmp/metnos-rm0008-audit-final3e815-readonly.py`, SHA256
`b9ea158ff5dda8a58da888ad98893f7be6abf4c7ab70a516409b31633cbe1ffa`.
Baseline completa e ingressi HTTP stabili, archivio sorgente3257elementi/
2997file verificato. Wheelhouse71su74 e supplemento locale esatto disponibili;
resta solo la predisposizione esplicita della directory tomlkit vuota.
Esito `rm0008_predictive_checked_preparation_required`, NON autorizza
il passaggio produttivo prima delle due prove clone ancora mancanti.

CLI r13: sessione88553 confermata ancora viva, driver PID1689916;
CLI padre PID1689949 e figlio managed PID1690783, quest'ultimo attivamente
in calcolo (>535secondi CPU all'ultimo censimento). Transazione
`sha256:aab3b869d029840e1fcfb032b831729c04b2a5ba84f3cfb290be9bbeb7cbcdde`,
record0 PREPARED e record1 RECEIPTS_COMPLETE; stdout/stderr ancora vuoti.
Non considerarla terminata per assenza di output e NON rilanciarla.
## R13: esito terminale e diagnosi mirata — 8 settembre 2026

Questo aggiornamento supera le indicazioni precedenti che descrivono la CLI
r13 come ancora attiva. La sessione 88553 è TERMINATA con exit 78; non
rieseguirla. Il log del driver è
`/tmp/metnos-rm0008-deploy-real-r13.zjyz_3z5/driver.log`.
Il prodotto ha restituito `birth_ownership_preflight_invalid`. La transazione
`sha256:aab3b869d029840e1fcfb032b831729c04b2a5ba84f3cfb290be9bbeb7cbcdde`
ha solo PREPARED e RECEIPTS_COMPLETE, nessuna prova finale CLI o funzionale.

Le diagnosi native, in sola lettura e senza ripetere la transizione, sono in
`/tmp/metnos-rm0008-preflight-diagnostic-r13.tcwfjxsz/diagnostic.log` e
`/tmp/metnos-rm0008-materials-diagnostic-r13.hw19od8p/diagnostic.log`.
Passano: decodifica catalogo e descrittore, service_source_identity (il
difetto r12 è corretto), compilazione unità, cattura TCB, autenticazione
snapshot di ownership, tutti gli otto legami tra descrittore/catalogo/
transazione/predecessore, byte degli artefatti, frammenti systemd e binding
degli eseguibili. Queste sono prove parziali, NON un'attestazione di successo.

Per rispettare il limite di risorse: nessun nuovo agente, clone o suite completa;
si conserva lo stato fallito r13 e si restringe la diagnosi sullo stesso clone.
Produzione non modificata. Il candidato rimane 3e815ab5; il draft finale rimane
bloccato dai due pin CLI/functional PENDING. Non installare o eseguire il draft.

### R13: causa isolata e correzione watchdog verificata — 8 settembre 2026

Diagnosi successiva `/tmp/metnos-rm0008-narrow-preflight-r13.zctlcynn/diagnostic.log`:
la revisione delle sorgenti effettivamente installate passa; TUTTE le 12 unità
live sono già identiche ai frammenti del candidato. La transizione è dunque
arrivata all'installazione della topologia, pur senza attraversare il certificato.
Non dedurre dal solo record RECEIPTS_COMPLETE che la topologia sia intatta.
Il confronto con il manager passa per 11 unità e fallisce solo per Playwright.

Errore esatto in
`/tmp/metnos-rm0008-directive-mismatch-r13.7be4cuge/diagnostic.log`:
`Service/WatchdogSec`: firmato `45000000`, osservato `infinity`.
Systemd 255 espone il watchdog runtime, inizialmente infinito; copia il valore
configurato solo in `service_start()`. Riscontro primario:
https://github.com/systemd/systemd/blob/v255/src/core/service.c
https://github.com/systemd/systemd/blob/v255/src/core/dbus-service.c

Correzione LOCALE in `/tmp/metnos-rm0008-legacy-catalog-fix`:
HEAD rimane `3e815ab58bd03fdf055ec0e65582783a0cb850ee`, con 8 file modificati
NON committati, più il preesistente `--help/` non tracciato e conservato.
19 righe di logica nel preflight: per un watchdog configurato, soltanto il
sentinel infinito iniziale è proiettato sul valore firmato. Serve nello stesso
show una prova completa inactive/dead, MainPID=ControlPID=0 e timestamp monotono
del primo avvio principale=0. Dopo l'avvio il confronto rimane esatto.
I cinque campi sono prove, non entrano nell'hash stabile della configurazione.
Nessun nuovo framework, nessun cambio a unità, timeout firmato o capacità.

Test: il caso iniziale fallisce prima della correzione; dopo la correzione
25 PASS / 1 SKIP nel modulo systemd-live e 282 PASS / 3 SKIP nei tre moduli
preflight interessati (3.02 secondi). Coperti dati mancanti/duplicati,
avvio precedente, processi presenti, stato attivo/in avvio e timeout diversi.
97 pagine HTML pubbliche validate; documentazione IT/EN e decisione aggiornate.

Prova NATIVA in sola lettura, sullo stesso clone fallito:
`/tmp/metnos-rm0008-watchdog-proof-r13.zksm3clh/verify.log`, exit 0.
Il modulo diagnostico separato
`/opt/rm0008-lab/watchdog-candidate-preflight.py`, SHA256
`ca0302a1a490c2098a466279eeb3aa8f4d9ad63eb93d6a38737282d2b6d167ae`,
esegue l'intero lettore effective-systemd e la sua rilettura: 12 unità PASS,
snapshot `sha256:1106a69deea65a490e31116e7d4b802919a8d1c4244dff40b9c54208b0641f0f`.
NON sono state fabbricate distribuzioni, firme, seal o attestazioni: il lettore
non autorizzante usa soltanto i campi catalogo/descrittore/frammenti reali.
La release r13 congelata e la transazione fallita NON sono state modificate,
nessuna CLI ritentata, nessun servizio produttivo riavviato.

Pin sorgenti locali aggiornati con patch esplicite:
private 754 `sha256:82ef18a60c3313a03bd142eddb1722f0b0faaa47d6604d497d322400a5d8857d`;
public 742 `sha256:b24a457556caa4d0d16f2d3e21975b8b364e24d6a3f72cb4b181c22e156432b3`.
Il pubblico è calcolato in memoria applicando le sole tre inserzioni al precedente
export verificato r13; non è ancora stato materializzato un export nuovo.
Il primo confronto carattere-per-carattere è stato interrotto (sessione17739,
exit130) perché inefficiente; il confronto per righe termina subito e verifica
contesti univoci. Non ripetere il confronto a caratteri su questo modulo grande.

Prossimo lavoro residuo: congelare il candidato aggiornato, verificare l'export
effettivo, ottenere prova end-to-end e funzionale valide per questo candidato,
poi il passaggio produttivo e pubblicazione documenti/Tutor. Non riusare i pin
CLI/G6 del vecchio candidato come attestazione del nuovo. Per il limite di
risorse espresso dall'utente evitare nuove copie e suite complete: conservare
queste prove e pianificare il riuso conservativo del laboratorio, NON un retry
cieco sullo stato r13 modificato dalla topologia.

## 2026-09-08 — candidato watchdog congelato e riuso conservativo del clone

Supera le note precedenti su commit/export non pronti. Nuovo commit
`b34c1140926d33863423f1ff45a262a68f372c8a`, worktree candidato pulito nei
tracciati; il preesistente `--help/` resta escluso e conservato.
Export pubblico effettivo `/tmp/metnos-rm0008-public-watchdog.FdzdYoCF/tree`:
1732 file, scrub senza PII, 97 HTML validi, radice pubblica 742 confermata
`sha256:b24a457556caa4d0d16f2d3e21975b8b364e24d6a3f72cb4b181c22e156432b3`;
proiezione dei pin pubblici applicata con `public-fs-pin`, exit 0.

Archivio privato `/tmp/metnos-rm0008-source-r14.tar`, 49766400 byte,
2997 file / 3257 membri, SHA256
`5978f5734eace244f2a0c5289aea4a2ae534cae403fd3afdd5a546eaa9823664`;
source ID `sha256:0c8938cadb8b9cf886b9e933e1e152e23bf1210eb285648f609a38d584b4c2ae`;
inventario SHA256 `12a6813863a0587a3c4bdfef03f773fb3f009727a02ab805124799f85aeef84c`.
Primo output vuoto per normalizzazione mode Git mancante conservato come
`/tmp/metnos-rm0008-source-r14.empty-preserved.tar`; nessun archivio valido perso.

Run r14 RIUSA la macchina r13, NON è una nuova macchina o copia.
Identità, mappatura, namespace e origine della macchina r13 restano invariati.
Log `/tmp/metnos-rm0008-reuse-stage-r14.1pf7ab6m`: tutti i passi exit 0.
31 oggetti di topologia esattamente censiti, inclusi link e unità, conservati
senza sovrascritture in `/opt/rm0008-lab/reuse-preparation-r14`; gate volatile
conservato in `/run/rm0008-lab-preserved-r13-startup` sul medesimo filesystem.
Quindi il restore già revisionato ha conservato e ripristinato i 14 target
in `/opt/rm0008-lab/restore-clone-r14`, senza rigenerare chiavi o cambiare NSS.
Solo il manager utente del clone è stato fermato; nessuna modifica produttiva.
Daemon-reload del clone eseguito dopo il restore, nessun avvio Metnos.
Nuovo source ID verificato anche dal checker indipendente dentro il clone.
Baseline identica in byte/metadata: SHA256
`4c4f37b04c064dcc0e4ebf453578b62db6f00d94c002771e102d35a8fd5e124c`,
identità `[64512,15089865,0,0,493]`. Fixture G6 live assente;
quella precedente resta archiviata in `g6c-r13/completed-fixture`.

Helper riuso root 444 SHA256
`b5c6422b132b5e5d10a0cb96a0db59a900a21d38212d8bcd2d66dda4a6d6ced1`;
test offline rename: tre tipi preservati e tre collisioni rifiutate.
G6 aggiornato avviato con output nuovi r14, ancora da leggere a conclusione.
La prova non può essere dichiarata PASS prima del risultato; CLI r14 e
funzionale ancora da eseguire. Produzione NON aggiornata.

### Run r14: G6 e readiness PASS, prova CLI avviata

Il G6 nuovo è terminato con exit 0, singolo caso nativo firmato senza skip:
`/tmp/metnos-rm0008-g6-r14.avr2erdp/g6.log` e
guest `/opt/rm0008-lab/g6c-r14/result.json`, SHA256
`30dfee62e5158e95fca69c8c6f24d1f7956b153afbdd58eb79fe827c90074916`.
Baseline preservata/restituita identica, identità `[64512,15089865,0,0,493]`;
ambiente managed invariato SHA256
`be4478667f70b0f32888942e899122f77d453e8e5e2057463ff9202889ff06d7`.

Readiness nuova PASS, exit 0:
`/tmp/metnos-rm0008-readiness-r14.k4u3ag0k/readiness.log` e
guest `/opt/rm0008-lab/readiness-r14/result.json`, SHA256
`598635f73bd178f90b0f57574cc3ae9127c6bb39a0742f8f61c7cf4c24f74734`.
17 pacchetti nativi e 74 distribuzioni managed verificati, browser reale
UID995/GID985 e bwrap reali PASS, nessuna navigazione esterna o installazione.

Driver CLI nuovo `/tmp/metnos-rm0008-guest-deploy-real-r14.py`, SHA256
`86ecd10fd6db9ec5f43b8b90351f1974e6a3d0c8c7cd33d7670e38645bf8a809`;
helper installato guest `/opt/rm0008-lab/deploy-real-r14.py` root444.
Sessione tool host `86361`: avviata prova, attendere l'esito reale.
Nessun retry del vecchio r13: candidato b34c, output nuovi, baseline restaurata.
Le bozze produttive aggiornate in `/tmp/metnos-rm0008-*-b34c1140-r14.py`
sono ancora NON installate, con prove CLI e funzionale PENDING.
Test offline adattatori 13 PASS e barriera produttiva 15 PASS (nessuna
attestazione sintetica scritta: fixture solo in memoria nei test).

### Run r14 terminato: transizione firmata completa, avvio fallito (8/9, 11:23 CEST)

La sessione `86361` è TERMINALE, exit 78: non riprenderla o rilanciare il driver.
Log host `/tmp/metnos-rm0008-deploy-real-r14.drrz4he8/driver.log`;
stderr prodotto `birth_transition_activation_failed`, nessun `result.json`.
Il clone contiene tutti i sette record, fino a `record-006-v2.json` nello stato
`PREFLIGHT_VERIFIED`. Questo prova l'avanzamento della transizione, NON il
successo end-to-end: target/readiness non attivi, nessun turno funzionale svolto.

Cause misurate, non ipotesi:

- Playwright: `ExecStartPre` iniziato 11:20:31, timeout alle 11:22:01 dopo 90 s;
  29,919 s CPU. Il successivo TERM dei servizi deriva dall'arresto controllato.
  Cgroup clone: `oom=0`, `oom_kill=0`, memoria massima 3365879808 byte.
- HTTP ha completato il primo controllo e avviato il processo, poi alle
  11:22:56 ha rifiutato il contesto. Diagnosi separata, filesystem in sola
  lettura e identità reale 995:985, in
  `/tmp/metnos-rm0008-context-diagnostic-r14.uo039umr/diagnostic.log`:
  `BirthBootstrapError -> OwnershipChainError`, dettaglio
  `required lock metadata`; causa ultima `PermissionError` leggendo
  `/var/lib/metnos/executor-birth/chain-v1/.required-head-v1.lock`.
  Call site `executor_birth_ownership_chain.py::_require_required_head_lock_metadata_v1`
  chiama `_safe_read` su un lock intenzionalmente root:root 0600. Non allargare
  alla cieca i permessi del lock né rigenerare chiavi.
- Profilazione non autorizzante e in sola lettura del controllo installato:
  `/tmp/metnos-rm0008-profile-preflight-r14.b48nvxjs/profile.log`, processo
  terminato. 80,225 s sotto profiler, 531 milioni di chiamate: 59,495 s nel
  censimento statico di 754 Python e 20,075 s nell'analisi degli import.
  Il primo caricamento dei materiali assorbe 79,833 s; il percorso normale
  lo esegue nuovamente dopo l'osservazione systemd. Questi tempi includono
  l'overhead del profiler e non sono una misura diretta della latenza normale.
- La stessa diagnosi ha individuato un ulteriore rifiuto dopo l'arresto:
  `parse_systemd_exec_v1` non accetta il dato reale `code=killed ; status=15/TERM`.
  È lo stato dinamico del processo, non una variazione del comando firmato.

Non è stata applicata alcuna nuova patch al prodotto dopo `b34c1140`.
Prossimo intervento: correggere separatamente lettura del controllo privato,
grammatica degli esiti systemd e ripetizione dell'analisi puramente statica;
conservare letture complete, firme, confronto A/B e controlli dell'ambiente vivo.
Non aumentare semplicemente i timeout e non riusare un risultato diagnostico
come attestazione di successo.

Controllo preventivo HOST nuovo, solo lettura, PASS con preparazione residua:
`/tmp/metnos-rm0008-audit-b34c-r14.j5tq15ao/audit.log`. Sei alberi e configurazione
HTTP stabili; archivio nuovo verificato; 71/74 wheel, tre mancanti già disponibili
nel supplemento verificato; mountpoint tomlkit ancora assente. Prima invocazione
della diagnosi fermata da import mancante prima degli accessi operativi, poi
corretta: nessun tentativo produttivo. Dieci test offline del protocollo funzionale
riapplicati ai due helper r14: PASS, 0,032 s. Produzione tuttora NON modificata.

### Candidato r15: tre regressioni corrette e verificate prima del tentativo

Le tre correzioni sono ora nel worktree candidato
`/tmp/metnos-rm0008-legacy-catalog-fix`, ancora senza installazione produttiva:
lettore POSIX non privilegiato verifica i metadati del lock root:root 0600 senza
aprirlo; amministratore/test continuano a verificare il marcatore; grammatica
systemd accetta gli esiti nativi per segnale; censimento statico puro conserva
al massimo una coppia identica di percorsi/byte nel processo. Nessun incremento
dei timeout, nuovo permesso, cambio chiavi o cache dello stato vivo.

Regressioni osservate RED prima delle patch (14 fallimenti), poi 370 PASS e
3 SKIP nei quattro moduli preflight/materials/systemd/ownership-chain, 5,94 s.
I tre SKIP sono requisiti ambientali, non successi. Sei test del pubblicatore
PASS. Test nuovo conferma che la cache calda non evita il rifiuto di metadati
del filesystem alterati. Controllo policy generata e confine chiuso PASS;
97 documenti HTML bilingui validi. Test fixture inizialmente incompleta
corretta soltanto nel suo adattatore di policy, senza cambiare il verificatore.

Misura sul corpus reale di 754 Python: censimento freddo 14,757 s, riuso
identico meno di un millisecondo; una voce conservata, confronto fatti uguale.
Diagnosi POSIX sul clone come UID995/GID985:
`/tmp/metnos-rm0008-context-candidate-r15._jjvzjol/diagnostic.log`, exit 0,
`RequiredContextRuntimeV1` in 60,952 s. Solo la funzione corretta in memoria,
filesystem in sola lettura: NON è una prova del candidato completo, non
produce attestazioni e non modifica il clone o la produzione.

Radice privata aggiornata 754:
`sha256:e39943d87dead33cba5abbc6d01fbf769fea18b5d2f77c1e5f884a53ae0d36db`.
Export pubblico nuovo `/tmp/metnos-rm0008-public-r15.0WBrlzhQ/tree`, 1732 file,
scrub PASS, radice 742
`sha256:632e3fc266c087cd42e0b9ff1ed26de448675457a506e21f0728222574f24741`;
pin pubblici applicati e verificati, nessun push/deploy. Il solo preesistente
`--help/` è escluso dalla proiezione e resta intatto.

Diagnosi preventiva preflight con le sole funzioni censimento/parser corrette
in memoria avviata sul clone: sessione `80565`, log
`/tmp/metnos-rm0008-preflight-candidate-r15.zhiw_wp0/diagnostic.log`.
Attendere l'esito, non rilanciare. Nessun tentativo CLI r15 ancora effettuato;
le bozze produttive r14 restano PENDING e non vanno eseguite.

### Ripresa r15: archivio congelato, ripristino conservativo completato

La diagnosi `80565` è terminale exit 0: `READONLY_CHECK_PASSED elapsed 26.039`,
cache una voce, un calcolo e un riuso. Non è un'attestazione del candidato completo.
Commit candidato `4758894b6aab3f28549491af6d1b98dce702d806`; archivio
`/tmp/metnos-rm0008-source-r15.tar` SHA256
`8b0e8538f4611856a8951353e7392dffd196793fa4bd12e39b4a541445ef58fa`,
source ID `sha256:2994000a421e3c65ca0f33b7e60b0e3a9b2efe7a573ace1c31789c3c2185ae1b`.

Il confronto prima del ripristino ha trovato solo il preflight r14 aggiornato
e cinque ricevute diverse esclusivamente nell'inode dei file preservati;
contenuti, owner e permessi invariati. Censimento:
`/tmp/metnos-rm0008-reuse-census-r15.bd9uqd62/census.json`, SHA256
`71548c182f8dd97d9c3663fba45585b0b730b55e4110e0d0efc54336734f262b`.

Sessione `68798` terminale exit 0, log
`/tmp/metnos-rm0008-reuse-stage-r15.zc7wfogf`: conservato il tentativo r14,
ripristinati gli input originali verificati, nessuna cancellazione/nuova chiave/NSS.
Clone sempre `rm0008-test-r13`, init host 1682695; nessun nuovo clone.
Base r15 identità `[64512,15219605,0,0,493]`, SHA256 invariato
`4c4f37b04c064dcc0e4ebf453578b62db6f00d94c002771e102d35a8fd5e124c`.
Prova ripristino guest `restore-clone-r15/result.json` SHA256
`3492626f0710790ae15b55a331eb47a7cbbfaed7736f5204e8c8d0104ea9aa36`.
Archivio r15 estratto e identificato dal verificatore canonico nel clone;
`source-r15-result.json` SHA256
`94a424c12891b70427060c723a4367d1739f25409a5cf6ea5fd35d88bd74779e`.

Adattatori offline r15 13 PASS. Un confronto test richiedeva l'aggiornamento
della sola newline aggiuntiva di copia del verificatore, nessuna semantica mutata.
Sessione `32869`: prova G6 r15 avviata, attenderla senza rilancio.
Aiutante G6 SHA256 `81fd9fcad2780129f3a4498f4eb1bdadb32e1fa6fae777d2b06cd0c3d2989874`.
Readiness/CLI/funzionale r15 ancora senza attestazioni; non eseguire bozze con PENDING.
Produzione non modificata.

### G6 e dipendenze r15 PASS, prova CLI completa avviata

G6 sessione `32869` terminale exit 0, log `/tmp/metnos-rm0008-g6-r15.vjznm7ww`;
ricevuta guest `g6c-r15/result.json` SHA256
`eff4a8b7939b38e76a77f6d3e5003ffd42a1d380f6c51b383dbb20e277efcc80`.
Base restituita identica anche per inode; ambiente managed invariato.
Readiness sessione `50195` terminale exit 0, log
`/tmp/metnos-rm0008-readiness-r15.6ct8yeyl`; ricevuta
`readiness-r15/result.json` SHA256
`8889352839f2def17f61d0eb7c23b7635e2bbbc211950a7e19fc8c6389de5de5`.
17 pacchetti nativi, 74 distribuzioni, byte dei browser, browser reale995 e bwrap PASS.

Audit produttivo soltanto lettura exit 0:
`/tmp/metnos-rm0008-audit-4758894b-r15.hoaj5oxy/audit.log`.
Sei alberi e HTTP stabili, archivio r15 verificato; 71 wheel e supplemento esatto
per le 3 mancanti. Mountpoint tomlkit assente, da predisporre prima del finale.
Non prova ancora il browser nel nuovo ambiente produttivo.

Sessione `85782`: CLI completa r15 avviata nel clone r13; attendere, NON rilanciare.
Driver `/tmp/metnos-rm0008-guest-deploy-real-r15.py` SHA256
`8d48c1c63fb832763bc8b02bab9a6bc34caffce867483b5f608d0132c0af056b`.
Aiutanti produttivi nuovi `/tmp/metnos-rm0008-*-4758894b-r15.py` solo bozze:
quattro prove finali PENDING, non installati. Barriera finale 15 test PASS,
protocollo funzionale 10 test PASS; nessuna prova sintetica scritta su disco.

Controllo aggiuntivo del browser HOST exit 0, sessione `12910`, log
`/tmp/metnos-rm0008-host-native-browser-r15.wurm60fi/probe.log`:
binario confrontato con l'archivio vincolato per impronta già verificato nel clone,
esecuzione effettiva UID995/GID985 su `data:` locale, rete privata, sistema in
sola lettura, profilo transitorio eliminato da systemd. Nessun permesso cambiato.
Questo prova binario e dipendenze native/accesso, non il nuovo ambiente Playwright
gestito produttivo. La CLI r15 resta in corso; log
`/tmp/metnos-rm0008-deploy-real-r15.jvj9dtoa/driver.log`.

### R15: transazione completa, attivazione NON superata (8/9, 12:35)

Sessione 85782 terminale exit 78, nessun rilancio. Nel clone tutti i sette
record presenti fino a PREFLIGHT_VERIFIED; stdout CLI vuoto, stderr
`birth_transition_activation_failed`. Nessuna ricevuta CLI di successo.
Alle 12:30:56 il browser ha iniziato ExecStartPre; alle 12:32:26 timeout
90 secondi dopo 29,933 secondi CPU. Il riavvio automatico systemd, non
richiesto dall'agente, ha completato il controllo e avviato il browser alle
12:34:58. HTTP attivo dal 12:30:56; readiness ancora in corso alla rilettura.
Produzione non avviata; bozze finali e sonda funzionale restano PENDING.
La prossima diagnosi deve misurare il costo dei controlli ripetuti nell'avvio,
senza ampliare timeout/risorse o fabbricare una ricevuta di successo.

### Ottimizzazione r16 in verifica — nessun nuovo cutover ancora

La prontezza r15 ha poi superato il proprio limite alle 12:36:07; la
quarantena prevista dal prodotto ha fermato HTTP e browser. Clone conservato,
nessun ripristino/rilancio finora. Il censimento offline profilato ha contato
391 milioni di chiamate; la profilazione del vero preflight installato
(`/tmp/metnos-rm0008-native-profile-r15.6okbxwuz/profile.log`, exit0)
attribuisce quasi tutto a censimento AST e due analisi identiche degli import.

Nel worktree candidato, ancora HEAD4758894b, due moduli modificati:
`contract_boundary_guard.py` e `executor_birth_admin_preflight.py`.
Analisi comandi solo per PROCESS_CALLS; taint dei file solo se la chiamata
può leggere/scrivere. Estrazione della sola analisi sintattica degli import
in cache a singola voce percorsi+byte esatti, risoluzione filesystem ancora
viva a ogni osservazione. Aumento netto del prodotto 9 righe; nessuna
attestazione, controllo vivo, autorizzazione o durata esclusa/ampliata.
Nuova suite `test_executor_birth_import_analysis_cache.py`: sei prove.
Verifica estesa:476PASS3SKIP, 27,57s, ambiente `/opt/metnos/.venv/bin/python`.
Inventario rigenerato:466voci identiche, nessuna aggiunta/rimossa/cambiata;
solo nuova radice sorgente754
`sha256:5588a48f67d6fb01aebef793dd9a6da3617db583856152133da5095c07dae3ce`.

Confronto sul medesimo stato autentico del clone, filesystem protetto,
sole tre funzioni ottimizzate in memoria, exit0:
`/tmp/metnos-rm0008-native-comparison-r16.vxrwozmx/comparison.log`.
Originale26,246s, ottimizzato18,108s (1,449x); entrambi PASS e stato
autenticato/epoca identici. Non è prova del nuovo candidato completo.
Documenti IT/EN e ADR0224 aggiornati;97HTMLvalidi. Export pubblico r16
`/tmp/metnos-rm0008-public-r16.7c7TySEK/tree` in verifica.
Prossimo: pin pubblico, congelamento, conservazione esatta del r15 e prova
completa r16 sullo stesso clone r13. Le bozze produttive r15 restano negate.

### R16 congelato e clone ripristinato (8/9, 13:04)

Commit `46f1e408cf6363900e1b7b2390c2d75920b07561`, worktree dedicato
`/tmp/metnos-rm0008-legacy-catalog-fix`; nessuna modifica a `/opt/metnos`.
Archivio `/tmp/metnos-rm0008-source-r16.tar`, 3258 membri / 2998 file,
49786880 byte, SHA256 `b0d90febda03bc95e0f5ab28146c52c539cad14e18c3010cc185ccc9b38160f3`.
Source ID `sha256:d6e04630a9334379fed683e04149970e89f6cb3dd3991666d91683d9f2ea8348`.
Inventario SHA256 `71c757c960eb3c13cfcaa68ad978335cb7d33006d5de6e0ff7425b08e6a06928`.
Esportazione pubblica742 verificata, radice
`sha256:4dfd2ededc853fd88935cee4342d0ad0ac2bb4bfa5bd86c0fe3269f8330860a7`;
pin e inventario dell'albero esportato aggiornati. Test pubblicatore6PASS.

Il primo censimento in sola lettura ha rifiutato il namespace aggiuntivo
creato dalle protezioni systemd; ripetuto correttamente nel namespace nativo,
senza alterare il verificatore. 42 righe: soltanto il preflight esatto r15 e
cinque inode nelle ricevute differiscono dal precedente censimento r14.
Ripristino r16 completato, sessione70179 exit0, log
`/tmp/metnos-rm0008-reuse-stage-r16.pkr3lt2i`.
Tentativo r15 conservato in `reuse-preparation-r16`, `restore-clone-r16/preserved`
e `/run/rm0008-lab-preserved-r15-startup`; nessuna cancellazione.
Nuova base identità `[64512,15349309,0,0,493]`, SHA invariato `4c4f37b04c064dcc0e4ebf453578b62db6f00d94c002771e102d35a8fd5e124c`.
Ricevuta `restore-clone-r16/result.json` SHA256 `3492626f0710790ae15b55a331eb47a7cbbfaed7736f5204e8c8d0104ea9aa36`;
`source-r16-result.json` SHA256 `8f19f8f4b933c1e363c72eeb1f9957632e395814ec40aa3118b8be6c49ce917c`.

G6 r16 avviato una sola volta: sessione69213, log
`/tmp/metnos-rm0008-g6-r16.48ekh_oc`. Non rilanciare; leggere la sessione.
Aiutante SHA256 `f866e677eabd3bd934fc127cfdb4352cca1c0f94b1c6793744530d73ff943da9`.
Adattatori13PASS, finale produttivo15PASS, sonda funzionale10PASS.
Nel confronto del test archivio aggiornati solo i conteggi esatti per il nuovo
file di regressione; nessuna condizione rimossa.
Bozze `/tmp/metnos-rm0008-*-46f1e408-r16.py` non installate e ancora PENDING
per tutte le prove finali: nessuna autorizzazione produttiva deriva dai test.

### R16: G6 e dipendenze superati; CLI reale in corso (8/9, 13:17)

G6 sessione69213 exit0: ricevuta effettiva `g6c-r16/result.json`, SHA256
`c7a3acfa31d4c99e796fdc6f93d7a856901bb877c2629c677f2872ab5021d68b`.
Readiness sessione63539 exit0: `readiness-r16/result.json`, SHA256
`8c5b6d418d137ee15b02c5f0b1d27ea538671c5007895788b086a8504495086e`.
Entrambe legate a source ID r16 e namespace nativi del clone r13.

CLI completa avviata alle13:09:13CEST, sessione8300 ancora viva alla rilettura;
unità `rm0008-guest-deploy-r16.service` active/running. Log host root-private
`/tmp/metnos-rm0008-deploy-real-r16.snygg_vs/driver.log`; output effettivi
guest `/opt/rm0008-lab/deploy-real-r16/`. Nessun exit-code/result ancora.
NON rilanciare, resettare o dedurre successo da ActiveState/Result provvisori.

Audit host readonly sessione88327 exit0, log
`/tmp/metnos-rm0008-audit-46f1e408-r16.5uo6_h58/audit.log`:
baseline e impostazioni HTTP stabili; archivio esatto2998file/3258membri;
71wheel presenti, mancano solo greenlet3.5.5/playwright1.61.0/pyee13.0.1,
già disponibili nel supplemento verificato. Mountpoint tomlkit ancora assente.
Nessuna preparazione/cutover produttivo eseguito; pin CLI/funzionale ancora
PENDING. Il vecchio stato failed di stack-ready non prova un nuovo fallimento:
la CLI r16 non ha ancora emesso un esito e i servizi non sono ancora avviati.

### R16 fallito; ottimizzazione runtime r17 misurata (8/9, 13:53)

La CLI r16 è TERMINALE: exit78, stderr `birth_transition_activation_failed`,
nessun result.json. Stack-ready è andato in timeout alle13:27:49; HTTP e
Playwright erano partiti, poi la quarantena li ha fermati. Non rilanciare
la sessione8300 né dedurre successo dal Result=success dell'aiutante systemd.
Il profilo diagnostico r16 è terminato dopo SIGINT per raccogliere le misure;
unità inattiva/MainPID0 verificati, nessun processo PPID1 sopra50%CPU rilevato.
Nessuna preparazione o transizione r16 in produzione.

Il profilo ha mostrato tre analisi integrali boundary/import per ogni lettura
del catalogo servizi. Due moduli runtime del candidato ora riusano soltanto
fatti sintattici immutabili, una voce per cache, percorsi+byte+limiti esatti.
Enumerazione, letture, firme, metadati e risoluzione import restano vivi.
Confronto sul medesimo clone r13 conservato, UID995, filesystem protetto,
sole funzioni pure sostituite in memoria, exit0:
`/tmp/metnos-rm0008-runtime-comparison-r17.doo_ddym/comparison.log`.
Originale51,485s; ottimizzato freddo18,056s; successivo1,464s; stessi9servizi.
Non è ancora una prova end-to-end r17. Nessun nuovo tentativo iniziato.

27nuove prove passate; prima suite estesa461PASS4SKIP e due rifiuti attesi dei
pin sorgente vecchi. Inventario466voci identiche, nessuna classificazione
aggiunta/rimossa/modificata. Radice privata754 rivista:
`sha256:83b2e4e75991be41358c9d516c703dba3f246ab50c8e91eb03314cc599257467`.
Pin privati, inventario, IT/EN e ADR0224 aggiornati nel worktree dedicato;
HEAD ancora46f1e408, modifiche non congelate. Suite estesa in sessione79531.
Prossimo: esito test, pin pubblico/export, congelamento, censimento conservativo
esatto del r16, poi prova completa. Le bozze produttive r16 restano PENDING.
### R17 congelato; precedente conservato e base ripristinata (8/9, 14:00)

Candidato `e1a9fd2bb43bf5c6154de853bcab359a8d1d9a7d`, worktree dedicato;
solo `--help/` preesistente non tracciato. Suite752PASS4SKIP, doc/publisher21PASS,
97HTMLvalidi. Export pubblico1734file,742Python,0PII verificato in
`/tmp/metnos-rm0008-public-r17.xzlzIepN/tree`; radice pubblica
`sha256:130aaa43887a4dec9dadfc7d602b5bf06ff088edd4ac71f850315d1c3ecc62da`.
Archivio `/tmp/metnos-rm0008-source-r17.tar`,2999file/3259membri,49797120byte,
SHA256 `a5dcd7ceb6fcb6e63b697284f45ee3cf49273dc63ca1cebeb135b6245e7c520d`;
source ID `sha256:daac7363777a93188a38f2c27faaa17b5a1181148c402f6c955fe1703bac1936`;
inventario SHA256 `52b1ab68355c43a8780066a1587a1d0a36264bf84a4f2a83544fea0154321ef7`.

Censimento r16 readonly42righe: solo preflight esatto e cinque inode delle
ricevute differivano da r15. Ripristino conservativo r17 exit0 sessione75314,
log `/tmp/metnos-rm0008-reuse-stage-r17.dv5vy3s3`. Tentativo r16 conservato in
`reuse-preparation-r17`, `restore-clone-r17/preserved` e
`/run/rm0008-lab-preserved-r16-startup`; nessuna cancellazione.
Base identità `[64512,15478908,0,0,493]`, SHA256 invariato
`4c4f37b04c064dcc0e4ebf453578b62db6f00d94c002771e102d35a8fd5e124c`.
Ricevuta effettiva `restore-clone-r17/result.json` SHA256
`3492626f0710790ae15b55a331eb47a7cbbfaed7736f5204e8c8d0104ea9aa36`;
`source-r17-result.json` SHA256
`71329330a42f288264df1d69e37db7ee77847343894083c17906aa9a1b0b1523`.
Adattatori13PASS. G6 r17 pronto/avviato con aiutante SHA256
`55fbfb2bdb3eac5abf9462788512f897d7ce1fbc44c3af226e85811ce04371b5`.
Le prove CLI/funzionale restano mancanti: nessuna azione produttiva autorizzata
dal solo confronto dei tempi o dai test locali.

### R17: G6/readiness passati, CLI completa avviata (8/9, 14:11)

Ricevute effettive r17: G6 SHA256
`71dbfc3eb5f19a6a7e80079e32fbf678ba81e3f8ecbba345fa21566afbc87eff`;
readiness SHA256
`6434b83b68e47b82a4e385e00ab3cdd983c5328b72c641b779c2bd5a20966475`.
CLI completa in sessione56041, log
`/tmp/metnos-rm0008-deploy-real-r17.ho5hap7r/driver.log`; aiutante SHA256
`3d6400b79acc6e7daf5f05b122116ca9fff081e0500f4a369afc14539972b77e`.
NON rilanciare: verificare sessione e ricevuta reale. Adattatori13PASS,
finale15PASS, funzionale10PASS. Corretti solo conteggi esatti e newline nel
confronto della bozza produttiva con il riferimento storico.

Audit host r17 readonly sessione56632 exit1: sei alberi predecessore uguali,
ma `exact legacy unit supplement differs`. Log
`/tmp/metnos-rm0008-audit-e1a9fd2b-r17.o904gpae/audit.log`.
Nessuna scrittura produttiva: identificare la differenza esatta senza
aggiornare ciecamente la baseline o indebolire il controllo.

Differenza isolata: solo `system.mtime_ns`, cioè la directory condivisa
`/etc/systemd/system`, da1788735186169419686 a1788868771734222554;
tutti i file/permessi/impronte e i metadati delle unità Metnos sono uguali.
La bozza finale ora esclude soltanto mtime_ns dei due contenitori condivisi
system/user dal confronto storico, non da quello di stabilità durante la
preparazione. Nessuna baseline riscritta né timestamp riparato.
16test PASS includono ogni altro campo e i percorsi figli come rifiuti.
Nuovo audit readonly sessione83210 exit0, log
`/tmp/metnos-rm0008-audit-e1a9fd2b-r17.pn_eibw_/audit.log`:
baseline/HTTP stabili,2999file/3259membri,71+3wheel, mountpointtomlkit assente.
Bozza finale SHA256
`143590840625b4c2cda3dac43a923e5e110ee191c2c4a6c06b7eef9a002b2d33`;
pin CLI/funzionale ancora PENDING. Nessuna mutazione produttiva.

### R17 fallito su avvio LRE; correzione minima e passaggio diretto autorizzato (8/9)

La sessione56041 è TERMINALE exit78: `birth_transition_activation_failed`.
Il controllo finale del clone passa HTTP, browser, catalogo122/122 e gli altri
componenti; falliscono soltanto il worker durevole e la sua salute. Il worker
supera ExecStartPre ma termina prima del proprio main con exit24; tre ripartenze
automatiche systemd non risolvono. Stack-ready termina alle14:18:06 e la
quarantena ferma HTTP/browser. Nessuna ricevuta CLI positiva è stata prodotta.
Non rilanciare r17. Prove e residuo del clone restano conservati.

Riproduzione locale indipendente: dei23 moduli Python dichiarati, soltanto
`durable_workloads.service` non si risolve dalla directory firmata. La ricetta
deve indicare `@installation_root@/runtime`, non la radice. Correzione di una
riga, impronta della ricetta amministrativa riallineata, prova generale sui23
moduli e prova negativa del vecchio percorso rifirmato; nessuna modifica al
motore LRE, alla restrizione sys.path o ai controlli di prontezza.

Roberto autorizza esplicitamente il passaggio diretto in esercizio: «puo essere
ambiente di prova. se si arresta si aggiusta in ambiente di esercizio. vai
avanti». Il prossimo candidato r18 NON sarà dichiarato certificato sul clone.
L'autorizzazione amministrativa documenterà la scelta e le prove locali reali;
restano invariati baseline, conservazione delle impostazioni, dipendenze
bloccate, CLI di prodotto, catena firmata e verifica finale reale.
Non fabbricare le due ricevute r17 mancanti né riutilizzarle come prove r18.

### R18 congelato e avvio produttivo autorizzato (8/9, 14:44 circa)

Commit `b715a765a0f664d1c2c660e5d48e5f6abada0169`; pacchetto
`/tmp/metnos-rm0008-source-r18.tar` SHA256
`c88dd868fcbd2f4523fd9d99e523f18abe1a0dc194df90da49b3d8f6b7de041c`,
source ID `sha256:157c75be4e059f304e41abc57414445b75218fe7c8d1f58ce7a3430a68c68637`,
2999file/3259membri. Radice privata754
`sha256:2a6c978928a62f8f113d8e13c3664b31b6341df379d760a4256063fdb6256e3b`;
pubblica742 `sha256:44242641f4a4b53a413de311dff91a62b808d3ffc8094934ea057b9f7c7f5865`.
717test PASS,19skip espliciti; XML effettivo
`/tmp/metnos-rm0008-r18-portable-final.xml`,SHA256
`5cb98a2793f82929af4aa1efc0dc417c6e2fb2b509e9aec0a437943764bdf1df`.
97HTMLvalidi. Correzione funzionale una riga; aggiornate impronte e prove.

Audit host readonly68855 exit0; log
`/tmp/metnos-rm0008-audit-b715a765-r18.cufzp485/audit.log`.
Sette prove del percorso amministrativo passano in unità root con filesystem
protetto; tutte le precedenti funzioni di mutazione, baseline e verifica sono
AST-identiche al r17 dopo il solo riallineamento delle identità. Cambia soltanto
il requisito amministrativo delle quattro ricevute clone, sostituito dalla
scelta esplicita dell'operatore e dall'XML locale effettivo. Nessun controllo
del prodotto è disattivato. Autorizzazione SHA256
`02f3453ecdf95218d1dfa81e971e1ac81690292b99108c033b2da93f94ef966b`.

SESSIONE PRODUTTIVA VIVA: **67038**. NON RILANCIARE.
Bundle `/var/lib/metnos-admin/rm0008-final-b715a765`, launcherSHA256
`87dc401b82224b3ddcfb5dc11ff65a60d3d09070bac30aa6e235392710578cea`.
Unità supervisore `rm0008-operator-b715a765-r18.service`, unità CLI
`rm0008-deploy-b715a765-r18.service`. Log supervisore
`/tmp/metnos-rm0008-live-r18.s9mlullc/operator.log`; output CLI nel `run/`
del bundle. Prove e scelta conservate root-private in
`/var/lib/metnos-admin/rm0008-live-r18-evidence`.
Non dichiarare successo finché CLI, verifica durevole e turno reale non passano.

### R18: PASSAGGIO PRODUTTIVO RIUSCITO (8/9, 14:48 circa)

Sessione67038 TERMINATA exit0: `RM0008_CUTOVER_VERIFIED source=b715a765`.
La CLI ha restituito `PREFLIGHT_VERIFIED`; il verificatore ha riletto la catena
firmata, i sette record, la distribuzione e il legame con la richiesta.
`run/result.json` SHA256
`3e507091393b0b0217437529037b84c274644c759162c5436bf2d2c82e3d893d`.
closed_build_id `sha256:0f2354b537edcc8e790bd8f53d2cf245d290e24b9dd1dc7545391b97af44b862`;
cutover_id `sha256:4fbdd1fdc283edd6fb895081c43e2559c25d792cc03e86044d62e7de505271ca`;
request_id `sha256:59f0a756bbf65e46f34f5bcb4edafeff44c1788410ab1123ccbc7a1614e0cf26`.

HTTP PID1951450, LRE PID1951455, Playwright PID1952072 attivi;
stack-ready active/exited con exit0. LRE registra alle14:46:35
`durable_worker_start enabled=True`: in esercizio è abilitato, a differenza
del clone. Nessun cambiamento al suo interruttore è stato eseguito.
Originali HTTP conservati nel checkpoint del bundle; nessuna cancellazione.

Sonda funzionale UNA richiesta dell'ora avviata in sessione22050.
Aiutante root400 `/var/lib/metnos-admin/rm0008-functional-probe-b715a765-r18.py`,
SHA256 `296b1d226452725d5efb7b7e5d8f13a0c96faf25cc432c3d901519da79ae8eb0`.
Risultati `/var/lib/metnos-admin/rm0008-functional-b715a765-r18/`.
Dieci prove offline della sonda passate; non dedurre l'esito del turno reale.

Sonda22050 TERMINATA exit0: una sola POST, `get_now` riuscito, risultato
temporale confrontato con l'istante della richiesta e con la risposta, registro
persistente verificato, salute finale vera e medesimo PID HTTP.
Ricevuta funzionale SHA256
`edf595ae2df4f7c398bd2048a4d38f4886edc3abd0894991fbeb1c326b59862c`.
Non è una prova di inferenza LLM; questa distinzione resta nella ricevuta.
Il passaggio RM-0008 è riuscito; non riaprire reset, clone o transizione.
