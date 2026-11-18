# Santa 2026 - The Elf Calculator

Solver local e verificador exato para a [competição do Kaggle](https://www.kaggle.com/competitions/santa-2026/overview).
Cada puzzle começa com alguns inteiros positivos. Um programa usa `+`, `-`, `*` e divisão exata para
produzir todos os alvos, com 48 registradores. O objetivo é minimizar a soma das instruções dos 1.000 puzzles.
É um problema de otimização combinatória; este baseline não treina modelos e não usa GPU nem API paga.

## Executar

Python 3.9 ou superior, sem dependências externas. Execute os comandos nesta pasta:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m santa2026 inspect data/puzzles.csv
PYTHONPATH=src python3 -m santa2026 validate data/puzzles.csv data/sample_submission.csv --report outputs/sample-report.json
PYTHONPATH=src python3 -m santa2026 solve data/puzzles.csv outputs/baseline.csv --strategy radix --fallback data/sample_submission.csv --report outputs/baseline-report.json
PYTHONPATH=src python3 -m santa2026 solve data/puzzles.csv outputs/submission.csv --strategy window --fallback outputs/baseline.csv --report outputs/submission-report.json
PYTHONPATH=src python3 -m santa2026 solve data/practice_puzzles.csv outputs/practice-baseline.csv --strategy radix --report outputs/practice-baseline-report.json
PYTHONPATH=src python3 -m santa2026 solve data/practice_puzzles.csv outputs/practice-submission.csv --strategy window --fallback outputs/practice-baseline.csv --report outputs/practice-submission-report.json
PYTHONPATH=src python3 -m santa2026 validate data/puzzles.csv outputs/submission.csv
```

O `--fallback` valida o arquivo de referência inteiro antes de usá-lo e preserva o programa dele quando
for mais curto. O comando `solve` verifica todos os programas, grava o CSV e relê o arquivo para validar
o resultado após a serialização. Os comandos são locais; não enviam submissões.
Sem `--strategy`, o solver usa `portfolio`, que compara as duas construções na mesma execução.
Separar as rodadas permite reaproveitar o baseline e executar apenas a variação nova.

## Resultados locais em 18/11/2026

| Construção | Puzzles pontuados | Instruções | Maior programa |
| --- | ---: | ---: | ---: |
| Sample oficial | 1.000 | 1.490.379 | 2.873 |
| Portfólio de bases | 1.000 | 340.302 | 688 |
| Bases + janelas binárias assinadas | 1.000 | **288.694** | **561** |

O resultado final economiza 1.201.685 instruções (**80,63%**) sobre o sample. A segunda rodada melhorou
909 puzzles e preservou o baseline nos demais, economizando mais 51.608 instruções (**15,17%**).
A rodada inicial levou 104,870 s; a nova construção, já com o baseline em cache, levou 11,365 s.
Os 200 puzzles de prática caíram de 69.434 para **58.586 instruções**.

Em 18/11/2026, o Kaggle aceitou a submissão **58510005** e confirmou a pontuação oficial de
**288.694**, igual à previsão local. A submissão corresponde ao SHA-256
`304ade128eeeb382c25e61a3241fe400c03e442bfa4b0e1dc3b00ec168a05a77`.

O arquivo final é `outputs/submission.csv`. Todos os programas passaram no replay local após o round-trip
CSV/JSON. `outputs/submission-report.json` contém os detalhes por puzzle, hashes SHA-256 dos dados,
do CSV e do solver, versão do Python e falhas que exigiram fallback (nenhuma nesta rodada).

Para obter os dados oficiais em `data/`, use o CLI do Kaggle autenticado
e `kaggle competitions download -c santa-2026 -p data`, após entrar na competição e aceitar suas regras.
Extraia os três CSVs do ZIP nessa pasta. Dados, programas gerados e relatórios estão fora do versionamento.

## Método

- Um portfólio de bases posicionais constrói os inteiros por multiplicação e adição, escolhendo a solução mais curta por puzzle.
- Janelas binárias assinadas de larguras 4, 5 e 6 usam potências de dois e pequenos dígitos ímpares positivos ou negativos.
- Uma construção assinada é descartada se algum intermediário exceder `value_limit`; o portfólio preserva a alternativa válida.
- Constantes pequenas e prefixos calculados permanecem em cache quando há espaço nos 48 registradores.
- O solver procura alvos que podem sair em uma operação dos valores disponíveis e reutiliza diferenças e fatores exatos.
- A reciclagem de registradores preserva constantes e dependências ainda em uso.

Este método fornece um baseline construtivo e determinístico. Não há garantia de ótimo global.

## Validação

O verificador usa inteiros do Python, sem ponto flutuante. Confere as quatro operações, divisão sem resto,
positividade, `value_limit`, fontes definidas, índices, cobertura acumulada dos alvos e os limites de
4.096 instruções e 131.072 bytes UTF-8 por célula. A leitura exige exatamente as colunas `id,program`,
IDs únicos e um programa JSON válido. Ao pontuar, exige os mesmos IDs do arquivo de puzzles fornecido.

Os 28 testes passam. Eles cobrem o exemplo oficial de quatro instruções, operações inválidas, inteiros até `10^18`, leitura
antes de sobrescrita, cobertura depois de reciclar um alvo, serialização CSV/JSON, limites e puzzles aleatórios.
O sample oficial passou nos 1.000 puzzles, com **1.490.379 instruções**. Uma comparação independente de
10.000 programas aleatórios encontrou zero divergências entre o verificador e um segundo interpretador.

A pontuação do CSV final foi confirmada pelo Kaggle; as medidas de tempo e os resultados de prática são locais.

O próximo experimento indicado pelos dados de prática é compartilhar quase múltiplos entre alvos:
`target = anchor * quotient + residual`. Isso pode aproveitar estruturas que a construção independente
por dígitos não encontra. Essa melhoria ainda não está implementada.

## Referências oficiais

- [Descrição e cronograma](https://www.kaggle.com/competitions/santa-2026/overview)
- [Regras aritméticas e dados](https://www.kaggle.com/competitions/santa-2026/data)
- [Métrica e formato](https://www.kaggle.com/competitions/santa-2026/overview/evaluation)
- [Regras da competição](https://www.kaggle.com/competitions/santa-2026/rules)

A especificação técnica foi lida pela API oficial do Kaggle em 18/11/2026. O cronograma anuncia entrada e
junção de equipes até 21/01/2027 e encerramento em 28/01/2027, às 23:59 UTC (20:59 em Fortaleza).
A avaliação específica informa que não existe divisão entre placar público e privado.
