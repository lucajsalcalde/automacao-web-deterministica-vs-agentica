"""
Orquestrador do experimento: roda cada tarefa x nível de drift x repetição e
grava uma linha por execução em data/results.csv.

Uso:
    # piloto rápido do braço determinístico (sem custo de API)
    python -m common.runner --repeats 2 --drift-levels 0 --sites saucedemo

    # piloto do braço agêntico (CONSOME CRÉDITOS — comece com 1 repetição)
    python -m common.runner --approach agentic --repeats 1 --drift-levels 0 --sites saucedemo

    # coleta principal completa (2 sites x 2 braços x 3 níveis x 10 repetições)
    python -m common.runner --approach both --repeats 10 --drift-levels 0,0.25,0.5

    # extensão opcional (fora do escopo principal, ver README)
    python -m common.runner --repeats 10 --drift-levels 0,0.25,0.5 --sites parabank

    # COLETA 2: os cinco braços intercalados em ordem aleatória reprodutível
    python -m common.runner --approach all --intercalado --repeats 8 \
        --drift-levels 0,0.25,0.5,0.75,1.0 \
        --drift-types cosmetico,estrutural,semantico --coleta 2 --resume
"""

from __future__ import annotations

import argparse
import os
import sys

from common.agentic_base import (AgenteContornouAInterface, AgenteSemFerramentas,
                                 LimiteDeUsoAtingido)
from common.oracle import append_result_csv
from common.versao import impressao_instrumento

# garante que "sites.*" seja importável quando rodado como módulo
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sites.saucedemo import task as saucedemo_task  # noqa: E402
from sites.parabank import task as parabank_task  # noqa: E402
from sites.the_internet import task as the_internet_task  # noqa: E402
from sites.saucedemo import task_agentic as saucedemo_agentic  # noqa: E402
from sites.the_internet import task_agentic as the_internet_agentic  # noqa: E402
from sites.saucedemo import task_semantic as saucedemo_semantic  # noqa: E402
from sites.the_internet import task_semantic as the_internet_semantic  # noqa: E402
from sites.saucedemo import task_healing as saucedemo_healing  # noqa: E402
from sites.saucedemo import task_xpath as saucedemo_xpath  # noqa: E402
from sites.the_internet import task_healing as the_internet_healing  # noqa: E402
from sites.the_internet import task_xpath as the_internet_xpath  # noqa: E402

TASK_MODULES = {
    "saucedemo": saucedemo_task,
    "the_internet": the_internet_task,
    # ParaBank fica implementado mas FORA da coleta principal (ver escopo, seção 6.3):
    # exige registrar uma conta nova a cada execução, sendo o mais suscetível a
    # instabilidade/reset de dados do servidor. Rode explicitamente com
    # `--sites parabank` se quiser incluí-lo como extensão do experimento.
    "parabank": parabank_task,
}

# Braço agêntico: só existe para os sites da coleta principal.
AGENTIC_MODULES = {
    "saucedemo": saucedemo_agentic,
    "the_internet": the_internet_agentic,
}

# Os quatro braços do experimento, do menos ao mais adaptativo. Todos os que não
# são agênticos rodam no mesmo caminho síncrono e não consomem API.
SEMANTIC_MODULES = {
    "saucedemo": saucedemo_semantic,
    "the_internet": the_internet_semantic,
}

HEALING_MODULES = {
    "saucedemo": saucedemo_healing,
    "the_internet": the_internet_healing,
}

# Localização pelo CAMINHO no DOM — a única estratégia determinística sensível a
# reorganização estrutural. Sem ela, a condição de drift estrutural produzia
# resultado idêntico à cosmética em 191 de 192 pares comparados.
XPATH_MODULES = {
    "saucedemo": saucedemo_xpath,
    "the_internet": the_internet_xpath,
}

BRACOS_SINCRONOS = {
    "deterministic": (TASK_MODULES, "determinístico"),
    "xpath": (XPATH_MODULES, "xpath"),
    "semantic": (SEMANTIC_MODULES, "semântico"),
    "healing": (HEALING_MODULES, "self-healing"),
}

TODOS_OS_BRACOS = ["deterministic", "xpath", "semantic", "healing", "agentic"]

# Sites que entram na coleta quando --sites não é informado.
DEFAULT_SITES = ["saucedemo", "the_internet"]

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CSV_PATH = os.path.join(DATA_DIR, "results.csv")

# Preenchidos em main(): toda linha gravada leva o número da coleta e a
# impressão digital do código que a produziu (ver common/versao.py).
COLETA = 2
VERSAO_INSTRUMENTO = ""


def _gravar(result) -> None:
    result.coleta = COLETA
    result.versao_instrumento = VERSAO_INSTRUMENTO
    append_result_csv(result, CSV_PATH)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Roda a coleta de dados do experimento.")
    parser.add_argument("--repeats", type=int, default=10, help="repetições por combinação")
    parser.add_argument(
        "--drift-levels",
        type=str,
        default="0,0.25,0.5",
        help="níveis de drift separados por vírgula, ex.: 0,0.25,0.5",
    )
    parser.add_argument(
        "--drift-types",
        type=str,
        default="cosmetico",
        help=("tipos de drift separados por vírgula: cosmetico, estrutural, "
              "semantico. Cumulativos — cada um inclui o anterior."),
    )
    parser.add_argument(
        "--sites",
        type=str,
        default=",".join(DEFAULT_SITES),
        help=(
            "sites a rodar, separados por vírgula. Default: "
            f"{','.join(DEFAULT_SITES)} (parabank fica fora da coleta principal)"
        ),
    )
    parser.add_argument("--headed", action="store_true", help="abre o navegador visível (mais lento)")
    parser.add_argument(
        "--approach",
        type=str,
        default="deterministic",
        choices=TODOS_OS_BRACOS + ["sem-api", "all"],
        help=(
            "braço a executar. 'sem-api' roda os quatro braços que não usam LLM "
            "(deterministic, xpath, semantic, healing); 'all' inclui o agêntico. "
            "O braço agêntico consome créditos de API ou cota da assinatura."
        ),
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=None,
        help=(
            "teto de gasto em dólares. A coleta para ao atingi-lo, preservando "
            "o que já foi salvo. Proteção contra custo inesperado."
        ),
    )
    parser.add_argument(
        "--intercalado",
        action="store_true",
        help=(
            "intercala os braços: percorre as células (site, tipo, nível, "
            "repetição) em ordem aleatória reprodutível e roda nelas todos os "
            "braços pedidos, também em ordem sorteada. Evita que um braço seja "
            "coletado num dia e outro em outro, como ocorreu na coleta 1."
        ),
    )
    parser.add_argument("--semente-ordem", type=int, default=2026,
                        help="semente do sorteio da ordem no modo intercalado")
    parser.add_argument("--coleta", type=int, default=2,
                        help="número da coleta gravado em cada linha")
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "pula combinações que já existem no CSV. Use para continuar uma "
            "coleta interrompida sem repetir (nem pagar de novo) o que já foi feito."
        ),
    )
    return parser.parse_args()


def _run_sincrono(approach, sites, drift_levels, drift_types, repeats, headed,
                  progress, feitos=None) -> None:
    """Executa um dos braços que não usam LLM, compartilhando um navegador."""
    from playwright.sync_api import sync_playwright

    modulos, rotulo = BRACOS_SINCRONOS[approach]
    feitos = feitos or set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        try:
            for site_name in sites:
                if site_name not in modulos:
                    print(f"  (aviso: '{site_name}' não tem braço {rotulo} — ignorado)")
                    continue
                module = modulos[site_name]
                for drift_type in drift_types:
                    for drift_level in drift_levels:
                        for repetition in range(repeats):
                            chave = (site_name, approach, drift_type, drift_level, repetition)
                            if chave in feitos:
                                continue
                            result = module.run(browser, drift_level=drift_level,
                                                repetition=repetition,
                                                drift_type=drift_type)
                            _gravar(result)
                            progress(result, site_name, rotulo, drift_type,
                                     drift_level, repetition)
        finally:
            browser.close()


def _run_intercalado(approaches, sites, drift_levels, drift_types, repeats, headed,
                     progress, feitos, semente) -> None:
    """
    Coleta intercalada (coleta 2).

    As células são embaralhadas com semente fixa, e dentro de cada célula a
    ordem dos braços também é sorteada. Com --resume a mesma ordem é refeita e
    o que já está no CSV é pulado, então uma interrupção por limite de uso não
    altera o desenho.

    Os braços síncronos rodam num navegador aberto só para a célula; o agêntico
    roda fora dele, porque abre o próprio laço de eventos (ver a nota em
    validar_instrumento.py). Por isso, dentro da célula, o agêntico vai para o
    início ou para o fim da fila, por sorteio.
    """
    import random

    from playwright.sync_api import sync_playwright

    celulas = [(s, t, lv, r) for s in sites for t in drift_types
               for lv in drift_levels for r in range(repeats)]
    sorteio = random.Random(semente)
    sorteio.shuffle(celulas)

    for site_name, drift_type, drift_level, repetition in celulas:
        sincronos = [a for a in approaches if a != "agentic"]
        sorteio.shuffle(sincronos)
        agentico_primeiro = sorteio.random() < 0.5
        ordem = sincronos + (["agentic"] if "agentic" in approaches else [])
        if agentico_primeiro and "agentic" in approaches:
            ordem = ["agentic"] + sincronos

        pendentes = [a for a in ordem
                     if (site_name, a, drift_type, drift_level, repetition) not in feitos]
        if not pendentes:
            continue

        def rodar_agentico():
            module = AGENTIC_MODULES[site_name]
            result = module.run(drift_level=drift_level, repetition=repetition,
                                drift_type=drift_type)
            _gravar(result)
            progress(result, site_name, "agêntico", drift_type, drift_level, repetition)

        if "agentic" in pendentes and agentico_primeiro:
            rodar_agentico()

        bloco = [a for a in pendentes if a != "agentic"]
        if bloco:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=not headed)
                try:
                    for approach in bloco:
                        modulos, rotulo = BRACOS_SINCRONOS[approach]
                        result = modulos[site_name].run(
                            browser, drift_level=drift_level,
                            repetition=repetition, drift_type=drift_type)
                        _gravar(result)
                        progress(result, site_name, rotulo, drift_type,
                                 drift_level, repetition)
                finally:
                    browser.close()

        if "agentic" in pendentes and not agentico_primeiro:
            rodar_agentico()


class OrcamentoEsgotado(Exception):
    """Sinaliza que o teto de custo definido em --max-cost-usd foi atingido."""


def _run_agentic(sites, drift_levels, drift_types, repeats, progress, feitos=None) -> None:
    # o braço agêntico lança o próprio navegador por execução (precisa da porta CDP),
    # portanto não compartilha a instância do braço determinístico.
    feitos = feitos or set()
    for site_name in sites:
        if site_name not in AGENTIC_MODULES:
            print(f"  (aviso: '{site_name}' não tem versão agêntica — ignorado)")
            continue
        module = AGENTIC_MODULES[site_name]
        for drift_type in drift_types:
            for drift_level in drift_levels:
                for repetition in range(repeats):
                    chave = (site_name, "agentic", drift_type, drift_level, repetition)
                    if chave in feitos:
                        print(f"  (pulando {site_name} | {drift_type} {drift_level} | "
                              f"rep={repetition} — já existe no CSV)")
                        continue
                    result = module.run(drift_level=drift_level, repetition=repetition,
                                        drift_type=drift_type)
                    _gravar(result)
                    progress(result, site_name, "agêntico", drift_type,
                             drift_level, repetition)


def _problema_na_chave(chave: str) -> str:
    """
    Valida o formato da chave ANTES de gastar tempo e crédito.

    O erro 401 só aparece depois de abrir o navegador e subir o servidor MCP,
    o que torna a depuração lenta. Os enganos mais comuns são digitar o texto
    de exemplo e, no cmd, incluir aspas — que passam a fazer parte do valor.
    """
    if chave != chave.strip():
        return "há espaços em branco no início ou no fim"
    if chave[:1] in "\"'" or chave[-1:] in "\"'":
        return "a chave está entre aspas (no cmd, use sem aspas)"
    if "sua-chave" in chave or "sk-ant-..." in chave:
        return "parece ser o texto de exemplo, não uma chave real"
    if not chave.startswith("sk-ant-"):
        return "chaves da Anthropic começam com 'sk-ant-'"
    if len(chave) < 40:
        return f"a chave parece curta demais ({len(chave)} caracteres)"
    return ""


def _execucoes_ja_feitas() -> set:
    """
    Lê o CSV existente e devolve as combinações já executadas, para permitir
    retomar uma coleta interrompida (falta de crédito, queda de rede, etc.)
    sem repetir — e sem pagar de novo — o que já foi coletado.
    """
    import csv

    feitos = set()
    if not os.path.isfile(CSV_PATH):
        return feitos
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                # Só conta como feita a execução desta coleta e desta versão do
                # código. Uma linha de outra versão não pode ser "pulada" como
                # se valesse: a auditoria a acusaria, e com razão.
                if (row.get("versao_instrumento", "") != VERSAO_INSTRUMENTO
                        or str(row.get("coleta", "")) != str(COLETA)):
                    continue
                feitos.add((row["site"], row["approach"],
                            row.get("drift_type", "cosmetico"),
                            float(row["drift_level"]), int(row["repetition"])))
            except (KeyError, ValueError):
                continue
    return feitos


def main() -> None:
    args = parse_args()
    global COLETA, VERSAO_INSTRUMENTO
    COLETA = args.coleta
    VERSAO_INSTRUMENTO = impressao_instrumento()
    print(f"Coleta {COLETA} | impressão do instrumento: {VERSAO_INSTRUMENTO}\n")
    drift_levels = [float(x) for x in args.drift_levels.split(",")]
    sites = args.sites.split(",")
    drift_types = [t.strip() for t in args.drift_types.split(",")]

    if args.approach == "all":
        approaches = list(TODOS_OS_BRACOS)
    elif args.approach == "sem-api":
        approaches = [a for a in TODOS_OS_BRACOS if a != "agentic"]
    elif args.approach == "both":  # compatibilidade com o uso anterior
        approaches = ["deterministic", "agentic"]
    else:
        approaches = [args.approach]

    backend = os.getenv("AGENT_BACKEND", "api").lower()
    if "agentic" in approaches:
        if backend == "sdk":
            if os.getenv("ANTHROPIC_API_KEY"):
                raise SystemExit(
                    "AGENT_BACKEND=sdk, mas ANTHROPIC_API_KEY está definida.\n"
                    "Com a chave presente o SDK usaria a API COBRADA em vez da\n"
                    "assinatura. Remova a variável desta sessão:\n"
                    "  Remove-Item Env:ANTHROPIC_API_KEY"
                )
            print("Backend agêntico: Claude Agent SDK (assinatura, sem cobrança por token)\n")
        else:
            chave = os.getenv("ANTHROPIC_API_KEY")
            if not chave:
                raise SystemExit(
                    "ANTHROPIC_API_KEY não definida. O braço agêntico precisa dela.\n"
                    "  cmd:         set ANTHROPIC_API_KEY=sk-ant-...\n"
                    "  PowerShell:  $env:ANTHROPIC_API_KEY = \"sk-ant-...\"\n"
                    "Ou use a assinatura:  set AGENT_BACKEND=sdk"
                )
            problema = _problema_na_chave(chave)
            if problema:
                raise SystemExit(
                    f"A chave em ANTHROPIC_API_KEY parece inválida: {problema}\n"
                    "Isso causaria erro 401 (invalid x-api-key) depois de abrir o navegador.\n"
                    "No cmd, defina SEM aspas:  set ANTHROPIC_API_KEY=sk-ant-...\n"
                    "(aspas viram parte do valor no cmd, ao contrário do PowerShell)"
                )
            print("Backend agêntico: API da Anthropic (cobrada por token)\n")

    feitos = _execucoes_ja_feitas() if args.resume else set()
    if args.resume and feitos:
        print(f"Modo retomada: {len(feitos)} execuções já registradas no CSV serão puladas.\n")

    total_runs = (len(sites) * len(drift_levels) * len(drift_types)
                  * args.repeats * len(approaches))
    state = {"done": 0, "cost": 0.0}

    def progress(result, site_name, label, drift_type, drift_level, repetition) -> None:
        state["done"] += 1
        state["cost"] += result.cost_usd
        status = "OK" if result.success else f"FALHA ({result.error_type})"
        custo = f" | US$ {result.cost_usd:.4f}" if result.cost_usd else ""
        print(
            f"[{state['done']}/{total_runs}] {site_name} ({label}) | "
            f"{drift_type} {drift_level} | rep={repetition} | {status} | "
            f"{result.duration_s}s{custo}"
        )
        if args.max_cost_usd and state["cost"] >= args.max_cost_usd:
            raise OrcamentoEsgotado(
                f"teto de US$ {args.max_cost_usd:.2f} atingido "
                f"(gasto: US$ {state['cost']:.4f})"
            )

    interrompido = ""
    try:
        if args.intercalado:
            _run_intercalado(approaches, sites, drift_levels, drift_types,
                             args.repeats, args.headed, progress, feitos,
                             args.semente_ordem)
        for approach in ([] if args.intercalado else approaches):
            if approach == "agentic":
                _run_agentic(sites, drift_levels, drift_types, args.repeats,
                             progress, feitos)
            else:
                _run_sincrono(approach, sites, drift_levels, drift_types,
                              args.repeats, args.headed, progress, feitos)
    except OrcamentoEsgotado as e:
        interrompido = str(e)
    except LimiteDeUsoAtingido as e:
        interrompido = f"limite de uso do provedor atingido — {e}"
    except AgenteSemFerramentas as e:
        interrompido = f"configuração inválida do agente — {e}"
    except RuntimeError as e:
        # Falha de acesso ao site (rede caiu, servidor fora do ar). O `navegar`
        # já a marca com um prefixo próprio; aqui ela vira parada limpa em vez
        # de traceback. Nada é gravado: uma execução que não chegou a abrir a
        # página não é resultado experimental.
        if "[FALHA DE NAVEGACAO]" not in str(e):
            raise
        interrompido = (f"não foi possível acessar o site — {e}\n"
                        "  Verifique a conexão e rode o MESMO comando de novo.")
    except AgenteContornouAInterface as e:
        interrompido = (f"o agente contornou a interface degradada — {e}. "
                        "A execução não mede robustez de localização e não foi gravada.")
    except KeyboardInterrupt:
        interrompido = "interrompido pelo usuário"

    print(f"\n{'Interrompido' if interrompido else 'Concluído'}. Resultados em: {CSV_PATH}")
    if interrompido:
        print(f"Motivo: {interrompido}")
        print("Nenhuma execução inválida foi gravada: um erro de infraestrutura")
        print("não é resultado experimental e a coleta para em vez de registrá-lo.")
        print("Os dados já coletados estão salvos. Para continuar de onde parou:")
        print("  python -m common.runner --resume [mesmos parâmetros de antes]")
    if state["cost"]:
        print(f"Custo de API nesta execução: US$ {state['cost']:.4f}")


if __name__ == "__main__":
    main()
