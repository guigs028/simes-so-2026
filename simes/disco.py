# -*- coding: utf-8 -*-
"""
Modelos de dispositivo de armazenamento.

DiscoHDD  — FORNECIDO. Implementa o modelo de custo descrito em parametros.py.
DiscoFlash — BÔNUS. Armazenamento flash (SSD, celulares): implementem se quiserem o bônus.

Todo dispositivo oferece:
    atender(bloco, n_blocos, tipo) -> (duracao_ms, distancia_cilindros)
    mover(cilindro)                -> (duracao_ms, distancia_cilindros)
    cilindro_atual                 (atributo)
"""
import math
from collections import deque

from simes import parametros as P


def cilindro_do_bloco(bloco):
    return bloco // P.BLOCOS_POR_CILINDRO


class DiscoHDD:
    nome = "hdd"

    def __init__(self, busca_minima_ms=P.BUSCA_MINIMA_MS,
                 busca_por_cilindro_ms=P.BUSCA_POR_CILINDRO_MS,
                 latencia_rotacional_ms=P.LATENCIA_ROTACIONAL_MS,
                 transferencia_ms_por_bloco=P.TRANSFERENCIA_MS_POR_BLOCO,
                 cilindros=P.CILINDROS, cilindro_inicial=0, nome=None):
        self.busca_minima_ms = busca_minima_ms
        self.busca_por_cilindro_ms = busca_por_cilindro_ms
        self.latencia_rotacional_ms = latencia_rotacional_ms
        self.transferencia_ms_por_bloco = transferencia_ms_por_bloco
        self.cilindros = cilindros
        self.cilindro_atual = cilindro_inicial
        self.ultimo_bloco = None          # último bloco transferido (para detectar sequência)
        self.deslocamento_total = 0       # cilindros percorridos pelo braço
        if nome:
            self.nome = nome

    def tempo_busca(self, distancia):
        if distancia == 0:
            return 0.0
        return self.busca_minima_ms + distancia * self.busca_por_cilindro_ms

    def mover(self, cilindro):
        """Leva o braço até um cilindro sem transferir dados (usado pelo SCAN e pelo C-SCAN)."""
        distancia = abs(cilindro - self.cilindro_atual)
        duracao = self.tempo_busca(distancia)
        self.cilindro_atual = cilindro
        self.ultimo_bloco = None
        self.deslocamento_total += distancia
        return duracao, distancia

    def atender(self, bloco, n_blocos=1, tipo="L"):
        cilindro = cilindro_do_bloco(bloco)
        distancia = abs(cilindro - self.cilindro_atual)
        duracao = self.tempo_busca(distancia)
        em_sequencia = self.ultimo_bloco is not None and bloco == self.ultimo_bloco + 1
        if not em_sequencia:
            duracao += self.latencia_rotacional_ms
        duracao += n_blocos * self.transferencia_ms_por_bloco
        ultimo = bloco + n_blocos - 1
        self.cilindro_atual = cilindro_do_bloco(ultimo)
        self.ultimo_bloco = ultimo
        self.deslocamento_total += distancia
        return duracao, distancia


def criar_disco(nome):
    """Fábrica usada pelos simuladores e pelos experimentos."""
    if nome == "hdd":
        return DiscoHDD()
    if nome == "rapido":
        return DiscoHDD(nome="rapido", **P.DISPOSITIVO_RAPIDO)
    if nome == "flash":
        return DiscoFlash()
    raise ValueError("dispositivo desconhecido: %s" % nome)


# ======================================================================
# BÔNUS — armazenamento flash
# ======================================================================
class DiscoFlash:
    """
    Simula um SSD com uma camada de tradução simples (FTL) do tipo log:

    - uma página flash = um bloco lógico de 4 KiB;
    - páginas são agrupadas em blocos apagáveis de FLASH_PAGINAS_POR_BLOCO_APAGAVEL páginas;
    - uma página só pode ser gravada se estiver apagada; apagar só é possível por bloco inteiro;
    - regravar um bloco lógico grava em uma página livre nova e invalida a antiga;
    - quando faltam páginas livres, a coleta de lixo escolhe um bloco apagável, copia as
      páginas ainda válidas para outro lugar e apaga o bloco.

    Não há busca nem latência rotacional. Tempos em parametros.py (FLASH_*).
    Indicadores que o dispositivo deve expor (atributos):
        paginas_gravadas_host      páginas pedidas pelo sistema
        paginas_programadas        páginas efetivamente gravadas (inclui cópias da coleta de lixo)
        apagamentos                blocos apagados
    Amplificação de escrita = paginas_programadas / paginas_gravadas_host.
    """
    nome = "flash"
    RESERVA = 2           # blocos apagados mantidos livres; abaixo disso, roda a coleta de lixo

    def __init__(self, total_blocos_logicos=P.TOTAL_BLOCOS):
        self.cilindros = P.CILINDROS            # só para a interface dos escalonadores
        self.cilindro_atual = 0
        self.deslocamento_total = 0
        self.paginas_gravadas_host = 0
        self.paginas_programadas = 0
        self.apagamentos = 0
        self.copias_coleta = 0
        por_bloco = P.FLASH_PAGINAS_POR_BLOCO_APAGAVEL
        paginas = int(math.ceil(total_blocos_logicos * (1 + P.FLASH_AREA_RESERVA)))
        self.n_blocos_apagaveis = int(math.ceil(paginas / float(por_bloco)))
        self.por_bloco = por_bloco
        self.mapa = {}                                        # lógico -> página física (FTL)
        self.dono = [None] * (self.n_blocos_apagaveis * por_bloco)   # física -> lógico válido
        self.validas = [0] * self.n_blocos_apagaveis
        self.livres = deque(range(self.n_blocos_apagaveis))   # blocos apagáveis já apagados
        self.ativo = self.livres.popleft()                    # bloco recebendo gravações
        self.proxima = 0                                      # próxima página livre do ativo
        self.em_uso = set()                                   # blocos cheios (candidatos à coleta)

    def mover(self, cilindro):
        return 0.0, 0

    def atender(self, bloco, n_blocos=1, tipo="L"):
        if tipo == "L":
            return n_blocos * P.FLASH_LEITURA_MS, 0
        duracao = 0.0
        for b in range(bloco, bloco + n_blocos):
            self.paginas_gravadas_host += 1
            duracao += self._gravar(b, coleta=False)
        return duracao, 0

    # ------------------------------------------------------------------ FTL
    def _gravar(self, logico, coleta):
        """Grava o bloco lógico numa página apagada e invalida a página antiga."""
        duracao = 0.0
        while self.proxima == self.por_bloco:                 # bloco ativo cheio: pega outro
            self.em_uso.add(self.ativo)
            self.ativo, self.proxima = self.livres.popleft(), 0
            if not coleta and len(self.livres) < self.RESERVA:
                duracao += self._coletar_lixo()               # as cópias vão para o ativo
        antiga = self.mapa.get(logico)
        if antiga is not None:
            self.dono[antiga] = None
            self.validas[antiga // self.por_bloco] -= 1
        pagina = self.ativo * self.por_bloco + self.proxima
        self.proxima += 1
        self.mapa[logico] = pagina
        self.dono[pagina] = logico
        self.validas[self.ativo] += 1
        self.paginas_programadas += 1
        return duracao + P.FLASH_PROGRAMACAO_MS

    def _coletar_lixo(self):
        """Coleta gulosa: apaga o bloco cheio com menos páginas válidas, depois de copiá-las."""
        duracao = 0.0
        while len(self.livres) < self.RESERVA:
            vitima = min(self.em_uso, key=lambda b: self.validas[b])
            if self.validas[vitima] == self.por_bloco:
                break                                          # nada a recuperar
            self.em_uso.discard(vitima)
            inicio = vitima * self.por_bloco
            for pagina in range(inicio, inicio + self.por_bloco):
                logico = self.dono[pagina]
                if logico is not None:
                    self.copias_coleta += 1
                    duracao += P.FLASH_LEITURA_MS + self._gravar(logico, coleta=True)
            self.apagamentos += 1
            duracao += P.FLASH_APAGAMENTO_MS
            self.livres.append(vitima)
        return duracao
