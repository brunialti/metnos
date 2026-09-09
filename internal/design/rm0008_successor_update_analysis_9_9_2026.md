# RM-0008 — aggiornamento successore, 9 settembre 2026

## Esito

La catena ammette già una Release N+1 dopo N `PREFLIGHT_VERIFIED`, senza
rifare l'adozione legacy. Il percorso completo **non è ancora pronto**.
Il primo blocco, il bundle amministrativo imposto identico in tutta la storia,
è stato corretto nell'incremento descritto sotto; restano gli altri collegamenti.
Non eseguire altri tentativi di deployment prima di chiudere e provare l'intero percorso.

Analisi statica del worktree privato; nessuna chiamata in esercizio. Nessun
overwrite di release, bypass di firme, modifica del predecessore storico o
nuovo journal/autorità proposto. `executor_birth_admin_operations.main` resta
uno stub che restituisce 78, non un percorso alternativo.

## Ordine di intervento e prove richieste

1. **Risolvere prima il binding del bundle per release.**
   `_administrative_bundle_hash_v1` include tutti gli artifact del descrittore,
   comprese le unità group7: anche una sola unità diversa cambia l'hash.
   `_bind_candidate_cutover_materials_core_v1` lo confrontava incondizionatamente
   con il predecessore legacy iniziale; inoltre
   `_authenticate_fixed_ownership_snapshot_core_v1` esigeva lo stesso bundle
   per tutte le transazioni. È un'invariante della catena, non solo una
   collisione di file. Occorre verificare ogni bundle rispetto alla propria
   distribuzione firmata e al proprio record, conservando i binding storici;
   non basta togliere un confronto.
   Riferimenti: [hash](../../runtime/executor_birth_admin_preflight.py#L3552),
   [binding candidato](../../runtime/executor_birth_admin_preflight.py#L3801),
   [vincolo storico globale](../../runtime/executor_birth_admin_preflight.py#L7763).
   Prove: N→N+1 con helper e/o unità diversi; N ancora autenticabile;
   rigetto di bundle, firma, release, head o artifact scambiati.

2. **Separare la quiescenza iniziale da quella del successore.**
   `deploy_source_v1` chiama sempre `quiesce_legacy_systemd_v1`;
   `maintenance_targets_from_source_v1` deriva i bersagli solo dai binding
   legacy. Il guard mantiene i lock e richiede anche l'ingresso HTTP/browser
   fermo, ma questa lista non copre tutta la topologia system della Release N.
   Per N+1 serve fermare/verificare la topologia precedente autenticata,
   preservando esclusione lifecycle e quiescenza browser; non riadottare il
   vecchio account/repository. I gate di adozione `release_sequence == 1`
   esistono già in `complete_transition_cutover_v2`.
   Riferimenti: [deploy](../../install/executor_birth_transition.py#L459),
   [bersagli](../../runtime/executor_birth_service_catalog.py#L1081),
   [guard](../../runtime/contract_cutover_guard.py#L31),
   [adozione iniziale](../../install/birth_authority_provisioner.py#L5234).
   Prove: N attiva impedisce qualsiasi mutazione finché non è fermata;
   browser occupato/HTTP raggiungibile bloccano; nessuna adozione legacy per N+1.

3. **Collegare i predecessori autenticati ai due installer.**
   `_prepare_transition_receipt_material_locked_v2` dispone già, per N>1,
   di `preparation.previous_context.distribution`, selezionata tramite
   `_transition_edge_locked_v2` e l'head precedente. Riautenticare
   `encoded/signature`, poi usare `load_service_catalog_v1(record)`:
   questa API riverifica la release storica per sequenza e restituisce
   `unit_fragments`. Non usare `capture_current_service_catalog_v1` sul vecchio
   record: quella API è legata alla root del processo corrente.
   Passare i frammenti a `_install_bound_topology_v2` → `_install_core_v1`;
   ricavare analogamente l'helper precedente dal descrittore firmato.
   `prepared.materials.predecessor` è invece il predecessore legacy iniziale.
   `_publish_administrative_tree_v1` resta oggi create/replay: helper diverso
   rifiutato. Verificare preventivamente l'intero piano prima delle scritture.
   Riferimenti: [preparazione](../../install/birth_authority_provisioner.py#L4148),
   [edge](../../runtime/executor_birth_ownership_coordinator.py#L3391),
   [catalogo storico](../../runtime/executor_birth_service_catalog.py#L2475),
   [binding unità](../../install/birth_authority_provisioner.py#L4878),
   [helper](../../install/executor_birth_systemd.py#L525).
   Prove: predecessore non immediato/falso/alterato negato prima degli effetti;
   aggiornamento, replay, interruzioni di entrambe le pubblicazioni e backup
   conservati; aggiunte/rimozioni di unità negate nel primo incremento.
   Ulteriore prova necessaria prima dell'esercizio: un rifiuto dopo la sostituzione
   dell'helper ma prima del cambio head non deve lasciare senza avvio la release
   ancora selezionata. Il nuovo helper verifica la propria ricetta canonica anche
   nel percorso di avvio (`_load_installed_preflight_materials_v1` →
   `_bind_preflight_materials_core_v1` → `_service_source_identity_v1`).
   È un rischio dedotto dal codice, non una prova eseguita: verificare recupero
   esatto dell'helper precedente e disponibilità della manutenzione in quel punto.

4. **Non ripetere la neutralizzazione legacy come aggiornamento.**
   `_retire_bound_catalog_v2` è chiamata prima e dopo l'installazione da G7.
   Con il candidato N+1, `_preserve_replaced_unit_v1` trova ancora i byte N e
   li nega perché li confronta con N+1. Il successore deve verificare la
   disposizione legacy già autenticata, senza nuove mosse legacy, e produrre
   nuovamente evidenza coerente nelle due osservazioni; non restituire un
   digest inventato né saltare la verifica.
   Riferimenti: [retirement](../../install/birth_authority_provisioner.py#L4817),
   [collisione](../../runtime/executor_birth_legacy_neutralizer.py#L345),
   [doppia lettura G7](../../runtime/executor_birth_dominant_startup.py#L348).
   Prove: vecchie copie/mask intatte, loro deriva negata, entrambe le
   osservazioni valide prima/dopo la sostituzione N→N+1.

5. **Chiudere la ripresa oltre la pubblicazione dell'head.**
   Rischio dedotto dal flusso, non riprodotto in esercizio: dopo
   `HEAD_REQUIRED` (sequenza 5) ma prima di `PREFLIGHT_VERIFIED` (6),
   `_completed_transition_locked_v2` non restituisce completamento e la
   preparazione ricarica il contesto required corrente, pretendendo però che
   il suo head sia ancora quello precedente. Il required è ormai N+1.
   La ripresa deve recuperare l'esatto contesto precedente dalla storia
   autenticata o raggiungere la boundary già prevista per il preflight,
   senza riaprire un'adozione iniziale.
   Riferimenti: [preparazione](../../install/birth_authority_provisioner.py#L4173),
   [completamento](../../runtime/executor_birth_ownership_coordinator.py#L3651),
   [boundary 5→6](../../runtime/executor_birth_ownership_coordinator.py#L5023).
   Prove: interruzioni a ogni confine 1→6; in particolare dopo il cambio
   required-head, dopo l'attestazione e prima dell'append finale.

6. **Provare infine il percorso firmato completo e l'attivazione.**
   Riutilizzare `build_and_install_received_source_v1` → handoff al codice
   della nuova release → `complete_transition_cutover_v2` →
   `_activate_signed_topology_v1`. `_next_release_edge_v1` già ammette N+1
   soltanto dopo N completata. L'attivazione resta successiva a
   `PREFLIGHT_VERIFIED`; il replay completo deve solo riattestare e attivare,
   non riscrivere helper/unità o creare una nuova release.
   Riferimenti: [edge release](../../install/executor_birth_distribution_release.py#L235),
   [handoff e attivazione](../../install/executor_birth_transition.py#L295).
   Prove: fixture firmata N completata → N+1 differente → preflight →
   attivazione; tutte le denial precedenti senza effetti fuori dalle fixture;
   successivamente prova nativa isolata, non sul servizio dell'utente.

## Incremento locale già consegnato

Solo il core topologia è stato implementato: parametro esplicito
`previous_fragments`, stesso insieme di unità, preflight completo, byte e
metadati verificati, backup e pubblicazione con rename-no-replace, ripresa.
Senza parametro mantiene create/replay e collisioni. Nessuna integrazione
produttiva effettuata. Un backup già occupato mentre il predecessore è di
nuovo attivo viene rifiutato anche se identico: limite conservativo sui cicli
di contenuto, senza rimozione o sovrascrittura di backup.

Verifica: **67 passate** nei test topologia; **122 passate, 15 saltate** nella
selezione estesa con composizione G7, startup e cutover. Questo non dimostra
il deployment firmato N→N+1. Diff verificato senza errori di whitespace;
nessun pin, staging, pubblicazione o servizio live modificato.

## Esito del binding bundle per release

Incremento successivo del 9 settembre: il vincolo bundle sul predecessore
legacy ora vale solo per Release1. Il material binder controlla esplicitamente
anche gli hash del manifest e della firma contro la transazione, oltre ai
binding già presenti descriptor → artifact firmati → bundle. La storia conserva
i controlli per ciascuna transazione/attestazione, manifest, certificato e head;
nessuna vecchia firma o registrazione viene riscritta.

Limite esplicito e invariato: i bundle storici sono affermazioni di record e
attestazioni protetti, **non firme dirette sul bundle**. Lo snapshot non cattura
nuovi descriptor storici; la verifica completa degli artifact riguarda la
release selezionata. Non è una nuova autorità né una prova end-to-end di deploy.

Prove mirate: 298 passate, 2 saltate; il solo test di source-review pin è stato
eseguito, ha rilevato correttamente il sorgente cambiato ed è lasciato al
responsabile del pin (una successiva selezione lo esclude esplicitamente).
Inclusi N→N+1 con unità/helper differenti, firme Ed25519 reali verificate con
OpenSSL e rifiuti di bundle, record, attestazioni, manifest, firme, head e
certificati scambiati. I mutanti bundle ricostruiscono hash e catena dei record,
quindi vengono negati per incoerenza semantica, non per documento malformato.
Ulteriore selezione coordinator V2 + fixture di attivazione: 169 passate.

Ulteriore blocco storico identificato dal coordinamento: la verifica della
release installata applica ancora la source-review compilata corrente anche
alla Release N. Il caricamento del suo catalogo può quindi fermarsi prima del
binding bundle. Questo confine storico è un incremento separato, non risolto
qui; nessuna completezza end-to-end N→N+1 viene dichiarata.
