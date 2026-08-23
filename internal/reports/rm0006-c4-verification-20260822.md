# RM-0006 — verifica C4 del 22 agosto 2026

## Esito

C4 e' completata. Tutti i 24 flussi di riferimento, nelle formulazioni
italiana e inglese, hanno attraversato un ciclo completo: 48 risultati su 48
sono conformi. Il ciclo comprende il client Rust reale collegato a un server
Metnos isolato, il motore durevole con storage e lease reali, dialoghi,
approvazioni e condizioni avverse delimitate.

La successiva certificazione C6 ripete lo stesso perimetro per due cicli ed e'
la prova persistente autorevole:
`internal/reports/rm0006-c6-certification-20260823/`.

```text
certification_id=rm0006-c4-complete-v1
evaluated_cases=48
passed=48
failed=0
errors=0
objective_achieved=true
matrix_sha256=3623f900e37e8a8fe966eb2030fc7303ac670a7202d208a007b59ea1fbf00062
results_registry_sha256=27d16faeb888195377dea4c6c994083afc548674170fa14887211f55d255a151
```

## Confini attraversati

- Il dispositivo non e' una funzione finta nel processo di test: il client
  Rust si appaia al server isolato e riceve le normali invocazioni remote.
- L'isolamento del proprietario e' verificato su storage distinti; una
  richiesta non puo' leggere o usare il dispositivo di un altro proprietario.
- Il lavoro durevole usa il compilatore, lo storage, i lease, il fencing e il
  deposito artefatti del prodotto. Sono deterministici soltanto OCR e risposta
  del modello, per non trasformare la prova logica in una misura del modello.
- La prova di arresto e ripresa interrompe uno stage interno idempotente,
  riavvia il worker sullo stesso lavoro e verifica una sola uscita per unita',
  fencing valido e assenza di elementi orfani.
- L'artefatto e' riaperto attraverso `ArtifactStore.open_registered_download`;
  l'impronta e' calcolata sui byte effettivi, non sul solo record di catalogo.

## Condizioni avverse

Revoca credenziali, timeout, limite, errore del fornitore e risultato parziale
sono iniettati ai confini comuni con limiti finiti. Nessun caso introduce un
ramo per frase, lingua, calendario, file o dispositivo. Le postcondizioni
controllano mancato ripiego, unicita' degli effetti, stato terminale e pulizia
delle risorse.

## Limite

C4 e' un ciclo diagnostico. La promozione della roadmap richiede ancora sonde
reali C5 e due cicli consecutivi C6 sulla stessa matrice congelata.
