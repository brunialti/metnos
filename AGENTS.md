# Metnos — istruzioni per un agente di sviluppo

Corto apposta. Se non lo leggi tutto in un minuto, non serve a niente.

## A inizio sessione, sempre

1. `CLAUDE.md` — le norme del progetto. **Invariante**: lo modifica solo
   Roberto. Se una regola ti sembra superata, non toccarla: scrivi una
   richiesta (punto 3).
2. `CLAUDE.mutabile.md` — stato corrente e decisioni di runtime. Questo lo
   mantieni tu, secondo le regole in testa al file.
3. **`internal/coordination/richieste-aperte.md`** — le richieste che un
   agente ha lasciato a un altro. Leggile: potresti essere tu il
   destinatario, e potrebbero riguardare quello che stai per fare.
4. `git status`. Su questo repository lavorano piu' agenti insieme
   (Claude, Codex) e sessioni diverse in parallelo. **Non sovrascrivere
   modifiche che non sono tue.** Se trovi lavoro altrui in corso, committa
   separatamente il tuo, diviso per contenuto.

## A fine lavoro

Se lasci qualcosa che tocca a un altro — un passo che non puoi fare, una
domanda che non puoi risolvere, un albero da ripulire — **scrivilo nella
bacheca**, non nel messaggio di commit. Una voce dice chi la chiede, a chi,
perche', e come si capisce che e' chiusa. Si chiude scrivendo l'esito, non
cancellando la riga.

La bacheca vale nei due sensi: e' anche il posto dove rispondi.

## Due cose che costano care se non le sai

**Pubblicare un executor.** `runtime/sign.py publish` non funziona piu'.
Il raccordo che lo sostituisce esiste (`stack_reconcile deploy --executor
<nome> --sign`, produttore `stack_reconcile/restart_sign_first`) ma non e'
certificato: non e' mai stato eseguito davvero e riavvia anche il target.
Finche' la R-003 in bacheca e' aperta, non si pubblica. Dettaglio in
`internal/AGENTS.md` §6.

**Mai pubblicare da un albero di lavoro.** Il magazzino dei contratti sta
fuori dal repository ed e' unico per tutta la macchina: una pubblicazione da
un worktree finisce in produzione. E' successo il 30/8/2026 e il catalogo
vivo e' sceso da 122 a 101 executor. Si pubblica solo da `/opt/metnos`, a
ramo fuso e albero pulito.

## Dove sta il resto

`internal/AGENTS.md` — orientamento lungo: mappa del codice, fonti e
autorita', metodo di lavoro, criteri di revisione. Leggilo quando ti serve
il dettaglio, non a ogni sessione.
