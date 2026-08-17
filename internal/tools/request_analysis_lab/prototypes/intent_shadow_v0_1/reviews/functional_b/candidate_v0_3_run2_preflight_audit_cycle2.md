# Audit indipendente pre-esecuzione — candidate_v0_3 / RUN2, cycle 2

- Data: 2026-08-13
- Revisore: `reviewer_b_independent`
- Perimetro: massimo 36 controlli predefiniti; sola lettura degli artefatti canonici, salvo questo unico referto richiesto.
- Esito finale: **READY — 36 PASS, 0 FAIL**.
- Stato al momento dell'audit: RUN2 disarmato; autorizzazione assente; artefatti di consumo/esecuzione assenti; nessun POST, rete o GPU eseguito dall'audit.

## Byte finali autorevoli

| Artefatto | SHA-256 |
|---|---|
| candidate v0.2 freeze | `1004d0afa2a72567f4dbd0a0daad530ab05449d30d4cd4e57ab06c42d28f93b7` |
| prerequisito i18n v0.2.1 freeze | `8fb7190c18db3aeaae4aa98f3c9d730cae8ea57f5c015a1c68e5675df719043a` |
| candidate v0.3 freeze | `81c62394da4916ee4a2c55f8e8c028e8b589b497e9c222fd2c86a8d0b4a75a4d` |
| protocollo RUN2 freeze | `4c77fed4a9a8f481d412f2ff1fd9bcc274892ed8e553015bc2e8821eddccea31` |
| oracle freeze separato | `1eb9095890e9fd2c88b64758851de8d31670659232c32dd61fbe06ae90497fe7` |

Lo schema v0.3 è byte-identico allo schema v0.2 (`f40a9c974bcce773247ea203f5a4d63c14a0579ddefddf6bcc43430d0ea98f10`). Il prompt v0.3 è congelato a `3251da0495ca72a5e138f2ca9693eb3d128ff2e4648ccef474cb0a49d7288d5c`.

## Difetto storico e chiusura cycle 2

Sul byte precedente, ora superato (`961b257c…` / `ac3deaa2…` / `188f3d9a…`), il controllo 19 aveva trovato un blocker: il lookup dei tag grandfathered applicava `casefold()` prima di imporre ASCII, quindi `i-Klingon` e `ſgn-BE-FR` venivano accettati come tag validi.

Il byte finale applica prima il vincolo ASCII alfanumerico/trattino e solo dopo esegue lookup e normalizzazione. La riproduzione mirata conferma che `i-Klingon`, `ſgn-BE-FR`, lettere fullwidth e trattini Unicode sono respinti prima dell'invio. I 26 tag grandfathered IANA restano coperti esattamente (21 con Preferred-Value, 5 senza), i tag moderni seguono lo stesso parser v0.2 e le 158 request del carico RUN2 restano byte-identiche prima/dopo il prerequisito i18n.

## Checklist finita

| N. | Controllo | Esito | Evidenza sintetica |
|---:|---|:---:|---|
| 1 | Namespace RUN2 nuovo e senza collisioni | PASS | `live/run2/` è distinto; zero auth e zero artefatti run. |
| 2 | Lineage v0.2 → v0.2.1 → v0.3 → RUN2 chiusa | PASS | Ogni freeze lega SHA e insiemi di file del livello precedente. |
| 3 | Unica differenza inferenziale: prompt projection | PASS | 158/158 request B RUN1→RUN2 differiscono solo nel system prompt; il prerequisito i18n è 158/158 byte-invariante. |
| 4 | Schema byte-identico e legato da hash | PASS | SHA comune `f40a9c…98f10`; build e freeze lo verificano. |
| 5 | Validator invariato | PASS | Riusato dal candidate v0.2 e legato al suo hash congelato. |
| 6 | Compiler invariato | PASS | Stesso compiler v0.2, roundtrip e determinismo verdi. |
| 7 | API, tipi, canonicalizzazione e IR invariati | PASS | Tutti i file critici v0.2 sono enumerati e legati dal freeze v0.3. |
| 8 | Client/adapter strutturato invariato salvo binding versione | PASS | Arm B cambia solo request builder v0.3 e label; estrazione/compilazione restano v0.2. |
| 9 | Limiti tecnici invariati | PASS | Byte/depth/nodes/string/integer e classificazione fail-closed coincidono con RUN1. |
| 10 | Registry e projection data invariati | PASS | Stesso registry/projection SHA; nessuna rotta aggiunta o speciale. |
| 11 | Evaluator v0.3 invariato e congelato | PASS | Identità sorgente dopo sole sostituzioni meccaniche RUN1/RUN2; hash nel freeze RUN2. |
| 12 | Prompt deterministico e query-free | PASS | Ordinamento derivato dal registry; nessuna query, ID opaco o query hash del banco. |
| 13 | Tre root bilanciate, nessun default implicito | PASS | Tre template adiacenti prima del catalogo e scelta root esplicita. |
| 14 | Regola `from` completa | PASS | Step 0 senza `from`; solo dipendenza reale, precedente e visibile sullo stesso path; self/forward vietati. |
| 15 | Nessuna enumerazione del catalogo nell'output | PASS | Il prompt vieta catalogo e alternative nell'output; schema chiuso. |
| 16 | Universalità registry-derived | PASS | Nessuna rotta speciale; synthetic renamed registry guida il prompt senza cambio logico. |
| 17 | Anti-hardcoding banco | PASS | Zero query/frammenti risolutivi, ID, hash, indici o conteggi del banco nel candidato/compiler/adapter; 120/4/34 restano solo protocollo/test. |
| 18 | Prompt unico neutro e BCP47 esplicito | PASS | Nessun ramo per lingua o `it else en`; il tag normalizzato è sempre nel request path. |
| 19 | BCP47, Unicode e malformed pre-send | PASS | Cycle 2 chiude i confusabili; tabella IANA 26/26 esatta; modern tags invariati; 158/158 request invarianti. |
| 20 | `kind` first solo come serializer guidance | PASS | Entrambi gli ordini root sono validi e compilano uguali; serializzazione canonica mette `kind` per primo. |
| 21 | Freeze candidato completo e deterministico | PASS | Build check v0.2.1 e v0.3 verdi; SHA finali ricalcolati. |
| 22 | Roundtrip 124 e registry rinominato sintetico | PASS | 124/124 esatti; test di rinomina registry verde. |
| 23 | Fake transport sigillato, zero rete | PASS | Impostori e subclass respinti; fake RUN2 non può ricevere il transport live. |
| 24 | Critic/call budget/no repair invariati | PASS | B: una chiamata, critic OFF, zero repair; invalid document continua come dato misurato. |
| 25 | A snapshot/adapter/request esatti RUN1 | PASS | Snapshot e panel sono le autorità RUN1; 158/158 hash request A identici; arm A differisce solo nelle label. |
| 26 | Stesso panel 158 e pannelli separati | PASS | `120 canonical + 4 typed + 34 legacy = 158`, entrambi i bracci, nessuna compensazione. |
| 27 | Manifest 316, ordine e identità casi | PASS | 316 record AB/BA; ordine/case identity uguali a RUN1; A identica, B derivata dal prompt v0.3. |
| 28 | Stesso backend, modello, pesi e autorità host | PASS | Verifier runtime verde; PID 1505501 in `llama-server.service`, exe/pesi con hash attesi, listener `0.0.0.0:8080`. |
| 29 | Stesso generation profile e serialità | PASS | Temp 0, seed 42, max 4000, thinking false, retry 0; profilo comune A/B; esecuzione seriale AB/BA. |
| 30 | Policy adaptive-run chiusa | PASS | Prossimo run solo dopo suite+audit verdi; stop su nuova policy semantica o run identico in stallo. |
| 31 | Single-use, primo POST, zero retry | PASS | Marker esclusivo prima del socket; consumo al primo POST accettato; tentativo ambiguo impedisce rerun; nessuna collisione. |
| 32 | Partial fail-closed e catena raw/journal/checkpoint/seal | PASS | Transport/envelope/adapter fermano e sigillano partial; mutazioni marker/checkpoint respinte. |
| 33 | Isolamento gold e accesso solo post-seal | PASS | Import graph live senza oracle/expected; panel e manifest query-only; evaluator separato e preceduto dal controllo batch+seal. |
| 34 | Fake/replay 316 e tolleranza chiusa | PASS | 316/316 fake+replay; unica tolleranza replay resta il booleano esatto `adapter_metadata.implicit_actions_ignored`. |
| 35 | Verifier, mutazioni, freeze e preflight spento | PASS | RUN2 27/27; verifier 0 errori; preflight `disarmed_ready_for_audit`; auth/run assenti. |
| 36 | Semplicità, laboratorio e no-dict production | PASS | Un solo ingresso live, fake separato, nessun framework capability; tutto il nuovo codice è nel laboratorio e nessun modello dati `dict` è portato nel runtime production. Il porting futuro è dichiarato con tipi espliciti immutabili. |

## Prove rieseguite sul byte finale

- Candidate v0.2: **32/32 PASS**, incluso roundtrip **124/124**, mutazioni, fake transport e prova di determinismo/tempo.
- Prerequisito i18n v0.2.1: **4/4 PASS**, inclusi confusabili Unicode e tabella grandfathered completa.
- Candidate v0.3: **9/9 PASS**, incluso roundtrip **124/124**, prompt query-free, root bilanciate, `from` e registry sintetico.
- Protocollo RUN2: **27/27 PASS**, incluso fake/replay **316/316**, mutazioni, single-use, partial fail-closed, isolamento gold e identità RUN1.
- Build/check: v0.2.1, v0.3 e RUN2 verdi; verifier RUN2 **0 errori**; preflight spento verde.
- Controlli indipendenti: **158/158** request A identiche a RUN1; **158/158** request B con sola differenza prompt; **158/158** request invariate dal prerequisito i18n.

## Verdetto e limiti

**READY 36/36** significa che questi byte sono idonei a essere legati a una futura autorizzazione RUN2 e a un successivo preflight armato. Questo referto non arma il runner e non autorizza né esegue il POST. Non è una dichiarazione di accuratezza semantica: tale misura esiste soltanto dopo una corsa completa, sigillata e valutata sui pannelli separati.
