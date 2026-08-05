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
## Dados oficiais, recorte e integridade

O observatório usa os dados abertos do Tribunal Superior Eleitoral (TSE) para 2022. O conjunto inclui os resultados por seção eleitoral — a unidade atendida por uma urna — de todos os cargos nos dois turnos, além dos arquivos de detalhe por seção com aptos, comparecimento, abstenção, votos nominais e de legenda, brancos e nulos. O recorte exibido depende da exportação carregada: nesta etapa, somente Roraima foi processado; nenhum resultado da página deve ser lido como conclusão nacional.

Os arquivos brutos ficam congelados depois do download e podem ser baixados novamente das fontes do TSE. Cada arquivo publicado pelo projeto recebe no nome um hash calculado sobre seu conteúdo e aparece em `data-manifest.json`. Assim, é possível identificar exatamente os dados usados, repetir a carga e impedir que uma página combine arquivos de execuções diferentes.

Antes da análise, um portão de reconciliação soma os dados por município, cargo e turno e compara os totais com as totalizações oficiais do TSE. Também confere, dentro de cada seção, os campos de votos e estatísticas correspondentes. Diferenças são registradas integralmente para diagnóstico; uma divergência que não esteja prevista e explicada interrompe a publicação, em vez de ser ajustada ou descartada em silêncio.

<!-- section:detectors -->
## Como os indicadores funcionam

Os indicadores procuram resultados incomuns em relação a uma referência adequada ao fenômeno medido. Sempre que há pontuação por seção, a página publica a linha de base, o tamanho da amostra e a pontuação ou probabilidade correspondente. Uma pontuação alta significa maior distância dessa referência; não converte uma diferença estatística em explicação sobre sua origem.

### Consistência aritmética e presença entre cargos

A consistência aritmética verifica, em cada seção, cargo e turno, se votos nominais, de legenda, brancos e nulos somam o comparecimento, se o comparecimento não supera os aptos e se não há valores negativos. Cada regra violada vale um ponto; campos ausentes são excluídos e contabilizados à parte. A presença entre cargos deriva dos próprios arquivos a lista esperada para cada estado e turno e pontua quantos cargos faltam em uma seção que registrou os demais.

### Dominância extrema

Para os candidatos a presidente com pelo menos 1 % dos votos nacionais no turno, o indicador calcula a probabilidade de uma votação ao menos tão extrema quanto a observada. A referência beta-binomial usa as outras seções do município, deixando a seção avaliada de fora, e admite sobredispersão natural; com menos de cinco seções de referência, usa o estado. A pontuação é `−log10` da menor probabilidade de cauda e o detalhe mostra candidato, votos `k`, votos nominais `n`, participação esperada, correlação e as duas caudas.

### Divergência entre cargos

No primeiro turno, este indicador subtrai da participação de cada um dos dois principais presidenciáveis a participação, para deputado federal, dos partidos de sua coligação na mesma seção. A diferença é comparada à mediana do próprio município com um z robusto baseado no MAD, a mediana dos desvios absolutos; municípios com menos de dez seções usam a referência estadual. A pontuação é metade do valor absoluto desse z, de modo que padrões de aliança comuns a todo o município não sejam tratados como desvios locais.

### Comparecimento atípico

O comparecimento é `comparecimento ÷ aptos` em cada seção presidencial. O indicador mede a distância até a mediana municipal por um z robusto, usando `1,4826 × MAD` como escala e considerando tanto valores altos quanto baixos; a pontuação é `|z| ÷ 2`. Se houver menos de dez seções ou MAD igual a zero, a referência passa a ser o estado. O detalhe publicado identifica a proporção observada, mediana, MAD, z e linha de base.

### Indicador composto e concentração

Para cada seção e turno, as pontuações de consistência aritmética, presença entre cargos, dominância extrema, comparecimento e divergência entre cargos são convertidas em percentis de posto médio dentro de cada indicador. O composto é a média dos percentis disponíveis quando existem pelo menos dois; caso contrário, não é calculado. O 1 % de maior sinal é então testado quanto à concentração em zonas e municípios por uma distribuição hipergeométrica, com correção de Benjamini-Hochberg a `q = 0,05` dentro de cada família de testes.

### Assinatura de votação

Esta triagem constrói, por estado e turno presidencial, uma grade de 100 × 100 células entre o comparecimento e a participação do vencedor nacional nos votos válidos. A massa no canto é a fração de seções acima de 90 % nos dois eixos, uma síntese da assinatura descrita por Klimek e coautores. O resultado descreve a distribuição estadual e não produz pontuação nem ranking de seções.

### Distribuição de dígitos

Para os dois candidatos mais votados à Presidência, o segundo dígito das contagens com ao menos dez votos é comparado à distribuição de Benford; o último dígito das contagens com ao menos cem votos é comparado à distribuição uniforme. Os testes qui-quadrado são separados por estado e por tamanho do município. Cada estrato informa `n`, frequências, χ² e valor-p, ou explica por que a amostra foi insuficiente; não há pontuação por seção.

### Modelos de urna

A comparação de modelos usa o segundo turno presidencial e somente municípios com urnas anteriores a 2020 e urnas do modelo 2020. Ela estima, dentro de cada município, a diferença na participação de Bolsonaro entre as gerações e combina essas diferenças com ponderação por votos e intervalo de confiança de 95 % robusto por município. A diferença bruta também aparece para mostrar o efeito da geografia, mas não é o achado; esta é outra análise de distribuição, sem pontuação por seção.

<!-- section:caveats -->
## Limites da leitura estatística

Um sinal estatístico não é prova de fraude, assim como a ausência de sinal não atesta que nada irregular ocorreu. O observatório mostra onde um resultado se afasta da linha de base escolhida e com que intensidade. A interpretação requer o contexto da seção, o tamanho da amostra e a pontuação ou o valor-p. Quando um teste não foi calculado, a página diz isso de modo distinto de um teste executado no qual nada ultrapassou o limiar.

Deckert, Myagkov e Ordeshook (2011) mostram que testes eleitorais baseados na lei de Benford podem gerar tanto falsos positivos quanto falsos negativos e são sensíveis à maneira como as seções são dimensionadas e os distritos são formados. Por isso, uma reprovação nos testes de segundo ou último dígito indica apenas incompatibilidade com a distribuição de referência naquele estrato. Não é evidência de fraude, e um valor-p alto tampouco funciona como certificado de regularidade.

Há cerca de 470 mil seções no país e várias comparações por seção. Mesmo que todas sigam os modelos, algumas terão pontuações altas por acaso. Na verificação de concentração do conjunto composto, os valores-p de zonas e municípios são corrigidos pelo procedimento de Benjamini-Hochberg, com taxa de falsas descobertas `q = 0,05`; as demais listas são ordenações de triagem, não testes que prometem uma taxa global de erro.

Seções com poucos eleitores produzem proporções mais instáveis, e referências municipais pequenas podem representar mal a variação local; por isso os detalhes exibem `n` e identificam quando foi necessário recorrer ao estado. Anomalias também têm explicações corriqueiras com muito mais frequência que explicações extraordinárias: composição do eleitorado, alianças locais, agregação administrativa, erros de registro e diferenças geográficas são hipóteses a verificar, não desculpas automáticas nem conclusões antecipadas.

<!-- section:reproduce -->
## Reprodução da análise

Todo o processamento está no repositório do projeto e roda em uma máquina comum, sem serviços pagos. Os comandos abaixo baixam arquivos oficiais, constroem a base, executam as verificações e geram exatamente os arquivos estáticos consumidos pelo site:

1. `make pipeline UF=RR` — executa o fluxo completo para um estado, aqui exemplificado por Roraima.
2. `make pipeline-all` — repete o mesmo fluxo para todas as unidades federativas.
3. `make test-pipeline` e `make test-dashboard` — executam, respectivamente, os testes do processamento e do site.

O repositório se divide em `pipeline/`, com download, carga, detectores e exportação em Python; `dashboard/`, com este site; e `data/`, com arquivos brutos e a exportação publicada. Durante a carga, o portão de reconciliação compara as somas reconstruídas com os totais do TSE e interrompe o fluxo diante de diferenças não explicadas. Depois da exportação, os arquivos são relidos e validados antes de poderem ser publicados.

Não é necessário executar o código para auditar a procedência de uma página. O `data-manifest.json` enumera a versão da estrutura e os nomes com hash de conteúdo, permitindo associar a exportação a uma execução sem depender da data ou de um arquivo mutável. Quem repetir o fluxo com os mesmos arquivos congelados pode conferir os totais, examinar os parâmetros registrados pelos indicadores e comparar os artefatos produzidos.

<!-- section:citations -->
## Referências e contribuições

Os trabalhos listados a seguir fundamentam a modelagem beta-binomial, a assinatura de comparecimento e votação, os testes de dígitos e suas limitações; cada referência indica o ponto específico que oferece à metodologia, sem transferir suas conclusões para os dados aqui analisados.
