# Lessons Learned — prompt engineering, determinismo LLM, backend multipli

> Doc interno canonico (estensibile). Raccoglie le lezioni durature emerse dal
> lavoro reale. Aggiungere in coda con data. Le lezioni promosse a norma vivono
> in `CLAUDE.md`/ADR; qui resta la spiegazione + l'evidenza.
>
> Tier modello di riferimento: **Gemma 4 26B (middle/wise locale)** — NON un
> frontier. L'instruction-following è più debole: i segnali strutturati pesano
> più della prosa.

---

## A. Prompt engineering & bias del modello (31/5/2026)

**A1 — pattern > discorsivo.** Un prompt pattern-oriented (schema, `enum`,
esempi strutturati, token `[X]`) è PIÙ FORTE di uno discorsivo/colloquiale.
Coerente con §6/§6.1 (prescriptive) e con la convenzione "prompt usa pattern
formali, non esempi narrativi".

**A2 — niente contraddizioni interne.** Verificando un prompt, controllare che
non si contraddica. Anti-pattern ricorrente: **correzione AGGIUNTA senza
RIMUOVERE l'originale** → segnali in competizione. Il modello non ignora il
prompt: segue il segnale più concreto/precoce.
- Evidenza: manifest `create_events` conteneva insieme "(default `local`)" e
  "OMETTI `client`". Il modello sceglieva `local`.

**A3 — bias colloquiale→pattern (il più sottile).** Se una situazione DUBBIA è
descritta in parte in modo colloquiale e in parte pattern-oriented, il modello
ha un BIAS verso il pattern — anche quando l'alternativa colloquiale era quella
corretta nel caso in esame.
- Evidenza doppia (stessa call `create_events`, 31/5):
  - `client="local"` ← l'`enum={local,google_workspace}` esposto dal proposer
    (`proposer.py::_tools_block`, "§8.3 anti-invenzione") batte il colloquiale
    "OMETTI".
  - `start="now_plus_1dT16:00:00"` ← il pattern del vocab placeholder
    `${RUNTIME:now_plus_Nd}` (in `engine_proposer.j2`) batte il colloquiale
    "domani alle 16" → stringa non-ISO → `create` ok=false.

**Corollario operativo (A4).** La riduzione di prompt più potente NON è
riformulare: è **togliere all'LLM le decisioni che non gli competono**. Dove
serve garanzia, rete deterministica nel runtime, non istruzioni all'LLM
(lezione trasversale confermata più volte: client=local, list_processes
allucinato, get_inputs over-asking).

**Come applicare.**
- DEVI: rimuovere il PATTERN che induce il bias (es. l'`enum` di un arg di
  configurazione), non solo correggere la prosa.
- DEVI: per gli arg che sono CONFIGURAZIONE non INTENTO, marcarli
  `runtime_resolved` così il proposer non li vede → li risolve il runtime.
- NON DEVI: contrastare un enum/pattern con una nota colloquiale ("OMETTI X").
  Perde.

---

## B. Backend multipli — problema GENERALE (31/5/2026)

**B1 — la selezione del backend NON è intento, è configurazione.** Quale
provider serve una richiesta (locale vs Google vs account IMAP X) dipende da
cosa è autenticato/configurato, non da una decisione "intelligente". Chiederlo
all'LLM è un errore di categoria: ogni arg `client`/`account`/`provider` è un
punto dove l'LLM indovina male (vedi A3).

**B2 — il problema si ripropone OVUNQUE esistano backend multipli** (insight
Roberto 31/5). Non è calendar-specifico. Catalogo dei casi a rischio:

| OBJECT | arg di backend | provider/i | rischio |
|--------|----------------|------------|---------|
| events | `client` | local_ics / google_workspace | **ATTIVO** (calendar #8) |
| messages | `account` | metnos_system/_roberto/mykleos/knowcastle/tiscali / `all` | enum lista → bias di scelta |
| contacts/persons | (provider) | local / google_workspace | ADR 0137 |
| files/dirs | mount/path | local / CIFS-SMB | ADR 0087 |
| github | provider | first-party (ADR 0141) | provider qualifier |
| qualunque `_<provider>` | suffix qualifier | ADR 0136 | filtro pool grammar |

**B3 — un backend dev'essere BEN DEFINITO.** Anti-pattern osservato: `local_ics`
aveva commenti "stub" + manifest "stub not_implemented" mentre create/read/delete
erano TUTTI implementati (documentation rot da edit incrementali). Se non si
riesce a dire dai sorgenti se un backend è stub o reale, è un problema generale:
ogni provider deve dichiarare in modo univoco *sono disponibile?* (creds).

**B4 — il resolver-runtime è la GARANZIA, non il prompt-hiding** (verificato 1/6).
Marcare l'arg `runtime_resolved` lo nasconde dall'`enum` esposto, MA il proposer
mostra solo la PRIMA FRASE della description (`proposer.py:_tools_block`,
`desc.split(".")[0]`) e l'LLM emette comunque `client` da TRAINING (sa che i
tool calendar hanno un backend). Quindi nascondere non basta: **il RUNTIME deve
SOVRASCRIVERE** l'arg al dispatch (override incondizionato). Verificato: LLM
emette `client='local'` + query senza "locale" → resolver corregge a
`google_workspace`. Il roundtrip create→delete-by-id (deleted=True) prova la
coerenza. Corollario test: verifica l'ESITO (backend coerente), non il dettaglio
interno (se l'LLM ha emesso l'arg).

**Direzione (ADR da aprire).** Resolver backend UNIFORME e deterministico:
1. `client`/`account`/`provider` spariscono dagli args visibili all'LLM
   (flag `runtime_resolved` saltato da `proposer.py::_tools_block`).
2. Il runtime risolve il backend: provider esplicitamente nominato dall'utente
   ("sul calendario locale", "dalla casella tiscali") → quello; altrimenti il
   default per-object basato su creds (`_default_client()` generalizzato).
3. Rispetta ADR 0155 correttamente: il backend non è scelta del planner →
   farglielo scegliere era il bug; il runtime che lo risolve è il proprietario
   giusto, non un override interceptor.
4. Elimina i `_default_client()` ad-hoc duplicati per-executor.

---

## C. Onestà del layer finale (§2.8) (31/5/2026)

**C1 — il final_message deve riflettere il RESULT, non il claim del proposer.**
- `delete_events` su id inesistente: executor onesto (`not_found`) ma il proposer
  scriveva "è stato cancellato". Fix: rete `_enforce_mutating_honesty` in
  `agent_runtime` (success=0 + not_found/failed → messaggio onesto). Generale per
  delete/move/send.

**C2 — la sintesi finale dev'essere cieca su nulla.** `_synthesize_final_from_steps`
scartava i campi-LISTA delle entries → vedeva solo `name=Roberto` → rifiuto
privacy. Fix: includere i campi-lista salienti (mail_accounts/channels) + sys_msg
anti-rifiuto. Generale per ogni entry aggregata.

**C3 — ricerche ranked: il top-K È la risposta.** Modi `output_policy.RANKED_MODES`
(G/W/TG): niente "allargo?" (creava un dialog pendente che mangiava la query
successiva) né preambolo "Troppi… considero K". Skip in `_collect_expandable_caps`
e `_collect_truncation_notices`.

**C4 — fallimenti per-item/account NON devono renderizzare come "0"** (1/6/2026).
`read_messages(account="all")`: l'account `knowcastle` fallisce a intermittenza
(`SSL bad record mac`), `fail_count=2`, ma `available_total` non viene settato →
il `@count` cade a 0 → final "Hai 0 mail non lette" mentre 2 account NON sono stati
controllati. **Silent failure §2.8.** Fix: (a) rete `_collect_failure_notices` in
`agent_runtime` — qualunque step con `failed[]` → notice "N non controllati per
errore"; (b) retry 3× su handshake transiente in `mail_client.open_imap` (ADR 0130).
Generale: ogni executor con `failed[]` ottiene la nota d'onestà.

---

## D. Metodo (lezioni di processo)

**D1 — riproduci prima di diagnosticare.** Ogni ipotesi-bug va riprodotta con
args REALI dal turn-log prima di chiamarla bug (terminale glitchato + fretta =
diagnosi errate, 31/5).

**D2 — convergenza a error=0 ×2.** La verifica dei fix cicla fino a error=0 per
≥2 run CONSECUTIVI (guardia contro la non-determinazione del proposer).

**D3 — soluzioni generali/deterministiche, mai patch.** §7.3: forma astratta +
regola sistemica prima di ogni fix. Detection sintomatica solo come safety-net.

---

_Promozioni suggerite_: A1–A4 → estendere §6.1 CLAUDE.md; B1–B3 → ADR nuovo
"backend resolver uniforme"; C1–C3 → già wired (note nel session log).
