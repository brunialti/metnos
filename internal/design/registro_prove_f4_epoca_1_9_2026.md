# RM-0008 F4-EPOCA-01 — registro delle 24 prove richieste (§11): la meta' di B

Data: 1 settembre 2026 · Agente B · **parziale per costruzione**

Il §11 della specifica elenca ventiquattro prove richieste prima di B2. Alla
richiesta di chiusura non esisteva un documento che dicesse quali fossero
soddisfatte e da cosa. Questo e' **la meta' che posso compilare io**: per ogni
voce, l'artefatto del mio perimetro che la copre, oppure il fatto misurato che
dice che non e' coperta.

**Non compilo le righe di A.** Dove scrivo «non mia» significa soltanto che non
ho l'evidenza in mano, non che manchi: la riga la chiude lui.

Regola che mi sono dato: rivendico una voce solo se posso indicare un file che
gira e che diventa rosso se la proprieta' cade. Le sonde sono state tutte
provate rompendo il prodotto apposta.

| § | prova richiesta | copertura B | stato |
|---|---|---|---|
| 1 | prima transizione dall'ancora V1 | `sonda_convergenza_confine`, scena «prima uscita, certificato all'ancora» | parziale: copre l'attraversamento, non l'intera transizione |
| 2 | seconda transizione dalla testa V1 | `sonda_convergenza_confine`, scena «uscita seguente, certificato in catena» | parziale, come sopra |
| 3 | zero, una e molte generazioni correnti | — | **non mia** |
| 4 | i 12 legami storici O15 byte per byte immutati | — | **non mia** |
| 5 | zero ricevute produce una riattestazione per ciascuna generazione | `prova_accettazione_v2` caso 1 | parziale: una generazione, non molte |
| 6 | interruzione dopo ogni frontiera durevole | `sonda_convergenza_confine` | **coperta**: 19 giunture attraversate in 5 scene; la sonda dichiara anche le 12 che le sue scene non raggiungono e chi le copre |
| 7 | ripetizione identica dopo ogni interruzione | `sonda_convergenza_confine` (riprende e confronta l'albero file per file) + `prova_accettazione_v2` caso 3 | **coperta** per le 19 |
| 8 | due richieste concorrenti uguali e due diverse | — | **non mia** |
| 9 | collisione di nome con byte uguali e diversi | `sonda_conservazione_unita` casi 4-6, `sonda_pendente_estraneo_v2` | **coperta** per la conservazione e per i pendenti |
| 10 | generazione scomparsa, aggiunta o cambiata dopo il censimento | — | **non mia** |
| 11 | distribuzione diversa da quella legata alla testa | — | **non mia** |
| 12 | vecchia epoca e vecchie ricevute leggibili e immutate | `prova_ricevute_v2` casi 6, 7, 7-bis | **coperta** |
| 13 | un solo selettore, nessuna combinazione fra testa F4 e contesto diverso | — | **non mia** |
| 14 | diniego di una build precedente dopo il punto di non ritorno | `test_closed_build_denies_signing_before_path_or_key_access` e le altre citate da A; misurato oggi anche il bit acceso | **coperta**: rimettendo il bit a falso una prova diventa rossa — misurato |
| 15 | ritorno mediante nuova release con sequenza superiore | — | **non mia** |
| 16 | certificato V1 emendato completo | — | **non mia** |
| 17 | interruzione del provisioning prima e dopo `PREPARED` | `sonda_pendente_estraneo_v2`, `sonda_finestra_transazione_v2` | parziale |
| 18 | richiesta Producer V1 o di altra epoca non riutilizzabile in V2 | `prova_producer_registrazione_v2` casi 6, 6-bis | **coperta** |
| 19 | ricevuta dominante assente, diversa o senza `context_transition_id` | — | **non mia** |
| 20 | digest dominante che omette o cambia il piano di ritiro | — | **non mia** |
| 21 | V1 storica e V2 corrente coesistono come triple distinte | `prova_ricevute_v2` (nessun ripiego da V2 a V1) | **coperta** |
| 22 | inventario, oggetto non posseduto, alias o file non regolare impediscono il congelamento | `sonda_conservazione_unita` caso 6 (collegamento), `sonda_ruoli_epoca_v2` | parziale: copre il collegamento e i ruoli, non l'oggetto non posseduto |
| 23 | recupero del contenitore incompleto sul negozio | — | **NON COPERTA, misurato**: `4e2feabf…` e' ancora fra i 123 contenitori del negozio vero, in attesa della decisione operativa |
| 24 | server completo e turni reali in copia dopo la transizione | — | **NON COPERTA**: dichiarato da me alla consegna di `prova_accettazione_v2`, e da allora nessuno ha fatto girare un server vero |

## Come si legge

- **coperta** (6): esiste un artefatto che gira, e diventa rosso se la proprieta'
  cade. Provato rompendo il prodotto.
- **parziale** (6): l'artefatto copre una parte nominata della voce.
- **non mia** (10): l'evidenza sta nel perimetro di A.
- **non coperta** (2): misurato che non lo e'.

## Le due che non dipendono da nessuno dei due

La 23 aspetta una decisione operativa sull'uso della primitiva di recupero sul
negozio vero. La 24 aspetta che la sequenza del coordinatore, ora scritta,
venga eseguita su una copia con un server e turni veri.

Nessuna delle due si chiude scrivendo altro codice.

## Correzione, 1 settembre

La prima stesura diceva che la voce 14 era «misurata, non inchiodata»: che il
bit fosse acceso ma che nessuna prova sarebbe diventata rossa se tornasse
spento. **Falso.** Il registro di A cita quattro prove; le ho eseguite
rimettendo il letterale a `False` e una diventa rossa. La 14 e' coperta.

Lascio la correzione scritta invece di riscrivere la riga in silenzio, perche'
l'errore era della stessa specie che questo registro serve a evitare: una
riga compilata su quello che avevo guardato io, senza cercare l'evidenza
dell'altra meta'.
