# RM-0008 — consegna del gruppo 7

> Stato al 31 agosto 2026. Worktree di lavoro `/tmp/metnos-rm0008-g6`,
> pubblicazione da `/tmp/metnos-rm0008-a-only` (ramo `main`).
> Il gruppo 6 e' chiuso e certificato (roadmap §23.32); questa consegna riguarda
> soltanto il gruppo 7.

## 1. Che cosa deve fare il gruppo 7

Il piano ottimizzato (`internal/reports/rm0008-gruppo6-piano-ottimizzato.md`,
§2 «Confine con il gruppo 7») gli assegna cinque cose, in UNA sola chiamata che
non rilascia i blocchi di deployment, avvio esclusivo e manutenzione:

1. installa e rilegge la topologia dominante;
2. neutralizza ogni `legacy_binding` e prova che nessun ingresso legacy sia gia'
   in volo;
3. ricalcola dai byte correnti catalogo, topologia effettiva ed evidenza con
   `closed_build_enforcement()=True`;
4. costruisce la capacita' privata legata a richiesta, testa precedente,
   catalogo, topologia, evidenza e alle tre sessioni di blocco vive;
5. la consuma una volta sola, ripete le verifiche e soltanto allora oltrepassa
   `CERTIFICATE_READY`.

Poi, fuori dalla chiamata: attivazione reale, disattivazione dei vecchi
ingressi, riavvio, due cicli di instradamento, dichiarazione di F4.

## 2. Che cosa e' gia' costruito e provato, in isolamento

| pezzo | modulo | prove | roadmap |
|---|---|---|---|
| capacita' di completamento (punto 4-5) | `runtime/executor_birth_dominant_startup.py` | 23 | §23.34, §23.37 |
| piano di ritiro, decisione (punto 2) | `runtime/executor_birth_legacy_retirement.py` | 12 | §23.35 |
| evidenza del bit (punto 3) | `runtime/executor_birth_enforcement_evidence.py` | 10 | §23.36, §23.39 |
| ritiro, esecuzione (punto 2) | `runtime/executor_birth_legacy_neutralizer.py` | 12 | §23.38, §23.40 |
| topologia dominante (punto 1) | `runtime/executor_birth_dominant_topology.py` | 13 | §23.41 |
| **composizione dei cinque** | `tests/portable/test_executor_birth_group7_composition.py` | 3 | §23.42 |
| percorsi scrivibili delle unita' | `runtime/executor_birth_service_catalog.py` | — | §23.31, §23.33 |

Nessuno di questi ha un chiamante produttivo: l'autorita' vive isolata finche'
l'involucro non la conia, com'e' stato per il nucleo di pubblicazione di G6-B4.

## 3. Che cosa manca

**Tutti e cinque i punti dell'involucro sono costruiti, provati singolarmente e
provati INSIEME** (§23.42). Resta soltanto cio' che tocca il sistema reale:

- collegare l'involucro al giornale del coordinatore, cioe' far si' che il
  superamento di `CERTIFICATE_READY` passi davvero di li' invece che dalla
  transizione attuale;
- l'esecuzione reale: installazione dei nomi definitivi in
  `/etc/systemd/system`, commutazione dei servizi correnti, ribaltamento del
  letterale in `closed_build_enforcement`, riavvio, due cicli di instradamento,
  dichiarazione di F4.

La seconda parte tocca il server gestito e va concordata prima, non dedotta da
questa consegna. Per la prima, la forma da riusare e'
`install/executor_birth_systemd.py::_install_locked_core_v1`: `require_session()`
ai due estremi, `verify()` prima e dopo, confronto delle due verifiche.

## 4. Trappole gia' pagate — non ripagarle

- **Il file del workflow e' DENTRO la base 2A congelata.** Alzarne il
  `timeout-minutes` fa negare in blocco le sei attivita' 2A. Il budget del job
  Linux e' un vincolo, non un parametro (§23.24).
- **Ogni modifica a un file censito porta con se' la propria conseguenza sul
  censimento**: contenitore nuovo da motivare, autorita' di modulo da ripuntare,
  impronta superata da potare. Se non la si paga nello stesso passo, il debito
  chiuso si riapre (§23.27, §23.30).
- **Due nomi sono stati cambiati perche' l'euristica del censimento li leggeva
  come marcatori linguistici**: `MARKER` e `SUFFIX` nei nomi di simbolo. Il
  falso positivo si toglie rinominando per verita', non silenziando.
- **La matrice e' Linux E Windows.** Una misura locale non dimostra una
  proprieta' generale: e' costato un giro rosso sui fine-riga (§23.39). E una
  prova verde su Windows non dimostra che il meccanismo abbia senso li' — il
  collegamento pendente passava senza mascherare nulla (§23.40).
- **Non modificare l'albero mentre una suite lo misura.** E' successo due volte
  e ha invalidato due misure.
- **Perni**: dopo ogni modifica a `runtime`, `install`, `scripts` o `executors`
  eseguire `internal/tools/rm0008_repin_source_roots.py`, che riallinea privata
  e pubblica nell'ordine che converge. Dopo un modulo NUOVO va anche rigenerato
  l'inventario di produzione (`generate_production_inventory_v1.py --write`), o
  la prova R1 nega.

## 5. Come si verifica

Mirate durante gli incrementi: `tests/portable`, il censimento del lessico, le
due guardie di confine. La suite a copertura totale una sola volta, alla fine, e
su albero fermo. Il confronto onesto e' contro la base pre-G6 `e2305260`, con
lo stesso comando e le stesse condizioni: 74 rossi alla base, 69 all'albero
finale del gruppo 6.
