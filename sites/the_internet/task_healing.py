"""
Braço SELF-HEALING — The Internet.

Ver explicação metodológica em sites/saucedemo/task_healing.py.
"""

from __future__ import annotations

import argparse
import os

from common.drift import apply_drift
from common.healing import SessaoHealing, carregar_impressoes
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

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FINGERPRINTS = os.path.join(RAIZ, "data", "fingerprints", "the_internet.json")

ALVOS = {
    "campo_usuario": ("#username", "login"),
    "campo_senha": ("#password", "login"),
    "botao_login": ('button[type="submit"]', "login"),
    "botao_adicionar": ('button[onclick="addElement()"]', "add_remove"),
}


def _steps(page, counter, drift_level: float, seed: int, acumulador, sessao_ref: list,
           drift_type: str) -> None:
    impressoes = carregar_impressoes(FINGERPRINTS)
    sessao = SessaoHealing(page, impressoes)
    sessao_ref.append(sessao)

    def degradar() -> None:
        """Reaplica o drift: ele não sobrevive a uma navegação de página."""
        if drift_level > 0:
            acumulador.somar(apply_drift(page, drift_level, seed=seed, tipo=drift_type))
            counter.step("apply_drift")

    navegar(page, LOGIN_URL)
    counter.step("goto_login")
    degradar()

    sessao.localizar("campo_usuario").fill(USERNAME)
    counter.step("fill_username")
    sessao.localizar("campo_senha").fill(PASSWORD)
    counter.step("fill_password")
    sessao.localizar("botao_login").click()
    counter.step("click_login")

    page.wait_for_url("**/secure", timeout=15000)
    counter.step("assert_secure_url")
    expect_text_visible(page, "You logged into a secure area!")
    counter.step("assert_login_success")

    navegar(page, ADD_REMOVE_URL)
    counter.step("goto_add_remove")
    degradar()

    for _ in range(3):
        sessao.localizar("botao_adicionar").click()
        counter.step("click_add_element")

    # os elementos adicionados são criados em tempo de execução e não existem na
    # linha de base, portanto não têm impressão digital: são localizados
    # diretamente, como no braço frágil.
    adicionados = page.locator(".added-manually")
    expect_elementos_adicionados(page, 3)
    counter.step("assert_three_added")

    adicionados.first.click()
    counter.step("click_delete_one")

    expect_elementos_adicionados(page, 2)
    counter.step("assert_two_remaining")


def run(browser, drift_level: float = 0.0, repetition: int = 0,
        drift_type: str = "cosmetico") -> TaskResult:
    context = novo_contexto(browser)
    page = context.new_page()
    acumulador = AcumuladorDrift()
    sessao_ref: list = []
    try:
        result = run_task_safely(
            site=SITE,
            task=TASK,
            approach="healing",
            drift_level=drift_level,
            repetition=repetition,
            drift_type=drift_type,
            fn=lambda counter: _steps(page, counter, drift_level, repetition, acumulador, sessao_ref, drift_type),
        )
        acumulador.aplicar(result)
        if sessao_ref:
            e = sessao_ref[0].estatisticas
            result.heal_acionamentos = e["acionamentos"]
            result.heal_curados = e["curados"]
            result.heal_desistencias = e["desistencias"]
            result.heal_corretos = e["corretos"]
            result.heal_errados = e["errados"]
            result.heal_indeterminados = e["indeterminados"]
    finally:
        context.close()
    return result


if __name__ == "__main__":
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description="Smoke test healing — The Internet")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--drift", type=float, default=0.5)
    parser.add_argument("--tipo", type=str, default="cosmetico",
                        choices=["cosmetico", "estrutural", "semantico"])
    args = parser.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        print(run(browser, drift_level=args.drift, drift_type=args.tipo))
        browser.close()
