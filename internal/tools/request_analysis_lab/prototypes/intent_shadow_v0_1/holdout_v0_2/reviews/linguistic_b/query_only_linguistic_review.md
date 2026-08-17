# Query-only linguistic review B — holdout_v0_2

Data della revisione: 2026-08-13

## Esito

**Blocker presenti: sì.** La qualità linguistica complessiva è buona e tutti i dieci pannelli corrispondono al proprio language tag, ma due coppie adiacenti di casi giapponesi e serbi sono traduzioni parallele troppo precise per soddisfare il requisito di authoring indipendente.

Sono state esaminate 320 query: 32 casi per ciascuno dei dieci file `authors/*.json`. La revisione ha riguardato soltanto naturalezza, correttezza linguistica e regionale, varietà di scenario e sintassi, e possibili traduzioni parallele. Non sono stati consultati prompt, candidate, run, oracle, expected o review precedenti; non sono stati adjudicati expected e nessuna query è stata modificata.

## Blocker

| ID | Casi | Riscontro | Motivo |
|---|---|---|---|
| B1 | `ja-JP/q27`, `sr-Cyrl-RS/q27` | Entrambe chiedono di condividere/inviare **la foto di un plastico a un architetto** e, nella clausola negativa, di **non cancellare quella foto dall'album**. | Coincidono oggetto raro, destinatario, azione positiva, azione negata, contenitore e ordine delle clausole. Le due frasi sono naturali nelle rispettive lingue, ma sono equivalenti quasi clausola per clausola: parallelismo incompatibile con authoring indipendente. |
| B2 | `ja-JP/q28`, `sr-Cyrl-RS/q28` | Entrambe chiedono di leggere il **rapporto/issue numero 38**, riferirne il contenuto e **non cambiare l'etichetta di priorità**. | Il medesimo numero, lo stesso oggetto tecnico e la stessa combinazione positiva-negata rendono la corrispondenza una traduzione parallela inequivocabile. |

Per rimuovere i blocker va riautorato da zero almeno un lato di ciascuna coppia, con scenario, entità e struttura non correlati, seguito da un nuovo controllo incrociato. La somiglianza generalizzata dei casi `q30` non è stata considerata un difetto: la constitution impone lì un'unica richiesta molto specifica. Analogamente, nei `q31` è inevitabile che la prima clausola riguardi l'annullamento del turno precedente; le seconde operazioni sono sufficientemente diverse.

## Pannelli prioritari

| Pannello | Valutazione | Rilievi non bloccanti |
|---|---|---|
| `it-IT` | Italiano contemporaneo d'Italia corretto e naturale; buon equilibrio fra imperativi, domande, ellissi e richieste indirette. | `q25`: «Hai la mia conferma» è comprensibile ma più burocratico e meno idiomatico di una conferma formulata direttamente. `q30`–`q31` sono volutamente precisi ma suonano più metalinguistici della conversazione ordinaria; restano chiari e grammaticali. |
| `de-DE` | Tedesco standard della Germania coerente, con registro colloquiale e formale ben controllato e lessico locale plausibile. | `q31`: «Nimm zurück, was du … getan hast» è chiaro, ma per una modifica digitale risulta un po' meno idiomatico della normale costruzione con *rückgängig machen*. Non incide sull'interpretabilità. |
| `ar-EG` | Varietà egiziana nettamente riconoscibile e contemporanea: morfologia colloquiale, particelle egiziane e prestiti digitali sono usati con naturalezza. Nessuna deriva sistematica verso MSA o altra varietà. | `q06`: «جلسة دخولي» ha sapore di calco da interfaccia ed è meno spontaneo di una normale espressione egiziana per il logout. `q09`: «اضغط مجلد» può significare sia comprimere sia premere/cliccare, anche se il contesto rende probabile il primo senso. `q24`: «الأرقام تتراجع» è leggermente marcato perché può evocare numeri in calo anziché numeri da ricontrollare. |
| `zh-Hant-TW` | Cinese tradizionale coerente con Taiwan, con marcatori locali solidi quali `行事曆`, `資料夾`, `核銷`, `到府`, `佈告欄` e `承辦人`; nessuna evidente intrusione sistematica di lessico o grafia della Cina continentale. | `q04`: `各尺寸總共各有幾件` duplica lievemente `各`. `q17`: `幫我替` sovrappone due benefattivi. `q21`: `如果手邊方便` è comprensibile ma un po' ellittico rispetto all'uso taiwanese più fluido. `q30`–`q31`: `回合` è preciso ma richiama un registro da gioco/test più che una richiesta quotidiana; la cella di controllo ne giustifica comunque l'uso. |

Nessuno di questi rilievi prioritari è un blocker: non cambia la lingua assegnata, non rende la query incomprensibile e non crea un secondo intento linguistico plausibile tale da compromettere il caso.

## Altri pannelli

| Pannello | Valutazione | Anomalie evidenti o note non bloccanti |
|---|---|---|
| `en-GB` | Pass. Inglese britannico naturale e riconoscibile (`organising`, `high street`, `bung`), con buona varietà sintattica. | Nessun rilievo case-level. |
| `es-MX` | Pass. Spagnolo messicano naturale, con colloquialismi regionali ben dosati (`porfa`, `Chuy`, `viejitos`, `plomero`). | `q25` e `q26` ripetono da vicino lo schema «chiedi a un ruolo terzo di approvare; agisci solo se approva» e assegnano entrambe l'approvazione a terzi. È una debolezza locale di diversità/ownership, non un problema di lingua. |
| `hi-IN` | Pass. Hindi indiano contemporaneo plausibile; i prestiti digitali in inglese sono coerenti con il registro. | `q17`: `पिछली ब्रेक` presenta un accordo/collocazione marcato; nell'uso standard il prestito `ब्रेक` è più comunemente trattato al maschile o reso con «il freno posteriore». La frase rimane perfettamente comprensibile. |
| `ja-JP` | Pass linguistico. Giapponese naturale, con alternanza appropriata fra tono diretto, cortese ed ellittico. | Nessun difetto linguistico bloccante; `q27` e `q28` sono bloccanti esclusivamente per il parallelismo con il serbo. |
| `sr-Cyrl-RS` | Pass linguistico. Serbo ekavo in cirillico coerente e idiomatico, con colloquialità controllata. | Nessun difetto linguistico bloccante; `q27` e `q28` sono bloccanti esclusivamente per il parallelismo con il giapponese. |
| `tr-TR` | Pass. Turco di Turchia naturale, grammaticalmente stabile e adeguato al registro digitale. | `q01`: la ripetizione ravvicinata di `gelen` è solo una piccola pesantezza stilistica. |

## Diversità e indipendenza complessive

Al di fuori di B1 e B2, non emergono traduzioni parallele. Esistono normali collisioni di famiglia — per esempio compressione di cartelle fotografiche, manutenzione fisica o richieste mediche di confine — ma cambiano entità, azioni, formulazione e spesso posizione nella griglia; non costituiscono copie tradotte.

La varietà interna è nel complesso sufficiente: le celle linguistiche includono ellissi, cortesia, colloquialismi, dipendenze a distanza e periodi pluriclausola senza una matrice unica ripetuta. In `zh-Hant-TW/q01`, `q03`, `q06`–`q08` ricorre più volte la costruzione `把`, ma si tratta di una struttura produttiva naturale e non di un template completo. La maggiore uniformità dei casi di controllo finale deriva dalle istruzioni strette delle rispettive celle, non da parallelismo illecito.

## Verdetto finale

- Coerenza lingua–tag: **pass, 10/10 pannelli**.
- Naturalezza complessiva: **pass con rilievi non bloccanti**.
- Diversità generale di scenario e sintassi: **pass**, salvo la lieve ripetizione `es-MX/q25`–`q26`.
- Indipendenza fra lingue: **fail/blocker** per `ja-JP/q27` ↔ `sr-Cyrl-RS/q27` e `ja-JP/q28` ↔ `sr-Cyrl-RS/q28`.

Il set non dovrebbe essere considerato linguisticamente pronto finché le due coppie parallele non vengono sostituite e ricontrollate.
