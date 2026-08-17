# RUN4 — query-only custodian review

## Esito

**BLOCKER.** La struttura meccanica del pannello è corretta, ma il contenuto mostra una costruzione parallela sistematica tra i dieci blocchi linguistici. Le sostituzioni di entità e alcuni adattamenti regionali non bastano a rendere le query «independently conceived»: molti slot ripetono lo stesso scenario, la stessa coppia di azioni e lo stesso profilo sintattico. Questo viola in modo chiaro `parallel_translations_forbidden`, `queries_must_be_independently_conceived_per_language` e il divieto di formulazioni template-equivalenti.

La valutazione è esclusivamente query-only. Non sono stati letti prompt challenger/style, candidate output, live model output, expected, oracle, gold, review o evaluation, e non è stata fatta alcuna adjudication dell'operazione attesa.

## Controlli meccanici — PASS

- 320 casi totali; 10 language tag, 32 casi per lingua.
- Per ogni lingua: 24 casi general e 8 safety; safety totale 80.
- Distribuzione per cella corretta in ogni lingua: `6/3/3/6/6/2/2/1/1/1/1` nell'ordine richiesto.
- Ordine delle lingue conforme al roster del brief; ID consecutivi da `h4-0001` a `h4-0320`, univoci e conformi al pattern.
- Campi del proposal esattamente conformi; nessun campo proibito.
- Safety tag esattamente conformi alla cella, incluso l'ordine dei due tag di S3 e S5.
- JSON e UTF-8 validi; nessuna query vuota, replacement character o carattere di controllo inatteso.
- Il blinded panel contiene gli stessi `case_id`, `language_tag` e `query` nello stesso ordine; tutti i `query_sha256` verificati.
- Nessun duplicato esatto interno, nemmeno dopo normalizzazione Unicode NFKC, case-folding e rimozione di spazi/punteggiatura.
- Nessun duplicato esatto o normalizzato contro i tre pannelli storici query-only autorizzati.

## B-01 — authoring parallelo cross-language

Ambito interessato: i dieci blocchi `h4-0001`–`h4-0032`, `h4-0033`–`h4-0064`, `h4-0065`–`h4-0096`, `h4-0097`–`h4-0128`, `h4-0129`–`h4-0160`, `h4-0161`–`h4-0192`, `h4-0193`–`h4-0224`, `h4-0225`–`h4-0256`, `h4-0257`–`h4-0288`, `h4-0289`–`h4-0320`.

Non considero una violazione la sola equivalenza astratta imposta dalla cella, e in particolare non uso S4 come prova. Il blocker deriva da dettagli di scenario e strutture ripetuti che il brief non impone. Cluster ad alta confidenza:

| Motivo | Case ID sospetti |
|---|---|
| G1: aggiornamento dello stesso tipo di campo in un contatto, nello stesso slot | `h4-0004`, `h4-0036`, `h4-0068`, `h4-0100`, `h4-0132`, `h4-0164`, `h4-0196`, `h4-0228`, `h4-0260`, `h4-0292` |
| G2: cancellazione di bozze/menu vecchi più creazione di un task, con soli dettagli sostituiti | `h4-0008`, `h4-0072`, `h4-0104`, `h4-0136`, `h4-0168`, `h4-0200`, `h4-0232`, `h4-0264`, `h4-0296` |
| G3: reperimento di ricevuta/documento e spostamento in cartella garanzia/documenti | `h4-0010`, `h4-0106`, `h4-0138`, `h4-0170`, `h4-0202`, `h4-0234`, `h4-0266`, `h4-0298` |
| G3: reperimento dell'indirizzo e-mail da contatti e invio di orario/programma | `h4-0011`, `h4-0043`, `h4-0107`, `h4-0139`, `h4-0171`, `h4-0203`, `h4-0235`, `h4-0267`, `h4-0299` |
| G3: raggruppamento/filtro seguito da aggregazione sul risultato | `h4-0012`, `h4-0044`, `h4-0076`, `h4-0108`, `h4-0140`, `h4-0172`, `h4-0204`, `h4-0236`, `h4-0268`, `h4-0300` |
| G4: recupero di istruzioni/ricetta/cartamodello seguito dalla relativa azione fisica | `h4-0013`, `h4-0045`, `h4-0078`, `h4-0109`, `h4-0141`, `h4-0173`, `h4-0205`, `h4-0237`, `h4-0269`, `h4-0301` |
| G5: richiesta indiretta di ritrovare una vecchia foto o uno schizzo, spesso di un contatore | `h4-0019`, `h4-0051`, `h4-0083`, `h4-0115`, `h4-0147`, `h4-0179`, `h4-0211`, `h4-0243`, `h4-0275`, `h4-0307` |
| G5: frammento nominale per soli messaggi non letti, con filtro temporale | `h4-0020`, `h4-0052`, `h4-0084`, `h4-0116`, `h4-0148`, `h4-0180`, `h4-0212`, `h4-0244`, `h4-0276`, `h4-0308` |
| G5: nota/contatto creato dopo fiera, workshop o riunione, identificato da un dettaglio interno e poi rinominato/aggiornato | `h4-0023`, `h4-0087`, `h4-0119`, `h4-0151`, `h4-0183`, `h4-0215`, `h4-0247`, `h4-0279`, `h4-0311` |
| G5: conversazione dimenticata, ricerca del messaggio su uniforme/costume e lettura | `h4-0024`, `h4-0056`, `h4-0088`, `h4-0120`, `h4-0152`, `h4-0184`, `h4-0216`, `h4-0248`, `h4-0280`, `h4-0312` |
| S1: approvazione da responsabile finanziario/proprietario per cancellare esattamente un file/cartella | `h4-0025`, `h4-0058`, `h4-0089`, `h4-0121`, `h4-0153`, `h4-0185`, `h4-0217`, `h4-0249`, `h4-0281`, `h4-0313` |
| S1: approvazione di una versione esatta di avviso/report e successiva condivisione/invio | `h4-0026`, `h4-0057`, `h4-0090`, `h4-0122`, `h4-0154`, `h4-0186`, `h4-0218`, `h4-0250`, `h4-0282`, `h4-0314` |
| S3: se contatto/issue esiste aggiornalo, altrimenti crealo, con identica architettura dei rami | `h4-0029`, `h4-0061`, `h4-0093`, `h4-0125`, `h4-0157`, `h4-0189`, `h4-0221`, `h4-0253`, `h4-0285`, `h4-0317` |

La stessa impronta posizionale continua anche in altri slot G1, G2, G4, G5, S1 e S2. Nel complesso non è plausibile attribuirla soltanto all'equivalenza di cella consentita dal brief. Le superfici sono spesso scorrevoli e regionalizzate, ma appaiono come localizzazioni di una matrice comune.

## Duplicati e novità — sospetti non bloccanti autonomi

- `h4-0009` / `h4-0031`: la seconda query riusa quasi integralmente la stessa sotto-richiesta e la stessa entità della prima, aggiungendo il controllo undo richiesto da S5. Non è un duplicato dell'intera query, ma riduce la varietà interna.
- `h4-0062` rispetto a `frozen_sample.066` e, più debolmente, `frozen_sample.025`: near-duplicate lessicale e semantico in italiano sulla revoca dell'ultima operazione. Lo segnalo come rischio di novità, non come blocker autonomo, perché S4 obbliga proprio quella capacità e limita fortemente lo spazio semantico.
- Le coppie S4/S5 della stessa lingua condividono necessariamente la componente undo. Non sono state contate come duplicati per questo solo fatto.

## Aderenza alle celle e safety

Al netto del blocker cross-language, non ho trovato una query chiaramente assegnata alla cella sbagliata. Le G2 contengono azioni indipendenti, le G3 espongono una dipendenza leggibile, le G4 hanno quattro casi misti e due outside-only per lingua, e le celle safety mantengono negazione, rami, undo e controllo misto espliciti. Non emergono segreti, istruzioni dannose o contenuti illegali.

## Dubbi linguistici — non-blocker

Questi casi restano comprensibili, ma meritano controllo madrelingua; non li considero violazioni certe:

- `h4-0071`: ellissi spagnola nell'azione di invio a Memo, potenzialmente poco idiomatica per `es-MX`.
- `h4-0086`: ordine dei costituenti nella formulazione colloquiale spagnola verso la cartella Archivio.
- `h4-0145`: scelta del verbo turco per stringere/riparare un'anta allentata.
- `h4-0164`: flessione/apposizione del nome del contatto in serbo.
- `h4-0233`: scelta lessicale hindi per «messaggi non letti», con possibile lettura non idiomatica.
- `h4-0237`, `h4-0243`, `h4-0247`: rispettivamente lessico per il manuale, aggancio della relativa e accordo/ordine dei costituenti in hindi.

## Decisione di custodia

Il pannello non è ammissibile come nuovo holdout nella forma corrente. È necessaria una nuova authoring pass indipendente per lingua che spezzi i cluster template-equivalenti; questa review non propone né riscrive query e non assegna expected/gold.
