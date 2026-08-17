# V26.5.6.8 — contratto tipizzato per relazione, checkpoint OFFLINE

Status: **author checkpoint offline**, self-test **22/22 PASS** più la suite
V26.5.6.6 eseguita per intero e ancora verde. `inference_allowed=false`, zero
rete, zero chiamate modello, nessun gate creato o consumato.

Replay:

```bash
/usr/bin/python3 -I -B \
  internal/tools/request_analysis_lab/candidates/v26568/metnos_v26568_offline_selftest.py
```

Circa 70 s, deterministico (seme `20260810`).

## Perché esiste: il live del 10 agosto è stato un esperimento controllato

Nessuno l'aveva progettato così, ma il risultato è netto:

| | esito su 34 casi |
|---|---|
| ciò che V26.5.6.5 aveva messo **nello schema** (identità di clausola) | **giusto 34/34** — zero codici di espansione, zero codici di identità |
| ciò che era rimasto **nella prosa** del prompt (famiglie di prova, arietà, tipi di riferimento) | **sbagliato 34/34** |

E dentro il secondo gruppo, **72 errori di prova su 80** erano un solo obbligo
che la prosa enunciava e il modello ignorava: il ruolo di clausola si prova con
la struttura del discorso, non puntando ai segmenti.

Non è un problema di formulazione del prompt. Un controfattuale diagnostico
(sostituire quella sola prova, **nessun credito**) portava da 0 a **1/34**:
restavano tipi di riferimento e arietà sbagliati. Cioè il modello sbaglia
**contenuto**, e l'unica leva che ha funzionato è stata rendere l'errore
**inesprimibile**.

## Cosa fa

Applica la regola del progetto — *un invariante o è esprimibile nello schema o
non è fatale* — a tutto ciò che il registro congelato già determina:

- il **numero di argomenti** è fissato per relazione con `prefixItems`;
- **ogni posizione** è tipizzata da sola: i riferimenti ammessi lì sono
  esattamente quelli il cui tipo lo slot accetta;
- **ogni prova** porta la famiglia chiusa che il registro ammette *per quella
  affermazione* — ruolo, atto linguistico, relazione, stato dell'argomento,
  riferimento specifico;
- l'**output** di una dipendenza sta nello slot dichiarato dal registro, e
  quello slot non porta altro;
- **ruolo e atto linguistico** concordano per costruzione.

Nessun vocabolario nuovo, nessuna lista linguistica, nessun trucco di prompt:
ogni enum è letto dal registro congelato V26.4.1. La **forma** del documento è
identica a V26.5.6.6, quindi espansore, validator congelato e condotta sono
riusati **invariati**.

## I numeri

| | prima | ora |
|---|---:|---:|
| codici del validator irraggiungibili per costruzione | 11 | **20** |
| schema di risposta | 7.650 byte | 76.469 byte, 97 definizioni |
| gold rappresentabili con semantica esatta | 34/34 | **34/34** |
| famiglie di errore del live ancora esprimibili | 6 | **0** |

Sui **1.200 frame casuali schema-validi** nessuno dei 20 codici chiusi si
accende. Quelli che restano possibili sono, e devono restare, quelli che una
grammatica non può esprimere: conteggio degli `unknown` rispetto all'atto
linguistico, ordine di sorgente, contenimento delle prove esplicite, budget
degli atomi, direzione degli archi.

## Rischio residuo, dichiarato

Lo schema è **dieci volte più grande** e usa `prefixItems`. La conversione
schema→grammatica avviene **nel server**, e la si esercita solo al live. Se non
la regge, fallisce **rumorosamente sul primo caso**, non in silenzio: è il tipo
di guasto che si vede subito. Non ho modo di provarlo offline, e non fingo il
contrario.

## Limiti dichiarati

- Nessun live, nessun gate: entrambi i gate di V26.5.6.7 sono **esauriti**, un
  nuovo K1/34 richiede un preflight nuovo e un gate esterno nuovo.
- Registro **Phase-1 a 10 relazioni**: nulla qui certifica i 109.
- 22/22 offline dice che il contratto è rappresentabile e che gli errori
  misurati dal vivo sono ora inesprimibili. **Non** dice che il modello
  produrrà analisi semanticamente corrette dentro quei vincoli: è esattamente
  la domanda a cui il prossimo live risponde, e la risposta può essere no.
- Nessuna review indipendente, qui come nei due bundle precedenti.
