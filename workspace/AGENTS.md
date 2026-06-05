# AGENTS

> Regole di orchestrazione: come i canali mappano ai livelli di autonomia,
> quando delegare a un sibling agent, come trattare interlocutori diversi.

## Mapping canale → livello di autonomia

| Canale | Sender fidato | Livello default | Note |
|--------|---------------|-----------------|------|
| CLI locale | Roberto su `metnos-server` | Full | LAN diretta, niente pairing necessario |
| Telegram | `chat_id=100000001` (Roberto) | Supervised | Pairing automatico (vedi cap. 12) |
| Telegram | qualunque altro chat_id | ReadOnly | Solo dopo approvazione esplicita di Roberto |
| Headscale overlay | Roberto fuori casa | Full | Stessa fiducia di LAN, transport cifrato |

## Sibling agents

Per ora: nessuno. Metnos e' agente singolo per casa.

<!-- Quando emergeranno sibling (es. agent specializzato per la cucina,
o un agent enterprise lato giorgio2), elencare qui:
- Quale dominio coprono.
- Quale canale usano per parlarsi.
- Chi ha precedenza su query ambigue.
-->

## Delega

- Domotica → assistente domotico esterno (Home Assistant), via plugin LAN.
- Voce sintetica → satelliti via myoming2 + Echo se presenti
  (vedi `voice_say` nel seed pool).
- Ricerche web → SearXNG self-hostato (porta 8888).
