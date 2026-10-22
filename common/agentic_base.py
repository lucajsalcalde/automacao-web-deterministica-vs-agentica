"""
Base comum das tarefas agênticas.

Faz o ciclo completo de UMA execução agêntica:
 1. lança o Chromium com porta de depuração (CDP) aberta;
 2. abre a página inicial e injeta o drift de UI (controle nosso, não do agente);
 3. entrega o controle ao agente (Claude via Playwright MCP);
 4. executa o oráculo sobre a MESMA página, verificando o estado final real;
 5. devolve um TaskResult no mesmo formato do braço determinístico, para que os
    dois entrem no mesmo CSV e possam ser comparados diretamente.
"""

from __future__ import annotations

import os
import socket
import time

from common.agent import AgentOutcome, run_agent, save_transcript
from common.drift import (esperar_drift, instalar_drift_persistente,
                         totais_drift)
from common.oracle import (RASTREAR_CARRINHO_JS, RASTREAR_ELEMENTOS_JS,
                           RASTREAR_LOGIN_JS, TaskResult, navegar_async)

# Backend do braço agêntico:
#   "api" (padrão) -> common/agent.py: loop nosso, chave de API, custo por token.
#   "sdk"          -> common/agent_sdk.py: Claude Agent SDK pela assinatura, sem
#                     cobrança por token. Indicado para o piloto/medição.
# Troque com:  $env:AGENT_BACKEND = "sdk"
AGENT_BACKEND = os.getenv("AGENT_BACKEND", "api").lower()

# Teto de passos do agente por execução.
#
# Medição do piloto (30/07/2026, SauceDemo, SEM drift): 18 e 20 passos. Com a
# interface degradada o agente precisa explorar mais, então um teto de 25 faria
# execuções baterem no limite e serem registradas como falha por esgotamento de
# passos — e não por incapacidade de lidar com o drift, que é o que o estudo
# quer medir. O teto existe para conter custo em caso de loop, não para limitar
# a tarefa: por isso 40, com folga sobre o observado.
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "40"))


async def _executar_agente(cdp_endpoint: str, task_prompt: str, max_steps: int):
    if AGENT_BACKEND == "sdk":
        from common.agent_sdk import run_agent_sdk
        return await run_agent_sdk(cdp_endpoint, task_prompt, max_steps)
    return await run_agent(cdp_endpoint, task_prompt, max_steps)


class AgenteContornouAInterface(Exception):
    """
    Sinaliza que o agente resolveu a tarefa por fora da interface degradada.

    Duas formas foram observadas, ambas na validação:

      - executar código Playwright arbitrário, escrevendo de uma vez o fluxo
        inteiro em vez de ler a página e agir sobre ela;
      - mirar atributos `data-test`, que o drift deixa intactos por representarem
        um contrato de automação estável, e que por isso conferem imunidade
        completa à degradação.

    Nos dois casos a execução não mede robustez de localização, que é o objeto
    do estudo. Registrá-la como sucesso inflaria a taxa do braço agêntico
    justamente nas condições mais difíceis — foi onde o comportamento apareceu.

    As ferramentas de execução de código estão bloqueadas; esta verificação é a
    segunda linha de defesa, para o caso de o bloqueio falhar ou de a fuga
    ocorrer por um caminho ainda não previsto.
    """


class AgenteSemFerramentas(Exception):
    """
    Sinaliza que o agente terminou sem executar NENHUMA ação no navegador.

    Isso não é uma falha diante do drift: é o agente sem as ferramentas de que
    precisa, por erro de configuração. Um agente que não clicou em nada não
    tentou a tarefa, e registrar isso como falha faria a análise concluir que
    ele não deu conta da interface degradada.

    Ocorreu na prática ao bloquear indevidamente a ferramenta que carrega o
    servidor Playwright: oito execuções seguidas terminaram com o agente
    respondendo que não tinha acesso ao navegador, e o resultado — zero por
    cento nas duas condições comparadas — parecia perfeitamente interpretável.
    """


class LimiteDeUsoAtingido(Exception):
    """
    Sinaliza que o provedor recusou a requisição por limite de uso.

    Interrompe a coleta em vez de registrar a execução como falha. Um erro de
    infraestrutura não é resultado experimental: gravá-lo como falha faria a
    análise concluir que o agente não deu conta da tarefa, quando na verdade
    nem chegou a tentar. Ocorreu na prática — 11 execuções consecutivas foram
    registradas como falha por causa de um limite de sessão, e precisaram ser
    removidas do dataset.
    """


def _detectar_fuga(acoes: list[dict]) -> list[str]:
    """
    Procura, na transcrição, sinais de que o agente contornou a interface.

    Não julga sucesso nem falha: apenas identifica execuções cuja rota torna a
    medição sem sentido. O `data-test` entra aqui porque o drift o preserva
    deliberadamente — quem o utiliza fica imune à degradação por construção, e
    a execução deixa de informar qualquer coisa sobre robustez.
    """
    import json as _json

    FERRAMENTAS = ("run_code", "evaluate")
    ATRIBUTOS_IMUNES = ("data-test", "data-testid", "data-qa")

    achados = []
    for acao in acoes:
        nome = acao.get("tool", "")
        if any(marca in nome for marca in FERRAMENTAS):
            achados.append(f"executou código arbitrário via {nome.split('__')[-1]}")
            continue
        try:
            argumentos = _json.dumps(acao.get("input", {}), ensure_ascii=False)
        except (TypeError, ValueError):
            continue
        for atributo in ATRIBUTOS_IMUNES:
            if atributo in argumentos:
                achados.append(
                    f"mirou o atributo {atributo}, que o drift não altera "
                    f"(ferramenta {nome.split('__')[-1]})")
                break
    return achados


def _free_port() -> int:
    """Porta livre para o CDP — permite execuções em sequência sem conflito."""
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


async def run_agentic_task(
    site: str,
    task: str,
    start_url: str,
    task_prompt: str,
    oracle,
    drift_level: float = 0.0,
    drift_type: str = "cosmetico",
    repetition: int = 0,
    max_steps: int | None = None,
    headless: bool = True,
    transcript_dir: str | None = None,
) -> TaskResult:
    """
    `oracle` é uma corrotina `async def oracle(page) -> None` que deve levantar
    AssertionError se o estado final não for o esperado. Ela é executada por nós,
    nunca pelo agente — é o que impede que um "falso sucesso" relatado pelo
    modelo seja registrado como sucesso.
    """
    from playwright.async_api import async_playwright

    if max_steps is None:
        max_steps = MAX_STEPS
    port = _free_port()
    success = False
    error_type = ""
    error_message = ""
    outcome = AgentOutcome()  # garante valores válidos mesmo se o navegador falhar
    stats_drift: dict = {}
    start = time.perf_counter()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=[f"--remote-debugging-port={port}"],
        )
        try:
            context = await browser.new_context()

            # Registra o pico de itens no carrinho ANTES de qualquer navegação.
            # Sem isso o oráculo não distingue uma compra real de um checkout
            # vazio: o site conclui os dois e exibe a mesma confirmação.
            await context.add_init_script(RASTREAR_CARRINHO_JS)
            # Coleta 2: o agente passa pela mesma conferência dos braços
            # determinísticos (login confirmado e contagem de elementos no The
            # Internet). Ver o bloco correspondente em common/oracle.py.
            await context.add_init_script(RASTREAR_LOGIN_JS)
            await context.add_init_script(RASTREAR_ELEMENTOS_JS)

            if drift_level > 0:
                # O drift precisa ser instalado ANTES da primeira navegação, e
                # valer para todas as páginas seguintes.
                #
                # Antes desta correção ele era aplicado uma única vez, com
                # `apply_drift_async`, depois do `goto` inicial. Como o drift não
                # sobrevive a uma troca de documento, o agente encontrava a
                # interface degradada apenas na primeira página: no SauceDemo,
                # uma das cinco páginas da tarefa, contra as cinco que os braços
                # determinísticos enfrentam. A comparação media coisas
                # diferentes e favorecia o braço agêntico.
                #
                # mesma seed do braço determinístico: a repetição N sofre a mesma
                # mutação nos dois braços, permitindo comparação pareada.
                await instalar_drift_persistente(
                    context, drift_level, seed=repetition, tipo=drift_type)

            page = await context.new_page()
            await navegar_async(page, start_url)

            if drift_level > 0:
                # Confirma que a degradação ocorreu antes de entregar o controle.
                # Falhar aqui é preferível a rodar uma execução sem drift e
                # registrá-la como se tivesse.
                await esperar_drift(page)

            outcome = await _executar_agente(
                cdp_endpoint=f"http://localhost:{port}",
                task_prompt=task_prompt,
                max_steps=max_steps,
            )

            if transcript_dir:
                save_transcript(
                    outcome,
                    os.path.join(
                        transcript_dir,
                        f"{site}_{task}_{drift_type}{drift_level}_rep{repetition}.json",
                    ),
                )

            if outcome.stopped_reason == "rate_limit":
                raise LimiteDeUsoAtingido(outcome.error_message)

            # Nenhuma chamada de ferramenta significa que o agente não agiu
            # sobre a página. Como a transcrição também guarda os blocos de
            # texto, a contagem precisa filtrar por 'tool'. A verificação só
            # vale quando há transcrição: o backend antigo não a registrava.
            acoes = [a for a in outcome.transcript if isinstance(a, dict) and "tool" in a]

            # Fuga da interface: ver AgenteContornouAInterface. A checagem roda
            # ANTES do oráculo de propósito — uma execução assim costuma
            # terminar em sucesso, e é justamente por isso que precisa ser
            # barrada em vez de contabilizada.
            # Contorno da interface: registrado como desfecho PRÓPRIO, nem
            # sucesso nem falha.
            #
            # Interromper a coleta seria rigoroso, mas travaria uma célula
            # inteira caso o comportamento se repetisse — e ele aparece
            # justamente nas condições mais difíceis. Contá-lo como sucesso
            # inflaria a robustez do braço; como falha, a subestimaria. Nenhuma
            # das duas descreve o que houve: o agente concluiu a tarefa, mas por
            # um caminho que não exercita a localização na interface degradada.
            #
            # Separado, vira resultado: a taxa de contorno mede com que
            # frequência o agente abandona a interface quando ela fica difícil.
            # Só faz sentido falar em CONTORNO quando existe degradação a
            # contornar. Na linha de base a página está íntegra, e mirar
            # `data-test` ali é uma escolha de localização como outra qualquer.
            # Sem esta condição, uma execução perfeitamente válida do nível 0
            # era descartada — e como a linha de base é o que atesta que o
            # instrumento não introduz falha própria, perdê-la é especialmente
            # ruim.
            fugas = _detectar_fuga(acoes) if drift_level > 0 else []
            if fugas:
                error_type = "contornou"
                error_message = "; ".join(fugas)

            # Passa a registrar AÇÕES no navegador, e não turnos do SDK.
            #
            # Um turno pode conter várias chamadas de ferramenta em paralelo, o
            # que tornava a coluna incomparável com a dos braços determinísticos,
            # onde ela conta ações explícitas da tarefa. O efeito era visível:
            # execuções do SauceDemo apareciam com 3 e 4 "passos" contra mediana
            # de 19, sem terem pulado etapa alguma.
            #
            # Só se aplica quando há transcrição; o número de turnos permanece
            # como reserva para não perder o dado se a captura falhar.
            if acoes:
                outcome.n_steps = len(acoes)

            if outcome.transcript and not acoes:
                dito = next((a.get("texto", "") for a in outcome.transcript
                             if isinstance(a, dict) and a.get("texto")), "")
                raise AgenteSemFerramentas(
                    "o agente encerrou sem executar nenhuma ação no navegador. "
                    "Verifique allowed_tools/disallowed_tools e se o servidor "
                    f"Playwright MCP subiu. O agente disse: {dito[:200]}"
                )

            if error_type == "contornou":
                # o oráculo NÃO é executado: o estado final até pode estar
                # correto, mas foi alcançado por fora da interface degradada, e
                # marcar sucesso aqui é justamente o que se quer evitar
                pass
            elif outcome.stopped_reason == "error":
                error_type = "exception"
                error_message = outcome.error_message
            else:
                # O agente terminou, estourou os passos ou o orçamento; o veredito
                # é sempre do oráculo. Se ele confirmar o estado final esperado, a
                # execução conta como sucesso mesmo tendo esbarrado em um limite —
                # o que importa é o resultado, não o caminho.
                try:
                    await oracle(page)
                    success = True
                except AssertionError as e:
                    # Distinguir o motivo é essencial para a análise: uma falha por
                    # teto de custo ou de passos é um limite ARTIFICIAL imposto pelo
                    # experimento, não uma incapacidade do agente. Tratá-las como
                    # equivalentes subestimaria o desempenho do braço agêntico.
                    error_type = {
                        "max_steps": "max_steps",
                        "max_budget": "max_budget",
                    }.get(outcome.stopped_reason, "assertion_failed")
                    error_message = str(e)
                    if outcome.error_message:
                        error_message = f"{outcome.error_message} | {error_message}"
                except Exception as e:  # noqa: BLE001
                    error_type = "exception"
                    error_message = f"{type(e).__name__}: {e}"

            # Lido com a página ainda viva: o acumulado fica em sessionStorage,
            # que morre junto com o contexto.
            if drift_level > 0:
                try:
                    stats_drift = await totais_drift(page)
                except Exception:  # noqa: BLE001
                    stats_drift = {}
        finally:
            await browser.close()

    duration = time.perf_counter() - start

    return TaskResult(
        site=site,
        task=task,
        approach="agentic",
        drift_level=drift_level,
        drift_type=drift_type,
        repetition=repetition,
        success=success,
        duration_s=round(duration, 3),
        n_steps=outcome.n_steps,
        error_type=error_type,
        error_message=error_message[:500],
        tokens_input=outcome.tokens_input,
        tokens_output=outcome.tokens_output,
        cost_usd=outcome.cost_usd,
        drift_ids=int(stats_drift.get("ids", 0) or 0),
        drift_classes=int(stats_drift.get("classes", 0) or 0),
        drift_textos=int(stats_drift.get("textos", 0) or 0),
        drift_envolvidos=int(stats_drift.get("envolvidos", 0) or 0),
        drift_iscas=int(stats_drift.get("iscas", 0) or 0),
    )
