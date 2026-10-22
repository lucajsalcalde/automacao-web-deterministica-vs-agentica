"""
Versão AGÊNTICA da tarefa do SauceDemo.

Mesma tarefa do task.py (login -> carrinho -> checkout -> confirmação), mas
descrita em linguagem natural para o agente, sem informar nenhum seletor.
Isso é proposital: a diferença entre os braços deve estar na forma de localizar
os elementos, e é justamente essa diferença que o experimento mede.
"""

from __future__ import annotations

import argparse
import asyncio

from common.agentic_base import run_agentic_task
from common.oracle import TaskResult, carrinho_no_resumo

SITE = "saucedemo"
TASK = "transacional_checkout"
URL = "https://www.saucedemo.com/"

PROMPT = """Na loja aberta no navegador, execute a compra de um produto do início ao fim:

1. Faça login com o usuário "standard_user" e a senha "secret_sauce".
2. Adicione qualquer um dos produtos ao carrinho.
3. Abra o carrinho e inicie o checkout.
4. Preencha os dados solicitados: nome "Luca", sobrenome "Salcalde", CEP "01000-000".
5. Avance e finalize o pedido.

A tarefa só está concluída quando a página de confirmação do pedido for exibida."""


async def _oracle(page) -> None:
    """Verifica o estado final real — independente do que o agente relatou."""
    if "checkout-complete" not in page.url:
        raise AssertionError(f"URL final esperada era a de confirmação, mas é '{page.url}'")
    texto = await page.locator("body").inner_text()
    if "Thank you for your order!" not in texto:
        raise AssertionError("Mensagem de confirmação do pedido não encontrada na página")

    # A confirmação sozinha não basta: o SauceDemo conclui o checkout com o
    # carrinho vazio e exibe exatamente esta mensagem. Verificado no navegador.
    # Sem esta conferência, uma execução em que o agente não conseguiu adicionar
    # o produto seria registrada como sucesso.
    # Coleta 2: mesma exigência dos braços determinísticos, exatamente 1 item
    # no carrinho antes de finalizar. O pico usado na coleta 1 aceitava comprar
    # dois itens, ou adicionar e remover o único antes de concluir.
    itens = await carrinho_no_resumo(page)
    if itens != 1:
        raise AssertionError(
            f"o resumo do pedido tinha {itens} item(ns) no carrinho; "
            "o esperado é exatamente 1")


async def run_async(drift_level: float = 0.0, repetition: int = 0,
                    headless: bool = True,
                    drift_type: str = "cosmetico") -> TaskResult:
    return await run_agentic_task(
        site=SITE,
        task=TASK,
        start_url=URL,
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
    """
    Mesma assinatura do braço determinístico, para o runner tratar os dois de
    forma uniforme. O parâmetro `browser` é ignorado: o braço agêntico precisa
    lançar o próprio navegador com a porta de depuração aberta.
    """
    return asyncio.run(run_async(drift_level=drift_level, repetition=repetition,
                                 drift_type=drift_type))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Smoke test agêntico — SauceDemo")
    parser.add_argument("--headed", action="store_true", help="abre o navegador visível")
    parser.add_argument("--drift", type=float, default=0.0, help="nível de drift 0.0-1.0")
    parser.add_argument("--tipo", type=str, default="cosmetico",
                        choices=["cosmetico", "estrutural", "semantico"])
    args = parser.parse_args()

    result = asyncio.run(run_async(drift_level=args.drift, headless=not args.headed, drift_type=args.tipo))
    print(result)
