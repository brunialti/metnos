# Prompt per codex

Due usi distinti:

- **Avanzamento** (sotto): far eseguire a codex il passo successivo, con Claude
  ancora attivo come revisore.
- **Subentro** (in fondo, §Subentro): Claude non è più disponibile e codex prende
  anche il suo ruolo.

---

# Prompt di avanzamento per codex

Da usare quando serve far proseguire il lavoro senza una sessione Claude attiva.
Lanciare da `/opt/metnos`:

```bash
codex exec -s workspace-write \
  -c sandbox_workspace_write.network_access=true \
  -c approval_policy='never' \
  -c model_reasoning_effort='high' \
  -C /opt/metnos "$(cat internal/design/prompt_avanzamento_codex.md)"
```

---

## Istruzione

Prosegui il lavoro Metnos sull'estrazione di intento, eseguendo il **primo passo
non ancora completato** della sequenza concordata.

**Leggi prima, in quest'ordine:**

1. `internal/design/sintesi_soluzione_intento.md`, sezione finale **SINTESI
   OPERATIVA**: contiene i sette passi concordati, i vincoli non negoziabili e
   ciò che non si fa. È firmata da entrambi gli agenti; non riaprire il dibattito.
2. `internal/design/handover_prompt_ontologia_11_8_2026.md`: stato completo,
   contratto del validatore, tutte le misure e la disciplina di misura.

**Identifica il primo passo non completato**, guardando quali referti esistono
già in `internal/design/` e quali risultati esistono in
`internal/tools/request_analysis_lab/misure_11_8/`. Poi eseguilo per intero.

**Regole di lavoro:**

- Sola lettura sulla produzione: nessuna scrittura su store reali, nessun riavvio
  di servizio, nessuna modifica al runtime, **nessun commit**.
- Non modificare mai `unified_query_bench_v23_checkpoint.py`: le toppe
  sostituiscono attributi del modulo a runtime e devono **fallire rumorosamente**
  se non trovano il testo atteso.
- **Una sola misura per volta sulla GPU.** Verifica che nulla stia girando prima
  di partire. Serve `llama-server` vivo su `127.0.0.1:8080`.
- Ogni configurazione si misura con il suo **controllo eseguito di fianco**, sulle
  stesse 120 richieste e nello stesso ordine. Un risultato memorizzato da una
  corsa precedente non è un controllo.
- **Banda di rumore ±1 su 120.** Sotto 3 casi non si emette verdetto.
- Mai `pgrep -f` o `pkill -f` con un modello che compare nella riga di comando che
  lo contiene: si autocorrisponde. Filtra per processo con `pgrep -x python3` e
  `/proc/<pid>/cmdline`.
- Le regressioni su undo, consenso, negazione e rami condizionali si contano in
  una colonna separata e **non si compensano** con un guadagno medio.
- Commenti e docstring del codice in inglese; documenti e referti in italiano.

**Al termine:**

- Scrivi un referto in `internal/design/` con i numeri, il metodo e i limiti.
- Aggiorna `internal/design/handover_prompt_ontologia_11_8_2026.md` con l'esito e
  con qual è il passo successivo.
- Dichiara esplicitamente che cosa resta non verificato.

Se un passo richiede un giudizio che la sintesi non ha già concordato, **fermati
e scrivilo nel referto** invece di deciderlo da solo: la regola posta da Roberto
è che nessuno dei due agenti decide da solo.

---

# Subentro — frase di passaggio da incollare in CLI

Da usare quando Claude non è più disponibile e codex prende anche il suo ruolo.

> Subentri a Claude nel lavoro Metnos sull'estrazione di intento: da ora fai
> entrambi i ruoli. Leggi in quest'ordine la sezione SINTESI OPERATIVA di
> `internal/design/sintesi_soluzione_intento.md` (sette passi concordati, vincoli
> non negoziabili, cosa non si fa — firmata da entrambi, non riaprirla), poi
> `internal/design/handover_prompt_ontologia_11_8_2026.md` per stato, contratto
> del validatore, misure e disciplina, poi la sezione «Prompt di avanzamento» di
> `internal/design/prompt_avanzamento_codex.md` per le regole di lavoro. Esegui
> il primo passo non ancora completato, capendo da solo qual è dai referti già
> presenti. **Poiché sei solo, dopo ogni lavoro attacca il tuo stesso risultato
> come farebbe un revisore avversariale** — verifica i tuoi numeri rieseguendo i
> conteggi, cerca fatti persi o aggiunti, cerca ciò che non hai detto e avresti
> dovuto — e scrivi quell'attacco nel referto insieme alle tue risposte. In ogni
> referto dichiara che l'accordo è ricostruito da una parte sola, quindi vale
> come proposta a Roberto e non come accordo fra due agenti. Vincoli invariati:
> sola lettura sulla produzione, nessun commit, una sola misura per volta sulla
> GPU con il controllo eseguito di fianco, banda di rumore ±1 su 120, non
> modificare mai `unified_query_bench_v23_checkpoint.py`, regressioni su undo,
> consenso, negazione e rami condizionali in colonna separata e mai compensate.
> Aggiorna la consegna a ogni passo. Su qualunque giudizio che la sintesi non
> abbia già concordato fermati e scrivilo, non deciderlo da solo.
