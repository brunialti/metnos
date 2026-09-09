# Consegna RM-0008 — Release 3 costruita, non ancora attraversata (9/9/2026, sera)

Documento per l'agente che subentra. Stato reale al momento della scrittura,
niente promesse: ogni affermazione qui sotto e' stata misurata, e dove non lo
e' stata c'e' scritto.

## 1. Dove siamo in una riga

La Release 3 e' **costruita, firmata e installata** accanto a quella in
esercizio. Non e' stata attraversata: i servizi girano ancora sul codice
precedente. Il passaggio finale e' pronto, ha una modalita' di sola verifica,
e va lanciato da Roberto con `sudo`.

## 2. Ordini dell'utente in questa sessione, nell'ordine

1. riprendere da `handover_rm0008_stop_9_9_2026_1526.md`;
2. aggiungere l'uscita in avanti alla catena invece di tornare indietro;
3. eseguire l'abbandono della Release 2 in esercizio — **fatto alle 17:00**;
4. correggere i cookie in due modi «insieme» (contesti annidati + registrazione
   di cio' che si e' osservato);
5. «dopo passa alla versione 3»;
6. sul login: «correggi adesso», scegliendo esplicitamente di mettere login e
   cookie nella stessa versione, con un solo fermo dei servizi;
7. committare togliendo le due righe finali di attribuzione — **fatto su tutti
   i quattordici commit**;
8. questa consegna.

## 3. Il difetto del login: causa, correzione, prova

### Causa, misurata sulla pagina viva

Turno reale `ffe2a2d6`. Il vero collegamento di accesso di Telepass e':

```html
<a href="https://www.telepass.com/KTI/dashboard" target="_blank"
   aria-label="aria-label-middle_menu" ...><div><div>Accedi</div></div></a>
```

L'etichetta ARIA e' **una chiave di traduzione mai risolta**. Il nome
accessibile del collegamento era quindi `aria-label-middle_menu`, che non
somiglia a «login»: punteggio **0**. Gli unici nodi che dicevano «Accedi»
erano i due involucri grafici interni, senza `href`, identici fra loro:
punteggio 0,78 entrambi. Il risolutore vedeva due cose uguali senza
destinazione, dichiarava ambiguita' e si fermava. In chat: «non ho trovato un
modulo di login nella pagina», su una pagina che il modulo ce l'aveva.

La riapertura del menu non veniva ritentata perche' per quel bersaglio un
tentativo era gia' stato speso (`reveal_attempts`), ed e' giusto cosi'.

### Correzione (commit `d498cad6`)

Generale, deterministica, nessun nome di sito nel codice:

- `session_broker.py`, JS di enumerazione: ogni candidato porta ora anche
  `text`, il testo visibile, limitato a 160 caratteri come il ripiego
  `cursor:pointer` gia' esistente;
- `action_resolver.py`: nuovo `_candidate_names()` — nome accessibile,
  etichetta e testo visibile sono **tutti** nomi del controllo; entrano nel
  mucchio di confronto e nel confronto esatto. Nessuno nasconde l'altro;
- cio' che si mostra a una persona (richiesta di consenso, riga di registro)
  preferisce un'etichetta leggibile invece della chiave di traduzione.

### Prova

Sulla pagina viva, stessa sequenza della produzione: il collegamento passa da
**0 a 1,0**, ambiguita' sparita, e il clic apre davvero la pagina di accesso
con il campo password presente.

Un dettaglio che il prossimo agente deve conoscere: quel collegamento ha
`target="_blank"`, quindi l'accesso **si apre in una scheda nuova su
`login.telepass.com`**, che e' un host diverso da quello di partenza. Il
broker adotta le finestre nuove, ma per un host non ancora consentito prepara
un consenso di ampliamento (`_prepare_resource_expansion`). E' il confine
voluto, non un guasto: attendersi **un passaggio di consenso in piu'** al
primo accesso.

Prove nuove: `tests/runtime/sites/test_etichetta_sbagliata_non_nasconde_il_testo.py`
(4 deterministiche + 1 con browser vero, che si salta con onesta' se il
browser non c'e'). Confine conservato e provato: nomi diversi con destinazioni
diverse **restano ambigui**, e non li scioglie il risolutore.

## 4. I cookie

Stato: la precondizione semantica osserva anche i contesti annidati
(`MAX_OBSERVED_FRAMES = 8`, `MAX_OBSERVED_PANELS = 3`) e registra una riga di
osservazione con soli conteggi, mai testo.

Verificato oggi su una **copia fedele** del pannello che la produzione ha
mostrato nel turno `ed846e59` («Che biscotti vuoi?»): riconosciuto come
pannello cookie, premuto **«Solo necessari»**, pannello sparito.

Verificato anche il contrario, sul sito vero: quando al posto del banner
compare un riquadro pubblicitario, il codice lo vede, lo classifica «non
cookie» e **non lo tocca**.

Attenzione: sul sito vero, dal mio browser, **il banner dei cookie non
compare** — atteso 24 secondi, mai apparso. In produzione compare. Non ho
capito perche' e non ho indagato oltre; e' il motivo per cui la prova e' su
copia fedele e non sul sito.

Difetto **mio** trovato e corretto stasera (commit `d2346b34`): dopo aver
chiuso il pannello, il giro successivo non vedeva piu' nulla e azzerava i
conteggi, cosi' la registrazione non si scriveva proprio nel caso riuscito.
Ora i conteggi sono il massimo osservato nel flusso. **Questa correzione NON
e' nella Release 3**, che era gia' costruita: tocca solo un conteggio
diagnostico, mai la chiusura del pannello, e il caso fallito era gia'
registrato.

Sempre in `d2346b34`: riparata una prova che la mia modifica ai contesti
annidati aveva rotto e che gira **solo con browser vero**
(`METNOS_SITES_SIM=1`), quindi la suite ordinaria non la mostrava rossa. Da
oggi in poi conviene far girare `tests/runtime/sites` almeno una volta con
quella variabile: **454 verdi, 3 saltate**.

## 5. La Release 3

### Come e' stata costruita

L'esportazione e' la stessa proiezione del repository pubblico:

1. `bash scripts/export-public.sh <DEST>`
2. `python -I -S <DEST>/scripts/check_contract_boundary_policy.py`
3. `python internal/tools/rm0008_public_source_review.py public-fs-pin <DEST>
   <PIN_PUBBLICO> <CONTEGGIO> <PIN_PRIVATO>` — questo passo **riscrive** il
   riferimento dentro l'esportazione: senza, il candidato non e' coerente.
4. copia in un percorso stabile, **modi 644/755 e nessun bytecode**.

Due rifiuti incontrati, entrambi corretti e ora prevenuti dal costruttore:
il codice compilato lasciato dalle mie verifiche dentro le sorgenti, e i
permessi di gruppo del repository (il ricevitore accetta solo 644/755).

### Identita' esatte

| voce | valore |
|---|---|
| sorgente ricevuta | `sha256:5d6448ec8b98b4bac7ae86f03c01dac84978c873ea9a88839a7e767e830332ad` |
| build chiusa | `sha256:0f71b2331bbe31e80fb49370c6779ada49d82b6ca219bfa51e93f2b83b201eeb` |
| testa richiesta | `sha256:d302bb32544f2352ad479ae4e529cdc90809be6cb0f7e7e5aaaef22cf3fabe32` |
| descrittore | `b77128fe5333c86ec7d609c05649c8d7c4c2b1b588108db5044d4554d41404c4` |
| controllo amministrativo | `3669ed5fd0887b35670fb7d4152b0ba2e1a40d094d2868a604f21433a5fef739` |
| prove di build | `/var/lib/metnos-admin/rm0008-release3-evidence-20260909` |
| installata in | `/var/lib/metnos/executor-birth/releases-v1/00000000000000000003` |
| costruita dall'albero al commit | `5b146480` |

### La prova che questa versione risolve il blocco

| | Release 2 (abbandonata) | **Release 3** |
|---|---|---|
| `python_executable` nel descrittore | interprete gestito | **`/usr/bin/python3.12`** |
| interprete nelle unita' amministrative | `/var/lib/metnos/python-envs-v1/…` | **`/usr/bin/python3.12`** |

E' esattamente la clausola che la Release 2 non poteva soddisfare.

## 6. Il passo successivo, con i comandi

`internal/tools/rm0008_complete_release3.py`, due modalita'.

```
sudo /usr/bin/python3.12 /opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_complete_release3.py audit
sudo /usr/bin/python3.12 /opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_complete_release3.py complete
```

`audit` legge e confronta soltanto: unita' dichiarate uguali a prima, nessuna
modificata a mano, macchina ferma, e **rifiuta subito** se l'interprete
amministrativo non e' quello del sistema operativo. Si puo' rilanciare quante
volte si vuole.

`complete` esegue l'attraversamento vero sotto i lock del prodotto e
**ferma e riavvia i servizi**. Non lanciarlo con un turno in corso (§8.6).

Rispetto al tentativo della Release 2 c'e' **un pezzo in meno fuori dal
prodotto**: la riparazione d'emergenza del controllo amministrativo non serve
piu', perche' il controllo vivo e' ora quello firmato installato dalla
Release 2 (`35b3dc13…`), non piu' quello rattoppato a mano. Il vecchio
riconciliatore in `/tmp` **non va riusato**.

Dopo l'attraversamento, in quest'ordine: salute dei quattro servizi, catena di
proprieta', `metnos.target`, HTTP pubblico, **un turno reale** sul dominio
toccato (§8.5) — cioe' proprio l'accesso a Telepass — poi documentazione,
GII e pubblicazione incrementale con note in inglese.

## 7. Prove rosse, classificate con onesta'

Suite runtime completa: **91 rosse su 8652**, tutte preesistenti a questa
sessione (contracts 70, i18n 7, infra 6, tutor 4, executors 3, skills 1).
**Zero** nel dominio siti.

Suite portable: 2504 verdi, 7 rosse, di cui

- **5** chiedono `sudo` senza password per cambiare proprietario a file finti:
  limite d'ambiente, non difetto di prodotto;
- **1** era l'elenco firmato dei file Python, rimasto indietro di 31 file (30
  non miei): **rigenerato**, ora verde;
- **1** resta rossa **di proposito**: il sigillo di
  `tests/portable/conftest.py`. Quel file ha preso due righe con il lavoro
  ereditato (`329d51b3`) che aggiungono `tests/portable` al percorso di
  importazione. Rifissare il sigillo **approva una modifica all'avvio delle
  prove**: e' una decisione da mettere a verbale, non un numero da rinfrescare
  di passaggio. **In attesa di Roberto.**

## 8. Commit di questa sessione (worktree `rm0008-reboot`, ramo `codex/rm0008-reboot`)

Base `8519edb1`. Quattordici commit, **tutti senza le due righe finali di
attribuzione** (riscritti su richiesta esplicita):

```
329d51b3 cookie semantici + confine di avvio dei servizi   (lavoro ereditato)
a6cbe2b5 interprete amministrativo + uscita in avanti
5b83e945 abbandono della release 2 registrato
1ef63b87 .. 15ec2086  sei strumenti di diagnosi dei turni
d498cad6 il testo visibile fa parte del nome di un controllo   <- login
5b146480 riallineamento riferimenti + preparazione build 3
18832452 strumento di attraversamento della release 3
d2346b34 un pannello chiuso resta un pannello visto           <- conteggi
45ac6e5f riallineamento riferimenti dopo la correzione
```

Riferimenti sorgenti correnti nel repository:
privato `sha256:8b69da9a…` (755), pubblico `sha256:7ed2a8d2…` (743).
**Sono avanti rispetto alla Release 3**, che porta la propria copia firmata:
appartengono alla versione successiva.

## 9. Decisioni aperte per Roberto

1. **Attraversare la Release 3** — pronto, serve solo il suo via.
2. **Sigillo di `tests/portable/conftest.py`** — vedi §7.
3. `internal/tools/grant_roberto_access_to_install_root.sh` — scritto,
   approvato in linea di principio, **mai eseguito**. Rimedio provvisorio: la
   vera correzione e' separare la radice d'installazione dall'albero di
   sviluppo, cosa su cui Roberto e' d'accordo.

## 10. Vincoli operativi da non riscoprire

- I comandi `sudo` li lancia Roberto dal suo terminale: il prefisso `!` non
  offre un terminale e `sudo` non puo' chiedere la password.
- Il lettore dei turni e' installato e non chiede password:
  `sudo -n /usr/bin/python3.12 /usr/local/lib/metnos-diagnostics/inspect_sites_turn.py <turno>`.
- `session_broker.py` e i moduli di confine: un edit sgancia i processi vivi
  del sidecar (impronta di contratto). Con la Release 3 il punto e' risolto
  dall'attraversamento.
- Comunicazione con Roberto: parole semplici, esito prima di tutto, niente
  log grezzi; note Git e GitHub in inglese, conversazione in italiano.
- Mai riportare la password di Roberto in file, comandi o registri.
