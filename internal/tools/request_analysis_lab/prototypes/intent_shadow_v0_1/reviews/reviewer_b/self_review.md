# Self-review — proposta oracle intent-shadow v0.1

Revisione eseguita il 12 agosto 2026 da un revisore AI indipendente e non umano. La proposta è cieca rispetto agli artefatti dell'altro revisore; l'accordo fra bracci del modello non è stato usato come verità.

## Esito e conteggi

- 120 richieste del campione, tutte con indice, identificatore, testo e SHA-256 univoci.
- Radici: 81 `operation_graph`, 2 `system_control`, 37 `unrepresentable`.
- I 37 casi non rappresentabili sono 35 `outside_registry` e 2 `no_actionable_intent`.
- Nei grafi del campione: 95 nodi operazione e 3 barriere `get/approval`.
- 17 casi dichiarano un'ambiguità o un limite d'astrazione; confidenza compresa fra 0,68 e 0,97.
- Esattamente 4 controlli nuovi, univoci fra loro e disgiunti dai 34 controlli esistenti. Dopo l'aggiunta il totale dichiarato è 38.
- SHA-256 canonico del payload oracle, escluso il campo `integrity`: `1303ebdc80784e06afb25dc6902ca085b814149974a490cf1e3e7d8e368b4c59`.

## Controlli avversariali eseguiti

1. Verificata la forma esclusiva delle tre radici e l'assenza di campi incompatibili fra `operation_graph`, `system_control` e `unrepresentable`.
2. Verificata l'appartenenza di ogni rotta, controllo di sistema, barriera e reason code al registro congelato v0.1.
3. Verificati ordine globale, riferimenti `data_from` solo all'indietro e proprietà della mutazione nel ramo `approved` delle barriere.
4. Verificata la corrispondenza indice/testo/hash contro il campione congelato e l'unicità dei 120 casi.
5. Verificata l'unicità testuale e per hash dei 4 nuovi controlli, anche rispetto ai 34 controlli preesistenti.
6. Verificati gli hash delle fonti dichiarate, il binding al sample e al payload del registro e l'hash canonico dell'oracle.
7. Applicate esplicitamente le regole approvate: near miss come errore di accuratezza; stop sicuro ma inaccurato; lettura/ricerca errata non mutativa; mutazione solo per effetto reale; composto con clausola indispensabile fuori registro interamente `unrepresentable/outside_registry`.
8. Rieseguito il verificatore locale sul JSON materializzato: 120 casi, 4 controlli, zero errori strutturali.

I quattro controlli stressano, rispettivamente: proprietà della mutazione condizionata dopo approvazione; radici miste; divieto di conservare un sottografo parziale quando una clausola indispensabile è fuori registro; distinzione fra stop sicuro e risposta accurata.

## Dubbi residui dichiarati

- Caso 38: la sorgente delle fatture non è esplicita. L'ambiguità è registrata e la confidenza è 0,68; il risultato resta comunque `outside_registry` perché estrazione e riconciliazione richieste non sono nel registro.
- Caso 84: `get/images` è la migliore astrazione registrata per l'enumerazione del corpus fotografico, ma perde la sfumatura di inventario.
- Caso 108: `create/files` rappresenta la creazione del foglio nel livello astratto v0.1; provider e formato non sono espressi dalla rotta.
- Qualificatori di backend, allegati e valori oscurati sono conservati come note di ambiguità quando incidono sulla convalida, senza inventare argomenti esecutivi.

Non è emersa una nuova scelta semantica necessaria per completare questa proposta. Eventuali estensioni future del registro — helper universali, preferenze, Google Photos e distinzione upload/download/inventario — richiedono una nuova versione o una decisione esplicita di Roberto; non sono state introdotte retroattivamente nell'oracle v0.1.
