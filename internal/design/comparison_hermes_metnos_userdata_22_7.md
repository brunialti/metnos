# Analisi multidimensionale comparativa — dati utente: Hermes/Honcho vs Metnos (fondata)

> **22/7/2026.** Confronto su **fondamenta verificate**, non progetto su sabbia. Colonna Hermes/Honcho = doc
> ufficiale (source per cella); colonna **Metnos OGGI** = codice verificato (`file:line`) o **MANCANTE**. Nessun
> design qui: prima la fotografia onesta di cosa esiste. Distinzione chiave: **memorizzare** (dove/cosa) vs
> **USARE** (cosa sa fare coi dati) — il valore è nell'USO.
>
> Hermes ha DUE strati: (a) file locali `USER.md`/`MEMORY.md`; (b) Honcho, servizio **cloud** opzionale che
> aggiunge il ragionamento. Li tengo separati perché sono cose diverse.

---

## Matrice comparativa

| # | Dimensione | Hermes (file locali) | Honcho (cloud) | **Metnos OGGI (verificato)** | Gap |
|---|---|---|---|---|---|
| 1 | **Cosa cattura** | Prosa free-form: profilo, stile, cose da evitare | Peer-card (lista di fatti-stringa) + representations (vettore) + observations | Prefs a **vocabolario CHIUSO** (`lang/tone/reply_length/units`+stealth) — `users.py:632` | Metnos: enumerato, non aperto |
| 2 | **Forma storage** | 2 file `.md` in `~/.hermes/memories/` (USER 1375c, MEMORY 2200c) | DB relazionale+vettoriale server-side, keyed `(observer,observed)` | Tabella SQLite `user_prefs(user_id,key,value,source,updated_at)` — `users.py:658` | — |
| 3 | **Acquisizione** | Auto-write su disco quando «apprende» | **Dialectic reasoning async** post-turn (`dialecticCadence`), multi-pass | `set_pref(source='explicit')` — **solo esplicito**; `source='learned'` mai scritto | **Nessun apprendimento automatico** |
| 4 | **USO — applica prefs note (formato/tono)** | ✅ snapshot nel system prompt | ✅ `honcho_profile` (fast, no-LLM) | ⚠️ **PARZIALE**: `get_pref` letto solo per prefs **sites** (`agent_runtime.py:6168`); `tone/reply_length` **non** applicati a ogni risposta | Wired a metà |
| 5 | **USO — domanda NL arbitraria su di te (dialectic)** | ❌ | ✅ **`honcho_context`**: rispondi a «preferisce X?» ragionando sulla memoria (`minimal→max`) | ❌ **MANCANTE** (`consult_profile\|dialectic\|user_model` = 0) | **Assente del tutto** |
| 6 | **USO — disambigua riferimento aperto dalla storia** | debole | ✅ via dialectic sulle observations | ❌ disambiguazione è deterministica (`detection_lexicon`, `get_inputs`) ma **non** dal modello-utente | Assente |
| 7 | **USO — proposta proattiva («lo vorrebbe?»)** | debole | ✅ dalla representation | ⚠️ `learning-loop W1` propone, ma a livello **catalogo/piano** (`change_intents`), non «questo utente» | Diverso bersaglio |
| 8 | **USO — iniezione a prompt-time** | ✅ snapshot congelato all'avvio (`§` delims) | ✅ auto-inject `hybrid` (2 peer) | ⚠️ `read_persons` inietta il profilo **solo su query «chi sono io»**, non ogni turno | Non-continuo |
| 9 | **Determinismo/riproducibilità** | n/a (prosa) | ❌ sintesi LLM non riproducibile | ✅ closed-vocab deterministico; e **describe deterministico** (temp=0+seed) riusabile — `describe_entries.py:96` | Vantaggio Metnos |
| 10 | **Auditabilità/provenienza/esplorabilità** | media (leggi il file) | ❌ opaco server-side, «indovina» | ✅ ogni datum = riga closed-vocab con `source/updated_at`; `list_prefs` = tutto | Vantaggio Metnos |
| 11 | **Locality** | ✅ locale | ❌ **cloud** (`honcho.dev`, API key) | ✅ self-hosted §10.3 | Vantaggio Metnos |
| 12 | **Bidirezionalità (peer utente + peer agente)** | no | ✅ osserva sé e l'utente | ⚠️ parziale: `mnestoma` (self, ma **catalogo non per-utente**) + badge feedback ✓/✗ | Diverso |
| 13 | **Isolamento multi-utente** | 1 utente (file singolo) | ✅ peer pairs | ✅ `user_id` PK + device→users.id | Vantaggio Metnos |
| 14 | **Consenso** | auto-write (approval opt-in) | silenzioso | oggi solo esplicito (nessun apprendimento da gate-are) | — |
| 15 | **Onestà sul fallimento** | può scrivere prosa sbagliata su di te | può **allucinare** su di te (sintesi) | §2.8/§2.11: dichiara incertezza, chiede — **se** implementato | Potenziale vantaggio |
| 16 | **Latenza/costo dell'uso** | lookup gratis | profile gratis; **dialectic = chiamata LLM** | tier-1 gratis; tier-2 non esiste (sarebbe LLM) | — |
| 17 | **Ricchezza/espressività** | alta (prosa arbitraria) | **alta** (vettore + ragionamento aperto) | **bassa** (vocab chiuso enumerato) | Gap strutturale |
| 18 | **Profondità ragionamento scalabile** | no | ✅ `minimal→max`, dynamic | (avrebbe i tier `fast/middle/wise/frontier`) — non usati per questo | Latente |

---

## Verdetto sulla fondazione (cosa è solido, cosa è sabbia)

### Su TERRA SOLIDA (Metnos ce l'ha, verificato)
- Store prefs a **vocabolario chiuso** con `source`/`updated_at` (`users.py:632,658`) — l'ossatura del «peer-card».
- **Isolamento multi-utente** (`user_id`, device→users.id).
- **Riproducibilità LLM** già risolta (describe deterministico temp=0+seed, `describe_entries.py:96`) — riusabile.
- Rilevazione **deterministica** NL→canonico (`detection_lexicon`) — per il tier-1 di apprendimento prefs.
- Pipeline **propose-then-approve** (`change_intents`/`user_feedback`) — se si vorrà gate-are qualcosa.
- Segnale **feedback ✓/✗** esplicito (≈ auto-osservazione, ma onesto).

### SABBIA (mie affermazioni precedenti da correggere — NON esistono come credevo)
- ❌ **«mnestoma per-utente»**: FALSO. mnestoma è **catalogo** (co-attivazioni tool), nessun `actor`. Non è il corpus-utente.
- ❌ **«corpus turni interrogabile a runtime»**: i turni sono su disco con `actor`, ma **non c'è API** di recupero
  per-utente-per-ragionamento (`turn_events` espone solo gli in-flight). Va **costruito** (indice/retrieval).
- ❌ **«dialectic = wiring»**: FALSO. Ragionamento su-di-te = **zero** oggi. È **greenfield**, non wiring.
- ❌ **«~90% wiring»**: sovrastimato. Store-side ~60% c'è; **USO-side (il valore) è in gran parte da costruire.**

### Il divario che conta (USO, non storage)
Sulle righe 5-6-7 (le tre capacità-USO che rendono Honcho «best-practice»: rispondere a domande aperte su di te,
disambiguare dalla storia, proporre «lo vorrebbe?») Metnos oggi è a **zero**. Non è un problema di *dove* mette i
dati (lì Metnos è pari o meglio: locale, auditabile, multi-utente), ma di *cosa sa farci*: manca (a) un **corpus di
osservazioni per-utente** con retrieval, (b) un **helper di ragionamento** che lo interroghi. Sono i due pezzi
mancanti, ed erano proprio quelli che avevo dato per esistenti.

### Trade-off strutturale (riga 17)
Honcho è **espressivo-aperto** (vettore + prosa ragionata) → cattura sfumature non enumerabili, al prezzo di
opacità/cloud/allucinazione. Metnos è **chiuso-verificabile** → auditabile e locale, al prezzo di non catturare
l'arbitrario. **Non sono lo stesso oggetto con due storage diversi: sono due filosofie.** Qualsiasi «pareggiare
Honcho sull'uso» impone di introdurre un grado di apertura (un corpus + un LLM che ci ragiona) — decisione di
principio, non di implementazione.

---

## Implicazioni (da DECIDERE prima di progettare — non qui)
1. **Vuoi la capacità-USO aperta di Honcho (righe 5-7)?** Se sì, serve accettare un corpus per-utente + un LLM che
   lo interroga (con evidenza+riproducibilità, per restare Metnos). Se no, ci si ferma al tier-1 chiuso (e si chiude
   solo il gap riga 4/8: applicare le prefs a ogni turno + apprenderle).
2. **Costo:** il tier-2 è greenfield (corpus+retrieval+helper), non «poche righe». Va dimensionato onestamente.
3. **Nessun design è dato per solido finché (1) non è deciso.** Questo doc è la fondazione; il progetto viene dopo.

---

## Architettura-bersaglio: Honcho portato in Metnos (Hercules = lo standard, non un'opzione)

**Regola di questa sezione**: Honcho definisce la capacità da EGUAGLIARE. Non si giustifica alcun gap con «Metnos
è chiuso/locale». Locale, riproducibile, verificabile, esplorabile = **come** Metnos lo realizza, non scuse per
fare meno. La concessione onesta: per catturare ciò che Honcho cattura serve **superare il vocabolario chiuso** e
tenere **osservazioni libere per-utente** — si accetta.

Cinque componenti, ciascuna fedele a un pezzo di Honcho, con reuse-vs-**BUILD** dichiarato:

| # | Componente (fedele a Honcho) | Cosa fa | Metnos: riusa | Metnos: **BUILD** |
|---|---|---|---|---|
| 1 | **Observation store per-utente** (≈ collections/observations) | Accumula **fatti-stringa LIBERI** derivati dai turni (non enum): «lavora spesso su Progetto Atlas», «preferisce risposte brevi», «"il report" = budget Atlas». Con provenienza (turn_id, ts, confidenza). | store SQLite pattern, `user_id` isolamento | **la tabella osservazioni free-form + retrieval** (greenfield) |
| 2 | **Auto-learn silenzioso post-turno** (≈ `dialecticCadence`) | Un pass deriva osservazioni **senza conferme**, a cadenza. | cadenza nightly/learning-loop (infra c'è); describe-det (temp0+seed) | **il deriver LLM** che scrive le osservazioni |
| 3 | **Peer-card + iniezione a OGNI turno** (≈ `honcho_profile` + snapshot congelato) | Blocco compatto (prefs closed-vocab hot-path + top-N osservazioni) iniettato **ogni turno**, riproducibile. | `user_prefs`; describe-det | **l'iniezione per-turno** (oggi solo on-demand su «chi sono io») |
| 4 | **Dialectic query `consult_profile(domanda)`** (≈ `honcho_context`) | Il **middle LLM locale** ragiona sullo store e risponde a una **domanda NL arbitraria** su di te — con **evidenza citata** + riproducibile. | `llm_router` (fast→wise→frontier = la profondità `minimal→max`) | **l'helper dialettico** (greenfield) |
| 5 | **Bidirezionale** (≈ user peer + AI peer) | Osserva TE (dai tuoi turni) **e** osserva SÉ (esiti + feedback ✓/✗). | segnale feedback ✓/✗ (c'è) | **il lato osservazione-utente** |

**Uso (le righe 5-7 chiuse):** disambiguazione = `consult_profile("'il report' è X o Y?")` risolve dai fatti;
proposta = `consult_profile("Roberto vorrebbe schedularlo?")` prima di offrire. Iniezione continua = ogni turno
Metnos «sa già» chi sei.

**Adattamento Metnos (requisiti, NON sconti — anzi supera Honcho su questi):** gira sul **tier LLM locale**
(niente honcho.dev); ogni risposta dialettica **cita l'evidenza** e è **riproducibile** (temp0+seed) dove Honcho è
opaco; **esplorabile/cancellabile** («cosa sai di me?»); **per-utente isolato**.

**Sizing onesto (niente sabbia):** il grosso del VALORE — store osservazioni free-form (1), deriver (2), iniezione
per-turno (3), helper dialettico (4), lato-utente bidirezionale (5) — è **BUILD greenfield**. È un **sottosistema**,
non un tweak. Riuso reale: store-pattern, `llm_router`, describe-deterministico, cadenza nightly, feedback ✓/✗,
`user_id`. Chi lo realizza deve dimensionarlo come un topic, non come «poche righe di wiring».

**Fonti**: [Hermes memory.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory.md) ·
[Hermes honcho.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/honcho.md) ·
[Honcho Peer Card](https://honcho.dev/docs/v3/documentation/features/advanced/peer-card) ·
[Honcho×Hermes integration](https://honcho.dev/docs/v3/guides/integrations/hermes).
Metnos: `runtime/users.py`, `runtime/agent_runtime.py:6168`, `runtime/mnestoma.py`, `runtime/turn_events.py`, `runtime/describe_entries.py`.
