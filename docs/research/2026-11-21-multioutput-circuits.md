# Circuitos aritméticos com várias saídas: pesquisa e diagnóstico

Data: 21/11/2026. Escopo: descobrir **novas operações compartilhadas**, além de escolher receitas já observadas ou reconstruir um único valor. Baseline: `outputs/checkpoint-132542.csv`, **132.542 instruções**, SHA-256 `a3f6b9e1a6a0d4f2ef9baecb640f4bdf1203fbad02c8c68f1665a8e569b74605`.

**Resultado:** há um mecanismo concreto que o gerador atual não procura de maneira coordenada: construir uma base de dois ou três valores e fechar três alvos com duas operações por alvo. Um exemplo executável reduz 9 para 8 instruções; suas substituições parciais não reduzem o custo. Entretanto, os dois diagnósticos em 20 puzzles reais terminaram sem candidato positivo. Esta pesquisa não demonstra caminho para menos de 100.000: ainda seriam necessárias **32.543 instruções de economia** para chegar a 99.999.

## O problema matemático relevante

Um programa é um circuito aritmético, ou *straight-line program*, cujas saídas são todos os alvos observados durante a execução. Uma instrução pode alimentar várias saídas; seu custo é pago uma vez quando o valor permanece disponível. Aqui também existem restrições operacionais: intermediários positivos, limite por puzzle, divisão exata e 48 registradores. O custo final continua sendo o programa físico validado, não a soma dos tamanhos das árvores de expressão.

A generalização de uma cadeia de adições para produzir uma **sequência de inteiros** já é estudada como *addition-sequence problem*. Downey, Leong e Sethi demonstram NP-completude dessa versão com várias saídas e somente adições a partir de 1. Isso não prova a mesma classificação para todas as variantes do Santa nem para a cadeia de um único inteiro. A lição aplicável é otimizar o conjunto de saídas, sem supor que a união de soluções individuais seja ótima. [Downey–Leong–Sethi, SIAM J. Computing, 1981](https://epubs.siam.org/doi/abs/10.1137/0210047).

Cadeias de adição/subtração e multiplicação por constantes são famílias úteis de candidatos, mas suas métricas precisam ser traduzidas. No modelo de *multiple constant multiplication* (MCM), deslocamentos podem integrar uma operação de soma/subtração. No Santa, produzir uma potência de dois, multiplicar por ela e dividir têm seus próprios custos e condições; não existe deslocamento gratuito. [Voronenko–Püschel, 2007, §2](https://spiral.ece.cmu.edu/pubs/synth.pdf).

## O que a literatura muda na estratégia

| Fonte primária | Mecanismo que interessa | Aplicação e limite neste projeto |
| --- | --- | --- |
| [MCM, Voronenko–Püschel, §§3–5](https://spiral.ece.cmu.edu/pubs/synth.pdf) | Um conjunto de valores disponíveis cresce por sucessores; a heurística Hcub acumula o benefício sobre **todos** os alvos. Distâncias pequenas são tratadas por topologias e equações, sem construir toda a expansão futura. | Avaliar um helper pelo que ele desbloqueia no grupo. Não exigir dois alvos diretamente alcançáveis por uma operação. Importar a ideia, não os custos de deslocamentos do MCM. |
| [Knuth, 1977](https://www.sciencedirect.com/science/article/abs/pii/0020019077900023) | Generalização de Dijkstra para estruturas de derivação e grafos AND/OR. | Bom orientador para distâncias/derivações. A recorrência aditiva de árvore pode cobrar repetidamente um filho compartilhado; não substitui a contagem da união do DAG. |
| [egg, Willsey et al., POPL 2021, §§2 e 4](https://arxiv.org/pdf/2004.03082) | Guardar alternativas equivalentes sem destruir as anteriores; análises por classe e regras condicionais. O extrator simples usa custo local de expressão. | A classe pode ser o valor inteiro exato. Um rótulo numérico não pode virar uma constante gratuita: somente sementes têm custo zero. O extrator padrão de tamanho de AST não é o objetivo de várias saídas. |
| [TENSAT, Yang et al., MLSys 2021, §§4–6.5](https://proceedings.mlsys.org/paper_files/paper/2021/file/cc427d934a7f6c0663e5923f49eba531-Paper.pdf) | Regras com vários padrões/saídas e extração conjunta. A ablação mostra um caso em que o ganho exige que **ambas** as saídas escolham o novo subgrafo; escolhas independentes não capturam a economia. Também trata ciclos e crescimento rápido da exploração. | Gerar e selecionar o pacote inteiro de helpers e consumidores. Limitar explicitamente a geração; aceitar somente DAG acíclico e programa inteiro válido. Trocar o extrator, sozinho, não inventa as operações ausentes. |
| [Sun–Zhang–Ni, 2024, §§2–4](https://arxiv.org/html/2408.17042v2) | Extração de e-graphs corresponde a satisfazer um circuito monotônico ponderado, com tratamento de ciclos. Apresenta solução parametrizada por treewidth. | Modelo natural: OR escolhe receita; AND exige ambos os operandos; um custo ativa a operação uma vez. Útil para cortes pequenos. Não presumir que o grafo denso de todas as operações tenha treewidth pequeno. |
| [Goharshady–Lam–Parreaux, OOPSLA 2024](https://2024.splashcon.org/details/splash-2024-oopsla/144/Fast-and-Optimal-Extraction-for-Sparse-Equality-Graphs) | Extração global é difícil em geral; o trabalho dá um algoritmo para grafos de baixa treewidth e uma implementação Rust. | Medir a estrutura dos pequenos candidatos antes de adotar uma DP estrutural. Resolver exatamente a extração de um pool sem receitas melhores continua sem melhorar a solução. |
| [Mishchenko–Chatterjee–Brayton, DAC 2006, §4](https://people.eecs.berkeley.edu/~alanmi/courses/2007_290N/papers/synthesis_berkeley_dac06.pdf) | Reescrita de cortes com contagem dos nós realmente removidos e realmente acrescentados, incluindo compartilhamento externo; movimentos de custo zero podem preparar ganhos posteriores. | Reutilizar a contabilização do DAG inteiro. Plateau já existe no LNS atual; a novidade necessária é o pacote de várias saídas, não apenas mais movimentos neutros. |

A analogia com Steiner é útil, mas limitada: queremos ligar sementes a vários terminais usando intermediários compartilhados. Uma receita aritmética exige **os dois** operandos, portanto uma aresta comum de grafo não representa a condição. É preciso manter hiperarestas/ANDs e escolhas de receitas/ORs. Esta é uma interpretação para o nosso modelo, não uma redução provada ao problema clássico de Steiner.

## Diferença em relação ao código e aos resultados existentes

- `rust/lns.rs` corta um root por tentativa. Seu catálogo e sua busca descobrem intermediários novos, e os plateaus podem preparar compartilhamento; não avalia diretamente uma base nova contra três roots ao mesmo tempo.
- `rust/joint.rs::fresh_helpers` parte de um helper de uma operação e exige rotas diretas para pelo menos dois alvos. O exemplo abaixo tem zero alvos diretamente fechados por `5` ou `55` usando apenas as sementes.
- `rust/joint.rs::planner_costs` já declara que os custos são de árvores, não uma cota ótima do DAG. A avaliação compilada é necessária e está correta como critério de aceitação.
- Os arquivos de `outputs/lns-pilot-20261121/` registram 83 programas arquivados, 301 valores novos e 11.420 receitas cruzadas no recorte, mas recombinar os 20 casos manteve 132.543. O CP posterior em 100 casos de `outputs/kaggle-rust-lns/cp-local100/report.json` manteve 132.542. Mais alternativas observadas não demonstraram economia adicional.

Consequência: adotar e-graphs ou outra formulação de extração sobre o mesmo pool não basta. O próximo teste precisa mudar o **gerador de circuitos**.

## Exemplo executável: uma base nova de duas operações

Sementes `[2, 3, 11, 17, 23, 31]`, alvos `[161, 583, 986]`, limite `1_000_000`.

Programa original, 9 operações:

```text
121 = 11 * 11       40 = 23 + 17       161 = 40 + 121
529 = 23 * 23       54 = 31 + 23       583 = 54 + 529
 34 = 17 * 2        29 = 31 - 2        986 = 29 * 34
```

Circuito compartilhado, 8 operações:

```text
h1 = 2 + 3 = 5
h2 = h1 * 11 = 55

161 = (h1 + 2) * 23
583 = (h2 - 2) * 11
986 = (h2 + 3) * 17
```

Parênteses representam operações reais: as seis operações finais são cobradas, além das duas da base. Os dois programas foram compilados pelo alocador Rust e reexecutados pelo `core.py`, cobrindo os três alvos.

Para o **pacote específico** acima, substituir os subconjuntos de roots dá:

| Roots substituídos | Custo do programa inteiro |
| --- | ---: |
| `161` | 9 |
| `583` | 10 |
| `986` | 10 |
| `161, 583` | 9 |
| `161, 986` | 9 |
| `583, 986` | 9 |
| Os três | **8** |

A busca completa de formas com até duas operações, usando o contexto externo seguro de cada root, não encontrou reescrita estrita de nenhum dos três alvos originais. Isso verifica uma barreira para a melhoria estrita de um root nesse exemplo; **não** prova inexistência de outro circuito de dois roots nem otimalidade global de 8. Plateaus também podem atravessar o exemplo em etapas. A vantagem pretendida da busca conjunta é encontrar o pacote diretamente.

Reprodução e programas físicos: `outputs/multioutput-toy.rs`, `outputs/multioutput-toy.json`. O diagnóstico usa somente o nosso código Rust; nenhum notebook ou solver estrangeiro foi executado.

## Dois diagnósticos reais, ambos negativos

Ambos usam os mesmos 20 IDs fixos de `outputs/lns-pilot-20261121/ids20.json`, com custos originais entre 79 e 255, sobre o SHA do checkpoint indicado no início.

**A. Três helpers com consumidores diretos.** Foram examinadas cadeias `h1=op(P,P)`, `h2=op(h1,P)`, `h3=op(h2,P ou h1)`, com um alvo diferente fechado por uma operação a partir de cada helper. Após restrições de dependência, houve 3.978 grupos acíclicos com contagem completa do DAG. Nenhum melhorou: 2.727 acrescentavam três nós, 1.207 acrescentavam dois e 44 acrescentavam um. O melhor resultado ainda era pior que a base. Esse gerador guarda uma receita representante por valor; não é uma enumeração completa de circuitos. Evidência: `outputs/multioutput-chain-diagnostic.json`.

**B. Base de dois helpers com consumidores de duas operações.** O teste dirigido pelo exemplo procurou:

```text
h1 = op(P,P)
h2 = op(h1,P)
t1 = (h1 +/- a1) * p1   ou divisão exata por p1
t2 = (h2 +/- a2) * p2   ou divisão exata por p2
t3 = (h2 +/- a3) * p3   ou divisão exata por p3
```

Inclui também `a-h`. Todos os `a,p` vêm do contexto observado; sementes, intermediários e alvos não recebem custo gratuito ao serem necessários no novo DAG. Foram encontradas **1.548 construções de base** compatíveis com as rotas. Nenhum grupo tinha uma cota de economia positiva após cobrar o corte removível e os nós novos distintos; portanto nenhum chegou à compilação física. O limite de 25.000 verificações por caso não foi atingido. Duração local medida: **20,46 s**. Evidência: `outputs/multioutput-basis-diagnostic.json` e seu resumo.

O segundo diagnóstico cobre esse padrão de terminais, não todas as formas com duas operações; por exemplo, não cobre genericamente `(h*p)+a`. Os resultados são negativos de um espaço declarado. Não são um certificado de ótimo, nem uma economia hipotética de registradores.

## Hipótese implementável em Rust

**Hipótese:** uma busca de estados de base com até três helpers, pontuada pelo benefício sobre três roots e com rotas finais de até duas operações, consegue gerar pacotes ausentes do LNS de um root e do pool de recombinação. O exemplo demonstra o mecanismo; os 20 casos não demonstraram headroom real para as duas famílias testadas.

Um piloto novo pode ter a seguinte forma:

1. Selecionar grupos de três roots cujo corte conjunto tenha pelo menos nove operações removíveis. Construir o contexto externo seguro, excluindo descendentes dos roots. Manter também as receitas antigas para calcular o custo inteiro.
2. Criar estados `B` contendo até três instruções novas explícitas. Cada novo helper usa valores do contexto ou helpers anteriores. Hash-consing numérico evita pagar duas vezes pelo mesmo valor, mantendo alternativas de receita quando mudam as dependências.
3. Calcular rotas de até duas operações dos helpers aos três roots. O gerador deve registrar **todas as rotas relevantes**, incluindo finais por multiplicação, adição, subtração e divisão exata; retornar apenas o primeiro plano por root pode esconder a interseção útil.
4. Ordenar os estados por um benefício cumulativo inspirado no MCM:

   `sum_t(d_context(t) - d_context+base(t)) - custo(base)`.

   Essa expressão é uma heurística de ordenação, não cota admissível nem economia final. Estados de benefício zero precisam sobreviver por dois ou três níveis: no exemplo, `h1` prepara `h2`, e só o pacote completo economiza.
5. Inserir as receitas de base e terminais num grafo AND/OR local com um root artificial de custo zero que exige as três saídas. Extrair pelo custo da **união dos nós ativos**, com aciclicidade. Para um pool local pequeno, branch-and-bound sobre bitsets de receitas/nós é uma primeira implementação Rust mais controlável que uma biblioteca inteira de saturação. Medir largura estrutural antes de investir em extração por treewidth.
6. Compilar preservando posições do programa, alocar os 48 registradores e reexecutar o programa completo. Rejeitar divisão inexata, intermediários fora do limite, ciclos, pressão de registradores e qualquer regressão no melhor programa salvo.

A análise semântica de um e-graph pode ajudar a validar as operações, mas não autoriza reescritas cujos intermediários sejam ilegais. Por exemplo, a distributividade sobre inteiros pode introduzir produtos acima do limite; cancelamentos podem passar por zero; uma divisão algébrica pode não ser exata no ponto em que é executada.

O teste de decisão deve continuar pequeno: primeiro o exemplo acima e casos com divisão final; depois os mesmos 20 IDs, com orçamento e contadores separados para geração, cotas, extração, compilação e replay. Só um programa real estritamente menor justifica ampliar a execução. Na ausência dele, a conclusão é que o mecanismo é válido, mas a distribuição dos puzzles ainda não forneceu a estrutura necessária; aumentar apenas seeds ou horas não resolve essa evidência ausente.
