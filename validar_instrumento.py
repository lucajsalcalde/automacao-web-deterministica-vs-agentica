"""
Validação do instrumento — UM comando que testa tudo.

Executa a matriz completa (site x braço x tipo de drift) e compara o resultado
observado com o comportamento teoricamente esperado. Substitui a sequência de
smoke tests individuais: em vez de rodar um comando, ler o erro, corrigir e
repetir, roda-se este e obtém-se o quadro inteiro de uma vez.

O braço agêntico fica de fora por padrão porque consome API; inclua com
--com-agentico se quiser.

HIPÓTESES VERIFICADAS
---------------------
    Sem drift ............ todos os braços devem concluir a tarefa.
    Drift cosmético ...... quebra id e classe. Só o braço frágil deve falhar.
    Drift estrutural ..... move e reposiciona. O frágil falha; os demais devem
                           resistir, embora o self-healing perca a pista de
                           posição.
    Drift semântico ...... altera rótulos e atributos. Frágil e semântico devem
                           falhar; o self-healing fica degradado.

Onde a teoria não determina o resultado (o caso do self-healing sob drift
semântico), o script apenas registra o observado, sem acusar divergência.

Uso:
    python validar_instrumento.py
    python validar_instrumento.py --nivel 1.0 --headed
    python validar_instrumento.py --sites saucedemo
"""

from __future__ import annotations

import argparse
import time

from sites.saucedemo import task as sd_fragil
from sites.saucedemo import task_healing as sd_healing
from sites.saucedemo import task_semantic as sd_semantico
from sites.saucedemo import task_xpath as sd_xpath
from sites.the_internet import task as ti_fragil
from sites.the_internet import task_healing as ti_healing
from sites.the_internet import task_semantic as ti_semantico
from sites.the_internet import task_xpath as ti_xpath
from sites.saucedemo import task_agentic as sd_agentico
from sites.the_internet import task_agentic as ti_agentico

BRACOS = {
    "frágil": {"saucedemo": sd_fragil, "the_internet": ti_fragil},
    "xpath": {"saucedemo": sd_xpath, "the_internet": ti_xpath},
    "semântico": {"saucedemo": sd_semantico, "the_internet": ti_semantico},
    "healing": {"saucedemo": sd_healing, "the_internet": ti_healing},
}

# Fora do conjunto padrão: consome recursos (créditos de API ou cota da
# assinatura). Incluído apenas com --com-agentico.
BRACO_AGENTICO = {"agêntico": {"saucedemo": sd_agentico, "the_internet": ti_agentico}}

# None = a teoria não determina; apenas observa.
ESPERADO = {
    ("xpath", "nenhum"): True, ("xpath", "cosmetico"): True,
    ("xpath", "estrutural"): False, ("xpath", "semantico"): False,
    ("frágil", "nenhum"): True,   ("semântico", "nenhum"): True,   ("healing", "nenhum"): True,
    ("frágil", "cosmetico"): False, ("semântico", "cosmetico"): True, ("healing", "cosmetico"): True,
    ("frágil", "estrutural"): False, ("semântico", "estrutural"): True, ("healing", "estrutural"): None,
    ("frágil", "semantico"): False, ("semântico", "semantico"): False, ("healing", "semantico"): None,
    # O agêntico interpreta a página em vez de localizar por regra. Espera-se que
    # conclua sem drift; nas demais condições a teoria não determina o resultado,
    # e é justamente isso que o experimento investiga.
    ("agêntico", "nenhum"): True, ("agêntico", "cosmetico"): None,
    ("agêntico", "estrutural"): None, ("agêntico", "semantico"): None,
}

TIPOS = ["nenhum", "cosmetico", "estrutural", "semantico"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Valida o instrumento de medição")
    ap.add_argument("--nivel", type=float, default=1.0,
                    help="intensidade do drift nos testes (padrão: 1.0, o pior caso)")
    ap.add_argument("--sites", type=str, default="saucedemo,the_internet")
    ap.add_argument("--bracos", type=str, default="",
                    help="filtra braços: fragil, semantico, healing (separados por vírgula)")
    ap.add_argument("--tipos", type=str, default="",
                    help="filtra tipos: nenhum, cosmetico, estrutural, semantico")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--com-agentico", action="store_true",
                    help="inclui o braço agêntico (CONSOME API)")
    args = ap.parse_args()

    sites = args.sites.split(",")

    # filtros aceitam os nomes sem acento, para não obrigar a digitá-los
    apelidos = {"fragil": "frágil", "semantico": "semântico", "healing": "healing",
                "agentico": "agêntico", "xpath": "xpath"}
    filtro_bracos = [apelidos.get(b.strip(), b.strip())
                     for b in args.bracos.split(",") if b.strip()]
    filtro_tipos = [t.strip() for t in args.tipos.split(",") if t.strip()]
    disponiveis = dict(BRACOS)
    if args.com_agentico:
        disponiveis.update(BRACO_AGENTICO)
    bracos = {k: v for k, v in disponiveis.items() if not filtro_bracos or k in filtro_bracos}
    tipos = [t for t in TIPOS if not filtro_tipos or t in filtro_tipos]

    from playwright.sync_api import sync_playwright

    linhas = []
    inicio = time.perf_counter()

    def registrar(site, braco, tipo, r):
        esperado = ESPERADO[(braco, tipo)]
        status = ("observado" if esperado is None
                  else ("OK" if r.success == esperado else "DIVERGE"))
        linhas.append({
            "site": site, "braco": braco, "tipo": tipo,
            "sucesso": r.success, "esperado": esperado,
            "status": status, "erro": r.error_type,
            "detalhe": (r.error_message or "").split("\n")[0][:70],
            "erro_completo": (r.error_message or "")[:600],
            "heal": (f"{r.heal_curados}/{r.heal_acionamentos}"
                     if r.heal_acionamentos else ""),
            "passos": r.n_steps,
            "custo": r.cost_usd,
        })
        print({"OK": ".", "DIVERGE": "X", "observado": "?"}[status], end="", flush=True)

    def combinacoes(nomes):
        for site in sites:
            for braco in nomes:
                if braco in bracos and site in bracos[braco]:
                    for tipo in tipos:
                        yield site, braco, tipo

    sincronos = [b for b in bracos if b != "agêntico"]
    agenticos = [b for b in bracos if b == "agêntico"]

    # Os braços síncronos compartilham um navegador; o agêntico NÃO pode rodar
    # aqui dentro. Ele abre o próprio laço de eventos (asyncio), e a API síncrona
    # do Playwright já mantém um laço ativo na mesma thread — chamar asyncio.run()
    # de dentro dela levanta "cannot be called from a running event loop".
    if sincronos:
        with sync_playwright() as p:
            navegador = p.chromium.launch(headless=not args.headed)
            try:
                for site, braco, tipo in combinacoes(sincronos):
                    nivel = 0.0 if tipo == "nenhum" else args.nivel
                    tipo_real = "cosmetico" if tipo == "nenhum" else tipo
                    registrar(site, braco, tipo,
                              bracos[braco][site].run(navegador, drift_level=nivel,
                                                      repetition=0, drift_type=tipo_real))
            finally:
                navegador.close()

    # Braço agêntico, fora do contexto síncrono: lança o próprio navegador.
    #
    # Limite de uso do provedor e agente sem ferramentas não são resultados de
    # validação: são falhas de infraestrutura ou de configuração. Interrompem a
    # execução com mensagem clara, em vez de aparecerem como divergência do
    # instrumento — que mandaria procurar defeito no lugar errado.
    from common.agentic_base import (AgenteContornouAInterface,
                                     AgenteSemFerramentas, LimiteDeUsoAtingido)
    try:
        for site, braco, tipo in combinacoes(agenticos):
            nivel = 0.0 if tipo == "nenhum" else args.nivel
            tipo_real = "cosmetico" if tipo == "nenhum" else tipo
            registrar(site, braco, tipo,
                      bracos[braco][site].run(drift_level=nivel, repetition=0,
                                              drift_type=tipo_real))
    except LimiteDeUsoAtingido as e:
        print(f"\n\nINTERROMPIDO — limite de uso do provedor atingido.\n  {e}")
        print("  Espere a liberação e rode o comando novamente.")
    except AgenteSemFerramentas as e:
        print(f"\n\nINTERROMPIDO — o agente ficou sem navegador.\n  {e}")
        print("  Isto é erro de configuração, não resultado do experimento.")
    except AgenteContornouAInterface as e:
        print(f"\n\nINTERROMPIDO — o agente contornou a interface.\n  {e}")
        print("  A execução não mede robustez de localização. Verifique o")
        print("  bloqueio das ferramentas de execução de código.")

    print(f"\n\n{'=' * 92}")
    print(f"MATRIZ DE VALIDAÇÃO  (drift nível {args.nivel}, {time.perf_counter()-inicio:.0f}s)")
    print("=" * 92)
    print(f"{'site':<14}{'braço':<12}{'drift':<12}{'obtido':<9}{'esperado':<10}"
          f"{'status':<10}{'heal':<7}{'passos':>7}{'US$':>9}  {'detalhe'}")
    print("-" * 108)
    for l in linhas:
        esp = {True: "sucesso", False: "falha", None: "—"}[l["esperado"]]
        custo = f"{l['custo']:.4f}" if l["custo"] else ""
        print(f"{l['site']:<14}{l['braco']:<12}{l['tipo']:<12}"
              f"{('sucesso' if l['sucesso'] else 'falha'):<9}{esp:<10}"
              f"{l['status']:<10}{l['heal']:<7}{l['passos']:>7}{custo:>9}  "
              f"{l['detalhe'][:40]}")

    agenticas = [l for l in linhas if l["braco"] == "agêntico"]
    if agenticas:
        custo_total = sum(l["custo"] for l in agenticas)
        passos = [l["passos"] for l in agenticas]
        com_falha = [l for l in agenticas if not l["sucesso"]]
        print("-" * 108)
        print(f"\nBRAÇO AGÊNTICO — {len(agenticas)} execuções")
        print(f"  custo total: US$ {custo_total:.4f}"
              f"  |  média por execução: US$ {custo_total/len(agenticas):.4f}")
        print(f"  passos: mínimo {min(passos)}, máximo {max(passos)}, "
              f"média {sum(passos)/len(passos):.1f}")
        if com_falha:
            c = sum(l["custo"] for l in com_falha) / len(com_falha)
            p = sum(l["passos"] for l in com_falha) / len(com_falha)
            print(f"  nas FALHAS: média US$ {c:.4f} e {p:.1f} passos "
                  f"({len(com_falha)} de {len(agenticas)})")
        print("\n  Projeção para a coleta (2 sites x 3 tipos x N níveis x R repetições):")
        media = custo_total / len(agenticas)
        for niveis, reps in ((3, 4), (3, 8), (5, 8)):
            n = 2 * 3 * niveis * reps
            print(f"    {niveis} níveis x {reps} rep. = {n:>3} execuções -> "
                  f"US$ {media*n:>6.2f}  (~R$ {media*n*5.4:>7.2f})")

    divergentes = [l for l in linhas if l["status"] == "DIVERGE"]
    print("=" * 92)
    if divergentes:
        print(f"\n{len(divergentes)} DIVERGÊNCIA(S) — o instrumento não está medindo o que se espera:")
        for l in divergentes:
            esp = "sucesso" if l["esperado"] else "falha"
            print(f"  - {l['site']} / {l['braco']} / drift {l['tipo']}: "
                  f"esperado {esp}, obtido {'sucesso' if l['sucesso'] else 'falha'}")
            if l.get("erro_completo"):
                for linha in l["erro_completo"].split("\n")[:12]:
                    print(f"      {linha}")
        print("\nCopie este bloco e envie para análise.")
    else:
        print("\nNenhuma divergência. Próximo passo: python validar_funcional.py "
              "(ver README.md)")
    print()


if __name__ == "__main__":
    main()
