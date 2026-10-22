"""
Análise dos resultados do experimento.

Lê data/results.csv e produz as tabelas e testes que vão para o capítulo de
Resultados, além das figuras. Roda com UM comando e não depende de nada além do
que já está instalado (matplotlib é opcional, só para os gráficos):

    python analise.py
    python analise.py --sem-figuras

POR QUE OS TESTES ESTÃO IMPLEMENTADOS AQUI, E NÃO IMPORTADOS DO SCIPY
---------------------------------------------------------------------
São testes curtos e a implementação explícita deixa claro na banca exatamente o
que foi calculado. Cada função abaixo tem a fórmula comentada. Se preferir
conferir contra o scipy, os valores batem — foram verificados nos casos-teste no
fim do arquivo (`python analise.py --autoteste`).

O QUE É TESTADO (plano registrado em docs/plano_analise_coleta2.md)
-------------------------------------------------------------------
1. Taxa de sucesso por braço x tipo de degradação x nível, com intervalo de
   confiança de Wilson (e não o intervalo normal: com proporções em 0% ou 100%,
   o intervalo normal colapsa para largura zero e afirmaria certeza que os dados
   não sustentam).
2. Hipótese primária por McNemar exato. O desenho é PAREADO: a mesma semente
   produz a mesma página degradada para os cinco braços, então cada execução
   agêntica tem uma execução self-healing gêmea. Na coleta 1 a análise usava
   Fisher, que trata as amostras como independentes e desperdiça o pareamento.
3. Comparações exploratórias entre braços, também pareadas, com Holm.
4. Tendência ao longo da intensidade, por Cochran-Armitage, com Holm sobre os
   testes calculáveis.
5. Tempo por Mann-Whitney SÓ entre execuções bem-sucedidas: a mediana de um
   braço que falha na maioria das vezes é o tempo limite do localizador, e
   comparar isso com o tempo do agente mede a configuração, não a abordagem.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
from collections import defaultdict

RAIZ = os.path.dirname(os.path.abspath(__file__))
CSV_ENTRADA = os.path.join(RAIZ, "data", "results.csv")
DIR_SAIDA = os.path.join(RAIZ, "data", "analise")

# Nomes usados no artigo. Na coleta 1 o braço por id e classe se chamava
# "frágil", o que antecipava o resultado, e o braço por papel e rótulo se chamava
# "semântico", o mesmo nome de um tipo de degradação.
NOME_BRACO = {
    "deterministic": "ID/CSS",
    "xpath": "XPath",
    "semantic": "papel e rótulo",
    "healing": "self-healing",
    "agentic": "agêntica",
}

# Comparador da hipótese primária, fixado ANTES da coleta 2 (ver o plano de
# análise). Na coleta 1 o comparador era "a melhor determinística de cada
# condição", escolhida depois de ver os dados.
COMPARADOR_PRIMARIO = "healing"

# Teto de custo por execução agêntica (common/agent.py), usado para ler o custo
# das execuções interrompidas. Atenção: o teto é aplicado pelo SDK sobre a
# estimativa de custo DELE, que não usa necessariamente o preço público.
TETO_USD = 0.60

# Preço público do claude-sonnet-5 em US$ por milhão de tokens (página de preços
# da Anthropic, consultada em 16/09/2026): US$ 2 de entrada e US$ 10 de saída;
# escrita de cache a 1,25x (5 minutos) ou 2x (1 hora); leitura de cache a 0,1x.
PRECO_ENTRADA = 2.0
PRECO_SAIDA = 10.0
DIR_TRANSCRICOES = os.path.join(RAIZ, "data", "transcripts")
ORDEM_BRACOS = ["deterministic", "xpath", "semantic", "healing", "agentic"]
ORDEM_TIPOS = ["cosmetico", "estrutural", "semantico"]


# ---------------------------------------------------------------- estatística

def wilson(sucessos: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """
    Intervalo de confiança de Wilson para uma proporção.

    Preferido ao intervalo normal (p ± z·sqrt(p(1-p)/n)) porque continua válido
    quando p é 0 ou 1 — situação frequente aqui, já que vários braços falham ou
    acertam todas as execuções de uma condição. O intervalo normal daria
    [0, 0] ou [1, 1], sugerindo certeza absoluta a partir de 8 observações.
    """
    if n == 0:
        return (0.0, 0.0)
    p = sucessos / n
    d = 1 + z * z / n
    centro = (p + z * z / (2 * n)) / d
    meio = (z / d) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, centro - meio), min(1.0, centro + meio))


def fisher_bicaudal(a: int, b: int, c: int, d: int) -> float:
    """
    Teste exato de Fisher para a tabela 2x2 [[a, b], [c, d]].

    Soma a probabilidade hipergeométrica de todas as tabelas com as mesmas
    margens cuja probabilidade seja <= à da tabela observada. É exato, então não
    depende de frequências esperadas grandes — ao contrário do qui-quadrado, que
    seria inválido com os n desta análise (8 por célula).
    """
    linha1, linha2 = a + b, c + d
    col1, total = a + c, a + b + c + d
    if total == 0:
        return 1.0

    def prob(x: int) -> float:
        return (math.comb(linha1, x) * math.comb(linha2, col1 - x)) / math.comb(total, col1)

    p_obs = prob(a)
    inicio = max(0, col1 - linha2)
    fim = min(linha1, col1)
    # a tolerância evita descartar tabelas igualmente prováveis por erro de
    # ponto flutuante, o que subestimaria o p-valor
    return min(1.0, sum(prob(x) for x in range(inicio, fim + 1)
                        if prob(x) <= p_obs * (1 + 1e-9)))


def cochran_armitage(niveis: list[float], sucessos: list[int],
                     totais: list[int]) -> tuple[float, float]:
    """
    Teste de tendência de Cochran-Armitage: a proporção de sucesso muda de forma
    monótona conforme o drift aumenta?

    Usa o VALOR numérico do nível como peso, aproveitando que 0 < 0,25 < 0,5 é
    uma ordem real. Um qui-quadrado comum trataria os níveis como categorias sem
    ordem e desperdiçaria essa informação, perdendo poder.

    Devolve (estatística z, p-valor bicaudal).
    """
    n = sum(totais)
    s = sum(sucessos)
    if n == 0 or s == 0 or s == n:
        return (0.0, 1.0)
    p = s / n
    t_barra = sum(t * x for t, x in zip(totais, niveis)) / n
    numerador = sum(x_i * (s_i - t_i * p) for x_i, s_i, t_i in zip(niveis, sucessos, totais))
    variancia = p * (1 - p) * sum(t_i * (x_i - t_barra) ** 2 for t_i, x_i in zip(totais, niveis))
    if variancia <= 0:
        return (0.0, 1.0)
    z = numerador / math.sqrt(variancia)
    return (z, 2 * (1 - _phi(abs(z))))


def _phi(x: float) -> float:
    """Função de distribuição acumulada da normal padrão."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def mann_whitney(a: list[float], b: list[float]) -> tuple[float, float]:
    """
    Mann-Whitney U com aproximação normal e correção para empates.

    Usado no lugar do teste t porque as durações são fortemente assimétricas: uma
    falha por timeout produz valores muito acima da mediana, e a média deixa de
    representar o comportamento típico.

    Devolve (U, p-valor bicaudal).
    """
    if not a or not b:
        return (0.0, 1.0)
    todos = sorted([(v, 0) for v in a] + [(v, 1) for v in b])
    postos = [0.0] * len(todos)
    i = 0
    empates = 0
    while i < len(todos):
        j = i
        while j + 1 < len(todos) and todos[j + 1][0] == todos[i][0]:
            j += 1
        posto_medio = (i + j) / 2 + 1
        for k in range(i, j + 1):
            postos[k] = posto_medio
        t = j - i + 1
        if t > 1:
            empates += t ** 3 - t
        i = j + 1

    n1, n2 = len(a), len(b)
    r1 = sum(p for p, (_, g) in zip(postos, todos) if g == 0)
    u1 = r1 - n1 * (n1 + 1) / 2
    mu = n1 * n2 / 2
    n = n1 + n2
    var = n1 * n2 / 12 * ((n + 1) - empates / (n * (n - 1))) if n > 1 else 0
    if var <= 0:
        return (u1, 1.0)
    z = (u1 - mu) / math.sqrt(var)
    return (u1, 2 * (1 - _phi(abs(z))))


def mediana(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def tamanho_de_efeito(sucessos_a: int, n_a: int,
                      sucessos_b: int, n_b: int) -> dict:
    """
    Tamanho de efeito para a comparação de duas proporções.

    POR QUE ISTO EXISTE
    -------------------
    O valor de p responde "esta diferença pode ser acaso?". Não responde
    "a diferença é grande?". Com amostra suficiente, uma diferença irrelevante
    atinge significância; com amostra pequena, uma diferença enorme não atinge.
    Reportar só p é a crítica mais comum a trabalhos quantitativos, e ela é justa.

    São devolvidas três medidas complementares:

      diferença de risco  Em pontos percentuais. É a mais direta de interpretar
                          e a que interessa a quem vai decidir.
      razão de chances    Quantas vezes a chance de sucesso é maior. O intervalo
                          é calculado na escala logarítmica, porque a razão de
                          chances é assimétrica: 0,5 e 2,0 são o mesmo efeito em
                          direções opostas, e só o logaritmo torna isso simétrico.
      NNT                 Quantas execuções a mais são necessárias, em média,
                          para obter um sucesso adicional. Vem da epidemiologia
                          e traduz o efeito em unidade prática.

    A correção de 0,5 na razão de chances (correção de Haldane-Anscombe) evita
    divisão por zero quando alguma casela é vazia — situação frequente aqui,
    porque vários braços zeram sob degradação alta.
    """
    if n_a == 0 or n_b == 0:
        return {}
    p_a, p_b = sucessos_a / n_a, sucessos_b / n_b
    dif = p_b - p_a
    erro = math.sqrt(p_a * (1 - p_a) / n_a + p_b * (1 - p_b) / n_b)

    a, b = sucessos_b, n_b - sucessos_b        # grupo B: sucesso, falha
    c, d = sucessos_a, n_a - sucessos_a        # grupo A: sucesso, falha
    if 0 in (a, b, c, d):
        a, b, c, d = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    razao = (a * d) / (b * c)
    erro_log = math.sqrt(1 / a + 1 / b + 1 / c + 1 / d)

    return {
        "dif": dif,
        "dif_ic": (dif - 1.96 * erro, dif + 1.96 * erro),
        "razao": razao,
        "razao_ic": (math.exp(math.log(razao) - 1.96 * erro_log),
                     math.exp(math.log(razao) + 1.96 * erro_log)),
        "nnt": (1 / dif) if dif else float("inf"),
    }


def kruskal_wallis(grupos: list[list[float]]) -> tuple[float, int, float]:
    """
    Kruskal-Wallis: extensão do Mann-Whitney para mais de dois grupos.

    Usado aqui para testar se o CUSTO do braço agêntico varia entre os cinco
    níveis de degradação. É o teste que sustenta — ou derruba — a afirmação de
    que o custo não cresce conforme a página piora.

    Cuidado de redação: não rejeitar a hipótese nula NÃO prova que os grupos são
    iguais. Prova que estes dados não os distinguem. A formulação correta no
    texto é "não se detectou variação", e nunca "o custo é constante".

    Devolve (H, graus de liberdade, p-valor).
    """
    grupos = [g for g in grupos if g]
    if len(grupos) < 2:
        return (0.0, 0, 1.0)

    todos = sorted((v, i) for i, g in enumerate(grupos) for v in g)
    postos = [0.0] * len(todos)
    i = 0
    empates = 0
    while i < len(todos):
        j = i
        while j + 1 < len(todos) and todos[j + 1][0] == todos[i][0]:
            j += 1
        posto_medio = (i + j) / 2 + 1
        for k in range(i, j + 1):
            postos[k] = posto_medio
        t = j - i + 1
        if t > 1:
            empates += t ** 3 - t
        i = j + 1

    n = len(todos)
    somas = [0.0] * len(grupos)
    tamanhos = [0] * len(grupos)
    for k, (_, g) in enumerate(todos):
        somas[g] += postos[k]
        tamanhos[g] += 1

    h = 12 / (n * (n + 1)) * sum(s * s / t for s, t in zip(somas, tamanhos)) - 3 * (n + 1)
    if empates and n > 1:
        h /= 1 - empates / (n ** 3 - n)   # correção para empates
    gl = len(grupos) - 1
    return (h, gl, _qui2_cauda(h, gl))


def _qui2_cauda(x: float, k: int) -> float:
    """
    P(X > x) para qui-quadrado com k graus de liberdade, por série fechada.

    Evita a dependência de scipy, na mesma linha das demais funções deste
    arquivo. Conferido em --autoteste contra valores tabelados.
    """
    if x <= 0 or k <= 0:
        return 1.0
    if k % 2 == 0:
        termo = math.exp(-x / 2)
        soma = termo
        for i in range(1, k // 2):
            termo *= x / (2 * i)
            soma += termo
        return min(1.0, soma)
    soma = math.erfc(math.sqrt(x / 2))
    termo = math.sqrt(2 * x / math.pi) * math.exp(-x / 2)
    for i in range(1, (k - 1) // 2 + 1):
        soma += termo
        termo *= x / (2 * i + 1)
    return min(1.0, soma)


def holm(pvalores: list[float]) -> list[float]:
    """
    Correção de Holm-Bonferroni para múltiplas comparações.

    São feitas dezenas de comparações entre braços; sem correção, alguns
    resultados apareceriam como significativos apenas por acaso. Holm é
    uniformemente mais poderoso que Bonferroni e não exige independência.
    """
    indexados = sorted(enumerate(pvalores), key=lambda t: t[1])
    m = len(pvalores)
    ajustados = [0.0] * m
    anterior = 0.0
    for posicao, (indice_original, p) in enumerate(indexados):
        valor = min(1.0, (m - posicao) * p)
        anterior = max(anterior, valor)
        ajustados[indice_original] = anterior
    return ajustados


# ------------------------------------------------------- estatística pareada

def _binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) para X ~ Binomial(n, p), por soma exata."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def mcnemar_exato(b: int, c: int) -> float:
    """
    Teste de McNemar exato (bicaudal) para dados pareados.

    Numa tabela pareada, só os pares DISCORDANTES informam: b é o número de
    pares em que o braço A acertou e o B errou; c, o contrário. Sob H0, cada par
    discordante tem a mesma chance de cair para um lado ou para o outro, então
    min(b, c) segue uma binomial(b + c; 0,5). O p-valor bicaudal é o dobro da
    cauda, limitado a 1.

    Por que não o Fisher usado na coleta 1: o Fisher compara duas amostras
    independentes. Aqui a mesma semente produz a mesma página para os dois
    braços, e as execuções vêm em pares. Tratar como independente ignora essa
    informação.
    """
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * _binom_cdf(min(b, c), n, 0.5))


def clopper_pearson(x: int, n: int, alfa: float = 0.05) -> tuple[float, float]:
    """
    Intervalo exato de Clopper-Pearson para uma proporção, por bisseção.

    Limite inferior: o p em que P(X >= x) = alfa/2. Superior: o p em que
    P(X <= x) = alfa/2. Sem scipy, na mesma linha das demais funções.
    """
    if n == 0:
        return (0.0, 1.0)

    def bissecao(funcao, alvo):
        lo, hi = 0.0, 1.0
        for _ in range(200):
            meio = (lo + hi) / 2
            if funcao(meio) > alvo:
                hi = meio
            else:
                lo = meio
        return (lo + hi) / 2

    inferior = 0.0 if x == 0 else bissecao(
        lambda p: 1 - _binom_cdf(x - 1, n, p), alfa / 2)
    # P(X <= x) DECRESCE com p; inverte-se o sinal para usar a mesma bisseção
    superior = 1.0 if x == n else bissecao(
        lambda p: -_binom_cdf(x, n, p), -alfa / 2)
    return (inferior, superior)


def razao_chances_condicional(b: int, c: int) -> dict:
    """
    Razão de chances condicional b/c, com intervalo exato.

    É a medida de efeito que corresponde ao McNemar: entre os pares em que os
    braços discordam, quantas vezes o A vence o B. O intervalo sai do intervalo
    exato de b/(b + c), transformado por p/(1 - p).
    """
    if b + c == 0:
        return {"razao": float("nan"), "ic": (float("nan"), float("nan"))}
    lo, hi = clopper_pearson(b, b + c)
    razao = b / c if c else float("inf")
    return {"razao": razao,
            "ic": (lo / (1 - lo) if lo < 1 else float("inf"),
                   hi / (1 - hi) if hi < 1 else float("inf"))}


def diferenca_pareada(a: int, b: int, c: int, d: int, z: float = 1.96) -> dict:
    """
    Diferença de proporções pareadas, com intervalo de Agresti e Min (2005).

    a = os dois acertam; b = só A acerta; c = só B acerta; d = os dois erram.
    A diferença é (b - c)/n. O intervalo de Wald pareado se comporta mal com
    poucos pares discordantes; Agresti e Min somam 0,5 a cada casela antes de
    calcular o erro padrão, o que corrige a cobertura sem complicar a fórmula.
    """
    n = a + b + c + d
    if n == 0:
        return {}
    b2, c2, n2 = b + 0.5, c + 0.5, n + 2
    centro = (b2 - c2) / n2
    erro = math.sqrt((b2 + c2) - (b2 - c2) ** 2 / n2) / n2
    return {"dif": (b - c) / n, "ic": (centro - z * erro, centro + z * erro)}


def newcombe_independente(s1: int, n1: int, s2: int, n2: int) -> tuple[float, float]:
    """
    Intervalo de Newcombe (método 10, híbrido de Wilson) para p2 - p1 com
    amostras independentes. Coerente com o Wilson usado nas taxas; a coleta 1
    usava Wald para a diferença e Wilson para as taxas.
    """
    p1, p2 = s1 / n1, s2 / n2
    l1, u1 = wilson(s1, n1)
    l2, u2 = wilson(s2, n2)
    dif = p2 - p1
    return (dif - math.sqrt((p2 - l2) ** 2 + (u1 - p1) ** 2),
            dif + math.sqrt((u2 - p2) ** 2 + (p1 - l1) ** 2))


def tabela_pareada(linhas, braco_a: str, braco_b: str, contorno_como_falha=False,
                   **criterios) -> dict:
    """
    Monta os pares (site, tipo, nível, semente) entre dois braços.

    Por padrão, um par em que qualquer das execuções contornou a interface é
    descartado (análise principal). Com contorno_como_falha=True, o contorno
    conta como falha (análise de sensibilidade prevista no plano).
    """
    def chave(r):
        return (r["site"], r["drift_type"], r["nivel"], r["rep"])

    def resultado(r):
        if r.get("error_type") == "contornou":
            return None if not contorno_como_falha else False
        return r["ok"]

    def indice(braco):
        saida = {}
        for r in linhas:
            if r["approach"] != braco:
                continue
            if all(r.get(k) == v for k, v in criterios.items()):
                saida[chave(r)] = resultado(r)
        return saida

    ia, ib = indice(braco_a), indice(braco_b)
    a = b = c = d = descartados = 0
    for k in sorted(set(ia) & set(ib)):
        ra, rb = ia[k], ib[k]
        if ra is None or rb is None:
            descartados += 1
            continue
        if ra and rb:
            a += 1
        elif ra and not rb:
            b += 1
        elif rb and not ra:
            c += 1
        else:
            d += 1
    sem_par = len(set(ia) ^ set(ib))
    return {"a": a, "b": b, "c": c, "d": d, "n": a + b + c + d,
            "descartados": descartados, "sem_par": sem_par}


# ---------------------------------------------------------------------- dados

def carregar(caminho: str) -> list[dict]:
    with open(caminho, encoding="utf-8") as f:
        linhas = list(csv.DictReader(f))
    for r in linhas:
        r["ok"] = r["success"].strip().lower() == "true"
        r["nivel"] = float(r["drift_level"])
        r["duracao"] = float(r["duration_s"])
        r["custo"] = float(r["cost_usd"] or 0)
        r["passos"] = int(r["n_steps"] or 0)
        r["rep"] = int(r["repetition"])
    return linhas


def filtrar(linhas, **criterios):
    """
    Filtra execuções, EXCLUINDO por padrão as que contornaram a interface.

    Uma execução em que o agente mirou atributos imunes ao drift não exercita a
    localização na interface degradada: não é sucesso nem falha, e incluí-la em
    qualquer um dos lados distorce a taxa. Elas são analisadas à parte, pela
    taxa de contorno.
    """
    incluir_contorno = criterios.pop("incluir_contorno", False)
    saida = [r for r in linhas
             if all(r.get(k) == v for k, v in criterios.items())]
    if not incluir_contorno:
        saida = [r for r in saida if r.get("error_type") != "contornou"]
    return saida


def contornos(linhas) -> list:
    return [r for r in linhas if r.get("error_type") == "contornou"]


# ------------------------------------------------------------------ relatório

def secao(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def tabela_sucesso(linhas, saida: list) -> None:
    secao("2. TAXA DE SUCESSO POR BRAÇO, TIPO E NÍVEL DE DEGRADAÇÃO")
    print("(entre colchetes: intervalo de confiança de Wilson a 95%)\n")

    niveis = sorted({r["nivel"] for r in linhas})
    for tipo in ORDEM_TIPOS:
        print(f"\ndrift {tipo}")
        print(f"  {'braço':<14}" + "".join(f"{n:>18}" for n in niveis))
        for braco in ORDEM_BRACOS:
            celulas = []
            for n in niveis:
                sub = filtrar(linhas, approach=braco, drift_type=tipo, nivel=n)
                if not sub:
                    celulas.append(f"{'—':>18}")
                    continue
                k = sum(r["ok"] for r in sub)
                lo, hi = wilson(k, len(sub))
                celulas.append(f"{100*k/len(sub):>6.0f}% [{100*lo:>3.0f},{100*hi:>3.0f}]")
                saida.append({
                    "braco": NOME_BRACO[braco], "drift_type": tipo, "drift_level": n,
                    "n": len(sub), "sucessos": k, "taxa": round(k / len(sub), 4),
                    "ic95_inf": round(lo, 4), "ic95_sup": round(hi, 4),
                })
            print(f"  {NOME_BRACO[braco]:<14}" + "".join(celulas))


def tendencia(linhas, saida: list) -> None:
    secao("3. TENDÊNCIA AO LONGO DA INTENSIDADE (Cochran-Armitage, Holm)")
    print("H0: a taxa de sucesso não muda conforme a intensidade aumenta.\n"
          "Holm corrige os testes calculáveis. Quando o braço acerta (ou erra)\n"
          "tudo em todos os níveis, a variância é zero e o teste NÃO é\n"
          "calculável; na coleta 1 esses casos foram chamados de 'nulos'.\n")
    testes = []
    for braco in ORDEM_BRACOS:
        for tipo in ORDEM_TIPOS:
            sub = filtrar(linhas, approach=braco, drift_type=tipo)
            if not sub:
                continue
            niveis = sorted({r["nivel"] for r in sub})
            suc = [sum(r["ok"] for r in sub if r["nivel"] == n) for n in niveis]
            tot = [len([r for r in sub if r["nivel"] == n]) for n in niveis]
            calculavel = 0 < sum(suc) < sum(tot)
            z, p = cochran_armitage(niveis, suc, tot) if calculavel else (None, None)
            testes.append({"braco": braco, "tipo": tipo, "z": z, "p": p,
                           "taxas": [round(100 * s / t) for s, t in zip(suc, tot)]})

    calculaveis = [t for t in testes if t["p"] is not None]
    for t, pa in zip(calculaveis, holm([t["p"] for t in calculaveis])):
        t["p_holm"] = pa

    print(f"  {'braço':<16}{'tipo':<12}{'taxas por nível (%)':<26}{'z':>7}{'p':>10}{'p Holm':>10}")
    print("  " + "-" * 81)
    for t in testes:
        taxas = "/".join(str(x) for x in t["taxas"])
        if t["p"] is None:
            print(f"  {NOME_BRACO[t['braco']]:<16}{t['tipo']:<12}{taxas:<26}"
                  f"{'não calculável':>27}")
        else:
            print(f"  {NOME_BRACO[t['braco']]:<16}{t['tipo']:<12}{taxas:<26}"
                  f"{t['z']:>7.2f}{t['p']:>10.4f}{t['p_holm']:>10.4f}")
        saida.append({"braco": NOME_BRACO[t["braco"]], "tipo": t["tipo"],
                      "taxas_por_nivel": taxas,
                      "z": "" if t["z"] is None else round(t["z"], 4),
                      "p": "" if t["p"] is None else f"{t['p']:.6f}",
                      "p_holm": "" if t["p"] is None else f"{t['p_holm']:.6f}"})


def comparacoes(linhas, saida: list) -> None:
    secao("4. COMPARAÇÕES EXPLORATÓRIAS ENTRE BRAÇOS (McNemar exato, Holm)")
    print("Pares pela mesma semente. Só condições COM degradação; resultado\n"
          "EXPLORATÓRIO, secundário à hipótese primária da seção 1.\n")

    testes = []
    for tipo in ORDEM_TIPOS:
        for nivel in sorted({r["nivel"] for r in linhas if r["nivel"] > 0}):
            for i, b1 in enumerate(ORDEM_BRACOS):
                for b2 in ORDEM_BRACOS[i + 1:]:
                    t = tabela_pareada(linhas, b1, b2, drift_type=tipo, nivel=nivel)
                    if not t["n"]:
                        continue
                    testes.append({"tipo": tipo, "nivel": nivel, "b1": b1, "b2": b2,
                                   **t, "p": mcnemar_exato(t["b"], t["c"])})

    for t, p_aj in zip(testes, holm([t["p"] for t in testes])):
        t["p_ajustado"] = p_aj

    significativos = [t for t in testes if t["p_ajustado"] < 0.05]
    print(f"  {len(significativos)} de {len(testes)} comparações significativas "
          f"(p ajustado < 0,05):\n")
    print(f"  {'tipo':<12}{'nível':>6}  {'braço A':<16}{'braço B':<16}"
          f"{'só A':>6}{'só B':>6}{'pares':>7}{'p Holm':>10}")
    print("  " + "-" * 80)
    for t in significativos:
        print(f"  {t['tipo']:<12}{t['nivel']:>6}  "
              f"{NOME_BRACO[t['b1']]:<16}{NOME_BRACO[t['b2']]:<16}"
              f"{t['b']:>6}{t['c']:>6}{t['n']:>7}{t['p_ajustado']:>10.5f}")

    for t in testes:
        saida.append({
            "drift_type": t["tipo"], "drift_level": t["nivel"],
            "braco_a": NOME_BRACO[t["b1"]], "braco_b": NOME_BRACO[t["b2"]],
            "pares": t["n"], "ambos_acertam": t["a"], "so_a_acerta": t["b"],
            "so_b_acerta": t["c"], "ambos_erram": t["d"],
            "p_bruto": round(t["p"], 6), "p_holm": round(t["p_ajustado"], 6),
            "significativo": t["p_ajustado"] < 0.05,
        })


def custo_tempo(linhas, saida: list) -> None:
    secao("5. CUSTO E TEMPO")
    com_drift = [r for r in linhas if r["nivel"] > 0]

    print("  Tempo mediano SÓ das execuções bem-sucedidas. Na coleta 1 a tabela\n"
          "  usava todas as execuções, e a mediana dos braços que falham na\n"
          "  maioria das vezes era o tempo limite do localizador (10 s).\n")
    print(f"  {'braço':<16}{'n':>5}{'sucessos':>10}{'tempo med. (sucessos)':>24}"
          f"{'US$/exec':>11}{'US$/sucesso':>13}")
    print("  " + "-" * 79)
    medianas_sucesso = {}
    for braco in ORDEM_BRACOS:
        sub = filtrar(com_drift, approach=braco)
        if not sub:
            continue
        k = sum(r["ok"] for r in sub)
        custo_total = sum(r["custo"] for r in sub)
        dur_ok = [r["duracao"] for r in sub if r["ok"]]
        medianas_sucesso[braco] = mediana(dur_ok) if dur_ok else None
        tem_custo = custo_total > 0
        por_sucesso = (custo_total / k) if (k and tem_custo) else None
        print(f"  {NOME_BRACO[braco]:<16}{len(sub):>5}{k:>10}"
              f"{(f'{mediana(dur_ok):.2f} s') if dur_ok else '—':>24}"
              f"{(f'{custo_total/len(sub):.4f}') if tem_custo else 'não se aplica':>11}"
              f"{(f'{por_sucesso:.4f}') if por_sucesso else 'não se aplica':>13}")
        saida.append({
            "braco": NOME_BRACO[braco], "n": len(sub), "sucessos": k,
            "taxa_sucesso": round(k / len(sub), 4),
            "duracao_mediana_sucessos_s": round(mediana(dur_ok), 3) if dur_ok else "",
            "duracao_mediana_todas_s": round(mediana([r["duracao"] for r in sub]), 3),
            "custo_medio_usd": round(custo_total / len(sub), 6) if tem_custo else "",
            "custo_por_sucesso_usd": round(por_sucesso, 6) if por_sucesso else "",
        })

    print("\n  Os braços determinísticos não têm custo de API por execução, mas não\n"
          "  são gratuitos (máquina, desenvolvimento e manutenção); por isso a\n"
          "  coluna registra 'não se aplica', e não zero.")

    print("\n  Tempo das execuções bem-sucedidas — agêntica contra cada braço (Mann-Whitney):")
    ag = [r["duracao"] for r in filtrar(com_drift, approach="agentic") if r["ok"]]
    for braco in ORDEM_BRACOS[:-1]:
        outro = [r["duracao"] for r in filtrar(com_drift, approach=braco) if r["ok"]]
        if not outro or not ag:
            continue
        _, p = mann_whitney(ag, outro)
        razao = mediana(ag) / mediana(outro) if mediana(outro) else float("inf")
        # a aproximação normal satura em 0 quando z é muito grande; declarar
        # "p = 0" seria errado, então reporta-se o limite do que é representável
        texto_p = f"p = {p:.2e}" if p > 1e-15 else "p < 1e-15"
        print(f"    agêntica vs {NOME_BRACO[braco]:<16} "
              f"{razao:>5.1f} vezes o tempo mediano   {texto_p}   "
              f"(n = {len(ag)} e {len(outro)})")

    ag_todos = filtrar(linhas, approach="agentic")
    if ag_todos:
        passos = [r["passos"] for r in ag_todos]
        print(f"\n  Agêntico — passos por execução: mediana {mediana(passos):.0f}, "
              f"mínimo {min(passos)}, máximo {max(passos)}")
        print(f"  Custo total do braço agêntico no experimento: "
              f"US$ {sum(r['custo'] for r in ag_todos):.2f} "
              f"({len(ag_todos)} execuções)")


def causa_da_falha(r) -> str:
    """
    Classifica a falha pela mensagem registrada. Serve para não repetir o erro
    da coleta 1, em que a causa foi inferida por eliminação e as falhas do login
    quebrado pelo degradador foram lidas como curas erradas.
    """
    m = (r.get("error_message") or "").lower()
    if r.get("error_type") in ("max_budget", "max_steps"):
        if "login não confirmado" in m:
            return "interrompida pelo teto, sem login confirmado"
        return f"interrompida pelo teto ({r['error_type']})"
    if "healing ambíguo" in m:
        return "self-healing desistiu (margem)"
    if "healing falhou" in m:
        return "self-healing desistiu (limiar)"
    if "login não confirmado" in m or "**/secure" in m:
        return "login não confirmado"
    if "carrinho" in m or "resumo do pedido" in m:
        return "carrinho diferente do esperado"
    if "esperava" in m and "elemento" in m:
        return "contagem de elementos diferente da esperada"
    if "thank you" in m or "confirmação" in m:
        return "confirmação do pedido ausente"
    if r.get("error_type") == "timeout":
        return "elemento não encontrado (tempo limite)"
    return r.get("error_type") or "outra"


def healing(linhas, saida: list) -> None:
    sub = [r for r in filtrar(linhas, approach="healing") if r["nivel"] > 0]
    if not sub or "heal_acionamentos" not in sub[0]:
        return
    secao("6. SELF-HEALING: ACIONAMENTO, CURA E ACERTO DA CURA")
    print("Acionamento = o seletor original falhou e o mecanismo tentou recuperar.\n"
          "Cura = escolheu um substituto acima do limiar e da margem.\n"
          "Correta/errada = o substituto era ou não o alvo verdadeiro, medido pela\n"
          "identidade que o degradador preserva, depois da escolha.\n")
    tem_verdade = "heal_corretos" in sub[0]
    print(f"  {'tipo':<12}{'nível':>6}{'acion.':>8}{'curas':>7}{'desist.':>9}"
          f"{'corretas':>10}{'erradas':>9}{'sem ref.':>10}{'sucesso':>10}")
    print("  " + "-" * 81)
    for tipo in ORDEM_TIPOS:
        for nivel in sorted({r["nivel"] for r in sub}):
            s = [r for r in sub if r["drift_type"] == tipo and r["nivel"] == nivel]
            if not s:
                continue
            soma = lambda campo: sum(int(r.get(campo) or 0) for r in s)  # noqa: E731
            a, c, d = soma("heal_acionamentos"), soma("heal_curados"), soma("heal_desistencias")
            corr = soma("heal_corretos") if tem_verdade else None
            err = soma("heal_errados") if tem_verdade else None
            ind = soma("heal_indeterminados") if tem_verdade else None
            k = sum(r["ok"] for r in s)
            print(f"  {tipo:<12}{nivel:>6}{a:>8}{c:>7}{d:>9}"
                  f"{'—' if corr is None else corr:>10}{'—' if err is None else err:>9}"
                  f"{'—' if ind is None else ind:>10}{f'{k}/{len(s)}':>10}")
            saida.append({"tipo": tipo, "nivel": nivel, "acionamentos": a, "curas": c,
                          "desistencias": d, "curas_corretas": corr, "curas_erradas": err,
                          "curas_sem_referencia": ind, "sucessos": k, "execucoes": len(s)})

    print("\n  Causas registradas das falhas do self-healing sob degradação:")
    causas = defaultdict(int)
    for r in sub:
        if not r["ok"]:
            causas[(r["drift_type"], r["site"], causa_da_falha(r))] += 1
    for (tipo, site, causa), n in sorted(causas.items()):
        print(f"    {tipo:<12}{site:<14}{n:>4}  {causa}")


def efeito_hipotese_primaria(linhas, saida: list) -> None:
    """
    Hipótese primária, fixada antes da coleta 2 (docs/plano_analise_coleta2.md).

    H1: sob degradação semântica de intensidade 0,50, 0,75 e 1,0, a abordagem
    agêntica conclui a tarefa com mais frequência que o self-healing.

    TRÊS MUDANÇAS EM RELAÇÃO À COLETA 1, TODAS APONTADAS NA REVISÃO
    ----------------------------------------------------------------
    1. Comparador fixo (self-healing). Antes era "a melhor determinística de
       cada condição", escolhida depois de ver os dados.
    2. Teste pareado. A semente N produz a mesma página degradada para os dois
       braços; cada execução agêntica tem uma gêmea self-healing. McNemar exato
       usa exatamente essa estrutura.
    3. Estratificação por aplicação. O pareamento já é feito dentro da mesma
       aplicação, mas o resultado também é reportado separadamente, porque a
       coleta 1 mostrou comportamentos muito diferentes entre os dois sites.

    Sensibilidade: execuções que contornaram a interface contadas como falha.
    """
    secao("1. HIPÓTESE PRIMÁRIA (McNemar exato, pares pela semente)")
    b_a, b_b = "agentic", COMPARADOR_PRIMARIO
    recorte = {"drift_type": "semantico"}
    sel = [r for r in linhas if r["drift_type"] == "semantico" and r["nivel"] >= 0.5]
    if not any(r["approach"] == b_a for r in sel) or not any(r["approach"] == b_b for r in sel):
        print("Sem dados dos dois braços no recorte da hipótese primária.\n")
        return

    def rodar(rotulo, linhas_recorte, contorno_como_falha=False):
        t = tabela_pareada(linhas_recorte, b_a, b_b,
                           contorno_como_falha=contorno_como_falha, **recorte)
        p = mcnemar_exato(t["b"], t["c"])
        rc = razao_chances_condicional(t["b"], t["c"])
        dif = diferenca_pareada(t["a"], t["b"], t["c"], t["d"]) if t["n"] else {}
        taxa_a = (t["a"] + t["b"]) / t["n"] if t["n"] else float("nan")
        taxa_b = (t["a"] + t["c"]) / t["n"] if t["n"] else float("nan")
        print(f"  {rotulo}")
        print(f"    pares: {t['n']}   (descartados por contorno: {t['descartados']};"
              f" sem par: {t['sem_par']})")
        print(f"    agêntica {t['a'] + t['b']}/{t['n']} ({100*taxa_a:.1f}%)   "
              f"self-healing {t['a'] + t['c']}/{t['n']} ({100*taxa_b:.1f}%)")
        print(f"    pares discordantes: só a agêntica acerta = {t['b']}; "
              f"só o self-healing acerta = {t['c']}")
        print(f"    McNemar exato p = {p:.6g}")
        if dif:
            print(f"    diferença pareada = {100*dif['dif']:.1f} pontos "
                  f"(IC 95% Agresti-Min {100*dif['ic'][0]:.1f} a {100*dif['ic'][1]:.1f})")
        print(f"    razão de chances condicional = {rc['razao']:.2f} "
              f"(IC 95% exato {rc['ic'][0]:.2f} a {rc['ic'][1]:.2f})\n")
        saida.append({"analise": rotulo, "pares": t["n"], "ambos_acertam": t["a"],
                      "so_agentica": t["b"], "so_self_healing": t["c"],
                      "ambos_erram": t["d"], "descartados_contorno": t["descartados"],
                      "taxa_agentica": round(taxa_a, 4), "taxa_self_healing": round(taxa_b, 4),
                      "p_mcnemar": f"{p:.8f}",
                      "dif_pareada": round(dif.get("dif", float("nan")), 4),
                      "dif_ic_inf": round(dif["ic"][0], 4) if dif else "",
                      "dif_ic_sup": round(dif["ic"][1], 4) if dif else "",
                      "rc_condicional": rc["razao"], "rc_ic_inf": rc["ic"][0],
                      "rc_ic_sup": rc["ic"][1]})

    print("Recorte fixado antes da coleta: degradação semântica, intensidades\n"
          "0,50 a 1,0, as duas aplicações; comparador fixo: self-healing.\n")
    rodar("PRINCIPAL — duas aplicações", sel)
    for site in ("saucedemo", "the_internet"):
        rodar(f"POR APLICAÇÃO — {site}", [r for r in sel if r["site"] == site])
    rodar("SENSIBILIDADE — contorno contado como falha", sel, contorno_como_falha=True)

    # Referência não pareada, só para comparação com a forma da coleta 1.
    sa = filtrar(sel, approach=b_a)
    sb = filtrar(sel, approach=b_b)
    ka, kb = sum(r["ok"] for r in sa), sum(r["ok"] for r in sb)
    lo, hi = newcombe_independente(kb, len(sb), ka, len(sa))
    print(f"  REFERÊNCIA NÃO PAREADA (forma da coleta 1): Fisher p = "
          f"{fisher_bicaudal(ka, len(sa) - ka, kb, len(sb) - kb):.6g}; diferença "
          f"{100*(ka/len(sa) - kb/len(sb)):.1f} pontos (IC 95% Newcombe "
          f"{100*lo:.1f} a {100*hi:.1f})")


def divergencia_entre_sites(linhas, saida: list) -> None:
    """
    Testa formalmente se cada braço se comporta de modo diferente nos dois sites.

    Existe porque o trabalho afirma que o self-healing "se sustenta num site e
    colapsa no outro". Isso era observação descritiva; aqui vira resultado.

    A distinção importa para a generalização: um braço cujo desempenho depende
    do site tem seu número agregado desprovido de sentido — a média descreve um
    comportamento que ele nunca tem em lugar nenhum.
    """
    secao("8. DIVERGÊNCIA ENTRE AS DUAS APLICAÇÕES (Fisher, Holm)")
    sel = [r for r in filtrar(linhas)
           if r["drift_type"] == "semantico" and r["nivel"] >= 0.5]
    if not sel:
        print("Sem dados no recorte.\n")
        return

    print("Recorte: degradação semântica, intensidades 0,5 a 1,0. As duas\n"
          "aplicações têm páginas diferentes, então aqui as amostras SÃO\n"
          "independentes e o Fisher é o teste adequado. Aplicação e tarefa\n"
          "variam juntas: uma diferença aqui não se atribui a nenhuma das duas.\n")
    print(f"  {'braço':<16}{'saucedemo':>12}{'the_internet':>15}{'p (Holm)':>13}")
    print("  " + "-" * 56)

    brutos, chaves = [], []
    for braco in ORDEM_BRACOS:
        a = [r for r in sel if r["approach"] == braco and r["site"] == "saucedemo"]
        b = [r for r in sel if r["approach"] == braco and r["site"] == "the_internet"]
        if not a or not b:
            continue
        sa, sb = sum(1 for r in a if r["ok"]), sum(1 for r in b if r["ok"])
        p = fisher_bicaudal(sa, len(a) - sa, sb, len(b) - sb)
        brutos.append(p)
        chaves.append((braco, sa, len(a), sb, len(b)))

    ajustados = holm(brutos)
    for (braco, sa, na, sb, nb), p, pa in zip(chaves, brutos, ajustados):
        marca = " *" if pa < 0.05 else ""
        lo, hi = newcombe_independente(sb, nb, sa, na)
        print(f"  {NOME_BRACO.get(braco, braco):<16}"
              f"{sa:>5}/{na:<6}{sb:>7}/{nb:<7}{pa:>12.6f}{marca}"
              f"   diferença {100*(sa/na - sb/nb):>6.1f} pontos [{100*lo:.1f}; {100*hi:.1f}]")
        saida.append({"braco": braco, "saucedemo_sucessos": sa, "saucedemo_n": na,
                      "the_internet_sucessos": sb, "the_internet_n": nb,
                      "p_bruto": f"{p:.8f}", "p_holm": f"{pa:.8f}",
                      "significativo": pa < 0.05})

    print("\n  * diferença significativa entre os sites após correção de Holm.")
    print("  Para esses braços, o número agregado dos dois sites não representa\n"
          "  o comportamento em nenhum deles, e deve ser reportado separado.")


def custo_versus_degradacao(linhas) -> None:
    """
    O custo do braço agêntico cresce conforme a página se degrada?

    A intuição diz que sim: página mais difícil, mais trabalho, mais tokens. Os
    dados dizem que não — e como esta é uma afirmação de AUSÊNCIA de efeito, ela
    exige teste. Cinco medianas parecidas não bastam como evidência.

    Só entram execuções bem-sucedidas. Incluir as que bateram no teto de custo
    tornaria o teste circular: elas foram interrompidas POR custo, então
    naturalmente teriam o maior valor.
    """
    ag = [r for r in filtrar(linhas) if r["approach"] == "agentic" and r["ok"]]
    if not ag:
        return
    secao("9. O CUSTO DAS EXECUÇÕES CONCLUÍDAS VARIA COM A INTENSIDADE? (Kruskal-Wallis)")
    print("Apenas execuções bem-sucedidas: as interrompidas pelo teto de custo\n"
          "tornariam o teste circular.\n")

    niveis = sorted({r["nivel"] for r in ag})
    grupos, custos = [], []
    print(f"  {'nível':>7}{'n':>6}{'custo mediano':>16}{'passos':>9}{'tempo':>9}")
    print("  " + "-" * 47)
    for nivel in niveis:
        c = [r for r in ag if r["nivel"] == nivel]
        valores = [r["custo"] for r in c]
        grupos.append(valores)
        custos.append(mediana(valores))
        print(f"  {nivel:>7}{len(c):>6}   US$ {mediana(valores):>9.4f}"
              f"{mediana([r['passos'] for r in c]):>9.0f}"
              f"{mediana([r['duracao'] for r in c]):>8.0f}s")

    h, gl, p = kruskal_wallis(grupos)
    print(f"\n  Kruskal-Wallis:  H = {h:.4f}   gl = {gl}   p = {p:.4f}")
    if p < 0.05:
        print("  -> os níveis DIFEREM em custo. A afirmação de patamar não se sustenta.")
    else:
        print("  -> não se detectou variação de custo entre os níveis.")
        print("\n  CUIDADO DE REDAÇÃO: isto não prova que o custo é constante. Prova\n"
              "  que estes dados não distinguem os níveis. Escreva 'não se detectou\n"
              "  variação', nunca 'o custo é constante'.")

    falhas = [r for r in filtrar(linhas)
              if r["approach"] == "agentic" and not r["ok"]]
    if falhas:
        print("\n  Custo das execuções agênticas que NÃO concluíram, lido contra o teto:")
        print(f"    concluídas (mediana)      US$ {mediana([r['custo'] for r in ag]):.4f}"
              f"   {mediana([r['passos'] for r in ag]):.0f} ações")
        print(f"    não concluídas (mediana)  US$ {mediana([r['custo'] for r in falhas]):.4f}"
              f"   {mediana([r['passos'] for r in falhas]):.0f} ações   (teto: US$ {TETO_USD:.2f})")
        no_teto = sum(1 for r in falhas if r["error_type"] in ("max_budget", "max_steps"))
        print(f"    {no_teto} de {len(falhas)} pararam no teto de custo ou de turnos.")
        print("\n  O custo de uma falha interrompida é, por construção, o próprio teto:\n"
              "  a razão entre os dois custos descreve o limite escolhido, não o agente.\n"
              "  Causas registradas:")
        causas = defaultdict(int)
        for r in falhas:
            causas[(r["site"], r["drift_type"], causa_da_falha(r))] += 1
        for (site, tipo, causa), n in sorted(causas.items()):
            print(f"    {site:<14}{tipo:<12}{n:>4}  {causa}")


def taxa_de_contorno(linhas) -> None:
    """
    Com que frequência o agente abandona a interface quando ela fica difícil.

    É um resultado por si só: mede a tendência de um agente a buscar o caminho
    mais estável disponível — inclusive um que a automação determinística, por
    construção, não usaria.
    """
    ag = [r for r in linhas if r["approach"] == "agentic"]
    fugas = contornos(ag)
    if not ag:
        return
    secao("10. TAXA DE CONTORNO DA INTERFACE (braço agêntico)")
    if not fugas:
        print("Nenhuma execução contornou a interface degradada.\n")
        return
    print("Execuções em que o agente mirou atributos imunes ao drift.\n")
    print(f"  {'drift':<13}{'nível':>7}{'contornos':>12}{'total':>8}{'taxa':>9}")
    print("  " + "-" * 50)
    for tipo in ORDEM_TIPOS:
        for nivel in sorted({r["nivel"] for r in ag}):
            todas = [r for r in ag if r["drift_type"] == tipo and r["nivel"] == nivel]
            f = contornos(todas)
            if todas:
                print(f"  {tipo:<13}{nivel:>7}{len(f):>12}{len(todas):>8}"
                      f"{100*len(f)/len(todas):>8.0f}%")
    print(f"\n  Total: {len(fugas)} de {len(ag)} execuções agênticas "
          f"({100*len(fugas)/len(ag):.0f}%).")


def erros(linhas) -> None:
    secao("7. NATUREZA DAS FALHAS")
    print("Distingue incapacidade real de limite artificial do experimento.\n")
    contagem = defaultdict(lambda: defaultdict(int))
    for r in linhas:
        if not r["ok"]:
            contagem[r["approach"]][r["error_type"] or "(vazio)"] += 1
    for braco in ORDEM_BRACOS:
        if braco not in contagem:
            continue
        total = sum(contagem[braco].values())
        print(f"  {NOME_BRACO[braco]} — {total} falhas")
        for tipo_erro, n in sorted(contagem[braco].items(), key=lambda t: -t[1]):
            print(f"      {tipo_erro:<22}{n:>5}  ({100*n/total:.0f}%)")
        causas = defaultdict(int)
        for r in linhas:
            if r["approach"] == braco and not r["ok"]:
                causas[causa_da_falha(r)] += 1
        for causa, n in sorted(causas.items(), key=lambda t: -t[1]):
            print(f"        causa: {causa:<46}{n:>5}")


def figuras(linhas) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n(matplotlib não instalado — figuras puladas; "
              "pip install matplotlib)")
        return

    os.makedirs(DIR_SAIDA, exist_ok=True)
    niveis = sorted({r["nivel"] for r in linhas})

    # Estilos distintos por braço: em várias condições três curvas ficam
    # sobrepostas em 100%, e só a cor não bastaria para distingui-las —
    # a última desenhada esconderia as demais.
    ESTILO = {
        "deterministic": {"ls": "-", "marker": "o", "lw": 1.8},
        "xpath": {"ls": (0, (3, 1, 1, 1, 1, 1)), "marker": "v", "lw": 2.0},
        "semantic": {"ls": "--", "marker": "s", "lw": 2.6},
        "healing": {"ls": "-.", "marker": "^", "lw": 2.0},
        "agentic": {"ls": ":", "marker": "D", "lw": 2.6},
    }

    fig, eixos = plt.subplots(1, 3, figsize=(15, 4.5), sharey=True)
    for eixo, tipo in zip(eixos, ORDEM_TIPOS):
        for braco in ORDEM_BRACOS:
            xs, ys = [], []
            for n in niveis:
                sub = filtrar(linhas, approach=braco, drift_type=tipo, nivel=n)
                if sub:
                    xs.append(n)
                    ys.append(100 * sum(r["ok"] for r in sub) / len(sub))
            if xs:
                eixo.plot(xs, ys, label=NOME_BRACO[braco],
                          markersize=6, alpha=0.9, **ESTILO[braco])
        eixo.set_title(f"drift {tipo}")
        eixo.set_xlabel("intensidade do drift")
        eixo.set_ylim(-5, 108)
        eixo.grid(alpha=0.3)
    eixos[0].set_ylabel("taxa de sucesso (%)")
    eixos[0].legend(loc="center left")
    fig.suptitle("Robustez por abordagem e tipo de degradação da interface")
    fig.tight_layout()
    fig.savefig(os.path.join(DIR_SAIDA, "fig1_robustez.png"), dpi=150)
    plt.close(fig)

    com_drift = [r for r in linhas if r["nivel"] > 0]
    fig, (esq, dir_) = plt.subplots(1, 2, figsize=(12, 4.5))
    dados = [[r["duracao"] for r in filtrar(com_drift, approach=b)]
             for b in ORDEM_BRACOS]
    rotulos = [NOME_BRACO[b] for b in ORDEM_BRACOS]
    esq.boxplot([d for d in dados if d],
                tick_labels=[r for r, d in zip(rotulos, dados) if d])
    esq.set_ylabel("duração (s)")
    esq.set_title("Tempo por execução")
    esq.set_yscale("log")
    esq.grid(alpha=0.3)

    taxas, custos, nomes = [], [], []
    for b in ORDEM_BRACOS:
        sub = filtrar(com_drift, approach=b)
        if sub:
            taxas.append(100 * sum(r["ok"] for r in sub) / len(sub))
            custos.append(sum(r["custo"] for r in sub) / len(sub))
            nomes.append(NOME_BRACO[b])
    dir_.scatter(custos, taxas, s=90)
    for x, y, nome in zip(custos, taxas, nomes):
        dir_.annotate(nome, (x, y), textcoords="offset points", xytext=(8, 4))
    dir_.set_xlabel("custo médio por execução (US$)")
    dir_.set_ylabel("taxa de sucesso (%)")
    dir_.set_title("Confiabilidade x custo")
    dir_.set_ylim(-5, 110)
    dir_.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(DIR_SAIDA, "fig2_custo_tempo.png"), dpi=150)
    plt.close(fig)

    print(f"\nFiguras salvas em {DIR_SAIDA}")


def custo_recalculado(linhas, dir_transcricoes: str, saida: list) -> None:
    """
    Recalcula o custo agêntico a partir da contagem de tokens, com o preço público.

    POR QUE EXISTE
    --------------
    O artigo da coleta 1 dizia que o custo vinha "da contagem de tokens e dos
    preços de US$ 2 e US$ 10 por milhão", com multiplicadores de 1,25 e 0,10 para
    o cache. O número gravado, porém, era o `total_cost_usd` do SDK, que bate com
    US$ 3 e US$ 15 e escrita de cache a 2x: US$ 36,54 no total, contra cerca de
    US$ 20 pela fórmula declarada. Aqui os dois aparecem lado a lado, e o texto
    deve dizer de onde vem cada um.
    """
    import json

    ag = [r for r in linhas if r["approach"] == "agentic"]
    if not ag or not os.path.isdir(dir_transcricoes):
        return
    secao("11. CUSTO AGÊNTICO: ESTIMATIVA DO SDK E RECÁLCULO PELOS TOKENS")
    soma = {"sdk": 0.0, "cache5min": 0.0, "cache1h": 0.0}
    por_execucao = {"sdk": [], "cache5min": [], "cache1h": []}
    faltando = 0
    for r in ag:
        nome = f"{r['site']}_{r['task']}_{r['drift_type']}{r['nivel']}_rep{r['rep']}.json"
        caminho = os.path.join(dir_transcricoes, nome)
        if not os.path.exists(caminho):
            faltando += 1
            continue
        with open(caminho, encoding="utf-8") as f:
            d = json.load(f)
        escrita = d.get("tokens_cache_write", 0) or 0
        leitura = d.get("tokens_cache_read", 0) or 0
        simples = (d.get("tokens_input", 0) or 0) - escrita - leitura
        saida_t = d.get("tokens_output", 0) or 0

        def preco(mult_escrita):
            return (simples * PRECO_ENTRADA + escrita * PRECO_ENTRADA * mult_escrita
                    + leitura * PRECO_ENTRADA * 0.1 + saida_t * PRECO_SAIDA) / 1e6

        valores = {"sdk": r["custo"], "cache5min": preco(1.25), "cache1h": preco(2.0)}
        for k, v in valores.items():
            soma[k] += v
            por_execucao[k].append(v)

    n = len(por_execucao["sdk"])
    print(f"  {n} execuções agênticas com transcrição ({faltando} sem arquivo)\n")
    print(f"  {'base de cálculo':<44}{'total US$':>11}{'mediana US$':>13}")
    print("  " + "-" * 68)
    rotulos = {"sdk": "estimativa gravada pelo SDK",
               "cache5min": "preço público, escrita de cache a 1,25x",
               "cache1h": "preço público, escrita de cache a 2x"}
    for k in ("sdk", "cache5min", "cache1h"):
        print(f"  {rotulos[k]:<44}{soma[k]:>11.2f}{mediana(por_execucao[k]):>13.4f}")
        saida.append({"base": rotulos[k], "execucoes": n, "total_usd": round(soma[k], 4),
                      "mediana_usd": round(mediana(por_execucao[k]), 6)})
    print("\n  O teto de US$ 0,60 é aplicado sobre a estimativa do SDK. A execução\n"
          "  agêntica usou a cota de uma assinatura: nenhum dos valores é despesa.")


def exportar(nome: str, linhas: list) -> None:
    if not linhas:
        return
    os.makedirs(DIR_SAIDA, exist_ok=True)
    caminho = os.path.join(DIR_SAIDA, nome)
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0].keys()))
        w.writeheader()
        w.writerows(linhas)
    print(f"  {caminho}")


def autoteste() -> None:
    """
    Confere as implementações contra valores calculados à mão.

    Os valores de referência abaixo foram derivados manualmente a partir das
    fórmulas, e não copiados de memória — a derivação está no comentário de cada
    caso, para que a banca possa refazer a conta.
    """
    casos = [
        # p = 2 * C(8,8)*C(8,0)/C(16,8) = 2/12870
        ("Fisher [[8,0],[0,8]]", fisher_bicaudal(8, 0, 0, 8), 2 / 12870),
        ("Fisher [[3,5],[5,3]]", fisher_bicaudal(3, 5, 5, 3), 0.61927),
        ("Fisher [[1,9],[11,3]]", fisher_bicaudal(1, 9, 11, 3), 0.00276),
        # p=1, n=8: d=1.4802; centro=(1+0.2401)/1.4802=0.83779;
        # meio=(1.96/1.4802)*sqrt(0+0.015006)=0.16221; inferior=0.67558
        ("Wilson 8/8 inf", wilson(8, 8)[0], 0.67558),
        ("Wilson 0/8 sup", wilson(0, 8)[1], 0.32442),
        # p=0.5, n=8: centro=0.5; meio=1.3241*sqrt(0.03125+0.015006)=0.28479
        ("Wilson 4/8 inf", wilson(4, 8)[0], 0.21521),
    ]
    # níveis [0, 0.5, 1], sucessos [8, 4, 0], totais [8, 8, 8]:
    #   p=0.5; T = 0*(8-4) + 0.5*(4-4) + 1*(0-4) = -4
    #   Var = 0.25 * [sum n_i x_i^2 - (sum n_i x_i)^2/N] = 0.25*(10-6) = 1
    #   z = -4 / sqrt(1) = -4
    z, _ = cochran_armitage([0, 0.5, 1.0], [8, 4, 0], [8, 8, 8])
    casos.append(("Cochran-Armitage z", z, -4.0))
    # separação completa, n1=n2=5: U=0, mu=12.5, var=25/12*11=22.9167,
    # z=-2.6113, p=2*(1-Phi(2.6113))=0.00902.
    # NOTA: o teste EXATO daria 2/252 = 0.00794. A diferença é a aproximação
    # normal, adequada aos n desta análise (>= 8 por grupo) e usada por padrão
    # também no scipy quando o n cresce.
    _, p_mw = mann_whitney([1, 2, 3, 4, 5], [6, 7, 8, 9, 10])
    casos.append(("Mann-Whitney p (aprox.)", p_mw, 0.00902))

    # --- tamanho de efeito -------------------------------------------------
    # A: 10/40 = 25%   B: 30/40 = 75%
    # diferença de risco = 0,75 - 0,25 = 0,50
    # erro = sqrt(0,25*0,75/40 + 0,75*0,25/40) = sqrt(0,009375) = 0,0968246
    # razão de chances = (30*30)/(10*10) = 9
    # NNT = 1/0,50 = 2
    ef = tamanho_de_efeito(10, 40, 30, 40)
    casos.append(("efeito — diferença de risco", ef["dif"], 0.50))
    casos.append(("efeito — IC inferior", ef["dif_ic"][0], 0.50 - 1.96 * 0.0968246))
    casos.append(("efeito — razão de chances", ef["razao"], 9.0))
    casos.append(("efeito — NNT", ef["nnt"], 2.0))
    # Correção de Haldane-Anscombe quando há casela vazia: 0/10 vs 10/10
    # vira (10,5 * 10,5)/(0,5 * 0,5) = 441
    ef0 = tamanho_de_efeito(0, 10, 10, 10)
    casos.append(("efeito — casela vazia corrigida", ef0["razao"], 441.0))

    # --- qui-quadrado, cauda superior --------------------------------------
    # Valores tabelados: P(X>3,841|gl=1)=0,050  P(X>5,991|gl=2)=0,050
    #                    P(X>9,488|gl=4)=0,050  P(X>0|gl=3)=1,0
    casos.append(("qui2 cauda gl=1", _qui2_cauda(3.8415, 1), 0.05))
    casos.append(("qui2 cauda gl=2", _qui2_cauda(5.9915, 2), 0.05))
    casos.append(("qui2 cauda gl=4", _qui2_cauda(9.4877, 4), 0.05))

    # --- Kruskal-Wallis ----------------------------------------------------
    # Três grupos idênticos não podem diferir: H = 0, p = 1.
    h_ig, _, p_ig = kruskal_wallis([[1, 2, 3], [1, 2, 3], [1, 2, 3]])
    casos.append(("Kruskal-Wallis H (iguais)", h_ig, 0.0))
    casos.append(("Kruskal-Wallis p (iguais)", p_ig, 1.0))
    # Separação total, 3 grupos de 3. Postos: 1-3, 4-6, 7-9.
    # somas 6, 15, 24; n=9
    # H = 12/(9*10) * (36/3 + 225/3 + 576/3) - 3*10
    #   = 0,133333 * 279 - 30 = 37,2 - 30 = 7,2
    h_sep, gl_sep, _ = kruskal_wallis([[1, 2, 3], [4, 5, 6], [7, 8, 9]])
    casos.append(("Kruskal-Wallis H (separados)", h_sep, 7.2))
    casos.append(("Kruskal-Wallis graus liberdade", gl_sep, 2))
    # Com dois grupos, Kruskal-Wallis equivale ao Mann-Whitney: mesmo p.
    _, p_dois = mann_whitney([1, 2, 3, 4, 5], [6, 7, 8, 9, 10])
    _, _, p_kw2 = kruskal_wallis([[1, 2, 3, 4, 5], [6, 7, 8, 9, 10]])
    casos.append(("KW com 2 grupos == Mann-Whitney", p_kw2, p_dois))

    # --- estatística pareada (coleta 2) ----------------------------------
    # McNemar exato com b = 9, c = 2: n = 11, cauda = [C(11,0)+C(11,1)+C(11,2)]/2^11
    #   = (1 + 11 + 55)/2048 = 67/2048; bicaudal = 134/2048 = 0,0654297
    casos.append(("McNemar exato b=9 c=2", mcnemar_exato(9, 2), 134 / 2048))
    casos.append(("McNemar sem discordantes", mcnemar_exato(0, 0), 1.0))
    # Clopper-Pearson 9/11: conferido com scipy.stats.beta.ppf(0,025; 9; 3) e
    # beta.ppf(0,975; 10; 2) = 0,4822441 e 0,9771688
    lo_cp, hi_cp = clopper_pearson(9, 11)
    casos.append(("Clopper-Pearson 9/11 inf", lo_cp, 0.4822441))
    casos.append(("Clopper-Pearson 9/11 sup", hi_cp, 0.9771688))
    # razão condicional 9/2 = 4,5; IC = p/(1-p) nos limites acima: 0,93141 e 42,7997
    rc = razao_chances_condicional(9, 2)
    casos.append(("RC condicional", rc["razao"], 4.5))
    casos.append(("RC condicional IC inf", rc["ic"][0], 0.9314123))
    # Agresti-Min com a=12, b=9, c=2, d=21 (n=44): caselas + 0,5, n* = 46
    #   centro = (9,5 - 2,5)/46 = 0,152174; erro = sqrt(12 - 49/46)/46 = 0,071940
    #   IC = 0,152174 -+ 1,96 x 0,071940 = 0,011276 a 0,293071; diferença = 7/44
    dp = diferenca_pareada(12, 9, 2, 21)
    casos.append(("Agresti-Min diferença", dp["dif"], 7 / 44))
    casos.append(("Agresti-Min IC inf", dp["ic"][0], 0.0112764))
    casos.append(("Agresti-Min IC sup", dp["ic"][1], 0.2930714))

    print("AUTOTESTE — implementação própria vs. valores derivados à mão\n")
    tudo_ok = True
    for nome, obtido, esperado in casos:
        ok = abs(obtido - esperado) < 1e-4
        tudo_ok &= ok
        print(f"  {'OK ' if ok else 'ERRO'}  {nome:<26} obtido {obtido:>10.5f}  "
              f"esperado {esperado:>10.5f}")
    print("\n" + ("Todos os testes passaram." if tudo_ok
                  else "ATENÇÃO: divergência encontrada."))


def main() -> None:
    ap = argparse.ArgumentParser(description="Analisa os resultados do experimento")
    ap.add_argument("--csv", default=CSV_ENTRADA)
    ap.add_argument("--sem-figuras", action="store_true")
    ap.add_argument("--transcricoes", default=DIR_TRANSCRICOES)
    ap.add_argument("--autoteste", action="store_true",
                    help="valida os testes estatísticos e sai")
    args = ap.parse_args()

    if args.autoteste:
        autoteste()
        return

    linhas = carregar(args.csv)
    print(f"{len(linhas)} execuções carregadas de {args.csv}")
    print(f"  braços: {', '.join(sorted({NOME_BRACO.get(r['approach'], r['approach']) for r in linhas}))}")
    print(f"  sites: {', '.join(sorted({r['site'] for r in linhas}))}")

    fugas = contornos(linhas)
    if fugas:
        print(f"\n  ATENÇÃO: {len(fugas)} execução(ões) contornaram a interface")
        print("  (miraram atributos que o drift não altera). Ficam FORA das taxas")
        print("  de sucesso e são reportadas à parte, na seção 10.")

    tab_sucesso, tab_comp, tab_custo = [], [], []
    tab_efeito, tab_sites, tab_tend, tab_heal = [], [], [], []
    efeito_hipotese_primaria(linhas, tab_efeito)
    tabela_sucesso(linhas, tab_sucesso)
    tendencia(linhas, tab_tend)
    comparacoes(linhas, tab_comp)
    custo_tempo(linhas, tab_custo)
    healing(linhas, tab_heal)
    erros(linhas)
    divergencia_entre_sites(linhas, tab_sites)
    custo_versus_degradacao(linhas)
    taxa_de_contorno(linhas)
    tab_custo_tokens = []
    custo_recalculado(linhas, args.transcricoes, tab_custo_tokens)

    secao("ARQUIVOS GERADOS")
    exportar("hipotese_primaria.csv", tab_efeito)
    exportar("tabela_sucesso.csv", tab_sucesso)
    exportar("tendencia.csv", tab_tend)
    exportar("comparacoes_pareadas.csv", tab_comp)
    exportar("resumo_custo_tempo.csv", tab_custo)
    exportar("self_healing.csv", tab_heal)
    exportar("divergencia_sites.csv", tab_sites)
    exportar("custo_tokens.csv", tab_custo_tokens)

    if not args.sem_figuras:
        figuras(linhas)
    print()


if __name__ == "__main__":
    main()
