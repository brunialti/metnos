# RUN3 style-only

Stato: **spento e non armato**. Questo laboratorio confronta il sistema
corrente congelato e tre forme del prompt candidate v0.3. Non contiene autorizzazione e l'import non usa
rete, GPU, endpoint o produzione.

- A SYSTEM CURRENT: richiesta e adapter byte-identici al braccio A del RUN2;
- S0 CURRENT: byte identici al prompt v0.3 usato nel RUN2;
- S1 METNOS SHORT: le stesse sei regole, una riga prescrittiva ciascuna;
- S2 PROCEDURAL: le stesse sei regole, nello stesso ordine, numerate.

Ogni braccio vede 120 canoniche, 4 tipizzate e 34 legacy. Il manifest contiene
158 × 4 = 632 richieste seriali con ordine latino ABCD/BCDA/CDAB/DABC ripetuto.
Ogni braccio occupa ogni posizione 39 o 40 volte.

Schema, registry, validator, compiler, modello, seed, limiti e metrica v0.3
sono identici. S0/S1/S2 differiscono soltanto nel system prompt; A conserva
esattamente richiesta e adapter del sistema RUN2. Critic OFF, una sola risposta,
raw byte-preserving, zero repair e zero retry.

L'evaluator apre il gold soltanto dopo 632 record completi, sigillo e replay
esatto. Pubblica ogni braccio contro l'oracolo e tutti i confronti a coppie;
non calcola un punteggio combinato. Le regressioni di sicurezza non si
compensano. A deve riprodurre RUN2-A e S0 deve riprodurre RUN2-B esattamente:
raw ed estrazione completa per ciascuno dei 158 casi, più metriche semantiche
aggregate. Anche un solo drift in uno dei due anchor rende ogni confronto
`non_attributable_anchor_drift`, pur conservando tutti i risultati.

Il processo deve partire con il `PYTHONHASHSEED` congelato nel protocollo.
Il valore è scelto una sola volta dalla matrice gold-free 0..255: tutti i 158
raw RUN2-A e tutti i 158 RUN2-B devono avere estrazione completa identica; il
tie-break è il minimo seed valido. Due processi nuovi confermano inoltre
316/316 richieste identiche. Il preflight rifiuta un interprete avviato con un
seed assente o diverso; impostare la variabile dopo l'avvio non è sufficiente.
Esempio di verifica disarmata: `PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1
python3 -m internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.live.run3_style.runner --preflight`.

I dizionari sono soltanto JSON del laboratorio. Un porting in produzione deve
usare oggetti tipizzati e immutabili.
