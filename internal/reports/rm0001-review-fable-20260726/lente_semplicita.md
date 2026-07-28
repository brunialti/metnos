# Lente semplicita

## Verdetto in tre righe

Il nucleo (F0-F3 più FTS5) è proporzionato e ben difeso; il costo eccedente sta in tre punti:
il dominio esperienza cucito nella stessa roadmap pur condividendo col dominio utente «soltanto
tipi primitivi e l'adapter dell'embedder»; il derivatore LLM messo in sequenza prima di una misura
del bisogno; una specifica già 2,6 volte più grande di ogni altra roadmap, con 18 ADR aperte.
Con una scissione e due riordini, circa metà delle 2832 righe esce dal cammino critico senza
perdere alcun caso d'uso core (UC-01/02/03/09/10/11).

## Rilievi

### R1 — Due prodotti in una roadmap: l'esperienza executor va scissa
[PROVATO] RM-0001:2538-2541 «`experience_*` può condividere con il dominio utente soltanto tipi
primitivi e l'adapter dell'embedder, mai policy, tabelle o claim»; idem 633-634. Store, compilatore,
facciata, purpose, metriche, suite e fasi sono tutti separati per decisione fissata (§22, righe
2326-2328).
[OPINIONE] Se i due domini non condividono quasi nulla per costruzione, tenerli in un documento
non è integrazione: è somma. Il prezzo lo paga il core: F0 deve preregistrare anche la suite
d'esperienza (R2), §11.6/§12.5/§14.5/§18.6/F7-F8 gonfiano la specifica e ogni revisione futura
rilegge 2832 righe. La scissione in una RM propria RAFFORZA la decisione fissata di separazione,
non la riapre. Conseguenza: RM-0001 perde ~1/3 delle righe e F0 si dimezza.

### R2 — F0 contraddice la regola «ogni riga nasce soltanto nella fase indicata»
[PROVATO] §25 riga 2475-2476 fissa la regola; ma F0 (righe 1758-1759) impone «costruire suite
separate di acquisizione, consultazione, temporalità/conflitti/oblio ed esperienza» e (1755-1757)
preregistra effetto minimo e harm metrics del pilot F8, mentre lo store d'esperienza «non viene
creato prima di F7» (riga 1140) e §27.3 (2779-2783) ha respinto la creazione anticipata proprio
perché speculativa.
[OPINIONE] Lo stesso argomento anti-speculazione vale per la suite: corpus, doppia annotazione e
metodo accoppiato per un modulo dichiarato opzionale (§23, riga 2417) e NO-GO live (§27.4) sono
lavoro a fondo perduto se F7 non parte. Conseguenza: F0 congela solo le tre suite utente; la
suite d'esperienza si congela a un «F7.0» d'ingresso.

### R3 — Il grosso di §11-§12 serve la classe di dati meno autorizzata: F4 dietro misura, F5 prima
[PROVATO] Derivatore (§12.2), riconciliatore (§12.3), segnali comportamentali (§12.4) e il modello
evidenze multiple (§11.3) servono i claim `derived`/`behavioral`, che §13.1 colloca al 6° e 7°
posto su 7 nella precedenza (righe 1465-1466) e che §21 rende «candidato invisibile al
comportamento» (riga 2311). I casi core UC-01/02/03/09/10/11 (§23) sono coperti da W2+scope,
snapshot, CRUD esplicito, un ReferenceSlot e FTS5 — nessuno richiede il derivatore.
[OPINIONE] È la parte più costosa dell'impianto (coda, lease, ombra, riconciliazione, opt-in) per
la classe di conoscenza cui il disegno stesso concede meno effetto: valore per unità di complessità
minimo. F5 non dipende da F4 (il retrieval lavora sulle memorie esplicite di F2). Conseguenza:
ordine F0→F1→F2→F3→F5→(F4 solo se un contatore di lacune — turni in cui esplicito+riferimenti non
bastavano e un claim derivato avrebbe risolto — supera una soglia fissata in F0).

### R4 — §7.9: prima un registro deterministico di regole statiche, poi lo store d'esperienza
[PROVATO] La roadmap stessa dichiara la baseline «regola statica nell'executor» (riga 2228) e il
criterio «Se una regola deterministica risolve il gotcha, la memoria d'esperienza non ha dimostrato
utilità» (righe 2232-2233). Il caso guida UC-04 (righe 71-85, pannello da chiudere) è esprimibile
come regola statica in act_sites. Eppure F7 costruisce store, compilatore, attestazione, scrubber
e fingerprint HMAC prima di qualunque conteggio di gotcha non riducibili a regola.
[OPINIONE] Per §7.9 l'ordine va invertito: un registro versionato di regole statiche dentro
l'executor (costo quasi nullo, stessa autorità, stessa postcondizione) e F7 si apre solo quando N
gotcha contati non sono esprimibili come regola. Le review precedenti hanno stretto sicurezza e
causalità degli hint, non questo cancello d'ingresso. Conseguenza: F7-F8 diventano condizionali a
un numero, non a una posizione in sequenza.

### R5 — F3 non ha un primo caso nominato: il pilastro «riferimenti» è senza deliverable
[PROVATO] Il caso guida UC-02 (Atlas) è differito (righe 51-52) e l'uscita di F3 lo ribadisce
(righe 1800-1801); la scelta di slot e provider è ADR aperta (§22 punto 6, riga 2352). Si
progettano quindi `references.py`, ReferenceSlot e provider (righe 2491, 1795-1797) senza sapere
per quale oggetto.
[OPINIONE] §9.3 elenca già tipi con ID deterministici esistenti (persona/contatto, calendario,
righe 754-760): nominare ORA il primo slot (contatti è il candidato naturale: la disambiguazione
di destinatari è frequente e il registro esiste) costa una riga e dà a F3 un criterio di
completamento concreto. Conseguenza: senza questa scelta F3 è impianto per completezza e scivola
di fatto dietro F5.

### R6 — §7.9/§7.3: il rilevatore bilingue non aggancia `detection_lexicon` (rischio secondo riconoscitore)
[PROVATO] `grep -c detection_lexicon RM-0001` = 0. §12.1 (riga 1254) introduce «un rilevatore
bilingue limitato» e §25.1 (riga 2484) un `commands.py` con «riconoscimento bilingue ristretto»;
il meccanismo SoT esistente `runtime/detection_lexicon.py` (+ seed IT+EN) non è mai nominato. La
regola di progetto (memoria `feedback_no_hardcoded_synonym_lists`) vieta liste bilingue proprie e
impone il lessico; il Tutor lo usa già (`lex:tutor_gate.*`, CLAUDE.mutabile §11).
[OPINIONE] Senza il vincolo scritto, F0.4 produrrà quasi certamente un secondo riconoscitore
parallelo, aggravando il debito i18n a 2 locali già censito. Conseguenza: una riga in §25.1 —
«commands.py = concept nel detection_lexicon» — elimina un modulo di fatto.

### R7 — Il costo della specifica è esso stesso impianto: 2,6×, 18 ADR, 23 file di test, 7 artefatti per fase
[PROVATO] `wc -l internal/roadmap/*.md`: RM-0001 2832 righe contro 1074 (RM-0002) e 1345
(RM-0003). §22 elenca 18 ADR da chiudere (righe 2344-2369) — ~9% dell'intero registro storico
(0001-0199, 4 saltate, CLAUDE.mutabile §S). §25.5 nomina 23 file di test candidati; ogni fase
deve salvare 7 artefatti (righe 1973-1981); §25.4 elenca 24 micro-attività.
[OPINIONE] Nessuna riga è indifendibile da sola; è la somma che ha un costo di manutenzione e di
rilettura che concorre col budget del valore. La mitigazione non è tagliare invarianti ma R1+R3:
scissione e condizionamento riducono il documento vivo a ~1500 righe senza perdere una decisione.

### R8 — Undici tabelle e quote a sei dimensioni per un tetto di ~200 memorie
[PROVATO] §11.4 fissa «massimo iniziale di circa 200 memorie attive per utente» (riga 1075) e
quote «su righe e byte di evidenze, relazioni, coda, applicazioni, FTS ed embedding» (1078-1079);
§11.5 elenca 11 «tabelle minime» (1109-1121), fra cui `memory_relations` e `memory_embeddings`
che appartengono a F4/F5/F9, non al giorno 1.
[OPINIONE] State/memories/evidence/tombstones/ledger/queue/applications sono valore (oblio,
provenienza, audit dei gate a tolleranza zero). Relazioni ed embedding nelle «minime» sono
completezza: violano lo spirito di §25 (riga 2476). Le quote a 6 dimensioni si riducono a due
(righe totali, byte totali) per un corpus di 200 record. Conseguenza: schema iniziale a 8 tabelle
e una pagina di §11 in meno da testare in F1.

### R9 — §7.9: nel riconciliatore l'LLM deve essere irraggiungibile per i claim con slot
[PROVATO] §12.3 (righe 1315-1317) ammette «un modello per proporre la relazione» in generale,
mentre per claim con `slot_key`/`normalized_key` la relazione è già calcolabile senza modello:
stesso slot+valore diverso ⇒ `contradicts`; correzione esplicita ⇒ `supersedes` (§12.3 punto 5).
[OPINIONE] Manca una frase che renda il modello raggiungibile SOLO per claim privi di chiave.
Costa una riga e toglie dal perimetro di test avversariale l'intera classe «LLM propone relazione
sbagliata su dati slottati». Conseguenza: riconciliatore deterministico-prima esplicito, coerente
con §7.9.

### R10 — Tre meccanismi d'oblio dove ne bastano due
[IPOTESI] `deletion_epoch` (riga 982, «barriera minima per rebuild e restore»), tombstone per
evidenza (1046-1050) e `memory_deletion_ledger` (1119, 1098-1101) si sovrappongono: l'epoch è
derivabile come massimo del ledger, e il ledger più le tombstone coprono rebuild e restore. Non ho
trovato nel documento un caso che richieda l'epoch come contatore autonomo.
[OPINIONE] Un intero da mantenere è poco; un terzo concetto da specificare, testare (§18.1, §25.3)
e spiegare è meno poco. Conseguenza: se l'ADR di schema (§22 punto 1-2) non trova il caso, l'epoch
si definisce come vista sul ledger, non come campo.

## Che cosa toglierei / che cosa aggiungerei

**Impianto minimo che dà la maggior parte del valore** (parti di §9/§11 dentro):
§9.1 istantanea senza cache; §9.2 preferenze W2 con ambito; §9.3 con UN primo slot nominato
(contatti); §9.4-§9.5 consultazione tipizzata coi tre trigger; §9.6 quattro builtin dopo ADR;
§9.7; §11.1-§11.2; §11.3 ridotta (memories, evidence, tombstones); §11.4 con quote a due
dimensioni; §11.5 senza relations/embeddings al giorno 1; §12.1; §13 gradini 1-2 più FTS5;
§15; §16. Fasi: F0 (solo suite utente) → F1 → F2 → F3 → F5-lessicale.

**Fuori dal cammino critico** (restano nel progetto, dietro condizione contata):
tutto il ramo esperienza (§9.0 ramo destro, §11.6, §12.5, blocco Leiden di §12.6, §14.5, F7-F8)
→ roadmap propria con cancello «N gotcha non riducibili a regola statica» (R1, R4); derivatore e
riconciliatore (§12.2-§12.4, F4) → dietro contatore di lacune (R3); embedding/RRF/grafo (§13.2)
e F6/F9/F10/F11 → già opzionali per §23, nulla da cambiare.

**Aggiungerei** (righe singole, non moduli): il vincolo `detection_lexicon` in §25.1 (R6); il
nome del primo ReferenceSlot in F3 (R5); il confine deterministico-prima nel riconciliatore (R9);
la definizione del contatore di lacune in F0 (R3).

## Ciò che ho cercato e NON ho trovato

- [PROVATO] Un aggancio a `detection_lexicon`: 0 occorrenze in RM-0001 (grep), mentre il
  meccanismo esiste (`/opt/metnos/runtime/detection_lexicon.py`) ed è regola di progetto.
- [PROVATO] Il documento di design del 22/7 (`internal/design/design_user_data_subsystem_22_7.md`):
  rimosso come dichiarato in §2 (righe 234-236); ne sopravvive solo il sommario in MEMORY.md:21
  («2 tier … 4 fasi P0-P3»). La crescita 2 livelli/4 fasi → 3 archivi/12 fasi non è quindi più
  verificabile contro l'originale; RM-0001 non motiva da nessuna parte questo salto di scala.
- [PROVATO] Un criterio numerico per APRIRE F4: esistono soglie per promuovere i claim (F0,
  §19.5) ma nessuna condizione d'ingresso del derivatore; la sequenza lo attiva per posizione.
- [PROVATO] Il primo ReferenceSlot: §22 punto 6 lo lascia aperto e UC-02 è differito.
- [PROVATO] `runtime/memory/` non esiste (ls fallisce): coerente con lo stato «non implementato»
  dichiarato in testa; nessun costo sommerso da proteggere, la scissione R1 è ancora gratis.
- Una stima di sforzo per fase (giorni, righe, GPU): assente; il costo d'impianto è definito solo
  per enumerazione di attività, mai dimensionato. [OPINIONE] Per una roadmap di questa taglia è
  l'unico numero che manca davvero.
