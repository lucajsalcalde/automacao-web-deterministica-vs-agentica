"""
Braço XPATH — The Internet.

Ver a explicação metodológica em sites/saucedemo/task_xpath.py.

Uma observação específica deste site: os elementos removíveis são criados em
tempo de execução, DEPOIS de o drift ter sido aplicado. O caminho capturado para
o primeiro deles vale enquanto a estrutura acima não mudar — e é justamente essa
dependência que o drift estrutural rompe.
"""

from __future__ import annotations

import argparse
import json
import os

from common.drift import apply_drift
from common.oracle import (AcumuladorDrift, TaskResult,
                           expect_elementos_adicionados,
                           expect_text_visible, navegar, novo_contexto,
                           run_task_safely)

SITE = "the_internet"
TASK = "conteudo_dinamico"
LOGIN_URL = "https://the-internet.herokuapp.com/login"
ADD_REMOVE_URL = "https://the-internet.herokuapp.com/add_remove_elements/"

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CAMINHOS = os.path.join(RAIZ, "data", "xpaths", "the_internet.json")

USERNAME = "tomsmith"
PASSWORD = "SuperSecretPassword!"


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
        return page.locator("xpath=" + xp[nome])

    def degradar() -> None:
        if drift_level > 0:
            acumulador.somar(apply_drift(page, drift_level, seed=seed, tipo=drift_type))
            counter.step("apply_drift")

    navegar(page, LOGIN_URL)
    counter.step("goto_login")
    degradar()

    alvo("campo_usuario").fill(USERNAME)
    counter.step("fill_username")
    alvo("campo_senha").fill(PASSWORD)
    counter.step("fill_password")
    alvo("botao_login").click()
    counter.step("click_login")

    page.wait_for_url("**/secure", timeout=15000)
    counter.step("assert_secure_url")
    expect_text_visible(page, "You logged into a secure area!")
    counter.step("assert_login_success")

    navegar(page, ADD_REMOVE_URL)
    counter.step("goto_add_remove")
    degradar()

    for _ in range(3):
        alvo("botao_adicionar").click()
        counter.step("click_add_element")

    expect_elementos_adicionados(page, 3)
    counter.step("assert_three_added")

    # AÇÃO pelo caminho capturado; CONFERÊNCIA neutra logo abaixo.
    alvo("botao_remover").click()
    counter.step("click_delete_one")

    expect_elementos_adicionados(page, 2)
    counter.step("assert_two_remaining")


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

    parser = argparse.ArgumentParser(description="Smoke test XPath — The Internet")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--drift", type=float, default=0.5)
    parser.add_argument("--tipo", type=str, default="cosmetico",
                        choices=["cosmetico", "estrutural", "semantico"])
    args = parser.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        print(run(browser, drift_level=args.drift, drift_type=args.tipo))
        browser.close()
