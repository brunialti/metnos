# metnos-helper — l'aiutante elevato su Windows

Il software piu' privilegiato che Metnos installa su una macchina altrui.
Decisioni in **ADR 0210**; qui c'e' solo cosa e' costruito e cosa manca.

## Perche' e' un progetto separato

Non e' organizzazione del codice: e' la scelta di sicurezza principale.
Tutto cio' che un componente privilegiato linka diventa superficie con i
privilegi di sistema.

|  | client | aiutante |
|---|---|---|
| librerie collegate | 210 | **52** |
| binario Windows | megabyte | **516 KB** |
| parla con la rete | si' | **no** |
| esegue codice ricevuto | si' (executor firmati) | **no** |

L'aiutante non dipende da un client HTTP, da un runtime asincrono, da un
estrattore di archivi o dal caricatore di executor.

## Che cosa sa fare

Tre operazioni tipizzate, e nessuna significa «esegui»:

| operazione | effetto |
|---|---|
| `query` | questo pacchetto e' installato, e in che versione |
| `install` | lo installa per tutti gli utenti |
| `uninstall` | lo rimuove |

La riga di comando la costruisce l'aiutante, dai soli valori validati. Non
esiste un campo «argomenti liberi», e una richiesta che ne porta uno viene
accettata ignorandolo (c'e' un test che lo prova).

## Stato (18 agosto 2026)

**Fatto e provato** — 88 test qui, 71 nel client, compila per Windows:

- `protocol.rs` — vocabolario chiuso, validazione della forma, costruzione
  della riga di comando, corpo canonico della firma. Logica pura: si prova su
  qualunque macchina.
- `journal.rs` — le chiavi gia' consumate, perche' una richiesta catturata non
  si possa rigiocare. Scrive PRIMA di agire: fra un'operazione persa e una
  ripetuta, su un sistema che si modifica, si perde.

- `channel.rs` + `win_pipe.rs` — il canale locale. Il nome porta il SID del
  proprietario, validato prima di entrarci. La pipe si apre al SOLO SID (non a
  un gruppo, che si puo' allargare), rifiuta i chiamanti remoti e usa
  `FIRST_PIPE_INSTANCE`: chi arriva secondo fallisce invece di affiancarsi in
  silenzio. Chi chiama si guarda chiedendo al sistema operativo.
- `pairing.rs` — l'appaiamento e l'autorizzazione. Due controlli distinti che
  servono a cose diverse: il SID dice CHI ha aperto la pipe, la firma dice DA
  DOVE viene la richiesta. Il primo senza il secondo lascerebbe passare
  qualunque cosa scritta dal processo giusto; il secondo senza il primo una
  richiesta firmata riprodotta da chiunque.
- `audit.rs` — il registro proprio, separato da quello del client: un registro
  che il servito puo' riscrivere non e' una prova. Si registrano anche i
  RIFIUTI, perche' un rifiuto senza traccia e' indistinguibile da un attacco
  che nessuno ha notato.
- `service.rs` — il ciclo: leggi l'appaiamento, autorizza, consuma la chiave
  PRIMA di agire, esegui, registra. La sequenza sta in un posto solo, cosi'
  non esiste un secondo percorso che salti un controllo.

- `setup.rs` — installarsi e togliersi. Il consenso si chiede UNA volta e il
  testo dice le quattro cose che servono a decidere: che cosa si concede, a
  chi, per quanto, e come si toglie. L'aiutante compare fra i programmi
  installati con il suo modo di disinstallarsi: se servisse Metnos per
  toglierlo, il proprietario dipenderebbe da noi per riprendersi un privilegio
  che ha concesso lui.

## Come si installera'

Un comando, una volta sola:

```
metnos-helper.exe install --owner-sid <SID> --public-key <chiave>
```

Windows mostra la richiesta di amministratore quando parte; da quel momento
l'aiutante resta e le installazioni successive non chiedono piu' niente.

Per toglierlo: **Impostazioni > App**, come qualunque altro programma.

Due cose che NON succedono, per scelta: l'aiutante non si installa insieme al
client ne' durante un'installazione di pacchetto (sarebbe far entrare il
componente piu' privilegiato come effetto collaterale), e un aiutante gia'
appaiato non cambia proprietario rilanciando l'installatore — si disinstalla e
si reinstalla, cosi' il passaggio e' un atto esplicito.

- `cli.rs` — tre verbi e nient'altro. Un'opzione sconosciuta e' un ERRORE, non
  qualcosa da ignorare: ignorarla vorrebbe dire che un comando scritto male fa
  una cosa diversa da quella che sembra. Niente abbreviazioni, niente prefissi:
  «unin» non e' «uninstall».
- `win_setup.rs` — l'aggancio a Windows. Copia, registra il servizio, scrive le
  voci fra i programmi installati. Se quest'ultima fallisce l'installazione si
  ANNULLA: un componente privilegiato presente e invisibile e' peggio di uno
  assente, perche' il proprietario non saprebbe che c'e' ne' come toglierlo.

- `win_serve.rs` — il ciclo del servizio. Volutamente noioso: una richiesta per
  connessione, nessuna coda, nessuna concorrenza. Due richieste che si
  sovrappongono su un componente che modifica il sistema sono due modi di
  lasciarlo a meta', e il guadagno sarebbe nullo perche' un'installazione dura
  secondi. Il ciclo non decide niente: legge, passa a `service::handle`,
  risponde.
- `frame.rs` — dove finisce un messaggio. Il canale e' bidirezionale e nessuno
  dei due capi lo chiude: leggere fino a fine-flusso vorrebbe dire aspettare
  l'altro che sta aspettando te. Il file e' BYTE-IDENTICO nei due progetti, e
  il test di contratto confronta le due copie e anche il MECCANISMO — romperle
  entrambe allo stesso modo passerebbe il solo confronto.

### Il lato client

Vive nell'altro progetto, perche' e' il client a parlare, non l'aiutante ad
essere parlato. Due file, e la divisione e' la stessa che c'e' qui fra i
moduli puri e quelli `win_*`:

- `client-rs/src/helper_client.rs` — il GIUDIZIO, e l'indirizzo. Logica pura:
  si prova su qualunque macchina, ed e' la parte che deve essere giusta.
- `client-rs/src/helper_win.rs` — la RACCOLTA. Chiede al sistema operativo chi
  c'e' dall'altro capo (id del processo, SID del token, percorso
  dell'eseguibile) e non decide niente.

Prima di scrivere QUALUNQUE COSA verifica tre fatti, tutti chiesti al sistema
operativo: dall'altro capo c'e' un processo che gira come `LocalSystem`, il suo
eseguibile e' quello installato sotto Program Files, e la pipe e' locale. Se
uno solo non regge non si scrive niente.

Il motivo e' la meta' che si dimentica dell'autenticazione a due direzioni: il
nome di una pipe non e' un segreto, e chi la crea PRIMA tiene il nome. Il danno
non sarebbe l'esecuzione — un impostore senza privilegi non installa niente —
sarebbe la RACCOLTA: richieste firmate valide, da rigiocare altrove.

Il formato su cui si calcola la firma e' scritto due volte, in due linguaggi
che non si parlano: e' il prezzo della separazione, e non e' evitabile senza
far linkare all'aiutante del codice del client. Il vincolo lo presidia
`tests/runtime/remote/test_helper_wire_contract.py`, l'unico posto che vede
entrambi i progetti.

### L'aggancio a `install_packages`

Il comando `metnos-client helper {check,query,install,uninstall}` e' l'unica
porta. L'executor non apre la pipe per conto suo, pur potendo: rifare il
giudizio in Python sarebbe un SECONDO esemplare di un controllo di sicurezza,
e il secondo esemplare e' quello che diverge.

Cosi' l'executor:

1. chiede `check` prima di comporre la scheda di conferma. E' una domanda che
   non manda niente all'aiutante: apre il canale, guarda chi c'e', richiude.
   Dove l'aiutante non risponde, «Per tutti gli utenti» non compare fra i
   bottoni, e la scheda spiega come aggiungerlo;
2. manda `install` all'aiutante quando la persona ha scelto quella portata.
   La portata «solo per me» resta a `winget` lanciato senza privilegi: cio'
   che il processo puo' fare da solo continua a farlo da solo;
3. sulla rimozione ritenta con l'aiutante SOLO dopo un fallimento vero del
   tentativo senza privilegi — non si indovina dove viva il pacchetto.

«Non risponde» e «ha detto di no» restano due esiti distinti nel risultato
(`helper_unreachable` e' una capacita' assente; un rifiuto porta il codice che
l'aiutante ha usato).

**Da provare dal vivo**: nessuno ha ancora installato l'aiutante su una
macchina vera. Finche' non succede, la catena e' verde solo nei test.

## Come si prova

```bash
cd helper-rs
cargo test                                      # logica pura, ovunque
cargo build --release --target x86_64-pc-windows-gnu
```
