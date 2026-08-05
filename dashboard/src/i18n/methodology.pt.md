<!--
  METHODOLOGY — pt-BR. This file is the SKELETON; the copy pass writes the prose.

  STRUCTURE (binding). Each of the five sections below is introduced by an HTML
  comment holding its id, in the form "section:<id>". The five ids, in this
  order, are: sources, detectors, caveats, reproduce, citations.

  Those comments are the contract. The table of contents, and the links the
  detector pages point at (#caveats), address the ID — never the heading text —
  precisely so the headings can be rewritten freely. Reword every heading; do
  not touch, reorder, or remove a marker comment. Every section must open with
  its own "## " heading. `src/pages/Methodology/sections.ts` throws at import
  time if any of that is broken, and the page fails to load rather than
  quietly shipping without a section.

  SUPPORTED MARKDOWN — anything else is rendered as a verbatim paragraph, so
  nothing is ever lost, but it will look wrong:

    `## heading`  — one per section, and its first line
    `### heading` — subheadings inside a section
    paragraphs, separated by a blank line
    `- item` and `1. item` lists — ONE LINE PER ITEM, no wrapping
    [text](https://example.org)   `code`   **bold**   *italic*

  No tables, no block quotes, no fenced code blocks, no images, no raw HTML.

  The reference list under the last section is NOT written here: it is
  structured data in `src/pages/Methodology/citations.ts` and the page renders
  it after this file's prose.
-->

<!-- section:sources -->
## Fontes de dados

TODO (Tarefa 22): de onde vêm os dados, qual é o recorte desta publicação e o
que exatamente cada arquivo oficial contém.

TODO (Tarefa 22): o que é conferido na carga — em particular o portão de
reconciliação contra os totais publicados pelo TSE — e o que acontece quando
uma linha não bate.

<!-- section:detectors -->
## Como cada indicador é calculado

TODO (Tarefa 22): a ideia comum a todos os indicadores — comparar cada seção
com sua própria vizinhança, e não com uma expectativa nacional.

### Indicadores por seção

TODO (Tarefa 22): um parágrafo por indicador que pontua seções
individualmente, com a linha de base usada e o significado do limiar.

### Triagens de distribuição

TODO (Tarefa 22): um parágrafo por triagem que descreve uma distribuição
inteira em vez de pontuar seções, e por que ela não produz um ranking.

<!-- section:caveats -->
## O que estes números não dizem

TODO (Tarefa 22): a postura editorial — um sinal estatístico não é prova de
fraude, e todo sinal é publicado com sua linha de base local, o tamanho da
amostra e a pontuação ou o valor-p correspondente.

TODO (Tarefa 22): os limites dos testes de dígitos aplicados a dados
eleitorais, discutindo explicitamente as objeções de Deckert, Myagkov e
Ordeshook (2011).

TODO (Tarefa 22): múltiplos testes, tamanho de amostra pequeno e o que um
achado significa quando a seção tem poucos votos.

<!-- section:reproduce -->
## Como reproduzir

Todo o processamento está no repositório do projeto e roda em uma máquina
comum, sem serviços pagos. Um estado por vez:

1. `make pipeline UF=RR` — baixa os arquivos oficiais do TSE, carrega no Postgres, roda o portão de reconciliação, executa os detectores e escreve os arquivos estáticos que este site lê.
2. `make pipeline-all` — o mesmo para todas as unidades federativas.
3. `make test-pipeline` e `make test-dashboard` — a suíte de testes do processamento e a do site.

O repositório está dividido em três partes: `pipeline/` (download, carga,
detectores e exportação, em Python), `dashboard/` (este site) e `data/` (os
arquivos brutos e a exportação publicada). Cada arquivo publicado leva o hash
do próprio conteúdo no nome e é listado em `data-manifest.json`, de modo que
uma exportação nunca mistura arquivos de execuções diferentes.

TODO (Tarefa 22): revisar o texto acima e acrescentar o que falta ao leitor
que não vai rodar nada, mas quer saber que é possível.

<!-- section:citations -->
## Referências

TODO (Tarefa 22): uma frase apresentando os trabalhos abaixo e o que cada um
contribui para os indicadores desta página.
