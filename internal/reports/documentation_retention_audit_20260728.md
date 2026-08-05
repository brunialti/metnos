# Verifica di conservazione della documentazione narrativa

Stato: decisione applicata; certificazione finale in corso, 28 luglio 2026.

## Criterio

Un documento pubblico collegato e organico al percorso di lettura non viene ritirato
solo perché esiste una fonte tecnica più recente. Le due fonti possono avere funzioni
diverse: la pagina tecnica definisce il comportamento corrente; un dialogo o un saggio
può invece rendere comprensibili motivazioni, tensioni e limiti del progetto.

Il ritiro è appropriato soltanto quando il documento riguarda un modulo non più
esistente, duplica integralmente una fonte corrente o induce il lettore a credere che
una possibilità progettuale sia già disponibile. Un vecchio nome pubblico può restare
esclusivamente in una regola di reindirizzamento, per non spezzare collegamenti esterni.

## Decisione sui documenti esaminati

| Documento | Decisione | Funzione nel percorso pubblico | Correzioni ammesse |
|---|---|---|---|
| `Metnos_Dialogo_v1.html` / `Metnos_Dialogue_v1.html` | conservare e indicizzare | dialogo filosofico su fini, limiti, giudizio e rapporto con l'utente | soltanto nomi superati, collegamenti, errori linguistici e affermazioni fattuali non più vere; preservare voce, ritmo e forma dialogica |
| `Metnos_Dialogo_Executor_v1.html` / `Metnos_Dialogue_Executors_v1.html` | conservare e indicizzare | spiegazione dialogica di executor, memoria associativa e crescita governata | stesso criterio; le pagine dei componenti restano la fonte tecnica corrente |
| `Metnos_Glossario_v1.html` / `Metnos_Glossary_v1.html` | conservare e indicizzare | ponte terminologico e punto di accesso agli approfondimenti | aggiornare definizioni e collegamenti; non trasformarlo in una copia del catalogo generato |
| `Metnos_Prospettive_Estese_v1.html` / `Metnos_Extended_Perspectives_v1.html` | conservare e indicizzare | esplorazione leggibile delle conseguenze e dei confini dell'architettura | distinguere sempre possibilità, ipotesi e funzioni attive |
| `Metnos_Prospettive_Giudizio_v1.html` / `Metnos_Perspectives_Judgement_v1.html` | conservare e indicizzare | critica dei limiti dell'autonomia e dell'antropomorfizzazione | mantenere il carattere critico; collegare i contratti correnti quando si citano funzioni concrete |

## Relazione con le fonti tecniche

I documenti narrativi non sostituiscono la Guida all'architettura, la guida
all'interfaccia, il catalogo generato degli executor o i manifesti caricati. Quando
descrivono una funzione concreta devono concordare con quelle fonti; quando ragionano
su una possibilità devono presentarla esplicitamente come tale.

I collegamenti dalla pagina iniziale e dal glossario sono parte del percorso pubblico.
I documenti conservati restano quindi `index, follow`, compaiono nella mappa del sito e
sono ammessi fra le fonti pubbliche del Tutor. Ogni loro modifica richiede la
ricompilazione completa e la nuova firma del catalogo Tutor.

## Verifiche necessarie per la certificazione finale

1. Controllare che italiano e inglese siano collegati alla controparte esatta.
2. Verificare assenza di nomi di moduli non più esistenti e di affermazioni presentate
   come implementate senza riscontro nel prodotto.
3. Convalidare indicizzazione, mappa del sito, collegamenti e ancore.
4. Ricompilare e verificare il catalogo Tutor dopo l'ultima modifica.
5. Conservare i reindirizzamenti storici soltanto quando proteggono URL già pubblici;
   non riproporre quei nomi nella navigazione o nei contenuti correnti.

## Esito

Il precedente piano di ritiro è superato. Dialoghi, glossario e prospettive rimangono
parte della documentazione pubblica. Saranno eliminati soltanto documenti realmente
orfani riferiti a moduli cessati, dopo avere verificato che nessun percorso corrente li
colleghi e che gli eventuali URL pubblici continuino a risolversi tramite
reindirizzamento.
