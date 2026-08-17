# Review statica di V26.5.6.9 — **d'autore, NON indipendente**

Data: 10 agosto 2026. Va detto subito e per intero: **questa review l'ha
scritta l'autore del bundle**, su richiesta esplicita di Roberto («al momento
non disponibile agente indipendente, fai tu review»). Non vale come STATIC PASS
indipendente e non va citata come tale in nessun gate, artefatto o riassunto.
Il suo unico valore è che ogni ipotesi qui sotto è stata **eseguita**, non
argomentata, e che i difetti trovati sono stati corretti.

Verdetto della sonda: **PASS** — 10 prove, 0 difetti. Questi byte sono quelli
di V26.5.6.7 ripinnati sul contratto tipizzato V26.5.6.8: i due difetti che la
review precedente aveva trovato sono gia' chiusi qui dentro. Zero rete, zero chiamate modello, zero gate creato o consumato.

Sonda e risultato: `metnos_v26569_author_static_review_probe.py` e
`..._probe_result.json` (deterministici, non fanno parte del freeze).

## Cosa ha trovato, prima delle correzioni

**C1 — i codici semantici recuperati dietro un validator censurato non erano
ricalcolati.** Un record poteva cancellarli e il valutatore lo accettava: era
l'unica prova semantica che quel record portava. Guardando meglio, il difetto
era più profondo di come l'avevo chiuso al primo colpo — vedi sotto.

**D1 — l'impronta non era mai confrontata con l'espansione.** Un record poteva
portare entrambe e farle dire cose diverse. È la stessa categoria del digest
del corpo di risposta che V26.5.6.3 aveva tolto: *un'osservazione fatta passare
per prova*.

## Come sono chiuse

**D1**: il valutatore ora **ricalcola dall'espansione** ciò che l'espansione
determina — stato, conteggio di atomi, dipendenze e archi, clausole proiettate
e fuori registro, multiinsiemi di relazioni, atti linguistici e ruoli — e lo
confronta con l'impronta. Un'espansione «pinzata» (letture di lunghezza diversa)
è esente e lo dichiara con il proprio codice di espansione.

**C1, e il difetto vero che ci stava sotto.** Per un frame **schema-valido** i
codici si ricalcolano davvero, rieseguendo lo sweep sull'espansione persistita.
Per un frame **schema-invalido** l'espansione non viene salvata — di proposito,
per tenere il batch query-free — quindi quei codici **nessuno può
ri-derivarli**. Fingere il contrario sarebbe stato ripetere l'errore di
V26.5.6.3. Quindi ogni record ora **dichiara**
`semantic_codes_reproducible`, e il valutatore impone la dichiarazione:

- dichiarata vera → ricalcola e confronta;
- dichiarata falsa → il record **non deve** portare un'espansione, e il numero
  di questi casi finisce nel risultato della valutazione
  (`unreproducible_semantic_records`);
- dichiarazione incoerente con `schema_ok` → respinta prima del gold.

Il confine di fiducia, detto in una riga: **un record che rivendica un successo
porta prove ri-derivabili; un record che dichiara un fallimento porta
osservazioni**, e lo scrive. Nessun record non ri-derivabile può essere
accreditato dallo scorer.

## Cosa risulta confermato (eseguito, non affermato)

| # | Affermazione | Prova |
|---|---|---|
| A0 | il valutatore accetta il batch prodotto dal bundle | 34 record, ~116 KB |
| B1 | `expansion_clean_cases` è legato alla sua evidenza | forgiarlo su un record invalido viene respinto |
| C1 | i codici semantici ri-derivabili lo sono davvero | cancellarli su un record censurato **con** espansione viene respinto |
| C2 | i codici che nessuno può ri-derivare sono **dichiarati** | ogni record senza espansione porta `semantic_codes_reproducible=false` |
| C3 | la dichiarazione non è forgiabile | ribaltarla viene respinto prima del gold |
| D1 | impronta ed espansione sono confrontate | una coppia incoerente viene respinta |
| E1 | nessuna forma di contrabbando raggiunge un campo persistito | 5 forme (prova con chiave extra, chiavi inventate annidate, letture di un'ambiguità, stato, motivo fuori registro): 0 occorrenze |
| F1 | uno span fuori scala viene preso | `clause_span` + `proof_family` |
| G1 | nessun trasporto è raggiungibile senza gate | preflight e live rifiutano entrambi |
| H1 | i byte del bundle e i pin sono intatti | 19 pin verificati |

## Cosa NON ho verificato, e perché

- **Indipendenza.** Sono l'autore. Le ipotesi che non mi sono venute in mente
  restano non provate, ed è esattamente ciò che una review indipendente serve a
  coprire: le due volte precedenti ne ha trovate quattro che l'autore non
  vedeva.
- **Accuratezza semantica del modello**: nessun live, nessun modello.
- **I 109**: il registro resta Phase-1 a 10 relazioni.
- **Il contratto V26.5.6.6**: qui è dato per pinnato; la sua review indipendente
  è stata saltata il 9 agosto per decisione dell'utente.
