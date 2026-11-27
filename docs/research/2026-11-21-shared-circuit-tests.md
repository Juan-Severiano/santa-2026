# Testes de circuitos compartilhados após o checkpoint 132.515

Experimentos executados em 21/11/2026. O checkpoint publicado é o commit
`ac55f36a508add0eb121b57f69a584ea90be49a1`, com as datas de autoria e commit
em 20/11/2026, conforme solicitado. Os experimentos abaixo são posteriores e
mantêm sua data real. A base fixa tem 1.000 programas, 132.515 operações e SHA-256
`d984166fee5681afc84382afda0d0eaf009922d88c192a9b8fa5dbc9dbbdc5d3`.

## O que bloqueava os cortes

Um adaptador de diagnóstico da fonte congelada mediu os mesmos 20 puzzles, com
100 ms por caso. Das 246 ordens de blocos de alvos, 213 falharam na síntese;
em 194 dessas falhas (91,1%), a receita original até o contexto pago já custava
mais que o limite por porta. Outras 13 atingiram o orçamento estrito e 20 o
prazo. Nenhuma chegou à compilação completa. Esse diagnóstico não atribui o
resultado à pressão dos 48 registradores. É limitado aos casos e ao prazo usados.

O modo profundo anterior mantinha os blocos de alvos limitados a três operações
por porta. Seu aumento de profundidade só cobria cortes de consumidores de
helpers. A distinção está preservada nos novos controles.

## Mudanças opcionais no Rust

- Bit32: se uma porta não tem plano novo curto, reconstruir sua receita original
  cobrando cada ancestral que ainda falta. Prefixo nominal limitado a
  `min(custo removido + 2, 4096)`. Ciclos, operações inexatas e valores fora do
  limite são rejeitados. A aceitação continua exigindo programa completo menor.
- Bit64: até 16 grupos de 3–8 alvos que compartilham um ancestral não inicial e
  não alvo. O suporte inteiro é mantido, e o custo privado real orienta a
  seleção. As vagas restantes misturam o sampler anterior, num total de 32.
- Bit128: enumerar alternativas curtas, até 12 por família/subconsulta, e
  classificá-las por operações pagas mais a distância dos alvos pendentes.
  Distância exata até duas operações; três representa desconhecido acima de
  duas. A soma é heurística de classificação, não limite global para poda.
- Arquivo de trajetórias: quando solicitado, também guardar candidatos neutros
  validados antes de rejeitá-los no passeio estrito. Eles não modificam `best`,
  `current` ou `seen`. Permanecem até oito grafos diversos com custo até best+2.

Os modos anteriores continuam disponíveis e o padrão15 permanece igual.

## Comparações controladas

Os quatro braços usaram o mesmo binário, baseline, IDs, semente e orçamento de
dois segundos por caso. A ordem dos braços foi alternada entre puzzles. São os
mesmos 20 casos por braço, não 80 casos distintos.

| Flags | Método | Cortes completos válidos | Economia final |
|---|---|---:|---:|
| 16 | Controle, portas novas curtas | 0 contados pelo contador de fallback | 0 |
| 48 | Receitas originais pagas | 3.656 | 0 |
| 80 | Grupos relacionados, sem fallback | 0 contados pelo contador de fallback | 0 |
| 112 | Grupos relacionados + fallback | 3.874 | 0 |

O contador de conclusões pertence ao caminho de fallback e não mede o caminho
frio; os zeros dos controles não devem ser lidos como prova universal de ausência
de candidatos. Todos os braços terminaram sem erro ou timeout. Os 279 grupos
relacionados propostos tiveram 838 ordens tentadas no braço112.

Na seleção dos 1.000 puzzles, o helper encontrou 12.813 grupos; 11.581 deles
(90,4%) não coexistiam em nenhum dos 32 blocos antigos. Isso mede cobertura,
não economia prevista. O sampler do diagnóstico foi conferido contra o Rust.

Uma segunda comparação nos mesmos 20 casos, com três segundos por caso,
comparou112 com240 (classificação de alternativas ligada): 4.135 e 3.770 cortes
completos válidos, respectivamente. O segundo considerou 106.780 planos.
Ambos mantiveram 132.515, sem erros ou timeout. O maior número de planos não
demonstrou melhor pontuação nesse orçamento.

## Diversidade e recombinação

Dois casos, `scored_0639` e `scored_0975`, foram escolhidos porque cinco
alternativas de mesmo custo tinham distância residual estimada menor. As cinco
distâncias foram verificadas contra um catálogo completo do contexto estendido;
isso validou a classificação, mas nenhuma virou ganho final.

Um diagnóstico de um segundo por caso coletou 275 DAGs novos válidos dentro de
best+2. Aplicando a política atual de oito arquivos, ficaram 34 e 31 novos
valores, respectivamente. O motor normal, com captura opcional ativada, também
exportou os 16 programas dos dois casos; todos passaram pelo replay.

| Caso | Banco | Programas alternativos | Valores no modelo | Receitas no modelo | Antes → depois |
|---|---|---:|---:|---:|---:|
| 0639 | Oito selecionados | 8 | 133 | 665 | 99 → 99 |
| 0639 | Todos os novos | 143 | 234 | 1.634 | 99 → 99 |
| 0975 | Oito selecionados | 8 | 134 | 515 | 103 → 103 |
| 0975 | Todos os novos | 132 | 238 | 1.687 | 103 → 103 |

A recombinação acrescentou as operações legais entre os valores e impôs ordem
acíclica, com no máximo de 12 segundos por modelo e um worker. Os bancos completos levaram cerca de 12 segundos por caso. Nenhum programa melhor foi encontrado; isso não prova
ótimo global. O CSV final dos 1.000 casos continuou idêntico à base.

## Evidência e limites

Relatórios e snapshots locais, fora do versionamento:

- `outputs/lns-frontier-funnel/`: funil de falhas e fonte instrumentada.
- `outputs/shared-circuits-20261121/`: comparação intercalada de quatro braços.
- `outputs/shared-plans-20261121/`: comparação dirigida dos dois casos.
- `outputs/shared-plans-full20-20261121/`: classificação de planos nos 20 casos.
- `outputs/target-block-profile-132515/`: cobertura e comparação do sampler.
- `outputs/lns-warm-diversity/`: candidatos neutros, seleção e recombinação.
- `outputs/lns-neutral-archive-smoke/`: captura pelo fluxo normal do programa.

Todos os resultados competitivos desta rodada preservaram 132.515. As melhorias
de cobertura e os exemplos sintéticos justificam mecanismos de busca, mas não
sustentam projeção para menos de 100.000. Faltam 32.516 operações para 99.999.
As fontes matemáticas e as distinções entre custo de árvore e de DAG estão na
[pesquisa de circuitos](2026-11-21-multioutput-circuits.md).
