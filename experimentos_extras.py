# -*- coding: utf-8 -*-
"""
Experimento EXTRA (não faz parte dos oficiais X1–X13; declarado no relatório, seção X13).

No X13 oficial o DiscoFlash começa novo e as cargas gravam poucos milhares de páginas num
dispositivo de 320.000: a coleta de lixo nunca roda e a amplificação de escrita fica em 1.
Aqui o dispositivo é menor (32.000 blocos lógicos = 500 blocos apagáveis) e é primeiro
PREENCHIDO até uma fração da capacidade lógica; depois recebe gravações aleatórias de 1 bloco
sobre a parte ocupada (5 vezes a capacidade). Varia-se a ocupação lógica e o padrão de acesso
(uniforme × "quente/frio": 80% das gravações em 20% dos blocos).

Uso:  python3 experimentos_extras.py     -> resultados/extra_flash.csv e graficos/extra_flash_wa.png
"""
import csv
import os
import random
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

from simes import parametros as P          # noqa: E402
from simes.disco import DiscoFlash         # noqa: E402

LOGICOS = 32000
OCUPACOES = (0.5, 0.75, 0.9, 1.0)
PADROES = ("uniforme", "quente_frio")


def rodar(ocupacao, padrao, semente=2026):
    rng = random.Random(semente)
    d = DiscoFlash(total_blocos_logicos=LOGICOS)
    usados = int(LOGICOS * ocupacao)
    for b in range(usados):                                # preenchimento sequencial
        d.atender(b, 1, "E")
    host0, prog0, apag0 = d.paginas_gravadas_host, d.paginas_programadas, d.apagamentos
    quentes = max(1, usados // 5)
    tempo = 0.0
    n = 5 * LOGICOS
    for _ in range(n):
        if padrao == "quente_frio" and rng.random() < 0.8:
            b = rng.randrange(quentes)
        else:
            b = rng.randrange(usados)
        tempo += d.atender(b, 1, "E")[0]
    host = d.paginas_gravadas_host - host0
    prog = d.paginas_programadas - prog0
    return {
        "ocupacao_logica": ocupacao, "padrao": padrao, "gravacoes_host": host,
        "paginas_programadas": prog, "apagamentos": d.apagamentos - apag0,
        "amplificacao_escrita": round(prog / float(host), 4),
        "tempo_medio_gravacao_ms": round(tempo / n, 4),
    }


def main():
    linhas = [rodar(o, p) for p in PADROES for o in OCUPACOES]
    os.makedirs(os.path.join(AQUI, "resultados"), exist_ok=True)
    with open(os.path.join(AQUI, "resultados", "extra_flash.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0]))
        w.writeheader()
        w.writerows(linhas)
    for l in linhas:
        print(l)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    fig, ax = plt.subplots(figsize=(6, 4))
    for p, marca in zip(PADROES, ("o", "s")):
        pts = [l for l in linhas if l["padrao"] == p]
        ax.plot([100 * l["ocupacao_logica"] for l in pts], [l["amplificacao_escrita"] for l in pts],
                marker=marca, label=p.replace("_", "/"))
    ax.set_xlabel("ocupação lógica do dispositivo (%)")
    ax.set_ylabel("amplificação de escrita")
    ax.set_title("EXTRA — flash com coleta de lixo gulosa (reserva de %d%%)" % round(100 * P.FLASH_AREA_RESERVA))
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    os.makedirs(os.path.join(AQUI, "graficos"), exist_ok=True)
    fig.savefig(os.path.join(AQUI, "graficos", "extra_flash_wa.png"), dpi=120)


if __name__ == "__main__":
    main()
