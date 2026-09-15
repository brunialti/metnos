# RM-0009 — review parziale delle chiarificazioni decisionali B/D/F

Data: 2026-09-15. Oggetto esclusivo: modifiche normative correnti nelle sezioni
5.3, 5.4.1, 5.5, 5.6/5.6.1 e D.8 della roadmap, confrontate con le direzioni B,
D e F di `internal/reports/rm0009-baseline/20260915/preparation-analysis.md`.

Questa è una review testuale parziale. Non è G0.7 o G0.8, non valuta codice o
prove e **non autorizza uno stato ready**.

## Esito sintetico

Le modifiche recepiscono la sostanza delle tre direzioni:

- B: `promotion` è ora un `DecisionMoment` distinto, il campione insufficiente
  precede le domande e produce `blocked/insufficient_evidence`, con rientro in
  `proposed`;
- D: sono esplicitati tipi/intervalli, esclusione dei booleani, valori finiti,
  delta strettamente positivo e casi baseline zero;
- F: emissione e risposta sono transazioni distinte; domanda logica,
  prenotazione settimanale, outbox e `delivery_unknown` sono separati dalla
  decisione e dalla consegna fisica.

Restano però cinque incoerenze o lacune ad alta priorità e due minori. Le più
importanti permettono a un’implementazione conforme a una scheda F5, ma non
alla norma generale, di omettere `promotion`, superare il cap settimanale o
lasciare una decisione in attesa senza un meccanismo che la risvegli.

## Rilievi

### P1 — `DecisionMoment` è ancora chiuso a due valori nella scheda F5.2

**Priorità: alta.**

La sezione 5.3 definisce correttamente i valori persistiti `trial`,
`activation`, `promotion` (`:321-326`), ma F5.2 continua a imporre
`DecisionMoment: trial o activation` (`:1532-1538`). È una contraddizione
normativa diretta: un esecutore che segue la scheda può rifiutare o non
implementare il momento unico di `promote_plan`, pur dichiarando di aver
seguito F5.2.

**Correzione minima:** cambiare F5.2 punto 3 in enum chiuso a tre valori e
aggiungere al criterio di test la matrice `promotion` con campione
`0`, `minimo-1`, `minimo`, regola di rifiuto attiva e costo ignoto. Richiamare
esplicitamente che promotion non richiede una nuova receipt Birth del piano.

### P2 — F5.3 regredisce rispetto alla transazione di emissione di 5.5

**Priorità: alta.**

La norma generale definisce emissione come creazione atomica di domanda,
token, prenotazione e outbox (`:486-491`). F5.3 ordina invece
`one_shot_tokens.issue` «per ogni ask_user» e soltanto dopo dice che il
riepilogo mostra domande entro il budget (`:1547-1552`). Letta alla lettera,
questa sequenza può:

- emettere token/domande anche oltre il cap e limitare soltanto la loro vista;
- separare token da domanda, budget e outbox;
- confondere il commit logico di emissione con il successivo tentativo fisico
  sul canale;
- non implementare `delivery_unknown`, perché non è citato nella scheda.

**Correzione minima:** sostituire i primi due passi F5.3 con un unico primitivo
di emissione che, sotto la stessa transazione del DB di governance, (a)
seleziona una sola entry di coda ancora eleggibile, (b) verifica/prenota il
bucket settimanale, (c) crea domanda e token, (d) crea l’outbox. Il sender
lavora solo dopo il commit e aggiorna esclusivamente lo stato di delivery. La
risposta resta una transazione distinta come già prescritto da 5.5.

I test minimi F5.3 devono includere due worker sullo stesso ultimo slot,
crash prima/dopo commit dell’emissione, prima/dopo invio e prima/dopo receipt,
errore certo, `delivery_unknown` e cambio settimana. I soli replay/doppi clic
della risposta non coprono questo confine.

### P3 — Non è assegnato il risveglio di `blocked` e della coda: l’attesa può essere indefinita

**Priorità: alta.**

La roadmap dice cosa accade *quando* il campione promotion diventa sufficiente
(`:358-360`) e che `blocked` torna al suo `resume_state` dopo rimozione del
veto (`:436`), ma non assegna evento, job, cadenza o limite di staleness che
riesegua la decisione. Analogamente:

- le domande sopra budget «aspettano in coda» (`:483-485`), senza una norma
  che promuova le entry al primo slot disponibile o al rollover ISO UTC;
- `question_budget_weekly=0` accoda tutto (`:528`) e può essere una scelta
  intenzionale, ma non è definito uno stato visibile distinto da una coda
  accidentalmente bloccata;
- la scadenza revoca il token e richiede una nuova valutazione (`:501-502`),
  ma nessuna unità possiede esplicitamente expiry, re-evaluation e requeue;
- `delivery_unknown` resta consultabile e finito dal TTL in teoria, ma senza
  un expirer proprietario può restare pendente per sempre in pratica.

Il riferimento generico al riepilogo notturno non chiude il problema: F5.3
non lo registra esplicitamente nella sequenza notturna né definisce un wakeup
su nuova evidenza.

**Correzione minima:** assegnare a F5.2/F5.3 un reconcile deterministico che
viene invocato sia su pubblicazione di fatti/evaluations sia a cadenza
notturna. Deve scandire `blocked` ritentabili, scadenze e coda non emessa,
registrare `last_checked_at`/`next_eligible_at` o equivalente e applicare il
CAS verso l’esatto `resume_state`. Al rollover della settimana prova in ordine
la coda fino al cap; con policy zero conserva una ragione visibile
`question_budget_disabled`, non una falsa consegna pendente. Serve una prova
con clock finto che dimostri avanzamento dopo campione sufficiente, nuovo
bucket e scadenza, senza intervento manuale.

### P4 — Il binding `row_version` non è ancora un invariante di schema/risposta sufficiente

**Priorità: alta.**

5.5 lega la domanda alla `row_version` attesa e rifiuta intent rivalutati
(`:469-482`). F5.3 include `row_version` nel digest (`:1548-1550`). D.8, però,
elenca soltanto un generico binding kind/ID/momento/generazione/digest/policy
(`:2231`): non richiede una colonna `expected_row_version`, un vincolo UNIQUE
del binding logico o la ricostruzione del digest corrente nella transazione di
risposta.

Un digest opaco non basta da solo per il CAS SQL. Inoltre una nuova evaluation
o una nuova policy può cambiare i fatti senza necessariamente modificare la
riga `change_intents`; il solo confronto della vecchia `row_version` potrebbe
quindi accettare una domanda semanticamente scaduta.

**Correzione minima:** in D.8 rendere espliciti almeno
`expected_row_version`, `facts_digest`, `effect_digest`, `policy_version` e un
UNIQUE sul binding logico completo. L’emissione legge versione e fatti nella
stessa transazione e non rende il proprio token immediatamente stale. La
risposta, nella transazione che consuma il token, rilegge la riga, ricostruisce
il binding con facts/policy correnti e fa CAS su stato e
`expected_row_version`. Qualunque pubblicazione decision-relevant che cambia
il binding deve revocare la domanda oppure renderla fallibile tramite quel
ricalcolo. Aggiungere test in cui evaluation o policy cambiano senza una
transizione di stato.

### P5 — Restano due buchi nei confini numerici: denominatore zero e precedenza del costo ignoto

**Priorità: alta.**

Le regole baseline zero sono ora corrette per i confronti relativi
(`:571-578`) e la soglia zero non trasforma più un delta nullo in regressione
(`:522`, `:571-573`). Rimangono però:

1. `success_rate = success / (success + partial + error)` (`:563-565`) non
   prescrive esplicitamente di controllare il denominatore prima della
   divisione. `no traffic` non è successo (`:587-590`), ma questa frase non
   assegna un valore/stato machine-readable e non copre mediane vuote o il
   denominatore di `need_completion_rate`.
2. La tabella di policy dice che costo ignoto è
   `blocked/cost_unverified` (`:518`), mentre la precedenza 5.3 può arrivare a
   «scelta umana sul costo» o al fallback `ask_user` (`:349`, `:354`) se
   `cost_unverified` non è normato come veto tecnico della riga 1. F5.2 prova
   soltanto «fatto mancante → mai auto» (`:1539-1541`), che permette ancora
   `ask_user` e quindi una sostituzione umana della verifica del costo.

**Correzione minima:** stabilire che sample/denominatore viene validato prima
di divisione o mediana: denominatore zero o insieme vuoto produce
`insufficient_evidence/no_observations`, mai `0`, `NaN` o eccezione. Definire
anche il denominatore di `need_completion_rate`. Inserire
`cost_unverified` tra i veti tecnici espliciti della riga 1 per ogni momento in
cui il costo è obbligatorio; solo un costo autorevole noto sopra soglia può
produrre `ask_user`. Testare che costo assente non sia né zero né consenso
sostitutivo.

### P6 — D.8 non congela ancora il cap settimanale come invariante concorrente

**Priorità: media.**

5.5 descrive correttamente prenotazione, settimana ISO UTC, rilascio soltanto
con prova di nessun invio e consumo su delivery confermata/ignota
(`:492-497`). D.8 non elenca tuttavia bucket/settimana, stato della
prenotazione, riferimento alla domanda, limite o vincolo di unicità. Non è
quindi possibile derivare dalla riga D.8 uno schema che impedisca a due worker
di superare il cap.

**Correzione minima:** aggiungere all’oggetto D.8 il bucket `iso_week_utc`,
reservation ID/question ID, stato `reserved/released/consumed`, motivo e
timestamp, UNIQUE per domanda/bucket e un’invariante transazionale
`reserved+consumed <= policy_cap` valutata sotto lo stesso lock/CAS usato per
l’emissione. Chiarire che “domande emesse” nell’indicatore 9 significa commit
logico dell’emissione, mentre `delivered`, `delivery_unknown` e non inviata
sono conteggi separati.

### P7 — La ripresa da `waiting_dependency` è troppo assoluta

**Priorità: media.**

5.4.1 conserva correttamente lo stato intent e la stessa operation, ma ordina
sempre di ripartire da `observe_effect` (`:453-462`). D.8 conserva anche la
«fase da riprendere» (`:2228`). Se il veto è comparso prima della prima
chiamata con effetti, l’osservazione può dimostrare che l’effetto è assente;
la norma non dice allora se riprendere la fase registrata, restare in attesa o
fallire. Se l’esito esterno è ignoto, non deve invece ripetere l’effetto.

**Correzione minima:** dopo il ripristino, `observe_effect` è il primo passo di
riconciliazione; effetto presente → avanza senza ripeterlo, effetto
autorevolmente assente e start mai durably committed → riprende la fase
registrata con lo stesso `operation_id`, esito ignoto → resta
`waiting_dependency`. Nella transizione `blocked -> resume_state`, sostituire
«rimozione del veto» con «risoluzione verificata della causa di blocked», così
da includere senza ambiguità anche `insufficient_evidence`.

### P8 — Duplicazione editoriale del titolo 5.6

**Priorità: bassa.**

`### 5.6 Registro di politica: valori iniziali` compare due volte consecutive
alle righe 505-506. Non cambia la semantica, ma può generare anchor duplicati o
letture errate da tooling documentale.

**Correzione minima:** rimuovere una delle due righe.

## Elementi confermati senza rilievo

- La precedenza `veto tecnico -> rejection_rule attiva -> campione promotion
  insufficiente -> domanda` è coerente con la direzione B. La frase «nessuna
  regola di rifiuto» alla riga 348 va letta come “l’attesa non ne crea una”:
  una regola già attiva è già stata gestita dalla riga 2.
- Promotion non eredita la receipt Birth del piano e conserva soltanto i veti
  applicabili agli executor referenziati.
- `proposed -> blocked` include promotion e campione insufficiente con
  `resume_state=proposed`; `awaiting_activation -> blocked` resta distinto.
- Emissione logica, delivery e risposta sono concetti distinti in 5.5;
  `delivery_unknown` non diventa uno stato dell’intent e non autorizza retry
  ciechi.
- Una prenotazione con delivery confermata o ignota resta consumata anche dopo
  la scadenza del token; il rollover non sposta prenotazioni pregresse.
- I numeri mancanti restano unknown, non zero; `NaN`, infinito, overflow e
  booleani sono esclusi; i confronti percentuali con baseline zero non
  serializzano `Infinity`.

## Correzione minima complessiva prima del freeze

Non serve ridisegnare la roadmap. È sufficiente riallineare F5.2/F5.3 e D.8
alla norma già scritta, aggiungendo:

1. `promotion` all’enum e ai test F5.2;
2. un solo primitivo atomico domanda+token+budget+outbox prima del sender;
3. un reconcile owner/cadence per blocked, coda, rollover, expiry e
   `delivery_unknown`;
4. binding strutturato con `expected_row_version`, ricalcolo corrente e UNIQUE;
5. stato esplicito per denominatori vuoti e `cost_unverified` come veto;
6. bucket settimanale concorrente e ripresa tri-state da
   `waiting_dependency`.

Queste correzioni chiuderebbero le ambiguità individuate in questa review
parziale; non costituirebbero da sole prova G0.7/G0.8 né readiness.

## Follow-up sulle correzioni applicate

Riesame eseguito sul documento finale con SHA-256
`bd33c190ccf37e0e59ab8177cf9fa5ff0e7b6afe39162da9777208c0270ad9df`.
Il controllo esatto
`rg -c '^### 5\.6 Registro di politica: valori iniziali$'` restituisce **1**:
P8 non è riprodotto nel documento finale.

Nel solo perimetro P1-P7, le correzioni risultano chiuse senza regressioni
dirette osservate:

| Rilievo | Esito del riesame | Evidenza normativa finale |
|---|---|---|
| P1 | chiuso | F5.2 enumera `trial`, `activation`, `promotion`, esclude una receipt Birth propria del piano e aggiunge la matrice promotion (`:1582-1595`); D-F5.2 è allineata (`:2218`). |
| P2 | chiuso | F5.3 aggiorna prima la coda e usa un unico primitivo transazionale per binding, bucket, domanda, token e outbox; il sender parte solo dopo commit e aggiorna soltanto delivery (`:1597-1619`); D-F5.3 ripete lo stesso confine (`:2219`). |
| P3 | chiuso | Il reconcile core-owned parte a bootstrap, dopo commit di nuova evidenza, sul timer di policy e nel ciclo notturno; copre blocked, expiry, rollover e requeue con stato osservabile (`:520-529`, `:556`, `:1616-1631`). Con cap zero espone `question_budget_disabled`. |
| P4 | chiuso | `expected_row_version`, facts/effect digest e policy version sono strutturati; risposta e emissione ricostruiscono/rileggono il binding, il CAS verifica stato/versione e fatti/policy mutati invalidano il token (`:509-519`). D.8 prescrive PK, indice parziale su domanda aperta e storico (`:2296`). |
| P5 | chiuso | `cost_unverified` è veto tecnico in precedenza (`:346`) e F5.2 prova che non diventa consenso sostitutivo (`:1590-1595`). Denominatore zero, mediana vuota e campione insufficiente producono reason tipizzato prima della divisione; `need_completion_rate` ha denominatore definito (`:598-607`). |
| P6 | chiuso | D.8 congela bucket ISO UTC, reservation/question UNIQUE, stati e `BEGIN IMMEDIATE`; il cap corrente è verificato atomicamente e un cap ridotto non riscrive la spesa storica (`:2297`). L’indicatore 9 separa emissione logica, delivered, unknown e mai inviata (`:939`). |
| P7 | chiuso | Dopo `observe_effect`, soltanto assenza autorevole con start non irrevocabile oppure idempotenza attestata consente la ripresa della fase registrata; unknown resta sospeso e non consuma tentativi (`:453-467`). Il ritorno da `blocked` usa ora la risoluzione della causa, incluso campione insufficiente (`:436`). |

La verifica è ancora una review normativa parziale: non esercita codice,
migrazioni, concorrenza o fault injection e non costituisce G0.8 né readiness.
