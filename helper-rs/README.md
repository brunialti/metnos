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
| binario Windows | megabyte | **238 KB** |
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

## Stato (17 agosto 2026)

**Fatto e provato** — 56 test, compila per Windows:

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

**Da costruire**:

1. l'installatore del servizio, col consenso una volta sola (D4);
2. la rimozione dalla lista dei programmi installati, senza la collaborazione
   di Metnos (D6);
3. il lato client: aprire la pipe verificando di parlare con l'aiutante VERO
   prima di scrivere (l'altra meta' di D2), e firmare le richieste.

## Come si prova

```bash
cd helper-rs
cargo test                                      # logica pura, ovunque
cargo build --release --target x86_64-pc-windows-gnu
```
