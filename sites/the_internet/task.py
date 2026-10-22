"""
Tarefa: interação com conteúdo dinâmico em The Internet
(the-internet.herokuapp.com), mantido por Dave Haeffner - clássico de
tutoriais de automação, estrutura estável há anos.

login -> página "Add/Remove Elements" -> adiciona 3 elementos via JS (sem
recarregar a página) -> remove 1 -> confirma que sobraram 2.

Login: usuário/senha fixos e públicos, documentados na própria página
(tomsmith / SuperSecretPassword!).
"""

from __future__ import annotations

import argparse

from common.drift import apply_drift
from common.oracle import (AcumuladorDrift, TaskResult,
                           expect_elementos_adicionados,
                           expect_text_visible, navegar, novo_contexto,
                           run_task_safely)

SITE = "the_internet"
TASK = "conteudo_dinamico"
LOGIN_URL = "https://the-internet.herokuapp.com/login"
ADD_REMOVE_URL = "https://the-internet.herokuapp.com/add_remove_elements/"

USERNAME = "tomsmith"
PASSWORD = "SuperSecretPassword!"


def _steps(page, counter, drift_level: float, seed: int, acumulador, drift_type: str) -> None:
    navegar(page, LOGIN_URL)
    counter.step("goto_login")

    if drift_level > 0:
        # seed derivada da repetição: cada repetição sofre uma mutação DIFERENTE
        # (gera variância entre repetições), mas continua reprodutível.
        acumulador.somar(apply_drift(page, drift_level, seed=seed, tipo=drift_type))
        counter.step("apply_drift")

    page.locator("#username").fill(USERNAME)
    counter.step("fill_username")
    page.locator("#password").fill(PASSWORD)
    counter.step("fill_password")
    page.locator('button[type="submit"]').click()
    counter.step("click_login")

    # este site faz navegação de página inteira no login (ao contrário do SauceDemo,
    # que é uma SPA). Sem esperar a nova URL, a verificação abaixo pode correr contra
    # a página antiga e falhar de forma intermitente.
    page.wait_for_url("**/secure", timeout=15000)
    counter.step("assert_secure_url")

    expect_text_visible(page, "You logged into a secure area!")
    counter.step("assert_login_success")

    navegar(page, ADD_REMOVE_URL)
    counter.step("goto_add_remove")

    if drift_level > 0:
        # reaplica em cima da nova página (o drift anterior não persiste após navegação)
        acumulador.somar(apply_drift(page, drift_level, seed=seed, tipo=drift_type))
        counter.step("apply_drift_add_remove")

    add_button = page.locator('button[onclick="addElement()"]')
    for _ in range(3):
        add_button.click()
        counter.step("click_add_element")

    # AÇÃO pela estratégia do braço (classe CSS, acoplada à implementação);
    # CONFERÊNCIA neutra, idêntica nos três braços. Ver a nota em
    # `expect_elementos_adicionados`.
    delete_buttons = page.locator(".added-manually")
    expect_elementos_adicionados(page, 3)
    counter.step("assert_three_added")

    delete_buttons.first.click()
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
            approach="deterministic",
            drift_level=drift_level,
            repetition=repetition,
            drift_type=drift_type,
            fn=lambda counter: _steps(page, counter, drift_level, repetition, acumulador, drift_type),
        )
        acumulador.aplicar(result)
    finally:
        context.close()
    return result


if __name__ == "__main__":
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description="Smoke test da tarefa The Internet")
    parser.add_argument("--headed", action="store_true", help="abre o navegador visível")
    parser.add_argument("--drift", type=float, default=0.0, help="nível de drift 0.0-1.0")
    parser.add_argument("--tipo", type=str, default="cosmetico",
                        choices=["cosmetico", "estrutural", "semantico"])
    args = parser.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        result = run(browser, drift_level=args.drift, drift_type=args.tipo)
        browser.close()

    print(result)
