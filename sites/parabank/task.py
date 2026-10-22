"""
Tarefa: formulário multi-campo / transação bancária no ParaBank
(parabank.parasoft.com).

registro de nova conta -> abertura de conta poupança -> transferência entre
contas -> confirmação.

ParaBank não tem um login fixo compartilhado como o SauceDemo: cada execução
registra um usuário novo e descartável (username com sufixo aleatório), o que
também tem uma vantagem metodológica — cada repetição fica estatisticamente
independente, sem estado compartilhado entre execuções.

Os ids do formulário de registro (`customer.*`) são estáveis há muitos anos
nesse demo do Parasoft. Já os botões/links de "Open New Account" e "Transfer
Funds" foram implementados via `get_by_role`/`get_by_text` (nome acessível),
que é mais tolerante a pequenas mudanças de markup do que um id exato — mas,
como não foi possível validar contra o site real neste ambiente (sem acesso
de rede), rode com `--headed` antes de confiar nos resultados.
"""

from __future__ import annotations

import argparse
import random
import time

from common.drift import apply_drift
from common.oracle import TaskResult, novo_contexto, expect_text_visible, run_task_safely

SITE = "parabank"
TASK = "transacao_bancaria"
BASE_URL = "https://parabank.parasoft.com/parabank"
REGISTER_URL = f"{BASE_URL}/register.htm"


def _unique_username(repetition: int) -> str:
    return f"tcc{int(time.time() * 1000)}{repetition}{random.randint(0, 9999)}"


def _steps(page, counter, drift_level: float, repetition: int, drift_type: str) -> None:
    username = _unique_username(repetition)
    password = "SenhaTcc!2026"

    page.goto(REGISTER_URL, wait_until="domcontentloaded")
    counter.step("goto_register")

    if drift_level > 0:
        apply_drift(page, drift_level, seed=repetition, tipo=drift_type)
        counter.step("apply_drift")

    page.locator("#customer\\.firstName").fill("Luca")
    page.locator("#customer\\.lastName").fill("Salcalde")
    page.locator("#customer\\.address\\.street").fill("Rua Exemplo, 100")
    page.locator("#customer\\.address\\.city").fill("Sao Paulo")
    page.locator("#customer\\.address\\.state").fill("SP")
    page.locator("#customer\\.address\\.zipCode").fill("01000-000")
    page.locator("#customer\\.phoneNumber").fill("11999999999")
    page.locator("#customer\\.ssn").fill(str(random.randint(100000000, 999999999)))
    page.locator("#customer\\.username").fill(username)
    page.locator("#customer\\.password").fill(password)
    page.locator("#repeatedPassword").fill(password)
    counter.step("fill_registration_form")

    page.get_by_role("button", name="Register").click()
    counter.step("submit_registration")

    # ParaBank loga automaticamente após o registro
    page.wait_for_url("**/overview.htm", timeout=10000)
    counter.step("assert_overview_url")

    # --- abrir uma conta poupança nova ---
    page.get_by_role("link", name="Open New Account").click()
    counter.step("open_new_account_page")

    type_select = page.locator("#type")
    if type_select.count():
        type_select.select_option(label="SAVINGS")
    counter.step("select_account_type")

    page.get_by_role("button", name="Open New Account").click()
    counter.step("submit_new_account")

    new_account_id_locator = page.locator("#newAccountId")
    new_account_id_locator.wait_for(state="visible", timeout=10000)
    new_account_id = new_account_id_locator.inner_text().strip()
    counter.step("capture_new_account_id")

    if not new_account_id:
        raise AssertionError("Não foi possível capturar o id da nova conta poupança")

    # --- transferir fundos da conta corrente para a nova conta poupança ---
    page.get_by_role("link", name="Transfer Funds").click()
    counter.step("open_transfer_page")

    page.locator("#amount").fill("50")
    counter.step("fill_amount")

    to_account_select = page.locator("#toAccountId")
    to_account_select.select_option(label=new_account_id)
    counter.step("select_to_account")

    page.get_by_role("button", name="Transfer").click()
    counter.step("submit_transfer")

    expect_text_visible(page, "Transfer Complete!")
    counter.step("assert_transfer_confirmation")


def run(browser, drift_level: float = 0.0, repetition: int = 0,
        drift_type: str = "cosmetico") -> TaskResult:
    context = novo_contexto(browser)
    page = context.new_page()
    try:
        result = run_task_safely(
            site=SITE,
            task=TASK,
            approach="deterministic",
            drift_level=drift_level,
            repetition=repetition,
            drift_type=drift_type,
            fn=lambda counter: _steps(page, counter, drift_level, repetition, drift_type=drift_type),
        )
    finally:
        context.close()
    return result


if __name__ == "__main__":
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description="Smoke test da tarefa ParaBank")
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
