# RM-0002 L4 — Rapporto dinamico di osservazione

> Misura eseguita il `2026-08-25` sulla revisione
> `9e743ca9a07cad7960be67a033fb8dec1b01c99b`. Questo documento registra una
> fotografia storica: i numeri non sono requisiti né costanti di prodotto.

## Scopo e metodo

Il rapporto è stato prodotto in sola lettura con l'inventario neutro di
`runtime/manifest_inventory.py` e con le API pubbliche del linter. Per ogni
manifest sono state controllate separatamente tutte le lingue presenti; ogni
tabella localizzata è stata poi confrontata a coppie. Il controllo di
`affinity` è stato eseguito una sola volta per manifest e non è stato esteso.

La prova non ha modificato manifest, firme, registro i18n o stato delle skill.
L'inventario non ha rilevato problemi di scoperta, alias, collisione o parsing.

## Inventario osservato

| Origine | Stato | Manifest | Varianti `en` | Varianti `it` |
|---|---|---:|---:|---:|
| core | admitted | 85 | 85 | 85 |
| builtin | admitted | 21 | 21 | 21 |
| user skill | admitted | 16 | 16 | 16 |
| retired | retired | 1 | 1 | 1 |

Il materializzatore conserva il proprio confine di autorità: enumera con lo
stesso inventario soltanto le radici configurate per la localizzazione. Le
skill utente restano visibili in questo audit, ma non sono state aggiunte alla
promozione automatica né modificate.

## Errori deterministici

### Catalogo mantenuto dal progetto

Un solo errore certo:

| Executor | Regola | Evidenza `en` | Evidenza `it` | Decisione |
|---|---|---|---|---|
| `set_signatures` | `pattern_atoms` | `set_signatures(kind,reason,signature)` | `set_signatures(kind,signature)` | allineare il pattern italiano aggiungendo `reason`; pubblicare e rifirmare soltanto attraverso RM-0007 |

Il precedente falso positivo di `get_images_google_photos` non ricorre. Lo
scanner si astiene correttamente dalla parentesi in prosa «Google UI (link…)» /
«UI Google (link…)» e riconosce soltanto la forma macchina adiacente
`identificatore(`. Una fixture specifica protegge questo confine.

### Pacchetti utente osservati

I rilievi seguenti sono separati perché il progetto non possiede quelle
sorgenti e L4 vieta una bonifica automatica degli import:

| Executor | Lingue | Regola | Evidenza |
|---|---|---|---|
| `list_dirs_github` | `en`, `it` | `pattern_unknown_arg` | il pattern usa `path`, lo schema dichiara `paths` |
| `send_messages_github` | `en`, `it` | `pattern_unknown_arg` | il pattern usa `body_template` e `target_template`, lo schema dichiara `body` e `target` |

Questi errori restano visibili nell'audit e non autorizzano né una modifica
dei pacchetti né eccezioni nominali nel linter.

## Avvisi e prestazioni

Sono stati osservati 230 avvisi editoriali: 208 nel catalogo core e 22 nei
builtin. Gli avvisi mantengono la loro gravità effettiva anche con
`--strict`; l'opzione modifica soltanto l'esito del gate e non falsifica il
rapporto.

Trenta esecuzioni complete consecutive, con file già disponibili sul
filesystem locale, hanno misurato:

| Campioni | Mediana | p95 | Massimo | Limite di RM-0002 |
|---:|---:|---:|---:|---:|
| 30 | 147,00 ms | 149,74 ms | 156,73 ms | 500 ms |

## Esito L4 in osservazione

- l'inventario comune copre le topologie configurate senza concedere autorità;
- il corpus mantenuto contiene una sola divergenza certa da bonificare;
- i difetti dei pacchetti utente restano separati e non sono nascosti;
- non servono allowlist, nomi cablati o regole specifiche per lingua;
- la parte mutante di L4 resta sospesa fino alla pubblicazione verificata di
  RM-0007.

