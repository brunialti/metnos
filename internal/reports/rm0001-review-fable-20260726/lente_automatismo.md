# Lente automatismo

Tutte le righe citate si riferiscono a
`/opt/metnos/internal/roadmap/RM-0001-conoscenza-utente-locale.md` (2832 righe,
lette per intero), salvo dove indicato altrimenti.

## Verdetto in tre righe

Il lato controllo («cosa sai di me?», correzione, oblio) rispetta la direzione
di Roberto; il lato apprendimento no: ho censito 21 punti in cui l'utente deve
agire, e nessuna fase F0-F11 rende mai l'acquisizione implicita il
comportamento predefinito del proprietario — le preferenze, l'unico tipo di
memoria che cambia le risposte, non hanno alcun percorso silenzioso in tutta la
roadmap, e l'apprendimento comportamentale termina sempre in una proposta da
approvare. Ogni gate preso da solo è motivato; è l'aggregato che nessuna delle
tre review precedenti ha contato.

## Rilievi

### R1 — Censimento: 21 azioni dell'utente perché la memoria funzioni

[PROVATO] — ogni voce con sezione e riga del documento:

| # | Azione richiesta all'utente | Sezione (riga) |
|---|---|---|
| 1 | confermare il primo riferimento (Atlas) e rispondere quando Metnos chiede | UC-02 (44-48) |
| 2 | ricordare i comandi «ricorda X» / «dimentica X» e il confine memories/tasks/texts | UC-10 (157), §9.6 (818-871) |
| 3 | porre «cosa sai di me?» / «cerca nei miei ricordi» | UC-09 (143-153), §9.6 (823-826) |
| 4 | rispondere alla domanda quando la scelta cambia materialmente il risultato | §6.1 (364) |
| 5 | rispondere alla domanda funzionale alla prima ambiguità; ri-risposte quando i candidati cambiano | §9.3 (762-766) |
| 6 | una conferma quando il comando esplicito è ambiguo | §9.5 (813-814) |
| 7 | approvare il delete «se richiesta dalla futura ADR» | §9.6 (887) |
| 8 | formulare le preferenze con marcatori persistenti riconoscibili («d'ora in poi», «sempre») | §10.1 punto 4 (934-936), §12.1 (1252-1264) |
| 9 | opt-in individuale dell'ospite per conservare preferenze | §10.2 (955-957) |
| 10 | rispondere alla disambiguazione gestita dal runtime | §12.1 (1261) |
| 11 | opt-in del proprietario per eventi impliciti reali | §12.2 (1290-1291), F4 (1806), §25.4 F4.3 (2638), §27.4 (2792) |
| 12 | richiedere il backfill (operazione amministrativa con preview e opt-in) oppure ripetere il già detto | §12.2 (1285-1287) |
| 13 | confermare il bersaglio con get_inputs per OGNI operazione mutante/outbound, anche con corrispondenza forte | §14.2 (1544-1546) |
| 14 | accettare o rifiutare le proposte proattive | §14.4 (1568-1577), F6 (1828-1837) |
| 15 | visitare la superficie di esplorazione; chiedere il dettaglio per vedere i candidati | §15.1 (1620-1630) |
| 16 | correggere il dato errato | §15.2 (1639-1647), §21 (2314) |
| 17 | dare il comando di oblio e rispondere «quale?» se il bersaglio non è esatto | §15.3 (1652-1653) |
| 18 | accendere gli interruttori per fase, utente e capacità (default tutti spenti) | §17 (1741-1742), §25.1 (2508) |
| 19 | etichettare il campione di claim derivati (confermati/corretti/rifiutati); valutazione manuale F4 | §19.1 (2144-2145), F4 (1809) |
| 20 | rispondere alla domanda sul conflitto materiale («domanda una volta») | §21 (2312), §20 (2290) |
| 21 | revisionare report/ChangeIntent e ratificare le ADR di attivazione | UC-07 (118-127), F10 (1924-1934), §22 punti 4/8/18 (2349, 2355, 2369) |

Conseguenza: il vincolo «automatica» del mandato va giudicato su questo
aggregato, non sul singolo gate; i rilievi seguenti lo fanno.

### R2 — Le preferenze tipizzate non hanno alcun percorso silenzioso, in nessuna fase

[PROVATO] — §11.2 (1004-1005): «Le preferenze tipizzate non entrano in
user_memories, non hanno embedding e non sono dedotte dal compilatore nelle
prime fasi»; nessuna fase F0-F11 (§17, 1745-1949) sblocca poi la deduzione. Il
rilevatore immediato copre solo «formulazioni ad alta precisione» con marcatori
(§12.1, 1252-1258; §10.1 punto 4, 934-936). F6 produce solo «proposte
deterministiche limitate» (1833). Quindi l'unico tipo di memoria che cambia le
risposte nasce SOLO da dichiarazione esplicita ben formulata o da proposta da
approvare — mai in silenzio. [PROVATO] La tensione è interna al documento: §21
(2306-2311) promette «per preferenze non sensibili e autoregolanti non sono
previste conferme continue», ma nessun modulo produce quelle preferenze
autoregolanti. Conseguenza: la promessa di §21 è priva di produttore; o si
aggiunge un percorso di derivazione a bassa sensibilità, o §21 va riscritto.

### R3 — Lo stato finale «apprende in silenzio» non è mai programmato

[PROVATO] — default tutti spenti (§25.1, 2508), interruttore per utente e
capacità (§17, 1741-1742), opt-in per dati reali impliciti (§12.2, 1290-1291),
ADR separata per «il primo cambiamento osservabile in produzione» (§22 punto
18, 2369). §23 (2377-2380) consente lo stato `implemented` con F0-F5: una
roadmap completata in cui il proprietario non ha mai fatto opt-in non impara
nulla implicitamente, e il documento non dichiara mai quando — a fasi promosse
— il silenzioso diventi il default. [OPINIONE] Una roadmap la cui condizione di
completamento è compatibile con «memoria che non impara mai da sola» non
soddisfa il vincolo «automatica» dichiarato dal mandato. Conseguenza: serve una
riga di stato finale (vedi sotto), non un'altra fase.

### R4 — L'opt-in del proprietario può collassare nell'installazione

[PROVATO il vincolo attuale] — §12.2 (1290-1291), §27.1 (2739) e §27.4 (2792)
esigono opt-in prima di dati reali impliciti. [PROVATO il riscontro nuovo] — la
direzione di prodotto «l'apprendimento avviene in silenzio e il controllo passa
da una domanda» è documentata fuori da RM-0001: contesto comune
`/home/roberto/.metnos/rm0001_review/comune.md` righe 25-29 e indice memorie
`~/.claude/projects/-opt-metnos/memory/MEMORY.md` («Silent-learn (NO conferme,
Roberto)», voce DESIGN dati-utente 22/7); il documento preparatorio che la
conteneva è stato rimosso (§2, 234-236) e la direzione NON è transitata in
RM-0001 come requisito. [OPINIONE] Poiché inventario e oblio esistono già da F1
(1769-1777) e l'opt-in non è fra le decisioni fissate di §22 (2320-2340), per
l'istanza mono-proprietario l'opt-in può diventare un consenso unico all'
installazione/prima attivazione, senza violare §2.8 (nulla viene dichiarato
diverso dal reale) né l'ispezionabilità (UC-09 resta il controllo). Gli ospiti
(§10.2) restano opt-in individuale. Conseguenza: un'azione una-tantum invece di
un gate che il documento oggi lascia perpetuamente aperto.

### R5 — L'apprendimento comportamentale termina sempre in una proposta

[PROVATO] — §14.4 (1568-1577): «Può proporre, non eseguire»; §21 (2313):
«inferenza debole: candidato invisibile al comportamento»; F6 misura «proposte
accettate» e «tasso di fastidio» (1835-1837). Non esiste il gradino intermedio:
applicazione silenziosa, reversibile e visibile in inventario per abitudini di
sola presentazione/lettura. [OPINIONE] Per questo sottoinsieme (nessuna
mutazione, nessun outbound, correzione a costo di una frase) la proposta
obbligatoria è più conservativa della direzione dichiarata e crea proprio il
«flusso di approvazioni» che Roberto ha escluso. Conseguenza: F6 dovrebbe
distinguere «proporre azioni» (giusto) da «applicare presentazione» (può essere
silenzioso con voce in inventario).

### R6 — La ri-domanda sui riferimenti non ha isteresi

[PROVATO] — §9.3 (764-766): «Se i candidati cambiano o il riferimento diventa
ambiguo, il sistema torna a chiedere», senza alcun limite di frequenza; i
budget di §9.5 (813-815) riguardano le consultazioni, non le domande poste
all'utente. [IPOTESI] Su domini volatili (cartelle create/rimosse, contatti
sincronizzati da provider) «i candidati cambiano» a ogni sincronizzazione: lo
stesso riferimento già confermato può rigenerare domande ripetute.
Conseguenza: serve una regola tipo «si richiede solo se il candidato confermato
è sparito o il nuovo insieme non lo contiene», altrimenti la prima domanda —
legittima — degrada in flusso.

### R7 — Niente importazione del già detto: costo di ripetizione non contato

[PROVATO] — §12.2 (1285-1287): «I turni precedenti all'attivazione non vengono
importati»; il backfill è operazione amministrativa con preview e opt-in. La
motivazione tecnica esiste (§5.4, 333-341: i TurnLog non hanno il principal
canonico). [IPOTESI] Per il proprietario sui canali già associati (Telegram
accoppiato, device binding) l'attribuzione è in pratica nota: il vincolo è più
largo del necessario e il costo — ripetere preferenze e riferimenti già
espressi in mesi d'uso — non compare in nessuna metrica di §19. Conseguenza:
almeno dichiarare il costo e offrire il backfill del proprietario come singola
azione guidata alla prima attivazione.

### R8 — «Cosa sai di me?» in chat non è garantito dalle prime fasi

[PROVATO l'ambiguità] — F2 (1783): «UI/runtime diretto prima; executor builtin
server-only soltanto dopo ADR»; l'oggetto `memories` richiede ratifica (§9.6,
828-830; §22 punto 4, 2349). [IPOTESI] Se «UI diretto» diventa il percorso
reale e l'ADR dell'executor slitta, il controllo passa dal porre una domanda in
chat al visitare una pagina di amministrazione — l'esatto rovescio della
direzione «il controllo è una domanda». Conseguenza: F2 deve garantire
esplicitamente che UC-09 e UC-10 funzionino dal canale chat, con o senza
executor ratificato.

### R9 — Che cosa NON può diventare silenzioso, e perché

[OPINIONE, su riscontri citati] Questi punti del censimento devono restare
azioni dell'utente:
- **#13** conferma su mutanti/outbound (§14.2, 1544-1546): la memoria non può
  ridurre un consenso (invariante 4, §8; minaccia «autorità implicita», §16) —
  renderla silenziosa trasformerebbe un claim in autorità;
- **#5** prima domanda su ambiguità materiale (§9.3): scegliere in silenzio
  senza fatto confermato rischia il falso successo (§2.8); una domanda una
  volta è il prezzo minimo dell'onestà;
- **#9** opt-in ospiti (§10.2): riguarda dati di terzi, non del proprietario;
- il rifiuto dei sensibili anche su richiesta esplicita (§16.1, 1697-1699;
  §10.1, 944-946): un rifiuto dichiarato è §2.8-conforme, un silenzioso
  non-salvataggio di un «ricorda» esplicito sarebbe uno scarto muto;
- **#17** il comando di oblio (§15.3): è il controllo stesso, non un attrito;
- **#21** revisione di report/ChangeIntent (F10): scritture verso autorità
  esterne (Tutor, autopath) esigono revisione umana per costruzione.

## Che cosa toglierei / che cosa aggiungerei

**Aggiungerei** (in ordine di valore):
1. una dichiarazione di stato finale in §17 o §22: «a fasi promosse, per il
   proprietario, acquisizione implicita e applicazione in lettura sono il
   default; il controllo è UC-09/UC-10» — oggi la roadmap è compatibile con un
   sistema che non impara mai da solo (R3);
2. un percorso di derivazione per preferenze a bassa sensibilità con retention
   `behavioral` e correzione a posteriori, che dia un produttore alla promessa
   di §21 (R2);
3. il collasso dell'opt-in del proprietario in un consenso unico alla prima
   attivazione, ospiti esclusi (R4);
4. in F6, il gradino «applicazione silenziosa reversibile» per abitudini di
   sola presentazione, distinto dalle proposte di azione (R5);
5. una regola di isteresi sulla ri-domanda dei riferimenti (R6) e una metrica
   aggregata del carico d'interazione in §19 (domande e conferme per settimana,
   non solo il «tasso di fastidio» delle proposte F6);
6. in F2, la garanzia che UC-09/UC-10 funzionino dal canale chat (R8).

**Toglierei**: l'«approvazione se richiesta dalla futura ADR» sul delete
esplicito (§9.6, 887). Un delete della propria memoria, con selezione esatta,
expected_version e postcondizione riletta (§25.3), ha già tutte le garanzie: il
comando esplicito È il consenso. Lasciare aperta la porta a un secondo giro di
approvazione contraddice la direzione senza aggiungere sicurezza. Nient'altro:
i gate di R9 restano.

## Ciò che ho cercato e NON ho trovato

- Una fase, gate o ADR che renda l'acquisizione implicita il default del
  proprietario: assente in §17, §22, §23 (verificato leggendo integralmente
  F0-F11 e i criteri di completamento).
- Il produttore delle preferenze «autoregolanti» promesse da §21: nessun modulo
  di §25.1 le genera; §11.2 esclude la deduzione «nelle prime fasi» senza mai
  indicare la fase che la sblocca.
- La direzione «silent-learn, il controllo è una domanda» dentro RM-0001: il
  documento preparatorio che la conteneva è stato rimosso (§2) e la frase non
  compare nella roadmap; il punto più vicino è §6.1 «effetto silenzioso quando
  il dato è non sensibile, affidabile e pertinente» (362), che però governa
  l'applicazione, non l'acquisizione.
- Una metrica del carico d'interazione complessivo in §19: esistono «ambiguità
  risolte correttamente senza domanda» (2140) e il fastidio delle sole proposte
  F6 (1835-1837), ma nessun conteggio di domande/conferme richieste all'utente
  per turno o per settimana.
- Un'osservazione equivalente nelle review precedenti: §7.1, §7.2 e §27 spingono
  tutte verso PIÙ gate (opt-in, terminalità, attestazioni); nessuna valuta il
  costo aggregato di interazione né la sua coerenza con la direzione dichiarata.
