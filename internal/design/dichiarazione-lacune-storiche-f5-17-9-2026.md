# Dichiarazione delle lacune storiche residue — F5, 17 settembre 2026

**Scopo.** L'emittente del certificato F5 rifiuta finché le lacune della storia
non sono **dichiarate** e **riviste**. Questo documento è la dichiarazione. Non
è una certificazione, non attiva nulla e non registra nulla: serve alla
revisione avversariale che Roberto ha chiesto.

**Come leggere il documento, in breve.** La storia della nascita degli executor
viene ricomposta e ogni pezzo che non si incastra viene lasciato scritto invece
che scartato in silenzio. Qui elenchiamo quei pezzi, diciamo quanti sono, quali
sono (con un'impronta, così più avanti si può dire se sono gli **stessi**), e
perché non falsano la soglia di ingresso.

---

## 1. L'osservazione

Ricomposizione completa eseguita il 17 settembre attraverso il lanciatore di
root già installato (`metnos-agent-admin`), in **sola lettura**, con i lettori
proprietari e le dipendenze della release in esercizio. Nessun servizio fermato,
nessun dato modificato, il lavoro LRE in corso non è stato toccato.

| fatto | valore |
|---|---|
| release in esercizio | 63 |
| testa richiesta | `sha256:97649f3a…92e3` |
| lettore | uid 995 / gid 985 (account di servizio) |
| ricevute fisiche | 8 270 |
| righe produttore | 8 288 |
| **candidati tecnici** | **40** |
| **produttori autenticati** | **2** (`builtin_contract_generator`, `stack_reconcile`) |
| impronta dell'insieme delle lacune | `sha256:fd18f7dc…7a65` |
| esito | `reconciled_observed_inventory_with_explicit_evidence_limits` |

La soglia approvata chiede **almeno 5** ammissioni tecniche e **almeno 2**
produttori autenticati. Sono 40 e 2.

---

## 2. Le lacune, una per una

Cinque codici. Per ciascuno: cosa significa, quanti, l'impronta delle identità,
e la disposizione proposta.

### 2.1 `historical_context_policy_unavailable` — 520

*Impronta*: nel censimento della sonda, voce omonima.

**Cosa vuol dire.** La ricevuta nomina un contesto di ammissione per cui la
catena selezionata non porta una dichiarazione firmata. La selezione è
l'intersezione fra i contesti nominati dalle ricevute e quelli che la catena
verificata dichiara come transizioni: questi 520 stanno fuori da
quell'intersezione.

**Disposizione proposta: fuori ambito, ed è una esclusione.** Queste ricevute
**non entrano** nell'insieme dei candidati. Non possono alzare la soglia: la
riducono. Un esempio dal censimento è una ricevuta di `act_sites/manifest.toml`
sotto un contesto che questa catena non porta.

### 2.2 `generation_not_reconciled` — 48

**Cosa vuol dire.** Il contratto dichiara una generazione per cui nessuna
ricevuta di ammissione è stata ricongiunta.

**Disposizione proposta: esclusione.** Una generazione non ricongiunta non
contribuisce ad alcuna ammissione tecnica.

### 2.3 `producer_without_verified_durable_admission` — 538

### 2.4 `issuance_without_verified_durable_admission` — 538

**Cosa vogliono dire.** Righe di produttore e di emissione che nessuna
ammissione durevole verificata ha usato.

**Disposizione proposta: esclusione, e sono in larga parte una conseguenza.**
Quando una ricevuta viene esclusa (§2.1), le sue righe di produttore e di
emissione restano non usate. I due numeri sono uguali, come ci si aspetta da
righe appaiate. Non sono 538 guasti nuovi.

### 2.5 `unbound_namespace_not_reconciled` — 1

**Cosa vuol dire.** Uno spazio di nomi presente nel deposito, vuoto e senza
legami: `generations` vuoto e un lucchetto di un byte.

**Disposizione proposta: esclusione.** Uno spazio vuoto non porta ammissioni.

---

## 3. L'argomento portante, e il suo contrario

**L'argomento.** Tutte e cinque le lacune **tolgono** prove dall'insieme dei
candidati; nessuna ne aggiunge. La soglia è un **minimo**. Una lacuna che
esclude prove può solo rendere la qualifica più difficile, mai più facile.
Dichiararle non indebolisce la soglia: la rende conservativa.

**Il contrario, che il revisore deve premere.** Una lacuna potrebbe nascondere
non una prova in più ma una **revoca**: un ritiro o una quarantena che avrebbe
dovuto *togliere* autorità e che non è stato ricongiunto. In quel caso
l'esclusione non sarebbe conservativa — starebbe ignorando una sottrazione.

**La risposta, verificabile.** I codici che segnalerebbero esattamente questo
**non compaiono nel censimento**, cioè sono a zero:

| codice assente | cosa avrebbe segnalato |
|---|---|
| `retirement_not_reconciled` | un ritiro dichiarato e non ricongiunto |
| `conflicting_admission_identity` | due ammissioni che si contendono la stessa identità |
| `continuity_predecessor_not_reconciled` | una catena di continuità spezzata |
| `duplicate_stored_identity` | una identità memorizzata due volte |
| `durable_inventory_reread_mismatch` | un inventario che cambia mentre lo si legge |

Inoltre la quarantena è stata **tolta dai candidati tecnici alla fonte** (17/9,
`executor_birth_history.py`): una quarantena cambia un campo del manifest, e la
classe di revisione la chiamava «revisione di contratto». Contarla avrebbe
lasciato che una **revoca alzasse** la soglia che non deve toccare. Ora è
classificata `quarantine` ed esclusa da `technical_acts`.

---

## 4. Cosa questa dichiarazione **non** afferma

- Non afferma che la storia sia completa. Afferma che è **osservata**, e che
  ogni pezzo che non si incastra è scritto qui.
- Non afferma che le 520 ricevute siano prive di valore: afferma che questa
  catena non porta la politica del loro contesto, e che quindi non entrano.
- Non è l'identità d'ambito delle prove. Quella è separata per dominio,
  appartiene a `install/birth_certification_qualification.py`, e viene
  **ricalcolata dal suo proprietario dalla storia viva** al momento della
  registrazione. Se nel frattempo comparisse una lacuna non dichiarata, quel
  valore cambierebbe e l'emittente rifiuterebbe con `undisclosed_evidence_gap`.
  È il meccanismo che rende questa dichiarazione vincolante invece che
  decorativa.
- Non è una certificazione, non attiva F5, e non sostituisce i due cicli mirati
  né la chiave dedicata, che non esistono ancora su questa installazione.

---

## 5. Cosa chiedo al revisore avversariale

1. **Attaccare l'argomento del §3**, non il conteggio. Il conteggio è misurato;
   l'argomento è un giudizio.
2. Cercare un caso in cui una delle cinque lacune possa **nascondere una
   sottrazione** nonostante i cinque codici a zero.
3. Verificare che i 40 candidati tecnici non contengano nulla che non sia
   un'ammissione genuina — in particolare che nessuna quarantena o ritiro sia
   rientrato per un'altra strada.
4. Dire se l'impronta `sha256:fd18f7dc…7a65` è sufficiente come promessa che
   l'insieme rivisto sia lo stesso più avanti, o se serve di più.

---

## 6. Come rifare la misura

```
sudo -n /usr/local/sbin/metnos-agent-admin \
  /opt/metnos/.claude/worktrees/rm0009-development/internal/tools/rm0008_g8_public_history_proof.sh \
  <sha256 dello script> historical-inventory
```

Sola lettura. Se la testa della catena si è spostata, la sonda rifiuta con
`proof_frontier_changed` invece di riportare numeri di una storia diversa.
