# RM-0008/F5-F6 — verifica e ripresa del 15 settembre 2026

**Esito: F5/F6 non sono ancora certificabili.** La verifica indipendente
trova componenti isolati ma non tutti i collegamenti di esercizio richiesti.
Il [rapporto puntuale](certification-status.md) distingue codice esistente,
parti mancanti e prove necessarie. Non e un attestato di autorizzazione.

## Base e prove

La copia isolata RM-0009 incorpora il seguito MIT/pubblicazione RM-0008
`5c1220ac25a1899b0d88aac5540fb0ab913db5ea`; commit di integrazione
`09a58c7cf33e2610ce1506b129db3b3d4a37b735`. Runtime, executor, client,
installer e test di prodotto coincidono con quel seguito: questa fotografia
non contiene ancora nuova implementazione F5/F6.

- **244 test superati**, zero fallimenti e zero salti: 137 dei preparatori
  RM-0009, 107 delle fondamenta RM-0008, in ambiente isolato Linux.
- Controlli statici della proiezione amministrativa e del confine
  `--birth-closed` superati; non sono una prova di integrazione produttiva.
- Inventario DATA ricontrollato: 110 input, 57 store, 8 ingressi di crescita
  e 3 produttori TurnLog; versioni ricavate dal sorgente, non da DB installati.
- Inventario sicurezza ricontrollato: 2411 input, 33 gruppi FS-A, 13 FS-B,
  10 percorsi F6; due scansioni identiche.
- Il vecchio inventario DATA viene correttamente rifiutato contro la nuova
  copia per nove digest diversi. Il controllo dei nuovi inventari lega
  soltanto i byte al commit, non certifica completezza o significato.

Comandi esatti, risultati, digest e limiti sono in [verification.json](verification.json).
I file `inventory-data.json` e `inventory-security.json` conservano anche
conteggi e dettagli delle scansioni; i rispettivi `.md` ne spiegano l'esito.
I precedenti rapporti `../20260915/` e `../20260915-consolidated/` restano
fotografie immutate. G0.5-G0.10 non sono dichiarati conclusi; nessun documento
normalizzato e stato approvato o sostituito.

## Riproduzione

Usare la copia `/opt/metnos/.claude/worktrees/rm0009-development` e i
comandi registrati in `verification.json`. Per ogni nuova corsa pytest
creare dati/configurazione inizialmente vuoti sotto `/tmp` e assegnare
soltanto `METNOS_USER_DATA` e `METNOS_USER_CONFIG`: non riutilizzare i dati
installati o copiare chiavi reali. Il bootstrap della suite prepara chiavi e
archivi effimeri. Nessun runner legacy e necessario.

I controlli degli inventari richiedono `--repository` e
`--expected-commit` espliciti. Prima di interpretare un esito positivo
leggere `scope`, `certifies_completeness=false` e
`authorizes_implementation=false`. Le scansioni sono fissate ai blob del
commit; modificare il prodotto richiede un nuovo inventario, non correggere
retroattivamente questo.

## Incarico successivo e limite concordato

Roberto ha autorizzato: «Completa anche RM0008 F5/F6», poi ha richiesto di
attendere alla conclusione per una revisione esterna. Il coordinatore
prosegue quindi su RM-0008 nella copia isolata, seguendo i gruppi 8, 9, 10 e
11 e le loro condizioni d'uscita. La transizione F4 gia verificata non va
ripetuta. Non si attivano funzioni o cancellazioni con prove sintetiche;
eventuali nuove autorita o modifiche normative restano soggette al §17.

Alla conclusione di F5/F6 il lavoro si ferma. RM-0009 non riparte
automaticamente: serve il successivo via libera di Roberto. Nessun servizio,
dato installato, chiave o autorita e stato cambiato da questa verifica.
