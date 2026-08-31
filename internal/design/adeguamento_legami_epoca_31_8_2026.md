# F4-EPOCA-01 · B1 — classificazione e adeguamento dei 12 legami vivi

> Agente B, 31 agosto 2026. Ancora: `80b5037c`.
> Perimetro: classificazione dei 12 legami di O15 e disegno del loro
> adeguamento. Il nucleo del protocollo di transizione appartiene all'agente A;
> questo documento non lo definisce e non lo anticipa.
>
> Fonte dei legami: `internal/design/diagnosi_avvio_nascita_31_8_2026.md`, O15.
> Strumento: `internal/tools/classifica_legami_epoca.py` (prove in
> `internal/tools/prova_classifica_legami_epoca.py`).
> Nessuna modifica al sistema in funzione: tutto qui è lettura.

## 1. Esito in cinque righe

I 12 legami vivi sono **sei fatti sotto due rappresentazioni**: sei nascite
concluse il 30 agosto, ciascuna registrata due volte — come ricevuta di
ammissione nel negozio dei contratti e come ricevuta di produttore nello stato
di nascita. Tutti e dodici vanno classificati **`epoca_storica`**: restano
legati all'epoca sotto cui sono avvenuti. **Zero ripuntamenti**, e non perché
sia comodo: perché ripuntarli sarebbe falso.

## 2. Che cosa sono, misurato

Il classificatore acquisisce il contesto corrente dalla radice di nascita e
attribuisce ogni legame per **regola chiusa**. Sul dato reale:

```
contesto corrente: sha256:f90abe9d64282390ae518b88e4bc9d879ca982e89c1c4b3fe73ed24c6a47f3ec
legami esaminati : 12
buste non interpretabili e non attribuibili a questo contesto: 2

epoca_storica              12
nuova_epoca                 0
cessa_di_essere_corrente    0
non_classificato            0
```

I sei contratti coinvolti sono tutti builtin: `write_entries`,
`read_tasks_history`, `set_skills`, `start_lre`, `set_tasks`,
`set_preferences`.

### Il fatto che decide la classificazione

Ogni ricevuta copre una generazione che **non è più corrente**. E non lo è per
un motivo preciso, che la ricevuta stessa registra: la nascita aveva reso
corrente quella generazione, e oggi il puntatore è tornato su quella
precedente.

| contratto | resa corrente dalla nascita | corrente oggi | esito |
|---|---|---|---|
| `read_tasks_history` | `b3683edf…` | `65f50c70…` | ritornata alla precedente |
| `set_preferences` | `2bc4deb8…` | `cc4058ed…` | ritornata alla precedente |
| `set_skills` | `7ca8c99f…` | `51b488b6…` | ritornata alla precedente |
| `set_tasks` | `bba4b66d…` | `d6504964…` | ritornata alla precedente |
| `start_lre` | `ac969abb…` | `2daef102…` | ritornata alla precedente |
| `write_entries` | `418f0ea4…` | `ac6bc2e9…` | ritornata alla precedente |

Sei su sei. **L'effetto di quelle nascite è stato annullato**: i dodici legami
attestano atti conclusi il cui risultato non è più in vigore.

### Un fatto trasversale che il perimetro A deve conoscere

Misurato sull'intero negozio, non solo sui sei:

```
pubblicazioni totali                        : 123
con almeno una ricevuta di ammissione       :  21
la cui GENERAZIONE CORRENTE ha una ricevuta :   0
```

**Nessun contratto oggi in vigore è stato ammesso dalla porta di nascita.** Le
21 ricevute esistenti (su due contesti distinti) coprono tutte generazioni
superate. È un fatto sul dominio, non una proposta: lo consegno al perimetro A
perché tocca la condizione d'ingresso della transizione, che non è mia.

## 3. La regola di classificazione

Tre futuri ammessi e un rifiuto. La regola è chiusa e non ha un valore di
difetto: ciò che non sa decidere **blocca**.

| classe | condizione | che cosa si fa |
|---|---|---|
| `epoca_storica` | la generazione coperta non è corrente | **niente**: il legame resta com'è |
| `nuova_epoca` | la generazione coperta È corrente | va **riattestata** sotto la nuova epoca prima della transizione |
| `cessa_di_essere_corrente` | la generazione coperta è corrente ma il contratto è in ritiro | la generazione smette di essere corrente; il legame non si riscrive |
| `non_classificato` | qualunque altro caso | **F4 non può essere dichiarata** |

Il rifiuto non è una cortesia. Una dipendenza non classificata è esattamente il
caso in cui dichiarare F4 significherebbe affermare qualcosa che nessuno ha
verificato, ed è per questo che ha un codice d'uscita proprio (`2`).

Una ricevuta di produttore **non è un fatto indipendente**: è il lato nascita
dello stesso commit di cui la ricevuta di ammissione è il lato contratto. Il suo
futuro è quindi quello di quel commit, e lo strumento lo deriva dal gemello
invece di inventare una seconda regola. Se il gemello non si trova, il legame
resta `non_classificato` — non viene indovinato.

## 4. Perché nessun ripuntamento è lecito (la prova richiesta)

La domanda posta a B1 è se un ripuntamento meccanico possa ridurre le guardie
esistenti. La risposta è sì, e per tre ragioni distinte e verificabili.

**(a) Riscriverebbe una provenienza.** Una ricevuta di ammissione dichiara sotto
quale contesto i sette controlli sono stati eseguiti — `approval`,
`authoring_install_journal_v1`, `dependency_closure`, `manifest_lint`,
`manifest_standard`, `properties`, `semantic_review`. Cambiarle il contesto
affermerebbe che quei controlli sono avvenuti sotto la nuova epoca, quando sono
avvenuti sotto la vecchia. Non è una migrazione: è la fabbricazione di una
provenienza. Il rapporto del gruppo 2 lo dice già in un altro punto — «una
differenza dopo l'installazione è un errore, non un invito a riprovare con altri
byte» (§8.2).

**(b) Sarebbe una sostituzione, che il disegno vieta.** Le ricevute vivono nel
negozio dei contratti come documenti finali. §8.1 del rapporto del gruppo 2:
«Non esiste sostituzione di una destinazione finale». Un ripuntamento è
esattamente una sostituzione.

**(c) Non servirebbe a nulla, ed è la ragione che chiude la questione.** Le sei
nascite sono state annullate: nessuna delle generazioni che hanno ammesso è
oggi corrente. Ripuntarle collegherebbe la nuova epoca a sei atti il cui effetto
non esiste più. Si pagherebbe il costo di una falsificazione per un beneficio
nullo.

Il verso opposto vale altrettanto: **conservare** i dodici legami non riduce
nessuna guardia. Sono terminali (`committed`, scaduti il 30 agosto alle 12:17),
non sono consumabili una seconda volta, e non sostengono alcuna generazione in
vigore. Restano ciò che devono essere: la registrazione di che cosa è successo.

## 5. Adeguamento: che cosa fare, per ciascuno dei 12

| # | legame | classe | azione |
|---|---|---|---|
| 1-6 | ricevute di ammissione dei sei contratti, in `contract-publications/v1/*/admission-receipts/` | `epoca_storica` | nessuna: restano immutate |
| 7-12 | righe `birth_producer_receipts` 18-23 in `birth/producer_receipts.sqlite` | `epoca_storica` | nessuna: restano immutate |

**L'adeguamento dei 12 legami è quindi: non toccarli.** Il lavoro non è
spostarli, è **provare** che non vanno spostati — e renderlo verificabile a
ogni esecuzione, invece che deciso una volta a mano.

Il che sposta la domanda dove deve stare: non «come adeguo i dodici legami», ma
**«come verifico, prima di ogni transizione, che non esista un legame che
richiede un'azione»**. Il classificatore è quella verifica, e ha un codice
d'uscita per ciascuna risposta:

```
0  ogni legame resta storico: nessuna azione prima di F4
2  almeno un legame non classificato: F4 NON dichiarabile
3  classificazione completa, ma qualche legame richiede un'azione
1  lo strumento non ha potuto girare
```

## 6. Prove

`internal/tools/prova_classifica_legami_epoca.py`, otto casi su fixture: zero
dipendenze; una su generazione superata; una sulla generazione corrente; un
contratto in ritiro; molte dipendenze miste con classi diverse nello stesso
giro; ricevuta di produttore senza gemello, che deve **bloccare**; busta
illeggibile, che deve bloccare invece di essere ignorata; contesto diverso, che
non deve essere raccolto.

**Un difetto trovato dalle prove, e vale la pena registrarlo.** La prima
versione dello strumento attribuiva una ricevuta di produttore al contesto
cercandone il testo nei byte grezzi della busta. Sul dato reale funzionava — ma
per caso, perché altri campi ripetono il contesto in chiaro, mentre la ricevuta
vera è in base64 e quel testo lì non c'è. Su una fixture minima il filtro non
trovava nulla e sei legami sparivano. Ora l'autorità è la ricevuta decodificata,
il confronto letterale resta solo come rete per una busta che non si riesce a
interpretare, e le buste non interpretabili né attribuibili vengono **contate e
dichiarate** invece di essere scartate in silenzio (sul dato reale ce ne sono 2,
le due ricevute `rejected`).

## 7. Che cosa questo documento NON stabilisce

- Il protocollo della nuova epoca: stati, proprietario normativo, ingresso,
  ripresa, ripetibilità, conservazione della precedente e ritorno controllato.
  È del perimetro A.
- Se e quando la transizione debba avvenire.
- Che cosa fare delle 21 ricevute complessive sugli **altri** contesti: la
  regola le classificherebbe allo stesso modo, ma non le ho misurate una per
  una e non le dichiaro.

---

## 8. Secondo giro — le quattro garanzie chieste dall'agente A

Revisione `MODIFICHE_RICHIESTE` di A su `937ba594`
(`internal/design/revisione_a_adeguamento_legami_epoca_31_8_2026.md`). Tutti e
quattro i rilievi sono accolti. **Il disegno dei tre futuri e la conclusione sui
12 oggetti non cambiano**, come A richiedeva: cambia ciò su cui poggiano.

**R1 — identità composta.** L'indice del gemello era la sola generazione, mentre
il contratto F4 usa già `(contract_id, generation_id)`. Due contratti con lo
stesso digest potevano prendersi il gemello a vicenda. Ora la chiave è la coppia,
e l'identità viene confrontata in **tutti e tre i posti** in cui compare —
percorso, documento e busta: una divergenza è un rifiuto, non qualcosa da
riconciliare preferendo una fonte.

**R2 — nessuna autorità dal chiamante.** `--ritirati` è stato **rimosso**. Il
ritiro proviene ora dalla lapide autenticata del contratto
(`ContractRetirement`), che è una decisione autorevole e non un dato
diagnostico. Una prova verifica anche che l'argomento non sia più accettato.

**R3 — fatti autenticati.** Inventario e generazione corrente arrivano dalle
primitive produttive del negozio — `inventory_store_manifests` e
`current_contract` — invece che da letture dirette di `binding.json` e
`current`. Sul dato reale: **122 contratti su 122 autenticati**, con la radice
d'installazione reale. Un contratto il cui stato corrente non è autenticabile
non riceve una classe: **blocca**.

> **Limite dichiarato, non taciuto.** La firma delle ricevute di ammissione non
> viene verificata: il verificatore appartiene all'autorità di nascita
> sigillata, che durante la transizione non è attiva. Lo strumento lo stampa
> a ogni esecuzione, invece di lasciar credere di aver verificato più di quanto
> abbia verificato.

**R4 — regola terminale chiusa.** Le due buste reali non erano illeggibili:
sono righe `rejected` con `admission_receipt` a `null` e un codice di rifiuto —
rifiuti conclusi correttamente, che non hanno ammesso niente e quindi **non sono
dipendenze**. Ora sono escluse per stato e forma, contate a parte e nominate col
loro codice. Al contrario, una conclusione `committed` con busta invalida che
non ripeta il contesto in chiaro **prima passava in silenzio**: oggi blocca. E un
rifiuto senza codice — cioè senza una ragione — blocca anch'esso.

### Esito reale dopo le quattro correzioni

```
contratti autenticati dall'inventario produttivo: 122
legami esaminati  : 12
rifiuti terminali validi (non sono legami): 2

epoca_storica              12
nuova_epoca                 0
cessa_di_essere_corrente    0
non_classificato            0
```

Undici prove mirate, tutte verdi, fra cui le cinque che A chiedeva per nome:
due contratti con lo stesso digest che non condividono il gemello; identità
discorde fra percorso e documento; stato corrente non autenticabile; rifiuto
terminale valido; conclusione invalida senza testo del contesto.

**La conclusione regge, e ora regge su qualcosa.** Prima era riproducibile sui
byte osservati; adesso poggia su inventario e generazioni autenticati, su
un'identità composta confrontata in tre punti, su un'autorità che il chiamante
non può fabbricare e su una regola terminale senza casi ignorati.
