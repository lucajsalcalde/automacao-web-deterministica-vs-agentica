"""
Regressão logística com penalização de Firth (seção 4.2 do artigo, coleta 2).

POR QUE SUBSTITUI modelo_falha.py
---------------------------------
Na coleta 1, oito classificadores tentavam prever a falha de uma execução a
partir dos quatro fatores do desenho. Com esses preditores, o melhor
classificador possível é consultar a taxa de cada célula: só com as taxas da
Figura 1 essa consulta acertava 95,1%, e a floresta aleatória obteve 95,2% e
95,3%. O modelo reproduzia a tabela de resultados, a validação agrupada vazava
pela intensidade zero (a mesma condição aparece sob três tipos) e o resultado
não tinha uso prático, porque numa mudança real tipo e intensidade são
desconhecidos.

Aqui a pergunta é outra, e inferencial: sob degradação semântica, quanto cada
fator muda a chance de concluir a tarefa, com intervalo de confiança.

O MODELO (fixado no plano de análise antes da coleta 2)
-------------------------------------------------------
Dados: degradação semântica, intensidades 0,25 a 1,0, braços papel e rótulo,
self-healing e agêntica. ID/CSS e XPath ficam de fora porque estão perto de 0%
em todos os níveis e não informam sobre efeitos. Execuções que contornaram a
interface ficam de fora, como nas taxas.

    logit P(sucesso) = b0
                     + b1 [agêntica] + b2 [papel e rótulo]      (ref.: self-healing)
                     + b3 int x [self-healing] + b4 int x [agêntica]
                     + b5 int x [papel e rótulo]
                     + b6 [The Internet]                          (ref.: SauceDemo)

    int = (intensidade - 0,625) / 0,25, ou seja, em passos de nível, centrada
    no meio da faixa. Assim exp(b1) é a razão de chances da agêntica contra o
    self-healing na intensidade média, e exp(b3..b5) é o efeito de subir um
    nível para cada braço.

POR QUE FIRTH
-------------
Vários braços acertam 100% em algum nível. A máxima verossimilhança comum
diverge nesses casos (separação): o coeficiente vai ao infinito. A penalização
de Firth (Firth, 1993; Heinze e Schemper, 2002) mantém as estimativas finitas e
reduz o viés de amostra pequena. Os intervalos são de verossimilhança
penalizada perfilada, que se comportam melhor que os de Wald nessa situação.

LIMITAÇÃO DECLARADA
-------------------
O pareamento pela semente não entra no modelo: cada linha é tratada como
independente. Como os pares tendem a ter resultados positivamente
correlacionados, isso deixa os intervalos dos contrastes entre braços, se
algo, mais largos. A inferência pareada da hipótese primária está em analise.py.

Uso:
    python modelo_logistico.py
    python modelo_logistico.py --autoteste
"""

from __future__ import annotations

import argparse
import csv
import math
import os

import numpy as np

RAIZ = os.path.dirname(os.path.abspath(__file__))
ENTRADA = os.path.join(RAIZ, "data", "results.csv")
SAIDA = os.path.join(RAIZ, "data", "analise")

BRACOS = ["healing", "agentic", "semantic"]
NOME = {"healing": "self-healing", "agentic": "agêntica", "semantic": "papel e rótulo"}
QUI2_95 = 3.841458820694124


# ------------------------------------------------------------- estimação

def _verossimilhanca_penalizada(X, y, beta) -> float:
    eta = X @ beta
    p = 1 / (1 + np.exp(-eta))
    p = np.clip(p, 1e-15, 1 - 1e-15)
    w = p * (1 - p)
    info = X.T @ (X * w[:, None])
    sinal, logdet = np.linalg.slogdet(info)
    return float(np.sum(y * np.log(p) + (1 - y) * np.log(1 - p)) + 0.5 * logdet)


def firth(X, y, fixo: dict | None = None, beta0=None, max_iter: int = 200,
          tol: float = 1e-9):
    """
    Maximiza a verossimilhança penalizada de Firth por Newton-Raphson.

    O escore modificado é X'(y - p + h(1/2 - p)), em que h é a diagonal da
    matriz chapéu H = W^(1/2) X (X'WX)^(-1) X' W^(1/2). `fixo` prende
    coeficientes em valores dados (usado no perfil dos intervalos).
    Passos são reduzidos à metade enquanto a função objetivo não aumenta.
    """
    n, k = X.shape
    fixo = fixo or {}
    livres = [j for j in range(k) if j not in fixo]
    beta = np.zeros(k) if beta0 is None else np.array(beta0, dtype=float)
    for j, v in fixo.items():
        beta[j] = v
    atual = _verossimilhanca_penalizada(X, y, beta)
    for _ in range(max_iter):
        eta = X @ beta
        p = 1 / (1 + np.exp(-eta))
        w = p * (1 - p)
        info = X.T @ (X * w[:, None])
        inv = np.linalg.inv(info)
        h = np.einsum("ij,jk,ik->i", X, inv, X) * w
        escore = X.T @ (y - p + h * (0.5 - p))
        sub = info[np.ix_(livres, livres)]
        passo = np.zeros(k)
        passo[livres] = np.linalg.solve(sub, escore[livres])
        fator = 1.0
        while True:
            candidato = beta + fator * passo
            valor = _verossimilhanca_penalizada(X, y, candidato)
            if valor >= atual - 1e-12 or fator < 1e-6:
                break
            fator /= 2
        beta, anterior, atual = candidato, atual, valor
        if np.max(np.abs(fator * passo)) < tol:
            break
    eta = X @ beta
    p = 1 / (1 + np.exp(-eta))
    w = p * (1 - p)
    cov = np.linalg.inv(X.T @ (X * w[:, None]))
    return beta, cov, atual


def intervalo_perfilado(X, y, beta, lmax, j: int) -> tuple[float, float]:
    """Intervalo de 95% por verossimilhança penalizada perfilada, por bisseção."""
    def desvio(valor):
        _, _, l = firth(X, y, fixo={j: valor}, beta0=beta)
        return 2 * (lmax - l) - QUI2_95

    def buscar(direcao):
        passo = 0.5
        fora = beta[j] + direcao * passo
        while desvio(fora) < 0 and abs(fora - beta[j]) < 50:
            passo *= 2
            fora = beta[j] + direcao * passo
        if desvio(fora) < 0:
            return direcao * math.inf
        dentro = beta[j]
        for _ in range(60):
            meio = (dentro + fora) / 2
            if desvio(meio) < 0:
                dentro = meio
            else:
                fora = meio
        return (dentro + fora) / 2

    return buscar(-1), buscar(+1)


# ------------------------------------------------------------- dados

def montar(caminho: str):
    with open(caminho, encoding="utf-8") as f:
        linhas = [r for r in csv.DictReader(f)
                  if r["drift_type"] == "semantico" and float(r["drift_level"]) > 0
                  and r["approach"] in BRACOS and r.get("error_type") != "contornou"]
    nomes = ["intercepto", "agêntica (vs. self-healing)", "papel e rótulo (vs. self-healing)",
             "nível, self-healing", "nível, agêntica", "nível, papel e rótulo",
             "The Internet (vs. SauceDemo)"]
    X, y = [], []
    for r in linhas:
        braco = r["approach"]
        passo = (float(r["drift_level"]) - 0.625) / 0.25
        X.append([1.0,
                  1.0 if braco == "agentic" else 0.0,
                  1.0 if braco == "semantic" else 0.0,
                  passo if braco == "healing" else 0.0,
                  passo if braco == "agentic" else 0.0,
                  passo if braco == "semantic" else 0.0,
                  1.0 if r["site"] == "the_internet" else 0.0])
        y.append(1.0 if r["success"].strip().lower() == "true" else 0.0)
    return np.array(X), np.array(y), nomes, linhas


def ajustar(caminho: str, exportar: bool = True) -> list[dict]:
    X, y, nomes, linhas = montar(caminho)
    if not len(y):
        print("Sem dados para o modelo.")
        return []
    beta, cov, lmax = firth(X, y)
    print("=" * 78)
    print("REGRESSÃO LOGÍSTICA COM PENALIZAÇÃO DE FIRTH — degradação semântica")
    print("=" * 78)
    print(f"{len(y)} execuções, {int(y.sum())} sucessos, braços: "
          f"{', '.join(NOME[b] for b in BRACOS)}\n")
    print(f"  {'termo':<36}{'coef.':>8}{'RC':>9}{'IC 95% (perfilado)':>24}")
    print("  " + "-" * 77)
    saida = []
    for j, nome in enumerate(nomes):
        lo, hi = intervalo_perfilado(X, y, beta, lmax, j)
        rc = math.exp(beta[j])
        texto_ic = f"{math.exp(lo):.2f} a {math.exp(hi):.2f}" if j else "—"
        print(f"  {nome:<36}{beta[j]:>8.3f}{(f'{rc:.2f}' if j else '—'):>9}{texto_ic:>24}")
        saida.append({"termo": nome, "coeficiente": round(float(beta[j]), 4),
                      "razao_chances": round(rc, 4) if j else "",
                      "ic95_inf": round(math.exp(lo), 4) if j else "",
                      "ic95_sup": round(math.exp(hi), 4) if j else "",
                      "erro_padrao_wald": round(math.sqrt(cov[j, j]), 4)})
    print("\n  Leitura: RC da agêntica é a razão de chances contra o self-healing na\n"
          "  intensidade média (0,625), ajustada pela aplicação. As linhas 'nível'\n"
          "  dão a razão de chances associada a subir um nível (0,25) em cada braço.")
    if exportar:
        os.makedirs(SAIDA, exist_ok=True)
        destino = os.path.join(SAIDA, "modelo_logistico.csv")
        with open(destino, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(saida[0].keys()))
            w.writeheader()
            w.writerows(saida)
        print(f"\n  Tabela exportada para {destino}")
    return saida


# ------------------------------------------------------------- autoteste

def autoteste() -> None:
    """
    Com uma única covariável binária, a estimativa de Firth coincide com o log
    da razão de chances corrigida por Haldane (0,5 somado a cada casela), e o
    intercepto com o log da chance do grupo de referência corrigida do mesmo
    modo. É um resultado conhecido (Firth, 1993) e dá valores fechados.
    """
    ok = True

    def checa(nome, obtido, esperado, tol=1e-5):
        nonlocal ok
        bom = abs(obtido - esperado) < tol
        ok &= bom
        print(f"  [{'OK ' if bom else 'ERRO'}] {nome:<44} obtido {obtido:>10.5f}  esperado {esperado:>10.5f}")

    print("AUTOTESTE — regressão logística de Firth\n")
    for a, b, c, d in ((12, 4, 5, 9), (10, 0, 3, 7), (8, 0, 0, 8)):
        # grupo x=1: a sucessos, b falhas; grupo x=0: c sucessos, d falhas
        X = np.array([[1, 1]] * (a + b) + [[1, 0]] * (c + d), dtype=float)
        y = np.array([1] * a + [0] * b + [1] * c + [0] * d, dtype=float)
        beta, _, _ = firth(X, y)
        checa(f"log RC Haldane, tabela {a},{b},{c},{d}", beta[1],
              math.log((a + .5) * (d + .5) / ((b + .5) * (c + .5))))
        checa(f"intercepto, tabela {a},{b},{c},{d}", beta[0], math.log((c + .5) / (d + .5)))
    print("\n" + ("TODOS OS TESTES PASSARAM." if ok else "HÁ FALHAS ACIMA."))


def main() -> None:
    ap = argparse.ArgumentParser(description="Regressão logística de Firth")
    ap.add_argument("--csv", default=ENTRADA)
    ap.add_argument("--autoteste", action="store_true")
    args = ap.parse_args()
    if args.autoteste:
        autoteste()
        return
    ajustar(args.csv)


if __name__ == "__main__":
    main()
