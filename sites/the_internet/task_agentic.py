"""
Versão AGÊNTICA da tarefa do The Internet.

Mesma tarefa do task.py (login -> adicionar 3 elementos dinâmicos -> remover 1),
descrita em linguagem natural, sem informar seletores.
"""

from __future__ import annotations

import argparse
import asyncio

from common.agentic_base import run_agentic_task
from common.oracle import (ELEMENTOS_ADICIONADOS, TaskResult,
                           contagem_elementos, login_confirmado)

SITE = "the_internet"
TASK = "conteudo_dinamico"
LOGIN_URL = "https://the-internet.herokuapp.com/login"

PROMPT = """No site aberto no navegador, faça o seguinte:

1. Faça login com o usuário "tomsmith" e a senha "SuperSecretPassword!".
2. Depois de logado, navegue até a página https://the-internet.herokuapp.com/add_remove_elements/
3. Nessa página, adicione exatamente 3 elementos.
4. Em seguida, remova exatamente 1 deles.

A tarefa só está concluída quando restarem exatamente 2 elementos adicionados na página."""


async def _oracle(page) -> None:
    """
    Mesma conferência dos braços determinísticos (coleta 2).

    Na coleta 1 este oráculo só olhava o estado final da página Add/Remove, que
    é pública: numa execução, o próprio agente relatou que não conseguiu fazer
    login, seguiu adiante e foi contado como sucesso. Agora ele precisa ter
    chegado à página segura com a mensagem de sucesso, adicionado exatamente 3
    elementos e removido exatamente 1, como os braços determinísticos.
    """
    if not await login_confirmado(page):
        raise AssertionError(
            "login não confirmado: a página /secure com a mensagem de sucesso "
            "não foi exibida nesta aba")
    if "add_remove_elements" not in page.url:
        raise AssertionError(f"Esperava estar na página add_remove_elements, mas está em '{page.url}'")
    contagem = await contagem_elementos(page)
    if contagem.get("adicionados") != 3 or contagem.get("removidos") != 1:
        raise AssertionError(
            f"Esperava 3 elementos adicionados e 1 removido nesta página; "
            f"registrou {contagem.get('adicionados')} e {contagem.get('removidos')}")
    restantes = await page.locator(ELEMENTOS_ADICIONADOS).count()
    if restantes != 2:
        raise AssertionError(f"Esperava 2 elementos restantes, encontrou {restantes}")


async def run_async(drift_level: float = 0.0, repetition: int = 0,
                    headless: bool = True,
                    drift_type: str = "cosmetico") -> TaskResult:
    return await run_agentic_task(
        site=SITE,
        task=TASK,
        start_url=LOGIN_URL,
        task_prompt=PROMPT,
        oracle=_oracle,
        drift_level=drift_level,
        drift_type=drift_type,
        repetition=repetition,
        headless=headless,
        transcript_dir="data/transcripts",
    )


def run(browser=None, drift_level: float = 0.0, repetition: int = 0,
        drift_type: str = "cosmetico") -> TaskResult:
    """Mesma assinatura do braço determinístico (ver nota em saucedemo/task_agentic.py)."""
    return asyncio.run(run_async(drift_level=drift_level, repetition=repetition,
                                 drift_type=drift_type))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Smoke test agêntico — The Internet")
    parser.add_argument("--headed", action="store_true", help="abre o navegador visível")
    parser.add_argument("--drift", type=float, default=0.0, help="nível de drift 0.0-1.0")
    parser.add_argument("--tipo", type=str, default="cosmetico",
                        choices=["cosmetico", "estrutural", "semantico"])
    args = parser.parse_args()

    result = asyncio.run(run_async(drift_level=args.drift, headless=not args.headed, drift_type=args.tipo))
    print(result)
