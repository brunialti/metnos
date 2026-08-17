# V26.4.1 — runner-only infrastructure freeze

## Analisi post-run degli ID di clausola

- `metnos_v2641_clause_id_canonicalization_probe.py` dimostra offline che
  etichette arbitrarie delle clausole possono essere sostituite da ID
  consecutivi nell'ordine della sorgente senza cambiare altro nel frame;
- `metnos_v2641_clause_id_canonicalization_probe_result.json`: 8/8, inclusi
  grafi multi-dominio, dipendenze, ambiguità e richieste miste;
- collisioni, dipendenze orfane e span incoerenti restano fail-closed;
- è solo una prova strutturale sintetica: il K1 redatto non conserva i frame
  decodificati e non permette di attribuire accuratezza live post-hoc.

## Analisi post-run di atomi ed edge

- `metnos_v2641_atom_edge_derivation_probe.py` e il risultato omonimo provano
  11/11 round-trip e controlli fail-closed;
- `atom_id` deriva dalla posizione nell'array topologico;
- `output_index` deriva dalla firma tecnica e dall'unico output dello source;
- un solo puntatore al precedente atomo produttore deve restare: due grafi
  entrambi validi possono differire semanticamente soltanto per quel legame;
- la riduzione JSON misurata sui grafi sintetici è solo 2,56%: il vantaggio
  principale è evitare inconsistenze, non una grande riduzione di latenza.

Stato: **INDEPENDENT STATIC PASS / RETE E LIVE BLOCCATI**.

Questo bundle non autorizza preflight, inferenza, cutover o retry di K1. Non è
stata effettuata alcuna chiamata server/modello durante la sua costruzione.
La review indipendente runner-only è PASS. Prima di qualunque rete servono
ancora un verifier/gate preflight separato e, dopo un preflight PASS, un nuovo
live gate che ne leghi hash e recenza.

## Scopo

V26.4.1 conserva integralmente la semantica congelata V26.4 e corregge soltanto
il runner dopo il K1 non valutabile. La causa forte del K1 fallito è
infrastrutturale: `URLError -> PermissionError(errno=1)`, richiesta non arrivata
al server, prima latenza 9.78 ms e successive circa 0.035 ms. Non è evidenza di
un errore del modello.

Il nuovo runner:

- conserva tipo/catena/errno delle eccezioni, HTTP status e diagnostica body
  limitata, redatta e hashata;
- distingue socket, HTTP, 2xx accettati, documento JSON, chat envelope, frame
  decodificato, caso valutato valido/invalido;
- interrompe il batch al primo errore socket/HTTP/protocollo, senza creare 33
  record sintetici e senza pubblicare `model_calls`;
- considera content JSON o frame semanticamente invalido un vero output modello
  `evaluated_invalid` e continua i 34 casi;
- pubblica l'output con primitive atomiche/no-clobber;
- offre un preflight standalone rigorosamente lockato: un solo `GET /v1/models`,
  nessun body, zero inferenza, zero retry;
- richiede nel live gate l'hash di un preflight PASS più recente di 15 minuti e
  ripete un GET inline nello stesso processo, massimo 5 secondi prima del primo
  POST. Se il GET inline fallisce, vengono eseguiti zero POST.

## Immutabilità semantica

I quattro artefatti V26.4 sono byte-identici:

- registry: `448c058d805e141c871580253e815849b24a9999d98c505cab970fcd55d1e46f`;
- schema: `06452e75f0a1ec7a42c9bf87dfae3da4e1528fc07b02d9276272a1e9ff0c7fec`;
- prompt: `2e60f19500b6d6193d53f483707533ca39ec1fee6a0256b18e6a96994f53406f`;
- fixture: `1dfbc5d2a7a83308a0f414d1b2c0b395f096d034471930fbaa886627aa79d954`.

Restano identici anche i source-bundle congelati di segmentatore, generatore
schema/prompt, validator, classifier, evaluator, request body e registry
canonico. Il vecchio `evaluate_records` resta byte-identico; un wrapper esterno
impone completezza e rimuove il suo ambiguo campo sintetico `model_calls` prima
della pubblicazione.

## Verifiche offline

- core V26.4.1: 69/69;
- probe graph V26.4 ereditato: 125/125;
- probe infrastrutturale V26.4.1: 72/72;
- oracle representability/gates: 34/34;
- contamination audit: zero overlap su 109 + 34 + 70 casi;
- network/model calls durante freeze e probe: 0.

Hash principali:

- runner: `6f215b04e6dd543b6de5193199957cb653599164f107177f7465fbef7dc95459`;
- infra probe: `9db8cb869160bc89a8655d3d2e1a003532a0d9bb605cf691b26f72ae8797ca0e`;
- infra result: `f15fd3788f0763ebae05840be72f898de5ae41ad6725922b054c19f96b20d7fb`;
- mutations: `8c050ed42142b845089ab0fa35346189672088d31d3e71422971bd1076d74229`;
- contamination audit: `4d56e736c666dae096cfebdf5618daf4125c538c2c11cf216531fb2c196b3349`;
- author pre-gate: `b7a6a219d57b1fd866bd8407d3178d093ac944c5509c118461d565c50290007a`;
- freeze: `6f98647d6a9ea3b46ec521dca50e9f5bb00352d9b9360047219bd81976cd6eff`.
- independent infra review:
  `598dac69fe53e5b1dd1884b400a6d48de35f586db1b4a6394b49a9392c195bfa`.

## Catena obbligatoria successiva

1. **Completato:** review indipendente runner-only sui byte congelati.
2. Verifier/gate separato per un solo preflight GET, con output monouso.
3. Solo dopo PASS: nuovo live gate V26.4.1 che lega freeze, review, preflight
   result/hash/recenza e output K1 assente.
4. Il runner esegue comunque il GET inline nello stesso processo; solo dopo
   PASS può iniziare i POST.

Nessuno di questi gate è incluso o implicitamente autorizzato da questo README.
