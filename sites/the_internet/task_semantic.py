"""
Braço DETERMINÍSTICO SEMÂNTICO — The Internet.

Mesmo fluxo do task.py, localizando por rótulo, papel e texto em vez de id.
Ver a explicação metodológica em sites/saucedemo/task_semantic.py.
"""

from __future__ import annotations

import argparse
import re

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
        acumulador.somar(apply_drift(page, drift_level, seed=seed, tipo=drift_type))
        counter.step("apply_drift")

    # os campos têm <label for=...>, então o rótulo é o localizador semântico natural
    page.get_by_label("Username", exact=True).fill(USERNAME)
    counter.step("fill_username")
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    counter.step("fill_password")
    # O botão é <button><i class="fa fa-sign-in"> Login</i></button>. O ícone
    # Font Awesome injeta um glifo por CSS (::before), que entra no nome
    # acessível — então `exact=True` com "Login" não casa, embora o rótulo
    # visível para o usuário seja exatamente "Login".
    #
    # A âncora no fim da expressão preserva a intenção do braço semântico:
    # continua exigindo que o rótulo TERMINE em "Login", de modo que o drift
    # semântico (que acrescenta sufixo, virando "Login_123") quebra o
    # localizador, como deve.
    page.get_by_role("button", name=re.compile(r"Login\s*$")).click()
    counter.step("click_login")

    page.wait_for_url("**/secure", timeout=15000)
    counter.step("assert_secure_url")

    expect_text_visible(page, "You logged into a secure area!")
    counter.step("assert_login_success")

    navegar(page, ADD_REMOVE_URL)
    counter.step("goto_add_remove")

    if drift_level > 0:
        acumulador.somar(apply_drift(page, drift_level, seed=seed, tipo=drift_type))
        counter.step("apply_drift_add_remove")

    add_button = page.get_by_role("button", name="Add Element", exact=True)
    for _ in range(3):
        add_button.click()
        counter.step("click_add_element")

    # AÇÃO por papel acessível (a estratégia deste braço); CONFERÊNCIA neutra.
    delete_buttons = page.get_by_role("button", name="Delete", exact=True)
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
            approach="semantic",
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

    parser = argparse.ArgumentParser(description="Smoke test semântico — The Internet")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--drift", type=float, default=0.0)
    parser.add_argument("--tipo", type=str, default="cosmetico",
                        choices=["cosmetico", "estrutural", "semantico"])
    args = parser.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        print(run(browser, drift_level=args.drift, drift_type=args.tipo))
        browser.close()
