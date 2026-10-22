"""
Calibração do limiar de similaridade do self-healing.

POR QUE ISTO É UM SUB-ESTUDO E NÃO UM AJUSTE ARBITRÁRIO
--------------------------------------------------------
O healer decide, entre centenas de candidatos, qual é o elemento procurado.
Essa decisão tem dois parâmetros: o limiar de similaridade e a margem mínima
sobre o segundo colocado. Escolhê-los "no olho" seria arbitrário e indefensável
numa banca.

Aqui eles são calibrados empiricamente. A verdade de referência existe porque a
injeção de drift preserva o identificador original no atributo
`data-drift-original-id` — então sabemos, para cada elemento da página
degradada, qual era o elemento original. Com isso é possível medir:

  - Verdadeiro positivo: curou e acertou o elemento.
  - Falso positivo: curou e pegou o elemento ERRADO (o caso perigoso, porque
    produz falso sucesso: o script segue adiante com o elemento errado).
  - Falso negativo: desistiu quando o elemento certo estava disponível.

O resultado é uma curva de precisão e revocação por limiar, e a escolha do
ponto de operação passa a ser justificada. Para este problema, precisão importa
mais que revocação: agarrar o elemento errado é pior que falhar explicitamente.

Uso:
    python calibrar_limiar.py
    python calibrar_limiar.py --repeticoes 5 --headed
"""

from __future__ import annotations

import argparse
import csv
import os

from common.drift import apply_drift
from common.healing import (
    carregar_impressoes,
    coletar_candidatos,
    melhor_candidato,
)

RAIZ = os.path.dirname(os.path.abspath(__file__))
SAIDA = os.path.join(RAIZ, "data", "calibracao_limiar.csv")

# Grade mais fina acima de 0,85: é nessa faixa que os casos legítimos se separam
# dos perigosos. Medido em teste: reidentificação correta pontua ~0,98, enquanto
# um campo vizinho de mesmo tipo (First Name vs. Last Name) pontua ~0,87.
LIMIARES = [0.40, 0.50, 0.60, 0.62, 0.70, 0.75, 0.80, 0.84, 0.86, 0.88,
            0.90, 0.92, 0.94, 0.96]
# 0,62 entra na grade para que o ponto de operação usado no experimento seja
# avaliado diretamente, e não por interpolação entre 0,60 e 0,70.
MARGENS = [0.0, 0.05, 0.10, 0.15]
NIVEIS_DRIFT = [0.25, 0.5, 0.75, 1.0]
# O healer só é realmente desafiado pelo drift semântico, que altera os
# atributos e textos usados na pontuação. Calibrar apenas no cosmético
# produziria acerto perfeito em qualquer limiar, sem poder discriminativo.
TIPOS_DRIFT = ["cosmetico", "estrutural", "semantico"]


def _id_base(valor: str) -> str:
    """Remove o sufixo numérico que o drift acrescenta ao renomear um id."""
    return valor.rsplit("-", 1)[0] if valor and valor.rsplit("-", 1)[-1].isdigit() else valor


def coletar_observacoes(page, impressoes: dict, alvos_da_tela: list,
                        niveis: list, repeticoes: int, tipos: list,
                        semente_inicial: int = 1000) -> list:
    """
    Para cada (alvo, nível de drift, repetição), registra a pontuação e a margem
    do melhor candidato e se ele era o elemento correto.
    """
    observacoes = []
    url = page.url
    verificaveis = [n for n in alvos_da_tela if impressoes[n].id_elemento]

    # --- Casos adversariais: o elemento-alvo é REMOVIDO da página ---
    # Sem eles a calibração só mede a situação fácil (alvo presente e íntegro) e
    # nunca produz falsos positivos, tornando todos os limiares equivalentes. É
    # justamente quando o alvo não existe que um limiar permissivo se torna
    # perigoso: o healer adota o elemento mais parecido e segue adiante.
    for nome in verificaveis:
        fp = impressoes[nome]
        for tipo in tipos:
            for nivel in niveis:
                for rep in range(repeticoes):
                    page.goto(url, wait_until="domcontentloaded")
                    page.evaluate(
                        "(sel) => { const e = document.querySelector(sel); "
                        "if (e) e.remove(); }",
                        fp.seletor_original,
                    )
                    apply_drift(page, nivel, seed=semente_inicial + rep, tipo=tipo)
                    candidatos = coletar_candidatos(page)
                    _, pontuacao, margem = melhor_candidato(fp, candidatos)
                    observacoes.append({
                        "alvo": nome + " (ausente)", "tipo": tipo, "drift": nivel,
                        "repeticao": rep, "pontuacao": pontuacao, "margem": margem,
                        "correto": 0,     # qualquer cura aqui é um falso positivo
                        "havia_alvo": 0,  # e desistir é o comportamento correto
                    })

    # --- Casos normais: o alvo existe, apenas degradado pelo drift ---
    #
    # Alvos sem id próprio ficam de fora: a verdade de referência depende do id
    # preservado pelo drift em data-drift-original-id. Incluí-los produziria
    # falsos positivos artificiais, como ocorreu numa versão anterior.
    for tipo in tipos:
        for nivel in niveis:
            for rep in range(repeticoes):
                page.goto(url, wait_until="domcontentloaded")
                apply_drift(page, nivel, seed=semente_inicial + rep, tipo=tipo)
                candidatos = coletar_candidatos(page)

                for nome in verificaveis:
                    fp = impressoes[nome]
                    escolhido, pontuacao, margem = melhor_candidato(fp, candidatos)

                    if escolhido is None:
                        observacoes.append({
                            "alvo": nome, "tipo": tipo, "drift": nivel,
                            "repeticao": rep, "pontuacao": 0.0, "margem": 0.0,
                            "correto": 0, "havia_alvo": 0,
                        })
                        continue

                    id_alvo = fp.id_elemento
                    id_escolhido = _id_base(escolhido.get("id_original", ""))
                    havia = int(any(
                        _id_base(c.get("id_original", "")) == id_alvo
                        for c in candidatos
                    ))
                    observacoes.append({
                        "alvo": nome, "tipo": tipo, "drift": nivel, "repeticao": rep,
                        "pontuacao": pontuacao, "margem": margem,
                        "correto": int(id_alvo == id_escolhido),
                        "havia_alvo": havia,
                    })

    return observacoes


def avaliar(observacoes: list, limiar: float, margem_min: float) -> dict:
    """Calcula VP, FP, FN, precisão, revocação e F1 para um par (limiar, margem)."""
    vp = fp_ = fn = 0
    for o in observacoes:
        curou = o["pontuacao"] >= limiar and o["margem"] >= margem_min
        if curou and o["correto"]:
            vp += 1
        elif curou and not o["correto"]:
            fp_ += 1
        elif not curou and o["havia_alvo"]:
            fn += 1
    precisao = vp / (vp + fp_) if (vp + fp_) else 0.0
    revocacao = vp / (vp + fn) if (vp + fn) else 0.0
    f1 = 2 * precisao * revocacao / (precisao + revocacao) if (precisao + revocacao) else 0.0
    return {"limiar": limiar, "margem": margem_min, "vp": vp, "fp": fp_, "fn": fn,
            "precisao": round(precisao, 4), "revocacao": round(revocacao, 4),
            "f1": round(f1, 4)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Calibra o limiar do self-healing")
    ap.add_argument("--repeticoes", type=int, default=5,
                    help="repetições por nível de drift (sementes diferentes)")
    ap.add_argument("--semente-inicial", type=int, default=1000,
                    help=("primeira semente do degradador. As sementes da "
                          "calibração começam em 1000 para não coincidir com as "
                          "do experimento (0 a 7): na coleta 1 as duas se "
                          "sobrepunham, e o limiar era avaliado nas mesmas "
                          "páginas degradadas que depois seriam medidas."))
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    observacoes = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        ctx = browser.new_context()
        page = ctx.new_page()
        try:
            # Tela de login do SauceDemo: três alvos com semântica distinta,
            # suficiente para calibrar sem percorrer o fluxo inteiro.
            impressoes = carregar_impressoes(
                os.path.join(RAIZ, "data", "fingerprints", "saucedemo.json"))
            page.goto("https://www.saucedemo.com/", wait_until="domcontentloaded")
            observacoes += coletar_observacoes(
                page, impressoes, ["campo_usuario", "campo_senha", "botao_login"],
                NIVEIS_DRIFT, args.repeticoes, TIPOS_DRIFT, args.semente_inicial)

            impressoes_ti = carregar_impressoes(
                os.path.join(RAIZ, "data", "fingerprints", "the_internet.json"))
            page.goto("https://the-internet.herokuapp.com/login",
                      wait_until="domcontentloaded")
            observacoes += coletar_observacoes(
                page, impressoes_ti, ["campo_usuario", "campo_senha", "botao_login"],
                NIVEIS_DRIFT, args.repeticoes, TIPOS_DRIFT, args.semente_inicial)
        finally:
            browser.close()

    resultados = [avaliar(observacoes, l, m) for m in MARGENS for l in LIMIARES]

    os.makedirs(os.path.dirname(SAIDA), exist_ok=True)
    with open(SAIDA, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(resultados[0].keys()))
        w.writeheader()
        w.writerows(resultados)

    print(f"\n{len(observacoes)} observações coletadas.\n")
    print(f"{'margem':>7}{'limiar':>8}{'VP':>5}{'FP':>5}{'FN':>5}"
          f"{'precisão':>10}{'revocação':>11}{'F1':>8}")
    print("-" * 60)
    for r in resultados:
        print(f"{r['margem']:>7.2f}{r['limiar']:>8.2f}{r['vp']:>5}{r['fp']:>5}{r['fn']:>5}"
              f"{r['precisao']:>10.3f}{r['revocacao']:>11.3f}{r['f1']:>8.3f}")

    # Ponto de operação recomendado: entre os que não cometem NENHUM falso
    # positivo, o de maior revocação. Justificativa: neste problema, curar para
    # o elemento errado gera falso sucesso e contamina os dados do experimento,
    # enquanto desistir apenas registra uma falha honesta.
    seguros = [r for r in resultados if r["fp"] == 0]
    if seguros:
        # Entre as combinações que empatam em revocação sem cometer falsos
        # positivos, escolhe-se a MAIS CONSERVADORA (maior limiar e maior
        # margem). Empate em desempenho não é empate em risco: um limiar baixo
        # só não errou porque os casos testados foram fáceis, e continuaria
        # aceitando candidatos fracos diante de situações não observadas aqui.
        melhor = max(seguros, key=lambda r: (r["revocacao"], r["limiar"], r["margem"]))
        print(f"\nRecomendado (sem falsos positivos): "
              f"limiar={melhor['limiar']:.2f}, margem={melhor['margem']:.2f} "
              f"-> revocação {melhor['revocacao']:.3f}")
        print(f"Aplique com:  set HEAL_THRESHOLD={melhor['limiar']}  "
              f"e  set HEAL_MARGIN={melhor['margem']}")
    else:
        melhor = max(resultados, key=lambda r: r["f1"])
        print(f"\nNenhuma combinação evitou falsos positivos. Maior F1: "
              f"limiar={melhor['limiar']:.2f}, margem={melhor['margem']:.2f}")

    print(f"\nTabela completa em: {SAIDA}\n")


if __name__ == "__main__":
    main()
