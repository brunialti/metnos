# ADR 0178 — Apprendimento bottom-up / top-down attorno al motore-gap (VISIONE)

- **Stato**: **VISIONE / aspirazionale.** NON un impegno né un piano a breve. Precondizione esplicita (sotto): stabilità + onestà §2.8 delle funzioni base. Da costruire SOLO quando la base regge.
- **Data**: 2026-06-23.
- **Origine**: conversazione con Roberto (23/6). Definizione di partenza: *«imparare» = colmare un GAP verso uno SCOPO* — si impara ciò che serve a chiudere una distanza fra dove si è e uno scopo.

---

## 1. Il principio (uno solo)

Un solo **motore-di-riempimento-gap**, **due alimentazioni** che producono la STESSA valuta — l'**intento** (`verbo+oggetto` / richiesta) — e quindi **indistinguibili a valle**:

- **Bottom-up (esplicito)** — l'utente chiede; se nessuna capability copre → **gap** (`lacune` / Aporia, il vicolo cieco onesto §2.8) → **`synt`** sintetizza la capacità mancante. Priorità: **`n_seen`** (ricorrenza). *Scopri il gap quando accade.*
- **Top-down (telos / implicito)** — un **telos** dichiarato («aiutami nel lavoro») viene **DECOMPOSTO** in sotto-goal → intenti impliciti; le foglie che non agganciano una capability → **gap** → **`synt`**. Priorità: **centralità-nel-telos + esercizio osservato**. *Anticipi il gap dallo scopo.*

Entrambi **convergono sullo stesso fondo**: **mnestoma** (aggancia una capability esistente) oppure **`synt`** (proposta per chiudere il gap).

## 2. L'anello (ciò che lo rende APPRENDIMENTO, non due tubi paralleli)

- **top-down PROPONE → bottom-up VALIDA**: una capability dedotta dal telos diventa "reale" solo se l'uso la **esercita**; altrimenti si **pota** (proattivo nella proposta, reattivo nella validazione).
- **bottom-up RICORRE → top-down ASSORBE**: un pattern esplicito ad alto `n_seen` **rivela/raffina il telos** (il telos è anche imparato dal basso = user-modeling, W2 del report feasibility).

L'alto guida; il basso corregge l'alto.

```
   TELOS ──decompone──►  intenti IMPLICITI ─┐
     ▲                                       ├─► [motore-gap] ─► mnestoma / synt
     └─assorbe (n_seen)── intenti ESPLICITI ─┘          │
                                                         └─ esercizio reale → conferma / pota
```

## 3. La cascata di decomposizione (top-down)

```
TELOS «aiutami nel lavoro»
  ├─ tieni in ordine la documentazione
  ├─ gestisci il calendario
  ├─ archivia fatture e documenti
  └─ rispondi alle email importanti
        ├─ trova le importanti
        └─ proponi una risposta
              └─ … fino a una foglia che TOCCA mnestoma:
                 ─ aggancia un executor (esistente o synt) → OK
                 ─ NON aggancia → gap implicito → synt propone
```

Termina **quando una foglia si connette al vocabolario chiuso / mnestoma**. Senza questo ancoraggio, decompone all'infinito in astrazioni. È lo **STESSO decomposer/planner** di `run_turn`, eseguito in **modalità-telos (proattiva)** invece che reattiva su una query.

## 4. Mappa ai primitivi GIÀ presenti in Metnos

| Pezzo | Primitivo esistente |
|---|---|
| gap | `lacune.n_seen` (terminator) + Aporia (vicolo cieco §2.8) |
| acquisizione | `synt` (5 stadi, `request_new_executor`) |
| scopo + filtro | telos (`runtime/telos_introspect.py`) + **vaglio** (allineamento teleologico) |
| memoria delle capability | mnestoma |
| decompositore | il planner/decomposer di `run_turn` |

**Mancano solo gli INNESTI** (non i moduli, §report feasibility): (1) **modalità-telos** del decomposer (proattivo); (2) **feed** delle foglie-non-agganciate a `synt`; (3) **potatura per uso reale**; (4) **raffinamento del telos da `n_seen`**. Il vaglio fa già da FILTRO (cosa, del decomponibile, serve davvero al telos).

## 5. Nodi onesti

- **La decomposizione è lavoro LLM vero** (spezzare «aiutami nel lavoro» è aperto) — legittimo (è NLU di alto livello), ma va **ANCORATO**: le foglie devono cadere nel vocabolario chiuso/mnestoma, o astrae senza fondo.
- **Proattività → rischio sovra-costruzione**: **proponi, non auto-applicare** (`proposals_state`) + **verifica** (admission gate 7-layer + vaglio) + **pota il non-usato**.
- **Priorità implicita ≠ `n_seen`**: serve l'**esercizio-osservato** come segnale di domanda reale, altrimenti il telos genera capability-fantasia.

## 6. Precondizione (perché NON ora)

Questa architettura presuppone che il **gap-signal sia AFFIDABILE.** Un sistema che **mente** su cosa ha fatto (§2.8: «99 spostate» con 0 fatte, bug live 23/6) **non può imparare dai gap** — non sa distinguere «fatto» da «non fatto», quindi non sa nemmeno *cosa* è un gap.

→ La **precondizione** è la **stabilità + onestà delle funzioni base**. Il lavoro di base (es. il guard §2.8 mail del 23/6, ADR 0177) **NON è un detour: è la FONDAZIONE** di questo motore. *«Stabilizza la base»* e *«costruisci verso questo»* sono **la stessa strada, in ordine.**

---

**Riferimenti**: ADR 0177 (analisi motore), ADR 0161 (Praxis), ADR 0170
(tassonomia skill), ADR 0185 (W1 learning-loop) e
`internal/roadmap/RM-0001-conoscenza-utente-locale.md` (conoscenza utente).
Conversazione 23/6 (single→multi-skill-agent, bottom-up/top-down).
