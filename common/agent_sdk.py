"""
Backend alternativo do braço agêntico: Claude Agent SDK autenticado pela
ASSINATURA (sem chave de API, sem cobrança por token).

Serve principalmente para MEDIR o consumo real das suas tarefas antes de decidir
se compra crédito de API. O SDK reporta tokens e uma estimativa de custo em
dólares por execução — é esse número que alimenta o `estimar_custo.py`.

DIFERENÇA IMPORTANTE PARA O BACKEND DE API
------------------------------------------
Aqui o loop do agente é do próprio SDK, não nosso. Isso significa que parte do
comportamento (prompt de sistema, gerenciamento de contexto, compactação) vem de
uma camada que não controlamos nem conseguimos descrever integralmente na
metodologia. Para a COLETA DEFINITIVA, prefira o backend de API (common/agent.py),
onde o loop é explícito. Use este backend para o piloto e para medir custo.

CUIDADO AO USAR ESTE BACKEND PARA ESTIMAR CUSTO
-----------------------------------------------
Medição feita em 30/07/2026: uma consulta trivial ("responda OK"), sem navegador
e sem ferramentas, consumiu 20.921 tokens de cache e custou US$ 0,1256. Esse é o
prompt de sistema da CLI do Claude Code, cobrado a cada execução.

Ou seja: o custo medido por este backend EMBUTE um overhead fixo que não existe
no backend de API. Ao projetar o orçamento da coleta pela API, desconte esse
valor — caso contrário você superestima o gasto. O `estimar_custo.py` aceita
`--descontar-overhead` justamente para isso.

Para reduzir a diferença entre os dois backends, aqui nós:
  - restringimos as ferramentas ao servidor Playwright MCP (bloqueando Bash,
    Read, Write etc.), para que o agente tenha exatamente as mesmas capacidades
    do outro backend;
  - desativamos settings de projeto/usuário e outros servidores MCP, para que a
    execução não dependa da configuração da máquina;
  - usamos o mesmo texto de instrução da tarefa.

REQUISITOS
----------
- Node.js instalado (o servidor Playwright MCP roda via npx).
- pip install claude-agent-sdk
- Estar autenticado na sua assinatura (o mesmo login do Claude Code / Cowork).
- NÃO defina ANTHROPIC_API_KEY, senão o SDK usa a API cobrada em vez da assinatura.
"""

from __future__ import annotations

import os

from common.agent import (MAX_USD_POR_EXECUCAO, MODEL, PLAYWRIGHT_MCP, SYSTEM_PROMPT,
                          AgentOutcome)

# Ferramentas nativas do Claude Code que NÃO devem existir nesta tarefa: o agente
# do experimento só pode agir pelo navegador, como no backend de API.
FERRAMENTAS_BLOQUEADAS = [
    "Bash", "Read", "Write", "Edit", "NotebookEdit", "Glob", "Grep",
    "WebFetch", "WebSearch", "Task", "TodoWrite", "SlashCommand",
]

# ToolSearch NÃO PODE ser bloqueada — anotação a partir de um erro cometido.
#
# Ao ver `ToolSearch` nas transcrições, ela foi incluída nesta lista, por parecer
# uma capacidade extra fora do servidor Playwright. O efeito foi o oposto do
# esperado: nesta versão da CLI as ferramentas MCP chegam adiadas, e ToolSearch
# é o mecanismo que as carrega. Sem ela o agente ficava literalmente sem
# navegador e respondia "não tenho acesso a ferramentas de automação".
#
# As oito execuções do controle falharam por isso, e o script de comparação
# leu os dois zeros como "a taxa se mantém" — uma conclusão confiante extraída
# de um experimento que não ocorreu. É o mesmo padrão dos outros defeitos deste
# projeto: o estrago não foi quebrar, foi produzir número plausível.
#
# ToolSearch não amplia o que o agente pode fazer no mundo: ela só torna
# visíveis ferramentas já autorizadas por allowed_tools. Consome turnos e
# tokens, e isso deve ser descontado ao comparar custo entre backends.

# FERRAMENTAS DE FUGA — precisam ficar bloqueadas SEMPRE.
#
# O Playwright MCP expõe uma ferramenta que executa código Playwright arbitrário
# na página. Com ela o agente deixa de ser um agente que lê a interface e passa a
# ser um autor de script determinístico: numa das execuções de validação ele
# escreveu, de uma vez só, o fluxo inteiro da compra usando seletores
# `[data-test=...]` — exatamente os atributos que o drift NUNCA altera, por
# representarem um contrato de automação estável.
#
# O efeito é devastador para o experimento, e seletivo da pior forma: das oito
# execuções de validação, o recurso apareceu apenas nas DUAS condições mais
# difíceis do SauceDemo (estrutural e semântico no nível máximo). Ou seja,
# justamente onde o estudo mede o que interessa, ele deixava de medir. Uma
# delas concluiu a tarefa em 7 turnos contra mediana de 20.
#
# Vale notar que os atributos `data-test` não aparecem na árvore de
# acessibilidade que o agente recebe em condições normais: sem execução de
# código, ele não tem como enxergá-los. Bloquear estas ferramentas fecha a
# brecha na origem.
FERRAMENTAS_DE_FUGA = [
    "mcp__playwright__browser_run_code_unsafe",
    "mcp__playwright__browser_evaluate",
    "mcp__playwright__browser_run_code",
]

# Condição de CONTROLE (ver controle_navegacao.py).
#
# As transcrições mostraram o agente indo direto a https://www.saucedemo.com/
# cart.html em vez de clicar no link do carrinho — justamente o elemento que o
# drift degrada. Ele resolve a TAREFA sem resolver o problema de LOCALIZAÇÃO,
# usando conhecimento prévio sobre o site em vez da página à sua frente.
#
# Com esta variável ativa, a navegação por URL é bloqueada e o agente é obrigado
# a atravessar a interface. Comparar as duas condições separa robustez real de
# desvio pelo endereço.
BLOQUEAR_NAVEGACAO = os.getenv("AGENT_BLOQUEAR_NAVEGACAO", "").strip() in ("1", "true", "sim")


async def run_agent_sdk(cdp_endpoint: str, task_prompt: str,
                        max_steps: int = 25) -> AgentOutcome:
    """
    Mesma assinatura de `common.agent.run_agent`, para os dois backends serem
    intercambiáveis. Também não julga sucesso — quem decide é o oráculo.
    """
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    outcome = AgentOutcome()

    bloqueadas = list(FERRAMENTAS_BLOQUEADAS) + list(FERRAMENTAS_DE_FUGA)
    if BLOQUEAR_NAVEGACAO:
        bloqueadas.append("mcp__playwright__browser_navigate")

    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={
            "playwright": {
                "command": "npx",
                "args": ["-y", PLAYWRIGHT_MCP, "--cdp-endpoint", cdp_endpoint],
            }
        },
        allowed_tools=["mcp__playwright"],
        disallowed_tools=bloqueadas,
        permission_mode="bypassPermissions",  # execução não supervisionada
        strict_mcp_config=True,               # ignora .mcp.json do projeto/usuário
        setting_sources=[],                   # ignora settings e CLAUDE.md locais
        max_turns=max_steps,
        # MESMO modelo do backend de API (common/agent.py). Sem isto, o SDK usa
        # o modelo padrão da CLI, e os dois braços do experimento rodariam em
        # modelos diferentes — o que invalidaria a comparação.
        model=MODEL,
        # Teto de gasto por execução, nativo do SDK. Mesmo valor usado no backend
        # de API, para que os dois se comportem igual no pior caso.
        max_budget_usd=MAX_USD_POR_EXECUCAO or None,
    )

    try:
        async for message in query(prompt=task_prompt, options=options):
            # Registro das ações do agente.
            #
            # Sem isto o arquivo de transcrição sai com a lista vazia, e o
            # resultado deixa de ser auditável: não há como verificar depois
            # POR QUE caminho o agente chegou ao estado final, nem construir a
            # taxonomia qualitativa de falhas. O oráculo continua garantindo que
            # o estado final é real, mas o processo fica sem registro.
            #
            # O acesso é por duck typing porque as classes de mensagem variam
            # entre versões do SDK; falhar aqui não pode derrubar a execução.
            for bloco in getattr(message, "content", None) or []:
                try:
                    if getattr(bloco, "name", None) and hasattr(bloco, "input"):
                        outcome.transcript.append(
                            {"tool": bloco.name, "input": bloco.input}
                        )
                    elif getattr(bloco, "text", None):
                        outcome.transcript.append(
                            {"texto": str(bloco.text)[:500]}
                        )
                except Exception:  # noqa: BLE001
                    pass

            if isinstance(message, ResultMessage):
                uso = message.usage or {}
                entrada = uso.get("input_tokens", 0) or 0
                escrita = uso.get("cache_creation_input_tokens", 0) or 0
                leitura = uso.get("cache_read_input_tokens", 0) or 0

                outcome.tokens_input = entrada + escrita + leitura
                outcome.tokens_cache_write = escrita
                outcome.tokens_cache_read = leitura
                outcome.tokens_output = uso.get("output_tokens", 0) or 0

                # ATENÇÃO À COMPARABILIDADE: aqui n_steps recebe o número de
                # TURNOS do SDK, e um turno pode conter várias chamadas de
                # ferramenta em paralelo. Nos braços determinísticos a mesma
                # coluna conta AÇÕES explícitas da tarefa (preencher, clicar,
                # verificar). As duas grandezas não são a mesma coisa e não
                # devem ser comparadas entre braços no texto do trabalho.
                #
                # O efeito é visível nos dados: duas execuções do SauceDemo
                # concluíram a compra em 3 e 4 turnos, contra mediana de 19 —
                # o agente agrupou as chamadas, não pulou etapas. Com a
                # transcrição agora registrada, `len(outcome.transcript)` dá a
                # contagem de ações realmente comparável.
                outcome.n_steps = message.num_turns or 0

                # o SDK já calcula o custo estimado; usamos o dele quando disponível
                if message.total_cost_usd is not None:
                    outcome.cost_usd = round(message.total_cost_usd, 6)

                # Atenção: com subtype="success" o campo is_error ainda pode ser
                # True — significa que o loop terminou, mas a última chamada à
                # API falhou. Esses campos são a única pista do motivo real.
                detalhes = []
                if getattr(message, "api_error_status", None):
                    detalhes.append(f"HTTP {message.api_error_status}")
                if getattr(message, "terminal_reason", None):
                    detalhes.append(f"terminal_reason={message.terminal_reason}")
                if message.errors:
                    detalhes.append("; ".join(message.errors))
                if getattr(message, "result", None):
                    detalhes.append(str(message.result)[:300])

                # Erro de INFRAESTRUTURA (limite de uso, indisponibilidade) não é
                # resultado experimental. Se for registrado como falha, a análise
                # concluirá que o agente não deu conta da tarefa — quando na
                # verdade ele sequer chegou a tentar. Marcado à parte para que a
                # coleta seja interrompida em vez de gravar dado inválido.
                texto_erro = " ".join(detalhes).lower()
                if (getattr(message, "api_error_status", None) == 429
                        or "session limit" in texto_erro
                        or "usage limit" in texto_erro
                        or "rate limit" in texto_erro):
                    outcome.stopped_reason = "rate_limit"
                    outcome.error_message = " | ".join(detalhes) or "limite de uso atingido"
                elif message.subtype == "success" and not message.is_error:
                    outcome.stopped_reason = "finished"
                elif message.subtype == "error_max_turns":
                    outcome.stopped_reason = "max_steps"
                elif message.subtype == "error_max_budget_usd":
                    outcome.stopped_reason = "max_budget"
                    outcome.error_message = (
                        f"teto de US$ {MAX_USD_POR_EXECUCAO:.2f} por execução atingido"
                    )
                else:
                    outcome.stopped_reason = "error"
                    outcome.error_message = (
                        f"subtype={message.subtype} is_error={message.is_error} | "
                        + (" | ".join(detalhes) or "sem detalhes da API")
                    )
    except Exception as e:  # noqa: BLE001
        # O SDK lança exceção DEPOIS de emitir o resultado. A mensagem já
        # capturada era preservada aqui, mas o MOTIVO não era — e a atribuição
        # cega de "error" apagava diagnósticos mais específicos.
        #
        # O efeito foi medido: duas execuções que pararam por teto de custo
        # (`max_budget`) foram gravadas como `exception`. No arquivo de
        # resultados isso é a diferença entre "o agente travou" e "nós o
        # interrompemos ao atingir US$ 0,60" — a segunda é um limite artificial
        # do experimento e precisa ser reportada como tal.
        MOTIVOS_ESPECIFICOS = {"finished", "max_steps", "max_budget", "rate_limit"}
        if not outcome.error_message:
            outcome.error_message = f"{type(e).__name__}: {e}"
        if outcome.stopped_reason not in MOTIVOS_ESPECIFICOS:
            outcome.stopped_reason = "error"

    return outcome
