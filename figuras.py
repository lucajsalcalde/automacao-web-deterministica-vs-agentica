"""
Gera as figuras do artigo, no padrão de publicação.

POR QUE UM SCRIPT SEPARADO
--------------------------
As figuras que o analise.py produzia serviam para inspecionar os dados durante o
desenvolvimento. Figura de artigo tem outra exigência: precisa ser legível
impressa em escala de cinza, mostrar a incerteza da medida, e vir acompanhada de
título e fonte no padrão que a norma define.

O DEFEITO QUE ESTE SCRIPT CORRIGE
---------------------------------
A figura anterior das curvas de degradação mostrava as taxas como PONTOS EXATOS.
O trabalho dedica parágrafos a explicar por que o intervalo de Wilson é
necessário — várias condições dão 0% ou 100%, e afirmar certeza a partir de 16
observações seria indefensável — e então apresentava um gráfico que faz
exatamente isso. Aqui cada curva vem com sua faixa de confiança.

LEGIBILIDADE EM ESCALA DE CINZA
-------------------------------
O artigo pode ser impresso em preto e branco. Cor sozinha não distingue as
curvas, e em várias condições três delas ficam sobrepostas em 100% — só a última
desenhada apareceria. Por isso cada braço tem traço, marcador e espessura
próprios, e não apenas cor.

SAÍDA
-----
    data/analise/fig1_robustez.png          curvas com intervalo de Wilson
    data/analise/fig2_custo_confiabilidade.png
    data/analise/fig5_equilibrio.png        ponto de equilíbrio econômico
    data/analise/legendas_figuras.txt       títulos e fontes prontos para colar

USO
---
    python figuras.py
    python figuras.py --cinza        simula a impressão em preto e branco
"""

from __future__ import annotations

import argparse
import os

from analise import (CSV_ENTRADA, DIR_SAIDA, NOME_BRACO, ORDEM_BRACOS,
                     ORDEM_TIPOS, carregar, filtrar, wilson)
from modelo_economico import (CAMBIO, DIAS_UTEIS, NOME, ORDEM, custo_anual,
                              medir, ponto_de_equilibrio)

# Traço, marcador e espessura distintos por braço. A cor é redundante de
# propósito: retirada, a figura continua legível.
ESTILO = {
    "deterministic": {"ls": "-",  "marker": "o", "lw": 1.6, "cor": "#1f77b4"},
    "xpath":         {"ls": (0, (3, 1, 1, 1)), "marker": "v", "lw": 1.8, "cor": "#ff7f0e"},
    "semantic":      {"ls": "--", "marker": "s", "lw": 2.2, "cor": "#2ca02c"},
    "healing":       {"ls": "-.", "marker": "^", "lw": 2.0, "cor": "#d62728"},
    "agentic":       {"ls": ":",  "marker": "D", "lw": 2.6, "cor": "#9467bd"},
}

TITULO_TIPO = {
    "cosmetico": "(a) degradação cosmética",
    "estrutural": "(b) degradação estrutural",
    "semantico": "(c) degradação semântica",
}


def _plt(cinza: bool):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    # Tamanhos pensados para a largura de impressão (cerca de 16 cm). Na coleta
    # 1 a figura era gerada com 13 polegadas de largura e reduzida no artigo, e
    # as letras ficavam com menos de 5 pt impressas.
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Liberation Sans", "DejaVu Sans"],
        "font.size": 8,
        "axes.titlesize": 8.5,
        "axes.labelsize": 8,
        "xtick.labelsize": 7.5,
        "ytick.labelsize": 7.5,
        "legend.fontsize": 7.5,
        "figure.dpi": 300,
    })
    if cinza:
        for e in ESTILO.values():
            e["cor"] = "black"
    return plt


# ------------------------------------------------------------------ figura 1

def _virgula(valor, _pos=None) -> str:
    """Rótulo de eixo com vírgula decimal, como no texto do artigo."""
    texto = f"{valor:.2f}".rstrip("0").rstrip(".") if valor % 1 else f"{valor:.0f}"
    return texto.replace(".", ",")


def figura_robustez(linhas, plt, cinza: bool) -> str:
    """
    Curvas de degradação, com intervalo de confiança de Wilson a 95%.

    A faixa sombreada é a informação que faltava. Sem ela, a queda do braço
    semântico de 62% para 0% parece uma medida precisa; com ela, fica visível
    que 62% com 16 observações comporta valores entre 39% e 82%.
    """
    niveis = sorted({r["nivel"] for r in linhas})
    from matplotlib.ticker import FuncFormatter
    fig, eixos = plt.subplots(1, 3, figsize=(6.9, 2.75), sharey=True)

    # DESLOCAMENTO HORIZONTAL por braço.
    #
    # Sob degradação cosmética, quatro dos cinco braços acertam 100% em todos
    # os níveis. Desenhados na mesma abscissa, viram uma curva só — a última
    # desenhada esconde as outras — e as quatro faixas de confiança se somam
    # numa mancha opaca que sugere incerteza muito maior que a real.
    #
    # Um deslocamento de poucos centésimos separa as curvas sem distorcer a
    # leitura: a escala do eixo vai de 0 a 1, e o afastamento máximo é 0,024.
    passo = 0.012
    desloc = {b: (i - (len(ORDEM_BRACOS) - 1) / 2) * passo
              for i, b in enumerate(ORDEM_BRACOS)}

    for eixo, tipo in zip(eixos, ORDEM_TIPOS):
        for braco in ORDEM_BRACOS:
            xs, ys, lo, hi = [], [], [], []
            for n in niveis:
                sub = filtrar(linhas, approach=braco, drift_type=tipo, nivel=n)
                if not sub:
                    continue
                s = sum(1 for r in sub if r["ok"])
                a, b = wilson(s, len(sub))
                xs.append(n + desloc[braco])
                ys.append(100 * s / len(sub))
                lo.append(100 * a)
                hi.append(100 * b)
            if not xs:
                continue
            e = ESTILO[braco]
            # BARRAS DE ERRO, e não faixa contínua.
            #
            # Duas razões. A primeira é de leitura: sob degradação cosmética
            # quatro braços têm o mesmo intervalo, [81, 100], e quatro faixas
            # sobrepostas viram um bloco opaco que sugere incerteza maior que
            # a real.
            #
            # A segunda é metodológica, e é a que decide. A intensidade foi
            # medida em CINCO pontos discretos. Uma faixa contínua entre 0,25
            # e 0,50 afirma algo sobre 0,37, que não foi medido. A barra de
            # erro diz o que se sabe, e só onde se sabe.
            eixo.errorbar(
                xs, ys,
                yerr=[[y - l for y, l in zip(ys, lo)],
                      [h - y for y, h in zip(ys, hi)]],
                label=NOME_BRACO[braco], color=e["cor"], ls=e["ls"],
                marker=e["marker"], lw=e["lw"] * 0.75, markersize=3.6,
                markerfacecolor="white", markeredgewidth=0.9,
                elinewidth=0.7, capsize=1.8, capthick=0.7, alpha=0.95)
        eixo.set_title(TITULO_TIPO[tipo])
        eixo.set_xlabel("intensidade da degradação")
        eixo.set_xticks(niveis)
        eixo.xaxis.set_major_formatter(FuncFormatter(_virgula))
        eixo.set_ylim(-6, 108)
        eixo.grid(alpha=0.25, linewidth=0.5)

    eixos[0].set_ylabel("taxa de sucesso (%)")
    # Legenda embaixo, numa linha só: ao lado dos painéis ela roubava largura,
    # e dentro deles cobria as curvas que ficam em 100%.
    alcas, rotulos = eixos[0].get_legend_handles_labels()
    fig.legend(alcas, rotulos, loc="lower center", ncol=5, frameon=False,
               bbox_to_anchor=(0.5, -0.02), handlelength=2.6, columnspacing=1.4)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    destino = os.path.join(DIR_SAIDA, "fig1_robustez.png")
    fig.savefig(destino, bbox_inches="tight")
    plt.close(fig)
    return destino


# ------------------------------------------------------------------ figura 2

def figura_custo_confiabilidade(linhas, plt, cinza: bool) -> str:
    """
    O compromisso central do trabalho, em um gráfico: confiabilidade contra
    custo por execução. Escala logarítmica no eixo do tempo porque as durações
    variam por duas ordens de grandeza entre os braços.
    """
    com_drift = [r for r in linhas if r["nivel"] > 0]
    fig, (esq, dir_) = plt.subplots(1, 2, figsize=(11.5, 4.2))

    # --- painel (a): tempo por execução, em escala logarítmica
    dados, nomes = [], []
    for b in ORDEM_BRACOS:
        sub = filtrar(com_drift, approach=b)
        if sub:
            dados.append([r["duracao"] for r in sub])
            nomes.append(NOME_BRACO[b])
    # A API do boxplot mudou de "labels" para "tick_labels" na 3.9 e de "vert"
    # para "orientation" na 3.10. Definir os rótulos por fora evita depender
    # da versão instalada.
    esq.boxplot(dados, widths=0.55,
                medianprops={"color": "black", "linewidth": 1.4},
                flierprops={"marker": ".", "markersize": 3, "alpha": 0.4})
    esq.set_xticks(range(1, len(nomes) + 1))
    esq.set_xticklabels(nomes, rotation=15, ha="right")
    esq.set_ylabel("duração por execução (s)")
    esq.set_yscale("log")
    esq.set_title("(a) tempo de execução")
    esq.grid(alpha=0.25, axis="y", linewidth=0.6)

    # --- painel (b): confiabilidade contra custo
    for b in ORDEM_BRACOS:
        sub = filtrar(com_drift, approach=b)
        if not sub:
            continue
        taxa = 100 * sum(1 for r in sub if r["ok"]) / len(sub)
        custo = sum(r["custo"] for r in sub) / len(sub)
        e = ESTILO[b]
        dir_.scatter(custo, taxa, s=110, color=e["cor"], marker=e["marker"],
                     edgecolors="black", linewidths=0.9, zorder=3)
        # Deslocamento à esquerda no ponto mais à direita, para o rótulo não
        # sair da área do gráfico.
        dx = -10 if custo > 0.10 else 9
        dir_.annotate(NOME_BRACO[b], (custo, taxa), fontsize=9,
                      textcoords="offset points", xytext=(dx, 7),
                      ha="right" if dx < 0 else "left")
    dir_.set_xlabel("custo médio por execução (US$)")
    dir_.set_ylabel("taxa de sucesso sob degradação (%)")
    dir_.set_title("(b) confiabilidade x custo")
    dir_.set_ylim(-6, 112)
    dir_.set_xlim(-0.02, 0.20)
    dir_.grid(alpha=0.25, linewidth=0.6)

    fig.tight_layout()
    destino = os.path.join(DIR_SAIDA, "fig2_custo_confiabilidade.png")
    fig.savefig(destino, bbox_inches="tight")
    plt.close(fig)
    return destino


# ------------------------------------------------------------------ figura 5

def figura_equilibrio(plt, cinza: bool, hora: float, conserto_h: float,
                      latencia: float = 1.0, tipo: str = "semantico",
                      csv_entrada: str = CSV_ENTRADA) -> str:
    """
    Custo anual em função da frequência de mudança de interface.

    É a tradução visual do critério de decisão. O ponto em que as curvas se
    cruzam é onde a escolha muda — e os dois painéis mostram que esse ponto
    depende fortemente do volume de execuções.
    """
    medidas = medir(csv_entrada)
    custo_conserto = hora * conserto_h
    frequencias = [f / 2 for f in range(0, 49)]     # 0 a 24 mudanças/ano

    fig, eixos = plt.subplots(1, 2, figsize=(11.5, 4.2))
    cenarios = [("(a) 1 execução por dia", DIAS_UTEIS),
                ("(b) 20 execuções por dia", DIAS_UTEIS * 20)]

    for eixo, (titulo, execs) in zip(eixos, cenarios):
        for b in ORDEM:
            if b not in medidas:
                continue
            ys = [custo_anual(medidas, b, execs, f, custo_conserto,
                              False, latencia, tipo)["total"]
                  for f in frequencias]
            e = ESTILO[b]
            eixo.plot(frequencias, ys, label=NOME[b], color=e["cor"],
                      ls=e["ls"], lw=e["lw"])
        # Marca o cruzamento contra o self-healing, que é o comparador da
        # hipótese primária e a melhor alternativa determinística.
        pe = ponto_de_equilibrio(medidas, "healing", execs, custo_conserto,
                                 False, latencia, tipo)
        if pe is not None and 0 < pe <= frequencias[-1]:
            eixo.axvline(pe, color="black", ls=(0, (1, 2)), lw=1.2)
            eixo.annotate(f"equilíbrio\n{pe:.1f}/ano", (pe, eixo.get_ylim()[1]),
                          fontsize=8, ha="center", va="top",
                          xytext=(0, -6), textcoords="offset points")
        eixo.set_title(titulo)
        eixo.set_xlabel(f"mudanças de interface por ano (tipo: {tipo})")
        eixo.set_ylabel("custo anual (R$)")
        eixo.grid(alpha=0.25, linewidth=0.6)
        eixo.set_xlim(0, frequencias[-1])

    eixos[1].legend(loc="center left", bbox_to_anchor=(1.02, 0.5), frameon=True)
    fig.tight_layout()
    destino = os.path.join(DIR_SAIDA, "fig5_equilibrio.png")
    fig.savefig(destino, bbox_inches="tight")
    plt.close(fig)
    return destino


# ------------------------------------------------------------------ legendas

LEGENDAS = """LEGENDAS DAS FIGURAS — padrão RICT/Facens (NBR 6022)

Regra do modelo: número e título ACIMA da figura, em Arial 12; fonte ABAIXO,
centralizada, em Arial 10.

Figuras complementares, fora do artigo. A Figura 1 do artigo é
fig1_sucesso.png, gerada por figura1_sucesso.py.

--------------------------------------------------------------------------
Figura complementar A - Taxa de sucesso por abordagem, tipo e intensidade de degradação
[figura]
Fonte: elaborado pelo autor (2026). As barras verticais representam o intervalo
de confiança de Wilson a 95%, com 16 execuções por ponto (menos na abordagem
agêntica quando houve exclusão). Os pontos de cada abordagem foram deslocados
horizontalmente para não se sobreporem.

--------------------------------------------------------------------------
Figura complementar B - Tempo de execução e compromisso entre confiabilidade e custo
[figura, uso opcional]
Fonte: elaborado pelo autor (2026). Eixo do tempo em escala logarítmica.

--------------------------------------------------------------------------
Figura complementar C - Custo anual por abordagem em função da frequência de mudanças semânticas
[figura, uso opcional]
Fonte: elaborado pelo autor (2026). Modelo da seção 3.9 do artigo: custo médio
por execução concluída, 0,5 h por intervenção a R$ 250 a hora, câmbio de
R$ 5,19 por dólar, sem o custo da automação parada.
"""


def main() -> None:
    ap = argparse.ArgumentParser(description="Gera as figuras do artigo")
    ap.add_argument("--csv", default=CSV_ENTRADA)
    ap.add_argument("--cinza", action="store_true",
                    help="simula impressão em preto e branco")
    ap.add_argument("--hora", type=float, default=250.0)
    ap.add_argument("--conserto", type=float, default=0.5)
    ap.add_argument("--latencia", type=float, default=1.0,
                    help="dias úteis até perceber que a automação quebrou")
    args = ap.parse_args()

    try:
        plt = _plt(args.cinza)
    except ImportError:
        print("matplotlib não instalado — rode: pip install matplotlib")
        return

    os.makedirs(DIR_SAIDA, exist_ok=True)
    linhas = carregar(args.csv)
    print(f"{len(linhas)} execuções carregadas de {args.csv}")
    if args.cinza:
        print("MODO ESCALA DE CINZA — para conferir a legibilidade impressa\n")

    for gerar in (
        lambda: figura_robustez(linhas, plt, args.cinza),
        lambda: figura_custo_confiabilidade(linhas, plt, args.cinza),
        lambda: figura_equilibrio(plt, args.cinza, args.hora, args.conserto,
                                  args.latencia, csv_entrada=args.csv),
    ):
        print(f"  gravada: {gerar()}")

    destino = os.path.join(DIR_SAIDA, "legendas_figuras.txt")
    with open(destino, "w", encoding="utf-8") as f:
        f.write(LEGENDAS)
    print(f"  gravada: {destino}")

    print("\n  A tabela do modelo logístico vem de modelo_logistico.py.")
    print("\n  Confira a legibilidade impressa antes de fechar o artigo:")
    print("      python figuras.py --cinza")


if __name__ == "__main__":
    main()
