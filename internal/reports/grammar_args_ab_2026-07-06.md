# Report CP5 — spike grammar-on-args: MISURA e cancello (6/7/2026)

> Spike ADR 0177 T2/M4 «grammar-on-args: quanti guard si possono spegnere». Misurato onestamente (§8.3). **Esito: risultato NEGATIVO per grammar-args come riduttore di guard — ma lo spike ha fatto il suo lavoro e indica il lever giusto.**

## Cosa è stato costruito (CP5.1-5.4, tutto corretto e testato)
- `build_framework_grammar_typed` (grammar-on-args): vincola gli args allo schema (enum→alternation, testo→jsonStr), union discriminata tool↔args, runtime_resolved esclusi, fallback totale. **Validato LIVE**: llama-server accetta e RISPETTA l'enum (`sort:"mtime"`/`"size"`). 9 test.
- Flag `METNOS_PROPOSER_GRAMMAR_ARGS` (default OFF), un solo punto copre Simple+Metis+v3.
- Contatore guard-fire per-guard (`METNOS_GUARD_FIRE_COUNT`).
- 14 commit, suite verde, tree pulito.

## La misura (bench A/B su proposer diretto, no cache/exec + probe avversario)

Corpus 15 query args-pesanti, proposer chiamato direttamente (isolato da cache/describe):

| | plans | enum_invalid | guard_fire | lat_med |
|---|---|---|---|---|
| **A (grammar-args OFF)** | 15/15 | **0** | **6** | 10.31s |
| **B (grammar-args ON)** | 15/15 | **0** | **6** | 10.02s |

guard_fire per-guard IDENTICO: `fill_clause_args`×5, `align_framework_objects`×1.

**Probe AVVERSARIO** (query che chiedono valori-enum FUORI dominio — `ordina per RILEVANZA`, `comprimi in RAR`, `encoding KOI8`): baseline `enum_invalid=0` — il proposer mappa `RAR`→`zip`, `KOI8`→`latin-1`, valori sempre validi.

## Conclusione onesta (§8.3, no gaming)

**grammar-args NON dà beneficio misurabile su questo modello.** Due fatti:
1. **Il proposer rispetta già gli enum** senza grammar, anche avversarialmente — l'hint soft `enums=[...]` nel prompt (`proposer._render_tool_pool`) è sufficiente sul Qwen 3.6 locale. La grammar-args previene un errore che **non accade**.
2. **I guard che sparano NON sono raggiungibili dalla grammar**: `fill_clause_args` (derivazione di pattern/path dalla clausola) e `align_framework_objects` (struttura) toccano testo-libero e struttura, non valori-enum. La grammar-args, per costruzione, non li riduce.

È la **stessa lezione di grammar-on-verbs** (accantonata ADR 0174 D4): il vincolo GBNF affronta un non-problema per questo modello.

## Ma lo spike ha fatto il suo lavoro — e indica il lever GIUSTO

Il valore dello spike NON è «grammar-args funziona» ma «grammar-args MISURATO non è il lever, e ora sappiamo qual è»:
- Il guard caldo è **`fill_clause_args` (5/6 fire)** = derivazione clausola→args. È esattamente ciò che il **clause-derive AUTORITATIVO** (FASE 2 dell'architettura provenienza, scoperta parallela) sussume per costruzione.
- Il bench è **evidenza empirica** che il debito-guard sta nella derivazione/struttura, non negli enum → conferma che la direzione giusta è l'**architettura di proprietà args** (`spec_args_provenance_architecture.md`), NON la grammar.
- L'infrastruttura CP5 resta **capitale**: `arg_provenance.py` (FASE 0, già fatto), il fire-counter (osservabilità permanente), e la grammar-args stessa come **assicurazione dormiente** (flag OFF, testata) per condizioni future avverse (modello più debole, spazi-enum più grandi, contesa server — dove ADR 0156/0174 documentano che i vincoli inline cedono).

## CANCELLO — raccomandazione a Roberto
1. **NON promuovere grammar-args a default ON**: nessun beneficio misurato, piccolo costo di latenza/complessità. Tenerla come flag dormiente (assicurazione, costo zero).
2. **Il vero taglio del debito-guard è l'architettura di proprietà args** (che tu hai già scelto con «mira alto»): FASE 0 fatta (classificatore + mappa: runtime 7%/clause 21%/semantic 72% + 30 config-args senza marker), FASE 2 (clause-derive autoritativo) sussume `fill_clause_args` — il guard che il bench mostra caldo. Protetta dall'oracolo di equivalenza per non toccare errore=0.
3. **Prossimo passo suggerito**: procedere con l'architettura provenienza (FASE 1 registro → FASE 2 clause-derive) come «un lavoro impegnativo una volta sola». grammar-args è pronta e dormiente se mai servisse.

## Nota laterale
Il bench ha ri-confermato il warning pre-esistente `routing_pool OVERSIZED` (30 candidati vs target 12) su (find,files) — filtro poco discriminante, routing impreciso. Aperto, ortogonale a CP5.
