"""
Braço XPATH — SauceDemo.

Quarta estratégia determinística: localizar pelo CAMINHO até o elemento na
árvore do documento, e não por identidade, significado ou similaridade.

    /html/body[1]/div[1]/div[1]/div[2]/div[1]/div[1]/div[1]/form[1]/input[1]

Modela a automação gerada por gravadores e por ferramentas que registram a
posição do elemento. É localizador nativo do Selenium (`By.XPATH`) e do
Playwright, cuja documentação recomenda evitá-lo — recomendação que este braço
existe para testar.

COMPORTAMENTO ESPERADO
----------------------
    cosmético   sobrevive. Renomear id e classe não altera o caminho.
    estrutural  QUEBRA. Inserir um irmão de mesma tag desloca o índice, e o
                caminho passa a apontar para outro elemento.
    semântico   quebra também, por ser cumulativo sobre o estrutural.

É o inverso do braço frágil, que quebra no cosmético e seria indiferente à
reorganização. Os dois juntos mostram que não existe seletor determinístico
robusto a tudo: cada um é imune a um tipo de mudança e vulnerável a outro.
"""

from __future__ import annotations

import argparse
import json
import os

from common.drift import apply_drift
from common.oracle import (AcumuladorDrift, TaskResult, expect_carrinho,
                           expect_text_visible, navegar, novo_contexto,
                           run_task_safely)

SITE = "saucedemo"
TASK = "transacional_checkout"
URL = "https://www.saucedemo.com/"

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CAMINHOS = os.path.join(RAIZ, "data", "xpaths", "saucedemo.json")

USERNAME = "standard_user"
PASSWORD = "secret_sauce"


def carregar_xpaths() -> dict:
    if not os.path.exists(CAMINHOS):
        raise FileNotFoundError(
            f"{CAMINHOS} não encontrado — rode antes: python capturar_xpaths.py")
    with open(CAMINHOS, encoding="utf-8") as f:
        return json.load(f)


def _steps(page, counter, drift_level: float, seed: int, acumulador,
           drift_type: str) -> None:
    xp = carregar_xpaths()

    def alvo(nome: str):
        # O prefixo "xpath=" é explícito para deixar claro no código qual
        # estratégia está em uso; o Playwright também aceita a string crua.
        return page.locator("xpath=" + xp[nome])

    def degradar() -> None:
        if drift_level > 0:
            acumulador.somar(apply_drift(page, drift_level, seed=seed, tipo=drift_type))
            counter.step("apply_drift")

    navegar(page, URL)
    counter.step("goto")
    degradar()

    alvo("campo_usuario").fill(USERNAME)
    counter.step("fill_username")
    alvo("campo_senha").fill(PASSWORD)
    counter.step("fill_password")
    alvo("botao_login").click()
    counter.step("click_login")

    page.wait_for_url("**/inventory.html", timeout=8000)
    counter.step("assert_inventory_url")
    degradar()

    alvo("botao_add_carrinho").click()
    counter.step("add_to_cart")
    alvo("link_carrinho").click()
    counter.step("open_cart")
    page.wait_for_url("**/cart.html", timeout=8000)
    degradar()

    expect_carrinho(page, 1)
    counter.step("assert_cart_count")

    alvo("botao_checkout").click()
    counter.step("click_checkout")
    page.wait_for_url("**/checkout-step-one.html", timeout=8000)
    degradar()

    alvo("campo_nome").fill("Luca")
    counter.step("fill_first_name")
    alvo("campo_sobrenome").fill("Salcalde")
    counter.step("fill_last_name")
    alvo("campo_cep").fill("01000-000")
    counter.step("fill_postal_code")
    alvo("botao_continuar").click()
    counter.step("click_continue")

    page.wait_for_url("**/checkout-step-two.html", timeout=8000)
    degradar()

    alvo("botao_finalizar").click()
    counter.step("click_finish")

    expect_text_visible(page, "Thank you for your order!")
    counter.step("assert_confirmation")


def run(browser, drift_level: float = 0.0, repetition: int = 0,
        drift_type: str = "cosmetico") -> TaskResult:
    context = novo_contexto(browser)
    page = context.new_page()
    acumulador = AcumuladorDrift()
    try:
        result = run_task_safely(
            site=SITE,
            task=TASK,
            approach="xpath",
            drift_level=drift_level,
            repetition=repetition,
            drift_type=drift_type,
            fn=lambda counter: _steps(page, counter, drift_level, repetition,
                                      acumulador, drift_type),
        )
        acumulador.aplicar(result)
    finally:
        context.close()
    return result


if __name__ == "__main__":
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description="Smoke test XPath — SauceDemo")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--drift", type=float, default=0.5)
    parser.add_argument("--tipo", type=str, default="cosmetico",
                        choices=["cosmetico", "estrutural", "semantico"])
    args = parser.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        print(run(browser, drift_level=args.drift, drift_type=args.tipo))
        browser.close()
