# Audit indipendente preflight GPU RUN 1 — candidate_v0_2 — cycle 2

Data audit: 2026-08-13  
Revisore: `reviewer_b_independent`  
Perimetro: protocollo offline `candidate_v0_2` e artefatti canonici `live/run1`  
Verdetto: **READY — 36/36 PASS, 0 FAIL**

## Oggetto e limite del verdetto

Questo referto attesta che il protocollo RUN 1, nel byte finale qui vincolato, è
pronto per la successiva autorizzazione e per un preflight armato separato. Non
autorizza né esegue il run. Durante l'audit non erano presenti file di
autorizzazione o artefatti di consumo/esecuzione; non sono stati effettuati POST,
accessi di rete, uso GPU, avvio o modifica di servizi, né scritture di produzione.

Il referto deriva da codice, artefatti materializzati e test; i documenti
`self_review.md` non sono stati usati come autorità. Non esprime alcuna nuova
misura di accuratezza semantica: gold e valutazione v0.3 restano post-sigillo.

## Freeze finali verificati

- Candidate `candidate_v0_2.freeze.json` SHA-256:
  `1004d0afa2a72567f4dbd0a0daad530ab05449d30d4cd4e57ab06c42d28f93b7`
- RUN 1 `protocol_run1.freeze.json` SHA-256:
  `e17246da0a7472a3d246c7dcd75823e14cd2fb25a0ae1a4c2ec8d176f6fef917`
- Il protocollo RUN 1 lega esattamente il freeze candidato sopra indicato.

## Esito della checklist finita

1. **PASS** — Namespace RUN 1 nuovo e separato; nessuna collisione con artefatti storici.
2. **PASS** — Freeze RUN 1 copre codice, test, dati, sorgenti di build e autorità assolute dichiarate.
3. **PASS** — Runner/import graph live privo di oracle, expected e percorsi gold.
4. **PASS** — Stato materializzato `disarmed_prepared_no_inference`; nessuna autorizzazione presente.
5. **PASS** — Braccio A usa snapshot fresco del controllo Metnos corrente e adapter read-only.
6. **PASS** — Braccio B è legato al freeze finale di `candidate_v0_2` e fallisce chiuso sul drift.
7. **PASS** — B usa realmente `response_format=json_schema` con `strict=true`, senza grammar o tools; A ne resta privo.
8. **PASS** — Output B validato e compilato deterministicamente; una sola risposta primaria, byte preservati, nessun repair.
9. **PASS** — Entrambi i bracci condividono endpoint, provider, modello fisico e identificatori backend congelati.
10. **PASS** — Profilo generativo identico: temperatura, seed, token, thinking, stream, cache e timeout.
11. **PASS** — Zero retry; critic disattivato nel run; nessuna seconda chiamata o probe.
12. **PASS** — Pannello di 158 query e manifest di 316 richieste, due bracci per caso.
13. **PASS** — Ordine seriale AB sugli indici pari e BA sugli indici dispari, con ordinali e hash chiusi.
14. **PASS** — Pannelli distinti 120 canonici, 4 typed control e 34 Phase-1 legacy per ciascun braccio.
15. **PASS** — Limiti JSON chiusi; overflow, non-finite, duplicati, profondità, nodi, stringhe e byte sono invalidità tecniche.
16. **PASS** — Denominatori e gate sono separati; nessuna compensazione tra pannelli o conversione automatica Phase-1.
17. **PASS** — Marker esclusivo scritto e sincronizzato prima del primo tentativo di socket.
18. **PASS** — Misura consumata al primo POST accettato; un tentativo ambiguo vieta comunque il rerun.
19. **PASS** — Guardia single-use verifica tutti i nomi riservati e rifiuta collisioni prima del trasporto.
20. **PASS** — Nessun percorso retry, resume o rerun; ingresso live unico e fake separato.
21. **PASS** — Trasporto, timeout, body limit, envelope o errore adapter arrestano e sigillano un partial fail-closed.
22. **PASS** — Solo JSON/IR model-facing invalido viene contato e consente di proseguire, senza riparazione.
23. **PASS** — Risposta HTTP raw, header, content Base64 e SHA-256 sono conservati e reciprocamente vincolati.
24. **PASS** — Journal append-only, una riga canonica per record, validato integralmente nel replay.
25. **PASS** — Checkpoint atomico vincola conteggi, ultimo ordinale, manifest, autorizzazione e journal.
26. **PASS** — Sigillo vincola batch/partial, journal, marker, autorizzazione, conteggi e motivo di arresto.
27. **PASS** — Solo 316 record e 316 POST accettati senza stop possono produrre stato `complete`.
28. **PASS** — Freeze, batch, seal, marker, checkpoint e replay raw sono validati prima di aprire il gold.
29. **PASS** — Metrica v0.3 è post-sigillo e simmetrica; canonicalizza solo rappresentazione derivabile.
30. **PASS** — Stati authorization `disarmed`/`armed` sono chiusi; assenza, extra e stato inatteso falliscono chiuso.
31. **PASS** — Schema authorization lega run, protocollo/freeze/payload, manifest, audit indipendente, root e nonce single-use.
32. **PASS** — Nessun artifact auth/run/consumption presente al momento dell'audit; storici non sovrascritti.
33. **PASS** — Fake offline esegue 316 record e replay316; rifiuta il trasporto live prima di ogni send.
34. **PASS** — Verifier controlla hash candidato/RUN 1, sorgenti runtime, DB read-only, binario, modello e configurazioni backend.
35. **PASS** — Mutazioni critiche, universalità, i18n, semplicità e confine lab/production falliscono chiuso come previsto.
36. **PASS** — Build e suite deterministiche verdi; verifier e preflight disarmato a zero errori; nessun difetto residuo.

## Prove rieseguite

- Suite candidato: **32/32 PASS**, incluso round-trip esatto di 124 casi.
- Suite RUN 1: **22/22 PASS**, incluso fake 316 e replay 316.
- `candidate_v0_2.build_artifacts --check`: exit 0.
- `live.run1.build_artifacts --check`: `status=ok`, zero errori.
- `live.run1.verify`: `status=ok`, `error_count=0`, rete/GPU/inferenza false.
- `live.run1.runner --preflight`: `disarmed_ready_for_audit`, guardia libera, 316 richieste, rete/GPU false.
- Scan anti-hardcoding su candidate, compiler, prompt e adapter: 17 file, zero query, frammenti, ID, hash o conteggi-banco nella soluzione.
- Prova canonicalizzazione: equivalenza rappresentazionale confermata; modifiche a root, route, order, edge e reason restano differenti.
- Prova i18n mirata: `sl-rozaj-ROZAJ` e `en-a-test-a-more` rifiutati pre-send sia dal client candidato sia da `arm_b`; `captured_requests=()`.
- File authorization/run/consumption trovati: **0**.

## Cycle 1 storico e chiusura del difetto

Il cycle 1 terminò **NOT READY, 35/36**, perché
`sl-rozaj-rozaj` (variante BCP47 ripetuta) veniva accettato e avrebbe raggiunto
il trasporto. Nel byte finale cycle 2 il normalizzatore mantiene un insieme di
varianti già viste e rifiuta i duplicati; rifiuta inoltre singleton di estensione
ripetuti. I test verificano che il rifiuto avvenga prima di registrare o inviare
la richiesta. Il blocker storico è quindi chiuso senza introdurre branch per
lingua, allowlist o fallback.

## Vincoli permanenti verificati

- **Universalità:** nessuna logica speciale per query, frammenti, ID, indici,
  hash, panel o eccezioni del banco; 120/4/34 resta solo protocollo/test.
- **i18n:** un solo prompt neutro; tag BCP47 normalizzato e passato
  esplicitamente; ogni tag strutturalmente valido segue lo stesso percorso;
  malformed fallisce pre-send; nessun branch `it/en` nel candidato. Il braccio A
  conserva intenzionalmente il comportamento del controllo production corrente.
- **No-dict production:** ogni nuovo file resta sotto il laboratorio; i dict sono
  rappresentazioni lab/JSON. Un eventuale porting production dovrà usare tipi
  espliciti, chiusi e immutabili.
- **Semplicità:** un solo ingresso live autorizzato, un fake offline separato,
  nessun framework di capability e nessun percorso di recupero implicito.

## Limiti e passaggio successivo

Il controllo è offline e pre-autorizzazione. Non prova una connessione live, non
verifica ancora un PID/argv/porta in esecuzione e non osserva output modello.
Il solo passo successivo ammesso da questo verdetto è materializzare
un'autorizzazione unica legata allo SHA-256 di questo referto e rieseguire un
preflight **armato ma senza POST**. Il RUN 1 resta vietato fino a un separato
verdetto `ready_to_execute`.
