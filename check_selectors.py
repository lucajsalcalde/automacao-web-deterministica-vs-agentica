"""
Diagnóstico de seletores — rode ISTO antes do primeiro smoke test.

Diferença para `python -m sites.<site>.task`: o task.py executa o fluxo e para no
primeiro erro. Este script percorre o fluxo verificando CADA seletor e segue em
frente mesmo quando um falha, imprimindo um relatório completo de uma vez só.

Uso:
    python check_selectors.py            # headless
    python check_selectors.py --headed   # navegador visível

Saída esperada: uma linha por seletor, com OK ou FALTANDO. Se algo aparecer como
FALTANDO, é esse seletor que precisa ser ajustado no task.py correspondente.
"""

from __future__ import annotations

import argparse

OK = "OK      "
FAIL = "FALTANDO"


def check(page, label: str, selector: str, results: list) -> bool:
    """Verifica se um seletor encontra pelo menos 1 elemento. Nunca levanta exceção."""
    try:
        count = page.locator(selector).count()
        found = count > 0
        results.append((OK if found else FAIL, label, selector, f"{count} elemento(s)"))
        return found
    except Exception as e:  # noqa: BLE001
        results.append((FAIL, label, selector, f"erro: {type(e).__name__}"))
        return False


def check_saucedemo(browser, results: list) -> None:
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto("https://www.saucedemo.com/", wait_until="domcontentloaded", timeout=30000)
        results.append(("---", "SAUCEDEMO — tela de login", "", page.url))

        check(page, "campo usuário", "#user-name", results)
        check(page, "campo senha", "#password", results)
        check(page, "botão login", "#login-button", results)

        # tenta logar para checar os seletores das telas seguintes
        page.locator("#user-name").fill("standard_user")
        page.locator("#password").fill("secret_sauce")
        page.locator("#login-button").click()
        page.wait_for_url("**/inventory.html", timeout=15000)
        results.append(("---", "SAUCEDEMO — inventário", "", page.url))

        check(page, "botão add-to-cart", '[data-test^="add-to-cart"]', results)
        check(page, "link do carrinho", ".shopping_cart_link", results)

        page.locator('[data-test^="add-to-cart"]').first.click()
        page.locator(".shopping_cart_link").click()
        page.wait_for_url("**/cart.html", timeout=15000)
        results.append(("---", "SAUCEDEMO — carrinho", "", page.url))

        check(page, "item no carrinho", ".cart_item", results)
        check(page, "botão checkout", '[data-test="checkout"]', results)

        page.locator('[data-test="checkout"]').click()
        page.wait_for_url("**/checkout-step-one.html", timeout=15000)
        results.append(("---", "SAUCEDEMO — dados do checkout", "", page.url))

        check(page, "primeiro nome", "#first-name", results)
        check(page, "sobrenome", "#last-name", results)
        check(page, "CEP", "#postal-code", results)
        check(page, "botão continuar", '[data-test="continue"]', results)

        page.locator("#first-name").fill("Luca")
        page.locator("#last-name").fill("Salcalde")
        page.locator("#postal-code").fill("01000-000")
        page.locator('[data-test="continue"]').click()
        page.wait_for_url("**/checkout-step-two.html", timeout=15000)
        results.append(("---", "SAUCEDEMO — revisão do pedido", "", page.url))

        check(page, "botão finish", '[data-test="finish"]', results)
        page.locator('[data-test="finish"]').click()
        page.wait_for_url("**/checkout-complete.html", timeout=15000)

        confirmed = page.get_by_text("Thank you for your order!").count() > 0
        results.append((OK if confirmed else FAIL, "texto de confirmação",
                        "Thank you for your order!", page.url))
    except Exception as e:  # noqa: BLE001
        results.append((FAIL, "SAUCEDEMO interrompido", type(e).__name__, str(e)[:120]))
    finally:
        context.close()


def check_the_internet(browser, results: list) -> None:
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto("https://the-internet.herokuapp.com/login",
                  wait_until="domcontentloaded", timeout=30000)
        results.append(("---", "THE INTERNET — login", "", page.url))

        check(page, "campo usuário", "#username", results)
        check(page, "campo senha", "#password", results)
        check(page, "botão login", 'button[type="submit"]', results)

        page.locator("#username").fill("tomsmith")
        page.locator("#password").fill("SuperSecretPassword!")
        page.locator('button[type="submit"]').click()

        # login aqui causa navegação de página inteira — esperar antes de verificar
        try:
            page.wait_for_url("**/secure", timeout=15000)
        except Exception:  # noqa: BLE001 - o relatório abaixo já registra a falha
            pass

        logged = page.get_by_text("You logged into a secure area!").count() > 0
        results.append((OK if logged else FAIL, "mensagem de login",
                        "You logged into a secure area!", page.url))

        page.goto("https://the-internet.herokuapp.com/add_remove_elements/",
                  wait_until="domcontentloaded", timeout=30000)
        results.append(("---", "THE INTERNET — add/remove elements", "", page.url))

        check(page, "botão Add Element", 'button[onclick="addElement()"]', results)

        for _ in range(3):
            page.locator('button[onclick="addElement()"]').click()

        added = page.locator(".added-manually").count()
        results.append((OK if added == 3 else FAIL, "elementos adicionados",
                        ".added-manually", f"{added} de 3 esperados"))
    except Exception as e:  # noqa: BLE001
        results.append((FAIL, "THE INTERNET interrompido", type(e).__name__, str(e)[:120]))
    finally:
        context.close()


def check_drift(browser, results: list) -> None:
    """Confirma que a injeção de drift realmente altera o DOM."""
    from common.drift import apply_drift

    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto("https://www.saucedemo.com/", wait_until="domcontentloaded", timeout=30000)
        results.append(("---", "DRIFT — injeção no DOM", "", "saucedemo"))

        alvos = ["#user-name", "#password", "#login-button"]

        antes = sum(page.locator(s).count() > 0 for s in alvos)
        results.append((OK if antes == 3 else FAIL, "antes do drift",
                        "3 seletores-chave", f"{antes}/3 funcionando"))

        stats = apply_drift(page, 1.0, seed=1)
        depois = sum(page.locator(s).count() > 0 for s in alvos)

        results.append((OK, "mutações aplicadas", "apply_drift(level=1.0)",
                        f"identidade quebrada: {stats.get('identity_broken')}/{stats.get('candidates')}"))
        # com level=1.0 todos os seletores por id/classe devem deixar de funcionar
        results.append((OK if depois == 0 else FAIL, "depois do drift (devem sumir)",
                        "3 seletores-chave", f"{depois}/3 ainda funcionando"))
    except Exception as e:  # noqa: BLE001
        results.append((FAIL, "DRIFT interrompido", type(e).__name__, str(e)[:120]))
    finally:
        context.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnóstico de seletores dos sites do TCC")
    parser.add_argument("--headed", action="store_true", help="abre o navegador visível")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    results: list = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        try:
            check_saucedemo(browser, results)
            check_the_internet(browser, results)
            check_drift(browser, results)
        finally:
            browser.close()

    print("\n" + "=" * 78)
    print("RELATÓRIO DE SELETORES")
    print("=" * 78)
    problems = 0
    for status, label, selector, extra in results:
        if status == "---":
            print(f"\n--- {label} ({extra})")
            continue
        if status == FAIL:
            problems += 1
        print(f"  [{status}] {label:<32} {selector:<34} {extra}")

    print("\n" + "=" * 78)
    if problems == 0:
        print("Nenhum problema encontrado — pode seguir para o piloto do runner.")
    else:
        print(f"{problems} seletor(es) com problema. Corrija os seletores listados acima.")
    print("=" * 78)


if __name__ == "__main__":
    main()
