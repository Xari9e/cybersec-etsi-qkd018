# Slide Beamer: ETSI GS QKD 018

Due versioni della presentazione, tratte dal paper in `../paper/`:

- `it/`: versione italiana → `qkd018_slides_it.pdf`
- `en/`: versione inglese → `qkd018_slides_en.pdf`

Stile Beamer classico: tema standard `Warsaw` con i suoi colori di
default, formato 4:3, 10pt, indice all'inizio di ogni sezione. Le figure in
`figures/` sono le stesse figure TikZ del paper, estratte dall'ambiente
`figure` e ridimensionate con `\slidefig`.

Struttura di ciascuna cartella:

```
main.tex            le slide
preamble.tex        tema, font, listati, stili TikZ
figures/            figure TikZ del paper
bibliography/       references.bib (copia di quella del paper)
```

## Compilazione

Si compila con pdfLaTeX normale (bibliografia con biber):

```
cd it            # oppure: cd en
latexmk -pdf main.tex
```

oppure `pdflatex main.tex ; biber main ; pdflatex main.tex ; pdflatex main.tex`.
Funziona anche con XeLaTeX/LuaLaTeX: in quel caso la matematica usa Cambria
Math, se installato. Su Overleaf va bene il compilatore di default.
