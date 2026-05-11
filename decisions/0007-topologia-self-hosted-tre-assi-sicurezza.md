---
id: 0007
title: Topologia self-hosted su `.33` e tre assi distinti di sicurezza
date: 2026-04-24
status: accepted
area: architecture
---

## Contesto

Per gran parte di aprile l'Architettura aveva trattato in modo astratto la
domanda "dove gira Metnos". I capitoli 1-3 parlavano di filosofia,
costituzione, telos; quando era ora di calare la macchina nel mondo,
restava aperta la scelta fra cloud, server proprio, laptop, ibrido. Il
24 aprile 2026 si è preso il tempo di fissare la topologia, e nel farlo è
emerso un secondo tema: la sicurezza non è un solo asse, sono tre, e
trattarli come uno solo (con un muro uniforme di "tutto richiede
conferma") era una semplificazione che impoveriva il pensiero.

Senza una topologia decisa, ogni microdesign — gateway, sandbox, pairing,
policy — era costretto a parlare in astratto, e ogni discussione di
sicurezza tendeva a collassare su "facciamo una gate uniforme". La
decisione è stata presa per sbloccare i microdesign successivi e per
posizionare correttamente l'asimmetria precauzionale come gradiente, non
come muro.

## Decisione

Metnos vive su un server casalingo identificato come `.33` (la sua
ultima ottava IP nella LAN domestica). Sistema operativo Linux. Tutto il
core gira lì: gateway, runtime, vaglio, mnestoma, audit log centrale. Il
laptop Windows di Roberto è un client remoto; la connessione fra laptop e
`.33` passa per un overlay **Headscale** self-hostato (il control plane
WireGuard open-source, in versione casalinga di Tailscale). Il bot
Telegram fa polling dal server (non webhook in entrata): il server vede la
rete, la rete non vede il server. La sincronizzazione di file fra
dispositivi è un modulo a parte con interfaccia chiara, non una funzione
del core. Scelta di default al day one: Syncthing.

Sopra questa topologia, il capitolo 5 dell'Architettura v1.1 distingue
**tre assi** di sicurezza, non due:

- **Libertà vs sicurezza dell'identità.** Non c'è compromesso accettabile.
  Chi parla con Metnos deve essere chi dice di essere; tutto il resto
  poggia su questo. È precondizione, non parametro.
- **Libertà vs sicurezza del perimetro.** Compromesso reale, mediato da
  un nucleo duro (mai modulabile: shell esecutiva, scrittura fuori
  workspace, esecuzione di codice arbitrario, accesso ai segreti) e da un
  guscio modulabile (path autorizzati, domini di rete, binari shell
  consentiti). Il guscio si stringe e si allarga su richiesta, sempre con
  la "carta a tre righe" del dialog manager.
- **Libertà vs robustezza.** Asimmetria precauzionale: Metnos può
  proporre molto (testo, suggerimento, domanda) ma eseguire poco e solo
  dopo conferma, con gradienti diversi per categoria di azione (lettura,
  scrittura, azioni irreversibili). Mitigata da reversibilità: ogni
  azione invasiva conserva il "prima" abbastanza a lungo da poter essere
  disfatta.

Gli executor remoti — esecuzione di codice su un dispositivo diverso da
`.33` — sono direzione futura, in certi casi unica via, e richiederanno un
microdesign dedicato (l'ADR successivo sull'architettura client/server li
formalizza).

## Alternative considerate

**Cloud (VPS o serverless).** Pro: sempre raggiungibile, niente single
point of failure se il server di casa cade. Contro: tradisce la promessa
"vivere nella casa dell'utente" del capitolo 1, introduce dipendenza da
un fornitore terzo, costo ricorrente, e mette i dati personali su una
macchina che non è di Roberto. Scartata: il telos della parsimonia e
quello della sovranità dei dati lo escludono.

**Solo laptop, niente server.** Pro: zero infrastruttura. Contro: il
laptop si chiude, si muove, si scarica; tutta la proattività (cron,
scheduler, monitoraggi notturni) richiede una macchina sempre accesa. La
proattività è uno dei quattro aggettivi del capitolo P&G v1.1; senza
server non esiste. Scartata.

**Tre assi collassati in uno.** Pro: il modello mentale è più semplice,
"tutto richiede conferma". Contro: tratta una richiesta di lettura del
calendario come un'esecuzione di codice arbitrario; il risultato è o una
gate troppo larga (l'utente clicca meccanicamente "ok") o una troppo
stretta (l'utente è infastidito da ogni operazione banale). Scartata: la
distinzione fra i tre assi è la struttura che rende l'asimmetria
precauzionale credibile.

**Webhook Telegram invece di polling.** Pro: latenza più bassa. Contro:
richiede un ingress dal pubblico verso `.33` (quindi DNS dinamico, port
forwarding, certificati validi sul dominio di casa). Polling toglie un
intero strato di superficie esposta in cambio di mezzo secondo di
latenza in più. Scartata.

## Conseguenze

I 22 microdesign scritti prima di questa decisione sono diventati
obsoleti rispetto alla topologia (vedi ADR 0008 sulla microarchitettura
UNTRUSTED). Il capitolo `gateway.html` quando verrà riscritto dovrà
esplicitare che vive su `.33` e che il bot Telegram è in polling; il
`sandbox.html` va calato su Linux reale (namespace, cgroup, seccomp,
landlock); il `pairing.html` va esteso al pairing del laptop contro il
server, su cui poggia la cerimonia di pairing dei dispositivi remoti
(ADR 0011).

Restano esplicitamente aperte alcune domande che non si sono volute
chiudere preventivamente: come fare il pairing del laptop in modo
resistente a furto/perdita; come gestire il single-point-of-failure di
Headscale su `.33`; come dimensionare il bother budget delle proposte
spontanee; quanto a lungo conservare il manifest "prima" dopo un'azione
remota.
