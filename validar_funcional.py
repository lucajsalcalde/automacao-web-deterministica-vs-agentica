"""
Validação funcional do degradador: a checagem que faltou na coleta 1.

POR QUE EXISTE
--------------
A matriz de validar_instrumento.py confere se cada braço falha onde a teoria
diz que deve falhar. Ela não confere se a APLICAÇÃO continua funcionando depois
da degradação. Na coleta 1 o degradador alterava o atributo `name` dos campos,
que é o que o formulário de login do The Internet envia ao servidor: com a
degradação semântica, a senha correta passava a ser recusada. Todos os braços
falhavam no login, inclusive o agêntico, e as falhas foram lidas como falhas de
localização.

COMO FUNCIONA
-------------
Um braço de REFERÊNCIA executa as duas tarefas localizando cada elemento pela
identidade que o degradador preserva (data-drift-original-id e
data-drift-original-class), com as mesmas chamadas de degradação e as mesmas
sementes do experimento. Ele não mede robustez: ele sempre encontra o elemento
certo. Se mesmo assim a tarefa não conclui, a degradação quebrou a aplicação, e
nenhuma coleta pode começar.

Uso:
    python validar_funcional.py
    python validar_funcional.py --sementes 8 --niveis 0.25,0.5,0.75,1.0 --headed

Grava data/validacao_funcional.csv. Sai com código 1 se houver qualquer falha.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import time

from common.drift import apply_drift
from common.oracle import (expect_carrinho, expect_elementos_adicionados,
                           expect_text_visible, navegar, novo_contexto)

RAIZ = os.path.dirname(os.path.abspath(__file__))
SAIDA = os.path.join(RAIZ, "data", "validacao_funcional.csv")


def ref(page, seletor: str):
    """Localiza pelo seletor ORIGINAL, como se a página estivesse íntegra."""
    m = re.fullmatch(r"#([\w-]+)", seletor)
    if m:
        css = f'[data-drift-original-id="{m.group(1)}"], #{m.group(1)}'
    else:
        m = re.fullmatch(r"\.([\w-]+)", seletor)
        css = (f'[data-drift-original-class~="{m.group(1)}"], .{m.group(1)}'
               if m else seletor)
    return page.locator(f":is({css}):not([data-drift-decoy])").first


def tarefa_saucedemo(page, nivel, semente, tipo, etapa):
    def degradar():
        if nivel > 0:
            apply_drift(page, nivel, seed=semente, tipo=tipo)

    etapa("goto"); navegar(page, "https://www.saucedemo.com/"); degradar()
    etapa("login")
    ref(page, "#user-name").fill("standard_user")
    ref(page, "#password").fill("secret_sauce")
    ref(page, "#login-button").click()
    page.wait_for_url("**/inventory.html", timeout=8000); degradar()
    etapa("carrinho")
    ref(page, 'button[id^="add-to-cart"]').click()
    ref(page, ".shopping_cart_link").click()
    page.wait_for_url("**/cart.html", timeout=8000); degradar()
    expect_carrinho(page, 1)
    etapa("checkout")
    ref(page, "#checkout").click()
    page.wait_for_url("**/checkout-step-one.html", timeout=8000); degradar()
    ref(page, "#first-name").fill("Luca")
    ref(page, "#last-name").fill("Salcalde")
    ref(page, "#postal-code").fill("01000-000")
    ref(page, "#continue").click()
    page.wait_for_url("**/checkout-step-two.html", timeout=8000); degradar()
    etapa("finalizar")
    ref(page, "#finish").click()
    expect_text_visible(page, "Thank you for your order!")


def tarefa_the_internet(page, nivel, semente, tipo, etapa):
    def degradar():
        if nivel > 0:
            apply_drift(page, nivel, seed=semente, tipo=tipo)

    etapa("goto"); navegar(page, "https://the-internet.herokuapp.com/login"); degradar()
    etapa("login")
    ref(page, "#username").fill("tomsmith")
    ref(page, "#password").fill("SuperSecretPassword!")
    ref(page, 'button[type="submit"]').click()
    page.wait_for_url("**/secure", timeout=15000)
    expect_text_visible(page, "You logged into a secure area!")
    etapa("adicionar")
    navegar(page, "https://the-internet.herokuapp.com/add_remove_elements/"); degradar()
    for _ in range(3):
        ref(page, 'button[onclick="addElement()"]').click()
    expect_elementos_adicionados(page, 3)
    etapa("remover")
    ref(page, ".added-manually").click()
    expect_elementos_adicionados(page, 2)


TAREFAS = {"saucedemo": tarefa_saucedemo, "the_internet": tarefa_the_internet}


def main() -> None:
    ap = argparse.ArgumentParser(description="Valida que a degradação preserva a aplicação")
    ap.add_argument("--sites", default="saucedemo,the_internet")
    ap.add_argument("--tipos", default="cosmetico,estrutural,semantico")
    ap.add_argument("--niveis", default="0.25,0.5,0.75,1.0")
    ap.add_argument("--sementes", type=int, default=8,
                    help="sementes 0..n-1, as mesmas do experimento")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    linhas = []
    inicio = time.perf_counter()
    with sync_playwright() as p:
        navegador = p.chromium.launch(headless=not args.headed)
        try:
            for site in args.sites.split(","):
                for tipo in args.tipos.split(","):
                    for nivel in [float(x) for x in args.niveis.split(",")]:
                        for semente in range(args.sementes):
                            contexto = novo_contexto(navegador)
                            page = contexto.new_page()
                            passo = {"atual": "inicio"}
                            erro = ""
                            try:
                                TAREFAS[site](page, nivel, semente, tipo,
                                              lambda e: passo.__setitem__("atual", e))
                                sucesso = True
                            except Exception as e:  # noqa: BLE001
                                sucesso = False
                                erro = f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
                            finally:
                                contexto.close()
                            linhas.append({"site": site, "tipo": tipo, "nivel": nivel,
                                           "semente": semente, "sucesso": sucesso,
                                           "etapa": "" if sucesso else passo["atual"],
                                           "erro": erro})
                            print("." if sucesso else "X", end="", flush=True)
        finally:
            navegador.close()

    os.makedirs(os.path.dirname(SAIDA), exist_ok=True)
    with open(SAIDA, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0].keys()))
        w.writeheader()
        w.writerows(linhas)

    falhas = [l for l in linhas if not l["sucesso"]]
    print(f"\n\n{len(linhas)} execuções de referência em "
          f"{time.perf_counter() - inicio:.0f}s. Resultado em {SAIDA}")
    if falhas:
        print(f"\n{len(falhas)} FALHA(S): a degradação alterou o funcionamento da aplicação.")
        for l in falhas[:15]:
            print(f"  {l['site']} / {l['tipo']} {l['nivel']} / semente {l['semente']}"
                  f" / etapa {l['etapa']}: {l['erro']}")
        print("\nNão comece a coleta. Copie este bloco e envie para análise.")
        raise SystemExit(1)
    print("\nNenhuma falha: com os elementos certos, as duas tarefas concluem em todas "
          "as combinações. A degradação atinge só a identificação dos elementos.")


if __name__ == "__main__":
    main()
