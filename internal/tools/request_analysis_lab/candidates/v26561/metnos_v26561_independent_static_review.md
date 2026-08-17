# Review infrastrutturale indipendente V26.5.6.1

Data: 9 agosto 2026.

Verdetto: **STATIC BLOCK**. Nessun preflight, gate, trasporto o live è
autorizzato.

## Evidenza fresca offline

| Controllo | Esito |
|---|---:|
| self-test autore | 34/34 PASS |
| `--verify-freeze` con `/usr/bin/python3 -I -B` | PASS |
| pin freeze verificati | 20/20 |
| tree jsonschema/regex | 38/38 + 11/11 |
| inventory non-stdlib | 51/51 |
| byte semantici parent | 8/8 identici |
| `__pycache__`/`.pyc` nel candidato | 0 |
| rete/server/modello reali | 0/0/0 |

I fix locali sono reali: tre subprocess di diniego (live, preflight ed
evaluator) terminano 2 con una sola diagnostica JSON, zero traceback, zero
output e nessuna variazione di `/var/crash`; il transport nativo non contiene
proxy, nega una 302 finta con un solo open e accetta soltanto `127.0.0.1`/`::1`.
L'anchor apre il parent prima del gate, rifiuta componenti symlink e conserva
il dirfd; il contatore batch coincide con `json.dumps` in 1.208 differenziali e
rifiuta 8 MB prima del dump.

Un batch indipendente, prodotto con 35 open finti, è query-free, viene
valutato offline e conserva il proprio SHA. Prima del gold vengono respinti
`expanded_frame={}`, una normal form full-schema-valid ma semanticamente
invalida (`clause_ids`) e una chiave result extra. Il nuovo exec carica solo i
byte validator/registry dai pin fissi: path/hash alterati e registry injection
errata sono respinti; il validator non contiene sink file, rete o dynamic exec.

## Blocker riproducibili

1. **I gate operativi sono logicamente irraggiungibili.** Sia
   `_verify_preflight_gate_snapshot` sia `_verify_external_gate_snapshot`
   invocano `_verify_author_checkpoint`; quest'ultimo impone con `lstat` che
   preflight gate ed external gate siano assenti. Se il gate è assente, la
   successiva apertura fallisce; se è presente, fallisce prima il checkpoint.
   Il self-test del producer aggira la contraddizione sostituendo l'intero
   verifier. Separare la verifica immutabile dei byte dalla sola asserzione
   author-time di assenza dei gate.
2. **La closure dei contatori pre-gold resta incompleta.** Quattro mutazioni
   indipendenti superano `_validate_batch_before_gold` e aprono
   `source_controls34`: contatore socket della diagnostica diverso dal record,
   contatore socket preflight diverso dal summary,
   `summary.total_transport_attempts=999` e latenza summary di tipo stringa.
   Vanno chiusi e riconciliati diagnostica↔record, preflight↔summary, totale
   trasporti e quattro latenze derivate prima di ogni `GOLD_IDENTITIES`.
3. **Il contesto del nuovo exec non è congelato dall'evaluator.** A differenza
   del runner, l'evaluator non verifica executable, Python 3.12.3, `-I`, `-B`
   o Unicode 15 prima di importare regex/jsonschema ed eseguire il validator.
   Un probe `/usr/bin/python3 -B` con `isolated=0` carica con successo il
   validator. Inoltre controlla i file elencati dal dependency manifest, ma
   non ricalcola la membership dei due tree. Applicare lo stesso context e
   tree verifier del runner prima del compile/exec.

Il difetto 2 è fail-open rispetto alla regola “batch integralmente validato
prima del gold”; il difetto 1 rende impossibile usare i gate che dovrebbero
autorizzare i due percorsi. La review leggibile a macchina è
`metnos_v26561_independent_static_review.json`.
