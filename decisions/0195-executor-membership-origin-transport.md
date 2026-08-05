---
id: 0195
title: Appartenenza, origine e trasporto degli executor sono assi distinti
date: 2026-07-19
status: accepted
area: executor | catalog | skills
related: [0013, 0123, 0141, 0160, 0170, 0193]
---

# 0195 - Appartenenza, origine e trasporto degli executor

## Contesto

Il termine `builtin` era usato sia per indicare una capability mantenuta e
distribuita da Metnos, sia come valore `source` dei soli handler in-process.
Questa sovrapposizione faceva apparire i 16 executor GitHub come importati solo
perche' il bundle riusa il layout delle skill installate e il codegen nato per
gli import di terze parti.

La posizione su disco, il provider e il trasporto non determinano l'autorialita'.
I 16 `*_github` sono progettati, mantenuti e validati da Metnos: sono builtin e
la loro origine e' handcrafted.

## Decisione

Catalogo e documentazione trattano separatamente:

1. **appartenenza**: builtin Metnos oppure third-party;
2. **origine**: handcrafted, synthesized oppure imported;
3. **trasporto**: in-process, subprocess locale, locale/remoto, device remoto o
   un futuro adapter MCP.

I 16 executor GitHub dichiarano `origin = "handcrafted"`, non contengono
`[provenance].imported_from`, hanno autore `Metnos builtin maintainers` e
continuano a usare `provider:access = github`. Il codegen applica la stessa
identita' a ogni futura rigenerazione del bundle GitHub. Gli import reali di
terze parti conservano invece la provenienza e l'origine `imported`.

I 17 handler runtime conservano contratti firmati on-disk e trasporto
`in-process`. Per il Composer non costituiscono una grammatica speciale: nome,
schema, authority, output e standard sono gli stessi degli executor
handcrafted. Il trasporto viene risolto dopo la composizione.

## Evidenza alla decisione

- inventario live: 115 executor, zero rejected;
- firme valide: 115/115;
- standard dichiarato e lint strutturale: 115/115, zero finding;
- origine osservata: 98 handcrafted e 17 runtime builtin in-process;
- GitHub: 16/16 handcrafted, `is_imported=false`, 64/64 birth test offline;
- due cicli consecutivi senza edit intermedi: runtime 4368 passed, 32 skipped,
  354 subtest; E2E isolato 24 passed, 5 skip dichiarati; birth core 348/348.

Questi dati attestano i gate elencati, non un errore zero universale e non
autorizzano il cutover definitivo.

## Conseguenze

- Un nome provider come `_github` non prova mai un'importazione.
- Il Composer non preferisce un executor perche' in-process.
- Report futuri dovrebbero esporre colonne separate per appartenenza, origine e
  trasporto; il campo combinato `source` resta una compatibilita' transitoria.
- Una skill first-party puo' riusare il substrate delle skill senza diventare
  third-party o imported.
