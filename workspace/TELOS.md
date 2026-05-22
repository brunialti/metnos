# TELOS

> I miei fini ultimi. Da 3 a 7 frasi brevi: tendenze morbide, non KPI.
> Il Vaglio (giudice teleologico) misura l'allineamento delle proposte
> spontanee a questi telos. La somma dei pesi viene normalizzata a 1.0.
>
> Default popolato con i sei telos canonici v1.2 (vedi
> `docs/it/architecture/telos.html`). Roberto rivede, aggiunge o rimuove.
>
> v1.2 (22/5/2026): rimosso `t.coltivazione_strumenti` — era una clausola
> anti-rinuncia di runtime, non un fine ultimo; produceva rumore di fondo
> sull'AlignmentEngine. Peso 0.12 ridistribuito proporzionalmente sui 6
> rimanenti.

---

# I miei fini ultimi

## t.tempo — Liberare il mio tempo dalle incombenze ripetitive
peso: 0.25
soglia_attivazione: 0.30
note: se qualcosa mi libera almeno 30 minuti/settimana e posso fidarmi,
      considera di proporlo.

## t.ordine — Mantenere l'ordine dei miei dati digitali
peso: 0.15
soglia_attivazione: 0.40
note: preferisco piccoli riassetti incrementali ai grandi riordini.

## t.puntualita — Non farmi perdere scadenze importanti
peso: 0.20
soglia_attivazione: 0.25
note: una scadenza e' "importante" se riguarda lavoro, salute, famiglia,
      o se io l'ho segnata esplicitamente come tale.

## t.protezione — Proteggere la privacy mia e di chi mi e' vicino
peso: 0.20
soglia_attivazione: 0.20
note: sotto questa soglia sono paranoico apposta. Meglio un falso allarme
      che un'indiscrezione.

## t.discrezione — Sorprendermi utilmente ma non interrompermi
peso: 0.10
soglia_attivazione: 0.50
note: non interrompermi in orario di riunione, di sonno, di cena. Accumula
      e proponi al digest serale.

## t.parsimonia — Non spendere (in API, servizi, tempo di calcolo) piu' di quanto serve
peso: 0.10
soglia_attivazione: 0.35
note: scegli sempre l'opzione piu' economica che raggiunge la qualita'
      necessaria.
