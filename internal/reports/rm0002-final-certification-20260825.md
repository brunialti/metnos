# RM-0002 — Certificazione finale L0-L6

> Data `2026-08-25` · revisione candidata
> `8746c4f3c5f1b285c1f9d5c1df99b1e57e7ce665` · esito `green`

## Esito

RM-0002 ha completato L0-L6. La lingua è esplicita, il controllo usa il
validatore reale e lo stesso inventario strutturale dei percorsi di
pubblicazione. Gli errori deterministici bloccano prima del commit; gli avvisi
editoriali restano informativi.

## Inventario e linter

| Misura | Esito |
|---|---:|
| manifest censiti | 123 |
| varianti linguistiche controllate | 246 |
| problemi di inventario | 0 |
| errori deterministici | 0 |
| avvisi non bloccanti | 230 |

Trenta esecuzioni sull'inventario completo hanno prodotto codice di uscita
verde in ogni campione: mediana `150,48 ms`, p95 `154,45 ms`, massimo
`167,44 ms`, entro il limite di `500 ms`.

## Routing

I due cicli consecutivi sul corpus congelato sono identici:

| Ciclo | Valutabili | Top-1 | Top-3 | Top-10 | Esito |
|---:|---:|---:|---:|---:|---|
| 1 | 223 | 96 | 154 | 185 | green |
| 2 | 223 | 96 | 154 | 185 | green |

## Certificazione condivisa

| Gate | Esito |
|---|---|
| suite runtime completa: 7.260 test e 1.166 subtest | green |
| prove portabili: 12 test | green |
| controlli documentali interni: 28 test | green |
| client Windows/Rust: 93 test | green |
| installazione di riferimento e catalogo 122/122 | green |
| secondo ciclo produttivo e readiness centralizzata | green |

Tutti i criteri di completamento della roadmap sono soddisfatti. Non restano
attività RM-0002.
