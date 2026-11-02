# Diagrammi UML ed E-R: ETSI GS QKD 002, 015, 018

PDF pronti: `qkd_uml_diagrammi_it.pdf`, `qkd_uml_diagrams_en.pdf`.

| # | File (figures/) | Contenuto |
|---|---|---|
| 1 | `uc_002.tex` | QKD 002: casi d'uso UC1–UC6 (cl. 7) |
| 2 | `cls_002.tex` | QKD 002: modello concettuale (cl. 4, 6, 7) |
| 3 | `cls_015.tex` | QKD 015: classi di `etsi-qkd-sdn-node` |
| 4 | `enum_015.tex` | QKD 015: identity/typedef di `etsi-qkd-node-types` |
| 5 | `er_015.tex` | QKD 015: schema E-R (vista del controller) |
| 6 | `cls_018_topo.tex` | QKD 018: topology + augment di inventory |
| 7 | `cls_018_conn.tex` | QKD 018: connectivity, RPC, notifiche |
| 8 | `er_018.tex` | QKD 018: schema E-R (vista dell'orchestratore) |
| 9 | `map_002_015_018.tex` | Tracciabilità 002 → 015 → 018 |

Compilazione: `lualatex main_it.tex` (o `main_en.tex`), due passate. Le figure sono TikZ puro
(stili in `uml_styles.tex`); la lingua si sceglie con `\langittrue`/`\langitfalse`.
Per includerne una nel paper: `\input{uml_styles}` nel preambolo e, nel testo,
`{\catcode`\_=12 \input{figures/cls_015}}`.

Fonti: tutti i moduli YANG sono stati verificati con `pyang -f tree` sulle copie in
`Bibliography/` (015 rev. 2022-01-30 da `yang_qkd015/`; 018 rev. 2022-02-22 da
`gs018-orch-int-sdn-main/`). Il corsivo nei diagrammi indica solo annotazioni (unità, commenti).
