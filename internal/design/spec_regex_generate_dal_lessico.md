# Regex generate dal lessico — specifica implementativa

**Data**: 16 agosto 2026 · **Stato**: specifica approvata, da eseguire più
avanti (decisione di Roberto: refactor completo one-off, non ora)
**Analisi che la motiva**: `analysis_regex_i18n_16_8_2026.md`
**Destinatario**: un modello di classe inferiore a Opus. Questo documento è
scritto per essere ESEGUITO, non interpretato.

---

## 0. Come si legge questo documento

DEVI: eseguire i passi di §10 nell'ordine dato, verificando ciascuno col suo
criterio prima di passare al successivo.
NON DEVI: cambiare il formato dati di §3, le firme di §4 o i test di §9 senza
che Roberto l'abbia approvato.
OK: se un passo non è verificabile col suo criterio, fermarsi e chiedere.
ERRORE: proseguire perché «sembra funzionare».

Ogni volta che questo documento dice «DEVI» o «NON DEVI», non è enfasi: è un
vincolo che un test controlla.

---

## 1. Che cosa esiste già — NON riscriverlo

Il meccanismo che Roberto ha descritto («generata prima dell'uso oppure al
boot e richiamata da un dizionario di regex compilate») **esiste già per
l'80%** in `runtime/detection_lexicon.py`:

| Pezzo | Dove | Stato |
|---|---|---|
| Dizionario di regex compilate | `_regex_cache: dict[(concept, lang), list]` | ✅ c'è |
| Compilazione pigra al primo uso | `_compiled(concept)` | ✅ c'è |
| Invalidazione su scrittura | `_invalidate(concept)` | ✅ c'è |
| Unione forme `{lingua_corrente} ∪ {it,en}` | `_resolve(concept)` | ✅ c'è |
| Traduzione automatica di elenchi di frasi | `jobs/detection_translate_pending.py` | ✅ c'è |
| Elenchi di lunghezza diversa per lingua | `_PHRASES_TMPL` dice già «drop forms that have no equivalent» | ✅ c'è |

DEVI: riusare `_regex_cache`, `_compiled` e `_invalidate` così come sono.
NON DEVI: creare una seconda cache, un secondo punto di invalidazione o un
«compile all at boot» separato. La cache pigra per `(concept, lingua)` È il
dizionario richiesto; si popola al primo uso e si svuota quando il lessico
cambia.
ERRORE: aggiungere `compile_all_at_boot()`. Il boot non conosce la lingua di
una richiesta futura, e precompilare per tutte le lingue spreca memoria per
lingue mai usate.

**Il pezzo che manca è uno solo**: oggi il payload di un concetto
`kind="regex"` è un pattern scritto a mano. Deve diventare un **template con
segnaposto** più **elenchi di termini per lingua**.

---

## 2. Il modello: template invariante + slot a cardinalità libera

Il problema che questo modello risolve, con le parole di Roberto: *«in alcune
lingue una lista di sinonimi può non avere un corrispettivo esatto; più
termini italiani possono essere ricompresi da un solo termine»*.

Quindi:

- Il **template** porta la struttura (confini di parola, spazi, negazioni,
  ancore). Cambia raramente e può, se una lingua lo richiede, essere diverso
  per quella lingua.
- Gli **slot** portano le parole. Sono elenchi di **lunghezza qualsiasi**: 33
  termini in italiano, 11 in inglese, 1 in una terza lingua, e va bene.

```
template (it):  \b{{PROSSIMITA}}\b
slot PROSSIMITA (it): ["vicino a me", "qui vicino", "piu vicin*", "in zona", ...]   → 12 termini
slot PROSSIMITA (en): ["near me", "nearby", "nearest", "closest", ...]              → 8 termini
slot PROSSIMITA (de): ["in der Nähe"]                                               → 1 termine
```

Espansione (it): `\b(?:vicino\s+a\s+me|qui\s+vicino|piu\s+vicin\w*|in\s+zona|…)\b`

DEVI: trattare la cardinalità di uno slot come **non nota a priori**, in ogni
punto del codice.
NON DEVI: scrivere codice che assume «almeno due termini», «gli stessi
termini in tutte le lingue» o «lo stesso numero di alternative».
OK: `"|".join(escaped)` funziona con 1, 5 o 40 termini.
ERRORE: `f"({a}|{b})"` con due variabili nominate.

---

## 3. Formato dati — esatto

### 3.1 Nuovo `kind`

In `runtime/detection_lexicon.py`:

```python
VALID_KINDS = ("phrases", "regex", "mapping", "template")
```

DEVI: aggiungere `"template"` in coda. NON DEVI: rimuovere `"regex"` — resta
per i pochi casi di §6.4.

### 3.2 Payload di un concetto `template`, per lingua

Un oggetto JSON con **esattamente due chiavi**:

```json
{
  "pattern": "\\b{{PROSSIMITA}}\\b",
  "slots": {
    "PROSSIMITA": ["vicino a me", "qui vicino", "piu vicin*", "in zona"]
  }
}
```

Regole del formato, tutte verificate da un test:

1. `pattern` è una stringa. I segnaposto hanno la forma `{{NOME}}` con NOME
   in `[A-Z][A-Z0-9_]*`.
2. `slots` è un oggetto `{NOME: [termini]}`. Ogni chiave DEVE comparire nel
   `pattern`; ogni segnaposto del `pattern` DEVE avere una chiave.
3. Un elenco di termini può avere **lunghezza 0**. Vedi §5.1 per cosa
   significa.
4. Un termine è testo naturale, **non** una regex. L'unico metacarattere
   ammesso è `*` in coda a una parola (§3.3).
5. `pattern` può contenere sintassi regex ordinaria (`\b`, `\s+`, `(?:…)`,
   `$`, lookahead). È l'unica parte dove la regex è ammessa, ed è scritta da
   un umano.

### 3.3 Il troncamento `*`

Un termine che finisce con `*` significa «questa radice più qualsiasi
continuazione di parola».

| Termine | Espande a | Cattura |
|---|---|---|
| `vicin*` | `vicin\w*` | vicino, vicina, vicini, vicine, vicinissimo |
| `cartell*` | `cartell\w*` | cartella, cartelle |
| `vicino a me` | `vicino\s+a\s+me` | esattamente quella frase, spazi liberi |

DEVI: ammettere `*` **solo in coda a un termine**.
NON DEVI: ammettere `*` in mezzo, né altri metacaratteri.
OK: `"piu vicin*"`. ERRORE: `"vicin*o"`, `"(vicino|vicina)"`, `"vicin.*"`.

Motivo: chi compila o traduce un elenco di termini è un traduttore o un
modello che localizza frasi, non un autore di regex. Un solo simbolo,
posizionale, è imparabile; una sintassi regex no.

---

## 4. Le funzioni da scrivere — firme esatte

Tutte in `runtime/detection_lexicon.py`. Nessun modulo nuovo.

```python
_SLOT_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")


def _expand_term(term: str) -> str:
    """One natural-language term -> one regex branch.

    Whitespace runs become ``\\s+`` so a double space still matches; a
    trailing ``*`` becomes ``\\w*``; everything else is escaped verbatim.
    """


def _expand_slot(terms: list[str]) -> str | None:
    """A term list -> one alternation group, or None when the slot is empty.

    ``None`` means the whole pattern must become inert: see §5.1. Longest
    term first, so a longer form is preferred over a prefix of itself.
    """


def build_pattern(pattern: str, slots: dict[str, list[str]]) -> str | None:
    """Template plus slots -> one regex source string, or None when inert.

    Every placeholder must have a slot and every slot must be used; a
    mismatch raises ValueError, because it is an authoring error that must
    not degrade silently into a pattern that matches nothing.
    """
```

E in `_compiled(concept)`, un ramo in più:

```python
    if res and res[0] == "template" and isinstance(res[2], dict):
        source = build_pattern(res[2].get("pattern", ""),
                               res[2].get("slots", {}))
        if source:
            out.append(re.compile(source, re.IGNORECASE))
```

DEVI: lasciare intatto il ramo `kind == "regex"` esistente.
NON DEVI: toccare `match()`, `search()`, `regexes()`: consumano `_compiled` e
non devono sapere se il pattern era scritto a mano o generato.

### 4.1 L'unione fra lingue per un `template`

`_resolve` oggi unisce liste (`phrases`/`regex`) e dizionari (`mapping`). Per
`template` DEVE unire così:

- `pattern`: **vince la prima lingua della catena** (`current_lang`, poi it,
  poi en). NON si concatenano pattern.
- `slots`: si uniscono **per nome di slot**, come già fa `mapping` per le sue
  chiavi: i termini italiani e inglesi restano entrambi disponibili, cosa che
  serve ai comandi-prestito («undo», «send me»).

OK: lingua `de` con `pattern` proprio e 1 termine, più i termini it/en uniti
allo stesso slot.
ERRORE: prendere il `pattern` italiano e i termini tedeschi quando la lingua
corrente è `de` e ha un pattern nativo.

---

## 5. Le sette trappole

Ognuna ha causato, o causerebbe, un difetto silenzioso.

### 5.1 Slot vuoto — la più pericolosa

Se un elenco è vuoto, `"|".join([])` produce `""` e `(?:)` **matcha la
stringa vuota**. Un pattern `\b(?:)\b` non è innocuo: in altre posizioni un
gruppo vuoto rende la regex universale, e il concetto passa a dire «sì» a
tutto.

DEVI: far restituire `None` a `_expand_slot([])`, e far restituire `None` a
`build_pattern` se **uno qualsiasi** degli slot è vuoto.
DEVI: quando `build_pattern` ritorna `None`, non compilare nulla e lasciare
`_compiled` con la lista vuota → il concetto non matcha mai.
NON DEVI: sostituire uno slot vuoto con `.*`, con `\b\b`, o saltarlo.
OK: lingua senza termini per quello slot ⇒ il concetto è inerte in quella
lingua, e `verify_coverage` lo segnala già come gap.
ERRORE: un concetto che, per assenza di traduzione, comincia a matchare tutto.

### 5.2 Spazi nei termini multi-parola

L'utente scrive «vicino  a me» con due spazi, o «vicino\na me» andando a capo.

DEVI: sostituire ogni sequenza di spazi **dentro il termine** con `\s+`.
ERRORE: `re.escape("vicino a me")` e basta — matcha solo con uno spazio.

### 5.3 Ordine delle alternative

DEVI: ordinare i termini dal **più lungo al più corto** prima di unirli.
Motivo: l'alternanza regex è leftmost-first; con «vicino» prima di «vicino a
me», il match si ferma su «vicino» e un'eventuale cattura perde il resto.
NON DEVI: affidarti all'ordine in cui il traduttore li ha scritti.

### 5.4 Escape

DEVI: `re.escape` su ogni pezzo di termine.
Motivo: un termine può contenere `.` (`«ecc.»`), `?`, `(`, `'`. Senza escape,
un termine tradotto può rompere la compilazione — e il `try/except re.error`
in `_compiled` lo nasconderebbe con un warning che nessuno legge.

### 5.5 Accenti e apostrofi

Gli elenchi attuali enumerano già entrambe le forme («più» e «piu», «piu'»).

DEVI: continuare a enumerarle nell'elenco dei termini.
NON DEVI: introdurre una normalizzazione Unicode nel matcher in questo
lavoro. Cambierebbe il comportamento di 93 concetti in un refactor che ne sta
già cambiando la forma; sono due lavori, e vanno misurati separatamente.

### 5.6 Il pattern NON si traduce

DEVI: far copiare al daemon di traduzione il campo `pattern` **verbatim**
nella lingua nuova, e tradurre **solo** i termini degli slot.
NON DEVI: passare il `pattern` all'LLM. Il motivo per cui oggi il daemon salta
`kind="regex"` («un regex sbagliato è peggio del gap») resta valido: nessun
modello scrive regex per una lingua che nessuno riverificherà.
OK: una lingua che ha bisogno di una struttura diversa riceve un `pattern`
proprio, scritto da un umano, come eccezione dichiarata.

### 5.7 Cardinalità: il punto di Roberto

DEVI: accettare che l'elenco tradotto abbia lunghezza diversa dall'originale,
in **entrambe** le direzioni.
NON DEVI: scrivere test che confrontano `len(slots_it["X"]) ==
len(slots_en["X"])`, né allarmi se una lingua ha meno termini.
OK: `_PHRASES_TMPL` dice già «drop forms that have no equivalent»: il
comportamento voluto è già nel prompt del daemon.
ERRORE: un traduttore che, per «riempire», inventa quattro sinonimi che nella
lingua di destinazione non si usano — è peggio di un elenco corto.

---

## 6. Come si scrive un elenco di termini

Questa sezione è per chi compila l'elenco (umano o daemon). È la parte che
Roberto ha indicato come «molto molto curata».

### 6.1 Che cosa è un termine

Un termine è **una forma che una persona scriverebbe davvero**, non un
sinonimo da dizionario.

OK: `["più vicina", "qui vicino", "in zona", "dove sono"]`
ERRORE: `["prossimale", "adiacente", "limitrofo"]` — corretti in italiano,
mai scritti in una chat.

### 6.2 Quante forme

Quante ne servono, zero incluse. NON esiste un numero minimo.

DEVI: preferire un elenco corto e vero a uno lungo e speculativo. Ogni forma
in più è una possibilità in più di falso positivo.
DEVI: usare il troncamento `*` invece di enumerare le flessioni, quando la
lingua le forma per suffisso: `vicin*` sostituisce cinque righe.
NON DEVI: usare `*` in lingue dove la flessione non è per suffisso — lì
l'enumerazione è l'unica strada corretta.

### 6.3 Il confine, che vale più dell'elenco

DEVI: scrivere sopra ogni concetto, come commento nel seed, **che cosa NON
deve matchare**, con un esempio.

```python
# geo.self_proximity — prossimità riferita a CHI CHIEDE.
# NON deve matchare «vicino a Padova»: lì il centro è un luogo nominato e la
# query lo porta già. Il superlativo nudo a fine richiesta («la più vicina?»)
# invece sì: non ha un altro termine di paragone.
```

Motivo: chi traduce vede l'elenco e il nome del concetto. Senza il confine,
traduce le parole e perde l'intenzione — che è l'unica cosa che conta.

### 6.4 Quando NON usare un template

Restano `kind="regex"` i pattern che sono **struttura, non parole**:
`^last[-_ ]?(\d+)d$`, `\$\{RUNTIME:([a-z_]+)\}`, `\.(jpg|png|heic)$`.

DEVI: tenerli `regex` e non toccarli.
Riconoscimento: se il pattern non contiene parole di lingua, non è materia di
questo lavoro.

---

## 7. Il daemon di traduzione — che cosa cambia

In `runtime/jobs/detection_translate_pending.py`:

DEVI: aggiungere il ramo `kind == "template"`, che
1. legge il payload sorgente,
2. per **ogni slot**, chiama `_llm_localize` con `_PHRASES_TMPL` (già
   esistente, già corretto: dice di scartare le forme senza equivalente),
3. ricompone `{"pattern": <copiato verbatim>, "slots": {…tradotti…}}`,
4. scrive con `set_translated`.
NON DEVI: cambiare `_PHRASES_TMPL`.
NON DEVI: chiedere all'LLM di produrre l'intero payload in un colpo: uno slot
per chiamata, così una localizzazione fallita non invalida gli altri.
DEVI: se **tutti** gli slot tornano vuoti, NON scrivere la riga: lasciarla
pendente e contarla, come già fa `skipped_regex`. Un concetto inerte scritto
come «tradotto» è un gap che si nasconde.

---

## 8. Migrazione — ricetta e esempio completo

### 8.1 La ricetta, per ogni regex di lingua

1. Isola le parole: tutto ciò che è alternanza di termini diventa uno slot.
2. Ciò che resta (confini, spazi, ancore, negazioni) è il `pattern`.
3. Dai allo slot un nome che dica il RUOLO, non il contenuto: `VERBI_RICERCA`,
   non `CERCA_TROVA_FIND`.
4. Scrivi il confine (§6.3).
5. Sostituisci il call-site con `detection_lexicon.match(concept, testo)`.
6. Aggiungi il caso al test dei concetti (§9.3).

### 8.2 Esempio completo — `dispatch.py:3319`

Prima (regex italiana+inglese cablata nel codice):

```python
_VERBI = re.compile(
    r"(?i)\b(cerca(mi)?|trova(mi)?|search|find|apri|open|leggi|read|"
    r"scarica|download|mostra(mi)?|show)\b")
```

Dopo, nel seed:

```python
    # request.producer_verb — il verbo con cui l'utente CHIEDE un dato.
    # NON deve matchare un verbo che nomina un'azione sul contenuto
    # («scrivi», «cancella»): quelli sono clausole, non richieste di lettura.
    R("request.producer_verb", "template",
      it={"pattern": r"\b{{VERBI}}\b",
          "slots": {"VERBI": ["cerca*", "trova*", "apri", "leggi",
                              "scarica", "mostra*"]}},
      en={"pattern": r"\b{{VERBI}}\b",
          "slots": {"VERBI": ["search", "find", "open", "read",
                              "download", "show"]}})
```

E al call-site:

```python
if _dl_match("request.producer_verb", query):
```

Nota che `cerca*` sostituisce `cerca|cercami` e `trova*` sostituisce
`trova|trovami`: sei righe di regex diventano sei termini leggibili.

### 8.3 Ordine della migrazione

Per rischio decrescente (dall'analisi §7):

1. `confirm.yes` / `confirm.no` — senza questi, in una lingua nuova l'utente
   non può confermare NIENTE.
2. `runtime/prefilter.py` (81 termini) e `runtime/fast_path.py` (79).
3. `runtime/target_device.py` (29), `runtime/engine/dispatch.py` (17 + 20
   regex).
4. I restanti 23 concetti `kind="regex"`.
5. Le 12 liste minori (`compare_entries`, `describe_images`, `store_entries`,
   `backend_resolver`, `skill_codegen`, …).
6. Deduplicare i **tre** elenchi di prossimità (`prefilter.py:741`,
   concept `geo.self_proximity`, affinity di `find_places`) su un'autorità
   sola.

DEVI: migrare un concetto per commit, con il suo test.
NON DEVI: fare un commit che ne sposta venti: se uno regredisce non si
capisce quale.

---

## 9. Test obbligatori

Nuovo file `tests/runtime/i18n/test_lexicon_templates.py`.

### 9.1 Espansione

```python
assert _expand_term("vicino a me")  == r"vicino\s+a\s+me"
assert _expand_term("vicin*")       == r"vicin\w*"
assert _expand_term("ecc.")         == r"ecc\."
assert _expand_slot([])             is None
assert _expand_slot(["a"])          == "(?:a)"
# lunghezza qualsiasi, ordine dal piu' lungo
assert _expand_slot(["a", "abc"])   == "(?:abc|a)"
```

### 9.2 Inerzia, non universalità

```python
assert build_pattern(r"\b{{X}}\b", {"X": []}) is None
# e il concetto non matcha nulla, invece di matchare tutto
assert not D.match("concetto.con.slot.vuoto", "qualsiasi cosa")
assert not D.match("concetto.con.slot.vuoto", "")
```

DEVI: questo test. È l'unico che distingue il difetto silenzioso dal
comportamento voluto.

### 9.3 Cardinalità libera

```python
# tre lingue, tre lunghezze: nessuna deve essere trattata come errore
payload_de = {"pattern": r"\b{{P}}\b", "slots": {"P": ["in der Nähe"]}}
# ... registra, imposta lingua de, verifica che matchi
```

NON DEVI: scrivere un test che pretende parità di lunghezza fra lingue.

### 9.4 Coerenza template/slot

```python
with pytest.raises(ValueError):
    build_pattern(r"\b{{A}}\b", {"B": ["x"]})      # segnaposto senza slot
with pytest.raises(ValueError):
    build_pattern(r"\bfisso\b", {"A": ["x"]})      # slot mai usato
```

### 9.5 Equivalenza con il comportamento attuale — la rete di sicurezza

Per ogni concetto migrato, un test che verifica che le stesse frasi che
matchavano prima matchano ancora, e quelle che non matchavano non matchano.

DEVI: raccogliere le frasi dal corpus reale delle query
(`~/.local/share/metnos/turns/*.jsonl`), non inventarle.
DEVI: eseguirlo PRIMA e DOPO la migrazione del concetto, e confrontare.
ERRORE: migrare un concetto e verificare a occhio che «funziona ancora».

### 9.6 La guardia anti-ricomparsa

Un test che fallisce se in `runtime/` compare una **nuova** regex letterale
contenente parole di lingua, fuori da una lista di eccezioni dichiarate.

DEVI: partire dall'inventario in `analysis_regex_i18n_16_8_2026.md` come
elenco iniziale di eccezioni, e togliere una riga a ogni migrazione.
Motivo: senza questa guardia, l'inventario andrà rifatto fra sei mesi con
numeri più grandi. È l'unica cosa che rende il lavoro permanente.

---

## 10. Ordine di esecuzione, con criterio di verifica

| # | Passo | Verificato da |
|---|---|---|
| 1 | `_expand_term`, `_expand_slot`, `build_pattern` | test §9.1, §9.2, §9.4 verdi |
| 2 | `kind="template"` in `VALID_KINDS` + ramo in `_compiled` | un concetto template registrato a mano matcha |
| 3 | Unione fra lingue per `template` in `_resolve` (§4.1) | test §9.3 verde |
| 4 | Ramo `template` nel daemon (§7) | una lingua finta viene accodata e tradotta slot per slot |
| 5 | Migrazione concetto per concetto (§8.3) | test §9.5 per ciascuno, `pytest tests/runtime` verde |
| 6 | Guardia anti-ricomparsa (§9.6) | fallisce se aggiungi una regex di lingua a mano |

DEVI: `python3 -m pytest tests/runtime -q` verde dopo OGNI passo.
NON DEVI: iniziare il passo 5 prima che 1-4 siano verdi.

---

## 11. Che cosa NON fare

- NON DEVI creare un modulo nuovo: tutto sta in `detection_lexicon.py` e nel
  suo daemon.
- NON DEVI cambiare la firma di `match()`, `search()`, `forms()`,
  `mapping()`, `regexes()`: i 93 concetti esistenti devono continuare a
  funzionare senza toccare un call-site.
- NON DEVI convertire i 187 pattern tecnici di §6.4.
- NON DEVI introdurre normalizzazione Unicode (§5.5).
- NON DEVI toccare `register()` per accettare un dizionario di lingue in
  questo lavoro: è la decisione 2 dell'analisi, ancora aperta, e va misurata
  a parte.
- NON DEVI cancellare `kind="regex"`.

---

## 12. Che cosa resta aperto dopo questo lavoro

Due cose, entrambe già segnate nell'analisi e non risolte qui:

1. `register()` accetta solo `it=` ed `en=`. Un concetto non si può seedare
   in tre lingue nel codice, nemmeno volendo. Finché resta così, la terza
   lingua esiste solo come riga generata dal daemon.
2. Le 24 liste di termini cablate fuori dal lessico (286 termini) vanno
   spostate; questa specifica dà il meccanismo, non le sposta.
