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
