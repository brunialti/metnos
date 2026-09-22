# RM-0009 — decisioni del coordinatore sul ricontrollo

Fonte codice: `1c308922839f7659a3cf54d988f995bba0f215d6`.
Stato: preparazione avanzata, non congelamento G0.6 o approvazione G0.9.
La roadmap rimane l'unico documento normativo; questo rapporto spiega le
scelte e i limiti della consegna preparatoria.

## 1. Ripartenza e confini

L'incarico RM-0008 pertinente e concluso e committato; l'albero sorgente ha
zero modifiche tracciate pendenti. `BACHECA` e `internal/coordination/` sono
untracked altrui ed esclusi. La successiva attivita della stessa task su
argomenti diversi non riapre l'attesa sul lavoro gia concluso.

Il branch `codex/rm0009-development` parte da quel commit, nel worktree
`/opt/metnos/.claude/worktrees/rm0009-development`. Sono riportati soltanto
il documento RM-0009 e i suoi preparatori identificati; non sono fusi i 13
commit esclusivi o i file sporchi del checkout principale. Le modifiche ai
prodotti eventualmente presenti su quelle linee richiedono una valutazione
separata, non un'importazione cumulativa.

`preflight.json` registra identita dei servizi e limiti. HTTP, Telegram,
worker durevole e Playwright risultano attivi sotto l'account di servizio;
non e una prova funzionale o di riavvio. Non sono aperti store installati e
non sono dichiarate osservate le loro versioni di schema.

## 2. Esame caso per caso dei rapporti

| Caso | Decisione del coordinatore | Stato |
|---|---|---|
| B, campione promotion | Momento `promotion` distinto, campione insufficiente prima delle domande e ripresa con tutti i fatti ricostruiti; schede F5.2/3 e D allineate. | Integrato nella roadmap, review parziale separata. |
| D, limiti numerici | Finitezza e tipi stretti; delta positivo; zero/zero non regressione; aumento da zero tipizzato; campioni/mediane vuoti non diventano zero; costo ignoto e veto tecnico. | Integrato, non implementato. |
| F, domande | Emissione atomica distinta da delivery e risposta; bucket UTC e cap concorrente; binding corrente; recupero su evidenza, bootstrap e timer; nessun retry cieco di delivery ignota. | Integrato, non implementato. |
| G, call graph | Builtin ordinario distinto da verb-unique; loader diretto, coda diretta e riprese censiti; undo precede enqueue e va considerato; durable riusa il punto comune. | §5.8 e F6.2 aggiornati; wire/identita e file/test completi da congelare. |
| Inventario FS-A | Accettati 33 gruppi e le semantiche dei 113 casi; corretto il testo iniziale che poteva spostare le prove sull'host. | Le fixture candidate si esercitano in Birth isolato, non con un pytest sostitutivo. Nessuna conversione eseguita. |
| Inventario FS-B | Accettati 13 gruppi consumer e radici correlate; corrette le formulazioni che potevano materializzare segreti nel figlio. | Segreto solo nel core; consumer ricevono handle opachi. Contratto broker ancora da congelare. |
| Lifecycle owner dei 57 store | Non accolta come dipendenza la proposta `D-F6.6 via D-P2.9` per store gia esistenti. Tutti i 57 ID sono assegnati in bozza a P2.9; F6.6 aggiunge soltanto i nuovi record v2/enforcement quando esistono. | Conserva il requisito normativo che I1.1 sia indipendente dalla tranche di sicurezza. |
| Versioni migrazione | I numeri dei rapporti sono candidati, non prenotazioni approvate. Lo schema non versionato nel codice non prova `user_version=0` nel DB installato. | Un migratore per DB fisico, verifica baseline prima dell'assegnazione. |
| Dati senza owner | Non diventano globali o anonimi per l'assenza della colonna; cifratura e hash di path non dimostrano ownership. | Vietato inventare owner, soprattutto per biometria; mapping/legacy quarantine da risolvere tecnicamente. |
| Proof Birth | Accettata la distinzione fra byte distribuiti, report umano e attestazione firmata verificata. | Release 53 non e EXT-RM0008-F5 e non chiude FS-A. |

## 3. Contratto Birth: parti da correggere prima dell'accordo G0.3

`birth-recheck.md` e una proposta verificata sul codice, non l'accordo fra
maintainer. Prima del congelamento servono queste precisazioni, oltre a
schema/nomi/file/test:

1. Il registro API di prodotto non puo essere modificato per chiudere un gate
   documentale che precede G0.10. G0.3 assegna la modifica e il contratto; il
   codice viene consegnato dalla successiva unita autorizzata.
2. Il controllo delle prove per **attivare** non va copiato alla cieca sul
   **ritiro/quarantine**. Perdita o revoca della prova non devono impedire la
   restrizione di una capacita pericolosa. Distinguere contratto di attivazione,
   restrizione monotona autenticata e rollback che riattiva una generazione.
3. Una lease scaduta non e fencing del target. Il binding durevole deve
   resistere anche al vecchio worker ancora vivo e distinguere richiesta mai
   registrata, in corso, rifiutata e receipt non leggibile. Unknown non e absent.
4. Decidere come si rinnova una decisione diventata stale durante
   `waiting_dependency` senza cambiare di nascosto operation/request identity
   o riutilizzare consenso per fatti diversi. Il digest di una decisione non
   e un sostituto della prova di autorizzazione al confine effettivo.
5. Congelare il consumer delle proof, senza attendere la loro disponibilita
   reale: contenitore firmato e attestazione restano consegne esterne. Nessun
   booleano, marker o fixture puo aprire D1/D2 in esercizio.

Non e disponibile in questa sessione uno strumento per inviare una richiesta
alla task esterna RM-0008. Non e stata simulata un'approvazione del maintainer
ne scritta una richiesta nella sua BACHECA o nei suoi file di coordinamento.

## 4. Questioni ancora da congelare, senza delegarle implicitamente a un medium

- **A:** identita del bisogno canonico e cicli successivi al rollback,
  watermark che distingua evento nuovo da backlog tardivo, receipt storiche
  non riassegnate al nuovo ciclo.
- **C:** snapshot realmente coerente fra DB/JSONL/RAM; barriera di writer
  completa, quiescenza e recupero; digest logico distinto dal file SQLite.
- **E:** serializzazione revoca/commit e mappa autorevole per ogni store;
  copie derivate e storiche; trattamento onesto dell'esito post-start ignoto.
- **G0.4:** il profilo a singolo principal fidato non e provato (operatore e
  servizio distinti). Il candidato e socket Unix protetto con peer verificati,
  non un bearer master mandato a una porta TCP. Il solo UID del servizio non
  deve autorizzare anche i suoi executor. Provare provisioning centrale,
  directory/inode/peer, restart e processo impostore prima di congelare S0.1.
- **H/G0.3:** le cinque precisazioni Birth sopra e accordo dei maintainer.

## 5. Incarichi e criterio di consegna

`work-manifest.draft.json` espande i due modelli `.N` in 33+13 ID concreti,
conserva le altre unita e produce **145 nodi**. I 57 store e gli 8 ingressi
di crescita hanno una collocazione esplicita nella bozza; i file condivisi
sono segnalati e non autorizzano scritture parallele.

Il controllo del grafo prova aciclicita, barriere e dipendenze richieste/vietate:
I1.1 ha 66 antenati, nessuno della tranche FS/S0/X0/enforcement; I0.1 ha 106
antenati. Non prova il contenuto degli incarichi. Ogni riga e ancora
`draft_unassignable`, senza lease o agente assegnato; simboli, migrazioni,
comandi di test e rollback mancanti sono dichiarati, non nascosti da valori
generici spacciati per prescrizioni eseguibili.

L'ordine successivo rimane: chiudere i contratti, completare G0.5/G0.6, due
dry-run medium indipendenti G0.7, review G0.8, payload normalizzato e approvazione
esatta G0.9/G0.10. I soli preparatori e le review parziali non sostituiscono
questi gate; la storia del documento non e stata rimossa.

## 6. Ricontrollo del ramo sorgente prima del checkpoint

Alle 16:33 UTC il ramo RM-0008 e risultato tracciato pulito ma avanzato a
`5c1220ac25a1899b0d88aac5540fb0ab913db5ea`. Dopo la base fissata sono presenti
`cd0776a8` (licenza MIT) e `5c1220ac` (registrazione pubblicazione).
Il diff complessivo contiene 147 file. Nelle aree runtime/executor/installer,
package, script, test e strumenti selezionate, 122 differenze consistono
esattamente nel solo commento SPDX; le altre 13 sono metadati di licenza,
testi installer, tre digest builtin, due pin del guard/preflight, pin di
pubblicazione, censimento RM-0007 e il nuovo test della licenza. Le restanti
superfici sono licenza, documentazione pubblica e rapporto di pubblicazione;
i PDF sono differenze binarie, non verifiche funzionali eseguite qui.

Non sono state introdotte nuove firme API di crescita nel diff osservato,
ma i byte e le identita attestate cambiano: non si riutilizza una prova legata
al commit precedente fingendo che copra quello nuovo. La base dei presenti
inventari resta `1c308922`; il riallineamento al seguito MIT e la ripetizione
dei censimenti sono un prerequisito esplicito del prossimo freeze G0.6. Il
branch preparatorio non e candidato a pubblicazione o distribuzione. Non e
stata cambiata la licenza di alcun file da parte del coordinatore RM-0009.

Questa osservazione non riapre l'attesa della manutenzione RM-0008 pertinente,
gia conclusa, e non certifica la pubblicazione dichiarata nel rapporto altrui
o il cold-install schedulato. Nessun test di licenza/installazione e stato
eseguito in questa consegna RM-0009.
