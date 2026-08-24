# Contratto executor e confine MCP

**Stato:** decisione architetturale completata il 24/8/2026. MCP resta un
possibile trasporto interno e non viene integrato finche' un caso d'uso concreto
non dimostra un vantaggio rispetto a un provider gia' ammesso. Nessuna
migrazione degli executor e' implicata da questo documento.

**Protocollo verificato:** release candidate MCP `2026-07-28`. La verifica non
assume che nomi, descrizioni o annotation del server siano affidabili; la
specifica stessa richiede che le annotation siano considerate non fidate e
ammette cataloghi diversi secondo l'autorita' presentata. Fonti primarie:
[tools 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/server/tools),
[authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization),
[reference servers](https://github.com/modelcontextprotocol/servers).

**Dipendenza normativa:** `EXECUTOR_STANDARD.md` definisce il contratto
fondativo `metnos.executor/1.0`. Questo documento descrive soltanto come MCP puo'
collocarsi sotto quel contratto e non puo' modificarlo o indebolirlo.

## Scopo

Definire un punto di integrazione che permetta a Metnos di usare strumenti MCP
senza sostituire il proprio modello di executor. Il pianificatore deve continuare
a vedere nomi canonici, argomenti tipizzati e risultati Metnos. Trasporto,
fornitore e protocollo restano dettagli interni di esecuzione.

## Vincoli

1. Gli executor esistenti non vengono riscritti per adottare il contratto.
2. Il vocabolario `verb_object[_qualifier]` resta l'autorità dei nomi visibili al
   pianificatore.
3. Capacità, sandbox, mandato, credenziali, consenso e verifica dell'esito restano
   responsabilità di Metnos.
4. Descrizioni, annotation e risultati provenienti da un server MCP sono dati non
   fidati.
5. Un cambiamento del catalogo o dello schema MCP non modifica automaticamente il
   catalogo attivo di Metnos.
6. L'integrazione deve fallire in modo esplicito e non deve trasformare un risultato
   non verificato in successo.

## Applicazione dello standard senza migrazione di massa

Il contratto definito in `EXECUTOR_STANDARD.md` va rappresentato una sola volta
nel runtime, componendo tre livelli:

| Livello | Contenuto | Visibile al pianificatore |
|---|---|---|
| Contratto semantico | nome, descrizione naturale, schema degli argomenti, forma del risultato | sì |
| Regola di esecuzione | capacità, sandbox, collocazione, provenienza, limiti, postcondizione, modalità diretta o intelligente | no |
| Adattatore di trasporto | processo locale, invocazione remota firmata, MCP | no |

Il loader costruisce questo modello dai manifest attuali. Gli executor privi
della dichiarazione restano temporaneamente `legacy` e continuano a caricarsi.
La validazione e' osservativa per i legacy e bloccante per ogni executor che
dichiara `executor_standard = "metnos.executor/1.0"`. Un refactor termina solo
quando aggiunge la dichiarazione, supera i gate, ri-firma e verifica la
regressione; non e' richiesta una bonifica simultanea del catalogo.

Il formato pubblico di invocazione resta quello corrente: un oggetto JSON in
ingresso e un oggetto JSON in uscita al confine del processo. L'uniformazione degli
errori e dei metadati avviene nel runtime, non duplicando logica dentro ogni
executor.

## Adattatore MCP

`McpBroker` è un componente interno, non un executor scelto dal pianificatore. Ha
responsabilità circoscritte:

- aprire e chiudere connessioni MCP;
- negoziare versione e capacità;
- acquisire l'elenco dei tool con paginazione;
- fissare identità del server, versione del protocollo e impronta degli schemi;
- invocare un tool con limite di tempo, annullamento e limite di concorrenza;
- validare input e output strutturati quando lo schema è disponibile;
- produrre una traccia di audit priva di segreti;
- segnalare ogni modifica del catalogo senza ammetterla automaticamente.

Un executor proxy Metnos traduce fra il contratto canonico e una chiamata al
broker. Il pianificatore non riceve mai argomenti come `server`, `tool` o un blocco
JSON arbitrario. Un executor pubblico generico `mcp_executor(server, tool, args)` è
vietato: introdurrebbe un linguaggio parallelo e aggirerebbe il controllo per
singola capacità.

## Ammissione di un tool MCP

Ogni tool scoperto produce uno solo dei seguenti esiti:

1. **Backend di un executor esistente.** Si usa quando scopo, schema e
   postcondizione coincidono con una capacità canonica già presente.
2. **Executor proxy.** Si crea un normale manifest Metnos e un adattatore ristretto
   quando la capacità è nuova ma sufficientemente tipizzata e verificabile.
3. **Quarantena.** Si applica quando mancano schema, autorità mappabile,
   postcondizione, provenienza affidabile o confini di rete e dati.

La descrizione MCP può suggerire la classificazione, ma non autorizzarla. Nomi,
capacità, tipo di effetto, idempotenza e reversibilità sono derivati o confermati
dal processo di ammissione Metnos.

## Promozione verso un executor nativo

La promozione è diversa dall'importazione. Il proxy può raccogliere, senza segreti,
un corpus di invocazioni riuscite e fallite. Su questa base Metnos può proporre un
executor nativo soltanto se sono soddisfatte tutte le condizioni:

- la logica è riproducibile senza dipendere dal codice remoto non disponibile;
- licenza e provenienza permettono l'implementazione;
- input, output, errori ed effetti hanno un contratto verificabile;
- esiste una suite di equivalenza fra proxy e candidato nativo;
- capacità, sandbox, mandato, idempotenza e procedura inversa sono definite;
- la proposta attraversa il normale vaglio e non viene applicata automaticamente.

Se il valore del tool dipende da un servizio remoto proprietario, la promozione non
può eliminare tale dipendenza: l'esito corretto resta un backend o un proxy.

## Sicurezza iniziale

La prima adozione, se approvata, deve accettare soltanto tool remoti di lettura con
`inputSchema`, `outputSchema` e origine fissata. Restano disabilitati:

- server locali avviati tramite `stdio`, perché equivalgono a eseguire codice locale;
- sampling, che invertirebbe il controllo chiedendo al client di usare un LLM;
- elicitation, che richiede un'integrazione esplicita col dominio dialoghi;
- task MCP e capacità sperimentali;
- tool mutativi privi di mandato, idempotenza e verifica della postcondizione;
- passaggio diretto di token destinati a servizi diversi.

Le credenziali restano nel vault Metnos. Il modello non vede token e il server MCP
riceve solo l'autorità necessaria alla singola capacità ammessa.

## Esperienza utente

L'interfaccia presenta capacità, non protocolli. Per ogni capacità importata mostra:

- scopo in linguaggio naturale;
- dati letti o modificati;
- esecuzione locale o tramite servizio remoto;
- account e mandato applicati;
- reversibilità e condizioni di consenso;
- provenienza, stato della verifica e dipendenza esterna.

I dettagli MCP e gli schemi restano disponibili in una sezione tecnica. Una
modifica di schema sospende la capacità e chiede un nuovo esame; non produce una
sequenza di approvazioni durante l'uso ordinario. Gli errori sono espressi nel
dominio dell'azione e conservano il codice tecnico solo come dettaglio diagnostico.

## Sequenza di adozione proposta

1. Usare `metnos.executor/1.0` come autorita' e misurare le deviazioni legacy.
2. Formalizzare il modello in memoria e l'adattatore per i manifest esistenti.
3. Costruire il broker e una prova di discovery senza esporre tool al pianificatore.
4. Ammettere un solo tool remoto di lettura come proxy canonico conforme.
5. Verificare timeout, modifica schema, output non valido, revoca e indisponibilità.
6. Provare la mappatura di un tool come backend di un executor esistente conforme.
7. Valutare separatamente tool mutativi e promozione nativa.

## Tre mappature end-to-end

Queste mappature usano server pubblici reali, ma non li ammettono.

| Tool esterno | Classificazione | Contratto Metnos | Autorita' ed esito |
|---|---|---|---|
| `search_issues` del server MCP ufficiale GitHub | backend possibile | un executor canonico di ricerca issue conserva schema, provider e output Metnos; owner/repository non diventano nomi di tool | token separato per GitHub, scope letto dal mandato, risultati ridotti e validati; schema o tool cambiato sospende il binding |
| operazioni di lettura del reference server Filesystem | quarantena nella forma pubblicata `stdio`; proxy possibile solo su un dispositivo gia' governato | il planner continua a vedere `read_files`/`find_files`, mai path o comando di avvio del server come metalinguaggio | root esatte, sandbox e identita' del device restano di Metnos; avviare un pacchetto locale non verificato equivale a eseguire codice e non e' discovery innocua |
| `fetch` del reference server MCP | backend possibile, ma oggi senza vantaggio rispetto a `read_urls_html` | URL, limiti, rete, redirect, contenuto e fallimenti restano nel contratto dell'executor esistente | allowlist/mandato di rete, timeout e limite di output applicati da Metnos; testo remoto resta non fidato e non puo' modificare catalogo o policy |

Le tre mappature non richiedono di riscrivere il catalogo: la relazione fra
executor e backend appartiene alla configurazione ammessa del trasporto. Non e'
stato trovato un caso che giustifichi oggi il costo di broker, credenziali,
revoca, invalidazione e nuova superficie operativa.

## Matrice semantica

| Concetto MCP | Concetto Metnos | Regola |
|---|---|---|
| server/tool name | backend di un executor ammesso | mai esposto direttamente al planner; identita' composta da origine fissata, versione protocollo e impronta schema |
| `inputSchema` | `args` del manifest | deve essere compatibile per inclusione; campi MCP extra non diventano automaticamente argomenti pubblici |
| `outputSchema`/`structuredContent` | contratto di output e postcondizione | validazione obbligatoria quando dichiarato; l'assenza di schema impedisce l'ammissione iniziale |
| annotation read-only/destructive/idempotent | effect, capability, idempotenza e undo | soltanto un indizio non fidato; l'autorita' e' il manifest Metnos verificato |
| errore tool/protocollo | errore operativo tipizzato | mai trasformato in successo; dettaglio MCP redatto e conservato nell'audit tecnico |
| `tools/list_changed` | candidato di configurazione | invalida cache e sospende il binding; non promuove o modifica il catalogo |
| task sperimentale | job/processo Metnos | nessuna equivalenza automatica; fuori dal primo perimetro |
| elicitation/sampling | dialogo/LLM governati da Metnos | disabilitati: non possono invertire controllo, consenso o scelta del modello |

## Threat model minimo

| Minaccia | Effetto | Controllo necessario |
|---|---|---|
| descrizione o output con prompt injection | il planner tenta azioni o amplia lo scopo | dati MCP trattati come contenuto, schema/limiti, nessuna autorita' derivata dal testo |
| cambio silenzioso di tool o schema | stessa capability esegue un'altra operazione | pin di origine/versione/impronta, invalidazione e nuova ammissione esplicita |
| token passthrough/confused deputy | credenziale riusata verso una risorsa diversa | token con audience per il server, credenziale upstream separata, nessun token nei prompt/log |
| server o trasporto compromesso | output falso, esfiltrazione o mutazione | sandbox/rete/mandato Metnos, postcondizione indipendente, timeout e revoca |
| collisione di nomi fra server | invocazione del backend sbagliato | identita' interna composta; il nome MCP non e' mai una chiave pubblica canonica |
| output enorme, ciclico o malformato | esaurimento di memoria, token o tempo | limiti byte/elementi/profondita', validazione streaming e fallimento tipizzato |
| catalogo dipendente dall'utente | capability leak fra identita' | discovery legata allo stesso principal/mandato dell'invocazione e cache segregata |
| task orfano o cancellazione inefficace | effetto tardivo non osservato | task esclusi dal primo perimetro; futura adozione richiede identita', TTL e riconciliazione |

## Decisione

Il broker interno, la classificazione `backend/proxy/quarantena`, il divieto di
ammissione automatica e il primo perimetro read-only sono ratificati come unico
confine accettabile. La decisione operativa e' **non integrare MCP ora**: Metnos
possiede gia' provider conformi per i tre esempi e non esiste un requisito
utente che compensi la nuova superficie di sicurezza e manutenzione. Una futura
richiesta riaprira' il lavoro dal passo 3 della sequenza, con una sola capacita'
read-only e senza modificare questa decisione per implicazione.

## Criteri di accettazione dell'analisi

Sono stati ratificati come precondizioni obbligatorie di qualunque futuro codice:

- il broker MCP come componente interno e non come tool generico;
- la classificazione backend, proxy o quarantena;
- il divieto di ammissione automatica dopo `tools/list_changed`;
- il perimetro iniziale limitato alla lettura remota;
- la promozione come proposta verificata, mai come riscrittura automatica diretta.
