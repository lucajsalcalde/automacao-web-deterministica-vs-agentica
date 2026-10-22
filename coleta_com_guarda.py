"""
Roda a coleta (python -m common.runner) com uma guarda contra site fora do ar.

POR QUE EXISTE
--------------
O `navegar` (common/oracle.py) só reconhece falha de acesso quando o
carregamento lança exceção. Um site do Heroku fora do ar responde HTTP 503 com
a página "Application Error", que carrega sem erro nenhum: a tarefa segue, não
encontra os campos e a execução seria gravada como falha, como se fosse efeito
da degradação. Aconteceu no teste do agente de 16/09/2026: o The Internet ficou
alguns minutos respondendo 503; o agente relatou o erro e desistiu.

O QUE FAZ
---------
Antes de gravar uma execução que FALHOU, confere se o site responde. Se ele
estiver fora do ar, a execução não é gravada e a coleta para com o mesmo aviso
de falha de navegação que o runner já usa ([FALHA DE NAVEGACAO]). O
executar_coleta2 espera 5 minutos e retoma do mesmo ponto, refazendo essa
execução. Sucessos são gravados sem conferência.

A regra é a mesma para todos os braços e depende só do estado do site, nunca
da tarefa. Declarar "fora do ar" sem prova seria grave: a falha seria refeita
até dar certo, o que enviesa o resultado. Por isso só conta como queda o que
o Chromium (o mesmo navegador das tarefas) confirma duas vezes, com 10 s de
intervalo: HTTP 5xx ou página que não carrega. Qualquer dúvida na conferência
leva a gravar a execução normalmente.

Nada muda na execução das tarefas nem na medição: common/ e sites/ ficam
intactos, e a impressão digital do instrumento é a mesma. Cada execução não
gravada fica anotada em data/logs/guarda_site.csv.

USO
---
    python coleta_com_guarda.py [mesmos argumentos de python -m common.runner]
    python coleta_com_guarda.py --conferir-site saucedemo,the_internet
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

RAIZ = os.path.dirname(os.path.abspath(__file__))
ARQ_REGISTRO = os.path.join(RAIZ, "data", "logs", "guarda_site.csv")

ENDERECOS = {
    "saucedemo": "https://www.saucedemo.com/",
    "the_internet": "https://the-internet.herokuapp.com/login",
}
AGENTE_HTTP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
INTERVALO_CONFIRMACAO_S = 10


def endereco(site: str) -> str | None:
    if site in ENDERECOS:
        return ENDERECOS[site]
    nome = (site or "").lower()
    if "saucedemo" in nome:
        return ENDERECOS.get("saucedemo")
    if "internet" in nome:
        return ENDERECOS.get("the_internet")
    return None


# ------------------------------------------------------------------ conferência

def pelo_http(url: str, timeout: float = 20) -> tuple:
    """Primeira olhada, rápida, pelo Python: ("no_ar" | "suspeito", detalhe)."""
    pedido = urllib.request.Request(url, headers={"User-Agent": AGENTE_HTTP})
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
            return "no_ar", f"HTTP {resposta.status}"
    except urllib.error.HTTPError as e:
        return ("suspeito" if e.code >= 500 else "no_ar"), f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return "suspeito", f"sem resposta ({e.reason})"
    except (OSError, ssl.SSLError, ValueError) as e:
        return "suspeito", f"sem resposta ({type(e).__name__}: {e})"


def pelo_navegador(url: str, timeout_ms: int = 30000) -> tuple:
    """
    Abre o endereço num Chromium. Devolve (True | False | None, detalhe), em que
    None quer dizer que a conferência em si não funcionou (sem prova de nada).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        return None, f"playwright indisponível ({e})"
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:  # noqa: BLE001
                return None, f"Chromium não abriu ({type(e).__name__})"
            try:
                page = browser.new_page()
                try:
                    resposta = page.goto(url, wait_until="domcontentloaded",
                                         timeout=timeout_ms)
                except Exception as e:  # noqa: BLE001
                    primeira = (str(e).splitlines() or [""])[0][:120]
                    return False, f"navegador sem resposta ({type(e).__name__}: {primeira})"
                if resposta is None:
                    return None, "navegador sem resposta HTTP para conferir"
                return resposta.status < 500, f"navegador HTTP {resposta.status}"
            finally:
                browser.close()
    except Exception as e:  # noqa: BLE001
        return None, f"conferência pelo navegador falhou ({type(e).__name__})"


def navegador_em_subprocesso(url: str) -> tuple:
    """
    O Chromium da conferência roda em outro processo: dentro da coleta já há um
    Playwright síncrono aberto, e a API síncrona não admite um segundo no mesmo
    processo.
    """
    try:
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "--navegador", url],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=150, cwd=RAIZ)
    except subprocess.TimeoutExpired:
        return None, "conferência pelo navegador não terminou em 150 s"
    except OSError as e:
        return None, f"conferência pelo navegador não rodou ({e})"
    linhas = (r.stdout or "").strip().splitlines()
    try:
        dados = json.loads(linhas[-1]) if linhas else {}
    except ValueError:
        dados = {}
    if not isinstance(dados, dict) or "no_ar" not in dados:
        return None, f"conferência pelo navegador sem resultado (código {r.returncode})"
    return dados["no_ar"], dados.get("detalhe", "")


def conferir(site: str) -> tuple:
    """
    (True, detalhe) se o site está no ar ou se não há prova do contrário;
    (False, detalhe) só com duas confirmações do Chromium.
    """
    url = endereco(site)
    if not url:
        return True, f"sem endereço de conferência para {site}"
    estado, detalhe_http = pelo_http(url)
    if estado == "no_ar":
        return True, detalhe_http
    detalhes = [detalhe_http]
    for tentativa in (1, 2):
        no_ar, detalhe = navegador_em_subprocesso(url)
        detalhes.append(detalhe)
        if no_ar is not False:
            return True, "; ".join(detalhes)
        if tentativa == 1:
            time.sleep(INTERVALO_CONFIRMACAO_S)
    return False, "; ".join(detalhes)


# ------------------------------------------------------------------ guarda

def anotar(result, detalhe: str) -> None:
    os.makedirs(os.path.dirname(ARQ_REGISTRO), exist_ok=True)
    novo = not os.path.isfile(ARQ_REGISTRO)
    with open(ARQ_REGISTRO, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if novo:
            w.writerow(["quando", "site", "approach", "drift_type", "drift_level",
                        "repetition", "error_type", "duration_s", "detalhe"])
        w.writerow([dt.datetime.now().isoformat(timespec="seconds"),
                    getattr(result, "site", ""), getattr(result, "approach", ""),
                    getattr(result, "drift_type", ""), getattr(result, "drift_level", ""),
                    getattr(result, "repetition", ""), getattr(result, "error_type", ""),
                    getattr(result, "duration_s", ""), detalhe])


def instalar_guarda(runner) -> None:
    """Troca o `_gravar` do runner por um que confere o site antes de gravar falha."""
    gravar_original = runner._gravar

    def gravar_com_guarda(result) -> None:
        if not result.success:
            no_ar, detalhe = conferir(result.site)
            if not no_ar:
                anotar(result, detalhe)
                print(f"\n[guarda] {result.site} fora do ar ({detalhe}): a falha de "
                      f"{result.approach} | {result.drift_type} {result.drift_level} | "
                      f"rep={result.repetition} não foi gravada.", flush=True)
                raise RuntimeError(
                    f"[FALHA DE NAVEGACAO] {result.site} fora do ar logo depois de uma "
                    f"execução que falhou ({detalhe}). A execução não foi gravada e será "
                    "refeita quando a coleta retomar.")
        gravar_original(result)

    runner._gravar = gravar_com_guarda


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--navegador":
        no_ar, detalhe = pelo_navegador(sys.argv[2])
        print(json.dumps({"no_ar": no_ar, "detalhe": detalhe}))
        return

    if len(sys.argv) >= 2 and sys.argv[1] == "--conferir-site":
        pedidos = sys.argv[2] if len(sys.argv) >= 3 else ",".join(ENDERECOS)
        fora = []
        for site in [s.strip() for s in pedidos.split(",") if s.strip()]:
            no_ar, detalhe = conferir(site)
            print(f"{site}: {'no ar' if no_ar else 'FORA DO AR'} ({detalhe})", flush=True)
            if not no_ar:
                fora.append(site)
        sys.exit(1 if fora else 0)

    sys.path.insert(0, RAIZ)
    from common import runner
    instalar_guarda(runner)
    print("Guarda de disponibilidade ligada: falha com o site fora do ar não é gravada "
          "(ver coleta_com_guarda.py).\n", flush=True)
    runner.main()


if __name__ == "__main__":
    main()
