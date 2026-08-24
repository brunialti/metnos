# Design TODO

Stato verificato il 25 agosto 2026 dopo analisi di attualita', implementazione
dei lavori autorizzati, suite completa e cutover OPS-001. Questo file contiene
soltanto attivita' ancora reali: le analisi concluse senza una modifica utile
non restano artificialmente aperte.

## Attivi

| Priorita' | Voce | Stato | Condizione di chiusura |
|---:|---|---|---|
| P0 MAX | **AFF-I18N-001** | analisi obbligatoria prima dello sviluppo | Confronto critico documentato, specifica approvata, implementazione generale, migrazione, benchmark di routing e copertura i18n verificati senza regressioni. |
| P0 | **PUB-001 / RM-0007** | `in_progress`; M0-M1 completate, M2 in corso | Variante linguistica pubblicata da base verificata come generazione coerente; firma pura, loader sugli stessi byte, concorrenza e arresti provati su Linux e Windows. |
| P0 | **SEC-001** | attesa esterna | Audit indipendente svolto da un soggetto diverso dall'implementatore; finding classificati e chiusura verificata di quelli alti o bloccanti. |
| P1 | **MAN-I18N-001 / RM-0002** | `in_progress`; L0-L4 completate, L5-L6 dopo RM-0007 | Lingua sempre esplicita, validatore reale coperto, confronto deterministico prima della pubblicazione, inventario comune in audit, nessun falso blocco e due cicli di regressione verdi. |
| P1 | **EXEC-BIND-001** | analisi separata; nessuna implementazione autorizzata | Stabilire se e come legare i byte verificati a quelli eseguiti per processi locali, builtin e bundle remoti, censendo prima la chiusura reale delle dipendenze. |
| P1 | **REL-001** | osservazione temporale | Almeno un ciclo di release con telemetria versionata e volume sufficiente per dominio; ratifica degli SLO sulla base dei dati osservati. |

`AFF-I18N-001` è la massima priorità, ma non autorizza una modifica immediata:
prima richiede analisi, confronto delle alternative e progettazione approvata.
`PUB-001` è il prerequisito di sicurezza per rendere bloccanti le nuove regole
di RM-0002. Di RM-0002 possono iniziare prima L0-L3; L2 produce l'inventario
neutro condiviso che RM-0007 consuma, mentre L5 richiede il confine di
pubblicazione già in servizio. I due progetti non devono essere fusi: uno
governa la pubblicazione linguistica, l'altro controlla la lingua e gli
invarianti macchina.
`EXEC-BIND-001` conserva il rischio deliberatamente escluso dalla revisione
KISS di RM-0007. Prima di proporre copie di codice o binding di release deve
censire file dichiarati, import, risorse locali, builtin già caricati,
invocazioni remote e identità del bundle accodato; la soluzione dovrà essere
generale e non basata su nomi executor.
`SEC-001` non puo' essere autocertificato da chi ha realizzato le modifiche.
`REL-001` dispone gia' di schema, raccolta, classificatore privacy-safe, report
atomico e test; il tempo di osservazione non puo' essere sostituito da dati
sintetici.

### AFF-I18N-001 - Internazionalizzazione completa di `affinity` (priorità massima)

**Problema verificato.** Nei manifest correnti <code>affinity</code> è una lista
piatta che mescola termini italiani e inglesi. Il loader la usa identica per
ogni lingua, mentre localizza davvero <code>description</code> e le descrizioni
degli argomenti. Il confronto semantico BGE-M3 attenua alcune lacune ma non è
un inventario di traduzioni, non misura la copertura e non garantisce
l'allineamento di una nuova lingua. ADR 0124 proponeva un dizionario di lemmi,
ma è ancora <code>proposed</code> e i relativi artefatti runtime non esistono.
Lo script storico che copia <code>affinity</code> nel database i18n non è letto
dal percorso di routing e quindi non risolve il problema.

**Sequenza obbligatoria.** Nessuna modifica al formato dei manifest o al
routing deve precedere questi risultati:

1. **Analisi tecnica approfondita.** Censire schema, loader, pre-filtro,
   confronto per frasi, fallback semantico, cache, linter, ammissione, Synt,
   importazione delle skill, catalogo incorporato e manifest firmati. Misurare
   la copertura reale per lingua e distinguere executor distribuiti,
   incorporati, generati e importati.
2. **Confronto critico e controverso.** Mettere in competizione almeno: lista
   mista corrente; tabelle per lingua nel manifest; lessico semantico centrale;
   dizionario di lemmi; risorse tradotte e compilate; instradamento solo
   vettoriale; soluzione ibrida. Per ogni opzione valutare determinismo,
   falsi positivi e negativi, morfologia, ambiguità, costo editoriale,
   aggiunta di lingue, firme, cache, migrazione, ripiego e possibilità di
   rollback. Una revisione avversaria deve provare a confutare la soluzione
   preferita.
3. **Documento di analisi.** Pubblicare baseline riproducibile, alternative,
   esperimenti, risultati, rischi, opzione raccomandata e motivazione delle
   opzioni respinte. I benchmark devono includere corpus reale anonimizzato e
   un insieme di prova tenuto fuori dalla progettazione.
4. **Specifica di progetto.** Solo dopo la decisione: definire schema
   versionato, autorità delle fonti, lingue enumerate, catena di ripiego,
   traduzione e allineamento, controllo di copertura, compatibilità con firme e
   cache, migrazione atomica, rollback e criteri di accettazione. La specifica
   deve essere approvata prima dello sviluppo.
5. **Sviluppo generale.** Implementare la soluzione scelta senza elenchi
   cablati per executor o correzioni ad hoc. Aggiornare tutti i consumatori e i
   produttori del campo, la validazione, gli strumenti di traduzione, le firme,
   i manifest esistenti e la documentazione italiana e inglese.
6. **Certificazione.** Eseguire test unitari, integrazione, migrazione e
   rollback; controllo automatico della parità linguistica; benchmark di
   routing per lingua, morfologia e richieste miste; verifica che precisione,
   richiamo, latenza e determinismo non peggiorino oltre le soglie approvate.

**Condizione di chiusura.** Analisi e specifica approvate; nessun percorso
runtime legge la vecchia lista senza passare dal contratto scelto; copertura e
allineamento sono verificabili automaticamente; tutti i manifest e gli
executor generati/importati sono migrati; firme e cache sono valide; benchmark,
suite completa, documentazione bilingue e distribuzione pubblica sono verdi.

### EXEC-BIND-001 - Identità del codice verificato fino all'esecuzione

**Problema verificato.** Il loader controlla il digest dei file dichiarati, ma
il runner locale riapre successivamente il percorso dell'entry point. I builtin
usano moduli già caricati dalla release, mentre il trasporto remoto costruisce
un bundle in un momento ancora diverso. La revisione KISS di RM-0007 impedisce
a una traduzione di firmare codice cambiato, ma non pretende che queste tre
forme eseguano necessariamente gli stessi byte osservati dal verificatore.

**Analisi obbligatoria.** Prima di progettare una soluzione:

1. censire per ogni trasporto file dichiarati, import Python, risorse lette a
   runtime, shim condivisi e dipendenze fornite dalla release;
2. seguire l'identità del contratto da catalogo, cache e scheduler fino a
   sandbox, runner, coda remota, download del bundle e percorso `reverse`;
3. verificare se le invocazioni remote sono legate a hash di manifesto e codice
   oppure soltanto al nome corrente dell'executor;
4. distinguere il callable builtin già importato dai byte presenti in seguito
   sul filesystem;
5. confrontare almeno bundle content-addressed, descrittori mantenuti aperti,
   ambienti di esecuzione immutabili, binding della release e combinazioni
   ibride;
6. misurare costo su spazio, latenza, cache, installazione, aggiornamento,
   rollback, Windows e dispositivi remoti;
7. fare revisionare avversarialmente la chiusura delle dipendenze: copiare i
   soli `[code].files` non è sufficiente se l'executor usa file non dichiarati.

**Vincolo.** Nessuna modifica al runner o copia generalizzata del codice è
autorizzata prima dell'analisi. La soluzione non può contenere tabelle di nomi
executor né trattamenti speciali per singoli domini. Se il rischio residuo
risulta accettabile nel modello di minaccia, anche la decisione di non
implementare deve essere documentata con prove.

**Condizione di chiusura.** Ogni invocazione locale, builtin e remota è legata a
un'identità immutabile verificabile fino al codice o callable eseguito, oppure
un'analisi approvata dimostra perché quella garanzia non è richiesta. Sono
obbligatori test di sostituzione concorrente, invocazione accodata seguita da
nuova pubblicazione, aggiornamento release, rollback e matrice Linux/Windows.

### SEC-001 - Audit di sicurezza indipendente

- Perimetro: vault e mandati, OTP email, browser/sites, prompt injection,
  sandbox, executor remoti, log, allegati e futuro trasporto MCP.
- Evidenza richiesta: threat model aggiornato, identita' e indipendenza del
  revisore, finding con gravita', riproducibilita' e stato di remediation.
- Vincolo: una revisione interna aggiuntiva puo' preparare il materiale, ma non
  soddisfa il requisito di indipendenza.

### REL-001 - Affidabilita' osservata nel tempo

- Contratto: `internal/design/rel-001-observed-reliability-contract-20260824.md`.
- Baseline: `internal/reports/reliability_snapshot_20260824.json`.
- Implementato: esiti terminali tipizzati, versione prodotto, origine del
  routing, dominio derivato dalla grammatica canonica, falsi successi, parziali,
  timeout, recovery e p95; nessun testo, path, argomento, utente o id di turno
  viene esportato.
- Gap deliberato: i record anteriori alla telemetria restano `pre-telemetry` e
  non possono sostenere confronti fra release. Le soglie per dominio saranno
  fissate soltanto dopo un campione osservato sufficiente.

## Completati il 24 agosto 2026

- **DEV-001** — contesto di destinazione circoscritto alla conversazione,
  scadenza e aggiornamento da esecuzione osservata.
- **UND-001** — censimento a tre stati, ricevute inverse esatte e
  riprogettazione di `delete_dirs`; `set_credentials` e `set_persons` restano
  intenzionalmente non annullabili.
- **TUT-001** — obblighi informativi composti, recupero bilanciato e due cicli
  completi Tutor verdi; turno live conclusivo `676b64e852e54168`.
- **UI-AUTH-001** — login amministrativo HTML/JSON tipizzato, ritorno sicuro e
  distinzione fra sessione assente, scaduta e ruolo insufficiente.
- **UI-MODEL-001** — identita' effettiva osservata per binding e tier, cache e
  stati non determinato/non raggiungibile senza nomi inventati.
- **EXE-001** — ammissione standard unica per Synt e promoter; catalogo attivo
  senza percorso legacy concorrente.
- **RLS-001** — installazione, upgrade e rollback ripetibili sulla matrice
  supportata, con stato sintetico e produzione invariata.
- **I18N-DEDUP-001** — classificazione completa, allowlist minima e gate su
  collisioni, placeholder e drift seed/live/device.
- **DOC-IT-001** — revisione tecnica italiana e parita' semantica inglese;
  parsing, struttura e collegamenti verificati. Le opere autoriali restano fuori
  perimetro e non sono debito tecnico.
- **CONV-001** — routing tipizzato delle richieste senza azione; nessun executor
  operativo viene scelto per conversazione o aiuto Metnos.
- **PERF-002** — budget FIFO condiviso per host fra invocazioni e processi,
  senza ridurre il parallelismo fra host distinti.
- **DLG-001** — decisione chiusa: infrastruttura comune dei dialoghi, ma oggetti
  pubblici e confini di autorita' distinti per input e approvazione.
- **MCP-001** — decisione chiusa: MCP ammesso solo come backend o proxy stretto
  di executor conformi; nessun secondo linguaggio libero nel planner.
- **EXE-DESC-001** — analisi conclusa; i warning editoriali restano una misura,
  non autorizzano riscritture senza benchmark di equivalenza.
- **CLN-001** — primo passaggio controllato chiuso: rimossa la fonte legacy non
  raggiungibile del promoter; nessuna cancellazione basata su eta' o nome.
- **OPS-001** — cutover live completato: servizio HTTP di sistema inattivo e
  disabilitato, `metnos.target` utente attivo e abilitato, due pilot con
  rollback, turno di cutover e due cicli post-cutover verdi. Evidenza in
  `internal/reports/stack_live.*` e `internal/reports/stack_lifecycle.*`.

Restano inoltre archiviati come completati **WIN-001**, **PKG-001**,
**JOB-001**, **QUA-001** e **RUN-001**. **RED-001** e' ritirato: il dominio
Reddit non verra' implementato e non lascia attivita' pendenti.
