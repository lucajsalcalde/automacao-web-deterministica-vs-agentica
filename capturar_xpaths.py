"""
Captura os caminhos XPath absolutos dos elementos-alvo, na página ÍNTEGRA.

    python capturar_xpaths.py

POR QUE UM BRAÇO POR XPATH
--------------------------
O espectro determinístico do experimento tinha três estratégias de localização:
identidade (id e classe), significado (papel acessível e rótulo) e similaridade
(self-healing). Faltava a quarta, e é a única sensível a reorganização do DOM:
o CAMINHO até o elemento.

    /html/body[1]/div[1]/div[1]/div[2]/div[1]/div[1]/div[1]/form[1]/input[1]

É um localizador nativo do Selenium (`By.XPATH`) e do Playwright, e a
documentação deste último recomenda explicitamente evitá-lo em favor de papéis
acessíveis. O experimento passa a testar essa recomendação em vez de repeti-la.

A ausência desse braço tinha uma consequência concreta e medida: a condição de
drift estrutural produzia resultado idêntico à cosmética em 191 de 192 pares
comparados. Ela mexia na árvore, mas nenhuma estratégia se ancorava na árvore.

COMO O DRIFT ESTRUTURAL O DESAFIA
---------------------------------
A inserção de um irmão de mesma tag desloca o índice do alvo: `input[1]` passa a
ser `input[2]`. Verificado no navegador — após a inserção, o XPath do campo de
usuário do SauceDemo resolvia para a cópia oculta, não para o campo real. O
localizador não deixa de encontrar algo; ele encontra o elemento ERRADO, que é a
falha mais perigosa.

A captura é feita na página sem drift, como acontece com um localizador gravado
por ferramenta antes de a interface mudar.
"""

from __future__ import annotations

import json
import os

RAIZ = os.path.dirname(os.path.abspath(__file__))
DESTINO = os.path.join(RAIZ, "data", "xpaths")

# Gera o caminho absoluto no formato que gravadores produzem: índice por
# posição entre irmãos de mesma tag.
_CAMINHO_JS = """
(seletor) => {
  const el = document.querySelector(seletor);
  if (!el) return null;
  const partes = [];
  let n = el;
  while (n && n.nodeType === 1 && n.tagName !== 'HTML') {
    let i = 1, irmao = n.previousElementSibling;
    while (irmao) { if (irmao.tagName === n.tagName) i++; irmao = irmao.previousElementSibling; }
    partes.unshift(n.tagName.toLowerCase() + '[' + i + ']');
    n = n.parentElement;
  }
  return '/html/' + partes.join('/');
}
"""

SAUCEDEMO = "https://www.saucedemo.com/"
TI_LOGIN = "https://the-internet.herokuapp.com/login"
TI_ADD = "https://the-internet.herokuapp.com/add_remove_elements/"


def _capturar(page, alvos: dict) -> dict:
    saida = {}
    for nome, seletor in alvos.items():
        caminho = page.evaluate(_CAMINHO_JS, seletor)
        if caminho is None:
            raise RuntimeError(
                f"elemento '{nome}' não encontrado pelo seletor {seletor!r} — "
                "a página mudou desde a última captura?")
        saida[nome] = caminho
        print(f"    {nome:<22}{caminho}")
    return saida


def capturar_saucedemo(page) -> dict:
    print("\nSAUCEDEMO")
    page.goto(SAUCEDEMO, wait_until="domcontentloaded")
    page.wait_for_selector('[data-test="username"]')
    xp = _capturar(page, {
        "campo_usuario": '[data-test="username"]',
        "campo_senha": '[data-test="password"]',
        "botao_login": '[data-test="login-button"]',
    })

    page.locator('[data-test="username"]').fill("standard_user")
    page.locator('[data-test="password"]').fill("secret_sauce")
    page.locator('[data-test="login-button"]').click()
    page.wait_for_url("**/inventory.html", timeout=15000)
    xp.update(_capturar(page, {
        "botao_add_carrinho": 'button[id^="add-to-cart"]',
        "link_carrinho": '[data-test="shopping-cart-link"]',
    }))

    # o link do carrinho muda de conteúdo quando há item; captura com 1 item,
    # que é o estado em que a automação o encontra
    page.locator('button[id^="add-to-cart"]').first.click()
    page.locator('[data-test="shopping-cart-link"]').click()
    page.wait_for_url("**/cart.html", timeout=15000)
    xp.update(_capturar(page, {"botao_checkout": '[data-test="checkout"]'}))

    page.locator('[data-test="checkout"]').click()
    page.wait_for_url("**/checkout-step-one.html", timeout=15000)
    xp.update(_capturar(page, {
        "campo_nome": '[data-test="firstName"]',
        "campo_sobrenome": '[data-test="lastName"]',
        "campo_cep": '[data-test="postalCode"]',
        "botao_continuar": '[data-test="continue"]',
    }))

    page.locator('[data-test="firstName"]').fill("Luca")
    page.locator('[data-test="lastName"]').fill("Salcalde")
    page.locator('[data-test="postalCode"]').fill("01000-000")
    page.locator('[data-test="continue"]').click()
    page.wait_for_url("**/checkout-step-two.html", timeout=15000)
    xp.update(_capturar(page, {"botao_finalizar": '[data-test="finish"]'}))
    return xp


def capturar_the_internet(page) -> dict:
    print("\nTHE INTERNET")
    page.goto(TI_LOGIN, wait_until="domcontentloaded")
    xp = _capturar(page, {
        "campo_usuario": "#username",
        "campo_senha": "#password",
        "botao_login": 'button[type="submit"]',
    })

    page.goto(TI_ADD, wait_until="domcontentloaded")
    xp.update(_capturar(page, {"botao_adicionar": 'button[onclick="addElement()"]'}))

    # o botão de remover só existe depois de adicionar; captura no estado em que
    # a automação vai encontrá-lo
    page.locator('button[onclick="addElement()"]').click()
    xp.update(_capturar(page, {"botao_remover": ".added-manually"}))
    return xp


def main() -> None:
    from playwright.sync_api import sync_playwright

    os.makedirs(DESTINO, exist_ok=True)
    print("=" * 78)
    print("CAPTURA DE CAMINHOS XPATH — páginas sem drift")
    print("=" * 78)

    with sync_playwright() as p:
        navegador = p.chromium.launch(headless=True)
        try:
            for nome, funcao in (("saucedemo", capturar_saucedemo),
                                 ("the_internet", capturar_the_internet)):
                contexto = navegador.new_context()
                pagina = contexto.new_page()
                try:
                    xpaths = funcao(pagina)
                finally:
                    contexto.close()
                caminho = os.path.join(DESTINO, f"{nome}.json")
                with open(caminho, "w", encoding="utf-8") as f:
                    json.dump(xpaths, f, ensure_ascii=False, indent=1)
                print(f"  -> {len(xpaths)} caminhos salvos em {caminho}")
        finally:
            navegador.close()

    print("\nPróximo passo: python validar_instrumento.py --bracos xpath\n")


if __name__ == "__main__":
    main()
