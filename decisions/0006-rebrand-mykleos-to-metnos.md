---
id: 0006
title: Rebrand del prodotto da Mykleos a Metnos
date: 2026-04-24
status: accepted
area: branding
---

## Contesto

Il 24 aprile 2026 una verifica di marchio ha mostrato che "Mykleos" — il nome
con cui il progetto era nato e che compariva ovunque nei documenti pubblici
del corpus — era già un marchio AI registrato altrove. Continuare con quel
nome significava esporsi a un conflitto, sia legale sia di SEO, prima ancora
di lanciare. La decisione andava presa rapidamente perché ogni giorno in più
il volume di documenti, link interni, glossari, dialoghi galileiani, e
filename pubblicati su `mykleos.example.com` cresceva, e con lui il costo
del cambio.

Il nome interno del processo — `myclaw`, vivente in `/opt/myclaw/`, nei
servizi systemd, nel namespace Python — non era a rischio: quello non è
pubblico, non viene dato in pasto a nessun crawler, e non collide con marchi
esterni. La rotta giusta era quindi distinguere fra identità pubblica del
prodotto (a rischio) e identità tecnica del codice (al sicuro).

## Decisione

Il prodotto pubblico si chiama **Metnos**, dominio **metnos.com**. Il nome
interno del processo resta **myclaw** (`/opt/myclaw/`, `myclaw-gateway.service`,
`myclaw.runtime`).

L'etimologia è stata riscritta. Mykleos veniva dalla radice greca *kleos*
(κλέος), gloria. Metnos viene dalla composizione di **mētis + noûs**
(μῆτις + νοῦς): la saggezza pratica adattiva di Ulisse, dea madre di Atena
nella mitologia, accoppiata all'intelletto che ragiona. La scelta non è
cosmetica: dice che il sistema riunisce le due forme della mente, dove l'LLM
è il *noûs* e vaglio + telos + mnestoma sono la *mētis*. I glossari sono
stati riscritti, i dialoghi galileiani aggiornati anche nei nomi dei
personaggi, l'entry "kleos" resta nel glossario come voce di lessico greco
ma con nota esplicita che non è più la radice del progetto.

Concretamente è stato fatto: 50+ file in `/opt/myclaw/docs/` aggiornati nei
titoli, meta tag, canonical, link interni, keyword; 18 filename HTML
rinominati `Mykleos_*.html` / `Myclaw_*.html` → `Metnos_*.html`; mappa di
redirect Cloudflare 301 dai vecchi URL ai nuovi in `_redirects`. La email
pubblica visibile è diventata `roberto@example.com`. Le memorie
locali Claude (`mykleos_*.md`) restano come sono, perché sono ID di memoria
non pubblici: solo il loro contenuto è stato aggiornato.

## Alternative considerate

**Tenere Mykleos sperando che il marchio altrui non si attivi.** Pro: zero
costo immediato. Contro: rischio crescente nel tempo, e il costo di un
rebrand a posteriori (con utenti reali, link esterni, indicizzazione
matura) sarebbe enormemente più alto di quello fatto in quel momento.
Scartata.

**Cambiare solo il dominio (mykleos.com diverso da mykleos AI esistente)
ma tenere il nome.** Contro: la collisione di marchio è sul nome, non sul
dominio. Avrebbe rinviato il problema senza risolverlo. Scartata.

**Cambiare nome del prodotto E del processo (`metnos` ovunque).** Pro:
coerenza assoluta. Contro: avrebbe richiesto migrazione di repository
Python, namespace, percorsi installati, servizi systemd in produzione,
con costo concreto e nessun beneficio pubblico (nessuno vede `myclaw`).
Scartata: la separazione fra identità pubblica e identità tecnica è
accettabile, e in casi noti (Kubernetes che si chiama `kube` nei comandi,
Ubuntu il cui kernel resta `linux`) anche desiderabile.

## Conseguenze

I documenti pubblici parlano sempre di Metnos; il codice e le memorie
tecniche tollerano `myclaw` come riferimento al processo. Cloudflare Pages
è da configurare con `metnos.com` come custom domain (il sito risponde
ancora su `mykleos.example.com` come fallback finché il custom domain
non è attivo, ma i canonical nei file HTML puntano già al nome nuovo).

Restano puliture residue da fare in batch quando ce ne sarà occasione: i
file HTML hanno alcuni link interni hardcoded ai vecchi nomi di filename;
Cloudflare Pages strippa `.html` con redirect 308 mentre i `<link
rel="canonical">` dichiarano ancora l'URL con `.html` (mismatch SEO che
chiede una decisione di forma); la casella `admin@metnos.com` referenziata
in `/.well-known/security.txt` non è ancora verificata.

Una verifica IP formale (USPTO classe Nice 9 e 42, EUIPO, WIPO Madrid) per
"Metnos" resta raccomandata prima di considerare il marchio difendibile;
una WebSearch del giorno della decisione ha mostrato `metnos` libero come
prodotto AI, ma è una verifica di superficie, non un parere professionale.
