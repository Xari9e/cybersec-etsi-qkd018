# Paper: ETSI GS QKD 018

Due versioni dello stesso paper, ognuna è un progetto LaTeX autonomo:

- `it/`: versione italiana
- `en/`: versione inglese

Struttura di ciascuna cartella:

```
main.tex            documento principale
preamble.tex        pacchetti, font (Cambria Math), ambienti
abstract/           abstract
chapters/           un file per sezione
figures/            figure TikZ
bibliography/       references.bib (biblatex + biber)
```

## Compilazione

Serve **XeLaTeX** (o LuaLaTeX), non pdfLaTeX, perché il font matematico
Cambria Math è caricato con `unicode-math`. Se Cambria Math non è installato
(per esempio su Overleaf) si usa automaticamente Latin Modern Math.

```
cd it            # oppure: cd en
latexmk -xelatex main.tex
```

oppure a mano:

```
xelatex main.tex
biber main
xelatex main.tex
xelatex main.tex
```

Su Overleaf: carica il contenuto di `it/` o `en/` come progetto e imposta
Menu → Compiler → XeLaTeX.
