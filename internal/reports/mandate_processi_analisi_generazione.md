# Mandato permanente — rendere UTILI / CREATIVI / POTENTI i processi di analisi e generazione

> Roberto, 11/6/2026. Passato all'agente notturno (`scripts/nightly_fable_audit.sh`).
> Fonte: parere Opus fondato sui dati reali del sistema in esercizio.

## Diagnosi (i dati, non opinioni)
- **Iper-generazione**: **1017 proposte telos / solo 2 accettate**. La rilevazione dei "desideri"
  (proto-mnest) produce soprattutto rumore → la soglia desiderio→sintesi è troppo bassa.
- **Iper-specializzazione**: **13 executor sintetizzati, tutti VARIANTI strette**
  (`find_images_indices_40000`, `get_processes_it`, `read_messages_http`, …). Il synt scatta
  **per-istanza**, non per-pattern.
- **Cluster sotto-sfruttati**: l'analisi dei cluster di mnest dovrebbe portare a sintetizzare
  **UN executor generale per cluster**, non N per-query. Oggi sembra non usata a questo fine.

## Mandato
Analizza a fondo la DOC (`docs/it/architecture/{mnest,mnestoma,synt,praxis_engine}.html`) e il
CODICE (`runtime/synt.py`, `runtime/mnestoma.py`, telos/proposals) e migliora TUTTI i processi di
analisi/generazione perché diventino **effettivamente utili, creativi e potenti** — non un backlog.
Direzioni (non esaustive, scegline UNA ben fatta per notte):
1. **Alza la soglia** desiderio→sintesi: meno proto-mnest ma forti/ricorrenti/generali (1017 → pochi veri).
2. **Sintetizza dai CLUSTER**, non dalle singole istanze: un executor generale per pattern, non N varianti.
   Usa/rafforza l'analisi dei cluster di mnest come INPUT del synt.
   → **Roberto (11/6)**: la cluster analysis è **SOTTOUTILIZZATA** — mancano **algoritmi che rendano
   EVIDENTI i percorsi possibili / utili / usati**. Sviluppali: graph/sequence-mining sui mnest per far
   emergere quali CATENE di executor sono *possibili*, quali *utili*, quali davvero *usate* — da lì
   ricavare i target di sintesi (e i fast-path che si compongono).
3. **Chiudi il loop proposta→accettazione→executor** (oggi 2/1017): valutazione, promozione, deduplica
   delle proposte; rendi la conversione efficace.
4. **Creatività/potenza**: i processi devono PRODURRE valore riusabile (executor generali, fast-path che
   si compongono), non rumore.

## Vincoli (vincolanti)
- UNA cosa alla volta, ben fatta e **VALIDATA** (gate suite + bench + sonda). NON degradare nulla.
- **NO gaming**: mai indebolire gold/baseline/sonde per far passare.
- §7.9 deterministico > LLM · §7.2 semplicità · §7.3 no hardcoding (soluzione generale).
- ⚠️ I cambi **ARCHITETTURALI** ai processi di generazione restano su **BRANCH per revisione umana**
  (NON auto-merge in prod): il gate valida "non rotto", non "creativo/potente" — quello lo vaglia Roberto.
