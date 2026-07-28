# Sottosistema dati-utente Metnos — report, proposta, progettazione

> **22/7/2026 — documento definitivo, eseguibile.** Chiude l'analisi Hermes/Honcho (best-practice) →
> `comparison_hermes_metnos_userdata_22_7.md` (matrice 18 dim, verificata) e `strategy_user_data_hermes_22_7.md`
> (esplorazione). Bersaglio: **eguagliare la capacità-USO di Honcho** (imparare da solo, ragionare su di te,
> disambiguare/proporre) realizzandola **locale, riproducibile, esplorabile**. Fondato su codice verificato
> (`file:line`); ogni pezzo marcato RIUSA / BUILD. Solo design — nulla implementato.

---

## 1. Chiusura del ragionamento (il verdetto)

Honcho è lo standard. Metnos oggi copre lo **store** (~60%: `user_prefs` closed-vocab con provenienza, isolamento
multi-utente) ma è a **zero sull'USO** — le tre capacità che rendono Honcho utile (rispondere a domande aperte su
di te, disambiguare dalla tua storia, proporre «lo vorrebbe?»). Non è wiring: è un **sottosistema greenfield**
dimensionato come un topic. Ma è **fattibile e KISS** perché i mattoni di supporto esistono già (LLM router,
describe-deterministico, cadenza nightly, embeddings, feedback ✓/✗, store-pattern). La scelta di principio —
tenere **osservazioni libere per-utente** oltre al vocabolario chiuso — è accettata: è il prezzo per eguagliare
Honcho, e Metnos lo paga meglio (evidenza citata + riproducibile + cancellabile, dove Honcho è cloud opaco).

## 2. Report — cosa esiste OGGI (verificato)

| Pezzo | Stato | Prova |
|---|---|---|
| `user_prefs(user_id,key,value,source,updated_at)`, vocab CHIUSO, `set/get/list_pref` | ✅ esiste | `users.py:632,658` |
| Applicazione prefs a runtime | ⚠️ **solo prefs `sites`**; `tone/reply_length/lang` NON applicati a ogni risposta | `agent_runtime.py:6168-6178` |
| `source='learned'` (apprendimento) | ❌ mai scritto (sempre `explicit`) | `users.py:669` |
| Corpus interazioni per-utente interrogabile | ❌ turni su disco con `actor`, ma nessuna API di retrieval (solo in-flight) | `turn_events.py:123` |
| Ragionamento LLM su di te | ❌ zero (`consult_profile\|dialectic\|user_model`=0) | grep |
| mnestoma per-utente | ❌ è a livello CATALOGO, no `actor` | `mnestoma.py` |
| LLM riproducibile (temp0+seed) | ✅ riusabile | `describe_entries.py:96` |
| Tier LLM fast/middle/wise/frontier | ✅ | `llm_router` |
| Embeddings BGE-M3 (retrieval semantico) | ✅ | autopath/prefilter |
| Cadenza nightly / learning-loop | ✅ | `maintenance_tasks.py`, ADR 0185 |
| Feedback ✓/✗ (auto-osservazione onesta) | ✅ | chat badges |
| Esplorazione «cosa sai di me?» | ❌ non esiste | — |

## 3. Proposta — due tier d'uso (fedele a Honcho)

- **Tier 1 — FAST, deterministico, NO-LLM** (≈ `honcho_profile` + snapshot): `user_prefs` (closed) + peer-card
  compatta, **iniettata a OGNI turno**. Applica tono/formato/lingua sempre. Gratis, riproducibile, auditabile.
- **Tier 2 — DIALECTIC, LLM locale on-demand** (≈ `honcho_context`): `consult_profile(domanda)` ragiona sulle
  **osservazioni libere per-utente** e risponde a una domanda NL arbitraria su di te, **citando l'evidenza**,
  riproducibile (temp0+seed). Invocato solo quando serve (riferimento ambiguo, decisione «proporre?»).

**Filosofia d'uso (differenza da Honcho, non sconto):** apprende in **silenzio** (nessuna conferma sul tuo
self-model, per tua indicazione); il controllo è **l'esplorazione a posteriori** («cosa sai di me?» → vedi tutto
con provenienza, cancelli in un tap). Il self-model influenza solo COME risponde/disambigua, **mai** autorizza
un'azione (il vaglio §2.11 resta ortogonale e invariato).

## 4. Progettazione

### 4.1 Modello dati (1 tabella nuova + riuso `user_prefs`)
`user_prefs` resta il **tier-1 chiuso** (hot-path). Nuova tabella per il **tier-2 aperto**:
```
user_observations(
  user_id     TEXT NOT NULL,          -- isolamento multi-utente (riusa la convenzione)
  obs_id      TEXT NOT NULL,
  text        TEXT NOT NULL,          -- fatto-stringa LIBERO: «"il report"=budget Atlas», «lavora la mattina»
  kind        TEXT NOT NULL,          -- etichetta MORBIDA per filtro: preference|reference|habit|fact|avoid
  embedding   BLOB,                   -- BGE-M3, per retrieval semantico (tier-2)
  source_turn_id TEXT,                -- PROVENIENZA (da quale turno)
  ts          TEXT NOT NULL,
  confidence  REAL NOT NULL DEFAULT 0.5,
  hits        INTEGER NOT NULL DEFAULT 0,   -- quante volte è servita (rinforzo)
  last_used_ts TEXT,
  superseded_by TEXT,                 -- versionamento onesto (una obs corregge un'altra)
  PRIMARY KEY (user_id, obs_id)
)
```
`kind` è morbido (filtro/leggibilità), **non** un vincolo sul valore — è ciò che permette l'espressività aperta di
Honcho. **Crescita bounded** (come mnestoma): cap per-utente (es. top-200 per `confidence*recency`), decay dei non
usati, `superseded_by` invece di cancellare. API gemella di `user_prefs`: `add_obs/list_obs/get_obs/delete_obs/reinforce_obs`.

### 4.2 I cinque componenti (RIUSA / BUILD)
1. **Peer-card + iniezione per-turno** (Tier 1). Compone un blocco compatto (prefs closed + top-K osservazioni per
   `confidence*recency`), **congelato per-turno**, iniettato nel contesto di planner/describe/final. RIUSA:
   `user_prefs`, describe-det. BUILD: il compositore + il punto d'iniezione (oggi solo on-demand `read_persons`).
2. **Deriver silenzioso** (auto-learn, ≈ `dialecticCadence`). Pass post-turno (o nightly a batch) che legge i turni
   recenti di UN actor e **emette osservazioni** (LLM middle, temp0+seed), dedup vs esistenti (cosine≥soglia →
   `reinforce`, non duplica). Scrive `source='learned'`+`source_turn_id`. RIUSA: cadenza nightly, describe-det,
   embeddings. BUILD: il deriver (prompt + dedup + write). **Nessuna conferma** (self-model del proprietario).
   Il tier-1 chiuso ha anche un **deriver deterministico** (`detection_lexicon`: «rispondi più corto»→`reply_length=breve`) — no-LLM, immediato.
3. **`consult_profile(domanda)`** (Tier 2, ≈ `honcho_context`). Retrieval delle osservazioni rilevanti (BGE-M3
   cosine) → ragionamento LLM (tier scelto = profondità `minimal→max`, temp0+seed) → **risposta + obs_id citati** +
   confidenza. Floor: se evidenza debole → **dichiara incertezza** (§2.11), non indovina. RIUSA: `llm_router`,
   embeddings, describe-det. BUILD: l'helper.
4. **Uso nel motore** (chiude righe 5-7). Disambiguazione: quando un riferimento è ambiguo e non risolto nel
   tier-1, `consult_profile("'il report' → X o Y?")`; proattività: prima di offrire, `consult_profile("lo
   vorrebbe?")`. RIUSA: `get_inputs` (fallback se il dialectic è incerto), `change_intents` (per la proposta
   d'azione). BUILD: i due call-site.
5. **Esplorazione + bidirezionale**. «cosa sai di me?» → `list_prefs`+`list_obs` con provenienza; «dimentica X» →
   `delete_obs`/`delete_pref`. Lato-sé: le osservazioni includono esiti+feedback ✓/✗. RIUSA: feedback ✓/✗, UI
   /admin/users. BUILD: la vista + i due comandi in chat.

### 4.3 Flusso (un turno)
```
turno → [Tier1: peer-card iniettata SEMPRE] → planner/describe
        ↘ se riferimento ambiguo / decisione proattiva → consult_profile() [Tier2, on-demand]
turno chiuso → deriver deterministico (immediato, prefs) + deriver LLM (batch nightly, osservazioni)
qualsiasi momento → «cosa sai di me?» = list_prefs+list_obs (esplora/cancella)
```

### 4.4 Fasi (KISS, valore-prima, ognuna utile e verificabile da sola)
- **P0 — Iniezione prefs per-turno** (piccola, quasi-wiring). Applica `tone/reply_length/lang` a OGNI risposta.
  Chiude il gap più sentito (riga 4/8) senza nulla di nuovo lato dati. *Verifica: turno con `tone=informale,
  reply_length=breve` → output cambia; byte-riproducibile.*
- **P1 — Store osservazioni + esplorazione + deriver deterministico**. Tabella `user_observations`, «cosa sai di
  me?», e il deriver **no-LLM** (`detection_lexicon`) che popola prefs+obs deterministiche. Store ispezionabile e
  cancellabile prima di introdurre qualsiasi LLM. *Verifica: «d'ora in poi più breve» → pref learned immediata;
  «cosa sai di me?» elenca con provenienza; «dimentica» cancella.*
- **P2 — Deriver LLM silenzioso**. Il pass nightly che deriva osservazioni libere dai turni per-actor, dedup+bounded.
  *Verifica: N turni su un dominio → osservazioni coerenti con provenienza; dedup non duplica; cap rispettato.*
- **P3 — `consult_profile` dialettico (parità Honcho)**. Retrieval+ragionamento con evidenza, usato per
  disambiguazione e proattività. *Verifica: turno con riferimento ambiguo risolto dal profilo con obs_id citati;
  evidenza debole → chiede invece di indovinare; stessa domanda → stessa risposta (temp0+seed).*

P0 da solo è già un miglioramento tangibile. P3 è la parità piena con Honcho.

### 4.5 Guardrail KISS (perché non degenera)
- **Bounded**: cap osservazioni per-utente + decay (pattern mnestoma) → niente crescita illimitata.
- **On-demand**: il tier-2 (costo LLM) gira solo quando serve, non a ogni turno; tier locale `middle`.
- **Onesto**: floor di confidenza → incertezza dichiarata, mai allucinazione spacciata per fatto (§2.8).
- **Ortogonale alle azioni**: il self-model cambia stile/disambiguazione, mai autorizza — vaglio §2.11 invariato.
- **Esplorabile/cancellabile**: ogni datum ha provenienza; `list_*` è il modello completo; delete in un tap.
- **Riproducibile**: temp0+seed ovunque si usi l'LLM (tier-1 è già deterministico).

### 4.6 Cosa NON fare (anti-pattern espliciti)
- ❌ prosa free-form scritta dal modello **nel prompt di sistema** senza provenienza (è l'opacità di Hermes) →
  le osservazioni sono righe con `source_turn_id`, non un blob.
- ❌ servizio esterno / API key (è il cloud di Honcho) → tutto su `llm_router` locale.
- ❌ conferme continue sul self-model (attrito, per indicazione utente) → silent-learn + esplorazione.
- ❌ vocabolario chiuso sul tier-2 (ucciderebbe l'espressività = il gap da chiudere) → `kind` morbido, `text` libero.

## 5. Riepilogo build (dimensionamento onesto)
**BUILD (il sottosistema)**: tabella `user_observations`+API; compositore peer-card + iniezione per-turno; deriver
deterministico (P1) e LLM (P2); `consult_profile` (P3); 2 call-site motore (disambigua/proattività); vista «cosa
sai di me?» + 2 comandi chat. **RIUSA**: `user_prefs`, `llm_router`, describe-deterministico, embeddings BGE-M3,
cadenza nightly, feedback ✓/✗, store-pattern, `user_id`, `get_inputs`, `change_intents`. È un **topic**, non un
tweak — ma con un P0 a ritorno immediato e una progressione a rischio crescente controllato.

**Verifica trasversale (§8)**: ogni fase con unit + ≥1 turno reale `/agent/turn` (§8.5); riproducibilità
`consult_profile` con seed fisso; `list_obs`/`list_prefs` come oracolo di ciò che il sistema detiene.

**Fonti**: vedi `comparison_hermes_metnos_userdata_22_7.md` (matrice + fonti Honcho/Hermes).
