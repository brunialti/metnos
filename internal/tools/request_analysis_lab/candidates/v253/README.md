# V25.3 Phase 1 — frozen diagnostic archive

Questa directory conserva byte per byte il predecessore semantico usato dalla
catena V26.2/V26.3. V25.3 **non è il candidato corrente** e non va rilanciato:
il suo unico run K1 è già archiviato e serve solo come evidenza diagnostica.

V25.3 usa UAX #29 e un prompt privo di dizionari della lingua sorgente, ma
espone campi ridondanti e un validator sovravincolato. Nel run K1 soltanto un
frame finale su 34 era valutabile. Il primo output grezzo, prima del retry,
conteneva però un segnale utile: il gate binario minimo current-location era
34/34, con 9/9 positivi e zero leakage. Questo è un rescore post-hoc e **non
riceve credito di accettazione**.

L'analisi V26.3 ha poi verificato che la tupla completa a sette campi del primo
tentativo era 24/34. Quindi “34/34 binding binario” non significa “34/34
semantic exact”. Le due misure devono restare separate.

## File byte-identici

| File | SHA-256 |
|---|---|
| `metnos_v253_phase1_relation_runner.py` | `c417278c40aca616b07b95834f75043920e3dc2427a729ab245a7c3f9bf74615` |
| `metnos_v253_phase1_relation.prompt.txt` | `a5582f9e275e95d43e913dfb7097066fcd1c20bee9e972d844b27e31a04fe113` |
| `metnos_v253_phase1_relation.schema.json` | `24a3f8c591104da7e344c35938661394e8645a44d905dcb262ae20d953808342` |
| `metnos_v253_phase1_relation.freeze.json` | `758ffae7a6487cdd103712f2ecb532455f4662ee8e60abf9bbd9a218bc95a4bb` |
| `metnos_v253_phase1_relation_controls_frozen.json` | `3cb2e32ef682606280bc0e24a30d054deac2f4c0e46f7d60048e5d5bb27f7b8e` |
| `metnos_v253_phase1_relation_mutations_frozen.json` | `0ece09b8fb8d78c115db7def80d29ec3b34754e1c30cca932197dd29b1c50a3e` |
| `metnos_v253_phase1_relation_controls_k1.json` | `2beb387e83d9dcc18ee412425bb54126fc7e24d09452743277b509ea5c80038e` |
| `metnos_v253_first_attempt_minimal_rescore.json` | `769046ee8aecfb2497a1c1ec708694d2047a22542a8d4c6fa705e9bdc3860759` |
| `metnos_v253_first_attempt_minimal_rescore.py` | `985cbc27898fb3192df54d5875ec1db90f931bc154a121b3601af2e602d1f7d5` |

Gli hash sono quelli degli originali `/tmp`; nessun freeze è stato
modificato. Il README è nuovo e non fa parte del freeze.

## Dipendenze e nomi obbligatori

Il runner importa `/tmp/metnos_v25_live_runner.py` per la configurazione del
tier/endpoint e legge:

- `/tmp/metnos_question_focus_negative_controls_v1.json`;
- `/tmp/metnos_v253_phase1_relation.schema.json`;
- `/tmp/metnos_v253_phase1_relation.prompt.txt`;
- `/tmp/metnos_v253_phase1_relation_controls_frozen.json`;
- `/tmp/metnos_v253_phase1_relation_mutations_frozen.json`;
- `/tmp/metnos_v253_phase1_relation.freeze.json`.

Il controllo sorgente è conservato due directory sopra come
`question_focus_controls_v1.json` con SHA-256
`22dac65689f720e07022ac204ba0fd25feebdf5e8db98a9d89f5bf00c120dcbf`;
va materializzato usando il nome `/tmp` storico indicato sopra.

Per la catena completa leggere `../REPLAY_CHAIN.md` prima di copiare o
eseguire qualsiasi file.
