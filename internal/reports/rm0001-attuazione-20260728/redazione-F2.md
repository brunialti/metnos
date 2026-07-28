### F2 — Controllo dalla chat e preferenze esplicite

**Contratti.**

```python
# runtime/user_context/contracts.py — estensione F2 dei tipi puri nati in F0 [IPOTESI]
class ChatCommand(StrEnum):          # vocabolario CHIUSO del confine, non del planner
    REMEMBER = "remember"; CORRECT = "correct"; FORGET = "forget"; INVENTORY = "inventory"

class BoundaryOutcome(StrEnum):
    NOT_MINE = "not_mine"      # prosegue il motore, nessun effetto
    DONE = "done"; ASK = "ask"; REFUSED = "refused"; UNAVAILABLE = "unavailable"

@dataclass(frozen=True)
class ClauseSpan:  start: int; end: int; text: str        # intervalli prodotti in F0
@dataclass(frozen=True)
class ChatCommandMatch:
    command: ChatCommand; span: ClauseSpan; concept: str   # concetto lessicale che ha deciso
    target_kind: str                                        # "pref" | "claim" | "all"
    target_key: str; captured: str                          # gruppo di cattura, mai riscritto
@dataclass(frozen=True)
class BoundaryResult:
    outcome: BoundaryOutcome; message: str                  # gia' i18n, gia' nella lingua del turno
    audit: dict                                             # concetto, intervallo, reason code, revisioni

# runtime/user_context/boundary.py (F2, §13)
def detect(query: str, *, spans: tuple[ClauseSpan, ...]) -> tuple[ChatCommandMatch, ...]: ...
def handle(query: str, principal, *, has_pending: bool) -> BoundaryResult | None: ...  # None = NOT_MINE

# runtime/user_context/presentation.py (F2) — l'unico oggetto che entra nel turno
@dataclass(frozen=True)
class PresentationSpec:
    reply_length: str; tone: str; units: str; lang: str
    prefs_revision: int; max_bullets: int; max_tokens: int
    suppressed: bool                                        # scavalcamento esplicito nel turno (§4.6)
def presentation_spec(snapshot, query: str) -> PresentationSpec | None: ...   # puro
def apply_units(text_or_entries, spec: PresentationSpec): ...                 # insieme CHIUSO di campi

# inventario: registro di sezioni, non catena di rami — F3/F4/F6 registrano, non modificano
@dataclass(frozen=True)
class InventorySection:
    key: str; label_key: str; state: str                    # "ready" | "not_implemented"
    items: tuple[dict, ...]                                 # valore, origine, ambito, data, comando
def register_section(key: str, provider) -> None: ...
def inventory(principal) -> tuple[InventorySection, ...]: ...  # deterministico, zero LLM
```

**Dati.** F2 non crea tabelle. Scrive nelle tabelle F1 (`source_events` con `kind='user_clause'`/`'correction'`, `memories`, `evidence`) e in `user_prefs` (`runtime/users.py:683` [PROVATO]). L'unica modifica di schema che F2 pretende — `prefs_revision` e transazioni esplicite su W2 — nasce in F0 (§6.1) e non e' riscritta qui.

**Innesti.**

| Punto | File:riga oggi | Che cosa cambia |
|---|---|---|
| Confine HTTP | `runtime/http_routes_agent.py:1072` (`tutor = await _apply_tutor_http(`) dentro `_preprocess_turn` (`:932`) [PROVATO] | Estensione: nuovo blocco **prima** del Tutor. «Che cosa sai di me?» e' semanticamente una domanda sul sistema e oggi la prenderebbe il Tutor; il confine vince solo su match ad alta precisione. [IPOTESI] |
| Sonda dei pendenti | `runtime/http_routes_agent.py:358` `_http_has_pending` [PROVATO] | Riuso invariato: pendente presente ⇒ il confine si astiene (una risposta a un modulo non e' un comando). |
| Confine Telegram | `runtime/channels/daemon.py:1500-1541`, principale da `_callback_principal` (`:1245`) [PROVATO] | Estensione simmetrica: stesso modulo, stessa posizione relativa. `_callback_principal` ritorna `None` per mittente ignoto ⇒ astensione fallendo chiuso [PROVATO]. |
| Principale | `runtime/tutor_boundary.py:47` e `:64` [PROVATO] | **Non toccato in F2**: le due funzioni derivano il pubblico da campi diversi e ripiegano su `"http-user"` (`:56`). Il confine usa direttamente la factory F0; la proiezione del Tutor e' lavoro F3 (§13). |
| Contesto di turno | `runtime/agent_runtime.py:6231` `runtime_ctx = {` [PROVATO] | Estensione: una sola chiave `presentation` con la `PresentationSpec`. Nessun `user_id`, nessuna memoria, nessun claim. Vietato esporla come `${RUNTIME:...}` (`runtime/engine/executor.py:269` [PROVATO]): finirebbe negli argomenti di passo. |
| Finalizzatore | `runtime/engine/executor.py:1524` `_finalize_answer_text(framework, steps, query, llm_fast)`, chiamato a `:2089` e `:2618` [PROVATO] | Estensione di firma con `presentation=None`. E' la fonte UNICA del testo di un turno `answer` (ADR 0177 T5, dichiarato nel docstring [PROVATO]): un solo punto d'effetto, due call-site. |
| Elenchi puntati | `runtime/engine/executor.py:156` `_entries_bullet_lines(..., max_items: int = 20)` [PROVATO] | Estensione: `max_items` passato dal chiamante secondo `reply_length` (breve/normale/dettagliata). Effetto deterministico, senza modello. |
| Sintesi finale | `runtime/engine/executor.py:1774` `sys_msg = _pl.get("final_assembler", _lang)` e `:1789` `final_tokens = 700 if len(obs_lines) >= 3 else 360` [PROVATO] | Estensione: variabili Jinja per tono/lunghezza/unita' e tetto di uscita derivato da `reply_length`. Il testo del `.j2` e' model-facing: lo scrive Fable, Opus integra e prova. |
| Unita' | `runtime/photon_client.py:175` `entry["distance_km"]` [PROVATO] | Nessuna modifica alla sorgente. Conversione solo in presentazione, su un insieme CHIUSO di campi dichiarati; insieme vuoto ⇒ nessun effetto, dichiarato nell'inventario invece che finto. |
| Lessico | `runtime/detection_lexicon_seed.py:50` `register_all()` [PROVATO] | Nuovi concetti `usercontext.*` di tipo `regex` con gruppo di cattura, ancorati all'inizio della clausola, IT+EN. `register` e' idempotente per riga: una forma sbagliata richiede `set_payload` (`runtime/detection_lexicon.py:220`), non un secondo seed [PROVATO]. |
| Segmentazione | `runtime/compound_decomposer.py:95` `split_query_chunks -> list[str]` [PROVATO] | Consumo, non modifica: F2 usa gli intervalli introdotti in F0. Oggi la funzione perde l'ancoraggio posizionale (`p.strip()`, scarto dei vuoti) e non basta [PROVATO]. |
| Lettura preferenze | `runtime/users.py:733` `list_prefs` (ritorna `{chiave: valore}`) [PROVATO] | Sostituzione del tipo di ritorno con record che portano `source` e `updated_at` — gia' in tabella (`:683`) e oggi scartati nella SELECT [PROVATO]. Consumatori da aggiornare insieme: `runtime/http_routes_admin.py:1122` e `runtime/templates/user_detail.html:164` [PROVATO]. |
| Scrittura preferenza | `runtime/users.py:695` `set_pref(..., source="explicit")` [PROVATO] | Contratto invariato; il confine passa `source="chat"`. Oggi nessun chiamante di prodotto valorizza `source`: diventa un vocabolario chiuso di due valori. |
| Oblio tipizzato | `runtime/users.py:743` `delete_pref` [PROVATO] | Chiamata dal ramo oblio dentro la transazione e il journal F1. Nota: `delete_user` (`:269-278`) non cancella `user_prefs` [PROVATO] — difetto W2 preesistente che l'inventario post-oblio rende visibile. |
| Superficie admin | `runtime/templates/user_detail.html:152-158` letterali italiani, `:164` chiave tecnica nuda [PROVATO] | Sostituzione con `msg(...)` (§7.13) e sezione inventario in sola lettura. Le chiavi `MSG_SETTINGS_*` del gruppo siti restano il modello. |
| Cache | `runtime/engine/fastpath.py:229` `normalize_hash(query)`, `:55` `NON_CACHEABLE_TOOLS` [PROVATO] | **Nessuna modifica, per prova**: il confine non produce piano ne' executor, quindi non c'e' nulla da escludere dalla cache. L'assenza di modifica e' essa stessa un requisito verificato (§5.10). |

**Ordine di costruzione.**

1. Concetti `usercontext.*` nel seed + consumo degli intervalli F0. *Verifica:* sulle 30 richieste-esca del corpus congelato, zero riconoscimenti; «ricordami di comprare il latte» resta un compito e non un ricordo. Fallisce in modo visibile: un solo falso positivo blocca il passo.
2. `list_prefs` allargata e i due consumatori aggiornati. *Verifica:* pagina `/admin/users/{id}` verde e prova di contratto che nessun chiamante legge piu' un dizionario piatto.
3. `PresentationSpec` + innesto nel finalizzatore, interruttore ancora spento. *Verifica:* con interruttore spento, testo finale byte-identico su un campione di turni registrati.
4. Accensione della sola presentazione (nessun comando). *Verifica:* A/B a memoria accesa/spenta sulle stesse richieste — piano, strumenti e `tools_sig`/`pool_sig` identici, lunghezza della risposta misurabilmente diversa. Un solo piano divergente e' un fallimento bloccante (§5.10).
5. Confine HTTP, ramo inventario in sola lettura. *Verifica:* turno reale; l'inventario elenca le sezioni non ancora implementate come `not_implemented` invece di ometterle.
6. Confine Telegram con lo stesso modulo. *Verifica:* stesso principale, stessa lingua, stesso testo dai due canali (UC-12, invariante 29).
7. Rami ricorda e correggi. *Verifica:* claim che ricade in una `PreferenceSpec` — in F2 derivata dalle sole chiavi W2 (`runtime/users.py:632`, `:637` [PROVATO]) — instradato a W2 e non alla memoria libera; revisione incrementata nella stessa transazione.
8. Ramo oblio + i18n + superficie admin. *Verifica:* dopo «dimentica», inventario che elenca esplicitamente cio' che resta, e assenza dopo riavvio del servizio.

**Interruttore.** `METNOS_USER_CONTEXT_CHAT`, predefinito `0` (spento). [IPOTESI] Spento: nessun confine su HTTP e Telegram, nessuna chiave `presentation` in `runtime_ctx`, `_finalize_answer_text` invocata come oggi. Restano attivi e **non** governati dall'interruttore, perche' sono cambi di contratto e non di comportamento: `list_prefs` allargata, i due consumatori aggiornati, le chiavi i18n, la pagina admin sanata, i concetti lessicali seedati ma non interrogati. F1 resta raggiungibile solo da amministrazione: nessun comando di chat scrive.

**Prove.**

- *Unitarie:* «ricorda che X» riconosciuto in IT e EN con l'intervallo esatto della clausola; «ricordami di X» non riconosciuto; forma dentro una citazione, un blocco di codice o un allegato non riconosciuta (invariante 4); `presentation_spec` restituisce `suppressed` quando il turno contiene un'istruzione esplicita di presentazione (invariante 6); `apply_units` non tocca un campo fuori dall'insieme chiuso.
- *Integrazione:* preferenza posta da HTTP e onorata nel turno successivo da Telegram; inventario identico dai due canali; comando dato dentro una richiesta composta di due clausole — la mutazione tocca solo la propria clausola, l'altra prosegue al motore; con dialogo pendente il confine si astiene e la risposta arriva al modulo aperto.
- *Avversariali:* pagina o messaggio inoltrato che contiene «ricorda che ...» non scrive nulla; ospite che chiede l'inventario non vede claim del proprietario e viceversa; comando di memoria pronunciato con l'attore vuoto o su rete locale non associata non diventa proprietario (invariante 2); dichiarazione di categoria sensibile rifiutata e non persistita, con il testo scartato non conservato.
- *Iniezione di guasto:* store memorie non consultabile — il confine risponde `UNAVAILABLE` con messaggio tecnico esplicito e non prosegue verso il motore fingendo una richiesta ordinaria; caduta fra scrittura W2 e incremento della revisione; lessico non seedato nella lingua dell'istanza — l'esito e' registrato, non subito; interruttore spento a caldo con turno in corso.
- *Turno reale (§8.5):* uno su `/agent/turn` e uno su Telegram per ciascuno di UC-01, UC-09 e UC-10, sullo stesso principale, con il registro dei turni citato nella chiusura di fase.

**Fuori da questa fase.**

- `PrincipalContext`, `authz_revision`, intervalli di clausola, corpus congelato, `prefs_revision` e transazioni W2 sono **F0**: F2 non e' costruibile senza. §6.1 non assegna una fase alla restituzione di origine e data dalle API di W2: se F0 la chiude, F2 la consuma; altrimenti F2 la reclama come passo 2 e lo dichiara.
- Store memorie, journal di cancellazione separato, replay idempotente e quarantena sono **F1**: il ramo oblio di F2 e' solo il chiamante.
- Default operativi, `ReferenceSlot`, sezione firmata `[personalization]` e la `PreferenceSpec` estesa alle dichiarazioni di dominio sono **F3**: in F2 l'inventario mostra quelle sezioni come non implementate invece di ometterle.
- Episodi, «che cosa abbiamo fatto ieri», FTS5 e domande aggregate sono **F4**; apprendimento implicito, compilatore e gemelli semantici sono **F5** — in F2 «dimentica» agisce su una chiave tipizzata o su un ID gia' esistente, non su un gemello compilato.
- Routine e riscrittura canonica prima di L0 sono **F6**: F2 non tocca `normalize_hash` ne' l'insieme dei tool non memorizzabili.
- Tono e lunghezza sulle risposte del Tutor e dentro `describe_entries` restano fuori: sono compositori distinti dal finalizzatore, e un secondo punto d'effetto va misurato prima di essere aggiunto (§8.3 ne dichiara uno solo).