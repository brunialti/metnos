# RM-0008 — la porta vecchia si chiude: le tre risposte

Data: 1 settembre 2026 · Agente B · **risposte tecniche + una proposta per §7.10**

Roberto ha delegato la soluzione. Le prime due domande sono tecniche e le
rispondo con quello che ho misurato nel codice. La terza tocca il
`CLAUDE.md` invariante, che per governance modifica solo lui: la lascio come
**proposta di testo**, non come modifica.

## Il fatto da cui si parte

`closed_build_enforcement()` e' `True`. Misurato:

    firma  «sign_executor»      NEGATA   birth_ownership_legacy_api_closed
    firma  «publish_executor»   NEGATA   birth_ownership_legacy_api_closed
    negozio produttivo          NEGATO
    negozio isolato             permesso

`python3 runtime/sign.py publish executors/<nome>` non parte piu'. E' il
comando che il §7.10 dichiara obbligatorio dopo ogni modifica a un executor
o a un manifest.

## 1. Cosa sostituisce `sign.py publish`

**`deploy --executor <nome> --sign`**, e la porta esiste gia'.

La prima stesura di questo documento diceva che nessun produttore accettava
un executor modificato a mano, e ne traeva una scelta di prodotto fra
aggiungere una capacita' d'autore o congelare gli 87 contratti di
`executors/`. **Era sbagliato**, e l'errore era di metodo: avevo cercato la
stringa `executors/` nei moduli il cui nome contiene «birth» e nel promotore.
La porta sta in `runtime/stack_reconcile.py`, che «birth» nel nome non ce l'ha.

La catena, verificata riga per riga e poi eseguita:

    runtime/stack_reconcile.py:454  verify_named_executors(..., sign_first=True)
      risolve un figlio diretto di `executors/`, pretende il manifest,
      copia in una staging temporanea e chiama
        submit_stack_reconcile_birth(BirthIntent(
            candidate_source_root=staging,
            contract_id=ContractId(ManifestOrigin.CORE, f"{name}/manifest.toml")))
      alzando `birth_admission_failed` se la nascita rifiuta;
    runtime/stack_reconcile.py:1027  `deploy --executor <nome> --sign`,
      con «--sign requires at least one --executor»;
    runtime/executor_birth_intent.py:60  la capacita' e'
      `stack_reconcile / restart_sign_first`, gia' fra le porte chiuse.

Eseguita su radici utente isolate:

    verify_named_executors(["find_files"], sign_first=True)
    -> birth_unavailable / birth_runtime_bundle_unavailable

Il percorso ARRIVA alla nascita e si ferma solo perche' quella radice non ha
l'autorita' preparata: e' un ambiente senza autorita', non una porta mancante.

**Non serve una dodicesima capacita' e non c'e' nessuna decisione di prodotto
da prendere.** Gli 87 contratti scritti a mano hanno la loro porta.

## 2. Cosa garantisce che la porta nuova sia aperta prima che la vecchia si chiuda

**Una garanzia c'e', ma copre l'altro guasto.**

`runtime/metnos_http_server.py:366` chiama
`require_birth_runtime_before_workers()` prima di ogni lavoratore che muta.
Il suo docstring registra l'incidente del 31 agosto: rifiutare l'avvio dove
l'insieme preparato non esiste rese il servizio non avviabile. Quindi oggi:

- insieme preparato **assente** -> stato dichiarato `prepared_not_active`,
  il server parte lo stesso;
- insieme preparato **presente** -> ogni fallimento di attivazione resta
  fatale, il server non parte.

Cioe': si e' protetti da un'autorita' di nascita ROTTA, non da una porta
vecchia chiusa senza porta nuova. Nel primo caso il server parte, e in quel
tratto `sign.py publish` e' negato mentre nessuna porta nuova esiste.

**La risposta operativa e' l'ordine, e non richiede codice nuovo:**

1. preparare l'insieme e completare la transizione, con la porta vecchia
   ancora aperta;
2. verificare che il produttore scelto al punto 1 pubblichi davvero un
   contratto attraverso il confine F4, su una copia;
3. solo allora portare in produzione il codice con il bit chiuso.

E' lo stesso ordine che la variazione 02 impone ai servizi — «prima si porta
l'autorita' a uno stato attivabile, poi si fonde il codice che la pretende»,
scritto nella consegna del 31 agosto a spese di un'interruzione.

Se si vuole una garanzia meccanica invece che procedurale, la forma minima
e': il bit chiuso non e' un letterale, ma una lettura del fatto che almeno
un contratto sia stato ammesso attraverso il confine F4. Cosi' la porta
vecchia non puo' chiudersi prima che la nuova abbia funzionato almeno una
volta. Non lo propongo come lavoro: lo scrivo perche' la scelta fra
procedura e meccanismo sia esplicita.

## 3. Proposta di testo per §7.10 — da valutare, non applicata

Il §7.10 oggi dice:

> Edit di `<executor>.py` O del solo `manifest.toml` → OBBLIGATORIO
> `python3 runtime/sign.py publish executors/<name>` + riavvio controllato.

Il testo proposto, nell'ipotesi (b):

> ### 7.10 Pubblicazione executor dopo edit
>
> Un executor nasce e rinasce SOLO attraverso il confine di nascita.
> `runtime/sign.py` non pubblica piu': in una distribuzione chiusa firma e
> pubblicazione sono negate, e il diniego precede ogni altro controllo.
>
> DEVI: dopo un edit di `executors/<nome>/`, ripubblicare con
> `python3 runtime/stack_reconcile.py deploy --executor <nome> --sign`.
> NON DEVI: usare `runtime/sign.py`, che in una distribuzione chiusa nega
> firma e pubblicazione prima di ogni altro controllo.
> OK: modifico `find_files`, eseguo `deploy --executor find_files --sign`,
> la ricevuta di nascita compare nel negozio.
> ERRORE: `sign.py publish executors/find_files` — nega, e non e' un guasto
> da aggirare.

(I builtin in-process restano a
`scripts/generate_builtin_executor_contracts.py --sign`, l'estensione di un
manifest a `change_applier`, il sintetizzato al promotore: sono altri
produttori, non alternative a questo.)

**Non ho toccato il `CLAUDE.md`.** La governance dice che l'invariante lo
modifica solo Roberto, o l'agente su sua istruzione esplicita e puntuale, e
che una regola contraddetta dal codice si segnala con una proposta. Questa
e' la proposta.

## Cosa resta da decidere, e da chi

**Niente, sul merito.** La decisione congiunta di A e B e' che non c'era una
scelta da prendere: la porta esiste, il comando esiste, gli 87 contratti non
sono in pericolo.

Resta un solo atto, ed e' di Roberto: incollare il §7.10 qui sopra nel
`CLAUDE.md` invariante, che per governance modifica solo lui.

Per il dispiegamento vale il punto 2: l'ordine e' definito e non richiede
codice.
