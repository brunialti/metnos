# RM-0008 — decisione su RM-VARIAZIONE-02

Data: 1 settembre 2026  
Decisione: `APPROVATA`

## Oggetto

La decisione riguarda esclusivamente la variazione normativa contenuta nel
commit `5a323981`, con lo stato della roadmap allineato in `efe999e2` e la
revisione tecnica dell'agente B conclusa con `ACCETTATA` nel commit
`3541d675`.

La variazione descrive per intero il passaggio del servizio HTTP vivo
dall'unita' utente al frammento dominante nello spazio di sistema, la finestra
di indisponibilita' misurabile ai due estremi e la conservazione senza
sostituzione dell'unico frammento di sistema preesistente.

Roberto ha confermato nella task dell'agente A di avere gia' autorizzato
l'agente B ad accettare la variazione. Ha inoltre approvato il passaggio in
produzione quando tutti i gate saranno chiusi e il blocco che impedisce il
ritorno automatico al passaggio precedente. Queste conferme costituiscono la
decisione dell'autorita' richiesta dal protocollo dopo la revisione
incrociata.

## Effetto della decisione

1. RM-VARIAZIONE-02 diventa normativa per RM-0008.
2. Il piano di ritiro deriva legami precedenti e nomi dominanti dallo stesso
   catalogo firmato; una lista indipendente non puo' decidere le collisioni.
3. L'unica collisione ammessa riceve l'azione nominata
   `preserve_replaced_system_unit`; una seconda collisione o una coppia
   sconosciuta arrestano il passaggio.
4. Il frammento precedente viene conservato senza sostituzione, riletto e
   legato alla ricevuta prima che il frammento firmato occupi il nome finale.
5. Il passaggio produttivo puo' essere eseguito soltanto dopo la chiusura dei
   gate tecnici, della revisione incrociata e del filtro GII.
6. Dopo il punto di non ritorno, un ritorno funzionale richiede una nuova epoca
   e non riabilita automaticamente l'unita' utente.

## Limiti invariati

Questa decisione autorizza lo sviluppo e il successivo passaggio produttivo
alle condizioni sopra elencate. Non autorizza un intervento anticipato sul
sistema in funzione, non riduce le postcondizioni del gruppo 7 e non consente
la pubblicazione del candidato prima delle prove finali, della revisione
incrociata e del filtro GII.

RM0008-Unita: RM-VARIAZIONE-02  
RM0008-Ruolo: autorita  
RM0008-Stato: APPROVATA  
RM0008-Ancora: 5a323981  
RM0008-Percorsi: internal/design/decisione_rm0008_variazione_02_1_9_2026.md; internal/design/rm0008_variazione_02_sovrapposizione_unita_1_9_2026.md; internal/roadmap/RM-0008-porta-unica-nascita-executor.md  
RM0008-Prova: 3541d675; conferme esplicite di Roberto del 1 settembre 2026  
RM0008-Ambito: roadmap
