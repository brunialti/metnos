# Lessico multilingua — specifica implementativa

Tre parti, indipendenti nell'ordine dato:
**A** regex generate dal lessico (§1-§10) · **B** i tre difetti del
dizionario (§13) · **C** le liste di parole cablate (§14).

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

---

# PARTE B — I tre difetti del dizionario

Analisi che la motiva: `analysis_regex_i18n_16_8_2026.md` §8.

DEVI: eseguire B1 **prima** di qualunque migrazione della parte A o C.
Motivo: B1 cambia la firma con cui si registrano i concetti, e farlo dopo
significa riscrivere due volte gli stessi 93 concetti.

## 13. Le tre modifiche

### 13.1 (B1) `register()` accetta un dizionario di lingue

Oggi:

```python
def register(concept: str, kind: str, *, it, en, match_mode="substring") -> bool
```

Diventa:

```python
def register(concept: str, kind: str, *, forms: dict[str, object],
             match_mode: str = "substring") -> bool:
    """Seed one concept for every language present in ``forms``.

    ``forms`` maps a language code to that language's payload: a list for
    ``phrases``/``regex``, a dict for ``mapping``/``template``. Any number of
    languages is accepted, one included. Idempotent per (concept, language):
    a language already present is left untouched.
    """
```

DEVI, in quest'ordine:
1. cambiare la firma e il corpo (il ciclo `for lang, payload in forms.items()`
   sostituisce `for lang, payload in (("it", it), ("en", en))`);
2. convertire le 93 registrazioni in `detection_lexicon_seed.py` con una
   trasformazione meccanica: `R("x", "phrases", it=A, en=B)` diventa
   `R("x", "phrases", forms={"it": A, "en": B})`;
3. rendere `SEED_LANGS` **derivata** invece che costante: le lingue seedate
   sono quelle effettivamente scritte dal seed.

NON DEVI: lasciare `it=`/`en=` come parametri di compatibilità. §7.1 —
niente shim in dev; una firma doppia significa due strade da mantenere e la
vecchia continuerà a essere usata.
NON DEVI: cambiare `_resolve`, `_union_langs`, `match()` in questo passo. B1
tocca SOLO la scrittura.

Verifica: `pytest tests/runtime/i18n -q` verde, e
`len(registered_concepts()) == 93` prima e dopo.

Per `SEED_LANGS` derivata, la forma esatta:

```python
def seed_langs() -> tuple[str, ...]:
    """Languages the seed actually wrote, in first-seen order.

    Derived, not declared: adding a language to the seed must not require
    editing a constant that can silently disagree with the data.
    """
```

DEVI: sostituire ogni uso di `SEED_LANGS` con `seed_langs()`.
ATTENZIONE: `_union_langs()` e `_startup_coverage_check()` la usano; cambiare
la fonte non deve cambiarne il risultato per un'istanza it/en. Un test lo
verifica: `seed_langs() == ("it", "en")` sul seed attuale.

### 13.2 (B2) Rendere osservabile l'unione fra lingue

Problema (analisi §8.2): `_union_langs()` unisce sempre
`{lingua corrente} ∪ {it, en}`. In un'istanza tedesca un concetto senza forme
tedesche continua a matchare via italiano/inglese. È voluto — copre i
comandi-prestito — ma **il degrado non si vede**.

DEVI: contare, per ogni match riuscito, **quale lingua ha fornito la forma
che ha matchato**.

Modello da copiare, non da inventare: `runtime/engine/guard_stats.py`
(contatore persistente, riepilogo notturno, verdetto umano). Stessa forma:

```python
# runtime/detection_lexicon_stats.py
def record_match(concept: str, lang_used: str, instance_lang: str) -> None:
    """Count one successful match by the language that supplied the form.

    A concept whose matches in a German instance come only from ``it`` is
    untranslated in practice, even though its row exists. That is the fact
    this counter makes measurable instead of anecdotal.
    """

def borrowed_only(instance_lang: str, min_matches: int = 20) -> list[str]:
    """Concepts that never matched via the instance language."""
```

DEVI: gate con variabile d'ambiente come `METNOS_GUARD_FIRE_COUNT`, acceso di
default, spegnibile.
DEVI: misurare il costo per match e scriverlo nel commit. Il precedente è
0,03 ms per piano: se qui costa di più, va discusso prima di accendere.
NON DEVI: cambiare il comportamento dell'unione. B2 misura, non decide.
NON DEVI: registrare il testo della query. Solo `(concetto, lingua, esito)`.

Verifica: in un'istanza it/en `borrowed_only("it")` è vuota (la lingua
d'istanza è anche quella delle forme).

### 13.3 (B3) Due esiti distinti: pendente vs non traducibile

Problema (analisi §8.3): `_startup_coverage_check()` accoda 93 concetti, il
daemon ne salta 25 perché `kind="regex"`, il boot successivo riaccoda gli
stessi 25, per sempre. Un meccanismo che non converge e lo dice ogni volta
con lo stesso testo è indistinguibile da uno rotto.

DEVI: introdurre due stati distinti al posto di uno.

| Stato | Significato | Dove si vede |
|---|---|---|
| `pending` | il daemon ci arriverà | log di boot, come oggi |
| `manual` | serve una persona | coda in `/admin`, MAI nel log di boot |

Implementazione esatta:
1. Colonna nuova `translation_state TEXT NOT NULL DEFAULT 'pending'` in
   `detection_lexicon` (migrazione additiva, come le altre di questo modulo).
2. `mark_for_translation` la imposta a `'manual'` quando
   `kind == "regex"`, a `'pending'` altrimenti.
3. `enqueue_language()` NON riaccoda le righe `manual`.
4. `verify_coverage` ritorna due elenchi separati: `missing_pending` e
   `missing_manual`.
5. `_startup_coverage_check` avvisa **solo** su `missing_pending`; le
   `manual` compaiono in `/admin` con il concetto e il motivo.

DEVI: dopo il refactor della parte A, i concetti `manual` scendono da 25 a
pochissimi. La distinzione resta comunque: è ciò che impedisce a un buco
permanente di travestirsi da coda.

Verifica: su istanza it/en nulla cambia (entrambe seedate, zero missing). Test
con una lingua finta: un concetto `regex` finisce in `manual` e NON viene
riaccodato al secondo boot.

---

# PARTE C — Le liste di parole cablate

Analisi che la motiva: `analysis_regex_i18n_16_8_2026.md` §9.
24 liste nel percorso della richiesta, 286 termini.

## 14. Una ricetta per forma, non una sola

Il principio, unico: **nel lessico va ciò che cambia con la lingua, e SOLO
quello.** Struttura, nomi di tool, tipo di match e comportamento di strip
restano nel codice e leggono le parole dal lessico.

### 14.1 Forma A — elenco piatto, match per sottostringa o parola

Esempio: `prefilter.py:765` (26 marcatori EXIF), `prefilter.py:792` (16
temporali).

```python
# prima
_EXIF_MARKERS = ("exif", "scattat", "metadati foto", "geotag", ...)
if any(m in norm for m in _EXIF_MARKERS): ...

# dopo — nel seed
R("photo.exif_intent", "phrases", match_mode="substring",
  forms={"it": [...], "en": [...]})
# dopo — al call-site
if _dl.match("photo.exif_intent", norm): ...
```

DEVI: conservare il `match_mode` che il codice usava. `in norm` è
`substring`; un confronto su token è `word`. Cambiarlo cambia il
comportamento.

### 14.2 Forma B — gruppi con comportamento diverso → `mapping`

Esempio: `target_device.py` distingue marcatori **adjunct** (si strippano
dalla query) da **nominal** (non si strippano: strippare demoliva la
semantica, bug del 9/7).

```python
R("device.server_marker", "mapping", match_mode="substring",
  forms={"it": {"adjunct": ["sul server", "lato server", ...],
                "nominal": ["del server", "il server", ...]},
         "en": {"adjunct": ["on the server", "server side"],
                "nominal": ["of the server", "this server", "the server"]}})
```

Al call-site: `mapping("device.server_marker")["adjunct"]`.

DEVI: usare `mapping` ogni volta che l'appartenenza a un gruppo decide un
comportamento. NON DEVI: appiattire due gruppi in un elenco solo e
ricostruire la distinzione con un `if` sul testo.

### 14.3 Forma C — indice a lookup ESATTO (`fast_path`)

È quella che si sbaglia. `fast_path` non fa un match: fa
`_PATTERN_INDEX[norm]`, uguaglianza esatta su query normalizzata, e lo fa
apposta («niente regex, niente fuzzy») per non rubare query al planner.

DEVI: cambiare la **fonte** dei termini, NON il tipo di match.

```python
# prima
_TIME_PATTERNS = ("che ora e", "che ore sono", ...)

# dopo
def _time_patterns() -> tuple[str, ...]:
    return tuple(_dl.forms("fastpath.time_question"))
```

DEVI: ricostruire `_PATTERN_INDEX` quando il lessico cambia. L'indice oggi si
costruisce a import-time; con la fonte nel lessico va costruito pigramente e
invalidato insieme al lessico, con lo stesso `_invalidate`.
NON DEVI: sostituire il lookup esatto con `match()`. Trasformerebbe
un'uguaglianza in un contenimento e `fast_path` comincerebbe a catturare
query che appartengono al planner — esattamente il difetto del gate Tutor
risolto il 16/8.

Verifica obbligatoria: le stesse query del corpus reale che oggi prendono il
fast path devono prenderlo dopo, e nessuna in più. Test §9.5.

### 14.4 Forma D — frammento di regex interpolato

Esempio: `target_device.py::_PREP_NOMINAL`, un'alternanza di articoli e
preposizioni IT+EN incollata dentro una regex più grande.

DEVI: diventa uno **slot** di un `template` (parte A). Il frammento non
esiste più come costante: il pattern che lo usava diventa il template.

### 14.5 Forma E — parole accoppiate a struttura

Esempio: `prefilter.py::_QUERY_DEPENDENT_PRECURSORS` associa
`find_places → get_location` quando la query è location-relativa, e porta con
sé 33 marcatori.

DEVI: separare. I due nomi di tool restano nel codice (non sono lingua e non
si traducono); i 33 marcatori vanno nel lessico.

```python
_QUERY_DEPENDENT_PRECURSORS = (
    ("find_places", "get_location", "geo.self_proximity"),   # concetto, non termini
)
...
if _dl.match(concept, norm): ...
```

NON DEVI: mettere nel dizionario multilingua nomi di executor.

### 14.6 Il caso della prossimità: tre elenchi, uno solo

Applicando la ricetta, `prefilter.py:741`, la guardia
`ensure_proximity_center` e l'affinity di `find_places` leggono tutti da
`geo.self_proximity`.

DEVI: unificare i primi due.
NON DEVI: toccare l'affinity del manifest in questo lavoro. Vive in un
manifest firmato e si cambia SOLO con una misura di prefilter (regola di
Roberto, `scripts/bench_prefilter_corpus.py`). Resta duplicata e va scritta
come debito residuo nel commit — non risolta di soppiatto.

## 15. Ordine della parte C

Per meccanicità decrescente: prima A e B (sostituzione diretta), poi C
(`fast_path`: cambia fonte, non semantica — richiede il test di equivalenza
sul corpus), poi D (dipende dalla parte A) e infine E.

DEVI: una lista per commit, con il suo test di equivalenza §9.5.

## 16. Che cosa NON entra nel lessico

Per chiudere il conteggio: queste liste **non** sono debito e non si toccano.

- `runtime/testing/populate_cases.py` (55): sono casi di prova, il loro testo
  è il dato in esame.
- `runtime/ui_surfaces.py` (14): etichette d'interfaccia, già per-lingua per
  costruzione, con l'autorità del registro delle superfici.
- `manifest_lint.py`, `prompts_lint.py`, `smoke.py`, `progress.py`:
  controllano il NOSTRO testo, non la richiesta dell'utente. Restano italiani
  perché il corpus che controllano è italiano.
- Elenchi di verbi/oggetti canonici (`vocab.py`): sono il vocabolario chiuso
  in inglese canonico, non forme di superficie.

---

## 17. Che cosa NON fare — vale per tutte e tre le parti

- NON DEVI creare un modulo nuovo, con una sola eccezione dichiarata:
  `detection_lexicon_stats.py` della parte B2. Tutto il resto sta in
  `detection_lexicon.py`, nel suo seed e nel suo daemon.
- NON DEVI cambiare la firma di `match()`, `search()`, `forms()`,
  `mapping()`, `regexes()`: i 93 concetti devono continuare a funzionare
  senza toccare un call-site. (`register()` invece CAMBIA: è la parte B1, ed
  è di scrittura, non di lettura.)
- NON DEVI convertire i 187 pattern tecnici (§6.4).
- NON DEVI introdurre normalizzazione Unicode (§5.5).
- NON DEVI cancellare `kind="regex"`.
- NON DEVI trasformare un lookup esatto in un match per contenimento
  (§14.3): è il modo più facile di far rubare a `fast_path` query che
  appartengono al planner.
- NON DEVI toccare l'affinity dei manifest firmati (§14.6): si cambia solo
  con una misura di prefilter.
- NON DEVI mettere nel dizionario multilingua nomi di executor, chiavi
  canoniche o verbi del vocabolario chiuso: non sono lingua e non si
  traducono.

---

## 18. Ordine complessivo e criterio di fine

| Ordine | Parte | Perché in questa posizione |
|---|---|---|
| 1 | **B1** — `register()` a dizionario di lingue | tocca la firma con cui si registrano i concetti: farlo dopo significa riscrivere due volte gli stessi 93 |
| 2 | **A** §1-§4 — motore dei template | non migra nulla, aggiunge solo la capacità |
| 3 | **A** §8.3 — migrazione dei concetti, a partire da `confirm.yes`/`confirm.no` | senza quelli, in una lingua nuova l'utente non può confermare niente |
| 4 | **B3** — pendente vs non traducibile | ha senso dopo la parte A, quando i `manual` sono pochi e veri |
| 5 | **C** §15 — le 24 liste, per forma | dipende dal motore (forma D) e dalla firma (tutte) |
| 6 | **B2** — unione osservabile | misura, e si misura ciò che è finito |
| 7 | **A** §9.6 — guardia anti-ricomparsa | ultima: prima si pulisce, poi si chiude la porta |

**Criterio di fine**, verificabile e non opinabile:

1. `pytest tests/runtime -q` verde;
2. l'inventario di `analysis_regex_i18n_16_8_2026.md` rieseguito
   (`scratchpad/sweep2.py`) dà **0** regex di lingua e **0** liste di termini
   nel percorso della richiesta, fuori dalle eccezioni dichiarate in §16;
3. `verify_coverage("xx")` per una lingua finta distingue `missing_pending` da
   `missing_manual`, e il secondo elenco è **breve e giustificato**, concetto
   per concetto.

DEVI: riportare i tre numeri nel commit finale. Un refactor i18n che non
finisce con un conteggio è un refactor di cui nessuno sa se è finito.
