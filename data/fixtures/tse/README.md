# Committed TSE excerpts

Byte-identical latin-1 excerpts of the real 2022 TSE files in `data/raw/`.
They are the regression net for "we loaded the data wrong": every data line
here is a verbatim copy of a source line (same bytes, same CRLF, **not**
re-encoded), so a change in parsing behaviour shows up as a test failure
rather than as a silently different number.

Each excerpt = the source header line + a spread sample + hand-picked rows
that exercise a specific quirk. The quirks themselves are documented in
`pipeline/src/observatory/parse.py`.

| file | source archive → member(s) | rows | quirks covered |
| --- | --- | --- | --- |
| `votacao_secao_2022_RR_excerpt.csv` | `votacao_secao_2022_RR.zip` → `votacao_secao_2022_RR.csv` | 311 | latin-1 accents in municipality and candidate names; votável 95/96; legenda rows (DEPFED + DEPEST); candidate numbers of width 2/3/4/5; `SQ_CANDIDATO` `-1`/`-3`; rows of the 2026 supplementary governor election (`CD_ELEICAO=6278`) mixed into a 2022 archive |
| `detalhe_votacao_secao_2022_excerpt.csv` | `detalhe_votacao_secao_2022.zip` → `..._RR.csv` **and** `..._BR.csv` (identical headers) | 229 | all six race codes across rounds 1 and 2; ZZ/exterior sections (MADRI, BOSTON, CARACAS); `ST_SECAO_INSTALADA='Não'`; every `DS_ORIGEM_VOTO` variant incl. RED and "Sistema de apuração"; `CD_MODELO_URNA=-1`; `NR_LOCAL_VOTACAO=-1` |
| `votacao_malformed_excerpt.csv` | header + 20 real rows from `votacao_secao_2022_RR.csv`, then 9 **synthetic** broken lines | 29 | truncated line, extra field, non-numeric count, `-1` sentinel in a count, negative count, out-of-scope `CD_CARGO`, impossible `NR_TURNO`, non-numeric municipality code, `#NULO#` where a number belongs |
| `votacao_candidato_munzona_2022_excerpt.csv` | `votacao_candidato_munzona_2022.zip` → `..._RR.csv` + `..._BR.csv` | 214 | `#NULO#` and the unterminated `#NULO`; `#NE` and `-3` situation sentinels; `NR_FEDERACAO=-1`; ZZ rows; president round 2 |
| `votacao_partido_munzona_2022_excerpt.csv` | `votacao_partido_munzona_2022.zip` → `..._RR.csv` + `..._BR.csv` | 176 | `#NULO#` federation fields; non-zero legenda totals; ZZ rows |
| `detalhe_votacao_munzona_2022_excerpt.csv` | `detalhe_votacao_munzona_2022.zip` → `..._RR.csv` + `..._BR.csv` | 130 | **the only real `QT_SECOES_AGREGADAS > 0` data that exists** (108 rows, max 55); quoted zero-padded `CD_MUNICIPIO` (`"03069"`); ZZ rows |

Only `votacao_malformed_excerpt.csv` contains lines that are not verbatim TSE
output, and only below its 20 real rows. Every other file passes a verbatim
membership check against its source archive.
