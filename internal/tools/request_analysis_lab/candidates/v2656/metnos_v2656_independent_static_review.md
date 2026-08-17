# Review infrastrutturale indipendente V26.5.6

Data: 9 agosto 2026.

Verdetto: **STATIC BLOCK**. Il gate autore resta chiuso e questa review non
autorizza preflight, rete, modello o run live.

## Evidenza riprodotta

| Controllo offline | Esito |
|---|---:|
| `/usr/bin/python3 -I -B metnos_v2656_offline_selftest.py` | 21/21 PASS |
| `/usr/bin/python3 -I -B metnos_v2656_k1_runner.py --verify-freeze` | PASS |
| trasporto del self-test | 1 GET + 34 POST finti |
| rete/modello reali | 0/0 |
| audit anti-contaminazione congelato | 15/15 PASS |
| pin del freeze verificati indipendentemente | 20/20 |
| tree jsonschema/regex | 38/38 + 11/11 file |
| inventory moduli non-stdlib | 51/51 |

Un replay indipendente con transport solo in memoria ha prodotto un batch
completo con 1 GET, 34 POST, zero retry, contatori riconciliati, zero campi o
occorrenze query e zero aperture post-batch nel runner. Il batch finto è stato
valutato offline e il risultato ha riportato il suo SHA esatto. Un'eccezione
locale sintetica al caso 6 ha conservato i primi cinque record senza testo
dell'eccezione. I reader bounded/no-follow e il confronto `fstat` prima/dopo
hanno respinto le mutazioni indipendenti.

Il runtime fixture contiene soltanto ordinal, ID opaco, query e SHA; il suo
SHA sorgente e la sequenza dei 34 query hash coincidono, con
`oracle_fields_present=false`. L'audit redatto 109+34+70 resta 15/15, senza
leggere output candidati. Il registro limita esattamente lo scope a 10
relazioni: `spatial.located_at`, `identity.same_as`,
`filesystem.located_at`, `runtime.host_of`, `spatial.near`, `acl.share`,
`communication.send`, `movement.destination`, `workflow.position` e
`document.position`.

## Cause bloccanti

1. **Errore CLI non contenuto.** L'assenza del gate blocca correttamente prima
   di trasporto e output, ma `main()` lascia propagare il `RuntimeError`.
   Nell'invocazione operativa osservata il traceback ha attivato
   `sys.excepthook`/apport, che ha tentato un side effect in `/var/crash`.
   Anche un diniego atteso deve terminare nonzero con diagnostica tecnica
   sanitizzata e senza crash hook.
2. **Preflight esterno non producibile dal bundle revisionato.** La CLI espone
   solo `--verify-freeze` e `--controls`, mentre il gate richiede un artifact
   preflight recente, endpoint/context-bound e con una call prima del lock.
   Non esiste un comando congelato, separatamente autorizzato e no-clobber che
   possa produrlo.
3. **Batch non interamente validato prima del gold.** Una mutazione
   `expanded_frame={}` su un record marcato valido supera
   `_validate_batch_before_gold`; il primo artifact gold
   (`source_controls34`) viene aperto prima che il frame malformato fallisca.
   Inoltre la shape di `result` non è chiusa. Serve chiusura per status e
   validazione full-frame pin-nata prima di ogni identità gold.
4. **Una call non equivale a un solo trasporto.** `urlopen` usa proxy ambiente
   e redirect di default. Un handler interamente finto ha seguito da loopback
   a un secondo URL non-loopback e ha riportato PASS con due open reali ma
   `socket_attempts=1`; un secondo probe ha mostrato la destinazione passare
   dal proxy pur partendo da `127.0.0.1`. Servono `ProxyHandler({})`, redirect
   deny, IP loopback letterale e contatori sul vero open.
5. **Writer runner non ancorato alla directory autorizzata.** Il link finale è
   atomico/no-clobber e usa `fsync`, ma il parent è risolto di nuovo per path
   dopo il batch: non c'è una chain `openat` no-follow né un dirfd stabile.
   Una directory symlink/sostituita può cambiare la destinazione o perdere il
   partial. Riutilizzare il writer dirfd/no-follow già presente
   nell'evaluator.

## Limite del batch prima di `json.dumps`

Il writer non ha un contatore esplicito, ma per questi byte il bound è
dimostrabile e non è un blocker distinto. Le tre istanze massime costruite
ricorsivamente dallo schema e validate hanno 20.288, 74.545 e 2.252 byte
canonici; la maggiore usa al massimo 64 atom, 192 binding e 8 clausole
unsupported. Includendo pretty-print e crescita conservativa dell'adapter, un
frame espanso resta sotto 189.120 byte. Con 32 KiB di envelope per record e
64 KiB top-level, 34 record restano sotto **7.609.728 byte**, contro il limite
evaluator di 33.554.432.

Sul Python congelato il conteggio per occorrenza della shape massima dà
386.636 byte per frame; batch object, due copie testuali/UTF-8 transitorie e
1 MiB di slack restano sotto **31.904.024 byte** di working set aggiuntivo.
Un cap esplicito pre-dump sarebbe comunque una difesa utile per un successore.

Fix minimo del successore: CLI catch sanitizzato; `--preflight` dietro gate
preliminare separato; opener senza proxy/redirect con contatori reali;
validator full-frame pre-gold; writer ancorato a dirfd no-follow. La review
leggibile a macchina è `metnos_v2656_independent_static_review.json`.
