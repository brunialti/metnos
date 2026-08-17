# Self-review candidate v0.2.1

- autorità dati: tutti e soli i 26 record `grandfathered` RFC 5646/IANA;
- lookup case-insensitive, nessun branch per lingua e nessuna allowlist moderna;
- controllo ASCII/alnum/trattino prima del casefold: confusabili Unicode non
  possono trasformarsi in un tag grandfathered registrato;
- `Preferred-Value` normalizzato dal parser v0.2; grafia IANA conservata quando
  non esiste un sostituto;
- tag moderni, regionali, script, estensioni e private-use percorrono invariati
  il parser v0.2; varianti o singleton duplicati restano respinti;
- nessuna query, ID, frammento, hash o risultato del banco;
- zero GPU, rete, produzione o repair.
