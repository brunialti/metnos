# Review adversariale di RM-0001 — intelligenza forte, semplice, automatica

Fase C (sintesi) — 27/7/2026. Riferimenti di sezione = revisione corrente di
`internal/roadmap/RM-0001-conoscenza-utente-locale.md` (1286 righe, riscritta il
26/7 alle 23:18, DOPO la consegna delle sette lenti). Fatto preliminare che
governa tutta questa sintesi: [PROVATO, `refutazione.md`] la riscrittura ha
recepito 63 dei 70 rilievi delle lenti, l'audit di fase B ha verificato riga per
riga che ogni «accolto» del §16 ha un'attuazione reale nel testo, e gli 8
rilievi restanti sono caduti sotto attacco (due per premessa fattuale falsa alla
rilettura di codice e TurnLog). Nessun rilievo originale sopravvive come
scritto; sopravvivono cinque difetti nuovi introdotti dalla riscrittura.

## Verdetto

[OPINIONE, su riscontri di fase A/B] La revisione corrente raggiunge lo scopo
«forte, semplice, automatica» **come specifica**: forte perché sei capacità
cambiano ciò che Metnos fa (default operativi, riferimenti, episodi, routine
ellittiche — §0, UC-01..12) con una prova a tre bracci che può bocciarle
(§11.2, inv. 30); semplice perché 1286 righe, sei fasi, otto ADR, deterministico
prima (inv. 18); automatica perché promozione silenziosa e nessuna approvazione
per elemento (§7.3, inv. 20). Dove no: un'incoerenza di fase rende F4 non
chiudibile com'è scritta (rilievo 1) e quattro sottospecifiche minori
lasciano promesse senza meccanismo dichiarato. Tutte riparabili in poche righe;
niente richiede un ridisegno.

## Verdetto su Leiden

**Non serve** — e la revisione corrente lo ha già rimosso correttamente (§3.2,
preambolo r. 21, §14); resta solo da NON riaprirlo.

- **La decisione che abiliterebbe:** nessuna nuova. [PROVATO, lente leiden 1]
  Nella revisione 2832 tutte le 27 occorrenze di «Leiden» portavano a un solo
  artefatto — report/ChangeIntent revisionabile — e quel canale esiste già in
  produzione con raggruppamento deterministico: ADR 0180/0185 +
  `runtime/telos_proposals_store.py:172` (`annotate_clusters`), `:233`
  (`cluster_score`), `:246` (`recompose_clusters`) — righe riverificate da me
  il 27/7. Un algoritmo di comunità che non alimenta decisioni nuove è
  ornamento, e un secondo canale verso lo stesso artefatto.
- **La scala che lo giustificherebbe:** assente per ordini di grandezza.
  [PROVATO] Il grafo relazionale reale dell'istanza (mnestoma) ha **17 archi e
  1034 eventi dopo due mesi** (`workspace/.mnestoma/mnest.sqlite`, contati da
  me il 27/7); il tetto era ~200 memorie/utente. Leiden si distingue dalle
  componenti connesse su reti empiriche grandi (Traag et al. 2019,
  arXiv:1810.08473: la garanzia di comunità connesse vale contro il 16-25% di
  comunità difettose di Louvain **su grafi grandi**); a 10^2-10^3 nodi la
  connessione si verifica in microsecondi. In più `leidenalg` e `igraph` non
  sono installati (ModuleNotFoundError, rieseguito il 27/7): sarebbe una
  dipendenza C nuova per un modulo a esito pre-scritto, più un harness
  multi-seed/VI/ARI che serve solo a sorvegliare una stocasticità che le
  alternative non hanno.
- **L'alternativa più semplice:** componenti connesse sulla proiezione
  positiva (deterministiche, connesse per costruzione, `networkx` già
  presente) + raggruppamento per chiavi + sequence mining per i workflow —
  che sono sequenze **ordinate** (`followed_by`): una comunità di
  co-occorrenza butta via proprio l'ordine che rende un workflow proponibile
  [PROVATO, lente leiden 9]. La baseline non era un termine da battere: era la
  soluzione.
- **Riapertura:** la condizione qualitativa di §3.2 (raggruppamento
  deterministico fallito su casi documentati + scala reale) è un doppio
  cancello corretto; la soglia numerica «~10^4 nodi» proposta dalla lente è
  caduta in fase B come pseudo-precisione — giusto così. [PROVATO,
  `refutazione.md` «Caduti sotto attacco»]

## 1. I rilievi che contano

Sono i cinque difetti della revisione 23:18 sopravvissuti alla refutazione,
in ordine d'importanza, più due minori. Nessun rilievo delle lenti originali
sopravvive come scritto.

1. **Incoerenza di fase: UC-05 preteso verde in F4, ma `relations` nasce in
   F5.** [PROVATO] F4 esige «UC-05 … verdi» (r. 986); UC-05 esige «una
   decisione successiva crea `supersedes`» (r. 106); §6.2 crea `relations`
   «soltanto nella fase che le usa» = F5 (r. 534); §6.4 rende le relazioni la
   fonte canonica e la decisione di UC-05 è memoria libera, senza via
   alternativa tipizzata. Lo stesso vale per §8.1 punto 5 (relazioni a un
   salto nell'ordine di recupero, che nasce in F4). Confermato da due
   attaccanti indipendenti in fase B. Conseguenza se non si fa niente: F4 non
   è chiudibile com'è scritta — la prima fase con retrieval fallirebbe la
   propria uscita per costruzione, o verrebbe chiusa barando.
2. **Il candidato di disambiguazione fabbricato da contenuto esterno ha la
   contromisura ma non la prova.** [PROVATO per assenza] §9 elenca la minaccia
   (r. 787), ma il corpus §11.1 copre trappole di riferimento *cambiato*,
   attacchi da *testo* e ID *riciclato* — non la cartella `atlas` piantata sul
   NAS *prima* della conferma. In più la riga di recepimento del rilievo manca
   dal registro §16, che si dichiara completo. Conseguenza: il modo di guasto
   più realistico dei riferimenti (chi scrive su uno share condiviso governa i
   candidati) non verrà mai misurato dai criteri bloccanti.
3. **L'oblio non specifica il destino del claim gemello già compilato.**
   [PROVATO per assenza + IPOTESI] §6.5 punto 4 invalida gli eventi *non
   completati*; un evento con lo stesso contenuto già COMMITTED prima del
   delete è ormai una memoria attiva distinta, e §6.5 punto 1 non dice con
   quale ampiezza il target si risolve in ID (per chiave? per contenuto? solo
   l'ID indicato?). UC-10 promette che la coda «non può ricreare il dato» —
   il gemello già compilato resta fuori dalla promessa. Conseguenza: un
   «dimentica» può lasciare vivo un duplicato semantico e UC-10 passerebbe i
   test restando falso nello spirito (§2.8).
4. **La corsa dell'enqueue non è dichiarata serializzata.** [IPOTESI su testo
   provato] Il lock di §6.5 copre allocazione epoch, cancellazione e
   compilazione — non l'enqueue: un evento inserito fra i punti 4 e 6 con
   epoch stantio congelato sfuggirebbe allo sweep. La mitigazione esiste
   probabilmente per costruzione (`deletion_epoch_at_enqueue` in §6.3,
   ricontrollato dal compilatore) ma il confronto non è mai dichiarato.
   Conseguenza: una garanzia di oblio che dipende da un dettaglio
   implementativo non scritto.
5. **`PreferenceSpec` è citata (§6.4, §9) e mai definita.** [PROVATO per
   assenza] In un documento che mappa ogni struttura a una fase (§6.2, §13) è
   l'attuazione a metà del recepimento «W2 e memoria libera cieche»: senza
   dire dove vive e chi la compila, il conflitto cross-store resta una
   promessa. Conseguenza: F5 la reinventerà a piacere dell'implementatore.

Minori (nessuna decisione richiesta): la numerazione salta da §19 a §28
(residuo della revisione vecchia — verificato sul file corrente);
`agent_runtime.py:5832-5833` porta un commento che promette un retry che il
corpo non esegue — igiene da assorbire quando F0 ridisegna quel dispatch
[PROVATO, fase B].

## 2. L'impianto minimo forte

La riscrittura ha già eseguito il grosso della potatura che le lenti
chiedevano (esperienza executor, Leiden, dense, MCP fuori — §3.2; tabelle
create per fase — §6.2; quote a due grandezze — §6.6). Il minimo che segue è
quindi una selezione *dentro* F0-F6, non un secondo disegno. (Il mandato
citava «§9/§11» della revisione 2832: nella revisione corrente quel contenuto
vive in §5-§8 e §11-§12.)

**Dentro, indispensabile:**

- **F0** (§5.1-5.2, §13): principale canonico fail-closed. [PROVATO, lente
  fondamento 2-3, riscontri confermati in fase B] Oggi l'identità viene
  fabbricata verso `host` in più punti (`actor_resolver`, `_resolve_actor`,
  dispatch builtin con `kwargs["actor"] = actor or "host"` a
  `agent_runtime.py:5803`); senza F0 ogni altra riga del documento poggia sul
  vuoto, e l'invariante 1 è irrealizzabile.
- **F1** (§6.1-6.2, §6.5): store esatto e oblio con journal separato. È
  l'unica parte che DEVE essere giusta prima che qualcosa impari: un oblio
  rotto non si ripara a posteriori.
- **F2** (§5.9 + cablaggio di `reply_length`/`tone`/`units`): il primo delta
  «forte» misurabile. [PROVATO] La tabella §2 ammette che oggi il percorso
  risposta non consuma quelle preferenze (solo `sites_*` e `lang`,
  `agent_runtime.py:6221-6231`, riscontro della lente ambizione confermato in
  fase B): UC-01 è cablaggio nuovo a costo basso e beneficio visibile dal
  primo giorno.
- **F3** (§5.4-5.6): ReferenceSlot `project` su `project_paths.json` + un
  default operativo dichiarato `[personalization]`. È il cuore deterministico
  che cambia le *azioni* (riempimento argomento dopo il piano, zero LLM,
  §7.9-conforme) — la classe di richieste che più distingue un assistente che
  conosce il suo utente.
- **F5** (§7): apprendimento implicito con promozione silenziosa. È il
  requisito «automatica» in persona; senza F5 il sistema è uno schedario che
  l'utente compila.

**Ridotto:**

- **F4 a metà:** dentro `episode_refs` + lookup esatto + FTS5 (UC-05/06,
  deterministici, alimentati da `CallbackOutcome`); **fuori la composizione
  LLM delle risposte aggregate** (§5.8): l'inventario deterministico di UC-09
  copre quasi tutto UC-08, e per §7.9 il compositore entra solo quando
  l'elenco citato si dimostra insufficiente su casi reali. È l'unico punto
  del nucleo dove il documento mette un LLM e basta (per ora) codice
  deterministico. [OPINIONE] Il resto del documento è già deterministico dove
  può esserlo (inv. 18-19, §7.3 «LLM soltanto quando il parser non basta»);
  il compilatore F5 sui claim liberi è LLM per necessità (testo libero) e
  correttamente ingabbiato (schema chiuso, ombra, reconciler
  deterministico-prima). Non ho trovato il caso inverso — determinismo dove
  servirebbe un modello.

**Dentro, ma ultima e spegnibile (com'è già):** **F6** (§5.7, routine
ellittiche). È la promessa più forte («richieste oggi non comprese») e
l'unica che tocca il pre-cache: giusto tenerla, giusto che sia in coda,
dietro corpus di richieste fallite e con interruttore che lascia F0-F5
integre (§12 F6).

**Il confine, dichiarato:** con questo minimo NON si ottengono: richiamo
semantico oltre FTS (una parafrasi lessicalmente lontana non trova il
claim — è il prezzo di niente-dense finché il corpus congelato non mostra
lacune, §8.1); sintesi aggregate ricche (UC-08 risponde da elenco citato, non
da prosa); routine con più di un candidato compatibile (chiede o si astiene,
§5.7); qualunque apprendimento di strategia degli executor (fuori perimetro,
§3.2); e la memoria non cambia mai piano, tool o autorità (§8.3, inv. 5) — la
promozione della fiducia sugli effetti resta nei mandati e nell'autopath ✓
(ADR 0190/0185), fuori dalla memoria.

## 3. L'automatismo: dove l'utente lavora, e dove non dovrebbe

[PROVATO, censimento di fase B sul documento corrente] A regime restano
cinque famiglie di interazione più una tantum. Il verdetto è che il documento
ha già raggiunto il confine dell'automatico: **nessuna va resa silenziosa**,
e nessuna va aggiunta.

| Interazione | Dove | Perché resta |
|---|---|---|
| prima conferma di un riferimento ambiguo | UC-03, inv. 15 | scegliere in silenzio senza fatto confermato = falso successo §2.8; l'isteresi §7.6 garantisce che si paghi una volta sola |
| domanda su conflitto irrisolto | inv. 17, UC-05 | la fusione silenziosa di verità incompatibili è il danno peggiore di una memoria |
| controllo del piano su mutanti/outbound | inv. 14 | è il gate del motore, esistente con o senza memoria; la memoria vi *aggiunge visibilità* delle sorgenti risolte, non un gate nuovo [PROVATO, attacco ad ambizione-6 in fase B] |
| chiarimento su mutazione memoria in richiesta composta ambigua | §5.9 | eseguire una mutazione parziale su confine incerto è peggio della domanda |
| opt-in individuale ospiti | §7.7, inv. 21 | dati di terzi, non del proprietario |
| una sola informativa all'attivazione | §7.1 | una tantum; sostituisce ogni approvazione per elemento |

Ciò che le lenti chiedevano di rendere silenzioso lo è già diventato nella
riscrittura: promozione silenziosa delle preferenze non sensibili con soglie
preregistrate (§7.3), apprendimento implicito attivo per il proprietario alla
promozione di F5 (inv. 20, §7.1), «cosa sai di me?» dalla chat senza pagina
amministrativa (UC-09, inv. 29), isteresi sulla ri-domanda (§7.6). Il
backfill del già-detto resta correttamente escluso: [PROVATO, fase B] sui
~9.000 turni reali il TurnLog non ha principal e `actor` è `host` nel ~75%
dei casi — un backfill fabbricherebbe l'identità (minaccia §9 r. 784).

## 4. La prova che distingue «serve» da «è presente»

Il documento la possiede già in §11; qui la riduco alla forma minima e
indico l'unico rinforzo necessario. [OPINIONE sul disegno; struttura PROVATA
nel testo §11.1-11.3]

- **Corpus** (congelato in F0, prima di vedere il codice): le 94 voci di
  §11.1 (30 coppie sorgente-beneficio separate da una sessione, 30 esche a
  profilo irrilevante etichettate nel corpus, 10 trappole di riferimento
  cambiato, 12 cross-principal, 12 attacchi da testo esterno) **più ~5 casi
  «candidato fabbricato da contenuto esterno»** (rilievo 2 — è l'unica
  aggiunta). Ogni caso porta referente/valore atteso indipendente dall'esito
  executor: mai giudicare contro la postcondizione, che riesce anche sul
  referente sbagliato.
- **Tre bracci sullo stesso replay e snapshot** (§11.2): (1) memoria spenta;
  (2) baseline lineare = ultimo valore esplicito per slot, senza compilatore
  né retrieval; (3) impianto della fase.
- **Numero primario:** interazioni dell'utente necessarie all'esito corretto,
  delta accoppiato (3)−(2) con effetto minimo, seed, potenza e regola di
  arresto preregistrati in F0 (§11.2). Riduzione passi = secondaria, mai
  sufficiente.
- **Soglia di bocciatura — l'esito che può fallire:** se il braccio 3 non
  batte il braccio 2 dell'effetto minimo preregistrato, la fase NON si
  promuove; e se questo accade per F3 o F5, l'esito onesto è **consegnare la
  baseline lineare** (un dizionario ultimo-valore-per-slot è ~cento righe
  deterministiche) e fermarsi lì. Battere «nessuna memoria» non giustifica
  niente: il confronto che conta è col dizionario.
- **Danni:** criteri bloccanti §11.3 a zero osservato con denominatore
  dichiarato e limite 3/N — incluso il falso riferimento silenzioso sulle
  trappole e sui nuovi casi fabbricati.
- **Per F6:** quota del corpus congelato di richieste ellittiche *oggi
  fallite* che diventano comprese (§15), sopra la baseline lineare. Un numero
  solo, e risponde per sempre alla domanda «più forte di prima?».

Non propongo numeri di soglia: §14 li congela in F0 dopo la baseline e prima
del test, ed è la sequenza giusta — un numero inventato oggi sarebbe
pseudo-precisione.

## 5. Che cosa cambierei in RM-0001

1. **§6.2 + §12/F4 — riscrivere** (rilievo 1): anticipare a F4 le
   `relations` limitate al `supersedes` deterministico su chiavi tipizzate
   (riga tabella: «`relations` — F4 (solo supersedes deterministico), estesa
   in F5»), allineando §8.1 punto 5; in alternativa spostare UC-05
   nell'uscita di F5. Raccomando l'anticipo: il supersedes deterministico è
   già specificato chiuso in §6.4 e UC-05 è caso core.
2. **§11.1 — aggiungere** (rilievo 2): la categoria di trappole «candidato di
   disambiguazione fabbricato da contenuto esterno prima della conferma»
   (~5 casi, share/NAS/contatti sincronizzati); **§16 — aggiungere** la riga
   di recepimento mancante del rilievo danno-4, perché il registro si
   dichiara completo e non lo è.
3. **§6.5 punto 1 + UC-10 — aggiungere** (rilievo 3): specificare l'ampiezza
   della risoluzione del target (per chiave tipizzata quando esiste; per i
   claim liberi l'ID indicato) e il destino del gemello già compilato: o
   entra nella cancellazione per chiave, o l'inventario post-oblio lo mostra
   esplicitamente. Vedi decisione 2 sotto.
4. **§6.5 — aggiungere una frase** (rilievo 4): «il compilatore confronta
   `deletion_epoch_at_enqueue` di ogni evento con l'epoch autorevole dentro
   la transazione finale; un evento con epoch arretrato non promuove» —
   chiude la corsa dell'enqueue fuori dal lock.
5. **§6.4 o §13 — aggiungere** (rilievo 5): definizione di `PreferenceSpec`:
   proposta — derivata a runtime dalle chiavi W2 e dalle dichiarazioni
   `[personalization]` firmate (nessun terzo registro), compilata dal filtro
   di F5; una riga nella tabella §13.
6. **§19/§28 — riscrivere** (minore): rinumerare l'appendice «Risposta
   Reddit» da §28 a §20.
7. **§0 o §14 — aggiungere una riga** (residuo sotto soglia di fase B):
   citare ADR 0185/0190 come percorso già esistente di consolidamento della
   fiducia sugli effetti (autopath ✓, mandati), così il confine «la memoria
   non promuove autorità» resta leggibile senza sembrare un vicolo cieco.
8. **F0 — aggiungere al censimento** (minore): la sistemazione del commento
   mendace di `agent_runtime.py:5832-5833` dentro il ridisegno del dispatch
   builtin già previsto (§13, r. 1046-1050).

## 6. Decisioni che spettano a Roberto

1. **Ampiezza dell'oblio (rilievo 3).** (a) Risoluzione stretta: si cancella
   solo l'ID indicato — oblio minimale, ma un gemello semantico già compilato
   può sopravvivere a un «dimentica»; (b) risoluzione per chiave/contenuto —
   oblio che mantiene la promessa di UC-10, ma può cancellare più di quanto
   l'utente intendeva. Raccomando (b) per le chiavi tipizzate e (a) con
   inventario post-oblio visibile per i claim liberi: la promessa resta
   onesta senza cancellazioni a sorpresa.
2. **Compositore LLM delle risposte aggregate (§5.8, UC-08).** (a) Dentro F4
   come da documento; (b) rimandato a dopo un caso reale in cui l'elenco
   deterministico citato non basta. Raccomando (b): è l'applicazione diretta
   di §7.9 e toglie dal cammino critico l'unico LLM non necessario del
   nucleo.
3. **Partenza.** (a) Ratificare ADR 0200 e aprire F0 ora; (b) tenere RM-0001
   `ready` finché la Fase 8 (stress logico) non è avviata o chiusa. La
   specifica è pronta — i cinque rilievi sono correzioni da poche righe, non
   blocchi; la priorità fra RM-0001 e Fase 8 è una scelta di prodotto che non
   mi spetta. Raccomando di ratificare l'ADR subito (fissa le decisioni) e
   decidere separatamente il calendario di F0.

Leiden non è fra le decisioni: è già risolto nel documento e il verdetto
sopra lo conferma — non serve, non serve dopo a scala prevedibile, e la
condizione qualitativa di riapertura in §3.2 è quella giusta.

## 7. Limiti di questa review

- Le sette lenti hanno esaminato la revisione da 2832 righe, riscritta prima
  della fase B: i loro numeri di riga verso RM-0001 non sono più
  riverificabili (nessuna copia su disco). I riscontri verso il **codice**
  sono stati ricontrollati tutti in fase B (28 verifiche, 1 smentita, 4
  imprecisioni, documentate in `refutazione.md`); in fase C ne ho rieseguiti
  in proprio solo quattro (leidenalg/igraph assenti, funzioni di
  `telos_proposals_store`, conteggi mnestoma 17/1034, occorrenze `supersedes`
  in RM-0001).
- L'audit «ogni accolto di §16 ha attuazione reale» è della fase B; ho
  verificato sul documento corrente le attuazioni che cito, non tutte le 63.
- Il sistema non è implementato: ogni giudizio è sulla specifica, nessun
  turno reale né misura di beneficio è stato possibile per costruzione.
- Non ho rieseguito l'audit Swafra (ricalcolo 92,34% vs 99,6%) né verificato
  in rete le fonti arXiv citate: le uso come riportate in §17 e nelle lenti.
- Nessun carico GPU e nessun riavvio di servizi, come da mandato: i test
  citati sono letture e `python3 -c` puntuali.
