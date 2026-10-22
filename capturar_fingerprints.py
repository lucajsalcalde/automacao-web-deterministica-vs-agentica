"""
Captura a linha de base do braço self-healing.

Percorre cada site SEM drift e grava a "impressão digital" de cada elemento-alvo
(tag, atributos semânticos, texto, classes e posição) em data/fingerprints/.

Rode UMA VEZ antes da coleta, e novamente se algum site mudar de layout — a
linha de base precisa refletir a página íntegra.

Uso:
    python capturar_fingerprints.py
    python capturar_fingerprints.py --headed    # para acompanhar visualmente
"""

from __future__ import annotations

import argparse
import os

from common.healing import capturar, salvar_impressoes

RAIZ = os.path.dirname(os.path.abspath(__file__))
DESTINO = os.path.join(RAIZ, "data", "fingerprints")


def capturar_saucedemo(browser, headed: bool) -> dict:
    from sites.saucedemo.task_healing import ALVOS

    ctx = browser.new_context()
    page = ctx.new_page()
    impressoes = {}
    try:
        # navega até cada estado da aplicação e captura os alvos daquele estado
        page.goto("https://www.saucedemo.com/", wait_until="domcontentloaded")
        for nome, (seletor, tela) in ALVOS.items():
            if tela == "login":
                impressoes[nome] = capturar(page, nome, seletor)

        page.locator("#user-name").fill("standard_user")
        page.locator("#password").fill("secret_sauce")
        page.locator("#login-button").click()
        page.wait_for_url("**/inventory.html", timeout=15000)
        impressoes["botao_add_carrinho"] = capturar(
            page, "botao_add_carrinho", ALVOS["botao_add_carrinho"][0])

        # IMPORTANTE: o link do carrinho é capturado DEPOIS de adicionar o item.
        # Com o carrinho vazio ele não exibe o contador, e sua impressão digital
        # registraria texto vazio — enquanto em uso o elemento exibe "1". Essa
        # divergência derrubava a pontuação de similaridade (0,586, abaixo do
        # limiar) e fazia o self-healing desistir de um elemento que existia.
        # A linha de base precisa refletir o MESMO estado em que o alvo é usado.
        page.locator('button[id^="add-to-cart"]').first.click()
        impressoes["link_carrinho"] = capturar(
            page, "link_carrinho", ALVOS["link_carrinho"][0])

        page.locator(".shopping_cart_link").click()
        page.wait_for_url("**/cart.html", timeout=15000)
        for nome, (seletor, tela) in ALVOS.items():
            if tela == "cart":
                impressoes[nome] = capturar(page, nome, seletor)

        page.locator('#checkout').click()
        page.wait_for_url("**/checkout-step-one.html", timeout=15000)
        for nome, (seletor, tela) in ALVOS.items():
            if tela == "checkout_step_one":
                impressoes[nome] = capturar(page, nome, seletor)

        page.locator("#first-name").fill("Luca")
        page.locator("#last-name").fill("Salcalde")
        page.locator("#postal-code").fill("01000-000")
        page.locator('#continue').click()
        page.wait_for_url("**/checkout-step-two.html", timeout=15000)
        for nome, (seletor, tela) in ALVOS.items():
            if tela == "checkout_step_two":
                impressoes[nome] = capturar(page, nome, seletor)
    finally:
        ctx.close()
    return impressoes


def capturar_the_internet(browser, headed: bool) -> dict:
    from sites.the_internet.task_healing import ALVOS

    ctx = browser.new_context()
    page = ctx.new_page()
    impressoes = {}
    try:
        page.goto("https://the-internet.herokuapp.com/login",
                  wait_until="domcontentloaded")
        for nome, (seletor, tela) in ALVOS.items():
            if tela == "login":
                impressoes[nome] = capturar(page, nome, seletor)

        page.goto("https://the-internet.herokuapp.com/add_remove_elements/",
                  wait_until="domcontentloaded")
        for nome, (seletor, tela) in ALVOS.items():
            if tela == "add_remove":
                impressoes[nome] = capturar(page, nome, seletor)
    finally:
        ctx.close()
    return impressoes


def main() -> None:
    ap = argparse.ArgumentParser(description="Captura impressões digitais dos alvos")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        try:
            for nome_site, funcao in (("saucedemo", capturar_saucedemo),
                                      ("the_internet", capturar_the_internet)):
                print(f"\n--- {nome_site} ---")
                impressoes = funcao(browser, args.headed)
                caminho = os.path.join(DESTINO, f"{nome_site}.json")
                salvar_impressoes(impressoes, caminho)
                for nome, fp in impressoes.items():
                    resumo = (fp.atributos.get("name") or fp.atributos.get("data_test")
                              or fp.atributos.get("placeholder") or fp.texto[:20] or "-")
                    print(f"  {nome:22} {fp.tag:8} {resumo}")
                print(f"  -> {len(impressoes)} alvos salvos em {caminho}")
        finally:
            browser.close()

    print("\nPronto. Agora rode:  python calibrar_limiar.py\n")


if __name__ == "__main__":
    main()
