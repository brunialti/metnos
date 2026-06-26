# ADR 0179 — Scope-restriction vs scope-extension: una classe di contaminazione del routing

- **Stato**: ACCETTATO (fix implementato + verificato live, 26/6/2026).
- **Data**: 2026-06-26.
- **Origine**: intuizione di Roberto durante il debug del bug provider-blind (turn
  582b4824/22f32adb). Ipotesi posta come «da verificare», verificata sistematicamente.
- **Scope**: il pool di routing (`engine/routing_pool.py`). Estende la dottrina
  [[feedback-contamination-is-function-not-prompt]].

---

## 1. Il pattern

Una query può contenere DUE modificatori in conflitto:

- **RESTRIZIONE dello scope** — vincola l'insieme bersaglio: provider («su github»),
  formato («.py»), path («in /tmp»), tempo («di oggi»), stato («non lette»).
- **ESTENSIONE dello scope** — quantificatore universale che massimizza l'inclusione.
  L'estensore è SPECIFICO PER ASSE: per la cardinalità è «tutti/ogni»; per il tempo
  è «sempre/di sempre»; non c'è un unico quantificatore universale.

Quando convivono, l'estensione può far scivolare l'interpretazione dell'intent LLM
verso l'accezione più ampia, scavalcando la restrizione. È **contaminazione**: un
segnale sporca l'interpretazione di un altro. Peggiora col **priming d'ordine**
(l'estensore PRIMA della restrizione si impone — gemello di «la 1ª def nel prompt
si impone»).

## 2. Il caso confermato e perché è raro

Sintomo (bug live): «riassumi **tutti** i file readme.md **su github**» → l'intent
classifica `object=urls` (legge «file» nella sua accezione web: un URL È un file
remoto) → il routing sceglie `find_urls`(web) invece di `find_files_github`. Senza
«tutti», `object=files` → routing corretto. «tutti» ha **liberato l'accezione più
generale** di «file».

**Verifica sistematica (non assunzione).** Testati gli assi con l'estensore GIUSTO
per ciascuno:

| Asse restrizione | Estensore | Effetto «X + estensore» | Scivola? |
|---|---|---|---|
| provider (su github) | tutti | object `files`→`urls`, executor cambia | **SÌ** |
| formato (.py) | tutti | `pattern=*.py` invariato | no |
| tempo (di oggi) | **sempre** | `time_window=today` invariato; «di sempre»→`all` (corretto) | no |

**Condizione STRUTTURALE perché il bug esista** (tutte e tre insieme):
1. due OBJECT in relazione generale/specifico (`urls` ⊃ `files`);
2. entrambi con un producer generico nel catalogo (`find_urls`, `find_files`);
3. la parola dell'object è AMBIGUA fra i due («file» = sia files sia url-come-file).

**Differenza con gli assi che NON scivolano**: tempo/formato/stato agiscono su un
ARGOMENTO (`time_window`, `pattern`), non sull'OBJECT. L'estensione può cambiare il
valore dell'arg (`today`→`all`), ma l'executor resta lo stesso → nessun misroute
possibile. Solo quando l'estensione sposta l'**object** il routing può sbagliare.

**Sul vocabolario attuale (23 object) SOLO `files/urls` soddisfa le 3 condizioni**
(enumerati tutti i producer generici per object). Quindi il fix non è una pezza
per-web: è il fix dell'UNICA istanza strutturale del principio.

## 3. La decisione

Il guard di pool **SCOPE-RESTRICTION** (`routing_pool._provider_recruit_and_gate`,
parte 3): quando un modificatore-restrizione (oggi: un provider attivo via
`active_provider_suffixes`, SoT) è presente E un produttore provider-nativo
dell'accezione-stretta è nel pool, **la restrizione vince** → sopprime i produttori
generici dell'accezione-ampia (gli url-generics, `object=urls`, derivati da SoT non
hardcoded). «su github» VINCOLA «i file» a github, non al web — anche se l'intent è
scivolato a `object=urls`.

Deterministico (§7.9), SoT-based, zero liste-sinonimi. Si applica a OGNI provider
(github, google_workspace) per costruzione.

### Casi legittimi preservati (mai sovra-soppressione)
- **web genuino** senza provider («cerca articoli sul web su rust»): `suffixes`
  vuoto → guard non scatta → l'accezione-ampia è la richiesta reale.
- **URL esplicito** («leggi github.com/o/r/blob/F»): single-URL read preservato.
- **mixed-compound genuino** (≥2 clausole-azione, una `urls`): la clausola
  dell'accezione-ampia è reale, non scivolamento.

## 4. Generalità futura

Il guard è scritto come PRINCIPIO (scope-restriction), non come «web-steal». Se in
futuro nascesse una seconda coppia object generale/specifico con producer
concorrenti, il meccanismo è già il principio — l'accezione-ampia è derivata
(`object=urls` perché è l'object dei producer-web generici), non cablata. Una
seconda istanza richiederebbe solo di generalizzare la derivazione dell'object-ampio,
non riscrivere la logica.

## 5. Conseguenze

- I 3 misroute provider-blind (web-via-find_urls, count-via-find_files-locale, e lo
  scivolamento da «tutti») sono chiusi deterministicamente.
- Il pattern «estensione-vs-restrizione = contaminazione» è ora documentato come
  classe nota: ogni futuro asse con object multi-accezione va verificato contro le
  3 condizioni strutturali §2.
- Verifica: `test_routing_provider_recruit.py` (T2-scivolamento + T3-web-genuino),
  turni reali live (T2 cold deterministico 2/2 github; T3→web), bench 29/29.

## Riferimenti
- [[feedback-contamination-is-function-not-prompt]] — la dottrina madre.
- ADR 0165 (backend_resolver) — «il provider è configurazione, non intent».
- ADR 0141 — provider github. ADR 0136 — asse provider §2.2.
