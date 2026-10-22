"""
Auditoria do conjunto de dados — verifica se os resultados são confiáveis
ANTES de a redação começar a apoiar-se neles.

    python auditoria.py

Cada verificação abaixo existe porque a ausência dela permitiria que uma
conclusão errada sobrevivesse até a defesa. O critério é sempre o mesmo: um
defeito que se manifesta como número plausível é mais perigoso que um que
quebra a execução, porque não pede para ser investigado.

O script não corrige nada. Ele apenas relata, classificando cada achado em:
    ERRO    — invalida análise; precisa ser resolvido antes de escrever.
    ALERTA  — não invalida, mas precisa ser explicado no texto.
    OK      — verificado.
"""

from __future__ import annotations

import csv
import json
import os
import statistics
from collections import Counter, defaultdict

RAIZ = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(RAIZ, "data", "results.csv")
TRANSCRICOES = os.path.join(RAIZ, "data", "transcripts")

BRACOS_SEM_API = ["deterministic", "xpath", "semantic", "healing"]
TODOS_BRACOS = BRACOS_SEM_API + ["agentic"]
TIPOS = ["cosmetico", "estrutural", "semantico"]
SITES = ["saucedemo", "the_internet"]

COLUNAS = [
    "site", "task", "approach", "drift_level", "repetition", "success",
    "duration_s", "n_steps", "drift_type", "error_type", "error_message",
    "tokens_input", "tokens_output", "cost_usd",
    "heal_acionamentos", "heal_curados", "heal_desistencias",
    "heal_corretos", "heal_errados", "heal_indeterminados",
    "drift_ids", "drift_classes", "drift_textos", "drift_envolvidos",
    "drift_iscas", "timestamp", "coleta", "versao_instrumento",
]

achados: list[tuple[str, str, str]] = []


def registrar(nivel: str, verificacao: str, detalhe: str) -> None:
    achados.append((nivel, verificacao, detalhe))


def ok(verificacao: str, detalhe: str) -> None:
    registrar("OK", verificacao, detalhe)


def carregar() -> list[dict]:
    with open(CSV, encoding="utf-8") as f:
        leitor = csv.DictReader(f)
        cabecalho = leitor.fieldnames or []
        linhas = list(leitor)

    if cabecalho != COLUNAS:
        registrar("ERRO", "Cabeçalho do CSV",
                  f"esperado {COLUNAS}, encontrado {cabecalho}")
    else:
        ok("Cabeçalho do CSV", f"{len(COLUNAS)} colunas na ordem esperada")

    for r in linhas:
        r["ok"] = r["success"].strip().lower() == "true"
        r["nivel"] = float(r["drift_level"])
        r["rep"] = int(r["repetition"])
        r["dur"] = float(r["duration_s"])
        r["custo"] = float(r["cost_usd"] or 0)
        r["passos"] = int(r["n_steps"] or 0)
        r["tin"] = int(r["tokens_input"] or 0)
        r["tout"] = int(r["tokens_output"] or 0)
    return linhas


# ----------------------------------------------------------------- verificações

REPETICOES_ESPERADAS = 8          # braços sem API; ajustável por --repeticoes
REPETICOES_AGENTICO = 8           # ajustável por --repeticoes-agentico
NIVEIS_AGENTICO = (0.0, 0.25, 0.5, 0.75, 1.0)  # ajustável por --niveis-agentico


def v_completude(linhas) -> None:
    """
    Toda célula do desenho experimental precisa existir com o número exato de
    repetições. Uma célula faltando distorce a média do braço sem produzir
    nenhum sinal visível na análise.
    """
    contagem = Counter((r["site"], r["approach"], r["drift_type"], r["nivel"])
                       for r in linhas)

    esperado = {}
    for site in SITES:
        for tipo in TIPOS:
            for braco in BRACOS_SEM_API:
                for nivel in (0.0, 0.25, 0.5, 0.75, 1.0):
                    esperado[(site, braco, tipo, nivel)] = REPETICOES_ESPERADAS
            for nivel in NIVEIS_AGENTICO:
                esperado[(site, "agentic", tipo, nivel)] = REPETICOES_AGENTICO

    faltando = [(k, v) for k, v in esperado.items() if contagem.get(k, 0) != v]
    sobrando = [k for k in contagem if k not in esperado]

    # Coleta em andamento não é defeito. Se TODAS as células ausentes forem de
    # um mesmo braço, o mais provável é que ele ainda não tenha sido coletado —
    # situação normal entre as duas fases da coleta. Reportar como erro nesse
    # caso treinaria o leitor a ignorar o veredito, que é o pior efeito
    # possível para um verificador.
    bracos_ausentes = {k[1] for k, _ in faltando if contagem.get(k, 0) == 0}
    if faltando and all(contagem.get(k, 0) == 0 for k, _ in faltando) \
            and len(bracos_ausentes) == 1:
        braco = bracos_ausentes.pop()
        registrar("ALERTA", "Completude do desenho",
                  f"o braço '{braco}' ainda não foi coletado "
                  f"({len(faltando)} células vazias). As demais estão completas. "
                  "Rode a auditoria de novo ao final da coleta.")
        return

    if faltando:
        registrar("ERRO", "Completude do desenho",
                  f"{len(faltando)} célula(s) com n diferente do previsto: "
                  + "; ".join(f"{k} tem {contagem.get(k,0)}, esperado {v}"
                              for k, v in faltando[:5]))
    elif sobrando:
        registrar("ERRO", "Completude do desenho",
                  f"células não previstas no desenho: {sobrando[:5]}")
    else:
        registrar("OK", "Completude do desenho",
                  f"{len(esperado)} células, todas com o n previsto "
                  f"({REPETICOES_ESPERADAS} sem API, {REPETICOES_AGENTICO} agênticas) — total {len(linhas)}")


def v_duplicatas(linhas) -> None:
    """
    A chave (site, braço, tipo, nível, repetição) identifica uma execução. Uma
    duplicata significa que a mesma condição foi coletada duas vezes — em geral
    porque o --resume não reconheceu o que já existia. O efeito é dar peso duplo
    a uma observação.
    """
    chaves = Counter((r["site"], r["approach"], r["drift_type"], r["nivel"], r["rep"])
                     for r in linhas)
    dup = {k: v for k, v in chaves.items() if v > 1}
    if dup:
        registrar("ERRO", "Duplicatas",
                  f"{len(dup)} chave(s) repetida(s): {list(dup.items())[:5]}")
    else:
        ok("Duplicatas", f"{len(chaves)} chaves únicas, nenhuma repetida")

    # as repetições precisam ser 0..n-1, sem buracos: um buraco indica que uma
    # execução foi perdida e outra coletada duas vezes com números diferentes
    por_celula = defaultdict(set)
    for r in linhas:
        por_celula[(r["site"], r["approach"], r["drift_type"], r["nivel"])].add(r["rep"])
    irregulares = {k: sorted(v) for k, v in por_celula.items()
                   if sorted(v) != list(range(len(v)))}
    if irregulares:
        registrar("ERRO", "Numeração das repetições",
                  f"células com repetições não contíguas: {list(irregulares.items())[:3]}")
    else:
        ok("Numeração das repetições", "todas as células têm repetições 0..n-1")


def v_linha_de_base(linhas) -> None:
    """
    Sem drift, toda abordagem deve concluir a tarefa. Se falhar aqui, a falha
    não vem da degradação — vem do site, da rede ou do próprio script. Qualquer
    falha nesta condição contamina a comparação, porque seria atribuída ao
    tratamento.
    """
    base = [r for r in linhas if r["nivel"] == 0.0]
    falhas = [r for r in base if not r["ok"]]
    if falhas:
        detalhe = "; ".join(f"{r['site']}/{r['approach']}/rep{r['rep']}: "
                            f"{r['error_type']}" for r in falhas[:5])
        registrar("ERRO", "Linha de base (drift 0)",
                  f"{len(falhas)} de {len(base)} falharam SEM drift: {detalhe}")
    else:
        ok("Linha de base (drift 0)",
           f"{len(base)}/{len(base)} sucesso — o instrumento não introduz falha própria")


def v_coerencia_colunas(linhas) -> None:
    """
    Um sucesso não pode ter tipo de erro preenchido, e uma falha não pode estar
    sem ele. Incoerência aqui costuma indicar desalinhamento de colunas no CSV
    — defeito que já ocorreu neste projeto quando o cabeçalho mudou e as linhas
    novas passaram a gravar valores em campos deslocados.
    """
    problemas = []
    for r in linhas:
        if r["ok"] and r["error_type"]:
            problemas.append(f"sucesso com error_type='{r['error_type']}'")
        if not r["ok"] and not r["error_type"]:
            problemas.append(f"falha sem error_type ({r['site']}/{r['approach']})")

    tipos_validos = {"", "timeout", "assertion_failed", "exception",
                     "max_steps", "max_budget", "contornou"}
    invalidos = {r["error_type"] for r in linhas} - tipos_validos
    if invalidos:
        problemas.append(f"error_type fora do domínio: {invalidos}")

    fora = {r["drift_type"] for r in linhas} - set(TIPOS)
    if fora:
        problemas.append(f"drift_type fora do domínio: {fora}")

    if problemas:
        registrar("ERRO", "Coerência entre colunas",
                  f"{len(problemas)} problema(s): {problemas[:4]}")
    else:
        ok("Coerência entre colunas",
           "sucesso/erro consistentes; drift_type e error_type dentro do domínio")


def v_custo_por_braco(linhas) -> None:
    """
    Os braços sem API precisam ter custo e tokens exatamente zero, e o agêntico
    precisa ter ambos positivos. Custo zero numa execução agêntica é a assinatura
    do erro de infraestrutura que já contaminou este conjunto uma vez.
    """
    problemas = []
    for braco in BRACOS_SEM_API:
        sub = [r for r in linhas if r["approach"] == braco]
        sujas = [r for r in sub if r["custo"] or r["tin"] or r["tout"]]
        if sujas:
            problemas.append(f"{braco}: {len(sujas)} execuções com custo/tokens não nulos")

    ag = [r for r in linhas if r["approach"] == "agentic"]
    sem_custo = [r for r in ag if r["custo"] <= 0]
    sem_tokens = [r for r in ag if r["tin"] <= 0]
    if sem_custo:
        problemas.append(f"agentic: {len(sem_custo)} execuções com custo zero "
                         "(assinatura de erro de infraestrutura)")
    if sem_tokens:
        problemas.append(f"agentic: {len(sem_tokens)} execuções sem tokens de entrada")

    if problemas:
        registrar("ERRO", "Custo e tokens por braço", "; ".join(problemas))
    else:
        ok("Custo e tokens por braço",
           f"braços sem API com custo zero; {len(ag)} agênticas todas com custo e tokens > 0")


def v_contadores_healing(linhas) -> None:
    """Só o braço self-healing pode ter contadores de cura preenchidos."""
    problemas = []
    for braco in TODOS_BRACOS:
        if braco == "healing":
            continue
        sub = [r for r in linhas if r["approach"] == braco]
        sujas = [r for r in sub
                 if any(int(r.get(c) or 0) for c in
                        ("heal_acionamentos", "heal_curados", "heal_desistencias",
                         "heal_corretos", "heal_errados", "heal_indeterminados"))]
        if sujas:
            problemas.append(f"{braco}: {len(sujas)} linhas com contador de healing")

    heal = [r for r in linhas if r["approach"] == "healing"]
    incoerentes = [r for r in heal
                   if int(r["heal_curados"] or 0) + int(r["heal_desistencias"] or 0)
                   > int(r["heal_acionamentos"] or 0)]
    if incoerentes:
        problemas.append(f"healing: {len(incoerentes)} linhas onde curas+desistências "
                         "excede acionamentos")

    # Coleta 2: toda cura precisa ter veredito (correta, errada ou sem verdade
    # de referência). Soma diferente de curados significa que a medição direta
    # deixou de ser feita em alguma cura.
    sem_veredito = [r for r in heal
                    if int(r.get("heal_corretos") or 0) + int(r.get("heal_errados") or 0)
                    + int(r.get("heal_indeterminados") or 0) != int(r["heal_curados"] or 0)]
    if sem_veredito:
        problemas.append(f"healing: {len(sem_veredito)} linhas em que corretos+errados+"
                         "indeterminados difere de curados")

    com_drift = [r for r in heal if r["nivel"] > 0]
    sem_acionar = [r for r in com_drift if not int(r["heal_acionamentos"] or 0)]
    if len(sem_acionar) == len(com_drift) and com_drift:
        problemas.append("healing: nenhum acionamento registrado sob drift — "
                         "o mecanismo pode não estar sendo exercitado")

    if problemas:
        registrar("ERRO", "Contadores do self-healing", "; ".join(problemas))
    else:
        ok("Contadores do self-healing",
           f"exclusivos do braço healing e internamente coerentes "
           f"({len(com_drift)-len(sem_acionar)} de {len(com_drift)} execuções acionaram)")


def v_assinatura_infraestrutura(linhas) -> None:
    """
    Procura o padrão que contaminou a coleta anterior: falhas agênticas rápidas
    demais e sem custo. Uma falha genuína consome passos antes de desistir; uma
    recusa do provedor não consome nada. A verificação é estatística porque a
    mensagem de erro nem sempre revela a causa.
    """
    ag = [r for r in linhas if r["approach"] == "agentic"]
    falhas = [r for r in ag if not r["ok"]]
    if not falhas:
        ok("Assinatura de erro de infraestrutura",
           f"nenhuma falha entre as {len(ag)} execuções agênticas — "
           "o padrão não pode ocorrer")
        return

    suspeitas = [r for r in falhas if r["custo"] <= 0 or r["passos"] <= 1]
    if suspeitas:
        registrar("ERRO", "Assinatura de erro de infraestrutura",
                  f"{len(suspeitas)} falha(s) com custo zero ou nenhum passo — "
                  "provavelmente recusa do provedor, não falha do agente")
    else:
        ok("Assinatura de erro de infraestrutura",
           f"as {len(falhas)} falhas agênticas consumiram passos e custo — "
           "são falhas reais")


def v_duracoes(linhas) -> None:
    """
    Duração fora da distribuição indica execução que não seguiu o caminho
    esperado: um sucesso rápido demais pode ter pulado etapas; uma falha rápida
    demais pode não ter chegado a tentar.
    """
    # O agrupamento precisa incluir o SITE e o TIPO DE ERRO. As duas tarefas têm
    # comprimentos diferentes, e uma falha por timeout dura dez segundos enquanto
    # uma falha por asserção é instantânea. Misturar esses casos produz uma
    # distribuição multimodal em que a mediana não representa nada, e a
    # verificação passa a acusar como anomalia a própria estrutura do desenho.
    # O agrupamento inclui ainda ONDE a execução parou. Uma falha logo na
    # primeira interação e uma falha após o login gastam tempos diferentes por
    # construção — a segunda carrega o custo de tudo que deu certo antes. Sem
    # separar os dois casos, a verificação acusa como anomalia a diferença
    # esperada entre pontos de parada.
    def onde_parou(r) -> str:
        msg = r.get("error_message") or ""
        return msg.split("'")[1] if msg.startswith("[apos '") else ""

    grupos = defaultdict(list)
    for r in linhas:
        grupos[(r["approach"], r["site"], r["ok"],
                r["error_type"], onde_parou(r))].append(r["dur"])

    alertas = []
    for (braco, site, sucesso, erro, parada), valores in sorted(grupos.items()):
        if len(valores) < 8:
            continue
        med = statistics.median(valores)
        # desvio absoluto mediano: robusto a outliers, ao contrário do
        # desvio-padrão, que é inflado pelos próprios pontos que se procura
        mad = statistics.median([abs(x - med) for x in valores]) or 0.001
        # Exigir TAMBÉM desvio absoluto relevante. As falhas por timeout se
        # concentram no mesmo valor por construção, então o MAD fica próximo de
        # zero e qualquer variação de décimos de segundo excede 10 MAD. Sem este
        # segundo critério a verificação acusa como anomalia a própria precisão
        # do instrumento.
        extremos = [x for x in valores
                    if abs(x - med) / mad > 10 and abs(x - med) > max(2.0, 0.25 * med)]
        if extremos:
            rotulo = ("sucesso" if sucesso
                      else f"falha/{erro}" + (f" apos {parada}" if parada else ""))
            alertas.append(f"{braco}/{site}/{rotulo}: {len(extremos)} de "
                           f"{len(valores)} fora do padrão (mediana {med:.2f}s, "
                           f"extremos {min(extremos):.2f}–{max(extremos):.2f}s)")
    if alertas:
        registrar("ALERTA", "Distribuição das durações", "; ".join(alertas))
    else:
        ok("Distribuição das durações",
           f"{len(grupos)} grupos homogêneos (braço x site x desfecho x tipo de "
           "erro); nenhum extremo além de 10 MAD")


def v_monotonicidade(linhas) -> None:
    """
    A taxa de sucesso não deve SUBIR conforme o drift aumenta. Se subir, o
    mecanismo de degradação não é cumulativo como a metodologia afirma, e a
    variável independente não significa o que o texto diz que significa.
    """
    violacoes = []
    for braco in TODOS_BRACOS:
        for tipo in TIPOS:
            niveis = sorted({r["nivel"] for r in linhas
                             if r["approach"] == braco and r["drift_type"] == tipo})
            taxas = []
            for n in niveis:
                sub = [r for r in linhas if r["approach"] == braco
                       and r["drift_type"] == tipo and r["nivel"] == n]
                taxas.append(sum(r["ok"] for r in sub) / len(sub))
            for i in range(len(taxas) - 1):
                # 1/8 = 0.125 é uma observação; exige-se mais que isso para
                # não acusar ruído amostral como violação
                if taxas[i + 1] - taxas[i] > 0.125:
                    violacoes.append(
                        f"{braco}/{tipo}: {100*taxas[i]:.0f}% no nível {niveis[i]} "
                        f"sobe para {100*taxas[i+1]:.0f}% no nível {niveis[i+1]}")
    if violacoes:
        registrar("ALERTA", "Monotonicidade do drift", "; ".join(violacoes))
    else:
        ok("Monotonicidade do drift",
           "nenhuma abordagem melhora quando a degradação aumenta")


def v_colapso_de_pagina(linhas) -> None:
    """
    Procura falhas causadas pela PÁGINA quebrar, e não pela localização falhar.

    Esta verificação não existia e por isso o defeito passou: o drift estrutural
    movia nós administrados pelo React, a aplicação desmontava ao primeiro
    preenchimento, e o self-healing registrava 'melhor pontuação 0.000' ao
    procurar o campo seguinte. A mensagem sugere limitação do mecanismo de
    similaridade; a causa era não haver mais página onde procurar.

    A assinatura é específica: `melhor_candidato` devolve exatamente 0.000
    apenas quando NENHUM candidato compatível existe. Num formulário íntegro
    sempre há ao menos um campo de texto a pontuar, ainda que errado. Pontuação
    zero, portanto, quase sempre significa página vazia.
    """
    suspeitas = [r for r in linhas
                 if not r["ok"] and "pontuação 0.000" in (r["error_message"] or "")]
    if not suspeitas:
        ok("Colapso de página", "nenhuma falha com a assinatura de página vazia")
        return

    por_condicao = Counter((r["site"], r["drift_type"]) for r in suspeitas)
    detalhe = "; ".join(f"{s}/{t}: {n}" for (s, t), n in por_condicao.most_common(4))
    registrar("ERRO", "Colapso de página",
              f"{len(suspeitas)} falha(s) com 'melhor pontuação 0.000' — o "
              f"mecanismo não encontrou NENHUM candidato compatível, o que "
              f"indica página desmontada e não falha de localização ({detalhe})")


def v_drift_efetivo(linhas) -> None:
    """
    Confere, pelos números gravados, que o drift REALMENTE fez o que diz fazer.

    Esta é a verificação que faltava e que teria evitado uma coleta inteira
    perdida. A condição estrutural passou 720 execuções sem produzir efeito
    algum além do cosmético — resultado idêntico em 191 de 192 pares — e o
    arquivo não guardava nada que denunciasse isso, porque registrava apenas o
    tipo e o nível PEDIDOS.

    Com as contagens do que foi de fato mutado, a diferença entre as condições
    passa a ser auditável em segundos.
    """
    com_drift = [r for r in linhas if r["nivel"] > 0]
    if not com_drift or "drift_iscas" not in com_drift[0]:
        registrar("ALERTA", "Efetividade do drift",
                  "o arquivo não tem as colunas de estatística do drift — "
                  "conjunto coletado antes da instrumentação")
        return

    def soma(tipo, campo):
        return sum(int(r.get(campo) or 0) for r in com_drift
                   if r["drift_type"] == tipo)

    problemas = []
    # cosmético renomeia identidade e NADA MAIS
    if soma("cosmetico", "drift_ids") + soma("cosmetico", "drift_classes") == 0:
        problemas.append("cosmético não renomeou nada")
    for campo, rotulo in (("drift_iscas", "iscas"), ("drift_textos", "textos")):
        if soma("cosmetico", campo) > 0:
            problemas.append(f"cosmético inseriu {rotulo}, o que pertence a "
                             "outro tipo — as condições não são distintas")
    # estrutural precisa inserir irmãos; é o que desloca o caminho no DOM
    if soma("estrutural", "drift_iscas") == 0:
        problemas.append("estrutural não inseriu nenhuma isca — a condição "
                         "é indistinguível da cosmética")
    # semântico precisa alterar textos
    if soma("semantico", "drift_textos") == 0:
        problemas.append("semântico não alterou nenhum texto")

    if problemas:
        registrar("ERRO", "Efetividade do drift", "; ".join(problemas))
    else:
        ok("Efetividade do drift",
           f"cosmético renomeia sem inserir; estrutural insere "
           f"{soma('estrutural', 'drift_iscas')} iscas; semântico altera "
           f"{soma('semantico', 'drift_textos')} textos — as três condições "
           "se distinguem nos dados")


def v_falhas_de_navegacao(linhas) -> None:
    """
    Falha de acesso ao site não é resultado experimental, e agora vem marcada.
    """
    marcadas = [r for r in linhas
                if "[FALHA DE NAVEGACAO]" in (r["error_message"] or "")]
    if marcadas:
        registrar("ERRO", "Falhas de navegação",
                  f"{len(marcadas)} execução(ões) não conseguiram carregar a "
                  "página. São problema de rede, não efeito do drift, e "
                  "precisam ser recoletadas.")
    else:
        ok("Falhas de navegação", "nenhuma; todas as páginas carregaram")


def v_guarda_site(linhas) -> None:
    """
    Execuções que falharam com o site fora do ar não chegam ao CSV: a guarda
    (coleta_com_guarda.py) as descarta e a coleta as refaz. Ficam anotadas à
    parte, e o total precisa aparecer no texto junto das demais exclusões.
    """
    caminho = os.path.join(RAIZ, "data", "logs", "guarda_site.csv")
    descartadas = []
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            descartadas = list(csv.DictReader(f))
    if not descartadas:
        ok("Site fora do ar durante a coleta", "nenhuma execução descartada pela guarda")
        return
    por_site = Counter(r.get("site", "?") for r in descartadas)
    detalhe = "; ".join(f"{s}: {n}" for s, n in por_site.most_common())
    registrar("ALERTA", "Site fora do ar durante a coleta",
              f"{len(descartadas)} execução(ões) descartada(s) e refeita(s) com o site "
              f"fora do ar ({detalhe}). Relatar no texto junto das exclusões "
              "(data/logs/guarda_site.csv).")


def v_rotulo_de_erro(linhas) -> None:
    """
    Uma falha rotulada genericamente cuja mensagem revela causa específica.

    Aconteceu: duas execuções pararam por teto de custo, o SDK registrou o
    motivo corretamente, e um `except` genérico logo depois sobrescreveu-o com
    "error". No arquivo elas viraram `exception` — que se lê como "o agente
    travou" — em vez de `max_budget`, que é um limite imposto pelo experimento.
    A distinção muda a interpretação: uma é incapacidade, a outra é uma régua
    nossa.
    """
    PISTAS = {
        "teto de US$": "max_budget",
        "teto de passos": "max_steps",
        "session limit": "rate_limit",
        "429": "rate_limit",
    }
    # Infraestrutura que derruba o agente DEPOIS de ele já ter agido: o SDK
    # devolve erro genérico e a linha chega como `exception`. Login expirado ou
    # API sobrecarregada não dizem nada sobre robustez; a execução é refeita.
    LOGIN, API = "erro de login (infraestrutura)", "falha da API (infraestrutura)"
    PISTAS_AGENTE = {
        "failed to authenticate": LOGIN, "oauth session expired": LOGIN,
        "oauth token has expired": LOGIN, "please run /login": LOGIN,
        "not logged in": LOGIN, "authentication_error": LOGIN, "http 401": LOGIN,
        "api error": API, "overloaded": API, "request timed out": API,
        "connection error": API, "http 500": API, "http 502": API, "http 503": API,
        "http 504": API, "http 529": API,
    }
    suspeitas = []
    for r in linhas:
        if r["error_type"] not in ("exception", ""):
            continue
        msg = (r["error_message"] or "").lower()
        pistas = {**PISTAS, **PISTAS_AGENTE} if r["approach"] == "agentic" else PISTAS
        for pista, correto in pistas.items():
            if pista.lower() in msg:
                suspeitas.append(f"{r['site']}/{r['approach']}/{r['drift_type']} "
                                 f"rotulada '{r['error_type']}' mas a mensagem "
                                 f"indica '{correto}'")
                break
    if suspeitas:
        registrar("ERRO", "Rótulo do tipo de erro",
                  f"{len(suspeitas)} falha(s) com rótulo genérico e causa "
                  f"específica na mensagem: {suspeitas[:3]}")
    else:
        ok("Rótulo do tipo de erro",
           "nenhuma falha genérica esconde causa específica na mensagem")


def v_transcricoes(linhas) -> None:
    """
    Cada execução agêntica grava a transcrição do que o agente fez. Sem ela, o
    resultado não é auditável: não há como verificar depois se o sucesso veio do
    caminho esperado. A transcrição é também a matéria-prima da taxonomia de
    falhas prevista no cronograma.
    """
    ag = [r for r in linhas if r["approach"] == "agentic"]
    if not os.path.isdir(TRANSCRICOES):
        registrar("ERRO", "Transcrições do braço agêntico",
                  f"diretório {TRANSCRICOES} não existe")
        return

    arquivos = [f for f in os.listdir(TRANSCRICOES) if f.endswith(".json")]
    esperados = {f"{r['site']}_{r['task']}_{r['drift_type']}{r['nivel']}"
                 f"_rep{r['rep']}.json" for r in ag}
    faltando = esperados - set(arquivos)

    # Não basta o arquivo existir e ser legível: ele contém metadados de custo
    # ALÉM da lista de ações. Verificar apenas se o JSON não é vazio deixa
    # passar um arquivo cujo campo `transcript` está vazio — foi exatamente o
    # que aconteceu, e a primeira versão desta verificação aprovou 72 arquivos
    # sem uma única ação registrada.
    sem_acoes, ilegiveis = [], []
    for nome in sorted(esperados & set(arquivos)):
        caminho = os.path.join(TRANSCRICOES, nome)
        try:
            with open(caminho, encoding="utf-8") as f:
                dados = json.load(f)
            if not dados.get("transcript"):
                sem_acoes.append(nome)
        except Exception:
            ilegiveis.append(nome)

    problemas = []
    if faltando:
        problemas.append(f"{len(faltando)} transcrição(ões) faltando "
                         f"(ex.: {sorted(faltando)[:2]})")
    if ilegiveis:
        problemas.append(f"{len(ilegiveis)} ilegíveis")
    if sem_acoes:
        problemas.append(
            f"{len(sem_acoes)} de {len(esperados)} sem nenhuma ação registrada: "
            "o arquivo traz custo e tokens, mas o campo transcript está vazio. "
            "O backend do SDK não gravava as ações. Consequência: os resultados "
            "de sucesso continuam válidos (o oráculo é independente), mas não é "
            "possível auditar POR QUE caminho o agente chegou ao estado final, "
            "nem produzir a análise qualitativa das execuções")

    if problemas:
        registrar("ALERTA", "Transcrições do braço agêntico", "; ".join(problemas))
    else:
        ok("Transcrições do braço agêntico",
           f"{len(esperados)} execuções com ações registradas")

    # OBSERVAÇÃO: nomear a transcrição sem o número da execução faz com que uma
    # repetição da mesma condição sobrescreva a anterior. Só é seguro porque
    # cada condição foi coletada uma única vez — o que a verificação de
    # duplicatas confirma.
    if len(arquivos) < len(ag):
        registrar("ALERTA", "Transcrições do braço agêntico",
                  f"{len(arquivos)} arquivos para {len(ag)} execuções: nomes "
                  "colidem entre execuções da mesma condição")


def v_janela_de_coleta(linhas) -> None:
    """
    Toda linha precisa ter sido gerada pelo código que está no disco, e todas
    pela MESMA versão dele.

    Na coleta 1 a versão ficava num arquivo à parte (data/versao_instrumento.json)
    e não dizia de qual versão veio cada linha. O degradador chegou a ser
    alterado entre a coleta dos braços determinísticos e a do agêntico sem que
    nada no CSV o registrasse. Desde a coleta 2, cada linha grava a impressão
    digital do instrumento (common/versao.py), e esta verificação a compara com
    a do código atual.
    """
    import sys
    sys.path.insert(0, RAIZ)
    from common.versao import impressao_instrumento

    atual = impressao_instrumento()
    versoes = Counter(r.get("versao_instrumento", "") for r in linhas)
    coletas = Counter(r.get("coleta", "") for r in linhas)

    if len(versoes) > 1:
        registrar("ERRO", "Janela de coleta",
                  f"linhas de {len(versoes)} versões do instrumento misturadas: "
                  f"{dict(versoes)}. Uma análise só pode usar uma versão.")
    elif atual not in versoes:
        registrar("ERRO", "Janela de coleta",
                  f"os dados vieram da versão {next(iter(versoes))!r}, mas o código "
                  f"no disco é a versão {atual!r}. Ou o código mudou depois da "
                  "coleta (as execuções precisam ser refeitas), ou o código da "
                  "coleta precisa ser restaurado (git checkout).")
    elif len(coletas) > 1:
        registrar("ERRO", "Janela de coleta",
                  f"linhas de coletas diferentes misturadas: {dict(coletas)}")
    else:
        ok("Janela de coleta",
           f"todas as {len(linhas)} linhas vieram da versão {atual} do instrumento "
           f"(coleta {next(iter(coletas))}), a mesma do código atual")


def v_pareamento(linhas) -> None:
    """
    A repetição N sofre a MESMA mutação em todos os braços, porque a semente do
    gerador é o número da repetição. Isso permite comparação pareada. A
    verificação possível a partir do CSV é indireta: as células comparáveis
    precisam cobrir exatamente o mesmo conjunto de repetições.
    """
    problemas = []
    for site in SITES:
        for tipo in TIPOS:
            for nivel in (0.0, 0.25, 0.5, 0.75, 1.0):
                conjuntos = {}
                for braco in TODOS_BRACOS:
                    reps = {r["rep"] for r in linhas
                            if r["site"] == site and r["approach"] == braco
                            and r["drift_type"] == tipo and r["nivel"] == nivel}
                    if reps:
                        conjuntos[braco] = reps
                # Coleta 2: todos os braços têm as mesmas repetições, e a
                # análise principal é pareada (McNemar). Cada semente precisa
                # existir em todos os braços da célula.
                if conjuntos and len({frozenset(v) for v in conjuntos.values()}) > 1:
                    problemas.append(f"{site}/{tipo}/{nivel}: braços com conjuntos "
                                     "de repetições diferentes")
    if problemas:
        registrar("ALERTA", "Pareamento entre braços", "; ".join(problemas[:4]))
    else:
        ok("Pareamento entre braços",
           "todos os braços cobrem as mesmas sementes em todas as células — "
           "comparação pareada válida")


def v_backup(linhas) -> None:
    """
    Confere que a limpeza do incidente de limite de uso removeu apenas as linhas
    contaminadas, e nada além delas.
    """
    caminho = os.path.join(RAIZ, "data", "results_antes_limpeza.csv")
    if not os.path.exists(caminho):
        # Coleta 2: sem limpeza, não há backup a conferir. Se alguma linha for
        # removida, o backup precisa ser salvo com este nome antes da remoção.
        ok("Rastreabilidade da limpeza",
           "nenhuma limpeza registrada nesta coleta (sem data/results_antes_limpeza.csv)")
        return

    with open(caminho, encoding="utf-8") as f:
        antigas = list(csv.DictReader(f))

    # Distinguir LIMPEZA de RECOLETA.
    #
    # Esta verificação existe para conferir que a remoção pontual de linhas
    # contaminadas não levou dado válido junto. Ela só faz sentido quando o
    # conjunto atual descende do backup. Depois de uma recoleta completa —
    # quando o instrumento muda e tudo é refeito do zero — nenhuma linha antiga
    # sobrevive, e comparar as duas acusaria a recoleta inteira como remoção
    # indevida. Foi o que aconteceu: 757 linhas apontadas como suspeitas quando
    # o correto era não comparar.
    def chave_simples(r):
        return (r["site"], r["approach"], r["drift_type"],
                float(r["drift_level"]), int(r["repetition"]), r["timestamp"])

    atuais_ts = {chave_simples(r) for r in linhas}
    sobreviventes = sum(1 for r in antigas if chave_simples(r) in atuais_ts)
    if antigas and sobreviventes < len(antigas) / 2:
        ok("Rastreabilidade da limpeza",
           f"o conjunto atual não descende de {os.path.basename(caminho)} "
           f"({sobreviventes} de {len(antigas)} linhas em comum): houve recoleta "
           "completa, não limpeza pontual — nada a auditar aqui")
        return

    def chave(r):
        return (r["site"], r["approach"], r["drift_type"],
                float(r["drift_level"]), int(r["repetition"]), r["timestamp"])

    atuais = {chave(r) for r in linhas}
    removidas = [r for r in antigas if chave(r) not in atuais]

    # O critério é a MENSAGEM de erro, não o custo. Uma execução pode registrar
    # custo pequeno e ainda assim ser contaminada: a recusa do provedor pode
    # chegar depois de a primeira chamada já ter sido faturada. Exigir custo
    # zero acusaria essa linha como remoção indevida — foi o que ocorreu na
    # primeira versão desta verificação.
    ASSINATURA = ("429", "session limit", "usage limit", "rate limit")

    def contaminada(r) -> bool:
        texto = (r.get("error_message") or "").lower()
        return (r["success"].strip().lower() != "true"
                and any(marca in texto for marca in ASSINATURA))

    # Segundo motivo legítimo de remoção: execução DUPLICADA. Em 20/09/2026 duas
    # instâncias do executar_coleta2 ficaram rodando ao mesmo tempo, cada uma com
    # o seu runner, e 89 condições do braço agêntico foram executadas duas vezes.
    # A cópia excedente foi removida e a condição continua no conjunto, com o n
    # previsto: não é dado perdido, é execução a mais que o desenho não prevê.
    # Nenhum par divergia em desfecho, então a escolha não seleciona resultado.
    condicoes_atuais = {(r["site"], r["approach"], r["drift_type"], r["nivel"], r["rep"])
                        for r in linhas}

    def duplicada(r) -> bool:
        return (r["site"], r["approach"], r["drift_type"],
                float(r["drift_level"]), int(r["repetition"])) in condicoes_atuais

    indevidas = [r for r in removidas if not contaminada(r) and not duplicada(r)]

    if indevidas:
        registrar("ERRO", "Rastreabilidade da limpeza",
                  f"{len(indevidas)} das {len(removidas)} linhas removidas NÃO "
                  "trazem a assinatura de recusa do provedor — pode ter sido "
                  "removido dado válido")
    else:
        # o que foi removido precisa ter sido recoletado, senão sobra um buraco
        # que a verificação de completude pegaria, mas convém dizer aqui também
        recoletadas = sum(
            1 for r in removidas
            if any(a["site"] == r["site"] and a["approach"] == r["approach"]
                   and a["drift_type"] == r["drift_type"]
                   and a["nivel"] == float(r["drift_level"])
                   and a["rep"] == int(r["repetition"]) for a in linhas))
        custo_perdido = sum(float(r["cost_usd"] or 0) for r in removidas)
        contaminadas = [r for r in removidas if contaminada(r)]
        duplicadas = [r for r in removidas if not contaminada(r)]
        ok("Rastreabilidade da limpeza",
           f"{len(removidas)} linha(s) removida(s): {len(contaminadas)} com assinatura "
           f"de recusa do provedor e {len(duplicadas)} duplicada(s) cuja condição "
           f"continua no conjunto (custo de API descartado US$ {custo_perdido:.2f}); "
           f"{recoletadas}/{len(removidas)} condições presentes; backup preservado")


# ---------------------------------------------------------------------- saída

def main() -> None:
    import argparse
    global REPETICOES_ESPERADAS, REPETICOES_AGENTICO, NIVEIS_AGENTICO
    ap = argparse.ArgumentParser(description="Audita o conjunto de dados")
    ap.add_argument("--repeticoes", type=int, default=8,
                    help="repetições esperadas nos braços sem API. Use "
                         "--repeticoes 1 para auditar um piloto.")
    ap.add_argument("--repeticoes-agentico", type=int, default=8,
                    help="repetições esperadas no braço agêntico")
    ap.add_argument("--niveis-agentico", type=str, default="0,0.25,0.5,0.75,1.0",
                    help="níveis de drift coletados no braço agêntico")
    args = ap.parse_args()
    REPETICOES_ESPERADAS = args.repeticoes
    REPETICOES_AGENTICO = args.repeticoes_agentico
    NIVEIS_AGENTICO = tuple(float(x) for x in args.niveis_agentico.split(","))

    print("=" * 78)
    print("AUDITORIA DO CONJUNTO DE DADOS")
    print("=" * 78)
    linhas = carregar()
    print(f"\nArquivo: {CSV}")
    print(f"Execuções: {len(linhas)}\n")

    for verificacao in (v_completude, v_duplicatas, v_linha_de_base,
                        v_coerencia_colunas, v_custo_por_braco,
                        v_contadores_healing, v_assinatura_infraestrutura,
                        v_colapso_de_pagina, v_drift_efetivo,
                        v_rotulo_de_erro,
                        v_falhas_de_navegacao, v_guarda_site,
                        v_duracoes, v_monotonicidade, v_transcricoes,
                        v_janela_de_coleta, v_pareamento, v_backup):
        verificacao(linhas)

    largura = max(len(v) for _, v, _ in achados) + 2
    for nivel in ("ERRO", "ALERTA", "OK"):
        grupo = [a for a in achados if a[0] == nivel]
        if not grupo:
            continue
        print(f"\n{nivel}\n{'-' * 78}")
        for _, verificacao, detalhe in grupo:
            print(f"  {verificacao:<{largura}}{detalhe}")

    erros = sum(1 for a in achados if a[0] == "ERRO")
    alertas = sum(1 for a in achados if a[0] == "ALERTA")
    print("\n" + "=" * 78)
    if erros:
        print(f"VEREDITO: {erros} erro(s) — resolva antes de escrever os resultados.")
    elif alertas:
        print(f"VEREDITO: nenhum erro. {alertas} alerta(s) a explicar no texto,")
        print("mas os dados sustentam a análise.")
    else:
        print("VEREDITO: nenhum erro nem alerta. Os dados estão consistentes.")
    print("=" * 78 + "\n")


if __name__ == "__main__":
    main()
