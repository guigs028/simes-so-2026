# Relatório — SimES: simulador de E/S e de sistema de arquivos

**Integrantes:** Guilherme Dentzien Silva

**Repositório:** https://github.com/guigs028/simes-so-2026

## 0. Resumo

Implementei o núcleo completo (SSTF, SCAN, custo de CPU por polling e por interrupção e o sistema de arquivos com resolução de caminhos, tabelas de arquivos abertos e `unlink` com o arquivo aberto), os seis módulos (M1 a M6), a integração ponta a ponta e o bônus do armazenamento flash. Os 26 testes automáticos passam, sem nenhum pulado, e os 13 experimentos rodaram com status `ok`.

Dos resultados, três me chamaram mais atenção. O primeiro é que escalonar pela posição do braço (SSTF/SCAN) derruba a resposta média da carga sequencial de 10.241 ms para 17 ms, só que o SCAN, por ir até a borda do disco, acaba pior que o FCFS na carga concentrada, coisa que o LOOK resolve. O segundo é que num dispositivo rápido a interrupção gasta mais CPU que o polling (1,12% contra 0,53%), que é o mesmo motivo de o Linux usar polling em SSDs NVMe. O terceiro é que a forma como os arquivos estão organizados no disco pesa muito no desempenho. A alocação contígua falhou 113 vezes por fragmentação externa mesmo tendo 2.052 blocos livres, e a indexada, que sem cache tem resposta 3,6 vezes maior que a contígua no banco de dados, passa na frente dela quando entra uma cache LRU, que absorve 74,5% dos acessos (quase todos nos blocos de índice).

## 1. Escopo realizado

| Parte | Situação | Observação |
|---|---|---|
| N1 · Dispositivo (fornecido) | usado | — |
| N2 · Subsistema de E/S (SSTF, SCAN, polling, interrupção) | completo | `simes/es/escalonadores.py`, `simes/es/cpu.py` |
| N3 · Sistema de arquivos | completo | `simes/arquivos/sistema.py` |
| N4 · Experimentos X1–X5 e relatório | completo | hipóteses registradas antes da análise em `hipoteses.md` |
| M1 · Cache de blocos | completo | LRU e FIFO, write-through e write-back |
| M2 · Escalonamento avançado | completo | C-SCAN, LOOK, C-LOOK e DEADLINE |
| M3 · DMA e buffering | completo | buffer simples, duplo e circular |
| M4 · Links e compartilhamento | completo | hard links, simbólicos, pendentes, ciclos |
| M5 · Proteção e travas | completo | rwx, ACL, travas consultivas e mandatórias |
| M6 · Alocação em disco | completo | encadeada, FAT e indexada (2 níveis) |
| Integração ponta a ponta | completo | `simes/integracao.py`, experimento X12 |
| Bônus flash | completo | `DiscoFlash` com FTL de log e coleta de lixo gulosa; experimento extra declarado em X13 |

Resultado dos testes automáticos (`python3 executar.py testes`): "Ran 26 tests in 0.003s OK"

## 2. Implementação

### N2 — Subsistema de E/S

**Onde:** `simes/es/escalonadores.py` (classes `SSTF` e `SCAN`, funções auxiliares `_mais_proxima`, `_a_frente` e `_direcao_ate`) e `simes/es/cpu.py` (`custo_cpu`).

**Decisões:** usei uma função auxiliar só, a `_mais_proxima`, para escolher a requisição de menor distância entre as que passam num filtro. Ela percorre a fila na ordem de chegada e só troca de candidata quando a distância é estritamente menor, então a regra de empate (ganha a mais antiga) sai de graça. O SSTF chama essa função sem filtro e devolve a direção do movimento que fez. O SCAN filtra as requisições à frente na direção atual, lembrando que o mesmo cilindro conta para os dois lados. Quando não sobra ninguém à frente, ele coloca a borda em `passar_por`, inverte a direção e pega a mais próxima da borda. No custo de CPU, o polling cobra a duração inteira do atendimento e a interrupção cobra `n_blocos × (custo da interrupção + cópia)`, como está no arquivo. Conferi com os três exemplos do livro (fila 98, 183, 37, 122, 14, 124, 65, 67, cabeça em 53) e bateu: SSTF com 236 cilindros, SCAN descendo com 236 e subindo com 331.

**Dificuldades:** o caso do SCAN sem requisição à frente. Na primeira versão ele invertia sem ir até a borda, o que na prática é o LOOK, e só percebi quando o teste do livro (331 subindo) não fechou.

### N3 — Sistema de arquivos

**Onde:** `simes/arquivos/sistema.py`, métodos `resolver`, `_resolver_pai`, `_nova_entrada`, `mkdir`, `create`, `open`, `read`, `write`, `seek`, `close` e `unlink`.

**Decisões:** o `resolver` anda pelos componentes do caminho e lê cada diretório antes de procurar o nome dentro dele, por isso `/a/b/c` custa 3 leituras e `/` não custa nenhuma. Se um componente no meio do caminho não é diretório, dá `ENOTDIR`. O `_resolver_pai` resolve o caminho do pai e lê o próprio pai, o que segue a regra "até o pai, inclusive", e é usado do mesmo jeito por `mkdir`, `create`, `unlink`, `link` e `symlink`. O `mkdir` aloca 1 bloco e grava o bloco do diretório novo e o do pai, e o `create` aloca `ceil(tamanho/4096)` blocos, não grava conteúdo e grava o pai.

As tabelas seguem o Silberschatz (11.1.2). A tabela global tem uma `EntradaGlobal` por arquivo, com o contador de aberturas, e a de cada processo tem uma `EntradaProcesso` por abertura, com posição e modo. Assim dois processos conseguem ler o mesmo arquivo cada um na sua posição.

O `read` limita o intervalo ao tamanho do arquivo e lê só os blocos que contêm `[posição, fim)`. O `write` estende o arquivo pelo alocador quando precisa, grava os blocos do intervalo, atualiza o tamanho e marca o FCB como sujo, e no modo `a` a posição vai para o fim antes de gravar. O `close` só grava o bloco do pai se o FCB estiver sujo. No `unlink`, a entrada é removida e `links` é decrementado; se o arquivo ainda está aberto ele continua legível, e os blocos só são liberados no último `close`, igual ao Unix.

Quanto aos erros, usei `EBADF` para handle inexistente ou modo incompatível, `EINVAL` para posição ou tamanho negativo, modo desconhecido ou handle já em uso, e `EISDIR` para abrir um diretório ou dar `unlink` nele.

**Dificuldades:** fazer a contagem de acessos bater certinho e ao mesmo tempo deixar os ganchos dos módulos no lugar certo. O `open`, por exemplo, tem que chamar `_verificar_permissao` depois de resolver o caminho e antes de mexer nas tabelas, senão uma negação deixa uma entrada órfã. Fui ajustando a ordem das operações pelos testes de custo (`(1, 2)`, `(2, 2)`, `(3, 1)` etc.).

### M1 — Cache de blocos

**Onde:** `simes/es/cache.py` (`Cache`, `CacheLRU`, `CacheFIFO`).

**Decisões:** guardei os blocos num `collections.OrderedDict` que vai do bloco para "sujo", em ordem de vítima (o primeiro é o próximo a sair). LRU e FIFO só mudam no método `_tocar`: a LRU faz `move_to_end` a cada acesso e a FIFO não faz nada. Em write-through, `escrever` devolve o próprio bloco, que nunca fica sujo. Em write-back, o bloco fica sujo e só os sujos que forem expulsos são devolvidos. Se `inserir` recebe um bloco que outra requisição já trouxe, isso conta como acesso e o bloco não é duplicado.

**Dificuldades:** nada de mais, o `OrderedDict` já resolve as duas políticas em O(1).

### M2 — Escalonamento avançado

**Onde:** `simes/es/escalonadores.py` (`CSCAN`, `LOOK`, `CLOOK`, `Deadline`).

**Decisões:** no C-SCAN, quando não há ninguém à frente, `passar_por = [max_cilindro, 0]`, e o retorno conta como deslocamento. O LOOK inverte sem ir até a borda. O C-LOOK salta direto para o menor cilindro pendente, e o salto também conta. O DEADLINE olha se alguma requisição já venceu o prazo e, se sim, atende a de prazo mais antigo mantendo a direção; se não, deixa o SSTF decidir. Os totais do livro conferiram: C-SCAN 382, LOOK 299 e C-LOOK 322.

**Dificuldades:** só tomar cuidado para manter a regra de empate em todos.

### M3 — DMA e buffering

**Onde:** `simes/es/cpu.py` (modo `dma`) e `simes/es/buffering.py` (`simular_buffering`).

**Decisões:** o buffering virou uma recorrência tirada direto das regras do arquivo. Para o bloco k, o buffer fica livre em `fim_gravação[k − n_buffers]`, o produtor começa em `max(fim da produção anterior, buffer livre)` (e a diferença vai somando no bloqueio), e a gravação começa em `max(fim da produção de k, fim da gravação de k − 1)`. Com isso os dois testes fecham: 1.000 ms com buffer simples e 802 ms com duplo, para 100 blocos de 2 ms e 8 ms.

**Dificuldades:** nenhuma.

### M4 — Links e compartilhamento

**Onde:** `simes/arquivos/sistema.py` (`resolver`, `link`, `symlink`, `percorrer`).

**Decisões:** quando o `resolver` encontra um link simbólico, ele troca o componente pelo alvo (que é sempre absoluto) mais o resto do caminho e recomeça da raiz. Por isso seguir um link custa as leituras do caminho do alvo, e o link não tem blocos próprios. No último componente ele só segue o link se `seguir_simbolico=True`, e depois de 8 links lança `ELOOP`.

Para contar `links_pendentes` no `open`, o `ENOENT` sai com a marca `link_pendente` quando o componente que faltou veio do alvo de um link, então um caminho comum que não existe não entra na conta. O `link` não segue o link simbólico do último componente (como o `link()` do POSIX) e recusa diretórios com `EPERM`.

O `percorrer` é uma busca em profundidade que guarda os diretórios já visitados na travessia. Os links simbólicos são resolvidos, o que custa leituras como no `find -L`. Alvo inexistente conta em `links_pendentes`, laço de links (l1 ↔ l2) conta num indicador extra que criei, `links_em_laco`, e voltar a um diretório já visitado conta em `ciclos_detectados`.

**Dificuldades:** separar link pendente de caminho inexistente comum, o que resolvi com a marca no erro.

### M5 — Proteção e travas

**Onde:** `simes/arquivos/sistema.py` (`_verificar_permissao`, `_verificar_trava`, `usuario`, `chmod`, `setacl`, `lock`, `unlock`).

**Decisões:** o `_verificar_permissao` segue as regras de custo do arquivo. Root custa 1, a primeira entrada da ACL do usuário custa a posição dela e, se o usuário não está na lista, custa `len(acl) + 1` e valem os bits do dono ou de "outros". A ACL tem precedência sobre os bits, então um usuário listado com `r--` não escreve mesmo que "outros" permita. `chmod` e `setacl` só funcionam para o dono ou para root (senão `EPERM`) e aceitam a permissão como texto ou como número, porque o simulador converte `600` em inteiro. O `lock` é uma trava exclusiva na entrada global, o `close` libera a trava se quem está fechando é o dono dela, e `unlock` vindo de outro processo dá `EPERM`.

**Dificuldades:** nenhuma.

### M6 — Alocação em disco

**Onde:** `simes/arquivos/alocacao.py` (`AlocadorEncadeado`, `AlocadorFAT`, `AlocadorIndexado`).

**Decisões:** a encadeada usa os primeiros blocos livres, o `estender` grava o ponteiro do antigo último bloco, e o `bloco_fisico` guarda um cursor em `fcb.extra['cursor']` e lê os blocos intermediários. A FAT usa os mesmos blocos, só que com os ponteiros numa tabela em memória, sem acessos extras. A indexada usa 1 bloco de índice até 1.024 blocos de dados e, passando disso, 1 bloco de primeiro nível mais `ceil(n/1024)` de segundo. Os índices ficam em `fcb.extra['indices']` e são reservados antes dos dados. Cada acesso custa 1 leitura (ou 2), e a extensão regrava todos os índices. Também acrescentei o indicador `blocos_indice` às métricas.

**Dificuldades:** a passagem de 1 para 2 níveis ao estender (de 1.024 para 1.025 blocos), em que é preciso acrescentar os índices que faltam e regravar todos.

### Integração ponta a ponta

**Onde:** `simes/integracao.py` (`historico_para_requisicoes`).

**Decisões:** ordenei o histórico pelo instante com `sorted`, que é estável e por isso mantém a ordem dos acessos de uma mesma operação, e depois criei as `Requisicao` com ids 0, 1, 2 e assim por diante.

**Dificuldades:** nenhuma.

### Bônus — armazenamento flash

**Onde:** `simes/disco.py` (`DiscoFlash`) e `experimentos_extras.py`.

**Decisões:** fiz uma FTL de log. Ela tem um mapa de bloco lógico para página física e um mapa reverso de página para lógico válido, além do contador de páginas válidas por bloco apagável, da fila de blocos apagados e de um bloco ativo que recebe as gravações. Regravar um bloco lógico invalida a página antiga. Quando sobram menos de 2 blocos apagados, roda a coleta de lixo gulosa, que pega o bloco cheio com menos páginas válidas, copia as válidas (leitura mais programação), apaga o bloco e devolve ele para a fila. Os 7% de páginas de reserva entram no tamanho físico, e as leituras custam `FLASH_LEITURA_MS` por página, sem busca nem rotação.

**Dificuldades:** a primeira versão esvaziava a fila de blocos livres durante a própria coleta, porque as cópias também consomem páginas. Resolvi mantendo 2 blocos de reserva e deixando a coleta usar o bloco ativo, e depois da correção um teste de consistência (mapa, páginas válidas e contadores) confirmou que a FTL estava certa.

## 3. Experimentos

As hipóteses abaixo foram escritas em `hipoteses.md` antes de eu abrir os CSVs, com base no livro e no modelo de custo, e estão reproduzidas sem alteração. Os tempos estão em ms, e "resposta" é o tempo entre a chegada e o fim do atendimento.

### X1 — Escalonadores sob quatro padrões de carga

**Hipótese:** SSTF e SCAN reduzem muito o deslocamento e a resposta média em relação ao FCFS nas cargas aleatória, concentrada e em rajadas, com ganho maior nas rajadas (fila longa). Na carga sequencial, com dois fluxos distantes, o FCFS alterna entre as regiões e paga uma busca longa por requisição, enquanto SSTF e SCAN ficam num fluxo só. O SSTF tem a menor resposta média, mas a maior espera máxima (inanição). O SCAN fica entre os dois, com espera mais uniforme.

**Fundamentação:** Silberschatz, seção 10.4. O FCFS é justo, mas ignora a posição do braço. O SSTF minimiza a busca a cada passo e pode adiar indefinidamente as requisições distantes, e o SCAN limita a espera ao tempo de uma varredura. No modelo, a busca custa 1 ms mais 0,003 ms por cilindro e cada requisição fora de sequência paga 4,17 ms de rotação, então diminuir a distância diminui direto o tempo de serviço.

**Configuração:** cargas padronizadas `es_sequencial`, `es_aleatoria`, `es_concentrada` e `es_rajadas` (2.000 requisições de 8 blocos), disco rígido, modo interrupção e sem cache. Variei o escalonador e medi deslocamento, resposta média e p95, espera máxima e vazão.

**Resultados:**

| Carga | Escalonador | Deslocamento (cil.) | Resposta média | Resposta p95 | Espera máx. | Vazão (req/s) |
|---|---|---:|---:|---:|---:|---:|
| sequencial | FCFS | 7.184.093 | 10.241,0 | 19.565,4 | 20.598,1 | 61,5 |
| sequencial | SSTF | 2.336.365 | 17,4 | 32,8 | 37,4 | 167,5 |
| sequencial | SCAN | 2.469.069 | 23,2 | 43,7 | 48,6 | 167,4 |
| aleatória | FCFS | 3.316.472 | 50,1 | 145,1 | 193,5 | 82,5 |
| aleatória | SSTF | 2.631.714 | 21,0 | 48,6 | 116,7 | 82,5 |
| aleatória | SCAN | 3.372.680 | 31,5 | 65,1 | 116,7 | 82,4 |
| concentrada | FCFS | 1.864.164 | 19,8 | 48,6 | 96,0 | 83,8 |
| concentrada | SSTF | 1.613.030 | 15,0 | 37,2 | 130,5 | 83,8 |
| concentrada | SCAN | 3.338.708 | 33,2 | 68,3 | 118,8 | 83,7 |
| rajadas | FCFS | 3.341.490 | 267,0 | 505,3 | 597,3 | 83,5 |
| rajadas | SSTF | 209.811 | 147,5 | 277,7 | 297,0 | 84,4 |
| rajadas | SCAN | 199.955 | 148,6 | 278,7 | 285,4 | 84,4 |

**Evidências:** resultados/es.csv (experimento=X1); graficos/X1_resposta_media_ms.png; graficos/X1_deslocamento_total.png; graficos/X1_espera_max_ms.png

**Análise:** a carga sequencial é o caso extremo. O FCFS fica alternando entre o fluxo perto do bloco 20.000 e o fluxo perto do 250.000, cerca de 3.600 cilindros a cada requisição. Com isso o disco não acompanha a taxa de chegada, a fila cresce sem parar e a resposta média chega a 10 s (vazão de 61 req/s contra 167). Já o SSTF e o SCAN atendem várias requisições seguidas do mesmo fluxo, com busca quase zero e sem latência rotacional quando o bloco é o seguinte, e a resposta cai 590 vezes.

Nas rajadas o ganho em deslocamento foi o maior de todos (−94%), porque com 50 requisições na fila o escalonador tem bastante opção para escolher. Mesmo assim a resposta só caiu pela metade, porque cada requisição continua pagando os 4,17 ms de rotação, que não dependem do escalonador.

Nas cargas aleatória e concentrada a carga é leve e a fila é curta, então o SSTF ganha pouco em deslocamento, mas ainda reduz a resposta média. O que eu não esperava foi o SCAN ter deslocamento maior que o FCFS nessas duas cargas, e na concentrada também resposta média maior (33,2 contra 19,8). Isso acontece por causa da regra do SCAN de ir até a borda. 80% das requisições estão nos primeiros 20% do disco (cilindros 0 a 999), mas o braço vai até o cilindro 4.999 a cada varredura e depois volta. É o defeito que o LOOK corrige, como aparece no X7.

A inanição do SSTF aparece na carga concentrada, em que a espera máxima dele (130,5) passa a do FCFS (96,0), porque os 20% de requisições fora da região quente ficam esperando enquanto continuam chegando requisições próximas. Na aleatória isso não aparece, porque a carga é leve demais para formar fila.

**Veredito:** PARCIALMENTE CONFIRMADA

### X2 — Polling × interrupção conforme o dispositivo

**Hipótese:** no disco rígido, o polling gasta de CPU quase todo o tempo em que o disco está ocupado e a interrupção gasta muito pouco. No dispositivo rápido, o atendimento é tão curto que o polling passa a gastar menos CPU que a interrupção (8 × 0,017 ms > 8 × 0,008 ms).

**Fundamentação:** Silberschatz 13.2.1 e 13.2.2. No polling a CPU fica testando o registrador de estado em espera ocupada enquanto o dispositivo trabalha, e no modelo isso custa a duração inteira do atendimento. Com interrupção a CPU fica livre e paga só o tratamento (0,015 ms) e a cópia (0,002 ms) de cada bloco. O livro comenta que o polling compensa quando o dispositivo é muito rápido, porque aí o custo fixo da interrupção (a troca de contexto) passa a dominar.

**Configuração:** FCFS, cargas aleatória e sequencial, disco rígido e dispositivo rápido (0,008 ms por bloco, sem busca nem rotação). Medi `cpu_es_pct`, que é a porcentagem do tempo total em que a CPU trabalhou para a E/S.

**Resultados:**

| Dispositivo | Carga | Polling (% CPU) | Interrupção (% CPU) | Resposta média |
|---|---|---:|---:|---:|
| HDD | aleatória | 86,28 | 1,12 | 50,10 |
| HDD | sequencial | 100,00 | 0,84 | 10.241,05 |
| rápido | aleatória | 0,53 | 1,12 | 0,064 |
| rápido | sequencial | 1,07 | 2,28 | 0,064 |

**Evidências:** resultados/es.csv (experimento=X2); graficos/X2_cpu.png

**Análise:** no disco rígido o polling ocupa a CPU durante todo o atendimento. Na carga sequencial, em que o FCFS satura o disco, isso dá praticamente 100% da CPU, e na aleatória o disco fica ocupado 86% do tempo. A interrupção custa 0,136 ms por requisição de 8 blocos, contra uns 10 ms de atendimento, o que fica abaixo de 1,2% da CPU.

No dispositivo rápido a situação se inverte. O atendimento leva 0,064 ms e o polling cobra só isso, enquanto a interrupção cobra 0,136 ms, mais que o dobro. Com 8 blocos por requisição, o ponto de equilíbrio fica num atendimento de 0,136 ms (0,017 ms por bloco), e abaixo disso o polling gasta menos CPU. Vale notar que o modo de CPU não muda a resposta, já que o modelo não desconta do tempo dos processos a CPU gasta com E/S.

**Veredito:** CONFIRMADA

### X3 — Acesso sequencial × direto

**Hipótese:** o compilador (acesso sequencial) tem menos tempo por acesso que o banco de dados (acesso direto), porque leituras de blocos vizinhos não pagam busca nem latência rotacional.

**Fundamentação:** Silberschatz 11.2 (métodos de acesso sequencial e direto) e o modelo do disco. Um bloco que vem logo depois do último transferido não paga os 4,17 ms de rotação, e com alocação contígua os blocos de um arquivo ficam lado a lado.

**Configuração:** cargas `fs_compilador` (3 builds que leem 40 fontes e cabeçalhos inteiros em pedaços de 4 KiB e gravam os objetos) e `fs_banco_dados` (tabela de 64 MiB com 3.000 transações em posições aleatórias e um log em modo acréscimo), com alocação contígua. Medi os acessos e o tempo por operação.

**Resultados:**

| Carga | Operações | Acessos | Tempo total | Tempo por acesso | `read`: tempo médio | `write`: tempo médio |
|---|---:|---:|---:|---:|---:|---:|
| compilador | 3.904 | 5.400 | 11.986,7 | 2,22 | 1,48 | 0,91 |
| banco de dados | 6.602 | 3.610 | 19.847,0 | 5,50 | 5,49 | 5,54 |

**Evidências:** resultados/arquivos.csv (experimento=X3); resultados/arquivos_por_operacao.csv; graficos/X3_tempo_por_operacao.png

**Análise:** nas duas cargas cada `read` lê 1 bloco, mas no compilador ele custa 1,48 ms e no banco de dados 5,49 ms. No banco cada leitura cai numa posição aleatória da tabela e paga busca e rotação (mais ou menos 1 ms + 4,17 ms + transferência). No compilador as leituras seguidas de um mesmo arquivo caem no bloco seguinte ao anterior, e o modelo cobra só a transferência (0,04 ms). A média de 1,48 ms vem da primeira leitura depois de cada `open`, que paga a rotação porque o `open` levou o braço até os diretórios. No fim, o tempo por acesso do banco ficou 2,5 vezes o do compilador. Um detalhe é que as gravações do log (512 bytes em modo `a`) regravam o mesmo bloco várias vezes, porque o modelo não junta escritas parciais, coisa que um sistema real faria na cache.

**Veredito:** CONFIRMADA

### X4 — Custo de resolver caminhos conforme a profundidade

**Hipótese:** o custo do `open` cresce linearmente com a profundidade: profundidade d custa d + 1 leituras de diretório, e o tempo cresce na mesma proporção.

**Fundamentação:** Silberschatz 11.3 (estrutura de diretórios em árvore) e as regras de custo. Para resolver `/n1/…/nd/arquivo` é preciso ler a raiz e os d diretórios intermediários, um bloco cada.

**Configuração:** 10 cargas geradas pelo próprio experimento, cada uma com um arquivo no fim de uma cadeia de 1 a 10 diretórios aberto 20 vezes, com alocação contígua. Medi os acessos e o tempo médio do `open`.

**Resultados:**

| Profundidade | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Acessos por `open` | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 |
| Tempo médio do `open` (ms) | 4,25 | 4,29 | 4,33 | 4,37 | 4,41 | 4,45 | 4,49 | 4,53 | 4,57 | 4,61 |

**Evidências:** resultados/arquivos_por_operacao.csv (experimento=X4, operacao=open); graficos/X4_open_por_profundidade.png

**Análise:** os acessos cresceram do jeito previsto (d + 1), mas o tempo cresceu só 0,04 ms por nível, e não na mesma proporção. Como os diretórios foram criados em sequência, o alocador contíguo (first-fit) colocou todos em blocos vizinhos: a raiz no 0, `/n1` no 1, `/n1/n2` no 2 e assim por diante. Então resolver o caminho vira uma leitura sequencial, em que só a primeira leitura paga a rotação (4,17 ms) e cada nível a mais paga só a transferência de um bloco (0,04 ms). Se a árvore estivesse espalhada pelo disco, cada nível pagaria uns 5 ms e o tempo acompanharia os acessos. Ou seja, a localidade dos diretórios no disco pesa tanto quanto o número de leituras.

**Veredito:** PARCIALMENTE CONFIRMADA

### X5 — Tabelas de arquivos abertos com vários processos

**Hipótese:** a tabela global fica bem menor que a soma das tabelas por processo, porque os processos compartilham os mesmos arquivos, e o contador de aberturas passa de 1.

**Fundamentação:** Silberschatz 11.1.2. O sistema mantém uma tabela global, com um FCB e um contador por arquivo aberto, e uma tabela por processo, com a posição e o modo de cada abertura. O que é compartilhado fica uma vez só, na global.

**Configuração:** carga `fs_servidor_web`, com 8 processos trabalhadores (P1 a P8) que, em 40 rodadas, abrem 3 de 12 páginas cada um, leem tudo e fecham. Medi o tamanho máximo das tabelas e do contador.

**Resultados:**

| Indicador | Valor |
|---|---:|
| Máximo da tabela global | 12 |
| Máximo de uma tabela de processo | 3 |
| Máximo da soma das tabelas de processo | 24 |
| Maior contador de aberturas | 6 |
| Acessos por `close` | 0 |

**Evidências:** resultados/arquivos.csv (experimento=X5); resultados/arquivos_por_operacao.csv; graficos/X5_tabelas.png

**Análise:** no pico os 8 processos têm 24 aberturas ao mesmo tempo (8 × 3), mas a tabela global tem só 12 entradas, uma por página, já que o tamanho dela depende do número de arquivos distintos e não do número de aberturas. Uma mesma página chegou a estar aberta por 6 processos ao mesmo tempo, cada um na sua posição (o teste `test_tabelas_compartilhadas` verifica isso). O `close` não custou nenhum acesso ao disco porque os arquivos só foram lidos, então o FCB ficou limpo e não havia metadado para gravar.

**Veredito:** CONFIRMADA

### X6 — Cache de blocos (M1)

**Hipótese:** o acerto cresce com a capacidade e a LRU supera a FIFO na carga com reúso (Zipf). Write-back faz menos escritas físicas que write-through, mas perde os blocos sujos na queda.

**Fundamentação:** Silberschatz 13.4.3 e 12.6. Quando o reúso está concentrado em poucos blocos, a LRU segura os mais usados, enquanto a FIFO pode expulsar um bloco popular só porque ele entrou cedo. A write-back junta várias escritas no mesmo bloco numa gravação só, mas os dados ficam apenas na memória até a sincronização periódica (5 s no modelo).

**Configuração:** carga `es_reuso` (3.000 requisições de 1 bloco sobre 4.000 blocos com popularidade de Zipf, 35% de escritas, uma requisição a cada 8 ms), FCFS e disco rígido. Variei LRU e FIFO, capacidades de 64 a 2.048 blocos e write-through e write-back, com uma queda de energia simulada na metade da carga.

**Resultados:**

| Configuração | Acerto (%) | Escritas físicas | Perdidos na queda | Resposta média | Espera máx. |
|---|---:|---:|---:|---:|---:|
| sem cache | — | 1.045 | 0 | 3.500,0 | 6.936,3 |
| LRU 64 WT | 39,6 | 1.045 | 0 | 74,7 | 257,8 |
| FIFO 64 WT | 31,3 | 1.045 | 0 | 436,1 | 885,5 |
| LRU 256 WT | 57,7 | 1.045 | 0 | 22,0 | 151,8 |
| LRU 256 WB | 57,7 | 543 | 55 | 47,6 | 1.271,1 |
| FIFO 256 WT | 53,2 | 1.045 | 0 | 25,9 | 151,8 |
| LRU 1.024 WT | 67,1 | 1.045 | 0 | 16,9 | 151,8 |
| LRU 1.024 WB | 67,0 | 541 | 55 | 38,9 | 1.416,8 |
| FIFO 1.024 WT | 67,1 | 1.045 | 0 | 16,9 | 151,8 |
| LRU/FIFO 2.048 | igual a 1.024 | | | | |

**Evidências:** resultados/es.csv (experimento=X6); graficos/X6_acerto.png; graficos/X6_escrita.png

**Análise:** sem cache o disco não dá conta de uma requisição a cada 8 ms (o atendimento médio de um bloco aleatório passa de 8 ms), e a fila cresce até chegar a 3,5 s de resposta média. Qualquer cache já estabiliza o sistema.

O acerto sobe rápido até uns 1.000 blocos e para em 67%. A partir daí todos os blocos acessados cabem na cache e só sobram as faltas compulsórias (o primeiro acesso a cada bloco). Por isso 1.024 e 2.048 dão o mesmo resultado, e por isso também LRU e FIFO empatam nessas capacidades. Com cache pequena a LRU ganha (39,6% contra 31,3% com 64 blocos), e a diferença na resposta é grande (74,7 contra 436,1 ms), porque cada falta a mais vai parar numa fila que já está carregada.

A write-back cortou as escritas físicas pela metade (541 contra 1.045) e perdeu 55 blocos na queda, o que nunca acontece com write-through. O que a hipótese não previa é que, a partir de 256 blocos, a write-back ficou com resposta média e espera máxima piores que a write-through (espera máxima de 1.417 contra 152 ms). Isso acontece porque a sincronização periódica joga de uma vez na fila do disco todos os blocos sujos dos últimos 5 s, e com FCFS as leituras que chegam logo depois esperam essa rajada inteira. Então a write-back troca menos escritas por picos de latência, que é o mesmo problema que o Linux tenta amenizar gravando em segundo plano com mais frequência e usando escalonadores que priorizam leituras.

**Veredito:** PARCIALMENTE CONFIRMADA

### X7 — Escalonadores avançados e inanição (M2)

**Hipótese:** com carga crescente, o FCFS satura primeiro. O SSTF tem a menor resposta média, mas a maior espera máxima. C-SCAN e C-LOOK têm espera mais uniforme. O DEADLINE mantém a média perto do SSTF e limita a espera máxima.

**Fundamentação:** Silberschatz 10.4. As variantes circulares tratam as duas pontas do disco do mesmo jeito, o que deixa a espera mais uniforme, e o LOOK evita ir até a borda à toa. O DEADLINE vem do mq-deadline do Linux, em que cada requisição recebe um prazo e as vencidas passam na frente.

**Configuração:** carga concentrada gerada com 2.000 requisições e intervalo médio de 14, 11, 9 e 8 ms (de 71 a 125 req/s), disco rígido e os 7 escalonadores. O prazo do DEADLINE é de 200 ms para leitura e 2.000 ms para escrita.

**Resultados:** resposta média e espera máxima (ms).

| Escalonador | Média 14 ms | Média 9 ms | Média 8 ms | Espera máx. 14 ms | Espera máx. 9 ms | Espera máx. 8 ms |
|---|---:|---:|---:|---:|---:|---:|
| FCFS | 15,8 | 61,2 | 281,6 | 71,3 | 244,5 | 616,2 |
| SSTF | 13,4 | 21,9 | 27,8 | 86,6 | 265,5 | 327,1 |
| SCAN | 29,5 | 41,8 | 49,0 | 114,4 | 210,0 | 226,7 |
| C-SCAN | 40,1 | 55,6 | 65,8 | 116,9 | 172,1 | 223,1 |
| LOOK | 13,8 | 24,6 | 31,4 | 72,8 | 161,9 | 170,3 |
| C-LOOK | 14,7 | 28,4 | 38,9 | 102,8 | 139,4 | 163,8 |
| DEADLINE | 13,4 | 22,0 | 28,4 | 86,6 | 204,2 | 244,8 |

**Evidências:** resultados/es.csv (experimento=X7); graficos/X7_espera_max_ms.png; graficos/X7_resposta_p95_ms.png

**Análise:** o FCFS é o primeiro a saturar, e com 8 ms entre chegadas a resposta média dele explode para 282 ms. O SSTF tem sempre a menor média, mas a espera máxima dele cresce mais que a dos outros (327 ms), que é justamente a inanição das requisições fora da região quente.

SCAN e C-SCAN são os piores em média, porque nessa carga concentrada eles vão até a borda (cilindro 4.999) sem precisar, o mesmo efeito do X1, e o C-SCAN ainda paga a volta até o cilindro 0. LOOK e C-LOOK eliminam essa viagem e ficam com as menores esperas máximas (170,3 e 163,8 ms com 8 ms entre chegadas), o que confirma a espera mais uniforme. Entre as circulares, a hipótese valeu para o C-LOOK, mas não para o C-SCAN.

Com carga leve (14 e 11 ms) nenhum prazo vence, e o DEADLINE se comporta igual ao SSTF. Com carga alta ele mantém a média do SSTF (28,4 contra 27,8) e reduz a espera máxima em 25% (244,8 contra 327,1). A espera ainda passa dos 200 ms do prazo porque o disco não é preemptivo e várias requisições podem vencer ao mesmo tempo, já que o prazo só decide a ordem e não garante quando o atendimento começa. Curiosamente, nessa carga o LOOK, mesmo sem prazo nenhum, segurou melhor a espera.

**Veredito:** PARCIALMENTE CONFIRMADA

### X8 — DMA e buffering (M3)

**Hipótese:** a CPU da interrupção cresce com o tamanho da requisição e a do DMA fica constante; com 1 bloco o DMA não compensa. O buffer duplo sobrepõe produção e gravação, e o circular (3) quase não melhora sobre o duplo com taxas constantes.

**Fundamentação:** Silberschatz 13.2.3 e 13.4.2. Com DMA a controladora copia os blocos sozinha e interrompe a CPU uma vez só no fim (custo fixo de 0,018 ms), enquanto na E/S programada a CPU é interrompida e copia a cada bloco (0,017 ms por bloco). O buffer duplo deixa o produtor preenchendo um buffer enquanto o dispositivo grava o outro.

**Configuração:** 1.000 requisições aleatórias de 1, 8, 64 e 256 blocos (uma a cada 40 ms), FCFS, nos modos polling, interrupção e DMA. No buffering, 500 blocos, produção de 2 ms e gravação de 0,5, 2 e 8 ms, com 1, 2 e 3 buffers.

**Resultados:** CPU gasta com E/S (% do tempo total).

| Blocos por requisição | Polling | Interrupção | DMA |
|---:|---:|---:|---:|
| 1 | 25,17 | 0,0423 | 0,0448 |
| 8 | 25,87 | 0,3383 | 0,0448 |
| 64 | 31,44 | 2,7059 | 0,0448 |
| 256 | 50,52 | 10,8204 | 0,0448 |

Buffering (500 blocos, produção de 2 ms):

| Gravação | Buffers | Tempo total | Bloqueio do produtor | Utilização do dispositivo (%) |
|---:|---:|---:|---:|---:|
| 0,5 | 1 / 2 / 3 | 1.250,0 / 1.000,5 / 1.000,5 | 249,5 / 0 / 0 | 20,0 / 25,0 / 25,0 |
| 2,0 | 1 / 2 / 3 | 2.000,0 / 1.002,0 / 1.002,0 | 998,0 / 0 / 0 | 50,0 / 99,8 / 99,8 |
| 8,0 | 1 / 2 / 3 | 5.000,0 / 4.002,0 / 4.002,0 | 3.992,0 / 2.988,0 / 2.980,0 | 80,0 / 99,95 / 99,95 |

**Evidências:** resultados/es.csv (experimento=X8); resultados/buffering.csv; graficos/X8_cpu.png; graficos/X8_buffering.png

**Análise:** o custo da interrupção cresce linearmente com o número de blocos, de 0,04% até 10,8%, enquanto o do DMA fica parado em 0,045%. Com 1 bloco o DMA é até um pouco pior (0,0448 contra 0,0423), porque programar a controladora (0,003 ms) custa mais que copiar um bloco (0,002 ms). Isso confirma que o DMA só vale a pena em transferências grandes, e é por isso que as controladoras reais usam DMA para blocos e E/S programada para registradores e dispositivos de caractere.

No buffering, o buffer duplo dá o maior ganho quando produção e gravação levam o mesmo tempo (2 ms). Nesse caso o tempo cai pela metade (de 2.000 para 1.002 ms) e o dispositivo sai de 50% para 99,8% de uso. Quando o dispositivo é mais rápido (0,5 ms), o gargalo passa a ser o produtor e o buffer duplo só tira o bloqueio. Quando é mais lento (8 ms), o gargalo é o dispositivo e nenhuma quantidade de buffers evita o bloqueio do produtor, mas pelo menos o dispositivo passa a gravar sem parar (99,95%) e o tempo cai de 5.000 para 4.002 ms. Com taxas constantes, 3 buffers dão praticamente o mesmo que 2 (diferença de 8 ms de bloqueio em 4 s). O buffer circular só faria diferença com taxas variando, para absorver rajadas.

**Veredito:** CONFIRMADA

### X9 — Links e compartilhamento (M4)

**Hipótese:** o hard link sobrevive ao `unlink` do original e o link simbólico fica pendente. l1 ↔ l2 gera ELOOP, o `percorrer` detecta os ciclos, hard link para diretório dá EPERM, e seguir um link simbólico custa leituras extras (a resolução recomeça na raiz).

**Fundamentação:** Silberschatz 11.3.4 (diretórios em grafo acíclico e geral). Um hard link é uma segunda entrada para o mesmo FCB, com contador de links, e o arquivo só some quando o contador chega a zero. Já um link simbólico guarda um caminho, que pode deixar de existir. Proibir hard links para diretórios mantém o grafo acíclico, mas os links simbólicos podem criar ciclos, que a travessia precisa detectar.

**Configuração:** carga `fs_links`, com 3 diretórios, 3 arquivos, um hard link, links simbólicos para arquivo e para diretório, um link de volta (`/dados/sub/volta → /atalhos`) e um par em laço (l1 ↔ l2). A carga faz leituras pelos links, três travessias (`percorrer`) e `unlink` dos originais, com alocação contígua.

**Resultados:**

| Indicador | Valor |
|---|---:|
| Erros: ENOENT / ELOOP / EPERM | 1 / 1 / 1 |
| `links_pendentes` (1 no `open` + 2 nas travessias) | 3 |
| `ciclos_detectados` | 5 |
| `links_em_laco` (indicador extra) | 6 |
| Diretórios / arquivos visitados pelo `percorrer` | 11 / 9 |
| Acessos médios: `open` / `percorrer` | 4,92 / 44,0 |

**Evidências:** resultados/arquivos.csv (experimento=X9); resultados/arquivos_por_operacao.csv (experimento=X9)

**Análise:** depois do `unlink /dados/a.txt`, o `open /atalhos/a_rigido` continuou funcionando e lendo os mesmos blocos, e o FCB ficou com 1 link, então só seria liberado no último `unlink`. Já depois do `unlink /dados/b.txt`, o `/atalhos/b_simbolico` passou a dar ENOENT, virando um link pendente, igual a um atalho quebrado. Os erros esperados também apareceram: `link /dados /atalhos/dir_rigido` deu EPERM e o par l1 ↔ l2 deu ELOOP depois de 8 saltos.

O `open` custou em média 4,92 acessos, contra 2 a 3 num caminho sem links, porque cada link simbólico faz a resolução recomeçar da raiz. Por exemplo, `/atalhos/dados_simbolico/sub/c.txt` lê `/` e `/atalhos`, recomeça em `/dados/sub/c.txt` e lê `/`, `/dados` e `/dados/sub`.

Cada `percorrer` custou 44 acessos e não entrou em laço. Só que os 5 "ciclos" incluem tanto voltas de verdade (`/dados/sub/volta → /atalhos → dados_simbolico → /dados`) quanto diretórios que foram alcançados por dois caminhos sem ciclo nenhum. A regra do enunciado conta qualquer reencontro, então ela superestima os ciclos reais. O `find -L` de verdade só acusa laço quando o diretório é um ancestral no caminho atual.

**Veredito:** CONFIRMADA

### X10 — Proteção e travas (M5)

**Hipótese:** o custo de verificação cresce com o tamanho da ACL, e as travas mandatórias bloqueiam operações (EBUSY) enquanto as consultivas só registram a violação.

**Fundamentação:** Silberschatz 11.6 (listas de acesso × bits rwx) e 11.1.2 (travas). A ACL dá um controle mais fino, mas é uma lista que precisa ser percorrida a cada `open`, enquanto os 9 bits têm custo constante. Uma trava mandatória é imposta pelo sistema, como no Windows, e uma consultiva depende de os processos consultarem a trava, como no `flock`/`fcntl` do Linux.

**Configuração:** carga `fs_protecao`, com os usuários alice (P1, dona dos arquivos), bob (P2) e carol (P3). O `notas.txt` tem permissão 600 e uma ACL de 11 entradas, com bob na 11ª com `r--`. O `conta.txt` tem permissão 666 e é aberto em `rw` por P1 e P2, com `lock`. Rodei a carga com bloqueio consultivo e com mandatório.

**Resultados:**

| Indicador | Consultivo | Mandatório |
|---|---:|---:|
| `open` negados (EACCES) | 41 | 41 |
| `verificacoes` (unidades de custo) | 961 | 961 |
| Travas concedidas / negadas | 40 / 20 | 40 / 20 |
| `violacoes_consultivo` | 40 | — |
| `bloqueados_mandatorio` | — | 40 |
| Total de EBUSY | 20 | 60 |
| Acessos totais | 613 | 573 |

**Evidências:** resultados/arquivos.csv (experimento=X10); resultados/arquivos_por_operacao.csv (experimento=X10)

**Análise:** as aberturas de `notas.txt` custam 11 unidades para bob (que está na 11ª entrada) e 12 para alice e carol, que não estão na lista (`len(acl) + 1`). A conta fecha: 1 + 220 + 220 + 240 + 240 + 40 = 961. As de `conta.txt`, que não tem ACL, custam 1. Ou seja, quase todo o custo vem da lista longa, e ele cresce linearmente com a posição do usuário na ACL.

Carol, que está fora da ACL e tem "outros" = 0, foi negada 20 vezes. Bob também foi negado 20 vezes ao abrir para escrita (a entrada `r--` dele tem precedência) e mais 1 vez antes de a ACL existir, e o `chmod` dele num arquivo da alice deu EPERM.

As 20 travas negadas são iguais nos dois modos, porque o `lock` sempre respeita a trava. A diferença aparece no `read`/`write` de P2 enquanto P1 segura a trava. No modo consultivo, as 40 operações passam e só são contadas como violação (ou seja, poderia haver corrupção). No mandatório, as 40 são recusadas com EBUSY, o que de quebra evita 40 acessos ao disco (de 613 para 573).

**Veredito:** CONFIRMADA

### X11 — Métodos de alocação (M6)

**Hipótese:** a contígua tem o menor custo de acesso, mas realocações e fragmentação externa. A encadeada é péssima no acesso direto, a FAT tem o custo da contígua sem fragmentação externa, e a indexada paga 1 (ou 2) leituras de índice por acesso.

**Fundamentação:** Silberschatz 12.4 e 12.5. A contígua permite endereçamento direto, mas precisa de trechos livres contíguos (fragmentação externa) e tem que mover o arquivo para ele crescer. A encadeada não fragmenta, mas para chegar ao bloco i precisa ler os i anteriores. A FAT leva os ponteiros para uma tabela em memória, e a indexada (i-nodes) dá acesso direto ao custo do bloco de índice.

**Configuração:** carga `fs_alocacao` numa partição de 20.000 blocos, com os 4 alocadores. A carga cria arquivos de 1 a 256 blocos até 70% de ocupação, apaga 40% deles, cria mais até 85%, estende 60 arquivos em modo acréscimo e lê em posições aleatórias.

**Resultados:**

| Alocador | Acessos | Acessos/op | Tempo total | `read`: acessos | `write`: acessos | Falhas (ENOSPC) | Realocações | Segmentos livres (maior) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| contígua | 18.151 | 7,35 | 85.273,7 | 3,04 | 15,10 | 113 | 59 | 50 (154) |
| encadeada | 191.309 | 77,52 | 31.412,7 | 74,91 | 142,86 | 0 | 0 | 1 (1.939) |
| FAT | 4.690 | 1,90 | 9.886,2 | 3,06 | 2,00 | 0 | 0 | 1 (1.939) |
| indexada | 10.585 | 4,29 | 35.345,4 | 6,12 | 5,00 | 0 | 0 | 1 (1.805) |

**Evidências:** resultados/arquivos.csv (experimento=X11); resultados/arquivos_por_operacao.csv (experimento=X11); graficos/X11_alocacao.png; graficos/X11_fragmentacao.png

**Análise:** na leitura a contígua e a FAT empatam (3,04 e 3,06 acessos), mas a contígua ficou com o pior tempo total. Foram 59 realocações copiando arquivos inteiros, o que deu uns 15 acessos por `write` e 78 ms em média, e 113 escritas falharam com ENOSPC mesmo havendo 2.052 blocos livres, só que espalhados em 50 pedaços de no máximo 154 blocos. É a fragmentação externa do livro acontecendo na prática.

A encadeada usa o espaço todo sem falhar, mas cada acesso direto precisa ler a corrente, dando 75 acessos por leitura e 143 por escrita, 10 vezes os da contígua. A FAT foi a melhor nessa carga (9.886 ms e 1,9 acesso por operação), porque usa os mesmos blocos e tem a mesma flexibilidade da encadeada sem precisar ler a corrente. A indexada usou 134 blocos de índice, fez o dobro de acessos por leitura (6,12 contra 3,06) e ficou com tempo total 3,6 vezes o da FAT.

O resultado que eu não tinha previsto foi a encadeada fazer 18 vezes mais acessos que a indexada e mesmo assim levar menos tempo (31.413 contra 35.345 ms). Boa parte dos blocos que a encadeada lê na corrente são consecutivos no disco e saem em sequência sem pagar rotação, enquanto a indexada fica alternando entre o bloco de índice e o de dados, e cada troca paga busca e rotação. Então só contar acessos não é suficiente, a localidade faz muita diferença. No X12 dá para ver que uma cache resolve esse problema da indexada.

**Veredito:** PARCIALMENTE CONFIRMADA

### X12 — Integração ponta a ponta

**Hipótese:** SSTF/SCAN e a cache reduzem a resposta das requisições geradas pelo sistema de arquivos, e a cache ajuda mais no compilador, que relê os cabeçalhos.

**Fundamentação:** Silberschatz 12.6 e 13.4. O sistema de arquivos gera um fluxo de E/S de bloco cuja localidade depende da alocação e dos diretórios. O escalonador aproveita a localidade espacial e a cache aproveita a temporal.

**Configuração:** cargas `fs_compilador` e `fs_banco_dados`, com alocação contígua e indexada. O histórico de acessos ao disco é convertido em requisições (`historico_para_requisicoes`), que passam pelo simulador de E/S com FCFS, SSTF e SCAN, sem cache e com LRU de 1.024 blocos em write-back.

**Resultados:** resposta média (ms) e acerto da cache.

| Carga + alocação | FCFS | SSTF | SCAN | FCFS + LRU | SSTF + LRU | SCAN + LRU | Acerto LRU (%) |
|---|---:|---:|---:|---:|---:|---:|---:|
| compilador + contígua | 4.970,9 | 5.170,1 | 5.170,1 | 0,23 | 0,15 | 0,94 | 90,4 |
| compilador + indexada | 14.961,0 | 10.563,6 | 10.563,6 | 0,17 | 0,15 | 0,75 | 95,7 |
| banco + contígua | 6.158,8 | 4.106,2 | 4.055,1 | 2.151,3 | 1.411,9 | 1.382,4 | 4,9 |
| banco + indexada | 22.146,1 | 17.353,2 | 17.353,2 | 765,1 | 509,5 | 493,2 | 74,5 |

**Evidências:** resultados/es.csv (experimento=X12); graficos/X12_resposta.png

**Análise:** a carga do sistema de arquivos emite uma operação a cada 0,5 ms, bem mais rápido do que o disco consegue atender, então sem cache as filas crescem e as respostas chegam na casa dos segundos.

No compilador a cache resolve praticamente tudo. São 90% de acertos (cabeçalhos e diretórios relidos a cada build), e a resposta cai de 5 s para menos de 1 ms. Sem cache, o SSTF não melhorou a média do compilador contíguo (5.170 contra 4.971), porque os acessos já chegam quase em ordem de bloco e ele só reordena a fila, sem diminuir o trabalho. No banco de dados contíguo é o contrário. A tabela de 64 MiB (16.384 blocos) não cabe em 1.024 blocos, o acerto fica em só 4,9%, e aí o escalonador volta a fazer diferença, com o SSTF reduzindo a resposta em 33%.

Para mim o caso mais interessante foi o da indexada. Sem cache ela é 3,6 vezes mais lenta que a contígua no banco de dados (22.146 contra 6.159 ms com FCFS), porque cada acesso também lê o bloco de índice. Com a LRU, os blocos de índice ficam na cache (74,5% de acertos, quase todos neles), e a indexada passa a ser mais rápida que a contígua com cache (765 contra 2.151 ms com FCFS, com o tempo total igual, cerca de 15 s). A desvantagem da indexada praticamente desaparece quando os metadados ficam em memória, que é o que os sistemas reais fazem com os i-nodes.

Por fim, com cache e o disco quase ocioso, o SCAN foi o pior, porque leva o braço até a borda sem necessidade (deslocamento de 49.996 cilindros contra 80 do SSTF).

**Veredito:** CONFIRMADA

### X13 — Armazenamento flash (bônus)

**Hipótese:** o flash tem resposta muito menor que o disco rígido, e num dispositivo novo e quase vazio não há coleta de lixo e a amplificação de escrita fica em 1.

**Fundamentação:** Silberschatz 10.1.2 (dispositivos de estado sólido). O flash não tem partes móveis, mas também não regrava no lugar: ele grava por página, apaga por bloco inteiro e precisa de uma camada de tradução e de coleta de lixo. A coleta copia páginas válidas, o que gera a amplificação de escrita, e isso só acontece quando faltam blocos apagados.

**Configuração:** no experimento oficial, cargas `es_reuso` e `es_aleatoria`, FCFS, disco rígido contra um `DiscoFlash` novo de 320.000 blocos lógicos. Fiz também um **experimento extra**, em configuração separada e declarado aqui (`experimentos_extras.py` → `resultados/extra_flash.csv`), com um dispositivo de 32.000 blocos lógicos (500 blocos apagáveis mais 7% de reserva), que primeiro é preenchido até 50%, 75%, 90% ou 100% da capacidade lógica e depois recebe 160.000 gravações aleatórias de 1 bloco (5 vezes a capacidade), uniformes ou no padrão "quente/frio" (80% das gravações em 20% dos blocos).

**Resultados:**

| Carga | Dispositivo | Resposta média | Resposta p95 | Páginas do host | Páginas programadas | Apagamentos | Amplificação |
|---|---|---:|---:|---:|---:|---:|---:|
| reúso | HDD | 3.499,95 | 6.427,45 | — | — | — | — |
| reúso | flash | 0,087 | 0,200 | 1.045 | 1.045 | 0 | 1,00 |
| aleatória | HDD | 50,10 | 145,09 | — | — | — | — |
| aleatória | flash | 0,644 | 1,600 | 4.792 | 4.792 | 0 | 1,00 |

Experimento extra, amplificação de escrita:

| Ocupação lógica | 50% | 75% | 90% | 100% |
|---|---:|---:|---:|---:|
| uniforme | 1,17 | 1,81 | 3,19 | 7,29 |
| quente/frio | 1,34 | 1,99 | 3,41 | 7,59 |
| tempo médio por gravação, uniforme (ms) | 0,26 | 0,42 | 0,77 | 1,79 |

**Evidências:** resultados/es.csv (experimento=X13); resultados/extra_flash.csv; graficos/extra_flash_wa.png

**Análise:** no experimento oficial o flash respondeu de 80 a 40.000 vezes mais rápido que o disco rígido, e no reúso o disco está saturado enquanto o flash não. Como eu esperava, a amplificação ficou em 1, porque as cargas gravam poucos milhares de páginas num dispositivo de 342 mil páginas físicas, então a coleta nunca roda e não há nenhum apagamento.

Como o experimento oficial não chega a encher o dispositivo, fiz o extra para ver o efeito da coleta. Com o dispositivo cheio (100% da capacidade lógica, só com os 7% de reserva livres), cada página gravada pelo host custa 7,3 páginas programadas, e o tempo médio de gravação vai de 0,2 ms para 1,8 ms, quase 9 vezes mais. Deixando espaço livre (50% de ocupação), a amplificação cai para 1,17. Isso explica por que os SSDs reservam área extra e por que o TRIM, que avisa o dispositivo de quais blocos o sistema de arquivos liberou, melhora o desempenho.

Uma coisa que foi contra o que eu imaginava foi o padrão quente/frio. Eu esperava que ele fosse favorável, mas deu amplificação um pouco maior que o uniforme. Pelo que entendi, a coleta gulosa sem separar dados quentes e frios acaba misturando os dois no mesmo bloco, e os blocos frios ficam cheios de páginas válidas que precisam ser copiadas. As FTLs reais separam os fluxos quentes e frios justamente por isso.

**Veredito:** CONFIRMADA

## 4. Relação com sistemas reais

**Escalonadores de E/S.** O Linux não usa SSTF nem SCAN puros. Em discos rígidos o padrão costuma ser o mq-deadline, em que, segundo a documentação do kernel, toda leitura recebe ao entrar um prazo igual ao instante atual mais `read_expire` (500 ms por padrão), e as escritas recebem `write_expire` (5 s). Dentro desses prazos ele atende em lotes na ordem dos setores, o que é uma variante do LOOK com prazos, parecida com o meu DEADLINE. O X7 mostra bem o porquê, já que a ordem por posição dá uma boa média e o prazo corta a cauda da espera. O BFQ divide a banda entre processos e é usado em desktops para melhorar a interatividade. Em SSDs e NVMe o padrão é `none`, sem reordenar nada, porque sem busca nem rotação não há o que ganhar escalonando por posição (X13). Além disso, discos SATA modernos já reordenam internamente (NCQ), o que diminui o ganho de escalonar no sistema operacional.

**Polling × interrupção.** O Linux implementa polling para NVMe (`io_poll` e filas de polling), porque em dispositivos com latência de microssegundos interromper e trocar de contexto sai mais caro que esperar, como no X2. Já o DMA é usado em praticamente toda transferência de blocos (X8).

**Cache de páginas e gravação periódica.** O Linux tem uma cache de páginas unificada com write-back. Segundo a documentação do kernel, as threads de flush acordam periodicamente para gravar os dados antigos (`dirty_writeback_centisecs`, 5 s por padrão), e `dirty_expire_centisecs` define a partir de que idade um dado sujo é gravado, o que corresponde ao `INTERVALO_SYNC_MS` do simulador. A perda de alguns segundos de dados numa queda de energia (X6) é o motivo de existir o `fsync` e de os sistemas de arquivos usarem journaling nos metadados. O pico de latência da write-back que apareceu no X6 é o problema que o `dirty_background_ratio` tenta suavizar, começando a gravar antes de acumular demais. O Windows faz algo parecido com o lazy writer do Cache Manager, e o macOS com a cache unificada (UBC).

**Resolução de caminhos.** No X4 o custo cresce com a profundidade, mas Linux, Windows e macOS evitam isso com caches de nomes, como a dentry cache do Linux, que guarda na memória os componentes já resolvidos. Num `open` comum nenhum diretório é lido do disco.

**Tabelas de arquivos abertos.** O Linux usa a mesma estrutura de duas camadas do X5: a tabela de descritores de cada processo aponta para uma `struct file`, com posição e modo, que por sua vez aponta para o inode compartilhado. O Windows usa handles por processo que apontam para objetos de arquivo do kernel.

**Links.** ext4 e APFS têm hard links (com contador no inode) e links simbólicos, e o NTFS tem hard links, links simbólicos e junctions. Todos proíbem hard links para diretórios, com exceção do caso histórico do Time Machine no HFS+. O `find` e o `ls -R` detectam laços de forma parecida com o X9.

**Proteção e travas.** Linux e macOS usam os bits rwx com ACLs POSIX ou NFSv4 como extensão, e o Windows usa só ACLs, com entradas ordenadas e avaliadas em sequência. As travas do Linux (`flock`, `fcntl`) são consultivas, como no X10, e as travas mandatórias, que já eram opcionais, foram removidas do kernel. O Windows impõe travas mandatórias por intervalo de bytes (`LockFileEx`) e modos de compartilhamento no `CreateFile`, e é por isso que não dá para apagar um arquivo que está aberto no Word.

**Alocação.** A FAT (FAT32, exFAT) continua nos pendrives e cartões SD pela simplicidade e compatibilidade, e foi a melhor no X11 porque a tabela cabe em memória. O ext4 usa extents (trechos contíguos indexados numa árvore) e alocação atrasada, que juntam o acesso direto da contígua com a flexibilidade da indexada e evitam a fragmentação externa que derrubou a contígua no X11. O NTFS guarda listas de trechos (runs) na MFT, e o APFS usa copy-on-write com B-trees e extents. No fim, o que deixa a indexação barata na prática é a cache de metadados (inodes, MFT), como apareceu no X12.

**Flash.** SSDs e armazenamento de celulares (UFS, eMMC) têm FTL com coleta de lixo, área de reserva e TRIM. O APFS foi pensado para flash, e o F2FS, do Android, é um sistema de arquivos de log feito para reduzir a amplificação de escrita que aparece no experimento extra do X13.

## 5. Limitações do simulador

- **Disco rígido:** a busca é linear na distância (num disco real ela cresce com a raiz quadrada para distâncias curtas), a rotação é fixa em meia volta e todos os cilindros têm o mesmo tamanho (sem zonas). Também não há NCQ nem cache na controladora, então as vantagens de SSTF/SCAN podem ser menores num disco que reordena sozinho.
- **Concorrência:** um único dispositivo, sem preempção e sem paralelismo interno (SSDs reais atendem várias requisições em paralelo, em vários chips).
- **Escalonadores:** o DEADLINE que implementei não separa leituras e escritas em filas diferentes, não agrupa em lotes e não prioriza leituras como o mq-deadline.
- **CPU:** o custo de CPU só é contabilizado e não atrasa as requisições (por isso X2 e X8 têm a mesma resposta em todos os modos). Num sistema real o polling também tiraria tempo dos processos.
- **Sistema de arquivos:** não há cache de diretórios nem de inodes, então toda resolução vai ao disco, o que exagera o custo de caminhos longos (X4) e dos links simbólicos (X9). Também não há journaling nem gravação do FCB a cada escrita.
- **Cargas:** as operações chegam mais rápido (0,5 ms entre elas) do que o disco atende, o que satura as filas na integração (X12) e faz os números absolutos dependerem do tamanho da carga. Também não há escrita parcial de bloco, então o log de 512 bytes do banco regrava o bloco inteiro a cada `write` (X3).
- **Alocação:** só first-fit, sem extents nem alocação atrasada. A localidade favorável dos diretórios no X4 é consequência da ordem em que a carga cria os diretórios.
- **Flash:** FTL de log com coleta gulosa, sem separar dados quentes e frios, sem nivelamento de desgaste e sem TRIM. Como o experimento oficial não chega a encher o dispositivo, incluí o experimento extra.
- **Ciclos:** a regra de contagem de ciclos do M4 conta qualquer reencontro de diretório e por isso superestima os laços reais (X9).

## 6. Conclusões

1. Escalonar pela posição do braço faz muita diferença quando há fila. Na carga sequencial o FCFS saturou o disco (10 s de resposta), enquanto SSTF e SCAN responderam em 17 a 23 ms. Por outro lado, ir até a borda (SCAN, C-SCAN) pode ficar pior que o FCFS numa carga concentrada, e LOOK e C-LOOK corrigem isso.
2. A melhor média (SSTF) e a menor espera máxima não vêm do mesmo escalonador. Os prazos do DEADLINE cortam a cauda do SSTF sem perder a média, que é a escolha que o Linux faz com o mq-deadline.
3. O custo da E/S para a CPU depende do dispositivo. Interrupção e DMA são a escolha certa para o disco rígido, mas num dispositivo rápido o polling gasta menos CPU, e o DMA só compensa em transferências de vários blocos.
4. Uma cache pequena com LRU já estabiliza um disco saturado. A write-back reduz as escritas pela metade, mas perde dados na queda e gera picos de latência a cada sincronização.
5. A organização dos dados no disco importa mais que o número de acessos. A localidade deixa a cadeia de diretórios quase de graça (X4), faz a encadeada ser mais rápida que a indexada sem cache (X11), e a cache de metadados elimina a desvantagem da indexada (X12). Já a contígua sofre com fragmentação externa justamente quando o disco enche.
6. No flash, o desempenho de escrita depende do espaço livre. A amplificação vai de 1 com o dispositivo vazio até 7,3 com ele cheio, o que justifica a área de reserva e o TRIM.

## 7. Referências

- SILBERSCHATZ, A.; GALVIN, P. B.; GAGNE, G. *Operating System Concepts*. 9. ed. Wiley, 2013. Seções 10.1, 10.4, 11.1–11.3, 11.6, 12.4–12.6, 13.2–13.4.
- Documentação do kernel Linux, *Deadline IO scheduler tunables*: https://docs.kernel.org/block/deadline-iosched.html
- Documentação do kernel Linux, *BFQ (Budget Fair Queueing)*: https://docs.kernel.org/block/bfq-iosched.html
- Documentação do kernel Linux, *Documentation for /proc/sys/vm/* (dirty_writeback_centisecs, dirty_expire_centisecs): https://docs.kernel.org/admin-guide/sysctl/vm.html
- Documentação do Python, `collections.OrderedDict`: https://docs.python.org/3/library/collections.html#collections.OrderedDict

## 8. Declaração de autoria e uso de IA

**Quem fez o quê:** Guilherme Dentzien Silva: trabalho individual. Revisei a implementação e os resultados e reescrevi o texto final do relatório.

**Uso de ferramentas de IA:**

| Ferramenta | Finalidade | Onde foi usada (arquivos ou seções) |
|---|---|---|
| Claude (Anthropic) | Implementação do núcleo, dos módulos, da integração e do bônus; execução dos experimentos e dos gráficos; primeira versão do relatório, que depois reescrevi. O código foi conferido pelos testes automáticos e os números do relatório saem dos CSVs gerados | `simes/es/escalonadores.py`, `simes/es/cpu.py`, `simes/es/cache.py`, `simes/es/buffering.py`, `simes/arquivos/sistema.py`, `simes/arquivos/alocacao.py`, `simes/integracao.py`, `simes/disco.py`, `experimentos_extras.py`, `hipoteses.md`, seções 0 a 7 deste relatório (primeira versão) |
