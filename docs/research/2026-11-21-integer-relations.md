# Relações inteiras para circuitos aritméticos — 21/11/2026

## Resultado e decisão

A redução de reticulados recuperou a relação sintética proposta, mas **não demonstrou economia competitiva** no diagnóstico de 20 puzzles. Não há evidência suficiente para portar este gerador para Rust ou expandir a busca em todos os puzzles.

Baseline: `outputs/checkpoint-132542.csv`, 132.542 instruções, SHA-256 `a3f6b9e1a6a0d4f2ef9baecb640f4bdf1203fbad02c8c68f1665a8e569b74605`. A meta abaixo de 100.000 exige economizar pelo menos mais 32.543 instruções. Esta nota pesquisa uma hipótese; não representa nova submissão ou avanço de pontuação.

Somente fontes primárias foram consultadas. Nenhum programa estrangeiro foi executado. O protótipo Python é próprio e fica em `outputs/integer-relations/probe.py`; os motores de competição continuam em Rust. Esta rodada não modificou os motores.

## O que os métodos fornecem

**LLL e aproximação diofantina.** O artigo original introduz redução de bases e aplicações a aproximações simultâneas e relações lineares. Uma base reduzida permite encontrar vetores relativamente curtos; não minimiza o número de operações de um circuito. A distinção é decisiva aqui: coeficientes numericamente pequenos podem custar várias instruções. Fonte: [Lenstra, Lenstra e Lovász, 1982, artigo original](https://www.math.ucdavis.edu/~deloera/MISC/LA-BIBLIO/trunk/Lovasz/LovaszLenstraLenstrafactor.pdf), também [registro do instituto dos autores](https://ir.cwi.nl/pub/9304).

**PSLQ.** O problema tratado pelos autores é encontrar inteiros não todos nulos que anulem uma combinação linear de entradas reais. A precisão faz parte das condições de aplicação. Aqui as entradas já são inteiras exatas: qualquer par possui uma relação, como `y*x - x*y = 0`, que pode ignorar o alvo e não produzir circuito útil. Portanto, aplicar PSLQ sem impor participação do alvo e custo dos coeficientes não resolve a síntese. Fonte: [Ferguson e Bailey, relatório original de 1992](https://www.davidhbailey.com/dhbpapers/pslq.pdf); [Ferguson, Bailey e Arno, análise de 1999](https://www.ams.org/mcom/1999-68-225/S0025-5718-99-00995-3/S0025-5718-99-00995-3.pdf).

**Frações contínuas e reconstrução racional.** FLINT documenta a recuperação de `n/d` a partir de uma congruência `n = a*d mod m`, com limites explícitos `|n| <= N`, `0 < d <= D` e unicidade quando `2*N*D < m`. A implementação usa o algoritmo de Euclides; a reconstrução precisa ser verificada no problema original. Isso fornece um filtro de candidatos, não uma prova de circuito curto. Fontes: [documentação oficial da reconstrução](https://flintlib.org/doc/fmpq/reconstruct.html), [implementação fixada no commit 66efe60](https://github.com/flintlib/flint/blob/66efe60ea72b922d2537fa1344c1c997bb7b5bf3/src/fmpq/reconstruct_fmpz_2.c), [frações contínuas, documentação oficial](https://flintlib.org/doc/fmpq/cfrac.html). A documentação nova avisa que partes foram geradas por LLM; o contrato matemático foi conferido também na implementação, sem executá-la.

**Vetor próximo.** O problema inhomogêneo é mais adequado que uma relação homogênea irrestrita: aproximar o vetor do alvo por uma combinação de âncoras. A biblioteca fplll distingue o modo rápido do modo com garantia para CVP e exige base reduzida. O diagnóstico abaixo usa implementação própria de dimensão dois, sem chamar fplll e sem alegar solução ótima de CVP. Fonte: [interface oficial de `closest_vector` e Babai](https://fplll.github.io/fplll/svpcvp_8cpp.html).

## Formulação testada e custo real

Procuramos `T = a*x + b*y + r`, com coeficientes assinados, mas registradores e resultados estritamente positivos. Para duas âncoras, usamos as linhas `(w,0,x)` e `(0,w,y)` e aproximamos `(0,0,T)`. Redução de Gauss inteira, projeção de Babai com aritmética inteira exata e vizinhos locais geram `(a,b,r)`.

O peso `w` penaliza o tamanho dos coeficientes no objetivo geométrico. Ele não representa seu custo aritmético. Depois de recuperar uma relação:

1. O contexto exclui a derivação privada removida e todos os descendentes da raiz, evitando reutilizar a própria resposta.
2. Cada coeficiente ausente é construído por aritmética binária positiva, incluindo o custo de obter `1`. Produtos e somas também são cobrados; valores já materializados são deduplicados.
3. Termos positivos são combinados antes das subtrações. Cada intermediário precisa estar entre `1` e `value_limit`, que nunca excede `10^18`. Cancelamentos que ultrapassem esse limite são inválidos mesmo quando o resultado final cabe.
4. O grafo completo é podado, testado contra ciclos, ordenado pela posição da fonte, alocado em 48 registradores e reexecutado pelo validador inteiro independente antes de contar economia.

As dependências preservadas do contexto continuam no grafo e são cobradas no programa completo. Nenhuma constante ou valor temporário é considerado gratuito.

## Diagnóstico reproduzível

Execute na raiz:

```sh
PYTHONPATH=src python3 outputs/integer-relations/probe.py
```

Resultados em `outputs/integer-relations/gauss20.json`. Script SHA-256: `ed817fadcd1fb9aab93c105e9262236c5f81b836ff67287ba92c3121cbb28251`.

| Medida | Resultado |
| --- | ---: |
| Puzzles, mesmos IDs do piloto anterior | 20 |
| Raízes alvo/helper com cone privado de pelo menos 4 operações | 181 |
| Bases reduzidas | 132.150 |
| Pesos | 1, 16 e 256 |
| Coeficientes aceitos | `1 <= abs(a), abs(b) <= 64` |
| Âncoras por raiz | até 24, mais próximas de T, entre T/64 e 64T |
| Vizinhança da projeção | deslocamentos -1, 0 e 1 em cada coordenada |
| Relações com residual zero ou presente no contexto seguro | 0 |
| Candidatos com economia validada | 0 |
| Tempo observado | 3,613 s |

O teste é limitado: não exclui relações com outras âncoras, resíduos construídos, coeficientes maiores, outras métricas, maior dimensão ou substituição coordenada de vários alvos. Não estabelece ausência de relações em geral. Em especial, a seleção das 24 âncoras por proximidade pode omitir bases úteis de menor escala.

O toy recuperou `1.999.683 = 13*1.000.003 - 11*1.000.033 + 7`. Com apenas as três parcelas iniciais nos registradores, construir os coeficientes e o resultado consumiu **14 operações**, todas válidas. Isso demonstra por que relação pequena não significa circuito barato. Compartilhar a combinação central entre dois alvos, com resíduos iniciais `7` e `19`, produziu os dois resultados em **15 operações**. É exemplo sintético de amortização, sem comparação competitiva. Programas e replay: `outputs/integer-relations/toy-paid.json`.

## Próximas hipóteses, condicionadas a evidência

**Primeira: congruências guiadas pelo dicionário de resíduos e pelo custo dos coeficientes.** Para cada residual barato `r`, resolver `a*x = T-r mod y` e recuperar `b = (T-r-a*x)/y`. Se `gcd(x,y)` não divide `T-r`, descartar; caso contrário, reduzir pelo gcd antes de inverter. Isso busca diretamente resíduos executáveis, em vez de esperar que o residual numericamente próximo pertença ao contexto. Enumerar coeficientes já residentes ou construíveis em até duas instruções fornece um objetivo de custo mais fiel que sua norma. Esta variante ainda não foi medida; o piloto acima não a implementa.

**Segunda: reconstrução racional coordenada.** Permitir `d*T = a*x + b*y + r`, com `d > 1`, e procurar a mesma base `(x,y)` para dois ou mais alvos. É uma forma mais ampla que o teste anterior `(A*B +/- C)/D`: há duas âncoras escaladas e oportunidade de amortizar coeficientes. É preciso pagar divisor, coeficientes, produtos, numerador e divisão exata, manter o numerador dentro do limite e medir a remoção conjunta de ancestrais. Ainda não há sinal quantitativo de ganho que justifique um motor Rust.

Uma próxima rodada só avança para Rust depois de produzir pelo menos um circuito positivo no piloto, com programas completos, custos líquidos, validação independente e comparação na baseline fixada. Ampliar para 100 puzzles só faz sentido após esse sinal. Uma implementação Rust deve usar verificações de overflow: os produtos dos termos cabem em `i128` sob os filtros, mas determinantes de Gram de entradas até `10^18` podem ultrapassar `i128`; copiar diretamente as fórmulas de inteiros arbitrários do Python seria incorreto.
