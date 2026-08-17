# Revisione indipendente del risigillo post-apertura gold

Data: 13 agosto 2026  
Perimetro: sola lettura delle fonti canoniche, del nuovo referto e dei dati
della misura già conclusa. Nessuna valutazione, punteggio, rete, GPU, endpoint
o servizio è stato eseguito.

## Esito

**PASS — ready_for_offline_evaluation.**

Il risigillo è limitato alla modifica autorizzata. Tra le fonti canoniche è
cambiato soltanto
`intent_shadow_oracle_v0_1.freeze.json`; il nuovo
`internal/design/referto_risigillo_post_gold_intento_13_8_2026.md` è esterno
alle 23 fonti sigillate.

## Verifiche indipendenti

- Ricostruendo il freeze precedente mediante i soli due valori dichiarati
  (impronta dell'handover e lock), il suo SHA-256 torna esattamente
  `3dd123caf0516f0f553c85ee9d62689a94d5b740b87f05d3572d8be18b3f552e`.
  Questo dimostra che nessun altro campo del freeze è cambiato.
- I primi 64.439 byte dell'handover corrente hanno ancora SHA-256
  `71c7c6dbd9b25b5e85ff4331491ebfd8cebbf21351666cc1def8a7cb0ef94da4`;
  i 1.573 byte successivi sono un'estensione in coda. L'impronta dell'intero
  file corrente è
  `eb0fe7533eb85ff4906e82a21eea11ad9f1cd551743a3fb27277bb0bf0469bd8`
  ed è quella registrata nel nuovo freeze.
- Il nuovo lock canonico è
  `f04c69e03f211833dfc6faa3a6de26b0dc0d5298694b0485cc0b8989b09990dd`;
  il freeze corrente ha SHA-256
  `eb9d051af8cee705536348ac602f7888595e5046937ec2ef09a632fddba63a03`.
- Il verificatore canonico restituisce `error_count=0`; tutte le 23 fonti
  esistono e le loro impronte coincidono, 23/23.
- La suite respinge 107/107 mutazioni negative e accetta 6/6 riordini
  semanticamente neutri.
- L'oracolo JSON è invariato: SHA-256 file
  `3e0d1155fad7b2920b3b04bb6e617747849c8fe4a1abfea5920f1b51dba6a123`
  e payload
  `2cdec2776fa48594db8729ec1aa17a635956a61f64b2ce7ad00aa32f8cc0427f`.
  Di conseguenza anche tutti gli `expected` sono invariati.
- I dati della misura conservano le impronte precedenti: batch
  `eac40d320a8e2d98b0a11ab2ed652cf9ecc64b4893fb2e658a9bbde50143ca0b`,
  sigillo batch
  `8f58208fd6082b6fafda953882356c3f4d0084cf183aefeeaafb8b08847cde4c`
  e journal
  `403135c52aff5852e782f6549bc55f0f08d64b6a92e7a8fb4b9c6374c7515c4e`.

## Conclusione

Non emerge corruzione, ampliamento dell'autorità o modifica dei risultati
attesi o misurati. La seconda esecuzione del solo valutatore offline può
procedere usando il risigillo approvato; non richiede e non autorizza una
nuova misura GPU.
