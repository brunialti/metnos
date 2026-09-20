# RM-0010 — Integrità del vocabolario di affinità e ammissione degli strumenti montati

| Campo | Valore |
|---|---|
| Identificatore | `RM-0010` |
| Stato | `active`; non ancora `ready` |
| Creazione | `2026-09-20` |
| Ultima revisione | `2026-09-20` |
| Conservazione | persistente |
| Implementazione reale | nessuna fase iniziata. Sette difetti sono misurati nel codice e le fasi F0-F5 attendono approvazione |
| Origine e prove | revisione pubblica del prefiltro su r/LLMDevs, 18-20 settembre 2026; dieci osservazioni esterne registrate in §4; misure sul catalogo vivo in §2bis |

**Principio che governa il documento.** §0, una regola sola, uguale per ogni
tipo di executor. Ogni fase va misurata sul catalogo vivo.

## 0. Il principio che governa ogni scelta qui

**Una regola sola, uguale per ogni tipo di executor.** Nessuna eccezione per
collocazione, provenienza o autore.

Non è un'aspirazione di stile: è la lettura del difetto. `send_messages_github`
ha rivendicato `posta` e `inbox` **perché stava in un posto che gli dava
un'esenzione**, e perché il suo provider era un suffisso nel nome mentre per
Google è un argomento. Due meccanismi per la stessa cosa, e un controllo che
guardava dove sta il file invece di cosa dichiara.

Ne discendono tre conseguenze vincolanti, che hanno cambiato le fasi scritte
sotto:

1. **Nessun rimedio può introdurre un caso speciale.** Un controllo che vale
   per i sintetizzati e non per gli scritti a mano non è un controllo, è una
   preferenza.
2. **Nessun rimedio può presidiare un posto sbagliato invece di rimuoverlo.**
   Se una cosa sta nel posto sbagliato, la si sposta; non le si mette una
   guardia intorno.
3. **Un meccanismo per concetto.** Il provider è un argomento (ADR 0136), per
   tutti. Non esistono provider-suffisso «solo per questo caso».

## 1. Esigenza

La selezione degli strumenti in Metnos non avviene sui nomi, avviene
sull'**affinity dichiarata nei manifest**. Misurato sul corpus congelato di 234
query reali:

| superficie | query senza alcuna sovrapposizione col proprio strumento |
|---|---|
| solo il nome canonico | 230 / 234 (98,3%) |
| nome + affinity dichiarata | 32 / 216 (14,8%) |
| + anche la descrizione | 23 / 216 (10,6%) |

(216 e non 234 perché 9 strumenti non hanno più un manifest nell'albero
corrente; esclusi invece di contarli come zeri automatici.)

L'affinity è quindi **la vera superficie di selezione**, ma è anche la meno
governata del sistema: le cinque garanzie che impediscono a un generatore di
rompere il vocabolario operano tutte sui **nomi**, e tutte in fase di
generazione. L'unica collisione realmente occorsa in esercizio è avvenuta
sull'affinity, a tempo di query.

RM-0010 chiude quel divario e affronta il secondo caso scoperto dalla stessa
revisione: l'ammissione di strumenti provenienti da un catalogo che non
controlliamo.

## 2. I difetti misurati

Elenco dei guasti concreti che le fasi devono chiudere. Ciascuno è stato
verificato nel codice, non dedotto.

### 2.1 L'esenzione dal controllo di sovrapposizione scatta sul percorso

`runtime/loader.py::_check_affinity_overlap` esclude dal confronto a coppie
tutto ciò che `_is_imported()` considera importato. Quella funzione risponde
`True` per **tre** condizioni in OR, e la terza è soltanto
`is_skill_path(manifest_path)`, cioè «il manifest sta sotto `skills/`».

I 16 strumenti GitHub dichiarano `origin = "handcrafted"` e **nessuno** di essi
porta `provenance.imported_from` (verificato: 0 su 16). Vivono però sotto
`skills/github/`, quindi ereditano un'esenzione scritta per strumenti di terzi.
La motivazione riportata nella docstring — «il binding `provenance.imported_from`
qualifica esplicitamente il dominio remoto» — **non si applica a loro, perché
quel binding non esiste**.

Conseguenza reale: `send_messages_github` non è mai stato confrontato con il
dominio posta nativo, e una frase di affinity troppo generosa lo ha tirato
dentro query di spostamento mail.

### 2.2 Il confronto è per insiemi, non per termine

Lo stesso controllo usa Jaccard a coppie con soglia 0,5 sull'insieme completo
dei termini. Un **singolo** termine troppo generico dentro un insieme per il
resto distinto non muove il punteggio e non viene mai visto. Anche senza
l'esenzione di §2.1, quel confronto avrebbe lasciato passare l'incidente.

### 2.3 Solo i sintetizzati possono essere rifiutati

Il controllo rifiuta esclusivamente strumenti sintetizzati; un handcrafted non
viene mai rifiutato (priorità handcrafted-vince, ADR 0079). La regola è
difendibile per l'identità strutturale, ma significa che un termine di affinity
sbagliato scritto a mano non ha **alcun** cancello davanti.

### 2.4 L'elenco dei token non discriminanti è scritto a mano

`runtime/prefilter.py::_GENERIC_AFFINITY_VERBS` è una `frozenset` letterale di
circa 40 token, cresciuta un incidente alla volta (i commenti datati nel file lo
documentano). Deriverà inevitabilmente dietro al catalogo: un termine che
diventa generico dopo l'ennesimo membro di una famiglia resta nel punteggio
finché qualcuno non se ne accorge sul campo.

### 2.5 Le cinque garanzie sui nomi non guardano l'affinity

Grammatica GBNF sul vocabolario chiuso, sinonimi prima dell'estensione,
`naming_grammar.validate_name` contro i canonici vivi, un livello alla volta,
cancello di governance sul token nuovo: tutte e cinque operano sul **nome**.
Nessuna ispeziona i termini di affinity proposti.

### 2.6 L'importatore non è mai stato esercitato

Distribuzione reale dei 122 strumenti vivi: `origin` assente per 106,
`handcrafted` per 16; autori 84 Roberto, 37 «Metnos builtin maintainers», 1
`synt-multistage`. **Nessuno strumento del catalogo vivo è stato nominato da
qualcun altro.** L'importatore esiste, è documentato, e non ha mai lavorato su
un catalogo che non controlliamo.

### 2.7 L'estensione del vocabolario è solo proposta

Il marcatore `RICHIEDE estensione vocab §2.2` è imposto in
`runtime/telos_lenses/_base.py:60` e `runtime/naming_grammar.py:268`, e **30
proposte su 218** lo portano davvero. Nessun percorso di codice scrive
`vocab.py`: la decisione è manuale e non lascia traccia strutturata.

## 2bis. La misura, e cosa smentisce

Eseguita il 20/9 sui 122 manifest vivi, in sola lettura.

| grandezza | valore |
|---|---|
| termini di affinity distinti | 898 |
| rivendicati da un solo strumento | 580 |
| condivisi dentro una famiglia | 100 |
| condivisi **fra** famiglie | 218 |
| famiglie (oggetto canonico) | 33 |

**Il controllo proposto in §4.1, preso alla lettera, non si può applicare.**
Rifiutare ogni termine rivendicato da una famiglia diversa scarterebbe 218
termini su 898, e i primi sono `read`, `find`, `cerca`, `trova`, `delete`: in
un vocabolario compositivo i verbi **devono** attraversare le famiglie. È il
motivo per cui esiste già l'elenco dei 42 token non discriminanti.

**E soprattutto non avrebbe preso l'incidente che lo motiva.**
`send_messages_github` ha **un solo** termine in collisione fra famiglie
(`email`, con `contacts`). L'incidente vero fu contro `move_messages`, che è
**la stessa famiglia**: `messages`. L'asse non è la famiglia, è il **provider**.

Misurato dentro la famiglia `messages`, `send_messages_github` condivide con i
nativi `email`, `mail`, `messaggi`, `posta`, `messages`; con
`delete_messages_github` arriva a dieci termini fra cui `posta`, `inbox`,
`lettera` e `newsletter`. Sono parole del dominio posta su strumenti GitHub.
Non era un tag troppo generoso: è un insieme intero preso in prestito.

Coppie provider/nativo che oggi condividono termini: **10**. Vanno distinte
due cose che si somigliano:

- `find_files_hash` vs `find_files` che condividono `file`: **atteso**, il
  suffisso è un qualificatore, non un provider, e l'oggetto è lo stesso;
- `send_messages_github` vs `send_messages` che condividono `posta`:
  **difetto**, il suffisso è un provider e il termine appartiene al dominio
  dell'altro.

**Un terzo risultato, non cercato**: fra le collisioni residue compare
`remove`, 13 strumenti su 6 famiglie, che **non è** nei 42 token esclusi
(l'elenco ha `delete`, `cancella`, `rimuovi`, non `remove`). È la deriva di
§2.4 colta sul fatto, senza doverla ipotizzare.

### 2bis.1 Quanto costa togliere l'esenzione (F0)

Simulato il confronto a coppie su tutti i 122 strumenti, senza esenzioni:
**17 coppie** superano Jaccard 0,5. È un insieme rivedibile a mano, non
un'alluvione. Le prime dicono più del numero:

| Jaccard | coppia | lettura |
|---|---|---|
| 1,00 | `delete_entries` / `find_entries` / `write_entries` | affinity **identica** fra tre strumenti nativi |
| 1,00 | `read_issues_github` / `read_pulls_github` | identica |
| 1,00 | `set_issues_github` / `set_pulls_github` | identica |
| 0,71 | `delete_messages_github` / `send_messages_github` | la famiglia dell'incidente |
| 0,70 | `read_files_csv` / `read_files_xlsx` | stesso provider, formati diversi: **atteso** |

Nota che pesa: i tre `*_entries` sono **nativi**, quindi non erano esenti per
percorso. Venivano confrontati e mai rifiutati, perché §2.3 rifiuta solo i
sintetizzati. L'esenzione per percorso è un difetto, ma non è l'unico motivo
per cui nessuno si è accorto di nulla.

### 2bis.2 Il ricalcolo non può sostituire l'elenco a mano (F2)

Ricalcolando «escludi i termini rivendicati da più di *n* strumenti su più di
una famiglia»:

| soglia | token derivati | in più del manuale | **presenti nel manuale e persi** |
|---|---|---|---|
| n>4 | 50 | 33 | 25 |
| n>5 | 30 | 15 | **27** |
| n>6 | 25 | 10 | 27 |
| n>8 | 13 | 3 | 32 |

A ogni soglia il ricalcolo **perde** venticinque o più esclusioni guadagnate
sul campo. Quindi F2 va fatta in **unione**, non in sostituzione: derivati ∪
approvati a mano. Il suggerimento §4.3 va accolto così, o si butta via
conoscenza pagata con incidenti.

Secondo limite del ricalcolo per sola frequenza: fra i token che aggiungerebbe
ci sono `email`, `foto`, `documento`, `cartella`, `contenuto`. Sono i termini
**più** discriminanti per la loro famiglia, non i meno. Escluderli
peggiorerebbe la selezione. La frequenza da sola non basta: un termine che
nomina l'oggetto di una famiglia non va escluso, nemmeno se frequente.

### 2bis.3 Lo slot condiviso è la norma, non l'anomalia (F4)

Slot `verbo_oggetto` occupati da più di uno strumento: **11**. `read_files` ne
ha **sette** (`_csv`, `_doc`, `_github`, `_ocr`, `_spreadsheet`, `_xlsx`, e il
nativo). Quindi «finisce in uno slot già occupato» **non** è di per sé un
difetto, ed era formulato male nella prima stesura: la condivisione di slot è
il modo normale in cui vivono qualificatori e provider.

Il difetto è più stretto: lo strumento è irraggiungibile se, dato lo slot, il
criterio di spareggio lo colloca **sempre** dietro a un altro. Va misurato, non
dedotto dalla presenza di un omonimo.

### 2bis.4 Gli strumenti senza niente da sovrapporre sono quattro (§4.10)

Strumenti con **zero** termini distintivi dopo aver tolto verbi generici e
parole vuote: `admin`, `classify_entries`, `describe_entries`,
`extract_entries`. Sono quattro su 122, e tre sono esattamente gli aiutanti
universali già permanenti nel pool; `admin` è un verbo di sistema riservato.

L'osservazione di EvalRaccoonDev è quindi già applicata, ma per consuetudine.
Diventa una regola verificabile: **uno strumento senza termini distintivi o è
permanente nel pool, o non deve nascere.** Il caso 0,20 → 0,18 che descriveva è
questo, e nessuna riscrittura della descrizione lo avrebbe risolto.

## 3. Cosa esiste già (non ricostruirlo)

- **Due controlli di sovrapposizione affinity**: `skill_admission._affinity_overlap_check`
  all'import e `loader._check_affinity_overlap` al boot. Vanno corretti, non
  riscritti.
- **Il corpus congelato** di 234 query reali con verità di riferimento
  (`data/prefilter_corpus_snapshot.jsonl`), e il banco che lo esegue.
- **La grammatica chiusa e il validatore dei nomi**, che funzionano: 122 nomi
  su 122 conformi, inclusi i 16 sotto `skills/`.
- **Il marcatore di richiesta di estensione**, già emesso e già usato.
- **Le vie strutturali di instradamento**: nome esplicito risolto
  deterministicamente, suffisso di provider
  (`tool_grammar._PROVIDER_SUFFIX_MARKERS`), slot verbo/oggetto dall'intent
  extractor. Non dipendono dall'affinity, e sono il materiale della fase F4.

## 4. I suggerimenti ricevuti dall'esterno

Registrati perché sono la parte di RM-0010 che non viene da noi. Tutti
accettati salvo dove indicato.

1. **Conteggio per termine con controllo di famiglia** (nitish-kmr): prima che
   lo strumento esista, contare quanti strumenti vivi rivendicano già ciascun
   termine proposto, e rifiutare quelli rivendicati da una **famiglia diversa**.
   **Idea accolta, asse corretto dalla misura** (§2bis): per famiglia
   rifiuterebbe 218 termini su 898 e non prenderebbe l'incidente che la
   motiva. L'asse è il **provider**, non la famiglia. Resta vero il cuore del
   suggerimento, cioè trasformare i 30 casi di §2.7 da giudizio a elenco con
   motivazione.
2. **Collisioni per approvazione** invece di decisioni per settimana
   (nitish-kmr): la seconda misura quanto è occupato il processo, la prima se
   sta funzionando. Deve calare al crescere del catalogo e impennarsi la
   settimana in cui passa un termine generico.
3. **Ricalcolo dei token esclusi a ogni build** (nitish-kmr): qualunque termine
   rivendicato da più di *n* strumenti su più di una famiglia viene escluso
   automaticamente dal punteggio.
4. **Le distinzioni appartengono all'affinity, non al prompt** (nitish-kmr).
   Principio accolto. Il caso specifico portato a esempio non regge:
   `get_persons` e `read_persons` hanno affinity già disgiunte (registro ed
   enrollment da un lato, profilo e identità dall'altro) e il vincolo nel
   planner nasce da una confusione del modello, non del catalogo. La regola
   generale resta valida e va applicata ai casi futuri.
5. **Il costo si è spostato sull'onboarding, non è sparito** (QuanTradin):
   qualcuno deve scrivere l'affinity per ogni strumento montato.
   Osservazione corretta e da dichiarare.
6. **Rigiocare il corpus è una prova di non-regressione, non di copertura**
   (QuanTradin): uno strumento che nessuno può raggiungere supera quel
   controllo pulito.
7. **Un esito «non so» esplicito** (QuanTradin): meglio nessun verdetto che uno
   sicuro basato su una finestra di dati sottile.
8. **Un corpus costruito dalle descrizioni del server misura il generatore, non
   il recall** (QuanTradin): la scorciatoia è circolare e va vietata.
9. **Front-loading del segnale identificante** (EvalRaccoonDev): già rispettato
   dal formato `SCOPO / PATTERN / NON / OUT`; confermato, nessuna azione.
10. **Uno strumento senza token distintivi non deve passare dalla selezione**
    (EvalRaccoonDev): già applicato ai quattro aiutanti universali; da estendere
    come criterio esplicito invece che come consuetudine.

## 5. Fasi

### F0 — Il controllo di affinity non ha esenzioni

**Dove.** `runtime/loader.py::_check_affinity_overlap` e il gemello
all'import in `skill_admission`.

**Cosa.** Togliere **tutte** le esenzioni, non solo quella per percorso:

1. sparisce `is_skill_path` da `_is_imported` (esenzione per collocazione);
2. sparisce l'esenzione per provenienza: un legame `imported_from` dice da dove
   viene uno strumento, non che i suoi termini possano invadere un altro
   dominio;
3. sparisce l'asimmetria di §2.3, per cui solo i sintetizzati sono rifiutati e
   gli scritti a mano mai. Misurato: i tre `*_entries` **nativi** hanno
   affinity identica e non sono mai stati rifiutati per questo.

Una collisione è un difetto di chiunque l'abbia scritta. Se il rimedio vale
solo per una categoria, il difetto si sposta nell'altra: è esattamente quello
che è successo.

**Prove.** Le 17 coppie sopra soglia congelate in un riferimento; la prova
fallisce se ne compare una diciottesima non dichiarata. Un manifest handcrafted
sotto `skills/` non deve essere trattato diversamente da uno sotto
`executors/`.

**Sequenza.** Le 17 coppie vanno risolte **prima** di rendere il controllo
bloccante, altrimenti il boot rifiuta ciò che è già installato. Prima
osservare e riportare, poi rifiutare.

### F1 — Controllo per termine, sull'asse del provider

Riscritta dopo la misura di §2bis: la versione per famiglia è inapplicabile e
non prende l'incidente. L'asse giusto è il **provider**.

**Dove.** Un modulo nuovo, chiamato da `skill_admission` all'import e dallo
stadio 4 della sintesi. Non dentro `prefilter`: è un cancello di ammissione,
non di selezione.

**Regola, nella forma che la misura ha imposto.** Non «il termine è
rivendicato altrove», che segnala anche `file github` e `ls repo`, ma:

> su uno strumento con provider, **ogni tag di affinity deve portare almeno un
> token che la famiglia nativa non rivendica.**

Verbi generici e parole vuote non contano come token propri. Un tag che nomina
il provider si salva da solo, anche se condivide il resto.

1. Scomporre il nome in `verbo_oggetto[_qualificatore|_provider]`. Il suffisso
   è un **provider** se compare fra le chiavi di `vocab.PROVIDER_SKILLS`
   (`github`, `google_workspace`, `google_photos`), altrimenti è un
   qualificatore. **Non** usare `tool_grammar._PROVIDER_SUFFIX_MARKERS`:
   misurato il 20/9, è **vuoto**, quindi il filtro per suffisso che CLAUDE.md
   §2.2 descrive oggi non ha marcatori e non discrimina nulla.
2. Per ogni tag, calcolare i token propri: `token(tag) − generici − vuote −
   rivendicati_dalla_famiglia_nativa`. Se l'insieme è vuoto, **rifiutare il
   tag** e restituirlo nel motivo.
3. Su un qualificatore (`_hash`, `_doc`, `_ocr`) la regola non si applica:
   l'oggetto è lo stesso e la condivisione è attesa.

Fra famiglie diverse: **segnalare, non rifiutare**. I 189 residui di §2bis
contengono ancora rumore, e serve prima la soglia misurata di F2.

**Resa misurata sul catalogo di oggi**: 35 tag su 7 strumenti GitHub, di cui
12 su 15 per `send_messages_github` e altrettanti per
`delete_messages_github`, e **zero** falsi positivi su `file github`,
`ls repo`, `directory repo`, `leggi file github`. L'elenco completo, pronto da
eseguire, è in `internal/reports/github-affinity-cleanup-20260920.md`.

**Criterio di uscita, verificabile oggi.** Applicata al catalogo attuale, la
regola deve segnalare `send_messages_github` (`posta`, `mail`, `messaggi`,
`email`) e `delete_messages_github` (dieci termini, fra cui `inbox`,
`lettera`, `newsletter`), e **non** deve segnalare `find_files_hash`,
`read_files_doc`, `read_files_csv`.

**Sequenza.** Prima si ripulisce l'affinity dei due strumenti GitHub, poi si
accende il cancello. Accenderlo prima bloccherebbe l'import di ciò che è già
installato.

---

### F2 — I token non discriminanti derivati, in unione con quelli approvati

**Dove.** `runtime/prefilter.py::_GENERIC_AFFINITY_VERBS` diventa
`_generic_affinity_tokens(catalog)`, calcolata una volta al caricamento.

**Regola.** Escluso ogni termine che soddisfa **tutte** e tre:

1. rivendicato da più di *n* strumenti (misurato: **n = 5**);
2. che coprono più di una famiglia;
3. che **non** nomina l'oggetto di alcuna famiglia.

Il terzo criterio è la correzione che la misura impone: senza di esso il
ricalcolo escluderebbe `email`, `foto`, `documento`, `cartella`, `contenuto`,
cioè i termini **più** discriminanti delle loro famiglie (§2bis.2).

**Unione obbligatoria.** Il risultato è `derivati ∪ approvati_a_mano`. A ogni
soglia il solo ricalcolo perde 25-32 esclusioni guadagnate con incidenti reali.
L'elenco a mano non sparisce: smette di essere l'unica fonte.

**Uscita.** Aggiungendo un membro fittizio a una famiglia, il token condiviso
entra fra i derivati senza che nessuno lo scriva. `remove` (13 strumenti, 6
famiglie, oggi assente dall'elenco a mano) compare al primo calcolo.

---

### F3 — Collisioni per approvazione

**Dove.** Un registro accanto a quello delle proposte, non una metrica in
linea.

**Cosa registrare**, per ogni decisione di vocabolario: quante collisioni F1 ha
rifiutato nella finestra, quante proposte hanno richiesto un termine nuovo,
l'esito della decisione. Il rapporto fra le prime due è l'indicatore.

**Come leggerlo.** Deve **calare** al crescere del catalogo: un vocabolario che
si assesta produce meno collisioni per parola nuova. Un'impennata dice che è
passato un termine troppo generico, e indica la settimana in cui cercarlo.

**Cosa non registrare.** «Decisioni per settimana» come indicatore di salute:
misura il carico, non l'esito. Resta utile solo per dimensionare il lavoro.

---

### F4 — Ammissione degli strumenti montati

**Tre stati** al posto di due: `unvalidated`, `validated`, `quarantined`.

**Al mount, senza alcuna query.**

1. **Non-regressione**: rigiocare il corpus congelato di 234 query e rifiutare
   se una che risolveva correttamente cambia esito. Prova solida, e **prova
   solo questo**: uno strumento irraggiungibile la supera pulita.
2. **Raggiungibilità, formulata come va**: non «occupa uno slot condiviso»,
   che è la norma (11 slot, `read_files` ne ha sette), ma «dato lo slot, il
   criterio di spareggio lo mette **sempre** dietro». Si misura enumerando le
   coppie verbo/oggetto e osservando la posizione finale, non la presenza di
   un omonimo.
3. **Termini distintivi**: zero termini distintivi dopo le esclusioni significa
   che la selezione non lo raggiungerà mai. Oggi i quattro casi sono aiutanti
   permanenti (§2bis.4). Per un montato, zero termini è un rifiuto.

**Il corpus, solo dal lato della domanda.** Chiedere al proprietario tre
formulazioni con parole sue (il flusso di import si ferma già per le
credenziali), e riusare le query reali degli oggetti già serviti nativamente
cambiando il solo qualificatore di provider.

**Sonde sintetiche: solo confutazione.** Se non raggiungono lo strumento,
l'affinity è rotta. Se lo raggiungono, non si è imparato nulla e **non si
riporta alcun punteggio**. Costruire il corpus dalle descrizioni del server è
vietato: misurerebbe il generatore.

**Finché è `unvalidated`** non entra nel pool per affinity. È raggiungibile
solo per nome esplicito, provider dichiarato, o slot verbo/oggetto. È così che
guadagna i primi turni veri senza barare sulla selezione.

---

### F5 — L'istruttoria del vocabolario, non la decisione

**Oggi** esiste la proposta (30 su 218 la chiedono) e non esiste la decisione
tracciata: nessun percorso di codice scrive `vocab.py`.

**Cosa costruire.** Una richiesta di estensione diventa un record con: i tre
criteri compilati (necessario, generale, comprensibile), l'esito di F1 sul
termine proposto, i sinonimi già esistenti che sono stati scartati e perché.

**Cosa NON costruire.** La decisione automatica. Resta umana, e va registrata
con la motivazione. Si automatizza l'istruttoria, che è ciò che oggi costa e
che rende la decisione arbitraria quando il generatore accelera.

**Perché è l'ultima fase.** Senza F1 l'istruttoria non ha nulla da allegare,
e senza F3 non si sa se il ritmo è sostenibile.

## 6. Cosa resta non misurabile, e va dichiarato

- **La copertura di uno strumento montato al momento del mount.** I registri
  sono muti per costruzione: l'assenza di una capacità sopprime la domanda, e
  quindi l'assenza di lacune registrate non è assenza di bisogno. Per gli
  strumenti **sintetizzati** il problema non esiste, perché la query che
  giustifica lo strumento lo precede ed è già nei registri.
- **Se qualcuno formulerà davvero una richiesta in quel modo.** La
  raggiungibilità strutturale si dimostra, la raggiungibilità lessicale no. È
  esattamente ciò per cui esiste lo stato `unvalidated`.
- **Il caso del catalogo non nostro**, finché l'importatore non viene
  esercitato davvero. Il risultato del banco riguarda il nostro catalogo, non
  il recupero in generale.

## 6bis. Spostare GitHub nella sede naturale: tre strade, non una

Analisi richiesta il 20/9. Il presupposto era che i 16 strumenti GitHub
stessero nel posto sbagliato. È vero, ma «il posto giusto» ha tre letture con
costi molto diversi, e la terza scioglie il problema che F1 si limita a
sorvegliare.

### Cosa sono oggi

Vivono in `~/.local/share/metnos/executors/skills/github/`, cioè nei **dati
utente**, non nel repository: 16 executor, 80 file, 600 KB, non versionati.
Ognuno porta `manifest.toml.sig` e un record `.birth-control-<digest>`: sono
registrati nella catena di nascita. Il nome del record deriva dal
`contract_id`, non dal percorso, quindi segue la directory se si sposta.
Otto moduli del runtime li nominano per percorso o per nome.

### Il precedente che credevo esistesse, e com'è andata davvero

`PATH_SKILLS_BUILTIN = /opt/metnos/executors/skills` **esiste già** ed è
scansionata per prima dal loader. Dentro c'è `google-workspace`, tracciata da
git. Ma contiene **zero manifest**: solo `SKILL.md`, `README.md`, `references/`
e `scripts/` (`google_api.py`, `gws_bridge.py`).

Non esiste alcun executor `*_google_workspace`, in nessuna parte del sistema.
Google Workspace **non usa il suffisso nel nome**: il provider arriva come
**argomento**, e la skill conserva solo gli script ponte. È il modello che
`ADR 0136` prescrive («provider via client-arg, non executor-suffisso»).

**GitHub è l'unico rimasto sul modello vecchio.** La collisione di affinity che
ha causato l'incidente non è un caso sfortunato: è il sintomo di quella
deviazione.

### Strada (a) — spostarla nella sede builtin delle skill

`~/.local/share/.../skills/github/` → `/opt/metnos/executors/skills/github/`.

*Guadagno*: versionata, riproducibile, entra nella release. È ciò che la nota
su google-workspace chiedeva già di valutare.

*Costo*: 16 ripubblicazioni attraverso Birth, perché ogni manifest è firmato
(§7.10: non è un `mv`, è un `deploy --sign` per ciascuno). Le radici sorgenti
riviste e l'inventario Python firmato si spostano, quindi va fatta in un ciclo
di rilascio, non fra un rilascio e l'altro.

*Quello che NON risolve*: **l'esenzione dal controllo di affinity resta**.
`is_skill_path` copre anche la radice builtin, quindi spostarli non li fa
rientrare nel confronto. Serve comunque F0.

### Strada (b) — promuoverli a executor ordinari

`/opt/metnos/executors/<nome>/`, accanto agli altri 85.

*Costo nascosto e alto*: uscirebbero dal meccanismo delle skill, e con esso
perderebbero la dormienza in assenza di credenziali, il legame del form
credenziali, e la verifica del confine dei verbi all'import. Sono tre
proprietà che non si riottengono gratis. **Sconsigliata.**

### Strada (c) — allinearli al modello Google

I 16 nomi con suffisso spariscono; `github` diventa un **argomento provider**
sugli strumenti nativi corrispondenti, e della skill resta lo script ponte.

*Guadagno strutturale*: `send_messages_github` non esiste più, quindi non può
rivendicare `posta`, `inbox` o `newsletter`. **La classe di difetto che F1
sorveglia sparisce invece di essere sorvegliata**, e sparisce anche la metà
delle 17 coppie sopra soglia di §2bis.1 (`read_issues_github` /
`read_pulls_github`, `set_issues_github` / `set_pulls_github`,
`delete_messages_github` / `send_messages_github`).

*Costo*: il più alto dei tre. Tocca il planner (gli strumenti cambiano nome e
guadagnano un argomento), le cache L0/L1 (i piani memorizzati citano i vecchi
nomi), l'undo, e i 16 contratti vanno ritirati e i nativi ripubblicati.
Va trattata come una fase di roadmap a sé, non come un rimedio dentro RM-0010.

### Raccomandazione, secondo il principio di §0

**Solo (c).** (a) e (b) sono scartate, e (a) va scartata proprio perché a
prima vista conviene.

**Perché (a) è la trappola.** Sposta GitHub in una sede migliore e lascia in
piedi le due cose che hanno prodotto il difetto: un provider espresso come
suffisso mentre per Google è un argomento, e un controllo che guarda dove sta
il file. Si pagherebbe una volta per il trasloco e una seconda per la
riscrittura, tenendosi l'eccezione nel frattempo. È un presidio intorno al
posto sbagliato invece della sua rimozione: §0.2.

**Perché (b) è peggio.** Introduce una terza forma di provider, dopo
l'argomento e il suffisso: §0.3.

**(c) in concreto.** I 16 nomi con suffisso vengono ritirati; `github` diventa
un valore dell'argomento provider sugli strumenti nativi corrispondenti; della
skill resta lo script ponte in `executors/skills/github/`, esattamente come
`google_api.py` per Google. Da quel momento esiste **un solo** modo di
esprimere un provider, per tutti.

Cosa sparisce da sola, senza presidio: `send_messages_github` non esiste più,
quindi non può rivendicare `posta`, `inbox` o `newsletter`; cadono anche
`read_issues_github`/`read_pulls_github` e `set_issues_github`/`set_pulls_github`,
cioè cinque delle diciassette coppie sopra soglia.

**F0 e F1 restano comunque**, e non come ripiego. F0 perché il controllo senza
eccezioni è la regola generale, e vale per i tre `*_entries` nativi che non
c'entrano con GitHub. F1 perché il prossimo provider, qualunque sia, va
misurato prima di nascere, non dopo l'incidente.

**Ordine.** (c) è una fase di roadmap a sé e non entra in RM-0010: qui si
registra la decisione e il motivo. RM-0010 procede con F0 e F2, che sono
indipendenti da (c) e valgono per tutti gli executor allo stesso modo.

## 7. Criterio di uscita

RM-0010 si chiude quando:

1. il controllo di affinity **non ha esenzioni di alcun genere**: né per
   percorso, né per provenienza, né per categoria di autore; riporta anche le
   coppie che non rifiuta, e le 17 coppie oltre soglia sono congelate in un
   riferimento (F0);
2. un tag privo di token propri su uno strumento con provider viene rifiutato
   prima che lo strumento esista, col tag nel motivo; sul catalogo di oggi la
   regola rende 35 tag su 7 strumenti e **zero** falsi positivi su
   `file github`, `ls repo`, `leggi file github`, `find_files_hash`,
   `read_files_doc` (F1);
3. l'elenco dei token non discriminanti è `derivati ∪ approvati`, con la
   soglia n=5 e l'esclusione dei termini che nominano l'oggetto di una
   famiglia; `remove` compare al primo calcolo (F2);
4. esiste la serie storica di collisioni per approvazione (F3);
5. uno strumento montato attraversa i tre stati, e la sua raggiungibilità è
   misurata come posizione finale nello spareggio, non come assenza di
   omonimi (F4);
6. una richiesta di estensione arriva alla decisione umana come istruttoria
   completa, e l'accettazione è tracciata (F5).

**Ordine obbligato.**

- **F0 e F2 subito**: indipendenti da tutto, e valgono per ogni executor allo
  stesso modo, che è il punto di §0.
- **F1 dopo la pulizia dei 35 tag** (`internal/reports/github-affinity-cleanup-20260920.md`):
  accenderla prima bloccherebbe l'import di ciò che è già installato. La
  pulizia richiede una ripubblicazione firmata per i 7 strumenti toccati,
  quindi passa da un ciclo di rilascio.
- **F3 dopo F1**, perché senza rifiuti non c'è nulla da contare.
- **F5 dopo F1 e F3**, perché l'istruttoria allega l'esito dell'una e il ritmo
  dell'altra.
- **F4 indipendente**, ma inutile finché nessun catalogo esterno viene montato
  davvero.

**Fuori da RM-0010**: l'allineamento di GitHub al modello Google (§6bis,
strada (c)). È una fase a sé, ed è la sola che rimuove la causa invece di
presidiarla. Qui resta registrata la decisione e il motivo.
