# Review statica indipendente V26.5.3

Data: 9 agosto 2026.

Verdetto: **STATIC BLOCK**.

V26.5.3 chiude correttamente path, manifest runtime, grounding pubblico e
registry injection, e riproduce tutti i risultati dichiarati. Rimane però un
blocker causale nello stesso confine eseguibile già contestato in V26.5.2:
`_exec_verified_source` riceve ancora byte Python e injection direttamente dal
caller e li porta a `compile/exec`. L'allowlist AST è inoltre aggirabile tramite
accesso indiretto ai builtins.

Report machine-readable:
`metnos_v2653_independent_static_review.json`, SHA-256
`865e193857edd565d830de89aa473ed5c0b39e5a0f07acd0556c38c0909da6fe`.

## Byte e replay

Gli artifact candidati sono rimasti invariati:

| Artifact | SHA-256 |
|---|---|
| core | `83622caab31d7f65e12e2f87823a6aa0f1946d0bd88deb16ab74cd654e574078` |
| core result | `eb1078f1fdfd77cc46c3db1c67f21ae5ed036d0491e360d5c2995c8be1aa021c` |
| runtime manifest | `14c1e2d6a666cef335d5567e550ec55db4af4c0cd46b59f9b8b344cf7e506d50` |
| injected validator | `66760987f5d2335c79114632affa5c04ffecd9ffe5f8e1a7adec7bc50b4b793d` |
| durable manifest | `d58c9ffe6371ffcb5c372d798a92d7297a57074de2c30cfe01e0bd8312982cdf` |
| verifier | `36514a56188a4220f5b9ab27eb3a427ccd10cd0603481950f3bc132b3c8be239` |
| verifier result | `b2d5e1803aa44b188398dfc02afe63ad2f6dd6043d9aafc98a8cd078ca1c43e1` |

Replay freschi, tutti con `PYTHONDONTWRITEBYTECODE=1 python3 -B`:

| Controllo | Esito | SHA-256 stdout |
|---|---:|---|
| core | 115/115 | `eb1078f1fdfd77cc46c3db1c67f21ae5ed036d0491e360d5c2995c8be1aa021c` |
| verifier | 49/49 su 43 artifact | `b2d5e1803aa44b188398dfc02afe63ad2f6dd6043d9aafc98a8cd078ca1c43e1` |
| mutation suite | 106/106 | `3306d299b18726da1b903643d9c85763c05ec57680259c2ab05ddf0aa8bdb68f` |
| contamination audit | 15/15 su 213 | `50e1c5931fe60bfb1007bf5624b1f836214c43fab98194350b66921549487c92` |

Il probe indipendente temporaneo ha SHA-256
`dc3d0fd6b59e744eff43ae997357ffb6131315b2ab672af1da6def353993072f`;
il suo stdout deterministico ha SHA-256
`5a9fac7a446985bcbb2b6866d59c2ceb42665ae6693bf1ae42b4693dc3b14734`.

## Controlli superati

- Il manifest runtime, pin-nato dal core, contiene esattamente cinque
  identità: adapter, validator, registry, schema e prompt. Dimensioni e hash
  del relativo snapshot coincidono.
- Path assoluti, non-member, `..`, `.`, doppio slash e alias interni sono
  respinti. Su una replica temporanea, un symlink di directory e uno di file
  falliscono con `O_NOFOLLOW`; un cambio tra i due `fstat` attiva il controllo
  TOCTOU.
- Core e verifier respingono chiavi JSON duplicate.
- `__all__ == ("evaluate",)`, `CandidateCore/evaluate_segments` sono assenti.
  `evaluate` respinge original id-only, subclass di stringa/dict, subclass
  annidate e frame ciclici, e deriva internamente testo e offset.
- Il validator corrente riceve registry bytes hash-pinned, rifiuta bytes
  errati, non usa file API e riporta `registry_errors() == []`.
- Il percorso normale resta schema -> adapter -> validator.
- `P04_fanout_multi_action`, `P05_multi_domain` e
  `P08_typed_ambiguity` sono accettati con segmenti reali derivati dalla query.
- Sette probe Unicode/multilingua conservano l'allineamento esatto degli span.
- È presente un unico sink artifact `compile/exec`, alle righe 277-278.
- Zero eventi rete effettivi e zero letture di `.pyc` repository osservati.

## Blocker — executor ancora caller-controlled

Alle righe 267-279 del core:

```python
def _exec_verified_source(identity, source, injections=None):
    ...
    module.__dict__.update(injections)
    code = compile(source, ...)
    exec(code, module.__dict__)
```

Il nome privato e `__all__` non costituiscono un confine: la funzione resta un
attributo del modulo e il suo contratto accetta proprio i valori che la
documentazione dichiara non caller-controlled.

Due riproduzioni offline riescono:

1. `_exec_verified_source("adapter", b"caller_controlled_marker = 2653\n")`
   restituisce un modulo con quel valore: i byte arbitrari sono stati eseguiti.
2. La stessa chiamata con sorgente che legge `injected_marker` e una mapping
   `{"injected_marker": 5312}` esegue e consuma l'injection del caller.

Quindi il blocker V26.5.2 è stato spostato dalla mappa `entries` a un helper
generico source/injection, non eliminato.

## Amplificatori fail-open

Sia `_source_ast_safe` sia `transport_ast_safe` accettano:

```python
globals()["__builtins__"]["open"]
globals()["__builtins__"]["__import__"]
```

Il core esegue quel sorgente e recupera entrambe le capability, nonostante
l'allowlist vieti i corrispondenti nomi diretti. Inoltre l'audit hook respinge
un evento sintetico `socket.connect` ma non `os.exec`. Non è stata effettuata
alcuna operazione esterna reale, tuttavia source arbitrario + builtins indiretti
+ audit incompleto rende il confine non fail-closed.

## Correzione minima V26.5.4

1. Nessuna funzione o attributo di modulo deve accettare source, code object,
   modulo o mapping di injection caller-controlled.
2. Adapter e validator vanno compilati soltanto dentro il percorso snapshot,
   da variabili locali contenenti gli esatti byte pin-nati; rimuovere l'executor
   generico riutilizzabile.
3. Rimuovere la pretesa di sandbox AST oppure negare anche `globals`, `locals`,
   `vars` e accessi indiretti ai builtins, con mutazioni dedicate.
4. Chiudere almeno `os.exec` e le varianti fork/spawn nell'audit policy; il
   futuro wrapper deve comunque negare la rete a livello processo.
5. Aggiungere ai self-test le due riproduzioni causali sopra.

La review non modifica gli artifact candidati e non crea wrapper, freeze o
gate. **STATIC BLOCK** non autorizza rete, modello o run live; il prossimo
passo è esclusivamente V26.5.4 offline e una nuova review indipendente.
