"""
Braço DETERMINÍSTICO SEMÂNTICO — SauceDemo.

Mesmo fluxo do task.py, mas localizando elementos por papel, rótulo e texto
(get_by_role, get_by_label, get_by_placeholder, get_by_text) em vez de id e
classe CSS.

Por que isto é um braço próprio do experimento: o drift injetado renomeia ids e
classes, mas preserva papel, texto e atributos semânticos — exatamente como
ocorre quando uma equipe refatora o CSS ou troca de framework sem mudar o
significado da interface. A hipótese é que este braço fique entre o
determinístico frágil e o agêntico em robustez, com custo de execução idêntico
ao frágil (zero chamadas de API).
"""

from __future__ import annotations

import argparse

from common.drift import apply_drift
from common.oracle import navegar, AcumuladorDrift, TaskResult, novo_contexto, expect_carrinho, expect_text_visible, run_task_safely

SITE = "saucedemo"
TASK = "transacional_checkout"
URL = "https://www.saucedemo.com/"

USERNAME = "standard_user"
PASSWORD = "secret_sauce"


def _steps(page, counter, drift_level: float, seed: int, acumulador, drift_type: str) -> None:
    navegar(page, URL)
    counter.step("goto")

    def degradar(onde: str) -> None:
        """Reaplica o drift a cada página: ele não sobrevive a uma navegação.

        Sem isto, apenas a primeira página seria degradada, e este braço
        enfrentaria menos drift que o braço self-healing — o que invalidaria a
        comparação entre eles.
        """
        if drift_level > 0:
            acumulador.somar(apply_drift(page, drift_level, seed=seed, tipo=drift_type))
            # rótulo por página: sem isso, uma falha após o drift não indica em
            # qual tela do fluxo ela ocorreu, já que o drift é reaplicado a cada
            # navegação e todas as ocorrências teriam o mesmo nome.
            counter.step(f"apply_drift_{onde}")

    degradar("login")

    page.get_by_placeholder("Username", exact=True).fill(USERNAME)
    counter.step("fill_username")
    page.get_by_placeholder("Password", exact=True).fill(PASSWORD)
    counter.step("fill_password")
    page.get_by_role("button", name="Login", exact=True).click()
    counter.step("click_login")

    page.wait_for_url("**/inventory.html", timeout=8000)
    counter.step("assert_inventory_url")
    degradar("inventario")

    page.get_by_role("button", name="Add to cart", exact=True).first.click()
    counter.step("add_to_cart")

    # LIMITAÇÃO DOCUMENTADA: o link do carrinho é <a> SEM href, portanto não
    # possui papel de link no ARIA, e não tem aria-label nem texto próprio (só o
    # número do contador). Não existe localizador semântico para ele.
    #
    # Este é um achado do experimento, não um contorno improvisado: a estratégia
    # semântica depende de a aplicação ser construída de forma acessível, e sua
    # cobertura é limitada quando não é. Onde a semântica não existe, recorre-se
    # ao atributo de teste — a alternativa recomendada pela prática de mercado.
    page.locator('[data-test="shopping-cart-link"]').click()
    counter.step("open_cart")

    # mesma conferência do braço frágil, pelo estado da aplicação
    expect_carrinho(page, 1)
    counter.step("assert_cart_count")

    page.wait_for_url("**/cart.html", timeout=8000)
    counter.step("assert_cart_url")
    degradar("carrinho")

    page.get_by_role("button", name="Checkout", exact=True).click()
    counter.step("click_checkout")

    page.get_by_placeholder("First Name", exact=True).fill("Luca")
    counter.step("fill_first_name")
    page.get_by_placeholder("Last Name", exact=True).fill("Salcalde")
    counter.step("fill_last_name")
    page.get_by_placeholder("Zip/Postal Code", exact=True).fill("01000-000")
    counter.step("fill_postal_code")
    page.get_by_role("button", name="Continue", exact=True).click()
    counter.step("click_continue")

    page.get_by_role("button", name="Finish", exact=True).click()
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

    parser = argparse.ArgumentParser(description="Smoke test semântico — SauceDemo")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--drift", type=float, default=0.0)
    parser.add_argument("--tipo", type=str, default="cosmetico",
                        choices=["cosmetico", "estrutural", "semantico"])
    args = parser.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        print(run(browser, drift_level=args.drift, drift_type=args.tipo))
        browser.close()
