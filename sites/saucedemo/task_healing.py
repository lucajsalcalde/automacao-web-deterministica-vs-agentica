"""
Braço SELF-HEALING — SauceDemo.

Mesmo fluxo e MESMOS seletores do braço determinístico frágil (task.py). A única
diferença é que cada localizador passa pela SessaoHealing: se o seletor original
não encontrar nada, o elemento é reencontrado por similaridade com a impressão
digital capturada numa execução de referência.

Isso isola exatamente a variável de interesse: frágil e healing usam a mesma
estratégia de seletor; o que muda é apenas existir ou não a recuperação.
"""

from __future__ import annotations

import argparse
import os

from common.drift import apply_drift
from common.healing import SessaoHealing, carregar_impressoes
from common.oracle import navegar, AcumuladorDrift, TaskResult, novo_contexto, expect_carrinho, expect_text_visible, run_task_safely

SITE = "saucedemo"
TASK = "transacional_checkout"
URL = "https://www.saucedemo.com/"

USERNAME = "standard_user"
PASSWORD = "secret_sauce"

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FINGERPRINTS = os.path.join(RAIZ, "data", "fingerprints", "saucedemo.json")

# Alvos nomeados. As chaves são usadas pelo healing e pelo script de captura;
# os valores são os mesmos seletores do braço frágil.
ALVOS = {
    "campo_usuario": ("#user-name", "login"),
    "campo_senha": ("#password", "login"),
    "botao_login": ("#login-button", "login"),
    "botao_add_carrinho": ('button[id^="add-to-cart"]', "inventory"),
    "link_carrinho": (".shopping_cart_link", "inventory"),
    "botao_checkout": ("#checkout", "cart"),
    "campo_nome": ("#first-name", "checkout_step_one"),
    "campo_sobrenome": ("#last-name", "checkout_step_one"),
    "campo_cep": ("#postal-code", "checkout_step_one"),
    "botao_continuar": ("#continue", "checkout_step_one"),
    "botao_finalizar": ("#finish", "checkout_step_two"),
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

    navegar(page, URL)
    counter.step("goto")
    degradar()

    sessao.localizar("campo_usuario").fill(USERNAME)
    counter.step("fill_username")
    sessao.localizar("campo_senha").fill(PASSWORD)
    counter.step("fill_password")
    sessao.localizar("botao_login").click()
    counter.step("click_login")

    page.wait_for_url("**/inventory.html", timeout=8000)
    counter.step("assert_inventory_url")
    degradar()

    sessao.localizar("botao_add_carrinho").click()
    counter.step("add_to_cart")
    sessao.localizar("link_carrinho").click()
    counter.step("open_cart")

    # mesma conferência do braço frágil, pelo estado da aplicação
    expect_carrinho(page, 1)
    counter.step("assert_cart_count")

    page.wait_for_url("**/cart.html", timeout=8000)
    counter.step("assert_cart_url")
    degradar()

    sessao.localizar("botao_checkout").click()
    counter.step("click_checkout")

    page.wait_for_url("**/checkout-step-one.html", timeout=8000)
    degradar()

    sessao.localizar("campo_nome").fill("Luca")
    counter.step("fill_first_name")
    sessao.localizar("campo_sobrenome").fill("Salcalde")
    counter.step("fill_last_name")
    sessao.localizar("campo_cep").fill("01000-000")
    counter.step("fill_postal_code")
    sessao.localizar("botao_continuar").click()
    counter.step("click_continue")

    page.wait_for_url("**/checkout-step-two.html", timeout=8000)
    degradar()

    sessao.localizar("botao_finalizar").click()
    counter.step("click_finish")

    expect_text_visible(page, "Thank you for your order!")
    counter.step("assert_confirmation")


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
        # estatísticas de healing em colunas próprias do CSV, para permitir
        # analisar quantas vezes o seletor quebrou e com que taxa foi recuperado
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

    parser = argparse.ArgumentParser(description="Smoke test healing — SauceDemo")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--drift", type=float, default=0.5)
    parser.add_argument("--tipo", type=str, default="cosmetico",
                        choices=["cosmetico", "estrutural", "semantico"])
    args = parser.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        print(run(browser, drift_level=args.drift, drift_type=args.tipo))
        browser.close()
