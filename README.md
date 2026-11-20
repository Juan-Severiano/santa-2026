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

## Checkpoint de 132.543

Em **21/11/2026**, o Kaggle confirmou **132.543 instruções**, submissão
**58673602**, com os 1.000 programas válidos. O resultado incorpora a referência
pública Apache-2.0 de **goodpjw2008, dataset versão 50**, com créditos em
[THIRD_PARTY.md](THIRD_PARTY.md). É uma redução de 8.851 sobre o melhor local
anterior de 141.394; nenhum ganho adicional do novo Rust foi demonstrado nesta base.

O arquivo exato enviado está versionado em [checkpoints/132543.csv.gz](checkpoints/132543.csv.gz),
com hashes e origem em [checkpoints/132543.json](checkpoints/132543.json).

```sh
mkdir -p outputs
gzip -dc checkpoints/132543.csv.gz > outputs/checkpoint-132543.csv
PYTHONPATH=src python3 -m santa2026 validate data/puzzles.csv outputs/checkpoint-132543.csv
```

O novo motor [rust/lns.rs](rust/lns.rs) reconstrói cones de alvos e intermediários,
explora alternativas de mesmo custo e perturbações de até duas instruções,
preservando o melhor programa. Seu alocador recompilou os 1.000 grafos da base
sem aumentar o custo. Dois pilotos locais de 20 casos, com oito segundos por
caso, ainda não reduziram a pontuação. O segundo guarda até oito programas
alternativos para recombinação por CP-SAT com ordem livre e novas conexões
entre valores observados. A meta de menos de 100.000 continua em pesquisa.

## Checkpoints anteriores, 18/11/2026

O checkpoint de **142.272 instruções**, submissão **58523172**, foi aceito e pontuado.
O resultado usa a referência pública Apache-2.0 de Atakan Aldemir versão 2; nenhum ganho próprio
adicional foi demonstrado nesta base. O CSV enviado está arquivado em `outputs/checkpoint-142272.csv`, SHA-256
`66f6842840e7b348d7ca01c06817efd9942cda76b5aa0dc879e1db59c3b7498c`.
Os 1.000 programas passaram no replay e a pontuação oficial coincidiu com a previsão local.

O checkpoint **144.213**, submissão **58519516**, parte da referência pública Apache-2.0 de Hanif versão 4 (**144.248**) e incorpora
**35 instruções de economia** de experimentos locais, incluindo o reparo em Rust.
O CSV enviado é `outputs/rust-main-submission.csv`, SHA-256
`39df138984fd17e02770d15a6a545bebdc84ee1a136951996f7dc2c9ff9c93a8`.
Os 1.000 programas passaram no replay e a pontuação oficial coincidiu com a previsão local.

O checkpoint de **159.737**, submissão **58512481**, parte dos **160.175** programas públicos de konbu17 e acrescenta **438 instruções de
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
esse arquivo é a entrada fixa dos testes, anterior ao checkpoint oficial de 142.272 no Kaggle.
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

## Pesquisa em Rust após o checkpoint

A nova referência de pesquisa é o portfólio público Apache-2.0 de
[Atakan Aldemir, versão 2](https://www.kaggle.com/code/atakanaldemir/santa-2026-joint-recipes-for-large-puzzles?scriptVersionId=371115522),
validado separadamente com **142.272 instruções**, posteriormente confirmado na submissão **58523172**. Esse avanço sobre o checkpoint anterior de 144.213
vem da referência pública; a contribuição de cada experimento é medida além dela. Proveniência,
licença, notebook fixado e replay ficam em `data/public/research/reference-atakan-v2/`.

Os motores experimentais compartilham o reparo original sem alterar `rust/repair.rs`:

- `rust/optimizer.rs` reconstrói grupos de 6–10 alvos com lookahead e três estados de grafo.
  A opção `SANTA2026_OPTIMIZER_BOUNDARY=1` prioriza os valores do prefixo que entram na parte removida
  e conserva alternativas construídas a partir das receitas originais, cobrando as operações usadas.
- `rust/joint.rs` combina receitas de vários programas, gera intermediários novos e testa mudanças
  coordenadas no helper e nos seus consumidores. Cada grafo passa por detecção de ciclos, alocação
  real de 48 registradores e replay inteiro antes de substituir o baseline.
- `rust/factor_groups.rs` procura fatores comuns ausentes, constrói também os quocientes e replaneja
  o grupo de alvos. Fatores e constantes são cobrados; receitas inseguras são bloqueadas e o grafo
  completo precisa passar na alocação e no replay.

```sh
python3 scripts/build_native.py --engine rust --source rust/optimizer.rs --output outputs/bin/optimizer-rust
python3 scripts/build_native.py --engine rust --source rust/joint.rs --output outputs/bin/joint-rust
python3 scripts/build_native.py --engine rust --source rust/factor_groups.rs --output outputs/bin/factor-groups-rust
python3 scripts/lab.py --puzzles data/puzzles.csv --baseline outputs/rust-research-baseline.csv --output outputs/labs/rust-joint --strategies rustjoint --candidates outputs/rust-main-submission.csv --joint-helpers 24 --sample 30 --jobs 1
```

O builder congela também os arquivos Rust incluídos. O laboratório assina `--joint-helpers`,
`--optimizer-boundary` e `--optimizer-ms` e restaura seus valores em cada worker; alterar essas opções
impede reutilizar checkpoints incompatíveis. `--ids-file` aceita uma lista JSON de puzzles exatos.

`scripts/rust_quality.py` compara qualidade sob um orçamento máximo por puzzle, intercalando métodos
e preservando o portfólio completo nos puzzles não testados. Os programas completos já emitidos podem
ser recuperados após timeout ou erro de processo, desde que passem na validação independente.
Resultados reais nesta base:

| Experimento | Casos | Limite por caso/método | Ganho observado |
| --- | ---: | ---: | ---: |
| Reparo, caudas e receitas V1 | 30 | 4 s | 0 |
| Fronteira prioritária e novos helpers V2 | 30 | 4 s | 0 |
| Macros dirigidas de helper e consumidores V3 | 10 | 8 s | 0 |
| Síntese de fatores e quocientes compartilhados | 10 | 8 s | 0 |

Todos esses lotes mantiveram programas válidos e a nota de 142.272. Ganhos nos exemplos pequenos dos
testes demonstram o comportamento das novas buscas, mas não são apresentados como ganho na competição.
Os experimentos não sustentam uma projeção abaixo de 100.000.

## Construções novas e janelas de várias saídas

`rust/construct.rs` recebe apenas sementes, alvos e limite do puzzle, sem programa de referência.
Trabalha em um banco real de 48 registradores, com joins exatos, aproximações e construção de resíduos.
O método é inspirado no Atlas Solver Apache-2.0 e tem atribuição em [THIRD_PARTY.md](THIRD_PARTY.md).
Programas válidos mais longos também são preservados como variantes para ampliar o pool de receitas;
eles não substituem um programa menor no portfólio principal.

`rust/window.rs` busca substituições de janelas de 3–8 instruções, preservando as leituras futuras e os
alvos vistos dentro da janela, inclusive quando depois são sobrescritos. A opção `--window-rename`
permite encontrar as saídas em outros registradores e aplicar uma permutação bijetiva ao sufixo.
Valores iguais em posições distintas continuam precisando de cópias físicas distintas.

O modo `--groups-direct` reconstrói um alvo a partir do contexto externo ao seu cone privado completo.
No joint, `--joint-plans` monta rotas inteiras do pool usando custos positivos de hipergrafo e um
prefixo fixado. A estimativa de árvore guia a busca; a aceitação usa o programa compilado e reexecutado.
Cada estado conserva sua ordem de alvos; cache, macros e mutações distinguem e herdam essa ordem.
O laboratório assina também `--groups-direct`, `--joint-plans` e `--joint-ms` para impedir retomadas
com opções incompatíveis.

```sh
python3 scripts/build_native.py --engine rust --source rust/construct.rs --output outputs/bin/construct-rust
python3 scripts/build_native.py --engine rust --source rust/window.rs --output outputs/bin/window-rust
```

Nos 20 puzzles representativos com limite de 4 s por método, a reconstrução direta e as janelas,
inclusive com renomeação, não melhoraram a referência. O construtor V1 gerou 4.295 instruções no recorte,
contra 2.837 da base; suas variantes continham 12.126 receitas vivas diferentes da base. Duas correções
de joins e o uso dos 64 bits da seed reduziram o construtor V2 para 4.197 instruções nos mesmos casos.
Essa economia de 98 é medida no construtor, não é ganho sobre a referência da competição.
Os testes de combinação com essas variantes e de planejamento por hipergrafo, incluindo a correção
da ordem preservada nos estados, também terminaram sem ganho nos mesmos 20 puzzles. Todos os programas
aceitos passaram no replay; resultados e candidatos ficam separados em `outputs/rust-new-pilot/`.

Uma versão pública posterior de Atakan, fixada em `scriptVersionId=371150866`, foi validada com
**141.394 instruções**. Ela fica separada dos pilotos congelados em 142.272 e ainda não foi submetida
por esta conta. Sua origem pública está documentada; ganhos dos nossos experimentos são medidos à parte.

## Validação

O verificador usa inteiros do Python, sem ponto flutuante. Confere as quatro operações, divisão sem resto,
positividade, `value_limit`, fontes definidas, índices, cobertura acumulada dos alvos e os limites de
4.096 instruções e 131.072 bytes UTF-8 por célula. A leitura exige exatamente as colunas `id,program`,
IDs únicos e um programa JSON válido. Ao pontuar, exige os mesmos IDs do arquivo de puzzles fornecido.

Os 162 testes passam, incluindo integração compilada, paridade C++/Rust/Go e o laboratório. As suítes Rust dos novos motores também passam. Eles cobrem o exemplo oficial de quatro instruções, operações inválidas, inteiros até `10^18`, leitura
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
