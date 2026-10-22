"""
Ponto de equilíbrio econômico entre automação determinística e agêntica.

A PERGUNTA
----------
A análise estatística responde qual abordagem é mais confiável. Não responde
qual compensa. Uma abordagem que acerta mais pode custar tanto que não valha, e
uma abordagem gratuita que quebra toda semana não é barata — é um passivo.

Este script responde: DADO um volume de execuções e uma frequência de mudança
de interface, qual abordagem custa menos por ano? É o insumo do último objetivo
específico do trabalho, o critério de decisão.

A PROBABILIDADE DE QUEBRA É POR TIPO DE MUDANÇA (coleta 2)
-----------------------------------------------------------
Na coleta 1 o modelo usava a taxa de falha de cada abordagem somada sobre os
três tipos de degradação. A própria discussão do artigo mostrava que essa taxa
agregada descreve um comportamento que não existe: o XPath resiste a toda
mudança cosmética e quebra em quase toda mudança estrutural, e a média das duas
depende só da proporção de cada tipo no desenho experimental. Aqui a quebra é
medida separadamente para cada tipo, e o custo é calculado para uma mudança
daquele tipo. Quem usar o critério precisa saber que tipo de mudança espera.

AS FALHAS SÃO PERSISTENTES
--------------------------
Cada repetição do experimento usa uma semente diferente, isto é, uma mudança de
interface diferente. Em produção a mudança é uma só: se a automação não dá conta
dela, falha em toda execução até alguém intervir, no determinístico e no
agêntico. A taxa medida estima a chance de uma mudança qualquer derrubar a
automação; não mede recuperação espontânea.

O custo de uma execução agêntica que falha é, na prática, o teto de custo por
execução (as execuções não concluídas param nele). É um parâmetro escolhido pelo
experimentador, e o relatório o trata como tal.

O MODELO
--------
A mesma fórmula para todos os braços. Qualquer tratamento especial precisaria
ser justificado por medida, não por intuição sobre a tecnologia:

    custo = execuções_normais    x custo_de_uma_execução_bem_sucedida
          + quebras              x custo_de_intervir
          + execuções_degradadas x custo_de_uma_execução_que_falha
          + execuções_degradadas x valor_da_execução_perdida

PARÂMETROS E DE ONDE VÊM
------------------------
  custo de execução OK      MEDIDO (média das execuções concluídas), pelo preço
                            público recalculado dos tokens (ver PRECO_ENTRADA)
  custo de execução FALHA   MEDIDO, e limitado pelo teto de US$ 0,60 do SDK
  P(quebra | abordagem, tipo)  MEDIDO, por tipo de degradação
  tempo de intervenção      PREMISSA: 0,5 h
  valor da hora             PREMISSA: R$ 250, com sensibilidade de R$ 100 a R$ 300
  câmbio                    PREMISSA DECLARADA: R$ 5,19 por dólar,
                            com sensibilidade de 10% para cima e para baixo

Os três primeiros são medida; os demais são premissa declarada. O script roda em
faixa, não em ponto único, porque premissa sem faixa esconde a incerteza que
carrega.

O CUSTO DE INTERVIR É O MESMO PARA TODOS OS BRAÇOS. É premissa, não medida, e
merece atenção: no determinístico é reescrever um seletor; no agêntico é ajustar
a instrução, revalidar a tarefa ou tratar a exceção. Nada indica que um seja
sistematicamente mais barato. Assumir zero para o agêntico — como fazia a
primeira versão deste modelo — favorecia indevidamente essa abordagem.

O QUE O MODELO NÃO INCLUI, e pesa contra o agêntico: ajuste de instrução ao
longo do tempo, revalidação a cada troca de versão do modelo, monitoramento
explícito (a falha agêntica não gera exceção, parece lentidão) e dependência de
fornecedor externo. Nada disso foi medido aqui, e a omissão é declarada.

USO
---
    python modelo_economico.py                    modelo do artigo (seção 3.9)
    python modelo_economico.py --autoteste        confere as contas
    python modelo_economico.py --hora 300         valor da hora
    python modelo_economico.py --conserto 2.0     conserto mais demorado
    python modelo_economico.py --mediana          custo por execução pela mediana
    python modelo_economico.py --com-perda        soma o custo da parada até o conserto

O modelo do artigo usa o custo MÉDIO por execução, porque o custo anual é uma
soma de execuções, e não inclui a parada. As duas opções acima são variações.
"""

from __future__ import annotations

import argparse
import csv
import os

RAIZ = os.path.dirname(os.path.abspath(__file__))
ENTRADA = os.path.join(RAIZ, "data", "results.csv")
SAIDA = os.path.join(RAIZ, "data", "analise")

NOME = {
    "deterministic": "ID/CSS",
    "xpath": "XPath",
    "semantic": "papel e rótulo",
    "healing": "self-healing",
    "agentic": "agêntica",
}
TIPOS = ("cosmetico", "estrutural", "semantico")
NOME_TIPO = {"cosmetico": "cosmética", "estrutural": "estrutural", "semantico": "semântica"}
ORDEM = ["deterministic", "xpath", "semantic", "healing", "agentic"]

# Câmbio DECLARADO. Fixá-lo é o correto num trabalho acadêmico: o resultado
# fica reproduzível, e quem reler em outra data sabe qual taxa foi usada.
CAMBIO = 5.19          # R$ por US$, premissa fixada, variada na sensibilidade
DIAS_UTEIS = 252

# Base do custo por execução agêntica (coleta 2). A coluna `cost_usd` guarda a
# estimativa do SDK, que usa tabela de preços própria e é sobre ela que o teto
# de US$ 0,60 incide. O artigo reporta o custo pelo preço público do modelo,
# recalculado a partir dos tokens de cada transcrição, com os mesmos valores da
# seção 11 de analise.py. `--base-custo sdk` refaz o modelo com a estimativa.
PRECO_ENTRADA = 2.0        # US$ por milhão de tokens de entrada
PRECO_SAIDA = 10.0         # US$ por milhão de tokens de saída
MULT_ESCRITA_CACHE = 2.0   # escrita de cache de 1 hora (1,25 para 5 minutos)
MULT_LEITURA_CACHE = 0.1
DIR_TRANSCRICOES = os.path.join(RAIZ, "data", "transcripts")


def custo_publico(linha: dict, dir_transcricoes: str = DIR_TRANSCRICOES):
    """Custo de uma execução agêntica pelo preço público; None se faltar a transcrição."""
    import json
    nome = (f"{linha['site']}_{linha['task']}_{linha['drift_type']}"
            f"{float(linha['drift_level'])}_rep{int(float(linha['repetition']))}.json")
    caminho = os.path.join(dir_transcricoes, nome)
    if not os.path.exists(caminho):
        return None
    with open(caminho, encoding="utf-8") as f:
        d = json.load(f)
    escrita = d.get("tokens_cache_write", 0) or 0
    leitura = d.get("tokens_cache_read", 0) or 0
    simples = (d.get("tokens_input", 0) or 0) - escrita - leitura
    saida = d.get("tokens_output", 0) or 0
    return (simples * PRECO_ENTRADA + escrita * PRECO_ENTRADA * MULT_ESCRITA_CACHE
            + leitura * PRECO_ENTRADA * MULT_LEITURA_CACHE + saida * PRECO_SAIDA) / 1e6


# ------------------------------------------------------------ medidas

def medir(caminho: str = ENTRADA, base: str = "publico",
          dir_transcricoes: str = DIR_TRANSCRICOES, estatistica: str = "media") -> dict:
    """
    Extrai do conjunto de dados o que o modelo precisa: custo unitário de cada
    abordagem e probabilidade de quebra sob degradação.

    A probabilidade de quebra é calculada apenas sobre execuções COM degradação
    aplicada. Incluir o nível zero diluiria a taxa com um cenário em que, por
    construção, nada quebra.
    """
    with open(caminho, encoding="utf-8") as f:
        linhas = [r for r in csv.DictReader(f) if r.get("error_type") != "contornou"]

    faltando = []

    def custo(r) -> float:
        if base == "publico" and r["approach"] == "agentic":
            c = custo_publico(r, dir_transcricoes)
            if c is not None:
                return c
            faltando.append(r)
        return float(r["cost_usd"] or 0)

    medidas = {}
    for braco in ORDEM:
        com_drift = [r for r in linhas
                     if r["approach"] == braco and float(r["drift_level"]) > 0]
        if not com_drift:
            continue
        falhas = [r for r in com_drift if r["success"] != "True"]
        sucessos = [r for r in com_drift if r["success"] == "True"]

        # Os dois custos são medidos SEPARADAMENTE, e não como uma média única.
        # Usar a média esconderia justamente o que interessa: no braço agêntico,
        # uma execução que falha tende a custar bem mais que uma que dá certo,
        # porque o agente insiste até bater no teto imposto.
        def med(g):
            v = sorted(custo(r) for r in g)
            if not v:
                return 0.0
            if estatistica == "media":
                return sum(v) / len(v)
            m = len(v) // 2
            return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2

        por_tipo = {}
        for tipo in ("cosmetico", "estrutural", "semantico"):
            c = [r for r in com_drift if r["drift_type"] == tipo]
            if c:
                por_tipo[tipo] = sum(1 for r in c if r["success"] != "True") / len(c)

        medidas[braco] = {
            "p_quebra": len(falhas) / len(com_drift),
            "p_quebra_tipo": por_tipo,
            "custo_ok_usd": med(sucessos),
            "custo_falha_usd": med(falhas),
            "n": len(com_drift),
        }
    if faltando:
        print(f"ATENÇÃO: {len(faltando)} execuções agênticas sem transcrição; "
              "para elas foi usada a estimativa do SDK.")
    return medidas


# ------------------------------------------------------------ o modelo

def custo_anual(medidas: dict, braco: str, execucoes_ano: int,
                mudancas_ano: float, custo_conserto: float,
                contar_perda: bool = True, latencia_dias: float = 1.0,
                tipo_mudanca: str = "semantico", cambio: float | None = None) -> dict:
    """
    Custo anual de operar uma automação, em reais.

    A FÓRMULA É A MESMA PARA TODOS OS BRAÇOS. Isso é deliberado: qualquer
    tratamento especial precisaria ser justificado por medida, e não por
    intuição sobre como cada tecnologia "deveria" se comportar. As únicas
    diferenças entre os braços são parâmetros MEDIDOS neste experimento.

        custo = execuções x custo_de_uma_execução_normal
              + quebras  x custo_de_intervir
              + execuções_degradadas x custo_de_uma_execução_que_falha
              + execuções_degradadas x valor_da_execução_perdida

    POR QUE A FALHA AGÊNTICA TAMBÉM É PERSISTENTE
    ---------------------------------------------
    A primeira versão deste modelo tratava a falha agêntica como transiente:
    uma execução falha, a próxima dá certo, ninguém precisa fazer nada. A
    premissa veio de observar que, dentro de uma mesma condição experimental,
    algumas repetições passam e outras falham.

    Era erro de leitura. Cada repetição usa uma SEMENTE diferente, ou seja, uma
    mutação diferente da interface. Em produção a mudança é UMA SÓ e não se
    altera a cada execução. Se o agente não dá conta daquela mudança
    específica, ele falha em toda execução até alguém intervir — exatamente
    como o determinístico.

    A variação entre repetições mede a probabilidade de uma mudança QUALQUER
    derrubar o agente. Não mede recuperação espontânea.

    E AQUI A ASSIMETRIA SE INVERTE
    ------------------------------
    Corrigida a persistência, a comparação fica menos favorável ao agêntico do
    que a taxa de sucesso sugeria:

      determinístico quebrado   falha em ~10 s, com custo de execução ZERO.
                                O prejuízo é só o trabalho não feito.
      agêntico quebrado         falha em ~4 min, consumindo US$ 0,61 por
                                tentativa — 4,7x o custo de uma execução
                                bem-sucedida. Ele queima dinheiro enquanto
                                está quebrado, e não avisa.

    Em alto volume, uma automação agêntica quebrada é ativamente cara. Uma
    determinística quebrada é apenas inútil.
    """
    cambio = CAMBIO if cambio is None else cambio
    m = medidas[braco]
    p = m["p_quebra_tipo"][tipo_mudanca]
    custo_ok = m["custo_ok_usd"] * cambio
    custo_falha = m["custo_falha_usd"] * cambio

    quebras = mudancas_ano * p

    # Execuções perdidas entre a quebra e a intervenção.
    #
    # DEFEITO CORRIGIDO: a primeira versão amarrava a janela de exposição à
    # frequência de mudança — supunha que a quebra só era percebida na mudança
    # seguinte. Com uma mudança a cada dois anos, isso implicava um ano inteiro
    # de automação quebrada sem ninguém notar, o que é absurdo. E produzia uma
    # descontinuidade visível na figura: o custo saltava de zero para o máximo
    # assim que a frequência deixava de ser exatamente zero.
    #
    # A janela real é limitada pela DETECÇÃO, não pela próxima mudança. Uma
    # automação com log é percebida em um dia; sem log, demora mais. O limite
    # pela metade do intervalo continua valendo como teto — não é possível
    # ficar quebrado por mais tempo do que existe entre duas mudanças.
    por_dia = execucoes_ano / DIAS_UTEIS
    janela = por_dia * latencia_dias
    if mudancas_ano > 0:
        janela = min(janela, (execucoes_ano / mudancas_ano) / 2)
    degradadas = min(quebras * janela, execucoes_ano)

    operacao = (execucoes_ano - degradadas) * custo_ok
    manutencao = quebras * custo_conserto
    # O que se GASTA tentando executar durante a degradação. Zero nos braços
    # determinísticos (um timeout não consome tokens); relevante no agêntico.
    desperdicio = degradadas * custo_falha
    # O que se PERDE por não executar. Avaliado, para todos os braços igualmente,
    # pelo custo de fazer a mesma tarefa por outro meio automatizado.
    perda = (degradadas * medidas["agentic"]["custo_ok_usd"] * cambio
             if contar_perda else 0.0)

    return {"operacao": operacao, "manutencao": manutencao,
            "desperdicio": desperdicio, "perda": perda,
            "total": operacao + manutencao + desperdicio + perda,
            "quebras": quebras, "degradadas": degradadas}


def ponto_de_equilibrio(medidas: dict, braco: str, execucoes_ano: int,
                        custo_conserto: float, contar_perda: bool = True,
                        latencia_dias: float = 1.0, tipo_mudanca: str = "semantico",
                        cambio: float | None = None):
    """
    Frequência anual de mudança de interface a partir da qual o agêntico passa
    a custar menos que a abordagem determinística indicada.

    Busca por bisseção. Não há fórmula fechada porque o termo de perda depende
    do número de mudanças de forma não linear.
    """
    def diferenca(f):
        d = custo_anual(medidas, braco, execucoes_ano, f, custo_conserto,
                        contar_perda, latencia_dias, tipo_mudanca, cambio)
        a = custo_anual(medidas, "agentic", execucoes_ano, f, custo_conserto,
                        contar_perda, latencia_dias, tipo_mudanca, cambio)
        return d["total"] - a["total"]

    if diferenca(0.0) > 0:
        return 0.0          # agêntico já ganha sem nenhuma mudança
    if diferenca(365.0) < 0:
        return None         # determinístico ganha mesmo com mudança diária

    lo, hi = 0.0, 365.0
    for _ in range(80):
        meio = (lo + hi) / 2
        if diferenca(meio) < 0:
            lo = meio
        else:
            hi = meio
    return (lo + hi) / 2


# ------------------------------------------------------------ relatório

def secao(t: str) -> None:
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


def relatorio(medidas: dict, hora: float, conserto_h: float,
              contar_perda: bool, latencia: float = 1.0,
              exportar: bool = True) -> None:
    custo_conserto = hora * conserto_h

    secao("1. PARÂMETROS")
    print("  MEDIDOS neste experimento (execuções que contornaram a interface ficam fora):")
    print(f"  {'abordagem':<16}{'quebra cosm.':>13}{'estrut.':>9}{'semânt.':>9}"
          f"{'US$ exec. OK':>14}{'US$ exec. falha':>17}")
    print("  " + "-" * 78)
    for b in ORDEM:
        if b not in medidas:
            continue
        m = medidas[b]
        q = m["p_quebra_tipo"]
        print(f"  {NOME[b]:<16}{100*q.get('cosmetico', 0):>12.1f}%{100*q.get('estrutural', 0):>8.1f}%"
              f"{100*q.get('semantico', 0):>8.1f}%{m['custo_ok_usd']:>14.4f}{m['custo_falha_usd']:>17.4f}")
    print("\n  O teto de US$ 0,60 por execução incide sobre a estimativa do SDK: é o limite")
    print("  escolhido, não uma propriedade do agente. Pelo preço público, a execução")
    print("  interrompida no teto custa menos que US$ 0,60.")
    print("\n  PREMISSAS declaradas:")
    print(f"    tempo de intervenção após uma quebra       {conserto_h:.2f} h")
    print(f"    valor da hora de manutenção                R$ {hora:.2f}")
    print(f"    custo de uma intervenção                   R$ {custo_conserto:.2f}")
    print(f"    câmbio                                     R$ {CAMBIO:.2f} / US$ (premissa)")
    print(f"    latência até detectar a quebra             {latencia:.1f} dia(s) útil(eis)")
    print(f"    custo de parada contabilizado              {'sim' if contar_perda else 'não'}")
    print("    custo de intervenção igual para todas as abordagens (não medido)")

    cenarios = [
        ("baixo volume", DIAS_UTEIS * 1),
        ("volume médio", DIAS_UTEIS * 20),
        ("alto volume", DIAS_UTEIS * 200),
    ]
    frequencias = (2, 12)
    linhas_export = []

    for tipo in TIPOS:
        secao(f"2. CUSTO ANUAL POR CENÁRIO — MUDANÇAS DO TIPO {NOME_TIPO[tipo].upper()}")
        print("Valores em reais por ano.\n")
        for rotulo, execs in cenarios:
            for mudancas in frequencias:
                print(f"  {rotulo.upper()} — {execs:,} execuções/ano, {mudancas} mudanças/ano"
                      .replace(",", "."))
                print(f"  {'abordagem':<16}{'operação':>11}{'manutenção':>12}"
                      f"{'desperdício':>13}{'parada':>10}{'TOTAL':>13}")
                print("  " + "-" * 75)
                totais = {}
                for b in ORDEM:
                    if b not in medidas:
                        continue
                    c = custo_anual(medidas, b, execs, mudancas, custo_conserto,
                                    contar_perda, latencia, tipo)
                    totais[b] = c["total"]
                    print(f"  {NOME[b]:<16}{c['operacao']:>11,.0f}{c['manutencao']:>12,.0f}"
                          f"{c['desperdicio']:>13,.0f}{c['perda']:>10,.0f}{c['total']:>13,.0f}"
                          .replace(",", "."))
                    linhas_export.append({
                        "tipo_mudanca": tipo, "cenario": rotulo, "execucoes_ano": execs,
                        "mudancas_ano": mudancas, "abordagem": NOME[b],
                        "operacao_brl": f"{c['operacao']:.2f}",
                        "manutencao_brl": f"{c['manutencao']:.2f}",
                        "desperdicio_brl": f"{c['desperdicio']:.2f}",
                        "parada_brl": f"{c['perda']:.2f}", "total_brl": f"{c['total']:.2f}"})
                menor = min(totais.values())
                empatadas = [NOME[b] for b, v in totais.items() if abs(v - menor) < 0.5]
                outras = sorted(v for v in totais.values() if abs(v - menor) >= 0.5)
                folga = (outras[0] - menor) if outras else 0.0
                print(f"  -> mais barata(s): {', '.join(empatadas).upper()} "
                      f"(diferença para a próxima: R$ {folga:,.0f} por ano)\n".replace(",", "."))

    secao("3. PONTO DE EQUILÍBRIO CONTRA O SELF-HEALING, POR TIPO DE MUDANÇA")
    print("Mudanças por ano a partir das quais a agêntica passa a custar menos.\n")
    print(f"  {'volume anual':<22}" + "".join(f"{NOME_TIPO[t]:>14}" for t in TIPOS))
    print("  " + "-" * 64)
    for rotulo, execs in [("1 vez ao dia", DIAS_UTEIS), ("20 vezes ao dia", DIAS_UTEIS * 20),
                          ("200 vezes ao dia", DIAS_UTEIS * 200)]:
        textos = []
        for tipo in TIPOS:
            pe = ponto_de_equilibrio(medidas, "healing", execs, custo_conserto,
                                     contar_perda, latencia, tipo)
            textos.append("nunca" if pe is None else ("sempre" if pe <= 0 else f"{pe:.1f}/ano"))
        print(f"  {rotulo:<22}" + "".join(f"{t:>14}" for t in textos))
    print("\n  'nunca' = o self-healing custa menos mesmo com uma mudança por dia útil.")

    secao("4. SENSIBILIDADE — mudanças semânticas, volume de 1 execução por dia")
    for h in (100.0, 200.0, 300.0):
        for fator in (0.9, 1.0, 1.1):
            pe = ponto_de_equilibrio(medidas, "healing", DIAS_UTEIS, h * conserto_h,
                                     contar_perda, latencia, "semantico", CAMBIO * fator)
            texto = "nunca" if pe is None else ("sempre" if pe <= 0 else f"{pe:.1f} mudanças/ano")
            print(f"  hora R$ {h:>5.0f}   câmbio R$ {CAMBIO*fator:.2f}   ->  equilíbrio: {texto}")

    if exportar and linhas_export:
        os.makedirs(SAIDA, exist_ok=True)
        destino = os.path.join(SAIDA, "modelo_economico.csv")
        with open(destino, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(linhas_export[0].keys()))
            w.writeheader()
            w.writerows(linhas_export)
        print(f"\n\nTabela exportada para: {destino}")


# ------------------------------------------------------------ autoteste

def autoteste() -> None:
    ok = True

    def checa(nome, obtido, esperado, tol=1e-6):
        nonlocal ok
        bom = abs(obtido - esperado) < tol
        ok &= bom
        print(f"  [{'OK ' if bom else 'ERRO'}] {nome:<52} "
              f"obtido {obtido:>11.4f}  esperado {esperado:>11.4f}")

    print("AUTOTESTE — contas do modelo econômico\n")

    # Medidas sintéticas, escolhidas para a conta fechar na mão:
    #   determinístico  quebra em 50%; execução custa zero, falhe ou não
    #   agêntico        quebra em 10%; OK custa R$ 1,00 e falha custa R$ 5,00
    m = {
        "deterministic": {"p_quebra": 0.50, "custo_ok_usd": 0.0,
                          "custo_falha_usd": 0.0, "n": 100,
                          "p_quebra_tipo": {"semantico": 0.50, "cosmetico": 0.0}},
        "agentic": {"p_quebra": 0.10, "custo_ok_usd": 1.0 / CAMBIO,
                    "custo_falha_usd": 5.0 / CAMBIO, "n": 100,
                    "p_quebra_tipo": {"semantico": 0.10, "cosmetico": 0.0}},
    }
    # 2.520 execuções/ano = exatamente 10 por dia útil, o que torna a janela
    # de exposição um número redondo.
    N = DIAS_UTEIS * 10
    LAT = 5.0                      # janela = 10/dia x 5 dias = 50 execuções

    # --- janela de exposição -------------------------------------------------
    # É o menor entre (execuções até detectar) e (metade do intervalo entre
    # mudanças). Com f=10, o intervalo dá 126 e a latência dá 50: manda a
    # latência.
    d = custo_anual(m, "deterministic", N, 10, 150.0, latencia_dias=LAT)
    checa("degradadas — limitadas pela latência", d["degradadas"], 5 * 50.0)
    # Com latência absurda, o teto passa a ser o intervalo: (2520/10)/2 = 126.
    d_teto = custo_anual(m, "deterministic", N, 10, 150.0, latencia_dias=100.0)
    checa("degradadas — limitadas pelo intervalo", d_teto["degradadas"], 5 * 126.0)

    # A correção que motivou o parâmetro: sem ele, o custo saltava de zero para
    # o máximo assim que a frequência deixava de ser exatamente zero. Agora a
    # transição é suave.
    quase_zero = custo_anual(m, "deterministic", N, 0.1, 150.0, latencia_dias=LAT)
    checa("sem descontinuidade perto de zero",
          quase_zero["degradadas"], 0.1 * 0.5 * 50.0)

    # --- agêntico, f=10 ------------------------------------------------------
    #   quebras     = 10 x 0,10 = 1
    #   degradadas  = 1 x 50 = 50
    #   operação    = (2520 - 50) x R$ 1,00 = R$ 2.470
    #   manutenção  = 1 x R$ 150 = R$ 150
    #   desperdício = 50 x R$ 5,00 = R$ 250
    #   parada      = 50 x R$ 1,00 = R$ 50
    #   TOTAL       = R$ 2.920
    a = custo_anual(m, "agentic", N, 10, 150.0, latencia_dias=LAT)
    checa("agêntico — quebras", a["quebras"], 1.0)
    checa("agêntico — degradadas", a["degradadas"], 50.0)
    checa("agêntico — operação", a["operacao"], 2470.0)
    checa("agêntico — manutenção NÃO é zero", a["manutencao"], 150.0)
    checa("agêntico — desperdício ao falhar caro", a["desperdicio"], 250.0)
    checa("agêntico — parada", a["perda"], 50.0)
    checa("agêntico — total", a["total"], 2920.0)

    # --- determinístico, f=10 ------------------------------------------------
    #   quebras     = 5;  degradadas = 250
    #   operação    = 2270 x R$ 0 = R$ 0
    #   manutenção  = 5 x R$ 150 = R$ 750
    #   desperdício = 250 x R$ 0 = R$ 0    (timeout não consome nada)
    #   parada      = 250 x R$ 1,00 = R$ 250
    #   TOTAL       = R$ 1.000
    checa("determinístico — quebras", d["quebras"], 5.0)
    checa("determinístico — operação é zero", d["operacao"], 0.0)
    checa("determinístico — manutenção", d["manutencao"], 750.0)
    checa("determinístico — desperdício é zero", d["desperdicio"], 0.0)
    checa("determinístico — parada", d["perda"], 250.0)
    checa("determinístico — total", d["total"], 1000.0)

    # --- sem contar parada ---------------------------------------------------
    a2 = custo_anual(m, "agentic", N, 10, 150.0, contar_perda=False, latencia_dias=LAT)
    d2 = custo_anual(m, "deterministic", N, 10, 150.0, contar_perda=False, latencia_dias=LAT)
    checa("agêntico sem parada", a2["total"], 2870.0)
    checa("determinístico sem parada", d2["total"], 750.0)

    # --- sem mudanças --------------------------------------------------------
    a3 = custo_anual(m, "agentic", N, 0, 150.0, latencia_dias=LAT)
    d3 = custo_anual(m, "deterministic", N, 0, 150.0, latencia_dias=LAT)
    checa("zero mudanças — determinístico custa zero", d3["total"], 0.0)
    checa("zero mudanças — agêntico paga a operação", a3["total"], float(N))

    # --- ponto de equilíbrio -------------------------------------------------
    # Com latência de 0,5 dia, a janela é 5 execuções e a latência manda em
    # toda a faixa de busca. Então:
    #   det(f) = 75f + (f x 0,5 x 5) x 1 = 77,5f
    #   ag(f)  = (2520 - 0,5f) + 15f + (0,5f x 5) + 0,5f = 2.520 + 17,5f
    #   77,5f = 2.520 + 17,5f  ->  f = 42
    pe = ponto_de_equilibrio(m, "deterministic", N, 150.0, latencia_dias=0.5)
    checa("ponto de equilíbrio", pe, 42.0, tol=1e-3)

    # --- monotonicidade ------------------------------------------------------
    for braco in ("deterministic", "agentic"):
        anterior, monotono = -1.0, True
        for f in range(0, 60, 5):
            t = custo_anual(m, braco, N, f, 150.0, latencia_dias=LAT)["total"]
            if t < anterior - 1e-9:
                monotono = False
            anterior = t
        checa(f"custo de {braco} cresce com as mudanças",
              0.0 if monotono else 1.0, 0.0)

    # --- tipo de mudança -----------------------------------------------------
    # Com mudanças cosméticas nenhuma das duas quebra: o determinístico custa
    # zero e a agêntica só paga a operação (2.520 x R$ 1,00).
    d4 = custo_anual(m, "deterministic", N, 10, 150.0, latencia_dias=LAT, tipo_mudanca="cosmetico")
    a4 = custo_anual(m, "agentic", N, 10, 150.0, latencia_dias=LAT, tipo_mudanca="cosmetico")
    checa("cosmética — determinístico sem quebra custa zero", d4["total"], 0.0)
    checa("cosmética — agêntica paga só a operação", a4["total"], float(N))

    print("\n" + ("TODOS OS TESTES PASSARAM." if ok else "HÁ FALHAS ACIMA."))


def main() -> None:
    p = argparse.ArgumentParser(description="Ponto de equilíbrio econômico")
    p.add_argument("--csv", default=ENTRADA)
    p.add_argument("--hora", type=float, default=250.0,
                   help="valor da hora de manutenção em reais (padrão: 250)")
    p.add_argument("--conserto", type=float, default=0.5,
                   help="horas para consertar um seletor (padrão: 0,5)")
    p.add_argument("--latencia", type=float, default=1.0,
                   help="dias úteis até perceber que quebrou (padrão: 1)")
    p.add_argument("--com-perda", action="store_true",
                   help="soma o custo das execuções perdidas na parada (fora do modelo do artigo)")
    p.add_argument("--mediana", action="store_true",
                   help="custo por execução pela mediana, e não pela média")
    p.add_argument("--base-custo", choices=("publico", "sdk"), default="publico",
                   help="custo agêntico pelo preço público (padrão) ou pela estimativa do SDK")
    p.add_argument("--transcricoes", default=DIR_TRANSCRICOES)
    p.add_argument("--autoteste", action="store_true")
    args = p.parse_args()

    if args.autoteste:
        autoteste()
        return

    medidas = medir(args.csv, args.base_custo, args.transcricoes,
                    estatistica="mediana" if args.mediana else "media")
    print(f"Medidas extraídas de {args.csv}")
    print("Custo agêntico: " + ("preço público, recalculado pelos tokens das transcrições"
                                if args.base_custo == "publico" else "estimativa gravada pelo SDK"))
    print("Custo por execução: " + ("mediana" if args.mediana else "média") + " das execuções concluídas")
    relatorio(medidas, args.hora, args.conserto, args.com_perda, args.latencia)


if __name__ == "__main__":
    main()
