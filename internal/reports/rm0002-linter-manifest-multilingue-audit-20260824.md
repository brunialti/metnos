# RM-0002 — Rapporto storico di audit e revisione avversariale

> **Natura:** rapporto non normativo  
> **Data:** 2026-08-24  
> **Perimetro:** linter dei manifest, localizzazione dei contratti, inventario,
> firma, caricamento e attivazione della lingua  
> **Specifiche vigenti:** `internal/roadmap/RM-0002-linter-manifest-multilingue.md`
> e `internal/roadmap/RM-0007-pubblicazione-verificata-contratti.md`  
> **Fotografie Git:** `702cd9ec`, `c5c7a6b1`, `fbb518fa`

## 1. Come leggere questo rapporto

Questo file conserva misure, errori di analisi, rilievi avversariali e prove che
non devono più stare nella specifica eseguibile. Descrive il repository e una
singola installazione osservati il 23 luglio e il 24 agosto 2026. Non stabilisce
conteggi permanenti, non autorizza modifiche e non sostituisce codice, test o
ADR.

I numeri decadono quando cambia il catalogo. Le prove di implementazione devono
quindi usare fixture ermetiche e rapporti generati dall'inventario corrente,
mai confronti cablati con i valori riportati qui.

## 2. Conclusioni rimaste valide

L'audit ha dimostrato che:

- `runtime/manifest_lint.py` sceglie implicitamente italiano, poi inglese, poi
  la prima lingua disponibile;
- una lingua può quindi essere attivata senza che il linter abbia controllato
  la relativa descrizione;
- italiano e inglese non si approssimano a vicenda: i superamenti dei limiti e
  i falsi positivi non appartengono agli stessi campi;
- la parità degli atomi macchina può degradare senza essere rilevata;
- il CLI, il materializzatore e il loader non enumerano le stesse classi di
  manifest;
- i test di attivazione sostituiscono il validatore reale con un doppio che
  accetta sempre;
- la promozione linguistica, la firma e il caricamento hanno problemi di
  autorità, concorrenza e coerenza che non appartengono al linter.

Il caso concreto di parità era `set_signatures`: il `PATTERN` inglese mostrava
anche `reason=`, quello italiano no. I pacchetti installati contenevano inoltre
tre errori di corrispondenza fra esempi e schema che nessun inventario comune
raggiungeva.

## 3. Fotografie quantitative

### 3.1 Fotografia del 23 luglio

- 115 manifest osservati;
- 230 descrizioni principali IT/EN;
- 654 descrizioni di argomento;
- nessuna divergenza rilevata nelle classi di atomi allora misurate;
- 108 avvisi di lunghezza italiani e 85 inglesi.

### 3.2 Fotografia del 24 agosto

La prima riverifica contò 122 manifest: 85 core al primo livello, 21 builtin e
16 installati nel profilo utente. Una scansione ricorsiva delle radici usate dal
materializzatore trovò però anche un executor ritirato, portando quella vista a
107 contratti anziché 106. Questa differenza fu la prova che “presente sul
disco”, “distribuito”, “ammesso” e “attivo” devono essere viste distinte.

Nella stessa fotografia:

- le descrizioni principali IT/EN erano 244;
- le descrizioni di argomento erano 693;
- gli avvisi del linter simulato erano 124 in italiano e 100 in inglese;
- gli errori strutturali erano tre in italiano e quattro in inglese;
- una divergenza certa negli argomenti del `PATTERN` era comparsa;
- il percorso dei pacchetti installati restava fuori sia dal CLI sia dalla
  localizzazione RM-0005;
- lo stato linguistico conteneva selettori e fotografie non allineati.

Questi valori sono conservati per spiegare le decisioni, non come criteri di
accettazione.

## 4. Correzioni prodotte dalla revisione

La prima riverifica conteneva due affermazioni errate.

### 4.1 Presenza dei capitoli

Si era scritto che nessun confine controllasse i capitoli
`SCOPO:/PATTERN:/NON:/OUT:` nella lingua tradotta. In realtà
`executor_standard._validate_description()` li richiede in ogni lingua e il
firmatario applica lo standard ai manifest conformi.

La controprova in memoria mostrò:

| Variante | Esito dello standard |
|---|---|
| manifest invariato | ammesso |
| marker tradotti in una lingua sintetica | rifiutato |
| marker presenti ma riordinati | ammesso |
| funzione del `PATTERN` rinominata | ammesso |

La presenza era quindi già protetta; ordine, chiamata e argomenti no.

### 4.2 Copertura del materializzatore

Si era scritto che la pipeline coprisse 106 contratti. La scansione ricorsiva
includeva anche `executors/_retired/reply_messages`, quindi la vista reale era
107. La correzione importante non è il nuovo numero: è il divieto di usare
conteggi manuali e la necessità di classificare origine e stato.

## 5. Esito dei rilievi avversariali

Il secondo giro verificò 23 rilievi: 20 confermati, 3 parzialmente confermati,
nessuno respinto. I verdetti completi restano nella fotografia Git `fbb518fa`.

| ID | Esito | Nucleo verificato |
|---|---|---|
| ADV-001 | confermato | una localizzazione può rifirmare modifiche tecniche precedenti |
| ADV-002 | confermato | manifest, firma e stato non formano una transazione |
| ADV-003 | confermato | verifica e loader possono leggere byte diversi |
| ADV-004 | parziale | RM-0005 resta valida nel proprio confine, ma serve un progetto successivo |
| ADV-005 | confermato | il vecchio schema `Finding` non rappresenta confronti fra lingue |
| ADV-006 | parziale | storia e certificazione erano mescolate; alcune fasi erano componibili |
| ADV-007 | confermato | scoperta, ammissione e autorità erano confuse |
| ADV-008 | confermato | `manifest_hash` era registrato ma non verificato al commit |
| ADV-009 | confermato | registro e companion usavano identità e autorità non unificate |
| ADV-010 | confermato | l'allineatore legacy poteva lasciare manifest scritto e firma vecchia |
| ADV-011 | confermato | il documento ignorava controlli multilingue già presenti |
| ADV-012 | confermato | mancava una grammatica implementabile degli atomi macchina |
| ADV-013 | confermato | i profili non avevano una matrice normativa |
| ADV-014 | parziale | l'enumerazione delle superfici divergeva, senza danno corrente osservato |
| ADV-015 | confermato | i marker lessicali avrebbero ricreato codice cablato per lingua |
| ADV-016 | confermato | `affinity` appartiene ad AFF-I18N-001 |
| ADV-017 | confermato | due piani incompatibili convivevano nella roadmap |
| ADV-018 | confermato | i conteggi manuali erano già scaduti |
| ADV-019 | confermato | il validatore reale non era esercitato dalle prove di attivazione |
| ADV-020 | confermato | diversi criteri non avevano soglia né oracolo |
| ADV-021 | confermato | la normalizzazione della lingua non era riusata |
| ADV-022 | confermato | un percorso nel registro diventava destinazione di scrittura |
| ADV-023 | confermato | storia, specifica e revisioni erano mescolate |

## 6. Difetti aggiuntivi emersi nel secondo giro

### NEW-001 — rifiuto dopo la promozione

`activate_language()` promuove e rifirma prima di eseguire il controllo dei
manifest. Un rifiuto successivo lascia pubblicato il contratto tradotto pur
dichiarando fallita l'attivazione.

### NEW-002 — scritture non atomiche nel firmatario

La pipeline sostituisce il manifest in modo atomico, ma `sign_executor()` lo
riscrive con `write_text()` e salva la firma con `write_bytes()`. Un arresto fra
le due operazioni annulla la garanzia del chiamante.

### NEW-003 — promotori concorrenti

Due promozioni partite dalla stessa base possono entrambe dichiararsi riuscite;
l'ultima scrittura perde l'aggiornamento precedente. Non esistono blocco per
contratto né confronto condizionato sulla generazione attesa.

### NEW-004 — ordine dei capitoli

Il linter segnala il riordinamento come avviso, mentre lo standard controlla
soltanto la presenza. Nessun confine corrente impedisce quindi di pubblicare
capitoli fuori ordine.

## 7. Separazione decisa dopo l'audit

Il lavoro è stato diviso in due progetti:

- RM-0002 conserva lingua esplicita, inventario in audit e confronto
  deterministico degli invarianti fra traduzioni;
- RM-0007 prende in carico base verificata, firma pura, generazioni immutabili,
  concorrenza, recupero e fotografia unica consumata dal loader.

RM-0005 non viene riaperta. Il suo registro e la sua pipeline restano la base
del processo di localizzazione; RM-0007 ne rafforza il confine di pubblicazione.
La localizzazione di `affinity` resta assegnata ad `AFF-I18N-001`.

## 8. Provenienza e riproducibilità

Le tre stesure complete precedenti sono conservate in Git:

- `702cd9ec` — riverifica contro codice e catalogo;
- `c5c7a6b1` — prima revisione avversariale;
- `fbb518fa` — controrevisione e quattro rilievi nuovi.

Chi deve rieseguire l'audit deve registrare almeno revisione Git, radici
inventariate, vista richiesta, hash dei file e script usato. Un nuovo rapporto
deve aggiungersi a questo; non deve aggiornare retroattivamente i numeri qui
conservati.
