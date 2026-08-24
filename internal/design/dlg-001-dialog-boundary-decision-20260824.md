# DLG-001 — decisione sul confine dei dialoghi

## Esito

La voce e' ancora attuale come domanda concettuale, ma non come modifica al
vocabolario. E' ratificata l'alternativa 3: `inputs` e `approval` restano oggetti
pubblici distinti; condividono esclusivamente infrastruttura e lifecycle.

## Motivo

`get_inputs` acquisisce valori tipizzati. `get_approval` registra una decisione
che concede o nega l'autorita' necessaria al ramo successivo. I due executor
usano gia' lo stesso storage pendente, isolamento per proprietario, `dialog_id`,
TTL, rendering per canale, sospensione/ripresa e capability canonica
`dialog.user_input`. Questa e' la generalita' tecnica reale.

Una famiglia pubblica `dialogues` non aggiungerebbe un comportamento autonomo e
renderebbe invece sostituibili due effetti di policy che devono restare
separati. Una famiglia di catalogo aggiuntiva duplicherebbe la capability gia'
firmata nei manifest senza offrire un nuovo controllo. Il criterio comune e'
quindi la capability, non il nome dell'executor e non una mappa mantenuta nel
runtime.

## Invarianti

1. Un valore libero, un `yes_no` generico o un altro input non puo' soddisfare
   un gate di approvazione.
2. Solo `get_approval` puo' produrre il ramo autorizzativo tipizzato e la sua
   ricevuta; `get_inputs` non concede autorita'.
3. Entrambi accedono allo storage solo tramite `dialog.user_input`, ristretto al
   proprietario e al dialogo corrente.
4. Cache e replay restano disabilitati per entrambi, perche' lo stato e'
   contestuale e a scadenza.
5. Nuove forme di dialogo possono riusare lifecycle e capability soltanto se
   dichiarano un effetto pubblico distinto; non entrano in una famiglia per
   somiglianza nominale.

## Impatto

Non servono migrazioni di manifest, cache, piani o i18n. Il comportamento
attuale realizza gia' la decisione; introdurre codice per rappresentarla
creerebbe una seconda fonte di verita'. DLG-001 si chiude quindi con una
decisione esplicita e senza modifica al runtime.
