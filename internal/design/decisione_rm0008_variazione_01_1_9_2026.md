# RM-0008 — decisione su RM-VARIAZIONE-01

Data: 1 settembre 2026  
Decisione: `APPROVATA`

## Oggetto

La decisione riguarda esclusivamente la variazione normativa contenuta nel
commit `a782065e`, gia' accettata dall'agente B nel commit `c656c241` e inclusa
nella composizione verificata alla testa F4 `2f1e042d`.

Roberto ha confermato di avere gia' autorizzato l'agente B ad accettare la
variazione. La conferma costituisce la decisione dell'autorita' richiesta dal
protocollo di coordinamento dopo la revisione incrociata.

## Effetto della decisione

1. RM-VARIAZIONE-01 diventa normativa per RM-0008.
2. Puo' iniziare il codice di prodotto della transizione F4 secondo la
   specifica convergente e i suoi criteri di prova.
3. Restano invariati i sette stati del coordinatore e tutti i criteri di
   arresto, recupero e autenticazione introdotti dalla variazione.
4. La primitiva per il contenitore incompleto resta una precondizione separata
   e non entra in alcun percorso automatico.

## Limiti invariati

Questa decisione non autorizza l'uso della primitiva sul negozio reale, la
rimozione di oggetti reali, modifiche ai servizi in esercizio o la
pubblicazione del candidato pubblico. Tali azioni conservano i rispettivi gate
operativi e di rilascio.

RM0008-Unita: RM-VARIAZIONE-01  
RM0008-Ruolo: autorita  
RM0008-Stato: APPROVATA  
RM0008-Ancora: a782065e  
RM0008-Percorsi: internal/design/decisione_rm0008_variazione_01_1_9_2026.md; internal/roadmap/RM-0008-porta-unica-nascita-executor.md  
RM0008-Prova: c656c241; 519154cb; conferma esplicita di Roberto del 1 settembre 2026  
RM0008-Ambito: roadmap
