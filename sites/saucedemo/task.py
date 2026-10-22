"""
Tarefa: fluxo transacional multi-etapa no SauceDemo (saucedemo.com).

login -> adicionar produto ao carrinho -> checkout -> confirmação de pedido.

Seletores baseados na estrutura pública e estável do site (data-test attrs e
ids usados nos tutoriais oficiais/comunidade há anos). Não foi possível
executar este script contra o site real dentro do sandbox usado para
desenvolvê-lo (sem acesso de rede) — valide localmente com `--headed` antes
de rodar a coleta completa.
"""

from __future__ import annotations

import argparse

from common.drift import apply_drift
from common.oracle import (AcumuladorDrift, TaskResult, expect_carrinho,
                           expect_text_visible, navegar, novo_contexto,
                           run_task_safely)

SITE = "saucedemo"
TASK = "transacional_checkout"
URL = "https://www.saucedemo.com/"

USERNAME = "standard_user"
PASSWORD = "secret_sauce"


def _steps(page, counter, drift_level: float, seed: int, acumulador, drift_type: str) -> None:
    navegar(page, URL)
    counter.step("goto")

    def degradar() -> None:
        """Reaplica o drift a cada página: ele não sobrevive a uma navegação.

        A seed é derivada da repetição, então cada repetição sofre uma mutação
        diferente, mas continua reprodutível.
        """
        if drift_level > 0:
            acumulador.somar(apply_drift(page, drift_level, seed=seed, tipo=drift_type))
            counter.step("apply_drift")

    degradar()

    page.locator("#user-name").fill(USERNAME)
    counter.step("fill_username")
    page.locator("#password").fill(PASSWORD)
    counter.step("fill_password")
    page.locator("#login-button").click()
    counter.step("click_login")

    # confirma que o login deu certo antes de continuar
    page.wait_for_url("**/inventory.html", timeout=8000)
    counter.step("assert_inventory_url")
    degradar()

    # IMPORTANTE: este braço usa apenas id e classe CSS — nunca atributos
    # data-test. Atributos de teste são um contrato deliberadamente estável,
    # mantido pelas equipes justamente para sobreviver a refatorações; usá-los
    # aqui tornaria o braço "frágil" imune ao drift e invalidaria a comparação.
    add_to_cart_btn = page.locator('button[id^="add-to-cart"]').first
    add_to_cart_btn.click()
    counter.step("add_to_cart")

    page.locator(".shopping_cart_link").click()
    counter.step("open_cart")
    page.wait_for_url("**/cart.html", timeout=8000)
    degradar()
    # Conferência pelo ESTADO DA APLICAÇÃO, não por seletor de DOM: ver
    # `expect_carrinho` em common/oracle.py. Igual nos quatro braços.
    expect_carrinho(page, 1)
    counter.step("assert_cart_count")

    page.locator('#checkout').click()
    counter.step("click_checkout")
    page.wait_for_url("**/checkout-step-one.html", timeout=8000)
    degradar()

    page.locator("#first-name").fill("Luca")
    counter.step("fill_first_name")
    page.locator("#last-name").fill("Salcalde")
    counter.step("fill_last_name")
    page.locator("#postal-code").fill("01000-000")
    counter.step("fill_postal_code")
    page.locator('#continue').click()
    counter.step("click_continue")
    page.wait_for_url("**/checkout-step-two.html", timeout=8000)
    degradar()

    page.locator('#finish').click()
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

    parser = argparse.ArgumentParser(description="Smoke test da tarefa SauceDemo")
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
