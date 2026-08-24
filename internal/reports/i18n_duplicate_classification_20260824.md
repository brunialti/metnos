# Classificazione delle somiglianze i18n — 24 agosto 2026

## Esito

Il controllo e' stato rieseguito sull'intero seed pubblico dopo avere assorbito
le chiavi presenti soltanto nel catalogo live. Le collisioni esatte ammesse
sono alias di rendering fra contesti tipizzati distinti: la loro unificazione
eliminerebbe informazioni utili ai call-site. L'allowlist le identifica tramite
lingua e insieme completo di chiavi, percio' una nuova chiave o la modifica di
un gruppo torna automaticamente in revisione.

Le stringhe italiane chiaramente editoriali rimaste in inglese sono state
corrette: passaggi dei dialoghi, destinatari, nomi delle sezioni lifecycle,
stati dell'aging, navigazione web, cartella, punteggio e schema della cache.
Il refuso bilingue `Executor ager` e' stato sostituito da `Gestione degli
executor` / `Executor manager`.

## Coppie quasi identiche

| Chiavi | Classificazione |
|---|---|
| `MSG_SITES_RC_QUOTA`, `MSG_SITES_RC_QUOTA_HOSTS` (IT/EN) | Il primo messaggio riguarda il budget complessivo, il secondo il limite per host. |
| `MSG_DEGENERATE_FINAL_ITEMS`, `MSG_DEGENERATE_FINAL_ITEM_ONE` (IT/EN) | Plurale e singolare hanno contratti di rendering distinti. |
| `ERR_ARG_NOT_INT`, `ERR_ARG_NOT_NONNEGATIVE_INT` (IT/EN) | Intero qualsiasi e intero non negativo sono vincoli diversi. |
| `MSG_DIALOG_MASKED_HINT`, `MSG_ORCH_MASKED_HINT` (IT) | Stesso concetto, ma descrive due stati e call-site separati. |
| `MSG_LIFECYCLE_ROW_SYNTH_ARCHIVED`, `MSG_LIFECYCLE_TLDR_SYNTH` (IT) | Etichetta di riga e frase del riepilogo non sono intercambiabili. |
| `MSG_TUTOR_HANDOFF_CONTINUE`, `MSG_TUTOR_HANDOFF_TITLE` (IT) | Azione e titolo del passaggio di consegne hanno funzioni diverse. |
| `MSG_CONSENT_GATE_MASS_MUTATION`, `MSG_CONSENT_GATE_MASS_MUTATION_GENERIC` (IT/EN) | Variante con dettaglio concreto e ripiego generico. |
| `MSG_CONSENT_GATE_OUTBOUND_BRIEF`, `MSG_CONSENT_GATE_OUTBOUND_N` (IT/EN) | Forma breve e forma con conteggio richiedono placeholder diversi. |
| `ERR_FROM_STEP_LIST_MISSING`, `ERR_FROM_STEP_RESULT_INVALID` (IT) | Sorgente assente e risultato malformato sono errori differenti. |

Non e' stata unificata alcuna coppia: ogni differenza corrisponde a un dato,
un vincolo o una funzione di interfaccia osservabile.

## Testi identici fra italiano e inglese

I 24 gruppi residui appartengono a quattro classi deliberate:

- nomi propri o termini tecnici adottati invariati: `Metnos`, `LRE`, `CPU`,
  `GPU`, `RAM`, `PID`, `browser`, `chat`, `console`, `email`, `executor`,
  `embedding`, `digest`, `password`, `rollback`, `OK`;
- unita', segnaposto e formati che non contengono prosa traducibile: `N`,
  `{seconds} s`, `{path}: {preview}` e i formati delle sonde CPU/GPU/RAM;
- valori brevi gia' naturali in entrambe le lingue: `No`;
- risorse di riconoscimento intenzionalmente language-neutral:
  `get_now.affinity` e `MSG_LOCATION_CANCEL_KEYWORDS`, che contengono entrambe
  le lingue per contratto e non sono testo mostrato all'utente.

Questi gruppi restano nel report come superficie di revisione umana e non sono
un errore CI. Placeholder, differenze seed/live e divergenze del bundle sono
invece bloccanti in entrambe le direzioni.
