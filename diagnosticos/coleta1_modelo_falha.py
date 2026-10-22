"""
Modelo de previsão de falha de execução.

O QUE ESTE SCRIPT RESPONDE
--------------------------
A análise inferencial (analise.py) responde SE as abordagens diferem. Não
responde QUANTO cada fator contribui isoladamente para o risco de falha. É essa
segunda pergunta que sustenta o último objetivo específico do trabalho — propor
um critério de decisão entre abordagem determinística e agêntica.

Formulação: classificação binária. Dada uma execução caracterizada por
abordagem, tipo de degradação, intensidade e site, ela vai falhar?

    unidade de observação   1 execução
    n                       1.194 (as 1.200 menos as 6 que contornaram a interface)
    classe positiva         falha
    prevalência             33,0%
    LINHA DE BASE           67,0% — acurácia de um modelo que sempre chuta "sucesso"

A linha de base é obrigatória em qualquer leitura dos resultados. Um modelo com
67% de acurácia não aprendeu nada; apenas descobriu qual classe é mais comum.

DOIS VAZAMENTOS QUE ESTE SCRIPT EVITA DE PROPÓSITO
--------------------------------------------------
O conjunto de dados tem colunas que parecem features úteis e não são. Todas
descrevem o que aconteceu DURANTE a execução, e por isso carregam o desfecho:

  n_steps          sucesso 15 ações      falha 2 ações
  duration_s       sucesso 2,8 s         falha 11,7 s
  drift_classes    sucesso 56 classes    falha 13 classes

O caso das colunas de degradação é o mais traiçoeiro, porque a coluna parece
medir a condição experimental. Ela não mede. O acumulador soma a degradação
aplicada A CADA PÁGINA VISITADA; uma execução que falha na primeira página soma
uma vez só. A coluna mede quantas páginas foram percorridas — ou seja, mede o
desfecho.

Um modelo treinado com essas colunas atinge acurácia alta e não serve para nada:
ele não prevê a falha, ele a observa. O script demonstra isso explicitamente com
o "modelo ingênuo" (--ingenuo), para que a diferença fique registrada no
trabalho em vez de ser um erro silencioso.

O MODELO HONESTO usa apenas o que se conhece ANTES de executar:

    approach      abordagem de automação        5 níveis
    drift_type    tipo de degradação            3 níveis (ordinal: cumulativos)
    drift_level   intensidade                   contínua, 0 a 1
    site          aplicação                     2 níveis

DUAS PERGUNTAS, DOIS ESQUEMAS DE VALIDAÇÃO
------------------------------------------
Há exatamente 150 combinações distintas dos quatro fatores, com 8 execuções
cada. Isso cria uma escolha que muda o significado do resultado:

  estratificado  Linhas da mesma combinação podem cair no treino e no teste.
                 Responde: "numa condição JÁ MEDIDA, esta execução vai falhar?"
                 É a pergunta operacional, e é o esquema do artigo de referência.

  agrupado       Combinações inteiras ficam fora do treino.
                 Responde: "numa condição NUNCA MEDIDA, vai falhar?"
                 É extrapolação, é mais difícil, e a queda de desempenho entre
                 os dois esquemas mede quanto o modelo depende de ter visto
                 aquela célula.

O script roda os dois. Reportar só o primeiro seria otimista; só o segundo,
pessimista. A diferença entre eles é informação.

MÉTRICAS
--------
Acurácia sozinha engana com classes desbalanceadas. São reportadas acurácia,
precisão, revocação, F1 e AUC, além da matriz de confusão. As funções de métrica
são implementadas à mão e conferidas em --autoteste, mesma disciplina de
analise.py; o scikit-learn entra apenas com os modelos.

USO
---
    pip install scikit-learn matplotlib
    python modelo_falha.py --autoteste     confere as métricas e a validação
    python modelo_falha.py                 roda tudo
    python modelo_falha.py --ingenuo       inclui o modelo com vazamento
    python modelo_falha.py --repeticoes 20 mais repetições da validação
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import random
from collections import Counter, defaultdict

RAIZ = os.path.dirname(os.path.abspath(__file__))
ENTRADA = os.path.join(RAIZ, "data", "results.csv")
SAIDA = os.path.join(RAIZ, "data", "analise")

ROTULO_BRACO = {
    "deterministic": "frágil",
    "xpath": "xpath",
    "semantic": "semântico",
    "healing": "self-healing",
    "agentic": "agêntico",
}

# Os tipos são CUMULATIVOS (estrutural inclui cosmético; semântico inclui os
# dois). Codificar como ordinal preserva essa relação; one-hot a jogaria fora.
ORDEM_TIPO = {"cosmetico": 0, "estrutural": 1, "semantico": 2}


# ---------------------------------------------------------------- dados

def carregar(caminho: str = ENTRADA) -> list:
    if not os.path.exists(caminho):
        raise FileNotFoundError(f"{caminho} não encontrado — rode a coleta antes.")
    with open(caminho, encoding="utf-8") as f:
        linhas = list(csv.DictReader(f))
    # As execuções em que o agente contornou a interface saem, pela mesma razão
    # que saem da análise inferencial: não mediram o que o experimento mede.
    validas = [r for r in linhas if r.get("error_type") != "contornou"]
    if not validas:
        raise ValueError("nenhuma linha válida no arquivo.")
    return validas


def construir_features(rows: list, incluir_vazamento: bool = False):
    """
    Devolve (X, y, grupos, nomes).

    X        lista de listas de float
    y        1 = falhou, 0 = teve sucesso
    grupos   identificador da célula experimental, para a validação agrupada
    nomes    nome de cada coluna de X, para a importância de variáveis
    """
    bracos = sorted({r["approach"] for r in rows})
    sites = sorted({r["site"] for r in rows})

    nomes = [f"abordagem={ROTULO_BRACO.get(b, b)}" for b in bracos]
    nomes += ["tipo_degradacao(ordinal)", "intensidade"]
    nomes += [f"site={s}" for s in sites[1:]]  # k-1 colunas, evita colinearidade
    if incluir_vazamento:
        nomes += ["n_steps", "duration_s", "drift_classes", "drift_ids", "drift_textos"]

    X, y, grupos = [], [], []
    for r in rows:
        linha = [1.0 if r["approach"] == b else 0.0 for b in bracos]
        linha.append(float(ORDEM_TIPO[r["drift_type"]]))
        linha.append(float(r["drift_level"]))
        linha += [1.0 if r["site"] == s else 0.0 for s in sites[1:]]
        if incluir_vazamento:
            linha += [
                float(r["n_steps"] or 0),
                float(r["duration_s"] or 0),
                float(r["drift_classes"] or 0),
                float(r["drift_ids"] or 0),
                float(r["drift_textos"] or 0),
            ]
        X.append(linha)
        y.append(0 if r["success"] == "True" else 1)
        grupos.append(f'{r["approach"]}|{r["drift_type"]}|{r["drift_level"]}|{r["site"]}')

    return X, y, grupos, nomes


# ---------------------------------------------------- métricas (à mão)

def matriz_confusao(y, pred):
    """Devolve (vn, fp, fn, vp). Classe positiva = falha = 1."""
    vn = fp = fn = vp = 0
    for real, p in zip(y, pred):
        if real == 1 and p == 1:
            vp += 1
        elif real == 1 and p == 0:
            fn += 1
        elif real == 0 and p == 1:
            fp += 1
        else:
            vn += 1
    return vn, fp, fn, vp


def metricas(y, pred, prob=None) -> dict:
    vn, fp, fn, vp = matriz_confusao(y, pred)
    total = vn + fp + fn + vp
    acuracia = (vp + vn) / total if total else 0.0
    precisao = vp / (vp + fp) if (vp + fp) else 0.0
    revocacao = vp / (vp + fn) if (vp + fn) else 0.0
    f1 = (2 * precisao * revocacao / (precisao + revocacao)
          if (precisao + revocacao) else 0.0)
    saida = {"acuracia": acuracia, "precisao": precisao,
             "revocacao": revocacao, "f1": f1}
    if prob is not None:
        saida["auc"] = auc_roc(y, prob)
    return saida


def auc_roc(y, prob) -> float:
    """
    AUC pela estatística de Mann-Whitney: é a probabilidade de um positivo
    sorteado ao acaso receber pontuação maior que a de um negativo. Calculada
    por postos, o que trata empates corretamente — e empates são frequentes
    aqui, porque muitas execuções compartilham a mesma combinação de fatores.
    """
    pares = sorted(zip(prob, y))
    postos = [0.0] * len(pares)
    i = 0
    while i < len(pares):
        j = i
        while j + 1 < len(pares) and pares[j + 1][0] == pares[i][0]:
            j += 1
        medio = (i + j) / 2 + 1  # postos começam em 1
        for k in range(i, j + 1):
            postos[k] = medio
        i = j + 1

    soma_pos = sum(p for p, (_, rotulo) in zip(postos, pares) if rotulo == 1)
    n_pos = sum(1 for _, rotulo in pares if rotulo == 1)
    n_neg = len(pares) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    return (soma_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def resumo_distribuicao(valores) -> dict:
    """
    Média, desvio, mínimo, quartis e máximo — o formato da Tabela 6 do artigo
    de referência. Comparar algoritmos por um número pontual esconde o quanto
    esse número varia entre partições.
    """
    v = sorted(x for x in valores if not math.isnan(x))
    if not v:
        return {k: float("nan") for k in
                ("media", "desvio", "min", "q25", "mediana", "q75", "max")}
    n = len(v)
    media = sum(v) / n
    desvio = math.sqrt(sum((x - media) ** 2 for x in v) / (n - 1)) if n > 1 else 0.0

    def q(p):
        if n == 1:
            return v[0]
        pos = p * (n - 1)
        baixo = int(math.floor(pos))
        alto = min(baixo + 1, n - 1)
        return v[baixo] + (pos - baixo) * (v[alto] - v[baixo])

    return {"media": media, "desvio": desvio, "min": v[0], "q25": q(0.25),
            "mediana": q(0.50), "q75": q(0.75), "max": v[-1]}


# ------------------------------------------------ partições (à mão)

def particoes_estratificadas(y, k: int, semente: int):
    """
    K partições preservando a proporção de falhas em cada uma. Sem isso, uma
    partição poderia ficar sem nenhuma falha e a AUC seria indefinida.
    """
    rng = random.Random(semente)
    por_classe = defaultdict(list)
    for i, rotulo in enumerate(y):
        por_classe[rotulo].append(i)
    dobras = [[] for _ in range(k)]
    for rotulo in sorted(por_classe):
        indices = por_classe[rotulo][:]
        rng.shuffle(indices)
        for pos, idx in enumerate(indices):
            dobras[pos % k].append(idx)
    return dobras


def particoes_agrupadas(grupos, k: int, semente: int):
    """
    K partições em que uma célula experimental nunca aparece no treino e no
    teste ao mesmo tempo. Força o modelo a extrapolar para combinações inéditas.
    """
    rng = random.Random(semente)
    unicos = sorted(set(grupos))
    rng.shuffle(unicos)
    destino = {g: i % k for i, g in enumerate(unicos)}
    dobras = [[] for _ in range(k)]
    for i, g in enumerate(grupos):
        dobras[destino[g]].append(i)
    return dobras


def padronizar(treino_X, teste_X):
    """
    Média zero e desvio um, com os parâmetros estimados SOMENTE no treino.
    Estimar no conjunto completo vazaria informação do teste — erro comum e
    silencioso. KNN e SVM dependem disso; os demais são indiferentes.
    """
    n_col = len(treino_X[0])
    medias, desvios = [], []
    for j in range(n_col):
        col = [linha[j] for linha in treino_X]
        m = sum(col) / len(col)
        d = math.sqrt(sum((x - m) ** 2 for x in col) / len(col)) or 1.0
        medias.append(m)
        desvios.append(d)
    esc = lambda M: [[(l[j] - medias[j]) / desvios[j] for j in range(n_col)] for l in M]
    return esc(treino_X), esc(teste_X)


# ---------------------------------------------------------------- modelos

def construir_modelos(semente: int = 42) -> dict:
    """
    Os oito classificadores. A lista segue o artigo de referência, com duas
    substituições declaradas: LightGBM e XGBoost exigem instalação separada e
    foram trocados por GradientBoosting e HistGradientBoosting, que pertencem
    ao scikit-learn e cumprem o mesmo papel de comitê por reforço.
    """
    from sklearn.ensemble import (GradientBoostingClassifier,
                                  HistGradientBoostingClassifier,
                                  RandomForestClassifier)
    from sklearn.linear_model import LogisticRegression
    from sklearn.naive_bayes import GaussianNB
    from sklearn.neighbors import KNeighborsClassifier
    from sklearn.svm import SVC
    from sklearn.tree import DecisionTreeClassifier

    return {
        "GaussianNB": GaussianNB(),
        "KNN": KNeighborsClassifier(n_neighbors=15),
        "Árvore de Decisão": DecisionTreeClassifier(
            max_depth=6, min_samples_leaf=10, random_state=semente),
        "Random Forest": RandomForestClassifier(
            n_estimators=300, min_samples_leaf=5, random_state=semente, n_jobs=-1),
        "SVM": SVC(probability=True, random_state=semente),
        "Regressão Logística": LogisticRegression(max_iter=2000),
        "Gradient Boosting": GradientBoostingClassifier(random_state=semente),
        "HistGradientBoosting": HistGradientBoostingClassifier(random_state=semente),
    }


def avaliar(modelo, X, y, dobras) -> list:
    """Treina e avalia uma vez por partição. Devolve as métricas de cada uma."""
    resultados = []
    for d in range(len(dobras)):
        teste_idx = dobras[d]
        treino_idx = [i for j, dob in enumerate(dobras) if j != d for i in dob]
        if not teste_idx or not treino_idx:
            continue
        tr_X = [X[i] for i in treino_idx]
        te_X = [X[i] for i in teste_idx]
        tr_y = [y[i] for i in treino_idx]
        te_y = [y[i] for i in teste_idx]
        if len(set(tr_y)) < 2:
            continue
        tr_X, te_X = padronizar(tr_X, te_X)
        modelo.fit(tr_X, tr_y)
        pred = list(modelo.predict(te_X))
        prob = None
        if hasattr(modelo, "predict_proba"):
            prob = [p[1] for p in modelo.predict_proba(te_X)]
        resultados.append(metricas(te_y, pred, prob))
    return resultados


def importancia_por_permutacao(modelo, X, y, nomes, semente=42, repeticoes=10):
    """
    Embaralha uma coluna por vez e mede quanto a acurácia cai. Funciona para
    qualquer modelo, inclusive os que não expõem coeficientes, e mede o que
    interessa: o quanto a previsão DEPENDE daquela variável.
    """
    rng = random.Random(semente)
    n = len(X)
    corte = int(n * 0.7)
    ordem = list(range(n))
    rng.shuffle(ordem)
    tr, te = ordem[:corte], ordem[corte:]

    tr_X, te_X = padronizar([X[i] for i in tr], [X[i] for i in te])
    tr_y = [y[i] for i in tr]
    te_y = [y[i] for i in te]
    modelo.fit(tr_X, tr_y)
    base = metricas(te_y, list(modelo.predict(te_X)))["acuracia"]

    quedas = {}
    for j, nome in enumerate(nomes):
        perdas = []
        for _ in range(repeticoes):
            embaralhado = [linha[:] for linha in te_X]
            coluna = [linha[j] for linha in embaralhado]
            rng.shuffle(coluna)
            for i, v in enumerate(coluna):
                embaralhado[i][j] = v
            acc = metricas(te_y, list(modelo.predict(embaralhado)))["acuracia"]
            perdas.append(base - acc)
        quedas[nome] = sum(perdas) / len(perdas)
    return base, quedas


# ---------------------------------------------------------------- relatório

def linha_de_base(y) -> float:
    c = Counter(y)
    return max(c.values()) / len(y)


def tabela_distribuicao(titulo, por_modelo, chave="acuracia"):
    print(f"\n  {titulo}")
    print("  " + "-" * 76)
    print(f"  {'modelo':<22}{'média':>8}{'desvio':>8}{'mín':>8}"
          f"{'25%':>8}{'mediana':>9}{'75%':>8}{'máx':>8}")
    print("  " + "-" * 76)
    ordenado = sorted(por_modelo.items(),
                      key=lambda kv: -resumo_distribuicao([m[chave] for m in kv[1]])["media"])
    for nome, folds in ordenado:
        r = resumo_distribuicao([m[chave] for m in folds])
        print(f"  {nome:<22}{r['media']:>8.4f}{r['desvio']:>8.4f}{r['min']:>8.4f}"
              f"{r['q25']:>8.4f}{r['mediana']:>9.4f}{r['q75']:>8.4f}{r['max']:>8.4f}")
    return ordenado


def executar(rows, incluir_vazamento, repeticoes, k, semente, rotulo):
    X, y, grupos, nomes = construir_features(rows, incluir_vazamento)
    base = linha_de_base(y)

    print("\n" + "=" * 78)
    print(rotulo)
    print("=" * 78)
    print(f"  observações {len(y)}   variáveis {len(nomes)}   "
          f"falhas {sum(y)} ({100*sum(y)/len(y):.1f}%)")
    print(f"  LINHA DE BASE (sempre a classe majoritária): {100*base:.1f}% de acurácia")
    print(f"  Qualquer modelo abaixo disso não aprendeu nada.")

    modelos = construir_modelos(semente)
    resultado = {}

    for esquema, construtor in (
        ("ESTRATIFICADA — condição já medida",
         lambda s: particoes_estratificadas(y, k, s)),
        ("AGRUPADA POR CÉLULA — condição inédita",
         lambda s: particoes_agrupadas(grupos, k, s)),
    ):
        por_modelo = defaultdict(list)
        for nome, modelo in modelos.items():
            for rep in range(repeticoes):
                dobras = construtor(semente + rep)
                por_modelo[nome].extend(avaliar(modelo, X, y, dobras))
        print(f"\n\n  >>> VALIDAÇÃO CRUZADA {esquema}")
        print(f"      {k} partições x {repeticoes} repetições = "
              f"{k*repeticoes} ajustes por modelo")
        ordenado = tabela_distribuicao("Acurácia", por_modelo)

        print("\n  Demais métricas (média entre as partições)")
        print("  " + "-" * 62)
        print(f"  {'modelo':<22}{'precisão':>10}{'revocação':>11}{'F1':>9}{'AUC':>9}")
        print("  " + "-" * 62)
        for nome, folds in ordenado:
            r = {c: resumo_distribuicao([m[c] for m in folds if c in m])["media"]
                 for c in ("precisao", "revocacao", "f1", "auc")}
            print(f"  {nome:<22}{r['precisao']:>10.4f}{r['revocacao']:>11.4f}"
                  f"{r['f1']:>9.4f}{r['auc']:>9.4f}")
        resultado[esquema] = dict(por_modelo)

    return X, y, nomes, base, resultado, modelos


def main():
    p = argparse.ArgumentParser(description="Modelo de previsão de falha")
    p.add_argument("--csv", default=ENTRADA)
    p.add_argument("--repeticoes", type=int, default=10)
    p.add_argument("--particoes", type=int, default=10)
    p.add_argument("--semente", type=int, default=42)
    p.add_argument("--ingenuo", action="store_true",
                   help="roda também o modelo com vazamento, para contraste")
    p.add_argument("--autoteste", action="store_true")
    args = p.parse_args()

    if args.autoteste:
        autoteste()
        return

    rows = carregar(args.csv)
    print(f"{len(rows)} execuções válidas carregadas de {args.csv}")
    print("(as 6 execuções que contornaram a interface ficam fora, "
          "pela mesma razão que ficam fora da análise inferencial)")

    X, y, nomes, base, resultado, modelos = executar(
        rows, False, args.repeticoes, args.particoes, args.semente,
        "MODELO HONESTO — só o que se conhece antes de executar")

    # ---- importância de variáveis, no melhor modelo da validação agrupada
    chave = [k for k in resultado if k.startswith("AGRUPADA")][0]
    melhor = max(resultado[chave].items(),
                 key=lambda kv: resumo_distribuicao(
                     [m["acuracia"] for m in kv[1]])["media"])[0]
    print("\n\n" + "=" * 78)
    print(f"IMPORTÂNCIA DAS VARIÁVEIS — {melhor}")
    print("=" * 78)
    print("  Queda de acurácia ao embaralhar cada variável, mantendo as demais.")
    print("  Responde: quanto a previsão DEPENDE de cada fator?\n")
    acc, quedas = importancia_por_permutacao(modelos[melhor], X, y, nomes, args.semente)
    print(f"  acurácia sem embaralhar nada: {acc:.4f}\n")
    print(f"  {'variável':<32}{'queda':>10}")
    print("  " + "-" * 42)
    for nome, q in sorted(quedas.items(), key=lambda kv: -kv[1]):
        barra = "#" * int(max(q, 0) * 100)
        print(f"  {nome:<32}{q:>10.4f}  {barra}")

    if args.ingenuo:
        print("\n\n")
        executar(rows, True, args.repeticoes, args.particoes, args.semente,
                 "MODELO INGÊNUO — inclui colunas contaminadas pelo desfecho")
        print("\n  LEITURA: a acurácia sobe, e o ganho é ilusório. n_steps,")
        print("  duration_s e as colunas de degradação só existem DEPOIS da")
        print("  execução, e carregam o resultado. Um modelo que as usa não")
        print("  prevê a falha — ele a observa. Está aqui para registrar a")
        print("  diferença, não para ser usado.")

    os.makedirs(SAIDA, exist_ok=True)
    destino = os.path.join(SAIDA, "modelo_falha.csv")
    with open(destino, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["esquema", "modelo", "metrica", "media", "desvio",
                    "min", "q25", "mediana", "q75", "max"])
        for esquema, por_modelo in resultado.items():
            for nome, folds in por_modelo.items():
                for met in ("acuracia", "precisao", "revocacao", "f1", "auc"):
                    r = resumo_distribuicao([m[met] for m in folds if met in m])
                    w.writerow([esquema, nome, met] +
                               [f"{r[c]:.6f}" for c in ("media", "desvio", "min",
                                                        "q25", "mediana", "q75", "max")])
    print(f"\n\nResultados gravados em: {destino}")

    figura(resultado, base)


def figura(resultado, base):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("(matplotlib não instalado — figura pulada; pip install matplotlib)")
        return

    # A ORDEM DOS MODELOS É DEFINIDA UMA VEZ SÓ, e vale para os dois painéis.
    #
    # Os painéis compartilham o eixo Y (sharey=True). Ordenar cada um pela sua
    # própria acurácia colocaria rótulos diferentes no mesmo eixo, e o segundo
    # sobrescreveria o primeiro — um dos painéis ficaria com os nomes trocados
    # em relação às caixas, sem erro nenhum aparecendo. Além disso, ordem única
    # é o que permite ler na horizontal e comparar os dois esquemas.
    esquemas = list(resultado.items())
    referencia = esquemas[0][1]
    nomes = sorted(referencia, key=lambda n: resumo_distribuicao(
        [m["acuracia"] for m in referencia[n]])["media"])

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2), sharey=True)
    for ax, (esquema, por_modelo) in zip(axes, esquemas):
        dados = [[m["acuracia"] for m in por_modelo[n]] for n in nomes]
        # A API do boxplot mudou duas vezes: "labels" virou "tick_labels" na
        # 3.9 e "vert" virou "orientation" na 3.10. Definir os rótulos por
        # fora e testar a assinatura evita depender da versão instalada.
        try:
            ax.boxplot(dados, orientation="horizontal", widths=0.6)
        except TypeError:
            ax.boxplot(dados, vert=False, widths=0.6)
        ax.set_yticks(range(1, len(nomes) + 1))
        ax.set_yticklabels(nomes)
        ax.axvline(base, color="crimson", linestyle="--", linewidth=1.4,
                   label=f"linha de base ({100*base:.1f}%)")
        ax.set_title(esquema.split("—")[0].strip(), fontsize=11)
        ax.set_xlabel("acurácia")
        ax.grid(axis="x", alpha=0.3)
        # "lower left" é a única região livre: a linha de base fica à esquerda
        # de todas as caixas, então há espaço vazio ali. Em "lower right" a
        # legenda cobria a caixa do último modelo.
        ax.legend(loc="lower left", fontsize=9)
    fig.suptitle("Desempenho dos classificadores por esquema de validação cruzada")
    fig.tight_layout()
    os.makedirs(SAIDA, exist_ok=True)
    destino = os.path.join(SAIDA, "fig3_classificadores.png")
    fig.savefig(destino, dpi=150)
    print(f"Figura gravada em: {destino}")


# ---------------------------------------------------------------- autoteste

def autoteste():
    """Confere as funções implementadas à mão contra valores derivados na mão."""
    ok = True

    def checa(nome, obtido, esperado, tol=1e-9):
        nonlocal ok
        bom = abs(obtido - esperado) < tol
        ok &= bom
        print(f"  [{'OK ' if bom else 'ERRO'}] {nome:<52} "
              f"obtido {obtido:.6f}  esperado {esperado:.6f}")

    print("AUTOTESTE — funções implementadas à mão\n")

    # Matriz de confusão e métricas derivadas, contadas manualmente.
    y = [1, 1, 1, 1, 0, 0, 0, 0, 0, 0]
    pred = [1, 1, 1, 0, 1, 0, 0, 0, 0, 0]
    # vp=3, fn=1, fp=1, vn=5
    vn, fp, fn, vp = matriz_confusao(y, pred)
    checa("matriz de confusão — vp", vp, 3)
    checa("matriz de confusão — fn", fn, 1)
    checa("matriz de confusão — fp", fp, 1)
    checa("matriz de confusão — vn", vn, 5)
    m = metricas(y, pred)
    checa("acurácia = (3+5)/10", m["acuracia"], 0.8)
    checa("precisão = 3/(3+1)", m["precisao"], 0.75)
    checa("revocação = 3/(3+1)", m["revocacao"], 0.75)
    checa("F1 = 2*0,75*0,75/(0,75+0,75)", m["f1"], 0.75)

    # AUC: separação perfeita e caso com empates, ambos conferíveis à mão.
    checa("AUC separação perfeita", auc_roc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]), 1.0)
    checa("AUC separação invertida", auc_roc([1, 1, 0, 0], [0.1, 0.2, 0.8, 0.9]), 0.0)
    checa("AUC tudo empatado", auc_roc([0, 1, 0, 1], [0.5, 0.5, 0.5, 0.5]), 0.5)
    # 1 pos (0.9) e 2 neg (0.1 e 0.9): vence um, empata outro -> (1+0,5)/2
    checa("AUC com um empate", auc_roc([1, 0, 0], [0.9, 0.1, 0.9]), 0.75)

    # Quartis de uma sequência conhecida.
    r = resumo_distribuicao([1, 2, 3, 4, 5])
    checa("média de 1..5", r["media"], 3.0)
    checa("mediana de 1..5", r["mediana"], 3.0)
    checa("q25 de 1..5", r["q25"], 2.0)
    checa("máximo de 1..5", r["max"], 5.0)
    checa("desvio amostral de 1..5", r["desvio"], math.sqrt(2.5))

    # Partições estratificadas: preservam a proporção e cobrem tudo uma vez.
    y2 = [1] * 30 + [0] * 70
    dobras = particoes_estratificadas(y2, 5, 1)
    checa("estratificada — cobre todas as linhas",
          len({i for d in dobras for i in d}), 100)
    checa("estratificada — sem repetição",
          sum(len(d) for d in dobras), 100)
    props = [sum(y2[i] for i in d) / len(d) for d in dobras]
    checa("estratificada — proporção preservada em toda partição",
          max(abs(p - 0.30) for p in props), 0.0, tol=0.02)

    # Partições agrupadas: nenhuma célula em duas partições.
    grupos = [f"c{i//8}" for i in range(80)]
    dobras = particoes_agrupadas(grupos, 4, 1)
    vazou = False
    for d in dobras:
        gd = {grupos[i] for i in d}
        for outra in dobras:
            if outra is d:
                continue
            if gd & {grupos[i] for i in outra}:
                vazou = True
    checa("agrupada — nenhuma célula em duas partições", 0.0 if not vazou else 1.0, 0.0)
    checa("agrupada — cobre todas as linhas",
          len({i for d in dobras for i in d}), 80)

    # Padronização estimada só no treino.
    tr = [[0.0], [10.0]]
    te = [[5.0]]
    tr_p, te_p = padronizar(tr, te)
    checa("padronização — treino vira média 0",
          (tr_p[0][0] + tr_p[1][0]) / 2, 0.0)
    checa("padronização — teste usa parâmetros do treino", te_p[0][0], 0.0)

    print("\n" + ("TODOS OS TESTES PASSARAM." if ok else "HÁ FALHAS ACIMA."))


if __name__ == "__main__":
    main()
