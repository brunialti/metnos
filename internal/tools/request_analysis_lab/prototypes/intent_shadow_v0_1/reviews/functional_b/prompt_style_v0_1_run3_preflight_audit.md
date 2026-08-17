# Audit indipendente pre-esecuzione — prompt_style_v0_1 / RUN3

- Data: 2026-08-13
- Revisore: `reviewer_b_independent`
- Perimetro: 44 controlli fissati prima dei byte finali; lettura e prove offline, salvo questo unico referto richiesto.
- Esito: **READY — 44 PASS, 0 FAIL**.
- Stato auditato: disarmato; autorizzazione e artefatti run assenti; nessun POST, rete, GPU o accesso gold eseguito dall'audit.

## Byte finali autorevoli

| Artefatto | SHA-256 |
|---|---|
| prompt-style package freeze | `4bc9b7322f3c78111a0d86abee56d64c650d5b75cc4bad59b65cb0bb08f9e065` |
| matrice `PYTHONHASHSEED` | `d776d3261f9e01b955be386a0a9329d446119fb217495749766319925fcdfac9` |
| protocollo RUN3 freeze | `2dec09e3edce00b13e833c908df389a4425ff1c00db345ff26a014e41212dcad` |
| prompt S0 / candidate v0.3 | `3251da0495ca72a5e138f2ca9693eb3d128ff2e4648ccef474cb0a49d7288d5c` |
| schema candidate | `f40a9c974bcce773247ea203f5a4d63c14a0579ddefddf6bcc43430d0ea98f10` |

Il freeze RUN3 enumera e verifica anche sorgente e report della matrice seed. Build check, verifier e preflight sono stati rieseguiti sui byte sopra.

## Checklist finita

| N. | Controllo | Esito | Evidenza sintetica |
|---:|---|:---:|---|
| 1 | Namespace RUN3 nuovo, nessuna collisione | PASS | `live/run3_style/` è distinto; auth e nomi di consumo/run assenti. |
| 2 | Lineage RUN2 → prompt-style → RUN3 chiusa | PASS | Protocollo e freeze legano batch/evaluation RUN2, candidate, style package, registry e autorità host. |
| 3 | Quattro bracci esatti | PASS | Solo `A_SYSTEM_CURRENT`, `S0_CURRENT`, `S1_METNOS_SHORT`, `S2_PROCEDURAL`; 158 record ciascuno. |
| 4 | Inventario autorità legato al CURRENT congelato | PASS | Inventario deriva dal prompt v0.3 SHA `3251da…8d5c` e ne verifica confini e statement. |
| 5 | Statement CURRENT coperti esattamente una volta | PASS | 20/20 posizioni d'istruzione/template/riepilogo mappate senza sovrapposizioni. |
| 6 | Rule ID e label univoci | PASS | Sei regole canoniche: root, operations, edges, ports, barrier, output. |
| 7 | Provenance statement ↔ rule completa e invertibile | PASS | Ogni statement ha rule ID validi; tutte le regole risultano coperte. |
| 8 | Nessun vincolo CURRENT omesso | PASS | Confronto umano e strutturale dell'inventario non trova perdita semantica. |
| 9 | Nessun vincolo semantico aggiunto | PASS | Contratto dichiara e verifica `new_semantic_rules: 0`. |
| 10 | Nessuna duplicazione di regole in S1 | PASS | Ogni constraint canonico compare una volta, nell'ordine comune. |
| 11 | Nessuna duplicazione di regole in S2 | PASS | Ogni constraint canonico compare una volta, nell'ordine comune. |
| 12 | Ordine canonico identico S1/S2 | PASS | Sei constraint nello stesso ordine; cambia soltanto la forma espositiva. |
| 13 | S0 byte-identico al prompt candidate v0.3 | PASS | SHA comune `3251da…8d5c`; test e freeze lo impongono. |
| 14 | A_SYSTEM request byte-identiche a RUN2-A | PASS | **158/158** request canoniche esatte. |
| 15 | S0 request byte-identiche a RUN2-B | PASS | **158/158** request canoniche esatte. |
| 16 | Adapter ed estrazioni dei due anchor esatti | PASS | A usa adapter/versione RUN2-A, S0 usa RUN2-B; matrice e due conferme fresche danno **158/158 + 158/158** full-extraction exact. |
| 17 | Root template condivisi senza variazione | PASS | S0/S1/S2 riusano byte e ordine dei tre template canonici. |
| 18 | Registry authority/data condivisi | PASS | Tail registry byte-identica e hash-bound; nessuna copia alterata per stile. |
| 19 | Schema e response contract invariati | PASS | S0/S1/S2 usano lo schema byte-identico e `json_schema strict`; A riusa esattamente la request RUN2-A. |
| 20 | Core API, tipi, canonical, validator e compiler invariati | PASS | Tutti riusati dal candidato congelato e inclusi nei build sources. |
| 21 | Adapter one-call, zero critic/repair | PASS | Entrambi gli adapter RUN2 sono preservati; nessuna seconda chiamata o percorso di correzione. |
| 22 | Backend, modello, profilo e limiti invariati | PASS | Snapshot e autorità assolute sono hash-bound; temp 0, seed modello 42, max 4000, retry 0, serialità. |
| 23 | S1 conforme allo stile breve ADR0027 | PASS | Constraint imperativi compatti, senza rationale o esempi lunghi. |
| 24 | S2 procedurale equivalente | PASS | Stesse frasi e ordine di S1, numerate; nessuna regola aggiuntiva. |
| 25 | Nessuna nuova coverage/root/reason policy | PASS | Assenti coverage-before-root, whole-compound e nuova precedence dei reason. |
| 26 | Autorità `outside_registry` invariata | PASS | Nessuna nuova regola whole-compound o eccezione per il banco. |
| 27 | Distinzione runtime-argument invariata | PASS | Nessuna nuova semantica o ramo nel prompt/compiler. |
| 28 | Minimalità operations invariata | PASS | Solo operazioni richieste; nessuna enumerazione di alternative/catalogo. |
| 29 | Regola `from` minima e scoped invariata | PASS | Solo dipendenza reale, precedente e visibile; ordinal 0 senza `from`; self/forward vietati. |
| 30 | Semantica barrier invariata | PASS | Body approvato, rejected vuoto compiler-derived; niente outcome/continuation model-facing. |
| 31 | Universalità registry-derived | PASS | Test synthetic-renamed registry verde; nessuna route o soluzione speciale. |
| 32 | i18n e Unicode fail-closed | PASS | Un percorso neutro BCP47; tag regionali/grandfathered validi invariati, malformed/confusabili respinti pre-send. |
| 33 | Anti-leakage e zero hardcoding soluzione | PASS | Nessuna query/frammento, ID opaco, hash, route o indice del banco nella logica; conteggi solo protocollo/test. |
| 34 | Metriche statiche oneste | PASS | S0 `22868/114/3167`, S1 `22301/105/3110`, S2 `22323/105/3122` per byte/linee/proxy whitespace, ricalcolate. |
| 35 | Freeze package completo e deterministico | PASS | Freeze style verde e base-bound; nessuna cache Python residua. |
| 36 | Suite package/core/roundtrip | PASS | Suite finale complessiva **34/34 PASS**; roundtrip oracle **124/124** incluso. |
| 37 | Solo laboratorio, nessuna modifica runtime production | PASS | Nuovo codice sotto `request_analysis_lab`; i `dict` restano lab/JSON/test, nessun nuovo modello dati production. |
| 38 | Panel/query/backend/profile esatti RUN2 | PASS | Stessi 158 casi e pannelli 120/4/34; entrambi gli anchor sono snapshot RUN2 effettivi. |
| 39 | Manifest 632 e ciclo a quattro posizioni | PASS | 158×4; ABCD/BCDA/CDAB/DABC = 40/40/39/39; ogni arm occupa le posizioni 40/40/39/39 e conserva pannelli separati. |
| 40 | Evaluator v0.3 e colonne invariati | PASS | Riuso diretto di row/aggregate RUN2; critical/safety, typed e legacy riportati separatamente per arm. |
| 41 | Doppio anchor zero-tolerance | PASS | A→A e S0→B su raw hash, full extraction, canonical120, typed4 e legacy34; mapping/aggregate scambiati falliscono. Qualunque drift conserva i risultati ma invalida ogni attribuzione. |
| 42 | Sei pairwise simmetriche | PASS | Tutte le coppie unordered; delta bidirezionali, safety e pass speculari, soglia esatta ≥3, nessun ±1, combined score `None`, nessun global winner. |
| 43 | Live freeze, mutation, fake/replay e safety operativa | PASS | Fake/replay **632/632**; 7 mutation test; single-use, primo POST, zero retry, raw/journal/checkpoint/seal, partial fail-closed e gold isolation; auth/run assenti. |
| 44 | Semplicità | PASS | Un ingresso live sigillato, fake separato, nessun framework capability; implementazione lineare e confinata al laboratorio. |

## Matrice `PYTHONHASHSEED` e provenienza

Il range **0–255 inclusivo** e il tie-break **minimo seed globalmente esatto** erano fissati prima della selezione. La matrice finale contiene 256 righe:

- 158 seed sono full-extraction exact sull'intero anchor A **e** sull'intero anchor B; il minimo è **0**;
- i 98 seed non esatti differiscono esclusivamente sul path diagnostico booleano `/adapter_metadata/implicit_actions_ignored`; status e documento semantico restano 158/158 su entrambi gli anchor;
- il seed 0 è confermato due volte in processi freschi: full extraction 158+158, request 158+158 e hash sentinel identico;
- seed errato e seed mancante falliscono prima del runner; protocollo, freeze, verifier e preflight legano seed, sentinel e report;
- report e sorgente non aprono oracle/expected, non usano rete o GPU. Un probe provvisorio interrotto è stato scartato e non appartiene alla catena autorevole; soltanto il report finale SHA `d776d326…ac9` è congelato.

## Prove finali

- Suite: **34/34 PASS** (`12` prompt-style package, `7` mutation, `15` live), incluso roundtrip **124/124** e fake/replay **632/632**.
- Build/check RUN3: **0 errori**.
- Verifier disarmato: **0 errori**, conteggi 158 query / 4 arms / 632 request / pannelli 120+4+34.
- Preflight: `disarmed_ready_for_two_audits`, `python_hash_seed: 0`, guard libera, rete/GPU false.
- Prove indipendenti: request A 158/158, S0 158/158, diff prompt-only S0/S1/S2 474/474; sei coppie e 36 casi direzionali/safety/boundary/anchor verdi; wrong/missing seed respinti.

## Verdetto, limite di attribuzione e autorizzazione

**READY 44/44** significa che questi byte possono essere legati a un'eventuale autorizzazione RUN3 e sottoposti al successivo preflight armato. Questo referto non arma il runner e non autorizza o esegue inferenza.

La qualifica **style-only** vale causalmente soltanto per i confronti tra `S0_CURRENT`, `S1_METNOS_SHORT` e `S2_PROCEDURAL`, che condividono schema, adapter e stack e differiscono solo nel system prompt. Le tre coppie che coinvolgono `A_SYSTEM_CURRENT` sono confronti **full-stack** descrittivi contro il sistema corrente, non una misura isolata dello stile. L'evaluator calcola simmetricamente tutte e sei le coppie, ma nessun risultato A↔S può essere interpretato come effetto causale del solo stile. Inoltre nessuna coppia è attribuibile se anche uno solo dei due anchor RUN2 devia.

READY non è una dichiarazione di accuratezza semantica né un vincitore: i risultati esistono soltanto dopo 632 risposte complete, sigillo, gate pre-gold ed evaluation separata.
