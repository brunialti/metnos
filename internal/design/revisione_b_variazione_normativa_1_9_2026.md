# RM-VARIAZIONE-01 — revisione B della variazione normativa F4

Commit esaminato: `9d43c1d0` (`internal/roadmap/RM-0008-porta-unica-nascita-executor.md`)
Verdetto: `MODIFICHE_RICHIESTE`

Revisione contro i cinque controlli che il §8 del coordinamento impone a una
variazione di roadmap.

## Quello che regge

- **(1) fatti, lavoro residuo e regola nuova sono distinti.** La riga del
  registro di stato riporta un fatto verificabile e nomina i due commit esatti;
  la sezione nuova è regola e si dichiara tale.
- **(2) nessuna barriera è dichiarata chiusa senza commit e prove.** La
  convergenza tecnica è ancorata ad `ba26f5cd` e `df36c169`, che sono le due
  ACCETTATA reali.
- **(3) nessun criterio già accettato viene ridotto.** Al contrario, la
  variazione **stringe**: un certificato privo dei due campi nuovi è invalido e
  non è ammesso un ripiego sul payload precedente. Che il certificato V1 resti
  V1 è motivato — non esiste un certificato produttivo precedente da
  conservare — e la modifica del §7.3 congelato è dichiarata, non contrabbandata.
  È esattamente per questo che serve la decisione di Roberto.
- I sette stati restano sette e ordinati; il record V2 aggiunge campi senza
  toccarli.

## Rilievo B3 — la precondizione del contenitore incompleto non entra nella norma

Il disegno A accolto dice che «la transizione non può iniziare finché il
contenitore incompleto permane», e il perimetro B ne possiede la primitiva di
recupero. Ma **la roadmap non lo dice in nessun punto**:

```
$ git show 9d43c1d0:internal/roadmap/RM-0008-porta-unica-nascita-executor.md \
    | grep -ic "pubblicazione incompleta\|contenitore incompleto"
0
```

Il disegno è una proposta; la roadmap è la fonte normativa. Chi legge la sola
roadmap — cioè l'autorità — non apprende che la transizione ha una precondizione
sul negozio, e il §8 punto 5 chiede che l'ordine delle fasi resti coerente con
le **dipendenze reali**. Questa lo è: sul negozio attuale il censimento esce `2`
e la transizione non può partire.

**Disposizione**: la sezione «Transizione dell'epoca di autorità» deve nominare
la precondizione — inventario produttivo con zero problemi e nessun oggetto
immediato non posseduto, contenitore di prima pubblicazione incompleto risolto —
come condizione d'ingresso della transizione, non come dettaglio del disegno.

## Rilievo B4 — «prima del codice di prodotto» è più largo di quanto sia vero

La riga del registro dice che la variazione è offerta alla revisione incrociata
«prima del codice di prodotto». Letta così, sbarra **tutto** il codice di
prodotto fino all'approvazione.

Ma il disegno A, accettato da entrambi, qualifica la primitiva di recupero come
precondizione **distinta** dalla transizione, fuori dall'esecuzione automatica
F4 e con un gate operativo separato; e il §13 subordina alla variazione la
transizione, non ogni riga di codice. Su quella base ho già consegnato la
primitiva in `82f8e4e8`, dieci prove verdi su copia.

O la riga è più larga del vero, o la mia consegna ha anticipato una barriera —
e le due letture non possono coesistere in una fonte normativa.

**Disposizione**: la riga deve dire **quale** codice la variazione sbarra — il
codice della transizione di epoca — oppure dichiarare esplicitamente che
nessuna eccezione esiste, e in quel caso il mio `82f8e4e8` va riclassificato.
Non chiedo quale delle due: chiedo che la roadmap ne scelga una.

## Nota

Nessun rilievo di forma. Entrambi i punti riguardano ciò che la fonte normativa
dice o non dice, ed entrambi sono verificabili con un comando.
