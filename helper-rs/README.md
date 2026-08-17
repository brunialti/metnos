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

**Fatto e provato** — 22 test, compila per Windows:

- `protocol.rs` — vocabolario chiuso, validazione della forma, costruzione
  della riga di comando, corpo canonico della firma. Logica pura: si prova su
  qualunque macchina.
- `journal.rs` — le chiavi gia' consumate, perche' una richiesta catturata non
  si possa rigiocare. Scrive PRIMA di agire: fra un'operazione persa e una
  ripetuta, su un sistema che si modifica, si perde.

**Da costruire**, nell'ordine:

1. il canale locale — named pipe con ACL legata al SID del proprietario, e
   l'autenticazione nelle DUE direzioni (ADR 0210 D2): il nome di una pipe non
   e' un segreto, e chi la crea per primo raccoglie cio' che le si manda;
2. la verifica della firma al momento di agire (D3);
3. il registro proprio, separato da quello del client (D5);
4. il servizio e il suo installatore, con il consenso una volta sola (D4) e la
   rimozione senza la collaborazione di Metnos (D6).

## Come si prova

```bash
cd helper-rs
cargo test                                      # logica pura, ovunque
cargo build --release --target x86_64-pc-windows-gnu
```
