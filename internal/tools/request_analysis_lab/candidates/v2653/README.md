# V26.5.3 — core chiuso e ancorato alla query

Stato: **SOLO OFFLINE, STATIC BLOCK, NON FROZEN**.

V26.5.3 conserva prompt, schema, adapter, registry e semantica del grafo e
chiude path, manifest e grounding pubblico, ma non il confine eseguibile:

- l'unica API dichiarata è `evaluate(original_request, frame)`;
- segmenti, testo e offset sono ricavati internamente dalla query intatta;
- path ed entry manifest non arrivano dal caller, ma `_exec_verified_source`
  accetta ancora source bytes e mapping di injection dal caller;
- un manifest runtime separato contiene esattamente cinque identità;
- il core pinna quel manifest e legge gli stessi byte verificati con path
  repository-relative, `O_NOFOLLOW`, tipo/dimensione/hash e controllo TOCTOU;
- solo adapter e validator hash-pinned possono raggiungere l'unico
  `compile/exec`; registry/schema/prompt restano dati;
- il validator riceve i byte del registry già verificati e non riapre file;
- JSON duplicato, input subclassed e path alias/assoluti falliscono chiusi;
  l'allowlist sorgenti è aggirabile tramite `globals()["__builtins__"]`.

Risultati correnti:

- core self-test: **115/115**;
- pre-import verifier: **49/49**, 43 artefatti durevoli;
- replay compact-native: **106/106**;
- audit anti-contaminazione: **15/15** su 213 query, zero surface/overlap;
- contenuto tecnico del 115: 6 positivi, 16 negativi nativi, 68 mutazioni
  storiche e 25 controlli API/path/source/Unicode/ambiente;
- rete, modello e candidate output letti: zero.

Hash principali:

- core: `83622caab31d7f65e12e2f87823a6aa0f1946d0bd88deb16ab74cd654e574078`;
- core result: `eb1078f1fdfd77cc46c3db1c67f21ae5ed036d0491e360d5c2995c8be1aa021c`;
- runtime manifest: `14c1e2d6a666cef335d5567e550ec55db4af4c0cd46b59f9b8b344cf7e506d50`;
- validator injected: `66760987f5d2335c79114632affa5c04ffecd9ffe5f8e1a7adec7bc50b4b793d`;
- durable manifest: `d58c9ffe6371ffcb5c372d798a92d7297a57074de2c30cfe01e0bd8312982cdf`;
- verifier: `36514a56188a4220f5b9ab27eb3a427ccd10cd0603481950f3bc132b3c8be239`;
- verifier result: `b2d5e1803aa44b188398dfc02afe63ad2f6dd6043d9aafc98a8cd078ca1c43e1`;
- review STATIC BLOCK MD/JSON:
  `089892c97b1734671655a5e55a624f65cc82cd266601379768a899b44d2ac19e` /
  `865e193857edd565d830de89aa473ed5c0b39e5a0f07acd0556c38c0909da6fe`.

Il result del verifier non è incluso nel manifest verificato: includerlo
creerebbe un ciclo perché contiene l'hash del manifest. La futura trust root
esterna deve pin-nare manifest, core, verifier, risultati e review.

La review indipendente ha trovato un blocker causale, mentre gli altri replay
restano verdi: `_exec_verified_source(identity, source, injections)` è ancora
raggiungibile come attributo del modulo e accetta byte/injection forniti dal
caller. Inoltre l'allowlist AST non impedisce di recuperare builtins tramite
`globals()` e subscripting; l'audit hook nega `socket.connect` ma non
`os.exec`. Il report durevole è archiviato; non modificare i byte V26.5.3.

Restano, in ordine:

1. creare V26.5.4 senza sink/helper o attributo che accetti source/injection
   dal caller, e chiudere builtins indiretti/audit events;
2. nuova review; soltanto dopo STATIC PASS, wrapper/freeze/gate esterni;
3. K1/K5, full 109, adversarial 70 e holdout storico.

Nessun wrapper, freeze, gate, rete o inferenza è autorizzato da questa
directory.
