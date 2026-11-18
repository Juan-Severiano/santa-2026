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

**Checkpoint atual no Kaggle: 144.213 instruções**, submissão **58519516**, aceita e pontuada.
O portfólio parte da referência pública Apache-2.0 de Hanif versão 4 (**144.248**) e incorpora
**35 instruções de economia** de experimentos locais, incluindo o reparo em Rust.
O CSV enviado é `outputs/rust-main-submission.csv`, SHA-256
`39df138984fd17e02770d15a6a545bebdc84ee1a136951996f7dc2c9ff9c93a8`.
Os 1.000 programas passaram no replay e a pontuação oficial coincidiu com a previsão local.

O checkpoint anterior, **159.737**, submissão **58512481**, parte dos **160.175** programas públicos de konbu17 e acrescenta **438 instruções de
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

O motor principal de reparo fica em `rust/repair.rs`. A construção original e os outros modos
experimentais ficam em `cpp/beam_search.cpp`, compilado em C++17 com otimização para a CPU local.
Eles usam dicionários de expressões curtas, busca regressiva e variantes de ordem dos alvos.
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
nos demais. Cada execução salva cópias imutáveis do Python e do executável nativo utilizado, seus hashes, checkpoints
por puzzle e o CSV completo validado. Repetir o mesmo comando retoma checkpoints compatíveis.

Outras estratégias disponíveis: `shared` (intermediários compartilhados), `synthesis` (bases variáveis),
`factor` (fatorações próximas), `optimize` (compressão exata do grafo) e `milp` (seleção global de receitas).
Para `milp`, instale o extra `lab`; o SciPy usa o solver HiGHS em C++. Sem SciPy, essa estratégia preserva
o programa de entrada. Os comandos não enviam nada ao Kaggle nem ao GitHub.

A estratégia `polish` usa o modo 6, por padrão em Rust: importa um programa já válido, tenta substituir trechos de
seu grafo por construções mais curtas e aceita apenas programas completos menores. Exemplo:

```sh
python3 scripts/lab.py --puzzles data/puzzles.csv --baseline outputs/checkpoint-submission.csv --output outputs/labs/repair-pilot --strategies polish milp optimize --seeds 0 1 --effort 1 --jobs 1 --sample 100
```

A construção própria chegou a **233.968 instruções** nos 1.000 puzzles. A referência pública Apache-2.0
de konbu17 foi validada separadamente com **160.175**. Resultados que usam essa referência como fallback
devem informar essa origem; esse número não é apresentado como ganho de construção própria.

## Comparação entre C++, Rust e Go

`rust/repair.rs` e `go/repair/main.go` implementam o mesmo reparo guiado do modo 6: dicionário de
expressões, busca regressiva, substituição de grafos e alocação nos 48 registradores. As três versões
recebem os mesmos puzzles e programas iniciais. Rust e Go implementam somente esse modo; os modos
de construção e as outras experiências em C++ continuam separados.

Rust é o motor padrão para novos reparos do modo 6. Ao aproveitar código C++ de novos notebooks,
a preferência do projeto é portá-lo para Rust, preservando as condições de licença e a atribuição.
Uma tradução de linguagem continua derivada da implementação de origem.

```sh
python3 scripts/build_native.py
python3 scripts/compare_languages.py --puzzles data/puzzles.csv --baseline outputs/language-comparison/baseline.csv --engines cpp:outputs/bin/repair-cpp rust:outputs/bin/repair-rust go:outputs/bin/repair-go --output outputs/language-comparison/controlled --sample 100 --seeds 0 --width 3 --trials 12 --passes 1 --repeats 3 --budget-seconds 60
```

O builder precisa de Clang/GCC, Rust e Go; não instala ferramentas. Rust também pode ser indicado com
`--rustc`, ou encontrado no toolchain isolado em `outputs/toolchains/rustup/toolchains/stable-*/bin/`.
Cada build salva fonte congelada, versão do compilador, flags e hashes. O comparador executa um motor
por vez, alterna a ordem nas três repetições e registra tempo, CPU e pico de memória. A verificação
dos programas fica fora do tempo medido. Diferenças nos programas ou falta de repetibilidade impedem
atribuir o resultado apenas à velocidade das implementações.

O baseline congelado desta comparação tem **144.238 instruções**, partindo da referência pública
Hanif versão 4 (**144.248**) e incorporando melhorias locais. Os 1.000 programas passaram no replay;
esse arquivo é a entrada fixa dos testes, anterior ao checkpoint oficial de 144.213 no Kaggle.
Os relatórios preservam o melhor programa válido por puzzle e mantêm os demais do baseline.

Em 18/11/2026, três repetições de 100 puzzles com o mesmo modo 6, largura 3, 12 tentativas,
uma passagem e seed 0 produziram programas idênticos nos três motores, sem divergências ou erros:

| Motor | Tempo mediano | CPU mediana | Maior pico RSS |
| --- | ---: | ---: | ---: |
| C++ / Clang 21 | 24,488 s | 19,014 s | 60,4 MB |
| Rust 1.100.0 | **19,256 s** | **14,628 s** | 289,5 MB |
| Go 1.26.5 | 53,984 s | 44,030 s | 139,1 MB |

Os programas reduziram o portfólio completo para **144.237**, mantendo o baseline nos puzzles não
testados. Um treino de outro projeto usou nove workers durante parte das medições; os tempos refletem
essa carga variável. As rodadas de 60 s por motor chegaram à mesma pontuação; C++/Rust cobriram os
mesmos 100 pares puzzle/seed, e Go cobriu 71. Repetições da mesma seed foram contabilizadas à parte.
Esses resultados descrevem este núcleo, os builds registrados e esta máquina. Rust foi adotado como
padrão por decisão do usuário, com paridade comprovada e a menor mediana observada; o maior uso de
memória deve orientar a quantidade de workers.

O modo 6 começa com um worker quando `--jobs` é omitido. Uma rodada local com quatro workers
perdeu um processo após 178 puzzles, com causa ainda não confirmada; os checkpoints foram preservados
e a rodada foi concluída com o mesmo código/binário e um worker. `--jobs` continua disponível para testar
paralelismo em condições de memória e carga conhecidas.

A consolidação em Rust terminou os **1.000 puzzles**, nas configurações `seed 0` e `seed 1` do
laboratório, com 12 tentativas e uma passagem. O resultado local é **144.213 instruções**, 25 a menos
que o baseline congelado da comparação; 24 vieram da rodada completa, em 22 puzzles, e uma do piloto.
Zero erros de candidatos. O CSV foi reaberto e validado integralmente após a cópia para
`outputs/rust-main-submission.csv`, SHA-256
`39df138984fd17e02770d15a6a545bebdc84ee1a136951996f7dc2c9ff9c93a8`.
O Kaggle confirmou **144.213** na submissão **58519516**; a meta abaixo de 100.000 permanece aberta.

`SANTA2026_REPAIR_BINARY=/caminho/do/binario` e `lab.py --repair-binary /caminho/do/binario` permitem
escolher outro motor para o modo 6. O laboratório congela esse executável e verifica seu hash na
retomada, separadamente do motor de construção.

## Validação

O verificador usa inteiros do Python, sem ponto flutuante. Confere as quatro operações, divisão sem resto,
positividade, `value_limit`, fontes definidas, índices, cobertura acumulada dos alvos e os limites de
4.096 instruções e 131.072 bytes UTF-8 por célula. A leitura exige exatamente as colunas `id,program`,
IDs únicos e um programa JSON válido. Ao pontuar, exige os mesmos IDs do arquivo de puzzles fornecido.

Os 118 testes passam, incluindo integração compilada, paridade C++/Rust/Go e o laboratório. Eles cobrem o exemplo oficial de quatro instruções, operações inválidas, inteiros até `10^18`, leitura
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
