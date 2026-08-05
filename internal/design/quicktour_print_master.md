# Quick Tour: master editoriale e PDF

## Sorgenti canoniche

Il testo resta in due master HTML, così ogni aggiornamento continua a essere una
normale modifica documentale e rimane leggibile anche sul web:

- `docs/it/Metnos_QuickTour.html`
- `docs/en/Metnos_QuickTour.html`

Il progetto tipografico è condiviso in
`docs/assets/quicktour-print.css`. La copertina canonica è
`docs/assets/metnos-quicktour-cover.png`; la copia nel repository ha lo stesso
SHA-256 dell'originale consegnato dall'autore.

I nomi dei master e dei PDF sono intenzionalmente stabili e non contengono un
numero di versione. La versione editoriale, quando necessaria, vive nei
metadati e nel contenuto; i vecchi URL pubblici versionati sono soltanto alias
301 di compatibilità.

Il CSS di stampa governa formato A4, copertina al vivo, font, ritmo verticale,
pagine mastro, foliazione, testatine, aperture di capitolo, vedove/orfane,
tabelle, scene, chat, figure SVG e colophon. Il CSS web rimane indipendente.

## Rigenerazione

Dal root del repository:

```bash
python3 scripts/build_quick_tour_pdf.py
```

Per una sola lingua:

```bash
python3 scripts/build_quick_tour_pdf.py it
python3 scripts/build_quick_tour_pdf.py en
```

Lo script cerca Chromium nel `PATH` e nelle installazioni Playwright locali. Su
un'altra macchina si può usare `--chrome /percorso/al/binario` oppure la variabile
`METNOS_QUICKTOUR_CHROME`. I risultati pubblici sono:

- `docs/it/Metnos_QuickTour.pdf`
- `docs/en/Metnos_QuickTour.pdf`

## Controlli editoriali prima della pubblicazione

1. Generare entrambe le lingue e verificare che abbiano lo stesso numero di pagine.
2. Controllare formato, metadati e font con `pdfinfo` e `pdffonts`.
3. Renderizzare tutte le pagine con `pdftoppm` e ispezionare copertina, giunzioni
   fra scene, tabelle, diagrammi e ultima pagina.
4. Verificare che i link Quick Tour delle landing IT/EN puntino al PDF.
5. Eseguire `./deploy.sh` solo dopo il controllo visivo.

Il Quick Tour è una fonte del Tutor F2. I passaggi che illustrano funzioni non
ancora implementate devono usare la classe HTML `tutor-exclude`: restano
visibili al lettore ma non entrano nel catalogo di conoscenza corrente.

Il PDF è un derivato rigenerabile, non una seconda sorgente: non va corretto a
mano.
