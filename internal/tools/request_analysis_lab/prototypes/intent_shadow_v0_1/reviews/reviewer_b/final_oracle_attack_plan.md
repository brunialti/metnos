# Piano avversariale indipendente per l'oracolo finale

Data: 2026-08-12  
Autore: Revisore AI B, indipendente e non umano  
Stato: piano soltanto; nessuna adjudication e nessuna nuova policy

## Confini di esecuzione

- Eseguire tutti i controlli in sola lettura sugli artefatti del repository.
- Eseguire le mutazioni negative soltanto su oggetti JSON in memoria; non scrivere copie mutate nel workspace.
- Non avviare GPU, executor, servizi o chiamate di rete e non creare commit.
- Usare come autorità soltanto contratto, registro/freeze, fonti con impronta, decisioni esplicite di Roberto e adjudication approvate. L'accordo tra output dei bracci non è gold.
- Una discordanza non coperta dalle decisioni esistenti è un FAIL da segnalare, non un'autorizzazione ad adjudicare.

## Controlli

### Binding dei 120 casi e hash

1. **ATK-01 — JSON leggibile e formato dichiarato.** Decodificare l'oracolo una sola volta con parser rigoroso, rifiutando chiavi duplicate. PASS se il documento è valido e dichiara il formato e la versione di contratto attesi.
2. **ATK-02 — Cardinalità esatta.** Contare le voci dell'oracolo. PASS soltanto con 120 casi.
3. **ATK-03 — Bijezione col banco congelato.** Costruire dai due file le mappe per indice e per hash. PASS se ogni richiesta congelata ha una e una sola voce e non esistono voci estranee.
4. **ATK-04 — Testo esatto.** Confrontare per indice i byte UTF-8 del testo, inclusi maiuscole, apostrofi, spazi e punteggiatura. PASS se tutti i 120 testi coincidono col banco.
5. **ATK-05 — Hash ricalcolati.** Ricalcolare `sha256(query_text.encode("utf-8"))` per ogni caso. PASS se tutti i digest coincidono col campo dichiarato e col digest congelato.
6. **ATK-06 — Unicità identificatori.** Verificare unicità separata di indice, identificatore del caso e hash. PASS se ciascun insieme contiene 120 valori.
7. **ATK-07 — Ordine e serializzazione deterministici.** Verificare che i casi seguano l'ordine congelato e che due serializzazioni canoniche dello stesso oggetto producano gli stessi byte e digest.

### Tipizzazione, registro e grafi

8. **ATK-08 — Radice esclusiva.** Ogni expected deve avere esattamente una delle tre radici `operation_graph`, `system_control` o `unrepresentable`, con soli campi ammessi per quella radice.
9. **ATK-09 — Forma ricorsiva del grafo.** Ogni `operation_graph` deve avere un `body` non vuoto; ogni nodo deve essere esclusivamente `operation` o `barrier`; ogni ramo deve essere non vuoto e tipizzato.
10. **ATK-10 — Route chiuse.** Ogni `route` deve essere una chiave esatta di `registry.operations`; vietare correzioni fuzzy, sinonimi e route plausibili non registrate.
11. **ATK-11 — Reason chiuse.** Ogni `unrepresentable.reason` deve essere una chiave esatta di `registry.unrepresentable_reasons` e la radice non deve contenere un sottografo parziale.
12. **ATK-12 — Control chiusi.** Ogni `system_control.control` deve essere una chiave esatta di `registry.system_controls`; nessun controllo può comparire come operation.
13. **ATK-13 — Barrier e outcome chiusi.** Ogni barriera deve appartenere a `registry.barriers`; outcome unici, completi quando richiesto, nell'ordine congelato, con soli campi ammessi.
14. **ATK-14 — Dominanza di `data_from`.** Ogni arco deve contenere solo `from`, puntare a un ordinal intero precedente e visibile nel percorso corrente; vietare self-reference, forward-reference e sorgenti future.
15. **ATK-15 — Proprietà dei rami.** Vietare riferimenti a producer di rami fratelli o discendenti e verificare che gli effetti condizionati restino nel caso della barriera che li possiede.
16. **ATK-16 — Ordine semantico già adjudicato.** Per i casi risolti, confrontare ordine e dipendenze con le decisioni approvate, inclusi `read/events -> create/files`, `read/issues -> set/issues` condizionato e i grafi minimi a nodo singolo. Non inferire nuovi ordinamenti.

### Freeze, provenienza e fonti

17. **ATK-17 — Binding al contratto e al registro.** Confrontare `contract_version` e `registry_payload_sha256` dell'oracolo con registro e freeze; ogni differenza è FAIL.
18. **ATK-18 — Integrità del registro.** Ricalcolare il payload canonico del registro secondo la convenzione dichiarata e verificare digest interno, file freeze e stato di revisione.
19. **ATK-19 — Impronte delle sorgenti del registro.** Per ogni operation/control/barrier verificare manifest, codice, firma e digest dichiarati quando presenti. Nessuna sorgente mancante o cambiata può essere ignorata.
20. **ATK-20 — Impronte dell'audit delle fonti.** Ricalcolare gli hash dei file elencati da `intent_shadow_oracle_source_audit_v0_1.json`; PASS con zero mismatch e `audit_error_count == 0`.
21. **ATK-21 — Integrità del banco congelato.** Verificare percorso, conteggio e hash del campione indicati dall'audit/freeze prima e dopo la revisione.
22. **ATK-22 — Output salvati non promossi a gold.** Verificare hash e immutabilità di c10, c11, repaired e sealed; controllare che la provenienza dell'oracolo non citi il loro accordo come autorità semantica.
23. **ATK-23 — `catalog_snapshot` arretrato contro manifest correnti.** Diffare meccanicamente nomi e digest del catalogo snapshot rispetto ai manifest reali. Ogni aggiunta, rimozione o variazione deve essere riportata; l'oracolo resta validato contro il registro congelato e non può incorporare silenziosamente il catalogo futuro.
24. **ATK-24 — Dichiarazione double-AI non umana.** I metadati devono dichiarare due revisioni AI indipendenti, nessuna revisione umana e nessuna falsa attribuzione a Roberto oltre alle sue decisioni esplicite.
25. **ATK-25 — Tracciabilità delle decisioni.** Ogni override dei 18 casi divergenti e dei casi 38, 84 e 113 deve puntare alla decisione/adjudication pertinente; gli altri casi devono conservare una provenienza verificabile senza retrofitting dagli output.

### Totale 34+4 e duplicati

26. **ATK-26 — Baseline dei 34 controlli.** Verificare hash congelato e conteggio esatto di `question_focus_controls_v1.json`; nessuna modifica o sostituzione è ammessa.
27. **ATK-27 — Quattro aggiunte e totale 38.** Contare esattamente quattro nuovi controlli, verificare i quattro expected approvati e confermare `34 + 4 == 38` senza perdere casi esistenti.
28. **ATK-28 — Collisioni testuali e hash.** Ricalcolare tutti i digest e verificare assenza di duplicati o collisioni tra i 38 controlli, tra i quattro nuovi e rispetto alle 120 richieste quando dichiarati nuovi.
29. **ATK-29 — Duplicati semantici.** Eseguire una matrice cieca di intenti normalizzati sui 38: soggetto, temporalità, operazione, oggetto, radice e confine avversariale. PASS se i quattro nuovi coprono distintamente fail-closed, vista multi-corpus materializzata, proiezione strutturata e similarità da reference photo, senza duplicare i 34.

### Mutazioni negative che devono fallire

30. **ATK-30 — Mutazioni di tipo e vocabolario.** Su copie in memoria provare, una alla volta: route, reason, control o barrier sconosciuti; root mescolate; campi extra; outcome mancante, duplicato o riordinato. PASS soltanto se ogni mutante è rifiutato.
31. **ATK-31 — Mutazioni di binding e dipendenza.** Su copie in memoria provare: cambiare testo senza hash, scambiare hash tra due indici, duplicare/eliminare un indice, creare `data_from` verso sé/futuro/ramo fratello e invertire producer-consumer. PASS soltanto se ogni mutante è rifiutato.

### Integrità del laboratorio e della produzione

32. **ATK-32 — Zero deriva esterna.** Confrontare snapshot pre/post di hash e stato Git per banco, registro/freeze, output salvati, checkpoint, handover e file di produzione; confrontare inoltre processi/servizi senza avviarli. PASS con zero variazioni attribuibili alla verifica, nessun servizio riavviato e nessun processo GPU creato.

## Criterio finale

Il risultato complessivo è PASS soltanto con 32/32 controlli superati. Qualsiasi falso zero, mismatch di provenienza o caso semanticamente non coperto resta un errore esplicito da sottoporre a Roberto; il piano non lo adjudica.
