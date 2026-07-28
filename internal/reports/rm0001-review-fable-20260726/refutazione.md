# Refutazione dei rilievi

Fase B della review adversariale di RM-0001 — 27/7/2026, notte.
Otto attaccanti indipendenti; per i rilievi con riscontro di codice l'attaccante
ha riletto il codice. In caso di dubbio: CADE.

## Nota preliminare — il documento è cambiato sotto la review

[PROVATO] Le sette lenti (26/7, 14:39-15:32) hanno esaminato una revisione di
`internal/roadmap/RM-0001-conoscenza-utente-locale.md` da 2832 righe che **non
esiste più**: era stato dell'albero di lavoro mai committato. Il 26/7 alle 22:25
le lenti sono state consegnate all'agente autore (`CONSEGNA-lenti-26-7.md`) e
alle 23:18 (`stat` sul file) l'autore ha riscritto integralmente il documento:
1286 righe, fasi F0-F6, e un **§16 «Recepimento della review Fable»** che
dichiara accolto/respinto per ~30 rilievi. Conseguenze per questa fase:

- i numeri di riga citati dalle lenti verso RM-0001 **non sono più
  riverificabili** (nessuna copia della revisione 2832 su disco); i riscontri
  verso il **codice** sì, e sono stati ricontrollati tutti;
- la refutazione è condotta contro la **revisione corrente**: un rilievo
  «accolto» in §16 è scartato a monte solo se l'attuazione nel testo è stata
  **verificata** (audit riga per riga, non sulla parola del §16 — §2.8);
- il valore residuo di questa fase si sposta: non «quali rilievi reggono», ma
  «il recepimento è reale?» e «che difetti ha introdotto la riscrittura?».

Esito in una riga: il recepimento è sostanziale (24 «accolto» su 24 hanno una
sezione attuativa reale), nessun rilievo originale sopravvive come scritto, e
sopravvivono invece **cinque difetti della revisione nuova** emersi verificando
il recepimento.

## Scartati a monte

Ragione unica per tutti: **già recepito nella revisione 23:18, attuazione
verificata nel testo corrente** (audit dedicato, un agente; sezioni citate =
documento corrente). Una riga ciascuno.

**Lente ambizione**
- 1 nucleo troppo debole, nessuna metrica di richieste nuove → §0 (6 capacità), UC-04/05/06/07/08 casi core; metrica in F6/§15 («corpus congelato di richieste ellittiche oggi fallite»).
- 2 richieste implicite senza meccanismo → UC-07 + canonicalizzatore §5.7 (trigger dal fallimento della forma, non solo dei riferimenti).
- 3 porta della riscrittura pre-cache richiusa → accolta con limite: forma canonica povera pre-L0 (§0, inv. 9, §5.7, §5.10).
- 4 default operativi assenti, `backend_resolver` ignorato → UC-04, §5.4 `[personalization]`, §5.5, §2 riga «Default provider».
- 5 memoria episodica assente → UC-06, §6.3 `runtime_outcome`, §7.5, `episode_refs` F4.
- 7 esperienza = scelta fra k rami scritti a mano → dominio esperienza RIMOSSO da RM-0001 (§3.2); il rilievo decade con l'oggetto.
- 8 F6 duplica W1/ADR 0185 → §2 riga «Ciclo W1» + F6 «riusando W1»; niente secondo canale.
- 9 domande aggregate non coperte → UC-08, §5.8.
- 10 Zep/Mem0 non citati, costo del divieto d'iniezione non quantificato → fonti in §17.4, prova a tre bracci §11.2; il residuo «quantificare vs iniezione» è caduto sotto attacco (vedi sotto).

**Lente automatismo**
- R2 preferenze senza percorso silenzioso → §7.3 (promozione silenziosa con soglie preregistrate), F5, UC-02; catena completa verificata dall'audit (filtro §7.2 → eventi §6.3 → reconciler F5 → W2/`active_default`).
- R3 stato finale «apprende in silenzio» mai programmato → invariante 20 + §7.1 (default ATTIVO alla promozione di F5) + §15 (criterio di completamento).
- R4 opt-in collassabile in un consenso unico → §7.1: una sola informativa all'attivazione, opt-out, niente approvazioni per elemento.
- R5 apprendimento termina sempre in proposta → §7.3/§7.4: promozione silenziosa per classi non sensibili; proposta residua solo dove serve.
- R6 ri-domanda senza isteresi → §7.6, letterale (cinque condizioni chiuse di ri-domanda).
- R8 «cosa sai di me» non garantito in chat → invariante 29, §5.9, UC-09 («senza visitare obbligatoriamente una pagina amministrativa»), uscita F2.
- R9 che cosa NON può diventare silenzioso → non è un difetto ma un confine; coincide con gli invarianti 5/14/15 del documento corrente.

**Lente danno**
- 1 oblio riscritto dalla coda → §6.5 punto 4 (invalidazione content-blind di TUTTI gli eventi pre-epoch del principale) + invariante 25; lo span diverso è irrilevante per costruzione. Residui nuovi: vedi Sopravvissuti 3-4.
- 2 deletion ledger dentro il DB → §6.5: journal append-only SEPARATO, `PREPARED/COMMITTED`, replay all'avvio, quarantena (inv. 26).
- 3 lettura/mutazione sul passo e non sul piano → invariante 14 «Piano intero», F3, §9.
- 4 candidati fabbricati da origine esterna → riga di minaccia in §9 (provenienza, ancora, non-preselezione). Residuo nuovo: manca il caso di corpus — Sopravvissuti 2.
- 5 MemorySourceEvent senza esito → §6.3: `runtime_outcome` con esito semantico; «assenza, parziale, errore o ripresa tecnica non contano come successo».
- 7 W2↔memoria libera cieche → §6.4 ultimo capoverso: instradamento PreferenceSpec + conflitto cross-store, W2 prevale. Residuo nuovo: PreferenceSpec mai definita — Sopravvissuti 5.
- 8 profilo minore senza substrato → invariante 22 + §7.7: nessun claim di protezione finché manca il marcatore verificabile.
- 9 isolamento ospite vs amministratore → §7.7: limite applicativo dichiarato nella documentazione, niente cifratura promessa.
- 10 identificatore riciclato → invariante 13 + `identity_anchor` (§5.4, §5.6, §7.6, §11.1 trappole, §11.5); l'attuazione più completa del documento.
- 11 famiglia executor = canale di trasferimento avvelenato → dominio esperienza rimosso; decade con l'oggetto.
- 12 get_inputs preselezionato → invariante 15 «Nessuna preselezione», letterale.

**Lente fondamento** (rilievo 1 = conferma positiva, non un difetto)
- 2 dispatch builtin fabbrica host, InvocationContext senza produttore → blocco F0 (§13 `principal_context.py` + censimento esplicito dei punti, riga 1046-1050), invariante 2; F2 senza executor (§5.9).
- 3 fabbriche identità fail-open, TutorPrincipal non citato → §5.1: «TutorPrincipal diventa una proiezione ristretta»; §2 tabella riga Tutor.
- 4 project_paths.json ignorato → UC-03 «Prima istanza obbligatoria», §5.6.
- 5 clause_span senza sorgente → §2 riga Clausole + F0 «intervalli generici nel segmentatore compound» + §13 (`compound_decomposer.py`, «unico helper comune»).
- 6 capability filesystem inesistenti per builtin in-process → risolto per eliminazione: il boundary non è un executor (§5.9), vincolo d'import in §13.
- 7 users.py oltre la patch, authz_revision senza substrato → §6.1: WAL, busy_timeout, transazioni, `prefs_revision`/`authz_revision`; §5.1; ADR di fase F0.
- 8 `_RUNTIME_ARG_SOURCES` non nominato / sorgenti senza contesto → §5.5: evoluzione dichiarata a provider con `TurnInvocationContext`, «senza un secondo canale» (§2).

**Lente leiden**
- 1-9 (macro-verdetto «non serve»: nessun consumatore, scala 2-4 ordini sotto, precedente mnestoma, contraddizione interfaccia-retrieval, relazioni portanti da anticipare, dipendenza nuova, harness anti-stocasticità, ordine perso dalla proiezione) → Leiden RIMOSSO da RM-0001 (§3.2, preambolo r. 21, §14, §19); le relazioni tipizzate restano nel nucleo (F5) e la riapertura è condizionata a fallimenti documentati. Il verdetto della lente è stato accolto in blocco; la sola proposta di soglia numerica è caduta sotto attacco (sotto). L6 lascia un residuo nuovo di fase — Sopravvissuti 1.

**Lente misura**
- 1 prova causale solo per gli hint → §11.2 tre bracci per OGNI fase influente + invariante 30 + uscite F3-F6.
- 2 metrica cieca al referente sbagliato → §11.1: «referente/valore atteso indipendente dall'esito executor», letterale.
- 3 riduzione correzioni come criterio → sparita dai criteri; metrica primaria = interazioni all'esito corretto (§11.2).
- 4 partizione idoneo/non-idoneo autoprodotta → §11.1: «nell'etichetta del corpus, non decisa dal sistema sotto test», letterale.
- 5 denominatore N libero → §7.2 ultimo capoverso (N = eventi realmente esposti) + log obbligatorio degli scarti con reason code (§7.2 p.6) + §11.3.
- 6 claim-pappagallo → metrica rimossa; il valore si misura a valle coi tre bracci; annotazione qualificata (§11.4).
- 7 «riduzione passi» come scappatoia → §11.2: «secondaria e non basta per promuovere», letterale.
- 8 gotcha e rimedio dalla stessa mano → dominio esperienza rimosso; decade.
- 9 metriche vere-per-costruzione → §11.3: riclassificate «invarianti/property test, non prove di qualità», letterale.
- 10 metrica primaria senza mappa per fase → §11.2: preregistrazione PER OGNI FASE di primaria, danni, effetto minimo, seed, potenza, arresto.
- 11 doppia annotazione senza indipendenza → §11.4, letterale (natura, indipendenza, disaccordo, adjudication).

**Lente semplicità**
- R1 scissione del dominio esperienza → eseguita (§3.2, preambolo).
- R2 F0 costruiva la suite experience → eliminata con il dominio (F0 «Lavoro»).
- R3 derivatore prima della misura → ordine F0→F4 esatti/episodi, compilatore solo in F5 dietro F1-F4 certificate (§6.2 `compile_queue`: «il consumer reale arriva in F5»).
- R4 registro di regole statiche prima dello store esperienza → fuori perimetro con l'esperienza; la condizione «solo dopo un caso reale» resta in §3.2.
- R5 primo ReferenceSlot senza nome → UC-03/`project` su `project_paths.json` (§5.6).
- R6 secondo riconoscitore bilingue → `detection_lexicon` vincolato in §5.7, §5.9, §10, §13.
- R8 11 tabelle e quote a 6 dimensioni → §6.2 «create soltanto nella fase che le usa», embedding/comunità fuori dalle minime; §6.6 quote su DUE grandezze.
- R9 LLM irraggiungibile per claim con slot → invariante 18 + §6.4 (deterministico per slot/enum/correzioni/supersedes tipizzato).
- R10 tre meccanismi d'oblio → epoch ora derivato dal journal separato e rispecchiato (§5.2, §6.5): due meccanismi, come chiesto.

## Caduti sotto attacco

- **[ambizione 6] «Automatica vale solo in lettura, per sempre» → CADE.**
  Attacco: la conferma sui mutanti è il controllo di piano del motore, presente
  con o senza memoria (§0: «normale controllo... già esistente»; inv. 14
  *aggiunge visibilità* dentro il gate esistente, non un gate nuovo); la memoria
  elimina l'attrito dove ha giurisdizione (isteresi §7.6); la promozione della
  fiducia esiste già come atto esplicito FUORI dalla memoria (mandati ADR 0190,
  task schedulati, autopath ✓ ADR 0185); e il rilievo confligge frontalmente con
  automatismo R9 e con l'invariante 5, che un'altra lente della stessa consegna
  difende. Il respingimento in §16 è fondato. Residuo sotto soglia: citare ADR
  0185/0190 in RM-0001 come percorso di consolidamento dichiarato.
- **[automatismo R7] backfill del proprietario → CADE (premessa fattuale falsa).**
  Attacco sul TurnLog reale: nessun principal né sender_id nei record; su ~9.000
  turni `actor` è `host` nel ~75% dei casi e il proprietario compare sia come
  `roberto` sia come `Roberto`. «Attribuzione in pratica nota» è falso; un
  backfill fabbricherebbe il principal (minaccia già enumerata in §9 r. 784:
  «nessuna conversione da actor solo»). La ri-dichiarazione confermata in
  preview resta possibile oggi senza toccare il documento; le 14 preferenze W2
  già espresse persistono e vengono riusate (§2).
- **[danno 6] finestra di co-attivazione dei set espliciti → CADE (marginale).**
  Attacco: la premessa non corrisponde più al testo — §7.4 condiziona
  l'attivazione dei claim liberi a «policy e riconciliazione», e l'effetto
  pre-F5 è confinato a `active_read` su risposta finale/inventario (§8.3):
  nessun percorso di danno operativo; «correggi» con `supersedes` deterministico
  esiste da F2. Residuo = ambiguità di sequenziamento (chi riconcilia in F2-F4),
  da una riga nell'ADR di fase F2, non da Roberto.
- **[automatismo R1] «21 azioni dell'utente» → CADE (assorbito).** Ricensimento
  sul documento corrente a regime: restano ~4 famiglie per-elemento (prima
  conferma di riferimento ambiguo, conflitto irrisolto, controllo piano
  mutanti/outbound, chiarimento compound) — esattamente le voci che la stessa
  lente in R9 dichiarava irrinunciabili, più l'opt-in ospiti (dati di terzi).
- **[semplicità R7] costo della specifica → CADE (esaurito nei fatti).** 1286
  righe (sotto il target ~1500 della lente stessa), ADR da 18 a 8; il rimedio
  proposto (scissione + condizionamento) è stato applicato. §16 respinge solo la
  tesi epistemica «le righe da sole provano complessità», che la lente stessa
  usava come segnale.
- **[leiden, proposta] soglia numerica di riapertura (~10^4 nodi) → CADE.**
  La condizione qualitativa di §3.2 è un doppio cancello (casi documentati +
  scala reale) che produce solo il diritto di proporre una ADR, dove scattano le
  misure di §6.2/§8.1; non c'è un sistema sotto test che si autopromuove, quindi
  nessuna incoerenza con §11. Il 10^4 era esso stesso un [IPOTESI]: congelarlo
  sarebbe pseudo-precisione.
- **[ambizione 10, residuo] quantificare «cosa perdiamo vs iniezione» → CADE.**
  Richiederebbe di costruire il design rifiutato per misurare un numero che non
  governa alcun cancello (l'esclusione poggia su invarianti di danno 7/8/27, non
  su un confronto di beneficio); la domanda decisionale è coperta dai tre bracci,
  il cui braccio 2 è la forma degenere e sicura dell'iniezione; §17.2 mostra sul
  caso Swafra perché i numeri esterni non trasferiscono (92,34% ricalcolato vs
  99,6% dichiarato).
- **[ambizione 11] cap «da rubrica» senza criterio di crescita → CADE.** Il 200
  non esiste più; quote «iniziali» su due grandezze con sottoquote solo a fronte
  di misura (§6.6), budget per richiesta dichiarati (§8.2), soglie congelate in
  F0 (§14), troncamento visibile (inv. 24). Il tetto a una consultazione resta,
  ma riclassificato invariante di boundedness, modificabile per ADR.

## Sopravvissuti

Nessun rilievo delle lenti sopravvive come scritto. Sopravvivono cinque difetti
**della revisione 23:18**, emersi verificando il recepimento — che è il punto in
cui questa catena poteva ancora aggiungere valore. In ordine d'importanza:

1. **Incoerenza di fase: UC-05 richiesto verde in F4, ma `relations` nasce in F5.**
   [PROVATO] F4 uscita esige «UC-05 … verdi»; UC-05 esige «una decisione
   successiva crea `supersedes`»; §6.2 crea `relations` «soltanto nella fase che
   le usa» = F5; §6.4 rende le relazioni la fonte canonica di `supersedes` e la
   decisione di UC-05 è memoria libera, non chiave tipizzata di `users.db`, quindi
   non esiste via alternativa. Lo stesso vale per §8.1 punto 5 («relazioni a un
   salto» nell'ordine iniziale del recupero, che nasce in F4). Confermato da DUE
   attaccanti indipendenti. Attacco tentato: cercata una via `user_defaults`/
   invariante 18 — copre solo slot tipizzati, non regge. Fix a costo zero:
   anticipare `relations` (almeno `supersedes` deterministico) a F4, o spostare
   UC-05 in F5.
2. **Il caso avversariale «candidato di disambiguazione fabbricato da contenuto
   esterno» ha la contromisura ma non la prova.** [PROVATO per assenza] §9 elenca
   la minaccia (riga «candidato ostile creato su share/registro»); ma in §11.1 le
   trappole coprono il riferimento *confermato poi cambiato*, gli attacchi coprono
   il *testo* esterno, §11.5 il solo ID riutilizzato: la cartella `atlas` piantata
   sul NAS *prima* della conferma non ha un caso di corpus. In più la riga di
   recepimento per danno-4 manca da §16: il registro si dichiara completo
   («verificati singolarmente») e non lo è. Una categoria in più in §11.1 chiude.
3. **La risoluzione del target di cancellazione non è specificata per il caso
   «già compilato».** [PROVATO per assenza + IPOTESI] §6.5 punto 4 invalida gli
   eventi *non completati*; ma un evento same-content già compilato e COMMITTED
   prima del delete è ormai una memoria attiva distinta, e §6.5 punto 1 non dice
   con quale ampiezza il target viene risolto in ID (per chiave? per contenuto?
   solo l'ID indicato?). UC-10 promette «gli eventi precedenti ancora in coda non
   possono ricreare il dato» — il gemello già compilato resta fuori dalla
   promessa. Serve una riga di specifica.
4. **La corsa dell'enqueue non è serializzata dal lock di §6.5.** [IPOTESI su
   testo provato] Il lock copre «allocazione dell'epoch, cancellazione e
   compilazione», non l'enqueue: un evento inserito fra i punti 4 e 6 con epoch
   stantio congelato sfuggirebbe allo sweep. La mitigazione esiste probabilmente
   per costruzione (`deletion_epoch_at_enqueue` per-evento, §6.3, ricontrollato
   dal compilatore) ma il testo non dichiara quel confronto. Una frase la chiude.
5. **`PreferenceSpec` è citata (§6.4, §9) e mai definita**: dove vive, chi la
   compila, in quale fase. [PROVATO per assenza] In un documento che mappa ogni
   struttura a una fase (§6.2, §13) è un'attuazione a metà del recepimento
   «W2 e memoria libera cieche». Una riga in §6.1 o §13.

Minori (nessuna azione di Roberto richiesta): la numerazione salta da §19 a §28
(«Risposta Reddit», residuo della revisione vecchia); nel codice,
`agent_runtime.py:5832-5833` porta un commento che promette un retry senza
kwargs che il corpo non esegue (logga e fallisce) — igiene da sistemare quando
F0 ridisegnerà comunque quel dispatch.

## Riscontri [PROVATO] smentiti alla rilettura

Ricontrollati **tutti** i riscontri di codice delle sette lenti (28 verifiche su
tre attaccanti). Esito: nessun percorso o funzione inventata; 1 smentita, 4
imprecisioni.

- **SMENTITO** — lente leiden, «`users.db` reale … nessuna tabella `user_prefs`
  ancora creata»: la tabella **esiste** in `~/.local/share/metnos/users.db` con
  **14 righe** del proprietario (tone, reply_length, lang, units + 10 `sites_*`,
  tutte `explicit`). L'argomento di scala della lente resta in piedi (14 righe
  non cambiano gli ordini di grandezza), ma il riscontro come scritto è falso.
  Resta invece vero il punto gemello della lente ambizione: il consumo nel
  percorso del turno è solo `sites_*`+`lang` (`agent_runtime.py:6221-6231`).
- **IMPRECISO** — lente fondamento, «il fallback su TypeError riprova senza
  kwargs (5833)»: a `agent_runtime.py:5832-5833` il commento promette il retry,
  il corpo **non riprova** (logga e ritorna errore). L'imprecisione non tocca la
  sostanza del rilievo 2 (il fallback `actor or "host"` a r. 5803 è confermato).
- **IMPRECISO** — lente fondamento, «la revoca oggi è `verified_at=NULL`
  (users.py:435-438)»: quel codice sta dentro `issue_pairing_token` (riemissione
  del token), non in una funzione di revoca; `revoke_*` non esiste in users.py.
- **IMPRECISO** — lente fondamento, «API prefs keyed su stringa»: vero per la
  firma dell'API, ma lo storage scrive `u["id"]` risolto via `get_user()`.
- **IMPRECISO** — lente leiden, «84 executor firmati»: sono 83 `manifest.toml.sig`.

Tutto il resto regge alla rilettura, incluse le misure d'istanza (mnestoma
17/1034/84 esatti; turni 7023/61 giorni/16441 step esatti; autopath 5, fastpath
76, executor_stats 188 esatti; leidenalg/igraph davvero assenti,
`leiden_communities` davvero NotImplementedError su networkx 3.6.1).

## Convergenze fra lenti

- **ambizione (F9 da declassare) + leiden (verdetto «non serve»)**: convergenza
  indipendente, la seconda con dati d'istanza; accolta in blocco dalla revisione
  (rimozione §3.2).
- **ambizione 6 vs automatismo R9**: conflitto frontale sulla conferma dei
  mutanti; risolto a favore di R9 dal §16 e dall'attacco (CADE). Da registrare:
  quando due lenti confliggono, quella allineata agli invarianti d'autorità vince.
- **fondamento 5 (span di clausola) + semplicità R6 (lessico)**: due metà dello
  stesso rischio «secondo riconoscitore»; entrambe recepite nello stesso punto
  (§5.9 + F0 splitter comune + detection_lexicon).
- **fondamento 4 (project_paths) + semplicità R5 (primo slot senza nome)**:
  convergenza piena; recepita in UC-03/§5.6.
- **misura 1 (nessun braccio di confronto) + ambizione 1 (nessuna metrica di
  ambizione)**: convergenza su «promozione senza prova del beneficio»; recepita
  con tre bracci + invariante 30 + corpus ellittico in F6/§15.
- **danno 1 (coda vs oblio) + semplicità R10 (tre meccanismi d'oblio)**:
  convergenza sul sottosistema oblio; recepite insieme nel journal separato con
  epoch derivato.

## Riepilogo

In ingresso 70 rilievi numerati dalle sette lenti (1 era una conferma positiva
del fondamento). La riscrittura di RM-0001 delle 23:18 del 26/7 ne ha recepiti
63, e l'audit ha verificato che ogni «accolto» di §16 ha un'attuazione reale nel
testo: nessun falso successo. Gli 8 rilievi restanti (6 numerati + 2 proposte
residue) sono stati attaccati e sono tutti CADUTI, due per premessa fattuale
falsa alla rilettura del codice/TurnLog. Sopravvivono 5 difetti nuovi della
revisione: (1) UC-05 esige `supersedes` in F4 ma `relations` nasce in F5 — unica
incoerenza che rende una fase non chiudibile com'è scritta; (2) il candidato
ostile fabbricato ha la contromisura ma nessun caso nel corpus §11.1 (e la riga
manca da §16); (3) l'oblio non specifica il destino del claim gemello già
compilato prima del delete. Un solo [PROVATO] delle lenti è stato smentito
(user_prefs esiste, 14 righe); il resto regge.
