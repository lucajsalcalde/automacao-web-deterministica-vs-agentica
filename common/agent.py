"""
Braço agêntico: agente LLM (Claude) dirigindo o navegador via Playwright MCP.

ARQUITETURA (importante para a metodologia)
-------------------------------------------
O ponto delicado é: se quem dirige o navegador é o agente, como aplicar o drift
de UI e como verificar o sucesso de forma independente?

Solução adotada — um único navegador, dois clientes:

    Chromium (lançado por nós, com --remote-debugging-port)
        |
        +-- Playwright (nosso)      -> injeta o drift, executa o oráculo
        +-- Playwright MCP (agente) -> conectado por --cdp-endpoint ao MESMO navegador

Assim o agente atua livremente, mas a manipulação do ambiente e a verificação do
resultado permanecem sob controle do pesquisador. Também mantém o Playwright como
motor nos dois braços do experimento, isolando a variável de interesse (a lógica
de decisão) — que é justamente a decisão metodológica registrada no escopo.

CUSTO
-----
Cada execução consome tokens. `max_steps` limita o número de iterações do agente,
evitando custo descontrolado em caso de loop. O uso de tokens é lido diretamente
da resposta da API e convertido em dólares pelos preços configurados abaixo.

REQUISITOS
----------
- Node.js instalado (o servidor Playwright MCP roda via npx).
- Variável de ambiente ANTHROPIC_API_KEY.
- pip install anthropic mcp
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass, field

# Preços em dólares por milhão de tokens (consultados em 30/07/2026).
#
# ATENÇÃO — o preço do Sonnet 5 MUDA EM 01/09/2026:
#   até 31/08/2026:  US$ 2 (entrada) / US$ 10 (saída)  <- promocional, valor padrão abaixo
#   a partir de 01/09/2026: US$ 3 (entrada) / US$ 15 (saída)
# Se a coleta atravessar essa data, registre no TCC qual preço valia em cada
# execução — o custo em dólares é uma das métricas do experimento.
#
# Referência (Haiku 4.5, opção mais barata): US$ 1 / US$ 5.
INPUT_USD_PER_MTOK = float(os.getenv("LLM_INPUT_USD_PER_MTOK", "2.0"))
OUTPUT_USD_PER_MTOK = float(os.getenv("LLM_OUTPUT_USD_PER_MTOK", "10.0"))

MODEL = os.getenv("LLM_MODEL", "claude-sonnet-5")

# Versão do servidor Playwright MCP fixada para a coleta 2 (última estável em
# 14/09/2026). Com @latest, a versão podia mudar no meio da coleta, entre uma
# retomada e outra, e o artigo não teria como informar qual foi usada.
PLAYWRIGHT_MCP = "@playwright/mcp@0.0.81"


# Cache de prompt: a lista de ferramentas do Playwright MCP (~20 ferramentas) é
# reenviada a cada passo do agente. Marcando-a para cache, as leituras seguintes
# custam 10% do preço de entrada — economia relevante em tarefas com muitos passos.
# Desligue com LLM_PROMPT_CACHE=0 se a API recusar o parâmetro.
USE_PROMPT_CACHE = os.getenv("LLM_PROMPT_CACHE", "1") == "1"

# Teto de gasto POR EXECUÇÃO, em dólares.
#
# O teto de passos sozinho não limita o custo: uma execução pode gastar poucos
# passos com respostas enormes, ou o contexto pode crescer a ponto de cada passo
# custar muito mais que o anterior. Este limite é verificado após cada chamada e
# interrompe a execução assim que for ultrapassado.
#
# É especialmente relevante porque, no braço agêntico, FALHAR custa mais caro que
# ter sucesso: diante de um elemento que não encontra, o agente continua
# explorando a página até esgotar os passos. As condições de maior drift, onde se
# espera mais falhas, são portanto as mais caras.
#
# 0 desativa o teto. Ajuste com LLM_MAX_USD_POR_EXECUCAO.
MAX_USD_POR_EXECUCAO = float(os.getenv("LLM_MAX_USD_POR_EXECUCAO", "0.60"))

SYSTEM_PROMPT = (
    "Você é um agente de automação web. Execute a tarefa solicitada usando as "
    "ferramentas de navegador disponíveis. A página já está aberta no navegador. "
    "Aja diretamente: não peça confirmação e não explique o que pretende fazer. "
    "Inspecione a página antes de agir e adapte-se caso os elementos não estejam "
    "onde você espera. Quando a tarefa estiver concluída, responda apenas CONCLUIDO."
)


@dataclass
class AgentOutcome:
    """Resultado bruto da execução do agente (sem julgamento de sucesso)."""

    n_steps: int = 0
    tokens_input: int = 0          # total de tokens de entrada processados
    tokens_output: int = 0
    tokens_cache_write: int = 0    # subconjunto de tokens_input, cobrado a 1,25x
    tokens_cache_read: int = 0     # subconjunto de tokens_input, cobrado a 0,10x
    cost_usd: float = 0.0
    stopped_reason: str = ""       # finished | max_steps | max_budget | error
    error_message: str = ""
    transcript: list = field(default_factory=list)

    def add_usage(self, usage) -> None:
        """
        Acumula uso e recalcula o custo.

        Com cache de prompt ativo, a API separa a entrada em três categorias com
        preços distintos: entrada normal (1x), escrita de cache (1,25x) e leitura
        de cache (0,10x). Somar tudo como entrada normal superestimaria o custo.
        """
        novo_input = usage.input_tokens
        novo_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        novo_read = getattr(usage, "cache_read_input_tokens", 0) or 0

        self.tokens_input += novo_input + novo_write + novo_read
        self.tokens_cache_write += novo_write
        self.tokens_cache_read += novo_read
        self.tokens_output += usage.output_tokens

        entrada_normal = self.tokens_input - self.tokens_cache_write - self.tokens_cache_read
        self.cost_usd = round(
            entrada_normal / 1_000_000 * INPUT_USD_PER_MTOK
            + self.tokens_cache_write / 1_000_000 * INPUT_USD_PER_MTOK * 1.25
            + self.tokens_cache_read / 1_000_000 * INPUT_USD_PER_MTOK * 0.10
            + self.tokens_output / 1_000_000 * OUTPUT_USD_PER_MTOK,
            6,
        )


def _mcp_tools_to_claude(mcp_tools) -> list:
    """Converte o schema de ferramentas do MCP para o formato da API da Anthropic."""
    tools = []
    for t in mcp_tools:
        tools.append({
            "name": t.name,
            "description": (t.description or "")[:1000],
            "input_schema": t.inputSchema or {"type": "object", "properties": {}},
        })
    # marca o fim do bloco de ferramentas para cache — tudo que vem antes é
    # reaproveitado nas chamadas seguintes a 10% do preço de entrada.
    if tools and USE_PROMPT_CACHE:
        tools[-1]["cache_control"] = {"type": "ephemeral"}
    return tools


def _comando_npx() -> str:
    """
    Caminho do executável npx.

    No Windows o npm instala `npx.cmd`, não `npx`. O asyncio cria o subprocesso
    sem shell, então passar apenas "npx" resulta em falha de spawn — que sobe
    como um ExceptionGroup genérico vindo do TaskGroup do cliente MCP, sem
    indicar a causa real. Por isso resolvemos o caminho explicitamente.
    """
    for nome in (("npx.cmd", "npx") if sys.platform == "win32" else ("npx",)):
        caminho = shutil.which(nome)
        if caminho:
            return caminho
    raise RuntimeError(
        "npx não encontrado no PATH. O servidor Playwright MCP roda via npx, "
        "que vem com o Node.js. Instale o Node.js LTS (https://nodejs.org) e "
        "abra um terminal novo."
    )


def _descrever_excecao(e: BaseException, profundidade: int = 0) -> str:
    """
    Desembrulha ExceptionGroup (usado pelo anyio/MCP) para expor a causa real.
    Sem isto, toda falha aparece como "unhandled errors in a TaskGroup".
    """
    prefixo = "  " * profundidade
    texto = f"{prefixo}{type(e).__name__}: {e}"
    sub = getattr(e, "exceptions", None)
    if sub:
        for filha in sub:
            texto += "\n" + _descrever_excecao(filha, profundidade + 1)
    elif e.__cause__ is not None:
        texto += "\n" + prefixo + "causa -> " + _descrever_excecao(e.__cause__, profundidade + 1)
    return texto


def _result_to_text(result) -> str:
    """Reduz o retorno de uma ferramenta MCP a texto, para devolver ao modelo."""
    parts = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
        else:
            # conteúdo não textual (ex.: screenshot) — informa o tipo sem inflar tokens
            parts.append(f"[conteúdo {getattr(item, 'type', 'desconhecido')} omitido]")
    return "\n".join(parts)[:8000] or "(sem conteúdo)"


async def run_agent(cdp_endpoint: str, task_prompt: str, max_steps: int = 25) -> AgentOutcome:
    """
    Executa a tarefa descrita em `task_prompt` usando Claude + Playwright MCP,
    conectado por CDP ao navegador indicado em `cdp_endpoint`.

    Retorna o custo e o número de passos. NÃO decide se a tarefa teve sucesso —
    isso é responsabilidade do oráculo, propositalmente independente do agente.
    """
    from anthropic import AsyncAnthropic
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    outcome = AgentOutcome()
    client = AsyncAnthropic()  # lê ANTHROPIC_API_KEY do ambiente

    server = StdioServerParameters(
        command=_comando_npx(),
        args=["-y", PLAYWRIGHT_MCP, "--cdp-endpoint", cdp_endpoint],
    )

    try:
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                mcp_tools = (await session.list_tools()).tools
                tools = _mcp_tools_to_claude(mcp_tools)

                messages = [{"role": "user", "content": task_prompt}]

                for _ in range(max_steps):
                    response = await client.messages.create(
                        model=MODEL,
                        max_tokens=2048,
                        system=SYSTEM_PROMPT,
                        tools=tools,
                        messages=messages,
                    )
                    outcome.add_usage(response.usage)

                    # teto de gasto verificado a cada chamada, antes de decidir
                    # continuar: interrompe imediatamente ao ultrapassar o limite
                    if MAX_USD_POR_EXECUCAO and outcome.cost_usd >= MAX_USD_POR_EXECUCAO:
                        outcome.stopped_reason = "max_budget"
                        outcome.error_message = (
                            f"teto de US$ {MAX_USD_POR_EXECUCAO:.2f} por execução atingido "
                            f"(gasto: US$ {outcome.cost_usd:.4f}) após {outcome.n_steps} passos"
                        )
                        break

                    if response.stop_reason != "tool_use":
                        outcome.stopped_reason = "finished"
                        break

                    messages.append({"role": "assistant", "content": response.content})

                    tool_results = []
                    for block in response.content:
                        if block.type != "tool_use":
                            continue
                        outcome.n_steps += 1
                        outcome.transcript.append(
                            {"tool": block.name, "input": block.input}
                        )
                        try:
                            result = await session.call_tool(block.name, block.input or {})
                            content = _result_to_text(result)
                        except Exception as e:  # noqa: BLE001
                            content = f"ERRO ao executar a ferramenta: {type(e).__name__}: {e}"
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": content,
                        })

                    messages.append({"role": "user", "content": tool_results})
                else:
                    outcome.stopped_reason = "max_steps"

    except Exception as e:  # noqa: BLE001 - ExceptionGroup também é Exception
        descricao = _descrever_excecao(e)
        # Ver a nota em common/agent_sdk.py: limite de uso e indisponibilidade
        # não são resultado experimental e não podem virar linha de falha.
        baixo = descricao.lower()
        if ("429" in descricao or "rate_limit" in baixo or "rate limit" in baixo
                or "overloaded" in baixo or "usage limit" in baixo):
            outcome.stopped_reason = "rate_limit"
        else:
            outcome.stopped_reason = "error"
        outcome.error_message = descricao

    return outcome


def save_transcript(outcome: AgentOutcome, path: str) -> None:
    """
    Salva a sequência de ações do agente. Útil para a discussão qualitativa do
    TCC (por exemplo: como o agente reagiu quando o seletor mudou) e para
    auditar casos de falso sucesso.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "n_steps": outcome.n_steps,
            "tokens_input": outcome.tokens_input,
            "tokens_output": outcome.tokens_output,
            "tokens_cache_write": outcome.tokens_cache_write,
            "tokens_cache_read": outcome.tokens_cache_read,
            "cost_usd": outcome.cost_usd,
            "model": MODEL,
            "preco_input_usd_por_mtok": INPUT_USD_PER_MTOK,
            "preco_output_usd_por_mtok": OUTPUT_USD_PER_MTOK,
            "stopped_reason": outcome.stopped_reason,
            "error_message": outcome.error_message,
            "transcript": outcome.transcript,
        }, f, ensure_ascii=False, indent=1)
