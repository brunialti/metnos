# Review ADVERSARIALE del design dati-utente (auto-critica)

> **23/7/2026.** Oggetto: `internal/design/design_user_data_subsystem_22_7.md` (il mio stesso documento).
> Mandato: attaccarlo, non difenderlo. Ogni critica ancorata al codice (`file:line`) dove possibile — per
> non fare a mia volta «critica sulla sabbia». Verdetto in testa, poi reperti per severità, poi «cosa
> sopravvive» e «da risolvere PRIMA di costruire».

## Verdetto

Il design ha **un difetto architetturale profondo non affrontato** (collisione col motore deterministico +
cache dei piani) e **un residuo di auto-difesa**: continua a rivendicare per il sottosistema aperto (free-text)
i vantaggi — «verificabile, riproducibile» — che valgono solo per il vocabolario chiuso. Una volta che le
osservazioni sono prosa LLM, quei vantaggi **evaporano**: restano solo *locale* e *cancellabile*. Inoltre il
design **droppa proprio il meccanismo** (riconciliazione delle contraddizioni) che rende robusto il bersaglio
Honcho, pur dichiarando «parità». Non è inutile — T1 (prefs chiuse) e l'esplorazione reggono — ma il cuore
(T2 dialettico) va riprogettato su tre fronti prima di considerarlo «terra solida».

---

## CRITICI (invalidano il design se non risolti)

### C1 — Collisione col routing deterministico e la cache dei piani (il vero «sulla sabbia»)
**Claim attaccato**: «peer-card iniettata a OGNI turno nel contesto di planner» (§4.2/P0/P3) come miglioria innocua.
**Difetto (CONFERMATO)**: le chiavi cache sono **user-agnostiche** — L0 = `normalize_hash(query)` (`fastpath.py:228`),
L1 = `_compute_intent_sig(intent)` = verb|object+cluster (`autopath.py:289`), e `cache_validity.py:17` dichiara
esplicito «niente prefilter/affinity nella firma». Il routing è deterministico per costruzione («stesso piano 5/5»).
Iniettare un profilo per-utente nel contesto di planning crea un **dilemma senza uscita**:
- se il profilo NON entra nella chiave → su ogni **HIT L0/L1 il profilo è ignorato** (il piano cachato si rigioca
  identico) → la personalizzazione non ha effetto proprio sulle query frequenti (quelle cachate). Inutile dove serve.
- se il profilo entra nella chiave → la cache diventa **per-utente**, l'hit-rate crolla, e **L1 (piano di cluster
  CONDIVISO) si rompe** perché la sua generalizzazione è cross-utente per definizione. Si distrugge il motore.
- e comunque: due esecuzioni della stessa query dello stesso utente **routano diverso** dopo che il deriver ha
  imparato qualcosa → **si perde la riproducibilità del routing**, che è l'identità di Metnos.
Honcho non ha questo problema perché **ri-invoca l'LLM ogni turno** (nessuna cache di piani, nessun determinismo).
Il design ha ignorato che la personalizzazione del *planning* è in **tensione fondamentale** con cache+determinismo.
**Risoluzione**: il profilo può influenzare la **generazione/descrizione finale** (dove non c'è cache di piano),
NON il routing/planning; oppure la disambiguazione va fatta **prima** del lookup cache, riscrivendo la query
canonica (così la cache lavora sulla query risolta) — ma allora è un pre-processo, non «iniezione nel planner».
Da riprogettare esplicitamente.

### C2 — «Verificabile/riproducibile» è overclaim residuo una volta free-text
**Claim attaccato**: §1 «Metnos lo paga meglio (evidenza citata + riproducibile + cancellabile, dove Honcho è opaco)».
**Difetto**: nel momento in cui `user_observations.text` è **prosa generata dall'LLM** (P2), il vantaggio
«closed-vocab = verificabile» **sparisce**. `consult_profile` «cita l'evidenza» ma l'evidenza è un'altra riga di
prosa LLM → **catena circolare**: cita un fatto fabbricato come prova del fatto. L'unica verità di terra è
`source_turn_id` (il turno reale), che il design NON fa citare (cita `obs_id`). Quindi, sul sottosistema aperto,
Metnos è **verificabile e riproducibile quanto Honcho, cioè poco** — sopravvivono solo *cancellabile* e *locale*.
Il documento ripete i pregi del chiuso su un sottosistema che chiuso non è: è la stessa auto-difesa che mi era
stata contestata, in forma più sottile.
**Risoluzione**: derubricare l'advantage a «locale + cancellabile + tracciabile-al-turno»; far citare a
`consult_profile` il **turno**, non l'osservazione; smettere di chiamarlo «verificabile».

---

## MAGGIORI

### M1 — P0 sovrastimato: il tono non si può applicare alle risposte TEMPLATE
**Claim**: P0 «quasi-wiring», applica `tone/reply_length` a OGNI risposta.
**Difetto (CONFERMATO)**: `orchestration.py` ha **117 call-site `_msg`/template i18n** → gran parte delle risposte
finali (ricevute, esiti, errori) è **stringa templata deterministica**, non sintesi LLM. `reply_length=breve` non
ha alcun knob su un template. Il tono si può inclinare **solo** sulle uscite `describe` LLM (peraltro invocate solo
se `intent.verb` non è d'azione). Quindi P0 copre **una frazione** delle risposte, non «ogni turno», e non è wiring:
per estenderlo servirebbe rendere LLM-driven e tone-condizionata la generazione finale — cambio non piccolo.
Inoltre P0-standalone vale poco: le prefs vanno *impostate*, e l'impostazione automatica è P1 → «valore immediato» oversold.

### M2 — Riproducibilità di `consult_profile` = costo del processo monouso (nascosto)
**Claim**: `consult_profile` «riproducibile (temp0+seed)», «riusa describe-deterministico».
**Difetto (CONFERMATO)**: la byte-riproducibilità esiste **solo** via processo llama **monouso** (`describe_entries.py:97`);
l'HTTP server è «NON byte-riproducibile» (`:73`). `consult_profile` è un Q&A-su-retrieval, **forma diversa** dal
map-reduce di describe: o gira sul server (veloce ma **non riproducibile**), o adotta il monouso (riproducibile ma
**lento, uno alla volta**). Il «riusa describe-det» è asserito, non dimostrato per questa forma. Costo/latenza reali sottaciuti.

### M3 — Non-determinismo del retrieval + store che MUTA
**Claim**: «stessa domanda → stessa risposta».
**Difetto**: il ranking cosine ha **pareggi** (ordine dipende dal tiebreak, non specificato → serve `obs_id`), e lo
store **cambia fra i turni** (il deriver aggiunge/rinforza/decade). Quindi la riproducibilità è **per-chiamata a
store congelato**, NON nel tempo — anzi, è *giusto* che la risposta cambi quando impara. Il doc lo dichiara «riproducibile»
senza questa qualifica → fuorviante.

### M4 — Deriva/entrenchment silenzioso: manca la riconciliazione (proprio il pezzo che rende Honcho robusto)
**Claim**: silent-learn con `decay` + `superseded_by` = sicuro.
**Difetto**: il design **decade i non usati** ma **rinforza (`hits++`) gli usati**. Un'osservazione **sbagliata ma
usata** si **radica** (confidence sale), e nulla la corregge: `superseded_by` è un campo **senza processo che lo
popola**. Honcho invece ha il **pass-2 dialettico di riconciliazione delle contraddizioni** — esattamente ciò che
qui è droppato. Quindi il design è **strettamente meno robusto del bersaglio** mentre rivendica parità. Con
silent-learn (nessuna conferma) e nessun reconciler, un errore precoce può auto-rinforzarsi.
**Risoluzione**: un pass periodico che rileva contraddizioni fra osservazioni e le concilia/degrada — non opzionale.

### M5 — Il design VIOLA il proprio anti-pattern §4.6
**Difetto (contraddizione interna)**: §4.6 vieta «prosa free-form scritta dal modello nel prompt di sistema». Ma il
componente-1 (peer-card) inietta le **top-K osservazioni** — cioè **testo prosa LLM** — nel contesto del planner
(P0/P3). La distinzione «riga con provenienza, non blob» non salva: nel prompt ci finisce comunque prosa
model-authored. L'anti-pattern è violato dalla stessa iniezione che il design propone.

### M6 — Dossier per-utente + eredita gli authz aperti (privacy/sicurezza)
**Difetto**: `user_observations` è un **dossier concentrato, LLM-derivato**, su di te (da mail/calendario/file/foto/
posizione/contatti). Anche se locale, è un **nuovo bersaglio di valore** e una nuova superficie di leak. `consult_profile`
come executor **eredita i buchi authz** già trovati negli audit (`_actor` spoofabile, dialog/callback senza owner-binding)
→ **un guest potrebbe interrogare il modello dell'host**. §4.5 elenca guardrail ma **non l'access-control sullo store**.
E il deriver, silent, può scrivere fatti **medici/finanziari** (dai turni che toccano `ANALISI MEDICHE`/`UTENZE`) o
**segreti** (turni con `<REDACTED:cred>` — legge il raw o il redatto? non specificato) dentro `user_observations`.
Silent-learn = nessuna occasione di intervenire prima della scrittura.
**Risoluzione**: access-control esplicito (actor==user_id fail-closed) sul nuovo store e su `consult_profile`;
esclusione delle sorgenti protette/redatte dal deriver; niente derivazione da contenuti sensibili senza opt-in.

---

## MINORI / da specificare

- **m1 — Profilazione degli ospiti**: silent-learn è giustificato come «è il TUO self-model», ma Metnos è multi-utente
  (host + ospiti, incluso un minore). Profilare **silenziosamente un ospite** è un'altra cosa: la giustificazione
  «dati tuoi» non tiene. Il design applica la logica-proprietario a tutti uniformemente.
- **m2 — Sfratto dei fatti stabili**: una tabella + cap `confidence*recency` **sfratta i fatti stabili** (compleanno)
  che Honcho tiene *apposta* nel peer-card perché stabili. Confonde osservazioni volatili e fatti durevoli in una
  sola tabella con una sola policy di decadimento. Serve una corsia «stabile» esente da decay.
- **m3 — Trigger «on-demand» sotto-specificato**: chi decide che serve `consult_profile`? Se lo decide un LLM →
  altro punto non-deterministico e di latenza; se deterministico (miss del tier-1) → può scattare **spesso**,
  regressione latenza vs l'etica fastpath-0-LLM di Metnos. Budget non quantificato.
- **m4 — Non-verificabilità per costruzione**: non esiste ground-truth per «Roberto preferisce X» → **non puoi
  testare che `consult_profile` sia GIUSTO**, solo che citi obs esistenti, dichiari incertezza, sia deterministico
  a store congelato. Il design **importa un componente non-verificabile-per-costruzione** in un sistema la cui
  identità è la verificabilità. Va dichiarato come limite accettato, non nascosto sotto «riproducibile».

---

## Cosa SOPRAVVIVE (la review non demolisce tutto)
- **T1 prefs closed-vocab** come segnale di formattazione — MA applicato alla **generazione finale**, non al planning
  (per C1), e onesto sul fatto che copre solo le risposte LLM, non i template (M1).
- **Esplorazione «cosa sai di me?» + cancellazione** — genuinamente utile e onesta; è il vero controllo.
- **Locale + cancellabile** — advantage reali su Honcho (che è cloud).
- La distinzione **azioni ortogonali** (il self-model non autorizza; vaglio §2.11 invariato) — regge.

## Da RISOLVERE prima di costruire (checklist)
1. **C1**: decidere dove agisce il profilo — generazione finale e/o pre-riscrittura della query canonica prima della
   cache — MAI iniezione nel planner cachato. Senza questo, il resto è sabbia.
2. **C2/M5**: derubricare le rivendicazioni «verificabile/riproducibile»; citare il **turno** non l'osservazione;
   riconoscere che l'iniezione mette prosa LLM nel prompt (violazione del proprio anti-pattern) → o si accetta o si
   inietta solo la peer-card **chiusa** (prefs) e le osservazioni restano solo dietro `consult_profile` on-demand.
3. **M4**: aggiungere il **reconciler delle contraddizioni** (il pezzo Honcho droppato) — obbligatorio con silent-learn.
4. **M6**: access-control fail-closed sullo store + esclusione sorgenti sensibili/redatte dal deriver.
5. **m1/m2**: policy separata per ospiti e corsia fatti-stabili.
6. **M2/m3/m4**: dimensionare onestamente latenza/costo di `consult_profile` e dichiarare il limite di
   non-verificabilità.

**Nota di metodo**: questa review conferma il timore dell'utente («non progettare sulla sabbia») su un punto
sostanziale (C1) che il design entusiasta aveva mancato. Il bersaglio Honcho resta valido; la *via* proposta
va corretta prima di essere «terra solida».
