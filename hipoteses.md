# Hipóteses registradas antes da análise dos resultados

Escritas a partir do Silberschatz e do modelo de custo (`simes/parametros.py`), antes de abrir
os CSVs. O relatório (`RELATORIO.md`, seção 3) usa estas hipóteses sem alteração.

- **X1:** SSTF e SCAN reduzem muito o deslocamento e a resposta média em relação ao FCFS nas
  cargas aleatória, concentrada e em rajadas; o ganho é maior nas rajadas (fila longa). Na
  carga sequencial (dois fluxos distantes), o FCFS alterna entre as regiões e paga uma busca
  longa por requisição, e SSTF/SCAN ficam num fluxo só. O SSTF tem a menor resposta média, mas
  a maior espera máxima (inanição); o SCAN fica entre os dois, com espera mais uniforme.
- **X2:** no disco rígido, o polling gasta de CPU quase todo o tempo em que o disco está
  ocupado, e a interrupção gasta muito pouco. No dispositivo rápido, o atendimento é tão curto
  que o polling passa a gastar menos CPU que a interrupção (8 × 0,017 ms > 8 × 0,008 ms).
- **X3:** o compilador (acesso sequencial) tem menos tempo por acesso que o banco de dados
  (acesso direto), porque leituras de blocos vizinhos não pagam busca nem latência rotacional.
- **X4:** o custo do `open` cresce linearmente com a profundidade: profundidade d custa d + 1
  leituras de diretório, e o tempo cresce na mesma proporção.
- **X5:** a tabela global fica bem menor que a soma das tabelas por processo, porque os
  processos compartilham os mesmos arquivos; o contador de aberturas passa de 1.
- **X6:** o acerto cresce com a capacidade e a LRU supera a FIFO na carga com reúso (Zipf);
  write-back faz menos escritas físicas que write-through, mas perde os blocos sujos na queda.
- **X7:** com carga crescente, o FCFS satura primeiro; o SSTF tem a menor resposta média, mas
  a maior espera máxima; C-SCAN e C-LOOK têm espera mais uniforme; o DEADLINE mantém a média
  perto do SSTF e limita a espera máxima.
- **X8:** a CPU da interrupção cresce com o tamanho da requisição e a do DMA fica constante;
  com 1 bloco o DMA não compensa. Buffer duplo sobrepõe produção e gravação; o circular (3)
  quase não melhora sobre o duplo com taxas constantes.
- **X9:** o hard link sobrevive ao `unlink` do original e o link simbólico fica pendente;
  l1 ↔ l2 gera ELOOP; o `percorrer` detecta os ciclos; hard link para diretório dá EPERM;
  seguir um link simbólico custa leituras extras (a resolução recomeça na raiz).
- **X10:** o custo de verificação cresce com o tamanho da ACL; as travas mandatórias bloqueiam
  operações (EBUSY) e as consultivas só registram a violação.
- **X11:** contígua tem o menor custo de acesso, mas realocações e fragmentação externa;
  encadeada é péssima no acesso direto; FAT tem o custo da contígua sem fragmentação externa;
  indexada paga 1 (ou 2) leituras de índice por acesso.
- **X12:** SSTF/SCAN e a cache reduzem a resposta das requisições geradas pelo sistema de
  arquivos; a cache ajuda mais no compilador, que relê os cabeçalhos.
- **X13:** o flash tem resposta muito menor que o disco rígido; num dispositivo novo e quase
  vazio não há coleta de lixo, e a amplificação de escrita fica em 1.
