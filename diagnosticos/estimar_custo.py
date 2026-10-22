"""
Estimador de custo da coleta agêntica — não gasta nada, só faz contas.

Dois usos:

1) ANTES de qualquer execução, com estimativa da literatura:
       python estimar_custo.py

2) DEPOIS do piloto, usando o custo real de UMA execução (mais confiável):
       python estimar_custo.py --custo-por-execucao 0.2841

O segundo modo é o que vale para decidir o orçamento: o primeiro usa um número
de referência publicado (~114 mil tokens por tarefa com Playwright MCP), que
pode não corresponder às suas tarefas.
"""

from __future__ import annotations

import argparse

# Preços em US$ por milhão de tokens (consultados em 30/07/2026).
TABELA = {
    "claude-sonnet-5 (até 31/08/2026)": (2.0, 10.0),
    "claude-sonnet-5 (a partir de 01/09/2026)": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-5": (5.0, 25.0),
}

# Referência publicada para uma tarefa de automação via Playwright MCP.
TOKENS_INPUT_REF = 114_000
TOKENS_OUTPUT_REF = 5_000

# Overhead fixo do backend SDK, medido em 30/07/2026: o prompt de sistema da CLI
# do Claude Code (20.921 tokens de cache) é cobrado a cada execução. Não existe
# no backend de API, onde o prompt de sistema é o nosso (poucas centenas de tokens).
OVERHEAD_SDK_USD = 0.1256


def main() -> None:
    ap = argparse.ArgumentParser(description="Estima o custo da coleta agêntica")
    ap.add_argument("--sites", type=int, default=2)
    ap.add_argument("--niveis-drift", type=int, default=3)
    ap.add_argument("--repeticoes", type=int, default=10)
    ap.add_argument("--custo-por-execucao", type=float, default=None,
                    help="custo real de uma execução, medido no piloto (US$)")
    ap.add_argument("--cotacao", type=float, default=5.40,
                    help="cotação do dólar para converter (padrão: 5.40)")
    ap.add_argument("--descontar-overhead", action="store_true",
                    help=("use quando o custo medido veio do backend SDK: desconta o "
                          "prompt de sistema da CLI (~US$ 0,1256/execução), que não "
                          "existe no backend de API"))
    args = ap.parse_args()

    execucoes = args.sites * args.niveis_drift * args.repeticoes
    print(f"\nExecuções agênticas previstas: {execucoes}")
    print(f"({args.sites} sites x {args.niveis_drift} níveis de drift x {args.repeticoes} repetições)\n")

    if args.custo_por_execucao is not None:
        medido = args.custo_por_execucao
        print("Baseado no custo REAL medido no piloto:")
        print(f"  US$ {medido:.4f} por execução (medido)")

        if args.descontar_overhead:
            ajustado = max(medido - OVERHEAD_SDK_USD, 0.0)
            print(f"  - US$ {OVERHEAD_SDK_USD:.4f} de overhead do prompt de sistema da CLI")
            print(f"  = US$ {ajustado:.4f} por execução (estimativa para o backend de API)\n")
            for rotulo, valor in (("Se rodar pelo SDK (assinatura)", medido),
                                  ("Se rodar pela API (cobrado)", ajustado)):
                total = valor * execucoes
                print(f"  {rotulo}: US$ {total:.2f}  (~R$ {total * args.cotacao:.2f})")
            print()
        else:
            total = medido * execucoes
            print(f"  US$ {total:.2f} no total  (~R$ {total * args.cotacao:.2f})\n")
        return

    print("Estimativa a partir de referência publicada")
    print(f"(~{TOKENS_INPUT_REF:,} tokens de entrada e ~{TOKENS_OUTPUT_REF:,} de saída por execução).")
    print("Números APENAS indicativos — rode o piloto para medir o real.\n")

    largura = max(len(k) for k in TABELA) + 2
    print(f"{'Modelo'.ljust(largura)}{'Por execução':>14}{'Total':>12}{'Total (R$)':>14}")
    print("-" * (largura + 40))
    for nome, (entrada, saida) in TABELA.items():
        por_exec = (TOKENS_INPUT_REF / 1e6 * entrada) + (TOKENS_OUTPUT_REF / 1e6 * saida)
        total = por_exec * execucoes
        print(f"{nome.ljust(largura)}{('US$ %.3f' % por_exec):>14}"
              f"{('US$ %.2f' % total):>12}{('R$ %.2f' % (total * args.cotacao)):>14}")

    print("\nObservações:")
    print("  - O cache de prompt (ativado por padrão) tende a reduzir esses valores,")
    print("    porque a lista de ferramentas do MCP é reenviada a cada passo.")
    print("  - Novas contas na Anthropic recebem um pequeno crédito gratuito:")
    print("    use-o para rodar o piloto e medir o custo real antes de pagar.")
    print("  - Trocar o modelo: $env:LLM_MODEL = \"claude-haiku-4-5-20251001\"\n")


if __name__ == "__main__":
    main()
