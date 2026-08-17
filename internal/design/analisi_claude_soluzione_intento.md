# Analisi — esiste una soluzione? (Claude, 11/8/2026)

Analisi indipendente. Documento gemello: `analisi_codex_soluzione_intento.md`.
Dati: `misure_11_8/confronto_intento.json`, referto in
`referto_confronto_intento.md`, misure in `handover_prompt_ontologia_11_8_2026.md`.

## Tesi

**Sì, esiste** — ma non sostituendo tutto il percorso dell'intento in un colpo. I
dati dicono che il problema si spacca in due parti **disgiunte**, e il lavoro
finora le ha confuse perché ha confrontato i due percorsi da un capo all'altro.

| | dove vivono i dizionari | dove vive il divario di qualità |
|---|---|---|
| richieste a **una** operazione | **tutte e 5 le scorciatoie** | accordo **76%** (56/74) |
| richieste **composte** | **nessuna scorciatoia** | accordo **12%** a 2 operazioni, 26% a 3+ |

**Non serve vincere il confronto sulle richieste composte per cancellare i
dizionari.** Sono due problemi diversi che si sono presentati insieme.

## Le due ipotesi che ho falsificato prima di arrivarci

Le registro perché sono le spiegazioni intuitive, e sono sbagliate.

1. **«Il nuovo percorso è cieco al catalogo»** — è la spiegazione più naturale
   guardando `login/urls` e `write` al posto di `create`. **Falsa**: contando le
   coppie che non esistono fra i manifest firmati, **l'attuale ne emette di più**
   — 35 casi contro 30 sui 55 disaccordi. La validità di catalogo non separa i
   due percorsi.
2. **«Il nuovo è penalizzato perché scompone di più»** — plausibile, visto che
   emette un frame multi-predicato mentre l'attuale estrae una coppia primaria.
   **Falsa**: emette in media **meno** rotte (1,98 contro 2,11), e nei disaccordi
   scompone di più in 4 casi contro 13 in cui scompone di meno.

## L'evidenza della tesi

Accordo fra i due percorsi, per numero di operazioni della richiesta (conteggio
secondo l'attuale, che è la sua stessa lettura della complessità):

| operazioni | casi | accordo | % |
|---:|---:|---:|---:|
| 1 | 74 | 56 | **76%** |
| 2 | 24 | 3 | **12%** |
| 3+ | 19 | 5 | 26% |

Le richieste a una sola operazione sono **77 su 120, il 64% del traffico reale**.

E le cinque scorciatoie lessicali stanno **tutte** lì: tre `undo.intent_bypass` e
due `machine.reference + health.section_focus`, tutte su richieste a zero o una
operazione. Nessuna scorciatoia scatta su una richiesta composta. Il vantaggio
dell'attuale sul composto **non viene dai dizionari**: viene dal fatto che la sua
decomposizione è governata a valle, dal planner, non dall'estrattore.

## Proposta

**P1 — Restringere il perimetro della sostituzione alle richieste a una
operazione.** È dove stanno i dizionari e dove i due percorsi già concordano al
76%. Il composto resta come sta. Misurare solo su quelle 77 e adjudicare i 18
disaccordi semplici: è un numero raggiungibile, mentre «battere l'attuale su
tutto» non lo è oggi.

**P2 — I tre `undo` non sono un problema di dizionario, sono un buco del
vocabolario.** `undo` è escluso di proposito dal vocabolario chiuso dei verbi
(è un builtin di runtime, §2.2 «System verbs riservati»). Nessun modello può
emetterlo: non esiste nell'enum. Perciò l'attuale **deve** scavalcare il modello,
e lo fa con un lessico. Il rimedio non è un modello migliore ma una scelta di
struttura — un ruolo riservato nel frame, oppure un controllo deterministico —
ed è una decisione di design, non di prompt. Finché `undo` non esiste nella
struttura, quelle tre voci di dizionario **non sono cancellabili**, e il nuovo
percorso continuerà a proporre `delete/entries` per «annulla», che è una
regressione di sicurezza.

**P3 — I due casi hardware funzionano già senza lessico.** Sono bloccati solo
perché le stesse voci hanno altri consumatori: `health.section_focus` sceglie
anche la sezione della risposta, `machine.reference` serve a `_ensure_health_arg`.
Il rimedio è separare l'uso di **instradamento** da quello di **presentazione**:
il primo si può togliere, il secondo resta. È l'unica cancellazione dimostrata.

**P4 — Prerequisito tecnico: il catalogo del banco è del 5 giugno.**
`tests/benchmarks/catalog_snapshot.json`, 96 righe, **senza `login_sites` e
`act_sites`**: manca l'intero dominio `sites`, arrivato l'11 luglio. Contro i
manifest firmati sul disco mancano 11 coppie. Qualunque riparo che si appoggi al
catalogo va rifatto sulla fonte firmata, non su quel file. Nota che il banco
calcola `CATALOG_ACTION_OBJECT_PAIRS` alla riga 114 e **non lo usa per vincolare
la generazione**: lo schema lascia `verb` libero su tutti i 26 verbi e `object`
su tutti i 27 oggetti, cioè 702 combinazioni per ~69 che esistono.

## Che cosa NON è riparabile, e va detto

- **Le richieste composte**, con gli strumenti provati finora. Otto tentativi di
  correggere l'instradamento aggiungendo testo al prompt sono tutti falliti. Non
  ho motivo di credere che il nono funzioni.
- **`undo`**, finché resta fuori dalla struttura (vedi P2).
- **La latenza**: 310 ms contro 3.961 ms di mediana. Se il nuovo percorso
  prendesse tutto il traffico sarebbe un peggioramento di tredici volte; sul solo
  perimetro P1 va comunque quantificato.

## Limiti di questa analisi

- Il 76% di accordo sulle richieste semplici **non è parità**: i 18 disaccordi
  semplici non sono stati adjudicati uno per uno. Prima di dichiarare P1
  raggiungibile vanno letti.
- «Numero di operazioni secondo l'attuale» è un proxy della complessità, ed è la
  lettura di una delle due parti in causa.
- Un caso sospetto da verificare: `undo.intent_bypass` risulta scattato su
  «Analizza in parallelo le email e gli appuntamenti degli ultimi…», dove di
  annullamento non c'è traccia. Se confermato è un falso positivo del dizionario,
  della stessa famiglia del gate goloso del Tutor.

## Giudizio finale

L'obiettivo **è raggiungibile su circa due terzi del traffico reale**, ed è lì che
stanno tutti i dizionari misurati. Non è raggiungibile, oggi, sul composto — ma
sul composto i dizionari non c'entrano, quindi non è un ostacolo all'obiettivo:
è un altro lavoro.

La cancellazione dimostrata oggi resta però **piccola**: due voci di
instradamento hardware. Le altre tre (`undo`) richiedono una decisione di
struttura, non un modello migliore.
