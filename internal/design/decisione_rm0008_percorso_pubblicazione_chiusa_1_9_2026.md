# RM-0008 — percorso di pubblicazione dopo la chiusura F4

Data: 1 settembre 2026  
Decisione: `APPROVATA, ESECUZIONE DIFFERITA AI GATE FINALI`

## Autorita'

La decisione trascrive due autorizzazioni dirette ricevute nella task
principale RM-0008:

```text
approvo passaggio in produzione quando avete finito
approvo il blocco che impedisce il ritorno al vecchio passaggio
```

La prima autorizza il futuro passaggio produttivo soltanto dopo sviluppo,
prova totale, revisione incrociata e filtro GII. La seconda rende normativa la
negazione permanente del vecchio percorso nella build chiusa. Nessuna delle
due autorizza un intervento anticipato sul sistema in funzione.

## Percorso sostitutivo

Non esiste un nuovo comando manuale che esponga chiavi o la primitiva di
scrittura. Per l'edit ordinario di un executor il comando operativo e':

```text
./.venv/bin/python runtime/stack_reconcile.py deploy --executor <name> --sign
```

L'opzione storica `--sign` non seleziona piu' il vecchio firmatario: in
`verify_named_executors(..., sign_first=True)` prepara una copia candidata e
la consegna a `submit_stack_reconcile_birth`, cioe' alla facciata Producer
registrata di Executor Birth. Solo il nucleo sigillato puo' arrivare a
`commit_birth_snapshot`. Il comando esegue poi il riavvio controllato.

Gli altri produttori usano nello stesso modo la propria facciata nominale:
Change, Synth, promoter, skills, installer e generatore builtin non condividono
una scorciatoia generica. Un agente non chiama direttamente chiavi, negozio,
pubblicatore interno o `commit_birth_snapshot`. Un riavvio isolato non rende
vivi byte candidati.

## Ordine di consegna F4

1. Costruire la distribuzione chiusa in una radice isolata e autenticarne i
   byte, la testa richiesta, il certificato, il record di transizione e il
   prerequisito di avvio.
2. Eseguire server completo e turni reali sulla copia; congelare il candidato.
3. Eseguire una sola suite totale, revisione incrociata conclusiva e filtro GII
   forte sul contenuto destinato al repository pubblico.
4. Pubblicare prima la nuova catena e il nuovo percorso verificato; soltanto
   dentro la transizione amministrativa gia' autorizzata ritirare le vecchie
   unita' e rendere richiesta la nuova testa.
5. Avviare attraverso il controllo del prerequisito, verificare le
   postcondizioni e applicare il recupero mirato del contenitore incompleto.

L'ordine impedisce una finestra nella quale il percorso precedente sia chiuso
senza che quello nuovo sia gia' verificato. Dopo la nuova testa richiesta, la
build precedente resta negata; un ritorno funzionale richiede una nuova
release con sequenza superiore.

## Effetto documentale

`CLAUDE.md` §7.10 viene aggiornato nello stesso candidato: il comando
`runtime/sign.py publish` non e' piu' prescritto e viene sostituito dal
passaggio nominale attraverso `stack_reconcile` ed Executor Birth. Le pagine
pubbliche e la roadmap saranno aggiornate in inglese sul candidato finale,
dopo R3 e prima del filtro GII.

RM0008-Unita: F4-EPOCA-01  
RM0008-Ruolo: autorita-integrata  
RM0008-Stato: APPROVATA  
RM0008-Ancora: decisione diretta del 1 settembre 2026  
RM0008-Percorsi: CLAUDE.md; internal/design/decisione_rm0008_percorso_pubblicazione_chiusa_1_9_2026.md  
RM0008-Prova: autorizzazione al passaggio finale; autorizzazione al blocco del vecchio percorso
RM0008-Ambito: roadmap
