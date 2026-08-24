# Design TODO

Stato verificato il 24 agosto 2026 dopo analisi di attualita', implementazione
dei lavori autorizzati, suite completa e cutover OPS-001. Questo file contiene
soltanto attivita' ancora reali: le analisi concluse senza una modifica utile
non restano artificialmente aperte.

## Attivi

| Priorita' | Voce | Stato | Condizione di chiusura |
|---:|---|---|---|
| P0 | **SEC-001** | attesa esterna | Audit indipendente svolto da un soggetto diverso dall'implementatore; finding classificati e chiusura verificata di quelli alti o bloccanti. |
| P1 | **REL-001** | osservazione temporale | Almeno un ciclo di release con telemetria versionata e volume sufficiente per dominio; ratifica degli SLO sulla base dei dati osservati. |

Non restano attivita' di sviluppo immediatamente eseguibili. `SEC-001` non puo'
essere autocertificato da chi ha realizzato le modifiche. `REL-001` dispone gia'
di schema, raccolta, classificatore privacy-safe, report atomico e test; il
tempo di osservazione non puo' essere sostituito da dati sintetici.

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
