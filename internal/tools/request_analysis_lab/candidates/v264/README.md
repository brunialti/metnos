# V26.4 typed relational freeze — K1 CONSUMATO, NOT_EVALUATED

Stato: **freeze autore pre-inferenza byte-stabile; review indipendente del
graph PASS; unico K1 tentato ma non valutato perché il sandbox ha negato il
socket localhost; gate monouso consumato; accuracy live ancora sconosciuta**.

V26.4 sostituisce il contratto V26.3 bloccato con un grafo relazionale
normalizzato. Il modello emette atomi `projection` e `dependency`, argomenti
ordinati senza tipo/ruolo duplicato, binding `bound|unknown|output|from_atom_output`
e prove compatibili con il fatto. Gli edge puntano soltanto a un atomo
precedente; ordine, aciclicità, profondità, arità, output slot, adapter,
copertura e limiti sono controllati fail-closed dal validator.

Il registro tecnico è l'unica fonte di firme, tipi, output e coercizioni. Lo
schema generico è generato dal registro e resta compatto: 14.518 byte
formattato, 7.302 byte canonico senza spazi. Il prompt non contiene esempi,
sinonimi o liste della lingua sorgente.

## Gate operativo monouso

Il gate `metnos_v264_typed_phase1_external_gate.lock.json` esiste sia nel path
esatto `/tmp` richiesto dal runner sia in questo archivio. Autorizzava soltanto
34 casi, una ripetizione, una chiamata per caso, zero retry e l'output esatto
`/tmp/metnos_v264_typed_phase1_controls_k1.json`. L'output ora esiste: il gate
è consumato e il runner deve rifiutare qualsiasi rilancio.

Il freeze riporta ancora `runtime_cutover_eligible=false`. Non esiste ancora
alcun risultato modello valutabile V26.4 in questo archivio.

### Esito del tentativo monouso

Il comando lockato è uscito zero e ha scritto 34 record, ma tutti sono
`not_evaluated/transport_or_json`. Il processo Python era confinato: una GET
`urllib` inference-free nello stesso contesto ricostruisce
`URLError(PermissionError(errno=1, Operation not permitted))`; il `curl`
autorizzato vede invece `/v1/models` sia prima sia dopo. Il POST non ha
raggiunto il server, quindi gli zeri del summary grezzo non misurano accuracy.

Artefatti:

- output grezzo byte-identico `metnos_v264_typed_phase1_controls_k1.json`,
  SHA-256
  `7fb2ac3aa3315dcc56ef5ee11296d8f94d6a6d8bb9c4d06d689adbc5421b83b3`;
- diagnosi JSON, SHA-256
  `8105a843c101476012122db8c3b42ffda10b1e1706740ad56dadccd188f1120d`;
- diagnosi MD, SHA-256
  `a16b47e024ed030736588cd3e55e0a3a5e436aa61ede692604454ee91bb51a3b`.
- design V26.4.1 fail-fast `metnos_v2641_transport_failfast_design.md`,
  SHA-256
  `7617ea44be36bd58449fd0198dec2fe3571d49c85807d15131cd6b5e44b7ab75`.

Non rilanciare V26.4 con questo gate. Il successore V26.4.1 deve fare prima
uno smoke di trasporto separatamente autorizzato, senza inferenza e nello
stesso contesto; deve inoltre fermarsi al primo errore, preservare tipo/status
e diagnostica redatta, distinguere tentativi da richieste server accettate ed
uscire non-zero su `NOT_EVALUATED`. Un eventuale nuovo live run richiede un
nuovo gate e un contesto esplicitamente unsandboxed/escalated.

Verifier e lock indipendenti:

- `metnos_v264_external_gate_verifier.py`:
  `9fec580283aa514b16e685d8f5117fd06878145ee43c4a714a6bfc8960facec5`;
- `metnos_v264_external_gate_authorization_review.md`:
  `135f153c02cadac69d9d0303622599c55e677832e2bcd2ac23acd5fdf50eea84`;
- `metnos_v264_typed_phase1_external_gate.lock.json`:
  `3d204e369e73e47713682b3f281d19416fd0d2ee439d481f77e93bea52bbfab7`;
- `metnos_v264_external_gate_verification_result.json`:
  `0a1d0336f4c949a1fddd2bd1da8c3d76549b7a2d0b5f962bf866643c6453ba87`.

Il verifier passa 23/23 mutazioni del gate e lega 18 artefatti, comprese review
MD+JSON, errata sia `/tmp` sia durevole, probe, contaminazione e tutta la
catena dell'oracolo. Non effettua chiamate server.

La review indipendente finale sui byte congelati passa il solo contratto del
grafo e autorizza quel componente a essere legato in un futuro lock/run34; non
attesta accuracy live né cutover:

- `metnos_v264_independent_graph_final_review.md`:
  `9e77d7261a144615d2329febefcfbab1d2b05c174e2c43819d149bf8d6dca35a`;
- `metnos_v264_independent_graph_final_review.json`:
  `ccbc518837af72a60e67da5e2ae445bffd17f64ff1dfba727d54fadcd2c8ac62`.

La review ha un errata tecnico obbligatorio, da legare nello stesso gate:
`metnos_v264_independent_graph_final_review.addendum.md`, SHA-256
`53254dca278c2d2da7f4ac2958502866b3758dd332f672643950c87ccffe1c4d`.
Corregge soltanto l'affermazione sulla profondità: il registro congelato può
costruire una catena depth-4 tramite l'adapter esplicito
`spatial_position_as_entity`; il verdetto PASS non cambia.

Il valore `pending_on_byte_stable_bundle` dentro l'author pre-gate è lo stato
storico al momento del freeze e non va riscritto: sarà il gate esterno, se
creato dal coordinatore, a legare gli hash delle review successive.

## Esito offline prima della review finale

- core self-test e oracle projection/gates: 69/69;
- probe indipendente: 125/125, di cui 19 controlli positivi, 68 mutazioni del
  validator, 19 controlli classifier e 4 omissioni demandate all'oracolo;
- rappresentabilità e gate dell'oracolo tipizzato: 34/34;
- richieste sintetiche multi-azione, multi-dominio, fan-out, direct+azione,
  ambiguità con lavoro invariato e supported+unsupported: valide;
- righe prompt surface/mixed: 0;
- query intere e overlap di almeno tre token: 0 su 109 + 34 + 70;
- chiamate modello/server: 0.

Il probe depth-4/diamond usa anche un adapter identity sintetico e temporaneo,
senza cambiare il registro congelato. Depth-4 è comunque rappresentabile nel
registro frozen tramite `spatial_position_as_entity`; l'addendum sopra
corregge la frase imprecisa della review. Il caso embedded unknown è positivo
per coerenza con l'oracolo tipizzato, non una mutazione da respingere.

## File byte-identici

| File | SHA-256 |
|---|---|
| `metnos_v264_typed_phase1_runner.py` | `ea3fc46b50ce25a72652214b380e251f092ada87420a3e056b67eda60b24057d` |
| `metnos_v264_typed_registry.json` | `448c058d805e141c871580253e815849b24a9999d98c505cab970fcd55d1e46f` |
| `metnos_v264_typed_phase1.schema.json` | `06452e75f0a1ec7a42c9bf87dfae3da4e1528fc07b02d9276272a1e9ff0c7fec` |
| `metnos_v264_typed_phase1.prompt.txt` | `2e60f19500b6d6193d53f483707533ca39ec1fee6a0256b18e6a96994f53406f` |
| `metnos_v264_typed_phase1_controls_frozen.json` | `1dfbc5d2a7a83308a0f414d1b2c0b395f096d034471930fbaa886627aa79d954` |
| `metnos_v264_typed_phase1_mutations_frozen.json` | `ff2c02177cab32eb8d84e6d0f714442f3161a9371f85189b6ae55589bbf34922` |
| `metnos_v264_typed_phase1_contamination_audit.json` | `6845ef5ab1a3b7f7871909a285b070c292cfe97b2cf923471ebfeee99af78ff9` |
| `metnos_v264_typed_phase1_author_pre_gate.json` | `ead754de64da5d241c6296669417ee3c332e0539591e4992fb57240aa083a2d8` |
| `metnos_v264_typed_phase1.freeze.json` | `83d3b4441d83d8f90e5eb591f9eeaf052885150e6806cd3ceefd046d948ccde6` |
| `metnos_v264_graph_invariant_review_preoutput.md` | `001f834d67f0937173123c0fab7398957d6aefd0ea3f35d62f6219382dd21b41` |
| `metnos_v264_graph_mutation_proposal.json` | `c3e6d50744237b98b6381a017cb4d7725d0b4ab01e708bb1fb36319ce624f790` |
| `metnos_v264_independent_graph_probe.py` | `9116a851241c5d1c03385a81a2eff23bb830599ea57e46abb2f6ddd1fd15e29a` |
| `metnos_v264_independent_graph_probe_result.json` | `7b0ff821783d9f5d071b4da0ccaa7f562f57afd47136cfc73c863a12663fb62c` |

Gli hash sono quelli dei file originali `/tmp`. Il README è nuovo e non fa
parte del freeze.

## Dipendenze durevoli

L'oracolo tipizzato esatto è in `../../oracles/phase1_v1/`; i cinque hash
attesi sono inclusi nel freeze. Il controllo focalizzato resta
`../../question_focus_controls_v1.json`. L'auditor e i bench storici sono in
`../replay_deps/`; la catena precedente è descritta in `../REPLAY_CHAIN.md`.

Per una futura valutazione revisionata, non ripristinare né riutilizzare il
gate monouso V26.4. Creare V26.4.1 con runner fail-fast, smoke inference-free,
nuovo freeze/review/gate e contesto live unsandboxed. Conservare questi byte
come prova che il tentativo V26.4 originale è stato consumato senza inferenza.
