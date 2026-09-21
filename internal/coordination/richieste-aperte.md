# Richieste aperte fra agenti

Un agente lascia qui una richiesta a un altro. Si legge a inizio sessione e
si chiude la voce quando il lavoro e' fatto, **scrivendo l'esito**, non
cancellando la riga.

Formato: una voce per richiesta, con chi la chiede, a chi, perche' e come si
capisce che e' chiusa.

---

## R-001 — Finalizzare il lavoro in corso in `/opt/metnos`

- **Chiede**: Claude (sessione RM-0011), 21/9/2026
- **A**: l'agente che sta lavorando nel checkout principale
- **Stato**: aperta

Il checkout `/opt/metnos`, ramo `session/detection-lexicon-i18n`, ha **49
modifiche non committate**, fra cui la cancellazione di tutto l'installer
(`install/*.py`, `install/*.sh`) con i corrispondenti `*.retired-v1` non
tracciati, e `CLAUDE.mutabile.md` modificato. C'e' anche un permesso negato
su `install/data/`.

Finche' resta cosi', **dal checkout principale non si puo' pubblicare
niente**, e la pubblicazione si fa solo da li' (`internal/AGENTS.md` §6.3).

**Chiuso quando**: `git status` in `/opt/metnos` e' pulito, o le modifiche
sono su un ramo proprio, e questa voce riporta cosa e' stato fatto.

---

## R-002 — Pubblicare RM-0011 (13 executor nuovi, 23 modificati)

- **Chiede**: Claude (sessione RM-0011), 21/9/2026
- **A**: l'agente che prende in carico la produzione (Roberto ha assegnato
  la pubblicazione a un altro agente il 21/9)
- **Stato**: aperta, **dipende da R-001**

Il ramo `session/rm0011-provider` chiude F0, F1 e F2 di
`internal/roadmap/RM-0011-un-solo-modo-di-esprimere-un-provider.md`: il
fornitore diventa un argomento e non un suffisso nel nome.

Pronto e verificato:

- 13 executor nuovi, prove di nascita verdi per invocazione diretta;
- 405 prove di nascita su 406, su 98 executor (l'unica rossa e'
  `undo_last_turn`, che e' rossa proprio perche' manca la firma);
- suite: **37 rosse contro le 41 di `main`**, e tutte e 37 sono del cancello
  di nascita, verificate una per una;
- `internal/tools/check_github_retirement.py` prova che tutti e 16 i
  contratti `*_github` hanno un sostituto che accetta i loro argomenti
  obbligatori (16/16, zero problemi).

**Ordine obbligato per F3**, misurato, in `RM-0011 §F3.1`: prima si
ritirano i sedici contratti, **poi** si toglie `github` da
`vocab.PROVIDER_SUFFIXES`. Al contrario il cancello di messa a fuoco si
spegne mentre i sedici sono ancora installati, e una domanda sul filesystem
locale torna a finire su GitHub (misurato: primi tre da 156 a 153).

**Il ramo non contiene solo RM-0011.** Chi lo fonde deve sapere che tocca
due cose trasversali, entrambe con la suite invariata (37 rosse prima e
dopo, tutte del cancello di nascita):

- **l'involucro dei contratti generati** guadagna un `origin` che
  sopravvive alla promozione, e i tre punti che generano un sintetizzato lo
  dichiarano (`generated_executor_contract.py`, `synt.py`,
  `synth_request.py`). Serviva perche' il sintetizzatore promuove ad
  `active`, la riga del ciclo di vita sparisce e un sintetizzato promosso
  era indistinguibile da uno scritto a mano;
- **`loader._is_synth` e `_is_imported` non leggono piu' il percorso** ma
  `ex.source`. Prima l'esenzione dal confronto di sovrapposizione la
  decideva la cartella, e la sua intera popolazione erano i sedici
  contratti GitHub — roba nostra con un'esenzione scritta per gli estranei.

Se stai lavorando su synt, sul codegen delle skill o sul caricatore,
guarda qui prima di fondere: e' la zona dove i due lavori si incontrano.

**Chiuso quando**: i contratti sono in esercizio, le prove di catalogo sono
verdi e questa voce riporta la generazione pubblicata.

---

## R-003 — Qual e' oggi la procedura che sostituisce `sign.py publish`?

- **Chiede**: Claude (sessione RM-0011), 21/9/2026
- **A**: chi ha costruito RM-0008 (Codex)
- **Stato**: aperta — **e' la domanda che blocca tutto il resto**

`runtime/executor_birth_intent.py` espone undici produttori di nascita
(`change_extend`, `change_rollback`, `synth_multistage`, `synth_specialize`,
`synth_approve`, `promote`, `stack_reconcile`, `skills`, `installer`,
`builtin_generation`, `promoter_rollback`). **Nessuno copre il caso «un
agente ha modificato un executor a mano e vuole pubblicarlo»**, che e'
esattamente cio' che faceva `sign.py publish`.

La domanda e' aperta dal 30/8 — sta gia' in
`internal/design/handover_rm0008_blocco_pubblicazione_30_8_2026.md` §4.1 — e
non ha ancora risposta. Finche' non ce l'ha, nessuna manutenzione di un
executor entra in esercizio, RM-0011 compresa.

Serve una riga sola: **quale produttore si usa, oppure quale ne va
aggiunto.** Va scritta in `internal/AGENTS.md` §6.2, che oggi documenta la
lacuna.

### Direzione data da Roberto il 21/9, e cosa ho verificato

Roberto: gli undici sono l'origine dell'esigenza, ma «dovrebbero essere
fattorizzati e incanalati verso una unica richiesta che discrimina, oppure
verifica l'origine dei cambiamenti e agisce opportunamente».

**L'imbuto esiste gia'.** Tutti e undici gli `submit_*` sono involucri di
tre righe attorno a `_submit(intent, capability)`, che chiama
`executor_birth_operational._execute_intent_with_capability`. Un solo punto
di passaggio, gia' oggi.

Gli undici non sono porte: sono **etichette di autorita'**, coppie
`(producer_id, operation)` costruite con un sigillo
(`_CAPABILITY_SEAL`) che un chiamante non puo' fabbricare. Dicono *chi* sta
nascendo *che cosa*: `change_applier/extend`, `promoter/rollback`,
`stack_reconcile/restart_sign_first`.

**Quello che manca e' un produttore per la manutenzione manuale**, non un
imbuto.

### Avvertimento, dalla stessa giornata

«Verificare l'origine dei cambiamenti e agire di conseguenza» e' attraente,
ma va costruito con cura: se il produttore si **deduce** dai file toccati,
si sta deducendo un'**autorita'** dal filesystem. E' lo stesso errore
trovato oggi due volte in `loader.py`, dove `_is_imported` e `_is_synth`
deducevano dalla cartella cosa fosse uno strumento
(`internal/design/TODO.md`, AFF-OVERLAP-001): il primo tentativo di
correzione ha fatto sparire un contratto dal catalogo, il secondo ha
disattivato il controllo di sovrapposizione. Il sigillo esiste proprio
perche' l'autorita' sia **concessa**, non dedotta.

**Forma proposta**, che rispetta la direzione senza dedurre autorita': un
dodicesimo produttore per la manutenzione governata, la cui capacita' non si
deduce dai file ma si ottiene da un'**approvazione umana esplicita** — la
filiera dei cambiamenti con il vaglio su `/admin/changes` esiste gia' ed e'
il posto naturale. L'imbuto resta uno, la regola resta una, e chi modifica a
mano non si autoproclama produttore.

**Chiuso quando**: §6.2 riporta la procedura, e un executor modificato a
mano e' stato pubblicato seguendola.

---

## R-004 — `CLAUDE.md` §7.10 e' obsoleto (per Roberto)

- **Chiede**: Claude (sessione RM-0011), 21/9/2026
- **A**: Roberto — `CLAUDE.md` e' invariante e lo modifica solo lui
- **Stato**: aperta

§7.10 prescrive `python3 runtime/sign.py publish executors/<name>` come
passaggio **obbligatorio** dopo ogni modifica a un executor. Quel comando
oggi si rifiuta sempre. Un agente che segue la norma alla lettera sbatte
contro un errore e non trova scritto da nessuna parte cosa fare.

La correzione dipende da R-003: prima si stabilisce la procedura, poi si
scrive in §7.10.
