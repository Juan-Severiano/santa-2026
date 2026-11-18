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

## Resultados e checkpoints em 18/11/2026

**Checkpoint atual no Kaggle: 159.737 instruções**, submissão **58512481**, aceita e pontuada.
Esse portfólio parte dos **160.175** programas públicos de konbu17 e acrescenta **438 instruções de
economia** por MILP, replanejamento C++ e compressão próprios. A origem Apache-2.0 está documentada
em [THIRD_PARTY.md](THIRD_PARTY.md). A construção exclusivamente própria chegou a 233.968.

O CSV enviado está em `outputs/checkpoint-submission.csv`, com SHA-256
`0b1d5d8f9c70b7a050138d33e921be0616632afe7774d6a227755118d0a58eb1`.
Todos os 1.000 programas foram reexecutados localmente e a nota foi confirmada pelo Kaggle.
A meta de menos de 100.000 permanece em pesquisa.

A evolução inicial, anterior à incorporação da referência pública, foi:

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

O arquivo da primeira versão é `outputs/submission.csv`. Todos os programas passaram no replay local após o round-trip
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

## Laboratório de busca

O motor de busca pesada fica em `cpp/beam_search.cpp`, compilado em C++17 com otimização para a CPU
local. Ele usa dicionários de expressões curtas, busca regressiva e variantes de ordem dos alvos.
Python coordena processos, checkpoints, comparação de programas e validação exata. As origens Apache-2.0
do código e dos programas públicos estão descritas em [THIRD_PARTY.md](THIRD_PARTY.md).

```sh
python3 scripts/build_cpp.py
python3 scripts/lab.py --puzzles data/puzzles.csv --baseline outputs/submission.csv --output outputs/labs/replan-pilot --strategies beam optimize --beam-mode 1 --seeds 0 1 --effort 1 --jobs 10 --sample 100
python3 scripts/lab.py --puzzles data/puzzles.csv --baseline outputs/submission.csv --output outputs/labs/replan-full --strategies beam optimize --beam-mode 4 --seeds 0 1 --effort 1 --jobs 10 --sample 0
python3 scripts/merge.py --puzzles data/puzzles.csv --output outputs/labs/merged.csv outputs/submission.csv outputs/labs/replan-full/best.csv
```

O modo 0 reproduz o planejamento original; o modo 1 escolhe o alvo mais barato a cada etapa; o modo 2
varia a ordem; o modo 3 considera afinidade entre alvos; o modo 4 compara o custo do programa já alocado
nos 48 registradores. `--seeds` seleciona parâmetros e ordens complementares. `--effort` aumenta a busca.

`--sample 100` cobre uniformemente as faixas do custo do baseline. `--sample 0` avalia todos os puzzles.
O relatório distingue a redução nos puzzles testados da nota do portfólio completo, que mantém o baseline
nos demais. Cada execução salva cópias imutáveis do Python e do binário C++, seus hashes, checkpoints
por puzzle e o CSV completo validado. Repetir o mesmo comando retoma checkpoints compatíveis.

Outras estratégias disponíveis: `shared` (intermediários compartilhados), `synthesis` (bases variáveis),
`factor` (fatorações próximas), `optimize` (compressão exata do grafo) e `milp` (seleção global de receitas).
Para `milp`, instale o extra `lab`; o SciPy usa o solver HiGHS em C++. Sem SciPy, essa estratégia preserva
o programa de entrada. Os comandos não enviam nada ao Kaggle nem ao GitHub.

A estratégia `polish` usa o modo 6 do C++: importa um programa já válido, tenta substituir trechos de
seu grafo por construções mais curtas e aceita apenas programas completos menores. Exemplo:

```sh
python3 scripts/lab.py --puzzles data/puzzles.csv --baseline outputs/checkpoint-submission.csv --output outputs/labs/repair-pilot --strategies polish milp optimize --seeds 0 1 --effort 1 --jobs 6 --sample 100
```

A construção própria chegou a **233.968 instruções** nos 1.000 puzzles. A referência pública Apache-2.0
de konbu17 foi validada separadamente com **160.175**. Resultados que usam essa referência como fallback
devem informar essa origem; esse número não é apresentado como ganho de construção própria.

## Validação

O verificador usa inteiros do Python, sem ponto flutuante. Confere as quatro operações, divisão sem resto,
positividade, `value_limit`, fontes definidas, índices, cobertura acumulada dos alvos e os limites de
4.096 instruções e 131.072 bytes UTF-8 por célula. A leitura exige exatamente as colunas `id,program`,
IDs únicos e um programa JSON válido. Ao pontuar, exige os mesmos IDs do arquivo de puzzles fornecido.

Os 77 testes passam, incluindo integração compilada em C++ e o laboratório. Eles cobrem o exemplo oficial de quatro instruções, operações inválidas, inteiros até `10^18`, leitura
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
